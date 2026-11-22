""











from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import pwd
import re
import secrets
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRV = Path(os.environ.get("ZERO_SRV", "/srv/zero"))
REGISTRY = SRV / "homes.json"


def _registered_owner() -> str:
    ""
    try:
        homes = json.loads(REGISTRY.read_text()).get("homes") or {}
        return next((str(e["user"]) for e in homes.values() if isinstance(e, dict) and e.get("owner") and e.get("user")),
                    "")
    except (OSError, ValueError, AttributeError):
        return ""



OWNER_USER = os.environ.get("ZERO_OWNER_USER", "").strip() or _registered_owner() or pwd.getpwuid(os.getuid()).pw_name


ACCOUNT_HOME_RE = re.compile(r"^(u|qa)-[a-z0-9]{2,14}$")
HOME_ID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$|^(u|qa)-[a-z0-9]{2,14}$")
HOME_ID_RULE = "a home id is 2 to 16 lowercase letters or digits starting with a letter, or u- or qa- and 2 to 14 of them"
ZIP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")







PREVIEW_COUNT = 10
PREVIEW_SPAN = 10 * PREVIEW_COUNT



def _first_dir(*candidates: Path) -> Path:
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


UNIT_TEMPLATES = _first_dir(HERE.parent / "systemd" / "templates", SRV / "systemd-templates")
HOME_TEMPLATES = _first_dir(HERE / "zero-home-templates", SRV / "bin" / "zero-home-templates")





DEFAULTS: dict = {
    "version": 1,
    "home_id": "unnamed",
    "owner_home": False,
    "person": {"name": "", "first_name": "", "timezone": "America/New_York",
               "quiet_hours": {"start": 23, "end": 7}, "pronoun_free": True},
    "locale": {"units": "us", "spelling": "american", "clock": "12h"},
    "paths": {"hermes_home": "~/.taffy", "hermes_bin": "~/taffy/bin/taffy", "hub_state": "~/taffy/hub-state",
              "work": "~/taffy/work", "shared": "~/taffy/shared", "plugin": "~/taffy/plugins/taffy-tools",
              "owner": "~/taffy/owner", "background_docs": []},
    "ports": {"hub": 8650, "hermes_api": 8642, "broker_http": 8660,
              "preview": {"base": 18200, "per_zip": 10, "count": PREVIEW_COUNT}, "docker_subnet_base": "172.30"},
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
}



ZERO_GOOGLE_CLIENT_ID = "846197417273-d38hoec6rld9eec2ppbubvhggfh0qb4a.apps.googleusercontent.com"

BROKER_DIR = SRV / "broker"
BROKER_CLAUDE = str(BROKER_DIR / "claude-client")
BROKER_CODEX = str(BROKER_DIR / "codex-client")


def merge(base: dict, over: dict) -> dict:
    ""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


NO_ID = "unnamed"


def owner_settings_file() -> Path:
    ""
    try:
        return Path(pwd.getpwnam(OWNER_USER).pw_dir) / "taffy" / "home.json"
    except KeyError:
        return Path("/home") / OWNER_USER / "taffy" / "home.json"


def unmigrated_owner(data: dict | None, path: Path | None) -> bool:
    ""
    if path is None or (data or {}).get("owner_home") is True or not _registered_owner():
        return False
    try:
        return os.path.realpath(path) == os.path.realpath(owner_settings_file())
    except (OSError, ValueError):
        return False


def load_home(path: Path | None) -> dict:
    ""

    data = None
    if path is not None and path.exists():
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")
    if unmigrated_owner(data, path):
        raise ValueError(f"{path} has no owner_home mark. Run hub/scripts/migrate-owner-home.py on this server first.")
    out = merge(DEFAULTS, data or {})
    if not str((data or {}).get("home_id") or ""):
        out["home_id"] = NO_ID
    return out


def owner_settings() -> dict:
    ""

    try:
        f = owner_settings_file()
        return load_home(f if os.access(f, os.R_OK) else None)
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)


def owner_first_name() -> str:
    ""

    p = owner_settings().get("person") or {}
    return str(p.get("first_name") or str(p.get("name") or "").split(" ")[0] or "")


def _default_public_host() -> str:
    ""
    from urllib.parse import urlsplit
    return urlsplit(str((owner_settings().get("network") or {}).get("public_url") or "")).hostname or "localhost"


def expand(value: str, home_dir: Path) -> Path:
    ""
    value = str(value)
    if value == "~":
        return home_dir
    if value.startswith("~/"):
        return home_dir / value[2:]
    return Path(value)


def new_home_settings(home_id: str, person: str, timezone: str, ports: dict, *, first_name: str | None = None,
                      public_host: str | None = None, whisper_url: str | None = None,
                      google_account: str | None = None, owner_name: str | None = None,
                      own_plan: bool = False) -> dict:
    ""


    first = first_name or person.split()[0]
    public_host = public_host or _default_public_host()
    machine = owner_settings()
    whisper_url = whisper_url or (machine.get("integrations") or {}).get("whisper_url")
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
                  "preview": {"base": ports["preview_base"], "per_zip": 10, "count": PREVIEW_COUNT},
                  "docker_subnet_base": ports["docker_subnet_base"]},
        "network": {"public_url": f"https://{public_host}:{serve}", "serve_port": serve},
        "host": {"name": (machine.get("host") or {}).get("name") or "server", "kind": "server",
                 "sandbox_image": DEFAULTS["host"]["sandbox_image"],
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


        "zips": {"max": None, "container_memory_mb": 2048},
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
                         "preview_span": PREVIEW_SPAN, "docker_subnet_base": "172.30", "broker_http": 8660}}


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
    ""


    used = set(listening)
    used_subnets = set()
    used_previews = []
    for entry in registry["homes"].values():
        p = entry.get("ports", {})
        used |= {p.get("hub"), p.get("hermes_api"), p.get("serve_port")}
        used_subnets.add(p.get("docker_subnet_base"))
        if p.get("preview_base"):
            used_previews.append((p["preview_base"], p["preview_base"] + p.get("preview_span", PREVIEW_SPAN)))
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

    span = PREVIEW_SPAN
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
    ""
    ports = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) > 3 and parts[3] == "0A":
                ports.add(int(parts[1].rsplit(":", 1)[1], 16))
    return ports







OWNER_SLOTS = {"builder": 0, "clients": 1, "school": 2, "career": 3, "money": 4}


def slots_path(home_dir: Path) -> Path:
    ""

    named = os.environ.get("ZERO_SLOTS_FILE", "").strip()
    if named:
        return Path(named)
    return home_dir / "taffy" / "state" / "zero-slots.json"


def read_slots(home_dir: Path, settings: dict) -> dict:
    try:
        data = json.loads(slots_path(home_dir).read_text())
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    return dict(OWNER_SLOTS) if settings.get("owner_home") is True else {}


def assign_slot(slots: dict, zip_id: str, count: int) -> dict:
    if zip_id in slots:
        return slots
    free = [i for i in range(count) if i not in slots.values()]
    if not free:
        raise RuntimeError(f"no preview slot left for {zip_id}; the home allows {count}")
    out = dict(slots)
    out[zip_id] = free[0]
    return out



OVERFLOW_PORTS = 25000


def preview_range(settings: dict, slot: int) -> tuple[int, int]:
    p = settings["ports"]["preview"]
    count = p.get("count") or PREVIEW_COUNT
    first = p["base"] + slot * p["per_zip"] if slot < count else OVERFLOW_PORTS + (slot - count) * p["per_zip"]
    return first, first + p["per_zip"] - 1


def subnet(settings: dict, slot: int) -> str:
    return f"{settings['ports']['docker_subnet_base']}.{slot}.0/24"






LEAD = "default"
FALLBACK = [{"provider": "openai-codex", "model": "gpt-6.1-sol"}]


def models_rules():
    ""

    import importlib.util
    candidates = [HERE.parent / "plugins" / "taffy-tools" / "models.py"]
    try:
        candidates.append(Path(pwd.getpwnam(OWNER_USER).pw_dir) / "taffy" / "plugins" / "taffy-tools" / "models.py")
    except KeyError:
        pass
    for f in candidates:
        if f.exists():
            spec = importlib.util.spec_from_file_location("zero_home_models", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def model_keys(settings: dict, zip_id: str) -> dict:
    ""


    rules = models_rules()
    block = rules.hermes_block(rules.setup(settings.get("models")), zip_id) if rules else None
    if not block:
        return {"model": {"provider": "claude-subscription-directsdk-experimental", "default": "opus"},
                "fallback_providers": copy.deepcopy(FALLBACK)}
    out: dict = {}
    for path, value in block.items():
        *parents, key = path.split(".")
        node = out
        for p in parents:
            node = node.setdefault(p, {})
        node[key] = copy.deepcopy(value)
    return out
PLUGINS = ["claude-subscription-directsdk-experimental", "taffy-tools"]


def zip_team(settings: dict, home_dir: Path, zip_id: str) -> str | None:
    ""



    state = expand(settings["paths"]["hub_state"], home_dir)
    try:
        c = sqlite3.connect(f"file:{state / 'hub.db'}?mode=ro", uri=True, timeout=2)
        try:
            row = c.execute("SELECT t.id FROM teams t JOIN team_members m ON m.team_id = t.id "
                            "WHERE m.bot_id=? AND t.state != 'deleted'", (zip_id,)).fetchone()
        finally:
            c.close()
        team = re.sub(r"[^A-Za-z0-9_-]", "", str(row[0])) if row else ""
        if team:
            return team
    except sqlite3.Error:
        pass
    try:
        reg = json.loads((state / "bots.json").read_text())
    except (OSError, ValueError):
        return None
    for e in reg if isinstance(reg, list) else []:
        if isinstance(e, dict) and e.get("id") == zip_id and (e.get("team") or e.get("merged_team")):
            return re.sub(r"[^A-Za-z0-9_-]", "", str(e.get("team") or e["merged_team"])) or None
    return None


def zip_absorbed(settings: dict, home_dir: Path, zip_id: str) -> list[str]:
    ""


    try:
        reg = json.loads((expand(settings["paths"]["hub_state"], home_dir) / "bots.json").read_text())
    except (OSError, ValueError):
        return []
    entry = next((e for e in reg if isinstance(e, dict) and e.get("id") == zip_id), {}) if isinstance(reg, list) else {}
    absorbed = entry.get("merged_from") if isinstance(entry.get("merged_from"), list) else []
    return [a for a in absorbed if isinstance(a, str) and re.fullmatch(r"[a-z][a-z0-9]{1,19}", a) and a != zip_id]


def zip_scripts(settings: dict, home_dir: Path, zip_id: str) -> Path:
    ""
    hermes_home = expand(settings["paths"].get("hermes_home") or DEFAULTS["paths"]["hermes_home"], home_dir)
    return (hermes_home if zip_id == LEAD else hermes_home / "profiles" / zip_id) / "scripts"


def zip_board_folder(settings: dict, home_dir: Path, zip_id: str) -> Path:
    ""

    hermes_home = expand(settings["paths"].get("hermes_home") or DEFAULTS["paths"]["hermes_home"], home_dir)
    return hermes_home / "kanban" / "workspaces" / zip_id


def docker_block(settings: dict, home_dir: Path, zip_id: str, slot: int, owner: bool) -> dict:
    work = expand(settings["paths"]["work"], home_dir)
    shared = expand(settings["paths"]["shared"], home_dir)
    first, last = preview_range(settings, slot)




    volumes = [f"{work}/{zip_id}:{work}/{zip_id}:rw", f"{shared}:{shared}:ro"]
    if zip_id == LEAD:
        volumes.insert(0, f"{work}:{work}:ro")
    volumes += [f"{work}/{a}:{work}/{a}:rw" for a in zip_absorbed(settings, home_dir, zip_id)]
    team = zip_team(settings, home_dir, zip_id)
    if team:
        volumes.append(f"{work}/teams/{team}:{work}/teams/{team}:rw")


    scripts = zip_scripts(settings, home_dir, zip_id)
    volumes.append(f"{scripts}:{scripts}:ro")



    skills = scripts.parent / "skills"
    volumes.append(f"{skills}:{skills}:ro")
    board = zip_board_folder(settings, home_dir, zip_id)
    volumes.append(f"{board}:{board}:rw")
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
        "docker_volumes": volumes,
        "docker_env": {"HOME": str(home_dir), "TZ": settings["person"]["timezone"]},
        "docker_forward_env": ["PIXABAY_API_KEY", "ZERO_PIXABAY_API_KEY"],
        "docker_extra_args": ["-p", f"127.0.0.1:{first}-{last}:{first}-{last}", "--network", f"zip-{zip_id}"],
    }


WEB_CONFIG = {"search_backend": "parallel", "extract_backend": "parallel", "keyless_fallback": True,
              "provider_tier": {"parallel": "free"}}


def managed_config(settings: dict, home_dir: Path, zip_id: str, slot: int | None, owner: bool) -> dict:
    ""

    lead = zip_id == LEAD
    local = lead and settings["host"]["lead_backend"] == "local"
    keys = model_keys(settings, zip_id)
    cfg: dict = {
        "plugins": {"enabled": list(PLUGINS), "disabled": []},
        **{k: keys.pop(k) for k in ("model", "fallback_providers")},
        "terminal": {"home_mode": "real"} if local else docker_block(settings, home_dir, zip_id, slot or 0, owner),
        "bot_desktop": {"placement": "gateway", "auto_start": True, "idle_stop_minutes": 20,
                        "min_free_memory_mb": 1200 if owner else 1500},
        "browser": {"headed": True, "restrict_evaluate": True, "allow_private_urls": True},




        "approvals": {"single_query_mode": "approve", "cron_mode": "approve"},

        "web": copy.deepcopy(WEB_CONFIG),
        "auxiliary": {"background_review": {"provider": "claude-subscription-directsdk-experimental",
                                            "model": "claude-haiku-5-5"},
                      "compression": {"provider": "claude-subscription-directsdk-experimental",
                                      "model": "claude-haiku-5-5"}},
    }
    if local:
        del cfg["bot_desktop"]["placement"]
    if lead:
        cfg["gateway"] = {"multiplex_profiles": True}
    return merge(cfg, keys)




CHECK_KEYS = ["plugins.enabled", "model.provider", "terminal", "bot_desktop.placement", "fallback_providers",
              "approvals.single_query_mode", "approvals.cron_mode",
              "gateway.multiplex_profiles", "web.search_backend", "web.extract_backend", "web.keyless_fallback",
              "web.provider_tier.parallel"]
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
    ""
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
    ""

    out = copy.deepcopy(existing)
    for k, v in managed.items():
        if k == "terminal":
            keep = (out.get(k) or {}).get("docker_forward_env") if isinstance(out.get(k), dict) else None
            out[k] = copy.deepcopy(v)
            if keep and "docker_forward_env" in out[k]:

                out[k]["docker_forward_env"] = list(dict.fromkeys(
                    [name for name in keep if name not in ("PEXELS_API_KEY", "ZERO_PEXELS_API_KEY")]
                    + v["docker_forward_env"]))
        elif k == "model":
            out[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
            if k == "auxiliary" and "compression" in v:


                out[k]["compression"] = copy.deepcopy(v["compression"])
        else:
            out[k] = copy.deepcopy(v)
    return out


def base_config() -> dict:
    ""
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
    ""

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
    ""
    lines = ["# The plan broker's environment for this home. Written by zero-home-render. Mode 600."]
    if settings["plan"]["claude"]["mode"] == "broker":
        lines.append(f"CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND={settings['plan']['claude']['command']}")
    if settings["plan"]["chatgpt"]["mode"] == "broker" and broker_secret:
        lines.append(f"HERMES_CODEX_BASE_URL={codex_base_url(settings, broker_secret)}")
    return "\n".join(lines) + "\n"


PLACEHOLDER_TOKEN = "zero-broker-placeholder"


def codex_placeholder_auth(existing: dict | None) -> dict | None:
    ""

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
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        pass
    try:
        from ruamel.yaml import YAML
        return YAML(typ="safe").load(text)
    except ImportError:
        pass
    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError("no YAML reader here: run this with the Hermes venv (~/taffy/.venv/bin/python)")






class Ops:
    ""


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
        ""
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
        ""
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
    ""
    entry = pwd.getpwuid(os.getuid())
    os.environ["HOME"] = entry.pw_dir
    os.environ["USER"] = os.environ["LOGNAME"] = entry.pw_name
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


    own = [f"{entry.pw_dir}/taffy/bin", f"{entry.pw_dir}/.local/bin", str(SRV / "bin")]
    rest = [d for d in os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(":") if d and d not in own]
    os.environ["PATH"] = ":".join(own + rest)


    try:
        os.chdir(entry.pw_dir)
    except OSError:
        pass


def runtime_env(uid: int | None = None) -> dict:
    ""
    uid = os.getuid() if uid is None else uid
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
    env["DOCKER_HOST"] = f"unix:///run/user/{uid}/docker.sock"
    env["HOME"] = str(Path(pwd.getpwuid(uid).pw_dir))
    env["PATH"] = f"{env['HOME']}/taffy/bin:{env['HOME']}/.local/bin:/srv/zero/bin:/usr/local/bin:/usr/bin:/bin"
    return env


def assert_home_identity(home_id: str, registry: dict) -> dict:
    ""
    entry = home_entry(home_id, registry)
    if not entry:
        raise SystemExit(f"{home_id} is not in {REGISTRY}. Run zero-home-sudo-prep home {home_id} first.")
    me = current_user()
    if entry.get("owner") or me == OWNER_USER or os.getuid() == 0:
        raise SystemExit("This step runs as the home's own user, never as the owner or root.")
    if me != entry["user"]:
        raise SystemExit(f"This step must run as {entry['user']}, not {me}.")
    return entry
