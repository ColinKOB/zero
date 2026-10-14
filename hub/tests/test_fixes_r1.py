"""Internals QA round 1 fixes in the hub and the plugin's tool text (docs/qa/2026-10-08-internals-fixes-r1.md).

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import board, config, logins, store, team, turns  # noqa: E402
import plugin_loader  # noqa: E402
from test_onboarding import _fresh_store, _load_app, _real_reload  # noqa: E402

tt = plugin_loader.load()
plugin = tt.consent
REPO = Path(__file__).resolve().parents[2]


class AppCase(unittest.TestCase):
    """The hub's API with a fresh store and consent.db, and Colin's phone already paired."""

    def setUp(self):
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY,
                      config.LOOKS, config.reload_bots, list(config.BOTS), dict(config.BOTS_BY_ID), plugin.DB_PATH,
                      turns.follow_up, board.get_task, board.answer_block, board.comment)
        tmp = Path(tempfile.mkdtemp())
        _fresh_store(tmp)
        plugin.DB_PATH = tmp / "consent.db"
        config.HERMES_HOME = tmp / "hermes"
        for b in (config.BUILTIN + config.OWNER_ZIPS):
            (config.HERMES_HOME / "profiles" / b.id).mkdir(parents=True, exist_ok=True)
        config.NAMES = tmp / "bot-names.json"
        config.REGISTRY = tmp / "bots.json"
        config.LOOKS = tmp / "bot-looks.json"
        config.migrate_registry()
        config.reload_bots = _real_reload
        config._registry_mtime = None
        config.reload_bots()
        self.followed = []

        def fake_follow_up(bot_id, note, session_id, urgent):
            self.followed.append((bot_id, note, session_id, urgent))
            return asyncio.sleep(0)
        turns.follow_up = fake_follow_up
        board.get_task = lambda tid: {"id": tid, "title": "Send the Halloween preview to Miss Dana",
                                      "summary": "To: heron.studio@example.com"}
        self.answered = []
        board.answer_block = lambda tid, note: (self.answered.append((tid, note)) or (True, ""))
        board.comment = lambda tid, note: (True, "")
        self.app.app.dependency_overrides[self.app.require_device] = lambda: "Test iPhone"
        self.client = TestClient(self.app.app)

    def tearDown(self):
        self.app.app.dependency_overrides.clear()
        (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY, config.LOOKS,
         config.reload_bots, bots, by_id, plugin.DB_PATH, turns.follow_up, board.get_task, board.answer_block,
         board.comment) = self.saved
        config.BOTS[:] = bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id)
        store._local.conn = None
        store._schema_ready = False


class CardAnswers(AppCase):
    def decide(self, req, decision):
        return self.client.post(f"/api/signin-requests/{req}", json={"decision": decision})

    def test_not_now_in_a_chat_resumes_the_bot(self):
        """H2: Not Now in a chat told the bot nothing, so the conversation just stopped."""
        req = plugin.request("school", "elearning.lakeview.test", "what is due?", "chat-1", None, why="mismatch")
        r = self.decide(req, "deny")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.followed), 1)
        bot, note, session, urgent = self.followed[0]
        self.assertEqual((bot, session, urgent), ("school", "chat-1", True))
        self.assertIn("said not now", note)
        self.assertIn("Finish what you can without it", note)

    def test_allow_resumes_and_scheduled_cards_do_not(self):
        req = plugin.request("school", "elearning.lakeview.test", "what is due?", "chat-1", None, why="sensitive")
        self.decide(req, "once")
        self.assertIn("Continue with what you were doing", self.followed[0][1])
        sched = plugin.request("money", "chase.com", "", "cron-1", None, why="scheduled")
        self.decide(sched, "deny")
        self.assertEqual(len(self.followed), 1)

    def test_card_lists_kind_for_the_wording(self):
        plugin.request("money", "chase.com", "", None, None, why="background", kind="navigate")
        got = self.client.get("/api/signin-requests").json()["requests"]
        self.assertEqual((got[0]["kind"], got[0]["why"]), ("navigate", "background"))


class ActionCards(AppCase):
    """C1 in the app: the card shows the exact action, Allow Once resumes the bot to run it, rules are reviewable."""

    def card(self, scope="ken@brightpath.test"):
        return plugin.action_request("builder", "send", "", "send email with himalaya to ken@brightpath.test",
                                     "ken@brightpath.test", "To: ken@brightpath.test\n\nHi Theo, thanks.", "fp1",
                                     "chat-1", None, why="asked", scope=scope)

    def test_card_shows_the_exact_message(self):
        self.card()
        got = self.client.get("/api/signin-requests").json()["requests"][0]
        self.assertEqual((got["kind"], got["target"], got["can_always"]), ("send", "ken@brightpath.test", True))
        self.assertIn("Hi Theo, thanks.", got["inputs"])
        self.assertFalse(got["sensitive"])

    def test_allow_tells_the_bot_to_repeat_the_exact_call(self):
        req = self.card()
        self.client.post(f"/api/signin-requests/{req}", json={"decision": "once"})
        self.assertIn("Make exactly the same call again", self.followed[0][1])
        self.assertTrue(plugin.use_approval("builder", "fp1"))

    def test_always_allow_rule_shows_in_settings_and_can_be_removed(self):
        req = self.card()
        self.client.post(f"/api/signin-requests/{req}", json={"decision": "always"})
        rules = self.client.get("/api/permissions").json()["rules"]
        self.assertEqual([(r["bot_id"], r["kind"], r["scope"]) for r in rules], [("builder", "send", "ken@brightpath.test")])
        self.assertEqual(self.client.delete(f"/api/rules/{rules[0]['id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/permissions").json()["rules"], [])
        self.assertEqual(self.client.delete(f"/api/rules/{rules[0]['id']}").status_code, 404)

    def test_actions_are_listed_and_reach_the_chat(self):
        from taffy_hub import watcher
        plugin.log_action("builder", "write", "github.com", "push to the branch fix-flaky-test", "tell")
        self.assertEqual(self.client.get("/api/permissions").json()["actions"][0]["summary"],
                         "push to the branch fix-flaky-test")
        store.set_cursor("consent_action", 0)
        store.set_cursor("consent_request", 10**6)
        store.set_cursor("consent_signin", 10**6)
        asyncio.run(watcher.scan_consent())
        row = store.db().execute("SELECT * FROM feed WHERE kind='action'").fetchone()
        self.assertEqual((row["bot_id"], row["title"], row["body"]), ("builder", "Done", "Push to the branch fix-flaky-test"))


class ApproveIsAGoAhead(AppCase):
    def test_approve_without_a_note_opens_a_button_pass(self):
        """H3: an empty Approve opened no pass, so the approved work's sign-in raised a "working on its own" card."""
        board.approve = lambda tid, note: (True, "")
        r = self.client.post("/api/tasks/t_abc123/approve", json={})
        self.assertEqual(r.status_code, 200, r.text)
        p = plugin.live_pass(task_id="t_abc123")
        self.assertIsNotNone(p)
        self.assertEqual(p["kind"], "approve")
        self.assertIn("Send the Halloween preview to Miss Dana", p["text"])
        self.assertIn("heron.studio@example.com", p["text"])


class WaitingSignins(unittest.TestCase):
    def test_sibling_hosts_match(self):
        """L3: a login saved for crm.bloomerang.co did not answer a task waiting on identity.bloomerang.co."""
        real = board.grouped
        board.grouped = lambda: {"needs_you": [
            {"id": "t_1", "status": "blocked", "assignee": "clients", "title": "Sign in to Bloomerang",
             "block_reason": "URL: https://identity.bloomerang.co/login", "body": ""},
            {"id": "t_2", "status": "blocked", "assignee": "clients", "title": "Sign in to Other",
             "block_reason": "URL: https://other.example.com/login", "body": ""}]}
        try:
            self.assertEqual(board.waiting_signins("crm.bloomerang.co", ["clients"]), ["t_1"])
        finally:
            board.grouped = real


class FakeVault:
    def __init__(self):
        self.items = {}
        self.n = 0

    def add_item(self, kind, label, secret, origin=None):
        self.n += 1
        m = SimpleNamespace(id=f"i{self.n}", kind=kind, label=label, origin=origin,
                            identifier=secret.get("identifier"), identifier_type=secret.get("identifier_type"))
        m.to_dict = lambda m=m: {"id": m.id, "kind": m.kind, "label": m.label, "origin": m.origin,
                                 "identifier": m.identifier}
        self.items[m.id] = m
        return m

    def list_items(self):
        return list(self.items.values())

    def remove_item(self, item_id):
        return self.items.pop(item_id, None) is not None


class SavedLogins(unittest.TestCase):
    """M4: duplicate rows, Remove leaving a copy with the password, and re-saving stacking a third item."""

    def setUp(self):
        self.vault = FakeVault()
        self.real = (logins._store, logins.sign_in_origins, config.BOTS)
        logins._store = lambda bot: self.vault
        logins.sign_in_origins = lambda url: ["https://crm.bloomerang.co", "https://identity.bloomerang.co"]
        self.fake_save = sys.modules.get("agent.vault_store")
        sys.modules["agent"] = SimpleNamespace()
        sys.modules["agent.vault_store"] = SimpleNamespace(LOGIN_IDENTIFIER_TYPES={"email"},
                                                           normalize_origin=lambda u: u,
                                                           normalize_otp_secret=lambda s: s)
        config.BOTS = [config.Bot("clients", "Clients", "", "#000", "x")]

    def tearDown(self):
        logins._store, logins.sign_in_origins, config.BOTS = self.real
        sys.modules.pop("agent", None)
        sys.modules.pop("agent.vault_store", None)

    def save(self, password="one"):
        return logins.save(["clients"], "Bloomerang", "https://crm.bloomerang.co", "colin@example.com", "email",
                           password, None)

    def test_one_row_per_login(self):
        self.save()
        rows = logins.list_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["label"], rows[0]["copies"]), ("Bloomerang", 2))

    def test_remove_deletes_every_copy(self):
        self.save()
        row = logins.list_all()[0]
        self.assertTrue(logins.remove("clients", row["id"]))
        self.assertEqual(self.vault.list_items(), [])

    def test_saving_again_replaces(self):
        self.save("one")
        self.save("two")
        self.assertEqual(len(self.vault.list_items()), 2)
        self.assertEqual(len(logins.list_all()), 1)


class ToolText(unittest.TestCase):
    def test_tally_never_promises_balances_and_names_a_bank_to_relink(self):
        """M7: the tool said it returned balances, which Tally never stores."""
        self.assertNotIn("balances where available", json.dumps(tt.schemas.TALLY))
        self.assertNotIn("a balance from Money", json.dumps(tt.teammates.SCHEMAS))
        out = tt.tools._with_relink({"items": [{"institution": "Chase", "needs_relink": True},
                                               {"institution": "Ally", "needs_relink": False}]})
        self.assertIn("Chase needs Colin to sign in again", out["needs_relink"])
        self.assertNotIn("Ally", out["needs_relink"])

    def test_no_stale_taffy_in_bot_or_colin_facing_text(self):
        """M8: tool text said "Taffy", and a closed task told Colin "Colin closed this himself in the Taffy app"."""
        for module in (tt.teammates, tt.share):
            self.assertNotIn("Taffy", json.dumps(getattr(module, "SCHEMAS", None) or module.SCHEMA))
        self.assertNotIn("Taffy", tt.OTHERS_HEAR)
        src = (Path(__file__).resolve().parents[1] / "taffy_hub" / "board.py").read_text()
        self.assertIn("You closed this yourself.", src)
        self.assertNotIn("Taffy app", src)

    def test_connections_tool_is_off(self):
        """M12: Hermes's connections tool could install MCP servers or start OAuth with no card."""
        if not (REPO / "alien" / "bin" / "set-toolsets.py").exists():
            self.skipTest("repo-only check (alien runs the hub without the repo)")
        self.assertNotIn('"connections"', (REPO / "alien" / "bin" / "set-toolsets.py").read_text().split("CORE =")[1]
                         .split("]")[0])
        blocked = tt.gate.pre_tool_call("manage_connections", {"action": "add"}, session_id="s1")
        self.assertEqual(blocked["action"], "block")


class LeadName(unittest.TestCase):
    def test_specialist_house_rules_follow_a_renamed_lead(self):
        """M8: after onboarding named the lead Sunny, specialists still said "Zero talks with Colin"."""
        if not (REPO / "alien" / "profiles" / "school" / "SOUL.md").exists():
            self.skipTest("repo-only check (alien runs the hub without the repo)")
        text = (REPO / "alien" / "profiles" / "school" / "SOUL.md").read_text()
        renamed = team._with_lead_name(text, "Sunny")
        self.assertIn("You are one of Sunny's specialists", renamed)
        self.assertIn("Sunny talks with Colin", renamed)
        self.assertIn("the same way Sunny would", renamed)
        self.assertNotIn("Zero talks with Colin", renamed)
        self.assertEqual(team._with_lead_name(renamed, "Sunny"), renamed)



def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()


class OnboardingReachesTheZips(AppCase):
    """H4 and L5: onboarding answers were saved and never read; the call greeted everyone as Colin."""

    def setUp(self):
        super().setUp()
        from taffy_hub import onboarding
        self.onboarding = onboarding
        self.real_person = onboarding.PERSON_FILE
        onboarding.PERSON_FILE = config.HUB_DIR / "person.json"
        for home in (config.HERMES_HOME, config.HERMES_HOME / "profiles" / "school"):
            (home / "memories").mkdir(parents=True, exist_ok=True)
        (config.HERMES_HOME / "memories" / "USER.md").write_text("Style: short answers.")

    def tearDown(self):
        self.onboarding.PERSON_FILE = self.real_person
        super().tearDown()

    def post(self):
        body = {"agent_name": "", "person_name": "Sam Rivera", "voice": "kore",
                "about": {"work": "I teach middle school science", "family": "Jo and two dogs"}}
        r = self.client.post("/api/onboarding", json=body)
        self.assertEqual(r.status_code, 200, r.text)

    def test_answers_go_into_every_user_md_and_the_name_is_used(self):
        self.post()
        lead = (config.HERMES_HOME / "memories" / "USER.md").read_text()
        school = (config.HERMES_HOME / "profiles" / "school" / "memories" / "USER.md").read_text()
        for text in (lead, school):
            self.assertIn("- Work: I teach middle school science", text)
            self.assertIn("- Name: Sam Rivera", text)
        self.assertTrue(lead.startswith("Style: short answers."))
        self.post()
        self.assertEqual((config.HERMES_HOME / "memories" / "USER.md").read_text().count("onboarding:start"), 1)
        self.assertEqual(json.loads(self.onboarding.PERSON_FILE.read_text())["first_name"], "Sam")
        from taffy_hub import voice
        self.assertEqual(voice.greeting(), "Hey Sam, what's up?")
        from taffy_hub import profile
        self.assertEqual(profile.summary(None)["name"], "Sam")
        self.assertIn("What You Told Me", [n["title"] for n in profile.notes()])

    def test_the_leads_next_chat_turn_mentions_one_answer_once(self):
        self.post()
        ctx = team.context_for("default", "chat-9", first=True)
        self.assertIn("Jo and two dogs", ctx)
        self.assertNotIn("Jo and two dogs", team.context_for("default", "chat-9", first=False))

    def test_no_onboarding_keeps_colin(self):
        from taffy_hub import voice
        self.assertEqual(voice.greeting(), "Hey Colin, what's up?")
        self.assertNotIn("how are you doing", voice.greeting())


class HandOffNews(AppCase):
    def test_the_zip_that_made_the_task_hears_how_it_went(self):
        """M10: Builder blocked on Clients' publish and Clients never heard."""
        from taffy_hub import watcher
        told = []
        real = team.tell
        team.tell = lambda frm, to, text, sid, **kw: told.append((frm, to, text)) or "ok"
        try:
            ev = {"created_by": "clients", "assignee": "builder", "title": "Publish Halloween v4", "task_id": "t_9"}
            watcher._tell_creator(ev, "task_blocked", "Sign in to Netlify")
            watcher._tell_creator({**ev, "created_by": "default"}, "task_done", "")
            watcher._tell_creator(ev, "task_started", "")
        finally:
            team.tell = real
        self.assertEqual(len(told), 1)
        self.assertEqual(told[0][:2], ("builder", "clients"))
        self.assertIn("is blocked", told[0][2])


class UserNotesStayInStep(AppCase):
    def test_specialists_follow_the_leads_copy(self):
        """M13: each Zip had its own copy of USER.md, and they drifted."""
        lead = config.HERMES_HOME / "memories" / "USER.md"
        lead.parent.mkdir(parents=True, exist_ok=True)
        lead.write_text("Colin moved to Ohio.")
        self.assertIn("school", team.sync_user_notes())
        self.assertEqual((config.HERMES_HOME / "profiles" / "school" / "memories" / "USER.md").read_text(),
                         "Colin moved to Ohio.")
        self.assertEqual(team.sync_user_notes(), [])


class SettingsRows(AppCase):
    def test_hand_sign_ins_are_listed(self):
        """M5: after Sign In Myself the site never appeared in Settings."""
        plugin.mark_signed_in("school", "elearning.lakeview.test")
        rows = self.client.get("/api/permissions").json()["permissions"]
        self.assertIn(("school", "lakeview.test"), [(r["bot_id"], r["site"]) for r in rows])


class VaultText(unittest.TestCase):
    def test_vault_hint_and_save_login_point_at_the_app(self):
        """L1: Hermes told the bot to have the user run `hermes vault add`."""
        out = tt.transform_result(tool_name="browser_vault_list", result=json.dumps(
            {"items": [], "hint": "To save a credential: run `hermes vault add` in a terminal, or use Desktop."}))
        self.assertNotIn("hermes vault add", out)
        self.assertIn("ask_to_sign_in", json.loads(out)["hint"])
        self.assertIn("ask_to_sign_in", tt.gate.pre_tool_call("browser_vault_save_login", {}, session_id="s")["message"])
