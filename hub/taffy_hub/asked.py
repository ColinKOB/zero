""



































from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime

from . import blockask, board, config, store, who

log = logging.getLogger("taffy.asked")

SKIP_ZIPS = config.PRIVATE_ZIPS
ANSWERS_MAX = 12
ANSWER_CHARS = 1_500



_NOT_THE_PERSON = re.compile(r"^(?:\[Hub note|From the developer\b)", re.I)


_QUOTED = re.compile(r'^To the question "(.+?)", the answer is:\s*(.*)$', re.S)

PROMPT = """{zip} is an assistant working for {first}. It stopped on a task to ask {first} this: {question}

Task: {title}

What {first} has already said on this task, oldest first:
{answers}

Do {first}'s words above already answer the question, so that {zip} can carry on without asking? An answer given to an \
earlier question that was worded differently still counts when it gives what this question needs. An answer {zip} \
called unverified or unsupported still counts: {first}'s own words are the source of truth. "answered" is false when \
the question asks for something {first} has not given yet, or when only part of it is given.
"uses": the numbers of the answers that give it.

Reply with JSON only: {{"answered": true, "uses": [1]}}"""




HELD = ("{hub} Written {when}. You asked again for what {first} already answered on this task, so no card went to "
        "{first}. Your question: \"{question}\" {first}'s answer is on the task, and here it is in full: {words} Use it "
        "as given and carry on. It is the source of truth for this task, so never set it aside as unverified and never "
        "ask for it again.")

ANSWERS_HEAD = ("[{first}'s answers on this task, newest first, in full. They are the source of truth: use them as given, "
                "and never ask for them again]")

HANDED = ("{hub} Written {when}. {first} already answered this on this task, so no card went to {first}. Your question: \"{question}\" "
          "{first}'s own words: {words} Use them as given. They are the source of truth for this task, so never set "
          "them aside as unverified and never ask for them again. If something they do not cover is still missing, "
          "block again and name exactly that part.")



SAME_QUESTION = """An assistant stopped on a task to ask {first} this: "{question}"

Questions {first} already answered on this task, numbered:
{asked}

Does the new question ask for the same information as one of these, so that {first}'s answer to it answers the new one \
too? Different wording still counts as the same. A question about another detail, a follow-up, or a part the earlier \
question did not ask for is new.
"same_as": the numbers of the questions it repeats, or [] when it is new.

Reply with JSON only: {{"same_as": [1]}}"""



RESUMED = ("{hub} Written {when}. {first} answered your question on this task. {first}'s own words, each with the "
           "question it answered, are in answers_from_the_person when you call kanban_show: that list is always current, "
           "and any older hub note that listed them is not. Use them as given, and never ask for them again.")
RESUME_HEAD = "[What the hub handed back for this run of your task]"


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS asked_again (
        task_id TEXT PRIMARY KEY,
        block_at REAL NOT NULL DEFAULT 0,      -- the block last checked against the person's earlier words
        outcome TEXT NOT NULL DEFAULT '',      -- what that check decided ('' when the card stood)
        handed_at REAL NOT NULL DEFAULT 0,     -- when the hub last handed the person's earlier words back to the Zip
        kind TEXT NOT NULL DEFAULT '',         -- the block kind that hand-back was for
        handed_for TEXT NOT NULL DEFAULT '')   -- the answered questions it handed back, one per line
        """)
    for column in ("kind", "handed_for"):
        try:
            c.execute(f"ALTER TABLE asked_again ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        except Exception as e:
            if "duplicate column" not in str(e):
                raise

    c.execute("""CREATE TABLE IF NOT EXISTS resume_notes (
        task_id TEXT PRIMARY KEY,
        note TEXT NOT NULL,
        made REAL NOT NULL)""")


def take_resume(task_id: str) -> str:
    ""


    if not task_id:
        return ""
    with store._lock:
        c = store.db()
        row = c.execute("SELECT note FROM resume_notes WHERE task_id=?", (task_id,)).fetchone()
        if row:
            c.execute("DELETE FROM resume_notes WHERE task_id=?", (task_id,))
            c.commit()
    try:
        block = answers_block(board.get_task(task_id) or {})
    except Exception:
        log.warning("could not read the person's answers on %s", task_id, exc_info=True)
        block = ""
    return "\n\n".join(p for p in (f"{RESUME_HEAD}\n{row['note']}" if row else "", block) if p)


def answers_block(task: dict) -> str:
    ""
    given = answers(task)
    if not given:
        return ""
    return ANSWERS_HEAD.format(first=who.first()) + "\n" + "\n".join(f"- {_quote(a, None)}" for a in reversed(given))


def _row(task_id: str) -> dict:
    r = store.db().execute("SELECT * FROM asked_again WHERE task_id=?", (task_id,)).fetchone()
    return dict(r) if r else {}


def _save(task_id: str, **cols) -> None:
    with store._lock:
        c = store.db()
        c.execute("INSERT OR IGNORE INTO asked_again (task_id) VALUES (?)", (task_id,))
        c.execute(f"UPDATE asked_again SET {', '.join(f'{k}=?' for k in cols)} WHERE task_id=?", (*cols.values(), task_id))
        c.commit()


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def answers(task: dict) -> list[dict]:
    ""

    first = who.first()
    said = re.compile(rf"{re.escape(first)} answered in the app:\s*(.*)$", re.S)
    out = []
    for c in task.get("comments") or []:
        body = str(c.get("body") or "").strip()
        if str(c.get("author") or "").strip() == first:
            text = body
        elif m := said.search(body):
            text = m.group(1).strip()
        else:
            continue
        if _NOT_THE_PERSON.match(text):
            continue
        m = _QUOTED.match(text)
        question, words = (m.group(1), m.group(2)) if m else ("", text)
        if words.strip():
            out.append({"id": c.get("id"), "at": float(c.get("created_at") or 0), "question": _flat(question),
                        "words": words.strip()})
    return out


def _when(at: float) -> str:
    from . import home
    return datetime.fromtimestamp(at, home.zone()).strftime("%b %-d at %-I:%M %p")


def _quote(a: dict, limit: int | None = ANSWER_CHARS) -> str:
    ""
    asked = f' (asked "{a["question"]}")' if a["question"] else ""
    where = f"Comment {a['id']}, " if a.get("id") is not None else ""
    return f'{where}{_when(a["at"])}{asked}: "{a["words"] if limit is None else a["words"][:limit]}"'


async def check(task_id: str, ask=None) -> str:
    ""

    task = await asyncio.to_thread(board.get_task, task_id)
    if not task or task.get("raw_status") not in ("blocked", "triage"):
        return ""
    question = _flat((task.get("card") or {}).get("ask"))
    if not question or blockask.signs_in(task.get("raw_reason") or "", task.get("title") or ""):
        return ""
    given = answers(task)[-ANSWERS_MAX:]
    if not given:
        return ""
    block_at = float(task.get("filed_at") or 0)
    row = _row(task_id)
    if block_at and abs(float(row.get("block_at") or 0) - block_at) <= 1:
        return str(row.get("outcome") or "")
    _save(task_id, block_at=block_at, outcome="")
    latest = max(a["at"] for a in given)
    handed_since = float(row.get("handed_at") or 0) > latest
    bot, first = str(task.get("assignee") or "default"), who.first()
    from . import wayforward
    exact = {a["question"] for a in given if a["question"] and _words(a["question"]) == _words(question)}
    if exact:


        return await _repeat(task, question, [a for a in given if a["question"] in exact], row, handed_since)
    if wayforward._private(task):
        return await _private(task, question, given, row, handed_since, ask)
    data = await wayforward._ask(PROMPT.format(
        zip=wayforward._name(bot), first=first, question=question, title=_flat(task.get("title"))[:200],
        answers="\n".join(f"{i}. {_quote(a)}" for i, a in enumerate(given, 1))), ask, 120, board.task_zips(task))
    if not data or data.get("answered") is not True:
        return ""
    uses = [given[i - 1] for i in data.get("uses") or [] if isinstance(i, int) and 1 <= i <= len(given)] or given[-1:]
    if handed_since:
        return await _hold(task, question, uses)
    note = HANDED.format(hub=who.hub_note(), when=_when(time.time()), first=first, question=question,
                         words=" ".join(_quote(a) for a in uses))
    return await _hand_back(task, note, "answered")


async def _private(task: dict, question: str, given: list[dict], row: dict, handed_since: bool, ask) -> str:
    ""
    from . import wayforward
    asked = [a for a in given if a["question"]]
    if not asked:
        return ""
    listed = list(dict.fromkeys(a["question"] for a in asked))
    data = await wayforward._ask(SAME_QUESTION.format(first=who.first(), question=question[:300], asked="\n".join(
        f"{i}. {q[:300]}" for i, q in enumerate(listed, 1))), ask, 60, (), titles=board.task_zips(task))
    numbers = (data or {}).get("same_as")
    same = {listed[i - 1] for i in numbers if isinstance(i, int) and 1 <= i <= len(listed)} \
        if isinstance(numbers, list) else set()
    if not same:
        return ""
    return await _repeat(task, question, [a for a in asked if a["question"] in same], row, handed_since)


def _words(text) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(text or "").lower()))


async def _repeat(task: dict, question: str, uses: list[dict], row: dict, handed_since: bool) -> str:
    ""

    handed_for = "\n".join(sorted({a["question"] for a in uses}))
    if handed_since and handed_for == str(row.get("handed_for") or ""):
        return await _hold(task, question, uses)
    note = HANDED.format(hub=who.hub_note(), when=_when(time.time()), first=who.first(), question=question,
                         words=" ".join(_quote(a) for a in uses))
    return await _hand_back(task, note, "answered", handed_for)


async def _hold(task: dict, question: str, uses: list[dict]) -> str:
    ""

    from . import wayforward
    await asyncio.to_thread(_report, task, question)
    note = HELD.format(hub=who.hub_note(), when=_when(time.time()), first=who.first(), question=question,
                       words=" ".join(_quote(a, None) for a in uses))
    with store._lock:
        c = store.db()
        c.execute("INSERT OR REPLACE INTO resume_notes (task_id, note, made) VALUES (?, ?, ?)", (task["id"], note, time.time()))
        c.commit()
    if await asyncio.to_thread(wayforward._park_for_a_day, task["id"], str(task.get("raw_reason") or "")):
        _save(task["id"], outcome="reported")
        return "reported"
    log.warning("could not park %s, which asked again for an answer it was given", task["id"])
    return ""


async def _hand_back(task: dict, note: str, outcome: str, handed_for: str = "") -> str:
    ""

    with store._lock:
        c = store.db()
        c.execute("INSERT OR REPLACE INTO resume_notes (task_id, note, made) VALUES (?, ?, ?)", (task["id"], note, time.time()))
        c.commit()
    pointer = RESUMED.format(hub=who.hub_note(), when=_when(time.time()), first=who.first())
    ok, detail = await asyncio.to_thread(board.resume_with, task["id"], pointer, board.ANSWER)
    if not ok:
        with store._lock:
            store.db().execute("DELETE FROM resume_notes WHERE task_id=?", (task["id"],))
            store.db().commit()
        log.warning("could not hand %s its earlier answer: %s", task["id"], str(detail)[-200:])
        return ""
    _save(task["id"], outcome=outcome, handed_at=time.time(), kind=str(task.get("block_kind") or ""), handed_for=handed_for)
    log.info("%s: the person had already answered the question on %s", outcome, task["id"])
    return outcome


def _report(task: dict, question: str) -> None:
    from . import problems
    bot = str(task.get("assignee") or "default")
    from . import shortreply
    private = shortreply.withheld(bot)
    args = {"kind": "break", "concerns_zip": bot, "where": f"blocked task {task['id']}", "source": "code",
            "what_happened": "The Zip asked the person again for something the person had already answered on this "
                             "task, after the hub handed it that answer."
                             + ("" if private else f" Its question: {question[:300]}"),
            "evidence": (f"The Zip filed the block as {_flat(task.get('block_kind')) or 'unsaid'}. Its words stay on the "
                         "task.") if private else _flat(task.get("raw_reason"))[:600] or question[:600],
            "expected": "A Zip uses the person's earlier answer on the task and never asks for it again."}
    try:
        problems.report(bot, args)
    except problems.ProblemError:
        log.warning("could not file a report for %s asking again", task["id"], exc_info=True)
