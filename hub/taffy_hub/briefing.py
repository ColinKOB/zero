""









import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from . import board, config, hermes, history, home, modelsetup, shortreply, who

log = logging.getLogger("taffy.briefing")

MODEL = modelsetup.M.HELPER["claude"].id
FRESH_FOR_CALL_SEC = 30 * 60
REFRESH_EVERY_SEC = 10 * 60
CALL_WAIT_SEC = 10
PLAIN_CHAT_SEC = 3 * 3600
DIR = config.HUB_DIR / "briefings"
_building: dict[str, asyncio.Task] = {}
_catch_up: dict[str, asyncio.Task] = {}
_revisions: dict[str, int] = {}


def invalidate(bot_id: str) -> None:
    ""
    from . import store
    with store._lock:
        _revisions[bot_id] = _revisions.get(bot_id, 0) + 1
        (DIR / f"{bot_id}.md").unlink(missing_ok=True)


def _revision(bot_id: str) -> tuple:
    from . import openloops, store
    row = store.db().execute("SELECT COUNT(*),COALESCE(SUM(updated_at),0) FROM open_loops WHERE bot_id=?",
                             (bot_id,)).fetchone()
    return (_revisions.get(bot_id, 0), *row)


def _visible(msgs: list[dict]) -> list[dict]:
    ""
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


                if status in ("done", "archived") or shortreply.withheld(*board.task_zips(t)):
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

    from . import recentchat
    summary = await asyncio.to_thread(recentchat.summary_part, bot_id)
    if summary:
        chat = summary + ("\n\nLatest messages:\n" + chat if chat else "")
    from . import loopevents, privatewords
    work += "\n\nAuthoritative outcomes, newer than older notes or chat:\n" + loopevents.context(bot_id)



    out = privatewords.leave_out
    return {"soul": out(_read(home / "SOUL.md", 6000)), "memory": out(memory) or "Nothing saved yet.",
            "work": out(work), "chat": out(chat) or "No recent chat.", "now": _now()}


async def ask_haiku(prompt: str, timeout: float = 90, effort: str | None = None, model: str | None = None) -> str:
    ""




    from . import shortreply, usage
    selected = None
    if model is not None:
        selected = modelsetup.M.BY_ID.get(model) or modelsetup.M.CATALOG.get(model)
        if selected is None:
            raise ValueError(f"Unknown helper model: {model}")
    origin = usage.caller()
    with usage.errand(origin):
        return await shortreply.ask_helper(prompt, model=selected, timeout=timeout, system=HELPER_SYSTEM,
                                           max_tokens=32000, effort=effort or "medium", origin=origin)


HELPER_SYSTEM = ("Complete the requested text task using only the supplied material. Follow its format and preserve "
                 "the facts it asks for. Do not add facts or take actions.")


class MaterialChanged(RuntimeError):
    ""



async def build(bot_id: str) -> str:
    bot = config.BOTS_BY_ID[bot_id]


    from . import openloops
    reading = openloops._tails.get(bot_id)
    if reading is not None and not reading.done():
        await asyncio.wait({reading})
    revision = _revision(bot_id)
    from . import onboarding
    from . import usage
    from . import ownerrules
    with usage.errand("hub-briefing", bot_id):


        with shortreply.reading((bot_id,), titles=tuple(z for z in sorted(config.PRIVATE_ZIPS) if not shortreply.withheld(z))):
            text = await ask_haiku(PROMPT.format(name=bot.name, person=onboarding.person_name(), **(await _material(bot_id)))
                                   + ownerrules.block(ownerrules.UPDATE, bot.name, bot_id, answered=False), effort="low")
    from . import store
    with store._lock:
        if revision != _revision(bot_id):
            raise MaterialChanged(f"Briefing material changed while it was being read ({bot_id})")
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


class Written(str):
    ""


class Plain(str):
    ""


async def written(bot_id: str, wait: float) -> str | None:
    ""

    task = _building.get(bot_id)
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=wait)
    cached = _cached(bot_id)
    return cached[0] + _loops(bot_id) + _right_now(bot_id) if cached else None


async def for_call(bot_id: str) -> str:
    ""

    cached = _cached(bot_id)
    if cached:
        if cached[1] >= FRESH_FOR_CALL_SEC:
            refresh_soon(bot_id)
        return Written(cached[0] + _loops(bot_id) + _right_now(bot_id)
                       + await _since(bot_id, time.time() - cached[1]) + f"\n\nRight now it is {_now()}.")



    build = asyncio.ensure_future(_build_once(bot_id))
    build.add_done_callback(_build_ended)
    done, _ = await asyncio.wait({build}, timeout=CALL_WAIT_SEC)
    if done:
        try:
            return Written(build.result() + _loops(bot_id) + _right_now(bot_id))
        except Exception:
            log.warning("briefing rebuild for %s failed", bot_id, exc_info=True)
    else:
        log.warning("briefing for %s was not written in %d s; the call starts on the plain one", bot_id, CALL_WAIT_SEC)
    return Plain(plain(bot_id) + await _since(bot_id, time.time() - PLAIN_CHAT_SEC))


def _build_ended(task: asyncio.Task) -> None:
    ""
    if not task.cancelled():
        task.exception()


def plain(bot_id: str) -> str:
    ""

    from . import onboarding
    soul = _read(_home(bot_id) / "SOUL.md", 6000)
    return (f"You are {config.BOTS_BY_ID[bot_id].name}, {onboarding.person_name()}'s assistant. It is {_now()}."
            + (f"\n\n--- Personality notes ---\n{soul}" if soul else "") + _loops(bot_id) + _right_now(bot_id))


def _loops(bot_id: str) -> str:
    ""
    try:
        from . import openloops
        text = openloops.briefing_text(bot_id)
    except Exception:
        log.warning("could not read the open loops for a briefing", exc_info=True)
        return ""
    return f"\n\n{text}" if text else ""


def _right_now(bot_id: str) -> str:
    ""



    try:
        from . import rightnow, standing
        text = "\n\n".join(t for t in (rightnow.text(bot_id), standing.for_call(bot_id)) if t)
    except Exception:
        log.warning("could not read the right-now or standing block for a briefing", exc_info=True)
        return ""
    return f"\n\n{text}" if text else ""


async def _since(bot_id: str, written: float) -> str:
    ""
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
    ""

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
        _building[bot_id].add_done_callback(_refresh_ended)


def _refresh_ended(task: asyncio.Task) -> None:
    ""
    if task.cancelled():
        return
    err = task.exception()
    if isinstance(err, MaterialChanged):
        log.info("%s; not saved", err)
    elif err:
        log.warning("briefing refresh failed", exc_info=err)
