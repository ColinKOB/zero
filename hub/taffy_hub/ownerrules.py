""
























from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
import sqlite3
import time
from datetime import datetime

from . import config, facts, home, rulereach, store, who

log = logging.getLogger("taffy.ownerrules")

ENABLED = False

BUZZ, TODO, URGENT, REMINDER, INBOX, MONEY = "buzz", "todo", "urgent", "reminder", "inbox", "money"
FOLLOWUP, SIGNIN, QUESTION, UPDATE, WORDS = "followup", "signin", "question", "update", "words"
DECISIONS = {
    BUZZ: "whether a message or alert buzzes the person's phone",
    TODO: "what goes on the person's to-do list, and what a helper should do instead",
    URGENT: "what must be done today and how the things waiting on the person are ranked",
    REMINDER: "which of the person's reminders show on the to-do list",
    INBOX: "which texts and emails need the person, and how they are described",
    MONEY: "bills, charges, payments and questions about spending",
    FOLLOWUP: "whether a helper may come back to its work later on its own",
    SIGNIN: "asking the person to sign in to something",
    QUESTION: "whether to ask the person a question or raise a card",
    UPDATE: "what the person is told about finished work and news: briefs, recaps, notifications and status lines",
    WORDS: "how the words the person reads are written",
}

READ_KINDS = ("preference", "decision", "status", "plan", "other")
BATCH = 40
EVERY_SEC = 30
IDLE_SEC = 900
READ_TIMEOUT = 45
LATE_SEC = 86400
NOTE_MAX = 600
NAMES_MAX = 5
DECISIONS_MAX = 2
NAME_NOTES_MAX = 8
NAME_MIN, NAME_MAX = 3, 40
ITEM_MAX = 8000
RULES_CHARS = 12000
ANSWER_DAYS = 45
ANSWERS_MAX = 12
ANSWER_CHARS = 200

SYSTEM = "You sort the notes a team of AI helpers keeps about a person. Reply with JSON only."
READ = """These are notes a team of AI helpers keeps about {first}. Answer two things for each note.

"decisions": the one or two decisions this note is directly about, from the list below. Choose one only when the note \
itself says how that decision should go, so a helper making it would get it wrong without this note. Most notes choose \
none: a fact, a plan, something finished, a rule about one project or person, and a general rule about how to work or \
talk to {first} all choose none.
{decisions}

"names": the specific things the note is about, as at most {most} short names that a message about the same thing would \
use: a person, company, product, course, group, place or event. Use the note's own words, and add the shortest form \
people would also use ("M&S" for "M&S Produce"). Leave it empty for a note about {first} in general.

Reply with JSON only: {{"notes": [{{"n": 12, "decisions": ["{example}"], "names": ["Harbor Grocer"]}}]}}, one entry per \
note.

Notes:
{notes}"""


HEAD = ("Notes {first} gave the team, with the day each was said. Most will not apply here. Use a note only when it is "
        "plainly about this very thing or this kind of decision, and never stretch a note about one subject to another. "
        "A note that does not plainly apply changes nothing, so decide as you would without it. When one does apply and "
        "disagrees with anything else here (a calendar, an email, a note or a card), {first}'s newest word wins.")
ANSWERS_HEAD = ("What {first} answered lately to other questions. Only an answer about this same thing counts: what {first} "
                "closed, declined or said to drop stays closed unless something materially new has happened since that "
                "answer. Every other answer changes nothing.")


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
CREATE TABLE IF NOT EXISTS rule_topics (fact_id INTEGER PRIMARY KEY, digest TEXT NOT NULL, decisions TEXT NOT NULL,
    names TEXT NOT NULL, at INTEGER NOT NULL);
""")


def _notes(bot_id: str | None = None) -> list[dict]:
    ""
    where, args = facts._visible(bot_id or "default")
    holds, now_arg = facts.live()
    marks = ",".join("?" * len(READ_KINDS))
    rows = facts._db().execute(f"SELECT * FROM facts WHERE {holds} AND {where} AND kind IN ({marks}) ORDER BY id DESC",
                               now_arg + args + READ_KINDS).fetchall()
    return [f for f in map(dict, rows) if rulereach._sendable(f)]


def _topics(rows: list[dict]) -> dict[int, dict]:
    ""
    ids = [f["id"] for f in rows]
    found = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        found.update({r["fact_id"]: r for r in store.db().execute(
            f"SELECT * FROM rule_topics WHERE fact_id IN ({','.join('?' * len(part))})", part)})
    return {f["id"]: {"decisions": set(json.loads(found[f["id"]]["decisions"])),
                      "names": json.loads(found[f["id"]]["names"])}
            for f in rows if f["id"] in found and found[f["id"]]["digest"] == rulereach._digest(f["text"])}




_generation = 0


def cancel_writes() -> None:
    ""
    global _generation
    with store._lock:
        _generation += 1


def unread() -> list[dict]:
    ""
    rows = _notes()
    known = _topics(rows)
    return [f for f in rows if f["id"] not in known]


def _numbered(rows: list[dict]) -> str:
    return "\n".join(f"{f['id']}. {' '.join(str(f['text']).split())[:NOTE_MAX]}" for f in rows)


def _entries(answer: str, allowed: set[int]) -> dict[int, tuple[list[str], list[str]]]:
    ""
    out = {}
    for raw in re.findall(r"\{[^{}]*\}", str(answer or "")):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        n = entry.get("n") if isinstance(entry, dict) else None
        if not isinstance(n, int) or n not in allowed:
            continue
        said = (entry.get("decisions") if isinstance(entry.get("decisions"), list) else [])[:DECISIONS_MAX]
        named = entry.get("names") if isinstance(entry.get("names"), list) else []
        names = [" ".join(x.split()) for x in named if isinstance(x, str)]
        out[n] = ([d for d in said if d in DECISIONS],
                  [x for x in names if NAME_MIN <= len(x) <= NAME_MAX][:NAMES_MAX])
    return out


async def read_some(limit: int = BATCH, ask=None) -> int:
    ""
    generation = _generation
    rows = (await asyncio.to_thread(unread))[:limit]
    if not rows:
        return 0
    from . import shortreply
    prompt = READ.format(first=who.first(), most=NAMES_MAX, example=BUZZ, notes=_numbered(rows),
                         decisions="\n".join(f'- "{k}": {v}' for k, v in DECISIONS.items()))
    try:
        with shortreply.reading(tuple({str(f.get('bot_id') or '') for f in rows})):
            answer = await (ask or shortreply.ask_helper)(prompt, timeout=READ_TIMEOUT, system=SYSTEM,
                                                          max_tokens=60 * len(rows) + 100, effort="low", origin="ownerrules")
    except Exception as e:
        log.warning("could not read %d saved notes: %s", len(rows), type(e).__name__)
        return 0
    found = _entries(answer, {f["id"] for f in rows})
    if not found:
        log.warning("the answer about %d saved notes was not usable", len(rows))
        return 0
    t = int(time.time())
    with store._lock:
        if generation != _generation:
            return 0
        c = store.db()
        c.executemany("INSERT OR REPLACE INTO rule_topics (fact_id, digest, decisions, names, at) VALUES (?,?,?,?,?)",
                      [(f["id"], rulereach._digest(f["text"]), json.dumps(found[f["id"]][0]),
                        json.dumps(found[f["id"]][1]), t) for f in rows if f["id"] in found])
        c.commit()
    return len(found)


def late() -> int:
    ""
    cutoff = time.time() - LATE_SEC
    return sum(1 for f in unread() if f["kind"] in rulereach.RULE_KINDS and float(f.get("created_at") or 0) < cutoff)


async def run() -> None:
    await asyncio.sleep(EVERY_SEC)
    while True:
        read = 0
        try:
            if ENABLED:
                read = await read_some()
        except Exception:
            log.warning("reading the saved notes failed", exc_info=True)
        await asyncio.sleep(EVERY_SEC if read else IDLE_SEC)




def _plain(text: str) -> str:
    return f" {' '.join(re.findall(r'[^\W_]+', str(text or '').casefold()))} "


def _private(bot_id: str | None) -> bool:
    return bool(bot_id) and (rulereach._private(bot_id) or config.is_sandbox(bot_id))


def rules_for(decision: str | tuple[str, ...], item: str = "", bot_id: str | None = None,
              always: bool = True) -> list[dict]:
    ""


    if _private(bot_id):
        return []
    wanted = {decision} if isinstance(decision, str) else set(decision)
    unknown = wanted - set(DECISIONS)
    if unknown:
        raise ValueError(f"not a decision: {sorted(unknown)}")
    rows = _notes(bot_id)
    known = _topics(rows)
    words = _plain(str(item or "")[:ITEM_MAX])


    held = collections.Counter(p for t in known.values() for p in {_plain(n) for n in t["names"]})
    broad = {p for p, n in held.items() if n > NAME_NOTES_MAX} | {_plain(x) for b in config.BOTS for x in (b.id, b.name)}

    def named(f: dict) -> bool:
        return any(p.strip() and p not in broad and p in words
                   for p in map(_plain, known.get(f["id"], {}).get("names", ())))

    def filed(f: dict) -> bool:
        return bool(known.get(f["id"], {}).get("decisions", set()) & wanted)

    def new(f: dict) -> bool:
        return f["id"] not in known and f["kind"] in rulereach.RULE_KINDS



    tiers = [named, new]
    if always:
        reach = rulereach.reach_of(rows)
        tiers.append(lambda f: reach.get(f["id"]) == rulereach.ALWAYS)
    tiers.append(filed)
    out, seen = [], set()
    for tier in tiers:
        for f in rows:
            if f["id"] not in seen and tier(f):
                seen.add(f["id"])
                out.append(f)
    return out


def _line(f: dict) -> str:
    day = datetime.fromtimestamp(float(f.get("created_at") or 0), home.zone())
    return f"- {day.strftime('%b')} {day.day}: {' '.join(str(f['text']).split())[:NOTE_MAX]}"


def _fit(rows: list[dict], decision) -> list[str]:
    out, used = [], 0
    for f in rows:
        line = _line(f)
        if used + len(line) + 1 > RULES_CHARS:
            log.warning("%d of %d rules did not fit a decision (%s)", len(rows) - len(out), len(rows), decision)
            break
        out.append(line)
        used += len(line) + 1
    return out


def lines(decision: str | tuple[str, ...], item: str = "", bot_id: str | None = None, always: bool = True) -> str:
    ""

    try:
        return "\n".join(_fit(rules_for(decision, item, bot_id, always), decision))
    except (sqlite3.Error, OSError, ValueError, TypeError):
        log.warning("the saved rules could not be read for a decision", exc_info=True)
        return ""


def texts(decision: str | tuple[str, ...], item: str = "", bot_id: str | None = None, always: bool = True) -> list[str]:
    ""
    try:
        return [" ".join(str(f["text"]).split())[:NOTE_MAX] for f in rules_for(decision, item, bot_id, always)]
    except (sqlite3.Error, OSError, ValueError, TypeError):
        log.warning("the saved rules could not be read for a decision", exc_info=True)
        return []


def answers(noticer: str = "", days: int = ANSWER_DAYS) -> list[str]:
    ""

    since = time.time() - days * 86400
    c = store.db()
    kind, args = ("AND noticer=?", (noticer,)) if noticer else ("", ())
    found: list[tuple[float, str]] = []
    for r in c.execute(f"SELECT card, picked, state, owner, updated, answered FROM notices WHERE updated>=? {kind} AND "
                       "(picked IS NOT NULL OR state='dismissed') ORDER BY updated DESC LIMIT ?",
                       (since, *args, ANSWERS_MAX)):
        if _private(r["owner"]):
            continue
        asked = str(json.loads(r["card"] or "{}").get("question") or "")
        found.append((float(r["answered"] or r["updated"]), f'"{asked}" Answer: {r["picked"] or "dismissed it"}.'))
    if not noticer:
        for r in c.execute("SELECT bot_id, body, created_at FROM feed WHERE kind='choices' AND created_at>=? "
                           "ORDER BY id DESC LIMIT 60", (since,)):
            try:
                card = json.loads(r["body"] or "{}")
            except ValueError:
                continue

            if _private(r["bot_id"]) or not isinstance(card, dict) or not card.get("picked") or card.get("notice"):
                continue
            found.append((float(r["created_at"]), f'"{card.get("question") or ""}" Answer: {card["picked"]}.'))
    from . import consent, privatewords

    newest = privatewords.keep(sorted(found, key=lambda x: -x[0]), lambda x: x[1])[:ANSWERS_MAX]
    return [consent.scrub(" ".join(text.split()))[:ANSWER_CHARS] for _, text in newest]


def numbered(decision: str | tuple[str, ...], item: str = "", bot_id: str | None = None, always: bool = True) -> list[str]:
    ""
    if _private(bot_id):
        return []
    said = [x for x in lines(decision, item, bot_id, always).split("\n") if x]
    try:
        return said + [f"- {who.first()} answered: {a}" for a in answers()]
    except (sqlite3.Error, OSError, ValueError, TypeError):
        log.warning("the person's recent answers could not be read for a decision", exc_info=True)
        return said


def block(decision: str | tuple[str, ...], item: str = "", bot_id: str | None = None, noticer: str = "",
          always: bool = True, answered: bool = True, rules: bool = True) -> str:
    ""



    if _private(bot_id):
        return ""
    first, parts = who.first(), []
    if rules and (said := lines(decision, item, bot_id, always)):
        parts.append(f"{HEAD.format(first=first)}\n{said}")
    try:
        recent = answers(noticer) if answered else []
    except (sqlite3.Error, OSError, ValueError, TypeError):
        log.warning("the person's recent answers could not be read for a decision", exc_info=True)
        recent = []
    if recent:
        parts.append(ANSWERS_HEAD.format(first=first) + "\n" + "\n".join(f"- {a}" for a in recent))
    return "".join(f"\n\n{p}" for p in parts)
