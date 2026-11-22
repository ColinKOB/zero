""




















from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from . import shortreply, store, zipvoice

log = logging.getLogger("taffy.teamvoice")

ENABLED = False
TIMEOUT_SEC = 15
ATTEMPTS = 2
MAX_CHARS = 700
KINDS = ("ask", "answer", "reply", "tell")

SYSTEM = "You rewrite one message between two coworkers so it reads naturally. Answer with the rewritten message only."

PROMPT = zipvoice.TEXT + "\n\n" + """{sender} and {receiver} are two members of {person}'s team of assistants. {sender} wrote this \
{what} to {receiver}. {person} reads it in a chat and should hear two coworkers, not a technical log.

Rewrite it the way a friendly coworker would say it out loud, in {sender}'s voice:
- {sender} is the one talking, so {sender} says "I", "my" and "me" about itself, never "{sender}" or \
"{sender}'s". {receiver} is the one listening, so call {receiver} "you". For example, "My notes say" stays "My notes \
say", never "{sender}'s notes say".
- {shape}
- Use only facts in the message. Never add, guess or change anything. A first name already in the message is fine. Omit redundant full dates. Keep every number, date, time, link and \
address you mention exactly as written. You may leave out technical detail {person} does not need, such as tool \
names, file paths, step lists and instructions about format.
- Use the chat voice in the guide above. No lists, headings, markdown, em dashes or emojis.
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
    ""



    from . import who
    plain = (plain or "").strip()
    if not plain:
        return "empty"
    if names and who.names_itself(plain, names[0], original):
        return f'"{names[0]}" where {names[0]} should say "I"'
    if len(names) > 1 and who.names_itself(re.sub(rf"^Hey {re.escape(names[1])},", "", plain), names[1], original):
        return f'"{names[1]}" where it should say "you"'
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
    ""
    ask = ask or shortreply.ask_quick
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
    ""
    if not ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(apply(feed_id, ask))


async def apply(feed_id: int, ask=None) -> str | None:
    ""
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
            with shortreply.reading_message(body):
                job = asyncio.ensure_future(rewrite(kind, name(body.get("from", "")), name(body.get("to", "")),
                                                    who.first(), text, ask))
            _running[key] = job
        try:
            _done[key] = await job
        finally:
            _running.pop(key, None)
    plain = _done[key]
    changed = False
    if plain and plain != text.strip():
        body["plain"] = plain
        changed = True
    if kind == "tell" and not body.get("title"):
        if key not in _titles:
            with shortreply.reading_message(body):
                _titles[key] = await short_title(text, plain or "", ask)
        if _titles[key]:
            body["title"] = _titles[key]
            changed = True
    if changed:
        store.update_feed(feed_id, row["title"], json.dumps(body))
    return plain







TITLE_SYSTEM = "You name what one message between two coworkers is about. Answer with the short title only."
TITLE_PROMPT = """Name what this heads-up between two coworkers is about in at most six words, the way a chat list \
names a thread, such as "Pageview events before the Nov 2 posts" or "Jake sent links instead of text".
- Use only words from the heads-up. No period, quotes, em dashes or emojis.

The heads-up:
<message>
{text}
</message>"""

_titles: dict[str, str | None] = {}


async def short_title(text: str, plain: str = "", ask=None) -> str | None:
    ""
    ask = ask or shortreply.ask_quick
    prompt = TITLE_PROMPT.format(text=(plain or text).strip())
    why = None
    for _ in range(ATTEMPTS):
        p = prompt if why is None else prompt + f"\n\nYour last title was refused because it had {why}. Try again."
        try:
            title = (await ask(p, timeout=TIMEOUT_SEC, system=TITLE_SYSTEM)).strip().strip('"').rstrip(".").strip()
        except Exception as e:
            log.info("heads-up title failed: %s", type(e).__name__)
            return None
        why = label_faithful(text + "\n" + plain, title)
        if why is None:
            return title
        log.info("heads-up title refused: %s", why)
    return None




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
    ""

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
    ask = ask or shortreply.ask_quick
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
    ""
    if not ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(apply_step(feed_id, ask))


_labels: dict[str, str | None] = {}


async def apply_step(feed_id: int, ask=None) -> str | None:
    row = store.db().execute("SELECT kind, title, body, plain_title, bot_id FROM feed WHERE id = ? AND step_of IS NOT NULL",
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
        with shortreply.reading((row['bot_id'],)):
            _labels[text] = await short_label(text, ask)
    label = _labels[text]
    if label:
        store.set_plain_title(feed_id, label)
    return label
