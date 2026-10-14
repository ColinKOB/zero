"""On-Device Voice calls (ondevice.py): the reply stream, speculative turns, the hand-off to the Zip, news, what
the call saves, and the Claude command line the plan broker accepts.

A fake `claude` (written to a temporary folder) speaks the stream-json protocol, so nothing here reaches a model.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien:
~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import asyncio
import importlib.util
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import briefing, calllog, config, health, ondevice, store, turns, voice, who  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BROKER = REPO / "alien" / "broker" / "zero_broker.py"
REAL_ASK_HAIKU = briefing.ask_haiku

FAKE = textwrap.dedent('''\
    import json, os, sys, time
    log = open(os.environ["FAKE_CLAUDE_LOG"], "a")
    log.write(json.dumps({"argv": sys.argv[1:], "api_key": "ANTHROPIC_API_KEY" in os.environ}) + "\\n"); log.flush()
    def out(ev):
        print(json.dumps(ev), flush=True)
    final = None
    for line in sys.stdin:
        f = json.loads(line)
        log.write(json.dumps({"frame": f}) + "\\n"); log.flush()
        if f["type"] == "user" and f.get("shouldQuery") is False:
            out({"type": "result", "subtype": "success", "num_turns": 0, "is_error": False})
        elif f["type"] == "user":
            final = f["message"]["content"][0]["text"]
    if final is None:
        sys.exit(0)
    if "SLOW" in final:
        time.sleep(30)
    if "FAILAUTH" in final:
        out({"type": "assistant", "error": "authentication_failed",
             "message": {"content": [{"type": "text", "text": "Not logged in. Please run /login"}]}})
        out({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1})
        sys.exit(1)
    def delta(t):
        out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}})
    if "[News" not in final and ("email" in final.lower() or "add milk" in final.lower()):
        name = "update_job" if "add milk" in final.lower() else "ask_bot"
        arg = {"note": "Add milk."} if name == "update_job" else {"request": "Check the email from Theo and say what he wants."}
        delta("One sec.")
        out({"type": "assistant", "message": {"content": [{"type": "text", "text": "One sec."},
             {"type": "tool_use", "id": "t1", "name": "mcp__hermes__" + name, "input": arg}]}})
        out({"type": "stream_event", "event": {"type": "message_stop"}})
        out({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 1})
        sys.exit(1)
    if "NOTEXT" in final:
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "mcp__hermes__ask_bot",
             "input": {"request": "Look it up."}}]}})
        out({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 1})
        sys.exit(1)
    reply = "Theo wants the slides by Friday. Want me to draft a reply?" if "[News" in final else "Sure thing. Tacos at six sound great."
    half = len(reply) // 2
    delta(reply[:half]); delta(reply[half:])
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}})
    out({"type": "stream_event", "event": {"type": "message_stop"}})
    out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1})
''')


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB)
        self.tmp = Path(tempfile.mkdtemp())
        config.HUB_DIR = self.tmp
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        calllog._open.clear()
        fake = self.tmp / "claude"
        fake.write_text(f"#!{sys.executable}\n" + FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = self.tmp / "claude-runs.jsonl"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        self.real = (ondevice.claude_exe, briefing.for_call, briefing.ask_haiku, turns.run_internal,
                     briefing.refresh_soon, voice.server.proc, voice.ask_bot, voice.update_job)
        ondevice.claude_exe = lambda: str(fake)

        async def for_call(bot_id):
            return "You are Zero. BRIEFING-MARK: dinner plans are open."
        briefing.for_call = for_call

        async def haiku(prompt, timeout=60):
            return "You talked about dinner.\n\n- **Dinner:** Tacos at six."
        briefing.ask_haiku = haiku

        async def run_internal(bot_id, text, continues=None):
            return "Saved."
        turns.run_internal = run_internal
        briefing.refresh_soon = lambda bot_id: None
        voice.server.proc = None
        self.asked = []

        async def ask_bot(call, request):
            self.asked.append(("ask_bot", request))
            return "Theo wants the slides by Friday."
        voice.ask_bot = ask_bot

        async def update_job(call, note):
            self.asked.append(("update_job", note))
            return "Passed on to the job."
        voice.update_job = update_job

    def tearDown(self):
        (ondevice.claude_exe, briefing.for_call, briefing.ask_haiku, turns.run_internal, briefing.refresh_soon,
         voice.server.proc, voice.ask_bot, voice.update_job) = self.real
        for call in list(voice.calls.values()):
            if call.engine == ondevice.ENGINE:
                ondevice.closed(call)
        voice.calls.clear()
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False
        calllog._open.clear()
        os.environ.pop("FAKE_CLAUDE_LOG", None)

    def lines(self, call_id):
        path = next(p for p in (self.tmp / "calls").glob("*/*.jsonl") if p.stem == call_id)
        return [json.loads(l) for l in path.read_text().splitlines()]

    def runs(self):
        """Each fake Claude run: its argument list and the frames it was sent."""
        out = []
        if not self.log.exists():
            return out
        for line in self.log.read_text().splitlines():
            row = json.loads(line)
            if "argv" in row:
                out.append({"argv": row["argv"], "api_key": row["api_key"], "frames": []})
            elif out:
                out[-1]["frames"].append(row["frame"])
        return out

    def asked_runs(self):
        """Runs that asked the model (their last frame has no shouldQuery: False)."""
        return [r for r in self.runs() if r["frames"] and r["frames"][-1].get("shouldQuery") is not False
                and r["frames"][-1]["type"] == "user"]

    @staticmethod
    async def collect(gen):
        return [ev async for ev in gen]

    @staticmethod
    async def settle(seconds=0.4):
        await asyncio.sleep(seconds)


class Shape(Base):
    def test_command_line_is_the_one_the_plan_broker_allows(self):
        if not BROKER.exists():
            self.skipTest("the broker is not next to the hub here")
        spec = importlib.util.spec_from_file_location("zero_broker_for_test", BROKER)
        broker = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = broker     # dataclasses look the module up while it loads
        try:
            spec.loader.exec_module(broker)
        finally:
            sys.modules.pop(spec.name, None)
        tmp = self.tmp / "run"
        tmp.mkdir()
        args = ondevice.argv("claude", tmp)
        files = {str(tmp / "system.md"): "You are Zero.",
                 str(tmp / "settings.json"): json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps(ondevice.request_body())}}),
                 str(tmp / "tools.json"): json.dumps(ondevice.tool_manifest())}
        req = broker.parse_claude(args[1:], files, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "600"})
        self.assertEqual(req.shape, "directsdk")
        self.assertEqual(req.model, ondevice.MODEL)
        self.assertEqual(req.effort, "low")
        self.assertEqual({t["name"] for t in req.tools}, {"ask_bot", "update_job", "work_status"})
        body = json.loads(req.extra_body)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual({t["name"] for t in body["tools"]},
                         {"mcp__hermes__ask_bot", "mcp__hermes__update_job", "mcp__hermes__work_status"})
        self.assertNotIn("temperature", body)

    def test_shared_home_runs_start_without_owner_context(self):
        """A home on a shared plan reaches Claude only through the broker. What the broker runs for the hub's two
        model calls (this voice and briefing.ask_haiku) carries no CLAUDE.md, memory, settings or account email of
        the owner's: the switches are set, the folder is neutral, and the request goes through its filter."""
        if not BROKER.exists():
            self.skipTest("the broker is not next to the hub here")
        spec = importlib.util.spec_from_file_location("zero_broker_for_test", BROKER)
        broker = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = broker
        try:
            spec.loader.exec_module(broker)
        finally:
            sys.modules.pop(spec.name, None)
        tmp = self.tmp / "run"
        tmp.mkdir()
        files = {str(tmp / "system.md"): "You are Zero.",
                 str(tmp / "settings.json"): json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps(ondevice.request_body())}}),
                 str(tmp / "tools.json"): json.dumps(ondevice.tool_manifest())}
        seen = []

        async def fake_exec(*args, **kwargs):
            seen.append(list(args))
            raise FileNotFoundError("stop here")

        orig = briefing.asyncio.create_subprocess_exec
        briefing.asyncio.create_subprocess_exec = fake_exec
        try:
            with self.assertRaises(FileNotFoundError):
                asyncio.run(REAL_ASK_HAIKU("Write the briefing."))
        finally:
            briefing.asyncio.create_subprocess_exec = orig
        hub_env = dict(ondevice._env(), CLAUDE_CONFIG_DIR="/home/owner/.claude", CLAUDE_CODE_DISABLE_CLAUDE_MDS="0")
        for args in (ondevice.argv("claude", tmp), seen[0]):
            req = broker.parse_claude(args[1:], files, hub_env)
            self.assertIn(req.shape, ("directsdk", "oneshot"))
            run_dir = Path("/owner/.zero-broker/tmp/run1")
            env = broker.claude_env({}, req, Path("/owner/.zero-broker/homes/riley/claude"), Path("/owner"),
                                    "/owner/.local/bin/claude", "http://127.0.0.1:1/ctx/x")
            for k, v in broker.NO_OWNER_CONTEXT_ENV.items():
                self.assertEqual(env[k], v)
            self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/owner/.zero-broker/homes/riley/claude")
            self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:1/ctx/x")
            run = broker.claude_argv(req, "/owner/.local/bin/claude", run_dir, "/py", Path("/srv/zero/broker/inert_mcp.py"))
            self.assertEqual(run[run.index("--setting-sources") + 1], "")
            self.assertIn("--disable-slash-commands", run)
            settings = json.loads(broker.claude_files(req)["settings.json"])
            self.assertFalse(settings["autoMemoryEnabled"])
            self.assertEqual(broker.Config().claude_cwd, "/")

    def test_plan_sign_in_only_never_an_api_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-real"
        os.environ["ANTHROPIC_BASE_URL"] = "https://example.invalid"
        try:
            env = ondevice._env()
        finally:
            os.environ.pop("ANTHROPIC_API_KEY")
            os.environ.pop("ANTHROPIC_BASE_URL")
        self.assertFalse(any(k.startswith("ANTHROPIC_") for k in env))
        self.assertEqual(env["HOME"], str(config.HOME))

    def test_prompt_has_the_briefing_the_rules_and_the_hand_off(self):
        p = ondevice.prompt("default", "BRIEFING-MARK")
        first = who.first()
        self.assertTrue(p.startswith("BRIEFING-MARK"))
        self.assertIn("ask_bot", p)
        self.assertIn("update_job", p)
        self.assertIn("yes-or-no", p)
        self.assertIn("Never ask an either-or question", p)
        self.assertIn("two to six words", p)
        self.assertIn('Never say "Oh, man"', p)
        self.assertIn(f"live voice call with {first}", p)
        self.assertNotIn("{", p.replace("{" + "}", ""))

    def test_history_holds_confirmed_finished_turns_with_what_was_heard(self):
        local = ondevice.Local(greeting="Hey there, what's up?")
        a = ondevice.Turn(n=1, kind="words", text="Dinner ideas?", speculative=False, reply="Tacos. Want a recipe?",
                          confirmed=True, done=True)
        b = ondevice.Turn(n=2, kind="words", text="Not this one", speculative=True, reply="x", done=True)
        c = ondevice.Turn(n=3, kind="words", text="Check my email", speculative=False, reply="One sec.",
                          confirmed=True, done=True, handoff=("ask_bot", "Check email."))
        d = ondevice.Turn(n=4, kind="news", text="Theo wants slides.", speculative=False,
                          reply="Theo wants the slides by Friday and more.", confirmed=True, done=True,
                          spoken="Theo wants the slides", cut_off=True)
        local.turns = {1: a, 2: b, 3: c, 4: d}
        msgs = ondevice.merge(ondevice.history(local) + [("user", "Thanks")])
        replay, final = ondevice.split(msgs)
        texts = [t for _, t in replay]
        self.assertEqual(replay[0], ("user", ondevice.CONNECTED))
        self.assertNotIn("Not this one", " ".join(texts))
        self.assertIn("Theo wants the slides (cut off)", texts)
        self.assertTrue(any(t.startswith("[You handed this") and "Check email." in t and ondevice.NEWS_MARK in t
                            for t in texts))
        self.assertEqual(final, "Thanks")
        roles = [r for r, _ in replay]
        self.assertTrue(all(x != y for x, y in zip(roles, roles[1:])))
        self.assertEqual(roles[-1], "assistant")

    def test_shared_plan_apology_names_the_shared_plan(self):
        from taffy_hub import home
        line = ondevice._sorry(ondevice.RunError("x", signed_out=True))
        self.assertEqual(line, ondevice.SORRY_SHARED if home.plan_kind() == "shared" else ondevice.SORRY_SIGNED_OUT)
        self.assertEqual(ondevice._sorry(RuntimeError("boom")), ondevice.SORRY)


class Flow(Base):
    def test_a_turn_streams_and_saves_like_a_chatgpt_call(self):
        async def go():
            call = await ondevice.start("default", "alba", "chosen")
            await voice.greet(call.id)
            evs = await self.collect(ondevice.turn(call, 1, text="What should I have for dinner?"))
            await self.settle()
            ondevice.said(call, 1, "Sure thing. Tacos at six sound great.", first_audio_ms=1450)
            await voice.stop(call.id, reason="the app ended it")
            await self.settle(0.3)
            return call, evs
        call, evs = asyncio.run(go())
        texts = [e["text"] for e in evs if e["type"] == "text"]
        self.assertGreaterEqual(len(texts), 2)
        self.assertEqual("".join(texts), "Sure thing. Tacos at six sound great.")
        self.assertEqual(evs[-1]["type"], "done")
        lines = self.lines(call.id)
        start = lines[0]
        self.assertEqual((start["event"], start["engine"], start["voice"], start["reason"]),
                         ("start", "on-device", "alba", "chosen"))
        self.assertEqual(start["reply_model"], ondevice.MODEL)
        events = [l["event"] for l in lines]
        for e in ("greeting", "line", "reply", "first_reply", "end", "summary"):
            self.assertIn(e, events)
        said = [l for l in lines if l["event"] == "line"]
        self.assertEqual(said[0]["text"], call.local.greeting)
        self.assertEqual(said[1]["who"], who.first())
        self.assertEqual(said[1]["text"], "What should I have for dinner?")
        self.assertEqual(said[2]["text"], "Sure thing. Tacos at six sound great.")
        self.assertEqual(next(l for l in lines if l["event"] == "first_reply")["ms_after_speech"], 1450)
        self.assertEqual(call.transcript[1], (who.first(), "What should I have for dinner?"))
        runs = self.asked_runs()
        self.assertTrue(runs)
        self.assertFalse(any(r["api_key"] for r in self.runs()))
        system = next(a for a in runs[0]["argv"] if a.endswith("system.md"))
        self.assertTrue(system)

    def test_speculative_hand_off_waits_for_confirm_and_news_comes_back(self):
        async def go():
            call = await ondevice.start("default", None)
            evs = await self.collect(ondevice.turn(call, 1, text="Check my email from Theo", speculative=True))
            await self.settle(0.3)
            before = list(self.asked)
            self.assertTrue(ondevice.confirm(call, 1))
            await self.settle(2.5)
            news = None
            while not call.events.empty():
                ev = call.events.get_nowait()
                if ev["type"] == "news":
                    news = ev
            reply = None
            if news:
                reply = await self.collect(ondevice.turn(call, 2, news=news["id"]))
            await voice.stop(call.id)
            return call, evs, before, news, reply
        call, evs, before, news, reply = asyncio.run(go())
        self.assertEqual("".join(e["text"] for e in evs if e["type"] == "text"), "One sec.")
        self.assertEqual(evs[-1]["handoff"], "ask_bot")
        self.assertEqual(before, [])
        self.assertEqual(self.asked, [("ask_bot", "Check the email from Theo and say what he wants.")])
        self.assertIsNotNone(news)
        self.assertIn("slides", "".join(e["text"] for e in reply if e["type"] == "text"))
        last = self.asked_runs()[-1]
        asked = last["frames"][-1]["message"]["content"][0]["text"]
        self.assertIn(ondevice.NEWS_MARK + " Theo wants the slides by Friday.", asked)
        replayed = " ".join(f["message"]["content"][0]["text"] for f in last["frames"][:-1])
        self.assertIn("Check my email from Theo", replayed)
        self.assertIn("[You handed this", asked + replayed)
        tools = [l for l in self.lines(call.id) if l["event"] == "tool"]
        self.assertEqual([t["name"] for t in tools], ["ask_bot"])

    def test_update_job_goes_to_the_running_job(self):
        async def go():
            call = await ondevice.start("default", None)
            await self.collect(ondevice.turn(call, 1, text="Oh and add milk too"))
            await self.settle(0.8)
            await voice.stop(call.id)
        asyncio.run(go())
        self.assertEqual(self.asked, [("update_job", "Add milk.")])

    def test_replaced_speculation_is_never_acted_on_or_remembered(self):
        async def go():
            call = await ondevice.start("default", None)
            await self.collect(ondevice.turn(call, 1, text="Check my email", speculative=True))
            evs = await self.collect(ondevice.turn(call, 1, text="What should I cook", speculative=False))
            await self.settle(0.5)
            await self.collect(ondevice.turn(call, 2, text="Thanks"))
            await voice.stop(call.id)
            return call, evs
        call, evs = asyncio.run(go())
        self.assertEqual(self.asked, [])
        last = self.asked_runs()[-1]
        replayed = " ".join(f["message"]["content"][0]["text"] for f in last["frames"])
        self.assertNotIn("Check my email", replayed)
        self.assertIn("What should I cook", replayed)
        persons = [t for w, t in call.transcript if w == who.first()]
        self.assertEqual(persons, ["What should I cook", "Thanks"])

    def test_a_cancelled_speculative_reply_stops_claude(self):
        async def go():
            call = await ondevice.start("default", None)
            gen = ondevice.turn(call, 1, text="SLOW question", speculative=True)
            task = asyncio.create_task(self.collect(gen))
            await asyncio.sleep(1.5)
            t = call.local.turns[1]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await gen.aclose()
            await self.settle(0.3)
            stopped = t.task.done()
            in_history = any("SLOW" in text for _, text in ondevice.history(call.local))
            await voice.stop(call.id)
            return stopped, in_history
        stopped, in_history = asyncio.run(go())
        self.assertTrue(stopped)
        self.assertFalse(in_history)

    def test_the_next_turn_uses_claude_started_ahead_with_the_history(self):
        async def go():
            call = await ondevice.start("default", None)
            await self.collect(ondevice.turn(call, 1, text="What should I have for dinner?"))
            await self.settle(1.0)
            warm = call.local.warm
            await self.collect(ondevice.turn(call, 2, text="And for dessert?"))
            await self.settle(0.3)
            await voice.stop(call.id)
            return call, warm
        call, warm = asyncio.run(go())
        self.assertIsNotNone(warm)
        replies = [l for l in self.lines(call.id) if l["event"] == "reply"]
        self.assertEqual([r["turn"] for r in replies], [1, 2])
        self.assertTrue(replies[1]["warm"])
        self.assertIn("ms_first_text", replies[1])

    def test_a_hand_off_with_no_words_still_says_one_sec(self):
        async def go():
            call = await ondevice.start("default", None)
            evs = await self.collect(ondevice.turn(call, 1, text="NOTEXT please"))
            await self.settle(0.5)
            await voice.stop(call.id)
            return evs
        evs = asyncio.run(go())
        self.assertEqual("".join(e["text"] for e in evs if e["type"] == "text"), "One sec.")
        self.assertEqual(self.asked, [("ask_bot", "Look it up.")])

    def test_a_failed_reply_is_an_apology_and_a_saved_error(self):
        async def go():
            call = await ondevice.start("default", None)
            evs = await self.collect(ondevice.turn(call, 1, text="FAILAUTH hello"))
            await voice.stop(call.id)
            return call, evs
        call, evs = asyncio.run(go())
        spoken = "".join(e["text"] for e in evs if e["type"] == "text")
        self.assertIn(spoken, (ondevice.SORRY_SIGNED_OUT, ondevice.SORRY_SHARED))
        self.assertTrue(evs[-1].get("error"))
        errors = [l for l in self.lines(call.id) if l["event"] == "error"]
        self.assertEqual(errors[0]["stage"], "reply")

    def test_cut_off_reply_is_remembered_as_heard(self):
        async def go():
            call = await ondevice.start("default", None)
            await self.collect(ondevice.turn(call, 1, text="What should I have for dinner?"))
            ondevice.said(call, 1, "Sure thing.", cut_off=True)
            msgs = ondevice.history(call.local)
            await voice.stop(call.id)
            return call, msgs
        call, msgs = asyncio.run(go())
        self.assertIn(("assistant", "Sure thing. (cut off)"), msgs)
        self.assertIn("interrupted", [l["event"] for l in self.lines(call.id)])

    def test_stop_ends_every_claude_process(self):
        async def go():
            call = await ondevice.start("default", None)
            await self.collect(ondevice.turn(call, 1, text="What should I have for dinner?"))
            await self.settle(1.0)
            warm = call.local.warm
            proc = warm.proc if warm else None
            await voice.stop(call.id)
            await self.settle(0.5)
            return call, proc
        call, proc = asyncio.run(go())
        self.assertTrue(call.local.closed)
        self.assertIsNone(call.local.warm)
        if proc is not None:
            self.assertIsNotNone(proc.returncode)


class Engines(Base):
    def setUp(self):
        super().setUp()
        self.voice_ok = health.status.get("voice", True)

    def tearDown(self):
        health.status["voice"] = self.voice_ok
        super().tearDown()

    def test_chatgpt_voice_is_off_when_signed_out_or_minutes_are_used(self):
        health.status["voice"] = True
        self.assertEqual(voice.engines()["chatgpt"], {"ok": True, "reason": None, "minutes_left": None})
        store.set_setting(voice.MINUTES_SETTING, 0)
        self.assertEqual(voice.engines()["chatgpt"]["reason"], "minutes")
        store.set_setting(voice.MINUTES_SETTING, 14)
        self.assertEqual(voice.engines()["chatgpt"], {"ok": True, "reason": None, "minutes_left": 14})
        health.status["voice"] = False
        self.assertEqual(voice.engines()["chatgpt"]["reason"], "signed_out")
        self.assertTrue(voice.engines()["on_device"]["ok"])
        self.assertEqual(voice.engines()["greeting"], voice.greeting())

    def test_a_chatgpt_call_at_the_limit_says_so(self):
        store.set_setting(voice.MINUTES_SETTING, 0)
        with self.assertRaises(voice.MinutesUsed):
            asyncio.run(voice.start("default", "v=0", None))
        self.assertEqual(voice.start_error(voice.MinutesUsed("x")), voice.MINUTES_USED)


class Endpoints(Base):
    def setUp(self):
        super().setUp()
        from test_onboarding import _load_app
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.app.app.dependency_overrides.clear()
        self.client = TestClient(self.app.app)
        self.client.__enter__()      # one event loop for the whole test, so a call's tasks live between requests
        code = store.new_pairing_code()
        self.token = store.redeem_pairing_code(code, "Test iPhone")
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        for call in list(voice.calls.values()):
            if call.engine == ondevice.ENGINE:
                ondevice.closed(call)
        voice.calls.clear()
        self.client.__exit__(None, None, None)
        super().tearDown()

    def test_needs_a_paired_device(self):
        self.assertEqual(self.client.post("/api/bots/default/voice/local", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/voice/od-x/turn", json={"n": 1, "text": "hi"}).status_code, 401)
        self.assertEqual(self.client.get("/api/voice/engines").status_code, 401)

    def test_a_call_over_http(self):
        r = self.client.post("/api/bots/default/voice/local", json={"voice": "george", "reason": "signed_out"},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["engine"], "on-device")
        self.assertEqual(body["voice"], "george")
        self.assertEqual(body["greeting"], voice.greeting())
        cid = body["call_id"]
        self.assertEqual(self.client.post(f"/api/voice/{cid}/ready", headers=self.auth).status_code, 200)
        r = self.client.post(f"/api/voice/{cid}/turn", json={"n": 1, "text": "What's for dinner?", "speculative": True},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
        evs = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
        self.assertEqual("".join(e["text"] for e in evs if e["type"] == "text"), "Sure thing. Tacos at six sound great.")
        self.assertEqual(self.client.post(f"/api/voice/{cid}/confirm", json={"n": 1}, headers=self.auth).json(),
                         {"ok": True})
        self.assertEqual(self.client.post(f"/api/voice/{cid}/said", headers=self.auth,
                                          json={"n": 1, "spoken": "Sure thing.", "cut_off": True,
                                                "first_audio_ms": 1200}).json(), {"ok": True})
        self.assertEqual(self.client.post(f"/api/voice/{cid}/confirm", json={"n": 9}, headers=self.auth).json(),
                         {"ok": False})
        self.assertEqual(self.client.post(f"/api/voice/{cid}/turn", json={"n": 2}, headers=self.auth).status_code, 400)
        self.assertEqual(self.client.post(f"/api/voice/{cid}/stop", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post(f"/api/voice/{cid}/turn", json={"n": 2, "text": "hi"},
                                          headers=self.auth).status_code, 404)
        events = [l["event"] for l in self.lines(cid)]
        self.assertEqual(events[0], "start")
        for e in ("greeting", "line", "reply", "interrupted", "first_reply", "end"):
            self.assertIn(e, events)

    def test_engines_and_a_chatgpt_call_at_the_limit(self):
        store.set_setting(voice.MINUTES_SETTING, 0)
        r = self.client.get("/api/voice/engines", headers=self.auth)
        self.assertEqual(r.json()["chatgpt"]["reason"], "minutes")
        r = self.client.post("/api/bots/default/voice", json={"sdp": "v=0\r\n"}, headers=self.auth)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], voice.MINUTES_USED)

    def test_an_unknown_or_chatgpt_call_has_no_turns(self):
        voice.calls["chatgpt1"] = voice.Call(id="chatgpt1", bot_id="default", voice="cove")
        r = self.client.post("/api/voice/chatgpt1/turn", json={"n": 1, "text": "hi"}, headers=self.auth)
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
