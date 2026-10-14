"""Calls on the person's own ChatGPT account (docs/qa/2026-10-09-calls-on-openai.md).

- A missing or expired Codex sign-in, or no Codex at all, reaches the app as one plain sentence that says what to do,
  using Codex's real words for a signed-out start (Codex 0.160.1).
- The Codex app server runs in its own process group, so stopping it also stops the real server its launcher
  starts, and it is stopped after a quiet spell with no call; the next call starts a fresh one.
- The ChatGPT check in accounts.py no longer leaves an app server running after every check.

Nothing here reaches OpenAI or the real Codex. The process tests start `sh` and `sleep` only.
"""

import asyncio
import os
import signal
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import accounts, voice  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402

CODEX_SIGNED_OUT = ("unexpected status 401 Unauthorized: You didn't provide an API key. You need to provide your API "
                    "key in an Authorization header using Bearer auth (i.e. Authorization: Bearer YOUR_KEY)., url: "
                    "https://api.openai.com/v1/live, cf-ray: a47e0a79288fc8c6-EWR")


def gone(pid: int, wait: float = 3.0) -> bool:
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:   # a child of this test that has exited but not been reaped yet
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                return True
        except ChildProcessError:
            pass
        time.sleep(0.05)
    return False


class PlainWords(unittest.TestCase):
    def test_signed_out_codex_says_to_sign_in(self):
        self.assertEqual(voice.plain_error(CODEX_SIGNED_OUT), voice.SIGNED_OUT)
        self.assertEqual(voice.start_error(RuntimeError(CODEX_SIGNED_OUT)), voice.SIGNED_OUT)

    def test_expired_sign_in_says_to_sign_in(self):
        for msg in ("Your access token could not be refreshed because your refresh token was already used. "
                    "Please log out and sign in again.",
                    "Provided authentication token is expired. Please try signing in again.",
                    "token_expired", "invalid_api_key", "Not logged in"):
            self.assertEqual(voice.start_error(RuntimeError(msg)), voice.SIGNED_OUT, msg)

    def test_no_codex_says_to_install_it(self):
        text = voice.start_error(FileNotFoundError(2, "No such file or directory", "codex"))
        self.assertEqual(text, voice.NO_CODEX)
        self.assertIn("Codex", text)
        self.assertIn("ChatGPT plan", text)

    def test_limits_and_other_failures(self):
        self.assertEqual(voice.start_error(RuntimeError("429 Too Many Requests")), voice.VOICE_LIMIT)
        self.assertIn("in a moment", voice.start_error(asyncio.TimeoutError()))
        self.assertIn("in a moment", voice.start_error(RuntimeError("websocket closed 1006")))
        self.assertEqual(voice.plain_error("429 rate_limit_exceeded: tokens per min"), voice.VOICE_LIMIT)

    def test_sentences_are_plain(self):
        for text in (voice.SIGNED_OUT, voice.NO_CODEX, voice.VOICE_LIMIT, voice.start_error(RuntimeError("x"))):
            self.assertNotIn("—", text)
            for word in ("server", "alien", "401", "API", "websocket", "app-server"):
                self.assertNotIn(word, text)
            self.assertTrue(text.endswith("."), text)


class StartRoute(AppCase):
    def start_with(self, error):
        async def fail(bot_id, sdp, voice_name, chosen=None):
            raise error
        with mock.patch.object(voice, "start", fail), mock.patch("taffy_hub.health.voice_failed", lambda: None):
            return self.client.post("/api/bots/default/voice", json={"sdp": "v=0\r\n", "voice": "cove"})

    def test_signed_out_reaches_the_app_as_a_sentence(self):
        r = self.start_with(RuntimeError(CODEX_SIGNED_OUT))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], voice.SIGNED_OUT)

    def test_missing_codex_reaches_the_app_as_a_sentence(self):
        r = self.start_with(FileNotFoundError(2, "No such file or directory"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], voice.NO_CODEX)

    def test_raw_error_never_reaches_the_app(self):
        r = self.start_with(RuntimeError("thread/realtime/start failed: stream error at wss://x"))
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("wss", r.json()["detail"])


def launcher(tmp: Path) -> Path:
    """A stand-in for the `codex` launcher: starts a child (the "real" server), writes its pid, and waits."""
    script = tmp / "codex"
    script.write_text("#!/bin/sh\nsleep 120 &\necho $! > \"$(dirname \"$0\")/child.pid\"\nwait\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


async def child_pid(tmp: Path) -> int:
    for _ in range(100):
        f = tmp / "child.pid"
        if f.exists() and f.read_text().strip():
            return int(f.read_text())
        await asyncio.sleep(0.02)
    raise AssertionError("the stand-in never started its child")


class CodexProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.exe = launcher(self.tmp)
        self.saved = (voice.server.proc, voice.CODEX_IDLE_SEC, dict(voice.calls), dict(voice.server.listeners))
        voice.calls.clear()
        voice.server.listeners.clear()

    def tearDown(self):
        voice.server.proc, voice.CODEX_IDLE_SEC, calls, listeners = self.saved
        voice.calls.clear()
        voice.calls.update(calls)
        voice.server.listeners.clear()
        voice.server.listeners.update(listeners)

    async def spawn(self):
        proc = await asyncio.create_subprocess_exec(str(self.exe), stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, start_new_session=True)
        return proc, await child_pid(self.tmp)

    def test_stopping_ends_the_real_server_too(self):
        async def go():
            proc, child = await self.spawn()
            await voice.stop_process(proc, grace=1)
            return proc, child
        proc, child = asyncio.run(go())
        self.assertIsNotNone(proc.returncode)
        self.assertTrue(gone(child), "the launcher's child is still running")

    def test_idle_server_is_stopped_quietly(self):
        voice.CODEX_IDLE_SEC = 0

        async def go():
            proc, child = await self.spawn()
            voice.server.proc = proc
            reader = asyncio.create_task(voice.server._read())
            with self.assertNoLogs("taffy.voice", level="WARNING"):
                await voice.server._stop_when_idle()
                await asyncio.wait_for(reader, 3)
            return proc, child
        proc, child = asyncio.run(go())
        self.assertIsNone(voice.server.proc)
        self.assertTrue(gone(child))

    def test_server_with_a_live_call_is_kept(self):
        voice.CODEX_IDLE_SEC = 0

        async def go():
            proc, child = await self.spawn()
            voice.server.proc = proc
            voice.calls["c1"] = voice.Call(id="c1", bot_id="default", voice="cove")
            await voice.server._stop_when_idle()
            alive = proc.returncode is None
            await voice.stop_process(proc, grace=1)
            return alive, child
        alive, child = asyncio.run(go())
        self.assertTrue(alive)
        self.assertTrue(gone(child))

    def test_hanging_up_the_last_call_schedules_the_idle_stop(self):
        class Proc:
            returncode = None

        async def go():
            voice.server.proc = Proc()
            voice.calls["c1"] = voice.Call(id="c1", bot_id="default", voice="cove")
            scheduled = []
            with mock.patch.object(voice.server, "call", lambda *a, **k: asyncio.sleep(0, {})), \
                    mock.patch.object(voice.server, "idle_soon", lambda: scheduled.append(True)), \
                    mock.patch.object(voice, "_wrap_up", lambda call: asyncio.sleep(0)), \
                    mock.patch.object(voice.calllog, "write", lambda *a, **k: False):
                await voice.stop("c1", reason="test")
            return scheduled
        self.assertEqual(asyncio.run(go()), [True])

    def test_answer_after_the_server_went_away_is_dropped(self):
        class Gone:
            returncode = 0

        async def go():
            voice.server.proc = None
            await voice.server._answer(Gone(), 7, "item/tool/call", {}, None, "ask_bot")
        asyncio.run(go())


class AccountsCheck(unittest.TestCase):
    def test_chatgpt_check_leaves_no_app_server_running(self):
        tmp = Path(tempfile.mkdtemp())
        exe = launcher(tmp)

        async def go():
            with mock.patch.object(accounts, "CHECK_TIMEOUT_SEC", 0.5):
                with self.assertRaises(asyncio.TimeoutError):
                    await accounts._codex_installed(str(exe))
            return await child_pid(tmp)
        child = asyncio.run(go())
        self.assertTrue(gone(child), "the check left the app server running")



def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()
