""















from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from . import config, home, shortreply, store, who

log = logging.getLogger("taffy.stillasks")

ENABLED = False
TIMEOUT_SEC = 25
WORDS_MAX = 2000
CHAT_CHARS = 4000
FEED_LINES = 12
WHY_MAX = 160
STILL, PASSED, UNSURE = "still", "passed", "unsure"


SINCE_KINDS = ("task_done", "task_failed", "event", "result", "action", "signin_decision")

SYSTEM = "You read a person's chat with an assistant and answer with JSON only."

ANSWERED_PROMPT = """{first} has these questions from {zip} still open in the chat, each with options to tap:
{cards}

Then {first} typed this message in the same chat instead of tapping:
{words}

Which of the questions does the message answer, decide, or make unnecessary (it picks or names an option, gives the \
answer in other words, or says to drop it)? A message about something else answers none of them. Leave out any you \
are not sure about.

Reply with JSON only: {{"answered": [question numbers]}}"""

CHECK_PROMPT = """{zip} asked {first} a question on {when}, and {first} has not answered it yet.

Question: {question}
Options: {options}
{why}{date}
What happened after it was asked, oldest first:
{since}

Is the question settled without {first}? It is settled only when what it asked about was done, sent, paid or \
withdrawn, or its moment passed (a post whose time slot is gone, an event that is over, a choice someone else already \
made). Being old, or {first} being busy, does not settle it. When you are not sure, say null.

If it is settled, write "why": one short plain sentence to {first}, in the first person as {zip} (never saying the \
name {zip}), saying what happened, for example "The 12:15 post went out without this change." No ids, no dates in \
numbers like 2026-11-09.

Reply with JSON only: {{"settled": true, false or null, "why": "..."}}"""


@dataclass
class Verdict:
    state: str
    why: str = ""


def _parse(answer: str) -> dict | None:
    m = re.search(r"\{.*\}", answer or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _ask(prompt: str, ask, max_tokens: int) -> dict | None:
    if ask is None:
        if not ENABLED:
            return None
        from . import shortreply
        ask = shortreply.ask_helper
    from . import ownerrules
    prompt += ownerrules.block(ownerrules.QUESTION, prompt)
    try:
        answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=max_tokens),
                                        TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no helper answer about a waiting question (%s)", type(e).__name__)
        return None
    return _parse(answer)


def _name(bot_id: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id or "default")
    return bot.name if bot else "Zero"


def _flat(text) -> str:
    return " ".join(str(text or "").split())


async def answered(bot_id: str, questions: list[dict], words: str, ask=None) -> set[int]:
    ""

    words = _flat(words)[:WORDS_MAX]
    if not questions or not words:
        return set()
    cards = "\n".join(f"{i}. {_flat(q.get('question'))[:200]} (options: "
                      f"{' / '.join(_flat(o)[:80] for o in q.get('options') or [])})"
                      for i, q in enumerate(questions, 1))
    with shortreply.reading_text(f"{cards}\n{words}", (bot_id,)):
        data = await _ask(ANSWERED_PROMPT.format(first=who.first(), zip=_name(bot_id), cards=cards, words=words), ask, 80)
    found = data.get("answered") if data else None
    if not isinstance(found, list):
        return set()
    return {int(n) - 1 for n in found if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(questions)}


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, home.TZ).strftime("%A, %b %-d at %-I:%M %p").replace("AM", "a.m.").replace(
        "PM", "p.m.")


def clean_why(text) -> str:
    ""
    why = _flat(text).strip("\"'“”")
    if len(why) > WHY_MAX:
        why = why[:WHY_MAX].rsplit(" ", 1)[0].rstrip(",;:-")
    if why and why[-1] not in ".!?":
        why += "."
    return why[:1].upper() + why[1:]


async def happened_since(bot_id: str, at: float, skip_feed: int | None = None) -> str:
    ""

    from . import hermes, history, recentchat
    parts = []
    try:
        if store.session_for(bot_id):
            msgs = history.visible(await asyncio.wait_for(hermes.messages(bot_id, limit=60), 8))
            said = recentchat.lines([m for m in msgs if isinstance(m.get("ts"), (int, float)) and m["ts"] > at],
                                    CHAT_CHARS)
            if said:
                parts.append(f"The chat (lines marked You are {_name(bot_id)}):\n{said}")
    except Exception:
        log.info("could not read %s's chat for a waiting question", bot_id, exc_info=True)
    rows = store.db().execute(
        f"SELECT kind, title, created_at FROM feed WHERE bot_id=? AND created_at>? AND id IS NOT ? "
        f"AND kind IN ({','.join('?' * len(SINCE_KINDS))}) ORDER BY id DESC LIMIT ?",
        (bot_id, at, skip_feed, *SINCE_KINDS, FEED_LINES)).fetchall()
    done = [f"- {_when(r['created_at'])}: {_flat(r['title'])[:160]}" for r in reversed(rows) if _flat(r["title"])]
    if done:
        parts.append("Recorded by the app:\n" + "\n".join(done))
    return "\n\n".join(parts) or "(nothing recorded)"


def chat_zips(bot_id: str, at: float) -> tuple[str, ...]:
    ""


    from . import team
    rows = store.db().execute("SELECT bot_id, body, task_id FROM feed WHERE bot_id=? AND kind='team' AND created_at>?",
                              (bot_id, at)).fetchall()
    return tuple(sorted({bot_id, *(z for r in rows for z in team.message_zips(dict(r)))}))


async def check(bot_id: str, question: str, options: list, asked_at: float, why: str = "",
                deadline: float | None = None, since: str | None = None, ask=None) -> Verdict:
    ""

    if since is None:
        since = await happened_since(bot_id, asked_at)



    from . import privatewords
    since, why = privatewords.leave_out(since) or "(nothing recorded)", privatewords.leave_out(why)
    if privatewords.holds(f"{question}\n{' / '.join(map(str, options or []))}"):
        return Verdict(UNSURE)
    with shortreply.reading(await asyncio.to_thread(chat_zips, bot_id, asked_at)):
        data = await _ask(CHECK_PROMPT.format(
            zip=_name(bot_id), first=who.first(), when=_when(asked_at), question=_flat(question)[:200],
            options=" / ".join(_flat(o)[:80] for o in options or []) or "(none)",
            why=f"Why it was asked: {_flat(why)[:500]}\n" if _flat(why) else "",
            date=f"The date it was about: {_when(deadline)}\n" if deadline else "", since=since), ask, 120)
    if not data:
        return Verdict(UNSURE)
    if data.get("settled") is True:
        line = clean_why(data.get("why"))
        return Verdict(PASSED, line) if line else Verdict(UNSURE)
    return Verdict(STILL if data.get("settled") is False else UNSURE)
