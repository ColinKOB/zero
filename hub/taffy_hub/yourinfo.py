""



















from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import date

from . import config, store, who

log = logging.getLogger("taffy.yourinfo")

FIELDS = ("legal_name", "birthday", "birthplace", "sex", "height", "eyes", "hair", "phone", "email", "address")
LABELS = {"legal_name": "Legal name", "birthday": "Date of birth", "birthplace": "Born in", "sex": "Sex",
          "height": "Height", "eyes": "Eye color", "hair": "Hair color", "phone": "Phone", "email": "Email",
          "address": "Home address"}


PREFILLED_FIELDS = ("legal_name", "birthday", "birthplace", "phone", "email", "address")
ADDRESS_KEYS = ("line1", "line2", "city", "region", "postal_code", "country")
ID_TYPES = ("drivers_license", "passport", "ssn")
ID_NAMES = {"drivers_license": "Driver's license", "passport": "Passport", "ssn": "Social Security number"}
READY = "your_info_ready"
PREFILLED = "your_info_prefilled"
PREFILL_TIMEOUT_SEC = 60
_ready_lock = asyncio.Lock()


class InfoError(ValueError):
    ""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS your_info (
        field TEXT PRIMARY KEY,       -- one of FIELDS
        value TEXT NOT NULL,          -- text, or JSON for the address
        source TEXT NOT NULL,         -- you | scan | zip:<id> | facts | wallet
        changed_at REAL NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS your_info_shown (
        session_id TEXT PRIMARY KEY, version TEXT NOT NULL, at INTEGER NOT NULL
    )""")




def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean(field: str, value) -> str | dict | None:
    ""
    if field not in FIELDS:
        raise InfoError("That isn't one of the fields on Your Info.")
    if value in (None, "") or (isinstance(value, dict) and not any(str(v or "").strip() for v in value.values())):
        return None
    if field == "address":
        if isinstance(value, str):
            raise InfoError("Give the address as its parts: street, city, state, ZIP code and country.")
        a = {k: _text(value.get(k), 200) for k in ADDRESS_KEYS}
        if not (a["line1"] and a["city"]):
            raise InfoError("Add the street and the city.")
        return a
    if field == "birthday":
        try:
            d = date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise InfoError("Give the date of birth as YYYY-MM-DD.") from None
        if d > date.today() or d.year < 1900:
            raise InfoError("That date of birth can't be right.")
        return d.isoformat()
    text = _text(value, 200)
    if field == "phone" and sum(ch.isdigit() for ch in text) < 7:
        raise InfoError("That phone number is too short.")
    if field == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        raise InfoError("That email address doesn't look right.")
    return text or None


def values() -> dict:
    ""
    out = {}
    for r in store.db().execute("SELECT field, value FROM your_info"):
        out[r["field"]] = json.loads(r["value"]) if r["field"] == "address" else r["value"]
    return out


def sources() -> dict:
    return {r["field"]: r["source"] for r in store.db().execute("SELECT field, source FROM your_info")}





_generation = 0


def cancel_writes() -> None:
    ""
    global _generation
    with store._lock:
        _generation += 1


def generation() -> int:
    return _generation


def _set_if(generation_at: int | None, key: str) -> bool:
    ""
    with store._lock:
        if generation_at is not None and generation_at != _generation:
            return False
        store.set_setting(key, True)
        return True


def set_field(field: str, value, source: str = "you", generation_at: int | None = None, fill_only: bool = False) -> dict:
    ""



    kept = clean(field, value)
    with store._lock:
        if generation_at is not None and generation_at != _generation:
            return values()
        if fill_only and (kept is None or store.db().execute("SELECT 1 FROM your_info WHERE field=?", (field,)).fetchone()):
            return values()
        c = store.db()
        if kept is None:
            c.execute("DELETE FROM your_info WHERE field=?", (field,))
        else:
            c.execute("INSERT INTO your_info (field, value, source, changed_at) VALUES (?,?,?,?) ON CONFLICT(field) "
                      "DO UPDATE SET value=excluded.value, source=excluded.source, changed_at=excluded.changed_at",
                      (field, json.dumps(kept) if field == "address" else kept, source, time.time()))
        c.commit()
    return values()


def address_text(a: dict | None) -> str:
    if not a:
        return ""
    street = ", ".join(x for x in (a.get("line1"), a.get("line2")) if x)
    place = " ".join(x for x in (", ".join(x for x in (a.get("city"), a.get("region")) if x), a.get("postal_code")) if x)
    return ", ".join(x for x in (street, place, a.get("country")) if x)


def ids() -> list[dict]:
    ""
    from . import wallet
    out = []
    for item in wallet._load()["items"].values():
        meta = item.get("meta") or {}
        if item.get("kind") == "identity" and meta.get("type") in ID_TYPES:
            out.append({"id": item["id"], "type": meta["type"], "label": item.get("label") or ID_NAMES[meta["type"]],
                        "issuer": meta.get("issuer") or "", "last4": meta.get("last4") or "",
                        "expires": meta.get("expires") or "",
                        "zips": item.get("zips") or []})
    return out


def page() -> dict:
    ""
    v = values()
    name = v.get("legal_name") or who.first()
    parts = [p for p in re.split(r"\s+", name) if p[:1].isalpha()]
    initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else ""
    return {"fields": v, "sources": sources(), "ids": ids(), "name": name, "initials": initials}




def block() -> str:
    ""
    v = values()
    lines = [f"- {LABELS[f]}: {address_text(v[f]) if f == 'address' else v[f]}" for f in FIELDS if v.get(f)]
    saved = [f"{i['label']}{' (' + i['issuer'] + ')' if i['issuer'] else ''}" for i in ids()]
    if not lines and not saved:
        return ""
    first = who.first()
    out = [f"{first}'s info (Settings, Your Info). Type these into forms yourself, without asking {first}, and never "
           f"ask {first} for one that is here:", *lines]
    if saved:
        out.append(f"Saved ID numbers ({first} approves each use; call use_id): " + ", ".join(saved) + ".")
    out.append(f"When {first} tells you one of these that is missing or has changed, save it with save_your_info and say "
               f"so in one line.")
    return "\n".join(out)


def version() -> str:
    return hashlib.sha256(block().encode()).hexdigest()[:16]


def for_turn(bot_id: str, session_id: str, first: bool, kept: bool = True, sent_session: str = "") -> str:
    ""

    if config.is_sandbox(bot_id):
        return ""
    text = block()
    if not text:
        return ""
    if not session_id:
        return text if first else ""
    from . import lineage
    now_version = hashlib.sha256(text.encode()).hexdigest()[:16]
    c = store.db()
    row = c.execute("SELECT version, at FROM your_info_shown WHERE session_id=?", (session_id,)).fetchone()
    compacted = row is not None and lineage.compacted_at(bot_id, {session_id, sent_session}) > row["at"]
    if not (first or row is None or compacted or row["version"] != now_version):
        return ""
    if kept:
        with store._lock:
            c.execute("INSERT OR REPLACE INTO your_info_shown (session_id, version, at) VALUES (?,?,?)",
                      (session_id, now_version, int(time.time())))
            c.commit()
    return text


def save_from_zip(bot_id: str, field: str, value) -> str:
    ""
    if config.is_sandbox(bot_id):
        raise InfoError("A test Zip can't change Your Info.")
    def text(v):
        return address_text(v) if field == "address" else (v or "")
    before = values().get(field)
    v = set_field(field, value, f"zip:{bot_id}")
    shown = text(v.get(field))
    if not shown:
        return (f"Cleared {LABELS[field]} from Your Info" + (f"; it was {text(before)}" if before else "")
                + f". Tell {who.first()} in one line.")
    if before and before != v.get(field):


        return (f"Changed {LABELS[field]} on Your Info from {text(before)} to {shown}. Every Zip has it now. Tell "
                f"{who.first()} in one line, with the old value, so a mistake can be put back.")
    return f"Saved to Your Info: {LABELS[field]}, {shown}. Every Zip has it now. Tell {who.first()} in one line."




def migrate(generation_at: int | None = None) -> list[str]:
    ""


    from . import wallet
    filled = []
    have = values()
    for item in list(wallet._load()["items"].values()):
        kind, meta = item.get("kind"), item.get("meta") or {}
        moving = kind == "address" or (kind == "identity" and meta.get("type") == "dob")
        needs_last4 = kind == "identity" and meta.get("type") in ID_TYPES and not meta.get("last4")
        if not (moving or needs_last4):
            continue
        if generation_at is not None and generation_at != _generation:
            break
        secret = _secret_of(item)
        if secret is None:
            continue
        try:
            if needs_last4:
                number = re.sub(r"[\s-]", "", str(secret.get("value") or ""))
                if len(number) >= 4:
                    wallet.set_meta(item["id"], last4=number[-4:])
                continue
            field = "address" if kind == "address" else "birthday"
            value = {"line1": secret.get("address_line1"), "line2": secret.get("address_line2"),
                     "city": secret.get("city"), "region": secret.get("state"),
                     "postal_code": secret.get("postal_code"), "country": secret.get("country")} \
                if kind == "address" else secret.get("value")
            moved = clean(field, value)
            if field not in have:
                have = set_field(field, moved, "wallet", generation_at, fill_only=True)
                if sources().get(field) == "wallet":
                    filled.append(field)



            if kind == "address":
                for extra, key in (("phone", "phone"), ("email", "email")):
                    if secret.get(key) and extra not in have:
                        try:
                            have = set_field(extra, secret.get(key), "wallet", generation_at, fill_only=True)
                        except InfoError:
                            pass
            if have.get(field) == moved and _carried(kind, secret, have):
                wallet.delete(item["id"])
        except (InfoError, wallet.WalletError):
            log.info("a wallet %s stayed in the wallet: its value didn't check out", kind)
        finally:
            del secret
    return filled


def _carried(kind: str, secret: dict, have: dict) -> bool:
    ""

    if kind != "address":
        return True
    def same(a, b) -> bool:
        return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()
    name = secret.get("name")
    if name and not same(name, have.get("legal_name")):
        return False
    for key in ("phone", "email"):
        if secret.get(key):
            try:
                if clean(key, secret[key]) != have.get(key):
                    return False
            except InfoError:
                return False
    return True


def _secret_of(item: dict) -> dict | None:
    from . import wallet
    for bot in item.get("zips") or []:
        handle = (item.get("handles") or {}).get(bot)
        if not handle:
            continue
        try:
            return dict(wallet._vault(bot).resolve_secret(handle))
        except Exception:
            continue
    return None


PREFILL_SYSTEM = "You fill in a person's own record from what they told their helpers. Answer with JSON only."
PREFILL_PROMPT = """These are notes {first}'s helpers saved from what {first} told them, and {first}'s own settings, numbered:

{notes}

Fill in {first}'s record from them: legal_name (the full legal name, with a middle name when one was given), birthday (YYYY-MM-DD), birthplace (city and state or country), phone, email, address (street, city, state, ZIP code and country, as parts).

Take a value only when a note states it as {first}'s own. Leave a field out when no note does, when notes disagree, or when you would be guessing: an empty field is fine. Never take another person's details.{rules}

Answer with this JSON and nothing else: {{"legal_name": {{"value": "...", "from": 3}}, "birthday": {{"value": "...", "from": 1}}, "address": {{"value": {{"line1": "...", "line2": "", "city": "...", "region": "...", "postal_code": "...", "country": "..."}}, "from": 2}}}}, with only the fields you can fill."""
QUERIES = {"legal_name": "legal name full name middle name", "birthday": "date of birth birthday born",
           "birthplace": "birthplace born in hometown place of birth", "phone": "phone number cell mobile",
           "email": "email address", "address": "home address street lives apartment ZIP"}


def _notes() -> list[str]:
    ""
    from . import facts, factkeeper, home, privatewords
    seen, out = set(), []
    for query in QUERIES.values():
        for f in facts.search(query, None, limit=8):
            if f["id"] in seen or f.get("bot_id") in factkeeper.SKIP_ZIPS or privatewords.holds(f["text"]):
                continue
            seen.add(f["id"])
            subject = f"{f['subject']}: " if f.get("subject") else ""
            out.append(f"{subject}{f['text']}")

    own = sorted((a for a in home.mail_accounts() if not a.get("zips_own")), key=lambda a: not a.get("default"))
    if own:
        out.append(f"{who.first()}'s own email address: {own[0].get('address')}")
    return out


async def prefill(ask=None) -> list[str]:
    ""


    started = _generation
    have = values()
    empty = [f for f in PREFILLED_FIELDS if f not in have]
    if not empty:
        return []
    try:
        notes = await asyncio.to_thread(_notes)
    except Exception:
        log.warning("could not read the notes for Your Info", exc_info=True)
        return []
    if not notes:
        _set_if(started, PREFILLED)
        return []
    from . import ownerrules

    text = PREFILL_PROMPT.format(first=who.first(), notes="\n".join(f"{n}. {x[:300]}" for n, x in enumerate(notes, 1)),
                                 rules=ownerrules.block(ownerrules.QUESTION, "your info", "default", answered=False))
    from . import config, shortreply


    zips = tuple(z for z in config.BOTS_BY_ID if not config.is_private(z)) or ("default",)
    try:
        with shortreply.reading(zips):
            if ask is None:
                answer = await shortreply.ask_helper(text, system=PREFILL_SYSTEM, timeout=PREFILL_TIMEOUT_SEC)
            else:
                answer = await ask(text)
        got = json.loads(re.search(r"\{.*\}", answer or "", re.S).group(0))
    except Exception:
        log.warning("could not fill Your Info from the notes", exc_info=True)
        return []
    if not _set_if(started, PREFILLED):
        return []
    filled = []
    for field in empty:
        pick = got.get(field) if isinstance(got, dict) else None
        if not isinstance(pick, dict) or not isinstance(pick.get("from"), int) or not 1 <= pick["from"] <= len(notes):
            continue
        try:
            set_field(field, pick.get("value"), "facts", started, fill_only=True)
        except InfoError:
            continue
        if sources().get(field) == "facts":
            filled.append(field)
    return filled


async def ready(ask=None) -> None:
    ""


    async with _ready_lock:
        if not store.get_setting(READY, False):
            started = _generation
            try:
                await asyncio.to_thread(migrate, started)
            except Exception:
                log.exception("could not move the wallet's address and date of birth to Your Info")
            _set_if(started, READY)
        if not store.get_setting(PREFILLED, False):
            await prefill(ask)
