""

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextvars import ContextVar

from . import config

_lock = threading.RLock()
_local = threading.local()
_setups: list = []
feed_turn: ContextVar[tuple[str, str] | None] = ContextVar("feed_turn", default=None)

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
CREATE TABLE IF NOT EXISTS waits (              -- what a Zip is waiting on from someone else (waits.py)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    session_id TEXT,
    what TEXT NOT NULL,           -- "the streetlight repair request for the two poles on Elm St"
    expect_from TEXT NOT NULL DEFAULT '[]',   -- JSON: addresses and @domains a reply may come from; [] = check back
    subject TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',          -- the mailbox to watch; '' = every mailbox the Zip can read
    thread TEXT NOT NULL DEFAULT '',           -- Gmail's threadId of the message the Zip sent: an exact match
    next_step TEXT NOT NULL DEFAULT '',
    due REAL NOT NULL,
    state TEXT NOT NULL,          -- open | answered | overdue | closed
    created REAL NOT NULL,
    updated REAL NOT NULL,
    answer TEXT,                  -- JSON: the envelope that answered it
    close_reason TEXT,
    source TEXT NOT NULL DEFAULT 'tool',      -- tool | email
    wakes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS correspondents (     -- whom a Zip wrote to from its own mailbox (waits.py)
    bot_id TEXT NOT NULL,
    account TEXT NOT NULL,
    address TEXT NOT NULL,
    last_sent REAL NOT NULL,
    PRIMARY KEY (bot_id, account, address)
);
CREATE TABLE IF NOT EXISTS mail_seen (          -- a Zip's own mailbox: messages already handed to it (waits.py)
    account TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (account, msg_id)
);
CREATE TABLE IF NOT EXISTS zipmail_seen (       -- Zip mail copied to disk; acked or due an ack (zipmail.py)
    key TEXT PRIMARY KEY,         -- the cloud's object key
    account TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS zipmail_held (       -- live addresses with no Zip here, whose mail is held on disk (zipmail.py)
    address TEXT PRIMARY KEY,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wake_notes (         -- a Zip's errand wake not yet run; resent after a restart (waits.py)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    note TEXT NOT NULL,
    at REAL NOT NULL,
    started INTEGER NOT NULL DEFAULT 0     -- the Zip's turn began: after a restart it resumes as a check-first note
);
CREATE TABLE IF NOT EXISTS triggers (           -- routines an outside request starts (triggers.py)
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL,
    token TEXT NOT NULL,          -- the link's key; the app shows the link again for copying
    created REAL NOT NULL,
    last_fired REAL,
    fires INTEGER NOT NULL DEFAULT 0,
    day TEXT,
    day_count INTEGER NOT NULL DEFAULT 0,
    unread INTEGER NOT NULL DEFAULT 0,        -- fires whose result the person has not opened since
    state TEXT NOT NULL DEFAULT 'on'          -- on | paused
);
CREATE TABLE IF NOT EXISTS trigger_events (   -- kept apart from the trigger: a wake still queued can read its
    id INTEGER PRIMARY KEY AUTOINCREMENT,        -- request after the trigger is deleted (EVENT_DAYS)
    trigger_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    name TEXT NOT NULL,
    at REAL NOT NULL,
    body TEXT NOT NULL
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


def schema(setup):
    ""





    _setups.append(setup)
    return setup


def db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.HUB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.HUB_DB, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    done_conn, done = getattr(_local, "set_up", (None, 0))
    if done_conn is not conn:
        done = 0
    if done < len(_setups):
        with _lock:
            for setup in _setups[done:]:
                setup(conn)
            conn.commit()
        _local.set_up = (conn, len(_setups))
    return conn


@schema
def _store_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for stmt in ("ALTER TABLE feed ADD COLUMN changed_at REAL",
                 "ALTER TABLE wake_notes ADD COLUMN tell_id INTEGER",
                 "ALTER TABLE feed ADD COLUMN step_of INTEGER",
                 "ALTER TABLE feed ADD COLUMN plain_title TEXT",
                 "ALTER TABLE feed ADD COLUMN team_id TEXT",
                 "ALTER TABLE feed ADD COLUMN turn_id TEXT",
                 "ALTER TABLE feed ADD COLUMN aside_ids TEXT",
                 "ALTER TABLE feed ADD COLUMN quiet_word TEXT",
                 "ALTER TABLE feed ADD COLUMN quiet_title TEXT",
                 "ALTER TABLE feed ADD COLUMN quiet_why TEXT",
                 "ALTER TABLE feed ADD COLUMN quiet_at REAL",
                 "ALTER TABLE feed ADD COLUMN written_by TEXT",
                 "ALTER TABLE feed ADD COLUMN written_for TEXT",
                 "ALTER TABLE devices ADD COLUMN mac_link INTEGER",
                 "ALTER TABLE devices ADD COLUMN last_used_at INTEGER",
                 "ALTER TABLE devices ADD COLUMN tester INTEGER",
                 "ALTER TABLE notices ADD COLUMN prep TEXT",
                 "ALTER TABLE notice_log ADD COLUMN detail TEXT"):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _norm_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


def new_pairing_code(ttl_sec: int = 900) -> str:
    ""
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    with _lock:
        db().execute("DELETE FROM pairing_codes WHERE expires_at < ?", (int(time.time()),))
        db().execute("INSERT INTO pairing_codes (code, expires_at) VALUES (?, ?)", (_hash(code), int(time.time()) + ttl_sec))
        db().commit()
    return code


def redeem_pairing_code(code: str, device_name: str, tester: bool = False) -> str | None:
    ""

    code = _hash(_norm_code(code))
    with _lock:
        db().execute("DELETE FROM pairing_codes WHERE expires_at < ?", (int(time.time()),))
        row = db().execute("SELECT expires_at FROM pairing_codes WHERE code = ?", (code,)).fetchone()
        if not row:
            db().commit()
            return None
        db().execute("DELETE FROM pairing_codes WHERE code = ?", (code,))
        token = secrets.token_urlsafe(32)
        db().execute("INSERT INTO devices (token_hash, name, created_at, tester) VALUES (?, ?, ?, ?)",
                     (_hash(token), device_name[:80], int(time.time()), 1 if tester else None))
        db().commit()
    return token


def device_for_token(token: str) -> str | None:
    h = _hash(token)
    row = db().execute("SELECT name, last_used_at FROM devices WHERE token_hash = ?", (h,)).fetchone()
    if not row:
        return None
    now = int(time.time())
    if (row["last_used_at"] or 0) < now - 3600:
        with _lock:
            db().execute("UPDATE devices SET last_used_at = ? WHERE token_hash = ?", (now, h))
            db().commit()
    return row["name"]


def tester_name(token: str | None) -> str | None:
    ""
    if not token:
        return None
    row = db().execute("SELECT name, tester FROM devices WHERE token_hash = ?", (_hash(token),)).fetchone()
    return (row["name"] or "unnamed") if row and row["tester"] else None




TOOL_PAIRINGS = ("Chat trace",)


def tool_pairing(token: str | None) -> str | None:
    ""
    if not token:
        return None
    row = db().execute("SELECT name FROM devices WHERE token_hash = ?", (_hash(token),)).fetchone()
    return row["name"] if row and row["name"] in TOOL_PAIRINGS else None


def may_hold_mac_link(token: str) -> bool:
    ""

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




WRITTEN_BY = ("zip", "model", "hub", "person")
WRITTEN_FOR = ("person", "hub", "zip", "record")
_CARD_KINDS = {"choices", "kit", "login", "account", "code", "mac_ask", "mail", "text", "result", "image", "project",
               "wallet_request", "wallet_pay", "team_proposal", "signin_request", "zip_apps", "service_key"}
_RECORD_KINDS = {"event", "mac", "action", "signin", "signin_decision", "call", "wallet_receipt", "task_started"}


def fields_for(kind: str) -> tuple[str, str]:
    ""



    if kind == "team":
        return "zip", "zip"
    if kind in _RECORD_KINDS:
        return "hub", "record"
    if kind.startswith("task_"):
        return "zip", "hub"
    if kind in ("message", "message_local"):
        return "zip", "person"
    if kind == "checkin":
        return "hub", "person"
    return ("zip", "person") if kind in _CARD_KINDS else ("hub", "person")


def with_fields(row: dict) -> dict:
    ""
    if not row.get("written_by") or not row.get("written_for"):
        by, for_ = fields_for(row.get("kind") or "")
        row["written_by"], row["written_for"] = row.get("written_by") or by, row.get("written_for") or for_
    return row


def _fields(kind: str, written_by: str | None, written_for: str | None) -> tuple[str, str]:
    by, for_ = fields_for(kind)
    by, for_ = written_by or by, written_for or for_
    if by not in WRITTEN_BY or for_ not in WRITTEN_FOR:
        raise ValueError(f"a chat row needs written_by in {WRITTEN_BY} and written_for in {WRITTEN_FOR}")
    return by, for_


def add_feed(kind: str, bot_id: str | None, task_id: str | None, title: str, body: str,
             at: float | None = None, step_of: int | None = None, *, written_by: str | None = None,
             written_for: str | None = None, team_id: str | None = None) -> int:
    ""




    by, for_ = _fields(kind, written_by, written_for)
    with _lock:
        turn = feed_turn.get()
        if turn is None and bot_id:
            from . import turns
            active = turns.current(bot_id)
            if active and active.typed:
                turn = (bot_id, active.id)
        turn_id = turn[1] if turn and turn[0] == bot_id else None
        cur = db().execute(
            "INSERT INTO feed (kind, bot_id, task_id, title, body, created_at, step_of, turn_id, written_by, "
            "written_for, team_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, bot_id, task_id, title, body, int(time.time() if at is None else at), step_of, turn_id, by, for_,
             team_id))
        db().commit()
        return cur.lastrowid


def restate_feed(feed_id: int, kind: str, title: str, body: str, *, expect_kind: str | None = None,
                 written_by: str | None = None, written_for: str | None = None) -> bool:
    ""

    by, for_ = _fields(kind, written_by, written_for)
    with _lock:
        cur = db().execute("UPDATE feed SET kind=?, title=?, body=?, written_by=?, written_for=?, changed_at=? "
                           "WHERE id=?" + (" AND kind=?" if expect_kind else ""),
                           (kind, title, body, by, for_, time.time(), feed_id,
                            *((expect_kind,) if expect_kind else ())))
        db().commit()
        return cur.rowcount > 0


def update_feed(feed_id: int, title: str, body: str) -> None:
    with _lock:
        db().execute("UPDATE feed SET title = ?, body = ?, changed_at = ? WHERE id = ?",
                     (title, body, time.time(), feed_id))
        db().commit()


def set_feed_aside(feed_id: int, aside: list[dict]) -> None:
    ""
    with _lock:
        db().execute("UPDATE feed SET aside_ids = ? WHERE id = ?", (json.dumps(aside), feed_id))
        db().commit()


def set_plain_title(feed_id: int, label: str) -> None:
    ""
    with _lock:
        db().execute("UPDATE feed SET plain_title = ?, changed_at = ? WHERE id = ?", (label, time.time(), feed_id))
        db().commit()


def touch_feed(feed_id: int, at: float | None = None) -> None:
    ""
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
    ""


    with _lock:
        said = get_setting("said_at", {}) or {}
        said[bot_id] = float(at if at is not None else time.time())
        set_setting("said_at", said)


def said_at(bot_id: str) -> float:
    ""
    try:
        return float((get_setting("said_at", {}) or {}).get(bot_id) or 0)
    except (TypeError, ValueError):
        return 0.0


CARD_NOTES_MAX = 5


def add_card_note(bot_id: str, text: str, queue: str = "card_notes", keep: int = CARD_NOTES_MAX) -> None:
    ""


    with _lock:
        notes = get_setting(queue, {}) or {}
        notes[bot_id] = (notes.get(bot_id) or [])[-(keep - 1):] + [text]
        set_setting(queue, notes)


def take_card_notes(bot_id: str, queue: str = "card_notes") -> list[str]:
    with _lock:
        notes = get_setting(queue, {}) or {}
        mine = notes.pop(bot_id, None) or []
        if mine:
            set_setting(queue, notes)
    return mine


BOARD_NOTES = "board_notes"
BOARD_NOTES_MAX = 20

REPORT_NOTES = "report_notes"
REPORT_NOTES_MAX = 3


def quiet(bot_id: str | None) -> bool:
    ""
    b = bot_id or "default"
    if b in (get_setting("muted_bots", []) or []):
        return True
    return (get_setting("snoozed", {}) or {}).get(b, 0) > time.time()


def notify_on(bot_id: str | None) -> bool:
    ""

    return (bot_id or "default") not in (get_setting("notify_off_bots", []) or [])


def set_notify(bot_id: str, on: bool) -> None:
    off = [b for b in (get_setting("notify_off_bots", []) or []) if b != bot_id]
    set_setting("notify_off_bots", off if on else sorted(off + [bot_id]))




WALLET_KINDS = ("wallet_request", "wallet_pay", "wallet_receipt")


def chat_kinds(bot_id: str) -> tuple[str, ...]:
    ""


    kinds = (("checkin", "signin", "signin_decision", "action", "image", "call", "team", "mac", "choices", "account", "event",
              "login", "code", "mac_ask", "mac_wait", "project", "mail", "text", "result", "kit", "todo_card", "handoff",
              *WALLET_KINDS)
             if bot_id == "default" else
             ("task_done", "task_failed", "checkin", "signin", "signin_decision", "action",
              "image", "call", "team", "mac", "choices", "account", "event", "login", "code", "mac_ask", "mac_wait", "project",
              "mail", "text", "result", "kit", "todo_card", "handoff", *WALLET_KINDS))


    if getattr(config.BOTS_BY_ID.get(bot_id), "merged_from", ()):
        kinds = tuple(k for k in kinds if k != "team")
    return kinds


def activity_for(bot_id: str, limit: int = 40) -> list[dict]:
    ""



    kinds = chat_kinds(bot_id)
    q = f"SELECT * FROM feed WHERE bot_id = ? AND kind IN ({','.join('?' * len(kinds))}) ORDER BY id DESC LIMIT ?"
    rows = [dict(r) for r in reversed(db().execute(q, (bot_id, *kinds, limit)).fetchall())]

    oldest = rows[0]["id"] if rows else None
    if oldest is not None and len(rows) >= limit:
        waiting = db().execute(
            "SELECT * FROM feed WHERE bot_id = ? "
            "AND kind IN ('login', 'account', 'code', 'project', 'mail', 'text', 'choices', 'mac_ask', 'mac_wait', 'kit', "
            "'wallet_request', 'wallet_pay') "
            "AND id < ? "
            "AND CASE WHEN json_valid(body) THEN COALESCE(json_extract(body, '$.state'), "
            "CASE WHEN kind = 'wallet_request' THEN 'open' END) END IN "
            "('open', CASE WHEN kind IN ('mail', 'text') THEN 'sending' WHEN kind = 'wallet_pay' THEN 'pending' END) "


            "OR (bot_id = ? AND kind = 'todo_card' AND id < ? AND created_at >= ?) "
            "ORDER BY id DESC", (bot_id, oldest, bot_id, oldest, int(time.time()) - 86400)).fetchall()
        rows = [dict(r) for r in reversed(waiting)] + rows
    return [with_fields(r) for r in rows]





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
    ""
    with _lock:
        c = db()
        rows = [dict(r) for r in c.execute("SELECT message_id, kind, excerpt FROM reactions WHERE bot_id=? AND "
                                           "who='colin' AND told=0 ORDER BY created_at", (bot_id,))]
        c.execute("UPDATE reactions SET told=1 WHERE bot_id=? AND who='colin'", (bot_id,))
        c.commit()
    return rows
