""

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime

from . import blockask, config, home, reviewcard, wayforward, who

log = logging.getLogger("taffy.board")






ACTIVE = ("ready", "running", "todo")
WORKING = (*ACTIVE, "scheduled")
NEEDS_YOU = ("blocked", "review", "triage")
FINISHED = ("done",)


class _conn:
    ""
    def __enter__(self) -> sqlite3.Connection:
        self.c = sqlite3.connect(f"file:{config.KANBAN_DB}?mode=ro", uri=True, timeout=5)
        self.c.row_factory = sqlite3.Row
        return self.c

    def __exit__(self, *exc):
        self.c.close()


ATTACHMENTS_ROOT = (config.HERMES_HOME / "kanban").resolve()
CHECK_AUTHOR = "Mail check"


def _on_card() -> set[str]:
    ""




    from . import consent, macqueue, signin, things
    return {r["task_id"] for r in consent.pending() if r.get("task_id")} | macqueue.waiting_tasks() \
        | things.asked_tasks() | signin.challenge_tasks()


def _task_row(c: sqlite3.Connection, r: sqlite3.Row) -> dict:
    run = c.execute(
        "SELECT status, outcome, summary, metadata, error, started_at, ended_at FROM task_runs "
        "WHERE task_id = ? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
    attachments = c.execute(
        "SELECT id, filename, size, content_type FROM task_attachments WHERE task_id = ? ORDER BY id",
        (r["id"],)).fetchall()
    block_reason, ev = None, None
    if r["status"] in ("blocked", "triage"):
        ev = c.execute(
            "SELECT payload, created_at FROM task_events WHERE task_id = ? AND kind IN ('blocked', 'block_loop_detected') "
            "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        if ev and ev["payload"]:
            try:
                block_reason = json.loads(ev["payload"]).get("reason")
            except ValueError:
                block_reason = ev["payload"]


    standing = None
    if ev is not None and not blockask.parse(block_reason or "") and not blockask.signs_in(block_reason or "", r["title"]) \
            and ("block_kind" not in r.keys() or r["block_kind"] != "dependency") \
            and not wayforward.card(r["id"], ev["created_at"]):
        standing = _standing(c, r["id"], r["title"] or "", r["status"])
        if standing and standing[0] == (block_reason or "") and abs(standing[1] - float(ev["created_at"] or 0)) <= 1:
            standing = None
    if standing:
        block_reason = standing[0]
    summary = (run["summary"] if run else None) or r["result"]
    card, status, on_card = None, r["status"], False
    filed_at = None
    bot = config.BOTS_BY_ID.get(r["assignee"] or "default")
    zip_name = bot.name if bot else "the Zip"
    mail = []
    if r["status"] == "review":
        filed = c.execute("SELECT created_at FROM task_events WHERE task_id = ? AND kind IN ('review_requested', 'review') "
                          "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        filed_at = filed["created_at"] if filed else None
        mail = reviewcard.mail_cards(r["assignee"] or "default", (run["started_at"] if run else None) or r["created_at"] or 0,
                                     summary, run["metadata"] if run else None, len(attachments))
        if mail:


            status = "scheduled"
        else:


            card = reviewcard.read(summary, run["metadata"] if run else None, zip_name, r["title"] or "",
                                   task_zips({"id": r["id"], "assignee": r["assignee"], "created_by": r["created_by"]}))

            changed = c.execute("SELECT body FROM task_comments WHERE task_id = ? AND author = ? AND created_at >= ? "
                                "ORDER BY id DESC LIMIT 1", (r["id"], CHECK_AUTHOR, filed_at or 0)).fetchone()
            if changed:
                card["lead"] = f"{changed['body'].strip()}\n\n{card['lead']}".strip()
    elif r["status"] in ("blocked", "triage"):


        filed_at = (standing[1] if standing else ev["created_at"]) if ev else None
        kind = r["block_kind"] if "block_kind" in r.keys() else None
        reason = block_reason or ""
        on_card = not blockask.signs_in(reason, r["title"]) and not blockask.parse(reason) and r["id"] in _on_card()
        quiet = blockask.quiet(reason, kind, r["title"])
        parked = r["status"] == "triage" and quiet

        ready = wayforward.card(r["id"], filed_at) if quiet and not on_card else None
        card = None if on_card else reviewcard.blocked(block_reason, kind, r["status"], zip_name, r["title"], ready)
        if card:
            wayforward.dress(card, r["id"], filed_at)




        blank = r["status"] == "blocked" and blockask.asks_nothing(reason, kind, r["title"])
        if on_card or (r["status"] == "blocked" and quiet and not card) or (parked and not card) or blank:
            status = "scheduled"
    return {
        "id": r["id"],
        "title": r["title"],
        "body": r["body"],
        "assignee": r["assignee"],

        "status": "blocked" if status == "triage" else status,
        "created_by": r["created_by"],
        "created_at": r["created_at"],


        "started_at": (run["started_at"] if run and r["status"] == "running" else None) or r["started_at"],
        "completed_at": r["completed_at"],
        "summary": reviewcard.text(card) if card and r["status"] == "review" else summary,
        "card": card,

        "raw_status": r["status"],
        "raw_reason": block_reason if r["status"] in ("blocked", "triage") else None,
        "block_kind": (r["block_kind"] if "block_kind" in r.keys() else None) if r["status"] in ("blocked", "triage") else None,
        "filed_at": filed_at,
        "on_card": on_card,
        "standing": bool(standing),
        "mail_cards": [m["id"] for m in mail],
        "error": run["error"] if run else None,
        "block_reason": (reviewcard.blocked_text(card) if card else reviewcard.to_you(block_reason))
        if block_reason else block_reason,
        "attachments": [dict(a) for a in attachments],
    }


WAITING_MAX = 14
WAITING_LINE = 170


def waiting_lines() -> list[str]:
    ""
    groups = grouped()
    tasks = groups["needs_you"] + [t for t in groups["working"] if t.get("on_card")]
    out = []
    for t in tasks[:WAITING_MAX]:
        bot = config.BOTS_BY_ID.get(t["assignee"] or "default")
        said = (t["card"]["ask"] if t.get("card") else t.get("block_reason")) or ""
        said = " ".join(said.split())
        kind = "review" if t["status"] == "review" else "blocked"
        if config.sealed(*task_zips(t)):


            said = " ".join(str((t.get("card") or {}).get("ask") or "").split())
        out.append(f"- {t['id']} ({bot.name if bot else t['assignee']}, {kind}): {t['title']}. {said[:WAITING_LINE]}")
    if len(tasks) > WAITING_MAX:
        out.append(f"- and {len(tasks) - WAITING_MAX} more in Work.")
    return out


def grouped() -> dict[str, list[dict]]:
    ""
    def q(c, statuses, order, limit=None):
        sql = (f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY {order}"
               + (" LIMIT ?" if limit else ""))
        rows = c.execute(sql, (*statuses, *((limit,) if limit else ()))).fetchall()
        return [_task_row(c, r) for r in rows]
    with _conn() as c:
        waiting = q(c, NEEDS_YOU, "created_at DESC")
        return {
            "needs_you": [t for t in waiting if t["status"] != "scheduled"],
            "working": q(c, WORKING, "created_at DESC") + [t for t in waiting if t["status"] == "scheduled"],
            "done": q(c, FINISHED, "COALESCE(completed_at, created_at) DESC", 60),
        }


def task_zips(task) -> tuple[str, ...]:
    ""



    item = {"id": task} if isinstance(task, str) else dict(task or {})
    task_id = str(item.get("task_id") or item.get("id") or "")
    found = {str(item.get("created_by") or "")} | ({str(item.get("assignee") or "default")} if "assignee" in item else set())
    found |= {str(c.get("author") or "") for c in item.get("comments") or [] if isinstance(c, dict)}
    if task_id and os.path.exists(config.KANBAN_DB):
        try:
            with _conn() as c:
                r = c.execute("SELECT assignee, created_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if r:
                    found |= {str(r["assignee"] or "default"), str(r["created_by"] or "")}
                found |= {str(a[0] or "") for a in c.execute(
                    "SELECT DISTINCT author FROM task_comments WHERE task_id = ?", (task_id,))}
        except sqlite3.Error:
            log.warning("could not read whose words are in %s; using what the caller gave", task_id, exc_info=True)
    zips = set(config.BOTS_BY_ID) | config.PRIVATE_ZIPS | {"default"}
    return tuple(sorted(z for z in found if z in zips))


def get_task(task_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not r:
            return None
        task = _task_row(c, r)
        task["comments"] = [dict(x) for x in c.execute(
            "SELECT id, author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (task_id,))]
        task["events"] = [dict(x) for x in c.execute(
            "SELECT kind, payload, created_at FROM task_events WHERE task_id = ? ORDER BY id", (task_id,))]
        return task


def run_metadata(task_id: str) -> str | None:
    ""
    if not config.KANBAN_DB.exists():
        return None
    with _conn() as c:
        r = c.execute("SELECT metadata FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    return r["metadata"] if r else None


def run_filed(task_id: str) -> tuple[str | None, str | None]:
    ""
    if not config.KANBAN_DB.exists():
        return None, None
    with _conn() as c:
        r = c.execute("SELECT summary, metadata FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                      (task_id,)).fetchone()
    return (r["summary"], r["metadata"]) if r else (None, None)


def attachment_path(task_id: str, attachment_id: int) -> tuple[str, str] | None:
    ""


    with _conn() as c:
        r = c.execute("SELECT filename, stored_path FROM task_attachments WHERE task_id = ? AND id = ?",
                      (task_id, attachment_id)).fetchone()
    if not r:
        return None
    from pathlib import Path
    from . import safe_files
    try:
        real = safe_files.resolve(str(r["stored_path"]), (ATTACHMENTS_ROOT,))
    except safe_files.Refused:
        return None
    return Path(r["filename"]).name or "file", real


def open_attachment(task_id: str, attachment_id: int):
    ""

    found = attachment_path(task_id, attachment_id)
    if not found:
        return None
    from . import safe_files
    try:
        return found[0], safe_files.open_file(found[1], (ATTACHMENTS_ROOT,))
    except safe_files.Refused:
        return None


FILES_AUTHOR = "File check"


def _declared(metadata: str | None) -> list[str]:
    try:
        found = (json.loads(metadata or "{}") or {}).get("artifacts")
    except (ValueError, AttributeError):
        return []
    found = [found] if isinstance(found, str) else found
    return [str(p).strip() for p in found or [] if isinstance(p, str) and str(p).strip()][:20]


def keep_artifacts(task_id: str) -> dict:
    ""











    from pathlib import Path
    from . import safe_files, zipfiles
    out = {"kept": [], "missing": [], "outside": [], "failed": []}
    with _conn() as c:
        task = c.execute("SELECT assignee FROM tasks WHERE id = ?", (task_id,)).fetchone()
        run = c.execute("SELECT metadata FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                        (task_id,)).fetchone()
        have = [dict(a) for a in c.execute("SELECT filename, size, stored_path FROM task_attachments WHERE task_id = ?",
                                           (task_id,))]
    if not task or not run:
        return out
    bot = task["assignee"] or "default"
    roots = (str(zipfiles.made_root(bot)), *(str(Path(config.WORK_DIR) / m) for m in zipfiles._absorbed(bot)),
             str(ATTACHMENTS_ROOT))
    stored = {os.path.realpath(a["stored_path"]) for a in have if a.get("stored_path")}
    for path in _declared(run["metadata"]):
        name = Path(path).name or path
        try:
            real = safe_files.resolve(path, roots)
        except safe_files.Refused as e:
            out["missing" if "does not exist" in str(e) else "outside"].append(name)
            continue
        size = os.path.getsize(real)
        if real in stored or any(a["filename"] == name and a["size"] == size for a in have):
            continue
        ok, detail = _cli("attach", f"--name={name}", f"--author={bot}", "--", task_id, real)
        if ok:
            out["kept"].append(name)
            have.append({"filename": name, "size": size})
        else:
            log.warning("could not attach %s to %s: %s", name, task_id, detail[-200:])
            out["failed"].append(name)
    lines = [f"{', '.join(out[k])} {why}" for k, why in (
        ("missing", "was not found when this task finished."),
        ("outside", f"is outside {zipfiles_owner(bot)} work folder, so it was not kept with the task."),
        ("failed", "could not be kept with the task (too large, or the board refused it).")) if out[k]]
    if lines:
        _cli("comment", f"--author={FILES_AUTHOR}", "--", task_id,
             _note("The completion note names files that are not attached: " + " ".join(lines)))
    if out["kept"]:
        log.info("task %s: attached %s from its completion note", task_id, out["kept"])
    return out


def zipfiles_owner(bot_id: str) -> str:
    ""
    b = config.BOTS_BY_ID.get(bot_id)
    return who.possessive(b.name) if b else "the Zip's"


SUMMARY_MAX = 4000


def events_after(event_id: int) -> list[dict]:
    ""


    with _conn() as c:
        rows = c.execute(
            "SELECT e.id, e.task_id, e.kind, e.payload, e.created_at, t.title, t.body, t.assignee, t.created_by, "
            "t.status, r.summary AS run_summary, r.started_at AS run_started, r.ended_at AS run_ended "
            "FROM task_events e JOIN tasks t ON t.id = e.task_id LEFT JOIN task_runs r ON r.id = e.run_id "
            "WHERE e.id > ? ORDER BY e.id LIMIT 500", (event_id,)).fetchall()
    out = []
    for r in rows:
        ev = dict(r)
        run_summary, started, ended = ev.pop("run_summary"), ev.pop("run_started"), ev.pop("run_ended")
        try:
            clipped = (json.loads(ev["payload"] or "{}") or {}).get("summary")
        except (ValueError, AttributeError):
            clipped = None
        ev["summary"] = (str(run_summary or "").strip() or str(clipped or "").strip())[:SUMMARY_MAX] or None
        ev["worked"] = ended - started if started and ended and ended >= started else None
        out.append(ev)
    return out


def latest_event_id() -> int:
    with _conn() as c:
        row = c.execute("SELECT COALESCE(MAX(id), 0) AS m FROM task_events").fetchone()
        return row["m"]




LONG_TASK_SEC = 3600


def long_running(min_age_sec: int) -> list[dict]:
    ""

    cutoff = int(time.time()) - min_age_sec
    with _conn() as c:
        rows = c.execute(
            "SELECT t.id, t.title, t.assignee FROM tasks t LEFT JOIN task_runs r ON r.id = t.current_run_id "
            "WHERE t.status = 'running' AND COALESCE(r.started_at, t.started_at) < ?", (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def bot_load() -> dict[str, dict]:
    ""





    out: dict[str, dict] = {}
    if not config.KANBAN_DB.exists():
        return out
    with _conn() as c:
        for r in c.execute("SELECT assignee, status, COUNT(*) AS n FROM tasks "
                           "WHERE status IN ('ready','running','todo','blocked','review') "
                           "GROUP BY assignee, status"):
            d = out.setdefault(r["assignee"] or "default", {"working": 0, "running": 0, "needs_you": 0})
            if r["status"] in NEEDS_YOU:
                d["needs_you"] += r["n"]
            else:
                d["working"] += r["n"]
                d["running"] += r["n"] if r["status"] == "running" else 0
    return out






_chat_looks: set[tuple[str, str]] = set()
LOOK_TOOLS = ("kanban_show", "kanban_comment")


def note_chat_look(bot_id: str, tool: str | None, args) -> None:
    task_id = args.get("task_id") if isinstance(args, dict) else None
    if tool in LOOK_TOOLS and isinstance(task_id, str) and task_id:
        _chat_looks.add((bot_id, task_id))


def chat_looked(bot_id: str, task_id: str) -> bool:
    return (bot_id, task_id) in _chat_looks


def _cli(*args: str) -> tuple[bool, str]:
    ""
    env = dict(os.environ, HERMES_HOME=str(config.HERMES_HOME))
    p = subprocess.run([str(config.HERMES_BIN), "kanban", *args], capture_output=True, text=True,
                       timeout=60, env=env)
    out = "\n".join(l for l in (p.stdout + p.stderr).splitlines()
                    if "SyntaxWarning" not in l and "WSL launcher" not in l)
    return p.returncode == 0, out.strip()


def _note(text: str) -> str:
    ""
    return text if not text.startswith("-") else " " + text
















ANSWER, ARRIVED = "answer", "arrived"
DATE, RETRY, HANDBACK = "date", "retry", "handback"
WAITING_WAKES = (ANSWER, ARRIVED)
_refused: dict[tuple[str, str], float] = {}


def _his_cards() -> set[str]:
    ""

    from . import consent, signin, things
    return {r["task_id"] for r in consent.pending() if r.get("task_id")} | things.asked_tasks() | signin.challenge_tasks()


def _block_events(c: sqlite3.Connection, task_id: str) -> tuple[list[dict], list[tuple[str, float]], list[dict]]:
    ""
    events = [dict(e) for e in c.execute("SELECT kind, payload, created_at FROM task_events WHERE task_id = ? ORDER BY id",
                                         (task_id,))]
    blocks = []
    for e in events:
        if e["kind"] in ("blocked", "block_loop_detected"):
            try:
                reason = (json.loads(e["payload"] or "{}") or {}).get("reason") or ""
            except (ValueError, AttributeError):
                reason = str(e["payload"] or "")
            blocks.append((str(reason), float(e["created_at"] or 0)))
    comments = [dict(x) for x in c.execute("SELECT author, body, created_at FROM task_comments WHERE task_id = ? "
                                           "ORDER BY id", (task_id,))]
    return events, blocks, comments


def _standing(c: sqlite3.Connection, task_id: str, title: str, status: str) -> tuple[str, float] | None:
    ""

    events, blocks, comments = _block_events(c, task_id)
    if not blocks:
        return None
    current = blocks[-1][1] if status in ("blocked", "triage") else None
    return blockask.standing_question(blocks, blockask.settled_times(events, comments, who.first()), title,
                                      wayforward.hub_word(task_id, current))


def waits_on_person(task_id: str) -> str:
    ""

    if not config.KANBAN_DB.exists():
        return ""
    try:
        with _conn() as c:
            r = c.execute("SELECT title, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not r or r["status"] not in ("blocked", "triage", "scheduled"):
                return ""
            standing = _standing(c, task_id, r["title"] or "", r["status"])
    except sqlite3.Error:
        log.warning("could not read whether %s waits on the person", task_id, exc_info=True)
        return ""
    if task_id in _his_cards():
        return "card"
    return "question" if standing else ""


def _refuse(task_id: str, cause: str, why: str) -> None:
    key = (task_id, why if why == "nothing new" else cause)
    if key in _refused:
        return
    _refused[key] = time.time()
    if why == "nothing new":

        log.warning("refused a %s wake of %s that carried nothing new", cause, task_id)
        from . import problems
        try:
            problems.report("default", {"kind": "break", "source": "code", "concerns_zip": "default",
                                        "where": f"board task {task_id}",
                                        "what_happened": f"The hub tried to wake a board task ({cause}) with nothing "
                                                         "new to tell it, and the wake was refused.",
                                        "evidence": f"cause {cause}", "expected": "Every wake says what changed."})
        except Exception:
            log.warning("could not report the empty wake of %s", task_id, exc_info=True)
    else:
        log.info("left %s waiting on the person (%s): a %s wake does not bring it back", task_id, why, cause)


def may_wake(task_id: str, note: str, cause: str) -> str:
    ""


    if not str(note or "").strip():
        _refuse(task_id, cause, "nothing new")
        return "a wake with nothing new"
    if cause in WAITING_WAKES:
        return ""
    why = waits_on_person(task_id)
    if not why:
        return ""
    _refuse(task_id, cause, why)
    keep_waiting(task_id, why)
    return "waiting on the person"


def _unblock(task_id: str, note: str, cause: str) -> tuple[bool, str]:
    ""
    refused = may_wake(task_id, note, cause)
    if refused:
        return False, refused
    return _cli("unblock", f"--reason={str(note).strip()}", "--", task_id)


def _write(sql: str, args: tuple) -> int:
    ""
    from hermes_cli import kanban_db as kb
    import hermes_cli.kanban_specify as ks
    with ks.kbc.connect_closing() as conn, kb.write_txn(conn):
        return conn.execute(sql, args).rowcount


def keep_waiting(task_id: str, why: str | None = None) -> bool:
    ""




    why = waits_on_person(task_id) if why is None else why
    if not why:
        return False
    task = get_task(task_id) or {}
    if task.get("raw_status") in ("blocked", "triage") and not task.get("standing"):
        return False
    try:
        changed = _write("UPDATE tasks SET status = 'blocked', block_recurrences = 0 WHERE id = ? AND "
                         "status IN ('scheduled', 'triage', 'blocked') AND NOT (status = 'blocked' AND "
                         "COALESCE(block_recurrences, 0) = 0)", (task_id,))
    except Exception:
        log.warning("could not put %s back to waiting on the person", task_id, exc_info=True)
        return False
    if changed:
        log.info("%s waits on the person (%s): back to waiting for the answer", task_id, why)
        if why == "question":
            _reopen_rows(task_id)
    return bool(changed)


def _reopen_rows(task_id: str) -> None:
    ""

    from . import store, withdrawn
    task = get_task(task_id) or {}
    ask = " ".join(str((task.get("card") or {}).get("ask") or "").split())
    if not ask:
        return
    bot = config.BOTS_BY_ID.get(task.get("assignee") or "default")
    prefix = withdrawn.LINE.split("{", 1)[0]
    with store._lock:
        rows = [r["id"] for r in store.db().execute(
            "SELECT id FROM feed WHERE task_id = ? AND kind = 'message' AND body LIKE ? AND created_at >= ? ORDER BY id",
            (task_id, prefix + "%", float(task.get("filed_at") or 0) - 5))]
    for fid in rows[-1:]:
        store.restate_feed(fid, "task_blocked", f"{bot.name if bot else 'A Zip'} needs you", ask, expect_kind="message",
                           written_by="hub", written_for="record")


def restore_waiting() -> list[str]:
    ""

    if not config.KANBAN_DB.exists():
        return []
    with _conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE status IN ('scheduled', 'triage', 'blocked')")]
    return [t for t in ids if keep_waiting(t)]


RETRY_AFTER_SEC = 6 * 3600

RETRY_NOTE = ("Try this again. Tools and rules may have changed since it stopped you, so also try the routes that failed "
              "before, and any route you have not tried yet. If it stops you the same way, block again and say in your "
              "note which routes you tried.")


def retry_stuck(booted_at: float) -> list[str]:
    ""



    if not config.KANBAN_DB.exists():
        return []
    now = time.time()
    out = []
    with _conn() as c:
        rows = c.execute(
            "SELECT t.id, t.title, (SELECT e.payload FROM task_events e WHERE e.task_id = t.id AND e.kind = 'blocked' "
            "ORDER BY e.id DESC LIMIT 1) AS payload, (SELECT MAX(e.created_at) FROM task_events e WHERE e.task_id = t.id "
            "AND e.kind = 'blocked') AS at FROM tasks t WHERE t.status = 'blocked' AND t.block_kind IN "
            f"({','.join('?' * len(blockask.QUIET_KINDS))})", blockask.QUIET_KINDS).fetchall()
    on_card = _on_card()
    for r in rows:
        if r["id"] in on_card:
            continue
        try:
            reason = json.loads(r["payload"] or "{}").get("reason") or ""
        except ValueError:
            continue
        if not blockask.quiet(reason, blockask.QUIET_KINDS[0], r["title"]):
            continue
        if wayforward.card(r["id"], r["at"]):
            continue
        if not r["at"] or (r["at"] >= booted_at and now - r["at"] < RETRY_AFTER_SEC):
            continue
        ok, detail = _unblock(r["id"], RETRY_NOTE, RETRY)
        if ok:
            out.append(r["id"])
        elif detail != "waiting on the person":
            log.warning("could not retry %s: %s", r["id"], detail[-200:])
    return out + _hand_back_blank()






BLANK_NOTE = ("This task was waiting on {first} without a question, so there was nothing to answer. Read the task and "
              "its comments. Do the work if you can. If it truly needs {first}, block it with one question that can be "
              "answered cold, with the background and your recommendation.")


def _hand_back_blank() -> list[str]:
    ""
    from . import who
    with _conn() as c:
        rows = c.execute(
            "SELECT t.id, t.title, t.block_kind, (SELECT e.payload FROM task_events e WHERE e.task_id = t.id AND "
            "e.kind = 'blocked' ORDER BY e.id DESC LIMIT 1) AS payload FROM tasks t WHERE t.status = 'blocked'").fetchall()
    on_card, out = _on_card(), []
    for r in rows:
        try:
            reason = json.loads(r["payload"] or "{}").get("reason") or ""
        except ValueError:
            reason = r["payload"] or ""
        if r["id"] in on_card or not blockask.asks_nothing(reason, r["block_kind"], r["title"]):
            continue
        ok, detail = _unblock(r["id"], BLANK_NOTE.format(first=who.first()), HANDBACK)
        if ok:
            out.append(r["id"])
        elif detail != "waiting on the person":
            log.warning("could not hand back %s: %s", r["id"], detail[-200:])
    return out




_WAKE_AT = re.compile(r"SCHEDULED_UNTIL=(\S+)")


def _wake_time(reason: str) -> float | None:
    ""
    found = _WAKE_AT.search(reason or "")
    if not found:
        return None
    text = found.group(1).rstrip(".,;)")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if "T" not in text:
        when = when.replace(hour=8)
    return (when if when.tzinfo else when.replace(tzinfo=home.zone())).timestamp()






UNDATED_PER_PASS = 3


def undated_note() -> str:
    from . import who
    first = who.first()


    return (f"Nothing is set to bring this task back, so here it is. It may have sat for a long time: check its dates "
            f"first. If the period it covers has passed, close it without asking and never bring {first} a review or "
            f"a question about it. If another of your tasks already covers it, close "
            f"this one as a duplicate and say nothing about it. If what you were waiting for has come, carry on from "
            f"your last note. If you are waiting on {first}, block it with one question {first} can answer cold, with "
            f"the background and your recommendation; a park never reaches {first}. To wait for a date, park it again "
            f"with SCHEDULED_UNTIL=<date and time> at the start of the reason. If something else is in the way, block "
            f"it and say what.")


DATE_NOTE = "The date you parked this for has arrived. Pick it up from your last note."


def wake_parked(now: float | None = None) -> list[str]:
    ""



    if not config.KANBAN_DB.exists():
        return []
    now = time.time() if now is None else now
    with _conn() as c:
        rows = c.execute(
            "SELECT t.id, e.payload, e.created_at AS at FROM tasks t LEFT JOIN task_events e ON e.id = (SELECT MAX(x.id) "
            "FROM task_events x WHERE x.task_id = t.id AND x.kind = 'scheduled') WHERE t.status = 'scheduled' "
            "ORDER BY e.created_at, t.id").fetchall()
    out = []
    owed, undated = None, 0
    for r in rows:
        try:
            reason = json.loads(r["payload"] or "{}").get("reason") or ""
        except (ValueError, AttributeError):
            continue
        at = _wake_time(reason)
        note = DATE_NOTE
        if at is None and "SCHEDULED_UNTIL=" not in reason:
            if owed is None:
                from . import consent
                owed = owed_wakes() | {x["task_id"] for x in consent.pending() if x.get("task_id")}
            if undated >= UNDATED_PER_PASS or now - float(r["at"] or 0) < RETRY_AFTER_SEC or r["id"] in owed:
                continue
            undated += 1
            note = undated_note()
        elif at is None or at > now:
            continue
        ok, detail = _unblock(r["id"], note, DATE)
        if ok:
            out.append(r["id"])
        elif detail != "waiting on the person":
            log.warning("could not wake %s: %s", r["id"], detail[-200:])
    return out







def owed_wakes() -> set[str]:
    ""



    from . import codesessions, maccodex, macqueue
    out = set(macqueue.waiting_tasks())
    for source in (maccodex, codesessions):
        try:
            out |= source.owed_tasks()
        except Exception:
            log.warning("could not read the %s runs that owe a wake", source.__name__, exc_info=True)
    return out


def _forget_blocks(task_id: str) -> None:
    ""
    try:
        from hermes_cli import kanban_db as kb
        import hermes_cli.kanban_specify as ks
        with ks.kbc.connect_closing() as conn, kb.write_txn(conn):
            conn.execute("UPDATE tasks SET block_recurrences = 0 WHERE id = ?", (task_id,))
    except Exception:
        log.warning("could not clear the block count of %s", task_id, exc_info=True)


def _park(task_id: str, reason: str, settled: bool = False) -> bool:
    ""




    if not settled and waits_on_person(task_id):
        log.info("did not park %s: it waits on the person", task_id)
        keep_waiting(task_id)
        return False
    try:
        from hermes_cli import kanban_db as kb
        import hermes_cli.kanban_specify as ks
        with ks.kbc.connect_closing() as conn:
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET status = 'blocked' WHERE id = ? AND status = 'triage'", (task_id,))
            if not kb.schedule_task(conn, task_id, reason=reason or None):
                return False
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET block_recurrences = 0 WHERE id = ?", (task_id,))
        return True
    except Exception:
        log.warning("could not park %s as waiting", task_id, exc_info=True)
        return False


def park_waits(only: str | None = None) -> list[str]:
    ""



    if not config.KANBAN_DB.exists():
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT t.id, t.title, (SELECT e.payload FROM task_events e WHERE e.task_id = t.id AND e.kind IN "
            "('blocked', 'block_loop_detected') ORDER BY e.id DESC LIMIT 1) AS payload FROM tasks t "
            "WHERE t.status IN ('blocked', 'triage')" + (" AND t.id = ?" if only else ""),
            (only,) if only else ()).fetchall()
    if not rows:
        return []
    from . import consent
    owed, on_card = owed_wakes(), {r["task_id"] for r in consent.pending() if r.get("task_id")}
    out = []
    for r in rows:
        try:
            reason = json.loads(r["payload"] or "{}").get("reason") or ""
        except (ValueError, AttributeError):
            reason = ""
        if r["id"] in on_card or blockask.signs_in(reason, r["title"]) or blockask.parse(reason):
            continue
        if (r["id"] in owed or _wake_time(reason) is not None) and _park(r["id"], reason):
            out.append(r["id"])
    return out


def wake(task_id: str, note: str, cause: str = ARRIVED) -> tuple[bool, str]:
    ""




    _forget_blocks(task_id)
    text = str(note or "").strip()
    return resume_with(task_id, text if text.startswith("[Hub note") else f"{who.hub_note()} {text}", cause)


REQUEUED = "transient_triage_requeued"


def requeue_transient_triage() -> int:
    ""


    from . import store
    if store.get_setting(REQUEUED) or not config.KANBAN_DB.exists():
        return 0
    with _conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE status = 'triage' AND block_kind = 'transient'")]
    note = (f"{who.hub_note()} This task was parked by mistake while it was only waiting. Pick it up from your last "
            "note. When you have to wait for your own Terra run, for the Mac or for a date, park the task with "
            "kanban_schedule and never block it.")
    moved = 0
    for task_id in ids:
        ok, detail = send_back(task_id, note, HANDBACK)
        if ok:
            _forget_blocks(task_id)
            moved += 1
        else:
            log.warning("could not return %s to its Zip: %s", task_id, str(detail)[-200:])
    store.set_setting(REQUEUED, time.time())
    return moved


def _back_to_zip(task_id: str, reason: str) -> tuple[bool, str]:
    ""



    try:
        from hermes_cli import kanban_db as kb
        import hermes_cli.kanban_specify as ks
        with ks.kbc.connect_closing() as conn:
            if kb.claim_review_task(conn, task_id) is None:
                return False, f"cannot claim {task_id}: it is not in review"
            ok, detail = kb.request_changes(conn, task_id, reason=reason)
        return ok, detail or ""
    except Exception as e:
        return False, str(e)


def approve(task_id: str, note: str | None, sends_to: list[str] | tuple[str, ...] = ()) -> tuple[bool, str]:
    ""
    name = who.first()
    if sends_to:
        msg = (f"{name} tapped Send on your review card. Send the message to {', '.join(sends_to)} exactly as you "
               "prepared it, with no other recipients, then finish the task")
    else:
        msg = (f"{name} approved this in the app. Go ahead as prepared. This approval does not cover sending or "
               f"publishing: anything like that needs {who.possessive()} yes on its own card")
    msg += f". {who.possessive()} note: {note}" if note else "."
    return _back_to_zip(task_id, msg)


def request_changes(task_id: str, note: str) -> tuple[bool, str]:
    return _back_to_zip(task_id, f"{who.first()} asked for changes in the app: {note}")


def hand_back(task_id: str, note: str) -> tuple[bool, str]:
    ""
    return _back_to_zip(task_id, note)


def decline(task_id: str, unsent: bool) -> tuple[bool, str]:
    ""

    first = who.first()
    what = "not to send it" if unsent else "it isn't needed"
    comment(task_id, f"{first} said {what}. Don't do it or bring it back unless {first} asks.")
    return _close(task_id, "You said not to send it." if unsent else "You said this isn't needed.")


def _close(task_id: str, note: str | None, *flags: str) -> tuple[bool, str]:
    ""



    with _conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row and row["status"] == "triage":
        try:
            from hermes_cli import kanban_db as kb
            import hermes_cli.kanban_specify as ks
            with ks.kbc.connect_closing() as conn, kb.write_txn(conn):
                conn.execute("UPDATE tasks SET status = 'blocked' WHERE id = ? AND status = 'triage'", (task_id,))
        except Exception as e:
            return False, str(e)
    return _cli("complete", *flags, f"--result={note or 'You closed this yourself.'}", "--", task_id)


def mark_done(task_id: str, note: str | None) -> tuple[bool, str]:
    ""

    return _close(task_id, note, "--force")


def drop_waiting(task_id: str, note: str | None) -> tuple[bool, str]:
    ""


    return _close(task_id, note)


def close_while_running(task_id: str, note: str | None) -> tuple[bool, str]:
    ""



    with _conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row or row["status"] != "running":
        return False, "not running"
    first = who.first()
    comment(task_id, f"{first} marked this done in the app while you were working on it. Stop here.")
    ok, detail = _cli("reclaim", f"--reason={first} marked this done in the app.", "--", task_id)
    if not ok:
        log.warning("could not stop the worker of %s before closing it: %s", task_id, detail[-200:])
    return mark_done(task_id, note)


def answer_block(task_id: str, answer: str) -> tuple[bool, str]:
    ""

    refused = may_wake(task_id, answer, ANSWER)
    if refused:
        return False, refused
    with _conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row and row["status"] == "triage":
        comment(task_id, answer)
        try:
            from hermes_cli import kanban_db as kb
            import hermes_cli.kanban_specify as ks
            with ks.kbc.connect_closing() as conn:
                ok = kb.specify_triage_task(conn, task_id, author=who.first())
            return ok, "" if ok else "not in triage"
        except Exception as e:
            return False, str(e)
    return _unblock(task_id, f"{who.first()} answered in the app: {answer}", ANSWER)


def send_back(task_id: str, note: str, cause: str = HANDBACK) -> tuple[bool, str]:
    ""

    refused = may_wake(task_id, note, cause)
    if refused:
        return False, refused
    with _conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row or row["status"] != "triage":
        return False, "not in triage"
    ok, detail = check_note(task_id, note)
    if not ok:
        return False, detail
    try:
        from hermes_cli import kanban_db as kb
        import hermes_cli.kanban_specify as ks
        with ks.kbc.connect_closing() as conn:
            ok = kb.specify_triage_task(conn, task_id, author=CHECK_AUTHOR)
        return ok, "" if ok else "not in triage"
    except Exception as e:
        return False, str(e)


def resume_with(task_id: str, note: str, cause: str = ARRIVED) -> tuple[bool, str]:
    ""


    with _conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row and row["status"] == "triage":
        return send_back(task_id, note, cause)
    return _unblock(task_id, note, cause)


def block(task_id: str, reason: str) -> tuple[bool, str]:
    ""
    return _cli("block", "--kind=capability", "--", task_id, _note(reason))


def stop_running(bot_id: str) -> list[str]:
    ""



    if not config.KANBAN_DB.exists():
        return []
    with _conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE status = 'running' "
                                          "AND COALESCE(NULLIF(assignee, ''), 'default') = ?", (bot_id,))]
    first = who.first()
    stopped = []
    for task_id in ids:
        comment(task_id, f"{first} pressed Stop in the app. Don't pick this up again unless {first} asks.")
        ok, detail = _cli("archive", "--", task_id)
        if ok:
            stopped.append(task_id)
        else:
            log.warning("Stop could not end task %s: %s", task_id, detail)
    return stopped


def comment(task_id: str, text: str) -> tuple[bool, str]:
    return _cli("comment", f"--author={who.first()}", "--", task_id, _note(text))


def check_note(task_id: str, text: str) -> tuple[bool, str]:
    return _cli("comment", f"--author={CHECK_AUTHOR}", "--", task_id, _note(text))


def later_notes(task_id: str, assignee: str | None, after: float) -> list[dict]:
    ""
    with _conn() as c:
        rows = c.execute(
            "SELECT t.id, t.title, (SELECT r.summary FROM task_runs r WHERE r.task_id = t.id ORDER BY r.id DESC LIMIT 1) AS note "
            "FROM tasks t WHERE t.assignee IS ? AND t.created_at > ? AND t.id != ? AND t.status != 'archived' "
            "ORDER BY t.created_at DESC", (assignee, after, task_id)).fetchall()
    return [{"id": r["id"], "title": r["title"], "note": (r["note"] or "")[:SUMMARY_MAX]} for r in rows if r["note"]]


def waiting_signins(site: str, bot_ids: list[str]) -> list[str]:
    ""
    import re
    from urllib.parse import urlsplit
    out = []
    for t in grouped()["needs_you"]:
        if t["status"] not in ("blocked", "triage") or (t["assignee"] or "default") not in bot_ids:
            continue
        if not (t["title"] or "").lower().startswith("sign in"):
            continue
        text = "\n".join(x for x in (t.get("block_reason"), t.get("body")) if x)
        m = re.search(r"https?://[^\s)]+", text)
        host = (urlsplit(m.group(0)).hostname or "").lower().removeprefix("www.") if m else ""


        from . import consent
        if host and consent.base(host) == consent.base(site):
            out.append(t["id"])
    return out
