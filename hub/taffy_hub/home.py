""























import copy
import json
import logging
import os
import pwd
import threading
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("taffy.home")

USER_HOME = Path(os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir)
VERSION = 1



DEFAULTS: dict = {
    "version": 1,
    "home_id": "unnamed",
    "owner_home": False,
    "person": {
        "name": "", "first_name": "",
        "timezone": "America/New_York",
        "quiet_hours": {"start": 23, "end": 7},
        "pronoun_free": True,
    },
    "locale": {"units": "us", "spelling": "american", "clock": "12h"},
    "paths": {
        "hermes_home": "~/.taffy", "hermes_bin": "~/taffy/bin/taffy",
        "hub_state": "~/taffy/hub-state", "work": "~/taffy/work", "shared": "~/taffy/shared",
        "plugin": "~/taffy/plugins/taffy-tools", "owner": "~/taffy/owner",
        "background_docs": [],
    },
    "ports": {"hub": 8650, "hermes_api": 8642, "broker_http": 8660,

              "preview": {"base": 18200, "per_zip": 10, "count": 10}, "docker_subnet_base": "172.30"},
    "network": {"public_url": "", "serve_port": 6443},
    "host": {"name": "", "kind": "server", "sandbox_image": "taffy/zip-sandbox:2026-10-08-v1",
             "lead_backend": "docker"},
    "plan": {
        "claude": {"mode": "own", "command": "claude"},
        "chatgpt": {"mode": "own", "codex_command": "~/.local/bin/codex", "codex_base_url": None},
        "connectors": "none",
        "frugal": False,

        "owner_name": None,
    },
    "mail": {"accounts": []},
    "calendars": [],
    "integrations": {"tally": None, "trello": None, "whisper_url": None},
    "judge": {"enabled": False},

    "google": {"client_id": "846197417273-d38hoec6rld9eec2ppbubvhggfh0qb4a.apps.googleusercontent.com"},


    "apps": {"trello": {"key": "00514c49fcec6be5b932eef57b09ebc1"},

             "github": {"client_id": "Ov23ct9lO7FLHzpqHlF6"},


             "dropbox": {"app_key": "ejyqj553i4j53zj"},


             "linear": {"client_id": "e817e7427f2a61441078d3a8a8231402"},


             "microsoft": {"client_id": "76ae834f-0883-46c9-8b47-bdbfdba8e267"},


             "relay": {"url": "https://relay-production-0e56.up.railway.app"},

             "notion": {"client_id": "3f7d872b-594c-8138-b45a-0037e7ad772a"}},
    "push": {"key_dir": "~/.config/taffy-push", "team_id": "Q3Q2BF22GL", "topic": "com.colinobrien.Taffy"},
    "backup": {"repo": "~/backups/taffy-restic", "drop": None, "mac_copy_expected": False},
    "zips": {"max": None, "container_memory_mb": None},

    "cache": {"keep_warm": False},



    "permissions": {"hard_stops": False},
}

NO_ID = "unnamed"


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


def unmigrated_owner(p: Path | None, raw_file: dict | None) -> bool:
    ""

    if FILE is not None or p is None or (raw_file or {}).get("owner_home") is True:
        return False
    owner = owner_file()
    try:
        return owner is not None and os.path.realpath(p) == os.path.realpath(owner)
    except (OSError, ValueError):
        return False



FILE: Path | None = None

_lock = threading.Lock()
_cache: dict = {"key": None, "data": None, "raw": None}


class HomeError(RuntimeError):
    pass


UNMIGRATED = ("This server's owner home has no \"owner_home\" mark in its settings file, so it would run on a "
              "stranger's defaults. Run hub/scripts/migrate-owner-home.py on the server, then start the hub again.")


def path() -> Path:
    ""
    if FILE is not None:
        return Path(FILE)
    env = os.environ.get("ZERO_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    return USER_HOME / "taffy" / "home.json"


def merge(base: dict, over: dict) -> dict:
    ""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read(p: Path) -> dict | None:
    ""
    try:
        text = p.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise HomeError(f"cannot read {p}: {e.strerror}") from None
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise HomeError(f"{p} is not valid JSON ({e})") from None
    if not isinstance(raw, dict):
        raise HomeError(f"{p} must hold one JSON object")
    if int(raw.get("version") or VERSION) > VERSION:
        raise HomeError(f"{p} is version {raw.get('version')}; this hub reads version {VERSION}")
    return raw


def load() -> dict:
    ""
    p = path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(p), None, None)
    with _lock:
        if _cache["key"] == key and _cache["data"] is not None:
            return _cache["data"]
        try:
            raw = _read(p)
            if unmigrated_owner(p, raw):
                log.critical("%s", UNMIGRATED)
                raise HomeError(UNMIGRATED)
        except HomeError:
            if _cache["data"] is not None and _cache["key"] and _cache["key"][0] == str(p):
                log.warning("home settings unreadable; keeping the last good copy", exc_info=True)
                return _cache["data"]
            raise
        data = merge(DEFAULTS, raw or {})
        if not str((raw or {}).get("home_id") or ""):
            data["home_id"] = NO_ID
        _cache.update(key=key, data=data, raw=raw)
        return data


def raw() -> dict | None:
    ""
    load()
    return copy.deepcopy(_cache["raw"])


def exists() -> bool:
    return path().is_file()


def reset_cache() -> None:
    with _lock:
        _cache.update(key=None, data=None, raw=None)


def get(dotted: str, default=None):
    ""
    cur = load()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return copy.deepcopy(cur)


def save(changes: dict, whole: tuple[str, ...] = ()) -> dict:
    ""

    p = path()
    with _lock:
        current = _read(p)
    base = current if current is not None else copy.deepcopy(DEFAULTS)
    base = {k: v for k, v in base.items() if k not in whole}
    new = merge(base, changes)
    new["version"] = VERSION
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o644)
    tmp.replace(p)
    reset_cache()
    return load()


def expand(value: str | os.PathLike) -> Path:
    ""
    s = str(value)
    if s == "~" or s.startswith("~/"):
        return USER_HOME / s[2:] if len(s) > 1 else USER_HOME
    return Path(s)


def path_of(key: str) -> Path:
    ""
    return expand(get(f"paths.{key}") or DEFAULTS["paths"][key])




def person_name(full: bool = True) -> str:
    ""
    p = get("person") or {}
    name = " ".join(str(p.get("name") or "").split())
    if full:
        return name or str(p.get("first_name") or "").strip() or "the person"
    return str(p.get("first_name") or "").strip() or (name.split()[0] if name else "the person")


def timezone_name() -> str:
    tz = str(get("person.timezone") or "").strip() or DEFAULTS["person"]["timezone"]
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown time zone %r in home settings; using %s", tz, DEFAULTS["person"]["timezone"])
        return DEFAULTS["person"]["timezone"]
    return tz


def zone() -> ZoneInfo:
    return ZoneInfo(timezone_name())



_TZ_WORDS = {
    "America/New_York": "Eastern", "America/Detroit": "Eastern", "America/Indiana/Indianapolis": "Eastern",
    "America/Kentucky/Louisville": "Eastern", "America/Toronto": "Eastern",
    "America/Chicago": "Central", "America/Winnipeg": "Central",
    "America/Denver": "Mountain", "America/Boise": "Mountain", "America/Edmonton": "Mountain",
    "America/Phoenix": "Arizona time",
    "America/Los_Angeles": "Pacific", "America/Vancouver": "Pacific",
    "America/Anchorage": "Alaska time", "Pacific/Honolulu": "Hawaii time",
    "Europe/London": "UK time", "UTC": "UTC", "Etc/UTC": "UTC",
}


def tz_word() -> str:
    ""
    name = timezone_name()
    if name in _TZ_WORDS:
        return _TZ_WORDS[name]
    return name.rsplit("/", 1)[-1].replace("_", " ") + " time"


def tz_phrase() -> str:
    ""
    name = timezone_name()
    city = name.rsplit("/", 1)[-1].replace("_", " ")
    word = tz_word()
    return word if word.lower().startswith(city.lower()) or word == "UTC" else f"{word} ({city})"


class _HomeZone(tzinfo):
    ""


    def _z(self) -> ZoneInfo:
        return zone()

    def utcoffset(self, dt):
        return self._z().utcoffset(dt.replace(tzinfo=None) if dt is not None else None)

    def dst(self, dt):
        return self._z().dst(dt.replace(tzinfo=None) if dt is not None else None)

    def tzname(self, dt):
        return self._z().tzname(dt.replace(tzinfo=None) if dt is not None else None)

    def fromutc(self, dt: datetime) -> datetime:
        z = self._z()
        return z.fromutc(dt.replace(tzinfo=z)).replace(tzinfo=self)

    def __repr__(self) -> str:
        return f"home.TZ({timezone_name()})"


TZ = _HomeZone()


def quiet_hours() -> tuple[int, int]:
    ""
    q = get("person.quiet_hours") or {}
    try:
        start, end = int(q.get("start", 23)) % 24, int(q.get("end", 7)) % 24
    except (TypeError, ValueError):
        start, end = 23, 7
    return start, end


def hour_words(h: int) -> str:
    ""
    h %= 24
    if h == 0:
        return "midnight"
    if h == 12:
        return "noon"
    return f"{h % 12 or 12} {'a.m.' if h < 12 else 'p.m.'}"


def units_sentence() -> str:
    ""
    units = str(get("locale.units") or "us")
    if units == "metric":
        return "Celsius and metric units"
    if units == "uk":
        return "Celsius, miles and UK units"
    return "Fahrenheit and US units"


def spelling() -> str:
    return str(get("locale.spelling") or "american")




def plan_kind() -> str:
    ""
    p = get("plan") or {}
    modes = {str((p.get(k) or {}).get("mode") or "own") for k in ("claude", "chatgpt")}
    return "shared" if "broker" in modes else "own"


def connectors() -> str:
    ""

    c = str(get("plan.connectors") or "").strip().lower()
    return c if c in ("claude", "chatgpt") else "none"


def is_google(account: dict) -> bool:
    ""
    return str(account.get("kind") or "").lower() == "google_oauth" or account.get("auth") == "google"


def is_google_calendar(cal: dict) -> bool:
    ""
    return str(cal.get("kind") or "").lower() in ("google_oauth", "google")


def calendar_address(cal: dict) -> str:
    ""
    return str(cal.get("address") or cal.get("account") or "").strip().lower()


ZIP_MAIL_ENV = "ZERO_ZIP_MAIL_REGISTRY"


def zip_mail_file() -> Path | None:
    ""


    env = os.environ.get(ZIP_MAIL_ENV, "").strip()
    if env:
        return Path(env)
    if FILE is not None:
        return None
    from . import config
    return config.HUB_DIR / "zip-mail.json"


def zip_mail_registry() -> list[dict]:
    ""
    f = zip_mail_file()
    try:
        rows = json.loads(f.read_text()) if f else []
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("id") and r.get("address") and r.get("owner_zip")] \
        if isinstance(rows, list) else []


def zip_name(bot_id: str) -> str:
    from . import config
    bot = config.BOTS_BY_ID.get(bot_id)
    return bot.name if bot else bot_id


def zip_mail_accounts() -> list[dict]:
    ""

    return [{"id": str(r["id"]), "address": str(r["address"]),
             "label": zip_name(str(r["owner_zip"])) + "'s own address", "kind": "zip_mail", "provider": "zero",
             "zips_own": True, "owner_zip": str(r["owner_zip"])} for r in zip_mail_registry()]


def mail_accounts() -> list[dict]:
    ""

    accts = [a for a in get("mail.accounts") or [] if isinstance(a, dict) and a.get("id") and a.get("address")]
    known = {str(a["id"]) for a in accts}
    return accts + [z for z in zip_mail_accounts() if z["id"] not in known]


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


def mail_owner(account_id: str) -> str:
    ""
    for a in mail_accounts():
        if str(a.get("id") or "") == str(account_id or ""):
            return str(a.get("owner_zip") or "") if a.get("zips_own") or a.get("kind") == "zip_mail" else ""
    return ""


def alias_owner(account: dict, address: str) -> str:
    ""
    address = str(address or "").strip().lower()
    for x in account.get("aliases") or []:
        if isinstance(x, dict) and str(x.get("address") or "").strip().lower() == address:
            return str(x.get("owner_zip") or "")
    return ""


def calendars() -> list[dict]:
    cals = get("calendars") or []
    return [c for c in cals if isinstance(c, dict) and c.get("id")]


def trello() -> dict | None:
    t = get("integrations.trello")
    if isinstance(t, str) and t:
        return {"board_id": t}
    return t if isinstance(t, dict) and t.get("board_id") else None


def tally() -> dict | None:
    t = get("integrations.tally")
    return t if isinstance(t, dict) and t.get("url") else None


def is_owner_home() -> bool:
    ""

    return plan_kind() == "own" and get("owner_home") is True


def runs_on_mac() -> set[str]:
    ""


    return {str(r).lower() for r in (get("github.runs_on_mac") or [])}


def summary() -> dict:
    ""
    q = quiet_hours()
    return {
        "home_id": get("home_id"),
        "person": {"name": person_name(), "first_name": person_name(False), "timezone": timezone_name(),
                   "quiet_hours": {"start": q[0], "end": q[1]}},
        "locale": {"units": get("locale.units") or "us", "spelling": spelling(),
                   "clock": get("locale.clock") or "12h"},
        "plan": plan_kind(),


        "connectors": (get("plan.brain") or "claude") if get("plan.connectors_on") is False else connectors(),
        "host": {"name": get("host.name"), "kind": get("host.kind")},
    }


def next_quiet_end(now: datetime | None = None) -> datetime:
    ""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    end = now.replace(hour=quiet_hours()[1], minute=0, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=1)
    return end
