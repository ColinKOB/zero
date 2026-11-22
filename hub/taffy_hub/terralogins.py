""
























import asyncio
import ipaddress
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import config, consent, home, logins, mac, maclogins, who

log = logging.getLogger("taffy.terralogins")

SWITCH = "terra_saved_logins"
MAX_FILLS = 3
LIFETIME = 25

FILLED = ("{bot} answers: I filled the saved login into the sign-in page in your window. Don't type into its fields "
          "or show the password. Press the site's own Sign In or Continue button, then carry on with the job.")


@dataclass(repr=False)
class Pending:
    run: str
    bot: str
    handle: str
    context: str
    origin: str
    public_key: bytes = field(repr=False)
    link: object = field(repr=False)
    expires: float


_pending: dict[str, Pending] = {}


def on(bot_id: str | None = None) -> bool:
    ""


    if config.is_sandbox(bot_id):
        return config.practice_logins(bot_id)
    return home.get(SWITCH) is True


def loopback(origin: str) -> bool:
    ""
    host = urlsplit(origin_of(origin)).hostname or ""
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def practice_only(bot_id: str, origin: str, saved: list[str]) -> None:
    ""

    if config.is_sandbox(bot_id) and not (loopback(origin) and saved and all(loopback(s) for s in saved)):
        raise mac.MacError("A practice run uses only a login saved for a practice site on the Mac (127.0.0.1).")


def origin_of(raw: str) -> str:
    ""
    try:
        p = urlsplit(str(raw or "").strip())
        host = (p.hostname or "").encode("idna").decode("ascii").lower()
        if not host or p.username or p.password:
            return ""
        if p.scheme == "http":
            try:
                loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                return ""
        elif p.scheme != "https":
            return ""
        port = p.port
        default = 443 if p.scheme == "https" else 80
        return f"{p.scheme}://{host}" + (f":{port}" if port and port != default else "")
    except (ValueError, UnicodeError):
        return ""


def exact(origin: str, saved: list[str]) -> bool:
    ""

    here = origin_of(origin)
    return bool(here) and any(origin_of(s) == here for s in saved)


def asked_for(report: str) -> str:
    ""
    last = report.strip().rsplit("\n", 1)[-1].strip()
    if not last.upper().startswith("SIGNIN:"):
        return ""
    words = last.split(":", 1)[1].split()
    return origin_of(words[0].strip("<>()[]\"'.,")) if words else ""


def _item(bot: str, handle: str):
    try:
        return logins.authorized_item(bot, bot, handle)[1]
    except Exception:
        raise mac.MacError("That saved login is not available to you. Check browser_vault_list.") from None


def allowed(bot_id: str, origin: str) -> bool:
    ""

    if on(bot_id):
        return True
    if config.is_sandbox(bot_id) or not origin:
        return False
    from . import signin_notes
    return signin_notes.mac_only(origin)


async def prepare(run: dict, handle: str) -> dict:
    ""
    if not allowed(run["bot_id"], asked_for(run.get("report") or "")):
        raise mac.MacError("Signing Terra in with a saved login is off in this home. Ask "
                           f"{who.first()} to sign in on the page, or answer Terra another way.")
    from . import maccodex
    if run["state"] != "reported" or maccodex._ending(run["report"]) != "signin":
        raise mac.MacError("Terra isn't waiting on a sign-in page in this run.")
    if int(run.get("logins") or 0) >= MAX_FILLS:
        raise mac.MacError(f"This run has filled a saved login {MAX_FILLS} times. Tell {who.first()} where it got to.")
    wanted = asked_for(run["report"])
    item = await asyncio.to_thread(_item, run["bot_id"], handle)
    saved = list(getattr(item, "allowed_origins", None) or []) or [str(item.origin or "")]
    if not wanted or not exact(wanted, saved):
        raise mac.MacError("That saved login isn't for the page Terra is on. Only a login saved for that exact site "
                           "is used.")
    practice_only(run["bot_id"], wanted, saved)
    for token, p in list(_pending.items()):
        if p.expires <= time.monotonic() or p.link is not mac._link:
            _pending.pop(token, None)
    link = mac._link
    try:
        reply = await mac._call("codex_login_context", {"run": run["id"], "origin": wanted})
    except mac.MacError as e:
        if str(e).startswith("Unknown action"):
            raise mac.MacError(f"The Zero app on {who.possessive()} Mac needs an update before Terra can use saved "
                               "logins.") from None


        raise mac.MacError("Not filled: the sign-in page with its password field isn't in the browser window the Mac "
                           f"opened for this run. A saved login is filled only there, never in one of {who.possessive()} "
                           "own windows or one Terra opened. If Terra used another window, answer it to open the page "
                           f"in the window the Mac opened for it; otherwise tell {who.first()} in one line.") from None
    context = reply.get("result") or {}
    try:
        import base64
        identifier = context["context"]
        real = origin_of(context["origin"])
        public_key = base64.b64decode(context["public_key"], validate=True)
        if not isinstance(identifier, str) or not 16 <= len(identifier) <= 128 or len(public_key) != 32 \
                or real != wanted or not exact(real, saved) or mac._link is not link:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise mac.MacError("Not filled: the page in Terra's window isn't the saved login's site.") from None
    token = secrets.token_urlsafe(32)
    _pending[token] = Pending(run["id"], run["bot_id"], handle, identifier, real, public_key, link,
                              time.monotonic() + LIFETIME)
    return {"ticket": token, "site": consent.site_of(real)}


async def fill(run: dict, ticket: str, bot_name: str) -> str:
    ""
    pending = _pending.pop(ticket, None)
    if pending is None or (pending.run, pending.bot) != (run["id"], run["bot_id"]):
        raise mac.MacError("Not filled: this sign-in attempt is no longer available. Call mac_codex with sign_in again.")
    if pending.expires <= time.monotonic() or pending.link is not mac._link or not allowed(run["bot_id"], pending.origin):
        raise mac.MacError("Not filled: the Mac connection or the attempt changed. Try again.")
    item = await asyncio.to_thread(_item, run["bot_id"], pending.handle)
    saved = list(getattr(item, "allowed_origins", None) or []) or [str(item.origin or "")]
    if not exact(pending.origin, saved):
        raise mac.MacError("Not filled: this login is no longer saved for that site.")
    practice_only(run["bot_id"], pending.origin, saved)
    try:
        secret = await asyncio.to_thread(logins.resolve, run["bot_id"], run["bot_id"], pending.handle)
        password = secret.get("password")
        identifier = getattr(item, "identifier", None) or secret.get("identifier") or ""
        if not isinstance(password, str) or not password or len(password) > 4096 \
                or not isinstance(identifier, str) or len(identifier) > 4096:
            raise ValueError()
        package = maclogins.seal(pending.public_key, pending.context, identifier, password)
        del secret, password, identifier
    except Exception:
        raise mac.MacError("Not filled: the saved login could not be opened safely.") from None
    try:
        reply = await mac._call("codex_fill_login", {"run": run["id"], "context": pending.context, **package})
    except mac.MacError:
        raise mac.MacError("Not filled: the Mac couldn't put the login into the page. Sign-in is not confirmed.") from None
    result = reply.get("result")
    if not isinstance(result, dict) or result.get("status") != "filled":
        raise mac.MacError("Not filled: the Mac didn't confirm the fill. Sign-in is not confirmed.")
    log.info("Terra run %s: a saved login was filled for %s", run["id"], consent.site_of(pending.origin))
    return FILLED.format(bot=bot_name)


async def cancel(run: dict, ticket: str) -> None:
    pending = _pending.pop(ticket, None)
    if pending and pending.run == run["id"] and pending.link is mac._link:
        try:
            await mac._call("codex_login_cancel", {"run": run["id"], "context": pending.context})
        except mac.MacError:
            pass
