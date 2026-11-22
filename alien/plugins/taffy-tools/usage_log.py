""


















import json
import logging
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("taffy.usage")

KEEP_DAYS = 120
_lane_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()
_pruned_day: list[str] = [""]
FORK_ORIGINS = {"keep_warm": "keep-warm", "background_review": "background-review", "side_question": "side-question"}


def _gate():
    from . import gate
    return gate


def usage_dir() -> Path:
    return _gate()._hub_state() / "usage"


def _zone():
    from zoneinfo import ZoneInfo
    from . import home
    try:
        return ZoneInfo(str(home.get("person.timezone") or "America/New_York"))
    except Exception:
        return ZoneInfo("America/New_York")


def _agent_in_stack():
    ""


    f = sys._getframe(1)
    for _ in range(25):
        if f is None:
            return None
        if f.f_code.co_name == "_fire_post_api_request_hook":
            return f.f_locals.get("agent")
        f = f.f_back
    return None


def _hub_key(session_id: str) -> str:
    ""
    if not session_id:
        return ""
    now = time.time()
    with _lock:
        hit = _lane_cache.get(session_id)
    if hit and now - hit[1] < 300:
        return hit[0]
    key = ""
    try:
        from . import lineage
        sid = lineage.root(session_id)
        c = sqlite3.connect(f"file:{_gate()._hub_state() / 'hub.db'}?mode=ro", uri=True, timeout=2)
        try:
            r = c.execute("SELECT bot_id FROM bot_sessions WHERE session_id=?", (sid,)).fetchone()
        finally:
            c.close()
        key = str(r[0]) if r else ""
    except Exception:
        key = ""
    with _lock:
        _lane_cache[session_id] = (key, now)
    return key


def _write_origin() -> str:
    ""

    try:
        from tools.skill_provenance import get_current_write_origin
        return str(get_current_write_origin() or "")
    except Exception:
        return ""


def origin_of(agent, session_id: str, platform: str) -> str:
    fork = getattr(agent, "_turn_origin", None) if agent is not None else None
    if fork not in FORK_ORIGINS:
        fork = _write_origin()
    if fork in FORK_ORIGINS:
        return FORK_ORIGINS[fork]
    if os.environ.get("HERMES_KANBAN_TASK"):
        return "board"
    platform = platform or (getattr(agent, "platform", "") if agent is not None else "") or ""
    if platform == "cron":
        return "cron"
    key = _hub_key(session_id)
    if key:
        return key.rsplit("#", 1)[1] if "#" in key else "chat"
    return platform or "other"


def _n(usage: dict, name: str) -> int:
    try:
        return int(usage.get(name) or 0)
    except (TypeError, ValueError):
        return 0


def turn_identity() -> dict:
    ""
    try:
        from agent.relay_runtime import current_turn
        turn = current_turn()
        return {**getattr(turn, "usage_context", {}),
                "turn_id": str(getattr(turn, "turn_id", "") or ""),
                "task_id": str(getattr(turn, "task_id", "") or "")}
    except Exception:
        return {}


def write(record: dict) -> None:
    ""
    now = datetime.fromtimestamp(record.get("ts") or time.time(), _zone())
    record.setdefault("at", now.isoformat(timespec="seconds"))
    try:
        from . import claudeaccount, home
        record.setdefault("home", home.home_id())
        record.setdefault("account", claudeaccount.current)
    except Exception:
        pass
    d = usage_dir()
    d.mkdir(parents=True, exist_ok=True)
    day = now.strftime("%Y-%m-%d")
    line = json.dumps(record, separators=(",", ":")) + "\n"
    fd = os.open(d / f"{day}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)
    if _pruned_day[0] != day:
        _pruned_day[0] = day
        cutoff = time.time() - KEEP_DAYS * 86400
        for f in d.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


def _record(usage, *, session_id: str, model, origin: str, secs=None, **extra) -> None:
    available = isinstance(usage, dict) and bool(usage)
    usage = usage if isinstance(usage, dict) else {}
    rec = {"ts": round(time.time(), 3), "zip": _gate()._bot(), "origin": origin, "session": session_id or "",
           "model": str(model or ""), "read": _n(usage, "cache_read_tokens"), "write": _n(usage, "cache_write_tokens"),
           "uncached": _n(usage, "input_tokens"), "out": _n(usage, "output_tokens"),
           "reasoning": _n(usage, "reasoning_tokens")}
    if secs is not None:
        try:
            rec["secs"] = round(float(secs), 2)
        except (TypeError, ValueError):
            pass
    identity = turn_identity()
    turn = extra.pop("turn_id", "") or identity.get("turn_id") or ""
    request = extra.pop("api_request_id", "") or uuid.uuid4().hex
    finished = extra.pop("ended_at", None) or rec["ts"]
    started = extra.pop("started_at", None) or finished - max(0.0, float(secs or 0))
    rec.update({"kind": "usage", "scope": "request", "errand_id": identity.get("errand_id") or turn or request,
                "parent_errand_id": identity.get("parent_errand_id"), "request_id": request,
                "attempt_id": f"{request}:{extra.get('retry_count', 0)}",
                "task_id": identity.get("task_id") or "", "started_at": started, "finished_at": finished,
                "outcome": "response", "usage_available": available,
                "reasoning_available": "reasoning_tokens" in usage})
    rec.update({k: v for k, v in extra.items() if v is not None})
    usd = _cost(rec)
    if usd is not None:
        rec["usd"] = round(usd, 6)
    write(rec)


def _cost(rec: dict) -> float | None:
    ""



    try:
        from . import models
        m = models.BY_ID.get(str(rec.get("model") or ""))
        if m is None or not m.keyed:
            return None
        return m.cost(rec.get("uncached", 0), rec.get("read", 0) + rec.get("write", 0), rec.get("out", 0))
    except Exception:
        return None


def post_api_request(session_id: str = "", platform: str = "", model=None, usage=None, api_duration=None,
                     provider=None, **kw):
    try:
        agent = _agent_in_stack()
        _record(usage, session_id=session_id, model=kw.get("response_model") or model,
                origin=origin_of(agent, session_id, platform),
                secs=api_duration, provider=provider, **_metadata(kw))
    except Exception:
        log.debug("could not log a call's usage", exc_info=True)


def post_auxiliary_call(aux_task: str = "", session_id: str = "", model=None, usage=None, api_duration=None,
                        provider=None, error=None, **kw):
    try:
        identity = turn_identity()
        parent = identity.get("errand_id") or kw.get("turn_id") or identity.get("turn_id")
        _record(usage, session_id=session_id, model=kw.get("response_model") or model,
                origin=str(aux_task or "auxiliary").replace("_", "-"),
                secs=api_duration, provider=provider, **_metadata(kw),
                errand_id=kw.get("api_request_id") or uuid.uuid4().hex, parent_errand_id=parent,
                outcome="failed" if error else "response", failed=True if error else None)
    except Exception:
        log.debug("could not log a helper call's usage", exc_info=True)


def _metadata(kw: dict) -> dict:
    return {k: kw[k] for k in ("turn_id", "task_id", "api_request_id", "started_at", "ended_at", "retry_count") if k in kw}


def api_request_error(session_id="", platform="", model=None, provider=None, api_duration=None, **kw):
    try:
        _record(None, session_id=session_id, model=model, provider=provider, secs=api_duration,
                origin=origin_of(None, session_id, platform), outcome="failed", failed=True, **_metadata(kw))
    except Exception:
        log.debug("could not log a failed request", exc_info=True)


def errand_started(session_id="", turn_id="", task_id="", platform="", model=None, **kw):
    try:
        identity = turn_identity()
        write({"kind": "errand", "phase": "start", "ts": time.time(),
               "errand_id": identity.get("errand_id") or turn_id,
               "parent_errand_id": identity.get("parent_errand_id"), "task_id": task_id,
               "session": session_id, "zip": _gate()._bot(),
               "origin": origin_of(None, session_id, platform), "model": str(model or "")})
    except Exception:
        log.debug("could not log errand start", exc_info=True)


def errand_finished(session_id="", turn_id="", task_id="", platform="", assistant_response=None, **kw):
    try:
        identity = turn_identity()
        write({"kind": "errand", "phase": "finish", "ts": time.time(),
               "errand_id": identity.get("errand_id") or turn_id,
               "parent_errand_id": identity.get("parent_errand_id"), "task_id": task_id,
               "session": session_id, "zip": _gate()._bot(),
               "origin": origin_of(None, session_id, platform),
               "outcome": "answered" if assistant_response else "no_answer"})
    except Exception:
        log.debug("could not log errand finish", exc_info=True)


def log_connector(result, *, service, provider, model, started, session_id, failed=False):
    ""
    try:
        raw = (result or {}).get("usage")
        u = raw if isinstance(raw, dict) else {}
        if provider == "openai":
            usage = {"input_tokens": max(0, _n(u, "input_tokens") - _n(u, "cached_input_tokens")),
                     "cache_read_tokens": _n(u, "cached_input_tokens"), "output_tokens": _n(u, "output_tokens")}
        else:
            usage = {"input_tokens": _n(u, "input_tokens"),
                     "cache_read_tokens": _n(u, "cache_read_input_tokens"),
                     "cache_write_tokens": _n(u, "cache_creation_input_tokens"),
                     "output_tokens": _n(u, "output_tokens"), "reasoning_tokens": _n(u, "reasoning_tokens")}
        identity = turn_identity()
        parent = identity.get("errand_id") or identity.get("turn_id")
        _record(usage if raw is not None else None, session_id=session_id, model=model, provider=provider,
                origin=f"connector-{service}", secs=time.monotonic() - started,
                scope="execution", errand_id=uuid.uuid4().hex, parent_errand_id=parent,
                reasoning_available="reasoning_tokens" in u,
                outcome="failed" if failed or (result or {}).get("is_error") else "completed")
    except Exception:
        log.debug("could not log connector usage", exc_info=True)
