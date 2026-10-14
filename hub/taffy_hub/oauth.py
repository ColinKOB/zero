"""Sign-in for the Connect Apps that hand Zero a token through the provider's own page
(docs/specs/2026-10-11-native-connectors-design.md): Outlook (Microsoft), Dropbox and Linear with PKCE and no client
secret, Notion through Zero's OAuth relay (oauth-relay/relay.py, the only place Notion's client secret lives), and
GitHub with the device flow. Every app here is read only for now; the plugin's gate still decides every call
(apps_tool.classify).

  PKCE    POST /api/apps/{id}/start makes a state and a code verifier (kept here only, single use, fifteen minutes)
          and returns the provider's page with the verifier's challenge. The app opens it in a web sheet; the provider
          sends the person to zero://oauth/<provider>?code=...&state=..., which closes the sheet, and the app posts the
          code and state to POST /api/apps/{id}/code. The hub swaps them for tokens.
  Notion  the same without PKCE: Notion returns to <relay>/callback/notion, whose page forwards to
          zero://oauth/notion, and the hub swaps the code (and later the refresh token) by POST <relay>/token/notion.
  GitHub  POST /api/apps/github/start asks GitHub for a user code; the app shows it and opens github.com/login/device,
          then calls POST /api/apps/github/poll every few seconds until the person allowed Zero or the code ran out.

Tokens go into apps.py's store (`<hub-state>/apps/<id>.json`, mode 600) with the refresh token and expiry. A token is
never logged, never in a URL, never returned to the app or a Zip. Each app's calls go through request() here, which
refreshes two minutes before expiry (a rotated refresh token is saved first), retries once after a 401, and marks the
app needs_sign_in when the provider refuses the refresh or the token.

Tests point the URLs in PROVIDERS, GITHUB and the provider modules at a local fake.
"""

import base64
import hashlib
import logging
import re
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

from . import home

log = logging.getLogger("taffy.oauth")

TIMEOUT_SEC = 20
START_TTL_SEC = 15 * 60
REFRESH_EARLY_SEC = 120
SCHEME = "zero"

CONNECTED = "connected"
NEEDS_SIGN_IN = "needs_sign_in"
MISSING = "missing"

MS_AUTHORITY = "https://login.microsoftonline.com/common"

PROVIDERS = {
    "outlook": {"name": "Outlook", "home": "microsoft", "field": "client_id",
                "authorize": f"{MS_AUTHORITY}/oauth2/v2.0/authorize", "token": f"{MS_AUTHORITY}/oauth2/v2.0/token",
                "scope": "offline_access User.Read Mail.Read Calendars.Read Files.Read",
                "redirect": f"{SCHEME}://oauth/microsoft", "extra": {"response_mode": "query",
                                                                     "prompt": "select_account"}},
    "dropbox": {"name": "Dropbox", "home": "dropbox", "field": "app_key",
                "authorize": "https://www.dropbox.com/oauth2/authorize",
                "token": "https://api.dropboxapi.com/oauth2/token",
                "revoke": "https://api.dropboxapi.com/2/auth/token/revoke",
                "scope": "files.metadata.read files.content.read", "redirect": f"{SCHEME}://oauth/dropbox",
                "extra": {"token_access_type": "offline"}},
    "linear": {"name": "Linear", "home": "linear", "field": "client_id",
               "authorize": "https://linear.app/oauth/authorize", "token": "https://api.linear.app/oauth/token",
               "revoke": "https://api.linear.app/oauth/revoke", "scope": "read",
               "redirect": f"{SCHEME}://oauth/linear", "extra": {}},
    "notion": {"name": "Notion", "home": "notion", "field": "client_id",
               "authorize": "https://api.notion.com/v1/oauth/authorize", "token": "relay", "scope": "",
               "redirect": "relay", "extra": {"owner": "user"}},
}
# GitHub's device flow: no secret and no redirect. `repo` is the only scope that reads private repositories; the
# github tool only reads (apps.GITHUB_READS), and the gate decides each call.
GITHUB = {"name": "GitHub", "home": "github", "field": "client_id", "device": "https://github.com/login/device/code",
          "token": "https://github.com/login/oauth/access_token", "scope": "repo read:user",
          "verify": "https://github.com/login/device"}
APPS = (*PROVIDERS, "github")

_pending: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class OAuthError(RuntimeError):
    """`str(e)` is one plain sentence for the person or the Zip; `state` is needs_sign_in when the provider refused
    the token, missing when the app isn't connected, else connected (busy, unreachable, or a wrong request)."""

    def __init__(self, message: str, state: str = CONNECTED):
        super().__init__(message)
        self.state = state


def _spec(app_id: str) -> dict:
    return GITHUB if app_id == "github" else PROVIDERS[app_id]


def name(app_id: str) -> str:
    return _spec(app_id)["name"]


def again(app_id: str) -> str:
    return f"{name(app_id)} asked you to connect it again."


def client_id(app_id: str) -> str | None:
    s = _spec(app_id)
    cid = str(home.get(f"apps.{s['home']}.{s['field']}") or "").strip()
    return cid if re.fullmatch(r"[A-Za-z0-9._-]{8,80}", cid) else None


def relay() -> str | None:
    url = str(home.get("apps.relay.url") or "").strip().rstrip("/")
    return url if url.startswith("https://") else None


def redirect_uri(app_id: str) -> str:
    r = PROVIDERS[app_id]["redirect"]
    return f"{relay()}/callback/{app_id}" if r == "relay" else r


def available(app_id: str) -> bool:
    """This home has Zero's public ID for the app (and the relay, for Notion)."""
    if not client_id(app_id):
        return False
    return app_id == "github" or PROVIDERS[app_id]["redirect"] != "relay" or relay() is not None


def _lock(app_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(app_id, threading.Lock())


def _sweep(now: float) -> None:
    for s, e in list(_pending.items()):
        if e["exp"] < now:
            _pending.pop(s, None)


def _post(url: str, data: dict | None = None, json: dict | None = None) -> httpx.Response:
    """The one place the hub posts to a provider's sign-in addresses (and the relay). Tests point the URLs at a fake."""
    with httpx.Client(timeout=TIMEOUT_SEC) as c:
        return c.post(url, data=data, json=json, headers={"Accept": "application/json",
                                                          "User-Agent": "Zero"})


def _error_of(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        return ""
    return str(err if isinstance(err, str) else "")[:60]


# MARK: Starting and finishing a sign-in

def start(app_id: str) -> dict:
    """POST /api/apps/{id}/start for a PKCE or relay app: the provider's page, with a fresh state."""
    p = PROVIDERS[app_id]
    cid = client_id(app_id)
    if not available(app_id):
        raise OAuthError(f"{p['name']} isn't ready for your home yet.", MISSING)
    now = time.time()
    _sweep(now)
    state = secrets.token_urlsafe(24)
    q = {"client_id": cid, "response_type": "code", "redirect_uri": redirect_uri(app_id), "state": state,
         **p["extra"]}
    if p["scope"]:
        q["scope"] = p["scope"]
    entry = {"app": app_id, "exp": now + START_TTL_SEC}
    if p["redirect"] != "relay":
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        q.update(code_challenge=challenge, code_challenge_method="S256")
        entry["verifier"] = verifier
    _pending[state] = entry
    return {"mode": "oauth", "url": f"{p['authorize']}?{urlencode(q)}", "callback_scheme": SCHEME}


def _record(data: dict, old: dict) -> dict:
    """What the store keeps from a provider's token answer."""
    out = {**old, "token": data["access_token"], "state": CONNECTED,
           "refresh_token": data.get("refresh_token") or old.get("refresh_token") or "",
           "expires_at": time.time() + float(data["expires_in"]) if data.get("expires_in") else 0}
    if data.get("scope"):
        out["scope"] = data["scope"] if isinstance(data["scope"], str) else " ".join(map(str, data["scope"]))
    return out


def _who(app_id: str, token: str, data: dict) -> str:
    """The account the token belongs to, as the row shows it. Also proves the token reads."""
    from . import dropbox, github, linear, microsoft, notion
    if app_id == "notion":
        notion.check(token)
        return str(data.get("workspace_name") or "")[:120]
    return {"outlook": microsoft.me, "dropbox": dropbox.me, "linear": linear.me, "github": github.me}[app_id](token)


def _keep(app_id: str, data: dict) -> dict:
    """Checks a fresh token with the provider and keeps it. Returns {ok, detail, app}."""
    from . import apps
    try:
        account = _who(app_id, data["access_token"], data)
    except OAuthError as e:
        return {"ok": False, "detail": str(e) if e.state == CONNECTED else
                f"{name(app_id)} didn't let Zero read your account. Tap Connect and try again."}
    with _lock(app_id), apps._lock:
        rec = _record(data, {})
        rec.update(account=account, connected_at=time.time())
        apps._save(app_id, rec)
    log.info("oauth: %s was connected", app_id)
    return {"ok": True, "detail": f"Connected {name(app_id)}{(' as ' + account) if account else ''}. Your Zips can "
                                  "read it now.", "app": apps.describe(app_id)}


def finish(app_id: str, code: str, state: str) -> dict:
    """POST /api/apps/{id}/code with the code and state the provider sent back to Zero. The state is single use."""
    entry = _pending.pop(str(state or ""), None)
    if not entry or entry["app"] != app_id or entry["exp"] < time.time():
        return {"ok": False, "detail": "This sign-in expired. Tap Connect and try again."}
    code = str(code or "").strip()
    if not code or len(code) > 2000:
        return {"ok": False, "detail": f"{name(app_id)} didn't connect. Tap Connect and try again."}
    p = PROVIDERS[app_id]
    try:
        if p["token"] == "relay":
            r = _post(f"{relay()}/token/{app_id}", json={"code": code, "redirect_uri": redirect_uri(app_id)})
        else:
            form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(app_id),
                    "client_id": client_id(app_id), "code_verifier": entry.get("verifier", "")}
            if app_id == "outlook":
                form["scope"] = p["scope"]
            r = _post(p["token"], data=form)
    except httpx.HTTPError:
        return {"ok": False, "detail": f"{p['name']} couldn't be reached. Try again in a minute."}
    if r.status_code != 200 or not _json(r).get("access_token"):
        log.info("oauth: %s code exchange refused (%s %s)", app_id, r.status_code, _error_of(r) or "no error code")
        return {"ok": False, "detail": f"{p['name']} didn't connect. Tap Connect and try again."}
    return _keep(app_id, _json(r))


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# MARK: GitHub's device flow

def device_start() -> dict:
    """POST /api/apps/github/start: a user code for github.com/login/device."""
    if not available("github"):
        raise OAuthError("GitHub isn't ready for your home yet.", MISSING)
    try:
        r = _post(GITHUB["device"], data={"client_id": client_id("github"), "scope": GITHUB["scope"]})
    except httpx.HTTPError:
        raise OAuthError("GitHub couldn't be reached. Try again in a minute.") from None
    data = _json(r)
    if r.status_code != 200 or not data.get("device_code") or not data.get("user_code"):
        log.info("oauth: github device code refused (%s %s)", r.status_code, _error_of(r) or "no error code")
        raise OAuthError("GitHub didn't start the sign-in. Try again in a minute.")
    now = time.time()
    _sweep(now)
    state = secrets.token_urlsafe(24)
    interval = max(5, min(int(data.get("interval") or 5), 60))
    _pending[state] = {"app": "github", "exp": now + min(float(data.get("expires_in") or 900), START_TTL_SEC),
                       "device_code": data["device_code"], "interval": interval, "next": now}
    url = str(data.get("verification_uri") or "")
    return {"mode": "device", "state": state, "user_code": str(data["user_code"])[:20], "interval": interval,
            "url": url if url.startswith("https://github.com/") else GITHUB["verify"], "callback_scheme": SCHEME}


def device_poll(state: str) -> dict:
    """POST /api/apps/github/poll: {ok: true, ...} once the person allowed Zero, {ok: false, pending: true, interval}
    while GitHub waits, else {ok: false, detail}. Asks GitHub at most once per interval, whatever the app does."""
    entry = _pending.get(str(state or ""))
    now = time.time()
    if not entry or entry["app"] != "github" or entry["exp"] < now:
        _pending.pop(str(state or ""), None)
        return {"ok": False, "detail": "The code ran out. Tap Connect to get a new one."}
    if now < entry["next"]:
        return {"ok": False, "pending": True, "interval": entry["interval"]}
    entry["next"] = now + entry["interval"]
    try:
        r = _post(GITHUB["token"], data={"client_id": client_id("github"), "device_code": entry["device_code"],
                                          "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    except httpx.HTTPError:
        return {"ok": False, "pending": True, "interval": entry["interval"]}
    data = _json(r)
    err = str(data.get("error") or "")
    if err == "authorization_pending":
        return {"ok": False, "pending": True, "interval": entry["interval"]}
    if err == "slow_down":
        entry["interval"] = max(entry["interval"] + 5, min(int(data.get("interval") or 0), 60))
        entry["next"] = now + entry["interval"]
        return {"ok": False, "pending": True, "interval": entry["interval"]}
    _pending.pop(state, None)
    if err == "access_denied":
        return {"ok": False, "detail": "GitHub wasn't allowed. Tap Connect to try again."}
    if err == "expired_token":
        return {"ok": False, "detail": "The code ran out. Tap Connect to get a new one."}
    if r.status_code != 200 or not data.get("access_token"):
        log.info("oauth: github device sign-in refused (%s %s)", r.status_code, err or "no error code")
        return {"ok": False, "detail": "GitHub didn't connect. Tap Connect and try again."}
    return _keep("github", data)


# MARK: Tokens

def _refresh(app_id: str, data: dict) -> str:
    """A new access token from the refresh token (held: the app's lock). Saves a rotated refresh token first."""
    from . import apps
    p = PROVIDERS[app_id]
    try:
        if p["token"] == "relay":
            r = _post(f"{relay()}/token/{app_id}", json={"refresh_token": data["refresh_token"]})
        else:
            form = {"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                    "client_id": client_id(app_id) or ""}
            if app_id == "outlook":
                form["scope"] = p["scope"]
            r = _post(p["token"], data=form)
    except httpx.HTTPError:
        raise OAuthError(f"{p['name']} couldn't be reached just now.") from None
    fresh = _json(r)
    if r.status_code != 200 or not fresh.get("access_token"):
        if r.status_code in (400, 401):
            data["state"] = NEEDS_SIGN_IN
            with apps._lock:
                apps._save(app_id, data)
            log.info("oauth: %s refresh refused (%s); it needs a new sign-in", app_id, _error_of(r) or r.status_code)
            raise OAuthError(again(app_id), NEEDS_SIGN_IN)
        raise OAuthError(f"{p['name']} couldn't be reached just now.")
    data = _record(fresh, data)
    with apps._lock:
        apps._save(app_id, data)
    return data["token"]


def access_token(app_id: str, force: bool = False) -> str:
    """The app's current access token, refreshed when it is near its expiry (or `force`, after a 401)."""
    from . import apps
    with _lock(app_id):
        data = apps._load(app_id)
        if not data.get("token"):
            raise OAuthError(f"{name(app_id)} isn't connected.", MISSING)
        if data.get("state") == NEEDS_SIGN_IN:
            raise OAuthError(again(app_id), NEEDS_SIGN_IN)
        exp = float(data.get("expires_at") or 0)
        if app_id == "github" or not data.get("refresh_token") or \
                (not force and (not exp or exp - time.time() > REFRESH_EARLY_SEC)):
            return data["token"]
        return _refresh(app_id, data)


def request(app_id: str, method: str, url: str, *, token: str | None = None, params: dict | None = None,
            json: dict | None = None, headers: dict | None = None) -> httpx.Response:
    """One call to a provider with the app's token (or `token`, while connecting). Raises OAuthError with a plain
    sentence. Logs only the app, the method and the status."""
    from . import apps
    n = name(app_id)
    for attempt in (0, 1):
        tok = token or access_token(app_id, force=attempt == 1)
        try:
            with httpx.Client(timeout=TIMEOUT_SEC) as c:
                r = c.request(method, url, params=params or None, json=json,
                              headers={"Accept": "application/json", "User-Agent": "Zero", **(headers or {}),
                                       "Authorization": f"Bearer {tok}"})
        except httpx.HTTPError:
            raise OAuthError(f"{n} couldn't be reached just now.") from None
        if r.status_code == 401 and attempt == 0 and token is None and app_id != "github":
            continue
        if r.status_code == 401:
            if token is None:
                apps.mark(app_id, NEEDS_SIGN_IN)
            log.info("oauth: %s refused the token", app_id)
            raise OAuthError(again(app_id), NEEDS_SIGN_IN)
        if r.status_code == 429:
            raise OAuthError(f"{n} is busy. Try again in a minute.")
        if r.status_code == 404:
            raise OAuthError(f"{n} couldn't find that.")
        if r.status_code >= 400:
            log.info("oauth: %s %s answered %s", app_id, method, r.status_code)
            raise OAuthError(f"{n} didn't allow that." if r.status_code == 403 else
                             f"{n} didn't accept that." if r.status_code < 500 else f"{n} couldn't answer just now.")
        return r
    raise OAuthError(again(app_id), NEEDS_SIGN_IN)


def disconnect(app_id: str) -> dict:
    """DELETE /api/apps/{id}: forgets the tokens, and revokes them at the provider where it allows that without a
    secret (Dropbox, Linear). Outlook, Notion and GitHub keep Zero in the person's own settings there."""
    from . import apps
    with _lock(app_id), apps._lock:
        data = apps._load(app_id)
        apps._forget(app_id)
    tok, url = data.get("token"), (PROVIDERS.get(app_id) or {}).get("revoke")
    if tok and url:
        try:
            with httpx.Client(timeout=TIMEOUT_SEC) as c:
                if app_id == "linear":
                    c.post(url, data={"token": tok}, headers={"Authorization": f"Bearer {tok}"})
                else:
                    c.post(url, headers={"Authorization": f"Bearer {tok}"})
        except httpx.HTTPError:
            log.info("oauth: revoking %s failed; the tokens are forgotten here anyway", app_id)
    where = {"outlook": "your Microsoft account's privacy settings", "notion": "Notion's settings",
             "github": "GitHub's settings"}.get(app_id)
    return {"ok": True, "detail": f"Disconnected. Your Zips no longer use {name(app_id)}."
                                  + (f" You can also remove Zero in {where}." if where and tok else "")}
