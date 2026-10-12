"""Sign In with Google for a home's Gmail and Google Calendar (Oct 9: Zero connects Google the way a professional
app does; app passwords stay as the fallback for iCloud and other mail).

Zero has its own Google OAuth client, an iOS client with PKCE and no client secret. Its id is in home.json
`google.client_id`. The app runs the sign-in (ASWebAuthenticationSession, PKCE) and sends the authorization code and
the code verifier to the hub over the paired channel; the hub exchanges them here, keeps the refresh token in this
home only, and hands short-lived access tokens to the plugin's email and calendar tools on request
(POST /internal/google/token).

The store: one file per Google account under `<hub-state>/google/`, folder mode 700, files mode 600, written
atomically. A token is never logged, never returned to the app and never written anywhere else. A refresh that
Google refuses (`invalid_grant`: the person revoked access, changed the password, or a Testing consent screen's
seven days ran out) marks the account `needs_sign_in`, and the app shows "Google asked you to sign in again."

Tests point TOKEN_URL, REVOKE_URL and API_BASE at a local fake.
"""

import base64
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import httpx

from . import config, home

log = logging.getLogger("taffy.google")

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
DRIVE_API = "https://www.googleapis.com/drive/v3"
TIMEOUT_SEC = 20
REFRESH_EARLY_SEC = 120   # refresh this long before Google's expiry, so a token handed out still has time left

SCOPE_MAIL_READ = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_MAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.events"
SCOPES = ["openid", "email", SCOPE_MAIL_READ, SCOPE_MAIL_SEND, SCOPE_CALENDAR]
# Google Drive is asked for on its own, when the person connects Drive (apps.py, Connect Apps): reading and searching
# every file needs drive.readonly, a restricted scope like gmail.modify (docs/specs/2026-10-11-native-connectors-design.md).
SCOPE_DRIVE_READ = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_SCOPES = ["openid", "email", SCOPE_DRIVE_READ]

CONNECTED = "connected"
NEEDS_SIGN_IN = "needs_sign_in"
MISSING = "missing"

SIGN_IN_AGAIN = "Google asked you to sign in again."
NOT_SET_UP = "Sign In with Google isn't set up for this home yet."

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class GoogleError(RuntimeError):
    """`str(e)` is one plain sentence for the person; `state` is connected, needs_sign_in or missing. `reason` is
    Google's own reason for a refusal when it gave one (accessNotConfigured, insufficientPermissions...)."""

    def __init__(self, message: str, state: str = NEEDS_SIGN_IN, reason: str = ""):
        super().__init__(message)
        self.state = state
        self.reason = reason


def client_id() -> str | None:
    cid = str(home.get("google.client_id") or "").strip()
    return cid or None


def redirect_uri(cid: str | None = None) -> str | None:
    """The iOS client's redirect: its reversed client id as a URL scheme."""
    cid = cid or client_id()
    if not cid:
        return None
    return "com.googleusercontent.apps." + cid.removesuffix(".apps.googleusercontent.com") + ":/oauth2redirect"


def setup() -> dict:
    """What the app needs to start the sign-in."""
    cid = client_id()
    return {"client_id": cid, "scopes": SCOPES, "redirect_uri": redirect_uri(cid), "available": bool(cid)}


# MARK: The store

def _dir() -> Path:
    return config.HUB_DIR / "google"


def _key(address: str) -> str:
    a = (address or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24}", a):
        raise GoogleError("That is not an email address.", MISSING)
    return a


def _file(address: str) -> Path:
    return _dir() / f"{_key(address)}.json"


def _lock(address: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(_key(address), threading.Lock())


def _load(address: str) -> dict | None:
    try:
        data = json.loads(_file(address).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("google: token file for one account is unreadable")
        return None
    return data if isinstance(data, dict) else None


def _save(address: str, data: dict) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    f = _file(address)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o600)
    tmp.replace(f)


def accounts() -> list[str]:
    """Every Google account this home holds a token for."""
    d = _dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def state(address: str) -> str:
    data = _load(address)
    if not data or not data.get("refresh_token"):
        return MISSING
    return NEEDS_SIGN_IN if data.get("state") == NEEDS_SIGN_IN else CONNECTED


def scopes(address: str) -> set[str]:
    return set(str((_load(address) or {}).get("scope") or "").split())


def can_mail(granted: set[str]) -> bool:
    return SCOPE_MAIL_READ in granted and SCOPE_MAIL_SEND in granted


def can_calendar(granted: set[str]) -> bool:
    return SCOPE_CALENDAR in granted


def can_drive(granted: set[str]) -> bool:
    return SCOPE_DRIVE_READ in granted


def describe(address: str) -> dict:
    """One account as the app shows it (never a token)."""
    st, granted = state(address), scopes(address)
    detail = {CONNECTED: "Connected. Your Zips can use it.", NEEDS_SIGN_IN: SIGN_IN_AGAIN,
              MISSING: "Not connected."}[st]
    return {"id": address, "address": address, "state": st, "detail": detail, "mail": can_mail(granted),
            "calendar": can_calendar(granted), "drive": can_drive(granted)}


# MARK: Talking to Google

def _post(url: str, data: dict) -> httpx.Response:
    """The one place the hub posts to Google's OAuth endpoints. Tests point the URLs at a fake."""
    with httpx.Client(timeout=TIMEOUT_SEC) as c:
        return c.post(url, data=data, headers={"Accept": "application/json"})


def _error_of(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error") or "")
    except ValueError:
        return ""


def _id_token_email(id_token: str) -> str | None:
    """The email claim of the ID token Google's token endpoint just returned over TLS. A token received directly
    from the token endpoint does not need its signature checked (OpenID Connect Core 1.0, 3.1.3.7)."""
    try:
        part = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return None
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is False:
        return None
    return email


def exchange(code: str, code_verifier: str, redirect: str | None = None, app_client_id: str | None = None) -> dict:
    """Trades the app's authorization code for tokens and keeps them, replacing any earlier tokens for the same
    address (Sign In Again). `app_client_id` is the client the app signed in with (public; the exchange must use
    the same one); without it, the home's. Returns describe() for the account."""
    cid = str(app_client_id or "").strip() or client_id()
    if cid and not re.fullmatch(r"[0-9]{6,20}-[a-z0-9]{10,64}\.apps\.googleusercontent\.com", cid):
        raise GoogleError("The sign-in didn't finish. Try Sign In with Google again.", MISSING)
    if not cid:
        raise GoogleError(NOT_SET_UP, MISSING)
    code, code_verifier = str(code or "").strip(), str(code_verifier or "").strip()
    if not code or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier):
        raise GoogleError("The sign-in didn't finish. Try Sign In with Google again.", MISSING)
    try:
        r = _post(TOKEN_URL, {"grant_type": "authorization_code", "code": code, "code_verifier": code_verifier,
                              "client_id": cid, "redirect_uri": redirect or redirect_uri(cid)})
    except httpx.HTTPError:
        raise GoogleError("Google couldn't be reached. Try again in a minute.", MISSING) from None
    if r.status_code != 200:
        log.info("google: code exchange refused (%s %s)", r.status_code, _error_of(r) or "no error code")
        raise GoogleError("The sign-in didn't finish. Try Sign In with Google again.", MISSING)
    data = r.json()
    refresh, access = data.get("refresh_token"), data.get("access_token")
    address = _id_token_email(str(data.get("id_token") or ""))
    if not address:
        raise GoogleError("Google didn't say which account signed in. Try again and allow the email address.",
                          MISSING)
    if not access:
        raise GoogleError("The sign-in didn't finish. Try Sign In with Google again.", MISSING)
    if not refresh:
        # Google gives an iOS client a refresh token on every code exchange; without one Zero could read only for
        # an hour, so the account is not kept.
        log.info("google: the code exchange returned no refresh token")
        raise GoogleError("Google didn't give Zero lasting access. Try Sign In with Google again.", MISSING)
    with _lock(address):
        _save(address, {"address": address, "refresh_token": refresh, "access_token": access,
                        "expires_at": time.time() + float(data.get("expires_in") or 3600),
                        "scope": str(data.get("scope") or ""), "client_id": cid, "state": CONNECTED,
                        "connected_at": time.time()})
    log.info("google: an account was connected")
    return describe(address)


def access_token(address: str) -> dict:
    """A current access token for the account, refreshed when it is near its expiry. Raises GoogleError with the
    sentence the person reads when Google refuses."""
    with _lock(address):
        data = _load(address)
        if not data or not data.get("refresh_token"):
            raise GoogleError(f"{address} isn't connected to Google here.", MISSING)
        if data.get("state") == NEEDS_SIGN_IN:
            raise GoogleError(SIGN_IN_AGAIN)
        if data.get("access_token") and float(data.get("expires_at") or 0) - time.time() > REFRESH_EARLY_SEC:
            return {"access_token": data["access_token"], "expires_at": int(data["expires_at"]),
                    "token_type": "Bearer", "scopes": sorted(str(data.get("scope") or "").split())}
        try:
            r = _post(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                                  "client_id": data.get("client_id") or client_id() or ""})
        except httpx.HTTPError:
            raise GoogleError("Google couldn't be reached just now.", CONNECTED) from None
        if r.status_code != 200:
            err = _error_of(r)
            if err in ("invalid_grant", "unauthorized_client", "invalid_client") or r.status_code in (400, 401):
                data["state"] = NEEDS_SIGN_IN
                data.pop("access_token", None)
                _save(address, data)
                log.info("google: a refresh was refused (%s); the account needs a new sign-in", err or r.status_code)
                raise GoogleError(SIGN_IN_AGAIN)
            raise GoogleError("Google couldn't be reached just now.", CONNECTED)
        fresh = r.json()
        data["access_token"] = fresh.get("access_token")
        data["expires_at"] = time.time() + float(fresh.get("expires_in") or 3600)
        if fresh.get("refresh_token"):   # Google may rotate it
            data["refresh_token"] = fresh["refresh_token"]
        if fresh.get("scope"):
            data["scope"] = fresh["scope"]
        _save(address, data)
        return {"access_token": data["access_token"], "expires_at": int(data["expires_at"]), "token_type": "Bearer",
                "scopes": sorted(str(data.get("scope") or "").split())}


def mark_revoked(address: str) -> None:
    """An API call answered 401 with a token this module handed out: the person has to sign in again."""
    with _lock(address):
        data = _load(address)
        if data:
            data["state"] = NEEDS_SIGN_IN
            data.pop("access_token", None)
            _save(address, data)


def disconnect(address: str) -> bool:
    """Revokes the grant at Google (best effort) and forgets the tokens. True when there was something to forget."""
    with _lock(address):
        data = _load(address)
        if not data:
            return False
        token = data.get("refresh_token") or data.get("access_token")
        if token:
            try:
                _post(REVOKE_URL, {"token": token})
            except httpx.HTTPError:
                log.info("google: revoking at Google failed; the tokens are forgotten here anyway")
        try:
            _file(address).unlink()
        except FileNotFoundError:
            pass
    return True


def _reason(r: httpx.Response) -> str:
    """Google's reason for a refusal: accessNotConfigured when the API is off in Zero's Google project,
    insufficientPermissions when the sign-in did not allow it."""
    try:
        err = r.json().get("error") or {}
    except ValueError:
        return ""
    if not isinstance(err, dict):
        return ""
    for e in err.get("errors") or []:
        if isinstance(e, dict) and e.get("reason"):
            return str(e["reason"])
    for d in err.get("details") or []:
        if isinstance(d, dict) and d.get("reason"):
            return str(d["reason"])
    return str(err.get("status") or "")


def api_response(address: str, url: str, params: dict | None = None) -> httpx.Response:
    """A GET to a Google API with the account's token. One retry after a refresh when Google answers 401."""
    for attempt in (0, 1):
        tok = access_token(address)["access_token"]
        try:
            with httpx.Client(timeout=TIMEOUT_SEC) as c:
                r = c.get(url, params=params or {}, headers={"Authorization": f"Bearer {tok}"})
        except httpx.HTTPError:
            raise GoogleError("Google couldn't be reached just now.", CONNECTED) from None
        if r.status_code == 401 and attempt == 0:
            with _lock(address):
                data = _load(address)
                if data:   # disconnected meanwhile: nothing to refresh, and no file to bring back
                    data["expires_at"] = 0   # force a refresh on the next try
                    _save(address, data)
            continue
        if r.status_code == 401:
            mark_revoked(address)
            raise GoogleError(SIGN_IN_AGAIN)
        if r.status_code == 403:
            raise GoogleError("Google didn't allow that. Sign in again and allow mail and calendar.", CONNECTED,
                              _reason(r))
        if r.status_code == 404:
            raise GoogleError("Google couldn't find that.", CONNECTED, "notFound")
        if r.status_code >= 400:
            raise GoogleError("Google couldn't answer just now.", CONNECTED, _reason(r))
        return r
    raise GoogleError(SIGN_IN_AGAIN)


def api_get(address: str, url: str, params: dict | None = None) -> dict:
    """A GET to a Google API that answers JSON."""
    return api_response(address, url, params).json()


def check_mail(address: str) -> None:
    """Reads the mailbox's profile: proves the token reads mail."""
    api_get(address, f"{GMAIL_API}/users/me/profile")


def check_calendar(address: str) -> None:
    """Reads one event of the primary calendar: proves the token reads the calendar."""
    api_get(address, f"{CALENDAR_API}/calendars/primary/events", {"maxResults": 1})
