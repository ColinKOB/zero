""














from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta

from . import config, factdates, facts, home, privatewords, shortreply, store

log = logging.getLogger("taffy.extract")

ENABLED = False
HAIKU_DAILY = 400
TIMEOUT_SEC = 60
ORDERS_EVERY_SEC = 30 * 60
ORDER_BODY_MAX = 6000


def signal(words: str) -> bool:
    ""



    from . import history
    w = " ".join((words or "").split())
    return 3 <= len(w.split()) and len(w) <= 4000 and not w.startswith("[") and not history.spoken_for(w)




def _budget_ok() -> bool:
    day = datetime.now(home.TZ).strftime("%Y-%m-%d")
    used = store.get_setting("facts_haiku", None) or {}
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
    store.set_setting("facts_haiku", used)
    return True


async def ask_haiku(prompt: str) -> str:
    ""
    from . import briefing
    return await briefing.ask_haiku(prompt, timeout=TIMEOUT_SEC)


def _json(text: str) -> dict | None:
    t = (text or "").strip()
    t = re.sub(r"^```(json)?|```$", "", t, flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        return None
    try:
        out = json.loads(t[i:j + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None




PROMPT = """You keep the shared notes a team of assistants has about {person}. Read what {person} just said to \
{zip} and decide whether it states a lasting fact worth remembering weeks from now, so {person} never has to say it \
again: a preference or usual (brand, product, size, flavor, store and its location, how {person} likes things \
done), a place, a purchase, a supply {person} keeps, a person in {person}'s life, a plan, a decision, or a date \
that matters. A correction ("actually", "not anymore", "switched") replaces the fact it corrects.

Rules:
- Only {person}'s own words count. {zip}'s message is context only: never save what {zip} said, suggested or did.
- If {person} did not write this message, answer {{"author": "other", "facts": []}}: another assistant or a person \
writing for {person}, a test, or a message that speaks of {person} by name. A practice run or made-up data states \
nothing about {person}, so it has no facts either.
- Never save passwords, codes, account or card numbers, sign-in details, instructions for a task ("email Jordan"), \
one-off requests, or moods.
- A dated plan is lasting until it passes, even one for today or the next hour: a trip (when {person} leaves, where \
{person} stays each night, when {person} is back), an appointment, or a deadline {person} committed to. "We're \
camping Friday night but not the other two" and "heading out in 10 minutes" are plans. Save each with its real dates, \
say plainly what is still undecided, and put the last day it holds in "until".
- "until": the last day the fact holds, as YYYY-MM-DD (or "YYYY-MM-DD HH:MM" when it ends at a time, like "home by \
noon"), only when {person}'s words date it. null for anything lasting.
- "quote": the few words of {person}'s message the fact rests on, copied exactly.
- One sentence per fact, about {person} in the third person, with every specific detail (brand, size, store and \
location, how they buy it, dates). Today is {today}; write real dates like "October 7, 2026", never "today". \
Birthdays and other yearly dates have no year.
- "subject" is the plain thing the fact is about, one or two common words ("coffee", "dog food", "contact lenses", \
"accountant", "job search"). When a current fact below is about the same thing, use its subject exactly.
- If a current fact below already says it, leave it out. If {person}'s words change or correct a current fact, \
give that fact's number in "replaces" and write the whole new fact.
- "confidence": "said" when {person} said it plainly, "inferred" when you had to read between the lines.
- "private": true only for health conditions, medications and account balances. Where {person} shops and what \
{person} buys are never private.
- When {person} says {person} finished or quit something ("I'm done with BIO 110", "I quit the gym") or not to bring \
something up again, save it as a status and list in "closes" each thing closed, in words from the message ("the old \
apartment"). Leave "closes" empty otherwise.
- At most three facts. Most messages have none.

Current facts that may be related:
{candidates}

{zip}'s message just before (context only): {before}

{person} said: {words}

{zip} answered (context only): {reply}

Answer with JSON only, no other text:
{{"facts": [{{"subject": "one to three words", \
"kind": "preference|place|purchase|supply|person|plan|decision|status|other", \
"text": "one sentence", "until": null, "quote": "words from the message", "replaces": null, "confidence": "said", \
"private": false, "closes": []}}]}}
Use {{"facts": []}} when there is nothing lasting."""

_running: set[asyncio.Task] = set()


def after_turn(bot_id: str, words: str, reply: str = "", session_id: str | None = None, before: str = "",
               source: str = "chat") -> asyncio.Task | None:
    ""

    try:
        if not ENABLED or not signal(words):
            return None
        task = asyncio.get_running_loop().create_task(_extract(bot_id, words, reply, session_id, before, source))
    except Exception:
        log.warning("could not start the fact extractor", exc_info=True)
        return None
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


def _candidates(bot_id: str, words: str, reply: str = "", session_id: str | None = None) -> list[dict]:
    ""


    found = {f["id"]: f for f in facts.search(words, bot_id, limit=8)}
    for f in facts.search(reply, bot_id, limit=4) if reply else []:
        found.setdefault(f["id"], f)
    c = facts._db()
    held, now_arg = facts.live("f")
    holds, _ = facts.live()
    if session_id:
        for f in c.execute(f"SELECT f.* FROM facts f LEFT JOIN fact_shown s ON s.fact_id = f.id AND s.session_id = ? "
                           f"WHERE {held} AND (f.session_id = ? OR s.fact_id IS NOT NULL) "
                           "AND f.kind NOT IN ('other') ORDER BY f.id DESC LIMIT 6", (session_id,) + now_arg + (session_id,)):
            found.setdefault(f["id"], dict(f))
    subjects = {f["subject"] for f in found.values() if f["subject"]}
    for s in subjects:
        for f in c.execute(f"SELECT * FROM facts WHERE {holds} AND subject=? ORDER BY id DESC LIMIT 5", now_arg + (s,)):
            found.setdefault(f["id"], dict(f))
    where, args = facts._visible(bot_id)
    for f in c.execute(f"SELECT * FROM facts WHERE {holds} AND {where} AND kind IN ('preference', "
                       f"'place', 'supply', 'purchase') ORDER BY observed_at DESC, id DESC LIMIT 4", now_arg + args):
        found.setdefault(f["id"], dict(f))
    return list(found.values())[:15]


_reading: dict[tuple[int, str], asyncio.Lock] = {}


async def _extract(bot_id: str, words: str, reply: str, session_id: str | None, before: str, source: str) -> list[int]:
    ""

    lock = _reading.setdefault((id(asyncio.get_running_loop()), bot_id), asyncio.Lock())
    async with lock:
        return await _read_words(bot_id, words, reply, session_id, before, source)


async def _read_words(bot_id: str, words: str, reply: str, session_id: str | None, before: str,
                      source: str) -> list[int]:
    from . import who
    cands = _candidates(bot_id, words, reply, session_id)
    if not _budget_ok():
        log.info("fact extractor skipped: today's budget is used")
        return []



    shown = privatewords.leave_out(words[:2000])
    if words.strip() and not shown.strip():
        return []
    zip_name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "the assistant"
    prompt = PROMPT.format(
        person=who.first(), zip=zip_name, today=datetime.now(home.TZ).strftime("%A, %B %-d, %Y"),
        candidates="\n".join(privatewords.keep(f"[{f['id']}] ({f['subject'] or 'no subject'}, {f['kind']}) {f['text']}"
                                               for f in cands)) or "None.",
        before=privatewords.leave_out((before or "None.")[:600]) or "None.", words=shown,
        reply=privatewords.leave_out((reply or "")[:800]))
    began = time.monotonic()
    try:
        with shortreply.reading((bot_id,)):
            out = _json(await ask_haiku(prompt))
    except Exception:
        log.warning("fact extractor call failed", exc_info=True)
        return []
    if isinstance(out, dict) and out.get("author") == "other":
        log.info("fact extractor for %s: someone else wrote this message", bot_id)
        return []
    said = time.time()
    out = check_until(out, _dates_from(said, words))
    for item in (out or {}).get("facts") or []:
        if isinstance(item, dict):
            item["evidence"] = {"session_id": session_id, "at": int(said), "quote": _quote(item.get("quote"), words)}
    from . import factkeeper
    out, compared = await factkeeper.settle(out, bot_id)
    saved = save_answer(out, bot_id, session_id, source, {**compared, **facts.revisions(cands)}, own=words)
    log.info("fact extractor for %s: %d saved in %.1f s", bot_id, len(saved), time.monotonic() - began)
    return saved


def _quote(quote, words: str) -> str:
    ""
    q = " ".join(str(quote or "").split())
    flat = " ".join((words or "").split())
    return q if q and q.lower() in flat.lower() else flat[:160]


def check_until(out: dict | None, allowed: set[tuple[int, int]]) -> dict | None:
    ""

    items = (out or {}).get("facts") if isinstance(out, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("until"):
            continue
        at = factdates.parse_until(item["until"], time.time())
        if at is None or factdates.day_of(at) not in allowed:
            log.info("extracted end dropped: its day is not one the message gives")
            item["until"] = None
    return out


def save_answer(out: dict | None, bot_id: str, session_id: str | None, source: str, known: dict[int, list],
                source_ref: str | None = None, observed_at: float | None = None, most: int = 3,
                own: str = "") -> list[int]:
    ""



    saved = []
    items = (out or {}).get("facts") if isinstance(out, dict) else None
    for item in (items or [])[:most]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text or facts.secret(text):
            continue
        if not facts.owned_by_words(text, item.get("quote"), own):
            log.info("extracted fact not saved: the person's own words do not say they will do it themselves")
            continue
        given = item.get("replaces")
        rep = [int(r) for r in (given if isinstance(given, list) else [given])
               if (isinstance(r, (int, float)) or str(r or "").isdigit()) and int(r) in known]
        conf = str(item.get("confidence") or "said")
        until = factdates.parse_until(item.get("until"), observed_at or time.time())
        data = item.get("data") if isinstance(item.get("data"), dict) else None

        closed = [" ".join(x.split())[:60] for x in item.get("closes") or [] if isinstance(x, str) and x.strip()] \
            if isinstance(item.get("closes"), list) else []
        if closed and item.get("kind") in ("decision", "status"):
            data = {**(data or {}), "closes": closed[:5]}
        try:
            fid, _ = facts.save("hub" if bot_id not in config.BOTS_BY_ID else bot_id, text,
                                subject=str(item.get("subject") or ""), kind=str(item.get("kind") or "other"),
                                replaces=rep, session_id=session_id, source=source, source_ref=source_ref,
                                confidence=conf if conf in ("said", "inferred") else "said",

                                private=bool(item.get("private")) and facts.sensitive(text)
                                or bool(item.get("keep_private")),
                                data=data, observed_at=observed_at, limits=False, scope=item.get("scope"),
                                merge=not item.get("compared"), seen={r: known[r] for r in rep},
                                until=until, evidence=item.get("evidence") if isinstance(
                                    item.get("evidence"), dict) else None)
        except facts.FactError as e:
            log.info("extracted fact not saved: %s", e)
            continue
        if fid:
            saved.append(fid)
    return saved


async def after_call(bot_id: str, person_lines: list[str], call_id: str = "") -> list[int]:
    ""
    words = " ".join(person_lines)[-3000:]
    if not ENABLED or not signal(words):
        return []
    return await _extract(bot_id, words, "", None, "", "call")








SUMMARY_SEEN = "facts_summaries_seen"
SUMMARY_EVERY_SEC = 5 * 60
SUMMARY_MAX_AGE = 2 * 86400
SUMMARY_FACTS_MAX = 5

SUMMARY_PROMPT = """You keep the shared notes a team of assistants has about {person}. Below is {zip}'s own summary \
of a long chat with {person}. It was written {written}, which is not when the things in it were said. Today is \
{today}. List the dated plans and decisions in it that still matter: trips (when {person} leaves, where {person} \
stays each night, when {person} is back), appointments, deadlines, decisions {person} made, and what {person} said \
is still undecided.

Rules:
- Only what {person} said, decided or agreed to. Never {zip}'s own suggestions, options {person} did not pick, or \
work {zip} did.
- A decision, a preference, a status, or a fact that {person} will do something themselves needs "quote": the words \
of {person}'s own message below that say so, copied exactly. {zip}'s summary saying it is not enough. Without them, \
leave it out; the nightly notes pass reads the messages themselves.
- One sentence per fact, about {person} in the third person.
- Dates: words like "tonight", "tomorrow", "yesterday", "Friday" or "in 10 minutes" count from when {person} said \
them, never from when the summary was written. Find when in {person}'s own messages below, which run from {first} \
to {last}. Write a date (with its weekday, "Thursday, October 22, 2026") only when the summary states that date or \
one of those messages pins it down, and then put that message's time in "said_at" exactly as it is written below. \
Otherwise write the fact without a date, or leave it out.
- "until": the last day a plan or decision holds, as YYYY-MM-DD (or "YYYY-MM-DD HH:MM" when it ends at a time), only \
from a date you were allowed to write. null for anything lasting.
- Leave out plans that ended before today, and anything a current fact below already says. If the summary changes a \
current fact, give that fact's number in "replaces" and write the whole new fact.
- A current fact marked "newer" was said after everything in the summary, so it wins: never replace it, and leave out \
anything in the summary about the same thing.
- Never save passwords, codes, account or card numbers, sign-in details, or health details.
- "subject" is the plain thing the fact is about, one to three words ("maine trip", "ups shift"). When a current \
fact below is about the same thing, use its subject exactly.
- When {person} said {person} finished or quit something ("I'm done with BIO 110") or not to bring something up \
again, it is a status: list in "closes" each thing closed, in words from {person}'s message ("the old apartment"). \
Leave "closes" empty otherwise.
- At most {most} facts. Most summaries have one or two.

Current facts that may be related:
{candidates}

The summary:
{summary}

{person}'s own messages, with when each was said:
{said}

Answer with JSON only, no other text:
{{"facts": [{{"subject": "one to three words", "kind": "plan|decision|status|person|other", "text": "one sentence", \
"said_at": "the time of the message that dates it, as written above, or null", "until": null, "quote": null, \
"replaces": null, "confidence": "said", "closes": []}}]}}
Use {{"facts": []}} when there is nothing dated or decided."""
SAID_CHARS = 8000

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_DAY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?!\d)", re.I)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _month_days(text: str) -> set[tuple[int, int]]:
    return {(_MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2))) for m in _MONTH_DAY.finditer(text or "")}


def _dates_from(ts: float, words: str) -> set[tuple[int, int]]:
    ""

    day = datetime.fromtimestamp(ts, home.TZ).date()
    low = (words or "").lower()
    near = [day]
    if "tomorrow" in low:
        near.append(day + timedelta(days=1))
    if "yesterday" in low:
        near.append(day - timedelta(days=1))
    if "weekend" in low:
        near += [day + timedelta(days=(5 - day.weekday()) % 7), day + timedelta(days=(6 - day.weekday()) % 7)]
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b{name}", low):
            ahead = (i - day.weekday()) % 7
            near += [day + timedelta(days=ahead), day - timedelta(days=(7 - ahead) % 7 or 7)]
    return {(d.month, d.day) for d in near} | _month_days(words)


_DATE_WORDS = set(_WEEKDAYS) | {m for m in ("january february march april may june july august september october "
                                            "november december").split()} | set(_MONTHS)


def _stated_for(text: str, summary: str) -> set[tuple[int, int]]:
    ""


    want = {w for w in facts._words(text) - _DATE_WORDS - facts._person_words() if len(w) > 3 and not w.isdigit()}
    out = set()
    for line in (summary or "").split("\n"):
        days = _month_days(line)
        if days and want and len(facts._words(line) & want) >= min(2, len(want)):
            out |= days
    return out


def check_dates(out: dict | None, said: list[tuple[float, str]], summary: str) -> dict | None:
    ""

    items = (out or {}).get("facts") if isinstance(out, dict) else None
    if not isinstance(items, list):
        return out
    from . import recentchat
    by_time: dict[str, list[tuple[float, str]]] = {}
    for ts, words in said:
        by_time.setdefault(recentchat.when(ts), []).append((ts, words))
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dates = _month_days(str(item.get("text") or ""))
        allowed = _stated_for(str(item.get("text") or ""), summary)
        cited = by_time.get(" ".join(str(item.get("said_at") or "").split()), [])
        for ts, words in cited:
            allowed |= _dates_from(ts, words)
        if dates - allowed:
            log.info("summary fact dropped: its date is not one the summary or its message gives")
            continue
        check_until({"facts": [item]}, allowed)
        if cited:
            item["evidence"] = {"at": int(cited[0][0]), "quote": cited[0][1][:160]}
        kept.append(item)
    return {**out, "facts": kept}


SUMMARY_TRIES = 3
_summary_failures: dict[tuple[str, float], int] = {}




SUMMARY_READ_SECTIONS = ("User Messages", "Active State", "Critical Context", "Key Decisions",
                         "Decisions & Standing Preferences", "Constraints & Preferences")
SUMMARY_READ_MAX = 16_000
SUMMARY_READ_SECTION_MAX = 5_000


def _summary_read(bot_id: str, at: float, generation: int) -> None:
    ""
    from . import openloops
    with store._lock:
        if generation != openloops._generation:
            return
        seen = store.get_setting(SUMMARY_SEEN, None)
        store.set_setting(SUMMARY_SEEN, {**(seen if isinstance(seen, dict) else {}), bot_id: at})
    _summary_failures.pop((bot_id, at), None)


CARRIED = 0.5

_SPECIFIC = re.compile(r"(?<![.!?:\"“]\s)(?<![\"“‘])(?<!^)\b[A-Z][\w'&-]*|\b\w*\d[\w:,./-]*")


def _carried(new: str, old: str, names: set[str]) -> bool:
    ""



    has = set(re.findall(r"[a-z0-9]+", old.lower())) | names
    return facts._kept(new, old) >= CARRIED and all(
        w in has for s in _SPECIFIC.findall(new) for w in re.findall(r"[a-z0-9]+", s.lower()))


def not_read_before(out: dict, before: float, held: list[dict]) -> dict:
    ""






    cited: dict[int, list[str]] = {}
    for f in held:
        try:
            cited.setdefault(int(json.loads(f.get("evidence") or "null")["at"]), []).append(f["text"])
        except (TypeError, ValueError, KeyError):
            continue
    names = facts._person_words() | {w for b in config.BOTS for w in re.findall(r"[a-z]+", b.name.lower())}
    kept = []
    for item in (out or {}).get("facts") or []:
        evidence = item.get("evidence") if isinstance(item, dict) else None
        said = evidence.get("at") if isinstance(evidence, dict) else None
        if before and isinstance(said, (int, float)) and said < before and any(
                _carried(str(item.get("text") or ""), old, names) for old in cited.get(int(said), ())):
            log.info("summary fact dropped: a current fact already gives it from the same message")
            continue
        kept.append(item)
    return {**(out or {}), "facts": kept}


def not_older(out: dict, newer: list[dict]) -> dict:
    ""

    ids = {f["id"] for f in newer}
    subjects = {f["subject"] for f in newer if f.get("subject")}
    kept = []
    for item in (out or {}).get("facts") or []:
        rep = item.get("replaces") if isinstance(item, dict) else None
        if isinstance(item, dict) and (str(rep) in {str(i) for i in ids}
                                       or facts._norm_subject(str(item.get("subject") or "")) in subjects):
            log.info("summary fact dropped: a newer fact already covers %r", item.get("subject"))
            continue
        kept.append(item)
    return {**(out or {}), "facts": kept}


def quoted_rules(out: dict | None, own: str) -> dict:
    ""

    items = (out or {}).get("facts") if isinstance(out, dict) else None
    kept = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("kind") in facts.RULES \
                and not facts.quoted(str(item.get("text") or ""), item.get("quote"), own):
            log.info("summary fact not saved: it does not quote the person's own words")
            continue
        kept.append(item)
    return {**(out or {}), "facts": kept}


async def after_compaction(bot_id: str, now: float | None = None) -> list[int]:
    ""
    from . import openloops, recentchat, who


    generation = openloops._generation
    now = time.time() if now is None else now
    found = await asyncio.to_thread(recentchat.latest_summary, bot_id)
    seen = store.get_setting(SUMMARY_SEEN, None)
    seen = seen if isinstance(seen, dict) else {}

    erased = float(store.get_setting("openloops_waits_erased_at", 0) or 0)
    if not found or found[0] <= max(float(seen.get(bot_id) or 0), erased):
        return []
    at, raw = found
    text = recentchat.summary_text(raw, SUMMARY_READ_MAX, SUMMARY_READ_SECTIONS, SUMMARY_READ_SECTION_MAX)
    fresh = bool(text) and now - at <= SUMMARY_MAX_AGE
    if not fresh:
        _summary_read(bot_id, at, generation)
        return []
    if not _budget_ok():
        return []
    where, args = facts._visible(bot_id)
    holds, now_arg = facts.live()
    cands = [dict(r) for r in facts._db().execute(
        f"SELECT * FROM facts WHERE {holds} AND {where} AND kind IN ('plan', 'decision', 'status') "
        f"ORDER BY id DESC LIMIT 15", now_arg + args)]



    held = facts.current(bot_id)
    newer = [f for f in held if (f.get("observed_at") or 0) > at]
    newer_ids = {f["id"] for f in newer}
    zip_name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "the assistant"
    said = await asyncio.to_thread(recentchat.said_before, bot_id, at)
    lines, used = [], 0
    for ts, words in reversed(said):
        line = f"- {recentchat.when(ts)}: {words[:400]}"
        if used + len(line) > SAID_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    lines.reverse()
    kept = said[len(said) - len(lines):]
    own = "\n".join(w for _, w in kept)

    prompt = SUMMARY_PROMPT.format(
        person=who.first(), zip=zip_name, most=SUMMARY_FACTS_MAX,
        written=datetime.fromtimestamp(at, home.TZ).strftime("%A, %B %-d, %Y at %-I:%M %p"),
        today=datetime.fromtimestamp(now, home.TZ).strftime("%A, %B %-d, %Y"),
        candidates="\n".join(privatewords.keep(f"[{f['id']}] ({f['subject'] or 'no subject'}, {f['kind']}"
                                               f"{', newer' if f['id'] in newer_ids else ''}) {f['text']}"
                                               for f in cands)) or "None.",
        summary=privatewords.leave_out(text), said="\n".join(privatewords.keep(lines)) or "(none saved)",
        first=recentchat.when(kept[0][0]) if kept else "(none)", last=recentchat.when(kept[-1][0]) if kept else "(none)")
    try:
        with shortreply.reading((bot_id,)):
            answer = _json(await ask_haiku(prompt))
        if answer is None:
            raise ValueError("the answer was not JSON")
        out = not_older(not_read_before(check_dates(answer, kept, text), float(seen.get(bot_id) or 0), held), newer)
        out = quoted_rules(out, own)
        from . import factkeeper
        out, compared = await factkeeper.settle(out, bot_id, fresh=False, said_at=at)
    except Exception:
        log.warning("compaction summary read failed for %s", bot_id, exc_info=True)


        _summary_failures[(bot_id, at)] = _summary_failures.get((bot_id, at), 0) + 1
        if _summary_failures[(bot_id, at)] >= SUMMARY_TRIES:
            _summary_read(bot_id, at, generation)
        return []
    with store._lock:
        if generation != openloops._generation:
            log.info("compaction summary for %s dropped: everything was deleted while it was read", bot_id)
            return []
        _summary_read(bot_id, at, generation)
        saved = save_answer(out, bot_id, store.session_for(bot_id), "chat", {**compared, **facts.revisions(cands)},
                            source_ref=f"summary:{int(at)}", observed_at=at, most=SUMMARY_FACTS_MAX,
                            own=own)
    log.info("compaction summary for %s: %d saved", bot_id, len(saved))
    return saved


async def watch_summaries() -> None:
    ""
    await asyncio.sleep(120)
    if not ENABLED:
        return
    while True:
        for bot_id in list(config.BOTS_BY_ID):
            try:
                await after_compaction(bot_id)
            except Exception:
                log.warning("compaction summary check failed for %s", bot_id, exc_info=True)
        await asyncio.sleep(SUMMARY_EVERY_SEC)




_ORDER_SUBJECT = re.compile(r"\b(your order|^your\b.{0,40}\border\b|order (confirmation|confirmed|received|#|number|no\.?)|"
                            r"thanks for (your )?(order|shopping|purchase)|thank you for (your )?(order|purchase)|"
                            r"order .{0,40} (has been )?(placed|confirmed)|receipt for|your receipt|purchase "
                            r"confirmation|we('ve| have) received your order|delivery order)", re.I)
_NOT_ORDER = re.compile(r"\b(shipped|on its way|out for delivery|delivered|return|refund|cancel)", re.I)

ORDER_PROMPT = """Read this order confirmation email {person} received and list what was bought. Today is {today}.

Answer with JSON only:
{{"order": true, "store": "store name", "ordered_on": "YYYY-MM-DD", "order_id": "as printed or null", \
"items": [{{"name": "product as named, with brand", "subject": "what it is in one or two common words, e.g. \
coffee, dog food, contact lenses, oat milk", "size": "e.g. 12 oz or null", "quantity": 1, \
"price": "e.g. $8.99 or null", "every": "how often it repeats when the email says (e.g. 6 weeks), else null"}}], \
"total": "e.g. $23.10 or null"}}
If it is not a confirmation of something {person} bought (a shipping notice, an ad, a newsletter, a return), answer \
{{"order": false}}.

From: {sender}
Subject: {subject}
Date: {date}

{body}"""


def is_order(subject: str) -> bool:
    return bool(_ORDER_SUBJECT.search(subject or "")) and not _NOT_ORDER.search(subject or "")


def _seen() -> list[str]:
    v = store.get_setting("facts_orders_seen", None)
    return list(v) if isinstance(v, list) else []


def _mark_seen(key: str) -> None:
    seen = _seen()
    if key not in seen:
        seen.append(key)
    store.set_setting("facts_orders_seen", seen[-500:])


def purchase_facts(order: dict, person: str) -> list[tuple[str, str, dict]]:
    ""
    store_name = " ".join(str(order.get("store") or "").split())[:60]
    day = str(order.get("ordered_on") or "")
    try:
        when = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=home.TZ)
        day_words = when.strftime("%b %-d, %Y")
    except ValueError:
        when, day_words = None, ""
    out = []
    for it in (order.get("items") or [])[:12]:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        name = " ".join(str(it["name"]).split())[:120]
        size = " ".join(str(it.get("size") or "").split())[:30]
        try:
            qty = max(1, int(float(it.get("quantity") or 1)))
        except (TypeError, ValueError):
            qty = 1
        price = " ".join(str(it.get("price") or "").split())[:20]
        what = f"{qty} x {name}" + (f" ({size})" if size and size.lower() not in name.lower() else "")
        text = (f"{person} bought {what}" + (f" from {store_name}" if store_name else "")
                + (f" on {day_words}" if day_words else "") + (f" for {price}" if price else "") + ".")
        every = " ".join(str(it.get("every") or "").split())[:30]
        data = {"item": name, "size": size or None, "quantity": qty, "price": price or None, "store": store_name or None,
                "ordered_at": when.timestamp() if when else None, "order_id": order.get("order_id"),
                "every": every if every and every.lower() != "null" else None}
        given = facts._norm_subject(str(it.get("subject") or ""))
        subject = given if given and given != "null" else _item_subject(name)
        out.append((subject, text, data))
    return out


_SUBJECT_WORDS = re.compile(r"[a-z]+")
_GENERIC = {"the", "and", "with", "for", "pack", "count", "size", "original", "new", "classic", "organic", "whole",
            "ground", "bag", "box", "oz", "lb", "of", "in", "a", "x"}


def _item_subject(name: str) -> str:
    ""
    ws = [w for w in _SUBJECT_WORDS.findall(name.lower()) if w not in _GENERIC and len(w) > 2]
    return " ".join(ws[-2:]) if ws else name.lower()[:30]


async def read_order(account: str, e: dict) -> list[int]:
    ""
    from . import mail, who
    key = f"{account}:{e['folder']}:{e['id']}"
    if key in _seen():
        return []
    try:
        body = await asyncio.to_thread(mail.read, account, e["folder"], e["id"])
    except Exception as err:
        log.info("order email not read: %s", err)
        return []
    if not _budget_ok():
        return []
    prompt = ORDER_PROMPT.format(person=who.first(), today=datetime.now(home.TZ).strftime("%B %-d, %Y"),
                                 sender=f"{e.get('from_name') or ''} <{e.get('from_addr') or ''}>",
                                 subject=e.get("subject") or "",
                                 date=datetime.fromtimestamp(e["date"], home.TZ).strftime("%Y-%m-%d"),
                                 body=body[:ORDER_BODY_MAX])
    try:
        with shortreply.reading((home.mail_owner(account),)):
            out = _json(await ask_haiku(prompt))
    except Exception:
        log.warning("order email read failed", exc_info=True)
        return []
    _mark_seen(key)
    if not out or not out.get("order"):
        return []
    if not out.get("ordered_on"):
        out["ordered_on"] = datetime.fromtimestamp(e["date"], home.TZ).strftime("%Y-%m-%d")
    saved = []
    for subject, text, data in purchase_facts(out, who.first()):
        try:
            fid, _ = facts.save("hub", text, subject=subject, kind="purchase", source="mail", source_ref=key,
                                confidence="receipt", data=data, observed_at=data.get("ordered_at") or e["date"],
                                limits=False)
        except facts.FactError as err:
            log.info("purchase fact not saved: %s", err)
            continue
        if fid:
            saved.append(fid)
    log.info("order email %s: %d purchase facts", key, len(saved))
    return saved


async def check_orders() -> list[int]:
    ""
    from . import mail
    saved = []
    for account in list(mail.ACCOUNTS):
        try:
            items = await asyncio.to_thread(mail.envelopes, account, "INBOX", 50)
        except Exception as err:
            log.info("mail not read for orders: %s", err)
            continue
        for e in sorted(items, key=lambda x: x["date"]):
            if time.time() - e["date"] > 60 * 86400 or not is_order(e.get("subject") or ""):
                continue
            saved += await read_order(account, e)
    return saved


async def watch_orders() -> None:
    ""
    await asyncio.sleep(90)
    if not ENABLED:
        return
    while True:
        try:
            await check_orders()
        except Exception:
            log.warning("order email check failed", exc_info=True)
        await asyncio.sleep(ORDERS_EVERY_SEC)


def save_bookings(e: dict, raw: str) -> list[int]:
    ""
    from . import bookings
    saved = []
    for b in bookings.parse(raw, e):
        identity = bookings.key(e["account"], b.id)
        reservation = bookings.key(e["account"], b.reservation)
        siblings = [dict(row) for row in facts._db().execute(
            "SELECT * FROM facts WHERE source='mail' AND kind='plan' AND replaced_by IS NULL "
            "AND subject LIKE 'booking %'") if json.loads(row["data"] or "{}").get("reservation") == reservation]
        if any(json.loads(row["data"])["cancelled"] and float(row["observed_at"]) >= e["date"] for row in siblings):
            continue
        old = []
        for row in facts._db().execute("SELECT * FROM facts WHERE source='mail' AND kind='plan' "
                                      "AND replaced_by IS NULL AND subject=?", ("booking " + identity,)):
            old.append(dict(row))

        if b.cancelled and b.id == b.reservation:
            for row in siblings:
                if row["id"] not in {
                        r["id"] for r in old}:
                    old.append(dict(row))
        targets = old or [None]
        for previous in targets:
            if previous and float(previous["observed_at"]) > e["date"]:
                continue
            data = {**b.data(), "id": identity, "reservation": reservation, "account": e["account"]}
            if previous:
                data["id"] = json.loads(previous["data"])["id"]
            if previous and json.loads(previous["data"]) == data:
                with store._lock:
                    facts._db().execute("UPDATE facts SET observed_at=MAX(observed_at, ?) WHERE id=?",
                                        (e["date"], previous["id"]))
                    facts._db().commit()
                continue
            start = datetime.fromtimestamp(b.start, home.TZ).strftime("%b %-d, %Y at %-I:%M %p %Z")
            end = datetime.fromtimestamp(b.end, home.TZ).strftime("%b %-d, %Y at %-I:%M %p %Z")
            text = (f"{b.title} is cancelled." if b.cancelled else
                    f"{b.title} is booked from {start} to {end}" + (f" at {b.place}" if b.place else "") + ".")
            fid, _ = facts.save("hub", text, subject="booking " + data["id"], kind="plan", source="mail",
                                source_ref=f"{e['account']}:{e['folder']}:{e['id']}:{data['id']}", confidence="receipt",
                                data=data, observed_at=e["date"], replaces=previous["id"] if previous else None,
                                limits=False)
            saved.append(fid)
    return saved
