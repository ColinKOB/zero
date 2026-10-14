"""Tests for zero-home-render and zero-home-lib: golden files for a test home, idempotent re-renders, the
owner check mode, port allocation, the settable keys and the placeholder ChatGPT entry.

Reading back existing YAML needs PyYAML or ruamel.yaml (the Hermes venv has ruamel). Without one, the tests
that re-read a config are skipped. Regenerate the golden files with ZERO_UPDATE_GOLDEN=1.

Run: python3 -m unittest discover -s alien/broker/tests
"""

import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / "bin"
GOLDEN = Path(__file__).resolve().parent / "golden"


def load(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


lib = load("zero_home_lib_t", BIN / "zero-home-lib.py")
render = load("zero_home_render_t", BIN / "zero-home-render")


def have_yaml():
    try:
        lib.load_yaml("a: [1]")
        return True
    except RuntimeError:
        return False


TEST_PORTS = {"hub": 8652, "hermes_api": 8644, "serve_port": 6445, "preview_base": 18500, "preview_span": 60,
              "docker_subnet_base": "172.32", "broker_http": 8660}
SECRET = "golden-secret-not-real"


def test_settings():
    return lib.new_home_settings("test", "Sam Rivera", "America/Chicago", TEST_PORTS, first_name="Sam")


class Golden(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()) / "zero-test"
        self.home.mkdir()
        (self.home / ".taffy" / "profiles" / "inbox").mkdir(parents=True)
        (self.home / "taffy").mkdir()
        self.settings = test_settings()
        path = self.home / "taffy" / "home.json"
        path.write_text(json.dumps(self.settings, indent=2))

    def run_render(self, dry_run=False):
        ops = lib.Ops(dry_run=dry_run, quiet=True)
        render.render_home(lib.load_home(self.home / "taffy" / "home.json"), self.home, ops, broker_secret=SECRET)
        return ops

    def normalized(self, rel):
        text = (self.home / rel).read_text().replace(str(self.home), "/home/zero-test")
        text = re.sub(r"API_SERVER_KEY=\S+", "API_SERVER_KEY=<random>", text)
        text = re.sub(r'"last_refresh": "[^"]+"', '"last_refresh": "<now>"', text)
        return text

    FILES = [".taffy/config.yaml", ".taffy/profiles/inbox/config.yaml", ".taffy/.env", ".taffy/profiles/inbox/.env",
             ".taffy/SOUL.md", ".taffy/profile.yaml", ".taffy/auth.json", ".config/zero-broker/env",
             ".config/docker/daemon.json", "taffy/state/zero-slots.json",
             ".config/systemd/user/taffy-gateway.service", ".config/systemd/user/taffy-hub.service",
             ".config/systemd/user/taffy-backup.service", ".config/systemd/user/taffy-backup.timer",
             ".config/systemd/user/taffy-gateway.service.d/docker.conf",
             ".config/systemd/user/taffy-gateway.service.d/zero-broker.conf",
             ".config/systemd/user/docker.service.d/zip-sandbox.conf"]

    def test_golden_files(self):
        self.run_render()
        update = os.environ.get("ZERO_UPDATE_GOLDEN") == "1"
        for rel in self.FILES:
            golden = GOLDEN / "test-home" / rel
            got = self.normalized(rel)
            if update:
                golden.parent.mkdir(parents=True, exist_ok=True)
                golden.write_text(got)
                continue
            with self.subTest(file=rel):
                self.assertEqual(got, golden.read_text())

    def test_modes(self):
        self.run_render()
        for rel in (".taffy/.env", ".taffy/auth.json", ".config/zero-broker/env", ".taffy/config.yaml"):
            self.assertEqual((self.home / rel).stat().st_mode & 0o777, 0o600, rel)

    def test_no_owner_facts(self):
        self.run_render()
        for rel in self.FILES:
            text = (self.home / rel).read_text()
            for bad in ("Colin", "jdoe", "America/New_York", "tail0000", "/home/colin", " his ", " he "):
                self.assertNotIn(bad, text, f"{bad!r} in {rel}")

    @unittest.skipUnless(have_yaml(), "needs a YAML reader (run with the Hermes venv)")
    def test_a_cloned_zip_gets_its_own_slot(self):
        """team_add_bot clones a profile, then runs `zero-home-render HOME --zip ID` (integration): the clone's
        preview ports and network are replaced with the new Zip's own, and the other Zips stay as they were."""
        self.run_render()
        inbox = (self.home / ".taffy/profiles/inbox/config.yaml").read_text()
        errands = self.home / ".taffy/profiles/errands"
        errands.mkdir()
        (errands / "config.yaml").write_text(inbox)   # what `taffy profile create --clone-from inbox` leaves
        ops = lib.Ops(quiet=True)
        render.render_home(lib.load_home(self.home / "taffy" / "home.json"), self.home, ops, only_zip="errands",
                           broker_secret=SECRET)
        text = (errands / "config.yaml").read_text()
        self.assertNotIn("18510-18519", text)
        self.assertIn("zip-errands", text)
        self.assertNotIn("zip-inbox", text)
        self.assertRegex(text, r"127\.0\.0\.1:185[2-5]0-185[2-5]9:")
        self.assertEqual((self.home / ".taffy/profiles/inbox/config.yaml").read_text(), inbox)

    def test_ports_and_networks(self):
        self.run_render()
        text = (self.home / ".taffy/profiles/inbox/config.yaml").read_text()
        self.assertIn("127.0.0.1:18510-18519:18510-18519", text)
        self.assertIn("zip-inbox", text)
        lead = (self.home / ".taffy/config.yaml").read_text()
        self.assertIn("127.0.0.1:18500-18509:18500-18509", lead)
        self.assertIn("allow_private_urls: true", lead)
        self.assertIn("container_memory: 2048", lead)
        self.assertIn("--port 8652", (self.home / ".config/systemd/user/taffy-hub.service").read_text())
        self.assertIn("API_SERVER_PORT=8644", (self.home / ".taffy/.env").read_text())
        self.assertIn(f"HERMES_CODEX_BASE_URL=http://127.0.0.1:8660/test/{SECRET}",
                      (self.home / ".config/zero-broker/env").read_text())

    @unittest.skipUnless(have_yaml(), "needs a YAML reader (run with the Hermes venv)")
    def test_second_render_changes_nothing(self):
        self.run_render()
        key = (self.home / ".taffy/.env").read_text()
        ops = self.run_render()
        self.assertEqual(ops.changes, [])
        self.assertEqual((self.home / ".taffy/.env").read_text(), key)

    @unittest.skipUnless(have_yaml(), "needs a YAML reader (run with the Hermes venv)")
    def test_render_keeps_other_keys(self):
        self.run_render()
        path = self.home / ".taffy/profiles/inbox/config.yaml"
        data = lib.load_yaml(path.read_text())
        data["platform_toolsets"] = {"api_server": ["browser", "file"]}
        data["terminal"]["docker_forward_env"] = ["GH_TOKEN"]
        data["model"]["provider"] = "something-else"
        path.write_text(lib.emit_yaml(data))
        self.run_render()
        data = lib.load_yaml(path.read_text())
        self.assertEqual(data["platform_toolsets"], {"api_server": ["browser", "file"]})
        self.assertEqual(data["terminal"]["docker_forward_env"], ["GH_TOKEN"])
        self.assertEqual(data["model"]["provider"], "claude-subscription-directsdk-experimental")

    def test_chatgpt_brain(self):
        """A home signed in to ChatGPT only thinks with Codex in every Zip; without the key, Claude as before."""
        s = test_settings()
        self.assertEqual(lib.managed_config(s, self.home, "inbox", 0, False)["model"]["provider"],
                         "claude-subscription-directsdk-experimental")
        s["plan"]["brain"] = "chatgpt"
        for z in (lib.LEAD, "inbox"):
            cfg = lib.managed_config(s, self.home, z, 0, False)
            self.assertEqual(cfg["model"], {"provider": "openai-codex", "default": lib.CHATGPT_MODEL})
            self.assertEqual(cfg["fallback_providers"], [])
            self.assertEqual({v["provider"] for v in cfg["auxiliary"].values()}, {"openai-codex"})

    @unittest.skipUnless(have_yaml(), "needs a YAML reader (run with the Hermes venv)")
    def test_emitted_yaml_reads_back(self):
        self.run_render()
        for rel in (".taffy/config.yaml", ".taffy/profiles/inbox/config.yaml", ".taffy/profile.yaml"):
            data = lib.load_yaml((self.home / rel).read_text())
            self.assertIsInstance(data, dict)
        cfg = lib.load_yaml((self.home / ".taffy/config.yaml").read_text())
        self.assertEqual(cfg["tools"]["tool_search"]["enabled"], "off")
        self.assertEqual(cfg["terminal"]["docker_env"]["TZ"], "America/Chicago")

    def test_dry_run_changes_nothing(self):
        before = sorted(p for p in self.home.rglob("*"))
        ops = self.run_render(dry_run=True)
        self.assertTrue(ops.changes)
        self.assertEqual(sorted(p for p in self.home.rglob("*")), before)

    def test_existing_soul_is_never_overwritten(self):
        (self.home / ".taffy" / "SOUL.md").write_text("Sam's own words")
        self.run_render()
        self.assertEqual((self.home / ".taffy" / "SOUL.md").read_text(), "Sam's own words")


class OwnerCheck(unittest.TestCase):
    """--check colin compares the owner's live configs and changes nothing."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp()) / "jdoe"
        self.settings = lib.load_home(None)
        hermes = self.home / ".taffy"
        (hermes / "profiles").mkdir(parents=True)
        lead = lib.managed_config(self.settings, self.home, "default", None, True)
        (hermes / "config.yaml").write_text(lib.emit_yaml(lead))
        for z, slot in lib.OWNER_SLOTS.items():
            (hermes / "profiles" / z).mkdir()
            cfg = lib.managed_config(self.settings, self.home, z, slot, True)
            cfg["terminal"]["container_memory"] = 3072
            cfg["browser"]["allow_private_urls"] = z in ("builder", "clients")
            if z == "builder":
                cfg["terminal"]["docker_forward_env"] = ["GH_TOKEN"]
            (hermes / "profiles" / z / "config.yaml").write_text(lib.emit_yaml(cfg))

    @unittest.skipUnless(have_yaml(), "needs a YAML reader (run with the Hermes venv)")
    def test_matches(self):
        out = io.StringIO()
        sys_stdout, sys.stdout = sys.stdout, out
        try:
            code = render.check_owner(self.settings, self.home)
        finally:
            sys.stdout = sys_stdout
        self.assertEqual(code, 0, out.getvalue())

    @unittest.skipUnless(have_yaml(), "needs a YAML reader (run with the Hermes venv)")
    def test_reports_a_difference(self):
        path = self.home / ".taffy/profiles/school/config.yaml"
        data = lib.load_yaml(path.read_text())
        data["terminal"]["docker_extra_args"][1] = "127.0.0.1:18300-18309:18300-18309"
        path.write_text(lib.emit_yaml(data))
        out = io.StringIO()
        sys_stdout, sys.stdout = sys.stdout, out
        try:
            code = render.check_owner(self.settings, self.home)
        finally:
            sys.stdout = sys_stdout
        self.assertEqual(code, 1)
        self.assertIn("school: 1 difference", out.getvalue())

    def test_owner_layout_matches_alien_today(self):
        cfg = lib.managed_config(self.settings, self.home, "career", lib.OWNER_SLOTS["career"], True)
        self.assertEqual(cfg["terminal"]["docker_extra_args"],
                         ["-p", "127.0.0.1:18230-18239:18230-18239", "--network", "zip-career"])
        self.assertEqual(lib.subnet(self.settings, lib.OWNER_SLOTS["money"]), "172.30.4.0/24")
        lead = lib.managed_config(self.settings, self.home, "default", None, True)
        self.assertEqual(lead["terminal"], {"home_mode": "real"})
        self.assertNotIn("placement", lead["bot_desktop"])


class Allocation(unittest.TestCase):
    def test_presets(self):
        reg = {"version": 1, "homes": {"colin": lib.OWNER_ENTRY}}
        riley = lib.allocate_ports("riley", reg, set())
        self.assertEqual((riley["hub"], riley["hermes_api"], riley["serve_port"], riley["preview_base"],
                          riley["docker_subnet_base"]), (8651, 8643, 6444, 18300, "172.31"))
        reg["homes"]["riley"] = {"ports": riley}
        test = lib.allocate_ports("test", reg, {18421, 18422, 8650, 8642})
        self.assertEqual((test["hub"], test["hermes_api"], test["serve_port"], test["preview_base"],
                          test["docker_subnet_base"]), (8652, 8644, 6445, 18500, "172.32"))

    def test_test_first_keeps_rileys_ports(self):
        reg = {"version": 1, "homes": {"colin": lib.OWNER_ENTRY}}
        reg["homes"]["test"] = {"ports": lib.allocate_ports("test", reg, {18421})}
        riley = lib.allocate_ports("riley", reg, {18421})
        self.assertEqual((riley["hub"], riley["preview_base"]), (8651, 18300))

    def test_a_third_home_avoids_everything(self):
        reg = {"version": 1, "homes": {"colin": lib.OWNER_ENTRY}}
        for h in ("riley", "test"):
            reg["homes"][h] = {"ports": lib.allocate_ports(h, reg, {18421})}
        other = lib.allocate_ports("dana", reg, {8653, 18421})
        self.assertNotIn(other["hub"], (8650, 8651, 8652, 8653))
        self.assertLess(other["hermes_api"], 8650)
        spans = [(e["ports"]["preview_base"], e["ports"]["preview_base"] + 60) for e in reg["homes"].values()]
        for lo, hi in spans:
            self.assertFalse(lo < other["preview_base"] + 60 and other["preview_base"] < hi)


class Settings(unittest.TestCase):
    def test_new_home_shape(self):
        s = lib.new_home_settings("riley", "Riley Ellis", "America/New_York",
                                  lib.allocate_ports("riley", {"homes": {}}, set()),
                                  google_account="riley@example.com")
        self.assertEqual(s["plan"]["claude"], {"mode": "broker", "command": "/srv/zero/broker/claude-client"})
        self.assertEqual(s["plan"]["chatgpt"]["codex_base_url"], "http://127.0.0.1:8660/riley")
        self.assertEqual(s["plan"]["connectors"], "none")
        self.assertEqual(s["network"]["public_url"], "https://alien.tail0000.ts.net:6444")
        self.assertEqual(s["backup"]["drop"], "/srv/zero/backups/riley")
        self.assertEqual(s["google"], {"client_id": lib.ZERO_GOOGLE_CLIENT_ID,
                                       "expected_account": "riley@example.com"})
        self.assertTrue(s["plan"]["owner_name"])

    def test_stranger_file_never_inherits_colin(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "home.json"
            f.write_text(json.dumps({"home_id": "dana", "plan": {"claude": {"mode": "broker"}}}))
            s = lib.load_home(f)
        self.assertEqual(s["person"]["name"], "")
        self.assertTrue(s["person"]["pronoun_free"])
        self.assertEqual(s["plan"]["connectors"], "none")
        self.assertFalse(s["judge"]["enabled"])
        self.assertEqual(s["host"]["lead_backend"], "docker")
        self.assertFalse(s["backup"]["mac_copy_expected"])
        self.assertEqual(lib.load_home(None)["person"]["first_name"], "Colin")
        self.assertEqual(s["mail"]["accounts"], [])
        self.assertEqual(set(s) - set(lib.DEFAULTS), set())

    def test_set_value(self):
        s = {"home_id": "test"}
        s = lib.set_value(s, "google.client_id", "123456789012-abcdefghijklmnop.apps.googleusercontent.com")
        self.assertEqual(s["google"]["client_id"], "123456789012-abcdefghijklmnop.apps.googleusercontent.com")
        for key, value in (("google.client_secret", "x"), ("plan.claude.mode", "own"),
                           ("google.client_id", "GOCSPX-secret"), ("person.timezone", "../../etc")):
            with self.assertRaises(ValueError):
                lib.set_value(s, key, value)

    def test_placeholder_auth(self):
        first = lib.codex_placeholder_auth(None)
        self.assertEqual(first["providers"]["openai-codex"]["tokens"]["access_token"], lib.PLACEHOLDER_TOKEN)
        self.assertIsNone(lib.codex_placeholder_auth(first))
        self.assertIsNone(lib.codex_placeholder_auth({"credential_pool": {"openai-codex": [{"id": "x"}]}}))

    def test_env_keeps_existing_lines(self):
        s = test_settings()
        out = lib.render_env(s, "# mine\nAPI_SERVER_KEY=keep\nOPENAI_API_KEY=x\nAPI_SERVER_PORT=1\n", lead=True,
                             broker_secret="abc")
        self.assertIn("API_SERVER_KEY=keep", out)
        self.assertIn("OPENAI_API_KEY=x", out)
        self.assertIn("API_SERVER_PORT=8644", out)
        self.assertNotIn("API_SERVER_PORT=1\n", out)
        self.assertIn("HERMES_CODEX_BASE_URL=http://127.0.0.1:8660/test/abc", out)

    def test_defaults_are_todays_alien(self):
        d = lib.load_home(None)
        self.assertEqual((d["ports"]["hub"], d["ports"]["hermes_api"], d["network"]["serve_port"]), (8650, 8642, 6443))
        self.assertEqual(d["plan"]["claude"]["mode"], "own")
        self.assertEqual(d["host"]["lead_backend"], "local")


class SetCommand(unittest.TestCase):
    def test_set_writes_home_json(self):
        home = Path(tempfile.mkdtemp())
        (home / "taffy").mkdir()
        (home / "taffy" / "home.json").write_text(json.dumps(test_settings()))
        old = os.environ.pop("ZERO_HOME", None)
        try:
            render.cmd_set(["test", "google.client_id", "123456789012-abcdefghijklmnop.apps.googleusercontent.com",
                            "--home-dir", str(home)])
        finally:
            if old:
                os.environ["ZERO_HOME"] = old
        data = json.loads((home / "taffy" / "home.json").read_text())
        self.assertTrue(data["google"]["client_id"].endswith(".apps.googleusercontent.com"))
        with self.assertRaises(SystemExit):
            render.cmd_set(["riley", "google.client_id", "123456789012-abcdefghijklmnop.apps.googleusercontent.com",
                            "--home-dir", str(home)])


if __name__ == "__main__":
    unittest.main()
