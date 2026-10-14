"""Proactivity phase 2: the money noticers, over Tally's transactions (read the way Money's `tally` tool reads them;
Tally keeps no balances) and Money's own notes.

  charge  a charge from a place Money has never seen in 200 days of purchases or its notes, or a regular monthly
          charge that went up
  bill    a bill in Money's notes due in the next three days, not on autopay, with no matching payment in Tally

Both are money questions: the lock screen shows the specific line (the place and the amount; Colin, Oct 10: "Bill names
and amounts should be on the lock screen once you sign in with Face ID"), and the answers there need Face ID first.
No option ever pays, moves money or contacts a bank; the answers are Colin's words to Zero, or a note for Money.
"""

import re
from datetime import datetime

from . import notices
from . import noticers as base
from .calendar_noticers import words
from .notices import Candidate, Option
P = notices.plain

NEW_WINDOW_DAYS = 2
HISTORY_DAYS = 200


def _day(p: dict) -> float | None:
    try:
        return datetime.strptime(str(p.get("date") or "")[:10], "%Y-%m-%d").replace(tzinfo=notices.ET).timestamp()
    except ValueError:
        return None


def _amount(p: dict) -> float:
    try:
        return float(p.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def merchant(p: dict) -> str:
    return str(p.get("merchant") or " ".join(re.findall(r"[a-z0-9]+", str(p.get("name") or "").lower()))).strip()


def money(x: float) -> str:
    return f"${x:,.2f}"


# MARK: 9. A charge Money has never seen

async def charge(src: base.Sources) -> list[Candidate]:
    rows = await src.purchases()
    if not rows:
        return []
    t = notices.now()
    today = notices.day_start(t)
    notes = " ".join(src.notes_of("money")).lower()
    found = []
    for p in rows:
        d, amt = _day(p), _amount(p)
        if d is None or d < today - NEW_WINDOW_DAYS * 86400 or amt < 1:
            continue
        name = " ".join(str(p.get("name") or "").split())
        if not name or name.lower().startswith("venmo"):
            continue
        key = merchant(p)
        history = [q for q in rows if q is not p and merchant(q) == key and (_day(q) or 0) < d
                   and (_day(q) or 0) >= d - HISTORY_DAYS * 86400]
        if not history:
            if name.lower() in notes or (key and key in notes):
                continue
            found.append(_new_charge(p, name, d, amt))
            continue
        past = sorted(history, key=lambda q: _day(q) or 0)
        if len(past) >= 2:
            last, before = past[-1], past[-2]
            gap = ((_day(last) or 0) - (_day(before) or 0)) / 86400
            prev = _amount(last)
            if 25 <= gap <= 35 and 25 <= (d - (_day(last) or 0)) / 86400 <= 35 and amt >= prev * 1.05 and \
                    amt - prev >= 1:
                found.append(_went_up(p, name, d, amt, prev))
    return found


def _new_charge(p: dict, name: str, d: float, amt: float) -> Candidate:
    """A new place in the spending: a filing question, never a fraud alarm (#70: a purchase flagged from a receipt
    felt creepy). It waits in the chat and the morning brief; it has no deadline, so it never pushes."""
    on = notices._et(d).strftime("%b %-d")
    return Candidate(
        noticer="charge", subject=f"charge:{p.get('id') or base._slug(name + on)}", owner="money",
        evidence=f"Your card shows a {money(amt)} charge from {name} on {on}, a place Money hasn't seen in your "
                 "spending before.",
        question="Should Money count it as yours?",
        facts={"what": name, "amount": money(amt), "when": on, "source": "your card transactions in Tally"},
        sensitive=True, consequence="Money can't sort it into the right category until it knows.",
        options=[Option("Yes, it's mine", "decide",
                        say=f"The {money(amt)} charge from {P(name)} on {on} is mine. Please let Money know so it can "
                            "sort it.", follow="Got it. I'll let Money know."),
                 Option("Look into it", "act",
                        say=f"I'm not sure about the {money(amt)} charge from {P(name)} on {on}. Please help me find "
                            "out what it is. Don't contact the bank or change anything without asking me.")])


def _went_up(p: dict, name: str, d: float, amt: float, prev: float) -> Candidate:
    on = notices._et(d).strftime("%b %-d")
    return Candidate(
        noticer="charge", subject=f"charge-up:{merchant(p)}:{notices._et(d).strftime('%Y-%m')}", owner="money",
        evidence=f"Your regular {name} charge went from {money(prev)} to {money(amt)} on {on}.",
        question="Want Money to look into why?",
        facts={"what": name, "amount": money(amt), "when": on}, sensitive=True,
        consequence="A price went up without notice.",
        options=[Option("Look into it", "act",
                        say=f"My {P(name)} charge went from {money(prev)} to {money(amt)} on {on}. Please find out why. "
                            "Don't contact anyone or change anything without asking me."),
                 Option("That's expected", "done", follow="Okay. I'll remember it went up.")])


# MARK: 10. A bill inside three days with nothing showing it paid

BILL = re.compile(r"\b(bill|payment|rent|loan|mortgage|credit card|card|insurance|utilities|phone|internet|"
                  r"tuition|due)\b", re.I)
DOM = re.compile(r"\b(?:on|by|due(?: on| by)?|every month on|monthly on|each month on)\s+(?:the\s+)?"
                 r"(\d{1,2})(?:st|nd|rd|th)\b", re.I)
AUTOPAY = re.compile(r"\b(auto-?pay|autopay|automatic(ally)? (payment|paid|draft)|autodraft|auto-?debit)\b", re.I)
AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d\d)?)")
BILL_DAYS = 3


def _next_dom(day: int, t: float) -> float | None:
    d = notices._et(notices.day_start(t))
    for months in (0, 1):
        y, m = d.year + (d.month + months - 1) // 12, (d.month + months - 1) % 12 + 1
        try:
            due = datetime(y, m, day, tzinfo=notices.ET)
        except ValueError:
            continue
        if due.date() >= d.date():
            return due.timestamp()
    return None


def _name(entry: str) -> str:
    first = re.split(r"(?<=[.;\n])\s+", entry.strip())[0]
    m = re.search(r"\$|\bdue\b|\bon the\b|\bevery\b|\bmonthly\b|:", first, re.I)
    name = first[:m.start()] if m else first
    name = " ".join(name.split()).strip(" -*,(").removesuffix(" of").strip()
    return (name[:1].upper() + name[1:])[:40] if name else "A bill"


async def bill(src: base.Sources) -> list[Candidate]:
    t = notices.now()
    rows = None
    found: dict[str, Candidate] = {}
    for entry in src.notes_of("money"):
        if not BILL.search(entry) or AUTOPAY.search(entry):
            continue
        am = AMOUNT.search(entry)
        if not am:
            continue
        amt = float(am.group(1).replace(",", ""))
        dues = [d for d in base.dates_in(entry, t - 2 * 86400) if 0 <= notices.days_ahead(d) <= BILL_DAYS]
        for m in DOM.finditer(entry):
            d = _next_dom(int(m.group(1)), t)
            if d is not None and 0 <= notices.days_ahead(d) <= BILL_DAYS:
                dues.append(d)
        if not dues:
            continue
        due = min(dues)
        name = _name(entry)
        if rows is None:
            rows = await src.purchases()
        if _paid(rows, name, amt, due):
            continue
        c = _bill(name, amt, due)
        found.setdefault(c.subject, c)
    return list(found.values())


def _paid(rows: list[dict], name: str, amt: float, due: float) -> bool:
    """A payment in Tally from 12 days before the due date on, of about that amount or to a place the note names."""
    for p in rows:
        d = _day(p)
        if d is None or d < due - 12 * 86400:
            continue
        a = _amount(p)
        if abs(a - amt) <= max(1.0, amt * 0.02):
            return True
        if words(name) & words(str(p.get("name") or "")) and a >= amt * 0.9:
            return True
    return False


def _bill(name: str, amt: float, due: float) -> Candidate:
    on = notices._et(due).strftime("%A, %b %-d")
    pay_day = "today" if due - notices.now() < 1.5 * 86400 else notices.day(due - 86400)
    return Candidate(
        noticer="bill", subject=f"bill:{base._slug(name)}:{notices._et(due).strftime('%Y%m%d')}", owner="money",
        evidence=f"{name} of {money(amt)} is due {on}, and I don't see it paid in Tally yet.",
        question="Is it on autopay, or will you pay it yourself?",
        facts={"what": name, "amount": money(amt), "when": on}, sensitive=True, deadline=due + 17 * 3600,
        options=[Option("It's on autopay", "decide",
                        say=f"The {P(name)} payment of {money(amt)} due {on} is on autopay. Please ask Money to note "
                            "that so it isn't flagged again.", follow="Got it. Money will note it."),
                 Option(f"I'll pay it {pay_day}", "done", follow="Okay. I'll leave it with you."),
                 Option("Already paid", "close")])


FINDERS = {"charge": charge, "bill": bill}
