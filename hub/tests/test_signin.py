"""A bot asking Colin to sign in from a chat (signin.py)."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import config, consent, signin, store, turns, watcher  # noqa: E402


class SignInCards(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hub_db = config.HUB_DB
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        plugin = sys.modules["taffy_consent"]
        self.consent_db = plugin.DB_PATH
        plugin.DB_PATH = self.tmp / "consent.db"
        self.run_internal, self.loop, self.push = turns.run_internal, signin.loop, watcher.push
        self.notes, self.pushed = [], []

        async def fake_run(bot_id, text, continues=None):
            self.notes.append((bot_id, text))
            return "Signed in, publishing now."
        turns.run_internal = fake_run

        async def fake_push(title, body, bot_id=None, **kw):
            self.pushed.append((title, body, kw.get("urgent")))
            return True
        watcher.push = fake_push

    def tearDown(self):
        config.HUB_DB = self.hub_db
        store._local.conn = None
        store._schema_ready = False
        sys.modules["taffy_consent"].DB_PATH = self.consent_db
        turns.run_internal, signin.loop, watcher.push = self.run_internal, self.loop, self.push

    def cards(self):
        return [json.loads(r["body"]) for r in store.db().execute("SELECT body FROM feed WHERE kind='login'")]

    def test_one_card_per_site_and_signing_in_resumes_the_bot(self):
        fid, said = signin.ask("clients", "https://app.netlify.com/login", "publish the Halloween update", "s1")
        self.assertIsNotNone(fid)
        # Fluid chat (#46): the Zip leaves the site alone but keeps doing the rest of what was asked.
        self.assertIn("finish any part of the request that does not need it", said)
        self.assertNotIn("Stop working on this now", said)
        again, said2 = signin.ask("clients", "https://app.netlify.com/login", "publish", "s1")
        self.assertIsNone(again)                                   # already asked
        self.assertEqual(len(self.cards()), 1)

        async def go():
            signin.loop = asyncio.get_running_loop()
            await asyncio.to_thread(signin.resolve, "clients", fid, "signed_in")   # from a worker thread, as in the app
            for _ in range(50):
                if self.notes:
                    break
                await asyncio.sleep(0.02)
        asyncio.run(go())
        self.assertEqual(self.cards()[0]["state"], "signed_in")
        self.assertEqual(len(self.notes), 1)
        self.assertIn("Carry on", self.notes[0][1])
        self.assertTrue(consent.db().execute(
            "SELECT 1 FROM requests WHERE bot_id='clients' AND session_id='s1' AND status='allowed'").fetchone())
        self.assertIn("netlify.com", sys.modules["taffy_consent"].signed_in_sites("clients"))

    def resolve_and_wait(self, fid, outcome):
        async def go():
            signin.loop = asyncio.get_running_loop()
            await asyncio.to_thread(signin.resolve, "clients", fid, outcome)
            for _ in range(50):
                if self.notes and self.pushed:
                    break
                await asyncio.sleep(0.02)
        asyncio.run(go())

    def test_closing_the_card_tells_the_bot_to_finish_without_it(self):
        """H2: closing the card or backing out used to leave the chat hanging after "there's a card"."""
        fid, _ = signin.ask("clients", "https://app.netlify.com/login", "publish", "s1")
        self.resolve_and_wait(fid, "declined")
        self.assertEqual(self.cards()[0]["state"], "declined")
        self.assertEqual(len(self.notes), 1)
        self.assertIn("without signing in", self.notes[0][1])
        self.assertIn("Do not sign in there", self.notes[0][1])
        self.assertFalse(consent.db().execute("SELECT 1 FROM requests WHERE status='allowed'").fetchone())
        fid2, _ = signin.ask("clients", "https://app.netlify.com/login", "publish", "s1")
        self.assertIsNotNone(fid2)                                 # a new ask is allowed after closing

    def test_resumed_reply_is_pushed_as_urgent(self):
        """H2: the answer after Save Login or Sign In Myself used to land in the chat without a notification."""
        fid, _ = signin.ask("clients", "https://app.netlify.com/login", "publish", "s1")
        self.resolve_and_wait(fid, "saved")
        self.assertEqual([(b, u) for _, b, u in self.pushed], [("Signed in, publishing now.", True)])
        self.assertEqual(store.db().execute("SELECT COUNT(*) FROM feed WHERE kind='message'").fetchone()[0], 1)


class FollowUpPass(unittest.TestCase):
    """H3: Colin's request counts again for the hub's follow-up turn for it, and only for that turn."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.plugin = sys.modules["taffy_consent"]
        self.consent_db = self.plugin.DB_PATH
        self.plugin.DB_PATH = self.tmp / "consent.db"
        self.run_turn = turns.hermes.run_turn

    def tearDown(self):
        self.plugin.DB_PATH = self.consent_db
        turns.hermes.run_turn = self.run_turn

    def test_follow_up_turn_reopens_and_recloses_the_request(self):
        self.plugin.open_pass("message", "what is due in School?", bot_id="school", session_id="chat-1")
        consent.close_message_passes("chat-1")                     # his turn ended
        self.assertIsNone(self.plugin.live_pass(session_id="chat-1"))
        seen = {}

        async def fake_run_turn(bot_id, text, team=False):
            seen["pass"] = self.plugin.live_pass(session_id="chat-1")
            return "Here is what is due."
        turns.hermes.run_turn = fake_run_turn
        asyncio.run(turns.run_internal("school", "[Hub note] Colin allowed it.", continues="chat-1"))
        self.assertIsNotNone(seen["pass"])
        self.assertEqual(seen["pass"]["text"], "what is due in School?")
        self.assertIsNone(self.plugin.live_pass(session_id="chat-1"))   # closed again afterwards
        asyncio.run(turns.run_internal("school", "[Board update] something finished"))
        self.assertIsNone(self.plugin.live_pass(session_id="chat-1"))

    def test_an_old_request_is_not_reopened(self):
        self.plugin.open_pass("message", "what is due?", bot_id="school", session_id="chat-1")
        with self.plugin.db() as c:
            c.execute("UPDATE passes SET created = created - 3600")
        consent.close_message_passes("chat-1")
        self.assertIsNone(self.plugin.reopen_pass("chat-1"))
