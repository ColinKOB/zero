"""Adds the Oct 9 conversation rules to every Zip's SOUL.md (docs/qa/2026-10-09-conversation-qa.md): US units and
American spelling, say a reminder once, no file paths in chat, no internals in anything Colin reads or hears, do
look-ups without asking, and a one-line tell when a Zip starts something on its own.

Run on alien: ~/taffy/.venv/bin/python update-soul-style-lines.py   (add --dry-run to see what would change)
It inserts the lines after the style line in ~/.taffy/SOUL.md and ~/.taffy/profiles/*/SOUL.md, backs each changed
file up first (SOUL.md.bak-style-<time>), and is safe to run again: a file that already has the lines is left
alone. The team section between <!-- team:start --> and <!-- team:end --> is the hub's (team.py) and is never
touched here. Paths given on the command line are edited instead of the live files (used by the tests).
"""

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STYLE_LINES = [
    "- Units and spelling: American spelling, and Colin's units: Fahrenheit, miles, feet and inches, pounds, "
    "dollars, 12-hour times.",
    "- Say a reminder once. Do not tack a deadline or a reminder onto later replies unless he asks or it changed.",
    "- Files: never paste a file path in a reply. Send pictures with `share_screen`; for anything else, say what "
    "the file is and that it is in your work folder, and offer to show it. Attach files to board tasks with "
    "`artifacts`.",
    "- Internals stay out of chat and calls: never say Hermes, alien, the hub, Claude, Codex, connectors, lanes, "
    "tiers, the judge, sessions or tool names. If he asks what you run on, say his home server. A failed "
    "connection is \"I couldn't reach your Gmail just now\", never the name of a part.",
    "- Just do it: reading, looking things up, checking a site you can sign in to, drafting, and preparing a cart "
    "need no permission and no question. Do not ask \"want me to check?\" when you can check. Never say you "
    "can't before checking your tools; if a sign-in is all that is missing, ask for it with the card.",
    "- Starting on your own: when you begin something Colin did not just ask for (a routine's follow-up, a check "
    "you decided was needed), your first line says what you are doing and why, in one plain sentence.",
]
MARKER = "- Units and spelling:"
ANCHORS = ("- Style for anything Colin reads:", "- Time zone is America/New_York.")
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
    """The text with the style lines added after the anchor; unchanged when they are there or there is no anchor."""
    if MARKER in text:
        return text
    anchors = tuple(_p(a, text) for a in ANCHORS)
    lines = text.split("\n")
    inside_team = False
    for i, line in enumerate(lines):
        if TEAM_START in line:
            inside_team = True
        if TEAM_END in line:
            inside_team = False
            continue
        if inside_team or not line.startswith(anchors):
            continue
        return "\n".join(lines[:i + 1] + [_style_line(s, text) for s in STYLE_LINES] + lines[i + 1:])
    return text


def _style_line(line: str, soul: str) -> str:
    """A style line for this home: its units and its person (Colin's SOULs keep his wording)."""
    reader = _reader()
    if reader and line.startswith(MARKER) and not re.search(r"\bColin\b", soul):
        return f"{MARKER} " + reader.units_line()
    return _p(line, soul)


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
            state = "already updated" if MARKER in text else "no style line to add after"
            print(f"{path}: {state}")
            continue
        changed += 1
        if dry:
            print(f"{path}: would add {len(STYLE_LINES)} lines (dry run)")
            continue
        backup = path.with_name(f"{path.name}.bak-style-{stamp}")
        shutil.copy2(path, backup)
        path.write_text(new)
        print(f"{path}: added {len(STYLE_LINES)} lines (backup {backup.name})")
    print(f"{changed} file(s) {'would change' if dry else 'changed'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
