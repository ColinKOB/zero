"""Zip sandbox stage 3, test mode (docs/qa/2026-10-08-sandbox-stage3-code.md): a Zip run from the Hermes command line
with TAFFY_TEST_CARDS=1 raises its cards as tests. The hub records them in consent.db but never lists, pushes or shows
them, a test answer never covers a real call and a real one never covers a test, tools that reach Colin are off, and
the email tool only reports what it would send. alien/bin/zip-test-cards lists and answers the test cards.

Nothing here reaches the network, Hermes, himalaya or the home folder.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, gate, plugin, tt  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402
from taffy_hub import store, watcher  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEPLOY = "netlify deploy --prod --dir dist"
TEST = {"TAFFY_TEST_CARDS": "1"}
REAL = {"TAFFY_TEST_CARDS": ""}


def test_env():
    return mock.patch.dict(os.environ, TEST)


def real_env():
    return mock.patch.dict(os.environ, REAL)


class TheGate(GateCase):
    bot = "builder"

    def test_a_test_card_is_recorded_and_says_so(self):
        with test_env():
            self.ask("deploy the site")
            b = self.call("terminal", {"command": DEPLOY})
        self.assertTrue(b["message"].startswith("[Test mode: nothing here reached Colin's app.]"))
        self.assertIn("was recorded as a test card (see zip-test-cards), not shown in his app", b["message"])
        rows = self.cards()
        self.assertEqual([(r["kind"], r["test"]) for r in rows], [("publish", 1)])
        self.assertEqual(plugin.pending(), [])
        self.assertEqual(plugin.requests_after(0), [])
        self.assertEqual([r["id"] for r in plugin.test_cards()], [rows[0]["id"]])

    def test_without_the_flag_nothing_changes(self):
        with real_env():
            self.ask("deploy the site")
            b = self.call("terminal", {"command": DEPLOY})
        self.assertNotIn("Test mode", b["message"])
        self.assertEqual([r["test"] for r in plugin.pending()], [0])
        self.assertEqual(plugin.test_cards(), [])

    def test_answers_never_cross(self):
        with test_env():
            self.call("terminal", {"command": DEPLOY})
            test_id = plugin.test_cards()[0]["id"]
        with real_env():
            self.call("terminal", {"command": DEPLOY})
            real_id = plugin.pending()[0]["id"]
            self.assertIsNone(plugin.decide_test(real_id, True))
        self.assertNotEqual(test_id, real_id)
        self.assertIsNotNone(plugin.decide_test(test_id, True))
        with real_env():
            self.assertIsNotNone(self.call("terminal", {"command": DEPLOY}))
        with test_env():
            self.assertIsNone(self.call("terminal", {"command": DEPLOY}))
            self.assertIsNotNone(self.call("terminal", {"command": DEPLOY}))
        plugin.decide(real_id, True)
        with test_env():
            self.assertIsNotNone(self.call("terminal", {"command": DEPLOY}))
        with real_env():
            self.assertIsNone(self.call("terminal", {"command": DEPLOY}))

    def test_not_now_on_a_test_card_stays_in_test_mode(self):
        with test_env():
            self.call("terminal", {"command": DEPLOY})
            plugin.decide_test(plugin.test_cards()[0]["id"], False)
            self.assertIn("said not now", self.call("terminal", {"command": DEPLOY})["message"])
        with real_env():
            self.assertNotIn("said not now", self.call("terminal", {"command": DEPLOY})["message"])

    def test_always_allow_on_a_test_card_makes_no_rule(self):
        with test_env():
            self.ask("email Theo")
            self.call("terminal", {"command": "printf 'To: ken@brightpath.test\\nSubject: Hi\\n\\nHi' | himalaya template send"})
            card = plugin.test_cards()[0]
        self.assertEqual(card["scope"], "ken@brightpath.test")
        plugin.decide(card["id"], True, always=True)
        self.assertEqual(plugin.rules(), [])

    def test_sign_in_cards_and_logs_are_tests_too(self):
        with test_env():
            plugin.request("money", "chase.com", "", "cli-1", None, why="background")
            plugin.log_action("money", "write", "", "Moved a card", "tell")
            plugin.log_signin("money", "chase.com", "test", "asked")
        self.assertEqual(plugin.pending(), [])
        self.assertEqual(plugin.actions_after(0), [])
        self.assertEqual(plugin.recent_actions(), [])
        self.assertEqual(plugin.signins_after(0), [])
        self.assertEqual(plugin.recent_signins(), [])
        self.assertEqual(len(plugin.test_cards()), 1)

    def test_tools_that_reach_colin_are_off(self):
        with test_env():
            for tool in ("connectors", "mac_start", "mac_run", "offer_choices", "ask_to_sign_in", "close_sign_in_card",
                         "share_screen",
                         "react", "ask_teammate", "tell_teammate", "team_note", "send_message", "cronjob",
                         "kanban_create", "kanban_block", "kanban_comment", "team_add_bot"):
                with self.subTest(tool=tool):
                    b = self.call(tool, {"service": "trello", "request": "list cards"})
                    self.assertIn("off in test mode", b["message"])
            for tool, args in (("kanban_list", {}), ("kanban_show", {"task_id": "t_1"}), ("web_search", {"query": "x"}),
                               ("read_file", {"path": "/tmp/x"}), ("terminal", {"command": "ls"})):
                with self.subTest(tool=tool):
                    self.assertIsNone(self.call(tool, args))
        with real_env():
            self.assertIsNone(self.call("offer_choices", {"question": "x", "choices": ["a", "b"]}))

    def test_the_email_tool_only_reports_a_send(self):
        def never(*a, **kw):
            raise AssertionError("himalaya ran in test mode")
        args = {"action": "send", "account": "personal", "to": ["ken@brightpath.test"], "subject": "Preview",
                "body": "Hi Theo, the preview is ready."}
        with test_env(), mock.patch.object(tt.email_tool, "run", never):
            self.ask("send Theo the preview")
            self.assertIn("test card", self.call("email", args)["message"])
            plugin.decide_test(plugin.test_cards()[0]["id"], True)
            self.assertIsNone(self.call("email", args))
            out = json.loads(tt.email_tool.email_tool(args))
            self.assertEqual((out["test_mode"], out["would_be"]), (True, "sent"))
            self.assertIn("Hi Theo, the preview is ready.", out["message"])
            out = json.loads(tt.email_tool.email_tool({**args, "action": "draft"}))
            self.assertEqual(out["would_be"], "saved in Drafts")


class TheHub(AppCase):
    """A test card never reaches the app: not in its card list, its counts, its feed, its pushes or its activity."""

    def setUp(self):
        super().setUp()
        self.pushed = []

        async def fake_push(title, body, bot_id, urgent=False, **kw):
            self.pushed.append((title, body, bot_id))
        p = mock.patch.object(watcher, "push", fake_push)
        p.start()
        self.addCleanup(p.stop)
        store.set_cursor("consent_request", 0)
        store.set_cursor("consent_action", 0)
        store.set_cursor("consent_signin", 0)

    def raise_cards(self):
        with test_env():
            plugin.action_request("builder", "publish", "", "Publish the site to production on Netlify", "", DEPLOY,
                                  "fp-test", "cli-1", None, why="background")
            plugin.request("money", "chase.com", "", "cli-1", None, why="background")
            plugin.log_action("builder", "write", "github.com", "Pushed to the branch test", "tell")
            plugin.log_signin("money", "chase.com", "test run", "asked")

    def feed(self):
        return [dict(r) for r in store.db().execute("SELECT * FROM feed")]

    def test_never_shown_or_pushed(self):
        self.raise_cards()
        self.assertEqual(self.client.get("/api/signin-requests").json()["requests"], [])
        perms = self.client.get("/api/permissions").json()
        self.assertEqual((perms["actions"], perms["signins"]), ([], []))
        self.assertEqual(plugin.pending(), [])
        asyncio.run(watcher.scan_consent())
        self.assertEqual(self.pushed, [])
        self.assertEqual(self.feed(), [])
        self.assertEqual(len(plugin.test_cards()), 2)

    def test_a_real_card_beside_it_still_arrives(self):
        self.raise_cards()
        with real_env():
            plugin.action_request("builder", "publish", "", "Publish the site to production on Netlify", "", DEPLOY,
                                  "fp-real", "chat-1", None, why="asked")
        got = self.client.get("/api/signin-requests").json()["requests"]
        self.assertEqual([r["reason"] for r in got], ["Publish the site to production on Netlify"])
        asyncio.run(watcher.scan_consent())
        self.assertEqual(len(self.pushed), 1)
        self.assertEqual([f["kind"] for f in self.feed()], ["signin_request"])

    def test_the_app_cannot_answer_one(self):
        """Even by its id, a test card answered through the app's endpoint makes no rule."""
        with test_env():
            req = plugin.action_request("builder", "send", "", "Email ken@brightpath.test", "ken@brightpath.test",
                                        "Hi", "fp-t2", "cli-1", None, why="background", scope="ken@brightpath.test")
        self.client.post(f"/api/signin-requests/{req}", json={"decision": "always"})
        self.assertEqual(self.client.get("/api/permissions").json()["rules"], [])


class Script(unittest.TestCase):
    """alien/bin/zip-test-cards against a throwaway consent.db."""

    def setUp(self):
        self.script = REPO / "alien" / "bin" / "zip-test-cards"
        if not self.script.exists():
            self.skipTest("repo-only check (alien runs the hub without the repo)")
        self.db = Path(tempfile.mkdtemp()) / "consent.db"
        self.saved = plugin.DB_PATH
        plugin.DB_PATH = self.db
        self.addCleanup(setattr, plugin, "DB_PATH", self.saved)
        with test_env():
            self.test_id = plugin.action_request("school", "delete", "", "Delete ~/Documents/x", "", "rm -rf ~/Documents/x",
                                                 "fp-s", "cli-1", None, why="background")
        with real_env():
            self.real_id = plugin.action_request("school", "delete", "", "Delete ~/Documents/real", "",
                                                 "rm -rf ~/Documents/real", "fp-r", "chat-1", None, why="asked")

    def run_script(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "TAFFY_TEST_CARDS"}
        return subprocess.run([sys.executable, "-I", str(self.script), *args, "--db", str(self.db)],
                              capture_output=True, text=True, timeout=60, env=env, cwd=tempfile.gettempdir())

    def test_lists_shows_and_answers_test_cards_only(self):
        out = self.run_script("list")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Delete ~/Documents/x", out.stdout)
        self.assertNotIn("real", out.stdout)
        shown = json.loads(self.run_script("show", str(self.test_id)).stdout)
        self.assertEqual((shown["kind"], shown["inputs"]), ("delete", "rm -rf ~/Documents/x"))
        self.assertEqual(self.run_script("allow", str(self.real_id)).returncode, 1)
        self.assertEqual(plugin.get_request(self.real_id)["status"], "pending")
        self.assertIn("Allowed once", self.run_script("allow", str(self.test_id)).stdout)
        self.assertEqual(plugin.get_request(self.test_id)["status"], "allowed")
        self.assertIn("No test cards.", self.run_script().stdout)
        self.assertIn("allowed", self.run_script("all").stdout)

    def test_clear(self):
        self.assertIn("Closed 1 test cards.", self.run_script("clear").stdout)
        self.assertEqual(plugin.get_request(self.real_id)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
