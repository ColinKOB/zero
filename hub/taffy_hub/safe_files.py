"""Reading files a bot named, with the gate's rules (Zip sandbox fixes, Oct 8).

The taffy-tools plugin's safe_files.py is loaded by path, as consent.py is, so the hub and the gate share one copy of
the rules and of SECRET_PATHS. It uses only the standard library.
"""

import importlib.util
import sys
from pathlib import Path

from . import config

_PATH = config.PLUGIN_DIR / "safe_files.py"
# Off alien (tests on a Mac), read the same file from this repo's alien/ folder.
_REPO_PATH = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "safe_files.py"
if not _PATH.exists() and _REPO_PATH.exists():
    _PATH = _REPO_PATH
_spec = importlib.util.spec_from_file_location("taffy_safe_files", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["taffy_safe_files"] = _mod
_spec.loader.exec_module(_mod)

Refused = _mod.Refused
SECRET_PATHS = _mod.SECRET_PATHS
resolve = _mod.resolve
open_file = _mod.open_file
