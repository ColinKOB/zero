""






















from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import time

import httpx

from urllib.parse import quote

from . import (calcache, config, facts, mail, mailcursor, notices, openloops, shifts, shortreply, store, watcher, work,
               workmail)

log = logging.getLogger("taffy.meetprep")

INTERVAL = 60
LOOKAHEAD_SEC = 6 * 3600
SHOW_SEC = 3600
SHOW_AFTER_SEC = 2 * 3600
KEEP_SEC = 86400
TRAFFIC = 1.2
MARGIN_SEC = 10 * 60
DEVICE_MARGIN_SEC = 5 * 60
MAX_DRIVE_SEC = 4 * 3600
ROUTE_RETRY_SEC = 3600
LATE_SEC = 15 * 60
HEADS_UP_SEC = 15 * 60
PREP_MAX = 120
ROUTINE_SEC = 14 * 86400
GEOCODE_TRIES = 4
EMAILS = 3
NOTES = 6
CARD_LEAD_SEC = 90 * 60
CARD_MORNING_HOUR = 7
CARD_NOTES = 3
MAPS = "https://www.google.com/maps/dir/?api=1&destination="
AGENT = "ZeroHomeHub/1.0 (personal calendar helper)"
GEOCODER = "https://nominatim.openstreetmap.org/search"
ROUTER = "https://router.project-osrm.org/route/v1/driving/"
_generation = 0
_LIVES = re.compile(r"\blives? (?:in|at|on) (.+?)\.?$", re.I)

_NAMED = re.compile(r"\bwith ([A-Z][a-z'-]+(?: [A-Z][a-z'-]+)*)")


_JOIN = re.compile(r"https://(?:[\w-]+\.)*(?:teams\.microsoft\.com/(?:meet|l/meetup-join)/|zoom\.us/(?:j|my|w)/|"
                   r"meet\.google\.com/[a-z]|webex\.com/(?:meet|join|[\w-]+/j\.php))[^\s<>\"'()\[\]]*", re.I)
_SERVICES = (("teams.microsoft", "Teams"), ("zoom.us", "Zoom"), ("meet.google", "Google Meet"), ("webex", "Webex"))
_PREP = re.compile(r"(bring|print|wear|ask|remember|prepare|prep|review|have|take|send|confirm|topic|agenda|discuss)\b",
                   re.I)



_CALENDAR_ADDRESS = re.compile(r"@(?:group\.calendar|group\.v\.calendar|import\.calendar)\.google\.com$", re.I)




_NAME = r"[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:['’-][^\W\d_]+)*\.?"
_MEETING_NAME = re.compile(
    r"(?i:(?:\d{1,3}[- ]min(?:ute)?s?[- ]|(?:intro|introductory|quick|first|phone|video|zoom|teams|discovery|kickoff|"
    r"follow[- ]up|catch[- ]up|coffee|lunch|job|advising|team|project|planning|sales|strategy)[- ]){0,2}"
    r"(?:call|chat|meeting|interview|intro|coffee|lunch|dinner|breakfast|demo|sync|appointment|consultation))"
    rf"(?: with (?P<names>{_NAME}(?: (?:and )?{_NAME}){{0,2}}))?")

_ORDER_WORD = re.compile(r"\b(?:you|your|please|now|urgent|confirm|verify|login|log|sign|click|pay|send|wire|transfer|"
                         r"password|account|update|reply|text|call)\b", re.I)


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS work_meetings (
            key TEXT PRIMARY KEY, body TEXT NOT NULL, start REAL NOT NULL, end_at REAL NOT NULL,
            drive_sec INTEGER, drive_source TEXT NOT NULL DEFAULT '', routed_at REAL,
            address TEXT NOT NULL DEFAULT '', pushed_at REAL, join_url TEXT NOT NULL DEFAULT '',
            join_checked TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS work_places (query TEXT PRIMARY KEY, body TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS work_meeting_visits (
            place TEXT NOT NULL, title TEXT NOT NULL, last_start REAL NOT NULL, PRIMARY KEY(place, title)
        );
    """)

    columns = {r[1] for r in c.execute("PRAGMA table_info(work_meetings)")}

    for name, definition in (("join_url", "TEXT NOT NULL DEFAULT ''"), ("join_checked", "TEXT NOT NULL DEFAULT '[]'"),
                             ("card_id", "TEXT NOT NULL DEFAULT ''"), ("carded_at", "REAL"), ("card_closed", "REAL")):
        if name not in columns:
            c.execute(f"ALTER TABLE work_meetings ADD COLUMN {name} {definition}")


class Stale(Exception):
    ""


def cancel_reads() -> None:
    ""

    global _generation
    with store._lock:
        _generation += 1


def _key(e: dict) -> str:
    return f"{e.get('calendar_id') or e.get('calendar') or ''}|{e['id']}"


def wanted(e: dict) -> bool:
    ""

    if e.get("all_day") or e.get("free") or calcache.declined(e) or str(e.get("status") or "").lower() == "cancelled":
        return False
    return bool(calcache.outside_people(e) or calcache.physical_place(e) or own_meeting(e))


def own_meeting(e: dict) -> bool:
    ""




    from . import calendar_noticers
    title = str(e.get("title") or "")
    if e.get("recurring") or calendar_noticers.SELF_BLOCK.search(title) or not calendar_noticers.meeting(e):
        return False
    return bool(_NAMED.search(title) or str(e.get("description") or "").strip())


def leave_by(row: dict) -> float | None:
    ""


    if not row.get("drive_sec"):
        return None
    device = row["drive_source"] == "device"
    at = math.floor((row["start"] - (row["drive_sec"] + DEVICE_MARGIN_SEC if device else
                                     row["drive_sec"] * TRAFFIC + MARGIN_SEC)) / 300) * 300
    if not device and any(calcache.physical_place(json.loads(r["body"])) for r in store.db().execute(
            "SELECT body FROM work_meetings WHERE key!=? AND start<? AND end_at>?", (row["key"], row["start"], at))):
        return None
    return at




_geocoded_at = 0.0


def _get(url: str, params: dict) -> dict | list:
    global _geocoded_at
    if url == GEOCODER:
        time.sleep(max(0.0, _geocoded_at + 1.1 - time.time()))
        _geocoded_at = time.time()
    r = httpx.get(url, params=params, headers={"User-Agent": AGENT}, timeout=10)
    r.raise_for_status()
    return r.json()


_NOT_A_PLACE = re.compile(r"\b(room|rm|suite|ste|floor|fl|unit|apt|building|bldg|meet at|entrance)\b", re.I)


def _forms(query: str) -> list[str]:
    ""


    parts = [p.strip() for p in re.sub(r"\([^)]*\)", " ", query).split(",")]
    parts = [p for p in parts if p and not _NOT_A_PLACE.search(p)]
    forms = [", ".join(parts[i:]) for i in range(len(parts)) if len(parts) - i >= 2 or i == 0]
    return list(dict.fromkeys([query, *forms]))[:GEOCODE_TRIES]


def _place(query: str, near: dict | None = None, generation: int | None = None) -> dict | None:
    ""
    query = " ".join(query.split())[:200]
    if not query:
        return None
    with store._lock:
        row = store.db().execute("SELECT body FROM work_places WHERE query=?", (query,)).fetchone()
    if row:
        return json.loads(row["body"]) or None
    found = []
    for form in _forms(query):
        params = {"q": form, "format": "jsonv2", "limit": 1}
        if near:
            params["viewbox"] = f"{near['lon'] - 1},{near['lat'] + 1},{near['lon'] + 1},{near['lat'] - 1}"
        if found := _get(GEOCODER, params):
            break
    place = {"lat": float(found[0]["lat"]), "lon": float(found[0]["lon"]),
             "address": str(found[0].get("display_name") or "")[:300]} if found else {}
    with store._lock:
        if generation is not None and generation != _generation:
            raise Stale
        c = store.db()
        c.execute("INSERT OR REPLACE INTO work_places(query,body,at) VALUES(?,?,?)", (query, json.dumps(place), time.time()))
        c.commit()
    return place or None


def home_place() -> str:
    ""
    for f in facts.search("home lives address", None, limit=5, kinds=("place",)):
        if workmail._usable(f) and (m := _LIVES.search(f["text"].strip())):
            return m.group(1)
    return ""


def _route(place: str, generation: int | None = None) -> tuple[int | None, str]:
    ""
    origin = _place(home_place(), None, generation)
    target = _place(place, origin, generation)
    if not target:
        return None, ""
    if not origin:
        return None, target["address"]
    out = _get(ROUTER + f"{origin['lon']},{origin['lat']};{target['lon']},{target['lat']}", {"overview": "false"})
    routes = out.get("routes") if isinstance(out, dict) else None
    seconds = int(routes[0]["duration"]) if routes else None
    return (seconds if seconds and seconds <= MAX_DRIVE_SEC else None), target["address"]




def _rows(where: str = "1", args: tuple = ()) -> list[dict]:
    return [dict(r) for r in store.db().execute(f"SELECT * FROM work_meetings WHERE {where}", args)]


def _save_events(now: float) -> None:
    gone: list[str] = []
    with store._lock:
        c = store.db()
        kept = set()
        for e in notices.calendar_events():
            if not (now - KEEP_SEC < e["end"] and e["start"] <= now + LOOKAHEAD_SEC) or not wanted(e):
                continue
            kept.add(_key(e))
            body = {k: e.get(k) for k in ("id", "title", "location", "description", "calendar", "calendar_id",
                                          "recurring", "organizer")}
            if e["start"] <= now and (place := _place_key(e)):
                c.execute("INSERT INTO work_meeting_visits(place,title,last_start) VALUES(?,?,?) ON CONFLICT(place,title) "
                          "DO UPDATE SET last_start=MAX(last_start,excluded.last_start)",
                          (place, openloops._key(e["title"]), e["start"]))
            body["people"] = [{"name": p.get("name") or "", "email": p.get("email") or ""}
                              for p in calcache.outside_people(e)]
            old = c.execute("SELECT body,start FROM work_meetings WHERE key=?", (_key(e),)).fetchone()
            moved = old and (json.loads(old["body"]).get("location") != e.get("location") or old["start"] != e["start"])
            c.execute("INSERT INTO work_meetings(key,body,start,end_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                      "body=excluded.body,start=excluded.start,end_at=excluded.end_at",
                      (_key(e), json.dumps(body), e["start"], e["end"]))
            if moved:
                gone += [r["card_id"] for r in c.execute("SELECT card_id FROM work_meetings WHERE key=? AND card_id!='' "
                                                         "AND card_closed IS NULL", (_key(e),))]
                c.execute("UPDATE work_meetings SET drive_sec=NULL,drive_source='',routed_at=NULL,address='',"
                          "pushed_at=NULL,join_url='',join_checked='[]',card_id='',carded_at=NULL,card_closed=NULL "
                          "WHERE key=?", (_key(e),))
        c.execute("DELETE FROM work_meetings WHERE end_at<?", (now - KEEP_SEC,))
        c.execute("DELETE FROM work_meeting_visits WHERE last_start<?", (now - ROUTINE_SEC - KEEP_SEC,))
        for row in c.execute("SELECT key,card_id,card_closed FROM work_meetings WHERE start>?", (now,)).fetchall():
            if row["key"] not in kept:
                if row["card_id"] and row["card_closed"] is None:
                    gone.append(row["card_id"])
                c.execute("DELETE FROM work_meetings WHERE key=?", (row["key"],))
        c.commit()
    for card_id in gone:
        _close_card(card_id, "The meeting moved or was cancelled.")


async def _route_due(now: float) -> None:
    with store._lock:
        generation = _generation
        due = [r for r in _rows("start>? AND drive_source!='device' AND drive_sec IS NULL", (now,))
               if calcache.physical_place(json.loads(r["body"])) and (r["routed_at"] or 0) < now - ROUTE_RETRY_SEC]
    for row in due:
        try:
            seconds, address = await asyncio.to_thread(_route, calcache.physical_place(json.loads(row["body"])),
                                                       generation)
        except Stale:
            return
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            log.info("no drive time for a meeting: %s", type(e).__name__)
            seconds, address = None, ""
        with store._lock:
            if generation != _generation:
                return
            c = store.db()
            c.execute("UPDATE work_meetings SET drive_sec=COALESCE(drive_sec,?),drive_source=CASE WHEN drive_sec IS NULL "
                      "AND ? IS NOT NULL THEN 'map' ELSE drive_source END,address=?,routed_at=? WHERE key=?",
                      (seconds, seconds, address, time.time(), row["key"]))
            c.commit()


def join_link(text) -> str:
    m = _JOIN.search(str(text or ""))
    return m.group(0).rstrip(".,;:") if m else ""


def join_url(row: dict) -> str:
    ""
    e = json.loads(row["body"])
    if calcache.physical_place(e):
        return ""
    return join_link(e.get("location")) or join_link(e.get("description")) or row.get("join_url") or ""


def _related(e: dict, project_id: str | None = None) -> list[dict]:
    ""

    emails = {p["email"].casefold() for p in e["people"] if p.get("email")}
    named = m.group(1).casefold() if (m := _NAMED.search(str(e.get("title") or ""))) else ""
    found = []
    for r in store.db().execute("SELECT project_id,body FROM work_sources WHERE kind='email' AND active=1"):
        body = work._json(r["body"])
        sender = (body.get("people") or [{}])[0]
        address, name = str(sender.get("email") or "").casefold(), str(sender.get("name") or "").casefold()
        if (address and address in emails) or (named and re.search(rf"\b{re.escape(named)}\b", name)) or \
                (project_id and r["project_id"] == project_id and project_id != work.OTHER):
            found.append({"source_id": body["id"], "title": body.get("title") or "", "summary": body.get("summary") or "",
                          "from": sender, "at": body.get("at")})
    return sorted(found, key=lambda m: -(m["at"] or 0))


def _read_link(source_id: str) -> tuple[str, bool]:
    ""
    try:
        return join_link(workmail.message(source_id).get("body")), True
    except mail.MessageGone:
        return "", True
    except Exception as e:
        log.info("no join link from an email: %s", type(e).__name__)
        return "", False


async def _link_due(now: float) -> None:
    ""

    with store._lock:
        generation = _generation
        due = [r for r in _rows("start>? AND join_url=''", (now,)) if not join_url(r)
               and not calcache.physical_place(e := json.loads(r["body"])) and not e.get("recurring")]
        related = {r["key"]: [m for m in _related(json.loads(r["body"])) if (m["at"] or 0) < r["start"]][:EMAILS]
                   for r in due}
    for row in due:
        checked, found = json.loads(row["join_checked"] or "[]"), ""
        for m in [m for m in related[row["key"]] if m["source_id"] not in checked]:
            found = join_link(m["summary"])
            if not found:
                found, done = await asyncio.to_thread(_read_link, m["source_id"])
                if not done:
                    break
            checked.append(m["source_id"])
            if found:
                break
        if checked == json.loads(row["join_checked"] or "[]"):
            continue
        with store._lock:
            if generation != _generation:
                return
            c = store.db()
            c.execute("UPDATE work_meetings SET join_url=?,join_checked=? WHERE key=? AND join_url=''",
                      (found, json.dumps(checked), row["key"]))
            c.commit()


def _drive_words(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(round(minutes / 5) * 5, 60)
    said = f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{said} and {rest} minutes" if rest else said


def push_at(row: dict) -> float | None:
    ""

    e = json.loads(row["body"])
    if calcache.physical_place(e):
        return leave_by(row)
    return None if e.get("recurring") else row["start"] - HEADS_UP_SEC


def prep_line(notes: str) -> str:
    ""


    for part in re.split(r"(?<=[.!?;])\s+|\n+", str(notes or "")):
        part = part.strip()
        m = _PREP.match(part)
        if not m:
            continue
        if m.group(1).lower() in ("topic", "agenda") and part[m.end():].lstrip().startswith(":"):
            part = f"The {m.group(1).lower()} is {part[m.end():].lstrip()[1:].strip()}"
        else:
            part = part.split(": ")[0]
        part = part.strip(" .;:,")
        if 8 <= len(part) < PREP_MAX:
            return part + "."
    return ""


def plain_title(title: str) -> str:
    ""
    name = re.sub(r"\s*\([^)]*\)", "", str(title or "")).strip()
    m = _MEETING_NAME.fullmatch(name)
    return name if m and not _ORDER_WORD.search(m["names"] or "") else ""








_LINK_OR_NUMBER = re.compile(r"https?:|www\.|\b[\w-]+\.(?:com|net|org|io|co|ly|me|app)\b|\d[\d\s().-]{6,}\d", re.I)
NAME_TIMEOUT_SEC = 20
NAME_PROMPT = """This is the title of a calendar event that someone else put on a person's calendar:

{title}

A reminder would show the title as it is. Is the title only a name for the occasion: what it is, who is in it, where \
or when? Answer NO when it asks or tells the reader to do anything (confirm, sign in, pay, call, reply, open \
something), or carries a link, a code or contact details.

Answer with one word: YES or NO."""
_named: dict[str, bool] = {}


async def occasion_name(title: str, ask=None) -> str:
    ""
    name = re.sub(r"\s*\([^)]*\)", "", str(title or "")).strip()
    plain = plain_title(title)
    if not name or plain or _LINK_OR_NUMBER.search(name) or len(name) > 80:
        return plain
    if name not in _named:
        try:
            with shortreply.reading(()):
                answer = await (ask or shortreply.ask_helper)(NAME_PROMPT.format(title=name), timeout=NAME_TIMEOUT_SEC,
                                                              max_tokens=5, effort="low")
        except Exception as error:
            log.info("could not judge a meeting's title, so it stays out: %s", type(error).__name__)
            return plain
        if len(_named) > 500:
            _named.clear()
        _named[name] = str(answer).strip().strip(".").upper() == "YES"
    return name if _named[name] else plain


def event_trusted(e: dict) -> bool:
    ""





    organizer = str(e.get("organizer") or "").casefold()
    if not organizer:
        return not e.get("people")
    if organizer in mail.OWN or _CALENDAR_ADDRESS.search(organizer):
        return True
    for account in list(mail.ACCOUNTS):
        try:
            if mailcursor.written_to(account, [organizer]):
                return True
        except Exception as error:
            log.info("could not search sent mail for a meeting: %s", type(error).__name__)
    return False


def push_text(row: dict, title: str) -> str:
    ""
    e = json.loads(row["body"])
    if calcache.physical_place(e):
        title = title or "something on your calendar"
        text = (f"Leave by {notices._et(leave_by(row)).strftime('%-I:%M')} for {title}. "
                f"The drive is about {_drive_words(row['drive_sec'])}.")
        if shift := shifts.during(leave_by(row), row["end_at"]):
            text += f" Leaving then overlaps your shift, which ends at {notices.clock(shift['end'])}."
    else:
        title = re.sub(r"\s*\([^)]*\)", "", title).strip() or title or "Something on your calendar"
        text = f"{title} starts at {notices._et(row['start']).strftime('%-I:%M')}."
        if link := join_url(row):
            service = next((name for host, name in _SERVICES if host in link.casefold()), "")
            text = f"{text[:-1]} on {service}. Tap to join."
    return text


async def _push_due(now: float) -> None:
    with store._lock:
        generation = _generation
        due = [r for r in _rows("pushed_at IS NULL AND start>?", (now,)) if (at := push_at(r)) and at <= now]
        projects = {s["id"]: s["project"] for s in _placed()}
    for row in due:
        with store._lock:
            if generation != _generation:
                return
            c = store.db()
            claimed = c.execute("UPDATE work_meetings SET pushed_at=? WHERE key=? AND pushed_at IS NULL",
                                (now, row["key"])).rowcount
            c.commit()
        if not claimed or now - push_at(row) > LATE_SEC or routine(row):
            continue
        e = json.loads(row["body"])
        source_id = "meeting:" + row["key"]
        extra = {"work_project_id": projects.get(source_id) or "", "work_source_id": source_id}
        if link := join_url(row):
            extra["join_url"] = link
        line, title, plain = prep_line(e.get("description")), e["title"], plain_title(e["title"])
        if (line or plain != title) and not await asyncio.to_thread(event_trusted, e):
            line, title = "", await occasion_name(title)
        await watcher.push(e["title"], f"{push_text(row, title)} {line}".strip(), _owner(e), urgent=True, now=True, kind="needs",
                           exact=True, extra=extra)












def card_at(row: dict) -> float | None:
    ""

    at = push_at(row)
    if at is None:
        return None
    morning = notices.day_start(row["start"]) + CARD_MORNING_HOUR * 3600
    early = at - CARD_LEAD_SEC
    return early if early >= morning else min(morning, at)


def card(row: dict, title: str, line: str, notes: list[str], emails: list[dict]) -> tuple[str, list[dict]]:
    ""

    e = json.loads(row["body"])
    place = calcache.physical_place(e)
    name = re.sub(r"\s*\([^)]*\)", "", title).strip() or "something on your calendar"
    start = notices._et(row["start"]).strftime("%-I:%M")
    link = join_url(row)
    if place:
        text = f"You have {name} at {start}."
        if (lb := leave_by(row)) is not None:
            text += f" Leave by {notices._et(lb).strftime('%-I:%M')}. The drive is about {_drive_words(row['drive_sec'])}."
            if shift := shifts.during(lb, row["end_at"]):
                text += f" Leaving then overlaps your shift, which ends at {notices.clock(shift['end'])}."
    else:
        service = next((label for host, label in _SERVICES if host in link.casefold()), "")
        text = f"You have {name} at {start}{' on ' + service if service else ''}."
    if line:
        text += f" {line}"
    pulled = [f"- {n}" for n in notes[:CARD_NOTES]]
    for m in emails[:EMAILS]:
        sender = str((m.get("from") or {}).get("name") or "").strip()
        if subject := " ".join(str(m.get("title") or "").split())[:120]:
            pulled.append(f"- Email{' from ' + sender if sender else ''}: {subject}")
    if pulled:
        text += " I pulled together " + ("my notes and the recent emails." if notes and len(pulled) > len(notes[:CARD_NOTES])
                                         else "my notes." if notes else "the recent emails.")
    blocks: list[dict] = [{"type": "text", "text": text}]
    if pulled:
        blocks.append({"type": "text", "text": "\n".join(pulled)})
    if place:
        blocks.append({"type": "buttons", "buttons": [{"label": "Directions",
                                                       "link": MAPS + quote(row["address"] or place, safe="")}]})
    elif link:
        blocks.append({"type": "buttons", "buttons": [{"label": "Join", "link": link}]})
    return (name[0].upper() + name[1:])[:60], blocks


def _close_card(card_id: str, why: str) -> None:
    from . import kit
    try:
        kit.close(config.LEAD.id, card_id, why)
    except Exception as e:
        log.info("a meeting's card was not closed: %s", type(e).__name__)


async def _card_due(now: float) -> None:
    from . import kit
    with store._lock:
        generation = _generation
        due = [r for r in _rows("carded_at IS NULL AND start>?", (now,)) if (at := card_at(r)) and at <= now]
        over = _rows("card_id!='' AND card_closed IS NULL AND end_at<=?", (now,))
        projects = {s["id"]: s["project"] for s in _placed()}
    for row in over:
        _close_card(row["card_id"], "The meeting is over.")
        with store._lock:
            store.db().execute("UPDATE work_meetings SET card_closed=? WHERE key=?", (now, row["key"]))
            store.db().commit()
    for row in due:
        with store._lock:
            if generation != _generation:
                return
            c = store.db()
            claimed = c.execute("UPDATE work_meetings SET carded_at=? WHERE key=? AND carded_at IS NULL",
                                (now, row["key"])).rowcount
            c.commit()
        if not claimed or routine(row):
            continue
        e = json.loads(row["body"])
        line, title, plain = prep_line(e.get("description")), e["title"], plain_title(e["title"])
        notes, emails = _notes(e), _related(e, projects.get("meeting:" + row["key"]))


        if (line or plain != title or emails) and not await asyncio.to_thread(event_trusted, e):
            line, title, notes, emails = "", await occasion_name(title), [], []
        try:
            name, blocks = card(row, title, line, notes, emails)
            prepared = await asyncio.to_thread(kit.prepare, config.LEAD.id, name, blocks)
            with store._lock:
                if generation != _generation:
                    return
                made = kit.put_up(config.LEAD.id, prepared, False)
                store.db().execute("UPDATE work_meetings SET card_id=? WHERE key=?",
                                   (str(made.get("card") or ""), row["key"]))
                store.db().commit()
        except Exception as error:
            log.warning("a meeting's card was not made: %s", type(error).__name__)


def _place_key(e: dict) -> str:
    return openloops._key(calcache.physical_place(e))


def routine(row: dict) -> bool:
    ""



    e = json.loads(row["body"])
    place = _place_key(e)
    if not place:
        return False
    titles = {r["title"] for r in store.db().execute(
        "SELECT title FROM work_meeting_visits WHERE place=? AND last_start>=? AND last_start<?",
        (place, row["start"] - ROUTINE_SEC, row["start"]))}
    return openloops._key(e["title"]) in titles or bool(titles and e.get("recurring"))


def _placed() -> list[dict]:
    return [{"id": r["id"], "project": r["project_id"]} for r in store.db().execute(
        "SELECT id,project_id FROM work_sources WHERE kind='meeting' AND active=1")]


async def tick(now: float | None = None) -> None:
    now = time.time() if now is None else now
    _save_events(now)
    await _route_due(now)
    await _link_due(now)
    work.rebuild()
    await _card_due(now)
    await _push_due(now)


async def run() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await tick()
        except Exception:
            log.exception("meeting prep could not refresh")
        await asyncio.sleep(INTERVAL)


def report_drive(source_id: str, seconds: int) -> dict:
    ""
    key = source_id.removeprefix("meeting:")
    with store._lock:
        c = store.db()
        row = c.execute("SELECT body FROM work_meetings WHERE key=?", (key,)).fetchone()
        if row and not calcache.physical_place(json.loads(row["body"])):
            raise LookupError("That event has no place to drive to.")
        changed = c.execute("UPDATE work_meetings SET drive_sec=?,drive_source='device' WHERE key=? AND pushed_at IS NULL "
                            "AND start>?", (int(seconds), key, time.time())).rowcount
        c.commit()
        rows = _rows("key=?", (key,))
    if not rows:
        raise LookupError("That event is no longer coming up.")
    return {"accepted": bool(changed), "leave_by": leave_by(rows[0])}




def _owner(e: dict) -> str:
    from . import calendar_noticers
    return calendar_noticers.owner_for_event({**e, "attendees": e.get("people") or []})


def _project_for(source_id: str, people: list[dict], start: float) -> str | None:
    ""


    emails = {p["email"].casefold() for p in people if p.get("email")}
    rows = [(r["id"], work._json(r["body"])) for r in store.db().execute(
        "SELECT id,body FROM work_projects WHERE active=1 AND id!=? ORDER BY id", (work.OTHER,))]
    rows = [(pid, body) for pid, body in rows if body.get("primary_source_id") != source_id]
    for pid, body in rows:
        if emails & {str(p.get("email") or "").casefold() for p in body.get("people") or []}:
            return pid
    for pid, body in rows:
        date = body.get("date")
        if isinstance(date, (int, float)) and abs(date - start) < 60 and \
                not str(body.get("primary_source_id") or "").startswith("meeting:"):
            return pid
    return None


def sources(now: float | None = None) -> list[work.Source]:
    ""
    now = time.time() if now is None else now
    out = []
    for row in _rows("end_at>?", (now - SHOW_AFTER_SEC,)):
        lb = leave_by(row)
        if now < min(row["start"] - SHOW_SEC, (lb or row["start"]) - 15 * 60):
            continue
        e = json.loads(row["body"])
        cal = str(e.get("calendar_id") or e.get("calendar") or "")
        sid = "meeting:" + row["key"]
        source = work.Source(sid, "meeting", e["title"] or "Meeting", _owner(e), row["start"],
                             {"calendar": cal, "event": str(e["id"]), "subject": openloops._key(e["title"])},
                             date=row["start"], place=str(e.get("location") or ""), people=e["people"],
                             reference={"calendar": cal, "event": str(e["id"]),
                                        "filed_project_id": _project_for(sid, e["people"], row["start"])},
                             creates_project=True)
        out.append(source)
    return out


def _notes(e: dict) -> list[str]:
    seen, out = set(), []
    for name in [p["name"] for p in e["people"] if p.get("name")] + [e["title"]]:
        for f in workmail._about(name):
            if workmail._usable(f) and f["text"] not in seen and len(out) < NOTES:
                seen.add(f["text"])
                out.append(f["text"][:240])
    return out


def prep(project_id: str) -> list[dict]:
    ""
    out = []
    for s in sorted(_placed(), key=lambda s: s["id"]):
        if s["project"] != project_id:
            continue
        rows = _rows("key=?", (s["id"].removeprefix("meeting:"),))
        if not rows:
            continue
        row, e = rows[0], json.loads(rows[0]["body"])
        place = calcache.physical_place(e)
        out.append({"source_id": s["id"], "title": e["title"], "start": row["start"], "end": row["end_at"],
                    "people": e["people"], "place": str(e.get("location") or ""), "address": row["address"],
                    "map_query": row["address"] or place, "notes": str(e.get("description") or ""),
                    "known": _notes(e), "emails": _related(e, project_id)[:EMAILS], "join": join_url(row),
                    "calendar": str(e.get("calendar_id") or e.get("calendar") or ""), "event": str(e["id"]),
                    "drive": {"seconds": row["drive_sec"], "source": row["drive_source"]} if row["drive_sec"] else None,
                    "leave_by": leave_by(row), "needs_drive": bool(place) and row["pushed_at"] is None,
                    "push": bool(place or push_at(row)) and not routine(row)})
    return sorted(out, key=lambda p: p["start"])


def leave_lines(bot_id: str, now: float) -> list[str]:
    ""



    from . import calendar_noticers
    out = []
    for row in _rows("start>? AND start<? ORDER BY start", (now, notices.day_start(now) + 86400)):
        e = json.loads(row["body"])
        at = leave_by(row)
        if at is None or at < now or not calcache.physical_place(e) or bot_id not in ("default", _owner(e)):
            continue
        line = (f"- Leave by {calendar_noticers.clock(at)} for {e['title']} at {calendar_noticers.clock(row['start'])}"
                f" ({calcache.physical_place(e)}): the hub's drive estimate is about {_drive_words(row['drive_sec'])}"
                f" with room for traffic and parking, and it pushes this time to the phone.")
        if shift := shifts.during(at, row["end_at"]):
            line += (f" This overlaps {shift['title']} {calendar_noticers._range(shift)}, so say that whenever you give "
                     "this time.")
        out.append(line)
    return out


def upcoming() -> list[dict]:
    ""
    placed = {s["id"]: s["project"] for s in _placed()}
    out = []
    for row in _rows():
        sid = "meeting:" + row["key"]
        if sid in placed:
            e = json.loads(row["body"])
            out.append({"source_id": sid, "project_id": placed[sid], "title": e["title"], "start": row["start"],
                        "place": str(e.get("location") or ""), "leave_by": leave_by(row)})
    return sorted(out, key=lambda m: m["start"])
