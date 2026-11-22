""






import logging
import threading
import time

import httpx
import jwt

from . import config, desk, store

log = logging.getLogger("taffy.apns")

from . import home


KEY_DIR = home.expand(home.get("push.key_dir") or "~/.config/taffy-push")
TEAM_ID = str(home.get("push.team_id") or "Q3Q2BF22GL")
TOPIC = str(home.get("push.topic") or "com.colinobrien.Taffy")
HOSTS = {"production": "https://api.push.apple.com", "development": "https://api.sandbox.push.apple.com"}

_lock = threading.Lock()
_clients: dict[str, httpx.Client] = {}
_bearer: tuple[str, float] | None = None


def _key() -> tuple[str, str] | None:
    ""
    keys = sorted(KEY_DIR.glob("AuthKey_*.p8"))
    if not keys:
        return None
    return keys[0].stem.removeprefix("AuthKey_"), keys[0].read_text()


def _token() -> str | None:
    global _bearer

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
    ""
    return store.db().execute("SELECT p.* FROM push_tokens p JOIN devices d ON d.token_hash = p.device_hash").fetchall()


def ready() -> bool:
    ""

    from . import icloud
    return (bool(_key()) and bool(tokens())) or icloud.ready()


def send(title: str, body: str, bot_id: str | None = None, collapse_id: str | None = None,
         extra: dict | None = None, category: str | None = None, platforms: tuple | None = None,
         sound: bool = True, relevance: float | None = None, actions: list | None = None,
         urgent: bool = False, urgent_sound: bool = False, hold: bool = True, subtitle: str = "",
         devices: tuple | None = None) -> bool:
    ""























    if _under_tests() and _token is _REAL_TOKEN:


        log.info("push not sent: running under tests (%s)", title[:60])
        return False
    with _lock:
        bearer = _token()
    if not bearer:


        from . import icloud
        return icloud.send(title, body, bot_id, collapse_id, subtitle) if icloud.ready() else False
    with _lock:
        payload = payload_for(title, body, bot_id, extra=extra, category=category, sound=sound, relevance=relevance,
                              actions=actions, urgent=urgent,
                              urgent_sound=urgent_sound, subtitle=subtitle)
        headers = {"authorization": f"bearer {bearer}", "apns-topic": TOPIC, "apns-push-type": "alert",
                   "apns-priority": "10"}
        if collapse_id:
            headers["apns-collapse-id"] = collapse_id[:64]
        rows = [r for r in tokens()
                if (not platforms or r["platform"] in platforms) and (not devices or r["device_hash"] in devices)]
        later = []
        if hold and sound and not urgent and not urgent_sound:
            rows, later = desk.route(rows)
        delivered = _post(rows, payload, headers)
        if later and not delivered:
            rows, later = later, []
            delivered = _post(rows, payload, headers)
    if later:
        log.info("push from %s went to the %s; the others get it in %d s if unseen", bot_id or "default",
                 "/".join(sorted({r["platform"] for r in rows})), desk.WAIT_SEC)
        desk.hold(bot_id or "default", collapse_id, {
            "title": title, "body": body, "bot_id": bot_id, "collapse_id": collapse_id, "extra": extra,
            "category": category, "sound": sound, "relevance": relevance, "actions": actions, "subtitle": subtitle,
            "devices": sorted({r["device_hash"] for r in later})})
    elif not devices:
        desk.replaced(collapse_id)
    if delivered:
        _alerted[bot_id or "default"] = time.time()
    return delivered






_alerted: dict[str, float] = {}


def clear(chat: str) -> None:
    ""
    if not _alerted.pop(chat, None) or (_under_tests() and _token is _REAL_TOKEN):
        return
    threading.Thread(target=_send_clear, args=(chat,), daemon=True).start()


def _send_clear(chat: str) -> None:
    with _lock:
        bearer = _token()
        if not bearer:
            return
        headers = {"authorization": f"bearer {bearer}", "apns-topic": TOPIC, "apns-push-type": "background",
                   "apns-priority": "5"}
        _post(tokens(), clear_payload(chat), headers)


def withdraw_task(task_id: str) -> None:
    ""

    if not task_id or (_under_tests() and _token is _REAL_TOKEN):
        return
    threading.Thread(target=_send_withdraw, args=(task_id,), daemon=True).start()


def _send_withdraw(task_id: str) -> None:
    with _lock:
        bearer = _token()
        if not bearer:
            return
        headers = {"authorization": f"bearer {bearer}", "apns-topic": TOPIC, "apns-push-type": "background",
                   "apns-priority": "5"}
        _post(tokens(), withdraw_payload(task_id), headers)


def withdraw_payload(task_id: str) -> dict:
    ""
    return {"aps": {"content-available": 1}, "clear_task": task_id}


def clear_payload(chat: str) -> dict:
    ""

    return {"aps": {"content-available": 1}, "clear_chat": chat}


def for_device(row, payload: dict) -> dict:
    ""


    from . import looking, tones
    if row["platform"] != "mac" or payload.get("aps", {}).get("sound") != tones.NOTIFY:
        return payload
    front, chat = looking.shown(row["device_hash"])
    sound = tones.mac_sound((payload.get("sender") or {}).get("id"), front, chat)
    return payload if sound == tones.NOTIFY else {**payload, "aps": {**payload["aps"], "sound": sound}}


LAST_ANSWER = "apns_last"


def _answered(code: int) -> None:
    store.set_setting(LAST_ANSWER, {"at": time.time(), "ok": code == 200, "code": code})


def last_answer() -> dict:
    return store.get_setting(LAST_ANSWER, {}) or {}


def _post(rows: list, payload: dict, headers: dict) -> bool:
    ""
    delivered = False
    for row in rows:
        try:
            r = _client(row["env"]).post(f"/3/device/{row['apns_token']}", json=for_device(row, payload),
                                         headers=headers)
        except httpx.HTTPError as e:
            log.warning("push to %s failed: %s", row["platform"], e)
            continue
        _answered(r.status_code)
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
                actions: list | None = None, urgent: bool = False, urgent_sound: bool = False,
                subtitle: str = "") -> dict:
    ""
    from . import notify, tones
    sid, sname = notify.sender(bot_id)
    if bot_id and bot_id != sid:
        sid, sname = bot_id, title
    thread = sid
    payload = {"aps": {"alert": {"title": title, "body": notify.fit(body)}, "sound": "default", "thread-id": thread,
                       "mutable-content": 1}}
    if subtitle:
        payload["aps"]["alert"]["subtitle"] = subtitle
    if not sound:
        payload["aps"].pop("sound")
        payload["aps"]["interruption-level"] = "passive"
    else:
        tone = tones.sound_for(sid)
        if tone:
            payload["aps"]["sound"] = tone
        else:
            payload["aps"].pop("sound")
    if (urgent or urgent_sound) and sound:
        payload["aps"]["sound"] = tones.URGENT
    if urgent and sound:
        payload["aps"]["interruption-level"] = "time-sensitive"
    if relevance is not None:
        payload["aps"]["relevance-score"] = max(0.0, min(1.0, float(relevance)))
    if category in CATEGORIES or (category or "").startswith(("zero.msg.", "zero.card.")):
        payload["aps"]["category"] = category
    elif category is None:
        payload["aps"]["category"] = f"zero.msg.{sid}"
    payload["sender"] = {"id": sid, "name": sname}
    if bot_id:
        payload["bot_id"] = bot_id




        payload["aps"]["content-available"] = 1
    if actions:
        payload["actions"] = [{"id": str(a["id"]), "title": str(a["title"])[:40], "mode": a.get("mode") or "background"}
                              for a in actions[:4]]
    for k, v in (extra or {}).items():
        if k not in ("aps", "bot_id", "sender", "actions"):
            payload[k] = v
    return payload


def _under_tests() -> bool:
    ""
    import os
    import sys
    return "unittest" in (sys.argv[0] if sys.argv else "") or os.environ.get("ZERO_TESTS") == "1"


_REAL_TOKEN = _token
