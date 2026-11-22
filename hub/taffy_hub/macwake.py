""




























from __future__ import annotations

import asyncio
import json
import logging
import time

from . import config, store, who

log = logging.getLogger("taffy.macwake")

POLL_SEC = 3
IDLE_SEC = 90
LOCKED_SEEN_SEC = 15 * 60
PAUSED_KEEP_SEC = 24 * 3600
YES, NOT_NOW = "Wake My Mac", "Not Now"
QUESTION_MAX = 240

_locked_seen: dict[tuple[str, str], float] = {}
_waking: dict[int, asyncio.Lock] = {}
_idle_since: dict[int, float] = {}
_password_bad = False
_deferred: set[int] = set()


def _stop_line(why: str) -> str:
    first = who.first()
    return (f"{who.mac()} stayed locked: {why} Tell {first} in one line, in your words, and that this work waits until "
            f"{first} unlocks the Mac. Don't ask to wake it again for this work.")


_STOPS = {
    "second_screen": _stop_line("it has a second screen attached, and Zero keeps the screen dark while it has the Mac "
                                "unlocked, which only works on the built-in screen."),
    "no_dimming": _stop_line("its screen couldn't be darkened, and Zero keeps the screen dark while it has the Mac "
                             "unlocked."),


    "no_guard": _stop_line("Zero needs Input Monitoring turned on for it in the Mac's Privacy & Security settings, so "
                           "it can lock the Mac the moment anyone touches it while it's unlocked."),
}


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS mac_wakes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id TEXT NOT NULL,
        work TEXT NOT NULL,             -- the board task, or the chat session: one yes per piece of work
        session_id TEXT NOT NULL DEFAULT '',
        origin_task TEXT,
        feed_id INTEGER,                -- the choice card that asks
        state TEXT NOT NULL,            -- asked, yes, woke, paused, no, failed, relock (the Mac hasn't answered it), ended
        paused_at REAL,                 -- someone used the Mac Zero had unlocked, and it locked at once
        asked_at REAL NOT NULL,
        answered_at REAL,
        woke_at REAL,
        ended_at REAL)""")


def _db():
    return store.db()


def _wake_lock() -> asyncio.Lock:
    ""
    return _waking.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())


def _work(session_id: str, origin_task: str | None) -> str:
    return origin_task or session_id or "chat"


def note_locked(bot_id: str, session_id: str, origin_task: str | None) -> None:
    _locked_seen[(bot_id, _work(session_id, origin_task))] = time.time()


def _met_lock(bot_id: str, work: str) -> bool:
    return time.time() - _locked_seen.get((bot_id, work), 0) <= LOCKED_SEEN_SEC


def _live(bot_id: str, work: str):
    ""
    return _db().execute("SELECT * FROM mac_wakes WHERE bot_id=? AND work=? AND state IN ('asked','yes','woke','paused') "
                         "ORDER BY id DESC LIMIT 1", (bot_id, work)).fetchone()


def _set(row_id: int, **fields) -> None:
    with store._lock:
        cols = ", ".join(f"{k}=?" for k in fields)
        _db().execute(f"UPDATE mac_wakes SET {cols} WHERE id=?", (*fields.values(), row_id))
        _db().commit()


class WakeError(Exception):
    ""




async def ask(bot_id: str, session_id: str, origin_task: str | None, question: str) -> str:
    ""
    from . import choices, mac
    question = " ".join(str(question or "").split())
    work = _work(session_id, origin_task)
    if not question or len(question) > QUESTION_MAX:
        raise WakeError("Say it in one or two short sentences, in your own words: what this work is, and ask to wake "
                        f"{who.possessive()} Mac.")
    if not _met_lock(bot_id, work):
        raise WakeError(f"Ask only after a screen step on {who.mac()} said it is locked. Work that doesn't need the "
                        "screen, such as commands and files, runs without waking it.")
    if _password_bad:
        raise WakeError(f"The saved Mac password stopped working, and {who.first()} was told. Don't ask again until "
                        f"{who.first()} saves it again on the Mac. Do what you can without the screen.")
    row = _live(bot_id, work)
    if row is not None and row["state"] == "paused":
        return paused_line()
    if row is not None and row["state"] in ("yes", "woke"):
        return (f"{who.first()} already said yes for this work. Take your screen step again; the Mac wakes for it.")
    if row is not None and row["feed_id"]:
        choices.close_passed(bot_id, row["feed_id"], "asked again")
        _set(row["id"], state="ended", ended_at=time.time())
    now = time.time()
    with store._lock:
        cur = _db().execute("INSERT INTO mac_wakes (bot_id, work, session_id, origin_task, state, asked_at) "
                            "VALUES (?,?,?,?, 'asked', ?)", (bot_id, work, session_id or "", origin_task, now))
        wake_id = cur.lastrowid
        _db().commit()
    card = {"question": question, "options": [YES, NOT_NOW], "title": "Wake your Mac", "mac_wake": wake_id,
            **({"task_id": origin_task} if origin_task else {})}
    with store._lock:
        choices._publish_new(bot_id, card)
        feed = _db().execute("SELECT id FROM feed WHERE kind='choices' AND bot_id=? ORDER BY id DESC LIMIT 1",
                             (bot_id,)).fetchone()
    feed_id = feed["id"] if feed else None
    _set(wake_id, feed_id=feed_id)
    await _notify(bot_id, question, feed_id)
    log.info("%s asked to wake the Mac (card %s)", bot_id, feed_id)
    return (f"{who.first()} has your card and a notification. Stop here and leave the Mac alone. {who.possessive()} "
            "answer arrives in this chat: on Wake My Mac, take your screen step again and the Mac wakes for it; on "
            "Not Now, leave the work waiting and say so in one line.")


async def _notify(bot_id: str, question: str, feed_id: int | None) -> None:
    from . import apns
    bot = config.BOTS_BY_ID.get(bot_id)
    try:

        actions = [{"id": "wake:yes", "title": YES, "mode": "auth"}, {"id": "wake:no", "title": NOT_NOW, "mode": "background"}]
        await asyncio.to_thread(lambda: apns.send(bot.name if bot else "Zero", question, bot_id,
                                                  collapse_id=f"wake-{feed_id}",
                                                  extra={"feed_id": feed_id, "mac_wake": 1}, category="zero.open",
                                                  actions=actions, hold=False))
    except Exception:
        log.warning("could not send the wake card's notification", exc_info=True)


def answered(card: dict, picked: str | None) -> None:
    ""
    wake_id = card.get("mac_wake")
    if not wake_id:
        return
    row = _db().execute("SELECT * FROM mac_wakes WHERE id=?", (wake_id,)).fetchone()
    if row is None or row["state"] != "asked":
        return
    _set(wake_id, state="yes" if picked == YES else "no", answered_at=time.time())


def answer_from_notification(bot_id: str, feed_id: int, yes: bool) -> dict:
    ""

    from . import choices
    row = _db().execute("SELECT body FROM feed WHERE id=? AND kind='choices' AND bot_id=?", (feed_id, bot_id)).fetchone()
    if row is None or not json.loads(row["body"] or "{}").get("mac_wake"):
        raise WakeError("That card is gone.")
    return choices.answer(bot_id, feed_id, YES if yes else NOT_NOW)




async def _wake(row) -> bool:
    ""
    global _password_bad
    from . import mac, turns
    async with _wake_lock():
        fresh = _db().execute("SELECT * FROM mac_wakes WHERE id=?", (row["id"],)).fetchone()
        if fresh is None or fresh["state"] not in ("yes", "woke"):
            return False
        try:
            reply = await mac._call("wake", {"wake": row["id"]})
        except mac.MacError as e:
            code = getattr(e, "code", "")
            if code == "wrong_password":
                _password_bad = True
                _set(row["id"], state="failed", ended_at=time.time())
                note = (f"{who.hub_note()} The Mac password saved on {who.mac()} didn't unlock it, so the Mac stayed "
                        f"locked. It may have changed. Tell {who.first()} in one line, in your words, and that it can "
                        "be updated in Zero's settings on the Mac. Don't ask to wake it again until then.")
                asyncio.create_task(turns.follow_up(row["bot_id"], note, row["session_id"] or None, urgent=False))
                log.info("a wake for %s failed: the saved password", row["bot_id"])
            elif code in _STOPS:


                _set(row["id"], state="failed", ended_at=time.time())
                note = f"{who.hub_note()} {_STOPS[code]}"
                asyncio.create_task(turns.follow_up(row["bot_id"], note, row["session_id"] or None, urgent=False))
                log.info("a wake for %s didn't happen: %s", row["bot_id"], code)
            else:
                _deferred.add(row["id"])
                log.info("a wake for %s didn't happen yet: %s", row["bot_id"], code or "mac")
            return False
        result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
        if result.get("unlocked"):



            _set(row["id"], state="woke", woke_at=time.time())
            _idle_since.pop(row["id"], None)
            if result.get("woke"):
                log.info("Zero woke the Mac for %s", row["bot_id"])
        return bool(result.get("unlocked"))


async def wake_if_yes(bot_id: str, session_id: str, origin_task: str | None, live_since: float | None = None) -> bool:
    ""


    from . import desk
    work = _work(session_id, origin_task)
    note_locked(bot_id, session_id, origin_task)
    row = _live(bot_id, work)
    if row is None or _password_bad:
        return False
    if row["state"] == "paused":
        if not (desk.mac_free() or (live_since is not None and live_since > (row["paused_at"] or 0))):
            return False
        _set(row["id"], state="yes")
        row = _live(bot_id, work)
    if row["state"] not in ("yes", "woke"):
        return False
    _deferred.discard(row["id"])
    return await _wake(row)


def paused_line() -> str:
    ""
    first = who.first()
    return (f"Someone used {who.mac()} while it was unlocked for you, so it locked at once. Stop here and leave the "
            f"window as it is; finish what you can without the screen. {first}'s yes still holds, so don't ask again: "
            f"the home computer wakes you when {first} is off the Mac, or {first} can say go on, and your next screen "
            "step wakes it.")


def is_paused(bot_id: str, session_id: str, origin_task: str | None) -> bool:
    row = _live(bot_id, _work(session_id, origin_task))
    return row is not None and row["state"] == "paused"


def guard_tripped() -> None:
    ""

    from . import macqueue
    now = time.time()
    for row in _db().execute("SELECT * FROM mac_wakes WHERE state='woke'").fetchall():
        _set(row["id"], state="paused", paused_at=now)
        _idle_since.pop(row["id"], None)
        macqueue.wait(row["bot_id"], row["session_id"], row["origin_task"], "carry on with the work on the Mac",
                      macqueue.PERSON, say=False, trusted=True)
        log.info("someone used the Mac Zero had unlocked for %s: locked and paused", row["bot_id"])


def _busy() -> bool:
    ""

    from . import mac, maccodex
    return any(mac.acting(g) for g in list(mac._grants.values())) or bool(maccodex.running())


async def _relock(row) -> None:
    from . import mac
    _set(row["id"], state="relock")
    _idle_since.pop(row["id"], None)
    async with _wake_lock():
        try:
            reply = await mac._call("relock", {})
        except mac.MacError:
            log.info("the Mac didn't answer the lock again; asking again once it does")
            return
    with store._lock:
        _db().execute("UPDATE mac_wakes SET state='ended', ended_at=? WHERE state='relock'", (time.time(),))
        _db().commit()
    locked = bool((reply.get("result") or {}).get("locked"))
    log.info("the work Zero woke the Mac for ended; %s", "locked again" if locked else "left as it is")


async def tick(now: float | None = None) -> None:
    from . import mac
    now = time.time() if now is None else now
    if not mac.online():
        return
    for row in _db().execute("SELECT * FROM mac_wakes WHERE state='paused' AND paused_at < ?",
                             (now - PAUSED_KEEP_SEC,)).fetchall():
        _set(row["id"], state="ended", ended_at=now)
    for row in _db().execute("SELECT * FROM mac_wakes WHERE state IN ('yes','woke')").fetchall():
        if row["state"] == "yes" and not _password_bad and row["id"] not in _deferred:
            if await _wake(row):
                _idle_since[row["id"]] = now
            continue
        if row["state"] != "woke":
            continue
        if _busy():
            _idle_since.pop(row["id"], None)
            continue
        since = _idle_since.setdefault(row["id"], now)
        if now - since >= IDLE_SEC:
            await _relock(row)
            return

    pending = _db().execute("SELECT * FROM mac_wakes WHERE state='relock' LIMIT 1").fetchone()
    if pending is not None and not _busy() and \
            _db().execute("SELECT 1 FROM mac_wakes WHERE state='woke' LIMIT 1").fetchone() is None:
        await _relock(pending)


def password_changed() -> None:
    ""
    global _password_bad
    _password_bad = False


async def run() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.warning("the Mac wake pass failed", exc_info=True)
        await asyncio.sleep(POLL_SEC)
