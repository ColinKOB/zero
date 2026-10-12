"""Home-agnostic checks: what must hold for any home, whoever it belongs to. Standard library only.

- `validate_home(data)`   the home.json contract from the plan (section 4): types, allowed values, and no secret in
                          the file. Returns a list of problems (empty when the file is good).
- `colin_facts(home)`     the facts about Colin that must never show up in a home that is not his, as
                          (label, pattern, severity). "fail" facts name him, his people, schools, employers,
                          clients, addresses, board or paths; "warn" facts are wording that usually means a
                          Colin-shaped default leaked (his, alien, his time zone).
- `scan(texts, home)`     runs those patterns over rendered strings (prompts, tool descriptions, API answers) and
                          returns findings [{label, severity, where, excerpt}]. Excerpts are short and never
                          include anything that looks like a token.

The hub and plugin suites call these from their own tests for each home shape (homes.py), and assert_state.py runs
them against a live hub's answers and the turns the hub sent to its Zips.
"""

from __future__ import annotations

import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLAN_MODES = {"own", "broker", "api_key"}
CONNECTORS = {"none", "claude", "chatgpt"}
SECRET_KEY = re.compile(r"(pass(word)?|secret|token|api_?key|credential|bearer|private)", re.I)
ALLOWED_SECRETISH_KEYS = {"key_dir", "pronoun_free"}
TOKENISH = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{40,})")


def _get(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return KeyError
        cur = cur[part]
    return cur


def validate_home(data) -> list[str]:
    """Problems with a home.json, in plain sentences. The schema is the plan's; unknown keys are allowed (a reader
    must ignore what it does not know), missing ones are fine where the reader has a default."""
    if not isinstance(data, dict):
        return ["home.json must be a JSON object."]
    problems: list[str] = []

    def want(path: str, kind, required: bool = False, allowed=None):
        v = _get(data, path)
        if v is KeyError:
            if required:
                problems.append(f"{path} is missing.")
            return None
        if v is not None and not isinstance(v, kind):
            problems.append(f"{path} should be {getattr(kind, '__name__', kind)}, not {type(v).__name__}.")
            return None
        if allowed is not None and v not in allowed:
            problems.append(f"{path} is {v!r}; expected one of {sorted(allowed)}.")
        return v

    if want("version", int, required=True) not in (None, 1):
        problems.append("version must be 1.")
    hid = want("home_id", str, required=True)
    if hid is not None and not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", hid):
        problems.append(f"home_id {hid!r} should be a short lowercase id.")
    want("person.name", str)
    want("person.first_name", str)
    tz = want("person.timezone", str)
    if tz:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"person.timezone {tz!r} is not a time zone.")
    for k in ("start", "end"):
        h = want(f"person.quiet_hours.{k}", int)
        if h is not None and not 0 <= h <= 23:
            problems.append(f"person.quiet_hours.{k} must be an hour from 0 to 23.")
    want("locale.units", str, allowed={"us", "metric", "uk"})
    want("locale.clock", str, allowed={"12h", "24h"})
    for k in ("hub", "hermes_api", "broker_http"):
        p = want(f"ports.{k}", int)
        if p is not None and not 1024 <= p <= 65535:
            problems.append(f"ports.{k} must be a port above 1023.")
    if (hub := _get(data, "ports.hub")) not in (KeyError, None) and hub == _get(data, "ports.hermes_api"):
        problems.append("ports.hub and ports.hermes_api must differ.")
    want("network.public_url", str)
    want("host.kind", str, allowed={"server", "mac"})
    want("host.lead_backend", str, allowed={"local", "docker"})
    want("plan.claude.mode", str, allowed=PLAN_MODES)
    want("plan.chatgpt.mode", str, allowed=PLAN_MODES)
    conn = want("plan.connectors", str, allowed=CONNECTORS)
    modes = {_get(data, "plan.claude.mode"), _get(data, "plan.chatgpt.mode")}
    if "broker" in modes and conn not in (None, "none"):
        problems.append("A home on the shared plan (broker) cannot use connectors; plan.connectors must be none.")
    accounts = want("mail.accounts", list) or []
    ids = set()
    for i, a in enumerate(accounts):
        if not isinstance(a, dict) or not isinstance(a.get("address"), str) or "@" not in a.get("address", ""):
            problems.append(f"mail.accounts[{i}] needs an address.")
            continue
        if a.get("id") in ids:
            problems.append(f"mail.accounts[{i}] repeats the id {a.get('id')!r}.")
        ids.add(a.get("id"))
    if sum(1 for a in accounts if isinstance(a, dict) and a.get("default")) > 1:
        problems.append("Only one mailbox can be the default.")
    for i, c in enumerate(want("calendars", list) or []):
        if not isinstance(c, dict) or c.get("kind") not in ("caldav", "ical"):
            problems.append(f"calendars[{i}] needs a kind of caldav or ical.")
    want("judge.enabled", bool)
    want("backup.mac_copy_expected", bool)
    problems += [f"{p} looks like a secret; passwords and keys never go in home.json." for p in _secretish(data)]
    return problems


def _secretish(value, path: str = "") -> list[str]:
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            if SECRET_KEY.search(k) and k not in ALLOWED_SECRETISH_KEYS and v not in (None, "", False):
                out.append(p)
            else:
                out += _secretish(v, p)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _secretish(v, f"{path}[{i}]")
    elif isinstance(value, str) and TOKENISH.fullmatch(value) and "/" not in value:
        out.append(path)
    return out


# MARK: Colin's facts

_COLIN_FAIL = [
    ("Colin's name", r"\bColin\b"),
    ("Colin's username or home", r"jdoe|/home/jdoe|/Users/jdoe"),
    ("Colin's tailnet address", r"tail0000"),
    ("Colin's school", r"\bLakeview\b"),
    ("Colin's projects", r"Lumenly|Fernhill AI"),
    ("Colin's clients", r"Brightpath|Blue Heron|GoodWorks|goodworks"),
    ("Colin's employer", r"D\.\s?R\. Acme Homes"),
    ("Colin's Trello board", r"AbCd1234"),
    ("Colin's addresses", r"jd910@|jdoe1@|colin@goodworks"),
    ("Colin's documents", r"background-notes|background-record"),
    ("Colin's people", r"\bKen Avery\b"),
]
_COLIN_WARN = [
    ("a pronoun for the person", r"\b(?:his|him|he's)\b"),
    ("Colin's server by name", r"\balien\b"),
]


def colin_facts(home: dict | None) -> list[tuple[str, str, str]]:
    """The patterns for a home. Colin's own home (home_id colin, or no file at all) has none."""
    if home is None or home.get("home_id") == "colin":
        return []
    facts = [(label, rx, "fail") for label, rx in _COLIN_FAIL]
    name = ((home.get("person") or {}).get("name") or "")
    if "O'Brien" not in name:
        facts.append(("Colin's surname", r"O['’]Brien", "fail"))
    if "Riley" not in name:
        facts.append(("Colin's family", r"\bRiley\b", "fail"))
    facts += [(label, rx, "warn") for label, rx in _COLIN_WARN]
    tz = (home.get("person") or {}).get("timezone")
    if tz and tz != "America/New_York":
        facts += [("Colin's time zone", r"America/New_York|\bEastern\b", "fail")]
    return facts


def scannable(answer: dict) -> str:
    """An answer as text for scan(). A shared plan names whose plan it is on purpose ("a plan Colin shares with
    you"), so plan_owner is left out there: it is the owner's name, not a fact about him leaking into this home."""
    if answer.get("plan") == "shared":
        answer = {k: v for k, v in answer.items() if k != "plan_owner"}
    return json.dumps(answer, ensure_ascii=False)


def scan(texts: dict[str, str] | list[tuple[str, str]], home: dict | None) -> list[dict]:
    """Findings for every pattern in every text. `texts` maps where the text came from to the text."""
    items = texts.items() if isinstance(texts, dict) else texts
    facts = [(label, re.compile(rx, re.I if severity == "warn" else 0), severity)
             for label, rx, severity in colin_facts(home)]
    out = []
    for where, text in items:
        text = text if isinstance(text, str) else str(text)
        for label, rx, severity in facts:
            m = rx.search(text)
            if m:
                lo, hi = max(0, m.start() - 40), min(len(text), m.end() + 40)
                excerpt = TOKENISH.sub("[...]", " ".join(text[lo:hi].split()))
                out.append({"label": label, "severity": severity, "where": where, "excerpt": excerpt})
    return out
