"""Contracts between the five Zero for Anyone packages, checked across their real code (integration, Oct 9).

- A home written by the home scripts (WP3's zero-home-lib.new_home_settings) reads the same in the hub's and the
  plugin's readers, never inherits Colin's facts, names who shares the plan, and carries Zero's Google client.
- All three readers (hub, plugin, WP3's renderer) agree on the defaults a partial stranger's file falls back to.
- The plugin's Google token call (google_api -> mac._call) is answered by the hub's /internal/accounts/google_token.
- /api/home-settings has every field the app's HomeSettings decoder reads, and /api/templates leaves no {name}.
- The protocol strings the plugin writes are the ones the hub and the app parse.

Run: python3 -m unittest discover -s hub/tests
"""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, google, hermes, history, home, voice  # noqa: E402
import home_fixture  # noqa: E402
from test_gate import tt  # noqa: E402
from test_home import projection  # noqa: E402
from test_home_accounts import CLIENT, VERIFIER, FakeGoogle  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "alien" / "bin" / "zero-home-lib.py"
APP_STATE = REPO / "ios" / "Taffy" / "Model" / "AppState.swift"
HOME_SETTINGS = REPO / "ios" / "Taffy" / "Model" / "HomeSettings.swift"
COLIN_FACTS = ("Colin", "jdoe", "Jd910@", "goodworks", "AbCd1234", "18930", "tally/server", "background-record")


def _lib():
    loader = SourceFileLoader("zero_home_lib_contracts", str(LIB))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _both(settings: dict | None) -> tuple[dict, dict]:
    """(the hub's view, the plugin's view) of one settings file."""
    with home_fixture.use(settings if settings is not None else None):
        hub_full = home.load()
        with tt.home.pinned(settings):
            plugin_full = tt.home.load()
    return hub_full, plugin_full


@unittest.skipUnless(LIB.exists(), "the home scripts are not in this tree")
class HomeScriptsWriteWhatTheReadersRead(unittest.TestCase):
    def setUp(self):
        self.lib = _lib()

    def written(self, home_id: str) -> dict:
        preset = self.lib.PRESETS[home_id]
        ports = self.lib.allocate_ports(home_id, {"homes": {}}, set())
        return self.lib.new_home_settings(home_id, preset["person"], preset["timezone"], ports,
                                          first_name=preset.get("first_name"),
                                          google_account=preset.get("google_account"), owner_name="Colin")

    def test_hub_and_plugin_agree_on_written_homes(self):
        for home_id in ("riley", "test"):
            with self.subTest(home=home_id):
                s = self.written(home_id)
                hub_view, plugin_view = _both(s)
                self.assertEqual(projection(hub_view), projection(plugin_view))
                with tempfile.TemporaryDirectory() as d:
                    f = Path(d) / "home.json"
                    f.write_text(json.dumps(s))
                    self.assertEqual(projection(self.lib.load_home(f)), projection(hub_view))

    def test_a_written_home_holds_no_colin_fact_but_the_sharer_name(self):
        for home_id in ("riley", "test"):
            s = self.written(home_id)
            plan_owner = s["plan"].pop("owner_name")
            self.assertEqual(plan_owner, "Colin")
            text = json.dumps(s)
            for bad in COLIN_FACTS:
                self.assertNotIn(bad, text, f"{bad} in {home_id}'s home.json")
            with home_fixture.use({**s, "plan": {**s["plan"], "owner_name": plan_owner}}):
                summary = home.summary()
                self.assertEqual((summary["plan"], summary["connectors"], summary["host"]["kind"]),
                                 ("shared", "none", "server"))
                self.assertEqual(home.get("plan.owner_name"), "Colin")
                self.assertEqual(google.setup()["client_id"], CLIENT)
                self.assertTrue(google.setup()["available"])
                self.assertEqual(home.mail_accounts(), [])
                self.assertIsNone(home.tally())
                self.assertIsNone(home.trello())

    def test_the_three_readers_agree_on_a_partial_stranger(self):
        partial = {"home_id": "dana", "person": {"timezone": "America/Denver"}}
        hub_view, plugin_view = _both(partial)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "home.json"
            f.write_text(json.dumps(partial))
            wp3 = self.lib.load_home(f)
        for view in (hub_view, plugin_view, wp3):
            self.assertEqual(view["person"]["name"], "")
            self.assertTrue(view["person"]["pronoun_free"])
            self.assertEqual(view["plan"]["connectors"], "none")
            self.assertFalse(view["judge"]["enabled"])
            self.assertEqual(view["host"]["lead_backend"], "docker")
            self.assertFalse(view["backup"]["mac_copy_expected"])
            self.assertEqual(view["mail"]["accounts"], [])
            self.assertEqual(view["paths"]["background_docs"], [])
        self.assertEqual(projection(hub_view), projection(plugin_view))

    def test_hub_and_plugin_agree_on_odd_connector_values(self):
        """Night review: an explicit null, an empty string or an unknown value for plan.connectors meant "claude" to
        the hub and "none" to the plugin, so the hub ran connector checks for a home whose Zips had no connectors.
        Both now read anything but "claude" or "chatgpt" as "none"."""
        for value in (None, "", "false", "yes", "CLAUDE", "chatgpt", "claude"):
            with self.subTest(value=value):
                s = {"home_id": "dana", "plan": {"connectors": value}}
                with home_fixture.use(s):
                    hub_says = home.connectors()
                with tt.home.pinned(s):
                    plugin_says = tt.home.connectors()
                    plugin_on = tt.home.connectors_on()
                self.assertEqual(hub_says, plugin_says)
                self.assertEqual(plugin_on, hub_says != "none")
                self.assertEqual(hub_says, {"CLAUDE": "claude"}.get(value, value)
                                 if str(value).lower() in ("claude", "chatgpt") else "none")

    def test_the_three_readers_agree_on_no_file(self):
        hub_view, plugin_view = _both(None)
        wp3 = self.lib.load_home(None)
        for view in (hub_view, plugin_view, wp3):
            self.assertEqual((view["home_id"], view["person"]["first_name"], view["person"]["pronoun_free"]),
                             ("colin", "Colin", False))
            self.assertEqual(view["plan"]["connectors"], "claude")
            self.assertTrue(view["judge"]["enabled"])
            self.assertEqual(view["host"]["lead_backend"], "local")
            self.assertEqual(view["ports"]["hub"], 8650)


class PluginTokenCallReachesTheHub(AppCase):
    """google_api.token -> mac._call("google_token", area="accounts") -> POST /internal/accounts/google_token."""

    SAM = {"home_id": "test", "mail": {"accounts": []}, "calendars": [],
           "plan": {"connectors": "none", "owner_name": "Jordan", "claude": {"mode": "broker"},
                    "chatgpt": {"mode": "broker"}},
           "integrations": {"trello": None, "tally": None}, "person": {"name": "Sam Rivera", "first_name": "Sam"}}

    def setUp(self):
        super().setUp()
        self.fake = FakeGoogle()
        self.addCleanup(self.fake.close)
        saved = (google.TOKEN_URL, google.REVOKE_URL, google.GMAIL_API, google.CALENDAR_API)
        self.addCleanup(lambda: (setattr(google, "TOKEN_URL", saved[0]), setattr(google, "REVOKE_URL", saved[1]),
                                 setattr(google, "GMAIL_API", saved[2]), setattr(google, "CALENDAR_API", saved[3])))
        google.TOKEN_URL, google.REVOKE_URL = f"{self.fake.base}/token", f"{self.fake.base}/revoke"
        google.GMAIL_API, google.CALENDAR_API = f"{self.fake.base}/gmail/v1", f"{self.fake.base}/calendar/v3"
        self.home = home_fixture.use_in(self, self.SAM)

    def _route(self, seen: list):
        """urlopen for the plugin's mac module: the request goes to the hub under test, never the network."""
        client = self.client

        def urlopen(req, timeout=None):
            url = urllib.parse.urlsplit(req.full_url)
            seen.append((url.path, dict(req.header_items()), json.loads(req.data)))
            r = client.post(url.path, content=req.data, headers=dict(req.header_items()))
            if r.status_code >= 400:
                raise urllib.error.HTTPError(req.full_url, r.status_code, "refused", {}, io.BytesIO(r.content))
            return io.BytesIO(r.content)
        return urlopen

    def test_the_plugin_gets_a_token_from_the_hub(self):
        r = self.client.post("/api/accounts/google", json={"code": "good-code", "code_verifier": VERIFIER,
                                                           "redirect_uri": google.redirect_uri(), "client_id": CLIENT})
        self.assertTrue(r.json()["ok"], r.text)
        saved = json.loads(self.home.read_text())
        key = Path(tempfile.mkdtemp()) / "internal-key"
        key.write_text(self.app._internal_key())
        seen: list = []
        mac, google_api = tt.mac, tt.google_api
        google_api._tokens.clear()
        with tt.home.pinned(saved), mock.patch.object(mac, "key_file", return_value=key), \
                mock.patch.object(mac.urllib.request, "urlopen", self._route(seen)):
            [mailbox] = [m for m in saved["mail"]["accounts"] if m.get("kind") == "google_oauth"]
            token = google_api.token(mailbox, "mail", "s1")
            [cal] = [c for c in saved["calendars"] if c.get("kind") == "google_oauth"]
            google_api._tokens.clear()
            token2 = google_api.token(cal, "calendar", "s1")
        self.assertTrue(token.startswith("at-") and token2.startswith("at-"))
        path, headers, body = seen[0]
        self.assertEqual(path, "/internal/accounts/google_token")
        self.assertIn("X-taffy-internal", headers)
        self.assertEqual(set(body), {"bot_id", "session_id", "args"})
        self.assertEqual(set(body["args"]), {"account", "address", "purpose"})

    def test_a_missing_sign_in_is_a_plain_sentence_for_the_zip(self):
        key = Path(tempfile.mkdtemp()) / "internal-key"
        key.write_text(self.app._internal_key())
        tt.google_api._tokens.clear()
        with mock.patch.object(tt.mac, "key_file", return_value=key), \
                mock.patch.object(tt.mac.urllib.request, "urlopen", self._route([])):
            with self.assertRaises(tt.google_api.GoogleError) as e:
                tt.google_api.token({"id": "gone", "address": "gone@gmail.com"}, "mail", "s1")
        self.assertNotIn("401", str(e.exception))
        self.assertNotIn("refused", str(e.exception), str(e.exception))
        self.assertIn("sign in with Google", str(e.exception))


class AppDecodesTheHub(AppCase):
    """Every key the app's HomeSettings decoder names is in the hub's answer (an unknown key is ignored; a missing
    required one would make the app drop the whole answer)."""

    def test_home_settings_has_the_app_fields(self):
        with home_fixture.use(PluginTokenCallReachesTheHub.SAM):
            s = self.client.get("/api/home-settings").json()
        for key in ("person", "locale", "proactivity", "plan", "plan_owner", "connectors", "host", "mail",
                    "calendars", "google"):
            self.assertIn(key, s)
        self.assertEqual(set(s["person"]) >= {"name", "first_name", "timezone", "quiet_hours"}, True)
        self.assertEqual(set(s["person"]["quiet_hours"]), {"start", "end"})
        self.assertEqual(set(s["locale"]), {"units", "spelling", "clock"})
        self.assertIn("enabled", s["proactivity"])
        self.assertEqual(set(s["host"]) >= {"kind", "name"}, True)
        self.assertIn("client_id", s["google"])
        self.assertEqual((s["plan"], s["plan_owner"]), ("shared", "Jordan"))
        if HOME_SETTINGS.exists():
            swift = HOME_SETTINGS.read_text()
            for field in ("plan_owner", "client_id", "quiet_hours", "first_name", "connectors"):
                self.assertIn(f"var {field}:", swift)

    def test_colin_home_settings_are_his(self):
        with home_fixture.use(None):
            s = self.client.get("/api/home-settings").json()
        self.assertEqual((s["plan"], s["plan_owner"], s["connectors"]), ("own", "", "claude"))
        self.assertEqual(s["person"]["timezone"], "America/New_York")
        self.assertEqual(s["person"]["quiet_hours"], {"start": 23, "end": 7})
        self.assertEqual(len(s["mail"]["accounts"]), 3)

    def test_templates_name_the_person(self):
        tpl = REPO / "alien" / "shared" / "zip-templates.json"
        if not tpl.exists():
            self.skipTest("zip-templates.json is not in this tree")
        from taffy_hub import app as app_mod
        with home_fixture.use(PluginTokenCallReachesTheHub.SAM), mock.patch.object(app_mod, "TEMPLATES", tpl):
            out = self.client.get("/api/templates").json()["templates"]
        self.assertGreater(len(out), 5)
        text = json.dumps(out)
        self.assertNotIn("{name}", text)
        self.assertNotIn("Colin", text)
        by_id = {t["id"]: t for t in out}
        self.assertTrue(by_id["inbox"]["available"])     # mail can be added in the app
        self.assertTrue(by_id["errands"]["available"])


class ProtocolStrings(unittest.TestCase):
    """What the plugin writes, the hub and the app parse (plan section 9, "Protocol strings")."""

    SAM = {"home_id": "sam", "person": {"name": "Sam Rivera", "first_name": "Sam", "pronoun_free": True},
           "plan": {"connectors": "none"}}

    def test_card_messages_end_in_the_request_number_for_any_home(self):
        for settings in (None, self.SAM):
            with tt.home.pinned(settings):
                for template in ("A card asking Colin to allow it is now in his app (request {req}). ",
                                 "A card showing what you read is in Colin's app (request {req}). "):
                    text = tt.home.say(template, req=42)
                    m = hermes._CARD_REQUEST.search(text)
                    self.assertIsNotNone(m, text)
                    self.assertEqual(m.group(1), "42")
                    if settings:
                        self.assertNotIn("Colin", text)

    def test_mac_start_asks_for_the_person_words_by_a_neutral_name(self):
        with tt.home.pinned(None):
            self.assertEqual(tt.mac.schema("mac_start"), tt.mac.SCHEMAS["mac_start"])   # Colin's, unchanged
        with tt.home.pinned(self.SAM):
            params = tt.mac.schema("mac_start")["parameters"]
        self.assertIn("person_said", params["properties"])
        self.assertNotIn("colin_said", json.dumps(params))
        self.assertIn("person_said", params["required"])

    def test_test_mode_note_keeps_its_prefix(self):
        for settings in (None, self.SAM):
            with tt.home.pinned(settings):
                note = tt.home.personalize(tt.gate.TEST_NOTE)
            self.assertTrue(note.startswith("[Test mode:"), note)

    def test_call_request_and_reactions_markers_match_the_app(self):
        self.assertIn(voice.CALL_REQUEST_MARKER, history.CALL_REQUEST_MARKERS)
        self.assertIn("Colin's request: ", history.CALL_REQUEST_MARKERS)   # the old form, for one release
        if not APP_STATE.exists():
            self.skipTest("the app is not in this tree")
        swift = APP_STATE.read_text()
        for marker in history.CALL_REQUEST_MARKERS:
            self.assertIn(json.dumps(marker), swift)
        self.assertIn(json.dumps(history.REACTIONS_PREFIX), swift)
        self.assertIn('"[From a voice call]"', swift)
        self.assertIn('"[Hub note"', swift)



class NewHomeBeforeItsBoard(unittest.TestCase):
    def test_team_load_is_empty_until_the_gateway_makes_the_board(self):
        """Live e2e, Oct 9: a fresh home's /api/bots answered 500 until Hermes created kanban.db."""
        from taffy_hub import board
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "KANBAN_DB", Path(d) / "kanban.db"):
            self.assertEqual(board.bot_load(), {})


if __name__ == "__main__":
    unittest.main()
