""

















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
NO_ID = "unnamed"

TEMPLATE_FIRST = "Colin"
FULL_NAME = "@FULL_NAME@"


DEFAULTS = {
    "version": 1,
    "home_id": NO_ID,
    "owner_home": False,
    "person": {"name": "", "first_name": "", "timezone": "America/New_York",
               "quiet_hours": {"start": 23, "end": 7}, "pronoun_free": True},
    "locale": {"units": "us", "spelling": "american", "clock": "12h"},
    "paths": {"hermes_home": "~/.taffy", "hermes_bin": "~/taffy/bin/taffy", "hub_state": "~/taffy/hub-state",
              "work": "~/taffy/work", "shared": "~/taffy/shared", "plugin": "~/taffy/plugins/taffy-tools",
              "owner": "~/taffy/owner", "background_docs": []},
    "ports": {"hub": 8650, "hermes_api": 8642, "broker_http": 8660,

              "preview": {"base": 18200, "per_zip": 10, "count": 10}, "docker_subnet_base": "172.30"},
    "network": {"public_url": "", "serve_port": 6443},
    "host": {"name": "", "kind": "server", "sandbox_image": "taffy/zip-sandbox:2026-10-08-v1",
             "lead_backend": "docker"},
    "plan": {"claude": {"mode": "own", "command": "claude"},
             "chatgpt": {"mode": "own", "codex_command": "~/.local/bin/codex", "codex_base_url": None},
             "connectors": "none", "frugal": False},
    "mail": {"accounts": []},
    "calendars": [],
    "integrations": {"tally": None, "trello": None, "whisper_url": None},
    "judge": {"enabled": False},
    "push": {"key_dir": "~/.config/taffy-push", "team_id": "Q3Q2BF22GL", "topic": "com.colinobrien.Taffy"},
    "backup": {"repo": "~/backups/taffy-restic", "drop": None, "mac_copy_expected": False},
    "zips": {"max": None, "container_memory_mb": None},
    "google": {"client_id": None},

    "permissions": {"hard_stops": False},
}


TALLY_DEFAULTS = {"url": "http://127.0.0.1:18930", "env_file": "~/tally/server/.env"}


def _merge(base: dict, over: dict) -> dict:
    ""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def os_home() -> Path:
    ""
    return Path(os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir)


def settings_path() -> Path:
    env = os.environ.get(ENV, "").strip()
    return Path(env).expanduser() if env else os_home() / "taffy" / "home.json"


def _srv() -> Path:
    return Path(os.environ.get("ZERO_SRV", "/srv/zero"))


def owner_user() -> str:
    ""

    named = os.environ.get("ZERO_OWNER_USER", "").strip()
    if named:
        return named
    try:
        homes = json.loads((_srv() / "homes.json").read_text()).get("homes") or {}
        return next((str(e["user"]) for e in homes.values() if isinstance(e, dict) and e.get("owner") and e.get("user")),
                    "")
    except (OSError, ValueError, AttributeError):
        return ""


def owner_file() -> Path | None:
    ""
    user = owner_user()
    if not user:
        return None
    try:
        return Path(pwd.getpwnam(user).pw_dir) / "taffy" / "home.json"
    except KeyError:
        return Path("/home") / user / "taffy" / "home.json"


def unmigrated_owner(path: Path, data: dict | None) -> bool:
    ""

    if (data or {}).get("owner_home") is True:
        return False
    owner = owner_file()
    try:
        return owner is not None and os.path.realpath(path) == os.path.realpath(owner)
    except (OSError, ValueError):
        return False




_STATE = sys.modules.setdefault("_zero_home_state", types.ModuleType("_zero_home_state"))
if not hasattr(_STATE, "pinned"):
    _STATE.pinned = []
_pinned: list = _STATE.pinned
_cache: dict = {}




UNREADABLE = {"home_id": "unreadable"}


def raw() -> dict | None:
    ""


    if _pinned:
        return copy.deepcopy(_pinned[-1][0])
    path = settings_path()
    data = _read_file(path)
    if unmigrated_owner(path, data):
        return dict(UNREADABLE)
    return data


def _read_file(path: Path) -> dict | None:
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
    ""
    data = raw()
    out = _merge(DEFAULTS, data or {})
    if not str((data or {}).get("home_id") or ""):
        out["home_id"] = NO_ID
    return out


@contextlib.contextmanager
def pinned(settings: dict | None, zip_mail=None):
    ""

    _pinned.append((copy.deepcopy(settings), zip_mail))
    _person_cache.clear()
    try:
        yield
    finally:
        _pinned.pop()
        _person_cache.clear()


def get(dotted: str, default=None):
    cur = load()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def home_id() -> str:
    return str(get("home_id") or NO_ID)


def is_owner_home() -> bool:
    ""
    return get("owner_home") is True




def expand(value, real_home=None) -> Path:
    ""

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
    ""
    return f"http://127.0.0.1:{int(get('ports.hub') or 8650)}/internal/"



MAX_SLOTS = 250



OVERFLOW_PORTS = 25000


def preview_ports(slot: int) -> tuple[int, int]:
    ""
    base, per, count = _preview_shape()
    first = base + slot * per if slot < count else OVERFLOW_PORTS + (slot - count) * per
    return first, first + per - 1


def _preview_shape() -> tuple[int, int, int]:
    p = get("ports.preview") or {}
    try:
        return int(p.get("base")), int(p.get("per_zip")), int(p.get("count"))
    except (TypeError, ValueError):
        return 18200, 10, 10


def preview_range() -> frozenset:
    ""
    base, per, count = _preview_shape()
    ports = set(range(base, base + per * count))
    if is_owner_home():
        ports.update(range(OVERFLOW_PORTS, OVERFLOW_PORTS + per * (MAX_SLOTS - count)))
    return frozenset(ports)




_person_cache: dict = {}


def _onboarding_person(real_home=None) -> dict:
    ""
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
    ""
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
    return str(get("host.name") or "server")


def units_line() -> str:
    ""
    units, spelling, clock = (get("locale.units") or "us"), (get("locale.spelling") or "american"), \
        (get("locale.clock") or "12h")
    spell = "British spelling" if str(spelling).lower().startswith("brit") else "American spelling"
    if str(units).lower() == "metric":
        measures = "Celsius, kilometers, meters and centimeters, kilograms"
    else:
        measures = "Fahrenheit, miles, feet and inches, pounds, dollars"
    hours = "24-hour times" if str(clock).lower().startswith("24") else "12-hour times"
    return f"{spell}, and {first_name()}'s units: {measures}, {hours}."




def connectors() -> str:
    ""

    c = str(get("plan.connectors") or "").strip().lower()
    return c if c in ("claude", "chatgpt") else "none"


def connectors_on() -> bool:
    return connectors() != "none"


def tally() -> dict | None:
    t = get("integrations.tally")
    if not t:
        return None
    return {**TALLY_DEFAULTS, **t} if isinstance(t, dict) else dict(TALLY_DEFAULTS)


def trello() -> dict | None:
    t = get("integrations.trello")
    if not t:
        return None
    if isinstance(t, str):
        return {"board_id": t}
    return dict(t) if isinstance(t, dict) and t.get("board_id") else None


def judge_enabled() -> bool:
    return bool(get("judge.enabled"))


ZIP_MAIL_ENV = "ZERO_ZIP_MAIL_REGISTRY"


def zip_mail_file(real_home=None) -> Path | None:
    ""


    env = os.environ.get(ZIP_MAIL_ENV, "").strip()
    if env:
        return Path(env)
    if _pinned:
        return Path(_pinned[-1][1]) if _pinned[-1][1] else None
    return path("hub_state", real_home) / "zip-mail.json"


def _zip_name(bot_id: str) -> str:
    ""
    if __package__:
        try:
            from . import team
            return team.zip_name(bot_id)
        except Exception:
            pass
    return bot_id


def zip_mail_accounts(real_home=None) -> list[dict]:
    ""
    f = zip_mail_file(real_home)
    try:
        rows = json.loads(f.read_text()) if f else []
    except (OSError, ValueError):
        rows = []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("id") and r.get("address") and r.get("owner_zip"):
            out.append({"id": str(r["id"]), "address": str(r["address"]),
                        "label": f"{_zip_name(str(r['owner_zip']))}'s own address", "kind": "zip_mail",
                        "provider": "zero", "zips_own": True, "owner_zip": str(r["owner_zip"])})
    return out


def mail_accounts(real_home=None) -> list[dict]:
    ""

    out = []
    for a in get("mail.accounts") or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", aid):
            continue
        out.append({**a, "id": aid, "address": str(a.get("address") or "").strip(),
                    "kind": str(a.get("kind") or "imap").strip().lower()})
    known = {a["id"] for a in out}
    return out + [z for z in zip_mail_accounts(real_home)
                  if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", z["id"]) and z["id"] not in known]


APPLE_MAIL = ("icloud.com", "me.com", "mac.com")


def mailbox_addresses(account: dict) -> list[str]:
    ""


    own = str(account.get("address") or "").strip().lower()
    out = [own]
    name, _, domain = own.partition("@")
    if domain in APPLE_MAIL:
        out += [f"{name}@{d}" for d in APPLE_MAIL]
    for x in account.get("aliases") or []:
        out.append(str((x.get("address") if isinstance(x, dict) else x) or "").strip().lower())
    return list(dict.fromkeys(a for a in out if "@" in a))


def alias_owner(account: dict, address: str) -> str:
    ""
    address = str(address or "").strip().lower()
    for x in account.get("aliases") or []:
        if isinstance(x, dict) and str(x.get("address") or "").strip().lower() == address:
            return str(x.get("owner_zip") or "")
    return ""


def calendars(real_home=None) -> list[dict]:
    ""

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




_PRONOUNS = [(re.compile(r"\bhimself\b"), "themselves"), (re.compile(r"\bHimself\b"), "Themselves"),
             (re.compile(r"\b[Hh]e's\b"), "{first}'s"), (re.compile(r"\b[Hh]e\b"), "{first}"),
             (re.compile(r"\b[Hh]is\b"), "{first}'s"), (re.compile(r"\b[Hh]im\b"), "{first}")]


def _braces(s: str) -> str:
    ""
    return s.replace("{", "{{").replace("}", "}}")


def personalize(text: str, real_home=None, *, for_format: bool = False) -> str:
    ""




    if not text:
        return text
    full, first = full_name(real_home), first_name(real_home)
    if for_format:
        full, first = _braces(full), _braces(first)
    out = text
    out = out.replace(FULL_NAME, full)
    if first != TEMPLATE_FIRST:
        out = re.sub(rf"\b{TEMPLATE_FIRST}\b", lambda _m: first, out)
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
    ""


    return personalize(template, for_format=True).format(**values)


def personalize_schema(schema: dict) -> dict:
    ""
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
