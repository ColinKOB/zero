"""The hub's part of the usage and cache work (Oct 10): plan readings in the usage log, and keep-warm pings.

The usage log. The taffy-tools plugin writes one line per model call to <hub_state>/usage/YYYY-MM-DD.jsonl
(usage_log.py; alien/bin/zero-usage sums it). Every half hour the hub adds the plan's own percentages ("kind":
"plan"), read the way tools/claude-usage.sh reads them on the Mac: Claude Code's sign-in on this machine, read only.
The token is never printed or refreshed (Claude Code owns it; refreshing it here could sign Claude Code out), so a
reading is skipped while the token is expired. Only a home on its own plan reads it.

Keep-warm pings. A chat's request prefix (tools, system prompt, history: about 140k tokens for Zero on Oct 10) stays
in the provider's cache for an hour after its last use. A person who comes back after that pays the whole prefix
again as a cache write, which costs 20 times a cache read. About 55 minutes after a chat's last turn the hub asks
Hermes to refresh it (POST /api/sessions/{id}/keepwarm, alien/patches/hermes-session-keepwarm.patch): one call that
reads the cached prefix and adds one line, on a detached copy of the session, so nothing is added to the chat.
Measured on Oct 10: the ping read 68,857 of 69,195 prompt tokens and wrote 101; the next real turn read 68,958 of
69,176 and wrote none. Pings stop three hours after the chat's last turn (at most three per quiet spell), never run
during a turn or in the person's quiet hours, and only when the home's settings say cache.keep_warm.
"""

import asyncio
import json
import logging
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import httpx

from . import activity, config, home, store, turns

log = logging.getLogger("taffy.usage")

PLAN_EVERY = 30 * 60
PING_AFTER = 55 * 60       # the cache lasts an hour after its last read
WINDOW = 3 * 3600          # pings stop this long after the chat's last turn
TICK = 60
UNPATCHED_PAUSE = 6 * 3600  # Hermes without the keepwarm endpoint: try again this much later

_last_turn: dict[str, float] = {}    # Zip -> when its chat's last turn ended (the cache was written or read then)
_last_ping: dict[str, float] = {}
_paused_until = [0.0]


# ── The log ───────────────────────────────────────────────────────────────────────────────────────────────────

def usage_dir() -> Path:
    return config.HUB_DIR / "usage"


def write(record: dict) -> None:
    """One line in today's file (the home's date), as the plugin writes them."""
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


def log_cli(origin: str, result: dict | None, model: str, started: float, bot_id: str | None = None) -> None:
    """One line for a model call the hub itself makes through the claude CLI (Haiku for short replies, notification
    lines, the question gate, memory, briefings and the calendar copy), from the CLI's result event, in the plugin's
    fields. Before Oct 11 these were not in the log at all (Fable's usage review). Never raises."""
    try:
        u = (result or {}).get("usage") or {}
        write({"zip": bot_id or "", "origin": origin, "model": model, "read": int(u.get("cache_read_input_tokens") or 0),
               "write": int(u.get("cache_creation_input_tokens") or 0), "uncached": int(u.get("input_tokens") or 0),
               "out": int(u.get("output_tokens") or 0), "secs": round(time.monotonic() - started, 2),
               "provider": "hub", "ok": not (result or {}).get("is_error", result is None)})
    except Exception:
        log.debug("could not log a hub model call", exc_info=True)


def caller(depth: int = 2) -> str:
    """The hub module that asked for a model call, as the log's origin ("hub-notify", "hub-extract")."""
    import sys
    try:
        f = sys._getframe(depth)
    except ValueError:
        f = None
    while f is not None and f.f_globals.get("__name__", "").startswith("asyncio"):   # through asyncio.wait_for
        f = f.f_back
    name = f.f_globals.get("__name__", "") if f is not None else ""
    return "hub-" + (name.rsplit(".", 1)[-1] or "unknown")


# ── Plan readings ─────────────────────────────────────────────────────────────────────────────────────────────

def _credentials() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(home.USER_HOME / ".claude")
    return Path(base) / ".credentials.json"


def read_plan(now: float | None = None) -> dict | None:
    """The plan's weekly and five-hour percentages, or None (not this home's own plan, no usable sign-in, or the
    reading failed)."""
    now = time.time() if now is None else now
    if str(home.get("plan.claude.mode") or "own") != "own":
        return None
    try:
        oauth = json.loads(_credentials().read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return None
    token = oauth.get("accessToken")
    if not token or float(oauth.get("expiresAt") or 0) / 1000 < now + 120:
        return None   # expired: Claude Code refreshes it on its next call; never here
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


# ── Keep-warm ─────────────────────────────────────────────────────────────────────────────────────────────────

def on() -> bool:
    return bool(home.get("cache.keep_warm"))


def turn_ended(bot_id: str, at: float | None = None) -> None:
    """A turn in this Zip's chat just finished: its cache was read or written then."""
    _last_turn[bot_id] = time.time() if at is None else at


def quiet(now: float | None = None) -> bool:
    """Inside the person's quiet hours (11 p.m. to 7 a.m. unless the home's settings say otherwise)."""
    h = datetime.fromtimestamp(time.time() if now is None else now, home.TZ).hour
    start, end = home.quiet_hours()
    return (start <= h or h < end) if start > end else (start <= h < end)


def due(now: float | None = None) -> list[str]:
    """The Zips whose chat should be refreshed now."""
    now = time.time() if now is None else now
    if not on() or now < _paused_until[0] or quiet(now):
        return []
    out = []
    for bot_id, last in list(_last_turn.items()):
        if bot_id not in config.BOTS_BY_ID or now - last >= WINDOW:
            continue
        base = max(last, _last_ping.get(bot_id, 0.0))
        if now - base < PING_AFTER:
            continue
        if turns.is_busy(bot_id) or bot_id in activity._live:
            continue   # never during a turn (a run held on a card counts)
        out.append(bot_id)
    return out


async def ping(bot_id: str) -> dict:
    """One keep-warm call for the Zip's chat. The call itself is logged by the plugin (origin keep-warm); this
    logs the outcome, including a skip."""
    from . import hermes
    _last_ping[bot_id] = time.time()
    sid = store.session_for(bot_id)
    if not sid:
        return {"skipped": "no_chat"}
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/api/sessions/{sid}/keepwarm",
                                  headers=hermes._headers(bot_id), json={})
        if r.status_code in (404, 405) and "json" not in r.headers.get("content-type", ""):   # no such route
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
            except Exception:
                log.warning("could not record the plan reading", exc_info=True)
        try:
            for bot_id in due(now):
                await ping(bot_id)
        except Exception:
            log.warning("keep-warm pass failed", exc_info=True)
