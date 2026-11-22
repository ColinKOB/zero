""














from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import config, home, loopcheck, made, notices, openloops, store, watcher, who

DAYS = 7
STUCK_DAYS = 7
STUCK_MAX = 5
STEP_MAX = 180
MAX_CHARS = 3600
WORK_HOURS = 2


_ASKS = re.compile(r"\bweek ahead\b|\b(?:this|my|the) week(?:'s)?\b.{0,40}\b(?:look|calendar|schedule|plans?|due|"
                   r"have|got)\b|\b(?:calendar|schedule|plans?|due|coming up|happening|going on)\b.{0,40}"
                   r"\b(?:this|my|the) week\b", re.I)


def _day(d) -> str:
    return d.strftime("%a %b %-d")


def stuck(t: float) -> list[dict]:
    ""



    out = []
    for r in store.db().execute("SELECT * FROM open_loops WHERE status='open' ORDER BY opened_at, id"):
        row, bot = dict(r), config.BOTS_BY_ID.get(r["bot_id"])
        if not bot or bot.sandbox or not loopcheck.waits_on_person(row):
            continue
        since = loopcheck.advanced_at(row)
        if t - since >= STUCK_DAYS * 86400 and not loopcheck.put_off(row, since) and \
                (notices.row(loopcheck.subject(row)) or {}).get("state") != "open":
            out.append({**row, "since": since})
    return sorted(out, key=lambda r: (r["since"], r["opened_at"], r["id"]))[:STUCK_MAX]


def _requests(start, end) -> list[str]:
    ""
    from . import teams
    out = []
    for r in store.db().execute("SELECT n.*, t.name AS team FROM team_needs n JOIN teams t ON t.id=n.team_id "
                                "WHERE n.state='open' AND t.state='active' ORDER BY n.id").fetchall():
        at = teams.need_due(r)
        day = datetime.fromtimestamp(at, home.TZ).date() if at else None
        if day and start <= day < end:
            out.append((at, f"- {r['team']}: {r['title'].rstrip('.?')}, due {_day(day)}"))
    lines = [line for _, line in sorted(out, key=lambda x: x[0])]
    return lines[:STUCK_MAX] + ([f"- {len(lines) - STUCK_MAX} more after these"] if len(lines) > STUCK_MAX else [])


def _loop_steps(start, end) -> list[tuple]:
    ""
    out = []
    for r in store.db().execute("SELECT * FROM open_loops WHERE status='open' ORDER BY id"):
        row, bot = dict(r), config.BOTS_BY_ID.get(r["bot_id"])
        if not bot or bot.sandbox or loopcheck.blocker(row).get("on") == "parked":
            continue
        days = sorted({d.date() for d, i in loopcheck._dates(row) if i > len(row["summary"]) and start <= d.date() < end})
        if days:
            out.append((days[0], f"{row['topic']} ({bot.name}'s note): {openloops._clean(row['next_step'], STEP_MAX)}"))
    return out


def _events(start, end) -> list[tuple]:
    from . import calcache, calendar_noticers, factkeeper
    out = []
    first = datetime.combine(start, datetime.min.time(), home.TZ).timestamp()
    last = datetime.combine(end, datetime.min.time(), home.TZ).timestamp()
    for e in notices.calendar_events():
        if e["start"] >= last or e["end"] <= first or calcache.declined(e):
            continue
        day = datetime.fromtimestamp(max(e["start"], first), home.TZ).date()
        if e.get("all_day"):
            noted = calendar_noticers.noted_dates(e, first)
            out.append((day, f"{e['title']} (all day{f'; {noted}' if noted else ''}){factkeeper.calendar_note(e, "default")}"))
        else:
            place = f", {e['location']}" if e.get("location") else ""
            out.append((day, f"{notices.clock(e['start'])} {e['title']}{place}{factkeeper.calendar_note(e, "default")}"))
    return out


def _busy(start, end) -> dict:
    ""
    from . import calcache
    hours: dict = {}
    for e in notices.calendar_events():
        day = datetime.fromtimestamp(e["start"], home.TZ).date()
        if not e.get("all_day") and not e.get("free") and not calcache.declined(e) and start <= day < end:
            hours[day] = hours.get(day, 0) + (e["end"] - e["start"]) / 3600
    return hours


def _classes(start, end) -> list[str]:
    ""



    from . import school
    names, busy = school.class_names(), _busy(start, end)
    due: dict[str, dict] = {}
    undated: dict[str, list] = {}
    from . import settled
    for d in school.deadlines():
        if d.get("id") and settled.deadline_settled(d):
            continue
        day = datetime.fromtimestamp(d["due"], home.TZ).date() if d.get("due") else None
        name = re.split(r"\s+-\s+\S+:\s*", d.get("course_name") or "", maxsplit=1)[-1]
        names.setdefault(d["course"], " ".join(re.sub(r"\b(Fall|Spring|Summer|Winter)\s+\d{4}\s*$", "", name).split()))
        title = re.sub(r"\s*\(.*?\)", "", d["title"].split(":", 1)[0]).strip()
        if d.get("also_due"):

            other = datetime.fromtimestamp(d["also_due"], home.TZ).date()
            title += f" (that day is from {d.get('date_from')}; {d.get('also_from')} says {_day(other)})"
        if day and start <= day < end:
            key = (day, d.get("dated_by") == "range", notices.clock(d["due"]))
            due.setdefault(d["course"], {}).setdefault(key, []).append(title)
        elif d.get("dated_by") == "none":
            undated.setdefault(d["course"], []).append(title)
    out = []
    for course in sorted(names):
        label = f"{course} ({names[course]})" if names[course] and names[course] != course else course
        parts = []
        for (day, open_range, clock), titles in sorted(due.get(course, {}).items()):
            when = f"the unit runs through {_day(day)} with no due date posted" if open_range else \
                f"due {_day(day)} at {clock}"
            days = [start + timedelta(days=i) for i in range((day - start).days)] or [day]
            work = min(days, key=lambda x: (busy.get(x, 0), x))
            free = "nothing timed on the calendar" if not busy.get(work) else f"{busy[work]:.0f} hours booked"
            busy[work] = busy.get(work, 0) + WORK_HOURS
            parts.append(f"{', '.join(titles)}: {when}; do it {_day(work)} ({free})")
        if course in undated:
            parts.append(f"{', '.join(undated[course])}: the course site posts no date")
        out.append(f"- {label}: " + (". ".join(parts) or "nothing due on the course site this week") + ".")
    return out


def text(t: float | None = None) -> str:
    ""
    t = notices.now() if t is None else t
    first, today = who.first(), datetime.fromtimestamp(t, home.TZ).date()
    end = today + timedelta(days=DAYS)
    requests, waiting = _requests(today, end), stuck(t)
    dated = _events(today, end) + _loop_steps(today, end)
    classes = _classes(today, end)
    if not requests and not waiting and not dated and not classes:
        return ""
    lines = [f"[The week ahead, {_day(today)} to {_day(end - timedelta(days=1))}, from the calendar, the team's open "
             f"work and the course sites. Lead with one thing. Unless something is due today, make it the "
             f"first request or project below that waits on {first}, with its next step already prepared wherever you "
             f"can do and undo it yourself (a draft, a filled form left for {first} to press, a plan). The rest of the week "
             "comes after it, one item at a time, with one line per class saying what is due and the day to do it.]"]
    if requests:
        lines.append(f"Requests with a date, waiting on {first}:")
        lines += requests
    if waiting:
        lines.append(f"Waiting on {first} longest, with nothing moving them for a week or more:")
        for r in waiting:
            name = config.BOTS_BY_ID[r["bot_id"]].name
            lines.append(f"- {r['topic']} ({name}, since {datetime.fromtimestamp(r['since'], home.TZ).strftime('%b %-d')}): "
                         f"{openloops._clean(r['next_step'], STEP_MAX)}")
    if dated:
        lines.append("Dated this week:")
        by_day: dict = {}
        for day, line in dated:
            by_day.setdefault(day, []).append(line)
        lines += [f"- {_day(day)}: " + "; ".join(i.rstrip(".") for i in items) for day, items in sorted(by_day.items())]
    if classes:
        lines.append("Classes this week, from the course sites, each with a day to do its work:")
        lines += classes
    out = "\n".join(lines)
    return out if len(out) <= MAX_CHARS else out[:MAX_CHARS].rsplit("\n", 1)[0]


def brief_job(bot_id: str, session_id: str) -> bool:
    ""

    if bot_id != config.LEAD.id or not (session_id or "").startswith("cron_"):
        return False
    job = watcher._jobs_of(config.HERMES_HOME / "cron" / "jobs.json").get(made._job(session_id)) or {}
    return "brief" in str(job.get("name") or "").lower()


def brief_turn(bot_id: str, session_id: str, t: float | None = None) -> bool:
    ""
    t = notices.now() if t is None else t
    return datetime.fromtimestamp(t, home.TZ).weekday() == 0 and brief_job(bot_id, session_id)


def for_message(bot_id: str, message: str) -> str:
    ""
    if bot_id != config.LEAD.id or not _ASKS.search(message or ""):
        return ""
    return text()
