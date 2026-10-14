"""Chat (QA round 6): one rule for what each message is, and messages sent while a bot is busy wait their
turn instead of being refused.

Run on alien: ~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import history, turns  # noqa: E402


def msgs(*pairs):
    return [{"role": r, "text": t, "ts": i} for i, (r, t) in enumerate(pairs)]


class Classes(unittest.TestCase):
    def test_every_kind(self):
        out = history.classify(msgs(
            ("user", "[Reactions from Colin since your last message: heart on x.]\nHow are you"),
            ("assistant", "Good"),
            ("user", "[Board update from the hub] School finished"), ("assistant", "School is done."),
            ("user", "[Hub note, not a message from Colin] Colin allowed it"), ("assistant", "Signed in."),
            ("user", "[From a voice call] Colin is on a live call... Colin's request: cart total?"),
            ("assistant", "Your cart is $14.10."), ("assistant", "Anything else?"),
            ("user", "[Voice call ended] transcript..."), ("assistant", "Saved."),
            ("user", "[Voice call] old style words"), ("assistant", "ok")))
        self.assertEqual([m["class"] for m in out],
                         ["colin", "bot", "board_update", "board_reply", "hub_note", "bot", "call_turn", "call_reply",
                          "call_reply", "wrap_up", "wrap_up_reply", "colin", "bot"])
        self.assertEqual(out[0]["display"], "How are you")
        self.assertEqual(out[6]["display"], "cart total?")
        self.assertEqual(out[11]["display"], "old style words")

    def test_colin_words_only(self):
        words = [m["display"] for m in history.colin_words(msgs(
            ("user", "open walmart on my mac"), ("user", "[Board update] x"), ("user", "[From Money] hi"),
            ("user", "[kanban] Task t_1 completed. Title: Draft")))]
        self.assertEqual(words, ["open walmart on my mac"])


class Queue(unittest.TestCase):
    def test_busy_message_waits_its_turn(self):
        order = []

        async def fake_run(turn, text, own):
            order.append(("start", text))
            await asyncio.sleep(0.2)
            await turn.push({"type": "reply", "text": "re " + text})
            await turn.push({"type": "done"})
            turns._turns.pop(turn.bot_id, None)

        async def go():
            real = turns._run
            turns._run = fake_run
            try:
                first = turns.send("default", "one")
                second = turns.send("default", "two")
                self.assertIsNot(first, second)
                events = [ev async for ev in second.follow() if ev]
                self.assertEqual(events[0]["type"], "queued")
                self.assertEqual(events[1]["type"], "started")
                self.assertEqual(events[-2], {"type": "reply", "text": "re two"})
            finally:
                turns._run = real
        asyncio.run(go())
        self.assertEqual([t for _, t in order], ["one", "two"])


if __name__ == "__main__":
    unittest.main()


class Round7(unittest.TestCase):
    def test_turns_found_by_id(self):
        async def go():
            t = turns.Turn("default", "app")
            self.assertIs(turns.by_id(t.id), t)
            self.assertIsNone(turns.by_id("nope"))
        asyncio.run(go())

    def test_internal_turns_wait_for_queued_messages(self):
        order = []

        async def go():
            turns._queued["money"] = [object()]
            real = turns.hermes.run_turn

            async def fake(bot_id, text, team=False):
                order.append(text)
                return "ok"
            turns.hermes.run_turn = fake
            try:
                task = asyncio.create_task(turns.run_internal("money", "board update"))
                await asyncio.sleep(0.7)
                self.assertEqual(order, [])
                turns._queued["money"] = []
                await asyncio.wait_for(task, 3)
            finally:
                turns.hermes.run_turn = real
                turns._queued.pop("money", None)
        asyncio.run(go())
        self.assertEqual(order, ["board update"])


class MacLinkHolder(unittest.TestCase):
    def test_first_device_claims_and_others_are_refused(self):
        import tempfile
        from taffy_hub import config, store
        real = config.HUB_DB
        config.HUB_DB = Path(tempfile.mkdtemp()) / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        try:
            mac_code, phone_code = store.new_pairing_code(), store.new_pairing_code()
            mac = store.redeem_pairing_code(mac_code, "Mac")
            phone = store.redeem_pairing_code(phone_code, "iPhone")
            self.assertTrue(store.may_hold_mac_link(mac))
            self.assertFalse(store.may_hold_mac_link(phone))
            self.assertTrue(store.may_hold_mac_link(mac))
            self.assertTrue(any(d["mac_link"] for d in store.devices()))
        finally:
            config.HUB_DB = real
            store._local.conn = None
            store._schema_ready = False


class Choices(unittest.TestCase):
    def setUp(self):
        import tempfile
        from taffy_hub import config, store
        self.config, self.store = config, store
        self.real = config.HUB_DB
        config.HUB_DB = Path(tempfile.mkdtemp()) / "hub.db"
        store._local.conn = None
        store._schema_ready = False

    def tearDown(self):
        self.config.HUB_DB = self.real
        self.store._local.conn = None
        self.store._schema_ready = False

    def test_card_during_turn_waits_for_the_reply_and_answers_once(self):
        from taffy_hub import choices
        choices.offer("default", "Want me to open it?", ["Open it", "Not now"], during_turn=True)
        self.assertEqual(self.store.db().execute("SELECT COUNT(*) FROM feed WHERE kind='choices'").fetchone()[0], 0)
        choices.flush("default")
        row = self.store.db().execute("SELECT id FROM feed WHERE kind='choices'").fetchone()
        self.assertIsNotNone(row)
        with self.assertRaises(choices.ChoiceError):
            choices.answer("default", row["id"], "Something else")
        self.assertEqual(choices.answer("default", row["id"], "Open it")["state"], "picked")
        with self.assertRaises(choices.ChoiceError):
            choices.answer("default", row["id"], "Not now")

    def test_bad_cards_refused(self):
        from taffy_hub import choices
        for q, opts in [("Pick?", ["Only one"]), ("Pick?", ["A", "a"]), ("", ["A", "B"]), ("Pick?", list("ABCDE"))]:
            with self.subTest(opts=opts), self.assertRaises(choices.ChoiceError):
                choices.offer("default", q, opts, during_turn=False)


class NightHealth(unittest.TestCase):
    def test_short_night_outage_wakes_nobody(self):
        import tempfile
        from taffy_hub import config, health, store, watcher
        real_db, real_night, real_push = config.HUB_DB, watcher._night, watcher.push
        config.HUB_DB = Path(tempfile.mkdtemp()) / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        sent = []

        async def push(title, body, bot_id=None, bots=None, urgent=False):
            sent.append((body, urgent))
            return True
        watcher._night, watcher.push = (lambda: True), push
        health.down_since.clear()
        try:
            asyncio.run(health._set("bots", False))
            self.assertEqual(sent, [])
            health.down_since["bots"] -= health.NIGHT_DOWN_GRACE_SEC + 1
            asyncio.run(health._set("bots", False))
            self.assertTrue(sent and sent[-1][1])
            asyncio.run(health._set("bots", True))
            self.assertFalse(sent[-1][1])
        finally:
            config.HUB_DB, watcher._night, watcher.push = real_db, real_night, real_push
            store._local.conn = None
            store._schema_ready = False


class AddDevice(unittest.TestCase):
    def test_code_from_a_paired_device_pairs_once(self):
        import tempfile
        from taffy_hub import app as hub_app, config, store
        real = config.HUB_DB
        config.HUB_DB = Path(tempfile.mkdtemp()) / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        hub_app._new_codes.clear()
        try:
            out = hub_app.new_pairing_code("Mac")
            self.assertEqual(out["expires_in"], 900)
            self.assertIsNotNone(store.redeem_pairing_code(out["code"], "New iPhone"))
            self.assertIsNone(store.redeem_pairing_code(out["code"], "Again"))
            for _ in range(4):
                hub_app.new_pairing_code("Mac")
            with self.assertRaises(Exception):
                hub_app.new_pairing_code("Mac")
        finally:
            config.HUB_DB = real
            store._local.conn = None
            store._schema_ready = False
