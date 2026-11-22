""

















import asyncio
import json
import logging
import os
import re
import time
import urllib.request
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

import httpx

from . import activity, config, health, home, store, turns

log = logging.getLogger("taffy.usage")

PLAN_EVERY = 30 * 60
PING_AFTER = 55 * 60
WINDOW = 3 * 3600
TICK = 60
UNPATCHED_PAUSE = 6 * 3600

_last_turn: dict[str, float] = {}
_last_ping: dict[str, float] = {}
_paused_until = [0.0]
_errand: ContextVar[dict | None] = ContextVar("usage_errand", default=None)


def context() -> dict:
    current = _errand.get() or {}
    return {k: current[k] for k in ("errand_id", "parent_errand_id", "zip") if current.get(k)}


@contextmanager
def errand(origin: str, bot_id: str = "", *, parent: str | None = None, errand_id: str | None = None):
    ""
    previous = _errand.get() or {}
    record = {"errand_id": errand_id or uuid.uuid4().hex,
              "parent_errand_id": parent if parent is not None else previous.get("errand_id"),
              "zip": bot_id or previous.get("zip", ""), "origin": origin, "started_at": time.time()}
    token = _errand.set(record)
    outcome = "completed"
    try:
        yield record
    except BaseException as e:
        outcome = "cancelled" if isinstance(e, (asyncio.CancelledError, GeneratorExit)) else "failed"
        raise
    finally:
        try:
            write({"kind": "errand", **record, "finished_at": time.time(),
                   "outcome": outcome if outcome != "completed" else record.get("outcome", outcome)})
        except Exception:
            log.debug("could not log errand", exc_info=True)
        _errand.reset(token)




def usage_dir() -> Path:
    return config.HUB_DIR / "usage"


def write(record: dict) -> None:
    ""
    now = datetime.fromtimestamp(record.setdefault("ts", round(time.time(), 3)), home.TZ)
    record.setdefault("at", now.isoformat(timespec="seconds"))
    record.setdefault("home", str(home.get("home_id") or ""))
    d = usage_dir()
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / f"{now.strftime('%Y-%m-%d')}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(record, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(fd)


def log_cli(origin: str, result: dict | None, model: str, started: float, bot_id: str | None = None,
            *, outcome: str | None = None) -> None:
    ""


    try:
        u = (result or {}).get("usage") or {}
        current = _errand.get() or {}
        elapsed = max(0.0, time.monotonic() - started)
        finished = time.time()
        ok = not (result or {}).get("is_error", result is None)
        if outcome is not None:
            ok = outcome == "completed"
        owned = current.get("origin") == origin
        thinking = (u.get("output_tokens_details") or {}).get("thinking_tokens")
        models = sorted((result or {}).get("modelUsage") or {})
        write({"kind": "usage", "scope": "execution", "zip": bot_id or current.get("zip", ""),
               "errand_id": current.get("errand_id") if owned else uuid.uuid4().hex,
               "parent_errand_id": current.get("parent_errand_id") if owned else current.get("errand_id"),
               "request_id": (result or {}).get("session_id") or uuid.uuid4().hex,
               "started_at": finished - elapsed, "finished_at": finished,
               "outcome": "completed" if ok else "failed", "usage_available": bool(u),
               "origin": origin, "model": models[0] if len(models) == 1 else model,
               "requested_model": model, "models": models, "read": int(u.get("cache_read_input_tokens") or 0),
               "write": int(u.get("cache_creation_input_tokens") or 0), "uncached": int(u.get("input_tokens") or 0),
               "out": int(u.get("output_tokens") or 0), "reasoning": int(u.get("reasoning_tokens") or thinking or 0),
               "reasoning_available": "reasoning_tokens" in u or thinking is not None,
               "secs": round(elapsed, 2), "provider": "anthropic", "ok": ok})
    except Exception:
        log.debug("could not log a hub model call", exc_info=True)


def log_codex(origin: str, usage: dict | None, *, model: str, started: float, request_id: str,
              bot_id: str = "", parent: str | None = None, outcome: str = "completed") -> None:
    ""
    try:
        u = usage or {}
        finished = time.time()
        elapsed = max(0.0, time.monotonic() - started)
        read = int(u.get("cachedInputTokens") or 0)
        write({"kind": "usage", "scope": "turn", "errand_id": request_id,
               "parent_errand_id": parent, "request_id": request_id,
               "zip": bot_id, "origin": origin, "provider": "openai", "model": model,
               "started_at": finished - elapsed, "finished_at": finished, "secs": round(elapsed, 2),
               "outcome": outcome, "usage_available": usage is not None, "read": read, "write": 0,
               "uncached": max(0, int(u.get("inputTokens") or 0) - read),
               "out": int(u.get("outputTokens") or 0), "reasoning": int(u.get("reasoningOutputTokens") or 0),
               "reasoning_available": usage is not None and "reasoningOutputTokens" in usage})
    except Exception:
        log.debug("could not log app-server usage", exc_info=True)


def log_realtime(call_id: str, event: dict) -> None:
    ""
    try:
        request = event.get("request_id")
        if not isinstance(request, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request):
            return
        numbers = {k: v for k in ("input_tokens", "cached_tokens", "output_tokens")
                   if type(v := event.get(k)) is int and 0 <= v <= 1_000_000_000}
        finished = time.time()
        stamp = event.get("at")
        if isinstance(stamp, (float, int)) and abs(stamp - finished) < 86400:
            finished = stamp
        started = event.get("started_at")
        if not isinstance(started, (int, float)) or not finished - 3600 <= started <= finished:
            started = finished
        read = numbers.get("cached_tokens", 0)
        write({"kind": "usage", "scope": "response", "record_id": f"realtime:{call_id}:{request}",
               "errand_id": f"call:{call_id}", "parent_errand_id": None, "request_id": request,
               "zip": event.get("zip") if event.get("zip") in config.BOTS_BY_ID else "",
               "origin": "voice-realtime", "provider": "openai", "model": str(event.get("model") or "")[:120],
               "started_at": started, "finished_at": finished, "secs": round(finished - started, 3),
               "outcome": str(event.get("outcome") or "unknown")[:40],
               "usage_available": "input_tokens" in numbers and "output_tokens" in numbers,
               "reasoning_available": False, "read": read, "write": 0,
               "uncached": max(0, numbers.get("input_tokens", 0) - read), "out": numbers.get("output_tokens", 0),
               "reasoning": 0})
    except Exception:
        log.debug("could not log realtime usage", exc_info=True)


def caller(depth: int = 2) -> str:
    ""
    import sys
    try:
        f = sys._getframe(depth)
    except ValueError:
        f = None
    while f is not None and f.f_globals.get("__name__", "").startswith("asyncio"):
        f = f.f_back
    name = f.f_globals.get("__name__", "") if f is not None else ""
    return "hub-" + (name.rsplit(".", 1)[-1] or "unknown")




def _credentials() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(home.USER_HOME / ".claude")
    return Path(base) / ".credentials.json"


def read_plan(now: float | None = None, credentials: Path | None = None) -> dict | None:
    ""

    now = time.time() if now is None else now
    if str(home.get("plan.claude.mode") or "own") != "own":
        return None
    try:
        oauth = json.loads((credentials or _credentials()).read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return None
    token = oauth.get("accessToken")
    if not token or float(oauth.get("expiresAt") or 0) / 1000 < now + 120:
        return None
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-code"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except Exception as e:
        log.info("plan reading failed: %s", type(e).__name__)
        return None
    week, five = data.get("seven_day") or {}, data.get("five_hour") or {}
    return {"kind": "plan", "weekly": week.get("utilization"), "weekly_resets": week.get("resets_at"),
            "five_hour": five.get("utilization"), "five_hour_resets": five.get("resets_at")}




def on() -> bool:
    return bool(home.get("cache.keep_warm"))


def turn_ended(bot_id: str, at: float | None = None) -> None:
    ""
    _last_turn[bot_id] = time.time() if at is None else at


def quiet(now: float | None = None) -> bool:
    ""
    h = datetime.fromtimestamp(time.time() if now is None else now, home.TZ).hour
    start, end = home.quiet_hours()
    return (start <= h or h < end) if start > end else (start <= h < end)


def due(now: float | None = None) -> list[str]:
    ""
    now = time.time() if now is None else now


    if not on() or now < _paused_until[0] or quiet(now) or not health.status.get("claude", True):
        return []
    out = []
    for bot_id, last in list(_last_turn.items()):
        if bot_id not in config.BOTS_BY_ID or now - last >= WINDOW:
            continue
        base = max(last, _last_ping.get(bot_id, 0.0))
        if now - base < PING_AFTER:
            continue
        if turns.is_busy(bot_id) or bot_id in activity._live:
            continue
        out.append(bot_id)
    return out


async def ping(bot_id: str) -> dict:
    ""

    from . import hermes
    _last_ping[bot_id] = time.time()
    sid = store.session_for(bot_id)
    if not sid:
        return {"skipped": "no_chat"}
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/api/sessions/{sid}/keepwarm",
                                  headers=hermes._headers(bot_id), json={})
        if r.status_code in (404, 405) and "json" not in r.headers.get("content-type", ""):
            _paused_until[0] = time.time() + UNPATCHED_PAUSE
            out = {"skipped": "hermes_without_keepwarm"}
        elif r.status_code >= 400:
            out = {"failed": r.status_code}
        else:
            body = r.json()
            u = body.get("usage") or {}
            out = {k: body[k] for k in ("ok", "skipped", "error") if k in body}
            out.update({"read": u.get("cache_read_tokens"), "write": u.get("cache_write_tokens")} if u else {})
    except Exception as e:
        out = {"failed": type(e).__name__}
    try:
        write({"kind": "keep_warm", "zip": bot_id, "session": sid, **out})
    except OSError:
        pass
    log.info("keep-warm %s: %s", bot_id, out)
    return out


async def run() -> None:
    next_plan = 0.0
    while True:
        await asyncio.sleep(TICK)
        now = time.time()
        if now >= next_plan:
            next_plan = now + PLAN_EVERY
            try:
                rec = await asyncio.to_thread(read_plan)
                if rec:
                    write(rec)
                    await health.plan_read(rec)
            except Exception:
                log.warning("could not record the plan reading", exc_info=True)
        try:
            for bot_id in due(now):
                await ping(bot_id)
        except Exception:
            log.warning("keep-warm pass failed", exc_info=True)
