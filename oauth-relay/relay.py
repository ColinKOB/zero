"""Zero's OAuth relay (Railway): the one public place that holds the client secrets of Zero's own developer apps.

Some providers (Notion, Atlassian, Todoist) give no sign-in without a client secret, and a secret cannot ship in the
iPhone app or in every home's hub. Most also want an https address to send the person back to, and a home's hub has
none. This service does both, and nothing else:

  GET  /callback/<provider>   the provider's redirect. A tiny page forwards the query and fragment to
                              zero://oauth/<provider>, which closes the app's sign-in sheet with the code.
  POST /token/<provider>      the hub sends {"code", "redirect_uri", "code_verifier"?} or {"refresh_token"}; the relay
                              adds the client ID and secret, asks the provider, and returns the provider's answer.
  POST /stripe/webhook        Stripe's events. The relay checks Stripe's signature (STRIPE_WEBHOOK_SECRET, comma-separated
                              for the test and live endpoints), signs the same body again with BILLING_FORWARD_SECRET in
                              Stripe's format, and posts it to BILLING_FORWARD_URL (the front door's
                              /billing/stripe/webhook, hub/taffy_hub/billing.py). A failed forward answers 502, so
                              Stripe retries for up to three days. The Stripe secret never leaves Railway.
  GET  /health

It keeps nothing: no store, no codes or tokens in the log. A code only works for the app it was issued to, so the
relay only ever swaps codes for Zero's own apps, and only with its own callback as the redirect. Secrets come from the
environment (Railway variables): <PROVIDER>_CLIENT_ID and <PROVIDER>_CLIENT_SECRET.

Run: python3 relay.py (PORT from the environment, 8080 by default). Standard library only.
"""

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("relay")

PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
SCHEME = "zero"
LIMIT = 30            # token requests per address per ten minutes
WINDOW_SEC = 600


def _basic(cid: str, secret: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()}


# How each provider wants its token request. "auth": "basic" puts the client in a Basic header, "body" in the body.
PROVIDERS = {
    "notion": {"url": "https://api.notion.com/v1/oauth/token", "format": "json", "auth": "basic"},
    "atlassian": {"url": "https://auth.atlassian.com/oauth/token", "format": "json", "auth": "body"},
    "todoist": {"url": "https://todoist.com/oauth/access_token", "format": "form", "auth": "body"},
}
# Providers whose return page the relay serves without holding a secret (their apps use PKCE or a public key).
CALLBACK_ONLY = {"trello", "dropbox", "linear", "github", "microsoft"}

_recent: dict[str, deque] = defaultdict(deque)

STRIPE_TOLERANCE_SEC = 300
MAX_EVENT_BYTES = 1_000_000


def _secrets(name: str) -> list[str]:
    return [s.strip() for s in os.environ.get(name, "").split(",") if s.strip()]


def stripe_sign(payload: bytes, secret: str, now: float | None = None) -> str:
    t = int(time.time() if now is None else now)
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()


def stripe_verified(payload: bytes, header: str | None, secrets: list[str], now: float | None = None) -> bool:
    """Stripe's scheme: HMAC-SHA256 of "<t>.<body>" in a v1 entry, with t within five minutes."""
    if not header or not secrets:
        return False
    parts = [p.strip().partition("=") for p in header.split(",")]
    t = next((v for k, _, v in parts if k == "t"), "")
    sigs = [v for k, _, v in parts if k == "v1"]
    now = time.time() if now is None else now
    if not t.isdigit() or not sigs or abs(now - int(t)) > STRIPE_TOLERANCE_SEC:
        return False
    for s in secrets:
        want = hmac.new(s.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
        if any(hmac.compare_digest(want, sig) for sig in sigs):
            return True
    return False


def billing_ready() -> bool:
    return bool(_secrets("STRIPE_WEBHOOK_SECRET") and os.environ.get("BILLING_FORWARD_URL")
                and _secrets("BILLING_FORWARD_SECRET"))


def forward_event(payload: bytes) -> int:
    """Posts a verified Stripe event to the front door, signed with the forwarding secret. Returns its status."""
    req = urllib.request.Request(os.environ["BILLING_FORWARD_URL"], data=payload, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "zero-oauth-relay",
        "Stripe-Signature": stripe_sign(payload, _secrets("BILLING_FORWARD_SECRET")[0])})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def callback_url(provider: str) -> str:
    return f"{PUBLIC_URL}/callback/{provider}"


def client(provider: str) -> tuple[str, str] | None:
    cid = os.environ.get(f"{provider.upper()}_CLIENT_ID", "")
    secret = os.environ.get(f"{provider.upper()}_CLIENT_SECRET", "")
    return (cid, secret) if cid and secret else None


def allowed(addr: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    q = _recent[addr]
    while q and q[0] < now - WINDOW_SEC:
        q.popleft()
    if len(q) >= LIMIT:
        return False
    q.append(now)
    return True


def token_request(provider: str, body: dict) -> urllib.request.Request:
    """The provider's token request for a code or a refresh token. Raises ValueError on a bad body."""
    spec = PROVIDERS[provider]
    cid, secret = client(provider)
    if body.get("refresh_token"):
        form = {"grant_type": "refresh_token", "refresh_token": str(body["refresh_token"])}
    elif body.get("code"):
        redirect = str(body.get("redirect_uri") or callback_url(provider))
        if redirect != callback_url(provider):
            raise ValueError("redirect_uri must be this relay's callback")
        form = {"grant_type": "authorization_code", "code": str(body["code"]), "redirect_uri": redirect}
        if body.get("code_verifier"):
            form["code_verifier"] = str(body["code_verifier"])
    else:
        raise ValueError("send code or refresh_token")
    headers = {"Accept": "application/json", "User-Agent": "zero-oauth-relay"}
    if spec["auth"] == "basic":
        headers.update(_basic(cid, secret))
    else:
        form.update(client_id=cid, client_secret=secret)
    if spec["format"] == "json":
        data = json.dumps(form).encode()
        headers["Content-Type"] = "application/json"
    else:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return urllib.request.Request(spec["url"], data=data, headers=headers, method="POST")


def forward_page(provider: str) -> str:
    """The return page: hands the query and fragment to the app. Fragments never reach a server (Trello's token)."""
    target = html.escape(f"{SCHEME}://oauth/{provider}", quote=True)
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Back to Zero</title><style>body{{font:17px -apple-system,system-ui,sans-serif;text-align:center;padding:4em 1em;color:#222}}</style>
<p>Taking you back to Zero.</p><p><a id="go" href="{target}">Open Zero</a></p>
<script>var t="{target}"+location.search+location.hash;document.getElementById("go").href=t;location.replace(t);</script>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "zero-relay"

    def log_message(self, fmt, *args):   # the default logs full paths, and a callback's query holds a code
        log.info("%s %s %s", self.command, urllib.parse.urlsplit(self.path).path, args[1] if len(args) > 1 else "")

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if path == "/health":
            return self._json(200, {"ok": True, "ready": sorted(p for p in PROVIDERS if client(p)),
                                    "billing": billing_ready()})
        parts = path.split("/")
        if len(parts) == 3 and parts[1] == "callback" and (parts[2] in PROVIDERS or parts[2] in CALLBACK_ONLY):
            return self._send(200, forward_page(parts[2]).encode(), "text/html; charset=utf-8")
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path.rstrip("/") == "/stripe/webhook":
            return self._stripe()
        parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
        if len(parts) != 3 or parts[1] != "token" or parts[2] not in PROVIDERS:
            return self._json(404, {"error": "not_found"})
        provider = parts[2]
        if not client(provider):
            return self._json(503, {"error": "not_set_up"})
        addr = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        if not allowed(addr):
            return self._json(429, {"error": "slow_down"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(size, 16384)) or b"{}")
            req = token_request(provider, body if isinstance(body, dict) else {})
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"error": "bad_request", "detail": str(e)})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return self._send(r.status, r.read(), "application/json")
        except urllib.error.HTTPError as e:
            log.info("%s token request refused: %s", provider, e.code)
            return self._send(e.code, e.read(), "application/json")
        except OSError:
            return self._json(502, {"error": "provider_unreachable"})

    def _stripe(self):
        if not billing_ready():
            return self._json(503, {"error": "not_set_up"})
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_EVENT_BYTES:
            return self._json(413, {"error": "too_large"})
        payload = self.rfile.read(size)
        if not stripe_verified(payload, self.headers.get("Stripe-Signature"), _secrets("STRIPE_WEBHOOK_SECRET")):
            return self._json(400, {"error": "bad_signature"})
        status = forward_event(payload)
        if 200 <= status < 300:
            return self._json(200, {"received": True})
        log.info("stripe event not delivered to the front door: %s", status)
        return self._json(502, {"error": "front_door_unavailable"})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    port = int(os.environ.get("PORT", "8080"))
    log.info("relay on %s, ready: %s", port, sorted(p for p in PROVIDERS if client(p)))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
