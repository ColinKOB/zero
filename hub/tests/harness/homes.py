"""Sample homes for the harness: the `home.json` shapes the plan fixes (docs/plans/2026-10-09-zero-for-anyone-plan.md,
section 4, "The Settings File"), and a helper that lays a home out in a temporary folder.

Shapes:
  none    no settings file at all; the hub and plugin fall back to their defaults (today's alien values)
  colin   Colin's file as the integration step renders it from the defaults (own plans, Claude connectors, Tally,
          Trello, three mailboxes, the judge on, the Mac backup copy expected)
  riley   the second home: broker plans, no connectors, one mailbox, one CalDAV calendar
  test    the throwaway test home a stranger's onboarding runs against (zero-test on alien): broker plans, no
          connectors, no mailbox until the onboarding adds one

`make_home(shape, root)` writes a home tree under `root` (the folders the hub and plugin expect, an empty lead
profile, a templates file) and, for every shape but `none`, a `taffy/home.json`. With `hermetic=True` (the default)
every path in the file points inside `root`, so nothing a test does can reach the real home of whoever runs it.

CLI:  python3 homes.py write <shape> <dir> [--port N] [--not-hermetic]   prints the home.json path (or "" for none)
      python3 homes.py show <shape>                                       prints the shape as JSON
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHAPES = ("none", "colin", "riley", "test")

# The schema's defaults: today's alien values (section 4). Every reader must fall back to these.
DEFAULTS: dict = {
    "version": 1,
    "home_id": "colin",
    "person": {"name": "", "first_name": "", "timezone": "America/New_York",
               "quiet_hours": {"start": 23, "end": 7}, "pronoun_free": True},
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
    "judge": {"enabled": False},
    "push": {"key_dir": "~/.config/taffy-push", "team_id": "YOUR_TEAM_ID", "topic": "com.example.Taffy"},
    "backup": {"repo": "~/backups/taffy-restic", "drop": None, "mac_copy_expected": False},
    "zips": {"max": 8, "container_memory_mb": None},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _broker(home_id: str) -> dict:
    return {"claude": {"mode": "broker", "command": "/srv/zero/broker/claude-client"},
            "chatgpt": {"mode": "broker", "codex_command": "/srv/zero/broker/codex-client",
                        "codex_base_url": f"http://127.0.0.1:8660/{home_id}"},
            "connectors": "none", "frugal": True}


COLIN = _merge(DEFAULTS, {
    "home_id": "colin",
    "person": {"name": "Colin O'Brien", "first_name": "Colin"},
    "paths": {"background_docs": ["~/taffy/shared/background-notes.md",
                                  "~/taffy/shared/background-record.txt"]},
    "host": {"lead_backend": "local"},
    "mail": {"accounts": [
        {"id": "personal", "address": "jdoe1@example.com", "label": "Personal", "default": True,
         "owner_zip": "default"},
        {"id": "lakeview", "address": "jd910@lakeview.test", "label": "Lakeview", "default": False, "owner_zip": "school"},
        {"id": "goodworks", "address": "colin@goodworks.test", "label": "GoodWorks", "default": False,
         "owner_zip": "clients"}]},
    "integrations": {"tally": {"url": "http://127.0.0.1:18930", "env": "~/tally/server/.env"},
                     "trello": {"board_id": "AbCd1234", "board_name": "Colin | School & Projects"}},
    "judge": {"enabled": True},
    "backup": {"mac_copy_expected": True},
})

RILEY = _merge(DEFAULTS, {
    "home_id": "riley",
    "person": {"name": "Riley Ellis", "first_name": "Riley"},
    "ports": {"hub": 8651, "hermes_api": 8643, "preview": {"base": 18300}, "docker_subnet_base": "172.31"},
    "network": {"public_url": "https://alien.tail0000.ts.net:6444", "serve_port": 6444},
    "host": {"lead_backend": "docker"},
    "plan": _broker("riley"),
    "mail": {"accounts": [{"id": "icloud", "address": "riley@icloud.com", "label": "Personal", "default": True,
                           "owner_zip": "default"}]},
    "calendars": [{"id": "icloud", "kind": "caldav", "url": "https://caldav.icloud.com",
                   "username": "riley@icloud.com"}],
    "backup": {"drop": "/srv/zero/backups/riley"},
    "zips": {"max": 4, "container_memory_mb": 2048},
})

# The stranger. Her answers in the onboarding are in persona.json; her settings start nearly empty, as
# zero-home-create writes them before anyone has onboarded.
TEST = _merge(DEFAULTS, {
    "home_id": "test",
    "person": {"name": "", "first_name": ""},
    "ports": {"hub": 8652, "hermes_api": 8644, "preview": {"base": 18400}, "docker_subnet_base": "172.32"},
    "network": {"public_url": "https://alien.tail0000.ts.net:6445", "serve_port": 6445},
    "host": {"lead_backend": "docker"},
    "plan": _broker("test"),
    "backup": {"drop": "/srv/zero/backups/test"},
    "zips": {"max": 4, "container_memory_mb": 2048},
})

_BY_NAME = {"colin": COLIN, "riley": RILEY, "test": TEST}


def shape(name: str) -> dict | None:
    """A copy of the named shape (None for "none")."""
    if name not in SHAPES:
        raise ValueError(f"unknown home shape {name!r}; use one of {', '.join(SHAPES)}")
    return copy.deepcopy(_BY_NAME[name]) if name in _BY_NAME else None


def _hermetic(data: dict, home: Path) -> dict:
    """Every "~/..." path pointed inside the temporary home."""
    def fix(v):
        if isinstance(v, str) and v.startswith("~/"):
            return str(home / v[2:])
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        return v
    return fix(data)


# The folders zero-home-create lays out (section 4, "Users and Layout"), relative to the user's home.
LAYOUT = (".taffy/memories", ".taffy/profiles", "taffy/hub-state", "taffy/work", "taffy/shared", "taffy/owner",
          "taffy/bin", "taffy/plugins", ".config/himalaya", ".config/taffy-push", ".local/bin")


def make_home(name: str, root: Path, hermetic: bool = True, hub_port: int | None = None,
              api_port: int | None = None, templates: Path | None = None) -> tuple[Path, Path | None]:
    """Lays out a home under `root` (root itself is the user's home directory). Returns (root, home.json or None)."""
    root = Path(root)
    for rel in LAYOUT:
        (root / rel).mkdir(parents=True, exist_ok=True)
    tpl = templates or (HERE / "fixtures" / "zip-templates.json")
    if tpl.exists():
        (root / "taffy" / "shared" / "zip-templates.json").write_text(tpl.read_text())
    data = shape(name)
    if data is None:
        return root, None
    if hermetic:
        data = _hermetic(data, root)
    if hub_port:
        data["ports"]["hub"] = hub_port
    if api_port:
        data["ports"]["hermes_api"] = api_port
    path = root / "taffy" / "home.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return root, path


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "show":
        print(json.dumps(shape(argv[1]), indent=2))
        return 0
    if len(argv) >= 3 and argv[0] == "write":
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else None
        _, path = make_home(argv[1], Path(argv[2]), hermetic="--not-hermetic" not in argv, hub_port=port)
        print(path or "")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
