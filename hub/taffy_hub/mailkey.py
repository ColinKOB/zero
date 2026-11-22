""


import importlib.util
import sys
from pathlib import Path

from . import config

_PATH = config.PLUGIN_DIR / "mailkey.py"

_REPO_PATH = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "mailkey.py"
if not _PATH.exists() and _REPO_PATH.exists():
    _PATH = _REPO_PATH
_spec = importlib.util.spec_from_file_location("taffy_mailkey", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["taffy_mailkey"] = _mod
_spec.loader.exec_module(_mod)

mail_call_key = _mod.mail_call_key
mail_fingerprint = _mod.mail_fingerprint
