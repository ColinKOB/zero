""





























from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime

from . import config, home, store

log = logging.getLogger("taffy.remindermirror")

ENABLED = False
READ_EVERY_SEC = 10 * 60


UNDO_SEC = 12
OWNER = "default"
_completing: dict[str, object] = {}
_generation = 0


def cancel_reads() -> None:
    ""
    global _generation
    with store._lock:
        _generation += 1
        _completing.clear()


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS reminder_mirror (
        id TEXT PRIMARY KEY,        -- the reminder's id in Reminders (EventKit's calendar item id)
        list TEXT NOT NULL,         -- the id of the list it is in (titles can repeat: Codex on #933)
        list_name TEXT NOT NULL DEFAULT '',   -- that list's title, for the row's gray line only
        title TEXT NOT NULL,
        due TEXT,                   -- as the Mac gave it: 2026-11-12 or 2026-11-12T15:00, in the home's zone
        first_seen REAL NOT NULL,
        read_at REAL NOT NULL       -- the last full read that returned it
    )""")


    c.execute("""CREATE TABLE IF NOT EXISTS reminder_verdicts (
        id TEXT PRIMARY KEY, version TEXT NOT NULL, show INTEGER NOT NULL, look_again REAL NOT NULL, judged_at REAL NOT NULL
    )""")

    if "missed" not in {r[1] for r in c.execute("PRAGMA table_info(reminder_mirror)")}:
        c.execute("ALTER TABLE reminder_mirror ADD COLUMN missed INTEGER NOT NULL DEFAULT 0")


    c.execute("""CREATE TABLE IF NOT EXISTS reminder_replaced (
        id TEXT PRIMARY KEY, by_id TEXT NOT NULL, at REAL NOT NULL
    )""")


def _due_ts(due: str | None) -> float | None:
    if not due:
        return None
    try:
        at = datetime.fromisoformat(str(due).replace(" ", "T"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=home.zone())).timestamp()


def _version(r) -> str:
    from . import work
    return work._revision([r["title"], r["list_name"], r["due"] or ""])


def _end_of_today(now: float | None = None) -> float:
    day = datetime.fromtimestamp(time.time() if now is None else now, home.zone())
    return day.replace(hour=23, minute=59, second=59, microsecond=0).timestamp()


def _shown(r, verdicts: dict, now: float | None = None) -> bool:
    ""

    v = verdicts.get(r["id"])
    if v is not None and v["version"] == _version(r):
        return bool(v["show"])
    due = _due_ts(r["due"])
    return due is not None and due <= _end_of_today(now)


def _verdicts() -> dict:
    return {v["id"]: v for v in store.db().execute("SELECT * FROM reminder_verdicts")}


def _replaced() -> set[str]:
    return {r["id"] for r in store.db().execute("SELECT id FROM reminder_replaced")}


def sources() -> list:
    from . import work
    out = []
    verdicts, replaced = _verdicts(), _replaced()
    for r in store.db().execute("SELECT * FROM reminder_mirror ORDER BY first_seen, id"):
        if r["id"] in replaced or r["missed"] or (not _shown(r, verdicts) and r["id"] not in _completing):
            continue

        out.append(work.Source(f"reminder:{r['id']}", "reminder", r["title"], OWNER, float(r["first_seen"]),
                               {"card": f"reminder:{r['id']}"}, f"In your {r['list_name']} list in Reminders.",
                               "needs_you", date=_due_ts(r["due"]), reference={"reminder": r["id"], "list": r["list"],
                                                                      "list_name": r["list_name"]}))
    return out


MISSES_TO_LEAVE = 2


def save_read(lists: dict[str, tuple[str, list[dict], bool]], present: list[str] | None = None,
              now: float | None = None, generation: int | None = None, started: float | None = None) -> bool:
    ""














    now = time.time() if now is None else now
    fresh = started if started is not None else now + 1
    present = list(lists) if present is None else present
    with store._lock:
        if generation is not None and generation != _generation:
            return False
        c = store.db()
        held = c.execute("SELECT COUNT(*) FROM reminder_mirror").fetchone()[0]
        if held and not any(items for _, items, _ in lists.values()):
            log.info("a Reminders read came back empty while %d were open: nothing changed", held)
            return False
        for list_id, (name, items, cut) in lists.items():
            seen = set()
            for item in items:
                rid = str(item.get("id") or "").strip()
                title = " ".join(str(item.get("title") or "").split())[:300]
                if not rid or not title:
                    continue
                seen.add(rid)
                c.execute("INSERT INTO reminder_mirror (id, list, list_name, title, due, first_seen, read_at, missed) "
                          "VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET list=excluded.list, "
                          "list_name=excluded.list_name, title=excluded.title, due=excluded.due, "
                          "read_at=excluded.read_at, missed=0", (rid, list_id, name, title, item.get("due"), now, now))
            if not cut:
                _miss(c, "list=? AND read_at<?" + (f" AND id NOT IN ({','.join('?' * len(seen))})" if seen else ""),
                      (list_id, fresh, *seen))

        if present:
            _miss(c, f"read_at<? AND list NOT IN ({','.join('?' * len(present))})", (fresh, *present))
        c.execute("DELETE FROM reminder_replaced WHERE id NOT IN (SELECT id FROM reminder_mirror)")
        c.commit()
    return bool(present) and all(k in lists and not lists[k][2] for k in present)


def _miss(c: sqlite3.Connection, where: str, args: tuple) -> None:
    ""
    c.execute(f"UPDATE reminder_mirror SET missed=missed+1 WHERE {where}", args)
    c.execute("DELETE FROM reminder_mirror WHERE missed>=?", (MISSES_TO_LEAVE,))




JUDGE_TIMEOUT_SEC = 60
JUDGE_MAX = 40
LOOK_AGAIN_SEC = 7 * 86400
JUDGE_SYSTEM = "You decide which of a person's reminders belong on their to-do list now. Answer with JSON only."
JUDGE_PROMPT = """Today is {today}. These are {first}'s open reminders from Apple Reminders, with their list and due date when they have one.

For each, say whether it belongs on {first}'s to-do list now: something {first} needs to act on today or in the next week or two, or something overdue. Leave out what isn't {first}'s to do soon: a birthday or anniversary more than about two weeks away, a standing note or reference list, an idea or wish with nothing to do yet, a far-off date. An undated reminder belongs when it reads like a live task.

For one left out, give look_again: the date (YYYY-MM-DD) to judge it again, such as two weeks before a birthday. Use an empty string when no date makes sense.

Reminders:
{items}

Answer with this JSON and nothing else: {{"items": [{{"n": 1, "show": true, "look_again": ""}}]}}, one entry per reminder."""


def _due_words(due: str | None) -> str:
    at = _due_ts(due)
    if at is None:
        return "no due date"
    when = datetime.fromtimestamp(at, home.zone())
    return "due " + when.strftime("%a %b %-d, %Y" + (" at %-I:%M %p" if "T" in str(due) else ""))


def _due_now(r, now: float | None = None) -> bool:
    due = _due_ts(r["due"])
    return due is not None and due <= _end_of_today(now)


async def judge(ask=None, now: float | None = None) -> int:
    ""

    from . import ownerrules, shortreply, who
    now = time.time() if now is None else now
    generation = _generation
    with store._lock:
        verdicts = _verdicts()
        due = [dict(r) for r in store.db().execute("SELECT * FROM reminder_mirror ORDER BY first_seen, id")
               if (v := verdicts.get(r["id"])) is None or v["version"] != _version(r)
               or (not v["show"] and v["look_again"] <= now)][:JUDGE_MAX]
    if not due:
        return 0
    items = "\n".join(f"{n}. {' '.join(r['title'].split())[:200]} (list: {r['list_name'] or 'Reminders'}; "
                      f"{_due_words(r['due'])})" for n, r in enumerate(due, 1))
    today = datetime.fromtimestamp(now, home.zone()).strftime("%A, %B %-d, %Y")
    prompt = JUDGE_PROMPT.format(today=today, first=who.first(), items=items) \
        + ownerrules.block(ownerrules.REMINDER, items, OWNER)
    try:
        with shortreply.reading(()):
            answer = await asyncio.wait_for((ask or shortreply.ask_helper)(
                prompt, timeout=JUDGE_TIMEOUT_SEC, system=JUDGE_SYSTEM, max_tokens=60 * len(due) + 100),
                JUDGE_TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no call on which reminders show (%s)", type(e).__name__)
        return 0
    found = _parse_judgment(answer, len(due))
    if found is None:
        log.info("an unusable call on which reminders show")
        return 0
    with store._lock:
        if generation != _generation:
            return 0
        c = store.db()
        for n, (show, look_again) in found.items():
            r = due[n]
            again = look_again if look_again and look_again > now else now + LOOK_AGAIN_SEC
            c.execute("INSERT INTO reminder_verdicts(id,version,show,look_again,judged_at) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET version=excluded.version,show=excluded.show,"
                      "look_again=excluded.look_again,judged_at=excluded.judged_at",
                      (r["id"], _version(r), int(show), again, now))
        c.execute("DELETE FROM reminder_verdicts WHERE id NOT IN (SELECT id FROM reminder_mirror)")
        c.commit()
    shown = sum(1 for show, _ in found.values() if show)
    log.info("judged %d reminders: %d show, %d left out", len(found), shown, len(found) - shown)
    return len(found)


def _parse_judgment(answer: str | None, count: int) -> dict[int, tuple[bool, float | None]] | None:
    ""
    import json
    import re
    m = re.search(r"\{.*\}", answer or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    entries = data.get("items") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    out = {}
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("n"), int) or not isinstance(e.get("show"), bool) \
                or not 1 <= e["n"] <= count:
            continue
        again = None
        try:
            day = datetime.strptime(str(e.get("look_again") or "")[:10], "%Y-%m-%d")
            again = day.replace(hour=6, tzinfo=home.zone()).timestamp()
        except ValueError:
            pass
        out[e["n"] - 1] = (e["show"], again)
    return out or None


def left_out(limit: int = 20) -> list[str]:
    ""
    with store._lock:
        verdicts = _verdicts()
        rows = [r for r in store.db().execute("SELECT * FROM reminder_mirror ORDER BY first_seen, id")
                if not _shown(r, verdicts)]
    return [f"{r['title']} ({_due_words(r['due'])})" for r in rows[:limit]]


def due_today_ids(now: float | None = None) -> set[str]:
    ""

    from . import todogate
    with store._lock:
        verdicts = _verdicts()
        replaced = _replaced()
        ids = {f"reminder:{r['id']}" for r in store.db().execute("SELECT * FROM reminder_mirror")
               if _shown(r, verdicts, now) and _due_now(r, now) and r["id"] not in replaced and not r["missed"]}
        if not ids:
            return ids
        try:
            return ids - todogate.replaced()
        except Exception:
            log.warning("could not read which reminders a newer one replaced", exc_info=True)
            return ids


RADAR = "reminder_radar"
RADAR_NOTE = ("{possessive} reminder \"{title}\" is due today. Put it on your radar: work out whether you can prepare "
              "it or do part of it, by your usual rules on what needs {first}'s yes. It is already on {possessive} "
              "list, so don't message {first} only to repeat it.")


def radar(now: float | None = None) -> int:
    ""


    from . import notices, who
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, home.zone()).date().isoformat()
    ids = due_today_ids(now)
    with store._lock:
        state = store.get_setting(RADAR, {}) or {}
        if state.get("day") != day:
            state = {"day": day, "heard": [], "brief": []}
        heard, brief = set(state["heard"]), set(state["brief"])
        new = sorted(ids - heard)
        if not new:
            return 0
        try:
            posted = notices.brief_posted_today()
        except Exception:
            posted = True
        noted = 0
        titles = {f"reminder:{r['id']}": r["title"] for r in store.db().execute("SELECT id, title FROM reminder_mirror")}
        for item in new:
            if not posted:
                brief.add(item)
                continue
            heard.add(item)
            if item in brief:
                continue
            store.add_card_note(OWNER, RADAR_NOTE.format(possessive=who.possessive(),
                                                         title=titles.get(item, "")[:200], first=who.first()))
            noted += 1
        store.set_setting(RADAR, {"day": day, "heard": sorted(heard), "brief": sorted(brief)})
    return noted


async def read_once() -> bool:
    ""


    from . import mac
    if not mac.online():
        return False
    generation, started = _generation, time.time()
    try:
        found = await mac.reminders(OWNER, "lists", {})
    except Exception as e:
        log.info("Reminders read skipped: %s", str(e)[:120])
        return False
    entries = [e for e in (found if isinstance(found, list) else []) if isinstance(e, dict) and e.get("id")]
    if not entries:
        return False
    lists: dict[str, tuple[str, list[dict], bool]] = {}
    for entry in entries:


        list_id, name = str(entry["id"]), str(entry.get("title") or "")
        try:
            got = await mac.reminders(OWNER, "due", {"list": list_id})
        except Exception as e:
            log.info("one Reminders list was skipped: %s", str(e)[:120])
            continue
        if not isinstance(got, dict) or not isinstance(got.get("reminders"), list):
            log.info("one Reminders list was skipped: the answer held no list of reminders")
            continue
        lists[list_id] = (name, [r for r in got["reminders"] if isinstance(r, dict)], bool(got.get("more")))
    complete = save_read(lists, present=[str(e["id"]) for e in entries], generation=generation, started=started)
    await _finish_replaced(generation)
    from . import remindertime
    if complete and remindertime.ENABLED:
        await asyncio.to_thread(remindertime.follow_mirror)
    return complete




def _list_id(name: str) -> str | None:
    row = store.db().execute("SELECT list FROM reminder_mirror WHERE list_name=? ORDER BY read_at DESC LIMIT 1",
                             (name,)).fetchone()
    return row["list"] if row else None


def noted_add(item: dict, list_id: str, generation: int | None = None, now: float | None = None) -> str | None:
    ""

    now = time.time() if now is None else now
    rid = str(item.get("id") or "").strip()
    title = " ".join(str(item.get("title") or "").split())[:300]
    if not rid or not title:
        return None
    with store._lock:
        if generation is not None and generation != _generation:
            return None
        c = store.db()
        c.execute("INSERT INTO reminder_mirror (id, list, list_name, title, due, first_seen, read_at) VALUES (?,?,?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET title=excluded.title, due=excluded.due, missed=0",
                  (rid, list_id, str(item.get("list") or ""), title, item.get("due") or None, now, now))
        c.commit()
    return rid


def noted_complete(rid: str) -> None:
    ""
    with store._lock:
        c = store.db()
        c.execute("DELETE FROM reminder_mirror WHERE id=?", (rid,))
        c.execute("DELETE FROM reminder_replaced WHERE id=?", (rid,))
        c.commit()


def noted_replaced(old: str, new: str, now: float | None = None) -> None:
    ""

    now = time.time() if now is None else now
    with store._lock:
        c = store.db()
        c.execute("INSERT OR REPLACE INTO reminder_replaced (id, by_id, at) VALUES (?,?,?)", (old, new, now))
        verdicts = _verdicts()
        before = c.execute("SELECT * FROM reminder_mirror WHERE id=?", (old,)).fetchone()
        after = c.execute("SELECT * FROM reminder_mirror WHERE id=?", (new,)).fetchone()
        if before and after and _shown(before, verdicts, now):
            c.execute("INSERT INTO reminder_verdicts(id,version,show,look_again,judged_at) VALUES(?,?,1,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET version=excluded.version,show=1,look_again=excluded.look_again,"
                      "judged_at=excluded.judged_at", (new, _version(after), now + LOOK_AGAIN_SEC, now))
        c.commit()


async def _finish_replaced(generation: int) -> None:
    ""
    from . import mac
    with store._lock:
        waiting = [r["id"] for r in store.db().execute(
            "SELECT id FROM reminder_replaced WHERE id IN (SELECT id FROM reminder_mirror)")]
    for rid in waiting:
        if generation != _generation:
            return
        try:
            await mac.reminders(OWNER, "complete", {"id": rid})
        except Exception as e:
            log.info("a replaced reminder is still open (%s)", type(e).__name__)
            continue
        noted_complete(rid)


async def relay(bot_id: str, action: str, args: dict, in_turn: bool) -> object:
    ""





    from . import donecard, mac, remindertime
    generation = _generation
    old = str(args.get("replaces") or "").strip() if action == "add" else ""
    old = old.removeprefix("reminder:")
    was = None
    if old and in_turn:
        try:
            was = await asyncio.to_thread(donecard.row_now, f"reminder:{old}")
        except Exception:
            log.warning("could not read a reminder's row before it was replaced", exc_info=True)
    result = await mac.reminders(bot_id, action, args)
    item = result if isinstance(result, dict) else {}
    if action == "complete" and item.get("completed"):
        noted_complete(str(item.get("id") or args.get("id") or ""))
        if remindertime.ENABLED:
            await asyncio.to_thread(remindertime.follow_mirror)
    if action != "add" or not item.get("id"):
        return result
    name = str(item.get("list") or "")
    with store._lock:
        list_id = _list_id(name)
        if not list_id and old:
            row = store.db().execute("SELECT list, list_name FROM reminder_mirror WHERE id=?", (old,)).fetchone()
            list_id = row["list"] if row and (not name or row["list_name"] == name) else None
    if not list_id:
        try:
            found = await mac.reminders(OWNER, "lists", {})
            list_id = next((str(e["id"]) for e in found if isinstance(e, dict) and e.get("id")
                            and (e.get("title") == name or (not name and e.get("default")))), None) \
                if isinstance(found, list) else None
        except Exception as e:
            log.info("could not read the lists after an add (%s)", type(e).__name__)
    new = noted_add(item, list_id or f"title:{name}", generation)
    if not new:
        return result
    if old and old != new:
        noted_replaced(old, new)
        try:
            await asyncio.to_thread(remindertime.cancel_reminder, old)
        except Exception:
            log.warning("could not cancel a replaced reminder's delivery", exc_info=True)
        try:
            done = await mac.reminders(OWNER, "complete", {"id": old})
            if isinstance(done, dict) and done.get("completed"):
                noted_complete(old)
        except Exception as e:
            log.info("a replaced reminder waits for its check-off (%s)", type(e).__name__)
        result = {**item, "replaced": old}
        if was:
            donecard.changed(bot_id, f"reminder:{new}", was, in_turn)
    else:
        judge_soon()
    return result


def judge_soon() -> None:
    ""
    if not ENABLED:
        return

    async def go() -> None:
        try:
            if await judge():
                from . import work
                await asyncio.to_thread(work.rebuild)
        except Exception:
            log.exception("judging a new reminder failed")
    try:
        asyncio.get_running_loop().create_task(go())
    except RuntimeError:
        pass


def complete_soon(item_id: str) -> None:
    ""



    from . import signin
    rid = str(item_id).removeprefix("reminder:")
    token = object()
    _completing[rid] = token
    try:
        asyncio.run_coroutine_threadsafe(_complete(rid, token), signin.loop)
    except Exception:
        log.warning("could not send a reminder's Done to the Mac", exc_info=True)
        _not_done(rid)


def cancel(item_id: str) -> None:
    ""
    _completing.pop(str(item_id).removeprefix("reminder:"), None)


def _not_done(rid: str) -> None:
    ""
    _completing.pop(rid, None)
    with store._lock:
        c = store.db()
        c.execute("UPDATE work_user_state SET done=0, handled_revision=NULL, revision=revision+1 WHERE id=?",
                  (f"reminder:{rid}",))
        c.commit()


async def _wait_out(rid: str) -> None:
    ""
    await asyncio.sleep(UNDO_SEC)


async def _complete(rid: str, token: object) -> None:
    from . import mac
    await _wait_out(rid)
    if _completing.get(rid) is not token:
        return
    try:
        await mac.reminders(OWNER, "complete", {"id": rid})
    except Exception as e:


        log.info("could not check off a reminder (%s): %s", type(e).__name__, str(e)[:120])
        if _completing.get(rid) is token:
            _not_done(rid)
        return
    with store._lock:
        store.db().execute("DELETE FROM reminder_mirror WHERE id=?", (rid,))
        store.db().commit()
    if _completing.get(rid) is token:
        _completing.pop(rid, None)


def clear_stale_done() -> int:
    ""


    from . import autofinish
    with store._lock:
        c = store.db()
        waiting = [f"reminder:{rid}" for rid in _completing]


        waiting += [r["id"] for r in c.execute("SELECT id FROM work_user_state WHERE id LIKE 'reminder:%' AND done=1")
                    if autofinish.by_itself(r["id"])]
        cleared = c.execute(f"UPDATE work_user_state SET done=0, handled_revision=NULL, revision=revision+1 "
                            f"WHERE id LIKE 'reminder:%' AND done=1 AND id NOT IN ({','.join('?' * len(waiting)) or 'NULL'})"
                            if waiting else "UPDATE work_user_state SET done=0, handled_revision=NULL, revision=revision+1 "
                            "WHERE id LIKE 'reminder:%' AND done=1", waiting).rowcount
        c.commit()
    return cleared


async def run() -> None:
    clear_stale_done()
    while True:
        if ENABLED and not config.is_sandbox(OWNER):
            try:
                await read_once()
            except Exception:
                log.exception("the Reminders read failed")
            try:
                from . import remindertime
                if remindertime.ENABLED:
                    await asyncio.to_thread(remindertime.settle)
            except Exception:
                log.exception("settling the reminder deliveries failed")
            try:
                if await judge():
                    from . import work
                    work.rebuild()
                radar()
            except Exception:
                log.exception("judging the reminders failed")
        await asyncio.sleep(READ_EVERY_SEC)
