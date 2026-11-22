""







import json
import os

from . import home

VAR = "CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR"
current = "main"


def folder() -> str | None:
    ""
    hub = home.path("hub_state")
    try:
        active = json.loads((hub / "claude-accounts.json").read_text()).get("active") or "main"
    except (OSError, ValueError):
        return None
    if active == "main" or "/" in active or active.startswith("."):
        return None
    d = hub / "claude-accounts" / active
    return str(d) if (d / ".credentials.json").exists() else None


def pre_llm_call(**kw):
    global current
    d = folder()
    if d:
        os.environ[VAR] = d
        current = os.path.basename(d)
    else:
        os.environ.pop(VAR, None)
        current = "main"
    return None
