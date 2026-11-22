""






























import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, connections, google, home, keys, oauth

log = logging.getLogger("taffy.apps")

CONNECTED = "connected"
NEEDS_SIGN_IN = "needs_sign_in"
NOT_CONNECTED = "not_connected"
NOT_SET_UP = "not_set_up"
ELSEWHERE = "elsewhere"
UNAVAILABLE = "unavailable"

START_TTL_SEC = 15 * 60
CALLBACK_SCHEME = "zero"


@dataclass(frozen=True)
class App:
    id: str
    name: str
    category: str
    symbol: str
    native: bool
    can: str
    connector: str = ""
    blurb: str = ""
    popular: bool = False




BLURBS = {
    "gmail": "Read and sort your mail", "google_calendar": "See and add your events",
    "outlook": "Read your mail and calendar", "google_drive": "Search Docs, Sheets and Slides",
    "dropbox": "Search and read your files", "trello": "Read and update your boards",
    "notion": "Search and read your pages", "todoist": "Read and add your tasks",
    "atlassian": "Read Jira issues and Confluence pages", "linear": "Find and read your issues",
    "slack": "Read the channels you can see", "github": "Read repos and open pull requests",
    "figma": "Read design files and comments", "onepassword": "Use logins from a shared vault",
    "plaid": "See your balances, read only",
}
POPULAR = ("gmail", "google_calendar", "google_drive", "outlook", "github", "notion", "plaid")


CATEGORIES = ("Email and Calendar", "Files", "Productivity", "Communication", "Code", "Sites and Analytics", "Design",
              "Password Managers", "Money")

CATALOG = (
    App("gmail", "Gmail", "Email and Calendar", "envelope", True,
        "Your Zips read and sort your mail, write drafts, and ask you before anything is sent.", "gmail"),
    App("google_calendar", "Google Calendar", "Email and Calendar", "calendar", True,
        "Your Zips read your calendar and add your own events. They ask you before anyone is invited.", "calendar"),
    App("outlook", "Outlook", "Email and Calendar", "envelope.badge", True,
        "Your Zips read your Outlook mail and calendar. They don't send mail or change events."),
    App("google_drive", "Google Drive", "Files", "folder", True,
        "Your Zips search your Drive and read Docs, Sheets and Slides. They don't change or share files.", "drive"),
    App("dropbox", "Dropbox", "Files", "shippingbox", True,
        "Your Zips search your Dropbox and read text files. They don't change or share files."),
    App("trello", "Trello", "Productivity", "rectangle.split.3x1", True,
        "Your Zips read your boards and cards. They ask you before they add, move or change a card.", "trello"),
    App("notion", "Notion", "Productivity", "doc.text", True,
        "Your Zips search and read the Notion pages you share with Zero. They don't change pages.", "notion"),
    App("todoist", "Todoist", "Productivity", "checklist", False, "Read and add your tasks.", "todoist"),
    App("atlassian", "Jira and Confluence", "Productivity", "square.stack.3d.up", False,
        "Read issues and pages from your Atlassian sites.", "atlassian"),
    App("linear", "Linear", "Productivity", "line.3.horizontal.decrease", True,
        "Your Zips find and read your Linear issues. They don't change them.", "linear"),
    App("slack", "Slack", "Communication", "number", False, "Read channels and messages you can see.", "slack"),
    App("github", "GitHub", "Code", "chevron.left.forwardslash.chevron.right", True,
        "Your Zips read your repositories and issues. They open pull requests for you to merge.", "github"),
    App("figma", "Figma", "Design", "paintpalette", False, "Read your design files and comments.", "figma"),
    App("onepassword", "1Password", "Password Managers", "key", False,
        "Use the logins you keep in a vault you share with Zero.", "1password"),
    App("plaid", "Your Bank", "Money", "building.columns", True,
        "Your Zips read your balances and transactions. They can't move money."),
) + tuple(App(k.id, k.name, "Sites and Analytics", "key", True, k.can) for k in keys.APPS)
BLURBS.update({k.id: k.blurb for k in keys.APPS})
CATALOG = tuple(App(**{**a.__dict__, "blurb": BLURBS.get(a.id, ""), "popular": a.id in POPULAR}) for a in CATALOG)
BY_ID = {a.id: a for a in CATALOG}


class AppError(RuntimeError):
    ""


    def __init__(self, message: str, connected: bool = True, why: str = ""):
        super().__init__(message)
        self.connected = connected
        self.why = why




_lock = threading.Lock()
_starts: dict[str, tuple[str, float]] = {}


def _dir() -> Path:
    return config.HUB_DIR / "apps"


def _file(app_id: str) -> Path:
    if app_id not in BY_ID:
        raise AppError("There is no such app.")
    return _dir() / f"{app_id}.json"


def _document(app_id: str) -> dict:
    try:
        data = json.loads(_file(app_id).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("apps: the file for %s is unreadable", app_id)
        return {}
    return data if isinstance(data, dict) else {}


def _load(app_id: str) -> dict:
    return connections.record(app_id, _document(app_id))


def _save(app_id: str, data: dict) -> None:
    _write_document(app_id, connections.saved(app_id, _document(app_id), data))


def _write_document(app_id: str, data: dict) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    f = _file(app_id)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o600)
    tmp.replace(f)


def _forget(app_id: str) -> None:
    remaining = connections.removed(app_id, _document(app_id))
    if remaining is not None:
        _write_document(app_id, remaining)
        return
    try:
        _file(app_id).unlink()
    except FileNotFoundError:
        pass


def secret(app_id: str) -> str | None:
    ""
    data = _load(app_id)
    return data.get("token") if data.get("state") == CONNECTED else None


def mark(app_id: str, state: str) -> None:
    with _lock:
        data = _load(app_id)
        if data:
            data["state"] = state
            _save(app_id, data)


def stored_state(app_id: str) -> str:
    data = _load(app_id)
    if not data.get("token"):
        return NOT_CONNECTED
    return NEEDS_SIGN_IN if data.get("state") == NEEDS_SIGN_IN else CONNECTED




def drive_address() -> str | None:
    ""
    found = drive_addresses()
    return found[0] if found else None






def drive_addresses(bot_id: str = "") -> list[str]:
    ""

    if _load("google_drive").get("off"):
        return []
    found = [a for a in google.accounts() if google.can_drive(google.scopes(a)) and google.state(a) != google.MISSING]
    return sorted(found, key=lambda a: a not in _zips_own_addresses(bot_id))


def _zips_own_addresses(bot_id: str) -> set[str]:
    if not bot_id:
        return set()
    from . import noticers
    own = {str(m.get("address") or "").lower() for m in home.mail_accounts() if m.get("owner_zip") == bot_id}
    domains = {d for d, bot in noticers.owner_by_domain().items() if bot == bot_id}
    return own | {a for a in google.accounts() if a.rsplit("@", 1)[-1] in domains}


def _drive_read(addresses: list[str], ref: str, max_chars, bot_id: str) -> dict:
    ""
    from . import gdrive
    failed = []
    for a in addresses:
        try:
            return {**gdrive.read(a, ref, max_chars), "account": a}
        except gdrive.NotFound:
            continue
        except gdrive.DriveError as e:


            failed.append((a, str(e)))
    if failed and len(failed) == len(addresses):
        raise gdrive.DriveError(failed[0][1])
    them = "that account" if len(addresses) == 1 else "them"
    said = (f"Google Drive couldn't open that file with {' or '.join(addresses)}. It isn't shared with {them}, or it "
            "no longer exists: Google gives the same answer for both.")
    if failed:
        said += " " + " ".join(f"{a} could not be tried: {why}" for a, why in failed)
    google_mail = [str(m.get("address") or "").lower() for m in home.mail_accounts()
                   if m.get("kind") == "google_oauth" or m.get("provider") == "gmail"]
    own = _zips_own_addresses(bot_id)
    others = sorted(dict.fromkeys(a for a in google_mail if a and a not in addresses), key=lambda a: a not in own)[:3]
    if others:
        said += (f" {' and '.join(others)} {'is' if len(others) == 1 else 'are'} not connected to Drive. Connecting "
                 "Drive with one in Connect Apps lets Drive try it.")
    raise gdrive.DriveError(said)


def _drive_list(addresses: list[str], action: str, args: dict) -> dict:
    ""

    from . import gdrive
    limit, files, missed, first_error = args.get("limit") or 20, {}, [], None
    for a in addresses:
        try:
            got = gdrive.search(a, str(args.get("query") or ""), limit) if action == "search" else gdrive.recent(a, limit)
        except gdrive.DriveError as e:
            first_error = first_error or e
            missed.append({"account": a, "why": str(e)})
            continue
        for f in got:
            files.setdefault(f["id"], {**f, "account": a})
    if first_error is not None and len(missed) == len(addresses):
        raise first_error
    ordered = sorted(files.values(), key=lambda f: str(f.get("modified") or ""), reverse=True)
    return {"files": ordered[:max(1, min(int(limit), gdrive.LIMIT_MAX))], **({"not_searched": missed} if missed else {})}


def connect_drive(body: dict) -> dict:
    ""
    from . import mailboxes
    try:
        acct = google.exchange(str(body.get("code") or ""), str(body.get("code_verifier") or ""),
                               body.get("redirect_uri") or None, body.get("client_id") or None)
    except google.GoogleError as e:
        return {"ok": False, "checked": False, "detail": str(e)}
    with _lock:
        _forget("google_drive")
    if not acct.get("drive"):
        mailboxes._forget_google_if_unused(acct["address"])
        return {"ok": False, "checked": False,
                "detail": "Google didn't give Zero access to your Drive. Try again and allow Drive."}
    log.info("apps: Google Drive was connected")
    return {"ok": True, "checked": True, "id": acct["address"], "address": acct["address"],
            "google": google.describe(acct["address"]), "mailbox": None, "calendar": None,
            "detail": f"Connected Google Drive for {acct['address']}. Your Zips can search and read it now."}


def disconnect_drive() -> dict:
    from . import mailboxes
    address = drive_address()
    with _lock:
        _save("google_drive", {"off": True, "at": time.time()})
    if address:
        mailboxes._forget_google_if_unused(address)
    return {"ok": True, "detail": "Disconnected. Your Zips no longer read your Google Drive."}




def trello_setup() -> dict:
    from . import trello
    public = str(home.get("network.public_url") or "").strip().rstrip("/")
    redirect = bool(home.get("apps.trello.redirect")) and public.startswith("https://")
    return {"available": trello.key() is not None, "redirect": redirect, "public_url": public if redirect else ""}


def _sweep(now: float) -> None:
    for s, (_, exp) in list(_starts.items()):
        if exp < now:
            _starts.pop(s, None)


def start(app_id: str, relay_ok: bool = False, connection: dict | None = None) -> dict:
    with connections.using(connections.begin(app_id, connection or {})):
        out = _start(app_id, relay_ok)
        selection = connections.capture()
        if out.get("mode") == "key" and selection:
            out["state"] = connections.key_start(selection)
        return out


def _start(app_id: str, relay_ok: bool = False) -> dict:
    ""
    a = BY_ID.get(app_id)
    if not a:
        raise AppError("There is no such app.")
    if app_id == "plaid":
        from . import plaid
        try:
            return plaid.link_start()
        except plaid.PlaidError as e:
            raise AppError(str(e)) from None
    if app_id in ("gmail", "google_calendar"):
        return {"mode": "google", "use": ["mail", "calendar"], "scopes": google.SCOPES}
    if app_id == "google_drive":
        return {"mode": "google", "use": ["drive"], "scopes": google.DRIVE_SCOPES}
    if app_id in keys.BY_ID and app_id not in oauth.APPS:
        k = keys.BY_ID[app_id]
        return {"mode": "key", "url": k.key_url, "make": k.make}
    if app_id in oauth.APPS:
        try:
            return oauth.device_start() if app_id == "github" else oauth.start(app_id)
        except oauth.OAuthError as e:
            raise AppError(str(e), connected=e.state != oauth.MISSING) from None
    if app_id != "trello":
        raise AppError(f"{a.name} connects through your Claude or ChatGPT account for now.")
    from . import trello
    setup = trello_setup()
    if not setup["available"]:
        raise AppError("Trello isn't ready for your home yet.", connected=False)
    now = time.time()
    _sweep(now)
    if setup["redirect"]:
        state = secrets.token_urlsafe(24)
        _starts[state] = (app_id, now + START_TTL_SEC)
        url = trello.authorize_url(f"{setup['public_url']}/connect/trello/done/{state}")
        return {"mode": "redirect", "url": url, "callback_scheme": CALLBACK_SCHEME}


    relay = str(home.get("apps.relay.url") or "").strip().rstrip("/")
    if relay_ok and relay.startswith("https://"):
        return {"mode": "relay", "url": trello.authorize_url(f"{relay}/callback/trello"),
                "callback_scheme": CALLBACK_SCHEME}
    return {"mode": "paste", "url": trello.authorize_url(), "callback_scheme": CALLBACK_SCHEME}


def connect_trello(pasted: str) -> dict:
    ""

    from . import trello
    token = trello.find_token(pasted)
    if not token:
        return {"ok": False, "detail": "That doesn't look like the code Trello shows. Copy the whole code and paste "
                                       "it again."}
    try:
        who = trello.me(token)
    except trello.TrelloError as e:
        if e.state == NEEDS_SIGN_IN:
            return {"ok": False, "detail": "Trello didn't accept that code. Open Trello again and tap Allow."}
        return {"ok": False, "detail": str(e)}
    with _lock:
        old = _load("trello").get("token")
        _save("trello", {"token": token, "state": CONNECTED, "member_id": who.get("id"),
                         "username": who.get("username"), "name": who.get("name"), "connected_at": time.time()})
    if old and old != token:
        trello.revoke(old)
    log.info("apps: Trello was connected")
    return {"ok": True, "detail": f"Connected Trello{(' as ' + who['name']) if who.get('name') else ''}. Your Zips can "
                                  "read your boards now.", "app": describe("trello")}


def finish(state: str, pasted: str) -> dict:
    ""
    entry = _starts.pop(str(state or ""), None)
    if not entry or entry[1] < time.time():
        return {"ok": False, "detail": "This link expired. Go back to Zero and tap Connect again."}
    return connect_trello(pasted)


def connect_key(app_id: str, pasted: str) -> dict:
    ""

    a = BY_ID[app_id]
    try:
        got = keys.check(app_id, pasted)
    except keys.KeyAppError as e:
        return {"ok": False, "detail": str(e)}
    with _lock:
        _save(app_id, {"token": got["key"], "state": CONNECTED, "account": got["account"], "api": got["api"],
                       "connected_at": time.time(), **({"proof": got["proof"]} if got.get("proof") else {})})
    log.info("apps: %s was connected with a key", a.name)
    selected = describe(app_id)
    return {"ok": True, "detail": selected["detail"] if got.get("proof") else
            f"Connected {a.name}. Your Zips can read it now.", "app": selected}


def disconnect(app_id: str) -> dict:
    ""
    if app_id == "plaid":
        from . import plaid
        return plaid.disconnect()
    if app_id in keys.BY_ID and _load(app_id).get("auth") != "oauth":
        with _lock:
            _forget(app_id)
        return {"ok": True, "detail": f"Disconnected. Your Zips no longer use {BY_ID[app_id].name}. You can delete "
                                      f"the key on {BY_ID[app_id].name} too."}
    if app_id == "google_drive":
        return disconnect_drive()
    if app_id in ("gmail", "google_calendar"):
        raise AppError("Disconnect Gmail and Google Calendar in Email and Calendars.")
    if app_id in oauth.APPS:
        return oauth.disconnect(app_id)
    if app_id != "trello":
        raise AppError("This app is connected in your Claude or ChatGPT account. Disconnect it there.")
    from . import trello
    with _lock:
        token = _load("trello").get("token")
        _forget("trello")
    if token:
        trello.revoke(token)
    return {"ok": True, "detail": "Disconnected. Your Zips no longer use Trello."}




def _google_rows() -> dict[str, tuple[str, str]]:
    ""
    out: dict[str, tuple[str, str]] = {}
    for a in google.accounts():
        st, granted = google.state(a), google.scopes(a)
        if st == google.MISSING:
            continue
        for app_id, ok in (("gmail", google.can_mail(granted)), ("google_calendar", google.can_calendar(granted))):
            if ok and out.get(app_id, ("",))[0] != CONNECTED:
                out[app_id] = (CONNECTED if st == google.CONNECTED else NEEDS_SIGN_IN, a)
    d = drive_address()
    if d:
        out["google_drive"] = (CONNECTED if google.state(d) == google.CONNECTED else NEEDS_SIGN_IN, d)
    return out


def _elsewhere(accounts_result: dict | None) -> dict[str, str]:
    ""
    out = {}
    for row in (accounts_result or {}).get("services") or []:
        if row.get("connected") and row.get("via") in ("claude", "chatgpt"):
            out[row["id"]] = "ChatGPT" if row["via"] == "chatgpt" else "Claude"
    return out


def describe(app_id: str, accounts_result: dict | None = None, google_rows: dict | None = None,
             *, bot_id: str | None = None) -> dict:
    if bot_id is not None:
        with connections.for_bot(app_id, bot_id):
            return describe(app_id, accounts_result, google_rows)
    a = BY_ID[app_id]
    row = {"id": a.id, "name": a.name, "category": a.category, "symbol": a.symbol, "native": a.native, "can": a.can,
           "blurb": a.blurb, "popular": a.popular, "account": "", "via": "",
           "multiple": connections.supported(app_id),
           "connection_count": len(connections.payload(app_id)["connections"]) if connections.supported(app_id) else 0}
    elsewhere = _elsewhere(accounts_result).get(a.connector) if a.connector else None
    if a.id == "plaid":
        from . import plaid
        banks = plaid.banks()
        row["banks"] = banks
        if not banks:
            return {**row, "name": "Connect Your Bank", "state": NOT_CONNECTED, "action": "connect", "detail": ""}
        names = " and ".join(b["name"] for b in banks)
        bad = [b["name"] for b in banks if b["state"] == NEEDS_SIGN_IN]
        row = {**row, "name": "Your Banks", "account": names, "via": "zero"}
        if bad:
            return {**row, "state": NEEDS_SIGN_IN, "action": "connect",
                    "detail": f"{' and '.join(bad)} asked you to connect again."}
        return {**row, "state": CONNECTED, "action": "disconnect", "detail": f"Connected to {names}."}
    if a.id == "trello":
        data, st = _load("trello"), stored_state("trello")
        if st == CONNECTED:
            name = data.get("name") or data.get("username") or ""
            return {**row, "state": CONNECTED, "via": "zero", "account": name, "action": "disconnect",
                    "detail": f"Connected{(' as ' + name) if name else ''}."}
        if st == NEEDS_SIGN_IN:
            return {**row, "state": NEEDS_SIGN_IN, "via": "zero", "action": "connect",
                    "detail": "Trello asked you to connect it again."}
        if not trello_setup()["available"]:
            if elsewhere:
                return {**row, "state": ELSEWHERE, "via": elsewhere.lower(), "action": "none",
                        "detail": f"Connected through your {elsewhere} account."}
            return {**row, "state": NOT_SET_UP, "action": "none", "detail": "Trello isn't ready for your home yet."}
        return {**row, "state": NOT_CONNECTED, "action": "connect",
                "detail": f"Also connected through your {elsewhere} account." if elsewhere else ""}
    if a.id in keys.BY_ID:
        data, st = _load(a.id), stored_state(a.id)
        if st == CONNECTED:
            name = data.get("account") or ""
            return {**row, "state": CONNECTED, "via": "zero", "account": name, "action": "disconnect",
                    "detail": f"Connected{(' as ' + name) if name else ''}." +
                              (" " + data["proof"]["detail"] if data.get("proof") else "") +
                              (f" Your Zips can read {', '.join(data['projects'])}." if data.get("projects") else "")}
        if st == NEEDS_SIGN_IN:
            return {**row, "state": NEEDS_SIGN_IN, "via": "zero", "action": "connect",
                    "detail": oauth.again(a.id) if data.get("auth") == "oauth" else
                              f"{a.name} stopped accepting the key. Connect it again."}
        return {**row, "state": NOT_CONNECTED, "action": "connect", "detail": ""}
    if a.id in oauth.APPS:
        data, st = _load(a.id), stored_state(a.id)
        if st == CONNECTED:
            name = data.get("account") or ""
            return {**row, "state": CONNECTED, "via": "zero", "account": name, "action": "disconnect",
                    "detail": f"Connected{(' as ' + name) if name else ''}."}
        if st == NEEDS_SIGN_IN:
            return {**row, "state": NEEDS_SIGN_IN, "via": "zero", "action": "connect", "detail": oauth.again(a.id)}
        if not oauth.available(a.id):
            if elsewhere:
                return {**row, "state": ELSEWHERE, "via": elsewhere.lower(), "action": "none",
                        "detail": f"Connected through your {elsewhere} account."}
            return {**row, "state": NOT_SET_UP, "action": "none", "detail": f"{a.name} isn't ready for your home yet."}
        return {**row, "state": NOT_CONNECTED, "action": "connect",
                "detail": f"Also connected through your {elsewhere} account." if elsewhere else ""}
    if a.id in ("gmail", "google_calendar", "google_drive"):
        rows = google_rows if google_rows is not None else _google_rows()
        st, address = rows.get(a.id, (NOT_CONNECTED, ""))
        if st == CONNECTED:
            return {**row, "state": CONNECTED, "via": "zero", "account": address, "action": "disconnect",
                    "detail": f"Connected with {address}."}
        if st == NEEDS_SIGN_IN:
            return {**row, "state": NEEDS_SIGN_IN, "via": "zero", "account": address, "action": "google",
                    "detail": google.SIGN_IN_AGAIN}
        if not google.client_id():
            return {**row, "state": NOT_SET_UP, "action": "none",
                    "detail": "Sign In with Google isn't set up for your home yet."}
        return {**row, "state": NOT_CONNECTED, "action": "google",
                "detail": f"Also connected through your {elsewhere} account." if elsewhere else ""}
    if elsewhere:
        return {**row, "state": ELSEWHERE, "via": elsewhere.lower(), "action": "none",
                "detail": f"Connected through your {elsewhere} account."}
    if home.connectors() != "none":
        return {**row, "state": NOT_CONNECTED, "action": "claude",
                "detail": "Connect it in your Claude or ChatGPT account for now. Zero is building a direct "
                          "connection."}
    return {**row, "state": UNAVAILABLE, "action": "none", "detail": "Coming to Zero."}


def payload(accounts_result: dict | None = None, bank: bool = False) -> dict:
    ""

    g = _google_rows()
    rows = [describe(a.id, accounts_result, g) for a in CATALOG if bank or a.id != "plaid"]
    from . import service_keys
    keyed = service_keys.rows()
    rows += keyed
    connected = [r["id"] for r in rows if r["state"] in (CONNECTED, ELSEWHERE)]
    return {"apps": rows, "categories": [c for c in CATEGORIES if bank or c != "Money"]
            + ([service_keys.CATEGORY] if keyed else []), "connected": len(connected),
            "connected_ids": connected,
            "trello": {k: v for k, v in trello_setup().items() if k != "public_url"}}





SIGN_IN_WITH = {"gmail": "Google", "google_calendar": "Google", "google_drive": "Google", "outlook": "Microsoft",
                "dropbox": "Dropbox", "trello": "Trello", "notion": "Notion", "linear": "Linear", "github": "GitHub",
                "plaid": "Plaid", **{k.id: k.name for k in keys.APPS}}

TOOLS = {"gmail": "email", "google_calendar": "calendar", "google_drive": "drive", "trello": "trello",
         "outlook": "outlook", "dropbox": "dropbox", "notion": "notion", "linear": "linear", "github": "github",
         "plaid": "bank", **{k.id: k.id for k in keys.APPS}}


_ALIASES = {"calendar": "google_calendar", "google calendar": "google_calendar", "gcal": "google_calendar",
            "drive": "google_drive", "google drive": "google_drive", "mail": "gmail", "google mail": "gmail",
            "microsoft outlook": "outlook", "jira": "atlassian", "confluence": "atlassian", "bank": "plaid",
            "your banks": "plaid", "connect your bank": "plaid"}


def find_id(name: str) -> str | None:
    ""
    key = " ".join(str(name or "").lower().replace("_", " ").split())
    if not key:
        return None
    for a in CATALOG:
        if key in (a.id.replace("_", " "), a.name.lower()):
            return a.id
    return _ALIASES.get(key)


def can_me(app_id: str) -> str:
    ""

    a = BY_ID.get(app_id)
    if not a or not a.native:
        return ""
    return a.can.replace("Your Zips ", "I ").replace("They ", "I ").replace(" they ", " I ")


def uses_key(app_id: str) -> bool:
    return app_id in keys.BY_ID and app_id not in oauth.APPS


def connectable(app_id: str) -> bool:
    ""

    if app_id not in BY_ID:
        return False
    if app_id == "plaid":
        from . import plaid
        if not plaid.config():
            return False
    row = describe(app_id)
    return row["state"] in (NOT_CONNECTED, NEEDS_SIGN_IN) and row.get("action") in ("google", "connect")


def connected_natively(app_id: str, *, bot_id: str | None = None) -> bool:
    ""
    if bot_id is not None:
        with connections.for_bot(app_id, bot_id):
            return connected_natively(app_id)
    if app_id == "plaid":
        from . import plaid
        return any(b["state"] == CONNECTED for b in plaid.banks())
    if app_id == "trello":
        return stored_state("trello") == CONNECTED and trello_setup()["available"]
    if app_id == "google_drive":
        return drive_address() is not None
    if app_id == "gmail":
        return any(home.is_google(m) and google.state(m["address"]) == google.CONNECTED and
                   google.can_mail(google.scopes(m["address"])) for m in home.mail_accounts())
    if app_id == "google_calendar":
        return any(home.is_google_calendar(c) and google.state(home.calendar_address(c)) == google.CONNECTED and
                   google.can_calendar(google.scopes(home.calendar_address(c))) for c in home.calendars())
    if app_id in oauth.APPS or app_id in keys.BY_ID:
        return stored_state(app_id) == CONNECTED
    return False




CONNECTOR_SERVICES = {"trello": "trello", "google_drive": "drive"}


def _through_connectors(app_id: str) -> None:
    ""


    from . import accounts
    service = CONNECTOR_SERVICES[app_id]


    row = next((x for x in (accounts.cached() or {}).get("services", []) if x.get("id") == service), {})
    if row.get("state") == accounts.CONNECTED and (row.get("plan_via") or row.get("via")) in ("claude", "chatgpt"):
        raise AppError(f"{BY_ID[app_id].name} works through the connectors tool here, not this one. Call connectors "
                       f'with service "{service}" instead.')


def _need(app_id: str) -> None:
    a = BY_ID[app_id]
    if app_id == "trello":
        st = stored_state("trello")
        if st == NEEDS_SIGN_IN:
            _through_connectors(app_id)
            raise AppError("Trello asked to be connected again. Ask the person to open Connect Apps in Zero's "
                           "settings and connect Trello.", connected=False)
        if st != CONNECTED:
            _through_connectors(app_id)
            raise AppError("Trello isn't connected. Ask the person to connect it in Connect Apps in Zero's settings.",
                           connected=False)
    elif app_id == "google_drive":
        d = drive_address()
        if not d:
            _through_connectors(app_id)
            raise AppError("Google Drive isn't connected. Ask the person to connect it in Connect Apps in Zero's "
                           "settings.", connected=False)
        if google.state(d) == google.NEEDS_SIGN_IN:
            _through_connectors(app_id)
            raise AppError("Google asked the person to sign in again. Ask them to connect Google Drive again in "
                           "Connect Apps.", connected=False)
    elif app_id in oauth.APPS or app_id in keys.BY_ID:
        st = stored_state(app_id)
        if st == NEEDS_SIGN_IN:
            raise AppError(f"{a.name} asked to be connected again. Ask the person to open Connect Apps in Zero's "
                           f"settings and connect {a.name}.", connected=False)
        if st != CONNECTED:
            raise AppError(f"{a.name} isn't connected. Ask the person to connect it in Connect Apps in Zero's "
                           "settings.", connected=False)
    else:
        raise AppError(f"{a.name} isn't connected directly.", connected=False)


TRELLO_READS = {"boards", "board", "card", "search"}
TRELLO_WRITES = {"create_card", "update_card", "comment"}
DRIVE_READS = {"search", "recent", "read"}


READS = {"outlook": ("mail", "message", "events"), "dropbox": ("search", "list", "read"),
         "notion": ("search", "read"), "linear": ("my_issues", "search", "issue"),
         "github": ("repos", "search", "issue", "clone", "draft_pr")}


def _read(app_id: str, action: str, args: dict, bot_id: str = "") -> dict:
    from . import dropbox, github, linear, microsoft, notion, zipfiles
    limit = args.get("limit") or 0
    if app_id == "outlook":
        if action == "mail":
            return {"messages": microsoft.mail(str(args.get("query") or ""), limit or 20)}
        if action == "message":
            return {"message": microsoft.message(str(args.get("id") or ""), args.get("max_chars") or 0)}
        return {"events": microsoft.events(str(args.get("start") or ""), str(args.get("end") or ""), limit or 50)}
    if app_id == "dropbox":
        if action == "search":
            return {"files": dropbox.search(str(args.get("query") or ""), limit or 20)}
        if action == "list":
            return {"files": dropbox.folder(str(args.get("path") or ""), limit or 50)}
        return {"file": dropbox.read(str(args.get("path") or ""), args.get("max_chars") or 0)}
    if app_id == "notion":
        if action == "search":
            return {"pages": notion.search(str(args.get("query") or ""), limit or 20)}
        return {"page": notion.read(str(args.get("page") or ""), args.get("max_chars") or 0)}
    if app_id == "linear":
        if action == "my_issues":
            return {"issues": linear.my_issues(limit or 25)}
        if action == "search":
            return {"issues": linear.search(str(args.get("query") or ""), limit or 20)}
        return {"issue": linear.issue(str(args.get("issue") or ""))}
    if action == "repos":
        return {"repos": github.repos(limit or 30)}
    if action == "search":
        return {"results": github.search(str(args.get("query") or ""), limit or 20)}
    if action == "clone":
        if not re.fullmatch(r"[a-z][a-z0-9]{0,19}", bot_id):
            raise oauth.OAuthError("A copy goes to a Zip's own folder, and this call named no Zip.")
        return {"copy": github.clone(str(args.get("repo") or ""), zipfiles.made_root(bot_id))}
    if action == "draft_pr":
        if not re.fullmatch(r"[a-z][a-z0-9]{0,19}", bot_id):
            raise oauth.OAuthError("A pull request comes from a Zip's own folder, and this call named no Zip.")
        from . import teams
        roots = [zipfiles.made_root(bot_id), *teams.extra_folders(bot_id)]
        return {"pull_request": github.draft_pr(str(args.get("repo") or ""), str(args.get("folder") or ""),
                                                str(args.get("branch") or ""), str(args.get("title") or ""),
                                                str(args.get("body") or ""), roots)}
    return {"issue": github.issue(str(args.get("issue") or ""))}


def call(app_id: str, action: str, args: dict, bot_id: str = "") -> dict:
    ""
    with connections.for_bot(app_id, bot_id):
        return _call(app_id, action, args, bot_id)


def _call(app_id: str, action: str, args: dict, bot_id: str) -> dict:
    args = args if isinstance(args, dict) else {}
    if app_id == "trello":
        from . import trello
        if action not in TRELLO_READS | TRELLO_WRITES:
            raise AppError("action must be boards, board, card, search, create_card, update_card or comment.")
        _need("trello")
        try:
            if action == "boards":
                return {"boards": trello.boards()}
            if action == "board":
                return trello.board(str(args.get("board") or ""))
            if action == "card":
                return {"card": trello.card(str(args.get("card") or ""))}
            if action == "search":
                return trello.search(str(args.get("query") or ""))
            if action == "create_card":
                return {"created": trello.create_card(str(args.get("list") or ""), str(args.get("name") or ""),
                                                      str(args.get("board") or ""), str(args.get("description") or ""),
                                                      args.get("due") or None, str(args.get("position") or ""))}
            if action == "update_card":
                fields = ("name", "description", "due", "due_complete", "list", "archived")
                return {"changed": trello.update_card(str(args.get("card") or ""),
                                                      {k: args[k] for k in fields if k in args},
                                                      str(args.get("board") or ""))}
            return {"commented": trello.comment(str(args.get("card") or ""), str(args.get("text") or ""))}
        except trello.TrelloError as e:
            if e.state == NEEDS_SIGN_IN:
                _through_connectors("trello")
            raise AppError(str(e), connected=e.state not in (NEEDS_SIGN_IN, "missing")) from None
    if app_id == "google_drive":
        from . import gdrive
        if action not in DRIVE_READS:
            raise AppError("action must be search, recent or read.")
        _need("google_drive")
        addresses = drive_addresses(bot_id)
        try:
            if action == "read":
                return {"file": _drive_read(addresses, str(args.get("file") or ""), args.get("max_chars") or 0, bot_id)}
            return _drive_list(addresses, action, args)
        except gdrive.DriveError as e:

            if any(google.state(a) == google.NEEDS_SIGN_IN for a in addresses):
                _through_connectors("google_drive")
            raise AppError(str(e)) from None
    if app_id == "plaid":

        from . import plaid
        try:
            return plaid.call(action, args)
        except plaid.PlaidError as e:
            raise AppError(str(e), connected=e.connected) from None
    if app_id in keys.BY_ID:
        _need(app_id)
        try:
            if app_id == "cloudflare" and action in ("inspect_project", "deploy_project", "deploy_worker"):
                from . import cloudflare
                if action == "deploy_worker":
                    return cloudflare.deploy_worker(_load(app_id), args)
                operation = cloudflare.inspect_project if action == "inspect_project" else cloudflare.deploy_project
                return operation(_load(app_id), args, bot_id)
            if action and action != "request":
                return keys.friendly(app_id, _load(app_id), action, args)
            return keys.request(app_id, _load(app_id), str(args.get("method") or "GET"), str(args.get("path") or ""),
                                args.get("body"))
        except keys.KeyAppError as e:
            if e.rejected:
                mark(app_id, NEEDS_SIGN_IN)
            raise AppError(str(e), connected=not e.rejected, why=e.why) from None
    if app_id in READS:
        if action not in READS[app_id]:
            raise AppError("action must be " + " or ".join(READS[app_id]) + ".")
        _need(app_id)
        try:
            return _read(app_id, action, args, bot_id)
        except oauth.OAuthError as e:
            raise AppError(str(e), connected=e.state not in (NEEDS_SIGN_IN, oauth.MISSING)) from None
    raise AppError("There is no such app.")
