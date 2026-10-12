"""The hub's own model calls in the usage log (Fable's usage review, Oct 11): short replies, notification lines, the
question gate, memory, briefings and the calendar copy run Haiku through the claude CLI, and none of it was logged.

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import usage  # noqa: E402


class HubCallsAreLogged(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.patch = mock.patch.object(usage, "write", self.records.append)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_a_call_is_one_line_in_the_plugins_fields(self):
        result = {"usage": {"input_tokens": 512, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 2048,
                            "output_tokens": 61}, "is_error": False}
        usage.log_cli("hub-notify", result, "claude-haiku-5-5", time.monotonic() - 2.5)
        r = self.records[0]
        self.assertEqual((r["origin"], r["model"], r["uncached"], r["write"], r["read"], r["out"], r["ok"]),
                         ("hub-notify", "claude-haiku-5-5", 512, 2048, 0, 61, True))
        self.assertGreaterEqual(r["secs"], 2.5)

    def test_a_failed_call_is_logged_and_never_raises(self):
        usage.log_cli("hub-calcache-trello", None, "claude-haiku-5-5", time.monotonic())
        self.assertFalse(self.records[0]["ok"])
        with mock.patch.object(usage, "write", side_effect=OSError("disk full")):
            usage.log_cli("hub-x", {}, "m", time.monotonic())   # no exception

    def test_the_origin_is_the_module_that_asked_even_through_wait_for(self):
        async def ask():
            return usage.caller()

        async def notify_like():
            return await asyncio.wait_for(ask(), 5)

        self.assertEqual(asyncio.run(notify_like()), "hub-" + __name__.rsplit(".", 1)[-1])


if __name__ == "__main__":
    unittest.main()
