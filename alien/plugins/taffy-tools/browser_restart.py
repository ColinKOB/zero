""
















import os
from urllib.parse import urlsplit

from . import stale_refs

WATCHED = ("browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_press", "browser_scroll",
           "browser_back", "browser_vision", "browser_console", "browser_steps", "browser_actions")
_MOVES = ("browser_navigate", "browser_click", "browser_press", "browser_back", "browser_steps", "browser_actions")
_CHOICES = ("radio", "checkbox", "switch", "option", "combobox", "listbox")
_LEAVES = ("button", "link")
_SUBMIT_KEYS = ("enter", "return")
_KEEP_TASKS = 64
_KEEP_ANSWERS = 40

RESTARTED = "The browser restarted during this step, so the step may not have reached the page."
REOPENED = " The page was reopened: {url}."
NOT_REOPENED = " The page could not be reopened: {url}. Open it with browser_navigate."
GONE = (" These answers may be gone: {names}. A restored box can show an answer the site will not send, so type each one "
        "again even if it looks filled. Set each choice to your final answer: a click flips a checkbox or switch, so read it "
        "after clicking. Then check them on the review page before you submit.")
READ_FIRST = " Old refs no longer work. Read the page with browser_snapshot first."
NOT_ENTERED = ("Not done: the browser restarted, and these answers have not been entered again: {names}. They may be blank "
               "when the site gets them, even if the page shows them. Read the page and enter them again. If you have "
               "checked and they don't apply, click this again.")

_tasks: dict = {}


def browser_id() -> str:
    ""


    try:
        from tools.bot_desktop import browser
        return os.readlink(os.path.join(str(browser.profile_dir()), "SingletonLock"))
    except Exception:
        return ""


def _state(task_id: str) -> dict:
    state = _tasks.setdefault(task_id or "default", {"browser": "", "url": "", "answers": {}, "again": {}})
    while len(_tasks) > _KEEP_TASKS:
        _tasks.pop(next(iter(_tasks)))
    return state


def _names(answers: dict) -> str:
    return ", ".join(f'"{name}"' for name in answers)


def before(tool_name: str, args, task_id: str) -> str:
    ""
    if tool_name not in ("browser_type", "browser_click", "browser_press") or not isinstance(args, dict):
        return ""
    state = _state(task_id)
    role, name = stale_refs.element(task_id, str(args.get("ref") or ""))
    if tool_name == "browser_type" or role in _CHOICES:
        name = name.strip() or str(args.get("ref") or "").lstrip("@")
        state["answers"][name] = True
        state["again"].pop(name, None)
        while len(state["answers"]) > _KEEP_ANSWERS:
            state["answers"].pop(next(iter(state["answers"])))
        return ""
    leaves = role in _LEAVES if tool_name == "browser_click" else str(args.get("key") or "").strip().lower() in _SUBMIT_KEYS
    if not leaves or not state["again"]:
        return ""
    names = _names(state["again"])
    state["again"] = {}
    return NOT_ENTERED.format(names=names)


def _at(state: dict, url: str) -> None:
    ""

    if urlsplit(url).scheme not in ("http", "https"):
        return
    if urlsplit(url).netloc != urlsplit(state["url"]).netloc:
        state["answers"], state["again"] = {}, {}
    state["url"] = url


def after(task_id: str, tool_name: str, read_url, reopen) -> str:
    ""

    state = _state(task_id)
    now = browser_id()
    if not now:
        return ""
    was, state["browser"] = state["browser"], now
    if not was or was == now:
        if tool_name in _MOVES or not state["url"]:
            _at(state, read_url())
        return ""
    here = read_url()


    blank = here.strip().lower().split("#")[0] == "about:blank"
    known = blank or urlsplit(here).scheme in ("http", "https")
    if known and not blank:
        _at(state, here)
    if known and not state["answers"] and (not blank or not state["url"]):
        return ""
    note = RESTARTED
    if blank and state["url"] and tool_name != "browser_navigate":
        try:
            reopen(state["url"])
            note += REOPENED.format(url=state["url"])
        except Exception:
            note += NOT_REOPENED.format(url=state["url"])
    if state["answers"]:
        state["again"] = dict(state["answers"])
        note += GONE.format(names=_names(state["again"]))
    return note + READ_FIRST
