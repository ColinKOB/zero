""





























from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time

from . import chatter, shortreply, store, who

log = logging.getLogger("taffy.buzz")

SUBJECT = "phone buzz"









GUIDE = (
    "You decide whether this message also buzzes {first}'s phone. It shows in the chat either way, and {first} reads "
    "the chat between other jobs. Buzz when {first} would thank you for the interruption, the way a top executive "
    "assistant walks in:\n"
    "- It needs {first}: a question, an okay, a sign-in, a choice or a step only {first} can do, and no card under the "
    "message already asks it.\n"
    "- A result {first} asked for or is waiting on, or news {first} would want today even unasked: a refund or payment "
    "that came through, a confirmation that settles a worry, a draft that is ready to send, work that went live.\n"
    "- Something due today or tomorrow, a money, account or billing problem, something that broke, or a change to "
    "{first}'s plans (someone declined a meeting, a delivery moved).\n"
    "Stay quiet when the message:\n"
    "- repeats what {first} already heard in this chat today, or what a card under it already asks;\n"
    "- reports progress, partial work, or what you will do next or later;\n"
    "- confirms what {first} just did, or says nothing changed;\n"
    "- is routine numbers, ideas, drafts or a report with no date to act on (the week's post ideas, client hours to "
    "total sometime, a weekly report that only offers more).\n"
    "End the message with one line of its own: [NOTIFY] and one short sentence for the lock screen in your own voice, "
    "or [QUIET]."
)
RULES_HEAD = "{first}'s own buzz rules come first:"

RULE_GUIDE = (
    "When {first} tells you what should or shouldn't buzz {own} phone (\"don't buzz me for post ideas\"), save it "
    "with save_fact as a preference with the subject \"" + SUBJECT + "\", in {own} terms, replacing an older buzz "
    "rule it changes. Then confirm once in one short sentence, such as \"Got it. I won't buzz you for post ideas.\" "
    "Every Zip follows these rules from then on."
)

RULE_WORDS = re.compile(r"\b(buzz\w*|notif\w*|ding\w*|ping me|pinging me|alert me|lock ?screen|my phone)\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS buzz_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    bot_id TEXT NOT NULL,
    feed_id INTEGER,          -- the chat row the message is, when there is one
    source TEXT NOT NULL,     -- asked, zip, report (a scheduled job, notify.judge_report) or hub (the hub's own alert)
    choice TEXT NOT NULL,     -- buzz or quiet
    reason TEXT NOT NULL,     -- why, in a word or two: asked, marked, unmarked, urgent words, a report's reason, a kind
    note TEXT,                -- the Zip's own few words after [QUIET], at most NOTE_CHARS
    outcome TEXT NOT NULL     -- quiet, sent, held (Quiet Hours), off (the Zip's switch), snoozed, unsent
);
CREATE INDEX IF NOT EXISTS buzz_log_at ON buzz_log (at);
"""
NOTE_CHARS = 80
KEEP_DAYS = 60


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)




def rules(bot_id: str | None = None) -> list[str]:
    ""

    from . import facts
    holds, now_arg = facts.live()
    where, args = facts._visible(bot_id)
    try:
        rows = facts._db().execute(
            f"SELECT text FROM facts WHERE {holds} AND {where} AND kind='preference' AND "
            "(subject LIKE '%buzz%' OR subject LIKE '%notif%') ORDER BY id", now_arg + args).fetchall()
    except sqlite3.Error:
        log.warning("could not read the buzz rules", exc_info=True)
        return []
    return [r["text"] for r in rows]


def guide(bot_id: str | None = None) -> str:
    ""

    from . import todo
    first = who.first()
    out = GUIDE.format(first=first)
    said = rules(bot_id)
    if said:
        out += "\n" + RULES_HEAD.format(first=first) + "\n" + "\n".join(f"- {r}" for r in said)
    return out + "\n" + todo.guide()


def rule_guide() -> str:
    return RULE_GUIDE.format(first=who.first(), own=who.possessive())




def take(bot_id: str, reply: str | None) -> tuple[str | None, tuple[str, str] | None]:
    ""

    from . import hermes
    mark = hermes.last_buzz.pop(bot_id, None)
    own = chatter.buzz_mark(reply)
    if own:
        reply = chatter.without_buzz_mark(reply) or chatter.SILENT
    return reply, mark or own


def decide(bot_id: str, text: str, mark: tuple[str, str] | None = None, asked: bool = False, card: str = "") -> dict:
    ""




    from . import notify
    if asked:
        return {"buzz": True, "source": "asked", "reason": "asked", "line": None, "note": None}
    if mark:
        choice, words = mark
        if choice == "notify":
            return {"buzz": True, "source": "zip", "reason": "marked", "line": words or None, "note": None}
    if card:
        return {"buzz": True, "source": "zip", "reason": "card", "line": notify.fit(card), "note": None}
    if mark:
        choice, words = mark
        return {"buzz": False, "source": "zip", "reason": "marked", "line": None, "note": words[:NOTE_CHARS] or None}
    if notify.URGENT.search(notify.plain(text)):
        return {"buzz": True, "source": "zip", "reason": "urgent words", "line": None, "note": None}
    return {"buzz": False, "source": "zip", "reason": "unmarked", "line": None, "note": None}




RULE_PROMPT = """{first} set these rules for what buzzes {first}'s phone:
{rules}

The hub is about to buzz {first}'s phone with this notification from {name}:
<notification>
{text}
</notification>

List each separate thing this notification tells {first}. For each, give the number of the rule that plainly says {first} \
does not want a buzz for that very thing, or 0. A rule about one subject covers only that subject.

Reply with JSON only: {{"things": [{{"what": "a few words", "rule": 0}}]}}"""


async def ruled_out(bot_id: str | None, title: str, body: str) -> str | None:
    ""


    from . import notify, ownerrules

    from . import privatewords
    said = privatewords.keep(dict.fromkeys([*rules(bot_id), *ownerrules.texts(ownerrules.BUZZ, f"{title} {body}", bot_id,
                                                                               always=False)]))
    if not said:
        return None
    if not notify._haiku_ok():
        return None
    _, name = notify.sender(bot_id)
    first = who.first()
    prompt = RULE_PROMPT.format(first=first, name=name, rules="\n".join(f"{i}. {r}" for i, r in enumerate(said, 1)),
                                text=notify.plain(f"{title}: {body}")[:1500])
    notify._spend()
    try:
        with shortreply.reading_text(f"{title}: {body}", (bot_id,)):
            answer = await asyncio.wait_for(notify.ask_haiku(prompt), 15)
    except Exception as e:
        log.warning("could not check an alert against the buzz rules (%s)", type(e).__name__)
        return None


    m = re.search(r"\{.*\}", str(answer or ""), re.S)
    try:
        things = json.loads(m.group(0)).get("things") if m else None
    except (ValueError, AttributeError):
        things = None
    picked = [x.get("rule") if isinstance(x, dict) else 0 for x in things] if isinstance(things, list) else []
    if not picked or not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(said) for n in picked):
        return None
    return said[picked[0] - 1]




def record(bot_id: str | None, decision: dict, outcome: str, feed_id: int | None = None) -> None:
    ""
    try:
        with store._lock:
            c = store.db()
            c.execute("INSERT INTO buzz_log (at, bot_id, feed_id, source, choice, reason, note, outcome) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (time.time(), bot_id or "default", feed_id if feed_id is None else int(feed_id),
                       str(decision.get("source") or "hub"), "buzz" if decision.get("buzz") else "quiet",
                       str(decision.get("reason") or "")[:40], (decision.get("note") or None), outcome))
            c.execute("DELETE FROM buzz_log WHERE at < ?", (time.time() - KEEP_DAYS * 86400,))
            c.commit()
    except Exception:
        log.warning("could not log a buzz decision", exc_info=True)
    log.info("%s %s from %s (%s, %s): %s", "buzz" if decision.get("buzz") else "quiet", feed_id or "-",
             bot_id or "default", decision.get("source") or "hub", decision.get("reason") or "", outcome)


def summary(days: float = 7) -> dict:
    ""

    since = time.time() - days * 86400
    c = store.db()
    out = {"days": days, "total": c.execute("SELECT COUNT(*) FROM buzz_log WHERE at >= ?", (since,)).fetchone()[0]}
    for col in ("choice", "outcome", "source", "reason", "bot_id"):
        out[f"by_{col.removesuffix('_id')}"] = {
            r[0]: r[1] for r in c.execute(f"SELECT {col}, COUNT(*) FROM buzz_log WHERE at >= ? GROUP BY {col} "
                                          "ORDER BY COUNT(*) DESC", (since,))}
    return out


def for_feed(feed_ids: list[int]) -> dict[int, str]:
    ""
    ids = [int(i) for i in feed_ids if i is not None]
    if not ids:
        return {}
    rows = store.db().execute(f"SELECT feed_id, choice FROM buzz_log WHERE feed_id IN ({','.join('?' * len(ids))}) "
                              "ORDER BY id", ids).fetchall()
    return {r[0]: r[1] for r in rows}
