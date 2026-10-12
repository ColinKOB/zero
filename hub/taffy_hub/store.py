"""The hub's own small SQLite store: paired devices, bot sessions, the activity feed and cursors."""

import hashlib
import secrets
import sqlite3
import threading
import time

from . import config

_lock = threading.RLock()         # serializes writes (re-entrant: db() may run inside it)
_local = threading.local()        # one connection per thread: requests run concurrently
_schema_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (token_hash TEXT PRIMARY KEY, name TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS pairing_codes (code TEXT PRIMARY KEY, expires_at INTEGER);
CREATE TABLE IF NOT EXISTS bot_sessions (bot_id TEXT PRIMARY KEY, session_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,           -- task_done | task_blocked | task_review | task_started | message | checkin
    bot_id TEXT,
    task_id TEXT,
    title TEXT,
    body TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reactions (
    bot_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    who TEXT NOT NULL,            -- colin | bot
    kind TEXT NOT NULL,           -- heart | like | dislike | laugh | emphasize | question
    excerpt TEXT,
    told INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (bot_id, message_id, who)
);
CREATE TABLE IF NOT EXISTS push_tokens (       -- Apple push, one per paired device and platform (apns.py)
    apns_token TEXT PRIMARY KEY,
    device_hash TEXT NOT NULL,
    env TEXT NOT NULL,            -- development | production
    platform TEXT NOT NULL,       -- iphone | mac
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notices (            -- proactive questions and the alerts on their budget (notices.py)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT UNIQUE NOT NULL, -- what it is about: a thread, a task, a renewal; one question per subject
    noticer TEXT NOT NULL,        -- unreplied | reply_waiting | waiting_in_app | renewal | promise | nudge
    owner TEXT,                   -- the Zip that does the follow-through
    state TEXT NOT NULL,          -- open | picked | dismissed | expired | handled | waiting | stopped | nudge | preparing
    card TEXT NOT NULL DEFAULT '{}',
    version TEXT,                 -- changes when the underlying date changes
    deadline REAL,
    urgent INTEGER NOT NULL DEFAULT 0,
    feed_id INTEGER,
    follow TEXT,
    picked TEXT,
    pick_kind TEXT,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    answered REAL,
    expires REAL,
    recheck REAL,
    pushes INTEGER NOT NULL DEFAULT 0,
    last_push REAL,
    push_due INTEGER NOT NULL DEFAULT 0,
    push_after REAL,
    raised INTEGER NOT NULL DEFAULT 0,
    task_id TEXT,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS message_ids (       -- the id the app first saw each chat message under (lineage.py)
    bot_id TEXT NOT NULL,
    key TEXT NOT NULL,            -- role|timestamp|tool call: a compaction's copy has the original's
    id TEXT NOT NULL,
    PRIMARY KEY (bot_id, key)
);
CREATE TABLE IF NOT EXISTS card_calls (        -- tool calls that put a card in front of the person (lineage.py)
    bot_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    PRIMARY KEY (bot_id, call_id)
);
CREATE TABLE IF NOT EXISTS notice_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    kind TEXT NOT NULL,           -- push | nudge_push | held | haiku | posted | dismiss | close | action | wait | expire ...
    noticer TEXT,
    subject TEXT
);
"""

REACTIONS = ("heart", "like", "dislike", "laugh", "emphasize", "question")


def db() -> sqlite3.Connection:
    global _schema_ready
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.HUB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.HUB_DB, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        if not _schema_ready:
            with _lock:
                conn.executescript(SCHEMA)
                for stmt in ("ALTER TABLE feed ADD COLUMN changed_at REAL",          # items edited in place
                             "ALTER TABLE feed ADD COLUMN step_of INTEGER",         # a step of a teammate's answer
                             "ALTER TABLE feed ADD COLUMN plain_title TEXT",        # a step's short label (teamvoice.py)
                             "ALTER TABLE devices ADD COLUMN mac_link INTEGER",      # the one Mac bots may use
                             "ALTER TABLE devices ADD COLUMN last_used_at INTEGER",
                             "ALTER TABLE notices ADD COLUMN prep TEXT"):            # a prepared action (prepared.py)
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e):
                            raise
                _schema_ready = True
        _local.conn = conn
    return conn


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _norm_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


def new_pairing_code(ttl_sec: int = 900) -> str:
    """Only a hash of the code is stored, like device tokens; expired codes are deleted."""
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    with _lock:
        db().execute("DELETE FROM pairing_codes WHERE expires_at < ?", (int(time.time()),))
        db().execute("INSERT INTO pairing_codes (code, expires_at) VALUES (?, ?)", (_hash(code), int(time.time()) + ttl_sec))
        db().commit()
    return code


def redeem_pairing_code(code: str, device_name: str) -> str | None:
    code = _hash(_norm_code(code))
    with _lock:
        db().execute("DELETE FROM pairing_codes WHERE expires_at < ?", (int(time.time()),))
        row = db().execute("SELECT expires_at FROM pairing_codes WHERE code = ?", (code,)).fetchone()
        if not row:
            db().commit()
            return None
        db().execute("DELETE FROM pairing_codes WHERE code = ?", (code,))
        token = secrets.token_urlsafe(32)
        db().execute("INSERT INTO devices (token_hash, name, created_at) VALUES (?, ?, ?)",
                     (_hash(token), device_name[:80], int(time.time())))
        db().commit()
    return token


def device_for_token(token: str) -> str | None:
    h = _hash(token)
    row = db().execute("SELECT name, last_used_at FROM devices WHERE token_hash = ?", (h,)).fetchone()
    if not row:
        return None
    now = int(time.time())
    if (row["last_used_at"] or 0) < now - 3600:   # at most hourly, for "last used" in Paired Devices
        with _lock:
            db().execute("UPDATE devices SET last_used_at = ? WHERE token_hash = ?", (now, h))
            db().commit()
    return row["name"]


def may_hold_mac_link(token: str) -> bool:
    """Only one paired device is the Mac bots can use: the first to connect claims it, and removing that
    device in Paired Devices frees it. Another device's token cannot take the Mac's place."""
    h = _hash(token)
    with _lock:
        holder = db().execute("SELECT token_hash FROM devices WHERE mac_link = 1").fetchone()
        if holder is None:
            db().execute("UPDATE devices SET mac_link = 1 WHERE token_hash = ?", (h,))
            db().commit()
            return True
        return holder["token_hash"] == h


def session_for(bot_id: str) -> str | None:
    row = db().execute("SELECT session_id FROM bot_sessions WHERE bot_id = ?", (bot_id,)).fetchone()
    return (row["session_id"] or None) if row else None


def set_session(bot_id: str, session_id: str) -> None:
    with _lock:
        db().execute("INSERT OR REPLACE INTO bot_sessions (bot_id, session_id) VALUES (?, ?)", (bot_id, session_id))
        db().commit()


def add_feed(kind: str, bot_id: str | None, task_id: str | None, title: str, body: str,
             at: float | None = None, step_of: int | None = None) -> int:
    """`at`: when it happened, if not now (a call is placed where it started, like the Phone app's Recents).
    `step_of`: the teammate's question (team_msgs id) this sign-in or action was part of answering; the app shows it
    as a small work row between the question and the answer instead of a message."""
    with _lock:
        cur = db().execute(
            "INSERT INTO feed (kind, bot_id, task_id, title, body, created_at, step_of) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, bot_id, task_id, title, body, int(time.time() if at is None else at), step_of))
        db().commit()
        return cur.lastrowid


def update_feed(feed_id: int, title: str, body: str) -> None:
    with _lock:
        db().execute("UPDATE feed SET title = ?, body = ?, changed_at = ? WHERE id = ?",
                     (title, body, time.time(), feed_id))
        db().commit()


def set_plain_title(feed_id: int, label: str) -> None:
    """A step's short label for the work log; its title stays as it was for Show Original."""
    with _lock:
        db().execute("UPDATE feed SET plain_title = ?, changed_at = ? WHERE id = ?", (label, time.time(), feed_id))
        db().commit()


def touch_feed(feed_id: int, at: float | None = None) -> None:
    """Moves an item to a new place in its chat's time order (proactive questions go under the morning brief)."""
    with _lock:
        db().execute("UPDATE feed SET created_at = ?, changed_at = ? WHERE id = ?",
                     (int(at if at is not None else time.time()), time.time(), feed_id))
        db().commit()


def feed_changed_bots(after: float) -> list[str]:
    rows = db().execute("SELECT DISTINCT COALESCE(bot_id, 'default') AS b FROM feed WHERE changed_at > ?", (after,))
    return [r["b"] for r in rows]


def feed_since(after_id: int, limit: int = 200) -> tuple[list[dict], bool]:
    rows = db().execute("SELECT * FROM feed WHERE id > ? ORDER BY id ASC LIMIT ?", (after_id, limit + 1)).fetchall()
    return [dict(r) for r in rows[:limit]], len(rows) > limit


def max_feed_id() -> int:
    return db().execute("SELECT COALESCE(MAX(id), 0) FROM feed").fetchone()[0]


def devices(current_token: str | None = None) -> list[dict]:
    cur = _hash(current_token) if current_token else None
    rows = db().execute("SELECT rowid, token_hash, name, created_at, mac_link, last_used_at FROM devices "
                        "ORDER BY created_at").fetchall()
    return [{"id": r["rowid"], "name": r["name"], "created_at": r["created_at"], "current": r["token_hash"] == cur,
             "mac_link": bool(r["mac_link"]), "last_used_at": r["last_used_at"]} for r in rows]


def remove_device(rowid: int) -> bool:
    with _lock:
        cur = db().execute("DELETE FROM devices WHERE rowid = ?", (rowid,))
        db().commit()
        return cur.rowcount > 0


def revoke_device(token: str) -> None:
    with _lock:
        db().execute("DELETE FROM devices WHERE token_hash = ?", (_hash(token),))
        db().commit()


def get_cursor(name: str, default: int = 0) -> int:
    row = db().execute("SELECT value FROM cursors WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else default


def set_cursor(name: str, value: int) -> None:
    with _lock:
        db().execute("INSERT OR REPLACE INTO cursors (name, value) VALUES (?, ?)", (name, value))
        db().commit()


def checkins(limit: int = 60) -> list[dict]:
    rows = db().execute("SELECT * FROM feed WHERE kind = 'checkin' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def latest_feed_for(bot_id: str) -> dict | None:
    row = db().execute("SELECT * FROM feed WHERE bot_id = ? AND kind NOT IN ('task_started', 'event') ORDER BY id DESC LIMIT 1",
                       (bot_id,)).fetchone()
    return dict(row) if row else None


def get_setting(name: str, default=None):
    import json
    row = db().execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(name: str, value) -> None:
    import json
    with _lock:
        db().execute("INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)", (name, json.dumps(value)))
        db().commit()


def note_said(bot_id: str, at: float | None = None) -> None:
    """The person just wrote (or said on a call) something to this Zip. A card the Zip raised before this is no longer
    what it waits on: the conversation moved on (Oct 9: an 11:54 sign-in card kept Zero "paused for your okay" at
    11:18 p.m., while Colin was talking to it about something else). Kept in settings, so it survives a restart."""
    with _lock:
        said = get_setting("said_at", {}) or {}
        said[bot_id] = float(at if at is not None else time.time())
        set_setting("said_at", said)


def said_at(bot_id: str) -> float:
    """When the person last wrote to this Zip; 0 when never (or before the hub kept this)."""
    try:
        return float((get_setting("said_at", {}) or {}).get(bot_id) or 0)
    except (TypeError, ValueError):
        return 0.0


CARD_NOTES_MAX = 5   # a Zip hears at most this many card outcomes at the start of a turn


def add_card_note(bot_id: str, text: str) -> None:
    """A card outcome the Zip hears at the start of its next chat turn (a card that ran out of time). Kept in
    settings, so no table changes."""
    with _lock:
        notes = get_setting("card_notes", {}) or {}
        notes[bot_id] = (notes.get(bot_id) or [])[-(CARD_NOTES_MAX - 1):] + [text]
        set_setting("card_notes", notes)


def take_card_notes(bot_id: str) -> list[str]:
    with _lock:
        notes = get_setting("card_notes", {}) or {}
        mine = notes.pop(bot_id, None) or []
        if mine:
            set_setting("card_notes", notes)
    return mine


def quiet(bot_id: str | None) -> bool:
    """Muted, or snoozed until a time that has not come yet: no alerts from this bot."""
    b = bot_id or "default"
    if b in (get_setting("muted_bots", []) or []):
        return True
    return (get_setting("snoozed", {}) or {}).get(b, 0) > time.time()


def notify_on(bot_id: str | None) -> bool:
    """The Zip's "Notify Me When It Finishes or Needs Me" switch. On for every Zip until Colin turns it off, so a new
    Zip behaves like the rest. Only Zips he turned off are stored."""
    return (bot_id or "default") not in (get_setting("notify_off_bots", []) or [])


def set_notify(bot_id: str, on: bool) -> None:
    off = [b for b in (get_setting("notify_off_bots", []) or []) if b != bot_id]
    set_setting("notify_off_bots", off if on else sorted(off + [bot_id]))


def activity_for(bot_id: str, limit: int = 40) -> list[dict]:
    """A bot's own results for its chat: finished, blocked or review tasks, failures and check-ins, and its sign-in
    asks ("login": Oct 9, a Lens Shop card was posted but never reached Zero's chat)."""
    kinds = (("checkin", "signin", "signin_decision", "action", "image", "call", "team", "mac", "choices", "account", "event",
              "login", "mac_ask")
             if bot_id == "default" else
             ("task_done", "task_blocked", "task_review", "task_failed", "checkin", "signin", "signin_decision", "action",
              "image", "call", "team", "mac", "choices", "account", "event", "login", "mac_ask"))
    q = f"SELECT * FROM feed WHERE bot_id = ? AND kind IN ({','.join('?' * len(kinds))}) ORDER BY id DESC LIMIT ?"
    rows = [dict(r) for r in reversed(db().execute(q, (bot_id, *kinds, limit)).fetchall())]
    # A card still waiting on Colin stays in the chat however much came after it (Oct 9, chat freshness).
    oldest = rows[0]["id"] if rows else None
    if oldest is not None and len(rows) >= limit:
        waiting = db().execute(
            "SELECT * FROM feed WHERE bot_id = ? AND kind IN ('login', 'account') AND id < ? "
            "AND CASE WHEN json_valid(body) THEN json_extract(body, '$.state') END = 'open' ORDER BY id DESC LIMIT 10", (bot_id, oldest)).fetchall()
        rows = [dict(r) for r in reversed(waiting)] + rows
    return rows



# MARK: Reactions (tapbacks on chat messages, from Colin and from the bots)

def set_reaction(bot_id: str, message_id: str, who: str, kind: str | None, excerpt: str = "") -> None:
    with _lock:
        c = db()
        if kind is None:
            c.execute("DELETE FROM reactions WHERE bot_id=? AND message_id=? AND who=?", (bot_id, message_id, who))
        else:
            c.execute("INSERT OR REPLACE INTO reactions (bot_id, message_id, who, kind, excerpt, told, created_at) "
                      "VALUES (?, ?, ?, ?, ?, 0, ?)", (bot_id, message_id, who, kind, excerpt[:200], int(time.time())))
        c.commit()


def reactions_for(bot_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in db().execute("SELECT message_id, who, kind FROM reactions WHERE bot_id=?", (bot_id,)):
        out.setdefault(r["message_id"], []).append({"who": r["who"], "kind": r["kind"]})
    return out


def take_untold_reactions(bot_id: str) -> list[dict]:
    """Colin's reactions the bot has not heard about yet; marks them told."""
    with _lock:
        c = db()
        rows = [dict(r) for r in c.execute("SELECT message_id, kind, excerpt FROM reactions WHERE bot_id=? AND "
                                           "who='colin' AND told=0 ORDER BY created_at", (bot_id,))]
        c.execute("UPDATE reactions SET told=1 WHERE bot_id=? AND who='colin'", (bot_id,))
        c.commit()
    return rows
