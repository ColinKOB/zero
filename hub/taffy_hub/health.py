""



import asyncio
import json
import logging
import time

import httpx

from . import config, home, store

log = logging.getLogger("taffy.health")

CHECK_EVERY_SEC = 5 * 60
VOICE_CHECK_EVERY_SEC = 6 * 3600
status: dict[str, bool] = {"bots": True, "voice": True, "sites": True, "claude": True, "backup": True,
                           "mac_copy": True}
PSL_FILE = "/usr/share/publicsuffix/public_suffix_list.dat"
checked_at: dict[str, float] = {}

MESSAGES = {
    ("bots", False): "Your Zips stopped answering. Chats and board work are paused until they are back.",
    ("bots", True): "Your Zips are answering again.",
    ("voice", False): "Calls won't connect until ChatGPT is signed in again. Ask Claude to fix it.",
    ("voice", True): "Calls work again.",
    ("sites", False): "Sign-in permissions may treat unrelated websites as one until a missing list is put back. "
                      "Ask Claude to fix it.",
    ("sites", True): "Sign-in permissions are back to normal.",
    ("claude", False): "Your Zips have used up the Claude plan's limit for now. Until it resets, they will answer "
                       "using your ChatGPT plan.",
    ("claude", True): "Your Zips are back on the Claude plan; its limit has reset.",

    ("claude_refused", False): "Check your Claude plan at claude.ai. It stopped working, so your Zips are using "
                               "ChatGPT for now.",
    ("claude_refused", True): "Your Claude plan works again.",
    ("backup", False): "Zero's nightly backup didn't finish, so the newest history isn't saved anywhere else yet. "
                       "Ask Claude to look at it.",
    ("backup", True): "Zero's nightly backup is working again.",
    ("mac_copy", False): "The copy of Zero's backup on your Mac is more than three days old. It catches up the "
                         "next time your Mac is awake in the morning.",
    ("mac_copy", True): "The copy of Zero's backup on your Mac is up to date again.",
}


SHARED_MESSAGES = {
    ("voice", False): "Calls won't connect until the shared ChatGPT plan is signed in again. The person who shares "
                      "it needs to sign in again.",
    ("claude", False): "Your Zips have used up the shared Claude plan's limit for now. Until it resets, they will "
                       "answer using the shared ChatGPT plan.",
    ("claude", True): "Your Zips are back on the shared Claude plan; its limit has reset.",
    ("claude_refused", False): "The shared Claude plan stopped working, so your Zips are using the shared ChatGPT "
                               "plan for now. The person who shares it needs to check it.",
    ("claude_refused", True): "The shared Claude plan works again.",
}

claude_refused = False


def message(name: str, ok: bool) -> str:
    if name == "claude" and claude_refused:
        name = "claude_refused"
    if home.plan_kind() == "shared" and (name, ok) in SHARED_MESSAGES:
        return SHARED_MESSAGES[(name, ok)]
    return MESSAGES[(name, ok)]


def _host() -> str:
    return str(home.get("host.name") or "the server")



FIXES = {"bots": "check taffy-gateway.service", "voice": "run `codex login` on " + _host(),
         "claude": "usage limit on the Claude subscription, or the plan refused (see claudeaccounts.json \"refused\"); "
                   "turns fall back to openai-codex gpt-6.1-sol (fallback_providers, set by alien/bin/set-toolsets.py)",
         "sites": "apt install publicsuffix on " + _host(),
         "backup": "see ~/taffy/hub-state/backup.json and journalctl --user -u taffy-backup (alien/bin/taffy-backup)",
         "mac_copy": "Mac launchd com.colinobrien.taffy-backup-pull; log ~/Library/Logs/taffy-backup-pull.log"}


async def _bots_ok() -> bool:
    for _ in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{config.API_BASE}/health")
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(10)
    return False


async def _voice_ok() -> bool:
    from .voice import CODEX
    try:
        proc = await asyncio.create_subprocess_exec(CODEX, "login", "status", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, env={"HOME": str(config.HOME),
                                                    "PATH": f"{config.HOME}/.local/bin:/usr/bin:/bin"})
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
    except Exception:
        return True
    return "logged in" in out.decode(errors="replace").lower()


BACKUP_FILE = config.HUB_DIR / "backup.json"
BACKUP_MAX_AGE_SEC = 36 * 3600


def _backup_ok() -> bool:
    ""
    try:
        data = json.loads(BACKUP_FILE.read_text())
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return bool(data.get("ok")) and time.time() - float(data.get("at", 0)) < BACKUP_MAX_AGE_SEC


MAC_COPY_FILE = config.HUB_DIR / "backup-mac.json"
MAC_COPY_MAX_AGE_SEC = 72 * 3600


def _mac_copy_ok() -> bool:
    ""
    try:
        return time.time() - float(json.loads(MAC_COPY_FILE.read_text()).get("at", 0)) < MAC_COPY_MAX_AGE_SEC
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False


NOT_URGENT = {"backup", "mac_copy"}
NIGHT_DOWN_GRACE_SEC = 10 * 60
down_since: dict[str, float] = {}


async def _set(name: str, ok: bool) -> None:
    from . import watcher
    checked_at[name] = time.time()
    saved = store.get_setting("health", {}) or {}
    if not ok:
        down_since.setdefault(name, time.time())
    else:
        down_since.pop(name, None)
    if saved.get(name, True) == ok:
        status[name] = ok
        return
    if watcher._night():


        if not ok and time.time() - down_since[name] < NIGHT_DOWN_GRACE_SEC:
            status[name] = ok
            return
        if ok:
            status[name] = ok
            if await watcher.push("Zero", message(name, ok)):
                saved[name] = ok
                store.set_setting("health", saved)
            return
    status[name] = ok
    log.warning("health: %s is %s%s", name, "back" if ok else "down", "" if ok else f" ({FIXES[name]})")


    if await watcher.push("Zero", message(name, ok), urgent=name not in NOT_URGENT):
        saved[name] = ok
        store.set_setting("health", saved)


LIMIT_WORDS = ("usage limit", "rate limit", "rate_limit", "429", "quota", "limit reached", "out of extra usage",
               "overloaded", "exceeded your")


def turn_finished(error: str | None, backup: bool = False, claude: bool | None = None) -> None:
    ""



    from .claudeaccounts import REFUSED
    if not error and not backup and claude is False:
        return
    limited = (bool(error) and (any(w in error.lower() for w in LIMIT_WORDS) or bool(REFUSED.search(error)))) \
        or (not error and backup)
    if limited != status["claude"]:
        return
    try:
        asyncio.get_running_loop().create_task(_claude_down() if limited else _set("claude", True))
    except RuntimeError:
        pass


_down_check: asyncio.Task | None = None


async def _claude_down() -> None:
    ""

    global _down_check, claude_refused
    if _down_check is not None and not _down_check.done() and _down_check is not asyncio.current_task():
        return
    _down_check = asyncio.current_task()
    from . import claudeaccounts
    try:
        moved, refused = await asyncio.to_thread(claudeaccounts.off_refused)
    except Exception:
        log.warning("health: checking the Claude plan failed", exc_info=True)
        moved, refused = None, False
    if moved:
        await claudeaccounts._tell_moved(moved, refused=True)
        return
    claude_refused = refused
    await _set("claude", False)


async def claude_works_again() -> None:
    ""
    global claude_refused
    claude_refused = True
    try:
        await _set("claude", True)
    finally:
        claude_refused = False


async def plan_read(reading: dict | None) -> None:
    ""



    if status["claude"] or not reading or claude_refused:
        return
    used = [v for v in (reading.get("five_hour"), reading.get("weekly")) if isinstance(v, (int, float))]
    if used and max(used) < 100:
        await _set("claude", True)


_voice_check: asyncio.Task | None = None


def voice_failed() -> None:
    ""
    global _voice_check
    if _voice_check is None or _voice_check.done():
        async def check():
            await _set("voice", await _voice_ok())
        _voice_check = asyncio.create_task(check())


async def run() -> None:
    await asyncio.sleep(60)
    last_voice = 0.0
    while True:
        try:
            await _set("bots", await _bots_ok())
            import os
            await _set("sites", os.path.exists(PSL_FILE))
            await _set("backup", _backup_ok())
            if home.get("backup.mac_copy_expected"):
                await _set("mac_copy", _mac_copy_ok())


            from . import mailcard
            await asyncio.to_thread(mailcard._look_for_unsure)
            if time.time() - last_voice > VOICE_CHECK_EVERY_SEC:
                last_voice = time.time()
                await _set("voice", await _voice_ok())
        except Exception:
            log.warning("health check failed", exc_info=True)
        await asyncio.sleep(CHECK_EVERY_SEC)
