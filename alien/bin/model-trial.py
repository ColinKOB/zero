#!/usr/bin/env python3
"""The blind model trial (Colin, Oct 11): half of the Zips run Sol (gpt-6.1-sol on the ChatGPT plan) and half run Opus,
picked at random, and nobody is told which until the trial ends. "I want it to be anonymous."

    model-trial.py start     pick at random, switch those Zips to Sol, seal the assignment, print no names
    model-trial.py status    how long it has run, still without names
    model-trial.py reveal    print the assignment (for the end-of-trial report)
    model-trial.py end       put every Zip back on Opus

The assignment is kept in ~/taffy/owner/model-trial.json (readable only by this user). Each Zip's config.yaml gets
the new model, and set-toolsets.py then gives a Sol Zip Opus as its backup. Restart taffy-gateway afterwards, when
the Zips are idle. Run with the home's venv python, like set-toolsets.py.
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ruamel.yaml import YAML

HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".taffy"))
SEALED = Path.home() / "taffy" / "owner" / "model-trial.json"
SOL = {"provider": "openai-codex", "default": "gpt-6.1-sol"}
OPUS = {"provider": "claude-subscription-directsdk-experimental", "default": "opus"}


def zips() -> dict[str, Path]:
    out = {"default": HOME / "config.yaml"}
    for p in sorted((HOME / "profiles").glob("*/config.yaml")):
        if not p.parent.name.startswith("."):
            out[p.parent.name] = p
    return out


def set_model(path: Path, model: dict) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text())
    shutil.copy2(path, path.with_name(f"config.yaml.bak-trial-{time.strftime('%Y%m%d-%H%M%S')}"))
    data.setdefault("model", {}).update(model)
    with path.open("w") as f:
        yaml.dump(data, f)


def toolsets() -> None:
    script = Path.home() / "taffy" / "bin" / "set-toolsets.py"
    subprocess.run([sys.executable, str(script)], check=True, capture_output=True)


def start() -> None:
    if SEALED.exists():
        sys.exit("A trial is already running. Use status, reveal or end.")
    ids = list(zips())
    picked = set()
    while len(picked) < len(ids) // 2:
        picked.add(ids[secrets.randbelow(len(ids))])
    for z, path in zips().items():
        set_model(path, SOL if z in picked else OPUS)
    SEALED.parent.mkdir(parents=True, exist_ok=True)
    SEALED.write_text(json.dumps({"started": time.time(), "sol": sorted(picked),
                                  "opus": sorted(set(ids) - picked)}, indent=1))
    os.chmod(SEALED, 0o600)
    toolsets()
    print(f"{len(picked)} of {len(ids)} Zips now run Sol and the rest run Opus. The assignment is sealed. "
          "Restart taffy-gateway when the Zips are idle.")


def status() -> None:
    if not SEALED.exists():
        sys.exit("No trial is running.")
    d = json.loads(SEALED.read_text())
    print(f"Running for {(time.time() - d['started']) / 86400:.1f} days: {len(d['sol'])} on Sol, {len(d['opus'])} on Opus.")


def reveal() -> None:
    print(SEALED.read_text() if SEALED.exists() else "No trial is running.")


def end() -> None:
    for path in zips().values():
        set_model(path, OPUS)
    if SEALED.exists():
        SEALED.rename(SEALED.with_name(f"model-trial-ended-{time.strftime('%Y%m%d')}.json"))
    toolsets()
    print("Every Zip runs Opus again. Restart taffy-gateway when the Zips are idle.")


if __name__ == "__main__":
    {"start": start, "status": status, "reveal": reveal, "end": end}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
