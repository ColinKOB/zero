""









import asyncio
import json
import logging
import re
import time

from . import consent, store, turns, who

log = logging.getLogger("taffy.twofactor")

KIND = "code"
CODE_TTL_SEC = 10 * 60
REPUSH_SEC = 120

_CODE = re.compile(r"^[A-Za-z0-9]{4,10}$")
loop: asyncio.AbstractEventLoop | None = None

_codes: dict[int, tuple[str, float]] = {}


class CodeError(Exception):
    pass


def clean_code(text: str) -> str:
    ""
    code = re.sub(r"[\s-]+", "", str(text or ""))
    if not _CODE.match(code):
        raise CodeError("That doesn't look like a code. Check the text message and try again.")
    return code


def _open_card(bot_id: str, host: str):
    ""

    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)):
        card = json.loads(row["body"] or "{}")
        if card.get("state") == "open" and card.get("host") == host:
            return row
    return None


def _site_name(site: str) -> str:
    ""
    site = " ".join(str(site or "").split())[:60]
    if "." in site and " " not in site:
        host = consent.site_of(site if "://" in site else "https://" + site) or site
        return consent.base(host).split(".")[0].capitalize()
    return site


def _host(url: str, site: str) -> str:
    ""

    for text in (url, site):
        text = " ".join(str(text or "").split())
        if "." in text and " " not in text:
            if "://" in text and not text.lower().startswith("https://"):
                raise CodeError("A code is only typed into a secure page. Give the address starting with https://.")
            host = consent.site_of(text if "://" in text else "https://" + text)
            if host:
                return consent.base(host)
    return ""


def ask(bot_id: str, site: str, why: str, session_id: str | None, digits: int | None = None,
        url: str = "") -> tuple[int | None, str]:
    ""
    name = _site_name(site)
    if not name:
        raise CodeError("Say which site is sending the code.")
    host = _host(url, site)
    if not host:
        raise CodeError("Give the address of the page that asks for the code, starting with https://.")
    why = who.to_you(" ".join(str(why or "").split())[:200])
    length = digits if isinstance(digits, int) and 4 <= digits <= 10 else None
    row = _open_card(bot_id, host)
    now = time.time()
    if row:
        card = json.loads(row["body"] or "{}")
        pushed = card.get("pushed_at")
        due = now - float(pushed if pushed is not None else row["created_at"] or 0) >= REPUSH_SEC
        card.update(site=name, why=why or card.get("why") or "", session_id=session_id or card.get("session_id"),
                    digits=length or card.get("digits"))
        if due:
            card["pushed_at"] = now
        store.update_feed(row["id"], row["title"], json.dumps(card))
        store.touch_feed(row["id"], now)
        fid = row["id"] if due else None
    else:
        fid = store.add_feed(KIND, bot_id, None, f"{name} sent you a code",
                             json.dumps({"site": name, "host": host, "why": why, "state": "open",
                                         "session_id": session_id, "digits": length, "pushed_at": now}),
                                             written_by="zip", written_for="person")
        row = {"id": fid}
    return fid, (
        f"{who.first()} now has a card at the bottom of your chat for the code {name} texted, and a notification. "
        f"Stop here. {who.quiet_card()} A note arrives in this chat when "
        f"{who.first()} has entered it. Then call enter_texted_code with the ref of the code field on the page, press "
        "the page's Verify or Continue button yourself, and carry on. You never see the code.")


def submit(bot_id: str, feed_id: int, text: str) -> dict:
    ""

    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?", (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise CodeError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("state") != "open":
        raise CodeError("That code was already sent.")
    code = clean_code(text)
    _forget_old()
    _codes[feed_id] = (code, time.time())
    card.update(state="sent", at=time.time())
    store.update_feed(feed_id, row["title"], json.dumps(card))
    site = card.get("site") or "the site"


    try:
        consent.allow_now(bot_id, str(card.get("host") or ""), f"You entered the code {site} sent you")
    except Exception:
        log.exception("could not record the go-ahead for %s", card.get("host"))
    note = (f"{who.hub_note()} {who.first()} entered the code {site} texted. It is held for you and you cannot read it. "
            f"Call enter_texted_code with the ref of the code field on the {site} page (look at the page first if you "
            "need to), then press the page's Verify or Continue button yourself and carry on with what "
            f"{who.first()} asked. If the page says the code is wrong or expired, call ask_for_code again.")
    if loop:
        asyncio.run_coroutine_threadsafe(_continue(bot_id, note, card.get("session_id")), loop)
    return public(card)


def decline(bot_id: str, feed_id: int) -> dict:
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?", (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise CodeError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("state") != "open":
        return public(card)
    card.update(state="declined", at=time.time())
    store.update_feed(feed_id, row["title"], json.dumps(card))
    site = card.get("site") or "the site"
    note = (f"{who.hub_note()} {who.first()} closed the card without entering the code {site} texted. Do not try that "
            f"sign-in again. Finish what you can without it and tell {who.first()} in a sentence what you could not do.")
    if loop:
        asyncio.run_coroutine_threadsafe(_continue(bot_id, note, card.get("session_id")), loop)
    return public(card)


def retire(bot_id: str, site: str = "") -> list[str]:
    ""
    want = _site_name(site).lower()
    closed = []
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)).fetchall():
        card = json.loads(row["body"] or "{}")
        if card.get("state") != "open" or (want and (card.get("site") or "").lower() != want):
            continue
        card.update(state="retired", at=time.time())
        store.update_feed(row["id"], row["title"], json.dumps(card))
        closed.append(card.get("site") or "")
        if not want:
            break
    if not closed:
        raise CodeError("There is no open code card to close.")
    return closed


def take(bot_id: str, page_site: str = "") -> str | None:
    ""



    _forget_old()
    here = consent.base(page_site) if page_site else ""
    if not here:
        return None
    for r in store.db().execute("SELECT id, body FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 10",
                                (KIND, bot_id)).fetchall():
        if r["id"] in _codes and json.loads(r["body"] or "{}").get("host") == here:
            return _codes.pop(r["id"])[0]
    return None


def _forget_old() -> None:
    cutoff = time.time() - CODE_TTL_SEC
    for k in [k for k, (_, t) in _codes.items() if t < cutoff]:
        _codes.pop(k, None)


def public(card: dict) -> dict:
    ""
    return {k: v for k, v in card.items() if k != "session_id"}


async def _continue(bot_id: str, note: str, session_id: str | None = None) -> None:
    try:
        await turns.follow_up(bot_id, note, session_id, urgent=True)
    except Exception:
        log.exception("could not tell %s about the code", bot_id)
