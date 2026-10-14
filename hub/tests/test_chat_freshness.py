"""Chat freshness (docs/qa/2026-10-09-chat-freshness.md): long-lived chats pick up SOUL.md changes, a sign-in card
asked for again moves to the bottom of the chat, Save Login answers the card's own Zip, the screen check never
knocks on the screen's socket, and errors the app shows reach the hub's log.

Nothing here reaches Hermes, the network or a real screen.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import hashlib
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import cli, config, freshness, screen, signin, store, turns, watcher  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402

SOUL = """# Zero

You are Zero, Colin's lead assistant.

## Style

- Length: write like a teammate texting, not a report.
- Units and spelling: American spelling, and Colin's units.
"""
OLD_PROMPT = "# Taffy\n\nYou are Taffy, Colin's lead assistant.\n\n## Style\n\nConversation started: Oct 7\n"


def _state_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE system_prompts (hash TEXT PRIMARY KEY, prompt TEXT NOT NULL);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, system_prompt TEXT, system_prompt_hash TEXT,
                               parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL, end_reason TEXT);
    """)
    return conn


def _add_session(conn, sid, prompt, parent=None, end_reason=None, started=1.0):
    h = None
    if prompt is not None:
        h = hashlib.sha256(prompt.encode()).hexdigest()
        conn.execute("INSERT OR IGNORE INTO system_prompts VALUES (?, ?)", (h, prompt))
    conn.execute("INSERT INTO sessions (id, source, system_prompt_hash, parent_session_id, started_at, end_reason) "
                 "VALUES (?, 'api_server', ?, ?, ?, ?)", (sid, h, parent, started, end_reason))
    conn.commit()


class Freshness(unittest.TestCase):
    """Issue 1: Zero's chat ran on its Oct 7 prompt (it still said "You are Taffy") after every SOUL.md change."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (config.HUB_DB, config.HERMES_HOME, config.HERMES_BIN)
        config.HUB_DB = self.tmp / "hub.db"
        config.HERMES_HOME = self.tmp / "taffy"
        config.HERMES_BIN = self.tmp / "bin" / "taffy"
        store._local.conn = None
        store._schema_ready = False
        (config.HERMES_HOME / "SOUL.md").parent.mkdir(parents=True)
        (config.HERMES_HOME / "SOUL.md").write_text(SOUL)
        self.zero = _state_db(config.HERMES_HOME / "state.db")
        self.school = _state_db(config.HERMES_HOME / "profiles" / "school" / "state.db")
        (config.HERMES_HOME / "profiles" / "school" / "SOUL.md").write_text(SOUL.replace("Zero", "School"))
        self.calls = []
        self.fail = set()
        if "school" not in config.BOTS_BY_ID:
            config.BOTS_BY_ID["school"] = next(b for b in config.OWNER_ZIPS if b.id == "school")
            self.addCleanup(config.BOTS_BY_ID.pop, "school", None)

        def fake_run(cmd, **kw):
            self.calls.append(cmd)
            sid = cmd[cmd.index("repair-prompts") + 1]
            if sid in self.fail:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            conn = self.school if "-p" in cmd else self.zero
            conn.execute("UPDATE sessions SET system_prompt_hash = NULL WHERE id = ?", (sid,))
            conn.commit()
            return subprocess.CompletedProcess(
                cmd, 0, "warning: something\n" + json.dumps({"findings": [], "apply": True, "cleared": [sid]}), "")
        self.run = mock.patch.object(freshness.subprocess, "run", side_effect=fake_run)
        self.run.start()

    def tearDown(self):
        self.run.stop()
        self.zero.close()
        self.school.close()
        config.HUB_DB, config.HERMES_HOME, config.HERMES_BIN = self.saved
        store._local.conn = None
        store._schema_ready = False

    def stored(self, conn, sid):
        return conn.execute("SELECT system_prompt_hash FROM sessions WHERE id = ?", (sid,)).fetchone()[0]

    def test_an_old_prompt_is_cleared_once_and_rebuilt_by_hermes_on_the_next_turn(self):
        _add_session(self.zero, "api_zero", OLD_PROMPT)
        store.set_session("default", "api_zero")
        self.assertEqual(freshness.check(), ["default"])
        self.assertIsNone(self.stored(self.zero, "api_zero"))
        cmd = self.calls[0]
        self.assertEqual(cmd[0], str(config.HERMES_BIN))
        self.assertNotIn("-p", cmd)
        self.assertEqual(cmd[1:], ["sessions", "repair-prompts", "api_zero", "--apply", "--json"])
        # Hermes rebuilds it from the current SOUL.md on the next turn and stores it; nothing more to do after that.
        self.zero.execute("UPDATE sessions SET system_prompt_hash = ? WHERE id = 'api_zero'",
                          (self._store(self.zero, SOUL + "\nMemory: Colin uses US units.\n"),))
        self.zero.commit()
        self.assertEqual(freshness.check(), [])
        self.assertEqual(freshness.check(recheck=True), [])
        self.assertEqual(len(self.calls), 1)

    def _store(self, conn, prompt):
        h = hashlib.sha256(prompt.encode()).hexdigest()
        conn.execute("INSERT OR IGNORE INTO system_prompts VALUES (?, ?)", (h, prompt))
        return h

    def test_a_current_prompt_is_left_alone_so_the_cache_holds(self):
        _add_session(self.zero, "api_zero", "Header\n" + SOUL + "\nConversation started: today\n")
        store.set_session("default", "api_zero")
        self.assertEqual(freshness.check(), [])
        self.assertEqual(self.calls, [])

    def test_a_prompt_that_never_matches_is_cleared_only_once_per_soul_version(self):
        """A line Hermes stores differently (or a SOUL.md past its size cap) must never rebuild every turn."""
        _add_session(self.zero, "api_zero", OLD_PROMPT)
        store.set_session("default", "api_zero")
        freshness.check()
        self.zero.execute("UPDATE sessions SET system_prompt_hash = ? WHERE id = 'api_zero'",
                          (self._store(self.zero, OLD_PROMPT + "rebuilt\n"),))
        self.zero.commit()
        self.assertEqual(freshness.check(), [])
        self.assertEqual(len(self.calls), 1)
        (config.HERMES_HOME / "SOUL.md").write_text(SOUL + "- Just do it: look things up without asking.\n")
        self.assertEqual(freshness.check(), ["default"])
        self.assertEqual(len(self.calls), 2)

    def test_every_lane_of_every_zip_is_checked(self):
        _add_session(self.zero, "api_zero", OLD_PROMPT)
        _add_session(self.school, "api_school", OLD_PROMPT)
        _add_session(self.school, "api_school_team", OLD_PROMPT)
        _add_session(self.school, "api_school_prep", "x\n" + SOUL.replace("Zero", "School"))
        store.set_session("default", "api_zero")
        store.set_session("school", "api_school")
        store.set_session("school#team", "api_school_team")
        store.set_session("school#prep", "api_school_prep")
        store.set_session("ghost", "api_ghost")
        self.assertEqual(sorted(freshness.check()), ["default", "school", "school#team"])
        school_calls = [c for c in self.calls if "-p" in c]
        self.assertEqual(len(school_calls), 2)
        self.assertEqual(school_calls[0][1:3], ["-p", "school"])

    def test_a_new_session_without_a_stored_prompt_is_not_touched(self):
        _add_session(self.zero, "api_zero", None)
        store.set_session("default", "api_zero")
        self.assertEqual(freshness.check(), [])
        self.assertEqual(self.calls, [])

    def test_a_compressed_chat_refreshes_the_session_hermes_moved_it_to(self):
        _add_session(self.zero, "api_zero", OLD_PROMPT, end_reason="compression")
        _add_session(self.zero, "api_zero_2", OLD_PROMPT + "summary\n", parent="api_zero", started=2.0)
        store.set_session("default", "api_zero")
        self.assertEqual(freshness.check(), ["default"])
        self.assertIn("api_zero_2", self.calls[0])
        self.assertIsNone(self.stored(self.zero, "api_zero_2"))

    def test_a_failed_refresh_is_logged_and_tried_again(self):
        _add_session(self.zero, "api_zero", OLD_PROMPT)
        store.set_session("default", "api_zero")
        self.fail.add("api_zero")
        with self.assertLogs("taffy.freshness", "WARNING") as logs:
            self.assertEqual(freshness.check(), [])
        self.assertIn("could not refresh", logs.output[0])
        self.fail.clear()
        self.assertEqual(freshness.check(), ["default"])

    def test_refresh_everything_clears_current_chats_too(self):
        _add_session(self.zero, "api_zero", "x\n" + SOUL)
        store.set_session("default", "api_zero")
        self.assertEqual(freshness.check(), [])
        self.assertEqual(freshness.check(everything=True), ["default"])

    def test_the_lead_command_refreshes_now(self):
        _add_session(self.zero, "api_zero", OLD_PROMPT)
        store.set_session("default", "api_zero")
        with mock.patch.object(sys, "argv", ["taffy-hub", "refresh-chats"]), \
                mock.patch("builtins.print") as said:
            cli.main()
        self.assertIn("Refreshed 1 chat(s): default.", said.call_args_list[0].args[0])
        with mock.patch.object(sys, "argv", ["taffy-hub", "refresh-chats", "--all"]), \
                mock.patch.object(freshness, "check", return_value=[]) as check, mock.patch("builtins.print"):
            cli.main()
        check.assert_called_once_with(recheck=True, everything=True)

    def test_the_team_sync_loop_runs_the_check(self):
        from taffy_hub import team
        source = Path(team.__file__).read_text()
        loop = source[source.index("async def keep_souls_current"):]
        self.assertIn("freshness.check", loop)


class SignInCardMoves(unittest.TestCase):
    """Issue 2: asked again at 12:59, Zero said "your app still has the sign-in card from before", which sat at
    11:54, far up the chat."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hub_db = config.HUB_DB
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False

    def tearDown(self):
        config.HUB_DB = self.hub_db
        store._local.conn = None
        store._schema_ready = False

    def row(self, fid):
        return store.db().execute("SELECT * FROM feed WHERE id = ?", (fid,)).fetchone()

    def test_asking_again_moves_the_open_card_to_now_with_the_new_address(self):
        fid, said = signin.ask("default", "https://www.1800contacts.com/account/signin", "see the last order", "s1")
        self.assertIn("one short line", said)
        self.assertNotIn("one or two sentences", said)
        store.db().execute("UPDATE feed SET created_at = created_at - 3900, body = json_set(body, '$.pushed_at', "
                           "json_extract(body, '$.pushed_at') - 3900) WHERE id = ?", (fid,))
        store.db().commit()
        before = self.row(fid)["created_at"]
        again, said2 = signin.ask("default", "https://www.1800contacts.com/signin", "see the last order", "s1")
        self.assertEqual(again, fid)
        row = self.row(fid)
        self.assertGreaterEqual(row["created_at"], before + 3800)
        self.assertIsNotNone(row["changed_at"])
        card = json.loads(row["body"])
        self.assertEqual(card["state"], "open")
        self.assertEqual(card["url"], "https://www.1800contacts.com/signin")
        self.assertIn("now at the bottom of your chat", said2)
        self.assertIn("one short line", said2)
        self.assertNotIn("from before", said2)
        self.assertEqual(store.db().execute("SELECT COUNT(*) FROM feed WHERE kind='login'").fetchone()[0], 1)

    def test_asking_again_right_away_moves_it_without_a_second_notification(self):
        fid, _ = signin.ask("default", "https://example.com/login", "check", "s1")
        again, said = signin.ask("default", "https://example.com/login", "check", "s1")
        self.assertIsNone(again)
        self.assertIn("now at the bottom of your chat", said)
        self.assertNotIn("new notification", said)

    def test_an_open_card_stays_in_the_chat_however_much_came_after_it(self):
        fid, _ = signin.ask("default", "https://example.com/login", "check", "s1")
        done = store.add_feed("login", "default", None, "Sign in to old.com", json.dumps({"state": "signed_in"}))
        for i in range(45):
            store.add_feed("checkin" if i % 2 else "event", "default", None, f"item {i}", "not json" if i % 2 else "{}")
        ids = [i["id"] for i in store.activity_for("default")]
        self.assertEqual(ids[0], fid)
        self.assertNotIn(done, ids)
        self.assertEqual(len(ids), 41)

    def test_the_tool_tells_the_zip_to_keep_it_to_one_line(self):
        import plugin_loader
        text = (plugin_loader.PLUGIN_DIR / "asksignin.py").read_text()
        self.assertIn("one short line", text)
        self.assertNotIn("sentence or two", text)
        self.assertIn("moves to the bottom", text)


class SignInEndpoints(AppCase):
    """Issue 3, the parameters the card sends: a feed id for the card, the card's own Zip for Save Login."""

    def setUp(self):
        super().setUp()
        self.app.app.dependency_overrides[self.app.require_internal] = lambda: None
        self.pushed = []

        async def push(title, body, bot_id=None, **kw):
            self.pushed.append((title, body, bot_id))
            return True
        self.real_push = watcher.push
        watcher.push = push

    def tearDown(self):
        watcher.push = self.real_push
        super().tearDown()

    def test_asking_again_notifies_again_once_the_card_is_old(self):
        body = {"bot_id": "default", "args": {"url": "https://example.com/login", "why": "check the order"},
                "session_id": "s1"}
        r = self.client.post("/internal/chat/ask_sign_in", json=body)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(len(self.pushed), 1)
        r = self.client.post("/internal/chat/ask_sign_in", json=body)
        self.assertIn("bottom of your chat", r.json()["result"])
        self.assertEqual(len(self.pushed), 1)
        store.db().execute("UPDATE feed SET body = json_set(body, '$.pushed_at', 0)")
        store.db().commit()
        self.client.post("/internal/chat/ask_sign_in", json=body)
        self.assertEqual(len(self.pushed), 2)

    def test_save_login_answers_the_cards_own_zip_whatever_order_the_app_sends(self):
        fid, _ = signin.ask("school", "https://elearning.lakeview.test/login", "see grades", "s1")
        resolved = []
        with mock.patch.object(self.app.logins, "save", return_value=[{"bot": "school"}, {"bot": "default"}]), \
                mock.patch.object(signin, "resolve", side_effect=lambda b, f, o: resolved.append((b, f, o)) or {}):
            r = self.client.post("/api/logins", json={
                "bots": ["default", "school"], "label": "elearning.lakeview.test", "url": "https://elearning.lakeview.test/login",
                "identifier": "colin@example.com", "password": "not-a-real-password", "request_id": fid})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["warning"])
        self.assertEqual(resolved, [("school", fid, "saved")])

    def test_errors_the_app_showed_are_logged_without_query_strings(self):
        with self.assertLogs("taffy.hub", "WARNING") as logs:
            r = self.client.post("/api/app-errors", json={
                "screen": "take_over", "bot_id": "default",
                "message": "Zero's computer is taking too long to answer. Try again in a moment.",
                "detail": "URLError -1001 /screen/screen.html?ticket=SECRET"})
        self.assertEqual(r.status_code, 200)
        line = logs.output[0]
        self.assertIn("app error on take_over for default", line)
        self.assertIn("taking too long", line)
        self.assertNotIn("SECRET", line)


class ScreenCheck(unittest.TestCase):
    """Issue 3, the cause: every status check connected to the screen's socket and closed it at once. Xvnc counts
    that as a failed login, and after five it answered every viewer with "Too many security failures"."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sock = self.tmp / "rfb.sock"
        self.sock.touch()
        self.table = self.tmp / "unix"
        self.real_table = screen.UNIX_TABLE
        screen.UNIX_TABLE = self.table

    def tearDown(self):
        screen.UNIX_TABLE = self.real_table

    def write(self, flags, path):
        self.table.write_text("Num       RefCount Protocol Flags    Type St Inode Path\n"
                              f"0000000000000000: 00000002 00000000 {flags} 0001 01 156676897 {path}\n"
                              "0000000000000000: 00000003 00000000 00000000 0001 03 156676898\n")

    def test_a_listening_socket_is_running_without_any_connection(self):
        self.write("00010000", self.sock)
        with mock.patch.object(socket, "socket", side_effect=AssertionError("connected to the screen")):
            self.assertTrue(screen._socket_alive(self.sock))

    def test_a_socket_file_nobody_listens_on_is_asleep(self):
        self.write("00000000", self.sock)
        self.assertFalse(screen._socket_alive(self.sock))
        self.write("00010000", self.tmp / "other.sock")
        self.assertFalse(screen._socket_alive(self.sock))
        self.sock.unlink()
        self.assertFalse(screen._socket_alive(self.sock))

    def test_a_refusal_is_told_apart_from_a_normal_start(self):
        self.assertTrue(screen.refused(b"RFB 003.003\n\0\0\0\0\0\0\0\x1aToo many security failures"))
        self.assertFalse(screen.refused(b"RFB 003.008\n"))
        self.assertFalse(screen.refused(b"RFB 003.008\n\x01\x01"))



def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()
