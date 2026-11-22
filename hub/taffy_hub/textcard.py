""
























import asyncio
import hashlib
import json
import logging
import re
import threading
import time

from . import choices, config, consent, imessage, mac, store, who

KIND = "text"
TEXT_MAX = 2000
SEND_WAIT_SEC = 90
UNSURE = "I couldn't confirm that this text went out. Check Messages before you send it again."
FAILED = "Messages couldn't deliver this text. Check Messages before you send it again."
_HANDLE = re.compile(r"^\+?[0-9]{5,15}$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PRIVATE = ("session_id", "req", "sending_at", "guid")
MEMBERS_WAIT_SEC = 10
FILL_AFTER_SEC = (15, 45, 120, 300, 900, 1800, 3600)
SLOW_MAC = ("no_answer", "offline")

log = logging.getLogger("taffy.textcard")


_lock = threading.RLock()


class TextError(Exception):
    pass


def _handle(raw) -> str:
    ""
    s = str(raw or "").strip()
    if "@" not in s:
        digits = re.sub(r"[^\d]", "", s)
        s = ("+" if s.startswith("+") or len(digits) == 11 and digits.startswith("1") else "+1" if len(digits) == 10 else "") + digits
    if not _HANDLE.match(s):
        raise TextError(f"{raw or 'That'} isn't a phone number or email address.")
    return s.lower() if "@" in s else s


def _words(text) -> str:
    out = str(text or "").replace("\r\n", "\n").strip()
    if not out:
        raise TextError("A text needs some words.")
    if len(out) > TEXT_MAX:
        raise TextError("That text is too long to send from here.")
    return out


async def offer(bot_id: str, session_id: str | None, args: dict, during_turn: bool, practice: bool = False) -> dict:
    ""


    text = _words(args.get("text"))
    conversation = str(args.get("conversation") or "").strip()[:200]
    if bool(conversation) == bool(str(args.get("to") or "").strip()):
        raise TextError("Give either the conversation from a messages result or one phone number or email in to.")
    waiting = ""
    if practice:
        people = [{"handle": str(p.get("handle") or "")[:80], "name": str(p.get("name") or "")[:80]}
                  for p in args.get("practice_people") or [] if isinstance(p, dict)][:10]
        card_to = {"conversation": conversation, "name": "", "people": people, "service": "practice"}
        if not conversation:
            card_to["handle"] = people[0]["handle"] if people else ""
    else:
        try:
            card_to = await _members(bot_id, conversation, args.get("to"), MEMBERS_WAIT_SEC)
        except mac.MacError as e:
            waiting = getattr(e, "code", "")
            if waiting not in SLOW_MAC:
                raise
            card_to = _waiting_to(conversation, args.get("to"))
    if not card_to["people"] and not waiting:
        raise TextError("That conversation has nobody in it I can text.")
    card = {"state": "open", "version": 1, **card_to, "text": text, "error": None, "session_id": session_id or None}
    if waiting:
        card["waiting"] = True

    def publish() -> int:
        with _lock:
            revised = _replace_open(bot_id, card)
            return revised if revised else store.add_feed(KIND, bot_id, None, title_for(card), json.dumps(card),
                                                          written_by="zip", written_for="person")
    late = {"waiting": waiting} if waiting else {}
    if late:
        _fill_soon(bot_id, _target(card))
    with _lock:
        revised = _replace_open(bot_id, card)
        if revised:
            return {"revised": True, "id": revised, **late}
    if during_turn:
        choices.defer(bot_id, publish, ask=title_for(card))
        return {"revised": False, **late}
    return {"revised": False, "id": publish(), **late}


async def _members(bot_id: str, conversation: str, to, timeout: float | None = None) -> dict:
    ""

    if conversation:
        members, _ = imessage.known(conversation)
        if members and members.get("people"):
            return dict(members)
        found = await imessage.read(bot_id, {"action": "read", "conversation": conversation, "limit": 1}, timeout)
        return imessage.card_to(conversation, found.get("conversation") or {})
    handle = _handle(to)
    found = await imessage.read(bot_id, {"action": "lookup", "handle": handle}, timeout)
    return {"conversation": str(found.get("conversation") or ""), "name": "", "handle": handle,
            "people": [{"handle": handle, "name": str(found.get("name") or "")}],
            "service": str(found.get("service") or "iMessage")}


def _waiting_to(conversation: str, to) -> dict:
    ""

    if conversation:
        _, label = imessage.known(conversation)
        return {"conversation": conversation, "name": label, "people": [], "service": ""}
    handle = _handle(to)
    return {"conversation": "", "name": "", "handle": handle, "people": [{"handle": handle, "name": ""}],
            "service": "iMessage"}


def _open_waiting(bot_id: str, target: tuple[str, str]):
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)).fetchall():
        card = _load(row)
        if card.get("state") == "open" and card.get("waiting") and _target(card) == target:
            return row, card
    return None, None


def _fill(row, card: dict, found: dict) -> dict:
    ""
    keep_handle = card.get("handle")
    if found.get("people") != card.get("people"):
        _new_people(card)
    card.update({k: v for k, v in found.items() if k in ("name", "people", "service")})
    if keep_handle:
        card["handle"] = keep_handle
        card["conversation"] = found.get("conversation") or card.get("conversation") or ""
    card.pop("waiting", None)
    _save(row, card)
    return card


def _fill_soon(bot_id: str, target: tuple[str, str]) -> None:
    try:
        asyncio.get_running_loop().create_task(_fill_later(bot_id, target))
    except RuntimeError:
        pass


async def _fill_later(bot_id: str, target: tuple[str, str]) -> None:
    ""


    conversation, handle = target
    for wait in FILL_AFTER_SEC:
        await asyncio.sleep(wait)
        with _lock:
            row, card = _open_waiting(bot_id, target)
        if row is None and wait > FILL_AFTER_SEC[0]:
            return
        try:
            found = await _members(bot_id, conversation, handle)
        except (mac.MacError, TextError) as e:
            log.info("Text card for %s still waiting for the Mac: %s", bot_id, getattr(e, "code", "") or "refused")
            continue
        with _lock:
            row, card = _open_waiting(bot_id, target)
            if row is not None and found.get("people"):
                _fill(row, card, found)
                return


def _target(card: dict) -> tuple[str, str]:
    return (card.get("conversation") or "", "" if card.get("conversation") else card.get("handle") or "")


def _new_people(card: dict) -> None:
    ""
    card["version"] = int(card.get("version") or 1) + 1
    card["people_version"] = card["version"]


def _replace_open(bot_id: str, new: dict) -> int | None:
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)).fetchall():
        old = _load(row)
        if old.get("state") != "open" or _target(old) != _target(new):
            continue
        new["version"] = int(old.get("version") or 1)
        new["people_version"] = int(old.get("people_version") or 0)
        if old.get("people") != new.get("people"):
            _new_people(new)
        elif old.get("text") != new["text"]:
            new["version"] += 1
        if not new["people_version"]:
            new.pop("people_version")
        store.update_feed(row["id"], title_for(new), json.dumps(new))
        return row["id"]
    return None


def _load(row) -> dict:
    try:
        return json.loads(row["body"] or "{}")
    except ValueError:
        return {}


def _row(feed_id: int, bot_id: str):
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?", (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise TextError("That text is gone.")
    return row, _load(row)


def _save(row, card: dict) -> None:
    store.update_feed(row["id"], title_for(card), json.dumps(card))


def _check_version(card: dict, version) -> None:
    if version is not None and int(version) != int(card.get("version") or 1):
        raise TextError("This text changed. Review the current words and try again.")


def public(card: dict) -> dict:
    ""
    out = {k: v for k, v in card.items() if k not in _PRIVATE}
    out["can_edit"] = card.get("state") == "open"
    out["can_reopen"] = can_reopen(card)
    return out






REOPEN_SEC = 24 * 3600


def can_reopen(card: dict, now: float | None = None) -> bool:
    ""
    now = time.time() if now is None else now
    return card.get("state") == "dismissed" and bool(card.get("declined")) and \
        now - float(card.get("at") or 0) <= REOPEN_SEC


def title_for(card: dict) -> str:
    return f"Text to {to_line(card)}"


def to_line(card: dict) -> str:
    ""
    if card.get("name"):
        return card["name"]
    people = card.get("people") or []
    first = people[0] if people else {}
    label = first.get("name") or first.get("handle") or "someone"
    return f"{label} and {len(people) - 1} more" if len(people) > 1 else label


def settle(row: dict) -> dict:
    ""
    with _lock:
        fresh = store.db().execute("SELECT * FROM feed WHERE id=?", (row["id"],)).fetchone()
        row = dict(fresh) if fresh else row
        card = _load(row)
        if card.get("state") == "sending" and time.time() - float(card.get("sending_at") or 0) > SEND_WAIT_SEC:
            card.update(state="open", error=UNSURE)
            store.update_feed(row["id"], title_for(card), json.dumps(card))
        return card


def edit(bot_id: str, feed_id: int, edits: dict) -> dict:
    ""
    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") != "open":
            raise TextError("That text is no longer open.")
        _check_version(card, edits.get("version"))
        text = _words(edits.get("text") if edits.get("text") is not None else card.get("text"))
        if text != card.get("text"):
            card.update(text=text, version=int(card.get("version") or 1) + 1, error=None)
            _save(row, card)
        return card


def dismiss(bot_id: str, feed_id: int) -> dict:
    ""
    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") == "open":
            card.update(state="dismissed", at=time.time(), declined=True)
            _save(row, card)
        return card


def reopen(bot_id: str, feed_id: int) -> dict:
    ""
    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") == "open":
            return card
        if not can_reopen(card):
            raise TextError("This text can't be brought back now.")
        card.update(state="open", declined=False, at=time.time())
        _save(row, card)
        return card


def declined_lately(bot_id: str, now: float | None = None) -> list[dict]:
    ""
    now = time.time() if now is None else now
    out = []
    with _lock:
        for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                      (KIND, bot_id)).fetchall():
            card = _load(row)
            if can_reopen(card, now):
                out.append({"card": row["id"], "to": to_line(card), "starts": str(card.get("text") or "")[:120]})
    return out[:5]


def fingerprint(bot_id: str, card: dict) -> str:
    ""
    blob = json.dumps({"tool": "messages", "bot": bot_id, "target": list(_target(card)), "text": card["text"]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _approve(bot_id: str, card: dict) -> int:
    ""

    fp = fingerprint(bot_id, card)
    rid = consent.action_request(bot_id, "send", "", f"Text {to_line(card)}", ", ".join(
        p["handle"] for p in card.get("people") or []), card["text"], fp, card.get("session_id"), None, why="drafted")
    with consent.db() as c:
        c.execute("UPDATE requests SET status='allowed', decided=? WHERE id=? AND status='pending'", (time.time(), rid))
    if not consent.use_approval(bot_id, fp):
        raise TextError("That okay could not be recorded. Nothing was sent.")
    return rid


async def send(bot_id: str, feed_id: int, edits: dict) -> dict:
    ""

    edits = edits or {}
    if config.is_sandbox(bot_id):
        raise TextError("This is a practice text, so it is not sent.")
    with _lock:
        row, card = _row(feed_id, bot_id)
    if card.get("waiting") and card.get("state") == "open":


        try:
            found = await _members(bot_id, *_target(card))
        except mac.MacError as e:
            code = getattr(e, "code", "")
            raise TextError({"offline": f"{who.mac()} isn't connected, so nothing was sent. Open Zero on it, then tap "
                                        "Send again.",
                             "no_answer": f"{who.mac()} didn't answer, so nothing was sent. Tap Send again in a "
                                          "minute."}.get(code, str(e))) from None
        with _lock:
            row, card = _row(feed_id, bot_id)
            if card.get("waiting") and found.get("people"):
                card = _fill(row, card, found)
        if not card.get("people"):
            raise TextError("That conversation has nobody in it I can text.")
    with _lock:
        row, card = _row(feed_id, bot_id)
        card = settle(dict(row)) if card.get("state") == "sending" else card
        if card.get("state") != "open":
            raise TextError({"sent": "That text was already sent.", "sending": "That text is already on its way."}.get(
                card.get("state"), "That text is closed."))
        if card.get("people_version") and (edits.get("version") is None
                                           or int(edits["version"]) < int(card["people_version"])):
            raise TextError("Check who this goes to, then tap Send again.")
        if card.get("waiting"):
            raise TextError("Check who this goes to, then tap Send again.")
        _check_version(card, edits.get("version"))
        if edits.get("text") is not None and _words(edits["text"]) != card["text"]:
            card.update(text=_words(edits["text"]), version=int(card.get("version") or 1) + 1)
        req = _approve(bot_id, card)
        card.update(state="sending", sending_at=time.time(), error=None, req=req)
        _save(row, card)
    conversation, handle = _target(card)
    try:
        reply = await mac._call("messages_send", {"conversation": conversation, "handle": handle, "text": card["text"]})
        result = reply.get("result") or {}
        state = str(result.get("state") or "unconfirmed")
        error = None
    except mac.MacError as e:

        state, error = ("unconfirmed", None) if str(e) == mac.NO_ANSWER else ("refused", str(e))
    with _lock:
        row, now = _row(feed_id, bot_id)
        if now.get("req") != req:
            return now
        if state == "sent":
            now.update(state="sent", at=time.time(), error=None, guid=str(result.get("guid") or ""))
        else:
            now.update(state="open", error=error or (FAILED if state == "failed" else UNSURE))
        _save(row, now)
        return now


def drafts(bot_id: str) -> list[dict]:
    ""
    out = []
    with _lock:
        for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                      (KIND, bot_id)).fetchall():
            card = _load(row)
            if card.get("state") == "open":
                out.append({"card": row["id"], "to": to_line(card), "text": card.get("text")})
    return out[:3]
