"""No owner context reaches a run the broker makes for a home on a shared plan.

Claude Code attaches the signed-in account's email to every request (a <system-reminder> "# userEmail"
section, read from CLAUDE_CONFIG_DIR/.claude.json, which it fills from the owner's sign-in), and would load the
owner's CLAUDE.md, auto memory, settings, hooks and plugins from his folders. These tests pin what the broker
does about it: the spawn environment and arguments carry none of it, the working folder is neutral, and every
model request passes the ContextFilter, which removes the account context before it leaves the owner's side.

Run: python3 -m unittest discover -s alien/broker/tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from test_broker import BROKER_DIR, directsdk_argv, directsdk_files, zb

OWNER_EMAIL = "owner.person@example.com"
OWNER_FULL = "Ownerfirst Ownerlast"
OWNER_DISPLAY = "Ownerfirst"
OWNER_ORG = "Ownerfirst's Organization"
ACCOUNT = {"accountUuid": "acct-1", "emailAddress": OWNER_EMAIL, "organizationUuid": "org-1",
           "displayName": OWNER_DISPLAY, "fullName": OWNER_FULL, "organizationName": OWNER_ORG,
           "billingType": "stripe_subscription"}
CLAUDE_MD_MARKER = "OWNER-CLAUDE-MD-MARKER: always sign as the owner"


def reminder_with_email(email=OWNER_EMAIL):
    return ("<system-reminder>\nAs you answer the user's questions, you can use the following context:\n"
            "# claudeMd\nContents of /owner/.claude/CLAUDE.md (user's private global instructions):\n\n"
            f"{CLAUDE_MD_MARKER}\n"
            f"# userEmail\nThe user's email address is {email}. Use it only to identify the user, such as for "
            "authorship, attribution, or filtering their own work. Never send it to an unrelated service.\n"
            "# attachedProject\nThe owner's claude.ai project: Taxes 2026.\n"
            "# currentDate\nToday's date is 2026-10-09.\n\n"
            "Claude Code attached this context automatically; it isn't part of the user's message.\n"
            "</system-reminder>")


def request_body(home_text="Tell Colin that dinner is at six. His address is owner.person@example.com."):
    return {"model": "claude-haiku-5-5", "system": [{"type": "text", "text": "You are Sam's Zip."}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>\n# Environment\n - Primary working directory: /\n"
                                         "</system-reminder>"},
                {"type": "text", "text": reminder_with_email()},
                {"type": "text", "text": home_text, "cache_control": {"type": "ephemeral"}}]}]}


IDENTITY = [OWNER_ORG, OWNER_EMAIL, OWNER_FULL, OWNER_DISPLAY, "/home/ownerperson", "ownerperson"]


class Scrubbing(unittest.TestCase):
    def test_the_account_sections_are_removed(self):
        out, n = zb.scrub_owner_context(json.dumps(request_body()).encode(), IDENTITY)
        body = json.loads(out)
        reminder = body["messages"][0]["content"][1]["text"]
        self.assertEqual(n, 1)
        self.assertNotIn(OWNER_EMAIL, reminder)
        self.assertNotIn("# userEmail", reminder)
        self.assertNotIn(CLAUDE_MD_MARKER, reminder)
        self.assertNotIn("Taxes 2026", reminder)
        self.assertIn("Today's date is 2026-10-09.", reminder)
        self.assertTrue(reminder.endswith("</system-reminder>"))

    def test_the_homes_own_words_are_never_edited(self):
        home_text = "Tell Colin that dinner is at six. His address is owner.person@example.com."
        out, _ = zb.scrub_owner_context(json.dumps(request_body(home_text)).encode(), IDENTITY)
        body = json.loads(out)
        self.assertEqual(body["messages"][0]["content"][2],
                         {"type": "text", "text": home_text, "cache_control": {"type": "ephemeral"}})
        self.assertEqual(body["system"], [{"type": "text", "text": "You are Sam's Zip."}])

    def test_identity_strings_in_a_reminder_are_hidden(self):
        text = (f"<system-reminder>\n# Environment\n - Primary working directory: /home/ownerperson/x\n"
                f"Signed in as {OWNER_FULL.upper()}\n</system-reminder>")
        body = {"messages": [{"role": "user", "content": text}]}
        out, n = zb.scrub_owner_context(json.dumps(body).encode(), IDENTITY)
        got = json.loads(out)["messages"][0]["content"]
        self.assertEqual(n, 1)
        self.assertNotIn("ownerperson", got)
        self.assertNotIn(OWNER_FULL.lower(), got.lower())
        self.assertIn(zb.HIDDEN, got)

    def test_later_turns_and_clean_requests_pass_byte_for_byte(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"},
                                           {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                                                         "content": "done"}]}]}
        raw = json.dumps(body, indent=2).encode()
        self.assertEqual(zb.scrub_owner_context(raw, IDENTITY), (raw, 0))

    def test_not_a_messages_body(self):
        for raw in (b"not json", b"[1, 2]", b'{"messages": "x"}'):
            with self.assertRaises(ValueError):
                zb.scrub_owner_context(raw, IDENTITY)

    def test_owner_identity_reads_the_configs(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "a.json").write_text(json.dumps({"oauthAccount": ACCOUNT}))
        (d / "b.json").write_text("not json")
        got = zb.owner_identity([d / "a.json", d / "b.json", d / "missing.json"], ["ownerperson", "/home/ownerperson", "x"])
        for value in (OWNER_EMAIL, OWNER_FULL, OWNER_DISPLAY, OWNER_ORG, "ownerperson", "/home/ownerperson"):
            self.assertIn(value, got)
        self.assertNotIn("x", got)
        self.assertNotIn("acct-1", got)
        self.assertEqual(got, sorted(got, key=len, reverse=True))


class Upstream:
    """A fake Anthropic (or admission relay) that records each body it is sent."""

    def __init__(self, prefix=""):
        self.bodies, self.paths, self.auth = [], [], []
        rec = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                rec.bodies.append(body)
                rec.paths.append(self.path)
                rec.auth.append(self.headers.get("Authorization"))
                out = b'event: message_start\ndata: {"type":"message_start"}\n\n'
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}{prefix}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def post(url, body: bytes):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer owner-token", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


class ContextFilterRelay(unittest.TestCase):
    def setUp(self):
        self.up = Upstream("/admit/abc")
        self.addCleanup(self.up.close)
        self.f = zb.ContextFilter(self.up.url, lambda: IDENTITY, timeout=10)
        self.addCleanup(self.f.close)

    def test_scrubs_and_streams(self):
        status, out = post(self.f.url + "/v1/messages?beta=true", json.dumps(request_body()).encode())
        self.assertEqual(status, 200)
        self.assertIn(b"message_start", out)
        self.assertEqual(self.up.paths, ["/admit/abc/v1/messages?beta=true"])
        self.assertEqual(self.up.auth, ["Bearer owner-token"])
        sent = self.up.bodies[0].decode()
        self.assertNotIn("# userEmail", sent)
        self.assertNotIn(CLAUDE_MD_MARKER, sent)
        self.assertIn("Tell Colin that dinner is at six.", sent)
        self.assertEqual(self.f.scrubbed, 1)

    def test_other_paths_are_not_relayed(self):
        for path in ("/v1/messages", self.f.prefix + "/v1/messages/count_tokens", self.f.prefix + "/api/hello",
                     "/ctx/wrong/v1/messages"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    post(f"http://127.0.0.1:{self.f.server.server_port}{path}", b"{}")
                cm.exception.close()
                self.assertEqual(cm.exception.code, 404)
        self.assertEqual(self.up.bodies, [])

    def test_an_unreadable_body_naming_the_owner_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.f.url + "/v1/messages", f"garbage {OWNER_EMAIL}".encode())
        cm.exception.close()
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.up.bodies, [])
        post(self.f.url + "/v1/messages", b"garbage without names")
        self.assertEqual(self.up.bodies, [b"garbage without names"])

    def test_a_compressed_body_is_refused(self):
        """Night review: the owner's email inside a gzip body cannot be found by a byte search, so an encoded body
        is never forwarded unread."""
        import gzip
        body = json.dumps({"messages": [{"role": "user", "content": "<system-reminder>\n# userEmail\n"
                                         f"The user's email address is {OWNER_EMAIL}.\n</system-reminder>"}]})
        req = urllib.request.Request(self.f.url + "/v1/messages", data=gzip.compress(body.encode()), method="POST",
                                     headers={"Authorization": "Bearer owner-token", "Content-Type": "application/json",
                                              "Content-Encoding": "gzip"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        cm.exception.close()
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.up.bodies, [])


# A stand-in for the real `claude`: it does what Claude Code does with the owner's account (reads the email from
# CLAUDE_CONFIG_DIR/.claude.json and attaches it to the request it sends to ANTHROPIC_BASE_URL), then reports
# everything it was started with: every argument, every environment variable, its working folder, and the
# files its arguments name.
FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys, urllib.request
stdin = sys.stdin.read()
argv = sys.argv[1:]
cfg = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
try:
    email = json.load(open(os.path.join(cfg, ".claude.json")))["oauthAccount"]["emailAddress"]
except Exception:
    email = None
files = {}
for flag in ("--settings", "--system-prompt-file"):
    if flag in argv:
        p = argv[argv.index(flag) + 1]
        files[flag] = open(p).read() if os.path.exists(p) else None
if "-p" in argv and os.environ.get("ANTHROPIC_BASE_URL"):
    blocks = [{"type": "text", "text": "<system-reminder>\n# Environment\n - Primary working directory: "
               + os.getcwd() + "\n</system-reminder>"}]
    if email:
        blocks.append({"type": "text", "text": "<system-reminder>\nAs you answer the user's questions, you can use "
                       "the following context:\n# userEmail\nThe user's email address is " + email + ". Use it "
                       "only to identify the user.\n# currentDate\nToday's date is 2026-10-09.\n\nClaude Code "
                       "attached this context automatically.\n</system-reminder>"})
    blocks.append({"type": "text", "text": stdin or "hi"})
    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": blocks}]}).encode()
    req = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages", data=body,
                                 headers={"Authorization": "Bearer owner-token", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
print(json.dumps({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "files": files,
                  "saw_email_in_config": bool(email)}))
"""


class NoOwnerContextEndToEnd(unittest.TestCase):
    """The real client and socket server, an owner with an email, names and a CLAUDE.md, and the fake claude.
    Fails if the owner's email, names or CLAUDE.md (path or words) reach the run's arguments, environment,
    working folder, files, or the request that leaves for the model."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir="/tmp", prefix="zb-oc-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        owner = self.d / "ownerperson"
        (owner / ".claude").mkdir(parents=True)
        (owner / ".claude.json").write_text(json.dumps({"oauthAccount": ACCOUNT}))
        (owner / ".claude" / "CLAUDE.md").write_text(CLAUDE_MD_MARKER + "\n")
        (owner / "CLAUDE.md").write_text(CLAUDE_MD_MARKER + "\n")
        self.owner = owner
        fake = self.d / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        (owner / ".claude" / ".credentials.json").write_text("{}")
        (self.d / "auth.json").write_text("{}")
        self.up = Upstream()
        self.addCleanup(self.up.close)
        self.c = zb.Config(socket_path=self.d / "s.sock", registry=self.d / "homes.json", state=owner / ".zero-broker",
                           claude_bin=str(fake), codex_bin=str(fake),
                           claude_credentials=owner / ".claude" / ".credentials.json", codex_auth=self.d / "auth.json",
                           admission_module=self.d / "missing.py", owner_home=owner, anthropic_upstream=self.up.url)
        for sub in ("", "homes", "tmp"):
            (self.c.state / sub).mkdir(parents=True, exist_ok=True)
        self.orig = zb.peer_uid_unix, zb.home_for_uid
        zb.peer_uid_unix = lambda sock: os.getuid()
        zb.home_for_uid = lambda uid, reg: "test"
        self.broker = zb.Broker(self.c)
        self.broker.homes.add("test")
        # What Claude Code writes into the home's config folder after its first start with the owner's sign-in.
        (self.c.state / "homes" / "test" / "claude" / ".claude.json").write_text(json.dumps({"oauthAccount": ACCOUNT}))
        self.server = self.broker.serve_socket()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        zb.peer_uid_unix, zb.home_for_uid = self.orig

    def client(self, argv, stdin=b"", env=None):
        e = dict(os.environ, ZERO_BROKER_SOCKET=str(self.c.socket_path), CLAUDE_CONFIG_DIR=str(self.owner / ".claude"),
                 CLAUDE_CODE_DISABLE_CLAUDE_MDS="0", CLAUDE_CODE_DISABLE_AUTO_MEMORY="0", USER_EMAIL=OWNER_EMAIL,
                 **(env or {}))
        r = subprocess.run([sys.executable, str(BROKER_DIR / "claude-client"), *argv], input=stdin,
                           capture_output=True, env=e, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.decode().strip().splitlines()[-1])

    def assertNoOwnerFacts(self, text: str, where: str):
        low = text.lower()
        for fact in (OWNER_EMAIL, OWNER_FULL, OWNER_ORG, CLAUDE_MD_MARKER, str(self.owner / ".claude" / "CLAUDE.md"),
                     str(self.owner / "CLAUDE.md"), str(self.owner / ".claude.json"), str(self.owner / ".claude") + '"'):
            self.assertNotIn(fact.lower(), low, f"{fact!r} reached {where}")

    def check_run(self, out):
        self.assertTrue(out["saw_email_in_config"])
        self.assertEqual(out["cwd"], "/")
        env = out["env"]
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.c.state / "homes" / "test" / "claude"))
        for k, v in zb.NO_OWNER_CONTEXT_ENV.items():
            self.assertEqual(env[k], v, k)
        self.assertNotIn("USER_EMAIL", env)
        self.assertTrue(env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:"))
        self.assertIn("/ctx/", env["ANTHROPIC_BASE_URL"])
        self.assertNoOwnerFacts(json.dumps(out["argv"]), "the run's arguments")
        self.assertNoOwnerFacts(json.dumps(env), "the run's environment")
        self.assertNoOwnerFacts(json.dumps(out["files"]), "the run's files")
        argv = out["argv"]
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        settings = json.loads(out["files"]["--settings"])
        self.assertFalse(settings["autoMemoryEnabled"])
        self.assertEqual(settings["crossSessionInbound"], "refuse")
        self.assertNotIn("hooks", settings)
        self.assertEqual(len(self.up.bodies), 1)
        sent = self.up.bodies[0].decode()
        self.assertNoOwnerFacts(sent, "the request sent to the model")
        self.assertNotIn("# userEmail", sent)
        self.assertIn("Today's date is 2026-10-09.", sent)
        stats = json.loads((self.c.state / "status.json").read_text())
        self.assertEqual(stats["test"]["owner_context_removed"], 1)

    def test_oneshot_run(self):
        out = self.client(["-p", "--model", "claude-haiku-5-5", "--tools", "", "--output-format", "json"],
                          stdin=b"Write a short briefing for Sam.")
        self.check_run(out)
        self.assertIn("Write a short briefing for Sam.", self.up.bodies[0].decode())

    def test_directsdk_run(self):
        tmp = Path(tempfile.mkdtemp(prefix="claude-directsdk-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name, text in directsdk_files(str(tmp)).items():
            Path(name).write_text(text)
        frame = {"type": "user", "message": {"role": "user", "content": "hi"}}
        out = self.client(directsdk_argv(str(tmp), effort="low"), stdin=(json.dumps(frame) + "\n").encode())
        self.check_run(out)
        self.assertEqual(out["files"]["--system-prompt-file"], "You are a Zip.")

    def test_directsdk_goes_through_the_admission_relay_after_the_filter(self):
        admit = Upstream("/admit/xyz")
        self.addCleanup(admit.close)

        class FakeAdmission:
            def __init__(self, upstream, timeout):
                self.url = admit.url
                self.queried = None

            def close(self):
                pass

        self.broker.Admission = FakeAdmission
        tmp = Path(tempfile.mkdtemp(prefix="claude-directsdk-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name, text in directsdk_files(str(tmp)).items():
            Path(name).write_text(text)
        frame = {"type": "user", "message": {"role": "user", "content": "hi"}}
        self.client(directsdk_argv(str(tmp)), stdin=(json.dumps(frame) + "\n").encode())
        self.assertEqual(self.up.bodies, [])
        self.assertEqual(admit.paths, ["/admit/xyz/v1/messages"])
        self.assertNotIn(OWNER_EMAIL, admit.bodies[0].decode())

    def test_version_and_auth_status_run_in_the_neutral_folder(self):
        r = subprocess.run([sys.executable, str(BROKER_DIR / "claude-client"), "--version"], capture_output=True,
                           env=dict(os.environ, ZERO_BROKER_SOCKET=str(self.c.socket_path)), timeout=30)
        out = json.loads(r.stdout)
        self.assertEqual(out["cwd"], "/")
        self.assertNotIn("ANTHROPIC_BASE_URL", out["env"])
        self.assertNoOwnerFacts(json.dumps(out), "the version check")
        self.assertEqual(self.up.bodies, [])


class SpawnShape(unittest.TestCase):
    def test_home_cannot_turn_the_switches_back_on(self):
        req = zb.parse_claude(["-p", "--model", "claude-haiku-5-5", "--tools", "", "--output-format", "json"], {},
                              {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
                               "CLAUDE_CONFIG_DIR": "/owner/.claude", "ANTHROPIC_BASE_URL": "https://evil"})
        self.assertEqual(req.env, {})
        req.env = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0"}
        env = zb.claude_env({}, req, Path("/c"), Path("/owner"), "/x/claude", "http://127.0.0.1:1/ctx/a")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:1/ctx/a")

    def test_default_working_folder_is_neutral(self):
        self.assertEqual(zb.Config().claude_cwd, "/")

    def test_settings_hold_no_owner_customization(self):
        for argv, files in ((directsdk_argv(), directsdk_files()),
                            (["-p", "--model", "claude-haiku-5-5", "--tools", "", "--output-format", "json"], {})):
            req = zb.parse_claude(argv, files)
            settings = json.loads(zb.claude_files(req)["settings.json"])
            self.assertLessEqual(set(settings), {"disableClaudeAiConnectors", "autoMemoryEnabled",
                                                 "crossSessionInbound", "env"})
            argv_run = zb.claude_argv(req, "/x/claude", Path("/b/tmp"), "/x/python", Path("/srv/zero/broker/inert_mcp.py"))
            self.assertEqual(argv_run[argv_run.index("--setting-sources") + 1], "")
            self.assertIn("--disable-slash-commands", argv_run)
            self.assertNotIn("--plugin-dir", argv_run)
            self.assertNotIn("--add-dir", argv_run)


if __name__ == "__main__":
    unittest.main()
