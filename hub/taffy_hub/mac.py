"""Bots using Colin's Mac, with one approval per task.

The Mac app holds a WebSocket here (/api/mac/link) while Taffy is open on the Mac. Bots call the
internal endpoints (taffy-tools plugin, 127.0.0.1 with the internal key). A grant starts only from
the conversation where Colin asked, quoting words he really said, and only after he presses Allow on
the Mac. It belongs to that one conversation and lasts until its turn ends, 30 minutes pass, the bot
says it is done, or Colin presses Stop. The Mac app keeps its own copy of the grant and refuses
everything without it, so this file is not the only lock.

Colin can also answer from his iPhone (Oct 10: "if I'm out of the house ... I should be able to authorize that
from my phone too"). The request shows on the Mac, as a card in the Zip's chat, as a notification with Allow and
Not Now, and on the call screen during a call. The first answer wins. An Allow from the phone is signed by a key
in the phone's Secure Enclave that only Face ID or the passcode unlocks, over the same request id and words the Mac
shows, and the Mac checks that signature against the phones Colin trusted on the Mac itself before it starts the
grant. So the hub still cannot start a grant on its own, and a phone approval has the same task, limits, in-use
bar and Stop as one made at the Mac.
"""

import asyncio
import itertools
import json
import logging
import re
import secrets
import shlex
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fastapi import WebSocket

from . import home, store, turns, who

log = logging.getLogger("taffy.mac")

GRANT_LIMIT_SEC = 30 * 60
APPROVAL_WAIT_SEC = 120
RECONNECT_GRACE_SEC = 15
CALL_TIMEOUT_SEC = {"run": 660, "screenshot": 30, "approve": APPROVAL_WAIT_SEC + 5, "steps": 90}


class MacError(Exception):
    """A plain-words reason the action did not happen, returned to the bot."""


class NotGranted(MacError):
    """The Mac says Colin's go-ahead has ended there (Stop, its clock, a dropped link)."""


@dataclass
class Grant:
    bot_id: str
    bot_name: str
    session_id: str
    task: str
    colin_said: str
    started: float = field(default_factory=time.time)
    turn: object | None = None
    ended: bool = False
    actions: list = field(default_factory=list)
    details: list = field(default_factory=list)
    feed_id: int | None = None

    def expired(self) -> bool:
        return time.time() - self.started > GRANT_LIMIT_SEC


@dataclass
class Approval:
    """One request to use the Mac, waiting for Colin on the Mac, his phone, or the call screen."""
    id: str
    bot_id: str
    bot_name: str
    task: str
    said: str
    said_at: float
    created: float = field(default_factory=time.time)
    state: str = "open"
    via: str = ""
    feed_id: int | None = None

    @property
    def expires(self) -> int:
        """When the Mac's card closes itself (the bridge's approvalSeconds, under the hub's wait)."""
        return int(self.created + APPROVAL_WAIT_SEC - 5)

    def public(self) -> dict:
        """What the phone shows and signs: the same words the Mac shows."""
        return {"id": self.id, "bot_id": self.bot_id, "bot": self.bot_name, "task": self.task, "said": self.said,
                "said_at": self.said_at, "expires": self.expires, "state": self.state, "via": self.via,
                "feed_id": self.feed_id}


_link: WebSocket | None = None
_pending: dict[int, asyncio.Future] = {}
_ids = itertools.count(1)
_grant: Grant | None = None
_approval: Approval | None = None
_unreachable_told: dict[str, float] = {}
_lock = asyncio.Lock()


# MARK: The Mac's connection

async def serve_link(ws: WebSocket):
    """Runs for as long as the Mac app is connected. A newer connection replaces an older one."""
    global _link
    old, _link = _link, ws
    if old is not None:
        log.info("Mac link replaced by a newer connection")
        try:
            await old.send_text(json.dumps({"event": "replaced"}))
            await old.close(code=4000, reason="replaced")
        except Exception:
            pass
    log.info("Mac connected")
    try:
        await ws.send_text(json.dumps({"event": "person", "name": who.first()}))
        await ws.send_text(json.dumps({"event": "approvers", "keys": approver_keys()}))
    except Exception:
        pass
    if _grant is None or _grant.ended or _grant.expired():
        asyncio.create_task(_idle())
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("event") == "stop":
                await end_grant(f"{who.first()} pressed Stop on the Mac.", tell_mac=False)
                continue
            fut = _pending.pop(msg.get("id"), None)
            if fut and not fut.done():
                fut.set_result(msg)
    except Exception:
        pass
    finally:
        if _link is ws:
            _link = None
            log.info("Mac disconnected")
            asyncio.create_task(_end_unless_back(_grant))


async def _end_unless_back(grant: "Grant | None"):
    """A short drop (Wi-Fi, sleep, an app update) keeps the grant if the Mac is back within a few seconds.
    The Mac keeps its own copy, so a different Taffy instance coming back still cannot act on it."""
    if grant is None or grant.ended:
        return
    await asyncio.sleep(RECONNECT_GRACE_SEC)
    if _link is None:
        await end_grant("The Mac disconnected.", tell_mac=False)


def online() -> bool:
    return _link is not None


async def _call(op: str, args: dict | None = None) -> dict:
    if _link is None:
        raise MacError(f"{who.mac()} isn't connected. The Zero app needs to be open on it.")
    i = next(_ids)
    fut = asyncio.get_running_loop().create_future()
    _pending[i] = fut
    try:
        await _link.send_text(json.dumps({"id": i, "op": op, "args": args or {}}))
        reply = await asyncio.wait_for(fut, CALL_TIMEOUT_SEC.get(op, 30))
    except asyncio.TimeoutError:
        raise MacError("The Mac did not answer in time.")
    finally:
        _pending.pop(i, None)
    if not reply.get("ok"):
        if reply.get("code") == "not_granted":
            raise NotGranted(reply.get("error") or f"{who.possessive()} go-ahead on the Mac has ended.")
        raise MacError(reply.get("error") or "The Mac could not do that.")
    return reply


# MARK: Colin's own words

RECENT_ASK_SEC = 30 * 60


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'").replace("'", ""))


def best_match(quote: str, candidates: list[str]) -> str | None:
    """The thing Colin said that the quote comes from: nearly every word of the quote must appear in it.
    Loose enough for a trimmed or lightly cleaned-up quote and for speech recognition, strict enough that
    a request a web page, an email or another bot wrote for the bot does not pass."""
    q = _words(quote)
    if len(q) < 2:
        return None
    for text in candidates:
        have = set(_words(text))
        if have and sum(w in have for w in q) / len(q) >= 0.8:
            return text
    return None


def quote_matches(quote: str, candidates: list[str]) -> bool:
    return best_match(quote, candidates) is not None


_MAC_WORDS = {"mac", "macbook", "imac", "computer", "laptop", "desktop", "screen", "pc", "machine", "terminal",
              "finder", "safari", "chrome", "browser", "mouse", "cursor", "app", "apps", "folder", "file", "files",
              "download", "downloads", "install", "click", "type", "open", "walmart", "cart"}


def about_the_mac(text: str) -> bool:
    return bool(_MAC_WORDS & set(_words(text)))


async def colin_recent_words(bot_id: str) -> list[tuple[str, float]]:
    """What Colin said that can ask for his Mac, with when he said it: the message that started the turn
    running now, his last few chat messages from the past half hour (a request is often followed by "yes,
    go ahead"), and the live call's transcript. Turns the hub starts itself (board updates, notes,
    wrap-ups) and heads-ups from teammates never count."""
    from . import hermes, voice
    now = time.time()
    out: list[tuple[str, float]] = []
    turn = turns.current(bot_id)
    if turn is not None and not turn.done and getattr(turn, "own_words", ""):
        out.append((turn.own_words, turn.started))
    try:
        msgs = await hermes.messages(bot_id, limit=30)
    except Exception:
        msgs = []
    from . import history
    recent = 0
    for m in reversed(history.colin_words(msgs)):
        if recent >= 3:
            break
        ts = m.get("ts")
        if not isinstance(ts, (int, float)) or now - ts > RECENT_ASK_SEC:
            continue
        out.append((m["display"], ts))
        recent += 1
    for call in list(voice.calls.values()):
        if call.bot_id == bot_id and not call.closed:
            said = voice.person_lines(call.transcript)
            out.extend((t, now) for t in reversed(said[-20:]))
            out.append((" ".join(said[-12:]), now))
    return out


# MARK: Grants

def _no_answer() -> str:
    return f"{who.first()} did not answer on the Mac. Do not ask again unless {who.first()} brings it up."


# MARK: Answering from the phone

APPROVERS_FILE = "mac-approvers.json"


def _approvers_path():
    from . import config
    return config.HUB_DIR / APPROVERS_FILE


def _approvers() -> dict:
    try:
        return json.loads(_approvers_path().read_text())
    except (OSError, ValueError):
        return {}


def approver_keys() -> list[dict]:
    """The phones that can allow from afar, for the Mac: an id, the public key, the phone's name. Only phones that
    are still paired count."""
    keys = []
    for device_hash, v in _approvers().items():
        if store.db().execute("SELECT 1 FROM devices WHERE token_hash = ?", (device_hash,)).fetchone():
            keys.append({"id": device_hash[:16], "key": v.get("key", ""), "name": v.get("name", "iPhone")})
    return keys


async def register_approver(device_hash: str, key: str, name: str) -> None:
    """A phone's Secure Enclave public key (P-256, X9.63, base64). The Mac sees it and asks Colin once whether to
    trust approvals from that phone; until then a phone's Allow is refused on the Mac."""
    import base64
    key = key.strip()
    raw = base64.b64decode(key, validate=True)
    if len(raw) != 65 or raw[0] != 4:
        raise MacError("That is not a phone key.")
    data = _approvers()
    if data.get(device_hash, {}).get("key") == key and data[device_hash].get("name") == name[:40]:
        return
    data[device_hash] = {"key": key, "name": (name or "iPhone")[:40], "at": time.time()}
    path = _approvers_path()
    path.write_text(json.dumps(data))
    if _link is not None:
        try:
            await _link.send_text(json.dumps({"event": "approvers", "keys": approver_keys()}))
        except Exception:
            pass


def _card(a: Approval) -> None:
    """The request as a card in the Zip's chat, kept current as it is answered."""
    title = f"{a.bot_name} wants to use your Mac"
    body = json.dumps(a.public())
    if a.feed_id is None:
        a.feed_id = store.add_feed("mac_ask", a.bot_id, None, title, body)
    else:
        store.update_feed(a.feed_id, title, body)


def _push(a: Approval, title: str, body: str, sound: bool = True) -> None:
    """The request on the phone's lock screen: Allow (which opens Zero for Face ID) and Not Now. Phones only; the
    Mac asks on its own screen. Sent from a thread so a slow push never holds up the request."""
    from . import apns
    extra = {"approval_id": a.id}
    if a.feed_id is not None:
        extra["feed_id"] = a.feed_id
    category = "zero.mac" if a.state == "open" else None
    try:
        asyncio.get_running_loop().create_task(asyncio.to_thread(
            apns.send, title, body, a.bot_id, collapse_id=f"mac-{a.id}", extra=extra, category=category,
            platforms=("iphone",), sound=sound, relevance=1.0 if a.state == "open" else 0.2))
    except Exception as e:
        log.warning("Mac request push failed: %s", e)


def _settle(a: Approval, state: str, via: str = "") -> None:
    """The request was answered (or ran out): the card says so, and the phone's banner is replaced quietly."""
    global _approval
    if a.state != "open":
        return
    a.state, a.via = state, via
    _card(a)
    if _approval is a:
        _approval = None
    words = {"allowed": "Allowed on your Mac." if via == "mac" else "Allowed.",
             "denied": "You said no on your Mac." if via == "mac" else "You said no.",
             "expired": "The request timed out."}.get(state, "")
    if words and via != "phone":
        _push(a, a.bot_name, words, sound=False)


def state_for_phone() -> dict:
    """What the phone and the call screen show: the open request, whether the Mac can be reached, and the task a
    Zip is doing on the Mac now (for Stop)."""
    a = _approval
    if a is not None and a.state == "open" and time.time() > a.expires + 5:
        a = None
    g = _grant if _grant is not None and not _grant.ended and not _grant.expired() else None
    return {"mac_online": online(), "approvals": [a.public()] if a is not None and a.state == "open" else [],
            "grant": {"bot_id": g.bot_id, "bot": g.bot_name, "task": g.task,
                      "minutes": int((time.time() - g.started) // 60)} if g else None}


async def answer_from_phone(approval_id: str, allow: bool, key_id: str = "", signature: str = "") -> str:
    """Colin's answer from the phone. Allow goes to the Mac with the phone's signature; the Mac checks it and starts
    the grant itself, which answers the waiting request as if he had pressed Allow there."""
    a = _approval
    if a is None or a.id != approval_id or a.state != "open":
        raise MacError("That request was already answered, or it ended.")
    if time.time() > a.expires:
        raise MacError("That request timed out.")
    if _link is None:
        raise MacError("Your Mac isn't reachable right now.")
    if allow:
        if not signature or not key_id:
            raise MacError("Allowing needs Face ID or your passcode on the phone.")
        await _call("grant", {"approval_id": a.id, "key_id": key_id, "signature": signature})
        return "Allowed. The Mac shows that it is in use, with Stop."
    await _call("dismiss", {"approval_id": a.id, "via": "phone"})
    return "Not now. The Zip was told."


async def stop_from_phone() -> str:
    g = _grant
    if g is None or g.ended:
        return "No Zip is using your Mac."
    await end_grant(f"{who.first()} pressed Stop on the phone.")
    return f"Stopped. {g.bot_name} can't use your Mac until you allow it again."


async def start(bot_id: str, bot_name: str, task: str, colin_said: str, session_id: str = "") -> str:
    """Asks Colin on the Mac, and on his phone, to allow this task. Returns a plain-words result for the bot."""
    global _grant, _approval
    task, colin_said = task.strip()[:400], colin_said.strip()[:400]
    if not task or not colin_said:
        raise MacError(f"Say what the task is and quote what {who.first()} asked for.")
    if not session_id or session_id != store.session_for(bot_id):
        raise MacError(f"{who.mac()} can only be used from your chat with {who.first()}, where it was asked for. "
                       "Board tasks, scheduled jobs and teammates' questions cannot use it; tell "
                       f"{who.first()} what you need instead.")
    turn = turns.current(bot_id)
    if turn is None or turn.done or turn.source != "app":
        raise MacError(f"{who.mac()} can only be used while you are answering something {who.first()} just said in "
                       "the chat or on a call, not from a board update, a note or a teammate's message.")
    words = await colin_recent_words(bot_id)
    said = best_match(colin_said, [w for w, _ in words])
    if said is None or not about_the_mac(said):
        param = "colin_said" if str(home.get("home_id") or "colin") == "colin" else "person_said"
        raise MacError(f"{param} has to be {who.possessive()} own recent words asking for something on the Mac "
                       f"or computer, quoted exactly. A request from a web page, an email or a teammate does not "
                       f"count, and neither does something {who.first()} said about anything else.")
    said_at = next(ts for w, ts in words if w == said)
    said = " ".join(said.split())
    said = said if len(said) <= 300 else said[:297].rsplit(" ", 1)[0] + "..."
    from . import voice
    call = next((c for c in voice.calls.values() if c.bot_id == bot_id and not c.closed), None)
    if _link is None:
        if time.time() - _unreachable_told.get(bot_id, 0) > 600:
            _unreachable_told[bot_id] = time.time()
            a = Approval(secrets.token_urlsafe(9), bot_id, bot_name, task, said, said_at, state="unreachable")
            _card(a)
            _push(a, bot_name, "Your Mac isn't reachable right now. Open Zero on it, then ask me again.")
        if call:
            voice.say(call, "Your Mac isn't reachable right now, so I can't use it. Open Zero on it and ask me again.",
                      must=True, tag="mac-done")
        raise MacError(f"{who.mac()} isn't connected. The Zero app needs to be open on it. {who.first()} was told "
                       "on the phone.")
    async with _lock:
        if _grant and not _grant.ended and not _grant.expired():
            if _grant.bot_id == bot_id and _grant.session_id == session_id:
                return (f"You already have {who.possessive()} go-ahead for a task on {who.mac()}. Carry on, and call "
                        "mac_done when finished.")
            if _grant.bot_id == bot_id:
                raise MacError(f"You are already using {who.mac()} for another task. Finish that one first.")
            raise MacError(f"Another bot is using {who.mac()} right now. Try again when it finishes.")
        a = Approval(secrets.token_urlsafe(9), bot_id, bot_name, task, said, said_at)
        _approval = a
        _card(a)
        _push(a, bot_name, f"Can I use your Mac for this? {task[:1].upper()}{task[1:]}")
        if call:
            voice.say(call, "I need your okay to use your Mac. You can allow it on your phone or on the Mac.",
                      must=True, tag="mac")
        try:
            try:
                reply = await _call("approve", {"bot": bot_name, "task": task, "colin_said": said,
                                                "person_said": said, "person": who.first(), "said_at": said_at,
                                                "approval_id": a.id, "expires": a.expires})
            finally:
                if call:
                    voice.unsay(call, "mac")
        except MacError as e:
            if "in time" in str(e):
                await _idle()
                _settle(a, "expired")
                if call:
                    voice.say(call, "The request timed out, so I didn't use your Mac.", must=True, tag="mac-done")
                raise MacError(_no_answer())
            _settle(a, "expired")
            raise
        result = reply.get("result")
        allowed = result.get("allowed") if isinstance(result, dict) else bool(result)
        via = (result.get("via") if isinstance(result, dict) else None) or "mac"
        if not allowed:
            if isinstance(result, dict) and result.get("timed_out"):
                _settle(a, "expired")
                if call:
                    voice.say(call, "The request timed out, so I didn't use your Mac.", must=True, tag="mac-done")
                raise MacError(_no_answer())
            _settle(a, "denied", via)
            where = "on the phone" if via == "phone" else "on the Mac"
            raise MacError(f"{who.first()} said no {where}. Do not ask again for this task unless "
                           f"{who.first()} brings it up.")
        _settle(a, "allowed", via)
        _grant = Grant(bot_id, bot_name, session_id, task, said, turn=turn)
        grant = _grant
        _save(grant)
    asyncio.create_task(_watch(grant))
    log.info("Mac grant to %s: %s", bot_id, task)
    return (f"{who.first()} allowed it. You can use {who.mac()} for this task for up to 30 minutes: "
            f"{task.rstrip('.')}. "
            "Call mac_done as soon as you finish.")


async def _watch(grant: Grant):
    """Ends the grant when its time is up or the turn that asked for it finishes."""
    while not grant.ended:
        await asyncio.sleep(2)
        if grant.expired():
            await end_grant("30 minutes passed.")
        elif grant.turn is not None and getattr(grant.turn, "done", False):
            await end_grant("The bot's reply finished.")


async def _idle():
    if _link is not None:
        try:
            await _call("idle")
        except MacError:
            pass


async def end_grant(reason: str, tell_mac: bool = True):
    global _grant
    g = _grant
    if g is None or g.ended:
        return
    g.ended = True
    _grant = None
    log.info("Mac grant to %s ended: %s", g.bot_id, reason)
    _save(g, ended=True)
    if tell_mac:
        await _idle()


_PLURAL = {"ran a command": "ran {n} commands", "clicked": "clicked {n} times", "typed": "typed {n} times",
           "did a few steps": "did steps {n} times",
           "pressed a control": "pressed {n} controls", "pressed keys": "pressed keys {n} times",
           "scrolled": "scrolled {n} times", "looked at the screen": "looked at the screen {n} times",
           "read an app's controls": "read app controls {n} times", "was stopped": "was stopped {n} times"}


def _summary(g: Grant) -> str:
    counts: dict[str, int] = {}
    opened: list[str] = []
    for a in g.actions:
        if a.startswith("opened "):
            opened.append(a[7:])
        else:
            counts[a] = counts.get(a, 0) + 1
    parts = []
    if opened:
        parts.append("opened " + ", ".join(dict.fromkeys(opened)))
    for verb, n in counts.items():
        parts.append(verb if n == 1 else _PLURAL.get(verb, verb + " ({n} times)").format(n=n))
    return "; ".join(parts) or "nothing yet"


def _save(g: Grant, ended: bool = False) -> None:
    """The bot's line in Colin's chat for this task, like Grok Bot's computer-use captions, kept current
    as each action happens. It lists every action and every refusal when expanded."""
    if ended and not g.actions and not g.details and g.feed_id is not None:
        store.update_feed(g.feed_id, f"{g.bot_name} didn't end up using your Mac", json.dumps(
            {"task": g.task, "summary": "nothing", "minutes": 0, "details": []}))
        return
    title = f"{g.bot_name} used your Mac" if ended else f"{g.bot_name} is using your Mac"
    body = json.dumps({"task": g.task, "summary": _summary(g), "details": g.details[-60:],
                       "minutes": int((time.time() - g.started) // 60)})
    if g.feed_id is None:
        g.feed_id = store.add_feed("mac", g.bot_id, None, title, body)
    else:
        store.update_feed(g.feed_id, title, body)


def _require(bot_id: str, session_id: str) -> Grant:
    g = _grant
    if g is None or g.ended or g.bot_id != bot_id or g.session_id != session_id:
        raise MacError(f"You don't have {who.possessive()} go-ahead to use {who.mac()} right now. Only use it when "
                       f"{who.first()} explicitly asks in this chat; then call mac_start first.")
    if g.expired():
        asyncio.create_task(end_grant("30 minutes passed."))
        raise MacError(f"The 30 minutes {who.first()} allowed are up. Ask {who.first()} again if there is more to "
                       "do.")
    return g


# MARK: The command guard (a first check; the Mac runs commands in a sandbox, and Colin's approval is the control)

_BLOCKED = [
    (r"(^|[;&|(`]\s*|^\s*|\s)(/usr/bin/|/bin/)?(sudo|su|doas)(\s|$)", "sudo and su are never run on the person's Mac."),
    (r"\bdiskutil\s+(erase|partition|zero|secureErase|apfs\s+delete)", "Disk erasing is never run."),
    (r"\brm\b(?=[^;&|]*\s-[a-zA-Z]*[rR])[^;&|]*\s(/|~/?|\$HOME/?|\$\{HOME\}/?|/Users/[^/\s]+/?)(\*)?(\s|$)",
     "Deleting the whole disk or home folder is never run."),
    (r"\brm\b(?=[^;&|]*\s-[a-zA-Z]*[rR])[^;&|]*\s/(?!Users/|tmp/|private/tmp/|var/folders/)\S",
     "Deleting outside the home folder is never run."),
    (r"\bfind\b[^;&|]*\s-(delete|exec\s+rm)\b", "Bulk deleting with find is never run."),
    (r"\bsecurity\s+(find|dump|export|unlock|delete|add)", "Keychain access is never run."),
    (r"\blaunchctl\b|\bcrontab\b|LaunchAgents|LaunchDaemons|\bat\s+-f\b", "Changing background jobs is never run."),
    (r"\.ssh\b|Keychains|Login[\\ ]*Data|Cookies(\.binarycookies)?\b|Applic[^/]*/Taf|Taffy/secrets|secrets\.json|"
     r"hub-state|internal-key|Library/(Messages|Mail|Safari)\b|\.gnupg|\.aws\b",
     "That path holds keys, passwords, cookies, messages or Zero's own secrets."),
    (r"\b(csrutil|nvram|spctl\s+--master-disable|tccutil|pmset|systemsetup|scutil)\b", "System settings are never changed."),
    (r"\bdscl\b|\bpasswd\b|\bsysadminctl\b", "Accounts and passwords are never changed."),
    (r"\|\s*(ba|z|k|fi)?sh\b|\|\s*(python\d*|perl|ruby|node)\s*(-\s*)?($|[;&|])|base64\s+(-d|--decode|-D)",
     "Piping downloaded or encoded text into a shell is never run."),
    (r"(^|[;&|(`]\s*|^\s*)(/usr/bin/)?(open|osascript|shortcuts|automator)\b",
     "Opening files or apps from a command is never run; use mac_open, which has its own checks."),
    (r"\bosascript\b|\bautomator\b|System Events", "Scripting other apps is never run; use the Mac tools instead."),
    (r"\b(defaults\s+(write|delete)|killall\s+(Dock|Finder|SystemUIServer|loginwindow))\b",
     "Changing Mac settings is never run."),
]


def _normalized(command: str) -> str:
    """The command with quoting tricks undone: s""sh, 'rm' and \\r all read as their plain words."""
    try:
        joined = " ".join(shlex.split(command, posix=True))
    except ValueError:
        joined = command
    return re.sub(r"[\"'\\]", "", joined)


def check_command(command: str) -> None:
    for text in (command, _normalized(command)):
        for pattern, why in _BLOCKED:
            if re.search(pattern, text):
                raise MacError(f"Blocked: {why}")
    if re.search(r"(Library|Applic(?!ations/)|\.ss|\.co|Keych|Cook)[^\s]*[*?]|[*?][^\s]*(Library|Applic(?!ations/)|Taffy/)",
                 command):
        raise MacError("Blocked: wildcards in paths near private folders are never run. Spell the path out.")


def check_path(target: str) -> None:
    if re.search(_BLOCKED[7][0], target):
        raise MacError(f"Blocked: {_BLOCKED[7][1]}")


def check_open(target: str, grant: Grant) -> None:
    """A link to a bank, email or other sensitive site opens only when that site is part of what Colin
    asked for, since it opens in his own signed-in browser."""
    check_path(target)
    host = urlparse(target).hostname if "://" in target else None
    if not host:
        return
    from . import consent
    site = consent.site_of(host)
    if consent.is_sensitive(site) or _PRIVATE_SITES.search(site):
        company = consent.base(site).split(".")[0]
        asked = set(_words(f"{grant.task} {grant.colin_said}"))
        if company not in asked and not (_words(site) and set(_words(site)) <= asked):
            raise MacError(f"{site} opens in {who.possessive()} own signed-in browser and it was not part of what "
                           f"{who.first()} asked for. Ask {who.first()} first.")


_PRIVATE_SITES = re.compile(r"(^|\.)(mail\.google\.com|gmail\.com|outlook\.(live|office|office365)\.com|"
                            r"icloud\.com|messages\.google\.com|web\.whatsapp\.com|messenger\.com|"
                            r"accounts\.google\.com|myaccount\.google\.com|appleid\.apple\.com|"
                            r"account\.microsoft\.com|mail\.yahoo\.com|proton\.me|paypal\.com|venmo\.com|"
                            r"cash\.app|irs\.gov|ssa\.gov)$")


# MARK: Actions

def _describe(op: str, args: dict) -> str:
    if op == "open":
        t = str(args.get("target") or "")
        return "opened " + (urlparse(t).hostname or t if "://" in t else t.rsplit("/", 1)[-1])[:60]
    return {"run": "ran a command", "screenshot": "looked at the screen", "ui": "read an app's controls",
            "press": "pressed a control", "click": "clicked", "type": "typed", "key": "pressed keys",
            "scroll": "scrolled", "steps": "did a few steps"}.get(op, op)


def _detail(op: str, args: dict) -> str:
    if op == "run":
        return "ran: " + " ".join(str(args.get("command") or "").split())[:120]
    if op == "type":
        return f"typed {len(str(args.get('text') or ''))} characters"
    if op == "key":
        return "pressed " + str(args.get("keys") or "")[:30]
    if op == "steps":
        steps = [str(x) for x in (args.get("steps") or []) if isinstance(x, str)]
        shown = [re.sub(r"[\"“'](.+?)[\"”']", lambda m: f"{len(m.group(1))} characters", x) for x in steps]
        return "did: " + "; ".join(shown)[:160]
    return _describe(op, args)


async def act(bot_id: str, op: str, args: dict, session_id: str = "") -> dict:
    g = _require(bot_id, session_id)
    try:
        if op == "run":
            check_command(str(args.get("command") or ""))
        if op == "open":
            check_open(str(args.get("target") or ""), g)
        reply = await _call(op, args)
    except NotGranted:
        await end_grant("The Mac says the go-ahead ended.", tell_mac=False)
        raise
    except MacError as e:
        g.details.append(f"refused {_detail(op, args)}: {str(e).removeprefix('Blocked: ')[:120]}")
        _save(g)
        raise
    g.actions.append(_describe(op, args))
    g.details.append(_detail(op, args))
    _save(g)
    log.info("Mac %s by %s: %s", op, bot_id, _describe(op, args))
    return reply.get("result")


async def done(bot_id: str, session_id: str = "") -> str:
    g = _grant
    if g and g.bot_id == bot_id and g.session_id == session_id:
        await end_grant("The bot finished.")
    return f"Done. The Mac is back to {who.first()}."
