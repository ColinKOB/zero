""










import hashlib
import math
import time

AWAY_SEC = 15
IDLE_SEC = 120
IDLE_FRESH_SEC = 60
_seen: dict[str, float] = {}
_input: dict[str, float] = {}
_front: dict[str, tuple[float, bool, str | None]] = {}


def key(authorization: str | None) -> str | None:
    ""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return hashlib.sha256(authorization[7:].encode()).hexdigest()


def saw(device: str | None, idle: float | None = None) -> None:
    ""

    if device:
        now = _seen[device] = time.time()
        if idle is not None and math.isfinite(idle) and idle >= 0:
            _input[device] = now - idle


def showing(device: str | None, front: bool | None, chat: str | None) -> None:
    ""

    if device:
        _front[device] = (time.time(), bool(front), (chat or "").strip()[:80] or None)


def shown(device: str | None) -> tuple[bool, str | None]:
    ""

    at_, front, chat = _front.get(device or "", (0.0, False, None))
    if time.time() - at_ > AWAY_SEC or not front:
        return False, None
    return True, chat


def at(device: str | None) -> bool:
    ""


    return not away(device) and time.time() - _input.get(device, 0) <= IDLE_SEC


def idle(device: str | None) -> float | None:
    ""
    if not device or device not in _input or time.time() - _seen.get(device, 0) > IDLE_FRESH_SEC:
        return None
    return time.time() - _input[device]


def away(device: str | None) -> bool:
    ""
    if not device:
        return False
    return time.time() - _seen.get(device, 0) > AWAY_SEC
