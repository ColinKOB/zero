""



















from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time

from . import config, shortreply, store

log = logging.getLogger("taffy.reviewwords")

ENABLED = False
EVERY_SEC = 20
PER_TICK = 3
TIMEOUT_SEC = 25
ASK_MAX, LINE_MAX = 80, 160
NO_MAX = 24

SYSTEM = "You write the question at the top of an approval card, and one line under it. Reply with JSON only."
PROMPT = """A helper finished a task and waits for the person's okay. Write what their approval card says.

The card's button is Approve. It lets the helper go ahead. It never sends, posts or publishes anything itself.

- "ask": the question the person answers, in their own everyday words, under 10 words, ending in "?"
  (for example "Go ahead with the hiring team follow-up?" or "Okay the new Halloween page?").
  Never start it with Send, Post, Publish, Submit, Email or Text.
- "line": one plain sentence on what they are approving, under 25 words.
- "message": true when the work is a message (an email or a text) the helper wrote for someone else, else false.
- "step": true only when this approval is a step between helpers: a review of a review, an implementation "for review",
  a plan or a repair that is not ready to reach customers, where nothing goes out and the helpers can settle it
  themselves. When you are not sure, false.

When the work is software (a fix, a feature, a release), the person does not code, so write for someone who runs the
product:
- "ask" is one yes about what customers get, like "Ship the fix that stops finished episodes being rejected?"
- "line" says what will be different for customers, or "Nothing customers see; it prevents" and what it prevents.
- "checked": one plain sentence on what was checked, under 20 words, with no test counts.
- "risk": one plain sentence on what could go wrong, under 20 words.
- "no": two or three words that turn it down, named for the work, like "Hold the Fix".
Leave "checked", "risk" and "no" empty for other work. No test counts, file names, tool names or engineering terms.

Never include email addresses, ids, codes, counts of mailboxes or messages, file paths, or anything the helper wrote
for itself ("internal", "not authorization", tool names). Speak to the person as "you".

Task: {title}
The helper's note: {note}

Reply with JSON: {{"ask": "...", "line": "...", "message": false, "step": false, "checked": "", "risk": "", "no": ""}}"""

_pending: dict[str, tuple[str, str, tuple]] = {}
_generation = 0
_BAD = re.compile(r"@|\b[0-9a-f]{8,}\b|\binternal\b|\bauthoriz", re.I)
_SENDS = re.compile(r"^\W*(?:send|post|publish|submit|email|text|mail|reply)\b", re.I)
VERSION = "3"


def cancel_writes() -> None:
    ""
    global _generation
    with store._lock:
        _generation += 1
        _pending.clear()


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS review_words (key TEXT PRIMARY KEY, ask TEXT NOT NULL, line TEXT NOT NULL, "
              "at REAL NOT NULL)")
    if "message" not in {r[1] for r in c.execute("PRAGMA table_info(review_words)")}:
        c.execute("ALTER TABLE review_words ADD COLUMN message INTEGER NOT NULL DEFAULT 0")
    if "details" not in {r[1] for r in c.execute("PRAGMA table_info(review_words)")}:
        c.execute("ALTER TABLE review_words ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")


def key(title: str, note: str) -> str:
    return hashlib.sha256(f"{VERSION}\n{title}\n{note}".encode()).hexdigest()[:32]


def get(title: str, note: str, zips: tuple = ()) -> tuple[str, str, bool] | None:
    ""


    zips = (zips,) if isinstance(zips, str) else tuple(zips or ())
    if shortreply.withheld(*zips):
        return None
    k = key(title, note)
    row = store.db().execute("SELECT ask, line, message FROM review_words WHERE key=?", (k,)).fetchone()
    if row:
        return (row["ask"], row["line"], bool(row["message"])) if row["ask"] else None
    _pending[k] = (title, note[:3000], tuple(zips))
    return None


def details(title: str, note: str) -> dict:
    ""
    row = store.db().execute("SELECT details FROM review_words WHERE key=?", (key(title, note),)).fetchone()
    try:
        return json.loads(row["details"]) if row else {}
    except ValueError:
        return {}


def _parsed(answer: str | None) -> dict | None:
    try:
        data = json.loads(str(answer or "").strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def extras(answer: str | None) -> dict:
    ""
    data = _parsed(answer) or {}
    out = {}
    for field, limit in (("checked", LINE_MAX), ("risk", LINE_MAX), ("no", NO_MAX)):
        words = " ".join(str(data.get(field) or "").split())
        if words and len(words) <= limit and not _BAD.search(words):
            out[field] = words
    if data.get("step") is True and data.get("message") is not True:
        out["step"] = True
    return out


def good(answer: str | None) -> tuple[str, str, bool] | None:
    data = _parsed(answer)
    if data is None:
        return None
    ask = " ".join(str(data.get("ask") or "").split())
    line = " ".join(str(data.get("line") or "").split())
    if not ask.endswith("?") or not 4 <= len(ask) <= ASK_MAX or len(line) > LINE_MAX or _BAD.search(ask + " " + line) \
            or _SENDS.match(ask):
        return None
    return ask, line, data.get("message") is True


async def _answer(title: str, note: str, ask=None, *, zips) -> str | None:
    from . import ownerrules, shortreply
    ask = ask or shortreply.ask_helper
    prompt = PROMPT.format(title=title, note=note) + ownerrules.block(ownerrules.WORDS, f"{title} {note}", answered=False)
    try:
        with shortreply.reading_text(f"{title}\n{note}", zips):
            return await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=320),
                                          TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no review words for a task (%s)", type(e).__name__)
        return None


async def write(title: str, note: str, ask=None, *, zips) -> tuple[str, str, bool] | None:
    ""
    answer = await _answer(title, note, ask, zips=zips)
    return None if answer is None else good(answer) or ("", "", False)


def save(k: str, words: tuple[str, str, bool], generation: int | None = None, more: dict | None = None) -> None:
    with store._lock:
        if generation is not None and generation != _generation:
            return
        store.db().execute("INSERT OR REPLACE INTO review_words(key, ask, line, message, at, details) VALUES (?,?,?,?,?,?)",
                           (k, words[0], words[1], int(bool(words[2])), time.time(), json.dumps(more or {})))
        store.db().commit()


async def written(title: str, note: str, ask=None, *, zips) -> dict:
    ""
    k = key(title, note)
    if not store.db().execute("SELECT 1 FROM review_words WHERE key=?", (k,)).fetchone():
        generation = _generation
        answer = await _answer(title, note[:3000], ask, zips=zips)
        if answer is None:
            return {}
        _pending.pop(k, None)
        await asyncio.to_thread(save, k, good(answer) or ("", "", False), generation, extras(answer))
    return details(title, note)


async def write_due(ask=None, limit: int = PER_TICK) -> int:
    done, generation = 0, _generation
    for k, (title, note, zips) in list(_pending.items())[:limit]:
        if generation != _generation:
            break
        _pending.pop(k, None)
        answer = await _answer(title, note, ask, zips=zips)
        if answer is not None:
            words = good(answer) or ("", "", False)
            await asyncio.to_thread(save, k, words, generation, extras(answer))
            done += bool(words[0])
    return done


async def run() -> None:
    while True:
        await asyncio.sleep(EVERY_SEC)
        if ENABLED and _pending:
            try:
                await write_due()
            except Exception:
                log.warning("could not write review words", exc_info=True)
