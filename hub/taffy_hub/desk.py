""






















import json
import logging
import sqlite3
import time

from . import looking, store

log = logging.getLogger("taffy.desk")


WAIT_SEC = 180


RECENT_SEC = 5 * 60
USE_WRITE_SEC = 20
STALE_SEC = 15 * 60
FREE_SEC = 5 * 60


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS phone_followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT NOT NULL,           -- the Zip whose chat holds the message; reading it there drops this
            collapse TEXT,                -- apns-collapse-id: a newer push for the same subject replaces this one
            due_at REAL NOT NULL,
            push TEXT NOT NULL            -- apns.send's arguments, as JSON
        );
        CREATE INDEX IF NOT EXISTS phone_followups_due ON phone_followups(due_at);
        CREATE TABLE IF NOT EXISTS device_use (
            device_hash TEXT PRIMARY KEY, -- store.devices.token_hash
            used_at REAL NOT NULL         -- the last time the person did something on that device
        );
    """)


_written: dict[str, float] = {}
_platforms: dict[str, str] = {}


def used(device: str | None, at: float | None = None) -> None:
    ""
    if not device:
        return
    now = time.time() if at is None else at
    if now - _written.get(device, 0) < USE_WRITE_SEC:
        return
    _written[device] = now
    with store._lock:
        store.db().execute("INSERT INTO device_use (device_hash, used_at) VALUES (?, ?) "
                           "ON CONFLICT(device_hash) DO UPDATE SET used_at = MAX(used_at, excluded.used_at)",
                           (device, now))
        store.db().commit()


def _platform(device: str) -> str:
    if device not in _platforms:
        row = store.db().execute("SELECT platform FROM push_tokens WHERE device_hash = ? LIMIT 1", (device,)).fetchone()
        if not row:
            return ""
        _platforms[device] = row["platform"]
    return _platforms[device]


def last_used(device: str, platform: str) -> float:
    ""


    row = store.db().execute("SELECT used_at FROM device_use WHERE device_hash = ?", (device,)).fetchone()
    t = row["used_at"] if row else 0.0
    now = time.time()
    if platform == "mac":
        idle = looking.idle(device)
        if idle is not None:
            t = max(t, now - idle)
    elif not looking.away(device):
        t = now
    return t


def route(rows) -> tuple[list, list]:
    ""

    devices = {r["device_hash"]: r["platform"] for r in rows}
    if len(devices) < 2:
        return list(rows), []
    now = time.time()
    recent = {d: t for d, p in devices.items() if now - (t := last_used(d, p)) <= RECENT_SEC}
    if not recent:
        return list(rows), []
    pick = max(recent, key=recent.get)
    return [r for r in rows if r["device_hash"] == pick], [r for r in rows if r["device_hash"] != pick]


def polled(device: str | None, front: bool | None, idle: float | None = None) -> None:
    ""


    if not device:
        return
    platform = _platform(device)
    if front is None:
        front = platform not in ("mac", "")
    if not front:
        return
    if platform == "mac" and (idle is None or idle > looking.IDLE_SEC):
        return
    used(device)
    opened()


def opened() -> None:
    ""
    with store._lock:
        c = store.db()
        if c.execute("SELECT 1 FROM phone_followups LIMIT 1").fetchone() is None:
            return
        c.execute("DELETE FROM phone_followups")
        c.commit()


def _macs() -> list[str]:
    from . import apns
    return [r["device_hash"] for r in apns.tokens() if r["platform"] == "mac"]






_asked: tuple[float, float] | None = None


def asked(idle: float) -> None:
    ""
    global _asked
    if idle >= 0:
        _asked = (time.time(), float(idle))


def silent() -> bool:
    ""
    idle = _idles()
    return not idle or None in idle


def _idles() -> list[float | None]:
    idle = [looking.idle(d) for d in _macs()]
    if _asked is not None and time.time() - _asked[0] <= looking.IDLE_FRESH_SEC:
        idle = [_asked[1] if i is None else i for i in idle] or [_asked[1]]
    return idle


def mac_free() -> bool:
    ""

    from . import macremote
    if macremote.active():
        return False
    idle = _idles()
    return bool(idle) and None not in idle and min(idle) >= FREE_SEC


def person_at_mac() -> bool:
    ""


    from . import macremote
    if macremote.active():
        return True
    idle = _idles()
    return not idle or None in idle or min(idle) <= looking.IDLE_SEC


def hold(chat: str, collapse: str | None, push: dict) -> None:
    with store._lock:
        c = store.db()
        if collapse:
            c.execute("DELETE FROM phone_followups WHERE collapse = ?", (collapse,))
        c.execute("INSERT INTO phone_followups (chat, collapse, due_at, push) VALUES (?,?,?,?)",
                  (chat, collapse, time.time() + WAIT_SEC, json.dumps(push)))
        c.commit()


def drop_task(task_id: str) -> int:
    ""
    with store._lock:
        c = store.db()
        rows = [r["rowid"] for r in c.execute("SELECT rowid, push FROM phone_followups")
                if (json.loads(r["push"] or "{}").get("extra") or {}).get("task_id") == task_id]
        for rid in rows:
            c.execute("DELETE FROM phone_followups WHERE rowid = ?", (rid,))
        c.commit()
    return len(rows)


def replaced(collapse: str | None) -> None:
    ""
    if not collapse:
        return
    with store._lock:
        store.db().execute("DELETE FROM phone_followups WHERE collapse = ?", (collapse,))
        store.db().commit()


def read_chat(chat: str, device: str | None = None) -> None:
    ""
    used(device)
    with store._lock:
        store.db().execute("DELETE FROM phone_followups WHERE chat = ?", (chat,))
        store.db().commit()
    from . import apns
    apns.clear(chat)


def take_due(now: float | None = None) -> list[dict]:
    ""
    now = time.time() if now is None else now
    out = []
    with store._lock:
        c = store.db()
        for r in c.execute("SELECT id, due_at, push FROM phone_followups WHERE due_at <= ? ORDER BY due_at",
                           (now,)).fetchall():
            if c.execute("DELETE FROM phone_followups WHERE id = ?", (r["id"],)).rowcount != 1:
                continue
            if now - r["due_at"] > STALE_SEC:
                log.info("dropped a follow-up %d s overdue", int(now - r["due_at"]))
                continue
            out.append(json.loads(r["push"]))
        c.commit()
    return out


def send_due() -> int:
    ""
    from . import apns
    sent = 0
    for push in take_due():
        if push.get("devices"):
            push["devices"] = tuple(push["devices"])
            push.pop("platforms", None)
        else:
            push["platforms"] = tuple(push.get("platforms") or ("iphone",))
        try:
            if apns.send(**push, hold=False):
                sent += 1
                log.info("follow-up sent for %s", push.get("bot_id") or "default")
        except Exception:
            log.exception("follow-up failed")
    return sent
