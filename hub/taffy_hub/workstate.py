""







import asyncio
import json
import logging
import math
import sqlite3
import time

from . import store, work

log = logging.getLogger("taffy.workstate")


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS work_user_state (
            id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
            handled_revision TEXT, done INTEGER NOT NULL DEFAULT 0,
            snoozed_until REAL, last_read_revision TEXT
        );
        CREATE TABLE IF NOT EXISTS work_state_operations (
            operation_id TEXT PRIMARY KEY, item_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, response TEXT NOT NULL, at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS work_refresh_control (
            id INTEGER PRIMARY KEY CHECK(id=1), started_at REAL NOT NULL
        );
    """)


class StateError(Exception):
    def __init__(self, message: str, status: int = 400, current: dict | None = None):
        super().__init__(message)
        self.status, self.current = status, current


def _row(item_id: str) -> dict:
    r = store.db().execute("SELECT * FROM work_user_state WHERE id=?", (item_id,)).fetchone()
    return dict(r) if r else {"id": item_id, "revision": 0, "handled_revision": None, "done": 0,
                             "snoozed_until": None, "last_read_revision": None}


def _state(row: dict, source_revision: str) -> dict:
    same = row["handled_revision"] == source_revision
    return {"revision": row["revision"], "done": bool(same and row["done"]),
            "snoozed_until": row["snoozed_until"] if same else None,
            "last_read_revision": row["last_read_revision"],
            "unread": row["last_read_revision"] != source_revision,
            "reopened_reason": "source_changed" if row["handled_revision"] and not same else None}


def decorate(item: dict) -> dict:
    return {**item, "user_state": _state(_row(item["id"]), item["revision"])}


def visible(item: dict) -> bool:
    ""
    state = item["user_state"]
    return not state["done"] and (state["snoozed_until"] is None or state["snoozed_until"] <= time.time()) and \
        (item.get("reply_draft") or {}).get("state") != "sent"


def done_at(item_id: str) -> float | None:
    ""
    r = store.db().execute("SELECT response, at FROM work_state_operations WHERE item_id=? ORDER BY at DESC LIMIT 1",
                           (item_id,)).fetchone()
    if not r or not json.loads(r["response"]).get("user_state", {}).get("done"):
        return None
    return r["at"]


def _target(item_id: str) -> dict:
    c = store.db()
    source = c.execute("SELECT * FROM work_sources WHERE id=? AND active=1", (item_id,)).fetchone()
    if source:
        body = work._json(source["body"])
        if work._visible(body):
            return body
    else:
        project = c.execute("SELECT body FROM work_projects WHERE id=? AND active=1", (item_id,)).fetchone()
        if project:
            return {**work._json(project["body"]), "kind": "project_page"}
    raise StateError("That item is no longer here.", 404)


def _group_members(item_id: str, action: str) -> list[str]:
    ""

    c = store.db()
    if action == "undo":
        r = c.execute("SELECT response FROM work_state_operations WHERE item_id=? ORDER BY at DESC LIMIT 1",
                      (item_id,)).fetchone()
        members = json.loads(r["response"]).get("members") if r else None
    elif action == "snooze":
        from . import workdue
        needs, _cleared = workdue.arrange([s for s in work._sources() if s["section"] == "needs_you" and visible(s)], [])
        members = next(([m["id"] for m in n["members"]] for n in needs if n["id"] == item_id), None)
    else:
        raise StateError("Open this item to answer or review it.")
    if not members:
        raise StateError("That item is no longer here.", 404)
    return members


def _change_group(item_id: str, action: str, source_revision: str, operation_id: str,
                  snoozed_until: float | None) -> dict:
    c = store.db()
    fingerprint = work._revision([item_id, action, source_revision, snoozed_until])
    receipt = c.execute("SELECT * FROM work_state_operations WHERE operation_id=?", (operation_id,)).fetchone()
    if receipt:
        if receipt["item_id"] != item_id or receipt["fingerprint"] != fingerprint:
            raise StateError("That action was already used for something else.", 409)
        return json.loads(receipt["response"])
    if action == "snooze" and (snoozed_until is None or not math.isfinite(snoozed_until) or snoozed_until <= time.time()):
        raise StateError("Choose a future time.")
    members, states = _group_members(item_id, action), []
    try:
        for member in members:
            try:
                revision = _target(member)["revision"]
            except StateError:
                continue
            row = _row(member)
            row.update(handled_revision=revision if action == "snooze" else None, done=0,
                       snoozed_until=snoozed_until if action == "snooze" else None, revision=row["revision"] + 1)
            save(c, row)
            states.append(_state(row, revision))
        if not states:
            raise StateError("That item is no longer here.", 404)
        response = {"id": item_id, "source_revision": source_revision, "user_state": states[0], "members": members}
        c.execute("INSERT INTO work_state_operations(operation_id,item_id,fingerprint,response,at) VALUES(?,?,?,?,?)",
                  (operation_id, item_id, fingerprint, json.dumps(response), time.time()))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return response


def change(item_id: str, action: str, expected_revision: int, source_revision: str,
           operation_id: str, snoozed_until: float | None = None) -> dict:
    with store._lock:
        work.rebuild()
        if item_id.startswith("group:"):
            return _change_group(item_id, action, source_revision, operation_id, snoozed_until)
        target = _target(item_id)
        c = store.db()
        fingerprint = work._revision([item_id, action, expected_revision, source_revision, snoozed_until])
        receipt = c.execute("SELECT * FROM work_state_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if receipt:
            if receipt["item_id"] != item_id or receipt["fingerprint"] != fingerprint:
                raise StateError("That action was already used for something else.", 409)
            return json.loads(receipt["response"])
        row = _row(item_id)
        current = {"id": item_id, "source_revision": target["revision"], "user_state": _state(row, target["revision"])}
        if source_revision != target["revision"] or expected_revision != row["revision"]:
            raise StateError("This item changed on another device. Refresh and try again.", 409, current)
        if action in ("done", "snooze"):
            if target.get("section") != "needs_you":
                raise StateError("Choose an item that needs your attention.")
            if action == "done" and target["kind"] in ("choices", "approval", "team_need"):
                raise StateError("Open this item to answer or review it.")
            if action == "snooze" and (snoozed_until is None or not math.isfinite(snoozed_until) or snoozed_until <= time.time()):
                raise StateError("Choose a future time.")
            row.update(handled_revision=source_revision, done=int(action == "done"),
                       snoozed_until=snoozed_until if action == "snooze" else None)
        elif action == "undo":
            row.update(handled_revision=None, done=0, snoozed_until=None)
        elif action == "mark_read":
            row["last_read_revision"] = source_revision
        else:
            raise StateError("Choose Done, Snooze, Undo or Mark Read.")
        row["revision"] += 1
        response = {"id": item_id, "source_revision": source_revision, "user_state": _state(row, source_revision)}
        try:
            save(c, row)
            c.execute("INSERT INTO work_state_operations(operation_id,item_id,fingerprint,response,at) VALUES(?,?,?,?,?)",
                      (operation_id, item_id, fingerprint, json.dumps(response), time.time()))
            c.commit()
        except Exception:
            c.rollback()
            raise
        _follow_through(target, action)
        if action in ("done", "snooze", "undo"):
            _carry_to_folded(item_id, action, snoozed_until)
        return response


def _carry_to_folded(item_id: str, action: str, snoozed_until: float | None) -> None:
    ""


    from . import todogate
    c = store.db()
    try:
        for member in todogate.folded_with(item_id):
            try:
                target = _target(member)
            except StateError:
                continue
            row = _row(member)
            row.update(handled_revision=None if action == "undo" else target["revision"], done=int(action == "done"),
                       snoozed_until=snoozed_until if action == "snooze" else None, revision=row["revision"] + 1)
            save(c, row)
            _follow_through(target, action)
        c.commit()
    except Exception:
        c.rollback()
        log.warning("could not carry a change to the rows folded into %s", item_id, exc_info=True)


def _follow_through(target: dict, action: str) -> None:
    ""


    from . import remindermirror, todo
    if action not in ("done", "undo"):
        return
    try:
        if target.get("kind") == "task":
            task_id = str(target["id"]).removeprefix("task:")
            if action == "done":
                _task_done_soon(task_id)
            else:
                _finishing.pop(task_id, None)
        elif target.get("kind") == "todo" and todo.line_of(target["id"]):
            todo.finish(todo.line_of(target["id"]), "hub", done=action == "done")
        elif target.get("kind") == "reminder" and action == "done":
            remindermirror.complete_soon(target["id"])
        elif target.get("kind") == "reminder":
            remindermirror.cancel(target["id"])
    except todo.TodoError:
        log.info("the line behind %s is gone", target.get("id"))







TASK_DONE_NOTE = "You marked this done in Needs You."
CARD_WHY = "You marked the task done."
_finishing: dict[str, object] = {}


def _task_done_soon(task_id: str) -> None:
    ""

    from . import signin
    token = object()
    _finishing[task_id] = token
    try:
        asyncio.run_coroutine_threadsafe(_finish_task(task_id, token), signin.loop)
    except Exception:
        log.warning("could not start closing task %s", task_id, exc_info=True)
        _finishing.pop(task_id, None)
        _task_not_done(task_id)


async def _wait_out(task_id: str) -> None:
    ""
    from . import remindermirror
    await asyncio.sleep(remindermirror.UNDO_SEC)


async def _finish_task(task_id: str, token: object) -> None:
    from . import board, things
    await _wait_out(task_id)
    if _finishing.get(task_id) is not token:
        return
    _finishing.pop(task_id, None)
    try:
        ok, detail = await asyncio.to_thread(board.drop_waiting, task_id, TASK_DONE_NOTE)
    except Exception as e:
        ok, detail = False, str(e)
    if not ok:

        try:
            ok, forced = await asyncio.to_thread(board.close_while_running, task_id, TASK_DONE_NOTE)
            detail = detail if forced == "not running" else forced
        except Exception as e:
            ok, detail = False, str(e)
    if not ok:

        log.info("Done could not close task %s: %s", task_id, str(detail)[-200:])
        _task_not_done(task_id)
        return
    try:
        await asyncio.to_thread(things.settle, {"kind": "task", "id": task_id}, things.DROPPED, CARD_WHY)
    except Exception:
        log.warning("could not close the open questions of %s", task_id, exc_info=True)


def _task_not_done(task_id: str) -> None:
    with store._lock:
        c = store.db()
        c.execute("UPDATE work_user_state SET done=0, handled_revision=NULL, revision=revision+1 WHERE id=?",
                  (f"task:{task_id}",))
        c.commit()


def save(c: sqlite3.Connection, row: dict) -> None:
    c.execute("INSERT INTO work_user_state(id,revision,handled_revision,done,snoozed_until,last_read_revision) "
              "VALUES(:id,:revision,:handled_revision,:done,:snoozed_until,:last_read_revision) "
              "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,handled_revision=excluded.handled_revision,"
              "done=excluded.done,snoozed_until=excluded.snoozed_until,last_read_revision=excluded.last_read_revision", row)


def reserve_refresh() -> dict:
    with store._lock:
        c, now = store.db(), time.time()
        row = c.execute("SELECT started_at FROM work_refresh_control WHERE id=1").fetchone()
        wait = max(0, 30 - (now - row[0])) if row else 0
        if wait:
            return {"accepted": False, "retry_after": math.ceil(wait)}
        c.execute("INSERT INTO work_refresh_control(id,started_at) VALUES(1,?) "
                  "ON CONFLICT(id) DO UPDATE SET started_at=excluded.started_at", (now,))
        c.commit()
        return {"accepted": True, "retry_after": 30}
