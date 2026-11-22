""























import base64
import hashlib
import logging
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

from . import connections, home

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
    "posthog": {"name": "PostHog", "home": "posthog", "field": "client_id",
                "authorize": "https://oauth.posthog.com/oauth/authorize/",
                "token": "https://oauth.posthog.com/oauth/token/",
                "register": "https://oauth.posthog.com/oauth/register/",
                "revoke": "https://oauth.posthog.com/oauth/revoke/",
                "scope": "openid profile email user:read organization:read project:read query:read "
                         "insight:read error_tracking:read event_definition:read",
                "redirect": f"{SCHEME}://oauth/posthog", "extra": {"required_access_level": "project"}},
    "railway": {"name": "Railway", "home": "railway", "field": "client_id",
                "authorize": "https://backboard.railway.com/oauth/auth",
                "token": "https://backboard.railway.com/oauth/token",
                "register": "https://backboard.railway.com/oauth/register",



                "scope": "openid email profile offline_access workspace:viewer project:member",
                "redirect": f"{SCHEME}://oauth/railway", "extra": {"prompt": "consent"}},
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


GITHUB = {"name": "GitHub", "home": "github", "field": "client_id", "device": "https://github.com/login/device/code",
          "token": "https://github.com/login/oauth/access_token", "scope": "repo read:user",
          "verify": "https://github.com/login/device"}
APPS = (*PROVIDERS, "github")

_pending: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class OAuthError(RuntimeError):
    ""


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
    if re.fullmatch(r"[A-Za-z0-9._-]{8,200}", cid):
        return cid
    if s.get("register"):
        try:
            data = json.loads(_client_file(app_id).read_text())
            cid = data.get("client_id")
            if isinstance(cid, str) and cid and data.get("redirect_uri") == redirect_uri(app_id):
                return cid
        except (OSError, ValueError, AttributeError):
            pass
    return None


def _client_file(app_id: str):
    from . import config
    return config.HUB_DIR / "apps" / ".oauth" / f"{app_id}.json"


def _registered_client(app_id: str) -> str:
    ""
    with _lock(app_id):
        cid = client_id(app_id)
        if cid:
            return cid
        p = PROVIDERS[app_id]
        try:
            r = _post(p["register"], json={"client_name": "Zero", "redirect_uris": [redirect_uri(app_id)],
                      "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                      "token_endpoint_auth_method": "none"})
        except httpx.HTTPError:
            raise OAuthError(f"{p['name']} couldn't be reached. Try again in a minute.") from None
        data = _json(r)
        cid = data.get("client_id")
        if not r.is_success or not isinstance(cid, str) or not cid or \
                data.get("token_endpoint_auth_method", "none") != "none":
            raise OAuthError(f"{p['name']} couldn't start the sign-in. Try again in a minute.")
        f = _client_file(app_id)
        f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(f.parent.parent, 0o700)
        os.chmod(f.parent, 0o700)
        tmp = f.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"client_id": cid, "redirect_uri": redirect_uri(app_id)}, fh)
        os.chmod(tmp, 0o600)
        tmp.replace(f)
        return cid


def relay() -> str | None:
    url = str(home.get("apps.relay.url") or "").strip().rstrip("/")
    return url if url.startswith("https://") else None


def redirect_uri(app_id: str) -> str:
    r = PROVIDERS[app_id]["redirect"]
    return f"{relay()}/callback/{app_id}" if r == "relay" else r


def available(app_id: str) -> bool:
    ""
    if PROVIDERS.get(app_id, {}).get("register"):
        return True
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
    ""
    with httpx.Client(timeout=TIMEOUT_SEC) as c:
        return c.post(url, data=data, json=json, headers={"Accept": "application/json",
                                                          "User-Agent": "Zero"})


def _error_of(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        return ""
    return str(err if isinstance(err, str) else "")[:60]




def start(app_id: str) -> dict:
    ""
    p = PROVIDERS[app_id]
    if not available(app_id):
        raise OAuthError(f"{p['name']} isn't ready for your home yet.", MISSING)
    cid = _registered_client(app_id) if p.get("register") else client_id(app_id)
    now = time.time()
    _sweep(now)
    state = secrets.token_urlsafe(24)
    q = {"client_id": cid, "response_type": "code", "redirect_uri": redirect_uri(app_id), "state": state,
         **p["extra"]}
    if p["scope"]:
        q["scope"] = p["scope"]
    entry = {"app": app_id, "exp": now + START_TTL_SEC, "client_id": cid, "connection": connections.capture()}
    if p["redirect"] != "relay":
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        q.update(code_challenge=challenge, code_challenge_method="S256")
        entry["verifier"] = verifier
    _pending[state] = entry
    return {"mode": "oauth", "url": f"{p['authorize']}?{urlencode(q)}", "callback_scheme": SCHEME}


def _record(data: dict, old: dict) -> dict:
    ""
    out = {**old, "token": data["access_token"], "state": CONNECTED,
           "refresh_token": data.get("refresh_token") or old.get("refresh_token") or "",
           "expires_at": time.time() + float(data["expires_in"]) if data.get("expires_in") else 0}
    if data.get("scope"):
        out["scope"] = data["scope"] if isinstance(data["scope"], str) else " ".join(map(str, data["scope"]))
    if data.get("client_id"):
        out["client_id"] = data["client_id"]
    return out


def _who(app_id: str, token: str, data: dict) -> str:
    ""
    from . import dropbox, github, linear, microsoft, notion
    if app_id == "notion":
        notion.check(token)
        return str(data.get("workspace_name") or "")[:120]
    return {"outlook": microsoft.me, "dropbox": dropbox.me, "linear": linear.me, "github": github.me}[app_id](token)


def _keep(app_id: str, data: dict) -> dict:
    ""
    from . import apps, keys
    checked = {}
    try:
        if app_id in keys.BY_ID:
            checked = keys.check(app_id, data["access_token"])
            account = checked["account"]
            stored = {"token": data["access_token"], "api": checked["api"]}
            proof = keys.request(app_id, stored, "GET", "/api/projects/") if app_id == "posthog" else \
                    keys.request(app_id, stored, "POST", "/graphql/v2", {"query": keys.RAIL_OAUTH_PROJECTS})
            if proof.get("status") != 200 or (proof.get("data") or {}).get("errors"):
                raise keys.KeyAppError(f"{name(app_id)} didn't let Zero read your projects. Tap Connect and try again.")
            checked["projects"] = _project_names(app_id, proof)
        else:
            account = _who(app_id, data["access_token"], data)
    except keys.KeyAppError as e:
        return {"ok": False, "detail": str(e)}
    except OAuthError as e:
        return {"ok": False, "detail": str(e) if e.state == CONNECTED else
                f"{name(app_id)} didn't let Zero read your account. Tap Connect and try again."}
    with _lock(app_id), apps._lock:
        rec = _record(data, {})
        rec.update(account=account, connected_at=time.time(), auth="oauth")
        for field in ("api", "projects"):
            if field in checked:
                rec[field] = checked[field]
        apps._save(app_id, rec)
    log.info("oauth: %s was connected", app_id)
    projects = checked.get("projects", [])
    detail = f"Connected {name(app_id)}{(' as ' + account) if account else ''}."
    detail += f" Your Zips can read {', '.join(projects)}." if projects else \
              " No projects are available to this connection." if app_id in keys.BY_ID else " Your Zips can read it now."
    return {"ok": True, "detail": detail, "app": apps.describe(app_id)}


def _project_names(app_id: str, proof: dict) -> list[str]:
    data = proof.get("data") or {}
    if app_id == "posthog":
        rows = data.get("results", []) if isinstance(data, dict) else data
    else:
        rows = [p for w in (data.get("data") or {}).get("externalWorkspaces", []) for p in w.get("projects", [])]
    return [str(r["name"])[:120] for r in rows if isinstance(r, dict) and r.get("name")][:20]


def finish(app_id: str, code: str, state: str) -> dict:
    ""
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
                    "client_id": entry["client_id"], "code_verifier": entry.get("verifier", "")}
            if app_id == "outlook":
                form["scope"] = p["scope"]
            r = _post(p["token"], data=form)
    except httpx.HTTPError:
        return {"ok": False, "detail": f"{p['name']} couldn't be reached. Try again in a minute."}
    if r.status_code != 200 or not _json(r).get("access_token"):
        log.info("oauth: %s code exchange refused (%s %s)", app_id, r.status_code, _error_of(r) or "no error code")
        return {"ok": False, "detail": f"{p['name']} didn't connect. Tap Connect and try again."}
    with connections.using(entry.get("connection")):
        return _keep(app_id, {**_json(r), "client_id": entry["client_id"]})


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}




def device_start() -> dict:
    ""
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
                       "device_code": data["device_code"], "interval": interval, "next": now,
                       "connection": connections.capture()}
    log.info("oauth: github device sign-in started")
    url = str(data.get("verification_uri") or "")
    return {"mode": "device", "state": state, "user_code": str(data["user_code"])[:20], "interval": interval,
            "url": url if url.startswith("https://github.com/") else GITHUB["verify"], "callback_scheme": SCHEME}


def device_poll(state: str) -> dict:
    ""


    entry = _pending.get(str(state or ""))
    now = time.time()
    if not entry or entry["app"] != "github" or entry["exp"] < now:
        if entry and "result" not in entry:
            log.info("oauth: github device code ran out before GitHub allowed Zero")
        _pending.pop(str(state or ""), None)
        return {"ok": False, "detail": "The code ran out. Tap Connect to get a new one."}
    if "result" in entry:
        return entry["result"]
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
    if err == "access_denied":
        log.info("oauth: github device sign-in denied by the person")
        out = {"ok": False, "detail": "GitHub wasn't allowed. Tap Connect to try again."}
    elif err == "expired_token":
        log.info("oauth: github device code expired before it was entered")
        out = {"ok": False, "detail": "The code ran out. Tap Connect to get a new one."}
    elif r.status_code != 200 or not data.get("access_token"):
        log.info("oauth: github device sign-in refused (%s %s)", r.status_code, err or "no error code")
        out = {"ok": False, "detail": "GitHub didn't connect. Tap Connect and try again."}
    else:
        with connections.using(entry.get("connection")):
            out = _keep("github", data)
        if not out["ok"]:
            log.info("oauth: github device sign-in allowed, but the token didn't read the account")
    entry["result"] = out
    return out


def device_status(state: str) -> dict:
    ""


    entry = _pending.get(str(state or ""))
    if not entry or entry["app"] != "github" or entry["exp"] < time.time():
        return {"ok": False, "detail": "The code ran out. Tap Connect to get a new one."}
    return entry.get("result") or {"ok": False, "pending": True, "interval": entry["interval"]}




def _refresh(app_id: str, data: dict) -> str:
    ""




    from . import apps
    p = _spec(app_id)
    try:
        if p["token"] == "relay":
            r = _post(f"{relay()}/token/{app_id}", json={"refresh_token": data["refresh_token"]})
        else:
            form = {"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                    "client_id": data.get("client_id") or client_id(app_id) or ""}
            if app_id == "outlook":
                form["scope"] = p["scope"]
            r = _post(p["token"], data=form)
    except httpx.HTTPError:
        raise OAuthError(f"{p['name']} couldn't be reached just now.") from None
    fresh = _json(r)
    if r.status_code != 200 or not fresh.get("access_token"):
        if r.status_code in (400, 401) or fresh.get("error"):
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
    ""
    from . import apps
    with _lock(app_id):
        data = apps._load(app_id)
        if not data.get("token"):
            raise OAuthError(f"{name(app_id)} isn't connected.", MISSING)
        if data.get("state") == NEEDS_SIGN_IN:
            raise OAuthError(again(app_id), NEEDS_SIGN_IN)
        exp = float(data.get("expires_at") or 0)
        if not data.get("refresh_token") or \
                (not force and (not exp or exp - time.time() > REFRESH_EARLY_SEC)):
            return data["token"]
        return _refresh(app_id, data)


KEEP_AHEAD_SEC = 45 * 60


def keep_fresh() -> None:
    ""

    from . import apps
    for app_id in APPS:
        try:
            doc = connections.document(apps._document(app_id))
        except apps.AppError:
            continue
        for cid, data in doc["connections"].items():
            exp = float(data.get("expires_at") or 0)
            if not data.get("refresh_token") or data.get("state") == NEEDS_SIGN_IN or not exp \
                    or exp - time.time() > KEEP_AHEAD_SEC:
                continue
            try:
                with connections.using({"app": app_id, "id": cid}):
                    access_token(app_id, force=True)
            except OAuthError as e:
                log.info("oauth: keeping %s fresh failed (%s)", app_id, e.state)


def request(app_id: str, method: str, url: str, *, token: str | None = None, params: dict | None = None,
            json: dict | None = None, headers: dict | None = None) -> httpx.Response:
    ""

    with connections.using(connections.current(app_id)):
        return _request(app_id, method, url, token=token, params=params, json=json, headers=headers)


def _request(app_id: str, method: str, url: str, *, token: str | None, params: dict | None,
             json: dict | None, headers: dict | None) -> httpx.Response:
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
        if r.status_code == 401 and attempt == 0 and token is None:
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
    ""

    from . import apps
    with _lock(app_id), apps._lock:
        data = apps._load(app_id)
        apps._forget(app_id)
    tok, url = data.get("token"), (PROVIDERS.get(app_id) or {}).get("revoke")
    if tok and url:
        try:
            with httpx.Client(timeout=TIMEOUT_SEC) as c:
                if app_id == "posthog":
                    c.post(url, data={"token": data.get("refresh_token") or tok,
                                     "client_id": data.get("client_id") or client_id(app_id) or ""})
                elif app_id == "linear":
                    c.post(url, data={"token": tok}, headers={"Authorization": f"Bearer {tok}"})
                else:
                    c.post(url, headers={"Authorization": f"Bearer {tok}"})
        except httpx.HTTPError:
            log.info("oauth: revoking %s failed; the tokens are forgotten here anyway", app_id)
    where = {"outlook": "your Microsoft account's privacy settings", "notion": "Notion's settings",
             "github": "GitHub's settings"}.get(app_id)
    return {"ok": True, "detail": f"Disconnected. Your Zips no longer use {name(app_id)}."
                                  + (f" You can also remove Zero in {where}." if where and tok else "")}
