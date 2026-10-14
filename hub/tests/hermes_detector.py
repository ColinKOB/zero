"""Hermes's own dangerous-command detector for the hub's tests: the one the gateway uses, not the gate's copy of its
patterns (Oct 8 QA round 5, M1: `python3 -c "import json; ..."` raised a card on alien because Hermes's detector calls
a fragment cut at the quoted ";" a malformed payload, and tiers.hermes_like has no such check, so the tests passed).

detect(text) returns Hermes's description of what it would stop to ask about, or None. It uses Hermes's own module
when Hermes is importable (on alien with its source on the path) and the copy in hermes_fixture/ otherwise. The copy is
loaded under private names, and `tools` is put in sys.modules only while it runs, so nothing else in the tests can
import it by accident (the gate looks for `tools.approval_detection` and `tools.terminal_tool`).
"""

import importlib.util
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "hermes_fixture" / "tools"
_MISSING = object()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _real():
    try:
        from tools import approval_detection
    except Exception:
        return None
    return approval_detection


def _vendored(mod, ansi):
    pkg = type(sys)("tools")
    pkg.__path__ = []
    pkg.ansi_strip = ansi
    # The detector imports these at call time: tools.ansi_strip from the copy, and the two it can do without blocked,
    # so a `cron` or `hermes_constants` elsewhere on the path is never picked up.
    swap = {"tools": pkg, "tools.ansi_strip": ansi, "cron": None, "cron.lifecycle_guard": None, "hermes_constants": None}

    def detect_dangerous_command(text: str) -> tuple:
        saved = {k: sys.modules.get(k, _MISSING) for k in swap}
        sys.modules.update(swap)
        try:
            return mod.detect_dangerous_command(text)
        finally:
            for k, v in saved.items():
                if v is _MISSING:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
    return detect_dangerous_command


module = _real()
SOURCE = "hermes" if module is not None else "fixture"
if module is not None:
    _detector = module.detect_dangerous_command
else:
    module = _load("hermes_fixture_approval_detection", FIXTURE / "approval_detection.py")
    _detector = _vendored(module, _load("hermes_fixture_ansi_strip", FIXTURE / "ansi_strip.py"))


def detect(text: str) -> str | None:
    hit, _key, description = _detector(text or "")
    return description if hit else None
