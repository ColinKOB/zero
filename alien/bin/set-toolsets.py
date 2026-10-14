"""Sets every bot's tool lists, skills and fallback from one place, so the Hermes configs never drift apart.

Run as the home's user with its venv: ~/taffy/.venv/bin/python set-toolsets.py   (backs up each config first)
Then restart taffy-gateway when the bots are idle. The Zips are whatever the home has: the lead in HERMES_HOME
(paths.hermes_home in the home's settings, ~/.taffy by default) and every profile under it, so the same script serves
Colin's home and any other.

- chat (api_server): Colin's conversations and teammates' questions.
- board (cli): kanban workers. No clarify: a board task can't stop to ask.
- cron: scheduled jobs. No kanban (a schedule never hands out work by itself) and no cronjob.
Nobody gets image_gen or tts: Colin's bots don't make images or speech, and both would bill the OpenAI key.
Only Taffy can create scheduled jobs (cronjob); the hub reports every new job in Colin's chat.

Every bot falls back to GPT-6.1-Sol on Colin's ChatGPT plan when the Claude plan hits its limit. Hermes
has its own ChatGPT login for this (`taffy auth add openai-codex`), separate from the call voice's.

Each bot sees only the skills its job needs (skills.disabled lists the rest). Nobody gets the raw mail
skills (colin-email holds Colin's account and draft rules), the X posting skill, the macOS-only Apple
skills, or the skills for changing Hermes itself. Memory is what makes a Zip feel one step ahead, so it gets far
more room than Hermes's default (Fable's usage review, Oct 11: about 1.5k cached tokens a turn for memory that
lasts): 10,000 characters for the lead, whose job is remembering the person, and 5,000 for every other Zip. Every
bot's USER.md (what the team knows about the person) gets 5,000 too, so the onboarding About entry fits beside the
notes (the hub's onboarding.USER_CHAR_LIMIT).

Every Zip compacts its chats the same way (COMPRESSION): at 200k tokens or after three idle hours. The memory review
runs on each Zip's own model, which reads its warm cache, instead of Haiku, which has to read the whole chat cold.

The hub tells Colin and Taffy about board changes, so Hermes's own board notices are off: they ran a second
turn in Taffy's chat outside the hub's queue. Hermes's automatic reviewer is off too, because tasks in
review are waiting for Colin's approval and a bot must never approve them.
"""

import importlib.util
import re
import shutil
import time
from pathlib import Path

from ruamel.yaml import YAML


def _hermes_home() -> Path:
    """HERMES_HOME from the home's settings (the plugin's home.py, deployed beside this script's folder)."""
    reader = Path(__file__).resolve().parent.parent / "plugins" / "taffy-tools" / "home.py"
    try:
        spec = importlib.util.spec_from_file_location("taffy_plugin_home_cli", reader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.path("hermes_home")
    except Exception:
        return Path.home() / ".taffy"


HOME = _hermes_home()
# No "connections": Hermes's own connector tool installs MCP servers and starts OAuth with no card in Zero's app
# and nothing in Settings (Oct 8 QA, M12). Zero gets its own Connect flow first; the gate also blocks the tool.
CORE = ["browser", "code_execution", "computer_use", "delegation", "file", "memory",
        "session_search", "skills", "taffy", "terminal", "todo", "vision", "web"]
SCREEN = {"default", "builder", "career", "clients", "money", "school"}


def screen(bot: str) -> bool:
    """Every Zip, in every home, keeps the screen tool."""
    return True
FALLBACK = [{"provider": "openai-codex", "model": "gpt-6.1-sol"}]
FALLBACK_FOR_SOL = [{"provider": "claude-subscription-directsdk-experimental", "model": "opus"}]
LEAD_MEMORY_CHARS = 10000
ZIP_MEMORY_CHARS = 5000
USER_CHARS = 5000
KANBAN = {"review_dispatch": False, "auto_subscribe_on_create": False, "notify_in_gateway": False}
COMPRESSION = {"threshold_tokens": 200000, "protect_last_n": 30, "idle_compact_after_seconds": 10800}

EVERYONE = {"colin-email", "pdf", "docx", "xlsx", "powerpoint", "google-workspace", "document-to-action-items",
            "meeting-action-items", "maps", "blocked-page-recovery", "grounded-citations", "computer-use",
            "weekly-review-planning", "youtube-content"}
DESIGN = {"claude-design", "design-md", "popular-web-designs", "architecture-diagram", "humanizer"}
SKILLS = {
    "default": EVERYONE | {"icloud-caldav", "product-price-monitor"},
    "school": EVERYONE | {"arxiv"},
    "builder": EVERYONE | DESIGN | {"codebase-inspection", "dogfood", "github", "requesting-code-review",
                                    "simplify-code", "spike", "systematic-debugging", "test-driven-development",
                                    "node-inspect-debugger", "python-debugpy", "sdlc-review", "claude-code",
                                    "codex", "opencode", "competitor-news-monitor"},
    "clients": EVERYONE | DESIGN | {"github"},
    "money": EVERYONE | {"product-price-monitor"},
    "career": EVERYONE | {"humanizer"},
}


def skill_names(home: Path) -> set[str]:
    names = set()
    for f in (home / "skills").rglob("SKILL.md"):
        m = re.search(r"^name:\s*[\"']?([\w.-]+)", f.read_text(errors="replace"), re.M)
        names.add(m.group(1) if m else f.parent.name)
    return names


def lists(lead: bool, bot: str = "default") -> dict:
    core = [t for t in CORE if t != "computer_use" or screen(bot)]
    chat = sorted(core + ["kanban"] + (["clarify", "cronjob"] if lead else []))
    return {"api_server": chat, "cli": sorted(core + ["kanban"]), "cron": sorted(core)}


def main() -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    stamp = time.strftime("%Y%m%d-%H%M%S")
    paths = [(HOME / "config.yaml", True)] + [(p, False) for p in sorted((HOME / "profiles").glob("*/config.yaml"))]
    for path, lead in paths:
        if not lead and path.parent.name.startswith("."):
            continue
        data = yaml.load(path.read_text())
        before = {k: list(v) for k, v in (data.get("platform_toolsets") or {}).items()}
        bot = "default" if lead else path.parent.name
        data["platform_toolsets"] = lists(lead, bot)
        on_sol = (data.get("model") or {}).get("provider") == "openai-codex"
        data["fallback_providers"] = FALLBACK_FOR_SOL if on_sol else FALLBACK
        data.setdefault("kanban", {}).update(KANBAN)
        data.setdefault("compression", {}).update(COMPRESSION)
        aux = data.get("auxiliary")
        if isinstance(aux, dict):
            aux.pop("background_review", None)
        if bot in SKILLS:
            skills = data.setdefault("skills", {})
            keep = SKILLS[bot] if screen(bot) else SKILLS[bot] - {"computer-use"}
            skills["disabled"] = sorted(skill_names(path.parent) - keep)
        data.setdefault("memory", {})["memory_char_limit"] = LEAD_MEMORY_CHARS if lead else ZIP_MEMORY_CHARS
        data.setdefault("memory", {})["user_char_limit"] = USER_CHARS
        agent = data.get("agent") or {}
        if agent.get("reasoning_effort") == "":
            del agent["reasoning_effort"]
        shutil.copy2(path, path.with_name(f"config.yaml.bak-{stamp}"))
        with path.open("w") as f:
            yaml.dump(data, f)
        gone = {k: sorted(set(before.get(k, [])) - set(v)) for k, v in data["platform_toolsets"].items()}
        off = len((data.get("skills") or {}).get("disabled") or [])
        print(bot, {k: v for k, v in gone.items() if v}, f"skills off: {off}")
        for d in [path.parent / "bot-desktop", *path.parent.glob("bot-desktop/browser-profile*")]:
            if d.is_dir():
                d.chmod(0o700)


if __name__ == "__main__":
    main()
