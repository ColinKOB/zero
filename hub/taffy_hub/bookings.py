""




from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import Parser
from email.utils import parseaddr
from html.parser import HTMLParser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import mail

MAX_MESSAGE = 2_000_000
TYPES = {"FlightReservation": "flight", "TaxiReservation": "ride", "LodgingReservation": "hotel",
         "FoodEstablishmentReservation": "reservation"}


def key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Booking:
    id: str
    reservation: str
    kind: str
    title: str
    start: float
    end: float
    place: str = ""
    destination: str = ""
    city: str = ""
    cancelled: bool = False

    def data(self) -> dict:
        return {"booking": True, **asdict(self)}


def read_message(e: dict) -> str:
    ""



    account, mid = e["account"], e["id"]
    if not mail._known(account) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", mid) or mid.startswith("-"):
        raise mail.MailError("Not a booking message I can read.")
    if not mail.uses_google(account):
        return mail.read(account, e["folder"], mid)
    msg = mail.gmail_get(mail._address_of(account), f"messages/{mid}", {"format": "raw"})
    raw = msg.get("raw") or ""
    if len(raw) > MAX_MESSAGE * 2:
        return ""
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(errors="replace")


def stamp(value: str, tz: str = "") -> float | None:
    ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            if not tz or "T" not in str(value):
                return None
            zone = ZoneInfo(tz)

            a, b = dt.replace(tzinfo=zone, fold=0), dt.replace(tzinfo=zone, fold=1)
            if a.utcoffset() != b.utcoffset():
                return None
            dt = a
        return dt.timestamp()
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        pass

    value = re.sub(r"\s+at\s+", " ", str(value or "").strip(), flags=re.I)
    m = re.fullmatch(r"(.+)\s+(UTC|GMT|EST|EDT|PST|PDT|MST|MDT)", value)
    if not m:
        return None
    hours = {"UTC": 0, "GMT": 0, "EST": -5, "EDT": -4, "PST": -8, "PDT": -7, "MST": -7, "MDT": -6}
    for fmt in ("%B %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p", "%A, %B %d, %Y %I:%M %p"):
        try:
            return datetime.strptime(m[1], fmt).replace(tzinfo=timezone(timedelta(hours=hours[m[2]]))).timestamp()
        except ValueError:
            continue
    return None


def name(obj) -> str:
    if isinstance(obj, dict):
        return str(obj.get("iataCode") or obj.get("name") or "").strip()[:100]
    return str(obj or "").strip()[:100]


def location(obj) -> str:
    if isinstance(obj, dict) and obj.get("iataCode") and obj.get("name"):
        return f"{str(obj['name'])[:80]} ({str(obj['iataCode'])[:3]})"
    return name(obj)


def city(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    address = obj.get("address")
    return str(address.get("addressLocality") or "").strip()[:80] if isinstance(address, dict) else ""


def typename(value) -> str:
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


class Markup(HTMLParser):
    ""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.objects = []
        self.stack = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.script = ""
        parent = next((x[1] for x in reversed(self.stack) if x[1] is not None), None)
        obj = None
        prop = a.get("itemprop")
        if "itemscope" in a:
            obj = {"@type": typename(a.get("itemtype"))}
            if parent is not None and prop:
                parent[prop] = obj
            else:
                self.objects.append(obj)
        elif parent is not None and prop:
            parent[prop] = a.get("content") or a.get("href") or a.get("datetime") or ""
        if tag not in ("meta", "link", "br", "hr", "img", "input", "source", "wbr"):
            self.stack.append((tag, obj, parent, prop))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.script is not None:
            self.script += data
        elif self.stack:
            _, obj, parent, prop = self.stack[-1]
            if obj is None and parent is not None and prop and data.strip():
                parent[prop] = str(parent.get(prop) or "") + data.strip()

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            try:
                self.objects.append(json.loads(self.script))
            except (ValueError, RecursionError):
                pass
            self.script = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _objects(value, depth=0):
    if depth > 15:
        return
    if isinstance(value, list):
        for item in value[:100]:
            yield from _objects(item, depth + 1)
    elif isinstance(value, dict):
        if typename(value.get("@type")) in TYPES:
            yield value
        else:
            for item in value.values():
                yield from _objects(item, depth + 1)


def _schema(obj: dict) -> Booking | None:
    kind = TYPES[typename(obj.get("@type"))]
    number = str(obj.get("reservationNumber") or "").strip()
    if not number:
        return None
    service = obj.get("reservationFor")
    service = service if isinstance(service, dict) else {}
    status = typename(obj.get("reservationStatus"))
    if status not in ("", "ReservationConfirmed", "Confirmed", "ReservationCancelled", "Cancelled"):
        return None
    cancelled = status in ("ReservationCancelled", "Cancelled")
    if kind == "flight":
        start, end = stamp(service.get("departureTime")), stamp(service.get("arrivalTime"))
        place, dest = location(service.get("departureAirport")), location(service.get("arrivalAirport"))
        town = city(service.get("arrivalAirport"))
        title = f"{name(service.get('airline')) or 'Your'} flight {name(service.get('flightNumber'))}".strip()
    elif kind == "ride":
        start = stamp(obj.get("pickupTime"))
        end = stamp(obj.get("dropoffTime")) or start
        place, dest, town = location(obj.get("pickupLocation")), location(obj.get("dropoffLocation")), ""
        title = f"{name(obj.get('provider') or obj.get('broker') or service.get('provider')) or 'Your'} ride"
    elif kind == "hotel":
        start = stamp(obj.get("checkinDate"))
        end = stamp(obj.get("checkoutDate"))
        place, dest, town = name(service), "", city(service)
        title = place or "Your hotel stay"
    else:
        start = stamp(obj.get("startTime"))
        end = stamp(obj.get("endTime")) or start
        place, dest, town = name(service), "", city(service)
        title = f"Your reservation at {place}" if place else "Your reservation"
    if start is None or end is None or end < start or (kind in ("flight", "hotel") and end == start):
        if not cancelled:
            return None
        start, end = 0, 0
    reservation = key(kind, number)

    identity = key(reservation, place, dest) if kind == "flight" and place and dest else reservation
    return Booking(identity, reservation, kind, title, start, end, place, dest, town, cancelled)


def _ics(text: str, kind: str) -> list[Booking]:
    text = re.sub(r"\r?\n[ \t]", "", text)
    out = []
    for block in re.findall(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", text, re.S)[:30]:
        fields = {}
        for line in block.splitlines():
            left, sep, value = line.partition(":")
            if sep:
                prop, *params = left.split(";")
                fields[prop] = (value.replace(r"\,", ",").replace(r"\n", " "), params)
        def get(prop):
            return fields.get(prop, ("", []))[0]
        def date(prop):
            value, params = fields.get(prop, ("", []))
            zone = next((p.split("=", 1)[1].strip('"') for p in params if p.startswith("TZID=")), "")
            return stamp(value, zone)
        uid = get("UID")
        start, end = date("DTSTART"), date("DTEND")
        cancelled = get("STATUS") == "CANCELLED" or bool(re.search(r"^METHOD:CANCEL\s*$", text, re.M))
        if not uid or (start is None and not cancelled):
            continue
        if cancelled and start is None:
            start = 0
        if end is None:
            end = start
        if end < start or (kind in ("flight", "hotel") and end == start and not cancelled):
            continue
        identity = key("ics", uid)
        out.append(Booking(identity, identity, kind, get("SUMMARY")[:100] or "Your reservation", start, end,
                           get("LOCATION")[:100], cancelled=cancelled))
    return out


def _sender_kind(sender: str, subject: str) -> str:
    domain = parseaddr(sender)[1].lower().rsplit("@", 1)[-1]
    for domains, kind in ((("uber.com", "lyft.com", "lyftmail.com"), "ride"),
                          (("opentable.com", "resy.com"), "reservation"),
                          (("united.com", "aa.com", "delta.com", "southwest.com", "jetblue.com"), "flight"),
                          (("hilton.com", "marriott.com", "booking.com", "hotels.com"), "hotel")):
        if any(domain == d or domain.endswith("." + d) for d in domains):
            return kind
    return ""


def _labelled(text: str, kind: str) -> Booking | None:
    fields = {m[0].strip().lower(): m[1].strip() for m in re.findall(r"^([^:\n]{2,30}):[ \t]*(.+)$", text, re.M)}
    obj = {"@type": next(t for t, k in TYPES.items() if k == kind),
           "reservationNumber": fields.get("reservation id") or fields.get("confirmation number"),
           "reservationStatus": "ReservationCancelled" if re.search(r"\bcancelled\b", fields.get("status", ""), re.I)
           else "ReservationConfirmed"}
    if kind == "ride":
        obj.update(pickupTime=fields.get("pickup time"), pickupLocation=fields.get("pickup location"),
                   provider="Lyft" if "lyft" in text.lower() else "Uber")
    elif kind == "reservation":
        obj.update(startTime=fields.get("reservation time"), endTime=fields.get("end time"),
                   reservationFor={"name": fields.get("restaurant")})
    elif kind == "hotel":
        obj.update(checkinDate=fields.get("check-in"), checkoutDate=fields.get("check-out"),
                   reservationFor={"name": fields.get("hotel")})
    elif kind == "flight":
        obj["reservationFor"] = {"departureTime": fields.get("departure time"), "arrivalTime": fields.get("arrival time"),
                                 "departureAirport": fields.get("from"), "arrivalAirport": fields.get("to"),
                                 "flightNumber": fields.get("flight")}
    return _schema(obj)


def _parts(msg, kind, depth=0):
    if depth > 10:
        return
    if msg.get("From"):
        kind = _sender_kind(str(msg["From"]), str(msg.get("Subject") or ""))
    if msg.is_multipart():
        for child in msg.get_payload()[:100]:
            yield from _parts(child, kind, depth + 1)
    else:
        yield msg, kind


def parse(raw: str, envelope: dict) -> list[Booking]:
    ""
    if len(raw) > MAX_MESSAGE:
        return []
    msg = Parser(policy=policy.default).parsestr(raw)
    sender = str(msg.get("From") or envelope.get("from_addr") or "")
    subject = str(msg.get("Subject") or envelope.get("subject") or "")
    kind = _sender_kind(sender, subject)
    rows, calendars, texts = [], [], []
    for part, part_kind in list(_parts(msg, kind))[:100]:
        mime = part.get_content_type()
        if mime not in ("text/html", "text/plain", "text/calendar", "application/ics", "application/icalendar"):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        try:
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            continue
        if mime == "text/html":
            markup = Markup()
            try:
                markup.feed(text)
                for obj in _objects(markup.objects):
                    b = _schema(obj)
                    if b:
                        rows.append(b)
            except (ValueError, TypeError, RecursionError):
                continue
        elif "BEGIN:VCALENDAR" in text and part_kind:
            calendars.extend(_ics(text, part_kind))
        elif mime == "text/plain":
            texts.append(text)
    rows = rows or calendars
    if not rows and kind:
        b = _labelled(sender + "\n" + "\n".join(texts), kind)
        if b:
            rows.append(b)
    return list({b.id: b for b in rows}.values())
