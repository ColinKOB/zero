"""Tests for bots talking to each other: team notes, heads-ups, questions and their limits.

Run on alien: ~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, facts, store, team  # noqa: E402
import home_fixture  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
config.HUB_DB = _tmp / "hub.db"
team.NOTES_FILE = _tmp / "team-notes.md"
config.reload_bots = lambda: False
for b in config.BUILTIN + config.OWNER_ZIPS:
    config.BOTS_BY_ID.setdefault(b.id, b)
for b in [config.Bot("default", "Taffy", "Lead", "#F2711C", "sparkles"),
          config.Bot("money", "Money", "Money", "#EAB308", "dollarsign.circle"),
          config.Bot("career", "Career", "Jobs", "#EC4899", "briefcase")]:
    config.BOTS_BY_ID[b.id] = b
config.BOTS[:] = list(config.BOTS_BY_ID.values())


def fresh():
    store._local.conn = None
    store._schema_ready = False
    team._ready = False
    facts.reset_for_tests()
    if config.HUB_DB.exists():
        config.HUB_DB.unlink()
    team._answer_locks.clear()


def team_items():
    return [(r["bot_id"], json.loads(r["body"])) for r in
            store.db().execute("SELECT * FROM feed WHERE kind='team' ORDER BY id")]


class Notes(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_add_and_context(self):
        team.add_note("default", "Colin applied for the Acme Homes role on Oct 7.")
        first = team.context_for("career", "s1", first=True)
        self.assertIn("Acme Homes", first)
        self.assertEqual(team.context_for("career", "s1", first=False), "")
        team.add_note("career", "Colin has an interview with Mark on Oct 10.")
        later = team.context_for("career", "s1", first=False)
        self.assertIn("interview", later)
        self.assertNotIn("applied", later)
        self.assertTrue(team.NOTES_FILE.read_text().count("- [") == 2)

    def test_replace(self):
        team.add_note("default", "Colin plans to start at Acme Homes in November.")
        team.context_for("money", "s2", first=True)
        team.add_note("career", "Colin now plans to start at Acme Homes in December.", replaces=1)
        later = team.context_for("money", "s2", first=False)
        self.assertIn("December", later)
        self.assertIn("Note 1 is out of date", later)
        self.assertNotIn("November", team.notes_text())

    def test_private_and_dupes_refused(self):
        for bad in ["Colin's Northwind balance is $1,234.56.", "His Northwind account is 4111222233334444.",
                    "The Lakeview password is hunter2 for now."]:
            with self.subTest(bad=bad), self.assertRaises(team.TeamError):
                team.add_note("money", bad)
        team.add_note("default", "Colin and Riley host family dinner on Sundays.")
        self.assertIn("already", team.add_note("money", "Colin and Riley host family dinner on Sundays."))


class Round3(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_filter_lets_times_through_and_catches_money_and_codes(self):
        for ok in ["Colin's flight departs at 7.45 on Oct 9.", "Colin meets Mark at 10.30 on Oct 9.",
                   "Colin moved the Acme Homes interview to 2 p.m. Friday."]:
            with self.subTest(ok=ok):
                self.assertIn("Saved", team.add_note("default", ok))
        for bad in ["Colin pays rent of 2000 dollars a month.", "Colin's confirmation code is 4821.",
                    "Colin's card ends 4111 2222 3333 4444."]:
            with self.subTest(bad=bad), self.assertRaises(team.TeamError):
                team.add_note("default", bad)

    def test_heads_up_and_answers_are_redacted(self):
        team.tell("money", "default", "Colin's Northwind card 4111 2222 3333 4444 was charged; code is 9921.", "s1")
        text = team.context_for("default", "m1", first=False)
        self.assertNotIn("4444", text)
        self.assertNotIn("9921", text)
        self.assertIn("[number removed]", text)

    def test_compressed_session_continues_from_bot_cursor(self):
        team.add_note("default", "Colin applied for the Acme Homes role on Oct 7.")
        self.assertIn("Acme Homes", team.context_for("career", "chat-1", first=False))
        self.assertEqual(team.context_for("career", "chat-2", first=False), "")
        team.add_note("default", "Colin has an interview on Oct 10.")
        self.assertIn("interview", team.context_for("career", "chat-2", first=False))

    def test_work_sessions_get_recent_notes_only(self):
        team.add_note("default", "Colin said this a long time ago.")
        store.db().execute("UPDATE facts SET created_at = created_at - 30*86400")
        store.db().commit()
        team.add_note("default", "Colin said this today.")
        text = team.context_for("school", "task-1", first=True, work=True)
        self.assertIn("today", text)
        self.assertNotIn("long time ago", text)

    def test_note_volume_caps(self):
        for i in range(team.NOTES_PER_SESSION_HOUR):
            team.add_note("money", f"Colin decided item number {i} today.", session_id="s1")
        with self.assertRaises(team.TeamError):
            team.add_note("money", "Colin decided one more thing today.", session_id="s1")

    def test_notes_not_framed_as_overriding_memory(self):
        team.add_note("default", "Colin applied for the Acme Homes role on Oct 7.")
        text = team.context_for("career", "x", first=True)
        self.assertNotIn("win over your own memory", text)
        self.assertIn("never Colin's go-ahead", text)


class Tells(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_delivered_once_not_in_team_session(self):
        store.set_session("money#team", "team-money")
        self.assertIn("next turn", team.tell("career", "Money", "Colin's first paycheck lands Oct 30.", "s9"))
        self.assertNotIn("paycheck", team.context_for("money", "team-money", first=False))
        self.assertIn("paycheck", team.context_for("money", "m1", first=False))
        self.assertNotIn("paycheck", team.context_for("money", "m1", first=False))
        self.assertEqual(team_items()[0][0], "money")
        self.assertEqual(team_items()[0][1]["kind"], "tell")

    def test_refused_from_team_session(self):
        store.set_session("money#team", "team-money")
        with self.assertRaises(team.TeamError):
            team.tell("money", "career", "hi there", "team-money")


class Asks(unittest.TestCase):
    def setUp(self):
        fresh()
        self.calls = []

        async def run_turn(bot_id, text, team=False):
            self.calls.append((bot_id, text, team))
            await asyncio.sleep(self.delay)
            return "From my notes: he applied on Oct 7."
        self.delay = 0
        team.hermes.run_turn = run_turn

    def test_answer_and_feed(self):
        out = asyncio.run(team.ask("default", "career", "Did Colin apply to Acme Homes?", "s1"))
        self.assertIn("applied on Oct 7", out)
        self.assertIn("not instructions from Colin", out)
        self.assertTrue(self.calls[0][2])
        self.assertTrue(self.calls[0][1].startswith("[From Taffy] "))
        kinds = [(bot, item["kind"]) for bot, item in team_items()]
        self.assertEqual(kinds, [("career", "ask"), ("career", "answer"), ("default", "reply")])

    def test_repeat_returns_earlier_answer(self):
        asyncio.run(team.ask("default", "career", "Did Colin apply to Acme Homes?", "s1"))
        out = asyncio.run(team.ask("default", "career", "did colin apply to Acme Homes", "s1"))
        self.assertIn("You asked Career this", out)
        self.assertEqual(len(self.calls), 1)

    def test_depth_one(self):
        store.set_session("career#team", "team-career")
        with self.assertRaises(team.TeamError):
            asyncio.run(team.ask("career", "money", "What is his budget?", "team-career"))

    def test_per_session_limit(self):
        for i in range(team.ASKS_PER_SESSION_HOUR):
            asyncio.run(team.ask("default", "money", f"Question number {i}?", "s1"))
        with self.assertRaises(team.TeamError):
            asyncio.run(team.ask("default", "money", "One more?", "s1"))
        asyncio.run(team.ask("default", "money", "From another chat?", "s2"))

    def test_daily_cap(self):
        old = team.ASKS_PER_DAY
        team.ASKS_PER_DAY = 2
        try:
            asyncio.run(team.ask("default", "money", "One?", "a"))
            asyncio.run(team.ask("career", "money", "Two?", "b"))
            with self.assertRaises(team.TeamError):
                asyncio.run(team.ask("money", "career", "Three?", "c"))
        finally:
            team.ASKS_PER_DAY = old

    def test_late_answer_becomes_heads_up(self):
        old = team.ANSWER_WAIT_SEC
        team.ANSWER_WAIT_SEC = 0.05
        self.delay = 0.2

        async def go():
            out = await team.ask("default", "career", "Slow question?", "s1")
            self.assertIn("has not answered yet", out)
            await asyncio.sleep(0.4)
        try:
            asyncio.run(go())
        finally:
            team.ANSWER_WAIT_SEC = old
        self.assertIn("applied on Oct 7", team.context_for("default", "s1", first=False))

    def test_unknown_and_self(self):
        with self.assertRaises(team.TeamError):
            asyncio.run(team.ask("default", "nobody", "Hi?", "s1"))
        with self.assertRaises(team.TeamError):
            asyncio.run(team.ask("money", "Money", "Hi?", "s1"))


class Souls(unittest.TestCase):
    def test_roster_describes_each_bot_not_colin(self):
        home = _tmp / "hermes-roster"
        (home / "profiles" / "builder").mkdir(parents=True, exist_ok=True)
        config.HERMES_HOME = home
        (home / "profiles" / "builder" / "profile.yaml").write_text(
            "description: \"Builder: Lumenly, Colin's apps | sites,\n  code and deploys.\"\ndescription_auto: false\n")
        (home / "profile.yaml").write_text("description: 'Taffy: lead coordinator. Talks with Colin''s team.'\n")
        block = team._soul_block()
        self.assertIn("| `builder` | Builder | Lumenly, Colin's apps / sites, code and deploys. |", block)
        self.assertIn("| Taffy | Lead coordinator. Talks with Colin's team. |", block)
        self.assertIn("| `money` | Money | " + config.BOTS_BY_ID["money"].role + " |", block)

    def test_block_inserted_once_and_updated(self):
        home = _tmp / "hermes"
        (home / "profiles" / "money").mkdir(parents=True, exist_ok=True)
        (home / "profiles" / "career").mkdir(parents=True, exist_ok=True)
        config.HERMES_HOME = home
        (home / "SOUL.md").write_text("# Taffy\n\nBe kind.\n")
        (home / "profiles" / "money" / "SOUL.md").write_text("# Money\n")
        team.sync_souls()
        text = (home / "SOUL.md").read_text()
        self.assertEqual(text.count(team.SOUL_START), 1)
        self.assertIn("`career` | Career", text)
        self.assertEqual(team.sync_souls(), [])
        money = config.BOTS_BY_ID["money"]
        config.BOTS_BY_ID["money"] = config.Bot("money", "Budget", "Money", "#EAB308", "dollarsign.circle")
        config.BOTS[:] = list(config.BOTS_BY_ID.values())
        try:
            team.sync_souls()
        finally:
            config.BOTS_BY_ID["money"] = money
            config.BOTS[:] = list(config.BOTS_BY_ID.values())
        text = (home / "SOUL.md").read_text()
        self.assertEqual(text.count(team.SOUL_START), 1)
        self.assertIn("| Budget |", text)
        self.assertTrue(text.startswith("# Taffy\n\nBe kind."))
        (home / "SOUL.md").write_text(text + "\n" + team._soul_block() + "\nAfter.\n")
        team.sync_souls()
        healed = (home / "SOUL.md").read_text()
        self.assertEqual(healed.count(team.SOUL_START), 1)
        self.assertTrue(healed.rstrip().endswith("After."))


def setUpModule():
    home_fixture.pin(None)


def tearDownModule():
    home_fixture.unpin()


if __name__ == "__main__":
    unittest.main()
