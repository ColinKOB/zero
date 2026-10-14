"""Swaps the Oct 9 "Quiet while working" rule for Grok Bot's way of working, which Colin asked for on Oct 10: a short
line beside each step he would notice ("Opening eLearning now."), each shown as its own small message, and a final
reply that stays on what he asked, a point per paragraph, ending with choices for the next step.

Colin, Oct 10: "see how it doesnt output a whole bunch of text at the end, but instead gives small messages like a
teamate would? its just not the same as an interation like this on my phone where Zero gives me a whole bunch of
essentially unrelated (to logging in) info about periferals."

Run on alien: ~/taffy/.venv/bin/python update-soul-teammate-lines.py   (add --dry-run to see what would change)
It edits ~/.taffy/SOUL.md and ~/.taffy/profiles/*/SOUL.md, backs each changed file up first
(SOUL.md.bak-teammate-<time>), and is safe to run again. The team section between <!-- team:start --> and
<!-- team:end --> is the hub's (team.py) and is never touched. Paths given on the command line are edited instead of
the live files (used by the tests).
"""

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STEPS = ("- Show your steps: when a request takes a step, start with \"Will do\" or \"On it\", then put one short line "
         "in the same message as each step he would notice, such as \"Opening eLearning now.\" Each shows as its own "
         "message. End with \"Done.\" and what you set, never what you left alone. Never send \"still working\", "
         "\".\" or \"ok\".")
OLD_STEPS = ("- Show your steps: like a teammate texting as they go, put one short line in the same message as each step "
             "he would notice, such as \"Opening eLearning now.\" Each shows as its own message. Skip quick look-ups; "
             "never send \"still working\", \".\" or \"ok\".")
STAY = ("- Stay on what he asked: answer his request and nothing else, and offer other news as a choice only if it is "
        "urgent. One short point per paragraph (each shows as its own message). End with `offer_choices` when there "
        "are next steps.")
EDITS = [
    (" Do not narrate your search or list what you could not find unless it changes the plan.", ""),
    (" You replaced his ChatGPT dot on 2026-10-07 and took over its job; until 2026-10-08 you were called Taffy, and "
     "the app is now called Zero.", " Until 2026-10-08 you were called Taffy."),
    ("Questions you raise on your own: when Colin turns them on, the app notices",
     "Questions you raise on your own: the app notices"),
]
STEPS_MARKER, STAY_MARKER = "- Show your steps:", "- Stay on what he asked:"
QUIET = "- Quiet while working:"
LENGTH = "- Length:"
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


def _p(text: str, soul: str) -> str:
    """Text written for Colin's SOULs, said for this home's person; a SOUL that names Colin keeps his wording."""
    reader = _reader()
    if reader is None or re.search(r"\bColin\b", soul or ""):
        return text
    return reader.personalize(text)


def update_text(text: str) -> str:
    """The text with the step and stay-on-topic rules; unchanged when they are there or nothing fits."""
    if _p(OLD_STEPS, text) in text:
        text = text.replace(_p(OLD_STEPS, text), _p(STEPS, text))
    if STEPS_MARKER in text and STAY_MARKER in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    inside_team = False
    placed_steps = STEPS_MARKER in text
    placed_stay = STAY_MARKER in text
    for line in lines:
        if TEAM_START in line:
            inside_team = True
        if TEAM_END in line:
            inside_team = False
        if inside_team:
            out.append(line)
            continue
        if line.startswith(QUIET) and not placed_steps:
            out.append(_p(STEPS, text))
            placed_steps = True
            continue
        if line.startswith(LENGTH):
            out.append(line)
            if not placed_stay:
                out.append(_p(STAY, text))
                placed_stay = True
            continue
        out.append(line)
    if not placed_steps or not placed_stay:
        return text
    new = "\n".join(out)
    for before, after in EDITS:
        new = new.replace(_p(before, text), _p(after, text))
    return new


def _hermes_home() -> Path:
    reader = _reader()
    return reader.path("hermes_home") if reader else Path.home() / ".taffy"


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
            print(f"{path}: {'already updated' if STEPS_MARKER in text else 'no Oct 9 lines to replace'}")
            continue
        changed += 1
        if dry:
            print(f"{path}: would update (dry run)")
            continue
        backup = path.with_name(f"{path.name}.bak-teammate-{stamp}")
        shutil.copy2(path, backup)
        path.write_text(new)
        print(f"{path}: updated (backup {backup.name})")
    print(f"{changed} file(s) {'would change' if dry else 'changed'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
