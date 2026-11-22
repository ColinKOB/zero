""





















import asyncio
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import chatter, choices, config, consent, facts, hermes, home, looks, shortreply, store, turns, wallpapers, who, zipvoice

log = logging.getLogger("taffy.newzip")

MAKE_LANE = "make"
MAKE_TIMEOUT_SEC = 240
ONLINE_WAIT_SEC = 90
FIRST_TURN_TIMEOUT_SEC = 240
SETTING_UP = "Getting set up"
READING = "Reading what I know about you"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9 '&-]{0,23}")
RESERVED_IDS = {"default", "taffy", "zero", "hub", "chip", "admin", "root", "system", "all", "team"}


class NewZipError(ValueError):
    pass


@dataclass
class Request:
    id: str
    bot_id: str
    name: str
    look: dict
    color: str
    device: str | None
    state: str = "planning"
    plan: dict | None = None
    model: str = ""
    general: bool = False
    note: str = ""
    created_at: float = field(default_factory=time.time)


_requests: dict[str, Request] = {}
_tasks: set = set()
_make_lock: asyncio.Lock | None = None




def start(name: str, look: dict, color: str = "", device: str | None = None, model: str = "",
          general: bool = False) -> dict:
    ""



    config.reload_bots()
    name = " ".join(str(name or "").split())
    if not NAME.fullmatch(name):
        raise NewZipError("Use a name of up to 24 letters, numbers and spaces, starting with a letter.")
    model = str(model or "").strip()
    if model:
        from . import modelsetup
        m = modelsetup.M.CATALOG.get(model)
        if m is None:
            raise NewZipError("Choose a model from the list.")
        if m.keyed and not modelsetup.key_ready(m.plan):
            raise NewZipError(f"{m.name} needs its key on your home first.")
    retry = next((r for r in _requests.values() if r.name.lower() == name.lower() and r.state == "failed"), None)
    same = next((n for n in [b.name for b in config.BOTS] + [r.name for r in _requests.values() if r is not retry]
                 if n.lower() == name.lower()), None)
    if same:
        raise NewZipError(f"You already have a Zip called {same}. Pick another name.")
    room = _room()
    if room is not None and room <= 0:
        raise NewZipError("Your home has as many Zips as it runs. Delete one to make room.")
    try:
        look = looks.clean_rendered(look or {})
    except looks.LookError as e:
        raise NewZipError(str(e)) from None
    color = color.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", color or "") else "#64748B"
    if retry:
        _requests.pop(retry.bot_id, None)
    r = Request(id="nz_" + secrets.token_hex(4), bot_id=_free_id(name), name=name, look=look, color=color,
                device=device, model=model, general=bool(general))
    _requests[r.bot_id] = r
    try:
        task = asyncio.get_running_loop().create_task(_run(r))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except RuntimeError:
        pass
    return _listed(r)


def _free_id(name: str) -> str:
    ""
    base = re.sub(r"[^a-z0-9]", "", name.lower())[:20]
    if not base or not base[0].isalpha():
        base = ("zip" + base)[:20]
    if len(base) < 2:
        base = (base + "zip")[:20]
    profiles = config.HERMES_HOME / "profiles"
    taken = RESERVED_IDS | set(config.BOTS_BY_ID) | set(_requests) | (
        {p.name for p in profiles.iterdir()} if profiles.is_dir() else set())
    first = re.sub(r"[^a-z0-9]", "", who.first().lower())
    if first:
        taken.add(first)
    out, n = base, 2
    while out in taken:
        out = base[:18] + str(n)
        n += 1
    return out


def _room() -> int | None:
    ""
    try:
        limit = home.get("zips.max")
        limit = int(limit) if limit not in (None, "") else None
    except (TypeError, ValueError):
        limit = None
    if limit is None:
        return None
    profiles = config.HERMES_HOME / "profiles"
    have = ({p.name for p in profiles.iterdir() if p.is_dir()} if profiles.is_dir() else set()) | set(config.BOTS_BY_ID)
    have |= {r.bot_id for r in _requests.values() if r.state != "failed"}
    have.discard("default")
    return limit - len(have)




def _listed(r: Request) -> dict:
    failed = r.state == "failed"
    return {"id": r.bot_id, "name": r.name, "role": "", "color": r.color, "symbol": "sparkles", "shape": "pebble",
            "look": r.look, "custom": True, "working": 0, "needs_you": 0, "busy": not failed,
            "preview": r.note if failed else SETTING_UP, "last_at": r.created_at, "making": not failed,
            "status": {"state": "failed", "line": r.note, "at": r.created_at} if failed else None}


def pending_bots() -> list[dict]:
    ""
    return [_listed(r) for r in _requests.values() if r.bot_id not in config.BOTS_BY_ID]


def making(bot_id: str) -> bool:
    r = _requests.get(bot_id)
    return bool(r and r.state != "failed")


def busy_ids() -> list[str]:
    ""

    return [r.bot_id for r in _requests.values() if r.state != "failed"]


def live(now: float | None = None) -> dict[str, dict]:
    ""

    now = now or time.time()
    return {r.bot_id: {"since": r.created_at, "last_at": now,
                       "steps": [SETTING_UP] + ([READING] if r.state == "greeting" else []), "stalled": False,
                       "source": "hub", "waiting": False}
            for r in _requests.values() if r.state != "failed"}


def forget(bot_id: str) -> bool:
    ""
    r = _requests.get(bot_id)
    if not r:
        return False
    if r.state == "making":
        raise NewZipError(f"{r.name} is almost set up. Delete it in a minute if you still want to.")
    _requests.pop(bot_id, None)
    return True


def drop(bot_id: str) -> None:
    ""
    _requests.pop(bot_id, None)


def plan_for(request_id: str) -> dict:
    ""
    r = next((x for x in _requests.values() if x.id == request_id), None)
    if not r or r.state != "making" or not r.plan:
        raise NewZipError("There is no new Zip waiting with that request. Tell the person it did not work.")
    return {"id": r.bot_id, "name": r.name, "role": r.plan["role"], "description": r.plan["description"],
            "job": r.plan["job"], "color": r.color, "shape": "pebble"}




async def _run(r: Request) -> None:
    try:
        r.plan = general_plan(r.name) if r.general else await write_plan(r.name, r.bot_id)
        global _make_lock
        _make_lock = _make_lock or asyncio.Lock()
        async with _make_lock:
            if _requests.get(r.bot_id) is not r:
                return
            r.state = "making"
            await asyncio.wait_for(_make(r), MAKE_TIMEOUT_SEC)
        try:
            looks.save(r.bot_id, r.look)
        except Exception:
            log.warning("could not save %s's look", r.bot_id, exc_info=True)
        if r.model:

            from . import modelsetup
            await modelsetup.pin(r.bot_id, r.model)
        if not await _online(r.bot_id):
            raise NewZipError("I was made, but I'm not answering yet. Say hi in a minute.")
        r.state = "greeting"
        await asyncio.wait_for(first_turn(r.bot_id, r.name, r.device, model=r.model, general=r.general),
                               FIRST_TURN_TIMEOUT_SEC)
        _requests.pop(r.bot_id, None)
    except Exception as e:
        log.warning("new Zip %s did not finish: %s", r.bot_id, e, exc_info=not isinstance(e, NewZipError))
        if _requests.get(r.bot_id) is not r:
            return
        if r.bot_id in config.BOTS_BY_ID:
            _requests.pop(r.bot_id, None)
            return
        r.state = "failed"
        r.note = str(e) if isinstance(e, NewZipError) else \
            "I couldn't finish setting up. Delete me and try again in a minute."
        hermes.last_error.pop("default", None)


async def _make(r: Request) -> None:
    ""
    sid = await hermes.ensure_session("default", lane=MAKE_LANE)
    consent.open_pass("button", f"Create a new Zip named {r.name}", bot_id="default", session_id=sid)
    words = (f'{who.tapped("Create on the New Zip screen")} The Zip is named "{r.name}" '
             f'(request {r.id}). Call team_add_bot with request "{r.id}" and no other arguments, then reply with one '
             "word: Done.")
    reply = await hermes.run_turn("default", words, lane=MAKE_LANE)
    config.reload_bots()
    if r.bot_id not in config.BOTS_BY_ID:
        why = " ".join(str(reply or hermes.last_error.get("default") or "").split())[:200]
        log.warning("the lead did not make %s: %s", r.bot_id, why)
        raise NewZipError("I couldn't finish setting up. Delete me and try again in a minute.")


async def _online(bot_id: str) -> bool:
    ""
    deadline = time.time() + ONLINE_WAIT_SEC
    while time.time() < deadline:
        try:
            await hermes.ensure_session(bot_id)
        except Exception:
            await asyncio.sleep(2)
            continue
        try:
            await asyncio.to_thread(wallpapers.ensure, bot_id)
        except Exception:
            log.warning("could not set %s's background", bot_id, exc_info=True)
        return True
    return False




PLAN_PROMPT = """{person} just made a new AI helper (a "Zip") in the Zero app and named it "{name}". That name is the \
only brief. Work out what {person} most likely wants a helper called "{name}" for, using what the team knows about \
{person} below. Then write its job.

Return only JSON, no other text:
{{"role": "...", "description": "...", "job": "..."}}

- role: one line for the app, under 80 characters, no ending period. Example: "Classes, deadlines and study time"
- description: one or two sentences on what it owns, for routing work to it.
- job: Markdown, second person ("You own..."), 150 to 400 words: what it owns, the people, accounts, sites, dates \
and files involved that the notes below name, what to watch for, and limits specific to this area. Use only facts \
from the notes below; never invent a date, a person, a course or an account. Where the notes say nothing, say what \
to find out from {person}. End with one line saying this job is a first guess from the name and that {person}'s \
words win.
- Other Zips already own the areas listed below. Do not take over their work; if the name overlaps one, focus on what \
it does not cover and say to work with it.

Other Zips on the team:
{team}

What the team knows about {person}:
{known}

{person}'s calendar for the next two weeks:
{calendar}
"""


async def write_plan(name: str, bot_id: str, ask=None) -> dict:
    ""

    from . import briefing
    with shortreply.reading((bot_id,)):
        ask = ask or (lambda p: briefing.ask_haiku(p, timeout=60, effort="low"))
    prompt = PLAN_PROMPT.format(person=who.first(), name=name, team=_team_lines(), known=_known(bot_id) or "Nothing yet.",
                                calendar=calendar_lines() or "Nothing on it, or no calendar is connected.")
    try:
        with shortreply.reading((bot_id,)):
            text = await ask(prompt)
        plan = _parse_plan(text)
        if plan:
            return plan
        log.warning("Haiku's plan for %s was unusable: %r", name, (text or "")[:300])
    except Exception:
        log.warning("Haiku could not write %s's job", name, exc_info=True)
    return fallback_plan(name)


def _parse_plan(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    role = " ".join(str(data.get("role") or "").split()).rstrip(".")[:90]
    description = " ".join(str(data.get("description") or "").split())[:400]
    job = str(data.get("job") or "").strip()
    if not 3 <= len(role) <= 90 or len(description) < 10 or len(job) < 300:
        return None
    if len(job) < 500:
        job += "\n\n" + _GROUND_RULES
    if len(job) < 500:
        return None
    return {"role": role, "description": description, "job": job[:9000]}


_GROUND_RULES = ("Check what you know before asking, and ask only the question whose answer changes what you do. "
                 "When something belongs to another Zip, hand it over instead of doing it yourself.")


def general_plan(name: str) -> dict:
    ""

    person = who.first()
    job = (f"You are {person}'s general helper. You have no area of your own: you take whatever {person} brings you "
           f"that no other Zip owns, and quick questions of any kind.\n\n"
           f"- Answer quick questions at once, in a sentence or two.\n"
           f"- When something belongs to another Zip (school, money, clients, career, builds), ask that Zip with "
           f"ask_teammate or hand it the work on the board, and tell {person} in one line who has it.\n"
           f"- Other Zips may ask you for help or hand you work. Do it the way they would, with the same care.\n"
           f"- Keep notes on what you learn, so {person} never has to repeat it.\n"
           f"- Work it out from what you can read before you ask. Ask only the question whose answer changes what "
           f"you do.\n\n"
           f"{person} made you as an experiment, to see how a faster model feels as one of the Zips. Do the work as "
           f"well as any Zip would; never cut a step because you are the fast one.\n\n"

           f"Never write an em dash or an en dash. Use a comma, a period or two sentences instead.\n\n"
           + _GROUND_RULES)
    return {"role": "Quick questions and anything that has no Zip yet",
            "description": f"{name} is a general helper: quick questions, and work no other Zip owns.", "job": job}


def fallback_plan(name: str) -> dict:
    person = who.first()
    job = (f"{person} made you in the app and named you {name}. That name is the only brief, so work out what you "
           f"are for from your name and what you know about {person}, and confirm it in your first conversation.\n\n"
           f"- Own everything that fits your name and that no other Zip already owns.\n"
           f"- Learn the people, accounts, sites, dates and files involved, and keep notes so {person} never has to "
           f"repeat them.\n"
           f"- Watch for what matters in your area: deadlines, changes and anything {person} should decide.\n"
           f"- When {person} tells you what you are for, that wins over this guess.\n\n"
           + _GROUND_RULES)
    return {"role": "Learning what you need help with",
            "description": f"A new Zip named {name}. It owns what its name suggests until {person} says more.",
            "job": job}


def _team_lines() -> str:
    lines = [f"- {b.name}: {b.role}" for b in config.BOTS if b.id != "default" and b.role and not b.team]
    return "\n".join(lines) or "None yet."


def _known(bot_id: str, limit: int = 4000) -> str:
    ""
    parts = []
    try:
        user = (config.HERMES_HOME / "memories" / "USER.md").read_text().strip()
        if user:
            parts.append(user[:limit])
    except OSError:
        pass
    try:
        shared = facts.briefing_text(bot_id, limit_chars=limit)
        if shared:
            parts.append(shared)
    except Exception:
        log.warning("could not read the team's facts", exc_info=True)
    from . import privatewords
    return privatewords.leave_out("\n\n".join(parts))


def calendar_lines(now: float | None = None, days: int = 14, limit: int = 25) -> str:
    ""
    from . import notices
    now = now or time.time()
    tz = home.zone()
    out = []
    for e in notices.calendar_events():
        if e["end"] <= now or e["start"] > now + days * 86400 or not e.get("title"):
            continue
        t = datetime.fromtimestamp(e["start"], tz)
        when = t.strftime("%a %b %-d") + ("" if e.get("all_day") else ", " + t.strftime("%-I:%M %p").replace(":00", ""))
        out.append(f"- {when}: {' '.join(str(e['title']).split())[:80]}")
        if len(out) >= limit:
            break
    return "\n".join(out)




FIRST_TURN = """{note} {person} just made you in the app and named you {name}. That name was the only brief, so the \
job above is a first guess. Send your first message now.

- Start by saying hi to {person} and what you're here for, in one short sentence in your own words. Say "I", \
never your name. Follow the shared voice guide below.
- Then, in one sentence, one specific thing you know that matters right now for that job: a date, a deadline or a \
fact from the notes below or your memory. Use only what is written there; never invent one. If nothing fits, leave \
this sentence out.
- Then call offer_choices once: a short question such as "What would help most right now?" and three or four concrete \
ways to start, each under 60 characters, written as {person} would say them ("Finish this weekend's assignments").
- Two or three sentences in all. No list, no question in the text (the card asks it) and no other tools: what you \
need is here and in your memory.

What the team knows about {person}:
{known}

{person}'s calendar for the next two weeks:
{calendar}""" + "\n\n" + zipvoice.TEXT


KEYED_HELLO = ("- After your first sentence, add one short sentence saying you run on a different, faster model than "
               "the other Zips and that you're an experiment. Don't name the company or the model.")
GENERAL_BRIEF = ("{person} chose a general helper, so you have no area of your own: say you're here for quick questions "
                 "and anything that has no Zip yet.")


async def first_turn(bot_id: str, name: str, device: str | None = None, model: str = "",
                     general: bool = False) -> str | None:
    ""


    from . import modelsetup
    note = FIRST_TURN.format(note=who.hub_note(), person=who.first(), name=name,
                             known=_known(bot_id, limit=3000) or "Nothing yet.",
                             calendar=calendar_lines() or "Nothing on it, or no calendar is connected.")
    m = modelsetup.M.CATALOG.get(model or "")
    extra = ([GENERAL_BRIEF.format(person=who.first())] if general else []) + ([KEYED_HELLO] if m and m.keyed else [])
    if extra:
        head, sep, rest = note.partition("\n- Then, in one sentence")
        note = head + "\n" + "\n".join(extra) + sep + rest if sep else note + "\n\n" + "\n".join(extra)
    try:
        reply = await turns.run_internal(bot_id, note)
        if reply and not chatter.empty(reply):
            store.add_feed("message_local", bot_id, None, name, reply[:600],
                           written_by="zip", written_for="person")
            await _tell_away(bot_id, name, reply, device)
        return reply
    finally:
        choices.flush(bot_id)


async def _tell_away(bot_id: str, name: str, reply: str, device: str | None) -> None:
    from . import looking, watcher
    if not looking.away(device):
        return
    first = next((p for p in re.split(r"\n\s*\n", reply.strip()) if p.strip()), reply)
    try:
        await watcher.push(name, first, bot_id, urgent=True, kind="reply", exact=True)
    except Exception:
        log.warning("could not send %s's first message as a notification", bot_id, exc_info=True)
