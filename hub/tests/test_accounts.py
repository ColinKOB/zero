"""Connected accounts (accounts.py): reading Claude Code's answers without keeping anything secret, the cached
check, the Connect card a Zip raises when an account is missing, waking the Zip once it is connected, the
alert when a used account breaks, and the connectors tool asking the hub first.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien:
~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import accounts, config, profile, store, turns  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools"
if not PLUGIN_DIR.exists():   # on alien the hub is deployed without the repo; use the installed plugin
    PLUGIN_DIR = Path.home() / "taffy" / "plugins" / "taffy-tools"

SIGNED_IN = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                        "email": "pat@example.com", "orgId": "org-secretish", "subscriptionType": "pro"})


def get_output(name: str, status: str) -> str:
    return (f"{name}:\n  Scope: claude.ai config\n  Status: {status}\n"
            f"  URL: https://gmail.mcp.claude.com/mcp/abc?token=SHOULD-NOT-LEAK\n")


class FakeClaude:
    """Stands in for the claude binary: `auth status` and `mcp get <name>` answers, and a log of calls."""

    def __init__(self, auth=SIGNED_IN, statuses=None):
        self.auth = auth
        self.statuses = statuses or {}
        self.calls = []

    async def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("auth", "status"):
            return 0, self.auth
        if args[:2] == ("mcp", "get"):
            name = args[2]
            st = self.statuses.get(name)
            if st is None:
                return 1, f'No MCP server named "{name}". Configured servers: claude.ai Trello'
            return 0, get_output(name, st)
        raise AssertionError(args)


def fresh_store(tmp: Path) -> None:
    config.HUB_DIR = tmp
    config.HUB_DB = tmp / "hub.db"
    store._local.conn = None
    store._schema_ready = False


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB, accounts._run, turns.run_internal, accounts._chatgpt)
        fresh_store(Path(tempfile.mkdtemp()))
        accounts._last = None
        accounts._others.update(at=0.0, items=[])
        self.fake = FakeClaude()
        accounts._run = self.fake
        self.chatgpt = {"state": "missing", "apps": {}}   # no Codex here unless a test says so

        async def fake_chatgpt():
            return self.chatgpt
        accounts._chatgpt = fake_chatgpt
        self.woken = []

        self.continues = []

        async def fake_turn(bot_id, text, continues=None):
            self.woken.append((bot_id, text))
            self.continues.append(continues)
            return "Picking that back up now."
        turns.run_internal = fake_turn

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB, accounts._run, turns.run_internal, accounts._chatgpt = self.saved
        accounts._others.update(at=0.0, items=[])
        store._local.conn = None
        store._schema_ready = False
        accounts._last = None

    def cards(self):
        return [json.loads(r["body"]) for r in store.db().execute("SELECT body FROM feed WHERE kind='account' ORDER BY id")]


class Reading(unittest.TestCase):
    def test_auth_keeps_only_the_kind_and_email(self):
        self.assertEqual(accounts.parse_auth(SIGNED_IN), {"state": "signed_in", "account": "pat@example.com"})
        self.assertEqual(accounts.parse_auth('{"loggedIn": false}')["state"], "signed_out")
        self.assertEqual(accounts.parse_auth(json.dumps({"loggedIn": True, "authMethod": "apiKey"}))["state"], "api_key")
        self.assertEqual(accounts.parse_auth(json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                                         "apiProvider": "bedrock"}))["state"], "api_key")
        self.assertEqual(accounts.parse_auth("warning: something\n" + SIGNED_IN)["state"], "signed_in")
        self.assertEqual(accounts.parse_auth("not json")["state"], "unknown")

    def test_each_status_line(self):
        cases = {
            "✔ Connected": accounts.CONNECTED,
            "cached 2h ago · connects on first use · 12 tools": accounts.CONNECTED,
            "! Needs authentication": accounts.NEEDS_SIGN_IN,
            "✘ Failed to connect — HTTP 502": accounts.TROUBLE,
            "✘ Connection error": accounts.TROUBLE,
            "⊘ Disabled for this project (re-enable via /mcp)": accounts.TURNED_OFF,
            "✘ session token rejected": accounts.CLAUDE_SIGN_IN_AGAIN,
            "something new": accounts.UNKNOWN,
        }
        for line, want in cases.items():
            self.assertEqual(accounts.parse_get(get_output("claude.ai Gmail", line)), want, line)
        self.assertEqual(accounts.parse_get('No MCP server named "claude.ai Gmail". Configured servers: x'),
                         accounts.NOT_CONNECTED)
        self.assertEqual(accounts.parse_get(""), accounts.UNKNOWN)

    def test_details_have_no_plumbing_words(self):
        for st in (accounts.CONNECTED, accounts.NOT_CONNECTED, accounts.NEEDS_SIGN_IN, accounts.TURNED_OFF,
                   accounts.TROUBLE, accounts.CLAUDE_SIGNED_OUT, accounts.CLAUDE_API_KEY,
                   accounts.CLAUDE_SIGN_IN_AGAIN, accounts.CLAUDE_MISSING, accounts.UNKNOWN):
            for s in accounts.SERVICES:
                text = accounts.detail(st, s).lower()
                for word in ("connector", "mcp", "oauth", "token", "server", "—"):
                    self.assertNotIn(word, text, (st, s.id))


class Checking(Base):
    def test_signed_in_reports_each_service_and_never_the_address(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected", "claude.ai Google Calendar": "! Needs authentication",
                              "claude.ai Trello": "✔ Connected"}
        result = asyncio.run(accounts.check())
        states = {x["id"]: x["state"] for x in result["services"]}
        self.assertEqual(states, {"gmail": "connected", "calendar": "needs_sign_in", "drive": "not_connected",
                                  "trello": "connected"})
        self.assertEqual(result["google"], {"connected": 1, "total": 3})
        self.assertEqual(result["claude"], {"state": "signed_in", "account": "pat@example.com"})
        self.assertEqual(result["connect_url"], "https://claude.ai/customize/connectors")
        text = json.dumps(result)
        self.assertNotIn("SHOULD-NOT-LEAK", text)
        self.assertNotIn("mcp.claude.com", text)
        self.assertNotIn("org-secretish", text)
        self.assertEqual(accounts.connected_names(), ["Gmail", "Trello"])

    def test_signed_out_skips_the_connector_checks(self):
        self.fake.auth = '{"loggedIn": false}'
        result = asyncio.run(accounts.check())
        self.assertTrue(all(x["state"] == accounts.CLAUDE_SIGNED_OUT for x in result["services"]))
        self.assertEqual([c[:2] for c in self.fake.calls], [("auth", "status")])

    def test_missing_claude(self):
        async def missing(*args):
            raise FileNotFoundError("claude")
        accounts._run = missing
        result = asyncio.run(accounts.check())
        self.assertEqual(result["claude"]["state"], "missing")
        self.assertTrue(all(x["state"] == accounts.CLAUDE_MISSING for x in result["services"]))

    def test_cache_and_polling(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected"}

        async def go():
            await accounts.check()
            n = len(self.fake.calls)
            await accounts.check()                      # cached for ten minutes
            await accounts.check(force=True)            # the Connect screen polling right after: shared
            self.assertEqual(len(self.fake.calls), n)
            accounts._last["checked_at"] -= accounts.MIN_GAP_SEC + 1
            await accounts.check(force=True)            # a real new check
            self.assertEqual(len(self.fake.calls), 2 * n)
            # Two polls at once run one check.
            accounts._last["checked_at"] -= accounts.MIN_GAP_SEC + 1
            await asyncio.gather(accounts.check(force=True), accounts.check(force=True))
            self.assertEqual(len(self.fake.calls), 3 * n)
        asyncio.run(go())


class Cards(Base):
    def test_missing_account_raises_one_google_card_and_stops_the_zip(self):
        async def go():
            first = await accounts.need("money", "gmail", "find the Walmart receipt", "s1")
            second = await accounts.need("money", "calendar", "check Friday", "s1")
            return first, second
        first, second = asyncio.run(go())
        self.assertFalse(first["connected"])
        self.assertEqual(first["state"], accounts.NOT_CONNECTED)
        self.assertIn("Stop working on this now", first["message"])
        self.assertIsNotNone(first["card"])
        self.assertIsNone(second["card"])                # one Connect Google card covers all three
        self.assertIn("is now at the bottom of your chat", second["message"])
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["why"], "find the Walmart receipt")
        self.assertEqual(store.db().execute("SELECT title FROM feed WHERE kind='account'").fetchone()["title"],
                         "Connect Google")

    def test_connected_or_unreadable_goes_ahead(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected", "claude.ai Google Drive": "✘ Failed to connect"}

        async def go():
            return (await accounts.need("money", "gmail", "x", "s1"), await accounts.need("money", "drive", "x", "s1"))
        ok, trouble = asyncio.run(go())
        self.assertTrue(ok["connected"])
        self.assertTrue(trouble["connected"])            # never block the Zip on a hiccup
        self.assertEqual(self.cards(), [])

    def test_connecting_closes_the_card_and_wakes_the_zip(self):
        async def go():
            await accounts.need("school", "drive", "open the syllabus", "s9")
            self.fake.statuses = {"claude.ai Google Drive": "✔ Connected"}
            accounts._last["checked_at"] -= accounts.MIN_GAP_SEC + 1
            await accounts.check(force=True)
            for _ in range(50):
                if self.woken:
                    break
                await asyncio.sleep(0.02)
        asyncio.run(go())
        self.assertEqual(self.cards()[0]["state"], "connected")
        self.assertEqual(len(self.woken), 1)
        bot, note = self.woken[0]
        self.assertEqual(bot, "school")
        self.assertTrue(note.startswith("[Hub note"))
        self.assertIn("Google Drive is connected", note)
        reply = store.db().execute("SELECT body FROM feed WHERE kind='message' AND bot_id='school'").fetchone()
        self.assertEqual(reply["body"], "Picking that back up now.")

    def test_closing_the_card(self):
        fid, _ = accounts.ask("money", "gmail", "x", "s1", accounts.NOT_CONNECTED)
        self.assertEqual(accounts.close("money", fid)["state"], "declined")
        fid2, _ = accounts.ask("money", "gmail", "x", "s1", accounts.NOT_CONNECTED)
        self.assertIsNotNone(fid2)                       # a new ask is allowed after closing
        with self.assertRaises(accounts.AskError):
            accounts.close("school", fid2)               # another Zip's card

    def test_trello_has_its_own_card(self):
        accounts.ask("builder", "gmail", "x", "s1", accounts.NOT_CONNECTED)
        fid, _ = accounts.ask("builder", "trello", "move the card", "s1", accounts.NOT_CONNECTED)
        self.assertIsNotNone(fid)
        self.assertEqual(store.db().execute("SELECT title FROM feed WHERE id=?", (fid,)).fetchone()["title"],
                         "Connect Trello")

    def test_activity_includes_account_cards(self):
        accounts.ask("money", "gmail", "x", "s1", accounts.NOT_CONNECTED)
        self.assertIn("account", [i["kind"] for i in store.activity_for("money")])


class Alerts(Base):
    def test_only_accounts_that_worked_before_are_news_once(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected"}
        r = asyncio.run(accounts.check())
        self.assertEqual(accounts.broken_news(r), [])
        self.fake.statuses = {"claude.ai Gmail": "! Needs authentication",
                              "claude.ai Google Calendar": "! Needs authentication"}
        accounts._last = None
        r = asyncio.run(accounts.check())
        news = accounts.broken_news(r)
        self.assertEqual(len(news), 1)                   # Calendar was never connected, so only Gmail
        self.assertIn("Gmail needs you to sign in again", news[0][1])
        self.assertEqual(accounts.broken_news(r), [])    # told once
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected"}
        accounts._last = None
        r = asyncio.run(accounts.check())
        self.assertEqual(accounts.broken_news(r), [])
        self.assertEqual(store.get_setting("accounts_told"), [])

    def test_claude_signed_out_is_one_message(self):
        self.fake.statuses = {"claude.ai Gmail": "✔ Connected", "claude.ai Trello": "✔ Connected"}
        asyncio.run(accounts.check())
        self.fake.auth = '{"loggedIn": false}'
        accounts._last = None
        news = accounts.broken_news(asyncio.run(accounts.check()))
        self.assertEqual(len(news), 1)
        self.assertIn("Claude is signed in again", news[0][1])

    def test_welcome_lists_what_works(self):
        self.fake.statuses = {"claude.ai Google Calendar": "✔ Connected"}
        asyncio.run(accounts.check())
        with mock.patch.object(profile.logins, "list_all", return_value=[]):   # never the live vaults
            self.assertEqual(profile.summary("")["connectors"], ["Google Calendar"])


def load_plugin():
    """The taffy-tools plugin as a package, without Hermes (nothing in it needs Hermes at import)."""
    name = "taffy_tools_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class ConnectorsTool(unittest.TestCase):
    def setUp(self):
        self.pkg = load_plugin()
        self.tools, self.mac = self.pkg.tools, self.pkg.mac
        self.real_call, self.real_popen = self.mac._call, self.tools.subprocess.Popen
        self.asked = []
        self.runs = []
        self.hub_says = {"ok": True, "result": {"connected": True}}

        def fake_call(op, args, timeout=60, session_id="", area="mac"):
            self.asked.append((area, op, args, session_id))
            return self.hub_says
        self.mac._call = fake_call
        test = self

        class FakePopen:
            def __init__(self, cmd, **kw):
                test.runs.append(cmd)
                self.pid, self.returncode = 0, 0

            def communicate(self, timeout=None):
                return json.dumps({"result": test.claude_says, "is_error": False}), ""
        self.tools.subprocess.Popen = FakePopen
        self.claude_says = "Found 2 emails from Walmart."

    def tearDown(self):
        self.mac._call, self.tools.subprocess.Popen = self.real_call, self.real_popen

    def test_connected_runs_claude(self):
        out = json.loads(self.tools.connectors({"service": "gmail", "request": "Find Walmart receipts"}, session_id="s1"))
        self.assertEqual(out["result"], "Found 2 emails from Walmart.")
        self.assertEqual(self.asked[0][:2], ("accounts", "need"))
        self.assertEqual(self.asked[0][2]["service"], "gmail")
        self.assertEqual(self.asked[0][3], "s1")
        self.assertEqual(len(self.runs), 1)

    def test_missing_account_stops_before_running_claude(self):
        self.hub_says = {"ok": True, "result": {"connected": False, "message": "Stop working on this now."}}
        out = json.loads(self.tools.connectors({"service": "gmail", "request": "Find receipts"}, session_id="s1"))
        self.assertEqual(out["error"], "Stop working on this now.")
        self.assertTrue(out["account_not_connected"])
        self.assertEqual(self.runs, [])

    def test_hub_unreachable_never_blocks(self):
        self.hub_says = {"ok": False, "error": "Could not reach the hub"}
        out = json.loads(self.tools.connectors({"service": "calendar", "request": "List events"}))
        self.assertIn("result", out)
        self.assertEqual(len(self.runs), 1)

    def test_a_run_that_finds_the_account_missing_asks_again(self):
        self.claude_says = "The Gmail connector needs authentication before I can search."
        answers = iter([{"ok": True, "result": {"connected": True}},
                        {"ok": True, "result": {"connected": False, "message": "Stop; card is up."}}])

        def fake_call(op, args, timeout=60, session_id="", area="mac"):
            self.asked.append((area, op, args, session_id))
            return next(answers)
        self.mac._call = fake_call
        out = json.loads(self.tools.connectors({"service": "gmail", "request": "Find receipts"}, session_id="s1"))
        self.assertEqual(out["error"], "Stop; card is up.")
        self.assertTrue(self.asked[1][2]["failed"])



# These tests check Colin's own home (his name, mailboxes, connectors, plans and time zone), so they run in it
# whatever ZERO_HOME says (tests/run_matrix.py runs the rest of the suite in every home shape).
def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()
