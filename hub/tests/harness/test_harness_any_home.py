"""Home-agnostic checks the hub suite runs for whichever home is active (matrix.py sets ZERO_HOME per shape), and the
harness's own tests: the sample homes, the Colin-fact scan, the fake Hermes, the mail seed, the isolation probe and a
headless end-to-end run against the fixture hub.

Run: cd hub && python3 -m pytest -q tests/harness     (or python3 tests/harness/matrix.py for every shape)

The reader checks adapt to the hub's and plugin's `home.py` (WP1, WP2) once they exist and skip with a reason before.
"""

import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HUB = HERE.parents[1]
REPO = HUB.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HUB))

import fake_hermes  # noqa: E402
import home_checks  # noqa: E402
import homes  # noqa: E402
import isolation_probe  # noqa: E402
from greenmail import seed  # noqa: E402

PLUGIN_HOME = REPO / "alien" / "plugins" / "taffy-tools" / "home.py"
COMPARED = ("person.name", "person.timezone", "person.quiet_hours.start", "person.quiet_hours.end", "ports.hub",
            "ports.hermes_api", "plan.claude.mode", "plan.chatgpt.mode", "plan.connectors", "judge.enabled",
            "network.public_url", "host.lead_backend")


def dig(d, dotted):
    for part in dotted.split("."):
        if isinstance(d, dict):
            d = d.get(part)
        else:
            d = getattr(d, part, None)
        if d is None:
            return None
    return d


def as_dict(obj):
    if obj is None or isinstance(obj, dict):
        return obj
    for name in ("to_dict", "as_dict", "dict", "model_dump"):
        if callable(getattr(obj, name, None)):
            return getattr(obj, name)()
    if hasattr(obj, "__dataclass_fields__"):
        import dataclasses
        return dataclasses.asdict(obj)
    return vars(obj)


def read_with(module, path: Path | None):
    """The settings a home.py reader gives for `path` (None: no file). WP1's and WP2's readers both read ZERO_HOME at
    call time with a cache keyed on the file, so this points ZERO_HOME at the file and calls load(); a reader that
    takes the path as an argument is tried too. None when the module has no reader this recognizes."""
    old = os.environ.get("ZERO_HOME")
    os.environ["ZERO_HOME"] = str(path) if path else str(Path(tempfile.mkdtemp()) / "absent.json")
    try:
        if callable(getattr(module, "reset_cache", None)):
            module.reset_cache()
        for fn in ("load", "settings", "current", "read", "load_home"):
            f = getattr(module, fn, None)
            if not callable(f):
                continue
            try:
                return as_dict(f())
            except TypeError:
                try:
                    return as_dict(f(path))
                except TypeError:
                    continue
        return None
    finally:
        if old is None:
            os.environ.pop("ZERO_HOME", None)
        else:
            os.environ["ZERO_HOME"] = old
        if callable(getattr(module, "reset_cache", None)):
            module.reset_cache()


def load_plugin_home():
    spec = importlib.util.spec_from_file_location("zero_e2e_plugin_home", PLUGIN_HOME)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zero_e2e_plugin_home"] = mod
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ActiveHome(unittest.TestCase):
    """Whatever home this run has (matrix.py sets ZERO_HOME): its file must keep the contract."""

    def test_active_home_json_keeps_the_contract(self):
        path = os.environ.get("ZERO_HOME")
        if not path:
            self.skipTest("no ZERO_HOME in this run (the defaults shape)")
        data = json.loads(Path(path).read_text())
        self.assertEqual(home_checks.validate_home(data), [])


class Shapes(unittest.TestCase):
    def test_every_shape_keeps_the_contract(self):
        for name in ("colin", "riley", "test"):
            with self.subTest(name):
                self.assertEqual(home_checks.validate_home(homes.shape(name)), [])

    def test_hermetic_homes_stay_inside_their_folder(self):
        with tempfile.TemporaryDirectory() as d:
            _, path = homes.make_home("riley", Path(d))
            blob = path.read_text()
            self.assertNotIn("~/", blob)
            for v in json.loads(blob)["paths"].values():
                for p in v if isinstance(v, list) else [v]:
                    self.assertTrue(str(p).startswith(str(Path(d))), p)

    def test_validate_catches_what_the_plan_forbids(self):
        bad = homes.shape("riley")
        bad["plan"]["connectors"] = "claude"
        bad["mail"]["accounts"][0]["password"] = "hunter22"
        bad["person"]["timezone"] = "Mars/Olympus"
        problems = " ".join(home_checks.validate_home(bad))
        self.assertIn("connectors", problems)
        self.assertIn("secret", problems)
        self.assertIn("time zone", problems)


class ColinFacts(unittest.TestCase):
    def test_finds_colin_in_a_strangers_home(self):
        found = home_checks.scan({"prompt": "Tell Colin when his Lakeview class moves."}, homes.shape("test"))
        labels = {f["label"] for f in found}
        self.assertIn("Colin's name", labels)
        self.assertIn("Colin's school", labels)
        self.assertIn("a pronoun for the person", labels)

    def test_colins_own_home_is_never_flagged(self):
        self.assertEqual(home_checks.scan({"x": "Colin O'Brien, Lakeview"}, homes.shape("colin")), [])
        self.assertEqual(home_checks.scan({"x": "Colin"}, None), [])

    def test_riley_may_say_her_own_surname(self):
        found = home_checks.scan({"x": "Riley Ellis's Inbox"}, homes.shape("riley"))
        self.assertEqual([f for f in found if f["severity"] == "fail"], [])

    def test_excerpts_hide_tokens(self):
        found = home_checks.scan({"x": "Colin key sk-abcdefghijklmnopqrstuvwxyz123456"}, homes.shape("test"))
        self.assertNotIn("sk-abcdefghijklmnop", json.dumps(found))


class Readers(unittest.TestCase):
    """The hub's and the plugin's home.py read the same file the same way (plan section 4)."""

    def hub_reader(self):
        try:
            return importlib.import_module("taffy_hub.home")
        except ImportError:
            self.skipTest("taffy_hub/home.py does not exist yet (WP1)")

    def test_hub_reader_gives_todays_defaults_without_a_file(self):
        mod = self.hub_reader()
        data = read_with(mod, None)
        if data is None:
            self.skipTest("taffy_hub.home has no reader shape this harness recognizes; see read_with()")
        self.assertEqual(dig(data, "ports.hub"), 8650)
        self.assertEqual(dig(data, "ports.hermes_api"), 8642)
        self.assertEqual(dig(data, "person.timezone"), "America/New_York")

    def test_readers_agree_on_every_shape(self):
        hub = self.hub_reader()
        if not PLUGIN_HOME.exists():
            self.skipTest("alien/plugins/taffy-tools/home.py does not exist yet (WP2)")
        plugin = load_plugin_home()
        for name in ("colin", "riley", "test"):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                _, path = homes.make_home(name, Path(d))
                a, b = read_with(hub, path), read_with(plugin, path)
                if a is None or b is None:
                    self.skipTest("a reader has no shape this harness recognizes; see read_with()")
                for key in COMPARED:
                    self.assertEqual(dig(a, key), dig(b, key), f"{name}: {key}")
                addr = lambda d: sorted(x.get("address") for x in (dig(d, "mail.accounts") or []))
                self.assertEqual(addr(a), addr(b), f"{name}: mail.accounts")


class FakeHermesZips(unittest.TestCase):
    def test_the_lead_makes_picked_zips_like_team_add_bot(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = homes.make_home("test", Path(d))
            (root / ".taffy" / "memories" / "USER.md").write_text("<!-- onboarding:start -->\nSam\n<!-- onboarding:end -->")
            fake = fake_hermes.FakeHermes(root)
            reply = fake.reply("default", "Please make me these Zips from their templates: Inbox (the inbox "
                                          "template), Errands (the errands template).")
            self.assertIn("Inbox and Errands", reply)
            reg = json.loads((root / "taffy" / "hub-state" / "bots.json").read_text())
            self.assertEqual([e["id"] for e in reg], ["inbox", "errands"])
            for z in ("inbox", "errands"):
                self.assertIn("Sam", (root / ".taffy" / "profiles" / z / "memories" / "USER.md").read_text())
                self.assertTrue(fake.key(z))
            self.assertIn(seed.subjects()[0], fake.reply("inbox", "What is in my inbox?"))


class MailSeed(unittest.TestCase):
    def test_messages_are_well_formed_and_owe_a_reply(self):
        msgs = [seed.build(m, "sam@zero.test") for m in seed.load()]
        self.assertEqual(len(msgs), 3)
        self.assertTrue(all(m["To"] == "sam@zero.test" and m["Subject"] for m in msgs))
        keys = {m["X-Zero-Seed"] for m in msgs}
        self.assertTrue({"reply_owed", "renewal"} <= keys)

    def test_seed_writes_eml_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(seed.main(["--eml-dir", d]), 0)
            self.assertEqual(len(list(Path(d).glob("*.eml"))), 3)


class IsolationProbe(unittest.TestCase):
    def test_probe_sees_a_leak_and_a_locked_home(self):
        if os.geteuid() == 0:
            self.skipTest("root reads mode-000 folders")
        self.assertEqual(isolation_probe.self_test(), 0)

    def test_probe_refuses_to_run_as_the_owner(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(isolation_probe.main(["--victim-home", d]), 2)


class HeadlessEndToEnd(unittest.TestCase):
    """The fixture hub (the real hub on a temporary home, fake Hermes), the app's calls, then the assertions."""

    def test_stranger_onboarding_against_the_fixture_hub(self):
        try:
            import fastapi, uvicorn  # noqa: F401
        except ImportError:
            self.skipTest("the hub's dependencies are not installed here")
        with tempfile.TemporaryDirectory() as d:
            home, state = Path(d) / "home", Path(d) / "state.json"
            port = free_port()
            proc = subprocess.Popen([sys.executable, str(HERE / "fixture_hub.py"), "--home", str(home), "--state",
                                     str(state), "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                deadline = time.time() + 30
                while time.time() < deadline and not state.exists():
                    time.sleep(0.2)
                self.assertTrue(state.exists(), "the fixture hub did not start")
                st = json.loads(state.read_text())
                for _ in range(50):
                    try:
                        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                        break
                    except OSError:
                        time.sleep(0.2)
                tok = Path(d) / "token"
                r = subprocess.run([sys.executable, str(HERE / "simulate_app.py"), "--hub", st["hub"], "--code",
                                    st["code"], "--token-out", str(tok)], capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                r = subprocess.run([sys.executable, str(HERE / "assert_state.py"), "--hub", st["hub"], "--token-file",
                                    str(tok), "--home-dir", st["home"], "--expect-mailbox", "--ask-inbox"],
                                   capture_output=True, text=True, timeout=180)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("PASS  zips_created", r.stdout)
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
