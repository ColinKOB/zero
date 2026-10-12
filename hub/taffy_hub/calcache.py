"""The hub's copy of the person's calendar and Trello cards (proactivity phase 2, docs/research/2026-10-08-proactivity-design.md).

Once an hour in waking hours the hub reads Google Calendar and Trello through the same path the Zips' `connectors`
tool uses: Claude Code on the person's Claude plan, signed in with their own account, running Haiku with only that one
connector's tools. Every tool that would change anything is switched off for these runs, so a refresh can only read.
Nothing here uses an API key or a paid model.

What it keeps:

- `hub-state/calendar.json`: the events from yesterday through the next eight days, with their places and the people
  invited. notices.py reads it for busy blocks; the calendar noticers read it for overlaps, prep and follow-ups.
- `hub-state/trello.json`: open cards with a due date and the cards in the Doing list.

Each file is written only when what it holds changed, so a change is the calendar wake (notices.wake) and an
unchanged hour costs nothing downstream. The voice `lookup` tool can read the same copy later (events_between,
cards), so a call answers "what's on tomorrow" without a fresh connector run.

A home with its own calendars in home.json (`calendars`: Google through Sign In with Google, CalDAV, private iCal
addresses; calsources.py) reads those directly instead, with no model call. Trello connected in Connect Apps (apps.py)
is read the same way, through Trello's own API (trello.cache_cards), on any plan. Otherwise Trello is read only when
the home names a board (home.json `integrations.trello`) and connectors come through Claude.

Refreshes pause while the Claude plan is at its limit, and run only while proactivity is on.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import timedelta

from . import config, home, notices, store

log = logging.getLogger("taffy.calcache")

TRELLO_FILE = config.HUB_DIR / "trello.json"
STATUS = "proactivity_cache"
DAYS_BACK, DAYS_AHEAD = 1, 8
TIMEOUT_SEC = 240
MODEL = "claude-haiku-5-5"   # the connectors tool's model (taffy-tools tools.CONNECTOR_MODEL)

# Claude Code's tool prefix for each connector, and every tool that could change something. The runs allow the
# connector as a server minus these (allowing tools one by one stops ToolSearch from loading them; see tools.py).
SERVERS = {"calendar": "mcp__claude_ai_Google_Calendar", "trello": "mcp__claude_ai_Trello"}
WRITE_TOOLS = {
    "calendar": ["create_event", "update_event", "delete_event", "respond_to_event"],
    "trello": ["trelloWriteBoard", "trelloWriteCard", "trelloWriteChecklist", "trelloWriteInbox", "trelloWriteList",
               "trelloWritePlanner", "trelloWriteWorkspace"],
}


def trello_board() -> str | None:
    """The board named in the prompt (it closes the quote the prompt opens), or None when the home has none."""
    t = home.trello()
    if not t:
        return None
    name = str(t.get("name") or t.get("board_name") or "").replace('"', "'")
    return f'{name}" (id {t["board_id"]}' if name else f'{t["board_id"]}" (id {t["board_id"]}'


CAL_PROMPT = """You are reading {person}'s Google Calendar for {person}'s assistant. Time zone {tz}. Only read; \
never create, change, delete or answer anything.

List every event on all of {first}'s calendars from {start} through {end}. Reply with one JSON object and nothing else:
{{"events": [{{"id": "event id", "title": "title", "start": "ISO 8601 with offset, or YYYY-MM-DD for all-day", \
"end": "same form", "all_day": false, "location": "place or empty", "calendar": "calendar name", \
"organizer": "email or empty", "my_response": "accepted, declined, tentative, needsAction or empty", \
"show_as": "busy, or free when the event is transparent", \
"attendees": [{{"name": "name", "email": "email", "self": false}}], "description": "first 200 characters or empty"}}]}}
Include events {first} declined, with my_response "declined". If there are none, reply {{"events": []}}."""

TRELLO_PROMPT = """You are reading {person}'s Trello for {person}'s assistant. Time zone {tz}. Only read; never \
create, change, move or archive anything.

Read the board "{board}) and any other open boards {first} belongs to. List every open card that has a due date \
between {start} and {end}, and every card in a list whose name starts with "Doing". Reply with one JSON object and \
nothing else:
{{"cards": [{{"id": "card id", "name": "card name", "board": "board name", "list": "list name", \
"due": "ISO 8601 with offset or null", "due_complete": false, "labels": ["label"], "url": "card link"}}]}}
If there are none, reply {{"cards": []}}."""


class CacheError(RuntimeError):
    pass


def _cal_file():
    return notices.CALENDAR_FILE


# MARK: Running a read-only connector call on the person's Claude plan

def command(service: str, prompt: str) -> list[str]:
    """`claude -p` with Haiku and only this connector's reading tools (ToolSearch loads them)."""
    from . import accounts
    exe = accounts.claude_bin() or "claude"
    server = SERVERS[service]
    return [exe, "-p", prompt, "--model", MODEL, "--tools", "ToolSearch",
            "--allowedTools", f"{server},ToolSearch",
            "--disallowedTools", ",".join(f"{server}__{t}" for t in WRITE_TOOLS[service]),
            "--output-format", "json"]


async def run_connector(service: str, prompt: str) -> str:
    """One read-only connector run; its answer text. Tests replace this."""
    from . import accounts, usage
    started = time.monotonic()
    env = accounts._env()
    env["MCP_CONNECTION_NONBLOCKING"] = "false"   # wait for the claude.ai connectors before the turn starts
    proc = await asyncio.create_subprocess_exec(*command(service, prompt), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL,
                                                cwd=str(config.HOME), env=env, start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_SEC)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    try:
        data = json.loads(out.decode(errors="replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        usage.log_cli(f"hub-calcache-{service}", None, MODEL, started)
        raise CacheError("the connector run gave no answer") from None
    usage.log_cli(f"hub-calcache-{service}", data, MODEL, started)
    if data.get("is_error"):
        raise CacheError(str(data.get("result") or "the connector run failed")[:200])
    return str(data.get("result") or "")


async def connected(service: str) -> str:
    """"ok" when the account is reachable through Claude, else why not. A status the hub cannot read never stops a
    refresh (the run itself then says)."""
    from . import accounts
    try:
        result = await accounts.check()
    except Exception:
        return "ok"
    st = accounts.state_of(result, service)
    if accounts.route(result, service).get("via") == "chatgpt":
        return "chatgpt"
    return "ok" if st in (accounts.CONNECTED, accounts.UNKNOWN, accounts.TROUBLE) else "not_connected"


# MARK: Reading the answers

def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise CacheError("the answer had no list")
    try:
        out = json.loads(m.group(0))
    except ValueError:
        raise CacheError("the answer was not readable") from None
    if not isinstance(out, dict):
        raise CacheError("the answer was not readable")
    return out


def _clean(v, limit: int = 200) -> str:
    return " ".join(str(v or "").split())[:limit]


def parse_events(text: str) -> list[dict]:
    out = []
    for e in _json(text).get("events") or []:
        if not isinstance(e, dict):
            continue
        start, end = notices._ts(e.get("start")), notices._ts(e.get("end"))
        all_day = bool(e.get("all_day")) or bool(re.fullmatch(r"\d{4}-\d\d-\d\d", str(e.get("start") or "")))
        if start is None:
            continue
        if end is None or end <= start:
            end = start + (86400 if all_day else 3600)
        people = []
        for a in e.get("attendees") or []:
            if isinstance(a, dict) and (a.get("email") or a.get("name")):
                people.append({"name": _clean(a.get("name"), 80), "email": _clean(a.get("email"), 120).lower(),
                               "self": bool(a.get("self"))})
        title = _clean(e.get("title") or e.get("summary"), 160)
        eid = _clean(e.get("id"), 200) or hashlib.sha1(f"{title}|{start}".encode()).hexdigest()[:16]
        out.append({"id": eid, "title": title, "start": start, "end": end, "all_day": all_day,
                    "location": _clean(e.get("location"), 200), "calendar": _clean(e.get("calendar"), 80),
                    "organizer": _clean(e.get("organizer"), 120).lower(),
                    "my_response": _clean(e.get("my_response"), 20).lower(), "attendees": people[:30],
                    "free": str(e.get("show_as") or e.get("transparency") or "").lower() in ("free", "transparent"),
                    "description": _clean(e.get("description"), 300)})
    return sorted(out, key=lambda x: (x["start"], x["id"]))


def parse_cards(text: str) -> list[dict]:
    out = []
    for c in _json(text).get("cards") or []:
        if not isinstance(c, dict) or not (c.get("name") or "").strip():
            continue
        due = notices._ts(c.get("due")) if c.get("due") else None
        out.append({"id": _clean(c.get("id"), 80) or hashlib.sha1(_clean(c.get("name")).encode()).hexdigest()[:16],
                    "name": _clean(c.get("name"), 200), "board": _clean(c.get("board"), 120),
                    "list": _clean(c.get("list"), 80), "due": due, "due_complete": bool(c.get("due_complete")),
                    "labels": [_clean(x, 40) for x in (c.get("labels") or []) if isinstance(x, str)][:8],
                    "url": _clean(c.get("url"), 300)})
    return sorted(out, key=lambda x: (x["due"] or 0, x["id"]))


# MARK: The files

def _write_if_changed(path, key: str, items: list[dict]) -> bool:
    try:
        old = json.loads(path.read_text()).get(key)
    except (OSError, ValueError, AttributeError):
        old = None
    if old == items:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({key: items}, indent=1))
    os.replace(tmp, path)
    return True


def trello_cards() -> list[dict]:
    try:
        data = json.loads(TRELLO_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [c for c in (data.get("cards") or []) if isinstance(c, dict)] if isinstance(data, dict) else []


def events_between(start: float, end: float) -> list[dict]:
    """For the voice lookup: events that overlap [start, end), from the hourly copy."""
    return [e for e in notices.calendar_events() if e["start"] < end and e["end"] > start]


def cards() -> list[dict]:
    """For the voice lookup: the Trello cards with a due date and the Doing list, from the hourly copy."""
    return trello_cards()


# MARK: Refreshing

def status() -> dict:
    return store.get_setting(STATUS, {}) or {}


def _note(service: str, **fields) -> None:
    s = status()
    s[service] = {**(s.get(service) or {}), **fields}
    store.set_setting(STATUS, s)


def _plan_ok() -> bool:
    try:
        from . import health
        return bool(health.status.get("claude", True))
    except Exception:
        return True


def source(service: str) -> str:
    """Where the copy comes from: "own" (the home's calendars, or Trello connected in Connect Apps, read directly),
    "connector", or "none"."""
    if service == "calendar" and home.calendars():
        return "own"
    if service == "trello":
        try:
            from . import apps
            if apps.connected_natively("trello"):
                return "own"
        except Exception:
            log.warning("could not read Connect Apps", exc_info=True)
    if home.connectors() == "none":
        return "none"
    if service == "trello" and not home.trello():
        return "none"
    return "connector"


async def _refresh_own_trello(t: float) -> bool:
    """Trello connected in Connect Apps: the cards straight from Trello's API, no model."""
    from . import trello
    day0 = notices.day_start(t)
    items = await asyncio.to_thread(trello.cache_cards, day0 - DAYS_BACK * 86400, day0 + (DAYS_AHEAD + 1) * 86400)
    notices._log("cache", "trello")
    changed = _write_if_changed(TRELLO_FILE, "cards", items)
    _note("trello", state="ok", tried=t, at=t, count=len(items))
    if changed:
        notices.wake("trello")
    return changed


async def _refresh_own(t: float) -> bool:
    from . import calsources
    day0 = notices.day_start(t)
    items, failed = await asyncio.to_thread(calsources.events, day0 - DAYS_BACK * 86400,
                                            day0 + (DAYS_AHEAD + 1) * 86400)
    notices._log("cache", "calendar")
    if failed and not items:
        _note("calendar", state="failed", tried=t)
        return False
    changed = _write_if_changed(_cal_file(), "events", items)
    _note("calendar", state="ok", tried=t, at=t, count=len(items))
    if changed:
        notices.wake("calendar")
    return changed


async def refresh(service: str) -> bool:
    """One refresh. True when the copy changed (which wakes the noticers that read it)."""
    t = notices.now()
    src = source(service)
    if src == "none":
        _note(service, state="not_set", tried=t)
        return False
    if src == "own":
        try:
            return await (_refresh_own_trello(t) if service == "trello" else _refresh_own(t))
        except Exception as e:
            log.info("%s copy not refreshed: %s", service, e)
            _note(service, state="failed", tried=t)
            return False
    why = await connected(service)
    if why != "ok":
        _note(service, state=why, tried=t)
        return False
    day0 = notices._et(notices.day_start(t))
    start = (day0 - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    end = (day0 + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
    person = notices.person_name()
    from . import who
    names = {"person": person, "first": who.first(), "tz": home.timezone_name()}
    notices._log("cache", service)
    try:
        if service == "calendar":
            items = parse_events(await run_connector("calendar", CAL_PROMPT.format(start=start, end=end, **names)))
            changed = _write_if_changed(_cal_file(), "events", items)
        else:
            items = parse_cards(await run_connector("trello", TRELLO_PROMPT.format(start=start, end=end,
                                                                                   board=trello_board(), **names)))
            changed = _write_if_changed(TRELLO_FILE, "cards", items)
    except Exception as e:
        log.info("%s copy not refreshed: %s", service, e)
        _note(service, state="failed", tried=t)
        return False
    _note(service, state="ok", tried=t, at=t, count=len(items))
    if changed:
        notices.wake(service)
    return changed


_task: asyncio.Task | None = None


def due(service: str) -> bool:
    every = float(notices.settings()[f"{service}_every_min"]) * 60
    last = float((status().get(service) or {}).get("tried") or 0)
    return notices.now() - last >= every


async def tick() -> None:
    """From notices.tick: one refresh at a time, hourly in waking hours, never at the plan's limit."""
    global _task
    if not notices.enabled() or not notices.waking() or not _plan_ok():
        return
    if _task is not None and not _task.done():
        return
    for service in ("calendar", "trello"):
        if due(service):
            _task = asyncio.create_task(refresh(service))
            return


# MARK: Busy blocks

SHIFT = re.compile(r"\b(ups|shift|work shift)\b", re.I)
CLASS = re.compile(r"\b(class|lecture|seminar|lab session|exam|midterm|final exam)\b", re.I)
COURSE = re.compile(r"\b[A-Z]{2,4}[ -]?\d{3}\b")
CHURCH = re.compile(r"\b(church|worship|sunday service|sound (team|booth|check)|church sound|bible study|"
                    r"small group)\b", re.I)
MEETING = re.compile(r"\b(client|meeting|call|zoom|google meet|consult|consultation|interview|appointment|advising|"
                     r"1:1|one on one)\b|\bwith [A-Z]", re.I)
DEADLINE = re.compile(r"\b(due|deadline|submit by|expires?)\b", re.I)
VIRTUAL = re.compile(r"(https?://|zoom\.us|meet\.google|teams\.microsoft|\bphone\b|\bonline\b|\bvirtual\b)", re.I)


def outside_people(e: dict) -> list[dict]:
    """People invited who are not the person (their own addresses, or marked self) and not a room or group
    calendar."""
    from . import mail
    out = []
    for a in e.get("attendees") or []:
        email = (a.get("email") or "").lower()
        if a.get("self") or email in mail.OWN or not email or "@" not in email:
            continue
        if re.search(r"(resource\.calendar\.google\.com|group\.calendar\.google\.com|@group\.|calendar-)", email):
            continue
        out.append(a)
    return out


def declined(e: dict) -> bool:
    return (e.get("my_response") or "").lower() == "declined"


def busy_kind(e: dict, extra_words: list[str] | None = None) -> str | None:
    """Why an event is a time the person can't look at the phone: a shift, a class, church, or a meeting with someone
    outside. All-day events, declined or free ones and deadlines are not busy blocks."""
    title = e.get("title") or ""
    if e.get("all_day") or declined(e) or e.get("free") or DEADLINE.search(title):
        return None
    if SHIFT.search(title):
        return "shift"
    if CHURCH.search(title):
        return "church"
    if CLASS.search(title) or COURSE.search(title):
        return "class"
    if outside_people(e) or re.search(r"\bclient\b", title, re.I):
        return "meeting"
    for w in extra_words or []:
        if w and re.search(rf"\b{re.escape(w.lower())}\b", title.lower()):   # whole words: not "Upset", "Classical"
            return "busy"
    return None


def physical_place(e: dict) -> str:
    """The event's place when it is somewhere the person has to travel to; "" for a link, a phone call or nothing."""
    loc = (e.get("location") or "").strip()
    return "" if not loc or VIRTUAL.search(loc) else loc
