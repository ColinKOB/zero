"""Fluid calls (docs/qa/2026-10-09-fluid-voice.md): what people said about Grok Bot and ChatGPT dots calls
(docs/research/2026-10-09-grokbot-dots-user-feedback.md, rows #16 to #37 and #87), turned into checks.

- An honest hello after a slow connection, with the person's name (#16 to #18, #87).
- Soft sounds for work started, still working and done, and never a "let me check" that dead-ends (#19, #23, #29).
- The key point first, details moved to the chat (#29, #35).
- A line that got no answer is noticed and recovered; a TV or a bystander is not chased (#26, #30, #37).
- One voice across calls and devices (#33, #34).
- "What are you working on?" gets a short spoken answer (#20).
- A goodbye ends the call.

Run: python3 -m unittest discover -s hub/tests, or on alien ~/taffy/.venv/bin/python -m unittest discover -s tests
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import board, calllog, config, ondevice, store, turns, voice, who  # noqa: E402
from test_ondevice_voice import Base  # noqa: E402


def chat_call(cid: str) -> voice.Call:
    """A ChatGPT-voice call as voice.start makes it, with its saved-call file."""
    call = voice.Call(id=cid, bot_id="default", voice="cove")
    calllog.begin(cid, at=call.started, bot="default", bot_name="Zero", voice="cove", engine=voice.ENGINE)
    return call


def events_of(call) -> list[dict]:
    out = []
    while not call.events.empty():
        out.append(call.events.get_nowait())
    return out


class FakeServer:
    """Stands in for Codex's app server: records every call."""

    def __init__(self, fail_speech: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail_speech = fail_speech

    async def call(self, method, params, timeout=40):
        self.calls.append((method, params))
        if method == "thread/realtime/appendSpeech" and self.fail_speech:
            raise RuntimeError("no session")
        return {}

    def said(self) -> list[str]:
        return [p["text"] for m, p in self.calls if m == "thread/realtime/appendSpeech"]


class Greeting(Base):
    def test_a_slow_connection_gets_an_honest_hello_with_the_name(self):
        first = who.first()
        self.assertEqual(voice.greeting(), f"Hey {first}, what's up?")
        late = voice.greeting(late=True)
        self.assertEqual(late, f"Sorry for the wait, {first}. What's up?")
        for text in (voice.greeting(), late):
            self.assertFalse(text.lower().startswith(("oh", "well", "so ")))
            self.assertNotIn("ring", text.lower())

    def test_greet_picks_the_hello_from_how_long_the_person_waited(self):
        fake = FakeServer()
        for waited, expect_late in ((None, False), (2500, False), (voice.LATE_GREETING_MS + 1, True)):
            call = chat_call(f"g{waited}")
            voice.calls[call.id] = call
            with mock.patch.object(voice.server, "call", fake.call):
                asyncio.run(voice.greet(call.id, waited_ms=waited))
            self.assertEqual(fake.said()[-1], voice.greeting(late=expect_late))
            line = next(l for l in self.lines(call.id) if l["event"] == "greeting")
            self.assertEqual(line.get("late"), True if expect_late else None)
            voice.calls.pop(call.id, None)

    def test_on_device_late_hello_goes_in_the_history(self):
        async def go():
            call = await ondevice.start("default", None)
            await voice.greet(call.id, waited_ms=20000)
            history = ondevice.history(call.local)
            await voice.stop(call.id)
            return call, history
        call, history = asyncio.run(go())
        self.assertEqual(history[1], ("assistant", voice.greeting(late=True)))
        self.assertEqual(call.transcript[0], ("You", voice.greeting(late=True)))

    def test_engines_carry_both_greetings_and_the_home_voices(self):
        e = voice.engines()
        self.assertEqual(e["greeting"], voice.greeting())
        self.assertEqual(e["greeting_late"], voice.greeting(late=True))
        self.assertIsNone(e["voice"])
        store.set_setting(voice.VOICE_SETTING, "maple")
        store.set_setting(ondevice.VOICE_SETTING, "alba")
        e = voice.engines()
        self.assertEqual((e["voice"], e["on_device_voice"]), ("maple", "alba"))


class Brief(unittest.TestCase):
    def test_short_answers_are_said_whole(self):
        self.assertEqual(voice.brief("Your cart is ready: 2 items, $14.10."), "Your cart is ready: 2 items, $14.10.")

    def test_a_three_sentence_result_is_said_in_full(self):
        """Oct 11 call review: results are said in full, up to about four sentences, not sent to the chat."""
        text = ("Tomorrow in Boston is sunny with a high of 64. Saturday night gets cloudy, low near 48. Sunday has "
                "a 40 percent chance of rain after 2 p.m. and winds from the northwest at 10 to 15 miles an hour.")
        self.assertEqual(voice.brief(text), text)

    def test_a_short_list_is_said_whole_without_markdown(self):
        text = "Found three flights:\n1. **JetBlue** 7:40 a.m., $189\n2. Delta 9:15 a.m., $204\n3. United noon, $231"
        out = voice.brief(text)
        self.assertNotIn("*", out)
        self.assertNotIn(voice.DETAILS_IN_CHAT, out)
        self.assertIn("United", out)

    def test_questions_are_said_whole_and_progress_has_no_chat_line(self):
        q = "Question: want the $89 one with free delivery tomorrow before noon at your door?"
        self.assertEqual(voice.brief(q), "Want the $89 one with free delivery tomorrow before noon at your door?")
        note = "Added Coke. Added ice. Added chips. Checking the delivery windows now for tomorrow morning."
        self.assertNotIn(voice.DETAILS_IN_CHAT, voice.brief(note, chat_line=False))

    def test_a_question_the_work_ends_on_is_asked_on_the_call(self):
        """Oct 10, Clients on a call: the result ended with a question, and the call said only the first two
        sentences and "the details are in our chat"."""
        text = ("The copy on your Mac isn't newer for the Halloween part. It's in your Projects folder, it was last "
                "changed in August, and it has no Halloween files. I searched the rest of your Mac.\n\nMy plan is to "
                "start from what's on GitHub.\n\nQuestion: Should I include the scraper-blocking change from your "
                "Mac when I update her site?")
        out = voice.brief(text)
        self.assertTrue(out.endswith("My plan is to start from what's on GitHub. Should I include the "
                                     "scraper-blocking change from your Mac when I update her site?"))
        self.assertTrue(out.startswith("The copy on your Mac isn't newer for the Halloween part."))
        self.assertNotIn(voice.DETAILS_IN_CHAT, out)
        self.assertNotIn("Question", out)

    def test_a_runaway_sentence_is_cut_at_a_word(self):
        out = voice.brief("word " * 300)
        self.assertLessEqual(len(out), voice.BRIEF_CHARS + 1)
        self.assertTrue(out.endswith("word."))


class FakeTurn:
    """A bot turn the test drives: events are pushed by hand."""

    def __init__(self):
        self.real = turns.Turn("default", "app")

    async def push(self, ev):
        await self.real.push(ev)


class HandOff(Base):
    def setUp(self):
        super().setUp()
        voice.ask_bot, voice.update_job = self.real[6], self.real[7]
        self.fake = FakeServer()
        self.patches = [mock.patch.object(voice.server, "call", self.fake.call),
                        mock.patch.object(voice, "WORKING_FIRST_SEC", 0.2),
                        mock.patch.object(voice, "WORKING_EVERY_SEC", 0.3),
                        mock.patch.object(voice, "CUE_LEAD_SEC", 0.05)]
        for p in self.patches:
            p.start()
        self.after = []

        async def after(call, text, title=""):
            self.after.append((title, text))
        self.patches.append(mock.patch.object(voice, "_after_hang_up", after))
        self.patches[-1].start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def start_turn(self, script):
        """turns.start returns a turn whose events follow `script` [(delay, event), ...]."""
        made = []

        def start(bot_id, text, consent_text=None):
            t = turns.Turn(bot_id, "app")
            made.append((t, text))

            async def run():
                for delay, ev in script:
                    await asyncio.sleep(delay)
                    await t.push(ev)
            asyncio.get_running_loop().create_task(run())
            return t
        return mock.patch.object(turns, "start", start), made

    def test_a_quick_answer_sounds_started_then_done_and_comes_back_brief(self):
        reply = ("Theo wants the slides by Friday. He also asked about the budget numbers for Q3 and whether "
                 "Dana can join the review. He wants a call next week about the venue and the catering order.")
        patch, made = self.start_turn([(0.05, {"type": "reply", "text": reply}), (0.02, {"type": "done"})])

        async def go():
            call = chat_call("q1")
            voice.calls[call.id] = call
            with patch:
                out = await voice.ask_bot(call, "Check the email from Theo")
            evs = events_of(call)
            call.closed = True
            return out, evs
        out, evs = asyncio.run(go())
        cues = [(e["text"], e["id"]) for e in evs if e["type"] == "cue"]
        self.assertEqual(cues, [("started", 1), ("done", 1)])
        self.assertEqual(out, reply)
        self.assertIn(voice.CALL_REQUEST_MARKER, made[0][1])

    def test_no_working_sound_after_a_tasks_done_sound(self):
        """Night review: the speaker plays a task's "done" sound, waits CUE_LEAD_SEC and then says the result, and
        the result is off the speech queue in that gap. A "working" sound for the same task could slip in there
        (before Hermes's final "done" event marked the turn done), so the person heard done, then working. This
        was also why the long-job test above failed now and then."""
        async def go():
            call = chat_call("cue1")
            await voice.cue(call, "started", 1)
            await voice.cue(call, "working", 1)
            await voice.cue(call, "done", 1)
            await voice.cue(call, "working", 1)
            await voice.cue(call, "working", 2)
            evs = events_of(call)
            call.closed = True
            return [(e["text"], e["id"]) for e in evs if e["type"] == "cue"]
        self.assertEqual(asyncio.run(go()), [("started", 1), ("working", 1), ("done", 1), ("working", 2)])

    def test_a_long_job_sounds_working_then_done_before_its_result_is_said(self):
        patch, _ = self.start_turn([(1.6, {"type": "reply", "text": "Your cart is ready: 2 items, $14.10."}),
                                    (0.05, {"type": "done"})])

        async def go():
            call = chat_call("j1")
            voice.calls[call.id] = call
            speaker = asyncio.create_task(voice._speaker(call))
            with patch, mock.patch.object(voice, "QUICK_ANSWER_SEC", 0.5):
                out = await voice.ask_bot(call, "Build my Walmart cart")
                await asyncio.sleep(3.5)
            evs = events_of(call)
            call.closed = True
            call.speech_ready.set()
            await speaker
            return out, evs
        out, evs = asyncio.run(go())
        self.assertIn("still working", out)
        kinds = [e["text"] for e in evs if e["type"] == "cue"]
        self.assertEqual(kinds[0], "started")
        self.assertIn("working", kinds)
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(self.fake.said(), ["Your cart is ready: 2 items, $14.10."])

    def test_a_job_that_ends_without_a_reply_still_says_so(self):
        patch, _ = self.start_turn([(0.8, {"type": "error", "text": "boom"}), (0.05, {"type": "done"})])

        async def go():
            call = chat_call("j2")
            voice.calls[call.id] = call
            speaker = asyncio.create_task(voice._speaker(call))
            with patch, mock.patch.object(voice, "QUICK_ANSWER_SEC", 0.3):
                await voice.ask_bot(call, "Find my receipt")
                await asyncio.sleep(2.5)
            call.closed = True
            call.speech_ready.set()
            await speaker
        asyncio.run(go())
        self.assertEqual(self.fake.said(), ["That one hit a problem and didn't finish. Want me to try again?"])

    def test_a_job_that_fails_after_hang_up_lands_in_the_chat(self):
        patch, _ = self.start_turn([(0.8, {"type": "done"})])

        async def go():
            call = chat_call("j3")
            voice.calls[call.id] = call
            with patch, mock.patch.object(voice, "QUICK_ANSWER_SEC", 0.3):
                await voice.ask_bot(call, "Find my receipt")
                call.closed = True
                await asyncio.sleep(1.0)
        asyncio.run(go())
        self.assertEqual(len(self.after), 1)
        self.assertIn("couldn't finish", self.after[0][1])

    def test_words_the_voice_could_not_take_go_to_the_chat(self):
        self.fake.fail_speech = True

        async def go():
            call = chat_call("s1")
            speaker = asyncio.create_task(voice._speaker(call))
            voice.say(call, "Your cart is ready.", must=True, final=True)
            await asyncio.sleep(3.0)
            call.closed = True
            call.speech_ready.set()
            await speaker
        asyncio.run(go())
        self.assertEqual(self.after, [("From your call", "Your cart is ready.")])

    def test_no_working_sound_while_the_person_talks(self):
        async def go():
            call = chat_call("w1")
            turn = turns.Turn("default", "app")
            now = time.time()
            call.colin_talking, call.talking_at, call.last_user_delta = True, now, now
            task = asyncio.create_task(voice._working_cues(call, turn, 1))
            await asyncio.sleep(0.8)
            quiet = events_of(call)
            call.colin_talking = False
            call.last_user_sound = call.last_voice_sound = time.time() - 10
            await asyncio.sleep(0.6)
            later = events_of(call)
            call.closed = True
            await task
            return quiet, later
        quiet, later = asyncio.run(go())
        self.assertEqual(quiet, [])
        self.assertIn({"type": "cue", "text": "working", "id": 1}, later)

    def test_no_sounds_while_on_hold(self):
        async def go():
            call = chat_call("h1")
            call.away = True
            await voice.cue(call, "started", 1)
            return events_of(call)
        self.assertEqual(asyncio.run(go()), [])


class MissedTurn(Base):
    def setUp(self):
        super().setUp()
        self.fake = FakeServer()
        self.patches = [mock.patch.object(voice.server, "call", self.fake.call),
                        mock.patch.object(voice, "MISSED_TURN_SEC", 0.3)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def run_line(self, text, heard=True, answer=None, wait=1.0):
        async def go():
            call = chat_call("m1")
            voice.calls[call.id] = call
            if heard:
                voice.set_talking(call.id, True)
                voice.set_talking(call.id, False)
            call.last_user_delta = call.last_user_sound = time.time() - 5
            await voice.on_event(call, "thread/realtime/transcript/done", {"role": "user", "text": text})
            if answer:
                await asyncio.sleep(0.1)
                await voice.on_event(call, "thread/realtime/transcript/done", {"role": "assistant", "text": answer})
            await asyncio.sleep(wait)
            call.closed = True
            return call
        return asyncio.run(go())

    def test_an_unanswered_line_is_said_again_then_asked_for(self):
        call = self.run_line("Can you check the weather for Boston tomorrow")
        methods = [(m, p.get("text"), p.get("role")) for m, p in self.fake.calls]
        self.assertEqual(methods[0], ("thread/realtime/appendText", "Can you check the weather for Boston tomorrow",
                                      "user"))
        self.assertEqual(methods[1], ("thread/realtime/appendSpeech", "Sorry, I missed that. Can you say it again?",
                                      None))
        stages = [l["stage"] for l in self.lines(call.id) if l["event"] == "missed_turn"]
        self.assertEqual(stages, ["again", "ask"])

    def test_an_answered_line_is_left_alone(self):
        self.run_line("What time is it", answer="It's quarter to three.")
        self.assertEqual(self.fake.calls, [])

    def test_words_the_phone_never_heard_the_person_say_are_not_chased(self):
        self.run_line("and in other news the markets fell today", heard=False)
        self.assertEqual(self.fake.calls, [])

    def test_a_hand_off_counts_as_an_answer(self):
        async def go():
            call = chat_call("m2")
            voice.set_talking(call.id, True)
            call.mic_heard_at = time.time()
            await voice.on_event(call, "thread/realtime/transcript/done", {"role": "user", "text": "Check my email"})
            call.tools_running = 1
            await asyncio.sleep(1.0)
            call.closed = True
        asyncio.run(go())
        self.assertEqual(self.fake.calls, [])


class Goodbye(Base):
    def test_what_counts_as_goodbye(self):
        for line in ("Okay, bye!", "Thanks, talk to you later.", "That's all for now, thanks.", "Gotta go. Bye.",
                     "Good night.", "Alright, I'll let you go."):
            self.assertTrue(voice.is_goodbye(line), line)
        for line in ("Can you say bye to Sam for me?", "Remind me later about the dentist", "Bye the way, what's the "
                     "plan for tomorrow with the movers and the storage unit and the keys?", ""):
            self.assertFalse(voice.is_goodbye(line), line)
        self.assertTrue(voice.is_farewell("Bye, talk soon!"))
        self.assertTrue(voice.is_farewell("Take care. I'll put the cart in our chat when it's done."))
        self.assertFalse(voice.is_farewell("Sure, I'll check that now."))

    def chatgpt_call(self, lines, tool_between=False):
        async def go():
            call = chat_call("b1")
            voice.calls[call.id] = call
            for role, text in lines:
                if tool_between and role == "assistant":
                    call.last_tool_at = time.time()
                await voice.on_event(call, "thread/realtime/transcript/done", {"role": role, "text": text})
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)
            call.closed = True
            return call, events_of(call)
        with mock.patch.object(voice, "MISSED_TURN_SEC", 30):
            return asyncio.run(go())

    def test_goodbye_said_back_tells_the_phone_to_end_the_call(self):
        call, evs = self.chatgpt_call([("user", "Okay, thanks. Bye!"), ("assistant", "Bye, talk soon!")])
        self.assertIn({"type": "hangup"}, evs)
        self.assertIn("goodbye", [l["event"] for l in self.lines(call.id)])

    def test_no_hang_up_when_the_voice_carries_on(self):
        _, evs = self.chatgpt_call([("user", "That's it for now"), ("assistant", "Sure. Want me to send the list?")])
        self.assertNotIn({"type": "hangup"}, evs)

    def test_no_hang_up_when_a_hand_off_started_after_the_goodbye(self):
        _, evs = self.chatgpt_call([("user", "Thanks, bye"), ("assistant", "Bye! See you.")], tool_between=True)
        self.assertNotIn({"type": "hangup"}, evs)

    def test_on_device_goodbye_ends_after_the_reply_is_said(self):
        async def go():
            call = await ondevice.start("default", None)
            await voice.greet(call.id)
            await self.collect(ondevice.turn(call, 1, text="Okay thanks, bye"))
            await self.settle(0.2)
            ondevice.said(call, 1, "Bye, talk soon!")
            await self.settle(0.6)
            evs = events_of(call)
            await voice.stop(call.id)
            return evs
        evs = asyncio.run(go())
        self.assertIn({"type": "hangup"}, evs)


class VoiceIdentity(Base):
    def test_the_last_voice_chosen_on_any_device_is_the_homes(self):
        pick = lambda given, chosen: voice.pick_voice(given, chosen, voice.VOICE_SETTING, voice.VOICES,
                                                      voice.DEFAULT_VOICE)
        self.assertEqual(pick("cove", False), "cove")
        self.assertEqual(pick("maple", True), "maple")
        self.assertEqual(store.get_setting(voice.VOICE_SETTING), "maple")
        self.assertEqual(pick("cove", False), "maple")
        self.assertEqual(pick("cove", None), "cove")
        self.assertEqual(store.get_setting(voice.VOICE_SETTING), "maple")
        self.assertEqual(pick("nonsense", True), "maple")
        self.assertEqual(pick(None, None), "maple")

    def test_on_device_calls_keep_the_homes_voice(self):
        async def go():
            a = await ondevice.start("default", "alba", "chosen", chosen=True)
            b = await ondevice.start("default", "george", "signed_out", chosen=False)
            voices = (a.voice, b.voice)
            await voice.stop(a.id)
            await voice.stop(b.id)
            return voices
        self.assertEqual(asyncio.run(go()), ("alba", "alba"))

    def test_a_chatgpt_call_starts_in_the_homes_voice(self):
        store.set_setting(voice.VOICE_SETTING, "juniper")
        seen = {}

        async def call(method, params, timeout=40):
            if method == "thread/start":
                seen["tools"] = [t["name"] for t in params["dynamicTools"]]
                return {"thread": {"id": "v1"}}
            if method == "thread/realtime/start":
                seen["voice"] = params["voice"]
                asyncio.get_running_loop().call_soon(voice.calls["v1"].answer.set_result, "v=0 answer")
            return {}

        async def go():
            with mock.patch.object(voice.server, "call", call):
                c, answer = await voice.start("default", "v=0\r\n", "cove", chosen=False)
                c.closed = True
                voice.calls.pop(c.id, None)
                return answer
        self.assertEqual(asyncio.run(go()), "v=0 answer")
        self.assertEqual(seen["voice"], "juniper")
        self.assertEqual(seen["tools"], ["ask_bot", "update_job", "work_status"])


class WorkStatus(Base):
    def status(self, groups, busy=(), job=None):
        call = chat_call("st1")
        if job:
            call.job, call.job_task = turns.Turn("default", "app"), job
        with mock.patch.object(board, "grouped", lambda: groups), mock.patch.object(turns, "busy_bots", lambda: list(busy)):
            return call, voice.work_status(call)

    def test_nothing_running(self):
        _, text = self.status({"working": [], "needs_you": [], "done": []})
        self.assertEqual(text, "Nothing is running right now, and nothing is waiting on you.")

    def test_a_short_spoken_summary_of_the_queue(self):
        now = time.time()
        groups = {"working": [{"title": "Build the Walmart cart", "status": "running"},
                              {"title": "Find a dentist near Lakeview", "status": "running"}],
                  "needs_you": [{"title": "Sign in to eLearning", "status": "blocked"}],
                  "done": [{"title": "Weather for Boston", "completed_at": now - 600}]}
        _, text = self.status(groups, job=None)
        self.assertEqual(text, "2 things are in progress: Build the Walmart cart and Find a dentist near Lakeview. "
                               "One thing is waiting on you: Sign in to eLearning. One finished today.")
        self.assertLessEqual(len(text.split(". ")), 4)

    def test_a_long_queue_goes_to_the_chat(self):
        groups = {"working": [{"title": f"Task {i}", "status": "running"} for i in range(5)], "needs_you": [],
                  "done": []}
        call, text = self.status(groups, job="the grocery order")
        self.assertTrue(text.startswith("On this call I'm still on the grocery order."))
        self.assertIn("5 things are in progress: Task 0 and Task 1, and 3 more.", text)
        self.assertTrue(text.endswith(voice.DETAILS_IN_CHAT))
        row = store.db().execute("SELECT title, body FROM feed WHERE kind='checkin' ORDER BY id DESC").fetchone()
        self.assertEqual(row["title"], "Where things stand")
        self.assertIn("In progress: Task 4", row["body"])

    def test_on_device_hand_off_says_the_status(self):
        said = []

        async def go():
            call = await ondevice.start("default", None)
            with mock.patch.object(voice, "work_status", lambda c: "Nothing is running right now."), \
                    mock.patch.object(voice, "say", lambda c, text, must=False, **kw: said.append(text)):
                t = ondevice.Turn(n=1, kind="words", text="What are you working on?", speculative=False)
                t.handoff = ("work_status", "What is in progress.")
                await ondevice._handoff(call, t)
            await voice.stop(call.id)
        asyncio.run(go())
        self.assertEqual(said, ["Nothing is running right now."])


class Prompts(Base):
    def test_the_voice_is_told_the_fluid_rules(self):
        p = voice.voice_prompt("default", "You are Zero.")
        first = who.first()
        for want in ("one or two short sentences, the key point first", "I put the details in our chat",
                     f"You know {first}'s name", "I don't have that", "unless you hand it off in that same turn",
                     "If you missed it, say so plainly", "sounds like a TV", "goodbye", "When {0} starts talking, "
                     "stop and listen".format(first), "a soft sound tells"):
            self.assertIn(want, p)
        for keep in ("Never ask an either-or question", "Never join two questions with a comma", "Oh, man",
                     'If {0} says "um" or "uh"'.format(first)):
            self.assertIn(keep, p)
        self.assertNotIn("one to three sentences", p)

    def test_the_relay_knows_the_status_tool_and_keeps_the_chat_line(self):
        r = voice.relay_instructions()
        self.assertIn("work_status", r)
        self.assertIn("details are in the chat", r)
        self.assertIn("answer in its first sentence", voice.job_guide())

    def test_the_on_device_voice_gets_the_same_rules(self):
        p = ondevice.prompt("default", "You are Zero.")
        for want in ("one or two short sentences, the key point first", "call work_status", "write nothing at all",
                     "I put the details in our chat", "without calling a tool in that same reply"):
            self.assertIn(want, p)


class Routes(Base):
    def setUp(self):
        super().setUp()
        from test_onboarding import _load_app
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.app.app.dependency_overrides.clear()
        self.client = TestClient(self.app.app)
        self.client.__enter__()
        self.auth = {"Authorization": f"Bearer {store.redeem_pairing_code(store.new_pairing_code(), 'Test')}"}

    def tearDown(self):
        for call in list(voice.calls.values()):
            if call.engine == ondevice.ENGINE:
                ondevice.closed(call)
        voice.calls.clear()
        self.client.__exit__(None, None, None)
        super().tearDown()

    def test_ready_takes_the_wait_and_older_apps_still_work(self):
        r = self.client.post("/api/bots/default/voice/local", json={"voice": "alba", "voice_chosen": True},
                             headers=self.auth)
        body = r.json()
        self.assertEqual(body["greeting_late"], voice.greeting(late=True))
        self.assertEqual(store.get_setting(ondevice.VOICE_SETTING), "alba")
        cid = body["call_id"]
        self.assertEqual(self.client.post(f"/api/voice/{cid}/ready", json={"waited_ms": 9000},
                                          headers=self.auth).status_code, 200)
        self.assertEqual(voice.calls[cid].transcript[0], ("You", voice.greeting(late=True)))
        r = self.client.post("/api/bots/default/voice/local", json={"voice": "george"}, headers=self.auth)
        cid2 = r.json()["call_id"]
        self.assertEqual(r.json()["voice"], "george")
        self.assertEqual(self.client.post(f"/api/voice/{cid2}/ready", headers=self.auth).status_code, 200)
        self.assertEqual(voice.calls[cid2].transcript[0], ("You", voice.greeting()))


if __name__ == "__main__":
    unittest.main()
