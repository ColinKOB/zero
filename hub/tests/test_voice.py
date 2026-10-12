"""Calls: what gets said when, in Colin's own words where it matters, and plain error text.

Run on alien: ~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import briefing, voice  # noqa: E402


def new_call():
    return voice.Call(id="t1", bot_id="default", voice="cove")


class Speech(unittest.TestCase):
    def setUp(self):
        self.spoken = []

        async def speak(call, text):
            self.spoken.append(text)
        self.real_speak = voice.speak
        voice.speak = speak
        self.gap = voice.PROGRESS_GAP_SEC

    def tearDown(self):
        voice.speak = self.real_speak
        voice.PROGRESS_GAP_SEC = self.gap

    def run_speaker(self, call, seconds=0.5):
        async def go():
            task = asyncio.create_task(voice._speaker(call))
            await asyncio.sleep(seconds)
            call.closed = True
            call.speech_ready.set()
            await task
        asyncio.run(go())

    def test_newest_progress_only_and_final_wins(self):
        call = new_call()
        voice.say(call, "Added Coke.")
        voice.say(call, "Added ice.")
        voice.say(call, "Your cart is ready: 2 items, $14.10.", must=True, final=True)
        self.run_speaker(call, 2.5)
        self.assertEqual(self.spoken, ["Your cart is ready: 2 items, $14.10."])

    def test_question_goes_ahead_of_waiting_progress(self):
        voice.PROGRESS_GAP_SEC = 60
        call = new_call()
        call.last_progress = __import__("time").time()     # progress was just said
        voice.say(call, "Found two ice makers.")
        voice.say(call, "Question: the $89 one or the $129 one?", must=True)
        self.run_speaker(call, 1.0)
        self.assertEqual(self.spoken, ["Question: the $89 one or the $129 one?"])

    def test_waits_while_colin_talks(self):
        call = new_call()
        now = __import__("time").time()
        call.colin_talking, call.talking_at, call.last_user_delta = True, now, now
        voice.say(call, "Question: pickup at 6?", must=True)
        self.run_speaker(call, 1.0)
        self.assertEqual(self.spoken, [])

    def test_stale_or_wordless_talking_does_not_block(self):
        # Muted with the flag stuck, a lost "stopped" update, or road noise with no words: news still comes.
        call = new_call()
        now = __import__("time").time()
        call.colin_talking, call.talking_at, call.last_user_delta = True, now - 10, now - 30
        voice.say(call, "Your cart is ready.", must=True, final=True)
        self.run_speaker(call, 2.5)
        self.assertEqual(self.spoken, ["Your cart is ready."])

    def test_nothing_said_while_on_hold(self):
        call = new_call()
        voice.calls[call.id] = call
        voice.set_away(call.id, True)
        voice.say(call, "Your cart is ready.", must=True, final=True)
        self.run_speaker(call, 1.0)
        self.assertEqual(self.spoken, [])          # not even the 10-second fallback speaks into a held call
        voice.calls.pop(call.id, None)

    def test_withdrawn_cue_is_not_said(self):
        call = new_call()
        now = __import__("time").time()
        call.colin_talking, call.talking_at, call.last_user_delta = True, now, now
        voice.say(call, "Press Allow on your Mac.", must=True, tag="mac")
        voice.unsay(call, "mac")
        call.colin_talking = False
        self.run_speaker(call, 2.0)
        self.assertEqual(self.spoken, [])

    def test_hang_up_sends_unsaid_results(self):
        call = new_call()
        voice.calls[call.id] = call
        sent = []

        async def after(c, text, title=""):
            sent.append(text)

        async def wrap(c):
            return None
        real = voice._after_hang_up, voice._wrap_up
        voice._after_hang_up, voice._wrap_up = after, wrap
        voice.server.proc = None
        try:
            voice.say(call, "Progress note.")
            voice.say(call, "Your cart is ready: $14.10.", must=True, final=True)
            voice.say(call, "Press Allow on your Mac.", must=True, tag="mac")

            async def go():
                await voice.stop(call.id)
                await __import__("asyncio").sleep(0.05)
            asyncio.run(go())
        finally:
            voice._after_hang_up, voice._wrap_up = real
        self.assertEqual(sent, ["Your cart is ready: $14.10."])


class Words(unittest.TestCase):
    def test_colin_since_last_handoff(self):
        call = new_call()
        call.transcript = [("Colin", "Can you add Coke"), ("You", "Sure."), ("Colin", "and ice")]
        self.assertEqual(voice._colin_since_last(call), "Can you add Coke and ice")
        call.transcript.append(("Colin", "use my Mac"))
        self.assertEqual(voice._colin_since_last(call), "use my Mac")

    def test_speakable_cuts_at_sentence(self):
        text = "First sentence here. " * 100
        out = voice.speakable(text, 200)
        self.assertLessEqual(len(out), 200)
        self.assertTrue(out.endswith("."))

    def test_plain_errors(self):
        self.assertIn("limit", voice.plain_error("429 rate_limit_exceeded: tokens per min"))
        self.assertNotIn("websocket", voice.plain_error("websocket closed 1006").lower())
        self.assertNotIn("limit", voice.plain_error("failed to generate audio"))
        self.assertNotIn("signed in", voice.plain_error("signal lost"))
        self.assertNotIn("server", voice.plain_error("401 Unauthorized"))


class BriefingChat(unittest.TestCase):
    def test_hidden_turns_left_out(self):
        msgs = [{"role": "user", "text": "[Board update] School finished"},
                {"role": "assistant", "text": "School found the syllabus says Chicago style"},
                {"role": "user", "text": "[Reactions from Colin since...]\nHow are you"},
                {"role": "assistant", "text": "Good"},
                {"role": "user", "text": "[From a voice call] Colin is on a live call... Colin's request: hi"},
                {"role": "assistant", "text": "Hi"}]
        out = briefing._visible(msgs)
        # Board replies and what a call found are part of the conversation Colin saw.
        self.assertEqual([m["text"] for m in out],
                         ["School found the syllabus says Chicago style", "How are you", "Good", "Hi"])
        # Fluid chat (#75, #78): a reply to the hub's own turn that says nothing is left out.
        quiet = [{"role": "user", "text": "[Board update] School finished"}, {"role": "assistant", "text": "Noted"}]
        self.assertEqual(briefing._visible(quiet), [])


if __name__ == "__main__":
    unittest.main()
