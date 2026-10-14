"""Onboarding answers: only known fields are kept, they are saved in the hub's store, and the agent's name
renames the lead Zip through the same rename rules as its profile.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien:
~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import config, onboarding, store  # noqa: E402

_real_reload = config.reload_bots

GOOD = {"agent_name": "Pip", "person_name": " Colin  O'Brien ", "voice": "kore",
        "about": {"work": "I build apps", "home": "Ohio", "age": "", "family": "Riley and two kids"},
        "sources": {"files": True, "apps": True, "contact": False, "calendar": True, "email": False,
                    "picked_files": True},
        "look": {"body": "o", "outfit": "classic"}}


def _fresh_store(tmp: Path) -> None:
    config.HUB_DIR = tmp
    config.HUB_DB = tmp / "hub.db"
    store._local.conn = None
    store._schema_ready = False


class Answers(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB)
        _fresh_store(Path(tempfile.mkdtemp()))

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False

    def test_cleans_and_keeps_known_fields(self):
        a = onboarding.clean(GOOD)
        self.assertEqual(a["person_name"], "Colin O'Brien")
        self.assertEqual(a["about"], {"work": "I build apps", "home": "Ohio", "family": "Riley and two kids"})
        self.assertEqual(a["sources"]["contact"], False)
        self.assertEqual(a["look"], {"body": "o", "outfit": "classic"})
        self.assertFalse(a["no_computer"])
        self.assertTrue(onboarding.clean({"no_computer": True})["no_computer"])

    def test_missing_sources_are_off(self):
        a = onboarding.clean({"agent_name": "Zero"})
        self.assertEqual(set(a["sources"]), set(onboarding.SOURCES))
        self.assertFalse(any(a["sources"].values()))
        self.assertEqual(a["about"], {})

    def test_refuses_unknown_or_oversized_answers(self):
        for bad in [{"about": {"salary": "lots"}}, {"sources": {"photos": True}}, {"sources": {"files": "yes"}},
                    {"person_name": "x" * 61}, {"about": {"work": "y" * 301}}, {"person_name": 7},
                    {"look": {"outfit": "x" * 41}}, {"look": {"outfit": ["classic"]}}, {"about": ["work"]},
                    {"no_computer": "yes"}]:
            with self.assertRaises(onboarding.OnboardingError, msg=str(bad)):
                onboarding.clean(bad)

    def test_saves_and_loads(self):
        self.assertIsNone(onboarding.load())
        onboarding.save(onboarding.clean(GOOD))
        loaded = onboarding.load()
        self.assertEqual(loaded["voice"], "kore")
        self.assertEqual(loaded["about"]["home"], "Ohio")
        self.assertIn("saved_at", loaded)


def _load_app():
    """The app module loads the sign-in rules from the taffy-tools plugin. Off alien, that plugin is read from
    this repo's alien/ folder instead."""
    if "taffy_hub.app" in sys.modules:
        mod = sys.modules["taffy_hub.app"]
        mod.BACKGROUND = False
        return mod
    plugin = config.HOME / "taffy" / "plugins" / "taffy-tools" / "consent.py"
    repo_plugin = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "consent.py"
    real_home = config.HOME
    try:
        if not plugin.exists():
            if not repo_plugin.exists():
                return None
            home = Path(tempfile.mkdtemp())
            (home / "taffy" / "plugins" / "taffy-tools").mkdir(parents=True)
            os.symlink(repo_plugin, home / "taffy" / "plugins" / "taffy-tools" / "consent.py")
            config.HOME = home
        mod = importlib.import_module("taffy_hub.app")
        mod.BACKGROUND = False
        return mod
    except ImportError:
        return None
    finally:
        config.HOME = real_home


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY,
                      config.LOOKS, config.reload_bots, list(config.BOTS), dict(config.BOTS_BY_ID))
        tmp = Path(tempfile.mkdtemp())
        _fresh_store(tmp)
        config.HERMES_HOME = tmp / "hermes"
        config.NAMES = tmp / "bot-names.json"
        config.REGISTRY = tmp / "bots.json"
        config.LOOKS = tmp / "bot-looks.json"
        config.reload_bots = _real_reload
        config.reload_bots()
        self.app.app.dependency_overrides[self.app.require_device] = lambda: "Test iPhone"
        self.client = TestClient(self.app.app)

    def tearDown(self):
        self.app.app.dependency_overrides.clear()
        (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY,
         config.LOOKS, config.reload_bots, bots, by_id) = self.saved
        config.BOTS[:] = bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id)
        store._local.conn = None
        store._schema_ready = False

    def test_saves_answers_and_renames_the_lead(self):
        r = self.client.post("/api/onboarding", json=GOOD)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["onboarding"]["agent_name"], "Pip")
        self.assertEqual(config.BOTS_BY_ID["default"].name, "Pip")
        self.assertEqual(json.loads(config.NAMES.read_text())["default"], "Pip")
        from taffy_hub import looks
        self.assertEqual(looks.for_bot("default"), {"body": "o", "outfit": "classic"})
        got = self.client.get("/api/onboarding").json()["onboarding"]
        self.assertEqual(got["person_name"], "Colin O'Brien")
        self.assertTrue(got["sources"]["calendar"])

    def test_no_name_keeps_the_lead_name(self):
        before = config.BOTS_BY_ID["default"].name
        r = self.client.post("/api/onboarding", json={**GOOD, "agent_name": ""})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(config.BOTS_BY_ID["default"].name, before)

    def test_bad_name_is_refused_and_nothing_is_saved(self):
        r = self.client.post("/api/onboarding", json={**GOOD, "agent_name": "<script>"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(onboarding.load())

    def test_look_endpoint_takes_rendered_looks(self):
        r = self.client.put("/api/bots/default/look", json={"body": "moon", "outfit": "none"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["look"], {"body": "moon", "outfit": "plain"})
        r = self.client.put("/api/bots/default/look", json={"body": "moon", "outfit": "classic"})
        self.assertEqual(r.json()["look"], {"body": "moon", "outfit": "classic"})
        bad = self.client.put("/api/bots/default/look", json={"body": "flat-moon", "outfit": "classic"})
        self.assertEqual(bad.status_code, 400)

    def test_bad_answers_are_refused(self):
        r = self.client.post("/api/onboarding", json={**GOOD, "sources": {"photos": True}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("photos", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
