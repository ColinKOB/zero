""

import asyncio
from dataclasses import dataclass
import math
import re
import time


@dataclass(frozen=True)
class Step:
    action: str
    args: dict
    exact: bool = True


FIELDS = {
    "open": {"target": str, "front": bool},
    "press": {"name": str, "element": int, "text": str, "expect": str},
    "type": {"text": str},
    "key": {"keys": str},
    "click": {"x": (int, float), "y": (int, float), "button": str, "count": int,
              "to_x": (int, float), "to_y": (int, float), "real": bool},
    "wait": {"text": str, "name": str, "timeout": (int, float)},
    "ui": {"query": str},
}
REQUIRED = {"open": ("target",), "type": ("text",), "key": ("keys",), "click": ("x", "y")}
WAIT_SEC, WAIT_AFTER_OPEN, WAIT_MAX = 5, 10, 10
SHOWN_CONTROLS = 40
KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789,./;'[]-=`") | {
    "return", "enter", "tab", "space", "delete", "backspace", "escape", "esc", "left", "right", "up", "down",
    "home", "end", "pageup", "pagedown", "forwarddelete", "f1", "f2", "f3", "f4", "f5",
}


def legacy(raw: str) -> Step:
    ""
    raw = raw.strip()
    match = re.fullmatch(r'''(?:type|enter|fill in|write|put)\s+["“'‘](.+?)["”'’]\s+(?:in|into|on)\s+(?:the\s+)?(.+)''', raw, re.I)
    if match:
        return Step("press", {"name": match[2], "text": match[1]}, False)
    match = re.fullmatch(r'''(?:choose|pick|select)\s+(?:"(.+?)"|“(.+?)”|'(.+?)'|‘(.+?)’|(.+?))\s+(?:in|from|for)\s+(?:the\s+)?(.+)''', raw, re.I)
    if match:
        return Step("press", {"name": match[6], "text": next(v for v in match.groups()[:5] if v is not None)}, False)
    match = re.fullmatch(r'''(?:type|enter)\s+["“'‘](.+?)["”'’]''', raw, re.I)
    if match:
        return Step("type", {"text": match[1]})
    match = re.fullmatch(r"(?:press|hit)\s+(?:the\s+)?((?:(?:cmd|command|shift|opt|option|alt|ctrl|control)\+)*\S+?)(?:\s+key)?", raw, re.I)
    if match and match[1].lower().split("+")[-1] in KEYS:
        return Step("key", {"keys": match[1]})
    name = re.sub(r"^(?:click|press|tap|open|select|turn|tick|check)\s+", "", raw, flags=re.I)
    return Step("press", {"name": name}, False)


def parse(args: dict, error) -> list[Step]:
    raw = args.get("steps")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 15 or set(args) - {"steps", "include_screenshot"}:
        raise error("Give between 1 and 15 Mac steps.")
    if "include_screenshot" in args and type(args["include_screenshot"]) is not bool:
        raise error("include_screenshot must be true or false.")
    steps = []
    for number, value in enumerate(raw, 1):
        if isinstance(value, str):
            step = legacy(value)
        elif isinstance(value, dict):
            step = Step(value.get("action"), {k: v for k, v in value.items() if k != "action"})
        else:
            raise error(f"Step {number} must be an action or a short sentence.")
        if not isinstance(step.action, str) or step.action not in FIELDS:
            raise error(f"Step {number} has an unsupported action.")
        fields = FIELDS[step.action]
        for key, value in step.args.items():
            expected = fields.get(key)
            if expected is None or not isinstance(value, expected) or (isinstance(value, bool) and expected is not bool):
                raise error(f"Step {number} has an invalid {key}.")
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise error(f"Step {number} has an invalid {key}.")
        needed = REQUIRED.get(step.action, ())
        if any(key not in step.args or step.args[key] == "" for key in needed):
            raise error(f"Step {number} is missing its target or text.")
        if step.action == "press" and not (step.args.get("name", "").strip() or step.args.get("element", 0) > 0):
            raise error(f"Step {number} needs an element name or number.")
        if step.action == "wait" and not (step.args.get("text", "").strip() or step.args.get("name", "").strip()):
            raise error(f"Step {number} needs the text or the control name to wait for.")
        if step.action == "wait":



            after_open = bool(steps) and steps[-1].action == "open"
            timeout = min(max(step.args.get("timeout", WAIT_AFTER_OPEN if after_open else WAIT_SEC), 0), WAIT_MAX)
            step = Step(step.action, {**step.args, "timeout": timeout}, step.exact)
        steps.append(step)
    return steps


async def run(args: dict, dispatch, error) -> dict:
    steps = parse(args, error)
    result = {"ok": True, "completed": []}
    started = time.monotonic()
    for number, step in enumerate(steps, 1):
        try:
            if time.monotonic() - started >= 75:
                raise error("The batch time limit ended. Check the current state before continuing.")
            inner = step.args
            if step.action == "press" and "name" in inner and step.exact:
                inner = {**inner, "exact": True}
            try:
                reply = await asyncio.wait_for(dispatch(step.action, inner), max(0.01, started + 75 - time.monotonic()))
            except asyncio.TimeoutError:
                raise error("The action did not answer in time. Its outcome is unknown; do not repeat a change.")
            result["completed"].append({"step": number, "action": step.action, "result": reply})
        except error as exc:
            result.update(ok=False, failed_step=number, action=step.action, error=str(exc))
            break
    if args.get("include_screenshot") or not result["ok"]:
        try:
            result["screenshot"] = await asyncio.wait_for(dispatch("screenshot", {}), 15)
        except (error, asyncio.TimeoutError):
            result["screenshot_unavailable"] = True
    return result


async def wait(args: dict, poll, error) -> str:
    text = args.get("text", "").strip()
    wanted = text or args["name"].strip()
    match = "match_text" if text else "match_name"
    timeout = min(max(args.get("timeout", WAIT_SEC), 0), WAIT_MAX)
    until = time.monotonic() + timeout
    while True:
        reply = await poll({"query": wanted, match: wanted})
        if isinstance(reply, dict) and isinstance(reply.get("matched"), bool):
            if reply["matched"]:
                return str(reply.get("ui", ""))
            reply = ""
        controls = [line.strip() for line in str(reply).splitlines() if re.match(r'^\s*\[\d+\] \w+ "', line)]
        fields = [re.findall(r'"([^"]*)"', line) for line in controls]
        present = any(
            (values[0].removesuffix(" (by its row)").casefold() == wanted.casefold()) if not text
            else any(wanted.casefold() in value.casefold() for value in values)
            for values in fields if values
        )
        if present:
            return str(reply)
        if time.monotonic() >= until:

            shown = "\n".join(controls[:SHOWN_CONTROLS]) or "no controls"
            raise error(f'"{wanted}" did not appear within {timeout:g} seconds. The window showed last:\n{shown}')
        await asyncio.sleep(0.2)
