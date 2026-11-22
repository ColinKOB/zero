""


































from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from . import config, store

log = logging.getLogger("taffy.passcard")

KIND = "handoff"
ENABLED = False
TIMEOUT_SEC = 30
TOPIC_WORDS = 4
NOTE_MAX = 300
WORDS_MAX = 8

DONE, NOT_DONE, NOTHING = "done", "not_done", "nothing"

SYSTEM = "You word one card in a person's chat about work passed between two of their assistants. Answer with JSON only."

WORD = """{sender} and {receiver} are two of {person}'s assistants. {sender} sent {receiver} this {what}:
<message>
{text}
</message>

{person} sees a small card about it in the chat. Answer with this JSON and nothing else:
{{{work_key}"topic": "...", "note": "..."}}
{work_rule}- topic: what it is about, in two or three plain words {person} would use, like "Car insurance" or "Dentist \
visit". Capitalize only the first word. No assistant names, ids, task names, colons or quotes.
- note: one or two short sentences {person} reads when opening the card: first the fact {person} would care about, \
then what {receiver} is going to do about it, naming {receiver}. For example: "Your car insurance renews Dec 1 for $84 \
more. {receiver} is going to try and find a better price." Speak to {person} as "you" and never write {person}'s \
name. Use only facts in the message, keeping every number, date and amount exactly as written. Never say {receiver} \
"should" look at it. No technical words, tool names, ids, file paths, em dashes or emojis."""

WORK_RULE = (f"- work: true when {{sender}} gives {{receiver}} something to do or act on for {{person}}; false when it "
             "only passes along news, a fact, thanks, or an answer.\n")

SETTLE = """{sender} passed this to {receiver}, another of {person}'s assistants:
<message>
{text}
</message>

What {receiver} came back with:
<result>
{answer}
</result>

Answer with this JSON and nothing else:
{{"outcome": "done" or "not_done" or "nothing", "words": "..."}}
- outcome: done when {receiver} finished what was passed over; not_done when it stopped short, could not, or only said \
what it will do later; nothing when it closed with nothing to do (already handled, a duplicate, no longer needed).
- words: what {receiver} did or will do, as a few plain words that follow "{receiver}" on a card, like "found two \
quotes" or "will compare quotes tonight". At most six words, starting lowercase, with no period. "" for nothing. No \
ids, task names or technical words."""

AGAIN = "\n\nYour last answer was not usable: {why}. Answer again with the JSON only."

_NUM = re.compile(r"\d[\d,.:]*")


def _name(bot_id: str) -> str:
    b = config.BOTS_BY_ID.get(bot_id)
    return b.name if b else bot_id


def private(*zips: str) -> bool:
    return any(config.is_private(z) or config.is_sandbox(z) for z in zips if z)


def _parse(answer) -> dict | None:
    m = re.search(r"\{.*\}", str(answer or ""), re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _plain(text) -> str:
    return " ".join(str(text or "").replace("—", ", ").replace("–", ", ").split())


def _numbers(text: str) -> set[str]:
    return {n.strip(".,:") for n in _NUM.findall(text or "")}


def _worded(data: dict | None, original: str, work_asked: bool) -> tuple[dict | None, str]:
    ""
    from . import quiet
    if data is None:
        return None, "it was not JSON"
    topic = quiet.clean(_plain(data.get("topic"))).rstrip(".")
    note = _plain(data.get("note"))
    work = data.get("work") is True if work_asked else True
    if work_asked and not isinstance(data.get("work"), bool):
        return None, "work must be true or false"
    if not work:
        return {"work": False, "topic": "", "note": ""}, ""
    if not topic or len(topic.split()) > TOPIC_WORDS or ":" in topic:
        return None, f"the topic must be one to {TOPIC_WORDS} plain words with no colon"
    if not note or len(note) > NOTE_MAX:
        return None, f"the note must be one or two short sentences, under {NOTE_MAX} characters"
    if quiet.names_him(topic) or quiet.names_him(note):
        return None, "it named the person instead of saying you"
    if not _numbers(note) <= _numbers(original):
        return None, "it has a number the message does not"
    if re.search(r"\bshould\b", note, re.I):
        return None, "the note must say what the assistant is going to do, not what it should do"
    return {"work": True, "topic": topic, "note": note}, ""


async def _ask(prompt: str, msg: dict, item: str, bot_id: str, ask) -> dict | None:
    ""


    from . import ownerrules, shortreply
    ask = ask or shortreply.ask_helper
    prompt += ownerrules.block(ownerrules.UPDATE, item, bot_id, always=False, answered=False)
    if msg["kind"] == "task" and msg.get("task_id"):
        task = {"id": msg["task_id"], "assignee": msg["to_bot"], "created_by": msg["from_bot"]}
        with shortreply.reading_task(task):
            answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=300),
                                            TIMEOUT_SEC + 5)
    else:
        with shortreply.reading_message(msg):
            answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=300),
                                            TIMEOUT_SEC + 5)
    return _parse(answer)


async def word(msg: dict, ask=None) -> dict | None:
    ""

    from . import who
    sender, receiver = msg["from_bot"], msg["to_bot"]
    text = str(msg.get("text") or "")
    if msg["kind"] == "task" and (msg.get("title") or "") and (msg.get("title") or "") not in text:
        text = f"{msg['title']}\n\n{text}"
    work_asked = msg["kind"] != "task"
    what = "board task" if msg["kind"] == "task" else "message"
    prompt = WORD.format(sender=_name(sender), receiver=_name(receiver), person=who.first(), what=what, text=text,
                         work_key='"work": true or false, ' if work_asked else "",
                         work_rule=WORK_RULE.format(sender=_name(sender), receiver=_name(receiver), person=who.first())
                         if work_asked else "")
    for attempt in range(2):
        try:
            data = await _ask(prompt, msg, text, sender, ask)
        except Exception as e:
            log.info("no words for hand-off %s (%s)", msg["id"], type(e).__name__)
            return None
        worded, why = _worded(data, text, work_asked)
        if worded is not None:
            return worded
        prompt += AGAIN.format(why=why)
    log.info("hand-off %s: the helper's words did not hold twice", msg["id"])
    return None


async def settle(mid: int, ask=None) -> dict | None:
    ""

    from . import who
    msg = _msg(mid)
    if not msg or not msg.get("handoff") or msg["answer"] in (None, "(no answer)"):
        return None
    h = msg["handoff"]
    if private(msg["from_bot"], msg["to_bot"]):
        return None
    prompt = SETTLE.format(sender=_name(msg["from_bot"]), receiver=_name(msg["to_bot"]), person=who.first(),
                           text=msg["text"], answer=msg["answer"])
    for attempt in range(2):
        try:
            data = await _ask(prompt, msg, msg["answer"], msg["to_bot"], ask)
        except Exception as e:
            log.info("no outcome for hand-off %s (%s)", mid, type(e).__name__)
            return None
        outcome = (data or {}).get("outcome")
        words = _plain((data or {}).get("words")).rstrip(".")
        from . import quiet
        if outcome in (DONE, NOT_DONE, NOTHING) and len(words.split()) <= WORDS_MAX and not quiet.names_him(words) \
                and ":" not in words:
            out = {"outcome": outcome, "words": "" if outcome == NOTHING else words[:1].lower() + words[1:]}
            _save(mid, {**h, **out})
            touch(mid)
            return out
        prompt += AGAIN.format(why="outcome must be done, not_done or nothing, and words at most six plain words")
    return None




def _msg(mid: int) -> dict | None:
    from . import team
    row = team._db().execute("SELECT * FROM team_msgs WHERE id=?", (mid,)).fetchone()
    if not row:
        return None
    m = dict(row)
    try:
        m["handoff"] = json.loads(m.get("handoff") or "null")
    except ValueError:
        m["handoff"] = None
    return m


def _save(mid: int, h: dict) -> None:
    from . import team
    with store._lock:
        team._db().execute("UPDATE team_msgs SET handoff=? WHERE id=?", (json.dumps(h), mid))
        team._db().commit()


def of_task(task_id: str) -> int | None:
    ""
    from . import team
    row = team._db().execute("SELECT id FROM team_msgs WHERE kind='task' AND task_id=? AND handoff IS NOT NULL",
                             (task_id,)).fetchone()
    return row["id"] if row else None


def carded(mid: int) -> bool:
    m = _msg(mid)
    return bool(m and m.get("handoff"))


def open_cards(mid: int, words: dict | None) -> dict | None:
    ""
    m = _msg(mid)
    if not m or m.get("handoff"):
        return None
    words = words or {}
    h = {"topic": words.get("topic") or "", "note": words.get("note") or ""}
    body = json.dumps({"msg": mid})
    title = h["topic"] or "Work passed on"
    h["sent"] = store.add_feed(KIND, m["from_bot"], m.get("task_id"), title, body, at=m["created_at"],
                               written_by="hub", written_for="person")
    h["got"] = store.add_feed(KIND, m["to_bot"], m.get("task_id"), title, body, at=m["created_at"],
                              written_by="hub", written_for="person")
    _save(mid, h)
    return h


def touch(mid: int) -> None:
    ""
    m = _msg(mid)
    h = (m or {}).get("handoff") or {}
    ids = [i for i in (h.get("sent"), h.get("got")) if i]
    if not ids:
        return
    with store._lock:
        store.db().execute(f"UPDATE feed SET changed_at=? WHERE id IN ({','.join('?' * len(ids))})", (time.time(), *ids))
        store.db().commit()


def touch_task(task_id: str) -> None:
    mid = of_task(task_id)
    if mid:
        touch(mid)




def _later(coro) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()


async def consider_tell(mid: int, ask=None) -> bool:
    ""
    from . import team
    m = _msg(mid)
    if not m:
        return False
    words = None if private(m["from_bot"], m["to_bot"]) else await word(m, ask)
    if words and words.get("work"):
        open_cards(mid, words)
        return True
    team.feed_tell(m)
    return False


async def word_task(mid: int, ask=None) -> None:
    ""
    m = _msg(mid)
    h = (m or {}).get("handoff")
    if not m or not h or h.get("topic") or private(m["from_bot"], m["to_bot"]):
        return
    words = await word(m, ask)
    if not words:
        return
    h = {**(_msg(mid) or {}).get("handoff", h), "topic": words["topic"], "note": words["note"]}
    _save(mid, h)
    for fid in (h.get("sent"), h.get("got")):
        if fid:
            store.update_feed(fid, words["topic"], json.dumps({"msg": mid}))


def tell_sent(mid: int) -> bool:
    ""

    if not ENABLED:
        return False
    _later(consider_tell(mid))
    return True


def task_made(mid: int, task_id: str) -> bool:
    ""

    if not ENABLED or not task_id:
        return False
    open_cards(mid, None)
    _later(word_task(mid))
    return True


def answered(mid: int) -> None:
    ""
    if carded(mid):
        touch(mid)
        if ENABLED:
            _later(settle(mid))




def _task_state(task_id: str) -> tuple[str, bool]:
    ""
    from . import board
    if not task_id or not config.KANBAN_DB.exists():
        return "", False
    try:
        with board._conn() as k:
            r = k.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        raw = r["status"] if r else ""
        if raw in ("blocked", "triage", "review"):
            t = board.get_task(task_id) or {}
            return raw, t.get("status") in ("blocked", "review") and bool(t.get("card") or t.get("on_card"))
        return raw, False
    except Exception:
        log.warning("could not read task %s for its hand-off card", task_id, exc_info=True)
        return "", False


def _step(bot_id: str, task_id: str = "") -> str:
    ""

    from . import activity, doing
    line = doing.line_for({"id": task_id, "assignee": bot_id}) if task_id else ""
    if not line or line == doing.FALLBACK:
        steps = [s for s in (activity.live(bot_id) or {}).get("steps") or [] if isinstance(s, str) and s.strip()]
        line = steps[-1] if steps else ""
    s = line.strip().rstrip(".")
    return s[:1].lower() + s[1:] if s else "on it"


def status(m: dict, now: float | None = None) -> tuple[str, str]:
    ""

    from . import choices, turns
    h = m.get("handoff") or {}
    to = _name(m["to_bot"])
    words, outcome = h.get("words") or "", h.get("outcome")
    answered_ = m.get("answer") not in (None, "", "(no answer)")
    raw, waits = _task_state(m.get("task_id") or "") if m["kind"] == "task" else ("", False)
    if outcome == NOTHING or (m["kind"] == "task" and raw == "archived" and not answered_):
        return "", f"To {to}"
    if answered_ or raw == "done":
        if outcome == DONE or (outcome is None and m["kind"] == "task"):
            return "Done", f"{to} {words}" if words else f"{to} finished"
        if outcome == NOT_DONE:
            return ("Stopped", f"{to} {words}" if words else f"{to} couldn't finish") if m["kind"] == "task" \
                else ("Passed", f"{to} {words}" if words else f"to {to}")
    if waits or (m["kind"] != "task" and m.get("delivered") and
                 choices.pending_ask(m["to_bot"], float(m["created_at"]))):
        return "Needs you", f"{to} has a question"
    if raw == "running" or (m["kind"] != "task" and m.get("delivered") and not answered_ and
                            turns.shows_working(m["to_bot"])):


        step = "on it" if private(m["from_bot"], m["to_bot"]) else _step(m["to_bot"], m.get("task_id") or "")
        return "Working", f"{to} is {step}"
    return "Passed", f"to {to}"


def present(item: dict) -> dict:
    ""

    try:
        body = json.loads(item.get("body") or "{}")
    except ValueError:
        body = {}
    m = _msg(int(body.get("msg") or 0)) if isinstance(body, dict) else None
    h = (m or {}).get("handoff") or {}
    if not m or not h:
        item["body"] = "{}"
        return item
    side = "sent" if item.get("id") == h.get("sent") else "got"
    out = {"side": side, "from": m["from_bot"], "to": m["to_bot"], "topic": h.get("topic") or "",
           "note": h.get("note") or "", "peer": h.get("got") if side == "sent" else h.get("sent")}
    if side == "sent":
        out["status"], out["line"] = status(m)
    item["body"] = json.dumps(out)
    return item


def context_line(item: dict, first: str) -> tuple[str, str] | None:
    ""
    present(item)
    body = json.loads(item["body"] or "{}")
    if body.get("side") not in ("sent", "got"):
        return None
    topic = body.get("topic") or "work"
    if body["side"] == "got":
        return f"a card showed {_name(body['from'])} passed you {topic}", body.get("note") or ""
    what = f"a card showed you passed {topic} to {_name(body['to'])}"
    if body.get("status"):
        what += f" ({body['status']}: {body.get('line')})"
    return what, body.get("note") or ""
