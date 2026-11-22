""







from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta

from . import config, home, store, work





AREA_WORDS = (("School", ("school", "scholar", "class", "course")),
              ("Clients", ("client", "connect", "localsit", "sites")),
              ("Money", ("money", "saver", "budget")),
              ("Career", ("career", "job")))
PERSONAL = "Personal"
AREA_ORDER = ("School", "Clients", "Career", "Money")
QUIET_DAYS = 14


DONE_DAYS = 2
ACTIONS = ("archive", "restore", "pin", "unpin", "rename", "move_area", "merge", "unmerge", "keep", "seen", "undo")
FIELDS = ("archived_at", "pin_rank", "title", "area", "merged_into", "merged_title", "kept_revision",
          "archive_seen", "back_reason", "back_at")


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS work_project_state (
            id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
            archived_at REAL, pin_rank REAL, title TEXT, area TEXT,
            merged_into TEXT, merged_title TEXT, kept_revision TEXT
        );
        CREATE TABLE IF NOT EXISTS work_areas (bot_id TEXT PRIMARY KEY, area TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS work_project_operations (
            operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            response TEXT NOT NULL, before TEXT NOT NULL, at REAL NOT NULL, undone INTEGER NOT NULL DEFAULT 0
        );
    """)


    columns = {r[1] for r in c.execute("PRAGMA table_info(work_project_state)")}
    for name, definition in {"archive_seen": "TEXT", "back_reason": "TEXT", "back_at": "REAL"}.items():
        if name not in columns:
            c.execute(f"ALTER TABLE work_project_state ADD COLUMN {name} {definition}")


class ProjectError(Exception):
    def __init__(self, message: str, status: int = 400, current: dict | None = None):
        super().__init__(message)
        self.status, self.current = status, current


def _blank(project_id: str) -> dict:
    return {"id": project_id, "revision": 0, **{f: None for f in FIELDS}}


def _rows() -> dict[str, dict]:
    return {r["id"]: dict(r) for r in store.db().execute("SELECT * FROM work_project_state")}


def _row(project_id: str) -> dict:
    r = store.db().execute("SELECT * FROM work_project_state WHERE id=?", (project_id,)).fetchone()
    return dict(r) if r else _blank(project_id)


def _public(row: dict) -> dict:
    return {"id": row["id"], "revision": row["revision"], "archived_at": row["archived_at"],
            "pin_rank": row["pin_rank"], "title": row["title"], "area": row["area"], "merged_into": row["merged_into"]}


def merges() -> dict[str, str]:
    return {r["id"]: r["merged_into"] for r in store.db().execute(
        "SELECT id,merged_into FROM work_project_state WHERE merged_into IS NOT NULL")}


def resolve(project_id: str, moves: dict[str, str], present) -> str:
    ""
    out, seen, at = project_id, {project_id}, project_id
    while (at := moves.get(at)) and at not in seen:
        seen.add(at)
        if at in present:
            out = at
    return out


def zip_area(bot_id: str | None) -> str:
    saved = store.db().execute("SELECT area FROM work_areas WHERE bot_id=?", (bot_id or "",)).fetchone()
    if saved:
        return saved["area"]
    if not bot_id or bot_id == "default":
        return PERSONAL
    bot = config.BOTS_BY_ID.get(bot_id)
    words = f"{bot_id} {getattr(bot, 'name', '')}".casefold()
    return next((area for area, keys in AREA_WORDS if any(k in words for k in keys)), PERSONAL)


def set_zip_area(bot_id: str, area: str) -> None:
    with store._lock:
        c = store.db()
        c.execute("INSERT INTO work_areas(bot_id,area) VALUES(?,?) ON CONFLICT(bot_id) DO UPDATE SET area=excluded.area",
                  (bot_id, _clean(area, 40, "Choose an area name.")))
        c.commit()


def _owner(project: dict, sources: list[dict], worked: dict[str, list[str]]) -> str | None:
    ""


    if project.get("bot_id") and project["bot_id"] != "default":
        return project["bot_id"]
    helpers = [s["bot_id"] for s in sources if s.get("bot_id") and s["bot_id"] != "default"]
    if helpers:
        return max(helpers, key=helpers.count)
    ranked = Counter(worked.get(project["id"], [])).most_common(2)
    return ranked[0][0] if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]) else project.get("bot_id")


def worked_on(projects: list[dict]) -> dict[str, list[str]]:
    ""





    from . import made
    if not projects:
        return {}
    c, ids, out = store.db(), {p["id"] for p in projects}, {}
    for r in c.execute("SELECT project_id,bot_id FROM work_made WHERE active=1"):
        if r["project_id"] in ids and r["bot_id"] != "default" and r["bot_id"] in config.BOTS_BY_ID:
            out.setdefault(r["project_id"], []).append(r["bot_id"])
    names = {p["id"]: [" " + " ".join(n) + " " for n in made.name_runs(p["title"])] for p in projects}
    if not any(names.values()):
        return out
    for pair, text in _team_words(c):
        zips = {z for z in pair if z != "default" and z in config.BOTS_BY_ID}
        for pid, said in names.items():
            if zips and any(n in text for n in said):
                out.setdefault(pid, []).extend(zips)
    return out




_team_seen: dict = {"db": None, "rows": {}}


def _team_words(c) -> list[tuple[tuple, str]]:
    if _team_seen["db"] != str(config.HUB_DB):
        _team_seen.update(db=str(config.HUB_DB), rows={})
    from . import made
    seen, now = _team_seen["rows"], {}
    order, changed = [], []
    for r in c.execute("SELECT id, COALESCE(changed_at, created_at) AS at FROM feed WHERE kind='team'"):
        order.append(r["id"])
        if (got := seen.get(r["id"])) and got[0] == r["at"]:
            now[r["id"]] = got
        else:
            changed.append(r["id"])
    for start in range(0, len(changed), 500):
        part = changed[start:start + 500]
        for r in c.execute(f"SELECT id, COALESCE(changed_at, created_at) AS at, body FROM feed WHERE id IN "
                           f"({','.join('?' * len(part))})", part):
            b = work._json(r["body"])
            now[r["id"]] = (r["at"], (b.get("from"), b.get("to")),
                            " " + " ".join(made._tokens(f"{b.get('text') or ''} {b.get('result') or ''}")) + " ")
    _team_seen["rows"] = now
    return [(now[i][1], now[i][2]) for i in order if i in now]


def annotate(projects: list[dict], sources: list[dict], now: float | None = None) -> dict:
    ""






    from . import workstate
    now = time.time() if now is None else now
    rows, by_project = _rows(), {}
    for s in sources:
        by_project.setdefault(s["project_id"], []).append(s)
    present = {p["id"] for p in projects}
    aliases: dict[str, list[str]] = {}
    for pid, row in rows.items():
        if row["merged_into"] and row["merged_title"]:
            aliases.setdefault(resolve(pid, merges(), present), []).append(row["merged_title"])
    worked = worked_on([p for p in projects if p["id"] != work.OTHER and p.get("bot_id") in (None, "default") and
                        not any(s.get("bot_id") not in (None, "default") for s in by_project.get(p["id"], []))])
    for p in projects:
        row, mine = rows.get(p["id"]) or _blank(p["id"]), by_project.get(p["id"], [])
        asks = [s for s in mine if s["section"] == "needs_you" and workstate.visible(s)]
        dates = [s["date"] for s in mine if s.get("date") is not None]
        owner = _owner(p, mine, worked)
        p.update(natural_title=p["title"], title=row["title"] or p["title"],
                 aliases=sorted(set(aliases.get(p["id"], []) + ([p["title"]] if row["title"] else []))),
                 owner_bot_id=owner, area=None if p["id"] == work.OTHER else row["area"] or zip_area(owner),
                 archived_at=row["archived_at"], pin_rank=row["pin_rank"], state_revision=row["revision"],
                 back={"reason": row["back_reason"], "at": row["back_at"]} if row["back_at"] else None,
                 needs_you_count=len(asks), last_activity_at=max((s["at"] for s in mine), default=None))
        upcoming = any(d >= now for d in dates)
        p["quiet"] = not upcoming and (p["last_activity_at"] or 0) < now - QUIET_DAYS * 86400


        p["looks_done"] = bool(p["id"] != work.OTHER and not row["archived_at"] and dates and not upcoming and not asks
                               and not any(s["section"] == "in_progress" for s in mine)
                               and row["kept_revision"] != p["revision"])



        unseen = 0 if row["archived_at"] else sum(s["section"] == "updates" and s["user_state"]["unread"] for s in mine)
        p.update(unseen_updates=unseen, user_state={**p["user_state"], "unread": bool(unseen and p["user_state"]["unread"])},
                 status=status(p, now))
    live = [p for p in projects if p["id"] != work.OTHER]
    pinned = sorted((p for p in live if p["pin_rank"] is not None and not p["archived_at"]), key=lambda p: p["pin_rank"])
    rest = [p for p in live if p["pin_rank"] is None and not p["archived_at"]]
    names = sorted({p["area"] for p in rest}, key=lambda a: (a == PERSONAL, a not in AREA_ORDER,
                                                             AREA_ORDER.index(a) if a in AREA_ORDER else 0, a.casefold()))

    def soonest(p):
        upcoming = [s["date"] for s in by_project.get(p["id"], []) if s.get("date") is not None and s["date"] >= now]
        return (not upcoming, min(upcoming, default=0), -(p["last_activity_at"] or 0), p["title"].casefold())
    return {"pinned": [p["id"] for p in pinned],
            "areas": [{"name": a, "project_ids": [p["id"] for p in sorted((p for p in rest if p["area"] == a), key=soonest)]}
                      for a in names],
            "archived": [p["id"] for p in sorted((p for p in live if p["archived_at"]), key=lambda p: -p["archived_at"])]}


def updated_words(at: float, now: float) -> str:
    ""
    when, today = datetime.fromtimestamp(at, home.TZ), datetime.fromtimestamp(now, home.TZ).date()
    if when.date() >= today:
        return "Updated " + when.strftime("%-I:%M %p")
    if when.date() == today - timedelta(days=1):
        return "Updated yesterday"
    if when.date() > today - timedelta(days=7):
        return "Updated " + when.strftime("%A")
    return "Updated " + when.strftime("%b %-d" if when.year == today.year else "%b %-d, %Y")


def status(p: dict, now: float) -> dict | None:
    ""

    if p["archived_at"]:
        return {"kind": "archived", "text": "Archived " + datetime.fromtimestamp(p["archived_at"], home.TZ).strftime("%b %-d")}
    if p.get("back"):
        return {"kind": "back", "text": "Back"}
    if n := p["needs_you_count"]:
        return {"kind": "needs_you", "text": f"{n} need{'s' if n == 1 else ''} you"}
    if p["looks_done"]:
        return {"kind": "done", "text": "Done?"}
    if p["last_activity_at"]:
        return {"kind": "updated", "text": updated_words(p["last_activity_at"], now)}
    return None


def _clean(text: str | None, limit: int, message: str) -> str:
    out = " ".join((text or "").split())
    if not out or len(out) > limit:
        raise ProjectError(message)
    return out


def _save(c: sqlite3.Connection, row: dict) -> None:
    names = ("id", "revision", *FIELDS)
    c.execute(f"INSERT INTO work_project_state({','.join(names)}) VALUES({','.join(':' + n for n in names)}) "
              f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{n}=excluded.{n}' for n in names[1:])}",
              {n: row[n] for n in names})


def _active() -> dict[str, dict]:
    return {r["id"]: work._json(r["body"]) for r in store.db().execute("SELECT id,body FROM work_projects WHERE active=1")}


def change(project_id: str, action: str, operation_id: str, expected_revision: int | None = None,
           title: str | None = None, area: str | None = None, target_id: str | None = None,
           rank: float | None = None, undo_operation_id: str | None = None) -> dict:
    ""

    if action not in ACTIONS:
        raise ProjectError("Choose Archive, Restore, Pin, Unpin, Rename, Move to Area, Merge, Keep or Undo.")
    with store._lock:
        work.rebuild()
        c = store.db()
        fingerprint = work._revision([project_id, action, expected_revision, title, area, target_id, rank, undo_operation_id])
        receipt = c.execute("SELECT * FROM work_project_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if receipt:
            if receipt["project_id"] != project_id or receipt["fingerprint"] != fingerprint:
                raise ProjectError("That action was already used for something else.", 409)
            return json.loads(receipt["response"])
        if project_id == work.OTHER:
            raise ProjectError("Other holds what fits no project, so it stays as it is.")
        active, row = _active(), _row(project_id)
        if project_id not in active and not row["merged_into"]:
            raise ProjectError("That project is no longer here.", 404)
        if expected_revision is not None and expected_revision != row["revision"]:
            raise ProjectError("This project changed on another device. Refresh and try again.", 409, _public(row))
        before, cleared = dict(row), []
        if row["merged_into"] and action not in ("unmerge", "undo"):
            raise ProjectError("That project was merged into another one. Undo the merge first.")
        if action == "archive":
            cleared = _clear_mail(c, project_id)
            row.update(archived_at=time.time(), back_reason=None, back_at=None,
                       archive_seen=json.dumps(_waiting(project_id)))
        elif action == "restore":

            row.update(archived_at=None, kept_revision=active[project_id]["revision"] if project_id in active else None)
        elif action == "pin":
            if row["archived_at"]:
                raise ProjectError("Restore this project before pinning it.")
            ranks = [r["pin_rank"] for r in _rows().values() if r["pin_rank"] is not None and r["id"] != project_id]
            row["pin_rank"] = float(rank) if rank is not None else max(ranks, default=0) + 1
        elif action == "unpin":
            row["pin_rank"] = None
        elif action == "rename":
            name = _clean(title, 120, "Choose a name up to 120 characters.")
            row["title"] = None if name == active[project_id]["title"] else name
        elif action == "move_area":
            row["area"] = _clean(area, 40, "Choose an area name up to 40 characters.")
        elif action == "merge":
            if not target_id or target_id == project_id or target_id == work.OTHER or target_id not in active:
                raise ProjectError("Choose another project to merge into.")
            if resolve(target_id, merges(), set(active)) == project_id:
                raise ProjectError("That project is already merged into this one.")
            row.update(merged_into=target_id, merged_title=row["title"] or active[project_id]["title"])
        elif action == "unmerge":
            row.update(merged_into=None, merged_title=None)
        elif action == "keep":
            row["kept_revision"] = active[project_id]["revision"]
        elif action == "seen":
            row.update(back_reason=None, back_at=None)
        else:
            row = _undo(c, project_id, undo_operation_id, row)
            if before["archived_at"] and not row["archived_at"] and project_id in active:
                row["kept_revision"] = active[project_id]["revision"]
        row["revision"] += 1
        response = {**_public(row), "action": action, "undo_operation_id": None if action == "undo" else operation_id}
        try:
            _save(c, row)
            c.execute("INSERT INTO work_project_operations(operation_id,project_id,fingerprint,response,before,at) "
                      "VALUES(?,?,?,?,?,?)", (operation_id, project_id, fingerprint, json.dumps(response),
                                              json.dumps({**before, "cleared": cleared}), time.time()))
            c.commit()
        except Exception:
            c.rollback()
            raise
        return response


def _undo(c: sqlite3.Connection, project_id: str, operation_id: str | None, row: dict) -> dict:
    op = c.execute("SELECT * FROM work_project_operations WHERE operation_id=?", (operation_id or "",)).fetchone()
    if not op or op["project_id"] != project_id or json.loads(op["response"])["action"] == "undo":
        raise ProjectError("There is nothing to undo there.")
    if op["undone"]:
        raise ProjectError("That was already undone.", 409, _public(row))

    if json.loads(op["response"])["revision"] != row["revision"]:
        raise ProjectError("This project changed since then, so Undo can't put it back.", 409, _public(row))
    c.execute("UPDATE work_project_operations SET undone=1 WHERE operation_id=?", (operation_id,))
    before = json.loads(op["before"])
    from . import workstate
    for old in before.pop("cleared", []):

        if workstate._row(old["id"])["revision"] == old["revision"] + 1:
            workstate.save(c, {**old, "revision": old["revision"] + 2})
    return {**_blank(project_id), **before, "revision": row["revision"]}


def _project_sources(project_id: str) -> list[dict]:
    from . import workstate
    return [workstate.decorate({**work._json(r["body"]), "project_id": project_id}) for r in store.db().execute(
        "SELECT body FROM work_sources WHERE project_id=? AND active=1", (project_id,)) if work._visible(work._json(r["body"]))]


def _waiting(project_id: str) -> dict:
    from . import workstate
    now, mine = time.time(), _project_sources(project_id)
    return {"asks": sorted(f"{s['id']}:{s['revision']}" for s in mine
                           if s["section"] == "needs_you" and workstate.visible(s)),
            "dates": sorted({s["date"] for s in mine if s.get("date") is not None and s["date"] >= now})}


def _clear_mail(c: sqlite3.Connection, project_id: str) -> list[dict]:
    ""

    from . import workstate
    cleared = []
    for s in _project_sources(project_id):
        if s["kind"] == "email" and s["section"] == "needs_you" and workstate.visible(s):
            old = workstate._row(s["id"])
            cleared.append(old)
            workstate.save(c, {**old, "revision": old["revision"] + 1, "handled_revision": s["revision"], "done": 1,
                               "snoozed_until": None})
    return cleared


def bring_back(c: sqlite3.Connection) -> None:
    ""

    for row in _rows().values():
        if not row["archived_at"] or row["merged_into"]:
            continue
        seen, now = work._json(row["archive_seen"]), time.time()
        asks, dates = set(seen.get("asks") or []), set(seen.get("dates") or [])
        from . import workstate
        mine = sorted(_project_sources(row["id"]), key=lambda s: -s["at"])
        new = next((s for s in mine if s["section"] == "needs_you" and workstate.visible(s) and
                    f"{s['id']}:{s['revision']}" not in asks), None)
        new = new or next((s for s in mine if s.get("date") is not None and s["date"] >= now and s["date"] not in dates),
                          None)
        if new:
            reason = " ".join(str(new.get("summary") or new["title"]).split())[:200]
            _save(c, {**row, "revision": row["revision"] + 1, "archived_at": None, "archive_seen": None,
                      "back_reason": reason, "back_at": now})
    c.commit()



def tidy(c: sqlite3.Connection) -> None:
    ""



    from . import workstate
    now, rows = time.time(), _rows()
    for pid, body in _active().items():
        row = rows.get(pid) or _blank(pid)
        if pid == work.OTHER or row["archived_at"] or row["merged_into"] or row["pin_rank"] is not None or \
                row["back_at"] or row["kept_revision"] == body.get("revision"):
            continue
        mine = _project_sources(pid)
        if any(s["section"] == "in_progress" or (s["section"] == "needs_you" and workstate.visible(s)) for s in mine):
            continue

        dates = [s["date"] for s in mine if s.get("date") is not None] + ([body["date"]] if body.get("date") else [])
        ended = bool(dates) and max(dates) < now - DONE_DAYS * 86400
        quiet = not any(d >= now for d in dates) and max((s["at"] for s in mine), default=0) < now - QUIET_DAYS * 86400
        if not (ended or quiet):
            continue
        before = dict(row)
        row.update(archived_at=now, archive_seen=json.dumps(_waiting(pid)), revision=row["revision"] + 1)
        op = "tidy_" + uuid.uuid4().hex
        _save(c, row)
        c.execute("INSERT INTO work_project_operations(operation_id,project_id,fingerprint,response,before,at) "
                  "VALUES(?,?,?,?,?,?)", (op, pid, work._revision([pid, "archive", op]),
                                          json.dumps({**_public(row), "action": "archive", "undo_operation_id": op}),
                                          json.dumps({**before, "cleared": []}), now))
    c.commit()


def find(name: str, projects: list[dict]) -> list[dict]:
    ""
    words = work.openloops._key(name).split()
    exact, loose = [], []
    for p in projects:
        names = [work.openloops._key(n) for n in (p["title"], p["natural_title"], *p["aliases"])]
        if p["id"] == work.OTHER or not words:
            continue
        if " ".join(words) in names:
            exact.append(p)
        elif any(all(w in n for w in words) for n in names):
            loose.append(p)
    return exact or loose


def _line(p: dict) -> str:
    return f"{p['title']} ({p['area']}{', archived' if p['archived_at'] else ''})"


def ask(args: dict) -> dict:
    ""

    action = str(args.get("action") or "")
    if action == "list":
        out = work.overview()
        projects = [p for p in out["projects"] + out["archived_projects"] if p["id"] != work.OTHER]
        return {"ok": True, "projects": [_line(p) + (", looks done" if p["looks_done"] else "") for p in projects]}
    op = "zero_" + uuid.uuid4().hex
    if action == "undo":
        receipt = store.db().execute("SELECT project_id FROM work_project_operations WHERE operation_id=?",
                                     (str(args.get("undo_id") or ""),)).fetchone()
        if not receipt:
            return {"ok": False, "error": "I have no change with that undo_id."}
        change(receipt["project_id"], "undo", op, undo_operation_id=str(args["undo_id"]))
        return {"ok": True, "done": "Undone."}
    if action not in ("archive", "restore", "pin", "unpin", "rename", "move_area", "merge"):
        return {"ok": False, "error": "Choose archive, restore, pin, unpin, rename, move_area, merge, undo or list."}
    out = work.overview()
    projects = out["projects"] + out["archived_projects"]
    matches = find(str(args.get("project") or ""), projects)
    target = None
    if action == "merge" and not args.get("into") and len(matches) == 2:
        target, smaller = sorted(matches, key=lambda p: -len(p["sources"]))
        matches = [smaller]
    elif action == "merge":
        into = find(str(args.get("into") or ""), projects)
        if len(into) != 1:
            return {"ok": False, "error": "Say which project to merge into.",
                    "matches": [_line(p) for p in into]}
        target = into[0]
        matches = [p for p in matches if p["id"] != target["id"]]
    if not matches:
        return {"ok": False, "error": "No project matches that name. Call with action list to see them."}
    if len(matches) > 1:
        return {"ok": False, "error": "More than one project matches. Use the full name.",
                "matches": [_line(p) for p in matches]}
    p = matches[0]
    out = change(p["id"], action, op, title=args.get("title"), area=args.get("area"),
                 target_id=target["id"] if target else None)
    done = {"archive": f"Archived {p['title']}.", "restore": f"Restored {p['title']}.", "pin": f"Pinned {p['title']}.",
            "unpin": f"Unpinned {p['title']}.", "rename": f"Renamed {p['title']} to {out['title'] or p['natural_title']}.",
            "move_area": f"Moved {p['title']} to {out['area']}.",
            "merge": f"Merged {p['title']} into {target['title'] if target else ''}."}[action]
    return {"ok": True, "done": done, "undo_id": out["undo_operation_id"]}
