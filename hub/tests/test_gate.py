"""The taffy-tools gate (internals QA round 1): which sites are sensitive, when opening a page needs a card,
and how the card explains itself. The permission tiers have their own file (test_tiers.py).

Run: python3 -m unittest discover -s hub/tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import consent  # noqa: E402,F401  (loads the shared consent module first)
import plugin_loader  # noqa: E402
import hermes_detector  # noqa: E402

tt = plugin_loader.load()
gate, plugin, fast_steps = tt.gate, tt.consent, tt.fast_steps


class GateCase(unittest.TestCase):
    """A fresh consent.db, a fixed bot, no saved logins and a judge that must be set per test."""

    bot = "school"
    paid_on = False   # classes that test the (mocked) judge turn Colin's paid-calls switch on

    def setUp(self):
        # Colin's home, written out, whatever home the suite runs in: these tests read his wording and facts. Other
        # homes have their own tests (test_plugin_home.py).
        pin = tt.home.pinned(tt.home.colin_settings())
        pin.__enter__()
        self.addCleanup(pin.__exit__, None, None, None)
        # Restored last of all (cleanups run in reverse): a subclass that swaps _taffy_home again and restores it in a
        # cleanup of its own would otherwise put this class's stand-in back after tearDown.
        self.addCleanup(setattr, gate, "_taffy_home", gate._taffy_home)
        self.real_db = plugin.DB_PATH
        plugin.DB_PATH = Path(tempfile.mkdtemp()) / "consent.db"
        self.saved = {"bot": gate._bot, "sites": gate._saved_sites, "team": gate._in_team_session, "lane": gate._lane,
                      "decide": fast_steps.decide_one, "env": gate._session_env, "task": gate._running_task,
                      "paid": fast_steps.paid_calls_on, "detect": gate._hermes_detect, "cwd": gate._terminal_cwd,
                      "home": gate._taffy_home}
        # The judge is mocked and never reaches the network. Tests of the judge set self.paid to True; the default
        # is Colin's: paid calls off, so the hard rules decide.
        self.paid = self.paid_on
        fast_steps.paid_calls_on = lambda: self.paid
        gate._bot = lambda: self.bot
        gate._saved_sites = lambda bot: set(self.saved_sites)
        gate._in_team_session = lambda sid: False
        gate._lane = lambda sid: ""
        gate._session_env = lambda name: ""
        gate._running_task = lambda bot: None
        # Hermes's own detector (the faithful copy when Hermes is not importable), and no terminal folder unless a
        # test sets one, wherever the tests run (Oct 8 QA round 5, M1).
        gate._hermes_detect = lambda: hermes_detector.detect
        gate._terminal_cwd = lambda task_id: None
        # The Zips' folder (scheduled scripts and job lists) is an empty temporary one, never the home folder's.
        taffy_home = Path(tempfile.mkdtemp()) / ".taffy"
        gate._taffy_home = lambda: taffy_home
        self.saved_sites = set()
        self.judge_calls = []
        self.judge = None   # a function(body) -> answer dict, or None to fail the test if the judge is asked

        def decide(body, timeout=3):
            self.judge_calls.append(body)
            if self.judge is None:
                raise AssertionError("the judge was asked: " + str(body)[:200])
            return self.judge(body)
        fast_steps.decide_one = decide

    def tearDown(self):
        plugin.DB_PATH = self.real_db
        gate._bot, gate._saved_sites, gate._in_team_session = self.saved["bot"], self.saved["sites"], self.saved["team"]
        gate._lane = self.saved["lane"]
        fast_steps.decide_one, gate._session_env = self.saved["decide"], self.saved["env"]
        gate._running_task = self.saved["task"]
        fast_steps.paid_calls_on = self.saved["paid"]
        gate._hermes_detect, gate._terminal_cwd = self.saved["detect"], self.saved["cwd"]
        gate._taffy_home = self.saved["home"]

    def ask(self, text="what is due this week?", session="chat-1"):
        return plugin.open_pass("message", text, bot_id=self.bot, session_id=session)

    def call(self, tool, args, session="chat-1", result='{"ok": true}'):
        """The gate's answer, and, when it lets the call through, the tool's result as Hermes passes it to the
        post hook (actions are listed as done only after the tool came back without an error)."""
        out = gate.pre_tool_call(tool, args, session_id=session)
        if out is None:
            gate.post_tool_call(tool, args, result, session_id=session)
        return out

    def cards(self):
        with plugin.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM requests WHERE status='pending' ORDER BY id")]


class SensitiveSites(unittest.TestCase):
    """H1: the old substring pattern was wrong both ways (48 hosts tested in the QA report)."""

    def test_money_sites_the_old_pattern_missed(self):
        for host in ["bilt.com", "biltrewards.com", "ally.com", "sofi.com", "etrade.com", "wise.com", "affirm.com",
                     "klarna.com", "geico.com", "progressive.com", "bluecrossnc.com", "zocdoc.com", "cvs.com",
                     "walgreens.com", "turbotax.intuit.com", "secure.chase.com", "nj.gov", "irs.gov",
                     "accounts.google.com", "mychart.atlantichealth.org", "tdbank.com", "navyfederal.org"]:
            with self.subTest(host=host):
                self.assertTrue(plugin.is_sensitive(host))

    def test_ordinary_sites_the_old_pattern_flagged(self):
        for host in ["healthline.com", "menshealth.com", "weather.gov", "nps.gov", "recreation.gov", "bankrate.com",
                     "creditkarma.com", "tally.so", "acu.edu", "ecu.edu", "netlify.com", "mail.google.com",
                     "elearning.lakeview.test", "github.com", "amazon.com"]:
            with self.subTest(host=host):
                self.assertFalse(plugin.is_sensitive(host))

    def test_opening_a_public_page_is_never_sensitive(self):
        self.assertFalse(plugin.is_sensitive("chase.com", kind="navigate"))
        self.assertTrue(plugin.is_sensitive("chase.com", kind="signin"))


class Navigation(GateCase):
    def test_public_pages_on_sensitive_sites_open_without_a_card(self):
        """H1: a Mayo Clinic article or weather.gov at night used to raise an urgent card."""
        for url in ["https://www.mayoclinic.org/diseases", "https://www.weather.gov/phi", "https://www.chase.com/rates"]:
            with self.subTest(url=url):
                self.assertIsNone(self.call("browser_navigate", {"url": url}, session=""))
        self.assertEqual(self.cards(), [])

    def test_a_site_with_a_saved_login_is_gated_and_worded_as_opening(self):
        """M1: a blocked visit said "wants to sign in" when the bot only tried to open a page."""
        self.saved_sites = {"chase.com"}
        blocked = self.call("browser_navigate", {"url": "https://secure.chase.com/accounts"}, session="")
        self.assertIsNotNone(blocked)
        self.assertIn("allow opening", blocked["message"])
        card = self.cards()[0]
        self.assertEqual((card["kind"], card["why"], card["reason"]), ("navigate", "background", ""))

    def test_ordinary_site_with_a_session_opens_while_colin_asked(self):
        self.saved_sites = {"netlify.com"}
        self.ask("check the Blue Heron preview")
        self.assertIsNone(self.call("browser_navigate", {"url": "https://app.netlify.com/sites/x"}))
        self.assertEqual(self.judge_calls, [])     # ordinary sites never ask the judge


class CardWording(GateCase):
    paid_on = True

    def test_a_sensitive_default_is_not_called_colins_own_rule(self):
        """M1: the card said "You set this site to ask every time" when Colin had set nothing."""
        self.saved_sites = {"chase.com"}
        self.ask("check my balance")
        self.judge = lambda body: {"type": "predicate", "probability": 0.9}
        blocked = self.call("browser_navigate", {"url": "https://secure.chase.com/"})
        self.assertIn("can involve money", blocked["message"])
        self.assertEqual(self.cards()[0]["why"], "sensitive")

    def test_a_site_colin_set_to_ask_says_so(self):
        self.saved_sites = {"netlify.com"}
        plugin.set_mode(self.bot, "netlify.com", "ask")
        self.ask("check the preview")
        self.call("browser_navigate", {"url": "https://app.netlify.com/"})
        self.assertEqual(self.cards()[0]["why"], "ask")

    def test_a_sensitive_site_that_does_not_fit_says_so(self):
        self.saved_sites = {"chase.com"}
        self.ask("check the Blue Heron preview")
        self.judge = lambda body: {"type": "predicate", "probability": 0.05}
        self.call("browser_navigate", {"url": "https://secure.chase.com/"})
        self.assertEqual(self.cards()[0]["why"], "mismatch")


class Relevance(GateCase):
    paid_on = True

    def test_new_wording_and_threshold(self):
        self.assertEqual(gate.RELEVANCE_MIN, 0.3)
        p = plugin.live_pass(session_id="x") or {"id": self.ask("check the Blue Heron preview"),
                                                   "text": "check the Blue Heron preview"}
        self.judge = lambda body: {"type": "predicate", "probability": 0.35}
        self.assertTrue(gate._fits(p, "netlify.com", "open the deploy preview"))
        self.assertIn("plausibly", self.judge_calls[0]["questions"][0]["instructions"])
        self.assertIn("in order to: open the deploy preview", self.judge_calls[0]["input"])

    def test_judge_failure_or_refusal_counts_as_fitting(self):
        p = {"id": self.ask("check the preview"), "text": "check the preview"}

        def boom(body):
            raise TimeoutError("slow")
        self.judge = boom
        self.assertTrue(gate._fits(p, "netlify.com"))
        self.judge = lambda body: {"type": "refusal"}
        self.assertTrue(gate._fits(p, "github.com"))


class Expiry(GateCase):
    def test_unanswered_cards_expire(self):
        """M3: yesterday's cards sat in Needs You and resumed old conversations."""
        chat = plugin.request(self.bot, "netlify.com", "check", "s1", None, why="mismatch")
        task = plugin.request(self.bot, "chase.com", "", None, "t_1", why="background")
        with plugin.db() as c:
            c.execute("UPDATE requests SET created = created - 7*3600")
        self.assertEqual([r["id"] for r in plugin.pending()], [task])
        with plugin.db() as c:
            c.execute("UPDATE requests SET created = created - 20*3600 WHERE id=?", (task,))
        self.assertEqual(plugin.pending(), [])
        self.assertIsNone(plugin.decide(chat, True))
        self.assertEqual(plugin.get_request(chat)["status"], "expired")


class SignInLog(GateCase):
    def test_repeated_sign_ins_in_one_sitting_are_logged_once(self):
        """L4: six "Signed in" lines in six minutes."""
        for _ in range(6):
            plugin.log_signin(self.bot, "elearning.lakeview.test", "You allowed it", "button")
        plugin.log_signin(self.bot, "lakeview.test", "You allowed it", "button")
        plugin.log_signin("money", "elearning.lakeview.test", "You allowed it", "button")
        self.assertEqual(len(plugin.signins_after(0)), 2)


if __name__ == "__main__":
    unittest.main()
