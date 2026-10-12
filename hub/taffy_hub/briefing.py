"""What the call voice knows about Colin before it says hello.

ChatGPT's realtime voice does the talking on calls, so it needs a briefing: the bot's personality,
what the bot knows about Colin, the recent chat, open work, and the date. Haiku (Colin's Claude
subscription, through the claude CLI) condenses those into one briefing per bot, cached in
hub-state/briefings/. A call uses the cached briefing at once, plus the chat lines newer than it word for
word, and refreshes it in the background when it is over 30 minutes old. Finished chat turns refresh
it at most every 10 minutes; a turn inside that window schedules a catch-up for when it ends.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from . import board, config, hermes, history, home, who

log = logging.getLogger("taffy.briefing")

MODEL = "claude-haiku-5-5"
FRESH_FOR_CALL_SEC = 30 * 60
REFRESH_EVERY_SEC = 10 * 60
DIR = config.HUB_DIR / "briefings"
_building: dict[str, asyncio.Task] = {}
_catch_up: dict[str, asyncio.Task] = {}


def _visible(msgs: list[dict]) -> list[dict]:
    """The chat as Colin saw it (history.py), including what calls found."""
    return [{**m, "text": m["display"]} for m in history.visible(msgs)]

PROMPT = """You prepare a briefing for the voice of {name}, {person}'s assistant bot, before a phone-style \
call with {person}. The voice has no tools and no other memory, so the briefing is everything it knows. Write it \
in second person ("You are {name}..."), in plain sentences and short lists, under 900 words. Include:

1. Who you are and how you talk (from the personality notes), in a few sentences.
2. What you know about {person} that matters in conversation: life, people, preferences, routines, \
current priorities.
3. What is going on right now: today's date and time, open and waiting work, deadlines, and what the \
two of you talked about recently, with concrete names, dates and numbers.
4. Anything you promised {person} or are waiting on.

Only use facts from the material below. Do not add advice or instructions about tools.

Current date and time: {now}

--- Personality notes ---
{soul}

--- What you remember about {person} ---
{memory}

--- Open work on the team board ---
{work}

--- Recent chat (oldest first) ---
{chat}
"""


def _home(bot_id: str) -> Path:
    return config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id


def _read(path: Path, limit: int) -> str:
    try:
        return path.read_text()[:limit]
    except OSError:
        return ""


def _now() -> str:
    from . import home
    return datetime.now(home.TZ).strftime("%A, %B %-d, %Y, %-I:%M %p ") + home.tz_word()


async def _material(bot_id: str) -> dict:
    home = _home(bot_id)
    from . import facts
    shared = facts.briefing_text(bot_id)
    memory = "\n\n".join(t for t in (_read(home / "memories" / "USER.md", 8000),
                                      ("What the team knows (shared by every bot; newer than your own notes):\n"
                                       + shared) if shared else "",
                                      _read(home / "memories" / "MEMORY.md", 8000)) if t)
    try:
        groups = await asyncio.to_thread(board.grouped)
        lines = []
        for status, tasks in groups.items():
            for t in tasks[:12]:
                if status in ("done", "archived"):
                    continue
                assignee = config.BOTS_BY_ID.get(t.get("assignee") or "default")
                lines.append(f"- [{status}] {t.get('title')} ({assignee.name if assignee else 'team'})")
        work = "\n".join(lines) or "Nothing open."
    except Exception:
        work = "Unknown."
    try:
        msgs = _visible(await hermes.messages(bot_id, limit=60))[-30:]
        chat = "\n".join(f"{who.first() if m['role'] == 'user' else 'You'}: {m['text'][:600]}" for m in msgs)
    except Exception:
        chat = ""
    return {"soul": _read(home / "SOUL.md", 6000), "memory": memory or "Nothing saved yet.",
            "work": work, "chat": chat or "No recent chat.", "now": _now()}


async def ask_haiku(prompt: str, timeout: float = 90, effort: str | None = None) -> str:
    """One answer from Haiku on the home's Claude plan (claude CLI, no tools). `effort` (low, medium, high) is passed
    only on the home's own plan: the shared plan's broker takes the one-shot form without it (Haiku 5.5's default
    is medium)."""
    from . import usage
    origin, started = usage.caller(), time.monotonic()
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "HERMES_HOME")}
    env["HOME"] = str(config.HOME)
    given = str(home.get("plan.claude.command") or "claude")
    exe = str(home.expand(given)) if "/" in given else "claude"   # the broker's client in a shared home
    extra = ["--effort", effort] if effort in ("low", "medium", "high") and home.get("plan.claude.mode") != "broker" \
        else []
    proc = await asyncio.create_subprocess_exec(
        exe, "-p", "--model", MODEL, "--tools", "", "--output-format", "json", *extra,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=env, cwd="/tmp")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    finally:
        if proc.returncode is None:   # timed out or cancelled: never leave a stray claude process
            proc.kill()
            await proc.wait()
    lines = out.decode().strip().splitlines()
    result = json.loads(lines[-1]) if lines else None
    usage.log_cli(origin, result, MODEL, started)
    if not lines:
        raise RuntimeError("no answer from Haiku")
    text = (result.get("result") or "").strip()
    if not text:
        raise RuntimeError("empty answer from Haiku")
    return text


async def build(bot_id: str) -> str:
    bot = config.BOTS_BY_ID[bot_id]
    from . import onboarding
    text = await ask_haiku(PROMPT.format(name=bot.name, person=onboarding.person_name(), **(await _material(bot_id))))
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{bot_id}.md").write_text(text)
    log.info("briefing for %s rebuilt (%d chars)", bot_id, len(text))
    return text


def _cached(bot_id: str) -> tuple[str, float] | None:
    path = DIR / f"{bot_id}.md"
    try:
        return path.read_text(), time.time() - path.stat().st_mtime
    except OSError:
        return None


async def _build_once(bot_id: str) -> str:
    task = _building.get(bot_id)
    if task is None or task.done():
        task = asyncio.create_task(build(bot_id))
        _building[bot_id] = task
    return await task


async def for_call(bot_id: str) -> str:
    """The briefing for a call starting now. An older briefing is used at once and refreshed in the
    background; the call only waits when there is no briefing at all."""
    cached = _cached(bot_id)
    if cached:
        if cached[1] >= FRESH_FOR_CALL_SEC:
            refresh_soon(bot_id)
        return cached[0] + await _since(bot_id, time.time() - cached[1]) + f"\n\nRight now it is {_now()}."
    try:
        return await asyncio.wait_for(_build_once(bot_id), 25)
    except Exception:
        log.warning("briefing rebuild for %s failed", bot_id, exc_info=True)
    if cached:
        return cached[0] + f"\n\nRight now it is {_now()}."
    from . import onboarding
    return f"You are {config.BOTS_BY_ID[bot_id].name}, {onboarding.person_name()}'s assistant. It is {_now()}."


async def _since(bot_id: str, written: float) -> str:
    """The chat lines newer than the briefing, word for word, so a call knows what was just said."""
    try:
        msgs = _visible(await asyncio.wait_for(hermes.messages(bot_id, limit=40), 5))
    except Exception:
        return ""
    new = [m for m in msgs if isinstance(m.get("ts"), (int, float)) and m["ts"] > written][-12:]
    out = ""
    if new:
        lines = "\n".join(f"{who.first() if m['role'] == 'user' else 'You'}: {' '.join(m['text'].split())[:400]}"
                          for m in new)
        out += "\n\n--- Said in chat since this briefing was written ---\n" + lines
    # Calls in the last day, so calling back picks up where the last call left off.
    from . import store
    rows = store.db().execute("SELECT body, created_at FROM feed WHERE kind='call' AND bot_id=? AND created_at>? "
                              "ORDER BY id DESC LIMIT 3", (bot_id, int(time.time()) - 86400)).fetchall()
    calls = []
    for r in reversed(rows):
        try:
            summary = json.loads(r["body"] or "{}").get("summary") or ""
        except ValueError:
            summary = ""
        if summary:
            when = datetime.fromtimestamp(r["created_at"], home.TZ).strftime("%-I:%M %p")
            calls.append(f"Call that ended at {when}:\n{summary}")
    if calls:
        out += f"\n\n--- Your recent calls with {who.first()} (what came of them) ---\n" + "\n\n".join(calls)
    return out


def refresh_soon(bot_id: str) -> None:
    """After a chat turn: rebuild in the background if the briefing is over 10 minutes old, and otherwise
    once it reaches that age, so the last turns of a conversation are never left out."""
    cached = _cached(bot_id)
    if cached and cached[1] < REFRESH_EVERY_SEC:
        if bot_id not in _catch_up or _catch_up[bot_id].done():
            async def later(wait: float):
                await asyncio.sleep(wait)
                refresh_soon(bot_id)
            _catch_up[bot_id] = asyncio.create_task(later(REFRESH_EVERY_SEC - cached[1] + 5))
        return
    task = _building.get(bot_id)
    if task is None or task.done():
        _building[bot_id] = asyncio.create_task(build(bot_id))
