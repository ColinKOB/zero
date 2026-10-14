"""Shared code for the zero-home-* scripts: the home settings reader, the registry of homes, port allocation,
the renderer, a dependency-free YAML writer and a dry-run aware file and command layer.

Loaded by path (the file name has dashes so it matches the scripts it serves):

    spec = importlib.util.spec_from_file_location("zero_home_lib", HERE / "zero-home-lib.py")

Standard library only, so it runs under the system python3 (3.12 on alien) and the Hermes venv (3.14).
The settings schema is the one in docs/plans/2026-10-09-zero-for-anyone-plan.md, section 4. Every default
equals today's value on alien, so Colin's home keeps working before its home.json exists.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import pwd
import re
import secrets
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRV = Path(os.environ.get("ZERO_SRV", "/srv/zero"))
REGISTRY = SRV / "homes.json"
OWNER_USER = os.environ.get("ZERO_OWNER_USER", "jdoe")
ACCOUNT_HOME_RE = re.compile(r"^(u|qa)-[a-z0-9]{2,14}$")
HOME_ID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$|^(u|qa)-[a-z0-9]{2,14}$")
HOME_ID_RULE = "a home id is 2 to 16 lowercase letters or digits starting with a letter, or u- or qa- and 2 to 14 of them"
ZIP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

def _first_dir(*candidates: Path) -> Path:
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


UNIT_TEMPLATES = _first_dir(HERE.parent / "systemd" / "templates", SRV / "systemd-templates")
HOME_TEMPLATES = _first_dir(HERE / "zero-home-templates", SRV / "bin" / "zero-home-templates")

DEFAULTS: dict = {
    "version": 1,
    "home_id": "colin",
    "person": {"name": "Colin O'Brien", "first_name": "Colin", "timezone": "America/New_York",
               "quiet_hours": {"start": 23, "end": 7}, "pronoun_free": False},
    "locale": {"units": "us", "spelling": "american", "clock": "12h"},
    "paths": {"hermes_home": "~/.taffy", "hermes_bin": "~/taffy/bin/taffy", "hub_state": "~/taffy/hub-state",
              "work": "~/taffy/work", "shared": "~/taffy/shared", "plugin": "~/taffy/plugins/taffy-tools",
              "owner": "~/taffy/owner", "background_docs": []},
    "ports": {"hub": 8650, "hermes_api": 8642, "broker_http": 8660,
              "preview": {"base": 18200, "per_zip": 10, "count": 6}, "docker_subnet_base": "172.30"},
    "network": {"public_url": "https://alien.tail0000.ts.net:6443", "serve_port": 6443},
    "host": {"name": "alien", "kind": "server", "sandbox_image": "taffy/zip-sandbox:2026-10-08-v1",
             "lead_backend": "local"},
    "plan": {"claude": {"mode": "own", "command": "claude"},
             "chatgpt": {"mode": "own", "codex_command": "~/.local/bin/codex", "codex_base_url": None},
             "connectors": "claude", "frugal": False},
    "mail": {"accounts": []},
    "calendars": [],
    "integrations": {"tally": None, "trello": None, "whisper_url": "http://10.0.0.65:11437"},
    "judge": {"enabled": True},
    "push": {"key_dir": "~/.config/taffy-push", "team_id": "YOUR_TEAM_ID", "topic": "com.example.Taffy"},
    "backup": {"repo": "~/backups/taffy-restic", "drop": None, "mac_copy_expected": True},
    "zips": {"max": 6, "container_memory_mb": 3072},
    "google": {"client_id": None},
}

ZERO_GOOGLE_CLIENT_ID = "000000000000-yourgoogleclientid.apps.googleusercontent.com"

NEUTRAL: dict = {
    "person": {"name": "", "first_name": "", "pronoun_free": True},
    "host": {"lead_backend": "docker"},
    "plan": {"connectors": "none"},
    "judge": {"enabled": False},
    "backup": {"mac_copy_expected": False},
    "zips": {"max": None, "container_memory_mb": None},
}

BROKER_DIR = SRV / "broker"
BROKER_CLAUDE = str(BROKER_DIR / "claude-client")
BROKER_CODEX = str(BROKER_DIR / "codex-client")


def merge(base: dict, over: dict) -> dict:
    """Deep merge: values in `over` win; dicts merge key by key; lists and scalars replace."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


NO_ID = "unnamed"


def owner_settings_file() -> Path:
    """The owner's (Colin's) own ~/taffy/home.json: the only file that may leave home_id out and still be his home."""
    try:
        return Path(pwd.getpwnam(OWNER_USER).pw_dir) / "taffy" / "home.json"
    except KeyError:
        return Path("/home") / OWNER_USER / "taffy" / "home.json"


def is_owner_settings(data: dict | None, path: Path | None) -> bool:
    """No file is Colin's home, and so is a file whose home_id is "colin". A file without a home_id is his only at his
    own known path (hub/taffy_hub/home.py and the plugin's home.py hold the same rule)."""
    if data is None:
        return True
    hid = str(data.get("home_id") or "")
    if hid:
        return hid == "colin"
    try:
        return path is not None and os.path.realpath(path) == os.path.realpath(owner_settings_file())
    except (OSError, ValueError):
        return False


def load_home(path: Path | None) -> dict:
    """The settings with defaults filled in. A missing file means today's values (Colin's home)."""
    data = None
    if path is not None and path.exists():
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")
    colin = is_owner_settings(data, path)
    out = merge(DEFAULTS, data or {}) if colin else merge(merge(DEFAULTS, NEUTRAL), data)
    if not str((data or {}).get("home_id") or ""):
        out["home_id"] = "colin" if colin else NO_ID
    return out


def owner_first_name() -> str:
    """The first name of the person whose plans the broker shares (the owner's home.json, else the defaults), for
    the app's "a plan that Colin shares with you" (home.json plan.owner_name)."""
    try:
        owner_home = Path(pwd.getpwnam(OWNER_USER).pw_dir) / "taffy" / "home.json"
        settings = load_home(owner_home if os.access(owner_home, os.R_OK) else None)
    except (KeyError, OSError, ValueError):
        settings = DEFAULTS
    p = settings.get("person") or {}
    return str(p.get("first_name") or str(p.get("name") or "").split(" ")[0] or "")


def _default_public_host() -> str:
    """The tailnet name of this machine, from the owner's default address (every home on it shares the name)."""
    from urllib.parse import urlsplit
    return urlsplit(DEFAULTS["network"]["public_url"]).hostname or "localhost"


def expand(value: str, home_dir: Path) -> Path:
    """`~/x` relative to the home's own directory (never the running user's, which may differ)."""
    value = str(value)
    if value == "~":
        return home_dir
    if value.startswith("~/"):
        return home_dir / value[2:]
    return Path(value)


def new_home_settings(home_id: str, person: str, timezone: str, ports: dict, *, first_name: str | None = None,
                      public_host: str | None = None, whisper_url: str | None = "http://10.0.0.65:11437",
                      google_account: str | None = None, owner_name: str | None = None,
                      own_plan: bool = False) -> dict:
    """home.json for a new home on a shared plan (section 4's example shape), or on the person's own plans when
    own_plan is set: then nothing points at the broker or names the owner, and the person signs in to their own
    Claude or ChatGPT from the app."""
    first = first_name or person.split()[0]
    public_host = public_host or _default_public_host()
    owner_name = owner_first_name() if owner_name is None else owner_name
    serve = ports["serve_port"]
    settings = {
        "version": 1,
        "home_id": home_id,
        "person": {"name": person, "first_name": first, "timezone": timezone,
                   "quiet_hours": {"start": 23, "end": 7}, "pronoun_free": True},
        "locale": {"units": "us", "spelling": "american", "clock": "12h"},
        "paths": copy.deepcopy(DEFAULTS["paths"]),
        "ports": {"hub": ports["hub"], "hermes_api": ports["hermes_api"], "broker_http": ports.get("broker_http", 8660),
                  "preview": {"base": ports["preview_base"], "per_zip": 10, "count": 6},
                  "docker_subnet_base": ports["docker_subnet_base"]},
        "network": {"public_url": f"https://{public_host}:{serve}", "serve_port": serve},
        "host": {"name": "alien", "kind": "server", "sandbox_image": DEFAULTS["host"]["sandbox_image"],
                 "lead_backend": "docker"},
        "plan": {"claude": {"mode": "broker", "command": BROKER_CLAUDE},
                 "chatgpt": {"mode": "broker", "codex_command": BROKER_CODEX,
                             "codex_base_url": f"http://127.0.0.1:{ports.get('broker_http', 8660)}/{home_id}"},
                 "connectors": "none", "frugal": True, "owner_name": owner_name or None},
        "mail": {"accounts": []},
        "calendars": [],
        "integrations": {"tally": None, "trello": None, "whisper_url": whisper_url},
        "judge": {"enabled": False},
        "push": copy.deepcopy(DEFAULTS["push"]),
        "backup": {"repo": "~/backups/taffy-restic", "drop": str(SRV / "backups" / home_id), "mac_copy_expected": False},
        "zips": {"max": 4, "container_memory_mb": 2048},
        "google": {"client_id": ZERO_GOOGLE_CLIENT_ID},
    }
    if own_plan:
        settings["plan"] = {"claude": {"mode": "own", "command": "claude"},
                            "chatgpt": {"mode": "own", "codex_command": "~/.local/bin/codex", "codex_base_url": None},
                            "connectors": "none", "frugal": True}
    if google_account:
        settings["google"]["expected_account"] = google_account
    return settings


PRESETS = {
    "riley": {"person": "Riley Ellis", "first_name": "Riley", "timezone": "America/New_York",
              "google_account": "riley@example.com", "index": 1},
    "test": {"person": "Sam Rivera", "first_name": "Sam", "timezone": "America/New_York", "index": 2},
}

SETTABLE = {
    "google.client_id": re.compile(r"^[0-9]{6,20}-[a-z0-9]{10,64}\.apps\.googleusercontent\.com$"),
    "network.public_url": re.compile(r"^https://[a-z0-9.-]+(:[0-9]{2,5})?$"),
    "person.timezone": re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$"),
}


def set_value(settings: dict, dotted: str, value: str) -> dict:
    if dotted not in SETTABLE:
        raise ValueError(f"{dotted} cannot be set by a script. Settable: {', '.join(sorted(SETTABLE))}")
    if not SETTABLE[dotted].match(value):
        raise ValueError(f"{value!r} is not a valid value for {dotted}")
    out = copy.deepcopy(settings)
    node = out
    *parents, last = dotted.split(".")
    for p in parents:
        node = node.setdefault(p, {})
    node[last] = value
    return out


OWNER_ENTRY = {"user": OWNER_USER, "owner": True, "plan": "own",
               "ports": {"hub": 8650, "hermes_api": 8642, "serve_port": 6443, "preview_base": 18200,
                         "preview_span": 60, "docker_subnet_base": "172.30", "broker_http": 8660}}


def read_registry(path: Path = REGISTRY) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("homes", {})
    return data


def home_entry(home_id: str, registry: dict | None = None) -> dict | None:
    reg = registry if registry is not None else read_registry()
    return reg["homes"].get(home_id)


def allocate_ports(home_id: str, registry: dict, listening: set[int]) -> dict:
    """Ports for a new home. Riley and the test home keep the plan's numbers when they are free; the preview
    range moves up in steps of 100 past anything already listening (18421 and 18422 are taken on alien today,
    so the test home's previews land at 18500)."""
    used = set(listening)
    used_subnets = set()
    used_previews = []
    for entry in registry["homes"].values():
        p = entry.get("ports", {})
        used |= {p.get("hub"), p.get("hermes_api"), p.get("serve_port")}
        used_subnets.add(p.get("docker_subnet_base"))
        if p.get("preview_base"):
            used_previews.append((p["preview_base"], p["preview_base"] + p.get("preview_span", 60)))
    used.discard(None)

    preferred = PRESETS.get(home_id, {}).get("index")
    order = ([preferred] if preferred else []) + [i for i in range(1, 40) if i != preferred]
    for i in order:
        hub, api, serve = 8650 + i, 8642 + i, 6443 + i
        subnet = f"172.{30 + i}"
        if hub in used or api in used or serve in used or subnet in used_subnets or api >= 8650:
            continue
        break
    else:
        raise RuntimeError("no free port set for a new home")

    span = 60
    base = 18200 + 100 * i
    while True:
        rng = set(range(base, base + span))
        clash = rng & listening or any(lo < base + span and base < hi for lo, hi in used_previews)
        if not clash:
            break
        base += 100
        if base > 19900:
            raise RuntimeError("no free preview range for a new home")
    return {"hub": hub, "hermes_api": api, "serve_port": serve, "preview_base": base, "preview_span": span,
            "docker_subnet_base": subnet, "broker_http": 8660}


def listening_ports() -> set[int]:
    """TCP ports something listens on, from /proc (no extra tools needed)."""
    ports = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) > 3 and parts[3] == "0A":   # LISTEN
                ports.add(int(parts[1].rsplit(":", 1)[1], 16))
    return ports


OWNER_SLOTS = {"builder": 0, "clients": 1, "school": 2, "career": 3, "money": 4}


def slots_path(home_dir: Path) -> Path:
    return home_dir / "taffy" / "state" / "zero-slots.json"


def read_slots(home_dir: Path, settings: dict) -> dict:
    try:
        data = json.loads(slots_path(home_dir).read_text())
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    return dict(OWNER_SLOTS) if settings.get("home_id") == "colin" else {}


def assign_slot(slots: dict, zip_id: str, count: int) -> dict:
    if zip_id in slots:
        return slots
    free = [i for i in range(count) if i not in slots.values()]
    if not free:
        raise RuntimeError(f"no preview slot left for {zip_id}; the home allows {count}")
    out = dict(slots)
    out[zip_id] = free[0]
    return out


def preview_range(settings: dict, slot: int) -> tuple[int, int]:
    p = settings["ports"]["preview"]
    first = p["base"] + slot * p["per_zip"]
    return first, first + p["per_zip"] - 1


def subnet(settings: dict, slot: int) -> str:
    return f"{settings['ports']['docker_subnet_base']}.{slot}.0/24"


LEAD = "default"
CHATGPT_MODEL = "gpt-6.1-sol"
FALLBACK = [{"provider": "openai-codex", "model": CHATGPT_MODEL}]
PLUGINS = ["claude-subscription-directsdk-experimental", "taffy-tools"]


def docker_block(settings: dict, home_dir: Path, zip_id: str, slot: int, owner: bool) -> dict:
    work = expand(settings["paths"]["work"], home_dir)
    shared = expand(settings["paths"]["shared"], home_dir)
    first, last = preview_range(settings, slot)
    return {
        "home_mode": "real",
        "backend": "docker",
        "docker_image": settings["host"]["sandbox_image"],
        "container_persistent": True,
        "docker_persist_across_processes": True,
        "container_cpu": 2,
        "container_memory": 3072 if owner else int(settings["zips"].get("container_memory_mb") or 2048),
        "docker_network": True,
        "docker_run_as_host_user": False,
        "docker_mount_cwd_to_workspace": False,
        "docker_volumes": [f"{work}:{work}:ro", f"{work}/{zip_id}:{work}/{zip_id}:rw", f"{shared}:{shared}:ro"],
        "docker_env": {"HOME": str(home_dir), "TZ": settings["person"]["timezone"]},
        "docker_forward_env": [],
        "docker_extra_args": ["-p", f"127.0.0.1:{first}-{last}:{first}-{last}", "--network", f"zip-{zip_id}"],
    }


def managed_config(settings: dict, home_dir: Path, zip_id: str, slot: int | None, owner: bool) -> dict:
    """The keys the renderer owns in a Hermes profile's config.yaml. Everything else in the file (tool lists
    from set-toolsets.py, skills, memory limits, onboarding flags) is left as it is."""
    lead = zip_id == LEAD
    local = lead and settings["host"]["lead_backend"] == "local"
    cfg: dict = {
        "plugins": {"enabled": list(PLUGINS), "disabled": []},
        "model": {"provider": "claude-subscription-directsdk-experimental", "default": "opus"},
        "terminal": {"home_mode": "real"} if local else docker_block(settings, home_dir, zip_id, slot or 0, owner),
        "bot_desktop": {"placement": "gateway", "auto_start": True, "idle_stop_minutes": 20,
                        "min_free_memory_mb": 1200 if owner else 1500},
        "browser": {"headed": True, "restrict_evaluate": True, "allow_private_urls": True},
        "fallback_providers": copy.deepcopy(FALLBACK),
        "auxiliary": {"background_review": {"provider": "claude-subscription-directsdk-experimental",
                                            "model": "claude-haiku-5-5"},
                      "compression": {"provider": "claude-subscription-directsdk-experimental",
                                      "model": "claude-haiku-5-5"}},
    }
    if settings["plan"].get("brain") == "chatgpt":
        cfg["model"] = {"provider": "openai-codex", "default": CHATGPT_MODEL}
        cfg["fallback_providers"] = []
        cfg["auxiliary"] = {"background_review": {"provider": "openai-codex", "model": CHATGPT_MODEL},
                            "compression": {"provider": "openai-codex", "model": CHATGPT_MODEL}}
    if local:
        del cfg["bot_desktop"]["placement"]
    if lead:
        cfg["gateway"] = {"multiplex_profiles": True}
    return cfg


CHECK_KEYS = ["plugins.enabled", "model.provider", "terminal", "bot_desktop.placement", "fallback_providers",
              "gateway.multiplex_profiles"]
CHECK_IGNORE = {"terminal.container_memory", "browser.allow_private_urls",
                "terminal.docker_forward_env"}


def get_dotted(d: dict, dotted: str):
    node = d
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


class _Missing:
    def __repr__(self):
        return "(missing)"


_MISSING = _Missing()


def diff_config(rendered: dict, live: dict, keys: list[str] = CHECK_KEYS, ignore: set[str] = CHECK_IGNORE) -> list[str]:
    """Plain lines naming each managed value that differs between what the renderer would write and the file."""
    out = []

    def walk(prefix: str, a, b):
        if prefix in ignore:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in list(a) + [k for k in b if k not in a]:
                walk(f"{prefix}.{k}", a.get(k, _MISSING), b.get(k, _MISSING))
            return
        if a != b:
            out.append(f"{prefix}: renderer {a!r}, live {b!r}")

    for key in keys:
        walk(key, get_dotted(rendered, key), get_dotted(live, key))
    return out


def apply_managed(existing: dict, managed: dict) -> dict:
    """The existing config with every managed key set. Managed dicts replace the existing value only at the
    leaves the renderer owns, so unrelated siblings survive (for example bot_desktop.auto_start set by hand)."""
    out = copy.deepcopy(existing)
    for k, v in managed.items():
        if k == "terminal":
            keep = (out.get(k) or {}).get("docker_forward_env") if isinstance(out.get(k), dict) else None
            out[k] = copy.deepcopy(v)
            if keep and "docker_forward_env" in out[k]:
                out[k]["docker_forward_env"] = keep
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def base_config() -> dict:
    """A new profile's starting file, before set-toolsets.py adds the tool lists and skills."""
    return {
        "_config_version": 50,
        "kanban": {"dispatch_interval_seconds": 5, "auto_decompose": False, "max_in_progress_per_profile": 1,
                   "review_dispatch": False, "auto_subscribe_on_create": False, "notify_in_gateway": False},
        "tools": {"tool_search": {"enabled": "off"}},
        "streaming": {"enabled": False},
        "agent": {},
        "memory": {"user_char_limit": 5000},
    }


def render_profile_config(settings: dict, home_dir: Path, zip_id: str, slot: int | None, owner: bool,
                          existing: dict | None = None) -> dict:
    start = existing if existing is not None else base_config()
    return apply_managed(start, managed_config(settings, home_dir, zip_id, slot, owner))


def render_env(settings: dict, existing: str, *, lead: bool, broker_secret: str | None) -> str:
    """A profile's .env: keeps every line already there (API keys the person added), sets the lines the home
    owns, and makes a fresh API_SERVER_KEY only when none exists."""
    lines = existing.splitlines() if existing else ["# Secrets for this Hermes profile. Mode 600."]
    values = {}
    for line in lines:
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2)
    want = {"AGENT_BROWSER_ARGS": "--disable-dev-shm-usage"}
    if lead:
        want.update(API_SERVER_ENABLED="true", API_SERVER_HOST="127.0.0.1",
                    API_SERVER_PORT=str(settings["ports"]["hermes_api"]))
    if not values.get("API_SERVER_KEY"):
        want["API_SERVER_KEY"] = secrets.token_urlsafe(32)
    if settings["plan"]["chatgpt"]["mode"] == "broker" and broker_secret:
        want["HERMES_CODEX_BASE_URL"] = codex_base_url(settings, broker_secret)
    out = []
    seen = set()
    for line in lines:
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in want:
            out.append(f"{m.group(1)}={want[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in want.items() if k not in seen]
    return "\n".join(out) + "\n"


def codex_base_url(settings: dict, broker_secret: str) -> str:
    base = settings["plan"]["chatgpt"].get("codex_base_url") or \
        f"http://127.0.0.1:{settings['ports']['broker_http']}/{settings['home_id']}"
    return f"{base.rstrip('/')}/{broker_secret}"


def render_broker_env(settings: dict, broker_secret: str | None) -> str:
    """~/.config/zero-broker/env (mode 600), read by the gateway unit. Empty when the home has its own plans."""
    lines = ["# The plan broker's environment for this home. Written by zero-home-render. Mode 600."]
    if settings["plan"]["claude"]["mode"] == "broker":
        lines.append(f"CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND={settings['plan']['claude']['command']}")
    if settings["plan"]["chatgpt"]["mode"] == "broker" and broker_secret:
        lines.append(f"HERMES_CODEX_BASE_URL={codex_base_url(settings, broker_secret)}")
    return "\n".join(lines) + "\n"


PLACEHOLDER_TOKEN = "zero-broker-placeholder"


def codex_placeholder_auth(existing: dict | None) -> dict | None:
    """The openai-codex entry Hermes needs to pick the provider in a broker home. Not a credential: the broker
    replaces it with the owner's token on the way out. Returns None when an entry already exists."""
    data = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    providers = data.setdefault("providers", {})
    if "openai-codex" in providers or (data.get("credential_pool") or {}).get("openai-codex"):
        return None
    providers["openai-codex"] = {
        "tokens": {"access_token": PLACEHOLDER_TOKEN, "refresh_token": PLACEHOLDER_TOKEN},
        "last_refresh": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "auth_mode": "chatgpt",
    }
    data.setdefault("version", 1)
    return data


def fill_template(text: str, values: dict) -> str:
    def sub(m):
        key = m.group(1)
        if key not in values:
            raise KeyError(f"template value {key} is missing")
        return str(values[key])
    return re.sub(r"\{\{([a-z_]+)\}\}", sub, text)


def unit_values(settings: dict, home_dir: Path) -> dict:
    return {"home_id": settings["home_id"], "hub_port": settings["ports"]["hub"],
            "hermes_api_port": settings["ports"]["hermes_api"], "timezone": settings["person"]["timezone"],
            "person": settings["person"]["first_name"]}


UNIT_FILES = ["taffy-gateway.service", "taffy-hub.service", "taffy-backup.service", "taffy-backup.timer",
              "taffy-gateway.service.d/docker.conf", "taffy-gateway.service.d/zero-broker.conf",
              "docker.service.d/zip-sandbox.conf"]


def render_units(settings: dict, home_dir: Path, templates: Path = UNIT_TEMPLATES) -> dict[str, str]:
    values = unit_values(settings, home_dir)
    return {name: fill_template((templates / name).read_text(), values) for name in UNIT_FILES}


def render_daemon_json() -> str:
    return json.dumps({"dns": ["1.1.1.1", "8.8.8.8"], "log-driver": "local"}) + "\n"


UNITS_WORDS = {"us": "Fahrenheit, miles, feet and inches, pounds, dollars",
               "metric": "Celsius, kilometers, meters and centimeters, kilograms"}


def render_lead_soul(settings: dict, home_dir: Path, agent_name: str = "Zero",
                     templates: Path = HOME_TEMPLATES) -> str:
    p = settings["person"]
    loc = settings["locale"]
    values = {
        "agent_name": agent_name, "name": p["name"], "first_name": p["first_name"], "timezone": p["timezone"],
        "units": UNITS_WORDS.get(loc.get("units"), UNITS_WORDS["us"]),
        "spelling": "British" if loc.get("spelling") == "british" else "American",
        "clock": "24-hour" if loc.get("clock") == "24h" else "12-hour",
        "work_dir": str(expand(settings["paths"]["work"], home_dir)),
        "shared_dir": str(expand(settings["paths"]["shared"], home_dir)),
    }
    return fill_template((templates / "lead-SOUL.md").read_text(), values)


def render_profile_yaml(settings: dict) -> str:
    first = settings["person"]["first_name"]
    return emit_yaml({"description": f"Lead: talks with {first}, routes work to the other Zips and keeps track "
                                     "of everything.", "description_auto": False})


_PLAIN = re.compile(r"^[A-Za-z_/][A-Za-z0-9_./@+-]*$")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "~", "y", "n"}


def _scalar(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if _PLAIN.match(s) and s.lower() not in _YAML_WORDS:
        return s
    return json.dumps(s, ensure_ascii=False)


def emit_yaml(obj, indent: int = 0) -> str:
    pad = "  " * indent
    lines = []
    if isinstance(obj, dict):
        if not obj:
            return pad + "{}\n"
        for k, v in obj.items():
            key = _scalar(k)
            if isinstance(v, dict) and v:
                lines.append(f"{pad}{key}:\n" + emit_yaml(v, indent + 1).rstrip("\n"))
            elif isinstance(v, list) and v:
                lines.append(f"{pad}{key}:\n" + emit_yaml(v, indent).rstrip("\n"))
            elif isinstance(v, dict):
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(v, list):
                lines.append(f"{pad}{key}: []")
            else:
                lines.append(f"{pad}{key}: {_scalar(v)}")
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict) and v:
                body = emit_yaml(v, indent + 1).rstrip("\n").splitlines()
                lines.append(f"{pad}- {body[0].lstrip()}")
                lines += body[1:]
            else:
                lines.append(f"{pad}- {_scalar(v) if not isinstance(v, (dict, list)) else json.dumps(v)}")
    else:
        lines.append(pad + _scalar(obj))
    return "\n".join(lines) + "\n"


def load_yaml(text: str):
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        pass
    try:
        from ruamel.yaml import YAML  # type: ignore
        return YAML(typ="safe").load(text)
    except ImportError:
        pass
    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError("no YAML reader here: run this with the Hermes venv (~/taffy/.venv/bin/python)")


class Ops:
    """Every change goes through here. With dry_run nothing changes and each step is printed as
    "would ...". Each method is idempotent: it does nothing when the target is already right."""

    def __init__(self, dry_run: bool = False, quiet: bool = False, out=None):
        self.dry_run = dry_run
        self.quiet = quiet
        self.out = out or sys.stdout
        self.changes: list[str] = []

    def say(self, text: str) -> None:
        if not self.quiet:
            print(text, file=self.out, flush=True)

    def _did(self, what: str) -> None:
        self.changes.append(what)
        self.say(("would " if self.dry_run else "") + what)

    def mkdir(self, path: Path, mode: int = 0o755) -> None:
        if path.is_dir():
            return
        self._did(f"create folder {path}")
        if not self.dry_run:
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, mode)

    def write(self, path: Path, content: str, mode: int = 0o644, *, keep_existing: bool = False,
              secret: bool = False) -> bool:
        """Writes content when it differs. keep_existing leaves a file that is already there alone."""
        try:
            current = path.read_text()
        except OSError:
            current = None
        if current is not None and (keep_existing or current == content):
            if (path.stat().st_mode & 0o777) != mode:
                self.chmod(path, mode)
            return False
        self._did(f"{'write' if current is None else 'update'} {path}" + (" (secret, contents not shown)" if secret else ""))
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        return True

    def chmod(self, path: Path, mode: int) -> None:
        self._did(f"set mode {oct(mode)} on {path}")
        if not self.dry_run:
            os.chmod(path, mode)

    def symlink(self, link: Path, target: Path | str) -> None:
        target = str(target)
        if link.is_symlink() and os.readlink(link) == target:
            return
        if link.exists() and not link.is_symlink():
            raise RuntimeError(f"{link} exists and is not a link; leaving it alone")
        self._did(f"link {link} -> {target}")
        if not self.dry_run:
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.is_symlink():
                link.unlink()
            link.symlink_to(target)

    def run(self, cmd: list[str], *, check: bool = True, env: dict | None = None, input: str | None = None,
            capture: bool = False, describe: str | None = None, always: bool = False) -> subprocess.CompletedProcess:
        """Runs a command. `always` runs it in a dry run too (read-only checks)."""
        shown = describe or " ".join(shlex.quote(c) for c in cmd)
        if self.dry_run and not always:
            self._did(f"run {shown}")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if not always:
            self._did(f"run {shown}")
        return subprocess.run(cmd, check=check, env=env, input=input, text=True,
                              capture_output=capture)


def user_home(user: str) -> Path:
    return Path(pwd.getpwnam(user).pw_dir)


def current_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def fix_home_env() -> None:
    """Under sudo, $HOME can still name the caller's home. The home scripts always mean the running user's."""
    entry = pwd.getpwuid(os.getuid())
    os.environ["HOME"] = entry.pw_dir
    os.environ["USER"] = os.environ["LOGNAME"] = entry.pw_name
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # The same search order as the home's units, so `claude` and `codex` are the home's own (the broker's clients
    # on a shared plan) and never the machine's /usr/bin copies, which sudo's reset PATH would find first.
    own = [f"{entry.pw_dir}/taffy/bin", f"{entry.pw_dir}/.local/bin", str(SRV / "bin")]
    rest = [d for d in os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(":") if d and d not in own]
    os.environ["PATH"] = ":".join(own + rest)
    # sudo keeps the caller's working folder, which the home's user cannot read; uv, git and the rest look for
    # their config files from there and fail.
    try:
        os.chdir(entry.pw_dir)
    except OSError:
        pass


def runtime_env(uid: int | None = None) -> dict:
    """The environment `systemctl --user` and rootless Docker need when entered through sudo."""
    uid = os.getuid() if uid is None else uid
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
    env["DOCKER_HOST"] = f"unix:///run/user/{uid}/docker.sock"
    env["HOME"] = str(Path(pwd.getpwuid(uid).pw_dir))
    env["PATH"] = f"{env['HOME']}/taffy/bin:{env['HOME']}/.local/bin:/srv/zero/bin:/usr/local/bin:/usr/bin:/bin"
    return env


def assert_home_identity(home_id: str, registry: dict) -> dict:
    """Refuses unless the running user is exactly the registered, non-owner user of home_id."""
    entry = home_entry(home_id, registry)
    if not entry:
        raise SystemExit(f"{home_id} is not in {REGISTRY}. Run zero-home-sudo-prep home {home_id} first.")
    me = current_user()
    if entry.get("owner") or me == OWNER_USER or os.getuid() == 0:
        raise SystemExit("This step runs as the home's own user, never as the owner or root.")
    if me != entry["user"]:
        raise SystemExit(f"This step must run as {entry['user']}, not {me}.")
    return entry
