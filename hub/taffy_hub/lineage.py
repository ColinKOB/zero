""




















import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config, store

_cache: dict[tuple[str, str], tuple[str, float]] = {}
_cache_lock = threading.Lock()
ROOT_TTL = 60.0


_FORK_MARKERS = ("_branched_from", "_delegate_from", "_reset_from")


def _state_db(bot_id: str) -> Path:
    return config.HERMES_HOME / "state.db" if bot_id == "default" else \
        config.HERMES_HOME / "profiles" / bot_id / "state.db"


def _parent_by_compression(conn: sqlite3.Connection, sid: str) -> str | None:
    row = conn.execute(
        "SELECT s.parent_session_id, s.model_config, s.source, p.end_reason FROM sessions s "
        "LEFT JOIN sessions p ON p.id = s.parent_session_id WHERE s.id = ?", (sid,)).fetchone()
    if not row or not row[0] or row[3] != "compression" or (row[2] or "") == "tool":
        return None
    try:
        cfg = json.loads(row[1] or "{}")
    except ValueError:
        cfg = {}
    if isinstance(cfg, dict) and any(cfg.get(k) for k in _FORK_MARKERS):
        return None
    return row[0]


def root(bot_id: str | None, session_id: str | None) -> str | None:
    ""

    if not session_id:
        return session_id
    for b in ([bot_id] if bot_id else list(config.BOTS_BY_ID)):
        found = _root_in(b, session_id)
        if found != session_id:
            return found
    return session_id


def _root_in(bot_id: str, session_id: str) -> str:
    now = time.time()
    with _cache_lock:
        hit = _cache.get((bot_id, session_id))


    if hit and (hit[0] != session_id or now - hit[1] < ROOT_TTL):
        return hit[0]
    path = _state_db(bot_id)
    if not path.exists():
        return session_id
    found = session_id
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            seen = {session_id}
            while True:
                parent = _parent_by_compression(conn, found)
                if not parent or parent in seen:
                    break
                seen.add(parent)
                found = parent
        finally:
            conn.close()
    except sqlite3.Error:
        return session_id
    with _cache_lock:
        _cache[(bot_id, session_id)] = (found, now)
    return found


def compacted_at(bot_id: str, session_ids: set[str]) -> float:
    ""


    ids = [s for s in session_ids if s]
    path = _state_db(bot_id)
    if not ids or not path.exists():
        return 0.0
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(f"SELECT MAX(timestamp) FROM messages WHERE session_id IN ({','.join('?' * len(ids))}) "
                               "AND _compressed_summary = 1", ids).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return 0.0
    return float(row[0] or 0) if row else 0.0


def same(bot_id: str | None, a: str | None, b: str | None) -> bool:
    ""
    if not a or not b:
        return False
    return a == b or root(bot_id, a) == root(bot_id, b)




def _key(m: dict) -> str | None:
    ts = m.get("ts") if "ts" in m else m.get("timestamp")
    if ts in (None, ""):
        return None
    try:
        ts = f"{float(ts):.6f}"
    except (TypeError, ValueError):
        ts = str(ts)
    return f"{m.get('role')}|{ts}|{m.get('tool_call_id') or ''}"


def stable_ids(bot_id: str, msgs: list[dict]) -> list[dict]:
    ""


    keyed = [(m, _key(m)) for m in msgs]
    keys = [k for _, k in keyed if k]
    if not keys:
        return msgs
    known: dict[str, str] = {}
    c = store.db()
    for i in range(0, len(keys), 400):
        part = keys[i:i + 400]
        q = "SELECT key, id FROM message_ids WHERE bot_id=? AND key IN (%s)" % ",".join("?" * len(part))
        known.update({r["key"]: r["id"] for r in c.execute(q, (bot_id, *part))})
    new = [(bot_id, k, str(m.get("id"))) for m, k in keyed if k and k not in known and m.get("id") is not None]
    if new:
        with store._lock:
            c.executemany("INSERT OR IGNORE INTO message_ids (bot_id, key, id) VALUES (?, ?, ?)", new)
            c.commit()
    out = []
    for m, k in keyed:
        if k and k in known and str(m.get("id")) != known[k]:
            m = {**m, "id": _typed(known[k], m.get("id"))}
        out.append(m)
    return out


def _typed(stored: str, like):
    ""
    if isinstance(like, int):
        try:
            return int(stored)
        except ValueError:
            return stored
    return stored




def remember_card(bot_id: str, call_ids: set[str]) -> None:
    if not bot_id or not call_ids:
        return
    with store._lock:
        c = store.db()
        c.executemany("INSERT OR IGNORE INTO card_calls (bot_id, call_id) VALUES (?, ?)",
                      [(bot_id, cid) for cid in call_ids])
        c.commit()


def carded(bot_id: str, call_ids: set[str]) -> set[str]:
    ""
    if not bot_id or not call_ids:
        return set()
    ids = list(call_ids)
    out: set[str] = set()
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        q = "SELECT call_id FROM card_calls WHERE bot_id=? AND call_id IN (%s)" % ",".join("?" * len(part))
        out |= {r["call_id"] for r in store.db().execute(q, (bot_id, *part))}
    return out
