#!/usr/bin/env python3
"""Adds the teammate-length line to Zero's and every specialist's SOUL.md, just before the "Units and spelling"
style line (Oct 9: a four-paragraph reply about contact lenses read like an LLM, not a teammate). Idempotent, with
a backup per file, and --dry-run. The hub's team section is not touched."""
import re
import shutil
import sys
import time
from pathlib import Path

LINE = ("- Length: write like a teammate texting, not a report. Most replies are two or three short sentences: what "
        "you did or found, then the next step or the one thing you need from him. Do not narrate your search or list "
        "what you could not find unless it changes the plan. No numbered lists unless he asks for steps or choices.")
MARKER, ANCHOR = "- Length: write like a teammate", "- Units and spelling:"


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
    if MARKER in text or ANCHOR not in text:
        return text
    i = text.index(ANCHOR)
    return text[:i] + _p(LINE, text) + "\n" + text[i:]


def main() -> None:
    dry = "--dry-run" in sys.argv
    home = _hermes_home()
    paths = [home / "SOUL.md", *sorted((home / "profiles").glob("*/SOUL.md"))]
    changed = 0
    for p in paths:
        old = p.read_text()
        new = update_text(old)
        if new == old:
            print(f"{p}: unchanged")
            continue
        changed += 1
        if dry:
            print(f"{p}: would add the length line (dry run)")
            continue
        backup = p.with_name(f"SOUL.md.bak-length-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(p, backup)
        p.write_text(new)
        print(f"{p}: added the length line (backup {backup.name})")
    print(f"{changed} file(s) {'would change' if dry else 'changed'}.")


if __name__ == "__main__":
    main()
