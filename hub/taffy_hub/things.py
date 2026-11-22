""


















import json
import logging

from . import board, store

log = logging.getLogger("taffy.things")

ANSWERED, DONE, DROPPED = "answered", "done", "dropped"
WAITING = ("blocked", "triage")


def asked_tasks() -> set[str]:
    ""
    rows = store.db().execute(
        "SELECT DISTINCT task_id FROM feed WHERE kind='choices' AND task_id IS NOT NULL AND task_id != '' "
        "AND json_extract(body, '$.state') = 'open' AND json_extract(body, '$.notice') IS NULL").fetchall()
    return {r[0] for r in rows}


def waits(task_id: str) -> bool:
    ""
    try:
        return (board.get_task(task_id) or {}).get("raw_status") in WAITING
    except Exception:
        log.warning("could not read task %s", task_id, exc_info=True)
        return False


def task_of(item: dict) -> str | None:
    if item["kind"] == "task":
        return item["id"]
    row = store.db().execute("SELECT task_id FROM feed WHERE id=? AND kind='choices'", (item["id"],)).fetchone()
    return row["task_id"] if row and row["task_id"] else None


def members(item: dict) -> list[dict]:
    ""

    task_id = task_of(item)
    if not task_id:
        return []
    out = []
    if waits(task_id):
        out.append({"kind": "task", "id": task_id})
    for r in store.db().execute("SELECT id, bot_id, body FROM feed WHERE kind='choices' AND task_id=? ORDER BY id",
                                (task_id,)).fetchall():
        try:
            card = json.loads(r["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") == "open" and not card.get("notice"):
            out.append({"kind": "card", "id": r["id"], "bot": r["bot_id"]})
    return out


def settle(item: dict, outcome: str, why: str, words: str | None = None, origin: dict | None = None) -> list[dict]:
    ""







    skip = [{k: m[k] for k in ("kind", "id")} for m in (item, origin) if m]
    settled = []
    for m in members(item):
        if {"kind": m["kind"], "id": m["id"]} in skip:
            continue
        if m["kind"] == "task" and outcome == ANSWERED and words and words.strip():
            ok, detail = board.answer_block(m["id"], words)
            if not ok:
                log.warning("the answer could not reach %s: %s", m["id"], str(detail)[-200:])
                continue
        elif m["kind"] == "card" and outcome in (DONE, DROPPED):
            from . import choices
            if not choices.close_passed(m["bot"], m["id"], why):
                continue
        else:
            continue
        settled.append(m)
    if settled:
        log.info("%s %s settled %s", item["kind"], item["id"], [f"{m['kind']}:{m['id']}" for m in settled])
    return settled
