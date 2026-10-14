"""Delete Everything (erase.py and POST /api/delete-everything): what the hub removes, what it must leave alone,
and the checks in front of it.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien:
~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, erase, logins, onboarding, store, team, turns  # noqa: E402

ABOUT = f"{onboarding.ABOUT_START}\nAbout Sam\n- Name: Sam\n- Work: Teacher\n{onboarding.ABOUT_END}"
HERMES_ENTRY = "Sam prefers short answers."
DELIM = onboarding.ENTRY_DELIMITER


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, team.NOTES_FILE, logins.list_all, logins.remove)
        self.tmp = Path(tempfile.mkdtemp())
        self.hub = self.tmp / "hub-state"
        self.hermes = self.tmp / ".taffy"
        self.hub.mkdir()
        self.hermes.mkdir()
        config.HUB_DIR = self.hub
        config.HUB_DB = self.hub / "hub.db"
        config.HERMES_HOME = self.hermes
        team.NOTES_FILE = self.tmp / "shared" / "team-notes.md"
        store._local.conn = None
        store._schema_ready = False
        team._ready = False
        self.vault = [{"bot": "money", "id": "login-1"}, {"bot": "default", "id": "login-2"}]
        self.removed_logins = []
        logins.list_all = lambda: list(self.vault)
        logins.remove = lambda bot, item: self.removed_logins.append((bot, item)) or True

    def tearDown(self):
        (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, team.NOTES_FILE, logins.list_all, logins.remove) = self.saved
        store._local.conn = None
        store._schema_ready = False
        team._ready = False

    def count(self, table):
        return store.db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def seed(self):
        """A hub that has been used: chats, tapbacks, notes, calls, pictures and answers, and the Hermes files."""
        store.db()
        for i in range(3):
            store.add_feed("message", "default", None, f"Item {i}", "a private note")
        store.set_session("default", "sess-1")
        store.set_session("default#team", "sess-2")
        store.set_reaction("default", "m1", "colin", "heart", "excerpt")
        store.set_setting("onboarding", {"person_name": "Sam"})
        store.set_setting("onboarding_ack", {"answer": "Teacher"})
        store.set_setting("muted_bots", ["money"])
        store.set_setting("held_pushes", [["Zero", "Dinner is at 6"]])
        store.set_setting("save_call_transcripts", False)
        store.set_setting("health", {"bots": True})
        store.set_cursor("consent_request", 7)
        team._db().execute("INSERT INTO team_notes (bot_id, text, created_at) VALUES ('money', 'Rent is due on the 1st', 1)")
        team._db().execute("INSERT INTO team_msgs (kind, from_bot, to_bot, text, created_at) "
                           "VALUES ('ask', 'money', 'default', 'q', 1)")
        team._db().commit()
        team._write_notes_file()
        store.db().execute("INSERT INTO notices (subject, noticer, state, created, updated) VALUES ('s', 'renewal', 'open', 1, 1)")
        store.db().execute("INSERT INTO notice_log (at, kind) VALUES (1, 'push')")
        store.db().commit()
        (self.hub / "calls" / "2026-10-08").mkdir(parents=True)
        (self.hub / "calls" / "2026-10-08" / "c1.jsonl").write_text('{"event": "start"}\n')
        (self.hub / "media").mkdir()
        (self.hub / "media" / ("a" * 32 + ".jpg")).write_bytes(b"jpg")
        (self.hub / "briefings").mkdir()
        (self.hub / "briefings" / "default.md").write_text("Sam teaches.")
        (self.hub / "person.json").write_text('{"name": "Sam"}')
        (self.hub / "calendar.json").write_text("[]")
        (self.hub / "internal-key").write_text("secret")
        (self.hub / "bot-names.json").write_text('{"default": "Pip"}')
        (self.hermes / "memories").mkdir()
        (self.hermes / "memories" / "USER.md").write_text(HERMES_ENTRY + DELIM + ABOUT + "\n")
        (self.hermes / "profiles" / "money" / "memories").mkdir(parents=True)
        (self.hermes / "profiles" / "money" / "memories" / "USER.md").write_text(ABOUT + "\n")
        (self.hermes / "state.db").write_bytes(b"hermes sessions")
        (self.hermes / "SOUL.md").write_text("You are Zero.")
        (self.hermes / "config.yaml").write_text("model: x")
        (self.hermes / "profiles" / "money" / "skills").mkdir()
        (self.hermes / "profiles" / "money" / "skills" / "tip.md").write_text("a skill")


class Erase(Base):
    def test_removes_the_hubs_data(self):
        self.seed()
        out = erase.delete_everything()
        self.assertTrue(out["ok"])
        for table in ("feed", "bot_sessions", "reactions", "team_notes", "team_msgs", "notices", "notice_log"):
            self.assertEqual(self.count(table), 0, table)
        self.assertIsNone(store.get_setting("onboarding"))
        self.assertIsNone(store.get_setting("onboarding_ack"))
        self.assertIsNone(store.get_setting("muted_bots"))
        self.assertIsNone(store.get_setting("held_pushes"))
        self.assertFalse((self.hub / "calls").exists())
        self.assertFalse((self.hub / "media").exists())
        self.assertFalse((self.hub / "briefings").exists())
        self.assertFalse((self.hub / "person.json").exists())
        self.assertFalse((self.hub / "calendar.json").exists())
        self.assertNotIn("Rent is due", team.NOTES_FILE.read_text())
        self.assertEqual(out["removed"]["feed"], 3)
        self.assertEqual(out["removed"]["calls"], 1)
        self.assertEqual(out["removed"]["saved_logins"], 2)
        self.assertEqual(sorted(self.removed_logins), [("default", "login-2"), ("money", "login-1")])

    def test_leaves_hermes_and_hub_state_alone(self):
        self.seed()
        erase.delete_everything()
        self.assertEqual((self.hermes / "state.db").read_bytes(), b"hermes sessions")
        self.assertEqual((self.hermes / "SOUL.md").read_text(), "You are Zero.")
        self.assertEqual((self.hermes / "config.yaml").read_text(), "model: x")
        self.assertEqual((self.hermes / "profiles" / "money" / "skills" / "tip.md").read_text(), "a skill")
        self.assertEqual((self.hub / "internal-key").read_text(), "secret")
        self.assertEqual((self.hub / "bot-names.json").read_text(), '{"default": "Pip"}')
        self.assertIs(store.get_setting("save_call_transcripts"), False)
        self.assertEqual(store.get_setting("health"), {"bots": True})
        self.assertEqual(store.get_cursor("consent_request"), 7)

    def test_only_the_hubs_about_entry_leaves_the_memory_files(self):
        self.seed()
        erase.delete_everything()
        lead = (self.hermes / "memories" / "USER.md").read_text()
        self.assertEqual(lead.strip(), HERMES_ENTRY)
        self.assertNotIn("About Sam", lead)
        self.assertEqual((self.hermes / "profiles" / "money" / "memories" / "USER.md").read_text(), "")

    def test_a_link_in_a_hub_folder_is_removed_without_touching_its_target(self):
        self.seed()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("not Zero's")
        (self.hub / "outbox").symlink_to(outside)
        erase.delete_everything()
        self.assertFalse((self.hub / "outbox").exists())
        self.assertEqual((outside / "keep.txt").read_text(), "not Zero's")

    def test_keeps_paired_devices_and_removes_the_callers_push_token(self):
        self.seed()
        token = store.redeem_pairing_code(store.new_pairing_code(), "iPhone")
        other = store.redeem_pairing_code(store.new_pairing_code(), "Mac")
        from taffy_hub import apns
        apns.save_token(store._hash(token), "a" * 64, "development", "iphone")
        apns.save_token(store._hash(other), "b" * 64, "development", "mac")
        erase.delete_everything(token)
        self.assertEqual(self.count("devices"), 2)
        self.assertEqual(self.count("push_tokens"), 1)

    def test_works_on_a_hub_with_nothing_in_it(self):
        out = erase.delete_everything()
        self.assertTrue(out["ok"])
        self.assertEqual(out["removed"]["feed"], 0)

    def test_running_it_twice_is_fine(self):
        self.seed()
        erase.delete_everything()
        self.assertTrue(erase.delete_everything()["ok"])

    def test_refuses_while_a_zip_is_answering(self):
        self.seed()
        real = turns.busy_bots
        turns.busy_bots = lambda: ["default"]
        try:
            with self.assertRaises(erase.Busy):
                erase.delete_everything()
        finally:
            turns.busy_bots = real
        self.assertEqual(self.count("feed"), 3)

    def test_strip_about_keeps_the_neighbouring_entries(self):
        both = "first" + DELIM + ABOUT + DELIM + "last\n"
        self.assertEqual(erase.strip_about(both), "first" + DELIM + "last\n")
        self.assertEqual(erase.strip_about("first" + DELIM + ABOUT + "\n"), "first\n")
        self.assertEqual(erase.strip_about(ABOUT + DELIM + "last"), "last\n")
        self.assertEqual(erase.strip_about(ABOUT + "\n"), "")
        self.assertIsNone(erase.strip_about("only Hermes wrote this"))


class Endpoint(Base):
    def setUp(self):
        super().setUp()
        from test_onboarding import _load_app
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.app.app.dependency_overrides.clear()
        self.client = TestClient(self.app.app)
        self.token = store.redeem_pairing_code(store.new_pairing_code(), "Test iPhone")
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def post(self, body, headers=None):
        return self.client.post("/api/delete-everything", json=body, headers=self.auth if headers is None else headers)

    def test_needs_a_paired_device(self):
        self.seed()
        self.assertEqual(self.post({"confirm": "delete everything"}, headers={}).status_code, 401)
        self.assertEqual(self.post({"confirm": "delete everything"}, headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertEqual(self.count("feed"), 3)

    def test_needs_the_confirmation_phrase(self):
        self.seed()
        self.assertEqual(self.post({}).status_code, 400)
        self.assertEqual(self.post({"confirm": "yes"}).status_code, 400)
        self.assertEqual(self.count("feed"), 3)
        self.assertTrue((self.hub / "calls").exists())

    def test_deletes_and_keeps_the_device_signed_in(self):
        self.seed()
        r = self.post({"confirm": "Delete Everything"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["removed"]["feed"], 3)
        self.assertEqual(self.count("feed"), 0)
        self.assertFalse((self.hub / "calls").exists())
        self.assertEqual(self.client.post("/api/unpair", headers=self.auth).status_code, 200)

    def test_says_so_when_a_zip_is_answering(self):
        self.seed()
        real = turns.busy_bots
        turns.busy_bots = lambda: ["money"]
        try:
            r = self.post({"confirm": "delete everything"})
        finally:
            turns.busy_bots = real
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.count("feed"), 3)


if __name__ == "__main__":
    unittest.main()
