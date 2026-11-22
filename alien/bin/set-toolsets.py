""


















































import importlib.util
import re
import shutil
import time
from pathlib import Path

from ruamel.yaml import YAML


PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "taffy-tools"


def _plugin(name: str):
    ""
    spec = importlib.util.spec_from_file_location(f"taffy_plugin_{name}_cli", PLUGIN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hermes_home() -> Path:
    ""
    try:
        return _plugin("home").path("hermes_home")
    except Exception:
        return Path.home() / ".taffy"


def _timezone() -> str:
    ""
    try:
        return _plugin("home").timezone()
    except Exception:
        return "America/New_York"


HOME = _hermes_home()
TIMEZONE = _timezone()
MODELS = _plugin("models")


CORE = ["browser", "code_execution", "computer_use", "delegation", "file", "memory",
        "session_search", "skills", "taffy", "terminal", "todo", "vision", "web"]



SCREEN = {"default", "builder", "career", "clients", "money", "school"}


def screen(bot: str) -> bool:
    ""
    return True


def fallback(data: dict) -> list[dict]:
    ""


    try:
        choice = MODELS.setup(_plugin("home").get("models"))
    except Exception:
        choice = None
    provider = (data.get("model") or {}).get("provider") or MODELS.PROVIDERS["claude"]
    return MODELS.backup(choice, provider)





BROWSER_BACKEND = "off"
LEAD_MEMORY_CHARS = 10000
ZIP_MEMORY_CHARS = 8000
USER_CHARS = 5000







SOUL_CHARS = 40000





HOOK_CONTEXT_CHARS = 40000
KANBAN = {"review_dispatch": False, "auto_subscribe_on_create": False, "notify_in_gateway": False}












COMPRESSION = {"protect_last_n": 30, "idle_compact_after_seconds": 10800}



EVERYONE = {"colin-email", "colin-copy", "pdf", "docx", "xlsx", "powerpoint", "google-workspace", "document-to-action-items",
            "meeting-action-items", "maps", "blocked-page-recovery", "grounded-citations", "computer-use",
            "weekly-review-planning", "youtube-content", "make-video"}
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







OURS = {"colin-copy", "make-video", "colin-email", "icloud-caldav", "email-mailboxes"}


NEVER = {"himalaya", "email-mailboxes", "agentmail", "xurl", "apple-notes", "apple-reminders", "findmy", "imessage",
         "hermes-agent", "hermes-agent-skill-authoring"}


def bundled(home: Path) -> set[str]:
    ""
    try:
        lines = (home / "skills" / ".bundled_manifest").read_text().splitlines()
    except OSError:
        return set()
    return {line.split(":", 1)[0].strip() for line in lines if line.strip()}


def disabled(home: Path, keep: set[str]) -> list[str]:
    ""
    shipped = bundled(home) | OURS
    return sorted({n for n in skill_names(home) if n in shipped and n not in keep} | NEVER)


def skill_names(home: Path) -> set[str]:
    names = set()
    for f in (home / "skills").rglob("SKILL.md"):
        m = re.search(r"^name:\s*[\"']?([\w.-]+)", f.read_text(errors="replace"), re.M)
        names.add(m.group(1) if m else f.parent.name)
    return names


def lists(lead: bool, bot: str = "default") -> dict:
    core = [t for t in CORE if t != "computer_use" or screen(bot)]
    chat = sorted(core + ["kanban", "cronjob"] + (["clarify"] if lead else []))
    return {"api_server": chat, "cli": sorted(core + ["kanban", "cronjob"]), "cron": sorted(core + ["kanban"])}


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
        data["timezone"] = TIMEZONE
        data["platform_toolsets"] = lists(lead, bot)
        data["fallback_providers"] = fallback(data)
        data.setdefault("kanban", {}).update(KANBAN)
        hooks = data["hooks"] = data.get("hooks") or {}
        hooks.setdefault("output_spill", {})["max_chars"] = HOOK_CONTEXT_CHARS
        data.setdefault("compression", {}).update(COMPRESSION)
        data["compression"].setdefault("threshold_tokens", MODELS.COMPACT_AT)


        aux = data.get("auxiliary")
        if isinstance(aux, dict):
            aux.pop("background_review", None)
        if bot in SKILLS:
            skills = data.setdefault("skills", {})
            keep = SKILLS[bot] if screen(bot) else SKILLS[bot] - {"computer-use"}
            skills["disabled"] = disabled(path.parent, keep)
        terminal = data.setdefault("terminal", {})
        forwarded = terminal.get("docker_forward_env") or []
        terminal["docker_forward_env"] = list(dict.fromkeys(
            [name for name in forwarded if name not in ("PEXELS_API_KEY", "ZERO_PEXELS_API_KEY")]
            + ["PIXABAY_API_KEY", "ZERO_PIXABAY_API_KEY"]))
        data.setdefault("browser", {})["backend"] = BROWSER_BACKEND
        data.setdefault("memory", {})["memory_char_limit"] = LEAD_MEMORY_CHARS if lead else ZIP_MEMORY_CHARS
        data.setdefault("memory", {})["user_char_limit"] = USER_CHARS
        data["context_file_max_chars"] = SOUL_CHARS
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
