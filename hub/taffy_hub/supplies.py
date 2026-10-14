"""Running low (a proactive noticer; the rules and the card are in notices.py): one step ahead on what the person buys
again and again.

Colin, Oct 10: "Two weeks ago we bought three weeks of coffee, so you're probably low. Want me to order more?"

The purchases are facts (facts.py, kind purchase, with data: item, size, quantity, store, ordered_at), from order
emails (extract.py) and from carts the Zips prepared. How long one lasts comes from either:

- the person's own pattern: two or more orders of the same thing, and the usual gap between them; or
- what the person said ("a bag lasts me about a week"), saved as a supply or preference fact on the same subject,
  times the quantity bought.

Nothing is guessed beyond that, so there is no card from a single order with no known pace. Autoship and
subscriptions refill themselves and are skipped. The card comes RUN_OUT_LEAD_DAYS before the predicted day (time to
get it), never earlier, and closes a few days after; a newer order of the same thing ends it. The answer gets a cart
ready (Zips never order or pay; the person checks out).
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime

from . import facts, notices, who
from .notices import Candidate, Option

RUN_OUT_LEAD_DAYS = 2
LATE_DAYS = 3
MIN_GAP_DAYS = 3
_AUTO = re.compile(r"\b(autoship|auto-ship|subscribe|subscription|auto-?refill|every \d+ (days?|weeks?|months?))\b", re.I)
_LASTS = re.compile(r"\b(?:lasts?|last me|goes? through|go through|uses? up|runs? out (?:in|after)|every)\s+"
                    r"(?:about |around |roughly |a |one )?(?P<n>\d+(?:\.\d+)?|a|one|two|three|four|five|six)?\s*"
                    r"(?P<unit>days?|weeks?|months?)\b", re.I)
_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _data(f: dict) -> dict:
    try:
        d = json.loads(f.get("data") or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def lasts_days(text: str) -> float | None:
    """'a bag lasts me about a week' -> 7. None when the text says no pace."""
    m = _LASTS.search(text or "")
    if not m:
        return None
    n = m.group("n")
    num = _WORDS.get((n or "a").lower()) if not (n or "").replace(".", "").isdigit() else float(n)
    unit = m.group("unit").lower().rstrip("s")
    return float(num or 1) * _UNIT_DAYS[unit]


def predictions(now: float) -> list[dict]:
    """[{subject, item, store, last, runs_out, how, quantity}] for things bought more than once or with a known pace."""
    buys: dict[str, list[dict]] = {}
    for f in facts.current(None, kinds=("purchase",), limit=2000):
        d = _data(f)
        when = d.get("ordered_at") or f.get("observed_at")
        if not when or not f.get("subject"):
            continue
        buys.setdefault(f["subject"], []).append({**d, "ordered_at": float(when), "text": f["text"], "id": f["id"]})
    paces: dict[str, float] = {}
    for f in facts.current(None, kinds=("supply", "preference"), limit=2000):
        days = lasts_days(f["text"])
        if days and f.get("subject") and not _AUTO.search(f["text"]):
            paces[f["subject"]] = days
    out = []
    for subject, items in buys.items():
        items.sort(key=lambda x: x["ordered_at"])
        last = items[-1]
        if any(_AUTO.search(x["text"]) or x.get("every") for x in items[-2:]):
            continue
        trips = [items[0]["ordered_at"]]
        for x in items[1:]:
            if x["ordered_at"] - trips[-1] >= MIN_GAP_DAYS * 86400:
                trips.append(x["ordered_at"])
        last_trip = trips[-1]
        if len(trips) >= 2:
            gaps = [b - a for a, b in zip(trips, trips[1:])]
            runs_out = last_trip + statistics.median(gaps)
            how = f"you usually buy it every {round(statistics.median(gaps) / 86400)} days"
        elif subject in paces:
            qty = sum(int(x.get("quantity") or 1) for x in items if x["ordered_at"] >= last_trip)
            runs_out = last_trip + paces[subject] * 86400 * max(1, qty)
            how = f"that is about {round(paces[subject] * max(1, qty) / 7)} weeks' worth"
        else:
            continue
        out.append({"subject": subject, "item": last.get("item") or subject, "size": last.get("size"),
                    "store": last.get("store"), "last": last_trip, "runs_out": runs_out, "how": how,
                    "quantity": int(last.get("quantity") or 1), "fact": last["id"]})
    return out


def _ago(ts: float, now: float) -> str:
    days = round((now - ts) / 86400)
    if days <= 1:
        return "Yesterday"
    if days < 14:
        return f"{days} days ago"
    return f"{round(days / 7)} weeks ago"


async def running_low(src=None) -> list[Candidate]:
    now = notices.now()
    found = []
    for p in predictions(now):
        start = p["runs_out"] - RUN_OUT_LEAD_DAYS * 86400
        if not start <= now < p["runs_out"] + LATE_DAYS * 86400:
            continue
        found.append(_card(p, now))
    return sorted(found, key=lambda c: c.deadline or 0)


def _card(p: dict, now: float) -> Candidate:
    item = p["item"]
    what = (f"{p['quantity']} x " if p["quantity"] > 1 else "") + item + (f" ({p['size']})" if p.get("size") else "")
    at_store = f" from {p['store']}" if p.get("store") else ""
    bought = datetime.fromtimestamp(p["last"], notices.ET)
    when = notices.date_short(p["last"])
    out_day = datetime.fromtimestamp(p["runs_out"], notices.ET).strftime("%A, %b %-d")
    evidence = (f"{_ago(p['last'], now)} you bought {what}{at_store}, and {p['how']}, so you're probably running low "
                f"around {out_day}.")
    return Candidate(
        noticer="running_low", subject=f"running_low:{p['subject']}", owner="default",
        version=str(int(p["last"])), evidence=evidence,
        question=f"Want me to get a cart ready with the same {p['subject']}?",
        lock=f"Running low on {p['subject']}?"[:60],
        facts={"who": who.first(), "what": what + at_store, "when": f"bought {when}, runs out around {out_day}"},
        consequence=f"running out of {p['subject']}", deadline=p["runs_out"] + LATE_DAYS * 86400,
        expires=p["runs_out"] + LATE_DAYS * 86400,
        excerpt=f"Last order: {what}{at_store} on {bought.strftime('%b %-d')}.",
        options=[Option("Get a cart ready", "act",
                        say=f"Please get a cart ready with the same {p['subject']} as last time: {what}{at_store}. "
                            "Stop at checkout so I can finish it."),
                 Option("I still have some", "wait", wait_days=4),
                 Option("Already got more", "done", follow="Good. I'll count from your next order.")])


FINDERS = {"running_low": running_low}
