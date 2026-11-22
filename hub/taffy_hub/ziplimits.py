""










from __future__ import annotations

import logging
import re
from pathlib import Path

from . import config

log = logging.getLogger("taffy.ziplimits")

NEAR = 0.95
SOUL_LIMIT = 30_000
NOTEBOOK_LIMIT = 2_200
PROFILE_LIMIT = 1_375
FILES = (("instructions", "SOUL.md", "context_file_max_chars", SOUL_LIMIT),
         ("notebook", "memories/MEMORY.md", "memory_char_limit", NOTEBOOK_LIMIT),
         ("profile", "memories/USER.md", "user_char_limit", PROFILE_LIMIT))

_refused: set[tuple[str, int]] = set()


def home_of(bot_id: str) -> Path:
    return config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id


def _setting(home: Path, key: str, default: int) -> int:
    ""
    try:
        found = re.search(rf"^\s*{key}:\s*(\d+)\s*$", (home / "config.yaml").read_text(), re.M)
    except (OSError, UnicodeDecodeError):
        return default
    return int(found.group(1)) if found and int(found.group(1)) > 0 else default


def soul_limit(path: Path) -> int:
    return _setting(path.parent, "context_file_max_chars", SOUL_LIMIT)


def write_soul(path: Path, new: str, old: str = "") -> bool:
    ""

    limit = soul_limit(path)
    if len(new) > limit and len(new) >= len(old):
        mark = (str(path), len(new))
        if mark not in _refused:
            _refused.add(mark)
            log.error("refused to write %s: %d characters is past its limit of %d", path, len(new), limit)
        return False
    path.write_text(new)
    return True


def measure(bot_id: str) -> list[dict]:
    ""
    home = home_of(bot_id)
    out = []
    for kind, name, key, default in FILES:
        try:
            size = len((home / name).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        out.append({"kind": kind, "size": size, "limit": _setting(home, key, default)})
    return out


def state(size: int, limit: int) -> str:
    ""
    return "over" if size > limit else "near" if size >= limit * NEAR else "ok"
