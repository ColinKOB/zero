"""Read Hermes's kanban board directly (read-only) and act on it through the Hermes CLI."""

import json
import os
import sqlite3
import subprocess
import time

from . import config, who

# Board statuses as the app groups them.
WORKING = ("ready", "running", "todo", "triage", "scheduled")
NEEDS_YOU = ("blocked", "review", "triage")
FINISHED = ("done",)


class _conn:
    """Read-only connection to Hermes's board that always closes."""
    def __enter__(self) -> sqlite3.Connection:
        self.c = sqlite3.connect(f"file:{config.KANBAN_DB}?mode=ro", uri=True, timeout=5)
        self.c.row_factory = sqlite3.Row
        return self.c

    def __exit__(self, *exc):
        self.c.close()


ATTACHMENTS_ROOT = (config.HERMES_HOME / "kanban").resolve()


def _task_row(c: sqlite3.Connection, r: sqlite3.Row) -> dict:
    run = c.execute(
        "SELECT status, outcome, summary, error, started_at, ended_at FROM task_runs "
        "WHERE task_id = ? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
    attachments = c.execute(
        "SELECT id, filename, size, content_type FROM task_attachments WHERE task_id = ? ORDER BY id",
        (r["id"],)).fetchall()
    block_reason = None
    if r["status"] in ("blocked", "triage"):
        ev = c.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND kind IN ('blocked', 'block_loop_detected') "
            "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        if ev and ev["payload"]:
            try:
                block_reason = json.loads(ev["payload"]).get("reason")
            except ValueError:
                block_reason = ev["payload"]
    return {
        "id": r["id"],
        "title": r["title"],
        "body": r["body"],
        "assignee": r["assignee"],
        # A task that blocked on the same thing repeatedly waits in triage; to Colin it is just blocked.
        "status": "blocked" if r["status"] == "triage" else r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "started_at": r["started_at"],
        "completed_at": r["completed_at"],
        "summary": (run["summary"] if run else None) or r["result"],
        "error": run["error"] if run else None,
        "block_reason": block_reason,
        "attachments": [dict(a) for a in attachments],
    }


def grouped() -> dict[str, list[dict]]:
    """Every task that needs Colin or is in progress (no limit), plus the 60 most recently finished."""
    def q(c, statuses, order, limit=None):
        sql = (f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY {order}"
               + (" LIMIT ?" if limit else ""))
        rows = c.execute(sql, (*statuses, *((limit,) if limit else ()))).fetchall()
        return [_task_row(c, r) for r in rows]
    with _conn() as c:
        return {
            "needs_you": q(c, NEEDS_YOU, "created_at DESC"),
            "working": q(c, WORKING, "created_at DESC"),
            "done": q(c, FINISHED, "COALESCE(completed_at, created_at) DESC", 60),
        }


def get_task(task_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not r:
            return None
        task = _task_row(c, r)
        task["comments"] = [dict(x) for x in c.execute(
            "SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (task_id,))]
        task["events"] = [dict(x) for x in c.execute(
            "SELECT kind, payload, created_at FROM task_events WHERE task_id = ? ORDER BY id", (task_id,))]
        return task


def attachment_path(task_id: str, attachment_id: int) -> tuple[str, str] | None:
    """(name, real path) of a board attachment. Only regular files inside Hermes's kanban attachment store are
    served, whatever the database says: links and ".." are followed first, and protected or hard-linked files are
    refused (safe_files.py)."""
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
    """(name, open file) for a board attachment, checked as attachment_path and opened without following a link,
    so a link swapped in after the check is refused too. None when it may not be served."""
    found = attachment_path(task_id, attachment_id)
    if not found:
        return None
    from . import safe_files
    try:
        return found[0], safe_files.open_file(found[1], (ATTACHMENTS_ROOT,))
    except safe_files.Refused:
        return None


def events_after(event_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT e.id, e.task_id, e.kind, e.payload, e.created_at, t.title, t.assignee, t.created_by, t.status "
            "FROM task_events e JOIN tasks t ON t.id = e.task_id WHERE e.id > ? ORDER BY e.id LIMIT 500",
            (event_id,)).fetchall()
        return [dict(r) for r in rows]


def latest_event_id() -> int:
    with _conn() as c:
        row = c.execute("SELECT COALESCE(MAX(id), 0) AS m FROM task_events").fetchone()
        return row["m"]


def long_running(min_age_sec: int) -> list[dict]:
    """Tasks whose current run has gone on longer than min_age_sec. Hermes has no time limit for workers, and
    a worker stuck on a page keeps its heartbeat going, holding that bot's only slot."""
    cutoff = int(time.time()) - min_age_sec
    with _conn() as c:
        rows = c.execute(
            "SELECT t.id, t.title, t.assignee FROM tasks t LEFT JOIN task_runs r ON r.id = t.current_run_id "
            "WHERE t.status = 'running' AND COALESCE(r.started_at, t.started_at) < ?", (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def bot_load() -> dict[str, dict]:
    """Per-assignee counts of open work, for the Team screen."""
    out: dict[str, dict] = {}
    if not config.KANBAN_DB.exists():   # a new home before the gateway made its board: nothing is open yet
        return out
    with _conn() as c:
        for r in c.execute("SELECT assignee, status, COUNT(*) AS n FROM tasks "
                           "WHERE status IN ('ready','running','todo','blocked','review','scheduled') "
                           "GROUP BY assignee, status"):
            d = out.setdefault(r["assignee"] or "default", {"working": 0, "needs_you": 0})
            if r["status"] in NEEDS_YOU:
                d["needs_you"] += r["n"]
            else:
                d["working"] += r["n"]
    return out


def _cli(*args: str) -> tuple[bool, str]:
    """Runs a Hermes kanban verb. Callers pass options as --opt=value and put task ids after "--"."""
    env = dict(os.environ, HERMES_HOME=str(config.HERMES_HOME))
    p = subprocess.run([str(config.HERMES_BIN), "kanban", *args], capture_output=True, text=True,
                       timeout=60, env=env)
    out = "\n".join(l for l in (p.stdout + p.stderr).splitlines()
                    if "SyntaxWarning" not in l and "WSL launcher" not in l)
    return p.returncode == 0, out.strip()


def _note(text: str) -> str:
    """Notes are positional text; never let one be read as an option."""
    return text if not text.startswith("-") else " " + text


def approve(task_id: str, note: str | None) -> tuple[bool, str]:
    """Colin approves work a bot sent for review. The bot gets the task back with his go-ahead."""
    msg = f"{who.first()} approved this in the app. Go ahead exactly as prepared"
    msg += f". {who.possessive()} note: {note}" if note else "."
    return _cli("request-changes", "--", task_id, _note(msg))


def request_changes(task_id: str, note: str) -> tuple[bool, str]:
    return _cli("request-changes", "--", task_id, _note(f"{who.first()} asked for changes in the app: {note}"))


def mark_done(task_id: str, note: str | None) -> tuple[bool, str]:
    """Colin closes the task himself (for example, he will press the final button)."""
    # The result is shown to Colin as the task's summary, so it speaks to him (Oct 8 QA, M8).
    return _cli("complete", "--force", f"--result={note or 'You closed this yourself.'}", "--", task_id)


def drop_waiting(task_id: str, note: str | None) -> tuple[bool, str]:
    """Closes a task that is waiting on Colin (blocked or in review), from one of Zero's questions. Unlike Work's
    Mark Done it never forces: if a worker picked the task up meanwhile, Hermes refuses and nothing is closed
    (QA round 10, L7)."""
    return _cli("complete", f"--result={note or 'You closed this yourself.'}", "--", task_id)


def answer_block(task_id: str, answer: str) -> tuple[bool, str]:
    """Answers a blocked task and puts it back in the queue. A task that blocked on the same thing
    several times sits in triage instead; it gets the answer as a comment and goes back to the queue."""
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
    return _cli("unblock", f"--reason={who.first()} answered in the app: {answer}", "--", task_id)


def comment(task_id: str, text: str) -> tuple[bool, str]:
    return _cli("comment", f"--author={who.first()}", "--", task_id, _note(text))


def waiting_signins(site: str, bot_ids: list[str]) -> list[str]:
    """Blocked "Sign in to ..." tasks for these bots whose sign-in address is on this site."""
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
        # The same company rule as every other sign-in check: a login saved for crm.bloomerang.co answers a
        # task waiting on identity.bloomerang.co (Oct 8 QA, L3).
        from . import consent
        if host and consent.base(host) == consent.base(site):
            out.append(t["id"])
    return out
