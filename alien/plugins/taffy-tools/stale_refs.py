""























import re

PAGE_TOOLS = ("browser_snapshot", "browser_click", "browser_navigate", "browser_back")
ACT_TOOLS = ("browser_click", "browser_type")

_REF_LINE = re.compile(r'^[ \t]*-[ \t]+(?P<role>[^"\[\n]*)(?:"(?P<name>(?:[^"\\\n]|\\.)*)")?.*?\bref=(?P<ref>e\d+)[,\]]',
                       re.M)
_KEEP_TASKS = 64
_TAB = frozenset(("tab", "shift+tab"))
_CHANGES_ANSWER = frozenset(("space", " ", "enter", "return", "arrowup", "arrowdown", "arrowleft", "arrowright"))
MISSED = "The click did not take: @{ref} is still unchecked. Read the page before you try another way, and never use keys to reach an answer."

_tasks: dict = {}


def _page(snapshot: str) -> dict:
    ""


    page, count = {}, {}
    for m in _REF_LINE.finditer(snapshot):
        element = (m["role"].strip(), m["name"] or "")
        count[element] = count.get(element, 0) + 1
        page[m["ref"]] = (*element, count[element])
    return page


def saw(task_id: str, snapshot: str, tool_name: str = "") -> None:
    ""

    page = _page(snapshot)
    if not page:
        return
    state = _tasks.setdefault(task_id or "default", {"batch": "", "seen": page, "latest": page})
    state["latest"], state["text"] = page, snapshot
    if tool_name != "browser_click":
        state["unread"] = False
    while len(_tasks) > _KEEP_TASKS:
        _tasks.pop(next(iter(_tasks)))


def element(task_id: str, ref: str) -> tuple:
    ""
    state = _tasks.get(task_id or "default")
    return (state["latest"].get(ref.lstrip("@")) or ("", ""))[:2] if state else ("", "")


def text(task_id: str) -> str | None:
    ""
    state = _tasks.get(task_id or "default")
    return state.get("text") if state else None


def only(snapshot: str, element: tuple) -> str:
    ""
    if not element or not element[1]:
        return ""
    found = [ref for ref, now in _page(snapshot).items() if now[:2] == tuple(element[:2])]
    return found[0] if len(found) == 1 else ""


def pressed(task_id: str, key: str) -> None:
    if key.strip().lower() in _TAB and task_id in _tasks:
        _tasks[task_id]["unread"] = True


def missed(task_id: str, ref: str, snapshot: str) -> str:
    ""

    ref = ref.lstrip("@")
    state = _tasks.get(task_id)
    if not state or state["seen"].get(ref) != state["latest"].get(ref) or state["latest"].get(ref, ("",))[0] != "radio":
        return ""
    if not re.search(rf'^[ \t]*-[ \t]+radio\b[^\n]*\bchecked=false\b[^\n]*\bref={ref}\b', snapshot, re.M):
        return ""
    state["unread"] = True
    return MISSED.format(ref=ref)


def refusal(tool_name: str, args, task_id: str, batch: str) -> str:
    ""

    state = _tasks.get(task_id or "default")
    if not state or not batch:
        return ""
    if state["batch"] != batch:
        state["batch"], state["seen"] = batch, state["latest"]
    if tool_name == "browser_press" and isinstance(args, dict) and state.get("unread") and \
            str(args.get("key") or "").strip().lower() in _CHANGES_ANSWER:
        return ("Not done: you haven't seen the page since the focus moved or a click missed, so this key could change the "
                "wrong answer. Read the page with browser_snapshot first. browser_click scrolls to what it clicks.")
    if tool_name not in ACT_TOOLS or not isinstance(args, dict):
        return ""
    ref = str(args.get("ref") or "").lstrip("@")
    was, now = state["seen"].get(ref), state["latest"].get(ref)
    if not was or not now or was == now:
        return ""
    return (f"Not done: the page changed after an earlier step, so @{ref} is now a different element "
            f'({now[0]} "{now[1]}"). Use the refs in the newest snapshot.')


def remap(tool_name: str, args, task_id: str, batch: str) -> str:
    ""


    state = _tasks.get(task_id or "default")
    if not state or not batch or state["batch"] != batch or tool_name not in ACT_TOOLS or not isinstance(args, dict):
        return ""
    ref = str(args.get("ref") or "")
    was = state["seen"].get(ref.lstrip("@"))
    if not was or not was[1] or state["latest"].get(ref.lstrip("@")) == was:
        return ""


    if sum(1 for seen in state["seen"].values() if seen[:2] == was[:2]) != 1:
        return ""
    found = [r for r, now in state["latest"].items() if now[:2] == was[:2]]
    return ("@" if ref.startswith("@") else "") + found[0] if len(found) == 1 else ""
