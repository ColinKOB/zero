"""Usage and cache work (docs/qa/2026-10-10-usage-cache.md): a chat stays the same chat when Hermes compacts it.

Hermes compacts in place by default: the session id stays, the summarized messages are archived, every kept message
is copied to a new row with a new id, old tool results in the copies are shortened, and the summary is a hidden row.
Or it rotates: the chat moves to a child session and the Zip's tools send the child's id. Nothing here reaches
Hermes or the network.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, history, lineage, store  # noqa: E402
from taffy_hub import consent as hub_consent  # noqa: E402
from taffy_hub import hermes as hub_hermes  # noqa: E402
import plugin_loader  # noqa: E402

tt = plugin_loader.load()

BLOCK = ("Not done yet: send this needs Colin's yes for this exact action. A card showing it is now in his app "
         "(request 5). Reply to Colin with only one short plain line.")


def _state_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model_config TEXT, parent_session_id TEXT,
                               started_at REAL NOT NULL, ended_at REAL, end_reason TEXT);
    """)
    return conn


def _session(conn, sid, parent=None, end_reason=None, source="api_server", model_config=None):
    conn.execute("INSERT INTO sessions (id, source, model_config, parent_session_id, started_at, end_reason) "
                 "VALUES (?, ?, ?, ?, 1.0, ?)", (sid, source, model_config, parent, end_reason))
    conn.commit()


class Isolated(unittest.TestCase):
    """A temporary hub store and Hermes home: Zero's state.db holds a chat that compaction rotated twice."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME)
        config.HUB_DIR = self.tmp
        config.HUB_DB = self.tmp / "hub.db"
        config.HERMES_HOME = self.tmp / "taffy"
        store._local.conn = None
        store._schema_ready = False
        lineage._cache.clear()
        self.zero = _state_db(config.HERMES_HOME / "state.db")
        _session(self.zero, "chat", end_reason="compression")
        _session(self.zero, "chat-2", parent="chat", end_reason="compression")
        _session(self.zero, "chat-3", parent="chat-2")
        _session(self.zero, "team", end_reason="compression")
        _session(self.zero, "team-2", parent="team")
        # Children that are their own conversation: a branch, and a tool's session.
        _session(self.zero, "branch", parent="chat", model_config=json.dumps({"_branched_from": "chat"}))
        _session(self.zero, "tool-run", parent="chat", source="tool")

    def tearDown(self):
        self.zero.close()
        config.HUB_DIR, config.HUB_DB, config.HERMES_HOME = self.saved
        store._local.conn = None
        store._schema_ready = False
        lineage._cache.clear()


class Rotation(Isolated):
    def test_any_session_of_a_chain_maps_to_the_one_the_hub_stored(self):
        self.assertEqual(lineage.root("default", "chat-3"), "chat")
        self.assertEqual(lineage.root("default", "chat-2"), "chat")
        self.assertEqual(lineage.root("default", "chat"), "chat")
        self.assertEqual(lineage.root(None, "team-2"), "team")       # without the bot, every Zip's store is read
        self.assertTrue(lineage.same("default", "chat", "chat-3"))
        self.assertFalse(lineage.same("default", "chat-3", "team-2"))

    def test_a_branch_or_a_tools_session_is_its_own_conversation(self):
        self.assertEqual(lineage.root("default", "branch"), "branch")
        self.assertEqual(lineage.root("default", "tool-run"), "tool-run")
        self.assertEqual(lineage.root("default", "unknown"), "unknown")
        self.assertEqual(lineage.root("default", ""), "")

    def test_a_card_raised_before_the_move_still_marks_the_note_beside_it(self):
        raw = [{"id": 1, "role": "user", "content": "Tell Theo Thursday works."},
               {"id": 2, "role": "assistant", "content": "Waiting for your okay on the card.",
                "tool_calls": [{"id": "call_1", "function": {"name": "terminal"}}]},
               {"id": 3, "role": "tool", "tool_call_id": "call_1", "content": json.dumps({"error": BLOCK})},
               {"id": 4, "role": "assistant", "content": "Waiting for your okay on the card."}]
        card = {5: {"id": 5, "bot_id": "default", "session_id": "chat"}}
        with mock.patch.object(hub_consent, "get_request", lambda req: card.get(req)):
            shown = history.visible(hub_hermes._shown(raw, "default", "chat-3"))
            self.assertEqual([m["display"] for m in shown].count("Waiting for your okay on the card."), 1)
            # A card of another conversation still doesn't count.
            shown = history.visible(hub_hermes._shown(raw, "default", "team-2"))
            self.assertEqual([m["display"] for m in shown].count("Waiting for your okay on the card."), 2)

    def test_the_tools_child_session_is_compared_as_the_stored_chat(self):
        from test_onboarding import _load_app
        hub_app = _load_app()
        if hub_app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        self.assertEqual(hub_app.MacBody(bot_id="default", session_id="chat-3").session_id, "chat")
        self.assertEqual(hub_app.MacBody(bot_id="nobody", session_id="team-2").session_id, "team")
        self.assertEqual(hub_app.MacBody(bot_id="default", session_id="").session_id, "")
        # A teammate's question in the moved team session is still a teammate's question.
        store.set_session("default#team", "team")
        from taffy_hub import team
        self.assertTrue(team.in_team_session(hub_app.MacBody(bot_id="default", session_id="team-2").session_id))


class InPlace(Isolated):
    """Measured in a copy of Zero's chat (Oct 10): every kept message came back with a new id, and old tool results
    were shortened so they no longer name their card."""

    def raw(self, ids, tool_result, hidden_summary=False):
        out = [{"id": ids[0], "role": "user", "content": "Tell Theo Thursday works.", "timestamp": 100.5},
               {"id": ids[1], "role": "assistant", "content": "Waiting for your okay on the card.", "timestamp": 101.25,
                "tool_calls": [{"id": "call_1", "function": {"name": "terminal"}}]},
               {"id": ids[2], "role": "tool", "tool_call_id": "call_1", "content": tool_result, "timestamp": 101.5},
               {"id": ids[3], "role": "assistant", "content": "Waiting for your okay on the card.", "timestamp": 102.0}]
        if hidden_summary:   # Hermes's API sends the summary row with no text and display_kind "hidden"
            out.insert(0, {"id": 99, "role": "assistant", "content": "Summary of earlier turns: Colin is in Ohio.",
                           "display_kind": "hidden", "timestamp": 50.0})
        return out

    def read(self, raw):
        class Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": raw}

        async def fake_session(bot_id, fresh=False, team=False):
            return "chat"

        seen = {}

        async def fake_get(bot_id, sid, raw_limit):
            return Response()

        card = {5: {"id": 5, "bot_id": "default", "session_id": "chat"}}
        with mock.patch.object(hub_hermes, "ensure_session", fake_session), \
                mock.patch.object(hub_hermes, "_get_messages", fake_get), \
                mock.patch.object(hub_consent, "get_request", lambda req: card.get(req)):
            return asyncio.run(hub_hermes.messages("default")), seen

    def test_the_app_keeps_each_messages_first_id_and_its_card_marking(self):
        first, _ = self.read(self.raw([1, 2, 3, 4], json.dumps({"error": BLOCK})))
        self.assertEqual([m["id"] for m in first], [1, 2, 4])
        self.assertEqual([m["display"] for m in history.visible(first)].count("Waiting for your okay on the card."), 1)
        store.set_reaction("default", "4", "colin", "like")
        # After compaction: new ids, the tool result shortened to a pointer, and the hidden summary row.
        after, _ = self.read(self.raw([416, 417, 418, 419], "[terminal] command=send (736 chars result)", True))
        self.assertEqual([m["id"] for m in after], [1, 2, 4])
        visible = [m["display"] for m in history.visible(after)]
        self.assertEqual(visible.count("Waiting for your okay on the card."), 1)
        self.assertNotIn("Summary of earlier turns: Colin is in Ohio.", visible)
        self.assertIn("4", store.reactions_for("default"))

    def test_messages_never_seen_keep_their_hermes_ids(self):
        after, _ = self.read(self.raw([416, 417, 418, 419], json.dumps({"output": "sent"})))
        self.assertEqual([m["id"] for m in after], [416, 417, 419])

    def test_the_history_read_asks_for_the_conversation_as_the_person_had_it(self):
        sent = {}

        class Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, params=None):
                sent.update(params or {})
                return "response"

        with mock.patch.object(hub_hermes.httpx, "AsyncClient", Client), \
                mock.patch.object(hub_hermes.config, "api_prefix", lambda b: "http://hermes"), \
                mock.patch.object(hub_hermes, "_headers", lambda b: {}):
            asyncio.run(hub_hermes._get_messages("default", "chat", 400))
        self.assertEqual(sent.get("include_compacted"), "true")
        self.assertEqual(sent.get("order"), "latest")


class PluginLineage(unittest.TestCase):
    """The gate compares the session the Zip's tools send with the hub's; after a rotation that is a child session."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = _state_db(self.tmp / "state.db")
        _session(self.db, "team", end_reason="compression")
        _session(self.db, "team-2", parent="team")
        self.addCleanup(self.db.close)
        tt.lineage._cache.clear()
        self.addCleanup(tt.lineage._cache.clear)
        patcher = mock.patch.object(tt.lineage, "_state_db", lambda: self.tmp / "state.db")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_root(self):
        self.assertEqual(tt.lineage.root("team-2"), "team")
        self.assertEqual(tt.lineage.root("team"), "team")
        self.assertEqual(tt.lineage.root(""), "")

    def test_a_teammates_question_keeps_its_lane_after_the_move(self):
        hub = self.tmp / "hub-state"
        hub.mkdir()
        conn = sqlite3.connect(hub / "hub.db")
        conn.execute("CREATE TABLE bot_sessions (bot_id TEXT PRIMARY KEY, session_id TEXT NOT NULL)")
        conn.execute("INSERT INTO bot_sessions VALUES ('school#team', 'team')")
        conn.commit()
        conn.close()
        with mock.patch.object(tt.gate, "_hub_state", lambda: hub):
            self.assertEqual(tt.gate._lane("team-2"), "team")
            self.assertTrue(tt.gate._in_team_session("team-2"))
        self.assertEqual(tt.gate._fill_key("team-2", ""), "team")   # the password-fill lock holds across the move


if __name__ == "__main__":
    unittest.main()


# ── The usage log, plan readings and keep-warm pings ──────────────────────────────────────────────────────────────

import importlib.util  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
from datetime import datetime  # noqa: E402

from taffy_hub import home, usage  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _noon(hour=12) -> float:
    return datetime(2026, 10, 10, hour, 0, tzinfo=home.TZ).timestamp()


class KeepWarm(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (config.HUB_DIR, config.HUB_DB)
        config.HUB_DIR, config.HUB_DB = self.tmp, self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
        usage._last_turn.clear()
        usage._last_ping.clear()
        usage._paused_until[0] = 0.0
        for p in (mock.patch.object(usage, "on", lambda: True),
                  mock.patch.object(home, "quiet_hours", lambda: (23, 7)),
                  mock.patch.object(usage.turns, "is_busy", lambda b: False)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        config.HUB_DIR, config.HUB_DB = self.saved
        store._local.conn = None
        store._schema_ready = False
        usage._last_turn.clear()
        usage._last_ping.clear()

    def test_first_ping_at_55_minutes_then_every_55_until_three_hours(self):
        start = _noon()
        usage.turn_ended("default", start)
        self.assertEqual(usage.due(start + 30 * 60), [])
        self.assertEqual(usage.due(start + 54 * 60), [])
        self.assertEqual(usage.due(start + 55 * 60), ["default"])
        times = []
        t = start
        while t < start + 4 * 3600:
            if usage.due(t):
                times.append(round((t - start) / 60))
                usage._last_ping["default"] = t
            t += 60
        self.assertEqual(times, [55, 110, 165])           # three pings, then the window has passed

    def test_never_in_quiet_hours_during_a_turn_or_when_off(self):
        late = _noon(22) + 50 * 60                        # 10:50 p.m.: the ping would fall at 11:45 p.m.
        usage.turn_ended("default", late)
        self.assertEqual(usage.due(late + 55 * 60), [])
        usage.turn_ended("default", _noon())
        with mock.patch.object(usage.turns, "is_busy", lambda b: True):
            self.assertEqual(usage.due(_noon() + 56 * 60), [])
        usage.activity._live["default"] = {"waiting": True}
        try:
            self.assertEqual(usage.due(_noon() + 56 * 60), [])   # a run held on a card is still a running turn
        finally:
            usage.activity._live.pop("default", None)
        with mock.patch.object(usage, "on", lambda: False):
            self.assertEqual(usage.due(_noon() + 56 * 60), [])
        self.assertEqual(usage.due(_noon() + 56 * 60), ["default"])

    def test_the_setting_is_on_in_colins_home_and_off_elsewhere(self):
        self.assertTrue(home.merge(home.DEFAULTS, home.COLIN_FACTS)["cache"]["keep_warm"])
        self.assertFalse(home.DEFAULTS["cache"]["keep_warm"])

    def _client(self, status, body, ctype="application/json"):
        sent = []

        class Response:
            status_code = status
            headers = {"content-type": ctype}
            text = body if isinstance(body, str) else json.dumps(body)

            def json(self):
                return body

        class Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                sent.append(url)
                return Response()
        return Client, sent

    def _log(self):
        return [json.loads(line) for f in sorted((self.tmp / "usage").glob("*.jsonl")) for line in f.read_text().splitlines()]

    def test_a_ping_is_logged_and_an_unpatched_hermes_pauses_pings(self):
        store.set_session("default", "chat")
        Client, sent = self._client(200, {"ok": True, "usage": {"cache_read_tokens": 68857, "cache_write_tokens": 101}})
        with mock.patch.object(usage.httpx, "AsyncClient", Client), \
                mock.patch.object(usage.config, "api_prefix", lambda b: "http://hermes"), \
                mock.patch("taffy_hub.hermes._headers", lambda b: {}):
            out = asyncio.run(usage.ping("default"))
        self.assertEqual(sent, ["http://hermes/api/sessions/chat/keepwarm"])
        self.assertEqual(out, {"ok": True, "read": 68857, "write": 101})
        rec = self._log()[-1]
        self.assertEqual((rec["kind"], rec["zip"], rec["read"]), ("keep_warm", "default", 68857))
        Client, sent = self._client(404, "404: Not Found", "text/plain")
        with mock.patch.object(usage.httpx, "AsyncClient", Client), \
                mock.patch.object(usage.config, "api_prefix", lambda b: "http://hermes"), \
                mock.patch("taffy_hub.hermes._headers", lambda b: {}):
            out = asyncio.run(usage.ping("default"))
        self.assertEqual(out, {"skipped": "hermes_without_keepwarm"})
        usage.turn_ended("default", time.time() - 56 * 60)
        self.assertEqual(usage.due(), [])
        # A session Hermes lost is a JSON 404, not a missing endpoint: no pause for that.
        usage._paused_until[0] = 0.0
        Client, sent = self._client(404, {"error": {"code": "session_not_found"}})
        with mock.patch.object(usage.httpx, "AsyncClient", Client), \
                mock.patch.object(usage.config, "api_prefix", lambda b: "http://hermes"), \
                mock.patch("taffy_hub.hermes._headers", lambda b: {}):
            self.assertEqual(asyncio.run(usage.ping("default")), {"failed": 404})
        self.assertEqual(usage._paused_until[0], 0.0)

    def test_plan_reading_only_on_the_homes_own_plan_and_never_with_an_expired_sign_in(self):
        creds = self.tmp / ".claude" / ".credentials.json"
        creds.parent.mkdir()
        creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": (time.time() - 10) * 1000}}))
        called = []

        def fake_open(req, timeout=None):
            called.append(req.full_url)

            class R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self, *a):
                    return json.dumps({"seven_day": {"utilization": 4.0, "resets_at": "x"},
                                       "five_hour": {"utilization": 20.0, "resets_at": "y"}}).encode()
            return R()
        with mock.patch.object(usage, "_credentials", lambda: creds), \
                mock.patch.object(usage.urllib.request, "urlopen", fake_open), \
                mock.patch.object(home, "get", lambda k, d=None: "own" if k == "plan.claude.mode" else d):
            self.assertIsNone(usage.read_plan())             # expired: Claude Code refreshes it, never the hub
            self.assertEqual(called, [])
            creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": (time.time() + 3600) * 1000}}))
            self.assertEqual(usage.read_plan(), {"kind": "plan", "weekly": 4.0, "weekly_resets": "x", "five_hour": 20.0,
                                                 "five_hour_resets": "y"})
        with mock.patch.object(usage, "_credentials", lambda: creds), \
                mock.patch.object(usage.urllib.request, "urlopen", fake_open), \
                mock.patch.object(home, "get", lambda k, d=None: "broker" if k == "plan.claude.mode" else d):
            self.assertIsNone(usage.read_plan())             # a shared plan is the owner's to read
        self.assertEqual(len(called), 1)


class PluginUsageLog(unittest.TestCase):
    """One line per model call, with its origin, and no message text."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        hub = self.tmp / "hub-state"
        hub.mkdir()
        conn = sqlite3.connect(hub / "hub.db")
        conn.execute("CREATE TABLE bot_sessions (bot_id TEXT PRIMARY KEY, session_id TEXT NOT NULL)")
        conn.executemany("INSERT INTO bot_sessions VALUES (?, ?)", [("school", "chat"), ("school#team", "team")])
        conn.commit()
        conn.close()
        self.ul = tt.usage_log
        self.ul._lane_cache.clear()
        for p in (mock.patch.object(tt.gate, "_hub_state", lambda: hub), mock.patch.object(tt.gate, "_bot", lambda: "school"),
                  mock.patch.object(tt.lineage, "root", lambda sid: sid)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.ul._lane_cache.clear)
        self.hub = hub

    def lines(self):
        return [json.loads(line) for f in sorted((self.hub / "usage").glob("*.jsonl")) for line in f.read_text().splitlines()]

    def _fire_post_api_request_hook(self, agent, **kw):   # the name Hermes's own caller has
        self.ul.post_api_request(**kw)

    def test_origins(self):
        u = {"input_tokens": 237, "output_tokens": 4, "cache_read_tokens": 68857, "cache_write_tokens": 101,
             "reasoning_tokens": 0, "prompt_tokens": 69195}
        fork = types.SimpleNamespace(_turn_origin="keep_warm", platform="api_server")
        review = types.SimpleNamespace(_turn_origin="background_review", platform="api_server")
        live = types.SimpleNamespace(platform="api_server")
        self._fire_post_api_request_hook(live, session_id="chat", platform="api_server", model="opus", usage=u,
                                         api_duration=7.8)
        self._fire_post_api_request_hook(fork, session_id="chat", platform="api_server", model="opus", usage=u)
        self._fire_post_api_request_hook(review, session_id="chat", platform="api_server", model="claude-haiku-5-5", usage=u)
        self._fire_post_api_request_hook(live, session_id="team", platform="api_server", model="opus", usage=u)
        self._fire_post_api_request_hook(live, session_id="cron_1", platform="cron", model="opus", usage=u)
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": "t_1"}):
            self._fire_post_api_request_hook(live, session_id="k1", platform="cli", model="opus", usage=u)
        self.ul.post_auxiliary_call(aux_task="compression", session_id="chat", model="claude-haiku-5-5",
                                    usage={"input_tokens": 9000, "output_tokens": 800})
        rows = self.lines()
        self.assertEqual([r["origin"] for r in rows],
                         ["chat", "keep-warm", "background-review", "team", "cron", "board", "compression"])
        first = rows[0]
        self.assertEqual({k: first[k] for k in ("zip", "session", "model", "read", "write", "uncached", "out", "secs")},
                         {"zip": "school", "session": "chat", "model": "opus", "read": 68857, "write": 101,
                          "uncached": 237, "out": 4, "secs": 7.8})
        self.assertEqual(oct(os.stat(next((self.hub / "usage").glob("*.jsonl"))).st_mode & 0o777), "0o600")

    def test_a_logging_failure_never_fails_the_call(self):
        with mock.patch.object(self.ul, "write", side_effect=OSError("disk full")):
            self.ul.post_api_request(session_id="chat", usage={"input_tokens": 1})
            self.ul.post_auxiliary_call(aux_task="title_generation", usage=None)


@unittest.skipUnless((REPO / "alien" / "bin" / "zero-usage").exists(),
                     "repo-only check (the hub's deploy copy has no alien/bin)")
class Summarizer(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_loader("zero_usage", loader=None)
        self.mod = importlib.util.module_from_spec(spec)
        exec(compile((REPO / "alien" / "bin" / "zero-usage").read_text(), "zero-usage", "exec"), self.mod.__dict__)

    def test_weights_and_cold_starts(self):
        warm = {"read": 139036, "write": 930, "uncached": 743, "out": 201}
        coldcall = {"read": 0, "write": 140000, "uncached": 500, "out": 300}
        self.assertAlmostEqual(self.mod.weighted(warm), 139036 * 0.1 + 930 * 2 + 743 + 201 * 5)
        self.assertFalse(self.mod.cold(warm))
        self.assertTrue(self.mod.cold(coldcall))
        self.assertFalse(self.mod.cold({"read": 0, "write": 5000, "uncached": 100, "out": 10}))   # a small prompt

    def test_per_zip_per_day_with_origins_and_plan_lines(self):
        d = Path(tempfile.mkdtemp())
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [{"zip": "default", "origin": "chat", "read": 100000, "write": 0, "uncached": 10, "out": 10},
                {"zip": "default", "origin": "keep-warm", "read": 100000, "write": 100, "uncached": 200, "out": 4},
                {"zip": "school", "origin": "cron", "read": 0, "write": 40000, "uncached": 500, "out": 900},
                {"kind": "plan", "weekly": 4.0, "five_hour": 20.0, "at": "now"},
                {"kind": "keep_warm", "zip": "default", "ok": True}]
        (d / f"{today}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
        calls, plan = self.mod.read(d, 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(plan[-1]["weekly"], 4.0)
        table = self.mod.summarize(calls, by_origin=True)
        zero = table[(today, "default")]
        self.assertEqual(zero["all"]["calls"], 2)
        self.assertEqual(zero["keep-warm"]["calls"], 1)
        self.assertEqual(table[(today, "school")]["all"]["cold_starts"], 1)


class PluginUsageLogOnHookWorker(PluginUsageLog):
    """Hermes runs hook callbacks on a worker thread with a copy of the turn's context: the fork's write origin."""

    def test_origins(self):
        u = {"input_tokens": 237, "output_tokens": 4, "cache_read_tokens": 68857, "cache_write_tokens": 101}
        for origin, expected in (("keep_warm", "keep-warm"), ("background_review", "background-review"),
                                 ("assistant_tool", "chat")):
            with mock.patch.object(self.ul, "_write_origin", lambda o=origin: o):
                self.ul.post_api_request(session_id="chat", platform="api_server", model="opus", usage=u)
        self.assertEqual([r["origin"] for r in self.lines()], ["keep-warm", "background-review", "chat"])
