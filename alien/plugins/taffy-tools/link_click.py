""
























import json
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

WAIT_SEC = 3.0
POLL_SEC = 0.25
NOTE = ("The click did not open the link (agent-browser clicked between the two lines of a wrapped link), "
        "so its page was opened directly: {url}")


class NotOpened(Exception):
    ""


class Page:
    ""

    def __init__(self, task_id: str | None):
        self.task_id = task_id or "default"
        self.stalled = False

    def _raw(self, command: str, args: list) -> dict:
        if self.stalled:
            return {}
        from tools import browser_tool, browser_tool_session
        key = browser_tool._last_session_key(self.task_id)
        res = browser_tool_session._run_browser_command(key, command, args, timeout=10) or {}
        if str(res.get("error") or "").startswith("Command timed out"):
            self.stalled = True
            browser_tool._suspect_browser_sessions.pop(key, None)
        return res

    def _run(self, command: str, args: list) -> dict:
        res = self._raw(command, args)
        return (res.get("data") or {}) if res.get("success") else {}

    def url(self) -> str:
        return str(self._run("get", ["url"]).get("url") or "")

    def scroll_to(self, ref: str) -> None:
        self._run("scrollintoview", [at(ref)])

    def click_center(self, ref: str) -> bool:
        ""

        self.scroll_to(ref)
        box = self._run("get", ["box", at(ref)])
        box = box.get("box") if isinstance(box.get("box"), dict) else box
        try:
            x = float(box["x"]) + float(box["width"]) / 2
            y = float(box["y"]) + float(box["height"]) / 2
        except (KeyError, TypeError, ValueError):
            return False
        return all(self._raw("mouse", step).get("success") for step in
                   (["move", f"{x:.0f}", f"{y:.0f}"], ["down"], ["up"]))

    def href(self, ref: str) -> str:
        ""
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
    ""
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
    ""


    try:
        here, target = page.url(), page.href(ref)
    except Exception:
        here, target = "", ""
    try:
        page.scroll_to(ref)
    except Exception:
        pass
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
            return out, None
        if clock() >= deadline:
            break
        sleep(POLL_SEC)
    if now != here:
        return out, None
    open_url(target)
    return out, target


def annotate(out, opened: str | None):
    ""
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
    ""
    note = f"The click did not open the link, and opening its page directly was not allowed: {why}"
    try:
        data = json.loads(out) if isinstance(out, str) else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        data["note"] = note
        return json.dumps(data, ensure_ascii=False)
    return f"{out}\n\n{note}"
