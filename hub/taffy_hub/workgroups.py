""



























from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta

from . import config, home, store, who

log = logging.getLogger("taffy.workgroups")

ENABLED = False
EVERY_SEC = 20
PER_TICK = 4
TIMEOUT_SEC = 25
RAISE_EVERY_SEC = 300
FRESH_SEC = 30 * 60
WEEK_DAYS = 7
WHY_MAX = 60
LINE_MAX = 160
NOW, WEEK, WHENEVER = "now", "week", "whenever"
DATE_ONLY = ("deadline", "calendar", "meeting", "todo", "reminder")
STOPS_A_ZIP = ("task", "approval")
KIND_WORDS = {"task": "a helper's task that stopped and waits on an answer", "approval": "an okay a helper asked for",
              "choices": "a question a helper asked", "team_need": "something a team of helpers asked for",
              "mail": "an email a helper drafted for sending", "email": "an email waiting on a reply",
              "text": "a text message waiting on a reply", "todo": "a line on the to-do list",
              "reminder": "a reminder in the Reminders app"}

SYSTEM = "You sort a person's to-do rows and answer with JSON only."
PROMPT = """{first} has this row on a list of things waiting on {first}. Decide whether it has to be dealt with today.

The row: {act}
More about it: {context}
What it is: {kind}
Who raised it: {zip}
What the work is waiting for: {waiting}
Its date: {due}

"stopped": true only when a helper has work under way that cannot move at all until {first} deals with this row, and \
that work matters today. A helper asking, or calling it urgent, is not enough: read what the work is waiting for. A \
choice about something for later, a nudge, a reply that could go tomorrow, an okay for a job that runs on another day: \
false.
"costs_today": true only when leaving it until tomorrow would lose {first} money, a deadline, a booking, or someone \
who is waiting on {first} today.
"why": at most 7 plain words on who or what is waiting, or what it costs, such as "The buyer is waiting" or "{zip} is \
stopped until you answer". No ids, no dates written in numbers, no words like task, server or token.
"later": true only when {first} already answered this helper and the work now waits for something {first} said \
{first} would do later. A question {first} has not answered yet: false.
When you are not sure, answer false.

Reply with JSON only: {{"stopped": false, "costs_today": false, "later": false, "why": "..."}}"""

PROJECT_SYSTEM = "You write one plain status sentence for a person's project list. Reply with the sentence only."
PROJECT_PROMPT = """{zip} is an assistant looking after this project for {first}. Write where it stands, as {zip} \
would say it to {first}.

Project: {title}
Being worked on now: {doing}
Waiting on {first}: {asks}
Latest news: {news}
Its next date: {date}

Rules:
- One or two short sentences, at most 22 words in all, in the first person as {zip} ("I'm drafting section two. \
Nothing needs you." or "The agenda is ready for you to read.").
- Say plainly whether anything needs {first}. Never use the name {zip}.
- No ids, file paths, tool names or words like task, board, server or token."""

_IDS = re.compile(r"\b(?:[0-9a-f]{8,}|[A-Za-z]+_[A-Za-z0-9_]+|#?\d{5,})\b", re.I)
_pending: dict[str, dict] = {}
_pending_projects: dict[str, dict] = {}
_generation = 0


def generation() -> int:
    ""

    return _generation


def cancel_writes() -> None:
    global _generation
    with store._lock:
        _generation += 1
        _pending.clear()
        _pending_projects.clear()


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS work_group_verdicts (
            key TEXT PRIMARY KEY, urgent INTEGER NOT NULL, why TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS work_not_today (
            item_id TEXT PRIMARY KEY, until REAL NOT NULL, told INTEGER NOT NULL DEFAULT 0, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS work_now_raised (item_id TEXT PRIMARY KEY, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS work_project_lines (key TEXT PRIMARY KEY, line TEXT NOT NULL, at REAL NOT NULL);
    """)
    if "later" not in {r[1] for r in c.execute("PRAGMA table_info(work_group_verdicts)")}:
        c.execute("ALTER TABLE work_group_verdicts ADD COLUMN later INTEGER NOT NULL DEFAULT 0")


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _name(bot_id: str | None) -> str:
    bot = config.BOTS_BY_ID.get(bot_id or "default")
    return bot.name if bot else "Zero"


def _title(item: dict) -> str:
    return _flat(item.get("short_act") or item.get("act") or item.get("short_title") or item.get("title"))


def key(item: dict) -> str:
    ""


    return _hash(item.get("id"), item.get("revision"), _flat(item.get("ask") or item.get("title")),
                 [m.get("id") for m in item.get("members") or []])




def _midnight(now: float, days: int = 0) -> float:
    ""

    start = datetime.fromtimestamp(now, home.zone()).replace(hour=0, minute=0, second=0, microsecond=0)
    return (start + timedelta(days=days)).timestamp()


def _day(now: float) -> tuple[float, float]:
    ""
    return _midnight(now), _midnight(now, 1)


def next_morning(now: float) -> float:
    ""
    return _day(now)[1]


def place(item: dict, verdict: dict | None, put_off: float | None, now: float) -> dict:
    ""
    start, end = _day(now)
    due = item.get("due_at")
    detail = _flat((verdict or {}).get("why"))
    urgent = bool(verdict and verdict.get("urgent")) and item.get("kind") not in DATE_ONLY


    asking = item.get("blocked_at") is not None and not (verdict or {}).get("later")

    def out(group, kind="", label="", words=""):
        return {"group": group, "why_kind": kind, "why_label": label,
                "why": " · ".join(x for x in (label, words) if x)}
    if put_off is not None and put_off > now:
        return out(WEEK, label="Tomorrow")
    pace = item.get("pace_at")
    if due is None and pace is not None:


        if pace < start:
            return out(WEEK, label="Behind pace", words=detail)
        if pace < _midnight(now, 1 + WEEK_DAYS):
            return out(WEEK, label=datetime.fromtimestamp(pace, home.zone()).strftime("%a"), words=detail)
        return out(WHENEVER)
    also = (item.get("reference") or {}).get("also_due")
    if due is not None and also and due < _midnight(now, 1 + WEEK_DAYS):


        return out(NOW if due < end else WEEK, "due", f"Due {_day_word(due, now)} or {_day_word(also, now)}",
                   detail or "Sources differ")
    if due is not None and due < end:
        return out(NOW, "due", "Overdue" if due < start else "Due today", detail)
    if urgent or asking:
        return out(NOW, "blocking", "Blocking",
                   detail or (f"{_name(item.get('bot_id'))} is stopped until you answer" if asking else ""))
    if due is not None and due < _midnight(now, 1 + WEEK_DAYS):
        label = "Tomorrow" if due < _midnight(now, 2) else datetime.fromtimestamp(due, home.zone()).strftime("%a")
        return out(WEEK, label=label)
    return out(WHENEVER)


def _day_word(at: float, now: float) -> str:
    ""
    day = datetime.fromtimestamp(at, home.zone())
    days = (day.date() - datetime.fromtimestamp(now, home.zone()).date()).days
    return {0: "today", 1: "tomorrow", -1: "yesterday"}.get(days) or day.strftime("%a" if 0 < days <= 6 else "%b %-d")


def _verdicts(keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    return {r["key"]: {"urgent": bool(r["urgent"]), "why": r["why"], "later": bool(r["later"])} for r in
            store.db().execute(f"SELECT key, urgent, why, later FROM work_group_verdicts WHERE key IN ({marks})", keys)}


def put_offs() -> dict[str, float]:
    return {r["item_id"]: r["until"] for r in store.db().execute("SELECT item_id, until FROM work_not_today")}


def add(items: list[dict], now: float | None = None, started: int | None = None) -> tuple[list[dict], int]:
    ""

    now = time.time() if now is None else now
    judged = [it for it in items if it.get("kind") not in DATE_ONLY]
    stored = _verdicts([key(it) for it in judged])
    off = put_offs()
    with store._lock:
        for it in items:
            k = key(it)
            it.update(place(it, stored.get(k), off.get(it["id"]), now))
            if started is None or started == _generation:
                if it.get("kind") not in DATE_ONLY and k not in stored:
                    _pending[k] = dict(it)
    return items, sum(it["group"] == NOW for it in items)




def clean_why(text) -> str:
    ""
    why = _flat(text).strip("\"'“”").rstrip(".")
    if not why or len(why) > WHY_MAX or len(why.split()) > 9 or _IDS.search(why):
        return ""
    return why[:1].upper() + why[1:]


def _waiting(item: dict) -> str:
    ""
    ref, parts = item.get("reference") or {}, [_flat(item.get("summary"))]
    try:
        if item.get("kind") == "approval" and ref.get("request_id") is not None:
            from . import consent
            r = consent.get_request(int(ref["request_id"])) or {}
            parts += [_flat(r.get("because")), f"It runs as the scheduled job {_flat(r.get('routine_name'))}."
                      if r.get("routine_name") else ""]
        elif item.get("kind") == "choices" and ref.get("feed_id") is not None:
            row = store.db().execute("SELECT body FROM feed WHERE id=?", (ref["feed_id"],)).fetchone()
            parts.append(_flat(json.loads(row["body"] or "{}").get("reasoning")) if row else "")
        elif item.get("kind") == "task" and ref.get("task_id") is not None:
            from . import board
            task = board.get_task(str(ref["task_id"])) or {}
            said = [c.get("body") for c in task.get("comments") or []] + \
                [e.get("payload") for e in task.get("events") or [] if e.get("kind") == "unblocked"]
            parts += [_flat(s)[:200] for s in said[-4:]]
    except Exception:
        log.info("could not read what a row is waiting for", exc_info=True)
    return " ".join(p for p in parts if p)[:800] or "(nothing more is recorded)"


def _due_words(item: dict, now: float) -> str:
    due = item.get("due_at")
    if due is None:
        return "no date"
    day = datetime.fromtimestamp(due, home.zone())
    days = (day.date() - datetime.fromtimestamp(now, home.zone()).date()).days
    rel = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days" if days > 1 else "already past"
    return f"{day.strftime('%A, %B')} {day.day} ({rel})"


async def judge(item: dict, ask=None, now: float | None = None) -> dict | None:
    ""
    from . import board, shortreply
    ask = ask or shortreply.ask_helper



    task_id = str((item.get("reference") or {}).get("task_id") or "")
    if shortreply.withheld(item.get("bot_id"), *(board.task_zips(task_id) if task_id else ())):
        return {"urgent": False, "why": "", "later": False}
    title = _title(item)
    context = _flat(item.get("context"))[:300] or "(none)"
    waiting = _waiting(item)
    prompt = PROMPT.format(first=who.first(), act=title[:300], context=context,
                           kind=KIND_WORDS.get(item.get("kind"), "something waiting on an answer"),
                           zip=_name(item.get("bot_id")), waiting=waiting,
                           due=_due_words(item, time.time() if now is None else now))
    from . import ownerrules
    prompt += ownerrules.block(ownerrules.URGENT, f"{title} {item.get('context') or ''} {waiting}",
                               item.get("bot_id"))
    try:
        with shortreply.reading_task(task_id, also=(item.get("bot_id"),)):
            answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=80), TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no verdict for a Needs You row (%s)", type(e).__name__)
        return None
    m = re.search(r"\{.*\}", answer or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None
    return {"urgent": data.get("stopped") is True or data.get("costs_today") is True, "why": clean_why(data.get("why")),
            "later": data.get("later") is True}


def save(k: str, verdict: dict, generation: int | None = None) -> None:
    with store._lock:
        if generation is not None and generation != _generation:
            return
        store.db().execute("INSERT OR REPLACE INTO work_group_verdicts(key, urgent, why, later, at) VALUES (?,?,?,?,?)",
                           (k, int(verdict["urgent"]), verdict["why"], int(bool(verdict.get("later"))), time.time()))
        store.db().commit()


async def write_due(ask=None, limit: int = PER_TICK) -> int:
    ""

    done, generation = 0, _generation
    for k, item in list(_pending.items())[:limit]:
        if generation != _generation:
            break
        _pending.pop(k, None)
        verdict = await judge(item, ask)
        if verdict is not None:
            await asyncio.to_thread(save, k, verdict, generation)
            done += 1
    for k, job in list(_pending_projects.items())[:max(0, limit - done)]:
        if generation != _generation:
            break
        _pending_projects.pop(k, None)
        line = await write_line(job, ask)
        if line is not None:
            await asyncio.to_thread(_save_line, k, line, generation)
            done += 1
    return done




class NotTodayError(Exception):
    ""


def not_today(item_id: str, items: list[dict], now: float | None = None, started: int | None = None) -> dict:
    ""


    now = time.time() if now is None else now
    item = next((it for it in items if it["id"] == item_id), None)
    if item is None:
        raise NotTodayError("That is no longer waiting on you.")
    if item.get("group") != NOW:
        raise NotTodayError("That isn't in Now, so there is nothing to put off.")
    until = next_morning(now)
    with store._lock:
        if started is not None and started != _generation:
            raise NotTodayError("That is no longer waiting on you.")
        old = store.db().execute("SELECT told FROM work_not_today WHERE item_id=?", (item_id,)).fetchone()
        tell = not (old and old["told"]) and (item.get("why_kind") == "blocking" or item.get("kind") in STOPS_A_ZIP)
        store.db().execute("INSERT INTO work_not_today(item_id, until, told, at) VALUES (?,?,?,?) ON CONFLICT(item_id) "
                           "DO UPDATE SET until=excluded.until, told=MAX(told, excluded.told), at=excluded.at",
                           (item_id, until, int(tell), now))
        store.db().commit()
    if tell:
        try:
            _tell_zip(item, started)
        except Exception:
            log.warning("could not tell %s that a row was put off", item.get("bot_id"), exc_info=True)
    return {"id": item_id, "until": until, **place(item, None, until, now)}


def _tell_zip(item: dict, started: int | None = None) -> None:
    ""



    from . import board, consent
    first = who.first()
    note = (f"{who.hub_note()} {first} put this off until tomorrow: {_title(item)[:200]}. It still waits for {first}. "
            f"Don't ask about it again today, and do what you can without it.")
    ref = item.get("reference") or {}
    task = ref.get("task_id")
    if task is None and ref.get("request_id") is not None:
        task = (consent.get_request(int(ref["request_id"])) or {}).get("task_id")
    if task:
        if started is None or started == _generation:
            board.comment(str(task), note)
    elif item.get("bot_id"):
        with store._lock:
            if started is None or started == _generation:
                store.add_card_note(item["bot_id"], note)




def _raise_line(titles: list[str]) -> str:
    if len(titles) == 1:
        return f"One thing needs you today: {titles[0]}."
    shown = titles[:3]
    rest = len(titles) - len(shown)
    listed = ", ".join(shown[:-1]) + f" and {shown[-1]}" if not rest else ", ".join(shown) + f" and {rest} more"
    return f"{len(titles)} things need you today: {listed}."


def newly_now(items: list[dict], now: float | None = None, started: int | None = None) -> list[dict]:
    ""


    now = time.time() if now is None else now
    current = [it for it in items if it.get("group") == NOW]
    with store._lock:
        if started is not None and started != _generation:
            return []
        c = store.db()
        seeded = store.get_setting("work_now_seeded", False)
        raised = {r["item_id"] for r in c.execute("SELECT item_id FROM work_now_raised")}
        fresh = [it for it in current if it["id"] not in raised and (not seeded or now - float(it.get("blocked_at") or it.get("at") or 0) > FRESH_SEC)]
        for it in fresh:
            c.execute("INSERT OR IGNORE INTO work_now_raised(item_id, at) VALUES (?,?)", (it["id"], now))
        c.execute("DELETE FROM work_now_raised WHERE at<?", (now - 60 * 86400,))
        c.commit()
        if not seeded:
            store.set_setting("work_now_seeded", True)
            return []
    return fresh


async def raise_new(items: list[dict] | None = None, now: float | None = None) -> str | None:
    ""

    from . import notices, watcher, work
    started = _generation
    if items is None:
        items = (await asyncio.to_thread(work.overview))["needs_you"]
    fresh = await asyncio.to_thread(newly_now, items, now, started)
    if not fresh:
        return None
    line = _raise_line([_title(it)[:80].rstrip(".") for it in fresh])
    hour = datetime.fromtimestamp(time.time() if now is None else now, home.zone()).hour
    brief = watcher._night() or (notices.enabled() and hour < 9 and not notices.brief_posted_today())
    with store._lock:
        if started != _generation:
            return None
        if brief:
            notices._brief_line(line)
            return line


    if started != _generation:
        return None
    subject = f"work-now:{_hash([it['id'] for it in fresh])}"
    await notices.nudge(subject, _name("default"), line, "default", kind="needs")


    with store._lock:
        if started != _generation:
            store.db().execute("DELETE FROM notices WHERE subject=?", (subject,))
            store.db().commit()
            return None
    return line




def _steps(mine: list[dict]) -> tuple[int | None, int | None]:
    ""
    tasks = [s for s in mine if s.get("kind") == "task"]
    if len(tasks) < 2:
        return None, None
    return sum(s.get("section") == "updates" for s in tasks), len(tasks)


def _facts(project: dict, mine: list[dict]) -> dict:
    from . import doing, workstate
    doing_now = []
    for s in mine:
        if s.get("section") == "in_progress":
            line = doing.written(str((s.get("reference") or {}).get("task_id") or "")) if s.get("kind") == "task" else None
            doing_now.append(line or _flat(s.get("title")))
    asks = [_title(s) for s in mine if s.get("section") == "needs_you" and workstate.visible(s)]
    news = [_flat(s.get("summary") or s.get("title")) for s in sorted(mine, key=lambda s: -float(s.get("at") or 0))
            if s.get("section") == "updates"][:2]
    return {"doing": doing_now[:3], "asks": asks[:4], "news": [n[:200] for n in news]}


def fallback_line(waiting: bool, working: bool, asks: int) -> str:
    if waiting:
        return "One thing here needs you." if asks == 1 else f"{asks} things here need you."
    return "I'm working on this. Nothing needs you." if working else "Nothing is moving here, and nothing needs you."


def clean_line(text) -> str | None:
    line = _flat(text).strip("\"'“”")
    if not line or len(line) > LINE_MAX or len(line.split()) > 30 or _IDS.search(line):
        return None
    return line if line[-1] in ".!?" else line + "."


def project_lines(projects: list[dict], sources: list[dict], started: int | None = None) -> list[dict]:
    ""

    from . import work
    by_project: dict[str, list[dict]] = {}
    for s in sources:
        by_project.setdefault(s["project_id"], []).append(s)
    keys = {p["id"]: _hash("project", p["id"], p.get("revision")) for p in projects if p["id"] != work.OTHER}
    stored = {}
    if keys:
        marks = ",".join("?" * len(keys))
        stored = {r["key"]: r["line"] for r in store.db().execute(
            f"SELECT key, line FROM work_project_lines WHERE key IN ({marks})", list(keys.values()))}
    for p in projects:
        mine = by_project.get(p["id"], [])
        facts = _facts(p, mine)
        waiting = bool(facts["asks"])
        done, total = _steps(mine)
        p.update(waiting_on_you=waiting, steps_done=done, steps_total=total,
                 standing=fallback_line(waiting, bool(facts["doing"]), len(facts["asks"])))
        k = keys.get(p["id"])
        if not k:
            continue
        if stored.get(k):
            p["standing"] = stored[k]
        elif k not in stored:
            with store._lock:
                if started is None or started == _generation:

                    _pending_projects[k] = {"project": {"title": p.get("title"), "date": p.get("date"),
                                                        "bot_id": p.get("owner_bot_id") or p.get("bot_id")},
                                            "facts": facts, "zips": sorted({str(s.get("bot_id") or "") for s in mine})}
    return projects


async def write_line(job: dict, ask=None) -> str | None:
    ""
    from . import shortreply
    ask = ask or shortreply.ask_helper
    from . import privatewords
    p, facts = job["project"], {k: privatewords.keep(v) for k, v in job["facts"].items()}
    name = _name(p.get("bot_id"))
    date = datetime.fromtimestamp(p["date"], home.zone()).strftime("%A, %B %-d") if p.get("date") else "none"
    prompt = PROJECT_PROMPT.format(zip=name, first=who.first(), title=_flat(p.get("title"))[:200],
                                   doing="; ".join(facts["doing"]) or "nothing right now",
                                   asks="; ".join(facts["asks"]) or "nothing", news="; ".join(facts["news"]) or "none",
                                   date=date)
    from . import ownerrules
    prompt += ownerrules.block(ownerrules.UPDATE, f"{p.get('title') or ''} {' '.join(facts['asks'])} {' '.join(facts['news'])}",
                               p.get("bot_id"), answered=False)
    try:
        with shortreply.reading((p.get('bot_id'),) + tuple(job.get('zips') or ())):
            answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=PROJECT_SYSTEM, max_tokens=80),
                                            TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no status sentence for a project (%s)", type(e).__name__)
        return None
    line = clean_line(answer)
    return "" if line is None or re.search(rf"\b{re.escape(name)}\b", line) else line


def _save_line(k: str, line: str, generation: int | None = None) -> None:
    with store._lock:
        if generation is not None and generation != _generation:
            return
        store.db().execute("INSERT OR REPLACE INTO work_project_lines(key, line, at) VALUES (?,?,?)", (k, line, time.time()))
        store.db().commit()


async def run() -> None:
    last_raise = 0.0
    while True:
        await asyncio.sleep(EVERY_SEC)
        if not ENABLED:
            continue
        try:
            if _pending or _pending_projects:
                await write_due()
            if time.time() - last_raise >= RAISE_EVERY_SEC:
                last_raise = time.time()
                await raise_new()
        except Exception:
            log.warning("could not sort or raise the Needs You rows", exc_info=True)
