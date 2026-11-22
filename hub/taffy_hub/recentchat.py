""














from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from datetime import datetime

from . import facts, home, lineage, store, who

log = logging.getLogger("taffy.recentchat")







SUMMARY_SECTIONS = ("User Messages", "Active State", "Unfinished Requests", "Critical Context", "Key Decisions")
SECTION_MAX = 3000
SUMMARY_MAX = 8000
SUMMARY_MAX_AGE = 7 * 86400
WINDOW_SEC = 24 * 3600
MESSAGES_FETCH = 80
MESSAGES_MAX = 40
MESSAGE_MAX = 600
MESSAGES_CHARS = 8000
FETCH_TIMEOUT_SEC = 8.0
SAID_WINDOW_SEC = 72 * 3600
SAID_MAX = 80







HOOK_MAX = 10_000
BLOCK_MAX = 9_500


SUMMARY_FLOOR = 3_000
CUT = "\n(cut short)"

_HEADING = re.compile(r"(?m)^## +(.+?)[ \t]*$")

_MARKER = re.compile(r"(?m)^(>[ \t]*)?\[Chat turn: [a-f0-9]{32}\][ \t]*(\n|$)|\[Chat turn: [a-f0-9]{32}\][ \t]*")

_HUB_NOTE = re.compile(r"(?m)^(>[ \t]*)?\[Hub note[^\n]*(\n>[ \t]*[^\s\[][^\n]*)*\n?")
_PREAMBLE = "[CONTEXT COMPACTION"
_END = "--- END OF CONTEXT SUMMARY"


def _chat_sessions(conn: sqlite3.Connection, root: str) -> list[str]:
    ""
    out = [root]
    i = 0
    while i < len(out) and len(out) < 50:
        for (sid,) in conn.execute(
                "SELECT s.id FROM sessions s JOIN sessions p ON p.id = s.parent_session_id "
                "WHERE s.parent_session_id = ? AND p.end_reason = 'compression' AND COALESCE(s.source, '') != 'tool'",
                (out[i],)):
            if sid not in out:
                out.append(sid)
        i += 1
    return out


def latest_summary(bot_id: str) -> tuple[float, str] | None:
    ""
    root = store.session_for(bot_id)
    path = lineage._state_db(bot_id)
    if not root or not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            ids = _chat_sessions(conn, root)

            row = conn.execute(
                f"SELECT timestamp, content FROM messages WHERE session_id IN ({','.join('?' * len(ids))}) "
                "AND _compressed_summary = 1 ORDER BY timestamp DESC, id DESC LIMIT 1", ids).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        log.warning("could not read %s's compaction summary", bot_id, exc_info=True)
        return None
    if not row or not row[1]:
        return None
    return float(row[0] or 0), str(row[1])


def clean(text: str) -> str:
    ""
    text = _HUB_NOTE.sub("", _MARKER.sub("", text or ""))
    return "\n".join(line for line in text.split("\n") if not facts.secret(line)).strip()


def _cut(text: str, limit: int) -> str:
    ""
    if len(text) <= limit:
        return text
    room = max(limit - len(CUT), 0)
    cut = text[:room]
    cut = cut[:cut.rfind("\n")] if "\n" in cut[room // 2:] else cut.rsplit(" ", 1)[0]
    return cut.rstrip() + CUT


def summary_text(raw: str, limit: int = SUMMARY_MAX, wanted: tuple[str, ...] = SUMMARY_SECTIONS,
                 section_max: int = SECTION_MAX) -> str:
    ""

    parts = _HEADING.split(raw or "")
    sections = list(zip(parts[1::2], parts[2::2]))
    picked = []
    for want in wanted:
        for name, body in sections:
            if name.startswith(want):
                body = clean(body)
                if body:
                    picked.append((name, body))
                break
    if not picked:
        body = (raw or "").split(_END, 1)[0]
        if body.lstrip().startswith(_PREAMBLE):
            body = body.split("\n", 1)[1] if "\n" in body else ""
        return _cut(clean(body), limit)

    def joined(cap: int) -> str:
        return "\n\n".join(f"{name}:\n{_cut(body, cap)}" for name, body in picked)
    cap = section_max
    text = joined(cap)
    while len(text) > limit and cap > 200:
        cap = cap * 9 // 10
        text = joined(cap)
    return _cut(text, limit)


def when(ts: float) -> str:
    d = datetime.fromtimestamp(ts, home.TZ)
    return d.strftime("%a %b %-d, %-I:%M %p")



def said_before(bot_id: str, until: float, window: float = SAID_WINDOW_SEC) -> list[tuple[float, str]]:
    ""




    from . import history
    root = store.session_for(bot_id)
    path = lineage._state_db(bot_id)
    if not root or not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            ids = _chat_sessions(conn, root)
            rows = conn.execute(
                f"SELECT timestamp, content FROM messages WHERE session_id IN ({','.join('?' * len(ids))}) "
                "AND role = 'user' AND _compressed_summary = 0 "
                "AND COALESCE(display_kind, '') NOT IN ('hidden', 'failed_turn') AND timestamp BETWEEN ? AND ? "
                "ORDER BY timestamp, id", (*ids, until - window, until)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        log.warning("could not read %s's messages", bot_id, exc_info=True)
        return []
    out, seen = [], set()
    for ts, content in rows:

        words = history.colin_words([{"role": "user", "text": content or ""}])
        text = clean(" ".join(words[0]["display"].split())) if words else ""
        if text and (round(ts, 3), text) not in seen:
            seen.add((round(ts, 3), text))
            out.append((float(ts), text))
    return out[-SAID_MAX:]


def _latest(bot_id: str, now: float) -> tuple[float, str] | None:
    ""
    found = latest_summary(bot_id)
    return found if found and now - found[0] <= SUMMARY_MAX_AGE else None


def _dated(found: tuple[float, str] | None, limit: int = SUMMARY_MAX) -> str:
    ""
    text = summary_text(found[1], limit) if found else ""
    return f"{_dated_head(found)}{text}" if text else ""


def _dated_head(found: tuple[float, str]) -> str:
    return f"Summary of the chat up to {when(found[0])}:\n"


def summary_part(bot_id: str, now: float | None = None) -> str:
    ""
    return _dated(_latest(bot_id, time.time() if now is None else now))


async def recent_messages(bot_id: str, now: float | None = None) -> list[dict]:
    ""
    from . import hermes, history
    now = time.time() if now is None else now
    if not store.session_for(bot_id):
        return []
    msgs = history.visible(await hermes.messages(bot_id, limit=MESSAGES_FETCH))
    return [m for m in msgs if isinstance(m.get("ts"), (int, float)) and m["ts"] >= now - WINDOW_SEC][-MESSAGES_MAX:]


MESSAGES_HEAD = "Messages from the last day, oldest first:\n"


def lines(msgs: list[dict], limit: int = MESSAGES_CHARS) -> str:
    ""

    out, used = [], 0
    for m in reversed(msgs):
        text = clean(" ".join(str(m.get("display") or m.get("text") or "").split()))
        if not text:
            continue
        speaker = who.first() if m.get("role") == "user" else "You"
        line = f"- {when(m['ts'])}, {speaker}: {text[:MESSAGE_MAX]}"
        if used + len(line) > limit:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(reversed(out))


def _joined(summary: str, said: str) -> str:
    return "\n\n".join(([summary] if summary else []) + ([MESSAGES_HEAD + said] if said else []))


def fit(found: tuple[float, str] | None, msgs: list[dict], limit: int | None = None) -> str:
    ""


    summary, said = _dated(found), lines(msgs)
    text = _joined(summary, said)
    if limit is None or len(text) <= limit:
        return text
    keep = min(len(summary), SUMMARY_FLOOR)
    said = lines(msgs, limit - keep - (2 if summary else 0) - len(MESSAGES_HEAD))
    room = limit - (len(MESSAGES_HEAD) + len(said) + 2 if said else 0) - (len(_dated_head(found)) if found else 0)
    summary = _dated(found, room) if found and room >= 200 else ""
    text = _joined(summary, said)
    return text if len(text) <= limit else _cut(text, limit)


async def body(bot_id: str, now: float | None = None, timeout: float = FETCH_TIMEOUT_SEC,
               limit: int | None = None) -> str:
    ""
    now = time.time() if now is None else now
    found = await asyncio.to_thread(_latest, bot_id, now)
    try:
        msgs = await asyncio.wait_for(recent_messages(bot_id, now), timeout)
    except Exception:
        log.warning("could not read %s's recent chat", bot_id, exc_info=True)
        msgs = []
    return fit(found, msgs, limit)


def _head() -> str:
    first = who.first()
    return (f"[Your recent chat with {first}, from the hub. It is newer than your memory, the team notes and your "
            f"earlier reports. When they disagree, go by the chat and the dates in it. It is background: do the task "
            f"you were given.]\n")


async def block(bot_id: str, now: float | None = None, timeout: float = FETCH_TIMEOUT_SEC,
                limit: int | None = None) -> str:
    ""

    head = _head()
    text = await body(bot_id, now, timeout, None if limit is None else limit - len(head))
    return head + text if text else ""
