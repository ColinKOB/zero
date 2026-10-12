"""Whether the person still has Zero open on the device they sent a message from.

The app checks the feed every 4 seconds while it is on screen (10 in Low Power Mode) and stops when it goes to the
background (AppState.startPolling, stopPolling). A device that has not checked in for AWAY_SEC has been put down, so
a reply to its message goes out as a notification (turns._run). Colin, Oct 11: he asked School something, left the
app, and the answer arrived without a notification; iMessage and ChatGPT would have sent one.

Memory only: after a hub restart every device counts as away until its next check-in, which is at most one extra
notification. The app hides a banner for the chat on screen (Push.swift willPresent), so a wrong guess costs little.
"""

import hashlib
import time

AWAY_SEC = 15
_seen: dict[str, float] = {}


def key(authorization: str | None) -> str | None:
    """The device, from its bearer token (hashed, like store.devices)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return hashlib.sha256(authorization[7:].encode()).hexdigest()


def saw(device: str | None) -> None:
    """The device checked the feed just now."""
    if device:
        _seen[device] = time.time()


def away(device: str | None) -> bool:
    """True when the device has not checked in for AWAY_SEC; False for no device (a call, a hub turn)."""
    if not device:
        return False
    return time.time() - _seen.get(device, 0) > AWAY_SEC
