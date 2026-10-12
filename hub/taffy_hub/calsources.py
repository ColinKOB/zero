"""Reading a home's own calendars for the hub's calendar copy, without connectors: Google Calendar through the API
(Sign In with Google), CalDAV (iCloud and others) and private iCal addresses (plan 2026-10-09, section 4).

Each reader returns events in the same shape calcache.parse_events gives the connector's answer, so the noticers and
the voice read one copy whatever the source. Only reads; nothing here changes a calendar.
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx

from . import google, home, mailboxes

log = logging.getLogger("taffy.calsources")

DAV, CAL = "{DAV:}", "{urn:ietf:params:xml:ns:caldav}"


class SourceError(RuntimeError):
    pass


def _clean(v, limit: int = 200) -> str:
    return " ".join(str(v or "").split())[:limit]


def _event(eid, title, start, end, all_day, location="", calendar="", organizer="", response="", attendees=None,
           free=False, description="") -> dict:
    title = _clean(title, 160)
    if end is None or end <= start:
        end = start + (86400 if all_day else 3600)
    return {"id": _clean(eid, 200) or hashlib.sha1(f"{title}|{start}".encode()).hexdigest()[:16], "title": title,
            "start": start, "end": end, "all_day": all_day, "location": _clean(location, 200),
            "calendar": _clean(calendar, 80), "organizer": _clean(organizer, 120).lower(),
            "my_response": _clean(response, 20).lower(), "attendees": (attendees or [])[:30], "free": bool(free),
            "description": _clean(description, 300)}


# MARK: Google Calendar

def google_events(cal: dict, start: float, end: float) -> list[dict]:
    address = home.calendar_address(cal)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        cal_id = str(cal.get("calendar_id") or "primary")
        data = google.api_get(address, f"{google.CALENDAR_API}/calendars/{cal_id}/events",
                              {"timeMin": iso(start), "timeMax": iso(end), "singleEvents": "true",
                               "orderBy": "startTime", "maxResults": 250})
    except google.GoogleError as e:
        raise SourceError(str(e)) from None
    out = []
    for e in data.get("items") or []:
        if e.get("status") == "cancelled":
            continue
        s, en = e.get("start") or {}, e.get("end") or {}
        all_day = "date" in s and "dateTime" not in s
        ts = _parse_iso(s.get("dateTime") or s.get("date"))
        if ts is None:
            continue
        people, response = [], ""
        for a in e.get("attendees") or []:
            if a.get("self"):
                response = a.get("responseStatus") or ""
            people.append({"name": _clean(a.get("displayName"), 80), "email": _clean(a.get("email"), 120).lower(),
                           "self": bool(a.get("self"))})
        out.append(_event(e.get("id"), e.get("summary"), ts, _parse_iso(en.get("dateTime") or en.get("date")),
                          all_day, e.get("location"), cal.get("label") or "Google Calendar",
                          (e.get("organizer") or {}).get("email"), response, people,
                          e.get("transparency") == "transparent", e.get("description")))
    return out


def _parse_iso(v) -> float | None:
    if not v:
        return None
    s = str(v)
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d", s):
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=home.TZ).timestamp()
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=home.TZ)).timestamp()
    except ValueError:
        return None


# MARK: iCalendar text (CalDAV answers and private addresses)

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_time(value: str, params: dict) -> tuple[float | None, bool]:
    v = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
            d = datetime.strptime(v[:8], "%Y%m%d")
            return d.replace(tzinfo=home.TZ).timestamp(), True
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp(), False
        dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
        tz = home.TZ
        if params.get("TZID"):
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(params["TZID"])
            except Exception:
                tz = home.TZ
        return dt.replace(tzinfo=tz).timestamp(), False
    except ValueError:
        return None, False


def _ics_text(v: str) -> str:
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _expand(ev: dict, rrule: str, start: float, end: float) -> list[float]:
    """Start times of a repeating event inside [start, end): DAILY and WEEKLY rules with INTERVAL, COUNT, UNTIL and
    BYDAY, the ones personal calendars use. Other rules give the first time only."""
    parts = dict(p.split("=", 1) for p in rrule.split(";") if "=" in p)
    freq = parts.get("FREQ")
    first = datetime.fromtimestamp(ev["start"], home.TZ)
    if freq not in ("DAILY", "WEEKLY"):
        return [ev["start"]] if start <= ev["start"] < end else []
    step = max(1, int(parts.get("INTERVAL", "1") or 1))
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until = _ics_time(parts["UNTIL"], {})[0] if parts.get("UNTIL") else None
    days = [WEEKDAYS[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in WEEKDAYS] or [first.weekday()]
    out, n, cur = [], 0, first
    limit = datetime.fromtimestamp(end, home.TZ)
    while cur <= limit and n < 2000:
        if freq == "DAILY":
            cands = [cur]
        else:
            week0 = cur - timedelta(days=cur.weekday())
            cands = sorted(week0 + timedelta(days=d) for d in days)
            cands = [c for c in cands if c >= first]
        for c in cands:
            ts = c.timestamp()
            if (until is not None and ts > until) or (count is not None and n >= count):
                return out
            n += 1
            if start <= ts < end:
                out.append(ts)
        cur = cur + (timedelta(days=step) if freq == "DAILY" else timedelta(weeks=step))
        if freq == "WEEKLY":
            cur = cur.replace(hour=first.hour, minute=first.minute)
    return out


def parse_ics(text: str, start: float, end: float, calendar: str = "") -> list[dict]:
    out = []
    cur: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"attendees": []}
            continue
        if line == "END:VEVENT" and cur is not None:
            ev, cur = cur, None
            if ev.get("start") is None or ev.get("status") == "CANCELLED":
                continue
            dur = (ev.get("end") or ev["start"]) - ev["start"]
            starts = _expand(ev, ev["rrule"], start, end) if ev.get("rrule") else (
                [ev["start"]] if ev["start"] < end and ev["start"] + max(dur, 1) > start else [])
            for s in starts:
                out.append(_event(f"{ev.get('uid', '')}:{int(s)}" if ev.get("rrule") else ev.get("uid"),
                                  ev.get("summary"), s, s + dur if dur > 0 else None, ev.get("all_day", False),
                                  ev.get("location", ""), calendar, ev.get("organizer", ""), ev.get("response", ""),
                                  ev["attendees"], ev.get("transp") == "TRANSPARENT", ev.get("description", "")))
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *pp = head.split(";")
        params = dict(p.split("=", 1) for p in pp if "=" in p)
        name = name.upper()
        if name == "DTSTART":
            cur["start"], cur["all_day"] = _ics_time(value, params)
        elif name == "DTEND":
            cur["end"] = _ics_time(value, params)[0]
        elif name == "SUMMARY":
            cur["summary"] = _ics_text(value)
        elif name == "LOCATION":
            cur["location"] = _ics_text(value)
        elif name == "DESCRIPTION":
            cur["description"] = _ics_text(value)
        elif name == "UID":
            cur["uid"] = value
        elif name == "TRANSP":
            cur["transp"] = value.upper()
        elif name == "STATUS":
            cur["status"] = value.upper()
        elif name == "RRULE":
            cur["rrule"] = value
        elif name == "ORGANIZER":
            cur["organizer"] = value.removeprefix("mailto:").removeprefix("MAILTO:")
        elif name == "ATTENDEE":
            email = value.removeprefix("mailto:").removeprefix("MAILTO:").lower()
            cur["attendees"].append({"name": _clean(params.get("CN", "").strip('"'), 80), "email": email,
                                     "self": False})
    return sorted(out, key=lambda e: (e["start"], e["id"]))


# MARK: Private addresses and CalDAV

def _secret(cal_id: str, ext: str, given: str | None = None) -> str:
    """A calendar's password (`password_file`) or private address (`url_file`), by default in
    ~/.config/zero-calendars/<id>.pass or .url."""
    try:
        f = home.expand(given) if given else mailboxes.calendars_dir() / f"{cal_id}.{ext}"
        return f.read_text().strip()
    except OSError:
        raise SourceError("the calendar's saved address or password is missing") from None


def ical_events(cal: dict, start: float, end: float) -> list[dict]:
    url = str(cal.get("url") or "") or _secret(cal["id"], "url", cal.get("url_file"))
    try:
        r = mailboxes.http_request("GET", url)
    except httpx.HTTPError:
        raise SourceError("the calendar address could not be reached") from None
    if r.status_code != 200:
        raise SourceError(f"the calendar address answered {r.status_code}")
    return parse_ics(r.text, start, end, cal.get("label") or "Calendar")


REPORT = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-data/></d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
    <c:time-range start="{start}" end="{end}"/>
  </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""

PROPFIND_HOME = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" '
                 'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>')
PROPFIND_CALS = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
                 '<d:displayname/></d:prop></d:propfind>')


def _dav(method: str, url: str, auth, body: str, depth: str) -> ET.Element:
    try:
        r = mailboxes.http_request(method, url, auth=auth, content=body,
                                   headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
    except httpx.HTTPError:
        raise SourceError("the calendar server could not be reached") from None
    if r.status_code in (401, 403):
        raise SourceError("the calendar server did not accept the password")
    if r.status_code not in (200, 207):
        raise SourceError(f"the calendar server answered {r.status_code}")
    try:
        return ET.fromstring(r.content)
    except ET.ParseError:
        raise SourceError("the calendar server's answer was not readable") from None


def _href(root: ET.Element, prop: str) -> str | None:
    el = root.find(f".//{prop}/{DAV}href")
    return el.text.strip() if el is not None and el.text else None


def caldav_events(cal: dict, start: float, end: float) -> list[dict]:
    auth = (str(cal.get("username") or ""), _secret(cal["id"], "pass", cal.get("password_file")))
    base = str(cal.get("url") or mailboxes.CALDAV_ICLOUD)
    principal = _href(_dav("PROPFIND", base, auth, mailboxes.PROPFIND_PRINCIPAL, "0"), f"{DAV}current-user-principal")
    home_url = urljoin(base, principal) if principal else base
    home_set = _href(_dav("PROPFIND", home_url, auth, PROPFIND_HOME, "0"), f"{CAL}calendar-home-set")
    cals_url = urljoin(home_url, home_set) if home_set else home_url
    listing = _dav("PROPFIND", cals_url, auth, PROPFIND_CALS, "1")
    stamp = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = []
    for resp in listing.findall(f"{DAV}response"):
        if resp.find(f".//{DAV}resourcetype/{CAL}calendar") is None:
            continue
        href = resp.findtext(f"{DAV}href") or ""
        name = resp.findtext(f".//{DAV}displayname") or cal.get("label") or "Calendar"
        found = _dav("REPORT", urljoin(cals_url, href), auth, REPORT.format(start=stamp(start), end=stamp(end)), "1")
        for data in found.iter(f"{CAL}calendar-data"):
            out += parse_ics(data.text or "", start, end, name)
    return out


READERS = {"google_oauth": google_events, "google": google_events, "caldav": caldav_events, "ical": ical_events}


def events(start: float, end: float) -> tuple[list[dict], list[str]]:
    """Every configured calendar's events in [start, end), and a short reason for each calendar that failed."""
    out, failed = [], []
    for cal in home.calendars():
        reader = READERS.get(str(cal.get("kind") or ""))
        if not reader:
            continue
        try:
            out += reader(cal, start, end)
        except SourceError as e:
            log.info("calendar %s not read: %s", cal.get("id"), e)
            failed.append(f"{cal.get('id')}: {e}")
    seen, unique = set(), []
    for e in sorted(out, key=lambda x: (x["start"], x["id"])):
        if e["id"] not in seen:
            seen.add(e["id"])
            unique.append(e)
    return unique, failed
