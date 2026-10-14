"""Runs the real hub from this repo on the Mac against a temporary home, with fake_hermes.py in place of Hermes.

This is the dry mode of the end-to-end harness: the simulator pairs with this hub, onboards, and the hub-state
assertions read its home, before any test home exists on alien. Nothing here reaches a real home, plan or account:

- The home is a temporary folder laid out by homes.py (shape `test` by default) with every path inside it.
- `ZERO_HOME` names its home.json, so a hub that reads settings (WP1) picks it up; for a hub that does not yet,
  the launcher points config's paths at the folder before any other hub module loads, then rewrites every
  module-level path that still points into the real home (the plugin's consent.db included).
- `claude`, `codex` and `himalaya` in the home's ~/.local/bin are stubs that say "not logged in" (or, for
  himalaya, list an INBOX), so no account check, connector run or voice call can use Colin's sign-ins. PATH is
  cut down to that folder and the system's.
- Hermes is fake_hermes.py on a free loopback port. No model is called.
- Ports are checked before binding; the launcher refuses a port in use and never stops anything it did not start.

  python3 fixture_hub.py --home /tmp/wp5-home --state /tmp/wp5-home/state.json [--shape test] [--port 8652]

The state file gets {"hub": url, "home": dir, "home_json": path, "code": pairing code, "pid": n, "hermes": url}.
Stop it with SIGTERM to the pid (zero-e2e.sh does).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import pwd
import secrets
import socket
import sqlite3
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
HUB_DIR = HERE.parents[1]
REPO = HUB_DIR.parent
sys.path.insert(0, str(HERE))

import fake_hermes  # noqa: E402
import homes  # noqa: E402

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)

STUBS = {
    "claude": """#!/bin/sh
# Harness stub: never a real Claude sign-in.
case "$*" in
  *--version*) echo "2.0.0 (harness stub)"; exit 0 ;;
  "auth status"*) echo '{"loggedIn": false}'; echo "Not logged in" >&2; exit 1 ;;
esac
echo "harness stub: claude is not available in dry mode" >&2
exit 1
""",
    "codex": """#!/bin/sh
# Harness stub: never a real ChatGPT sign-in.
case "$*" in
  "login status"*) echo "Not logged in"; exit 1 ;;
  *--version*) echo "codex-cli 0.0.0 (harness stub)"; exit 0 ;;
esac
echo "harness stub: codex is not available in dry mode" >&2
exit 1
""",
    "himalaya": """#!/bin/sh
# Harness stub: a mailbox check that always finds an INBOX (dry mode has no mail server).
case "$*" in
  *"folder list"*|*"folders list"*) echo '[{"name":"INBOX","desc":""}]'; exit 0 ;;
  *--version*) echo "himalaya 1.0.0 (harness stub)"; exit 0 ;;
esac
echo '[]'
exit 0
""",
}

KANBAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT, status TEXT,
  created_by TEXT, created_at INTEGER, started_at INTEGER, completed_at INTEGER, result TEXT, current_run_id INTEGER);
CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS task_runs (id INTEGER PRIMARY KEY, task_id TEXT, status TEXT, outcome TEXT, summary TEXT,
  error TEXT, started_at INTEGER, ended_at INTEGER);
CREATE TABLE IF NOT EXISTS task_attachments (id INTEGER PRIMARY KEY, task_id TEXT, filename TEXT, size INTEGER,
  content_type TEXT, stored_path TEXT);
CREATE TABLE IF NOT EXISTS task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER);
"""


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare(root: Path, shape: str, hub_port: int, api_port: int) -> Path | None:
    """Lays out the home, its stubs, the lead's key and an empty board. Returns home.json (None for shape none)."""
    root = root.resolve()
    if root == REAL_HOME or REAL_HOME in root.parents and root.parts[len(REAL_HOME.parts)] in (".taffy", "taffy"):
        raise SystemExit(f"refusing to use {root}: it is (inside) a real home")
    _, home_json = homes.make_home(shape, root, hermetic=True, hub_port=hub_port, api_port=api_port)
    for name, body in STUBS.items():
        p = root / ".local" / "bin" / name
        p.write_text(body)
        p.chmod(0o755)
    taffy_bin = root / "taffy" / "bin" / "taffy"
    taffy_bin.write_text("#!/bin/sh\necho 'harness stub: no Hermes CLI in dry mode' >&2\nexit 1\n")
    taffy_bin.chmod(0o755)
    env = root / ".taffy" / ".env"
    if not env.exists():
        env.write_text(f"API_SERVER_KEY={secrets.token_urlsafe(24)}\n")
        env.chmod(0o600)
    user = root / ".taffy" / "memories" / "USER.md"
    if not user.exists():
        user.write_text("")
    with sqlite3.connect(root / ".taffy" / "kanban.db") as c:
        c.executescript(KANBAN_SCHEMA)
    return home_json


def _rewrite(value, root: Path):
    if isinstance(value, Path):
        try:
            rel = value.relative_to(REAL_HOME)
        except ValueError:
            return value
        return root / rel
    return value


def load_hub(root: Path, api_port: int, hub_port: int):
    """Imports the hub with every path pointed into `root`. Returns the FastAPI app."""
    sys.path.insert(0, str(HUB_DIR))
    from taffy_hub import config
    config.HOME = root
    config.HERMES_HOME = root / ".taffy"
    config.HERMES_BIN = root / "taffy" / "bin" / "taffy"
    config.KANBAN_DB = config.HERMES_HOME / "kanban.db"
    config.HUB_DIR = root / "taffy" / "hub-state"
    config.HUB_DB = config.HUB_DIR / "hub.db"
    config.REGISTRY = config.HUB_DIR / "bots.json"
    config.NAMES = config.HUB_DIR / "bot-names.json"
    config.LOOKS = config.HUB_DIR / "bot-looks.json"
    config.API_BASE = f"http://127.0.0.1:{api_port}"
    config.LISTEN_PORT = hub_port
    config._registry_mtime = None
    config.reload_bots()
    import taffy_hub
    for m in pkgutil.iter_modules(taffy_hub.__path__):
        importlib.import_module(f"taffy_hub.{m.name}")
    for name, mod in list(sys.modules.items()):
        if not (name.startswith("taffy_hub") or name.startswith("taffy_consent") or name.startswith("taffy_tools")
                or name.startswith("taffy_safe_files")):
            continue
        for attr, value in list(vars(mod).items()):
            new = _rewrite(value, root)
            if new is not value:
                setattr(mod, attr, new)
    leftovers = [f"{n}.{a}" for n, mod in sys.modules.items() if n.startswith("taffy")
                 for a, v in vars(mod).items() if isinstance(v, Path) and REAL_HOME in v.parents
                 and root not in v.parents]
    if leftovers:
        raise SystemExit(f"refusing to start: paths still in the real home: {', '.join(leftovers[:8])}")
    from taffy_hub import app as app_module
    return app_module.app


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Run the hub against a temporary home (dry mode).")
    ap.add_argument("--home", required=True, help="the temporary home folder (created)")
    ap.add_argument("--state", required=True, help="where to write the JSON state")
    ap.add_argument("--shape", default="test", choices=homes.SHAPES)
    ap.add_argument("--port", type=int, default=0, help="hub port; 0 picks 8652 when free, else any free port")
    ap.add_argument("--hermes-port", type=int, default=0)
    ap.add_argument("--code-hours", type=float, default=2)
    a = ap.parse_args(argv)

    hub_port = a.port or (8652 if port_free(8652) else free_port())
    api_port = a.hermes_port or free_port()
    for p in (hub_port, api_port):
        if not port_free(p):
            raise SystemExit(f"port {p} is in use; pick another (nothing was stopped)")
    root = Path(a.home)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    home_json = prepare(root, a.shape, hub_port, api_port)

    os.environ.update({"HOME": str(root), "HERMES_HOME": str(root / ".taffy"),
                       "PATH": f"{root}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"})
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(k, None)
    if home_json:
        os.environ["ZERO_HOME"] = str(home_json)
    else:
        os.environ.pop("ZERO_HOME", None)

    app = load_hub(root, api_port, hub_port)
    from taffy_hub import store
    code = store.new_pairing_code(int(a.code_hours * 3600))

    hermes = fake_hermes.serve(root, api_port)
    threading.Thread(target=hermes.serve_forever, daemon=True).start()

    state = {"hub": f"http://127.0.0.1:{hub_port}", "hermes": f"http://127.0.0.1:{api_port}", "home": str(root),
             "home_json": str(home_json) if home_json else None, "shape": a.shape, "code": code, "pid": os.getpid()}
    Path(a.state).write_text(json.dumps(state, indent=1))
    print(f"Pairing code: {code[:4]}-{code[4:]} (valid for {a.code_hours:g} hours)", flush=True)
    print(f"Server: {state['hub']}", flush=True)

    import uvicorn
    try:
        uvicorn.run(app, host="127.0.0.1", port=hub_port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        hermes.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
