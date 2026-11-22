""


















import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config, home

log = logging.getLogger("taffy.usagesplit")

WEIGHTS = {"read": 0.1, "write": 2.0, "in": 1.0, "out": 5.0}
DAYS = 7
SOURCES = {"zero": "Zero", "mac": "Claude Code on your Mac", "server": "Coding on the home server"}


def _model_factor(model: str) -> float:
    m = (model or "").lower()
    return 0.2 if "haiku" in m else 0.6 if "sonnet" in m else 1.0


def weigh(model: str, read: int = 0, write: int = 0, uncached: int = 0, out: int = 0) -> float:
    return _model_factor(model) * (WEIGHTS["read"] * read + WEIGHTS["write"] * write + WEIGHTS["in"] * uncached
                                   + WEIGHTS["out"] * out)


def _days(now: float) -> set[str]:
    today = datetime.fromtimestamp(now)
    return {(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS)}


def _usage_dir() -> Path:
    return config.HUB_DIR / "usage"




def zero(now: float | None = None) -> dict[str, float]:
    ""
    now = time.time() if now is None else now
    out: dict[str, float] = {}
    for day in _days(now):
        try:
            lines = (_usage_dir() / f"{day}.jsonl").read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            model = str(r.get("model") or "").lower()

            if r.get("kind") != "usage" or not (model.startswith("claude") or model in ("opus", "sonnet", "haiku")):
                continue
            acct = r.get("account") or "main"
            out[acct] = out.get(acct, 0.0) + weigh(r["model"], r.get("read") or 0, r.get("write") or 0,
                                                   r.get("uncached") or 0, r.get("out") or 0)
    return out




def scan_logs(root: Path, since: float) -> dict[str, dict[str, dict[str, int]]]:
    ""

    days: dict[str, dict[str, dict[str, int]]] = {}
    try:
        files = [p for p in root.glob("*/*.jsonl") if p.stat().st_mtime >= since]
    except OSError:
        return days
    for f in files:
        seen: set[str] = set()
        try:
            fh = f.open(errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                m = r.get("message") or {}
                u = m.get("usage") or {}
                mid = m.get("id") or r.get("requestId") or ""
                if not u or not mid or mid in seen:
                    continue
                seen.add(mid)
                day = str(r.get("timestamp") or "")[:10]
                if not day:
                    continue
                t = days.setdefault(day, {}).setdefault(m.get("model") or "", {"read": 0, "write": 0, "in": 0, "out": 0})
                t["read"] += int(u.get("cache_read_input_tokens") or 0)
                t["write"] += int(u.get("cache_creation_input_tokens") or 0)
                t["in"] += int(u.get("input_tokens") or 0)
                t["out"] += int(u.get("output_tokens") or 0)
    return days


def _weigh_days(days: dict, now: float) -> float:
    keep = _days(now)
    return sum(weigh(model, t.get("read", 0), t.get("write", 0), t.get("in", 0), t.get("out", 0))
               for day, models in days.items() if day in keep for model, t in models.items())


def _mac_file() -> Path:
    return _usage_dir() / "claude-code-mac.json"


def save_mac(days: dict) -> None:
    ""
    clean = {}
    for day, models in list(days.items())[:14]:
        if not (isinstance(day, str) and len(day) == 10 and isinstance(models, dict)):
            continue
        clean[day] = {str(m)[:60]: {k: max(0, int((t or {}).get(k) or 0)) for k in ("read", "write", "in", "out")}
                      for m, t in list(models.items())[:20] if isinstance(t, dict)}
    _usage_dir().mkdir(parents=True, exist_ok=True)
    tmp = _mac_file().with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": time.time(), "days": clean}))
    tmp.replace(_mac_file())


def mac(now: float | None = None) -> float | None:
    ""
    now = time.time() if now is None else now
    try:
        data = json.loads(_mac_file().read_text())
    except (OSError, ValueError):
        return None
    return _weigh_days(data.get("days") or {}, now)


_server_cache: tuple[float, float] = (0.0, 0.0)


def server(now: float | None = None) -> float:
    ""
    global _server_cache
    now = time.time() if now is None else now
    if now - _server_cache[0] < 600:
        return _server_cache[1]
    root = home.USER_HOME / ".claude" / "projects"
    value = _weigh_days(scan_logs(root, now - (DAYS + 1) * 86400), now)
    _server_cache = (now, value)
    return value




def split(account_id: str, now: float | None = None) -> list[dict]:
    ""

    now = time.time() if now is None else now
    parts = {"zero": zero(now).get(account_id, 0.0)}
    if account_id == "main":
        parts["mac"] = mac(now) or 0.0
        parts["server"] = server(now)
    total = sum(parts.values())
    if total <= 0:
        return []
    out = [{"name": SOURCES[k], "share": round(100 * v / total)} for k, v in parts.items() if v > 0]
    return sorted(out, key=lambda p: -p["share"])
