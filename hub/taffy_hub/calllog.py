""





















import asyncio
import json
import logging
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from . import config, home, store

log = logging.getLogger("taffy.calllog")

ET = home.TZ
SETTING = "save_call_transcripts"
KEEP_DAYS = 30
MAX_BYTES = 2 * 1024 * 1024
TEXT_LIMIT = 4000
SHORT = 300
APP_EVENTS_PER_POST = 100
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_APP_ID = re.compile(r"app-[A-Za-z0-9-]{8,40}")
_EVENT = re.compile(r"[a-z][a-z0-9_]{0,39}")
_lock = threading.Lock()
_open: dict[str, tuple[Path, float]] = {}


def calls_dir() -> Path:
    return config.HUB_DIR / "calls"


def enabled() -> bool:
    return bool(store.get_setting(SETTING, True))


def set_enabled(on: bool) -> None:
    store.set_setting(SETTING, bool(on))


def stamp(at: float | None = None) -> str:
    ""
    return datetime.fromtimestamp(at if at is not None else time.time(), ET).isoformat(timespec="milliseconds")


def short(text, limit: int = SHORT) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def valid_id(call_id: str) -> bool:
    return bool(call_id) and bool(_ID.fullmatch(call_id))


def _find(call_id: str) -> tuple[Path, float] | None:
    known = _open.get(call_id)
    if known:
        return known
    root = calls_dir()
    if not root.is_dir():
        return None
    for day in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        path = day / f"{call_id}.jsonl"
        if path.is_file():
            started = path.stat().st_mtime
            try:
                with path.open() as f:
                    first = json.loads(f.readline() or "{}")
                started = datetime.fromisoformat(first["t"]).timestamp()
            except (OSError, ValueError, KeyError, TypeError):
                pass
            _remember(call_id, path, started)
            return path, started
    return None


def _remember(call_id: str, path: Path, started: float) -> None:
    _open[call_id] = (path, started)
    while len(_open) > 200:
        _open.pop(next(iter(_open)))


def _append(path: Path, record: dict, force: bool = False) -> bool:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not force and path.exists() and path.stat().st_size > MAX_BYTES:
                return False
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
            return True
        except OSError:
            log.warning("could not save a call line", exc_info=True)
            return False


def begin(call_id: str, at: float | None = None, **fields) -> bool:
    ""
    try:
        if not valid_id(call_id) or not enabled():
            return False
        at = time.time() if at is None else at
        path = calls_dir() / datetime.fromtimestamp(at, ET).strftime("%Y-%m-%d") / f"{call_id}.jsonl"
        _remember(call_id, path, at)
        return _append(path, {"t": stamp(at), "s": 0.0, "event": "start", "from": "hub", "call": call_id, **fields})
    except Exception:
        log.warning("could not start saving call %s", call_id, exc_info=True)
        return False


def write(call_id: str, event: str, at: float | None = None, **fields) -> bool:
    ""
    return _write(call_id, event, at, "hub", fields)


def _write(call_id: str, event: str, at: float | None, source: str, fields: dict) -> bool:
    try:
        if not valid_id(call_id) or not enabled():
            return False
        found = _find(call_id)
        if not found:
            return False
        path, started = found
        at = time.time() if at is None else at
        record = {"t": stamp(at), "s": round(at - started, 2), "event": event, "from": source}
        record.update({k: v for k, v in fields.items() if v is not None})
        return _append(path, record, force=event in ("end", "summary"))
    except Exception:
        log.warning("could not save a call line", exc_info=True)
        return False


def _clean_value(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v if v == v and abs(v) < 1e15 else None
    return short(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False), 500)


def app_events(call_id: str, events: list) -> int:
    ""

    if not valid_id(call_id) or not enabled():
        return 0
    now = time.time()
    if not _find(call_id):
        if not _APP_ID.fullmatch(call_id):
            return 0
        first = min((e.get("at") for e in events if isinstance(e, dict) and isinstance(e.get("at"), (int, float))
                     and abs(e["at"] - now) < 86400), default=now)
        if not begin(call_id, at=first, note="This call never reached the hub; only the app's events are here."):
            return 0
    saved = 0
    for e in events[:APP_EVENTS_PER_POST]:
        if not isinstance(e, dict):
            continue
        name = str(e.get("event") or "")
        if not _EVENT.fullmatch(name):
            continue
        at = e.get("at")
        at = at if isinstance(at, (int, float)) and abs(at - now) < 86400 else now
        fields = {}
        for k, v in list(e.items())[:16]:
            if k in ("event", "at", "t", "s", "from") or not isinstance(k, str) or not _EVENT.fullmatch(k):
                continue
            fields[k] = _clean_value(v)
        if _write(call_id, name, at, "app", fields):
            saved += 1
    return saved


def prune(today: date | None = None, keep_days: int = KEEP_DAYS) -> int:
    ""
    root = calls_dir()
    if not root.is_dir():
        return 0
    today = today or datetime.now(ET).date()
    cutoff = today - timedelta(days=keep_days)
    removed = 0
    for day in root.iterdir():
        try:
            when = date.fromisoformat(day.name)
        except ValueError:
            continue
        if day.is_dir() and when < cutoff:
            shutil.rmtree(day, ignore_errors=True)
            removed += 1
    return removed


async def run():
    ""
    while True:
        try:
            n = await asyncio.to_thread(prune)
            if n:
                log.info("deleted %d day(s) of saved calls older than %d days", n, KEEP_DAYS)
        except Exception:
            log.warning("pruning saved calls failed", exc_info=True)
        await asyncio.sleep(24 * 3600)
