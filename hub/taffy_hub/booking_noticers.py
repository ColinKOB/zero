""

import itertools
import json
import re
from dataclasses import asdict

from . import bookings, calendar_noticers as calendar, extract, facts, home, noticers as base, notices
from .notices import Candidate, Option

BOOKING_SUBJECT = re.compile(r"\b(booked|booking|confirmation|confirmed|reservation|itinerary|flight|"
                             r"scheduled ride|pickup|pick-up|rescheduled|cancelled|canceled)\b", re.I)
HORIZON = 14 * 86400


def when(ts: float) -> str:
    return f"{calendar.when_full(ts)} {home.tz_word()}"


def same_place(a: str, b: str) -> bool:
    a, b = re.sub(r"\W+", " ", a).strip().lower(), re.sub(r"\W+", " ", b).strip().lower()
    if not a or not b:
        return False
    return a == b or (len(a) >= 3 and re.search(r"\b" + re.escape(a) + r"\b", b) is not None) or (
        len(b) >= 3 and re.search(r"\b" + re.escape(b) + r"\b", a) is not None)


def _overlap(a: dict, b: dict) -> bool:
    if a["start"] == a["end"] and b["start"] == b["end"]:
        return a["start"] == b["start"]
    if a["start"] == a["end"]:
        return b["start"] <= a["start"] < b["end"]
    if b["start"] == b["end"]:
        return a["start"] <= b["start"] < a["end"]
    return max(a["start"], b["start"]) < min(a["end"], b["end"])


def _candidate(a: dict, b: dict, sentence: str, deadline: float, ride: dict | None = None) -> Candidate:
    subject = "booking:" + bookings.key(*sorted((a["id"], b["id"])))
    version = bookings.key(str(a["start"]), str(a["end"]), str(b["start"]), str(b["end"]))
    title_a, title_b = notices.plain(a["title"], 90), notices.plain(b["title"], 90)
    evidence = notices.plain(sentence, 500)
    action = ("Move the ride" if ride else "Help me fix the clash")
    return Candidate(
        noticer="booking", subject=subject, owner=base.owner_for(a.get("account", "")), version=version,
        evidence=evidence, question="Want me to check the bookings?", deadline=deadline, event_timed=True,
        haiku=False, lead=True, facts={"what": f"{title_a} and {title_b}", "when": when(deadline)},
        ref={"booking_ids": [x["id"] for x in (a, b) if x.get("kind")],
             "calendar_id": b["id"] if not b.get("kind") else None},
        options=[Option(action, "act", say=f"{evidence} Please check the current bookings and "
                        + ("prepare a new pickup time" if ride else "help me resolve the clash")
                        + "; show me an okay card before changing or cancelling anything."),
                 Option("Tell me the times", "act", say=f"{evidence} Tell me the current times for both, "
                        "including the time zones, without changing anything."),
                 Option("I've handled it", "close")])


def _pair(a: dict, b: dict) -> Candidate | None:
    if a["reservation"] == b["reservation"]:
        return None
    if {a["kind"], b["kind"]} == {"flight", "ride"}:
        flight, ride = (a, b) if a["kind"] == "flight" else (b, a)

        if same_place(flight["destination"], ride["place"]) and 0 < flight["end"] - ride["start"] <= 6 * 3600:
            return _candidate(flight, ride, f"{ride['title']} picks you up {when(ride['start'])} "
                              f"before {flight['title']} lands {when(flight['end'])}, "
                              "so check the pickup time.", ride["start"], ride)
        return None
    if "hotel" in (a["kind"], b["kind"]):
        hotel, flight = (a, b) if a["kind"] == "hotel" else (b, a)
        if flight["kind"] != "flight" or not same_place(hotel["city"], flight["city"]):
            return None
        if 0 < flight["end"] - hotel["end"] <= 3 * 86400:
            return _candidate(flight, hotel, f"{flight['title']} lands {when(flight['end'])} "
                              f"after {hotel['title']} ends {when(hotel['end'])}, "
                              "so check the hotel dates.", min(flight["start"], hotel["end"]))
        return None
    if _overlap(a, b):
        return _candidate(a, b, f"{a['title']} at {when(a['start'])} overlaps "
                          f"{b['title']} at {when(b['start'])}, so check which booking to move.",
                          max(a["start"], b["start"]))
    return None


async def booking(src: base.Sources) -> list[Candidate]:
    t = notices.now()
    src.booking_reads = 0
    for account in base.ACCOUNTS:
        folder = base.ALL_MAIL.get(account) or "INBOX"
        envelopes = await src.envelopes(account, folder)
        for e in sorted(envelopes, key=lambda e: e["date"]):
            if not 0 <= t - e["date"] <= 180 * 86400 or not BOOKING_SUBJECT.search(e.get("subject") or ""):
                continue
            raw = await src.booking_body(e)
            if raw:
                try:
                    extract.save_bookings(e, raw)
                except facts.FactError as err:
                    base.log.info("booking not saved: %s", err)
    known = {}
    for f in facts._db().execute("SELECT data FROM facts WHERE source='mail' AND kind='plan' "
                                "AND subject LIKE 'booking %' AND replaced_by IS NULL"):
        b = json.loads(f["data"] or "{}")
        if b.get("booking"):
            known[b["id"]] = b
    rows = [b for b in known.values() if not b["cancelled"] and t < b["end"] and b["start"] <= t + HORIZON]
    out = []
    for a, b in itertools.combinations(rows, 2):
        c = _pair(a, b)
        if c and c.deadline > t:
            out.append(c)
    events = src.calendar()
    for b in rows:
        if b["kind"] == "hotel":
            continue
        for e in events:
            if (str(e.get("status") or "").lower() == "cancelled" or not calendar.commitment(e)
                    or e["end"] <= t or not _overlap(b, e)):
                continue


            if (e["start"] == b["start"] and e["end"] == b["end"]
                    and (same_place(b["place"], e.get("location", "")) or calendar.alike(b["title"], e["title"]))):
                continue
            event = {**e, "id": "calendar:" + str(e["id"])}
            sentence = (f"{b['title']} at {when(b['start'])} overlaps {e['title']} "
                        f"at {when(e['start'])}, so check which time to move.")
            if max(b["start"], e["start"]) > t:
                out.append(_candidate(b, event, sentence, max(b["start"], e["start"])))
    _sync_cards(out, known, events)
    return out


def _sync_cards(candidates: list[Candidate], known: dict, events: list[dict]) -> None:
    current = {c.subject: c for c in candidates}
    calendar_ids = {"calendar:" + str(e["id"]) for e in events}
    for old in notices._db().execute("SELECT * FROM notices WHERE noticer='booking' AND state='open'").fetchall():
        old = dict(old)
        card = json.loads(old["card"] or "{}")
        candidate = current.get(old["subject"])
        if candidate:
            if card.get("evidence") != candidate.evidence:
                card.update(evidence=candidate.evidence, options=[asdict(o) for o in candidate.options],
                            ref=candidate.ref, lock=notices.compose(candidate.evidence, candidate.question))
                notices._set(old["id"], card=json.dumps(card), deadline=candidate.deadline)
                notices._update_card(old, evidence=candidate.evidence,
                                     options=[o.text for o in candidate.options])
            continue
        refs = card.get("ref") or {}
        ids = refs.get("booking_ids") or []
        if not ids or not all(identity in known for identity in ids):
            continue
        cancelled = any(known[identity]["cancelled"] for identity in ids)

        if not cancelled and refs.get("calendar_id") and not calendar_ids:
            continue
        notices._end(old, "handled", end_reason="The booking clash is resolved.")
        notices._follow(notices.by_id(old["id"]), "The bookings no longer clash.")


FINDERS = {"booking": booking}
