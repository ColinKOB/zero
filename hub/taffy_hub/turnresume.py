""















from __future__ import annotations

import asyncio
import logging
import time

from . import config, store, who

log = logging.getLogger("taffy.turnresume")

BOOTED = time.time()



MAX_AGE_SEC = 2 * 3600
WAIT_SEC = 15
TRIES = 8
RESUMING_SEC = 5 * 60
WORDS_MAX = 4000
FAILED = "I couldn't finish that reply after a restart. Try again."

_picking_up: dict[str, float] = {}


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS open_turns (
        turn_id TEXT PRIMARY KEY,
        bot_id TEXT NOT NULL,
        words TEXT NOT NULL,      -- the message, so the Zip can be handed it again
        started REAL NOT NULL)""")


def began(turn_id: str, bot_id: str, words: str) -> None:
    ""
    try:
        with store._lock:
            c = store.db()
            c.execute("DELETE FROM open_turns WHERE bot_id=?", (bot_id,))
            c.execute("INSERT OR REPLACE INTO open_turns (turn_id, bot_id, words, started) VALUES (?,?,?,?)",
                      (turn_id, bot_id, str(words or "")[:WORDS_MAX], time.time()))
            c.commit()
        _picking_up.pop(bot_id, None)
    except Exception:
        log.warning("could not note that %s's turn began", bot_id, exc_info=True)


def ended(turn_id: str) -> None:
    ""
    try:
        with store._lock:
            store.db().execute("DELETE FROM open_turns WHERE turn_id=?", (turn_id,))
            store.db().commit()
    except Exception:
        log.warning("could not note that a turn ended", exc_info=True)


def _written_down(turn_id: str) -> bool:
    return store.db().execute("SELECT 1 FROM open_turns WHERE turn_id=?", (turn_id,)).fetchone() is not None


def cut_off() -> list[dict]:
    ""
    rows = store.db().execute("SELECT * FROM open_turns WHERE started<? ORDER BY started", (BOOTED,)).fetchall()
    return [dict(r) for r in rows]


def resuming(bot_id: str) -> bool:
    ""
    if time.time() - _picking_up.get(bot_id, 0) < RESUMING_SEC:
        return True
    try:
        return any(r["bot_id"] == bot_id and BOOTED - r["started"] < MAX_AGE_SEC for r in cut_off())
    except Exception:
        return False


def note(words: str) -> str:
    first = who.first()
    return (f"{who.hub_note()} The home computer restarted while you were answering {first}'s message below, so your "
            f"reply never reached {first}. Part of the work may already be done: check first (your Sent mail, the "
            "site, this chat) and don't repeat a send, a submission or a booking that already happened. Then finish "
            f"what is left and answer {first} as you would have. Don't mention the restart unless it changed the "
            f"result. {first}'s message was:\n\n{words}")


def answers(m: dict) -> bool:
    ""


    return not m.get("step") and not str(m.get("id") or "").startswith("first-")


def _finished(msgs: list[dict], started: float) -> bool:
    ""
    if not msgs:
        return False
    last = msgs[-1]
    return (last.get("class") in ("bot", "board_reply") and answers(last)
            and isinstance(last.get("ts"), (int, float)) and last["ts"] > started)


async def _replied_since(bot_id: str, started: float) -> bool | None:
    ""

    from . import hermes, history
    if not store.session_for(bot_id):
        return False
    try:
        msgs = history.visible(await asyncio.wait_for(hermes.messages(bot_id, limit=12), 10))
    except Exception:
        return None
    return _finished(msgs, started)


async def pick_up(row: dict, tries: int = TRIES, wait: float = WAIT_SEC) -> str:
    ""



    from . import activity, turns
    bot_id = row["bot_id"]
    keep = False
    try:
        if bot_id not in config.BOTS_BY_ID or BOOTED - row["started"] > MAX_AGE_SEC:
            return "old"
        landed = None
        for i in range(tries):
            landed = await _replied_since(bot_id, row["started"])
            if landed is not None:
                break
            if i + 1 < tries:
                await asyncio.sleep(wait)
        if landed:
            return "landed"



        if not _written_down(row["turn_id"]):
            return "landed"
        _picking_up[bot_id] = time.time()
        log.info("picking %s's cut-off reply back up", bot_id)
        asked = time.time()
        reply = await turns.follow_up(bot_id, note(row["words"]), None, True, tracked=True)
        if reply:
            return "answered"


        from . import handoff
        if handoff.since(bot_id, asked):
            return "handed"
        if turns._stopped_since(bot_id, asked):
            return "stopped"



        if not _written_down(row["turn_id"]):
            return "landed"
        if turns._left_a_card(bot_id, asked):
            return "card"
        activity.finished(bot_id, FAILED, row["words"])
        return "failed"
    except asyncio.CancelledError:
        keep = True
        raise
    except Exception:
        activity.finished(bot_id, FAILED, row["words"])
        raise
    finally:
        _picking_up.pop(bot_id, None)
        if not keep:
            ended(row["turn_id"])


async def run() -> None:
    ""
    try:
        rows = cut_off()
    except Exception:
        log.exception("could not read the turns a restart cut off")
        return
    if rows:
        log.info("%d turn(s) were cut off by the restart", len(rows))
    results = await asyncio.gather(*(pick_up(r) for r in rows), return_exceptions=True)
    for r, out in zip(rows, results):
        if isinstance(out, Exception):
            log.warning("could not pick %s's cut-off reply back up: %r", r["bot_id"], out)
        else:
            log.info("%s's cut-off reply: %s", r["bot_id"], out)
