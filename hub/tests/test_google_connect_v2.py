"""Connect Google v2 (docs/qa/2026-10-08-google-connect-v2.md): every connector on the person's Claude account,
the ChatGPT plan through Codex, the plan picked per account, the connectors tool's routes and guards, how requests
for other connectors are tiered, and which templates use which accounts.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien:
~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import importlib
import json
import re
import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import accounts, store  # noqa: E402
from test_accounts import SIGNED_IN, Base, load_plugin  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

LIST = """Checking MCP server health…

claude.ai Gmail: https://gmail.mcp.claude.com/mcp/abc?token=SHOULD-NOT-LEAK - ✔ Connected
claude.ai Notion: https://mcp.notion.com/mcp?secret=SHOULD-NOT-LEAK - ✔ Connected
claude.ai Slack: https://mcp.slack.com/mcp - ! Needs authentication
claude.ai Hugging Face: https://huggingface.co/mcp?login - ✔ Connected
claude.ai Linear: https://mcp.linear.app/mcp - ✘ Failed to connect — HTTP 502
plugin:design:notion: https://mcp.notion.com/mcp (HTTP) - ! Needs authentication
my-local-server: /usr/bin/thing --flag - ✔ Connected
"""

GMAIL_APP = "connector_2128aebfecb84f64a069897515042a44"
DRIVE_APP = "connector_5f3c8c41a1e54ad7a76272c89e2554fa"


class Reading(unittest.TestCase):
    def test_the_list_keeps_names_and_states_only(self):
        items = accounts.parse_list(LIST)
        self.assertEqual([(x["id"], x["name"], x["state"]) for x in items], [
            ("hugging_face", "Hugging Face", accounts.CONNECTED),
            ("linear", "Linear", accounts.TROUBLE),
            ("notion", "Notion", accounts.CONNECTED),
            ("slack", "Slack", accounts.NEEDS_SIGN_IN),
        ])
        self.assertNotIn("SHOULD-NOT-LEAK", json.dumps(items))
        self.assertEqual(items[2]["connector"], "claude.ai Notion")
        self.assertEqual(accounts.parse_list(""), [])

    def test_codex_login(self):
        self.assertEqual(accounts.parse_codex_login("Logged in using ChatGPT\n"), "signed_in")
        self.assertEqual(accounts.parse_codex_login("Logged in using an API key - sk-***"), "api_key")
        self.assertEqual(accounts.parse_codex_login("Not logged in"), "signed_out")
        self.assertEqual(accounts.parse_codex_login("???"), "unknown")

    def test_installed_apps_by_name(self):
        result = {"apps": [
            {"id": GMAIL_APP, "runtimeName": "Gmail", "enabled": True, "callable": True},
            {"id": DRIVE_APP, "runtimeName": "Google Drive", "enabled": True, "callable": False},
            {"id": "connector_947e", "runtimeName": "Google Calendar", "enabled": False, "callable": True},
            {"id": "bad id; rm -rf", "runtimeName": "Gmail", "enabled": True, "callable": True},
            {"id": "connector_x", "runtimeName": "Expedia", "enabled": True, "callable": True},
        ]}
        self.assertEqual(accounts.parse_installed(result), {"gmail": GMAIL_APP})
        self.assertEqual(accounts.parse_installed({}), {})

    def test_new_details_have_no_plumbing_words(self):
        for st in (accounts.NEEDS_CLAUDE, accounts.NEEDS_SIGN_IN):
            for s in accounts.SERVICES:
                text = accounts.detail(st, s, "ChatGPT").lower()
                for word in ("connector", "mcp", "oauth", "token", "server", "codex", "api", "—"):
                    self.assertNotIn(word, text, (st, s.id))


class FakeWithList:
    """`claude` with `mcp list` too."""

    def __init__(self, base, listing):
        self.base, self.listing = base, listing

    async def __call__(self, *args):
        if args[:2] == ("mcp", "list"):
            self.base.calls.append(args)
            return 0, self.listing
        return await self.base(*args)


class Plans(Base):
    def setUp(self):
        super().setUp()
        accounts._run = FakeWithList(self.fake, LIST)

    def test_other_connectors_are_listed(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected"}
        result = asyncio.run(accounts.check())
        more = [x for x in result["services"] if x["group"] == "more"]
        self.assertEqual([x["id"] for x in more], ["hugging_face", "linear", "notion", "slack"])
        self.assertTrue(all(x["via"] == "claude" for x in more))
        self.assertEqual(result["plan"], "claude")
        self.assertIn("Notion", accounts.connected_names())
        self.assertNotIn("SHOULD-NOT-LEAK", json.dumps(result))
        n = sum(1 for c in self.fake.calls if c[:2] == ("mcp", "list"))
        accounts._last["checked_at"] -= accounts.MIN_GAP_SEC + 1
        asyncio.run(accounts.check(force=True))
        self.assertEqual(sum(1 for c in self.fake.calls if c[:2] == ("mcp", "list")), n)

    def test_only_a_chatgpt_plan(self):
        self.fake.auth = '{"loggedIn": false}'
        self.chatgpt = {"state": "signed_in", "apps": {"gmail": GMAIL_APP}}
        result = asyncio.run(accounts.check())
        rows = {x["id"]: x for x in result["services"]}
        self.assertEqual(result["plan"], "chatgpt")
        self.assertEqual(result["connect_url"], accounts.CHATGPT_CONNECT_URL)
        self.assertEqual((rows["gmail"]["state"], rows["gmail"]["via"]), (accounts.CONNECTED, "chatgpt"))
        self.assertEqual((rows["drive"]["state"], rows["drive"]["via"]), (accounts.NOT_CONNECTED, "chatgpt"))
        self.assertEqual(rows["trello"]["state"], accounts.NEEDS_CLAUDE)
        self.assertEqual(result["google"], {"connected": 1, "total": 3})
        self.assertNotIn(GMAIL_APP, json.dumps(result))
        self.assertEqual(accounts.broken_news(result), [])

    def test_both_plans_claude_first(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected"}
        self.chatgpt = {"state": "signed_in", "apps": {"gmail": GMAIL_APP, "drive": DRIVE_APP}}
        result = asyncio.run(accounts.check())
        rows = {x["id"]: x for x in result["services"]}
        self.assertEqual(result["plan"], "both")
        self.assertEqual(result["connect_url"], accounts.CONNECT_URL)
        self.assertEqual(rows["gmail"]["via"], "claude")
        self.assertEqual((rows["drive"]["state"], rows["drive"]["via"]), (accounts.CONNECTED, "chatgpt"))
        self.assertEqual(rows["calendar"]["state"], accounts.NOT_CONNECTED)

    def test_no_plan_keeps_the_claude_message(self):
        self.fake.auth = '{"loggedIn": false}'
        self.chatgpt = {"state": "signed_out", "apps": {}}
        result = asyncio.run(accounts.check())
        self.assertEqual(result["plan"], "none")
        self.assertTrue(all(x["state"] == accounts.CLAUDE_SIGNED_OUT for x in result["services"]))


class Routes(Base):
    def setUp(self):
        super().setUp()
        accounts._run = FakeWithList(self.fake, LIST)

    def test_claude_route_names_the_connector(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected"}
        r = asyncio.run(accounts.need("money", "notion", "find the budget page", "s1"))
        self.assertEqual(r, {"connected": True, "state": accounts.CONNECTED, "via": "claude",
                             "connector": "claude.ai Notion"})
        r = asyncio.run(accounts.need("money", "gmail", "x", "s1"))
        self.assertEqual(r["connector"], "claude.ai Gmail")

    def test_chatgpt_route_names_the_app(self):
        self.fake.auth = '{"loggedIn": false}'
        self.chatgpt = {"state": "signed_in", "apps": {"gmail": GMAIL_APP}}
        r = asyncio.run(accounts.need("money", "gmail", "find receipts", "s1"))
        self.assertEqual((r["via"], r["app"]), ("chatgpt", GMAIL_APP))
        r = asyncio.run(accounts.need("money", "drive", "open the sheet", "s1"))
        self.assertFalse(r["connected"])
        self.assertEqual(self.cards()[0]["service"], "drive")

    def test_an_account_that_is_not_there(self):
        r = asyncio.run(accounts.need("money", "asana", "list my tasks", "s1"))
        self.assertFalse(r["connected"])
        self.assertEqual(r["state"], accounts.NO_SUCH)
        self.assertIn('no account called "asana"', r["message"])
        self.assertIn("notion (Notion)", r["message"])
        self.assertEqual(self.cards(), [])

    def test_another_connector_gets_its_own_card(self):
        r = asyncio.run(accounts.need("school", "slack", "read #class", "s1"))
        self.assertFalse(r["connected"])
        title = store.db().execute("SELECT title FROM feed WHERE id=?", (r["card"],)).fetchone()["title"]
        self.assertEqual(title, "Connect Slack")
        self.assertEqual(self.cards()[0]["name"], "Slack")
        self.assertIn("sign in again", r["message"])


class PluginRoutes(unittest.TestCase):
    def setUp(self):
        self.pkg = load_plugin()
        self.tools, self.mac = self.pkg.tools, self.pkg.mac
        self.real_call, self.real_popen = self.mac._call, self.tools.subprocess.Popen
        self.runs, self.hub_says, self.stdout = [], {"ok": True, "result": {"connected": True}}, ""
        test = self

        def fake_call(op, args, timeout=60, session_id="", area="mac"):
            return test.hub_says
        self.mac._call = fake_call

        class FakePopen:
            def __init__(self, cmd, **kw):
                test.runs.append(cmd)
                self.pid, self.returncode = 0, 0

            def communicate(self, timeout=None):
                return test.stdout, ""
        self.tools.subprocess.Popen = FakePopen

    def tearDown(self):
        self.mac._call, self.tools.subprocess.Popen = self.real_call, self.real_popen

    def test_chatgpt_runs_codex_with_one_app_and_no_send(self):
        self.hub_says = {"ok": True, "result": {"connected": True, "via": "chatgpt", "app": GMAIL_APP}}
        self.stdout = "\n".join(json.dumps(e) for e in [
            {"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "codex_apps", "tool": "gmail.search_emails"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Found 2 receipts from Walmart."}},
            {"type": "turn.completed"}])
        out = json.loads(self.tools.connectors({"service": "gmail", "request": "Find Walmart receipts"}, session_id="s"))
        self.assertEqual(out["result"], "Found 2 receipts from Walmart.")
        cmd = self.runs[0]
        self.assertEqual(cmd[:2], ["codex", "exec"])
        for flag in ("--ignore-user-config", "--ephemeral", f"apps.{GMAIL_APP}.enabled=true",
                     f"apps.{GMAIL_APP}.tools.send_email.enabled=false", "apps._default.enabled=false",
                     "apps._default.destructive_enabled=false", "tools.web_search=false", "shell_tool"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], self.tools.CHATGPT_MODEL)

    def test_chatgpt_failure_asks_the_hub_again(self):
        answers = iter([{"ok": True, "result": {"connected": True, "via": "chatgpt", "app": GMAIL_APP}},
                        {"ok": True, "result": {"connected": False, "message": "Stop; card is up."}}])
        self.mac._call = lambda *a, **k: next(answers)
        self.stdout = json.dumps({"type": "turn.failed", "error": {"message": "Gmail is not connected"}})
        out = json.loads(self.tools.connectors({"service": "gmail", "request": "Find receipts"}))
        self.assertEqual(out["error"], "Stop; card is up.")

    def guard_of(self, cmd):
        """The run's PreToolUse hook: its matcher, and a function that asks the hook's guard about one tool."""
        hook = json.loads(cmd[cmd.index("--settings") + 1])["hooks"]["PreToolUse"][0]
        argv = shlex.split(hook["hooks"][0]["command"].rsplit(" || ", 1)[0])
        server, kinds = argv[3], argv[4:]
        guard = importlib.import_module(self.pkg.__name__ + ".connector_guard")
        return hook["matcher"], lambda name: guard.allowed(f"{server}__{name}", kinds, server)

    def test_other_connector_runs_claude_with_a_guard(self):
        self.hub_says = {"ok": True, "result": {"connected": True, "via": "claude", "connector": "claude.ai Notion"}}
        self.stdout = json.dumps({"result": "The budget page lists rent and food.", "is_error": False})
        out = json.loads(self.tools.connectors({"service": "notion", "request": "Find the budget page"}))
        self.assertEqual(out["result"], "The budget page lists rent and food.")
        cmd = self.runs[0]
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertIn("mcp__claude_ai_Notion,ToolSearch", cmd)
        matcher, allowed = self.guard_of(cmd)
        self.assertTrue(re.search(matcher, "mcp__claude_ai_Notion__notion-update-page"))
        for name, open_ in (("notion-create-comment", False), ("notion-delete-page", False), ("deletePage", False),
                            ("share_page", False), ("notion-search", True), ("notion-get-comments", True),
                            ("notion-update-page", False)):
            self.assertEqual(allowed(name), open_, name)

    def test_an_approved_send_is_not_blocked(self):
        self.hub_says = {"ok": True, "result": {"connected": True, "via": "claude", "connector": "claude.ai Slack"}}
        self.stdout = json.dumps({"result": "Posted.", "is_error": False})
        self.tools.connectors({"service": "slack", "request": "Post \"running late\" in #general"})
        _, allowed = self.guard_of(self.runs[0])
        self.assertTrue(allowed("slack_send_message"))
        self.assertFalse(allowed("slack_delete_message"))

    def test_bad_service_names(self):
        for bad in ("", "Notion Pages!", "../x", "a"):
            out = json.loads(self.tools.connectors({"service": bad, "request": "x"}))
            self.assertIn("Need service", out["error"])
        self.assertEqual(self.runs, [])

    def test_no_such_account_stops(self):
        self.hub_says = {"ok": True, "result": {"connected": False, "state": "no_such_account",
                                                "message": 'There is no account called "asana".'}}
        out = json.loads(self.tools.connectors({"service": "asana", "request": "List tasks"}))
        self.assertIn("asana", out["error"])
        self.assertEqual(self.runs, [])

    def test_known_services_keep_their_deny_lists(self):
        self.stdout = json.dumps({"result": "ok", "is_error": False})
        self.tools.connectors({"service": "drive", "request": "Find the syllabus"})
        cmd = self.runs[0]
        self.assertIn("mcp__claude_ai_Google_Drive__share_file", cmd[cmd.index("--disallowedTools") + 1])
        self.assertNotIn("--settings", cmd)


class Tiers(unittest.TestCase):
    def test_other_connectors(self):
        tiers = load_plugin().tiers
        cases = {
            ("notion", "Find the meeting notes from last week"): "read",
            ("slack", "Summarize unread messages in #team"): "read",
            ("notion", "Add a row to the reading list database"): "write",
            ("linear", "Archive ENG-40"): "write",
            ("slack", "Post \"running late\" in #general"): "send",
            ("linear", "Comment on ENG-12 that it is fixed"): "send",
            ("slack", "Reply to Theo saying yes"): "send",
            ("notion", "Share the trip plan page with ken@example.com"): "account",
            ("linear", "Invite Theo to the workspace"): "account",
            ("notion", "Publish the blog page"): "publish",
            ("notion", "Delete the old draft page"): "delete",
        }
        for (service, request), kind in cases.items():
            a = tiers.classify_connector(service, request)
            self.assertEqual(a.kind, kind, request)
            self.assertEqual(a.site, tiers.OTHER_SITES[service])
        self.assertIn(tiers.classify_connector("slack", "Post hi in #general").kind, tiers.ASK_KINDS)
        self.assertNotIn(tiers.classify_connector("notion", "Add a row to the list").kind, tiers.ASK_KINDS)


class Templates(unittest.TestCase):
    def test_services_per_template(self):
        from taffy_hub import app
        self.assertEqual(app.template_services({"id": "inbox"}), ["gmail"])
        self.assertEqual(app.template_services({"id": "cleanup"}), [])
        self.assertEqual(app.template_services({"id": "inbox", "services": ["drive"]}), ["drive"])
        self.assertEqual(app.template_services({"id": "new-one"}), [])
        known = {s.id for s in accounts.SERVICES}
        self.assertTrue(all(set(v) <= known for v in app.TEMPLATE_SERVICES.values()))

    def test_the_three_voice_screens_say_the_same_thing(self):
        """Voice setup QA L2: one wording for the ChatGPT plan line."""
        if not (REPO / "ios").exists():
            self.skipTest("needs the repo")
        line = "For now, calls use ChatGPT's voice. They need Codex on your computer, signed in with a ChatGPT plan."
        self.assertIn(line, (REPO / "ios" / "Taffy" / "Views/Onboarding/OnboardingSetup.swift").read_text())
        for f in ("Views/Onboarding/OnboardingSetup.swift", "Views/RootView.swift", "Views/VoiceSetupView.swift"):
            text = (REPO / "ios" / "Taffy" / f).read_text()
            self.assertIn("VoiceWording.chatGPTPlan", text, f)
            self.assertNotIn("which needs a ChatGPT plan", text, f)



def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()
