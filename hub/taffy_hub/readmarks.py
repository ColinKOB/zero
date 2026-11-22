""










from __future__ import annotations

import math
import sqlite3
import time

from . import store

AHEAD_SEC = 60
CHAT_MAX = 200

OVERLAP_SEC = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS read_marks (
    chat TEXT PRIMARY KEY,
    at REAL NOT NULL,
    saved_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS read_marks_saved ON read_marks(saved_at);
"""


class ReadMarkError(ValueError):
    pass


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)


def read(chat: str, at: float, now: float | None = None) -> float | None:
    ""
    chat = (chat or "").strip()
    if not chat or len(chat) > CHAT_MAX:
        raise ReadMarkError("That chat isn't one I know.")
    if not math.isfinite(at) or at <= 0:
        raise ReadMarkError("That time isn't one I can use.")
    now = time.time() if now is None else now
    c = store.db()
    if at <= now + AHEAD_SEC:
        with c:
            c.execute("INSERT INTO read_marks (chat, at, saved_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(chat) DO UPDATE SET at = excluded.at, saved_at = excluded.saved_at "
                      "WHERE excluded.at > read_marks.at", (chat, at, now))
    row = c.execute("SELECT at FROM read_marks WHERE chat = ?", (chat,)).fetchone()
    return row["at"] if row else None


def changed(after: float = 0) -> dict[str, float]:
    ""
    rows = store.db().execute("SELECT chat, at FROM read_marks WHERE saved_at > ?",
                              (after - OVERLAP_SEC if after > 0 else 0,)).fetchall()
    return {r["chat"]: r["at"] for r in rows}
