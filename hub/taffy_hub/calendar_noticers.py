"""Proactivity phase 2: the noticers that read the calendar and Trello copy (calcache.py), the Zips' notes and work
folders, and the voice calls. The rules and the card are in notices.py; mail-based noticers are in noticers.py.

  school_deadline  schoolwork due in the next 48 hours with no Doing card, no time blocked and no finished task for it
  due_soon         schoolwork due in the next three hours that the course site doesn't show as turned in (the School
                   Zip looks first, schoolcheck.py): "Is it done?" with "mark it done on Trello" and "open what I need
                   on my Mac"
  conflict         two real commitments at the same time (a shift, a class, a meeting, somewhere to be), or back to
                   back in different places with under 30 minutes between; never a due time, an all-day event, a
                   free or declined event, or one of Colin's own routine or planning blocks; one question a day
  prep             a meeting with someone outside, the evening before or two hours before, with no prep file yet; any
                   promise Colin made for it is named
  followup         a meeting with someone outside ended in the last half hour, or a call with a Zip ended with a next step
  window           a dated window in School's or Career's notes (registration, applications) opens or closes in 3 days

Each is plain code; what reaches Colin goes through the same rules, budget and Haiku check as every question.
"""

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
                     r"review with|check-in|catch up|coffee with|meet with)\b", re.I)


def _now() -> float:
    return notices.now()


NUMBERED = re.compile(r"\b(unit|form|module|chapter|week|lesson|part|quiz|exam)\s+([a-z0-9]{1,2})\b")


def words(text: str) -> set[str]:
    """The telling words of a title. "Unit 6" and "Form H" are one word each ("unit6", "formh"), so Unit 5's
    finished task never stands for Unit 6's deadline (Oct 9)."""
    t = NUMBERED.sub(r"\1\2", (text or "").lower())
    own = {w.lower() for w in who.full().split()}
    return {w for w in re.findall(r"[a-z][a-z0-9]{3,}", t) if w not in STOP and w not in own}


def numbered(text: str) -> set[str]:
    """"Unit 6" and "Form C" in a title, as "unit6" and "formc"."""
    return {f"{a}{b}" for a, b in NUMBERED.findall((text or "").lower())}


def _items(text: str) -> set[str]:
    """The numbered things in a title that name one piece of work ("formc", "quiz3"), not a whole unit or week."""
    return {w for w in numbered(text) if not w.startswith(("unit", "week", "module", "chapter", "lesson", "part"))}


def courses(text: str) -> set[str]:
    return {re.sub(r"[ -]", "", c.upper()) for c in COURSE.findall(text or "")}


def alike(a: str, b: str) -> bool:
    """Two titles are about the same thing: two shared words, or the same course and one more word."""
    shared = words(a) & words(b)
    return len(shared) >= 2 or (bool(courses(a) & courses(b)) and len(shared) >= 1)


def clock(ts: float) -> str:
    """7 p.m., or 7:30 p.m."""
    return notices.clock(ts).replace(":00 ", " ")


def when(ts: float) -> str:
    """today at 7 p.m., Friday at 9 a.m., Oct 20 at 2 p.m."""
    return f"{notices.day(ts)} at {clock(ts)}"


def when_full(ts: float) -> str:
    return f"{notices._et(ts).strftime('%A, %b %-d')} at {clock(ts)}"


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
    """A due time, not a meeting: 15 minutes or less, a due word in the title, or an LMS calendar."""
    return (e["end"] - e["start"] <= DUE_SEC or bool(DUE.search(e["title"]))
            or bool(LMS.search(e.get("calendar") or "")))


def free(e: dict) -> bool:
    """Shown as free (transparent) on the calendar."""
    return bool(e.get("free")) or str(e.get("transparency") or e.get("show_as") or "").lower() in (
        "transparent", "free")


def commitment(e: dict) -> bool:
    """A timed event where Colin must be somewhere or do something then: people from outside, a place to go, a
    shift, a class, church or a meeting. Never a due time, an all-day, free or declined event, or his own block."""
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


# MARK: 3. A school deadline inside 48 hours with nothing behind it

DEADLINE_HOURS = 48


def _due_from_note(note: str, ref: float) -> list[tuple[float, str]]:
    """(due time, the thing) for each date in a note that says something is due. No time written means 11:55 p.m.,
    Lakeview's usual."""
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
    if notices.mode("due_soon") != "off":
        items = [i for i in items if i[2] - t > DUE_SOON_SEC]
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
        c = _deadline(g[0][0], g[0][1], g[0][2], events) if len(g) == 1 else _deadlines(g, events)
        found.setdefault(c.subject, c)
    return list(found.values())


def _due_name(title: str) -> str:
    """The thing that is due, from a due-time event's title: "POL 101 — Unit 6 Form H due" is "POL 101 — Unit 6
    Form H"."""
    name = re.sub(r"\s*\b(is )?(due|(final )?deadline|submission)\b.*$", "", title, flags=re.I).strip(" -:—–")
    return name or title


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _deadlines(group: list[tuple[str, str, float]], events: list[dict]) -> Candidate:
    """Several related things due the same evening, as one question."""
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
    c = _deadline(what, sources[0], due, events)
    c.subject = f"deadline:{base._slug(label or names[0])}:{notices._et(due).strftime('%Y%m%d')}"
    c.evidence = (f"{count} {label + ' ' if label else ''}items are due {when(due)}, according to {_join(sources)}: "
                  f"{_join(names)}. I don't see time set aside for them or anything showing they are done.")
    c.facts = {"what": what, "when": when_full(due), "source": ", ".join(sources)}
    c.question = c.question.replace("for it, or is it already in?", "for them, or are they already in?")
    c.lock = f"{count} {label + ' ' if label else ''}items are due {notices.day(due)}. Block time?"[:60]
    first = c.options[0]
    if first.text == "Find time for it":
        first = Option("Find time for them", first.kind, say=first.say)
    c.options = [first, Option("Already submitted", "close"),
                 Option("I'll handle them", "done", follow="Okay. I'll leave them with you.")]
    return c


def free_slot(events: list[dict], after: float, before: float, minutes: int = 30) -> float | None:
    """The first half hour between 8 a.m. and 10 p.m., starting an hour from now, that nothing on the calendar
    overlaps and that ends before the deadline."""
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


def _deadline(name: str, source: str, due: float, events: list[dict]) -> Candidate:
    slot = free_slot(events, _now(), due)
    if slot:
        first = Option(f"Block {when(slot)}", "act",
                       say=f"Please put a 30-minute block on my calendar {when_full(slot)} to work on {P(name)}, which is "
                           f"due {when_full(due)}.")
        question = f"Want a 30-minute block {when(slot)} for it, or is it already in?"
    else:
        first = Option("Find time for it", "act",
                       say=f"Please find 30 minutes on my calendar before {when_full(due)} for {P(name)} and block it.")
        question = "Want me to find 30 minutes for it, or is it already in?"
    return Candidate(
        noticer="school_deadline", subject=f"deadline:{base._slug(name)}:{notices._et(due).strftime('%Y%m%d')}",
        owner="school" if "school" in config.BOTS_BY_ID else "default", version=str(int(due)),
        evidence=f"{name[:1].upper() + name[1:]} is due {when(due)}, according to {source}, and I don't see time "
                 "set aside for it or anything showing it is done.",
        question=question, lock=f"{short(name, 28)} is due {notices.day(due)}. Block time?",
        facts={"what": name, "when": when_full(due), "source": source}, deadline=due,
        options=[first, Option("Already submitted", "close"),
                 Option("I'll handle it", "done", follow="Okay. I'll leave it with you.")])


# MARK: 15. Schoolwork due within hours that the course site doesn't show as turned in

DUE_SOON_SEC = 3 * 3600
DUE_SOON_MIN_SEC = 10 * 60


def _due_items(src: base.Sources, t: float, start: float, end: float) -> list[tuple[str, float, dict | None]]:
    """(name, due, its Trello card or None) for schoolwork due between start and end, from Trello, the calendar and
    School's notes. An all-day item has no hour to be due at, so it is school_deadline's alone."""
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


def _left(due: float) -> str:
    """"in about two hours", "in 40 minutes"."""
    mins = max(1, round((due - _now()) / 60))
    if mins < 50:
        return f"in {mins} minutes"
    halves = max(2, round(mins / 30))
    words = {2: "an hour", 3: "an hour and a half", 4: "two hours", 5: "two and a half hours", 6: "three hours"}
    return f"in about {words.get(halves, f'{halves // 2} hours')}"


def _has_mac() -> bool:
    """A Mac is paired as the one Zips may use (the Mac app's link), so "open what I need on my Mac" can happen."""
    from . import store
    try:
        return store.db().execute("SELECT 1 FROM devices WHERE mac_link=1 LIMIT 1").fetchone() is not None
    except Exception:
        return False


async def due_soon(src: base.Sources) -> list[Candidate]:
    """Colin's own example (Oct 10): an hour or so before something is due, the Zip has looked at the course site
    and asks whether it is done, with one answer that closes the loop on Trello and one that opens what he needs on
    his Mac. The course site is read by the School Zip (schoolcheck.py); the question waits for its answer, and an
    item it saw as turned in is not asked about."""
    from . import schoolcheck
    t = _now()
    items = _due_items(src, t, t + DUE_SOON_MIN_SEC, t + DUE_SOON_SEC)
    if not items:
        return []
    groups_now = await src.board()
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
        names = [n for n, _, _ in g]
        due = min(d for _, d, _ in g)
        subject = f"due:{base._slug(' '.join(sorted(courses(' '.join(names)))) or names[0])}:" \
                  f"{notices._et(due).strftime('%Y%m%d%H%M')}"
        res = schoolcheck.result(subject)
        if res is None:
            if not notices.row(subject):
                busy = any((x.get("assignee") or "default") == schoolcheck.checker()
                           for x in groups_now.get("working") or [])
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
        found.append(_due_soon(subject, g, left, status, res.get("site") or "", due, cards[0] if cards else None))
    return found


def _due_soon(subject: str, group: list, left: list[str], status: dict, site: str, due: float,
              card: dict | None) -> Candidate:
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
    page = f"the assignment page ({P(link, 200)})" if link else "the assignment page"
    if _has_mac():
        not_done = Option("Not done yet. Open what I need on my Mac", "act",
                          say=f"Not done yet: {P(what, 160)}, due {due_at}. Please open what I need for "
                              f"{'them' if several else 'it'} on my Mac: {page} and my draft or notes if you can find "
                              "them.")
    else:
        not_done = Option("Not done yet. Help me finish it", "act",
                          say=f"Not done yet: {P(what, 160)}, due {due_at}. Please pull up what I need for "
                              f"{'them' if several else 'it'}: {page} and my draft or notes if you can find them.")
    if card:
        finished = Option("It's finished. Mark it done on Trello", "act",
                          say=f"It's finished: {P(what, 160)}. Please mark the Trello card \"{P(card.get('name'), 120)}\" "
                              "done.")
        options = [finished, not_done]
    else:
        options = [not_done, Option("It's finished", "done", follow="Good. I won't ask about it again.")]
    owner = "school" if "school" in config.BOTS_BY_ID else "default"
    return Candidate(
        noticer="due_soon", subject=subject, owner=owner, version=str(int(due)),
        evidence=f"{head} {found}", question="Are they done?" if several else "Is it done?",
        lock="Something is due soon. Is it done?",
        facts={"what": what, "when": due_at, "source": f"{site_name}, checked {notices.clock(_now())}"},
        consequence=("They are late" if several else "It is late") + " if not turned in by then.", deadline=due,
        haiku=False,
        options=options)


def _course_label(names: list[str]) -> tuple[str, list[str]]:
    """("POL 101", ["Unit 6 Form H", "Unit 6 Form C"]) when every name shares a course, else ("", names)."""
    found = [courses(n) for n in names]
    shared = set.intersection(*found) if found and all(found) else set()
    if not shared or len(names) < 2:
        return "", names
    m = COURSE.search(names[0])
    label = m.group(0) if m else sorted(shared)[0]
    return label, [re.sub(rf"^\s*{re.escape(label)}\s*[—–:,-]*\s*", "", n).strip() or n for n in names]


# MARK: 6. A calendar overlap or an impossible gap

CONFLICT_DAYS = 7
GAP_SEC = 30 * 60


def _same_place(a: str, b: str) -> bool:
    na, nb = (" ".join(re.findall(r"[a-z0-9]+", x.lower())) for x in (a, b))
    return na == nb or na in nb or nb in na


async def conflict(src: base.Sources) -> list[Candidate]:
    """Clashes between real commitments only, at most one question a day: the first clash that day, which names
    how many more there are."""
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
    """from 3 to 4 p.m., from 11:30 a.m. to 4 p.m."""
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
        consequence=f"{who.first()} can't be at both.", deadline=min(a["start"], b["start"]), haiku=False,
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
        consequence=f"{who.first()} may not make it in time.", deadline=b["start"], haiku=False,
        options=[Option("Help me fix it", "act",
                        say=f"{P(a['title'])} ends at {clock(a['end'])} and {P(b['title'])} starts at {clock(b['start'])} "
                            f"in a different place on {notices._et(a['end']).strftime('%A, %b %-d')}. Please suggest "
                            "a fix, and move my own events if that is all it takes."),
                 Option("That's enough time", "done", follow="Okay. I'll leave it as it is.")])


# MARK: 7. A meeting with someone outside needs prep

def meeting(e: dict) -> bool:
    """A meeting with someone outside Colin's team: people invited from outside, or a meeting word in the title. Not
    a shift, a class, church, a deadline, or a health appointment."""
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
    """Something Colin said he would do for this event, in his chat with Zero or the owner, in the last two weeks."""
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
    ask = (f"Please have {owner_name} make a one-page prep for {P(e['title'])}{P(with_who)} {when_full(e['start'])}. Use "
           "the prep format from my notes if there is one, and save it in their work folder.")
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


# MARK: 14. A meeting or a call just ended

FOLLOW_WINDOW_SEC = 30 * 60
NEXT = re.compile(r"\*\*Next:\*\*\s*(.+)", re.I)


def _account_for(addr: str) -> str | None:
    """The person's mailbox for writing to this address: one at the same domain, else the default one."""
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
    """What Colin said about this meeting since an hour before it, in his chats with Zero and the owner. Follow-ups
    come from his words, never from a guess."""
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


def _followup(e: dict, owner: str, to: dict | None, account: str, material: str, prepared: bool) -> Candidate:
    to_who = (to.get("name") or to["email"]) if to else people_line(e)
    first = to_who.split()[0] if to_who else ""
    owner_name = notices._bot_name(owner)
    ask = (f"My {P(e['title'])} just ended. Ask me what came up, then draft a short follow-up"
           + (f" to {P(to_who)} <{to['email']}>" if to else "") + " for me to approve. Don't send anything yet.")
    c = Candidate(
        noticer="followup", subject=f"followup:{e['id']}", owner=owner,
        evidence=f"{e['title']} ended at {clock(e['end'])}." + (" You mentioned it in chat, so I can draft the recap."
                                                                   if material else ""),
        question=f"Want {owner_name} to {'send the recap below' if prepared else 'draft a follow-up'}"
                 + (f" to {first}?" if first else "?"),
        lock=f"{short(e['title'], 30)} ended. Follow up?",
        facts={"what": e["title"], "who": f"{to_who} <{to['email']}>" if to else to_who, "when": when_full(e["end"])},
        deadline=e["end"] + 2 * 3600, event_timed=True, excerpt=material,
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
        evidence=f"Your call with {zip_name} ended. The next step from it: {next_step}.",
        question=f"Want {zip_name} to take care of it?",
        facts={"what": next_step, "who": zip_name, "when": "just now"}, event_timed=True,
        deadline=float(call["at"]) + 4 * 3600, haiku=False,
        options=[Option("Go ahead", "act",
                        say=f"From my call with {zip_name}: {P(next_step, 200)}. Please take care of it, and show me anything "
                            "that needs my okay first."),
                 Option("I'll handle it", "done", follow="Okay. I'll leave it with you.")])


# MARK: 11. A window opens or closes

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


def windows_in(note: str, ref: float) -> list[tuple[str, str, float]]:
    """(what, "opens" or "closes", day) for each dated window in a note."""
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
    found: dict[str, Candidate] = {}
    for bot in ("school", "career"):
        if bot not in config.BOTS_BY_ID:
            continue
        for note in src.notes_of(bot):
            for what, kind, day_ts in windows_in(note, t - 60 * 86400):
                if not 0 <= notices.days_ahead(day_ts) <= WINDOW_DAYS:
                    continue
                c = _window(what, kind, day_ts, bot)
                found.setdefault(c.subject, c)
    return list(found.values())


def _window(what: str, kind: str, day_ts: float, owner: str) -> Candidate:
    on = notices._et(day_ts).strftime("%A, %b %-d")
    deadline = day_ts + (9 * 3600 if kind == "opens" else 23 * 3600 + 55 * 60)
    owner_name = notices._bot_name(owner)
    return Candidate(
        noticer="window", subject=f"window:{base._slug(what)}:{kind}:{notices._et(day_ts).strftime('%Y%m%d')}",
        owner=owner, evidence=f"{what} {kind} {on}, according to {owner_name}'s notes.",
        question=f"Want {owner_name} to get you ready for it?",
        lock=f"{short(what, 34)} {kind} {notices.day(day_ts)}"[:60],
        facts={"what": what, "when": f"{kind} {on}"}, deadline=deadline,
        options=[Option("Get me ready for it", "act",
                        say=f"{P(what)} {kind} {on}. Please have {owner_name} get me ready for it and tell me what I "
                            "need to do and when."),
                 Option("I've got it", "done", follow="Okay. I'll leave it with you.")])


FINDERS = {"school_deadline": school_deadline, "due_soon": due_soon, "conflict": conflict, "prep": prep,
           "followup": followup, "window": window}
