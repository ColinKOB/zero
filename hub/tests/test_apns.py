"""Notifications go only to paired devices, and Apple's "gone" answers drop the token.

Run on alien: ~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import apns, config, store  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code, self.text = status_code, text


class FakeClient:
    def __init__(self, answers: dict):
        self.answers, self.sent = answers, []

    def post(self, path, json=None, headers=None):
        token = path.rsplit("/", 1)[-1]
        self.sent.append((token, json, headers))
        return self.answers.get(token, FakeResponse(200))


class Push(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db, self.token, self.clients = config.HUB_DB, apns._token, dict(apns._clients)
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        apns._token = lambda: "bearer-for-tests"
        for name in ("phone", "mac"):
            store.db().execute("INSERT INTO devices (token_hash, name, created_at) VALUES (?, ?, 0)", (name, name))
        store.db().commit()

    def tearDown(self):
        config.HUB_DB, apns._token = self.db, self.token
        apns._clients.clear()
        apns._clients.update(self.clients)
        store._local.conn = None
        store._schema_ready = False

    def test_only_paired_devices_get_notifications(self):
        apns.save_token("phone", "aa" * 32, "development", "iphone")
        apns.save_token("unpaired", "bb" * 32, "development", "iphone")
        self.assertEqual([r["apns_token"] for r in apns.tokens()], ["aa" * 32])

    def test_a_new_token_replaces_the_old_one_for_that_device(self):
        apns.save_token("phone", "aa" * 32, "development", "iphone")
        apns.save_token("phone", "cc" * 32, "development", "iphone")
        apns.save_token("mac", "dd" * 32, "development", "mac")
        self.assertEqual(sorted(r["apns_token"] for r in apns.tokens()), ["cc" * 32, "dd" * 32])

    def test_send_reaches_every_device_and_drops_dead_tokens(self):
        apns.save_token("phone", "aa" * 32, "development", "iphone")
        apns.save_token("mac", "dd" * 32, "development", "mac")
        fake = FakeClient({"dd" * 32: FakeResponse(410, '{"reason":"Unregistered"}')})
        apns._clients["development"] = fake
        self.assertTrue(apns.send("School needs you", "Sign in to eLearning", "school"))
        self.assertEqual(len(fake.sent), 2)
        token, payload, headers = fake.sent[0]
        self.assertEqual(payload["bot_id"], "school")
        self.assertEqual(payload["aps"]["thread-id"], "school")
        self.assertEqual(headers["apns-topic"], "com.example.Taffy")
        self.assertEqual([r["apns_token"] for r in apns.tokens()], ["aa" * 32])

    def test_nothing_to_send_without_a_key(self):
        apns._token = lambda: None
        apns.save_token("phone", "aa" * 32, "development", "iphone")
        self.assertFalse(apns.send("Title", "Body"))


if __name__ == "__main__":
    unittest.main()



class NeverFromTests(unittest.TestCase):
    """Oct 10: tests sent real pushes ("Allow Taffy to use your Mac") to Colin's iPhone through the live database."""

    def test_a_test_run_never_reaches_a_real_phone(self):
        self.assertTrue(apns._under_tests())
        self.assertIs(apns._token, apns._REAL_TOKEN)
        self.assertFalse(apns.send("Test", "This must never reach a phone."))
