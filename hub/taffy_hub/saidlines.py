""























from __future__ import annotations

import logging
import re
import time

from . import store

log = logging.getLogger("taffy.saidlines")

KINDS = ("message", "message_local")


BEFORE_SEC = 2 * 3600
AFTER_SEC = 300

KEY_WORDS = 30

EMPTY_LOOK_BACK_SEC = 24 * 3600


STARTS = 1794805200
ID_PREFIX = "said-"


def _norm(text) -> str:
    return " ".join(re.findall(r"\w+", str(text or "").lower()))


def _key(text) -> str:
    return " ".join(_norm(text).split()[:KEY_WORDS])


def _ts(m: dict) -> float | None:
    ts = m.get("ts")
    return float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None


def _holds(said: str, key: str) -> bool:
    ""

    return re.search(rf"(?<!\S){re.escape(key)}\w*(?!\S)", said) is not None


def weave(bot_id: str, msgs: list[dict], now: float | None = None) -> list[dict]:
    ""


    stamps = [t for t in (_ts(m) for m in msgs) if t is not None]
    since = max(STARTS, min(stamps) if stamps else (time.time() if now is None else now) - EMPTY_LOOK_BACK_SEC)
    try:
        rows = store.db().execute(
            f"SELECT * FROM feed WHERE bot_id = ? AND kind IN ({','.join('?' * len(KINDS))}) "
            "AND created_at >= ? ORDER BY id LIMIT 400", (bot_id, *KINDS, int(since))).fetchall()
    except Exception:
        log.warning("could not read the lines the hub wrote for %s", bot_id, exc_info=True)
        return msgs
    if not rows:
        return msgs

    said = [(_ts(m), [_norm(m.get(k)) for k in ("text", "display", "full") if m.get(k)])
            for m in msgs if m.get("role") == "assistant"]
    lines: list[dict] = []
    keys: list[tuple[str, float]] = []
    from . import chatgate
    for r in rows:
        key, at = _key(r["body"]), float(r["created_at"])
        shown = chatgate.shown_as(dict(r))
        if not key or shown not in (chatgate.BUBBLE, chatgate.LINE):
            continue
        if any((ts is None or at - BEFORE_SEC <= ts <= at + AFTER_SEC) and any(_holds(w, key) for w in words)
               for ts, words in said):
            continue

        if any(k == key and abs(at - t) <= AFTER_SEC for k, t in keys):
            continue
        keys.append((key, at))
        text = str(r["body"]).strip()


        lines.append({"id": f"{ID_PREFIX}{r['id']}", "role": "assistant",
                      "class": "bot" if shown == chatgate.BUBBLE else "hub_line", "text": text, "display": text,
                      "ts": at})
    if not lines:
        return msgs
    out: list[dict] = []
    for m in msgs:
        ts = _ts(m)
        while lines and ts is not None and lines[0]["ts"] < ts:
            out.append(lines.pop(0))
        out.append(m)
    return out + lines


def held(bot_id: str, rows: list[dict]) -> set[int] | None:
    ""


    import sqlite3

    from . import lineage, recentchat
    rows = [r for r in rows if r.get("kind") in KINDS]
    if not rows:
        return set()
    root = store.session_for(bot_id)
    path = lineage._state_db(bot_id)
    if not root or not path.exists():
        return None
    since = min(float(r["created_at"]) for r in rows) - BEFORE_SEC
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            ids = recentchat._chat_sessions(conn, root)
            said = [(float(ts), _norm(content)) for content, ts in conn.execute(
                f"SELECT content, timestamp FROM messages WHERE session_id IN ({','.join('?' * len(ids))}) "
                "AND role='assistant' AND timestamp >= ?", (*ids, since))]
        finally:
            conn.close()
    except sqlite3.Error:
        log.warning("could not read %s's chat for the lines it holds", bot_id, exc_info=True)
        return None
    out = set()
    for r in rows:
        key, at = _key(r.get("body")), float(r["created_at"])
        if key and any(at - BEFORE_SEC <= ts <= at + AFTER_SEC and _holds(words, key) for ts, words in said):
            out.add(int(r["id"]))
    return out
