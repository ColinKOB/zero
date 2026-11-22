""















from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta

from . import config, factdates, facts, home, who

log = logging.getLogger("taffy.shifts")

BACK_DAYS = 1
AHEAD_DAYS = 14
OVERLAP_DAYS = 8
LONG_ENOUGH_SEC = 3600
_PLACE = re.compile(r"\bat ((?:[Tt]he )?[A-Z][\w'&-]*(?: [A-Z][\w'&-]*)*)")


def _told() -> list[dict]:
    ""
    where, args = facts._world(None)
    holds, now_arg = facts.live()
    return [dict(r) for r in facts._db().execute(
        f"SELECT * FROM facts WHERE {holds} AND {where} AND visibility='team' AND status='current' "
        "ORDER BY observed_at, id", now_arg + args)]


def _on_calendar(real: list[dict]) -> set:
    ""
    from . import calcache
    return {datetime.fromtimestamp(e["start"], home.TZ).date() for e in real if calcache.SHIFT.search(e["title"])
            and (e.get("all_day") or e["end"] - e["start"] >= LONG_ENOUGH_SEC)}


def events(real: list[dict], t: float) -> list[dict]:
    ""
    try:
        told = _told()
    except sqlite3.Error:
        log.exception("could not read the facts for a weekly shift")
        return []
    weekly, off = {}, _on_calendar(real)
    for f in told:
        off |= factdates.days_off(f["text"], f["observed_at"])
        if shift := factdates.weekly(f["text"]):
            weekly |= {day: (f, *shift[1:]) for day in shift[0]}
    today = datetime.fromtimestamp(t, home.TZ).date()
    out = []
    for n in range(-BACK_DAYS, AHEAD_DAYS) if weekly else ():
        day = today + timedelta(days=n)
        if day.weekday() not in weekly or day in off:
            continue
        f, start, end = weekly[day.weekday()]
        if day < datetime.fromtimestamp(f["observed_at"], home.TZ).date():
            continue
        place = _PLACE.search(f["text"])
        out.append({"id": f"shift-{f['id']}-{day}", "title": f"Shift at {place.group(1)}" if place else "Work shift",
                    "start": datetime.combine(day, start, home.TZ).timestamp(),
                    "end": datetime.combine(day, end, home.TZ).timestamp(),
                    "description": f["text"], "recurring": True, "shift_fact": f["id"]})
    return out


def during(start: float, end: float) -> dict | None:
    ""
    from . import calcache, notices
    return next((e for e in notices.calendar_events() if calcache.busy_kind(e) == "shift" and e["start"] < end
                 and e["end"] > start), None)


def _can_overlap(e: dict) -> bool:
    ""

    from . import calcache, calendar_noticers as cn
    return (cn.timed(e) and not cn.free(e) and not cn.deadline_like(e) and not cn.FLEX_WORDS.search(e["title"])
            and calcache.busy_kind(e) != "shift")


def lines(bot_id: str, t: float) -> list[str]:
    ""
    if config.is_sandbox(bot_id):
        return []
    from . import calcache, calendar_noticers as cn, notices
    every = notices.calendar_events()
    shifts = [e for e in every if calcache.busy_kind(e) == "shift" and e["end"] > t]
    out, seen = [], set()
    for e in shifts:
        fid = e.get("shift_fact")
        if fid and fid not in seen:
            seen.add(fid)
            out.append(f"- Next on {notices._et(e['start']).strftime('%a %b %-d')}: {e['description']} [{fid}] It repeats "
                       "every week. Keep plans, study blocks and leave-by times clear of it, and say so plainly when "
                       f"something overlaps it. When {who.first()} says it changed or ended, save that with save_fact, "
                       f"replaces {fid}.")
    for e in shifts:
        if e["start"] >= t + OVERLAP_DAYS * 86400:
            continue
        for other in every:
            if other["start"] < e["end"] and other["end"] > e["start"] and _can_overlap(other):
                out.append(f"- {notices._et(e['start']).strftime('%a %b %-d')}: {e['title']} {cn._range(e)} overlaps "
                           f"{other['title']} {cn._range(other)}; name this overlap in any plan or leave-by time for "
                           "that day.")
    return out
