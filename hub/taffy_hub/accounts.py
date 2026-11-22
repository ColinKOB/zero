""

































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
    Service("trello", "Trello", "claude.ai Trello", "Trello", "other", "Trello"),
)
BY_ID = {s.id: s for s in SERVICES}


CONNECTED = "connected"
NOT_CONNECTED = "not_connected"
NEEDS_SIGN_IN = "needs_sign_in"
TURNED_OFF = "turned_off"
TROUBLE = "trouble"
CLAUDE_SIGNED_OUT = "claude_signed_out"
CLAUDE_API_KEY = "claude_api_key"
CLAUDE_SIGN_IN_AGAIN = "claude_sign_in_again"
CLAUDE_MISSING = "claude_missing"
UNKNOWN = "unknown"
NEEDS_CLAUDE = "needs_claude"
NO_SUCH = "no_such_account"
NEEDS_OWN_PLAN = "needs_own_plan"
CONNECTIONS_OFF = "connections_off"
OWN_PLAN_DETAIL = ("Accounts connect through your own Claude or ChatGPT plan. For now, add your mailbox and calendar "
                   "here.")


BROKEN = {NEEDS_SIGN_IN, CLAUDE_SIGN_IN_AGAIN, CLAUDE_SIGNED_OUT, CLAUDE_API_KEY, CLAUDE_MISSING}


def computer() -> str:
    ""

    return "your Mac" if sys.platform == "darwin" else "the computer your Zips use"


def _lead() -> str:
    b = config.BOTS_BY_ID.get("default")
    return b.name if b else "Zero"


def detail(state: str, s: Service, plan: str = "Claude") -> str:
    ""

    lead, where = _lead(), computer()
    return {
        CONNECTED: "Connected. Your Zips can use it.",
        NOT_CONNECTED: "Not connected yet.",
        NEEDS_SIGN_IN: f"{s.provider} asked you to sign in again. Reconnect it in {plan}.",
        NEEDS_CLAUDE: f"Your Zips can use {s.name} through a Claude plan only, for now.",
        NEEDS_OWN_PLAN: OWN_PLAN_DETAIL,
        CONNECTIONS_OFF: "Off. Turn on Use My Claude Connections to let your Zips use it.",
        TURNED_OFF: f"It is turned off for your Zips. Ask {lead} to turn it back on.",
        TROUBLE: f"Claude couldn't reach {s.name} just now. {lead} will check again soon.",
        CLAUDE_SIGNED_OUT: f"Claude needs to be signed in on {where} first.",
        CLAUDE_API_KEY: f"Claude on {where} isn't signed in with your Claude plan, so it can't use accounts you "
                        "connected in Claude. Sign in with your plan there to use them.",
        CLAUDE_SIGN_IN_AGAIN: f"Claude asked you to sign in again on {where}.",
        CLAUDE_MISSING: f"Claude isn't set up on {where} yet.",
        UNKNOWN: f"{lead} couldn't check just now.",
    }.get(state, f"{lead} couldn't check just now.")




CHECK_TIMEOUT_SEC = 25


def claude_bin() -> str | None:
    ""

    given = str(home.get("plan.claude.command") or "")
    if "/" in given and os.access(home.expand(given), os.X_OK):
        return str(home.expand(given))
    for p in (config.HOME / ".local" / "bin" / "claude", config.HOME / ".claude" / "local" / "claude"):
        if os.access(p, os.X_OK):
            return str(p)
    return shutil.which("claude") or next(
        (p for p in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude") if os.access(p, os.X_OK)), None)


def _env() -> dict:
    ""

    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "HERMES_HOME")}
    env["HOME"] = str(config.HOME)
    env["PATH"] = f"{config.HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    return env


async def _run(*args: str) -> tuple[int, str]:
    ""
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




def parse_auth(text: str) -> dict:
    ""

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
    ""
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
    ""
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




LIST_EVERY_SEC = 120
_KNOWN = {s.connector for s in SERVICES}
_others: dict = {"at": 0.0, "items": []}


def slug(name: str) -> str:
    ""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]


def parse_list(text: str) -> list[dict]:
    ""


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




def codex_bin() -> str | None:
    ""

    given = home.get("plan.chatgpt.codex_command")
    if given and os.access(home.expand(given), os.X_OK):
        return str(home.expand(given))
    for p in (config.HOME / ".local" / "bin" / "codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")):
        if os.access(p, os.X_OK):
            return str(p)
    return shutil.which("codex")


def parse_codex_login(text: str) -> str:
    ""
    low = text.lower()
    if "not logged in" in low:
        return "signed_out"
    if "logged in using chatgpt" in low:
        return "signed_in"
    if "api key" in low or "access token" in low:
        return "api_key"
    return "unknown"


def parse_installed(result: dict) -> dict[str, str]:
    ""

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
    ""
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


        from .voice import stop_process
        await stop_process(proc, grace=2)


async def _chatgpt() -> dict:
    ""
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
    ""
    c, g = claude.get("state") == "signed_in", (chatgpt or {}).get("state") == "signed_in"
    return "both" if c and g else "claude" if c else "chatgpt" if g else "none"


def claude_first() -> bool:
    ""



    models = home.get("models")
    use = models.get("use") if isinstance(models, dict) else None
    if not isinstance(use, list) or not use:
        return True
    return "claude" in [str(u).strip().lower() for u in use]


def merge(claude: dict, states: dict[str, str], chatgpt: dict | None,
          prefer_claude: bool = True) -> tuple[dict[str, str], dict[str, str]]:
    ""

    apps = (chatgpt or {}).get("apps") or {}
    gpt = (chatgpt or {}).get("state") == "signed_in"
    out, via = {}, {}
    for s in SERVICES:
        st = states.get(s.id, UNKNOWN)
        if st == CONNECTED and (prefer_claude or s.id not in apps):
            out[s.id], via[s.id] = st, "claude"
        elif s.id in apps:
            out[s.id], via[s.id] = CONNECTED, "chatgpt"
        elif (claude.get("state") == "signed_in" and prefer_claude) or not gpt:
            out[s.id], via[s.id] = st, "claude"
        else:
            out[s.id], via[s.id] = (NOT_CONNECTED, "chatgpt") if s.chatgpt else (NEEDS_CLAUDE, "claude")
    return out, via


def payload(claude: dict, states: dict[str, str], checked_at: float, chatgpt: dict | None = None,
            others: list[dict] | None = None, prefer_claude: bool = True) -> dict:
    states, via = merge(claude, states, chatgpt, prefer_claude)
    plan = plan_of(claude, chatgpt)

    def row(s: Service, st: str, how: str) -> dict:
        return {"id": s.id, "name": s.name, "group": s.group, "state": st, "connected": st == CONNECTED,
                "via": how, "detail": detail(st, s, "ChatGPT" if how == "chatgpt" else "Claude")}
    services = [row(s, states.get(s.id, UNKNOWN), via.get(s.id, "claude")) for s in SERVICES]
    services += [row(other_service(o), o["state"], "claude") for o in others or []]
    google = [x for x in services if x["group"] == "google"]
    return {"checked_at": checked_at, "claude": claude, "plan": plan,
            "chatgpt": {"state": (chatgpt or {}).get("state", "missing")},
            "connect_url": CHATGPT_CONNECT_URL if plan == "chatgpt" or (plan == "both" and not prefer_claude)
            else CONNECT_URL,
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
    ""
    services = [{"id": s.id, "name": s.name, "group": s.group, "state": NEEDS_OWN_PLAN, "connected": False,
                 "via": "none", "detail": OWN_PLAN_DETAIL} for s in SERVICES]
    return {"checked_at": checked_at, "claude": {"state": "shared", "account": ""}, "plan": "shared",
            "connectors": "none", "chatgpt": {"state": "shared"}, "connect_url": None, "claude_connect_url": None,
            "chatgpt_connect_url": None, "computer": computer(), "google_connections_url": GOOGLE_CONNECTIONS_URL,
            "services": services, "google": {"connected": 0, "total": 3}}


def _own_accounts() -> dict:
    ""
    from . import mailboxes
    try:
        return mailboxes.status()
    except Exception:
        log.warning("accounts: listing the home's mailboxes failed", exc_info=True)
        return {"mailboxes": [], "calendars": [], "google": {}}


async def _claude_login() -> dict:
    ""
    try:
        _, out = await _run("auth", "status", "--json")
        return parse_auth(out)
    except FileNotFoundError:
        return {"state": "missing", "account": ""}
    except Exception as e:
        log.warning("accounts: auth status failed: %s", type(e).__name__)
        return {"state": "unknown", "account": ""}


async def own_payload(checked_at: float) -> dict:
    ""


    claude, chatgpt = await asyncio.gather(_claude_login(), _chatgpt())
    result = shared_payload(checked_at)
    result.update(claude=claude, chatgpt={"state": chatgpt.get("state", "unknown")}, plan=plan_of(claude, chatgpt))
    if home.get("plan.connectors_on") is False:


        for s in result["services"]:
            s.update(state=CONNECTIONS_OFF, detail="Off. Turn on Use My Claude Connections to let your Zips use it.")
    return result


def connections_switch() -> bool | None:
    ""
    on = home.get("plan.connectors_on")
    return on if isinstance(on, bool) else None


def set_connections(on: bool) -> None:
    ""
    brain = home.get("plan.brain") or "claude"
    home.save({"plan": {"connectors_on": on, "connectors": (brain if brain in ("claude", "chatgpt") else "claude")
                        if on else "none"}})
    global _last
    _last = None
    _restart_mac_gateway()


def _restart_mac_gateway() -> None:
    ""

    import subprocess
    if home.get("host.kind") != "mac":
        return
    try:
        engine = json.loads((home.path().parent / "engine.json").read_text())
        label = engine.get("label")
        if not label:
            return
        subprocess.Popen(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}.gateway"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        log.warning("accounts: could not restart the Mac gateway after the connections switch", exc_info=True)


async def _check_now() -> dict:
    if home.connectors() == "none":
        result = await own_payload(time.time()) if home.plan_kind() == "own" else shared_payload(time.time())
    else:
        (claude, states, others), chatgpt = await asyncio.gather(_claude_side(), _chatgpt())
        _apps.clear()
        _apps.update(chatgpt.get("apps") or {})
        result = payload(claude, states, time.time(), chatgpt, others, claude_first())
        result["connectors"] = home.connectors()
    own = await asyncio.to_thread(_own_accounts)
    result.update(mailboxes=own.get("mailboxes", []), calendars=own.get("calendars", []),
                  google_sign_in=own.get("google", {}))
    native(result)
    return result




NATIVE = {"trello": "trello", "drive": "google_drive", "gmail": "gmail", "calendar": "google_calendar"}
NATIVE_TOOLS = {"trello": "trello", "drive": "drive", "gmail": "email", "calendar": "calendar"}


def native(result: dict) -> dict:
    ""



    try:
        from . import apps
        on = {sid for sid, app_id in NATIVE.items() if apps.connected_natively(app_id)}
    except Exception:
        log.warning("accounts: reading Connect Apps failed", exc_info=True)
        return result
    for row in result.get("services") or []:
        if row.get("id") in on:
            if row.get("state") == CONNECTED and row.get("via") in ("claude", "chatgpt"):
                row["plan_via"] = row["via"]
            row.update(state=CONNECTED, connected=True, via="zero", detail="Connected in Zero.")
    return result


async def check(force: bool = False) -> dict:
    ""
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
    ""
    return [x["name"] for x in (_last or {}).get("services", []) if x["connected"]]


def state_of(result: dict, service: str) -> str:
    return next((x["state"] for x in result.get("services", []) if x["id"] == service), UNKNOWN)




ALERT_EVERY_SEC = 30 * 60


def _remember(result: dict) -> None:
    ""
    ever = set(store.get_setting("accounts_ever", []) or [])
    now = {x["id"] for x in result["services"] if x["connected"]}
    if not now <= ever:
        store.set_setting("accounts_ever", sorted(ever | now))


def broken_news(result: dict) -> list[tuple[str, str]]:
    ""

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


def apps_signed_out() -> list[str]:
    ""
    from . import apps, connections, keys, oauth
    out = []
    for app_id in sorted({*oauth.APPS, *keys.BY_ID}):
        try:
            doc = connections.document(apps._document(app_id))
        except apps.AppError:
            continue
        if any(c.get("token") and c.get("state") == apps.NEEDS_SIGN_IN for c in doc["connections"].values()):
            out.append(app_id)
    return out


async def tell_signed_out() -> None:
    ""


    from . import apps, watcher
    told = set(store.get_setting("apps_told", []) or [])
    now = set(apps_signed_out())
    lead = "default" if "default" in config.BOTS_BY_ID else None
    for app_id in sorted(now - told):
        got = lead and await asyncio.to_thread(ask_app, lead, app_id, "", None, heads_up=True)
        if got and got[0]:
            name = apps.BY_ID[app_id].name
            await watcher.push(f"Sign in to {name} again", f"Your {name} sign-in ran out. Tap to sign in again so "
                                                           "your Zips can keep using it.", lead)
    if now != told:
        store.set_setting("apps_told", sorted(now))


async def run() -> None:
    ""

    await asyncio.sleep(20)
    while True:
        try:
            result = await check(force=True)
            from . import watcher
            from . import notices
            for kind, text in broken_news(result):
                await notices.nudge(f"account:{kind}:{text[:80]}", _lead(), text, held=text)
            from . import zipapps
            await zipapps.announce()
        except Exception:
            log.warning("accounts check failed", exc_info=True)
        try:
            from . import oauth
            await asyncio.to_thread(oauth.keep_fresh)
            await tell_signed_out()
        except Exception:
            log.warning("keeping app sign-ins fresh failed", exc_info=True)
        await asyncio.sleep(ALERT_EVERY_SEC)




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
    ""
    if service in BY_ID:
        return BY_ID[service]
    item = next((o for o in _others["items"] if o["id"] == service), None)
    return other_service(item) if item else None


def ask(bot_id: str, service: str, why: str, session_id: str | None, state: str) -> tuple[int | None, str]:
    ""

    s = find(service)
    if not s:
        raise AskError("Unknown service.")
    for row, card in _open_cards(bot_id):
        if card.get("via") == "zero":
            continue
        if (BY_ID[card["service"]].group if card.get("service") in BY_ID else "") == s.group == "google" \
                or card.get("service") == s.id:


            store.touch_feed(row["id"])
            return None, (f"{who.possessive()} card to connect {card.get('name') or s.name} is now at the bottom of "
                          f"your chat. Stop working on this now. {who.quiet_card()} You'll get a note here when it's "
                          "connected.")
    why = " ".join(str(why or "").split())[:160]
    title = "Connect Google" if s.group == "google" else f"Connect {s.name}"
    from . import apps
    app_id = SERVICE_APPS.get(s.id, s.id if s.id in apps.BY_ID else "")
    fid = store.add_feed(KIND, bot_id, None, title, json.dumps(
        {"service": s.id, "app": app_id, "via": "claude", "name": s.name, "group": s.group, "why": why,
         "state": "open", "reason": state, "session_id": session_id}), written_by="zip", written_for="person")
    return fid, (f"{s.name} isn't connected for your Zips ({detail(state, s)}) {who.first()} now has a card in your "
                 f"chat to connect it, and a notification. Stop working on this now. {who.quiet_card()} A note "
                 "will arrive in this chat when it's connected, and you'll carry on then.")



SERVICE_APPS = {"gmail": "gmail", "calendar": "google_calendar", "drive": "google_drive", "trello": "trello"}


def ask_app(bot_id: str, app_id: str, why: str, session_id: str | None,
            origin_task: str | None = None, heads_up: bool = False) -> tuple[int | None, str] | None:
    ""









    from . import apps
    from . import connections
    if app_id in apps.BY_ID and apps.connected_natively(app_id, bot_id=bot_id):
        return None, f"{apps.BY_ID[app_id].name} is already connected. Use the {apps.TOOLS.get(app_id, app_id)} tool."
    with connections.for_bot(app_id, bot_id):
        connectable = apps.connectable(app_id)
        again = connectable and apps.describe(app_id)["state"] == apps.NEEDS_SIGN_IN
        selection = connections.capture()
    service = {v: k for k, v in SERVICE_APPS.items()}.get(app_id, "")
    if not connectable or (service and through_plan(service)):
        return None
    a = apps.BY_ID[app_id]
    from . import task_context, zipapps
    joined = zipapps.join(bot_id, app_id, session_id)
    if joined:
        return None, joined
    context = task_context.get(origin_task)
    why = " ".join(str(why or "").split())[:160] or (f"Your {a.name} sign-in ran out." if again else "")
    if context:
        why = "\n".join(part for part in (context.purpose, why) if part)


    say_why = None, (f"{a.name} isn't connected. Before {who.first()} gets a card for it, say why you need it: call "
                     f"ask_to_connect with app \"{app_id}\" and why, a few words written to {who.first()} as \"you\" "
                     "(\"see your payouts for the weekly report\"). Then stop until it is connected.")
    for row, card in _open_cards(bot_id):
        if card.get("app") == app_id and card.get("via") == "zero":
            if not (why or card.get("why")):
                return say_why

            changed = not heads_up and card.get("heads_up")
            if changed:
                card.pop("heads_up")
                card["session_id"] = session_id
            if why and (context or not card.get("why")):
                card["why"] = why
                changed = True
            if changed:
                store.update_feed(row["id"], row["title"], json.dumps(card))
            store.touch_feed(row["id"])
            return None, (f"{who.possessive()} card to connect {card.get('name') or a.name} is now at the bottom of your chat. Stop "
                          f"working on this now. {who.quiet_card()} You'll get a note here when it's connected.")



    for row, card in _open_cards():
        if card.get("app") == app_id and card.get("via") == "zero" and row["bot_id"] != bot_id \
                and card.get("connection_id") == (selection or {}).get("id"):
            others = [w for w in card.get("also", []) if w.get("bot_id") != bot_id]
            card["also"] = [*others, {"bot_id": bot_id, "session_id": session_id}]
            store.update_feed(row["id"], row["title"], json.dumps(card))
            store.touch_feed(row["id"])
            asker = config.BOTS_BY_ID.get(row["bot_id"])
            return None, (f"{a.name} isn't connected yet. {who.first()} already has a card to connect it"
                          + (f" in {asker.name}'s chat" if asker else "") + f". Stop working on this now. "
                          f"{who.quiet_card()} You'll get a note here when it's connected.")
    if not why:
        return say_why
    title = f"Sign In to {a.name} Again" if again else f"Connect {a.name}"
    fid = store.add_feed(KIND, bot_id, origin_task, title, json.dumps(

        {"service": {v: k for k, v in SERVICE_APPS.items()}.get(app_id, app_id), "app": app_id, "via": "zero",
         "name": a.name, "group": "", "why": why, "state": "open",
         "provider": apps.SIGN_IN_WITH.get(app_id, a.name), "can": apps.can_me(app_id), "session_id": session_id,
         "key": apps.uses_key(app_id), "connection_id": (selection or {}).get("id"),
         **({"again": True} if again else {}), **({"heads_up": True} if heads_up else {})}),
             written_by="zip", written_for="person")
    state = "needs a new sign-in" if again else "isn't connected"
    return fid, (f"{a.name} {state}. {who.first()} now has a card in your chat to connect it with one tap, "
                 f"and a notification. Stop working on this now. {who.quiet_card()} A note will arrive "
                 "in this chat when it's connected, and you'll carry on then.")






MAILBOX_NAMES = {"icloud": "iCloud Mail", "other": "Email"}


def mailbox_connected(card: dict) -> bool:
    ""
    from . import mailboxes
    want = str(card.get("address") or "").lower()
    for a in home.mail_accounts():
        if a.get("zips_own") or home.is_google(a):
            continue
        if (card.get("provider") == "icloud" and a.get("provider") == "icloud"
                and (not want or mailboxes.receives(a, want))) or \
                (want and str(a.get("address") or "").lower() == want):
            return True
    return False


def ask_mailbox(bot_id: str, address: str, provider: str, why: str, session_id: str | None) -> tuple[int | None, str]:
    ""

    provider = provider if provider in MAILBOX_NAMES else "other"
    name = MAILBOX_NAMES[provider]
    for row, card in _open_cards(bot_id):
        if card.get("via") == "mailbox":
            store.touch_feed(row["id"])
            return None, (f"{who.possessive()} card to connect {card.get('name') or name} is now at the bottom of your "
                          f"chat. Stop working on this now. {who.quiet_card()} You'll get a note here when it's "
                          "connected.")
    why = " ".join(str(why or "").split())[:160]
    fid = store.add_feed(KIND, bot_id, None, f"Connect {name}", json.dumps(
        {"service": f"{provider}_mail", "app": f"{provider}_mail", "via": "mailbox", "name": name,
         "provider": provider, "address": address, "group": "", "why": why, "state": "open",
         "session_id": session_id}), written_by="zip", written_for="person")
    return fid, (f"{who.first()} now has a card in your chat to connect {name}, and a notification. Stop working on this now. {who.quiet_card()} A note will arrive in "
                 "this chat when it's connected, and you'll carry on then.")


def through_plan(service: str) -> bool:
    ""
    if home.connectors() == "none":
        return False
    result = cached()
    row = next((x for x in (result or {}).get("services", []) if x.get("id") == service), {})
    if row.get("state", UNKNOWN) in (UNKNOWN, TROUBLE):
        return service in ("gmail", "calendar")
    return row.get("state") == CONNECTED and row.get("via") in ("claude", "chatgpt")


async def need_app(bot_id: str, app_id: str, why: str, session_id: str | None,
                   origin_task: str | None = None) -> str | None:
    ""

    from . import team, watcher
    if bot_id not in config.BOTS_BY_ID or team.in_team_session(session_id):
        return None
    got = await asyncio.to_thread(ask_app, bot_id, app_id, why, session_id, origin_task=origin_task)
    if not got:
        return None
    fid, message = got
    if fid:
        from . import apps
        name = apps.BY_ID[app_id].name
        again = (store.db().execute("SELECT title FROM feed WHERE id=?", (fid,)).fetchone() or [""])[0].startswith("Sign In")
        ask = f"Can you sign in to {name} again" if again else f"Can you connect {name}"
        await watcher.push(f"{config.BOTS_BY_ID[bot_id].name} needs {name}",
                           ask + (f" so I can {why.rstrip('.')}?" if why else "?"), bot_id)
    return message


def close(bot_id: str, feed_id: int) -> dict:
    ""

    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?", (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise AskError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("state") == "open":
        card.update(state="declined", at=time.time())
        store.update_feed(feed_id, row["title"], json.dumps(card))
        from . import service_keys
        if service_keys.is_id(card.get("app")):
            service_keys.forget_ask(card["app"])
        name = card.get("name") or "the account"
        note = (f"{who.hub_note()} {who.first()} closed the card without connecting {name}. Finish "
                f"what you can without it, and tell {who.first()} in a sentence what you could not do.")
        try:
            for b, sid in [(bot_id, card.get("session_id")),
                           *((w["bot_id"], w.get("session_id")) for w in card.get("also", []) if w.get("bot_id"))]:
                asyncio.get_running_loop().create_task(_continue(b, note, sid, urgent=False))
        except RuntimeError:
            pass
    return card


def _connected_note(name: str, tool: str) -> str:
    ""

    return (f"{who.hub_note()} {name} is connected now. Start your reply with \"{name} is connected.\" and one short "
            f"line on what you're doing now, then carry on with what {who.first()} asked; use {tool}.")


def resolve_connected(result: dict | None) -> list[tuple[str, str, str | None]]:
    ""


    from . import apps
    services = (result or {}).get("services", [])
    now = {x["id"] for x in services if x["connected"]}
    wake = []
    for row, card in list(_open_cards()):
        if card.get("setup"):
            from . import zipapps
            wake += zipapps.settle(row, card)
            continue
        if card.get("via") == "mailbox":
            done, tool = mailbox_connected(card), "the email tool"
            if done and card.get("provider") == "icloud" and card.get("address"):

                from . import mailboxes
                box = mailboxes.icloud_mailbox()
                if box and mailboxes.receives(box, card["address"]):
                    mailboxes.add_alias(box["id"], card["address"], row["bot_id"])
        elif card.get("via") == "zero":
            app_id = card.get("app") or ""
            from . import service_keys
            if service_keys.is_id(app_id):
                done, tool = service_keys.connected(app_id), "the service_api tool"
            else:
                try:
                    with apps.connections.for_bot(app_id, row["bot_id"]):
                        done = app_id in apps.BY_ID and apps.describe(app_id)["state"] == apps.CONNECTED
                except Exception:
                    log.warning("accounts: reading %s in Connect Apps failed", app_id, exc_info=True)
                    continue
                tool = f"the {apps.TOOLS.get(app_id, app_id)} tool"
        else:
            done = card.get("service") in now
            via = next((x.get("via") for x in services if x["id"] == card.get("service")), "")
            tool = f"the {NATIVE_TOOLS[card['service']]} tool" if via == "zero" and card.get("service") in NATIVE_TOOLS \
                else "the connectors tool again"
        if not done:
            continue
        card.update(state="connected", at=time.time())
        store.update_feed(row["id"], row["title"], json.dumps(card))
        from . import asks
        asks.answered("account", row["id"], row["bot_id"], row["title"], "Connected. Check your own account access.")
        note = _connected_note(card.get("name") or card.get("service") or "It", tool)
        if not card.get("heads_up"):
            wake.append((row["bot_id"], note, card.get("session_id")))
        wake.extend((w["bot_id"], note, w.get("session_id")) for w in card.get("also", []) if w.get("bot_id"))
    return wake


async def _after_check(result: dict | None) -> None:
    for bot_id, note, session_id in resolve_connected(result):
        asyncio.get_running_loop().create_task(_continue(bot_id, note, session_id))


async def app_connected() -> None:
    ""



    result = _last if _last is not None else payload({"state": UNKNOWN}, {}, 0)
    await _after_check(native(result))


async def _continue(bot_id: str, note: str, session_id: str | None = None, urgent: bool = True) -> None:
    ""


    from . import turns
    try:
        await turns.follow_up(bot_id, note, session_id, urgent=urgent)
    except Exception:
        log.exception("could not pick %s back up after an account card", bot_id)


async def need(bot_id: str, service: str, why: str, session_id: str | None, failed: bool = False) -> dict:
    ""

    if service in NATIVE:
        try:
            from . import apps
            if apps.connected_natively(NATIVE[service], bot_id=bot_id):
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

    got = ask_app(bot_id, NATIVE[service], why, session_id) if service in NATIVE else None
    fid, message = got or ask(bot_id, service, why, session_id, st)
    return {"connected": False, "state": st, "card": fid, "message": message}


def route(result: dict, service: str) -> dict:
    ""


    row = next((x for x in result.get("services", []) if x["id"] == service), {})
    if row.get("via") == "chatgpt":
        return {"via": "chatgpt", "app": _apps.get(service, "")}
    s = find(service)
    out = {"via": "claude", "connector": s.connector if s else ""}
    if _apps.get(service):
        out["chatgpt_app"] = _apps[service]
    return out


def no_such(service: str, result: dict) -> str:
    names = [f'{x["id"]} ({x["name"]})' for x in result.get("services", []) if x["connected"]]
    return (f"There is no account called \"{service}\" in {who.possessive()} Claude account. "
            + (f"Accounts your Zips can use now: {', '.join(names)}." if names else "No accounts are connected now.")
            + f" Use one of those, or tell {who.first()} what you would need.")


def no_connectors_message() -> str:
    ""
    return ("Connected accounts are not available in this home. Use the email tool for mail and the calendar tool "
            f"for the calendar, a browser for other sites, or tell {who.first()} what you would need.")
