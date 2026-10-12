"""The home's settings (home.json): who the person is, where the home's files live, and what the home can use.

The plugin's reader for the file the hub reads too (hub/taffy_hub/home.py). The schema and its defaults are fixed in
docs/plans/2026-10-09-zero-for-anyone-plan.md, section 4:

1. ZERO_HOME in the environment names the file; the systemd templates set it.
2. Without it, ~/taffy/home.json is tried.
3. Without that, the defaults apply, and they are Colin's home exactly as it ran before this file existed.

A file whose home_id is not "colin" (or that has none and is not Colin's own ~/taffy/home.json) never inherits
Colin's own facts (his name, mailboxes, Trello board, Tally, the background documents, his paid-calls judge): those
keys default to empty there, so a missing key in a stranger's file can never put Colin's data in front of their Zips.
Paths and ports default to the same layout in every home.

Only the standard library is used and nothing is imported from the package, so safe_files.py (which the hub loads by
path) can load this file by path as well. Nothing here formats a name at import time: every reader call sees the
file as it is now, so a rename reaches running services on the next turn.
"""

import contextlib
import copy
import json
import os
import pwd
import re
import sys
import types
from pathlib import Path

ENV = "ZERO_HOME"
COLIN = "colin"

# Every key in the schema, with the values every home shares unless its file says otherwise.
DEFAULTS = {
    "version": 1,
    "home_id": COLIN,
    "person": {"name": "", "first_name": "", "timezone": "America/New_York",
               "quiet_hours": {"start": 23, "end": 7}, "pronoun_free": True},
    "locale": {"units": "us", "spelling": "american", "clock": "12h"},
    "paths": {"hermes_home": "~/.taffy", "hermes_bin": "~/taffy/bin/taffy", "hub_state": "~/taffy/hub-state",
              "work": "~/taffy/work", "shared": "~/taffy/shared", "plugin": "~/taffy/plugins/taffy-tools",
              "owner": "~/taffy/owner", "background_docs": []},
    "ports": {"hub": 8650, "hermes_api": 8642, "broker_http": 8660,
              "preview": {"base": 18200, "per_zip": 10, "count": 6}, "docker_subnet_base": "172.30"},
    "network": {"public_url": "", "serve_port": 6443},   # a home without an address prints no link
    "host": {"name": "alien", "kind": "server", "sandbox_image": "taffy/zip-sandbox:2026-10-08-v1",
             "lead_backend": "docker"},
    "plan": {"claude": {"mode": "own", "command": "claude"},
             "chatgpt": {"mode": "own", "codex_command": "~/.local/bin/codex", "codex_base_url": None},
             "connectors": "none", "frugal": False},
    "mail": {"accounts": []},
    "calendars": [],
    "integrations": {"tally": None, "trello": None, "whisper_url": "http://10.0.0.65:11437"},
    "judge": {"enabled": False},
    "push": {"key_dir": "~/.config/taffy-push", "team_id": "YOUR_TEAM_ID", "topic": "com.example.Taffy"},
    "backup": {"repo": "~/backups/taffy-restic", "drop": None, "mac_copy_expected": False},
    "zips": {"max": None, "container_memory_mb": None},
    "google": {"client_id": None},
}

# Colin's own facts: the defaults of his home (no file, a file with home_id "colin", or his own file without one),
# equal to what the code held before home.json existed.
COLIN_FACTS = {
    "person": {"name": "Colin O'Brien", "first_name": "Colin", "pronoun_free": False},
    "paths": {"background_docs": ["~/taffy/shared/background-notes.md",
                                  "~/taffy/shared/background-record.txt"]},
    "network": {"public_url": "https://alien.tail0000.ts.net:6443"},
    "host": {"lead_backend": "local"},
    "plan": {"connectors": "claude"},
    "mail": {"accounts": [
        {"id": "personal", "address": "jdoe1@example.com", "label": "personal", "default": True,
         "owner_zip": "default", "kind": "imap"},
        {"id": "lakeview", "address": "jd910@lakeview.test", "label": "school", "owner_zip": "school", "kind": "imap"},
        {"id": "goodworks", "address": "colin@goodworks.test", "label": "Good Works work",
         "owner_zip": "clients", "kind": "imap"},
    ]},
    "integrations": {
        "tally": {"url": "http://127.0.0.1:18930", "env_file": "~/tally/server/.env"},
        "trello": {"board_id": "AbCd1234", "name": "Colin | School & Projects",
                   "url": "https://trello.com/b/AbCd1234/colin-school-projects", "lists": ["Doing (one task)", "Next"]},
    },
    "judge": {"enabled": True},
    "backup": {"mac_copy_expected": True},
}


def _merge(base: dict, over: dict) -> dict:
    """`over` laid over `base`: dictionaries merge key by key; lists, scalars and an explicit null replace."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def os_home() -> Path:
    """The running user's home directory (not $HOME: the Zips may run with a per-profile HOME)."""
    return Path(os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir)   # the hub's config.HOME


def settings_path() -> Path:
    env = os.environ.get(ENV, "").strip()
    return Path(env).expanduser() if env else os_home() / "taffy" / "home.json"


# The owner's user (Colin's) and the one file that may leave home_id out and still be his home: his own
# ~/taffy/home.json. The hub's reader and zero-home-lib.py use the same user and rule.
OWNER_USER = os.environ.get("ZERO_OWNER_USER", "jdoe")
NO_ID = "unnamed"   # the home_id a file without one reads as, anywhere but the owner's own file


def owner_file() -> Path:
    try:
        return Path(pwd.getpwnam(OWNER_USER).pw_dir) / "taffy" / "home.json"
    except KeyError:
        return Path("/home") / OWNER_USER / "taffy" / "home.json"


def _colin(data: dict | None, path: Path | None) -> bool:
    """No file is Colin's home, and so is a file whose home_id is "colin". A file without a home_id is his only when it
    is his own file at its known path; anywhere else (or pinned by a test) it is another home's."""
    if data is None:
        return True
    hid = str(data.get("home_id") or "")
    if hid:
        return hid == COLIN
    try:
        return path is not None and os.path.realpath(path) == os.path.realpath(owner_file())
    except (OSError, ValueError):
        return False


# The plugin imports this module as part of its package, while consent.py and safe_files.py (which the hub loads by
# path) load it on their own: every copy keeps its pins in one shared place, so a test's pin reaches all of them.
_STATE = sys.modules.setdefault("_zero_home_state", types.ModuleType("_zero_home_state"))
if not hasattr(_STATE, "pinned"):
    _STATE.pinned = []
_pinned: list = _STATE.pinned
_cache: dict = {}


# What a file that exists but cannot be read counts as, when no good copy of it was read before: a home that is
# not Colin's, so none of his facts (name, mailboxes, Tally, Trello, background documents, connectors) reach it.
# The hub stops on such a file instead (hub/taffy_hub/home.py); a tool call must not crash, so the plugin fails
# closed. Night review, Oct 9: an unreadable stranger's file used to read as "no file", which is Colin's home.
UNREADABLE = {"home_id": "unreadable"}


def raw() -> dict | None:
    """The settings file as written, or None when there is none. A file that cannot be read as an object keeps the
    last good copy of the same file, or else reads as UNREADABLE (never as Colin's home)."""
    if _pinned:
        return copy.deepcopy(_pinned[-1][0])
    path = settings_path()
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        st = None
    key = (str(path), st.st_mtime_ns, st.st_size) if st else (str(path), None, None)
    if st and _cache.get("key") == key:
        return copy.deepcopy(_cache["raw"])
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        good = _cache.get("good")
        if good is not None and good[0] == str(path):
            return copy.deepcopy(good[1])
        return dict(UNREADABLE)
    _cache.update(key=key, raw=data, good=(str(path), data))
    return copy.deepcopy(data)


def load() -> dict:
    """The whole settings object: the file over the defaults (Colin's facts included only in his home)."""
    data = raw()
    colin = _colin(data, None if _pinned else settings_path())
    base = _merge(DEFAULTS, COLIN_FACTS) if colin else copy.deepcopy(DEFAULTS)
    out = _merge(base, data or {})
    if not str((data or {}).get("home_id") or ""):
        out["home_id"] = COLIN if colin else NO_ID   # the defaults' "colin" never names another home
    return out


@contextlib.contextmanager
def pinned(settings: dict | None):
    """Tests: read `settings` as the file (None: no file at all, so Colin's defaults) for the duration."""
    _pinned.append((copy.deepcopy(settings),))   # a pinned None stands for "no file": raw() returns None
    _person_cache.clear()
    try:
        yield
    finally:
        _pinned.pop()
        _person_cache.clear()


def colin_settings() -> dict:
    """Colin's home written out in full: what the integration step writes as his home.json, and what tests pin."""
    return _merge(DEFAULTS, COLIN_FACTS)


def get(dotted: str, default=None):
    cur = load()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def home_id() -> str:
    return str(get("home_id") or COLIN)


def is_colin() -> bool:
    return home_id() == COLIN


# ── Paths ─────────────────────────────────────────────────────────────────────────────────────────────────

def expand(value, real_home=None) -> Path:
    """A settings path as a real path: "~" is the running user's home, or `real_home` when given (the plugin passes
    consent.REAL_HOME, so tests that move the home move every path with it)."""
    text = str(value or "")
    base = Path(real_home) if real_home is not None else os_home()
    if text == "~":
        return base
    if text.startswith("~/"):
        return base / text[2:]
    return Path(text)


def path(key: str, real_home=None) -> Path:
    return expand(get(f"paths.{key}") or DEFAULTS["paths"].get(key) or "", real_home)


def background_docs(real_home=None) -> list[Path]:
    return [expand(p, real_home) for p in (get("paths.background_docs") or []) if str(p or "").strip()]


def hub_url() -> str:
    """The hub's address for the plugin's internal calls."""
    return f"http://127.0.0.1:{int(get('ports.hub') or 8650)}/internal/"


def preview_range() -> range:
    p = get("ports.preview") or {}
    try:
        base, per, count = int(p.get("base")), int(p.get("per_zip")), int(p.get("count"))
    except (TypeError, ValueError):
        base, per, count = 18200, 10, 6
    return range(base, base + per * count)


# ── The person ────────────────────────────────────────────────────────────────────────────────────────────

_person_cache: dict = {}


def _onboarding_person(real_home=None) -> dict:
    """The name the hub keeps from onboarding (hub-state/person.json), as the plugin read it before home.json."""
    p = path("hub_state", real_home) / "person.json"
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns)
        if _person_cache.get("key") != key:
            data = json.loads(p.read_text())
            _person_cache.update(key=key, data=data if isinstance(data, dict) else {})
        return dict(_person_cache["data"])
    except (OSError, ValueError):
        return {}


def full_name(real_home=None) -> str:
    """The person's name: the settings file's, then the onboarding answer's, then the home's default."""
    written = ((raw() or {}).get("person") or {})
    if str(written.get("name") or "").strip():
        return str(written["name"]).strip()
    on = str(_onboarding_person(real_home).get("name") or "").strip()
    if on:
        return on
    return str(get("person.name") or "").strip() or "the person"


def first_name(real_home=None) -> str:
    written = ((raw() or {}).get("person") or {})
    if str(written.get("first_name") or "").strip():
        return str(written["first_name"]).strip()
    if str(written.get("name") or "").strip():
        return str(written["name"]).split()[0]
    on = _onboarding_person(real_home)
    if str(on.get("first_name") or "").strip():
        return str(on["first_name"]).strip()
    if str(on.get("name") or "").strip():
        return str(on["name"]).split()[0]
    first = str(get("person.first_name") or "").strip()
    if first:
        return first
    name = str(get("person.name") or "").strip()
    return name.split()[0] if name else "the person"


def pronoun_free() -> bool:
    return bool(get("person.pronoun_free"))


def timezone() -> str:
    return str(get("person.timezone") or "America/New_York")


def host_name() -> str:
    return str(get("host.name") or "alien")


def units_line() -> str:
    """The units and spelling the person uses, as one phrase for the Zips' style rules."""
    units, spelling, clock = (get("locale.units") or "us"), (get("locale.spelling") or "american"), \
        (get("locale.clock") or "12h")
    spell = "British spelling" if str(spelling).lower().startswith("brit") else "American spelling"
    if str(units).lower() == "metric":
        measures = "Celsius, kilometers, meters and centimeters, kilograms"
    else:
        measures = "Fahrenheit, miles, feet and inches, pounds, dollars"
    hours = "24-hour times" if str(clock).lower().startswith("24") else "12-hour times"
    return f"{spell}, and {first_name()}'s units: {measures}, {hours}."


# ── What the home can use ─────────────────────────────────────────────────────────────────────────────────

def connectors() -> str:
    """"claude", "chatgpt" or "none": whose plan carries connectors (a shared plan never does). Anything else is
    "none", as the hub reads it."""
    c = str(get("plan.connectors") or "").strip().lower()
    return c if c in ("claude", "chatgpt") else "none"


def connectors_on() -> bool:
    return connectors() != "none"


def tally() -> dict | None:
    t = get("integrations.tally")
    if not t:
        return None
    base = (COLIN_FACTS["integrations"]["tally"])
    return {**base, **t} if isinstance(t, dict) else dict(base)


def trello() -> dict | None:
    t = get("integrations.trello")
    if not t:
        return None
    if isinstance(t, str):
        return {"board_id": t}
    return dict(t) if isinstance(t, dict) and t.get("board_id") else None


def judge_enabled() -> bool:
    return bool(get("judge.enabled"))


def mail_accounts() -> list[dict]:
    """The home's mailboxes, each with id, address, label, kind ("imap" through himalaya, or "google_oauth")."""
    out = []
    for a in get("mail.accounts") or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", aid):
            continue
        out.append({**a, "id": aid, "address": str(a.get("address") or "").strip(),
                    "kind": str(a.get("kind") or "imap").strip().lower()})
    return out


def calendars(real_home=None) -> list[dict]:
    """The home's calendars (kind "caldav", "ical" or "google_oauth"): home.json's list, plus any the hub wrote to
    hub-state/calendars.json that home.json does not name."""
    items = [c for c in (get("calendars") or []) if isinstance(c, dict)]
    try:
        extra = json.loads((path("hub_state", real_home) / "calendars.json").read_text())
        if isinstance(extra, dict):
            extra = extra.get("calendars") or []
        if isinstance(extra, list):
            known = {str(c.get("id")) for c in items}
            items += [c for c in extra if isinstance(c, dict) and str(c.get("id")) not in known]
    except (OSError, ValueError):
        pass
    out = []
    for c in items:
        cid = str(c.get("id") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", cid):
            out.append({**c, "id": cid, "kind": str(c.get("kind") or "caldav").strip().lower()})
    return out


def zips_max() -> int | None:
    try:
        n = get("zips.max")
        return int(n) if n not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ── Words ─────────────────────────────────────────────────────────────────────────────────────────────────

_PRONOUNS = [(re.compile(r"\bhimself\b"), "themselves"), (re.compile(r"\bHimself\b"), "Themselves"),
             (re.compile(r"\b[Hh]e's\b"), "{first}'s"), (re.compile(r"\b[Hh]e\b"), "{first}"),
             (re.compile(r"\b[Hh]is\b"), "{first}'s"), (re.compile(r"\b[Hh]im\b"), "{first}")]


def _braces(s: str) -> str:
    """A value put into a template that str.format reads afterwards: its braces are literal ("{Al}" stays "{Al}")."""
    return s.replace("{", "{{").replace("}", "}}")


def personalize(text: str, real_home=None, *, for_format: bool = False) -> str:
    """Text written for Colin's home, said for this home: his name becomes the person's, "he", "his" and "him"
    become the person's name where the person goes without pronouns, the time zone and the server's name become the
    home's. Only for text this code wrote: never pass a person's words or a page through it. In Colin's home with
    no file it returns the text unchanged. `for_format`: the result is a str.format template (say), so the braces
    of a name put in are doubled and read back as themselves."""
    if not text:
        return text
    full, first = full_name(real_home), first_name(real_home)
    if for_format:
        full, first = _braces(full), _braces(first)
    out = text
    if full != "Colin O'Brien":
        out = out.replace("Colin O'Brien", full)
    if first != "Colin":
        out = re.sub(r"\bColin\b", lambda _m: first, out)
    if pronoun_free():
        for pat, repl in _PRONOUNS:
            out = pat.sub(lambda _m, r=repl: r.replace("{first}", first), out)
    tz = timezone()
    if tz != "America/New_York":
        out = out.replace("America/New_York", _braces(tz) if for_format else tz)
    host = host_name()
    if host != "alien":
        host = _braces(host) if for_format else host
        out = re.sub(r"\balien\b", lambda _m: host, out)
    return out


def say(template: str, **values) -> str:
    """A message from a template written for Colin's home: personalized first, then filled with `values` (which are
    never personalized, so a site, a request or a summary stays exactly as it was). A name with a brace in it is
    said as written."""
    return personalize(template, for_format=True).format(**values)


def personalize_schema(schema: dict) -> dict:
    """A tool schema with every description personalized, for registration."""
    def walk(v, key=""):
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        if isinstance(v, str) and key == "description":
            return personalize(v)
        return v
    return walk(copy.deepcopy(schema))


def count_word(n: int) -> str:
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    return words[n] if 0 <= n < len(words) else str(n)
