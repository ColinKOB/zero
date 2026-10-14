"""Internals QA round 9 (docs/qa/2026-10-08-internals-qa-round-9.md): requests to connectors other than Google and
Trello are tiered by what they do (money is never, merges, rollbacks, promotions and deploys ask, ordinary changes
tell, a scheduled Slack message is a send), and each run of those connectors lets a tool through only when its name
says it does what the request may do.

The judge is never asked and no connector, claude or network is reached: the connectors tool's subprocess is faked,
and the guard's hook script runs with this Python on a JSON line, as Claude Code would run it. Nothing here reads the
home folder, needs Hermes, or depends on the folder the tests run from.

Run: python3 -m unittest discover -s hub/tests
"""

import importlib
import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, plugin, tt  # noqa: E402

tiers = tt.tiers
guard = importlib.import_module(tt.__name__ + ".connector_guard")
STRIPE, NETLIFY = "mcp__claude_ai_Stripe", "mcp__claude_ai_Netlify"
SLACK, NOTION, GITHUB = "mcp__claude_ai_Slack", "mcp__claude_ai_Notion", "mcp__claude_ai_GitHub"


def kind(service: str, request: str) -> str:
    return tiers.classify_connector(service, request).kind


class MoneyIsNever(unittest.TestCase):
    """Zips never pay, refund, charge, pay out, transfer or change billing, whatever the connector."""

    CASES = [
        ("stripe", "Refund Theo's last payment"),
        ("stripe", "Issue a $40 refund to ken@example.com"),
        ("stripe", "Create and finalize an invoice for Theo for $300"),
        ("stripe", "Charge Theo's card on file $50"),
        ("paypal", "Send $20 to Riley"),
        ("stripe", "Pay out the balance to the bank"),
        ("stripe", "Cancel Theo's subscription"),
        ("stripe", "Upgrade Theo to the annual plan"),
        ("stripe", "Create a payment link for the $25 class"),
        ("stripe", "Transfer $500 to the bank account"),
        ("stripe", "Mark invoice 12 as paid"),
        ("stripe", "Request a payment from Theo for the class"),
        ("paypal", "Wire $300 to the landlord"),
        ("slack", "Send Riley $20 for lunch"),
        ("notion", "Upgrade the workspace to the Business plan"),
        ("netlify", "Buy the heron.com domain"),
        ("stripe", "Can you refund Theo?"),
    ]

    def test_money_requests_are_never(self):
        for service, request in self.CASES:
            a = tiers.classify_connector(service, request)
            self.assertEqual(a.kind, "pay", request)
            self.assertEqual(tiers.floor(a.kind), "never", request)
            self.assertIn("never", a.why_never)

    def test_reading_about_money_and_ordinary_changes_stay_open(self):
        for service, request, want in (
                ("stripe", "List Theo's payments from last month", "read"),
                ("stripe", "Show refunds issued this week", "read"),
                ("stripe", "How much did we refund last month?", "read"),
                ("stripe", "What is my Stripe balance?", "read"),
                ("stripe", "Add a note to Theo's customer record", "write"),
                ("notion", "Create a report of last month's payments", "write"),
                ("notion", "Add Theo's payment to the tracker", "write"),
                ("notion", "Pay attention to the deadline field and list overdue tasks", "read"),
                ("notion", "Wire up the form to the database", "write"),
                ("netlify", "Upgrade the site's Node version to 22", "write"),
                ("slack", "Send a message saying lunch is $10 each", "send"),
                ("notion", "Transfer ownership of the trip page to Theo", "account")):
            self.assertEqual(kind(service, request), want, request)


class PublishingAsks(unittest.TestCase):
    def test_merges_rollbacks_promotions_and_deploys_ask(self):
        for service, request in (
                ("github", "Open a pull request from feat to main"),
                ("github", "Merge pull request 42 into main"),
                ("netlify", "Deploy the Heron site to production"),
                ("netlify", "Roll back the Heron site to yesterday's deploy"),
                ("vercel", "Promote the preview to production"),
                ("github", "Push the changes to main"),
                ("github", "Merge #42"),
                ("netlify", "Trigger a new build of the Heron site"),
                ("github", "Revert the last commit on main")):
            a = tiers.classify_connector(service, request)
            self.assertEqual(a.kind, "publish", request)
            self.assertEqual(tiers.floor(a.kind), "ask", request)

    def test_a_request_that_asks_two_things_names_both_for_the_run(self):
        a = tiers.classify_connector("github", "Merge PR 42 and comment that it shipped")
        self.assertEqual(tiers.floor(a.kind), "ask")
        self.assertEqual(set(a.extra["kinds"]), {"send", "publish"})


class OrdinaryChangesTell(unittest.TestCase):
    def test_changes_tell(self):
        for service, request in (
                ("linear", "Close the login bug issue"),
                ("atlassian", "Transition PROJ-12 to Done"),
                ("linear", "Reopen ENG-12"),
                ("linear", "Assign ENG-4 to Theo"),
                ("linear", "Push the due date to Friday"),
                ("linear", "Transfer ENG-5 to the Mobile team"),
                ("linear", "Schedule the update for next sprint"),
                ("notion", "Add a row to the reading list database")):
            a = tiers.classify_connector(service, request)
            self.assertEqual((a.kind, tiers.floor(a.kind)), ("write", "tell"), request)

    def test_a_scheduled_slack_message_is_a_send(self):
        a = tiers.classify_connector("slack", "Schedule a message to #general for 9am saying standup moved")
        self.assertEqual((a.kind, tiers.floor(a.kind)), ("send", "ask"))

    def test_reads_stay_reads(self):
        for service, request in (("slack", "Summarize unread messages in #team"),
                                 ("notion", "Find the meeting notes from last week"),
                                 ("linear", "What is the status of ENG-12?"),
                                 ("netlify", "Did last night's deploy of the Heron site succeed?"),
                                 ("hugging_face", "Who am I signed in as?")):
            self.assertEqual(kind(service, request), "read", request)

    def test_google_and_trello_are_unchanged(self):
        self.assertEqual(kind("gmail", "Find my receipts from Walmart"), "read")
        self.assertEqual(kind("trello", "Move the Repo card to Doing"), "write")
        self.assertEqual(kind("calendar", "Add Soccer practice Saturday at 9am"), "write")


def ok(server: str, tool: str, kinds) -> bool:
    return guard.allowed(f"{server}__{tool}", kinds, server)


class GuardOnARead(unittest.TestCase):
    """On a read request only reading tools run, and names that say nothing (W01 to W28)."""

    def test_asking_never_and_change_tools_are_closed(self):
        for server, tool in ((SLACK, "slack_send_message"),
                             (SLACK, "slack_schedule_message"),
                             (NETLIFY, "netlify-deploy-services-updater"),
                             (STRIPE, "create_refund"),
                             (STRIPE, "cancel_subscription"),
                             (STRIPE, "finalize_invoice"),
                             (STRIPE, "create_payment_link"),
                             (GITHUB, "merge_pull_request"),
                             (GITHUB, "create_or_update_file"),
                             (GITHUB, "push_files"),
                             (GITHUB, "create_pull_request"),
                             (NOTION, "notion-create-comment"),
                             (NOTION, "notion-update-page"),
                             (NOTION, "deletePage"),
                             (NOTION, "share_page"),
                             (STRIPE, "create_customer"),
                             (STRIPE, "update_dispute")):
            self.assertFalse(ok(server, tool, "read"), tool)

    def test_reading_tools_stay_open(self):
        for server, tool in ((SLACK, "slack_read_channel"), (SLACK, "slack_search_public"),
                             (NOTION, "notion-search"), (NOTION, "notion-fetch"), (NOTION, "notion-get-comments"),
                             (STRIPE, "list_customers"), (STRIPE, "list_refunds"), (STRIPE, "list_payment_intents"),
                             (STRIPE, "retrieve_balance"), (STRIPE, "search_stripe_documentation"),
                             (NETLIFY, "netlify-deploy-services-reader"), (NETLIFY, "netlify-user-services-reader"),
                             (GITHUB, "get_pull_request"), ("mcp__claude_ai_Linear", "get_issue"),
                             ("mcp__claude_ai_Hugging_Face", "hf_whoami"),
                             ("mcp__claude_ai_Hugging_Face", "hub_repo_search")):
            self.assertTrue(ok(server, tool, "read"), tool)

    def test_names_that_say_nothing_stay_open(self):
        for server, tool in (("mcp__claude_ai_Hugging_Face", "hf_fs"), ("mcp__claude_ai_Hugging_Face", "dynamic_space"),
                             (NETLIFY, "netlify-coding-rules")):
            self.assertEqual(guard.tool_kinds(f"{server}__{tool}", server), {"unknown"})
            self.assertTrue(ok(server, tool, "read"), tool)

    def test_another_connectors_tools_are_closed(self):
        self.assertFalse(guard.allowed(f"{SLACK}__slack_read_channel", "read", NOTION))


class GuardOnOtherKinds(unittest.TestCase):
    def test_a_change_opens_changes_only(self):
        for server, tool in ((NOTION, "notion-update-page"), (NOTION, "notion-create-pages"),
                             ("mcp__claude_ai_Linear", "update_issue"), (STRIPE, "create_customer"),
                             (NETLIFY, "netlify-project-services-updater"), (SLACK, "slack_send_message_draft"),
                             (SLACK, "slack_create_canvas")):
            self.assertTrue(ok(server, tool, "write"), tool)
        for server, tool in ((SLACK, "slack_send_message"), (SLACK, "slack_schedule_message"),
                             (NOTION, "notion-create-comment"), (NOTION, "deletePage"), (NOTION, "share_page"),
                             (GITHUB, "merge_pull_request"), (GITHUB, "create_or_update_file"),
                             (NETLIFY, "netlify-deploy-services-updater"), (STRIPE, "create_refund"),
                             (STRIPE, "create_invoice")):
            self.assertFalse(ok(server, tool, "write"), tool)

    def test_an_approved_kind_opens_that_kind(self):
        self.assertTrue(ok(SLACK, "slack_send_message", "send"))
        self.assertTrue(ok(SLACK, "slack_schedule_message", "send"))
        self.assertFalse(ok(SLACK, "slack_delete_message", "send"))
        self.assertTrue(ok(GITHUB, "merge_pull_request", "publish"))
        self.assertFalse(ok(GITHUB, "merge_pull_request", "send"))
        self.assertTrue(ok(NETLIFY, "netlify-deploy-services-updater", "publish"))
        self.assertTrue(ok(NOTION, "deletePage", "delete"))
        self.assertTrue(ok(NOTION, "share_page", "account"))
        self.assertFalse(ok(NOTION, "share_page", "delete"))
        self.assertTrue(ok(GITHUB, "merge_pull_request", ["send", "publish"]))
        self.assertTrue(ok(GITHUB, "add_issue_comment", ["send", "publish"]))

    def test_money_tools_never_open(self):
        for kinds in ("read", "write", "send", "publish", "delete", "account", "pay", ["send", "publish", "delete"]):
            for tool in ("create_refund", "cancel_subscription", "finalize_invoice", "create_payment_link",
                         "update_subscription", "create_payout", "createTransfer"):
                self.assertFalse(ok(STRIPE, tool, kinds), (tool, kinds))


class TheHook(unittest.TestCase):
    """The hook as Claude Code runs it: the command from the run's settings, through the shell, with the tool's JSON
    on stdin. Exit 2 blocks; anything that is not 0 or 2 would let the tool run, so failures must come out as 2."""

    def settings(self, server: str, kinds: list[str]) -> dict:
        return json.loads(tt.tools._guard(server, kinds))["hooks"]["PreToolUse"][0]

    def run_hook(self, command: str, payload) -> subprocess.CompletedProcess:
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(["/bin/sh", "-c", command], input=data, capture_output=True, text=True, timeout=30,
                              cwd="/", env={"PATH": "/usr/bin:/bin"})

    def test_matcher_covers_every_tool_of_the_connector(self):
        import re
        matcher = self.settings(STRIPE, ["read"])["matcher"]
        self.assertTrue(re.search(matcher, f"{STRIPE}__list_customers"))
        self.assertTrue(re.search(matcher, f"{STRIPE}__create_refund"))
        self.assertFalse(re.search(matcher, "ToolSearch"))

    def test_the_command_blocks_and_allows(self):
        command = self.settings(STRIPE, ["read"])["hooks"][0]["command"]
        self.assertTrue(command.endswith(" || exit 2"))
        allow = self.run_hook(command, {"hook_event_name": "PreToolUse", "tool_name": f"{STRIPE}__list_customers",
                                        "tool_input": {}})
        self.assertEqual(allow.returncode, 0, allow.stderr)
        refund = self.run_hook(command, {"tool_name": f"{STRIPE}__create_refund", "tool_input": {"charge": "ch_1"}})
        self.assertEqual(refund.returncode, 2)
        self.assertIn("never", refund.stderr)
        change = self.run_hook(command, {"tool_name": f"{STRIPE}__create_customer"})
        self.assertEqual(change.returncode, 2)
        self.assertIn("Zero blocked this tool", change.stderr)

    def test_an_approved_send_runs_through_the_hook(self):
        command = self.settings(SLACK, ["send"])["hooks"][0]["command"]
        self.assertEqual(self.run_hook(command, {"tool_name": f"{SLACK}__slack_schedule_message"}).returncode, 0)
        self.assertEqual(self.run_hook(command, {"tool_name": f"{SLACK}__slack_delete_message"}).returncode, 2)

    def test_failures_block(self):
        command = self.settings(STRIPE, ["read"])["hooks"][0]["command"]
        self.assertEqual(self.run_hook(command, "not json").returncode, 2)
        self.assertEqual(self.run_hook(command, "[]").returncode, 2)
        argv = shlex.split(command.rsplit(" || ", 1)[0])
        argv[0] = "/nonexistent/python3"
        broken = " ".join(shlex.quote(a) for a in argv) + " || exit 2"
        self.assertEqual(self.run_hook(broken, {"tool_name": f"{STRIPE}__list_customers"}).returncode, 2)

    def test_the_script_sits_beside_the_tools(self):
        self.assertEqual(os.path.dirname(tt.tools.GUARD_SCRIPT), os.path.dirname(os.path.abspath(tt.tools.__file__)))
        self.assertTrue(os.path.isfile(tt.tools.GUARD_SCRIPT))


class ConnectorRuns(unittest.TestCase):
    def setUp(self):
        self.tools, self.mac = tt.tools, importlib.import_module(tt.__name__ + ".mac")
        self.real_call, self.real_popen = self.mac._call, self.tools.subprocess.Popen
        self.runs, self.connector = [], "claude.ai Stripe"
        test = self
        self.mac._call = lambda *a, **k: {"ok": True, "result": {"connected": True, "via": "claude",
                                                                  "connector": test.connector}}

        class FakePopen:
            def __init__(self, cmd, **kw):
                test.runs.append(cmd)
                self.pid, self.returncode = 0, 0

            def communicate(self, timeout=None):
                return json.dumps({"result": "Done.", "is_error": False}), ""
        self.tools.subprocess.Popen = FakePopen

    def tearDown(self):
        self.mac._call, self.tools.subprocess.Popen = self.real_call, self.real_popen

    def guard_of(self, service: str, request: str):
        self.runs.clear()
        self.tools.connectors({"service": service, "request": request}, session_id="s")
        cmd = self.runs[0]
        hook = json.loads(cmd[cmd.index("--settings") + 1])["hooks"]["PreToolUse"][0]
        argv = shlex.split(hook["hooks"][0]["command"].rsplit(" || ", 1)[0])
        return cmd, (lambda tool: guard.allowed(f"{argv[3]}__{tool}", argv[4:], argv[3]))

    def test_a_stripe_read_cannot_refund(self):
        cmd, allowed = self.guard_of("stripe", "Find Theo's last payment")
        self.assertIn("Never pay, refund, charge or transfer money", cmd[2])
        self.assertTrue(allowed("list_payment_intents"))
        for tool in ("create_refund", "cancel_subscription", "finalize_invoice", "create_payment_link"):
            self.assertFalse(allowed(tool), tool)

    def test_a_netlify_read_cannot_deploy(self):
        self.connector = "claude.ai Netlify"
        _, allowed = self.guard_of("netlify", "Did the Heron site's last deploy work?")
        self.assertTrue(allowed("netlify-deploy-services-reader"))
        self.assertFalse(allowed("netlify-deploy-services-updater"))

    def test_an_approved_deploy_can_deploy_but_not_refund(self):
        self.connector = "claude.ai Netlify"
        _, allowed = self.guard_of("netlify", "Deploy the Heron site to production")
        self.assertTrue(allowed("netlify-deploy-services-updater"))
        self.assertFalse(allowed("netlify-project-services-delete"))

    def test_slack_and_notion_changes_run_their_change_tools(self):
        self.connector = "claude.ai Notion"
        _, allowed = self.guard_of("notion", "Add a row to the reading list database")
        self.assertTrue(allowed("notion-create-pages"))
        self.assertFalse(allowed("notion-create-comment"))
        self.connector = "claude.ai Slack"
        _, allowed = self.guard_of("slack", "Schedule a message to #general for 9am saying standup moved")
        self.assertTrue(allowed("slack_schedule_message"))
        self.assertFalse(allowed("slack_send_message_to_everyone_and_delete"))


class ThroughTheGate(GateCase):
    def test_a_refund_is_refused_with_no_card(self):
        self.ask("Theo asked for his money back, can you handle it?")
        out = self.call("connectors", {"service": "stripe", "request": "Refund Theo's last payment"})
        self.assertTrue(out["message"].startswith("Not done"))
        self.assertIn("never", out["message"])
        self.assertEqual(self.cards(), [])

    def test_a_rollback_and_a_scheduled_message_ask(self):
        self.ask("Roll the site back and tell the team")
        self.assertIsNotNone(self.call("connectors", {"service": "netlify",
                                                      "request": "Roll back the Heron site to yesterday's deploy"}))
        self.assertIsNotNone(self.call("connectors", {"service": "slack", "request":
                                                      "Schedule a message to #general for 9am saying standup moved"}))
        self.assertEqual([c["kind"] for c in self.cards()], ["publish", "send"])

    def test_reads_and_changes_run_with_no_card(self):
        self.ask("Close the login bug and check Stripe for Theo's payment")
        self.assertIsNone(self.call("connectors", {"service": "stripe", "request": "Find Theo's last payment"}))
        self.assertIsNone(self.call("connectors", {"service": "linear", "request": "Close the login bug issue"}))
        self.assertEqual(self.cards(), [])
        act = plugin.recent_actions(1)[0]
        self.assertEqual((act["kind"], act["how"]), ("write", "tell"))

    def test_an_approved_send_covers_that_request_once(self):
        self.ask("Remind #general about standup")
        args = {"service": "slack", "request": "Schedule a message to #general for 9am saying standup moved"}
        self.assertIsNotNone(self.call("connectors", args))
        plugin.decide(self.cards()[-1]["id"], True)
        self.assertIsNone(self.call("connectors", args))
        self.assertIsNotNone(self.call("connectors", args))


if __name__ == "__main__":
    unittest.main()
