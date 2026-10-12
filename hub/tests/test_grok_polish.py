"""Grok Bot polish (docs/qa/2026-10-09-grok-polish.md): the Computer card's picture and state, and the event
lines in a Zip's chat (created routines, updated memory) and the live "Pinging" chip.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import base64
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, events, hermes, screen, store, team, watcher  # noqa: E402
from test_onboarding import _fresh_store, _load_app, _real_reload  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
DATA_URL = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()


class FakeLease:
    """Stands in for Hermes's screen lease: who holds it, which app, and how many times it changed."""
    HUMAN, AGENT = "human", "agent"

    def __init__(self):
        self.holder, self.viewer_id, self.epoch = self.AGENT, None, 0

    def get(self, key=None):
        return types.SimpleNamespace(holder=self.holder, viewer_id=self.viewer_id, epoch=self.epoch, reason="", since=0)


def fake_hermes(lease: FakeLease, grab):
    """The Hermes modules screen.snapshot imports, with `grab` standing in for the screen picture."""
    mods = {"tools": types.ModuleType("tools"), "tools.bot_desktop": types.ModuleType("tools.bot_desktop"),
            "tools.bot_desktop.lease": lease, "tools.bot_desktop.thumbnail": types.ModuleType("thumb"),
            "hermes_constants": types.ModuleType("hermes_constants")}
    mods["tools.bot_desktop"].lease = lease
    mods["tools.bot_desktop.thumbnail"].thumbnail_data_url = grab
    mods["hermes_constants"].set_hermes_home_override = lambda p: "token"
    mods["hermes_constants"].reset_hermes_home_override = lambda t: None
    return mods


class Store(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB)
        _fresh_store(Path(tempfile.mkdtemp()))

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False
        team._ready = False

    def feed(self):
        return [(r["bot_id"], r["title"], json.loads(r["body"])) for r in
                store.db().execute("SELECT * FROM feed WHERE kind='event' ORDER BY id")]


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.lease = FakeLease()
        self.sock = Path(tempfile.mkdtemp())
        self.alive = True
        self.real = (screen.profile_home, screen._socket_alive)
        screen.profile_home = lambda bot: self.sock
        screen._socket_alive = lambda path: self.alive

    def tearDown(self):
        screen.profile_home, screen._socket_alive = self.real

    def take(self, grab=None):
        grab = grab or (lambda size, quality: DATA_URL)
        with mock.patch.dict(sys.modules, fake_hermes(self.lease, grab)):
            return screen.snapshot("money")

    def test_a_running_screen_gives_its_picture(self):
        self.assertEqual(self.take(), (JPEG, ""))

    def test_a_sleeping_screen_gives_nothing(self):
        self.alive = False
        self.assertEqual(self.take(), (None, "asleep"))
        self.alive = True
        self.assertEqual(self.take(lambda size, quality: None), (None, "asleep"))

    def test_it_follows_the_screen_that_the_viewer_shows(self):
        sizes = []
        self.take(lambda size, quality: sizes.append(size) or DATA_URL)
        self.assertEqual(sizes, [screen.SNAPSHOT_MAX])

    def test_colin_in_control_still_sees_it_but_someone_else_does_not(self):
        self.lease.holder, self.lease.viewer_id = "human", screen.VIEWER_ID
        self.assertEqual(self.take()[1], "")
        self.lease.viewer_id = "hermes-desktop"
        self.assertEqual(self.take(), (None, "private"))

    def test_a_takeover_during_the_grab_drops_the_frame(self):
        def grab(size, quality):
            self.lease.holder, self.lease.viewer_id, self.lease.epoch = "human", "hermes-desktop", 1
            return DATA_URL
        self.assertEqual(self.take(grab), (None, "private"))


class Computers(unittest.TestCase):
    def setUp(self):
        self.real = (dict(screen._agent_used), dict(screen._taken_at), screen.status)
        screen._agent_used.clear()
        screen._taken_at.clear()

    def tearDown(self):
        screen._agent_used.clear()
        screen._agent_used.update(self.real[0])
        screen._taken_at.clear()
        screen._taken_at.update(self.real[1])
        screen.status = self.real[2]

    def test_which_tools_drive_the_screen(self):
        for tool in ("computer_use", "browser_exec", "browser_navigate", "run_steps"):
            self.assertTrue(screen.uses_computer(tool), tool)
        for tool in ("terminal", "memory", "web_search", "read_file", None, ""):
            self.assertFalse(screen.uses_computer(tool), tool)

    def test_working_only_while_a_turn_is_running_and_recent(self):
        screen.note_agent_use("money")
        screen.note_agent_use("clients")
        self.assertEqual(screen.live_states({"money"}), {"money": "working"})   # clients' turn is over
        screen._agent_used["money"] = time.time() - screen.USE_WINDOW_SEC - 1
        self.assertEqual(screen.live_states({"money"}), {})

    def test_control_while_colin_holds_it_and_not_after(self):
        screen._taken_at["money"] = time.time()
        screen.status = lambda bot: {"you_have_control": True}
        self.assertEqual(screen.live_states(set()), {"money": "control"})
        screen.status = lambda bot: {"you_have_control": False}
        self.assertEqual(screen.live_states(set()), {})
        self.assertNotIn("money", screen._taken_at)

    def test_a_screen_that_cannot_be_read_is_not_an_error(self):
        screen._taken_at["money"] = time.time()

        def boom(bot):
            raise ImportError("no hermes")
        screen.status = boom
        self.assertEqual(screen.live_states(set()), {})


class RoutineLines(Store):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.dirs = watcher._cron_dirs
        (self.tmp / "money" / "cron" / "output").mkdir(parents=True)
        watcher._cron_dirs = lambda: [("money", self.tmp / "money" / "cron" / "output")]

    def tearDown(self):
        watcher._cron_dirs = self.dirs
        super().tearDown()

    def jobs(self, *items):
        (self.tmp / "money" / "cron" / "jobs.json").write_text(json.dumps({"jobs": list(items)}))

    def test_a_new_job_is_one_event_line_in_that_zips_chat(self):
        a = {"id": "a1", "name": "Daily spending update", "schedule": {"display": "0 19 * * *"}, "enabled": True}
        self.jobs(a)
        watcher.scan_jobs()
        self.assertEqual(self.feed(), [])               # the first look only remembers
        self.jobs(a, {"id": "b2", "name": "Overnight outbound", "schedule": {"display": "0 2 * * *"}, "enabled": True})
        watcher.scan_jobs()
        watcher.scan_jobs()
        self.assertEqual(self.feed(), [("money", "Added a scheduled job",
                                        {"event": "routine", "name": "Overnight outbound", "when": "every day at 2 a.m."})])
        self.assertEqual(store.db().execute("SELECT 1 FROM feed WHERE kind='checkin'").fetchall(), [])

    def test_a_changed_or_paused_job_is_still_a_check_in(self):
        a = {"id": "a1", "name": "Daily spending update", "schedule": {"display": "0 19 * * *"}, "enabled": True}
        self.jobs(a)
        watcher.scan_jobs()
        self.jobs({**a, "enabled": False})
        watcher.scan_jobs()
        self.assertEqual([r["title"] for r in store.db().execute("SELECT title FROM feed WHERE kind='checkin'")],
                         ["Paused a scheduled job"])
        self.assertEqual(self.feed(), [])

    def test_event_lines_show_in_the_chat_but_never_as_the_home_preview(self):
        store.add_feed("message", "money", None, "Money", "Dining is over the cap.")
        events.routine_created("money", "Overnight outbound", "0 2 * * *")
        events.memory_updated("money")
        self.assertEqual(store.latest_feed_for("money")["kind"], "message")
        for bot in ("default", "money"):
            events.memory_updated(bot)
            self.assertIn("event", [i["kind"] for i in store.activity_for(bot)])


class MemoryLines(Store):
    def stream(self, bot, sse, lane=None):
        httpx = __import__("httpx")
        real = hermes.httpx.AsyncClient
        factory = lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=sse)), **kw)  # noqa: E731

        async def run():
            return [ev async for ev in hermes.stream_turn(bot, "remember this", lane=lane)]
        with mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")), \
                mock.patch.object(hermes, "_headers", lambda bot_id: {}), \
                mock.patch.object(hermes.httpx, "AsyncClient", factory):
            return asyncio.run(run())

    @staticmethod
    def sse(*items):
        return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in items)

    def started(self, tool="memory", **args):
        return ("tool.started", {"tool_name": tool, "preview": "x", "args": args})

    def test_a_finished_memory_write_adds_one_line_per_turn(self):
        self.stream("money", self.sse(
            self.started(action="add", target="memory", content="Dining cap is $60."),
            ("tool.completed", {"tool_name": "memory"}),
            self.started(action="replace", target="user", old_text="a", content="b"),
            ("tool.completed", {"tool_name": "memory"}),
            ("assistant.completed", {"content": "Saved."})))
        self.assertEqual(self.feed(), [("money", "Updated memory", {"event": "memory"})])

    def test_a_failed_write_adds_nothing(self):
        self.stream("money", self.sse(self.started(action="add", content="x"),
                                      ("tool.failed", {"tool_name": "memory"}),
                                      ("assistant.completed", {"content": "Could not."})))
        self.stream("money", self.sse(self.started(action="add", content="x"),
                                      ("tool.completed", {"tool_name": "memory", "error": True}),
                                      ("assistant.completed", {"content": "Could not."})))
        self.assertEqual(self.feed(), [])

    def test_other_tools_and_other_lanes_add_nothing(self):
        self.stream("money", self.sse(self.started("terminal", command="ls"), ("tool.completed", {"tool_name": "terminal"}),
                                      ("assistant.completed", {"content": "Done."})))
        self.stream("money", self.sse(self.started(action="add", content="x"), ("tool.completed", {"tool_name": "memory"}),
                                      ("assistant.completed", {"content": "Saved."})), lane="team")
        self.assertEqual(self.feed(), [])

    def test_a_memory_tool_that_ends_while_another_runs_is_not_mixed_up(self):
        self.stream("money", self.sse(self.started(action="add", content="x"),
                                      ("tool.completed", {"tool_name": "terminal"}),
                                      ("tool.completed", {"tool_name": "memory"}),
                                      ("assistant.completed", {"content": "Saved."})))
        self.assertEqual(len(self.feed()), 1)

    def test_using_the_computer_in_a_chat_turn_is_noted(self):
        screen._agent_used.pop("money", None)
        self.stream("money", self.sse(self.started("computer_use", action="screenshot"),
                                      ("assistant.completed", {"content": "Looking."})))
        self.assertIn("money", screen._agent_used)
        screen._agent_used.pop("money", None)
        self.stream("money", self.sse(self.started("computer_use", action="screenshot"),
                                      ("assistant.completed", {"content": "Looking."})), lane="team")
        self.assertNotIn("money", screen._agent_used)

    def test_writes_memory(self):
        self.assertTrue(events.writes_memory({"action": "add"}))
        self.assertTrue(events.writes_memory({"action": "batch", "operations": [{"action": "remove"}]}))
        self.assertTrue(events.writes_memory(None))
        self.assertFalse(events.writes_memory({"action": "list"}))


class Pinging(Store):
    def setUp(self):
        super().setUp()
        team._ready = False

    def ask(self, from_bot, to_bot, answer=None, age=0):
        team._db().execute("INSERT INTO team_msgs (kind, from_bot, to_bot, text, answer, created_at) VALUES "
                           "('ask',?,?,'q',?,?)", (from_bot, to_bot, answer, int(time.time()) - age))
        team._db().commit()

    def test_a_question_with_no_answer_yet_is_a_chip_in_the_askers_chat(self):
        self.ask("default", "money")
        self.ask("default", "career")
        self.ask("default", "money")
        self.ask("builder", "money", answer="Sixty dollars.")
        self.ask("clients", "money", answer="(busy)")
        self.assertEqual(team.pinging(), [{"from": "default", "to": ["money", "career"]}])

    def test_a_question_nobody_answered_for_a_long_time_gives_up(self):
        self.ask("default", "money", age=team.PING_SHOWN_SEC + 5)
        self.assertEqual(team.pinging(), [])


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        from taffy_hub import board, consent
        self.saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY, config.LOOKS,
                      config.reload_bots, list(config.BOTS), dict(config.BOTS_BY_ID), board.bot_load, consent.pending,
                      screen.snapshot)
        tmp = Path(tempfile.mkdtemp())
        _fresh_store(tmp)
        config.HERMES_HOME = tmp / "hermes"
        for b in (config.BUILTIN + config.OWNER_ZIPS):
            (config.HERMES_HOME / "profiles" / b.id).mkdir(parents=True, exist_ok=True)
        config.NAMES = tmp / "bot-names.json"
        config.REGISTRY = tmp / "bots.json"
        config.LOOKS = tmp / "bot-looks.json"
        config.migrate_registry()   # the team from the registry, as in any home (not only the owner's)
        config.reload_bots = _real_reload
        config._registry_mtime = None
        config.reload_bots()
        self.client = TestClient(self.app.app)
        self.phone = {"Authorization": "Bearer " + store.redeem_pairing_code(store.new_pairing_code(), "Phone")}
        team._ready = False

    def tearDown(self):
        from taffy_hub import board, consent
        (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY, config.LOOKS,
         config.reload_bots, bots, by_id, board.bot_load, consent.pending, screen.snapshot) = self.saved
        config.BOTS[:] = bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id)
        store._local.conn = None
        store._schema_ready = False
        team._ready = False

    def test_the_picture_is_a_jpeg_for_a_paired_device(self):
        screen.snapshot = lambda bot: (JPEG, "")
        r = self.client.get("/api/bots/money/screen/snapshot", headers=self.phone)
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/jpeg", JPEG))
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_it_needs_a_paired_device_like_the_viewer(self):
        screen.snapshot = lambda bot: (JPEG, "")
        self.assertEqual(self.client.get("/api/bots/money/screen/snapshot").status_code, 401)
        self.assertEqual(self.client.get("/api/bots/money/screen/snapshot",
                                         headers={"Authorization": "Bearer nope"}).status_code, 401)

    def test_a_sleeping_private_or_unreadable_screen_has_no_picture(self):
        for result, code in (((None, "asleep"), 404), ((None, "private"), 423)):
            screen.snapshot = lambda bot, result=result: result
            r = self.client.get("/api/bots/money/screen/snapshot", headers=self.phone)
            self.assertEqual(r.status_code, code)
            self.assertNotRegex(r.json()["detail"], r"(?i)hermes|vnc|server|hub|api")

        def boom(bot):
            raise ImportError("no hermes here")
        screen.snapshot = boom
        self.assertEqual(self.client.get("/api/bots/money/screen/snapshot", headers=self.phone).status_code, 503)
        self.assertEqual(self.client.get("/api/bots/nobody/screen/snapshot", headers=self.phone).status_code, 404)

    def test_the_feed_says_which_computers_are_live_and_who_is_pinging(self):
        screen.note_agent_use("money")
        team._db().execute("INSERT INTO team_msgs (kind, from_bot, to_bot, text, created_at) VALUES ('ask','default',"
                           "'money','q',?)", (int(time.time()),))
        team._db().commit()
        with mock.patch.object(self.app.turns, "busy_bots", lambda: ["money"]):
            body = self.client.get("/api/feed", headers=self.phone).json()
        self.assertEqual(body["computers"], {"money": "working"})
        self.assertEqual(body["pinging"], [{"from": "default", "to": ["money"]}])
        screen._agent_used.pop("money", None)

    def test_a_problem_reading_them_never_breaks_the_feed(self):
        with mock.patch.object(screen, "live_states", side_effect=RuntimeError("x")), \
                mock.patch.object(team, "pinging", side_effect=RuntimeError("x")):
            body = self.client.get("/api/feed", headers=self.phone).json()
        self.assertEqual((body["computers"], body["pinging"]), ({}, []))


if __name__ == "__main__":
    unittest.main()


class JobWordingAndLoginCards(RoutineLines):
    """Oct 9: rewording how schedules read made every job post "Changed a scheduled job", and a sign-in card was
    posted but never reached Zero's chat."""

    def test_rewording_a_schedule_is_not_a_change(self):
        a = {"id": "a1", "name": "Daily spending update", "schedule": {"display": "0 19 * * *"}, "enabled": True}
        self.jobs(a)
        watcher.scan_jobs()
        seen = store.get_setting("cron_jobs_seen", {})
        for v in seen.values():
            v["when"] = "a differently worded time"          # the words changed, the schedule did not
        store.set_setting("cron_jobs_seen", seen)
        watcher.scan_jobs()
        self.assertEqual(store.db().execute("SELECT 1 FROM feed WHERE kind='checkin'").fetchall(), [])
        self.jobs({**a, "schedule": {"display": "0 20 * * *"}})
        watcher.scan_jobs()
        self.assertEqual(len(store.db().execute("SELECT 1 FROM feed WHERE kind='checkin'").fetchall()), 1)

    def test_a_sign_in_ask_reaches_the_zips_chat(self):
        for bot in ("default", "school"):
            store.add_feed("login", bot, None, "Sign in to example.com",
                           '{"url": "https://example.com", "site": "example.com", "why": "check", "state": "open"}')
            self.assertIn("login", [i["kind"] for i in store.activity_for(bot)])
