"""Talks to the Hermes API server: one long-lived session per bot, turns streamed over SSE."""

import hashlib
import json
import re
import time
from collections.abc import AsyncIterator

import logging

import httpx

from . import activity, chatter, config, events, screen, store

log = logging.getLogger("taffy.hermes")
TURN_TIMEOUT = httpx.Timeout(connect=10, read=900, write=30, pool=10)


def _headers(bot_id: str) -> dict:
    return {"Authorization": f"Bearer {config.api_key(bot_id)}", "Content-Type": "application/json"}


_session_locks: dict[str, "asyncio.Lock"] = {}


LANES = {"team": "answering teammates", "prep": "preparing drafts", "quiet": "looking things over"}


def session_key(bot_id: str, lane: str | None = None) -> str:
    return f"{bot_id}#{lane}" if lane else bot_id


async def ensure_session(bot_id: str, fresh: bool = False, team: bool = False, lane: str | None = None) -> str:
    """The bot's long-lived app session, created once. `fresh` replaces a session Hermes lost.
    `team` is the separate session where the bot answers teammates' questions (see team.py); `lane` names any of
    LANES (team=True is lane "team")."""
    import asyncio
    lane = "team" if team else lane
    if lane is not None and lane not in LANES:
        raise ValueError(f"unknown lane {lane}")
    key = session_key(bot_id, lane)
    lock = _session_locks.setdefault(key, asyncio.Lock())
    async with lock:
        sid = None if fresh else store.session_for(key)
        if sid:
            return sid
        bot = config.BOTS_BY_ID[bot_id]
        title = f"{bot.name} {LANES[lane]}" if lane else f"{bot.name} in the Taffy app"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/api/sessions", headers=_headers(bot_id),
                                  json={"title": title})
            if r.status_code == 400 and "invalid_title" in r.text:
                # Hermes titles are unique; an older conversation can still hold this one.
                import time
                r = await client.post(f"{config.api_prefix(bot_id)}/api/sessions", headers=_headers(bot_id),
                                      json={"title": f"{title} ({time.strftime('%b %-d %-I.%M %p')})"})
            r.raise_for_status()
            data = r.json()
        sid = (data.get("session") or {}).get("id") or data.get("id")
        if not sid:
            raise RuntimeError("Hermes did not return a session id")
        store.set_session(key, sid)
        return sid


async def _get_messages(bot_id: str, sid: str, raw_limit: int) -> httpx.Response:
    # Only the latest page: tool calls and results are messages too, so ask for a few times what is shown.
    # include_compacted: the conversation as the person had it. Without it, a compaction (lineage.py) drops every
    # message it summarized from the chat.
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get(f"{config.api_prefix(bot_id)}/api/sessions/{sid}/messages",
                                headers=_headers(bot_id),
                                params={"inline_images": "false", "order": "latest", "limit": str(raw_limit),
                                        "include_compacted": "true"})


async def messages(bot_id: str, limit: int = 200) -> list[dict]:
    sid = await ensure_session(bot_id)
    raw_limit = min(500, max(40, limit * 4))
    r = await _get_messages(bot_id, sid, raw_limit)
    if r.status_code == 404:  # Hermes pruned the session: start a new one
        sid = await ensure_session(bot_id, fresh=True)
        r = await _get_messages(bot_id, sid, raw_limit)
    r.raise_for_status()
    data = r.json()
    raw = (data.get("data") or data.get("messages")) if isinstance(data, dict) else data
    raw = raw or []
    remembered: set[str] = set()
    try:
        # Compaction (lineage.py) shortens old tool results, and a shortened one no longer names its card, and gives the
        # messages it keeps new ids: remember the calls that put up a card, and keep each message's first id.
        from . import lineage
        calls = {str(m.get("tool_call_id")) for m in raw if m.get("role") == "tool" and m.get("tool_call_id")}
        found = _carded_calls(raw, bot_id, sid)
        lineage.remember_card(bot_id, found)
        remembered = lineage.carded(bot_id, calls - found)
    except Exception:
        log.warning("could not read the remembered cards", exc_info=True)
    shown = _shown(raw, bot_id, sid, remembered=remembered)[-limit:]
    try:
        return lineage.stable_ids(bot_id, shown)
    except Exception:
        log.warning("could not keep message ids stable", exc_info=True)
        return shown


_CARD_REQUEST = re.compile(r"\bapp \(request (\d+)\)")
_HERMES_TIMEOUT = "BLOCKED: Command timed out without user response"


def _text(content) -> str:
    if isinstance(content, list):
        content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def _raised_card(content, bot_id: str | None, session_id: str | None) -> bool:
    """Did this tool result come from a call that put a card in front of Colin? The gate's block names its card
    (request 12), and that card must be one this bot raised in this conversation (Oct 8 QA round 8, L1: the note
    beside the call is judged by the card it raised, not by words). Hermes giving up on its own prompt names no
    card; its result is Hermes's own text."""
    text = _text(content)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        text = " ".join(str(data.get(k) or "") for k in ("error", "output", "message"))
    if text.lstrip().startswith(_HERMES_TIMEOUT):
        return True
    m = _CARD_REQUEST.search(text)
    if not m:
        return False
    if bot_id is None:
        return True
    try:
        from . import consent
        card = consent.get_request(int(m.group(1)))
    except Exception:
        return False
    if not card or card.get("bot_id") != bot_id:
        return False
    if not card.get("session_id") or not session_id or card.get("session_id") == session_id:
        return True
    from . import lineage
    return lineage.same(bot_id, card.get("session_id"), session_id)


def _carded_calls(raw: list[dict], bot_id: str | None, session_id: str | None) -> set[str]:
    """The tool calls whose results show they put up a card."""
    return {str(m.get("tool_call_id")) for m in raw
            if m.get("role") == "tool" and m.get("tool_call_id")
            and _raised_card(m.get("content"), bot_id, session_id)}


def _offer_short(bot_id: str, full: str, short: str) -> None:
    """The Zip's own short version of a long reply, kept for the chat when it holds up (shortreply.offer)."""
    try:
        from . import shortreply
        shortreply.offer(bot_id, full, short)
    except Exception:
        log.warning("could not keep %s's own short version", bot_id, exc_info=True)


def _shown(raw: list[dict], bot_id: str | None = None, session_id: str | None = None,
           remembered: set[str] | frozenset = frozenset()) -> list[dict]:
    """The user and assistant messages of a Hermes session. An assistant message whose tool call put up a card is
    marked `before_card` (history.classify decides whether Colin sees it). With `bot_id`, the card must be one this
    bot raised (in `session_id`, when the card names its session). `remembered`: calls the hub saw put up a card
    before a compaction shortened their results (lineage.py)."""
    carded = _carded_calls(raw, bot_id, session_id) | set(remembered)
    out = []
    for m in raw:
        role = m.get("role")
        if m.get("display_kind") == "hidden":   # a compaction's summary, never a chat message
            continue
        content = _text(m.get("content"))
        if role == "assistant":
            content, own_short = chatter.split_short(content)
            content = chatter.without_hermes_notes(content)
            if own_short and bot_id:
                _offer_short(bot_id, content, own_short)
        if role not in ("user", "assistant") or not content.strip():
            continue
        item = {"id": m.get("id"), "role": role, "text": content, "ts": m.get("timestamp") or m.get("created_at")}
        calls = m.get("tool_calls") if role == "assistant" else None
        if carded and isinstance(calls, list) and any(isinstance(c, dict) and str(c.get("id")) in carded for c in calls):
            item["before_card"] = True
        out.append(item)
    return out


def primary_provider(bot_id: str) -> str:
    """The provider a Zip's own config.yaml names (model.provider), read fresh each time; Claude when unreadable."""
    try:
        path = config.HERMES_HOME / ("config.yaml" if bot_id == "default" else f"profiles/{bot_id}/config.yaml")
        m = re.search(r"^model:\s*\n(?:[ \t]+.*\n)*?[ \t]+provider:\s*([\w.-]+)", path.read_text(), re.M)
        return m.group(1) if m else "claude-subscription-directsdk-experimental"
    except OSError:
        return "claude-subscription-directsdk-experimental"


BACKUP_KEEP_DAYS = 30


def _backup_key(bot_id: str, text: str) -> str:
    return hashlib.sha256(f"{bot_id}\n{' '.join((text or '').split())}".encode()).hexdigest()


def _backup_table():
    conn = store.db()
    conn.execute("CREATE TABLE IF NOT EXISTS backup_replies (key TEXT PRIMARY KEY, bot_id TEXT, created_at INTEGER)")
    return conn


def note_backup(bot_id: str, *texts: str) -> None:
    with store._lock:
        conn = _backup_table()
        now = int(time.time())
        for t in dict.fromkeys(t for t in texts if t):
            conn.execute("INSERT OR REPLACE INTO backup_replies (key, bot_id, created_at) VALUES (?, ?, ?)",
                         (_backup_key(bot_id, t), bot_id, now))
        conn.execute("DELETE FROM backup_replies WHERE created_at < ?", (now - BACKUP_KEEP_DAYS * 86400,))
        conn.commit()


def mark_backup(bot_id: str, msgs: list[dict]) -> list[dict]:
    """The chat with `backup: true` on each reply the backup model gave."""
    try:
        conn = _backup_table()
        out = []
        for m in msgs:
            if m.get("role") == "assistant" and any(
                    conn.execute("SELECT 1 FROM backup_replies WHERE key = ?", (_backup_key(bot_id, t),)).fetchone()
                    for t in (m.get("text"), m.get("full"), m.get("display")) if t):
                m = {**m, "backup": True}
            out.append(m)
        return out
    except Exception:
        log.warning("could not read the backup replies", exc_info=True)
        return msgs


async def stream_turn(bot_id: str, text: str, team: bool = False, lane: str | None = None) -> AsyncIterator[dict]:
    """Run one turn in the bot's session, yielding simplified events for the app.

    Event shapes: {"type": "status", "text"} while tools run, {"type": "reply", "text"} for the
    final answer, {"type": "error", "text"} on failure, then {"type": "done"}.
    """
    lane = "team" if team else lane
    chat = lane is None
    if chat:
        activity.begin(bot_id)
    try:
        async for ev in _stream_turn(bot_id, text, lane):
            if chat:
                activity.note(bot_id, ev)
            yield ev
    finally:
        if chat:
            activity.end(bot_id)
            from . import usage
            usage.turn_ended(bot_id)


async def _stream_turn(bot_id: str, text: str, lane: str | None) -> AsyncIterator[dict]:
    event = None
    final_text = None
    backup = False
    run_id = None
    memory_open = told_memory = False
    last_error.pop(bot_id, None)
    last_yield.pop(bot_id, None)
    try:
        sid = await (ensure_session(bot_id, team=True) if lane == "team" else
                     ensure_session(bot_id, lane=lane) if lane else ensure_session(bot_id))
        url = f"{config.api_prefix(bot_id)}/api/sessions/{sid}/chat/stream"
        async with httpx.AsyncClient(timeout=TURN_TIMEOUT) as client:
            async with client.stream("POST", url, headers=_headers(bot_id), json={"input": text}) as r:
                if r.status_code == 404:
                    store.set_session(session_key(bot_id, lane), "")
                if r.status_code >= 400:
                    last_error[bot_id] = "The bot could not start that turn. Try again in a moment."
                    yield {"type": "error", "text": last_error[bot_id]}
                    yield {"type": "done"}
                    return
                async for line in r.aiter_lines():
                    if line.startswith(":") or not line.strip():
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:])
                    except ValueError:
                        continue
                    if not run_id and isinstance(data, dict) and data.get("run_id"):
                        run_id = data["run_id"]
                        yield {"type": "run", "id": run_id}
                    if event == "approval.request" and isinstance(data, dict):
                        from . import approvals
                        waiting = await approvals.handle(bot_id, run_id or "", data, session_id=sid,
                                                         team=lane is not None)
                        if waiting:
                            yield {"type": "status", "text": waiting, "waiting": True}
                    elif event == "tool.started":
                        tool = data.get("tool_name") or data.get("tool")
                        step = activity.label(tool, data.get("args"), data.get("preview"))
                        if step:
                            yield {"type": "status", "text": step}
                        if lane is None:
                            if screen.uses_computer(tool):
                                screen.note_agent_use(bot_id)
                            memory_open = tool == "memory" and events.writes_memory(data.get("args"))
                    elif event in ("tool.completed", "tool.failed") and memory_open \
                            and (data.get("tool_name") or data.get("tool")) == "memory":
                        memory_open = False
                        if event == "tool.completed" and not data.get("error") and not told_memory:
                            told_memory = True
                            events.memory_updated(bot_id)
                    elif event in ("assistant.commentary",):
                        if data.get("text"):
                            yield {"type": "commentary", "text": data["text"]}
                    elif event == "assistant.delta":
                        if data.get("delta"):
                            yield {"type": "delta", "text": data["delta"]}
                    elif event == "assistant.completed":
                        final_text = data.get("content") or final_text
                        served = (data.get("runtime") or {}).get("provider")
                        backup = bool(served) and served != primary_provider(bot_id)
                    elif event == "run.cancelled" and run_id and run_id in yielded_runs:
                        yielded_runs.discard(run_id)
                        last_yield[bot_id] = True
                        yield {"type": "yielded"}
                    elif event in ("run.failed", "run.cancelled"):
                        raw = str(data.get("error") or "")
                        if event == "run.failed":
                            from . import health
                            health.turn_finished(raw)
                            limited = any(w in raw.lower() for w in health.LIMIT_WORDS)
                        else:
                            limited = False
                        last_error[bot_id] = ("That reply stopped partway." if event == "run.cancelled"
                                              else (limit_line(raw) if limited else
                                                    "That reply ran into a problem. Try again in a moment."))
                        yield {"type": "error", "text": last_error[bot_id]}
    except Exception:
        name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "Zero"
        last_error[bot_id] = f"{name} lost its connection partway through, so that reply didn't finish. Try again."
        yield {"type": "error", "text": last_error[bot_id]}
    written = final_text
    final_text, own_short = chatter.split_short(final_text)
    final_text = chatter.without_hermes_notes(final_text)
    if final_text and own_short:
        _offer_short(bot_id, final_text, own_short)
    if final_text:
        from . import health
        health.turn_finished(None, backup=backup)
        if backup:
            try:
                note_backup(bot_id, written, final_text)
            except Exception:
                log.warning("could not note a backup reply", exc_info=True)
        yield {"type": "reply", "text": final_text, **({"backup": True} if backup else {})}
    yield {"type": "done"}


async def steer(bot_id: str, run_id: str, text: str) -> bool:
    """Gives a running turn more to go on; the bot sees it right after its current step."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/v1/runs/{run_id}/steer",
                                  headers=_headers(bot_id), json={"input": text})
            return r.status_code == 200 and bool(r.json().get("accepted"))
    except Exception:
        return False


last_error: dict[str, str] = {}
yielded_runs: set[str] = set()
last_yield: dict[str, bool] = {}


async def stop(bot_id: str, run_id: str) -> bool:
    """Interrupts a running turn (Hermes's POST /v1/runs/{run}/stop). The stream then ends with run.cancelled."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/v1/runs/{run_id}/stop", headers=_headers(bot_id), json={})
            return r.status_code == 200
    except Exception:
        return False


async def run_turn(bot_id: str, text: str, team: bool = False, lane: str | None = None) -> str | None:
    """Run a turn to completion and return the reply text. A run that was cut off (Stop, or a run held on a card that
    made way for the person's message) ends with Hermes's "Operation interrupted: ..." note as its final text; that is
    never the Zip's words, so it is no reply (night review 2: the reply after a card and Zero's board relay posted and
    pushed it, and the relay counted its news as told)."""
    from . import chatter
    reply = None
    async for ev in stream_turn(bot_id, text, team=team, lane=lane):
        if ev["type"] == "reply":
            reply = None if chatter.interrupted(ev["text"]) else ev["text"]
    return reply


# A usage limit with its retry time, when the provider's error names one (#52: "explain which limit applies and retry
# time"). Seconds or minutes to wait ("try again in 37 minutes", "retry after 120"), or a clock time ("resets at
# 3:40 PM"), or an epoch ("resets_at": 1791600000).
_RETRY_IN = re.compile(r"(?:in|after)\s+(\d{1,5})\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b", re.I)
_RETRY_AFTER = re.compile(r"retry[-_ ]after\W{0,3}(\d{1,6})\b", re.I)
_RESETS_AT = re.compile(r"reset\w*\W{0,4}(?:at\W{0,3})?(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)", re.I)
_RESETS_EPOCH = re.compile(r"reset\w*\W{0,4}(1[6-9]\d{8})\b", re.I)


def retry_at(raw: str, now: float | None = None) -> float | str | None:
    """When a limited turn can run again: an epoch time, a clock time as the provider wrote it, or None."""
    import time as _time
    now = _time.time() if now is None else now
    t = str(raw or "")
    m = _RESETS_EPOCH.search(t)
    if m:
        return float(m.group(1))
    m = _RETRY_IN.search(t)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now + n * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    m = _RETRY_AFTER.search(t)
    if m:
        return now + int(m.group(1))
    m = _RESETS_AT.search(t)
    if m:
        return " ".join(m.group(1).lower().replace(".", "").replace("am", " a.m.").replace("pm", " p.m.").split())
    return None


def limit_line(raw: str, now: float | None = None) -> str:
    """The plain line for a turn the plan's limit stopped: which limit, and when to try again when it is known."""
    import time as _time
    from datetime import datetime
    from . import home
    now = _time.time() if now is None else now
    when = retry_at(raw, now)
    if isinstance(when, float):
        d = datetime.fromtimestamp(when, home.TZ)
        when = d.strftime("%-I:%M %p").replace(":00 ", " ").replace("AM", "a.m.").replace("PM", "p.m.")
        if d.date() != datetime.fromtimestamp(now, home.TZ).date():
            when += " " + d.strftime("%A")
    head = "Your Zips have hit their plan's usage limit, so this reply didn't run."
    if when:
        return f"{head} Try again after {when}" + ("" if when.endswith(".") else ".")
    return f"{head} Try again once the limit resets; you'll get a note when they are back."


TOOL_LABELS = {
    "kanban_create": "Handing work to the team",
    "kanban_show": "Checking the board",
    "kanban_list": "Checking the board",
    "kanban_complete": "Wrapping up the task",
    "kanban_comment": "Leaving a note on the task",
    "connectors": "Using your connected accounts",
    "tally": "Reading transactions",
    "terminal": "Working on its computer",
    "read_file": "Reading a file",
    "write_file": "Writing a file",
    "web_search": "Searching the web",
    "web_extract": "Reading a web page",
    "browser_exec": "Using its browser",
    "computer_use": "Using its computer",
    "run_steps": "Clicking through steps",
    "memory": "Updating memory",
    "skill_view": "Reading a skill",
}


def _tool_label(name: str | None, preview: str | None) -> str:
    """Kept for older callers: the plain words now come from activity.label, which never shows a tool name."""
    return activity.label(name, None, preview) or "Working"
