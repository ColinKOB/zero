""
















import json
import logging
import sqlite3
import time

from . import store

log = logging.getLogger("taffy.checklist")

STATES = {"pending": "planned", "in_progress": "doing", "completed": "done"}
MAX_STEPS = 8
TEXT_MAX = 80

_items: dict[str, list[dict]] = {}
_revision = 0
KEEP_SEC = 30 * 86400


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS checklists (session TEXT PRIMARY KEY, items TEXT NOT NULL, "
              "updated_at REAL NOT NULL)")


def _held(session: str) -> list[dict]:
    ""
    if session not in _items:
        try:
            row = store.db().execute("SELECT items FROM checklists WHERE session=?", (session,)).fetchone()
            _items[session] = json.loads(row["items"]) if row else []
        except (sqlite3.Error, ValueError, TypeError):
            log.warning("could not read the saved checklist", exc_info=True)
            _items[session] = []
    return _items[session]


def _save(session: str) -> None:
    try:
        with store._lock:
            c = store.db()
            now = time.time()
            c.execute("INSERT OR REPLACE INTO checklists (session, items, updated_at) VALUES (?,?,?)",
                      (session, json.dumps(_items.get(session, [])), now))
            c.execute("DELETE FROM checklists WHERE updated_at<?", (now - KEEP_SEC,))
            c.commit()
    except sqlite3.Error:
        log.warning("could not save the checklist", exc_info=True)


def _clean(text) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= TEXT_MAX else t[: TEXT_MAX - 1].rstrip() + "…"


def _status(value) -> str | None:
    s = str(value or "").strip().lower()
    return s if s in STATES or s == "cancelled" else None


def write(session: str, args, turn: str = "") -> dict | None:
    ""

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    todos = args.get("todos") if isinstance(args, dict) else None
    if not isinstance(todos, list):
        return None
    before = shown(session)
    if args.get("merge"):
        items = _held(session)
        by_id = {i["id"]: i for i in items}
        for t in todos:
            if not isinstance(t, dict) or not str(t.get("id") or "").strip():
                continue
            cur = by_id.get(str(t["id"]).strip())
            if cur is None:
                cur = {"id": str(t["id"]).strip(), "content": "", "status": "pending"}
                by_id[cur["id"]] = cur
                items.append(cur)
            if t.get("content"):
                cur["content"] = str(t["content"])
            if _status(t.get("status")):
                cur["status"] = _status(t["status"])
            if "parent" in t:
                cur["parent"] = str(t["parent"] or "").strip()
            cur["turn"], cur["at"] = turn, time.time()
    else:
        fresh = {}
        for t in todos:
            if isinstance(t, dict) and str(t.get("id") or "").strip():
                fresh[str(t["id"]).strip()] = {"id": str(t["id"]).strip(), "content": str(t.get("content") or ""),
                                               "status": _status(t.get("status")) or "pending",
                                               "parent": str(t.get("parent") or "").strip(),
                                               "turn": turn, "at": time.time()}
        _items[session] = list(fresh.values())
    _save(session)
    after = shown(session) or {"steps": [], "done": 0, "total": 0}
    if after["steps"] == (before or {"steps": []})["steps"]:
        return None
    global _revision
    _revision = max(_revision + 1, int(time.time() * 1000))
    return {**after, "revision": _revision}








def _current(items: list[dict]) -> list[dict]:
    if not items:
        return items
    latest = max(items, key=lambda i: i.get("at") or 0).get("turn") or ""
    return [i for i in items if (i.get("turn") or "") == latest]


def shown(session: str, whole: bool = False) -> dict | None:
    ""

    items = _held(session)
    steps = [{"id": i["id"], "text": _clean(i["content"]), "state": STATES[i["status"]]}
             for i in (items if whole else _current(items))
             if i["status"] in STATES and not i.get("parent") and i["content"].strip()]
    steps = steps[:MAX_STEPS]
    if not steps:
        return None
    return {"steps": steps, "done": sum(s["state"] == "done" for s in steps), "total": len(steps)}


def done_steps(session: str) -> list[str]:
    ""

    return [s["text"] for s in (shown(session, whole=True) or {"steps": []})["steps"] if s["state"] == "done"]


def reset() -> None:
    ""
    global _revision
    _items.clear()
    _revision = 0
    try:
        with store._lock:
            store.db().execute("DELETE FROM checklists")
            store.db().commit()
    except sqlite3.Error:
        pass
