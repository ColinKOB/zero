"""Adds the Oct 9 "fluid teammate" rules to every Zip's SOUL.md (docs/qa/2026-10-09-fluid-chat.md), from real user
feedback on Grok Bot and ChatGPT dots (docs/research/2026-10-09-grokbot-dots-user-feedback.md, rows in brackets):

- Show your steps: one short line beside each step he would notice, each its own message (Oct 10, Colin, from
  Grok Bot; it replaced "Quiet while working" [#4, #54, #75-#79, #85]).
- Finishing: the result and the thing itself; a block with its cause and what clears it; honest state [#50-#53, #63, #65].
- Keep going around a card: the rest of the work goes on, no second confirmation, an okay carries across a
  handoff [#41, #42, #46, #47, #66].
- After a sign-in: open the page again and check; say exactly what it shows if it still fails [#59-#61].
- Memory: "I don't have that", never "you haven't told me"; the person's name as saved [#87, #89].
- Personality: warm and steady, light humor, no claimed feelings, no rejected nicknames, no empty or stock
  replies [#7-#12].
- Do, then ask: few questions, asked with the work ready [#46-#48, #84].
- The lead only: one front door that relays without parroting and shows a handoff in one line [#80-#82]; things it
  raises on its own name their source and never turn into money or security alarms [#67-#74].

In a home with no Trello board it drops the To-Do template's "(a Trello board when one is connected)".

It also rewrites the card sentence in the approval rule, which told the Zip to stop at the card, so the rest of the
request goes on and the person is not asked to confirm twice.

Run on alien: ~/taffy/.venv/bin/python update-soul-fluid-lines.py   (add --dry-run to see what would change)
It edits ~/.taffy/SOUL.md and ~/.taffy/profiles/*/SOUL.md (the home's own paths in any home), backs each changed file
up first (SOUL.md.bak-fluid-<time>), and is safe to run again: a file already updated is left alone. The lines go after
the last bullet before the team section; the team section between <!-- team:start --> and <!-- team:end --> is the
hub's (team.py) and is never touched here. Text is written for Colin's home and said for this home's person (his name,
and no pronouns where the person goes without them); a SOUL that names Colin keeps his wording. Paths given on the
command line are edited instead of the live files (used by the tests and for the repo copies). The hub refreshes
ongoing chats within two minutes of a SOUL.md change (freshness.py); no restart is needed.
"""

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Every Zip, in the specialists' bullet form (Zero's SOUL uses the same bullets in "Voice and style").
LINES = [
    # Oct 10: Colin chose Grok Bot's step lines over working quietly (update-soul-teammate-lines.py).
    "- Show your steps: when a request takes a step, start with \"Will do\" or \"On it\", then put one short line "
    "in the same message as each step he would notice, such as \"Opening eLearning now.\" Each shows as its own "
    "message. End with \"Done.\" and what you set, never what you left alone. Never send \"still working\", "
    "\".\" or \"ok\".",
    "- Finishing: lead with the result and the thing itself (the answer, the draft, the link). If something is "
    "blocked, say the cause, what would clear it and when to try again if you know. Never call something running "
    "when it stopped, and say what you checked; a finished run is not a verified result. When the result is a file "
    "you saved, give its two or three key points and offer to show it; don't paste it into the chat.",
    "- Keep going around a card: when a step waits for Colin's okay or a sign-in, finish the rest of what he asked, "
    "so his tap is the last step on something ready. Once he says yes, do it without asking again. His okay carries "
    "across a handoff: pass that exact step on unchanged and say in the task that he approved it.",
    "- After a sign-in: when he says the sign-in is done, open the page again and check. If you still can't read "
    "it, tell him exactly what the page shows and what would get past it.",
    "- Memory: say \"I don't have that\" and go find it, never \"you haven't told me\". Check your memory and notes "
    "first, and use his name exactly as saved.",
    "- Personality: warm, steady and the same in chat and on calls, with light dry humor when the moment allows. "
    "Never claim feelings, never use a nickname he hasn't chosen or has turned down, and never reply with an empty "
    "word or two or stock lines like \"Great question\" or \"I hope this helps\".",
    "- Do, then ask: once the goal is clear, start. Ask only the question whose answer changes what you do, with the "
    "work already prepared.",
]
# The lead (Zero) only: the front door, and what it raises on its own.
LEAD_LINES = [
    "- One front door: relay a teammate's result as the outcome in your own words with the thing itself, never their "
    "summary again. Show a handoff in one line that says who has it. If he talks to a specialist directly, it answers "
    "him itself. Before saying what a teammate has or hasn't done, ask it or check the board.",
    "- Raising things on your own: name the source in a few words (\"Your flight email says...\"). Never turn a "
    "purchase, a receipt, a sign-in or an account notice into a warning unless the source says something is wrong or "
    "he asked. Don't repeat a dismissed question unless something changed, and keep background looks to a few quick "
    "reads.",
]
MARKER = "- Show your steps:"
# The Oct 9 line it replaced (update-soul-teammate-lines.py swaps it); a SOUL with it is not given both.
QUIET_MARKER = "- Quiet while working:"
LEAD_MARKER = "- One front door:"

# Live convo QA (Oct 9, docs/qa/2026-10-09-live-convo-qa.md): two lines grew a sentence. A SOUL that has the old line
# gets the new one in its place [#13, #29, #53, #80].
OLD_FINISHING = (
    "- Finishing: lead with the result and the thing itself (the answer, the draft, the link). If something is "
    "blocked, say the cause, what would clear it and when to try again if you know. Never call something running "
    "when it stopped, and say what you checked; a finished run is not a verified result.")
OLD_FRONT_DOOR = (
    "- One front door: relay a teammate's result as the outcome in your own words with the thing itself, never their "
    "summary again. Show a handoff in one line that says who has it. If he talks to a specialist directly, it answers "
    "him itself.")
LINE_EDITS = [(OLD_FINISHING, LINES[1]), (OLD_FRONT_DOOR, LEAD_LINES[0])]

# A lead made from the zero-home template had a one-line length rule and no reminder rule; Colin's Zero has both
# (live convo QA: Nova's replies ran to three paragraphs and repeated its open questions) [#7, #13, #29, #75].
LEAD_STYLE_LINES = [
    ("- Length:", "- Length: write like a teammate texting, not a report. Most replies are two or three short "
                  "sentences: what you did or found, then the next step or the one thing you need from him. Do not "
                  "narrate your search or list what you could not find unless it changes the plan. No numbered lists "
                  "unless he asks for steps or choices."),
    ("- Say a reminder once", "- Say a reminder once. Do not tack a deadline or a reminder onto later replies unless "
                              "he asks or it changed."),
]
# The template's short length line, which the full one replaces.
SHORT_LENGTH = "- Write like a teammate texting: most replies are two or three short sentences."
# The house rules' example of a failed connection names Gmail; in a home that is not Colin's it is "your email".
GMAIL_EXAMPLE = ("\"I couldn't reach your Gmail just now\"", "\"I couldn't reach your email just now\"")

# The approval rule's card sentence: Colin's SOULs and the house rules, then the lead template other homes start from.
CARD_EDITS = [
    ("Then reply with only one short plain line, such as \"Waiting for your okay on the card.\", and when he approves, "
     "make exactly the same call again.",
     "Keep doing the rest of what he asked that does not need that step, then reply with only one short plain line "
     "about the card, such as \"Waiting for your okay on the card.\", and when he approves, make exactly the same "
     "call again without asking him to confirm."),
    ("Reply with one short plain line, such as \"Waiting for your okay on the card.\", and when it is approved, make "
     "exactly the same call again.",
     "Keep doing the rest of what was asked that does not need that step, then reply with one short plain line about "
     "the card, such as \"Waiting for your okay on the card.\", and when it is approved, make exactly the same call "
     "again without asking again."),
]
TEAM_START, TEAM_END = "<!-- team:start -->", "<!-- team:end -->"
# The To-Do template's aside, dropped in a home with no Trello board (live convo QA 2: To-Do told Sam to "connect the
# Trello board" in a home that cannot connect one).
TRELLO_ASIDE = " (a Trello board when one is connected)"


_READER = None


def _reader():
    """The home's settings reader: the plugin's home.py beside this script's folder (the repo, or ~/taffy/bin next to
    ~/taffy/plugins), else the running user's own ~/taffy/plugins/taffy-tools/home.py.

    It used to fall back to Colin's wording when neither loaded. Run from /tmp in the test home and Riley's on Oct 9,
    it wrote "Colin's okay" and "he" into Sam's and Riley's SOULs and skipped their card sentence (live convo QA).
    Now it stops instead (main says why)."""
    global _READER
    if _READER is not None:
        return _READER
    import importlib.util
    for path in (Path(__file__).resolve().parent.parent / "plugins" / "taffy-tools" / "home.py",
                 Path.home() / "taffy" / "plugins" / "taffy-tools" / "home.py"):
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("taffy_plugin_home_cli", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:
            continue
        _READER = mod
        return mod
    raise RuntimeError("Could not load this home's settings reader (plugins/taffy-tools/home.py beside this script or "
                       "in ~/taffy). Nothing was changed; copy the script to ~/taffy/bin and run it from there.")


def _colin_texts() -> list[str]:
    """Every line and sentence this script writes, in Colin's wording."""
    out = LINES + LEAD_LINES + [old for old, _ in LINE_EDITS] + [line for _, line in LEAD_STYLE_LINES]
    for old, new in CARD_EDITS:
        out += [old, new]
    return out + [GMAIL_EXAMPLE[0]]


def _names_colin(soul: str) -> bool:
    """A SOUL written for Colin: outside the lines this script writes, it names him more often than this home's
    person (his own SOULs, and the repo copies the tests read in any home shape). A SOUL for Riley that mentions
    Colin now and then is still hers."""
    rest = soul or ""
    for text in sorted(_colin_texts(), key=len, reverse=True):
        rest = rest.replace(text, "")
    colin = len(re.findall(r"\bColin\b", rest))
    if not colin:
        return False
    first = _reader().first_name()
    if first == "Colin":
        return True
    return colin > len(re.findall(rf"\b{re.escape(first)}\b", rest))


def _p(text: str, soul: str = "") -> str:
    """Text written for Colin's SOULs, said for this home's person. A SOUL written for Colin keeps his wording, so
    his own SOULs read as before in any case."""
    if _names_colin(soul):
        return text
    return _reader().personalize(text)


def _hermes_home() -> Path:
    return _reader().path("hermes_home")


def is_lead(text: str) -> bool:
    """The lead's SOUL hands work to a team ("## Your team"); a specialist's has its own job instead."""
    return "\n## Your team\n" in text


def _last_bullet(lines: list[str]) -> int | None:
    """The index of the last "- " line outside the hub's team section, or None."""
    inside, last = False, None
    for i, line in enumerate(lines):
        if TEAM_START in line:
            inside = True
        if TEAM_END in line:
            inside = False
            continue
        if not inside and line.startswith("- "):
            last = i
    return last


def _repair(text: str) -> str:
    """Lines this script wrote in Colin's wording into a SOUL for someone else (the Oct 9 fallback, see _reader),
    said for the home's person instead. A SOUL written for Colin is left alone."""
    if _names_colin(text):
        return text
    out = text
    for colin in sorted(_colin_texts(), key=len, reverse=True):
        said = _reader().personalize(colin)
        if said != colin and colin in out:
            out = out.replace(colin, said)
    if not _reader().is_colin() and GMAIL_EXAMPLE[0] in out:
        out = out.replace(*GMAIL_EXAMPLE)
    return out


def update_text(text: str) -> str:
    """The text with the card sentence rewritten and the lines added; unchanged when both are already there or
    there is no bullet to add after."""
    out = _repair(text)
    if TRELLO_ASIDE in out and not _reader().trello():
        out = out.replace(TRELLO_ASIDE, "")
    for old, new in CARD_EDITS + LINE_EDITS:
        old_p, new_p = _p(old, text), _p(new, text)
        if old_p in out and new_p not in out:
            out = out.replace(old_p, new_p)
    lead = is_lead(out)
    if lead and SHORT_LENGTH in out and "- Length:" not in out:
        out = out.replace(SHORT_LENGTH, _p(LEAD_STYLE_LINES[0][1], text))
    add = ([] if MARKER in out or QUIET_MARKER in out else LINES) + ([] if not lead or LEAD_MARKER in out else LEAD_LINES)
    if lead:
        add += [line for marker, line in LEAD_STYLE_LINES if marker not in out]
    if add:
        lines = out.split("\n")
        at = _last_bullet(lines)
        if at is not None:
            lines[at + 1:at + 1] = [_p(line, text) for line in add]
            out = "\n".join(lines)
    return out


def live_files() -> list[Path]:
    home = _hermes_home()
    return [p for p in [home / "SOUL.md", *sorted((home / "profiles").glob("*/SOUL.md"))] if p.is_file()]


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    try:
        _reader()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    paths = [Path(a) for a in argv if not a.startswith("--")] or live_files()
    stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d-%H%M%S")
    changed = 0
    for path in paths:
        text = path.read_text()
        new = update_text(text)
        if new == text:
            state = "already updated" if MARKER in text or QUIET_MARKER in text else "nothing to add after"
            print(f"{path}: {state}")
            continue
        changed += 1
        size = f"{len(new):,} characters"
        if dry:
            print(f"{path}: would update ({size}) (dry run)")
            continue
        backup = path.with_name(f"{path.name}.bak-fluid-{stamp}")
        shutil.copy2(path, backup)
        path.write_text(new)
        print(f"{path}: updated, {size} (backup {backup.name})")
    print(f"{changed} file(s) {'would change' if dry else 'changed'}. Ongoing chats pick it up within two minutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
