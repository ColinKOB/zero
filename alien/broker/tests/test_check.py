"""Tests for zero-home-check's logic with fake probes: what counts as walled off, what fails, what is skipped."""

import importlib.util
import json
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / "bin"


def load(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


lib = load("zero_home_lib_c", BIN / "zero-home-lib.py")
check = load("zero_home_check_c", BIN / "zero-home-check")

REGISTRY = {"homes": {
    "colin": {"user": "jdoe", "owner": True, "plan": "own",
              "ports": {"hub": 8650, "hermes_api": 8642, "serve_port": 6443, "preview_base": 18200}},
    "test": {"user": "zero-test", "plan": "broker",
             "ports": {"hub": 8652, "hermes_api": 8644, "serve_port": 6445, "preview_base": 18500}},
    "riley": {"user": "zero-riley", "plan": "broker",
              "ports": {"hub": 8651, "hermes_api": 8643, "serve_port": 6444, "preview_base": 18300}},
}}
HOMES = {"jdoe": Path("/home/jdoe"), "zero-test": Path("/home/zero-test"),
         "zero-riley": Path("/home/zero-riley")}
UIDS = {"jdoe": 1000, "zero-test": 1002, "zero-riley": 1001}


class FakeProbes(check.Probes):
    def __init__(self):
        self.readable_paths = set()
        self.sockets = {}
        self.http = {}
        self.commands = {}
        self.procs = []
        self.labels = {}
        self.ran = []

    def readable(self, path):
        return True if str(path) in self.readable_paths else False

    def can_connect_unix(self, path):
        return self.sockets.get(str(path), False)

    def http_status(self, url):
        return self.http.get(url, 401)

    def run(self, cmd, *, input=None, timeout=120, env=None):
        self.ran.append(cmd)
        for prefix, result in self.commands.items():
            if " ".join(cmd).startswith(prefix):
                return result
        return 0, ""

    def processes(self):
        return self.procs

    def apparmor_label(self, pid):
        return self.labels.get(pid, "unconfined")

    def mem_available_mb(self):
        return 6000

    def home_dir(self, user):
        return HOMES.get(user)

    def uid(self, user):
        return UIDS.get(user)

    def host_addresses(self):
        return {"lan": "10.0.0.20", "router": "10.0.0.1", "tailnet": "100.64.0.10"}


def good_probes():
    p = FakeProbes()
    p.http["http://127.0.0.1:8652/api/health"] = 200
    targets = check.container_targets_for("test", REGISTRY, settings(), p)
    p.commands["docker run"] = (0, "\n".join(f"SHUT {hp}" for hp in targets.values()))
    p.commands["codex exec"] = (126, "codex exec is not available on a shared plan")
    p.commands["claude mcp"] = (126, "Claude's connector and MCP settings are not available on a shared plan.")
    p.commands["claude auth"] = (0, json.dumps({"loggedIn": True, "authMethod": "claude.ai", "sharedPlan": True}))
    p.commands["claude -p"] = (0, json.dumps({"result": "NONE"}))
    return p


def settings():
    return lib.new_home_settings("test", "Sam Rivera", "America/Chicago",
                                 {"hub": 8652, "hermes_api": 8644, "serve_port": 6445, "preview_base": 18500,
                                  "docker_subnet_base": "172.32"})


def statuses(results):
    return {r.name: r.status for r in results}


class AsHome(unittest.TestCase):
    def test_walled_off_home_passes(self):
        res = check.checks_as_home("test", REGISTRY, settings(), good_probes())
        failed = [r for r in res if r.status == "fail" and "AppArmor profile installed" not in r.name]
        self.assertEqual(failed, [], failed)
        names = statuses(res)
        self.assertEqual(names["owner: ~/.claude/.credentials.json"], "pass")
        self.assertEqual(names["riley: ~/.taffy"], "pass")
        self.assertEqual(names["riley: hub without a token"], "pass")
        self.assertEqual(names["no claude.ai connector through the broker"], "pass")
        self.assertEqual(names["container cannot reach the host on the tailnet"], "pass")
        self.assertEqual(names["this home's Chromium runs under zero-chromium"], "skip")

    def test_readable_neighbour_fails(self):
        p = good_probes()
        p.readable_paths.add("/home/zero-riley/.taffy")
        p.readable_paths.add("/home/jdoe/.codex/auth.json")
        names = statuses(check.checks_as_home("test", REGISTRY, settings(), p))
        self.assertEqual(names["riley: ~/.taffy"], "fail")
        self.assertEqual(names["owner: ~/.codex/auth.json"], "fail")

    def test_open_hub_and_socket_fail(self):
        p = good_probes()
        p.http["http://127.0.0.1:8651/api/bots"] = 200
        p.sockets["/run/user/1000/docker.sock"] = True
        names = statuses(check.checks_as_home("test", REGISTRY, settings(), p))
        self.assertEqual(names["riley: hub without a token"], "fail")
        self.assertEqual(names["colin: Docker socket"], "fail")

    def test_container_that_reaches_out_fails(self):
        p = good_probes()
        p.commands["docker run"] = (0, "OPEN 10.0.2.2:8650\nSHUT 10.0.0.20:22")
        names = statuses(check.checks_as_home("test", REGISTRY, settings(), p))
        self.assertEqual(names["container cannot reach owner's hub on the host loopback"], "fail")
        self.assertEqual(names["container cannot reach the host on the LAN (ssh)"], "pass")

    def test_connector_through_broker_fails(self):
        p = good_probes()
        p.commands["claude -p"] = (0, json.dumps({"result": "mcp__claude_ai_Gmail__search\nRead"}))
        names = statuses(check.checks_as_home("test", REGISTRY, settings(), p))
        self.assertEqual(names["no claude.ai connector through the broker"], "fail")

    def test_owner_email_through_broker_fails(self):
        p = good_probes()
        self.assertEqual(statuses(check.checks_as_home("test", REGISTRY, settings(), p))[
            "no owner email reaches a run through the broker"], "pass")
        p.commands["claude -p"] = (0, json.dumps({"result": "NONE\nEMAILS: owner.person@example.com"}))
        res = check.checks_as_home("test", REGISTRY, settings(), p)
        names = statuses(res)
        self.assertEqual(names["no owner email reaches a run through the broker"], "fail")
        self.assertEqual(names["no claude.ai connector through the broker"], "pass")
        self.assertNotIn("owner.person@example.com", json.dumps([r.detail for r in res]))

    def test_broker_that_runs_codex_exec_fails(self):
        p = good_probes()
        p.commands["codex exec"] = (0, "hi")
        p.commands["claude auth"] = (0, json.dumps({"loggedIn": True, "email": "owner@example.com"}))
        names = statuses(check.checks_as_home("test", REGISTRY, settings(), p))
        self.assertEqual(names["broker refuses codex exec"], "fail")
        self.assertEqual(names["shared Claude plan is signed in, owner's account hidden"], "fail")

    def test_no_model_calls(self):
        p = good_probes()
        res = check.checks_as_home("test", REGISTRY, settings(), p, model_calls=False)
        self.assertEqual(statuses(res)["no claude.ai connector through the broker"], "skip")
        self.assertEqual(statuses(res)["no owner email reaches a run through the broker"], "skip")
        self.assertFalse(any(cmd[:2] == ["claude", "-p"] for cmd in p.ran))

    def test_browser_label(self):
        p = good_probes()
        p.procs = [(4242, "/home/zero-test/.taffy/tools/chromium-1208/chrome-linux64/chrome")]
        p.labels[4242] = "zero-chromium (unconfined)"
        self.assertEqual(statuses(check.checks_as_home("test", REGISTRY, settings(), p))
                         ["this home's Chromium runs under zero-chromium"], "pass")
        p.labels[4242] = "unconfined"
        self.assertEqual(statuses(check.checks_as_home("test", REGISTRY, settings(), p))
                         ["this home's Chromium runs under zero-chromium"], "fail")

    def test_unknown_user_is_skipped(self):
        reg = {"homes": dict(REGISTRY["homes"], ghost={"user": "zero-ghost", "plan": "broker", "ports": {}})}
        names = statuses(check.checks_as_home("test", reg, settings(), good_probes()))
        self.assertEqual(names["ghost: files"], "skip")

    def test_container_targets_cover_every_neighbour(self):
        t = check.container_targets_for("test", REGISTRY, settings(), FakeProbes())
        self.assertIn("10.0.2.2:18300", t.values())
        self.assertIn("10.0.2.2:18200", t.values())
        self.assertIn("100.64.0.10:6445", t.values())
        self.assertNotIn("10.0.2.2:18500", t.values())      # this home's own previews are its own


class AsOwner(unittest.TestCase):
    def test_owner_checks(self):
        import tempfile
        state = Path(tempfile.mkdtemp())
        hd = state / "homes" / "test"
        (hd / "claude").mkdir(parents=True)
        (hd / "codex").mkdir()
        (hd / "claude" / ".credentials.json").symlink_to("/nowhere/creds.json")
        (hd / "codex" / "auth.json").write_text("{}")
        p = good_probes()
        names = statuses(check.checks_as_owner("test", REGISTRY, settings(), p, state))
        self.assertEqual(names["owner cannot read test's ~/.taffy"], "pass")
        self.assertEqual(names["test's hub without a token"], "pass")
        self.assertEqual(names["broker: test's Claude sign-in is linked to the owner's"], "pass")
        self.assertEqual(names["broker: test's Codex sign-in is linked to the owner's"], "fail")


class HomeId(unittest.TestCase):
    """Loose ends, Oct 9: a home.json without home_id reads as a nameless home that is not Colin's; the check fails it."""

    def test_the_file_must_name_this_home(self):
        import tempfile
        f = Path(tempfile.mkdtemp()) / "home.json"
        self.assertEqual(check.home_id_check("test", f).status, "fail")            # no file
        f.write_text(json.dumps({"version": 1, "person": {"name": "Tess"}}))
        r = check.home_id_check("test", f)
        self.assertEqual((r.status, r.detail), ("fail", f"{f} has no home_id"))
        f.write_text(json.dumps({"home_id": "riley"}))
        self.assertIn("'riley'", check.home_id_check("test", f).detail)
        f.write_text(json.dumps({"home_id": "test"}))
        self.assertEqual(check.home_id_check("test", f).status, "pass")

    def test_a_file_without_home_id_is_not_the_owners(self):
        import tempfile
        f = Path(tempfile.mkdtemp()) / "home.json"
        f.write_text(json.dumps({"version": 1}))
        s = lib.load_home(f)
        self.assertEqual((s["home_id"], s["plan"]["connectors"], s["judge"]["enabled"]), ("unnamed", "none", False))
        self.assertEqual(lib.load_home(None)["home_id"], "colin")


if __name__ == "__main__":
    unittest.main()
