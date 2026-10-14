"""Notifications on the person's iPhone and Mac through Apple's push service (replaced Telegram on Oct 8).

The app sends its device token after pairing; tokens are tied to the pairing, so an unpaired device stops
getting them. The key is the team's APNs key in ~/.config/taffy-push (the file name carries the key id), the
same key Summer Goals uses. Debug builds (everything installed with install-iphone.sh) use Apple's sandbox.
"""

import logging
import threading
import time

import httpx
import jwt

from . import config, store

log = logging.getLogger("taffy.apns")

from . import home

# One team key signs pushes for the app's bundle id on every phone, so each home holds its own copy (home.json `push`).
KEY_DIR = home.expand(home.get("push.key_dir") or "~/.config/taffy-push")
TEAM_ID = str(home.get("push.team_id") or "YOUR_TEAM_ID")
TOPIC = str(home.get("push.topic") or "com.example.Taffy")
HOSTS = {"production": "https://api.push.apple.com", "development": "https://api.sandbox.push.apple.com"}

_lock = threading.Lock()
_clients: dict[str, httpx.Client] = {}
_bearer: tuple[str, float] | None = None


def _key() -> tuple[str, str] | None:
    """(key id, PEM) or None when no key is installed."""
    keys = sorted(KEY_DIR.glob("AuthKey_*.p8"))
    if not keys:
        return None
    return keys[0].stem.removeprefix("AuthKey_"), keys[0].read_text()


def _token() -> str | None:
    global _bearer
    # Apple accepts a provider token for up to an hour and refuses ones refreshed too often.
    if _bearer and time.time() - _bearer[1] < 40 * 60:
        return _bearer[0]
    key = _key()
    if not key:
        return None
    now = int(time.time())
    _bearer = (jwt.encode({"iss": TEAM_ID, "iat": now}, key[1], algorithm="ES256", headers={"kid": key[0]}), now)
    return _bearer[0]


def _client(env: str) -> httpx.Client:
    if env not in _clients:
        _clients[env] = httpx.Client(base_url=HOSTS[env], http2=True, timeout=15)
    return _clients[env]


def save_token(device_hash: str, apns_token: str, env: str, platform: str) -> None:
    with store._lock:
        store.db().execute("DELETE FROM push_tokens WHERE device_hash = ? AND platform = ?", (device_hash, platform))
        store.db().execute("INSERT OR REPLACE INTO push_tokens (apns_token, device_hash, env, platform, updated_at) "
                           "VALUES (?,?,?,?,?)", (apns_token, device_hash, env, platform, int(time.time())))
        store.db().commit()


def tokens() -> list:
    """Push tokens of devices that are still paired."""
    return store.db().execute("SELECT p.* FROM push_tokens p JOIN devices d ON d.token_hash = p.device_hash").fetchall()


def ready() -> bool:
    return bool(_key()) and bool(tokens())


def send(title: str, body: str, bot_id: str | None = None, collapse_id: str | None = None,
         extra: dict | None = None, category: str | None = None, platforms: tuple | None = None,
         sound: bool = True, relevance: float | None = None, actions: list | None = None) -> bool:
    """Sends to every paired device. True when at least one device took it.
    `title` is the sender, a Zip's name (notify.sender); `body` one sentence (notify.line), fitted here once more so
    nothing is ever cut off on the lock screen. The payload names the Zip (`sender`) for the app's Notification
    Service Extension, which shows it as a message from that Zip with its avatar (iOS Communication Notifications),
    and groups it under that Zip (thread-id).
    `collapse_id`: a proactive question's subject (notices.py), so an updated question or its result replaces the
    earlier banner on the lock screen instead of stacking a second one. `extra`: more keys for the app (feed_id).
    `category`: the app's notification buttons (Push.swift): "zero.question" (Yes, Not Now, Reply), "zero.open"
    (Open, Not Now), "zero.mac" (Allow, Not Now on a Zip's request to use the Mac), or "zero.msg.<zip>" (Reply to a
    Zip's message). `actions`: a card's real answers ({"id": "option:0", "title", "mode"}), which the extension
    registers as the card's own category ("zero.card.<feed_id>") before the notification shows; `category` stays the
    fallback if it can't.
    `platforms`: only these devices ("iphone", "mac"); the Mac asks on its own screen, so a Mac request goes to phones.
    `sound`: False for a quiet update that replaces an earlier banner (the request was answered somewhere else).
    `relevance`: how it ranks inside its Zip's group (notify.relevance)."""
    if _under_tests() and _token is _REAL_TOKEN:
        log.info("push not sent: running under tests (%s)", title[:60])
        return False
    with _lock:
        bearer = _token()
        if not bearer:
            return False
        payload = payload_for(title, body, bot_id, extra=extra, category=category, sound=sound, relevance=relevance,
                              actions=actions)
        headers = {"authorization": f"bearer {bearer}", "apns-topic": TOPIC, "apns-push-type": "alert",
                   "apns-priority": "10"}
        if collapse_id:
            headers["apns-collapse-id"] = collapse_id[:64]
        delivered = False
        for row in tokens():
            if platforms and row["platform"] not in platforms:
                continue
            try:
                r = _client(row["env"]).post(f"/3/device/{row['apns_token']}", json=payload, headers=headers)
            except httpx.HTTPError as e:
                log.warning("push to %s failed: %s", row["platform"], e)
                continue
            if r.status_code == 200:
                delivered = True
            elif r.status_code == 410 or (r.status_code == 400 and "BadDeviceToken" in r.text):
                with store._lock:
                    store.db().execute("DELETE FROM push_tokens WHERE apns_token = ?", (row["apns_token"],))
                    store.db().commit()
            else:
                log.warning("push to %s refused: %s %s", row["platform"], r.status_code, r.text[:200])
        return delivered


CATEGORIES = ("zero.question", "zero.open", "zero.mac")


def payload_for(title: str, body: str, bot_id: str | None = None, *, extra: dict | None = None,
                category: str | None = None, sound: bool = True, relevance: float | None = None,
                actions: list | None = None) -> dict:
    """The JSON Apple delivers. Separate from `send` so tests can check it without a device."""
    from . import notify
    sid, sname = notify.sender(bot_id)
    if bot_id and bot_id != sid:
        sid, sname = bot_id, title
    thread = sid
    payload = {"aps": {"alert": {"title": title, "body": notify.fit(body)}, "sound": "default", "thread-id": thread,
                       "mutable-content": 1}}
    if not sound:
        payload["aps"].pop("sound")
        payload["aps"]["interruption-level"] = "passive"
    else:
        from . import tones
        tone = tones.sound_for(sid)
        if tone:
            payload["aps"]["sound"] = tone
        else:
            payload["aps"].pop("sound")
    if relevance is not None:
        payload["aps"]["relevance-score"] = max(0.0, min(1.0, float(relevance)))
    if category in CATEGORIES or (category or "").startswith(("zero.msg.", "zero.card.")):
        payload["aps"]["category"] = category
    elif category is None:
        payload["aps"]["category"] = f"zero.msg.{sid}"
    payload["sender"] = {"id": sid, "name": sname}
    if bot_id:
        payload["bot_id"] = bot_id
    if actions:
        payload["actions"] = [{"id": str(a["id"]), "title": str(a["title"])[:40], "mode": a.get("mode") or "background"}
                              for a in actions[:4]]
    for k, v in (extra or {}).items():
        if k not in ("aps", "bot_id", "sender", "actions"):
            payload[k] = v
    return payload


def _under_tests() -> bool:
    """Running under the test suite (`python -m unittest`, the matrix, deploy.sh's test step)."""
    import os
    import sys
    return "unittest" in (sys.argv[0] if sys.argv else "") or os.environ.get("ZERO_TESTS") == "1"


_REAL_TOKEN = _token
