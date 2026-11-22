""

















from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime

from . import config, facts, home, shortreply, store

log = logging.getLogger("taffy.openloops")

ENABLED = False
MAX_OPEN = 25
STALE_DAYS = 45
TOPIC_MAX, SUMMARY_MAX, NEXT_MAX = 40, 420, 200
BLOCK_MAX_CHARS = 1600
BRIEFING_MAX_CHARS = 1400
HAIKU_DAILY = 150
TIMEOUT_SEC = 60
MIN_WORDS, MIN_REPLY_CHARS = 4, 300
CHANGES_PER_READ = 3



REMIND_EVERY_TURNS = 12




PROOF_ACTIONS = ("buy", "submit")

SCHEMA = """
CREATE TABLE IF NOT EXISTS open_loops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    summary TEXT NOT NULL,
    next_step TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT NOT NULL DEFAULT 'chat',
    opened_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    closed_at INTEGER
);
CREATE INDEX IF NOT EXISTS open_loops_bot ON open_loops (bot_id, status, updated_at);
CREATE TABLE IF NOT EXISTS open_loop_shown (
    session_id TEXT NOT NULL, loop_id INTEGER NOT NULL, updated_at INTEGER NOT NULL, at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, loop_id)
);
CREATE TABLE IF NOT EXISTS open_loop_sessions (
    session_id TEXT PRIMARY KEY, turns INTEGER NOT NULL DEFAULT 0, full_at INTEGER NOT NULL DEFAULT 0,
    at INTEGER NOT NULL DEFAULT 0, round_open INTEGER NOT NULL DEFAULT 1
);
"""



def _db() -> sqlite3.Connection:
    return store.db()


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)
    if "identity" not in {r[1] for r in c.execute("PRAGMA table_info(open_loops)")}:
        c.execute("ALTER TABLE open_loops ADD COLUMN identity TEXT NOT NULL DEFAULT '{}'")
    columns = {r[1] for r in c.execute("PRAGMA table_info(open_loops)")}


    for name, definition in {
        "last_checked_at": "INTEGER", "last_confirmed_at": "INTEGER", "content_at": "INTEGER",
        "check_note": "TEXT NOT NULL DEFAULT ''", "user_edited_at": "INTEGER",
        "progress_at": "INTEGER", "blocker": "TEXT NOT NULL DEFAULT '{}'",
    }.items():
        if name not in columns:
            c.execute(f"ALTER TABLE open_loops ADD COLUMN {name} {definition}")


def now() -> int:
    ""
    return int(time.time())


def _clean(text, limit: int) -> str:
    ""


    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    end = max(text.rfind(p, 0, limit + 1) for p in (". ", "! ", "? "))
    if end + 1 >= limit // 2:
        return text[:end + 1]
    room = text[:limit - 1]
    if text[limit - 1] != " " and " " in room:
        room = room.rsplit(" ", 1)[0]
    return room.rstrip(" ,;:") + "…"


def _key(topic: str) -> str:
    return " ".join(re.findall(r"[\w&'.-]+", (topic or "").casefold()))


REFS = ("task", "account", "thread", "message", "card", "calendar", "event", "wait")


def identity_of(value, topic: str = "") -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    value = value if isinstance(value, dict) else {}
    out = {k: _clean(value.get(k), 200) for k in ("subject", "person", "action", *REFS) if value.get(k)}
    out["subject"] = _key(out.get("subject") or topic)
    out["person"] = _key(out.get("person", ""))
    return out


def same_work(a: dict, b: dict) -> bool:
    return (a["subject"], a["person"]) == (b["subject"], b["person"]) and not any(
        a.get(k) and b.get(k) and a[k] != b[k] for k in REFS)




def save(bot_id: str, topic: str, summary: str, next_step: str = "", loop_id: int | None = None,
         source: str = "chat", generation: int | None = None, *, identity: dict | None = None,
         expected: int | None = None) -> int | None:
    ""

    topic, summary, next_step = _clean(topic, TOPIC_MAX), _clean(summary, SUMMARY_MAX), _clean(next_step, NEXT_MAX)
    if not topic or not summary or facts.secret(f"{topic} {summary} {next_step}"):
        return None
    next_step = sign_in_step(bot_id, topic, summary, next_step)
    c, t = _db(), now()
    ident = identity_of(identity, topic)
    if facts.secret(json.dumps(ident)):
        return None
    with store._lock:
        if generation is not None and generation != _generation:
            return None
        row = c.execute("SELECT * FROM open_loops WHERE id=? AND bot_id=?", (loop_id, bot_id)).fetchone() \
            if loop_id is not None else None
        if expected is not None and (not row or row["updated_at"] != expected or row["status"] != "open"):
            return None
        if row and not same_work(identity_of(row["identity"], row["topic"]), ident):

            legacy_match = row["identity"] == "{}" and _key(row["topic"]) in (_key(topic), ident["subject"])
            if not legacy_match:
                row = None
        if row is None:
            row = next((r for r in c.execute("SELECT * FROM open_loops WHERE bot_id=? AND status='open'", (bot_id,))
                        if same_work(identity_of(r["identity"], r["topic"]), ident)), None)
        if row is None or row["status"] != "open":
            from . import loopcheck
            incoming_due = loopcheck.due_at({"summary": summary, "next_step": next_step, "opened_at": t})

            if any(same_work(identity_of(r["identity"], r["topic"]), ident) and not (
                    incoming_due and loopcheck.due_at(dict(r)) and
                    datetime.fromtimestamp(incoming_due, loopcheck.TZ).date() >
                    datetime.fromtimestamp(r["user_edited_at"], loopcheck.TZ).date() and
                    incoming_due != loopcheck.due_at(dict(r)))
                    for r in c.execute("SELECT * FROM open_loops WHERE bot_id=? AND user_edited_at IS NOT NULL "
                                       "AND status IN ('closed','removed')", (bot_id,))):
                return None
        if row:
            before = identity_of(row["identity"], row["topic"])
            ident = {**before, **ident}
            if before.get("action") in PROOF_ACTIONS:
                ident["action"] = before["action"]
            c.execute("UPDATE open_loops SET topic=?, summary=?, next_step=?, status='open', closed_at=NULL, "
                      "updated_at=MAX(?, updated_at + 1), source=?, identity=? WHERE id=?",
                      (topic, summary, next_step, t, source, json.dumps(ident), row["id"]))
            if (row["summary"], row["next_step"]) != (summary, next_step):


                c.execute("UPDATE open_loops SET content_at=MAX(?,COALESCE(content_at,0)+1),last_confirmed_at=NULL,"
                          "check_note='',progress_at=?,blocker=CASE WHEN json_extract(blocker,'$.on')='parked' "
                          "THEN '{}' ELSE blocker END WHERE id=?", (t, t, row["id"]))
                from . import loopcheck
                loopcheck.record_move(row["id"], bot_id, t, row["updated_at"])
            new_id = row["id"]
        else:
            new_id = c.execute("INSERT INTO open_loops (bot_id, topic, summary, next_step, source, opened_at, "
                               "updated_at, identity) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (bot_id, topic, summary, next_step, source, t, t, json.dumps(ident))).lastrowid
            c.execute("UPDATE open_loops SET content_at=? WHERE id=?", (t, new_id))
        _trim(c, bot_id, t)
        c.commit()
    return new_id


def _trim(c: sqlite3.Connection, bot_id: str, t: int) -> None:
    ""

    ids = [r["id"] for r in c.execute("SELECT id FROM open_loops WHERE bot_id=? AND status='open' "
                                      "ORDER BY MAX(COALESCE(content_at,opened_at),COALESCE(user_edited_at,0)) DESC, id DESC", (bot_id,))]
    for old in ids[MAX_OPEN:]:
        c.execute("UPDATE open_loops SET status='archived', closed_at=? WHERE id=?", (t, old))


def close(bot_id: str, loop_id: int, expected: int | None = None, note: str = "") -> bool:
    ""
    c = _db()
    with store._lock:
        done = c.execute("UPDATE open_loops SET status='closed', closed_at=?, updated_at=MAX(?,updated_at+1), "
                         "check_note=CASE WHEN ?='' THEN check_note ELSE ? END "
                         "WHERE id=? AND bot_id=? AND status='open'" + (" AND updated_at=?" if expected is not None else ""),
                         (now(), now(), note, note, loop_id, bot_id,
                          *((expected,) if expected is not None else ()))).rowcount
        c.commit()
    return bool(done)







_PROOF_WORDS = {"buy": "order|confirmation|confirmed|receipt|transaction|invoice|payment|reference|ref",
                "submit": "confirmation|confirmed|receipt|return|reference|ref|case|request|submission|application|"
                          "filing|ticket|transaction"}
_NUMBER = r"[^.!?\n]{0,40}?(?<![\w-])#?((?=[a-z0-9-]*\d[a-z0-9-]*\d[a-z0-9-]*\d)[a-z0-9][a-z0-9-]{3,}[a-z0-9])(?![\w-])"
_NOT_A_NUMBER = re.compile(r"^(?:1-)?\d{3}-\d{3}-\d{4}$|^\d{1,4}-\d{1,2}-\d{1,4}$")


_DONE = re.compile(
    r"\b(?:i|we)\b(?:\s+(?!(?:not|never|still|need|needs|want|wants|have|has|should|will|can|could|would|to|"
    r"haven'?t|hasn'?t|didn'?t|don'?t|won'?t|can'?t|cannot)\b)[\w']+){0,3}?\s+(?:already\s+)?(?:bought|ordered|"
    r"paid|purchased|placed|submitted|filed|returned|finished|completed|handled|took care of|did (?:it|that|them)|"
    r"cancell?ed)\b(?!\s+not\b)|"
    r"\b(?:it'?s|that'?s|all|already|is|are)\s+(?:done|taken care of|handled|submitted|filed|paid|ordered|bought|"
    r"cancell?ed)\b|"
    r"\b(?:cancel (?:it|that|the order)|never ?mind|forget (?:it|about it|that)|drop (?:it|that)|skip (?:it|that)|"
    r"no longer need|don'?t need (?:it|them|that) anymore|not (?:doing|buying|ordering|getting) (?:it|that|them))\b",
    re.I)
_NOT_YET = re.compile(r"\?|\b(?:for now|right now|later|not now|not yet|until|tomorrow|next week|another time|"
                      r"hold off|wait|pause)\b", re.I)


def confirmations(text: str, action: str = "buy") -> list[str]:
    ""

    found = re.findall(rf"\b(?:{_PROOF_WORDS[action]})\b{_NUMBER}", text or "", re.I)
    return [n for n in found if not _NOT_A_NUMBER.match(n)]


def said_done(words: str) -> bool:
    ""
    w = " ".join(str(words or "").split())
    return bool(w) and len(w) <= 600 and bool(_DONE.search(w)) and not _NOT_YET.search(w)


def proof_action(loop: dict) -> str:
    ""
    action = identity_of(loop.get("identity"), loop.get("topic") or "").get("action")
    return action if action in PROOF_ACTIONS else ""


def proof(bot_id: str, loop: dict, claimed="", said: str = "", seen: str = "") -> str | None:
    ""


    day, action = _day(now()), proof_action(loop) or "buy"
    if said_done(said):
        quote = _clean(said, 160)
        return f"Closed {day}: " + ("You said it was done." if facts.secret(quote) else f"You said \"{quote}\"")
    claimed = str(claimed or "").casefold()
    number = next((n for n in confirmations(seen, action) if n.casefold() in claimed), None)
    if number:
        return f"Closed {day}: confirmation " + (f"ending {number[-4:]}." if facts.secret(number) else f"{number}.")
    from . import purchases
    words = _tokens(f"{loop['topic']} {identity_of(loop.get('identity'), loop['topic'])['subject']}")
    for r in _db().execute("SELECT what, origin FROM purchases WHERE bot=? AND state='confirmed' AND updated_at>=?",
                           (bot_id, loop["opened_at"])) if action == "buy" else ():
        site = r["origin"].split("//")[-1]
        if words & _tokens(f"{r['what']} {site}"):
            return f"Closed {day}: the receipt for {r['what'] or 'the order'} on {site}."
    return None


def open_loops(bot_id: str) -> list[dict]:
    ""
    from . import loopevents
    loopevents.sync_waits(bot_id)
    cutoff = now() - STALE_DAYS * 86400
    return [dict(r) for r in _db().execute(
        "SELECT * FROM open_loops WHERE bot_id=? AND status='open' AND updated_at>=? ORDER BY updated_at DESC, id DESC",
        (bot_id, cutoff))]




_STOP = {"the", "and", "for", "with", "about", "from", "that", "this", "what", "when", "your", "have", "has", "was",
         "are", "its", "out", "any", "new", "all", "not", "but", "you", "got", "get", "can", "will"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if (len(w) >= 3 or w.isdigit()) and w not in _STOP}


def about(loop: dict, message: str) -> bool:
    ""
    words = _tokens(message)
    mine = _tokens(loop["topic"])
    return bool(mine) and mine <= words


def names_work(loop: dict, exchange: str) -> bool:
    ""





    ident = identity_of(loop.get("identity"), loop["topic"])
    return bool(_tokens(f"{loop['topic']} {ident['subject']} {ident['person']}") & _tokens(exchange))


_LONG_DAY = re.compile(r"\b(jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I)


def adds_to(loop: dict, item: dict, seen: str) -> bool:
    ""




    def words(text: str) -> set[str]:
        return _tokens(_LONG_DAY.sub(lambda m: m.group(1), text))
    heard = words(seen)
    added = (words(f"{item.get('summary') or ''} {item.get('next') or ''}") -
             words(f"{loop['summary']} {loop['next_step']}")) & heard
    ident = identity_of(loop["identity"], loop["topic"])
    names = (words(f"{loop['topic']} {ident['subject']} {ident['person']}") - words(who_first())) & heard
    return len(added) >= 2 or bool(added and names)


def closed_topic(loop: dict, terms: set[str]) -> str:
    ""

    return facts.closes(f"{loop['topic']}. {identity_of(loop.get('identity'), loop['topic'])['subject']}", terms) \
        if terms else ""


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, home.TZ).strftime("%b %-d")


def _line(loop: dict, ask: bool = True, warn: bool = True) -> str:
    ""




    from . import loopcheck
    text = f"- {loop['topic']}: {loop['summary']}"
    if loop["next_step"]:
        text += f" Next: {loop['next_step']}"
    if loop.get("check_note"):
        text += f" {loop['check_note']}"
    b = loopcheck.blocker(loop)
    if b.get("on") == "person" and ask:
        text += f" Waiting on {who_first()}, who has this question: {b['ask']}"
    elif b.get("on") == "other":
        text += f" Waiting on {b['who']} until {b['until']}."
    elif b.get("on") == "parked":
        text += f" {who_first()} parked this. Do not ask about it until it moves."
    if warn and loopcheck.unconfirmed(loop):
        text += " [Unconfirmed: do not treat this as current or repeat its deadline as fact. Ask before relying on it.]"
    return f"{text} (updated {_day(loop['updated_at'])})"


def forget_shown_before(session_id: str, since: float) -> None:
    ""
    if not session_id or not since:
        return
    with store._lock:
        c = _db()
        c.execute("DELETE FROM open_loop_shown WHERE session_id=? AND at < ?", (session_id, since))
        c.commit()


def block(bot_id: str, session_id: str = "", message: str = "") -> str:
    ""



    terms = facts.closed_terms(bot_id)
    loops = [lp for lp in open_loops(bot_id) if not closed_topic(lp, terms) or about(lp, message)]
    c = _db()
    shown, gone, gone_ids, due, turns, round_open = {}, [], [], False, 0, 0
    if session_id:
        row = c.execute("SELECT turns, full_at, round_open FROM open_loop_sessions WHERE session_id=?",
                        (session_id,)).fetchone()
        turns, full_at, round_open = (row["turns"], row["full_at"], row["round_open"]) if row else (0, 0, 1)
        turns += 1
        heard = {r["loop_id"]: r["updated_at"] for r in c.execute(
            "SELECT loop_id, updated_at FROM open_loop_shown WHERE session_id=?", (session_id,))}
        live = {lp["id"] for lp in loops}
        if heard:
            rows = c.execute(f"SELECT id, topic, status FROM open_loops WHERE id IN ({','.join('?' * len(heard))})",
                             list(heard))


            closed = [(r["id"], r["topic"], r["status"]) for r in rows if r["id"] not in live]
            gone = [t for _, t, status in closed if status == "closed"]
            gone_ids = [i for i, _, _ in closed]


        pending = any(heard.get(lp["id"], -1) < lp["updated_at"] for lp in loops)
        due = turns - full_at >= REMIND_EVERY_TURNS and not pending
        with store._lock:
            round_open = 1 if due else round_open
            c.execute("INSERT OR REPLACE INTO open_loop_sessions (session_id, turns, full_at, at, round_open) "
                      "VALUES (?, ?, ?, ?, ?)", (session_id, turns, full_at, now(), round_open))
            if due:
                c.execute("DELETE FROM open_loop_shown WHERE session_id=?", (session_id,))
            c.commit()
        shown = {} if due else heard
    fresh = [lp for lp in loops if shown.get(lp["id"], -1) < lp["updated_at"]]
    asked = [lp for lp in loops if lp not in fresh and about(lp, message)]
    chosen, used = [], 0

    for lp in sorted(fresh + asked, key=lambda lp: not about(lp, message)):
        line = _line(lp)
        if chosen and used + len(line) > BLOCK_MAX_CHARS:
            break
        chosen.append(lp)
        used += len(line)
    if not chosen and not gone:
        return ""
    first = who_first()
    head = (f"[Open loops with {first}: things still in flight, kept outside the chat so a long conversation never "
            "loses them. Use them as your own notes; they may be a little behind what he just said.]"
            if (not shown and chosen) or asked else f"[Open loops with {first} that changed since your last turn.]")
    left = len(fresh) + len(asked) - len(chosen)
    lines = [_line(lp) for lp in chosen]
    lines += [f"- {t}: no longer open. Ignore any earlier note that it is." for t in gone]
    lines += [f"- {left} more open loops are not shown here."] if left > 0 else []
    if session_id:
        with store._lock:
            c.executemany("INSERT OR REPLACE INTO open_loop_shown (session_id, loop_id, updated_at, at) "
                          "VALUES (?, ?, ?, ?)", [(session_id, lp["id"], lp["updated_at"], now()) for lp in chosen])


            sent = {lp["id"]: lp["updated_at"] for lp in chosen}
            if round_open and all(max(shown.get(lp["id"], -1), sent.get(lp["id"], -1)) >= lp["updated_at"]
                                  for lp in loops):
                c.execute("UPDATE open_loop_sessions SET full_at=?, round_open=0 WHERE session_id=?", (turns, session_id))
            if gone_ids:
                c.execute(f"DELETE FROM open_loop_shown WHERE session_id=? AND loop_id IN "
                          f"({','.join('?' * len(gone_ids))})", [session_id] + gone_ids)
            cutoff = now() - 14 * 86400
            c.execute("DELETE FROM open_loop_shown WHERE at < ?", (cutoff,))
            c.execute("DELETE FROM open_loop_sessions WHERE at < ?", (cutoff,))
            c.commit()
    return "\n".join([head] + lines)


def who_first() -> str:
    from . import who
    return who.first()


def briefing_text(bot_id: str) -> str:
    ""
    loops = open_loops(bot_id)
    if not loops:
        return ""
    lines, used = [], 0
    for lp in loops:
        line = _line(lp)
        if lines and used + len(line) > BRIEFING_MAX_CHARS:
            break
        lines.append(line)
        used += len(line)
    return "Still open with you (things in flight; do not read this list out):\n" + "\n".join(lines)




PROMPT = """You keep the open loops for {zip}, the assistant of {person}. An open loop is something still in flight \
that must not be lost when a long chat is shortened: waiting on a reply from someone, a decision {person} has not \
made, work started and not finished, a deadline coming, something promised. Group by topic: a person, a class, a \
project, an account, an order ("Ken", "BIO 110", "Loans", "Desk Prints").

The summary keeps the specifics a shortened chat would lose: names, numbers, dates, amounts, what was decided, what \
was sent to whom and when. One or two plain sentences. "next" is the next step and who owns it, or empty.

Rules:
- Only things still unfinished. A question that was answered, small talk, and work that is done are not loops.
- A practice run, a test or made-up data is not work. It opens no loop.
- One loop is one specific piece of unfinished work, never a broad bucket such as "Business pitch". Keep \
a store pitch, a domain account, and another person's memo separate. A person's food preferences are facts, \
never part of a work shift or any pending-work loop. Return kind "preference" or "fact" for durable information.
- A loop waits on at most one thing from {person}. When {person} owes answers about separate work (another person's \
site, a different order or donor), each is its own loop, and its "next" names that one thing.
- Every pending loop has identity.subject (the specific work) and identity.person (the person or organization). \
Copy these unchanged when updating it. Copy task, account, thread, message, card, calendar, event, or wait ids \
from the supplied identities only. Never invent ids. Set identity.action to task, send, book, wait, work, buy (a \
purchase, order or payment) or submit (a form, return or application filed for {person}), for exactly what remains \
unfinished. A completed verification task does not finish assignments it says are due.
- A purchase, order, return or form {person} asked for is a loop from the first step until it is finished, with who \
has the next step.
- When {person} says they need more of something that runs out (supplies, refills, contact lenses, medication), \
that is a buy loop even when nobody was asked to order it, and saying where or how to buy it does not finish it. Its \
"next" is "{person}: reorder <the thing> by <a date a few days after today>", unless this exchange placed the order.
- A form, return, booking or renewal is {zip}'s to carry to its last step, so its "next" starts with {zip}: fill in \
what can be found, then hand {person} only the final press (Submit, Pay, Book) or answers only {person} can give \
("{zip}: fill in the questionnaire and stop at review. {person}: answer the sworn questions and press Submit."). \
"next" starts with {person} only when {person} said so; then the summary says {person} will do it themselves, \
with the day.
- When a submit, buy or booking finishes, close it, and when it starts something dated (an answer to wait for, a \
step due if a request is turned down, a visit), add a new loop whose "next" is {zip}'s dated check ("{zip}: check \
the court portal on November 16 for the answer; if {person} is kept, the orientation video is due November 25").
- {zip} owns every step {zip} can take: filling in a form, looking something up, drafting, signing in with a saved \
login. "next" gives {person} a step only when only {person} can take it (paying, a sworn answer or signature, a code \
sent to {person}, a choice) or when {person}'s own words above say "I'll do it myself". Never write that {person} \
will do something themselves, or that only {person} can, from {zip}'s answer.
- When a current loop below is about the same work AND person, give its id and write its whole new summary. When this \
exchange finishes or drops a loop, give its id with state "closed". A buy or submit loop closes only when {person} \
says it is done or called off, or with "proof": the order, confirmation or return number exactly as it appears \
in this exchange. A pause ("skip it for now"), a stop at the pay or submit step, or your own report of progress \
keeps it open.
- Never include passwords, codes, account or card numbers, or sign-in details in a summary or next step.
- Today is {today}. Write real dates like "October 23", never "tomorrow" or "Friday" alone.
- When the summary reports what {person} said, picked or decided, or a question {zip} put to {person}, write \
the day it happened ("On October 20 {person} said 'Not now'"). Keep those days when you rewrite a summary.
- "topic" is one to three words. At most {changes} changes. Most exchanges have none.

Current open loops:
{current}

{person} said: {words}

{zip} answered: {reply}

Answer with JSON only, no other text:
{{"loops": [{{"id": null, "topic": "Ken budget", "summary": "...", "next": "...", "state": "open", \
"kind": "pending", "identity": {{"subject": "venue budget", "person": "ken", "action": "work"}}}}]}}
Use {{"loops": []}} when nothing changed."""


def worth_reading(words: str, reply: str, bot_id: str = "") -> bool:
    ""

    w = " ".join((words or "").split())
    if w.startswith("[") and w.endswith("]"):
        return False
    if len(w.split()) >= MIN_WORDS or len((reply or "").strip()) >= MIN_REPLY_CHARS:
        return True
    if not bot_id or len(w.split()) < 2:
        return False
    said = _tokens(f"{w} {reply}")
    return any(_tokens(lp["topic"]) & said for lp in open_loops(bot_id))


def _budget_ok() -> bool:
    day = datetime.now(home.TZ).strftime("%Y-%m-%d")
    used = store.get_setting("openloops_haiku", None) or {}
    if not isinstance(used, dict) or used.get("day") != day:
        used = {"day": day, "n": 0}
    if used["n"] >= HAIKU_DAILY:
        return False
    try:
        from . import health
        if not health.status.get("claude", True):
            return False
    except Exception:
        pass
    used["n"] += 1
    store.set_setting("openloops_haiku", used)
    return True


async def ask_haiku(prompt: str) -> str:
    ""
    from . import briefing
    return await briefing.ask_haiku(prompt, timeout=TIMEOUT_SEC, effort="low")


def _json(text: str) -> dict | None:
    t = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        return None
    try:
        out = json.loads(t[i:j + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def apply(out: dict | None, bot_id: str, source: str, generation: int | None = None, *,
          snapshot: dict | None = None, limit: int = CHANGES_PER_READ, event: bool = False,
          known: dict | None = None, said: str = "", seen: str = "", exchange: str = "", must_add: bool = True) -> list[int]:
    ""


    changed, terms = [], None
    items = out.get("loops") if isinstance(out, dict) else None
    mine = snapshot if snapshot is not None else {lp["id"]: lp for lp in open_loops(bot_id)}
    for item in (items if isinstance(items, list) else [])[:limit]:
        if not isinstance(item, dict):
            continue
        raw = item.get("id")
        loop_id = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        if loop_id is not None and loop_id not in mine:
            loop_id = None
        if event and loop_id is None:
            continue
        old = mine.get(loop_id)
        expected = old["updated_at"] if old and snapshot is not None else None
        identity = item.get("identity")
        if identity is None and old and _key(item.get("topic") or old["topic"]) == _key(old["topic"]):
            identity = identity_of(old["identity"], old["topic"])
        elif identity is None:
            identity = identity_of(None, str(item.get("topic") or ""))
        if known is not None:
            identity = identity_of(identity, str(item.get("topic") or ""))
            if any(identity.get(k) and identity[k] not in known[k] for k in REFS):
                continue
        if item.get("kind") in ("preference", "fact"):
            with store._lock:
                if generation not in (None, _generation):
                    continue
                try:
                    ident = identity_of(identity, str(item.get("topic") or ""))
                    summary = _clean(item.get("summary"), 400)
                    if facts.hands_off(summary) and not facts.said_so(summary, said):
                        log.info("loop fact not saved: it leaves work to the person without their words")
                        continue


                    done = facts.finished(summary)
                    facts.save(bot_id, summary, subject=f"{ident['person']} {ident['subject']}",
                               kind="preference" if item["kind"] == "preference" else "status" if done else "other",
                               source="chat", evidence={"quote": facts.finished_quote(said)} if done else None)
                except facts.FactError:
                    pass
            continue
        if event and old and identity is not None and not same_work(
                identity_of(old["identity"], old["topic"]), identity_of(identity, old["topic"])):
            continue
        if str(item.get("state") or "open").lower() == "closed":
            if old and (identity is None or same_work(identity_of(old["identity"], old["topic"]),
                                                      identity_of(identity, str(item.get("topic") or "")))):
                if exchange and not names_work(old, exchange):
                    log.info("loop %s stays open: the tapped card says nothing about %s", loop_id, old["topic"])
                    continue
                note = proof(bot_id, old, item.get("proof"), said, seen) if proof_action(old) else ""
                if note is None:
                    log.info("loop %s stays open: nothing proves the %s finished", loop_id, old["topic"])
                    continue
                with store._lock:
                    if generation in (None, _generation) and close(bot_id, loop_id, expected, note):
                        changed.append(loop_id)
            continue
        if loop_id is None and terms is None:
            terms = facts.closed_terms(bot_id)
        if loop_id is None and terms:

            new = {"topic": str(item.get("topic") or ""), "identity": identity}
            topic = closed_topic(new, terms)
            if topic and not facts.closes(said, terms):
                log.info("no loop opened for %s: %s is closed", new["topic"], topic)
                continue
        if old and must_add and seen and not adds_to(old, item, seen):
            log.info("loop %s keeps its words: what was read adds nothing to it", loop_id)
            continue


        summary = facts.without_hand_offs(str(item.get("summary") or ""), said)
        saved = save(bot_id, str(item.get("topic") or ""), summary,
                     facts.without_hand_offs(str(item.get("next") or ""), said), loop_id, source, generation,
                     identity=identity, expected=expected)
        if saved:
            changed.append(saved)
    return changed


def _current(bot_id: str, loops: list[dict] | None = None) -> str:
    return "\n".join(f"[{lp['id']}] {lp['topic']}: {lp['summary']}" + (f" Next: {lp['next_step']}" if lp["next_step"]
                                                                       else "") + f" Identity: {lp['identity']}"
                     for lp in (open_loops(bot_id) if loops is None else loops)) or "None."


_tails: dict[str, asyncio.Task] = {}


async def read(bot_id: str, words: str, reply: str, source: str = "chat", generation: int | None = None,
               asked: str = "") -> list[int]:
    ""


    before = _tails.get(bot_id)
    _tails[bot_id] = me = asyncio.current_task()
    try:
        if before is not None and not before.done():
            await asyncio.wait({before})
        return await _read(bot_id, words, reply, source, _generation if generation is None else generation, asked)
    finally:
        if _tails.get(bot_id) is me:
            _tails.pop(bot_id, None)


async def _read(bot_id: str, words: str, reply: str, source: str, generation: int, asked: str = "") -> list[int]:
    from . import who
    if not _budget_ok():
        log.info("open loops skipped: today's budget is used")
        return []
    from . import loopevents, privatewords
    snapshot = {lp["id"]: lp for lp in open_loops(bot_id)}
    material = loopevents.material(bot_id)
    zip_name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "the assistant"



    shown_words, shown_reply = privatewords.leave_out((words or "")[-2500:]), privatewords.leave_out((reply or "")[:1500])
    asked = privatewords.leave_out(asked)
    if (words or reply) and not (shown_words.strip() or shown_reply.strip()):
        return []

    card = (f'\n\nA card asked {who.first()}: "{asked[:1000]}" These are not {who.first()}\'s words and say nothing '
            f"about what {who.first()} did. Only the next line is {who.first()}'s answer.") if asked else ""
    current = privatewords.keep(snapshot.values(), lambda lp: _current(bot_id, [lp]))
    prompt = PROMPT.format(zip=zip_name, person=who.first(), today=datetime.now(home.TZ).strftime("%A, %B %-d, %Y"),
                           changes=CHANGES_PER_READ, current=_current(bot_id, current) + card,
                           words=shown_words or "(nothing of their own)", reply=shown_reply or "None.")
    prompt += ("\n\nKnown identities and authoritative events, newer than chat claims:\n"
               + json.dumps(loopevents.for_model(material)))
    from . import standing
    closed = privatewords.leave_out(standing.closed_text(bot_id))
    if closed:
        prompt += f"\n\n{closed}\nNever open a loop about one of these unless {who.first()}'s own words above do."
    began = time.monotonic()
    try:
        with shortreply.reading((bot_id,)):
            out = _json(await ask_haiku(prompt))
    except Exception:
        log.warning("open loops read failed", exc_info=True)
        return []
    with store._lock:
        changed = apply(out, bot_id, source, generation, snapshot=snapshot,
                        known=loopevents.known_refs(material, list(snapshot.values())),
                        said=words or "", seen=f"{(words or '')[-2500:]}\n{(reply or '')[:1500]}",
                        exchange=f"{asked}\n{(words or '')[-2500:]}\n{(reply or '')[:1500]}" if asked else "")
    log.info("open loops for %s: %d changed in %.1f s", bot_id, len(changed), time.monotonic() - began)
    return changed


_running: set[asyncio.Task] = set()


_generation = 0


def cancel_reads() -> None:
    ""


    global _generation
    _generation += 1
    for task in list(_running):
        task.get_loop().call_soon_threadsafe(task.cancel)


def after_turn(bot_id: str, words: str, reply: str = "", source: str = "chat",
               generation: int | None = None, asked: str = "") -> asyncio.Task | None:
    ""



    try:
        from . import history

        if not ENABLED or history.spoken_for(words) or not worth_reading(f"{asked} {words}".strip(), reply, bot_id):
            return None
        task = asyncio.get_running_loop().create_task(read(bot_id, words, reply, source, generation, asked))
    except Exception:
        log.warning("could not start the open loops read", exc_info=True)
        return None
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task




_PAYING = re.compile(r"\b(?:pay|buy|order|purchase|check ?out|cart|credits?|subscribe)\b", re.I)


def parked(bot_id: str, card: dict) -> int | None:
    ""



    try:
        return _parked(bot_id, card)
    except Exception:
        log.warning("could not keep the parked job as a loop", exc_info=True)
        return None


def _parked(bot_id: str, card: dict) -> int | None:
    job = _clean(card.get("job"), TOPIC_MAX)
    words = _tokens(job)
    if not words or card.get("purpose") == "stuck":
        return None
    row = next((lp for lp in open_loops(bot_id) if _tokens(lp["topic"]) and (
        _tokens(lp["topic"]) <= words or words <= _tokens(lp["topic"]))), None)
    from . import who
    first, step = who_first(), _clean(card.get("why"), NEXT_MAX - 60)
    name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "I"
    next_step = {"open": f"{first}: {step}.",
                 "done": f"{name}: look at the page and carry on; {first} did this: {step}.",
                 "declined": f"{name}: finish what I can and tell {first} what is left; {first} closed this: {step}."
                 }.get(card.get("state"))
    if next_step is None or (row is None and card.get("state") != "open"):
        return None
    ident = identity_of(row["identity"], row["topic"]) if row else {"subject": job, "person": card.get("site") or ""}
    if ident.get("action") not in PROOF_ACTIONS:
        ident["action"] = "work" if card.get("purpose") not in ("final", "file") else \
            "buy" if _PAYING.search(f"{job} {step}") else "submit"
    summary = row["summary"] if row else (f"I stopped at {who.possessive()} step" +
                                          (f" on {card['site']}" if card.get("site") else "") + f": {step}.")
    return save(bot_id, row["topic"] if row else job, summary, next_step, row["id"] if row else None, "park",
                identity=ident)


def _step_of(first: str, next_step: str) -> str:
    ""
    step = re.sub(rf"^{re.escape(first)}\s*(?::|\bto\b)?\s*", "", _persons_part(next_step), flags=re.I)
    return re.split(r"(?<=[.!?])\s", step)[0].rstrip(".")


def _persons_part(next_step: str) -> str:
    ""

    first, step = who_first(), next_step.strip()
    if _key(step).startswith(_key(first)):
        return step
    m = re.search(rf"(?<=[.!?])\s+{re.escape(first)}\s*:", step, re.I) if first else None
    return step[m.start():].strip() if m else ""


def persons_step(loop: dict) -> bool:
    ""


    return bool(_persons_part(loop["next_step"]))




_SIGN_IN = re.compile(r"(?:sign|log)\s*(?:back\s+)?in\s*to\s+(?P<site>.+?)(?:\s+on\s+(?:the|h(?:is|er)|their|your)\s+"
                      r"Mac)?(?:\s+if this is still wanted)?(?:,?\s+(?:and|then)\s+(?P<rest>.+))?", re.I)
_ADD_LOGIN = re.compile(r"(?:add|save)\s+(?:a|the)\s+(?:\w+\s+)?login(?:\s+in the app)?(?:\s+if this is still wanted)?",
                        re.I)


def saved_login(bot_id: str, text: str, after: float = 0) -> str:
    ""

    from . import logins
    try:
        sites = logins.shared_sites(bot_id)
    except Exception:
        log.warning("could not read %s's saved logins", bot_id, exc_info=True)
        return ""
    for site, at in sorted(sites.items()):
        m = re.search(rf"\b{re.escape(site.split('.')[0])}\b", text or "", re.I)
        if m and not (after and at <= after):
            return m.group(0)
    return ""


def sign_in_step(bot_id: str, topic: str, summary: str, next_step: str) -> str:
    ""




    first = who_first()
    if not _key(next_step).startswith(_key(first)):
        return next_step
    body = re.sub(rf"^{re.escape(first)}\s*(?::|\bto\b)?\s*", "", next_step.strip(), flags=re.I)
    head, *tail = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)
    head = head.rstrip(".")
    if m := _SIGN_IN.fullmatch(head):
        shown, rest = saved_login(bot_id, m["site"]), m["rest"]
    elif _ADD_LOGIN.fullmatch(head):
        shown, rest = saved_login(bot_id, f"{topic} {summary}"), None
    else:
        return next_step
    if not shown:
        return next_step
    name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "I"
    step = f"{name}: sign in to {shown} with the saved login and carry on."
    if rest:
        step += f" {first}: {rest.rstrip('.')}."
    return _clean(" ".join([step, *tail]), NEXT_MAX)


def waiting_on_person() -> list[dict]:
    ""



    from . import loopcheck, signin
    first, jobs = who_first(), []
    today = datetime.fromtimestamp(now(), home.TZ).date()
    parked_kinds = {p.value for p in signin.PARKED} - {signin.Purpose.stuck.value}
    for r in _db().execute("SELECT bot_id, body, created_at FROM feed WHERE kind=? ORDER BY id", (signin.KIND,)):
        try:
            card = json.loads(r["body"] or "{}")
        except ValueError:
            continue
        bot = config.BOTS_BY_ID.get(r["bot_id"])
        if bot is None or bot.sandbox or not isinstance(card, dict) or card.get("state") != "open" \
                or card.get("purpose") not in parked_kinds:
            continue
        jobs.append({"topic": card.get("job") or card.get("site") or "A step", "step": card.get("why") or "",
                     "at": r["created_at"], "due": None})
    for r in _db().execute("SELECT * FROM open_loops WHERE status='open' AND updated_at>=? ORDER BY opened_at, id",
                           (now() - STALE_DAYS * 86400,)):
        lp, bot = dict(r), config.BOTS_BY_ID.get(r["bot_id"])
        if bot is None or bot.sandbox or not proof_action(lp):
            continue
        mine = _tokens(lp["topic"])
        due = min((d.timestamp() for d, _ in loopcheck._dates(lp) if d.date() >= today), default=None)
        card = next((j for j in jobs if (t := _tokens(j["topic"])) and (t <= mine or mine <= t)), None)
        if card is not None:
            card["due"] = due
        elif persons_step(lp):
            jobs.append({"topic": lp["topic"], "step": _step_of(first, lp["next_step"]), "at": lp["opened_at"],
                         "due": due})
    jobs.sort(key=lambda j: (j["due"] is None, j["due"] or 0, j["at"]))
    return [{"topic": j["topic"], "step": j["step"]} for j in jobs]


def waiting_text() -> str:
    ""

    jobs = waiting_on_person()
    if not jobs:
        return ""
    return "\n".join([f"[Sign-ins, purchases and filings waiting on {who_first()}, soonest due first. Being on this "
                      "list is no reason to lead with one. Put them under <later>, in this order.]"]
                     + [f"- {j['topic']}: {j['step']}" for j in jobs])




ASK_SEC = 24 * 3600


def _topic_from(why: str) -> str:
    ""
    head = re.split(r"\s+(?:and|so|because|to see)\s+|[,;:]|\.(?:\s|$)", why, maxsplit=1)[0].strip()
    head = head if len(head) <= TOPIC_MAX else head[:TOPIC_MAX].rsplit(" ", 1)[0]
    return head[:1].upper() + head[1:]


def expire_asks(t: float | None = None) -> int:
    ""




    from . import signin, wallet
    t = time.time() if t is None else t
    first, closed = who_first(), 0
    asked = {p.value for p in signin.ASKED}
    for row in _db().execute("SELECT * FROM feed WHERE kind IN (?, ?) AND task_id IS NULL ORDER BY id",
                             (wallet.REQUEST_KIND, signin.KIND)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        bot = config.BOTS_BY_ID.get(row["bot_id"])
        if bot is None or not isinstance(card, dict) or card.get("state") != "open" or (
                row["kind"] == signin.KIND and card.get("purpose", "signin") not in asked):
            continue
        if card.get("from_check"):
            continue
        why = _clean(card.get("why"), 200)
        words = _tokens(why)
        loops = [dict(r) for r in _db().execute(
            "SELECT * FROM open_loops WHERE bot_id=? AND status IN ('open','closed') "
            "ORDER BY status='open' DESC, updated_at DESC", (row["bot_id"],))
            if _tokens(r["topic"]) and _tokens(r["topic"]) <= words]
        mine = loops[0] if loops else None
        with_loop = bool(mine and mine["status"] == "closed" and (mine["closed_at"] or 0) >= row["created_at"])



        saved = saved_login(row["bot_id"], why, float(row["created_at"] or 0)) if (
            row["kind"] == wallet.REQUEST_KIND and card.get("item_kind") == "login") else ""
        if not with_loop and not saved and t - float(row["created_at"] or 0) < ASK_SEC:
            continue
        card.update(state="saved" if saved else "expired" if row["kind"] == wallet.REQUEST_KIND else "retired", at=t)
        store.update_feed(row["id"], row["title"], json.dumps(card))
        closed += 1
        log.info("%s's card %s closed: %s", row["bot_id"], row["id"],
                 "its loop closed" if with_loop else "a login was saved" if saved else "unanswered")
        if saved:
            store.add_card_note(row["bot_id"], f"A login for {saved} was saved after you asked for one, and you may "
                                "use it, so your card to add a login closed. Sign in with browser_vault_fill and carry "
                                f"on with what it was for: {why}.")
        if with_loop or saved:
            continue
        if row["kind"] == wallet.REQUEST_KIND and card.get("item_kind") == "card":
            step, action = f"{bot.name}: take it to the pay step and hand {first} that one press.", "buy"
        elif row["kind"] == wallet.REQUEST_KIND:
            step, action = (f"{first}: add {wallet.ASK_NAMES.get(card.get('item_kind'), 'it')} in the app if this "
                            "is still wanted."), "work"
        else:
            step, action = f"{first}: sign in to {card.get('site') or 'the site'} if this is still wanted.", "work"
        if mine and mine["status"] == "open":
            save(row["bot_id"], mine["topic"], mine["summary"], step, mine["id"], "card",
                 identity=identity_of(mine["identity"], mine["topic"]))
        elif why:
            topic = _topic_from(why)
            save(row["bot_id"], topic, f"{row['title']} ({_day(int(row['created_at']))}): {why}. The card ran out "
                 "unanswered.", step, None, "card", identity={"subject": topic, "person": card.get("site") or "",
                                                             "action": action})
    return closed
