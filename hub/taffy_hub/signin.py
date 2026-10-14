"""A bot in a chat asks Colin to sign in to a site, while he is still there.

The bot calls ask_to_sign_in (taffy-tools) and stops. Colin gets the same card a board task's sign-in gets
("Save Login" or "Sign In Myself") in that bot's chat, plus a notification. When he finishes, the bot is
allowed on that site for this conversation and picks up where it left off; a closed card just goes away.
"""

import asyncio
import json
import logging
import re
import time

from . import config, consent, store, turns, who

log = logging.getLogger("taffy.signin")

KIND = "login"
loop: asyncio.AbstractEventLoop | None = None


class AskError(Exception):
    pass


def _open_card(bot_id: str, site: str):
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)):
        card = json.loads(row["body"] or "{}")
        if card.get("state") == "open" and card.get("site") == site:
            return row
    return None


def card_owner(feed_id: int) -> str | None:
    """The Zip whose chat holds this sign-in card."""
    row = store.db().execute("SELECT bot_id FROM feed WHERE id=? AND kind=?", (feed_id, KIND)).fetchone()
    return row["bot_id"] if row else None


REPUSH_SEC = 120


def ask(bot_id: str, url: str, why: str, session_id: str | None) -> tuple[int | None, str]:
    """(feed id of the card to notify Colin about, or None when no notification is due; what to tell the bot).

    Asked again for a site that already has an open card, the card moves to now, which is the bottom of the
    chat, with the address and reason the Zip just gave (Oct 9: Zero's 11:54 card sat far up the chat, and at
    12:59 Zero told Colin "your app still has the sign-in card from before", which he could not find)."""
    site = consent.site_of(url)
    if not site:
        raise AskError("Give the sign-in page's full https address.")
    why = who.to_you(" ".join(str(why or "").split())[:200])
    row = _open_card(bot_id, site)
    if row:
        card = json.loads(row["body"] or "{}")
        now = time.time()
        pushed = card.get("pushed_at")
        due = now - float(pushed if pushed is not None else row["created_at"] or 0) >= REPUSH_SEC
        card.update(url=url, why=why or card.get("why") or "", session_id=session_id or card.get("session_id"),
                    asked_again=now)
        if due:
            card["pushed_at"] = now
        store.update_feed(row["id"], row["title"], json.dumps(card))
        store.touch_feed(row["id"], now)
        return (row["id"] if due else None), (
            f"{who.possessive()} card to sign in to {site} is now at the bottom of your chat"
            + (", with a new notification" if due else "") + f". Stop here and tell {who.first()} in one short line "
            f"that the card is there. A note will arrive in this chat when {who.first()} is done, and you'll carry on "
            "then. If you no longer need it, close it with close_sign_in_card.")
    store.add_feed(KIND, bot_id, None, f"Sign in to {site}",
                   json.dumps({"url": url, "site": site, "why": why, "state": "open", "session_id": session_id,
                               "pushed_at": time.time()}))
    row = _open_card(bot_id, site)
    return (row["id"] if row else None), (
        f"{who.first()} now has a card at the bottom of your chat to sign in to {site}, and a notification. Leave "
        f"{site} alone until then, but finish any part of the request that does not need it. Then tell "
        f"{who.first()} in one short line what you'll do once the sign-in is done. A note will arrive in this chat "
        f"when {who.first()} is done, and you'll carry on then. If {who.first()} gets it done another way or you no "
        "longer need it, close the card with close_sign_in_card.")


# MARK: Which cards the Zip is waiting on (Oct 9, 11:18 p.m.)

ASK_KINDS = ("login", "account")


def _open_asks(bot_id: str, limit: int = 40) -> list[tuple[dict, dict]]:
    q = (f"SELECT * FROM feed WHERE bot_id=? AND kind IN ({','.join('?' * len(ASK_KINDS))}) "
         "ORDER BY id DESC LIMIT ?")
    out = []
    for row in store.db().execute(q, (bot_id, *ASK_KINDS, limit)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") == "open":
            out.append((dict(row), card))
    return out


def waiting(row: dict, said: float | None = None) -> bool:
    """Whether the Zip is waiting on this open card now: raised (or moved down by a new ask) no earlier than the
    start of the person's latest turn."""
    said = store.said_at(row["bot_id"]) if said is None else said
    return float(row["created_at"] or 0) >= int(said)


def live_asks(bot_id: str) -> list[tuple[dict, dict]]:
    """Open sign-in and account cards the Zip is waiting on now, newest first."""
    said = store.said_at(bot_id)
    return [(row, card) for row, card in _open_asks(bot_id) if waiting(row, said)]


def old_cards(bot_id: str) -> list[tuple[dict, dict]]:
    """Open sign-in cards the conversation has moved past, newest first."""
    said = store.said_at(bot_id)
    return [(row, card) for row, card in _open_asks(bot_id) if row["kind"] == KIND and not waiting(row, said)]


def retire(bot_id: str, site_or_url: str = "") -> list[str]:
    """The Zip closes its own sign-in card because it no longer needs it (the person bought it another way, changed
    their mind, or the Zip found what it needed). No turn follows: the Zip is the one closing it. With no site, the
    newest open card. Returns the sites closed; raises AskError when there is nothing to close."""
    want = consent.site_of(site_or_url) if "://" in (site_or_url or "") else (site_or_url or "").strip().lower()
    want = want.removeprefix("www.")
    closed = []
    for row, card in _open_asks(bot_id):
        if row["kind"] != KIND:
            continue
        site = (card.get("site") or "").lower().removeprefix("www.")
        if want and not (site == want or consent.base(site) == consent.base(want)):
            continue
        card.update(state="retired", at=time.time())
        store.update_feed(row["id"], row["title"], json.dumps(card))
        closed.append(card.get("site") or site)
        if not want:
            break
    if not closed:
        raise AskError(f"There is no open sign-in card for {want} in {who.possessive()} chat." if want else
                       f"There is no open sign-in card in {who.possessive()} chat.")
    log.info("%s closed its own sign-in card for %s", bot_id, ", ".join(closed))
    return closed


def old_cards_note(bot_id: str) -> str | None:
    """For the start of the Zip's chat turn: sign-in cards it raised earlier that are still open, so it can close one
    the person's new message makes unnecessary."""
    cards = old_cards(bot_id)[:3]
    if not cards:
        return None
    sites = [c.get("site") or "a site" for _, c in cards]
    listed = sites[0] if len(sites) == 1 else ", ".join(sites[:-1]) + " and " + sites[-1]
    return (f"{who.hub_note()} {who.possessive()} chat still has your older card"
            f"{'s' if len(sites) > 1 else ''} to sign in to {listed}. If what {who.first()} says now means you no "
            "longer need it (done another way, bought elsewhere, not wanted any more), close it with "
            f"close_sign_in_card and don't mention it again. If you need it for what {who.first()} asks now, call "
            f"ask_to_sign_in again, which brings that same card back to the bottom of the chat as current; don't "
            f"send {who.first()} to the old one.")


CHECK_AGAIN = ("Open the page again now and check before you answer. If you still cannot read it, tell {first} "
               "exactly what the page shows (its title and the message on it, such as a sign-in form or \"Access "
               "denied\") and what would get past it.")


def resolve(bot_id: str, feed_id: int, outcome: str, follow: bool = True) -> dict:
    """outcome: "saved" (a login went into the bot's vault), "signed_in" (he signed in on its screen) or
    "declined" (he closed the card). `follow` False: the person said it in the chat, so their own message carries
    the conversation on and the note rides along with it (done_in_chat)."""
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?",
                             (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise AskError("That sign-in card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("state") != "open":
        return card
    card.update(state=outcome, at=time.time())
    store.update_feed(feed_id, row["title"], json.dumps(card))
    site, session_id = card.get("site") or "", card.get("session_id")
    if outcome == "declined":
        note = (f"{who.hub_note()} {who.first()} closed the card without signing in to {site}. Do not "
                f"sign in there. Finish what you can without it and tell {who.first()} in a sentence what you could "
                "not check and why.")
        if loop:
            asyncio.run_coroutine_threadsafe(_continue(bot_id, note, session_id), loop)
        return card
    if outcome == "signed_in":
        consent.mark_signed_in(bot_id, site)
    with consent.db() as c:
        c.execute("INSERT INTO requests (bot_id, site, reason, session_id, status, created, decided) "
                  "VALUES (?,?,?,?, 'allowed', ?, ?)",
                  (bot_id, site, f"{who.first()} signed in when you asked", session_id, time.time(), time.time()))
    how = ("saved a login for it in your vault; sign in with your browser vault tools (they fill the password "
           "without showing it)" if outcome == "saved" else
           "signed in on your screen; the session is in your browser")
    note = (f"{who.hub_note()} {who.first()} {how}. You may use {site} for what {who.first()} asked in this "
            "conversation. Carry on with it now. " + CHECK_AGAIN.format(first=who.first()))
    if not follow:
        store.add_card_note(bot_id, note.removeprefix(who.hub_note()).strip())
    elif loop:
        asyncio.run_coroutine_threadsafe(_continue(bot_id, note, session_id), loop)
    return card


_SIGNED = re.compile(r"\bi(?:'ve|'m| am| have| just|'ve just| have just)?\s+(?:signed|logged)\s+(?:back\s+)?(?:in|on)\b|"
                     r"\bi(?:'?m| am) (?:back )?in(?: now)?(?:[\s,.!]*$|[,.!])|"
                     r"^(?:ok(?:ay)?[\s,.!]*)?(?:all\s+)?(?:signed|logged)\s+in(?: now)?\b", re.I)
_JUST_DONE = re.compile(r"^(?:ok(?:ay)?|alright|there|k)?[\s,.!]*(?:i'?m |i am |all |it's |its )?"
                        r"(?:done|finished|set)(?: now)?[\s,.!]*(?:(?:you can |go ahead and |please )?"
                        r"(?:try(?: it)? again|go ahead|continue|carry on|keep going)(?: now)?)?[\s.!]*$|"
                        r"^(?:ok(?:ay)?[\s,.!]*)?(?:try(?: it)? again|go ahead|carry on)(?: now)?[\s.!]*$", re.I)


def done_in_chat(bot_id: str, text: str) -> str | None:
    """The person said in the chat that the sign-in is done, while one of this Zip's sign-in cards is open: the card
    is answered as I'm Done would answer it, and the Zip hears at the start of this turn to check the page again.
    Returns the site, or None."""
    t = " ".join(str(text or "").split())
    if not t or len(t) > 160 or "?" in t:
        return None
    rows = store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                              (KIND, bot_id)).fetchall()
    for row in rows:
        card = json.loads(row["body"] or "{}")
        if card.get("state") != "open":
            continue
        site = card.get("site") or ""
        named = bool(site) and consent.base(site).split(".")[0] in t.lower()
        if _SIGNED.search(t) or _JUST_DONE.match(t) or (named and re.search(r"\b(done|finished)\b", t, re.I)):
            resolve(bot_id, row["id"], "signed_in", follow=False)
            return site
        return None
    return None


async def _continue(bot_id: str, note: str, session_id: str | None = None) -> None:
    try:
        await turns.follow_up(bot_id, note, session_id, urgent=True)
    except Exception:
        log.exception("could not tell %s about the sign-in", bot_id)
