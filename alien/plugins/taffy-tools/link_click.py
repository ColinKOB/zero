"""A click on a link that wraps onto two lines opens the link (Oct 9 QA, School and HIS 203).

agent-browser (0.26 on alien, and 0.38.2 the same) clicks the middle of an element's bounding box. A link whose text
wraps onto two lines has a small gap between its two line boxes, and that gap is the middle of the box: the click lands
on the element around the link, nothing happens, and the click still reports success. On Oct 9 School clicked the
HIS 203 course link on eLearning five times this way and told Colin the course page would not open.

So after a click on a link to another page, if the page is still where it was a few seconds later, this opens the
link's own address, the page a working click would have opened. Only http and https links to a different page
qualify; buttons, same-page anchors and javascript: links are left alone, and the opening goes through the same checks
as browser_navigate. Reading the page's address and the link's href uses agent-browser's own commands, not page
JavaScript.
"""

import json
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

WAIT_SEC = 3.0
POLL_SEC = 0.25
NOTE = ("The click did not open the link (agent-browser clicked between the two lines of a wrapped link), "
        "so its page was opened directly: {url}")


class NotOpened(Exception):
    """The direct open was refused (the gate or a sensitive site); the message says why."""


class Page:
    """The bot's current page, read through agent-browser: its address and a ref's href."""

    def __init__(self, task_id: str | None):
        self.task_id = task_id or "default"

    def _run(self, command: str, args: list) -> dict:
        from tools import browser_tool, browser_tool_session
        key = browser_tool._last_session_key(self.task_id)
        res = browser_tool_session._run_browser_command(key, command, args, timeout=10) or {}
        return (res.get("data") or {}) if res.get("success") else {}

    def url(self) -> str:
        return str(self._run("get", ["url"]).get("url") or "")

    def href(self, ref: str) -> str:
        """The link's absolute address, or "" when the element has no href."""
        data = self._run("get", ["attr", at(ref), "href"])
        value = data.get("value")
        if not isinstance(value, str) or not value.strip():
            return ""
        return urljoin(str(data.get("origin") or ""), value.strip())


def at(ref: str) -> str:
    ref = str(ref or "").strip()
    return ref if ref.startswith("@") else "@" + ref


def _without_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(fragment=""))


def other_page(href: str, here: str) -> bool:
    """True when following href leaves this document for another http(s) page."""
    if urlsplit(href).scheme not in ("http", "https"):
        return False
    return _without_fragment(href) != _without_fragment(here)


def failed(out) -> bool:
    try:
        data = json.loads(out) if isinstance(out, str) else out
    except ValueError:
        return False
    return isinstance(data, dict) and (bool(data.get("error")) or data.get("success") is False)


def click(ref: str, do_click, page, open_url, sleep=time.sleep, clock=time.monotonic):
    """Run do_click() once and return (its result, the address opened directly or None).

    open_url(url) opens the link's page the way browser_navigate would, raising NotOpened when that is refused."""
    try:
        here, target = page.url(), page.href(ref)
    except Exception:
        here, target = "", ""
    out = do_click()
    if not (here and target and other_page(target, here)) or failed(out):
        return out, None
    deadline = clock() + WAIT_SEC
    now = here
    while True:
        try:
            now = page.url()
        except Exception:
            now = ""
        if now and now != here:
            return out, None          # the click worked
        if clock() >= deadline:
            break
        sleep(POLL_SEC)
    if now != here:
        return out, None              # the address could not be read; never open a page on a guess
    open_url(target)
    return out, target


def annotate(out, opened: str | None):
    """The click's result with a line saying the link's page was opened directly."""
    if not opened:
        return out
    note = NOTE.format(url=opened)
    try:
        data = json.loads(out) if isinstance(out, str) else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        data["note"] = note
        data["opened"] = opened
        return json.dumps(data, ensure_ascii=False)
    return f"{out}\n\n{note}"


def annotate_refused(out, why: str):
    """The click's result when the link did not open and opening its page directly was refused."""
    note = f"The click did not open the link, and opening its page directly was not allowed: {why}"
    try:
        data = json.loads(out) if isinstance(out, str) else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        data["note"] = note
        return json.dumps(data, ensure_ascii=False)
    return f"{out}\n\n{note}"
