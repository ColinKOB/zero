"""Zip-to-Zip messages as two coworkers would say them (Oct 12).

The Zips keep talking to each other in their own precise way underneath: a question to a teammate and its answer go
between them word for word. What the person sees in a chat is a plain version of each, written by Haiku on the home's
own plan (shortreply.ask_haiku: the Claude Code CLI, never a paid API, usage logged by usage.log_cli). For example,
"Check elearning.lakeview.test for assignments due 10/12 to 10/13 and confirm whether the 10/11 submission posted" shows
as "Hey School, can you check eLearning for anything due today or tomorrow, and whether last night's work went in?"
only if every fact in it is in the original.

The plain version is kept with the message (the team feed item's body gains "plain"; "text" stays the original), so the
app shows it and Show Original shows the exact words. A rewrite that fails `faithful` is asked for once more with the
reason; one that still fails, or Haiku slow or erroring, leaves the original showing. The person's own messages are
never rewritten: only team feed items go through here.

The same pass gives each step of an answer (a sign-in or action marked with feed.step_of) a short label for the
work log: "Signed in to elearning.lakeview.test" becomes "Signed in to eLearning". Every word of the label must come from
the step's own text (`label_faithful`), and the raw title stays in the feed item for Show Original (feed.plain_title).

Runs only in the live hub (ENABLED, turned on by app.lifespan like extract.ENABLED), so tests never reach Claude.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from . import shortreply, store

log = logging.getLogger("taffy.teamvoice")

ENABLED = False
TIMEOUT_SEC = 15
ATTEMPTS = 2
MAX_CHARS = 700
KINDS = ("ask", "answer", "reply", "tell")

SYSTEM = "You rewrite one message between two coworkers so it reads naturally. Answer with the rewritten message only."

PROMPT = """{sender} and {receiver} are two members of {person}'s team of assistants. {sender} wrote this \
{what} to {receiver}. {person} reads it in a chat and should hear two coworkers, not a technical log.

Rewrite it the way a friendly coworker would say it out loud, in {sender}'s voice:
- {shape}
- Use only facts in the message. Never add, guess or change anything. Keep every name, number, date, time, link and \
address you mention exactly as written. You may leave out technical detail {person} does not need, such as tool \
names, file paths, step lists and instructions about format.
- Plain complete sentences, as short as the facts allow. No lists, headings, markdown, em dashes or emojis.
- Write only the new message.

The {what}:
<message>
{text}
</message>"""

SHAPE = {
    "ask": "Make it one short question or request to {receiver}, starting with \"Hey {receiver},\". Keep it a question.",
    "tell": "Make it a short heads-up to {receiver}, starting with \"Hey {receiver},\". Ask nothing.",
    "answer": "Make it a short, direct answer. Start with the answer itself, with no greeting. Ask nothing back unless "
              "the message does.",
}

_DASH = re.compile("[—–]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿]")


def faithful(original: str, plain: str, names: tuple[str, ...] = (), kind: str = "answer") -> str | None:
    """Why the plain version cannot stand in for the original, or None when it can (shortreply.faithful's checks for
    facts, without its length rule: a question can be as long as it needs)."""
    plain = (plain or "").strip()
    if not plain:
        return "empty"
    if len(plain) > MAX_CHARS or len(plain) > 1.3 * len(original) + 80:
        return "too long"
    if "```" in plain or any(shortreply._LIST_ITEM.match(line) for line in plain.splitlines()):
        return "a list or code"
    if _DASH.search(plain):
        return "an em dash"
    if _EMOJI.search(plain):
        return "an emoji"
    source = original + "\n" + " ".join(names)
    if shortreply._numbers(plain) - shortreply._numbers(source):
        return "a number not in the original"
    links = set(shortreply._URL.findall(plain)) | set(shortreply._PATH.findall(plain))
    if links - (set(shortreply._URL.findall(original)) | set(shortreply._PATH.findall(original))):
        return "a link not in the original"
    if {e.lower() for e in shortreply._EMAIL.findall(plain)} - {e.lower() for e in shortreply._EMAIL.findall(source)}:
        return "an address not in the original"
    lower = source.lower()
    for sentence in re.split(r"(?<=[.!?,])\s+", shortreply._URL.sub(". ", plain)):
        for m in shortreply._CAP_WORD.finditer(sentence):
            word = re.sub(r"['’]s$", "", m.group(0)).rstrip("'’-")
            low = word.lower().replace("’", "'")
            if low in shortreply._STARTERS or low in ("hey", "i", "i'm", "i'll", "i've", "i'd") or low in lower:
                continue
            return f'"{word}" is not in the original'
    if kind == "ask" and "?" not in plain and "?" in original:
        return "the question was left out"
    if kind != "ask" and "?" in shortreply._URL.sub(" ", plain) and "?" not in original:
        return "a question the message does not ask"
    return None


def _what(kind: str) -> str:
    return {"ask": "question", "tell": "heads-up"}.get(kind, "answer")


async def rewrite(kind: str, sender: str, receiver: str, person: str, text: str, ask=None) -> str | None:
    """The plain version of one team message, or None to keep the original."""
    ask = ask or shortreply.ask_haiku
    shape_kind = "ask" if kind == "ask" else "tell" if kind == "tell" else "answer"
    prompt = PROMPT.format(sender=sender, receiver=receiver, person=person, what=_what(shape_kind),
                           shape=SHAPE[shape_kind].format(receiver=receiver), text=text.strip())
    why = None
    for _ in range(ATTEMPTS):
        p = prompt if why is None else (prompt + f"\n\nYour last version was refused because it had {why}. "
                                        "Write it again without that.")
        try:
            plain = (await ask(p, timeout=TIMEOUT_SEC, system=SYSTEM)).strip().strip('"').strip()
        except Exception as e:
            log.info("team rewrite failed: %s", type(e).__name__)
            return None
        why = faithful(text, plain, (sender, receiver, person), shape_kind)
        if why is None:
            return plain
        log.info("team rewrite refused: %s", why)
    return None


_done: dict[str, str | None] = {}
_running: dict[str, asyncio.Task] = {}


def _key(kind: str, sender: str, receiver: str, text: str) -> str:
    role = "ask" if kind == "ask" else "tell" if kind == "tell" else "answer"
    return hashlib.sha256(f"{role}\n{sender}\n{receiver}\n{text}".encode()).hexdigest()


def schedule(feed_id: int, ask=None) -> None:
    """After a team feed item is written: rewrite it in the background and add "plain" to its body."""
    if not ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(apply(feed_id, ask))


async def apply(feed_id: int, ask=None) -> str | None:
    """Rewrites one team feed item and saves the plain version beside the original. Returns it, or None."""
    from . import config, who
    row = store.db().execute("SELECT title, body FROM feed WHERE id = ? AND kind = 'team'", (feed_id,)).fetchone()
    if not row:
        return None
    try:
        body = json.loads(row["body"])
    except (TypeError, ValueError):
        return None
    kind, text = body.get("kind"), str(body.get("text") or "")
    if kind not in KINDS or not text.strip() or body.get("plain"):
        return body.get("plain")

    def name(bot_id: str) -> str:
        b = config.BOTS_BY_ID.get(bot_id)
        return b.name if b else bot_id
    key = _key(kind, body.get("from", ""), body.get("to", ""), text)
    if key not in _done:
        job = _running.get(key)
        if job is None:
            job = asyncio.ensure_future(rewrite(kind, name(body.get("from", "")), name(body.get("to", "")),
                                                who.first(), text, ask))
            _running[key] = job
        try:
            _done[key] = await job
        finally:
            _running.pop(key, None)
    plain = _done[key]
    if plain and plain != text.strip():
        body["plain"] = plain
        store.update_feed(feed_id, row["title"], json.dumps(body))
    return plain


# MARK: Step labels

LABEL_SYSTEM = "You shorten one line from a work log. Answer with the short line only."
LABEL_PROMPT = """This line says what an assistant did while answering a teammate's question. Rewrite it as a short, \
friendly label of at most six words that a person reads at a glance, such as "Signed in to eLearning" for \
"Signed in to elearning.example.edu to answer a teammate" or "Checked Mail" for "Checked Mail for notes from \
instructors".
- Use only words from the line. You may turn a web address into the site's name, written the way people write it.
- Keep the verb in the past tense. No period, quotes, em dashes or emojis.

The line:
<line>
{text}
</line>"""

LABEL_MAX_WORDS = 6
_WORD = re.compile(r"[A-Za-z0-9]+")


def label_faithful(original: str, label: str) -> str | None:
    """Why the short label cannot stand in for the step, or None when it can: every word must come from the original
    (a site's name may be part of its address: "eLearning" from "elearning.lakeview.test")."""
    label = (label or "").strip()
    if not label:
        return "empty"
    words = _WORD.findall(label)
    if not words or len(words) > LABEL_MAX_WORDS:
        return "too long"
    if _DASH.search(label) or _EMOJI.search(label) or "\n" in label:
        return "an em dash, an emoji or a line break"
    source = original.lower()
    for w in words:
        if w.lower() not in source:
            return f'"{w}" is not in the original'
    return None


async def short_label(text: str, ask=None) -> str | None:
    ask = ask or shortreply.ask_haiku
    prompt = LABEL_PROMPT.format(text=text.strip())
    why = None
    for _ in range(ATTEMPTS):
        p = prompt if why is None else prompt + f"\n\nYour last label was refused because it had {why}. Try again."
        try:
            label = (await ask(p, timeout=TIMEOUT_SEC, system=LABEL_SYSTEM)).strip().strip('"').rstrip(".").strip()
        except Exception as e:
            log.info("step label failed: %s", type(e).__name__)
            return None
        why = label_faithful(text, label)
        if why is None:
            return label
        log.info("step label refused: %s", why)
    return None


def schedule_step(feed_id: int, ask=None) -> None:
    """After a step feed item is written: give it a short label in the background (feed.plain_title)."""
    if not ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(apply_step(feed_id, ask))


_labels: dict[str, str | None] = {}


async def apply_step(feed_id: int, ask=None) -> str | None:
    row = store.db().execute("SELECT kind, title, body, plain_title FROM feed WHERE id = ? AND step_of IS NOT NULL",
                             (feed_id,)).fetchone()
    if not row:
        return None
    if row["plain_title"]:
        return row["plain_title"]
    text = row["body"] if (row["title"] or "").startswith("Done") or not row["title"] else row["title"]
    text = str(text or "").strip()
    if not text:
        return None
    if text not in _labels:
        _labels[text] = await short_label(text, ask)
    label = _labels[text]
    if label:
        store.set_plain_title(feed_id, label)
    return label
