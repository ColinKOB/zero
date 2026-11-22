""

















from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from . import home

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_WRITTEN = (r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?" + _MONTH +
            r"\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)(?:,?\s+(\d{4}))?")
_DATE = re.compile(r"\b" + _WRITTEN, re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_WORD = re.compile(r"\b(" + "|".join(WEEKDAYS) + r"|tomorrow|tonight|today)\b", re.I)
_REPEATS = re.compile(r"\b(every|each|most|usually|always)\s+$", re.I)

_KNOWN_AT = (re.compile(r"\bas of [^,:;)]*", re.I),
             re.compile(r"\([^()]*\)"),
             re.compile(r"^\s*(?:on\s+)?" + _WRITTEN + r"\s*[,:]", re.I),
             re.compile(r"\b(?:set|since|created|made|saved|said|noted|told)\s+(?:on\s+)?" + _WRITTEN, re.I))
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?|\b(noon|midday|midnight)\b", re.I)
_NIGHT = re.compile(r"\b(night|tonight|overnight|evening|all day|weekend)\b", re.I)
TIME_GRACE = timedelta(hours=1)

_NAMED_DAY = r"\b(?:" + "|".join(WEEKDAYS) + r")s?\b"
_SHIFT = re.compile(r"\bshifts?\b|\bworks\b(?! on\b)", re.I)


_ONE_TIME = re.compile(r"\b(?:this|next|last|today|tonight|tomorrow|extra|picked up|covers?|covering|covered|swapped|"
                       r"had|was|off|wants?|considering|might|maybe|if|asked|requested|offered)\b", re.I)
_CLOCK = r"(?:(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s?m\b\.?)?|(noon))"
_RANGE = re.compile(r"(?<![\d:-])" + _CLOCK + r"\s*(?:to|until|till|-|–|—)\s*" + _CLOCK, re.I)
_WORK = re.compile(r"\b(?:ups|shifts?|work)\b", re.I)
_OFF = re.compile(rf"(?:{_NAMED_DAY}|\b{_MONTH}\s+\d|\d{{4}})[^.;]{{0,25}}\boff\b|\boff\b[^.;]{{0,25}}{_NAMED_DAY}|"
                  r"\b(?:covered|covering|swapped|not working|no shift|cancell?ed|called out)\b", re.I)


def _blank(text: str, pattern: re.Pattern) -> str:
    ""
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _written(m: re.Match, seen: date) -> date | None:
    month = MONTHS.index(m.group(1).lower()[:3]) + 1
    year = int(m.group(3)) if m.group(3) else seen.year
    try:
        d = date(year, month, int(m.group(2)))
    except ValueError:
        return None
    if not m.group(3) and d < seen - timedelta(days=120):
        d = d.replace(year=year + 1)
    return d


def _day_word(word: str, seen: date) -> date:
    w = word.lower()
    if w == "tomorrow":
        return seen + timedelta(days=1)
    if w in ("today", "tonight"):
        return seen
    return seen + timedelta(days=(WEEKDAYS.index(w) - seen.weekday()) % 7)


def days(text: str, observed: float, past: bool = False) -> list[tuple[int, int, date]]:
    ""

    seen = datetime.fromtimestamp(observed, home.TZ).date()
    t = text or ""
    for p in _KNOWN_AT:
        t = _blank(t, p)
    found = []
    for m in _DATE.finditer(t):
        d = _written(m, seen)
        if d:
            found.append((m.start(), m.end(), d))
    for m in _ISO.finditer(t):
        try:
            found.append((m.start(), m.end(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            continue
    t = _blank(_blank(t, _DATE), _ISO)
    for m in _DAY_WORD.finditer(t):
        if not _REPEATS.search(t[:m.start()]):
            found.append((m.start(), m.end(), _day_word(m.group(1), seen)))
    return sorted(f for f in found if past or f[2] >= seen)


def all_past(text: str, observed: float, today: date) -> bool:
    ""


    named = days(text, observed, past=True)
    return bool(named) and max(d for _, _, d in named) < today


def _end_of(d: date) -> datetime:
    return datetime.combine(d + timedelta(days=1), time(0), home.TZ)


def _hours(m: re.Match) -> tuple[time, time] | None:
    ""

    h1, m1, half1, noon1, h2, m2, half2, noon2 = m.groups()
    if not (half2 or noon2) or int(h1 or 12) > 12 or int(h2 or 12) > 12 or int(m1 or 0) > 59 or int(m2 or 0) > 59:
        return None
    end = time(12) if noon2 else time(int(h2) % 12 + (12 if half2.lower() == "p" else 0), int(m2 or 0))
    if noon1:
        start = time(12)
    else:
        pm = (half1 or half2 or "a").lower() == "p"
        start = time(int(h1) % 12 + (12 if pm else 0), int(m1 or 0))
        if not half1 and start >= end:
            start = time(int(h1) % 12 + (0 if pm else 12), int(m1 or 0))
    return (start, end) if start < end else None


def weekly(text: str) -> tuple[list[int], time, time] | None:
    ""


    t = text or ""
    known = t
    for p in _KNOWN_AT:
        known = _blank(known, p)
    if not _SHIFT.search(t) or _ONE_TIME.search(known) or _DATE.search(known) or _ISO.search(known):
        return None
    named = sorted({WEEKDAYS.index(d.lower().rstrip("s")) for d in re.findall(_NAMED_DAY, known, re.I)})
    hours = next(filter(None, map(_hours, _RANGE.finditer(known))), None)
    return (named, *hours) if named and hours else None


def days_off(text: str, observed: float) -> set[date]:
    ""

    if not _WORK.search(text or "") or not _OFF.search(text or ""):
        return set()
    return {d for _, _, d in days(text, observed)}


def span(text: str, observed: float) -> tuple[float | None, float | None]:
    ""
    if weekly(text):
        return None, None
    found = days(text, observed)
    if not found:
        return None, None
    first = min(d for _, _, d in found)
    last = max(d for _, _, d in found)
    at = max(s for s, _, d in found if d == last)
    tail = (text or "")[at:]
    end = _end_of(last)
    if not _NIGHT.search(tail):
        clock = []
        for m in _TIME.finditer(tail):
            if m.group(4):
                if m.group(4).lower() == "midnight":
                    clock = []
                    break
                clock.append(time(12))
                continue
            hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
            minute = int(m.group(2) or 0)
            if hour < 24 and minute < 60:
                clock.append(time(hour, minute))
        if clock:
            end = min(end, datetime.combine(last, max(clock), home.TZ) + TIME_GRACE)
    start = datetime.combine(first, time(0), home.TZ)
    return start.timestamp(), end.timestamp()


def parse_until(value, observed: float) -> float | None:
    ""

    s = " ".join(str(value or "").split())
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?", s)
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        at = (datetime.combine(d, time(int(m.group(4)), int(m.group(5))), home.TZ) if m.group(4)
              else _end_of(d))
    except ValueError:
        return None
    ts = at.timestamp()
    return ts if abs(ts - observed) <= 550 * 86400 else None


def day_of(ts: float) -> tuple[int, int]:
    ""
    d = datetime.fromtimestamp(ts - 1, home.TZ)
    return d.month, d.day


def ended_words(until: float) -> str:
    ""
    return datetime.fromtimestamp(until - 1, home.TZ).strftime("%b %-d")
