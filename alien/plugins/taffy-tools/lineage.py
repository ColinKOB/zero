"""The conversation a session belongs to, through a compaction that moved it (the hub's lineage.py, Oct 10).

When Hermes compacts a chat by rotation (compression.in_place: false, or its fallback when an in-place commit cannot
run), the chat continues in a child session and the Zip's tools receive the child's id. The hub's bot_sessions,
the cards and the consent passes name the first session. `root` maps any session of a compression chain to that
first one, so the lane checks (a teammate's question must never get the chat's permissions), the password-fill
lock and the passes hold across the move. In-place compaction, Hermes's default, keeps the id and needs none of this.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

_cache: dict[tuple[str, str], tuple[str, float]] = {}
_lock = threading.Lock()
TTL = 60.0
_FORK_MARKERS = ("_branched_from", "_delegate_from", "_reset_from")


def _state_db() -> Path | None:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()) / "state.db"
    except Exception:
        return None


def root(session_id: str) -> str:
    """The first session of `session_id`'s compression chain; itself when it has none or the store can't be read."""
    if not session_id:
        return session_id
    path = _state_db()
    if path is None or not path.exists():
        return session_id
    now = time.time()
    key = (str(path), session_id)
    with _lock:
        hit = _cache.get(key)
    if hit and (hit[0] != session_id or now - hit[1] < TTL):
        return hit[0]
    found = session_id
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            seen = {session_id}
            while True:
                row = conn.execute(
                    "SELECT s.parent_session_id, s.model_config, s.source, p.end_reason FROM sessions s "
                    "LEFT JOIN sessions p ON p.id = s.parent_session_id WHERE s.id = ?", (found,)).fetchone()
                if not row or not row[0] or row[3] != "compression" or (row[2] or "") == "tool":
                    break
                try:
                    cfg = json.loads(row[1] or "{}")
                except ValueError:
                    cfg = {}
                if isinstance(cfg, dict) and any(cfg.get(k) for k in _FORK_MARKERS):
                    break
                if row[0] in seen:
                    break
                seen.add(row[0])
                found = row[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return session_id
    with _lock:
        _cache[key] = (found, now)
    return found
