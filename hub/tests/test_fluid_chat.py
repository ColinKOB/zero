"""Fluid chat (docs/qa/2026-10-09-fluid-chat.md): Zips that feel like teammates, from real user feedback on Grok Bot
and ChatGPT dots (docs/research/2026-10-09-grokbot-dots-user-feedback.md). Each test names the evidence rows (#) it
keeps true. The last class replays patterns from Colin's own recent conversations (read from alien, read-only).

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import (chatter, config, hermes, history, notices, quietlook, signin, store, team,  # noqa: E402
                       turns, watcher)
from test_fixes_r1 import AppCase, plugin  # noqa: E402
from test_gate import GateCase, gate, tt  # noqa: E402
from test_proactivity import ProCase, run  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "alien" / "bin" / "update-soul-fluid-lines.py"
TEMPLATE = REPO / "alien" / "bin" / "zero-home-templates" / "lead-SOUL.md"
_REAL_FOLLOW_UP = turns.follow_up


def _script():
    spec = importlib.util.spec_from_file_location("update_soul_fluid_lines", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def feed(kind=None):
    q = "SELECT * FROM feed" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
    return [dict(r) for r in store.db().execute(q, (kind,) if kind else ())]


# MARK: What counts as saying nothing

class Chatter(unittest.TestCase):
    """#78 (a bare "." sets off the other bots), #79 (acknowledgments and thanks between bots), #75 ("do not send
    'still working' unless blocked")."""

    def test_nothing_and_acknowledgments(self):
        for text in (".", "", "  ", "...", "[SILENT]", "[SILENT] nothing new"):
            with self.subTest(text=text):
                self.assertTrue(chatter.empty(text))
        for text in ("ok", "Noted.", "Got it, thanks!", "Thanks, School!", "Thanks for the heads-up.",
                     "Still working on it.", "I'm still working on it, will keep you posted", "On it.",
                     "I'll let you know.", "Sounds good, standing by."):
            with self.subTest(text=text):
                self.assertTrue(chatter.chatter(text, ["School", "Zero"]))
                self.assertFalse(chatter.empty(text))

    def test_news_is_never_chatter(self):
        for text in ("Done.", "Sent.", "Still working on the Soft 404 notices", "Thanks! The cap is $60.",
                     "It's still open.", "No, I don't have it.", "Got it: Form A is due at 11:55 p.m.",
                     "The weekly dining cap is $60 for Colin and Riley combined."):
            with self.subTest(text=text):
                self.assertFalse(chatter.chatter(text, ["School", "Zero", "Colin"]))


# MARK: Bot-to-bot messages are held in code

class TeamQuiet(unittest.TestCase):
    """#78, #79, #90: quiet rules are hard, not hints: acknowledgments are not delivered, and bot-to-bot traffic is
    rate-limited per pair and per conversation."""

    def setUp(self):
        from test_team import fresh
        fresh()
        self.bots = (list(config.BOTS), dict(config.BOTS_BY_ID), config.reload_bots)
        config.reload_bots = lambda: False
        for b in (config.Bot("default", "Zero", "Lead", "#F2711C", "sparkles"),
                  config.Bot("money", "Money", "Money", "#EAB308", "dollarsign.circle"),
                  config.Bot("school", "School", "School", "#3B82F6", "book")):
            config.BOTS_BY_ID[b.id] = b
        config.BOTS[:] = list(config.BOTS_BY_ID.values())

    def tearDown(self):
        config.BOTS[:], by_id, config.reload_bots = self.bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id)

    def team_feed(self):
        return [json.loads(r["body"]) for r in store.db().execute("SELECT body FROM feed WHERE kind='team'")]

    def test_acknowledgments_and_thanks_are_not_delivered_or_shown(self):
        for text in ("Thanks, Money!", "Got it.", "Still working on it.", "."):
            with self.subTest(text=text), self.assertRaises(team.TeamError) as e:
                team.tell("school", "money", text, "s1")
            self.assertIn("Not sent", str(e.exception))
        self.assertEqual(self.team_feed(), [])
        self.assertEqual(team.context_for("money", "m1", first=False), "")   # nothing rides along, nothing wakes

    def test_a_real_heads_up_still_goes(self):
        out = team.tell("school", "money", "A $120 tuition charge from Lakeview posted today.", "s1")
        self.assertIn("next turn", out)
        self.assertEqual(len(self.team_feed()), 1)

    def test_heads_ups_per_pair_are_limited(self):
        for i in range(team.TELLS_PER_PAIR_HOUR):
            team.tell("school", "money", f"Fact number {i} about the tuition bill.", f"s{i}")
        with self.assertRaises(team.TeamError) as e:
            team.tell("school", "money", "One more fact about the tuition bill.", "s9")
        self.assertIn("in the last hour", str(e.exception))
        team.tell("school", "default", "Form A is due tonight at 11:55 p.m.", "s9")   # another pair is fine

    def test_one_conversation_is_limited_across_teammates(self):
        n = 0
        with mock.patch.object(team, "TELLS_PER_PAIR_HOUR", 99):
            for i in range(team.TEAM_MSGS_PER_SESSION_HOUR):
                team.tell("school", ("money", "default")[i % 2], f"Distinct fact {i} about the semester.", "s1")
                n += 1
            with self.assertRaises(team.TeamError):
                team.tell("school", "money", "Yet another fact about the semester.", "s1")
        self.assertEqual(n, team.TEAM_MSGS_PER_SESSION_HOUR)

    def test_news_the_hub_passes_on_is_not_held_back(self):
        for i in range(team.TELLS_PER_PAIR_HOUR + 2):
            team.tell("school", "money", f"The task you handed me ({i}) is finished.", None, from_hub=True)
        self.assertEqual(len(self.team_feed()), team.TELLS_PER_PAIR_HOUR + 2)

    def test_a_thank_you_is_not_a_question(self):
        with self.assertRaises(team.TeamError):
            run(team.ask("default", "money", "Thanks, Money!", "s1"))

    def test_an_empty_answer_counts_as_no_answer(self):
        with mock.patch.object(hermes, "run_turn", lambda *a, **k: asyncio.sleep(0, "Got it.")):
            out = run(team.ask("default", "money", "What is the weekly dining cap?", "s1"))
        self.assertIn("could not answer", out)
        self.assertNotIn("Got it", json.dumps(self.team_feed()))

    def test_a_yes_is_an_answer(self):
        """Night review: "yes", "it" and "posted" are all acknowledgment words, so a teammate's plain answer to a
        yes-or-no question ("Yes, it posted.") was dropped and the asker heard it could not answer. A short question
        made of those words was refused as "no question in it"."""
        for answer in ("Yes.", "Yes, it posted.", "Yep, it's updated."):
            with self.subTest(answer=answer):
                with mock.patch.object(hermes, "run_turn", lambda *a, **k: asyncio.sleep(0, answer)):
                    out = run(team.ask("default", "money", f"Did the tuition charge post? ({answer})", "s1"))
                self.assertNotIn("could not answer", out)
                self.assertIn(answer, out)
        with mock.patch.object(hermes, "run_turn", lambda *a, **k: asyncio.sleep(0, "No.")):
            out = run(team.ask("school", "money", "Is it posted?", "s2"))
        self.assertIn("No.", out)

    def test_the_soul_block_says_one_owner_and_quiet(self):
        block = team._soul_block()
        self.assertIn("One owner per", block)
        self.assertIn("No acknowledgments", block)
        self.assertIn("they are not delivered", block)


# MARK: Board updates: relayed, not parroted, and quiet when there is nothing to add

class BoardUpdates(AppCase):
    """#75, #76, #80: one front door that relays the outcome; quiet replies are never posted or pushed."""

    def setUp(self):
        super().setUp()
        self.pushed = []
        self.notes = []
        self.reply = None

        async def fake_run(bot_id, text, continues=None):
            self.notes.append(text)
            return self.reply

        async def fake_push(title, body, bot_id=None, **kw):
            self.pushed.append((title, body))
            return True
        for obj, name, new in ((turns, "run_internal", fake_run), (watcher, "push", fake_push)):
            p = mock.patch.object(obj, name, new)
            p.start()
            self.addCleanup(p.stop)

    def test_the_note_asks_for_the_outcome_in_its_own_words(self):
        self.reply = "School confirmed Chicago style from the syllabus."
        ok = run(watcher._team_update([("school", '- School finished: "Citation style" (t_1). Chicago, per page 4.')]))
        self.assertTrue(ok)
        note = self.notes[0]
        self.assertIn("in your own words", note)
        self.assertIn("Do not repeat a teammate's summary", note)
        self.assertIn("what would clear it", note)
        self.assertEqual(len(self.pushed), 1)

    def test_a_quiet_reply_after_pushed_news_is_fine_and_silent(self):
        self.reply = "[SILENT]"
        ok = run(watcher._team_update([("school", '! School needs you: "Sign in" (t_2). Sign in to eLearning.')]))
        self.assertTrue(ok)
        self.assertEqual(feed("message"), [])
        self.assertEqual(self.pushed, [])

    def test_a_quiet_reply_to_news_only_zero_would_tell_is_no_answer(self):
        for self.reply in (".", "Noted.", None):
            with self.subTest(reply=self.reply):
                ok = run(watcher._team_update([("school", '- School finished: "Form A" (t_3). Submitted.')]))
                self.assertFalse(ok)   # retried, then told plainly by the watcher loop
        self.assertEqual(feed("message"), [])
        self.assertEqual(self.pushed, [])


class QuietHistory(unittest.TestCase):
    """#75, #78, #51: what the chat shows. A reply to the hub's own turn that says nothing is left out, and an empty
    reply is never shown as a bubble; a short real answer to Colin always stays."""

    def classes(self, msgs):
        return [(m["class"], m["display"]) for m in history.classify(msgs)]

    def test_board_and_hub_turns(self):
        msgs = [{"role": "user", "text": "[Board update from the hub, not a message from Colin]\n- x"},
                {"role": "assistant", "text": "[SILENT]"},
                {"role": "user", "text": "[Board update from the hub, not a message from Colin]\n- y"},
                {"role": "assistant", "text": "Noted."},
                {"role": "user", "text": "[Board update from the hub, not a message from Colin]\n- z"},
                {"role": "assistant", "text": "School finished Form A; nothing needs you."}]
        shown = [m["display"] for m in history.visible(msgs)]
        self.assertEqual(shown, ["School finished Form A; nothing needs you."])

    def test_colins_own_turns(self):
        msgs = [{"role": "user", "text": "Thanks!"}, {"role": "assistant", "text": "Got it."},
                {"role": "user", "text": "And the other thing?"}, {"role": "assistant", "text": "."}]
        self.assertEqual([m["display"] for m in history.visible(msgs)], ["Thanks!", "Got it.", "And the other thing?"])


# MARK: Honest state: no typing dots that just stop

class SilentTurns(AppCase):
    """#50, #51, #52, #53: a turn that ends with nothing says so plainly, with the cause and a retry time when known."""

    def turn_events(self, events):
        async def fake_stream(bot_id, text, **kw):
            for ev in events:
                yield ev

        async def go():
            with mock.patch.object(hermes, "stream_turn", fake_stream), \
                    mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")):
                t = turns.start("school", "what is due tonight?")
                out = [ev async for ev in t.follow() if ev is not None]
                return out
        return run(go())

    def test_no_reply_and_no_error(self):
        out = self.turn_events([{"type": "status", "text": "Searching the web"}, {"type": "done"}])
        errors = [e["text"] for e in out if e["type"] == "error"]
        self.assertEqual(errors, ["School stopped without answering. Send it again in a moment."])
        self.assertEqual(out[-1]["type"], "done")

    def test_a_bare_dot(self):
        out = self.turn_events([{"type": "reply", "text": "."}, {"type": "done"}])
        self.assertEqual([e["type"] for e in out], ["error", "done"])

    def test_a_stopped_turn_is_not_called_silent(self):
        """With fluid-app's Stop: a turn the person stopped ends as Stopped, never "stopped without answering"."""
        async def fake_stream(bot_id, text, **kw):
            turns.current("school").stopped = True
            yield {"type": "done"}

        async def go():
            with mock.patch.object(hermes, "stream_turn", fake_stream), \
                    mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")):
                t = turns.start("school", "what is due tonight?")
                return [ev async for ev in t.follow() if ev is not None]
        out = run(go())
        self.assertNotIn("error", [e["type"] for e in out])
        self.assertIn("stopped", [e["type"] for e in out])

    def test_a_real_reply_is_untouched(self):
        out = self.turn_events([{"type": "reply", "text": "Form A is due at 11:55 p.m."}, {"type": "done"}])
        self.assertEqual([e["type"] for e in out], ["reply", "done"])

    def test_the_limit_names_a_retry_time(self):
        from datetime import datetime
        from taffy_hub import home

        def clock(ts):   # in the home's own time zone, as the line says it
            return datetime.fromtimestamp(ts, home.TZ).strftime("%-I:%M %p").replace("AM", "a.m.").replace("PM", "p.m.")
        now = 1791600000.0   # Oct 9, 2026, 10:40 p.m. Eastern
        self.assertIn(f"Try again after {clock(now + 37 * 60)}",
                      hermes.limit_line("429 rate limit, try again in 37 minutes", now))
        self.assertIn("Try again after 3:40 p.m.", hermes.limit_line("usage limit; resets at 3:40 PM", now))
        later = hermes.limit_line("limit reached, resets_at: 1791700000", now)
        self.assertIn(datetime.fromtimestamp(1791700000, home.TZ).strftime("%A"), later)
        plain = hermes.limit_line("usage limit reached", now)
        self.assertIn("once the limit resets", plain)
        self.assertNotIn("..", plain)

    def test_a_picked_up_conversation_that_says_nothing_is_told_plainly(self):
        """After Colin's tap, silence used to look like nothing happened."""
        pushed = []

        async def fake_push(title, body, bot_id=None, **kw):
            pushed.append((body, kw.get("urgent")))
            return True

        async def failing(bot_id, text, continues=None):
            hermes.last_error[bot_id] = "That reply ran into a problem. Try again in a moment."
            return None
        with mock.patch.object(turns, "run_internal", failing), mock.patch.object(watcher, "push", fake_push):
            out = run(_REAL_FOLLOW_UP("school", "[Hub note] Colin approved this.", "chat-1", True))
        self.assertIsNone(out)
        [item] = feed("checkin")
        self.assertEqual(item["title"], "School didn't pick this back up")
        self.assertIn("That reply ran into a problem", item["body"])
        self.assertIn("Ask School here to carry on", item["body"])
        self.assertEqual(pushed[0][1], True)

    def test_a_picked_up_conversation_that_answers_is_posted(self):
        async def fine(bot_id, text, continues=None):
            return "Sent the email to Miss Dana."
        with mock.patch.object(turns, "run_internal", fine), \
                mock.patch.object(watcher, "push", lambda *a, **k: asyncio.sleep(0, True)):
            run(_REAL_FOLLOW_UP("clients", "[Hub note] approved", "chat-1", True))
        self.assertEqual([f["body"] for f in feed("message")], ["Sent the email to Miss Dana."])
        self.assertEqual(feed("checkin"), [])


# MARK: Approvals: keep going, no second confirmation, carried across a handoff

class KeepGoingAroundACard(GateCase):
    """#46, #47, #66: the rest of the work goes on and the okay is the last step; #41: no second "should I?"."""

    def test_chat_card(self):
        self.ask("deploy the site and draft the announcement")
        msg = self.call("terminal", {"command": "cd /tmp/site && netlify deploy --prod --site heron"})["message"]
        self.assertIn("finish every other part of his request that does not need this step", msg)
        self.assertIn("Do not also ask him to confirm it in the chat", msg)
        self.assertIn("Waiting for your okay on the card.", msg)
        self.assertNotIn("stop and wait", msg)

    def test_board_card(self):
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": "t_42"}):
            msg = gate.pre_tool_call("terminal", {"command": "netlify deploy --prod --site heron"},
                                     session_id="")["message"]
        self.assertIn("First finish and save every other part of the task that does not need this step", msg)
        self.assertIn("Call kanban_block on t_42", msg)


class HandoffApproval(GateCase):
    """#42: "I approve the specific action, but the task still says approval is missing... re-authorize the same
    action defeats the point of delegating." Colin's okay to Zero covers that exact call once in the task Zero handed
    to School, and nothing else."""

    DEPLOY = {"command": "netlify deploy --prod --site heron"}

    def board(self, rows):
        db = gate._taffy_home() / "kanban.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT, title TEXT, assignee TEXT, status TEXT, created_by TEXT)")
        c.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", rows)
        c.commit()
        c.close()

    def approve_for(self, bot, args):
        with mock.patch.object(gate, "_bot", lambda: bot):
            self.ask("deploy the heron site", session=f"chat-{bot}")
            blocked = self.call("terminal", args, session=f"chat-{bot}")
        self.assertIsNotNone(blocked)
        [card] = self.cards()
        plugin.decide(card["id"], True)

    def as_school_in(self, task, args):
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": task}):
            return gate.pre_tool_call("terminal", args, session_id="")

    def test_the_exact_call_goes_through_once_in_the_handed_task(self):
        self.board([("t_1", "Deploy the heron site", "school", "running", "default")])
        self.approve_for("default", self.DEPLOY)
        self.assertIsNone(self.as_school_in("t_1", self.DEPLOY))
        again = self.as_school_in("t_1", self.DEPLOY)
        self.assertIn("Call kanban_block on t_1", again["message"])   # once, like Allow Once

    def test_another_call_or_another_task_still_asks(self):
        self.board([("t_1", "Deploy", "school", "running", "default"), ("t_2", "Deploy", "school", "running", "money")])
        self.approve_for("default", self.DEPLOY)
        other = {"command": "netlify deploy --prod --site lumenly"}
        self.assertIsNotNone(self.as_school_in("t_1", other))
        self.assertIsNotNone(self.as_school_in("t_2", self.DEPLOY))   # Money did not get Colin's okay


class OneOwnerPerStep(GateCase):
    """#79, #90 and the vendors' advice to ask for a single owner per stage: no second open task for the same step."""

    def setUp(self):
        super().setUp()
        db = gate._taffy_home() / "kanban.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE tasks (id TEXT, title TEXT, assignee TEXT, status TEXT, created_by TEXT)")
        c.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", [
            ("t_9", "Check the POL 101 syllabus for the citation style", "school", "running", "default"),
            ("t_8", "Sign-in rules test: log out and back in", "builder", "done", "default")])
        c.commit()
        c.close()

    def test_a_second_open_task_for_the_same_step_is_not_made(self):
        blocked = self.call("kanban_create", {"title": "Check the POL 101 syllabus for the citation style.",
                                              "assignee": "career"})
        self.assertIn("task t_9 with this title is already open", blocked["message"])
        self.assertIn("kanban_comment", blocked["message"])

    def test_different_or_finished_steps_are_fine(self):
        self.assertIsNone(self.call("kanban_create", {"title": "Check the BUS 305 syllabus for the citation style",
                                                      "assignee": "school"}))
        self.assertIsNone(self.call("kanban_create", {"title": "Sign-in rules test: log out and back in",
                                                      "assignee": "builder"}))


class PluginWords(unittest.TestCase):
    """The tool texts the Zips read say the same as the SOULs (#41, #46, #59-#61, #78, #79, #80)."""

    def test_sign_in_tool_keeps_the_rest_going_and_checks_on_return(self):
        d = tt.asksignin.SCHEMA["description"]
        self.assertIn("finish any part of his request that does not need it", d)
        self.assertIn("open the page again and check", d)
        self.assertIn("exactly what the page shows", d)
        self.assertNotIn("and then stop", d)

    def test_asking_first_does_not_stop_the_rest(self):
        self.assertIn("keep doing every part that does not need it", tt.teammates.ASK_FIRST["api_server"])
        self.assertNotIn("stop and ask him now", tt.teammates.ASK_FIRST["api_server"])

    def test_teammate_tools_say_no_chatter(self):
        self.assertIn("not delivered", tt.teammates.SCHEMAS["tell_teammate"]["description"])
        self.assertIn("never to thank, acknowledge or check in", tt.teammates.SCHEMAS["ask_teammate"]["description"])

    def test_a_handoff_is_one_line(self):
        self.assertIn("in one line who has it now", tt.LEAD_HEARS)
        self.assertIn("in your own words", tt.LEAD_HEARS)


class ApprovalNotes(AppCase):
    """#41: after his tap, the Zip does it; it never asks him to confirm again."""

    def test_allow_once_says_the_tap_was_the_yes(self):
        req = plugin.action_request("clients", "send", "", "Email Miss Dana the preview", "heron.studio@example.com",
                                    "Hi", "fp-k", "chat-1", None, why="asked")
        self.client.post(f"/api/signin-requests/{req}", json={"decision": "once"})
        [(bot, note, session, urgent)] = self.followed
        from taffy_hub import who
        self.assertIn(f"Do not ask {who.first()} to confirm it again", note)
        self.assertIn("the tap was the yes", note)


# MARK: The sign-in round trip

class SignInRoundTrip(AppCase):
    """#59, #60, #61: "I completed sign-in... told dot I was done and asked it to try again, but it still couldn't read
    the page." Done, by button or in the chat, means the Zip goes back and checks, and says exactly what it sees."""

    def open_card(self, bot="default", site_url="https://www.1800contacts.com/account/signin"):
        fid, said = signin.ask(bot, site_url, "reorder contacts", "chat-1")
        return fid

    def test_the_card_note_says_check_again(self):
        fid = self.open_card()

        async def go():
            with mock.patch.object(signin, "loop", asyncio.get_running_loop()), \
                    mock.patch.object(watcher, "push", lambda *a, **k: asyncio.sleep(0, True)):
                await asyncio.to_thread(signin.resolve, "default", fid, "signed_in")
                for _ in range(50):
                    if self.followed:
                        break
                    await asyncio.sleep(0.02)
        run(go())
        [(_bot, note, _session, _urgent)] = self.followed
        self.assertIn("Open the page again now and check", note)
        self.assertIn("exactly what the page shows", note)
        self.assertEqual(store.take_card_notes("default"), [])   # the button path resumes on its own

    def test_saying_it_in_the_chat_answers_the_card_and_the_zip_checks(self):
        fid = self.open_card()
        self.assertEqual(signin.done_in_chat("default", "ok I'm in, try again"), "1800contacts.com")
        card = json.loads(store.db().execute("SELECT body FROM feed WHERE id=?", (fid,)).fetchone()["body"])
        self.assertEqual(card["state"], "signed_in")
        [note] = store.take_card_notes("default")
        self.assertIn("Open the page again now and check", note)
        self.assertIn("exactly what the page shows", note)
        self.assertTrue(plugin.db().execute("SELECT 1 FROM requests WHERE bot_id='default' AND status='allowed' AND "
                                            "session_id='chat-1'").fetchone())

    def test_other_messages_leave_the_card_alone(self):
        self.open_card()
        for text in ("Ask me for the login again. Sorry it errored for me",      # Colin, Oct 9 12:59
                     "Log out of the practice site, then log back in with the saved login and tell me the banner text.",
                     "What does the page say once you are logged in?", "I'm in the car", "what's for dinner"):
            with self.subTest(text=text):
                self.assertIsNone(signin.done_in_chat("default", text))
        self.assertIsNone(signin.done_in_chat("school", "I signed in"))   # no card of School's is open

    def test_the_first_ask_keeps_the_rest_of_the_work_going(self):
        _fid, said = signin.ask("school", "https://elearning.lakeview.test/login", "check grades", "chat-2")
        self.assertIn("finish any part of the request that does not need it", said)


# MARK: Proactive questions

class CalmProactivity(ProCase):
    """#67, #71: a proactive question names its source; #70, #72: no money or security alarms unprompted; #69: a
    dismissed question returns only when something changed; #74: no speculative background work."""

    def test_an_alarm_its_source_does_not_raise_is_refused(self):
        c = self.cand(noticer="quiet", evidence="Your Gmail shows a suspicious sign-in from another device.",
                      question="Was it you?", deadline=self.soon(), haiku=False)
        self.assertEqual(notices.check(c), "an alarm its source does not raise")
        ok = self.cand(noticer="quiet", evidence="Google's email says there was suspicious activity on your account.",
                       question="Want me to look?", deadline=self.soon(), excerpt="We noticed suspicious activity.",
                       haiku=False)
        self.assertIsNone(notices.check(ok))

    def test_haikus_alarm_keeps_codes_calm_words(self):
        self.haiku.extra = {"evidence": "This looks like possible fraud on your card.",
                            "question": "Should I flag it as fraud?"}
        c = self.cand(deadline=self.soon())
        words = run(notices.judge(c))
        self.assertEqual(words["evidence"], c.evidence)
        self.assertEqual(words["question"], c.question)

    def test_the_prompts_ask_for_the_source_and_no_alarms(self):
        p = notices._prompt(self.cand())
        self.assertIn("starting with where it came from", p)
        self.assertIn("Never call anything suspicious", p)
        g = notices._gate_prompt(self.cand())
        self.assertIn("does not raise an alarm on its own", g)
        self.assertIn("says where it came from", g)

    def test_a_dismissed_question_returns_only_when_it_changed(self):
        c = self.cand(version="v1", deadline=self.soon(30))
        [nid] = self.raise_(c)
        notices.answer(notices.by_id(nid)["feed_id"], None)
        self.assertEqual(self.raise_(self.cand(version="v1", deadline=self.soon(30))), [])
        self.assertEqual(len(self.raise_(self.cand(version="v2", deadline=self.soon(20)))), 1)

    def test_the_quiet_look_is_short_and_knows_what_was_turned_down(self):
        [nid] = self.raise_(self.cand(deadline=self.soon(30)))
        notices.answer(notices.by_id(nid)["feed_id"], None)
        p = quietlook.prompt()
        self.assertIn("turned down in the last two weeks", p)
        self.assertIn("- Haiku question?", p)   # the card's question as it was shown
        self.assertIn("no research, no new tasks", p)
        self.assertIn("unless the source itself says something is wrong", p)


# MARK: Which kinds of news reach the phone (fluid-app's notifyprefs.py, wired here)

class NotificationKinds(unittest.TestCase):
    """#75, #76, #77: "levels: stuck / question needing input-approval / something important / status updates". Each
    Zip's kinds (needs, done, progress) decide which pushes go out; a push with no kind counts as needs."""

    def setUp(self):
        # Never the live calendar: a real shift on it held these test notifications (Oct 10, 9 a.m.).
        from taffy_hub import notices as _notices
        real_busy = _notices.busy_block
        _notices.busy_block = lambda t=None: None
        self.addCleanup(setattr, _notices, "busy_block", real_busy)
        from taffy_hub import apns
        from test_onboarding import _fresh_store
        self.saved = (config.HUB_DIR, config.HUB_DB)
        _fresh_store(Path(tempfile.mkdtemp()))
        self.sent = []
        for obj, name, new in ((apns, "ready", lambda: True), (watcher, "_night", lambda: False),
                               (watcher, "_push_sync", lambda t, b, bot_id=None, **kw: self.sent.append(kw.get("topic") or t) or True)):
            p = mock.patch.object(obj, name, new)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False

    def push(self, *a, **kw):
        return run(watcher.push(*a, **kw))

    def test_each_kind_can_be_turned_off_per_zip(self):
        from taffy_hub import notifyprefs
        notifyprefs.set_kinds("school", progress=False, done=False)
        self.assertFalse(self.push("Check-ins", "x", "school", kind="progress"))
        self.assertFalse(self.push("School finished", "x", "school", kind="done"))
        self.assertTrue(self.push("School needs your okay", "x", "school", kind="needs"))
        self.assertTrue(self.push("School wants to sign in", "x", "school"))            # no kind: needs
        self.assertTrue(self.push("Money finished", "x", "money", kind="done"))         # another Zip is untouched
        self.assertEqual(self.sent, ["School needs your okay", "School wants to sign in", "Money finished"])

    def test_the_master_switch_still_wins(self):
        store.set_notify("school", False)
        self.assertFalse(self.push("School needs your okay", "x", "school", kind="needs", urgent=True))

    def test_callers_name_their_kind(self):
        """Cards and blocks are needs, finished team work is done, check-ins and long tasks are progress."""
        from taffy_hub import notifyprefs
        notifyprefs.set_kinds("school", progress=False)
        got = []

        async def fake_push(title, body, bot_id=None, **kw):
            got.append((title, kw.get("kind")))
            return True
        with mock.patch.object(watcher, "push", fake_push), \
                mock.patch.object(turns, "run_internal", lambda *a, **k: asyncio.sleep(0, "School finished Form A.")):
            run(watcher._team_update([("school", '- School finished: "Form A" (t_3). Submitted.')]))
            run(_REAL_FOLLOW_UP("school", "[Hub note] approved", "chat-1", True))
            run(notices.nudge("task:t_9:long", "A task is taking a long time", "x", "school", kind="progress"))
        self.assertEqual([kind for _title, kind in got], ["done", "needs", "progress"])


# MARK: The instructions every Zip reads

@unittest.skipUnless(SCRIPT.exists(), "repo-only check (alien runs the hub without the repo)")
class SoulLines(unittest.TestCase):
    def setUp(self):
        pin = tt.home.pinned(None)
        pin.__enter__()
        self.addCleanup(pin.__exit__, None, None, None)
        self.s = _script()
        self.souls = sorted((REPO / "alien" / "profiles").glob("*/SOUL.md"))

    def test_every_repo_soul_and_the_house_rules_carry_the_lines(self):
        if not self.souls:
            self.skipTest("repo-only check (alien runs the hub without the repo)")
        house = tt.team.HOUSE_RULES
        for line in self.s.LINES:
            for soul in self.souls:
                self.assertIn(line, soul.read_text(), f"{soul.parent.name}: {line[:40]}")
            self.assertIn(line, house, f"house rules: {line[:40]}")
        for soul in self.souls:
            lead = soul.parent.name == "taffy"
            for line in self.s.LEAD_LINES:
                self.assertEqual(line in soul.read_text(), lead, f"{soul.parent.name}: {line[:40]}")
            self.assertEqual(self.s.update_text(soul.read_text()), soul.read_text())   # the script is what made them

    def test_the_lines_cover_the_evidence(self):
        text = "\n".join(self.s.LINES + self.s.LEAD_LINES)
        for words in ("one short line in the same message as each step", "Never send \"still working\"", "Will do", "never what you left alone", "the cause, what would clear it",
                      "finish the rest of what he asked", "without asking again", "carries across a handoff",
                      "open the page again and check", "I don't have that", "never \"you haven't told me\"",
                      "Never claim feelings", "nickname he hasn't chosen", "Ask only the question",
                      "never their summary again", "Show a handoff in one line", "answers him itself",
                      "name the source", "into a warning", "dismissed question"):
            self.assertIn(words, text, words)
        self.assertNotIn("—", text)
        self.assertNotIn("not just", text)

    def test_zeros_soul_stays_under_hermess_floor(self):
        """Hermes truncates a SOUL.md past 20,000 characters when it does not know the model's window."""
        for soul in self.souls:
            self.assertLess(len(soul.read_text()), 20000, soul.parent.name)

    def test_script_rewrites_the_card_sentence_once_and_leaves_the_team_section(self):
        s = self.s
        soul = ("# School\n\n- Never do these... Then reply with only one short plain line, such as \"Waiting for your "
                "okay on the card.\", and when he approves, make exactly the same call again. Never pay.\n"
                "- Starting on your own: tell him.\n\n<!-- team:start -->\n- Team line\n<!-- team:end -->\n")
        new = s.update_text(soul)
        self.assertIn("Keep doing the rest of what he asked that does not need that step", new)
        self.assertIn("without asking him to confirm", new)
        self.assertEqual(new.count(s.MARKER), 1)
        self.assertNotIn(s.LEAD_MARKER, new)
        lines = new.split("\n")
        self.assertEqual(lines[lines.index("- Starting on your own: tell him.") + 1], s.LINES[0])
        self.assertIn("<!-- team:start -->\n- Team line\n<!-- team:end -->", new)
        self.assertEqual(s.update_text(new), new)                                   # idempotent
        lead = "# Zero\n\n## Your team\n\n- Starting on your own: x\n"
        self.assertEqual(s.update_text(lead).count(s.LEAD_MARKER), 1)
        self.assertEqual(s.update_text("# Nothing to anchor on\n"), "# Nothing to anchor on\n")

    def test_script_backs_up_and_dry_run_changes_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "SOUL.md"
        p.write_text("# Builder\n\n- Starting on your own: tell him.\n")
        self.s.main(["--dry-run", str(p)])
        self.assertNotIn(self.s.MARKER, p.read_text())
        self.assertEqual(list(tmp.glob("SOUL.md.bak-fluid-*")), [])
        self.s.main([str(p)])
        self.assertIn(self.s.MARKER, p.read_text())
        self.assertEqual(len(list(tmp.glob("SOUL.md.bak-fluid-*"))), 1)
        self.s.main([str(p)])                                                      # second run: nothing
        self.assertEqual(len(list(tmp.glob("SOUL.md.bak-fluid-*"))), 1)

    def test_any_home_gets_the_lines_said_for_its_person(self):
        """A home whose person goes without pronouns (home.json pronoun_free) gets the person's name, never "he"."""
        sam = {"home_id": "sam", "person": {"name": "Sam Rivera", "first_name": "Sam", "pronoun_free": True}}
        lead = TEMPLATE.read_text()
        self.assertEqual(lead.count(self.s.MARKER), 1)
        self.assertEqual(lead.count(self.s.LEAD_MARKER), 1)
        self.assertIn("Keep doing the rest of what was asked that does not need that step", lead)
        with tt.home.pinned(sam):
            spec = "# Errands\n\n- Never do these. Then reply with only one short plain line, such as \"Waiting " \
                   "for your okay on the card.\", and when Sam approves, make exactly the same call again.\n" \
                   "- Starting on your own: say so.\n"
            new = self.s.update_text(spec)
            rules = tt.team.house_rules()
        for text in (new, rules):
            added = "\n".join(line for line in text.split("\n") if line.startswith(("- Show your steps", "- Keep going",
                                                                                     "- After a sign-in",
                                                                                     "- Personality")))
            self.assertIn("Sam", added)
            self.assertNotRegex(added, r"\b(he|him|his|Colin)\b")


# MARK: Replays of Colin's recent conversations (read-only from alien, Oct 7 to 9)

class RecentConversations(unittest.TestCase):
    """Real lines from Colin's hub (team_msgs 1 to 5, feed items, Zero's sessions), run through the new rules: what
    was real still goes through, and what was empty no longer reaches him."""

    TEAM = [   # team_msgs 1, 2, 4, 5: real questions and answers between Zips must never be taken for chatter
        "What is the weekly dining cap in Colin and Riley's household budget?",
        "The weekly dining cap is $60 for Colin and Riley combined. It covers restaurants, coffee, fast food and treats.",
        "Do you have the source or Netlify access for the Blue Heron site?",
        "No, I don't have it. I checked just now and found no Blue Heron source on my computer.",
        "Has Colin made Dana's requested change since Oct 7, or is it still open? One line please.",
        "It's still open. I just checked the live site, theblueheron.test, and it has no Halloween design.",
    ]
    BOARD_REPLIES = [   # Zero's replies to board updates (feed 26, 30, 39, 49)
        "That's the same sign-in test I just reported. It passed: Builder logged out, logged back in with the saved "
        "login without ever seeing the password. Nothing needs you.",
        "I already passed this along. It's the same e-learning check, and the only thing that needs you is your Unit "
        "6 video, which isn't posted yet and is due tonight at 11:55.",
        "Clients is signed in to Bloomerang in its own browser, and the dashboard and transaction reports both open.",
    ]

    def test_real_team_messages_are_never_chatter(self):
        for text in self.TEAM:
            with self.subTest(text=text[:40]):
                self.assertFalse(chatter.chatter(text, ["Zero", "Money", "Builder", "School", "Clients", "Colin"]))

    def test_real_board_replies_stay_and_the_silent_one_goes(self):
        msgs = []
        for reply in self.BOARD_REPLIES + ["[SILENT]"]:   # Zero's [SILENT] (Oct 9 cron session)
            msgs += [{"role": "user", "text": "[Board update from the hub, not a message from Colin]\n- x"},
                     {"role": "assistant", "text": reply}]
        self.assertEqual([m["display"] for m in history.visible(msgs)], self.BOARD_REPLIES)

    def test_schools_stop_at_the_card_would_now_keep_going(self):
        """Feed 123 (Oct 8, 7:39 a.m.): School wrote "There's a card in your app asking you to allow that." and
        stopped, so nothing else in Colin's request got done until he answered. The card's instructions now say to
        finish the rest first."""
        self.assertIn("finish every other part of his request", gate.KEEP_GOING)
        self.assertIn("the card is the question", gate.KEEP_GOING)

    def test_colins_real_messages_are_read_right(self):
        self.assertIsNone(signin._JUST_DONE.match("Typing dots test: run sleep 25 in your terminal, then reply with "
                                                  "just done."))
        self.assertIsNone(signin._SIGNED.search("Log out of the practice site, then log back in with the saved login "
                                                "and tell me the banner text."))


if __name__ == "__main__":
    unittest.main()
