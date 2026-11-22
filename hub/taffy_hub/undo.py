""














from __future__ import annotations

import json
import logging
import time

from . import store

log = logging.getLogger("taffy.undo")

WINDOW_SEC = 10 * 60
TOO_LATE = "It is too late to take that back."
ONCE = "That was already taken back once."
NOT_YOURS = "That wasn't something you closed, so there is nothing to take back."


class UndoError(Exception):
    ""


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS undone_dismissals (
        kind TEXT NOT NULL,        -- question or approval
        item INTEGER NOT NULL,     -- the question's feed id, or the okay's request id
        at REAL NOT NULL,
        PRIMARY KEY (kind, item))""")


def now() -> float:
    return time.time()


def _claim(kind: str, item: int) -> None:
    ""
    with store._lock:
        cur = store.db().execute("INSERT OR IGNORE INTO undone_dismissals (kind, item, at) VALUES (?,?,?)",
                                 (kind, item, now()))
        store.db().commit()
    if cur.rowcount != 1:
        raise UndoError(ONCE)


def _release(kind: str, item: int) -> None:
    with store._lock:
        store.db().execute("DELETE FROM undone_dismissals WHERE kind=? AND item=?", (kind, item))
        store.db().commit()


def _used(kind: str, item: int) -> bool:
    return store.db().execute("SELECT 1 FROM undone_dismissals WHERE kind=? AND item=?", (kind, item)).fetchone() is not None


def question(bot_id: str, feed_id: int) -> dict:
    ""
    from . import choices, notices
    row = store.db().execute("SELECT body FROM feed WHERE id=? AND kind='choices' AND bot_id=?", (feed_id, bot_id)).fetchone()
    try:
        card = json.loads(row["body"] or "{}") if row else {}
    except ValueError:
        card = {}
    if not row or card.get("state") != "dismissed":
        raise UndoError(NOT_YOURS)
    if _used("question", feed_id):
        raise UndoError(ONCE)
    since = now() - WINDOW_SEC
    _claim("question", feed_id)
    try:
        out = notices.undismiss(feed_id, since) if card.get("notice") else choices.undismiss(bot_id, feed_id, since)
    except Exception:
        _release("question", feed_id)
        raise
    if isinstance(out, str):
        _release("question", feed_id)
        raise UndoError(out)
    log.info("the X on %s's question %s was taken back", bot_id, feed_id)
    return out


def _receipt_titles(r: dict) -> list[str]:
    ""
    from . import receipts
    kind = r.get("kind") or "signin"
    if kind in ("signin", "navigate"):
        return [receipts.answered(False, receipts.SIGN_IN + (r.get("site") or ""))]
    return [receipts.answered(False, x) for x in (r.get("title"), r.get("reason"), kind) if x]


def approval(req_id: int) -> dict:
    ""
    from . import consent, teams
    r = consent.get_request(req_id)
    if not r or r.get("status") != "denied":
        raise UndoError(NOT_YOURS)
    if teams.member_of(r.get("bot_id") or ""):
        raise UndoError("That okay was a team's, and the team has already heard your answer.")
    if _used("approval", req_id):
        raise UndoError(ONCE)
    since = now() - WINDOW_SEC
    if float(r.get("decided") or 0) < since:
        raise UndoError(TOO_LATE)
    _claim("approval", req_id)
    try:
        with consent.db() as c:
            changed = c.execute("UPDATE requests SET status='pending', decided=NULL WHERE id=? AND status='denied' "
                                "AND decided>=?", (req_id, since)).rowcount
    except Exception:
        _release("approval", req_id)
        raise
    if changed != 1:
        _release("approval", req_id)
        raise UndoError(TOO_LATE)
    titles = _receipt_titles(r)
    with store._lock:
        line = store.db().execute(
            f"SELECT id FROM feed WHERE kind='signin_decision' AND bot_id=? AND created_at>=? AND title IN "
            f"({','.join('?' * len(titles))}) ORDER BY id DESC LIMIT 1",
            (r["bot_id"], float(r["decided"]) - 5, *titles)).fetchone()
        if line:
            store.db().execute("DELETE FROM feed WHERE id=?", (line["id"],))
            store.db().commit()
    log.info("Not Now on %s's okay %s was taken back", r["bot_id"], req_id)
    return {**r, "status": "pending", "decided": None}
