"""The sign-in consent store, shared with the taffy-tools plugin (one copy of the rules).

The plugin's consent.py is loaded by path so the hub and the gate can never disagree.
"""

import importlib.util
import sys
from pathlib import Path

from . import config

_PATH = config.PLUGIN_DIR / "consent.py"
# Off alien (tests on a Mac), read the same file from this repo's alien/ folder.
_REPO_PATH = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "consent.py"
if not _PATH.exists() and _REPO_PATH.exists():
    _PATH = _REPO_PATH
_spec = importlib.util.spec_from_file_location("taffy_consent", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["taffy_consent"] = _mod
_spec.loader.exec_module(_mod)

site_of = _mod.site_of
base = _mod.base
is_sensitive = _mod.is_sensitive
open_pass = _mod.open_pass
mode_for = _mod.mode_for
set_mode = _mod.set_mode
MODES = _mod.MODES
pending = _mod.pending
get_request = _mod.get_request
decide = _mod.decide
allow_now = _mod.allow_now
recent_signins = _mod.recent_signins
signins_after = _mod.signins_after
requests_after = _mod.requests_after
db = _mod.db
mark_signed_in = _mod.mark_signed_in
close_message_passes = _mod.close_message_passes


def __getattr__(name: str):
    """Everything else the plugin's consent.py defines (rules, actions, passes), without listing each one."""
    return getattr(_mod, name)


# What Colin reads about cards in a Zip's chat and on the card itself (Grok Bot 0.68.1's wording).
ALLOW_RULE_ADDED = "Added to your Allow rules"
NO_ANSWER_TITLE = "No answer in time"


def allowed_line(sentence: str) -> str:
    """After Always Allow: Allowed from now on: "Money can email andrea@x.com without asking." """
    return f"Allowed from now on: “{sentence}”"


def expired_line(r: dict) -> str:
    """A card that ran out of time without an answer."""
    bot = config.BOTS_BY_ID.get(r.get("bot_id") or "")
    return f"No answer in time, so {bot.name if bot else 'the Zip'} didn't do this."
