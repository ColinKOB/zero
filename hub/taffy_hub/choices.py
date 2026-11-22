""







import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

from . import about, config, store, task_context, who

_pending: dict[str, list[tuple[float, Callable[[], None], str]]] = {}


log = logging.getLogger("taffy.choices")


class ChoiceError(Exception):
    pass


REASONING_MAX = 500
TITLE_MAX_WORDS = 8


ORDERED_FOLLOW_THROUGH = (
    "Take these one at a time, in this order. After each item, post one short line saying what you finished so I "
    "can steer as you go. Don't save the updates for one final summary. If an item leaves a draft, Send, sign-in, "
    "or approval card waiting for me, leave the card open and start the next item only after I answer it or tell "
    "you to go on. An ordered list doesn't approve sending, paying, or any other action that needs my okay."
)








_LEAVE = re.compile(
    r"(?:^|[.!?]\s+)(?:(?:do you )?want me to|should i|shall i|can i|could i|okay for me to|ok for me to|ok to|okay to)\s+"
    r"(?:go ahead and\s+)?(?:check|look|search|find|read|pull|open|run|draft|write|prepare|get|make|copy|download|"
    r"compare|try|dig|tell you|start|put|add|block)\b|(?:^|[.!?]\s+)want an? (?:plan|list|draft|summary|reminder|outline)\b"


    r"|(?:^|[.!?]\s+)(?:do you )?want (?:these|those|them|it|this|that|the)\b"
    r"(?![^.?!]*\b(?:invit|shar|with)\w*)[^.?!]{0,40}\bon (?:your|my) calendar\b", re.I)
_PAST_ZIP = (r"(?:send|email|text|message|post|publish|deploy|submit|file|fill|pay|buy|order|book|delete|cancel|remove|"
             r"share|invite|sign|ship|merge|push)")
_REACHES_OUT = re.compile(rf"\b(?:and|then|to)\s+(?:also\s+)?{_PAST_ZIP}\b", re.I)



_WHICH_PREP = re.compile(r"(?:^|[.!?]\s+)which\b[^.!?]*?\b(?:should|shall) i\s+(?:draft|write|make|prepare)\b", re.I)
_OPTION_REACHES_OUT = re.compile(rf"\b{_PAST_ZIP}\b", re.I)


def asks_leave(question: str) -> bool:
    q = " ".join(str(question or "").split())
    return bool(_LEAVE.search(q)) and not _REACHES_OUT.search(q)


def picks_prep(question: str, options: list | None) -> bool:
    q = " ".join(str(question or "").split())
    return (bool(_WHICH_PREP.search(q)) and not _REACHES_OUT.search(q)
            and not any(_OPTION_REACHES_OUT.search(str(o)) for o in options or []))



_ASKS_TO_ADD = re.compile(r"\b(?:put|add|block|schedule)\b|\bon (?:your|my) calendar\b", re.I)


def refuse_leave(question: str, questions: list | None, options: list | None = None, proposal: bool = False,
                 needs_him: Callable[[str], bool] = lambda q: False) -> None:
    ""


    for q, opts in [(question, options)] + [(str(x.get("question") or ""), x.get("options"))
                                            for x in questions or [] if isinstance(x, dict)]:
        if asks_leave(q) and not (proposal and _ASKS_TO_ADD.search(q)) and not needs_him(q):
            raise ChoiceError(f'Not shown: "{q}" asks leave for something you can do and undo yourself. Do it now, '
                              f"then tell {who.first()} in a line what you did or found. Ask only what needs "
                              f"{who.first()}: sending, spending, deleting, sharing, or a choice between real options.")
        if picks_prep(q, opts):
            raise ChoiceError(f'Not shown: "{q}" asks which one to prepare, and preparing it is yours to do. Draft the '
                              f"likeliest one now and show it to {who.first()}, who can ask for a different one.")


def clean_title(raw) -> str:
    ""



    title = " ".join(str(raw or "").split()).strip("\"'\u201c\u201d").rstrip(".")
    if not title:
        log.info("a card or teammate question came without a title")
        return ""
    if len(title.split()) > TITLE_MAX_WORDS or len(title) > 60:
        log.info("a card or teammate question came with a title too long to fold to; left without one")
        return ""
    return title


def offer(bot_id: str, question: str, options: list, during_turn: bool, reasoning: str = "", title: str = "",
          replace_key: str = "", answer_context: str = "", steps: bool = False,
          questions: list[dict] | None = None, origin_task: str | None = None, session_id: str = "",
          about_file: dict | None = None, pick=None) -> str:
    ""





    stack = None
    if questions is not None and questions != []:
        if question or options or steps:
            raise ChoiceError("Send either one question or a question stack.")
        stack = clean_questions(questions)
        question, options = stack[0]["question"], stack[0]["options"]
        title = title or stack[0]["title"]
        pick = stack[0].get("pick")
    question = " ".join(str(question or "").split())
    title = clean_title(title)
    reasoning = " ".join(str(reasoning or "").split())
    opts = [" ".join(str(o).split())[:80] for o in (options or []) if str(o).strip()]
    if not question or len(question) > 200:
        raise ChoiceError("Write the question in one short sentence.")
    if len(reasoning) > REASONING_MAX:
        raise ChoiceError("Keep the reasoning to two to four short sentences.")
    if not 2 <= len(opts) <= 4 or len(set(o.lower() for o in opts)) != len(opts):
        raise ChoiceError("Offer two to four different options.")
    if not isinstance(steps, bool):
        raise ChoiceError("Set steps to true only for separate tasks that can all be done.")
    picked = resolve_pick(pick, opts)
    made = None
    if about_file:
        from . import teams
        roots = [config.WORK_DIR if bot_id == "default" else config.WORK_DIR / bot_id,
                 config.SHARED_DIR, config.HERMES_HOME / "kanban" / "attachments", *teams.extra_folders(bot_id)]
        try:
            made = about.of_file(Path(str(about_file.get("path") or "")), roots)
        except about.AboutError as e:
            raise ChoiceError(str(e)) from None
        if about_file.get("made"):
            made["made"] = str(about_file["made"])
    elif about.asks_to_approve(title, question, opts, reasoning, stack):
        raise ChoiceError(about.NEEDS_ITEM.format(first=who.first()))
    from . import asks
    covered = "" if stack else asks.covered(bot_id, question)
    if covered:
        raise ChoiceError(covered)
    card = {"question": question, "options": opts, **({"reasoning": reasoning} if reasoning else {}),
            **({"questions": stack, "answers": []} if stack else {}),
            **({"steps": True} if steps else {}), **({"about": made} if made else {}),
            **({"pick": picked} if picked else {}),
            **({"title": title} if title else {}), **({"replace_key": replace_key} if replace_key else {}),
            **({"answer_context": answer_context} if answer_context else {})}
    context = task_context.get(origin_task)
    if context:
        card["task_id"] = context.id
        card["replace_key"] = replace_key or context.id
        card["reasoning"] = "\n".join(part for part in (context.purpose, reasoning) if part)
        card["answer_context"] = "\n".join(part for part in (context.purpose, reasoning) if part)
        card["task_session"] = session_id
    if not stack and dismissed_by_him(bot_id, question, card.get("replace_key", "")):
        raise ChoiceError(f"Not shown: {who.first()} already closed this question with the X, so the answer is no. "
                          "Don't ask it again. If something about it has changed, ask the new question in new words.")
    if during_turn:
        defer(bot_id, lambda: _publish(bot_id, card), ask=question)
        return f"{who.first()} will see the options under your reply. Keep your reply short and don't list them again."
    waiting = _publish(bot_id, card)
    if waiting:
        return waiting
    return f"{who.first()} will see the options in your chat."


def _his_x(card: dict) -> bool:
    ""
    if card.get("state") != "dismissed" or card.get("picked"):
        return False
    return bool(card.get("by_him")) or not any(card.get(k) for k in ("typed", "replaced", "passed", "expired"))


def undismiss(bot_id: str, feed_id: int, since: float) -> dict | str:
    ""

    from . import undo
    with store._lock:
        row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='choices' AND bot_id=?", (feed_id, bot_id)).fetchone()
        card = json.loads(row["body"] or "{}") if row else {}
        if not row or card.get("state") != "dismissed" or not card.get("by_him"):
            return undo.NOT_YOURS
        if float(card.get("at") or 0) < since:
            return undo.TOO_LATE
        for key in ("by_him", "picked", "at"):
            card.pop(key, None)
        card["state"] = "open"
        store.update_feed(feed_id, row["title"], json.dumps(card))
    return card


def dismissed_by_him(bot_id: str, question: str, replace_key: str = "") -> bool:
    ""


    words = " ".join(str(question or "").split()).casefold()
    for row in store.db().execute("SELECT body FROM feed WHERE kind='choices' AND bot_id=? AND "
                                  "json_valid(body) AND json_extract(body, '$.state')='dismissed'", (bot_id,)):
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("notice") or not _his_x(card):
            continue
        if " ".join(str(card.get("question") or "").split()).casefold() == words and \
                card.get("replace_key", "") == (replace_key or ""):
            return True
    return False


def resolve_pick(pick, options: list[str]) -> str | None:
    ""


    if pick is None or (isinstance(pick, str) and not pick.strip()):
        return None
    if isinstance(pick, bool):
        raise ChoiceError("pick is one of the options: its exact words, or its number (1 is the first).")
    text = " ".join(str(pick).split())
    if text.isdigit() and 1 <= int(text) <= len(options) and text.casefold() not in {o.casefold() for o in options}:
        return options[int(text) - 1]
    found = next((o for o in options if o.casefold() == text.casefold()), None)
    if found is None:
        raise ChoiceError("pick is one of the options: its exact words, or its number (1 is the first).")
    return found


def clean_questions(questions: list[dict]) -> list[dict]:
    ""
    if not isinstance(questions, list) or not 2 <= len(questions) <= 6:
        raise ChoiceError("Offer two to six questions in one stack.")
    cleaned = []
    for item in questions:
        if not isinstance(item, dict):
            raise ChoiceError("Give each question a title, question, and options.")
        title = clean_title(item.get("title"))
        question = item.get("question")
        options = item.get("options")
        if not title:
            raise ChoiceError("Give each question a short title.")
        if not isinstance(question, str) or not question.strip() or len(question.strip()) > 200 \
                or "\n" in question or "\r" in question:
            raise ChoiceError("Write each question in one short sentence on one line.")
        if not isinstance(options, list) or not 2 <= len(options) <= 4 \
                or any(not isinstance(o, str) or not o.strip() or len(o.strip()) > 80 for o in options):
            raise ChoiceError("Offer two to four short options for each question.")
        options = [" ".join(o.split()) for o in options]
        if len({o.casefold() for o in options}) != len(options):
            raise ChoiceError("Offer different options for each question.")
        picked = resolve_pick(item.get("pick"), options)
        cleaned.append({"title": title, "question": " ".join(question.split()), "options": options,
                        **({"pick": picked} if picked else {})})
    return cleaned


def defer(bot_id: str, publish: Callable[[], None], ask: str = "") -> None:
    ""

    _pending.setdefault(bot_id, []).append((time.time(), publish, " ".join(str(ask or "").split())))


def pending(bot_id: str, since: float) -> bool:
    ""


    return any(at >= since for at, _, _ in _pending.get(bot_id, []))


def pending_ask(bot_id: str, since: float) -> str:
    ""


    return next((ask for at, _, ask in _pending.get(bot_id, []) if at >= since and ask), "")


def flush(bot_id: str) -> None:
    ""
    for _, publish, _ in _pending.pop(bot_id, []):
        publish()


def _publish(bot_id: str, card: dict) -> str | None:
    with store._lock:
        from . import asks
        questions = tuple(q["question"] for q in card.get("questions", []))
        text = " ".join(questions) if questions else card["question"]
        existing = None if card.get("steps") or card.get("about") else asks.wait_on(bot_id, text, questions)
        if existing:
            return asks.waiting(existing)
        _publish_new(bot_id, card)


def _publish_new(bot_id: str, card: dict) -> None:



    replaced = []
    for row in store.db().execute("SELECT * FROM feed WHERE kind='choices' AND bot_id=? AND title=?",
                                  (bot_id, card["question"])).fetchall():
        try:
            old = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if old.get("state") == "open" and not old.get("notice") and not old.get("answers") \
                and old.get("replace_key", "") == card.get("replace_key", ""):
            old.update(state="dismissed", replaced=True, at=time.time())
            store.update_feed(row["id"], row["title"], json.dumps(old))
            replaced.append(row["id"])
    asked_at = time.time()
    fid = store.add_feed("choices", bot_id, card.get("task_id"), card["question"],
                         json.dumps({**card, "state": "open"}),
                         written_by="zip", written_for="person")
    if not card.get("notice"):
        look_again_soon(bot_id, asked_at - 1, newer=card["question"])
    for old_id in replaced:
        store.db().execute("INSERT OR IGNORE INTO ask_waiters SELECT source, ?, bot_id FROM ask_waiters "
                           "WHERE source='choices' AND ask_id=?", (fid, old_id))
        store.db().execute("DELETE FROM ask_waiters WHERE source='choices' AND ask_id=?", (old_id,))
    store.db().commit()


def answer(bot_id: str, feed_id: int, picked: str | None, custom: bool = False,
           ordered: list[str] | None = None) -> dict:
    ""

    with store._lock:
        card = _answer(bot_id, feed_id, picked, custom, ordered)
    if _for_task(card, picked, ordered) and _to_task(feed_id, picked):
        card = _told_task(feed_id, card)
    return card


async def answer_async(bot_id: str, feed_id: int, picked: str | None, custom: bool = False,
                       ordered: list[str] | None = None) -> dict:
    ""
    with store._lock:
        card = _answer(bot_id, feed_id, picked, custom, ordered)
    if _for_task(card, picked, ordered) and await asyncio.to_thread(_to_task, feed_id, picked):
        card = _told_task(feed_id, card)
    return card


def _for_task(card: dict, picked: str | None, ordered: list[str] | None) -> bool:
    ""
    return bool(picked and ordered is None and card.get("state") == "picked" and card.get("task_id")
                and not card.get("notice") and not card.get("questions"))


def _told_task(feed_id: int, card: dict) -> dict:
    ""


    if not card.get("send"):
        return card
    send = card["send"] + "\n\n" + TASK_HAS_IT
    with store._lock:
        row = store.db().execute("SELECT title, body FROM feed WHERE id=? AND kind='choices'", (feed_id,)).fetchone()
        if row:
            stored = json.loads(row["body"] or "{}")
            stored["sent"] = send
            store.update_feed(feed_id, row["title"], json.dumps(stored))
    return {**card, "send": send, "sent": send}


TASK_HAS_IT = ("[From the hub: the board task that asked this has this answer and carries on from here. Don't do its "
               "work in this turn, and don't pass the answer on again.]")


def _to_task(feed_id: int, words: str) -> bool:
    ""



    from . import things
    try:


        row = store.db().execute("SELECT title FROM feed WHERE id=? AND kind='choices'", (feed_id,)).fetchone()
        if row and row["title"]:
            words = f'About your question "{row["title"]}": {words}'
        settled = things.settle({"kind": "card", "id": feed_id}, things.ANSWERED, "", words=words)
        return any(m["kind"] == "task" for m in settled)
    except Exception:
        log.warning("the answer on card %s could not reach its task", feed_id, exc_info=True)
        return False


def _answer(bot_id: str, feed_id: int, picked: str | None, custom: bool, ordered: list[str] | None) -> dict:
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='choices' AND bot_id=?", (feed_id, bot_id)).fetchone()
    if not row:
        raise ChoiceError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("questions"):

        card["legacy_single"] = True
    if ordered is not None:
        options = card.get("options", [])
        if card.get("notice") or card.get("steps") is not True or len(options) < 3:
            raise ChoiceError("That card doesn't offer a list of steps.")
        if picked is not None or custom:
            raise ChoiceError("Send either an ordered list or one answer.")
        if not isinstance(ordered, list) or not ordered or any(not isinstance(item, str) for item in ordered):
            raise ChoiceError("Choose at least one step.")
        if len(set(ordered)) != len(ordered) or any(item not in options for item in ordered):
            raise ChoiceError("Choose each step from the card once.")
        lead = "Let's do all of these, in this order:" if len(ordered) == len(options) else "Let's do these, in this order:"
        picked = lead + "\n" + "\n".join(f"{i}. {item}" for i, item in enumerate(ordered, 1))
    if card.get("notice"):

        from . import notices
        try:
            return notices.answer(feed_id, picked, custom)
        except notices.AnswerError as e:
            raise ChoiceError(str(e)) from None
    if card.get("state") != "open" and not card.get("typed"):
        raise ChoiceError("That card was already answered.")
    if ordered is None and picked is not None and not custom and picked not in card.get("options", []):
        raise ChoiceError("That isn't one of the options.")
    if card.get("questions") and card.get("answers") and not picked:
        raise ChoiceError("Answer the current question before closing this stack.")
    if ordered is not None:
        card["ordered"] = ordered

    card.pop("typed", None)
    card.update(state="picked" if picked else "dismissed", picked=(picked or "")[:500] or None, at=time.time())
    if not picked:
        card["by_him"] = True
    send = f"{picked}\n\n{ORDERED_FOLLOW_THROUGH}" if ordered is not None else None
    if card.get("questions") and card.get("answers") and picked:

        card["answers"].append({"picked": picked[:500], "custom": custom, "at": time.time()})
        send = _stack_message(card)
    elif picked and card.get("answer_context"):

        send = f'{card["answer_context"]}\n\nAbout your question "{card["question"]}": {send or picked}'
    elif picked and send is None:


        from . import notices
        send = f'About your question "{card["question"]}": {picked}' + notices.plan_say(card["question"], picked,
                                                                                         time.time())
    if send:
        card["sent"] = send
    store.update_feed(feed_id, row["title"], json.dumps(card))
    if card.get("mac_wake"):
        from . import macwake
        macwake.answered(card, None if custom else picked)
    if card.get("week_in") and not custom:
        from . import loopcheck
        loopcheck.week_in_answer(card["week_in"], picked)
    from . import asks
    asks.answered("choices", feed_id, bot_id, card["question"], send or picked or "Dismissed without an answer.")
    if picked:
        from . import firstline
        firstline.note_card_answer(send or picked, bot_id)
    return {**card, "send": send} if send else card


def answer_question(bot_id: str, feed_id: int, index: int, picked: str, custom: bool = False) -> tuple[dict, str | None]:
    ""
    with store._lock:
        return _answer_question(bot_id, feed_id, index, picked, custom)


def _answer_question(bot_id: str, feed_id: int, index: int, picked: str, custom: bool) -> tuple[dict, str | None]:
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='choices' AND bot_id=?",
                             (feed_id, bot_id)).fetchone()
    if not row:
        raise ChoiceError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    questions, answers = card.get("questions"), card.get("answers", [])
    if not questions or card.get("legacy_single"):
        raise ChoiceError("That card is not a question stack.")
    if index < 0 or index >= len(questions):
        raise ChoiceError("That question is gone.")
    if not isinstance(picked, str) or not picked.strip() or len(picked) > 500:
        raise ChoiceError("Answer this question before continuing. Keep your answer under 500 characters.")
    picked = picked.strip()
    if not custom and picked not in questions[index]["options"]:
        raise ChoiceError("That isn't one of the options.")
    if index < len(answers) and answers[index]["picked"] == picked and answers[index]["custom"] == custom:
        return card, None
    if card.get("state") != "open" or index != len(answers):
        raise ChoiceError("Answer the current question once, in order.")
    answers.append({"picked": picked, "custom": custom, "at": time.time()})
    card["answers"] = answers
    send = None
    if len(answers) == len(questions):
        send = _stack_message(card)
        card.update(state="picked", picked=send, sent=send, at=time.time())
    else:
        current = questions[len(answers)]
        card.update(question=current["question"], options=current["options"])

    with store._lock:
        changed = store.db().execute("UPDATE feed SET body=?, changed_at=? WHERE id=? AND body=?",
                                     (json.dumps(card), time.time(), feed_id, row["body"])).rowcount
        store.db().commit()
    if not changed:
        raise ChoiceError("That question was answered elsewhere. Reload the card.")
    if send:
        from . import firstline
        firstline.note_card_answer(send, bot_id)
        from . import asks
        asks.answered("choices", feed_id, bot_id, row["title"], send)
    return card, send


def _stack_words(card: dict) -> str:
    return "\n".join(f'{q["title"]} ({q["question"]}): {a["picked"]}'
                     for q, a in zip(card["questions"], card["answers"]))


def _stack_message(card: dict) -> str:
    words = _stack_words(card)
    return f'{card["answer_context"]}\n\n{words}' if card.get("answer_context") else words




def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def asking_words(text: str) -> tuple[str, str] | None:
    ""





    flat, found = _flat(text), None
    from . import kit
    own = kit.card_answers().get(flat)
    if own is not None:
        return own, ""
    for row in store.db().execute("SELECT body FROM feed WHERE kind='choices' AND body LIKE '%\"sent\":%'"):
        card = json.loads(row["body"] or "{}")
        if card.get("sent") and _flat(card["sent"]) == flat:
            found = card
    if found is None:
        return None
    picked, question = found.get("picked") or "", _flat(found.get("question"))
    if found.get("notice"):
        own = _own_part(found)
        if not own and flat.startswith(f'About your question "{question}": {_flat(picked)}'):
            own = picked.replace("@", " at ")
        return own, ""
    if found.get("answers"):
        return ("\n".join(a["picked"] for a in found["answers"]),
                "\n".join(f'{q["title"]} ({q["question"]})' for q in found["questions"]))
    return picked, question


def _own_part(card: dict) -> str:
    ""

    picked = card.get("picked") or ""
    if card.get("notice"):
        return "" if picked in card.get("options", []) else picked
    return _stack_words(card) if card.get("answers") else picked


def card_answers() -> dict[str, str]:
    ""




    from . import kit
    return {**kit.card_answers(), **{_flat(card["sent"]): _own_part(card) for card in _sent_cards()}}


def _sent_cards() -> list[dict]:
    cards = [json.loads(row["body"] or "{}") for row in store.db().execute(
        "SELECT body FROM feed WHERE kind='choices' AND body LIKE '%\"sent\":%' ORDER BY id")]
    return [card for card in cards if card.get("sent")]


def own_words(text: str, answers: dict[str, str] | None = None) -> str:
    ""

    return (card_answers() if answers is None else answers).get(_flat(text), text)


def _loop_part(card: dict) -> tuple[str, str]:
    ""



    own, picked, question = _own_part(card), card.get("picked") or "", _flat(card.get("question"))
    if card.get("answers"):
        return own, ""
    if not own and _flat(card["sent"]).startswith(f'About your question "{question}": {_flat(picked)}'):
        own = picked
    return own, question if own else ""


def loop_words(texts: list[str]) -> tuple[str, str]:
    ""


    from . import kit
    cards = {**{sent: (own, "") for sent, own in kit.card_answers().items()},
             **{_flat(card["sent"]): _loop_part(card) for card in _sent_cards()}}
    parts = [cards.get(_flat(text), (text, "")) for text in texts]
    return (" / ".join(own for own, _ in parts if own.strip()).strip(),
            " / ".join(question for _, question in parts if question))







RECHECK_AFTER_SEC = 86400
RECHECK_EVERY_SEC = 86400
RECHECK_PER_RUN = 3
_looked: dict[int, float] = {}
_tasks: set = set()


def _open_zip_cards(bot_id: str | None, before: float) -> list:
    ""


    q, args = "SELECT * FROM feed WHERE kind='choices' AND created_at<=?", [before]
    if bot_id is not None:
        q, args = q + " AND bot_id=?", args + [bot_id]
    out = []
    for row in store.db().execute(q + " ORDER BY id", args).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") != "open" or card.get("notice"):
            continue
        if card.get("questions"):
            continue
        out.append(row)
    return out


async def recheck(now: float | None = None, ask=None) -> int:
    ""


    from . import stillasks
    t = time.time() if now is None else now
    looked = closed = 0
    for row in _open_zip_cards(None, t - RECHECK_AFTER_SEC):
        if looked >= RECHECK_PER_RUN:
            break
        if t - _looked.get(row["id"], 0.0) < RECHECK_EVERY_SEC:
            continue
        _looked[row["id"]] = t
        looked += 1
        card = json.loads(row["body"] or "{}")
        verdict = await stillasks.check(row["bot_id"], card.get("question") or row["title"], card.get("options") or [],
                                        row["created_at"], why=card.get("reasoning") or "", ask=ask)
        if verdict.state == stillasks.PASSED and close_passed(row["bot_id"], row["id"], verdict.why):
            closed += 1
    return closed


LOOK_AGAIN = False
LOOK_AGAIN_MAX = 3
LOOK_AGAIN_EVERY_SEC = 1800
_looked_again: dict[str, float] = {}


async def look_again(bot_id: str, before: float, newer: str = "", ask=None) -> int:
    ""






    from . import board, chatquestions, stillasks
    if ask is None and not stillasks.ENABLED:
        return 0
    now, done, looked = time.time(), 0, 0

    def due(key: str) -> bool:
        if now - _looked_again.get(key, 0.0) < LOOK_AGAIN_EVERY_SEC:
            return False
        _looked_again[key] = now
        while len(_looked_again) > 500:
            _looked_again.pop(next(iter(_looked_again)))
        return True
    bot = config.BOTS_BY_ID.get(bot_id)
    more = f"\n\n{bot.name if bot else 'The assistant'} has just asked a newer question: {_flat(newer)[:200]}" if newer else ""
    for row in _open_zip_cards(bot_id, before)[-LOOK_AGAIN_MAX:]:
        if looked >= LOOK_AGAIN_MAX or not due(f"card:{row['id']}"):
            continue
        looked += 1
        card = json.loads(row["body"] or "{}")
        since = await stillasks.happened_since(bot_id, row["created_at"], skip_feed=row["id"])
        verdict = await stillasks.check(bot_id, card.get("question") or row["title"], card.get("options") or [],
                                        row["created_at"], why=card.get("reasoning") or "", since=since + more, ask=ask)
        if verdict.state == stillasks.PASSED and close_passed(bot_id, row["id"], verdict.why):
            done += 1
    try:
        tasks = [t for t in await asyncio.to_thread(chatquestions.open_for, bot_id)
                 if t.get("card") and float(t.get("filed_at") or 0) < before]
    except Exception:
        log.warning("could not read %s's waiting tasks for a second look", bot_id, exc_info=True)
        tasks = []
    for t in tasks[-LOOK_AGAIN_MAX:]:
        if looked >= 2 * LOOK_AGAIN_MAX or not due(f"task:{t['id']}:{t.get('filed_at')}"):
            continue
        looked += 1
        asked_at = float(t.get("filed_at") or now)
        since = await stillasks.happened_since(bot_id, asked_at)
        verdict = await stillasks.check(bot_id, t["card"].get("ask") or t.get("title") or "", t["card"].get("options") or [],
                                        asked_at, why=t.get("title") or "", since=since + more, ask=ask)
        if verdict.state != stillasks.PASSED:
            continue
        note = (f"{who.hub_note()} Your question on this task looks settled by what happened in your chat with "
                f"{who.first()} since you asked: {verdict.why} Check that first. If the work is done, finish this task "
                f"and say so. If something is left, carry on from where the chat left it. Don't ask {who.first()} the "
                "same question again.")
        ok, _out = await asyncio.to_thread(board.answer_block, t["id"], note)
        if ok:
            log.info("%s's waiting task %s went back to it: its question looks settled", bot_id, t["id"])
            done += 1
    return done


def look_again_soon(bot_id: str, before: float, newer: str = "") -> None:
    ""
    from . import stillasks
    if not (LOOK_AGAIN and stillasks.ENABLED):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(look_again(bot_id, before, newer))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def close_passed(bot_id: str, feed_id: int, why: str) -> bool:
    ""

    with store._lock:
        row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='choices' AND bot_id=?",
                                 (feed_id, bot_id)).fetchone()
        card = json.loads(row["body"] or "{}") if row else {}
        if not row or card.get("state") != "open" or card.get("notice"):
            return False
        card.update(state="dismissed", passed=True, closed_why=why, at=time.time())
        store.update_feed(feed_id, row["title"], json.dumps(card))
    from . import asks
    asks.answered("choices", feed_id, bot_id, card["question"], why, closed=True)
    store.add_card_note(bot_id, f'Your question "{card["question"][:200]}" closed without an answer from '
                                f"{who.first()} because it no longer applies: {why} Don't ask it again unless "
                                "something changes.")
    log.info("%s's question card %s closed: it no longer applies", bot_id, feed_id)
    return True


def close_open(bot_id: str, before: float | None = None, answer: str = "") -> int:
    ""




    before = time.time() if before is None else before
    rows = _open_zip_cards(bot_id, before)
    if not rows or not str(answer or "").strip():
        return 0
    job = settle_typed(bot_id, [r["id"] for r in rows], answer)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(job)
        return len(rows)
    task = loop.create_task(job)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return len(rows)


async def settle_typed(bot_id: str, feed_ids: list[int], answer: str, ask=None) -> int:
    ""

    from . import stillasks
    rows = [r for r in _open_zip_cards(bot_id, time.time()) if r["id"] in set(feed_ids)]
    cards = [json.loads(r["body"] or "{}") for r in rows]
    hit = await stillasks.answered(bot_id, cards, answer, ask=ask)
    n = 0
    for i in sorted(hit):
        if not _close_typed(rows[i]["id"], "Wrote in the original chat: " + answer):
            continue
        await asyncio.to_thread(_to_task, rows[i]["id"], answer)
        n += 1
    return n


def _close_typed(feed_id: int, said: str) -> bool:
    ""

    with store._lock:
        row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='choices'", (feed_id,)).fetchone()
        card = json.loads(row["body"] or "{}") if row else {}
        if card.get("state") != "open":
            return False
        card.update(state="dismissed", typed=True, at=time.time())
        store.update_feed(row["id"], row["title"], json.dumps(card))
    from . import asks
    asks.answered("choices", row["id"], row["bot_id"], card["question"],
                  said + "\nCheck whether this answers the request or changes the task.")
    return True


def answer_in_chat(feed_id: int, words: str) -> bool:
    ""


    if not _to_task(feed_id, words):
        return False
    _close_typed(feed_id, f"Wrote in {config.LEAD.name}'s chat: {words}")
    return True
