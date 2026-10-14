"""A teammate, not a bot to educate (Colin, Oct 10, docs/qa/2026-10-10-proactive-teammate.md). Three cards came at
once, one about a monthly creator newsletter ("Please don't build this yet") worded about Colin instead of to him:
"...reached Colin's personal account on Oct 7, and no reply from Colin is recorded. The briefing material does not
mention Michia or this email."

These tests cover the fixes: newsletters, lists and regular bulk senders never become "a reply you owe"; every card is
the Zip speaking to the person ("I don't see a reply from you"), with the person's name and the machinery kept out;
a question Haiku isn't sure of is dropped; and Colin's own example, schoolwork due within hours that the course site
doesn't show as turned in, asked as "Is it done?" with answers that act. One card at a time and the spacing between
cards are in test_proactivity_quality.Pacing.

Run: python3 -m unittest discover -s hub/tests
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import calendar_noticers, choices, mail, noticers, notices, quietlook, schoolcheck, store, who  # noqa: E402
from test_proactivity import FakeSources, ProCase, at, env, run  # noqa: E402
from test_proactivity_p2 import P2Case, ev  # noqa: E402

MICHIA = ("Jordan Lee", "michia@askmichia.com")
NEWSLETTER = ("From: Jordan Lee <michia@askmichia.com>\n"
              "Subject: Please don't build this yet\n"
              "List-Unsubscribe: <mailto:unsub+k0@convertkit-mail.com>, <https://unsubscribe.kit-mail3.com/k0>\n"
              "Precedence: bulk\n\n"
              "Hey, it's Michia.\n\nIt's 8:47 PM here in Tokyo. What's the cheapest, fastest way to find out if "
              "you're wrong? Reply and tell me.")


# MARK: Bulk mail

class BulkMail(unittest.TestCase):
    def test_list_headers_and_precedence(self):
        self.assertTrue(mail.is_bulk(NEWSLETTER))
        self.assertTrue(mail.is_bulk("From: A <a@x.com>\nList-Id: <weekly.x.com>\n\nHi?"))
        self.assertTrue(mail.is_bulk("From: A <a@x.com>\nPrecedence: list\n\nHi?"))
        self.assertTrue(mail.is_bulk("From: A <a@x.com>\nAuto-Submitted: auto-generated\n\nHi?"))
        self.assertFalse(mail.is_bulk("From: A <a@x.com>\nAuto-Submitted: no\n\nCan you send it?"))
        self.assertFalse(mail.is_bulk("From: Theo Avery <ken@brightpath.test>\nSubject: Handoff\n\n"
                                      "Can you send me the handoff notes by Friday?"))

    def test_a_footer_gives_a_bulk_send_away(self):
        self.assertTrue(mail.is_bulk("Big news this week! What do you think?\n\nUnsubscribe | View in browser"))
        self.assertTrue(mail.is_bulk("Any questions?\n\nManage your email preferences here."))
        self.assertFalse(mail.is_bulk("Hi Colin, could you look at the draft before Friday?"))

    def test_the_headers_never_reach_the_excerpt(self):
        self.assertEqual(mail.own_text(NEWSLETTER).splitlines()[0], "Hey, it's Michia.")
        self.assertEqual(mail.headers_of(NEWSLETTER)["precedence"], "bulk")

    def test_a_message_is_read_with_the_list_headers(self):
        calls = []

        def fake(argv):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "From: A <a@x.com>\n\nHello?", "")
        with mock.patch.object(mail, "run", fake), \
                mock.patch.object(mail, "ACCOUNTS", {"personal": "me@example.com"}), \
                mock.patch.object(mail, "uses_google", lambda a: False):
            mail.read("personal", "INBOX", "12335")
        argv = calls[0]
        self.assertIn("-p", argv)
        for h in ("List-Unsubscribe", "List-Id", "Precedence", "From", "Subject"):
            self.assertIn(h, argv)
            self.assertEqual(argv[argv.index(h) - 1], "-H")
        self.assertEqual(argv[-2:], ["--", "12335"])


class Newsletters(ProCase):
    def setUp(self):
        super().setUp()
        self.src = FakeSources()
        for p in (mock.patch.object(noticers, "ACCOUNTS", {"personal": "me@example.com"}),
                  mock.patch.object(mail, "ACCOUNTS", {"personal": "me@example.com"})):
            p.start()
            self.addCleanup(p.stop)

    def letters(self, addr, n):
        return [env(100 + i, f"Letter number {i}", frm=("Sam", addr), date=at(10, 6, 9) - i * 7 * 86400)
                for i in range(n)]

    def test_a_newsletter_in_a_persons_name_is_never_a_reply_owed(self):
        karen = ("Miss Dana", "karen@blueheron.test")
        sam = ("Sam Creator", "sam@creator.example")
        self.src.mail[("personal", "INBOX")] = [
            env(1, "Please don't build this yet", frm=MICHIA, date=at(10, 6, 9)),
            env(2, "If you're working hard, read this", frm=sam, date=at(10, 5, 9)),
            env(3, "Banner size", frm=karen, date=at(10, 5, 9)),
        ]
        self.src.bodies = {"1": NEWSLETTER, "2": "Are you working on the right thing? Hit reply and tell me.",
                           "3": "Can you confirm the banner size by Friday?"}
        self.src.sent_by = {sam[1]: self.letters(sam[1], 5), karen[1]: self.letters(karen[1], 5)}
        self.src.wrote = {karen[1]}
        found = run(noticers.reply_waiting(self.src))
        self.assertEqual([c.subject for c in found], ["mail-in:personal:karen@blueheron.test:banner size"])
        c = found[0]
        self.assertIn("I don't see a reply from you yet", c.evidence)
        self.assertIsNone(notices.off_voice(c.evidence))
        self.assertIsNone(notices.off_voice(c.question))
        self.assertNotIn(who.first(), json.dumps(c.facts))
        self.assertEqual([o.text for o in c.options],
                         ["Draft a reply for me to check", "I already answered", "Remind me in a few days"])

    def test_two_letters_are_not_yet_a_cadence(self):
        sam = ("Sam Creator", "sam@creator.example")
        self.src.mail[("personal", "INBOX")] = [env(2, "Lunch next week?", frm=sam, date=at(10, 5, 9))]
        self.src.bodies = {"2": "Are you free for lunch next week?"}
        self.src.sent_by = {sam[1]: self.letters(sam[1], 2)}
        self.assertEqual(len(run(noticers.reply_waiting(self.src))), 1)

    def test_the_prompt_says_a_personal_sounding_newsletter_is_still_one(self):
        self.raise_(self.cand("mail-in:x", noticer="reply_waiting", consequence="They are waiting."))
        prompt = self.haiku.prompts[0]
        self.assertIn("A newsletter or mass email written to sound personal", prompt)
        self.assertIn('"confidence"', prompt)
        self.assertIn("like a teammate", prompt)


# MARK: The Zip speaks to the person

class Voice(ProCase):
    def code_worded(self):
        return self.cand("mail-in:x", noticer="reply_waiting", consequence="They are waiting.",
                         evidence="Jordan Lee wrote to you Wednesday about \"Please don't build this yet\" and "
                                  "asked you something. I don't see a reply from you yet.",
                         question="Want me to draft a reply for you to check?")

    def test_third_person_and_machinery_fall_back_to_codes_wording(self):
        first = who.first()
        self.haiku.extra = {
            "evidence": f"Jordan Lee's email \"Please don't build this yet\" reached {who.possessive()} personal "
                        f"account on Oct 7, and no reply from {first} is recorded. The briefing material does not "
                        "mention Michia or this email.",
            "question": f"Do you want to answer Michia, say {first} already talked, or leave it for now?"}
        c = self.code_worded()
        self.raise_(c)
        card = self.card("mail-in:x")
        self.assertEqual(card["evidence"], c.evidence)
        self.assertEqual(card["question"], c.question)
        for word in (first, "recorded", "briefing", "material"):
            self.assertNotIn(word, card["evidence"] + card["question"])

    def test_haikus_words_stay_when_they_speak_to_the_person(self):
        self.haiku.extra = {"evidence": "Michia wrote to you on Oct 7. I don't see a reply from you yet.",
                            "question": "Want me to draft a reply?"}
        self.raise_(self.code_worded())
        self.assertEqual(self.card("mail-in:x")["evidence"], "Michia wrote to you on Oct 7. I don't see a reply from "
                                                             "you yet.")

    def test_off_voice(self):
        first = who.first()
        self.assertEqual(notices.off_voice(f"No reply from {first} since Monday."), "third person")
        self.assertEqual(notices.off_voice(f"{who.possessive()} account got it."), "third person")
        self.assertEqual(notices.off_voice("No reply is recorded."), "machinery")
        self.assertEqual(notices.off_voice("The hub found an email."), "machinery")
        self.assertIsNone(notices.off_voice("Your calendar shows a class at 2 p.m. I don't see time set aside."))

    def test_a_question_haiku_is_not_sure_of_is_dropped(self):
        self.haiku.extra = {"confidence": "low", "why": "It may be a newsletter."}
        self.raise_(self.code_worded())
        r = self.notice("mail-in:x")
        self.assertEqual(r["state"], "handled")
        self.assertIsNone(r["feed_id"])
        self.haiku.extra = {"confidence": "high"}
        self.raise_(self.cand("mail-in:y", noticer="reply_waiting", consequence="They are waiting."))
        self.assertEqual(self.notice("mail-in:y")["state"], "open")

    def test_a_quiet_look_that_speaks_about_the_person_is_not_asked(self):
        first = who.first()

        def reply(evidence):
            return json.dumps({"raise": [{"subject": "dog-sitter", "owner": "default", "evidence": evidence,
                                          "question": "Want me to find a sitter?",
                                          "options": ["Find a sitter", "I have one"], "deadline": "2026-10-16 09:00",
                                          "about": "the trip"}]})
        self.assertEqual(quietlook.candidates(reply(f"{who.possessive()} calendar shows a trip Friday.")), [])
        self.assertEqual(quietlook.candidates(reply("Your calendar shows a trip Friday. No sitter is recorded.")), [])
        self.assertEqual(len(quietlook.candidates(reply("Your calendar shows a trip Friday and no sitter."))), 1)


# MARK: Colin's example: due within hours, is it done?

def check_reply(*items, site="eLearning"):
    return "Here is what I found:\n" + json.dumps({"site": site, "items": [
        {"name": n, "submitted": s, "seen": "The page shows no file." if s == "no" else "Submitted for grading.",
         "link": f"https://elearning.example.edu/mod/assign/view.php?id={i}"} for i, (n, s) in enumerate(items)]})


class DueSoon(P2Case):
    FORM_H = "POL 101 — Unit 6 Form H"
    FORM_C = "POL 101 — Unit 6 Form C Writing Lab report"

    def setUp(self):
        super().setUp()
        self.turns = []
        self.answer = check_reply((self.FORM_H, "no"), (self.FORM_C, "yes"))

        async def turn(bot_id, text):
            self.turns.append((bot_id, text))
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        p = mock.patch.object(schoolcheck, "run_turn", turn)
        p.start()
        self.addCleanup(p.stop)
        due = self.clock + 2 * 3600
        self.due = due
        self.src.events = [ev("formh", f"{self.FORM_H} due", due, due + 300, calendar="Lakeview Classes"),
                           ev("formc", f"{self.FORM_C} due", due, due + 300, calendar="Lakeview Classes"),
                           ev("later", "BUS 305 quiz due", due + 30 * 3600, due + 30 * 3600 + 300)]
        self.src.trello_cards = [{"id": "c1", "name": "POL 101 Unit 6 Form H", "board": "Colin | School & Projects",
                                  "list": "Next", "due": due, "due_complete": False, "url": "https://trello.com/c/1"}]

    def look(self):
        async def go():
            out = await calendar_noticers.due_soon(self.src)
            await schoolcheck.drain()
            return out
        return run(go())

    def test_the_school_zip_looks_first_then_asks_is_it_done(self):
        self.assertEqual(self.look(), [])
        self.assertEqual(len(self.turns), 1)
        bot, text = self.turns[0]
        self.assertEqual(bot, "school" if "school" in notices.config.BOTS_BY_ID else "default")
        self.assertIn("Only read: don't submit, upload, post, mark or change anything", text)
        self.assertIn(f"- {self.FORM_H}", text)
        self.assertIn(f"- {self.FORM_C}", text)
        self.assertNotIn("BUS 305", text)
        found = self.look()
        self.assertEqual(len(self.turns), 1)
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertEqual(c.noticer, "due_soon")
        self.assertEqual(c.question, "Is it done?")
        self.assertIn(f"Your {self.FORM_H} is due {calendar_noticers.when(self.due)}, in about two hours.",
                      c.evidence)
        self.assertIn(f"I checked eLearning: {self.FORM_C} shows as turned in, but I couldn't find a submission "
                      f"for {self.FORM_H}.", c.evidence)
        self.assertIsNone(notices.off_voice(c.evidence))
        self.assertEqual([o.text for o in c.options],
                         ["It's finished. Mark it done on Trello", "Not done yet. Help me finish it"])
        self.assertIn('Please mark the Trello card "POL 101 Unit 6 Form H" done.', c.options[0].say)
        self.assertIn("https://elearning.example.edu/mod/assign/view.php?id=0", c.options[1].say)
        self.assertIsNone(notices.check(c))
        self.raise_(c)
        card = self.card(c.subject)
        self.assertEqual(card["question"], "Is it done?")
        self.assertNotIn("—", card["evidence"])
        self.assertEqual(notices.row(c.subject)["urgent"], 1)

    def test_with_a_mac_the_answer_opens_what_he_needs_there(self):
        self.look()
        with mock.patch.object(calendar_noticers, "_has_mac", lambda: True):
            c = self.look()[0]
        self.assertEqual(c.options[1].text, "Not done yet. Open what I need on my Mac")
        self.assertIn("on my Mac", c.options[1].say)

    def test_nothing_is_asked_when_the_site_shows_it_turned_in(self):
        self.answer = check_reply((self.FORM_H, "yes"), (self.FORM_C, "yes"))
        self.look()
        self.assertEqual(self.look(), [])

    def test_a_check_that_fails_says_so_plainly(self):
        self.answer = RuntimeError("browser crashed")
        self.look()
        c = self.look()[0]
        self.assertEqual(c.question, "Are they done?")
        self.assertIn("I couldn't get into the course site to check, and nothing else shows they are done.",
                      c.evidence)
        self.assertNotIn("eLearning", c.evidence)

    def test_no_trello_card_means_finished_just_closes(self):
        self.src.trello_cards = []
        self.look()
        c = self.look()[0]
        self.assertEqual([o.text for o in c.options], ["Not done yet. Help me finish it", "It's finished"])
        self.assertEqual(c.options[1].kind, "done")

    def test_school_deadline_leaves_the_last_hours_to_it(self):
        self.assertEqual([c for c in self.find("school_deadline") if "POL 101" in c.evidence], [])

    def test_checks_have_a_daily_limit_and_wait_for_waking_hours(self):
        for i in range(notices.settings()["school_checks_daily"]):
            notices._log("school_check", "due_soon", f"x{i}")
        self.assertEqual(self.look(), [])
        self.assertEqual(self.turns, [])
        self.assertIsNone(schoolcheck.result(next(iter(schoolcheck._all()), "none")))

    def test_no_look_while_the_zip_is_busy_with_its_browser(self):
        self.src.groups["working"] = [{"id": "t1", "title": "Read chapter 4", "assignee": schoolcheck.checker(),
                                       "status": "running", "created_at": self.clock}]
        self.assertEqual(self.look(), [])
        self.assertEqual(self.turns, [])
        self.src.groups["working"] = []
        self.look()
        self.assertEqual(len(self.turns), 1)

    def test_its_kind_fits_only_people_in_school(self):
        self.assertIn("due_soon", notices.NOTICERS)
        self.assertEqual(notices.FITS["due_soon"], ("school",))
        self.assertIn("due_soon", notices.TOLD_FITS)


# MARK: A typed answer closes the card

class TypedAnswers(ProCase):
    def test_a_typed_answer_closes_a_choice_card_and_a_question(self):
        fid = store.add_feed("choices", "default", None, "Which one?",
                             json.dumps({"question": "Which one?", "options": ["A", "B"], "state": "open"}))
        out = choices.answer("default", fid, "Neither, the third one", custom=True)
        self.assertEqual(out["state"], "picked")
        self.assertEqual(out["picked"], "Neither, the third one")
        self.raise_(self.cand(deadline=self.soon(30)))
        out = choices.answer("default", self.notice()["feed_id"], "I'll cancel it myself", custom=True)
        self.assertEqual(self.notice()["state"], "picked")
        self.assertIn("I'll cancel it myself", out["send"])


if __name__ == "__main__":
    unittest.main()
