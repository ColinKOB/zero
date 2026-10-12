"""Hermes's own command approvals, for the turns the hub runs.

Hermes checks some terminal commands against its own list (recursive deletes, `python -c`, `bash -c`, stopping a
service, a force push and others) after the taffy-tools gate has already let them through, and then waits up to five
minutes for an answer on POST /v1/runs/{run}/approval. The hub ignored those prompts, so a Zip sat waiting with
nothing in the app and the command was then refused (Oct 8 QA round 3, M6).

Now each prompt is answered here:
- When the gate already judged every part of the command that Hermes's check would ask about, the prompt is answered
  yes at once. The gate records this as it lets the command through (consent.remember_command, tiers.unjudged): its
  delete, script and database rules, a push it allowed, routine work in the Zip's own project (a reset, a branch
  delete, npm uninstall, stopping the project's containers), and anything Colin just allowed on the gate's own card.
  Hermes names only the first thing it found, so its description alone is not enough: `rm -rf build && sudo
  systemctl restart nginx` reads as a delete (Oct 8 QA round 4, M2 and M3).
- Anything else becomes a card in the app ("Zero wants to run a command"), titled by the part the gate did not
  judge. Allow Once and Not Now answer Hermes directly. If Hermes has stopped waiting when Colin answers, the Zip
  picks the conversation back up with his answer, and Allow Once lets it run that command once (round 4, L5).
- In a teammate's conversation there is nobody to ask, so the rest are refused.
"""

import json
import logging
import re
import time

import httpx

from . import config, consent, who

log = logging.getLogger("taffy.approvals")

# Hermes's descriptions (tools/approval_detection.py) of checks that tiers.py makes itself. Used only when the gate
# left no record of the command (an older plugin, or a prompt whose command was redacted differently), and then only
# for a command with a single part.
GATE_DECIDES = re.compile(r"^(delete in root path|recursive delete|find -delete|find -exec|xargs with rm|"
                          r"git clean with force|script execution via|shell execution via heredoc|shell command via -c)",
                          re.I)
# Card titles live in consent.py, so the gate words a command it finds inside a script the same way (Oct 8 QA round 6).
plain = consent.plain_command
ALLOWED_LATER_SEC = 30 * 60   # Allow Once after Hermes gave up covers that command's next try for this long
# (bot, command) -> (until, the folder it was allowed in, as consent.place_hash). The same command in another folder
# gets its own card (Oct 8 QA round 5, M2).
_allowed_later: dict[tuple[str, str], tuple[float, str]] = {}
_commands: dict[str, str] = {}   # card fingerprint -> the command as Hermes sent it (the card keeps a scrubbed copy)


def one_part(command: str) -> bool:
    """A single simple command: no ;, &&, ||, |, &, newline or command substitution outside quotes."""
    bare = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", command or "")
    return not re.search(r"[;|\n`]|&&|(?<![<>])&(?![>&])|\$\(", bare.strip())


def fingerprint(run_id: str, request_id: str) -> str:
    return f"hermes:{run_id}:{request_id}"


def parse(fp: str) -> tuple[str, str] | None:
    m = re.match(r"^hermes:([^:]+):(.*)$", fp or "")
    return (m.group(1), m.group(2)) if m else None


async def answer(bot_id: str, run_id: str, request_id: str, choice: str) -> bool:
    """Answers Hermes's prompt: "once" or "deny". False when the run no longer waits (it gave up or finished)."""
    body = {"choice": choice}
    if request_id:
        body["request_id"] = request_id
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/v1/runs/{run_id}/approval",
                                  headers={"Authorization": f"Bearer {config.api_key(bot_id)}",
                                           "Content-Type": "application/json"}, json=body)
        return r.status_code == 200
    except Exception as e:
        log.warning("could not answer an approval for %s (%s)", bot_id, e)
        return False


async def handle(bot_id: str, run_id: str, data: dict, session_id: str | None = None,
                 team: bool = False) -> str | None:
    """Answers one approval.request from a turn's stream, or puts it in front of Colin. Returns the status line the
    app shows while the Zip waits, or None."""
    request_id = str(data.get("request_id") or "")
    description = str(data.get("description") or data.get("pattern_key") or "")
    command = str(data.get("command") or "")
    run_id = str(data.get("run_id") or run_id or "")
    if not run_id:
        return None
    try:
        seen = consent.gate_checked(bot_id, command)
    except Exception:
        log.warning("could not read the gate's record of a command", exc_info=True)
        seen = None
    place = (seen or {}).get("place") or ""
    until, allowed_in = _allowed_later.pop((bot_id, command), (0, ""))
    later = until > time.time() and allowed_in == place
    judged = bool(seen["cleared"]) if seen else one_part(command) and GATE_DECIDES.match(description) is not None
    if judged or later:
        log.info("answered %s's own check (%s) for %s", "Hermes", description, bot_id)
        await answer(bot_id, run_id, request_id, "once")
        return None
    if team:
        await answer(bot_id, run_id, request_id, "deny")
        return None
    asked = bool(session_id) and consent.live_pass(session_id=session_id) is not None
    fp = fingerprint(run_id, request_id)
    _commands[fp] = command
    while len(_commands) > 200:   # cards nobody answered
        _commands.pop(next(iter(_commands)))
    # The card names the part the gate did not judge, not Hermes's first finding (often a routine delete).
    consent.action_request(bot_id, "command", "", plain((seen or {}).get("flagged") or description, command), "", "",
                           fp, session_id, None, why="asked" if asked else "background", details=command,
                           likeness={"place": place})
    return "Waiting for your okay"


async def decide(card: dict, allow: bool) -> bool:
    """Colin's answer on a command card goes straight to the waiting run. If the run already gave up, Allow Once
    covers that command's next try instead, and the caller resumes the conversation. True when the run was still
    waiting."""
    fp = card.get("fingerprint") or ""
    parsed = parse(fp)
    if not parsed:
        return False
    ok = await answer(card["bot_id"], parsed[0], parsed[1], "once" if allow else "deny")
    if ok:   # the run goes on with the answer: no longer held on the card (activity.resumed)
        from . import activity
        activity.resumed(card["bot_id"], parsed[0])
    command = _commands.pop(fp, None) or card.get("details")
    if not ok and allow and command:
        try:
            place = (json.loads(card.get("likeness") or "{}") or {}).get("place") or ""
        except (TypeError, ValueError):
            place = ""
        _allowed_later[(card["bot_id"], command)] = (time.time() + ALLOWED_LATER_SEC, place)
    return ok


def _age(card: dict) -> str:
    """How long ago the card came up, in words, once that is more than a few minutes."""
    minutes = (time.time() - float(card.get("created") or time.time())) / 60
    if minutes < 10:
        return ""
    if minutes < 55:
        return f"{int(round(minutes / 5) * 5)} minutes"
    hours = max(1, int(round(minutes / 60)))
    return "an hour" if hours == 1 else f"{hours} hours"


def late_note(card: dict, allow: bool) -> str:
    """What the Zip hears when Colin answers a command card after Hermes stopped waiting: Hermes told it to stop and
    wait for him, so it picks the conversation back up (Oct 8 QA round 4, L5). A card can wait for hours, so a late
    Allow says how late it is and asks the Zip to check the command still fits (round 5, L3)."""
    what = (card.get("reason") or "the command").rstrip(".")
    what = what[:1].lower() + what[1:]
    if allow:
        age = _age(card)
        if age:
            return (f"{who.first()} allowed the command you were waiting on ({what}), {age} after you asked. Check "
                    "that it still fits what you are doing now. If it does, run exactly the same command again, "
                    "unchanged and in the same folder; it goes through once. If it no longer fits, do not run it, and "
                    f"tell {who.first()} in a sentence why.")
        return (f"{who.first()} allowed the command you were waiting on ({what}). Run exactly the same command again "
                "now, unchanged and in the same folder; it goes through once. Then carry on.")
    return (f"{who.first()} said not now to the command you were waiting on ({what}). Do not run it or try another "
            f"way. Finish what you can and tell {who.first()} in a sentence what is left undone.")
