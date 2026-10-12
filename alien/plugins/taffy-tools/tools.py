"""Handlers for the Taffy team's tools. Handlers never raise; they return JSON text."""

import json
import os
import pwd
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request

# Each connector is allowed as a server, minus the tools that share, trash, delete, mark spam or answer
# invitations on Colin's behalf. (Listing allowed tools one by one stops Claude Code's ToolSearch from loading
# them, so the guard is a deny list.) Labels stay on: archiving and labeling mail Colin asked to clean up is a
# reversible change the Zip does and then mentions, as the house rules and the Connect screen say (Oct 8 QA
# round 2, L3). The permission gate decides each request before this runs (tiers.classify_connector).
CONNECTOR_SERVERS = {
    "trello": "mcp__claude_ai_Trello",
    "calendar": "mcp__claude_ai_Google_Calendar",
    "drive": "mcp__claude_ai_Google_Drive",
    "gmail": "mcp__claude_ai_Gmail",
}
_DENIED = {
    "trello": ["trelloWriteBoard", "trelloWriteWorkspace"],
    "calendar": ["delete_event", "respond_to_event"],
    "drive": ["share_file", "trash_file", "update_file"],
    "gmail": ["trash_message", "trash_thread", "untrash_message", "untrash_thread", "mark_message_spam",
              "mark_thread_spam", "unmark_message_spam", "unmark_thread_spam", "apply_sensitive_message_label",
              "apply_sensitive_thread_label"],
}
CONNECTOR_TIMEOUT_SEC = 240
# Bots may run with a per-profile HOME; logins (Claude, himalaya) and Tally live in the real one.
REAL_HOME = os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir


def _tally_settings() -> dict:
    """Where Tally answers and where its key lives (integrations.tally); empty when the home has no Tally."""
    from . import home
    return home.tally() or {}


def tally_url() -> str:
    return str(_tally_settings().get("url") or "http://127.0.0.1:18930").rstrip("/")


def tally_env() -> str:
    from . import home
    return str(home.expand(_tally_settings().get("env_file") or "~/tally/server/.env", REAL_HOME))


# The model that runs account lookups (Trello, Calendar, Drive, Gmail search).
CONNECTOR_MODEL = "claude-haiku-5-5"


def _person() -> str:
    """The person's name: the home's settings, then the onboarding answer the hub keeps (home.full_name)."""
    from . import home
    return home.full_name(REAL_HOME)


def _timezone() -> str:
    from . import home
    return home.timezone()


# Words Claude Code's reply uses when the connector's tools never loaded (not added, signed out, expired).
_MISSING_WORDS = ("needs authentication", "need to authenticate", "not authenticated", "isn't connected",
                  "is not connected", "not connected", "isn't available", "is not available", "not available",
                  "unavailable", "no such tool", "don't have access", "do not have access", "no tools",
                  "re-authorize", "reauthorize", "sign in again", "authorize it")


_route = threading.local()   # how the last _account_check in this thread said to reach the account


def _account_check(service: str, request: str, session_id: str, failed: bool = False) -> str | None:
    """Ask the hub whether the person's account is connected (see the hub's accounts.py). When it isn't, the hub
    puts a Connect card in this Zip's chat and the text returned here tells the Zip to stop. None: go ahead
    (connected, or the hub couldn't tell, which never blocks the Zip). The hub also says which plan reaches the
    account, kept in _route.value: {"via": "claude", "connector": "claude.ai Notion"} or {"via": "chatgpt",
    "app": "<ChatGPT app id>"}."""
    from . import mac
    _route.value = {}
    reply = mac._call("need", {"service": service, "why": request[:160], "failed": failed}, 45,
                      session_id=session_id, area="accounts")
    result = reply.get("result") if reply.get("ok") else None
    if isinstance(result, dict) and result.get("connected") is False:
        return result.get("message") or f"{service} isn't connected."
    _route.value = result if isinstance(result, dict) else {}
    return None


def _limits(service: str, request: str) -> str:
    """A calendar request the gate let through as a plain change, not an invitation, must not reach anyone else:
    adding a guest emails them from the person's account (Oct 8 QA round 2, H3). "Dinner with Riley" makes an
    event called that, with no guests."""
    from . import tiers
    try:
        kind = tiers.classify_connector(service, request).kind
    except Exception:
        kind = ""
    if service == "calendar" and kind != "send":
        return (" This request is not an invitation: do not add, change or remove attendees or guests on any event, "
                "and do not send invitations or event updates to anyone. A person's name in it is part of the event's "
                "title. If it cannot be done without adding someone, stop and say so.")
    if service == "gmail" and kind != "send":
        return " This request does not send anything: leave drafts as drafts."
    return ""


# MARK: Other connectors on the person's Claude account (Notion, Slack, Linear and so on)

SERVICE_ID = re.compile(r"^[a-z0-9_]{2,40}$")
# Their tools are unknown ahead of time, so every tool of that connector goes through a hook (connector_guard.py)
# that lets a tool run only when its name says it does what this request may do: reading tools on every run, ordinary
# changes on a change, and a send, publish, delete or share only when the gate asked Colin about this exact request
# and he said yes. Tools that move money never run. Names that say nothing about what they do stay open (Oct 8 QA
# round 9, M1 and L1; before, only names with a send, delete or share word were blocked).
GUARD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "connector_guard.py")


def guard_kinds(service: str, request: str) -> list[str]:
    """Every kind of action the request asks for (tiers.classify_connector), for the run's guard."""
    from . import tiers
    try:
        a = tiers.classify_connector(service, request)
    except Exception:
        return ["read"]
    return list(a.extra.get("kinds") or [a.kind])


def _server_of(connector: str) -> str:
    """Claude Code's tool prefix for a claude.ai connector: "claude.ai Google Calendar" -> mcp__claude_ai_Google_Calendar."""
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", connector)


def _guard(server: str, kinds: list[str]) -> str:
    """Settings for `claude --settings`: a PreToolUse hook on every tool of this connector that runs
    connector_guard.py. The guard exits 2 to block; if it cannot start at all, the command still exits 2."""
    import shlex
    python = sys.executable or "python3"
    command = " ".join(shlex.quote(a) for a in [python, "-I", GUARD_SCRIPT, server, *kinds]) + " || exit 2"
    hook = {"type": "command", "command": command}
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": f"^{server}__.*$", "hooks": [hook]}]}})


# MARK: The ChatGPT plan: Codex CLI with the person's ChatGPT apps

# The model for account lookups on a ChatGPT plan: the plan's fast model (codex models list, Oct 8).
CHATGPT_MODEL = "gpt-6-luna"
# ChatGPT's tools that send, delete, share, answer invitations or change existing files. Each is switched off for the
# run (apps.<id>.tools.<tool>.enabled = false), like _DENIED for Claude. Names from Codex's app/read on Oct 8.
CHATGPT_DENIED = {
    "gmail": ["send_email", "send_draft", "forward_emails", "delete_emails"],
    "calendar": ["delete_event", "respond_event"],
    "drive": ["share_file", "delete_file", "update_file", "batch_update_document", "batch_update_presentation",
              "batch_update_spreadsheet", "bulk_update_file_comments"],
}


def codex_command(service: str, app: str, prompt: str) -> list[str] | None:
    """`codex exec` with only this one ChatGPT app: no shell, browser, web search or other apps, read-only files,
    the person's own Codex settings and servers ignored, and tools that advertise themselves as destructive off."""
    if service not in CHATGPT_DENIED or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", app or ""):
        return None
    cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
           "-s", "read-only", "-m", CHATGPT_MODEL, "--json", "-C", REAL_HOME]
    for feature in ("shell_tool", "unified_exec", "browser_use", "computer_use", "multi_agent", "image_generation",
                    "memories"):
        cmd += ["--disable", feature]
    cmd += ["-c", "tools.web_search=false", "-c", "apps._default.enabled=false",
            "-c", "apps._default.destructive_enabled=false", "-c", f"apps.{app}.enabled=true"]
    for tool in CHATGPT_DENIED[service]:
        cmd += ["-c", f"apps.{app}.tools.{tool}.enabled=false"]
    return cmd + [prompt]


def parse_codex(stdout: str) -> dict:
    """`codex exec --json` events -> {"result": the last message, "is_error": bool}."""
    text, error = "", False
    for line in stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict):
            continue
        item = m.get("item") or {}
        if m.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
        elif m.get("type") in ("turn.failed", "error"):
            error = True
            text = text or str((m.get("error") or {}).get("message") or m.get("message") or "The run failed.")
    return {"result": text, "is_error": error and not text}


def _run(cmd: list[str], service: str) -> tuple[subprocess.CompletedProcess | None, str]:
    try:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "HERMES_HOME", "CODEX_HOME")}
        env["HOME"] = REAL_HOME
        env["PATH"] = f"{REAL_HOME}/.local/bin:" + env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        # Wait for the claude.ai connectors to connect before the turn starts; otherwise the model
        # sometimes answers before their tools exist and reports that no connector is available.
        env["MCP_CONNECTION_NONBLOCKING"] = "false"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                stdin=subprocess.DEVNULL, cwd=REAL_HOME, env=env, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=CONNECTOR_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            return None, json.dumps({"error": f"{service} request timed out after {CONNECTOR_TIMEOUT_SEC} s."})
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr), ""
    except Exception as e:
        return None, json.dumps({"error": f"Could not run the {service} request: {e}"})


def connectors(args: dict, **kwargs) -> str:
    service = str(args.get("service") or "").strip().lower()
    request = (args.get("request") or "").strip()
    if not SERVICE_ID.match(service) or not request:
        return json.dumps({"error": "Need service (gmail, calendar, drive, trello, or another connected account by "
                                    "name, like notion or slack) and request."})
    session_id = kwargs.get("session_id") or ""
    _route.value = {}
    missing = _account_check(service, request, session_id)
    if missing:
        return json.dumps({"error": missing, "account_not_connected": True})
    route = getattr(_route, "value", None) or {}
    if route.get("via") == "zero" and route.get("tool"):
        # Connected in Zero's Connect Apps: the direct tool reads in a second and asks with a card before a change.
        return json.dumps({"error": f"{service} is connected directly in Zero. Use the {route['tool']} tool instead; "
                                    "it is faster.", "use_tool": route["tool"]})
    prompt = (
        f"You are acting for {_person()} (time zone {_timezone()}) through their {service} connector. "
        "Do exactly this request with the available tools, then reply with a short plain report of what you "
        "did or found, including names, ids, dates and links that matter. If something is ambiguous or "
        "missing, do not guess: say what is missing. Never send email, never add guests or attendees to an event "
        "and never send invitations unless this request names them exactly; never share, delete or empty anything "
        "unless this request says so exactly. Never pay, refund, charge or transfer money, and never start, change or "
        "cancel a subscription, an invoice or a payment link." + _limits(service, request) + "\n\nRequest: " + request
    )
    via = route.get("via") or "claude"
    if via == "chatgpt":
        # Only a ChatGPT plan has this account: Codex runs it with the person's ChatGPT app.
        cmd = codex_command(service, str(route.get("app") or ""), prompt)
        if not cmd:
            return json.dumps({"error": f"{service} can't be reached through ChatGPT right now."})
        out, err = _run(cmd, service)
        if err:
            return err
        data = parse_codex(out.stdout)
    else:
        server = CONNECTOR_SERVERS.get(service) or (_server_of(route["connector"]) if route.get("connector") else "")
        if not server:
            from . import home
            return json.dumps({"error": home.say("Zero couldn't find {service} among Colin's accounts just now. Try "
                                                 "again in a minute, or use another account.", service=service)})
        # Claude Code loads connector tools on demand through its ToolSearch tool, so it must stay enabled;
        # every other built-in tool stays off, and only this one connector's tools are allowed.
        cmd = ["claude", "-p", prompt, "--model", CONNECTOR_MODEL, "--tools", "ToolSearch",
               "--allowedTools", f"{server},ToolSearch", "--output-format", "json"]
        if service in _DENIED:
            cmd += ["--disallowedTools", ",".join(f"{server}__{t}" for t in _DENIED[service])]
        else:
            cmd += ["--settings", _guard(server, guard_kinds(service, request))]
        out, err = _run(cmd, service)
        if err:
            return err
        try:
            data = json.loads(out.stdout)
        except ValueError:
            return json.dumps({"error": "Connector run failed.", "stderr": out.stderr[-800:],
                               "stdout": out.stdout[-800:]})
    text = str(data.get("result", ""))
    # The run itself found the account missing (it expired between checks): the hub looks again and asks.
    if data.get("is_error") or any(w in text.lower() for w in _MISSING_WORDS):
        missing = _account_check(service, request, session_id, failed=True)
        if missing:
            return json.dumps({"error": missing, "account_not_connected": True})
    return json.dumps({"service": service, "result": text, "is_error": data.get("is_error", False)})


def _tally_key():
    try:
        with open(tally_env()) as f:
            for line in f:
                if line.startswith("API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def _tally(method, path):
    key = _tally_key()
    if not key:
        raise RuntimeError("Tally API key not found")
    req = urllib.request.Request(tally_url() + path, method=method, headers={"x-api-key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _with_relink(status: dict) -> dict:
    """Says in words which bank needs Colin to sign in again (Tally marks it needs_relink), so Money can tell
    him instead of quietly reporting stale spending (Oct 8 QA, M7 and H6)."""
    if not isinstance(status, dict):
        return status
    stale = [i.get("institution") or "One bank" for i in status.get("items") or []
             if isinstance(i, dict) and i.get("needs_relink")]
    if stale:
        from . import home
        status["needs_relink"] = home.say("{banks} needs Colin to sign in again in Tally before new "
                                          "transactions come in. Tell him; spending from it may be out of date.",
                                          banks=", ".join(stale))
    status["balances"] = "Not available: Tally stores transactions only."
    return status


def tally(args: dict, **kwargs) -> str:
    action = args.get("action")
    try:
        if action == "status":
            return json.dumps(_with_relink(_tally("GET", "/api/status")))
        if action == "refresh":
            return json.dumps(_with_relink(_tally("POST", "/api/refresh")))
        if action == "purchases":
            data = _tally("GET", "/api/purchases")
            start, end = args.get("start_date") or "", args.get("end_date") or "9999"
            keep = lambda rows: [r for r in rows if start <= str(r.get("date", "")) <= end]
            purchases, income = keep(data.get("purchases", [])), keep(data.get("income", []))
            limit = 400
            return json.dumps({"purchases": purchases[:limit], "income": income[:limit],
                               "truncated": len(purchases) > limit, "status": data.get("status")})
        return json.dumps({"error": "action must be purchases, status or refresh"})
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"Tally returned {e.code}"})
    except Exception as e:
        return json.dumps({"error": f"Tally request failed: {e}"})
