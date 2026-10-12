"""A reply counts as the ChatGPT backup only when it came from a provider other than the Zip's own (Oct 11: with half
the Zips on Sol for the blind model trial, every Sol reply looked like the backup, and the hub told Colin his Claude
plan was used up while it stood at 21 percent).

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, hermes, store  # noqa: E402
from test_onboarding import _fresh_store  # noqa: E402

CLAUDE = "claude-subscription-directsdk-experimental"


class OwnProvider(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "profiles" / "money").mkdir(parents=True)
        (self.home / "config.yaml").write_text(f"model:\n  provider: {CLAUDE}\n  default: opus\n")
        (self.home / "profiles" / "money" / "config.yaml").write_text(
            "model:\n  default: gpt-6.1-sol\n  provider: openai-codex\nfallback_providers:\n"
            f"- provider: {CLAUDE}\n  model: opus\n")
        self.patch = mock.patch.object(config, "HERMES_HOME", self.home)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_each_zip_reads_its_own_provider(self):
        self.assertEqual(hermes.primary_provider("default"), CLAUDE)
        self.assertEqual(hermes.primary_provider("money"), "openai-codex")

    def test_a_zip_with_no_config_is_on_claude(self):
        self.assertEqual(hermes.primary_provider("career"), CLAUDE)


class QuietMarker(OwnProvider):
    """A reply the backup gave carries `backup` to the app, live and in the history; nothing else changes."""

    def setUp(self):
        super().setUp()
        self.saved = (config.HUB_DIR, config.HUB_DB)
        _fresh_store(Path(tempfile.mkdtemp()))

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False
        super().tearDown()

    def stream(self, bot, provider, content):
        httpx = __import__("httpx")
        real = hermes.httpx.AsyncClient
        sse = f"event: assistant.completed\ndata: {json.dumps({'content': content, 'runtime': {'provider': provider}})}\n\n"
        factory = lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=sse)), **kw)  # noqa: E731

        async def run():
            return [ev async for ev in hermes.stream_turn(bot, "hi")]
        with mock.patch.object(hermes, "ensure_session", lambda *a, **k: asyncio.sleep(0, "sid-1")), \
                mock.patch.object(hermes, "_headers", lambda bot_id: {}), \
                mock.patch.object(hermes.httpx, "AsyncClient", factory), \
                mock.patch("taffy_hub.health.turn_finished", lambda *a, **k: None), \
                mock.patch("taffy_hub.usage.turn_ended", lambda *a, **k: None):
            return [ev for ev in asyncio.run(run()) if ev["type"] == "reply"][0]

    def test_only_a_reply_from_another_provider_is_marked(self):
        self.assertTrue(self.stream("default", "openai-codex", "From the backup.")["backup"])
        self.assertNotIn("backup", self.stream("default", CLAUDE, "From Claude."))
        self.assertNotIn("backup", self.stream("money", "openai-codex", "Sol, its own model."))
        msgs = [{"role": "assistant", "class": "bot", "text": "From the backup.", "display": "From the backup."},
                {"role": "assistant", "class": "bot", "text": "From Claude.", "display": "From Claude."},
                {"role": "user", "class": "colin", "text": "From the backup.", "display": "From the backup."}]
        marked = hermes.mark_backup("default", msgs)
        self.assertEqual([m.get("backup") for m in marked], [True, None, None])
        self.assertEqual([m.get("backup") for m in hermes.mark_backup("money", msgs)], [None, None, None])


if __name__ == "__main__":
    unittest.main()
