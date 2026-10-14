"""browser_steps: plain-English browser steps grounded by the OpenAI Decisions API.

The bot plans the steps; for each step this reads the page's accessibility snapshot through the
bot's own browser tools, asks gpt-6-luna (POST /v1/decisions, a choice over element refs) which
element the step means, and clicks or types there. A step it is unsure about (confidence under
the threshold) stops the run and hands back to the model with the current snapshot.

Measured on the Mac on 2026-10-06/07: 32 of 34 picks correct at about 0.2 s each; both misses
scored under 0.3, every correct pick scored 0.51 or higher.
"""

import json
import os
import pwd
import re
import time
import urllib.error
import urllib.request

from . import home, link_click, tiers

MODEL = "gpt-6-luna"
DEFAULT_MIN_CONFIDENCE = 0.4
REAL_HOME = os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir
ENV_FILE = str(home.path("hermes_home", REAL_HOME) / ".env")
CONSEQUENTIAL = re.compile(r"\b(submit|send|pay|purchase|buy|checkout|place order|confirm|delete|remove|publish|post)\b", re.I)
REF_LINE = re.compile(r'^\s*-\s+(?P<role>[^"\[]*)(?:"(?P<name>[^"]*)")?.*?\[ref=(?P<ref>e\d+)\]')

SCHEMA = {
    "name": "browser_steps",
    "description": (
        "Fast path for routine work in your browser. Give a short list of plain-English steps and they run "
        "back to back without a model turn per click, each grounded on the page's accessibility snapshot "
        "by a fast classifier. Step forms: 'go to URL' opens a page; 'type \"TEXT\" into TARGET' fills a "
        "field; 'press KEY' presses a key such as Enter or Tab; any other text clicks the matching "
        "element (for example 'open the Unit 6 forum' or 'click Submit assignment'). The run stops at the "
        "first step it is unsure about and returns what it did plus the current page snapshot, so you can "
        "continue with the normal browser tools. Never use it for steps that send, submit, publish or pay "
        "without Colin's explicit yes. Optional goal: a yes/no question checked against the final page."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25},
            "goal": {"type": "string", "description": "Yes/no question that is true when the task is done."},
            "min_confidence": {"type": "number", "description": f"Stop below this (default {DEFAULT_MIN_CONFIDENCE})."},
        },
        "required": ["steps"],
    },
}


def _api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        with open(ENV_FILE) as fh:
            for line in fh:
                if line.startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


JUDGE_TIMEOUT_SEC = 3


def _owner() -> str:
    """The home's owner-switch folder (paths.owner), in the running user's real home."""
    return str(home.path("owner", pwd.getpwuid(os.getuid()).pw_dir))


def paid_calls_on() -> bool:
    """Colin pays for every Decisions API call, so they are off unless he turns them on (Oct 8, after a $6.75
    surprise). Turn on: touch ~/taffy/owner/paid-decisions-on. Off: the permission judge falls back to the hard
    rules and browser_steps is not offered. A home whose settings say judge.enabled false never makes them, whatever
    its owner folder holds (a shared plan has no paid key of its own)."""
    return home.judge_enabled() and os.path.exists(os.path.join(_owner(), "paid-decisions-on"))


def browser_steps_on() -> bool:
    """browser_steps needs paid calls and its own switch. It was turned off on Oct 8 while its typing skipped the gate
    (QA round 7, M1); every step now passes the gate, and Colin turns it on when he wants it:
    touch ~/taffy/owner/browser-steps-on."""
    return paid_calls_on() and os.path.exists(os.path.join(_owner(), "browser-steps-on"))


def _post(body: dict, timeout: float) -> list[dict]:
    if not paid_calls_on():
        raise RuntimeError("Paid decision calls are off")
    key = _api_key()
    if not key:
        raise RuntimeError("No OPENAI_API_KEY for the Decisions API")
    req = urllib.request.Request("https://api.openai.com/v1/decisions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["answers"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Decisions API error {e.code}: {e.read().decode()[:200]}")


def _decide(body: dict) -> dict:
    return _post(body, 30)[0]


def decide_one(body: dict, timeout: float = JUDGE_TIMEOUT_SEC) -> dict:
    """One answer for a permission question, with the judge's short timeout."""
    return _post(body, timeout)[0]


def probability(a: dict) -> float | None:
    """A predicate's probability, or None when the model refused or answered something else; callers treat
    None as unknown and fall back to the hard rule (9 of 3,372 eval requests came back as refusals)."""
    if not isinstance(a, dict) or a.get("type") == "refusal":
        return None
    p = a.get("probability")
    return float(p) if isinstance(p, (int, float)) else None


def choice(a: dict) -> tuple[str | None, float]:
    """A choice question's (choice, confidence); (None, 0.0) for a refusal."""
    if not isinstance(a, dict) or a.get("type") != "choice":
        return None, 0.0
    return a.get("choice"), float(a.get("confidence") or 0.0)


def elements(snapshot: str) -> list[dict]:
    out, seen = [], set()
    for line in snapshot.splitlines():
        m = REF_LINE.match(line)
        if m and m["ref"] not in seen:
            seen.add(m["ref"])
            role = m["role"].strip().rstrip(":")
            label = f'{role} "{m["name"]}"' if m["name"] else role
            out.append({"value": "@" + m["ref"], "description": label[:200]})
    return out


def pick(snapshot: str, target: str, done: list[str]) -> tuple[str | None, float]:
    choices = elements(snapshot)
    if not choices:
        return None, 0.0
    a = _decide({
        "model": MODEL,
        "input": f"Accessibility snapshot of a web page:\n{snapshot[:60000]}\n\n"
                 f"Steps already done: {'; '.join(done) or 'none'}\nNext step: {target}",
        "questions": [{"type": "choice", "name": "element",
                       "instructions": "Which element ref should be clicked or typed into to carry out the next step?",
                       "choices": choices[:400]}],
    })
    return choice(a)


def check(snapshot: str, goal: str) -> float | None:
    a = _decide({"model": MODEL, "input": f"Accessibility snapshot of a web page:\n{snapshot[:60000]}",
                 "questions": [{"type": "predicate", "name": "done", "instructions": goal}]})
    return probability(a)


def parse(step: str) -> tuple[str, str | None, str | None]:
    m = re.match(r'^\s*(?:go to|open|navigate to)\s+(https?://\S+)\s*$', step, re.I)
    if m:
        return "goto", None, m.group(1)
    m = re.match(r'^\s*type\s+"(.*)"\s+(?:into|in)\s+(.+)$', step, re.I | re.S)
    if m:
        return "type", m.group(2), m.group(1)
    m = re.match(r'^\s*press\s+(?:the\s+)?(?:key\s+)?([A-Za-z]+(?:\+[A-Za-z]+)*)\s*$', step, re.I)
    if m:
        return "key", None, m.group(1)
    return "click", step, None


class Sensitive(Exception):
    """The page moved to a sensitive site mid-run: stop at once, before any snapshot goes to the grounding model."""


class Blocked(Exception):
    """The gate stopped a step, as it would stop the same browser tool called on its own (Oct 8 QA round 7, M1)."""


_SECRET_VALUE = re.compile(r"^\d{4,8}$|^(?:\d[ -]?){11,18}\d$|^\d{3}-?\d{2}-?\d{4}$")
_SECRET_LABEL = re.compile(r"password|passcode|pass\s*phrase|\bcode\b|\bpin\b|cvv|cvc|security|card number|"
                           r"account number|routing|ssn|social security|secret|token|api key|private key", re.I)
_FIELD_LINE = re.compile(r'^(?P<head>\s*-\s+(?:textbox|searchbox|combobox|textarea|spinbutton)\b[^"\n]*"(?P<name>[^"]*)"'
                         r'[^:\n]*:\s*)(?P<value>.+)$', re.I | re.M)
HIDDEN = "[hidden]"


def looks_secret(value: str, label: str = "") -> bool:
    """Is a typed value something the grounding model must never see?"""
    v = (value or "").strip()
    if not v:
        return False
    if _SECRET_LABEL.search(label or "") or _SECRET_VALUE.match(v):
        return True
    if re.search(r"\s", v) or "@" in v or "://" in v or len(v) < 6:
        return False
    return bool(re.search(r"[A-Za-z]", v) and re.search(r"\d", v))


def redact(text: str, secrets: list[str]) -> str:
    """The text with every secret value replaced, and the value of any field whose name looks secret hidden."""
    out = text or ""
    for v in sorted({s for s in secrets if s and len(s) >= 3}, key=len, reverse=True):
        out = out.replace(v, HIDDEN)
    return _FIELD_LINE.sub(lambda m: m["head"] + HIDDEN if _SECRET_LABEL.search(m["name"]) else m.group(0), out)


def _shown(kind: str, target: str | None, value: str | None, step: str) -> str:
    """How a step is told to the grounding model: what was typed never goes, and a page's query string (a reset
    link's token) does not either."""
    if kind == "type":
        return f"type into {target}"
    if kind == "goto":
        return "go to " + re.sub(r"[?#].*$", "", value or "")
    return step


def refusal(steps: list) -> str | None:
    """Why a run must not start: a step types into a password or code field (the rule browser_type has)."""
    for i, step in enumerate(steps or [], 1):
        kind, target, _value = parse(str(step))
        if kind == "type" and tiers.SECRET_FIELD.search(target or ""):
            return f"browser_steps did not run: step {i} types into a password or code field. " + tiers.TYPE_SECRET_NEVER
    return None


def run(dispatch, args: dict, page=None) -> str:
    """dispatch(tool_name, args) -> JSON string, calling the bot's own browser tools through the gate: it raises
    Blocked when the gate stops a call (a send, a pay button, typing into a password field), as the gate would stop
    the same tool called on its own. page (a link_click.Page) lets a click on a wrapped link that did not open it open
    the link's page through browser_navigate (Oct 9: School could not open the HIS 203 course page)."""
    steps = args.get("steps") or []
    parsed = [parse(str(s)) for s in steps]
    refused = refusal(steps)
    if refused:
        return refused
    secrets = [v for kind, target, v in parsed if kind == "type" and looks_secret(v or "", target or "")]

    def show(step: str) -> str:
        return redact(step, secrets)
    threshold = max(0.3, float(args.get("min_confidence", DEFAULT_MIN_CONFIDENCE) or DEFAULT_MIN_CONFIDENCE))
    t0 = time.time()

    def call(name: str, a: dict) -> str:
        out = dispatch(name, a)
        try:
            data = json.loads(out)
        except (TypeError, ValueError):
            return str(out)
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"{name}: {data['error']}")
        if isinstance(data, dict):
            return data.get("snapshot") or data.get("content") or data.get("result") or json.dumps(data)
        return str(data)

    log, done, said, stopped = [], [], [], None
    for i, (step, (kind, target, value)) in enumerate(zip(steps, parsed), 1):
        step = str(step)
        try:
            if kind == "goto":
                call("browser_navigate", {"url": value})
                log.append(f"{i}. {show(step)}: opened")
            elif kind == "key":
                call("browser_press", {"key": value})
                log.append(f"{i}. {show(step)}: pressed {value}")
            else:
                snap = call("browser_snapshot", {})
                ref, conf = pick(redact(snap, secrets), show(target or ""), said)
                if not ref or conf < threshold:
                    stopped = (f"Stopped before step {i} ({show(step)!r}): unsure which element (best guess {ref}, "
                               f"confidence {conf:.2f}).")
                    break
                label = next((e["description"] for e in elements(snap) if e["value"] == ref), "")
                if kind == "click" and CONSEQUENTIAL.search(label) and not CONSEQUENTIAL.search(step):
                    stopped = (f"Stopped before step {i} ({show(step)!r}): {label!r} looks like a send, submit, pay or "
                               "delete button. Say that action explicitly in the step, and only with Colin's yes.")
                    break
                opened = None
                if kind == "type":
                    call("browser_type", {"ref": ref, "text": value})
                elif page is not None:
                    _out, opened = link_click.click(ref, lambda: call("browser_click", {"ref": ref}), page,
                                                    lambda url: call("browser_navigate", {"url": url}))
                else:
                    call("browser_click", {"ref": ref})
                log.append(f"{i}. {show(step)}: {ref} (confidence {conf:.2f})"
                           + (f"; the click did not open the link, so its page was opened directly: {opened}"
                              if opened else ""))
            done.append(step)
            said.append(show(_shown(kind, target, value, step)))
        except Sensitive:
            raise
        except Blocked as e:
            stopped = f"Stopped before step {i} ({show(step)!r}): {e}"
            break
        except Exception as e:
            stopped = f"Stopped at step {i} ({show(step)!r}): {show(str(e))}"
            break

    try:
        final = redact(call("browser_snapshot", {}), secrets)
    except Exception as e:
        final = f"(could not read the page: {show(str(e))})"
    lines = [f"browser_steps: {len(done)} of {len(steps)} steps done in {time.time() - t0:.1f} s."] + log
    if stopped:
        lines.append(stopped)
    elif args.get("goal"):
        try:
            p = check(final, args["goal"])
            lines.append(f"Goal check ({args['goal']}): " + (f"probability {p:.2f}." if p is not None else "no answer."))
        except Exception as e:
            lines.append(f"Goal check failed: {e}")
    lines.append("\nCurrent page snapshot:\n" + final[:12000])
    return "\n".join(lines)
