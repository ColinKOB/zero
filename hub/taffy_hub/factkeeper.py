""























from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta

from . import config, factdates, facts, home, privatewords, shortreply, store

log = logging.getLogger("taffy.factkeeper")

SKIP_ZIPS = config.PRIVATE_ZIPS
_CLIENT_CARE_WORDS = r"connectabilit|\bpatients?\b"


def _client_care() -> re.Pattern:
    ""

    words = home.get("privacy.client_words") or []
    names = [re.escape(str(w)) for w in words if str(w).strip()] if isinstance(words, list) else []
    return re.compile("|".join([_CLIENT_CARE_WORDS, *names]), re.I)


SWEEP_BOT = "hub"
HELD_CHARS = 26_000
SWEEP_CHARS = 110_000
EVENT_DAYS = 14
ONE_OFF_DAYS = 7
RULE_KINDS = ("preference", "decision", "status")

_THEIRS = ("app", "setup")

SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_conflicts (source TEXT NOT NULL, key TEXT NOT NULL, title TEXT NOT NULL,
    shows TEXT NOT NULL, said TEXT NOT NULL, fact_id INTEGER NOT NULL, seen TEXT NOT NULL, found_at INTEGER NOT NULL,
    offered_at INTEGER, PRIMARY KEY (source, key));
"""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)


def now() -> float:
    ""
    return time.time()


async def ask_helper(prompt: str) -> str:
    ""
    from . import extract
    return await extract.ask_haiku(prompt)


async def ask_strong(prompt: str) -> str:
    ""
    from . import consolidate
    return (await consolidate.ask(prompt))[0]


def _sendable(f: dict) -> bool:
    ""



    return (not shortreply.withheld(f.get("bot_id")) and not config.is_sandbox(f.get("bot_id"))
            and not (_client_care().search(f"{f.get('subject') or ''} {f.get('text') or ''}")
                     and shortreply.withheld(*config.PRIVATE_ZIPS))
            and not privatewords.holds(f"{f.get('subject') or ''} {f.get('text') or ''} {_quote(f)}"))


def _quote(f: dict) -> str:
    try:
        return str((json.loads(f.get("evidence") or "null") or {}).get("quote") or "")[:120]
    except (TypeError, ValueError, AttributeError):
        return ""


def _today(at: float) -> str:
    return datetime.fromtimestamp(at, home.TZ).strftime("%A, %B %-d, %Y")


def _note_line(f: dict) -> str:
    bits = [f["kind"]] + ([f["subject"]] if f.get("subject") else [])
    bits.append(f"saved {facts._day(f.get('observed_at') or f.get('created_at') or now())}")
    return f"[{f['id']}] ({', '.join(bits)}) {f['text']}"




JUDGE_PROMPT = """You keep the shared notes a team of assistants has about {person}. New notes are about to be saved. \
Compare each with the notes already held, by what they mean, and say what should happen. Today is {today}.

For each new note:
- "known": a held note already says this and the new note adds nothing. Give that note's number in "fact".
- "update": the new note changes or corrects a held note, or says it again with more detail or in {person}'s newer \
words, so one note should hold it. Give that note's number in "fact" and list in "ends" any other held notes it \
repeats or makes wrong. The new note must keep every claim of the held note that is still true; if it would lose one, \
answer "add". "changes" is true when the new note says something different from the held note, and false when it says \
the same thing again.
- "add": nothing held says this. List in "ends" any held notes it makes wrong.
{fresh}
The same rule in other words is the same note. Different people, dates, places or amounts are different notes.

"scope": "everyone" when the note is about {person}: how to talk to {person}, what never to do, what {person} prefers, \
or anything in {person}'s life. "zip" only when it is about how {zip} alone does its own work and no other assistant \
could use it.
"lasts": "one_off" when it is a remark about one day, one email or one errand that stops mattering once that is done, \
and then "until" is the last day it matters, as YYYY-MM-DD. Otherwise "lasting", with "until" null.

Notes already held:
{held}

New notes, from {zip}:
{new}

Answer with JSON only, one entry per new note:
{{"verdicts": [{{"note": 0, "verdict": "add|update|known", "fact": null, "changes": true, "ends": [], \
"scope": "everyone|zip", \
"lasts": "lasting|one_off", "until": null}}]}}"""

_FRESH = ("These are {person}'s own words from just now, so when they say a held note again in different words, the "
          "answer is \"update\".")
_OLDER = ("These come from a summary of an older chat, so when a held note already says the same thing the answer is "
          "\"known\", whatever the wording. A held note marked [newer] was said after this summary: when a new note is "
          "about the same thing, answer \"known\" with that note's number even if they disagree, because the newer word "
          "wins.")


def held_for(texts: list[str], bot_id: str, skip: set[int] | frozenset = frozenset()) -> list[dict]:
    ""

    where, args = facts._visible(bot_id)
    holds, now_arg = facts.live()
    rows = [dict(r) for r in facts._db().execute(
        f"SELECT * FROM facts WHERE {holds} AND {where} AND source != 'mail' AND kind != 'purchase' ORDER BY id DESC",
        now_arg + args)]
    rows = [f for f in rows if f["id"] not in skip and _sendable(f)]
    matched = {f["id"] for t in texts for f in facts.search(t, bot_id, limit=8)}
    subjects = {facts._norm_subject(t) for t in texts}
    near = [f for f in rows if f["id"] in matched or f["subject"] in subjects]
    rules = [f for f in rows if f["kind"] in RULE_KINDS and f["id"] not in {n["id"] for n in near}]
    out, used = [], 0
    for f in near + rules:
        line = len(_note_line(f)) + 1
        if used + line > HELD_CHARS:
            break
        out.append(f)
        used += line
    return out


async def _verdicts(items: list[dict], held: list[dict], bot_id: str, fresh: bool,
                    newer: set[int] | frozenset = frozenset()) -> dict[int, dict] | None:
    ""
    from . import extract, who
    zip_name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "the assistant"
    person = who.first()

    new = "\n".join(line for line in (f"[{i}] ({it.get('kind') or 'other'}, {it.get('subject') or 'no subject'}) "
                                       f"{it.get('text')}" for i, it in enumerate(items)) if not privatewords.holds(line))
    if not new:
        return None
    with shortreply.reading((bot_id,)):
        answer = extract._json(await ask_helper(JUDGE_PROMPT.format(
            person=person, zip=zip_name, today=_today(now()), new=new,
            held="\n".join(_note_line(f) + (" [newer]" if f["id"] in newer else "") for f in held) or "None.",
            fresh=(_FRESH if fresh else _OLDER).format(person=person))))
    rows = (answer or {}).get("verdicts")
    if not isinstance(rows, list):
        return None
    out = {}
    for v in rows:
        if isinstance(v, dict) and str(v.get("note")).isdigit() and v.get("verdict") in ("add", "update", "known"):
            out[int(v["note"])] = v
    return out or None


def _ids(value, allowed: set[int]) -> list[int]:
    items = value if isinstance(value, list) else [value]
    return [int(i) for i in items if str(i).isdigit() and int(i) in allowed]


def _until(v: dict, at: float) -> str | None:
    ""
    if v.get("lasts") != "one_off":
        return None
    given = str(v.get("until") or "")
    if factdates.parse_until(given, at) is not None:
        return given
    return (datetime.fromtimestamp(at, home.TZ) + timedelta(days=ONE_OFF_DAYS)).strftime("%Y-%m-%d")


def _heard_again(fid: int, at: float, seen: list) -> bool:
    ""

    with store._lock:
        c = store.db()
        if not facts.unchanged({fid: seen}):
            return False
        c.execute("UPDATE facts SET observed_at=MAX(observed_at, ?) WHERE id=?", (int(at), fid))
        c.commit()
    return True


def _said_again(v: dict, new: dict, held: dict) -> bool:
    ""

    return v["verdict"] == "update" and v.get("changes") is False and facts.authority(held) > facts.authority(new)


def _stated_at(item: dict, said_at: float | None) -> float | None:
    ""
    cited = item.get("evidence", {}).get("at") if isinstance(item.get("evidence"), dict) else None
    return float(cited) if isinstance(cited, (int, float)) and said_at is not None else said_at


async def settle(out: dict | None, bot_id: str, fresh: bool = True,
                 said_at: float | None = None) -> tuple[dict | None, dict[int, list]]:
    ""






    items = [i for i in ((out or {}).get("facts") or []) if isinstance(i, dict)] if isinstance(out, dict) else []
    if not items or shortreply.withheld(bot_id) or config.is_sandbox(bot_id):
        return out, {}
    held = held_for([str(i.get("text") or "") for i in items] + [str(i.get("subject") or "") for i in items], bot_id)
    try:
        verdicts = await _verdicts(items, held, bot_id, fresh, {
            f["id"] for f in held if said_at is not None and facts.last_said(f) > said_at})
    except Exception:
        log.warning("could not compare new notes with the held ones", exc_info=True)
        return out, {}
    if verdicts is None:
        return out, {}
    by_id, seen = {f["id"]: f for f in held}, facts.revisions(held)
    allowed, at, kept = set(by_id), now(), []
    for n, item in enumerate(items):
        v = verdicts.get(n)
        if v is None:
            kept.append(item)
            continue
        target = _ids(v.get("fact"), allowed)
        if target and _said_again(v, item, by_id[target[0]]):
            v = {**v, "verdict": "known"}
        stated = _stated_at(item, said_at)
        if v["verdict"] == "known" and target and _heard_again(target[0], at if stated is None else stated,
                                                                      seen[target[0]]):
            log.info("a reader's note was already held as fact %d", target[0])
            continue
        ends = list(dict.fromkeys((target if v["verdict"] == "update" else []) + _ids(v.get("ends") or [], allowed)
                                  + _ids(item.get("replaces") or [], allowed)))
        if stated is not None and any(facts.last_said(by_id[i]) > stated for i in ends):
            log.info("a summary's note was dropped: the person said something newer about it")
            continue
        item["compared"] = True
        if ends:
            item["replaces"] = ends

            item["keep_private"] = any(by_id[i]["visibility"] == "owner" for i in ends)
        item["scope"] = "zip" if v.get("scope") == "zip" else "everyone"
        item["until"] = item.get("until") or _until(v, at)
        kept.append(item)
    return {**out, "facts": kept}, seen


_saving: set[asyncio.Task] = set()


def compares(bot_id: str) -> bool:
    ""
    from . import extract
    return extract.ENABLED and not shortreply.withheld(bot_id) and not config.is_sandbox(bot_id)


def after_zip_save(fid: int, bot_id: str) -> None:
    ""

    if not fid or not compares(bot_id):
        return
    try:
        task = asyncio.get_running_loop().create_task(judge_saved(fid, bot_id))
    except RuntimeError:
        return
    _saving.add(task)
    task.add_done_callback(_saving.discard)


async def judge_saved(fid: int, bot_id: str) -> str:
    ""
    f = facts.get(fid)
    if not f or f.get("replaced_by") is not None or not _sendable(f):
        return ""
    held = held_for([f["text"], f["subject"]], bot_id, {fid})
    seen = facts.revisions([f, *held])
    try:
        verdicts = await _verdicts([f], held, bot_id, True)
    except Exception:
        log.warning("could not compare fact %d with the held ones", fid, exc_info=True)
        return ""
    v = (verdicts or {}).get(0)
    if not v:
        return ""
    by_id, at = {h["id"]: h for h in held}, now()
    allowed = set(by_id)
    target = _ids(v.get("fact"), allowed)
    if target and _said_again(v, f, by_id[target[0]]):
        v = {**v, "verdict": "known"}
    elif target and v["verdict"] == "known" and facts.authority(f) > facts.authority(by_id[target[0]]):
        v = {**v, "verdict": "update"}

    earlier = {h["id"] for h in held if h["id"] < fid and (h.get("observed_at") or 0) <= f["observed_at"]}
    ends = list(dict.fromkeys(_ids((target if v["verdict"] == "update" else []) + list(v.get("ends") or []), earlier)))
    day = datetime.fromtimestamp(at, home.TZ).strftime("%Y-%m-%d")
    with store._lock:
        c = store.db()

        if not facts.unchanged({i: seen[i] for i in (fid, *target, *ends)}):
            log.info("fact %d or a note it was compared with changed meanwhile, so the verdict is dropped", fid)
            return ""
        if v["verdict"] == "known" and target:
            before = [facts.get(fid) or {}, facts.get(target[0]) or {}]
            c.execute("UPDATE facts SET replaced_by=? WHERE id=?", (target[0], fid))
            c.execute("UPDATE facts SET observed_at=MAX(observed_at, ?) WHERE id=?", (int(at), target[0]))
            _record(c, "MERGE", f"save:{day}", [fid], before, f"already held as fact {target[0]}", v, at)
            c.commit()
            return "known"
        before = [facts.get(i) or {} for i in [fid] + ends]
        for i in ends:
            c.execute("UPDATE facts SET replaced_by=? WHERE id=? AND replaced_by IS NULL", (fid, i))
        if v.get("scope") == "zip":
            c.execute("UPDATE facts SET visibility='owner' WHERE id=?", (fid,))
        last = factdates.parse_until(_until(v, at), at)
        if last is not None and f.get("valid_until") is None:
            c.execute("UPDATE facts SET valid_until=? WHERE id=?", (int(last), fid))
        if ends or v.get("scope") == "zip" or last is not None:
            _record(c, "MERGE" if ends else "SCOPE", f"save:{day}", ends or [fid], before,
                    f"compared when saved: {v['verdict']}", v, at)
        c.commit()
    return v["verdict"]




def _record(c: sqlite3.Connection, op: str, night: str, fact_ids: list[int], before: list[dict], reason: str,
            raw, at: float) -> None:
    c.execute("INSERT INTO fact_changes (bot_id, night, op, state, fact_ids, new_ids, before, detail, evidence, reason, "
              "at) VALUES (?,?,?,'ok',?,'[]',?,?,'[]',?,?)",
              (SWEEP_BOT, night, op, json.dumps(fact_ids), json.dumps(before), json.dumps(raw)[:4000], reason[:600],
               int(at)))


def restore(fid: int) -> dict:
    ""
    at = now()
    with store._lock:
        f = facts.get(fid)
        if not f:
            raise facts.FactError("That fact is gone.")
        data = _data(f)
        data.pop("said_by", None)
        c = store.db()
        c.execute("UPDATE facts SET replaced_by=NULL, status='current', valid_until=NULL, data=? WHERE id=?",
                  (json.dumps(data) if data else None, fid))
        _record(c, "RESTORE", f"restore:{datetime.fromtimestamp(at, home.TZ).strftime('%Y-%m-%d')}", [fid], [f],
                "put back by hand", {"fact": fid}, at)
        c.commit()
    return facts.get(fid) or {}


def _data(f: dict) -> dict:
    try:
        out = json.loads(f.get("data") or "null")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}




SWEEP_PROMPT = """You keep the shared notes a team of assistants has about {person}. Tonight read every note below and \
tidy them, the way a careful friend would. Today is {today}.

Operations:
- "merge": notes that say the same thing, across days and assistants, in any words. "keep" is the note that ranks first: \
one {person} wrote, then one an assistant saved on purpose, then one read from a chat; among equals a preference before \
a decision, then the newest. "copies" are the others. When a copy holds a detail the kept note lacks, "text" is one sentence with the kept \
note's meaning and that detail; otherwise "text" is null. Where the notes disagree, they are not copies. A note \
marked "owner" is private to the assistant that saved it, so merge it only with that assistant's other "owner" notes, \
and merge a "team" note only with other "team" notes. Different \
people, dates, places, amounts or rules that only look alike are different notes.
- "retire": a note that no longer holds. "reason" is "dates_passed" (everything it is about is over), \
"served_its_purpose" (a remark about one day, one email or one errand, now done) or "contradicted" (a newer note says \
otherwise). "why" says in one sentence what happened. A lasting rule or preference is never retired for being old, \
and something in {person}'s life that stays true after its date (a job left, a move, something bought) is never retired. \
A note with several claims is retired only when every claim is over.
- "widen": a "team" note that is a rule about how to treat {person} (how to talk to {person}, what never to do, what \
{person} prefers) but was worded for one assistant or one project only. Never widen an "owner" note, and leave out \
details of one assistant's own work. "text" is the same rule worded for every assistant, with its meaning unchanged. \
Never turn a remark about one occasion into a standing rule, and never add an approval step or a limit the note does \
not state.
- "not_theirs": a note that rests on words {person} did not say: written by another assistant or a test, a practice \
run, or made-up data. A quote that speaks of {person} by name is a sign of this. "why" says in one sentence how you \
can tell.
- "practice_work": numbers of the open work items below that exist only because of a practice or test run.

Leave a note alone when unsure. Use only the numbers shown.

Notes:
{notes}

Open work items:
{loops}

Answer with JSON only, no other text:
{{"merge": [{{"keep": 1, "copies": [2], "text": null, "why": "one sentence"}}], \
"retire": [{{"fact": 3, "reason": "dates_passed", "why": "one sentence"}}], \
"widen": [{{"fact": 4, "text": "one sentence", "why": "one sentence"}}], \
"not_theirs": [{{"fact": 5, "why": "one sentence"}}], "practice_work": []}}"""

CONFLICT_PROMPT = """{person}'s calendar is below, with what {person} has told the team. Find each calendar entry that \
disagrees with something {person} said: it moved, it was called off, or it is on a different day or time now. Only \
count a clear disagreement about that same entry. Today is {today}.

Calendar:
{events}

What {person} said:
{notes}

Answer with JSON only, no other text:
{{"conflicts": [{{"event": "the entry's id", "fact": 1, "shows": "what the calendar shows, in a few words", \
"said": "what {person} said, in a few words"}}]}}"""


def sweep_facts(at: float | None = None) -> list[dict]:
    ""
    holds, now_arg = facts.live()
    if at is not None:
        now_arg = (int(at),)
    rows = [dict(r) for r in store.db().execute(
        f"SELECT * FROM facts WHERE {holds} AND source != 'mail' AND kind != 'purchase' ORDER BY id", now_arg)]
    return [f for f in rows if _sendable(f)]


def _sweep_line(f: dict) -> str:
    quote = _quote(f)
    who_saved = facts._name(f["bot_id"])
    how = ("read from a chat", "saved on purpose", "written by the person")[facts.authority(f)]
    return f"{_note_line(f)} [by {who_saved}, {how}, {f.get('visibility')}]" + (f' (quote: "{quote}")' if quote else "")


def _batches(rows: list[dict]) -> list[list[dict]]:
    ""
    if sum(len(_sweep_line(f)) + 1 for f in rows) <= SWEEP_CHARS:
        return [rows] if rows else []
    rules = [f for f in rows if f["kind"] in RULE_KINDS]
    rest = [f for f in rows if f["kind"] not in RULE_KINDS]
    return [b for b in (rules, rest) if b]


def _open_work() -> list[dict]:
    ""
    rows = store.db().execute("SELECT id, bot_id, topic, updated_at FROM open_loops WHERE status='open' ORDER BY id").fetchall()
    return [dict(r) for r in rows if not shortreply.withheld(r["bot_id"]) and not config.is_sandbox(r["bot_id"])
            and not privatewords.holds(r["topic"])]


def _protected(f: dict) -> bool:
    ""
    return bool(f.get("edited_at")) or f.get("source") in _THEIRS


def _audience(f: dict) -> tuple:
    ""
    return ("team",) if f["visibility"] == "team" else ("owner", f["bot_id"])


def _closes(f: dict) -> set[str]:
    return facts._closed_by(f, {}, facts._person_words())


def check_sweep(raw: dict | None, shown: dict[int, dict], work: dict[int, dict] | None = None) -> list[dict]:
    ""

    shown = {i: f for i, f in shown.items() if _sendable(f)}
    raw = raw if isinstance(raw, dict) else {}
    out: list[dict] = []
    used: set[int] = set()

    def entry(op: str, item: dict, reason: str = "", **fields) -> None:
        ids = [i for i in (fields.get("keep"), fields.get("fact"), *fields.get("copies", [])) if i is not None]
        out.append({"op": op, "raw": item, "state": "rejected" if reason else "ok", "reason": reason,
                    "seen": facts.revisions(shown[i] for i in ids), **fields})

    def rows(name: str) -> list[dict]:
        v = raw.get(name)
        return [i for i in v if isinstance(i, dict)] if isinstance(v, list) else []

    for item in rows("merge"):
        keep = shown.get(item.get("keep")) if isinstance(item.get("keep"), int) else None
        copies = [shown[i] for i in _ids(item.get("copies") or [], set(shown)) if i != (keep or {}).get("id")]
        text = " ".join(str(item.get("text") or "").split())
        if keep is None or not copies:
            entry("MERGE", item, "names notes it was not shown")
        elif keep["id"] in used or any(f["id"] in used for f in copies):
            entry("MERGE", item, "touches a note another operation already changed")
        elif any(_protected(f) for f in copies):
            entry("MERGE", item, "would end a note the person wrote")
        elif any(_audience(f) != _audience(keep) for f in copies):

            entry("MERGE", item, "these notes are seen by different Zips")
        elif any(_closes(f) - _closes(keep) for f in copies):
            entry("MERGE", item, "would reopen a topic the person closed")
        elif any(facts.rank(f) > facts.rank(keep) for f in copies):
            entry("MERGE", item, "would end a note that outranks the one it keeps")
        elif text and (not 8 <= len(text) <= 400 or facts.secret(text) or _protected(keep)):
            entry("MERGE", item, "the new wording is not usable")
        else:
            used |= {keep["id"]} | {f["id"] for f in copies}
            entry("MERGE", item, keep=keep["id"], copies=[f["id"] for f in copies], text=text,
                  why=str(item.get("why") or "these notes say the same thing"))
    for item in rows("retire"):
        f = shown.get(item.get("fact")) if isinstance(item.get("fact"), int) else None
        why = " ".join(str(item.get("why") or "").split())
        if f is None or f["id"] in used:
            entry("RETIRE", item, "names a note it was not shown, or one already changed")
        elif not why or item.get("reason") not in ("dates_passed", "served_its_purpose", "contradicted"):
            entry("RETIRE", item, "gives no reason")
        elif _protected(f):
            entry("RETIRE", item, "the person wrote this note")
        elif _closes(f):
            entry("RETIRE", item, "this note is what keeps a topic closed")
        else:
            used.add(f["id"])
            entry("RETIRE", item, fact=f["id"], why=f"{item['reason']}: {why}")
    for item in rows("not_theirs"):
        f = shown.get(item.get("fact")) if isinstance(item.get("fact"), int) else None
        why = " ".join(str(item.get("why") or "").split())
        if f is None or f["id"] in used:
            entry("NOT_THEIRS", item, "names a note it was not shown, or one already changed")
        elif not why:
            entry("NOT_THEIRS", item, "gives no reason")
        elif _protected(f):
            entry("NOT_THEIRS", item, "the person wrote this note")
        else:
            used.add(f["id"])
            entry("NOT_THEIRS", item, fact=f["id"], why=why)
    for item in rows("widen"):
        f = shown.get(item.get("fact")) if isinstance(item.get("fact"), int) else None
        text = " ".join(str(item.get("text") or "").split())
        if f is None or f["id"] in used:
            entry("WIDEN", item, "names a note it was not shown, or one already changed")
        elif f["visibility"] != "team":
            entry("WIDEN", item, "a note kept to one Zip stays with that Zip")
        elif f["kind"] not in ("preference", "decision"):
            entry("WIDEN", item, "only a rule is widened")
        elif not 8 <= len(text) <= 400 or facts.secret(text):
            entry("WIDEN", item, "the new wording is not usable")
        elif facts.sensitive(text) or facts.sensitive(f["text"]) or facts._GRADE.search(f["text"]):
            entry("WIDEN", item, "an amount, a grade or a health detail stays with its Zip")
        elif _protected(f):
            entry("WIDEN", item, "the person wrote this note")
        else:
            used.add(f["id"])
            entry("WIDEN", item, fact=f["id"], text=text, why=str(item.get("why") or "a rule about the person"))
    work = work or {}
    for lid in raw.get("practice_work") if isinstance(raw.get("practice_work"), list) else []:
        if isinstance(lid, int) and lid in work:
            entry("PRACTICE_WORK", {"loop": lid}, loop=lid, bot_id=work[lid]["bot_id"], updated=work[lid]["updated_at"])
    return out


def apply_sweep(checked: list[dict], night: str, at: float) -> int:
    ""
    from . import openloops
    c = store.db()
    applied = 0
    for e in checked:

        if e["state"] == "ok" and not facts.unchanged(e["seen"]):
            e.update(state="rejected", reason="the note changed while it was being read")
        if e["state"] == "ok" and e["op"] == "PRACTICE_WORK" and not openloops.close(
                e["bot_id"], e["loop"], expected=e["updated"], note="It was a practice run."):
            e.update(state="rejected", reason="the work changed while it was being read")
        if e["state"] != "ok":
            c.execute("INSERT INTO fact_changes (bot_id, night, op, state, fact_ids, new_ids, before, detail, evidence, "
                      "reason, at) VALUES (?,?,?,'rejected','[]','[]','[]',?,'[]',?,?)",
                      (SWEEP_BOT, night, e["op"], json.dumps(e["raw"])[:4000], e["reason"], int(at)))
            continue
        if e["op"] == "MERGE":
            ids = [e["keep"], *e["copies"]]
            before = [facts.get(i) or {} for i in ids]
            newest = max(int(b.get("observed_at") or 0) for b in before)
            c.executemany("UPDATE facts SET replaced_by=? WHERE id=? AND replaced_by IS NULL",
                          [(e["keep"], i) for i in e["copies"]])
            c.execute("UPDATE facts SET observed_at=? WHERE id=?", (newest, e["keep"]))
            if e.get("text"):
                c.execute("UPDATE facts SET text=? WHERE id=?", (e["text"], e["keep"]))
            _record(c, "MERGE", night, ids, before, e["why"], e["raw"], at)
        elif e["op"] in ("RETIRE", "NOT_THEIRS"):
            f = facts.get(e["fact"]) or {}
            data = _data(f)
            if e["op"] == "NOT_THEIRS":
                data["said_by"] = "someone else"
            c.execute("UPDATE facts SET status='past', valid_until=MIN(COALESCE(valid_until, ?), ?), data=? WHERE id=?",
                      (int(at), int(at), json.dumps(data) if data else f.get("data"), e["fact"]))
            _record(c, e["op"], night, [e["fact"]], [f], e["why"], e["raw"], at)
        elif e["op"] == "WIDEN":
            f = facts.get(e["fact"]) or {}
            c.execute("UPDATE facts SET text=? WHERE id=?", (e["text"], e["fact"]))
            _record(c, "WIDEN", night, [e["fact"]], [f], e["why"], e["raw"], at)
        elif e["op"] == "PRACTICE_WORK":
            _record(c, "PRACTICE_WORK", night, [], [], "a practice or test run", e["raw"], at)
        applied += 1
    c.commit()
    return applied


def plan_of(checked: list[dict]) -> dict:
    ""

    names = {"MERGE": "merge", "RETIRE": "retire", "WIDEN": "widen", "NOT_THEIRS": "not_theirs"}
    out: dict = {name: [] for name in names.values()} | {"practice_work": [], "seen": {}, "loops": {}}
    for e in checked:
        if e["state"] == "ok" and e["op"] == "PRACTICE_WORK":
            out["practice_work"].append(e["loop"])
            out["loops"][e["loop"]] = e["updated"]
        elif e["state"] == "ok":
            out[names[e["op"]]].append(e["raw"])
            out["seen"].update(e["seen"])
    return out


def check_plan(plan: dict, at: float) -> list[dict]:
    ""

    checked = check_sweep(plan, {f["id"]: f for f in sweep_facts(at)}, {r["id"]: r for r in _open_work()})
    seen = {int(i): was for i, was in (plan.get("seen") or {}).items()}
    loops = {int(i): was for i, was in (plan.get("loops") or {}).items()}
    for e in checked:
        e["seen"] = {i: seen.get(i) for i in e["seen"]}
        if e["op"] == "PRACTICE_WORK":
            e["updated"] = loops.get(e["loop"], -1)
    return checked


async def plan_sweep(at: float) -> list[dict]:
    ""
    from . import extract, who
    rows = sweep_facts(at)
    work = {r["id"]: r for r in _open_work()}
    checked: list[dict] = []
    for n, batch in enumerate(_batches(rows)):
        loops = "\n".join(f"[{r['id']}] {r['topic']}" for r in work.values()) if n == 0 else ""
        with shortreply.reading(tuple({str(r.get('bot_id') or '') for r in batch})):
            answer = extract._json(await ask_strong(SWEEP_PROMPT.format(
                person=who.first(), today=_today(at), notes="\n".join(_sweep_line(f) for f in batch),
                loops=loops or "None.")))
        if answer is None:
            raise ValueError("the night read's answer was not JSON")
        checked += check_sweep(answer, {f["id"]: f for f in batch}, work if n == 0 else {})
    return checked


async def nightly_sweep(night: str, at: float) -> str:
    ""

    from . import consolidate, openloops
    generation = openloops._generation
    try:
        checked = await plan_sweep(at)
    except Exception:
        log.warning("the night read of the notes failed", exc_info=True)
        with store._lock:
            if generation == openloops._generation:
                consolidate._mark(SWEEP_BOT, night, "failed")
        return "failed"
    try:
        conflicts = await find_conflicts(at)
    except Exception:
        log.warning("could not compare the calendar with what the person said", exc_info=True)
        conflicts = None
    with store._lock:
        if generation != openloops._generation:
            return "dropped"
        n = apply_sweep(checked, f"sweep:{night}", at)
        if conflicts is not None:
            save_conflicts(conflicts, at)
        consolidate._mark(SWEEP_BOT, night, "done", ops=n)
    log.info("the night read of the notes: %d applied, %d rejected", n,
             sum(1 for e in checked if e["state"] != "ok"))
    return "done"




def _events(at: float) -> list[dict]:
    from . import calcache
    care = _client_care()
    return [e for e in calcache.events_between(at, at + EVENT_DAYS * 86400)
            if not care.search(str(e.get("title") or ""))]


async def find_conflicts(at: float) -> list[dict]:
    ""
    from . import extract, who
    events = _events(at)
    notes = [f for f in sweep_facts(at) if f["source"] in ("chat", "call", "app", "setup")
             and f["kind"] in ("plan", "decision", "preference", "status", "other")]
    if not events or not notes:
        return []
    by_id = {str(e["id"]): e for e in events}
    shown = {f["id"]: f for f in notes}
    with shortreply.reading(tuple({str(f.get('bot_id') or '') for f in notes})):
        answer = extract._json(await ask_strong(CONFLICT_PROMPT.format(
            person=who.first(), today=_today(at), notes="\n".join(_note_line(f) for f in notes)[:SWEEP_CHARS],
            events="\n".join(f"[{e['id']}] {datetime.fromtimestamp(e['start'], home.TZ).strftime('%a %b %-d, %-I:%M %p')}: "
                             f"{e['title']}" for e in events))))
    out = []
    for v in (answer or {}).get("conflicts") or []:
        if not isinstance(v, dict):
            continue
        e, f = by_id.get(str(v.get("event"))), shown.get(v.get("fact")) if isinstance(v.get("fact"), int) else None
        shows, said = " ".join(str(v.get("shows") or "").split()), " ".join(str(v.get("said") or "").split())
        if e and f and shows and said:
            out.append({"key": str(e["id"]), "title": e["title"], "shows": shows[:160], "said": said[:200],
                        "fact_id": f["id"], "seen": facts.revision(f)})
    return out


def save_conflicts(found: list[dict], at: float) -> None:
    ""

    c = store.db()
    offered = {r["key"]: r["offered_at"] for r in c.execute("SELECT key, offered_at FROM fact_conflicts")}
    c.execute("DELETE FROM fact_conflicts")
    c.executemany("INSERT OR REPLACE INTO fact_conflicts (source, key, title, shows, said, fact_id, seen, found_at, "
                  "offered_at) VALUES ('calendar',?,?,?,?,?,?,?,?)",
                  [(v["key"], v["title"], v["shows"], v["said"], v["fact_id"], json.dumps(v["seen"]), int(at),
                    offered.get(v["key"])) for v in found if facts.unchanged({v["fact_id"]: v["seen"]})])
    c.commit()


def _conflicts(bot_id: str | None) -> list[dict]:
    ""

    holds, now_arg = facts.live("f")
    where, args = facts._visible(bot_id)
    rows = store.db().execute(f"SELECT k.* FROM fact_conflicts k JOIN facts f ON f.id = k.fact_id WHERE {holds} AND {where} "
                              "ORDER BY k.found_at, k.key", now_arg + args).fetchall()
    return [dict(r) for r in rows if facts.unchanged({r["fact_id"]: json.loads(r["seen"])})]


def conflict_lines(bot_id: str) -> list[str]:
    ""

    from . import who
    if config.is_sandbox(bot_id):
        return []
    first = who.first()
    out = []
    for k in _conflicts(bot_id):
        line = f"- {k['title']}: the calendar still shows {k['shows']}, but {first} said {k['said']}. Go by what {first} said."
        out.append(line if k["offered_at"] else f"{line} Offer once to fix the calendar.")
    return out


def mark_offered(bot_id: str) -> None:
    ""
    if config.is_sandbox(bot_id):
        return
    where, args = facts._visible(bot_id)
    with store._lock:
        c = store.db()
        c.execute(f"UPDATE fact_conflicts SET offered_at=? WHERE offered_at IS NULL AND fact_id IN "
                  f"(SELECT id FROM facts WHERE {where})", (int(now()),) + args)
        c.commit()


def calendar_note(event: dict, bot_id: str) -> str:
    ""
    from . import who
    for k in _conflicts(bot_id):
        if k["key"] == str(event.get("id")):
            return f" ({who.first()} said {k['said']}; go by that)"
    return ""


def disputed(text: str) -> str:
    ""


    low = f" {' '.join(re.findall(r'[a-z0-9]+', (text or '').lower()))} "
    for k in _conflicts(None):
        title = " ".join(re.findall(r"[a-z0-9]+", k["title"].lower()))
        if len(title) >= 4 and f" {title} " in low:
            return k["title"]
    return ""
