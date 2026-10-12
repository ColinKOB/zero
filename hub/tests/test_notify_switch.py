"""Each Zip's "Notify Me When It Finishes or Needs Me" switch (store.notify_on, watcher.push, PUT /api/bots/{id}/notify).

On for every Zip until Colin turns it off. Off keeps that Zip's finished work and waiting cards in the app and sends
no notification; quiet hours, mute and snooze work as before. Proactivity (notices.py) has its own budget.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import apns, board, config, consent, notices, store, watcher  # noqa: E402
import plugin_loader  # noqa: E402
from test_onboarding import _fresh_store, _load_app, _real_reload  # noqa: E402

plugin = plugin_loader.load().consent


class Setting(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB)
        _fresh_store(Path(tempfile.mkdtemp()))

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False

    def test_every_zip_starts_on_so_nothing_changes_until_a_switch_is_flipped(self):
        for b in ("default", "builder", "career", "clients", "money", "school", "a-new-zip", None):
            self.assertTrue(store.notify_on(b), b)

    def test_off_and_on_again(self):
        store.set_notify("builder", False)
        self.assertFalse(store.notify_on("builder"))
        self.assertTrue(store.notify_on("career"))
        store.set_notify("builder", False)   # twice is the same as once
        store.set_notify("money", False)
        self.assertEqual(store.get_setting("notify_off_bots"), ["builder", "money"])
        store.set_notify("builder", True)
        self.assertTrue(store.notify_on("builder"))
        self.assertFalse(store.notify_on("money"))

    def test_none_means_the_lead(self):
        store.set_notify("default", False)
        self.assertFalse(store.notify_on(None))

    def test_the_switch_is_not_mute_or_snooze(self):
        store.set_notify("builder", False)
        self.assertFalse(store.quiet("builder"))


class Pushes(unittest.TestCase):
    def setUp(self):
        # Never the live calendar: a real shift on it held these test notifications (Oct 10, 9 a.m.).
        from taffy_hub import notices as _notices
        real_busy = _notices.busy_block
        _notices.busy_block = lambda t=None: None
        self.addCleanup(setattr, _notices, "busy_block", real_busy)
        self.saved = (config.HUB_DIR, config.HUB_DB, apns.ready, watcher._night, watcher._push_sync)
        _fresh_store(Path(tempfile.mkdtemp()))
        apns.ready = lambda: True
        self.sent = []
        watcher._push_sync = lambda title, body, bot_id=None, **kw: self.sent.append(kw.get("topic") or title) or True
        watcher._night = lambda: False

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB, apns.ready, watcher._night, watcher._push_sync = self.saved
        store._local.conn = None
        store._schema_ready = False

    def push(self, *a, **kw):
        return asyncio.run(watcher.push(*a, **kw))

    def test_on_by_default_sends_as_before(self):
        self.assertTrue(self.push("Builder finished", "x", "builder"))
        self.assertTrue(self.push("Career needs you", "x", "career"))
        self.assertEqual(self.sent, ["Builder finished", "Career needs you"])

    def test_off_sends_nothing_for_that_zip_only(self):
        store.set_notify("builder", False)
        self.assertFalse(self.push("Builder finished", "x", "builder"))
        self.assertFalse(self.push("Builder needs your okay", "x", "builder", urgent=True))
        self.assertTrue(self.push("Career finished", "x", "career"))
        self.assertEqual(self.sent, ["Career finished"])

    def test_off_wins_over_urgent_but_not_for_the_system(self):
        store.set_notify("default", False)
        self.assertFalse(self.push("Zero", "Here is what you asked for.", "default", urgent=True))
        self.assertTrue(self.push("Zero", "Your Zips stopped answering.", urgent=True))   # health: no Zip named
        self.assertEqual(self.sent, ["Zero"])

    def test_a_group_goes_out_unless_every_zip_in_it_is_off(self):
        store.set_notify("builder", False)
        self.assertTrue(self.push("Team update", "x", bots=["builder", "career"]))
        store.set_notify("career", False)
        self.assertFalse(self.push("Team update", "x", bots=["builder", "career"]))
        self.assertEqual(self.sent, ["Team update"])

    def test_turning_it_back_on_sends_again(self):
        store.set_notify("builder", False)
        self.assertFalse(self.push("Builder finished", "x", "builder"))
        store.set_notify("builder", True)
        self.assertTrue(self.push("Builder finished", "x", "builder"))

    def test_quiet_hours_still_hold_a_zip_that_is_on_and_skip_one_that_is_off(self):
        watcher._night = lambda: True
        store.set_notify("builder", False)
        self.push("Builder finished", "x", "builder")
        self.push("Career finished", "x", "career")
        self.assertEqual(self.sent, [])
        self.assertEqual([h["title"] for h in store.get_setting("held_pushes")], ["Career finished"])
        watcher._night = lambda: False
        asyncio.run(watcher.send_morning_digest())
        self.assertEqual(self.sent, ["Career finished"])   # one held item goes out as itself (Oct 10)

    def test_mute_and_snooze_still_work_with_the_switch_on(self):
        store.set_setting("muted_bots", ["money"])
        self.assertFalse(self.push("Money finished", "x", "money"))
        self.assertTrue(store.notify_on("money"))

    def test_task_alerts_from_the_lead_go_through_the_same_gate(self):
        """A blocked, review or failed task is told through notices.nudge; with proactivity off that is push()."""
        store.set_notify("school", False)
        asyncio.run(notices.nudge("task:t_1:blocked", "School needs you", "Quiz", "school"))
        asyncio.run(notices.nudge("task:t_2:blocked", "Money needs you", "Bill", "money"))
        self.assertEqual(self.sent, ["Money needs you"])


class Cards(unittest.TestCase):
    """A card from a Zip still reaches the app's feed with the switch off; only the notification is skipped."""

    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB, apns.ready, watcher._night, watcher._push_sync, plugin.DB_PATH)
        tmp = Path(tempfile.mkdtemp())
        _fresh_store(tmp)
        plugin.DB_PATH = tmp / "consent.db"
        apns.ready = lambda: True
        self.sent = []
        watcher._push_sync = lambda title, body, bot_id=None, **kw: self.sent.append(kw.get("topic") or title) or True
        watcher._night = lambda: False
        store.set_cursor("consent_request", 0)
        store.set_cursor("consent_signin", 10**6)
        store.set_cursor("consent_action", 10**6)

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB, apns.ready, watcher._night, watcher._push_sync, plugin.DB_PATH = self.saved
        store._local.conn = None
        store._schema_ready = False

    def titles(self):
        return [r["title"] for r in store.db().execute("SELECT title FROM feed WHERE kind='signin_request' ORDER BY id")]

    def test_a_sign_in_card_pushes_only_for_a_zip_that_is_on(self):
        store.set_notify("school", False)
        plugin.request("school", "elearning.lakeview.test", "what is due?", None, None, why="mismatch")
        plugin.request("career", "linkedin.com", "check messages", None, None, why="mismatch")
        asyncio.run(watcher.scan_consent())
        self.assertEqual(len(self.titles()), 2)   # both cards are in the app
        self.assertEqual([t for t in self.sent if "linkedin" in t], self.sent)
        self.assertEqual(len(self.sent), 1)

    def test_an_action_card_pushes_only_for_a_zip_that_is_on(self):
        store.set_notify("builder", False)
        plugin.request("builder", "gmail.com", "send the preview to Miss Dana", None, None, kind="send",
                       why="asked")
        plugin.request("career", "gmail.com", "send the resume to a recruiter", None, None, kind="send",
                       why="asked")
        asyncio.run(watcher.scan_consent())
        self.assertEqual(len(self.titles()), 2)
        self.assertEqual([t.lower() for t in self.sent], ["career needs your okay"])


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY, config.LOOKS,
                      config.reload_bots, list(config.BOTS), dict(config.BOTS_BY_ID), board.bot_load,
                      consent.pending, self.app._last_message)
        tmp = Path(tempfile.mkdtemp())
        _fresh_store(tmp)
        config.HERMES_HOME = tmp / "hermes"
        for b in (config.BUILTIN + config.OWNER_ZIPS):   # a bot whose profile is missing drops out of the team
            (config.HERMES_HOME / "profiles" / b.id).mkdir(parents=True, exist_ok=True)
        config.NAMES = tmp / "bot-names.json"
        config.REGISTRY = tmp / "bots.json"
        config.LOOKS = tmp / "bot-looks.json"
        config.migrate_registry()   # the team from the registry, as in any home (not only the owner's)
        config.reload_bots = _real_reload
        config._registry_mtime = None
        config.reload_bots()
        board.bot_load = lambda: {}
        consent.pending = lambda: []

        async def no_preview(bot_id):
            return None, None
        self.app._last_message = no_preview
        self.client = TestClient(self.app.app)
        self.phone = {"Authorization": "Bearer " + store.redeem_pairing_code(store.new_pairing_code(), "Phone")}
        self.mac = {"Authorization": "Bearer " + store.redeem_pairing_code(store.new_pairing_code(), "Mac")}

    def tearDown(self):
        self.app.app.dependency_overrides.clear()
        (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY, config.LOOKS,
         config.reload_bots, bots, by_id, board.bot_load, consent.pending, self.app._last_message) = self.saved
        config.BOTS[:] = bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id)
        store._local.conn = None
        store._schema_ready = False

    def listed(self, headers):
        r = self.client.get("/api/bots", headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        return {b["id"]: b["notify"] for b in r.json()["bots"]}

    def test_every_zip_is_listed_on_by_default(self):
        listed = self.listed(self.phone)
        self.assertTrue(listed and all(listed.values()), listed)

    def test_the_phone_changes_it_and_the_mac_sees_it(self):
        r = self.client.put("/api/bots/builder/notify", json={"on": False}, headers=self.phone)
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True, "notify": False}))
        for headers in (self.phone, self.mac):
            listed = self.listed(headers)
            self.assertFalse(listed["builder"])
            self.assertTrue(listed["career"])
        self.client.put("/api/bots/builder/notify", json={"on": True}, headers=self.mac)
        self.assertTrue(self.listed(self.phone)["builder"])

    def test_it_survives_a_restart_of_the_hub(self):
        self.client.put("/api/bots/money/notify", json={"on": False}, headers=self.phone)
        store._local.conn = None   # a new connection to the same file, as after a restart
        store._schema_ready = False
        self.assertFalse(self.listed(self.phone)["money"])

    def test_needs_a_paired_device(self):
        self.assertEqual(self.client.put("/api/bots/builder/notify", json={"on": False}).status_code, 401)
        r = self.client.put("/api/bots/builder/notify", json={"on": False}, headers={"Authorization": "Bearer nope"})
        self.assertEqual(r.status_code, 401)
        self.assertTrue(store.notify_on("builder"))

    def test_unknown_zip_and_bad_body_are_refused(self):
        self.assertEqual(self.client.put("/api/bots/nobody/notify", json={"on": False}, headers=self.phone).status_code,
                         404)
        for bad in ({}, {"on": "maybe"}, {"on": None}):
            self.assertEqual(self.client.put("/api/bots/builder/notify", json=bad, headers=self.phone).status_code,
                             422, bad)
        self.assertTrue(store.notify_on("builder"))


if __name__ == "__main__":
    unittest.main()
