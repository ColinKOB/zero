"""Loads the taffy-tools plugin from this repo as a package, so its gate can be tested off alien.

Hermes and the hub are not importable here; every plugin module guards those imports already. The plugin's
consent.py is the same module the hub loads (sys.modules["taffy_consent"]) when the hub was imported first,
so tests patch one DB_PATH.
"""

import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools"
if not PLUGIN_DIR.exists():
    PLUGIN_DIR = Path.home() / "taffy" / "plugins" / "taffy-tools"


def load():
    if "taffy_tools" in sys.modules:
        return sys.modules["taffy_tools"]
    shared = sys.modules.get("taffy_consent")
    spec = importlib.util.spec_from_file_location("taffy_tools", PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["taffy_tools"] = pkg
    if shared is not None:
        sys.modules["taffy_tools.consent"] = shared
        pkg.consent = shared
    spec.loader.exec_module(pkg)
    return pkg
