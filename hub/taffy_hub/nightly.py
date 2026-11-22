""


























from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

from . import config, home, privatewords, shortreply, store, who

log = logging.getLogger("taffy.nightly")

ENABLED = False
SKIP_ZIPS = config.PRIVATE_ZIPS
SETTLE_SEC = 300
STREAK_DAYS = 3
STANDING = "night_standing"
WRITE_SEC = 60
WORTH_SEC = 30
LINE_WORDS = 40
HEADING = "What didn't go as planned:"


WHY = {"connection": "I couldn't reach my AI service", "limit": "my AI plan was at its limit",
       "restart": "I restarted while it was running"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS night_misses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,    -- one failure is one row: job:<zip>:<job>:<when it first failed>, wake:<id>, task:<id>:<why>
    kind TEXT NOT NULL,          -- job, once (a one-time job), brief, wake or task
    bot_id TEXT NOT NULL,
    what TEXT NOT NULL,          -- what did not happen, in plain words
    why TEXT NOT NULL DEFAULT '',     -- a WHY key, or plain words
    can_do TEXT NOT NULL DEFAULT '',  -- what the person can do, when there is something
    due_at REAL,                 -- when it was meant to happen
    at REAL NOT NULL,            -- when the retries ended
    told_at REAL
);
CREATE INDEX IF NOT EXISTS night_misses_told ON night_misses (told_at, at);
"""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)




def add(c: sqlite3.Connection, key: str, kind: str, bot_id: str, what: str, why: str = "", can_do: str = "",
        due_at: float | None = None, at: float | None = None) -> bool:
    ""
    what = " ".join(str(what or "").split())[:200]
    if not what or config.is_sandbox(bot_id or "default"):
        return False
    return bool(c.execute("INSERT OR IGNORE INTO night_misses (key, kind, bot_id, what, why, can_do, due_at, at) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (key, kind, bot_id or "default", what, why, can_do, due_at,
                           time.time() if at is None else at)).rowcount)


def miss(key: str, kind: str, bot_id: str, what: str, why: str = "", can_do: str = "", due_at: float | None = None,
         at: float | None = None) -> bool:
    with store._lock:
        new = add(store.db(), key, kind, bot_id, what, why, can_do, due_at, at)
        store.db().commit()
    return new


def waiting() -> list[dict]:
    return [dict(r) for r in store.db().execute("SELECT * FROM night_misses WHERE told_at IS NULL ORDER BY at, id")]


def _claim() -> list[dict]:
    ""
    with store._lock:
        rows = waiting()
        if rows:
            store.db().execute(f"UPDATE night_misses SET told_at=? WHERE id IN ({','.join('?' * len(rows))})",
                               (time.time(), *[r["id"] for r in rows]))
            store.db().commit()
    return rows


def _release(rows: list[dict]) -> None:
    ""
    if rows:
        with store._lock:
            store.db().execute(f"UPDATE night_misses SET told_at=NULL WHERE id IN ({','.join('?' * len(rows))})",
                               [r["id"] for r in rows])
            store.db().commit()




def _day(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, home.TZ).date().isoformat()


def _in_a_row(days: list[str]) -> int:
    ""
    n, last = 0, None
    for d in sorted(set(days), reverse=True):
        day = date.fromisoformat(d)
        if last is not None and last - day != timedelta(days=1):
            break
        n, last = n + 1, day
    return n


def _record(saved: dict, key: str, name: str, detail: str, day: str) -> None:
    entry = saved.setdefault(key, {"days": []})
    entry.update(name=name, detail=detail, days=sorted({*entry["days"], day})[-14:])


def note_checks(results: dict[str, dict], now: float | None = None) -> None:
    ""
    saved = store.get_setting(STANDING, {}) or {}
    for key, r in results.items():
        if r.get("state") == "failed":
            _record(saved, key, r.get("name") or key, r.get("detail") or "", _day(now))
        elif r.get("state") == "ok":
            saved.pop(key, None)
    store.set_setting(STANDING, saved)


def selftest_results() -> dict | None:
    ""
    try:
        return json.loads((config.HUB_DIR.parent / "state" / "self-test" / "state.json").read_text()).get("last_run")
    except (OSError, ValueError, AttributeError):
        return None


def note_selftest(run: dict | None = None) -> None:
    ""
    run = selftest_results() if run is None else run
    if not isinstance(run, dict) or not run.get("results"):
        return
    try:
        day = datetime.fromisoformat(str(run.get("at"))).astimezone(home.TZ).date().isoformat()
    except (TypeError, ValueError):
        return
    saved = store.get_setting(STANDING, {}) or {}
    for r in run["results"]:
        key = f"selftest:{r.get('name')}"
        if r.get("status") == "fail":
            _record(saved, key, f"The nightly self-test's {r.get('name')} check", str(r.get("detail") or ""), day)
        elif r.get("status") == "pass":
            saved.pop(key, None)
    store.set_setting(STANDING, saved)


def _standing_due() -> dict[str, dict]:
    ""
    out = {}
    for key, e in (store.get_setting(STANDING, {}) or {}).items():
        enough = 1 if key.startswith("selftest:") else STREAK_DAYS
        if _in_a_row(e.get("days") or []) >= enough and e.get("said") != e.get("name"):
            out[key] = e
    return out


def _mark_said(keys) -> None:
    saved = store.get_setting(STANDING, {}) or {}
    for key in keys:
        if key in saved:
            saved[key]["said"] = saved[key].get("name")
    store.set_setting(STANDING, saved)




async def ask_model(prompt: str, timeout: float, max_tokens: int = 500) -> str:
    ""
    from . import shortreply
    return await shortreply.ask_helper(prompt, timeout=timeout, max_tokens=max_tokens, origin="nightly",
                                       system="You write short, plain messages for a busy person. Answer with JSON only.")


def _json(text: str | None) -> dict | None:
    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        out = json.loads(found.group(0)) if found else None
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def clock(ts: float | None) -> str:
    if not ts:
        return ""
    when = datetime.fromtimestamp(ts, home.TZ)
    hour = when.hour % 12 or 12
    return f"{hour}{'' if when.minute == 0 else f':{when.minute:02d}'} {'a.m.' if when.hour < 12 else 'p.m.'}"


def _why(row: dict) -> str:
    return WHY.get(row.get("why") or "", row.get("why") or "")


def plain(row: dict) -> str:
    ""
    if shortreply.withheld(row["bot_id"]):
        name = config.BOTS_BY_ID[row["bot_id"]].name if row["bot_id"] in config.BOTS_BY_ID else "A helper"
        return f"Some of {name}'s work didn't happen{f' at {clock(row.get('due_at'))}' if row.get('due_at') else ''}".rstrip(".") + "."
    when = f" at {clock(row['due_at'])}" if row.get("due_at") else ""
    why = _why(row)
    said = f"{row['what']} didn't happen{when}".rstrip(".") + "." + (f" {why[0].upper()}{why[1:]}." if why else "")
    return f"{said} {row['can_do']}".strip()


WRITE = """Write the lines {first} reads in the morning about what did not happen. {first} reads on a phone. Speak as \
Zero, in the first person, like a teammate texting.

What did not happen:
{items}

Write one plain sentence or two for each, saying what did not happen, why, and what {first} can do when there is \
something to do. Example: "Your 8 a.m. reminder to call the dentist didn't go out. I was offline until 10." Put several \
items with one cause into one line. For a check that keeps failing, say what it means for {first} in everyday words. \
Keep the names {first} would recognize. Use no ids, tool names, error text or words like server, job id, cron or \
provider. Don't apologize, and ask nothing.

Answer with this JSON and nothing else: {{"lines": [{{"ids": [1], "line": "..."}}]}}"""


def _fact(n: int, row: dict) -> str:
    when = datetime.fromtimestamp(row["due_at"], home.TZ).strftime("%A %-I:%M %p") if row.get("due_at") else "unknown"
    return (f"{n}. {row['what']} (a {row['kind']}). It was due {when}. Why: {_why(row) or 'not known'}. "
            f"{('What ' + who.first() + ' can do: ' + row['can_do']) if row.get('can_do') else ''}").strip()


def _check_fact(n: int, e: dict) -> str:
    days = _in_a_row(e.get("days") or [])
    return f"{n}. {e['name']} has failed {days} morning{'s' if days != 1 else ''} in a row. What it reported: {e.get('detail') or 'no detail'}."


def _check_line(e: dict) -> str:
    days = _in_a_row(e.get("days") or [])
    return f"{e['name']} failed last night." if days == 1 else f"{e['name']} has failed {days} mornings in a row."


async def _write(rows: list[dict], checks: dict[str, dict]) -> list[str]:
    ""
    from . import consent, gotdone, ownerrules

    readable = privatewords.keep((r for r in rows if not shortreply.withheld(r["bot_id"])), lambda r: _fact(0, r))
    entries = privatewords.keep(checks.values(), lambda e: _check_fact(0, e))
    held = [e for e in checks.values() if e not in entries]
    numbered = {i: r for i, r in enumerate(readable, 1)}
    offset = len(numbered)
    facts = [_fact(i, r) for i, r in numbered.items()] + [_check_fact(offset + i, e) for i, e in enumerate(entries, 1)]
    lines, done = [], set()
    if facts:
        prompt = WRITE.format(first=who.first(), items=consent.scrub("\n".join(facts))) \
            + consent.scrub(ownerrules.block(ownerrules.UPDATE, "\n".join(facts), answered=False))
        try:
            with shortreply.reading(tuple({r['bot_id'] for r in readable})):
                out = _json(await asyncio.wait_for(ask_model(prompt, WRITE_SEC), WRITE_SEC + 5)) or {}
        except Exception as e:
            log.info("no words from the model for what did not happen (%s)", type(e).__name__)
            out = {}
        for item in out.get("lines") or []:
            ids = [i for i in (item.get("ids") or []) if isinstance(i, int) and 1 <= i <= len(facts) and i not in done] \
                if isinstance(item, dict) else []
            line = gotdone.clean_line(item.get("line"), LINE_WORDS) if ids else ""
            if line:
                done.update(ids)
                lines.append(line)
    lines += [plain(r) for i, r in numbered.items() if i not in done]
    lines += [_check_line(e) for i, e in enumerate(entries, offset + 1) if i not in done] + [_check_line(e) for e in held]
    lines += [plain(r) for r in rows if r not in readable]
    return lines


async def section() -> str:
    ""
    rows, checks = _claim(), _standing_due()
    if not rows and not checks:
        return ""
    try:
        lines = await _write(rows, checks)
    except Exception:
        _release(rows)
        raise
    _mark_said(checks)
    return lines[0] if len(lines) == 1 else HEADING + "\n" + "\n".join(f"- {line}" for line in lines)




WORTH = """A scheduled job of {first}'s could not run because of a fault on our side, and it can be tried again now. \
Decide whether doing it now is still worth it.

The job: {name}
How often it runs: {when}
It was due: {due}
It is now: {now}
What it does: {prompt}

A report about a moment that has passed is no longer worth it: a morning brief is worth sending at 9 a.m. and not at \
4 p.m. A one-time job {first} asked for is usually still worth doing late, unless what it was for has already passed.

Answer with this JSON and nothing else: {{"worth": true or false}}"""


async def still_worth(bot_id: str, name: str, when: str, due_at: float, prompt: str, now: float) -> bool | None:
    ""
    from . import consent, ownerrules
    if shortreply.withheld(bot_id):
        return None
    text = WORTH.format(first=who.first(), name=name, when=when or "once",
                        due=datetime.fromtimestamp(due_at, home.TZ).strftime("%A %-I:%M %p"),
                        now=datetime.fromtimestamp(now, home.TZ).strftime("%A %-I:%M %p"),
                        prompt=consent.scrub(" ".join(str(prompt or "").split())[:600]) or "(not recorded)") \
        + consent.scrub(ownerrules.block(ownerrules.FOLLOWUP, f"{name} {prompt}", bot_id, always=False, answered=False))
    try:
        with shortreply.reading_text(f"{name}\n{prompt}", (bot_id,)):
            out = _json(await asyncio.wait_for(ask_model(text, WORTH_SEC, 60), WORTH_SEC + 5))
    except Exception as e:
        log.info("no answer on whether a failed job is still worth doing (%s)", type(e).__name__)
        return None
    worth = (out or {}).get("worth")
    return worth if isinstance(worth, bool) else None




async def _post() -> bool:
    from . import notify, watcher
    text = await section()
    if not text:
        return False
    name = notify.sender("default")[1]
    fid = store.add_feed("message", "default", None, name, text, written_by="model", written_for="person")
    first = text.split("\n", 2)[1].removeprefix("- ") if text.startswith(HEADING) else text
    await watcher.push(name, first, "default", kind="progress", exact=True, extra={"item_id": fid},
                       judged={"buzz": True, "source": "hub", "reason": "did not happen", "feed_id": fid})
    return True


async def tick(now: float | None = None) -> bool:
    ""

    from . import gotdone, watcher
    if not ENABLED or watcher._night():
        return False
    now = time.time() if now is None else now
    rows, checks = waiting(), _standing_due()
    if not rows and not checks:
        return False
    local = datetime.fromtimestamp(now, home.TZ)
    if local.hour < gotdone.MORNING_LAST_HOUR or gotdone.due(local) == "morning":
        return False
    running = gotdone._recapping[0]
    if (running is not None and not running.done()) or (rows and now - max(r["at"] for r in rows) < SETTLE_SEC):
        return False
    return await _post()
