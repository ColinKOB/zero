"""Bot turns that outlive the phone's connection.

A turn runs as a background task: its events go into a buffer that any number of app connections
can follow, its reply is always recorded, and its busy state always clears. One turn per bot runs
at a time (a per-bot lock shared with the watcher's board updates), so turns never collide on a
Hermes session.
"""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator

from . import activity, briefing, chatter, choices, config, consent, hermes, history, looking, notices, store, who

log = logging.getLogger("taffy.turns")
KEEPALIVE_SEC = 15


class Busy(Exception):
    """A turn is already running for this bot."""


class Turn:
    def __init__(self, bot_id: str, source: str):
        import uuid
        self.id = uuid.uuid4().hex
        _recent[self.id] = self
        while len(_recent) > 100:
            _recent.pop(next(iter(_recent)))
        self.bot_id = bot_id
        self.source = source
        self.started = time.time()
        self.events: list[dict] = []
        self.done = False
        self.run_id: str | None = None   # the Hermes run, for steering a live job
        self.own_words = ""               # what Colin himself said to start this turn (see start)
        self.stopped = False              # the person pressed Stop (see stop)
        self.typed = True                 # the person's own words were typed in the chat (not heard on a call)
        self.yielded = False              # held on a card, it made way for the person's new message (see send)
        self.replied = False              # Hermes gave the reply; it may still be held while it is shortened (_short)
        self.device: str | None = None    # the device the person typed on (looking.py), for a reply after they leave
        self._changed = asyncio.Condition()

    async def push(self, ev: dict) -> None:
        async with self._changed:
            self.events.append(ev)
            if ev.get("type") == "run":
                self.run_id = ev.get("id")
            if ev.get("type") == "done":
                self.done = True
            self._changed.notify_all()

    async def follow(self) -> AsyncIterator[dict | None]:
        """Yields events from the start; yields None as a keepalive when nothing happens for a while."""
        i = 0
        while True:
            async with self._changed:
                if i >= len(self.events) and not self.done:
                    try:
                        await asyncio.wait_for(self._changed.wait(), KEEPALIVE_SEC)
                    except TimeoutError:
                        pass
                batch = self.events[i:]
                i = len(self.events)
                finished = self.done
            if not batch and not finished:
                yield None
            for ev in batch:
                yield ev
            if finished and i >= len(self.events):
                return


_turns: dict[str, Turn] = {}
_recent: dict[str, "Turn"] = {}        # by id, so a reconnecting app finds its own turn, running or just done
_queued: dict[str, list[Turn]] = {}   # messages Colin sent while the bot was busy, in order
_locks: dict[str, asyncio.Lock] = {}


def _lock(bot_id: str) -> asyncio.Lock:
    return _locks.setdefault(bot_id, asyncio.Lock())


def busy_bots() -> list[str]:
    return sorted(b for b in config.BOTS_BY_ID if b in _turns or _lock(b).locked() or _queued.get(b))


def is_busy(bot_id: str) -> bool:
    return bot_id in _turns or _lock(bot_id).locked() or bool(_queued.get(bot_id))


def current(bot_id: str) -> Turn | None:
    return _turns.get(bot_id)


def by_id(turn_id: str) -> Turn | None:
    return _recent.get(turn_id)


def latest(bot_id: str) -> Turn | None:
    """The newest turn Colin is waiting on: his last queued message, or the one running."""
    q = _queued.get(bot_id)
    return q[-1] if q else _turns.get(bot_id)


def send(bot_id: str, text: str, reply_to: dict | None = None, device: str | None = None) -> Turn:
    """A message from Colin in the app. If the bot is busy (with him, a board update or a call job), the
    message waits its turn instead of being refused, and runs as soon as the bot is free. The app shows it as
    queued ("queued", then "started" when its turn begins), so the person can keep talking to a working Zip.

    A Zip held on a card is not busy (Oct 9, 11:18 p.m.): when the running turn is only waiting for the person's okay,
    it makes way at once (`make_way`) and the message goes straight through, with no "queued" line. The card stays
    for the person to answer later; answering it then picks the conversation back up (approvals.decide).

    `reply_to` is the message the person replied to ({"text", "who"}): it goes to the Zip as a quiet first line
    (history.reply_line), queued or not. Only `text` is the person's own words (consent, Retry, Stop).

    `device` is the one it was typed on (looking.key): if that device has left the app when the reply comes, the
    reply goes to the person as a notification (_tell_away)."""
    release(bot_id)   # the person is talking again: work held by Stop may go on after this message
    try:
        return start(bot_id, text, reply_to=reply_to, device=device)
    except Busy:
        pass
    turn = Turn(bot_id, "app")
    turn.own_words = text
    turn.device = device
    yielding = activity.waiting(bot_id) and not _queued.get(bot_id)
    _queued.setdefault(bot_id, []).append(turn)
    loop = asyncio.get_running_loop()
    if yielding:
        loop.create_task(_make_way(bot_id, turn))
    else:
        loop.create_task(turn.push({"type": "queued", "position": len(_queued[bot_id]), "text": QUEUED_LINE}))
    loop.create_task(_run_queued(turn, _with_reply(text, reply_to)))
    return turn


async def _make_way(bot_id: str, waiting_for: Turn) -> None:
    """The running turn is held on a card: stop that run so the person's message runs now. If it cannot be stopped,
    the message is shown as queued after all, which is the truth."""
    run = activity.run_id(bot_id)
    cur = _turns.get(bot_id)
    if run:
        hermes.yielded_runs.add(run)
        hermes.last_yield[bot_id] = True
        if cur and not cur.done:
            cur.yielded = True
        if await hermes.stop(bot_id, run):
            log.info("%s's turn was waiting on a card; it made way for the person's message (run %s)", bot_id, run)
            return
        hermes.yielded_runs.discard(run)
        hermes.last_yield.pop(bot_id, None)   # it did not make way: a follow-up that ends with nothing says so
        if cur:
            cur.yielded = False
    await waiting_for.push({"type": "queued", "position": 1, "text": QUEUED_LINE})


QUEUED_LINE = "Queued, it will see this next."


async def _run_queued(turn: Turn, text: str) -> None:
    try:
        while True:
            if turn.stopped:   # Stop handed this message back to the person (see stop)
                return
            q = _queued.get(turn.bot_id) or []
            if q and q[0] is turn and turn.bot_id not in _turns and not _lock(turn.bot_id).locked():
                break
            await asyncio.sleep(0.5)
        q.pop(0)
        _turns[turn.bot_id] = turn
        await turn.push({"type": "started"})
        await _run(turn, _with_reactions(turn.bot_id, text), text)
    except Exception:
        log.exception("queued turn failed")
        if turn in (_queued.get(turn.bot_id) or []):
            _queued[turn.bot_id].remove(turn)
        await turn.push({"type": "error", "text": "That message could not be sent. Try again in a moment."})
        await turn.push({"type": "done"})


async def steer(bot_id: str, text: str) -> bool:
    """Passes more detail to the bot's running turn. False when nothing is running to take it."""
    turn = _turns.get(bot_id)
    if not turn or turn.done or not turn.run_id:
        return False
    return await hermes.steer(bot_id, turn.run_id, text)


def start(bot_id: str, text: str, consent_text: str | None = None, reply_to: dict | None = None,
          device: str | None = None) -> Turn:
    """Starts a turn Colin sent from the app. Raises Busy if one is already running.
    `consent_text` is what Colin himself said, when `text` carries more than his words (a call's request
    in the call voice's words plus its guide). Only his own words count as a go-ahead for sign-ins."""
    if is_busy(bot_id):
        raise Busy(bot_id)
    release(bot_id)
    turn = Turn(bot_id, "app")
    turn.typed = consent_text is None    # a call's hand-off passes the words it heard as consent_text
    turn.device = device
    _turns[bot_id] = turn
    own_words = text if consent_text is None else consent_text
    turn.own_words = own_words
    asyncio.create_task(_run(turn, _with_reactions(bot_id, _with_reply(text, reply_to)), own_words))
    return turn


def _name(bot_id: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    return bot.name if bot else "Zero"


def silent_line(bot_id: str) -> str:
    """What the chat says when a Zip's turn ends with nothing to show, instead of typing dots that just stop."""
    return f"{_name(bot_id)} stopped without answering. Send it again in a moment."


def _with_reply(text: str, reply_to: dict | None) -> str:
    """The person's words with the message they replied to on a quiet first line (history.reply_line); the chat shows
    the words, with the quote above them."""
    if not reply_to or not " ".join(str(reply_to.get("text") or "").split()):
        return text
    return history.reply_line(reply_to["text"], reply_to.get("who") or "bot", who.first()) + "\n" + text


def _with_reactions(bot_id: str, text: str) -> str:
    """Colin's reactions since the last message ride along as quiet context (the app hides this line), and so does
    a request he stopped, so the Zip does not pick it back up on its own (see stop)."""
    text = _with_stopped(bot_id, text)
    told = store.take_untold_reactions(bot_id)
    if not told:
        return text
    # One line: history.peel takes the note off at its first line break.
    notes = "; ".join(f'{r["kind"]} on "{" ".join((r["excerpt"] or "").split())[:80]}"' for r in told)
    return (f"[Reactions from {who.first()} since your last message: {notes}. Context only; no need to mention "
            f"them.]\n{text}")


async def _run(turn: Turn, text: str, own_words: str) -> None:
    lock = _lock(turn.bot_id)
    failed: str | None = None
    if own_words.strip():
        try:   # cards raised before this are no longer what the Zip waits on (signin.waiting)
            store.note_said(turn.bot_id)
            choices.close_open(turn.bot_id)
        except Exception:
            log.warning("could not note when the person spoke", exc_info=True)
    try:
        async with lock:
            # Colin's own message is a go-ahead for sign-ins this request needs (see consent.py).
            try:
                sid = await hermes.ensure_session(turn.bot_id)
                if own_words.strip():
                    consent.open_pass("message", own_words, bot_id=turn.bot_id, session_id=sid)
            except Exception:
                log.warning("could not record the request for sign-in consent", exc_info=True)
            try:   # "I signed in, try again": the open card is answered and the Zip checks the page (#59-#61)
                from . import signin
                # Typed words only: "okay, go ahead" said on a call about something else must not close a card
                # as signed in (and record an allowed sign-in for its site). On a call the card is tapped.
                site = signin.done_in_chat(turn.bot_id, own_words) if turn.typed else None
                if site:
                    log.info("%s's sign-in card for %s answered from the chat", turn.bot_id, site)
            except Exception:
                log.warning("could not read the message as a finished sign-in", exc_info=True)
            activity.expect(turn.bot_id, own_words)   # the app can offer Retry with these words
            answered = False
            async for ev in hermes.stream_turn(turn.bot_id, text):
                if ev["type"] == "run" and turn.stopped:
                    await hermes.stop(turn.bot_id, ev["id"])   # Stop came before Hermes named the run
                if ev["type"] == "reply" and chatter.interrupted(ev["text"]):
                    # Hermes's "Operation interrupted: ..." is its note about the run, never the Zip's words: after
                    # Stop the chat shows "Stopped."; otherwise the turn ended without an answer (live convo QA).
                    if turn.stopped:
                        continue
                    ev = {"type": "error", "text": silent_line(turn.bot_id)}
                if ev["type"] == "reply" and chatter.empty(ev["text"]) and not turn.stopped:
                    ev = {"type": "error", "text": silent_line(turn.bot_id)}   # a "." after typing (#51)
                if ev["type"] == "reply" and not turn.stopped:
                    turn.replied = True   # Stop from here on comes too late: the answer exists (see stop)
                    if turn.typed:
                        ev = await _short(turn, ev, own_words)
                if ev["type"] == "done" and not answered and not turn.stopped and not turn.yielded:
                    # The run ended with no reply and no error: the typing dots would just stop (#50-#52).
                    failed = silent_line(turn.bot_id)
                    await turn.push({"type": "error", "text": failed})
                if ev["type"] == "error" and turn.yielded:
                    continue   # the run made way for the person's message (send); its card stays open
                if ev["type"] == "error":
                    if turn.stopped:
                        ev = {"type": "stopped", "text": STOPPED_LINE}
                    else:
                        failed = ev.get("text") or ""
                if ev["type"] == "done" and turn.stopped and \
                        not any(e.get("type") in ("stopped", "reply") for e in turn.events):
                    # A stopped run whose stream just ends: the open chat hears it before "done" ends its stream.
                    await turn.push({"type": "stopped", "text": STOPPED_LINE})
                answered = answered or ev["type"] in ("reply", "error", "stopped", "yielded")
                await turn.push(ev)
                if ev["type"] == "reply" and not turn.stopped:
                    await _tell_away(turn, ev["text"])
                if ev["type"] == "reply" and turn.bot_id != "default":
                    # Recorded for the home preview and the bot's activity; never a notification,
                    # because Colin started this conversation in the app.
                    store.add_feed("message_local", turn.bot_id, None, config.BOTS_BY_ID[turn.bot_id].name,
                                   ev["text"][:600])
    except Exception as e:
        log.exception("turn failed")
        failed = "Something went wrong with that reply. Try again in a moment."
        await turn.push({"type": "error", "text": failed})
        await turn.push({"type": "done"})
    finally:
        # A reply that already arrived is the answer; Stop came too late to say "Stopped." over it.
        if turn.stopped and not any(e.get("type") in ("stopped", "reply") for e in turn.events):
            await turn.push({"type": "stopped", "text": STOPPED_LINE})
        if not turn.done:
            await turn.push({"type": "done"})
        try:
            replied = any(e.get("type") == "reply" for e in turn.events)
            activity.finished(turn.bot_id, None if replied else failed, own_words, stopped=turn.stopped)
        except Exception:
            log.warning("could not record how the turn ended", exc_info=True)
        try:   # his words covered this turn; later hub-started turns in the same chat need their own go-ahead
            sid = store.session_for(turn.bot_id)
            if sid:
                consent.close_message_passes(sid)
        except Exception:
            log.warning("could not close the request's sign-in pass", exc_info=True)
        if _turns.get(turn.bot_id) is turn:
            _turns.pop(turn.bot_id, None)
        choices.flush(turn.bot_id)            # choice cards offered in this turn go up under its reply
        notices.chat_activity(turn.bot_id)   # a quiet wake waits for two idle hours after this
        briefing.refresh_soon(turn.bot_id)   # the call voice's knowledge stays current
        if turn.typed and own_words.strip() and not turn.stopped:
            # Facts in the person's own words (a brand, a store, a decision) are saved for the whole team; a code
            # check decides first whether there is anything to read (extract.signal), so most turns cost nothing.
            try:
                from . import extract
                reply = next((e.get("full") or e.get("text") or "" for e in reversed(turn.events)
                              if e.get("type") == "reply"), "")
                extract.after_turn(turn.bot_id, own_words, reply, store.session_for(turn.bot_id))
            except Exception:
                log.warning("could not start the fact extractor", exc_info=True)


async def _tell_away(turn: Turn, text: str) -> None:
    """The reply as a notification when the person left the app after sending (Colin, Oct 11: "Tell me if I have
    anything due today" to School, then the answer arrived in silence). Typed messages only: a call's hand-off is
    answered on the call. `urgent` because they are waiting on it: no night hold and no snooze, though a Zip whose
    notifications are off stays quiet (watcher.push)."""
    if not turn.typed or not looking.away(turn.device) or chatter.empty(text):
        return
    from . import watcher
    # The reply's first paragraph is its key point (the short version leads with it), so the notification is that
    # paragraph in the Zip's own words, with no second rewrite (Fable's usage review, Oct 11).
    first = next((p for p in re.split(r"\n\s*\n", text.strip()) if p.strip() and not p.lstrip().startswith("MEDIA:")),
                 text)
    try:
        await watcher.push(_name(turn.bot_id), first, turn.bot_id, urgent=True, kind="reply", exact=True)
    except Exception:
        log.warning("could not send the reply as a notification", exc_info=True)


async def _short(turn: Turn, ev: dict, own_words: str) -> dict:
    """A long finished reply the person did not ask to be long is shown short (shortreply.py), with the original as
    `full`; the Zip's history keeps the original. The reply is held while Haiku writes it (about 2 to 3 s, at most
    shortreply.TIMEOUT_SEC); anything that goes wrong shows the reply as written. Typed chat only: a call's
    hand-off goes back to the call voice, which has its own two-sentence rule."""
    from . import shortreply
    started = time.monotonic()
    short = await shortreply.for_reply(turn.bot_id, own_words, ev["text"])
    if not short or turn.stopped:
        return ev
    return {**ev, "text": short, "full": ev["text"], "held_ms": int((time.monotonic() - started) * 1000)}


async def run_internal(bot_id: str, text: str, continues: str | None = None) -> str | None:
    """A hub-initiated turn (board updates, notes, call wrap-ups). Messages Colin is waiting on go first.
    `continues`: the session whose latest request from Colin this turn carries on (the bot picking up after
    he answered a card). His words count again for this turn only; every other hub turn runs without them."""
    while _queued.get(bot_id) or (continues is None and held(bot_id)):
        await asyncio.sleep(0.5)
    async with _lock(bot_id):
        reopened = None
        if continues:
            try:
                reopened = consent.reopen_pass(continues)
            except Exception:
                log.warning("could not carry the person's request into the follow-up turn", exc_info=True)
        try:
            return await hermes.run_turn(bot_id, text)
        finally:
            if reopened:
                try:
                    consent.close_message_passes(continues)
                except Exception:
                    log.warning("could not close the request's sign-in pass", exc_info=True)


async def follow_up(bot_id: str, note: str, session_id: str | None, urgent: bool, push: bool = True) -> str | None:
    """The bot picks a conversation back up after Colin answered one of its cards (allowed, said not now,
    saved a login, signed in or closed the card). The reply goes into the chat and to his phone, because he
    may have put it down after tapping (Oct 8 QA: resumed answers arrived silently, and Not Now resumed
    nothing at all). `urgent` when the card came from something he asked for."""
    from . import watcher
    hermes.last_error.pop(bot_id, None)
    hermes.last_yield.pop(bot_id, None)
    try:
        reply = await run_internal(bot_id, note, continues=session_id or store.session_for(bot_id))
    except Exception:
        log.exception("could not pick %s's conversation back up", bot_id)
        reply = None
    name = _name(bot_id)
    if not reply and hermes.last_yield.pop(bot_id, False):
        return None   # it was held on a card and made way for the person's own message, which is answered instead
    if reply and not chatter.empty(reply):
        from . import shortreply   # the chat and the notification show it short when it is long (shortreply.py)
        shown = await shortreply.for_reply(bot_id, None, reply) or reply
        store.add_feed("message", bot_id, None, name, shown)
        if push:   # not for a card answered in the open app: the reply shows in the chat he is looking at
            await watcher.push(name, shown, bot_id, urgent=urgent, kind="needs")
        return reply
    # He tapped, and nothing came back (#50-#53): the chat says so plainly, with the cause when there is one.
    why = hermes.last_error.pop(bot_id, "")
    line = (f"{name} didn't pick this back up after your answer." + (f" {why}" if why else "") +
            f" Ask {name} here to carry on.")
    store.add_feed("checkin", bot_id, None, f"{name} didn't pick this back up", line)
    await watcher.push(name, line, bot_id, urgent=urgent, kind="needs")
    return None


# MARK: Stop (fluid app, Oct 9)
#
# Grok Bot staff on a "Stop didn't work" report: "Stop did work each time, but your other bots' queued messages
# immediately started a new turn, so it looked like he ignored it" (research row #55). Here Stop interrupts the
# Zip's running turn, hands the person's own queued messages back to the app (they go back in the message box, not
# sent), and holds the work the hub would start in that chat (board updates, teammates' news, call wrap-ups) until
# the person talks to the Zip again or HOLD_SEC passes. A card the person answers still picks the chat back up.

HOLD_SEC = 15 * 60
STOPPED_LINE = "Stopped."
_held: dict[str, float] = {}
# The request the person stopped, told to the Zip with their next message. Without it the Zip read its cut-off
# turn as a glitch and finished the stopped work in its next reply (live convo QA, Oct 9: To-Do built the stopped
# prep list while answering a different question). Protocol string: history.strip hides it like the reactions line.
STOPPED_PREFIX = "[Stopped by "
_stopped: dict[str, str] = {}


def _with_stopped(bot_id: str, text: str) -> str:
    words = _stopped.pop(bot_id, "")
    if not words:
        return text
    first = who.first()
    short = " ".join(words.split())
    short = short if len(short) <= 200 else short[:200].rsplit(" ", 1)[0] + "..."
    return (f'{STOPPED_PREFIX}{first}: "{short}". {first} pressed Stop on that request, so it is cancelled. Do not '
            f"finish or restart it unless {first} asks for it again. Context only; no need to mention it.]\n{text}")


def held(bot_id: str) -> bool:
    until = _held.get(bot_id)
    if until is None:
        return False
    if until <= time.time():
        _held.pop(bot_id, None)
        return False
    return True


def release(bot_id: str) -> None:
    _held.pop(bot_id, None)


async def stop(bot_id: str) -> dict:
    """Stops what the Zip is doing in its chat. Returns whether anything was running, and the person's own queued
    messages, which are not sent."""
    _held[bot_id] = time.time() + HOLD_SEC
    returned = []
    for t in list(_queued.get(bot_id) or []):
        t.stopped = True
        returned.append(t.own_words)
        await t.push({"type": "stopped", "returned": True, "text": "Not sent. It is back in the message box."})
        await t.push({"type": "done"})
    _queued.pop(bot_id, None)
    turn = _turns.get(bot_id)
    # A reply held while it is shortened (_short, up to shortreply.TIMEOUT_SEC with the typing dots still showing)
    # already exists: Stop comes too late, as for a reply that already arrived. Marking the turn stopped then sent the
    # reply as written while the history showed it short, and told the Zip with the next message that the request it
    # had just answered was cancelled.
    answered = bool(turn and not turn.done and turn.replied)
    running = (bool(turn and not turn.done) or _lock(bot_id).locked()) and not answered
    if turn and not turn.done and not answered:
        turn.stopped = True
        if turn.source == "app" and (turn.own_words or "").strip():
            _stopped[bot_id] = turn.own_words
    run = (turn.run_id if turn and turn.run_id else None) or activity.run_id(bot_id)
    asked = await hermes.stop(bot_id, run) if run else False
    if running:
        log.info("%s stopped by the person (run %s, %s)", bot_id, run or "not named yet",
                 "interrupted" if asked else "will stop when it is named" if turn else "hub work held")
    return {"stopped": running, "returned": returned, "held_until": _held[bot_id]}


def queued_counts() -> dict[str, int]:
    """How many of the person's messages wait behind each Zip's running turn (the app's "Queued" line)."""
    # GET /api/feed is a sync endpoint (a worker thread) while the event loop adds and drops queues: copy first.
    return {b: len(q) for b, q in list(_queued.items()) if q}
