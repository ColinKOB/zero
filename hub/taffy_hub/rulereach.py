""


















from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time

from . import config, facts, privatewords, shortreply, store, who

log = logging.getLogger("taffy.rulereach")

ENABLED = False
ALWAYS, WORK = "always", "work"
RULE_KINDS = ("preference", "decision")
BATCH = 40
EVERY_SEC = 30
IDLE_SEC = 300
READ_TIMEOUT = 45
PICK_TIMEOUT = 20
PICK_MAX = 25
INSTRUCTIONS_MAX = 3000
RULE_MAX = 300
KEEP_DAYS = 60
LATE_SEC = 86400

_waking: dict[str, list[int]] = {}
_flight: dict[str, asyncio.Task] = {}

SYSTEM = "You sort a person's saved rules for a team of AI helpers. Reply with JSON only."
READ = """These are rules {first} gave a team of AI helpers. Choose the rules that apply to everything any helper does \
for {first}, whatever the job: how to talk or write to {first}, how to treat {first}, what never to do, when to stop \
and ask, and who {first} is.

Leave out a rule that is about one subject, project, person, product, site, class, helper's job or past event, and a \
one-time permission or decision, however firmly it is worded. Those reach the work they bear on some other way.

Reply with JSON only: {{"always": [the numbers of the rules that apply to everything]}}

Rules:
{rules}"""
PICK = """A helper working for {first} is about to do the work below. Choose the saved rules from {first} that this \
work must follow, or that would change how it is done. A rule counts when breaking it during this work would annoy \
{first} or make the result wrong. Leave out rules about other subjects.

Choose at most {most}, the ones that matter most first. Choose none when none bear on it.

Reply with JSON only: {{"rules": [numbers]}}

The work:
{work}

Rules:
{rules}"""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
CREATE TABLE IF NOT EXISTS rule_reach (fact_id INTEGER PRIMARY KEY, digest TEXT NOT NULL, reach TEXT NOT NULL,
    at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rule_picks (work TEXT PRIMARY KEY, bot_id TEXT NOT NULL, seen TEXT NOT NULL,
    picked TEXT NOT NULL, at INTEGER NOT NULL);
""")


def _digest(text: str) -> str:
    return hashlib.sha256(" ".join(str(text or "").split()).encode()).hexdigest()[:16]


def _private(bot_id: str) -> bool:
    ""
    return shortreply.withheld(bot_id)


def _sendable(f: dict) -> bool:
    ""


    return (not _private(f["bot_id"]) and not config.is_sandbox(f["bot_id"])
            and not privatewords.holds(f"{f.get('subject') or ''} {f.get('text') or ''}"))


def _live_rules(bot_id: str | None = None) -> list[dict]:
    ""
    where, args = facts._visible(bot_id)
    holds, now_arg = facts.live()
    marks = ",".join("?" * len(RULE_KINDS))
    return [dict(r) for r in facts._db().execute(
        f"SELECT * FROM facts WHERE {holds} AND {where} AND kind IN ({marks}) ORDER BY id DESC",
        now_arg + args + RULE_KINDS).fetchall()]


def reach_of(rows: list[dict]) -> dict[int, str]:
    ""
    ids = [f["id"] for f in rows]
    if not ids:
        return {}
    found: dict[int, tuple[str, str]] = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for r in store.db().execute(f"SELECT fact_id, digest, reach FROM rule_reach WHERE fact_id IN "
                                    f"({','.join('?' * len(part))})", part):
            found[r["fact_id"]] = (r["digest"], r["reach"])
    return {f["id"]: found[f["id"]][1] for f in rows
            if f["id"] in found and found[f["id"]][0] == _digest(f["text"])}


def _lines(rows: list[dict]) -> str:
    return "\n".join(f"{f['id']}. {' '.join(str(f['text']).split())[:RULE_MAX]}" for f in rows)


def _numbers(answer: str, key: str, allowed: set[int]) -> list[int] | None:
    ""

    found = re.search(rf'"{key}"\s*:\s*\[([^\]]*)\]', str(answer or ""))
    if not found:
        return None
    return list(dict.fromkeys(n for n in map(int, re.findall(r"\d+", found.group(1))) if n in allowed))


async def _ask(prompt: str, timeout: float, tokens: int) -> str:
    from . import shortreply
    return await shortreply.ask_helper(prompt, timeout=timeout, system=SYSTEM, max_tokens=tokens, effort="low",
                                       origin="rulereach")




def unread() -> list[dict]:
    ""
    rows = [f for f in _live_rules() if _sendable(f)]
    known = reach_of(rows)
    return [f for f in rows if f["id"] not in known]


async def read_some(limit: int = BATCH) -> int:
    ""
    rows = (await asyncio.to_thread(unread))[:limit]
    if not rows:
        return 0
    try:
        with shortreply.reading(tuple({str(f.get('bot_id') or '') for f in rows})):
            answer = await _ask(READ.format(first=who.first(), rules=_lines(rows)), READ_TIMEOUT, 600)
    except Exception as e:
        log.warning("could not sort %d saved rules: %s", len(rows), type(e).__name__)
        return 0
    always = _numbers(answer, "always", {f["id"] for f in rows})
    if always is None:
        log.warning("the answer about %d saved rules was not usable", len(rows))
        return 0
    t = int(time.time())
    with store._lock:
        c = store.db()
        c.executemany("INSERT OR REPLACE INTO rule_reach (fact_id, digest, reach, at) VALUES (?,?,?,?)",
                      [(f["id"], _digest(f["text"]), ALWAYS if f["id"] in always else WORK, t) for f in rows])
        c.commit()
    return len(rows)


def late() -> int:
    ""
    cutoff = time.time() - LATE_SEC
    return sum(1 for f in unread() if float(f.get("created_at") or 0) < cutoff)


async def run() -> None:
    await asyncio.sleep(EVERY_SEC)
    while True:
        read = 0
        try:
            if ENABLED:
                read = await read_some()
        except Exception:
            log.warning("sorting the saved rules failed", exc_info=True)
        await asyncio.sleep(EVERY_SEC if read else IDLE_SEC)




def _work(bot_id: str, session_id: str, task: str, instructions: str) -> tuple[str, str]:
    ""

    if task:
        from . import board
        try:
            t = board.get_task(task) or {}
        except (OSError, sqlite3.Error):
            t = {}
        return f"task:{task}", "\n".join(str(t.get(k) or "") for k in ("title", "body")).strip() or instructions
    job = re.fullmatch(r"cron_(.+)_\d{8}_\d{6}", session_id or "")
    if job:
        from . import watcher
        home = config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id
        prompt = str(watcher._jobs_of(home / "cron" / "jobs.json").get(job.group(1), {}).get("prompt") or "")
        return f"job:{bot_id}:{job.group(1)}", prompt.strip() or instructions
    return f"text:{bot_id}:{_digest(instructions)}", instructions


def _stored(key: str) -> tuple[set[int], list[int]]:
    row = store.db().execute("SELECT seen, picked FROM rule_picks WHERE work=?", (key,)).fetchone()
    return (set(json.loads(row["seen"])), json.loads(row["picked"])) if row else (set(), [])


def _candidates(bot_id: str) -> list[dict]:
    ""
    rows = [f for f in _live_rules(bot_id) if _sendable(f)]
    known = reach_of(rows)
    return [f for f in rows if known.get(f["id"]) != ALWAYS]


async def _pick(bot_id: str, key: str, text: str, task: str) -> list[int]:
    rows = await asyncio.to_thread(_candidates, bot_id)
    live = {f["id"] for f in rows}
    seen, picked = await asyncio.to_thread(_stored, key)
    new = [f for f in rows if f["id"] not in seen]
    if new and ENABLED:
        try:



            with shortreply.reading_task(task, also=(bot_id,)):
                answer = await _ask(PICK.format(first=who.first(), most=PICK_MAX,
                                                work=privatewords.leave_out(text[:INSTRUCTIONS_MAX]) or "(none)",
                                                rules=_lines(new)), PICK_TIMEOUT, 300)
        except Exception as e:
            log.warning("could not pick the rules for %s: %s", key.split(":")[0], type(e).__name__)
            answer = None
        got = _numbers(answer, "rules", {f["id"] for f in new}) if answer is not None else None
        if got is None and answer is not None:
            log.warning("the rules picked for %s came back unusable", key.split(":")[0])
        if got:
            picked = [*got, *(p for p in picked if p not in got)]
            t = int(time.time())
            with store._lock:
                c = store.db()
                c.execute("INSERT OR REPLACE INTO rule_picks (work, bot_id, seen, picked, at) VALUES (?,?,?,?,?)",
                          (key, bot_id, json.dumps(sorted(seen | {f["id"] for f in new})), json.dumps(picked), t))
                c.execute("DELETE FROM rule_picks WHERE at < ?", (t - KEEP_DAYS * 86400,))
                c.commit()
    return [p for p in picked if p in live][:PICK_MAX]


async def picks(bot_id: str, key: str, text: str, task: str = "") -> list[int]:
    ""

    if _private(bot_id) or config.is_sandbox(bot_id) or not str(text or "").strip():
        return []
    running = _flight.get(key)
    if running is None:
        running = _flight[key] = asyncio.create_task(_pick(bot_id, key, text, task))
        running.add_done_callback(lambda _: _flight.pop(key, None))
    return await asyncio.shield(running)


async def prepare_wake(bot_id: str, note: str) -> None:
    ""
    try:
        got = await picks(bot_id, f"text:{bot_id}:{_digest(note)}", note)
    except Exception:
        log.warning("could not pick the rules for a wake", exc_info=True)
        return
    if got:
        _waking[bot_id] = got


async def for_turn(bot_id: str, session_id: str, first: bool, work: bool, task: str = "", asked: str = "",
                   instructions: str = "") -> list[int]:
    ""
    waiting = _waking.pop(bot_id, [])
    if asked:
        key, text = f"text:{bot_id}:{_digest(asked)}", asked
    elif work and first:
        key, text = await asyncio.to_thread(_work, bot_id, session_id, task, instructions)
    else:
        return waiting
    return list(dict.fromkeys([*waiting, *await picks(bot_id, key, text, task if work and first and not asked else "")]))
