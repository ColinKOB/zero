"""Zip-to-Zip threads (Oct 12): the plain coworker version of team messages (teamvoice.py) and the answering Zip's
sign-ins and actions marked as steps of the answer (feed.step_of). Haiku is always a stand-in here; nothing reaches
Claude.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien in a copy:
~/taffy/.venv/bin/python -m unittest discover -b -s tests
"""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, facts, store, team, teamvoice  # noqa: E402

ORIGINAL_ASK = ("Morning brief prep: check elearning.lakeview.test for assignments due today or tomorrow and confirm "
                "whether last night's submission went through. Reply with status only.")
PLAIN_ASK = "Hey School, can you check eLearning for anything due today or tomorrow, and whether last night's work went in?"


class Faithful(unittest.TestCase):
    def test_the_example_passes(self):
        self.assertIsNone(teamvoice.faithful(ORIGINAL_ASK, PLAIN_ASK, ("Zero", "School", "Sam"), "ask"))

    def test_refusals(self):
        names = ("Zero", "School", "Sam")
        cases = {
            "Hey School, can you check the 3 assignments due today?": "a number not in the original",
            "Hey School, can you check Canvas for anything due today?": '"Canvas" is not in the original',
            "Hey School, can you check eLearning — anything due today?": "an em dash",
            "Hey School, anything due today? \U0001F600": "an emoji",
            "": "empty",
        }
        for plain, why in cases.items():
            self.assertEqual(teamvoice.faithful(ORIGINAL_ASK, plain, names, "ask"), why, plain)

    def test_a_question_stays_a_question(self):
        self.assertEqual(teamvoice.faithful("Is anything due on elearning today?", "Hey School, check eLearning today.",
                                            ("Zero", "School"), "ask"), "the question was left out")

    def test_an_answer_may_not_ask_back(self):
        why = teamvoice.faithful("Nothing is due tomorrow. Last night's essay posted at 9:40.",
                                 "Nothing is due tomorrow. Want me to check again?", ("School", "Zero"), "answer")
        self.assertEqual(why, "a question the message does not ask")
        self.assertIsNone(teamvoice.faithful("Nothing is due tomorrow. Last night's essay posted at 9:40.",
                                             "Nothing is due tomorrow, and last night's essay went in at 9:40.",
                                             ("School", "Zero"), "answer"))


class Rewrite(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def test_good_rewrite(self):
        prompts = []

        async def ask(prompt, timeout, system):
            prompts.append(prompt)
            return PLAIN_ASK
        out = self.run_(teamvoice.rewrite("ask", "Zero", "School", "Sam", ORIGINAL_ASK, ask))
        self.assertEqual(out, PLAIN_ASK)
        self.assertEqual(len(prompts), 1)
        self.assertIn("Hey School,", prompts[0])
        self.assertIn(ORIGINAL_ASK, prompts[0])
        self.assertNotIn("—", prompts[0])

    def test_refused_twice_keeps_the_original(self):
        calls = []

        async def ask(prompt, timeout, system):
            calls.append(prompt)
            return "Hey School, can you check the 12 assignments in Canvas?"
        self.assertIsNone(self.run_(teamvoice.rewrite("ask", "Zero", "School", "Sam", ORIGINAL_ASK, ask)))
        self.assertEqual(len(calls), teamvoice.ATTEMPTS)
        self.assertIn("refused because it had", calls[1])

    def test_second_try_can_pass(self):
        answers = iter(["Hey School, can you check the 12 assignments?", PLAIN_ASK])

        async def ask(prompt, timeout, system):
            return next(answers)
        self.assertEqual(self.run_(teamvoice.rewrite("ask", "Zero", "School", "Sam", ORIGINAL_ASK, ask)), PLAIN_ASK)

    def test_haiku_failing_keeps_the_original(self):
        async def ask(prompt, timeout, system):
            raise TimeoutError()
        self.assertIsNone(self.run_(teamvoice.rewrite("answer", "School", "Zero", "Sam", "Nothing is due.", ask)))


def fresh():
    store._local.conn = None
    store._schema_ready = False
    team._ready = False
    facts.reset_for_tests()
    if config.HUB_DB.exists():
        config.HUB_DB.unlink()
    teamvoice._done.clear()


class Feed(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DB, dict(config.BOTS_BY_ID), teamvoice.ENABLED)
        config.HUB_DB = Path(tempfile.mkdtemp()) / "hub.db"
        for b in [config.Bot("default", "Zero", "Lead", "#F2711C", "sparkles"),
                  config.Bot("school", "School", "School", "#3B82F6", "book")]:
            config.BOTS_BY_ID[b.id] = b
        fresh()

    def tearDown(self):
        config.HUB_DB = self.saved[0]
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(self.saved[1])
        teamvoice.ENABLED = self.saved[2]
        fresh()

    def body(self, fid):
        return json.loads(store.db().execute("SELECT body FROM feed WHERE id=?", (fid,)).fetchone()["body"])

    def test_plain_version_is_kept_beside_the_original(self):
        fid = team._feed("school", "ask", "default", "school", ORIGINAL_ASK, msg=7)

        async def ask(prompt, timeout, system):
            return PLAIN_ASK
        self.assertEqual(asyncio.run(teamvoice.apply(fid, ask)), PLAIN_ASK)
        b = self.body(fid)
        self.assertEqual(b["text"], ORIGINAL_ASK)
        self.assertEqual(b["plain"], PLAIN_ASK)
        self.assertEqual(b["msg"], 7)

    def test_one_rewrite_for_both_chats(self):
        answer = "Nothing is due tomorrow. Last night's essay posted at 9:40."
        a = team._feed("school", "answer", "school", "default", answer, msg=3)
        r = team._feed("default", "reply", "school", "default", answer, "the question", msg=3)
        calls = []

        async def ask(prompt, timeout, system):
            calls.append(prompt)
            return "Nothing is due tomorrow, and last night's essay went in at 9:40."

        async def both():
            await asyncio.gather(teamvoice.apply(a, ask), teamvoice.apply(r, ask))
        asyncio.run(both())
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.body(a)["plain"], self.body(r)["plain"])

    def test_notes_and_failures_are_left_alone(self):
        n = team._feed("school", "note", "school", "school", "Essay due Friday.")
        f = team._feed("school", "answer", "school", "default", "Nothing is due.", msg=4)

        async def ask(prompt, timeout, system):
            raise RuntimeError("signed out")
        asyncio.run(teamvoice.apply(n, ask))
        asyncio.run(teamvoice.apply(f, ask))
        self.assertNotIn("plain", self.body(n))
        self.assertNotIn("plain", self.body(f))

    def test_nothing_is_scheduled_outside_the_live_hub(self):
        teamvoice.ENABLED = False
        called = []

        async def go():
            teamvoice.schedule(team._feed("school", "ask", "default", "school", ORIGINAL_ASK, msg=1),
                               ask=lambda *a, **k: called.append(1))
            await asyncio.sleep(0)
        asyncio.run(go())
        self.assertEqual(called, [])

    def test_steps_point_at_the_question(self):
        now = int(time.time())
        with store._lock:
            mid = team._db().execute("INSERT INTO team_msgs (kind, from_bot, to_bot, text, created_at) VALUES "
                                     "('ask','default','school','q',?)", (now - 30,)).lastrowid
            team._db().commit()
        self.assertEqual(team.answering("school", now), mid)
        self.assertIsNone(team.answering("school", now - 3600))
        self.assertIsNone(team.answering("default", now))
        fid = store.add_feed("signin", "school", None, "Signed in to elearning.lakeview.test", "Answering Zero's question",
                             step_of=mid)
        row = [r for r in store.activity_for("school") if r["id"] == fid][0]
        self.assertEqual(row["step_of"], mid)
        plain = store.add_feed("signin", "school", None, "Signed in to x.com", "Scheduled: Daily")
        self.assertIsNone([r for r in store.activity_for("school") if r["id"] == plain][0]["step_of"])



class StepLabels(unittest.TestCase):
    def setUp(self):
        self.saved = config.HUB_DB
        config.HUB_DB = Path(tempfile.mkdtemp()) / "hub.db"
        fresh()
        teamvoice._labels.clear()

    def tearDown(self):
        config.HUB_DB = self.saved
        fresh()

    def test_label_check(self):
        raw = "Signed in to elearning.lakeview.test to answer Zero"
        self.assertIsNone(teamvoice.label_faithful(raw, "Signed in to eLearning"))
        self.assertEqual(teamvoice.label_faithful(raw, "Logged in to eLearning"), '"Logged" is not in the original')
        self.assertEqual(teamvoice.label_faithful(raw, "Signed in to Canvas"), '"Canvas" is not in the original')
        self.assertEqual(teamvoice.label_faithful(raw, "Signed in to the lakeview eLearning site now"), "too long")
        self.assertEqual(teamvoice.label_faithful(raw, ""), "empty")

    def test_label_saved_beside_the_title(self):
        fid = store.add_feed("signin", "school", None, "Signed in to elearning.lakeview.test", "Answering Zero's question",
                             step_of=5)
        act = store.add_feed("action", "school", None, "Done", "Signed in to elearning.lakeview.test to answer Zero",
                             step_of=5)
        calls = []

        async def ask(prompt, timeout, system):
            calls.append(prompt)
            return "Signed in to eLearning."
        self.assertEqual(asyncio.run(teamvoice.apply_step(fid, ask)), "Signed in to eLearning")
        self.assertEqual(asyncio.run(teamvoice.apply_step(act, ask)), "Signed in to eLearning")
        rows = {r["id"]: r for r in store.activity_for("school")}
        self.assertEqual(rows[fid]["title"], "Signed in to elearning.lakeview.test")
        self.assertEqual(rows[fid]["plain_title"], "Signed in to eLearning")
        self.assertEqual(rows[act]["plain_title"], "Signed in to eLearning")
        self.assertEqual(len(calls), 2)   # two different lines, each asked once

    def test_a_bad_label_keeps_the_raw_one(self):
        fid = store.add_feed("signin", "school", None, "Signed in to elearning.lakeview.test", "Answering Zero's question",
                             step_of=5)

        async def ask(prompt, timeout, system):
            return "Logged in to Canvas"
        self.assertIsNone(asyncio.run(teamvoice.apply_step(fid, ask)))
        self.assertIsNone([r for r in store.activity_for("school") if r["id"] == fid][0]["plain_title"])

    def test_steps_outside_a_thread_get_no_label(self):
        fid = store.add_feed("signin", "school", None, "Signed in to x.com", "Scheduled: Daily")

        async def ask(prompt, timeout, system):
            raise AssertionError("must not be called")
        self.assertIsNone(asyncio.run(teamvoice.apply_step(fid, ask)))


if __name__ == "__main__":
    unittest.main()
