""





import os
import pwd
from dataclasses import dataclass, replace
from pathlib import Path

from . import home



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
    id: str
    name: str
    role: str
    color: str
    symbol: str
    shape: str = "pebble"
    team: str | None = None



    sandbox: bool = False



    mac_practice: bool = False




    practice_logins: bool = False




    practice: bool = False



    merged_from: tuple[str, ...] = ()
    merged_team: str | None = None
    modes: tuple[str, ...] = ()


LEAD = Bot("default", "Zero", "Lead. Talks with you and runs the team.", "#F2711C", "sparkles", "taffy")

BUILTIN = [LEAD]


OWNER_ZIPS = [
    Bot("school", "School", "Classes, deadlines, readings and sources.", "#3B82F6", "graduationcap", "droplet"),
    Bot("builder", "Builder", "Products, marketing and your builds.", "#8B5CF6", "hammer", "squircle"),
    Bot("clients", "Clients", "Client work and follow-ups.", "#10B981", "person.2", "cloud"),
    Bot("money", "Money", "Spending, bills, savings and the household sheet.", "#EAB308", "dollarsign.circle", "hexagon"),
    Bot("career", "Career", "Résumé and job decisions.", "#EC4899", "briefcase", "triangle"),
]


def live_registry() -> Path:
    ""
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / "taffy" / "hub-state" / "bots.json"


def refuse_live_registry(path: Path) -> None:
    ""
    if os.environ.get("ZERO_TESTS") != "1":
        return
    live = live_registry()
    try:
        hit = Path(path).resolve() == live.resolve()
    except OSError:
        hit = os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(live))
    if hit:
        raise RuntimeError(
            f"Refusing to read or write the live registry {live} while ZERO_TESTS=1. "
            "Point config.REGISTRY at a temporary bots.json before reloading the team, "
            "or set ZERO_USER_HOME to a throwaway directory, as tests/run_parallel.py does.")


class _RegistryPath(Path):
    ""

    def _guard(self) -> None:
        refuse_live_registry(self)

    def read_text(self, *args, **kwargs):
        self._guard()
        return super().read_text(*args, **kwargs)

    def read_bytes(self, *args, **kwargs):
        self._guard()
        return super().read_bytes(*args, **kwargs)

    def write_text(self, *args, **kwargs):
        self._guard()
        return super().write_text(*args, **kwargs)

    def write_bytes(self, *args, **kwargs):
        self._guard()
        return super().write_bytes(*args, **kwargs)

    def open(self, *args, **kwargs):
        self._guard()
        return super().open(*args, **kwargs)

    def stat(self, *args, **kwargs):
        self._guard()
        return super().stat(*args, **kwargs)

    def exists(self, *args, **kwargs):
        self._guard()
        return super().exists(*args, **kwargs)

    def unlink(self, *args, **kwargs):
        self._guard()
        return super().unlink(*args, **kwargs)

    def replace(self, target):
        self._guard()
        refuse_live_registry(Path(target))
        return super().replace(target)

    def rename(self, target):
        self._guard()
        refuse_live_registry(Path(target))
        return super().rename(target)


def _registry_file() -> Path:
    ""
    named = os.environ.get("ZERO_REGISTRY", "").strip()
    return Path(named) if named else HUB_DIR / "bots.json"



REGISTRY = _RegistryPath(_registry_file())

NAMES = HUB_DIR / "bot-names.json"
SHAPES = {"circle", "pebble", "squircle", "capsule", "triangle", "hexagon", "cloud", "droplet", "egg"}

LOOKS = HUB_DIR / "bot-looks.json"


BOTS: list[Bot] = list(BUILTIN)
BOTS_BY_ID: dict[str, Bot] = {b.id: b for b in BOTS}
_registry_mtime: float | None = None


def reload_bots() -> bool:
    ""

    global _registry_mtime
    refuse_live_registry(REGISTRY)
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
                             e.get("shape") if e.get("shape") in SHAPES else "pebble",
                             str(e["team"])[:40] if e.get("team") else None, e.get("sandbox") is True,
                             mac_practice=e.get("sandbox") is True and e.get("mac_practice") is True,
                             practice=e.get("sandbox") is True and e.get("practice") is True,
                             practice_logins=e.get("sandbox") is True and e.get("mac_practice") is True
                             and e.get("mac_practice_logins") is True,
                             **_merged(e)))
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
    try:
        from . import telemetry
        telemetry.zips_changed([e for e in entries if e["id"] in BOTS_BY_ID])
    except Exception:
        pass
    return True


ZIP_ID = r"[a-z][a-z0-9]{1,19}"


def _merged(e: dict) -> dict:
    ""
    import re
    absorbed = e.get("merged_from") if isinstance(e.get("merged_from"), list) else []
    modes = e.get("modes") if isinstance(e.get("modes"), list) else []
    team = str(e.get("merged_team") or "")
    return {"merged_from": tuple(str(a) for a in absorbed if re.fullmatch(ZIP_ID, str(a))),
            "merged_team": team if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", team) else None,
            "modes": tuple(" ".join(str(m.get("name") or "").split())[:24] for m in modes
                           if isinstance(m, dict) and str(m.get("name") or "").strip())}


def _registry_entries() -> list[dict]:
    ""
    import json, re
    refuse_live_registry(REGISTRY)
    try:
        entries = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and re.fullmatch(ZIP_ID, str(e.get("id", "")))]


def hellos() -> dict[str, str]:
    ""
    return {e["id"]: " ".join(str(e.get("hello") or "").split())[:300]
            for e in _registry_entries() if str(e.get("hello") or "").strip()}


def is_custom(bot_id: str) -> bool:
    ""
    return bot_id != LEAD.id and not (home.is_owner_home() and bot_id in {b.id for b in OWNER_ZIPS})








PRIVATE_ZIPS = frozenset({"connect"})


def is_private(bot_id: str | None) -> bool:
    ""
    return str(bot_id or "") in PRIVATE_ZIPS









CROSSES = ("title", "question")


def sealed(*owners) -> bool:
    ""

    return any(is_private(o) for o in owners)


def is_sandbox(bot_id: str | None) -> bool:
    b = BOTS_BY_ID.get(bot_id or "")
    return getattr(b, "sandbox", False) is True


def mac_practice(bot_id: str | None) -> bool:
    ""
    b = BOTS_BY_ID.get(bot_id or "")
    return getattr(b, "sandbox", False) is True and getattr(b, "mac_practice", False) is True


def practice(bot_id: str | None) -> bool:
    ""
    b = BOTS_BY_ID.get(bot_id or "")
    return getattr(b, "sandbox", False) is True and getattr(b, "practice", False) is True


def practice_logins(bot_id: str | None) -> bool:
    ""
    return mac_practice(bot_id) and getattr(BOTS_BY_ID.get(bot_id or ""), "practice_logins", False) is True


def sandboxes() -> tuple[str, ...]:
    return tuple(b.id for b in BOTS if b.sandbox)


def is_money(bot_id: str) -> bool:
    ""



    return (home.is_owner_home() and bot_id == "money") or any(
        e["id"] == bot_id and e.get("template") == "money" for e in _registry_entries())


def migrate_registry(write: bool = True) -> list[dict]:
    ""

    import json, time
    refuse_live_registry(REGISTRY)
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




_real_reload_bots = reload_bots

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
