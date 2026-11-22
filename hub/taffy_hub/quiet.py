""
































from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time

from . import config, store

log = logging.getLogger("taffy.quiet")

SHOW, FOLD, HIDE = "show", "fold", "hide"
ENABLED = False
EVERY_SEC = 15
PER_TICK = 6
LOOK_BACK_SEC = 3 * 86400
RELAY_AFTER_SEC = 180
RETRY_SEC = 1800
TIMEOUT_SEC = 25
TITLE_WORDS = 7
TITLE_MAX = 60



FOR, MACHINERY, SCREEN, NEWS = "for", "machinery", "screen", "news"


UNDONE = "undone"


ROUTED = "routed"


RETIRED = "retired"

PERSON = {"you", "colin", "person", "user", "me"}

TEAM_ASK = "ask"

OPEN = {"open", "sending", "pending", "asking", "waiting"}
CLOSED = {"dismissed", "expired", "closed", "replaced", "retired", "moved", "declined", "unreachable", "handled",
          "stopped", "done", "signed_in", "connected", "allowed", "cancelled", "canceled", "failed"}

ALWAYS = {"result", "image", "call", "project", "wallet_pay", "wallet_receipt", "team_proposal", "message",
          "message_local", "task_blocked", "task_review", "task_failed", "todo_card", "handoff"}

NO_ANSWER = "No answer in time"

ANSWERED_IN_CHAT = "Answered from the chat: "
ENDED = {"not_sent": "Email not sent: {subject}", "answered": ANSWERED_IN_CHAT + "{subject}"}

JUDGED = {"checkin", "task_done", "action", "event", "team", "mac", "signin_decision"}



TEAM_TITLE = "Passed a note to a teammate"
NOTE_TITLE = "Saved a note"


_IDS = re.compile(r"\(?\b(?:made_[0-9a-z]+|t_[0-9a-z]{4,}|(?=[0-9a-f]*\d)[0-9a-f]{8,}|[A-Za-z]+_[A-Za-z0-9_]*\d[A-Za-z0-9_]*|"
                  r"[a-z0-9]+(?:_[a-z0-9]+){2,}|#?\d{6,})\b\)?", re.I)


def _body(item: dict) -> dict:
    try:
        b = json.loads(item.get("body") or "{}")
    except (TypeError, ValueError):
        return {}
    return b if isinstance(b, dict) else {}


def _name(bot_id: str | None) -> str:
    b = config.BOTS_BY_ID.get(bot_id or "")
    return b.name if b else (bot_id or "")


def _in_his_turn(item: dict) -> bool:
    ""
    return bool(item.get("turn_id"))


def rule(item: dict) -> str | None:
    ""
    kind = item.get("kind") or ""
    body = _body(item)
    state = body.get("state")
    if kind in ALWAYS:
        return SHOW
    if kind == "choices":
        return _question(item, body)
    if kind == "kit":
        if state == "answered":
            return FOLD if _settled(item, body.get("answered_at")) else SHOW
        return FOLD if state == "closed" else SHOW
    if kind in ("login", "account", "code", "mac_ask", "wallet_request"):
        return FOLD if state in CLOSED else SHOW
    if kind in ("mail", "text"):
        return SHOW if state in OPEN or state == "sent" else FOLD
    if kind == "mac":


        return SHOW if body.get("live") or _in_his_turn(item) else None
    if kind == "task_started":
        return SHOW if _in_his_turn(item) else FOLD
    if kind == "signin":

        return SHOW if _in_his_turn(item) else FOLD
    if kind == "signin_decision":
        if (item.get("body") or "").startswith(NO_ANSWER):




            return FOLD
        if (item.get("title") or "").startswith(ANSWERED_IN_CHAT):




            return FOLD



        return SHOW
    if kind == "team":
        if body.get("to") in PERSON or body.get("from") in PERSON or body.get("kind") == "need":
            return SHOW



        if body.get("from") in config.BOTS_BY_ID and body.get("to") in config.BOTS_BY_ID:
            return FOLD
        return SHOW if _in_his_turn(item) else None
    if kind == "event" and isinstance(body.get("mail"), dict) and body["mail"].get("sent"):
        return SHOW
    if kind in JUDGED:
        return SHOW if _in_his_turn(item) else None
    return SHOW







STILL_ASKING = {"open", "expired", "nudge", "preparing"}
ANSWERED = {"picked", "waiting", "answering"}
NO_LONGER_APPLIES = {"handled", "stopped", "dismissed"}
ANSWER_GRACE_SEC = 30 * 60


def _question(item: dict, body: dict) -> str:
    ""
    state, answered_at = body.get("state"), body.get("at")
    if state == "replaced" or body.get("replaced"):
        return FOLD
    if body.get("notice"):
        try:
            r = store.db().execute("SELECT state, answered FROM notices WHERE id = ?", (int(body["notice"]),)).fetchone()
        except (sqlite3.Error, TypeError, ValueError):
            r = None
        if r is not None:
            state, answered_at = r["state"], r["answered"] or answered_at
    answered = state in ANSWERED or bool(body.get("answer") or body.get("picked") or body.get("answers"))
    if state == "open" or (state in STILL_ASKING and not answered):
        return SHOW
    if answered:
        return FOLD if _settled(item, answered_at) else SHOW
    return FOLD if state in NO_LONGER_APPLIES else SHOW


def _settled(item: dict, answered_at) -> bool:
    ""
    try:
        at = float(answered_at or item.get("changed_at") or item.get("created_at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    return time.time() - at >= ANSWER_GRACE_SEC


def verdict(item: dict, buzzed: bool = False, waiting: int = 0) -> str:
    ""

    if item.get("quiet_word") == UNDONE and item.get("kind") == "task_done":
        return HIDE
    if item.get("quiet_word") == ROUTED and item.get("kind") == "task_blocked":
        return HIDE
    if item.get("quiet_word") == RETIRED:
        return HIDE
    if buzzed or waiting:
        return SHOW
    by_rule = rule(item)
    judged = item.get("quiet_word")
    if by_rule == FOLD:
        return FOLD
    if by_rule == SHOW:
        return SHOW
    if judged == MACHINERY:
        return FOLD
    if judged == SCREEN:
        return HIDE
    if judged == "relay":
        return FOLD
    return SHOW






def clean(text: str | None) -> str:
    ""
    t = _IDS.sub(" ", str(text or ""))
    t = re.sub(r"[`*_#>\[\]{}|]", " ", t)
    t = " ".join(t.split()).strip(" .:;,-\"'“”‘’()")
    t = re.sub(r"\s+([,.:;])", r"\1", t)
    t = re.sub(r"\(\s*\)", "", t).strip()
    if len(t) > TITLE_MAX:
        from .sessioncard import _dangles
        words = t[:TITLE_MAX + 1].rsplit(" ", 1)[0].split(" ")
        while len(words) > 2 and _dangles(words[-1]):
            words.pop()
        t = " ".join(words).rstrip(" ,:;-")
    return t[:1].upper() + t[1:] if t else ""


def _first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text







def ended(how: str, subject: str) -> str:
    ""
    return ENDED[how].format(subject=_first_sentence(" ".join(str(subject or "").split())))


def first_sentence(text: str) -> str:
    ""


    return _first_sentence(text)


def actor(item: dict) -> str | None:
    ""
    if item.get("kind") == "team":
        b = _body(item)
        return (b.get("to") if b.get("kind") == TEAM_ASK else b.get("from")) or item.get("bot_id")
    return item.get("bot_id")


def fallback(item: dict) -> tuple[str, str]:
    ""
    kind, body = item.get("kind") or "", _body(item)
    title = item.get("title") or ""
    why = ""
    if kind == "team":
        sender = _name(body.get("from"))
        title = body.get("title") or (NOTE_TITLE if body.get("kind") == "note" else TEAM_TITLE)
        why = (f"Asked by {sender}" if sender else "") if body.get("kind") == TEAM_ASK else sender
    elif kind == "choices":
        title = body.get("title") or body.get("question") or title
        why = clean(body.get("closed_why")) or "Closed without an answer"
    elif kind == "task_started":
        task = (item.get("body") or "").split("\n\n")[0]
        title = f"Started {task}" if task else "Started a job"
    elif kind == "mac":
        title = title or "Used your Mac"
    elif kind == "signin_decision" and (item.get("body") or "").startswith(NO_ANSWER):
        why = "No answer in time"
    elif kind in ("signin", "signin_decision", "action"):
        title = title or (item.get("body") or "")
    if not why and actor(item) != item.get("bot_id"):
        why = _name(actor(item))
    return clean(title) or "Did some work", why




SYSTEM = "You sort the items in a person's chat with an assistant. Answer with JSON only."

PROMPT = """{first} chats with {zip}, an assistant that works with a team of other assistants. Decide whether this \
item is FOR {first}, or only how the work got done.

For {first} (it stays in the chat), even when there is nothing to do:
- anything said to {first}, or an answer to something {first} asked, even "yes, you're all set"
- news about {first}'s own life and work that {first} would want to be told: a grade, a reply from a person, money \
in or out, a delivery, a moved deadline, a result or finished thing {first} asked for or that was made for {first}, \
a problem with something of {first}'s
- anything that needs {first}: a question, an okay, a sign-in, a decision
- any fact about {first}'s own life or work, even when one assistant wrote it to another: a balance, a date, a \
grade, what a person said, something done on {first}'s behalf. It stays unless a message to {first} listed below \
already says it. Passing along {first}'s own request to another assistant is not news to {first}.
Only how the work got done (it folds away):
- assistants coordinating a job: who asked whom to do what, plans, handoffs, "started", "on it", corrections of \
their own earlier notes
- notices about the assistants' own tools and checks, like "stopped checking a site until the next update"
- a note that only says where other things are on the screen
When unsure, it is for {first}.
{relay}
The item ({what}):
<item>
{text}
</item>

Answer with this JSON and nothing else:
{{"for_person": true or false, "screen_note": true or false, "relayed": true or false, "title": "...", "why": "..."}}
- screen_note: true only when the item's words just point at other things on the screen.
- relayed: true only when a message to {first} listed above already tells {first} the same news.
- title: what was done, in the past tense, in at most 7 plain words, like "Diagnosed the PodGuard report problem", \
"Started the weekly Shorts scripts" or "Stopped checking Google until the next update". It is shown to {first}, so \
speak to {first} as "you" and never write {first}'s name: "Saved that you and Sam file jointly". No ids, codes, file \
names, colons, quotes or emojis.
- why: at most 6 plain words on why, like "It failed twice in a row.", or "" when the title says it."""

RELAY = """
Messages {zip} sent {first} after this item, oldest first:
{lines}
"""


def _what(item: dict) -> str:
    kind, body = item.get("kind"), _body(item)
    if kind == "team":
        word = {"ask": "a request", "tell": "a heads-up", "note": "a saved note"}.get(body.get("kind"), "a message")
        return f"{word} from {_name(body.get('from'))} to {_name(body.get('to'))}"
    return {"checkin": "a report", "task_done": "a finished job", "task_failed": "a job that hit a problem",
            "action": "a step an assistant took", "event": "a routine or reminder the assistant set up",
            "task_started": "a job that started", "mac": "time an assistant spent on the person's Mac",
            "choices": "a question card that closed", "signin": "a sign-in", "signin_decision": "an okay on a card",
            }.get(kind or "", "an update")


def _text(item: dict) -> str:
    body = _body(item)
    if item.get("kind") == "team":
        words = body.get("plain") or body.get("text") or ""
        return f"{body.get('title') or ''}\n{words}".strip()[:1500]
    if body:
        return " ".join(str(body.get(k) or "") for k in ("question", "name", "summary", "why", "when"))[:1500]
    return f"{item.get('title') or ''}\n{item.get('body') or ''}".strip()[:1500]


def parse(answer: str) -> dict | None:
    m = re.search(r"\{.*\}", answer or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def word_for(data: dict, relay_check: bool) -> str:
    ""
    if data.get("for_person") is False:
        return SCREEN if data.get("screen_note") is True else MACHINERY
    if relay_check:
        return "relay" if data.get("relayed") is True else FOR
    return NEWS





def names_him(text: str) -> bool:
    from . import who
    return any(re.search(rf"(?<![\w'’]){re.escape(n)}(?![\w-])", text or "")
               for n in {who.first(), who.full()} if n and n.strip())


def title_of(item: dict) -> str:
    ""
    t = clean(item.get("quiet_title"))
    return "" if names_him(t) else t


def good_title(title: str | None) -> str | None:
    t = clean(title)
    words = t.split()
    if not t or not 2 <= len(words) <= TITLE_WORDS + 2 or ":" in t or names_him(t):
        return None
    return t


async def _replies_after(item: dict) -> list[str]:
    ""
    from . import recentchat
    try:
        msgs = await recentchat.recent_messages(item["bot_id"])
    except Exception:
        log.info("could not read %s's chat for the relay check", item.get("bot_id"), exc_info=True)
        return []
    from . import privatewords
    at = float(item.get("created_at") or 0)
    said = [" ".join(str(m.get("display") or m.get("text") or "").split())[:400] for m in msgs
            if m.get("role") != "user" and m.get("class", "bot") == "bot" and float(m.get("ts") or 0) >= at - 5][:6]
    return privatewords.keep(said)


async def judge(item: dict, ask=None, replies: list[str] | None = None) -> dict | None:
    ""
    from . import ownerrules, shortreply, who
    ask = ask or shortreply.ask_helper
    relay_check = replies is not None
    relay = RELAY.format(zip=_name(item.get("bot_id")), first=who.first(),
                         lines="\n".join(f"- {r}" for r in replies) or "- (none)") if relay_check else ""
    prompt = PROMPT.format(first=who.first(), zip=_name(item.get("bot_id")), relay=relay, what=_what(item),
                           text=_text(item)) \
        + ownerrules.block(ownerrules.UPDATE, _text(item), item.get("bot_id"), always=False, answered=False)
    try:
        with shortreply.reading_message(item):
            answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=160),
                                            TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no quiet judgment for feed %s (%s)", item.get("id"), type(e).__name__)
        return None
    data = parse(answer)
    if data is None:
        return None
    why = clean(data.get("why"))
    return {"quiet": word_for(data, relay_check), "quiet_title": good_title(data.get("title")),
            "quiet_why": why if len(why.split()) <= 8 else ""}


def save(feed_id: int, found: dict) -> None:
    with store._lock:
        store.db().execute("UPDATE feed SET quiet_word = ?, quiet_title = COALESCE(?, quiet_title), quiet_why = ?, "
                           "quiet_at = ? WHERE id = ?",
                           (found["quiet"], found.get("quiet_title"), found.get("quiet_why") or "", time.time(),
                            feed_id))
        store.db().commit()


def hide_block_rows(task_id: str, block_at: float) -> int:
    ""
    with store._lock:
        rows = [r["id"] for r in store.db().execute(
            "SELECT id FROM feed WHERE kind = 'task_blocked' AND task_id = ? AND created_at >= ?", (task_id, block_at - 5))]
    for fid in rows:
        save(fid, {"quiet": ROUTED, "quiet_why": "Sent to the helper who can answer"})
    return len(rows)


_tried: dict[int, float] = {}


def _due(now: float, limit: int) -> list[dict]:
    ""

    rows = store.db().execute(
        "SELECT * FROM feed WHERE created_at > ? AND bot_id IS NOT NULL AND kind NOT IN ('message', 'message_local') "
        "AND (quiet_word IS NULL OR (quiet_word = ? AND kind = 'team' AND created_at < ?)) "
        "ORDER BY id DESC LIMIT 400", (now - LOOK_BACK_SEC, NEWS, now - RELAY_AFTER_SEC)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        if now - _tried.get(item["id"], 0) < RETRY_SEC:
            continue
        by_rule = rule(item)
        if by_rule == SHOW:
            continue
        word = item.get("quiet_word")
        if word is not None and not (word == NEWS and item.get("kind") == "team"):
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _sealed_word(item: dict) -> dict | None:
    ""

    from . import config, privatewords, shortreply, team
    zips = team.message_zips(item)
    if not shortreply.withheld(*zips):

        return {"quiet": FOR, "quiet_title": None, "quiet_why": ""} if privatewords.holds(_text(item)) else None
    if item.get("kind") == "team":
        return {"quiet": MACHINERY, "quiet_title": f"Between {' and '.join(_name(z) for z in zips)}"[:TITLE_MAX],
                "quiet_why": "Between your helpers"}
    return {"quiet": FOR, "quiet_title": None, "quiet_why": ""}


async def judge_due(ask=None, now: float | None = None, limit: int = PER_TICK) -> int:
    ""
    from . import buzz
    now = time.time() if now is None else now
    rows = await asyncio.to_thread(_due, now, limit)
    buzzed = await asyncio.to_thread(buzz.for_feed, [r["id"] for r in rows])
    done = 0
    for item in rows:
        _tried[item["id"]] = now
        if sealed := await asyncio.to_thread(_sealed_word, item):


            await asyncio.to_thread(save, item["id"], sealed)
            done += 1
            continue
        replies = await _replies_after(item) if item.get("quiet_word") == NEWS else None
        found = await judge(item, ask, replies)
        if found is None:
            continue
        if replies is not None and not found.get("quiet_title"):
            found["quiet_title"] = item.get("quiet_title")
        if buzzed.get(item["id"]) == "buzz" and found["quiet"] in (MACHINERY, SCREEN, "relay"):
            found["quiet"] = FOR
        await asyncio.to_thread(save, item["id"], found)
        done += 1
    return done


async def run() -> None:
    while True:
        await asyncio.sleep(EVERY_SEC)
        if ENABLED:
            try:
                await judge_due()
            except Exception:
                log.warning("could not judge the chats' background items", exc_info=True)





def present(items: list[dict]) -> list[dict]:
    ""

    from . import buzz
    buzzed = buzz.for_feed([it.get("id") for it in items])
    for it in items:
        waiting = it.get("aside_waiting") or 0
        word = verdict(it, buzzed.get(it.get("id")) == "buzz", waiting)
        it.pop("quiet_word", None)
        it.pop("quiet_at", None)
        if word == SHOW:
            it.pop("quiet_title", None)
            it.pop("quiet_why", None)
            continue
        it["quiet"] = word
        title, why = fallback(it)
        it["quiet_title"] = title_of(it) or title
        it["quiet_why"] = it.get("quiet_why") or why
        it["quiet_actor"] = actor(it)
        body = _body(it)
        if it.get("kind") == "team" and body and body.get("kind") != "note" and not body.get("title"):

            it["body"] = json.dumps({**body, "title": it["quiet_title"]})
    return items


def latest_shown(bot_id: str) -> dict | None:
    ""
    rows = store.db().execute("SELECT * FROM feed WHERE bot_id = ? AND kind NOT IN ('task_started', 'event') "
                              "ORDER BY id DESC LIMIT 30", (bot_id,)).fetchall()
    from . import worknotes
    for r in rows:

        if verdict(dict(r)) == SHOW and not worknotes.is_work(dict(r)):
            return dict(r)
    return None


def folds(feed_id: int | None) -> bool:
    ""

    if not feed_id:
        return False
    row = store.db().execute("SELECT * FROM feed WHERE id = ?", (feed_id,)).fetchone()
    return bool(row) and rule(dict(row)) == FOLD
