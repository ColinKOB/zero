"""Every model call a Zip makes, one line each, for measuring what the plan's usage goes to (Oct 10).

Hermes fires `post_api_request` after each main-loop call (chats, teammates, scheduled jobs, board work, and the
detached forks: the background review and the hub's keep-warm pings) and `post_auxiliary_call` after each helper
call (compaction summaries, titles). Both carry the call's token buckets. Each becomes one JSON line in
<hub_state>/usage/YYYY-MM-DD.jsonl (the home's own date), with no message text, only counts and ids:

    {"ts": 1791651738.0, "at": "2026-10-10T10:42:18-04:00", "home": "colin", "zip": "default", "origin": "chat",
     "session": "api_...", "model": "opus", "read": 68857, "write": 101, "uncached": 237, "out": 4,
     "reasoning": 0, "secs": 7.8}

origin: chat (the person's chat with the Zip), team (answering a teammate), prep and quiet (the hub's other lanes),
cron (scheduled jobs), board (board tasks), background-review, keep-warm, side-question, compression and the other
helper tasks by Hermes's name, or the platform's name. alien/bin/zero-usage sums the files; the hub adds plan
readings. Files older than KEEP_DAYS are removed. Logging never fails a call.
"""

import json
import logging
import os
import sqlite3
import sys
import threading
import time
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
    """The agent whose call this is, when the hook runs on the agent's own thread: Hermes's hook payload names the
    session but not the agent, and only the agent says whether this is a detached fork (a keep-warm ping, the
    background review) of the chat it shares an id with. On Hermes's hook worker thread, _write_origin says it."""
    f = sys._getframe(1)
    for _ in range(25):
        if f is None:
            return None
        if f.f_code.co_name == "_fire_post_api_request_hook":
            return f.f_locals.get("agent")
        f = f.f_back
    return None


def _hub_key(session_id: str) -> str:
    """The hub's name for this session ("school", "school#team"), or "" when it is not one of the hub's."""
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
    """Hermes's per-turn write origin: a detached fork's purpose ("keep_warm", "background_review"). Hermes runs hook
    callbacks on a worker thread with a copy of the turn's context, so this holds where the agent is out of reach."""
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


def write(record: dict) -> None:
    """Appends one line to today's file (the home's date)."""
    now = datetime.fromtimestamp(record.get("ts") or time.time(), _zone())
    record.setdefault("at", now.isoformat(timespec="seconds"))
    try:
        from . import home
        record.setdefault("home", home.home_id())
    except Exception:
        pass
    d = usage_dir()
    d.mkdir(parents=True, exist_ok=True)
    day = now.strftime("%Y-%m-%d")
    line = json.dumps(record, separators=(",", ":")) + "\n"
    fd = os.open(d / f"{day}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())   # one write of a short line: lines from several processes never interleave
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
    rec.update({k: v for k, v in extra.items() if v not in (None, "")})
    write(rec)


def post_api_request(session_id: str = "", platform: str = "", model=None, usage=None, api_duration=None,
                     provider=None, **kw):
    try:
        agent = _agent_in_stack()
        _record(usage, session_id=session_id, model=model, origin=origin_of(agent, session_id, platform),
                secs=api_duration, provider=provider)
    except Exception:
        log.debug("could not log a call's usage", exc_info=True)


def post_auxiliary_call(aux_task: str = "", session_id: str = "", model=None, usage=None, api_duration=None,
                        provider=None, error=None, **kw):
    try:
        _record(usage, session_id=session_id, model=model, origin=str(aux_task or "auxiliary").replace("_", "-"),
                secs=api_duration, provider=provider, failed=True if error else None)
    except Exception:
        log.debug("could not log a helper call's usage", exc_info=True)
