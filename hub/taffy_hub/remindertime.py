""










































from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime

from . import config, home, ownerrules, store, who

log = logging.getLogger("taffy.remindertime")

ENABLED = False
NO_DAY_TIME = (9, 0)
SAME_TIME_SEC = 30 * 60
CLI_TIMEOUT_SEC = 120
NAME_PREFIX = "Reminder: "
CHECK_TIMEOUT_SEC = 45




LATE_SEC = 2 * 3600
STOPPED_AFTER_SEC = 15 * 60
THIS_TURN_SEC = 10 * 60
TWIN_SEC = 5 * 60


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS reminder_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id TEXT NOT NULL,
        what TEXT NOT NULL,           -- what to remind the person of, in their words
        at REAL NOT NULL,             -- when
        reminder_id TEXT,             -- the Apple Reminder it keeps, when it came from one
        job_id TEXT,                  -- the Hermes one-time job that delivers it
        state TEXT NOT NULL,          -- scheduled | delivered | dropped | failed | cancelled | stopped
        source TEXT NOT NULL,         -- reminder (the reminders tool) | promise (a reply)
        seen INTEGER NOT NULL DEFAULT 0,   -- the reminders mirror has had the reminder in a read
        created_at REAL NOT NULL,
        changed_at REAL NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS reminder_deliveries_job ON reminder_deliveries(job_id)")




def _hermes(bot_id: str, *args: str) -> tuple[bool, str]:
    ""
    cmd = [str(config.HERMES_BIN)] + ([] if bot_id == "default" else ["-p", bot_id]) + ["cron", *args]
    try:
        done = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=CLI_TIMEOUT_SEC,
                              env=dict(os.environ, HERMES_HOME=str(config.HERMES_HOME), NO_COLOR="1"))
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("hermes cron %s for %s failed: %s", args[:1], bot_id, type(e).__name__)
        return False, ""
    out = re.sub(r"\x1b\[[0-9;]*m", "", (done.stdout or "") + (done.stderr or ""))
    return done.returncode == 0, out


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, home.zone()).isoformat(timespec="minutes")


def said_when(at: float) -> str:
    ""
    return datetime.fromtimestamp(at, home.zone()).strftime("%a %b %-d at %-I:%M %p").replace(":00 ", " ")


def _clock(at: float) -> str:
    d = datetime.fromtimestamp(at, home.zone())
    return d.strftime("%-I:%M %p").replace(":00 ", " ").replace("AM", "a.m.").replace("PM", "p.m.")


def prompt(what: str, at: float, late_from: float | None = None) -> str:
    ""

    first = who.first()
    if late_from is not None:
        return (f"It's {_clock(at)}. Earlier {first} asked you to remind them about this at {_clock(late_from)}, and "
                f"that reminder didn't go out on time: {what}\n\nRemind {first} now in one or two short sentences, in "
                f"your own words, as a message in your chat. Say plainly that this is late, then name the thing, and if "
                f"it fits, offer to help with it right now. Don't mention this job, the Reminders app, or why it was "
                f"late. If what you can see shows {first} already did it, reply with only [SILENT].")
    return (f"It's {_clock(at)}. Earlier {first} asked you to remind them about this at this time: {what}\n\n"
            f"Remind {first} now in one or two short sentences, in your own words, as a message in your chat: name the "
            f"thing plainly, and if it fits, offer to help with it right now. Don't mention this job, the Reminders "
            f"app, or how you were woken. If what you can see shows {first} already did it, reply with only [SILENT].")


def _create(bot_id: str, what: str, at: float, late_from: float | None = None) -> str | None:
    ok, out = _hermes(bot_id, "create", "--name", (NAME_PREFIX + what)[:120], "--repeat", "1", "--deliver", "local",
                      _iso(at), prompt(what, at, late_from))
    m = re.search(r"Created job:\s*(\S+)", out)
    return m.group(1) if ok and m else None




def _rows(where: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in store.db().execute(f"SELECT * FROM reminder_deliveries WHERE {where}", args)]


def schedule(bot_id: str, what: str, at: float, reminder_id: str | None = None, source: str = "reminder",
             late: bool = False) -> dict | None:
    ""


    what = " ".join(str(what or "").split())[:300]
    late_from = None
    if late and time.time() - LATE_SEC <= at <= time.time() + 60:
        late_from, at = at, time.time() + 60
    if not what or at <= time.time() or config.is_sandbox(bot_id):
        return None
    with store._lock:

        same = _rows("state='scheduled' AND bot_id=? AND reminder_id=?", (bot_id, reminder_id)) if reminder_id else \
            _rows("state='scheduled' AND bot_id=? AND what=? AND ABS(at-?)<?", (bot_id, what, at, SAME_TIME_SEC))
    if same:
        return same[0]
    job = _create(bot_id, what, at, late_from)
    now = time.time()
    with store._lock:
        c = store.db()
        rid = c.execute("INSERT INTO reminder_deliveries (bot_id, what, at, reminder_id, job_id, state, source, "
                        "created_at, changed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (bot_id, what, at, reminder_id, job, "scheduled" if job else "failed", source, now, now)).lastrowid
        c.commit()
    if not job:
        log.warning("could not schedule %s's reminder delivery for %s", bot_id, _iso(at))
        return None
    log.info("scheduled %s's reminder delivery %s for %s (%s)", bot_id, rid, _iso(at), source)
    return _rows("id=?", (rid,))[0]


def due_of(text: str | None) -> float | None:
    ""

    if not text or "T" not in str(text).replace(" ", "T"):
        return None
    try:
        at = datetime.fromisoformat(str(text).replace(" ", "T"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=home.zone())).timestamp()


def after_add(bot_id: str, args: dict, result: object) -> dict | None:
    ""
    try:
        res = result if isinstance(result, dict) else {}
        at = due_of(str(args.get("due") or ""))
        title = str(res.get("title") or args.get("title") or "")
        if at is None or not title:
            return None
        return schedule(bot_id, title, at, reminder_id=str(res.get("id") or "") or None, source="reminder")
    except Exception:
        log.exception("could not schedule the delivery of %s's reminder", bot_id)
        return None




def _reads() -> None:
    ""
    from . import remindermirror, workstate


def _done_in_work(reminder_id: str) -> bool:
    _reads()
    r = store.db().execute("SELECT done FROM work_user_state WHERE id=?", (f"reminder:{reminder_id}",)).fetchone()
    return bool(r and r["done"])


def follow_mirror(now: float | None = None) -> int:
    ""

    _reads()
    now = time.time() if now is None else now
    with store._lock:
        rows = _rows("state='scheduled' AND reminder_id IS NOT NULL AND at>?", (now,))
        mirror = {r["id"]: r["due"] for r in store.db().execute(
            f"SELECT id, due FROM reminder_mirror WHERE id IN ({','.join('?' * len(rows))})",
            [r["reminder_id"] for r in rows])} if rows else {}
    changed = 0
    for row in rows:
        rid = row["reminder_id"]
        if rid in mirror and not _done_in_work(rid):
            moved = due_of(mirror[rid])
            if not row["seen"]:
                _set(row["id"], seen=1)
            if moved and abs(moved - row["at"]) >= 60 and moved > now:
                ok, _ = _hermes(row["bot_id"], "edit", "--schedule", _iso(moved), "--prompt", prompt(row["what"], moved),
                                row["job_id"])
                if ok:
                    _set(row["id"], at=moved)
                    changed += 1
            continue
        if rid not in mirror and not row["seen"]:
            continue
        if _hermes(row["bot_id"], "remove", row["job_id"])[0]:
            _set(row["id"], state="dropped")
            changed += 1
    return changed + revive(now)


def revive(now: float | None = None) -> int:
    ""



    _reads()
    now = time.time() if now is None else now
    with store._lock:
        rows = _rows("state='dropped' AND reminder_id IS NOT NULL ORDER BY id")
        mirror = {r["id"]: dict(r) for r in store.db().execute("SELECT id, due, missed FROM reminder_mirror")}
        replaced = {r["id"] for r in store.db().execute("SELECT id FROM reminder_replaced")}
    put_back = 0
    for row in rows:
        rid, open_now = row["reminder_id"], mirror.get(row["reminder_id"])
        if not open_now or open_now["missed"] or rid in replaced or _done_in_work(rid):
            continue
        with store._lock:
            if _rows("reminder_id=? AND id>? AND state!='failed'", (rid, row["id"])):
                continue
        at = due_of(open_now["due"]) or row["at"]
        if at <= now - LATE_SEC:
            continue
        if schedule(row["bot_id"], row["what"], at, rid, row["source"], late=True):
            put_back += 1
            log.info("put back %s's delivery %s for a reminder still open%s", row["bot_id"], row["id"],
                     " (late)" if at <= now else "")
    return put_back


def settle(now: float | None = None) -> int:
    ""

    from . import jobretry, watcher
    now = time.time() if now is None else now
    with store._lock:
        rows = _rows("state='scheduled' AND job_id IS NOT NULL AND at<?", (now - STOPPED_AFTER_SEC,))
    if not rows:
        return 0
    jobs = {}
    for owner, folder in watcher._cron_dirs():
        if any(r["bot_id"] == owner for r in rows):
            jobs[owner] = watcher._jobs_of(folder.parent / "jobs.json")
    stopped = 0
    for row in rows:
        known = jobs.get(row["bot_id"])
        if not known:
            continue
        job = known.get(row["job_id"])
        if job is not None and not jobretry._off(job):
            continue
        _set(row["id"], state="stopped")
        stopped += 1
    if stopped:
        log.info("settled %d deliveries whose job its Zip turned off", stopped)
    return stopped


def _set(row_id: int, **values) -> None:
    with store._lock:
        c = store.db()
        c.execute(f"UPDATE reminder_deliveries SET {', '.join(f'{k}=?' for k in values)}, changed_at=? WHERE id=?",
                  (*values.values(), time.time(), row_id))
        c.commit()


def cancel(row_id: int) -> bool:
    ""


    with store._lock:
        rows = _rows("id=? AND state='scheduled'", (row_id,))
    if not rows:
        return False
    row = rows[0]
    if row["job_id"] and not _hermes(row["bot_id"], "remove", row["job_id"])[0]:
        log.warning("could not remove the job of %s's delivery %s; it is cancelled and will say nothing",
                    row["bot_id"], row_id)
    _set(row_id, state="cancelled")
    log.info("cancelled %s's delivery %s for %s (%s)", row["bot_id"], row_id, _iso(row["at"]), row["source"])
    return True


def cancel_reminder(reminder_id: str) -> int:
    ""


    with store._lock:
        rows = _rows("state='scheduled' AND reminder_id=?", (reminder_id,))
    return sum(1 for r in rows if cancel(r["id"]))


def delivering(bot_id: str, job_id: str) -> bool | None:
    ""


    _reads()
    with store._lock:
        rows = _rows("bot_id=? AND job_id=?", (bot_id, job_id))
        if not rows:
            return None
        row = rows[-1]
        gone = row["reminder_id"] and (_done_in_work(row["reminder_id"]) or (row["seen"] and not store.db().execute(
            "SELECT 1 FROM reminder_mirror WHERE id=?", (row["reminder_id"],)).fetchone()))
        keep = row["state"] == "scheduled" and not gone
    _set(row["id"], state="delivered" if keep else "dropped")
    return keep




CHECK_SYSTEM = "You read a helper's reply to the person it works for. Answer with JSON only."
CHECK_PROMPT = """It is {now} where {first} lives ({zone}, UTC{offset} today). This is a helper's reply to {first}:

<reply>
{reply}
</reply>

Does the reply promise that the helper will remind {first} of something, or tell or message {first} about something, at a particular time or on a particular day? Count only a promise to reach {first} later: "I'll remind you tomorrow at 10", "I'll ping you Friday morning", "I'll let you know at 3 p.m. what it says". Leave out a reminder {first} sets or is told to set, a promise with no day or time ("I'll let you know when it comes"), something to do now, and a promise to someone else.

For each promise, give what to remind {first} of, in {first}'s terms, so it makes sense on its own ("tell Zero about the bike-share idea from last week"), and when: YYYY-MM-DDTHH:MM in {first}'s own clock time, exactly as the reply says it, with no zone or offset; YYYY-MM-DD only when the reply names a day and no time.{rules}

Answer with this JSON and nothing else: {{"promises": [{{"what": "...", "at": "..."}}]}}, or {{"promises": []}}."""


def _when(text: str, now: float) -> float | None:
    ""

    text = str(text or "").strip().replace(" ", "T")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            d = datetime.fromisoformat(text).replace(hour=NO_DAY_TIME[0], minute=NO_DAY_TIME[1], tzinfo=home.zone())
        else:
            d = datetime.fromisoformat(re.sub(r"(?:Z|[+-]\d{2}:?\d{2})$", "", text)).replace(tzinfo=home.zone())
    except ValueError:
        return None
    at = d.timestamp()
    return at if now < at < now + 400 * 86400 else None


def _day_of(at: float) -> str:
    return datetime.fromtimestamp(at, home.zone()).date().isoformat()


def _near(bot_id: str, at: float, now: float | None = None) -> list[dict]:
    ""


    now = time.time() if now is None else now
    with store._lock:
        out = [{"what": r["what"], "at": r["at"]}
               for r in _rows("state='scheduled' AND bot_id=? AND (ABS(at-?)<? OR created_at>=?)",
                              (bot_id, at, SAME_TIME_SEC, now - THIS_TURN_SEC))
               if abs(r["at"] - at) < SAME_TIME_SEC or _day_of(r["at"]) == _day_of(at)]
    from . import watcher
    for owner, folder in watcher._cron_dirs():
        if owner != bot_id:
            continue
        for job in watcher._jobs_of(folder.parent / "jobs.json").values():
            if not job.get("enabled", True):
                continue
            try:
                nxt = datetime.fromisoformat(str(job.get("next_run_at") or job.get("schedule", {}).get("run_at") or ""))
            except (TypeError, ValueError, AttributeError):
                continue
            if abs(nxt.timestamp() - at) < SAME_TIME_SEC:
                words = " ".join(str(job.get(k) or "") for k in ("name", "prompt")).split()
                out.append({"what": " ".join(words)[:400], "at": nxt.timestamp()})
    return out


MATCH_PROMPT = """A helper promised to remind {first} of this at {when}: {what}

These are already scheduled to run for {first} around then:
{near}

Does one of them deliver this same reminder, the same thing to the same person? A different check, report or reminder that only runs at a nearby time does not count. One that names the same thing in other words, even at a slightly different time that day, does count.{rules}

Answer with this JSON and nothing else: {{"same": 1}} with its number, or {{"same": 0}} when none does."""


async def _kept(bot_id: str, what: str, at: float, ask, now: float | None = None) -> bool:
    ""



    from . import shortreply
    near = _near(bot_id, at, now)
    if not near:
        return False
    first = who.first()
    text = MATCH_PROMPT.format(first=first, when=_clock(at), what=what, near="\n".join(
        f"{n}. at {_clock(x['at'])}: {x['what']}" for n, x in enumerate(near, 1)),
        rules=ownerrules.block(ownerrules.FOLLOWUP, what, bot_id, answered=False))
    try:
        if ask is None:
            from . import shortreply
            with shortreply.reading_text(text, (bot_id,)):
                answer = await shortreply.ask_helper(text, system=CHECK_SYSTEM, timeout=CHECK_TIMEOUT_SEC)
        else:
            with shortreply.reading_text(text, (bot_id,)):
                answer = await ask(text)
        same = json.loads(re.search(r"\{.*\}", answer or "", re.S).group(0)).get("same")
    except Exception:
        log.warning("could not tell whether %s's promise is already kept", bot_id, exc_info=True)
        return False
    return isinstance(same, int) and not isinstance(same, bool) and 1 <= same <= len(near)


async def check_reply(bot_id: str, reply: str, ask=None, now: float | None = None) -> list[dict]:
    ""

    from . import shortreply
    from . import factkeeper
    if shortreply.withheld(bot_id) or config.is_sandbox(bot_id) or not (reply or "").strip():
        return []
    now = time.time() if now is None else now
    first = who.first()
    here = datetime.fromtimestamp(now, home.zone())
    text = CHECK_PROMPT.format(now=here.strftime("%A %Y-%m-%d %H:%M"), first=first, offset=here.strftime("%z")[:3] + ":"
                               + here.strftime("%z")[3:], zone=str(home.zone()), reply=reply[:4000],
                               rules=ownerrules.block(ownerrules.FOLLOWUP, reply[:400], bot_id, answered=False))
    try:
        if ask is None:
            from . import shortreply
            with shortreply.reading_text(text, (bot_id,)):
                answer = await shortreply.ask_helper(text, system=CHECK_SYSTEM, timeout=CHECK_TIMEOUT_SEC)
        else:
            with shortreply.reading_text(text, (bot_id,)):
                answer = await ask(text)
        found = json.loads(re.search(r"\{.*\}", answer or "", re.S).group(0)).get("promises") or []
    except Exception:
        log.warning("could not read %s's reply for a promised time", bot_id, exc_info=True)
        return []
    made = []
    for p in found if isinstance(found, list) else []:
        if not isinstance(p, dict):
            continue
        at = _when(p.get("at"), now)
        what = " ".join(str(p.get("what") or "").split())
        if at is None or not what or await _kept(bot_id, what, at, ask, now):
            continue
        row = await asyncio.to_thread(schedule, bot_id, what, at, None, "promise")
        if row:
            made.append(row)
    return made


def check_later(bot_id: str, reply: str) -> None:
    ""
    if not ENABLED:
        return
    try:
        asyncio.get_running_loop().create_task(check_reply(bot_id, reply))
    except RuntimeError:
        pass




async def drop_twins(bot_id: str | None = None, ask=None, now: float | None = None) -> int:
    ""




    from . import shortreply, todogate
    now = time.time() if now is None else now
    try:
        with store._lock:
            promises = _rows("state='scheduled' AND source='promise' AND at>?" + (" AND bot_id=?" if bot_id else ""),
                             (now, bot_id) if bot_id else (now,))
            pairs = [(p, _rows("state='scheduled' AND source='reminder' AND bot_id=? AND ABS(created_at-?)<?",
                               (p["bot_id"], p["created_at"], TWIN_SEC))) for p in promises]
    except Exception:
        log.warning("could not read the deliveries to compare", exc_info=True)
        return 0
    cancelled = 0
    for promise, reminders in pairs:
        if not reminders or shortreply.withheld(promise["bot_id"]):
            continue
        item = {"id": f"delivery:{promise['id']}", "kind": "reminder", "title": promise["what"],
                "summary": f"at {said_when(promise['at'])}", "bot_id": promise["bot_id"]}
        others = [{"id": f"delivery:{r['id']}", "kind": "reminder", "title": f"{r['what']} (at {said_when(r['at'])})",
                   "bot_id": r["bot_id"]} for r in reminders]
        try:
            same = await todogate.compare({"item": item, "others": others}, ask=ask)
        except Exception:
            log.warning("could not compare %s's promise delivery %s", promise["bot_id"], promise["id"], exc_info=True)
            continue
        if same and await asyncio.to_thread(cancel, promise["id"]):
            cancelled += 1
    if cancelled:
        log.info("cancelled %d promise deliveries a reminder already keeps", cancelled)
    return cancelled


def drop_twins_later(bot_id: str) -> None:
    ""
    if not ENABLED:
        return
    try:
        asyncio.get_running_loop().create_task(drop_twins(bot_id))
    except RuntimeError:
        pass




def added(bot_id: str, since: float) -> list[dict]:
    ""

    from . import lineage
    path = lineage._state_db(bot_id)
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            rows = conn.execute("SELECT m.timestamp, m.tool_calls FROM messages m WHERE m.tool_calls LIKE '%reminders%' "
                                "AND m.timestamp >= ? ORDER BY m.timestamp", (since,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out = []
    for at, calls in rows:
        try:
            calls = json.loads(calls)
        except (TypeError, ValueError):
            continue
        for call in calls if isinstance(calls, list) else []:
            f = (call.get("function") or call) if isinstance(call, dict) else {}
            if not isinstance(f, dict) or f.get("name") != "reminders":
                continue
            try:
                args = json.loads(f.get("arguments") or "{}") if isinstance(f.get("arguments"), str) else f.get("arguments")
            except ValueError:
                continue
            if isinstance(args, dict) and args.get("action") == "add" and args.get("title"):
                out.append({"title": str(args["title"]), "due": str(args.get("due") or ""), "at": float(at or 0)})
    return out


def backfill(days: int = 30, now: float | None = None) -> int:
    ""


    now = time.time() if now is None else now
    made = 0
    for bot_id in list(config.BOTS_BY_ID):
        if config.is_sandbox(bot_id):
            continue
        for r in added(bot_id, now - days * 86400):
            at = due_of(r["due"])
            if not at or at <= now + 60:
                continue
            with store._lock:
                match = store.db().execute("SELECT id FROM reminder_mirror WHERE title=? AND due=?",
                                           (" ".join(r["title"].split())[:300], r["due"])).fetchone()
                mirror_read = store.db().execute("SELECT max(read_at) FROM reminder_mirror").fetchone()[0] or 0
            if not match and mirror_read > r["at"]:
                continue
            if match and _done_in_work(match["id"]):
                continue
            if schedule(bot_id, r["title"], at, reminder_id=match["id"] if match else None, source="reminder"):
                made += 1
    return made


def upcoming(days: int = 7) -> list[dict]:
    ""
    return _rows("state='scheduled' AND at<? ORDER BY at", (time.time() + days * 86400,))


