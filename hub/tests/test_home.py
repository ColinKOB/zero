"""The home settings reader (taffy_hub/home.py): defaults equal Colin's home before the file existed, the merge
rules, a bad file, writes, the home's time zone, and agreement with the plugin's reader on the sample files.

Run: python3 -m unittest discover -s hub/tests (from the repo)
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import home  # noqa: E402
import home_fixture  # noqa: E402


class Defaults(unittest.TestCase):
    def test_no_file_means_todays_values(self):
        with home_fixture.use(None) as f:
            self.assertFalse(f.exists())
            self.assertEqual(home.load(), home.colin_settings())
            self.assertEqual(home.person_name(), "Colin O'Brien")
            self.assertEqual(home.person_name(False), "Colin")
            self.assertEqual(home.get("ports.hub"), 8650)
            self.assertEqual(home.get("ports.hermes_api"), 8642)
            self.assertEqual(home.path_of("hub_state"), home.USER_HOME / "taffy" / "hub-state")
            self.assertEqual(home.path_of("hermes_home"), home.USER_HOME / ".taffy")
            self.assertEqual(home.timezone_name(), "America/New_York")
            self.assertEqual(home.quiet_hours(), (23, 7))
            self.assertEqual(home.plan_kind(), "own")
            self.assertEqual(home.connectors(), "claude")
            self.assertEqual([a["id"] for a in home.mail_accounts()], ["personal", "lakeview", "goodworks"])
            self.assertEqual(home.trello()["board_id"], "AbCd1234")
            self.assertTrue(home.tally())
            self.assertTrue(home.is_owner_home())

    def test_colin_sample_is_the_defaults(self):
        self.assertEqual(home_fixture.sample("colin"), home.colin_settings())

    def test_another_home_never_inherits_colins_facts(self):
        with home_fixture.use({"home_id": "sam", "person": {"name": "Sam Rivera"}}):
            self.assertEqual(home.person_name(), "Sam Rivera")
            self.assertEqual(home.mail_accounts(), [])
            self.assertIsNone(home.trello())
            self.assertIsNone(home.tally())
            self.assertEqual(home.get("paths.background_docs"), [])
            self.assertFalse(home.get("judge.enabled"))
            self.assertEqual(home.connectors(), "none")
            self.assertFalse(home.get("backup.mac_copy_expected"))
            self.assertIsNone(home.get("zips.owner_keywords"))
            self.assertIsNone(home.get("mail.owner_by_domain"))
            self.assertEqual(home.get("ports.hub"), 8650)          # the layout is shared
        with home_fixture.use({"home_id": "sam"}):
            self.assertEqual(home.person_name(), "the person")
        with home_fixture.use({"home_id": "colin", "person": {"timezone": "America/Chicago"}}):
            self.assertEqual(home.person_name(), "Colin O'Brien")   # Colin's own file keeps his facts
            self.assertEqual(home.trello()["board_id"], "AbCd1234")

    def test_zero_home_names_the_file(self):
        saved_env, saved_file = os.environ.get("ZERO_HOME"), home.FILE
        try:
            home.FILE = None
            os.environ["ZERO_HOME"] = str(home_fixture.HOMES / "riley.json")
            home.reset_cache()
            self.assertEqual(home.person_name(), "Riley Ellis")
        finally:
            home.FILE = saved_file
            if saved_env is None:
                os.environ.pop("ZERO_HOME", None)
            else:
                os.environ["ZERO_HOME"] = saved_env
            home.reset_cache()


class Merge(unittest.TestCase):
    def test_objects_merge_and_the_rest_replaces(self):
        with home_fixture.use({"person": {"name": "Sam Rivera"}, "mail": {"accounts": []},
                               "integrations": {"tally": None}}):
            self.assertEqual(home.person_name(), "Sam Rivera")
            self.assertEqual(home.get("person.timezone"), "America/New_York")   # kept from the defaults
            self.assertEqual(home.mail_accounts(), [])
            self.assertIsNone(home.tally())
            self.assertIsNone(home.get("integrations.tally", "missing"))
            self.assertEqual(home.trello()["board_id"], "AbCd1234")

    def test_riley_shape(self):
        with home_fixture.use("riley"):
            self.assertEqual(home.plan_kind(), "shared")
            self.assertEqual(home.connectors(), "none")
            self.assertIsNone(home.trello())
            self.assertIsNone(home.tally())
            self.assertFalse(home.is_owner_home())
            self.assertEqual(home.get("ports.hub"), 8651)
            self.assertEqual(home.get("google.client_id"), "000000000000-yourgoogleclientid.apps.googleusercontent.com")

    def test_first_name_from_the_full_name(self):
        with home_fixture.use({"person": {"name": "Ada Lovelace", "first_name": ""}}):
            self.assertEqual(home.person_name(False), "Ada")


class BadFile(unittest.TestCase):
    def test_unreadable_file_stops_rather_than_becoming_colin(self):
        with home_fixture.use({}) as f:
            f.write_text("{not json")
            home.reset_cache()
            with self.assertRaises(home.HomeError):
                home.load()

    def test_a_later_bad_write_keeps_the_last_good_copy(self):
        with home_fixture.use({"person": {"name": "Sam Rivera"}}) as f:
            self.assertEqual(home.person_name(), "Sam Rivera")
            f.write_text("[1, 2")
            os.utime(f, (1, 1))
            self.assertEqual(home.person_name(), "Sam Rivera")

    def test_newer_version_is_refused(self):
        with home_fixture.use({"version": 99}):
            with self.assertRaises(home.HomeError):
                home.load()


class Save(unittest.TestCase):
    def test_first_save_writes_todays_values_in_full(self):
        with home_fixture.use(None) as f:
            home.save({"person": {"quiet_hours": {"start": 22, "end": 6}}})
            written = json.loads(f.read_text())
            self.assertEqual(written["ports"]["hub"], 8650)
            self.assertEqual(written["person"]["name"], "Colin O'Brien")
            self.assertEqual(home.quiet_hours(), (22, 6))

    def test_save_keeps_unknown_keys(self):
        with home_fixture.use({"home_id": "test", "future": {"x": 1}}) as f:
            home.save({"locale": {"units": "metric"}})
            written = json.loads(f.read_text())
            self.assertEqual(written["future"], {"x": 1})
            self.assertEqual(written["locale"], {"units": "metric"})
            self.assertEqual(home.get("locale.spelling"), "american")


class TimeZone(unittest.TestCase):
    def test_home_tz_matches_zoneinfo_across_the_clock_change(self):
        with home_fixture.use({"person": {"timezone": "America/Los_Angeles"}}):
            z = ZoneInfo("America/Los_Angeles")
            for ts in (1762074000, 1762077600, 1762081200, 1773568800, 1760000000):   # around Nov 2 and Mar 15
                a, b = datetime.fromtimestamp(ts, home.TZ), datetime.fromtimestamp(ts, z)
                self.assertEqual(a.replace(tzinfo=None), b.replace(tzinfo=None))
                self.assertEqual(a.utcoffset(), b.utcoffset())
                self.assertEqual(a.timestamp(), ts)
                self.assertEqual(a.astimezone(timezone.utc), b.astimezone(timezone.utc))
            self.assertEqual(home.tz_word(), "Pacific")

    def test_tz_follows_the_file(self):
        with home_fixture.use({"person": {"timezone": "Europe/Berlin"}}):
            self.assertEqual(datetime(2026, 7, 1, 12, tzinfo=timezone.utc).astimezone(home.TZ).hour, 14)
            self.assertEqual(home.tz_word(), "Berlin time")
        with home_fixture.use(None):
            self.assertEqual(datetime(2026, 7, 1, 12, tzinfo=timezone.utc).astimezone(home.TZ).hour, 8)
            self.assertEqual(home.tz_word(), "Eastern")
            self.assertEqual(home.tz_phrase(), "Eastern (New York)")

    def test_unknown_zone_falls_back(self):
        with home_fixture.use({"person": {"timezone": "Mars/Olympus"}}):
            self.assertEqual(home.timezone_name(), "America/New_York")

    def test_hour_words_and_next_quiet_end(self):
        self.assertEqual([home.hour_words(h) for h in (0, 7, 12, 23)], ["midnight", "7 a.m.", "noon", "11 p.m."])
        with home_fixture.use(None):
            now = datetime(2026, 10, 9, 22, 30, tzinfo=home.TZ)
            self.assertEqual(home.next_quiet_end(now).strftime("%d %H"), "10 07")
            now = datetime(2026, 10, 9, 5, 0, tzinfo=home.TZ)
            self.assertEqual(home.next_quiet_end(now).strftime("%d %H"), "09 07")


PLUGIN_HOME = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "home.py"
if not PLUGIN_HOME.exists():   # on alien the hub is deployed without the repo; use the installed plugin
    PLUGIN_HOME = Path.home() / "taffy" / "plugins" / "taffy-tools" / "home.py"


def projection(s: dict) -> dict:
    """What both readers must agree on: everything the hub and the plugin both read."""
    mail = [(a.get("id"), a.get("address"), str(a.get("kind") or "imap"), a.get("owner_zip"))
            for a in (s.get("mail") or {}).get("accounts") or []]
    cals = [(c.get("id"), c.get("kind"), c.get("address") or c.get("account")) for c in s.get("calendars") or []]
    trello = (s.get("integrations") or {}).get("trello")
    return {"home_id": s.get("home_id"), "person": {k: s["person"].get(k) for k in ("name", "first_name", "timezone",
                                                                                    "quiet_hours", "pronoun_free")},
            "locale": s.get("locale"), "paths": s.get("paths"), "ports": s.get("ports"), "network": s.get("network"),
            "host": s.get("host"), "plan": {k: s["plan"].get(k) for k in ("claude", "chatgpt", "connectors", "frugal")},
            "mail": mail, "calendars": cals, "tally": (s.get("integrations") or {}).get("tally"),
            "trello": (trello or {}).get("board_id") if isinstance(trello, dict) else trello,
            "judge": s.get("judge"), "push": s.get("push"), "backup": s.get("backup")}


@unittest.skipUnless(PLUGIN_HOME.exists(), "the plugin's home.py is not in this tree yet (WP2)")
class PluginAgrees(unittest.TestCase):
    """The hub and the plugin read the same file the same way (plan section 4): the same settings for no file,
    Colin's written-out file, Riley's, a stranger's, and a partial stranger's file that must not inherit Colin's
    facts. The plugin's reader is loaded by path; ZERO_HOME names the file for both."""

    def _plugin(self):
        spec = importlib.util.spec_from_file_location("plugin_home_under_test", PLUGIN_HOME)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _both(self, mod, settings) -> tuple[dict, dict]:
        saved_env, saved_file = os.environ.get("ZERO_HOME"), home.FILE
        folder = Path(tempfile.mkdtemp())
        f = folder / "home.json"
        if settings is not None:
            f.write_text(json.dumps(settings))
        try:
            os.environ["ZERO_HOME"] = str(f)
            home.FILE = None
            home.reset_cache()
            getattr(mod, "_cache", {}).clear()
            return projection(home.load()), projection(mod.load())
        finally:
            home.FILE = saved_file
            if saved_env is None:
                os.environ.pop("ZERO_HOME", None)
            else:
                os.environ["ZERO_HOME"] = saved_env
            home.reset_cache()

    def test_same_settings_for_every_shape(self):
        mod = self._plugin()
        shapes = {"no file": None, "partial stranger": {"home_id": "sam", "person": {"name": "Sam Rivera"}},
                  **{n: home_fixture.sample(n) for n in home_fixture.SAMPLES}}
        for name, settings in shapes.items():
            with self.subTest(shape=name):
                hub_view, plugin_view = self._both(mod, settings)
                self.assertEqual(hub_view, plugin_view)


if __name__ == "__main__":
    unittest.main()
