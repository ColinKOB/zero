""

























from __future__ import annotations

import asyncio
import json
import logging
import time

from . import config, store, who

log = logging.getLogger("taffy.macqueue")

POLL_SEC = 10
HOLD_SEC = 5 * 60
MAX_WAIT_SEC = 24 * 3600
WHAT_MAX = 160
SCREEN, PERSON, TERRA = "screen", "person", "terra"


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS mac_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id TEXT NOT NULL,
        work TEXT NOT NULL,                -- the board task, or the chat session: one place in line per piece of work
        session_id TEXT NOT NULL DEFAULT '',
        origin_task TEXT,
        team_id TEXT NOT NULL DEFAULT '',
        what TEXT NOT NULL,                -- the task, as the Zip said it when it asked for the Mac
        why TEXT NOT NULL,                 -- screen, person or terra
        live INTEGER NOT NULL DEFAULT 0,   -- 1: he was talking with this Zip when it got in line
        trusted INTEGER NOT NULL DEFAULT 0, -- 1: the person's own ask, or traced to the person or Zero (mac._traced)
        since REAL NOT NULL,
        woken REAL NOT NULL DEFAULT 0,     -- when it was told the Mac is free, or 0
        UNIQUE (bot_id, work))""")


def _db():
    return store.db()


def _name(bot_id: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    return bot.name if bot else bot_id


def _resource(why: str) -> tuple[str, ...]:
    return (TERRA,) if why == TERRA else (SCREEN, PERSON)


def _task_words(what: str) -> str:
    ""
    words = " ".join(str(what or "").split()).rstrip(".")[:WHAT_MAX]
    first = words.split(" ", 1)[0]
    return words if len(first) > 1 and first.isupper() else words[:1].lower() + words[1:]


def line_for(why: str, what: str, behind: str = "") -> str:
    ""
    task = _task_words(what) or "carry on"
    if why == PERSON:
        return f"I'll {task} when you're off your Mac. Say go ahead if you'd like me to start now."
    if why == TERRA:
        return f"I'll {task} as soon as one of the two jobs running on your Mac finishes."
    return f"I'll {task} as soon as {behind or 'the Zip using it'} is done on your Mac."


def wait(bot_id: str, session_id: str, origin_task: str | None, what: str, why: str, *, behind: str = "",
         team_id: str = "", live: bool = False, say: bool = True, trusted: bool = False) -> int:
    ""


    work = origin_task or session_id or "chat"
    now = time.time()
    try:
        with store._lock:
            c = _db()
            old = c.execute("SELECT id FROM mac_queue WHERE bot_id=? AND work=?", (bot_id, work)).fetchone()
            if old:



                c.execute("UPDATE mac_queue SET why=?, woken=0, live=CASE WHEN ?=? THEN 0 ELSE live END WHERE id=?",
                          (why, why, PERSON, old["id"]))
            else:
                c.execute("INSERT INTO mac_queue (bot_id, work, session_id, origin_task, team_id, what, why, live, "
                          "trusted, since) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (bot_id, work, session_id or "", origin_task, team_id or "",
                           " ".join(str(what or "").split())[:400], why, int(live), int(trusted), now))
            c.commit()
    except Exception:

        log.warning("could not put %s in line for the Mac", bot_id, exc_info=True)
        return 1
    if not old:
        log.info("%s is in line for the Mac (%s)", bot_id, why)
        if say:


            try:
                store.add_feed(CARD_KIND, bot_id, origin_task, CARD_TITLE,
                               json.dumps({"state": "open", "work": work, "go": why == PERSON,
                                           "line": card_line(why, what, _name(bot_id), behind)}),
                               written_by="hub", written_for="person")
            except Exception:
                log.warning("could not say that %s is waiting for the Mac", bot_id, exc_info=True)
    try:
        return place(bot_id, work) or 1
    except Exception:
        return 1


CARD_KIND = "mac_wait"
CARD_TITLE = "Waiting for Your Mac"
GO = "goahead:"


class GoAheadError(Exception):
    ""


def go_ahead(bot_id: str, feed_id: int) -> str:
    ""








    row = _db().execute("SELECT title, body FROM feed WHERE id=? AND bot_id=? AND kind=?",
                        (feed_id, bot_id, CARD_KIND)).fetchone()
    if row is None:
        raise GoAheadError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("state") != "open":
        return str(card.get("state") or "closed")
    work, now = str(card.get("work") or ""), time.time()
    with store._lock:
        c = _db()
        spot = c.execute("SELECT * FROM mac_queue WHERE bot_id=? AND work=?", (bot_id, work)).fetchone()
        if spot is not None and spot["why"] == TERRA:
            raise GoAheadError("This waits for a job on the Mac to finish, so it can't start sooner.")
        if spot is not None and spot["origin_task"]:
            c.execute("INSERT OR REPLACE INTO mac_queue (bot_id, work, session_id, origin_task, team_id, what, why, "
                      "live, trusted, since, woken) VALUES (?,?,?,NULL,'',?,?,1,1,?,?)",
                      (bot_id, GO + work, spot["session_id"], spot["what"], spot["why"], now, now))
            woke = c.execute("SELECT * FROM mac_queue WHERE bot_id=? AND work=?", (bot_id, GO + work)).fetchone()
        elif spot is not None:
            c.execute("UPDATE mac_queue SET live=1, trusted=1, woken=? WHERE id=?", (now, spot["id"]))
            woke = c.execute("SELECT * FROM mac_queue WHERE id=?", (spot["id"],)).fetchone()
        else:
            woke = None
        c.commit()
    if woke is None:
        settle_cards(bot_id, work, "closed")
        return "closed"
    from . import waits
    waits.queue_wake(bot_id, _note(woke))
    store.update_feed(feed_id, row["title"], json.dumps({**card, "state": "going", "at": now}))
    log.info("%s goes ahead on the Mac (Go Ahead pressed)", bot_id)
    return "going"


def card_line(why: str, what: str, name: str, behind: str = "") -> str:
    ""
    task = _task_words(what) or "carry on"
    if why == TERRA:
        return f"{name} will {task} when a job on your Mac finishes."
    if why == SCREEN and behind:
        return f"{name} will {task} when {behind} is done on your Mac."
    return f"{name} will {task} when your Mac is free."


def settle_cards(bot_id: str, work: str, state: str, was: tuple[str, ...] = ("open", "going")) -> None:
    ""

    try:
        with store._lock:
            for row in store.db().execute("SELECT id, title, body FROM feed WHERE kind=? AND bot_id=?",
                                          (CARD_KIND, bot_id)).fetchall():
                card = json.loads(row["body"] or "{}")
                if card.get("state") in was and card.get("work") == work:
                    store.update_feed(row["id"], row["title"], json.dumps({**card, "state": state, "at": time.time()}))
    except Exception:
        log.warning("could not settle the Mac card for %s", bot_id, exc_info=True)


def place(bot_id: str, work: str) -> int:
    ""
    row = _db().execute("SELECT id, why FROM mac_queue WHERE bot_id=? AND work=?", (bot_id, work)).fetchone()
    if not row:
        return 0
    kinds = _resource(row["why"])
    ahead = _db().execute(f"SELECT COUNT(*) FROM mac_queue WHERE id<? AND why IN ({','.join('?' * len(kinds))})",
                          (row["id"], *kinds)).fetchone()[0]
    return ahead + 1


def in_line(bot_id: str) -> bool:
    ""

    try:
        return _db().execute("SELECT 1 FROM mac_queue WHERE bot_id=? LIMIT 1", (bot_id,)).fetchone() is not None
    except Exception:
        log.warning("could not read the line for the Mac", exc_info=True)
        return False


def left(bot_id: str, session_id: str, origin_task: str | None, terra: bool = False) -> None:
    ""
    kinds = _resource(TERRA if terra else SCREEN)
    try:
        with store._lock:
            _db().execute(f"DELETE FROM mac_queue WHERE bot_id=? AND work=? AND why IN ({','.join('?' * len(kinds))})",
                          (bot_id, origin_task or session_id or "chat", *kinds))
            gone = [] if terra else [r["work"] for r in _db().execute(
                "SELECT work FROM mac_queue WHERE bot_id=? AND work LIKE ?", (bot_id, GO + "%"))]
            if gone:
                _db().execute("DELETE FROM mac_queue WHERE bot_id=? AND work LIKE ?", (bot_id, GO + "%"))
            _db().commit()
        settle_cards(bot_id, origin_task or session_id or "chat", "started")
        for work in gone:
            settle_cards(bot_id, work[len(GO):], "started", was=("going",))
    except Exception:
        log.warning("could not take %s out of the line for the Mac", bot_id, exc_info=True)


def waiting_tasks() -> set[str]:
    ""
    try:
        return {r["origin_task"] for r in _db().execute("SELECT origin_task FROM mac_queue WHERE origin_task IS NOT NULL")}
    except Exception:
        log.warning("could not read the line for the Mac", exc_info=True)
        return set()


def waiting() -> list[dict]:
    ""
    return [{"bot": r["bot_id"], "name": _name(r["bot_id"]), "what": r["what"], "why": r["why"], "since": r["since"]}
            for r in _db().execute("SELECT * FROM mac_queue ORDER BY id")]


def told(place_in_line: int, why: str, behind: str = "") -> str:
    ""
    first = who.first()
    spot = "You are next in line" if place_in_line <= 1 else f"You are number {place_in_line} in line"
    again = ("start your Terra run again with mac_codex" if why == TERRA else "call mac_start again")
    rest = (f"Don't try again before that, and don't ask {first} to arrange it or raise a card about it. Do everything "
            "else you can now. On a board task, park it with kanban_schedule until that note arrives, which brings the "
            "task back. Waiting is not a block, so don't use kanban_block for it.")
    if why == PERSON:



        return (f"{spot} for it. The home computer wakes you when {first} steps away from the Mac: then {again}. "
                f"{first}'s chat already shows one line naming this step and saying that \"go ahead\" starts it now, "
                f"so don't repeat it. If you mention it, say only what you'll do, that you'll do it when {first} steps "
                f"away, and that \"go ahead\" does it now. Never say queued, in line, or that the Mac isn't available. "
                + rest)
    return f"{spot} for it. The home computer wakes you the moment it is free: then {again}. " + rest


def _free(row) -> bool:
    from . import desk, mac, maccodex
    if not mac.online():
        return False
    if row["why"] == TERRA:
        return len(maccodex.running()) < maccodex.MAX_RUNNING
    if mac._screen_holder(row["bot_id"]) is not None:
        return False


    return bool(row["live"]) or desk.mac_free()


def trusted_leads() -> tuple[str, str]:
    ""



    return (f"{who.hub_note()} {who.mac()} is free now, and it is your turn.",
            f"{who.hub_note()} A Terra slot on {who.mac()} is free now, and it is your turn.")


def _note(row) -> str:
    first, what = who.first(), " ".join(str(row["what"]).split())[:300].rstrip(".")
    screen_lead, terra_lead = trusted_leads() if row["trusted"] else (
        f"{who.hub_note()} Your turn in the line for {who.mac()} has come.",
        f"{who.hub_note()} Your turn for a Terra slot on {who.mac()} has come.")
    if row["why"] == TERRA:
        return (f"{terra_lead} Start your Terra run again with mac_codex (mac_start first if your go-ahead has ended) "
                f"for this: {what}.")
    return (f"{screen_lead} Call mac_start again and carry on with this: {what}. If {first} or another Zip is on it "
            "again by the time you start, you keep your place and are woken again.")







def live_wake(bot_id: str, text: str) -> bool:
    ""
    try:
        rows = _db().execute("SELECT * FROM mac_queue WHERE bot_id=? AND live=1 AND woken>=? AND why!=?",
                             (bot_id, time.time() - HOLD_SEC, TERRA)).fetchall()
        return any(text.startswith(_note(row)) for row in rows)
    except Exception:
        log.warning("could not tell whether %s's wake is its own ask", bot_id, exc_info=True)
        return False


def _deliver(row) -> bool:
    ""
    from . import board, waits
    note = _note(row)
    if row["origin_task"]:
        ok, _ = board.wake(row["origin_task"], note)
        status = (board.get_task(row["origin_task"]) or {}).get("status")
        if not ok and status not in ("done", "archived", None):
            ok, _ = board.comment(row["origin_task"], note)
        if ok or status in ("done", "archived", None):
            return ok
    elif row["team_id"]:
        from . import teams
        teams.scheduler.note(row["team_id"], row["bot_id"], note)
        teams.scheduler.kick()
        return True
    waits.queue_wake(row["bot_id"], note)
    waits.kick()
    return True


def release(now: float | None = None) -> list[str]:
    ""
    now = time.time() if now is None else now
    with store._lock:



        gone = _db().execute("SELECT origin_task FROM mac_queue WHERE woken=0 AND since<? AND origin_task IS NOT NULL",
                             (now - MAX_WAIT_SEC,)).fetchall()
        dropped = _db().execute("SELECT bot_id, work FROM mac_queue WHERE (woken>0 AND woken<?) OR since<?",
                                (now - HOLD_SEC, now - MAX_WAIT_SEC)).fetchall()
        _db().execute("DELETE FROM mac_queue WHERE (woken>0 AND woken<?) OR since<?", (now - HOLD_SEC, now - MAX_WAIT_SEC))
        _db().commit()
    for row in dropped:
        if row["work"].startswith(GO):
            settle_cards(row["bot_id"], row["work"][len(GO):], "open", was=("going",))
        else:
            settle_cards(row["bot_id"], row["work"], "closed")
    for row in gone:
        try:
            from . import board
            board.wake(row["origin_task"], f"{who.hub_note()} A day passed without your turn on {who.mac()}, so your "
                       "place in line has ended. Call mac_start again for a new place, or finish what you can without it.",
                       board.DATE)
        except Exception:
            log.exception("could not bring %s back after its place in line ended", row["origin_task"])
    woken = []
    for kinds in ((SCREEN, PERSON), (TERRA,)):
        rows = _db().execute(f"SELECT * FROM mac_queue WHERE why IN ({','.join('?' * len(kinds))}) ORDER BY id",
                             kinds).fetchall()
        if not rows or any(r["woken"] for r in rows):
            continue
        head = rows[0]
        try:
            if not _free(head):
                continue
            _deliver(head)
        except Exception:
            log.exception("could not wake %s for the Mac", head["bot_id"])
            continue
        with store._lock:
            _db().execute("UPDATE mac_queue SET woken=? WHERE id=?", (now, head["id"]))
            _db().commit()
        log.info("%s's turn on the Mac (%s)", head["bot_id"], head["why"])
        woken.append(head["bot_id"])
    return woken


async def run() -> None:
    while True:
        try:
            await asyncio.to_thread(release)
        except Exception:
            log.exception("a pass over the line for the Mac failed")
        await asyncio.sleep(POLL_SEC)
