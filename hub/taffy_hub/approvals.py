""



















import json
import logging
import re
import time

import httpx

from . import config, consent, who

log = logging.getLogger("taffy.approvals")




GATE_DECIDES = re.compile(r"^(delete in root path|recursive delete|find -delete|find -exec|xargs with rm|"
                          r"git clean with force|script execution via|shell execution via heredoc|shell command via -c)",
                          re.I)

plain = consent.plain_command
ALLOWED_LATER_SEC = 30 * 60


_allowed_later: dict[tuple[str, str], tuple[float, str]] = {}
_commands: dict[str, str] = {}


def one_part(command: str) -> bool:
    ""
    bare = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", command or "")
    return not re.search(r"[;|\n`]|&&|(?<![<>])&(?![>&])|\$\(", bare.strip())


def fingerprint(run_id: str, request_id: str) -> str:
    return f"hermes:{run_id}:{request_id}"


def parse(fp: str) -> tuple[str, str] | None:
    m = re.match(r"^hermes:([^:]+):(.*)$", fp or "")
    return (m.group(1), m.group(2)) if m else None


async def answer(bot_id: str, run_id: str, request_id: str, choice: str) -> bool:
    ""
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
    ""

    request_id = str(data.get("request_id") or "")
    description = str(data.get("description") or data.get("pattern_key") or "")
    command = str(data.get("command") or "")
    run_id = str(data.get("run_id") or run_id or "")
    if not run_id:
        return None
    if str(data.get("surface") or "") == "vault-payment" or description.startswith("Fill payment card"):


        log.info("refused %s's unpatched card fill prompt", bot_id)
        await answer(bot_id, run_id, request_id, "deny")
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
    while len(_commands) > 200:
        _commands.pop(next(iter(_commands)))

    consent.action_request(bot_id, "command", "", plain((seen or {}).get("flagged") or description, command), "", "",
                           fp, session_id, None, why="asked" if asked else "background", details=command,
                           likeness={"place": place})
    return "Waiting for your okay"


async def decide(card: dict, allow: bool) -> bool:
    ""


    fp = card.get("fingerprint") or ""
    parsed = parse(fp)
    if not parsed:
        return False
    ok = await answer(card["bot_id"], parsed[0], parsed[1], "once" if allow else "deny")
    if ok:
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
    ""
    minutes = (time.time() - float(card.get("created") or time.time())) / 60
    if minutes < 10:
        return ""
    if minutes < 55:
        return f"{int(round(minutes / 5) * 5)} minutes"
    hours = max(1, int(round(minutes / 60)))
    return "an hour" if hours == 1 else f"{hours} hours"


def late_note(card: dict, allow: bool) -> str:
    ""


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
