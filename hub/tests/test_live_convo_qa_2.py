"""Live conversation QA, round 2, on the test home (docs/qa/2026-10-10-live-convo-qa-2.md): round 1's open items and
what the round 2 turns showed, kept fixed. Each test names its item and the evidence rows (#) of
docs/research/2026-10-09-grokbot-dots-user-feedback.md.

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, home, noticers, notices, onboarding, store, team  # noqa: E402
from test_proactivity import ProCase, run  # noqa: E402
import plugin_loader  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "alien" / "shared" / "zip-templates.json"
SCRIPT = REPO / "alien" / "bin" / "update-soul-fluid-lines.py"
HARNESS = Path(__file__).resolve().parent / "harness"
LIB = REPO / "alien" / "bin" / "zero-home-lib.py"
TRELLO_ASIDE = " (a Trello board when one is connected)"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# MARK: Item 3. Kinds of question chosen by what the person told onboarding

class NoticersFitThePerson(ProCase):
    """Round 1: "School Deadlines With Nothing Planned" was listed in the Settings of Sam, a bakery owner (#73: be able
    to say nothing; #84). Which kinds fit comes from onboarding, the Zips and the home's keywords, not a list of homes."""

    def setUp(self):
        super().setUp()
        only = {b.id: b for b in config.BUILTIN}
        real_get = home.get
        for p in (mock.patch.dict(config.BOTS_BY_ID, only, clear=True),
                  mock.patch.object(home, "get", lambda key, default=None: None if key == "zips.owner_keywords"
                                    else real_get(key, default)),
                  mock.patch.object(home, "tally", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def told(self, work, age="34"):
        onboarding.save({"agent_name": "Nova", "person_name": "Sam Rivera", "about": {"work": work, "age": age},
                         "sources": {}, "voice": "puck", "no_computer": False, "look": {}})

    def listed(self):
        return {n["id"] for n in notices.status()["noticers"]}

    def test_a_bakery_owner_gets_no_school_or_application_window_questions(self):
        self.told("I run a small bakery")
        self.assertFalse(notices.fits("school_deadline"))
        self.assertFalse(notices.fits("window"))
        listed = self.listed()
        self.assertNotIn("school_deadline", listed)
        self.assertNotIn("window", listed)
        for kind in ("unreplied", "renewal", "conflict", "form", "quiet"):
            self.assertIn(kind, listed)
        self.assertNotIn("bill", listed)
        self.assertNotIn("charge", listed)

    def test_the_money_kinds_follow_money_and_tally(self):
        with mock.patch.dict(config.BOTS_BY_ID, {"money": config.OWNER_ZIPS[3]}):
            self.assertTrue(notices.fits("bill"))
            with mock.patch.object(home, "tally", lambda: None):
                self.assertFalse(notices.fits("charge"))
            with mock.patch.object(home, "tally", lambda: {"url": "http://127.0.0.1:1"}):
                self.assertTrue(notices.fits("charge"))

    def test_a_student_gets_them(self):
        self.told("Nursing student at URI, and I work weekends at a cafe", age="20")
        self.assertIn("school_deadline", self.listed())
        self.assertIn("window", self.listed())

    def test_someone_looking_for_work_gets_windows_but_not_school_deadlines(self):
        self.told("Between jobs, looking for a new role in marketing")
        self.assertTrue(notices.fits("window"))
        self.assertFalse(notices.fits("school_deadline"))

    def test_a_school_zip_or_the_persons_own_choice_keeps_it(self):
        self.told("I run a small bakery")
        notices.set_mode("school_deadline", "on")
        self.assertIn("school_deadline", self.listed())
        with mock.patch.dict(config.BOTS_BY_ID, {"career": config.OWNER_ZIPS[4]}):
            self.assertTrue(notices.fits("window"))

    def test_no_onboarding_answers_means_no_school_questions(self):
        self.assertFalse(notices.fits("school_deadline"))

    def test_a_look_never_runs_a_kind_that_does_not_fit(self):
        self.told("I run a small bakery")
        calls = []

        def fake(name):
            async def f(src):
                calls.append(name)
                return []
            return f
        with mock.patch.object(noticers, "finders", lambda: {k: fake(k) for k in noticers.WAKES["quiet"]}):
            run(noticers.find({"quiet"}, noticers.Sources()))
        self.assertNotIn("school_deadline", calls)
        self.assertNotIn("window", calls)
        self.assertIn("renewal", calls)

    def test_the_quiet_kind_is_named_for_the_lead(self):
        self.told("I run a small bakery")
        with mock.patch.dict(config.BOTS_BY_ID, {"default": config.LEAD.__class__(
                "default", "Nova", "Lead.", "#F2711C", "sparkles", "taffy")}):
            quiet = next(n for n in notices.status()["noticers"] if n["id"] == "quiet")
        self.assertEqual(quiet["name"], "Things Nova Noticed on Its Own")
        self.assertNotIn("Zero", quiet["detail"])


# MARK: Item 2. A dismissed question reaches no one again

class DismissedQuestionsStayDismissed(ProCase):
    """Round 1, S13: Sam dismissed "Do you want to keep OvenSuite after the trial ends?", then Nova and Inbox each
    brought the trial up again in later replies (#69: do not repeat a dismissed nudge unless something changes)."""

    def setUp(self):
        super().setUp()
        team._ready = False
        self.addCleanup(setattr, team, "_ready", False)
        self.raise_(self.cand("renewal:ovensuite", question="Do you want to keep OvenSuite after the trial ends?",
                              evidence="Your October 9 email from OvenSuite says your free trial ends Tuesday, "
                                       "October 13.", deadline=self.soon(72)))
        self.fid = notices.row("renewal:ovensuite")["feed_id"]

    def dismiss(self):
        notices.answer(self.fid, None)

    def test_every_zip_hears_it_once_per_conversation(self):
        self.dismiss()
        note = team.context_for("money", "chat-1", first=True)
        self.assertIn("dismissed these questions in the app", note)
        self.assertIn('- "Haiku question?" (Haiku evidence.)', note)
        self.assertIn("Don't raise them again on your own", note)
        self.assertNotIn("dismissed these questions", team.context_for("money", "chat-1", first=False))
        self.assertIn("Haiku question?", team.context_for("default", "chat-2", first=True))

    def test_a_later_dismissal_reaches_an_ongoing_conversation(self):
        self.assertNotIn("dismissed these questions", team.context_for("money", "chat-1", first=True))
        self.clock += 60
        self.dismiss()
        self.assertIn("Haiku question?", team.context_for("money", "chat-1", first=False))

    def test_an_open_or_answered_question_is_not_listed(self):
        self.assertEqual(notices.passed_on(), [])
        self.assertNotIn("dismissed these questions", team.context_for("money", "chat-1", first=True))

    def test_after_a_week_it_is_not_listed(self):
        self.dismiss()
        self.clock += (notices.PASSED_DAYS + 1) * 86400
        self.assertEqual(notices.passed_on(), [])

    def test_a_teammate_answering_a_question_does_not_get_it(self):
        self.dismiss()
        with mock.patch.object(team, "in_team_session", lambda sid: sid == "team-1"):
            self.assertNotIn("Haiku question?", team.context_for("money", "team-1", first=True))


# MARK: Item 4. No integration the home lacks, in templates, SOULs or tool descriptions

class NoMissingIntegrations(unittest.TestCase):
    """Round 1, S11 and S12: To-Do told Sam to "connect the Trello board" in a home with no Trello and no way to
    connect one; its SOUL said "(a Trello board when one is connected)" (#9: no talk of things the person can't see).
    Round 2 found the email tool listing '[Gmail]/All Mail' in a home with one IMAP mailbox."""

    @classmethod
    def setUpClass(cls):
        cls.pkg = plugin_loader.load()
        cls.team = sys.modules["taffy_tools.team"]
        cls.email = sys.modules["taffy_tools.email_tool"]
        cls.home = sys.modules["taffy_tools.home"]

    def setUp(self):
        if not TEMPLATES.exists():
            self.skipTest("no repo files in a hub-only deploy copy")
        if not hasattr(self.team, "without_missing_integrations") or not hasattr(self.email, "_is_gmail"):
            self.skipTest("the installed plugin predates live convo QA 2")

    def test_the_templates_name_no_integration_a_home_may_lack(self):
        for t in json.loads(TEMPLATES.read_text()):
            text = json.dumps({k: t.get(k) for k in ("pitch", "role", "description", "job", "needs")})
            self.assertNotIn("Trello", text, t["id"])

    def test_a_rendered_template_in_a_home_without_trello_never_says_trello(self):
        old = {"id": "todo", "name": "To-Do", "job": "Use the list {name} already uses" + TRELLO_ASIDE
               + " instead of starting a new one. Connect the Trello board when you can. Stay quiet.",
               "needs": ["Which list you use (Trello, Todoist or another)", "Trello board access",
                         "How you like tasks titled"]}
        with mock.patch.object(self.home, "trello", lambda *a: None):
            out = self.team.render_template(old)
        self.assertNotIn("Trello", json.dumps(out))
        self.assertIn("instead of starting a new one. Stay quiet.", out["job"])
        self.assertEqual(out["needs"], ["Which list you use", "How you like tasks titled"])
        with mock.patch.object(self.home, "trello", lambda *a: {"board_id": "b1"}):
            self.assertIn(TRELLO_ASIDE, self.team.render_template(old)["job"])

    def test_the_gallery_says_the_same(self):
        from taffy_hub import app as hub_app
        with mock.patch.object(home, "trello", lambda *a: None):
            self.assertEqual(hub_app.template_text("Use the list" + TRELLO_ASIDE + " you use."), "Use the list you use.")
        with mock.patch.object(home, "trello", lambda *a: {"board_id": "b1"}):
            self.assertIn("Trello", hub_app.template_text("Use the list" + TRELLO_ASIDE + "."))

    def test_the_soul_script_drops_the_aside_where_there_is_no_board(self):
        script = _module("update_soul_lq2", SCRIPT)
        soul = ("# To-Do\n\nYou are To-Do.\n\n## Your job\n\n**Limits:** Use the list Sam already uses" + TRELLO_ASIDE
                + " instead of starting a new one.\n\n- Quiet while working: x\n")
        reader = mock.Mock(wraps=self.home)
        reader.trello = lambda *a: None
        reader.first_name = lambda *a: "Sam"
        reader.personalize = lambda text, *a: text.replace("Colin", "Sam")
        reader.is_colin = lambda *a: False
        with mock.patch.object(script, "_READER", reader):
            out = script.update_text(soul)
        self.assertNotIn("Trello", out)
        self.assertIn("Use the list Sam already uses instead of starting a new one.", out)
        reader.trello = lambda *a: {"board_id": "b1"}
        with mock.patch.object(script, "_READER", reader):
            self.assertIn(TRELLO_ASIDE, script.update_text(soul))

    def test_the_email_tool_names_gmail_folders_only_for_a_gmail_mailbox(self):
        imap = [{"id": "sam", "address": "sam@zero.test", "label": "Email", "default": True, "provider": "other",
                 "kind": "imap"}]
        with mock.patch.object(self.home, "mail_accounts", lambda *a: imap):
            desc = self.email.schema()["description"]
        self.assertNotIn("Gmail", desc)
        self.assertIn("Folders: INBOX (default), Sent and Drafts.", desc)
        gmail = [dict(imap[0], address="sam@gmail.com", provider="gmail")]
        with mock.patch.object(self.home, "mail_accounts", lambda *a: gmail):
            self.assertIn("'[Gmail]/All Mail'", self.email.schema()["description"])


# MARK: Item 1. The test persona lives in its own time zone

class PersonaZone(unittest.TestCase):
    """Round 1, S2: persona.json said Sam lives in Portland, Oregon, the app (the simulator) sent Eastern, and
    zero-home-lib made the test home in Central, so the time check tested the fixture (#9, #87)."""

    def setUp(self):
        if not LIB.exists():
            self.skipTest("no repo files in a hub-only deploy copy")

    def test_the_persona_says_one_zone_everywhere(self):
        persona = json.loads((HARNESS / "persona.json").read_text())
        self.assertEqual(persona["timezone"], "America/New_York")
        self.assertNotIn("Portland", persona["about"]["home"])
        self.assertIn("Rhode Island", persona["about"]["home"])
        lib = _module("zero_home_lib_lq2", LIB)
        self.assertEqual(lib.PRESETS["test"]["timezone"], persona["timezone"])

    def test_the_simulated_app_sends_the_persona_zone_as_the_app_sends_the_phones(self):
        sys.path.insert(0, str(HARNESS))
        sim = _module("simulate_app_lq2", HARNESS / "simulate_app.py")
        calls = []

        class FakeHub:
            def post(self, path, body):
                calls.append(("POST", path, body))
                return {"checked": True}

            def call(self, method, path, body):
                calls.append((method, path, body))
                return {}

            def get(self, path):
                return {"templates": []}

        persona = json.loads((HARNESS / "persona.json").read_text())
        sim.onboard(FakeHub(), persona, mailbox=False)
        put = next(b for m, p, b in calls if m == "PUT" and p == "/api/home-settings")
        self.assertEqual(put["person"]["timezone"], "America/New_York")
        self.assertEqual(put["person"]["quiet_hours"], {"start": 22, "end": 6})


if __name__ == "__main__":
    unittest.main()


# MARK: Round 2, N4. A card the person's correction made wrong is closed by the Zip

from test_fixes_r1 import AppCase  # noqa: E402
from test_gate import plugin as consent_plugin, tt  # noqa: E402


def _send_card(bot, to, subject, session="s1", task=None):
    inputs = f"From: sam (sam@zero.test)\nTo: {to}\nSubject: {subject}\n\nHi,\n\nThe oven repair is Monday at 9.\n"
    return consent_plugin.action_request(bot, "send", "", f'Email {to} about "{subject}"', to, inputs,
                                         f"fp-{to}-{subject}-{task}", session, task, why="asked")


class ZipClosesItsOwnWrongCard(AppCase):
    """N4: "No, I meant the other Jordan, Jordan Park." Inbox raised a new card for Jordan Park and told Sam to
    decline the card for Jordan Lee himself, which stayed open and approvable (#41, #63: recovering from a small
    mistake should not need the person's attention)."""

    def setUp(self):
        # Checked before AppCase's setUp: a skip raised after it would leave its global changes in place (tearDown
        # does not run after a failed setUp).
        if not hasattr(consent_plugin, "withdraw") or not hasattr(tt.gate, "_older_cards_note"):
            self.skipTest("the installed plugin predates close_card")
        super().setUp()

    def close(self, req, bot="school", session="s1"):
        return asyncio.run(self.app.internal_close_card(
            self.app.MacBody(bot_id=bot, session_id=session, args={"request": req}), None))

    def test_the_new_card_names_the_older_one_and_the_zip_can_close_it(self):
        lee = _send_card("school", "jordan@example.org", "Oven repair Monday")
        park = _send_card("school", "jordan.park@millerflour.test", "Oven repair Monday")
        note = tt.gate._older_cards_note("school", "s1", park)
        self.assertIn(f"request {lee}", note)
        self.assertIn("close_card", note)
        self.assertIn("don't ask", note)
        self.assertNotIn(f"request {park}", note)
        out = self.close(lee)
        self.assertTrue(out["ok"], out)
        self.assertIn("nothing was sent", out["result"])
        self.assertEqual(consent_plugin.get_request(lee)["status"], "withdrawn")
        self.assertEqual([r["id"] for r in consent_plugin.pending()], [park])
        row = self.app.store.db().execute("SELECT * FROM feed WHERE kind='signin_decision' ORDER BY id DESC").fetchone()
        self.assertTrue(row["title"].startswith("Closed: Email jordan@example.org"))
        self.assertEqual(tt.gate._older_cards_note("school", "s1", park), "")

    def test_only_its_own_waiting_chat_cards(self):
        mine = _send_card("school", "a@example.org", "One")
        theirs = _send_card("money", "b@example.org", "Two")
        board = _send_card("school", "c@example.org", "Three", task="t_1")
        self.assertFalse(self.close(theirs)["ok"])
        self.assertFalse(self.close(board)["ok"])
        consent_plugin.decide(mine, True)
        self.assertFalse(self.close(mine)["ok"])
        self.assertFalse(self.close("x")["ok"])
        with mock.patch.object(self.app.team, "in_team_session", lambda sid: True):
            self.assertIn("teammate", self.close(theirs, bot="money")["error"])

    def test_the_tool_reaches_the_hub_and_is_off_in_test_mode(self):
        calls = []
        with mock.patch.object(tt.asksignin.mac, "_call",
                               lambda op, args, timeout, session_id="", area="mac":
                               calls.append((op, args, area)) or {"ok": True, "result": "closed"}):
            out = json.loads(tt.asksignin.close_card({"request": "7"}, session_id="s1"))
        self.assertEqual(out, {"result": "closed"})
        self.assertEqual(calls, [("close_card", {"request": 7}, "chat")])
        self.assertIn("error", json.loads(tt.asksignin.close_card({"request": "seven"})))
        self.assertIn("close_card", tt.gate.TEST_OFF)
        self.assertIn("never ask", tt.asksignin.CLOSE_CARD_SCHEMA["description"])


class OldSignInCardIsRaisedAgain(unittest.TestCase):
    """N2a: Nova told Sam to use last night's sign-in card, which the app shows gray ("Asked Earlier") and which no
    longer marks Nova as waiting. The note on older cards now says to ask again, which brings the card back as
    current (#43, #59)."""

    def test_the_note_says_ask_again(self):
        from taffy_hub import signin
        with mock.patch.object(signin, "old_cards", lambda bot: [(None, {"site": "example.com"})]):
            note = signin.old_cards_note("default")
        self.assertIn("ask_to_sign_in again", note)
        self.assertIn("close_sign_in_card", note)
        self.assertIn("don't send", note)


class TypedAnswerClosesChoiceCard(ProCase):
    """R-N4: Inbox asked "Who should I email about the check going out Monday?" with a choice card; Sam typed "the
    other Jordan, Jordan Park" and the card stayed open under the answer (#43: what waits on the person is what is
    shown; #54)."""

    def test_typing_closes_the_zips_open_choice_cards_but_not_proactive_questions(self):
        from taffy_hub import choices
        choices.offer("money", "Which Jordan?", ["Jordan Lee", "Jordan Park"], during_turn=False)
        self.raise_(self.cand("renewal:x", deadline=self.soon(72)))
        self.assertEqual(choices.close_open("money"), 1)
        rows = {r["title"]: json.loads(r["body"]) for r in store.db().execute("SELECT * FROM feed WHERE kind='choices'")}
        self.assertEqual(rows["Which Jordan?"]["state"], "dismissed")
        self.assertTrue(rows["Which Jordan?"]["typed"])
        self.assertEqual([c["state"] for t, c in rows.items() if c.get("notice")], ["open"])
        fid = store.db().execute("SELECT id FROM feed WHERE title='Which Jordan?'").fetchone()[0]
        self.assertEqual(choices.answer("money", fid, "Jordan Park")["state"], "picked")

    def test_a_card_offered_after_the_message_stays(self):
        from taffy_hub import choices
        choices.offer("money", "Which one?", ["A", "B"], during_turn=False)
        self.assertEqual(choices.close_open("money", before=0), 0)


class NoHermesFooterInChat(unittest.TestCase):
    """N9: Nova's reply ended with Hermes's own "⚠️ File-mutation verifier: 1 file edit(s) FAILED this turn ... Run
    `git status` or `read_file` ..." and a file path (#9: no tool talk the person can't see; #53)."""

    REPLY = ("Nothing is running now. To-Do just finished your Saturday prep list.\n\n"
             "⚠️ File-mutation verifier: 1 file edit(s) FAILED this turn despite any wording above that may suggest "
             "otherwise. Run `git status` or `read_file` to confirm what actually landed.\n"
             "  • `/home/zero-test/taffy/work/todo/2026-10-10-saturday-oct17-prep-list.md` — [write_file] Failed")

    def test_the_footer_is_dropped(self):
        from taffy_hub import chatter
        self.assertEqual(chatter.without_hermes_notes(self.REPLY),
                         "Nothing is running now. To-Do just finished your Saturday prep list.")
        plain = "Run `git status` yourself? No, I mean the File-mutation idea."
        self.assertEqual(chatter.without_hermes_notes(plain), plain)

    def test_replies_and_history_never_carry_it(self):
        from taffy_hub import hermes
        import httpx
        sse = f"event: assistant.completed\ndata: {json.dumps({'content': self.REPLY})}\n\n"
        real = hermes.httpx.AsyncClient
        factory = lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=sse)), **kw)  # noqa: E731

        async def run():
            return [ev async for ev in hermes.stream_turn("default", "what's going on?")]
        with mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")), \
                mock.patch.object(hermes, "_headers", lambda bot_id: {}), \
                mock.patch.object(hermes.httpx, "AsyncClient", factory):
            events = asyncio.run(run())
        reply = next(e["text"] for e in events if e["type"] == "reply")
        self.assertEqual(reply, "Nothing is running now. To-Do just finished your Saturday prep list.")
        shown = hermes._shown([{"role": "assistant", "content": self.REPLY, "id": 1}])
        self.assertNotIn("verifier", json.dumps(shown))


class FooterAndShortReplyKeysAgree(unittest.TestCase):
    """After night review 2: a reply shortened as it streamed is found again from the chat history. Both sides drop
    Hermes's verifier footer, so the short version's key is the same; an interrupted run is never a reply."""

    def test_keys_agree(self):
        try:
            from taffy_hub import shortreply
        except ImportError:
            self.skipTest("no short replies here")
        from taffy_hub import chatter, hermes
        streamed = chatter.without_hermes_notes(NoHermesFooterInChat.REPLY)
        shown = hermes._shown([{"role": "assistant", "content": NoHermesFooterInChat.REPLY, "id": 1}])[0]["text"]
        self.assertEqual(shortreply._key("default", streamed), shortreply._key("default", shown))
        self.assertTrue(chatter.interrupted(chatter.without_hermes_notes(
            "Operation interrupted: waiting for model response (2.7s elapsed).")))
