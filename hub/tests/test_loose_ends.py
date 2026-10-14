"""Loose ends after the Oct 9 night review (docs/qa/2026-10-09-loose-ends.md):

1. SOUL.md flapping: tests that entered the app's lifespan ran the hub's loops against the live home, so every Zip's
   SOUL.md was rewritten by the test and put back by the hub. Tests never run the loops now, and a sync with no real
   change writes nothing.
2. One owner per step lets a scheduled job's new run make its task while an earlier run's task is still open.
3. A home.json without home_id is Colin's home only when it is his own file at its known path, in all three readers.
4. A brace in the person's name no longer breaks the voice prompts or the plugin's messages.

Run: python3 -m unittest discover -s hub/tests
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, home, ondevice, store, team, voice, who  # noqa: E402
import home_fixture  # noqa: E402
from test_gate import GateCase, gate, tt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent


def scratch_store(test: unittest.TestCase) -> None:
    """A hub database of the test's own for this thread and any other (never the live one)."""
    saved = (config.HUB_DB, getattr(store._local, "conn", None), store._schema_ready)
    config.HUB_DB = Path(tempfile.mkdtemp()) / "hub.db"
    store._local.conn = None
    store._schema_ready = False

    def restore():
        config.HUB_DB, store._local.conn, store._schema_ready = saved
    test.addCleanup(restore)


def _load(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    mod = module_from_spec(spec_from_loader(name, loader))
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


HAVE_SCRIPTS = (REPO / "alien" / "bin" / "zero-home-lib.py").exists()
lib = _load("zero_home_lib_loose_ends", REPO / "alien" / "bin" / "zero-home-lib.py") if HAVE_SCRIPTS else None
check = _load("zero_home_check_loose_ends", REPO / "alien" / "bin" / "zero-home-check") if HAVE_SCRIPTS else None


# MARK: 1. SOUL.md is rewritten only on a real change, and never by a test

class SoulSync(unittest.TestCase):
    def setUp(self):
        scratch_store(self)
        self.home = Path(tempfile.mkdtemp()) / "hermes"
        for zip_id in ("money", "career"):
            (self.home / "profiles" / zip_id).mkdir(parents=True)
            (self.home / "profiles" / zip_id / "SOUL.md").write_text(
                f"# {zip_id.title()}\n\nYou are one of Zero's specialists (a Zip) on Colin's team. Zero talks with "
                "Colin and hands you work. Work with him the same way Zero would.\n")
        (self.home / "SOUL.md").write_text("# Sunny\n\nBe kind.\n")
        saved = (config.HERMES_HOME, list(config.BOTS), dict(config.BOTS_BY_ID), config.reload_bots)
        self.addCleanup(self._restore, saved)
        config.HERMES_HOME = self.home
        config.reload_bots = lambda: False
        bots = [config.Bot("default", "Sunny", "Lead", "#F2711C", "sparkles"),
                config.Bot("money", "Money", "Money", "#EAB308", "dollarsign.circle"),
                config.Bot("career", "Career", "Jobs", "#EC4899", "briefcase")]
        config.BOTS[:] = bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update({b.id: b for b in bots})

    @staticmethod
    def _restore(saved):
        config.HERMES_HOME, bots, by_id, config.reload_bots = saved
        config.BOTS[:] = bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id)

    def souls(self):
        return [self.home / "SOUL.md", self.home / "profiles" / "money" / "SOUL.md",
                self.home / "profiles" / "career" / "SOUL.md"]

    def test_two_syncs_with_no_change_write_nothing(self):
        self.assertEqual(team.sync_souls(), ["default", "money", "career"])
        money = (self.home / "profiles" / "money" / "SOUL.md").read_text()
        self.assertIn("You are one of Sunny's specialists", money)
        self.assertEqual(money.count(team.SOUL_START), 1)
        before = {p: (p.read_text(), p.stat().st_mtime_ns) for p in self.souls()}
        real_write = Path.write_text
        writes = []

        def counting(path, *a, **k):
            writes.append(path)
            return real_write(path, *a, **k)
        with mock.patch.object(Path, "write_text", counting):
            self.assertEqual(team.sync_souls(), [])
            self.assertEqual(team.sync_souls(), [])
        self.assertEqual(writes, [])
        self.assertEqual({p: (p.read_text(), p.stat().st_mtime_ns) for p in self.souls()}, before)

    def test_a_real_change_writes_once_then_settles(self):
        team.sync_souls()
        config.BOTS[1] = config.BOTS_BY_ID["money"] = config.Bot("money", "Budget", "Money", "#EAB308", "dollarsign.circle")
        self.assertEqual(team.sync_souls(), ["default", "money", "career"])
        self.assertIn("| Budget |", (self.home / "SOUL.md").read_text())
        self.assertEqual(team.sync_souls(), [])


class NoLoopsInTests(unittest.TestCase):
    """The hub's background loops worked on the live home whenever a test entered the app's lifespan: the SOUL.md
    sync, the board watcher's busy file, the health and account checks (Oct 9, about 65 rewrites of every SOUL.md)."""

    def test_entering_the_app_runs_no_loop(self):
        from test_onboarding import _load_app
        app = _load_app()
        if app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        self.assertFalse(app.BACKGROUND)
        scratch_store(self)
        from fastapi.testclient import TestClient
        started = []

        async def loop(*_a, **_k):
            started.append(1)
        with mock.patch.object(team, "keep_souls_current", loop), mock.patch.object(app.watcher, "run", loop), \
                mock.patch.object(app.health, "run", loop), mock.patch.object(app.accounts, "run", loop), \
                mock.patch.object(app.calllog, "run", loop):
            with TestClient(app.app):
                pass
        self.assertEqual(started, [])

    def test_every_test_client_comes_from_load_app(self):
        """A test that built the app another way would run the loops again."""
        for f in sorted(TESTS.glob("test_*.py")):
            text = f.read_text()
            if "TestClient(" in text and f.name not in ("test_loose_ends.py", "test_billing.py"):
                self.assertIn("_load_app", text, f"{f.name} makes a TestClient without _load_app")


# MARK: 2. One owner per step, and a scheduled job's new run

class RoutineRuns(GateCase):
    RUN = "cron_9f0cc7e90ec6_20261010_073000"
    YESTERDAY = "cron_9f0cc7e90ec6_20261009_073000"
    TITLE = "Morning brief for Colin, with the weather and calendar"

    def setUp(self):
        super().setUp()
        self.db = gate._taffy_home() / "kanban.db"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE tasks (id TEXT, title TEXT, assignee TEXT, status TEXT, created_by TEXT, "
                  "session_id TEXT, created_at INTEGER)")
        c.commit()
        c.close()
        self.start = time.mktime(time.strptime("20261010_073000", "%Y%m%d_%H%M%S"))
        self.env = {}
        gate._session_env = lambda name: self.env.get(name, "")

    def task(self, tid, status, session, made_at):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", (tid, self.TITLE, "default", status, "default", session,
                                                               made_at))
        c.commit()
        c.close()

    def create(self, session):
        return self.call("kanban_create", {"title": self.TITLE + ".", "assignee": "default"}, session=session)

    def test_a_new_run_makes_its_task_while_yesterdays_waits_in_review(self):
        self.task("t_old", "review", self.YESTERDAY, self.start - 86400)
        self.assertIsNone(self.create(self.RUN))

    def test_the_same_run_cannot_make_it_twice(self):
        self.task("t_new", "todo", self.RUN, self.start + 40)
        blocked = self.create(self.RUN)
        self.assertIn("task t_new with this title is already open", blocked["message"])

    def test_a_task_without_a_run_stamp_counts_by_its_time(self):
        self.task("t_before", "review", None, self.start - 3600)
        self.assertIsNone(self.create(self.RUN))
        self.task("t_after", "todo", None, self.start + 60)
        self.assertIn("t_after", self.create(self.RUN)["message"])

    def test_the_run_is_known_from_hermes_session_env_too(self):
        self.task("t_old", "review", self.YESTERDAY, self.start - 86400)
        self.env = {"HERMES_CRON_SESSION": "1", "HERMES_SESSION_ID": self.RUN}
        self.assertIsNone(self.create("some-other-id"))
        self.task("t_new", "todo", self.RUN, self.start + 40)
        self.assertIn("t_new", self.create("some-other-id")["message"])

    def test_a_chat_still_gets_one_owner_per_step(self):
        self.task("t_old", "review", self.YESTERDAY, self.start - 86400)
        self.assertIn("t_old", self.create("api_1791377683_cccd3e21")["message"])

    def test_finished_runs_never_count(self):
        self.task("t_done", "done", self.RUN, self.start + 40)
        self.assertIsNone(self.create(self.RUN))


# MARK: 3. A home.json without home_id

NO_ID = {"version": 1, "person": {"name": "Dana Park", "first_name": "Dana"}}


@unittest.skipUnless(HAVE_SCRIPTS, "needs the repo's alien/bin home scripts")
class NoHomeId(unittest.TestCase):
    """No file is still Colin's home in every reader. A file without home_id is his only at his own known path."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.file = self.dir / "home.json"
        self.file.write_text(json.dumps(NO_ID))
        self.elsewhere = self.dir / "jdoe" / "taffy" / "home.json"
        saved_env, saved_file = os.environ.get("ZERO_HOME"), home.FILE
        self.addCleanup(self._restore, saved_env, saved_file)
        home.FILE = None
        os.environ["ZERO_HOME"] = str(self.file)
        home.reset_cache()

    @staticmethod
    def _restore(env, file):
        home.FILE = file
        if env is None:
            os.environ.pop("ZERO_HOME", None)
        else:
            os.environ["ZERO_HOME"] = env
        home.reset_cache()

    def owner_is(self, path: Path):
        """Pretends `path` is the owner's own ~/taffy/home.json, in all three readers."""
        stack = [mock.patch.object(home, "owner_file", lambda: path),
                 mock.patch.object(tt.home, "owner_file", lambda: path),
                 mock.patch.object(lib, "owner_settings_file", lambda: path)]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)
        home.reset_cache()

    def test_a_file_elsewhere_without_home_id_is_not_colins(self):
        self.owner_is(self.elsewhere)
        self.assertEqual(home.get("home_id"), home.NO_ID)
        self.assertEqual(home.person_name(), "Dana Park")
        self.assertIsNone(home.trello())
        self.assertIsNone(home.tally())
        self.assertEqual(home.mail_accounts(), [])
        self.assertFalse(home.is_owner_home())
        self.assertFalse(tt.home.is_colin())
        self.assertEqual(tt.home.home_id(), "unnamed")
        self.assertIsNone(tt.home.trello())
        self.assertEqual(tt.home.mail_accounts(), [])
        self.assertEqual(tt.home.connectors(), "none")
        s = lib.load_home(self.file)
        self.assertEqual(s["home_id"], "unnamed")
        self.assertEqual(s["plan"]["connectors"], "none")
        self.assertFalse(s["judge"]["enabled"])

    def test_an_empty_home_id_is_the_same_as_none(self):
        self.owner_is(self.elsewhere)
        self.file.write_text(json.dumps({**NO_ID, "home_id": ""}))
        home.reset_cache()
        self.assertEqual(home.get("home_id"), "unnamed")
        self.assertFalse(tt.home.is_colin())
        self.assertEqual(lib.load_home(self.file)["home_id"], "unnamed")

    def test_colins_own_file_without_home_id_is_his(self):
        self.owner_is(self.file)
        self.file.write_text(json.dumps({"version": 1}))
        home.reset_cache()
        self.assertEqual(home.get("home_id"), "colin")
        self.assertEqual(home.person_name(), "Colin O'Brien")
        self.assertEqual(home.trello()["board_id"], "AbCd1234")
        self.assertTrue(tt.home.is_colin())
        self.assertEqual(tt.home.trello()["board_id"], "AbCd1234")
        s = lib.load_home(self.file)
        self.assertEqual((s["home_id"], s["plan"]["connectors"]), ("colin", "claude"))

    def test_no_file_is_still_colins_home(self):
        self.owner_is(self.elsewhere)
        self.file.unlink()
        home.reset_cache()
        self.assertEqual(home.get("home_id"), "colin")
        self.assertTrue(home.is_owner_home())
        self.assertTrue(tt.home.is_colin())
        self.assertEqual(lib.load_home(None)["home_id"], "colin")
        self.assertEqual(lib.load_home(self.file)["home_id"], "colin")

    def test_a_pinned_file_without_home_id_is_not_colins(self):
        with tt.home.pinned(dict(NO_ID)):
            self.assertFalse(tt.home.is_colin())
            self.assertNotIn("Colin", tt.home.personalize("Colin asked."))

    def test_the_readers_agree_on_every_shape(self):
        self.owner_is(self.elsewhere)
        shapes = [NO_ID, {**NO_ID, "home_id": ""}, {**NO_ID, "home_id": "colin"}, {**NO_ID, "home_id": "dana"}]
        for shape in shapes:
            self.file.write_text(json.dumps(shape))
            os.utime(self.file, None)
            home.reset_cache()
            ids = (home.get("home_id"), tt.home.home_id(), lib.load_home(self.file)["home_id"])
            self.assertEqual(len(set(ids)), 1, (shape, ids))

    def test_zero_home_check_asserts_home_id(self):
        r = check.home_id_check("dana", self.file)
        self.assertEqual((r.status, r.detail), ("fail", f"{self.file} has no home_id"))
        self.file.write_text(json.dumps({**NO_ID, "home_id": "riley"}))
        self.assertEqual(check.home_id_check("dana", self.file).status, "fail")
        self.file.write_text(json.dumps({**NO_ID, "home_id": "dana"}))
        self.assertEqual(check.home_id_check("dana", self.file).status, "pass")
        self.assertEqual(check.home_id_check("dana", self.dir / "missing.json").status, "fail")


# MARK: 4. Braces in a person's name

class BracesInNames(unittest.TestCase):
    NAME = "{Al} Bo}"

    def test_the_voice_prompts_keep_the_name(self):
        with home_fixture.use({"home_id": "al", "person": {"name": self.NAME, "first_name": "{Al}"}}), \
                mock.patch.object(who, "first", return_value="{Al}"):
            p = voice.voice_prompt("default", "Brief with {braces} of its own.")
            self.assertIn("{Al}", p)
            self.assertIn("Brief with {braces} of its own.", p)
            self.assertIn("{Al}", voice.speaking_rules("Zero"))
            self.assertIn("{Al}", ondevice.prompt("default", "Brief."))
            self.assertIn("{Al}", voice.relay_instructions())
            self.assertNotIn("{{Al}}", p)

    def test_the_plugin_says_the_name_as_written(self):
        with tt.home.pinned({"home_id": "al", "person": {"name": self.NAME, "first_name": "{Al}",
                                                          "pronoun_free": True}}):
            self.assertEqual(tt.home.say("Colin asked for {site}; tell him.", site="x.com"),
                             "{Al} asked for x.com; tell {Al}.")
            self.assertEqual(tt.home.say("Colin O'Brien's {what}", what="list"), "{Al} Bo}'s list")
            self.assertEqual(tt.home.personalize("Colin O'Brien"), self.NAME)

    def test_a_gate_message_with_the_name_still_blocks(self):
        with tt.home.pinned({"home_id": "al", "person": {"name": self.NAME, "first_name": "{Al}"}}):
            msg = tt.home.say("Your browser is busy with \"{busy}\". Tell Colin which you did.", busy="a {task}")
            self.assertEqual(msg, "Your browser is busy with \"a {task}\". Tell {Al} which you did.")


if __name__ == "__main__":
    unittest.main()


class Oct10MacAndSteer(unittest.TestCase):
    """Colin's Clients call, Oct 10: the call's update showed in the chat inside Hermes's out-of-band wrapper, and
    "ls /Applications/Pages*.app" was refused as a wildcard near a private folder."""

    def test_a_steered_call_update_shows_as_his_words_with_the_call(self):
        from taffy_hub import history
        wrapped = ("[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered once at this position; "
                   "not tool output and not a new delivery when replayed from conversation history]\n"
                   "Update from Colin on the call: Colin says he just hit Allow on his Mac.\n"
                   "[/OUT-OF-BAND USER MESSAGE]")
        out = history.classify([{"role": "user", "text": wrapped}, {"role": "assistant", "text": "Thanks, on it."}])
        self.assertEqual(out[0]["class"], "call_turn")
        self.assertEqual(out[0]["display"], "Colin says he just hit Allow on his Mac.")
        self.assertNotIn("OUT-OF-BAND", out[0]["display"])
        self.assertEqual(out[1]["class"], "call_reply")

    def test_a_steered_typed_message_shows_only_his_words(self):
        from taffy_hub import history
        wrapped = "[OUT-OF-BAND USER MESSAGE — from the user]\nAlso check the blog.\n[/OUT-OF-BAND USER MESSAGE]"
        out = history.classify([{"role": "user", "text": wrapped}])
        self.assertEqual((out[0]["class"], out[0]["display"]), ("colin", "Also check the blog."))

    def test_listing_applications_with_a_wildcard_is_fine_but_application_support_is_not(self):
        from taffy_hub import mac
        mac.check_command("ls -d /Applications/Pages*.app /Applications/*/Pages*.app")
        with self.assertRaises(mac.MacError):
            mac.check_command("ls ~/Library/Applic*")
