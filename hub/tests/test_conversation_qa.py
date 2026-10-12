"""Conversation QA, Oct 9 (docs/qa/2026-10-09-conversation-qa.md): how the voice sounds, what the Zips say, and the
hub wording Colin reads. Each test names the hiccup it keeps from coming back.

Run: python3 -m unittest discover -s hub/tests
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, mail, notices, noticers, prepared, store, voice, watcher  # noqa: E402
from test_fixes_r1 import AppCase, plugin  # noqa: E402
from test_proactivity import FakeSources, Noticers, at, env, run  # noqa: E402
from test_gate import tt  # noqa: E402
import home_fixture  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "alien" / "bin" / "update-soul-style-lines.py"


def _script():
    spec = importlib.util.spec_from_file_location("update_soul_style_lines", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# MARK: The voice

class Greeting(unittest.TestCase):
    def test_one_sentence_with_a_comma(self):
        """Hiccup 3: "Hey Colin. What's up?" was spoken as "Hey__Colin, what's up." with a gap and a flat end."""
        from taffy_hub import onboarding
        saved = onboarding.person_name
        try:
            onboarding.person_name = lambda full=True: "Colin"
            self.assertEqual(voice.greeting(), "Hey Colin, what's up?")
            onboarding.person_name = lambda full=True: "Sam"
            self.assertEqual(voice.greeting(), "Hey Sam, what's up?")
        finally:
            onboarding.person_name = saved
        self.assertNotIn(". ", voice.greeting())
        self.assertTrue(voice.greeting().endswith("?"))


class VoicePrompt(unittest.TestCase):
    def setUp(self):
        home_fixture.use_in(self, None)   # Colin's home: US units, Eastern time
        from taffy_hub import who
        self.first = who.first()
        self.prompt = voice.voice_prompt("default", "You are Zero, Colin's assistant.")

    def test_carries_the_briefing_and_the_rules(self):
        self.assertTrue(self.prompt.startswith("You are Zero, Colin's assistant."))
        self.assertIn("How to sound:", self.prompt)
        lead = config.BOTS_BY_ID["default"].name
        self.assertIn(f"You are {lead};", self.prompt)   # the bot's name, not a placeholder
        self.assertNotIn("{name}", self.prompt)
        self.assertNotIn("{rules}", self.prompt)

    def test_question_rules(self):
        """Hiccups 4 and 14: short yes-or-no questions, no either-or, no two questions joined by a comma."""
        for words in ("yes-or-no", "Never ask an either-or question", "recommend one option",
                      "Never join two questions with a comma", "its own question mark"):
            self.assertIn(words, self.prompt, words)

    def test_opener_filler_reminder_and_hand_off_rules(self):
        """Hiccups 5 and 7, plus repeats: no comma openers, wait through "um", say a reminder once."""
        for words in ('"Oh," "Well," "So,"', '"um" or "uh"', f"stay quiet and let {self.first} finish",
                      "Say a reminder once", "Vary your hand-off lines", "do not repeat the question"):
            self.assertIn(words, self.prompt, words)

    def test_no_lists_links_paths_units_or_internals(self):
        for words in ("No lists", "no file paths", "Fahrenheit", "Eastern", "American spelling",
                      "Never mention how you work", "no servers, models, tools"):
            self.assertIn(words, self.prompt, words)

    def test_money_is_ready_for_his_tap_not_his_own_chore(self):
        """Capability: the voice does not tell Colin to place the order himself; it gets it ready for one tap."""
        f = self.first
        self.assertIn(f"waits for {f}'s okay on {f}'s phone", self.prompt)
        self.assertIn(f"so {f} can finish it from the app", self.prompt)
        self.assertIn(f"Never tell {f} to do the whole thing when you can get it ready", self.prompt)
        self.assertNotIn("Colin does the final step himself", self.prompt)
        self.assertIn("Never say you cannot do something before handing it off", self.prompt)

    def test_job_guide_asks_for_speakable_plain_replies(self):
        for words in ("no lists, no links, no file paths", "Fahrenheit", "Eastern time", "yes-or-no question"):
            self.assertIn(words, voice.JOB_GUIDE, words)

    def test_the_rules_use_the_bots_name(self):
        saved = dict(config.BOTS_BY_ID)
        try:
            config.BOTS_BY_ID["money"] = type("B", (), {"name": "Money"})()
            self.assertIn("You are Money;", voice.voice_prompt("money", "brief"))
        finally:
            config.BOTS_BY_ID.clear()
            config.BOTS_BY_ID.update(saved)


class Speakable(unittest.TestCase):
    def test_numbered_lists_become_sentences(self):
        text = "Here it is.\n\n1. **Form A** due tonight\n2. Form C Sunday\n\nThat is all."
        self.assertEqual(voice.speakable(text), "Here it is. Form A due tonight. Form C Sunday. That is all.")

    def test_bullets_at_the_start_and_links(self):
        self.assertEqual(voice.speakable("- one\n- two\nSee https://x.y/z now."), "one. two. See the link now.")

    def test_file_paths_and_picture_lines_are_not_read_aloud(self):
        text = ("The transcript is at /home/jdoe/taffy/work/school/2026-10-07-aden.md.\n"
                "MEDIA:/home/jdoe/taffy/work/taffy/shot.png\nDone.")
        self.assertEqual(voice.speakable(text), "The transcript is at a file in my work folder. Done.")
        self.assertEqual(voice.speakable("Saved to ~/taffy/work/money/snapshot.md"),
                         "Saved to a file in my work folder")

    def test_questions_and_plain_text_are_unchanged(self):
        self.assertEqual(voice.speakable("Question: keep agentmax?"), "Question: keep agentmax?")
        self.assertEqual(voice.speakable("Tomorrow is sunny with a high of 77."), "Tomorrow is sunny with a high of 77.")

    def test_still_cuts_at_a_sentence_end(self):
        long = "First sentence here. " * 40
        out = voice.speakable(long, 200)
        self.assertLessEqual(len(out), 200)
        self.assertTrue(out.endswith("."))


# MARK: Cards and the chat

class StraySessionCards(AppCase):
    """Hiccup 12: a card from a run outside the app (a sandbox test) resumed Colin's chat, so Career told him
    "nothing will get pushed to main" about a push he had never asked for."""

    def card(self, session):
        return plugin.action_request("career", "publish", "", "Push to main, which can publish it", "main", "",
                                     "fp-stray", session, None, why="background")

    def test_stray_session_is_known_only_once_the_zip_has_a_chat(self):
        from taffy_hub import app as hub_app
        self.assertFalse(hub_app.stray_session("career", "20261008_193832_dd7b37"))   # nothing to compare with yet
        store.set_session("career", "api_chat")
        store.set_session("career#team", "api_team")
        self.assertFalse(hub_app.stray_session("career", "api_chat"))
        self.assertFalse(hub_app.stray_session("career", "api_team"))
        self.assertFalse(hub_app.stray_session("career", None))
        self.assertTrue(hub_app.stray_session("career", "20261008_193832_dd7b37"))

    def test_not_now_on_a_stray_card_resumes_nothing_and_is_recorded(self):
        store.set_session("career", "api_chat")
        req = self.card("20261008_193832_dd7b37")
        r = self.client.post(f"/api/signin-requests/{req}", json={"decision": "deny"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.followed, [])
        kinds = [(f["kind"], f["title"]) for f in store.activity_for("career")]
        self.assertIn(("signin_decision", "Declined: Push to main, which can publish it"), kinds)

    def test_allow_on_a_stray_card_resumes_nothing_either(self):
        store.set_session("career", "api_chat")
        req = self.card("20261008_193832_dd7b37")
        self.client.post(f"/api/signin-requests/{req}", json={"decision": "once"})
        self.assertEqual(self.followed, [])

    def test_a_chat_card_still_resumes_the_chat(self):
        store.set_session("career", "api_chat")
        req = self.card("api_chat")
        self.client.post(f"/api/signin-requests/{req}", json={"decision": "deny"})
        self.assertEqual(len(self.followed), 1)
        self.assertEqual(self.followed[0][2], "api_chat")

    def test_the_card_says_where_it_came_from(self):
        store.set_session("career", "api_chat")
        self.card("20261008_193832_dd7b37")
        got = self.client.get("/api/signin-requests").json()["requests"][0]
        self.assertEqual(got["origin"], "This came up in a run outside the app, not in your chat with Career.")


class ScheduledJobWords(unittest.TestCase):
    """Colin saw "Paused a scheduled job: Ocean City 3-7 day surf planner (0 15 * * *)" in his chat."""

    def test_cron_in_plain_words(self):
        self.assertEqual(watcher.cron_words("0 15 * * *"), "every day at 3 p.m.")
        self.assertEqual(watcher.cron_words("30 7 * * *"), "every day at 7:30 a.m.")
        self.assertEqual(watcher.cron_words("0 10 */2 * *"), "every other day at 10 a.m.")
        self.assertEqual(watcher.cron_words("0 0 */3 * *"), "every 3 days at 12 a.m.")
        self.assertEqual(watcher.cron_words("0 9 * * 1-5"), "weekdays at 9 a.m.")
        self.assertEqual(watcher.cron_words("0 19 * * 0"), "every Sunday at 7 p.m.")
        self.assertEqual(watcher.cron_words("15 8 * * 1,3,5"), "every Monday, Wednesday and Friday at 8:15 a.m.")
        self.assertEqual(watcher.cron_words("0 12 1 * *"), "on the 1st of every month at 12 p.m.")
        self.assertEqual(watcher.cron_words("*/5 * * * *"), "*/5 * * * *")   # not a shape worth guessing at
        self.assertEqual(watcher.cron_words(""), "")

    def test_a_jobs_when_prefers_words(self):
        self.assertEqual(watcher._when({"schedule": {"kind": "cron", "expr": "0 15 * * *", "display": "0 15 * * *"}}),
                         "every day at 3 p.m.")
        self.assertEqual(watcher._when({"schedule": {"expr": "0 15 * * *", "display": "Daily at 3 p.m."}}),
                         "Daily at 3 p.m.")
        self.assertEqual(watcher._when({"schedule": {"run_at": "2026-10-21 10:00"}}), "2026-10-21 10:00")


class PlumbingWords(unittest.TestCase):
    """Every card on the morning of Oct 9 began "The hub found ..." even though the prompt forbids it."""

    def test_the_hub_becomes_the_zip(self):
        self.assertEqual(notices.unplumbed("The hub found Colin's September 25 email titled \"August Hours\"."),
                         "I found Colin's September 25 email titled \"August Hours\".")
        self.assertEqual(notices.unplumbed("The briefing says Colin has not approved it."),
                         "Your notes say Colin has not approved it.")
        self.assertEqual(notices.unplumbed("Money has not seen this payee before, and none of the briefing material "
                                           "mentions it."),
                         "Money has not seen this payee before, and none of your notes mention it.")
        self.assertEqual(notices.unplumbed("the question the hub reported is not in the notice"),
                         "the question I reported is not in the notice")

    def test_clean_applies_it(self):
        self.assertEqual(notices.clean("The hub found a $5.68 charge.", 200), "I found a $5.68 charge.")


class CourseSiteNotices(Noticers):
    """Two "Bill Hart" cards on Oct 9 were a feedback notice and a forum post copy from the course site, sent in
    the teacher's name. Nobody answers those by email."""

    def test_notification_subjects_and_footers(self):
        self.assertTrue(mail.is_notification("Bill Hart has given feedback for assignment 5.6 Term Paper Draft"))
        self.assertTrue(mail.is_notification("202610 POL 101 A01: Don't forget this special assignment"))
        self.assertTrue(mail.is_notification("Re: anything", "Hi all.\n\nThis is a copy of a message posted in the "
                                                             "Unit 6 forum. To change your forum digest preferences..."))
        self.assertFalse(mail.is_notification("Re: Halloween preview", "Can the pumpkin banner be smaller?"))
        self.assertFalse(mail.is_notification("Quote for the sign"))

    def test_reply_waiting_skips_them(self):
        hart = ("Bill Hart", "bhart@lakeview.test")
        karen = ("Miss Dana", "karen@blueheron.test")
        self.src.mail[("lakeview", "INBOX")] = [
            env(1, "Bill Hart has given feedback for assignment 5.6 Term Paper Draft", frm=hart,
                date=at(10, 5, 9), account="lakeview"),
            env(2, "202610 POL 101 A01: Don't forget this special assignment for Unit Six", frm=hart,
                date=at(10, 6, 9), account="lakeview"),
            env(3, "Your Form C", frm=hart, date=at(10, 5, 10), account="lakeview"),
        ]
        self.src.mail[("personal", "INBOX")] = [env(4, "Halloween preview", frm=karen, date=at(10, 6, 9))]
        self.src.bodies = {
            "1": "Bill Hart has given feedback. Would you like to view the feedback? View the feedback here.",
            "2": "Did you all see the wrap-up video? This is a copy of a message posted in the forum.",
            "3": "Colin, can you sign and date Form C and send it back to me?",
            "4": "Can the pumpkin banner be smaller?",
        }
        found = self.find("reply_waiting")
        self.assertEqual(sorted(c.subject for c in found),
                         ["mail-in:lakeview:bhart@lakeview.test:your form c",
                          "mail-in:personal:karen@blueheron.test:halloween preview"])


class PreparedSendAddress(unittest.TestCase):
    """School's prepared nudge failed once with "'store1234@... <store1234@...>' in to is not an email address"."""

    def plan(self, to_name):
        c = notices.Candidate(noticer="unreplied", subject="mail-sent:lakeview:x:y", owner="school", evidence="e",
                              question="q", lock="l", facts={}, consequence="c", excerpt="",
                              options=[notices.Option("Send this nudge", "send")],
                              draft={"account": "lakeview", "from_addr": "jd910@lakeview.test", "to_name": to_name,
                                     "to_addr": "store1234@theupsstore.com", "subject": "Re: Files"},
                              draft_options=[notices.Option("Send this nudge", "send")])
        return prepared.plan(c, "Hello")["to"]

    def test_a_bare_address_stays_bare(self):
        self.assertEqual(self.plan("store1234@theupsstore.com"), "store1234@theupsstore.com")
        self.assertEqual(self.plan(""), "store1234@theupsstore.com")
        self.assertEqual(self.plan("Alex Shaw"), "Alex Shaw <store1234@theupsstore.com>")


class PushCardTitle(unittest.TestCase):
    """The card "Push to main, 2>, which can publish it" (Oct 8): the redirection is not a branch."""

    def test_redirections_are_not_branches(self):
        tiers = tt.tiers
        # No working folder: on alien the real builder-reset-test folder pushes to a local remote.git, which is
        # rightly not publishing at all, so the shape is tested without the folder.
        cmd = "git push --force origin main 2>&1; echo done"
        found = tiers._git_pushes(cmd, None)
        self.assertEqual([(a.summary, a.target) for a in found], [("Push to main, which can publish it", "main")])


# MARK: The Zips' instructions

@unittest.skipUnless(SCRIPT.exists(), "repo-only check (alien runs the hub without the repo)")
class SoulStyleLines(unittest.TestCase):
    def setUp(self):
        # The script says its lines for the home it runs in (the plugin's reader); these tests check Colin's lines.
        pin = tt.home.pinned(None)
        pin.__enter__()
        self.addCleanup(pin.__exit__, None, None, None)
        self.script = _script()

    def test_every_repo_soul_and_the_house_rules_carry_the_lines(self):
        souls = sorted((REPO / "alien" / "profiles").glob("*/SOUL.md"))
        self.assertEqual(len(souls), 6)
        house = (REPO / "alien" / "plugins" / "taffy-tools" / "team.py").read_text()
        for line in self.script.STYLE_LINES:
            for soul in souls:
                self.assertIn(line, soul.read_text(), f"{soul.parent.name}: {line[:40]}")
            self.assertIn(line, house, f"house rules: {line[:40]}")

    def test_the_lines_cover_the_hiccups(self):
        text = "\n".join(self.script.STYLE_LINES)
        for words in ("Fahrenheit", "American spelling", "Say a reminder once", "never paste a file path",
                      "never say Hermes, alien, the hub", "Never say you can't before checking your tools",
                      "Do not ask \"want me to check?\"", "your first line says what you are doing and why"):
            self.assertIn(words, text, words)
        self.assertNotIn("—", text)

    def test_script_inserts_after_the_anchor_once_and_leaves_the_team_section(self):
        s = self.script
        soul = ("# School\n\n- Style for anything Colin reads: plain sentences.\n- Calls: short.\n\n"
                "<!-- team:start -->\n- Style for anything Colin reads: a copy inside the team block\n<!-- team:end -->\n")
        new = s.update_text(soul)
        self.assertEqual(new.count(s.MARKER), 1)
        lines = new.split("\n")
        self.assertEqual(lines[2], "- Style for anything Colin reads: plain sentences.")
        self.assertEqual(lines[3], s.STYLE_LINES[0])
        self.assertEqual(lines[3 + len(s.STYLE_LINES)], "- Calls: short.")
        self.assertIn("<!-- team:start -->\n- Style for anything Colin reads: a copy inside the team block\n<!-- team:end -->",
                      new)
        self.assertEqual(s.update_text(new), new)                     # idempotent
        self.assertEqual(s.update_text("# Nothing to anchor on\n"), "# Nothing to anchor on\n")
        zero = "- Time zone is America/New_York. He sleeps 11 p.m. to 7 a.m.\n\n<!-- team:start -->\n"
        self.assertTrue(s.update_text(zero).startswith(zero.split("\n")[0] + "\n" + s.STYLE_LINES[0]))

    def test_script_backs_up_and_dry_run_changes_nothing(self):
        s = self.script
        folder = Path(tempfile.mkdtemp())
        path = folder / "SOUL.md"
        original = "# Money\n\n- Style for anything Colin reads: plain.\n"
        path.write_text(original)
        s.main(["--dry-run", str(path)])
        self.assertEqual(path.read_text(), original)
        self.assertEqual(list(folder.glob("SOUL.md.bak-*")), [])
        s.main([str(path)])
        self.assertIn(s.MARKER, path.read_text())
        backups = list(folder.glob("SOUL.md.bak-style-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)
        s.main([str(path)])
        self.assertEqual(len(list(folder.glob("SOUL.md.bak-style-*"))), 1)   # a second run changes nothing


@unittest.skipUnless((REPO / "alien" / "profiles" / "taffy" / "SOUL.md").exists(),
                     "repo-only check (alien runs the hub without the repo)")
class ZeroSoulRules(unittest.TestCase):
    """The lines that keep the known chat hiccups from coming back are in Zero's SOUL."""

    def setUp(self):
        self.text = (REPO / "alien" / "profiles" / "taffy" / "SOUL.md").read_text()

    def test_card_talk_is_one_line(self):
        """Hiccup 10: "Nothing to ask first..." and a note on why the push looked safe."""
        self.assertIn("reply with only one short plain line", self.text)
        self.assertIn("never reason aloud about permissions or why a step looks safe", self.text)

    def test_teammate_questions_get_fresh_data(self):
        """Hiccup 8: a Zip answering a teammate signs in with its own saved login to read what it needs."""
        self.assertIn("you may sign in with your own saved login to read what it needs", self.text)

    def test_no_check_back_and_a_plain_tell(self):
        self.assertIn("never tell Colin to check back", self.text)
        self.assertIn("your first line says what you are doing and why", self.text)



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
