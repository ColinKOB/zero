""




































































from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime

from . import blockask, board, config, store, who

log = logging.getLogger("taffy.wayforward")

ENABLED = False
TIMEOUT_SEC = 25
ASK_MAX = 220
OPTION_MAX_WORDS = 7
ASK_MAX_WORDS = 40
BACKGROUND_MAX = 210
FIELD_MAX_WORDS = 5
WAYS_WITHOUT_MAX = 2

_PLUMBING = re.compile(r"\bt_[0-9a-f]{4,}\b|https?://|\b[\w-]+\.(?:py|js|ts|json|md|swift|sh|ya?ml|txt|csv|log|sql)\b"
                       r"|(?:^|\s)[~/][\w.-]*/[\w./-]+", re.I)

ROUTES = ("its own browser on the home computer, with the logins saved for it",
          "{first}'s Mac, where a helper can use {first}'s own browser and apps",
          "an app {first} connected, in place of the website",
          "another site, file or source that holds the same thing",
          "a draft of an email or message for {first} to send to whoever can fix it",
          "finishing the rest of the task and leaving only this part",
          "waiting and trying again at a later time")
FALLBACK_OPTIONS = ["Try another way", "Wait until tomorrow", "Drop it"]




FINAL_ACTION = re.compile(r"\b(?:submit(?:s|ted|ting)?|send(?:s|ing)?|sent|publish(?:es|ed|ing)?|"
                          r"purchas(?:e|es|ed|ing)|buy(?:s|ing)?|pay(?:s|ing|ment)?|paid|order(?:s|ed|ing)?|"
                          r"book(?:s|ed|ing)?|check ?out|delete(?:s|d)?|deleting|erase(?:s|d)?|cancel(?:s|led|ling)?)\b",
                          re.I)


def offers_final_action(text) -> bool:
    ""
    return bool(FINAL_ACTION.search(_flat(text)))


_ZIP_DOES = re.compile(r"\b(?:should|shall|can|may|could) I\b|\bwant me to\b|\bI(?:'ll| will)\b", re.I)


def proposes_final_action(question) -> bool:
    ""

    q = _flat(question)
    return bool(_ZIP_DOES.search(q)) and offers_final_action(q)

HANDED_OVER, OUR_TOOLS, ONLY_PERSON = "handed_over", "tools", "person"
ROUTED_WAIT_SEC = 24 * 3600
HOLD_RETRY_SEC = 15 * 60

SYSTEM = "You read an assistant's note about work it could not finish and answer with JSON only."

PROMPT = """{zip} is an assistant working for {first}. It stopped twice on the same thing in this task and now waits.

Task: {title}
What was asked: {body}
{zip}'s own note on why it stopped: {reason}
Earlier notes on the task, oldest first:
{notes}

Routes {zip} has:
{routes}

Answer six things.
"needs_person": true only when something is in the way that only {first} can clear or decide. false when the note is \
a status report: {zip} is waiting on work of its own that is still going or due back (a coding session, a build, a \
test run, a report), or the note says nothing is needed from {first}.
"untried": one of those routes that the notes do not show {zip} already tried and that could really get past this \
blocker, as one sentence telling {zip} what to try. "" when the notes show the other routes were tried, or none of \
them can get past it. A step only {first} can take (typing a password or a code sent to {first}, signing, paying, a \
final submit, a choice that is {first}'s) is not a route.
"in_the_way": one plain sentence to {first}, in the first person as {zip} (never the name {zip}), saying what is \
actually in the way. At most 25 words. No ids, file paths, tool names or words like server, session, token or API.
"ask": one question to {first}, in plain everyday words, that the options answer, ending in "?". At most 20 words, \
in the first person as {zip}. No ids, file paths, tool names or the task's own title. When two different things are \
in the way, ask only about the one that unblocks the most work now, and name the other in "in_the_way". Never join \
two requests in one question.
"options": 2 to 4 real ways forward for this blocker, each at most 6 words, written as {first} would tap them as \
answers to that question, with the one you recommend first. Choose what fits: {first} has already taken one named \
step ("I've signed in"), {zip} uses a named other route, wait until a named time, do the rest without this part, drop \
it. Never "Try again" by itself. Never an option that only promises {first} will do the asked thing later ("I'll send \
it", "I'll choose one", "I'll measure it"): the card would close and the work would still wait on {first}.
"why_first": at most 20 words, in the first person as {zip}, on why the first option.

Reply with JSON only: {{"needs_person": true, "untried": "...", "in_the_way": "...", "ask": "...", "options": \
["..."], "why_first": "..."}}"""

_BACKGROUND_RULES = """"background": a list of exactly two short plain sentences to {first}, who has not thought about \
this work in days. The first says what this work is and where it came from: who asked for it or which email started \
it. When {zip} started the work itself, say so in the first person, as in "I started this to reach five paying clients \
for your website business." If the notes do not show where it came from, say only what the work is, and never guess. \
The second says what is in the way right now. At most 15 words each. Say "you" to {first} and "I" for {zip}. Never \
write the name {first} or the name {zip}, not even as a possessive. No ids, file names, tool names or words like \
server, session, token or API. Do not repeat the question, and ask nothing."""




_ANSWER_RULES = """"answer_kind": decide it with one test: after {first} taps one of the answers, can the work carry on \
with nothing more from {first}? "information" when it cannot, because the work needs something only {first} has: an \
address, a number, a measurement, a date, a name, a file or some text. That holds even when the question says \
"choose", "pick" or "share", and even when the answers offered are kinds of answer or promises to provide it: asked \
which address to use, the answer is the address itself. "choice" only when each answer is a complete way forward by \
itself.
"answer_field": for "information", a label of at most 4 words for the box {first} types or pastes the answer into, such \
as "Mailing address" or "Box size and weight". "" for "choice".
For "information", "options" holds at most 2 ways forward that work without the answer, such as "Skip the labels", \
and is [] when there are none. Never an option that sends, submits, publishes, buys, pays, books or deletes anything. For either kind, never an option that only promises \
{first} will do the asked thing later ("I'll send it", "I'll choose one", "I'll measure it", "I'll share them")."""


BACKGROUND_ASK = "\n\nWhere this work came from: {origin}\n\nAdd one more key to that JSON. " + _BACKGROUND_RULES


ANSWER_ASK = "\n\nAdd these keys to that JSON too.\n" + _ANSWER_RULES

_CLEARS_RULES = """{filed}"clears": who can actually clear what is in the way, in one word.
{handed_rule}"tools": the assistants' own tools or computer cannot do this step, whatever anyone decides: a missing \
ability, a harmless command that was refused, no container or sandbox, no scheduler, a tool that returns cut-off, \
unreadable or broken results (even about the assistant's own task), a tool that times out or fails with an error, or a \
file or page the assistants' computer cannot open or read. {first} cannot act on a limit like this, even when the note \
asks {first} what to do about it. It is for the developer. Waiting for work that is still running is not this.
"person": only {first} can clear it: {first}'s own account, money or signature, a fact only {first} has, or a choice \
about {first}'s own life or business that no assistant was given. A sign-in, password, code or human check that only \
{first} can give stays "person", even when a tool is what ran into it. Never a tool or computer that failed by itself.
"our_limit": one sentence for the developer naming what the assistants' own tools or computer could not do, with the \
technical detail from the notes. Fill it in whatever "clears" says, because a limit can sit behind a decision. "" when \
no such limit is involved. These are never limits: waiting for work still running, a website's human check, a sign-in, \
an approval or step kept for {first} on purpose, or someone who has not replied yet."""

_HANDED_RULE = """"handed_over": {handed}, the assistant that handed {zip} this task, can settle it: the scope or plan is \
{handed}'s, the decision is {handed}'s, or {handed} can give or find what is missing. Choose this over "tools" whenever \
{handed} could change the plan, scope or approach so the work goes on without the missing ability, and still name that \
ability in "our_limit". A decision about {first}'s own life or business stays "person", even on a task {handed} handed \
over.
"""

_HANDOVER_KEY = """
"for_handover": when "clears" is "handed_over", one sentence to {handed} saying exactly what {zip} needs decided or \
given. "" otherwise."""


CLEARS_ASK = "\n\nAdd these keys to that JSON too.\n"



_FILED = {"capability": "{zip} filed this block as a limit of its own tools or abilities. That is evidence it is not "
                        "the person's to clear, unless the notes show a decision, a sign-in or a fact only the person "
                        "has.\n",
          "transient": "{zip} filed this block as something passing that it expects to clear by itself.\n"}

ROUTE_PROMPT = """{zip} is an assistant working for {first}. {handed_line}{zip} stopped on this task with this question \
for {first}: {question}

Task: {title}
What was asked: {body}
{zip}'s own note: {reason}
Earlier notes on the task, oldest first:
{notes}

Answer these.
{rules}

Reply with JSON only: {{"clears": "person", "our_limit": "", "for_handover": ""}}"""

HANDED_NOTE = ("{hub} {zip} stopped on \"{title}\" (task {task}), which you handed it, and this is yours to settle. "
               "{need} Its note: {reason} Decide it now, or get what it needs yourself. Write your answer on the task "
               "with kanban_comment, then call kanban_unblock on the task so {zip} picks it up. If the work is no longer "
               "wanted, say so in that comment. If only {first} can settle this, ask {first} yourself in plain words "
               "about the real thing, with the background and your recommendation. Otherwise reply with only {silent}.")

EXPLAIN_PROMPT = """{zip} is an assistant working for {first}. It stopped on a task to ask {first} this question: \
{question}
The card's answers now: {options}

Task: {title}
What was asked: {body}
{zip}'s own note: {reason}
Earlier notes on the task, oldest first:
{notes}
Where this work came from: {origin}

Write """ + _BACKGROUND_RULES + """

Then say what kind of answer the question needs.
""" + _ANSWER_RULES + """
For "choice", "options" repeats the card's answers word for word and leaves out any that only promises.

Reply with JSON only: {{"background": ["...", "..."], "answer_kind": "choice", "answer_field": "", "options": ["..."]}}"""

SAME_PROMPT = """{zip} is an assistant working for {first}. It stopped on a task, and its own note says why:
{reason}

A card would ask {first}: {question}

Does the card ask {first} for the same thing the note needs from {first}, only shorter or in plainer words? It is not \
the same when it asks about a different step, offers to do something the note did not ask about (such as submitting, \
sending, booking or paying), or leaves out what the note needs.

Reply with JSON only: {{"same": true}}"""

LINE_PROMPT = """{zip} is an assistant working for {first}. It just stopped work on a task because something got in \
its way, and it will try again by itself later.

Task: {title}
{zip}'s own note on why it stopped: {reason}

Write one plain sentence to {first}, in the first person as {zip} (never the name {zip}), saying that it has stopped \
on this for now and what is in the way. At most 30 words. No ids, file paths, tool names or words like server, \
session, token or API. Ask {first} for nothing.

Reply with JSON only: {{"line": "..."}}"""


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS block_cards (
        task_id TEXT PRIMARY KEY,
        block_at REAL NOT NULL DEFAULT 0,      -- when the block this card is for was filed (the board's event)
        ask TEXT NOT NULL DEFAULT '',          -- what is in the way, said to the person; '' until a card is made
        options TEXT NOT NULL DEFAULT '[]',
        more TEXT NOT NULL DEFAULT '',
        sent_back INTEGER NOT NULL DEFAULT 0,  -- 1: the hub handed it back once with a route to try
        said INTEGER NOT NULL DEFAULT 0,       -- 1: the Zip's chat already has its "I've stopped" line
        made REAL NOT NULL DEFAULT 0,
        background TEXT NOT NULL DEFAULT '',   -- two plain sentences on what the work is and what is in the way
        background_at REAL NOT NULL DEFAULT 0, -- the block they were written for, set when the writing is tried
        answer_field TEXT NOT NULL DEFAULT '', -- the label of the box for a piece of information; '' on a choice
        answer_ways TEXT NOT NULL DEFAULT '',  -- JSON: the options that replace a card's own; '' keeps them
        answer_at REAL NOT NULL DEFAULT 0,     -- the block the kind of answer was judged for
        routed_at REAL NOT NULL DEFAULT 0,     -- the block whose question was judged for who can clear it
        held_at REAL NOT NULL DEFAULT 0,       -- when `prepare` last got no usable answer for `block_at` (no card)
        asks_at REAL NOT NULL DEFAULT 0,       -- the quiet-marked block judged for whether it asks the person
        not_his_at REAL NOT NULL DEFAULT 0)    -- the block whose question went to someone else, or he had answered
        """)
    for stmt in ("ALTER TABLE block_cards ADD COLUMN background TEXT NOT NULL DEFAULT ''",
                 "ALTER TABLE block_cards ADD COLUMN background_at REAL NOT NULL DEFAULT 0",
                 "ALTER TABLE block_cards ADD COLUMN answer_field TEXT NOT NULL DEFAULT ''",
                 "ALTER TABLE block_cards ADD COLUMN answer_ways TEXT NOT NULL DEFAULT ''",
                 "ALTER TABLE block_cards ADD COLUMN answer_at REAL NOT NULL DEFAULT 0",
                 "ALTER TABLE block_cards ADD COLUMN routed_at REAL NOT NULL DEFAULT 0",
                 "ALTER TABLE block_cards ADD COLUMN held_at REAL NOT NULL DEFAULT 0",
                 "ALTER TABLE block_cards ADD COLUMN asks_at REAL NOT NULL DEFAULT 0",
                 "ALTER TABLE block_cards ADD COLUMN not_his_at REAL NOT NULL DEFAULT 0"):
        try:
            c.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise


def _row(task_id: str) -> dict:
    r = store.db().execute("SELECT * FROM block_cards WHERE task_id=?", (task_id,)).fetchone()
    return dict(r) if r else {}


def _save(task_id: str, **cols) -> None:
    with store._lock:
        c = store.db()
        c.execute("INSERT OR IGNORE INTO block_cards (task_id, made) VALUES (?, ?)", (task_id, time.time()))
        c.execute(f"UPDATE block_cards SET {', '.join(f'{k}=?' for k in cols)} WHERE task_id=?", (*cols.values(), task_id))
        c.commit()


def card(task_id: str, block_at: float | None) -> dict | None:
    ""
    try:
        row = _row(task_id)
    except Exception:
        log.warning("could not read the stuck-task card for %s", task_id, exc_info=True)
        return None
    if not row.get("ask") or abs(float(row.get("block_at") or 0) - float(block_at or 0)) > 1:
        return None
    try:
        options = [str(o) for o in json.loads(row["options"] or "[]")]
    except ValueError:
        options = []
    return {"ask": row["ask"], "options": options, "more": row["more"]}


def background(task_id: str, block_at: float | None) -> str:
    ""
    try:
        row = _row(task_id)
    except Exception:
        log.warning("could not read the background for %s", task_id, exc_info=True)
        return ""
    return str(row.get("background") or "") if _same_block(row, block_at) else ""


def dress(card: dict, task_id: str, block_at: float | None) -> None:
    ""

    card["background"] = background(task_id, block_at)
    card["answer_field"] = ""
    try:
        row = _row(task_id)
    except Exception:
        log.warning("could not read the answer box for %s", task_id, exc_info=True)
        return
    if not _judged(row, block_at):
        return
    if row.get("answer_ways"):
        try:
            card["options"] = [str(o) for o in json.loads(row["answer_ways"])]
        except ValueError:
            log.warning("unreadable options stored for %s", task_id)
    card["answer_field"] = str(row.get("answer_field") or "")

    if card["answer_field"] or card.get("pick") not in card.get("options", []):
        card["pick"] = None


def _same_block(row: dict, block_at: float | None) -> bool:
    return bool(block_at) and abs(float(row.get("background_at") or 0) - float(block_at)) <= 1


def _judged(row: dict, block_at: float | None) -> bool:
    ""
    return bool(block_at) and abs(float(row.get("answer_at") or 0) - float(block_at)) <= 1


def _explained(row: dict, block_at: float | None) -> bool:
    return _same_block(row, block_at) and _judged(row, block_at)


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _sentence(text, limit: int = ASK_MAX) -> str:
    ""
    line = _flat(text).strip("\"'“”")
    if len(line) > limit:
        line = line[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    if line and line[-1] not in ".!?":
        line += "."
    return line[:1].upper() + line[1:]


def _parse(answer: str) -> dict | None:
    m = re.search(r"\{.*\}", answer or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _private(task: dict) -> bool:
    ""


    from . import shortreply
    return shortreply.withheld(*board.task_zips(task))


async def _ask(prompt: str, ask, max_tokens: int, zips, titles=()) -> dict | None:
    ""


    from . import shortreply
    if ask is None:
        if not ENABLED:
            return None
        ask = shortreply.ask_helper
    from . import ownerrules
    prompt += ownerrules.block(ownerrules.QUESTION, prompt)
    try:
        with shortreply.reading(zips, titles=titles):
            answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=max_tokens),
                                            TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no helper answer about a stuck task (%s)", type(e).__name__)
        return None
    return _parse(answer)


def _name(bot_id: str | None) -> str:
    bot = config.BOTS_BY_ID.get(bot_id or "default")
    return bot.name if bot else "Zero"


def _options(found, least: int = 2, most: int = 4) -> list[str]:
    ""

    out = []
    for o in found if isinstance(found, list) else []:
        o = _flat(o).strip(".")
        if o and len(o.split()) <= OPTION_MAX_WORDS and o.lower() != "try again" and not offers_final_action(o) \
                and o.lower() not in {x.lower() for x in out}:
            out.append(o)
    return out[:most] if len(out) >= least else []


def _field(found) -> str:
    ""
    label = _flat(found).strip("\"'“”.:")
    if not label or label.endswith("?") or len(label.split()) > FIELD_MAX_WORDS or _PLUMBING.search(label):
        return ""
    return label[:1].upper() + label[1:]


def _asks_for(data: dict | None) -> str:
    ""
    data = data or {}
    return _field(data.get("answer_field")) if _flat(data.get("answer_kind")).lower() == "information" else ""


def fallback(reason: str) -> dict:
    ""

    from . import reviewcard
    first = re.split(r"(?<=[.!?])\s+", _flat(reason), maxsplit=1)[0]
    ask = _sentence(reviewcard.to_you(first)) if first and len(first.split()) <= ASK_MAX_WORDS else ""
    return {"ask": f"{ask} What should I do?".strip() if ask else "I'm stuck on this. What should I do?",
            "options": list(FALLBACK_OPTIONS), "more": reviewcard.to_you(reason).strip() if ask else ""}


def _parked(task: dict) -> bool:
    ""
    return task.get("raw_status") == "triage" and not task.get("on_card") and \
        blockask.quiet(task.get("raw_reason") or "", task.get("block_kind"), task.get("title") or "")


def _question(text) -> str:
    ""
    line = _sentence(text)
    return line if line.endswith("?") and len(line.split()) <= ASK_MAX_WORDS else ""


def _background(found, question: str = "", zip_name: str = "") -> str:
    ""



    from . import reviewcard
    if not isinstance(found, list) or len(found) != 2:
        return ""
    asked = _flat(question).rstrip(".?!").casefold()
    lines = []
    for sentence in found:
        line = _flat(sentence).strip("\"'“”")
        if not line or line.endswith("?") or line.rstrip(".!").casefold() == asked:
            return ""
        lines.append(line[:1].upper() + line[1:] + ("" if line[-1] in ".!" else "."))
    text = reviewcard.to_you(" ".join(lines))
    if zip_name and re.search(rf"(?<!\w){re.escape(zip_name)}(?!\w)", text):
        return ""
    return text if len(text) <= BACKGROUND_MAX and not _PLUMBING.search(text) else ""


def _origin(task: dict) -> str:
    ""
    by, first = str(task.get("created_by") or "").strip(), who.first()
    low = by.lower()
    if low in ("user", "human", "you", first.lower()):
        return f"{first} asked for this."
    if low in ("default", "taffy", "zero") or by in config.BOTS_BY_ID:
        if low == str(task.get("assignee") or "default").lower():
            return f"{_name(by)} started this itself. Its text may say what set it off, such as an email."
        return f"{_name(by)} asked for this."
    return "Not recorded. Go by the task's text."


def _handed_by(task: dict) -> str | None:
    ""
    by, assignee = str(task.get("created_by") or "").strip(), str(task.get("assignee") or "default")
    return by if by != assignee and by in config.BOTS_BY_ID and not config.is_sandbox(by) else None


def _clears_rules(zip_name: str, first: str, handed: str | None, kind: str | None = None) -> str:
    ""
    name = _name(handed) if handed else ""
    rule = _HANDED_RULE.format(handed=name, zip=zip_name, first=first) if handed else ""
    key = _HANDOVER_KEY.format(handed=name, zip=zip_name) if handed else ""
    filed = _FILED.get(str(kind or ""), "").format(zip=zip_name)
    return _CLEARS_RULES.format(handed_rule=rule, first=first, filed=filed) + key


def _route_of(data: dict | None, handed: str | None) -> tuple[str, str]:
    ""
    data = data or {}
    word = _flat(data.get("clears")).lower()
    known = (HANDED_OVER, OUR_TOOLS, ONLY_PERSON) if handed else (OUR_TOOLS, ONLY_PERSON)
    return (word if word in known else ""), _flat(data.get("our_limit"))


def _report_limit(task: dict, limit: str, reason: str) -> None:
    ""
    from . import problems
    bot = str(task.get("assignee") or "default")
    if _private(task):
        limit = "A task of a Zip whose work stays private stopped on a limit of its own tools."
        reason = f"The Zip filed the block as {_flat(task.get('block_kind')) or 'unsaid'}. Its words stay on the task."
    args = {"kind": "break", "what_happened": _flat(limit)[:600], "evidence": _flat(reason)[:600] or _flat(task.get("title"))[:600],
            "concerns_zip": bot, "where": f"blocked task {task['id']}", "source": "code",
            "expected": "A Zip's own tools can do this step, or the Zip is told before it starts."}
    try:
        problems.report(bot, args)
    except problems.ProblemError:
        try:
            problems.report(bot, {**args, "evidence": "The Zip's note was left out because it held a secret."})
        except problems.ProblemError:
            log.warning("could not file a report for the limit that stopped %s", task["id"], exc_info=True)


def _park_for_a_day(task_id: str, reason: str) -> bool:
    ""


    from . import board, home
    task = board.get_task(task_id) or {}
    if task.get("filed_at"):
        _save(task_id, not_his_at=float(task["filed_at"]))
    until = datetime.fromtimestamp(time.time() + ROUTED_WAIT_SEC, home.zone()).isoformat(timespec="seconds")
    return board._park(task_id, f"SCHEDULED_UNTIL={until} {reason}".strip(), settled=True)


def hub_word(task_id: str, current: float | None = None):
    ""



    try:
        row = _row(task_id)
        from . import asked
        seen = asked._row(task_id)
    except Exception:
        log.warning("could not read what the hub decided about the blocks of %s", task_id, exc_info=True)
        row, seen = {}, {}

    def word(_reason: str, at: float) -> str:
        if abs(float(row.get("not_his_at") or 0) - at) <= 1 or (
                abs(float(seen.get("block_at") or 0) - at) <= 1 and seen.get("outcome") in ("answered", "reported")):
            return "elsewhere"
        if row.get("ask") and abs(float(row.get("block_at") or 0) - at) <= 1 and current is not None and \
                abs(float(current) - at) <= 1:
            return "person"
        return ""
    return word


def _tell_handed(task: dict, handed: str, need: str, reason: str) -> None:
    ""
    from . import chatter, waits
    note = HANDED_NOTE.format(hub=who.hub_note(), zip=_name(task.get("assignee")), title=_flat(task.get("title"))[:120],
                              task=task["id"], need=_flat(need)[:400], reason=_flat(reason)[:700], first=who.first(),
                              silent=chatter.SILENT)
    waits.queue_wake(handed, note)
    waits.kick()


def _tell_limit(task: dict, handed: str, limit: str) -> None:
    ""
    from . import team
    if _private(task):
        text = (f"The task you handed me ({task['id']}) stopped on a limit of my tools. It is filed for the developer, "
                "and I'll try it again tomorrow.")
    else:
        text = (f"The task you handed me, \"{_flat(task.get('title'))[:120]}\" ({task['id']}), stopped because my tools "
                f"can't do one step: {_flat(limit)[:400]} It is filed for the developer, and I'll try it again tomorrow. "
                "Take another route if this can't wait.")
    team.tell(str(task.get("assignee") or "default"), handed, text, None, from_hub=True, shown=False)


async def _reroute(task: dict, word: str, limit: str, need: str) -> str:
    ""

    task_id, reason = task["id"], str(task.get("raw_reason") or "")
    handed = _handed_by(task)
    if word not in (HANDED_OVER, OUR_TOOLS) or (word == HANDED_OVER and not handed):
        return ""
    if word == OUR_TOOLS and await asyncio.to_thread(board.waits_on_person, task_id) == "card":


        await asyncio.to_thread(_report_limit, task, limit or reason, reason)
        return "reported"
    if not await asyncio.to_thread(_park_for_a_day, task_id, reason):
        log.warning("could not park %s, whose block is not the person's", task_id)
        return ""
    if limit or word == OUR_TOOLS:
        await asyncio.to_thread(_report_limit, task, limit or reason, reason)
    if word == HANDED_OVER:
        await asyncio.to_thread(_tell_handed, task, handed, need, reason)
        log.info("sent the question on %s to the Zip that handed it over", task_id)
        return "zip"
    try:
        if handed:
            await asyncio.to_thread(_tell_limit, task, handed, limit or reason)
        else:
            await stopped_line(task_id, task.get("title") or "", reason, task.get("assignee"))
    except Exception:
        log.warning("could not say that %s stopped", task_id, exc_info=True)
    log.info("filed the limit that stopped %s for the developer", task_id)
    return "reported"


def _judged_his(task_id: str, block_at: float) -> bool:
    ""

    row = _row(task_id)
    return bool(row.get("ask")) and abs(float(row.get("asks_at") or 0) - float(block_at or 0)) <= 1


def _declared(task: dict) -> str:
    ""

    return OUR_TOOLS if task.get("block_kind") == "capability" else ""


def _route_prompt(task: dict, handed: str | None) -> str:
    zip_name, first = _name(task.get("assignee")), who.first()
    handed_line = f"{_name(handed)}, another assistant, handed {zip_name} this task. " if handed else ""
    return ROUTE_PROMPT.format(zip=zip_name, first=first, handed_line=handed_line,
                               question=_flat((task.get("card") or {}).get("ask"))[:300],
                               title=_flat(task.get("title"))[:200], body=_flat(task.get("body"))[:600] or "(nothing more)",
                               reason=_flat(task.get("raw_reason"))[:1500], notes=_notes(task),
                               rules=_clears_rules(zip_name, first, handed, task.get("block_kind")))


def routes(task: dict) -> bool:
    ""

    return bool(_handed_by(task)) or task.get("block_kind") in blockask.QUIET_KINDS


async def route_question(task_id: str, ask=None) -> str:
    ""


    from . import board
    task = await asyncio.to_thread(board.get_task, task_id)
    handed = _handed_by(task or {})
    if not task or not routes(task) or not task.get("card") or task.get("raw_status") not in ("blocked", "triage"):
        return ""
    block_at = float(task.get("filed_at") or 0)
    if not block_at or abs(float(_row(task_id).get("routed_at") or 0) - block_at) <= 1:
        return ""
    _save(task_id, routed_at=block_at)
    if _private(task):
        if _judged_his(task_id, block_at):
            return ""
        return await _reroute(task, _declared(task), "", "")
    data = await _ask(_route_prompt(task, handed), ask, 300, board.task_zips(task))
    word, limit = _route_of(data, handed)
    done = await _reroute(task, word, limit, _flat((data or {}).get("for_handover")))
    if not done and limit:
        await asyncio.to_thread(_report_limit, task, limit, str(task.get("raw_reason") or ""))
    return done


async def reroute_open(apply: bool = False, ask=None) -> list[dict]:
    ""

    from . import board
    out = []
    for listed in (await asyncio.to_thread(board.grouped))["needs_you"]:
        handed = _handed_by(listed)
        if not listed.get("card") or listed.get("raw_status") not in ("blocked", "triage"):
            continue
        task = await asyncio.to_thread(board.get_task, listed["id"])
        private = _private(task)
        data = None if private else await _ask(_route_prompt(task, handed), ask, 300, board.task_zips(task))
        word, limit = (_declared(task) or ONLY_PERSON, "") if private else _route_of(data, handed)
        item = {"task": task["id"], "zip": task.get("assignee"), "handed_by": handed, "clears": word or "no answer",
                "report": bool(limit) or word == OUR_TOOLS, "moved": ""}
        if apply:
            item["moved"] = await _reroute(task, word, limit, _flat((data or {}).get("for_handover")))
            if item["moved"]:
                from . import withdrawn
                await asyncio.to_thread(withdrawn.settle, task["id"])
            elif limit:
                await asyncio.to_thread(_report_limit, task, limit, str(task.get("raw_reason") or ""))
        out.append(item)
    return out


def _notes(task: dict) -> str:
    return "\n".join(f"- {_flat(c.get('body'))[:300]}" for c in (task.get("comments") or [])[-6:]) or "(none)"


def _judge_answer(task_id: str, data: dict | None, options: list[str]) -> None:
    ""

    field = _asks_for(data)
    found = (data or {}).get("options")
    if field:
        _save(task_id, answer_field=field, answer_ways=json.dumps(_options(found, 0, WAYS_WITHOUT_MAX)))
        return
    theirs = {o.strip(".").casefold(): o for o in options}
    kept = [theirs[o.casefold()] for o in _options(found) if o.casefold() in theirs]
    if 2 <= len(kept) < len(options):
        _save(task_id, answer_ways=json.dumps(kept))


async def explain(task_id: str, ask=None) -> str:
    ""


    from . import board
    task = await asyncio.to_thread(board.get_task, task_id)
    card = (task or {}).get("card")
    block_at = float((task or {}).get("filed_at") or 0)
    row = _row(task_id)
    if not card or task.get("raw_status") not in ("blocked", "triage") or not block_at or _explained(row, block_at):
        return ""
    written = str(row.get("background") or "") if _same_block(row, block_at) else ""
    judged = _judged(row, block_at)
    fresh = {} if judged else {"answer_field": "", "answer_ways": "", "answer_at": block_at}
    _save(task_id, background=written, background_at=block_at, **fresh)
    zip_name, first = _name(task.get("assignee")), who.first()
    options = [str(o) for o in card.get("options") or []]
    if _private(task):
        parsed = blockask.parse(task.get("raw_reason") or "") or {}
        own = _flat(parsed.get("background"))
        text = "" if written or not own or len(own) > BACKGROUND_MAX or _PLUMBING.search(own) or \
            re.search(rf"(?<!\w){re.escape(zip_name)}(?!\w)", own) else own
        if text:
            _save(task_id, background=text)
        return text
    data = await _ask(EXPLAIN_PROMPT.format(
        zip=zip_name, first=first, question=_flat(card.get("ask"))[:300], title=_flat(task.get("title"))[:200],
        options="; ".join(options) or "(none)",
        body=_flat(task.get("body"))[:600] or "(nothing more)", reason=_flat(task.get("raw_reason"))[:1500],
        notes=_notes(task), origin=_origin(task)), ask, 300, board.task_zips(task))
    if not judged:
        _judge_answer(task_id, data, options)
    text = "" if written else _background((data or {}).get("background"), card.get("ask") or "", zip_name)
    if text:
        _save(task_id, background=text)
    return text


async def prepare(task_id: str, ask=None) -> str:
    ""




    from . import board, reviewcard
    task = await asyncio.to_thread(board.get_task, task_id)
    if not task or not _parked(task):
        return ""
    block_at = float(task.get("filed_at") or 0)
    row = _row(task_id)
    if row.get("ask") and abs(float(row.get("block_at") or 0) - block_at) <= 1:
        return ""
    reason = str(task.get("raw_reason") or "")
    zip_name, first = _name(task.get("assignee")), who.first()
    notes = _notes(task)
    if not row.get("ask") and abs(float(row.get("block_at") or 0) - block_at) <= 1 and \
            time.time() - float(row.get("held_at") or 0) < HOLD_RETRY_SEC:
        return ""
    if _private(task):
        return await _prepare_private(task, row, block_at, reason)
    data = await _ask(PROMPT.format(
        zip=zip_name, first=first, title=_flat(task.get("title"))[:200], body=_flat(task.get("body"))[:600] or "(nothing more)",
        reason=_flat(reason)[:1500], notes=notes,
        routes="\n".join(f"- {r.format(first=first)}" for r in ROUTES))
        + BACKGROUND_ASK.format(zip=zip_name, first=first, origin=_origin(task))
        + ANSWER_ASK.format(first=first)
        + CLEARS_ASK + _clears_rules(zip_name, first, _handed_by(task), task.get("block_kind")), ask, 800,
        board.task_zips(task))
    word, limit = _route_of(data, _handed_by(task))
    need = _flat((data or {}).get("for_handover"))
    if word == HANDED_OVER and (sent := await _reroute(task, word, limit, need)):
        return sent
    if (data or {}).get("needs_person") is False and word != OUR_TOOLS:
        if limit:
            await asyncio.to_thread(_report_limit, task, limit, reason)
        if await asyncio.to_thread(board._park, task_id, reason):
            log.info("parked %s: its note asks the person nothing", task_id)
            return "waiting"
        log.warning("could not park %s, whose note asks the person nothing", task_id)
    untried = _flat((data or {}).get("untried"))
    if untried and not row.get("sent_back"):
        note = (f"{who.hub_note()} Before this goes to {first}, try another route: {untried[:400]} {first}'s rule is "
                "that a blocker is not a reason to ask. If that route fails too, block again and say in your note what "
                f"you tried, what is in the way, and the one step only {first} can take, if there is one.")
        ok, detail = await asyncio.to_thread(board.send_back, task_id, note)
        if ok:
            _save(task_id, sent_back=1)
            log.info("handed %s back with a route it had not tried", task_id)
            return "back"
        log.warning("could not hand %s back: %s", task_id, str(detail)[-200:])
    if word == OUR_TOOLS and (sent := await _reroute(task, word, limit, need)):
        return sent
    if limit:
        await asyncio.to_thread(_report_limit, task, limit, reason)
    line, question = _sentence((data or {}).get("in_the_way")), _question((data or {}).get("ask"))
    field = _asks_for(data) if question else ""
    found_options = (data or {}).get("options")
    options = _options(found_options, 0, WAYS_WITHOUT_MAX) if field else _options(found_options)
    if field:
        found = {"ask": question, "options": options,
                 "more": "\n\n".join(x for x in (line, reviewcard.to_you(reason).strip()) if x)}
    elif line and options:
        why = _sentence((data or {}).get("why_first"), 160)
        found = {"ask": question or f"{line} What should I do?", "options": options,
                 "more": "\n\n".join(x for x in (line if question else "",
                                                 f"I'd go with “{options[0]}”. {why}" if why else "",
                                                 reviewcard.to_you(reason).strip()) if x)}
    elif data is None:
        return await asyncio.to_thread(_hold, task, row, block_at, reason)
    else:
        found = None
    if found is None or not await _same_ask(task, found["ask"], reason, ask):


        own = _own_sentence(reason)
        if not own or task.get("block_kind") == "capability":
            return await asyncio.to_thread(_hold, task, row, block_at, reason)
        found = {"ask": own, "options": options if field else (options or list(FALLBACK_OPTIONS)),
                 "more": reviewcard.to_you(reason).strip()}
    written = _background((data or {}).get("background"), found["ask"], zip_name)
    _save(task_id, block_at=block_at, ask=found["ask"], options=json.dumps(found["options"]), more=found["more"],
          background=written, background_at=block_at if written else 0,
          answer_field=field, answer_ways="", answer_at=block_at if data is not None else 0)
    return "card"


async def _prepare_private(task: dict, row: dict, block_at: float, reason: str) -> str:
    ""

    from . import board
    if _declared(task) == OUR_TOOLS and (sent := await _reroute(task, OUR_TOOLS, "", "")):
        return sent
    if await asyncio.to_thread(board._park, task["id"], reason):
        return "waiting"
    return await asyncio.to_thread(_hold, task, row, block_at, reason)


async def _same_ask(task: dict, question: str, reason: str, ask=None) -> bool:
    ""
    if proposes_final_action(question):
        return False
    if _private(task):
        return False
    data = await _ask(SAME_PROMPT.format(zip=_name(task.get("assignee")), first=who.first(), reason=_flat(reason)[:1500],
                                         question=_flat(question)[:300]), ask, 40, board.task_zips(task))
    return bool(data) and data.get("same") is True









ASKS_PROMPT = """{zip} is an assistant working for {first}. It stopped on a task and filed this note, which it marked \
as "{kind}" ({kind_words}).

Task: {title}
{label}: {reason}

Does the note ask {first} a question {first} can answer, so the work can go on? It counts even when the note is \
marked as a limit of the assistant's tools. A note that only reports a limit, a wait, or something only the \
assistant or the developer can fix asks nothing.
When it asks, give its choices if it offers some ("options", two to four short ones, each a few words, in {first}'s \
words), never one that submits, sends, pays, books or deletes something.

Reply with JSON only: {{"asks": true, "options": ["..."]}}"""

KIND_WORDS = {"capability": "a limit of its own tools", "transient": "something passing that may clear by itself"}
_QUESTION = re.compile(r"[^.!?\n]*\?")


def _questions(reason: str) -> list[str]:
    ""
    return [" ".join(q.split()) for q in _QUESTION.findall(str(reason or "")) if len(q.split()) >= 3]


async def asks_him(task_id: str, ask=None) -> bool:
    ""






    task = await asyncio.to_thread(board.get_task, task_id)
    if not task or task.get("raw_status") != "blocked":
        return False
    reason, kind = str(task.get("raw_reason") or ""), str(task.get("block_kind") or "")
    block_at = float(task.get("filed_at") or 0)
    if kind not in blockask.QUIET_KINDS or blockask.parse(reason) or blockask.signs_in(reason, task.get("title") or ""):
        return False
    asked = _questions(reason)
    if not asked:
        return False
    row = _row(task_id)
    if block_at and abs(float(row.get("block_at") or 0) - block_at) <= 1 and row.get("asks_at"):
        return bool(row.get("ask"))
    private = _private(task)
    data = await _ask(ASKS_PROMPT.format(
        zip=_name(task.get("assignee")), first=who.first(), kind=kind, kind_words=KIND_WORDS.get(kind, kind),
        title=_flat(task.get("title"))[:200], label="Its questions" if private else "The note",
        reason=" ".join(asked)[:600] if private else _flat(reason)[:1500]), ask, 120,
        () if private else board.task_zips(task), titles=board.task_zips(task) if private else ())
    if data is not None and data.get("asks") is not True:
        _save(task_id, block_at=block_at, asks_at=block_at, ask="")
        return False
    if data is None and kind == "capability":


        _save(task_id, block_at=block_at, asks_at=block_at, ask="")
        await asyncio.to_thread(_report_limit, task, "A block marked as a limit of the Zip's tools asked a question, and "
                                "no model could say whether it is the person's to answer.", reason)
        return False
    question = _sentence(asked[-1], ASK_MAX)
    if proposes_final_action(question):
        _save(task_id, block_at=block_at, asks_at=block_at, ask="")
        return False
    options = _options((data or {}).get("options"))
    _save(task_id, block_at=block_at, asks_at=block_at, ask=question, options=json.dumps(options), more="")
    log.info("block on %s asks the person something although marked %s: a card on its first block", task_id, kind)
    return True


def _own_sentence(reason: str) -> str:
    ""
    from . import reviewcard
    first = re.split(r"(?<=[.!?])\s+", _flat(reason), maxsplit=1)[0]
    if not first or len(first.split()) > ASK_MAX_WORDS:
        return ""
    line = _sentence(reviewcard.to_you(first))
    return line if line.endswith("?") else f"{line} What should I do?"


def _hold(task: dict, row: dict, block_at: float, reason: str) -> str:
    ""


    first_time = abs(float(row.get("block_at") or 0) - block_at) > 1 or not row.get("held_at")
    _save(task["id"], block_at=block_at, ask="", held_at=time.time())
    if first_time:
        _report_limit(task, f"A board task stopped on a limit of its own tools, and the hub could not judge who can clear "
                            f"it, so it is held with no card. The Zip's note: {_flat(reason)[:400]}", reason)
    log.info("held %s with no card: no usable answer on who can clear its block", task["id"])
    return "held"


async def sweep(ask=None) -> list[str]:
    ""
    from . import board
    done = []
    try:
        groups = await asyncio.to_thread(board.grouped)
        waiting = groups["needs_you"] + groups["working"]
    except Exception:
        log.warning("could not read the board for stuck tasks", exc_info=True)
        return done
    for t in waiting:
        if _parked(t) and not t.get("card"):
            try:
                if await prepare(t["id"], ask):
                    done.append(t["id"])
            except Exception:
                log.warning("could not prepare the card for %s", t["id"], exc_info=True)
    for t in groups["needs_you"]:
        if t.get("card") and t.get("raw_status") in ("blocked", "triage") and not _explained(_row(t["id"]), t.get("filed_at")):
            try:
                await explain(t["id"], ask)
            except Exception:
                log.warning("could not write the background for %s", t["id"], exc_info=True)
    return done


async def stopped_line(task_id: str, title: str, reason: str, bot_id: str | None, ask=None) -> str | None:
    ""
    bot_id = bot_id or "default"
    if _row(task_id).get("said"):
        return None
    _save(task_id, said=1)
    data = await _ask(LINE_PROMPT.format(zip=_name(bot_id), first=who.first(), title=_flat(title)[:200],
                                         reason=_flat(reason)[:1500]), ask, 120, board.task_zips(task_id) + (bot_id,))
    line = _sentence((data or {}).get("line"), 240)
    if not line:


        return None
    from . import turns
    if turns.is_busy(bot_id):
        task = asyncio.create_task(_said_after_turn(task_id, bot_id, line, ask))
        _after_turn.add(task)
        task.add_done_callback(_after_turn.discard)
        return None
    return await _say_stopped(task_id, bot_id, line, ask)





TURN_WAIT_SEC = 15 * 60
TURN_POLL_SEC = 2
_after_turn: set[asyncio.Task] = set()

SAID_PROMPT = """{zip} is about to send {first} this line about a task it stopped on:
{line}

What {zip} already said in the chat, oldest first:
{chat}

Does the chat already give {first} this news, so the line would only repeat it? Different wording still repeats it. \
A line that adds something {first} needs to know does not.

Reply with JSON only: {{"said": true or false}}"""


async def _chat_said(task_id: str, bot_id: str, line: str, ask=None) -> bool:
    ""
    from . import recentchat
    try:
        msgs = await recentchat.recent_messages(bot_id)
    except Exception:
        log.warning("could not read the chat before a stopped line", exc_info=True)
        return False
    from . import privatewords
    said = privatewords.keep(_flat(m.get("display") or m.get("text"))[:500] for m in msgs if m.get("role") == "assistant")[-6:]
    if not said:
        return False
    data = await _ask(SAID_PROMPT.format(zip=_name(bot_id), first=who.first(), line=line,
                                         chat="\n".join(f"- {text}" for text in said)), ask, 40,
                      board.task_zips(task_id) + (bot_id,))
    return bool(data) and data.get("said") is True


async def _say_stopped(task_id: str, bot_id: str, line: str, ask=None) -> str | None:
    if await _chat_said(task_id, bot_id, line, ask):
        log.info("the chat already said why %s stopped: no second message", task_id)
        return None
    store.add_feed("message", bot_id, task_id, _name(bot_id), line, written_by="model", written_for="person")
    return line


async def _said_after_turn(task_id: str, bot_id: str, line: str, ask=None) -> None:
    from . import turns
    waited = time.monotonic()
    while turns.is_busy(bot_id) and time.monotonic() - waited < TURN_WAIT_SEC:
        await asyncio.sleep(TURN_POLL_SEC)
    try:
        await _say_stopped(task_id, bot_id, line, ask)
    except Exception:
        log.warning("could not say that %s stopped", task_id, exc_info=True)
