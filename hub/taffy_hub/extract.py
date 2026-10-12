"""Facts the hub saves on its own (facts.py): from the person's words after a chat turn, and from order emails.

After a chat turn, a cheap code check (signal) asks whether the person's own words state something lasting: a
preference, a brand, a store, a size, a price, a decision, a person, a date, a plan, or a correction ("actually",
"not anymore"). Only then Haiku (Colin's Claude plan, the same call briefings use) reads the words with the current
facts on the same subject, so it replaces instead of piling up, and returns at most a few one-sentence facts. It never
saves secrets, task instructions, one-day states, the Zip's own words, or anything from a sign-in card; code checks
its answer again (facts.save refuses secrets and keeps amounts and health private).

Order confirmation emails in the person's own mailboxes become purchase facts with items, quantities, sizes, price and
date (data JSON), which the running-low check (supplies.py) reads. Zips never place orders; carts they prepare are
saved by the Zip itself (save_fact, kind purchase).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime

from . import config, facts, home, store

log = logging.getLogger("taffy.extract")

ENABLED = False           # set by the running hub (app.lifespan); tests call the parts directly or turn it on
HAIKU_DAILY = 80          # extractor and order reads together, per home, on top of the proactive checks' own budget
TIMEOUT_SEC = 60
ORDERS_EVERY_SEC = 30 * 60
ORDER_BODY_MAX = 6000

# The person speaking about themselves (not "me" alone: "can you order coffee for me" is a request, not a fact).
_SELF = re.compile(r"\b(i|i'm|im|i've|ive|i'd|my|mine|we|we're|our|ours)\b", re.I)
_SIGNAL = re.compile(
    r"\b(prefer|preference|like|likes|love|hate|can't stand|cant stand|favorite|favourite|usual|usually|always|never|"
    r"every (day|week|month|morning|night|weekend)|allergic|vegetarian|vegan|gluten|dairy|"
    r"buy|bought|order|ordered|reorder|shop|shopping|brand|store|subscription|autoship|"
    r"decided|decide|going to|plan|planning|applied|apply|accepted|offer|quit|started|starting|switch|switched|"
    r"moving|moved|signed up|enrolled|booked|scheduled|interview|"
    r"wife|husband|partner|mom|mother|dad|father|brother|sister|son|daughter|niece|nephew|kid|kids|friend|boss|"
    r"doctor|dentist|accountant|landlord|vet|coach|teacher|manager|girlfriend|boyfriend|named|birthday|"
    r"actually|anymore|no longer|instead|changed my mind|not that|"
    r"give me|send me|show me|call me|celsius|fahrenheit|metric|grams|ounces|miles|kilometers|units|format|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week|next month|"
    r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|"
    r"dec(ember)?)\b"
    r"|\b\d+\s?(oz|ounce|ounces|lb|lbs|pound|pounds|pack|count|ct|ml|liter|liters|gallon|gallons|bag|box|case)\b"
    r"|\$\s?\d", re.I)
_PROPER_PLACE = re.compile(r"\b(from|at)\s+(the\s+)?[A-Z0-9][\w&'.-]+")


def signal(words: str) -> bool:
    """Do the person's words state something lasting? Code only, so ordinary chat costs nothing."""
    w = " ".join((words or "").split())
    if len(w.split()) < 4 or len(w) > 4000 or w.startswith("["):
        return False
    if not _SELF.search(w):
        return False
    return bool(_SIGNAL.search(w) or _PROPER_PLACE.search(w))


# MARK: Budget

def _budget_ok() -> bool:
    day = datetime.now(home.TZ).strftime("%Y-%m-%d")
    used = store.get_setting("facts_haiku", None) or {}
    if not isinstance(used, dict) or used.get("day") != day:
        used = {"day": day, "n": 0}
    if used["n"] >= HAIKU_DAILY:
        return False
    try:
        from . import health
        if not health.status.get("claude", True):
            return False
    except Exception:
        pass
    used["n"] += 1
    store.set_setting("facts_haiku", used)
    return True


async def ask_haiku(prompt: str) -> str:
    """Tests replace this."""
    from . import briefing
    return await briefing.ask_haiku(prompt, timeout=TIMEOUT_SEC)


def _json(text: str) -> dict | None:
    t = (text or "").strip()
    t = re.sub(r"^```(json)?|```$", "", t, flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        return None
    try:
        out = json.loads(t[i:j + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


# MARK: From the person's words

PROMPT = """You keep the shared notes a team of assistants has about {person}. Read what {person} just said to \
{zip} and decide whether it states a lasting fact worth remembering weeks from now, so {person} never has to say it \
again: a preference or usual (brand, product, size, flavor, store and its location, how {person} likes things \
done), a place, a purchase, a supply {person} keeps, a person in {person}'s life, a plan, a decision, or a date \
that matters. A correction ("actually", "not anymore", "switched") replaces the fact it corrects.

Rules:
- Only {person}'s own words count. {zip}'s message is context only: never save what {zip} said, suggested or did.
- Never save passwords, codes, account or card numbers, sign-in details, instructions for a task ("email Jordan"), \
one-off requests, moods, or anything only true today.
- One sentence per fact, about {person} in the third person, with every specific detail (brand, size, store and \
location, how they buy it, dates). Today is {today}; write real dates like "October 7, 2026", never "today". \
Birthdays and other yearly dates have no year.
- "subject" is the plain thing the fact is about, one or two common words ("coffee", "dog food", "contact lenses", \
"accountant", "job search"). When a current fact below is about the same thing, use its subject exactly.
- If a current fact below already says it, leave it out. If {person}'s words change or correct a current fact, \
give that fact's number in "replaces" and write the whole new fact.
- "confidence": "said" when {person} said it plainly, "inferred" when you had to read between the lines.
- "private": true only for health conditions, medications and account balances. Where {person} shops and what \
{person} buys are never private.
- At most three facts. Most messages have none.

Current facts that may be related:
{candidates}

{zip}'s message just before (context only): {before}

{person} said: {words}

{zip} answered (context only): {reply}

Answer with JSON only, no other text:
{{"facts": [{{"subject": "one to three words", "kind": "preference|place|purchase|supply|person|plan|decision|other", \
"text": "one sentence", "replaces": null, "confidence": "said", "private": false}}]}}
Use {{"facts": []}} when there is nothing lasting."""

_running: set[asyncio.Task] = set()


def after_turn(bot_id: str, words: str, reply: str = "", session_id: str | None = None, before: str = "",
               source: str = "chat") -> asyncio.Task | None:
    """Called when a chat turn ends: when the person's words carry a signal, the extractor runs in the background.
    Never blocks the chat and never raises."""
    try:
        if not ENABLED or not signal(words):
            return None
        task = asyncio.get_running_loop().create_task(_extract(bot_id, words, reply, session_id, before, source))
    except Exception:
        log.warning("could not start the fact extractor", exc_info=True)
        return None
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


def _candidates(bot_id: str, words: str, reply: str = "", session_id: str | None = None) -> list[dict]:
    """The current facts the extractor sees, so it can skip what is saved and replace what changed. A correction
    often names nothing the old fact says ("actually, not that brand, I switched to Lavazza"), so besides facts that
    match the words or the Zip's reply, it sees facts this conversation was given or saved, and the newest facts."""
    found = {f["id"]: f for f in facts.search(words, bot_id, limit=8)}
    for f in facts.search(reply, bot_id, limit=4) if reply else []:
        found.setdefault(f["id"], f)
    c = facts._db()
    if session_id:
        for f in c.execute("SELECT f.* FROM facts f LEFT JOIN fact_shown s ON s.fact_id = f.id AND s.session_id = ? "
                           "WHERE f.replaced_by IS NULL AND (f.session_id = ? OR s.fact_id IS NOT NULL) "
                           "AND f.kind NOT IN ('other') ORDER BY f.id DESC LIMIT 6", (session_id, session_id)):
            found.setdefault(f["id"], dict(f))
    subjects = {f["subject"] for f in found.values() if f["subject"]}
    for s in subjects:
        for f in c.execute("SELECT * FROM facts WHERE replaced_by IS NULL AND subject=? ORDER BY id DESC LIMIT 5", (s,)):
            found.setdefault(f["id"], dict(f))
    where, args = facts._visible(bot_id)
    for f in c.execute(f"SELECT * FROM facts WHERE replaced_by IS NULL AND {where} AND kind IN ('preference', "
                       f"'place', 'supply', 'purchase') ORDER BY observed_at DESC, id DESC LIMIT 4", args):
        found.setdefault(f["id"], dict(f))
    return list(found.values())[:15]


async def _extract(bot_id: str, words: str, reply: str, session_id: str | None, before: str, source: str) -> list[int]:
    from . import who
    cands = _candidates(bot_id, words, reply, session_id)
    if not _budget_ok():
        log.info("fact extractor skipped: today's budget is used")
        return []
    zip_name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "the assistant"
    prompt = PROMPT.format(
        person=who.first(), zip=zip_name, today=datetime.now(home.TZ).strftime("%A, %B %-d, %Y"),
        candidates="\n".join(f"[{f['id']}] ({f['subject'] or 'no subject'}, {f['kind']}) {f['text']}" for f in cands)
        or "None.", before=(before or "None.")[:600], words=words[:2000], reply=(reply or "")[:800])
    began = time.monotonic()
    try:
        out = _json(await ask_haiku(prompt))
    except Exception:
        log.warning("fact extractor call failed", exc_info=True)
        return []
    saved = save_answer(out, bot_id, session_id, source, {f["id"] for f in cands})
    log.info("fact extractor for %s: %d saved in %.1f s", bot_id, len(saved), time.monotonic() - began)
    return saved


def save_answer(out: dict | None, bot_id: str, session_id: str | None, source: str, known: set[int],
                source_ref: str | None = None, observed_at: float | None = None) -> list[int]:
    """Checks the extractor's answer in code and saves what passes. A "replaces" must be a fact it was shown."""
    saved = []
    items = (out or {}).get("facts") if isinstance(out, dict) else None
    for item in (items or [])[:3]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text or facts.secret(text):
            continue
        rep = item.get("replaces")
        rep = int(rep) if isinstance(rep, (int, float)) or str(rep or "").isdigit() else None
        if rep is not None and rep not in known:
            rep = None
        conf = str(item.get("confidence") or "said")
        try:
            fid, _ = facts.save("hub" if bot_id not in config.BOTS_BY_ID else bot_id, text,
                                subject=str(item.get("subject") or ""), kind=str(item.get("kind") or "other"),
                                replaces=rep, session_id=session_id, source=source, source_ref=source_ref,
                                confidence=conf if conf in ("said", "inferred") else "said",
                                # Capability first: Haiku's "private" counts only for health details or amounts.
                                private=bool(item.get("private")) and facts.sensitive(text),
                                data=item.get("data") if isinstance(
                                    item.get("data"), dict) else None, observed_at=observed_at, limits=False)
        except facts.FactError as e:
            log.info("extracted fact not saved: %s", e)
            continue
        if fid:
            saved.append(fid)
    return saved


async def after_call(bot_id: str, person_lines: list[str], call_id: str = "") -> list[int]:
    """What the person said on a call, read the same way once the call ends (only their lines)."""
    words = " ".join(person_lines)[-3000:]
    if not ENABLED or not signal(words):
        return []
    return await _extract(bot_id, words, "", None, "", "call")


# MARK: Order emails

_ORDER_SUBJECT = re.compile(r"\b(your order|^your\b.{0,40}\border\b|order (confirmation|confirmed|received|#|number|no\.?)|"
                            r"thanks for (your )?(order|shopping|purchase)|thank you for (your )?(order|purchase)|"
                            r"order .{0,40} (has been )?(placed|confirmed)|receipt for|your receipt|purchase "
                            r"confirmation|we('ve| have) received your order|delivery order)", re.I)
_NOT_ORDER = re.compile(r"\b(shipped|on its way|out for delivery|delivered|return|refund|cancel)", re.I)

ORDER_PROMPT = """Read this order confirmation email {person} received and list what was bought. Today is {today}.

Answer with JSON only:
{{"order": true, "store": "store name", "ordered_on": "YYYY-MM-DD", "order_id": "as printed or null", \
"items": [{{"name": "product as named, with brand", "subject": "what it is in one or two common words, e.g. \
coffee, dog food, contact lenses, oat milk", "size": "e.g. 12 oz or null", "quantity": 1, \
"price": "e.g. $8.99 or null", "every": "how often it repeats when the email says (e.g. 6 weeks), else null"}}], \
"total": "e.g. $23.10 or null"}}
If it is not a confirmation of something {person} bought (a shipping notice, an ad, a newsletter, a return), answer \
{{"order": false}}.

From: {sender}
Subject: {subject}
Date: {date}

{body}"""


def is_order(subject: str) -> bool:
    return bool(_ORDER_SUBJECT.search(subject or "")) and not _NOT_ORDER.search(subject or "")


def _seen() -> list[str]:
    v = store.get_setting("facts_orders_seen", None)
    return list(v) if isinstance(v, list) else []


def _mark_seen(key: str) -> None:
    seen = _seen()
    if key not in seen:
        seen.append(key)
    store.set_setting("facts_orders_seen", seen[-500:])


def purchase_facts(order: dict, person: str) -> list[tuple[str, str, dict]]:
    """One purchase fact per item: (subject, sentence, data)."""
    store_name = " ".join(str(order.get("store") or "").split())[:60]
    day = str(order.get("ordered_on") or "")
    try:
        when = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=home.TZ)
        day_words = when.strftime("%b %-d, %Y")
    except ValueError:
        when, day_words = None, ""
    out = []
    for it in (order.get("items") or [])[:12]:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        name = " ".join(str(it["name"]).split())[:120]
        size = " ".join(str(it.get("size") or "").split())[:30]
        try:
            qty = max(1, int(float(it.get("quantity") or 1)))
        except (TypeError, ValueError):
            qty = 1
        price = " ".join(str(it.get("price") or "").split())[:20]
        what = f"{qty} x {name}" + (f" ({size})" if size and size.lower() not in name.lower() else "")
        text = (f"{person} bought {what}" + (f" from {store_name}" if store_name else "")
                + (f" on {day_words}" if day_words else "") + (f" for {price}" if price else "") + ".")
        every = " ".join(str(it.get("every") or "").split())[:30]
        data = {"item": name, "size": size or None, "quantity": qty, "price": price or None, "store": store_name or None,
                "ordered_at": when.timestamp() if when else None, "order_id": order.get("order_id"),
                "every": every if every and every.lower() != "null" else None}
        given = facts._norm_subject(str(it.get("subject") or ""))
        subject = given if given and given != "null" else _item_subject(name)
        out.append((subject, text, data))
    return out


_SUBJECT_WORDS = re.compile(r"[a-z]+")
_GENERIC = {"the", "and", "with", "for", "pack", "count", "size", "original", "new", "classic", "organic", "whole",
            "ground", "bag", "box", "oz", "lb", "of", "in", "a", "x"}


def _item_subject(name: str) -> str:
    """A short subject for a bought item: its last plain noun or two ("coffee", "paper towels")."""
    ws = [w for w in _SUBJECT_WORDS.findall(name.lower()) if w not in _GENERIC and len(w) > 2]
    return " ".join(ws[-2:]) if ws else name.lower()[:30]


async def read_order(account: str, e: dict) -> list[int]:
    """One order email: Haiku reads it, code saves a purchase fact per item (source mail, from a receipt)."""
    from . import mail, who
    key = f"{account}:{e['folder']}:{e['id']}"
    if key in _seen():
        return []
    try:
        body = await asyncio.to_thread(mail.read, account, e["folder"], e["id"])
    except Exception as err:
        log.info("order email not read: %s", err)
        return []
    if not _budget_ok():
        return []
    prompt = ORDER_PROMPT.format(person=who.first(), today=datetime.now(home.TZ).strftime("%B %-d, %Y"),
                                 sender=f"{e.get('from_name') or ''} <{e.get('from_addr') or ''}>",
                                 subject=e.get("subject") or "",
                                 date=datetime.fromtimestamp(e["date"], home.TZ).strftime("%Y-%m-%d"),
                                 body=body[:ORDER_BODY_MAX])
    try:
        out = _json(await ask_haiku(prompt))
    except Exception:
        log.warning("order email read failed", exc_info=True)
        return []
    _mark_seen(key)
    if not out or not out.get("order"):
        return []
    if not out.get("ordered_on"):
        out["ordered_on"] = datetime.fromtimestamp(e["date"], home.TZ).strftime("%Y-%m-%d")
    saved = []
    for subject, text, data in purchase_facts(out, who.first()):
        try:
            fid, _ = facts.save("hub", text, subject=subject, kind="purchase", source="mail", source_ref=key,
                                confidence="receipt", data=data, observed_at=data.get("ordered_at") or e["date"],
                                limits=False)
        except facts.FactError as err:
            log.info("purchase fact not saved: %s", err)
            continue
        if fid:
            saved.append(fid)
    log.info("order email %s: %d purchase facts", key, len(saved))
    return saved


async def check_orders() -> list[int]:
    """New order confirmations in the person's own mailboxes (INBOX, newest 50)."""
    from . import mail
    saved = []
    for account in list(mail.ACCOUNTS):
        try:
            items = await asyncio.to_thread(mail.envelopes, account, "INBOX", 50)
        except Exception as err:
            log.info("mail not read for orders: %s", err)
            continue
        for e in sorted(items, key=lambda x: x["date"]):
            if time.time() - e["date"] > 60 * 86400 or not is_order(e.get("subject") or ""):
                continue
            saved += await read_order(account, e)
    return saved


async def watch_orders() -> None:
    """Runs with the hub."""
    await asyncio.sleep(90)
    if not ENABLED:
        return
    while True:
        try:
            await check_orders()
        except Exception:
            log.warning("order email check failed", exc_info=True)
        await asyncio.sleep(ORDERS_EVERY_SEC)
