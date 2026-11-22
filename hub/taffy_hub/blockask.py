""


import importlib.util
import sys
from pathlib import Path

from . import config

_PATH = config.PLUGIN_DIR / "blockask.py"

_REPO_PATH = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "blockask.py"
if not _PATH.exists() and _REPO_PATH.exists():
    _PATH = _REPO_PATH
_spec = importlib.util.spec_from_file_location("taffy_blockask", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["taffy_blockask"] = _mod
_spec.loader.exec_module(_mod)
if not hasattr(_mod, "standing_question") and _REPO_PATH.exists() and _PATH != _REPO_PATH:

    _spec = importlib.util.spec_from_file_location("taffy_blockask", _REPO_PATH)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["taffy_blockask"] = _mod
    _spec.loader.exec_module(_mod)

QUIET_KINDS = _mod.QUIET_KINDS
parse = _mod.parse
quiet = _mod.quiet
asks_nothing = _mod.asks_nothing
signs_in = _mod.signs_in
maybe_sign_in = _mod.maybe_sign_in
SIGN_IN_FORM = _mod.SIGN_IN_FORM
CHECK_WAIT = getattr(_mod, "CHECK_WAIT", "Finish a human check on this page.")
GENERIC_WAKES = _mod.GENERIC_WAKES
generic_wake = _mod.generic_wake
settled_times = _mod.settled_times
standing_question = _mod.standing_question
