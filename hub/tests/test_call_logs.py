"""Saved calls (calllog.py): what a call writes as it happens, the off switch, the 30-day pass, the app's
diagnostics endpoint and its auth, and the zero-calls command on alien.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien:
~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import briefing, calllog, config, store, turns, voice  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
COMMAND = REPO / "alien" / "bin" / "zero-calls"


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB)
        self.tmp = Path(tempfile.mkdtemp())
        config.HUB_DIR = self.tmp
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        calllog._open.clear()

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False
        calllog._open.clear()

    def files(self):
        return sorted((self.tmp / "calls").glob("*/*.jsonl")) if (self.tmp / "calls").is_dir() else []

    def lines(self, call_id):
        path = next(p for p in self.files() if p.stem == call_id)
        return [json.loads(l) for l in path.read_text().splitlines()]


class CallCase(Base):
    def setUp(self):
        super().setUp()
        self.real = (briefing.ask_haiku, turns.run_internal, briefing.refresh_soon, voice.server.proc)

        async def haiku(prompt, timeout=60):
            return "You asked about dinner.\n\n- **Dinner:** Tacos at 6."
        briefing.ask_haiku = haiku

        async def run_internal(bot_id, text, continues=None):
            return "Saved."
        turns.run_internal = run_internal
        briefing.refresh_soon = lambda bot_id: None
        voice.server.proc = None

    def tearDown(self):
        briefing.ask_haiku, turns.run_internal, briefing.refresh_soon, voice.server.proc = self.real
        voice.calls.clear()
        super().tearDown()

    def run_call(self, call_id="019a-test-call"):
        """A short call as the hub sees it: hello, Colin talks, the voice answers, he talks over it, hangs up."""
        async def go():
            call = voice.Call(id=call_id, bot_id="default", voice="cove")
            voice.calls[call_id] = call
            calllog.begin(call_id, at=call.started, bot="default", bot_name="Zero", voice="cove",
                          engine=voice.ENGINE, relay_model=voice.RELAY_MODEL, route=voice.ROUTE)
            spoken = []

            async def speak(c, text):
                spoken.append(text)
            real_speak = voice.speak
            voice.speak = speak
            try:
                await voice.greet(call_id)
                await voice.on_event(call, "thread/realtime/outputAudio/delta", {})
                await voice.on_event(call, "thread/realtime/transcript/done",
                                     {"role": "assistant", "text": "Hey Colin. What's up?"})
                voice.set_talking(call_id, True)
                await asyncio.sleep(0.05)
                voice.set_talking(call_id, False)
                await voice.on_event(call, "thread/realtime/transcript/done",
                                     {"role": "user", "text": "What's for dinner tonight?"})
                await asyncio.sleep(0.05)
                await voice.on_event(call, "thread/realtime/transcript/delta", {"role": "assistant", "delta": "Ta"})
                await voice.on_event(call, "thread/realtime/transcript/done",
                                     {"role": "assistant", "text": "Tacos at six."})
                voice.set_talking(call_id, True)
                voice.set_talking(call_id, False)
                await voice.on_event(call, "thread/realtime/transcript/done", {"role": "user", "text": "Great, thanks."})
                await voice.on_event(call, "thread/realtime/speechStarted", {})
                await voice.on_event(call, "thread/realtime/speechStarted", {})
                voice.set_away(call_id, True)
                voice.set_away(call_id, False)
                call.started -= 40
                await voice.stop(call_id, reason="the app ended it")
                for _ in range(50):
                    await asyncio.sleep(0.02)
                    if any(json.loads(l)["event"] == "summary" for p in self.files() for l in p.read_text().splitlines()):
                        break
            finally:
                voice.speak = real_speak
        asyncio.run(go())


class Saving(CallCase):
    def test_a_call_is_saved_line_by_line_with_eastern_times(self):
        self.run_call()
        files = self.files()
        self.assertEqual(len(files), 1)
        path = files[0]
        today_et = datetime.now(calllog.ET).strftime("%Y-%m-%d")
        self.assertEqual(path.parent.name, today_et)
        self.assertEqual(path.name, "019a-test-call.jsonl")
        events = self.lines("019a-test-call")
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(events[0]["bot_name"], "Zero")
        self.assertEqual(events[0]["voice"], "cove")
        self.assertEqual(events[0]["engine"], "chatgpt-realtime")
        for e in events:
            t = datetime.fromisoformat(e["t"])
            self.assertIsNotNone(t.utcoffset())
            self.assertIn(t.utcoffset(), (timedelta(hours=-4), timedelta(hours=-5)))
            self.assertEqual(e["from"], "hub")
        said = [(e["who"], e["text"]) for e in events if e["event"] == "line"]
        lead = config.BOTS_BY_ID["default"].name
        self.assertEqual(said, [(lead, "Hey Colin. What's up?"), ("Colin", "What's for dinner tonight?"),
                                (lead, "Tacos at six."), ("Colin", "Great, thanks.")])
        firsts = [e for e in events if e["event"] == "first_reply"]
        self.assertIn("ms_after_greeting", firsts[0])
        self.assertIn("ms_after_mic", firsts[1])
        self.assertIn("ms_after_words", firsts[1])
        self.assertGreaterEqual(firsts[1]["ms_after_mic"], firsts[1]["ms_after_words"])
        self.assertIn("interrupted", kinds)
        self.assertEqual([e["on"] for e in events if e["event"] == "hold"], [True, False])
        self.assertEqual(kinds.count("other_event"), 1)
        end = next(e for e in events if e["event"] == "end")
        self.assertEqual(end["reason"], "the app ended it")
        self.assertEqual(end["colin_lines"], 2)
        self.assertIn("duration", end)
        summary = next(e for e in events if e["event"] == "summary")
        self.assertIn("Tacos at 6", summary["text"])
        self.assertLess(kinds.index("end"), kinds.index("summary"))

    def test_words_heard_after_the_reply_began_are_not_a_new_wait(self):
        call = voice.Call(id="c3", bot_id="default", voice="cove")
        voice.calls["c3"] = call
        calllog.begin("c3", bot="default")

        async def go():
            voice.set_talking("c3", True)
            voice.set_talking("c3", False)
            await voice.on_event(call, "thread/realtime/outputAudio/delta", {})
            await voice.on_event(call, "thread/realtime/transcript/done", {"role": "user", "text": "Hi there"})
            await voice.on_event(call, "thread/realtime/outputAudio/delta", {})
        asyncio.run(go())
        firsts = [e for e in self.lines("c3") if e["event"] == "first_reply"]
        self.assertEqual(len(firsts), 1)
        self.assertIn("ms_after_mic", firsts[0])

    def test_each_line_is_on_disk_as_soon_as_it_is_written(self):
        calllog.begin("c1", bot="default")
        calllog.write("c1", "line", who="Colin", text="Hello")
        self.assertEqual([e["event"] for e in self.lines("c1")], ["start", "line"])
        calllog.write("c1", "error", stage="voice", message="boom")
        self.assertEqual(self.lines("c1")[-1]["message"], "boom")

    def test_tool_calls_are_saved_in_short_form(self):
        call = voice.Call(id="c2", bot_id="default", voice="cove")
        voice.calls["c2"] = call
        calllog.begin("c2", bot="default")

        class Pipe:
            def __init__(self):
                self.sent = []

            def write(self, data):
                self.sent.append(data)

            async def drain(self):
                pass

        class Proc:
            stdin = Pipe()
            returncode = None
        real_ask, real_proc = voice.ask_bot, voice.server.proc

        async def ask(c, request):
            return "The weather is sunny. " * 40
        voice.ask_bot = ask
        voice.server.proc = Proc()
        try:
            asyncio.run(voice.server._serve(7, "item/tool/call", {"threadId": "c2", "tool": "ask_bot",
                                                                  "arguments": {"request": "Weather today?"}}))
        finally:
            voice.ask_bot, voice.server.proc = real_ask, real_proc
        tool = next(e for e in self.lines("c2") if e["event"] == "tool")
        self.assertEqual(tool["name"], "ask_bot")
        self.assertEqual(tool["asked"], "Weather today?")
        self.assertLessEqual(len(tool["result"]), calllog.SHORT)
        self.assertIn("secs", tool)

    def test_a_runaway_call_stops_growing_but_still_ends(self):
        real = calllog.MAX_BYTES
        calllog.MAX_BYTES = 2000
        try:
            calllog.begin("big", bot="default")
            for i in range(100):
                calllog.write("big", "line", who="Colin", text="x" * 100)
            calllog.write("big", "end", duration=5)
        finally:
            calllog.MAX_BYTES = real
        events = self.lines("big")
        self.assertLess(len(events), 30)
        self.assertEqual(events[-1]["event"], "end")

    def test_bad_ids_never_make_paths(self):
        for bad in ["../x", "a/b", "", ".hidden", "x" * 200]:
            self.assertFalse(calllog.begin(bad, bot="default"))
        self.assertEqual(self.files(), [])


class OffSwitch(CallCase):
    def test_nothing_is_saved_when_off(self):
        self.assertTrue(calllog.enabled())
        calllog.set_enabled(False)
        self.run_call("off-call")
        self.assertEqual(self.files(), [])
        self.assertFalse((self.tmp / "calls").exists())
        self.assertEqual(calllog.app_events("app-12345678-aaaa", [{"event": "system_call", "used": "fallback"}]), 0)
        self.assertEqual(self.files(), [])

    def test_turning_it_off_mid_call_stops_the_file(self):
        calllog.begin("mid", bot="default")
        calllog.set_enabled(False)
        calllog.write("mid", "line", who="Colin", text="Secret plans")
        calllog.set_enabled(True)
        self.assertEqual([e["event"] for e in self.lines("mid")], ["start"])


class Retention(Base):
    def test_days_older_than_thirty_are_deleted(self):
        today = date(2026, 10, 8)
        root = self.tmp / "calls"
        for days in (0, 1, 29, 30, 31, 45):
            d = root / (today - timedelta(days=days)).isoformat()
            d.mkdir(parents=True)
            (d / "c.jsonl").write_text("{}\n")
        (root / "notes").mkdir()
        removed = calllog.prune(today)
        self.assertEqual(removed, 2)
        left = sorted(p.name for p in root.iterdir())
        self.assertEqual(left, ["2026-09-08", "2026-09-09", "2026-10-07", "2026-10-08", "notes"])

    def test_nothing_to_prune(self):
        self.assertEqual(calllog.prune(), 0)


class Endpoint(Base):
    def setUp(self):
        super().setUp()
        from test_onboarding import _load_app
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.app.app.dependency_overrides.clear()
        self.client = TestClient(self.app.app)
        code = store.new_pairing_code()
        self.token = store.redeem_pairing_code(code, "Test iPhone")
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def post(self, call_id, events, headers=None):
        return self.client.post(f"/api/voice/{call_id}/diagnostics", json={"events": events},
                                headers=self.auth if headers is None else headers)

    def test_needs_a_paired_device(self):
        calllog.begin("hubcall1", bot="default")
        ev = [{"event": "system_call", "used": "fallback", "at": time.time()}]
        self.assertEqual(self.post("hubcall1", ev, headers={}).status_code, 401)
        self.assertEqual(self.post("hubcall1", ev, headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertEqual([e["event"] for e in self.lines("hubcall1")], ["start"])
        r = self.post("hubcall1", ev)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["saved"], 1)

    def test_app_events_join_the_hubs_call(self):
        calllog.begin("hubcall2", bot="default")
        now = time.time()
        r = self.post("hubcall2", [
            {"event": "system_call", "used": "fallback", "accepted": False, "at": now,
             "error_domain": "com.apple.CallKit.error.requesttransaction", "error_code": 1,
             "error_description": "The operation couldn't be completed."},
            {"event": "route", "output": "BluetoothHFP", "output_name": "AirPods", "at": now + 1,
             "source": "callkit", "call_id": "other"},
            {"event": "Bad Name", "at": now},
            {"event": "timing", "step": "x" * 900, "nested": {"a": 1}, "../path": 1, "at": now + 2},
            "not an event",
        ])
        self.assertEqual(r.json()["saved"], 3)
        events = self.lines("hubcall2")
        sysc = next(e for e in events if e["event"] == "system_call")
        self.assertEqual(sysc["from"], "app")
        self.assertEqual(sysc["error_code"], 1)
        self.assertEqual(sysc["error_domain"], "com.apple.CallKit.error.requesttransaction")
        route = next(e for e in events if e["event"] == "route")
        self.assertEqual((route["source"], route["call_id"], route["from"]), ("callkit", "other", "app"))
        timing = next(e for e in events if e["event"] == "timing")
        self.assertLessEqual(len(timing["step"]), 500)
        self.assertEqual(timing["nested"], '{"a": 1}')
        self.assertNotIn("../path", timing)

    def test_a_call_that_never_reached_the_hub_gets_its_own_file(self):
        r = self.post("app-0f1e2d3c-4b5a", [{"event": "hub_start_failed", "error": "Can't reach Zero right now.",
                                             "at": time.time()}])
        self.assertEqual(r.json()["saved"], 1)
        events = self.lines("app-0f1e2d3c-4b5a")
        self.assertEqual(events[0]["event"], "start")
        self.assertIn("never reached the hub", events[0]["note"])
        self.assertEqual(self.post("made-up-id", [{"event": "x", "at": time.time()}]).json()["saved"], 0)
        self.assertEqual(self.post("bad.id", [{"event": "x"}]).status_code, 400)

    def test_settings_switch(self):
        r = self.client.get("/api/settings", headers=self.auth)
        self.assertTrue(r.json()["save_call_transcripts"])
        store.set_setting("muted_bots", ["default"])
        r = self.client.put("/api/settings", json={"save_call_transcripts": False}, headers=self.auth)
        self.assertEqual(r.json(), {"muted_bots": ["default"], "save_call_transcripts": False,
                                    "keep_replies_short": True})
        self.assertFalse(calllog.enabled())
        r = self.client.put("/api/settings", json={"muted_bots": []}, headers=self.auth)
        self.assertEqual(r.json(), {"muted_bots": [], "save_call_transcripts": False,
                                    "keep_replies_short": True})
        self.assertEqual(self.client.put("/api/settings", json={"save_call_transcripts": True}).status_code, 401)
        calllog.begin("offcall", bot="default")
        self.assertEqual(self.post("offcall", [{"event": "x", "at": time.time()}]).json()["saved"], 0)
        self.assertEqual(self.files(), [])


@unittest.skipUnless(COMMAND.exists(), "repo-only check (alien runs the hub without the repo)")
class Command(Base):
    def setUp(self):
        super().setUp()
        now = time.time()
        calllog.begin("019abc-fallback", at=now - 300, bot="default", bot_name="Zero", voice="cove",
                      engine="chatgpt-realtime", relay_model="gpt-6-luna", route="codex app-server")
        calllog.app_events("019abc-fallback", [
            {"event": "system_call", "used": "fallback", "accepted": False, "at": now - 299,
             "error_domain": "com.apple.CallKit.error.requesttransaction", "error_code": 1,
             "error_description": "The operation couldn't be completed."},
            {"event": "route", "output": "Receiver", "reason": "start", "at": now - 298}])
        calllog.write("019abc-fallback", "line", at=now - 290, who="Zero", text="Hey Colin. What's up?")
        calllog.write("019abc-fallback", "line", at=now - 285, who="Colin", text="Is it raining?")
        calllog.write("019abc-fallback", "first_reply", at=now - 284, via="audio", ms_after_mic=900, ms_after_words=400)
        calllog.write("019abc-fallback", "tool", at=now - 283, name="ask_bot", asked="Is it raining?",
                      result="No rain today.", secs=3.1)
        calllog.write("019abc-fallback", "error", at=now - 280, stage="voice", message="Realtime session hiccup")
        calllog.write("019abc-fallback", "end", at=now - 200, reason="the app ended it", duration=95, lines=2)
        calllog.write("019abc-fallback", "summary", at=now - 190, text="You asked about rain.\n\n- **Rain:** None.")
        calllog.begin("019def-callkit", at=now - 3600, bot="career", bot_name="Career", voice="sol")
        calllog.app_events("019def-callkit", [{"event": "system_call", "used": "callkit", "accepted": True,
                                               "at": now - 3599}])
        calllog.write("019def-callkit", "line", at=now - 3590, who="Colin", text="Any news?")
        calllog.write("019def-callkit", "end", at=now - 3500, duration=61)
        old = now - 10 * 86400
        calllog.begin("019old-call", at=old, bot="default", bot_name="Zero", voice="cove")

    def run_cmd(self, *args):
        env = dict(os.environ, ZERO_CALLS_DIR=str(self.tmp / "calls"))
        return subprocess.run([sys.executable, str(COMMAND), *args], capture_output=True, text=True, env=env,
                              timeout=30)

    def test_list(self):
        r = self.run_cmd("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l for l in r.stdout.splitlines() if l.startswith("019")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("019abc-fal"))
        self.assertIn("1:35", rows[0])
        self.assertIn("fallback", rows[0])
        self.assertIn("1 error: Realtime session hiccup", rows[0])
        self.assertIn("CallKit", rows[1])
        self.assertIn("1:01", rows[1])
        self.assertIn("none", rows[1])
        self.assertRegex(rows[0], r"fallback\s+1\s")
        r = self.run_cmd("list", "--days", "30")
        self.assertEqual(len([l for l in r.stdout.splitlines() if l.startswith("019")]), 3)

    def test_show_last_and_by_prefix(self):
        r = self.run_cmd("show", "last")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("Call 019abc-fallback with Zero, voice cove", out)
        self.assertIn("Lasted 1:35 (the app ended it).", out)
        self.assertIn("System call: fallback. CallKit refused it: com.apple.CallKit.error.requesttransaction code 1",
                      out)
        self.assertIn("Colin: Is it raining?", out)
        self.assertIn("Zero: Hey Colin. What's up?", out)
        self.assertIn("900 ms after he stopped talking", out)
        self.assertIn("[tool ask_bot] 3.1 s", out)
        self.assertIn("!! hub error", out)
        self.assertIn("Rain:** None.", out)
        self.assertIn("Reply wait after Colin stopped talking: median 900 ms", out)
        self.assertLess(out.index("System call"), out.index("Colin: Is it raining?"))
        self.assertLess(out.index("Hey Colin"), out.index("Colin: Is it raining?"))
        r2 = self.run_cmd("show", "019def")
        self.assertIn("System call: CallKit.", r2.stdout)
        self.assertNotIn("refused", r2.stdout)

    def test_show_unknown(self):
        r = self.run_cmd("show", "nothing-like-this")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No saved call matches that.", r.stderr)
        r = self.run_cmd("show", "019")
        self.assertEqual(r.returncode, 1)
        self.assertIn("More than one call", r.stderr)

    def test_empty(self):
        env = dict(os.environ, ZERO_CALLS_DIR=str(self.tmp / "nowhere"))
        r = subprocess.run([sys.executable, str(COMMAND), "list"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("No saved calls", r.stdout)


@unittest.skipUnless((REPO / "alien" / "bin" / "taffy-backup").exists(), "repo-only check")
class Backup(unittest.TestCase):
    def test_backup_skips_saved_calls(self):
        self.assertIn('hub-state/calls', (REPO / "alien" / "bin" / "taffy-backup").read_text())



def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()
