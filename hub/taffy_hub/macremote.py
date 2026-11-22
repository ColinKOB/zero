""


























import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass

from fastapi import WebSocket

from . import mac, who

log = logging.getLogger("taffy.macremote")

RECONNECT_GRACE_SEC = 10
TEXT_MAX = 2000
PASSWORD_MAX = 256
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
KEY_NAME = re.compile(r"[A-Za-z0-9]{1,16}")
MODIFIERS = {"cmd", "command", "shift", "opt", "option", "alt", "ctrl", "control"}
PERSON_HOLDER = "__person__"


@dataclass(eq=False)
class Session:
    id: str
    device: str
    started: float
    locked: bool = False
    phone: WebSocket | None = None
    frame: bytes | None = None
    sending: bool = False
    pending: bool = False
    ended: bool = False


_session: Session | None = None


def active() -> bool:
    return _session is not None and not _session.ended


def holder():
    ""

    if not active():
        return None
    return mac.Grant(PERSON_HOLDER, who.first(), "", "", mac.Background(mac.BACKGROUND))


def is_person(grant) -> bool:
    return grant is not None and getattr(grant, "bot_id", "") == PERSON_HOLDER


def packet(seq: int, w: int, h: int, locked: bool, jpeg: bytes) -> bytes:
    ""
    header = json.dumps({"seq": int(seq), "w": int(w), "h": int(h), "locked": bool(locked)})
    return header.encode() + b"\n" + jpeg


def clean(msg) -> dict | None:
    ""

    if not isinstance(msg, dict):
        return None
    kind = msg.get("type")

    def unit(k):
        v = msg.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 else None

    def number(k, bound):
        v = msg.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
            return None
        return max(-bound, min(bound, float(v)))

    if kind == "click":
        x, y = unit("x"), unit("y")
        if x is None or y is None:
            return None
        count = msg.get("count", 1)
        count = count if isinstance(count, int) and not isinstance(count, bool) else 1
        return {"type": "click", "x": x, "y": y, "button": "right" if msg.get("button") == "right" else "left",
                "count": max(1, min(3, count))}
    if kind == "drag":
        x, y = unit("x"), unit("y")
        if x is None or y is None or msg.get("phase") not in ("down", "move", "up"):
            return None
        return {"type": "drag", "phase": msg["phase"], "x": x, "y": y}
    if kind == "scroll":
        x, y, dx, dy = unit("x"), unit("y"), number("dx", 2000), number("dy", 2000)
        if None in (x, y, dx, dy):
            return None
        return {"type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy}
    if kind in ("text", "unlock"):
        text = msg.get("text")
        if not isinstance(text, str) or not text or len(text) > (TEXT_MAX if kind == "text" else PASSWORD_MAX):
            return None
        return {"type": kind, "text": text}
    if kind == "key":
        key, mods = msg.get("key"), msg.get("modifiers") or []
        if not isinstance(key, str) or not KEY_NAME.fullmatch(key) or not isinstance(mods, list) \
                or not all(isinstance(m, str) and m.lower() in MODIFIERS for m in mods) or len(mods) > 4:
            return None
        return {"type": "key", "key": key, "modifiers": [m.lower() for m in mods]}
    if kind == "view":
        w = msg.get("width")
        if not isinstance(w, int) or isinstance(w, bool) or not 1 <= w <= 20000:
            return None
        return {"type": "view", "width": w}
    if kind == "touch":
        return {"type": "touch"}
    if kind == "unlock_saved":
        return {"type": "unlock_saved"}
    return None




class RemoteError(Exception):
    ""


async def start(device: str, session_id: str, key_id: str, signature: str, at: int, url: str, kind: str,
                width: int) -> dict:
    global _session
    if not SESSION_ID.fullmatch(session_id or ""):
        raise RemoteError("Open your Mac again.")
    if not mac.online():
        raise RemoteError("Your Mac isn't reachable right now. Open Zero on it, then try again.")
    old = _session
    if old is not None and not old.ended:
        await stop(old, "replaced")
    s = Session(session_id, device, time.time())
    _session = s
    try:
        reply = await mac._call("remote_start", {"session": session_id, "key_id": key_id, "signature": signature,
                                                 "at": int(at), "url": url, "device": kind,
                                                 "width": max(1, min(20000, int(width)))})
    except mac.MacError as e:
        if _session is s:
            _session = None
        raise RemoteError(str(e))
    result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
    s.locked = bool(result.get("locked"))
    _lane_changed()


    asyncio.create_task(_end_unless_back(s))
    log.info("The person opened the Mac from a %s", "iPad" if kind == "iPad" else "phone")
    return {"session": session_id, "locked": s.locked, "width": int(result.get("width") or 0),
            "height": int(result.get("height") or 0), "saved": bool(result.get("saved"))}


async def stop(s: Session | None = None, reason: str = "closed", tell_mac: bool = True) -> None:
    ""

    global _session
    s = s or _session
    if s is None or s.ended:
        return
    s.ended = True
    s.frame = None
    if _session is s:
        _session = None
    if tell_mac and mac.online():
        try:
            await mac._call("remote_stop", {"session": s.id})
        except mac.MacError:
            pass
    phone, s.phone = s.phone, None
    if phone is not None:
        try:
            await phone.send_text(json.dumps({"type": "ended", "reason": reason}))
            await phone.close(code=4000)
        except Exception:
            pass
    _lane_changed()
    log.info("The person's view of the Mac ended (%s)", reason)


def _lane_changed() -> None:
    ""

    mac._bar_shown = None


def link_lost() -> None:
    ""
    if active():
        asyncio.create_task(stop(_session, "mac_offline", tell_mac=False))




def from_mac(msg: dict) -> bool:
    ""
    event = msg.get("event")
    if event not in ("remote_frame", "remote_state", "remote_ended"):
        return False
    s = _session
    if s is None or s.ended or msg.get("session") != s.id:
        return True
    if event == "remote_ended":
        asyncio.create_task(stop(s, "stopped_at_mac", tell_mac=False))
        return True
    if event == "remote_state":
        s.locked = bool(msg.get("locked"))
        if s.locked:
            s.frame = None
        _send_text(s, {"type": "state", "locked": s.locked})
        return True
    try:
        jpeg = base64.b64decode(msg.get("jpeg") or "", validate=True)
    except (ValueError, TypeError):
        return True
    if len(jpeg) < 3:
        return True
    s.frame = packet(msg.get("seq") or 0, msg.get("w") or 0, msg.get("h") or 0, bool(msg.get("locked")), jpeg)
    _push(s)
    return True


def _send_text(s: Session, obj: dict) -> None:
    phone = s.phone
    if phone is None:
        return

    async def go():
        try:
            await phone.send_text(json.dumps(obj))
        except Exception:
            pass
    asyncio.create_task(go())


def _push(s: Session) -> None:
    ""

    if s.phone is None:
        return
    if s.sending:
        s.pending = True
        return
    s.sending = True
    asyncio.create_task(_drain(s))


async def _drain(s: Session) -> None:
    try:
        while s.phone is not None and not s.ended and s.frame is not None:
            s.pending = False
            await s.phone.send_bytes(s.frame)
            if not s.pending:
                break
    except Exception:
        pass
    finally:
        s.sending = False




async def serve_phone(ws: WebSocket, device: str, session_id: str) -> None:
    ""
    s = _session
    if s is None or s.ended or s.id != session_id or s.device != device:
        await ws.close(code=4404, reason="no such view")
        return
    old, s.phone = s.phone, ws
    if old is not None:
        try:
            await old.close(code=4000, reason="replaced")
        except Exception:
            pass
    await ws.send_text(json.dumps({"type": "state", "locked": s.locked}))
    if s.frame is not None:
        _push(s)
    closed_on_purpose = False
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "stop":
                closed_on_purpose = True
                await stop(s, "closed")
                return
            event = clean(msg)
            if event is None:
                await ws.send_text(json.dumps({"type": "error", "text": "That didn't reach your Mac."}))
                continue
            if s.ended:
                return
            try:
                await mac._call("remote_input", {"session": s.id, **event})
                if event["type"] in ("unlock", "unlock_saved"):
                    await ws.send_text(json.dumps({"type": "unlocked"}))
            except mac.MacError as e:

                await ws.send_text(json.dumps({"type": "error", "text": str(e),
                                               **({"for": "unlock"} if event["type"] in ("unlock", "unlock_saved")
                                                  else {})}))
    except Exception:
        pass
    finally:
        if s.phone is ws:
            s.phone = None
            if not closed_on_purpose and not s.ended:
                asyncio.create_task(_end_unless_back(s))


async def _end_unless_back(s: Session) -> None:
    ""
    await asyncio.sleep(RECONNECT_GRACE_SEC)
    if s.phone is None and not s.ended:
        await stop(s, "dropped")


def owns(device: str, session_id: str) -> bool:
    return _session is not None and not _session.ended and _session.id == session_id and _session.device == device
