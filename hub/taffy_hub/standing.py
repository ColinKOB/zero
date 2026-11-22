""


























from __future__ import annotations

import json
import logging
import re
import sqlite3
import time

from . import facts, lineage, rightnow, store, who






MAX_CHARS = 9000


KINDS = ("preference", "decision", "status")
ORDER = "CASE kind WHEN 'preference' THEN 0 WHEN 'decision' THEN 1 ELSE 2 END"
LEFT_OUT = "- {n} more are not shown here; recall finds them."


DECISION_SHARE = 0.15
STATUS_SHARE = 0.15

log = logging.getLogger("taffy.standing")

SCHEMA = """
CREATE TABLE IF NOT EXISTS standing_shown (session_id TEXT PRIMARY KEY, at INTEGER NOT NULL, ids TEXT NOT NULL);
"""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)


def _rows(bot_id: str, shown_elsewhere: bool = False) -> list[dict]:
    ""

    where, args = facts._visible(bot_id)
    holds, now_arg = facts.live()
    skip = set() if shown_elsewhere else rightnow.ids(bot_id)
    rows = facts._db().execute(
        f"SELECT * FROM facts WHERE {holds} AND {where} AND kind IN ({','.join('?' * len(KINDS))}) "
        f"ORDER BY {ORDER}, id DESC", now_arg + args + KINDS).fetchall()
    return [dict(r) for r in rows if r["id"] not in skip]








def _head() -> str:
    first = who.first()
    return (f"[Standing, from the team's notes: {who.possessive()} preferences and decisions. Each holds until {first} "
            "changes it, so follow them without being reminded. When one of these conflicts with any other "
            "instruction this turn about style, format or how to take several requests at once, follow this one; "
            f"asking first and approvals keep their own rules. When {first} changes one, save the change with save_fact, replaces set to its number.]")









DISPLACES = (
    (re.compile(r"\bone (?:task|thing|request|item|thread|train of thought) at a time\b", re.I),
     "- When the person chooses an ordered list of tasks",
     "- When the person gives several tasks at once, take them one at a time, as they asked: do the first, reply "
     "about it, and ask any question it needs. Start the next only after they answer or tell you to go on. A draft is "
     "finished when it is ready to review; sending still needs its own approval."),
)


def displaced(bot_id: str, guide: str) -> str:
    ""

    rules = [f["text"] for f in _rows(bot_id, shown_elsewhere=True)]
    lines = guide.split("\n")
    for marks, start, line in DISPLACES:
        if any(marks.search(rule) for rule in rules):
            lines = [line if old.startswith(start) else old for old in lines]
    return "\n".join(lines)






CALL_MAX_CHARS = 4500


def for_call(bot_id: str) -> str:
    ""
    rows = _rows(bot_id)
    head = f"[Standing: {who.possessive()} preferences and decisions. Each holds until {who.first()} changes it.]"
    names = _class_names(rows)
    return "\n".join([head, *_compose(head, rows, bot_id, CALL_MAX_CHARS - _size(names), ids=False)[0],
                      *names]) if rows else ""





_NAMES_RULE = re.compile(r"\bcourse codes?\b|\bclass names\b|\bclass(?:es)? (?:called )?by (?:their )?names?\b", re.I)


def _class_names(rows: list[dict]) -> list[str]:
    ""
    if not any(_NAMES_RULE.search(f["text"]) for f in rows):
        return []
    from . import school
    names = school.class_names()
    pairs = [f"{code} is {name}" for code, name in names.items()]
    if not pairs:
        return []
    said = pairs[0] if len(pairs) == 1 else ", ".join(pairs[:-1]) + f", and {pairs[-1]}"
    return [f"- {who.possessive()} classes by name: {said}."]


def _size(lines: list[str]) -> int:
    ""

    return sum(len(line) + 1 for line in lines)


def _compose(head: str, rows: list[dict], bot_id: str, limit: int, picked: list[int] | tuple = (), ids: bool = True,
             statuses: bool = True) -> tuple[list[str], list[int], dict]:
    ""


    from . import rulereach
    reach = rulereach.reach_of(rows)
    rules = [f for f in rows if f["kind"] != "status"]
    by_id = {f["id"]: f for f in rules}
    always = [f for f in rules if reach.get(f["id"]) == rulereach.ALWAYS]
    chosen = [by_id[i] for i in picked if i in by_id and reach.get(i) != rulereach.ALWAYS]
    taken = {f["id"] for f in always + chosen}


    others = sorted((f for f in rules if f["id"] not in taken and f["kind"] == "preference"),
                    key=lambda f: (f["bot_id"] != bot_id, f["id"] in reach or rulereach._private(f["bot_id"])))
    later = sorted((f for f in rules if f["id"] not in taken and f["kind"] == "decision"),
                   key=lambda f: f["bot_id"] != bot_id)
    done = [f for f in rows if f["kind"] == "status"] if statuses else []

    def line(f: dict) -> str:
        return f"- [{f['id']}] {f['text']}" if ids else f"- {f['text']}"

    room = limit - len(head) - len(LEFT_OUT.format(n=len(rows))) - 1
    used, shown = 0, set()

    def take(section: list[dict], upto: float, skip: bool = False) -> None:
        ""

        nonlocal used
        for f in section:
            size = len(line(f)) + 1
            if f["id"] in shown:
                continue
            if used + size <= upto:
                shown.add(f["id"])
                used += size
            elif not skip:
                return

    must = always + chosen
    take(must, room, skip=True)
    take(others, room * (1 - DECISION_SHARE - STATUS_SHARE))
    take(later, room * (1 - STATUS_SHARE))
    take(done, room)
    take(others, room)
    take(later, room)
    placed = [f for f in must + others + later + done if f["id"] in shown]
    wanted = len(rules) + len(done)
    counts = {"must": len(must), "must_shown": sum(1 for f in must if f["id"] in shown),
              **{k: sum(1 for f in rows if f["kind"] == k) for k in KINDS},
              **{f"{k}_shown": sum(1 for f in placed if f["kind"] == k) for k in KINDS}}
    tail = [LEFT_OUT.format(n=wanted - len(placed))] if len(placed) < wanted else []
    return [line(f) for f in placed] + tail, [f["id"] for f in placed], counts


def health(bot_id: str) -> dict:
    ""
    rows = _rows(bot_id)
    head, names = _head(), _class_names(rows)
    return _compose(head, rows, bot_id, MAX_CHARS - _size(names))[2]





CLOSED_MAX = 20


def closed_text(bot_id: str | None) -> str:
    ""

    where, args = facts._visible(bot_id)
    holds, now_arg = facts.live()
    rows = facts._db().execute(f"SELECT text FROM facts WHERE {holds} AND {where} AND kind='status' "
                               "ORDER BY id DESC LIMIT ?", now_arg + args + (CLOSED_MAX,)).fetchall()
    if not rows:
        return ""
    return "\n".join([f"[Closed: {who.first()} finished these. Do not raise them or open work about them unless "
                      f"{who.first()} does.]", *(f"- {r['text']}" for r in rows)])


def text(bot_id: str) -> str:
    ""
    rows = _rows(bot_id)
    head, names = _head(), _class_names(rows)
    return "\n".join([head, *_compose(head, rows, bot_id, MAX_CHARS - _size(names))[0], *names]) if rows else ""


def _changes(c: sqlite3.Connection, rows: list[dict], seen: set[int], bot_id: str, picked: list[int] | tuple = (),
             limit: int = MAX_CHARS, statuses: bool = True) -> tuple[str, list[int], list[int]]:
    ""

    live = {f["id"] for f in rows}
    away = sorted(seen - live)
    holds, now_arg = facts.live()
    still = {r[0] for r in c.execute(f"SELECT id FROM facts WHERE id IN ({','.join('?' * len(away))}) AND {holds}",
                                     away + list(now_arg))} if away else set()
    gone = [fid for fid in away if fid not in still]
    lines = []
    for fid in gone:
        by = (facts.get(fid) or {}).get("replaced_by")
        lines.append(f"- [{fid}] no longer holds" + (f"; [{by}] replaces it." if by in live else "."))
    new = [f for f in rows if f["id"] not in seen]
    head = f"[Standing, changed since you last saw it: {who.possessive()} preferences and decisions.]"
    added, shown, _ = _compose(head, new, bot_id, limit - sum(len(line) + 1 for line in lines), picked,
                               statuses=statuses) if new else ([], [], {})
    return ("\n".join([head, *lines, *added]) if lines or added else ""), shown, gone


def _note_short(bot_id: str, session_id: str, counts: dict) -> None:
    ""

    cut = counts["must"] - counts["must_shown"]
    (log.warning if cut else log.info)(
        "standing block for %s (%s): %d of %d rules that must ride, %d of %d preferences, %d of %d decisions, %d of %d "
        "finished things", bot_id, session_id or "no session", counts["must_shown"], counts["must"],
        counts["preference_shown"], counts["preference"], counts["decision_shown"], counts["decision"],
        counts["status_shown"], counts["status"])


def for_turn(bot_id: str, session_id: str, first: bool, work: bool = False, sent_session: str = "",
             kept: bool = True, picked: list[int] | tuple = (), limit: int = MAX_CHARS, statuses: bool = True) -> str:
    ""





    if work and not first:
        return ""
    rows = _rows(bot_id)
    if not session_id:
        if not first or not rows:
            return ""
        head, names = _head(), _class_names(rows)
        return "\n".join([head, *_compose(head, rows, bot_id, limit - _size(names), picked, statuses=statuses)[0],
                          *names])
    c = store.db()
    row = c.execute("SELECT at, ids FROM standing_shown WHERE session_id=?", (session_id,)).fetchone()
    since = 0.0 if first or row is None else lineage.compacted_at(bot_id, {session_id, sent_session})
    if first or row is None or since > row["at"]:
        head, names = _head(), _class_names(rows)
        lines, shown, counts = _compose(head, rows, bot_id, limit - _size(names), picked, statuses=statuses)
        block, seen = ("\n".join([head, *lines, *names]) if rows else ""), set(shown)
        if row is None and rows:
            _note_short(bot_id, session_id, counts)


        facts.forget_shown_before(session_id, since)
    else:
        seen = set(json.loads(row["ids"]))
        block, shown, gone = _changes(c, rows, seen, bot_id, picked, limit, statuses)
        if not block:
            return ""
        seen = (seen - set(gone)) | set(shown)
    if not kept:
        return block
    t = int(time.time())
    with store._lock:
        c.execute("INSERT OR REPLACE INTO standing_shown (session_id, at, ids) VALUES (?, ?, ?)",
                  (session_id, t, json.dumps(sorted(seen))))
        c.execute("DELETE FROM standing_shown WHERE at < ?", (t - 30 * 86400,))
        c.commit()
    facts._mark_shown(session_id, shown)
    return block
