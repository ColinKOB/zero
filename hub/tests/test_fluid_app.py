"""Fluid app (Oct 9): keep talking to a working Zip, Stop, a plain activity line, honest status in lists, which kinds
of news notify, and While You Were Away. Evidence rows are from docs/research/2026-10-09-grokbot-dots-user-feedback.md.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import activity, board, briefing, choices, config, consent, hermes, notices, notifyprefs, store, turns  # noqa: E402
from test_onboarding import _fresh_store, _load_app, _real_reload  # noqa: E402


class Labels(unittest.TestCase):
    """#4 and the Notion designer point: a compact line in plain words, never a tool name or a log."""

    def test_plain_words_with_the_useful_detail(self):
        self.assertEqual(activity.label("web_search", {"query": "Lakeside eLearning syllabus POL 101"}),
                         "Searching the web for “Lakeside eLearning syllabus POL 101”")
        self.assertEqual(activity.label("web_extract", {"urls": ["https://www.example.edu/syllabus.pdf"]}),
                         "Reading example.edu")
        self.assertEqual(activity.label("browser_exec", {"code": "await goto('https://elearning.lakeside.edu/my')"}),
                         "Opening elearning.lakeside.edu")
        self.assertEqual(activity.label("browser_exec", {"code": "click('Sign in')"}), "Using its browser")
        self.assertEqual(activity.label("email", {"action": "search"}), "Checking your email")
        self.assertEqual(activity.label("email", {"action": "draft"}), "Writing an email")
        self.assertEqual(activity.label("calendar", {"action": "create"}), "Updating your calendar")
        self.assertEqual(activity.label("browser_vault_fill", {}), "Signing in")
        self.assertEqual(activity.label("mac_open", {}), "Using your Mac")

    def test_teammates_by_name(self):
        name = config.BOTS_BY_ID["money"].name if "money" in config.BOTS_BY_ID else None
        if name:
            self.assertEqual(activity.label("ask_teammate", {"bot": "money", "question": "x"}), f"Asking {name}")
        self.assertEqual(activity.label("ask_teammate", {"bot": "nobody-here"}), "Asking a teammate")

    def test_never_a_tool_name(self):
        for tool in ("some_new_tool", "kanban_reassign", "browser_get_images", "terminal", "skill_view"):
            text = activity.label(tool, {})
            self.assertNotIn("_", text)
            self.assertNotIn(tool, text)
        self.assertEqual(activity.label("some_new_tool", {}), "Working on it")
        self.assertEqual(hermes._tool_label("some_new_tool", None), "Working on it")

    def test_quiet_calls_have_no_line(self):
        for tool in ("offer_choices", "react", "todo_list", "_thinking", None, ""):
            self.assertIsNone(activity.label(tool, {}), tool)


class Live(unittest.TestCase):
    def setUp(self):
        activity.reset()

    def test_steps_stall_and_end(self):
        activity.expect("school", "What is due this week?")
        activity.begin("school")
        for ev in ({"type": "run", "id": "run_1"}, {"type": "status", "text": "Opening elearning.lakeside.edu"},
                   {"type": "status", "text": "Opening elearning.lakeside.edu"}, {"type": "status", "text": "Signing in"},
                   {"type": "delta", "text": "x"}):
            activity.note("school", ev)
        live = activity.live("school")
        self.assertEqual(live["steps"], ["Opening elearning.lakeside.edu", "Signing in"])
        self.assertFalse(live["stalled"])
        self.assertEqual((live["source"], live["words"]), ("app", "What is due this week?"))
        self.assertEqual(activity.run_id("school"), "run_1")
        later = time.time() + activity.STALL_SEC + 1
        self.assertTrue(activity.live("school", later)["stalled"])
        activity.end("school")
        self.assertIsNone(activity.live("school"))

    def test_hub_turns_have_no_words_to_retry(self):
        activity.begin("default")
        self.assertEqual(activity.live("default")["source"], "hub")
        self.assertNotIn("words", activity.live("default"))

    def test_steps_are_capped(self):
        activity.begin("builder")
        for i in range(20):
            activity.note("builder", {"type": "status", "text": f"Step {i}"})
        self.assertEqual(len(activity.live("builder")["steps"]), activity.MAX_STEPS)
        self.assertEqual(activity.live("builder")["steps"][-1], "Step 19")

    def test_failed_reply_is_kept_until_the_next_turn(self):
        activity.finished("money", "That reply ran into a problem. Try again in a moment.", "Any bills this week?")
        self.assertEqual(activity.outcome("money")["words"], "Any bills this week?")
        self.assertIsNone(activity.outcome("money", time.time() + activity.STALE_SEC + 1))
        activity.finished("money", "x", "y")
        activity.begin("money")
        self.assertIsNotNone(activity.outcome("money"))
        activity.expect("money", "Try again")
        activity.begin("money")
        self.assertIsNone(activity.outcome("money"))
        activity.finished("money", "x", "y", stopped=True)
        self.assertIsNone(activity.outcome("money"))

    def test_stream_turn_reports_the_chat_lane_only(self):
        seen = []

        async def fake(bot_id, text, lane):
            seen.append(activity.live(bot_id))
            yield {"type": "run", "id": "run_9"}
            yield {"type": "status", "text": "Checking your email"}
            seen.append(activity.live(bot_id))
            yield {"type": "reply", "text": "Done."}
            yield {"type": "done"}

        async def go(lane=None):
            return [ev async for ev in hermes.stream_turn("clients", "hi", lane=lane)]

        with mock.patch.object(hermes, "_stream_turn", fake):
            events = asyncio.run(go())
            self.assertEqual([e["type"] for e in events], ["run", "status", "reply", "done"])
            self.assertEqual(seen[1]["steps"], ["Checking your email"])
            self.assertIsNone(activity.live("clients"))
            seen.clear()
            asyncio.run(go(lane="team"))
            self.assertEqual(seen, [None, None])


class _Turns(unittest.TestCase):
    """The real turns._run with Hermes and the side effects stubbed."""

    def setUp(self):
        activity.reset()
        turns._held.clear()
        self.stopped_runs = []
        self.patches = [
            mock.patch.object(hermes, "ensure_session", self._session),
            mock.patch.object(hermes, "stop", self._stop),
            mock.patch.object(consent, "open_pass", lambda *a, **k: None),
            mock.patch.object(consent, "close_message_passes", lambda *a, **k: None),
            mock.patch.object(store, "session_for", lambda *a: "sid"),
            mock.patch.object(store, "add_feed", lambda *a, **k: 1),
            mock.patch.object(store, "take_untold_reactions", lambda *a: []),
            mock.patch.object(choices, "flush", lambda *a: None),
            mock.patch.object(notices, "chat_activity", lambda *a: None),
            mock.patch.object(briefing, "refresh_soon", lambda *a: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        for d in (turns._turns, turns._queued, turns._held):
            d.clear()
        activity.reset()

    async def _session(self, *a, **k):
        return "sid"

    async def _stop(self, bot_id, run_id):
        self.stopped_runs.append((bot_id, run_id))
        self.release.set()
        return True


class QueueAndStop(_Turns):
    def slow_stream(self, reply="Here you go."):
        self.release = asyncio.Event()

        async def _gen():
            yield {"type": "run", "id": "run_1"}
            activity.note("default", {"type": "run", "id": "run_1"})
            yield {"type": "status", "text": "Checking the board"}
            try:
                await asyncio.wait_for(self.release.wait(), 2)
            except TimeoutError:
                pass
            if self.stopped_runs:
                yield {"type": "error", "text": "That reply stopped partway."}
            else:
                yield {"type": "reply", "text": reply}
            yield {"type": "done"}

        async def wrapped(bot_id, text, team=False, lane=None):
            activity.begin(bot_id)
            try:
                async for ev in _gen():
                    yield ev
            finally:
                activity.end(bot_id)
        return wrapped

    def test_a_message_to_a_working_zip_is_queued_visibly_then_runs(self):
        """#57: the person can keep typing to a working Zip, and sees that the message waits."""
        async def go():
            with mock.patch.object(hermes, "stream_turn", self.slow_stream()):
                first = turns.send("default", "Plan my week")
                await asyncio.sleep(0.05)
                second = turns.send("default", "Also check the dentist time")
                await asyncio.sleep(0.05)
                self.assertEqual(turns.queued_counts(), {"default": 1})
                self.assertEqual(second.events[0], {"type": "queued", "position": 1, "text": turns.QUEUED_LINE})
                self.release.set()
                await asyncio.wait_for(self._done(second), 5)
                types = [e["type"] for e in second.events]
                self.assertEqual(types[:2], ["queued", "started"])
                self.assertIn("reply", types)
                self.assertEqual(turns.queued_counts(), {})
                self.assertTrue(first.done)
        asyncio.run(go())

    async def _done(self, turn):
        while not turn.done:
            await asyncio.sleep(0.02)

    def test_stop_interrupts_and_hands_back_queued_messages(self):
        """#55, #56: Stop stops, and the person's queued messages come back instead of starting a new turn."""
        async def go():
            with mock.patch.object(hermes, "stream_turn", self.slow_stream()):
                first = turns.send("default", "Plan my week")
                await asyncio.sleep(0.05)
                second = turns.send("default", "Also check the dentist time")
                await asyncio.sleep(0.05)
                out = await turns.stop("default")
                self.assertTrue(out["stopped"])
                self.assertEqual(out["returned"], ["Also check the dentist time"])
                self.assertEqual(self.stopped_runs, [("default", "run_1")])
                await asyncio.wait_for(self._done(first), 5)
                types = [e["type"] for e in first.events]
                self.assertIn("stopped", types)
                self.assertNotIn("error", types)
                self.assertEqual(second.events[-2]["type"], "stopped")
                self.assertTrue(second.events[-2]["returned"])
                await asyncio.sleep(0.7)
                self.assertNotIn("started", [e["type"] for e in second.events])
                self.assertIsNone(activity.outcome("default"))
        asyncio.run(go())

    def test_stop_before_hermes_names_the_run(self):
        async def go():
            async def stream(bot_id, text, team=False, lane=None):
                await asyncio.sleep(0.1)
                yield {"type": "run", "id": "run_late"}
                yield {"type": "error", "text": "That reply stopped partway."}
                yield {"type": "done"}
            with mock.patch.object(hermes, "stream_turn", stream):
                self.release = asyncio.Event()
                t = turns.send("money", "Any bills?")
                await asyncio.sleep(0.01)
                await turns.stop("money")
                await asyncio.wait_for(self._done(t), 3)
                self.assertIn(("money", "run_late"), self.stopped_runs)
                self.assertIn("stopped", [e["type"] for e in t.events])
        asyncio.run(go())

    def test_stopped_comes_before_done_when_the_stream_just_ends(self):
        """Night review: a stopped run whose stream ends without an error event still tells the open chat it
        stopped. The "stopped" line used to be pushed after "done", which every live follower had already
        returned on."""
        async def go():
            release = asyncio.Event()

            async def stream(bot_id, text, team=False, lane=None):
                yield {"type": "run", "id": "run_quiet"}
                await release.wait()
                yield {"type": "done"}

            async def stop(bot_id, run_id):
                self.stopped_runs.append((bot_id, run_id))
                release.set()
                return True
            with mock.patch.object(hermes, "stream_turn", stream), mock.patch.object(hermes, "stop", stop):
                t = turns.send("builder", "Ship it")
                seen = []

                async def follow():
                    async for ev in t.follow():
                        if ev is not None:
                            seen.append(ev["type"])
                follower = asyncio.create_task(follow())
                await asyncio.sleep(0.05)
                await turns.stop("builder")
                await asyncio.wait_for(follower, 3)
                self.assertIn("stopped", seen)
                self.assertEqual(seen[-1], "done")
                self.assertNotIn("error", seen)
        asyncio.run(go())

    def test_stop_after_the_reply_arrived_keeps_the_reply(self):
        """The app treats a turn with "stopped" as stopped and does not show its reply; a reply that already
        arrived is the answer, so no "Stopped." goes over it."""
        async def go():
            release = asyncio.Event()

            async def stream(bot_id, text, team=False, lane=None):
                yield {"type": "run", "id": "run_done"}
                yield {"type": "reply", "text": "Done, it shipped."}
                await release.wait()
                yield {"type": "done"}

            async def stop(bot_id, run_id):
                release.set()
                return False
            with mock.patch.object(hermes, "stream_turn", stream), mock.patch.object(hermes, "stop", stop):
                t = turns.send("builder", "Ship it")
                await asyncio.sleep(0.05)
                await turns.stop("builder")
                await asyncio.wait_for(self._done(t), 3)
                types = [e["type"] for e in t.events]
                self.assertIn("reply", types)
                self.assertNotIn("stopped", types)
        asyncio.run(go())

    def test_only_typed_words_close_a_sign_in_card(self):
        """Night review: "okay, go ahead" said on a call (a hand-off's consent_text) closed an open sign-in card as
        signed in and recorded an allowed sign-in, because the call's words reached signin.done_in_chat like a
        typed message. Only a message the person typed in the chat answers the card."""
        from taffy_hub import signin
        seen = []

        async def go():
            async def stream(bot_id, text, team=False, lane=None):
                yield {"type": "reply", "text": "ok"}
                yield {"type": "done"}
            with mock.patch.object(hermes, "stream_turn", stream), \
                    mock.patch.object(signin, "done_in_chat", lambda bot, words: seen.append(words)):
                t = turns.start("money", "[From a voice call] ... Request from the call: go ahead",
                                consent_text="Okay, go ahead.")
                await asyncio.wait_for(self._done(t), 3)
                self.assertEqual(seen, [])
                t = turns.send("money", "I'm in, try again")
                await asyncio.wait_for(self._done(t), 3)
                self.assertEqual(seen, ["I'm in, try again"])
        asyncio.run(go())

    def test_hub_work_waits_after_stop_until_the_person_talks_again(self):
        """#55: other Zips' news must not restart a stopped Zip silently."""
        ran = []

        async def go():
            async def run_turn(bot_id, text, team=False):
                ran.append(text)
                return "ok"
            with mock.patch.object(hermes, "run_turn", run_turn):
                await turns.stop("default")
                self.assertTrue(turns.held("default"))
                task = asyncio.create_task(turns.run_internal("default", "[Board update] Builder finished"))
                await asyncio.sleep(0.7)
                self.assertEqual(ran, [])
                await turns.run_internal("default", "[Hub note] allowed", continues="sid")
                self.assertEqual(ran, ["[Hub note] allowed"])
                turns.release("default")
                await asyncio.wait_for(task, 3)
                self.assertEqual(ran, ["[Hub note] allowed", "[Board update] Builder finished"])
        asyncio.run(go())

    def test_a_new_message_releases_the_hold_and_the_hold_runs_out(self):
        async def go():
            async def stream(bot_id, text, team=False, lane=None):
                yield {"type": "reply", "text": "ok"}
                yield {"type": "done"}
            with mock.patch.object(hermes, "stream_turn", stream):
                await turns.stop("career")
                t = turns.send("career", "Never mind, go ahead")
                self.assertFalse(turns.held("career"))
                await asyncio.wait_for(self._done(t), 3)
            turns._held["career"] = time.time() - 1
            self.assertFalse(turns.held("career"))
        asyncio.run(go())

    def test_stop_with_nothing_running(self):
        out = asyncio.run(turns.stop("builder"))
        self.assertEqual((out["stopped"], out["returned"]), (False, []))
        self.assertEqual(self.stopped_runs, [])

    def test_a_failed_reply_is_recorded_with_the_words_and_a_reply_clears_it(self):
        """#52: a plain failure with Retry, not "sent and read" with nothing after."""
        async def go(events):
            async def stream(bot_id, text, team=False, lane=None):
                for ev in events:
                    yield ev
            with mock.patch.object(hermes, "stream_turn", stream):
                t = turns.send("school", "What is due?")
                await asyncio.wait_for(self._done(t), 3)
        asyncio.run(go([{"type": "error", "text": "That reply ran into a problem. Try again in a moment."},
                        {"type": "done"}]))
        o = activity.outcome("school")
        self.assertEqual((o["state"], o["words"]), ("failed", "What is due?"))
        asyncio.run(go([{"type": "reply", "text": "Form A, tomorrow."}, {"type": "done"}]))
        self.assertIsNone(activity.outcome("school"))


class Kinds(unittest.TestCase):
    def setUp(self):
        self.saved = (config.HUB_DIR, config.HUB_DB)
        _fresh_store(Path(tempfile.mkdtemp()))

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False

    def test_every_kind_starts_on(self):
        self.assertEqual(notifyprefs.kinds_for("builder"), {"needs": True, "done": True, "progress": True})
        self.assertTrue(notifyprefs.allows("builder", "progress"))
        self.assertTrue(notifyprefs.allows("builder", None))

    def test_turning_kinds_off_and_on(self):
        notifyprefs.set_kinds("builder", progress=False, done=None)
        self.assertEqual(notifyprefs.kinds_for("builder"), {"needs": True, "done": True, "progress": False})
        self.assertFalse(notifyprefs.allows("builder", "progress"))
        self.assertTrue(notifyprefs.allows("career", "progress"))
        notifyprefs.set_kinds("builder", progress=True)
        self.assertEqual(store.get_setting("notify_kinds_off"), {})
        with self.assertRaises(ValueError):
            notifyprefs.set_kinds("builder", loud=True)

    def test_the_master_switch_comes_first(self):
        store.set_notify("money", False)
        self.assertFalse(notifyprefs.allows("money", "needs"))
        self.assertEqual(notifyprefs.kinds_for("money")["needs"], True)


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY, config.LOOKS,
                      config.reload_bots, list(config.BOTS), dict(config.BOTS_BY_ID), board.bot_load, board.grouped,
                      consent.pending, self.app._last_message)
        tmp = Path(tempfile.mkdtemp())
        _fresh_store(tmp)
        config.HERMES_HOME = tmp / "hermes"
        for b in (config.BUILTIN + config.OWNER_ZIPS):
            (config.HERMES_HOME / "profiles" / b.id).mkdir(parents=True, exist_ok=True)
        config.NAMES = tmp / "bot-names.json"
        config.REGISTRY = tmp / "bots.json"
        config.LOOKS = tmp / "bot-looks.json"
        config.migrate_registry()
        config.reload_bots = _real_reload
        config._registry_mtime = None
        config.reload_bots()
        board.bot_load = lambda: {}
        self.needs: list[dict] = []
        board.grouped = lambda: {"needs_you": self.needs, "working": [], "done": []}
        self.pending: list[dict] = []
        consent.pending = lambda: self.pending

        async def no_preview(bot_id):
            return None, None
        self.app._last_message = no_preview
        self.client = TestClient(self.app.app)
        self.phone = {"Authorization": "Bearer " + store.redeem_pairing_code(store.new_pairing_code(), "Phone")}
        activity.reset()
        turns._held.clear()

    def tearDown(self):
        (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME, config.NAMES, config.REGISTRY, config.LOOKS,
         config.reload_bots, bots, by_id, board.bot_load, board.grouped, consent.pending,
         self.app._last_message) = self.saved
        config.BOTS[:] = bots
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id)
        store._local.conn = None
        store._schema_ready = False
        activity.reset()
        turns._held.clear()

    def bots(self):
        r = self.client.get("/api/bots", headers=self.phone)
        self.assertEqual(r.status_code, 200, r.text)
        return {b["id"]: b for b in r.json()["bots"]}

    def a_zip(self):
        return next(b for b in config.BOTS if b.id != "default")

    def test_no_status_when_nothing_is_wrong(self):
        listed = self.bots()
        self.assertTrue(listed)
        self.assertTrue(all(b["status"] is None for b in listed.values()))
        self.assertTrue(all(b["notify_kinds"] == {"needs": True, "done": True, "progress": True}
                            for b in listed.values()))

    def test_paused_for_an_okay(self):
        """#43: a clear paused state on the Zip while its card waits."""
        z = self.a_zip()
        self.pending = [{"id": 4, "bot_id": z.id, "site": "dana@x.example", "kind": "send",
                         "reason": "Email Dana about repo access"},
                        {"id": 5, "bot_id": z.id, "site": "trello.com", "kind": "signin", "reason": ""}]
        st = self.bots()[z.id]["status"]
        self.assertEqual(st["state"], "paused")
        self.assertEqual(st["line"], "Paused for your okay: email Dana about repo access, and 1 more.")

    def test_waiting_for_a_sign_in_card(self):
        z = self.a_zip()
        store.add_feed("login", z.id, None, "Sign in",
                       json.dumps({"url": "https://www.salesforce.com/login", "site": "salesforce.com", "state": "open"}))
        st = self.bots()[z.id]["status"]
        self.assertEqual((st["state"], st["line"]), ("paused", "Waiting for you to sign in to salesforce.com."))

    def test_blocked_shows_why_in_one_line(self):
        """#53: say when it is blocked, and why."""
        z = self.a_zip()
        self.needs = [{"id": "t_1", "assignee": z.id, "status": "blocked", "title": "Sign in to Lakeside eLearning",
                       "block_reason": "I need you to sign in once. URL: https://elearning.lakeside.edu Then I can read the syllabus."}]
        st = self.bots()[z.id]["status"]
        self.assertEqual((st["state"], st["line"]), ("blocked", "Blocked: I need you to sign in once."))

    def test_failed_reply_offers_the_words_to_retry(self):
        z = self.a_zip()
        activity.finished(z.id, "That reply ran into a problem. Try again in a moment.", "What is due?")
        st = self.bots()[z.id]["status"]
        self.assertEqual(st["state"], "failed")
        self.assertEqual(st["line"], "Its last reply didn't finish.")
        self.assertEqual(st["words"], "What is due?")

    def test_an_expired_card_offers_ask_again_until_the_next_turn(self):
        """#38, #39: a card that ran out of time says so, and the row offers to ask again."""
        z = self.a_zip()
        store.add_feed("signin_decision", z.id, None, "Email Dana about repo access",
                       consent.NO_ANSWER_TITLE + f", so {z.name} didn't do this.")
        st = self.bots()[z.id]["status"]
        self.assertEqual((st["state"], st["what"], st["line"]),
                         ("expired", "Email Dana about repo access", "Its card closed with no answer."))
        activity._began[z.id] = time.time() + 5
        self.assertIsNone(self.bots()[z.id]["status"])

    def test_stop_endpoint(self):
        z = self.a_zip()
        r = self.client.post(f"/api/bots/{z.id}/stop", headers=self.phone)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True, "stopped": False, "returned": []})
        self.assertTrue(turns.held(z.id))
        self.assertEqual(self.client.post("/api/bots/nobody/stop", headers=self.phone).status_code, 404)
        self.assertEqual(self.client.post(f"/api/bots/{z.id}/stop").status_code, 401)

    def test_notify_kinds_endpoint(self):
        z = self.a_zip()
        r = self.client.put(f"/api/bots/{z.id}/notify-kinds", json={"progress": False}, headers=self.phone)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["notify_kinds"], {"needs": True, "done": True, "progress": False})
        self.assertEqual(self.bots()[z.id]["notify_kinds"]["progress"], False)
        self.assertEqual(self.client.put(f"/api/bots/{z.id}/notify-kinds", json={"progress": "maybe"},
                                         headers=self.phone).status_code, 422)
        self.assertEqual(self.client.put("/api/bots/nobody/notify-kinds", json={}, headers=self.phone).status_code, 404)

    def test_feed_carries_live_activity_and_queue(self):
        z = self.a_zip()
        activity.expect(z.id, "Check the syllabus")
        activity.begin(z.id)
        activity.note(z.id, {"type": "status", "text": "Opening elearning.lakeside.edu"})
        r = self.client.get("/api/feed", headers=self.phone)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["activity"][z.id]["steps"], ["Opening elearning.lakeside.edu"])
        self.assertFalse(body["activity"][z.id]["stalled"])
        self.assertEqual(body["queued"], {})

    def test_while_you_were_away(self):
        """#75, #76: only when there is something to say, one line per Zip, what needs the person first."""
        since = time.time() - 60
        self.assertEqual(self.client.get(f"/api/away?since={since}", headers=self.phone).json()["items"], [])
        z = self.a_zip()
        store.add_feed("checkin", z.id, None, "Daily spending update", "All quiet.")
        self.assertEqual(self.client.get(f"/api/away?since={since}", headers=self.phone).json()["items"], [])
        store.add_feed("task_done", z.id, "t_1", f"{z.name} finished", "Draft reply to Dana\n\nDrafted and saved.")
        store.add_feed("task_blocked", "default", "t_2", "Zero is blocked", "Book the dentist\n\nWhich day works?")
        store.add_feed("signin_decision", z.id, None, "Email Dana about repo access",
                       consent.NO_ANSWER_TITLE + ", so it didn't do this.")
        items = self.client.get(f"/api/away?since={since}", headers=self.phone).json()["items"]
        self.assertEqual([i["bot_id"] for i in items], ["default", z.id] if items[0]["bot_id"] == "default" else
                         [z.id, "default"])
        line = {i["bot_id"]: i["line"] for i in items}
        self.assertEqual(line["default"], "Needs you: Book the dentist.")
        self.assertEqual(line[z.id], "A card closed with no answer: Email Dana about repo access. "
                                     "Finished: Draft reply to Dana.")
        self.assertTrue(all("—" not in i["line"] for i in items))
        old = self.client.get(f"/api/away?since={time.time() + 60}", headers=self.phone).json()["items"]
        self.assertEqual(old, [])


if __name__ == "__main__":
    unittest.main()
