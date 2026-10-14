"""A home's own accounts (plan 2026-10-09, section 4, and Oct 9's Sign In with Google): the Google token exchange,
storage, refresh and revocation against a fake Google on loopback; mailboxes with an app password; CalDAV and iCal
calendars; the endpoints the app calls; the internal token endpoint for the plugin; and connectors "none".

Run: python3 -m unittest discover -s hub/tests (from the repo)
"""

import base64
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import calsources, config, google, home, mail, mailboxes  # noqa: E402
import home_fixture  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402

CLIENT = "000000000000-yourgoogleclientid.apps.googleusercontent.com"
VERIFIER = "v" * 64


def id_token(email: str) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({'alg': 'none'})}.{part({'email': email, 'email_verified': True})}.sig"


class FakeGoogle:
    """Google's token, revoke, Gmail and Calendar endpoints, on loopback."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.revoked: set[str] = set()
        self.refresh_ok = True
        self.give_refresh = True
        self.scope = " ".join(google.SCOPES)
        self.email = "sam.rivera@gmail.com"
        self.n = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                fake.calls.append((self.path, form))
                if self.path == "/token":
                    if form.get("grant_type") == "authorization_code":
                        if form.get("code") != "good-code" or form.get("client_id") != CLIENT:
                            return self._send(400, {"error": "invalid_grant"})
                        fake.n += 1
                        out = {"access_token": f"at-{fake.n}", "expires_in": 3599, "scope": fake.scope,
                               "token_type": "Bearer", "id_token": id_token(fake.email)}
                        if fake.give_refresh:
                            out["refresh_token"] = f"rt-{fake.n}"
                        return self._send(200, out)
                    if form.get("grant_type") == "refresh_token":
                        if not fake.refresh_ok or form.get("refresh_token") in fake.revoked:
                            return self._send(400, {"error": "invalid_grant",
                                                    "error_description": "Token has been expired or revoked."})
                        fake.n += 1
                        return self._send(200, {"access_token": f"at-{fake.n}", "expires_in": 3599,
                                                "scope": fake.scope, "token_type": "Bearer"})
                if self.path == "/revoke":
                    fake.revoked.add(form.get("token"))
                    return self._send(200, {})
                self._send(404, {})

            def do_GET(self):
                fake.calls.append((self.path, {"auth": self.headers.get("Authorization")}))
                if not (self.headers.get("Authorization") or "").startswith("Bearer at-"):
                    return self._send(401, {"error": "unauthenticated"})
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if u.path == "/gmail/v1/users/me/profile":
                    return self._send(200, {"emailAddress": fake.email})
                if u.path == "/gmail/v1/users/me/messages":
                    return self._send(200, {"messages": [{"id": "18f0a1"}], "q": q.get("q")})
                if u.path == "/gmail/v1/users/me/messages/18f0a1":
                    body = base64.urlsafe_b64encode(b"Can you send the form by Friday?").decode()
                    return self._send(200, {"id": "18f0a1", "labelIds": ["INBOX", "UNREAD"], "payload": {
                        "headers": [{"name": "From", "value": "Dana Reyes <dana@example.org>"},
                                    {"name": "To", "value": fake.email}, {"name": "Subject", "value": "The form"},
                                    {"name": "Date", "value": "Thu, 08 Oct 2026 10:00:00 -0400"}],
                        "mimeType": "multipart/alternative",
                        "parts": [{"mimeType": "text/plain", "body": {"data": body}}]}})
                if u.path == "/calendar/v3/calendars/primary/events":
                    return self._send(200, {"items": [{
                        "id": "ev1", "summary": "Dentist", "location": "12 Main St",
                        "start": {"dateTime": "2026-10-10T09:00:00-04:00"},
                        "end": {"dateTime": "2026-10-10T10:00:00-04:00"},
                        "attendees": [{"email": fake.email, "self": True, "responseStatus": "accepted"}]}]})
                self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GoogleCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGoogle()
        self.addCleanup(self.fake.close)
        saved = (google.TOKEN_URL, google.REVOKE_URL, google.GMAIL_API, google.CALENDAR_API, config.HUB_DIR)
        self.addCleanup(lambda: (setattr(google, "TOKEN_URL", saved[0]), setattr(google, "REVOKE_URL", saved[1]),
                                 setattr(google, "GMAIL_API", saved[2]), setattr(google, "CALENDAR_API", saved[3]),
                                 setattr(config, "HUB_DIR", saved[4])))
        google.TOKEN_URL, google.REVOKE_URL = f"{self.fake.base}/token", f"{self.fake.base}/revoke"
        google.GMAIL_API, google.CALENDAR_API = f"{self.fake.base}/gmail/v1", f"{self.fake.base}/calendar/v3"
        config.HUB_DIR = Path(tempfile.mkdtemp())
        home_fixture.use_in(self, "riley")


class Tokens(GoogleCase):
    def test_exchange_keeps_the_tokens_in_this_home_only(self):
        acct = google.exchange("good-code", VERIFIER)
        self.assertEqual(acct["address"], "sam.rivera@gmail.com")
        self.assertEqual(acct["state"], google.CONNECTED)
        self.assertTrue(acct["mail"] and acct["calendar"])
        self.assertNotIn("rt-1", json.dumps(acct))
        f = config.HUB_DIR / "google" / "sam.rivera@gmail.com.json"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode), 0o700)
        self.assertEqual(json.loads(f.read_text())["refresh_token"], "rt-1")
        sent = self.fake.calls[0][1]
        self.assertEqual(sent["grant_type"], "authorization_code")
        self.assertEqual(sent["code_verifier"], VERIFIER)
        self.assertEqual(sent["client_id"], CLIENT)
        self.assertNotIn("client_secret", sent)
        self.assertEqual(sent["redirect_uri"],
                         "com.googleusercontent.apps.000000000000-yourgoogleclientid:/oauth2redirect")

    def test_a_401_after_a_disconnect_leaves_no_token_file(self):
        """Night review: api_get forced a refresh after a 401 by saving `_load(address) or {}` with expires_at 0, so an
        account disconnected in between came back as a file holding only expires_at, listed as an account."""
        from unittest import mock
        google.exchange("good-code", VERIFIER)

        class R:
            status_code = 401

        def get(self, url, params=None, headers=None):
            google.disconnect("sam.rivera@gmail.com")
            return R()
        with mock.patch("httpx.Client.get", get):
            with self.assertRaises(google.GoogleError):
                google.api_get("sam.rivera@gmail.com", f"{google.GMAIL_API}/users/me/profile")
        self.assertEqual(google.accounts(), [])

    def test_signing_in_again_replaces_the_tokens(self):
        google.exchange("good-code", VERIFIER)
        google.exchange("good-code", VERIFIER)
        data = json.loads((config.HUB_DIR / "google" / "sam.rivera@gmail.com.json").read_text())
        self.assertEqual(data["refresh_token"], "rt-2")

    def test_a_bad_code_or_verifier_is_a_plain_sentence(self):
        with self.assertRaises(google.GoogleError) as e:
            google.exchange("bad-code", VERIFIER)
        self.assertEqual(str(e.exception), "The sign-in didn't finish. Try Sign In with Google again.")
        with self.assertRaises(google.GoogleError):
            google.exchange("good-code", "short")
        self.assertEqual(google.accounts(), [])

    def test_no_refresh_token_is_not_kept(self):
        self.fake.give_refresh = False
        with self.assertRaises(google.GoogleError) as e:
            google.exchange("good-code", VERIFIER)
        self.assertIn("lasting access", str(e.exception))
        self.assertEqual(google.accounts(), [])

    def test_access_token_is_reused_then_refreshed_near_expiry(self):
        google.exchange("good-code", VERIFIER)
        self.assertEqual(google.access_token("sam.rivera@gmail.com")["access_token"], "at-1")
        f = config.HUB_DIR / "google" / "sam.rivera@gmail.com.json"
        data = json.loads(f.read_text())
        data["expires_at"] = time.time() + 30
        f.write_text(json.dumps(data))
        tok = google.access_token("sam.rivera@gmail.com")
        self.assertEqual(tok["access_token"], "at-2")
        self.assertGreater(tok["expires_at"], time.time() + 3000)
        refresh = [c for p, c in self.fake.calls if p == "/token" and c.get("grant_type") == "refresh_token"]
        self.assertEqual(refresh[0]["client_id"], CLIENT)
        self.assertEqual(set(refresh[0]), {"grant_type", "refresh_token", "client_id"})

    def test_invalid_grant_means_sign_in_again(self):
        google.exchange("good-code", VERIFIER)
        self.fake.refresh_ok = False
        f = config.HUB_DIR / "google" / "sam.rivera@gmail.com.json"
        data = json.loads(f.read_text())
        data["expires_at"] = 0
        f.write_text(json.dumps(data))
        with self.assertRaises(google.GoogleError) as e:
            google.access_token("sam.rivera@gmail.com")
        self.assertEqual(str(e.exception), "Google asked you to sign in again.")
        self.assertEqual(google.state("sam.rivera@gmail.com"), google.NEEDS_SIGN_IN)
        self.assertEqual(google.describe("sam.rivera@gmail.com")["detail"], "Google asked you to sign in again.")
        with self.assertRaises(google.GoogleError):
            google.access_token("sam.rivera@gmail.com")

    def test_disconnect_revokes_and_forgets(self):
        google.exchange("good-code", VERIFIER)
        self.assertTrue(google.disconnect("sam.rivera@gmail.com"))
        self.assertIn("rt-1", self.fake.revoked)
        self.assertEqual(google.accounts(), [])
        self.assertFalse(google.disconnect("sam.rivera@gmail.com"))

    def test_no_client_id_says_so(self):
        with home_fixture.use("stranger"):
            self.assertFalse(google.setup()["available"])
            with self.assertRaises(google.GoogleError) as e:
                google.exchange("good-code", VERIFIER)
            self.assertEqual(str(e.exception), google.NOT_SET_UP)
            acct = google.exchange("good-code", VERIFIER, app_client_id=CLIENT)
            self.assertEqual(acct["state"], google.CONNECTED)

    def test_tokens_never_reach_the_log(self):
        with self.assertLogs("taffy.google", level="INFO") as logs:
            google.exchange("good-code", VERIFIER)
            self.fake.refresh_ok = False
            f = config.HUB_DIR / "google" / "sam.rivera@gmail.com.json"
            data = json.loads(f.read_text())
            data["expires_at"] = 0
            f.write_text(json.dumps(data))
            with self.assertRaises(google.GoogleError):
                google.access_token("sam.rivera@gmail.com")
        joined = "\n".join(logs.output)
        for secret in ("rt-1", "at-1", "good-code", VERIFIER):
            self.assertNotIn(secret, joined)


class ConnectGoogle(GoogleCase):
    def test_connect_adds_the_mailbox_and_the_calendar(self):
        with home_fixture.use({"home_id": "test", "mail": {"accounts": []}, "calendars": [],
                               "plan": {"connectors": "none"}}) as f:
            out = mailboxes.connect_google({"code": "good-code", "code_verifier": VERIFIER, "client_id": CLIENT})
            self.assertTrue(out["ok"] and out["checked"], out)
            self.assertEqual(out["id"], "sam.rivera@gmail.com")
            saved = json.loads(f.read_text())
            [m] = saved["mail"]["accounts"]
            self.assertEqual((m["address"], m["kind"], m["provider"], m["default"]),
                             ("sam.rivera@gmail.com", "google_oauth", "gmail", True))
            [c] = saved["calendars"]
            self.assertEqual((c["kind"], c["address"], c["calendar_id"]),
                             ("google_oauth", "sam.rivera@gmail.com", "primary"))
            self.assertNotIn("rt-", f.read_text())
            [e] = mail.envelopes(m["id"], "INBOX", 5, ["from", "dana@example.org"])
            self.assertEqual((e["from_addr"], e["subject"], e["flags"]), ("dana@example.org", "The form", set()))
            self.assertIn("send the form", mail.read(m["id"], "INBOX", "18f0a1"))
            q = [p for p, _ in self.fake.calls if p.startswith("/gmail/v1/users/me/messages?")]
            self.assertIn("q=from%3Adana%40example.org", q[0])
            [ev] = calsources.google_events(c, 0, 4e9)
            self.assertEqual((ev["title"], ev["location"], ev["my_response"]), ("Dentist", "12 Main St", "accepted"))
            out = mailboxes.disconnect_google("sam.rivera@gmail.com")
            self.assertTrue(out["ok"])
            saved = json.loads(f.read_text())
            self.assertEqual((saved["mail"]["accounts"], saved["calendars"]), ([], []))
            self.assertIn("rt-1", self.fake.revoked)

    def test_mail_only_when_the_calendar_was_not_allowed(self):
        self.fake.scope = f"openid email {google.SCOPE_MAIL_READ} {google.SCOPE_MAIL_SEND}"
        with home_fixture.use({"mail": {"accounts": []}, "calendars": []}):
            out = mailboxes.connect_google({"code": "good-code", "code_verifier": VERIFIER})
            self.assertTrue(out["ok"])
            self.assertIsNone(out["calendar"])
            self.assertIn("allow the calendar", out["detail"])

    def test_removing_the_last_use_disconnects(self):
        with home_fixture.use({"mail": {"accounts": []}, "calendars": []}):
            mailboxes.connect_google({"code": "good-code", "code_verifier": VERIFIER})
            [m] = home.mail_accounts()
            mailboxes.remove_mailbox(m["id"])
            self.assertEqual(google.accounts(), ["sam.rivera@gmail.com"])
            [c] = home.calendars()
            mailboxes.remove_calendar(c["id"])
            self.assertEqual(google.accounts(), [])
            self.assertIn("rt-1", self.fake.revoked)

    def test_removing_a_mail_only_google_mailbox_forgets_its_tokens(self):
        """Night review: a Google mailbox has kind "google_oauth" and no "auth" key, so removing it must still revoke
        and forget the Google tokens when nothing else uses them."""
        self.fake.scope = f"openid email {google.SCOPE_MAIL_READ} {google.SCOPE_MAIL_SEND}"
        with home_fixture.use({"mail": {"accounts": []}, "calendars": []}):
            mailboxes.connect_google({"code": "good-code", "code_verifier": VERIFIER})
            [m] = home.mail_accounts()
            self.assertNotIn("auth", m)
            self.assertTrue(mailboxes.remove_mailbox(m["id"])["ok"])
            self.assertEqual(google.accounts(), [])
            self.assertIn("rt-1", self.fake.revoked)


class Endpoints(AppCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeGoogle()
        self.addCleanup(self.fake.close)
        saved = (google.TOKEN_URL, google.REVOKE_URL, google.GMAIL_API, google.CALENDAR_API)
        self.addCleanup(lambda: (setattr(google, "TOKEN_URL", saved[0]), setattr(google, "REVOKE_URL", saved[1]),
                                 setattr(google, "GMAIL_API", saved[2]), setattr(google, "CALENDAR_API", saved[3])))
        google.TOKEN_URL, google.REVOKE_URL = f"{self.fake.base}/token", f"{self.fake.base}/revoke"
        google.GMAIL_API, google.CALENDAR_API = f"{self.fake.base}/gmail/v1", f"{self.fake.base}/calendar/v3"
        self.home = home_fixture.use_in(self, {"home_id": "test", "mail": {"accounts": []}, "calendars": [],
                                               "plan": {"connectors": "none", "owner_name": "Jordan",
                                                        "claude": {"mode": "broker"}, "chatgpt": {"mode": "broker"}},
                                               "integrations": {"trello": None, "tally": None},
                                               "person": {"name": "Sam Rivera", "first_name": "Sam"}})

    def internal(self) -> dict:
        return {"X-Taffy-Internal": self.app._internal_key()}

    def test_home_settings_shape(self):
        r = self.client.get("/api/home-settings").json()
        self.assertEqual(r["plan"], "shared")
        self.assertEqual(r["plan_owner"], "Jordan")
        self.assertEqual(r["connectors"], "none")
        self.assertEqual(r["host"]["kind"], "server")
        self.assertEqual(r["google"]["client_id"], CLIENT)
        self.assertEqual(r["mail"], {"accounts": []})
        self.assertEqual(r["calendars"], [])
        self.assertEqual(r["person"]["quiet_hours"], {"start": 23, "end": 7})

    def test_put_home_settings(self):
        r = self.client.put("/api/home-settings", json={
            "person": {"name": "Sam Rivera", "timezone": "America/Chicago", "quiet_hours": {"start": 22, "end": 6}},
            "locale": {"units": "metric"}, "proactivity": {"enabled": False}})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["person"]["timezone"], "America/Chicago")
        self.assertEqual(body["person"]["quiet_hours"], {"start": 22, "end": 6})
        self.assertFalse(body["proactivity"]["enabled"])
        saved = json.loads(self.home.read_text())
        self.assertEqual(saved["locale"]["units"], "metric")
        p = self.client.get("/api/proactivity").json()
        self.assertEqual((p["timezone"], p["quiet_end"], p["limits"]["quiet_start"]), ("America/Chicago", 6, 22))
        self.assertEqual(json.loads((config.HUB_DIR / "person.json").read_text())["first_name"], "Sam")
        for bad in ({"person": {"timezone": "Mars/Base"}}, {"person": {"quiet_hours": {"start": 7, "end": 7}}},
                    {"locale": {"units": "cubits"}}, {"person": {"name": ""}}):
            r = self.client.put("/api/home-settings", json=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertTrue(r.json()["detail"].endswith("."))

    def test_google_round_trip_and_the_plugin_token(self):
        r = self.client.post("/api/accounts/google", json={"code": "good-code", "code_verifier": VERIFIER,
                                                           "redirect_uri": google.redirect_uri(), "client_id": CLIENT})
        body = r.json()
        self.assertTrue(body["ok"] and body["checked"], body)
        self.assertEqual(body["address"], "sam.rivera@gmail.com")
        self.assertNotIn("rt-", r.text)
        self.assertNotIn("at-", r.text)
        s = self.client.get("/api/home-settings").json()
        self.assertEqual(s["google"]["accounts"][0]["state"], "connected")
        self.assertEqual(s["mail"]["accounts"][0]["provider"], "google")
        self.assertEqual(s["calendars"][0]["provider"], "google")
        self.assertEqual(self.client.post("/internal/google/token", json={"address": "sam.rivera@gmail.com"})
                         .status_code, 404)
        tok = self.client.post("/internal/google/token", json={"address": "sam.rivera@gmail.com"},
                               headers=self.internal()).json()
        self.assertTrue(tok["ok"])
        self.assertTrue(tok["access_token"].startswith("at-"))
        self.assertIn(google.SCOPE_MAIL_SEND, tok["scopes"])
        other = self.client.post("/internal/google/token", json={"address": "someone@gmail.com"},
                                 headers=self.internal()).json()
        self.assertFalse(other["ok"])
        [m] = home.mail_accounts()
        for args in ({"account": m["id"], "purpose": "mail"}, {"address": "sam.rivera@gmail.com",
                                                                "purpose": "calendar"}):
            r = self.client.post("/internal/accounts/google_token", json={"bot_id": "default", "session_id": "s",
                                                                          "args": args}, headers=self.internal())
            res = r.json()
            self.assertTrue(res["ok"], res)
            self.assertTrue(res["result"]["access_token"].startswith("at-"))
            self.assertGreater(res["result"]["expires_in"], 3000)
        self.assertEqual(self.client.post("/internal/accounts/google_token", json={"bot_id": "default",
                                          "args": {"account": "nope"}}).status_code, 404)
        none = self.client.post("/internal/accounts/google_token", json={"bot_id": "default", "args": {
            "account": "nope"}}, headers=self.internal()).json()
        self.assertEqual((none["ok"], none["result"]["connected"]), (True, False))
        self.fake.refresh_ok = False
        f = config.HUB_DIR / "google" / "sam.rivera@gmail.com.json"
        data = json.loads(f.read_text())
        data["expires_at"] = 0
        f.write_text(json.dumps(data))
        tok = self.client.post("/internal/google/token", json={"address": "sam.rivera@gmail.com"},
                               headers=self.internal()).json()
        self.assertEqual((tok["ok"], tok["state"], tok["error"]),
                         (False, "needs_sign_in", "Google asked you to sign in again."))
        res = self.client.post("/internal/accounts/google_token", json={"bot_id": "default", "args": {
            "address": "sam.rivera@gmail.com"}}, headers=self.internal()).json()
        self.assertEqual(res, {"ok": True, "result": {"connected": False, "state": "needs_sign_in",
                                                      "message": "Google asked you to sign in again."}})
        s = self.client.get("/api/accounts/google").json()
        self.assertEqual(s["accounts"][0]["detail"], "Google asked you to sign in again.")
        r = self.client.delete("/api/accounts/google/sam.rivera@gmail.com").json()
        self.assertTrue(r["ok"])
        self.assertEqual(self.client.get("/api/accounts/google").json()["accounts"], [])

    def test_accounts_say_needs_own_plan_without_running_claude(self):
        from taffy_hub import accounts

        async def no_claude(*a):
            raise AssertionError("no claude command runs in a home without connectors")
        saved = (accounts._run, accounts._last)
        self.addCleanup(lambda: (setattr(accounts, "_run", saved[0]), setattr(accounts, "_last", saved[1])))
        accounts._run, accounts._last = no_claude, None
        r = self.client.get("/api/accounts").json()
        self.assertEqual(r["plan"], "shared")
        self.assertTrue(all(s["state"] == "needs_own_plan" for s in r["services"]))
        self.assertEqual(r["services"][0]["detail"], accounts.OWN_PLAN_DETAIL)
        self.assertEqual(r["mailboxes"], [])
        need = self.client.post("/internal/accounts/need", json={"bot_id": "default", "args": {"service": "gmail"}},
                                headers=self.internal()).json()
        self.assertEqual(need["result"]["state"], "needs_own_plan")
        self.assertIn("email tool", need["result"]["message"])

    def test_templates_say_what_the_home_can_run(self):
        from taffy_hub import app as app_mod
        tpl = Path(tempfile.mkdtemp()) / "zip-templates.json"
        tpl.write_text(json.dumps([{"id": i, "name": i, "pitch": "Keeps {name}'s inbox calm.", "look": {},
                                    "needs": ["Which email should I watch for {name}?"]} for i in
                                   ("inbox", "todo", "brief", "meetings", "coder")]))
        saved = app_mod.TEMPLATES
        self.addCleanup(setattr, app_mod, "TEMPLATES", saved)
        app_mod.TEMPLATES = tpl
        templates = self.client.get("/api/templates").json()["templates"]
        got = {t["id"]: t["available"] for t in templates}
        self.assertEqual(got, {"inbox": True, "todo": False, "brief": True, "meetings": False, "coder": True})
        self.assertEqual(templates[0]["pitch"], "Keeps Sam's inbox calm.")
        self.assertEqual(templates[0]["needs"], ["Which email should I watch for Sam?"])
        with home_fixture.use(None):
            got = {t["id"]: t["available"] for t in self.client.get("/api/templates").json()["templates"]}
            self.assertTrue(all(got.values()))

    def test_pair_qr_is_for_scripts_on_this_server_only(self):
        self.assertEqual(self.client.get("/api/pair/qr?code=ABCD2345").status_code, 404)
        self.assertEqual(self.client.get("/api/pair/qr?code=ABCD2345", headers=self.internal()).status_code, 409)
        with home_fixture.use(None):
            r = self.client.get("/api/pair/qr?code=ABCD2345", headers=self.internal()).json()
        self.assertEqual(r["link"], "zero://pair?server=https%3A%2F%2Falien.tail0000.ts.net%3A6443&code=ABCD2345")
        self.assertEqual(self.client.get("/api/pair/qr?code=AB", headers={**self.internal(),
                                                                          "X-Forwarded-For": "1.2.3.4"}).status_code,
                         404)


class FakeHimalaya:
    def __init__(self, ok=True, stderr=""):
        self.ok, self.stderr, self.argv = ok, stderr, []

    def __call__(self, argv):
        self.argv.append(argv)
        return subprocess.CompletedProcess(argv, 0 if self.ok else 1, stdout="[]", stderr=self.stderr)


class PasswordMailboxes(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        saved = (mailboxes.himalaya_dir, mailboxes.calendars_dir, mail.run, mailboxes.http_request)
        self.addCleanup(lambda: (setattr(mailboxes, "himalaya_dir", saved[0]),
                                 setattr(mailboxes, "calendars_dir", saved[1]), setattr(mail, "run", saved[2]),
                                 setattr(mailboxes, "http_request", saved[3])))
        mailboxes.himalaya_dir = lambda: self.dir / "himalaya"
        mailboxes.calendars_dir = lambda: self.dir / "cals"
        self.home = home_fixture.use_in(self, {"mail": {"accounts": []}, "calendars": [],
                                               "person": {"name": "Sam Rivera"}})

    def test_icloud_mailbox_is_checked_and_kept(self):
        mail.run = FakeHimalaya()
        out = mailboxes.add_password_mailbox({"address": "sam@icloud.com", "provider": "icloud",
                                              "password": "abcd-efgh-ijkl-mnop", "label": "iCloud"})
        self.assertTrue(out["ok"] and out["checked"], out)
        self.assertEqual(mail.run.argv[0][:4], ["folder", "list", "-a", "icloud"])
        cfg = (self.dir / "himalaya" / "config.toml").read_text()
        self.assertIn("# zero:begin icloud", cfg)
        self.assertIn('backend.host = "imap.mail.me.com"', cfg)
        self.assertIn('display-name = "Sam Rivera"', cfg)
        self.assertNotIn("abcd-efgh", cfg)
        pw = self.dir / "himalaya" / "icloud.pass"
        self.assertEqual(pw.read_text(), "abcd-efgh-ijkl-mnop")
        self.assertEqual(stat.S_IMODE(pw.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.dir / "himalaya" / "config.toml").stat().st_mode), 0o600)
        [m] = json.loads(self.home.read_text())["mail"]["accounts"]
        self.assertEqual((m["id"], m["kind"], m["default"]), ("icloud", "imap", True))
        self.assertNotIn("abcd", self.home.read_text())
        self.assertEqual(list(mail.ACCOUNTS), ["icloud"])
        cfg_path = self.dir / "himalaya" / "config.toml"
        cfg_path.write_text("[accounts.mine]\nemail = \"x@y.z\"\n\n" + cfg_path.read_text())
        self.assertTrue(mailboxes.remove_mailbox("icloud")["ok"])
        self.assertFalse(pw.exists())
        left = cfg_path.read_text()
        self.assertNotIn("zero:begin icloud", left)
        self.assertIn("[accounts.mine]", left)
        self.assertEqual(home.mail_accounts(), [])

    def test_bad_password_is_a_plain_sentence_and_nothing_is_kept(self):
        mail.run = FakeHimalaya(ok=False, stderr="Error: cannot authenticate to IMAP server: AUTHENTICATIONFAILED")
        out = mailboxes.add_password_mailbox({"address": "sam@icloud.com", "provider": "icloud", "password": "nope"})
        self.assertEqual(out, {"ok": False, "checked": False, "detail": "The mail server did not accept that "
                                                                         "password. Check the app password and try "
                                                                         "again."})
        self.assertFalse((self.dir / "himalaya" / "icloud.pass").exists())
        self.assertNotIn("zero:begin", (self.dir / "himalaya" / "config.toml").read_text())
        self.assertEqual(home.mail_accounts(), [])

    def test_other_provider_takes_the_apps_server_objects(self):
        mail.run = FakeHimalaya()
        out = mailboxes.add_password_mailbox({"address": "sam@example.test", "provider": "other", "password": "pw",
                                              "imap": {"host": "imap.example.test", "port": 1993},
                                              "smtp": {"host": "smtp.example.test", "port": 1587}})
        self.assertTrue(out["ok"], out)
        cfg = (self.dir / "himalaya" / "config.toml").read_text()
        self.assertIn('backend.host = "imap.example.test"', cfg)
        self.assertIn("backend.port = 1993", cfg)
        self.assertIn("message.send.backend.port = 1587", cfg)
        for bad in ({"address": "nope", "provider": "icloud", "password": "x"},
                    {"address": "a@b.co", "provider": "icloud", "password": ""},
                    {"address": "a@b.co", "provider": "other", "password": "x"}):
            self.assertFalse(mailboxes.add_password_mailbox(bad)["ok"], bad)

    def test_a_mailbox_on_this_machine_is_plain_text(self):
        """The test home's mail server (and a local bridge) listens on loopback without TLS (live e2e, Oct 9)."""
        mail.run = FakeHimalaya()
        out = mailboxes.add_password_mailbox({"address": "sam@zero.test", "provider": "other", "password": "pw",
                                              "imap": {"host": "127.0.0.1", "port": 18551},
                                              "smtp": {"host": "127.0.0.1", "port": 18550}})
        self.assertTrue(out["ok"], out)
        cfg = (self.dir / "himalaya" / "config.toml").read_text()
        self.assertIn('backend.encryption.type = "none"', cfg)
        self.assertIn('message.send.backend.encryption.type = "none"', cfg)
        self.assertNotIn('"tls"', cfg)
        self.assertNotIn('"start-tls"', cfg)

    def test_a_remote_server_keeps_tls(self):
        mail.run = FakeHimalaya()
        mailboxes.add_password_mailbox({"address": "sam@example.test", "provider": "other", "password": "pw",
                                        "imap": {"host": "imap.example.test"}, "smtp": {"host": "smtp.example.test"}})
        cfg = (self.dir / "himalaya" / "config.toml").read_text()
        self.assertIn('backend.encryption.type = "tls"', cfg)
        self.assertIn('message.send.backend.encryption.type = "start-tls"', cfg)

    def test_caldav_and_ical(self):
        class R:
            def __init__(self, code, text=""):
                self.status_code, self.text, self.content = code, text, text.encode()
        answers = {}
        mailboxes.http_request = lambda method, url, **kw: answers[(method, kw.get("auth", (None, None))[1])]
        answers[("PROPFIND", "wrong")] = R(401)
        out = mailboxes.add_calendar({"kind": "caldav", "username": "sam@icloud.com", "password": "wrong"})
        self.assertEqual(out["detail"], "The calendar server did not accept that password. Check the app password "
                                        "and try again.")
        answers[("PROPFIND", "right")] = R(207, "<multistatus/>")
        out = mailboxes.add_calendar({"provider": "icloud", "username": "sam@icloud.com", "password": "right"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["calendar"]["provider"], "icloud")
        pw = self.dir / "cals" / f"{out['calendar']['id']}.pass"
        self.assertEqual(stat.S_IMODE(pw.stat().st_mode), 0o600)
        self.assertNotIn("right", self.home.read_text())
        answers[("GET", None)] = R(200, "BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        out = mailboxes.add_calendar({"provider": "link", "url": "webcal://example.test/private-abc/basic.ics"})
        self.assertTrue(out["ok"], out)
        cid = out["calendar"]["id"]
        self.assertEqual(out["calendar"]["provider"], "link")
        self.assertEqual((self.dir / "cals" / f"{cid}.url").read_text(), "https://example.test/private-abc/basic.ics")
        self.assertNotIn("private-abc", self.home.read_text())
        self.assertTrue(mailboxes.remove_calendar(cid)["ok"])
        self.assertFalse((self.dir / "cals" / f"{cid}.url").exists())


ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:weekly-1
SUMMARY:Choir practice
LOCATION:St. Mark's
DTSTART;TZID=America/New_York:20261005T190000
DTEND;TZID=America/New_York:20261005T203000
RRULE:FREQ=WEEKLY;BYDAY=MO,TH;COUNT=6
END:VEVENT
BEGIN:VEVENT
UID:once-1
SUMMARY:Lunch with Dana\\, at noon
DTSTART:20261009T160000Z
DTEND:20261009T170000Z
ATTENDEE;CN=Dana Reyes:mailto:dana@example.org
END:VEVENT
BEGIN:VEVENT
UID:allday
SUMMARY:Trip
DTSTART;VALUE=DATE:20261012
DTEND;VALUE=DATE:20261014
END:VEVENT
END:VCALENDAR
"""


class Calendars(unittest.TestCase):
    def setUp(self):
        home_fixture.use_in(self, None)

    def test_ics_with_a_weekly_rule(self):
        from datetime import datetime
        start = datetime(2026, 10, 8, tzinfo=home.TZ).timestamp()
        end = datetime(2026, 10, 16, tzinfo=home.TZ).timestamp()
        evs = calsources.parse_ics(ICS, start, end, "Home")
        titles = [(e["title"], datetime.fromtimestamp(e["start"], home.TZ).strftime("%a %d %H:%M")) for e in evs]
        self.assertEqual(titles, [("Choir practice", "Thu 08 19:00"), ("Lunch with Dana, at noon", "Fri 09 12:00"),
                                  ("Trip", "Mon 12 00:00"), ("Choir practice", "Mon 12 19:00"),
                                  ("Choir practice", "Thu 15 19:00")])
        lunch = evs[1]
        self.assertEqual(lunch["attendees"][0]["email"], "dana@example.org")
        self.assertTrue(evs[2]["all_day"])
        self.assertEqual(evs[2]["end"] - evs[2]["start"], 2 * 86400)

    def test_caldav_discovery_and_report(self):
        class R:
            def __init__(self, body):
                self.status_code, self.content = 207, body.encode()
        D = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"'
        seen = []

        def fake(method, url, **kw):
            seen.append((method, url))
            if method == "PROPFIND" and "current-user-principal" in kw["content"]:
                return R(f'<d:multistatus {D}><d:response><d:propstat><d:prop><d:current-user-principal>'
                         '<d:href>/123/principal/</d:href></d:current-user-principal></d:prop></d:propstat>'
                         '</d:response></d:multistatus>')
            if method == "PROPFIND" and "calendar-home-set" in kw["content"]:
                return R(f'<d:multistatus {D}><d:response><d:propstat><d:prop><c:calendar-home-set>'
                         '<d:href>/123/calendars/</d:href></c:calendar-home-set></d:prop></d:propstat>'
                         '</d:response></d:multistatus>')
            if method == "PROPFIND":
                return R(f'<d:multistatus {D}><d:response><d:href>/123/calendars/home/</d:href><d:propstat><d:prop>'
                         '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Home'
                         '</d:displayname></d:prop></d:propstat></d:response><d:response><d:href>/123/calendars/'
                         '</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>'
                         '</d:propstat></d:response></d:multistatus>')
            ics = ICS.replace("&", "&amp;").replace("<", "&lt;")
            return R(f'<d:multistatus {D}><d:response><d:href>/123/calendars/home/a.ics</d:href><d:propstat><d:prop>'
                     f'<c:calendar-data>{ics}</c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>')
        saved = (mailboxes.http_request, calsources._secret)
        self.addCleanup(lambda: (setattr(mailboxes, "http_request", saved[0]), setattr(calsources, "_secret", saved[1])))
        mailboxes.http_request = fake
        calsources._secret = lambda cid, ext, given=None: "pw"
        from datetime import datetime
        evs = calsources.caldav_events({"id": "icloud", "kind": "caldav", "url": "https://caldav.example.test",
                                        "username": "sam"}, datetime(2026, 10, 8, tzinfo=home.TZ).timestamp(),
                                       datetime(2026, 10, 10, tzinfo=home.TZ).timestamp())
        self.assertEqual([e["title"] for e in evs], ["Choir practice", "Lunch with Dana, at noon"])
        self.assertEqual(evs[0]["calendar"], "Home")
        self.assertEqual(seen[-1], ("REPORT", "https://caldav.example.test/123/calendars/home/"))

    def test_calcache_reads_own_calendars_without_a_model(self):
        from taffy_hub import calcache
        with home_fixture.use({"calendars": [{"id": "ical", "kind": "ical"}], "plan": {"connectors": "none"}}):
            self.assertEqual(calcache.source("calendar"), "own")
            self.assertEqual(calcache.source("trello"), "none")
        with home_fixture.use(None):
            self.assertEqual(calcache.source("calendar"), "connector")
            self.assertEqual(calcache.source("trello"), "connector")
        with home_fixture.use({"integrations": {"trello": None}}):
            self.assertEqual(calcache.source("trello"), "none")


if __name__ == "__main__":
    unittest.main()
