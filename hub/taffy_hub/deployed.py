""






import json
import re
import time
from pathlib import Path

from . import config

PARTS = ("hub", "plugin", "gateway")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


def path() -> Path:
    return config.HUB_DIR / "deployed.json"


def read(file: Path | None = None) -> dict:
    ""
    try:
        data = json.loads((file or path()).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def short_commit(file: Path | None = None) -> str:
    ""
    commit = str(read(file).get("commit") or "").strip()
    if not _SHA.match(commit):
        return ""
    return commit[:12].lower()


def stamp_json(commit: str, subject: str, parts: list[str], dirty: bool, at: float | None = None) -> str:
    ""
    data = {
        "commit": str(commit or "").strip(),
        "subject": " ".join(str(subject or "").split())[:140],
        "at": time.time() if at is None else float(at),
        "parts": [p for p in parts if p in PARTS] or ["hub"],
        "dirty": bool(dirty),
    }
    return json.dumps(data)


def _when(at) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(at)))
    except (TypeError, ValueError, OverflowError):
        return ""


def _same(deployed: str, main: str) -> bool:
    a, b = str(deployed or "").strip().lower(), str(main or "").strip().lower()
    if not a or not b:
        return False
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


def summarize(stamp: dict | None, main: str, mention_main: bool = True) -> str:
    ""
    main = str(main or "").strip()
    if not stamp:
        lines = ["No deploy stamp is on this home yet. The build log is the record until the next deploy."]
        if mention_main and main:
            lines.append(f"Main is {main[:12]}.")
        return "\n".join(lines)
    commit = str(stamp.get("commit") or "").strip()
    subject = " ".join(str(stamp.get("subject") or "").split())
    when = _when(stamp.get("at"))
    shown = commit[:12] if commit else "unknown"
    line = f"Deployed commit {shown}"
    if subject:
        line += f" ({subject})"
    if when:
        line += f" at {when}"
    parts = [p for p in (stamp.get("parts") or []) if isinstance(p, str)]
    lines = [line + "."]
    if parts:
        lines.append("Updated: " + ", ".join(parts) + ".")
    if stamp.get("dirty"):
        lines.append("That deploy included edits that were not committed.")
    if mention_main:
        if main:
            lines.append(f"Main is {main[:12]}.")
            lines.append("This home is on main." if _same(commit, main) else "This home is behind main.")
        else:
            lines.append("Main could not be read from this checkout.")
    return "\n".join(lines)
