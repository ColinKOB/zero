""







import asyncio
import logging
import math
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime

from . import (activity, briefing, buzz, chatter, choices, config, consent, drafts, firstline, handoff, hermes,
               history, looking, home, notices, shortreply, store, turnsummary, who)

log = logging.getLogger("taffy.turns")
KEEPALIVE_SEC = 15


class Busy(Exception):
    ""


class WakeFailed(RuntimeError):
    ""

    def __init__(self, retryable: bool):
        super().__init__("The wake could not start." if retryable else "The wake did not finish.")
        self.retryable = retryable


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
        self.ended_at = 0.0
        self.run_id: str | None = None
        self.own_words = ""
        self.request_pass = None
        self.stopped = False
        self.typed = True
        self.yielded = False
        self.replied = False
        self.summary_source = ""
        self.summary_recorded = False
        self.used_tools = False
        self.card_answer = False
        self.steered: list[str] = []
        self.resumed = False
        self.asked_again = False
        self.retried = False
        self.handed_off = False
        self.quiet_ok = False
        self.replayed = False
        self.dispatched = False
        self.stop_pressed = False
        self.replays: list["Turn"] = []
        self.steer_words: dict[str, str] = {}
        self.follow_devices: list[str] = []
        self.maybe: list[str] = []
        self.closed = False
        self.device: str | None = None
        self.draft_raw = ""
        self.draft_shown = ""
        self.draft_at = 0.0
        self.draft_kept = False



        from . import openloops
        self.loops_generation = openloops._generation
        self._changed = asyncio.Condition()

    def record_request(self, request_pass) -> None:
        if self.request_pass is not None:
            origin = dict(max((self.request_pass, request_pass), key=lambda p: p["expires"]))
            origin["text"] = self.request_pass["text"] + "\n" + request_pass["text"]
            request_pass = origin
        self.request_pass = request_pass

    async def push(self, ev: dict) -> None:
        async with self._changed:
            if ev.get("type") == "done" and self.summary_recorded:
                turnsummary.finish(self)
            self.events.append(ev)
            if ev.get("type") == "run":
                self.run_id = ev.get("id")
            if ev.get("type") == "done":
                self.done = True
                self.ended_at = time.time()
            self._changed.notify_all()

    async def follow(self) -> AsyncIterator[dict | None]:
        ""
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
_recent: dict[str, "Turn"] = {}
_queued: dict[str, list[Turn]] = {}
_steering: dict[str, list[Turn]] = {}
_locks: dict[str, asyncio.Lock] = {}
_steer_locks: dict[str, asyncio.Lock] = {}



_internal: dict[str, int] = {}
_stepped_aside: dict[str, float] = {}


def _lock(bot_id: str) -> asyncio.Lock:
    return _locks.setdefault(bot_id, asyncio.Lock())


def busy_bots() -> list[str]:
    return sorted(b for b in config.BOTS_BY_ID if is_busy(b))


def is_busy(bot_id: str) -> bool:
    return bot_id in _turns or _lock(bot_id).locked() or bool(_queued.get(bot_id)) or bool(_steering.get(bot_id))


def shows_working(bot_id: str) -> bool:
    ""

    return is_busy(bot_id) or bot_id in activity.retrying_ids()


def current(bot_id: str) -> Turn | None:
    return _turns.get(bot_id)


def colin_messages(bot_id: str, since: float) -> list[Turn]:
    ""





    waiting = {id(t) for t in (_queued.get(bot_id) or []) + (_steering.get(bot_id) or [])}
    seen: set[str] = set()
    out = []
    for t in sorted((t for t in list(_recent.values()) if t.bot_id == bot_id and t.source == "app"
                     and t.started >= since and (t.own_words or "").strip() and id(t) not in waiting),
                    key=lambda t: t.started):
        words = " ".join(t.own_words.split())
        if words not in seen:
            seen.add(words)
            out.append(t)
    return out


def in_internal(bot_id: str) -> bool:
    return _internal.get(bot_id, 0) > 0


def by_id(turn_id: str) -> Turn | None:
    return _recent.get(turn_id)


def latest(bot_id: str) -> Turn | None:
    ""
    q = _queued.get(bot_id)
    steering = _steering.get(bot_id)
    return q[-1] if q else steering[-1] if steering else _turns.get(bot_id)


def send(bot_id: str, text: str, reply_to: dict | None = None, device: str | None = None,
         note: str | None = None, tester: str | None = None) -> Turn:
    ""




















    release(bot_id)
    if choices._flat(text) not in choices.card_answers():
        text = history.defang(text)
    if tester:
        note = "\n".join(n for n in (who.test_pairing(tester), note) if n)
    words = "" if tester else text
    try:
        return start(bot_id, text, reply_to=reply_to, device=device, note=note, tester=bool(tester))
    except Busy:
        pass
    running = _turns.get(bot_id)


    if _steering.get(bot_id) or (_steerable(running, bot_id) and not _has_files(text)):
        return _steer(bot_id, running, _with_reply(text, reply_to, note), words, device)
    turn = Turn(bot_id, "app")
    turn.own_words = words
    turn.device = device
    turn.card_answer = _card_answer(bot_id, text)
    _enqueue(turn, _with_reply(text, reply_to, note))
    return turn





STEER_WAIT_SEC = 5


def _steerable(running: "Turn | None", bot_id: str) -> bool:
    ""


    return bool(running and running.run_id and not running.done and not running.stopped and not running.yielded
                and not running.replied and not _queued.get(bot_id) and not activity.waiting(bot_id))


def _has_files(text: str) -> bool:
    from . import uploads
    return any(uploads.LINE.match(line) for line in str(text or "").splitlines())


def _steer_lock(bot_id: str) -> asyncio.Lock:
    return _steer_locks.setdefault(bot_id, asyncio.Lock())


def _steer(bot_id: str, running: "Turn | None", text: str, words: str, device: str | None) -> Turn:
    ""
    turn = Turn(bot_id, "app")
    turn.own_words = words
    turn.device = device
    _steering.setdefault(bot_id, []).append(turn)
    asyncio.get_running_loop().create_task(_steer_in(running, turn, text))
    return turn


async def _steer_in(running: "Turn | None", turn: Turn, text: str) -> None:
    bot_id, outcome = turn.bot_id, False
    try:
        async with _steer_lock(bot_id):
            if not turn.stopped and running is not None and running is _turns.get(bot_id) \
                    and _steerable(running, bot_id) and not _has_files(text):
                turn.dispatched = True
                running.maybe.append(turn.own_words)
                running.steer_words[text] = turn.own_words
                if turn.device and turn.device not in running.follow_devices:
                    running.follow_devices.append(turn.device)
                try:
                    outcome = await asyncio.wait_for(hermes.steer_outcome(bot_id, running.run_id, text),
                                                     STEER_WAIT_SEC)
                except TimeoutError:
                    outcome = None
                finally:
                    if turn.own_words in running.maybe:
                        running.maybe.remove(turn.own_words)
    finally:
        pending = _steering.get(bot_id) or []
        if turn in pending:
            pending.remove(turn)
    if turn.stopped:
        return
    if outcome is not False:


        if outcome is None:
            log.warning("no answer to a steer for %s (run %s); treated as delivered", bot_id, running.run_id)
        _took(running, turn)
        await turn.push({"type": "steered"})



        async with running._changed:
            await running._changed.wait_for(lambda: running.done)
        for replay in list(running.replays):
            async with replay._changed:
                await replay._changed.wait_for(lambda: replay.done)
        await turn.push({"type": "done"})
        return

    if (running is not None and running.stopped) or turn.stop_pressed:

        turn.stopped = True
        await turn.push({"type": "stopped", "returned": True, "text": "Not sent. It is back in the message box."})
        await turn.push({"type": "done"})
        return
    _enqueue(turn, text)


def _took(running: Turn, turn: Turn) -> None:
    ""


    words = turn.own_words
    if not words.strip():
        log.info("%s's running job took a test pairing's message (run %s)", turn.bot_id, running.run_id)
        return
    late = running.closed
    if not late:
        running.steered.append(words)
    if running.stopped and turn.bot_id in _stopped:
        _stopped[turn.bot_id] += " / " + words

    def step(what: str, fn) -> None:
        try:
            fn()
        except Exception:
            log.warning("could not %s for %s's steered message", what, turn.bot_id, exc_info=True)


    step("note that the person spoke", lambda: store.note_said(turn.bot_id, at=turn.started))
    step("close the open cards", lambda: choices.close_open(turn.bot_id, before=turn.started, answer=words))
    if late:


        step("remember the facts", lambda: _remember(running, words))
    else:
        def cover() -> None:
            sid = store.session_for(turn.bot_id)
            if sid and words.strip():
                running.record_request(consent.get_pass(_open_message_pass(turn.bot_id, words, sid)))
        step("cover it for sign-ins", cover)
    log.info("%s's running job took a typed message (run %s)", turn.bot_id, running.run_id)


def _open_message_pass(bot_id: str, words: str, sid: str) -> int:
    ""


    asking = choices.asking_words(words)
    pid = consent.open_pass("message", words, bot_id=bot_id, session_id=sid)
    note = getattr(consent, "card_words", None)
    if asking is not None and note is not None:
        note(pid, *asking)
    return pid


def _remember(turn: Turn, words: str) -> None:
    ""
    from . import choices, extract
    words = choices.own_words(words)
    if not (words.strip() and not turn.stopped):
        return
    reply = next((e.get("full") or e.get("text") or "" for e in reversed(turn.events) if e.get("type") == "reply"), "")
    extract.after_turn(turn.bot_id, words, reply, store.session_for(turn.bot_id))


def _enqueue(turn: Turn, text: str, first: bool = False) -> None:
    ""

    bot_id = turn.bot_id
    yielding = activity.waiting(bot_id) and not _queued.get(bot_id)
    background = not yielding and in_internal(bot_id) and bot_id not in _turns and not _queued.get(bot_id)
    line = _queued.setdefault(bot_id, [])
    if first:
        line.insert(0, turn)
    else:
        line.append(turn)
    loop = asyncio.get_running_loop()
    if yielding:
        loop.create_task(_make_way(bot_id, turn))
    elif background:
        loop.create_task(_step_aside(bot_id, turn))
    else:
        loop.create_task(turn.push({"type": "queued", "position": len(_queued[bot_id]), "text": QUEUED_LINE}))
    loop.create_task(_run_queued(turn, text))


async def _make_way(bot_id: str, waiting_for: Turn) -> None:
    ""

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
        hermes.last_yield.pop(bot_id, None)
        if cur:
            cur.yielded = False
    await waiting_for.push({"type": "queued", "position": 1, "text": QUEUED_LINE})






STEP_ASIDE_AFTER_SEC = 4


async def _step_aside(bot_id: str, waiting_for: Turn) -> None:
    ""

    loop = asyncio.get_running_loop()
    end = loop.time() + STEP_ASIDE_AFTER_SEC
    asked = None
    while waiting_for in (_queued.get(bot_id) or []):
        run = activity.run_id(bot_id) if in_internal(bot_id) else None
        if in_internal(bot_id) and loop.time() >= end and run != asked:
            if run:
                asked = run
                hermes.yielded_runs.add(run)
                _stepped_aside[bot_id] = time.time()
            if run and await hermes.stop(bot_id, run):
                log.info("%s's own work made way for the person's message (run %s)", bot_id, run)
            else:
                if run:
                    hermes.yielded_runs.discard(run)
                    _stepped_aside.pop(bot_id, None)
                await waiting_for.push({"type": "queued", "position": 1, "text": QUEUED_LINE})
                return
        await asyncio.sleep(0.1)


QUEUED_LINE = "Queued, it will see this next."


async def _run_queued(turn: Turn, text: str) -> None:
    try:
        while True:
            if turn.stopped:
                return
            q = _queued.get(turn.bot_id) or []
            if q and q[0] is turn and turn.bot_id not in _turns and not _lock(turn.bot_id).locked():
                break
            await asyncio.sleep(0.5)
        q.pop(0)
        _turns[turn.bot_id] = turn
        await turn.push({"type": "started"})
        await _run(turn, _with_reactions(turn.bot_id, text), text if turn.own_words else "")
    except Exception:
        log.exception("queued turn failed")
        if turn in (_queued.get(turn.bot_id) or []):
            _queued[turn.bot_id].remove(turn)
        await turn.push({"type": "error", "text": "That message could not be sent. Try again in a moment."})
        await turn.push({"type": "done"})


async def steer(bot_id: str, text: str) -> bool:
    ""
    turn = _turns.get(bot_id)
    if not turn or turn.done or not turn.run_id:
        return False
    return await hermes.steer(bot_id, turn.run_id, text)


def start(bot_id: str, text: str, consent_text: str | None = None, reply_to: dict | None = None,
          device: str | None = None, note: str | None = None, tester: bool = False) -> Turn:
    ""


    if is_busy(bot_id):
        raise Busy(bot_id)
    release(bot_id)
    turn = Turn(bot_id, "app")
    turn.typed = consent_text is None
    turn.device = device
    turn.card_answer = _card_answer(bot_id, text)
    _turns[bot_id] = turn
    own_words = "" if tester else text if consent_text is None else consent_text
    turn.own_words = own_words
    asyncio.create_task(_run(turn, _with_reactions(bot_id, _with_reply(text, reply_to, note)), own_words))
    return turn


def _card_answer(bot_id: str, text: str) -> bool:
    return firstline.take_card_answer(text, bot_id)


def _name(bot_id: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    return bot.name if bot else "Zero"


def silent_line(bot_id: str) -> str:
    ""

    return "I stopped before I finished this. Tap Retry and I'll pick it back up."


def _left_a_card(bot_id: str, since: float) -> bool:
    ""



    from . import accounts, mailcard, purchases, signin, textcard, twofactor, wallet
    if choices.pending(bot_id, since):
        return True
    try:
        with consent.db() as c:

            if c.execute("SELECT 1 FROM requests WHERE bot_id=? AND COALESCE(raised,created)>=? "
                         f"AND COALESCE(test,0)=0 AND COALESCE(why,'') != 'prepared' AND {consent.VISIBLE_REQUEST_SQL} LIMIT 1",
                         (bot_id, since)).fetchone():
                return True
        kinds = (signin.KIND, twofactor.KIND, accounts.KIND, purchases.PAY_KIND, wallet.REQUEST_KIND, mailcard.KIND,
                 textcard.KIND)
        return store.db().execute(
            f"SELECT 1 FROM feed WHERE bot_id=? AND kind IN ({','.join('?' * len(kinds))}) AND created_at>=? LIMIT 1",
            (bot_id, *kinds, int(since))).fetchone() is not None
    except Exception:
        log.warning("could not read %s's cards", bot_id, exc_info=True)
        return False


def _with_reply(text: str, reply_to: dict | None, note: str | None = None) -> str:
    ""

    if reply_to and " ".join(str(reply_to.get("text") or "").split()):
        text = history.reply_line(reply_to["text"], reply_to.get("who") or "bot", who.first()) + "\n" + text
    return f"{note}\n{text}" if note else text


def _with_reactions(bot_id: str, text: str) -> str:
    ""

    text = _with_stopped(bot_id, text)
    told = store.take_untold_reactions(bot_id)
    if not told:
        return text

    notes = "; ".join(f'{r["kind"]} on "{" ".join((r["excerpt"] or "").split())[:80]}"' for r in told)
    return (f"[Reactions from {who.first()} since your last message: {notes}. Context only; no need to mention "
            f"them.]\n{text}")


def sent_line(t: float) -> str:
    ""

    day = datetime.fromtimestamp(t, home.TZ).strftime("%a %b %-d")
    return f"{history.SENT_PREFIX}{day} at {notices.clock(t)} {home.tz_word()}]"


def _replayed_words(running: Turn, text: str) -> str:
    ""


    hits = [w for t, w in running.steer_words.items() if t and t in text]
    if hits:
        return "\n".join(w for w in hits if w)
    return "" if who.TEST_PAIRING_PREFIX in text else text


def _handed_back(running: Turn, text: str) -> list[tuple[str, str]]:
    ""


    def said(words: str) -> list[str]:
        return [line for line in words.split("\n") if line.strip() and not history.quiet_line(line)]
    found = sorted((text.find(steer), steer) for steer in running.steer_words if steer and steer in text)
    if len(found) > 1 and said(text) == [line for _, steer in found for line in said(steer)]:
        return [(steer, running.steer_words[steer]) for _, steer in found]
    return [(text, _replayed_words(running, text))]


async def _run(turn: Turn, text: str, own_words: str) -> None:
    from . import usage
    with turnsummary.recording(turn), usage.errand("chat" if turn.typed else "voice-handoff", turn.bot_id, errand_id=turn.id) as worker:
        if turn.typed:
            text = f"[Chat turn: {turn.id}]\n{sent_line(turn.started)}\n{text}"
        await _run_measured(turn, text, own_words)
        worker["outcome"] = ("cancelled" if turn.stopped else "yielded" if turn.yielded else
                             "answered" if turn.replied else "no_answer")


async def _run_measured(turn: Turn, text: str, own_words: str) -> None:
    from . import turnresume
    lock = _lock(turn.bot_id)
    failed: str | None = None
    hub_stopping = False
    if turn.typed and own_words.strip():
        turnresume.began(turn.id, turn.bot_id, own_words)
    if own_words.strip():
        try:
            store.note_said(turn.bot_id)




            if turn.typed:
                choices.close_open(turn.bot_id, answer=own_words)
        except Exception:
            log.warning("could not note when the person spoke", exc_info=True)


    if turn.typed and not turn.stopped:
        try:
            await firstline.begin(turn, own_words)
        except Exception:
            log.warning("could not start the first line", exc_info=True)
    try:
        async with lock:

            try:
                sid = await hermes.ensure_session(turn.bot_id)
                if own_words.strip():
                    turn.record_request(consent.get_pass(_open_message_pass(turn.bot_id, own_words, sid)))
            except Exception:
                log.warning("could not record the request for sign-in consent", exc_info=True)
            try:
                from . import signin


                site = signin.done_in_chat(turn.bot_id, own_words) if turn.typed else None
                if site:
                    log.info("%s's sign-in card for %s answered from the chat", turn.bot_id, site)
            except Exception:
                log.warning("could not read the message as a finished sign-in", exc_info=True)
            if turn.typed:
                text = _with_card_send(turn, text, own_words)
            activity.expect(turn.bot_id, own_words)
            answered = False
            async for ev in _finishing(turn, text):
                if turn.typed and ev["type"] in ("delta", "delta_reset", "tool_started", "reply"):
                    await _draft(turn, ev)
                if ev["type"] == "delta_reset":
                    await firstline.receive(turn, ev)
                if ev["type"] in ("delta", "delta_reset"):
                    continue
                if ev["type"] == "tool_started":
                    turn.used_tools = True
                    if ev.get("did"):
                        try:
                            turnsummary.note_step(turn, ev["did"])
                        except Exception:
                            log.warning("could not keep the turn's step", exc_info=True)
                if ev["type"] == "checklist":
                    try:
                        turnsummary.note_checklist(turn, ev)
                    except Exception:
                        log.warning("could not keep the turn's checklist", exc_info=True)
                said = ev["type"] == "commentary"
                ev = await firstline.receive(turn, ev)
                if said and turn.typed:
                    await _draft(turn, ev or {"type": "delta_reset"})
                if ev is None:
                    continue
                if ev["type"] == "commentary":
                    from . import shortreply
                    answered = answered or shortreply.has_work(chatter.without_hermes_notes(ev.get("text")))
                    if turn.typed:
                        ev = await _beside(turn, ev)
                if ev["type"] == "pending_steer":


                    if not turn.stopped:

                        devices = ([turn.device] if turn.typed and turn.device else []) + turn.follow_devices
                        handed_back = []
                        for words, own in _handed_back(turn, ev["text"]):
                            replay = Turn(turn.bot_id, "app")
                            replay.own_words = own
                            replay.replayed = True
                            replay.device = devices[0] if devices else None
                            replay.follow_devices = devices[1:]
                            turn.replays.append(replay)
                            handed_back.append((replay, words))
                        for replay, words in reversed(handed_back):
                            _enqueue(replay, words, first=True)
                    continue
                if ev["type"] == "run" and turn.stopped:
                    await hermes.stop(turn.bot_id, ev["id"])
                if ev["type"] == "reply" and chatter.interrupted(ev["text"]):


                    if turn.stopped:
                        continue
                    ev = {"type": "error", "text": silent_line(turn.bot_id)}
                if ev["type"] == "reply" and chatter.empty(ev["text"]) and not turn.stopped:
                    if answered and turn.typed:
                        continue
                    if turn.quiet_ok:
                        answered = True
                        continue
                    if _left_a_card(turn.bot_id, turn.started):
                        if turn.typed:
                            answered = True
                            continue


                        from .voice import WAITING_LINE
                        ev = {"type": "reply", "text": WAITING_LINE}
                    else:
                        ev = {"type": "error", "text": silent_line(turn.bot_id)}
                if ev["type"] == "reply" and not turn.stopped:
                    turn.summary_source = ev["text"]
                    turn.replied = True
                    if turn.typed:
                        said = " ".join([own_words, *turn.steered, *turn.maybe])



                        if turnsummary.worked(turn, replying=True) and not ev.get("topics"):
                            ev = await _short(turn, ev, said)
                if ev["type"] == "done" and not answered and not turn.stopped and not turn.yielded \
                        and not turn.handed_off:

                    failed = silent_line(turn.bot_id)
                    await turn.push({"type": "error", "text": failed})
                if ev["type"] == "error" and turn.yielded:
                    continue
                if ev["type"] == "error":
                    if turn.stopped:
                        ev = {"type": "stopped", "text": STOPPED_LINE}
                    else:
                        failed = ev.get("text") or ""
                if ev["type"] == "done" and turn.stopped and \
                        not any(e.get("type") in ("stopped", "reply") for e in turn.events):

                    await turn.push({"type": "stopped", "text": STOPPED_LINE})
                answered = answered or ev["type"] in ("reply", "error", "stopped", "yielded")
                await turn.push(ev)
                if ev["type"] == "commentary" and choices.ORDERED_FOLLOW_THROUGH in own_words:

                    choices.flush(turn.bot_id)
                if ev["type"] == "reply" and not turn.stopped:
                    await _tell_away(turn, ev["text"])
                if ev["type"] == "reply" and turn.bot_id != "default":


                    store.add_feed("message_local", turn.bot_id, None, config.BOTS_BY_ID[turn.bot_id].name,
                                   ev["text"][:600], written_by="zip", written_for="person")
    except asyncio.CancelledError:
        hub_stopping = True
        raise
    except Exception:
        log.exception("turn failed")
        if turn.typed and not turn.stopped and not any(e.get("type") == "reply" for e in turn.events):

            turn.handed_off = True
            failure = handoff.Failure("passing", "the hub ran into an error of its own", "")
            hid = handoff.start_handoff(turn.bot_id, failure, what=own_words, facts=_facts(turn))
            handoff._soon(handoff.finish_handoff(hid, turn.bot_id, failure))
        else:
            failed = "Something went wrong with that reply. Try again in a moment."
            await turn.push({"type": "error", "text": failed})
        await turn.push({"type": "done"})
    finally:
        if not hub_stopping or turn.stopped or turn.replied:
            turnresume.ended(turn.id)
        turn.closed = True

        if turn.stopped and not any(e.get("type") in ("stopped", "reply") for e in turn.events):
            await turn.push({"type": "stopped", "text": STOPPED_LINE})
        if not turn.done:
            await turn.push({"type": "done"})
        try:
            replied = any(e.get("type") == "reply" for e in turn.events)
            activity.finished(turn.bot_id, None if replied else failed, own_words, stopped=turn.stopped)
        except Exception:
            log.warning("could not record how the turn ended", exc_info=True)
        try:
            sid = store.session_for(turn.bot_id)
            if sid:
                consent.close_message_passes(sid)
        except Exception:
            log.warning("could not close the request's sign-in pass", exc_info=True)
        firstline.end(turn)
        if _turns.get(turn.bot_id) is turn:
            _turns.pop(turn.bot_id, None)
        if turn.typed and turn.replied and not turn.stopped:
            choices.look_again_soon(turn.bot_id, turn.started)
        choices.flush(turn.bot_id)
        notices.chat_activity(turn.bot_id)
        if not turn.stopped:



            try:
                if turn.typed:
                    _remember(turn, own_words)
                for words in turn.steered:
                    _remember(turn, words)
                said = ([own_words] if turn.typed else []) + turn.steered
                if "".join(said).strip():
                    from . import openloops
                    reply = next((e.get("full") or e.get("text") or "" for e in reversed(turn.events)
                                  if e.get("type") == "reply"), "")
                    words, asked = choices.loop_words(said)
                    openloops.after_turn(turn.bot_id, words, reply, generation=turn.loops_generation, asked=asked)
            except Exception:
                log.warning("could not start the fact extractor", exc_info=True)
        briefing.refresh_soon(turn.bot_id)







RESUME_NOTE = ("[Hub note: Restart] A restart cut off your work on the last message, so your last step may not have "
               "finished. Check what you already did in this chat, and don't repeat anything you already sent, saved "
               "or clicked. Then finish the request and answer as usual. Mention the restart only if something was "
               "lost.")
RESUMING_LINE = "Picking this back up after a restart"





ONE_LINE_NOTE = ("[Hub note: One line] {first} wrote to you, and your reply was [SILENT]. Answer {first} in one short "
                 "line now, in your own voice: what you will do with it, or that you have it. Reply with only [SILENT] "
                 "again only if there is truly nothing to say.")


def restart_line(bot_id: str) -> str:
    ""
    return "A restart cut me off partway through. Tap Retry and I'll pick it back up."





RETRYING_LINE = activity.RETRY_STEP


def _retry_note(words: str) -> str:
    return (f"{who.hub_note()} Your last turn ended before it did anything, so none of this has been done yet. "
            f'{who.first()} asked: "{words}" Carry it out now and answer as usual. Don\'t mention the retry.')


def _never_ran(ended: list[dict]) -> bool:
    ""
    if any(e.get("limit") or e.get("unsure") for e in ended):
        return False
    return not any(e["type"] == "reply" and not chatter.interrupted(e.get("text")) for e in ended)


def _ended_short(ev: dict) -> bool:
    ""
    if ev["type"] in ("error", "done"):
        return True
    return ev["type"] == "reply" and (chatter.empty(ev.get("text")) or chatter.interrupted(ev.get("text")))


async def _resumable(turn: Turn, text: str) -> AsyncIterator[dict]:
    ""
    before = hermes.gateway_id()
    held: list[dict] = []
    answered = False
    spoke = False
    worked = False
    hermes.start_failed.pop(turn.bot_id, None)
    async for ev in hermes.stream_turn(turn.bot_id, text):
        worked = worked or ev["type"] in ("status", "tool_started", "commentary")
        if ev["type"] in ("reply", "yielded") and not _ended_short(ev):
            answered = True
        if ev["type"] == "commentary" and not chatter.empty(ev.get("text")):
            spoke = True
        if not answered and _ended_short(ev):
            held.append(ev)
            continue
        if held:
            for h in held:
                yield h
            held = []
        yield ev
    if held and not answered and not spoke and turn.typed and not turn.stopped and not turn.yielded and not turn.asked_again \
            and any(h["type"] == "reply" and chatter.empty(h.get("text")) for h in held) \
            and not hermes.restarted_since(before) and not _left_a_card(turn.bot_id, turn.started):
        turn.asked_again = True
        log.info("%s replied [SILENT] to a typed message in turn %s; asking once more", turn.bot_id, turn.id)
        async for ev in hermes.stream_turn(turn.bot_id, ONE_LINE_NOTE.format(first=who.first())):
            if ev["type"] == "reply" and chatter.empty(ev.get("text")):
                turn.quiet_ok = True
            yield ev
        return
    if held and not answered and not worked and turn.typed and not turn.stopped and not turn.yielded \
            and not turn.retried and _never_ran(held) and not hermes.restarted_since(before):
        async for ev in _run_again(turn, text):
            yield ev
        return
    if not held or answered or turn.stopped or turn.yielded or not hermes.restarted_since(before):
        for h in held:
            yield h
        return
    log.warning("a gateway restart cut off %s's turn %s", turn.bot_id, turn.id)
    yield {"type": "status", "text": RESUMING_LINE}
    if await hermes.until_running(before) and not turn.stopped:
        turn.resumed = True
        async for ev in hermes.stream_turn(turn.bot_id, RESUME_NOTE):
            yield ev
        return
    if turn.stopped:
        yield {"type": "done"}
        return
    yield {"type": "error", "text": restart_line(turn.bot_id), "restart": True}
    yield {"type": "done"}


async def _finishing(turn: Turn, text: str) -> AsyncIterator[dict]:
    ""




    tries, began, prompt = 0, time.time(), text
    known = turn.bot_id in config.BOTS_BY_ID
    while True:
        held: list[dict] = []
        answered = restart = limited = False
        async for ev in _resumable(turn, prompt):
            if ev["type"] in ("reply", "yielded", "stopped") and not _ended_short(ev):
                answered = True
            if ev["type"] == "commentary" and shortreply.has_work(chatter.without_hermes_notes(ev.get("text"))):
                answered = True
            if not answered and _ended_short(ev):
                held.append(ev)
                restart = restart or bool(ev.get("restart"))
                limited = limited or bool(ev.get("limit"))
                continue
            for h in held:
                yield h
            held = []
            yield ev
        if answered or not held or not turn.typed or turn.stopped or turn.yielded or turn.quiet_ok \
                or _left_a_card(turn.bot_id, turn.started):
            for h in held:
                yield h
            return
        failure = await handoff.classify(turn.bot_id, restart=restart, limited=limited)
        if turn.stopped:
            yield {"type": "done"}
            return
        if failure.final:
            for h in held:
                yield h
            return
        if failure.held:
            turn.handed_off = True
            handoff.hold(turn.bot_id, failure, via="chat", note=handoff.held_chat_note(turn.own_words),
                         what=turn.own_words, session_id=store.session_for(turn.bot_id), urgent=True)
            yield {"type": "done"}
            return
        if tries >= len(handoff.RETRY_DELAYS):
            turn.handed_off = True
            activity.done_retrying(turn.bot_id)
            hid = handoff.start_handoff(turn.bot_id, failure, what=turn.own_words, facts=_facts(turn),
                                        tries=tries + 1, minutes=round((time.time() - began) / 60))
            handoff._soon(handoff.finish_handoff(hid, turn.bot_id, failure))
            yield {"type": "done"}
            return
        yield {"type": "status", "text": RETRYING_LINE}
        await _between_tries(turn.bot_id, handoff.RETRY_DELAYS[tries], lambda: turn.stopped)
        if known and turn.bot_id not in config.BOTS_BY_ID:
            turn.handed_off = True
        if turn.stopped or turn.handed_off:
            activity.done_retrying(turn.bot_id)
            yield {"type": "done"}
            return
        tries += 1
        log.info("%s's turn %s didn't finish; quiet try %d", turn.bot_id, turn.id, tries)
        prompt = text if hermes.start_failed.pop(turn.bot_id, False) else handoff.retry_note(turn.own_words)


async def _between_tries(bot_id: str, sec: float, stopped=lambda: False) -> None:
    ""

    loop = asyncio.get_running_loop()
    sec = handoff.wait_sec(sec)
    end, since = loop.time() + sec, time.time()
    while loop.time() < end and not stopped() and not _queued.get(bot_id) and not _stopped_since(bot_id, since):
        activity.retrying(bot_id)
        await asyncio.sleep(min(1.0, max(0.0, end - loop.time())))


def _facts(turn: Turn) -> str:
    ""
    steps = [e.get("text") for e in turn.events if e.get("type") == "status" and e.get("text")
             and e.get("text") != RETRYING_LINE]
    when = f"It began at {notices.clock(turn.started)}."
    return f"{when} Its steps: {'; '.join(steps[-6:])}." if steps else f"{when} It hadn't taken a step yet."


async def _run_again(turn: Turn, text: str) -> AsyncIterator[dict]:
    ""

    turn.retried = True
    log.info("%s's turn %s ended before it did anything; running it once more", turn.bot_id, turn.id)
    yield {"type": "status", "text": RETRYING_LINE}
    note = _retry_note(turn.own_words)
    held: list[dict] = []
    answered = worked = False
    async for ev in hermes.stream_turn(turn.bot_id, text if hermes.start_failed.pop(turn.bot_id, False) else note):
        worked = worked or ev["type"] in ("status", "tool_started", "commentary")
        answered = answered or (ev["type"] in ("reply", "yielded") and not _ended_short(ev))
        if not answered and _ended_short(ev):
            held.append(ev)
            continue
        for h in held:
            yield h
        held = []
        yield ev
    for h in held:
        yield h


def _with_card_send(turn: Turn, text: str, own_words: str) -> str:
    ""
    from . import mailcard
    if not turn.device:
        return text
    if turn.replayed:
        return text
    try:
        line = mailcard.send_said(turn.bot_id, own_words, before=turn.started)
    except Exception:
        log.warning("could not read the message as a send", exc_info=True)
        return text
    if not line:
        return text
    log.info("%s's mail card sent from the chat", turn.bot_id)
    marker = f"[Chat turn: {turn.id}]\n"
    return marker + line + "\n" + text.removeprefix(marker)


async def _draft(turn: Turn, ev: dict) -> None:
    ""


    kind = ev["type"]
    if kind == "delta":
        turn.draft_raw += ev.get("text") or ""
        if time.monotonic() - turn.draft_at < drafts.EVERY_SEC:
            return
    if kind in ("delta", "reply"):
        shown = drafts.visible(turn.draft_raw, turnsummary.worked(turn, replying=True) and shortreply.enabled())
        if shown != turn.draft_shown:
            turn.draft_shown, turn.draft_at = shown, time.monotonic()
            await turn.push({"type": "draft", "text": shown})
        return
    raw, turn.draft_raw = turn.draft_raw, ""
    from . import replypage
    shown = drafts.visible(raw, replypage.wanted(drafts.visible(raw), turn.own_words)) if kind != "delta_reset" else ""
    if kind == "delta_reset":
        if turn.draft_shown or turn.draft_kept:
            await turn.push({"type": "draft_end", "text": "", "keep": False})
        turn.draft_kept = False
    else:
        if turn.draft_shown or shown:
            await turn.push({"type": "draft_end", "text": shown, "keep": True})

        turn.draft_kept = kind == "tool_started" and bool(shown or turn.draft_kept)
    turn.draft_shown, turn.draft_at = "", 0.0


async def _tell_away(turn: Turn, text: str) -> None:
    ""





    devices = ([turn.device] if turn.typed else []) + turn.follow_devices
    if not devices or not any(looking.away(d) for d in devices) or chatter.empty(text):
        return
    from . import watcher


    first = next((p for p in re.split(r"\n\s*\n", text.strip()) if p.strip() and not p.lstrip().startswith("MEDIA:")),
                 text)
    try:


        await watcher.push(_name(turn.bot_id), first, turn.bot_id, urgent=True, kind="reply", exact=True,
                           judged=buzz.decide(turn.bot_id, first, asked=True),
                           subtitle=turn.own_words if turn.typed else "")
    except Exception:
        log.warning("could not send the reply as a notification", exc_info=True)


async def _short(turn: Turn, ev: dict, own_words: str) -> dict:
    ""



    from . import shortreply
    started = time.monotonic()
    short = await shortreply.for_reply(turn.bot_id, own_words, ev["text"])
    if not short or turn.stopped:
        return ev
    return {**ev, "text": short, "full": ev["text"], "held_ms": int((time.monotonic() - started) * 1000)}


async def _beside(turn: Turn, ev: dict) -> dict:
    ""
    from . import replypage, shortreply
    text = ev.get("text") or ""
    if not replypage.wanted(text, turn.own_words):
        return ev
    short = await shortreply.for_reply(turn.bot_id, turn.own_words, text)
    return {**ev, "text": short} if short else ev


def _picked_up(text: str) -> str:
    ""
    return (f"{who.hub_note()} {who.first()} wrote while you were working on this, so you stopped to answer. Part of it "
            "may already be done. Check what you did in this chat and don't repeat anything you already sent, saved or "
            f"clicked. Then finish what is left. What you were working on:\n\n{text}")


async def run_internal(bot_id: str, text: str, continues: str | None = None, on_start=None,
                       skip_if=None, origin_turns: list[Turn] | None = None,
                       cap_sec: float | None = None) -> str | None:
    ""

    prompt = text
    live = id(text) in _live_prompts
    try:
        while True:
            reply = await _internal_turn(bot_id, prompt, continues, on_start, skip_if, origin_turns, cap_sec)
            stepped = _stepped_aside.pop(bot_id, None)

            if stepped is None or reply is not None or not hermes.last_yield.pop(bot_id, False):
                return reply
            while _queued.get(bot_id) or bot_id in _turns:
                await asyncio.sleep(0.5)
            if _stopped_since(bot_id, stepped):
                return reply
            if prompt is text:
                prompt, on_start = _picked_up(text), None
                if live:
                    _live_prompts.add(id(prompt))
    finally:
        if prompt is not text:
            _live_prompts.discard(id(prompt))


async def _internal_turn(bot_id: str, text: str, continues: str | None = None, on_start=None,
                         skip_if=None, origin_turns: list[Turn] | None = None,
                         cap_sec: float | None = None) -> str | None:
    ""






    while _queued.get(bot_id) or (continues is None and held(bot_id)):
        await asyncio.sleep(0.5)
    async with _lock(bot_id):
        if cap_sec is not None and (_queued.get(bot_id) or (continues is None and held(bot_id))):
            return None
        if skip_if is not None and skip_if():
            return None
        _internal[bot_id] = _internal.get(bot_id, 0) + 1
        if on_start is not None:
            try:
                on_start()
            except Exception:
                log.warning("could not mark %s's turn as started", bot_id, exc_info=True)
        reopened = None
        spoken = None



        from . import mac, maccodex
        live = (mac.answered_card(bot_id) if id(text) in _live_prompts or maccodex.live_follow_on(bot_id, text)
                else None)
        origins = [turn.request_pass for turn in origin_turns or []
                   if not turn.stopped and turn.request_pass is not None]
        if origins:
            try:
                origin = dict(max(origins, key=lambda p: p["expires"]))
                origin["text"] = "\n".join(p["text"] for p in origins)
                sid = await hermes.ensure_session(bot_id)
                spoken = consent.relay_pass(origin, bot_id, sid)
            except Exception:
                log.warning("could not carry the person's call into the follow-up turn", exc_info=True)
        if continues:
            try:
                reopened = consent.reopen_pass(continues)
            except Exception:
                log.warning("could not carry the person's request into the follow-up turn", exc_info=True)
        try:
            if cap_sec is not None:
                return await _capped_internal(bot_id, text, cap_sec)
            return await hermes.run_turn(bot_id, text)
        finally:
            if live is not None:
                live.done = True
            _internal[bot_id] -= 1
            if spoken is not None:
                consent.close_pass(spoken)
            if reopened:
                try:
                    consent.close_message_passes(continues)
                except Exception:
                    log.warning("could not close the request's sign-in pass", exc_info=True)


async def _capped_internal(bot_id: str, text: str, cap_sec: float) -> str | None:
    run_id = reply = None
    failed = False
    try:
        async with asyncio.timeout(cap_sec):
            async for ev in hermes.stream_turn(bot_id, text):
                if ev["type"] == "run":
                    run_id = ev["id"]
                elif ev["type"] == "reply":
                    reply = None if chatter.interrupted(ev["text"]) else ev["text"]
                    hermes.last_buzz[bot_id] = ev.get("buzz") if reply else None
                    hermes.last_todo[bot_id] = (ev.get("todo") or []) if reply else []
                elif ev["type"] in ("error", "yielded"):
                    failed = True
        return None if failed else reply
    except (TimeoutError, asyncio.CancelledError) as exc:
        if run_id:
            await hermes.stop(bot_id, run_id)
        if isinstance(exc, asyncio.CancelledError):
            raise
        return None




_live_prompts: set[int] = set()


async def follow_up(bot_id: str, note: str, session_id: str | None, urgent: bool, push: bool = True,
                    tracked: bool = False) -> str | None:
    ""









    from . import watcher
    hermes.last_error.pop(bot_id, None)
    hermes.last_yield.pop(bot_id, None)
    started = time.time()
    mark = None

    first_prompt = prompt = note if urgent else f"{note}\n\n{buzz.guide(bot_id)}"
    reply = raw = None
    tries = 0
    kept: int | None = None
    stopping = False
    known = bot_id in config.BOTS_BY_ID
    try:
        while True:
            for _ in range(1 if tries else 2):
                raw = None


                hermes.start_failed.pop(bot_id, None)
                hermes.last_unsure.pop(bot_id, None)
                try:
                    if urgent:
                        _live_prompts.add(id(prompt))
                    try:
                        raw = await run_internal(bot_id, prompt, continues=session_id or store.session_for(bot_id))
                    finally:
                        _live_prompts.discard(id(prompt))
                    reply, mark = buzz.take(bot_id, raw)
                except Exception:
                    log.exception("could not pick %s's conversation back up", bot_id)
                    reply = None

                if raw is not None or hermes.last_tools.get(bot_id) or hermes.last_limited.get(bot_id) \
                        or hermes.last_unsure.get(bot_id) or hermes.last_yield.get(bot_id) \
                        or _stopped_since(bot_id, started):
                    break
            if raw is not None or hermes.last_yield.get(bot_id) or _stopped_since(bot_id, started) \
                    or _left_a_card(bot_id, started):
                break
            failure = await handoff.classify(bot_id)
            if _stopped_since(bot_id, started):
                break
            if failure.final:
                activity.done_retrying(bot_id)
                return None
            if failure.held:
                handoff.hold(bot_id, failure, via="follow_up", note=handoff.retry_note(work=note),
                             session_id=session_id, urgent=urgent)
                return None
            if tries >= len(handoff.RETRY_DELAYS):
                activity.done_retrying(bot_id)
                await handoff.hand_to_zero(bot_id, failure, what=_asked_last(bot_id, started), tries=tries + 1,
                                           minutes=round((time.time() - started) / 60),
                                           facts="It was picking the chat back up after a card was answered.")
                return None
            if kept is None and not tracked:
                kept = handoff.track(bot_id, handoff.retry_note(work=note), session_id, urgent)
            await _between_tries(bot_id, handoff.RETRY_DELAYS[tries])
            if _stopped_since(bot_id, started) or (known and bot_id not in config.BOTS_BY_ID):
                break
            tries += 1
            log.info("%s didn't pick its chat back up; quiet try %d", bot_id, tries)
            prompt = first_prompt if hermes.start_failed.get(bot_id) else handoff.retry_note(work=note)
        activity.done_retrying(bot_id)
        name = _name(bot_id)
        if not reply and hermes.last_yield.pop(bot_id, False):
            return None
        if not reply and _stopped_since(bot_id, started):
            return None
        ask = choices.pending_ask(bot_id, started)
        if chatter.empty(reply) and _left_a_card(bot_id, started):
            if ask and push:
                await watcher.push(name, ask, bot_id, urgent=urgent, kind="needs", exact=True)
            return None
        if reply and not chatter.empty(reply):
            from . import shortreply
            shown = await shortreply.for_reply(bot_id, None, reply) or reply
            fid = store.add_feed("message", bot_id, None, name, shown, written_by="zip", written_for="person")
            if push:
                await watcher._relay(bot_id, name, shown, fid,
                                     buzz.decide(bot_id, shown, mark, asked=urgent, card=ask),
                                     urgent=urgent, kind="needs")
            return reply
        return None
    except asyncio.CancelledError:
        stopping = True
        raise
    finally:
        if kept is not None and not stopping:
            handoff.untrack(kept)
        activity.done_retrying(bot_id)
        choices.flush(bot_id)


def _asked_last(bot_id: str, before: float) -> str:
    ""
    asked = colin_messages(bot_id, before - 6 * 3600)
    return asked[-1].own_words if asked else ""




CARD_HOLD_SEC = 30
_held_answers: dict[tuple[str, str], dict] = {}


def resume_after_card(bot_id: str, note: str, session_id: str | None, urgent: bool, more_waiting: bool) -> None:
    ""


    key = (bot_id, session_id or "")
    held = _held_answers.pop(key, None)
    if held:
        held["timer"].cancel()
    notes = [*(held["notes"] if held else []), note]
    urgent = urgent or bool(held and held["urgent"])
    if more_waiting:
        _held_answers[key] = {"notes": notes, "urgent": urgent,
                             "timer": asyncio.get_running_loop().call_later(CARD_HOLD_SEC, _resume_held_answers, key)}
        return
    _resume(bot_id, notes, session_id, urgent)


def _resume_held_answers(key: tuple[str, str]) -> None:
    if held := _held_answers.pop(key, None):
        _resume(key[0], held["notes"], key[1] or None, held["urgent"])


def drop_held_answers() -> None:
    ""
    for key in list(_held_answers):
        if held := _held_answers.pop(key, None):
            held["timer"].cancel()


def _resume(bot_id: str, notes: list[str], session_id: str | None, urgent: bool) -> None:
    ""

    first = who.first()
    if len(notes) == 1:
        text = notes[0] if notes[0].startswith(who.tap_prefix()) else f"{who.hub_note()} {notes[0]}"
    else:
        text = (f"{who.hub_note()} {first} answered {len(notes)} of your cards together. Follow each answer below, "
                f"then tell {first} in one reply, in a sentence or two, what was done:\n" + "\n".join(notes))
    asyncio.create_task(follow_up(bot_id, text, session_id, urgent=urgent))


async def wake(bot_id: str, note: str, urgent: bool = False, title: str | None = None, on_start=None,
               skip_if=None, cap_sec: float | None = None, on_result=None,
               raise_on_failure: bool = False, now: bool = False) -> str | None:
    ""






    from . import rulereach, watcher
    reply = None
    failure = None
    since = time.time()
    await rulereach.prepare_wake(bot_id, note)
    if not urgent:
        note = f"{note}\n\n{buzz.guide(bot_id)}"
    for attempt in range(2):
        started = False

        def began():
            nonlocal started
            started = True
            if on_start is not None:
                on_start()

        hermes.last_error.pop(bot_id, None)
        hermes.start_failed.pop(bot_id, None)
        try:
            reply = await run_internal(bot_id, note, on_start=began, **({"skip_if": skip_if} if skip_if else {}),
                                       **({"cap_sec": cap_sec, "origin_turns": None} if cap_sec is not None else {}))
        except Exception:
            log.exception("could not wake %s", bot_id)
            reply = None
            failure = WakeFailed(retryable=not started)
        refused = hermes.start_failed.pop(bot_id, False)
        missing = started and not reply and not hermes.last_yield.get(bot_id)

        if raise_on_failure and (failure or refused or hermes.last_error.get(bot_id) or missing) \
                and not _stopped_since(bot_id, since):
            failure = WakeFailed(retryable=refused or (failure is not None and failure.retryable))
            break
        if reply or not refused:
            break
    reply, mark = buzz.take(bot_id, reply)
    try:
        if raise_on_failure and failure is not None:
            raise failure
        if on_result is not None:
            on_result(reply)
        if reply and not chatter.empty(reply):
            from . import shortreply
            shown = await shortreply.for_reply(bot_id, None, reply) or reply
            fid = store.add_feed("message", bot_id, None, title or _name(bot_id), shown,
                                 written_by="zip", written_for="person")
            await watcher._relay(bot_id, _name(bot_id), shown, fid,
                                 buzz.decide(bot_id, shown, mark, asked=urgent, card=choices.pending_ask(bot_id, since)),
                                 urgent=urgent, **({"now": True} if now else {}))
            return reply
        if ask := choices.pending_ask(bot_id, since):
            await watcher.push(_name(bot_id), ask, bot_id, urgent=urgent, kind="needs", exact=True,
                               **({"now": True} if now else {}))
            return None
        if hermes.last_error.get(bot_id):
            log.warning("%s did not pick its errand back up: %s", bot_id, hermes.last_error.get(bot_id))
        return None
    finally:
        choices.flush(bot_id)










HOLD_SEC = 15 * 60
STOPPED_LINE = "Stopped."
_held: dict[str, float] = {}
_stop_pressed: dict[str, float] = {}



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


def _stopped_since(bot_id: str, since: float) -> bool:
    return _stop_pressed.get(bot_id, 0) >= since


async def stop(bot_id: str) -> dict:
    ""

    _held[bot_id] = time.time() + HOLD_SEC
    try:
        handoff.stopped(bot_id)
    except Exception:
        log.warning("could not end %s's held work on Stop", bot_id, exc_info=True)
    _stop_pressed[bot_id] = time.time()
    returned = []
    for t in list(_queued.get(bot_id) or []):
        t.stopped = True
        returned.append(t.own_words)
        await t.push({"type": "stopped", "returned": True, "text": "Not sent. It is back in the message box."})
        await t.push({"type": "done"})
    _queued.pop(bot_id, None)
    for t in [t for t in (_steering.get(bot_id) or []) if not t.dispatched]:
        t.stopped = True
        returned.append(t.own_words)
        await t.push({"type": "stopped", "returned": True, "text": "Not sent. It is back in the message box."})
        await t.push({"type": "done"})
    for t in _steering.get(bot_id) or []:
        t.stop_pressed = True
    _steering[bot_id] = [t for t in (_steering.get(bot_id) or []) if t.dispatched]
    turn = _turns.get(bot_id)




    answered = bool(turn and not turn.done and turn.replied)
    running = (bool(turn and not turn.done) or _lock(bot_id).locked()) and not answered
    if turn and not turn.done and not answered:
        turn.stopped = True
        if turn.source == "app" and (turn.own_words or "").strip():
            _stopped[bot_id] = " / ".join([turn.own_words, *turn.steered])
    run = (turn.run_id if turn and turn.run_id else None) or activity.run_id(bot_id)
    asked = await hermes.stop(bot_id, run) if run else False
    if running:
        log.info("%s stopped by the person (run %s, %s)", bot_id, run or "not named yet",
                 "interrupted" if asked else "will stop when it is named" if turn else "hub work held")
    return {"stopped": running, "returned": returned, "held_until": _held[bot_id]}






STOP_SETTLE_SEC = 10
_saying_stopped: set[asyncio.Task] = set()


async def _say_stopped(bot_id: str, turn: Turn | None) -> None:
    ""
    loop = asyncio.get_running_loop()
    end = loop.time() + HOLD_SEC

    while (not turn.done or _turns.get(bot_id) is turn) if turn is not None else _lock(bot_id).locked():
        if loop.time() > end:
            log.warning("%s did not stop within %s s", bot_id, HOLD_SEC)
            return
        await asyncio.sleep(0.05)
    say_stopped(bot_id, (turn.draft_shown if turn is not None and turn.stopped else "").strip())


def say_stopped(bot_id: str, words: str = "") -> None:
    ""

    from . import events
    token = store.feed_turn.set((None, None))
    try:
        at = math.ceil(time.time())
        if words:
            store.add_feed("message_local", bot_id, None, _name(bot_id), words, at=at,
                           written_by="zip", written_for="person")
        events.stopped(bot_id, f"{_name(bot_id)} stopped.", at=at)
    except Exception:
        log.warning("could not record %s's Stop in the chat", bot_id, exc_info=True)
    finally:
        store.feed_turn.reset(token)


async def stop_pressed(bot_id: str) -> dict:
    ""

    turn = _turns.get(bot_id)
    out = await stop(bot_id)
    if not out["stopped"]:
        return {**out, "settled": True}
    told = asyncio.create_task(_say_stopped(bot_id, turn))
    _saying_stopped.add(told)
    told.add_done_callback(_saying_stopped.discard)
    done, _ = await asyncio.wait({told}, timeout=STOP_SETTLE_SEC)
    return {**out, "settled": bool(done)}


def queued_counts() -> dict[str, int]:
    ""

    return {b: len(q) for b, q in list(_queued.items()) if q}
