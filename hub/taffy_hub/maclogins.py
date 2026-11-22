""






import base64
import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import consent, logins, mac

LIFETIME = 25
INFO = b"zero-mac-login-v1"


@dataclass(repr=False)
class Fill:
    bot: str
    session: str
    handle: str
    context: str
    origin: str
    public_key: bytes = field(repr=False)
    link: object = field(repr=False)
    expires: float


_pending: dict[str, Fill] = {}


async def _helper(op: str, args: dict | None = None) -> dict:
    try:
        return await mac._call(op, args)
    except mac.NotGranted:
        raise
    except mac.MacError as error:
        reasons = {
            "not_granted": "The Mac's go-ahead ended. Call mac_start again if there is more to do.",
            "login_unavailable": "Not filled: focus a password field on the site's own page in frontmost Safari or Chrome. The Mac must be able to verify its address.",
            "login_context_invalid": "Not filled: this sign-in attempt expired or was already used. Call mac_fill_login again.",
            "login_envelope_invalid": "Not filled: the private delivery could not be verified. Call mac_fill_login again.",
            "login_page_changed": "The browser page or focused password field changed. Check mac_login_status before trying again.",
            "login_output_busy": "Not filled: another Mac action is still finishing. Try again after it finishes.",
            "login_fill_failed": "macOS could not finish filling the saved login. Sign-in is not confirmed.",
            "login_protection_unavailable": "Not filled: the Mac could not keep saved-login protection working. Sign-in is paused.",
        }
        raise mac.MacError(reasons.get(str(error), "The Mac could not confirm this sign-in step. Check mac_login_status before trying again.")) from None


def _origin(raw: str) -> str:
    try:
        p = urlsplit(raw)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment \
                or p.path not in ("", "/"):
            return ""
        host = p.hostname.encode("idna").decode("ascii").lower()
        port = p.port
        if ":" in host:
            host = f"[{host}]"
        return f"https://{host}" + (f":{port}" if port and port != 443 else "")
    except (ValueError, UnicodeError):
        return ""


def matches(origin: str, allowed: list[str]) -> bool:
    ""
    here = _origin(origin)
    if not here:
        return False
    site = consent.login_site(urlsplit(here).hostname or "")
    for raw in allowed:
        saved = _origin(raw)
        if saved and (saved == here or (site and site == consent.login_site(urlsplit(saved).hostname or ""))):
            return True
    return False


def _item(bot: str, handle: str):
    try:
        vault, item = logins.authorized_item(bot, bot, handle)
    except Exception:
        item = None
    if item is None:
        raise mac.MacError("That saved login is not available to this Zip. Check browser_vault_list.")
    return vault, item


def _allowed(item) -> list[str]:
    return list(getattr(item, "allowed_origins", None) or []) or ([str(item.origin)] if item.origin else [])


async def prepare(bot: str, session: str, handle: str) -> dict:
    await asyncio.to_thread(_item, bot, handle)
    for token, pending in list(_pending.items()):
        if pending.expires <= time.monotonic() or pending.link is not mac._link:
            _pending.pop(token, None)
    if len(_pending) >= 32:
        raise mac.MacError("Too many sign-in attempts are waiting. Try again in half a minute.")
    link = mac._link
    reply = await _helper("login_context")
    context = reply.get("result")
    try:
        identifier = context["context"]
        origin = _origin(context["origin"])
        public_key = base64.b64decode(context["public_key"], validate=True)
        if not isinstance(identifier, str) or not 16 <= len(identifier) <= 128 or not origin or len(public_key) != 32:
            raise ValueError()
        _, item = await asyncio.to_thread(_item, bot, handle)
        if mac._link is not link or not matches(origin, _allowed(item)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise mac.MacError("Not filled: the front browser page does not match this saved login, or its address could not be verified.") from None
    token = secrets.token_urlsafe(32)
    _pending[token] = Fill(bot, session, handle, identifier, origin, public_key, link, time.monotonic() + LIFETIME)
    return {"ticket": token, "site": consent.site_of(origin)}


async def cancel(bot: str, session: str, ticket: str) -> dict:
    pending = _pending.get(ticket)
    if pending and (pending.bot, pending.session) == (bot, session):
        _pending.pop(ticket, None)
        if pending.link is mac._link:
            await _helper("login_cancel", {"context": pending.context})
    return {"status": "cancelled"}


async def fill(bot: str, session: str, ticket: str) -> dict:
    pending = _pending.get(ticket)
    if pending is None or (pending.bot, pending.session) != (bot, session):
        raise mac.MacError("Not filled: this sign-in attempt is no longer available. Call mac_fill_login again.")

    _pending.pop(ticket, None)
    if pending.expires <= time.monotonic() or pending.link is not mac._link:
        raise mac.MacError("Not filled: the Mac connection or sign-in attempt changed. Call mac_fill_login again.")
    _, item = await asyncio.to_thread(_item, bot, pending.handle)
    if not matches(pending.origin, _allowed(item)):
        raise mac.MacError("Not filled: this login is no longer saved for that page.")
    try:
        secret = await asyncio.to_thread(logins.resolve, bot, bot, pending.handle)
        password = secret.get("password")
        identifier = getattr(item, "identifier", None) or secret.get("identifier") or ""
        if not isinstance(password, str) or not password or len(password) > 4096 \
                or not isinstance(identifier, str) or len(identifier) > 4096:
            raise ValueError()
        package = seal(pending.public_key, pending.context, identifier, password)
        del secret, password, identifier
    except Exception:
        raise mac.MacError("Not filled: the saved login could not be opened safely.") from None

    reply = await _helper("fill_login", {"context": pending.context, **package})
    result = reply.get("result")
    if not isinstance(result, dict) or result.get("status") != "filled":
        raise mac.MacError("The Mac did not confirm the fill. Check mac_login_status before trying again.")
    return {"status": "filled", "message": "I filled the saved login. Sign-in is not confirmed. Submit the form, then call mac_login_status."}


def seal(public_key: bytes, context: str, identifier: str, password: str) -> dict:
    ""

    private = X25519PrivateKey.generate()
    shared = private.exchange(X25519PublicKey.from_public_bytes(public_key))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=context.encode(), info=INFO).derive(shared)
    nonce = secrets.token_bytes(12)
    plain = json.dumps({"identifier": identifier, "password": password}).encode()
    sealed = nonce + AESGCM(key).encrypt(nonce, plain, context.encode())
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    del plain, key, shared, private
    return {"public_key": base64.b64encode(public).decode(), "sealed": base64.b64encode(sealed).decode()}


async def status() -> dict:
    reply = await _helper("login_status")
    result = reply.get("result")
    value = result.get("status") if isinstance(result, dict) else "unknown"
    messages = {
        "two_factor": "The page shows a two-factor verification prompt.",
        "bot_check": "The page shows a bot check.",
        "login_form": "The page still shows a sign-in form. Sign-in is not confirmed.",
        "page_changed": "The sign-in page changed. I cannot confirm that sign-in succeeded.",
        "sign_out_control": "The page shows a Sign Out control and no password field. Sign-in appears to have succeeded.",
        "unknown": "I cannot verify the sign-in result from this page.",
    }
    value = value if value in messages else "unknown"
    return {"status": value, "message": messages[value]}
