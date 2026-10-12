"""Connect Apps: the apps a home's Zips reach directly through each provider's official API
(docs/specs/2026-10-11-native-connectors-design.md).

The catalog lists every app the Connect Apps screen shows, grouped like Grok Bot's (Email and Calendar, Files,
Productivity, Communication, Code, Design, Password Managers). An app is one of:

  native     Zero talks to the provider itself (Trello's REST API; Gmail, Google Calendar and Google Drive through
             Zero's Sign In with Google; Outlook, Dropbox, Notion, Linear and GitHub through oauth.py's sign-ins, read
             only for now). Read tools act; every change waits for the person's yes on a card (the plugin's gate,
             apps_tool.classify).
  connector  not native yet: a Zip reaches it only through the person's own Claude or ChatGPT account, as before
             (accounts.py and the plugin's connectors tool). The row says so, and the design doc ranks it.

The store: one file per app under `<hub-state>/apps/`, folder mode 700, files mode 600, written atomically, exactly
like google.py's. A token is never logged, never returned to the app or a Zip, never written anywhere else. The
plugin never sees one either: its app tools (trello, drive, outlook, dropbox, notion, linear, github) ask the hub to
make the call (POST /internal/apps/call), so every request to a provider is made here.

Outlook, Dropbox, Linear, Notion and GitHub connect through oauth.py (PKCE, Zero's relay for Notion, GitHub's device
flow); its docstring has the steps. Drive reuses Sign In with Google. Trello's connection:

  1. The app calls POST /api/apps/trello/start. With a redirect set up (home.json apps.trello.redirect: this home's
     address is an allowed origin of Zero's Trello key), Trello's Allow page sends the person back to
     <public_url>/connect/trello/done/<state>, a page of this hub that hands the token to POST /connect/trello/finish
     (single use, fifteen minutes) and then opens zero://connected/trello, which closes the app's sign-in sheet.
  2. Without the redirect, Trello's page shows a code; the person copies it and the app sends it to
     POST /api/apps/trello/code. The app's walkthrough says each step.

Either way the hub checks the token with Trello (members/me) before keeping it.
"""

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, google, home, oauth

log = logging.getLogger("taffy.apps")

CONNECTED = "connected"
NEEDS_SIGN_IN = "needs_sign_in"
NOT_CONNECTED = "not_connected"
NOT_SET_UP = "not_set_up"        # native, but this home lacks the one-time setup (Zero's Trello key, a Google client)
ELSEWHERE = "elsewhere"          # not native yet; connected through the person's Claude or ChatGPT account
UNAVAILABLE = "unavailable"      # not native yet, and this home has no Claude or ChatGPT account to reach it

START_TTL_SEC = 15 * 60
CALLBACK_SCHEME = "zero"


@dataclass(frozen=True)
class App:
    id: str
    name: str
    category: str
    symbol: str          # an SF Symbol: the app draws no provider marks it has no right to
    native: bool
    can: str             # what the Zips can do with it, said once, for the row
    connector: str = ""  # the matching service in accounts.py (Claude or ChatGPT), when there is one


CATEGORIES = ("Email and Calendar", "Files", "Productivity", "Communication", "Code", "Design", "Password Managers")

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
        "Your Zips read your repositories, issues and pull requests. They don't change anything.", "github"),
    App("figma", "Figma", "Design", "paintpalette", False, "Read your design files and comments.", "figma"),
    App("onepassword", "1Password", "Password Managers", "key", False,
        "Use the logins you keep in a vault you share with Zero.", "1password"),
)
BY_ID = {a.id: a for a in CATALOG}


class AppError(RuntimeError):
    """`str(e)` is one plain sentence. `connected` False means the person has to connect the app first."""

    def __init__(self, message: str, connected: bool = True):
        super().__init__(message)
        self.connected = connected


# MARK: The store

_lock = threading.Lock()
_starts: dict[str, tuple[str, float]] = {}   # state -> (app id, expiry): Connect flows that are under way


def _dir() -> Path:
    return config.HUB_DIR / "apps"


def _file(app_id: str) -> Path:
    if app_id not in BY_ID:
        raise AppError("There is no such app.")
    return _dir() / f"{app_id}.json"


def _load(app_id: str) -> dict:
    try:
        data = json.loads(_file(app_id).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("apps: the file for %s is unreadable", app_id)
        return {}
    return data if isinstance(data, dict) else {}


def _save(app_id: str, data: dict) -> None:
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
    try:
        _file(app_id).unlink()
    except FileNotFoundError:
        pass


def secret(app_id: str) -> str | None:
    """The app's token, for the module that calls the provider. Nothing else reads it."""
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


# MARK: Google Drive (Sign In with Google with the Drive scope)

def drive_address() -> str | None:
    """The Google account Drive is read with: the first one whose sign-in allowed Drive, unless the person
    disconnected Drive in Connect Apps."""
    if _load("google_drive").get("off"):
        return None
    for a in google.accounts():
        if google.can_drive(google.scopes(a)) and google.state(a) != google.MISSING:
            return a
    return None


def connect_drive(body: dict) -> dict:
    """POST /api/accounts/google with `use` ["drive"]: the app's code from a sign-in that asked for Drive."""
    from . import mailboxes
    try:
        acct = google.exchange(str(body.get("code") or ""), str(body.get("code_verifier") or ""),
                               body.get("redirect_uri") or None, body.get("client_id") or None)
    except google.GoogleError as e:
        return {"ok": False, "checked": False, "detail": str(e)}
    with _lock:
        _forget("google_drive")   # clears an earlier Disconnect
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


# MARK: Trello

def trello_setup() -> dict:
    from . import trello
    public = str(home.get("network.public_url") or "").strip().rstrip("/")
    redirect = bool(home.get("apps.trello.redirect")) and public.startswith("https://")
    return {"available": trello.key() is not None, "redirect": redirect, "public_url": public if redirect else ""}


def _sweep(now: float) -> None:
    for s, (_, exp) in list(_starts.items()):
        if exp < now:
            _starts.pop(s, None)


def start(app_id: str) -> dict:
    """POST /api/apps/{id}/start: where the app sends the person to connect."""
    a = BY_ID.get(app_id)
    if not a:
        raise AppError("There is no such app.")
    if app_id in ("gmail", "google_calendar"):
        return {"mode": "google", "use": ["mail", "calendar"], "scopes": google.SCOPES}
    if app_id == "google_drive":
        return {"mode": "google", "use": ["drive"], "scopes": google.DRIVE_SCOPES}
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
    return {"mode": "paste", "url": trello.authorize_url(), "callback_scheme": CALLBACK_SCHEME}


def connect_trello(pasted: str) -> dict:
    """The token from Trello's page (pasted by the person, or handed over by the redirect page). Checked with Trello
    before it is kept. Returns {ok, detail, app} with one plain sentence."""
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
    """POST /connect/trello/finish from the redirect page. The state is single use."""
    entry = _starts.pop(str(state or ""), None)
    if not entry or entry[1] < time.time():
        return {"ok": False, "detail": "This link expired. Go back to Zero and tap Connect again."}
    return connect_trello(pasted)


def disconnect(app_id: str) -> dict:
    """DELETE /api/apps/{id}."""
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


# MARK: What the app shows

def _google_rows() -> dict[str, tuple[str, str]]:
    """gmail, google_calendar and google_drive -> (state, the account)."""
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
    """Services the person's Claude or ChatGPT account has connected: id -> "Claude" or "ChatGPT"."""
    out = {}
    for row in (accounts_result or {}).get("services") or []:
        if row.get("connected") and row.get("via") in ("claude", "chatgpt"):
            out[row["id"]] = "ChatGPT" if row["via"] == "chatgpt" else "Claude"
    return out


def describe(app_id: str, accounts_result: dict | None = None, google_rows: dict | None = None) -> dict:
    a = BY_ID[app_id]
    row = {"id": a.id, "name": a.name, "category": a.category, "symbol": a.symbol, "native": a.native, "can": a.can,
           "account": "", "via": ""}
    elsewhere = _elsewhere(accounts_result).get(a.connector) if a.connector else None
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


def payload(accounts_result: dict | None = None) -> dict:
    """GET /api/apps: every app with its state, and the connected count for the header."""
    g = _google_rows()
    rows = [describe(a.id, accounts_result, g) for a in CATALOG]
    connected = [r["id"] for r in rows if r["state"] in (CONNECTED, ELSEWHERE)]
    return {"apps": rows, "categories": list(CATEGORIES), "connected": len(connected), "connected_ids": connected,
            "trello": {k: v for k, v in trello_setup().items() if k != "public_url"}}


def connected_natively(app_id: str) -> bool:
    """For accounts.py and calcache.py: the app works through Zero's own connection right now."""
    if app_id == "trello":
        return stored_state("trello") == CONNECTED and trello_setup()["available"]
    if app_id == "google_drive":
        return drive_address() is not None
    if app_id in oauth.APPS:
        return stored_state(app_id) == CONNECTED
    return False


# MARK: The Zips' calls (POST /internal/apps/call from the plugin's app tools)

def _need(app_id: str) -> None:
    a = BY_ID[app_id]
    if app_id == "trello":
        st = stored_state("trello")
        if st == NEEDS_SIGN_IN:
            raise AppError("Trello asked to be connected again. Ask the person to open Connect Apps in Zero's "
                           "settings and connect Trello.", connected=False)
        if st != CONNECTED:
            raise AppError("Trello isn't connected. Ask the person to connect it in Connect Apps in Zero's settings.",
                           connected=False)
    elif app_id == "google_drive":
        d = drive_address()
        if not d:
            raise AppError("Google Drive isn't connected. Ask the person to connect it in Connect Apps in Zero's "
                           "settings.", connected=False)
        if google.state(d) == google.NEEDS_SIGN_IN:
            raise AppError("Google asked the person to sign in again. Ask them to connect Google Drive again in "
                           "Connect Apps.", connected=False)
    elif app_id in oauth.APPS:
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
# The apps oauth.py connects: reading only, for now. Each action is one function of the app's module.
READS = {"outlook": ("mail", "message", "events"), "dropbox": ("search", "list", "read"),
         "notion": ("search", "read"), "linear": ("my_issues", "search", "issue"),
         "github": ("repos", "search", "issue")}


def _read(app_id: str, action: str, args: dict) -> dict:
    from . import dropbox, github, linear, microsoft, notion
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
    return {"issue": github.issue(str(args.get("issue") or ""))}


def call(app_id: str, action: str, args: dict) -> dict:
    """One call for a Zip. Raises AppError with a plain sentence."""
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
                keys = ("name", "description", "due", "due_complete", "list", "archived")
                return {"changed": trello.update_card(str(args.get("card") or ""),
                                                      {k: args[k] for k in keys if k in args},
                                                      str(args.get("board") or ""))}
            return {"commented": trello.comment(str(args.get("card") or ""), str(args.get("text") or ""))}
        except trello.TrelloError as e:
            raise AppError(str(e), connected=e.state not in (NEEDS_SIGN_IN, "missing")) from None
    if app_id == "google_drive":
        from . import gdrive
        if action not in DRIVE_READS:
            raise AppError("action must be search, recent or read.")
        _need("google_drive")
        address = drive_address()
        try:
            if action == "search":
                return {"files": gdrive.search(address, str(args.get("query") or ""), args.get("limit") or 20)}
            if action == "recent":
                return {"files": gdrive.recent(address, args.get("limit") or 20)}
            return {"file": gdrive.read(address, str(args.get("file") or ""), args.get("max_chars") or 0)}
        except gdrive.DriveError as e:
            raise AppError(str(e)) from None
    if app_id in READS:
        if action not in READS[app_id]:
            raise AppError("action must be " + " or ".join(READS[app_id]) + ".")
        _need(app_id)
        try:
            return _read(app_id, action, args)
        except oauth.OAuthError as e:
            raise AppError(str(e), connected=e.state not in (NEEDS_SIGN_IN, oauth.MISSING)) from None
    raise AppError("There is no such app.")
