"""Updates the "When to sign in" house rule in every Zip's SOUL.md for the Oct 9 change: a teammate's question or a
routine may sign in with the Zip's own saved login to read what it needs; money, health, government and
account-security sites still need Colin's tap.

Run on alien: ~/taffy/.venv/bin/python update-soul-signin-line.py   (add --dry-run to see what would change)
It edits only the "When to sign in" line in ~/.taffy/SOUL.md and ~/.taffy/profiles/*/SOUL.md, backs each changed
file up first (SOUL.md.bak-signin-<time>), and is safe to run again: a file already updated is left alone. The
team section between <!-- team:start --> and <!-- team:end --> is the hub's (team.py) and is never touched here.
Paths given on the command line are edited instead of the live files (used by the tests).
"""

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

OLD_START = "sign in to a site only when Colin's request needs it"
NEW_START = "sign in to a site when Colin's request needs it"
OLD_SENTENCE = "Scheduled work does not count as asking."
NEW_SENTENCE = ("A teammate's question or a routine is not Colin asking for sends, purchases or changes, but you may "
                "sign in with your own saved login to read what it needs. Money, health, government and "
                "account-security sites still need Colin's tap.")
MARKER = "When to sign in:"
TEAM_START, TEAM_END = "<!-- team:start -->", "<!-- team:end -->"


def _reader():
    """The home's settings reader (the plugin's home.py, deployed beside this script's folder), or None."""
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "plugins" / "taffy-tools" / "home.py"
    try:
        spec = importlib.util.spec_from_file_location("taffy_plugin_home_cli", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _p(text: str, soul: str = "") -> str:
    """Text written for Colin's SOULs, said for this home's person. A SOUL written for Colin keeps his wording, so
    his own SOULs read as before in any case."""
    reader = _reader()
    if reader is None or re.search(r"\bColin\b", soul or ""):
        return text
    return reader.personalize(text)


def _hermes_home() -> Path:
    reader = _reader()
    return reader.path("hermes_home") if reader else Path.home() / ".taffy"


def update_text(text: str) -> str:
    """The text with its "When to sign in" line updated; unchanged when it is already new or has no such line."""
    lines = text.split("\n")
    inside_team = False
    old_start, new_start, new_sentence = _p(OLD_START, text), _p(NEW_START, text), _p(NEW_SENTENCE, text)
    for i, line in enumerate(lines):
        if TEAM_START in line:
            inside_team = True
        if TEAM_END in line:
            inside_team = False
            continue
        if inside_team or MARKER not in line or OLD_SENTENCE not in line:
            continue
        lines[i] = line.replace(old_start, new_start, 1).replace(OLD_SENTENCE, new_sentence, 1)
    return "\n".join(lines)


def live_files() -> list[Path]:
    home = _hermes_home()
    return [p for p in [home / "SOUL.md", *sorted((home / "profiles").glob("*/SOUL.md"))] if p.is_file()]


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")] or live_files()
    stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d-%H%M%S")
    changed = 0
    for path in paths:
        text = path.read_text()
        new = update_text(text)
        if new == text:
            state = "already updated" if _p(NEW_SENTENCE, text) in text else "no sign-in line to update"
            print(f"{path}: {state}")
            continue
        changed += 1
        if dry:
            print(f"{path}: would update")
            continue
        backup = path.with_name(f"{path.name}.bak-signin-{stamp}")
        shutil.copy2(path, backup)
        path.write_text(new)
        print(f"{path}: updated (backup {backup.name})")
    print(f"{changed} file(s) {'would change' if dry else 'changed'}. New conversations pick it up; no restart needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
