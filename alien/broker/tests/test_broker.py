"""Tests for Zero's plan broker: what a home may ask for, what the broker actually runs, the app server
filter, the linked sign-ins, the caller checks, and an end-to-end run through the real client and socket with
a fake `claude`, plus the Codex proxy against a fake upstream.

Run: python3 -m unittest discover -s alien/broker/tests
"""

import base64
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BROKER_DIR = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


zb = load("zero_broker_under_test", BROKER_DIR / "zero_broker.py")


def directsdk_argv(tmp="/tmp/claude-directsdk-abc", effort=None, mcp=None, model="claude-opus-5-5"):
    mcp = mcp or {"mcpServers": {"hermes": {"command": "/home/zero-test/taffy/.venv/bin/python",
                                            "args": ["/home/zero-test/.taffy/plugins/x/inert_mcp.py", f"{tmp}/tools.json"]}}}
    argv = ["-p", "--model", model, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--tools", "", "--system-prompt-file", f"{tmp}/system.md", "--settings",
            f"{tmp}/settings.json", "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
            "--max-turns", "1", "--permission-mode", "dontAsk", "--no-session-persistence", "--mcp-config",
            json.dumps(mcp)]
    if effort:
        argv += ["--effort", effort]
    return argv


def directsdk_files(tmp="/tmp/claude-directsdk-abc", settings=None, tools=None):
    return {f"{tmp}/system.md": "You are a Zip.",
            f"{tmp}/settings.json": json.dumps(settings if settings is not None else
                                               {"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps({"max_tokens": 1000})}}),
            f"{tmp}/tools.json": json.dumps(tools if tools is not None else [{"name": "terminal", "inputSchema": {}}])}


class ClaudeRequests(unittest.TestCase):
    def test_directsdk_handshake_is_allowed(self):
        req = zb.parse_claude(directsdk_argv(effort="high"), directsdk_files(),
                              {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1000", "NODE_OPTIONS": "--require /tmp/x.js"})
        self.assertEqual(req.shape, "directsdk")
        self.assertEqual(req.effort, "high")
        self.assertEqual(req.tools[0]["name"], "terminal")
        self.assertEqual(req.env, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1000"})

    def test_small_commands(self):
        self.assertEqual(zb.parse_claude(["--version"]).shape, "version")
        self.assertEqual(zb.parse_claude(["auth", "status"]).shape, "auth-status")
        self.assertEqual(zb.parse_claude(["auth", "status", "--json"]).shape, "auth-status-json")
        self.assertEqual(zb.parse_claude(["-p", "--model", "claude-haiku-5-5", "--tools", "", "--output-format",
                                          "json"]).shape, "oneshot")

    def assertRefused(self, argv, files=None, contains=None):
        with self.assertRaises(zb.Refused) as cm:
            zb.parse_claude(argv, files or {})
        if contains:
            self.assertIn(contains, str(cm.exception))

    def test_mcp_commands_are_refused(self):
        self.assertRefused(["mcp", "list"], contains="connector")
        self.assertRefused(["mcp", "get", "claude.ai Gmail"], contains="connector")
        self.assertRefused(["mcp", "add", "x", "--", "sh"], contains="connector")
        self.assertRefused(["--mcp-config", "{}", "--version"], contains="connector")

    def test_prompts_naming_connectors_are_refused(self):
        self.assertRefused(["-p", "list my gmail", "--model", "sonnet", "--tools", "ToolSearch",
                            "--allowedTools", "mcp__claude_ai_Gmail__search"], contains="connector")
        self.assertRefused(["-p", "what tools do you have"])
        self.assertRefused(["-p", "--model", "haiku", "--tools", "", "--output-format", "json", "--mcp-config",
                            '{"mcpServers": {"gmail": {"type": "http", "url": "https://x"}}}'])

    def test_handshake_with_another_mcp_server_is_refused(self):
        mcp = {"mcpServers": {"hermes": {"command": "python", "args": ["/x/inert_mcp.py", "/tmp/claude-directsdk-abc/tools.json"]},
                              "evil": {"command": "sh"}}}
        self.assertRefused(directsdk_argv(mcp=mcp), directsdk_files(), contains="connector")
        mcp = {"mcpServers": {"hermes": {"command": "sh", "args": ["-c", "id"]}}}
        self.assertRefused(directsdk_argv(mcp=mcp), directsdk_files(), contains="connector")

    def test_settings_with_hooks_are_refused(self):
        hooks = {"env": {"CLAUDE_CODE_EXTRA_BODY": "{}"},
                 "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "id"}]}]}}
        self.assertRefused(directsdk_argv(), directsdk_files(settings=hooks))
        self.assertRefused(directsdk_argv(), directsdk_files(settings={"apiKeyHelper": "cat ~/.claude/x"}))
        self.assertRefused(directsdk_argv(), directsdk_files(settings={"env": {"CLAUDE_CODE_EXTRA_BODY": "{}",
                                                                               "NODE_OPTIONS": "x"}}))

    def test_extra_body_cannot_add_mcp_servers(self):
        body = {"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps({"mcp_servers": [{"url": "https://x"}]})}}
        self.assertRefused(directsdk_argv(), directsdk_files(settings=body), contains="connector")

    def test_unknown_shapes_are_refused(self):
        for argv in (["config", "set", "x"], ["--dangerously-skip-permissions", "-p", "x"], ["update"],
                     ["setup-token"], ["login"], ["plugin", "install", "x"], directsdk_argv()[:-2],
                     directsdk_argv() + ["--plugin-dir", "/tmp/x"], directsdk_argv(effort="ludicrous"),
                     directsdk_argv(model="opus; rm -rf /"), [], ["-p"]):
            with self.subTest(argv=argv[:4]):
                self.assertRefused(argv, directsdk_files())

    def test_missing_files_are_refused(self):
        self.assertRefused(directsdk_argv(), {}, contains="not sent")


class WhatTheBrokerRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/broker/tmp/run1")
        self.inert = Path("/srv/zero/broker/inert_mcp.py")

    def argv(self, req):
        return zb.claude_argv(req, "/owner/.local/bin/claude", self.tmp, "/owner/venv/python", self.inert)

    def test_every_model_run_strips_connectors(self):
        for req in (zb.parse_claude(directsdk_argv(effort="low"), directsdk_files()),
                    zb.parse_claude(["-p", "--model", "claude-haiku-5-5", "--tools", "", "--output-format", "json"])):
            argv = self.argv(req)
            self.assertIn("--strict-mcp-config", argv)
            i = argv.index("--disallowedTools")
            self.assertEqual(argv[i + 1], "mcp__claude_ai_*")
            mcp = json.loads(argv[argv.index("--mcp-config") + 1])
            self.assertLessEqual(set(mcp["mcpServers"]), {"hermes"})
            if mcp["mcpServers"]:
                self.assertEqual(mcp["mcpServers"]["hermes"]["args"], [str(self.inert), str(self.tmp / "tools.json")])
                self.assertEqual(mcp["mcpServers"]["hermes"]["command"], "/owner/venv/python")
            self.assertNotIn("/home/zero-test", " ".join(argv))
            settings = json.loads(zb.claude_files(req)["settings.json"])
            self.assertTrue(settings["disableClaudeAiConnectors"])
            self.assertEqual(set(settings) - {"disableClaudeAiConnectors", "autoMemoryEnabled", "crossSessionInbound"},
                             set() if req.shape == "oneshot" else {"env"})
            env = zb.claude_env({"LANG": "C.UTF-8", "NODE_OPTIONS": "--require x"}, req, Path("/owner/.zero-broker/homes/test/claude"),
                                Path("/owner"), "/owner/.local/bin/claude", None)
            self.assertEqual(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
            self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/owner/.zero-broker/homes/test/claude")
            self.assertNotIn("NODE_OPTIONS", env)
            self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_effort_and_model_carried(self):
        argv = self.argv(zb.parse_claude(directsdk_argv(effort="xhigh", model="opus[1m]"), directsdk_files()))
        self.assertEqual(argv[argv.index("--model") + 1], "opus[1m]")
        self.assertEqual(argv[-2:], ["--effort", "xhigh"])

    def test_admission_url_goes_to_the_owners_relay(self):
        req = zb.parse_claude(directsdk_argv(), directsdk_files())
        env = zb.claude_env({}, req, Path("/c"), Path("/owner"), "/x/claude", "http://127.0.0.1:5555/admit/abc")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:5555/admit/abc")

    def test_auth_status_hides_the_owner(self):
        raw = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                          "email": "owner@example.com", "orgId": "org-1", "orgName": "Owner", "subscriptionType": "max"})
        out = json.loads(zb.sanitize_auth_status(raw, True))
        self.assertTrue(out["loggedIn"])
        self.assertTrue(out["sharedPlan"])
        self.assertNotIn("email", out)
        self.assertNotIn("orgName", out)
        self.assertIn("shared plan", zb.sanitize_auth_status(raw, False))
        self.assertFalse(json.loads(zb.sanitize_auth_status("garbage", True))["loggedIn"])


class CodexRequests(unittest.TestCase):
    def test_allowed(self):
        self.assertEqual(zb.parse_codex(["app-server"]), "app-server")
        self.assertEqual(zb.parse_codex(["login", "status"]), "login-status")
        self.assertEqual(zb.parse_codex(["--version"]), "version")

    def test_exec_and_others_refused(self):
        for argv in (["exec", "hi"], ["e", "hi"], ["exec", "--json", "-c", "apps.x.enabled=true", "x"],
                     ["mcp", "list"], ["mcp-server"], ["login"], ["logout"], ["app-server", "--listen", "x"],
                     ["-c", "x=1", "app-server"], [], ["apply"], ["cloud"]):
            with self.subTest(argv=argv):
                with self.assertRaises(zb.Refused) as cm:
                    zb.parse_codex(argv)
                if argv[:1] in (["exec"], ["e"]):
                    self.assertIn("codex exec", str(cm.exception))

    def test_login_status_hides_the_account(self):
        self.assertEqual(zb.sanitize_login_status("Logged in using ChatGPT (owner@example.com)"),
                         "Logged in using ChatGPT (shared plan)\n")
        self.assertEqual(zb.sanitize_login_status("Not logged in"), "Not logged in\n")

    def test_codex_config_turns_off_apps_and_shell(self):
        self.assertIn("shell_tool = false", zb.CODEX_CONFIG)
        self.assertIn("[apps._default]\nenabled = false", zb.CODEX_CONFIG)
        self.assertIn("web_search = false", zb.CODEX_CONFIG)


def line(obj):
    return (json.dumps(obj) + "\n").encode()


class AppServerFilterTests(unittest.TestCase):
    def setUp(self):
        self.f = zb.AppServerFilter()

    def test_call_methods_pass(self):
        for m in ("initialize", "thread/realtime/start", "thread/realtime/appendSpeech", "thread/realtime/stop"):
            fwd, reply = self.f.from_client(line({"id": 1, "method": m, "params": {}}))
            self.assertIsNotNone(fwd, m)
            self.assertIsNone(reply)
        fwd, _ = self.f.from_client(line({"method": "initialized"}))
        self.assertIsNotNone(fwd)

    def test_thread_start_is_pinned(self):
        fwd, _ = self.f.from_client(line({"id": 2, "method": "thread/start", "params": {
            "model": "gpt-realtime", "sandbox": "danger-full-access", "approvalPolicy": "on-request",
            "environments": [{"type": "local"}], "config": {"model_reasoning_effort": "low", "apps": {"x": {"enabled": True}},
                                                             "features": {"shell_tool": True}},
            "mcpServers": {"x": {}}, "dynamicTools": [{"name": "ask_bot"}], "cwd": "/home/jdoe"}}))
        params = json.loads(fwd)["params"]
        self.assertEqual(params["sandbox"], "read-only")
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(params["environments"], [])
        self.assertEqual(params["config"], {"model_reasoning_effort": "low"})
        self.assertNotIn("mcpServers", params)
        self.assertNotIn("cwd", params)
        self.assertEqual(params["dynamicTools"], [{"name": "ask_bot"}])

    def test_other_requests_answered_with_an_error(self):
        for m in ("app/installed", "app/read", "command/exec", "account/logout", "account/login/start",
                  "config/value/write", "mcpServer/oauth/login", "turn/start", "fs/readFile"):
            fwd, reply = self.f.from_client(line({"id": 7, "method": m, "params": {}}))
            self.assertIsNone(fwd, m)
            err = json.loads(reply)
            self.assertEqual(err["id"], 7)
            self.assertIn("shared plan", err["error"]["message"])
        fwd, reply = self.f.from_client(line({"method": "something/else"}))
        self.assertIsNone(fwd)
        self.assertIsNone(reply)

    def test_only_tool_calls_can_be_answered(self):
        self.f.from_server(line({"id": 41, "method": "item/tool/call", "params": {"tool": "ask_bot"}}))
        self.f.from_server(line({"id": 42, "method": "execCommandApproval", "params": {}}))
        fwd, _ = self.f.from_client(line({"id": 41, "result": {"contentItems": []}}))
        self.assertIsNotNone(fwd)
        fwd, _ = self.f.from_client(line({"id": 42, "result": {"decision": "approved"}}))
        self.assertIsNone(fwd)
        fwd, _ = self.f.from_client(line({"id": 99, "result": {}}))
        self.assertIsNone(fwd)
        fwd, _ = self.f.from_client(b"not json\n")
        self.assertIsNone(fwd)


class LinkedSignIns(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.target = self.d / "owner" / ".credentials.json"
        self.target.parent.mkdir()
        self.target.write_text('{"token": "old"}')
        self.link = self.d / "home" / ".credentials.json"

    def test_created_then_ok(self):
        self.assertEqual(zb.ensure_link(self.link, self.target), "created")
        self.assertEqual(zb.ensure_link(self.link, self.target), "ok")
        self.assertEqual(json.loads(self.link.read_text()), {"token": "old"})

    def test_a_newer_copy_becomes_the_owners_file(self):
        self.link.parent.mkdir()
        self.link.write_text('{"token": "rotated"}')
        os.utime(self.target, (time.time() - 100, time.time() - 100))
        self.assertEqual(zb.ensure_link(self.link, self.target), "adopted-newer")
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(json.loads(self.target.read_text()), {"token": "rotated"})
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_an_older_copy_is_dropped(self):
        self.link.parent.mkdir()
        self.link.write_text('{"token": "stale"}')
        os.utime(self.link, (time.time() - 100, time.time() - 100))
        self.assertEqual(zb.ensure_link(self.link, self.target), "relinked")
        self.assertEqual(json.loads(self.target.read_text()), {"token": "old"})

    def test_a_broken_copy_is_never_adopted(self):
        self.link.parent.mkdir()
        self.link.write_text("not json")
        os.utime(self.target, (time.time() - 100, time.time() - 100))
        self.assertEqual(zb.ensure_link(self.link, self.target), "relinked")
        self.assertEqual(json.loads(self.target.read_text()), {"token": "old"})


class Callers(unittest.TestCase):
    def test_home_for_uid(self):
        import pwd
        me = pwd.getpwuid(os.getuid()).pw_name
        reg = {"colin": {"user": me, "owner": True, "plan": "own"}}
        self.assertIsNone(zb.home_for_uid(os.getuid(), reg))
        reg = {"test": {"user": me, "plan": "own"}}
        self.assertIsNone(zb.home_for_uid(os.getuid(), reg))
        reg = {"test": {"user": me, "plan": "broker"}}
        self.assertEqual(zb.home_for_uid(os.getuid(), reg), "test")
        self.assertIsNone(zb.home_for_uid(987654, reg))

    @unittest.skipUnless(sys.platform.startswith("linux"), "SO_PEERCRED is Linux only")
    def test_peer_uid_unix_is_the_kernel_answer(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.assertEqual(zb.peer_uid_unix(a), os.getuid())
        finally:
            a.close()
            b.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "/proc/net/tcp is Linux only")
    def test_peer_uid_tcp_live(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        c = socket.create_connection(srv.getsockname())
        conn, addr = srv.accept()
        try:
            self.assertEqual(zb.peer_uid_tcp(addr[:2], srv.getsockname()), os.getuid())
        finally:
            for s in (c, conn, srv):
                s.close()

    def test_peer_uid_tcp(self):
        proc = textwrap.dedent("""\
          sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
           0: 0100007F:21CC 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1 1
           1: 0100007F:D431 0100007F:21CC 01 00000000:00000000 00:00000000 00000000  1002        0 2 1
           2: 0100007F:21CC 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 3 1
        """)
        self.assertEqual(zb.peer_uid_tcp(("127.0.0.1", 0xD431), ("127.0.0.1", 8652), proc), 1002)
        self.assertIsNone(zb.peer_uid_tcp(("127.0.0.1", 0xD432), ("127.0.0.1", 8652), proc))

    def test_proxy_paths(self):
        self.assertEqual(zb.parse_proxy_path("/test/s3cret/responses?x=1"), ("test", "s3cret", "responses", "x=1"))
        self.assertIsNone(zb.parse_proxy_path("/test/responses"))
        self.assertIsNone(zb.parse_proxy_path("/../s/responses"))
        self.assertTrue(zb.route_allowed("POST", "responses"))
        self.assertTrue(zb.route_allowed("POST", "responses/compact"))
        self.assertTrue(zb.route_allowed("GET", "models"))
        for method, rest in (("GET", "responses"), ("POST", "../wham/usage"), ("POST", "files"), ("GET", "wham/apps")):
            self.assertFalse(zb.route_allowed(method, rest), rest)

    def test_homes_folders(self):
        d = Path(tempfile.mkdtemp())
        c = zb.Config(state=d / ".zero-broker", claude_credentials=d / "c.json", codex_auth=d / "a.json")
        (d / "c.json").write_text("{}")
        (d / "a.json").write_text("{}")
        homes = zb.Homes(c)
        s1 = homes.add("test")
        s2 = homes.add("test")
        self.assertEqual(s1, s2)
        self.assertGreater(len(s1), 30)
        self.assertEqual((c.state / "homes" / "test" / "secret").stat().st_mode & 0o777, 0o600)
        self.assertEqual(homes.link_state("test"), {"claude": "linked", "codex": "linked"})
        self.assertEqual((c.state / "homes" / "test" / "codex" / "config.toml").read_text(), zb.CODEX_CONFIG)
        with self.assertRaises(ValueError):
            homes.dir("../colin")


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
data = sys.stdin.read()
keys = ("HOME", "CLAUDE_CONFIG_DIR", "ENABLE_CLAUDEAI_MCP_SERVERS", "NODE_OPTIONS", "ANTHROPIC_BASE_URL")
print(json.dumps({"argv": sys.argv[1:], "stdin": data, "env": {k: os.environ.get(k) for k in keys},
                  "files": {n: open(p).read() for n, p in (("settings", sys.argv[sys.argv.index("--settings") + 1]),)
                            if "--settings" in sys.argv}}))
sys.stderr.write("note on stderr\\n")
sys.exit(3)
"""


class EndToEnd(unittest.TestCase):
    """The real client and the real socket server, with a fake `claude` that reports what it was given."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir="/tmp", prefix="zb-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        fake = self.d / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        (self.d / "creds.json").write_text("{}")
        (self.d / "auth.json").write_text("{}")
        self.c = zb.Config(socket_path=self.d / "s.sock", registry=self.d / "homes.json", state=self.d / "state",
                           claude_bin=str(fake), codex_bin=str(fake), claude_credentials=self.d / "creds.json",
                           codex_auth=self.d / "auth.json", admission_module=self.d / "missing.py")
        for sub in ("", "homes", "tmp"):
            (self.c.state / sub).mkdir(parents=True, exist_ok=True)
        self.orig = zb.peer_uid_unix, zb.home_for_uid
        zb.peer_uid_unix = lambda sock: os.getuid()
        zb.home_for_uid = lambda uid, reg: "test"
        broker = zb.Broker(self.c)
        broker.homes.add("test")
        self.server = broker.serve_socket()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        zb.peer_uid_unix, zb.home_for_uid = self.orig

    def client(self, tool, argv, stdin=b"", env=None):
        e = dict(os.environ, ZERO_BROKER_SOCKET=str(self.c.socket_path), NODE_OPTIONS="--require /tmp/evil.js",
                 **(env or {}))
        return subprocess.run([sys.executable, str(BROKER_DIR / f"{tool}-client"), *argv], input=stdin,
                              capture_output=True, env=e, timeout=30)

    def test_refused_request(self):
        r = self.client("codex", ["exec", "hello"])
        self.assertEqual(r.returncode, 126)
        self.assertIn(b"codex exec is not available", r.stderr)
        r = self.client("claude", ["mcp", "list"])
        self.assertEqual(r.returncode, 126)

    def test_directsdk_run_is_relayed_and_stripped(self):
        tmp = Path(tempfile.mkdtemp(prefix="claude-directsdk-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name, text in directsdk_files(str(tmp)).items():
            Path(name).write_text(text)
        frame = {"type": "user", "message": {"role": "user", "content": "hi"}}
        r = self.client("claude", directsdk_argv(str(tmp), effort="low"), stdin=(json.dumps(frame) + "\n").encode(),
                        env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "900"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(r.stderr, b"note on stderr\n")
        out = json.loads(r.stdout)
        self.assertIn("--strict-mcp-config", out["argv"])
        self.assertIn("mcp__claude_ai_*", out["argv"])
        self.assertEqual(out["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertIsNone(out["env"]["NODE_OPTIONS"])
        self.assertEqual(out["env"]["CLAUDE_CONFIG_DIR"], str(self.c.state / "homes" / "test" / "claude"))
        self.assertEqual(json.loads(out["stdin"]), frame)
        self.assertTrue(json.loads(out["files"]["settings"])["disableClaudeAiConnectors"])
        self.assertNotIn(str(tmp), " ".join(out["argv"]))
        deadline = time.time() + 3
        while list((self.c.state / "tmp").iterdir()) and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(list((self.c.state / "tmp").iterdir()), [])

    def test_oneshot_prompt_without_newline(self):
        r = self.client("claude", ["-p", "--model", "claude-haiku-5-5", "--tools", "", "--output-format", "json"],
                        stdin=b"Summarize this")
        self.assertEqual(json.loads(r.stdout)["stdin"], "Summarize this")

    def test_broker_unreachable(self):
        e = dict(os.environ, ZERO_BROKER_SOCKET=str(self.d / "nope.sock"))
        r = subprocess.run([sys.executable, str(BROKER_DIR / "claude-client"), "--version"], capture_output=True,
                           env=e, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"not reachable", r.stderr)


def fake_jwt(account="acct-owner"):
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": account}, "exp": 9999999999}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class CodexProxy(unittest.TestCase):
    def setUp(self):
        self.seen = []
        seen = self.seen
        self.fail_first = False
        test = self

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
                if test.fail_first and len(seen) == 1:
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: one\n\ndata: two\n\n")

        self.up = HTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        d = Path(tempfile.mkdtemp())
        (d / "c.json").write_text("{}")
        (d / "a.json").write_text("{}")
        self.c = zb.Config(state=d / "state", http_port=0, check_tcp_uid=False, claude_credentials=d / "c.json",
                           codex_auth=d / "a.json", admission_module=d / "none.py",
                           codex_upstream=f"http://127.0.0.1:{self.up.server_port}/backend-api/codex")
        (self.c.state).mkdir()
        self.broker = zb.Broker(self.c)
        self.secret = self.broker.homes.add("test")
        self.forced = []
        self.broker.codex_tokens = zb.CodexTokens(lambda force: (self.forced.append(force), fake_jwt())[1])
        self.srv = zb.make_http_server(self.broker)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.up.shutdown()
        self.up.server_close()

    def post(self, path, headers=None):
        req = urllib.request.Request(self.base + path, data=b'{"model": "x"}', method="POST",
                                     headers={"Authorization": "Bearer zero-broker-placeholder",
                                              "Content-Type": "application/json", **(headers or {})})
        r = urllib.request.urlopen(req, timeout=10)
        self.addCleanup(r.close)
        return r

    def test_token_swapped_and_streamed(self):
        r = self.post(f"/test/{self.secret}/responses", {"ChatGPT-Account-ID": "acct-home", "Cookie": "x=1"})
        self.assertEqual(r.read(), b"data: one\n\ndata: two\n\n")
        got = self.seen[0]
        self.assertEqual(got["path"], "/backend-api/codex/responses")
        h = {k.lower(): v for k, v in got["headers"].items()}
        self.assertEqual(h["authorization"], f"Bearer {fake_jwt()}")
        self.assertEqual(h["chatgpt-account-id"], "acct-owner")
        self.assertNotIn("cookie", h)
        self.assertEqual(got["body"], b'{"model": "x"}')
        stats = json.loads((self.c.state / "status.json").read_text())
        self.assertEqual(stats["test"]["http_requests"], 1)
        self.assertNotIn(fake_jwt(), (self.c.state / "status.json").read_text())

    def test_refresh_once_on_401(self):
        self.fail_first = True
        self.post(f"/test/{self.secret}/responses").read()
        self.assertEqual(len(self.seen), 2)
        self.assertIn(True, self.forced)

    def test_wrong_secret_and_route(self):
        for path in ("/test/wrong/responses", f"/other/{self.secret}/responses", f"/test/{self.secret}/files",
                     f"/test/{self.secret}/../wham/usage"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    self.post(path)
                cm.exception.close()
                self.assertEqual(cm.exception.code, 404)
        self.assertEqual(self.seen, [])


if __name__ == "__main__":
    unittest.main()
