"""The check-in watcher reports each cron output exactly once (regression: Oct 7, 7 PM, Money's spending
update went out every 10 seconds because the cursor was rounded and the file time was not).

Run on alien: ~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import apns, board, config, store, watcher  # noqa: E402


class CronOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db, self.dirs, self.push = config.HUB_DB, watcher._cron_dirs, watcher.push
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        self.out = self.tmp / "cron" / "output"
        watcher._cron_dirs = lambda: [("money", self.out)]
        self.pushed = []

        async def push(title, body, bot_id=None, bots=None, urgent=False, kind=None, extra=None):
            self.pushed.append(title)
            return True
        watcher.push = push

    def tearDown(self):
        config.HUB_DB, watcher._cron_dirs, watcher.push = self.db, self.dirs, self.push
        store._local.conn = None
        store._schema_ready = False

    def write(self, name: str, mtime: float):
        f = self.out / "job1" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# Cron Job: Daily spending update\n\n## Response\n\nDining is over the cap.\n")
        os.utime(f, (mtime, mtime))

    def reports(self):
        return [r["title"] for r in store.db().execute("SELECT title FROM feed WHERE kind='checkin' ORDER BY id")]

    def test_each_report_once(self):
        store.set_cursor("cron_mtime_ms", 1_000_000)
        self.write("a.md", 1791414062.123456)
        for _ in range(5):
            asyncio.run(watcher.scan_cron())
        self.assertEqual(self.reports(), ["Daily spending update"])
        self.write("b.md", 1791414062.123456)
        for _ in range(3):
            asyncio.run(watcher.scan_cron())
        self.assertEqual(len(self.reports()), 2)
        self.write("c.md", 1791414999.5)
        for _ in range(3):
            asyncio.run(watcher.scan_cron())
        self.assertEqual(len(self.reports()), 3)
        self.assertEqual(self.pushed, [])

    def test_error_part_way_never_resends(self):
        store.set_cursor("cron_mtime_ms", 1_000_000)
        self.write("a.md", 1791414000.5)
        self.write("b.md", 1791414001.5)
        real, calls = store.add_feed, []

        def flaky(kind, bot, task, title, body):
            calls.append(title)
            if len(calls) == 2:
                raise RuntimeError("database is locked")
            return real(kind, bot, task, title, body)
        store.add_feed = flaky
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(watcher.scan_cron())
        finally:
            store.add_feed = real
        asyncio.run(watcher.scan_cron())
        self.assertEqual(len(store.db().execute("SELECT id FROM feed WHERE kind='checkin'").fetchall()), 2)


class Pushes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db, self.ready, self.night, self.sync = config.HUB_DB, apns.ready, watcher._night, watcher._push_sync
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        apns.ready = lambda: True
        self.sent = []
        watcher._push_sync = lambda title, body, bot_id=None, **kw: self.sent.append(kw.get("topic") or title) or True
        watcher._night = lambda: False

    def tearDown(self):
        config.HUB_DB, apns.ready, watcher._night, watcher._push_sync = self.db, self.ready, self.night, self.sync
        store._local.conn = None
        store._schema_ready = False

    def test_snoozing_taffy_does_not_silence_others_or_health(self):
        store.set_setting("muted_bots", ["default"])
        asyncio.run(watcher.push("Team update", "x", bots=["default", "money"]))
        asyncio.run(watcher.push("Money needs you", "x", "money"))
        asyncio.run(watcher.push("Taffy", "Your bots stopped answering.", urgent=True))
        asyncio.run(watcher.push("Taffy finished", "x", "default"))
        self.assertEqual(self.sent, ["Team update", "Money needs you", "Taffy"])

    def test_night_holds_routine_news_for_a_morning_digest(self):
        watcher._night = lambda: True
        asyncio.run(watcher.push("Career finished", "x", "career"))
        asyncio.run(watcher.push("Taffy", "Your bots stopped answering.", urgent=True))
        self.assertEqual(self.sent, ["Taffy"])
        watcher._night = lambda: False
        asyncio.run(watcher.send_morning_digest())
        self.assertEqual(self.sent, ["Taffy", "Career finished"])
        self.assertEqual(store.get_setting("held_pushes"), [])

    def test_several_held_items_are_one_sentence_from_the_lead(self):
        got = []
        watcher._push_sync = lambda title, body, bot_id=None, **kw: got.append((title, body, bot_id)) or True
        watcher._night = lambda: True
        asyncio.run(watcher.push("Career finished", "x", "career"))
        asyncio.run(watcher.push("Builder finished", "y", "builder"))
        watcher._night = lambda: False
        asyncio.run(watcher.send_morning_digest())
        [(title, body, bot)] = got
        self.assertEqual((bot, title), watcher.notify.sender(None))
        self.assertTrue(body.startswith("While you slept, 2 updates came in from "), body)
        self.assertNotIn("\n", body)


if __name__ == "__main__":
    unittest.main()


class Jobs(unittest.TestCase):
    def setUp(self):
        from taffy_hub import notices as _notices
        real_busy = _notices.busy_block
        _notices.busy_block = lambda t=None: None
        self.addCleanup(setattr, _notices, "busy_block", real_busy)
        self.tmp = Path(tempfile.mkdtemp())
        self.db, self.dirs = config.HUB_DB, watcher._cron_dirs
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        (self.tmp / "money" / "cron" / "output").mkdir(parents=True)
        watcher._cron_dirs = lambda: [("money", self.tmp / "money" / "cron" / "output")]

    def tearDown(self):
        config.HUB_DB, watcher._cron_dirs = self.db, self.dirs
        store._local.conn = None
        store._schema_ready = False

    def jobs(self, *items):
        (self.tmp / "money" / "cron" / "jobs.json").write_text(json.dumps({"jobs": list(items)}))

    def lines(self):
        return [r["title"] for r in store.db().execute("SELECT title FROM feed ORDER BY id")]

    def test_new_changed_and_removed_jobs_show_in_chat(self):
        a = {"id": "a1", "name": "Daily spending update", "schedule": {"display": "0 19 * * *"}, "enabled": True}
        self.jobs(a)
        watcher.scan_jobs()
        self.assertEqual(self.lines(), [])
        b = {"id": "b2", "name": "Bill reminder", "schedule": {"display": "0 9 1 * *"}, "enabled": True}
        self.jobs(a, b)
        watcher.scan_jobs()
        watcher.scan_jobs()
        self.assertEqual(self.lines(), ["Added a scheduled job"])
        self.jobs({**a, "enabled": False}, b)
        watcher.scan_jobs()
        self.jobs(b)
        watcher.scan_jobs()
        self.assertEqual(self.lines(), ["Added a scheduled job", "Paused a scheduled job", "Removed a scheduled job"])

    def test_script_job_output_is_reported_and_its_failures_are_not(self):
        head = "# Cron Job: Theo Avery reply watch\n\n**Job ID:** 48d1\n**Run Time:** 2026-10-08 09:00:00\n**Mode:** no_agent (script)\n"
        d = self.tmp / "money" / "cron" / "output" / "48d1"
        d.mkdir(parents=True)
        (d / "a.md").write_text(head + "\n---\n\nKen Avery replied about the Brightpath handoff.\n")
        (d / "b.md").write_text(head + "**Status:** silent (empty output)\n")
        (d / "c.md").write_text(head + "**Status:** script failed\n\ncould not read the personal inbox\n")
        self.assertEqual(watcher._cron_report(d / "a.md"),
                         ("Theo Avery reply watch", "Theo Avery replied about the Brightpath handoff."))
        self.assertIsNone(watcher._cron_report(d / "b.md"))
        self.assertIsNone(watcher._cron_report(d / "c.md"))

    def test_a_job_that_keeps_failing_is_told_once_and_its_recovery_too(self):
        a = {"id": "a1", "name": "Theo Avery reply watch", "schedule": {"display": "*/30 7-23 * * *"}, "enabled": True,
             "failure_streak": 0}
        self.jobs(a)
        self.assertEqual(watcher.scan_jobs(), [])
        self.jobs({**a, "failure_streak": 1})
        self.assertEqual(watcher.scan_jobs(), [])
        self.jobs({**a, "failure_streak": 2})
        self.assertEqual([t for _, t, _ in watcher.scan_jobs()], ["A scheduled job keeps failing"])
        self.jobs({**a, "failure_streak": 3})
        self.assertEqual(watcher.scan_jobs(), [])
        self.jobs({**a, "failure_streak": 0})
        self.assertEqual([t for _, t, _ in watcher.scan_jobs()], ["A scheduled job works again"])
        self.assertEqual(self.lines(), ["A scheduled job keeps failing", "A scheduled job works again"])

    def test_a_task_running_over_an_hour_is_told_once(self):
        pushed = []

        async def fake_push(title, body, bot_id=None, **kw):
            pushed.append(body)
            return True
        real_push, real_long = watcher.push, board.long_running
        watcher.push = fake_push
        if "school" not in config.BOTS_BY_ID:
            school = next(b for b in (config.BUILTIN + config.OWNER_ZIPS) if b.id == "school")
            config.BOTS_BY_ID["school"] = school
            self.addCleanup(config.BOTS_BY_ID.pop, "school", None)
        board.long_running = lambda sec: [{"id": "t_1", "title": "Check Lakeview grades", "assignee": "school"}]
        try:
            asyncio.run(watcher.scan_long_tasks())
            asyncio.run(watcher.scan_long_tasks())
            board.long_running = lambda sec: []
            asyncio.run(watcher.scan_long_tasks())
        finally:
            watcher.push, board.long_running = real_push, real_long
        self.assertEqual(len(pushed), 1)
        self.assertIn("I've been working on \"Check Lakeview grades\"", pushed[0])
        self.assertEqual(store.get_setting("long_tasks_told", None), [])


class ClaudeLimit(unittest.TestCase):
    def test_limit_tells_once_and_clears(self):
        from taffy_hub import health
        calls = []

        async def fake_set(name, ok):
            calls.append((name, ok))
            health.status[name] = ok
        real = health._set
        health._set = fake_set
        health.status["claude"] = True

        async def go():
            health.turn_finished("Error: usage limit reached, resets at 10 PM")
            await asyncio.sleep(0)
            health.turn_finished("429 rate limit")
            await asyncio.sleep(0)
            health.turn_finished(None)
            await asyncio.sleep(0)
            health.turn_finished("some other error")
            await asyncio.sleep(0)
            health.turn_finished(None, backup=True)
            await asyncio.sleep(0)
            health.turn_finished(None, backup=True)
            await asyncio.sleep(0)
            health.turn_finished(None)
            await asyncio.sleep(0)
        try:
            asyncio.run(go())
        finally:
            health._set = real
            health.status["claude"] = True
        self.assertEqual(calls, [("claude", False), ("claude", True), ("claude", False), ("claude", True)])


class QuietRestart(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hub_dir = config.HUB_DIR
        config.HUB_DIR = self.tmp

    def tearDown(self):
        config.HUB_DIR = self.hub_dir

    def test_restart_waits_for_a_call_and_refuses_without_force(self):
        import time as _time
        from unittest import mock
        from taffy_hub import cli
        (self.tmp / "busy.json").write_text(json.dumps({"calls": 1, "queued": 0, "mac": False, "at": _time.time()}))
        with mock.patch("subprocess.run") as run:
            self.assertEqual(cli.restart(0, force=False), 1)
            run.assert_not_called()
            self.assertEqual(cli.restart(0, force=True), 0)
            run.assert_called_once()
        (self.tmp / "busy.json").write_text(json.dumps({"calls": 0, "queued": 0, "mac": False, "at": _time.time()}))
        with mock.patch("subprocess.run") as run:
            self.assertEqual(cli.restart(0, force=False), 0)
            run.assert_called_once()
        (self.tmp / "busy.json").write_text(json.dumps({"calls": 1, "queued": 0, "mac": False, "at": 0}))
        with mock.patch("subprocess.run") as run:
            self.assertEqual(cli.restart(0, force=False), 0)

    def test_busy_counts_calls_queued_messages_and_a_mac_task(self):
        from taffy_hub import mac, voice
        self.assertEqual(watcher.busy_now(), {"calls": 0, "queued": 0, "mac": False})
        voice.calls["x"] = object()
        try:
            self.assertEqual(watcher.busy_now()["calls"], 1)
        finally:
            voice.calls.pop("x")


def _has_hermes_desktop() -> bool:
    """Hermes's tools package with its desktop module. The repo's own tools/ folder is not it."""
    try:
        return importlib.util.find_spec("tools.bot_desktop") is not None
    except ImportError:
        return False


class IdleDesktops(unittest.TestCase):
    @unittest.skipUnless(_has_hermes_desktop(), "needs Hermes's tools package (alien)")
    def test_who_keeps_a_desktop(self):
        from unittest import mock
        from taffy_hub import screen
        home = Path(tempfile.mkdtemp())
        old = time.time() - 5 * 3600
        for b in config.BOTS_BY_ID:
            d = (home if b == "default" else home / "profiles" / b) / "bot-desktop"
            d.mkdir(parents=True)
            (d / "activity").touch()
            os.utime(d / "activity", (old, old))
        fresh = (home / "profiles" / "money" / "bot-desktop" / "activity")
        os.utime(fresh, None)
        real_home = config.HERMES_HOME
        config.HERMES_HOME = home
        stopped = []
        try:
            with mock.patch.object(screen, "_socket_alive", return_value=True), \
                 mock.patch.object(screen, "_available_mb", return_value=8000), \
                 mock.patch.object(screen, "_cli", side_effect=lambda b, *a: (stopped.append(b), (True, ""))[1]):
                screen._viewers["career"] = 1
                screen.sweep_idle_desktops(busy={"builder"}, waiting={"school"},
                                           still_busy=lambda b: b == "clients")
        finally:
            config.HERMES_HOME = real_home
            screen._viewers.pop("career", None)
        self.assertEqual(sorted(stopped), ["default"])


class BackupHealth(unittest.TestCase):
    def test_backup_judged_by_last_run(self):
        from taffy_hub import health
        tmp = Path(tempfile.mkdtemp())
        real = health.BACKUP_FILE
        health.BACKUP_FILE = tmp / "backup.json"
        try:
            self.assertTrue(health._backup_ok())
            health.BACKUP_FILE.write_text(json.dumps({"ok": True, "at": time.time()}))
            self.assertTrue(health._backup_ok())
            health.BACKUP_FILE.write_text(json.dumps({"ok": False, "at": time.time(), "error": "x"}))
            self.assertFalse(health._backup_ok())
            health.BACKUP_FILE.write_text(json.dumps({"ok": True, "at": time.time() - 40 * 3600}))
            self.assertFalse(health._backup_ok())
            real_mac = health.MAC_COPY_FILE
            health.MAC_COPY_FILE = tmp / "backup-mac.json"
            try:
                self.assertTrue(health._mac_copy_ok())
                health.MAC_COPY_FILE.write_text(json.dumps({"at": time.time() - 3600}))
                self.assertTrue(health._mac_copy_ok())
                health.MAC_COPY_FILE.write_text(json.dumps({"at": time.time() - 4 * 86400}))
                self.assertFalse(health._mac_copy_ok())
            finally:
                health.MAC_COPY_FILE = real_mac
        finally:
            health.BACKUP_FILE = real
