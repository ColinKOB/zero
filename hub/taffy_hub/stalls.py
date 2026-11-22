""
























from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from . import board, config, store

log = logging.getLogger("taffy.stalls")

REPEAT_RUNS = 4
AGAIN_RUNS = 2
QUIET_SEC = 3 * 3600
REFUSED_SEC = 24 * 3600
REFUSED_AGAIN_SEC = 3600
TOLD = "stalled_tasks"
NOTE_MAX = 300
SKIP_NOTES = config.PRIVATE_ZIPS

BROKE = ("crashed", "timed_out", "spawn_failed", "gave_up")
STOPPED_KINDS = ("capability", "transient")
REFUSALS = {"active_pr": "a code change it opened is still open", "recent_success": "it finished a run moments ago",
            "blocker_auth": "its plan or sign-in was refused", "rate_limit_cooldown": "the plan is at its limit",
            "infrastructure_cooldown": "the computer could not start it"}


def _chat_db(bot_id: str) -> Path:
    return config.HERMES_HOME / ("state.db" if bot_id == config.LEAD.id else f"profiles/{bot_id}/state.db")


def last_tool_call(bot_id: str, session_id: str | None) -> float | None:
    ""
    path = _chat_db(bot_id)
    if not session_id or not path.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = c.execute("SELECT MAX(timestamp) FROM messages WHERE session_id=? AND role='tool'", (session_id,)).fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return None
    return float(row[0]) if row and row[0] else None


def _refused_since(c, task_id: str) -> tuple[float, str, bool] | None:
    ""

    other = c.execute("SELECT MAX(created_at) FROM task_events WHERE task_id=? AND kind NOT IN ('respawn_guarded', 'heartbeat')",
                      (task_id,)).fetchone()[0] or 0
    row = c.execute("SELECT MIN(created_at) AS at, MAX(id) AS last FROM task_events WHERE task_id=? AND "
                    "kind='respawn_guarded' AND created_at>=?", (task_id, other)).fetchone()
    if not row or not row["at"]:
        return None
    payload = c.execute("SELECT payload FROM task_events WHERE id=?", (row["last"],)).fetchone()[0]
    before = c.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='respawn_guarded' AND created_at<? LIMIT 1",
                       (task_id, other)).fetchone() is not None
    try:
        return float(row["at"]), str(json.loads(payload or "{}").get("reason") or ""), before
    except ValueError:
        return float(row["at"]), "", before


def _failed_runs(c, task_id: str) -> int:
    broke = c.execute(f"SELECT COUNT(*) FROM task_runs WHERE task_id=? AND outcome IN ({','.join('?' * len(BROKE))})",
                      (task_id, *BROKE)).fetchone()[0]
    stopped = c.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND kind='blocked' AND "
                        f"json_extract(payload, '$.kind') IN ({','.join('?' * len(STOPPED_KINDS))})",
                        (task_id, *STOPPED_KINDS)).fetchone()[0]
    return broke + stopped


def find(now: float | None = None) -> list[dict]:
    ""
    if not config.KANBAN_DB.exists():
        return []
    now = time.time() if now is None else now
    out = []
    with board._conn() as c:
        tasks = c.execute("SELECT t.*, r.started_at AS run_started FROM tasks t LEFT JOIN task_runs r ON r.id=t.current_run_id "
                          "WHERE t.status IN ('todo', 'ready', 'running')").fetchall()
        for t in tasks:
            bot = t["assignee"] or config.LEAD.id
            if config.is_sandbox(bot):
                continue
            runs = _failed_runs(c, t["id"])
            item = {"id": t["id"], "title": " ".join(str(t["title"] or "").split())[:200], "bot": bot, "runs": runs}
            refused = _refused_since(c, t["id"]) if t["status"] == "ready" else None
            if refused and now - refused[0] >= (REFUSED_AGAIN_SEC if refused[2] else REFUSED_SEC):
                out.append({**item, "why": "refused", "detail": refused[1], "since": refused[0]})
            elif runs >= REPEAT_RUNS and t["status"] != "running":
                last = c.execute("SELECT summary FROM task_runs WHERE task_id=? AND ended_at IS NOT NULL "
                                 "ORDER BY ended_at DESC LIMIT 1", (t["id"],)).fetchone()
                out.append({**item, "why": "runs", "detail": " ".join(str((last or [""])[0] or "").split())[:NOTE_MAX],
                            "since": float(t["created_at"] or now)})
            elif t["status"] == "running" and t["run_started"] and now - float(t["run_started"]) >= QUIET_SEC:
                moved = last_tool_call(bot, t["session_id"])
                if moved is not None and now - max(moved, float(t["run_started"])) >= QUIET_SEC:
                    out.append({**item, "why": "quiet", "detail": "", "since": max(moved, float(t["run_started"]))})
    return out


def note(item: dict, now: float) -> str:
    ""
    hours = int((now - item["since"]) // 3600)
    if item["why"] == "refused":
        cause = REFUSALS.get(item["detail"], "the board would not start it")
        return (f"I stopped this task because the board keeps refusing to restart it: {cause}. Finish or close what is "
                "holding it, or say what you need to get past it.")
    if item["why"] == "quiet":
        return (f"I stopped this task because it has run for {hours} hours without doing anything. Pick it up from your "
                "last note and take a different route, or say what is in the way.")
    last = f" The last run ended with: {item['detail']}" if item["detail"] and item["bot"] not in SKIP_NOTES else ""
    return (f"I stopped this task because it has run {item['runs']} times without finishing.{last} Don't start it the same "
            "way again. Take a different route, or say what is in the way.")


def _again(item: dict, told: dict | None) -> bool:
    ""

    if not told or told.get("why") != item["why"]:
        return True
    if item["why"] == "runs":
        return item["runs"] >= int(told.get("runs") or 0) + AGAIN_RUNS
    return float(item["since"]) > float(told.get("at") or 0)


def sweep(now: float | None = None) -> list[str]:
    ""
    now = time.time() if now is None else now
    found = find(now)
    told = store.get_setting(TOLD, {}) or {}
    owed = board.owed_wakes() if found else set()
    stopped = []
    for item in found:
        if item["id"] in owed or not _again(item, told.get(item["id"])):
            continue
        ok, detail = board.block(item["id"], note(item, now))
        if not ok:
            log.warning("could not stop stalled task %s: %s", item["id"], detail[-200:])
            continue
        told[item["id"]] = {"why": item["why"], "runs": item["runs"], "at": now}
        store.set_setting(TOLD, told)
        stopped.append(item["id"])
        log.info("stopped stalled task %s (%s)", item["id"], item["why"])
    return stopped
