"""The home this hub serves: its settings file, `home.json` (docs/plans/2026-10-09-zero-for-anyone-plan.md, section 4).

Where the file is: `ZERO_HOME` in the environment names it (the systemd templates set it); without it,
`~/taffy/home.json` is tried; without that, DEFAULTS apply. DEFAULTS are the values alien had for Colin's home on
Oct 9, so a missing file means Colin's home behaves exactly as before the file existed.

How a file is read: it is laid over the defaults. Objects merge key by key; lists, strings, numbers and null replace
the default outright (an explicit `"tally": null` turns Tally off; `"accounts": []` means no mailboxes). The defaults
are DEFAULTS plus COLIN_FACTS in Colin's home (no file, `home_id` "colin", or his own ~/taffy/home.json without a
`home_id`), and DEFAULTS alone in any other home (a file elsewhere without `home_id` reads as home "unnamed"),
so a key missing from a stranger's file never brings Colin's name, mailboxes, board, Tally, documents or paid judge.
A file that exists but cannot be read stops the hub (HomeError) rather than fall back to Colin's values; once a good
copy was read, a later bad read keeps that copy.

The plugin (alien/plugins/taffy-tools/home.py) reads the same file with the same rules; a hub test loads both readers
on the same sample files and compares them.

`~` in a path is the running user's home from the password database, never $HOME (Zips run with a per-profile
HOME). Nothing here holds a password or a key: those stay where they live (himalaya `.pass` files, `.env`, vaults,
the push key, the Google token store under hub-state).
"""

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

USER_HOME = Path(os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir)   # see config.HOME
VERSION = 1

# Every key, with the values every home shares unless its file says otherwise (the plugin's reader,
# alien/plugins/taffy-tools/home.py, holds the same).
DEFAULTS: dict = {
    "version": 1,
    "home_id": "colin",
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
              "preview": {"base": 18200, "per_zip": 10, "count": 6}, "docker_subnet_base": "172.30"},
    "network": {"public_url": "", "serve_port": 6443},   # a home without an address prints no link
    "host": {"name": "alien", "kind": "server", "sandbox_image": "taffy/zip-sandbox:2026-10-08-v1",
             "lead_backend": "docker"},
    "plan": {
        "claude": {"mode": "own", "command": "claude"},
        "chatgpt": {"mode": "own", "codex_command": "~/.local/bin/codex", "codex_base_url": None},
        "connectors": "none",
        "frugal": False,
        # In a home on a shared plan: the first name of the person who shares it ("that Colin shares with you").
        "owner_name": None,
    },
    "mail": {"accounts": []},
    "calendars": [],
    "integrations": {"tally": None, "trello": None, "whisper_url": "http://10.0.0.65:11437"},
    "judge": {"enabled": False},
    # Zero's own iOS OAuth client (project zero-signin, CONFIGURE.md). Public, not a secret.
    "google": {"client_id": "000000000000-yourgoogleclientid.apps.googleusercontent.com"},
    # Zero's own Trello app, "Zero" (trello.com/power-ups/admin, Oct 11). Each person allows it on Trello's own page
    # (Connect Apps, trello.py); the key only names the app and is public, like the Google client ID above.
    "apps": {"trello": {"key": "YOUR_TRELLO_API_KEY"},
             # Zero's GitHub OAuth app (github.com/settings/developers, owner your-github-user): device flow, no secret.
             "github": {"client_id": "YOUR_GITHUB_CLIENT_ID"},
             # Zero's Dropbox app "Zero Assistant" (dropbox.com/developers, admin@example.com): PKCE, no secret,
             # redirect zero://oauth/dropbox, files.metadata.read and files.content.read.
             "dropbox": {"app_key": "YOUR_DROPBOX_APP_KEY"},
             # Zero's Linear OAuth app (workspace Fernhill, admin@example.com): public, PKCE, redirect
             # zero://oauth/linear.
             "linear": {"client_id": "YOUR_LINEAR_CLIENT_ID"},
             # Zero's Microsoft app (Entra, tenant yourtenant.onmicrosoft.com): work, school and personal
             # accounts, public client, redirect zero://oauth/microsoft, no secret. Scopes are asked at sign-in.
             "microsoft": {"client_id": "YOUR_MICROSOFT_CLIENT_ID"},
             # Zero's OAuth relay on Railway (oauth-relay/, project zero-oauth): the public return page for every
             # provider, and the code swap for apps whose secret it holds (Notion, Atlassian, Todoist).
             "relay": {"url": "https://relay.example.com"},
             # Zero's Notion OAuth connection (admin@example.com): any workspace, redirect <relay>/callback/notion.
             "notion": {"client_id": "YOUR_NOTION_CLIENT_ID"}},
    "push": {"key_dir": "~/.config/taffy-push", "team_id": "YOUR_TEAM_ID", "topic": "com.example.Taffy"},
    "backup": {"repo": "~/backups/taffy-restic", "drop": None, "mac_copy_expected": False},
    "zips": {"max": None, "container_memory_mb": None},
    # Keep-warm pings (usage.py): refresh an idle chat's prompt cache at read price. Off until a home is measured.
    "cache": {"keep_warm": False},
}

# Colin's own facts: the defaults of his home (no file, or a file whose home_id is "colin"), equal to what the code
# held before home.json existed. A file for any other home never inherits these, so a key missing from a stranger's
# file can never put Colin's data in front of their Zips.
COLIN_FACTS: dict = {
    "person": {"name": "Colin O'Brien", "first_name": "Colin", "pronoun_free": False},
    "paths": {"background_docs": ["~/taffy/shared/background-notes.md",
                                  "~/taffy/shared/background-record.txt"]},
    "network": {"public_url": "https://alien.tail0000.ts.net:6443"},
    "host": {"lead_backend": "local"},
    "plan": {"connectors": "claude"},
    "mail": {"accounts": [
        {"id": "personal", "address": "jdoe1@example.com", "label": "personal", "default": True,
         "owner_zip": "default", "kind": "imap", "provider": "gmail", "all_mail_folder": "[Gmail]/All Mail",
         "signoff": "Colin"},
        {"id": "lakeview", "address": "jd910@lakeview.test", "label": "school", "owner_zip": "school", "kind": "imap",
         "provider": "gmail", "all_mail_folder": "[Gmail]/All Mail", "signoff": "Best,\nColin O'Brien"},
        {"id": "goodworks", "address": "colin@goodworks.test", "label": "Good Works work",
         "owner_zip": "clients", "kind": "imap", "provider": "gmail", "signoff": "Colin"},
    ],
        # Which Zip follows up on a thread with someone at these domains (the hub's mail and calendar noticers).
        "owner_by_domain": {"lakeview.test": "school", "brightpath.test": "clients", "goodworks.test": "clients",
                            "blueheron.test": "clients", "theblueheron.test": "clients",
                            "netlify.com": "builder", "railway.app": "builder", "cloudflare.com": "builder",
                            "vercel.com": "builder", "namecheap.com": "builder", "porkbun.com": "builder"}},
    "integrations": {
        "tally": {"url": "http://127.0.0.1:18930", "env_file": "~/tally/server/.env"},
        "trello": {"board_id": "AbCd1234", "name": "Colin | School & Projects",
                   "url": "https://trello.com/b/AbCd1234/colin-school-projects", "lists": ["Doing (one task)", "Next"]},
    },
    "judge": {"enabled": True},
    "backup": {"mac_copy_expected": True},
    "cache": {"keep_warm": True},   # Colin asked for it (Oct 10); measured on the test home first
    # Words in an event that say which Zip prepares or follows up (beside the generic words in
    # calendar_noticers.owner_for_event): Colin's schools, employers, clients and projects.
    "zips": {"owner_keywords": {"school": "lakeview", "career": "acme",
                                "clients": "karen|ken|jake|matt|josh|heron|brightpath|maple grove|"
                                           "good works|gwi|bloomerang",
                                "builder": "lumenly|fernhill"}},
}


def colin_settings() -> dict:
    """Colin's home written out in full: what no file means, and what the integration step writes as his file."""
    return merge(DEFAULTS, COLIN_FACTS)


# The owner's user (Colin's) and the one file that may leave home_id out and still be his home: his own
# ~/taffy/home.json. zero-home-lib.py and the plugin's reader use the same user and rule.
OWNER_USER = os.environ.get("ZERO_OWNER_USER", "jdoe")
NO_ID = "unnamed"   # the home_id a file without one reads as, anywhere but the owner's own file


def owner_file() -> Path:
    """Colin's own settings file, where the integration step writes it."""
    try:
        return Path(pwd.getpwnam(OWNER_USER).pw_dir) / "taffy" / "home.json"
    except KeyError:
        return Path("/home") / OWNER_USER / "taffy" / "home.json"


def _is_owner_file(p: Path | None) -> bool:
    if p is None:
        return False
    try:
        return os.path.realpath(p) == os.path.realpath(owner_file())
    except (OSError, ValueError):
        return False


def is_colin(raw_file: dict | None, file: Path | None = None) -> bool:
    """No file is Colin's home, and so is a file whose home_id is "colin". A file without a home_id is his only when it
    is his own file at its known path (`file`); anywhere else it is another home's, so none of his facts reach it."""
    if raw_file is None:
        return True
    home_id = str(raw_file.get("home_id") or "")
    if home_id:
        return home_id == "colin"
    return _is_owner_file(file)


# Tests point this at a temporary file (tests/home_fixture.py); it wins over ZERO_HOME.
FILE: Path | None = None

_lock = threading.Lock()
_cache: dict = {"key": None, "data": None, "raw": None}


class HomeError(RuntimeError):
    pass


def path() -> Path:
    """The settings file, whether or not it exists."""
    if FILE is not None:
        return Path(FILE)
    env = os.environ.get("ZERO_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    return USER_HOME / "taffy" / "home.json"


def merge(base: dict, over: dict) -> dict:
    """`over` laid on `base`: objects merge by key, everything else replaces."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read(p: Path) -> dict | None:
    """The file's own content, None when there is no file. HomeError when it is there but unusable."""
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
    """The merged settings. Cheap to call often: the file is read again only when it changed."""
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
        except HomeError:
            if _cache["data"] is not None and _cache["key"] and _cache["key"][0] == str(p):
                log.warning("home settings unreadable; keeping the last good copy", exc_info=True)
                return _cache["data"]
            raise
        colin = is_colin(raw, p)
        data = merge(colin_settings() if colin else DEFAULTS, raw or {})
        if not str((raw or {}).get("home_id") or ""):
            data["home_id"] = "colin" if colin else NO_ID   # the defaults' "colin" never names another home
        _cache.update(key=key, data=data, raw=raw)
        return data


def raw() -> dict | None:
    """What the file itself says (no defaults), or None when there is no file."""
    load()
    return copy.deepcopy(_cache["raw"])


def exists() -> bool:
    return path().is_file()


def reset_cache() -> None:
    with _lock:
        _cache.update(key=None, data=None, raw=None)


def get(dotted: str, default=None):
    """`get("person.timezone")`. A missing key gives `default`; an explicit null gives None."""
    cur = load()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return copy.deepcopy(cur)


def save(changes: dict) -> dict:
    """Lays `changes` over the file's own content (or over DEFAULTS when there is no file yet, so the first write
    records today's values in full) and writes it atomically. Returns the merged settings."""
    p = path()
    with _lock:
        current = _read(p)
    base = current if current is not None else colin_settings()
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
    """A path from the settings: `~` is the running user's home (never $HOME)."""
    s = str(value)
    if s == "~" or s.startswith("~/"):
        return USER_HOME / s[2:] if len(s) > 1 else USER_HOME
    return Path(s)


def path_of(key: str) -> Path:
    """`path_of("hub_state")` -> /home/<user>/taffy/hub-state."""
    return expand(get(f"paths.{key}") or DEFAULTS["paths"][key])


# MARK: The person

def person_name(full: bool = True) -> str:
    """The person's name as the settings give it. The hub prefers what onboarding saved (onboarding.person_name)."""
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


# How a time zone is named in a sentence ("2:14 p.m. Eastern"). Others use the city from the zone's name.
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
    """"Eastern", "Pacific", "Berlin time"."""
    name = timezone_name()
    if name in _TZ_WORDS:
        return _TZ_WORDS[name]
    return name.rsplit("/", 1)[-1].replace("_", " ") + " time"


def tz_phrase() -> str:
    """"Eastern (New York)" for a prompt that says which clock the person lives by."""
    name = timezone_name()
    city = name.rsplit("/", 1)[-1].replace("_", " ")
    word = tz_word()
    return word if word.lower().startswith(city.lower()) or word == "UTC" else f"{word} ({city})"


class _HomeZone(tzinfo):
    """The home's time zone, looked up on every use, so `ET = home.TZ` at import still follows a change to the
    settings file. Behaves like the ZoneInfo it stands for."""

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
    """(start, end) hours of the person's night, 23 and 7 by default."""
    q = get("person.quiet_hours") or {}
    try:
        start, end = int(q.get("start", 23)) % 24, int(q.get("end", 7)) % 24
    except (TypeError, ValueError):
        start, end = 23, 7
    return start, end


def hour_words(h: int) -> str:
    """23 -> "11 p.m.", 7 -> "7 a.m.", 0 -> "midnight", 12 -> "noon"."""
    h %= 24
    if h == 0:
        return "midnight"
    if h == 12:
        return "noon"
    return f"{h % 12 or 12} {'a.m.' if h < 12 else 'p.m.'}"


def units_sentence() -> str:
    """How the person measures things, for a prompt."""
    units = str(get("locale.units") or "us")
    if units == "metric":
        return "Celsius and metric units"
    if units == "uk":
        return "Celsius, miles and UK units"
    return "Fahrenheit and US units"


def spelling() -> str:
    return str(get("locale.spelling") or "american")


# MARK: The plan and the home's accounts

def plan_kind() -> str:
    """"own" when the person's Zips think on the person's own plans, "shared" when through the broker."""
    p = get("plan") or {}
    modes = {str((p.get(k) or {}).get("mode") or "own") for k in ("claude", "chatgpt")}
    return "shared" if "broker" in modes else "own"


def connectors() -> str:
    """"claude", "chatgpt" or "none": where the person's connected accounts (Gmail, Calendar ...) come from. Anything
    else (null, "", "false", a typo) is "none", as the plugin reads it: connectors are never assumed."""
    c = str(get("plan.connectors") or "").strip().lower()
    return c if c in ("claude", "chatgpt") else "none"


def is_google(account: dict) -> bool:
    """A mailbox connected with Sign In with Google (kind "google_oauth"; "auth": "google" is read too)."""
    return str(account.get("kind") or "").lower() == "google_oauth" or account.get("auth") == "google"


def is_google_calendar(cal: dict) -> bool:
    """A calendar read through Sign In with Google (kind "google_oauth"; "google" is read too)."""
    return str(cal.get("kind") or "").lower() in ("google_oauth", "google")


def calendar_address(cal: dict) -> str:
    """The Google address a google_oauth calendar belongs to (`address`; `account` is read too)."""
    return str(cal.get("address") or cal.get("account") or "").strip().lower()


def mail_accounts() -> list[dict]:
    """The home's mailboxes: [{"id", "address", "label", "default", "owner_zip", "kind" (imap or google_oauth)}]."""
    accts = get("mail.accounts") or []
    return [a for a in accts if isinstance(a, dict) and a.get("id") and a.get("address")]


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
    """Colin's home: the one whose plans the broker shares. Only the owner's own data paths (his background
    documents, his registry migration) look at this."""
    return plan_kind() == "own" and str(get("home_id") or "") == "colin"


def summary() -> dict:
    """What the app's settings screens show (GET /api/home-settings builds on this)."""
    q = quiet_hours()
    return {
        "home_id": get("home_id"),
        "person": {"name": person_name(), "first_name": person_name(False), "timezone": timezone_name(),
                   "quiet_hours": {"start": q[0], "end": q[1]}},
        "locale": {"units": get("locale.units") or "us", "spelling": spelling(),
                   "clock": get("locale.clock") or "12h"},
        "plan": plan_kind(),
        "connectors": connectors(),
        "host": {"name": get("host.name"), "kind": get("host.kind")},
    }


def next_quiet_end(now: datetime | None = None) -> datetime:
    """The next end of quiet hours in the home's time zone (the app's snooze until morning)."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    end = now.replace(hour=quiet_hours()[1], minute=0, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=1)
    return end
