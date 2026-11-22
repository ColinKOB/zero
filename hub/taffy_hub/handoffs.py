""
























import logging
import re
import time
from datetime import datetime, timedelta

from . import board, config, consent, home, store, things, waits, who

log = logging.getLogger("taffy.handoffs")

QUEUED_SEC = 6 * 3600
TOLD = "quiet_handoffs_told"
LATE_TOLD = "late_handoffs_told"
RESULT_BY = re.compile(r"^[\s*_-]*result by[*_]*:[*_\s]*(.+?)[*_\s]*$", re.I | re.M)
DAY_ENDS = (17, 21)
MORNING = 9
MIN_LEAD_SEC = 2 * 3600
LATE_LOOKBACK_SEC = 7 * 86400


def _ago(seconds: float) -> str:
    hours = int(seconds // 3600)
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours // 24} days"


def note(zip_name: str, title: str, status: str, waited: float) -> str:
    first = who.first()
    doing = (f'{zip_name} has been working on "{title}" for {_ago(waited)} without finishing.' if status == "running"
             else f'{zip_name} has not started "{title}" in {_ago(waited)}.')
    return (f"{doing} You handed it that task. Check with {zip_name} or take the task back, and tell {first} only if "
            f"it changes something {first} is waiting on.")


def quiet(now: float | None = None) -> tuple[list[dict], set[str]]:
    ""
    if not config.KANBAN_DB.exists():
        return [], set()
    now = time.time() if now is None else now
    with board._conn() as c:
        rows = c.execute(
            "SELECT t.id, t.title, t.assignee, t.status, t.created_at, COALESCE(r.started_at, t.started_at) AS run, "
            "(SELECT MAX(e.created_at) FROM task_events e WHERE e.task_id = t.id) AS moved "
            "FROM tasks t LEFT JOIN task_runs r ON r.id = t.current_run_id "
            "WHERE t.created_by = ? AND t.status IN ('todo', 'ready', 'running')", (config.LEAD.id,)).fetchall()
    owed = None
    out = []
    for r in rows:
        bot = config.BOTS_BY_ID.get(r["assignee"] or config.LEAD.id)
        if not bot or bot.id == config.LEAD.id or config.is_sandbox(bot.id):
            continue
        if r["status"] == "running":
            since, limit = r["run"], board.LONG_TASK_SEC
        else:
            since, limit = r["moved"] or r["created_at"], QUEUED_SEC
        if not since or now - float(since) < limit:
            continue
        if owed is None:
            owed = board.owed_wakes()
        if r["id"] in owed:
            continue
        out.append({"id": r["id"], "title": " ".join(str(r["title"] or "").split())[:200], "zip": bot.name,
                    "status": r["status"], "since": float(since)})
    return out, {r["id"] for r in rows}


def tell_zero(now: float | None = None) -> list[str]:
    ""

    now = time.time() if now is None else now
    found, still_open = quiet(now)
    told = store.get_setting(TOLD, {}) or {}
    woken = store.get_setting(LATE_TOLD, {}) or {}

    keep, said = {k: v for k, v in told.items() if k in still_open}, []
    for q in found:
        state = f"{q['status']}:{int(q['since'])}"
        keep[q["id"]] = state
        if told.get(q["id"]) == state or q["id"] in woken:
            continue
        store.add_card_note(config.LEAD.id, note(q["zip"], q["title"], q["status"], now - q["since"]),
                            queue=store.BOARD_NOTES, keep=store.BOARD_NOTES_MAX)
        said.append(q["id"])
    if keep != told:
        store.set_setting(TOLD, keep)
    if said:
        log.info("told Zero about quiet hand-offs: %s", said)
    return said




def default_due(made: float) -> float:
    ""
    day = datetime.fromtimestamp(made, home.zone())
    for hour in DAY_ENDS:
        end = day.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()
        if end - made >= MIN_LEAD_SEC:
            return end
    return (day + timedelta(days=1)).replace(hour=MORNING, minute=0, second=0, microsecond=0).timestamp()


def result_by(body: str | None, made: float) -> float:
    ""
    found = RESULT_BY.search(body or "")
    if found:
        try:
            return waits.parse_due(found.group(1), at=made)
        except waits.Invalid:
            log.info("a hand-off's result time could not be read, so it gets the default")
    return default_due(made)


def _waits_on_person(task: dict, asked: set[str]) -> bool:
    ""
    return task["status"] in ("blocked", "review") or bool(task.get("mail_cards")) or task["id"] in asked


def _state(task: dict, zip_name: str) -> str:
    first = who.first()
    status = task["raw_status"]
    if status == "running":
        return f"{zip_name} is still working on it."
    if status in ("todo", "ready"):
        return f"{zip_name} has not started it."
    if status == "scheduled":
        return f"{zip_name} set it aside to wait, and no card asks {first} for anything."
    return f"{zip_name} is stuck on it, and no card asks {first} for anything."


def late_note(item: dict) -> str:
    first, name = who.first(), item["zip"]
    return (f"{who.hub_note()} Hand-off check. \"{item['title']}\" (task {item['id']}) was due to {first} by "
            f"{waits.say_date(item['due'])}. {item['state']} Read the task now with kanban_show, then get it moving: "
            f"push {name} with ask_teammate, take the next step yourself, or ask {first} the one thing it is waiting "
            f"on. If {first} already has the result, answer [SILENT]. Otherwise tell {first} in one line that it is "
            "late, what is in the way and what you are doing about it.")


def late(now: float | None = None) -> tuple[list[dict], set[str]]:
    ""
    if not config.KANBAN_DB.exists():
        return [], set()
    now = time.time() if now is None else now
    statuses = (*board.WORKING, *board.NEEDS_YOU)
    with board._conn() as c:
        rows = c.execute(f"SELECT id, title, body, assignee, created_at FROM tasks WHERE created_by = ? AND status IN "
                         f"({','.join('?' * len(statuses))})", (config.LEAD.id, *statuses)).fetchall()
    out, asked, told = [], None, store.get_setting(LATE_TOLD, {}) or {}
    for r in rows:
        bot = config.BOTS_BY_ID.get(r["assignee"] or config.LEAD.id)
        if r["id"] in told or not bot or bot.id == config.LEAD.id or config.is_sandbox(bot.id):
            continue
        due = result_by(r["body"], float(r["created_at"] or now))
        if now < due:
            continue
        task = board.get_task(r["id"])
        if asked is None:
            asked = {x["task_id"] for x in consent.pending() if x.get("task_id")} | things.asked_tasks()
        if task and not _waits_on_person(task, asked):
            out.append({"id": r["id"], "title": " ".join(str(r["title"] or "").split())[:200], "zip": bot.name,
                        "due": due, "state": _state(task, bot.name)})
    return out, {r["id"] for r in rows}


def tell_late(now: float | None = None) -> list[str]:
    ""
    now = time.time() if now is None else now
    found, still_open = late(now)
    told = store.get_setting(LATE_TOLD, {}) or {}
    keep, woke = {k: v for k, v in told.items() if k in still_open}, []
    for item in found:
        if now - item["due"] <= LATE_LOOKBACK_SEC:
            waits.queue_wake(config.LEAD.id, late_note(item))
            woke.append(item["id"])
        keep[item["id"]] = item["due"]
        store.set_setting(LATE_TOLD, keep)
    if keep != told:
        store.set_setting(LATE_TOLD, keep)
    if woke:
        waits.kick()
        log.info("woke Zero about late hand-offs: %s", woke)
    return woke
