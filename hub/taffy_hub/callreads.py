""

















import asyncio
import json
import logging
import re
import time
from datetime import datetime

from . import config, home, who

log = logging.getLogger("taffy.callreads")

PLACES = ("texts", "mail", "calendar", "todo", "memory")

READERS_USE = {"imessage": {"read"}, "mail": {"envelopes", "read"}, "calcache": {"events_between"},
               "todo": {"describe"}, "facts": {"search"}}
READ_TIMEOUT_SEC = 6
WORDS_MAX, WORD_CHARS, DAYS_MAX = 6, 40, 60
TEXT_MAX = 3500
MAIL_PAGE = 40
_WORD = re.compile(r"[\w@.'+-]{1,%d}" % WORD_CHARS)

NOTHING = ("Nothing was found there. If it should be there, call ask_bot with the request so it is looked for "
           "properly.")


def tool() -> dict:
    ""

    return {
        "type": "function", "name": "read",
        "description": f"Reads what is already in {who.possessive()} text messages, mail, calendar, to-do list or saved "
                       "notes, and returns it. It only reads: it cannot send, write, add or change anything.",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["place"], "properties": {
            "place": {"type": "string", "enum": list(PLACES)},
            "words": {"type": "array", "items": {"type": "string"}, "maxItems": WORDS_MAX,
                      "description": "Single words to search for, such as a person's first or last name. Empty for "
                                     "the newest texts, the whole to-do list, or everything on the calendar."},
            "days_back": {"type": "integer", "minimum": 0, "maximum": DAYS_MAX,
                          "description": "Mail and texts: how far back to look, 14 when not said. Calendar: days "
                                         "before today."},
            "days_ahead": {"type": "integer", "minimum": 0, "maximum": DAYS_MAX,
                           "description": "Calendar: days after today (tomorrow is 1, this week is 7)."}}},
    }


def may_read(bot_id: str) -> bool:
    ""
    return bot_id == config.LEAD.id and not config.is_sandbox(bot_id)


def shape(raw) -> dict | None:
    ""
    if isinstance(raw, str):
        found = re.search(r"\{.*\}", raw, re.S)
        try:
            raw = json.loads(found.group(0)) if found else None
        except ValueError:
            return None
    if not isinstance(raw, dict) or raw.get("place") not in PLACES:
        return None
    words = raw.get("words") or []
    if not isinstance(words, list) or len(words) > WORDS_MAX or not all(isinstance(w, str) and _WORD.fullmatch(w)
                                                                         for w in words):
        return None
    days = []
    for key in ("days_back", "days_ahead"):
        value = raw.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= DAYS_MAX:
            return None
        days.append(value)
    return {"place": raw["place"], "words": words, "days_back": days[0], "days_ahead": days[1]}


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, home.TZ).strftime("%a %b %-d, %-I:%M %p")


async def _texts(bot_id: str, m: dict) -> str:
    ""


    from . import imessage
    if not m["words"]:
        found = await imessage.read(bot_id, {"action": "recent", "limit": 10})
        return json.dumps(found, ensure_ascii=False) if (found or {}).get("conversations") else ""
    found = await imessage.read(bot_id, {"action": "search", "query": " ".join(m["words"][:2]), "limit": 15,
                                         "context": 0})
    matches = [x for x in (found or {}).get("messages") or [] if isinstance(x, dict)]
    if not matches:
        return ""
    out = "Texts that match:\n" + json.dumps(matches, ensure_ascii=False)[:TEXT_MAX // 2]
    conversation = matches[0].get("conversation")
    if conversation:
        latest = await imessage.read(bot_id, {"action": "read", "conversation": str(conversation), "limit": 12})
        if (latest or {}).get("messages"):
            out = "The conversation's latest messages:\n" + json.dumps(latest["messages"], ensure_ascii=False)[
                :TEXT_MAX // 2] + "\n\n" + out
    return out


async def _mail(bot_id: str, m: dict) -> str:
    from . import mail
    accounts = list(mail.ACCOUNTS)
    if not accounts:
        return ""
    since = time.time() - (m["days_back"] or 14) * 86400

    words = [w.lower() for w in m["words"]]

    def one(account: str) -> list[dict]:
        try:
            return mail.envelopes(account, "INBOX", MAIL_PAGE)
        except mail.MailError as e:
            log.info("call read: %s", e)
            return []

    def matches(e: dict) -> bool:


        hay = f"{e['from_name']} {e['from_addr']} {e['subject']}".lower()
        return not words or any(w in hay for w in words)
    lists = await asyncio.gather(*(asyncio.to_thread(one, a) for a in accounts[:4]))
    def score(e: dict) -> int:
        hay = f"{e['from_name']} {e['from_addr']} {e['subject']}".lower()
        return sum(w in hay for w in words)
    found = sorted((e for es in lists for e in es if e["date"] >= since and matches(e)),
                   key=lambda e: (-score(e), -e["date"]))[:6]
    if not found:
        return ""
    lines = [f"- {_when(e['date'])}, from {e['from_name'] or e['from_addr']}: {e['subject']}" for e in found]
    top = found[0]
    try:
        body = await asyncio.to_thread(mail.read, top["account"], top["folder"], top["id"])
    except mail.MailError:
        body = ""
    return "Newest matching mail first:\n" + "\n".join(lines) + (
        f"\n\nThe newest one says:\n{' '.join(body.split())[:1500]}" if body else "")


async def _calendar(bot_id: str, m: dict) -> str:
    from . import calcache, factkeeper
    now = datetime.now(home.TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - m["days_back"] * 86400
    end = start + (m["days_back"] + m["days_ahead"] + 1) * 86400
    events = sorted(calcache.events_between(start, end), key=lambda e: e["start"])
    words = [w.lower() for w in m["words"]]
    if words:
        events = [e for e in events if any(w in f"{e.get('title')} {e.get('location')}".lower() for w in words)]
    if not events:
        return ""
    def until(e: dict) -> str:
        end_at = e.get("end")
        return f" to {datetime.fromtimestamp(end_at, home.TZ).strftime('%-I:%M %p')}" if end_at else ""
    return "\n".join(f"- {_when(e['start'])}{until(e)}: {e.get('title')}"
                     + (f" ({e['location']})" if e.get("location") else "") + factkeeper.calendar_note(e, bot_id)
                     for e in events[:20])


async def _todo(bot_id: str, m: dict) -> str:
    from . import todo
    return await asyncio.to_thread(todo.describe, bot_id)


async def _memory(bot_id: str, m: dict) -> str:
    from . import facts
    if not m["words"]:
        return ""
    found = await asyncio.to_thread(facts.search, " ".join(m["words"]), bot_id, 8)
    return "\n".join(f"- {f['text']}" for f in found)


READERS = {"texts": _texts, "mail": _mail, "calendar": _calendar, "todo": _todo, "memory": _memory}
NAMES = {"texts": "text messages", "mail": "mail", "calendar": "calendar", "todo": "to-do list",
         "memory": "saved notes"}


async def read(bot_id: str, m: dict, probe: bool = False) -> str:
    ""
    m = shape(m)
    if m is None:
        return ""
    if probe:
        from . import voice
        return {"texts": voice.PROBE_TEXT, "mail": voice.PROBE_MAIL}.get(m["place"], "")
    if not may_read(bot_id):
        return ""
    try:
        text = await asyncio.wait_for(READERS[m["place"]](bot_id, m), READ_TIMEOUT_SEC)
    except Exception as e:
        log.info("call read of %s gave nothing: %s: %s", m["place"], type(e).__name__, str(e)[:160])
        return ""
    return (text or "").strip()[:TEXT_MAX]
