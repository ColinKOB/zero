""







import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from . import config, home

LOG = Path("logs") / "compactions.jsonl"
RECENT = 20


def _homes() -> list[tuple[str, Path]]:
    profiles = config.HERMES_HOME / "profiles"
    others = sorted(p for p in profiles.iterdir() if p.is_dir()) if profiles.is_dir() else []
    return [("default", config.HERMES_HOME)] + [(p.name, p) for p in others]


def _entries(path: Path, since: float) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("at"), (int, float)) and entry["at"] >= since:
            out.append(entry)
    return out


def summary(days: int = 3, now: float | None = None) -> dict:
    ""
    zone = home.zone()
    now = datetime.now(zone) if now is None else datetime.fromtimestamp(now, zone)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    zips = []
    for bot_id, zip_home in _homes():
        entries = _entries(zip_home / LOG, start.timestamp())
        if not entries:
            continue
        per_day = Counter(datetime.fromtimestamp(e["at"], zone).strftime("%Y-%m-%d") for e in entries)
        zips.append({"bot_id": bot_id, "total": len(entries), "per_day": dict(sorted(per_day.items())),
                     "recent": entries[-RECENT:][::-1]})
    return {"since": start.isoformat(), "zips": zips}
