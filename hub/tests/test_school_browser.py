"""Oct 9 QA (docs/qa/2026-10-09-school-browser.md): School could not open the HIS 203 course page.

agent-browser clicks the middle of an element's box. Moodle's course link wraps onto two lines, the middle falls in the
gap between them, and the click does nothing while reporting success. A click on a link to another page that leaves
the page where it was now opens the link's own address, through the gate's browser_navigate check: in browser_steps
and, through a tool_execution middleware, in the bot's own browser_click.

The grounding model and the judge are mocked; nothing here needs Hermes, the network or a browser.

Run: python3 -m unittest discover -s hub/tests
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, gate, tt  # noqa: E402

fast_steps, link_click = tt.fast_steps, tt.link_click

COURSES = "https://elearning.lakeview.test/my/courses.php"
COURSE = "https://elearning.lakeview.test/course/view.php?id=35272"


class FakeBrowser:
    """The Zip's browser as Hermes's tools and agent-browser see it. A click on a "wrapped" link does nothing, as
    agent-browser's click did on Oct 9; every other link click opens its page."""

    def __init__(self, url, links, wrapped=()):
        self.url = url
        self.links = links            # ref -> (role, name, href or None)
        self.wrapped = set(wrapped)
        self.calls = []

    def snapshot(self) -> str:
        return "\n".join(f'- {role} "{name}" [ref={ref}]' for ref, (role, name, _h) in self.links.items())

    def dispatch(self, name, args, **kw):
        self.calls.append((name, dict(args)))
        if name == "browser_snapshot":
            return json.dumps({"snapshot": self.snapshot()})
        if name == "browser_navigate":
            self.url = args["url"]
            return json.dumps({"success": True, "url": self.url})
        if name == "browser_click":
            ref = str(args["ref"]).lstrip("@")
            href = self.links[ref][2]
            if href and ref not in self.wrapped:
                self.url = href
            return json.dumps({"success": True, "clicked": "@" + ref})
        return json.dumps({"success": True})

    def dispatched(self, name):
        return [a for n, a in self.calls if n == name]


class FakePage:
    """link_click.Page over FakeBrowser: the address and a ref's href, as agent-browser's get url / get attr."""

    def __init__(self, browser):
        self.browser = browser

    def url(self):
        return self.browser.url

    def href(self, ref):
        return self.browser.links[str(ref).lstrip("@")][2] or ""


def moodle_courses():
    return FakeBrowser(COURSES, {
        "e83": ("link", "HIS 203 - C20: OT I: The Pentateuch Fall 2026", COURSE),
        "e87": ("link", "Course name HIS 203 - C20: OT I: The Pentateuch Fall 2026", COURSE),
        "e88": ("button", "Actions for course HIS 203 - C20", None),
        "e90": ("link", "Skip to main content", COURSES + "#maincontent"),
        "e91": ("link", "Show more", "javascript:void(0)"),
    }, wrapped={"e87"})


class Clock:
    """A fake monotonic clock that the fake sleep advances."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, sec):
        self.t += sec


class LinkClick(unittest.TestCase):
    """link_click.click on its own."""

    def setUp(self):
        self.browser = moodle_courses()
        self.page = FakePage(self.browser)
        self.clock = Clock()
        self.opened = []

    def click(self, ref, page=None):
        return link_click.click(ref, lambda: self.browser.dispatch("browser_click", {"ref": ref}), page or self.page,
                                lambda url: (self.opened.append(url), self.browser.dispatch("browser_navigate",
                                                                                            {"url": url})),
                                sleep=self.clock.sleep, clock=self.clock)

    def test_a_wrapped_course_link_that_does_nothing_opens_the_course(self):
        out, opened = self.click("e87")
        self.assertEqual(opened, COURSE)
        self.assertEqual(self.opened, [COURSE])
        self.assertEqual(self.browser.url, COURSE)
        self.assertEqual(len(self.browser.dispatched("browser_click")), 1, "the click runs once")
        self.assertGreaterEqual(self.clock.t, link_click.WAIT_SEC, "it waits for the click's own page first")

    def test_a_link_click_that_works_opens_nothing_more(self):
        out, opened = self.click("e83")
        self.assertIsNone(opened)
        self.assertEqual(self.opened, [])
        self.assertEqual(self.clock.t, 0.0, "no waiting once the page has moved")

    def test_buttons_same_page_anchors_and_script_links_are_left_alone(self):
        for ref in ("e88", "e90", "e91"):
            with self.subTest(ref=ref):
                self.clock.t = 0.0
                out, opened = self.click(ref)
                self.assertIsNone(opened)
                self.assertEqual(self.clock.t, 0.0, "nothing to wait for")
        self.assertEqual(self.opened, [])

    def test_a_failed_click_opens_nothing(self):
        failing = lambda: json.dumps({"success": False, "error": "Element not found"})  # noqa: E731
        out, opened = link_click.click("e87", failing, self.page, self.opened.append,
                                       sleep=self.clock.sleep, clock=self.clock)
        self.assertIsNone(opened)
        self.assertEqual(self.opened, [])

    def test_an_unreadable_address_after_the_click_opens_nothing(self):
        class Blind(FakePage):
            calls = 0

            def url(self):
                Blind.calls += 1
                return COURSES if Blind.calls == 1 else ""
        out, opened = self.click("e87", page=Blind(self.browser))
        self.assertIsNone(opened)
        self.assertEqual(self.opened, [])

    def test_a_page_that_cannot_be_read_before_the_click_just_clicks(self):
        class Broken(FakePage):
            def url(self):
                raise RuntimeError("daemon gone")
        out, opened = self.click("e87", page=Broken(self.browser))
        self.assertIsNone(opened)
        self.assertEqual(len(self.browser.dispatched("browser_click")), 1)

    def test_relative_hrefs_are_resolved_against_the_page(self):
        page = link_click.Page("t-1")
        with mock.patch.object(link_click.Page, "_run",
                               lambda self, cmd, args: {"origin": COURSES, "value": "/course/view.php?id=35272"}):
            self.assertEqual(page.href("e87"), COURSE)
        with mock.patch.object(link_click.Page, "_run", lambda self, cmd, args: {"origin": COURSES, "value": None}):
            self.assertEqual(page.href("e88"), "")

    def test_other_page(self):
        self.assertTrue(link_click.other_page(COURSE, COURSES))
        self.assertFalse(link_click.other_page(COURSES + "#maincontent", COURSES))
        self.assertFalse(link_click.other_page("javascript:void(0)", COURSES))
        self.assertFalse(link_click.other_page("mailto:ges@lakeview.test", COURSES))


class MoodleChat(GateCase):
    """School's chat on the eLearning My courses page, with the plugin registered through register() and a fake Hermes
    context, as on alien."""

    def setUp(self):
        super().setUp()
        self.browser = moodle_courses()

        def decide(body):
            q = body["questions"][0]
            if q["type"] == "predicate":
                on_course = "course/view.php" in self.browser.url
                return {"type": "predicate", "probability": 1.0 if on_course else 0.0}
            return {"type": "choice", "choice": "e87", "confidence": 0.98}

        page = types.ModuleType("tools.browser_vault_tool")
        page._current_page_origin = lambda task_id: "https://elearning.lakeview.test"
        tools_pkg = types.ModuleType("tools")
        tools_pkg.browser_vault_tool = page
        for patcher in (mock.patch.object(fast_steps, "_decide", decide),
                        mock.patch.object(fast_steps, "browser_steps_on", lambda: True),
                        mock.patch.object(tt, "_no_browser_password_manager", lambda: None),
                        mock.patch.object(link_click, "Page", lambda task_id: FakePage(self.browser)),
                        mock.patch.object(link_click, "WAIT_SEC", 0.05),
                        mock.patch.object(link_click, "POLL_SEC", 0.01),
                        mock.patch.dict(sys.modules, {"tools": tools_pkg, "tools.browser_vault_tool": page})):
            patcher.start()
            self.addCleanup(patcher.stop)
        handlers, self.middleware = {}, []
        ctx = types.SimpleNamespace(
            register_tool=lambda name, toolset, schema, handler: handlers.__setitem__(name, handler),
            register_hook=lambda *a, **kw: None, dispatch_tool=self.browser.dispatch,
            register_middleware=lambda kind, cb: self.middleware.append((kind, cb)))
        tt.register(ctx)
        self.steps = handlers["browser_steps"]
        self.ask("Gary emailed me. His class starts soon and I don't know what to buy. When does it start?")


class StepsOpenWrappedLinks(MoodleChat):
    """browser_steps."""

    def test_the_bib_203_click_reaches_the_course_page(self):
        out = self.steps({"steps": ["click link \"HIS 203 - C20: OT I: The Pentateuch Fall 2026\" in Course overview"],
                          "goal": "Is the HIS 203 course page open?"}, session_id="chat-1", task_id="t-1")
        self.assertEqual(self.browser.dispatched("browser_navigate"), [{"url": COURSE}])
        self.assertIn("its page was opened directly: " + COURSE, out)
        self.assertIn("probability 1.00", out)

    def test_the_direct_open_goes_through_the_gate(self):
        seen = []
        real = gate.pre_tool_call

        def spy(name, args, **kw):
            seen.append((name, dict(args or {})))
            return real(name, args, **kw)
        with mock.patch.object(gate, "pre_tool_call", spy):
            self.steps({"steps": ["open the HIS 203 course"]}, session_id="chat-1", task_id="t-1")
        self.assertIn(("browser_navigate", {"url": COURSE}), seen)

    def test_a_refused_direct_open_stops_the_run(self):
        def refuse(name, args, **kw):
            if name == "browser_navigate":
                return {"action": "block", "message": "Not opened: Colin has not said yes."}
            return None
        with mock.patch.object(gate, "pre_tool_call", refuse):
            out = self.steps({"steps": ["open the HIS 203 course"]}, session_id="chat-1", task_id="t-1")
        self.assertEqual(self.browser.dispatched("browser_navigate"), [])
        self.assertIn("Not opened: Colin has not said yes.", out)


class ClickMiddleware(MoodleChat):
    """The bot's own browser_click, through the tool_execution middleware the plugin registers."""

    def mw(self):
        found = [cb for kind, cb in self.middleware if kind == "tool_execution"]
        self.assertEqual(len(found), 1)
        return found[0]

    def click(self, ref, **kw):
        nexts = []

        def next_call(args):
            nexts.append(args)
            return self.browser.dispatch("browser_click", args)
        out = self.mw()(tool_name="browser_click", args={"ref": ref}, next_call=next_call,
                        task_id="t-1", session_id="chat-1", **kw)
        self.assertEqual(len(nexts), 1, "the real click runs exactly once")
        return out

    def test_the_wrapped_link_click_opens_the_course(self):
        out = json.loads(self.click("@e87"))
        self.assertTrue(out["success"])
        self.assertEqual(out["opened"], COURSE)
        self.assertIn("opened directly", out["note"])
        self.assertEqual(self.browser.url, COURSE)

    def test_a_working_click_is_returned_unchanged(self):
        out = self.click("e83")
        self.assertEqual(json.loads(out), {"success": True, "clicked": "@e83"})
        self.assertEqual(self.browser.dispatched("browser_navigate"), [])

    def test_a_refused_direct_open_is_said_and_nothing_opens(self):
        with mock.patch.object(gate, "pre_tool_call",
                               lambda name, args, **kw: {"action": "block", "message": "Not opened: no yes."}):
            out = json.loads(self.click("e87"))
        self.assertIn("Not opened: no yes.", out["note"])
        self.assertEqual(self.browser.dispatched("browser_navigate"), [])
        self.assertEqual(self.browser.url, COURSES)

    def test_other_tools_pass_straight_through(self):
        calls = []
        out = self.mw()(tool_name="browser_snapshot", args={}, next_call=lambda a: calls.append(a) or "snap",
                        task_id="t-1")
        self.assertEqual((out, calls), ("snap", [{}]))


if __name__ == "__main__":
    unittest.main()
