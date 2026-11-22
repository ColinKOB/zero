""













import contextlib
import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path

from . import config, consent

log = logging.getLogger("taffy.plugintools")

PACKAGE = "taffy_hub_plugin"
_REPO = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools"


def _package():
    ""

    if "taffy_tools" in sys.modules:
        return "taffy_tools"
    if PACKAGE not in sys.modules:
        folder = config.PLUGIN_DIR if (config.PLUGIN_DIR / "gate.py").exists() else _REPO
        pkg = types.ModuleType(PACKAGE)
        pkg.__path__ = [str(folder)]
        pkg.consent = consent._mod
        sys.modules[PACKAGE] = pkg
    sys.modules.setdefault(f"{PACKAGE}.consent", consent._mod)
    return PACKAGE


def module(name: str):
    ""
    try:
        return importlib.import_module(f"{_package()}.{name}")
    except Exception:
        log.exception("could not load the plugin's %s", name)
        return None


def can_act_as_zip() -> bool:
    gate = module("gate")
    return gate is not None and hasattr(gate, "AS_ZIP")


@contextlib.contextmanager
def as_zip(bot_id: str):
    ""
    gate = module("gate")
    token = gate.AS_ZIP.set(bot_id)
    try:
        yield
    finally:
        gate.AS_ZIP.reset(token)

