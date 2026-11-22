""

































import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import timedelta

from . import config, home, modelsetup, notices, store

log = logging.getLogger("taffy.calcache")

TRELLO_FILE = config.HUB_DIR / "trello.json"
STATUS = "proactivity_cache"
DAYS_BACK, DAYS_AHEAD = 1, 8
TIMEOUT_SEC = 240
MODEL = modelsetup.M.HELPER["claude"].id
CHATGPT_MODEL = modelsetup.M.HELPER["chatgpt"].id
OWN_EVERY_SEC = 10 * 60



SERVERS = {"calendar": "mcp__claude_ai_Google_Calendar", "trello": "mcp__claude_ai_Trello"}
WRITE_TOOLS = {
    "calendar": ["create_event", "update_event", "delete_event", "respond_to_event"],
    "trello": ["trelloWriteBoard", "trelloWriteCard", "trelloWriteChecklist", "trelloWriteInbox", "trelloWriteList",
               "trelloWritePlanner", "trelloWriteWorkspace"],
}




CHATGPT_WRITE_TOOLS = {
    "calendar": ["delete_event", "respond_event", "create_event", "update_event", "set_event_label_silently"],
    "trello": ["trelloWriteBoard", "trelloWriteCard", "trelloWriteChecklist", "trelloWriteInbox", "trelloWriteList",
               "trelloWritePlanner"],
}


def trello_board() -> str | None:
    ""
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




def command(service: str, prompt: str) -> list[str]:
    ""
    from . import accounts
    exe = accounts.claude_bin() or "claude"
    server = SERVERS[service]
    return [exe, "-p", prompt, "--model", MODEL, "--tools", "ToolSearch",
            "--allowedTools", f"{server},ToolSearch",
            "--disallowedTools", ",".join(f"{server}__{t}" for t in WRITE_TOOLS[service]),
            "--output-format", "json"]


def chatgpt_command(service: str, app: str, prompt: str) -> list[str] | None:
    ""

    from . import accounts
    exe = accounts.codex_bin()
    if not exe or service not in CHATGPT_WRITE_TOOLS or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", app or ""):
        return None
    cmd = [exe, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
           "-s", "read-only", "-m", CHATGPT_MODEL, "--json", "-C", str(config.HOME)]
    for feature in ("shell_tool", "unified_exec", "browser_use", "computer_use", "multi_agent", "image_generation",
                    "memories"):
        cmd += ["--disable", feature]
    cmd += ["-c", "tools.web_search=false", "-c", "apps._default.enabled=false",
            "-c", "apps._default.destructive_enabled=false", "-c", f"apps.{app}.enabled=true"]
    for tool in CHATGPT_WRITE_TOOLS[service]:
        cmd += ["-c", f"apps.{app}.tools.{tool}.enabled=false"]
    return cmd + [prompt]


async def run_connector(service: str, prompt: str, route: dict | None = None) -> str:
    ""

    from . import usage
    with usage.errand(f"hub-calcache-{service}"):
        if (route or {}).get("via") == "chatgpt":
            return await _run_chatgpt(service, str(route.get("app") or ""), prompt)
        return await _run_connector(service, prompt)


async def _read(service: str, prompt: str, how: str) -> str:
    ""
    if how == "chatgpt":
        return await run_connector(service, prompt, {"via": "chatgpt", "app": _chatgpt_app(service)})
    return await run_connector(service, prompt)


async def _run_chatgpt(service: str, app: str, prompt: str) -> str:
    from . import accounts
    cmd = chatgpt_command(service, app, prompt)
    if not cmd:
        raise CacheError("the account can't be read through ChatGPT")
    env = {k: v for k, v in accounts._env().items() if k != "CODEX_HOME"}
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                                stdin=asyncio.subprocess.DEVNULL, cwd=str(config.HOME), env=env,
                                                start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_SEC)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    text, failed = "", False
    for line in out.decode(errors="replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict):
            continue
        item = m.get("item") or {}
        if m.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
        elif m.get("type") in ("turn.failed", "error"):
            failed = True
    if not text or (failed and proc.returncode):
        raise CacheError("the connector run failed")
    return text


async def _run_connector(service: str, prompt: str) -> str:
    from . import accounts, usage
    started = time.monotonic()
    env = accounts._env()
    env["MCP_CONNECTION_NONBLOCKING"] = "false"
    data = None
    try:
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
            raise CacheError("the connector run gave no answer") from None
        if not isinstance(data, dict) or data.get("is_error") or proc.returncode:
            raise CacheError("the connector run failed")
        return str(data.get("result") or "")
    finally:
        usage.log_cli(f"hub-calcache-{service}", data if isinstance(data, dict) else None, MODEL, started)


async def connected(service: str) -> str:
    ""

    from . import accounts
    try:
        result = await accounts.check()
    except Exception:
        return "ok"
    st = accounts.state_of(result, service)
    if accounts.route(result, service).get("via") == "chatgpt":
        return "chatgpt" if st == accounts.CONNECTED and _chatgpt_app(service) else "not_connected"
    return "ok" if st in (accounts.CONNECTED, accounts.UNKNOWN, accounts.TROUBLE) else "not_connected"


def accounts_apps() -> dict:
    ""
    from . import accounts
    return accounts._apps


def _chatgpt_app(service: str) -> str:
    return str(accounts_apps().get(service) or "")




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
                    "description": _clean(e.get("description"), 300),
                    "recurring": bool(e.get("recurring") or e.get("recurringEventId"))})
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
    ""
    return [e for e in notices.calendar_events() if e["start"] < end and e["end"] > start]


def cards() -> list[dict]:
    ""
    return trello_cards()




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
    ""

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


def calendar_sources() -> list[str]:
    ""

    out = ["own"] if home.calendars() else []
    if home.connectors() != "none":
        out.append("connector")
    return out


def _merge(old: list[dict], fresh: dict[str, list[dict]], kept: tuple[str, ...]) -> list[dict]:
    ""


    out = [dict(e, source=src) for src, items in fresh.items() for e in items]
    out += [e for e in old if e.get("source") in kept and e.get("source") not in fresh]
    seen, unique = set(), []
    for e in sorted(out, key=lambda x: (x["start"], x.get("source") != "own", x["id"])):
        key = (" ".join(str(e.get("title") or "").lower().split()), e["start"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


async def _refresh_own_trello(t: float) -> bool:
    ""
    from . import trello
    day0 = notices.day_start(t)
    items = await asyncio.to_thread(trello.cache_cards, day0 - DAYS_BACK * 86400, day0 + (DAYS_AHEAD + 1) * 86400)
    notices._log("cache", "trello")
    changed = _write_if_changed(TRELLO_FILE, "cards", items)
    _note("trello", state="ok", tried=t, at=t, count=len(items))
    if changed:
        notices.wake("trello")
    return changed


async def _own_events(t: float) -> list[dict] | None:
    ""
    from . import calsources
    day0 = notices.day_start(t)
    items, failed = await asyncio.to_thread(calsources.events, day0 - DAYS_BACK * 86400,
                                            day0 + (DAYS_AHEAD + 1) * 86400)
    return None if failed and not items else items


def _window(t: float) -> tuple[str, str]:
    day0 = notices._et(notices.day_start(t))
    return ((day0 - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d"), (day0 + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d"))


def _names() -> dict:
    from . import who
    return {"person": notices.person_name(), "first": who.first(), "tz": home.timezone_name()}


async def _refresh_calendar(t: float, only: tuple[str, ...] | None = None) -> bool:
    ""

    wanted = [src for src in calendar_sources() if only is None or src in only]
    if not wanted and not calendar_sources():
        _note("calendar", state="not_set", tried=t)
        return False
    fresh: dict[str, list[dict]] = {}
    notes: dict = {}
    notices._log("cache", "calendar")
    for src in wanted:
        try:
            if src == "own":
                items = await _own_events(t)
                notes["own_tried"] = t
                if items is None:
                    raise CacheError("none of the home's calendars could be read")
            else:
                notes["tried"] = t
                why = await connected("calendar")
                notes["connector"] = why
                if why not in ("ok", "chatgpt"):
                    continue
                start, end = _window(t)
                items = parse_events(await _read("calendar", CAL_PROMPT.format(start=start, end=end, **_names()), why))
            fresh[src] = items
        except Exception as e:
            log.info("calendar copy: %s not refreshed: %s", src, e)
            notes["own" if src == "own" else "connector"] = "failed"



    held = {e.get("source") for e in notices.calendar_events()}
    before = status().get("calendar") or {}
    for src in [s for s, items in fresh.items() if not items and s in held]:
        notes[f"{src}_empty"] = t
        if not before.get(f"{src}_empty"):
            log.info("calendar copy: %s answered with no events while it held some; kept until a second read agrees", src)
            del fresh[src]
    for src in fresh:
        if fresh[src]:
            notes[f"{src}_empty"] = 0
    if not fresh:
        why = str(notes.get("connector") or "failed")
        _note("calendar", state="failed" if why in ("ok", "chatgpt") or "failed" in (notes.get("own"), why) else why, **notes)
        return False
    merged = _merge(notices.calendar_events(), fresh, tuple(calendar_sources()))
    changed = _write_if_changed(_cal_file(), "events", merged)
    _note("calendar", state="ok", at=t, count=len(merged), **{f"{src}_count": len(items) for src, items in fresh.items()},
          **notes)
    if changed:
        notices.wake("calendar")
    return changed


async def refresh(service: str, only: tuple[str, ...] | None = None) -> bool:
    ""
    t = notices.now()
    if service == "calendar":
        return await _refresh_calendar(t, only)
    src = source(service)
    if src == "none":
        _note(service, state="not_set", tried=t)
        return False
    if src == "own":
        try:
            return await _refresh_own_trello(t)
        except Exception as e:
            log.info("%s copy not refreshed: %s", service, e)
            _note(service, state="failed", tried=t)
            return False
    why = await connected(service)
    if why not in ("ok", "chatgpt"):
        _note(service, state=why, tried=t)
        return False
    start, end = _window(t)
    notices._log("cache", service)
    try:
        items = parse_cards(await _read("trello", TRELLO_PROMPT.format(start=start, end=end, board=trello_board(),
                                                                       **_names()), why))
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


def calendar_due() -> tuple[str, ...]:
    ""
    st, out = status().get("calendar") or {}, []
    for src in calendar_sources():
        if src == "own" and notices.now() - float(st.get("own_tried") or 0) >= OWN_EVERY_SEC:
            out.append(src)
        elif src == "connector" and due("calendar"):
            out.append(src)
    return tuple(out)


async def tick() -> None:
    ""
    global _task
    if not notices.enabled() or not notices.waking() or not _plan_ok():
        return
    if _task is not None and not _task.done():
        return
    sources_due = calendar_due()
    if sources_due or (not calendar_sources() and due("calendar")):
        every = not sources_due or set(sources_due) == set(calendar_sources())
        _task = asyncio.create_task(refresh("calendar") if every else refresh("calendar", sources_due))
        return
    if due("trello"):
        _task = asyncio.create_task(refresh("trello"))




SHIFT = re.compile(r"\b(ups|shift|work shift)\b", re.I)
CLASS = re.compile(r"\b(class|lecture|seminar|lab session|exam|midterm|final exam)\b", re.I)
COURSE = re.compile(r"\b[A-Z]{2,4}[ -]?\d{3}\b")
CHURCH = re.compile(r"\b(church|worship|sunday service|sound (team|booth|check)|church sound|bible study|"
                    r"small group)\b", re.I)
MEETING = re.compile(r"\b(client|meeting|call|zoom|google meet|consult|consultation|interview|appointment|advising|"
                     r"1:1|one on one)\b|\bwith [A-Z]", re.I)
DEADLINE = re.compile(r"\b(due|deadline|submit by|expires?)\b", re.I)


APPOINTMENT = re.compile(r"\b(interview|meeting|appointment|appt|advising|doctor|dentist|orientation|"
                         r"consultation|hearing|jury)\b", re.I)
VIRTUAL = re.compile(r"(https?://|zoom\.us|meet\.google|teams\.microsoft|\bphone\b|\bonline\b|\bvirtual\b)", re.I)


def outside_people(e: dict) -> list[dict]:
    ""

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
    ""

    title = e.get("title") or ""
    if e.get("all_day") or declined(e) or e.get("free") or DEADLINE.search(title):
        return None
    if SHIFT.search(title):
        return "shift"
    if CHURCH.search(title):
        return "church"
    if CLASS.search(title) or COURSE.search(title):
        return "class"
    if outside_people(e) or re.search(r"\bclient\b", title, re.I) or APPOINTMENT.search(title):
        return "meeting"
    for w in extra_words or []:
        if w and re.search(rf"\b{re.escape(w.lower())}\b", title.lower()):
            return "busy"
    return None


def physical_place(e: dict) -> str:
    ""

    loc = (e.get("location") or "").strip()
    if loc:
        return "" if VIRTUAL.search(loc) else loc
    return town(e)





TRAVEL = re.compile(r"\b(trip|travel(?:ing)?|overnight|staying|hotel|motel|airbnb|vacation|camping|campground|"
                    r"getaway|flight)\b", re.I)
_TOWN = re.compile(r"\b(?:in|to) ((?:[A-Z][a-z'.-]+ )*[A-Z][a-z'.-]+)")


def town(e: dict) -> str:
    ""

    title, notes = str(e.get("title") or ""), str(e.get("description") or "")
    if not TRAVEL.search(f"{title}\n{notes}"):
        return ""
    for text in (title, notes):
        if m := _TOWN.search(re.sub(r"\([^)]*\)", " ", text)):
            return m.group(1)
    return ""


def trip(e: dict) -> bool:
    ""


    if e.get("free") or declined(e) or e.get("recurring") or str(e.get("status") or "").lower() == "cancelled":
        return False
    return (e["end"] - e["start"] > 86400 or bool(TRAVEL.search(f"{e.get('title') or ''}\n{e.get('description') or ''}"))
            or bool(e.get("all_day") and physical_place(e)))
