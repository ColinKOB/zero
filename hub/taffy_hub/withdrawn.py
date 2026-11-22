""





















from __future__ import annotations

import logging
import time

from . import board, config, store

log = logging.getLogger("taffy.withdrawn")


LINE = "No longer needed: {question}"
QUESTION_MAX = 200
WAITING = ("blocked", "triage")

CARD_KINDS = ("choices", "kit", "login", "account", "code", "mac_ask", "mail", "text")
DONE_STATES = {"picked", "waiting", "answering", "answered", "signed_in", "connected", "allowed", "sent", "done"}


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _cards(task_id: str) -> list[dict]:
    ""
    import json
    out = []
    for r in store.db().execute(f"SELECT * FROM feed WHERE task_id=? AND kind IN ({','.join('?' * len(CARD_KINDS))}) "
                                "ORDER BY id", (task_id, *CARD_KINDS)):
        try:
            body = json.loads(r["body"] or "{}")
        except ValueError:
            body = {}
        out.append({**dict(r), "card": body if isinstance(body, dict) else {}})
    return out


def _answered_at(row: dict) -> float | None:
    ""

    card = row["card"]
    if not (card.get("state") in DONE_STATES or card.get("picked") or card.get("answer") or card.get("answers")):
        return None
    try:
        return float(card.get("at") or row.get("changed_at") or row.get("created_at") or 0)
    except (TypeError, ValueError):
        return float(row.get("created_at") or 0)


def _question(cards: list[dict], row: dict) -> str:
    ""



    texts = [_flat(c["card"].get("question") or (c.get("title") if c.get("kind") == "choices" else ""))
             for c in reversed(cards)] + [_flat(row.get("plain_title"))]
    text = next((t for t in texts if t), "")
    return text if len(text) <= QUESTION_MAX else text[:QUESTION_MAX].rsplit(" ", 1)[0] + "..."


def _drop_lines(feed_ids: list[int]) -> None:
    ""
    if not feed_ids:
        return
    from . import todo
    ids = {str(i) for i in feed_ids}
    with store._lock:
        c = store.db()
        rows = [r["id"] for r in c.execute("SELECT id, source FROM todo_lines WHERE status='open' AND source LIKE 'feed:%'")
                if (str(r["source"]).split(":") + [""])[1] in ids]
        for line_id in rows:
            c.execute("UPDATE todo_lines SET status='done', done_at=?, updated_at=? WHERE id=?",
                      (time.time(), time.time(), line_id))
        c.commit()
    if rows:
        todo._bump()


def settle(task_id: str) -> int:
    ""


    from . import apns, buzz, desk, quiet
    if not task_id:
        return 0
    task = board.get_task(task_id) or {}
    if task.get("raw_status") in WAITING and task.get("card"):
        return 0
    with store._lock:
        c = store.db()
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM feed WHERE kind='task_blocked' AND task_id=? AND COALESCE(quiet_word, '') != ? ORDER BY id",
            (task_id, quiet.ROUTED))]
        answered = [float(r["created_at"]) for r in c.execute(
            "SELECT created_at FROM feed WHERE kind='signin_decision' AND task_id=?", (task_id,))]
        cards = _cards(task_id)
    answered += [at for at in map(_answered_at, cards) if at is not None]
    if not rows:
        return 0
    rows = [r for r in rows if not any(at >= float(r["created_at"]) for at in answered)]
    if not rows:
        return 0
    told = buzz.for_feed([r["id"] for r in rows])
    changed = 0
    for r in rows:
        question = _question(cards, r)
        if told.get(r["id"]) == "buzz" and question:
            bot = config.BOTS_BY_ID.get(r["bot_id"] or "default")
            store.restate_feed(r["id"], "message", bot.name if bot else "Zero", LINE.format(question=question),
                               expect_kind="task_blocked", written_by="hub", written_for="person")
        else:
            quiet.save(r["id"], {"quiet": quiet.ROUTED, "quiet_why": "No longer needed"})
        changed += 1
    from . import notices, watcher
    desk.drop_task(task_id)
    watcher.drop_held(task_id)
    notices.drop_task(task_id)
    _drop_lines([r["id"] for r in rows])
    apns.withdraw_task(task_id)
    log.info("the card on %s went away without an answer: %d rows settled", task_id, changed)
    return changed
