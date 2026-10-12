"""Zero's plan broker: lets a home on a shared plan use the owner's Claude and ChatGPT plans without ever
holding one of the owner's tokens or reaching one of the owner's connectors.

Runs as the owner (jdoe) from zero-broker.service. Two doors:

1. A Unix socket, /srv/zero/broker/broker.sock (group zero-plans, mode 660). A home's `claude` and `codex`
   are small clients (client.py) that send their argument list here. The broker learns which home is
   calling from the kernel (SO_PEERCRED uid, matched against the root-owned /srv/zero/homes.json), checks
   the arguments against a short allowlist, runs the real program with the owner's sign-in in a per-home
   config folder, and relays stdin, stdout, stderr and the exit status, the way ssh does.
     claude: the DirectSDK handshake Hermes's Claude provider sends, the hub's one-shot Haiku call,
             `auth status` (the account's email removed) and `--version`. Every model run gets
             --strict-mcp-config, an MCP config that holds only the broker's own inert tool inventory,
             --disallowedTools mcp__claude_ai_*, ENABLE_CLAUDEAI_MCP_SERVERS=false and a settings file with
             disableClaudeAiConnectors, so none of the owner's claude.ai connectors ever loads.
             No owner context either: no CLAUDE.md, auto memory, settings, hooks or plugins of his, a
             neutral working folder (/), and every model request passes the broker's ContextFilter, which
             removes the account email Claude Code attaches to each request.
             `claude mcp`, other -p shapes, hooks, plugins and every unknown argument are refused.
     codex:  `app-server` (calls) behind a JSON-RPC filter that only passes the methods a call uses and pins
             every thread to no shell, no apps and read-only; `login status`; `--version`.
             `codex exec` is refused: it would reach the owner's ChatGPT apps.
2. An HTTP proxy on 127.0.0.1:8660 for Hermes's own ChatGPT fallback (HERMES_CODEX_BASE_URL). The path
   carries the home id and a per-home secret; the connecting socket's uid must be that home's. The proxy
   swaps the placeholder Authorization for the owner's current token (read through Hermes's own auth store,
   which handles refresh and its lock) and forwards to chatgpt.com/backend-api/codex.

Nothing here logs a prompt, a body, a header or a token. The log and status file hold counts only.

    zero_broker.py serve                 run the broker (the unit's ExecStart)
    zero_broker.py status                counts per home, the socket, and each home's linked sign-ins
    zero_broker.py add-home HOME         make the home's broker folders; prints its proxy secret with --print-secret
    zero_broker.py remove-home HOME      remove a home's broker folders (the test home only, unless --confirm HOME)
    zero_broker.py relink                re-point every home's sign-in links at the owner's live files
"""

from __future__ import annotations

import argparse
import base64
import hmac
import http.client
import importlib.util
import json
import logging
import os
import pwd
import re
import secrets
import shutil
import signal
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("zero-broker")

HERE = Path(__file__).resolve().parent
SRV = Path(os.environ.get("ZERO_SRV", "/srv/zero"))
OWNER_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


@dataclass
class Config:
    socket_path: Path = SRV / "broker" / "broker.sock"
    registry: Path = SRV / "homes.json"
    state: Path = OWNER_HOME / ".zero-broker"
    claude_bin: str = str(OWNER_HOME / ".local" / "bin" / "claude")
    codex_bin: str = str(OWNER_HOME / ".local" / "bin" / "codex")
    claude_credentials: Path = OWNER_HOME / ".claude" / ".credentials.json"
    codex_auth: Path = OWNER_HOME / ".codex" / "auth.json"
    admission_module: Path = OWNER_HOME / ".taffy" / "plugins" / "claude-subscription-directsdk-experimental" / "admission.py"
    inert_mcp: Path = HERE / "inert_mcp.py"
    python: str = sys.executable
    http_host: str = "127.0.0.1"
    http_port: int = 8660
    codex_upstream: str = "https://chatgpt.com/backend-api/codex"
    anthropic_upstream: str = "https://api.anthropic.com"
    owner_home: Path = OWNER_HOME
    # A home's runs start here: root-owned, no CLAUDE.md, AGENTS.md or .claude/ anyone but root could plant,
    # and a path that says nothing about the owner (Claude Code shows the working folder to the model).
    claude_cwd: str = "/"
    check_tcp_uid: bool = True
    run_timeout: float = 900.0


# ---------------------------------------------------------------------------------------------------------
# Framing between client.py and the broker: one type byte, a 4-byte big-endian length, the payload.
# ---------------------------------------------------------------------------------------------------------

HEADER, STDIN, STDIN_EOF, STDOUT, STDERR, EXIT, REFUSED, SIGNAL = (b"H", b"I", b"E", b"O", b"R", b"X", b"F", b"S")
MAX_FRAME = 64 * 1024 * 1024


def send_frame(sock: socket.socket, kind: bytes, payload: bytes = b"", lock: threading.Lock | None = None) -> None:
    data = kind + struct.pack(">I", len(payload)) + payload
    if lock:
        with lock:
            sock.sendall(data)
    else:
        sock.sendall(data)


def _read_exact(f, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_frame(f) -> tuple[bytes, bytes] | None:
    head = _read_exact(f, 5)
    if head is None:
        return None
    kind, size = head[:1], struct.unpack(">I", head[1:])[0]
    if size > MAX_FRAME:
        raise ValueError("frame too large")
    payload = _read_exact(f, size) if size else b""
    if payload is None:
        return None
    return kind, payload


# ---------------------------------------------------------------------------------------------------------
# What a home may ask for
# ---------------------------------------------------------------------------------------------------------

class Refused(Exception):
    """A request the broker will not run. The message is a plain sentence the home's logs may show."""


NOT_SHARED = "This {tool} command is not available on a shared plan."
REFUSE_MCP = "Claude's connector and MCP settings are not available on a shared plan."
REFUSE_CODEX_EXEC = ("codex exec is not available on a shared plan, because it would reach the owner's "
                     "ChatGPT apps.")

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-\[\]]{0,80}$")
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
# The plugin's handshake (claude-subscription-directsdk-experimental directsdk.py, _run): None marks a value.
DIRECTSDK = ["-p", "--model", None, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
             "--include-partial-messages", "--tools", "", "--system-prompt-file", None, "--settings", None,
             "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands", "--max-turns", "1",
             "--permission-mode", "dontAsk", "--no-session-persistence", "--mcp-config", None]
# The hub's side calls (briefing.ask_haiku): a prompt on stdin, no tools, one JSON answer.
ONESHOT = ["-p", "--model", None, "--tools", "", "--output-format", "json"]
# Environment a client may pass through. Everything else the broker sets itself.
PASS_ENV = {
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": re.compile(r"^[0-9]{1,7}$"),
    "DISABLE_TELEMETRY": re.compile(r"^(0|1|true|false)$"),
    "DISABLE_ERROR_REPORTING": re.compile(r"^(0|1|true|false)$"),
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": re.compile(r"^(0|1|true|false)$"),
    "CLAUDE_CODE_ENABLE_TELEMETRY": re.compile(r"^(0|1|true|false)$"),
}
EXTRA_BODY_DENY = {"mcp_servers"}
MAX_TEXT = 8 * 1024 * 1024


@dataclass
class ClaudeRequest:
    shape: str                       # version | auth-status | auth-status-json | directsdk | oneshot
    model: str | None = None
    effort: str | None = None
    system: str | None = None
    extra_body: str | None = None
    tools: list | None = None
    env: dict = field(default_factory=dict)


def _match(template: list, argv: list) -> list | None:
    """The values at the template's None positions, or None when argv does not have the template's shape."""
    if len(argv) != len(template):
        return None
    values = []
    for want, got in zip(template, argv):
        if not isinstance(got, str):
            return None
        if want is None:
            values.append(got)
        elif want != got:
            return None
    return values


def _filter_env(env: dict) -> dict:
    out = {}
    for k, v in (env or {}).items():
        rule = PASS_ENV.get(k)
        if rule and isinstance(v, str) and rule.match(v):
            out[k] = v
    return out


def _file(files: dict, path: str, what: str) -> str:
    text = files.get(path)
    if not isinstance(text, str):
        raise Refused(f"The {what} file was not sent with the request.")
    if len(text) > MAX_TEXT:
        raise Refused(f"The {what} file is too large.")
    return text


def parse_claude(argv: list, files: dict | None = None, env: dict | None = None) -> ClaudeRequest:
    files = files or {}
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise Refused(NOT_SHARED.format(tool="Claude"))
    if argv == ["--version"] or argv == ["-v"]:
        return ClaudeRequest("version")
    if argv == ["auth", "status"]:
        return ClaudeRequest("auth-status")
    if argv == ["auth", "status", "--json"]:
        return ClaudeRequest("auth-status-json")
    if argv[:1] == ["mcp"] or (argv[:1] != ["-p"] and any(a in ("--mcp-config", "--strict-mcp-config") for a in argv)):
        raise Refused(REFUSE_MCP)

    body = argv
    effort = None
    if len(argv) == len(DIRECTSDK) + 2 and argv[-2] == "--effort":
        body, effort = argv[:-2], argv[-1]
        if effort not in EFFORTS:
            raise Refused("That effort level is not known.")
    values = _match(DIRECTSDK, body)
    if values is not None:
        model, system_path, settings_path, mcp_json = values
        if not MODEL_RE.match(model):
            raise Refused("That model name is not allowed.")
        system = _file(files, system_path, "system prompt")
        extra = _check_settings(_file(files, settings_path, "settings"))
        tools = _check_mcp(mcp_json, files)
        return ClaudeRequest("directsdk", model=model, effort=effort, system=system, extra_body=extra, tools=tools,
                             env=_filter_env(env or {}))
    values = _match(ONESHOT, argv)
    if values is not None:
        if not MODEL_RE.match(values[0]):
            raise Refused("That model name is not allowed.")
        return ClaudeRequest("oneshot", model=values[0], env=_filter_env(env or {}))
    if any(a.startswith("--mcp") or a.startswith("--allowedTools") or a == "--plugin-dir" for a in argv):
        raise Refused(REFUSE_MCP)
    raise Refused(NOT_SHARED.format(tool="Claude"))


def _check_settings(text: str) -> str:
    """The plugin's settings file holds exactly {"env": {"CLAUDE_CODE_EXTRA_BODY": "<json object>"}}.
    Anything else (hooks above all, which would run commands as the owner) is refused."""
    try:
        data = json.loads(text)
    except ValueError:
        raise Refused("The settings file is not valid JSON.")
    if not isinstance(data, dict) or set(data) != {"env"} or not isinstance(data["env"], dict) \
            or set(data["env"]) != {"CLAUDE_CODE_EXTRA_BODY"}:
        raise Refused("Only the request body may be set on a shared plan.")
    extra = data["env"]["CLAUDE_CODE_EXTRA_BODY"]
    if not isinstance(extra, str):
        raise Refused("The request body setting must be text.")
    try:
        parsed = json.loads(extra)
    except ValueError:
        raise Refused("The request body setting is not valid JSON.")
    if not isinstance(parsed, dict):
        raise Refused("The request body setting must be a JSON object.")
    if EXTRA_BODY_DENY & set(parsed):
        raise Refused(REFUSE_MCP)
    return extra


def _check_mcp(mcp_json: str, files: dict) -> list:
    """The plugin's MCP config names exactly one server, `hermes`, its inert tool inventory. The broker runs its
    own copy of that inventory with the manifest the client sent; the home's command line is never run."""
    try:
        data = json.loads(mcp_json)
    except ValueError:
        raise Refused(REFUSE_MCP)
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or set(servers) != {"hermes"} or set(data) != {"mcpServers"}:
        raise Refused(REFUSE_MCP)
    server = servers["hermes"]
    args = server.get("args") if isinstance(server, dict) else None
    if not isinstance(args, list) or len(args) != 2 or not all(isinstance(a, str) for a in args) \
            or not args[0].endswith("inert_mcp.py") or set(server) - {"command", "args", "type"}:
        raise Refused(REFUSE_MCP)
    try:
        manifest = json.loads(_file(files, args[1], "tool list"))
    except ValueError:
        raise Refused("The tool list is not valid JSON.")
    if not isinstance(manifest, list) or not all(isinstance(t, dict) and isinstance(t.get("name"), str)
                                                  for t in manifest):
        raise Refused("The tool list must be a list of tools.")
    return manifest


CONNECTOR_DISALLOW = "mcp__claude_ai_*"
# Claude Code's own context switches, set on every run for a home and never overridable by it: no CLAUDE.md
# (user, project, managed or auto memory), no auto memory (whose folder path names the owner), no git
# instructions. The owner's account email is not covered by any switch; ContextFilter removes it on the wire.
NO_OWNER_CONTEXT_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                        "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1"}


def claude_env(base: dict, req: ClaudeRequest, config_dir: Path, owner_home: Path, claude_bin: str,
               base_url: str | None) -> dict:
    """The run's whole environment. `base_url` is the broker's ContextFilter for model runs."""
    env = {"HOME": str(owner_home), "LANG": base.get("LANG", "C.UTF-8"),
           "PATH": f"{Path(claude_bin).parent}:/usr/local/bin:/usr/bin:/bin",
           "CLAUDE_CONFIG_DIR": str(config_dir),
           "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
           "ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_MAX_RETRIES": "0", "DISABLE_AUTO_COMPACT": "1",
           "DISABLE_COMPACT": "1", "CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off", "DISABLE_AUTOUPDATER": "1"}
    env.update(req.env)
    env.update(NO_OWNER_CONTEXT_ENV)
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    return env


def claude_argv(req: ClaudeRequest, claude_bin: str, tmp: Path, python: str, inert: Path) -> list[str]:
    """The command the broker runs. Built from the parsed request, never copied from the home's argv."""
    if req.shape == "version":
        return [claude_bin, "--version"]
    if req.shape in ("auth-status", "auth-status-json"):
        return [claude_bin, "auth", "status", "--json"]
    if req.shape == "oneshot":
        return [claude_bin, "-p", "--model", req.model, "--tools", "", "--output-format", "json",
                "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {}}),
                "--disallowedTools", CONNECTOR_DISALLOW, "--setting-sources", "",
                "--settings", str(tmp / "settings.json"), "--no-session-persistence", "--disable-slash-commands"]
    mcp = {"mcpServers": {"hermes": {"command": python, "args": [str(inert), str(tmp / "tools.json")]}}}
    argv = [claude_bin, "-p", "--model", req.model, "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages", "--tools", "", "--system-prompt-file", str(tmp / "system.md"),
            "--settings", str(tmp / "settings.json"), "--setting-sources", "", "--strict-mcp-config",
            "--disable-slash-commands", "--max-turns", "1", "--permission-mode", "dontAsk",
            "--no-session-persistence", "--mcp-config", json.dumps(mcp), "--disallowedTools", CONNECTOR_DISALLOW]
    if req.effort:
        argv += ["--effort", req.effort]
    return argv


def claude_files(req: ClaudeRequest) -> dict[str, str]:
    """Files the broker writes into its own temporary folder for one run. crossSessionInbound keeps the
    owner's other Claude Code sessions on this machine from messaging into a home's run."""
    settings: dict = {"disableClaudeAiConnectors": True, "autoMemoryEnabled": False,
                      "crossSessionInbound": "refuse"}
    if req.shape == "directsdk":
        settings["env"] = {"CLAUDE_CODE_EXTRA_BODY": req.extra_body}
        return {"system.md": req.system, "settings.json": json.dumps(settings), "tools.json": json.dumps(req.tools)}
    if req.shape == "oneshot":
        return {"settings.json": json.dumps(settings)}
    return {}


def sanitize_auth_status(raw: str, as_json: bool) -> str:
    """`claude auth status` without the owner's email, organization or anything else that identifies them."""
    try:
        data = json.loads(raw[raw.index("{"):])
    except (ValueError, TypeError):
        data = {}
    keep = {k: data[k] for k in ("loggedIn", "authMethod", "apiProvider", "subscriptionType") if k in data}
    keep["loggedIn"] = bool(data.get("loggedIn"))
    keep["sharedPlan"] = True
    if as_json:
        return json.dumps(keep) + "\n"
    return ("Signed in through a shared plan.\n" if keep["loggedIn"] else "The shared plan is not signed in.\n")


# --- the owner's account context ------------------------------------------------------------------------------
#
# Claude Code prepends a <system-reminder> to the first user turn of every request that names the signed-in
# account ("# userEmail\nThe user's email address is ..."). It reads the address from oauthAccount in
# CLAUDE_CONFIG_DIR/.claude.json, which it fills from the owner's sign-in at every start (a per-home config
# folder holds it again after one run, and blanking it before a run does not help). No setting or variable
# turns it off for a claude.ai sign-in. So every model request of a home's run passes through ContextFilter,
# which removes that section, the claude.ai project and CLAUDE.md sections, and any of the owner's identity
# strings from Claude Code's own reminder blocks. The home's own words (its prompts, its chat) are never edited.

IDENTITY_FIELDS = ("emailAddress", "displayName", "fullName", "organizationName")
OWNER_SECTIONS_RE = re.compile(
    r"^# (?:userEmail|attachedProject|claudeMd)[ \t]*\n.*?(?=^# |^Claude Code attached this context|</system-reminder>|\Z)",
    re.M | re.S)
HIDDEN = "[hidden]"


def owner_identity(config_files: list[Path], extra: list[str] = ()) -> list[str]:
    """The owner's account strings (email, names, organization) from Claude Code's config files, plus `extra`
    (the owner's user name and home folder), longest first. Strings under three characters are ignored."""
    found = set()
    for path in config_files:
        try:
            acct = json.loads(path.read_text()).get("oauthAccount") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(acct, dict):
            continue
        for key in IDENTITY_FIELDS:
            value = acct.get(key)
            if isinstance(value, str) and len(value.strip()) >= 3:
                found.add(value.strip())
    found.update(x for x in extra if isinstance(x, str) and len(x) >= 3)
    return sorted(found, key=len, reverse=True)


def _is_reminder(text) -> bool:
    return isinstance(text, str) and text.lstrip().startswith("<system-reminder>")


def scrub_reminder(text: str, identity: list[str]) -> str:
    out = OWNER_SECTIONS_RE.sub("", text)
    for value in identity:
        out = re.sub(re.escape(value), HIDDEN, out, flags=re.I)
    return out


def scrub_owner_context(payload: bytes, identity: list[str]) -> tuple[bytes, int]:
    """(payload without the owner's account context, number of blocks changed). Only Claude Code's own
    <system-reminder> text blocks are edited. Raises ValueError for a body that is not a Messages request."""
    body = json.loads(payload)
    if not isinstance(body, dict) or not isinstance(body.get("messages", []), list):
        raise ValueError("not a messages request")
    changed = 0
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if _is_reminder(content):
            new = scrub_reminder(content, identity)
            if new != content:
                message["content"], changed = new, changed + 1
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and _is_reminder(block.get("text")):
                    new = scrub_reminder(block["text"], identity)
                    if new != block["text"]:
                        block["text"], changed = new, changed + 1
    if not changed:
        return payload, 0
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), changed


def mentions(payload: bytes, identity: list[str]) -> bool:
    low = payload.lower()
    return any(v.lower().encode() in low for v in identity)


HOP_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "proxy-authorization",
               "proxy-connection", "keep-alive", "te", "upgrade", "accept-encoding"}


class ContextFilter:
    """A loopback relay, owned by the broker, that every model request of a home's run goes through
    (ANTHROPIC_BASE_URL). It removes the owner's account context (scrub_owner_context) and forwards to
    `upstream`: the plugin's admission relay for DirectSDK runs, Anthropic otherwise. Only POST /v1/messages
    passes. A body it cannot read is forwarded only when it holds none of the owner's identity strings.
    Nothing is logged; the Authorization header passes through untouched (it is the owner's, on the owner's
    side) and is never stored."""

    def __init__(self, upstream: str, identity, timeout: float = 900.0):
        self.upstream = urlsplit(upstream)
        self.identity = identity             # () -> list[str], read at request time
        self.timeout = timeout
        self.scrubbed = 0
        self.refused = 0
        self.prefix = "/ctx/" + secrets.token_urlsafe(24)
        flt = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _fail(self, code: int, message: str) -> None:
                body = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                              "message": message}}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._fail(404, "Not found.")

            def do_POST(self):
                path = urlsplit(self.path)
                if path.path != flt.prefix + "/v1/messages":
                    return self._fail(404, "Not found.")
                payload = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if (self.headers.get("Content-Encoding") or "identity").strip().lower() != "identity":
                    # An encoded body cannot be read or searched for the owner's details: never forwarded unread.
                    flt.refused += 1
                    return self._fail(400, "The request could not be checked for the owner's details.")
                identity = flt.identity()
                try:
                    payload, n = scrub_owner_context(payload, identity)
                    flt.scrubbed += n
                except ValueError:
                    if mentions(payload, identity):
                        flt.refused += 1
                        return self._fail(400, "The request could not be checked for the owner's details.")
                flt.forward(self, path.query, payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}{self.prefix}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def forward(self, handler: BaseHTTPRequestHandler, query: str, payload: bytes) -> None:
        up = self.upstream
        if up.scheme == "https":
            conn = http.client.HTTPSConnection(up.hostname, up.port or 443, timeout=self.timeout,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=self.timeout)
        headers = {k: v for k, v in handler.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Accept-Encoding"] = "identity"
        route = up.path.rstrip("/") + "/v1/messages" + (f"?{query}" if query else "")
        try:
            try:
                conn.request("POST", route, payload, headers)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException):
                return handler._fail(502, "The model service could not be reached.")
            handler.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("connection", "transfer-encoding", "server", "date", "content-length",
                                     "keep-alive"):
                    handler.send_header(k, v)
            handler.send_header("Connection", "close")
            handler.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except OSError:
            pass
        finally:
            del headers
            conn.close()
            handler.close_connection = True

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# --- codex ---------------------------------------------------------------------------------------------------

def parse_codex(argv: list) -> str:
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise Refused(NOT_SHARED.format(tool="Codex"))
    if argv in (["--version"], ["-V"]):
        return "version"
    if argv == ["login", "status"]:
        return "login-status"
    if argv == ["app-server"]:
        return "app-server"
    if argv[:1] == ["exec"] or argv[:1] == ["e"]:
        raise Refused(REFUSE_CODEX_EXEC)
    if argv[:1] == ["mcp"] or argv[:1] == ["mcp-server"]:
        raise Refused("Codex's MCP commands are not available on a shared plan.")
    raise Refused(NOT_SHARED.format(tool="Codex"))


def sanitize_login_status(raw: str) -> str:
    low = raw.lower()
    if "not logged in" in low:
        return "Not logged in\n"
    if "logged in" in low and "chatgpt" in low:
        return "Logged in using ChatGPT (shared plan)\n"
    if "api key" in low:
        return "Logged in using an API key (shared plan)\n"
    return "Not logged in\n"


# The settings every shared home's Codex runs with (CODEX_HOME/config.toml): no shell, no browser or computer
# use, no web search, and none of the owner's ChatGPT apps.
CODEX_CONFIG = """# Written by zero-broker for a home on a shared plan. Do not edit: the broker rewrites it.
[features]
shell_tool = false
unified_exec = false
browser_use = false
computer_use = false
multi_agent = false
image_generation = false
memories = false

[tools]
web_search = false

[apps._default]
enabled = false
destructive_enabled = false
"""

APP_SERVER_REQUESTS = {"initialize", "thread/start"}
APP_SERVER_PREFIXES = ("thread/realtime/",)
APP_SERVER_NOTIFICATIONS = {"initialized"}
# The only requests from the app server a home may answer: its own dynamic tools (ask_bot, update_job).
ANSWERABLE = {"item/tool/call"}
THREAD_START_KEYS = {"ephemeral", "model", "baseInstructions", "developerInstructions", "dynamicTools", "config"}


class AppServerFilter:
    """Filters the JSON-RPC lines a home sends to `codex app-server`. Server lines pass unchanged, but the ids
    of the server's own requests are remembered so a home can only answer the kinds it is meant to."""

    def __init__(self):
        self.server_requests: dict = {}
        self.lock = threading.Lock()

    def from_server(self, line: bytes) -> bytes:
        try:
            msg = json.loads(line)
        except ValueError:
            return line
        if isinstance(msg, dict) and "method" in msg and "id" in msg:
            with self.lock:
                self.server_requests[json.dumps(msg["id"])] = msg["method"]
                if len(self.server_requests) > 10000:
                    self.server_requests.pop(next(iter(self.server_requests)))
        return line

    def from_client(self, line: bytes) -> tuple[bytes | None, bytes | None]:
        """(line for the app server or None, reply for the home or None)."""
        try:
            msg = json.loads(line)
        except ValueError:
            return None, None
        if not isinstance(msg, dict):
            return None, None
        method = msg.get("method")
        if method is None:
            if "id" not in msg:
                return None, None
            with self.lock:
                asked = self.server_requests.pop(json.dumps(msg["id"]), None)
            return (line if asked in ANSWERABLE else None), None
        if "id" not in msg:
            ok = method in APP_SERVER_NOTIFICATIONS or method.startswith(APP_SERVER_PREFIXES)
            return (line if ok else None), None
        if method in APP_SERVER_REQUESTS or method.startswith(APP_SERVER_PREFIXES):
            if method == "thread/start":
                msg["params"] = pin_thread(msg.get("params") or {})
                return (json.dumps(msg) + "\n").encode(), None
            return line, None
        err = {"jsonrpc": "2.0", "id": msg["id"],
               "error": {"code": -32601, "message": f"{method} is not available on a shared plan."}}
        return None, (json.dumps(err) + "\n").encode()


def pin_thread(params: dict) -> dict:
    out = {k: v for k, v in params.items() if k in THREAD_START_KEYS}
    cfg = out.get("config") if isinstance(out.get("config"), dict) else {}
    out["config"] = {k: v for k, v in cfg.items() if k == "model_reasoning_effort"}
    out.update(environments=[], sandbox="read-only", approvalPolicy="never")
    return out


# ---------------------------------------------------------------------------------------------------------
# Sign-ins: each home's config folder links to the owner's live credential file
# ---------------------------------------------------------------------------------------------------------

def ensure_link(link: Path, target: Path) -> str:
    """Keeps `link` a symlink to `target`. When a program replaced the link with a newer regular file (a token
    refresh that wrote a new file instead of writing through the link), that newer file's contents become the
    owner's file first, so the single-use refresh token that was just issued is the one everyone keeps.
    Returns created | ok | relinked | adopted-newer."""
    if link.is_symlink():
        if os.readlink(link) == str(target):
            return "ok"
        link.unlink()
        link.symlink_to(target)
        return "relinked"
    if not link.exists():
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        return "created"
    state = "relinked"
    try:
        newer = link.stat().st_mtime > (target.stat().st_mtime if target.exists() else 0)
        content = link.read_bytes()
        json.loads(content)
    except (OSError, ValueError):
        newer = False
    if newer:
        tmp = target.with_name(f".{target.name}.zero-broker-{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp, target)
        state = "adopted-newer"
    link.unlink()
    link.symlink_to(target)
    return state


HOME_ID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")


class Homes:
    """The broker's per-home folders under ~/.zero-broker/homes/<home>/ (mode 700)."""

    def __init__(self, config: Config):
        self.c = config

    def dir(self, home: str) -> Path:
        if not HOME_ID_RE.match(home):
            raise ValueError("bad home id")
        return self.c.state / "homes" / home

    def add(self, home: str) -> str:
        d = self.dir(home)
        for sub in ("claude", "claude-cwd", "codex", "codex-cwd"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        for p in (self.c.state, self.c.state / "homes", d, *(d / s for s in ("claude", "claude-cwd", "codex", "codex-cwd"))):
            os.chmod(p, 0o700)
        (self.c.state / "tmp").mkdir(mode=0o700, exist_ok=True)
        ensure_link(d / "claude" / ".credentials.json", self.c.claude_credentials)
        ensure_link(d / "codex" / "auth.json", self.c.codex_auth)
        cfg = d / "codex" / "config.toml"
        if not cfg.exists() or cfg.read_text() != CODEX_CONFIG:
            cfg.write_text(CODEX_CONFIG)
            os.chmod(cfg, 0o600)
        secret_file = d / "secret"
        if not secret_file.exists():
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
        return secret_file.read_text().strip()

    def secret(self, home: str) -> str | None:
        try:
            return (self.dir(home) / "secret").read_text().strip() or None
        except OSError:
            return None

    def relink(self, home: str) -> dict:
        d = self.dir(home)
        return {"claude": ensure_link(d / "claude" / ".credentials.json", self.c.claude_credentials),
                "codex": ensure_link(d / "codex" / "auth.json", self.c.codex_auth)}

    def link_state(self, home: str) -> dict:
        d = self.dir(home)
        out = {}
        for name, link, target in (("claude", d / "claude" / ".credentials.json", self.c.claude_credentials),
                                   ("codex", d / "codex" / "auth.json", self.c.codex_auth)):
            if link.is_symlink():
                out[name] = "linked" if os.readlink(link) == str(target) else "linked elsewhere"
            elif link.exists():
                out[name] = "separate copy (relink runs before the next use)"
            else:
                out[name] = "missing"
        return out

    def remove(self, home: str) -> None:
        d = self.dir(home)
        if d.exists():
            shutil.rmtree(d)

    def known(self) -> list[str]:
        root = self.c.state / "homes"
        return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


# ---------------------------------------------------------------------------------------------------------
# Counts only
# ---------------------------------------------------------------------------------------------------------

class Stats:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def bump(self, home: str, key: str, n: int = 1) -> None:
        with self.lock:
            h = self.data.setdefault(home, {})
            h[key] = h.get(key, 0) + n
            h["last_at"] = int(time.time())
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
                os.replace(tmp, self.path)
            except OSError:
                pass


# ---------------------------------------------------------------------------------------------------------
# Who is calling
# ---------------------------------------------------------------------------------------------------------

def read_registry(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data.get("homes", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def home_for_uid(uid: int, registry: dict) -> str | None:
    """The shared-plan home whose user has this uid. The owner and own-plan homes get None."""
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        return None
    for home, entry in registry.items():
        if entry.get("user") == name and not entry.get("owner") and entry.get("plan") == "broker":
            return home
    return None


def peer_uid_unix(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


def _hex_addr(ip: str, port: int) -> str:
    packed = socket.inet_aton(ip)
    return "%08X:%04X" % (struct.unpack("<I", packed)[0], port)


def peer_uid_tcp(client: tuple[str, int], server: tuple[str, int], proc_text: str | None = None) -> int | None:
    """The uid that owns the connecting end of a loopback TCP connection, from /proc/net/tcp."""
    if proc_text is None:
        try:
            proc_text = Path("/proc/net/tcp").read_text()
        except OSError:
            return None
    want_local, want_remote = _hex_addr(*client), _hex_addr(*server)
    for line in proc_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) > 7 and parts[1] == want_local and parts[2] == want_remote:
            return int(parts[7])
    return None


# ---------------------------------------------------------------------------------------------------------
# Running a program for a home and relaying it
# ---------------------------------------------------------------------------------------------------------

def load_admission(path: Path):
    """The DirectSDK plugin's own admission relay, run on the owner's side so the owner's token never passes
    through a home's process. None when the plugin is not installed (claude then talks to Anthropic directly)."""
    try:
        spec = importlib.util.spec_from_file_location("zero_broker_admission", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.Admission
    except Exception:
        return None


class Relay:
    """One request on the socket: spawn, pump bytes both ways, report the exit status."""

    def __init__(self, sock: socket.socket, rfile, home: str, tool: str):
        self.sock, self.rfile, self.home, self.tool = sock, rfile, home, tool
        self.wlock = threading.Lock()
        self.proc: subprocess.Popen | None = None

    def send(self, kind: bytes, payload: bytes = b"") -> None:
        try:
            send_frame(self.sock, kind, payload, self.wlock)
        except OSError:
            pass

    def kill(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def run(self, argv: list[str], env: dict, cwd: Path, *, on_stdin_line=None, on_stdout_line=None,
            timeout: float = 900.0) -> int:
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, cwd=str(cwd), start_new_session=True)
        p = self.proc

        def pump_out(stream, kind, per_line):
            try:
                if per_line:
                    for line in iter(stream.readline, b""):
                        self.send(kind, per_line(line))
                else:
                    for chunk in iter(lambda: stream.read1(65536), b""):
                        self.send(kind, chunk)
            except (OSError, ValueError):
                pass

        threads = [threading.Thread(target=pump_out, args=(p.stdout, STDOUT, on_stdout_line), daemon=True),
                   threading.Thread(target=pump_out, args=(p.stderr, STDERR, None), daemon=True)]
        for t in threads:
            t.start()

        def pump_in():
            pending = b""
            try:
                while True:
                    frame = recv_frame(self.rfile)
                    if frame is None:
                        self.kill()          # the home's side went away: never leave a run behind
                        return
                    kind, payload = frame
                    if kind == STDIN:
                        if on_stdin_line:
                            pending += payload
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                forward, reply = on_stdin_line(line + b"\n")
                                if reply:
                                    self.send(STDOUT, reply)
                                if forward:
                                    p.stdin.write(forward)
                            p.stdin.flush()
                        else:
                            p.stdin.write(payload)
                            p.stdin.flush()
                    elif kind == STDIN_EOF:
                        if on_stdin_line and pending:
                            forward, reply = on_stdin_line(pending)
                            if reply:
                                self.send(STDOUT, reply)
                            if forward:
                                p.stdin.write(forward)
                            pending = b""
                        p.stdin.close()
                    elif kind == SIGNAL:
                        self.kill()
                        return
            except (OSError, ValueError):
                self.kill()

        t_in = threading.Thread(target=pump_in, daemon=True)
        t_in.start()
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            code = p.wait()
        for t in threads:
            t.join(timeout=5)
        for pipe in (p.stdin, p.stdout, p.stderr):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        return code


class Broker:
    def __init__(self, config: Config):
        self.c = config
        self.homes = Homes(config)
        self.stats = Stats(config.state / "status.json")
        self.Admission = load_admission(config.admission_module)
        self.codex_tokens = CodexTokens()

    def owner_identity(self, home: str) -> list[str]:
        """What names the owner: the account in his Claude Code configs (his own and the home's broker folder,
        which Claude Code fills from his sign-in), his user name and his home folder."""
        oh = self.c.owner_home
        files = [oh / ".claude.json", oh / ".claude" / ".claude.json", self.homes.dir(home) / "claude" / ".claude.json"]
        return owner_identity(files, [oh.name, str(oh)])

    # --- the socket ---

    def handle(self, sock: socket.socket) -> None:
        with sock.makefile("rb") as rfile:
            self._handle(sock, rfile)

    def _handle(self, sock: socket.socket, rfile) -> None:
        home = home_for_uid(peer_uid_unix(sock), read_registry(self.c.registry))
        try:
            frame = recv_frame(rfile)
        except ValueError:
            frame = None
        if frame is None or frame[0] != HEADER:
            return
        if home is None or self.homes.secret(home) is None:
            send_frame(sock, REFUSED, b"This user is not a home on a shared plan.")
            log.info("refused: unknown caller")
            return
        try:
            header = json.loads(frame[1])
            tool = header.get("tool")
            if tool == "claude":
                self.run_claude(sock, rfile, home, header)
            elif tool == "codex":
                self.run_codex(sock, rfile, home, header)
            else:
                raise Refused("Unknown tool.")
        except Refused as e:
            self.stats.bump(home, f"{header.get('tool', 'unknown') if isinstance(header, dict) else 'unknown'}_refused")
            log.info("refused home=%s tool=%s", home, header.get("tool") if isinstance(header, dict) else "?")
            send_frame(sock, REFUSED, str(e).encode())
        except ValueError:
            send_frame(sock, REFUSED, b"The request was not understood.")

    def run_claude(self, sock, rfile, home: str, header: dict) -> None:
        req = parse_claude(header.get("argv"), header.get("files") or {}, header.get("env") or {})
        hd = self.homes.dir(home)
        self.homes.relink(home)
        tmp = Path(tempfile.mkdtemp(prefix=f"{home}-", dir=self.c.state / "tmp"))
        admission = context = None
        started = time.monotonic()
        try:
            for name, text in claude_files(req).items():
                (tmp / name).write_text(text)
            relay = Relay(sock, rfile, home, "claude")
            if req.shape in ("directsdk", "oneshot"):
                if req.shape == "directsdk" and self.Admission is not None:
                    admission = self.Admission(self.c.anthropic_upstream, self.c.run_timeout)
                context = ContextFilter(admission.url if admission else self.c.anthropic_upstream,
                                        lambda: self.owner_identity(home), self.c.run_timeout)
            env = claude_env(os.environ, req, hd / "claude", self.c.owner_home, self.c.claude_bin,
                             context.url if context else None)
            argv = claude_argv(req, self.c.claude_bin, tmp, self.c.python, self.c.inert_mcp)
            if req.shape in ("version", "auth-status", "auth-status-json"):
                out = subprocess.run(argv, env=env, cwd=self.c.claude_cwd, capture_output=True, text=True,
                                     timeout=60, stdin=subprocess.DEVNULL)
                text = out.stdout if req.shape == "version" else \
                    sanitize_auth_status(out.stdout, req.shape == "auth-status-json")
                relay.send(STDOUT, text.encode())
                relay.send(EXIT, json.dumps({"code": out.returncode}).encode())
                self.stats.bump(home, "claude_checks")
                return

            def watch_stdin(line: bytes):
                # The relay pins the cache breakpoint on the turn being asked; the newest user frame is it.
                if admission is not None:
                    try:
                        frame = json.loads(line)
                        if isinstance(frame, dict) and frame.get("type") == "user":
                            admission.queried = frame["message"]["content"]
                    except (ValueError, KeyError, TypeError):
                        pass
                return line, None

            code = relay.run(argv, env, Path(self.c.claude_cwd),
                             on_stdin_line=watch_stdin if req.shape == "directsdk" else None,
                             timeout=self.c.run_timeout)
            relay.send(EXIT, json.dumps({"code": code}).encode())
            self.stats.bump(home, "claude_runs")
            if context.scrubbed:
                self.stats.bump(home, "owner_context_removed", context.scrubbed)
            if context.refused:
                self.stats.bump(home, "owner_context_refused", context.refused)
            log.info("claude home=%s shape=%s exit=%s seconds=%.1f owner-context-removed=%d", home, req.shape,
                     code, time.monotonic() - started, context.scrubbed)
        finally:
            if context is not None:
                context.close()
            if admission is not None:
                try:
                    admission.close()
                except Exception:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)
            self.homes.relink(home)

    def run_codex(self, sock, rfile, home: str, header: dict) -> None:
        shape = parse_codex(header.get("argv"))
        hd = self.homes.dir(home)
        self.homes.relink(home)
        env = {"HOME": str(OWNER_HOME), "CODEX_HOME": str(hd / "codex"), "LANG": os.environ.get("LANG", "C.UTF-8"),
               "PATH": f"{Path(self.c.codex_bin).parent}:/usr/local/bin:/usr/bin:/bin", "RUST_LOG": "warn"}
        relay = Relay(sock, rfile, home, "codex")
        started = time.monotonic()
        try:
            if shape in ("version", "login-status"):
                argv = [self.c.codex_bin] + (["--version"] if shape == "version" else ["login", "status"])
                out = subprocess.run(argv, env=env, cwd=str(hd / "codex-cwd"), capture_output=True, text=True,
                                     timeout=60, stdin=subprocess.DEVNULL)
                raw = out.stdout + out.stderr
                text = out.stdout if shape == "version" else sanitize_login_status(raw)
                relay.send(STDOUT, text.encode())
                relay.send(EXIT, json.dumps({"code": out.returncode}).encode())
                self.stats.bump(home, "codex_checks")
                return
            filt = AppServerFilter()
            code = relay.run([self.c.codex_bin, "app-server"], env, hd / "codex-cwd",
                             on_stdin_line=filt.from_client, on_stdout_line=filt.from_server,
                             timeout=24 * 3600)
            relay.send(EXIT, json.dumps({"code": code}).encode())
            self.stats.bump(home, "codex_app_server_runs")
            log.info("codex app-server home=%s exit=%s seconds=%.1f", home, code, time.monotonic() - started)
        finally:
            self.homes.relink(home)

    def serve_socket(self) -> socketserver.BaseServer:
        broker = self
        path = self.c.socket_path
        if path.exists() or path.is_symlink():
            path.unlink()

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    broker.handle(self.request)
                except Exception:
                    log.exception("broker request failed")

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        server = Server(str(path), Handler)
        os.chmod(path, 0o660)     # the folder is setgid zero-plans, so the socket's group is zero-plans
        return server


# ---------------------------------------------------------------------------------------------------------
# The Codex proxy for Hermes's own ChatGPT fallback
# ---------------------------------------------------------------------------------------------------------

class CodexTokens:
    """The owner's current ChatGPT access token through Hermes's own auth store (HERMES_HOME is the owner's
    ~/.taffy in the unit), which refreshes under its lock. Cached for a minute."""

    def __init__(self, resolver=None):
        self.resolver = resolver
        self.lock = threading.Lock()
        self.cached: tuple[float, str] | None = None

    def _resolve(self, force: bool) -> str:
        if self.resolver is not None:
            return self.resolver(force)
        from hermes_cli.auth_codex import resolve_codex_runtime_credentials  # type: ignore
        return resolve_codex_runtime_credentials(force_refresh=force)["api_key"]

    def get(self, force: bool = False) -> str:
        with self.lock:
            if not force and self.cached and time.monotonic() - self.cached[0] < 60:
                return self.cached[1]
            token = self._resolve(force)
            self.cached = (time.monotonic(), token)
            return token


def account_headers(token: str) -> dict:
    """The workspace headers chatgpt.com derives from the owner's token (as agent/codex_headers.py does)."""
    out = {}
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        auth = claims.get("https://api.openai.com/auth", {})
        if isinstance(auth.get("chatgpt_account_id"), str) and auth["chatgpt_account_id"]:
            out["ChatGPT-Account-ID"] = auth["chatgpt_account_id"]
        residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
        if isinstance(residency, str) and residency.strip():
            out["x-openai-internal-codex-residency"] = residency.strip()
    except Exception:
        pass
    return out


def identity_headers(token: str) -> dict:
    try:
        from agent.codex_headers import codex_cloudflare_headers  # type: ignore
        return codex_cloudflare_headers(token)
    except Exception:
        return {"User-Agent": "codex_cli_rs/0.0.0 (Hermes Agent)", "originator": "codex_cli_rs", **account_headers(token)}


DROP_HEADERS = {"host", "authorization", "chatgpt-account-id", "x-openai-internal-codex-residency", "cookie",
                "originator", "user-agent", "content-length", "connection", "proxy-authorization", "transfer-encoding",
                "keep-alive", "proxy-connection", "te", "upgrade", "accept-encoding"}
ALLOWED_ROUTES = (("POST", re.compile(r"^responses(/compact)?$")), ("GET", re.compile(r"^models$")))


def route_allowed(method: str, rest: str) -> bool:
    return any(m == method and rx.match(rest) for m, rx in ALLOWED_ROUTES)


def parse_proxy_path(path: str) -> tuple[str, str, str, str] | None:
    """/<home>/<secret>/<rest>?<query> -> (home, secret, rest, query)."""
    parts = urlsplit(path)
    pieces = parts.path.lstrip("/").split("/", 2)
    if len(pieces) != 3 or not HOME_ID_RE.match(pieces[0]):
        return None
    return pieces[0], pieces[1], pieces[2], parts.query


def make_http_server(broker: Broker) -> ThreadingHTTPServer:
    c = broker.c
    up = urlsplit(c.codex_upstream)

    class Handler(BaseHTTPRequestHandler):
        server_version = "zero-broker"
        sys_version = ""

        def log_message(self, *args):
            pass      # never log paths: they carry a home's secret

        def _fail(self, code: int, message: str) -> None:
            body = json.dumps({"error": {"message": message}}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self, method: str) -> None:
            parsed = parse_proxy_path(self.path)
            if not parsed:
                return self._fail(404, "Not found.")
            home, secret, rest, query = parsed
            want = broker.homes.secret(home)
            if not want or not hmac.compare_digest(want, secret):
                return self._fail(404, "Not found.")
            registry = read_registry(c.registry)
            entry = registry.get(home) or {}
            if c.check_tcp_uid:
                uid = peer_uid_tcp(self.client_address[:2], (c.http_host, c.http_port))
                try:
                    expected = pwd.getpwnam(entry.get("user", "")).pw_uid
                except KeyError:
                    expected = None
                if uid is None or uid != expected or entry.get("plan") != "broker":
                    broker.stats.bump(home, "http_refused")
                    return self._fail(403, "This connection does not belong to the home.")
            if not route_allowed(method, rest):
                broker.stats.bump(home, "http_refused")
                return self._fail(404, "That path is not available on a shared plan.")
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_HEADERS}
            headers["Accept-Encoding"] = "identity"
            target = f"{up.path.rstrip('/')}/{rest}" + (f"?{query}" if query else "")
            for attempt in (0, 1):
                try:
                    token = broker.codex_tokens.get(force=attempt == 1)
                except Exception:
                    broker.stats.bump(home, "http_errors")
                    return self._fail(503, "The shared ChatGPT plan needs the owner to sign in again.")
                send = dict(headers, Authorization=f"Bearer {token}", **identity_headers(token))
                if up.scheme == "https":
                    conn = http.client.HTTPSConnection(up.hostname, up.port or 443, timeout=600,
                                                       context=ssl.create_default_context())
                else:
                    conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=600)
                try:
                    conn.request(method, target, body=body, headers=send)
                    resp = conn.getresponse()
                except (OSError, http.client.HTTPException):
                    conn.close()
                    broker.stats.bump(home, "http_errors")
                    return self._fail(502, "The ChatGPT service could not be reached.")
                if resp.status == 401 and attempt == 0:
                    resp.read()
                    conn.close()
                    continue
                break
            del token, send
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("connection", "transfer-encoding", "server", "date", "set-cookie",
                                     "content-length", "keep-alive"):
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            sent = 0
            try:
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    sent += len(chunk)
            except OSError:
                pass
            finally:
                conn.close()
            broker.stats.bump(home, "http_requests")
            broker.stats.bump(home, f"http_{resp.status // 100}xx")
            log.info("codex proxy home=%s route=%s status=%s bytes=%d", home, rest.split("/")[0], resp.status, sent)

        def do_POST(self):
            self._proxy("POST")

        def do_GET(self):
            self._proxy("GET")

    server = ThreadingHTTPServer((c.http_host, c.http_port), Handler)
    server.daemon_threads = True
    return server


# ---------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------

def cmd_serve(c: Config) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for d in (c.state, c.state / "homes", c.state / "tmp"):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
    broker = Broker(c)
    sock_server = broker.serve_socket()
    http_server = make_http_server(broker)
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    log.info("zero-broker listening (socket and loopback proxy); relay %s",
             "on" if broker.Admission else "off (plugin not found)")

    def stop(*_):
        threading.Thread(target=sock_server.shutdown, daemon=True).start()
        http_server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    sock_server.serve_forever()
    return 0


def cmd_status(c: Config) -> int:
    homes = Homes(c)
    print(f"socket: {'present' if c.socket_path.exists() else 'missing'} ({c.socket_path})")
    print(f"owner Claude sign-in file: {'present' if c.claude_credentials.exists() else 'missing'}")
    print(f"owner Codex sign-in file: {'present' if c.codex_auth.exists() else 'missing'}")
    try:
        stats = json.loads((c.state / "status.json").read_text())
    except (OSError, ValueError):
        stats = {}
    for home in homes.known():
        links = homes.link_state(home)
        counts = stats.get(home, {})
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(counts["last_at"])) if counts.get("last_at") else "never"
        shown = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()) if k != "last_at") or "no use yet"
        print(f"{home}: Claude {links['claude']}, Codex {links['codex']}; last use {when}; {shown}")
    if not homes.known():
        print("No homes use the broker yet.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zero-broker", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("status")
    sub.add_parser("relink")
    a = sub.add_parser("add-home")
    a.add_argument("home")
    a.add_argument("--print-secret", action="store_true")
    a.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("remove-home")
    r.add_argument("home")
    r.add_argument("--confirm", default="")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    c = Config()
    if args.cmd == "serve":
        return cmd_serve(c)
    if args.cmd == "status":
        return cmd_status(c)
    homes = Homes(c)
    if args.cmd == "relink":
        for home in homes.known():
            print(home, homes.relink(home))
        return 0
    if args.cmd == "add-home":
        if args.dry_run:
            print(f"would create {homes.dir(args.home)} with linked sign-ins and a proxy secret")
            return 0
        secret = homes.add(args.home)
        print(secret if args.print_secret else f"{args.home}: broker folders ready")
        return 0
    if args.cmd == "remove-home":
        if args.home != "test" and args.confirm != args.home:
            print(f"Refusing: only the test home is removed without --confirm {args.home}.", file=sys.stderr)
            return 2
        if args.dry_run:
            print(f"would remove {homes.dir(args.home)}")
            return 0
        homes.remove(args.home)
        print(f"removed the broker folders for {args.home}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
