""


































from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from . import config, home, notify

log = logging.getLogger("taffy.icloud")

RECORD_TYPE = "ZeroNotice"
STATE = config.HUB_DIR / "icloud.json"
OUTBOX = config.HUB_DIR / "icloud-outbox.json"
OUTBOX_MAX = 30
OUTBOX_MAX_AGE_SEC = 24 * 3600
RETRY_FIRST_SEC, RETRY_MAX_SEC = 30, 15 * 60
_lock = threading.Lock()


_generation = 0
_sender: asyncio.Task | None = None
_retry: asyncio.TimerHandle | None = None
_backoff = RETRY_FIRST_SEC


def cancel_writes() -> None:
    global _generation
    with _lock:
        _generation += 1


def container() -> str:
    ""
    from . import apns
    return str(home.get("push.icloud.container") or f"iCloud.{apns.TOPIC}")


def switched_on() -> bool:
    return home.get("host.kind") == "mac" and home.get("push.icloud.enabled") is not False


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def can_save() -> bool:
    ""
    state = _read(STATE, {})
    return isinstance(state, dict) and state.get("can_save") is True


def set_can_save(flag: bool) -> None:
    ""
    _write(STATE, {"can_save": bool(flag), "at": int(time.time())})


def ready() -> bool:
    return switched_on() and can_save()


def record(title: str, body: str, bot_id: str | None, collapse_id: str | None, subtitle: str) -> dict:
    ""


    fields = {"id": uuid.uuid4().hex, "title": title[:120], "body": notify.fit(body), "bot": bot_id or "default", "at": int(time.time())}
    if subtitle:
        fields["subtitle"] = subtitle[:120]
    if collapse_id:
        fields["collapse"] = collapse_id[:64]
    return fields


def outbox() -> list[dict]:
    ""
    items = _read(OUTBOX, [])
    cutoff = time.time() - OUTBOX_MAX_AGE_SEC
    return [i for i in items if isinstance(i, dict) and i.get("id") and i.get("at", 0) >= cutoff] \
        if isinstance(items, list) else []


def _queue(fields: dict) -> None:
    with _lock:
        _write(OUTBOX, (outbox() + [fields])[-OUTBOX_MAX:])


def _remove(notice_id: str, generation: int) -> None:
    with _lock:
        if generation == _generation:
            _write(OUTBOX, [i for i in outbox() if i.get("id") != notice_id])


def _linked() -> bool:
    from . import mac
    return mac.online()


async def _hand_over(payload: dict) -> dict:
    from . import mac
    return await mac._call("notice_save", payload)


async def _save(fields: dict) -> bool:
    ""
    if _under_tests() and _hand_over is _REAL_HAND_OVER:
        log.info("iCloud notice not handed over: running under tests (%s)", str(fields.get("title"))[:60])
        return False
    if not _linked():
        return False
    try:
        reply = await _hand_over({"record_type": RECORD_TYPE, "container": container(), "fields": fields})
    except Exception as e:
        log.warning("iCloud notice not saved by the Mac app: %s", type(e).__name__)
        return False
    return bool(reply.get("ok"))


def _loop() -> asyncio.AbstractEventLoop | None:
    from . import signin
    return signin.loop


def send(title: str, body: str, bot_id: str | None = None, collapse_id: str | None = None, subtitle: str = "") -> bool:
    ""


    if not ready():
        return False
    _queue(record(title, body, bot_id, collapse_id, subtitle))
    loop = _loop()
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(_start)
    return True


def _start() -> asyncio.Task:
    ""
    global _sender, _retry
    if _retry is not None:
        _retry.cancel()
        _retry = None
    if _sender is None or _sender.done():
        _sender = asyncio.get_running_loop().create_task(_drain())
    return _sender


async def flush() -> int:
    ""
    return await _start()


async def _drain() -> int:
    global _retry, _backoff
    with _lock:
        generation = _generation
    sent = 0
    while ready():
        if generation != _generation:

            _retry = asyncio.get_running_loop().call_later(RETRY_FIRST_SEC, _start)
            break
        items = outbox()
        if not items:
            break
        fields = items[0]
        if not await _save(fields):
            if _linked() and generation == _generation:
                _retry = asyncio.get_running_loop().call_later(_backoff, _start)
                _backoff = min(_backoff * 2, RETRY_MAX_SEC)
            break
        _backoff = RETRY_FIRST_SEC
        _remove(fields["id"], generation)
        sent += 1
    return sent


def _under_tests() -> bool:
    import sys
    return "unittest" in (sys.argv[0] if sys.argv else "") or os.environ.get("ZERO_TESTS") == "1"


_REAL_HAND_OVER = _hand_over
