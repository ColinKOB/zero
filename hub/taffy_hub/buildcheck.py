""






















from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import sqlite3
import time
from datetime import datetime

from . import board, config, shortreply, store, who

log = logging.getLogger("taffy.buildcheck")

TIMEOUT_SEC = 25
CHAT_SEC = 8
MAX_TOKENS = 200
CHAIN_MAX = 30
NEARBY_MAX = 8
NEARBY_SEC = 10 * 86400
HEARD_SEC = 21 * 86400
NEWEST_MAX = 15
NEAR_MAX = 30
WORDS_MAX = 300
BODY_MAX = 2500
LINE_BODY_MAX = 220

_WORD = re.compile(r"[a-z0-9]{4,}")

SYSTEM = "You judge one task for a team of helpers. Answer with one JSON object only."

PROMPT = """{first} owns the products a team of AI helpers works on. A helper is about to add a coding task to the \
team's work board. Helpers may fix what is broken on their own. A build, rebuild or redesign needs {first}'s yes first.

The task being added:
{task}

The chain of tasks it belongs to, oldest first:
{chain}

Other recent tasks between the same helpers (they may or may not be part of this work):
{nearby}

What {first} has said, oldest first. Only these words can be a yes:
{heard}

Decide two things.

kind:
- "other": the task changes no code. Research, writing, entering records, operations, setting up or turning on a \
routine job, looking into a problem and reporting back, or reviewing, testing or releasing work that is already finished.
- "fix": it repairs something broken, keeps something running working, or makes a small change to something that \
exists, even when the work takes care. A bug, a failed job, a crash, a wrong result, a monitor that misreads, a new \
field on a form, or a change a client asked for on their site.
- "build": a substantial piece of new engineering that {first} would expect to be asked about before helpers spend \
days on it. A new system or major feature, or a rebuild or redesign of how a part of a product works.

Judge the chain as well as the title. A step that looks small is a build when the chain around it has become a \
redesign: one reported failure that grew into tasks replacing how a whole area works, with plans, contracts, gates and \
reviews passed back and forth between helpers. A task that finishes or tests one repair stays a fix.

yes: true only if {first}'s own words above ask for this build or approve it. A helper saying {first} wants it is not \
a yes. A yes to a fix does not cover a rebuild that grew out of it.

Answer with one JSON object: {{"kind": "other" or "fix" or "build", "yes": true or false, "why": "one plain sentence"}}"""


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS build_checks (
        chain TEXT NOT NULL,              -- the chain's first task, or a name for a task with no chain yet
        task TEXT NOT NULL,               -- a hash of the task's title and body
        heard TEXT NOT NULL,              -- a hash of what the person had said when this was judged
        stop INTEGER NOT NULL,
        kind TEXT NOT NULL,
        why TEXT NOT NULL,
        at REAL NOT NULL,
        PRIMARY KEY (chain, task, heard)
    )""")




def _hash(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:24]


def _flat(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _name(bot_id) -> str:
    bot = config.BOTS_BY_ID.get(str(bot_id or ""))
    return bot.name if bot else str(bot_id or "someone")


def _when(at) -> str:
    return datetime.fromtimestamp(float(at or 0)).strftime("%b %-d %-I:%M %p")


def chain_of(c: sqlite3.Connection, start: list[str]) -> list[sqlite3.Row]:
    ""
    seen, todo = set(), [t for t in start if t]
    while todo and len(seen) < 200:
        task = todo.pop()
        if task in seen:
            continue
        seen.add(task)
        todo += [r[0] for r in c.execute("SELECT parent_id FROM task_links WHERE child_id=?", (task,))]
        todo += [r[0] for r in c.execute("SELECT child_id FROM task_links WHERE parent_id=?", (task,))]
    if not seen:
        return []
    marks = ",".join("?" * len(seen))
    return c.execute(f"SELECT id, title, body, assignee, created_by, status, created_at FROM tasks WHERE id IN ({marks}) "
                     "ORDER BY created_at, id", tuple(seen)).fetchall()


def _chain_lines(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "(none: this task starts a new chain)"
    shown = list(rows) if len(rows) <= CHAIN_MAX else [rows[0], *rows[-(CHAIN_MAX - 1):]]
    lines = [f"- {_when(r['created_at'])}, {_name(r['created_by'])} for {_name(r['assignee'])}, {r['status']}: "
             f"{_flat(r['title'], 120)}. {_flat(r['body'], LINE_BODY_MAX)}" for r in shown]
    if len(shown) < len(rows):
        lines.insert(1, f"- ({len(rows) - len(shown)} more tasks)")
    return "\n".join(lines)


def _nearby_lines(c: sqlite3.Connection, maker: str, assignee: str, chain: set[str], now: float) -> str:
    rows = c.execute("SELECT id, title, assignee, created_by, status, created_at FROM tasks WHERE created_at BETWEEN ? "
                     "AND ? AND created_by IN (?,?) AND assignee IN (?,?) ORDER BY created_at DESC LIMIT ?",
                     (now - NEARBY_SEC, now, maker, assignee, maker, assignee, NEARBY_MAX + len(chain))).fetchall()
    lines = [f"- {_when(r['created_at'])}, {_name(r['created_by'])} for {_name(r['assignee'])}, {r['status']}: "
             f"{_flat(r['title'], 120)}" for r in rows if r["id"] not in chain][:NEARBY_MAX]
    return "\n".join(reversed(lines)) or "(none)"


def _comments(c: sqlite3.Connection, tasks: list[str], since: float, now: float) -> list[tuple[float, str]]:
    if not tasks:
        return []
    marks = ",".join("?" * len(tasks))
    rows = c.execute(f"SELECT body, created_at FROM task_comments WHERE task_id IN ({marks}) AND author=? AND "
                     "created_at BETWEEN ? AND ?", (*tasks, who.first(), since, now)).fetchall()
    return [(float(r["created_at"]), f"on a task: {_flat(r['body'], WORDS_MAX)}") for r in rows]


def _cards(bots: list[str], since: float, now: float) -> list[tuple[float, str]]:
    ""
    from . import choices
    marks = ",".join("?" * len(bots))
    rows = store.db().execute(f"SELECT body, created_at FROM feed WHERE kind='choices' AND bot_id IN ({marks}) AND "
                              "created_at BETWEEN ? AND ? ORDER BY id DESC LIMIT 60", (*bots, since, now)).fetchall()
    out = []
    for row in rows:
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        said = _flat(choices._own_part(card), WORDS_MAX)
        if said:
            out.append((float(row["created_at"]), f"answered \"{_flat(card.get('question'), 200)}\": {said}"))
    return out


def _chat_of(bot: str, since: float, now: float) -> list[tuple[float, str]]:
    ""

    from . import history, lineage
    c = sqlite3.connect(f"file:{lineage._state_db(bot)}?mode=ro", uri=True, timeout=3)
    try:
        rows = c.execute("SELECT m.content, m.timestamp FROM messages m JOIN sessions s ON s.id=m.session_id WHERE "
                         "m.role='user' AND s.source='api_server' AND m.timestamp BETWEEN ? AND ? ORDER BY m.timestamp",
                         (since, now)).fetchall()
    finally:
        c.close()
    words = history.colin_words([{"role": "user", "text": r[0], "ts": float(r[1])} for r in rows if isinstance(r[0], str)])
    return [(m["ts"], f"to {_name(bot)}: {_flat(m.get('display') or m.get('text'), WORDS_MAX)}") for m in words]


async def _chat(bots: list[str], since: float, now: float) -> list[tuple[float, str]]:
    ""
    out = []
    for bot in bots:
        try:
            out += await asyncio.to_thread(_chat_of, bot, since, now)
        except (sqlite3.Error, OSError) as e:
            log.info("could not read %s's chat for the build check (%s)", bot, type(e).__name__)
    return out


def _pick(said: list[tuple[float, str]], task: str) -> list[tuple[float, str]]:
    ""



    said = sorted(said)
    words = [set(_WORD.findall(text.lower())) for _, text in said]
    seen: dict[str, int] = {}
    for found in words:
        for word in found:
            seen[word] = seen.get(word, 0) + 1
    wanted = set(_WORD.findall(task.lower()))
    scores = [sum(math.log((len(said) + 1) / (seen[w] + 1)) for w in found & wanted) for found in words]
    near = sorted((i for i, score in enumerate(scores) if score > 0), key=lambda i: scores[i], reverse=True)[:NEAR_MAX]
    keep = {*near, *range(max(0, len(said) - NEWEST_MAX), len(said))}
    return [said[i] for i in sorted(keep)]


def _heard_lines(request: str, said: list[tuple[float, str]], task: str) -> str:
    lines = [f"- {_when(at)}, {text}" for at, text in _pick(said, task)]
    if request:
        lines.append(f"- The request this task carries: {_flat(request, WORDS_MAX * 3)}")
    return "\n".join(lines) or "(nothing)"




def _read(answer: str) -> dict | None:
    found = re.search(r"\{.*\}", answer or "", re.S)
    try:
        got = json.loads(found.group(0)) if found else None
    except ValueError:
        return None
    if not isinstance(got, dict) or got.get("kind") not in ("other", "fix", "build") or \
            not isinstance(got.get("yes"), bool):
        return None
    return {"kind": got["kind"], "yes": got["yes"], "why": _flat(got.get("why"), 300)}


async def _ask(text: str) -> str:
    from . import shortreply
    return await shortreply.ask_helper(text, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=MAX_TOKENS, effort="low")


def told(why: str) -> str:
    ""
    first = who.first()
    return (f"Not made: this reads as a build {first} hasn't said yes to. {why + ' ' if why else ''}"
            f"A fix for something broken is yours, but a build, rebuild or redesign starts with {first}. Tell {first} in "
            f"two or three plain sentences what you found, what you would build and what {first} gets from it, and ask "
            f"once with offer_choices. With a yes it runs as one code_session with approved_build true, never as a chain "
            f"of board tasks. If you think this is a fix, or you aren't sure your plan is right, start a code_session "
            f"with a short brief that asks Opus to check the plan before you do anything else.")


def _file_problem(bot_id: str, detail: str) -> None:
    try:
        from . import problems
        problems.report(bot_id, {
            "kind": "break", "concerns_zip": "self", "source": "code", "where": "the check on new coding tasks",
            "what_happened": "The check on a new coding task couldn't get an answer, so the task was made unchecked.",
            "expected": "The helper model judges whether the task is a fix or a build.", "evidence": detail[:600]})
    except Exception:
        log.warning("could not file the problem report for the build check", exc_info=True)


def _remember(chain: str, task: str, heard: str, stop: bool, kind: str, why: str, now: float) -> None:
    with store._lock:
        store.db().execute("INSERT OR REPLACE INTO build_checks (chain, task, heard, stop, kind, why, at) "
                           "VALUES (?,?,?,?,?,?,?)", (chain, task, heard, int(stop), kind, why, now))
        store.db().commit()


def _known(chain: str, task: str, heard: str) -> dict | None:
    ""
    row = store.db().execute("SELECT stop, kind, why FROM build_checks WHERE chain=? AND heard=? AND (stop=1 OR task=?) "
                             "ORDER BY stop DESC, at DESC LIMIT 1", (chain, heard, task)).fetchone()
    return dict(row) if row else None


async def check(bot_id: str, args: dict, origin_task: str = "", request: str = "", asker=None,
                now: float | None = None) -> dict:
    ""
    if config.is_sandbox(bot_id):
        return {"stop": False}
    now = time.time() if now is None else now
    title, body = str(args.get("title") or ""), str(args.get("body") or "")
    assignee = str(args.get("assignee") or bot_id)
    try:
        parents = [str(p) for p in (args.get("parents") or []) if p]
        with board._conn() as c:
            rows = chain_of(c, [*parents, origin_task])
            ids = [r["id"] for r in rows]
            nearby = _nearby_lines(c, bot_id, assignee, set(ids), now)
            said = _comments(c, ids, now - HEARD_SEC, now)
        bots = sorted({bot_id, assignee, *(str(r["created_by"]) for r in rows), *(str(r["assignee"]) for r in rows)}
                      & set(config.BOTS_BY_ID))
        said += _cards(bots, now - HEARD_SEC, now) if bots else []
        try:
            said += await asyncio.wait_for(_chat(bots, now - HEARD_SEC, now), CHAT_SEC)
        except asyncio.TimeoutError:
            log.info("the chats were slow to read for the build check")
        heard = _heard_lines(request, said, f"{title} {body}")
        chain = ids[0] if ids else f"new:{_hash(bot_id, assignee, title)}"
        task, words = _hash(title, body), _hash(heard)
        known = _known(chain, task, words)
        if known is not None:
            return {"stop": bool(known["stop"]), **({"message": told(known["why"])} if known["stop"] else {})}
        text = PROMPT.format(first=who.first(), heard=heard, chain=_chain_lines(rows), nearby=nearby, task=(
            f"{_name(bot_id)} for {_name(assignee)}\nTitle: {_flat(title, 200)}\nBody: {_flat(body, BODY_MAX)}"))
        from . import ownerrules
        text += ownerrules.block(ownerrules.QUESTION, f"{title} {body}", bot_id, answered=False)

        chain_zips = tuple(z for i in ids[1:] for z in board.task_zips(i))
        with shortreply.reading_task(ids[0] if ids else "", also=(bot_id, assignee, *bots, *chain_zips)):
            verdict = _read(await (asker or _ask)(text))
        if verdict is None:
            raise ValueError("the helper's answer could not be read")
    except Exception as e:
        log.warning("the build check let a task through: %s", str(e)[:200])
        _file_problem(bot_id, f"{type(e).__name__}: {e}")
        return {"stop": False}
    stop = verdict["kind"] == "build" and not verdict["yes"]
    _remember(chain, task, words, stop, verdict["kind"], verdict["why"], now)
    log.info("build check on a task for %s: %s, yes=%s, stop=%s", assignee, verdict["kind"], verdict["yes"], stop)
    return {"stop": stop, **({"message": told(verdict["why"])} if stop else {})}
