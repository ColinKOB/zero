"""Short replies in the person's chat (docs/qa/2026-10-10-short-replies.md). Colin: "Long answers like this make this
feel less like a teammate and more like an llm." Research rows #7, #9, #13, #29, #75, #76 of
docs/research/2026-10-09-grokbot-dots-user-feedback.md: the key point first, short finals, details only when asked.

Haiku is always a fake here; nothing reaches a model.

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import asyncio
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import briefing, config, hermes, home, ondevice, shortreply, store, turns, watcher  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402
from test_proactivity import run  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BROKER = REPO / "alien" / "broker" / "zero_broker.py"
REAL_FOLLOW_UP = turns.follow_up

STATUS = """Good morning. One thing is due tonight: your POL 101 Unit 6 video, at 11:55 p.m. It's question 8, on the \
shortcuts people use to cope with voter ignorance.

The rest of the week from my notes:
- Form A is due tomorrow, Thursday, at 11:55 p.m.
- Form C, Form H and your final peer replies are due Sunday at 11:55 p.m., but Friday is better.
- Form C needs your signature and the date.

I couldn't check your calendar this morning because the connection came back unavailable, so I can't tell you \
what's scheduled for today. I'll get it working again.

Do you want me to pull up the reading and your notes on question 8 so they're ready when you record?"""

GOOD_SHORT = ("One thing is due tonight: your POL 101 Unit 6 video, at 11:55 p.m. Form A is due tomorrow at 11:55 p.m. "
              "Do you want me to pull up your notes on question 8?")

DRAFT = """Here's the draft for Priya:

Subject: Saturday order

Hi Priya,

Yes, we can do four dozen cinnamon rolls for Saturday. They'll be ready at 9 a.m. and the total is $96.

Thanks,
Sam"""

PRICES = """Your lenses are most likely Acuvue Oasys 1-Day. For a 90-pack, the lowest price I found was $93.49 a box at \
Costco Contacts online, but that needs a membership. Walmart Contacts is $111.86 a box. Your July charge of $193.98 \
works out to about $97 a box if it was two 90-packs. So Costco is about $7 cheaper for the same order.

When you call Lens Shop at 1-800-266-8228, ask them to match Costco's $93.49 per box. Delivery takes about 6 \
to 8 business days, so ordering this week gives you plenty of time."""


class FakeHaiku:
    """Answers in turn from `answers` (an Exception is raised), records every prompt."""

    def __init__(self, *answers, delay=0.0):
        self.answers = list(answers)
        self.prompts = []
        self.delay = delay

    async def __call__(self, prompt, timeout=90):
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        a = self.answers.pop(0) if self.answers else ""
        if isinstance(a, Exception):
            raise a
        return a


# MARK: When a reply is shortened

class Decision(unittest.TestCase):
    def test_a_long_status_is_shortened(self):
        self.assertTrue(shortreply.is_long(STATUS))
        self.assertTrue(shortreply.should_shorten("Morning. What's on my plate today?", STATUS))

    def test_a_short_answer_is_left_alone(self):
        reply = "Yes, that's the one. It's due Sunday at 11:55 p.m., though Friday is better."
        self.assertFalse(shortreply.is_long(reply))
        self.assertFalse(shortreply.should_shorten("Is that the intro video?", reply))

    def test_list_items_count_as_sentences(self):
        reply = "Here is where things stand:\n" + "\n".join(f"- Item {n} is done and filed." for n in range(1, 8))
        self.assertGreaterEqual(shortreply.measure(reply)[0], 8)
        self.assertTrue(shortreply.is_long(reply + "\n" + "More words here to pass the length line. " * 4))

    def test_asking_for_detail_a_list_steps_or_the_full_text_keeps_it_long(self):
        for words in ("Give me the full details on the contract.", "Can you list all my deadlines this week?",
                      "Walk me through how to file Form C.", "What are the steps?", "How do I reset the router?",
                      "What exactly did Bill Hart say?", "Paste the whole email.", "Show me the plan.",
                      "Draft a reply to Jordan.", "Write me a short bio.", "Explain why the build failed.",
                      "Tell me everything you found.", "Read me the note word for word."):
            self.assertTrue(shortreply.asked_for_detail(words), words)
            self.assertFalse(shortreply.should_shorten(words, STATUS), words)

    def test_status_questions_do_not_count_as_asking_for_detail(self):
        for words in ("Morning. What's on my plate today, and is anything due tonight?",
                      "What are you working on?", "Summarize what needs me this week.",
                      "What'd you find on the contacts?", "Did School finish?"):
            self.assertFalse(shortreply.asked_for_detail(words), words)

    def test_drafts_code_quotes_and_tables_are_never_shortened(self):
        self.assertTrue(shortreply.is_artifact(DRAFT))
        self.assertFalse(shortreply.should_shorten("Can you handle Priya's order?", DRAFT * 2))
        code = "Run this:\n```\nsudo systemctl restart taffy-hub\n```\n" + STATUS
        self.assertTrue(shortreply.is_artifact(code))
        quote = 'He wrote: "' + "I just released our last unit a day early to give you extra time. " * 3 + '" ' + STATUS
        self.assertTrue(shortreply.is_artifact(quote))
        self.assertTrue(shortreply.is_artifact("| Store | Price |\n|---|---|\n| Costco | $93.49 |\n" + STATUS))

    def test_a_closing_quote_and_the_next_opening_one_are_not_a_quote(self):
        reply = ('The demo pages have titles like "Blank thumbnail" and they all show the home page. ' * 3 +
                 'Don\'t click "Validate fix" yet.')
        self.assertFalse(shortreply.is_artifact(reply))


# MARK: Checking the short version

class Faithful(unittest.TestCase):
    def test_a_good_short_version_passes(self):
        self.assertIsNone(shortreply.faithful(STATUS, GOOD_SHORT, "What's on my plate today?"))

    def test_numbers_must_be_exact(self):
        bad = GOOD_SHORT.replace("11:55", "11:50", 1)
        self.assertIn("number", shortreply.faithful(STATUS, bad))
        self.assertIn("number", shortreply.faithful(PRICES, "Costco has it for $93.50 a box. Call 1-800-266-8228."))

    def test_names_must_come_from_the_reply(self):
        bad = "One thing is due tonight: your Unit 6 video for Professor Hart at 11:55 p.m. Want the notes?"
        self.assertIn("not in the original", shortreply.faithful(STATUS, bad))
        self.assertIn("Hart", shortreply.faithful(STATUS, bad.replace("Professor Hart", "your professor Hart")))

    def test_links_and_saved_files_are_kept_and_never_made_up(self):
        reply = STATUS.replace("Do you want", "The link is https://elearning.lakeview.test/mod/forum/view.php?id=911265\n\n"
                                              "Do you want")
        self.assertIn("link left out", shortreply.faithful(reply, GOOD_SHORT))
        with_link = GOOD_SHORT.replace("Form A", "The link is https://elearning.lakeview.test/mod/forum/view.php?id=911265 "
                                                 "and Form A")
        self.assertIsNone(shortreply.faithful(reply, with_link))
        self.assertIn("link not in", shortreply.faithful(STATUS, GOOD_SHORT + " https://example.com/x"))
        saved = STATUS + "\n\nThe full list is at /home/sam/taffy/work/inbox/week.md"
        self.assertIn("link left out", shortreply.faithful(saved, GOOD_SHORT))

    def test_the_question_to_the_person_stays_and_none_is_added(self):
        self.assertIn("question", shortreply.faithful(STATUS, GOOD_SHORT.replace("Do you want me to pull up your notes "
                                                                                 "on question 8?", "That's all.")))
        self.assertIn("question", shortreply.faithful(PRICES, "Costco is $93.49 a box. Should I call them?"))

    def test_an_action_never_moves_to_the_zip(self):
        """The replay on Colin's chat: "When you call Lens Shop" came back as "I can call Lens Shop", and
        "Builder will make the changes" as "I'll make the changes"."""
        reply = PRICES.replace("When you call", "Builder will compare them again tomorrow. When you call")
        self.assertIn("who does what", shortreply.faithful(reply, "Costco is $93.49 a box. I'll compare them again."))
        self.assertIn("who does what", shortreply.faithful(PRICES, "Costco is $93.49 a box, and I can call "
                                                                   "Lens Shop at 1-800-266-8228 to match it."))
        self.assertIsNone(shortreply.faithful(STATUS, "One thing is due tonight: your POL 101 Unit 6 video, at 11:55 "
                                                      "p.m. I couldn't check your calendar this morning. Do you want "
                                                      "your notes?"))

    def test_too_long_or_a_list_is_refused(self):
        self.assertIsNotNone(shortreply.faithful(STATUS, STATUS))
        self.assertIsNotNone(shortreply.faithful(STATUS, "- Unit 6 video at 11:55 p.m.\n- Form A tomorrow."))
        five = "Form A is due. " * 5
        self.assertIn("sentences", shortreply.faithful(STATUS, five))


# MARK: Shortening, with a fake Haiku

class Shorten(unittest.TestCase):
    def setUp(self):
        self.person = mock.patch("taffy_hub.onboarding.person_name", lambda full=True: "Sam")
        self.person.start()

    def tearDown(self):
        self.person.stop()

    def test_a_long_status_comes_back_short_with_the_facts_exact(self):
        haiku = FakeHaiku(GOOD_SHORT)
        short = run(shortreply.shorten("default", "What's on my plate today?", STATUS, ask=haiku))
        self.assertEqual(short, GOOD_SHORT)
        self.assertIn(STATUS, haiku.prompts[0])
        self.assertIn("What's on my plate today?", haiku.prompts[0])
        for n in ("11:55", "$93.49"):
            if n in short:
                self.assertIn(n, STATUS)

    def test_prices_keep_every_number_they_mention_exact(self):
        good = ("Costco Contacts has the lowest price, $93.49 a box for a 90-pack, but it needs a membership. When you "
                "call Lens Shop at 1-800-266-8228, ask them to match it.")
        self.assertEqual(run(shortreply.shorten("default", "Uhh it's the oasys one", PRICES, ask=FakeHaiku(good))), good)
        changed = good.replace("$93.49", "$93")
        shown = run(shortreply.shorten("default", "Uhh it's the oasys one", PRICES, ask=FakeHaiku(changed, changed)))
        self.assertNotIn("$93 ", shown)
        self.assertTrue(PRICES.startswith(shown))

    def test_a_rewrite_that_fails_the_checks_is_asked_for_once_more_with_the_reason(self):
        haiku = FakeHaiku(GOOD_SHORT.replace("11:55", "11:50", 1), GOOD_SHORT)
        self.assertEqual(run(shortreply.shorten("default", "What's up today?", STATUS, ask=haiku)), GOOD_SHORT)
        self.assertEqual(len(haiku.prompts), 2)
        self.assertIn("a number not in the original", haiku.prompts[1])

    def test_an_invented_question_is_cut_and_the_rest_kept(self):
        """Oct 11: four rewrites in a row ended with a question the reply never asked, and the person got the whole
        13-sentence reply. The paragraph holding it is cut instead."""
        reply = PRICES.replace("ordering this week gives you plenty of time.", "ordering this week is enough.")
        lead = ("Costco Contacts has the lowest price, $93.49 a box for a 90-pack, but it needs a membership. When you "
                "call Lens Shop at 1-800-266-8228, ask them to match it.")
        invented = lead + "\n\nDo you want me to call them for you? I can do it today."
        self.assertEqual(run(shortreply.shorten("default", "Uhh it's the oasys one", reply,
                                                ask=FakeHaiku(invented, invented))), lead)

    def test_a_failed_key_point_falls_back_to_the_replys_own_opening(self):
        bad = "Your Unit 6 video is due tonight at 11:55 p.m. Mr. Hart wants it in early."
        short = run(shortreply.shorten("default", "What's up today?", STATUS, ask=FakeHaiku(bad, bad)))
        self.assertTrue(STATUS.startswith(short.split("\n\n")[0]))
        self.assertIn("Do you want me to pull up the reading", short)
        self.assertIsNone(shortreply.faithful(STATUS, short, "What's up today?"))

    def test_the_opening_is_not_used_when_it_would_drop_a_link_or_is_only_a_lead_in(self):
        linked = STATUS + "\n\nThe reading is at https://example.edu/pol101/unit6."
        self.assertIsNone(shortreply.lead(linked))
        self.assertIsNone(shortreply.lead("Here's what I found:\n\n- one\n- two\n- three\n- four\n- five"))

    def test_failure_or_slowness_keeps_the_original(self):
        self.assertIsNone(run(shortreply.shorten("default", "What's up?", STATUS, ask=FakeHaiku(RuntimeError("down")))))
        slow = FakeHaiku(GOOD_SHORT, delay=0.5)
        self.assertIsNone(run(shortreply.shorten("default", "What's up?", STATUS, ask=slow, timeout=0.2)))
        self.assertIsNone(run(shortreply.shorten("default", "What's up?", STATUS, ask=FakeHaiku("", ""))))

    def test_a_requested_list_or_a_draft_never_reaches_haiku(self):
        haiku = FakeHaiku(GOOD_SHORT)
        self.assertIsNone(run(shortreply.shorten("default", "List all my deadlines this week.", STATUS, ask=haiku)))
        self.assertIsNone(run(shortreply.shorten("default", "Handle Priya's order.", DRAFT * 2, ask=haiku)))
        self.assertEqual(haiku.prompts, [])

    def test_an_attachment_line_stays_word_for_word(self):
        reply = STATUS + "\n\nMEDIA:/home/sam/taffy/work/inbox/week.png"
        haiku = FakeHaiku(GOOD_SHORT)
        short = run(shortreply.shorten("default", "What's on today?", reply, ask=haiku))
        self.assertEqual(short, GOOD_SHORT + "\n\nMEDIA:/home/sam/taffy/work/inbox/week.png")
        self.assertNotIn("MEDIA:", haiku.prompts[0])

    def test_no_em_dashes_reach_the_person(self):
        dashed = GOOD_SHORT.replace(": your", " — your")
        self.assertNotIn("—", run(shortreply.shorten("default", "Today?", STATUS, ask=FakeHaiku(dashed))))


# MARK: Haiku on the home's plan, in both kinds of home

class HomePlan(unittest.TestCase):
    """A home on its own plan and a home on a shared plan (Riley's: `plan.claude.command` is the broker's client)
    run the same command, which is the DIRECTSDK shape the broker allows, with thinking off and low effort, and
    never with an API key."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.record = self.tmp / "record.json"
        fake = self.tmp / "claude-client"
        fake.write_text(f"""#!{sys.executable}
import json, os, sys
a = sys.argv[1:]
def val(flag): return a[a.index(flag) + 1]
mcp = json.loads(val("--mcp-config"))["mcpServers"]["hermes"]["args"][1]
files = {{p: open(p).read() for p in (val("--system-prompt-file"), val("--settings"), mcp)}}
frame = json.loads(sys.stdin.readline())
json.dump({{"argv": a, "files": files, "prompt": frame["message"]["content"][0]["text"],
           "api_key": [k for k in os.environ if k.startswith("ANTHROPIC_")]}}, open({str(self.record)!r}, "w"))
print(json.dumps({{"type": "system", "subtype": "init"}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Short and kind."}}]}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False}}))
""")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.fake = fake

    def ask(self, shared: bool):
        real_get = home.get

        def get(key, default=None):
            if key == "plan.claude.command":
                return str(self.fake) if shared else default
            return real_get(key, default)
        own = mock.patch("taffy_hub.accounts.claude_bin", lambda: str(self.fake)) if not shared else mock.patch.dict({})
        with mock.patch.object(home, "get", get), own, \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-never-used"}):
            return run(shortreply.ask_haiku("Rewrite this.", timeout=10))

    def check_shape(self):
        rec = json.loads(self.record.read_text())
        self.assertEqual(rec["api_key"], [])
        self.assertEqual(rec["prompt"], "Rewrite this.")
        if not BROKER.exists():
            return rec
        spec = importlib.util.spec_from_file_location("zero_broker_short_test", BROKER)
        broker = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = broker
        try:
            spec.loader.exec_module(broker)
        finally:
            sys.modules.pop(spec.name, None)
        req = broker.parse_claude(rec["argv"], rec["files"], {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "600"})
        self.assertEqual(req.shape, "directsdk")
        self.assertEqual(req.model, briefing.MODEL)
        self.assertEqual(req.effort, "low")
        self.assertEqual(req.tools, [])
        body = json.loads(req.extra_body)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        return rec

    def test_a_home_on_its_own_plan(self):
        self.assertEqual(self.ask(shared=False), "Short and kind.")
        self.check_shape()

    def test_a_home_on_a_shared_plan_goes_through_the_broker_client(self):
        self.assertEqual(self.ask(shared=True), "Short and kind.")
        self.check_shape()


# MARK: The chat: held reply, short display, full original, pushes, the setting

class ChatTurns(AppCase):
    def setUp(self):
        super().setUp()
        self.haiku = FakeHaiku()
        self.patches = [mock.patch.object(shortreply, "ask_haiku", self.haiku),
                        mock.patch.object(briefing, "refresh_soon", lambda *a: None),
                        mock.patch("taffy_hub.onboarding.person_name", lambda full=True: "Sam")]
        for p in self.patches:
            p.start()
        shortreply._last_words.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def turn(self, reply, text):
        async def fake_stream(bot_id, text, **kw):
            yield {"type": "run", "id": "run_1"}
            yield {"type": "reply", "text": reply}
            yield {"type": "done"}

        async def go():
            with mock.patch.object(hermes, "stream_turn", fake_stream), \
                    mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")):
                t = turns.start("default", text)
                return [ev async for ev in t.follow() if ev is not None]
        return run(go())

    def history(self, msgs):
        async def fake_messages(bot_id, limit=200):
            return msgs
        with mock.patch.object(hermes, "messages", fake_messages):
            return self.client.get("/api/bots/default/messages").json()["messages"]

    def test_a_long_status_shows_short_and_the_history_keeps_the_original(self):
        self.haiku.answers = [GOOD_SHORT]
        out = self.turn(STATUS, "Morning. What's on my plate today?")
        reply = next(e for e in out if e["type"] == "reply")
        self.assertEqual(reply["text"], GOOD_SHORT)
        self.assertEqual(reply["full"], STATUS)
        self.assertIn("held_ms", reply)
        msgs = self.history([{"id": 1, "role": "user", "text": "Morning. What's on my plate today?", "ts": 1},
                             {"id": 2, "role": "assistant", "text": STATUS, "ts": 2}])
        self.assertEqual(msgs[1]["display"], GOOD_SHORT)
        self.assertEqual(msgs[1]["full"], STATUS)
        self.assertEqual(msgs[1]["text"], STATUS)
        self.assertNotIn("full", msgs[0])

    def test_a_requested_list_and_a_draft_are_shown_as_written(self):
        out = self.turn(STATUS, "List everything due this week.")
        self.assertEqual(next(e for e in out if e["type"] == "reply")["text"], STATUS)
        out = self.turn(DRAFT * 2, "Can you answer Priya?")
        self.assertEqual(next(e for e in out if e["type"] == "reply")["text"], DRAFT * 2)
        self.assertEqual(self.haiku.prompts, [])

    def test_when_haiku_fails_the_reply_goes_out_as_written(self):
        self.haiku.answers = [RuntimeError("signed out")]
        out = self.turn(STATUS, "What's on today?")
        reply = next(e for e in out if e["type"] == "reply")
        self.assertEqual(reply["text"], STATUS)
        self.assertNotIn("full", reply)

    def test_keep_replies_short_is_a_per_home_setting_on_by_default(self):
        self.assertTrue(self.client.get("/api/settings").json()["keep_replies_short"])
        self.haiku.answers = [GOOD_SHORT]
        self.turn(STATUS, "What's on today?")
        r = self.client.put("/api/settings", json={"keep_replies_short": False})
        self.assertFalse(r.json()["keep_replies_short"])
        out = self.turn(STATUS, "And now?")
        self.assertEqual(next(e for e in out if e["type"] == "reply")["text"], STATUS)
        self.assertEqual(len(self.haiku.prompts), 1)
        msgs = self.history([{"id": 2, "role": "assistant", "text": STATUS, "ts": 2}])
        self.assertEqual(msgs[0]["display"], STATUS)
        self.client.put("/api/settings", json={"keep_replies_short": True})
        self.assertEqual(self.history([{"id": 2, "role": "assistant", "text": STATUS, "ts": 2}])[0]["display"],
                         GOOD_SHORT)

    def test_the_notification_after_a_card_uses_the_short_version(self):
        self.haiku.answers = [GOOD_SHORT]
        pushed = []

        async def fake_internal(bot_id, text, continues=None):
            return STATUS

        async def fake_push(title, body, bot_id=None, **kw):
            pushed.append(body)
            return True
        shortreply._last_words["default"] = "What's on my plate today?"
        with mock.patch.object(turns, "run_internal", fake_internal), mock.patch.object(watcher, "push", fake_push):
            run(REAL_FOLLOW_UP("default", "[Hub note: allowed]", "sid-1", urgent=True))
        self.assertEqual(pushed, [GOOD_SHORT])
        self.assertIn("What's on my plate today?", self.haiku.prompts[0])

    def test_a_call_hand_off_is_never_shortened(self):
        async def fake_stream(bot_id, text, **kw):
            yield {"type": "reply", "text": STATUS}
            yield {"type": "done"}

        async def go():
            with mock.patch.object(hermes, "stream_turn", fake_stream), \
                    mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")):
                t = turns.start("default", "[From a voice call] Request from the call: what's on today?",
                                consent_text="what's on today?")
                return [ev async for ev in t.follow() if ev is not None]
        out = run(go())
        self.assertEqual(next(e for e in out if e["type"] == "reply")["text"], STATUS)
        self.assertEqual(self.haiku.prompts, [])


if __name__ == "__main__":
    unittest.main()


# MARK: The Zip's own short version (Fable's usage review, Oct 11)

OWN = ("One thing is due tonight: your POL 101 Unit 6 video, at 11:55 p.m. Form A is due tomorrow at 11:55 p.m.\n\n"
       "Do you want me to pull up the reading and your notes on question 8?")


class SplitShort(unittest.TestCase):
    def test_the_tag_comes_off_and_the_short_version_comes_out(self):
        from taffy_hub import chatter
        clean, short = chatter.split_short(f"<short>{OWN}</short>\n\n{STATUS}")
        self.assertEqual((clean, short), (STATUS, OWN))
        self.assertEqual(chatter.split_short(STATUS), (STATUS, None))

    def test_a_stray_or_unclosed_tag_never_shows(self):
        from taffy_hub import chatter
        self.assertNotIn("short>", chatter.without_hermes_notes("<short>Done. The file is saved."))
        self.assertNotIn("short>", chatter.without_hermes_notes(f"<short>{OWN}</short>\n\n{STATUS}"))


class OwnShort(ChatTurns):
    def test_a_faithful_own_short_version_shows_with_no_haiku_call(self):
        self.assertTrue(shortreply.offer("default", STATUS, OWN))
        out = self.turn(STATUS, "What's on my plate today?")
        reply = next(e for e in out if e["type"] == "reply")
        self.assertEqual(reply["text"], OWN)
        self.assertEqual(reply["full"], STATUS)
        self.assertEqual(self.haiku.prompts, [])

    def test_an_own_short_version_that_fails_the_checks_goes_to_haiku(self):
        bad = OWN.replace("11:55 p.m. Form", "11:30 p.m. Form")
        self.assertFalse(shortreply.offer("default", STATUS, bad))
        self.haiku.answers = [GOOD_SHORT]
        out = self.turn(STATUS, "What's on my plate today?")
        self.assertEqual(next(e for e in out if e["type"] == "reply")["text"], GOOD_SHORT)
        self.assertEqual(len(self.haiku.prompts), 1)

    def test_asking_for_detail_shows_the_full_reply_even_with_an_own_short_version(self):
        shortreply.offer("default", STATUS, OWN)
        out = self.turn(STATUS, "List everything due this week in detail.")
        self.assertEqual(next(e for e in out if e["type"] == "reply")["text"], STATUS)

    def test_the_chat_history_never_shows_the_tag_and_keeps_the_own_short_version(self):
        raw = [{"id": 1, "role": "user", "content": "What's on my plate today?", "timestamp": 1},
               {"id": 2, "role": "assistant", "content": f"<short>{OWN}</short>\n\n{STATUS}", "timestamp": 2}]
        shown = hermes._shown(raw, "default", "sid-1")
        self.assertEqual(shown[1]["text"], STATUS)
        self.assertEqual(shortreply.lookup("default", STATUS), OWN)


class ShortFirstRule(unittest.TestCase):
    def test_typed_chat_turns_ask_for_a_short_version_and_board_tasks_do_not(self):
        plugin = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "teammates.py"
        if not plugin.exists():
            self.skipTest("the plugin is not beside this copy of the hub")
        src = plugin.read_text()
        self.assertIn("<short>", src)
        self.assertIn('SHORT_FIRST if platform == "api_server"', src)
