"""A reply after the person left the app (Colin, Oct 11): he asked School "Tell me if I have anything due today", put
the phone down, and the answer arrived without a notification. Now a typed message's reply goes out as a notification
when the device it was typed on has stopped checking in (looking.py), and never while that device is on screen.

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import hermes, looking, turns, watcher  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402
from test_proactivity import run  # noqa: E402

REPLY = "Three POL 101 items are due tonight at 11:55 p.m."
PHONE = looking.key("Bearer phone-token")


class Looking(unittest.TestCase):
    def setUp(self):
        looking._seen.clear()

    def test_a_device_on_screen_is_not_away(self):
        looking.saw(PHONE)
        self.assertFalse(looking.away(PHONE))

    def test_a_device_that_stopped_checking_in_is_away(self):
        looking._seen[PHONE] = time.time() - looking.AWAY_SEC - 1
        self.assertTrue(looking.away(PHONE))

    def test_no_device_is_never_away(self):
        """A call's hand-off or a hub turn has no device: those are never pushed from here."""
        self.assertFalse(looking.away(None))
        self.assertIsNone(looking.key(None))
        self.assertIsNone(looking.key("Basic x"))


class ReplyAway(AppCase):
    def setUp(self):
        super().setUp()
        looking._seen.clear()
        self.pushed = []

    def turn(self, *, leave: bool, typed: bool = True, reply: str = REPLY):
        async def fake_stream(bot_id, text, **kw):
            if leave:
                looking._seen[PHONE] = time.time() - looking.AWAY_SEC - 1
            yield {"type": "reply", "text": reply}
            yield {"type": "done"}

        async def fake_push(title, body, bot_id=None, **kw):
            self.pushed.append((title, body, bot_id, kw))
            return True

        async def go():
            with mock.patch.object(hermes, "stream_turn", fake_stream), \
                    mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")), \
                    mock.patch.object(watcher, "push", fake_push):
                looking.saw(PHONE)
                if typed:
                    t = turns.send("school", "Tell me if I have anything due today", device=PHONE)
                else:
                    t = turns.start("school", "what's due", consent_text="what's due", device=PHONE)
                return [ev async for ev in t.follow() if ev is not None]
        return run(go())

    def test_the_reply_is_a_notification_after_leaving(self):
        self.turn(leave=True)
        self.assertEqual(len(self.pushed), 1)
        title, body, bot, kw = self.pushed[0]
        self.assertEqual((title, body, bot), ("School", REPLY, "school"))
        self.assertTrue(kw.get("urgent"))
        self.assertTrue(kw.get("exact"))

    def test_a_long_reply_notifies_with_its_first_paragraph(self):
        self.turn(leave=True, reply=REPLY + "\n\nForm C is already graded.")
        self.assertEqual(self.pushed[0][1], REPLY)

    def test_no_notification_while_the_chat_is_open(self):
        self.turn(leave=False)
        self.assertEqual(self.pushed, [])

    def test_a_calls_hand_off_is_not_pushed_from_here(self):
        self.turn(leave=True, typed=False)
        self.assertEqual(self.pushed, [])

    def test_a_reply_that_says_nothing_is_not_pushed(self):
        self.turn(leave=True, reply=".")
        self.assertEqual(self.pushed, [])


if __name__ == "__main__":
    unittest.main()
