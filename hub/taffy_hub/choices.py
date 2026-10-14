"""Grok Bot's choice cards: a bot asks Colin to pick between a few options, and he taps one instead of typing.

The bot calls offer_choices (taffy-tools). A card offered during a chat turn waits until that turn's reply
is in, so it shows under the reply like Grok Bot's; offered from other work it shows at once. Tapping an
option sends it as Colin's own message, so it counts as his words everywhere (sign-ins, the Mac). The X
dismisses the card.
"""

import json
import time

from . import config, store, who

_pending: dict[str, list[dict]] = {}


class ChoiceError(Exception):
    pass


def offer(bot_id: str, question: str, options: list, during_turn: bool) -> str:
    question = " ".join(str(question or "").split())
    opts = [" ".join(str(o).split())[:80] for o in (options or []) if str(o).strip()]
    if not question or len(question) > 200:
        raise ChoiceError("Write the question in one short sentence.")
    if not 2 <= len(opts) <= 4 or len(set(o.lower() for o in opts)) != len(opts):
        raise ChoiceError("Offer two to four different options.")
    card = {"question": question, "options": opts}
    if during_turn:
        _pending.setdefault(bot_id, []).append(card)
        return f"{who.first()} will see the options under your reply. Keep your reply short and don't list them again."
    _publish(bot_id, card)
    return f"{who.first()} will see the options in your chat."


def flush(bot_id: str) -> None:
    """After a chat turn's reply is recorded: its cards go up, under the reply."""
    for card in _pending.pop(bot_id, []):
        _publish(bot_id, card)


def _publish(bot_id: str, card: dict) -> None:
    store.add_feed("choices", bot_id, None, card["question"], json.dumps({**card, "state": "open"}))


def answer(bot_id: str, feed_id: int, picked: str | None, custom: bool = False) -> dict:
    """`custom`: Colin typed his own answer in the card instead of picking one (Grok Bot has the same field)."""
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='choices' AND bot_id=?", (feed_id, bot_id)).fetchone()
    if not row:
        raise ChoiceError("That card is gone.")
    card = json.loads(row["body"] or "{}")
    if card.get("notice"):
        from . import notices
        try:
            return notices.answer(feed_id, picked, custom)
        except notices.AnswerError as e:
            raise ChoiceError(str(e)) from None
    if card.get("state") != "open" and not card.get("typed"):
        raise ChoiceError("That card was already answered.")
    if picked is not None and not custom and picked not in card.get("options", []):
        raise ChoiceError("That isn't one of the options.")
    card.update(state="picked" if picked else "dismissed", picked=(picked or "")[:500] or None, at=time.time())
    store.update_feed(feed_id, row["title"], json.dumps(card))
    return card


def close_open(bot_id: str, before: float | None = None) -> int:
    """The person wrote to the Zip instead of tapping: its open choice cards from before that message leave the chat,
    the way quick replies go when you type (live convo QA 2: Sam typed "Jordan Park" and Inbox's "Who should I
    email?" card stayed open under the answer). A proactive question from the lead (notices.py) stays; it has its own
    answers and expiry. A tap that arrives after the message still counts (`typed`)."""
    before = time.time() if before is None else before
    n = 0
    for row in store.db().execute("SELECT * FROM feed WHERE kind='choices' AND bot_id=? AND created_at<=?",
                                  (bot_id, before)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") != "open" or card.get("notice"):
            continue
        card.update(state="dismissed", typed=True, at=time.time())
        store.update_feed(row["id"], row["title"], json.dumps(card))
        n += 1
    return n
