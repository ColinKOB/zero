""


















import hashlib
import re
from datetime import datetime, timedelta

from . import calcache, config, home, mail, notices, who
from . import noticers as base
from .notices import Candidate, Option
P = notices.plain

STOP = {"with", "meeting", "call", "about", "from", "this", "that", "your", "into", "work", "time", "block", "prep",
        "class", "week", "today", "tomorrow", "the", "and", "for", "zoom", "google", "meet", "appointment",
        "advising", "session", "review", "check", "due", "paper", "assignment", "deadline", "submit", "form"}
COURSE = re.compile(r"\b[A-Z]{2,4}[ -]?\d{3}\b")
SCHOOL = re.compile(r"\b(class|course|assignment|homework|paper|essay|quiz|exam|reading|discussion post|"
                    r"form [a-z]\b|writing lab|syllabus|module|chapter|lab report|presentation|coursework)\b", re.I)
DUE = re.compile(r"\b(due|deadline|submit|turn in|hand in)\b", re.I)
TIME = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*(a\.?m\.?|p\.?m\.?)", re.I)
MEETING = re.compile(r"\b(meeting|call|interview|appointment|advising|consult|consultation|1:1|one on one|demo|"
                     r"review with|check-in|catch up|coffee with|meet with|(?:breakfast|lunch|dinner|drinks) with)\b",
                     re.I)


def _now() -> float:
    return notices.now()


NUMBERED = re.compile(r"\b(unit|form|module|chapter|week|lesson|part|quiz|exam)\s+([a-z0-9]{1,2})\b")


def words(text: str) -> set[str]:
    ""

    t = NUMBERED.sub(r"\1\2", (text or "").lower())
    own = {w.lower() for w in who.full().split()}
    return {w for w in re.findall(r"[a-z][a-z0-9]{3,}", t) if w not in STOP and w not in own}


def numbered(text: str) -> set[str]:
    ""
    return {f"{a}{b}" for a, b in NUMBERED.findall((text or "").lower())}


def _items(text: str) -> set[str]:
    ""
    return {w for w in numbered(text) if not w.startswith(("unit", "week", "module", "chapter", "lesson", "part"))}


def courses(text: str) -> set[str]:
    return {re.sub(r"[ -]", "", c.upper()) for c in COURSE.findall(text or "")}


def alike(a: str, b: str) -> bool:
    ""
    shared = words(a) & words(b)
    return len(shared) >= 2 or (bool(courses(a) & courses(b)) and len(shared) >= 1)


def clock(ts: float) -> str:
    ""
    return notices.clock(ts).replace(":00 ", " ")


def when(ts: float, now: float | None = None) -> str:
    ""
    return f"{notices.day(ts, now)} at {clock(ts)}"


def when_full(ts: float) -> str:
    return f"{notices._et(ts).strftime('%A, %b %-d')} at {clock(ts)}"


def end(sentence: str) -> str:
    ""

    return sentence if sentence.endswith(".") else sentence + "."


def short(title: str, n: int = 40) -> str:
    t = " ".join((title or "").split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0]


def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def timed(e: dict) -> bool:
    return not e.get("all_day") and not calcache.declined(e)





SELF_BLOCK = re.compile(r"^\s*[A-Z][\w&/+ ]{0,24}:\s|^\s*(routine|focus|sleep|wind down|downtime|free time|"
                        r"buffer|hold|tentative|block|reminder|to ?do|travel|commute|lunch|breakfast|dinner|chores|"
                        r"gym|workout|rest)\b", re.I)
FLEX_WORDS = re.compile(r"\b(routine|sleep|wind down|downtime|focus|free time|buffer|get ready|decompress|chores|"
                        r"shower|breakfast|lunch|dinner|tidy|rest)\b", re.I)
LMS = re.compile(r"\b(canvas|blackboard|moodle|brightspace|d2l|schoology|assignments?|due dates?|deadlines?)\b",
                 re.I)
DUE_SEC = 15 * 60


def deadline_like(e: dict) -> bool:
    ""
    return (e["end"] - e["start"] <= DUE_SEC or bool(DUE.search(e["title"]))
            or bool(LMS.search(e.get("calendar") or "")))


def free(e: dict) -> bool:
    ""
    return bool(e.get("free")) or str(e.get("transparency") or e.get("show_as") or "").lower() in (
        "transparent", "free")


def commitment(e: dict) -> bool:
    ""

    if not timed(e) or free(e) or deadline_like(e):
        return False
    people, place = calcache.outside_people(e), calcache.physical_place(e)
    if people:
        return True
    if SELF_BLOCK.search(e["title"]) or FLEX_WORDS.search(e["title"]):
        return bool(place) and not FLEX_WORDS.search(e["title"])
    if place:
        return True
    return calcache.busy_kind(e) in ("shift", "class", "church", "meeting") or bool(MEETING.search(e["title"]))




DEADLINE_HOURS = 48


def _due_from_note(note: str, ref: float) -> list[tuple[float, str]]:
    ""

    out = []
    for sentence in re.split(r"(?<=[.;\n])\s+", note):
        m = DUE.search(sentence)
        if not m:
            continue
        tm = TIME.search(sentence)
        for d in base.dates_in(sentence, ref):
            if tm:
                h = int(tm.group(1)) % 12 + (12 if tm.group(3).lower().startswith("p") else 0)
                due = d + h * 3600 + int(tm.group(2) or 0) * 60
            else:
                due = d + 23 * 3600 + 55 * 60
            thing = sentence[:m.start()].strip(" -:*") or sentence[m.end():].strip(" -:*")
            thing = re.sub(r"\s+(is|are|was|will be|and|to)$", "", " ".join(thing.split()))
            thing = re.sub(rf"^({re.escape(who.first())}|he|she|they|you)\s+(must|needs to|has to|should|will)\s+",
                           "", thing, flags=re.I)
            out.append((due, short(thing, 60) or "Schoolwork"))
    return out


def _site_items(src: base.Sources, start: float, end: float,
                known: list[tuple[str, float]]) -> list[tuple[str, float, dict]]:
    ""








    from . import settled
    out = []
    for d in src.course_site():
        due = d.get("due")
        if d.get("dated_by") not in ("activity", "unit", "mail", "syllabus", "checked") or not due or not start < due <= end:
            continue
        if settled.deadline_settled(d):
            continue
        name = f"{d['course']} {d['title'].split(':', 1)[0] if d.get('module') == 'unit' else d['title']}"
        own = [name, d["title"], *(d.get("exams") or [])]
        if any(abs(due - other) < 6 * 3600 and any(alike(n, o) for o in own) for n, other in known):
            continue
        out.append((name, due, d))
    return out


def site_also(d: dict) -> str:
    ""

    if not d.get("also_due"):
        return ""
    source = d.get("date_from") or "the course site"
    return end(f"{source[:1].upper()}{source[1:]} gives {when_full(d['due'])}, but {d['also_from']} says "
               f"{when_full(d['also_due'])}")


def _site_left(d: dict) -> str:
    ""
    if d.get("module") != "unit":
        return ""
    exams = [re.sub(r"\s*\(.*?\)", "", t) for t in d.get("exams") or []]
    n = d.get("items") or 0
    return (f"The course site shows {n} {'item' if n == 1 else 'items'} not turned in"
            + (f", including {_join(exams)}." if exams else "."))


def _schoolish(*texts: str) -> bool:
    joined = " ".join(t or "" for t in texts)
    own = (home.get("zips.owner_keywords") or {}).get("school") if isinstance(home.get("zips.owner_keywords"),
                                                                              dict) else None
    return bool(SCHOOL.search(joined) or COURSE.search(joined) or re.search(r"\bschool\b", joined, re.I)
                or (own and re.search(rf"\b({own})\b", joined, re.I)))


async def school_deadline(src: base.Sources) -> list[Candidate]:
    t = _now()
    horizon = t + DEADLINE_HOURS * 3600
    events, cards = src.calendar(), src.trello()
    items: list[tuple[str, str, float, str]] = []
    for c in cards:
        due = c.get("due")
        if not due or c.get("due_complete") or not t < due <= horizon:
            continue
        if re.match(r"\s*(done|complete)", c.get("list") or "", re.I):
            continue
        if _schoolish(c["name"], c.get("board", ""), " ".join(c.get("labels") or [])):
            items.append((c["name"], "Trello", due, c.get("list") or ""))
    for e in events:
        if not DUE.search(e["title"]) or not _schoolish(e["title"]):
            continue
        due = e["start"] + 23 * 3600 + 55 * 60 if e.get("all_day") else e["start"]
        if t < due <= horizon:
            items.append((_due_name(e["title"]), "your calendar", due, ""))
    for note in src.notes_of("school"):
        for due, thing in _due_from_note(note, t - 86400):
            if t < due <= horizon:
                items.append((thing, "School's notes", due, ""))
    rows = {}
    for name, due, d in _site_items(src, t, horizon, [(i[0], i[2]) for i in items]):
        items.append((name, d.get("date_from") or "the course site", due, ""))
        rows[name] = d
    if notices.mode("due_soon") != "off":


        site = _site_items(src, t, t + LAST_CALL_MAX_SEC, [])
        items = [i for i in items if i[2] - t > _last_call_sec(site, i[0], i[2]) or not _handed_in(i[0])]
    if not items:
        return []
    groups = await src.board()
    tasks = [x for g in groups.values() for x in g if float(x.get("created_at") or 0) >= t - 14 * 86400]
    doing = [c for c in cards if (c.get("list") or "").lower().startswith("doing")]
    groups: list[list[tuple[str, str, float]]] = []
    for name, source, due, card_list in sorted(items, key=lambda x: x[2]):
        if card_list.lower().startswith("doing") or any(alike(name, c["name"]) for c in doing):
            continue
        if any(timed(e) and not DUE.search(e["title"]) and e["start"] < due and alike(name, e["title"])
               for e in events):
            continue
        if any(alike(name, x.get("title") or "") and x.get("status") in ("done", "running", "review", "ready")
               for x in tasks):
            continue

        for g in groups:
            gname, _, gdue = g[0]
            same_evening = notices._et(gdue).date() == notices._et(due).date() and abs(gdue - due) < 6 * 3600
            if same_evening and any(alike(name, n) or courses(name) & courses(n) or numbered(name) & numbered(n)
                                    for n, _, _ in g):

                if not any(n.lower() == name.lower() or _items(name) & _items(n) for n, _, _ in g):
                    g.append((name, source, due))
                break
        else:
            groups.append([(name, source, due)])
    found: dict[str, Candidate] = {}
    for g in groups:
        units = [rows[n] for n, _, _ in g if n in rows]
        minutes = _block_minutes(sum(u.get("items") or 0 for u in units if u.get("module") == "unit"))
        c = (_deadline(g[0][0], g[0][1], g[0][2], events, minutes) if len(g) == 1
             else _deadlines(g, events, minutes))
        c.evidence = " ".join([c.evidence, *filter(None, map(_site_left, units))])
        if also := " ".join(filter(None, map(site_also, units))):
            c.facts["also"] = also
            c.evidence += f" {also}"
        if _project_for(c, c.facts["what"]):
            c.options.insert(1, Option("Open what I need on my Mac", "hub", hub="open_project"))
        found.setdefault(c.subject, c)
    return list(found.values())


def _project_for(c: Candidate, what: str) -> bool:
    ""

    from . import project
    kit = project.find_for_deadline(c.owner, what) if _has_mac() else None
    if kit:
        c.ref = {**c.ref, "kit_id": kit["id"], "kit_bot": c.owner}
    return bool(kit)


def _due_name(title: str) -> str:
    ""

    name = re.sub(r"\s*\b(is )?(due|(final )?deadline|submission)\b.*$", "", title, flags=re.I).strip(" -:—–")
    return name or title


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _deadlines(group: list[tuple[str, str, float]], events: list[dict], minutes: int = 30) -> Candidate:
    ""
    names = [n for n, _, _ in group]
    found = [courses(n) for n in names if courses(n)]
    shared = set.intersection(*found) if found else set()
    course = next(iter(sorted(shared)), "")
    label = ""
    if course:
        m = COURSE.search(names[0])
        label = m.group(0) if m else course
        names = [re.sub(rf"^\s*{re.escape(label)}\s*[—–:,-]*\s*", "", n).strip() or n for n in names]
    due = min(d for _, _, d in group)
    sources = sorted({s for _, s, _ in group})
    count = {2: "Two", 3: "Three", 4: "Four", 5: "Five"}.get(len(names), str(len(names)))
    what = f"{label}: {_join(names)}" if label else _join(names)
    c = _deadline(what, sources[0], due, events, minutes)
    c.subject = f"deadline:{base._slug(label or names[0])}:{notices._et(due).strftime('%Y%m%d')}"
    c.evidence = (f"{count} {label + ' ' if label else ''}items are due {when(due)}, according to {_join(sources)}: "
                  f"{_join(names)}. I don't see time set aside for them or anything showing they are done.")
    c.facts = {"what": what, "when": when_full(due), "source": ", ".join(sources)}
    c.question = (c.question.replace("for it, or is it already in?", "for them, or are they already in?")
                  .replace("for it, or is it already done?", "for them, or are they already done?"))
    c.lock = f"{count} {label + ' ' if label else ''}items are due {notices.day(due)}. Block time?"[:60]
    first = c.options[0]
    if first.text == "Find time for it":
        first = Option("Find time for them", first.kind, say=first.say)
    c.options = [first, Option("Already submitted" if all(_handed_in(n) for n in names) else "Already done", "close"),
                 Option("I'll handle them", "done", follow="Okay. I'll leave them with you.")]
    return c


def free_slot(events: list[dict], after: float, before: float, minutes: int = 30) -> float | None:
    ""

    start = after + 3600
    start += (1800 - start % 1800) % 1800
    busy = [(e["start"], e["end"]) for e in events if timed(e)]
    while start + minutes * 60 <= before:
        d = notices._et(start)
        end_h = notices._et(start + minutes * 60)
        if 8 <= d.hour and (end_h.hour < 22 or (end_h.hour == 22 and end_h.minute == 0)) and d.date() == end_h.date():
            if not any(s < start + minutes * 60 and e > start for s, e in busy):
                return start
        start += 1800
    return None




NOT_SUBMITTED = re.compile(r"\b(contact|call|phone|email|e-mail|text|message|meet|meeting|talk|ask|schedule|book|"
                           r"pay|register|sign up|visit|pick up|renew|appointment)\b", re.I)


def _handed_in(name: str) -> bool:
    return not NOT_SUBMITTED.search(name or "")


DONE_WORDS = re.compile(r"\b(done|submitted|turned in|handed in|completed|finished)\b", re.I)
NOT_DONE = re.compile(r"\b(not|n't|never|haven't|hasn't|isn't|aren't|wasn't)\s+(\w+\s+){0,2}"
                      r"(done|submitted|turned in|handed in|completed|finished)\b", re.I)


def said_done(name: str, hours: int = 24) -> bool:
    ""
    from . import team
    since = int(_now()) - hours * 3600
    keys = {k for k in (name.lower().strip(), COURSE.sub("", name).strip(" :-\u2013\u2014,").lower()) if len(k) >= 4}
    try:


        rows = team._db().execute(
            "SELECT CASE WHEN kind IN ('ask', 'task') THEN answer ELSE text END FROM team_msgs WHERE created_at >= ? UNION ALL "
            "SELECT text FROM team_notes WHERE created_at >= ?", (since, since)).fetchall()
    except Exception:
        return False
    for (said,) in rows:
        for sentence in re.split(r"(?<=[.!?])\s+", said or ""):
            low = sentence.lower()
            if sentence.rstrip().endswith("?"):
                continue
            if any(k in low for k in keys) and DONE_WORDS.search(sentence) and not NOT_DONE.search(sentence):
                return True
    return False


def _block_minutes(items: int) -> int:
    ""




    return min(180, max(30, round(items * 20 / 30) * 30))


def _length(minutes: int) -> tuple[str, str]:
    ""
    n, unit = (f"{minutes / 60:g}", "hour") if minutes >= 120 or minutes == 60 else (str(minutes), "minute")
    return f"{n}-{unit}", f"{n} {unit}{'' if n == '1' else 's'}"


def _deadline(name: str, source: str, due: float, events: list[dict], minutes: int = 30) -> Candidate:
    slot = free_slot(events, _now(), due, minutes)
    size, amount = _length(minutes)
    if slot:
        first = Option(f"Block {when(slot)}", "act",
                       say=end(f"Please put a {size} block on my calendar {when_full(slot)} to work on {P(name)}, "
                               f"which is due {when_full(due)}"))
        question = f"Want a {size} block {when(slot)} for it, or is it already in?"
    else:
        first = Option("Find time for it", "act",
                       say=f"Please find {amount} on my calendar before {when_full(due)} for {P(name)} and block it.")
        question = f"Want me to find {amount} for it, or is it already in?"
    if not _handed_in(name):
        question = question.replace("already in?", "already done?")
    return Candidate(
        noticer="school_deadline", subject=f"deadline:{base._slug(name)}:{notices._et(due).strftime('%Y%m%d')}",
        owner="school" if "school" in config.BOTS_BY_ID else "default", version=str(int(due)),
        evidence=f"{name[:1].upper() + name[1:]} is due {when(due)}, according to {source}, and I don't see time "
                 "set aside for it or anything showing it is done.",
        question=question, lock=f"{short(name, 28)} is due {notices.day(due)}. Block time?",
        facts={"what": name, "when": when_full(due), "source": source}, deadline=due,
        options=[first, Option("Already submitted" if _handed_in(name) else "Already done", "close"),
                 Option("I'll handle it", "done", follow="Okay. I'll leave it with you.")])




DUE_SOON_SEC = 3 * 3600
DUE_SOON_MIN_SEC = 10 * 60
LAST_CALL_MAX_SEC = 12 * 3600
LENGTH = re.compile(r"\b(\d{1,3})\s*min(?:ute)?s?\b\.?\s*\)", re.I)


def _timed_work(site: list[tuple[str, float, dict]], name: str, due: float) -> list[str]:
    ""

    out = []
    for n, closes, d in site:
        own = [d["title"], *(d.get("exams") or [])]
        same = courses(name) & courses(n) if courses(name) else any(alike(name, o) for o in [n, *own])
        if same and abs(closes - due) < 1800:
            out += [title for title in own if LENGTH.search(title)]
    return out


def _minutes(titles: list[str]) -> int:
    return sum(int(LENGTH.search(title).group(1)) for title in titles)


def _last_call_sec(site: list[tuple[str, float, dict]], name: str, due: float) -> int:
    ""





    return min(LAST_CALL_MAX_SEC, max(DUE_SOON_SEC, _minutes(_timed_work(site, name, due)) * 120 + 1800))


def _takes(titles: list[str]) -> str:
    ""
    if not titles:
        return ""
    kind = "exam" if all(re.search(r"\bexam\b", title, re.I) for title in titles) else "item"
    hours, mins = divmod(_minutes(titles), 60)
    length = " ".join(filter(None, [hours and f"{hours} hour{'' if hours == 1 else 's'}", mins and f"{mins} minutes"]))
    several = {2: "two", 3: "three", 4: "four", 5: "five"}.get(len(titles), str(len(titles)))
    return f"The {kind} takes about {length}." if len(titles) == 1 else f"The {several} {kind}s take about {length}."


def _due_items(src: base.Sources, t: float, start: float, end: float,
               fits=lambda name, due: True) -> list[tuple[str, float, dict | None]]:
    ""


    out = [x for x in _listed_items(src, t, start, end) if fits(x[0], x[1])]
    return out + [(name, due, None) for name, due, _ in _site_items(src, start, end, [(n, d) for n, d, _ in out])
                  if fits(name, due)]


def _listed_items(src: base.Sources, t: float, start: float, end: float) -> list[tuple[str, float, dict | None]]:
    ""
    out = []
    for e in src.calendar():
        if e.get("all_day") or not DUE.search(e["title"]) or not _schoolish(e["title"]):
            continue
        if start < e["start"] <= end:
            out.append((_due_name(e["title"]), e["start"], None))
    for c in src.trello():
        due = c.get("due")
        if not due or c.get("due_complete") or not start < due <= end or re.match(r"\s*(done|complete)",
                                                                                  c.get("list") or "", re.I):
            continue
        if _schoolish(c["name"], c.get("board", ""), " ".join(c.get("labels") or [])):
            out.append((c["name"], due, c))
    for note in src.notes_of("school"):
        for due, thing in _due_from_note(note, t - 86400):
            if start < due <= end:
                out.append((thing, due, None))
    return out


BRIEF_DAYS = 3


def site_lines(bot_id: str, now: float) -> list[str]:
    ""








    from . import school, schoolcheck, settled
    if bot_id not in ("default", schoolcheck.checker()):
        return []
    src = base.Sources()
    start, end = now - school.GRACE, now + BRIEF_DAYS * 86400
    known = [(n, d) for n, d, _ in _listed_items(src, now, start, end)]
    names, today = school.class_names(), notices._et(now).date()
    read = school.status()["last_success"]
    site = "the course site"
    if read:
        site += f", read {'at ' + clock(read) if notices._et(read).date() == today else when(read, now)},"
    out = []
    for name, due, d in _site_items(src, start, end, known):
        if settled.quiet_past(d, now):



            continue
        course = d["course"] + (f" ({names[d['course']]})" if names.get(d["course"]) else "")
        days = (notices._et(due).date() - today).days
        day = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(days) or notices._et(due).strftime(
            "%A" if abs(days) <= 6 else "%b %-d")
        line = f"- {course}{name[len(d['course']):]}, {'due' if due > now else 'overdue since'} {day} {clock(due)}"
        exams = [re.sub(r"\s*\(.*?\)", "", t) for t in d.get("exams") or []]
        if exams:
            line += ", both exams open" if len(exams) == 2 else f", {_join(exams)} open"
        n = d.get("items") or 0
        line += (f": {site} shows {n} {'item' if n == 1 else 'items'} not turned in" if d.get("module") == "unit"
                 else f": {site} can't show whether it was posted" if d.get("module") == "forum"
                 else f": {site} doesn't show it turned in")
        if size := school.size_words(d.get("minutes") or 0):
            line += f", {size} by the course's own labels with any reading on top"
        if d.get("also_due"):
            other = notices._et(d["also_due"])
            said = (line + f", and the date is from {d['date_from']}, but {d['also_from']} says "
                    f"{other:%A} {clock(d['also_due'])}")
            out.append(said + ("" if said.endswith(".") else ".") + " Say both dates and where each is from.")
            continue
        out.append(line + (f", and the date is from {d['date_from']}." if d.get("date_from") else "."))
    return out


def _left(due: float) -> str:
    ""
    mins = max(1, round((due - _now()) / 60))
    if mins < 50:
        return f"in {mins} minutes"
    halves = max(2, round(mins / 30))
    words = {2: "an hour", 3: "an hour and a half", 4: "two hours", 5: "two and a half hours", 6: "three hours"}
    half = " and a half" if halves % 2 else ""
    return f"in about {words.get(halves, f'{halves // 2}{half} hours')}"


def _has_mac() -> bool:
    ""
    from . import store
    try:
        return store.db().execute("SELECT 1 FROM devices WHERE mac_link=1 LIMIT 1").fetchone() is not None
    except Exception:
        return False


async def due_soon(src: base.Sources) -> list[Candidate]:
    ""




    from . import schoolcheck
    t = _now()
    site = _site_items(src, t, t + LAST_CALL_MAX_SEC, [])
    items = _due_items(src, t, t + DUE_SOON_MIN_SEC, t + LAST_CALL_MAX_SEC,
                       lambda name, due: due - t <= _last_call_sec(site, name, due))
    if not items:
        return []
    groups_now = await src.board()
    busy = any((x.get("assignee") or "default") == schoolcheck.checker() for x in groups_now.get("working") or [])
    tasks = [x for g in groups_now.values() for x in g if float(x.get("created_at") or 0) >= t - 14 * 86400]
    groups: list[list[tuple[str, float, dict | None]]] = []
    for name, due, card in sorted(items, key=lambda x: x[1]):
        if any(alike(name, x.get("title") or "") and x.get("status") == "done" for x in tasks):
            continue
        for g in groups:
            if abs(g[0][1] - due) < 1800 and any(alike(name, n) or courses(name) & courses(n) for n, _, _ in g):
                same = next((i for i, (n, _, _) in enumerate(g) if n.lower() == name.lower() or _items(name) & _items(n)),
                            None)
                if same is None:
                    g.append((name, due, card))
                elif card and not g[same][2]:
                    g[same] = (g[same][0], g[same][1], card)
                break
        else:
            groups.append([(name, due, card)])
    found = []
    for g in groups:



        g = [x for x in g if _handed_in(x[0]) and not said_done(x[0])]
        if not g:
            continue
        names = [n for n, _, _ in g]
        due = min(d for _, d, _ in g)
        subject = f"due:{base._slug(' '.join(sorted(courses(' '.join(names)))) or names[0])}:" \
                  f"{notices._et(due).strftime('%Y%m%d%H%M')}"
        res = schoolcheck.result(subject)


        if res and res.get("state") == "done" and due - t <= DUE_SOON_SEC and float(res.get("at") or 0) < due - DUE_SOON_SEC \
                and schoolcheck.request(subject, names, due, busy=busy, again=True):
            continue
        if res is None:
            if not notices.row(subject):
                schoolcheck.request(subject, names, due, busy=busy)
            continue
        if res.get("state") != "done":
            continue
        status = {i["name"]: i for i in res.get("items") or []}
        left = [n for n in names if (status.get(n) or {}).get("submitted") != "yes"]
        if not left:
            continue
        cards = [c for n, _, c in g if c and n in left] or [c for c in src.trello() if not c.get("due_complete")
                                                             and any(alike(n, c.get("name") or "") for n in left)]
        titles = dict.fromkeys(title for n in left for title in _timed_work(site, n, due)
                               if not any(title in n for n in names if n not in left))
        c = _due_soon(subject, g, left, status, res.get("site") or "", due, cards[0] if cards else None,
                      _takes(list(titles)))
        if also := " ".join(dict.fromkeys(site_also(d) for n, _, d in site if n in left and d.get("also_due"))):
            c.facts["also"] = also
            c.evidence += f" {also}"
        found.append(c)
    return found


def _due_soon(subject: str, group: list, left: list[str], status: dict, site: str, due: float,
              card: dict | None, takes: str = "") -> Candidate:
    names = [n for n, _, _ in group]
    done = [n for n in names if n not in left]
    label, short_left = _course_label(left)
    what = f"{label}: {_join(short_left)}" if label and len(left) > 1 else _join(left)
    several = len(left) > 1
    site_name = site or "the course site"
    checked = [n for n in left if (status.get(n) or {}).get("submitted") == "no"]
    if done:
        found = (f"I checked {site_name}: {_join(done)} {'shows' if len(done) == 1 else 'show'} as turned in, but I "
                 f"couldn't find a submission for {_join(left)}.")
    elif checked and len(checked) == len(left):
        found = (f"I checked {site_name} and couldn't find a submission for "
                 f"{'any of them' if several else 'it'}.")
    elif checked:
        found = f"I checked {site_name} and couldn't find a submission for {_join(checked)}."
    else:
        found = (f"I couldn't get into {site_name} to check, and nothing else shows "
                 f"{'they are' if several else 'it is'} done.")
    count = {2: "two", 3: "three", 4: "four", 5: "five"}.get(len(left), str(len(left)))
    head = (f"Your {count} {label + ' ' if label else ''}items are due {when(due)}, {_left(due)}: "
            f"{_join(short_left)}." if several else f"Your {what} is due {when(due)}, {_left(due)}.")
    link = next(((status.get(n) or {}).get("link") for n in left if (status.get(n) or {}).get("link")), "")
    due_at = when_full(due)
    not_yet = end(f"Not done yet: {P(what, 160)}, due {due_at}")

    page = f"the assignment page ({P(link.replace('@', '%40'), 200)})" if link else "the assignment page"
    owner = "school" if "school" in config.BOTS_BY_ID else "default"
    c = Candidate(
        noticer="due_soon", subject=subject, owner=owner, version=str(int(due)),
        evidence=" ".join(filter(None, [head, found, takes])), question="Are they done?" if several else "Is it done?",
        lock="Something is due soon. Is it done?",
        facts={"what": what, "when": due_at, "source": f"{site_name}, checked {notices.clock(_now())}"},
        consequence=("They are late" if several else "It is late") + " if not turned in by then.", deadline=due,
        haiku=False, lead=True, options=[])
    if _project_for(c, what):
        not_done = Option("Not done yet. Open what I need on my Mac", "hub", hub="open_project")
    elif _has_mac():


        not_done = Option("Not done yet. Open what I need on my Mac", "act",
                          say=f"{not_yet} Please gather what I need for "
                              f"{'them' if several else 'it'} into a project I can open on my Mac: {page} and my "
                              "draft or notes if you can find them.")
    else:
        not_done = Option("Not done yet. Help me finish it", "act",
                          say=f"{not_yet} Please pull up what I need for "
                              f"{'them' if several else 'it'}: {page} and my draft or notes if you can find them.")
    if card:
        finished = Option("It's finished. Mark it done on Trello", "act",
                          say=f"It's finished: {P(what, 160)}. Please mark the Trello card \"{P(card.get('name'), 120)}\" "
                              "done.")
        options = [finished, not_done]
    else:
        options = [not_done, Option("It's finished", "done", follow="Good. I won't ask about it again.")]
    c.options = options
    return c


def _course_label(names: list[str]) -> tuple[str, list[str]]:
    ""
    found = [courses(n) for n in names]
    shared = set.intersection(*found) if found and all(found) else set()
    if not shared or len(names) < 2:
        return "", names
    m = COURSE.search(names[0])
    label = m.group(0) if m else sorted(shared)[0]
    return label, [re.sub(rf"^\s*{re.escape(label)}\s*[—–:,-]*\s*", "", n).strip() or n for n in names]




CONFLICT_DAYS = 7
GAP_SEC = 30 * 60


def _same_place(a: str, b: str) -> bool:
    na, nb = (" ".join(re.findall(r"[a-z0-9]+", x.lower())) for x in (a, b))
    return na == nb or na in nb or nb in na


async def conflict(src: base.Sources) -> list[Candidate]:
    ""

    t = _now()
    events = [e for e in src.calendar() if commitment(e) and e["end"] > t and e["start"] < t + CONFLICT_DAYS * 86400]
    by_day: dict[str, list] = {}
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if b["start"] >= a["end"] + GAP_SEC:
                continue
            if a["title"].strip().lower() == b["title"].strip().lower() and a["start"] == b["start"]:
                continue
            day_key = notices._et(min(a["start"], b["start"])).strftime("%Y-%m-%d")
            if b["start"] < a["end"] and a["start"] < b["end"]:
                by_day.setdefault(day_key, []).append(("overlap", a, b, "", ""))
            elif a["end"] <= b["start"]:
                pa, pb = calcache.physical_place(a), calcache.physical_place(b)
                if pa and pb and not _same_place(pa, pb):
                    by_day.setdefault(day_key, []).append(("gap", a, b, pa, pb))
    found = []
    for clashes in by_day.values():
        kind, a, b, pa, pb = clashes[0]
        more = len(clashes) - 1
        found.append(_overlap(a, b, more) if kind == "overlap" else _gap(a, b, pa, pb, more))
    return found


def _range(e: dict) -> str:
    ""
    a, b = clock(e["start"]), clock(e["end"])
    if a[-4:] == b[-4:] and notices._et(e["start"]).date() == notices._et(e["end"]).date():
        a = a[:-5]
    return f"from {a} to {b}"


def _more(n: int) -> str:
    return "" if n <= 0 else (" There is one more clash that day." if n == 1 else f" There are {n} more clashes that day.")


def _overlap(a: dict, b: dict, more: int = 0) -> Candidate:
    on = notices._et(a["start"]).strftime("%A, %b %-d")
    day = notices.day(a["start"])
    day = day[:1].upper() + day[1:]
    ta, tb = short(notices.tidy(a["title"]), 32), short(notices.tidy(b["title"]), 32)
    fix = (f"On {on}, I want to keep {{keep}} and not {{other}}, since they overlap. Please suggest how to fix my "
           "calendar, move my own events if that is all it takes, and tell me what you changed.")
    return Candidate(
        noticer="conflict", subject=f"conflict:{min(a['id'], b['id'])}:{max(a['id'], b['id'])}", owner="default",
        version=f"{int(a['start'])}-{int(b['start'])}",
        evidence=f"{day} you have {a['title']} {_range(a)} and {b['title']} {_range(b)}, and they overlap."
                 + _more(more),
        question="Which one do you want to keep?", lock=f"{ta} and {tb} overlap"[:60],
        facts={"what": f"{a['title']} and {b['title']}", "when": when_full(a["start"])},
        consequence=f"{who.first()} can't be at both.", deadline=min(a["start"], b["start"]), haiku=False, lead=True,
        options=[Option(f"Keep {ta}", "act", say=fix.format(keep=P(a["title"]), other=P(b["title"]))),
                 Option(f"Keep {tb}", "act", say=fix.format(keep=P(b["title"]), other=P(a["title"]))),
                 Option("I'll sort it out", "done", follow="Okay. I'll leave it with you.")])


def _gap(a: dict, b: dict, pa: str, pb: str, more: int = 0) -> Candidate:
    mins = int((b["start"] - a["end"]) // 60)
    gap = "no time" if mins <= 0 else f"{mins} minutes"
    return Candidate(
        noticer="conflict", subject=f"gap:{a['id']}:{b['id']}", owner="default",
        version=f"{int(a['end'])}-{int(b['start'])}",
        evidence=f"{a['title']} ends at {clock(a['end'])} at {short(pa, 50)}, and {b['title']} starts at "
                 f"{clock(b['start'])} at {short(pb, 50)}. That leaves {gap} to get there." + _more(more),
        question="Want help fixing it, or is that enough time?",
        lock=f"{gap[:1].upper() + gap[1:]} between {short(a['title'], 20)} and {short(b['title'], 20)}"[:60],
        facts={"what": f"{a['title']} then {b['title']}", "when": when_full(a["end"])},
        consequence=f"{who.first()} may not make it in time.", deadline=b["start"], haiku=False, lead=True,
        options=[Option("Help me fix it", "act",
                        say=f"{P(a['title'])} ends at {clock(a['end'])} and {P(b['title'])} starts at {clock(b['start'])} "
                            f"in a different place on {notices._et(a['end']).strftime('%A, %b %-d')}. Please suggest "
                            "a fix, and move my own events if that is all it takes."),
                 Option("That's enough time", "done", follow="Okay. I'll leave it as it is.")])




def meeting(e: dict) -> bool:
    ""

    if not timed(e) or calcache.busy_kind(e) in ("shift", "class", "church") or DUE.search(e["title"]):
        return False
    if notices.HEALTH.search(f"{e['title']} {e.get('location') or ''}"):
        return False
    return bool(calcache.outside_people(e) or MEETING.search(e["title"]))


def owner_for_event(e: dict) -> str:
    people = calcache.outside_people(e)
    if people:
        o = base.owner_for(mail.account_for(people[0]["email"]) or "", people[0]["email"])
        if o != "default":
            return o
    title = f"{e['title']} {e.get('description') or ''}".lower()
    own = home.get("zips.owner_keywords") or {}
    for bot, pat in (("school", r"advis|registr|professor|class|course|writing lab"),
                     ("career", r"interview|recruit|career|job|résumé|resume"),
                     ("clients", r"client"),
                     ("money", r"bank|loan|insurance|budget|tax"),
                     ("builder", r"youtube|website|hosting")):
        extra = str(own.get(bot) or "") if isinstance(own, dict) else ""
        pat = f"{extra}|{pat}" if extra else pat
        if re.search(pat, title) and bot in config.BOTS_BY_ID:
            return bot
    return "default"


def people_line(e: dict) -> str:
    names = [p.get("name") or p["email"] for p in calcache.outside_people(e)][:3]
    return ", ".join(names)


async def prep(src: base.Sources) -> list[Candidate]:
    t = _now()
    now_et = notices._et(t)
    files = src.work_files(3)
    found = []
    for e in src.calendar():
        if not meeting(e) or e["start"] <= t:
            continue
        start_et = notices._et(e["start"])
        if start_et.date() == now_et.date() + timedelta(days=1) and 18 <= now_et.hour < 23:
            phase = "eve"
        elif e["start"] - 2 * 3600 - 15 * 60 <= t < e["start"] - 30 * 60:
            phase = "day"
        else:
            continue
        subject = f"prep:{e['id']}"
        old = notices.row(subject)
        if old and old["state"] not in ("open", "expired"):
            continue
        names = words(e["title"]) | {w for p in calcache.outside_people(e) for w in words(p.get("name") or "")}
        stamp = {start_et.strftime("%Y-%m-%d"), start_et.strftime("%b%d").lower(), start_et.strftime("%m%d")}
        if any((words(name) & names) or any(s in name.lower() for s in stamp) for _, name, _ in files
               if "prep" in name.lower() or words(name) & names):
            continue
        owner = owner_for_event(e)
        promise = await _promise_for(src, e, owner, names)
        found.append(_prep(e, phase, owner, promise))
    return found


async def _promise_for(src: base.Sources, e: dict, owner: str, names: set[str]) -> str:
    ""
    t = _now()
    for bot in dict.fromkeys(["default", owner]):
        for m in await src.chat(bot):
            ts = m.get("ts")
            if m.get("class") != "colin" or not isinstance(ts, (int, float)) or t - ts > 14 * 86400:
                continue
            text = m.get("display") or m.get("text") or ""
            if not (words(text) & names):
                continue
            for p in base.promises_in(text, ts):
                if p["due"] <= e["start"] + 86400:
                    return p["what"]
            m2 = re.search(r"\b(?:i'll|i will|i need to|i have to|i'm going to)\s+([^.?!\n]{3,80})", text, re.I)
            if m2:
                return " ".join(m2.group(1).split())
    return ""


def _prep(e: dict, phase: str, owner: str, promise: str) -> Candidate:
    to_who = people_line(e)
    with_who = f" with {to_who}" if to_who and to_who.lower() not in e["title"].lower() else ""
    owner_name = notices._bot_name(owner)
    said = f" You said you'd {promise}." if promise else ""
    ask = (end(f"Please have {owner_name} make a one-page prep for {P(e['title'])}{P(with_who)} "
               f"{when_full(e['start'])}") + " Use the prep format from my notes if there is one, and save it in their work folder.")
    if promise:
        ask += f" I said I'd {promise}; put that at the top."
    return Candidate(
        noticer="prep", subject=f"prep:{e['id']}", owner=owner, version=phase,
        evidence=f"{e['title']}{with_who} is {when(e['start'])}, and I don't see any prep for it yet.{said}",
        question=f"Want {owner_name} to make a one-page prep{' tonight' if phase == 'eve' else ' now'}?",
        lock=f"{short(e['title'], 30)} is {notices.day(e['start'])}. Prep?"[:60],
        facts={"what": e["title"], "who": to_who, "when": when_full(e["start"])}, deadline=e["start"],
        event_timed=True, excerpt=(e.get("description") or "")[:600],
        options=[Option("Make the prep", "act", say=ask, follow=f"Sent to {notices._lead()}."),
                 Option("Skip it", "done", follow="Okay. No prep this time.")])




FOLLOW_WINDOW_SEC = 30 * 60
NEXT = re.compile(r"\*\*Next:\*\*\s*(.+)", re.I)


def _account_for(addr: str) -> str | None:
    ""
    return mail.account_for(addr)


async def followup(src: base.Sources) -> list[Candidate]:
    t = _now()
    found = []
    for e in src.calendar():
        if not meeting(e) or not e["end"] <= t < e["end"] + FOLLOW_WINDOW_SEC:
            continue
        owner = owner_for_event(e)
        people = calcache.outside_people(e)
        names = words(e["title"]) | {w for p in people for w in words(p.get("name") or "")}
        material = await _said_about(src, e, owner, names)
        to = people[0] if people else None
        account = (_account_for(to["email"]) if to else mail.default_account()) or ""
        prepared = bool(to and account and material and await src.wrote_to(account, to["email"]))
        found.append(_followup(e, owner, to, account, material, prepared))
    for call in src.calls(t - FOLLOW_WINDOW_SEC):
        m = NEXT.search(call["summary"] or "")
        if m:
            found.append(_after_call(call, " ".join(m.group(1).split()).rstrip(".")))
    return found


async def _said_about(src: base.Sources, e: dict, owner: str, names: set[str]) -> str:
    ""

    lines = []
    for bot in dict.fromkeys(["default", owner]):
        for m in await src.chat(bot):
            ts = m.get("ts")
            if m.get("class") != "colin" or not isinstance(ts, (int, float)) or ts < e["start"] - 3600:
                continue
            text = " ".join((m.get("display") or "").split())
            if words(text) & names:
                lines.append(text[:400])
    return "\n".join(lines)[:1500]


def _end_of_next_day(ts: float) -> float:
    ""


    d = notices._et(ts).date() + timedelta(days=1)
    return datetime(d.year, d.month, d.day, 23, 59, tzinfo=notices.ET).timestamp()


def _followup(e: dict, owner: str, to: dict | None, account: str, material: str, prepared: bool) -> Candidate:
    to_who = (to.get("name") or to["email"]) if to else people_line(e)
    first = to_who.split()[0] if to_who else ""
    owner_name = notices._bot_name(owner)
    ask = (f"My {P(e['title'])} just ended. Ask me what came up, then draft a short follow-up"
           + (f" to {P(to_who)} <{to['email']}>" if to else "") + " for me to approve. Don't send anything yet.")
    c = Candidate(
        noticer="followup", subject=f"followup:{e['id']}", owner=owner,
        evidence=end(f"{e['title']} ended at {clock(e['end'])}")
                 + (" You mentioned it in chat, so I can draft the recap." if material else ""),
        question=f"Want {owner_name} to {'send the recap below' if prepared else 'draft a follow-up'}"
                 + (f" to {first}?" if first else "?"),
        lock=f"{short(e['title'], 30)} ended. Follow up?",
        facts={"what": e["title"], "who": f"{to_who} <{to['email']}>" if to else to_who, "when": when_full(e["end"])},
        deadline=_end_of_next_day(e["end"]), event_timed=True, excerpt=material,
        options=[Option("Draft a follow-up", "act", say=ask),
                 Option("Nothing to send", "done", follow="Okay. Nothing to send.")])
    if to and material and account in mail.ACCOUNTS:
        c.draft = {"account": account, "from_addr": mail.ACCOUNTS[account], "to_name": to.get("name") or "",
                   "to_addr": to["email"], "subject": f"Following up on {e['title']}",
                   "purpose": f"recaps what came up and the next steps, using only what {who.first()} said about it"}
        c.draft_options = [Option("Send the recap as written", "send"),
                           Option("Change it", "decide", say=f"About the follow-up to {P(to_who)} after {P(e['title'])}: I want "
                                                             "to change it before it goes. Ask me what to change."),
                           Option("Nothing to send", "done", follow="Okay. Nothing to send.")]
        if prepared:
            c.prepare = {"bot": owner, "label": "Send the recap as written"}
    return c


def _after_call(call: dict, next_step: str) -> Candidate:
    zip_name = notices._bot_name(call["bot_id"])
    return Candidate(
        noticer="followup", subject=f"call:{call['id']}", owner=call["bot_id"],
        evidence=f"The next step from your call with {zip_name}: {next_step}.",
        question=f"Want {zip_name} to take care of it?",
        facts={"what": next_step, "who": zip_name, "when": "just now"}, event_timed=True,
        deadline=float(call["at"]) + 4 * 3600, haiku=False, lead=True,
        options=[Option("Go ahead", "act",
                        say=f"From my call with {zip_name}: {P(next_step, 200)}. Please take care of it, and show me anything "
                            "that needs my okay first."),
                 Option("I'll handle it", "done", follow="Okay. I'll leave it with you.")])




WINDOW = re.compile(r"\b(registration|window|enrollment|enrolment|advising|application|applications|apply|"
                    r"add/drop|drop deadline|withdraw|scholarship|open house)\b", re.I)
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
RANGE = re.compile(rf"\b(?P<m1>{_MON})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y1>20\d\d))?\s*"
                   rf"(?:to|through|thru|until|-|–)\s*(?:(?P<m2>{_MON})\s+)?(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?"
                   rf"(?:,?\s+(?P<y2>20\d\d))?\b", re.I)
OPENS = re.compile(rf"\b(opens?|starts?|begins?)\s+(?:on\s+)?(?P<date>{_MON}\s+\d{{1,2}})", re.I)
CLOSES = re.compile(rf"\b(closes?|ends?|deadline(?: is)?|due by|apply by)\s+(?:on\s+)?(?P<date>{_MON}\s+\d{{1,2}})",
                    re.I)
WINDOW_DAYS = 3
NOTED_DAYS = 30


def windows_in(note: str, ref: float) -> list[tuple[str, str, float]]:
    ""
    out = []
    for sentence in re.split(r"(?<=[.;\n])\s+", note):
        if not WINDOW.search(sentence):
            continue
        m = RANGE.search(sentence)
        if m:
            start = base.dates_in(f"{m.group('m1')} {m.group('d1')} {m.group('y1') or ''}", ref)
            end_text = f"{m.group('m2') or m.group('m1')} {m.group('d2')} {m.group('y2') or m.group('y1') or ''}"
            end = base.dates_in(end_text, start[0] if start else ref)
            what = _what(sentence[:m.start()])
            if start:
                out.append((what, "opens", start[0]))
            if end:
                out.append((what, "closes", end[0]))
            continue
        for pat, kind in ((OPENS, "opens"), (CLOSES, "closes")):
            for mm in pat.finditer(sentence):
                d = base.dates_in(mm.group("date"), ref)
                if d:
                    out.append((_what(sentence[:mm.start()]), kind, d[0]))
    return out


def _what(text: str) -> str:
    t = " ".join(text.split()).strip(" -:*,")
    t = re.sub(r"\b(runs|run|is|are|opens|open|from|between|on|starts|begins|closes|ends|window|the)\s*$", "", t,
               flags=re.I).strip(" -:*,")
    t = re.sub(r"\b(runs|run|is|are|from|between|on)\s*$", "", t, flags=re.I).strip(" -:*,")
    t = t.split(". ")[-1]
    return short(t[:1].upper() + t[1:], 60) if t else "A window in your notes"


async def window(src: base.Sources) -> list[Candidate]:
    t = _now()
    seen = []
    for bot in ("school", "career"):
        if bot not in config.BOTS_BY_ID:
            continue
        for note in src.notes_of(bot):
            seen += [(day_ts, what, kind, bot, "") for what, kind, day_ts in windows_in(note, t - 60 * 86400)]
    seen += [(e["start"], short(e["title"], 60), "is", owner_for_event(e), "on your calendar")
             for e in src.calendar() if day_event(e)]







    found: dict[str, Candidate] = {}
    first: dict[tuple, tuple[float, Candidate | None]] = {}
    for day_ts, what, kind, owner, source in sorted(seen, key=lambda w: w[0]):
        word = (owner, _window_word(what))
        if word in first and day_ts - first[word][0] <= WINDOW_DAYS * 86400:
            earliest, c = first[word]
            if c and notices._et(day_ts).date() != notices._et(earliest).date():
                c.facts["also"] = _window_line(what, kind, day_ts, owner, source)
                c.evidence += f" {c.facts['also']}"
            continue
        c = None
        if 0 <= notices.days_ahead(day_ts) <= WINDOW_DAYS:
            c = _window(what, kind, day_ts, owner, source)
            c = found.setdefault(c.subject, c)
        first[word] = (day_ts, c)
    return list(found.values())


def _window_line(what: str, kind: str, day_ts: float, owner: str, source: str = "") -> str:
    source = source or f"according to {notices._bot_name(owner)}'s notes"
    return f"{what} {kind} {notices._et(day_ts).strftime('%A, %b %-d')}, {source}."


def noted_dates(e: dict, t: float) -> str:
    ""


    if not day_event(e):
        return ""
    owner, dates = owner_for_event(e), {}
    for note in base.SOURCES.notes_of(owner):
        for what, kind, day_ts in windows_in(note, t - 60 * 86400):
            if _window_word(what) == _window_word(e["title"]) and abs(day_ts - e["start"]) <= NOTED_DAYS * 86400:
                dates.setdefault((day_ts, kind), what)
    if not dates:
        return ""
    said = ", ".join(f"{kind} {notices._et(day_ts).strftime('%a %b %-d')}" for day_ts, kind in sorted(dates))
    return f"{notices._bot_name(owner)}'s notes: {dates[min(dates)]} {said}"


def _window_word(what: str) -> str:
    ""
    m = WINDOW.search(what)
    word = m.group(1).lower() if m else base._slug(what)
    return {"applications": "application", "apply": "application", "enrolment": "enrollment"}.get(word, word)


def day_event(e: dict) -> bool:
    ""


    title = e.get("title") or ""
    if e.get("all_day") and ELECTION.search(title):
        return True
    return bool(e.get("all_day") and not free(e) and not calcache.declined(e) and WINDOW.search(title)
                and "holiday" not in str(e.get("calendar") or "").lower())




ELECTION = re.compile(r"\b(election day|primary election|general election)\b", re.I)


def _window(what: str, kind: str, day_ts: float, owner: str, source: str = "") -> Candidate:
    on = notices._et(day_ts).strftime("%A, %b %-d")
    deadline = day_ts + (9 * 3600 if kind == "opens" else 23 * 3600 + 55 * 60)
    owner_name = notices._bot_name(owner)
    c = Candidate(
        noticer="window", subject=f"window:{base._slug(what)}:{kind}:{notices._et(day_ts).strftime('%Y%m%d')}",
        owner=owner, evidence=_window_line(what, kind, day_ts, owner, source),
        question=f"Want {owner_name} to get you ready for it?",
        lock=f"{short(what, 34)} {kind} {notices.day(day_ts)}"[:60],
        facts={"what": what, "when": f"{kind} {on}"}, deadline=deadline,
        options=[Option("Get me ready for it", "act",
                        say=f"{P(what)} {kind} {on}. Please have {owner_name} get me ready for it and tell me what I "
                            "need to do and when."),
                 Option("I've got it", "done", follow="Okay. I'll leave it with you.")])
    if ELECTION.search(what):
        c.question = "Want me to look up where and when you can vote?"
        c.options[0] = Option("Look it up", "act",
                              say=f"{P(what)} {kind} {on}. Please look up my polling place and its hours for my voter "
                                  "registration address, and whether I can still vote early or by mail, and tell me "
                                  "in one bubble.")
    return c


FINDERS = {"school_deadline": school_deadline, "due_soon": due_soon, "conflict": conflict, "prep": prep,
           "followup": followup, "window": window}
