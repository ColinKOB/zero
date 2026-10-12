"""Saved calls, so Colin and the lead Claude session can read a call back and tune the voice.

Every call gets one file, ~/taffy/hub-state/calls/<YYYY-MM-DD>/<call id>.jsonl, written as the call happens:
one JSON object per line, appended and flushed line by line, so a crash loses at most the line being written.
Each line has `t` (America/New_York, with offset), `s` (seconds since the call started), `event` and `from`
("hub" or "app"). The app adds its own events for the same call (CallKit or not, audio routes, interruptions,
its timings) through /api/voice/{call_id}/diagnostics.

Events the hub writes: start, greeting (with waited_ms and late when the phone said how long the person waited),
line (who and text), colin_started, colin_stopped, interrupted, first_reply (milliseconds from the end of Colin's
speech to the voice's first sound), tool, job_started, job_result, job_no_result, news, cue (a soft sound sent to
the phone: started, working or done, with the task's number), missed_turn (a line that got no answer, and how it was
recovered), work_status, goodbye (the phone was told to end the call), hold, error, closed_by_voice, end
(duration), summary, other_event. A call in the On-Device
Voice (ondevice.py) has `engine` "on-device" on its start line, and adds `reply` (one per turn: how soon the
first words came, whether Claude was started ahead, any hand-off); its first_reply comes from the phone.

Saving is on by default; the "Save Call Transcripts" switch in Settings turns it off, and then nothing is
written at all. Files older than 30 days are deleted by a daily pass. The nightly backup skips this folder,
so the 30 days hold there too. Read them on alien with ~/taffy/bin/zero-calls.
"""

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

ET = home.TZ   # the home's time zone, followed live
SETTING = "save_call_transcripts"
KEEP_DAYS = 30
MAX_BYTES = 2 * 1024 * 1024        # one runaway call must not fill the disk; end and summary still go in
TEXT_LIMIT = 4000                  # a transcript line
SHORT = 300                        # tool requests and results, news
APP_EVENTS_PER_POST = 100
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_APP_ID = re.compile(r"app-[A-Za-z0-9-]{8,40}")
_EVENT = re.compile(r"[a-z][a-z0-9_]{0,39}")
_lock = threading.Lock()
_open: dict[str, tuple[Path, float]] = {}   # call id: (file, start time), for calls this hub run has seen


def calls_dir() -> Path:
    return config.HUB_DIR / "calls"


def enabled() -> bool:
    return bool(store.get_setting(SETTING, True))


def set_enabled(on: bool) -> None:
    store.set_setting(SETTING, bool(on))


def stamp(at: float | None = None) -> str:
    """2026-10-08T21:14:03.120-04:00"""
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
    """Opens the call's file with its start line. Nothing happens when saving is off."""
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
    """Adds one line from the hub to a call that has a file. Never raises: saving a call must not break it."""
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
    """The app's own events for a call. `app-…` ids are calls that never reached the hub (the app made the
    id); they get a file of their own. Returns how many lines were saved."""
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
    """Deletes the days older than `keep_days`. Returns how many days were removed."""
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
    """The daily pass: old calls go."""
    while True:
        try:
            n = await asyncio.to_thread(prune)
            if n:
                log.info("deleted %d day(s) of saved calls older than %d days", n, KEEP_DAYS)
        except Exception:
            log.warning("pruning saved calls failed", exc_info=True)
        await asyncio.sleep(24 * 3600)
