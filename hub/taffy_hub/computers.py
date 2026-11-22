""






import fcntl
import json
import logging
import os
import subprocess
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def slots_path() -> Path:
    ""
    named = os.environ.get("ZERO_SLOTS_FILE", "").strip()
    if named:
        return Path(named)
    return config.HOME / "taffy" / "state" / "zero-slots.json"


def _docker_env() -> dict:
    env = dict(os.environ)
    env.setdefault("DOCKER_HOST", f"unix:///run/user/{os.getuid()}/docker.sock")
    return env


def _held(bot_id: str) -> bool:
    try:
        slots = json.loads(slots_path().read_text())
    except (OSError, ValueError):
        return False
    return isinstance(slots, dict) and bot_id in slots


def release(bot_id: str) -> None:
    ""






    if not _held(bot_id):
        return
    network = f"zip-{bot_id}"
    try:
        env = _docker_env()
        ids = subprocess.run(["docker", "ps", "-aq", "--filter", f"network={network}"], capture_output=True,
                             text=True, timeout=30, env=env).stdout.split()
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True, timeout=60, env=env)
        p = subprocess.run(["docker", "network", "rm", network], capture_output=True, text=True, timeout=30, env=env)
        out = (p.stdout + p.stderr).strip()
        if p.returncode != 0 and "not found" not in out.lower() and "no such network" not in out.lower():
            log.warning("%s was not removed, so its slot stays taken: %s", network, out[-300:])
            return
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("%s was not removed, so its slot stays taken: %s", network, e)
        return
    path = slots_path()
    try:
        with open(path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            slots = json.loads(path.read_text())
            if not isinstance(slots, dict) or bot_id not in slots:
                return
            del slots[bot_id]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(slots, indent=1, sort_keys=True) + "\n")
            tmp.replace(path)
    except (OSError, ValueError):
        return
