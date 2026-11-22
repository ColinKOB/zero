""













import asyncio
import json
import logging
import time
import zlib

from . import board, config, mac, store, turnsummary, who

log = logging.getLogger("taffy.chatquestions")

CONTEXT_PREFIX = "[Your open question cards: "

NUMBER_BASE = 1 << 31
QUESTION_MAX = 200
WORDS_MAX = 3000
SHORT_ANSWER_WORDS = 4


def _question(task: dict) -> str:
    ""
    text = str(task.get("block_reason") or task.get("title") or "").strip()
    return " ".join(text.split("\n", 1)[0].split())[:QUESTION_MAX]


def _number(task: dict) -> int:
    ""
    return NUMBER_BASE + zlib.crc32(f"{task['id']}\n{task.get('block_reason') or ''}".encode())


def is_question_number(value) -> bool:
    return isinstance(value, int) and value >= NUMBER_BASE






OWN_PREFIX = "[Your open questions to "
CARD_BASE = 1 << 40
STILL_ASKS = ("open", "expired")
OWN_MAX = 12


def is_card_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= CARD_BASE


def own_asks(bot_id: str) -> list[dict]:
    ""

    out = []
    for row in store.db().execute("SELECT id, title, body, created_at FROM feed WHERE kind='choices' AND bot_id=? "
                                  "ORDER BY id DESC LIMIT 200", (bot_id,)):
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") not in STILL_ASKS or card.get("questions"):
            continue
        out.append({"request": CARD_BASE + row["id"],
                    "question": " ".join(str(card.get("question") or row["title"] or "").split())[:QUESTION_MAX],
                    "asked": time.strftime("%b %-d", time.localtime(row["created_at"]))})
        if len(out) >= OWN_MAX:
            break
    return out[::-1]


def close_own(bot_id: str, number: int, why) -> dict:
    ""

    from . import choices, notices, stillasks
    feed_id = number - CARD_BASE
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='choices' AND bot_id=?", (feed_id, bot_id)).fetchone()
    try:
        card = json.loads(row["body"] or "{}") if row else {}
    except ValueError:
        card = {}
    if not row or card.get("state") not in STILL_ASKS or card.get("questions"):
        return {"ok": False, "error": "That question is no longer open, or it isn't yours. Nothing changed."}
    line = stillasks.clean_why(why)
    if not line:


        return {"ok": False, "error": f"Call close_card again with request {number} and why: one plain sentence to "
                                      f"{who.first()} saying why it no longer applies (it was done, it was decided "
                                      "another way, its moment passed)."}
    if card.get("notice"):
        notice = notices.by_feed(feed_id)
        if not notice or notice["state"] not in STILL_ASKS:
            return {"ok": False, "error": "That question is no longer open. Nothing changed."}
        notices.passed(notice, line)
        closed = True
    else:
        closed = choices.close_passed(bot_id, feed_id, line)
    if not closed:
        return {"ok": False, "error": "That question was answered or closed a moment ago. Nothing changed."}
    log.info("%s closed its own question card %s: it no longer applies", bot_id, feed_id)
    return {"ok": True, "result": f"That question is closed with your reason and has left {who.possessive()} list. "
                                  f"If {who.first()} asked about it, say in one line that you cleared it and why."}


def open_for(bot_id: str) -> list[dict]:
    ""
    return [t for t in board.grouped()["needs_you"]
            if t["status"] in ("blocked", "triage") and (t["assignee"] or "default") == bot_id]






TEAM_MAX = 12


def _asked_on_card(task_id: str) -> dict | None:
    ""

    for row in store.db().execute("SELECT id, bot_id, title, body, created_at FROM feed WHERE kind='choices' AND "
                                  "task_id=? ORDER BY id DESC", (task_id,)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") == "open" and not card.get("notice") and not card.get("questions"):
            return {"feed": row["id"], "bot": row["bot_id"],
                    "question": " ".join(str(card.get("question") or row["title"] or "").split())[:QUESTION_MAX],
                    "asked": float(row["created_at"] or 0)}
    return None


def open_in_chat(bot_id: str) -> list[dict]:
    ""



    def entry(t: dict, card: dict | None = None) -> dict:
        bot = config.BOTS_BY_ID.get(t.get("assignee") or "default")
        return {"task": t, "question": card["question"] if card else _question(t), "zip": bot.name if bot else "",
                "card": card["feed"] if card else None,
                "asked": card["asked"] if card else float(t.get("filed_at") or t.get("created_at") or 0)}
    if bot_id != config.LEAD.id:
        return [entry(t) for t in open_for(bot_id)]
    from . import things
    grouped = board.grouped()
    out = [entry(t) for t in grouped["needs_you"] if t["status"] in ("blocked", "triage")
           and not config.is_sandbox(t["assignee"] or "default")]
    asked = things.asked_tasks()
    for t in grouped["working"]:
        who_id = t["assignee"] or "default"
        if t["id"] not in asked or who_id == config.LEAD.id or config.is_sandbox(who_id) \
                or t.get("raw_status") not in things.WAITING:
            continue
        card = _asked_on_card(t["id"])
        if card and card["bot"] == who_id:
            out.append(entry(t, card))
    return sorted(out, key=lambda e: e["asked"], reverse=True)[:TEAM_MAX]




ANSWERED_PREFIX = "[Your question cards answered lately: "
ANSWERED_SEC = 2 * 86400
ANSWERED_MAX = 8


def answered_lately(bot_id: str, now: float | None = None) -> list[dict]:
    ""

    now = time.time() if now is None else now
    rows = store.db().execute("SELECT title, body, created_at FROM feed WHERE kind='signin_decision' AND bot_id=? AND "
                              "title LIKE 'Answered: %' AND created_at>=? ORDER BY id DESC LIMIT ?",
                              (bot_id, now - ANSWERED_SEC, ANSWERED_MAX)).fetchall()
    from . import home
    from datetime import datetime
    return [{"question": " ".join(r["title"][len("Answered: "):].split())[:QUESTION_MAX],
             "answer": " ".join(str(r["body"] or "").split())[:WORDS_MAX // 3],
             "at": datetime.fromtimestamp(r["created_at"], home.zone()).strftime("%b %-d at %-I:%M %p")}
            for r in rows if str(r["body"] or "").strip()]


def with_questions(bot_id: str, text: str) -> str:
    ""
    from . import turns
    turn = turns.current(bot_id)
    if turn is None or turn.done or turn.source != "app" or not (turn.own_words or "").strip():
        return text
    lead = bot_id == config.LEAD.id
    try:
        cards = [{"request": _number(e["task"]), "task": e["task"]["id"], "question": e["question"],
                  **({"zip": e["zip"]} if lead else {})} for e in open_in_chat(bot_id)]
    except Exception:
        log.warning("could not read the open question cards", exc_info=True)
        cards = []
    try:
        own = own_asks(bot_id)
    except Exception:
        log.warning("could not read the Zip's own open questions", exc_info=True)
        own = []
    try:
        answered = answered_lately(bot_id)
    except Exception:
        log.warning("could not read the answered question cards", exc_info=True)
        answered = []
    if not cards and not own and not answered:
        return text
    turn_id, text = turnsummary.peel(text)
    first = who.first()
    notes = ""
    if cards and lead:
        notes += (CONTEXT_PREFIX + json.dumps(cards, ensure_ascii=True) +
                  f". Context only. Your team's board tasks wait on {first} for these, newest first; zip says whose "
                  f"task it is. When this message answers one or overtakes it, call close_card with its request: "
                  f"{who.possessive()} words go to that task, and its card leaves the chat. Leave one alone when this "
                  f"message is about something else, and don't ask {first} to answer it again.]\n")
    elif cards:
        notes += (CONTEXT_PREFIX + json.dumps(cards, ensure_ascii=True) +
                  f". Context only. Your own board tasks put these on cards for {first}. When this message answers one "
                  f"or overtakes it, call close_card with its request: {who.possessive()} words go to that task, and "
                  "the card leaves the chat. Leave a card alone when this message is about something else, and don't "
                  f"ask {first} to answer it again.]\n")
    if own:
        notes += (OWN_PREFIX + f"{first}: " + json.dumps(own, ensure_ascii=True) +
                  f". Context only. You asked these, and each still waits in {who.possessive()} list. When one no "
                  "longer applies (it was done, it was decided another way, its moment passed), close it yourself: "
                  f"call close_card with its request and why, one plain sentence to {first}. Never tell {first} that "
                  "you can't clear your own question. Leave one that still needs an answer.]\n")
    if answered:
        notes += (ANSWERED_PREFIX + json.dumps(answered, ensure_ascii=True) +
                  f". Context only. {who.first()} answered these on their cards; the answer is beside each. They are "
                  f"settled: use the answers as given, and never ask {who.first()} for them again or count one as "
                  "still open.]\n")
    return (f"[Chat turn: {turn_id}]\n" if turn_id else "") + notes + text


async def _his_answer(bot_id: str) -> str:
    ""

    words = [w for w, _ in sorted(await mac.colin_recent_words(bot_id), key=lambda x: x[1])]

    words = [w for i, w in enumerate(words) if w.strip() and w not in words[i + 1:]]
    if not words:
        return ""
    last = words[-1][:WORDS_MAX]
    if len(last.split()) > SHORT_ANSWER_WORDS or len(words) < 2:
        return last

    return f"{words[-2][:WORDS_MAX - len(last) - 3]} / {last}"


async def settle(bot_id: str, number: int, why: str, session_id: str) -> dict:
    ""

    try:
        live = isinstance(mac._origin(bot_id, session_id), mac.LiveChat)
    except mac.MacError:
        live = False
    if not live:
        return {"ok": False, "error": f"A question card settles only from your chat, while {who.first()} is talking to "
                                      "you. Leave it open."}
    asked = await asyncio.to_thread(open_in_chat, bot_id)
    found = next((e for e in asked if _number(e["task"]) == number), None)
    if found is None:
        return {"ok": False, "error": "That question card is no longer open, or it isn't yours. Nothing changed."}
    task = found["task"]
    said = await _his_answer(bot_id)
    if not said:
        return {"ok": False, "error": f"I found no words from {who.first()} to send. Leave the card open."}
    question = found["question"]
    note = " ".join(str(why or "").split())[:300]
    answer = f'in the chat, about "{question}": {said}' + (f" (Your chat's note: {note})" if note else "")
    if found["card"]:
        from . import choices
        ok = await asyncio.to_thread(choices.answer_in_chat, found["card"], said + (f" ({note})" if note else ""))
        out = "" if ok else "the board did not take it"
    else:
        ok, out = await asyncio.to_thread(board.answer_block, task["id"], answer)
    if not ok:
        log.warning("%s could not settle the question on %s: %s", bot_id, task["id"], out[-200:])
        return {"ok": False, "error": "The board didn't take it, so the card is still open. Try once more, then carry "
                                      "on."}
    log.info("%s settled the question card on %s from the chat", bot_id, task["id"])
    from . import quiet

    store.add_feed("signin_decision", bot_id, None, quiet.ended("answered", question), "",
                   written_by="hub", written_for="record")
    mine = (task.get("assignee") or "default") == bot_id
    whose = "that task" if mine or not found["zip"] else f"{who.possessive(found['zip'])} task"
    return {"ok": True, "result": f"{who.possessive()} words went to {whose}, and the card left the chat. The task "
                                  "picks up from here, so don't do its work in this turn. No need to mention it unless "
                                  "it matters to your answer."}
