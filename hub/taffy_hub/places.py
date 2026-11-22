""


























from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

from . import config, store, who

log = logging.getLogger("taffy.places")

RADIUS = 150
RADIUS_MIN, RADIUS_MAX = 100, 2000
EVENTS = ("arrive", "leave")
PER_ZIP = 40
REPEAT_GAP_SEC = 30 * 60
WHAT_MAX, PLACE_MAX, LABEL_MAX = 300, 200, 200


DEVICE_SETTING = "places_device"
PERMISSIONS = ("always", "while_using", "denied", "restricted", "not_asked")


class Invalid(ValueError):
    ""


def now() -> float:
    return time.time()


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS place_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, what TEXT NOT NULL, place TEXT NOT NULL,
            event TEXT NOT NULL, every INTEGER NOT NULL DEFAULT 0, radius REAL NOT NULL,
            lat REAL, lon REAL, label TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'open',
            created REAL NOT NULL, fired_at REAL, fires INTEGER NOT NULL DEFAULT 0
        );
    """)


    if "owed" not in {r[1] for r in c.execute("PRAGMA table_info(place_reminders)")}:
        c.execute("ALTER TABLE place_reminders ADD COLUMN owed REAL")


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _row(r) -> dict:
    d = dict(r)
    d["every"] = bool(d["every"])
    return d


def get(rid: int) -> dict | None:
    r = store.db().execute("SELECT * FROM place_reminders WHERE id=?", (int(rid),)).fetchone()
    return _row(r) if r else None


def listed(bot_id: str | None = None) -> list[dict]:
    ""
    q = "SELECT * FROM place_reminders WHERE state IN ('open','unfound')" + (" AND bot_id=?" if bot_id else "")
    return [_row(r) for r in store.db().execute(q + " ORDER BY id", (bot_id,) if bot_id else ())]


def add(bot_id: str, what: str, place: str, event: str = "arrive", every: bool = False,
        radius: float | None = None) -> dict:
    what, place = _clean(what, WHAT_MAX), _clean(place, PLACE_MAX)
    if not what or not place:
        raise Invalid("Say what to remind about (what) and the place (place), with the town, such as 'Riverside "
                      "Library, Springfield, IL'.")
    if event not in EVENTS:
        raise Invalid("on must be arrive or leave.")
    if bot_id not in config.BOTS_BY_ID:
        raise Invalid("unknown Zip")
    if config.is_sandbox(bot_id):
        raise Invalid(f"Test Zips can't set reminders on {who.possessive()} iPhone.")
    try:
        r = float(radius) if radius not in (None, "") else RADIUS
    except (TypeError, ValueError):
        raise Invalid(f"radius is in meters, {RADIUS_MIN} to {RADIUS_MAX}.")
    r = max(RADIUS_MIN, min(RADIUS_MAX, r))
    with store._lock:
        c = store.db()
        if c.execute("SELECT COUNT(*) FROM place_reminders WHERE bot_id=? AND state IN ('open','unfound')",
                     (bot_id,)).fetchone()[0] >= PER_ZIP:
            raise Invalid(f"You have {PER_ZIP} place reminders already. Cancel one that's done first.")
        cur = c.execute("INSERT INTO place_reminders (bot_id, what, place, event, every, radius, created) "
                        "VALUES (?,?,?,?,?,?,?)", (bot_id, what, place, event, int(bool(every)), r, now()))
        c.commit()
    return get(cur.lastrowid)


def cancel(bot_id: str, rid: int) -> bool:
    with store._lock:
        c = store.db()

        n = c.execute("UPDATE place_reminders SET state='cancelled' WHERE id=? AND bot_id=? AND (state IN "
                      "('open','unfound') OR (state='done' AND owed IS NOT NULL))", (int(rid), bot_id)).rowcount
        c.commit()
    return n > 0




def report(permission: str) -> None:
    ""
    if permission in PERMISSIONS:
        store.set_setting(DEVICE_SETTING, {"permission": permission, "at": now()})


def permission() -> str | None:
    return (store.get_setting(DEVICE_SETTING, None) or {}).get("permission")


def for_device() -> list[dict]:
    ""
    return [{"id": d["id"], "place": d["place"], "event": d["event"], "radius": d["radius"], "lat": d["lat"],
             "lon": d["lon"], "label": d["label"]} for d in listed() if d["state"] == "open"]


def spot(rid: int, lat: float | None, lon: float | None, label: str = "") -> dict | None:
    ""

    with store._lock:
        c = store.db()
        if lat is None or lon is None:
            n = c.execute("UPDATE place_reminders SET state='unfound' WHERE id=? AND state='open' AND lat IS NULL",
                          (int(rid),)).rowcount
        else:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise Invalid("That spot is not on the map.")
            n = c.execute("UPDATE place_reminders SET lat=?, lon=?, label=? WHERE id=? AND state='open' AND lat IS NULL",
                          (lat, lon, _clean(label, LABEL_MAX), int(rid))).rowcount
        c.commit()
    d = get(rid)
    if n and d and d["state"] == "unfound":
        _unfound(d)
    return d


def _unfound(d: dict) -> None:
    ""
    from . import waits
    first = who.first()
    waits.queue_wake(d["bot_id"], f"{who.hub_note()} The iPhone couldn't find \"{d['place']}\" on the map for "
                                  f"{who.possessive()} place reminder (\"{d['what']}\"), so it can't remind {first} "
                                  f"there yet. Ask {first} for the address in one short sentence, then set the reminder "
                                  "again with it.")
    waits.kick()


def place_name(d: dict) -> str:
    return d["label"] or d["place"]


def teller(d: dict) -> str:
    ""
    return d["bot_id"] if config.is_private(d["bot_id"]) else "default"


def arrival_note(d: dict) -> str:
    ""
    first, where = who.first(), place_name(d)
    did = f"just got to {where}" if d["event"] == "arrive" else f"just left {where}"
    setter = "" if teller(d) == d["bot_id"] else f" {config.BOTS_BY_ID[d['bot_id']].name} set this reminder."
    once = "" if d["every"] else " It was a one-time reminder, so it is finished after this."
    return (f"{who.hub_note()} {who.possessive()} iPhone says {first} {did}.{setter} The reminder {first} asked for: "
            f'"{d["what"]}".{once}\n\n'
            f"Tell {first} now, in your own words, in one or two short sentences: the thing to get or do there, with "
            f"the list when there is one. Then check your memory, the team notes and {who.possessive()} to-do list for "
            f"anything else {first} said they want that they could get or do at {where}. If there is something, offer "
            "it as one question, such as \"You also wanted a gift card for Tom. Want to grab that too?\" Don't explain "
            "how you knew where they are, and send nothing to anyone." + _mark(d))


def _mark(d: dict) -> str:
    ""
    return f"\n[Place reminder {d['id']} owed {d['owed']:.0f}]" if d.get("owed") else ""


def _claim(rid: int, event: str, at: float | None = None) -> dict | None:
    ""


    t = now()
    seen = min(t, at) if at is not None else t
    with store._lock:
        c = store.db()
        n = c.execute("UPDATE place_reminders SET fires=fires+1, fired_at=?, owed=?, state=CASE WHEN every=1 THEN "
                      "state ELSE 'done' END WHERE id=? AND event=? AND state='open' AND lat IS NOT NULL "
                      "AND (fired_at IS NULL OR fired_at<=?)", (t, t, int(rid), event, seen - REPEAT_GAP_SEC)).rowcount
        c.commit()
    return get(rid) if n else None


async def crossed(rid: int, event: str, at: float | None = None) -> bool:
    ""
    if event not in EVENTS:
        raise Invalid("event must be arrive or leave.")
    d = _claim(rid, event, at)
    if d is None:
        return False
    if d["bot_id"] not in config.BOTS_BY_ID:
        return False

    task = asyncio.get_running_loop().create_task(remind(d))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return True


_pending: set[asyncio.Task] = set()
TRIES = 2


async def remind(d: dict) -> bool:
    ""


    from . import choices, turns
    who_tells = teller(d)
    began = time.time()
    for _ in range(TRIES):
        if not still_wanted(d) or turns._stopped_since(who_tells, began):
            _paid(d)
            return False
        since = time.time()
        try:
            if await turns.wake(who_tells, arrival_note(d), urgent=True, now=True) \
                    or choices.pending_ask(who_tells, since):
                _paid(d)
                if who_tells != d["bot_id"]:
                    store.add_card_note(d["bot_id"], f"Your place reminder for {who.first()} at {place_name(d)} went "
                                        "off, and Zero told them." + ("" if d["every"] else " It is finished."),
                                        store.REPORT_NOTES, store.REPORT_NOTES_MAX)
                return True
        except Exception:
            log.exception("could not tell %s's place reminder", who_tells)
    if turns._stopped_since(who_tells, began):
        _paid(d)
        return False
    with store._lock:
        store.db().execute("UPDATE place_reminders SET state=CASE WHEN state='cancelled' THEN state ELSE 'open' END, "
                           "fired_at=NULL, fires=MAX(fires-1, 0), owed=NULL WHERE id=?", (d["id"],))
        store.db().commit()
    log.warning("place reminder %s didn't go out; it waits for the next visit", d["id"])
    return False


def still_wanted(d: dict) -> bool:
    ""

    now_d = get(d["id"])
    return bool(now_d) and now_d["state"] != "cancelled" and d["bot_id"] in config.BOTS_BY_ID \
        and teller(d) in config.BOTS_BY_ID


def _paid(d: dict) -> None:
    with store._lock:
        store.db().execute("UPDATE place_reminders SET owed=NULL WHERE id=? AND owed=?", (d["id"], d.get("owed")))
        store.db().commit()


async def _said(d: dict) -> bool | None:
    ""



    from . import hermes
    mark = _mark(d).strip()
    try:
        msgs = await asyncio.wait_for(hermes.messages(teller(d), limit=40), 10)
    except Exception:
        return None
    from . import chatter, turnresume
    for i, m in enumerate(msgs):
        if m.get("role") == "user" and mark in str(m.get("text") or ""):
            turn = []
            for n in msgs[i + 1:]:
                if n.get("role") == "user":
                    break
                turn.append(n)
            last = turn[-1] if turn else {}
            return last.get("role") == "assistant" and not chatter.empty(last.get("text")) and turnresume.answers(last)
    return False


RECOVER_TRIES, RECOVER_WAIT_SEC = 8, 15


async def recover_owed() -> int:
    ""

    rows = [_row(r) for r in store.db().execute("SELECT * FROM place_reminders WHERE owed IS NOT NULL ORDER BY id")]
    told = 0
    for d in rows:
        if not still_wanted(d):
            _paid(d)
            continue
        said = None
        for i in range(RECOVER_TRIES):
            said = await _said(d)
            if said is not None:
                break
            if i + 1 < RECOVER_TRIES:
                await asyncio.sleep(RECOVER_WAIT_SEC)
        if said is None:
            log.warning("place reminder %s: couldn't read the chat; it waits for the next start", d["id"])
        elif said:
            _paid(d)
        else:
            told += await remind(d)
    return told




def permission_line(p: str | None) -> str:
    ""
    if p == "always":
        return ""
    if p == "while_using":
        return ("Location for Zero is set to While Using, so I can't tell when you get there. Change it to Always in "
                "Settings, Zero, Location.")
    if p in ("denied", "restricted"):
        return ("Location is off for Zero on your iPhone. Turn it on in Settings, Zero, Location, and pick Always; "
                "the reminder waits until then.")
    if p == "not_asked":
        return "Your iPhone will ask once to use your location. Pick Allow, and the reminder starts."
    return "The Zero app on your iPhone needs its next update for place reminders. This one waits until then."


def said(d: dict) -> str:
    where = d["place"]
    when = "get to" if d["event"] == "arrive" else "leave"
    line = f"I'll remind you {'every time' if d['every'] else 'when'} you {when} {where}."
    extra = permission_line(permission())
    return f"{line} {extra}".strip()


def for_zip(d: dict) -> dict:
    state = {"unfound": "not found on the map"}.get(d["state"]) or (
        "watching" if d["lat"] is not None and permission() == "always" else "waiting for the iPhone")
    return {"id": d["id"], "what": d["what"], "place": place_name(d), "on": d["event"], "every_time": d["every"],
            "state": state}
