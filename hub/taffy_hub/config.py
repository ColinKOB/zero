"""Paths, bots and secrets for the Taffy hub. Secrets are read from the files that already own them.

Paths and ports come from the home's settings (home.py); with no settings file they are the values alien had for
Colin's home, so the names below mean what they always meant.
"""

import os
import pwd
from dataclasses import dataclass, replace
from pathlib import Path

from . import home

# ZERO_USER_HOME: the person's home folder when it is not the account's (a Mac home tested in a temporary HOME; the
# engine's LaunchAgents set it). Unset on alien, where the account's home is the one.
HOME = Path(os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir)
HERMES_HOME = home.path_of("hermes_home")
HERMES_BIN = home.path_of("hermes_bin")
KANBAN_DB = HERMES_HOME / "kanban.db"
HUB_DIR = home.path_of("hub_state")
HUB_DB = HUB_DIR / "hub.db"
SHARED_DIR = home.path_of("shared")
WORK_DIR = home.path_of("work")
PLUGIN_DIR = home.path_of("plugin")
API_BASE = f"http://127.0.0.1:{int(home.get('ports.hermes_api') or 8642)}"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(home.get("ports.hub") or 8650)


@dataclass(frozen=True)
class Bot:
    id: str          # Hermes profile name; "default" is Taffy
    name: str
    role: str
    color: str       # hex, used by the app
    symbol: str      # SF Symbol name, used by the app
    shape: str = "pebble"   # character shape in the app: circle, pebble, squircle, capsule, triangle, hexagon, cloud, droplet


LEAD = Bot("default", "Zero", "Lead. Talks with you and runs the team.", "#F2711C", "sparkles", "taffy")
# Every home starts with the lead alone; the other Zips are listed in the home's registry (bots.json).
BUILTIN = [LEAD]
# The five Zips Colin's home had built in before Oct 9. They belong in his registry (`taffy-hub migrate-registry`
# writes them there, first, in this order); until then the owner's home still shows them, so nothing changes for him.
OWNER_ZIPS = [
    Bot("school", "School", "Lakeview classes, deadlines, readings and sources.", "#3B82F6", "graduationcap", "droplet"),
    Bot("builder", "Builder", "Lumenly, Fernhill AI, marketing and your builds.", "#8B5CF6", "hammer", "squircle"),
    Bot("clients", "Clients", "Brightpath, Blue Heron and GWI.", "#10B981", "person.2", "cloud"),
    Bot("money", "Money", "Spending, bills, savings and the household sheet.", "#EAB308", "dollarsign.circle", "hexagon"),
    Bot("career", "Career", "Acme Homes, résumé and job decisions.", "#EC4899", "briefcase", "triangle"),
]
# Bots Taffy creates with team_add_bot are listed here by the plugin.
REGISTRY = HUB_DIR / "bots.json"
# Names the person gave bots in the app, by bot id; they win over the names above and in the registry.
NAMES = HUB_DIR / "bot-names.json"
SHAPES = {"circle", "pebble", "squircle", "capsule", "triangle", "hexagon", "cloud", "droplet", "egg"}
# How each Zip looks, chosen in the app's Change Look screen, by bot id (see looks.py).
LOOKS = HUB_DIR / "bot-looks.json"

# Mutated in place by reload_bots(), so every module sees new bots without re-importing.
BOTS: list[Bot] = list(BUILTIN)
BOTS_BY_ID: dict[str, Bot] = {b.id: b for b in BOTS}
_registry_mtime: float | None = None


def reload_bots() -> bool:
    """Picks up bots added or deleted since the last call. Returns True when the list changed.
    A bot whose Hermes profile is gone (deleted from the app) drops out, built-in or not."""
    global _registry_mtime
    try:
        mtime = REGISTRY.stat().st_mtime
    except OSError:
        mtime = None
    try:
        names_mtime = NAMES.stat().st_mtime
    except OSError:
        names_mtime = None
    profiles_dir = HERMES_HOME / "profiles"
    present = tuple(sorted(p.name for p in profiles_dir.iterdir() if p.is_dir())) if profiles_dir.is_dir() else ()
    signature = (mtime, names_mtime, present)
    if signature == _registry_mtime:
        return False
    _registry_mtime = signature
    entries = _registry_entries() if mtime is not None else []
    extra: list[Bot] = []
    listed = set()
    for e in entries:
        bid = e["id"]
        if bid != LEAD.id and bid not in listed and (HERMES_HOME / "profiles" / bid).is_dir():
            listed.add(bid)
            extra.append(Bot(bid, str(e.get("name") or bid)[:24], str(e.get("role") or "")[:90],
                             str(e.get("color") or "#64748B")[:7], str(e.get("symbol") or "sparkles")[:40],
                             e.get("shape") if e.get("shape") in SHAPES else "pebble"))
    fallback = [b for b in OWNER_ZIPS if b.id in present and b.id not in listed] if home.is_owner_home() else []
    builtin = [LEAD] + fallback
    names = {}
    if names_mtime is not None:
        import json
        try:
            names = {str(k): str(v)[:24] for k, v in json.loads(NAMES.read_text()).items() if str(v).strip()}
        except (OSError, ValueError, AttributeError):
            names = {}
    BOTS[:] = [replace(b, name=names[b.id]) if b.id in names else b for b in builtin + extra]
    BOTS_BY_ID.clear()
    BOTS_BY_ID.update({b.id: b for b in BOTS})
    return True


def _registry_entries() -> list[dict]:
    """The registry's entries with a valid id, in file order."""
    import json, re
    try:
        entries = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and re.fullmatch(r"[a-z][a-z0-9]{1,19}", str(e.get("id", "")))]


def is_custom(bot_id: str) -> bool:
    """A Zip the person made (the app's "custom" flag): not the lead, and not one of the owner's original five."""
    return bot_id != LEAD.id and not (home.is_owner_home() and bot_id in {b.id for b in OWNER_ZIPS})


def migrate_registry(write: bool = True) -> list[dict]:
    """Puts the owner's five original Zips into the registry, first and in their old order, with their colors,
    symbols and shapes; entries already there are kept as they are. Returns the new registry. Idempotent."""
    import json, time
    entries = _registry_entries() if REGISTRY.exists() else []
    have = {e["id"] for e in entries}
    head = [{"id": b.id, "name": b.name, "role": b.role, "color": b.color, "shape": b.shape, "symbol": b.symbol,
             "created_at": time.time()} for b in OWNER_ZIPS if b.id not in have]
    new = head + entries
    if write and head:
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        tmp = REGISTRY.with_suffix(".migrate.tmp")
        tmp.write_text(json.dumps(new, indent=1))
        tmp.replace(REGISTRY)
    return new


reload_bots()


def _env_value(path: Path, key: str) -> str | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        return None
    return None


def api_key(bot_id: str) -> str:
    env = HERMES_HOME / ".env" if bot_id == "default" else HERMES_HOME / "profiles" / bot_id / ".env"
    key = _env_value(env, "API_SERVER_KEY")
    if not key:
        raise RuntimeError(f"No API_SERVER_KEY for {bot_id}")
    return key


def api_prefix(bot_id: str) -> str:
    return API_BASE if bot_id == "default" else f"{API_BASE}/p/{bot_id}"
