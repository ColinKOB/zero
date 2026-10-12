"""Google APIs for a mailbox or calendar the person connected with "Sign In with Google" (kind "google_oauth").

The hub holds the refresh token (WP1); this module only ever sees short-lived access tokens, which it asks the hub for
on loopback with the internal key, the same way the Mac and account tools reach the hub:

    POST http://127.0.0.1:<ports.hub>/internal/accounts/google_token
    {"bot_id": ..., "session_id": ..., "args": {"account": "<id from home.json>", "address": "<the Google address>",
                                                 "purpose": "mail" | "calendar"}}
    -> {"ok": true, "result": {"access_token": "...", "expires_at": <unix seconds>}}       (or "expires_in": seconds)
    -> {"ok": true, "result": {"connected": false, "message": "<a plain sentence for the Zip>"}}
    -> {"ok": false, "error": "<a plain sentence>"}

Tokens are kept in this process until a minute before they expire, are never written anywhere, and never appear in
a tool's result. A 401 from Google drops the token and asks the hub once more.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/"
CALENDAR = "https://www.googleapis.com/calendar/v3/"
TIMEOUT_SEC = 60
SKEW_SEC = 60

_tokens: dict[tuple[str, str], tuple[str, float]] = {}


class GoogleError(RuntimeError):
    """Google or the hub could not do it. The message is a plain sentence for the Zip."""


def _hub_token(account: dict, purpose: str, session_id: str) -> dict:
    """The hub's answer for a fresh access token. Tests replace this."""
    from . import mac
    return mac._call("google_token", {"account": account.get("id", ""), "address": account.get("address", ""),
                                      "purpose": purpose}, 30, session_id=session_id, area="accounts")


def token(account: dict, purpose: str, session_id: str = "") -> str:
    key = (str(account.get("id")), purpose)
    hit = _tokens.get(key)
    if hit and hit[1] - SKEW_SEC > time.time():
        return hit[0]
    reply = _hub_token(account, purpose, session_id)
    if not reply.get("ok"):
        raise GoogleError(reply.get("error") or "The Google sign-in for this account could not be reached just now.")
    result = reply.get("result") or {}
    if result.get("connected") is False or not result.get("access_token"):
        raise GoogleError(result.get("message") or "This Google account needs to be signed in again in the app.")
    try:
        expires = float(result.get("expires_at") or (time.time() + float(result.get("expires_in") or 300)))
    except (TypeError, ValueError):
        expires = time.time() + 300
    _tokens[key] = (str(result["access_token"]), expires)
    return _tokens[key][0]


def forget(account: dict, purpose: str) -> None:
    _tokens.pop((str(account.get("id")), purpose), None)


def http(method: str, url: str, access_token: str, body=None, params=None):
    """One request to a Google API: JSON in and out. Raises urllib.error.HTTPError as it comes. Tests replace this."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {access_token}",
                                          **({"Content-Type": "application/json"} if data is not None else {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def call(account: dict, purpose: str, method: str, url: str, body=None, params=None, session_id: str = ""):
    """A request with the account's token, asking the hub again once if Google says the token is no good."""
    for attempt in (1, 2):
        tok = token(account, purpose, session_id)
        try:
            return http(method, url, tok, body=body, params=params)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                forget(account, purpose)
                continue
            detail = ""
            try:
                detail = (json.loads(e.read() or b"{}").get("error") or {}).get("message") or ""
            except (ValueError, AttributeError):
                pass
            raise GoogleError(f"Google answered {e.code}" + (f": {detail[:300]}" if detail else ".")) from None
        except urllib.error.URLError as e:
            raise GoogleError(f"Google could not be reached just now ({e.reason}).") from None
    raise GoogleError("This Google account needs to be signed in again in the app.")
