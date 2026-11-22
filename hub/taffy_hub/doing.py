""




























import asyncio
import logging
import re
import time

log = logging.getLogger("taffy.doing")

ENABLED = False
FALLBACK = "Working on a task"
MAX_CHARS = 60
MAX_WORDS = 9
TIMEOUT_SEC = 12
SETTLE_SEC = 4
ATTEMPTS = 3
RETRY_WAITS = (3, 20)
AGAIN_AFTER_SEC = 600



STEP_FRESH_SEC = 90
STEP_KEEP_SEC = 3600


NOT_A_STEP = {"Working on it", "Checking the board", "Leaving a note on the task", "Wrapping up the task",
              "Marking the task as waiting on you"}

SYSTEM = """You write the status line shown under a helper's name while it works on a task. Say what it is doing right \
now in 3 to 7 plain everyday words, like "Checking POL 101's final paper" or "Drafting the reply to Joe".

- Start with a verb ending in -ing. Never start with a label or a tag, and never use a colon.
- No quotes, em dashes, emojis, markdown or tool names, and never the helper's own name.
- Keep the course, person or thing the task is about when the task names one.
- Say it in your own plain words. Never copy the task title, which one helper wrote for another.
- Never write a line that fits any task, like "Working on a task" or "Handling the request"."""

PROMPT = """Helper: {name}
Task title: {title}
Task brief: {brief}

Write the status line."""

AGAIN = """

Your last answer was "{answer}". It can't be used because {why}. Write a new status line."""

_said: dict[str, str] = {}
_asked: dict[str, asyncio.Task] = {}
_gave_up: dict[str, float] = {}
_steps: dict[str, dict] = {}
_changed: dict[str, float] = {}


_GENERIC = {"working", "on", "a", "an", "the", "task", "tasks", "it", "this", "that", "job", "work", "request",
            "stuff", "things", "something", "doing", "handling", "assigned", "current", "my", "your", "its", "now",
            "processing", "completing", "finishing", "starting", "continuing", "item", "assignment", "of", "for",
            "you", "up", "some", "new", "next", "given", "with", "to"}
_ID = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b", re.I)
_PATH = re.compile(r"(^|\s)[~/]|\S/\S*/|\\")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


def check(text: str | None, name: str = "", title: str = "") -> tuple[str | None, str]:
    ""
    t = " ".join(str(text or "").split()).strip().strip("\"'“”`").rstrip(".!").strip()
    if t[:1].islower():
        t = t[:1].upper() + t[1:]
    if not t:
        return None, "it was empty"
    if len(t) > MAX_CHARS:
        return None, f"it is longer than {MAX_CHARS} characters"
    if not 2 <= len(t.split()) <= MAX_WORDS:
        return None, f"it must be 2 to {MAX_WORDS} words"
    bad = re.search(r"[:?!*_#\[\]<>|{}@—–]", t)
    if bad:
        return None, f"it uses “{bad.group(0)}”"
    if _ID.search(t) or _PATH.search(t):
        return None, "it holds an id or a file path"
    if name and re.search(rf"\b{re.escape(name)}\b", t, re.I):
        return None, "it names the helper"
    if not re.match(r"[A-Z][a-z]*(?:-[a-z]+)*ing\b", t):
        return None, "it must start with a verb ending in -ing"
    words = _words(t)
    if all(w in _GENERIC for w in words):
        return None, "it fits any task and doesn't say what this one is about"
    if title and words == _words(title):
        return None, "it copies the task title"
    return t, ""


def clean(text: str | None, name: str = "", title: str = "") -> str | None:
    ""
    return check(text, name, title)[0]


async def _write(task: dict, name: str, last: tuple[str, str] | None = None) -> str:
    from . import board, shortreply
    if shortreply.withheld(*board.task_zips(task)):
        raise shortreply.PrivateZip("a private Zip's task, off the company its own model runs on")
    prompt = PROMPT.format(name=name, title=" ".join(str(task.get("title") or "").split())[:200],
                           brief=" ".join(str(task.get("body") or "").split())[:500] or "(none)")
    if last:
        prompt += AGAIN.format(answer=" ".join(last[0].split())[:80], why=last[1])
    with shortreply.reading_task(task):
        return await shortreply.ask_helper(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=40)


_sleep = asyncio.sleep
_clock = time.time


async def _ask(task: dict) -> None:
    from . import board, config, shortreply
    if shortreply.withheld(*board.task_zips(task)):
        _gave_up[task["id"]] = _clock()
        return
    bot = config.BOTS_BY_ID.get(task.get("assignee") or "default")
    name = bot.name if bot else ""
    title = str(task.get("title") or "")
    last = None
    for attempt in range(ATTEMPTS):
        if attempt:
            await _sleep(RETRY_WAITS[min(attempt, len(RETRY_WAITS)) - 1])
        try:
            answer = await asyncio.wait_for(_write(task, name, last), TIMEOUT_SEC + 3)
        except Exception as e:
            log.info("no status line for task %s on try %d (%s)", task.get("id"), attempt + 1, type(e).__name__)
            continue
        line, why = check(answer, name, title)
        if line:
            _said[task["id"]] = line
            _changed[task["id"]] = _clock()
            _gave_up.pop(task["id"], None)
            return
        log.info("status line for task %s refused on try %d: %s", task.get("id"), attempt + 1, why)
        last = (str(answer or ""), why)
    _gave_up[task["id"]] = _clock()


def _has_line(task_id: str) -> bool:
    return _said.get(task_id) not in (None, FALLBACK)


def start(task: dict) -> asyncio.Task | None:
    ""

    tid = task["id"]
    cur = _asked.get(tid)
    if not ENABLED or _has_line(tid) or (cur is not None and not cur.done()):
        return cur
    if cur is not None and _clock() - _gave_up.get(tid, 0) < AGAIN_AFTER_SEC:
        return cur
    _asked[tid] = asyncio.get_running_loop().create_task(_ask(task))
    return _asked[tid]


async def settle(working: list[dict]) -> None:
    ""
    pending = {_asked[t["id"]] for t in working if t.get("status") == "running" and t["id"] in _asked
               and not _asked[t["id"]].done()}
    if pending:
        await asyncio.wait(pending, timeout=SETTLE_SEC)




def note_step(task_id: str, bot_id: str, tool: str, args=None, now: float | None = None) -> str | None:
    ""

    from . import activity
    if not task_id or not bot_id:
        return None
    text = activity.label(tool, args if isinstance(args, dict) else None)
    if not text or text in NOT_A_STEP:
        return None
    now = _clock() if now is None else now
    old = _steps.get(task_id)
    if not old or old["text"] != text or old["bot"] != bot_id:
        _changed[task_id] = now
    _steps[task_id] = {"bot": bot_id, "text": text, "at": now}
    if len(_steps) > 200:
        for gone in [k for k, s in _steps.items() if now - s["at"] > STEP_KEEP_SEC]:
            _steps.pop(gone, None)
            _changed.pop(gone, None)
    return text


def _step(task: dict) -> dict | None:
    ""
    s = _steps.get(task["id"])
    return s if s and s["bot"] == (task.get("assignee") or "default") else None


def line_for(task: dict, now: float | None = None) -> str:
    ""

    now = _clock() if now is None else now
    step = _step(task)
    if step and now - step["at"] < STEP_FRESH_SEC:
        return step["text"]
    if _has_line(task["id"]):
        return _said[task["id"]]
    return step["text"] if step else FALLBACK


def written(task_id: str) -> str | None:
    ""
    return _said[task_id] if _has_line(task_id) else None


def changed_since(since: float, now: float | None = None) -> bool:
    ""

    now = _clock() if now is None else now
    if any(since < at <= now for at in _changed.values()):
        return True
    return any(since < s["at"] + STEP_FRESH_SEC <= now for s in _steps.values())


def attach(working: list[dict]) -> list[dict]:
    ""


    live = {t["id"] for t in working}
    for gone in [k for k in _said if k not in live]:
        _said.pop(gone, None)
    for gone in [k for k in _asked if k not in live and _asked[k].done()]:
        _asked.pop(gone, None)
        _gave_up.pop(gone, None)
    for gone in [k for k in _steps if k not in live]:
        _steps.pop(gone, None)
    for gone in [k for k in _changed if k not in live]:
        _changed.pop(gone, None)
    out = []
    coding = _coding({t["id"] for t in working if t.get("status") == "scheduled"})
    for t in working:
        if t["id"] in coding:

            t = {**t, "doing": CODING}
        if t.get("status") in ("ready", "running"):
            start(t)
        if t.get("status") == "running":



            t = {**t, "doing": line_for(t), **({"job": _said[t["id"]]} if _has_line(t["id"]) else {})}
        out.append(t)
    return out


CODING = "Waiting for the code change and its tests"


def _coding(parked: set[str]) -> set[str]:
    ""
    if not parked:
        return set()
    try:
        from . import codesessions
        return parked & codesessions.owed_tasks()
    except Exception:
        log.warning("could not read the coding sessions tasks wait on", exc_info=True)
        return set()


def reset() -> None:
    ""
    for d in (_said, _asked, _gave_up, _steps, _changed):
        d.clear()
