""


































import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import config, home, usage

BUCKETS = ("read", "write", "uncached", "out")
PLANS = ("claude", "chatgpt", "other")
DIRECT = {"chat", "team", "board", "work", "make", "api-server", "approval", "side", "side-question",
          "voice", "voice-launch", "voice-handoff",
          "voice-relay", "voice-realtime", "voice-ondevice", "hub-firstline", "hub-shortreply", "hub-voice"}


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _stamp(record: dict) -> float | None:
    stamp = _number(record.get("ts"))
    if stamp is not None:
        return stamp
    return _date(record.get("at"))


def _date(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _plan(record: dict) -> str:
    provider = str(record.get("provider") or "").lower()
    model = str(record.get("model") or "").lower()
    origin = str(record.get("origin") or "").replace("_", "-")
    if origin == "voice-realtime":
        return "other"
    if provider in {"openai-codex", "codex"} or (
            provider == "openai" and (record.get("scope") == "turn" or (
                record.get("scope") == "execution" and origin.startswith("connector-")))):
        return "chatgpt"
    if provider in {"anthropic", "hub", "claude-subscription-directsdk-experimental"}:
        return "claude"
    if not provider and (model.startswith("claude-") or model in {"opus", "sonnet", "haiku"}):
        return "claude"
    return "other"


@dataclass(frozen=True)
class Work:
    id: str
    parent: str
    bot: str
    origin: str


def _work(record: dict) -> Work:
    return Work(str(record.get("errand_id") or record.get("turn_id") or ""),
                str(record.get("parent_errand_id") or ""),
                str(record.get("zip") or record.get("bot_id") or "").split("#", 1)[0],
                str(record.get("origin") or "").replace("_", "-"))


@dataclass(frozen=True)
class Call:
    ts: float
    work: Work
    plan: str
    counts: tuple[int, ...]
    available: bool
    record_id: str = ""


@dataclass
class Day:
    inode: tuple[int, int] = (0, 0)
    offset: int = 0
    mtime: int = 0
    calls: list[Call] = field(default_factory=list)
    readings: list[dict] = field(default_factory=list)
    errands: list[tuple[float, Work]] = field(default_factory=list)


def _owners(calls: list[Call], errands: list[Work]) -> dict[Work, str]:
    nodes = {c.work.id: c.work for c in calls if c.work.id}
    nodes.update((work.id, work) for work in errands if work.id)
    resolved = {}
    for call in calls:
        work, path, seen = call.work, [], set()
        while work not in resolved:
            if work in seen:
                row = "background"
                break
            seen.add(work)
            path.append(work)
            canonical = nodes.get(work.id, work)
            if canonical != work:
                work = canonical
            elif work.parent:
                if work.parent not in nodes:
                    row = "background"
                    break
                work = nodes[work.parent]
            else:
                row = work.bot if work.bot and (work.origin in DIRECT or
                                                work.origin.startswith("connector-")) else "background"
                break
        else:
            row = resolved[work]
        resolved.update((work, row) for work in path)
    return resolved


class Journal:
    def __init__(self):
        self._lock = threading.Lock()
        self._folder: Path | None = None
        self._days: dict[str, Day] = {}

    def _read(self, path: Path, day: Day) -> Day:
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            inode = (stat.st_dev, stat.st_ino)
            if day.inode != inode or stat.st_size < day.offset or (
                    stat.st_size == day.offset and stat.st_mtime_ns != day.mtime):
                day = Day(inode=inode)
            stream.seek(day.offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                day.offset = stream.tell()
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(record, dict) or (ts := _stamp(record)) is None:
                    continue
                if record.get("kind") == "plan":
                    provider = str(record.get("provider") or "claude").lower()
                    plan = {"anthropic": "claude", "openai": "chatgpt", "openai-codex": "chatgpt"}.get(provider, provider)
                    if plan in {"claude", "chatgpt"}:
                        day.readings.append({"id": plan, "ts": ts, **{k: record.get(k) for k in (
                            "weekly", "weekly_resets", "five_hour", "five_hour_resets")}})
                elif record.get("kind") == "errand":
                    day.errands.append((ts, _work(record)))
                elif record.get("kind") in (None, "usage") and record.get("accounting", True):
                    counts = tuple(max(0, int(n)) if (n := _number(record.get(k))) is not None else 0 for k in BUCKETS)
                    day.calls.append(Call(ts, _work(record), _plan(record), counts,
                                          record.get("usage_available") is not False,
                                          str(record.get("record_id") or "")))
            day.mtime = stat.st_mtime_ns
        return day

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        today = datetime.fromtimestamp(now, home.TZ).date()
        dates = [today - timedelta(days=i) for i in range(29, -1, -1)]
        folder = usage.usage_dir()
        with self._lock:
            if self._folder != folder:
                self._folder, self._days = folder, {}
            wanted = {day.isoformat() for day in dates}
            self._days = {key: value for key, value in self._days.items() if key in wanted}
            incomplete = False
            for date in dates:
                key = date.isoformat()
                try:
                    self._days[key] = self._read(folder / f"{key}.jsonl", self._days.get(key, Day()))
                except FileNotFoundError:
                    self._days.pop(key, None)
                except OSError:
                    incomplete = True
                    self._days.pop(key, None)
            calls, readings, errands, seen = [], [], [], set()
            for key in sorted(self._days):
                day = self._days[key]
                readings.extend(r for r in day.readings if r["ts"] <= now)
                errands.extend(work for ts, work in day.errands if ts <= now)
                for call in day.calls:
                    if call.ts > now or (call.record_id and call.record_id in seen):
                        continue
                    if call.record_id:
                        seen.add(call.record_id)
                    calls.append(call)
            owners = _owners(calls, errands)
            periods = {}
            for key, days in (("today", 1), ("week", 7), ("month", 30)):
                start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), home.TZ).timestamp()
                periods[key] = _period(calls, owners, start, now, incomplete)
            stamps = [c.ts for c in calls] + [r["ts"] for r in readings]
            return {"generated_at": now, "timezone": home.timezone_name(), "periods": periods,
                    "recorded_since": min(stamps, default=None),
                    "plans": [_picture(plan, readings, calls, now) for plan in ("claude", "chatgpt")]}


def _counts() -> dict:
    return {"calls": 0, "unmeasured_calls": 0, "tokens": 0, **dict.fromkeys(BUCKETS, 0)}


def _period(calls: list[Call], owners: dict[Work, str], start: float, now: float, incomplete: bool) -> dict:
    names = {b.id: b.name for b in config.BOTS}
    names.setdefault("default", "Zero")
    names["background"] = "Behind the scenes"
    rows = {key: {"id": key, "name": name, "kind": "background" if key == "background" else "zip",
                  "plans": {plan: _counts() for plan in PLANS}} for key, name in names.items()}
    totals = {plan: _counts() for plan in PLANS}
    for call in calls:
        if not start <= call.ts <= now:
            continue
        row = owners[call.work]
        if row not in rows:
            rows[row] = {"id": row, "name": "Former Zip", "kind": "zip",
                         "plans": {plan: _counts() for plan in PLANS}}
        for into in (rows[row]["plans"][call.plan], totals[call.plan]):
            into["calls"] += 1
            into["unmeasured_calls"] += not call.available
            for bucket, count in zip(BUCKETS, call.counts):
                into[bucket] += count
                into["tokens"] += count
    total = sum(p["tokens"] for p in totals.values())
    for row in rows.values():
        row["tokens"] = sum(p["tokens"] for p in row["plans"].values())
        row["share_percent"] = round(row["tokens"] * 100 / total, 1) if total else 0
    return {"start": start, "end": now, "totals": totals, "tokens": total, "incomplete": incomplete,
            "rows": sorted(rows.values(), key=lambda r: (-r["tokens"], r["name"], r["id"]))}


def _picture(plan: str, readings: list[dict], calls: list[Call], now: float) -> dict:
    latest = max((r for r in readings if r["id"] == plan), key=lambda r: r["ts"], default=None)
    windows = []
    if latest:
        for key, seconds in (("five_hour", 5 * 3600), ("weekly", 7 * 86400)):
            used = _number(latest.get(key))
            reset = _date(latest.get(key + "_resets"))
            if used is None or not 0 <= used <= 100 or reset is None:
                continue
            windows.append({"id": key, "used_percent": used, "resets_at": reset,
                            "expired": reset <= now,
                            "zero_tokens": sum(sum(c.counts) for c in calls if c.plan == plan and
                                               reset - seconds <= c.ts <= latest["ts"]),
                            "zero_share_percent": None})
    return {"id": plan, "name": "Claude" if plan == "claude" else "ChatGPT",
            "available": bool(windows), "updated_at": latest["ts"] if latest else None,
            "stale": latest is None or now - latest["ts"] > 2 * usage.PLAN_EVERY,
            "windows": windows, "estimate_method": "shared_plan_unidentifiable",
            "estimate_reason": "This plan also includes your own Claude Code use." if plan == "claude" else
                               "This plan also includes your own ChatGPT and Codex use."}


journal = Journal()


def snapshot() -> dict:
    return journal.snapshot()
