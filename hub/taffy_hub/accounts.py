"""Which of the person's accounts the Zips can use: Gmail, Google Calendar, Google Drive, Trello, and every other
connector on the person's Claude account (Notion, Slack, Linear and so on).

The Zips reach these accounts through the connectors on the person's own Claude account, which Claude Code on
this computer picks up when it is signed in with that account (the `connectors` tool in taffy-tools runs
`claude -p` with one connector's tools). A person connects Google on claude.ai, not in Zero, so Zero never
sees a Google password or token, and Google's review of Gmail access is Anthropic's, not Zero's.

This module finds out what is connected without reading any credential:
- `claude auth status --json` says whether Claude Code is signed in and with what kind of login. Connectors
  load only for a Claude plan login, not for an API key or a cloud provider.
- `claude mcp get "claude.ai Gmail"` (one per service, run side by side, about a second each) says whether that
  connector is on the Claude account and connected. Neither command calls the model, so a check costs nothing
  against the plan's limits. Server addresses in the output are never kept or returned.

Other connectors: `claude mcp list` names every connector on the Claude account ("claude.ai Notion: <address> -
✔ Connected"); only the name and the status are kept. It runs at most every LIST_EVERY_SEC, beside the per-service
checks, because it health-checks every server and takes several seconds.

People with only a ChatGPT plan: Codex CLI signed in with ChatGPT can use the Gmail, Google Calendar and Google Drive
apps connected on chatgpt.com (`codex exec`, see the plugin's tools.py). `codex login status` says how Codex is
signed in, and Codex's app server answers `app/installed` with the apps that can be called, without a model call
(docs/qa/2026-10-08-google-connect-v2.md). Each service is used through whichever plan has it connected, Claude
first.

A home that thinks on a plan someone shares with it (home.json `plan.connectors` "none") has no connectors: they
belong to the account that signed in, which is not the person's. Every connector service is then `needs_own_plan`,
no `claude mcp` command runs, and the person's mail and calendar come from the home's own mailboxes and calendars
(mailboxes.py, google.py), which the payload lists under `mailboxes` and `calendars` for every home.

It also raises the "Connect Google" card in a Zip's chat when the Zip needs an account that is not connected,
and picks the Zip's work back up once the account is connected (see docs/specs/2026-10-08-google-connect-design.md).
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, home, store, who

log = logging.getLogger("taffy.accounts")

CONNECT_URL = "https://claude.ai/customize/connectors"
CHATGPT_CONNECT_URL = "https://chatgpt.com/apps"
GOOGLE_CONNECTIONS_URL = "https://myaccount.google.com/connections"


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    connector: str
    provider: str
    group: str
    chatgpt: str = ""


SERVICES = (
    Service("gmail", "Gmail", "claude.ai Gmail", "Google", "google", "Gmail"),
    Service("calendar", "Google Calendar", "claude.ai Google Calendar", "Google", "google", "Google Calendar"),
    Service("drive", "Google Drive", "claude.ai Google Drive", "Google", "google", "Google Drive"),
    Service("trello", "Trello", "claude.ai Trello", "Trello", "other"),
)
BY_ID = {s.id: s for s in SERVICES}

CONNECTED = "connected"
NOT_CONNECTED = "not_connected"
NEEDS_SIGN_IN = "needs_sign_in"
TURNED_OFF = "turned_off"
TROUBLE = "trouble"
CLAUDE_SIGNED_OUT = "claude_signed_out"
CLAUDE_API_KEY = "claude_api_key"      # Claude Code uses an API key or cloud provider: connectors never load
CLAUDE_SIGN_IN_AGAIN = "claude_sign_in_again"
CLAUDE_MISSING = "claude_missing"
UNKNOWN = "unknown"
NEEDS_CLAUDE = "needs_claude"
NO_SUCH = "no_such_account"
NEEDS_OWN_PLAN = "needs_own_plan"
OWN_PLAN_DETAIL = ("Accounts connect through your own Claude or ChatGPT plan. For now, add your mailbox and calendar "
                   "here.")

BROKEN = {NEEDS_SIGN_IN, CLAUDE_SIGN_IN_AGAIN, CLAUDE_SIGNED_OUT, CLAUDE_API_KEY, CLAUDE_MISSING}


def computer() -> str:
    """Where the Zips' Claude runs: the computer this hub runs on. On a server home that is the server, not the
    person's Mac (Oct 8 QA round 2, M6: the screen said to sign in on the Mac)."""
    return "your Mac" if sys.platform == "darwin" else "the computer your Zips use"


def _lead() -> str:
    b = config.BOTS_BY_ID.get("default")
    return b.name if b else "Zero"


def detail(state: str, s: Service, plan: str = "Claude") -> str:
    """One plain sentence for the app. No plumbing words: the person knows Claude, ChatGPT, Google and Trello.
    `plan` is where the person reconnects it: "Claude" or "ChatGPT"."""
    lead, where = _lead(), computer()
    return {
        CONNECTED: "Connected. Your Zips can use it.",
        NOT_CONNECTED: "Not connected yet.",
        NEEDS_SIGN_IN: f"{s.provider} asked you to sign in again. Reconnect it in {plan}.",
        NEEDS_CLAUDE: f"Your Zips can use {s.name} through a Claude plan only, for now.",
        NEEDS_OWN_PLAN: OWN_PLAN_DETAIL,
        TURNED_OFF: f"It is turned off for your Zips. Ask {lead} to turn it back on.",
        TROUBLE: f"Claude couldn't reach {s.name} just now. {lead} will check again soon.",
        CLAUDE_SIGNED_OUT: f"Claude needs to be signed in on {where} first.",
        CLAUDE_API_KEY: f"Claude on {where} isn't signed in with your Claude plan, so it can't use accounts you "
                        "connected in Claude. Sign in with your plan there to use them.",
        CLAUDE_SIGN_IN_AGAIN: f"Claude asked you to sign in again on {where}.",
        CLAUDE_MISSING: f"Claude isn't set up on {where} yet.",
        UNKNOWN: f"{lead} couldn't check just now.",
    }.get(state, f"{lead} couldn't check just now.")


# MARK: Running Claude Code

CHECK_TIMEOUT_SEC = 25


def claude_bin() -> str | None:
    """The home's Claude Code (home.json `plan.claude.command` when it is a path: the plan broker's client in a shared
    home), then the native installer's ~/.local/bin, which the hub's own PATH may not include."""
    given = str(home.get("plan.claude.command") or "")
    if "/" in given and os.access(home.expand(given), os.X_OK):
        return str(home.expand(given))
    for p in (config.HOME / ".local" / "bin" / "claude", config.HOME / ".claude" / "local" / "claude"):
        if os.access(p, os.X_OK):
            return str(p)
    return shutil.which("claude") or next(
        (p for p in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude") if os.access(p, os.X_OK)), None)


def _env() -> dict:
    """The same environment the connectors tool gives `claude -p`: the real home and Claude config, so the
    check sees exactly the login and per-folder switches the Zips' runs see."""
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "HERMES_HOME")}
    env["HOME"] = str(config.HOME)
    env["PATH"] = f"{config.HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    return env


async def _run(*args: str) -> tuple[int, str]:
    """(exit code, output). Raises on a missing binary or a timeout."""
    exe = claude_bin()
    if not exe:
        raise FileNotFoundError("claude")
    proc = await asyncio.create_subprocess_exec(exe, *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
                                                cwd=str(config.HOME), env=_env(), start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), CHECK_TIMEOUT_SEC)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise
    return proc.returncode or 0, out.decode(errors="replace")


# MARK: Reading the answers

def parse_auth(text: str) -> dict:
    """`claude auth status --json` -> {"state": signed_in | signed_out | api_key | unknown, "account": email}.
    Only the login kind and the account's email are kept, never anything else it prints."""
    try:
        d = json.loads(text[text.index("{"):])
    except (ValueError, TypeError):
        return {"state": "unknown", "account": ""}
    if not isinstance(d, dict):
        return {"state": "unknown", "account": ""}
    if not d.get("loggedIn"):
        return {"state": "signed_out", "account": ""}
    method = str(d.get("authMethod") or "").lower()
    if method not in ("claude.ai", "claudeai", "oauth") or str(d.get("apiProvider") or "firstParty") != "firstParty":
        return {"state": "api_key", "account": ""}
    email = str(d.get("email") or "")
    return {"state": "signed_in", "account": email if "@" in email and len(email) < 200 else ""}


def parse_get(text: str) -> str:
    """`claude mcp get "<connector>"` -> one of the states above."""
    low = text.lower()
    if "no mcp server named" in low or "no mcp server found" in low:
        return NOT_CONNECTED
    status = ""
    for line in text.splitlines():
        if line.strip().lower().startswith("status:"):
            status = line.split(":", 1)[1].strip().lower()
            break
    return status_state(status)


def status_state(status: str) -> str:
    """One connector's status words, as `claude mcp get` and `claude mcp list` print them, -> a state above."""
    status = status.strip().lower()
    if not status:
        return UNKNOWN
    if "token rejected" in status or "rejected the session" in status:
        return CLAUDE_SIGN_IN_AGAIN
    if "needs authentication" in status or "re-authoriz" in status or "sign in again" in status:
        return NEEDS_SIGN_IN
    if "disabled" in status or "rejected" in status:
        return TURNED_OFF
    if "failed" in status or "error" in status or "pending" in status:
        return TROUBLE
    if "not configured" in status:
        return NOT_CONNECTED
    if "connected" in status or status.startswith("cached"):
        return CONNECTED
    return UNKNOWN


# MARK: Every other connector on the Claude account

LIST_EVERY_SEC = 120      # `claude mcp list` health-checks every server, so the list of others is reused this long
_KNOWN = {s.connector for s in SERVICES}
_others: dict = {"at": 0.0, "items": []}


def slug(name: str) -> str:
    """The id a Zip uses for a connector: "Hugging Face" -> "hugging_face"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]


def parse_list(text: str) -> list[dict]:
    """`claude mcp list` -> [{"id", "name", "connector", "state"}] for each claude.ai connector that is not one of
    SERVICES. Lines look like "claude.ai Notion: https://... - ✔ Connected". Only the name and the status words are
    kept; the address between them never is."""
    out, seen = [], {s.id for s in SERVICES}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("claude.ai ") or ":" not in line:
            continue
        connector, rest = line.split(":", 1)
        name = connector[len("claude.ai "):].strip()
        sid = slug(name)
        if connector in _KNOWN or not name or not sid or sid in seen or len(name) > 60:
            continue
        seen.add(sid)
        status = rest.rsplit(" - ", 1)[1] if " - " in rest else ""
        out.append({"id": sid, "name": name, "connector": connector, "state": status_state(status)})
    return sorted(out, key=lambda x: x["name"].lower())


async def _list_others(force: bool = False) -> list[dict]:
    if not force and time.time() - _others["at"] < LIST_EVERY_SEC:
        return _others["items"]
    try:
        _, out = await _run("mcp", "list")
        items = parse_list(out)
    except Exception as e:
        log.warning("accounts: listing connectors failed: %s", type(e).__name__)
        return _others["items"]
    _others.update(at=time.time(), items=items)
    return items


def other_service(item: dict) -> Service:
    return Service(item["id"], item["name"], item["connector"], item["name"], "more")


# MARK: The ChatGPT plan (Codex CLI)

def codex_bin() -> str | None:
    """The home's Codex (home.json `plan.chatgpt.codex_command`: the real one, or the plan broker's client), then the
    usual places."""
    given = home.get("plan.chatgpt.codex_command")
    if given and os.access(home.expand(given), os.X_OK):
        return str(home.expand(given))
    for p in (config.HOME / ".local" / "bin" / "codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")):
        if os.access(p, os.X_OK):
            return str(p)
    return shutil.which("codex")


def parse_codex_login(text: str) -> str:
    """`codex login status` -> signed_in (a ChatGPT plan), api_key, signed_out or unknown."""
    low = text.lower()
    if "not logged in" in low:
        return "signed_out"
    if "logged in using chatgpt" in low:
        return "signed_in"
    if "api key" in low or "access token" in low:
        return "api_key"
    return "unknown"


def parse_installed(result: dict) -> dict[str, str]:
    """Codex's `app/installed` answer -> {service id: app id} for each of SERVICES whose ChatGPT app can be called.
    Apps match by the name ChatGPT shows ("Gmail", "Google Calendar", "Google Drive")."""
    by_name = {s.chatgpt.lower(): s.id for s in SERVICES if s.chatgpt}
    out = {}
    for a in (result or {}).get("apps") or []:
        if not isinstance(a, dict) or not a.get("callable") or a.get("enabled") is False:
            continue
        sid = by_name.get(str(a.get("runtimeName") or "").strip().lower())
        if sid and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(a.get("id") or "")):
            out[sid] = a["id"]
    return out


async def _codex_installed(exe: str) -> dict:
    """Asks Codex's app server which apps can be called (no model call; under a second). Raises on a timeout."""
    proc = await asyncio.create_subprocess_exec(exe, "app-server", stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                                cwd=str(config.HOME), env=_env(), start_new_session=True)

    async def talk() -> dict:
        def send(m):
            proc.stdin.write((json.dumps(m) + "\n").encode())
        send({"id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "zero_hub", "version": "1"}, "capabilities": {"experimentalApi": True}}})
        send({"method": "initialized"})
        send({"id": 2, "method": "app/installed", "params": {"forceRefresh": True}})
        await proc.stdin.drain()
        while True:
            line = await proc.stdout.readline()
            if not line:
                return {}
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("id") == 2:
                return m.get("result") or {}
    try:
        return await asyncio.wait_for(talk(), CHECK_TIMEOUT_SEC)
    finally:
        # The whole process group: `codex` is a launcher whose child is the real app server, and killing only the
        # launcher left one app server running after every check (Oct 9: found orphaned on alien).
        from .voice import stop_process
        await stop_process(proc, grace=2)


async def _chatgpt() -> dict:
    """{"state": signed_in | signed_out | api_key | missing | unknown, "apps": {service id: app id}}."""
    exe = codex_bin()
    if not exe:
        return {"state": "missing", "apps": {}}
    try:
        proc = await asyncio.create_subprocess_exec(exe, "login", "status", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
                                                    cwd=str(config.HOME), env=_env(), start_new_session=True)
        out, _ = await asyncio.wait_for(proc.communicate(), CHECK_TIMEOUT_SEC)
        state = parse_codex_login(out.decode(errors="replace"))
        if state != "signed_in":
            return {"state": state, "apps": {}}
        return {"state": state, "apps": parse_installed(await _codex_installed(exe))}
    except Exception as e:
        log.warning("accounts: checking ChatGPT failed: %s", type(e).__name__)
        return {"state": "unknown", "apps": {}}


# MARK: The check, cached

CACHE_SEC = 10 * 60
MIN_GAP_SEC = 3
_last: dict | None = None
_lock: asyncio.Lock | None = None
_lock_loop = None


def _get_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


def plan_of(claude: dict, chatgpt: dict | None) -> str:
    """Which plan the Zips use: claude, chatgpt, both or none. A Claude plan comes first."""
    c, g = claude.get("state") == "signed_in", (chatgpt or {}).get("state") == "signed_in"
    return "both" if c and g else "claude" if c else "chatgpt" if g else "none"


def merge(claude: dict, states: dict[str, str], chatgpt: dict | None) -> tuple[dict[str, str], dict[str, str]]:
    """Each service through whichever plan has it connected, Claude first. Returns (states, via)."""
    apps = (chatgpt or {}).get("apps") or {}
    gpt = (chatgpt or {}).get("state") == "signed_in"
    out, via = {}, {}
    for s in SERVICES:
        st = states.get(s.id, UNKNOWN)
        if st == CONNECTED:
            out[s.id], via[s.id] = st, "claude"
        elif s.id in apps:
            out[s.id], via[s.id] = CONNECTED, "chatgpt"
        elif claude.get("state") == "signed_in" or not gpt:
            out[s.id], via[s.id] = st, "claude"
        else:
            out[s.id], via[s.id] = (NOT_CONNECTED, "chatgpt") if s.chatgpt else (NEEDS_CLAUDE, "claude")
    return out, via


def payload(claude: dict, states: dict[str, str], checked_at: float, chatgpt: dict | None = None,
            others: list[dict] | None = None) -> dict:
    states, via = merge(claude, states, chatgpt)
    plan = plan_of(claude, chatgpt)

    def row(s: Service, st: str, how: str) -> dict:
        return {"id": s.id, "name": s.name, "group": s.group, "state": st, "connected": st == CONNECTED,
                "via": how, "detail": detail(st, s, "ChatGPT" if how == "chatgpt" else "Claude")}
    services = [row(s, states.get(s.id, UNKNOWN), via.get(s.id, "claude")) for s in SERVICES]
    services += [row(other_service(o), o["state"], "claude") for o in others or []]
    google = [x for x in services if x["group"] == "google"]
    return {"checked_at": checked_at, "claude": claude, "plan": plan,
            "chatgpt": {"state": (chatgpt or {}).get("state", "missing")},
            "connect_url": CHATGPT_CONNECT_URL if plan == "chatgpt" else CONNECT_URL,
            "claude_connect_url": CONNECT_URL, "chatgpt_connect_url": CHATGPT_CONNECT_URL,
            "computer": computer(), "google_connections_url": GOOGLE_CONNECTIONS_URL, "services": services,
            "google": {"connected": sum(x["connected"] for x in google), "total": len(google)}}


async def _claude_side() -> tuple[dict, dict[str, str], list[dict]]:
    try:
        _, auth_out = await _run("auth", "status", "--json")
        claude = parse_auth(auth_out)
    except FileNotFoundError:
        claude = {"state": "missing", "account": ""}
    except Exception as e:
        log.warning("accounts: auth status failed: %s", type(e).__name__)
        claude = {"state": "unknown", "account": ""}
    whole = {"signed_out": CLAUDE_SIGNED_OUT, "api_key": CLAUDE_API_KEY, "missing": CLAUDE_MISSING}.get(claude["state"])
    if whole:
        return claude, {s.id: whole for s in SERVICES}, []

    async def one(s: Service) -> str:
        try:
            _, out = await _run("mcp", "get", s.connector)
            return parse_get(out)
        except Exception as e:
            log.warning("accounts: checking %s failed: %s", s.id, type(e).__name__)
            return UNKNOWN
    *found, others = await asyncio.gather(*(one(s) for s in SERVICES), _list_others())
    return claude, dict(zip([s.id for s in SERVICES], found)), others


_apps: dict[str, str] = {}


def shared_payload(checked_at: float) -> dict:
    """A home on a shared plan: no connector is checked or offered; the home's own mailboxes and calendars are."""
    services = [{"id": s.id, "name": s.name, "group": s.group, "state": NEEDS_OWN_PLAN, "connected": False,
                 "via": "none", "detail": OWN_PLAN_DETAIL} for s in SERVICES]
    return {"checked_at": checked_at, "claude": {"state": "shared", "account": ""}, "plan": "shared",
            "connectors": "none", "chatgpt": {"state": "shared"}, "connect_url": None, "claude_connect_url": None,
            "chatgpt_connect_url": None, "computer": computer(), "google_connections_url": GOOGLE_CONNECTIONS_URL,
            "services": services, "google": {"connected": 0, "total": 3}}


def _own_accounts() -> dict:
    """The home's mailboxes, calendars and Google sign-in (mailboxes.status), for every home."""
    from . import mailboxes
    try:
        return mailboxes.status()
    except Exception:
        log.warning("accounts: listing the home's mailboxes failed", exc_info=True)
        return {"mailboxes": [], "calendars": [], "google": {}}


async def _claude_login() -> dict:
    """Only whether the home's Claude Code is signed in, and with what (no connector checks)."""
    try:
        _, out = await _run("auth", "status", "--json")
        return parse_auth(out)
    except FileNotFoundError:
        return {"state": "missing", "account": ""}
    except Exception as e:
        log.warning("accounts: auth status failed: %s", type(e).__name__)
        return {"state": "unknown", "account": ""}


async def own_payload(checked_at: float) -> dict:
    """A home on the person's own plan that does not use Claude or ChatGPT connectors (a new account's home): the
    same rows as a shared home, but the plan and the sign-ins are the person's own, so the app can say which company
    gets their words and offer Sign In With Claude while nothing is signed in."""
    claude, chatgpt = await asyncio.gather(_claude_login(), _chatgpt())
    result = shared_payload(checked_at)
    result.update(claude=claude, chatgpt={"state": chatgpt.get("state", "unknown")}, plan=plan_of(claude, chatgpt))
    return result


async def _check_now() -> dict:
    if home.connectors() == "none":
        result = await own_payload(time.time()) if home.plan_kind() == "own" else shared_payload(time.time())
    else:
        (claude, states, others), chatgpt = await asyncio.gather(_claude_side(), _chatgpt())
        _apps.clear()
        _apps.update(chatgpt.get("apps") or {})
        result = payload(claude, states, time.time(), chatgpt, others)
        result["connectors"] = home.connectors()
    own = await asyncio.to_thread(_own_accounts)
    result.update(mailboxes=own.get("mailboxes", []), calendars=own.get("calendars", []),
                  google_sign_in=own.get("google", {}))
    native(result)
    return result


NATIVE = {"trello": "trello", "drive": "google_drive"}
NATIVE_TOOLS = {"trello": "trello", "drive": "drive"}


def native(result: dict) -> dict:
    """Marks the services connected in Connect Apps as connected "via zero", whatever the Claude or ChatGPT account
    says, so the Zips use the direct tool (and the Connect cards for them close)."""
    try:
        from . import apps
        on = {sid for sid, app_id in NATIVE.items() if apps.connected_natively(app_id)}
    except Exception:
        log.warning("accounts: reading Connect Apps failed", exc_info=True)
        return result
    for row in result.get("services") or []:
        if row.get("id") in on:
            row.update(state=CONNECTED, connected=True, via="zero", detail="Connected in Zero.")
    return result


async def check(force: bool = False) -> dict:
    """A fresh status (or one under MIN_GAP_SEC old when `force`); a cached one under CACHE_SEC otherwise."""
    global _last
    async with _get_lock():
        age = time.time() - _last["checked_at"] if _last else None
        if _last and age is not None and age < (MIN_GAP_SEC if force else CACHE_SEC):
            return _last
        result = _last = await _check_now()
    _remember(result)
    await _after_check(result)
    return result


def cached() -> dict | None:
    return _last


def connected_names() -> list[str]:
    """The accounts the Zips can use right now, by name, from the last check (Welcome's "Your Accounts")."""
    return [x["name"] for x in (_last or {}).get("services", []) if x["connected"]]


def state_of(result: dict, service: str) -> str:
    return next((x["state"] for x in result.get("services", []) if x["id"] == service), UNKNOWN)


# MARK: Telling the person when something they use breaks

ALERT_EVERY_SEC = 30 * 60


def _remember(result: dict) -> None:
    """Services that have ever been connected; only those can "break" (an account never connected is not news)."""
    ever = set(store.get_setting("accounts_ever", []) or [])
    now = {x["id"] for x in result["services"] if x["connected"]}
    if not now <= ever:
        store.set_setting("accounts_ever", sorted(ever | now))


def broken_news(result: dict) -> list[tuple[str, str]]:
    """(service id, sentence) for each service that was connected before and now needs the person, not yet
    told. Clears the told mark of services that work again."""
    ever = set(store.get_setting("accounts_ever", []) or [])
    told = set(store.get_setting("accounts_told", []) or [])
    news, still = [], set()
    for x in result["services"]:
        if x["state"] in BROKEN and x["id"] in ever:
            still.add(x["id"])
            if x["id"] not in told:
                news.append((x["id"], x["state"]))
    if still != told:
        store.set_setting("accounts_told", sorted(still))
    out = []
    claude_wide = [i for i, st in news if st in (CLAUDE_SIGNED_OUT, CLAUDE_API_KEY, CLAUDE_SIGN_IN_AGAIN, CLAUDE_MISSING)]
    if claude_wide:
        out.append(("claude", f"Your Zips can't use your accounts until Claude is signed in again on {computer()}. "
                              "Open Settings, then Connected Accounts."))
    by_id = {x["id"]: x["name"] for x in result["services"]}
    names = [by_id.get(i, i) for i, st in news if st == NEEDS_SIGN_IN]
    if names:
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        out.append(("sign_in", f"{joined} {'needs' if len(names) == 1 else 'need'} you to sign in again before your "
                               "Zips can use it. Open Settings, then Connected Accounts."))
    return out


async def run() -> None:
    """Checks every half hour, so a sign-in that expired is found before a Zip needs it."""
    await asyncio.sleep(20)
    while True:
        try:
            result = await check(force=True)
            from . import watcher
            from . import notices
            for kind, text in broken_news(result):
                await notices.nudge(f"account:{kind}:{text[:80]}", _lead(), text)
        except Exception:
            log.warning("accounts check failed", exc_info=True)
        await asyncio.sleep(ALERT_EVERY_SEC)


# MARK: Cards in a Zip's chat

KIND = "account"


class AskError(Exception):
    pass


def _open_cards(bot_id: str | None = None):
    q = "SELECT * FROM feed WHERE kind=?" + (" AND bot_id=?" if bot_id else "") + " ORDER BY id DESC LIMIT 50"
    for row in store.db().execute(q, (KIND, bot_id) if bot_id else (KIND,)):
        card = json.loads(row["body"] or "{}")
        if card.get("state") == "open":
            yield row, card


def find(service: str) -> Service | None:
    """One of SERVICES, or another connector on the Claude account from the last list."""
    if service in BY_ID:
        return BY_ID[service]
    item = next((o for o in _others["items"] if o["id"] == service), None)
    return other_service(item) if item else None


def ask(bot_id: str, service: str, why: str, session_id: str | None, state: str) -> tuple[int | None, str]:
    """A card in the Zip's chat that opens Connect Google (or Connect Trello, or Connect Notion). (feed id of a new
    card or None when one for the same flow is already open, what to tell the bot)."""
    s = find(service)
    if not s:
        raise AskError("Unknown service.")
    for row, card in _open_cards(bot_id):
        if (BY_ID[card["service"]].group if card.get("service") in BY_ID else "") == s.group == "google" \
                or card.get("service") == s.id:
            store.touch_feed(row["id"])
            return None, (f"{who.possessive()} card to connect {card.get('name') or s.name} is now at the bottom of "
                          f"your chat. Stop working on this now and tell {who.first()} in one short line that the card "
                          "is there; you'll get a note here when it's connected.")
    why = " ".join(str(why or "").split())[:160]
    title = "Connect Google" if s.group == "google" else f"Connect {s.name}"
    fid = store.add_feed(KIND, bot_id, None, title, json.dumps(
        {"service": s.id, "name": s.name, "group": s.group, "why": why, "state": "open", "reason": state,
         "session_id": session_id}))
    return fid, (f"{s.name} isn't connected for your Zips ({detail(state, s)}) {who.first()} now has a card in your "
                 f"chat to connect it, and a notification. Stop working on this now and tell {who.first()} in one "
                 "short line "
                 f"what you'll do once {s.name} is connected. Don't explain how to connect it; the card does. A note "
                 "will arrive in this chat when it's connected, and you'll carry on then.")


def close(bot_id: str, feed_id: int) -> dict:
    """The person closed the card with its X. The Zip was told to stop and wait for this card, so it hears that
    the person closed it and finishes what it can (Oct 8 QA round 2, M5)."""
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?", (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise AskError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("state") == "open":
        card.update(state="declined", at=time.time())
        store.update_feed(feed_id, row["title"], json.dumps(card))
        name = card.get("name") or "the account"
        note = (f"{who.hub_note()} {who.first()} closed the card without connecting {name}. Finish "
                f"what you can without it, and tell {who.first()} in a sentence what you could not do.")
        try:
            asyncio.get_running_loop().create_task(_continue(bot_id, note, card.get("session_id"), urgent=False))
        except RuntimeError:
            pass
    return card


def resolve_connected(result: dict) -> list[tuple[str, str, str | None]]:
    """Open cards whose account is now connected are marked done. Returns (bot id, note, the card's conversation)
    for each Zip to wake."""
    now = {x["id"] for x in result.get("services", []) if x["connected"]}
    wake = []
    for row, card in list(_open_cards()):
        if card.get("service") in now:
            card.update(state="connected", at=time.time())
            store.update_feed(row["id"], row["title"], json.dumps(card))
            name = card.get("name") or card["service"]
            via = next((x.get("via") for x in result.get("services", []) if x["id"] == card.get("service")), "")
            tool = f"the {NATIVE_TOOLS[card['service']]} tool" if via == "zero" and card.get("service") in NATIVE_TOOLS \
                else "the connectors tool again"
            wake.append((row["bot_id"], f"{who.hub_note()} {name} is connected now. Carry on with what "
                                        f"{who.first()} asked; use {tool}.", card.get("session_id")))
    return wake


async def _after_check(result: dict) -> None:
    for bot_id, note, session_id in resolve_connected(result):
        asyncio.get_running_loop().create_task(_continue(bot_id, note, session_id))


async def _continue(bot_id: str, note: str, session_id: str | None = None, urgent: bool = True) -> None:
    """The Zip picks the conversation back up in the chat the card was in, with the person's request counting
    again for that turn, and its reply is pushed like any answer to a card (Oct 8 QA round 2, M5: the reply
    arrived silently, and a sign-in it then needed got a "working on its own" card)."""
    from . import turns
    try:
        await turns.follow_up(bot_id, note, session_id, urgent=urgent)
    except Exception:
        log.exception("could not pick %s back up after an account card", bot_id)


async def need(bot_id: str, service: str, why: str, session_id: str | None, failed: bool = False) -> dict:
    """The connectors tool asks before (and, with `failed`, after) a run. Connected: go ahead. Otherwise a card
    goes up in the Zip's chat and the Zip is told to stop. A status we cannot read never blocks the Zip."""
    if service in NATIVE:
        try:
            from . import apps
            if apps.connected_natively(NATIVE[service]):
                return {"connected": True, "state": CONNECTED, "via": "zero", "tool": NATIVE_TOOLS[service]}
        except Exception:
            log.warning("accounts: reading Connect Apps failed", exc_info=True)
    if home.connectors() == "none":
        return {"connected": False, "state": NEEDS_OWN_PLAN, "message": no_connectors_message()}
    result = await check()
    if not find(service):
        await _list_others(force=True)
        if not find(service):
            return {"connected": False, "state": NO_SUCH, "message": no_such(service, result)}
    st = state_of(result, service)
    if failed or st != CONNECTED:
        result = await check(force=True)
        st = state_of(result, service)
    if st in (CONNECTED, UNKNOWN, TROUBLE):
        return {"connected": True, "state": st, **route(result, service)}
    fid, message = ask(bot_id, service, why, session_id, st)
    return {"connected": False, "state": st, "card": fid, "message": message}


def route(result: dict, service: str) -> dict:
    """How the connectors tool reaches the account: through Claude (the connector's name in Claude Code) or
    through ChatGPT (the app's id in Codex)."""
    row = next((x for x in result.get("services", []) if x["id"] == service), {})
    if row.get("via") == "chatgpt":
        return {"via": "chatgpt", "app": _apps.get(service, "")}
    s = find(service)
    return {"via": "claude", "connector": s.connector if s else ""}


def no_such(service: str, result: dict) -> str:
    names = [f'{x["id"]} ({x["name"]})' for x in result.get("services", []) if x["connected"]]
    return (f"There is no account called \"{service}\" in {who.possessive()} Claude account. "
            + (f"Accounts your Zips can use now: {', '.join(names)}." if names else "No accounts are connected now.")
            + f" Use one of those, or tell {who.first()} what you would need.")


def no_connectors_message() -> str:
    """What a Zip hears when it asks for a connector in a home without connectors."""
    return ("Connected accounts are not available in this home. Use the email tool for mail and the calendar tool "
            f"for the calendar, a browser for other sites, or tell {who.first()} what you would need.")
