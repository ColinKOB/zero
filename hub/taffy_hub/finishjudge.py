""
































import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from . import chatter, replycheck, store

log = logging.getLogger("taffy.finishjudge")

SWITCH = "finish_judge"
MODES = ("shadow", "on", "off")













DEFAULT_MODEL = "luna"
DEFAULT_EFFORT = "low"
TIMEOUT_SEC = 25.0
MAX_TOKENS = 400




SHORT_REQUEST = 3
RESULT_MAX = 700
ARGS_MAX = 300
RECORD_MAX = 18000

GAPS = {
    "unsearched": "a source this request needed was never searched",
    "unmentioned_failure": "a step failed and the reply doesn't say so",
    "answered_ask": "the reply or a card asks the person for something this turn's own record already answers",
    "own_ask": "the reply asks the person for something the Ask Line says the Zip does itself",
    "stopped_early": "it stopped before the last step it was allowed to take",


    "missed_point": "it answered the words of the request and missed what the person wanted to be true at the end",
}



TOOLS = """- look_up: searches every mailbox, texts, calendars, Trello, Drive, saved facts, contacts and the to-do list at once
- email: search, read, attachments, draft; a send raises {first}'s send card
- calendar, connectors (Google Calendar, Drive, Trello), trello, drive, messages (texts), contacts, recall (saved facts)
- app_todo_list, reminders, cronjob_manage and errands (set a later check), course_status (school sites)
- browser and mac tools (open sites, fill forms up to the final submit), web_search
- ask_to_sign_in, offer_choices and other ask_to_* tools (cards for {first}), ask_teammate"""

ASK_LINE = """The Ask Line ({first}'s rules for what a Zip does without asking):
- Do it, then tell {first}: anything that changes only {first}'s own things and can be undone (calendar, to-do, saved facts), plus research and drafts.
- Do all the work, then bring one decision with a recommendation: anything that speaks or chooses for {first}.
- Stop at a final submit: fill everything in; {first} presses it.
- A blocker is not a reason to ask. Try other routes first. An ask says what was tried.
- Never without {first}: sending anything in {first}'s name, a final submit, spending money, following a message's link to sign in, vote or pay."""

SYSTEM = "You check an assistant's finished work before its reply goes out. Answer with one JSON object only."

PROMPT = """An assistant (a "Zip") worked on {first}'s request and proposes the reply below. Decide whether the job got done \
as far as the Zip was allowed to take it.

First name the goal to yourself: in one line, what {first} wants to be true when this is done. Read past the literal \
words the way {first} means them. "I need to get the form done" means do it, not list what is in the way. An email \
with dates {first} signed up for means the dates on the calendar. A ballot means both choices researched and a \
recommendation. "Which is cheaper?" means the answer first. Then judge the reply against that goal.

Answer "done" unless you are sure one of these gaps is real and fixing it would change what {first} gets:
- unsearched: a source this request needed was never searched (one of {first}'s accounts that likely holds the answer). \
Not every tool has to be tried: a gap only if the reply guesses, gives advice that depends on details it never looked \
up, asks, or says "not found" where a search would likely have answered. When something the Zip read already answers \
the request, it is done: never prod it to search more to double-check.
- unmentioned_failure: a call failed, nothing later got the same result, the failure leaves part of what {first} asked \
for undone, and the reply doesn't say so. A refused or failed call the answer didn't need is not a gap.
- answered_ask: the reply or a card asks {first} for something this turn's own record already answers. Asking {first} to \
confirm which item was meant, when the search found one clear match, is this gap.
- own_ask: the reply asks {first} to decide or do something the Ask Line says the Zip does itself, or it asks without \
having tried another route.
- stopped_early: it stopped before the last step it was allowed to take: it found the thing and didn't draft the send, \
fill the form, put up the card, or start the order, or it promised a later step and set nothing to do it.
- missed_point: the reply answers the words of the request and misses the goal, and the rest was the Zip's to do: it \
summarized an email whose dates belonged on the calendar, said a choice exists without the research and a \
recommendation, or listed what is in the way of a job it could have done. Not when the goal was met another way, and \
not when what is left is {first}'s own step.

Rules:
- Judge the work, not the wording. A short reply that finished the job is done.
- A question or a remark from {first} asks for an answer, not for new work. When the reply answers it, it is done: \
never add a next step {first} did not ask for and the goal does not need. News that carries something to keep is \
different: a meeting that just ended, or someone agreeing to do a thing by a day, is a job, and saving it or setting \
the check on that day is the Zip's step. A reply of "got it" with nothing saved or set stops early.
- Before you name a gap, look for the step in the record and in the reply. If the Zip did it, offered it on a card, \
or the reply says plainly that it could not be done and why, it is done.
- A card, an okay, a sign-in request or a later check that this turn put up or set means the rest waits on {first} \
or on that check. That is done, and a call refused while raising such a card is the card, not a failure.
- unsearched needs a named source and an answer in the reply that nothing read this turn supports. More searching to \
be thorough is never a gap.
- If you would have to guess whether a gap is real, answer done.
- The request's own limits win: when it says read-only, quiet, just check, or don't change anything, never prod past them.
- A call refused on purpose is not a failure to fix: "Not done", "Blocked", a test copy, a teammate who can't answer, or \
a card waiting on {first}. Never prod the Zip to retry or route around one.
- An email send in the email tool puts a send card in front of {first}, and a tap on it is {first}'s approval. For a \
message {first} asked to send, calling send is the allowed last step; a draft alone stops early. A site that needs \
{first}'s sign-in gets ask_to_sign_in.
- A question that only {first} can answer (a preference, a fact no source holds) is fine when the reply says what was tried.
- Never ask the Zip to send anything in {first}'s name, press a final submit, or spend money. A draft, a card for {first}'s \
yes, or a form filled up to its final button is the last allowed step.
- If the request asked for a silent or quiet result, a reply of [SILENT] can be done.
- Pick only the most important gap. The prod is one specific instruction to the Zip, naming what it found or must use, \
in under 40 words (for example: "You found the pickleball registration email. Use it to fill the form instead of asking which form.").

{ask_line}

Tools a Zip has:
{tools}

{first}'s request:
<request>
{request}
</request>

What the Zip did this turn, in order (each call with a short form of its result):
<record>
{record}
</record>

The proposed reply:
<reply>
{reply}
</reply>

Answer with JSON only: {{"verdict": "done"}} or {{"verdict": "gap", "gap": "<one of: {kinds}>", "prod": "<one instruction>"}}"""

PROD_NOTE = ("Before this reply goes out: {prod}\nDo that now, then write the complete reply again. Don't send anything "
             "in {first}'s name, press a final submit, or spend money: draft it, fill it in up to that button, or put "
             "up the card. If you still can't do it, say plainly in the reply what was not done and why.")


@dataclass
class Verdict:
    verdict: str = "done"
    gap: str = ""
    prod: str = ""
    seconds: float = 0.0
    model: str = ""
    error: str = ""
    calls: int = 0
    skipped: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def gap_found(self) -> bool:
        return self.verdict == "gap" and bool(self.prod)




def _settings() -> dict:
    from . import home
    got = home.get(SWITCH)
    return got if isinstance(got, dict) else {}


def mode() -> str:
    got = _settings().get("mode")
    return got if got in MODES else "shadow"


def model():
    ""
    from . import modelsetup
    key = _settings().get("model")
    return modelsetup.M.CATALOG.get(key) or modelsetup.M.CATALOG[DEFAULT_MODEL]


def effort() -> str:
    got = _settings().get("effort")
    m = model()
    return got if got in m.efforts else DEFAULT_EFFORT




def _calls(turn: list[dict]) -> list[dict]:
    ""

    out, by_id = [], {}
    for message in turn:
        if message.get("role") == "assistant":
            said = chatter.without_hermes_notes(str(message.get("content") or "")).strip()
            for call_id, name, args in replycheck._calls(message):
                call = {"name": name or "", "args": args, "result": "", "failed": False, "said": said}
                said = ""
                by_id[call_id] = call
                out.append(call)
        elif message.get("role") == "tool":
            call = by_id.get(str(message.get("tool_call_id") or ""))
            if call is not None:
                content = message.get("content")
                call["result"] = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                call["failed"] = replycheck.failed(content)
    return out


def _request(turn: list[dict]) -> str:
    from . import history, turnsummary
    if not turn:
        return ""
    return turnsummary.peel(history.unwrap_steer(str(turn[0].get("content") or "")))[1].strip()


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _record(calls: list[dict]) -> str:
    ""

    lines = []
    budget = RECORD_MAX
    for i, call in reversed(list(enumerate(calls, 1))):
        if budget > RESULT_MAX:
            args = _short(json.dumps(call["args"], ensure_ascii=False), ARGS_MAX)
            result = _short(call["result"], RESULT_MAX) if call["result"] else "(no result)"
            line = f"{i}. {call['name']} {args}\n   {'FAILED: ' if call['failed'] else ''}{result}"
        else:
            line = f"{i}. {call['name']} {_short(json.dumps(call['args'], ensure_ascii=False), 80)}" + \
                (" (failed)" if call["failed"] else "")
        if call.get("said"):
            line = f"   (said in the chat: {_short(call['said'], 400)})\n" + line
        budget -= len(line)
        lines.append(line)
    return "\n".join(reversed(lines))


def _words(request: str) -> list[str]:
    ""
    from . import history, turnsummary
    text = turnsummary.peel(history.unwrap_steer(str(request or "")))[1]
    return re.sub(r"\[[^\]]*\]", " ", text).split()


def _is_card(name: str) -> bool:
    return name.startswith(("ask_to_", "offer_choices", "ask_for_"))


def skip(calls: list[dict], reply: str, request: str = "") -> str:
    ""

    asks = "?" in (reply or "")
    if not calls:
        return "" if asks or len(_words(request)) > SHORT_REQUEST else "conversation"
    if len(calls) == 1 and not _is_card(calls[0]["name"]) and not calls[0]["failed"] and not asks:
        return "one read"
    return ""


def _shown(call: dict) -> dict:
    ""


    from . import privatewords
    args = {} if privatewords.holds(json.dumps(call["args"], ensure_ascii=False)) else call["args"]
    return {**call, "args": args, "result": privatewords.leave_out(call["result"]),
            "said": privatewords.leave_out(call.get("said") or "")}




BOARD_RULE = """
This reply is the summary that closes a board task, and the request is the task's own words. A gap here must name the \
one thing the task asked for that is not done, or not said in the summary as done or not done, in words from the task \
itself. If you cannot name one such thing, answer done."""


def prompt(raw: list[dict], reply: str, board: bool = False) -> tuple[str, list[dict]]:
    ""

    from . import privatewords, who
    turn = replycheck.current_turn(raw)
    calls = _calls(turn)
    shown = privatewords.leave_out(visible(reply)).strip() or chatter.SILENT
    first = who.first()
    text = PROMPT.format(first=first, ask_line=ASK_LINE.format(first=first), tools=TOOLS.format(first=first),
                         request=_short(privatewords.leave_out(_request(turn)), 3000),
                         record=_record([_shown(c) for c in calls]) or "(no tool calls)", reply=shown[:8000],
                         kinds=", ".join(GAPS))
    if board:
        text = text.replace("\n\nAnswer with JSON only:", BOARD_RULE + "\n\nAnswer with JSON only:", 1)
    return text, calls


def _read(answer: str) -> dict | None:
    ""
    found = re.search(r"\{.*\}", answer or "", re.S)
    if not found:
        return None
    try:
        got = json.loads(found.group(0))
    except ValueError:
        return None
    if not isinstance(got, dict):
        return None
    if got.get("verdict") == "done":
        return {"verdict": "done"}
    if got.get("verdict") == "gap" and got.get("gap") in GAPS and str(got.get("prod") or "").strip():
        return {"verdict": "gap", "gap": got["gap"], "prod": " ".join(str(got["prod"]).split())[:400]}
    return None




async def ask(text: str, system: str = SYSTEM, timeout: float = TIMEOUT_SEC) -> str:
    ""

    from . import shortreply
    if _under_tests():
        raise RuntimeError("no model calls under the test suite")
    return await shortreply.ask_helper(text, model=model(), timeout=timeout, system=system, max_tokens=MAX_TOKENS,
                                       effort=effort())


def _under_tests() -> bool:
    ""

    import os
    import sys
    from pathlib import Path
    started = sys.argv[0] if sys.argv else ""
    return os.environ.get("ZERO_TESTS") == "1" or "unittest" in started or "tests" in Path(started).parts


async def judge(raw: list[dict], reply: str, asker=None, clock=time.monotonic, always: bool = False, *,
                zips, board: bool = False) -> Verdict:
    ""


    from . import config, privatewords, shortreply
    if shortreply.withheld(*((zips,) if isinstance(zips, str) else zips or ())):
        return Verdict("skipped", skipped="private")
    text, calls = prompt(raw, reply, board)
    turn = replycheck.current_turn(raw)
    if visible(reply).strip() and not privatewords.leave_out(visible(reply)).strip():
        return Verdict("skipped", calls=len(calls), skipped="private")
    why = "" if always else skip(calls, visible(reply), str(turn[0].get("content") or "") if turn else "")
    if why:
        return Verdict("skipped", calls=len(calls), skipped=why)
    started = clock()
    out = Verdict(calls=len(calls), model=model().id)
    try:
        from . import shortreply
        with shortreply.reading(zips):
            answer = await asyncio.wait_for((asker or ask)(text), TIMEOUT_SEC)
    except asyncio.TimeoutError:
        out.error = "timeout"
    except Exception as e:
        log.warning("the finish check could not run", exc_info=True)
        out.error = type(e).__name__
    else:
        got = _read(answer)
        if got is None:
            out.error = "unreadable"
        else:
            out.raw = got
            out.verdict, out.gap, out.prod = got["verdict"], got.get("gap", ""), got.get("prod", "")
    out.seconds = round(clock() - started, 2)
    return out


def note(v: Verdict) -> str | None:
    ""
    from . import who
    return PROD_NOTE.format(prod=v.prod, first=who.first()) if v.gap_found else None




@store.schema
def _tables(c) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS finish_judge_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at REAL NOT NULL,
            bot_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            verdict TEXT NOT NULL,
            gap TEXT NOT NULL DEFAULT '',
            prod TEXT NOT NULL DEFAULT '',
            seconds REAL NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            calls INTEGER NOT NULL DEFAULT 0,
            patterns TEXT NOT NULL DEFAULT '',
            skipped TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS finish_judge_log_at ON finish_judge_log(at);
    """)


def patterns_fired(pattern_note: str | None) -> str:
    ""
    if not pattern_note:
        return ""
    if pattern_note == "doneline":
        return "doneline"
    marks = {"before searching": "unlooked", "promises this for later": "promise", "These calls failed": "failure",
             "These teammates answered": "teammate", "flagged a conflict": "conflict", "nothing is set to look again":
             "no_next_date", "this request never read": "unread", "You put these to": "question"}
    return ",".join(word for mark, word in marks.items() if mark in pattern_note) or "other"


def record(bot_id: str, v: Verdict, run_mode: str, pattern_note: str | None = None) -> None:
    ""
    try:
        with store.db() as c:
            c.execute("INSERT INTO finish_judge_log (at, bot_id, mode, verdict, gap, prod, seconds, model, error, calls,"
                      " patterns, skipped) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (time.time(), bot_id, run_mode, v.verdict, v.gap, v.prod, v.seconds, v.model, v.error, v.calls,
                       patterns_fired(pattern_note), v.skipped))
    except Exception:
        log.warning("could not log the finish check", exc_info=True)


def visible(reply: str) -> str:
    return chatter.without_hermes_notes(chatter.split_short(reply)[0] or "")


def plain(tools_called: bool, request: str, reply: str) -> bool:
    ""
    return not tools_called and skip([], visible(reply), request) == "conversation"


async def shadow(bot_id: str, reply: str, pattern_note: str | None, raw: list[dict] | None, fetch) -> None:
    ""

    if raw is None:
        raw = await fetch()
        if raw is None:
            return
    v = await judge(raw, reply, zips=(bot_id,))
    if v.gap_found:
        log.info("finish check shadow: would prod %s (%s): %s", bot_id, v.gap, v.prod)
    record(bot_id, v, "shadow", pattern_note)


_tasks: set[asyncio.Task] = set()


def shadow_later(bot_id: str, reply: str, pattern_note: str | None, raw: list[dict] | None, fetch,
                 tools_called: bool = True, request: str = "") -> None:
    ""

    if plain(tools_called, request, reply):
        record(bot_id, Verdict("skipped", skipped="conversation"), "shadow", pattern_note)
        return
    try:
        task = asyncio.get_running_loop().create_task(shadow(bot_id, reply, pattern_note, raw, fetch))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)




def board_record(raw: list[dict], task_id: str, body: str, summary: str) -> list[dict]:
    ""

    start = next((i for i, m in enumerate(raw) if m.get("role") == "user"), None)
    if start is None:
        return []
    out = [{"role": "user", "content": f"Board task {task_id}:\n{body}" if body else str(raw[start].get("content") or "")}]
    for message in raw[start + 1:]:
        if message.get("role") == "user" and replycheck.starts_turn(message):
            out.append({**message, "display_kind": "steer"})
            continue
        if any(name == "kanban_complete" for _, name, _ in replycheck._calls(message)):
            break
        out.append(message)
    return out + [{"role": "assistant", "content": summary}]


def _task_body(task_id: str) -> str:
    import sqlite3
    from . import config
    try:
        conn = sqlite3.connect(f"file:{config.KANBAN_DB}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT body FROM tasks WHERE id = ?", (task_id,)).fetchone()
        finally:
            conn.close()
        return str(row[0] or "") if row else ""
    except sqlite3.Error:
        log.warning("could not read task %s for the finish check", task_id, exc_info=True)
        return ""


async def board(bot_id: str, session_id: str, task_id: str, summary: str, sent_back: bool) -> None:
    ""
    from . import hermes
    raw = await hermes._turn_messages(bot_id, session_id)
    if raw is None:
        record(bot_id, Verdict("done", error="unreadable record"), "board", "doneline" if sent_back else None)
        return
    body = await asyncio.to_thread(_task_body, task_id)
    from . import board as kanban
    v = await judge(board_record(raw, task_id, body, summary), summary, always=True,
                    zips=kanban.task_zips(task_id) + (bot_id,))
    if v.gap_found:
        log.info("finish check shadow: would send %s's close of %s back (%s): %s", bot_id, task_id, v.gap, v.prod)
    record(bot_id, v, "board", "doneline" if sent_back else None)




NAMED_MIN_WORDS = 5


async def board_close(bot_id: str, session_id: str, task_id: str, summary: str, asker=None) -> dict:
    ""



    from . import board as kanban, config, hermes, shortreply
    if shortreply.withheld(*kanban.task_zips(task_id), bot_id):
        return {"private": True}
    if mode() == "off":
        return {}
    raw = await hermes._turn_messages(bot_id, session_id)
    if raw is None:
        record(bot_id, Verdict("done", error="unreadable record"), "board-gate")
        return {}
    body = await asyncio.to_thread(_task_body, task_id)
    v = await judge(board_record(raw, task_id, body, summary), summary, asker, always=True,
                    zips=kanban.task_zips(task_id) + (bot_id,), board=True)
    named = v.gap_found and len(v.prod.split()) >= NAMED_MIN_WORDS
    record(bot_id, v, "board-gate")
    if v.gap_found and not named:
        log.info("finish check on %s's close of %s named nothing (%r): the close goes ahead", bot_id, task_id, v.prod)
    return {"gap": v.prod} if named else {}


def board_later(bot_id: str, session_id: str, task_id: str, summary: str, sent_back: bool) -> None:
    ""
    try:
        task = asyncio.get_running_loop().create_task(board(bot_id, session_id, task_id, summary, sent_back))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
