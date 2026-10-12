"""Proactivity phase 2 (calcache.py, calendar_noticers.py, money_noticers.py, prepared.py, quietlook.py and the phase 2
parts of notices.py and noticers.py), over fixtures: the calendar and Trello copy, busy blocks, the calendar wake, the
eight new noticers, prepared actions and what their approval covers, answers from the lock screen, the quiet look and
its budget, and the Settings, Notices status.

Haiku, the connector runs, Tally, himalaya, the Zips' turns and Apple push are all replaced. Nothing here reaches the
network, runs a program, reads the home folder, needs Hermes or depends on the folder the tests run from. The tests
that go through the real permission gate skip when the taffy-tools plugin can't be loaded.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import (calcache, calendar_noticers, config, consent, health, hermes, money_noticers,  # noqa: E402
                       noticers, notices, prepared, quietlook, store, watcher)
from taffy_hub.notices import Candidate, Option  # noqa: E402
from test_proactivity import FakeSources, ProCase, at, env, run  # noqa: E402

REAL_REFRESH = calcache.refresh   # ProCase replaces it; the cache tests use the real one with a fake connector
KEN = ("Theo Avery", "ken@brightpath.test")


def ev(eid, title, start, end, **kw):
    return {"id": eid, "title": title, "start": start, "end": end, "all_day": kw.pop("all_day", False),
            "location": kw.pop("location", ""), "attendees": kw.pop("attendees", []),
            "my_response": kw.pop("my_response", "accepted"), "description": kw.pop("description", ""), **kw}


def person(name, email):
    return {"name": name, "email": email, "self": False}


class Sources2(FakeSources):
    """Phase 1's fixture sources plus the calendar and Trello copy, every Zip's notes, work folders, calls and
    Tally."""

    def __init__(self):
        super().__init__()
        self.events, self.trello_cards, self.zip_notes = [], [], {}
        self.files, self.call_rows, self.buys = [], [], []
        self.wrote = set()

    def calendar(self):
        return sorted(self.events, key=lambda e: e["start"])

    def trello(self):
        return self.trello_cards

    def notes_of(self, bot_id):
        return self.zip_notes.get(bot_id, [])

    def work_files(self, days=3):
        return self.files

    def calls(self, since):
        return [c for c in self.call_rows if c["at"] >= since]

    async def purchases(self):
        return self.buys


class P2Case(ProCase):
    def setUp(self):
        super().setUp()
        self.src = Sources2()

    def find(self, name):
        return run(noticers.finders()[name](self.src))

    def write_calendar(self, events):
        (self.tmp / "calendar.json").write_text(json.dumps({"events": events}))


# MARK: The calendar and Trello copy

CAL_ANSWER = json.dumps({"events": [
    {"id": "e1", "title": "corner store shift", "start": "2026-10-08T15:00:00-04:00", "end": "2026-10-08T20:00:00-04:00",
     "all_day": False, "location": "The corner store", "attendees": [], "my_response": "accepted"},
    {"id": "e2", "title": "Advising with Andrea Stone", "start": "2026-10-09T14:00:00-04:00",
     "end": "2026-10-09T14:30:00-04:00", "attendees": [{"name": "Andrea Stone", "email": "astone@lakeview.test"},
                                                        {"name": "Colin", "email": "jd910@lakeview.test", "self": True}]},
    {"id": "e3", "title": "Church picnic", "start": "2026-10-11", "end": "2026-10-12", "all_day": True},
    {"id": "e4", "title": "Old meeting", "start": "2026-10-10T09:00:00-04:00", "end": "2026-10-10T10:00:00-04:00",
     "my_response": "declined"},
    "not an event"]})


class Cache(P2Case):
    def setUp(self):
        super().setUp()
        self.runs = []
        self.answer = {"calendar": "Here it is: " + CAL_ANSWER,
                       "trello": json.dumps({"cards": [{"id": "c1", "name": "POL 101 reflection paper",
                                                        "board": "Colin | School & Projects", "list": "Next",
                                                        "due": "2026-10-09T23:55:00-04:00", "due_complete": False}]})}

        async def connector(service, prompt):
            self.runs.append((service, prompt))
            return self.answer[service]

        async def connected(service):
            return "ok"
        for p in (mock.patch.object(calcache, "run_connector", connector),
                  mock.patch.object(calcache, "connected", connected),
                  mock.patch.object(calcache, "refresh", REAL_REFRESH)):
            p.start()
            self.addCleanup(p.stop)

    def test_the_runs_can_only_read(self):
        for service, server in (("calendar", "mcp__claude_ai_Google_Calendar"), ("trello", "mcp__claude_ai_Trello")):
            cmd = calcache.command(service, "list")
            self.assertEqual(cmd[cmd.index("--model") + 1], "claude-haiku-5-5")
            self.assertEqual(cmd[cmd.index("--allowedTools") + 1], f"{server},ToolSearch")
            denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
            for tool in calcache.WRITE_TOOLS[service]:
                self.assertIn(f"{server}__{tool}", denied)
        self.assertIn("mcp__claude_ai_Google_Calendar__create_event",
                      calcache.command("calendar", "x")[calcache.command("calendar", "x").index("--disallowedTools") + 1])
        self.assertIn("never create, change, delete or answer", calcache.CAL_PROMPT)

    def test_refresh_writes_only_on_change_and_wakes_the_noticers(self):
        notices._wakes.clear()
        self.assertTrue(run(calcache.refresh("calendar")))
        self.assertIn("calendar", notices._wakes)
        events = notices.calendar_events()
        self.assertEqual([e["id"] for e in events], ["e1", "e2", "e4", "e3"])
        advising = next(e for e in events if e["id"] == "e2")
        self.assertEqual(advising["attendees"][0]["email"], "astone@lakeview.test")
        self.assertTrue(next(e for e in events if e["id"] == "e3")["all_day"])
        self.assertIn("from 2026-10-07 through 2026-10-16", self.runs[0][1])
        mtime = notices.CALENDAR_FILE.stat().st_mtime_ns
        notices._wakes.clear()
        self.assertFalse(run(calcache.refresh("calendar")))     # the same events: nothing written, no wake
        self.assertEqual(notices._wakes, set())
        self.assertEqual(notices.CALENDAR_FILE.stat().st_mtime_ns, mtime)
        st = calcache.status()["calendar"]
        self.assertEqual((st["state"], st["count"]), ("ok", 4))
        self.assertTrue(run(calcache.refresh("trello")))
        self.assertIn("trello", notices._wakes)
        self.assertEqual(calcache.trello_cards()[0]["due"], at(10, 9, 23, 55))

    def test_shared_for_voice_lookups(self):
        run(calcache.refresh("calendar"))
        run(calcache.refresh("trello"))
        tomorrow = calcache.events_between(at(10, 9, 0), at(10, 10, 0))
        self.assertEqual([e["title"] for e in tomorrow], ["Advising with Andrea Stone"])
        self.assertEqual(calcache.cards()[0]["name"], "POL 101 reflection paper")

    def test_a_failed_or_unconnected_account_keeps_the_old_copy(self):
        run(calcache.refresh("calendar"))
        self.answer["calendar"] = "Sorry, I couldn't reach the calendar."
        self.assertFalse(run(calcache.refresh("calendar")))
        self.assertEqual(calcache.status()["calendar"]["state"], "failed")
        self.assertEqual(len(notices.calendar_events()), 4)

        async def not_connected(service):
            return "not_connected"
        self.runs.clear()
        with mock.patch.object(calcache, "connected", not_connected):
            self.assertFalse(run(calcache.refresh("calendar")))
        self.assertEqual(self.runs, [])
        self.assertEqual(calcache.status()["calendar"]["state"], "not_connected")

    def test_hourly_in_waking_hours_never_at_the_plan_limit_and_only_when_on(self):
        done = []

        async def refresh(service):
            done.append(service)
            calcache._note(service, tried=notices.now())
            return False

        def tick():
            async def go():
                await calcache.tick()
                if calcache._task:
                    await calcache._task
            run(go())
        with mock.patch.object(calcache, "refresh", refresh):
            calcache._task = None
            self.clock = at(10, 8, 6, 30)
            tick()
            self.assertEqual(done, [])                       # before 7 a.m.
            self.clock = at(10, 8, 10)
            tick()
            tick()
            self.assertEqual(done, ["calendar", "trello"])
            self.clock += 30 * 60
            tick()
            self.assertEqual(len(done), 2)                   # not yet an hour
            self.clock += 31 * 60
            with mock.patch.dict(health.status, {"claude": False}):
                tick()
            self.assertEqual(len(done), 2)                   # the plan is at its limit
            tick()
            self.assertEqual(done[-1], "calendar")
            notices.set_enabled(False)
            self.clock += 2 * 3600
            tick()
            self.assertEqual(done[-1], "calendar")
            self.assertEqual(len(done), 3)                   # proactivity off: no runs at all


# MARK: Busy blocks

class BusyBlocks(P2Case):
    def test_titles_and_people_make_a_busy_block(self):
        k = calcache.busy_kind
        self.assertEqual(k(ev("a", "corner store shift", 0, 1)), "shift")
        self.assertEqual(k(ev("b", "POL 101", 0, 1)), "class")
        self.assertEqual(k(ev("c", "BUS 305 lecture", 0, 1)), "class")
        self.assertEqual(k(ev("d", "Church sound", 0, 1)), "church")
        self.assertEqual(k(ev("e", "Banner review", 0, 1, attendees=[person("Dana", "karen@blueheron.test")])),
                         "meeting")
        self.assertEqual(k(ev("f", "Client call", 0, 1)), "meeting")
        self.assertIsNone(k(ev("g", "Lunch with Riley", 0, 1)))
        self.assertIsNone(k(ev("h", "Church picnic", 0, 1, all_day=True)))
        self.assertIsNone(k(ev("i", "corner store shift", 0, 1, my_response="declined")))
        self.assertIsNone(k(ev("j", "POL 101 paper due", 0, 1)))
        self.assertIsNone(k(ev("k", "Planning", 0, 1, attendees=[person("Me", "jdoe1@example.com"),
                                                                     person("Room", "c_123@resource.calendar.google.com")])))

    def test_pushes_wait_for_a_meeting_with_someone_outside_to_end(self):
        self.write_calendar([ev("m", "Banner review", at(10, 8, 9, 30), at(10, 8, 11),
                                attendees=[person("Dana", "karen@blueheron.test")])])
        self.raise_(self.cand("x", deadline=at(10, 9, 9)))
        self.assertEqual(run(notices.deliver()), 0)
        self.assertEqual(self.notice("x")["push_after"], at(10, 8, 11))
        self.assertEqual(notices.status()["busy"], {"title": "Banner review", "until": at(10, 8, 11)})
        self.clock = at(10, 8, 11, 1)
        self.assertEqual(run(notices.deliver()), 1)

    def test_alerts_during_a_shift_are_told_once_it_ends(self):
        self.write_calendar([ev("s", "corner store shift", at(10, 8, 9), at(10, 8, 14))])
        sent = []

        async def push(title, body, bot_id=None, bots=None, urgent=False, kind=None, extra=None):
            sent.append((title, body))
            return True
        with mock.patch.object(watcher, "push", push), mock.patch.object(watcher, "_night", lambda: False):
            self.assertTrue(run(notices.nudge("task:t1:blocked", "School needs you", "Form C", "school")))
            self.assertTrue(run(notices.nudge("task:t2:review", "Clients wants your review", "Dana", "clients")))
            self.assertEqual(sent, [])
            self.assertFalse(run(notices.release_busy_held()))
            self.clock = at(10, 8, 14, 5)
            self.assertTrue(run(notices.release_busy_held()))
            [(title, body)] = sent
            self.assertEqual(title, "While you were busy")
            self.assertRegex(body, r"^While you were busy, 2 updates came in from \w+ and \w+\.$")
            self.assertFalse(run(notices.release_busy_held()))
        self.assertEqual(notices._count("nudge_push", notices.day_start()), 1)   # one notification on the budget


# MARK: The calendar wake

class CalendarWake(P2Case):
    def test_calendar_and_trello_changes_run_the_noticers_that_read_them(self):
        calls = []

        def fake(name):
            async def f(src):
                calls.append(name)
                return []
            return f
        with mock.patch.object(noticers, "finders", lambda: {k: fake(k) for k in noticers.WAKES["quiet"]}):
            run(noticers.find({"calendar"}, self.src))
            self.assertEqual(sorted(calls), ["conflict", "due_soon", "followup", "prep", "school_deadline"])
            calls.clear()
            run(noticers.find({"trello"}, self.src))
            self.assertEqual(calls, ["school_deadline", "due_soon"])
            calls.clear()
            run(noticers.find({"call"}, self.src))
            self.assertEqual(calls, ["followup"])

    def test_the_tick_turns_a_changed_copy_into_a_wake(self):
        passes = []

        async def run_pass(wakes):
            passes.append(set(wakes))
            return 0

        async def no_mail():
            return False

        async def go():
            await notices.tick()
            if notices._pass_task:
                await notices._pass_task
        with mock.patch.object(notices, "run_pass", run_pass), mock.patch.object(noticers, "check_new_mail", no_mail):
            run(go())
            self.write_calendar([ev("s", "corner store shift", at(10, 8, 15), at(10, 8, 20))])
            self.clock += 61
            run(go())
        self.assertIn("calendar", passes[-1])


# MARK: The new noticers

class SchoolDeadlines(P2Case):
    def setUp(self):
        super().setUp()
        self.src.trello_cards = [{"id": "c1", "name": "POL 101 reflection paper", "board": "Colin | School & Projects",
                                  "list": "Next", "due": at(10, 9, 23, 55), "due_complete": False, "labels": []}]
        self.src.events = [ev("w", "corner store shift", at(10, 8, 11), at(10, 8, 19))]

    def test_due_inside_48_hours_with_nothing_behind_it(self):
        [c] = self.find("school_deadline")
        self.assertEqual(c.owner, "school")
        self.assertEqual(c.deadline, at(10, 9, 23, 55))
        self.assertEqual(c.options[0].text, "Block today at 7 p.m.")      # after the shift, before 10 p.m.
        self.assertIn("today, Oct 8", c.options[0].say.replace("Thursday, ", "today, "))
        self.assertEqual([o.text for o in c.options[1:]], ["Already submitted", "I'll handle it"])
        self.assertIn("according to Trello", c.evidence)
        self.assertIsNone(notices.check(c))

    def test_something_behind_it_means_no_question(self):
        self.src.trello_cards.append({"id": "c2", "name": "Write POL 101 reflection", "list": "Doing (one task)",
                                      "due": None})
        self.assertEqual(self.find("school_deadline"), [])
        self.src.trello_cards.pop()
        self.src.events.append(ev("b", "Work on POL 101 reflection", at(10, 9, 19), at(10, 9, 20)))
        self.assertEqual(self.find("school_deadline"), [])
        self.src.events.pop()
        self.src.groups["working"] = [{"id": "t1", "title": "POL 101 reflection paper outline", "status": "running",
                                       "created_at": self.clock - 3600}]
        self.assertEqual(self.find("school_deadline"), [])
        self.src.groups["working"] = []
        self.src.trello_cards[0]["due_complete"] = True
        self.assertEqual(self.find("school_deadline"), [])

    def test_only_inside_48_hours_and_only_schoolwork(self):
        self.src.trello_cards[0]["due"] = at(10, 11, 23, 55)
        self.assertEqual(self.find("school_deadline"), [])
        self.src.trello_cards = [{"id": "c3", "name": "Renew lumenly.app", "list": "Next", "due": at(10, 9, 12)}]
        self.assertEqual(self.find("school_deadline"), [])

    def test_from_schools_notes_and_the_calendar(self):
        self.src.trello_cards = []
        self.src.zip_notes["school"] = ["Form C for the Writing Lab is due Oct 9 at 5 p.m. and needs Colin's "
                                        "signature."]
        [c] = self.find("school_deadline")
        self.assertEqual(c.deadline, at(10, 9, 17))
        self.assertEqual(c.facts["what"], "Form C for the Writing Lab")
        self.src.zip_notes = {}
        self.src.events.append(ev("d", "BUS 305 quiz due", at(10, 9, 0), at(10, 10, 0), all_day=True))
        [c] = self.find("school_deadline")
        self.assertEqual(c.facts["what"], "BUS 305 quiz")
        self.assertEqual(c.deadline, at(10, 9, 23, 55))


class Conflicts(P2Case):
    def test_an_overlap(self):
        self.src.events = [ev("u", "corner store shift", at(10, 11, 8), at(10, 11, 14)),
                           ev("c", "Church sound", at(10, 11, 8, 30), at(10, 11, 12))]
        [c] = self.find("conflict")
        self.assertEqual(c.subject, "conflict:c:u")
        self.assertEqual([o.text for o in c.options], ["Keep corner store shift", "Keep Church sound",
                                                       "I'll sort it out"])
        self.assertIn("I want to keep corner store shift and not Church sound", c.options[0].say)
        self.assertIn("Sunday you have corner store shift from 8 a.m. to 2 p.m. and Church sound from 8:30 a.m. to "
                      "12 p.m., and they overlap.", c.evidence)
        self.assertEqual(c.question, "Which one do you want to keep?")
        self.assertFalse(c.haiku)
        self.assertIsNone(notices.check(c))

    def test_an_impossible_gap_needs_two_real_places(self):
        self.src.events = [ev("a", "Church sound", at(10, 11, 8), at(10, 11, 12), location="Grace Church, Springfield"),
                           ev("b", "corner store shift", at(10, 11, 12, 15), at(10, 11, 17), location="The corner store")]
        [c] = self.find("conflict")
        self.assertTrue(c.subject.startswith("gap:"))
        self.assertIn("That leaves 15 minutes to get there.", c.evidence)
        self.src.events[1]["location"] = "https://zoom.us/j/123"
        self.assertEqual(self.find("conflict"), [])
        self.src.events[1]["location"] = "Grace Church"
        self.assertEqual(self.find("conflict"), [])
        self.src.events[1].update(location="The corner store", start=at(10, 11, 12, 45))
        self.assertEqual(self.find("conflict"), [])          # 45 minutes is room enough

    def test_declined_all_day_and_doubled_events_are_not_clashes(self):
        self.src.events = [ev("u", "corner store shift", at(10, 11, 8), at(10, 11, 14)),
                           ev("c", "Team call", at(10, 11, 9), at(10, 11, 10), my_response="declined"),
                           ev("p", "Church picnic", at(10, 11, 0), at(10, 12, 0), all_day=True),
                           ev("u2", "corner store shift", at(10, 11, 8), at(10, 11, 14))]
        self.assertEqual(self.find("conflict"), [])


class Prep(P2Case):
    def setUp(self):
        super().setUp()
        self.src.events = [ev("adv", "Advising with Andrea Stone", at(10, 9, 14), at(10, 9, 14, 30),
                              attendees=[person("Andrea Stone", "astone@lakeview.test")])]

    def test_the_evening_before(self):
        self.assertEqual(self.find("prep"), [])               # 10 a.m. the day before: not yet
        self.clock = at(10, 8, 19)
        [c] = self.find("prep")
        self.assertEqual((c.owner, c.version, c.subject), ("school", "eve", "prep:adv"))
        self.assertTrue(c.event_timed)
        self.assertEqual([o.text for o in c.options], ["Make the prep", "Skip it"])
        self.assertIn("one-page prep for Advising with Andrea Stone Friday, Oct 9 at 2 p.m.", c.options[0].say)
        self.assertIn("prep format from my notes", c.options[0].say)

    def test_two_hours_before_and_a_promise_for_it(self):
        self.src.chats["default"] = [{"class": "colin", "role": "user", "ts": at(10, 7, 20),
                                      "display": "I'll bring the spring options to advising with Andrea on Friday."}]
        self.clock = at(10, 9, 12, 5)
        [c] = self.find("prep")
        self.assertEqual(c.version, "day")
        self.assertIn("You said you'd bring the spring options to advising with Andrea", c.evidence)
        self.assertIn("I said I'd bring the spring options", c.options[0].say)

    def test_no_question_when_prep_exists_or_he_answered(self):
        self.clock = at(10, 8, 19)
        self.src.files = [("school", "advising-prep-andrea-2026-10-09.md", self.clock - 3600)]
        self.assertEqual(self.find("prep"), [])
        self.src.files = []
        self.raise_(*self.find("prep"))
        notices.answer(notices.row("prep:adv")["feed_id"], "Skip it")
        self.clock = at(10, 9, 12, 5)
        self.assertEqual(self.find("prep"), [])               # skipped the evening before: no day-of question

    def test_shifts_classes_and_appointments_are_not_meetings(self):
        self.clock = at(10, 8, 19)
        self.src.events = [ev("s", "corner store shift", at(10, 9, 9), at(10, 9, 14)),
                           ev("c", "POL 101", at(10, 9, 15), at(10, 9, 16)),
                           ev("d", "Dentist appointment", at(10, 9, 10), at(10, 9, 11))]
        self.assertEqual(self.find("prep"), [])


class FollowUps(P2Case):
    def setUp(self):
        super().setUp()
        self.src.events = [ev("k", "Banner review", at(10, 8, 9), at(10, 8, 9, 50),
                              attendees=[person("Dana Miller", "karen@blueheron.test")])]

    def test_right_after_a_meeting_with_nothing_said_about_it(self):
        [c] = self.find("followup")
        self.assertEqual(c.subject, "followup:k")
        self.assertIsNone(c.draft)
        self.assertIsNone(c.prepare)
        self.assertEqual(c.options[0].text, "Draft a follow-up")
        self.assertIn("Ask me what came up", c.options[0].say)
        self.clock = at(10, 8, 10, 30)
        self.assertEqual(self.find("followup"), [])            # only in the half hour after

    def test_with_his_notes_and_someone_he_wrote_to_the_recap_is_prepared(self):
        self.src.chats["default"] = [{"class": "colin", "role": "user", "ts": at(10, 8, 9, 55),
                                      "display": "Dana wants the smaller banner by Friday and a quote for the "
                                                 "holiday page."}]
        self.src.wrote.add("karen@blueheron.test")
        [c] = self.find("followup")
        self.assertEqual(c.draft["to_addr"], "karen@blueheron.test")
        self.assertEqual(c.prepare, {"bot": "clients", "label": "Send the recap as written"})
        self.assertIn("smaller banner", c.excerpt)
        self.src.wrote.clear()
        [c] = self.find("followup")
        self.assertIsNotNone(c.draft)
        self.assertIsNone(c.prepare)                           # never prepared for someone he hasn't written to

    def test_after_a_call_with_a_next_step(self):
        self.src.events = []
        self.src.call_rows = [{"id": 77, "bot_id": "career", "at": self.clock - 600,
                               "summary": "We went over the offer.\n\n- **Acme Homes:** Waiting on Mark.\n"
                                          "- **Next:** Career drafts the questions for Mark by Friday."},
                              {"id": 78, "bot_id": "school", "at": self.clock - 300, "summary": "Just a hello."}]
        [c] = self.find("followup")
        self.assertEqual((c.subject, c.owner), ("call:77", "career"))
        self.assertIn("Career drafts the questions for Mark by Friday", c.evidence)
        self.assertIsNone(notices.check(c))


class Forms(P2Case):
    def setUp(self):
        super().setUp()
        self.src.mail[("personal", "INBOX")] = [
            env(1, "Please DocuSign: Year-end mailer approval", frm=("Matt via Docusign", "dse@docusign.net"),
                date=at(10, 7, 9)),
            env(2, "You're invited: 50% off ends Friday", frm=("Deals", "news@shop.example"), date=at(10, 7, 9))]
        self.src.bodies = {"1": "Matt sent you a document to review and sign. This envelope expires on Oct 10, 2026.",
                           "2": "Hurry, this offer expires Oct 10."}

    def test_a_signature_request_with_an_expiry(self):
        [c] = self.find("form")
        self.assertEqual(c.deadline, at(10, 10, 17))
        self.assertEqual([o.text for o in c.options], ["Show me the link", "Remind me Friday", "Already done"])
        self.assertIn("Don't sign, fill in, answer or submit anything.", c.options[0].say)
        self.assertIn("signature request", c.evidence)
        self.assertIsNone(notices.check(c))

    def test_a_completed_notice_after_it_closes_it(self):
        self.src.mail[("personal", "INBOX")].append(
            env(3, "Completed: Please DocuSign: Year-end mailer approval", frm=("Docusign", "dse@docusign.net"),
                date=at(10, 7, 15)))
        self.assertEqual(self.find("form"), [])


class Charges(P2Case):
    def buy(self, i, name, amount, date, merchant=None):
        return {"id": f"tx{i}", "name": name, "merchant": merchant or name.lower(), "amount": amount, "date": date}

    def test_a_place_money_has_never_seen(self):
        self.src.buys = [self.buy(1, "Scribd", 14.99, "2026-10-08"), self.buy(2, "Wawa", 8.12, "2026-10-08"),
                         self.buy(3, "Wawa", 9.40, "2026-09-20"), self.buy(4, "Venmo to Jake", 40, "2026-10-08")]
        [c] = self.find("charge")
        self.assertEqual(c.subject, "charge:tx1")
        self.assertTrue(c.sensitive)
        self.assertEqual([o.text for o in c.options], ["Yes, it's mine", "Look into it"])
        self.assertIn("Don't contact the bank or change anything", c.options[1].say)
        self.assertIsNone(notices.check(c))
        self.src.zip_notes["money"] = ["Scribd is Colin's reading subscription, $14.99 a month."]
        self.assertEqual(self.find("charge"), [])

    def test_a_regular_charge_that_went_up(self):
        self.src.buys = [self.buy(1, "Netflix", 17.99, "2026-10-07"), self.buy(2, "Netflix", 15.49, "2026-09-07"),
                         self.buy(3, "Netflix", 15.49, "2026-08-07")]
        [c] = self.find("charge")
        self.assertEqual(c.subject, "charge-up:netflix:2026-10")
        self.assertIn("from $15.49 to $17.99", c.evidence)

    def test_a_new_charge_waits_in_the_chat_without_an_alarm(self):
        """Fluid chat (#70): a new place in the spending is a calm filing question in the chat and the brief, never a
        push and never "fraud"; its lock line, if it were ever shown, says nothing specific."""
        self.src.buys = [self.buy(1, "Scribd", 14.99, "2026-10-08")]
        [c] = self.find("charge")
        self.assertIsNone(c.deadline)
        self.assertNotRegex(c.evidence + c.question + c.consequence, r"(?i)fraud|recogni|suspicious")
        self.assertIn("Your card shows", c.evidence)
        self.raise_(c)
        self.assertEqual(run(notices.deliver()), 0)
        self.assertEqual(self.pushes, [])


class Bills(P2Case):
    def setUp(self):
        super().setUp()
        self.src.zip_notes["money"] = ["Riley's car payment $292 to Chase Automotive, due on the 11th every month."]

    def test_due_in_three_days_with_nothing_paid(self):
        [c] = self.find("bill")
        self.assertEqual(c.deadline, at(10, 11, 17))
        self.assertEqual([o.text for o in c.options], ["It's on autopay", "I'll pay it Saturday", "Already paid"])
        self.assertIn("Riley's car payment of $292.00 is due Sunday, Oct 11", c.evidence)
        self.assertIsNone(notices.check(c))

    def test_paid_or_on_autopay_means_no_question(self):
        self.src.buys = [{"id": "p", "name": "Chase Automotive", "amount": 292.0, "date": "2026-10-06"}]
        self.assertEqual(self.find("bill"), [])
        self.src.buys = []
        self.src.zip_notes["money"] = ["Rent $1,450 due on the 10th, on autopay from checking."]
        self.assertEqual(self.find("bill"), [])

    def test_no_option_ever_pays(self):
        bad = [Option("Pay it now", "act", say="Please pay the car payment."), Option("Later", "done")]
        c = Candidate("bill", "bill:x", "money", "e", "q?", bad, facts={"what": "x"}, deadline=self.soon())
        self.assertEqual(notices.check(c), "an option asks for something Zips never do")


class Windows(P2Case):
    def setUp(self):
        super().setUp()
        self.src.zip_notes["school"] = ["Spring registration runs Nov 2 to 13; advising comes first.",
                                        "Advising window with Andrea Stone: Oct 5 to 28."]

    def test_opening_and_closing(self):
        self.clock = at(10, 30, 10)
        [c] = self.find("window")
        self.assertEqual(c.evidence, "Spring registration opens Monday, Nov 2, according to School's notes.")
        self.assertEqual(c.deadline, at(11, 2, 9))
        self.assertEqual([o.text for o in c.options], ["Get me ready for it", "I've got it"])
        self.clock = at(10, 26, 10)
        [c] = self.find("window")
        self.assertTrue(c.evidence.startswith("Advising window with Andrea Stone closes Wednesday, Oct 28"))
        self.clock = at(11, 11, 10)
        [c] = self.find("window")
        self.assertIn("closes Friday, Nov 13", c.evidence)
        self.clock = at(10, 15, 10)
        self.assertEqual(self.find("window"), [])


# MARK: Prepared actions

class Prepared(P2Case):
    """The owning Zip prepares the exact send before Zero asks; the tap in the app is Allow Once on that card."""

    def setUp(self):
        super().setUp()
        try:
            from test_email_tool import EmailCase
            from test_gate import gate, plugin
        except Exception as e:   # the plugin is in this repo; on alien it is the installed copy
            self.skipTest(f"the taffy-tools plugin is not loadable here: {e}")

        class Case(EmailCase):
            def runTest(self):
                pass
        self.case = Case()
        self.case.bot = "clients"
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.addCleanup(self.case.doCleanups)
        self.gate, self.plugin = gate, plugin
        gate._lane = lambda sid: {"prep-1": "prep", "quiet-1": "quiet"}.get(sid, "")
        self.turns = []
        self.body = None
        self.target = "Theo Avery <ken@brightpath.test>"

        async def zip_turn(bot_id, text):
            self.turns.append((bot_id, text))
            return self.call()
        p = mock.patch.object(prepared, "run_turn", zip_turn)
        p.start()
        self.addCleanup(p.stop)

    def args(self, body=None, to=None):
        return {"action": "send", "account": "personal", "to": [to or self.target],
                "subject": "Re: Brightpath handoff", "body": body or self.body}

    def call(self, **kw):
        """The Zip's email tool call in its prep session, through the gate first."""
        from test_gate import tt
        args = self.args(**kw)
        blocked = self.gate.pre_tool_call("email", args, session_id="prep-1")
        if blocked:
            return blocked["message"]
        return json.dumps(json.loads(tt.email_tool.email_tool(args, session_id="prep-1")))

    def raise_ken(self):
        self.src.mail[("personal", "Sent")] = [env(1, "Brightpath handoff", to=[KEN], date=at(10, 1, 9),
                                                   folder="Sent")]
        self.src.bodies["1"] = "Hi Theo, can you send the handoff notes?"
        [c] = self.find("unreplied")
        self.assertEqual(c.prepare, {"bot": "clients"})
        self.raise_(c)
        r = notices.row(c.subject)
        self.body = json.loads(r["prep"])["body"] if r["prep"] else None
        return c.subject

    def ready(self):
        subject = self.raise_ken()
        self.assertEqual(notices.row(subject)["state"], "preparing")
        self.assertIsNone(notices.row(subject)["feed_id"])     # held back until the Zip has prepared it
        run(prepared.prepare(notices.row(subject)["id"]))
        return subject

    def test_prepared_then_asked_with_exactly_what_will_be_sent(self):
        subject = self.ready()
        bot, note = self.turns[0]
        self.assertEqual(bot, "clients")
        self.assertIn('to "Theo Avery <ken@brightpath.test>"', note)
        self.assertIn(self.body, note)
        r = notices.row(subject)
        self.assertEqual(r["state"], "open")
        card = self.card(subject)
        self.assertEqual(card["options"], ["Send this nudge as written", "Wait another week",
                                           "I already heard from them"])
        self.assertEqual(card["prepared"]["to"], "ken@brightpath.test")
        self.assertEqual(card["detail"]["label"], "what will be sent")
        req = consent.get_request(card["prepared"]["request"])
        self.assertEqual((req["why"], req["kind"], req["scope"], req["status"]), ("prepared", "send", "", "pending"))
        self.assertEqual(card["detail"]["text"], req["inputs"])
        self.assertIn("To: Theo Avery <ken@brightpath.test>", req["inputs"])
        self.assertIn(self.body.splitlines()[-1], req["inputs"])
        self.assertEqual(self.case.fake.sent(), [])                         # preparing sent nothing
        # Hidden: not in Work, not on the badge, not pushed.
        self.assertNotIn(req["id"], [x["id"] for x in consent.pending()])
        self.assertNotIn(req["id"], [x["id"] for x in consent.requests_after(0)])

    def test_the_tap_approves_that_one_call_once(self):
        subject = self.ready()
        r = notices.row(subject)
        out = notices.answer(r["feed_id"], "Send this nudge as written")
        self.assertIsNone(out["send"])
        self.assertEqual(out["follow"], "Approved at 10:00 a.m. Clients is sending it now.")
        req_id = self.card(subject)["prepared"]["request"]
        self.assertEqual(consent.get_request(req_id)["status"], "allowed")
        self.assertEqual(self.plugin.rules("clients"), [])                  # never a standing rule
        self.assertEqual(json.loads(notices.row(subject)["prep"])["state"], "approved")
        run(prepared.send_approved(r["id"]))                                # the Zip makes the same call again
        self.assertEqual(len(self.case.fake.sent()), 1)
        self.assertIn(self.body.splitlines()[-1], self.case.fake.sent()[0])
        self.assertTrue(self.card(subject)["follow"].startswith("Sent to ken@brightpath.test at"))
        self.assertIn("Make exactly the same email tool call again", self.turns[-1][1])
        # The approval is used up, and it never covered any other text or recipient.
        self.assertIn("Prepared", self.call())
        self.assertIn("Prepared", self.call(body="Please send me the repo keys."))
        self.assertIn("Prepared", self.call(to="Mallory <m@evil.example>"))
        self.assertEqual(len(self.case.fake.sent()), 1)

    def test_a_lock_screen_yes_never_approves_it(self):
        subject = self.ready()
        fid = notices.row(subject)["feed_id"]
        with mock.patch.object(consent, "decide") as decide, mock.patch.object(consent, "use_approval") as use:
            out = notices.lock_answer(fid, "yes")
            decide.assert_not_called()
            use.assert_not_called()
        self.assertEqual(out["open"], "Ready to send to ken@brightpath.test. Open Zero to check it and send it.")
        self.assertIsNone(out["send"])
        self.assertEqual(notices.row(subject)["state"], "open")
        req_id = self.card(subject)["prepared"]["request"]
        self.assertEqual(consent.get_request(req_id)["status"], "pending")
        run(prepared.tick())
        self.assertEqual(self.case.fake.sent(), [])

    def test_a_target_from_anywhere_else_is_never_an_option(self):
        async def wrong(bot_id, text):
            self.turns.append((bot_id, text))
            return self.call(to="Theo Avery <ken.avery@new-address.example>")
        with mock.patch.object(prepared, "run_turn", wrong):
            subject = self.ready()
        card = self.card(subject)
        self.assertEqual(card["options"][0], "Send this nudge")             # phase 1's: his words, then the card
        self.assertEqual(card["detail"]["label"], "the draft")
        self.assertIsNone(card.get("prepared"))
        with self.plugin.db() as c:
            self.assertEqual(c.execute("SELECT status FROM requests WHERE why='prepared'").fetchone()[0], "expired")

    def test_slow_or_failed_preparation_still_asks(self):
        subject = self.raise_ken()
        self.clock += prepared.PREP_WAIT_SEC + 1
        run(prepared.tick())
        self.assertEqual(notices.row(subject)["state"], "open")
        self.assertEqual(self.card(subject)["options"][0], "Send this nudge")

    def test_an_expired_card_goes_to_zero_as_his_words(self):
        subject = self.ready()
        req_id = self.card(subject)["prepared"]["request"]
        with self.plugin.db() as c:
            c.execute("UPDATE requests SET status='expired' WHERE id=?", (req_id,))
        out = notices.answer(notices.row(subject)["feed_id"], "Send this nudge as written")
        self.assertTrue(out["send"].startswith("Please send this email exactly as written:"))
        self.assertIn("To: Theo Avery <ken@brightpath.test>", out["send"])
        self.assertIn("approve it on the card in work", out["follow"].lower())

    def test_five_a_day_and_waking_hours_only(self):
        for i in range(5):
            notices._log("prep", "unreplied", f"s{i}")
        self.assertFalse(prepared.allowed())
        subject = self.raise_ken()
        self.assertEqual(notices.row(subject)["state"], "open")             # asked at once, without preparing
        store.db().execute("DELETE FROM notice_log WHERE kind='prep'")
        store.db().commit()
        self.clock = at(10, 8, 21, 30)
        self.assertFalse(prepared.allowed())
        self.clock = at(10, 8, 10)
        with mock.patch.dict(health.status, {"claude": False}):
            self.assertFalse(prepared.allowed())
        self.assertTrue(prepared.allowed())

    def test_the_gate_in_a_prep_session(self):
        # A standing rule for this recipient never sends during preparation.
        self.plugin.set_rule("clients", "send", "ken@brightpath.test", source="test")
        self.body = "Hi Theo, any update?"
        self.assertIn("Prepared", self.call())
        self.assertEqual(self.case.fake.sent(), [])
        # A change is refused, and a quiet look takes no actions and raises no cards.
        blocked = self.gate.pre_tool_call("connectors", {"service": "calendar", "request": "create an event called "
                                                         "Form C at 7 pm Friday"}, session_id="prep-1")
        self.assertIn("this is preparation", blocked["message"])
        before = len(self.case.cards())
        blocked = self.gate.pre_tool_call("email", self.args(), session_id="quiet-1")
        self.assertIn("quiet look", blocked["message"])
        self.assertEqual(len(self.case.cards()), before)


# MARK: Answers from the lock screen

class LockScreen(P2Case):
    def act_question(self, subject="r1"):
        self.raise_(self.cand(subject, deadline=self.soon(3), options=[
            Option("Find the cancel page", "act", say="Find the cancel page for agentmax.app."),
            Option("Keep it", "decide", say="Keep it."), Option("Already canceled", "close")]))
        return notices.row(subject)

    def test_categories(self):
        self.assertEqual(notices.category({"options": [{"kind": "act"}]}), notices.QUESTION)
        self.assertEqual(notices.category({"options": [{"kind": "send"}]}), notices.OPEN_ONLY)
        self.assertEqual(notices.category({"options": [{"kind": "prepared"}]}), notices.OPEN_ONLY)
        self.assertEqual(notices.category({"options": [{"kind": "act"}], "sensitive": True}), notices.OPEN_ONLY)
        self.assertEqual(notices.category({"options": [{"kind": "hub", "hub": "approve_task"}]}), notices.OPEN_ONLY)
        self.assertEqual(notices.category({"options": [{"kind": "hub", "hub": "deny_card"}]}), notices.QUESTION)
        self.act_question("a")
        self.act_question("b")
        self.raise_(self.cand("c", noticer="promise", deadline=self.soon(3)))
        run(notices.deliver())
        self.assertEqual(self.pushes[-1]["category"], notices.OPEN_ONLY)      # several in one: Open only

    def test_yes_answers_with_the_first_option_as_his_words(self):
        r = self.act_question()
        run(notices.deliver())
        self.assertEqual(self.pushes[0]["category"], notices.QUESTION)
        out = notices.lock_answer(r["feed_id"], "yes")
        self.assertEqual(out["send"], "Find the cancel page for agentmax.app.")
        self.assertEqual(out["picked"], "Find the cancel page")
        self.assertEqual(notices.row("r1")["state"], "picked")

    def test_yes_on_a_send_or_money_question_never_answers(self):
        from taffy_hub import choices
        c = noticers._unreplied("personal", {"subject": "Brightpath handoff", "date": at(10, 1, 9), "who": KEN},
                                "ken@brightpath.test", "brightpath handoff")
        c.prepare = None
        self.raise_(c)
        r = notices.row(c.subject)
        self.assertEqual(json.loads(r["card"])["options"][0]["kind"], "send")
        with mock.patch.object(consent, "use_approval") as use, mock.patch.object(consent, "decide") as decide:
            out = notices.lock_answer(r["feed_id"], "yes")
            use.assert_not_called()
            decide.assert_not_called()
        self.assertIsNone(out["send"])
        self.assertIn("Ready to send to ken@brightpath.test", out["open"])
        self.assertEqual(notices.row(c.subject)["state"], "open")
        out = choices.answer("default", r["feed_id"], "Send this nudge")    # in the app, with the text on screen
        self.assertIn("exactly as written", out["send"])
        self.raise_(self.cand("money", deadline=self.soon(3), sensitive=True))
        out = notices.lock_answer(notices.row("money")["feed_id"], "yes")
        self.assertEqual(out["open"], "Open Zero to see this question.")
        self.assertEqual(notices.row("money")["state"], "open")

    def test_not_now_keeps_it_for_later_without_learning_a_dismissal(self):
        for i in range(3):
            r = self.act_question(f"n{i}")
            out = notices.lock_answer(r["feed_id"], "not_now")
            self.assertIsNone(out["send"])
            r = notices.row(f"n{i}")
            self.assertEqual((r["state"], r["push_due"]), ("open", 0))
            self.assertGreaterEqual(r["pushes"], 2)                          # no more notifications about it
        self.assertEqual(notices.mode("renewal"), "on")

    def test_reply_is_his_own_answer(self):
        r = self.act_question()
        out = notices.lock_answer(r["feed_id"], "reply", "  Cancel it after the trial  ")
        self.assertEqual(out["send"], 'About your question "Haiku question?": Cancel it after the trial')
        with self.assertRaises(notices.AnswerError):
            notices.lock_answer(self.act_question("r2")["feed_id"], "reply", " ")
        with self.assertRaises(notices.AnswerError):
            notices.lock_answer(r["feed_id"], "yes")                        # already answered


class LockScreenRoute(P2Case):
    def setUp(self):
        super().setUp()
        from test_onboarding import _load_app
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.app.app.dependency_overrides[self.app.require_device] = lambda: "Test iPhone"
        self.addCleanup(self.app.app.dependency_overrides.clear)
        self.client = TestClient(self.app.app)
        self.sent = []
        p = mock.patch.object(self.app.turns, "send", lambda bot, text: self.sent.append((bot, text)))
        p.start()
        self.addCleanup(p.stop)

    def test_yes_sends_his_words_and_a_send_opens_the_card(self):
        self.raise_(self.cand("r", deadline=self.soon(3)))
        fid = notices.row("r")["feed_id"]
        out = self.client.post(f"/api/bots/default/choices/{fid}/lock", json={"action": "yes"}).json()
        self.assertEqual(out["card"]["state"], "picked")
        self.assertEqual(self.sent, [("default", "Keep agentmax.app.")])
        c = noticers._unreplied("personal", {"subject": "Handoff", "date": at(10, 1, 9), "who": KEN},
                                "ken@brightpath.test", "handoff")
        c.prepare = None
        self.raise_(c)
        fid = notices.row(c.subject)["feed_id"]
        self.pushes.clear()
        out = self.client.post(f"/api/bots/default/choices/{fid}/lock", json={"action": "yes"}).json()
        self.assertTrue(out["opened"])
        self.assertEqual(len(self.sent), 1)                                 # nothing went to Zero
        self.assertEqual(self.pushes[0]["category"], notices.OPEN_ONLY)
        self.assertIn("Ready to send to ken@brightpath.test", self.pushes[0]["body"])
        self.assertEqual(self.client.post(f"/api/bots/default/choices/{fid}/lock",
                                          json={"action": "maybe"}).status_code, 409)


# MARK: The quiet look

QUIET_REPLY = json.dumps({"raise": [
    {"subject": "dog-sitter-oct-17", "owner": "default",
     "evidence": "You and Riley leave for Chicago on Oct 9 and nothing shows who is watching Pepper.",
     "question": "Want me to find a sitter, or is it handled?", "options": ["Find a sitter", "Riley has it"],
     "deadline": "2026-10-09 08:00", "consequence": "", "about": "Pepper"},
    {"subject": "pay-sitter", "owner": "money", "evidence": "The sitter wants a deposit.",
     "question": "Pay the deposit?", "options": ["Pay the sitter deposit", "Later"], "deadline": "2026-10-09",
     "about": "deposit"},
    {"subject": "third", "evidence": "x", "question": "y?", "options": ["a", "b"], "deadline": "2026-10-09"}]})


class QuietLook(P2Case):
    def setUp(self):
        super().setUp()
        self.prompts = []

        async def zero(text):
            self.prompts.append(text)
            return "Here is what I found:\n" + QUIET_REPLY
        p = mock.patch.object(quietlook, "run_turn", zero)
        p.start()
        self.addCleanup(p.stop)

        async def nothing(src):
            return []
        f = mock.patch.object(noticers, "finders", lambda: {k: nothing for k in noticers.WAKES["quiet"]})
        f.start()
        self.addCleanup(f.stop)

    def test_it_can_raise_what_no_noticer_covers_inside_the_rules(self):
        self.write_calendar([ev("t", "Chicago trip", at(10, 9, 9), at(10, 11, 18))])
        self.clock -= 25 * 3600
        self.raise_(self.cand("open-one", deadline=self.soon(96)))      # a day old: the next card may take its place
        self.clock += 25 * 3600
        run(notices.run_pass({"quiet"}))
        prompt = self.prompts[0]
        self.assertIn("Chicago trip", prompt)
        self.assertIn("Haiku question?", prompt)                        # the open questions, so it doesn't repeat
        self.assertIn("You only read here", prompt)
        r = notices.row("quiet:dog-sitter-oct-17")
        card = json.loads(r["card"])
        self.assertEqual([o["text"] for o in card["options"]], ["Find a sitter", "Riley has it"])
        self.assertEqual(card["options"][0]["kind"], "act")
        self.assertEqual(card["options"][1]["kind"], "done")             # a closing answer
        self.assertEqual(card["evidence"], "You and Riley leave for Chicago on Oct 9 and nothing shows who is "
                                           "watching Pepper.")
        self.assertEqual(len(self.haiku.prompts), 2)                     # "open-one", then the gate on Zero's words
        self.assertIn("Would a sharp human assistant interrupt", self.haiku.prompts[1])
        self.assertIsNone(notices.row("quiet:pay-sitter"))               # Zips never pay
        self.assertIsNone(notices.row("quiet:third"))                    # two at most
        self.assertEqual(len(self.pushes), 1)                             # inside 24 hours, so it was pushed

    def test_what_it_raises_stays_inside_the_budget(self):
        for i in range(3):
            notices._log("push", "renewal", f"earlier{i}")
        run(notices.run_pass({"quiet"}))
        r = notices.row("quiet:dog-sitter-oct-17")
        self.assertEqual(r["state"], "open")
        self.assertEqual(self.pushes, [])                                 # the day's three were used
        self.assertEqual(r["push_due"], 0)
        self.prompts.clear()
        run(notices.run_pass({"quiet"}))
        self.assertEqual(len([x for x in notices._open_rows() if x["noticer"] == "quiet"]), 1)   # one per subject

    def test_off_in_settings_or_at_the_plan_limit_means_no_turn(self):
        notices.set_mode("quiet", "off")
        run(notices.run_pass({"quiet"}))
        notices.set_mode("quiet", "on")
        with mock.patch.dict(health.status, {"claude": False}):
            run(notices.run_pass({"quiet"}))
        self.assertEqual(self.prompts, [])
        run(notices.run_pass({"timer"}))
        self.assertEqual(self.prompts, [])                                # only a quiet wake looks

    def test_a_session_apart_from_his_chat(self):
        lanes = []

        async def run_turn(bot_id, text, team=False, lane=None):
            lanes.append((bot_id, lane))
            return '{"raise": []}'
        with mock.patch.object(hermes, "run_turn", run_turn):
            run(REAL_QUIET_TURN("look"))
            run(REAL_PREP_TURN("clients", "prepare"))
        self.assertEqual(lanes, [("default", "quiet"), ("clients", "prep")])
        self.assertEqual(hermes.session_key("default", "quiet"), "default#quiet")
        self.assertEqual(hermes.LANES["prep"], "preparing drafts")

    def test_at_most_the_setting_a_day(self):
        s = notices.settings()
        s["quiet_wakes"] = 2
        notices._save(s)
        self.clock = at(10, 8, 9)
        notices.chat_activity("default")
        got = []
        for hour, minute in ((11, 1), (13, 10), (15, 20), (17, 30)):
            self.clock = at(10, 8, hour, minute)
            got.append(notices.quiet_wake_due())
            self.clock += 60
            notices.chat_activity("default")
        self.assertEqual(got, [True, True, False, False])
        self.assertEqual(notices._count("quiet_wake", notices.day_start()), 2)


REAL_QUIET_TURN = quietlook.run_turn
REAL_PREP_TURN = prepared.run_turn


# MARK: Settings, Notices and the voice wrap-up

class Status(P2Case):
    def test_each_noticer_with_its_state_and_answered_rate(self):
        self.raise_(self.cand("a", deadline=self.soon(3)), self.cand("b", deadline=self.soon(72)))
        notices.answer(notices.row("a")["feed_id"], "Keep it")
        notices.set_mode("conflict", "digest", "You dismissed two of these in two weeks.", by_colin=False)
        calcache._note("calendar", state="ok", at=self.clock - 600, count=12, tried=self.clock - 600)
        s = notices.status()
        renewal = next(n for n in s["noticers"] if n["id"] == "renewal")
        self.assertEqual((renewal["asked"], renewal["answered"], renewal["rate"], renewal["state"]),
                         (2, 1, 50, "Asking"))
        self.assertEqual(renewal["last"], "Haiku question?")
        conflict = next(n for n in s["noticers"] if n["id"] == "conflict")
        self.assertEqual((conflict["state"], conflict["rate"]), ("Morning brief only", None))
        self.assertEqual(len(s["noticers"]), 16)   # with Running Low (memory facts, Oct 10)
        self.assertEqual(s["sources"][0], {"id": "calendar", "name": "Google Calendar", "state": "ok",
                                           "at": self.clock - 600, "count": 12})
        self.assertEqual(s["sources"][1]["state"], "waiting")
        self.assertEqual((s["today"]["prepared_cap"], s["today"]["quiet_looks_cap"]), (5, 4))
        self.assertIsNone(s["busy"])

    def test_the_new_kinds_can_be_set_from_settings(self):
        for nid in ("school_deadline", "conflict", "prep", "followup", "form", "charge", "bill", "window", "quiet"):
            notices.set_mode(nid, "off")
            self.assertEqual(notices.mode(nid), "off")

    def test_after_a_call_with_zero_the_questions_go_under_it(self):
        self.raise_(self.cand("a", deadline=self.soon(72)))
        fid = notices.row("a")["feed_id"]
        store.db().execute("UPDATE feed SET created_at=1 WHERE id=?", (fid,))
        store.db().commit()
        call = store.add_feed("call", "default", None, "Voice call", json.dumps({"duration": 60, "summary": "Hi."}))
        notices._wakes.clear()
        self.assertEqual(notices.after_call("default"), 1)
        rows = {r["id"]: r["created_at"] for r in store.db().execute("SELECT id, created_at FROM feed")}
        self.assertGreater(rows[fid], rows[call])
        self.assertIn("call", notices._wakes)
        self.assertEqual(notices.after_call("school"), 0)

    def test_turned_off_nothing_new_runs(self):
        store.set_setting("proactivity", {"enabled": False})
        self.assertFalse(notices.enabled())
        with mock.patch.object(calcache, "refresh") as refresh, mock.patch.object(quietlook, "run") as look:
            run(notices.tick())
            run(calcache.tick())
            run(notices.run_pass({"quiet"}))
            refresh.assert_not_called()
            look.assert_not_called()


class PreparedCardsInConsent(P2Case):
    def setUp(self):
        super().setUp()
        try:
            from test_gate import plugin
        except Exception as e:
            self.skipTest(f"the taffy-tools plugin is not loadable here: {e}")
        import tempfile
        self.plugin = plugin
        real = plugin.DB_PATH
        plugin.DB_PATH = Path(tempfile.mkdtemp()) / "consent.db"
        self.addCleanup(setattr, plugin, "DB_PATH", real)

    def test_hidden_from_work_and_kept_a_day(self):
        import time
        p = self.plugin.action_request("clients", "send", "", "Email Theo", "ken@brightpath.test", "To: Theo", "fp1",
                                       "prep-1", None, why="prepared")
        a = self.plugin.action_request("clients", "send", "", "Email Jake", "jake@example.com", "To: Jake", "fp2",
                                       "chat-1", None, why="asked")
        self.assertEqual([r["id"] for r in consent.pending()], [a])
        self.assertEqual([r["id"] for r in consent.requests_after(0)], [a])
        self.plugin.expire_old(time.time() + 7 * 3600)
        self.assertEqual(consent.get_request(p)["status"], "pending")      # a chat card is gone after six hours
        self.assertEqual(consent.get_request(a)["status"], "expired")
        self.plugin.expire_old(time.time() + 25 * 3600)
        self.assertEqual(consent.get_request(p)["status"], "expired")



# These tests check Colin's own home (his name, mailboxes, connectors, plans and time zone), so they run in it
# whatever ZERO_HOME says (tests/run_matrix.py runs the rest of the suite in every home shape).
def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()
