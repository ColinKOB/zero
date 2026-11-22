""
































from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from datetime import time as clock

from . import (config, extract, factdates, factkeeper, facts, home, lineage, openloops, privatewords, recentchat, shortreply,
               store)

log = logging.getLogger("taffy.consolidate")

ENABLED = False
RUN_HOUR = 3
CHECK_EVERY_SEC = 15 * 60
NIGHTS_BACK = 3
MAX_TRIES = 4
RETRY_AFTER_SEC = 45 * 60
MAX_OPS = 15
TIMEOUT_SEC = 600
CHAT_CHARS = 60_000
PERSON_CHARS = 1_500
REPLY_CHARS = 400
FACTS_CHARS = 30_000
ENDED_DAYS = 4
MERGE_NEW_WORDS = 3
EFFORT = "medium"

MODELS = (("claude", "opus"), ("chatgpt", "sol"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_nights (bot_id TEXT NOT NULL, night TEXT NOT NULL, state TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0, last_try INTEGER, model TEXT, ops INTEGER, PRIMARY KEY (bot_id, night));
CREATE TABLE IF NOT EXISTS fact_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, night TEXT NOT NULL,
    op TEXT NOT NULL, state TEXT NOT NULL, fact_ids TEXT, new_ids TEXT, before TEXT, detail TEXT, evidence TEXT,
    reason TEXT, at INTEGER NOT NULL, undone_at INTEGER);
CREATE INDEX IF NOT EXISTS fact_changes_night ON fact_changes (bot_id, night);
"""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)


def now() -> float:
    ""
    return time.time()




def window(night: str) -> tuple[float, float]:
    ""
    d = date.fromisoformat(night)
    start = datetime.combine(d, clock(RUN_HOUR), home.TZ)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def due(at: float | None = None) -> list[str]:
    ""
    t = datetime.fromtimestamp(now() if at is None else at, home.TZ) - timedelta(hours=RUN_HOUR)
    return [(t.date() - timedelta(days=k)).isoformat() for k in range(NIGHTS_BACK, 0, -1)]


def _night_row(bot_id: str, night: str) -> dict | None:
    r = store.db().execute("SELECT * FROM fact_nights WHERE bot_id=? AND night=?", (bot_id, night)).fetchone()
    return dict(r) if r else None


def wanted(bot_id: str, night: str, at: float) -> bool:
    ""
    r = _night_row(bot_id, night)
    if r is None:
        return True
    return r["state"] == "failed" and r["tries"] < MAX_TRIES and at - (r["last_try"] or 0) >= RETRY_AFTER_SEC


def _mark(bot_id: str, night: str, state: str, model: str | None = None, ops: int | None = None) -> None:
    c = store.db()
    old = _night_row(bot_id, night)
    tries = (old["tries"] if old else 0) + (1 if state in ("done", "failed") else 0)
    c.execute("INSERT OR REPLACE INTO fact_nights (bot_id, night, state, tries, last_try, model, ops) "
              "VALUES (?,?,?,?,?,?,?)", (bot_id, night, state, tries, int(now()), model, ops))
    c.commit()




def day_chat(bot_id: str, start: float, end: float) -> list[dict]:
    ""


    from . import choices, history
    root = store.session_for(bot_id)
    path = lineage._state_db(bot_id)
    if not root or not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            ids = recentchat._chat_sessions(conn, root)
            rows = conn.execute(
                f"SELECT id, role, content, timestamp FROM messages WHERE session_id IN ({','.join('?' * len(ids))}) "
                "AND role IN ('user', 'assistant') AND _compressed_summary = 0 "
                "AND COALESCE(display_kind, '') NOT IN ('hidden', 'failed_turn') AND timestamp >= ? AND timestamp < ? "
                "ORDER BY timestamp, id", (*ids, start, end)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        log.warning("could not read %s's chat", bot_id, exc_info=True)
        return []
    msgs = [{"id": i, "role": role, "text": content or "", "ts": float(ts)} for i, role, content, ts in rows]
    out, seen = [], set()
    answers = choices.card_answers()
    for m in history.classify(msgs):
        person = m["class"] in ("colin", "call_turn") and not history.spoken_for(str(m["display"] or ""))
        if not person and m["class"] not in ("bot", "call_reply"):
            continue
        said = choices.own_words(str(m["display"] or ""), answers) if person else m["display"]
        text = recentchat.clean(" ".join(str(said or "").split()))

        if not text or person and text.startswith("["):
            continue
        key = (round(m["ts"], 3), m["role"], text)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": m["id"], "ts": m["ts"], "person": person,
                    "text": text[:PERSON_CHARS] if person else _short(text, REPLY_CHARS)})
    return out


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def chat_lines(msgs: list[dict], limit: int = CHAT_CHARS) -> str:
    ""
    from . import who

    def line(m: dict) -> str:
        return f"[{m['id']}] {recentchat.when(m['ts'])}, {who.first() if m['person'] else 'You'}: {m['text']}"
    keep = list(msgs)
    while keep and sum(len(line(m)) + 1 for m in keep) > limit:
        drop = next((m for m in keep if not m["person"]), keep[0])
        keep.remove(drop)
    return "\n".join(line(m) for m in keep)


def _ended_recently(bot_id: str, at: float) -> list[dict]:
    where, args = facts._visible(bot_id)
    rows = facts._db().execute(
        f"SELECT * FROM facts WHERE replaced_by IS NULL AND {where} AND valid_until > ? AND valid_until <= ? "
        "ORDER BY id", args + (int(at - ENDED_DAYS * 86400), int(at))).fetchall()
    return [dict(r) for r in rows]


def fact_line(f: dict, newer_after: float | None = None) -> str:
    bits = [f["kind"]] + ([f["subject"]] if f.get("subject") else [])
    if newer_after is not None and facts.last_said(f) > newer_after:
        bits.append("newer than this chat")
    over = facts.when_over(f)
    if over:
        bits.append(over)
    elif f.get("valid_until"):
        bits.append(f"holds until {factdates.ended_words(f['valid_until'])}")
    return f"[{f['id']}] ({', '.join(bits)}; saved {facts._day(f['observed_at'])}) {f['text']}"


def _fact_block(rows: list[dict], limit: int, newer_after: float | None = None) -> str:
    order = {k: i for i, k in enumerate(facts.BRIEFING_ORDER)}
    out, used = [], 0
    for f in sorted(rows, key=lambda f: (order.get(f["kind"], 99), -f["id"])):
        line = fact_line(f, newer_after)
        if used + len(line) > limit:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) or "None."





PROMPT = """You keep the shared notes a team of assistants has about {person}. Tonight you read one day of \
{person}'s chat with {zip} ({day}) and bring the notes up to date, the way a careful friend who was there would.

Today is {today}. Each message has its number in brackets and when it was said. Words like "tonight", "tomorrow", \
"Friday" or "in 10 minutes" count from when that message was said.

Operations:
- ADD: a fact the chat states that no note has: a plan, a decision, a commitment, a preference, a person, a place. \
For a trip, say when {person} left or leaves, with the time of day the messages give ("heading out in 10 minutes" \
said at 3:02 p.m. is about 3:15 p.m.), where {person} sleeps each night, and when {person} is back.
- UPDATE: notes the chat changes or corrects. Give the note numbers it replaces and the facts that hold now. When a \
note holds several claims and only one changed, write each claim that still holds as its own fact, so nothing true \
is lost ("they own the car camping gear" stays when where they sleep changes). Two notes that say the same thing \
become one UPDATE with both numbers and one fact.
- EXPIRE: a current note that no longer holds: its plan happened or passed ("past"), or it was called off \
("cancelled"). A note whose time is over, or a one-off remark that has served its purpose, can be expired with \
"dates_passed" instead of messages, and "why" says in one sentence what is over.
- NOOP when nothing needs to change.

Rules:
- A message {person} did not write (another assistant or a test speaking of {person} by name, a practice run, made-up \
data) is never evidence.
- Only what {person} said, decided or agreed to. {zip}'s messages (shown as "You") are context: a suggestion counts \
when {person} took it up or went ahead with it without objecting, and then cite both messages. Cite the message numbers each operation rests on, at least one of them {person}'s.
- One claim per fact, one sentence, about {person} in the third person, with every specific detail. Write real \
dates with the weekday ("Thursday, October 22, 2026"), never "today" or "tonight", and only dates the cited \
messages give.
- A day You suggested stays a plan when {person} goes along with it, never a deadline: "{person} planned to set up \
the course app by Monday, October 26, 2026, before class starts; no due date is posted", never "{person} needs to \
set up the course app by Monday". Keep the source the chat gives for a date ("from the teacher's email").
- "until": the last day a fact holds, as YYYY-MM-DD, or "YYYY-MM-DD HH:MM" when it ends at a time ("home by noon"). \
null for lasting facts.
- A fact that {person} will do something themselves ("will fill it out themselves", "only {person} can") needs \
"quote": the words of {person}'s own cited message that say so in the first person, copied exactly. Without them, \
leave it out: {zip} does that work.
- Leave out what {zip} did, one-off requests ("open the sites"), moods, passwords, codes, account or card numbers, \
sign-in details, and health details.
- A note marked "newer than this chat" was said after everything below, so it wins: never replace or expire it, and \
leave out anything in the chat about the same thing.
- Never expire or replace a note because the chat does not mention it. Notes that already ended can be the source of \
an ADD or UPDATE for a claim that still holds; never expire them again.
- At most {most} operations, the most important first.

Current notes:
{current}

Notes that ended in the last few days:
{ended}

The chat:
{chat}

Answer with JSON only, no other text:
{{"ops": [
 {{"op": "ADD", "kind": "plan|decision|status|preference|place|person|purchase|supply|other", "subject": "one to three \
words", "text": "one sentence", "until": null, "quote": null, "evidence": [message numbers]}},
 {{"op": "UPDATE", "replaces": [note numbers], "facts": [{{"kind": "...", "subject": "...", "text": "...", \
"until": null, "quote": null}}], "evidence": [message numbers]}},
 {{"op": "EXPIRE", "fact": note number, "status": "past|cancelled", "evidence": [message numbers] or \
"dates_passed", "why": "one sentence"}},
 {{"op": "NOOP"}}]}}"""


def prompt(bot_id: str, night: str, msgs: list[dict], current: list[dict], ended: list[dict], at: float) -> str:
    from . import who
    zip_name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "the assistant"
    room = FACTS_CHARS - FACTS_CHARS // 5
    return PROMPT.format(
        person=who.first(), zip=zip_name, most=MAX_OPS,
        day=date.fromisoformat(night).strftime("%A, %B %-d, %Y"),
        today=datetime.fromtimestamp(at, home.TZ).strftime("%A, %B %-d, %Y"),
        current=_fact_block(current, room, window(night)[1]), ended=_fact_block(ended, FACTS_CHARS - room), chat=chat_lines(msgs))


SYSTEM = ("Complete the requested text task using only the supplied material. Follow its format and preserve the "
          "facts it asks for. Do not add facts or take actions.")


async def ask(text: str) -> tuple[str, str]:
    ""

    from . import health, modelsetup, shortreply
    M = modelsetup.M
    s = modelsetup.current()
    use = s.use if s is not None else tuple(M.PLANS)
    order = [(plan, key) for plan, key in MODELS if plan in use]
    if not health.status.get("claude", True):
        order.sort(key=lambda pk: pk[0] == "claude")
    for plan, key in order:
        m = M.CATALOG[key]
        try:
            answer = await shortreply.ask_helper(text, model=m, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=32000,
                                                 effort=EFFORT, fallback=False)
        except Exception:
            log.warning("the nightly pass could not read on %s", m.id, exc_info=True)
            continue
        if answer and answer.strip():
            return answer, m.id
    raise RuntimeError("no plan answered")




class Rejected(Exception):
    ""


def _ints(v) -> list[int]:
    items = v if isinstance(v, list) else [v] if v is not None else []
    out = []
    for i in items:
        if isinstance(i, (int, float)) or str(i).strip().isdigit():
            out.append(int(i))
    return out


def _cited(op: dict, by_id: dict[int, dict], need: bool = True) -> list[dict]:
    ids = _ints(op.get("evidence"))
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise Rejected(f"cites messages that are not in the day's chat: {unknown}")
    cited = [by_id[i] for i in ids]
    if need and not any(m["person"] for m in cited):
        raise Rejected("cites none of the person's own messages")
    return cited


def _allowed(cited: list[dict], replaced: list[dict] = ()) -> set[tuple[int, int]]:
    days = set()
    for m in cited:
        days |= extract._dates_from(m["ts"], m["text"])
    for f in replaced:
        days |= extract._month_days(f["text"])
    return days


def _fact(item: dict, allowed: set[tuple[int, int]], at: float, cited: list[dict] = ()) -> dict:
    ""

    text = " ".join(str(item.get("text") or "").split())
    kind = str(item.get("kind") or "other")
    if kind not in facts.KINDS:
        raise Rejected(f"unknown kind {kind!r}")
    if len(text) < 8 or len(text) > 400:
        raise Rejected("the fact is not one sentence of a usable length")
    if facts.secret(text):
        raise Rejected("the fact holds a secret")
    if not facts.owned_by_words(text, item.get("quote"), "\n".join(m["text"] for m in cited if m["person"])):
        raise Rejected("leaves the work to the person without their own words saying so")
    bad = extract._month_days(text) - allowed
    if bad:
        raise Rejected(f"dates its evidence does not give: {sorted(bad)}")
    until = factdates.parse_until(item.get("until"), at) if item.get("until") else None
    if until is not None and factdates.day_of(until) not in allowed | _mornings_after(allowed, at):

        log.info("nightly end dropped: its day is not one the cited messages give")
        until = None
    return {"text": text, "kind": kind, "subject": str(item.get("subject") or "")[:60], "until": until}


def _mornings_after(days: set[tuple[int, int]], at: float) -> set[tuple[int, int]]:
    ""
    year = datetime.fromtimestamp(at, home.TZ).year
    out = set()
    for m, d in days:
        try:
            nxt = date(year, m, d) + timedelta(days=1)
        except ValueError:
            continue
        out.add((nxt.month, nxt.day))
    return out


def _new_words(new: list[dict], old: list[dict]) -> int:
    have = set().union(*(facts._words(f["text"]) for f in old)) if old else set()
    return max((len(facts._words(f["text"]) - have) for f in new), default=0)


def _dates_passed(f: dict, at: float) -> float | None:
    ""
    seen = datetime.fromtimestamp(f["observed_at"], home.TZ).date()
    days = []
    for m, d in extract._month_days(f["text"]):
        try:
            day = date(seen.year, m, d)
        except ValueError:
            continue
        days.append(day.replace(year=day.year + 1) if day < seen - timedelta(days=120) else day)
    if not days:
        return None
    end = datetime.combine(max(days) + timedelta(days=1), clock(0), home.TZ).timestamp()
    return end if end <= at else None


def check(raw: dict | None, msgs: list[dict], shown: dict[int, dict], at: float,
          newer_after: float | None = None) -> list[dict]:
    ""

    def said_since(ids: list[int]) -> bool:
        return newer_after is not None and any(facts.last_said(shown[i]) > newer_after for i in ids)

    ops = (raw or {}).get("ops") if isinstance(raw, dict) else None
    by_id = {m["id"]: m for m in msgs}
    out = []
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "").upper()
        if kind == "NOOP":
            continue
        entry = {"op": kind, "raw": op, "state": "ok", "reason": ""}
        try:
            if len([e for e in out if e["state"] == "ok"]) >= MAX_OPS:
                raise Rejected(f"past the night's cap of {MAX_OPS}")
            if kind == "ADD":
                cited = _cited(op, by_id)
                entry.update(cited=cited, facts=[_fact(op, _allowed(cited), at, cited)])
            elif kind == "UPDATE":
                ids = _ints(op.get("replaces"))
                if not ids or any(i not in shown for i in ids):
                    raise Rejected(f"replaces notes it was not shown: {ids}")
                replaced = [shown[i] for i in ids]
                cited = _cited(op, by_id, need=bool(op.get("evidence")))
                if cited and said_since(ids):
                    raise Rejected("a note saved after this chat is newer than what it says")
                items = op.get("facts") if isinstance(op.get("facts"), list) else []
                if not 1 <= len(items) <= 4:
                    raise Rejected("an update needs one to four facts")
                new = [_fact(i if isinstance(i, dict) else {}, _allowed(cited, replaced), at, cited) for i in items]
                if not cited and _new_words(new, replaced) > MERGE_NEW_WORDS:
                    raise Rejected("says something new without citing a message")
                entry.update(cited=cited, replaces=ids, facts=new, seen=facts.revisions(shown[i] for i in ids))
            elif kind == "EXPIRE":
                fid = (_ints(op.get("fact")) or [None])[0]
                f = shown.get(fid)
                if f is None or facts.state(f, at) != "current":
                    raise Rejected(f"note {fid} is not a current note it was shown")
                status = str(op.get("status") or "past")
                if status not in ("past", "cancelled"):
                    raise Rejected(f"unknown status {status!r}")
                if op.get("evidence") == "dates_passed":


                    end = _dates_passed(f, at) or at
                    entry["reason"] = " ".join(str(op.get("why") or "Its dates have passed.").split())[:300]
                    cited = []
                else:
                    cited = _cited(op, by_id)
                    if said_since([fid]):
                        raise Rejected("a note saved after this chat is newer than what it says")
                    end = max(m["ts"] for m in cited)
                entry.update(cited=cited, fact=fid, status=status, end=end, seen=facts.revisions([f]))
            else:
                raise Rejected(f"unknown operation {kind!r}")
        except Rejected as e:
            entry.update(state="rejected", reason=str(e))
        out.append(entry)
    return out




def _evidence(cited: list[dict], session_id: str | None) -> dict:
    person = next((m for m in cited if m["person"]), cited[0] if cited else None)
    if person is None:
        return {"session_id": session_id}
    return {"session_id": session_id, "message_id": person["id"], "at": int(person["ts"]), "quote": person["text"][:160]}


def _save(bot_id: str, night: str, f: dict, cited: list[dict], session_id: str | None, at: float,
          private: bool = False, replacing: set[int] = frozenset()) -> tuple[int, bool]:
    ""


    top = facts.top_id()
    fid, _ = facts.save(bot_id if bot_id in config.BOTS_BY_ID else "hub", f["text"], subject=f["subject"],
                        kind=f["kind"], session_id=session_id, source="chat", source_ref=f"nightly:{night}",
                        private=private, merge=False,
                        observed_at=max((m["ts"] for m in cited), default=at), limits=False, until=f["until"],
                        evidence=_evidence(cited, session_id), exclude=set(replacing))
    return fid, fid > top


def _record(c: sqlite3.Connection, bot_id: str, night: str, e: dict, fact_ids: list[int], new_ids: list[int],
            before: list[dict], at: float) -> None:
    c.execute("INSERT INTO fact_changes (bot_id, night, op, state, fact_ids, new_ids, before, detail, evidence, reason, "
              "at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (bot_id, night, e["op"], e["state"], json.dumps(fact_ids), json.dumps(new_ids), json.dumps(before),
               json.dumps(e["raw"])[:4000], json.dumps([m["id"] for m in e.get("cited") or []]), e["reason"], int(at)))


def apply(bot_id: str, night: str, checked: list[dict], session_id: str | None, at: float) -> int:
    ""
    c = store.db()
    applied = 0
    for e in checked:

        if e["state"] == "ok" and not facts.unchanged(e.get("seen", {})):
            e.update(state="rejected", reason="the note changed while the chat was being read")
        if e["state"] != "ok":
            _record(c, bot_id, night, e, [], [], [], at)
            continue
        try:
            if e["op"] == "EXPIRE":
                before = facts.get(e["fact"]) or {}
                end = min(int(e["end"]), int(before.get("valid_until") or e["end"]))
                c.execute("UPDATE facts SET valid_until=?, status=? WHERE id=? AND replaced_by IS NULL",
                          (end, e["status"], e["fact"]))
                _record(c, bot_id, night, e, [e["fact"]], [], [before], at)
            else:
                ids = e.get("replaces") or []
                before = [facts.get(i) or {} for i in ids]

                private = any(b.get("visibility") == "owner" for b in before)
                saved = [_save(bot_id, night, f, e["cited"], session_id, at, private, set(ids)) for f in e["facts"]]
                new = [i for i, _ in saved if i]

                inserted = [i for i, fresh in saved if i and fresh]

                for i in new:
                    for r in c.execute("SELECT * FROM facts WHERE replaced_by=?", (i,)).fetchall():
                        if r["id"] not in ids:
                            ids.append(r["id"])
                            before.append({**dict(r), "replaced_by": None})
                for i in ids:
                    if new and i not in new:
                        c.execute("UPDATE facts SET replaced_by=? WHERE id=? AND replaced_by IS NULL", (new[0], i))
                _record(c, bot_id, night, e, ids, inserted, before, at)
            applied += 1
        except facts.FactError as err:
            e.update(state="rejected", reason=str(err))
            _record(c, bot_id, night, e, [], [], [], at)
    c.commit()
    return applied


def merge_restated(bot_id: str, night: str, at: float) -> int:
    ""



    c = store.db()
    ended: set[int] = set()
    for f in reversed(facts.current(bot_id)):
        if f["source"] == "mail" or f.get("edited_at"):
            continue
        try:
            evidence = json.loads(f["evidence"]) if f.get("evidence") else None
        except ValueError:
            evidence = None



        keep = facts.restated(f["text"], f["kind"], f["bot_id"], f["visibility"], evidence, ended | {f["id"]})
        if keep is None or facts.authority(f) > facts.authority(keep):
            continue
        c.execute("UPDATE facts SET replaced_by=? WHERE id=? AND replaced_by IS NULL", (keep["id"], f["id"]))
        _record(c, bot_id, night, {"op": "UPDATE", "state": "ok", "reason": f"restates fact {keep['id']}",
                                   "raw": {"op": "UPDATE", "replaces": [f["id"]], "into": keep["id"]}},
                [f["id"]], [], [f], at)
        ended.add(f["id"])
    c.commit()
    return len(ended)


def undo_night(bot_id: str, night: str) -> int:
    ""

    with store._lock:
        c = store.db()
        rows = c.execute("SELECT * FROM fact_changes WHERE bot_id=? AND night=? AND state='ok' AND undone_at IS NULL "
                         "ORDER BY id DESC", (bot_id, night)).fetchall()
        for r in rows:
            for b in json.loads(r["before"] or "[]"):
                now_row = facts.get(b["id"]) if b.get("id") else None
                if now_row and now_row.get("edited_at") != b.get("edited_at"):
                    continue
                if b.get("id"):
                    c.execute("UPDATE facts SET replaced_by=?, valid_until=?, status=? WHERE id=?",
                              (b.get("replaced_by"), b.get("valid_until"), b.get("status") or "current", b["id"]))

                    for name in ("text", "visibility", "observed_at", "data"):
                        if name in b:
                            c.execute(f"UPDATE facts SET {name}=? WHERE id=?", (b[name], b["id"]))
            if r["op"] == "PRACTICE_WORK":
                c.execute("UPDATE open_loops SET status='open', closed_at=NULL WHERE id=?",
                          (json.loads(r["detail"])["loop"],))
            for i in json.loads(r["new_ids"] or "[]"):
                if (facts.get(i) or {}).get("edited_at"):
                    continue
                c.execute("DELETE FROM facts WHERE id=?", (i,))
                c.execute("DELETE FROM fact_shown WHERE fact_id=?", (i,))
            c.execute("UPDATE fact_changes SET undone_at=? WHERE id=?", (int(now()), r["id"]))

        c.execute("UPDATE fact_nights SET state='undone' WHERE bot_id=? AND night=?", (bot_id, night))
        c.commit()
    return len(rows)




async def consolidate(bot_id: str, night: str, at: float | None = None) -> str:
    ""

    at = now() if at is None else at
    generation = openloops._generation
    start, end = window(night)
    erased = float(store.get_setting("openloops_waits_erased_at", 0) or 0)



    msgs = [] if shortreply.withheld(bot_id) else privatewords.keep(
        await asyncio.to_thread(day_chat, bot_id, max(start, erased), end), lambda m: m["text"])
    if not any(m["person"] for m in msgs):
        with store._lock:
            if generation == openloops._generation:
                _mark(bot_id, night, "skipped")
        return "skipped"
    current = privatewords.keep(facts.current(bot_id), fact_line)
    ended = privatewords.keep(_ended_recently(bot_id, at), fact_line)
    shown = {f["id"]: f for f in current + ended}
    try:
        with shortreply.reading((bot_id,)):
            answer, model = await ask(prompt(bot_id, night, msgs, current, ended, at))
        raw = extract._json(answer)
        if raw is None:
            raise ValueError("the answer was not JSON")
    except Exception:
        log.warning("nightly pass for %s, %s failed", bot_id, night, exc_info=True)
        with store._lock:
            if generation == openloops._generation:
                _mark(bot_id, night, "failed")
        return "failed"
    checked = check(raw, msgs, shown, at, end)
    with store._lock:
        if generation != openloops._generation:
            log.info("nightly pass for %s dropped: everything was deleted while it read", bot_id)
            return "dropped"
        try:
            n = apply(bot_id, night, checked, store.session_for(bot_id), at)
            n += merge_restated(bot_id, night, at)
        except Exception:

            log.warning("nightly pass for %s, %s could not save its changes", bot_id, night, exc_info=True)
            store.db().rollback()
            _mark(bot_id, night, "failed")
            return "failed"
        _mark(bot_id, night, "done", model, n)
    log.info("nightly pass for %s, %s: %d applied, %d rejected", bot_id, night, n,
             sum(1 for e in checked if e["state"] != "ok"))
    if n:
        try:
            from . import team
            team.write_notes_file()
        except Exception:
            log.warning("could not write the team notes file", exc_info=True)
    return "done"


async def nightly(at: float | None = None) -> dict[tuple[str, str], str]:
    ""
    at = now() if at is None else at
    out = {}
    for night in due(at):
        for bot_id in list(config.BOTS_BY_ID):

            if not config.is_sandbox(bot_id) and wanted(bot_id, night, at):
                out[(bot_id, night)] = await consolidate(bot_id, night, at)

    night = due(at)[-1]
    if wanted(factkeeper.SWEEP_BOT, night, at):
        out[(factkeeper.SWEEP_BOT, night)] = await factkeeper.nightly_sweep(night, at)
    return out


async def run() -> None:
    ""
    await asyncio.sleep(300)
    if not ENABLED:
        return
    while True:
        try:
            await nightly()
        except Exception:
            log.warning("the nightly pass failed", exc_info=True)
        await asyncio.sleep(CHECK_EVERY_SEC)

