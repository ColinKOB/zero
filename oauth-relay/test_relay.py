import base64
import json
import os
import time
import unittest
import urllib.parse
from unittest import mock

os.environ.update(PUBLIC_URL="https://relay.example", NOTION_CLIENT_ID="nid", NOTION_CLIENT_SECRET="nsec",
                  TODOIST_CLIENT_ID="tid", TODOIST_CLIENT_SECRET="tsec")
import relay  # noqa: E402


class TokenRequest(unittest.TestCase):
    def test_notion_code_uses_basic_auth_and_json(self):
        r = relay.token_request("notion", {"code": "c1"})
        self.assertEqual(r.full_url, "https://api.notion.com/v1/oauth/token")
        self.assertEqual(r.get_header("Authorization"), "Basic " + base64.b64encode(b"nid:nsec").decode())
        body = json.loads(r.data)
        self.assertEqual(body, {"grant_type": "authorization_code", "code": "c1",
                                "redirect_uri": "https://relay.example/callback/notion"})

    def test_todoist_code_puts_client_in_form(self):
        r = relay.token_request("todoist", {"code": "c2", "redirect_uri": "https://relay.example/callback/todoist"})
        form = dict(urllib.parse.parse_qsl(r.data.decode()))
        self.assertEqual(form["client_id"], "tid")
        self.assertEqual(form["client_secret"], "tsec")
        self.assertIsNone(r.get_header("Authorization"))

    def test_refresh(self):
        body = json.loads(relay.token_request("notion", {"refresh_token": "r1"}).data)
        self.assertEqual(body, {"grant_type": "refresh_token", "refresh_token": "r1"})

    def test_other_redirect_refused(self):
        with self.assertRaises(ValueError):
            relay.token_request("notion", {"code": "c", "redirect_uri": "https://evil.example/cb"})

    def test_empty_body_refused(self):
        with self.assertRaises(ValueError):
            relay.token_request("notion", {})

    def test_missing_secret_means_not_set_up(self):
        with mock.patch.dict(os.environ, {"ATLASSIAN_CLIENT_ID": "", "ATLASSIAN_CLIENT_SECRET": ""}):
            self.assertIsNone(relay.client("atlassian"))


class Limits(unittest.TestCase):
    def test_rate_limit_and_window(self):
        relay._recent.clear()
        for _ in range(relay.LIMIT):
            self.assertTrue(relay.allowed("1.2.3.4", now=1000))
        self.assertFalse(relay.allowed("1.2.3.4", now=1000))
        self.assertTrue(relay.allowed("5.6.7.8", now=1000))
        self.assertTrue(relay.allowed("1.2.3.4", now=1000 + relay.WINDOW_SEC + 1))


class Page(unittest.TestCase):
    def test_forward_page_targets_app(self):
        page = relay.forward_page("trello")
        self.assertIn('"zero://oauth/trello"+location.search+location.hash', page)


class StripeWebhook(unittest.TestCase):
    """A real server on a free port; the front door is a second tiny server. Nothing calls Stripe."""

    def setUp(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.got = []
        test = self

        class FrontDoor(BaseHTTPRequestHandler):
            status = 200

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                test.got.append((body, self.headers.get("Stripe-Signature")))
                self.send_response(FrontDoor.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.FrontDoor = FrontDoor
        self.front = ThreadingHTTPServer(("127.0.0.1", 0), FrontDoor)
        self.relay = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        for srv in (self.front, self.relay):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.env = mock.patch.dict(os.environ, {
            "STRIPE_WEBHOOK_SECRET": "whsec_test_a,whsec_live_b", "BILLING_FORWARD_SECRET": "fwd",
            "BILLING_FORWARD_URL": f"http://127.0.0.1:{self.front.server_port}/billing/stripe/webhook"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for srv in (self.front, self.relay):
            srv.shutdown()
            srv.server_close()

    def post(self, body, sig):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.relay.server_port}/stripe/webhook", data=body,
                                     method="POST", headers={"Stripe-Signature": sig} if sig else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_verified_event_is_forwarded_and_signed_again(self):
        body = b'{"id":"evt_1","type":"checkout.session.completed"}'
        self.assertEqual(self.post(body, relay.stripe_sign(body, "whsec_live_b")), 200)
        fwd_body, fwd_sig = self.got[0]
        self.assertEqual(fwd_body, body)
        self.assertTrue(relay.stripe_verified(body, fwd_sig, ["fwd"]))
        self.assertFalse(relay.stripe_verified(body, fwd_sig, ["whsec_live_b"]))

    def test_bad_signature_is_not_forwarded(self):
        body = b'{"id":"evt_2"}'
        self.assertEqual(self.post(body, relay.stripe_sign(body, "someone_else")), 400)
        self.assertEqual(self.post(body, None), 400)
        self.assertEqual(self.got, [])

    def test_old_signature_refused(self):
        body = b'{"id":"evt_3"}'
        self.assertEqual(self.post(body, relay.stripe_sign(body, "whsec_test_a", now=time.time() - 3600)), 400)

    def test_front_door_failure_asks_stripe_to_retry(self):
        self.FrontDoor.status = 500
        body = b'{"id":"evt_4"}'
        self.assertEqual(self.post(body, relay.stripe_sign(body, "whsec_test_a")), 502)

    def test_not_set_up(self):
        with mock.patch.dict(os.environ, {"BILLING_FORWARD_URL": ""}):
            body = b"{}"
            self.assertEqual(self.post(body, relay.stripe_sign(body, "whsec_test_a")), 503)


if __name__ == "__main__":
    unittest.main()
