""











import logging
import os
import re
import time

from . import gate, home

log = logging.getLogger(__name__)

MAX_TASKS = 3
RESULT_CHARS = 600
TITLE_CHARS = 100
RECENT_SEC = 7 * 86400
POOL = 50
MIN_SHARED = 2
MIN_COVERED = 0.75

_IGNORED = frozenset("""
    about after also around asked before being could does done each every first from have into just last made make need
    needs next only other over same should some still that their them then there these they this those under what when
    where which while will with would your task tasks card cards draft
    january february march april may june july august september october november december
    jan feb mar apr jun jul aug sep sept oct nov dec
    monday tuesday wednesday thursday friday saturday sunday mon tue wed thu fri sat sun today tomorrow yesterday tonight
""".split())


def _words(text) -> set[str]:
    ""
    found = set()
    text = str(text or "").lower().replace("’", "'").replace("'s", "")
    for word in re.findall(r"[a-z][a-z0-9]+", text):
        if len(word) > 4:
            word = re.sub(r"(ing|ed|s)$", "", word)
            word = re.sub(r"([b-df-hj-np-tv-z])\1$", r"\1", word)
        if len(word) > 2 and word not in _IGNORED:
            found.add(word)
    return found


def overlap(title: str, body: str, other_title: str, other_body: str) -> int:
    ""
    ignored = {home.first_name().lower()}
    best = 0
    for head, text in ((title, f"{other_title} {other_body}"), (other_title, f"{title} {body}")):
        words = _words(head) - ignored
        shared = words & _words(text)
        if len(shared) >= MIN_SHARED and len(shared) >= MIN_COVERED * len(words):
            best = max(best, len(shared))
    return best


def _clip(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _stamp(at) -> str:
    return time.strftime("%b %-d, %-I:%M %p", time.localtime(float(at)))


def _latest(task: str, assignee: str, result) -> tuple[float, str] | None:
    ""

    found = [(float(at or 0), text) for text, at in gate._kanban(
        "SELECT body, created_at FROM task_comments WHERE task_id=? AND author=? ORDER BY id DESC LIMIT 1", (task, assignee))]
    found += [(float(at or 0), text) for text, at in gate._kanban(
        "SELECT summary, COALESCE(ended_at, started_at) FROM task_runs WHERE task_id=? AND COALESCE(summary, '') != '' "
        "ORDER BY COALESCE(ended_at, started_at) DESC, id DESC LIMIT 1", (task,))]
    if found:
        return max(found, key=lambda f: f[0])
    return (0.0, result) if result else None


def lines(assignee: str, title: str, body: str, *skip: str) -> list[str]:
    ""

    rows = gate._kanban(
        "SELECT id, title, body, status, result FROM tasks WHERE COALESCE(assignee, 'default') = ? "
        "AND status != 'archived' AND (status != 'done' OR completed_at > ?) ORDER BY created_at DESC LIMIT ?",
        (assignee, time.time() - RECENT_SEC, POOL))
    scored = [(overlap(title, body, r[1], r[2]), n, r) for n, r in enumerate(rows) if r[0] not in skip]
    picked = sorted((s for s in scored if s[0]), key=lambda s: (s[2][3] == "done", -s[0], s[1]))[:MAX_TASKS]
    out = []
    for _, _, (task, other_title, _, status, result) in picked:
        latest = _latest(task, assignee, result)
        said = "nothing on record yet" if not latest else \
            f"latest{', ' + _stamp(latest[0]) if latest[0] else ''}: {_clip(latest[1], RESULT_CHARS)}"
        out.append(f"- {task} ({status}) {_clip(other_title, TITLE_CHARS)}. {said}")
    return out


START = ("Your other board tasks touch the same work, and this task's words may not know about them. Read these lines "
         "before you write anything anywhere. Leave out what they did or are doing, and name it in your summary. "
         "kanban_show with a task's id gives its full record.")
CREATED = ("{task} is made and will start on its own. These other tasks of {who} touch the same work. If part of it "
           "repeats one of them, say so now with kanban_comment on {task}, or the same thing may be done twice.")


def start_pre_llm_call(is_first_turn: bool = False, **kw):
    ""
    task = os.environ.get("HERMES_KANBAN_TASK", "")
    if not task or not is_first_turn:
        return None
    try:
        rows = gate._kanban("SELECT title, body, COALESCE(assignee, 'default') FROM tasks WHERE id=?", (task,))
        found = lines(rows[0][2], rows[0][0], rows[0][1], task) if rows else []
    except Exception:
        log.warning("could not read %s's other tasks", task, exc_info=True)
        return None
    return {"context": "\n".join((START, *found))} if found else None


def created_note(args: dict, task: str) -> str:
    ""
    try:
        assignee = str(args.get("assignee") or gate._bot())

        found = lines(assignee, str(args.get("title") or ""), str(args.get("body") or ""), task,
                      os.environ.get("HERMES_KANBAN_TASK", ""))
        who = "yours" if assignee == gate._bot() else gate._bot_name(assignee)
    except Exception:
        log.warning("could not read the other tasks for %s", task, exc_info=True)
        return ""
    return "\n".join((CREATED.format(task=task, who=who), *found)) if found else ""
