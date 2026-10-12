"""Sign-in consent (round 4 of QA): hand sign-ins are gated, Not Now on scheduled work holds a day,
Colin's request covers only its own turn, logins use the shared-host rule, and a busy bot on a call
keeps Colin's words.

Run on alien: ~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import consent, logins, team, turns, voice  # noqa: E402

plugin = sys.modules["taffy_consent"]


class Consent(unittest.TestCase):
    def setUp(self):
        self.real = plugin.DB_PATH
        plugin.DB_PATH = Path(tempfile.mkdtemp()) / "consent.db"

    def tearDown(self):
        plugin.DB_PATH = self.real

    def test_hand_sign_in_is_remembered_by_company(self):
        consent.mark_signed_in("school", "elearning.lakeview.test")
        self.assertIn("lakeview.test", plugin.signed_in_sites("school"))
        self.assertEqual(plugin.signed_in_sites("money"), set())

    def test_not_now_on_scheduled_work_holds_a_day(self):
        sched = plugin.request("money", "tally.example.com", "", None, None, why="scheduled")
        asked = plugin.request("money", "chase.com", "check my balance", "s1", None, why="background")
        plugin.decide(sched, False)
        plugin.decide(asked, False)
        with plugin.db() as c:   # an hour later
            c.execute("UPDATE requests SET decided = decided - 3600")
        self.assertTrue(plugin.denied_recently("money", "tally.example.com"))
        self.assertFalse(plugin.denied_recently("money", "chase.com"))

    def test_request_covers_only_its_own_turn(self):
        plugin.open_pass("message", "check my Lakeview grades", bot_id="school", session_id="chat-1")
        self.assertIsNotNone(plugin.live_pass(session_id="chat-1"))
        consent.close_message_passes("chat-1")
        self.assertIsNone(plugin.live_pass(session_id="chat-1"))

    @unittest.skipUnless(Path("/usr/share/publicsuffix/public_suffix_list.dat").exists(),
                         "needs the system Public Suffix List (present on alien)")
    def test_logins_use_shared_host_rule(self):
        self.assertNotEqual(logins._base("lakeview.sharepoint.com"), logins._base("other.sharepoint.com"))
        self.assertEqual(logins._base("identity.bloomerang.co"), logins._base("crm.bloomerang.co"))


class Redaction(unittest.TestCase):
    def test_keeps_dates_course_codes_and_phones(self):
        for text in ["due 2026-10-11", "The course code BUS 305", "pushed code to GitHub", "call Theo at 609-555-0142"]:
            with self.subTest(text=text):
                self.assertEqual(team._redact(text)[0], text)

    def test_removes_numbers_and_codes(self):
        for text, gone in [("card 4111 2222 3333 4444", "4444"), ("account 123456789", "123456789"),
                           ("code is 4821", "4821"), ("password: hunter2", "hunter2")]:
            with self.subTest(text=text):
                self.assertNotIn(gone, team._redact(text)[0])


class CallWords(unittest.TestCase):
    def test_busy_bot_keeps_colins_words(self):
        call = voice.Call(id="c1", bot_id="default", voice="cove")
        call.transcript = [("Colin", "sign in to Lakeview and check my grade")]
        seen = []
        real_start, real_wait = turns.start, voice._wait_for

        def start(bot_id, text, consent_text=None):
            seen.append(consent_text)
            if len(seen) == 1:
                raise turns.Busy(bot_id)
            t = turns.Turn(bot_id, "app")
            t.done = True
            return t

        async def wait_for(call, turn, seconds):
            return "Done.", None
        turns.start, voice._wait_for = start, wait_for
        try:
            asyncio.run(voice.ask_bot(call, "check his Lakeview grade"))
        finally:
            turns.start, voice._wait_for = real_start, real_wait
        self.assertEqual(seen, ["sign in to Lakeview and check my grade"] * 2)


if __name__ == "__main__":
    unittest.main()
