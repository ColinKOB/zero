""





























from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass

from . import chatter, config, hermes, store, who

log = logging.getLogger("taffy.handoff")

RETRY_DELAYS = (30, 90, 120)
GATHER_SEC = 20
OUTAGE_WINDOW_SEC = 300
CHECK_EVERY_SEC = 60
RELEASE_GAP_SEC = 15
LIMIT_RECHECK_SEC = 15 * 60
SUMMARY_CAP_SEC = 120
WHAT_MAX, SUMMARY_MAX, RAW_MAX, FACTS_MAX = 600, 900, 300, 600
HOLD = ("internet", "provider", "limit", "engine")
HEAD = "[Hub note: Work handed to you]"



_NET = re.compile(r"connection error|connecterror|connecttimeout|apiconnectionerror|connection refused|connection reset|"
                  r"name or service not known|temporary failure in name resolution|nodename nor servname|getaddrinfo|"
                  r"network is unreachable|no route to host|server disconnected|remoteprotocolerror|"
                  r"\b50[234]\b|bad gateway|service unavailable|gateway timeout|overloaded", re.I)


def wait_sec(sec: float) -> float:
    ""
    return 0.0 if os.environ.get("ZERO_TESTS") == "1" else sec


@dataclass
class Failure:
    cause: str
    reason: str
    raw: str

    @property
    def held(self) -> bool:
        return self.cause in HOLD

    @property
    def final(self) -> bool:
        ""

        return self.cause == "key"


_net_failures: dict[str, float] = {}
_outage: dict | None = None


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL,
            -- held (an outage), running (held work running again), retrying (between quiet tries), asking (the Zip
            -- is asked for its summary), to_zero, sent (in Zero's wake), released (done), stopped (he pressed Stop),
            -- called_off (what it came from is no longer wanted: its wait closed, its Zip deleted)
            state TEXT NOT NULL,
            cause TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', raw TEXT NOT NULL DEFAULT '',
            what TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', facts TEXT NOT NULL DEFAULT '',
            via TEXT NOT NULL DEFAULT 'wake',    -- how held work runs again: chat, follow_up or wake
            note TEXT NOT NULL DEFAULT '', session_id TEXT, urgent INTEGER NOT NULL DEFAULT 0,
            tries INTEGER NOT NULL DEFAULT 0, minutes INTEGER NOT NULL DEFAULT 0,
            retry_after REAL NOT NULL DEFAULT 0, created REAL NOT NULL, sent_at REAL,
            told INTEGER NOT NULL DEFAULT 0      -- the outage's one line went out while this was held
        );
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(handoffs)")}
    if "told" not in cols:
        c.execute("ALTER TABLE handoffs ADD COLUMN told INTEGER NOT NULL DEFAULT 0")
    if "wait_id" not in cols:
        c.execute("ALTER TABLE handoffs ADD COLUMN wait_id INTEGER")


def _flat(text, limit: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "..."


def _name(bot_id: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    return bot.name if bot else "Zero"




def _plain(raw: str) -> str:
    low = raw.lower()
    if low.startswith("reply check"):
        return "its reply didn't pass the hub's check that it matched what it did"
    if low.startswith("http 4") or low.startswith("http 5"):
        return "the engine refused to start the turn"
    if low == "cancelled" or "stopped partway" in low:
        return "it was cut off partway"
    if "timeout" in low or "timed out" in low:
        return "it timed out"
    if not raw:
        return "it ended without an answer"
    return "it ran into an error"


async def reach() -> str | None:
    ""

    if os.environ.get("ZERO_TESTS") == "1":
        return None
    import httpx

    async def up(url: str, service: bool) -> bool:
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=False) as c:
                r = await c.head(url)
            return r.status_code < 500 if service else True
        except Exception:
            return False
    if not any(await asyncio.gather(up("https://www.apple.com", False), up("https://one.one.one.one", False))):
        return "internet"
    if not any(await asyncio.gather(up("https://api.anthropic.com", True), up("https://chatgpt.com", True))):
        return "provider"
    return None


def outage() -> dict | None:
    return dict(_outage) if _outage else None


def _start_outage(cause: str) -> None:
    global _outage
    if _outage is None:
        _outage = {"cause": cause, "since": time.time(), "told": cause == "engine"}
        log.warning("handoff: an outage (%s); Zips hold their work until it's over", cause)


async def classify(bot_id: str, *, restart: bool = False, limited: bool = False) -> Failure:
    ""


    raw = _flat(hermes.last_raw.get(bot_id) or hermes.last_error.get(bot_id) or "", RAW_MAX)
    from . import health
    if _bills_by_use(bot_id):
        return await _classify_keyed(raw, restart)
    if limited or hermes.last_limited.get(bot_id) or any(w in raw.lower() for w in health.LIMIT_WORDS if w != "overloaded"):
        return Failure("limit", "the plan's usage limit stopped it", raw)
    if restart or raw.startswith(hermes.ENGINE_DOWN):
        return Failure("engine", "the engine the Zips run on stopped answering", raw)
    if not _NET.search(raw):
        return Failure("passing", _plain(raw), raw)
    now = time.time()
    _net_failures[bot_id] = now
    if _outage and _outage["cause"] != "limit":
        return Failure(_outage["cause"], "the connection is down", raw)

    others = [b for b, t in _net_failures.items() if b != bot_id and now - t <= OUTAGE_WINDOW_SEC
              and not _bills_by_use(b)]
    cause = await reach()
    if cause is None and others:
        cause = "provider"
    if cause:
        return Failure(cause, "the connection is down", raw)
    return Failure("passing", "its connection dropped partway", raw)


def _bills_by_use(bot_id: str) -> bool:
    from . import modelsetup
    try:
        return modelsetup.bills_by_use(bot_id)
    except Exception:
        return False


async def _classify_keyed(raw: str, restart: bool) -> Failure:
    ""



    if restart or raw.startswith(hermes.ENGINE_DOWN):
        return Failure("engine", "the engine the Zips run on stopped answering", raw)
    if hermes.key_refused(raw):
        return Failure("key", "its model's key isn't working", raw)
    if _NET.search(raw) and await reach() == "internet":
        return Failure("internet", "the connection is down", raw)
    if hermes._BUSY.search(raw):
        return Failure("passing", "its model's provider was busy", raw)
    return Failure("passing", _plain(raw), raw)




LINES = {
    "internet": "The internet is down at home, so your Zips are holding their work. They'll pick it back up once "
                "it's back.",
    "provider": "Your Zips' AI service isn't answering, so they're holding their work. They'll pick it back up once "
                "it answers again.",
    "limit": "Your Zips have hit their plan's usage limit, so they're holding their work. They'll pick it back up "
             "once it resets.",
}


def hold(bot_id: str, failure: Failure, *, via: str, note: str, what: str = "", session_id: str | None = None,
         urgent: bool = False, wait_id: int | None = None) -> int:
    ""

    retry_after = 0.0
    if failure.cause == "limit":
        when = hermes.retry_at(failure.raw)
        retry_after = when if isinstance(when, float) else time.time() + LIMIT_RECHECK_SEC
    _start_outage(failure.cause)
    with store._lock:
        c = store.db()
        hid = c.execute("INSERT INTO handoffs (bot_id, state, cause, reason, raw, what, via, note, session_id, urgent, "
                        "retry_after, created, told, wait_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (bot_id, "held", failure.cause, failure.reason, failure.raw, _flat(what, WHAT_MAX), via, note,
                         session_id, int(urgent), retry_after, time.time(),
                         int(bool(_outage and _outage["told"])), wait_id)).lastrowid
        c.commit()
    log.info("handoff: holding %s's work (%s) until the connection is back", bot_id, failure.cause)
    _soon(_tell())
    return hid


async def _tell() -> None:
    ""

    global _outage
    if not _outage or _outage["told"]:
        return
    line = LINES.get(_outage["cause"])
    if not line:
        _outage["told"] = True
        return
    from . import watcher
    try:
        if await watcher.push("Holding work", line, "default", urgent=True, exact=True, kind="needs"):
            if _outage:
                _outage["told"] = True
            with store._lock:
                store.db().execute("UPDATE handoffs SET told=1 WHERE state='held'")
                store.db().commit()
    except Exception:
        log.warning("handoff: could not send the outage line", exc_info=True)


def _held() -> list[sqlite3.Row]:
    return store.db().execute("SELECT * FROM handoffs WHERE state='held' ORDER BY id").fetchall()


async def _back(rows: list[sqlite3.Row]) -> bool:
    now = time.time()
    causes = {r["cause"] for r in rows}
    if "limit" in causes and now < min(r["retry_after"] for r in rows if r["cause"] == "limit"):
        return False
    if "engine" in causes:
        from . import health
        if not health.status.get("bots", True):
            return False
    if causes & {"internet", "provider"}:
        if any(now - t < CHECK_EVERY_SEC for t in _net_failures.values()):
            return False
        if await reach() is not None:
            return False
    return True


def _set_state(hid: int, state: str, was: tuple[str, ...]) -> bool:
    ""
    with store._lock:
        n = store.db().execute(f"UPDATE handoffs SET state=?, sent_at=? WHERE id=? AND state IN "
                               f"({','.join('?' * len(was))})", (state, time.time(), hid, *was)).rowcount
        store.db().commit()
    return n > 0





OPEN = ("held", "running", "retrying", "asking", "to_zero")
RETRY_HEAD = "Your last try at this didn't finish"


def wanted(r: sqlite3.Row) -> bool:
    ""


    if r["bot_id"] not in config.BOTS_BY_ID:
        return False
    if r["wait_id"] is not None:
        w = store.db().execute("SELECT state FROM waits WHERE id=?", (r["wait_id"],)).fetchone()
        if w is None or w["state"] == "closed":
            return False
    return True


def _called_off(r: sqlite3.Row) -> bool:
    ""
    if wanted(r):
        return False
    _set_state(r["id"], "called_off", OPEN)
    log.info("handoff: %s's work was called off before it ran again", r["bot_id"])
    return True


def stopped(bot_id: str) -> int:
    ""
    with store._lock:
        c = store.db()
        n = c.execute(f"UPDATE handoffs SET state='stopped', sent_at=? WHERE bot_id=? AND state IN "
                      f"({','.join('?' * len(OPEN))})", (time.time(), bot_id, *OPEN)).rowcount


        n += c.execute("UPDATE wake_notes SET state='failed' WHERE bot_id=? AND state='retry' AND note LIKE ?",
                       (bot_id, f"%{RETRY_HEAD}%")).rowcount
        c.commit()
    return n


async def _release(r: sqlite3.Row) -> None:
    ""


    from . import waits
    if _called_off(r):
        return
    if r["via"] not in ("chat", "follow_up"):
        with store._lock:
            try:
                if not store.db().execute("UPDATE handoffs SET state='released', sent_at=? WHERE id=? AND state='held'",
                                          (time.time(), r["id"])).rowcount:
                    store.db().rollback()
                    return

                waits.queue_wake(r["bot_id"], r["note"], wait_id=r["wait_id"])
            except BaseException:
                store.db().rollback()
                raise
        waits.kick()
        return
    await _run_follow_up(r)


async def _run_follow_up(r: sqlite3.Row) -> None:
    from . import turns
    if _called_off(r) or not _set_state(r["id"], "running", ("held", "running", "retrying")):
        return
    try:
        await turns.follow_up(r["bot_id"], r["note"], r["session_id"], urgent=bool(r["urgent"]), tracked=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("handoff: %s's held work failed to run again", r["bot_id"])
    _set_state(r["id"], "released", ("running",))


def track(bot_id: str, note: str, session_id: str | None, urgent: bool) -> int:
    ""

    with store._lock:
        c = store.db()
        hid = c.execute("INSERT INTO handoffs (bot_id, state, cause, via, note, session_id, urgent, created) "
                        "VALUES (?,?,?,?,?,?,?,?)", (bot_id, "retrying", "passing", "follow_up", note, session_id,
                                                     int(urgent), time.time())).lastrowid
        c.commit()
    return hid


def untrack(hid: int) -> None:
    with store._lock:
        store.db().execute("DELETE FROM handoffs WHERE id=? AND state IN ('retrying', 'stopped')", (hid,))
        store.db().commit()


def since(bot_id: str, at: float) -> bool:
    ""
    return store.db().execute("SELECT 1 FROM handoffs WHERE bot_id=? AND created>=? AND state NOT IN ('retrying') "
                              "LIMIT 1", (bot_id, at)).fetchone() is not None


def recover() -> list[sqlite3.Row]:
    ""


    with store._lock:
        c = store.db()
        c.execute("UPDATE handoffs SET state='to_zero' WHERE state='asking'")
        rows = c.execute("SELECT * FROM handoffs WHERE state IN ('running', 'retrying') ORDER BY id").fetchall()
        c.executemany("UPDATE handoffs SET state='running' WHERE id=?", [(r["id"],) for r in rows])
        c.commit()
    if rows:
        log.info("handoff: %d follow-up(s) a restart cut off run again", len(rows))
    return rows


async def resume_left(rows: list[sqlite3.Row]) -> None:
    for i, r in enumerate(rows):
        if i:
            await asyncio.sleep(wait_sec(RELEASE_GAP_SEC))
        await _run_follow_up(r)


async def tick() -> None:
    ""

    global _outage
    await flush()
    rows = _held()
    if not rows:
        _outage = None
        return
    if _outage is None:
        _start_outage(rows[0]["cause"])
        _outage["told"] = _outage["told"] or any(r["told"] for r in rows)
    if not await _back(rows):
        await _tell()
        return
    log.info("handoff: the connection looks back; %d held piece(s) of work go back to their Zips", len(rows))
    _net_failures.clear()
    first, rest = rows[0], rows[1:]
    await _release(first)
    if len(_held()) > len(rest):
        return
    _outage = None
    for r in rest:
        await asyncio.sleep(wait_sec(RELEASE_GAP_SEC))
        if store.db().execute("SELECT state FROM handoffs WHERE id=?", (r["id"],)).fetchone()["state"] != "held":
            continue
        await _release(r)


def retire_old_lines() -> int:
    ""

    from . import quiet
    now = time.time()
    with store._lock:
        c = store.db()
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM feed WHERE kind='checkin' AND COALESCE(quiet_word,'')!=? "
            "AND (title LIKE '% didn''t pick this back up' OR title='Place reminder')", (quiet.RETIRED,))]
        c.executemany("UPDATE feed SET quiet_word=?, quiet_at=?, changed_at=? WHERE id=?",
                      [(quiet.RETIRED, now, now, i) for i in ids])
        c.commit()
    n = len(ids)
    if n:
        log.info("handoff: hid %d old failure or place reminder lines", n)
    return n


async def run() -> None:
    try:
        retire_old_lines()
    except Exception:
        log.exception("handoff: could not hide the old lines")
    try:
        _soon(resume_left(recover()))
    except Exception:
        log.exception("handoff: could not pick up what the last start left")
    while True:
        try:
            await tick()
        except Exception:
            log.exception("handoff: a pass failed")
        await asyncio.sleep(CHECK_EVERY_SEC)




SUMMARY_ASK = ("[From Zero, a teammate] Your work on this didn't finish after a few tries, so I'm taking it from here. "
               "The last try ended because {reason}. In two or three plain sentences, for me only: what you already "
               "did, where you stopped and why, and what is left. Take no other action and send nothing.")


async def _ask_summary(bot_id: str, failure: Failure) -> str:
    ""


    from . import turns
    if bot_id == "default" or turns.held(bot_id):
        return ""
    try:
        reply = await turns.run_internal(bot_id, SUMMARY_ASK.format(reason=failure.reason), cap_sec=SUMMARY_CAP_SEC)
    except Exception:
        log.warning("handoff: could not ask %s what it did", bot_id, exc_info=True)
        return ""
    return "" if chatter.empty(reply) else _flat(reply, SUMMARY_MAX)


def start_handoff(bot_id: str, failure: Failure, *, what: str = "", facts: str = "", tries: int = 1,
                  minutes: int = 0, wait_id: int | None = None) -> int:
    ""



    with store._lock:
        c = store.db()
        hid = c.execute("INSERT INTO handoffs (bot_id, state, cause, reason, raw, what, facts, tries, minutes, created, "
                        "wait_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (bot_id, "asking", failure.cause, failure.reason, failure.raw,
                         _flat(what, WHAT_MAX), _flat(facts, FACTS_MAX),
                         tries, minutes, time.time(), wait_id)).lastrowid
        c.commit()
    log.info("handoff: %s's work goes to Zero (%s)", bot_id, failure.reason)
    return hid


async def finish_handoff(hid: int, bot_id: str, failure: Failure) -> None:
    row = store.db().execute("SELECT * FROM handoffs WHERE id=?", (hid,)).fetchone()
    if row["state"] != "asking" or _called_off(row):
        return
    summary = await _ask_summary(bot_id, failure)
    with store._lock:
        store.db().execute("UPDATE handoffs SET state='to_zero', summary=? WHERE id=? AND state='asking'",
                           (summary, hid))
        store.db().commit()
    _soon(_flush_after(GATHER_SEC))


async def hand_to_zero(bot_id: str, failure: Failure, **kw) -> int:
    ""
    hid = start_handoff(bot_id, failure, **kw)
    await finish_handoff(hid, bot_id, failure)
    return hid


def _item(n: int, r: sqlite3.Row) -> str:
    zip_name = _name(r["bot_id"])
    lines = [f"{n}. {zip_name}'s work, after {r['tries']} {'try' if r['tries'] == 1 else 'tries'}"
             + (f" over about {r['minutes']} minutes." if r["minutes"] else ".")]
    if r["what"]:
        lines.append(f"What it was: {r['what']}")
    lines.append(f"Why it stopped: {r['reason']}." + (f" The error: {r['raw']}" if r["raw"] else ""))
    if r["summary"]:
        lines.append(f'{zip_name}\'s own summary: "{r["summary"]}"')
    elif r["bot_id"] == "default":
        lines.append("This was your own work. Read your chat for what you already did.")
    else:
        lines.append(f"{zip_name} couldn't answer when asked what it did." + (f" What the hub knows: {r['facts']}"
                                                                             if r["facts"] else ""))
    earlier = store.db().execute("SELECT COUNT(*) FROM handoffs WHERE bot_id=? AND state='sent' AND id<? AND what=? "
                                 "AND created>?", (r["bot_id"], r["id"], r["what"], r["created"] - 86400)).fetchone()[0]
    if earlier and r["what"]:
        lines.append(f"This is the second time {zip_name}'s work on this came to you today.")
    return "\n".join(lines)


GUIDE = """For each one, decide what to do, the way you would for a teammate:
- Prod the Zip when the failure was passing (it was interrupted or cut off, it timed out, a tool or the connection hiccupped) and it still has the access and context to finish. Give it a board task with kanban_create, assigned to it, that says what is already done and what is left.
- Do it yourself when the Zip has now failed twice on this, or when it needs something you have and it doesn't.
- Ask {first} only when it needs something only {first} has (a sign-in, a decision, money, {first}'s own hands), or when your own try also failed. Ask one question with offer_choices: in a sentence or two say what the work is and what was tried, and give your recommendation as the first answer.
- A Zip whose work stays private keeps it: prod it, and never do its work yourself.
The four things that wait for {first}'s yes still do. When the cause is a fault in the hub or in a Zip's own tools, file it with report_problem; never ask {first} about it. Nothing about this is in {first}'s chats. If you prodded a Zip or finished something {first} didn't ask about and {first} needs nothing, reply with only [SILENT]. When you finish work {first} asked for, tell {first} the result in one line."""


def note(rows: list[sqlite3.Row]) -> str:
    first = who.first()
    head = (f"{HEAD} {'This Zip' if len(rows) == 1 else 'These Zips'} couldn't finish after trying again quietly, so "
            f"the work comes to you as a message between Zips.")
    return "\n\n".join([head, *(_item(i + 1, r) for i, r in enumerate(rows)), GUIDE.format(first=first)])


async def flush(force: bool = False) -> int:
    ""
    from . import waits
    cutoff = time.time() - (0 if force else GATHER_SEC)
    rows = [r for r in store.db().execute("SELECT * FROM handoffs WHERE state='to_zero' AND created<=? ORDER BY id",
                                          (cutoff,)).fetchall() if not _called_off(r)]
    if not rows:
        return 0
    text = note(rows)
    with store._lock:
        c = store.db()
        try:
            c.executemany("UPDATE handoffs SET state='sent', sent_at=? WHERE id=?",
                          [(time.time(), r["id"]) for r in rows])
            waits.queue_wake("default", text)
        except BaseException:
            c.rollback()
            raise
    waits.kick()
    return len(rows)


async def _flush_after(sec: float) -> None:
    await asyncio.sleep(wait_sec(sec))
    await flush()


_tasks: set[asyncio.Task] = set()


def _soon(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)




def retry_note(words: str = "", work: str = "") -> str:
    ""

    first = who.first()
    what = f'{first} asked: "{words}"' if words else f"What you were working on:\n\n{work}"
    return (f"{who.hub_note()} {RETRY_HEAD}, so part of it may already be done. Check what you "
            "did in this chat first, and don't repeat anything you already sent, saved or clicked. Then finish what is "
            f"left and answer as usual. Don't mention the retry. {what}")


def held_chat_note(words: str) -> str:
    first = who.first()
    return (f"{who.hub_note()} {first} sent you this while the connection was down, and it didn't finish then. Check "
            "what you already did in this chat, don't repeat anything you sent, saved or clicked, then do the rest and "
            f'answer {first}: "{words}"')


def clear() -> None:
    ""
    global _outage
    _outage = None
    _net_failures.clear()
