"""Proactivity quality (Oct 9, docs/qa/2026-10-09-proactivity-quality.md): within minutes of proactivity going on,
the clash noticer raised five cards about Canvas due times "overlapping" a sleep block, and eleven cards arrived in one
look. These tests cover the fixes: the Haiku gate on code-worded questions, clashes only between real commitments,
school deadlines due the same evening as one question, pacing, Colin's punctuation, and closed cards he can still
answer.

Run: python3 -m unittest discover -s hub/tests
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import calendar_noticers, choices, health, notices, store  # noqa: E402
from taffy_hub.notices import Candidate, Option  # noqa: E402
from test_proactivity import ProCase, at, run  # noqa: E402
from test_proactivity_p2 import P2Case, ev, person  # noqa: E402


def clash(subject="conflict:a:b", **kw):
    """A question in code's own wording, like the clash noticer's."""
    return Candidate(noticer=kw.pop("noticer", "conflict"), subject=subject, owner="default",
                     evidence=kw.pop("evidence", "Sunday you have UPS shift from 1 to 5 p.m. and Dentist from 3 to "
                                                 "4 p.m., and they overlap."),
                     question="Which one do you want to keep?",
                     options=[Option("Keep UPS shift", "act", say="Keep the shift."),
                              Option("Keep Dentist", "act", say="Keep the dentist."),
                              Option("I'll sort it out", "done")],
                     facts={"what": "UPS shift and Dentist", "when": "Sunday"}, consequence="Colin can't be at both.",
                     deadline=kw.pop("deadline", None), haiku=False, **kw)


# MARK: 1. Every question passes a model check

class Gate(ProCase):
    def test_a_code_worded_question_is_checked_and_can_be_vetoed(self):
        self.haiku.extra = {"ask": False, "why": "It is his own sleep block, not an appointment."}
        self.raise_(clash(deadline=self.soon(30)))
        r = self.notice("conflict:a:b")
        self.assertEqual(r["state"], "handled")
        self.assertEqual(r["end_reason"], "Not worth asking: It is his own sleep block, not an appointment.")
        self.assertIsNone(r["feed_id"])                                       # nothing reached the chat
        self.assertEqual(notices._count("vetoed", 0), 1)
        prompt = self.haiku.prompts[0]
        self.assertIn("Would a sharp human assistant interrupt Colin O'Brien with this right now?", prompt)
        self.assertIn("routine, sleep, focus or planning blocks", prompt)
        self.assertIn("Keep UPS shift; Keep Dentist; I'll sort it out", prompt)
        self.raise_(clash(deadline=self.soon(30)))
        self.assertEqual(len(self.haiku.prompts), 1)                          # a veto is not asked again

    def test_the_gate_keeps_codes_wording(self):
        self.raise_(clash(deadline=self.soon(30)))
        card = self.card("conflict:a:b")
        self.assertEqual(card["question"], "Which one do you want to keep?")
        self.assertTrue(card["evidence"].startswith("Sunday you have UPS shift"))   # not "Haiku evidence."

    def test_no_check_no_question(self):
        self.haiku.fail = True
        self.raise_(clash(deadline=self.soon(30)))
        self.assertIsNone(self.notice("conflict:a:b"))                        # waits for a later look
        self.haiku.fail = False
        with mock.patch.dict(health.status, {"claude": False}):                # the plan's limit
            self.raise_(clash(deadline=self.soon(30)))
        self.assertIsNone(self.notice("conflict:a:b"))
        for i in range(notices.settings()["haiku_daily"]):
            notices._log("haiku", "renewal", f"x{i}")                         # the day's checks are used
        self.raise_(clash(deadline=self.soon(30)))
        self.assertIsNone(self.notice("conflict:a:b"))
        self.haiku.extra = {"ask": "maybe"}                                   # anything but true or false waits
        self.clock += 86400
        self.raise_(clash(deadline=self.soon(30)))
        self.assertIsNone(self.notice("conflict:a:b"))

    def test_the_full_judgment_can_say_it_is_not_worth_asking(self):
        self.haiku.extra = {"worth": False, "why": "A print job from September that is long done."}
        self.raise_(self.cand("mail-sent:lakeview:store1234@theupsstore.com:word documents", noticer="unreplied",
                              consequence="The thread is stalled."))
        r = self.notice("mail-sent:lakeview:store1234@theupsstore.com:word documents")
        self.assertEqual(r["state"], "handled")
        self.assertIn("Not worth asking", r["end_reason"])
        self.assertIn('"worth": would a sharp human assistant interrupt', self.haiku.prompts[0])
        self.assertIn("a one-time job that is probably finished by now", self.haiku.prompts[0])

    def test_a_quiet_look_and_a_calls_next_step_are_gated_too(self):
        self.haiku.extra = {"ask": False, "why": "Nothing to do."}
        quiet = clash("quiet:dog", noticer="quiet", deadline=self.soon(10))
        call = clash("call:7", noticer="followup", event_timed=True, deadline=self.soon(3))
        self.raise_(quiet, call)
        self.assertEqual(self.notice("quiet:dog")["state"], "handled")
        self.assertEqual(self.notice("call:7")["state"], "handled")
        self.assertEqual(len(self.haiku.prompts), 2)


# MARK: 2. Clashes only between real commitments

class Clashes(P2Case):
    def oct9(self):
        """The calendar that raised five cards on Oct 9 (Sunday Oct 11 here), and more that is not a clash."""
        sun = (10, 11)
        return [
            ev("sleep", "Routine: Sleep (11 p.m.–7 a.m.)", at(*sun, 23), at(10, 12, 7), calendar="colin@gmail.com"),
            ev("peer", "POL 101 — Unit 6 peer replies final deadline", at(*sun, 23, 55), at(10, 12, 0),
               calendar="Lakeview Classes"),
            ev("formh", "POL 101 — Unit 6 Form H due", at(*sun, 23, 55), at(10, 12, 0), calendar="Lakeview Classes"),
            ev("formc", "POL 101 — Unit 6 Form C Writing Lab report due", at(*sun, 23, 55), at(10, 12, 0),
               calendar="Lakeview Classes"),
            ev("holiday", "Columbus Day", at(10, 12, 0), at(10, 13, 0), all_day=True),
            ev("study", "School: Coursework before UPS", at(*sun, 9), at(*sun, 12), calendar="Lakeview Classes"),
            ev("ready", "Routine: Get ready & travel to UPS", at(*sun, 11), at(*sun, 12)),
            ev("ups", "UPS shift", at(*sun, 11, 30), at(*sun, 16), location="UPS EHT / store 4995"),
            ev("quiz", "BUS 305 quiz window", at(*sun, 12), at(*sun, 14), calendar="Canvas"),
            ev("declined", "Board meeting", at(*sun, 13), at(*sun, 14), my_response="declined",
               attendees=[person("Matt", "matt@goodworks.test")]),
            ev("free", "Riley's recital", at(*sun, 14), at(*sun, 15), location="Springfield", free=True),
            ev("focus", "Focus: Fernhill Tech", at(*sun, 14), at(*sun, 15)),
        ]

    def test_due_times_routines_holidays_and_own_blocks_never_clash(self):
        self.src.events = self.oct9()
        self.assertEqual(self.find("conflict"), [])

    def test_two_real_commitments_do(self):
        self.src.events = self.oct9() + [
            ev("dentist", "Dentist", at(10, 11, 15), at(10, 11, 16), location="Smile Dental, Springfield")]
        [c] = self.find("conflict")
        self.assertEqual(c.subject, "conflict:dentist:ups")
        self.assertIn("UPS shift from 11:30 a.m. to 4 p.m. and Dentist from 3 to 4 p.m., and they overlap.",
                      c.evidence)

    def test_one_question_a_day_however_many_clashes(self):
        self.src.events = self.oct9() + [
            ev("dentist", "Dentist", at(10, 11, 15), at(10, 11, 16), location="Smile Dental, Springfield"),
            ev("call", "Call with Dana", at(10, 11, 15, 30), at(10, 11, 16, 30),
               attendees=[person("Dana", "karen@blueheron.test")]),
            ev("mon", "Advising", at(10, 12, 10), at(10, 12, 11), attendees=[person("Andrea", "a@lakeview.test")]),
            ev("mon2", "UPS shift", at(10, 12, 10, 30), at(10, 12, 15), location="UPS EHT / store 4995")]
        found = self.find("conflict")
        self.assertEqual(len(found), 2)                                       # Sunday's and Monday's
        sunday = next(c for c in found if "ups" in c.subject and "mon" not in c.subject)
        self.assertIn("There are 2 more clashes that day.", sunday.evidence)

    def test_meetings_need_more_than_a_quarter_hour(self):
        self.src.events = [ev("a", "Call with Dana", at(10, 11, 15), at(10, 11, 15, 10),
                              attendees=[person("Dana", "karen@blueheron.test")]),
                           ev("b", "UPS shift", at(10, 11, 14), at(10, 11, 18), location="UPS EHT")]
        self.assertEqual(self.find("conflict"), [])

    def test_commitment_reads_the_kinds(self):
        c = calendar_noticers.commitment
        self.assertTrue(c(ev("x", "UPS shift", 0, 3600 * 4, location="UPS EHT")))
        self.assertTrue(c(ev("x", "Lunch", 0, 3600, attendees=[person("Sam", "sam@example.com")])))
        self.assertTrue(c(ev("x", "Dentist: cleaning", 0, 3600, location="Smile Dental")))
        self.assertFalse(c(ev("x", "Routine: Wind down for 11 p.m. sleep", 0, 3600)))
        self.assertFalse(c(ev("x", "Personal: Riley / rest", 0, 3600)))
        self.assertFalse(c(ev("x", "Build: Fernhill Tech (stop at 4:30)", 0, 3600)))
        self.assertFalse(c(ev("x", "Paper due", 0, 3600, location="Lakeview")))
        self.assertFalse(c(ev("x", "Something", 0, 3600, calendar="Canvas Assignments")))


# MARK: 5. School deadlines due the same evening are one question, never a clash

class SchoolDeadlines(P2Case):
    def test_unit_5s_finished_task_does_not_stand_for_unit_6(self):
        self.src.trello_cards = []
        self.src.events = [ev("formh", "POL 101 — Unit 6 Form H due", at(10, 9, 23, 55), at(10, 10, 0))]
        self.src.groups["done"] = [{"id": "t1", "title": "Read-only: find Aden's reply in POL 101 Unit 5 discussion",
                                    "status": "done", "created_at": self.clock - 86400}]
        self.assertEqual(len(self.find("school_deadline")), 1)
        self.src.groups["done"][0]["title"] = "Draft POL 101 Unit 6 Form H"
        self.assertEqual(self.find("school_deadline"), [])

    def test_three_items_due_the_same_evening_are_one_question(self):
        self.src.trello_cards = []
        fri = (10, 9)
        self.src.events = [
            ev("peer", "POL 101 — Unit 6 peer replies final deadline", at(*fri, 23, 55), at(10, 10, 0)),
            ev("formh", "POL 101 — Unit 6 Form H due", at(*fri, 23, 55), at(10, 10, 0)),
            ev("formc", "POL 101 — Unit 6 Form C Writing Lab report due", at(*fri, 23, 55), at(10, 10, 0)),
            ev("bns", "BUS 305 quiz due", at(*fri, 20), at(*fri, 20, 5)),
        ]
        found = self.find("school_deadline")
        self.assertEqual(len(found), 2)
        pol = next(c for c in found if "pol-101" in c.subject)
        self.assertEqual(pol.subject, "deadline:pol-101:20261009")
        self.assertEqual(pol.evidence, "Three POL 101 items are due tomorrow at 11:55 p.m., according to your "
                                       "calendar: Unit 6 peer replies, Unit 6 Form H and Unit 6 Form C Writing Lab "
                                       "report. I don't see time set aside for them or anything showing they are "
                                       "done.")
        self.assertIsNone(notices.check(pol))
        self.src.zip_notes["school"] = ["Writing Lab done. Colin must sign and date Form C and submit by Oct 9."]
        pol2 = next(c for c in self.find("school_deadline") if "pol-101" in c.subject)
        self.assertEqual(pol2.evidence, pol.evidence)                         # School's note is the same Form C
        self.assertEqual(self.find("conflict"), [])                           # due times are never a clash
        self.raise_(pol)
        card = self.card(pol.subject)
        self.assertNotIn("—", json.dumps(card, ensure_ascii=False))


# MARK: 3. Pacing

class Pacing(ProCase):
    def setUp(self):
        super().setUp()
        s = notices.settings()
        s["enabled_at"] = self.clock - 3 * 86400                               # not the first day
        notices._save(s)

    def paced(self, *cands):
        return run(notices.consider(list(cands), paced=True))

    def look(self, *cands):
        """One look, then Colin answers what it raised, and the next look is two hours later."""
        out = self.paced(*cands)
        for nid in out:
            notices.answer(notices.by_id(nid)["feed_id"], "Keep it")
        self.clock += 2 * 3600 + 60
        return out

    def test_one_card_a_look_and_the_most_important_wins(self):
        later = self.cand("renewal:x", deadline=self.soon(72))
        thread = self.cand("mail-sent:x", noticer="unreplied", consequence="Theo is waiting.")
        school = self.cand("deadline:x", noticer="school_deadline", deadline=self.soon(10))
        self.assertEqual(len(self.paced(later, thread, school)), 1)
        self.assertEqual(self.notice("deadline:x")["state"], "open")          # due inside a day
        self.assertIsNone(self.notice("renewal:x"))
        self.assertIsNone(self.notice("mail-sent:x"))
        self.assertEqual(len(self.haiku.prompts), 1)                          # the others cost nothing yet
        self.paced(later, thread, school)
        self.assertIsNone(self.notice("renewal:x"))                           # one card at a time (Oct 10)
        self.assertEqual(len(self.haiku.prompts), 1)
        notices.answer(self.notice("deadline:x")["feed_id"], "Keep it")
        self.clock += 2 * 3600
        self.paced(later, thread, school)
        self.assertEqual(self.notice("renewal:x")["state"], "open")           # then the next look's best

    def test_one_of_each_kind_a_day_and_three_in_all(self):
        self.look(self.cand("renewal:1", deadline=self.soon(72)))
        self.look(self.cand("renewal:2", deadline=self.soon(72)))
        self.assertIsNone(self.notice("renewal:2"))                           # one renewal a day
        for kind in ("bill", "form", "window", "charge"):
            self.look(self.cand(f"{kind}:1", noticer=kind, deadline=self.soon(72)))
        self.assertEqual(notices._raised_today(), 3)                          # three a day (Oct 10)
        self.assertIsNone(self.notice("window:1"))
        self.clock = at(10, 9, 10)
        self.paced(self.cand("renewal:2", deadline=self.soon(72)))
        self.assertEqual(self.notice("renewal:2")["state"], "open")           # a new day

    def test_a_saved_old_default_reads_as_the_new_one(self):
        s = notices.settings()
        s["daily_cards"] = 4                                                  # saved before Oct 9 by every home
        store.set_setting("proactivity", s)
        self.assertEqual(notices.settings()["daily_cards"], 3)
        store.set_setting("proactivity", {**s, "daily_cards": 6})             # a choice of the person's own
        self.assertEqual(notices.settings()["daily_cards"], 6)

    def test_the_first_day_is_gentle(self):
        notices.set_enabled(False)
        notices.set_enabled(True)                                             # turned on just now
        self.assertTrue(notices.first_day())
        backlog = [self.cand(f"{k}:1", noticer=k, deadline=self.soon(72 + i))
                   for i, k in enumerate(("renewal", "bill", "form", "window", "charge", "promise"))]
        raised = sum(len(self.look(*backlog)) for _ in range(5))
        self.assertEqual(raised, 2)                                           # the top two, not a backlog dump
        self.clock = notices.day_start() + 86400 + 10 * 3600
        self.assertFalse(notices.first_day())
        raised = sum(len(self.look(*backlog)) for _ in range(5))
        self.assertEqual(raised, 3)

    def test_one_card_at_a_time(self):
        self.paced(self.cand("renewal:1", deadline=self.soon(72)))
        self.clock += 3 * 3600
        self.paced(self.cand("promise:1", noticer="promise", deadline=self.soon(72)))
        self.assertIsNone(self.notice("promise:1"))                           # the open card first
        self.assertEqual(len(self.haiku.prompts), 1)                          # waiting costs no check
        self.clock += 22 * 3600
        self.paced(self.cand("promise:1", noticer="promise", deadline=self.soon(72)))
        self.assertEqual(self.notice("promise:1")["state"], "open")           # a day old: it gives way
        self.assertEqual(self.notice("renewal:1")["state"], "expired")
        self.assertEqual(self.notice("renewal:1")["end_reason"], "room")
        self.assertEqual(len(notices._open_rows()), 1)
        out = notices.answer(self.notice("renewal:1")["feed_id"], "Keep it")  # Closed is not final
        self.assertEqual(out["send"], "Keep agentmax.app.")

    def test_two_hours_between_cards(self):
        self.paced(self.cand("renewal:1", deadline=self.soon(72)))
        notices.answer(self.notice("renewal:1")["feed_id"], "Keep it")
        self.clock += 30 * 60
        self.paced(self.cand("bill:1", noticer="bill", deadline=self.soon(72)))
        self.assertIsNone(self.notice("bill:1"))                              # too soon after the last one
        self.paced(self.cand("deadline:1", noticer="due_soon", deadline=self.soon(2)))
        self.assertEqual(self.notice("deadline:1")["state"], "open")          # due within three hours: now
        notices.answer(self.notice("deadline:1")["feed_id"], "Keep it")
        self.clock += 2 * 3600
        self.paced(self.cand("bill:1", noticer="bill", deadline=self.soon(72)))
        self.assertEqual(self.notice("bill:1")["state"], "open")

    def test_something_due_within_hours_takes_the_open_cards_place(self):
        self.paced(self.cand("renewal:1", deadline=self.soon(72)))
        self.clock += 600
        self.paced(self.cand("deadline:1", noticer="school_deadline", deadline=self.soon(5)))
        self.assertIsNone(self.notice("deadline:1"))                          # due today, but not within hours
        self.paced(self.cand("due:1", noticer="due_soon", deadline=self.soon(2)))
        self.assertEqual(self.notice("due:1")["state"], "open")
        self.assertEqual(self.notice("renewal:1")["state"], "expired")       # Closed, still answerable
        self.assertEqual([r["subject"] for r in notices._open_rows()], ["due:1"])
        self.paced(self.cand("due:2", noticer="due_soon", deadline=self.soon(1)))
        self.assertIsNone(self.notice("due:2"))                               # never pushes out another one

    def test_cards_from_before_the_rule_close_to_one(self):
        for i, kind in enumerate(("reply_waiting", "school_deadline", "unreplied")):
            self.clock += 60
            self.raise_(self.cand(f"{kind}:1", noticer=kind, deadline=self.soon(30 + 10 * i)))
        self.assertEqual(len(notices._open_rows()), 3)                        # Oct 10, 8:10 a.m.
        self.assertEqual(notices.one_at_a_time(), 2)
        self.assertEqual([r["subject"] for r in notices._open_rows()], ["reply_waiting:1"])   # the soonest date
        self.assertIn("I set 2 questions aside so you only get one at a time", notices.brief_text())
        self.assertEqual(notices.one_at_a_time(), 0)

    def test_an_open_cards_new_date_is_not_a_new_card(self):
        self.paced(self.cand("renewal:1", deadline=self.soon(72), version="a"))
        self.paced(self.cand("bill:1", noticer="bill", deadline=self.soon(72)))
        self.paced(self.cand("renewal:1", deadline=self.soon(20), version="b"))   # no pacing for an update
        self.assertEqual(self.notice("renewal:1")["version"], "b")


# MARK: 4. Wording

class Wording(ProCase):
    def test_tidy(self):
        t = notices.tidy
        self.assertEqual(t("Sunday has POL 101 — Unit 6 Form H due from 11:55 p.m. to 12 a.m.. They overlap."),
                         "Sunday has POL 101: Unit 6 Form H due from 11:55 p.m. to 12 a.m. They overlap.")
        self.assertEqual(t("Routine: Sleep (11 p.m.–7 a.m.)"), "Routine: Sleep (11 p.m. to 7 a.m.)")
        self.assertEqual(t("I looked — and it seems fine — so no."), "I looked, and it seems fine, so no.")
        self.assertEqual(t("Form C - Writing Lab"), "Form C: Writing Lab")
        self.assertEqual(t("Done.."), "Done.")
        self.assertEqual(t("Is it right?."), "Is it right?")
        self.assertEqual(t("Wait... really"), "Wait... really")
        self.assertEqual(t("He said: — yes"), "He said: yes")
        self.assertEqual(t("well-known co-op"), "well-known co-op")

    def test_every_card_line_is_tidy(self):
        c = clash(evidence="Sunday you have POL 101 — Unit 6 from 11 p.m. to 12 a.m.. They overlap.",
                  deadline=self.soon(30))
        c.options[0] = Option("Keep POL 101 — Unit 6", "act", say="Keep it.")
        self.raise_(c)
        card = self.card("conflict:a:b")
        self.assertEqual(card["evidence"], "Sunday you have POL 101: Unit 6 from 11 p.m. to 12 a.m. They overlap.")
        self.assertEqual(card["options"][0], "Keep POL 101: Unit 6")
        out = notices.answer(self.notice("conflict:a:b")["feed_id"], "Keep POL 101: Unit 6")
        self.assertEqual(out["send"], "Keep it.")                             # the tidied option still answers

    def test_haikus_wording_is_tidy(self):
        self.haiku.extra = {"evidence": "Theo wrote — twice — about the handoff..", "question": "Nudge him?."}
        self.raise_(self.cand(deadline=self.soon(30)))
        card = self.card()
        self.assertEqual(card["evidence"], "Theo wrote, twice, about the handoff.")
        self.assertEqual(card["question"], "Nudge him?")


# MARK: Closed cards can still be answered

class ClosedCards(ProCase):
    def close(self, subject="thing:1", hours=30):
        self.raise_(self.cand(subject, deadline=self.soon(hours)))
        self.clock = self.soon(hours + 1)
        notices.expire()
        self.assertEqual(self.notice(subject)["state"], "expired")

    def test_an_answer_after_the_date_says_the_date_passed(self):
        self.close()
        out = notices.answer(self.notice()["feed_id"], "Find the cancel page")
        self.assertIn("Find the cancel page.", out["send"])
        self.assertIn("(This answers a closed question. Its date, today at 4 p.m., has passed. Tell me plainly that "
                      "the date has passed", out["send"])
        self.assertTrue(out["follow"].startswith("Its date, today at 4 p.m., has passed. Sent to Zero"))
        self.assertEqual(self.card()["state"], "picked")

    def test_dismiss_wait_and_own_words_work_on_a_closed_card(self):
        self.close("a")
        out = notices.answer(self.notice("a")["feed_id"], "I did it already", custom=True)
        self.assertIn("I did it already", out["send"])
        self.assertIn("has passed", out["send"])
        self.close("b")
        notices.answer(self.notice("b")["feed_id"], None)
        self.assertEqual(self.notice("b")["state"], "dismissed")

    def test_through_the_apps_answer_path(self):
        self.close()
        card = choices.answer("default", self.notice()["feed_id"], "Keep it")
        self.assertEqual(card["state"], "picked")
        with self.assertRaises(choices.ChoiceError):
            choices.answer("default", self.notice()["feed_id"], "Keep it")

    def test_from_the_lock_screen(self):
        self.close()
        fid = self.notice()["feed_id"]
        out = notices.lock_answer(fid, "not_now")                           # nothing to hold back on a closed card
        self.assertEqual(self.notice()["state"], "expired")
        self.assertIsNone(out["send"])
        out = notices.lock_answer(fid, "reply", "Handled it Friday")
        self.assertIn("Handled it Friday", out["send"])

    def test_never_closes_in_its_first_day_unless_its_date_passed(self):
        self.raise_(self.cand("early", deadline=self.soon(30), expires=self.soon(2)))
        self.clock = self.soon(3)
        notices.expire()
        self.assertEqual(self.notice("early")["state"], "open")
        self.raise_(self.cand("due", deadline=self.soon(1)))
        self.clock = self.soon(2)
        notices.expire()
        self.assertEqual(self.notice("due")["state"], "expired")



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


class SavedSettings(ProCase):
    def test_only_changed_settings_are_saved_so_new_defaults_arrive(self):
        store.set_setting("proactivity", {"enabled": True, "expiry_days": 3, "haiku_daily": 20})
        notices.set_mode("renewal", "digest")
        saved = store.get_setting("proactivity", {})
        self.assertNotIn("spacing_min", saved)                  # a default never gets pinned by saving
        self.assertEqual(saved["noticers"]["renewal"]["mode"], "digest")
        store.set_setting("proactivity", {k: v for k, v in saved.items() if k not in ("expiry_days", "haiku_daily")})
        notices.set_mode("renewal", "on")
        self.assertEqual(notices.settings()["haiku_daily"], notices.DEFAULTS["haiku_daily"])
        self.assertNotIn("haiku_daily", store.get_setting("proactivity", {}))
