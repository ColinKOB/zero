"""The front door with a fake home maker: no command runs and nothing reaches a live hub.

Run from alien/frontdoor:  python -m unittest discover -s tests
"""

import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from zero_frontdoor import app as appmod
from zero_frontdoor import homes, plans, store


class FakeHomes:
    def __init__(self):
        self.registry_homes = {"colin": {"owner": True, "ports": {"hub": 8650}}}
        self.created: list[str] = []
        self.tokens: list[tuple[str, str]] = []
        self.fail_create = False
        self.room = True
        self.gate = threading.Event()
        self.gate.set()

    def registry(self):
        return {"homes": self.registry_homes}

    def new_home_id(self, username, taken):
        lh = homes.LiveHomes.__new__(homes.LiveHomes)
        lh.prefix = "qa"
        lh.registry = self.registry
        return lh.new_home_id(username, taken)

    def has_room(self):
        return self.room

    def create(self, home_id, person, timezone, step=lambda s: None):
        step("user")
        self.gate.wait(5)
        step("home")
        if self.fail_create:
            raise homes.HomeError("boom")
        self.registry_homes[home_id] = {"ports": {"hub": 9000}}
        self.created.append(home_id)
        step("done")

    def device_token(self, home_id, device_name):
        self.tokens.append((home_id, device_name))
        return f"https://example.test/{home_id}", f"token-{home_id}-{len(self.tokens)}"


def wait_for(client, job, state, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = client.get(f"/api/sign-up/{job}").json()
        if r["state"] == state:
            return r
        time.sleep(0.02)
    raise AssertionError(f"job never reached {state}: {r}")


class FrontDoorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(Path(self.tmp.name) / "accounts.db")
        self.homes = FakeHomes()
        appmod.HOMES = self.homes
        appmod.SIGN_UP = "open"
        appmod._fails_user.clear()
        appmod._fails_all.clear()
        appmod._sign_ups.clear()
        self.client = TestClient(appmod.app)

    def tearDown(self):
        self.tmp.cleanup()

    def sign_up(self, username="Sam.Rivera", password="correct horse 1", **kw):
        body = {"username": username, "password": password, "name": "Sam Rivera", "timezone": "America/Chicago"}
        body.update(kw)
        return self.client.post("/api/sign-up", json=body)


class SignUpTests(FrontDoorCase):
    def test_sign_up_makes_a_home_then_sign_in_returns_server_and_token(self):
        r = self.sign_up()
        self.assertEqual(r.status_code, 202, r.text)
        wait_for(self.client, r.json()["job"], "ready")
        self.assertEqual(self.homes.created, ["qa-samrivera"])
        acct = store.get("sam.rivera")
        self.assertEqual(acct["home_id"], "qa-samrivera")
        self.assertEqual(acct["state"], "ready")
        self.assertEqual(acct["plan"], "free")
        r = self.client.post("/api/sign-in", json={"username": "SAM.rivera", "password": "correct horse 1",
                                                   "device_name": "Sam's iPhone"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["server"], "https://example.test/qa-samrivera")
        self.assertTrue(r.json()["token"].startswith("token-qa-samrivera"))
        self.assertEqual(r.json()["plan"], "free")
        self.assertEqual(self.homes.tokens, [("qa-samrivera", "Sam's iPhone")])

    def test_password_is_stored_as_scrypt_never_plain(self):
        r = self.sign_up(password="plain text secret")
        wait_for(self.client, r.json()["job"], "ready")
        raw = (Path(self.tmp.name) / "accounts.db").read_bytes()
        self.assertNotIn(b"plain text secret", raw)
        self.assertTrue(store.get("sam.rivera")["pw_hash"].startswith("scrypt$32768$8$1$"))

    def test_taken_username_and_bad_input(self):
        wait_for(self.client, self.sign_up().json()["job"], "ready")
        self.assertEqual(self.sign_up(password="another one 2").status_code, 409)
        self.assertEqual(self.sign_up(username="x").status_code, 400)
        self.assertEqual(self.sign_up(username="newperson", password="short").status_code, 400)
        self.assertEqual(self.sign_up(username="newperson", password="NewPerson").status_code, 400)

    def test_second_home_with_the_same_slug_gets_a_number(self):
        wait_for(self.client, self.sign_up(username="sam").json()["job"], "ready")
        wait_for(self.client, self.sign_up(username="s.a.m").json()["job"], "ready")
        self.assertEqual(self.homes.created, ["qa-sam", "qa-sam2"])

    def test_sign_in_waits_while_the_home_is_being_made(self):
        self.homes.gate.clear()
        job = self.sign_up().json()["job"]
        r = self.client.post("/api/sign-in", json={"username": "sam.rivera", "password": "correct horse 1"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("still being set up", r.json()["detail"])
        self.homes.gate.set()
        wait_for(self.client, job, "ready")

    def test_a_failed_build_resumes_with_the_same_details(self):
        self.homes.fail_create = True
        job = self.sign_up().json()["job"]
        r = wait_for(self.client, job, "failed")
        self.assertIn("same username and password", r["message"])
        self.assertEqual(store.get("sam.rivera")["state"], "failed")
        self.assertEqual(self.sign_up(password="not the same 9").status_code, 409)
        self.homes.fail_create = False
        r = self.sign_up()
        self.assertEqual(r.status_code, 202)
        wait_for(self.client, r.json()["job"], "ready")
        self.assertEqual(store.get("sam.rivera")["home_id"], "qa-samrivera")

    def test_closed_full_and_rate_limited(self):
        appmod.SIGN_UP = "closed"
        self.assertEqual(self.sign_up().status_code, 403)
        appmod.SIGN_UP = "open"
        self.homes.room = False
        self.assertEqual(self.sign_up().status_code, 503)
        self.assertEqual(self.client.get("/api/health").json()["sign_up"], "full")
        self.homes.room = True
        for i in range(appmod.SIGN_UP_ALL[0]):
            self.assertEqual(self.sign_up(username=f"person{i}").status_code, 202)
        self.assertEqual(self.sign_up(username="onemore").status_code, 429)

    def test_closed_is_the_default_and_an_invite_lets_one_username_in(self):
        import importlib
        import os
        saved = os.environ.pop("ZERO_FRONTDOOR_SIGN_UP", None)
        try:
            fresh = importlib.reload(appmod)
            self.assertEqual(fresh.SIGN_UP, "closed")
        finally:
            if saved is not None:
                os.environ["ZERO_FRONTDOOR_SIGN_UP"] = saved
        fresh.HOMES = self.homes
        self.client = TestClient(fresh.app)
        self.assertEqual(self.client.get("/api/health").json()["sign_up"], "closed")
        self.assertEqual(self.sign_up(username="stranger").status_code, 403)
        store.add_invite("Riley.D", hours=1)
        r = self.sign_up(username="riley.d")
        self.assertEqual(r.status_code, 202)
        wait_for(self.client, r.json()["job"], "ready")
        self.assertFalse(store.invited("riley.d"))
        self.assertEqual(self.sign_up(username="stranger2").status_code, 403)
        store.add_invite("late", hours=-1)
        self.assertEqual(self.sign_up(username="late").status_code, 403)

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/api/sign-up/nope").status_code, 404)


class SignInTests(FrontDoorCase):
    def setUp(self):
        super().setUp()
        store.add("colin", "his own choice 1", "colin")

    def test_attached_home_signs_in(self):
        r = self.client.post("/api/sign-in", json={"username": "colin", "password": "his own choice 1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.homes.tokens[-1][0], "colin")

    def test_wrong_password_and_unknown_user_read_the_same(self):
        a = self.client.post("/api/sign-in", json={"username": "colin", "password": "nope nope"})
        b = self.client.post("/api/sign-in", json={"username": "nobody", "password": "nope nope"})
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(a.json(), b.json())
        self.assertEqual(self.homes.tokens, [])

    def test_rate_limit_per_username(self):
        for _ in range(appmod.SIGN_IN_PER_USER[0]):
            self.client.post("/api/sign-in", json={"username": "colin", "password": "wrong guess"})
        r = self.client.post("/api/sign-in", json={"username": "colin", "password": "his own choice 1"})
        self.assertEqual(r.status_code, 429)
        store.add("other", "other pass 22", "riley")
        r = self.client.post("/api/sign-in", json={"username": "other", "password": "other pass 22"})
        self.assertEqual(r.status_code, 200)

    def test_global_rate_limit(self):
        for i in range(appmod.SIGN_IN_ALL[0]):
            self.client.post("/api/sign-in", json={"username": f"guess{i}", "password": "wrong guess"})
        r = self.client.post("/api/sign-in", json={"username": "colin", "password": "his own choice 1"})
        self.assertEqual(r.status_code, 429)

    def test_no_password_or_token_in_logs_or_errors(self):
        with self.assertLogs("zero-frontdoor", level=logging.INFO) as logs:
            self.client.post("/api/sign-in", json={"username": "colin", "password": "wrong secret 77"})
            ok = self.client.post("/api/sign-in", json={"username": "colin", "password": "his own choice 1"})
        text = "\n".join(logs.output)
        self.assertNotIn("wrong secret 77", text)
        self.assertNotIn("his own choice 1", text)
        self.assertNotIn(ok.json()["token"], text)
        bad = self.client.post("/api/sign-in", json={"username": "colin", "password": 5, "extra": "zz secret"})
        self.assertNotIn("zz secret", bad.text)

    def test_a_home_that_cannot_pair_says_try_again(self):
        def broken(home_id, device_name):
            raise homes.HomeError("hub down")
        self.homes.device_token = broken
        r = self.client.post("/api/sign-in", json={"username": "colin", "password": "his own choice 1"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], appmod.TROUBLE_TEXT)


class PlanTests(FrontDoorCase):
    def test_get_and_set_plan(self):
        store.add("sam", "a fine password", "qa-sam")
        self.assertEqual(plans.get_plan("sam"), "free")
        self.assertEqual(plans.get_plan(store.get("sam")), "free")
        self.assertEqual(plans.set_plan("sam", "Paid"), "paid")
        self.assertEqual(plans.get_plan("sam"), "paid")
        with self.assertRaises(ValueError):
            plans.set_plan("sam", "no spaces allowed")
        with self.assertRaises(KeyError):
            plans.get_plan("nobody")
        with self.assertRaises(KeyError):
            plans.set_plan("nobody", "free")


class ToolingTests(unittest.TestCase):
    def test_pairing_output_is_parsed(self):
        out = ("\nPairing for Sam's home (valid for one hour):\n  Address: https://alien.example:6446\n"
               "  Code:    ABCD-2345\n  Link:    zero://pair?server=x&code=ABCD2345\n")
        self.assertEqual(homes._field(out, "Code"), "ABCD2345")
        self.assertEqual(homes._field(out, "Address"), "https://alien.example:6446")
        owner = "Pairing code: WXYZ-6789 (valid for 0.1 hours)\nServer: https://alien.example:6443\n"
        self.assertEqual(homes._field(owner, "Pairing code"), "WXYZ6789")
        self.assertEqual(homes._field(owner, "Server"), "https://alien.example:6443")

    def test_slug(self):
        self.assertEqual(homes.slug("Sam.Rivera"), "samrivera")
        self.assertEqual(homes.slug("averyveryverylongname"), "averyveryver")
        self.assertEqual(homes.slug("a"), "az")
        self.assertEqual(homes.slug("..."), "user")

    def test_home_ids_fit_the_home_rule(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "zero_home_lib", Path(__file__).resolve().parents[2] / "bin" / "zero-home-lib.py")
        lib = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lib)
        for hid in ("u-samrivera", "qa-sam2", "u-averyveryver"):
            self.assertTrue(lib.HOME_ID_RE.match(hid), hid)
            self.assertTrue(lib.ACCOUNT_HOME_RE.match(hid), hid)
        for hid in ("colin", "riley", "test"):
            self.assertTrue(lib.HOME_ID_RE.match(hid))
            self.assertFalse(lib.ACCOUNT_HOME_RE.match(hid))
        self.assertFalse(lib.HOME_ID_RE.match("u-"))
        self.assertFalse(lib.HOME_ID_RE.match("x-abc"))
        s = lib.new_home_settings("u-sam", "Sam Rivera", "America/Chicago",
                                  {"hub": 8653, "hermes_api": 8645, "serve_port": 6446, "preview_base": 18600,
                                   "docker_subnet_base": "172.33"}, owner_name="Colin", own_plan=True)
        self.assertEqual(s["plan"]["claude"]["mode"], "own")
        self.assertEqual(s["plan"]["chatgpt"]["mode"], "own")
        self.assertNotIn("owner_name", s["plan"])
        self.assertNotIn("broker", str(s["plan"]))


if __name__ == "__main__":
    unittest.main()
