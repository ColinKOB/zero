"""The front door's accounts: a username, a password hash, the home the account maps to, and its plan.

SQLite in the owner's data folder (ZERO_FRONTDOOR_DB overrides it). Passwords are hashed with scrypt from the
standard library (N=2^15, r=8, p=1, a 16-byte salt per password), so nothing new needs installing on alien.
Neither a password nor a device token is ever written here or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("ZERO_FRONTDOOR_DB",
                              Path.home() / ".local" / "share" / "zero-frontdoor" / "accounts.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username TEXT PRIMARY KEY,          -- lowercased; what the person types to sign in
    home_id TEXT NOT NULL UNIQUE,       -- the home in /srv/zero/homes.json (Linux user zero-<home_id>)
    pw_hash TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',  -- plans.py; billing sets it later
    state TEXT NOT NULL DEFAULT 'ready',-- creating, ready or failed (a sign-up whose home is still being made)
    person TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_sign_in INTEGER
);
CREATE TABLE IF NOT EXISTS invites (
    username TEXT PRIMARY KEY,          -- lowercased; the one username this invite lets sign up while sign-up is closed
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 256

# scrypt settings: about 64 MB and 0.1 s a check on alien.
_N, _R, _P, _DKLEN = 2 ** 15, 8, 1, 32
_MAXMEM = 128 * 1024 * 1024

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            try:
                os.chmod(DB_PATH, 0o600)
            except OSError:
                pass
        return _conn


def reset_for_tests(path: Path) -> None:
    """Points the store at another file (tests only)."""
    global _conn, DB_PATH
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        DB_PATH = path


# ---------------------------------------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------------------------------------

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, want = stored.split("$")
        if kind != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(base64.b64decode(want)), maxmem=_MAXMEM)
        return hmac.compare_digest(dk, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


# A wrong username costs the same as a wrong password, so timing does not say which usernames exist.
_DUMMY = None


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(16))
    return _DUMMY


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def password_problem(username: str, password: str) -> str | None:
    """A plain sentence when the password will not do, else None."""
    if len(password) < MIN_PASSWORD:
        return f"Use a password with at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return "That password is too long."
    if password.strip().lower() == normalize_username(username):
        return "Choose a password that is different from your username."
    return None


def username_problem(username: str) -> str | None:
    if not USERNAME_RE.match(normalize_username(username)):
        return ("Choose a username of 3 to 32 characters. Use letters, numbers, dots, dashes or underscores, "
                "and start with a letter or number.")
    return None


# ---------------------------------------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------------------------------------

def get(username: str) -> dict | None:
    row = db().execute("SELECT * FROM accounts WHERE username = ?", (normalize_username(username),)).fetchone()
    return dict(row) if row else None


def by_home(home_id: str) -> dict | None:
    row = db().execute("SELECT * FROM accounts WHERE home_id = ?", (home_id,)).fetchone()
    return dict(row) if row else None


def all_accounts() -> list[dict]:
    rows = db().execute("SELECT username, home_id, plan, state, created_at, last_sign_in FROM accounts "
                        "ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def add(username: str, password: str, home_id: str, *, state: str = "ready", person: str = "",
        plan: str = "free") -> dict:
    """A new account. Raises ValueError with a plain sentence when the username or home is taken."""
    name = normalize_username(username)
    with _lock:
        if get(name):
            raise ValueError("That username is taken. Try another one.")
        if by_home(home_id):
            raise ValueError(f"The home {home_id} already has an account.")
        db().execute("INSERT INTO accounts (username, home_id, pw_hash, plan, state, person, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (name, home_id, hash_password(password), plan, state, person, int(time.time())))
        db().commit()
    return get(name)


def check(username: str, password: str) -> dict | None:
    """The account when the password is right, else None (always one scrypt check)."""
    acct = get(username)
    ok = verify_password(password, acct["pw_hash"] if acct else _dummy_hash())
    return acct if (acct and ok) else None


def set_password(username: str, password: str) -> None:
    with _lock:
        db().execute("UPDATE accounts SET pw_hash = ? WHERE username = ?",
                     (hash_password(password), normalize_username(username)))
        db().commit()


def set_state(username: str, state: str) -> None:
    with _lock:
        db().execute("UPDATE accounts SET state = ? WHERE username = ?", (state, normalize_username(username)))
        db().commit()


def touch_sign_in(username: str) -> None:
    with _lock:
        db().execute("UPDATE accounts SET last_sign_in = ? WHERE username = ?",
                     (int(time.time()), normalize_username(username)))
        db().commit()


def remove(username: str) -> bool:
    with _lock:
        cur = db().execute("DELETE FROM accounts WHERE username = ?", (normalize_username(username),))
        db().commit()
        return cur.rowcount > 0


def _set_plan_row(username: str, plan: str) -> None:
    with _lock:
        cur = db().execute("UPDATE accounts SET plan = ? WHERE username = ?", (plan, normalize_username(username)))
        db().commit()
        if cur.rowcount == 0:
            raise KeyError(username)


# ---------------------------------------------------------------------------------------------------------
# Invites: sign-up is closed by default; the owner lets one username in with `zero-account invite USERNAME`.
# ---------------------------------------------------------------------------------------------------------

def add_invite(username: str, hours: float = 72) -> dict:
    name = normalize_username(username)
    now = int(time.time())
    with _lock:
        db().execute("INSERT OR REPLACE INTO invites (username, created_at, expires_at) VALUES (?, ?, ?)",
                     (name, now, now + int(hours * 3600)))
        db().commit()
    return {"username": name, "expires_at": now + int(hours * 3600)}


def invited(username: str) -> bool:
    row = db().execute("SELECT expires_at FROM invites WHERE username = ?", (normalize_username(username),)).fetchone()
    return bool(row) and row["expires_at"] > time.time()


def use_invite(username: str) -> None:
    with _lock:
        db().execute("DELETE FROM invites WHERE username = ?", (normalize_username(username),))
        db().commit()


def all_invites() -> list[dict]:
    return [dict(r) for r in db().execute("SELECT * FROM invites ORDER BY created_at").fetchall()]
