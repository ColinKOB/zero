""
















from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from datetime import datetime

from . import factdates, facts, home, lineage, shifts, store, who

log = logging.getLogger("taffy.rightnow")

MAX_CHARS = 800
LINE_MAX = 220
EVERY_TURNS = 8
RECENT_DAYS = 21
KINDS = ("plan", "decision")

SCHEMA = """
CREATE TABLE IF NOT EXISTS right_now_shown (session_id TEXT PRIMARY KEY, turns INTEGER NOT NULL, at INTEGER NOT NULL,
    digest TEXT NOT NULL);
"""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)


def _when(f: dict, now: float) -> str:
    ""

    today = datetime.fromtimestamp(now, home.TZ).date()
    ahead = [d for _, _, d in factdates.days(f["text"], f["observed_at"]) if d >= today]
    if ahead:
        return min(ahead).strftime("%a %b %-d")
    if f.get("valid_until"):
        return "through " + factdates.ended_words(f["valid_until"])
    return ""


def _rows(bot_id: str, now: float) -> list[dict]:
    ""

    where, args = facts._visible(bot_id)
    holds, now_arg = facts.live()
    rows = [dict(r) for r in facts._db().execute(
        f"SELECT * FROM facts WHERE {holds} AND {where} AND (valid_until IS NOT NULL OR (kind IN "
        f"({','.join('?' * len(KINDS))}) AND observed_at > ?))", now_arg + args + KINDS +
        (int(now - RECENT_DAYS * 86400),))]
    dated = sorted((f for f in rows if f.get("valid_until")), key=lambda f: (f["valid_until"], f["id"]))


    today = datetime.fromtimestamp(now, home.TZ).date()
    undated = sorted((f for f in rows if not f.get("valid_until")
                      and not factdates.all_past(f["text"], f["observed_at"], today)), key=lambda f: -f["id"])
    return dated + undated


def _shown(bot_id: str, now: float, limit: int) -> tuple[str, list[tuple[int, str]]]:
    ""
    first = who.first()
    head = (f"[Right now, from the team's notes ({datetime.fromtimestamp(now, home.TZ).strftime('%a %b %-d')}): "
            f"{who.possessive()} current plans and decisions. Go by these over older memory; a newer word from "
            f"{first} wins.]")
    out, size = [], len(head)
    for f in _rows(bot_id, now):
        when = _when(f, now)
        words = f["text"] if len(f["text"]) <= LINE_MAX else f["text"][:LINE_MAX].rsplit(" ", 1)[0] + "..."
        line = f"- {when}: {words}" if when else f"- {words}"
        if size + len(line) + 1 > limit:
            continue
        out.append((f["id"], line))
        size += len(line) + 1
    return head, out


def text(bot_id: str, now: float | None = None, limit: int = MAX_CHARS) -> str:
    ""
    from . import calendar_noticers, factkeeper, meetprep
    now = facts.now() if now is None else now
    head, shown = _shown(bot_id, now, limit)
    lines = (factkeeper.conflict_lines(bot_id) + meetprep.leave_lines(bot_id, now) + calendar_noticers.site_lines(bot_id, now)
             + shifts.lines(bot_id, now) + [line for _, line in shown])
    return "\n".join([head] + lines) if lines else ""


def ids(bot_id: str, now: float | None = None) -> set[int]:
    ""
    return {fid for fid, _ in _shown(bot_id, facts.now() if now is None else now, MAX_CHARS)[1]}


def _digest(block: str) -> str:
    return hashlib.sha256(block.split("\n", 1)[-1].encode()).hexdigest()[:16]


def for_turn(bot_id: str, session_id: str, first: bool, work: bool = False, sent_session: str = "") -> str:
    ""

    if work and not first:
        return ""
    from . import openloops, weeklook, zipcheck

    week = weeklook.text() if work and weeklook.brief_turn(bot_id, session_id) else ""

    brief = work and weeklook.brief_job(bot_id, session_id)
    jobs = openloops.waiting_text() if brief else ""

    computers = zipcheck.brief_text() if brief else ""
    block = "\n\n".join(b for b in (text(bot_id), week, jobs, computers) if b)
    if not block or not session_id:
        return block if first else ""
    t = int(time.time())
    c = store.db()
    row = c.execute("SELECT * FROM right_now_shown WHERE session_id=?", (session_id,)).fetchone()
    digest = _digest(block)
    due = (first or work or row is None or row["turns"] + 1 >= EVERY_TURNS or row["digest"] != digest
           or lineage.compacted_at(bot_id, {session_id, sent_session}) > row["at"])
    if due and (brief or not work):
        from . import factkeeper
        factkeeper.mark_offered(bot_id)
    with store._lock:
        if due:
            c.execute("INSERT OR REPLACE INTO right_now_shown (session_id, turns, at, digest) VALUES (?, 0, ?, ?)",
                      (session_id, t, digest))
            c.execute("DELETE FROM right_now_shown WHERE at < ?", (t - 30 * 86400,))
        else:
            c.execute("UPDATE right_now_shown SET turns = turns + 1 WHERE session_id=?", (session_id,))
        c.commit()
    return block if due else ""
