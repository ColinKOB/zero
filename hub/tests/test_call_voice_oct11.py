"""How the call voice speaks, from Colin's review of a real call (Oct 11, 2026):

1. A result is said in full, up to about four sentences. "The details are in our chat" is only for a genuinely long
   list, and at most once per call.
2. A question the Zip asks is said together with the sentence it depends on, never dropped or held for later.
3. A label such as "Question:" is never said out loud.
4. The voice talks about its own work as "I", never "it".
5. No guessed dates: the voice says "I think" or checks before stating a date or deadline it is not sure of.

Nothing here reaches a live service. Run on alien:
cd hub && ZERO_TESTS=1 ~/taffy/.venv/bin/python -m unittest tests.test_call_voice_oct11
"""

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("ZERO_TESTS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import board, ondevice, turns, voice, who  # noqa: E402
from test_fluid_voice import FakeServer, chat_call  # noqa: E402
from test_ondevice_voice import Base  # noqa: E402

LONG_LIST = ("Six flights fit:\n1. JetBlue 7:40 a.m., $189\n2. Delta 9:15 a.m., $204\n3. United noon, $231\n"
             "4. Spirit 2 p.m., $99\n5. Delta 5 p.m., $210\n6. JetBlue 9 p.m., $150")


class ResultsInFull(unittest.TestCase):
    def test_four_sentences_are_said_whole(self):
        text = ("I checked eLearning. Your essay for Ethics is posted. The rubric asks for five pages. "
                "Professor Hall wants it as a PDF.")
        self.assertEqual(voice.brief(text), text)

    def test_long_prose_stops_at_four_sentences_with_no_chat_line(self):
        text = "One is done. Two is done. Three is done. Four is done. Five is done. Six is done."
        out = voice.brief(text)
        self.assertEqual(out, "One is done. Two is done. Three is done. Four is done.")
        self.assertNotIn(voice.DETAILS_IN_CHAT, out)

    def test_only_a_long_list_points_to_the_chat(self):
        out = voice.brief(LONG_LIST)
        self.assertTrue(out.startswith("Six flights fit: JetBlue 7:40 a.m., $189."))
        self.assertTrue(out.endswith(voice.DETAILS_IN_CHAT))
        short = "Two flights fit:\n1. JetBlue 7:40 a.m., $189\n2. Delta 9:15 a.m., $204"
        self.assertNotIn(voice.DETAILS_IN_CHAT, voice.brief(short))

    def test_the_chat_line_is_said_at_most_once_per_call(self):
        call = voice.Call(id="once", bot_id="default", voice="cove")
        first = voice.brief_for(call, LONG_LIST)
        second = voice.brief_for(call, LONG_LIST)
        self.assertIn(voice.DETAILS_IN_CHAT, first)
        self.assertNotIn(voice.DETAILS_IN_CHAT, second)
        self.assertTrue(second.startswith("Six flights fit:"))
        other = voice.Call(id="other", bot_id="default", voice="cove")
        self.assertIn(voice.DETAILS_IN_CHAT, voice.brief_for(other, LONG_LIST))

    def test_the_voice_is_told_to_say_results_in_full(self):
        rules = voice.speaking_rules("Zero")
        self.assertIn("say the result itself, up to about four sentences", rules)
        self.assertIn("at most once per call", rules)
        self.assertIn('Never put "the details are in our chat" in place of the result', rules)
        p = voice.voice_prompt("default", "You are Zero.")
        self.assertIn("say the result itself in your own words, most useful part first, up to about four sentences",
                      " ".join(p.split()))
        self.assertNotIn("briefly, most useful part first", p)
        self.assertIn("up to about four sentences", voice.job_guide())


class WorkStatusOnce(Base):
    def test_the_status_list_does_not_repeat_the_chat_line(self):
        groups = {"working": [{"title": f"Task {i}", "status": "running"} for i in range(5)], "needs_you": [],
                  "done": []}
        call = chat_call("st-once")
        voice.brief_for(call, LONG_LIST)
        with mock.patch.object(board, "grouped", lambda: groups), mock.patch.object(turns, "busy_bots", lambda: []):
            text = voice.work_status(call)
        self.assertIn("5 things are in progress", text)
        self.assertNotIn(voice.DETAILS_IN_CHAT, text)


class QuestionsWithTheirContext(unittest.TestCase):
    def test_the_sentence_a_question_depends_on_comes_with_it(self):
        text = ("I checked your inbox. Theo wrote twice this week. Dana sent the venue list. The caterer confirmed "
                "Friday. Theo says the budget is short by $400.\nQuestion: Want me to draft a reply to Theo?")
        out = voice.brief(text)
        self.assertTrue(out.endswith("Theo says the budget is short by $400. Want me to draft a reply to Theo?"))
        self.assertTrue(out.startswith("I checked your inbox."))
        self.assertNotIn("Question", out)

    def test_a_long_question_is_never_dropped(self):
        q = "Should I " + "really " * 40 + "send it?"
        out = voice.brief("I wrote the email to Theo. " + q)
        self.assertTrue(out.endswith(q))

    def test_the_voice_and_relay_are_told_to_keep_them_together(self):
        rules = voice.speaking_rules("Zero")
        self.assertIn("right after the sentence it depends on. Never drop it or save it for later.", rules)
        self.assertIn("keep a question together with the sentence before it", voice.relay_instructions())
        self.assertIn("right after the sentence it depends on", voice.job_guide())


class NotesWithQuestions(Base):
    def test_a_note_with_a_question_inside_is_said_whole_and_never_dropped(self):
        """A note like "Added the chips. Question: salsa too?" used to count as progress, so a newer note could
        replace it and its question was never asked."""
        said = []

        async def go():
            call = chat_call("note-q")
            turn = turns.Turn("default", "app")
            for ev in ({"type": "commentary", "text": "Added the chips.\nQuestion: salsa too?"},
                       {"type": "commentary", "text": "Checking delivery windows now."},
                       {"type": "reply", "text": "Your cart is ready: 2 items, $14.10."}, {"type": "done"}):
                await turn.push(ev)
            with mock.patch.object(voice, "say", lambda c, text, must=False, **kw: said.append((text, must))):
                await asyncio.wait_for(voice._follow_job(call, turn), 5)
            call.closed = True
        asyncio.run(go())
        self.assertEqual(said[0], ("Added the chips. Salsa too?", True))
        self.assertEqual(said[1], ("Checking delivery windows now.", False))
        self.assertEqual(said[2], ("Your cart is ready: 2 items, $14.10.", True))


class NoLabelsAloud(unittest.TestCase):
    def test_labels_come_off_at_the_start_of_a_sentence(self):
        self.assertEqual(voice.unlabel("Question: want the $89 one?"), "Want the $89 one?")
        self.assertEqual(voice.unlabel("Added Coke. Question: diet too?"), "Added Coke. Diet too?")
        self.assertEqual(voice.unlabel("Added Coke.\nQ: diet too?"), "Added Coke.\nDiet too?")
        self.assertEqual(voice.unlabel("Note: the store closes at 9."), "The store closes at 9.")
        self.assertEqual(voice.unlabel("The question: is it worth it?"), "The question: is it worth it?")
        self.assertEqual(voice.unlabel("Pickup at 5:30 today."), "Pickup at 5:30 today.")

    def test_speak_never_sends_a_label_to_the_voice(self):
        fake = FakeServer()

        async def go():
            call = voice.Call(id="lbl", bot_id="default", voice="cove")
            with mock.patch.object(voice.server, "call", fake.call):
                await voice.speak(call, "Question: want the $89 one?")
                await voice.speak(call, "Added the chips. Question: salsa too?")
        asyncio.run(go())
        self.assertEqual(fake.said(), ["Want the $89 one?", "Added the chips. Salsa too?"])

    def test_the_voice_and_relay_are_told_never_to_say_labels(self):
        self.assertIn('Never say a label such as "Question" out loud.', voice.speaking_rules("Zero"))
        self.assertIn('Never write a label such as "Question:".', voice.relay_instructions())


class FirstPerson(unittest.TestCase):
    def test_the_voice_and_relay_say_i_for_its_own_work(self):
        rules = voice.speaking_rules("Zero")
        self.assertIn('Talk about it as "I": say "I checked your calendar", never "It checked your calendar".',
                      " ".join(rules.split()))
        self.assertIn('never "It checked your calendar"', voice.relay_instructions())
        self.assertIn('as "I"', " ".join(voice.voice_prompt("default", "You are Zero.").split()))

    def test_the_hubs_own_hand_off_lines_say_i(self):
        source = Path(voice.__file__).read_text()
        for gone in ("Started and still working on it in the background", "is working on your earlier request too",
                     "Passed on to the job; it will", 'f"It hit a problem.'):
            self.assertNotIn(gone, source)
        for line in ("I'm still working on your earlier request too", "I started on this and I'm still working on it",
                     "I passed that on, and I'll pick it up", "I hit a problem and couldn't finish."):
            self.assertIn(line, source)


class StartedJobSaysI(Base):
    def setUp(self):
        super().setUp()
        voice.ask_bot = self.real[6]

    def test_a_job_that_keeps_running_comes_back_in_the_first_person(self):
        def start(bot_id, text, consent_text=None):
            return turns.Turn(bot_id, "app")

        async def go():
            call = chat_call("first-person")
            voice.calls[call.id] = call
            with mock.patch.object(turns, "start", start), mock.patch.object(voice, "QUICK_ANSWER_SEC", 0.2), \
                    mock.patch.object(voice, "WORKING_FIRST_SEC", 60):
                out = await voice.ask_bot(call, "Check eLearning for new assignments")
            call.closed = True
            voice.calls.pop(call.id, None)
            return out
        out = asyncio.run(go())
        self.assertTrue(out.startswith("I started on this and I'm still working on it: Check eLearning"))
        self.assertNotIn("It ", out)


class NoGuessedDates(unittest.TestCase):
    def test_the_voice_never_guesses_a_date(self):
        rules = voice.speaking_rules("Zero")
        self.assertIn("Never guess a date, a day or a deadline.", rules)
        self.assertIn('say "I think" with it, or hand it off to check before you say it', " ".join(rules.split()))
        self.assertIn("Never make up facts, dates, deadlines, times, prices or names.",
                      " ".join(voice.voice_prompt("default", "You are Zero.").split()))
        self.assertIn("State a date or deadline only when you have checked it.", voice.job_guide())

    def test_the_on_device_voice_gets_the_same_rule(self):
        p = " ".join(ondevice.prompt("default", "You are Zero.").split())
        self.assertIn("Never guess a date, a day or a deadline.", p)
        self.assertIn("Never make up facts, dates, deadlines, times, prices or names.", p)
        self.assertIn("up to about four sentences", p)


if __name__ == "__main__":
    unittest.main()
