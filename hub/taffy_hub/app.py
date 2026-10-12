"""Taffy hub: the HTTP API the Taffy iPhone app talks to."""

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from . import accounts, activity, approvals, apns, board, calllog, choices, erase, looks, config, consent, signin, health, hermes, history, logins, mac, notices, notifyprefs, onboarding, ondevice, profile, screen, store, team, turns, validate, voice, watcher, who
from . import facts, looking, shortreply, tones, usage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("taffy.hub")
logging.getLogger("httpx").setLevel(logging.WARNING)   # every poll was logged, session ids included


# The hub's background loops: the board watcher, the SOUL.md and USER.md sync with the chat refresh, the health
# and account checks and the call logs. Tests turn them off (tests/test_onboarding.py `_load_app`): a test that
# entered the app's lifespan ran them against the live home with the test's settings, rewriting every Zip's SOUL.md
# until the hub put it back (Oct 9, about 65 rewrites; docs/qa/2026-10-09-loose-ends.md).
BACKGROUND = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    signin.loop = asyncio.get_running_loop()
    store.db()
    if BACKGROUND:   # the running hub reads the Zips' chats and spends Haiku; a test never does (it turns them on)
        from . import extract
        extract.ENABLED = voice.CONTEXT_ON = True
        from . import teamvoice
        teamvoice.ENABLED = True   # Zip-to-Zip messages get a plain version for the chat
    tasks = [asyncio.create_task(watcher.run()), asyncio.create_task(team.keep_souls_current()),
             asyncio.create_task(health.run()), asyncio.create_task(accounts.run()),
             asyncio.create_task(calllog.run()), asyncio.create_task(usage.run()),
             asyncio.create_task(_watch_orders())] if BACKGROUND else []
    yield
    for t in tasks:
        t.cancel()


async def _watch_orders():
    """Order confirmation emails become purchase facts (extract.py)."""
    from . import extract
    await extract.watch_orders()


# No /docs, /redoc or /openapi.json: the hub's API listing stays off the Wi-Fi and the tailnet, like the front door's.
app = FastAPI(title="Taffy hub", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    """Never echo the request body back (it can hold a password): field names and messages only."""
    fields = [".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": f"Check these fields: {', '.join(fields) or 'request'}."})


def require_device(authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Pair this device first.")
    name = store.device_for_token(authorization[7:])
    if not name:
        raise HTTPException(401, "This device is not paired.")
    return name


class PairBody(BaseModel):
    code: str
    device_name: str = "iPhone"


_pair_failures: list[float] = []
_pair_lock = __import__("threading").Lock()


@app.post("/api/pair")
def pair(body: PairBody):
    with _pair_lock:   # requests run in parallel threads; the limit must hold for a burst too
        now = time.time()
        _pair_failures[:] = [t for t in _pair_failures if now - t < 600]
        if len(_pair_failures) >= 10:
            raise HTTPException(429, "Too many wrong codes. Wait ten minutes and try again.")
        token = store.redeem_pairing_code(body.code[:32], body.device_name[:80])
        if not token:
            _pair_failures.append(now)
            raise HTTPException(400, "That code is wrong or expired. Ask for a new one.")
    return {"token": token}


_new_codes: list[float] = []


@app.post("/api/pair/new-code")
def new_pairing_code(_: str = Depends(require_device)):
    """From a paired device: a one-time code for adding another (a new phone, a restore), so pairing never
    needs the server's command line. A few per hour at most."""
    with _pair_lock:
        now = time.time()
        _new_codes[:] = [t for t in _new_codes if now - t < 3600]
        if len(_new_codes) >= 5:
            raise HTTPException(429, "That's enough codes for now. Try again in an hour.")
        _new_codes.append(now)
    return {"code": store.new_pairing_code(15 * 60), "expires_in": 15 * 60}


@app.post("/api/unpair")
def unpair(authorization: str | None = Header(None), _: str = Depends(require_device)):
    store.revoke_device(authorization[7:])
    return {"ok": True}


class PlanCodeBody(BaseModel):
    code: str


@app.post("/api/plan/claude/start")
def plan_claude_start(_: str = Depends(require_device)):
    """Starts the home's own Claude Code sign-in (plansignin.py) and returns the page the app opens."""
    from . import plansignin
    try:
        return {"url": plansignin.start()}
    except plansignin.SignInError as e:
        raise HTTPException(409, str(e))


@app.post("/api/plan/claude/code")
def plan_claude_code(body: PlanCodeBody, _: str = Depends(require_device)):
    """The code from Claude's sign-in page. Never logged."""
    from . import plansignin
    try:
        return plansignin.finish(body.code)
    except plansignin.SignInError as e:
        raise HTTPException(409, str(e))


@app.post("/api/plan/claude/cancel")
def plan_claude_cancel(_: str = Depends(require_device)):
    from . import plansignin
    plansignin.cancel()
    return {"ok": True}


@app.get("/api/health")
def health_check():
    """The hub is up; `bots` and `voice` are the last background checks (see health.py)."""
    return {"ok": True, "time": int(time.time()), "bots": health.status["bots"], "voice": health.status["voice"]}


def _bot(bot_id: str) -> config.Bot:
    bot = config.BOTS_BY_ID.get(bot_id)
    if not bot:
        raise HTTPException(404, "That bot isn't on your team anymore.")
    return bot


# MARK: Bots and conversations

_preview_cache: dict[str, tuple[float, str | None, float | None]] = {}
PREVIEW_TTL = 20


async def _last_message(bot_id: str) -> tuple[str | None, float | None]:
    """Latest visible line of a bot's conversation, for the home list preview (cached briefly)."""
    cached = _preview_cache.get(bot_id)
    if cached and time.time() - cached[0] < PREVIEW_TTL and not turns.is_busy(bot_id):
        return cached[1], cached[2]
    if not store.session_for(bot_id):
        return None, None
    try:
        msgs = await hermes.messages(bot_id, limit=12)
    except Exception:
        return (cached[1], cached[2]) if cached else (None, None)
    result: tuple[str | None, float | None] = (None, None)
    # The same rule as the chat (history.py): hidden turns and their replies never become the preview.
    shown = [m for m in shortreply.apply(bot_id, history.visible(msgs)) if m["class"] in ("colin", "bot", "board_reply")]
    if shown:
        m = shown[-1]
        prefix = "You: " if m["class"] == "colin" else ""
        result = (prefix + " ".join(m["display"].split())[:140], m.get("ts"))
    _preview_cache[bot_id] = (time.time(), *result)
    return result


@app.get("/api/bots")
async def bots(_: str = Depends(require_device)):
    config.reload_bots()
    load = await asyncio.to_thread(board.bot_load)
    previews = await asyncio.gather(*[_last_message(b.id) for b in config.BOTS])
    asks: dict[str, int] = {}
    pending = await asyncio.to_thread(consent.pending)
    for r in pending:
        asks[r["bot_id"]] = asks.get(r["bot_id"], 0) + 1
    statuses = await asyncio.to_thread(_bot_statuses, pending)
    out = []
    for b, (preview, ts) in zip(config.BOTS, previews):
        l = load.get(b.id, {"working": 0, "needs_you": 0})
        f = await asyncio.to_thread(store.latest_feed_for, b.id)
        if f and f["kind"] in ("choices", "login", "account", "mac_ask") and '"state": "open"' not in (f["body"] or ""):
            f = None   # an answered or dismissed card is not news
        if f and (ts is None or f["created_at"] > ts):
            if f["kind"] == "call":
                try:
                    secs = int(json.loads(f["body"] or "{}").get("duration") or 0)
                except ValueError:
                    secs = 0
                first = f"Voice call, {secs // 60}:{secs % 60:02d}"
            elif f["kind"] in ("mac", "mac_ask"):
                first = f["title"] or "Used your Mac"
            elif f["kind"] == "choices":
                first = f["title"] or "Asked you to pick"
            elif f["kind"] == "login":
                # Oct 8: the preview showed the card's raw JSON.
                try:
                    site = json.loads(f["body"] or "{}").get("site") or ""
                except ValueError:
                    site = ""
                first = f"Asked you to sign in to {site}" if site else "Asked you to sign in"
            elif f["kind"] == "account":
                first = f"Asked you to {(f['title'] or 'connect an account').lower()}"
            elif f["kind"] == "team":
                try:
                    tm = json.loads(f["body"] or "{}")
                except ValueError:
                    tm = {}
                first = f"{f['title']}: {tm.get('text') or ''}" if tm.get("kind") != "answer" else (tm.get("text") or "")
            else:
                first = ("Sent a picture: " + (f["title"] or "")) if f["kind"] == "image" else \
                    ((f["body"] or "").split("\n\n")[-1].strip() or (f["title"] or ""))
            # Bots talking to each other, or a bot's Mac line, can show as the preview but never move the bot
            # up Colin's list: nothing happened for him.
            preview = " ".join(first.split())[:140]
            if f["kind"] not in ("team", "mac", "mac_ask"):
                ts = f["created_at"]
        look = looks.for_bot(b.id)
        out.append({"id": b.id, "name": b.name, "role": b.role, "color": (look or {}).get("color") or b.color,
                    "symbol": b.symbol, "shape": (look or {}).get("shape") or b.shape, "look": look,
                    "custom": config.is_custom(b.id),
                    "snoozed_until": ((store.get_setting("snoozed", {}) or {}).get(b.id) or None),
                    "notify": store.notify_on(b.id), "notify_kinds": notifyprefs.kinds_for(b.id),
                    "tone": tones.tone_for(b.id),
                    "status": statuses.get(b.id),
                    "working": l["working"], "needs_you": l["needs_you"] + asks.get(b.id, 0),
                    "busy": turns.is_busy(b.id), "preview": preview, "last_at": ts})
    return {"bots": out}


@app.get("/api/bots/{bot_id}/messages")
async def bot_messages(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    try:
        msgs = await hermes.messages(bot_id)
    except Exception:
        raise HTTPException(503, "Could not load this conversation right now.")
    # A reply shown short (shortreply.py) has `display` short and `full` as written; the app offers Show Full Reply.
    # A reply the backup model gave has `backup` true (hermes.mark_backup); the app marks it quietly.
    return {"messages": hermes.mark_backup(bot_id, shortreply.apply(bot_id, history.classify(msgs))),
            "busy": turns.is_busy(bot_id),
            "reactions": store.reactions_for(bot_id)}


class PushTokenBody(BaseModel):
    token: str
    environment: str = "development"
    platform: str = "iphone"


@app.post("/api/push-token")
def push_token(body: PushTokenBody, authorization: str | None = Header(None), _: str = Depends(require_device)):
    """The app's Apple push token, sent after pairing and whenever it changes."""
    token = body.token.strip().lower()
    if not (16 <= len(token) <= 200 and all(c in "0123456789abcdef" for c in token)):
        raise HTTPException(400, "That isn't a push token.")
    if body.environment not in apns.HOSTS or body.platform not in ("iphone", "mac"):
        raise HTTPException(400, "Unknown device type.")
    apns.save_token(store._hash(authorization[7:]), token, body.environment, body.platform)
    return {"ok": True}


class ReactionBody(BaseModel):
    kind: str | None = None
    excerpt: str = ""


@app.post("/api/bots/{bot_id}/messages/{message_id}/reaction")
def react(bot_id: str, message_id: str, body: ReactionBody, _: str = Depends(require_device)):
    """Colin's tapback on a message; kind null removes it."""
    _bot(bot_id)
    if body.kind is not None and body.kind not in store.REACTIONS:
        raise HTTPException(400, "That reaction isn't available.")
    if len(message_id) > 64 or message_id.startswith(("local-", "pending-", "feed-")):
        raise HTTPException(400, "That message can't take a reaction yet.")
    store.set_reaction(bot_id, message_id, "colin", body.kind, body.excerpt)
    return {"ok": True}


class ReplyTo(BaseModel):
    text: str
    who: str = "bot"   # "bot": the Zip's message; "person": their own; "other": a team update, a call or a note


class SendBody(BaseModel):
    text: str
    reply_to: ReplyTo | None = None   # the message the person swiped or chose Reply on (history.reply_line)


def _sse(turn: turns.Turn):
    async def gen():
        yield f"data: {json.dumps({'type': 'turn', 'id': turn.id})}\n\n"   # lets the app reattach to this one
        async for ev in turn.follow():
            if ev is None:
                yield ": keepalive\n\n"
            else:
                yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/bots/{bot_id}/messages")
async def send_message(bot_id: str, body: SendBody, authorization: str | None = Header(None),
                       _: str = Depends(require_device)):
    """Starts a turn and streams its events as SSE. The turn keeps running if the phone disconnects."""
    _bot(bot_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Type a message first.")
    if len(text) > 20000:
        raise HTTPException(400, "That message is too long.")
    reply_to = None
    if body.reply_to and body.reply_to.text.strip():
        if body.reply_to.who not in ("bot", "person", "other"):
            raise HTTPException(400, "That reply can't be sent.")
        reply_to = {"text": body.reply_to.text[:20000], "who": body.reply_to.who}
    device = looking.key(authorization)
    looking.saw(device)   # it is on screen now; a reply after it leaves goes out as a notification (looking.py)
    return _sse(turns.send(bot_id, text, reply_to=reply_to, device=device))   # waits its turn if the bot is busy


@app.get("/api/bots/{bot_id}/turn")
async def follow_turn(bot_id: str, id: str | None = None, _: str = Depends(require_device)):
    """Re-attaches to a turn that is still running (or just finished), for example after the app reconnects.
    With `id`, exactly that one, so two messages in flight never swap replies."""
    _bot(bot_id)
    turn = turns.by_id(id) if id else turns.latest(bot_id)
    if turn is not None and turn.bot_id != bot_id:
        turn = None
    if not turn:
        raise HTTPException(404, "Nothing is running.")
    return _sse(turn)


@app.get("/api/bots/{bot_id}/activity")
def bot_activity(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    items = store.activity_for(bot_id)
    said = store.said_at(bot_id)
    for it in items:
        if it.get("kind") not in signin.ASK_KINDS:
            continue
        try:
            card = json.loads(it.get("body") or "{}")
        except ValueError:
            continue
        if card.get("state") != "open":
            continue
        # `waiting`: the Zip is paused on this card now (signin.waiting). An older card stays usable in the chat but
        # never marks the Zip as paused. The reason speaks to the person, even on cards from before that rule.
        card["waiting"] = signin.waiting(it, said)
        if card.get("why"):
            card["why"] = who.to_you(card["why"])
        card.pop("session_id", None)
        it["body"] = json.dumps(card)
    return {"items": items}


# MARK: Board

@app.get("/api/board")
def get_board(_: str = Depends(require_device)):
    return {**board.grouped(), "requests": _signin_requests()}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, _: str = Depends(require_device)):
    t = board.get_task(validate.task_id(task_id))
    if not t:
        raise HTTPException(404, "That task isn't there anymore.")
    return t


def _file_response(f, filename: str | None, media_type: str | None = None, headers: dict | None = None):
    """Streams a file the hub already opened and checked (safe_files.open_file), so what is sent is exactly the
    checked file. Closes it when done."""
    import mimetypes
    import os
    from urllib.parse import quote
    size = os.fstat(f.fileno()).st_size
    out = {"Content-Length": str(size), **(headers or {})}
    if filename is not None:
        quoted = quote(filename)
        out["Content-Disposition"] = (f"attachment; filename*=utf-8''{quoted}" if quoted != filename
                                      else f'attachment; filename="{filename}"')
    media_type = media_type or (mimetypes.guess_type(filename or "")[0] or "application/octet-stream")

    def chunks():
        with f:
            while chunk := f.read(64 * 1024):
                yield chunk
    return StreamingResponse(chunks(), media_type=media_type, headers=out)


@app.get("/api/tasks/{task_id}/attachments/{attachment_id}")
def get_attachment(task_id: str, attachment_id: int, _: str = Depends(require_device)):
    found = board.open_attachment(validate.task_id(task_id), attachment_id)
    if not found:
        raise HTTPException(404, "That file isn't there anymore.")
    return _file_response(found[1], found[0])


class NoteBody(BaseModel):
    note: str | None = None


def _act(result: tuple[bool, str]):
    ok, out = result
    if not ok:
        log.info("board action refused: %s", out[-300:])
        moved = "not found" in out or "terminal" in out or "cannot" in out
        raise HTTPException(409, "That request already moved on. Pull down to refresh."
                            if moved else "The board did not accept that. Try again in a moment.")
    return {"ok": True}


def _task_pass(task_id: str, note: str | None) -> None:
    """Colin's answer on a task is a go-ahead for the sign-ins his words call for. Only his own words
    count; the task title was written by a bot."""
    if note and note.strip():
        consent.open_pass("button", note.strip(), task_id=task_id)


def _approve_pass(task_id: str, note: str | None) -> None:
    """Approve is Colin's go-ahead for the work he approved, note or not (Oct 8 QA: an empty Approve opened no
    pass, so the approved work's sign-in raised a "working on its own" card right after he pressed it). The
    text is the task as he saw it (title and summary); his own note leads when he wrote one. The gate lets a
    send, submission or publish through on this pass only when its target is named in this text."""
    t = board.get_task(task_id) or {}
    parts = ((note or "").strip(), f"Approved: {t.get('title') or 'this task'}.", (t.get("summary") or "").strip())
    consent.open_pass("approve", " ".join(x for x in parts if x), task_id=task_id)


@app.post("/api/tasks/{task_id}/approve")
def approve(task_id: str, body: NoteBody, _: str = Depends(require_device)):
    tid = validate.task_id(task_id)
    note = validate.short(body.note, 4000, "note")
    try:
        _approve_pass(tid, note)
    except Exception:
        log.warning("could not record the approval for sign-ins", exc_info=True)
    return _act(board.approve(tid, note))


@app.post("/api/tasks/{task_id}/changes")
def changes(task_id: str, body: NoteBody, _: str = Depends(require_device)):
    if not body.note:
        raise HTTPException(400, "Say what to change.")
    tid = validate.task_id(task_id)
    note = validate.short(body.note, 4000, "note")
    _task_pass(tid, note)
    return _act(board.request_changes(tid, note))


@app.post("/api/tasks/{task_id}/answer")
def answer(task_id: str, body: NoteBody, _: str = Depends(require_device)):
    if not body.note:
        raise HTTPException(400, "Type an answer.")
    tid = validate.task_id(task_id)
    note = validate.short(body.note, 4000, "answer")
    _task_pass(tid, note)
    return _act(board.answer_block(tid, note))


@app.post("/api/tasks/{task_id}/done")
def done(task_id: str, _: str = Depends(require_device)):
    """Colin is handling it himself. The note box is ignored on purpose."""
    return _act(board.mark_done(validate.task_id(task_id), None))


# MARK: Feed, check-ins and settings

@app.get("/api/checkins")
def checkins(_: str = Depends(require_device)):
    return {"items": store.checkins()}


class SettingsBody(BaseModel):
    muted_bots: list[str] | None = None
    save_call_transcripts: bool | None = None     # calllog.py; on unless Colin turns it off
    keep_replies_short: bool | None = None        # shortreply.py; on unless the person turns it off


def _settings() -> dict:
    return {"muted_bots": store.get_setting("muted_bots", []), "save_call_transcripts": calllog.enabled(),
            "keep_replies_short": shortreply.enabled()}


@app.get("/api/settings")
def get_settings(_: str = Depends(require_device)):
    return _settings()


@app.put("/api/settings")
def put_settings(body: SettingsBody, _: str = Depends(require_device)):
    """Each field is changed only when it is sent."""
    if body.muted_bots is not None:
        store.set_setting("muted_bots", [b for b in body.muted_bots if b in config.BOTS_BY_ID])
    if body.save_call_transcripts is not None:
        calllog.set_enabled(body.save_call_transcripts)
    if body.keep_replies_short is not None:
        shortreply.set_enabled(body.keep_replies_short)
    return _settings()


@app.get("/api/feed")
def feed(after: int = 0, changed_after: float = 0, authorization: str | None = Header(None),
         _: str = Depends(require_device)):
    """New feed items, plus the bots whose existing items changed since `changed_after` (the hub's own
    `time` from the previous poll), such as the live "is using your Mac" line."""
    looking.saw(looking.key(authorization))   # the app is on screen (looking.py)
    items, more = store.feed_since(after)
    now = time.time()
    changed = store.feed_changed_bots(changed_after) if changed_after else []
    busy = turns.busy_bots()
    return {"items": items, "has_more": more, "max_id": store.max_feed_id(), "busy": busy,
            "changed": changed, "time": now, "computers": _live_computers(set(busy)), "pinging": _pinging(),
            "activity": activity.all_live(list(config.BOTS_BY_ID), now), "queued": turns.queued_counts()}


def _live_computers(busy: set[str]) -> dict[str, str]:
    """The chats that show a Computer card right now (screen.live_states). The card is optional, so a problem
    reading the screens never stops the feed."""
    try:
        return screen.live_states(busy)
    except Exception:
        log.warning("could not read the computers", exc_info=True)
        return {}


def _pinging() -> list[dict]:
    try:
        return team.pinging()
    except Exception:
        log.warning("could not read open team questions", exc_info=True)
        return []


# MARK: Bot screens

app.mount("/screen", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="screen")


@app.get("/api/bots/{bot_id}/screen")
def screen_status(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    return screen.status(bot_id)


@app.get("/api/bots/{bot_id}/screen/snapshot")
def screen_snapshot(bot_id: str, _: str = Depends(require_device)):
    """A small still of that Zip's screen for the Computer card in its chat: the same screen the viewer shows."""
    _bot(bot_id)
    try:
        jpeg, why = screen.snapshot(bot_id)
    except Exception:
        log.warning("screen snapshot failed", exc_info=True)
        raise HTTPException(503, "The picture is not available right now.")
    if jpeg is None:
        if why == "private":
            raise HTTPException(423, "That computer is being used somewhere else.")
        raise HTTPException(404, "That computer is asleep.")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/bots/{bot_id}/screen/start")
def screen_start(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    ok, out = screen.start(bot_id)
    if not ok:
        log.info("screen start failed: %s", out[-300:])
        raise HTTPException(409, "The computer would not wake up. Try again in a moment.")
    return screen.status(bot_id)


@app.post("/api/bots/{bot_id}/screen/control")
def screen_control(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    return screen.take_control(bot_id)


@app.post("/api/bots/{bot_id}/screen/release")
def screen_release(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    return screen.hand_back(bot_id)


@app.post("/api/bots/{bot_id}/screen/ticket")
def screen_ticket(bot_id: str, control: bool = False, _: str = Depends(require_device)):
    """A single-use, 30-second ticket for the viewer page (noVNC cannot send an auth header)."""
    _bot(bot_id)
    t = screen.mint_ticket(bot_id, control)
    return {"ticket": t, "url": f"/screen/screen.html?ticket={t}&control={'1' if control else '0'}"}


@app.websocket("/api/screen/ws")
async def screen_ws(ws: WebSocket, ticket: str = ""):
    found = screen.redeem_ticket(ticket)
    await ws.accept(subprotocol="binary" if "binary" in (ws.headers.get("sec-websocket-protocol") or "") else None)
    if not found:
        await ws.close(code=4401, reason="bad or expired ticket")
        return
    bot_id, control = found
    await screen.bridge(ws, bot_id, control)


# MARK: Logins (the bots' vaults) and sign-in

class LoginBody(BaseModel):
    bots: list[str]
    label: str
    url: str
    identifier: str
    identifier_type: str = "email"
    password: str
    otp_secret: str | None = None
    task_id: str | None = None   # the sign-in request this answers, if any
    request_id: int | None = None   # a chat's sign-in card (signin.py)


@app.get("/api/logins")
def list_logins(_: str = Depends(require_device)):
    return {"logins": logins.list_all()}


@app.post("/api/logins")
def save_login(body: LoginBody, _: str = Depends(require_device)):
    bots = [b for b in body.bots if b in config.BOTS_BY_ID]
    if not bots:
        raise HTTPException(400, "Pick at least one bot.")
    url = validate.secure_url(body.url)
    task_id = validate.task_id(body.task_id) if body.task_id else None
    label = validate.short(body.label, 120, "label") or url
    identifier = validate.short(body.identifier, 320, "username")
    if not identifier or not body.password or len(body.password) > 1024:
        raise HTTPException(400, "Enter the username and password.")
    try:
        saved = logins.save(bots, label, url, identifier, body.identifier_type, body.password,
                            (body.otp_secret or "").strip() or None)
    except Exception as e:
        log.info("login save failed: %s", type(e).__name__)
        raise HTTPException(400, "That login could not be saved. Check the website and the 2FA key.")
    warning = None
    resumed: list[str] = []
    if body.request_id:
        # The card belongs to one Zip; Colin may have ticked others too, in any order (the app sends a set).
        owner = signin.card_owner(body.request_id)
        try:
            signin.resolve(owner if owner in bots else bots[0], body.request_id, "saved")
        except signin.AskError:
            warning = "The login is saved, but that request had already moved on."
    if not body.task_id and not body.request_id:
        # Saved from Settings: any bot already waiting to sign in to this site picks back up.
        site = consent.site_of(url)
        for tid in board.waiting_signins(site, bots):
            t = board.get_task(tid) or {}
            consent.allow_now(t.get("assignee") or "default", url, "You saved a login it was waiting for", tid)
            ok, _ = board.answer_block(tid, f"{who.first()} saved a login for this site in your vault. Sign in "
                                            "with your browser vault tools (they fill the password without showing it) and continue.")
            if ok:
                resumed.append(tid)
    if body.task_id:
        # Save Login on a sign-in card is the go-ahead to use it for that request.
        for b in bots:
            consent.allow_now(b, url, "You saved a login for this request", task_id)
        ok, _ = board.answer_block(task_id,
                                   f"{who.first()} saved a login for this site in your vault. Sign in with your "
                                   "browser vault tools (they fill the password without showing it) and continue.")
        if not ok:
            warning = "The login is saved, but the request had already moved on."
    return {"saved": saved, "warning": warning, "resumed": resumed}


@app.delete("/api/logins/{bot_id}/{item_id}")
def delete_login(bot_id: str, item_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    return {"removed": logins.remove(bot_id, item_id[:64])}


class SignInBody(BaseModel):
    url: str


@app.post("/api/bots/{bot_id}/signin")
def start_signin(bot_id: str, body: SignInBody, _: str = Depends(require_device)):
    """One tap from the app: wake the bot's computer, open the site and hand Colin control."""
    _bot(bot_id)
    url = validate.secure_url(body.url)   # Colin types a password on this page, so https only
    if not screen.status(bot_id)["running"]:
        ok, out = screen.start(bot_id)
        if not ok:
            log.info("screen start failed: %s", out[-300:])
            raise HTTPException(409, "The computer would not wake up. Try again in a moment.")
    try:
        logins.open_in_bot_browser(bot_id, url)
    except Exception:
        log.exception("could not open sign-in page")
        raise HTTPException(500, "Could not open that site on the bot's computer.")
    screen.take_control(bot_id)
    t = screen.mint_ticket(bot_id, True)
    return {"url": f"/screen/screen.html?ticket={t}&control=1"}


class SignedInBody(BaseModel):
    task_id: str | None = None
    request_id: int | None = None   # a chat's sign-in card (signin.py)
    site: str | None = None
    declined: bool = False


@app.post("/api/bots/{bot_id}/signed-in")
def finish_signin(bot_id: str, body: SignedInBody, _: str = Depends(require_device)):
    _bot(bot_id)
    screen.hand_back(bot_id)
    if not body.declined and body.site:
        # The bot's browser now holds a session Colin signed in himself, so later visits are gated like a
        # saved login: only on his request, his Allow, or the site's Anytime setting.
        site = consent.site_of(validate.short(body.site, 200, "site") or "")
        if site:
            consent.mark_signed_in(bot_id, site)
    if body.request_id:
        try:
            signin.resolve(bot_id, body.request_id, "declined" if body.declined else "signed_in")
        except signin.AskError:
            pass
    if body.task_id:
        tid = validate.task_id(body.task_id)
        site = validate.short(body.site, 200, "site") or "the site"
        if body.declined:
            board.comment(tid, f"{who.first()} opened the sign-in for {site} and backed out without signing in.")
        else:
            board.answer_block(tid, f"{who.first()} signed in to {site} in your browser. "
                                    "The session is saved in your browser profile; continue with it.")
    return screen.status(bot_id)


class AppErrorBody(BaseModel):
    screen: str                 # "take_over", "save_login", "computer_card", "sign_in_screen"
    message: str                # the sentence the app showed
    detail: str = ""            # what failed underneath: an HTTP status or a network error code
    bot_id: str | None = None


_app_errors: list[float] = []


@app.post("/api/app-errors")
def app_error(body: AppErrorBody, device: str = Depends(require_device)):
    """An error a sign-in or computer screen showed Colin, written to the hub's log. On Oct 9 his "it errored"
    on the Lens Shop card left no trace anywhere, because the failure never reached the hub."""
    now = time.time()
    _app_errors[:] = [t for t in _app_errors if now - t < 60]
    if len(_app_errors) >= 30:
        return {"ok": False}
    _app_errors.append(now)

    def clean(s, n):   # one line, no addresses with tickets or other query strings
        return re.sub(r"\?\S*", "", " ".join(str(s or "").split()))[:n]
    log.warning("app error on %s for %s (%s): %s [%s]", clean(body.screen, 40), clean(body.bot_id, 40) or "-",
                clean(device, 80), clean(body.message, 300), clean(body.detail, 300) or "no detail")
    return {"ok": True}


# MARK: Sign-in permissions (see the plugin's consent.py)

def stray_session(bot_id: str, session_id: str | None) -> bool:
    """A conversation the hub does not run: not the Zip's chat with Colin and not one of its lanes (answering
    teammates, preparing drafts, quiet looks). Hermes's command line and one-off test runs make such sessions.
    Unknown only once the hub has a chat session for the Zip; before that nothing can be told apart."""
    if not session_id or not store.session_for(bot_id):
        return False
    rows = store.db().execute("SELECT session_id FROM bot_sessions WHERE bot_id = ? OR bot_id LIKE ?",
                              (bot_id, f"{bot_id}#%")).fetchall()
    return session_id not in {row["session_id"] for row in rows}


def card_origin(r: dict, why: str) -> str | None:
    """Where a card that did not come from Colin's chat came from, in plain words. "Working on its own" read as if
    the Zip had started something by itself when it was a test run from Hermes's command line (Oct 8)."""
    if why not in ("background", "scheduled"):
        return None
    name = config.BOTS_BY_ID[r["bot_id"]].name if r.get("bot_id") in config.BOTS_BY_ID else "the Zip"
    job = " ".join(str(r.get("routine_name") or "").split())
    if job:
        return f"This came up during the scheduled job {job}."
    if r.get("task_id"):
        try:
            title = " ".join(str((board.get_task(r["task_id"]) or {}).get("title") or "").split())[:120].rstrip(".")
        except Exception:
            title = ""
        return (f"This came up while {name} was working on a board task: {title}." if title
                else f"This came up while {name} was working on a board task.")
    try:
        teammate = team.in_team_session(r.get("session_id"))
    except Exception:
        teammate = False
    if teammate:
        return f"This came up while {name} was answering a teammate's question."
    if why == "scheduled":
        return None   # the app's own "during scheduled work"
    if stray_session(r.get("bot_id") or "", r.get("session_id")):
        return f"This came up in a run outside the app, not in your chat with {name}."
    return f"This came up outside your chat with {name}."


def _signin_requests() -> list[dict]:
    out = []
    for r in consent.pending():
        if r["bot_id"] in config.BOTS_BY_ID:
            kind = r.get("kind") or "signin"
            why = r.get("why") or ("background" if not r["reason"] else "")
            item = {"id": r["id"], "bot_id": r["bot_id"], "site": r["site"], "reason": r["reason"],
                    "created_at": r["created"], "why": why, "origin": card_origin(r, why),
                    "kind": kind, "excerpt": r.get("excerpt") or None,
                    "sensitive": consent.is_sensitive(r["site"]) if kind in ("signin", "navigate") else False}
            if kind not in ("signin", "navigate"):
                # An action card: exactly what will be sent, published or deleted, and whether a standing rule
                # may cover it (one recipient, one routine).
                item.update(target=r.get("target") or "", inputs=r.get("inputs") or "",
                            can_always=bool(r.get("scope")), scope=r.get("scope") or "",
                            details=r.get("details") or None, routine_name=r.get("routine_name") or None)
                if r.get("ask_rule"):
                    # Something the Zip would just do, paused by one of Colin's Ask First rules (Custom Rules).
                    rule = consent.get_rule(r["ask_rule"])
                    item["paused_by"] = rule_sentence(rule) if rule else "An Ask First rule you removed."
            out.append(item)
    return out


@app.get("/api/signin-requests")
def signin_requests(_: str = Depends(require_device)):
    return {"requests": _signin_requests()}


class DecisionBody(BaseModel):
    decision: str   # "once", "always" or "deny"


@app.post("/api/signin-requests/{req_id}")
async def decide_signin(req_id: int, body: DecisionBody, _: str = Depends(require_device)):
    if body.decision not in ("once", "always", "deny"):
        raise HTTPException(400, "Choose Allow Once, Always Allow or Not Now.")
    allow = body.decision != "deny"
    r = consent.decide(req_id, allow, always=body.decision == "always")
    if not r:
        gone = consent.get_request(req_id)
        if gone and gone.get("status") == "expired":
            raise HTTPException(409, consent.expired_line(gone))
        raise HTTPException(409, "That request already moved on. Pull down to refresh.")
    name = config.BOTS_BY_ID[r["bot_id"]].name if r["bot_id"] in config.BOTS_BY_ID else "The Zip"
    kind = r.get("kind") or "signin"
    confirmation = None
    if allow and body.decision == "always" and not r.get("test"):
        # The standing yes Colin just gave, in the words Custom Rules uses (Grok Bot's "Allowed from now on").
        if kind in ("signin", "navigate"):
            confirmation = f"{name} can sign in to {r['site']} without asking."
        elif r.get("scope") and kind in consent.RULE_KINDS:
            made = consent.rule_for(r["bot_id"], kind, r["scope"])
            confirmation = rule_sentence(made) if made else None
    if kind == "command":
        # A command the Zip is waiting on right now: the answer goes straight to it (approvals.py, round 3 M6). If
        # Hermes already stopped waiting, it told the Zip to stop and wait for Colin, so his answer picks the
        # conversation back up, as sign-in cards do (round 4, L5).
        waiting = await approvals.decide(r, allow)
        later = ""
        if not waiting and r.get("session_id"):
            later = f" {name} is picking it back up now."
            asyncio.create_task(turns.follow_up(r["bot_id"], f"{who.hub_note()} "
                                                             f"{approvals.late_note(r, allow)}",
                                                r.get("session_id"), urgent=r.get("why") == "asked"))
        store.add_feed("signin_decision", r["bot_id"], r["task_id"],
                       f"{'Allowed' if allow else 'Declined'}: {(r['reason'] or kind)[:80]}",
                       f"You {'allowed' if allow else 'declined'} {name}: {r['reason']}.{later}")
        return {"ok": True}
    action = kind not in ("signin", "navigate")
    if action:
        what = r["reason"] or "that"
        always = body.decision == "always" and r.get("scope")
        if allow:
            note = (f"{who.first()} approved this: {what}."
                    + (f" {who.first()} also allowed this kind of action for {r['scope']} from now on."
                       if always else "")
                    + " Make exactly the same call again now, unchanged; it goes through once. Do not ask "
                    + f"{who.first()} to confirm it again in the chat: the tap was the yes. Then carry on and tell "
                    + f"{who.first()} in a sentence or two what was done.")
        else:
            note = (f"{who.first()} said not now to this: {what}. Do not do it, and do not try another way. "
                    f"Finish what you can and tell {who.first()} in a sentence what is left undone.")
    elif allow:
        note = (f"{who.first()} allowed you to sign in to {r['site']}"
                + (" any time from now on." if body.decision == "always" else " for this request.")
                + " Continue with what you were doing.")
    else:
        note = (f"{who.first()} said not now to signing in to {r['site']}. Do not sign in there. Finish what you "
                f"can without it and tell {who.first()} in a sentence what you could not check.")
    why = r.get("why") or ""
    if r["task_id"]:
        ok, _out = board.answer_block(r["task_id"], note)
        if not ok:
            board.comment(r["task_id"], note)
    elif not action and r.get("session_id") and team.in_team_session(r["session_id"]):
        # A sign-in card from answering a teammate (Oct 9): the Zip already answered with what it had. On Allow it
        # finishes in its team session, where Allow Once applies, and the asker gets the rest; resuming Colin's chat
        # would sign in from the wrong conversation and raise the card again.
        if allow:
            asyncio.create_task(team.finish_after_signin(r["bot_id"], r["site"], r.get("created") or time.time()))
    elif stray_session(r["bot_id"], r.get("session_id")):
        # A card from a conversation the hub does not run (a test run from Hermes's command line, a one-off
        # session): the answer is recorded, and nothing is resumed. Resuming went into Colin's chat, where Career
        # said "nothing will get pushed to main" about a push Colin had never heard of (Oct 8, 7:40 p.m.).
        pass
    elif why != "scheduled" and not r.get("routine"):
        # A chat request: pick the conversation back up either way, so the answer (or what the bot could not
        # check) arrives without Colin asking again. Scheduled work is not resumed in his chat: its card belongs to
        # the job, whose next run uses the answer (Oct 8 QA round 2, M4). Only a card from something he asked for
        # is pushed past quiet hours (L6): an action card says so in `why`; a sign-in card quotes his request.
        asked = why == "asked" or (not action and why in ("mismatch", "sensitive", "ask", "content")
                                   and bool(r.get("reason")))
        asyncio.create_task(turns.follow_up(r["bot_id"], f"{who.hub_note()} {note}",
                                            r.get("session_id"), urgent=asked))
    if action:
        later = (f" {name} will do it once on the next run of {r.get('routine_name') or 'its scheduled job'}."
                 if allow and r.get("routine") and not r["task_id"] and body.decision != "always" else "")
        store.add_feed("signin_decision", r["bot_id"], r["task_id"],
                       f"{'Allowed' if allow else 'Declined'}: {(r['reason'] or kind)[:80]}",
                       f"You {'allowed' if allow else 'declined'} {name}: {r['reason']}.{later}")
    else:
        store.add_feed("signin_decision", r["bot_id"], r["task_id"],
                       f"{'Allowed' if allow else 'Declined'}: {r['site']}",
                       f"You {'allowed' if allow else 'declined'} {name} signing in to {r['site']}.")
    if confirmation:
        store.add_feed("signin_decision", r["bot_id"], r["task_id"], consent.ALLOW_RULE_ADDED,
                       consent.allowed_line(confirmation))
    return {"ok": True, "confirmation": consent.allowed_line(confirmation) if confirmation else None,
            "confirmation_title": consent.ALLOW_RULE_ADDED if confirmation else None}


def _permission_rows() -> list[dict]:
    seen, rows = set(), []
    for l in logins.list_all():
        key = (l["bot"], consent.site_of(l.get("origin") or l.get("label") or ""))
        if key[1] and key not in seen:
            seen.add(key)
            rows.append(key)
    with consent.db() as c:
        # Sites Colin signed a Zip in to by hand (Sign In Myself) are listed too (Oct 8 QA, M5).
        for r in c.execute("SELECT bot_id, site FROM signed_in"):
            key = (r["bot_id"], r["site"])
            if key not in seen and not any(b == key[0] and consent.base(x) == key[1] for b, x in seen):
                seen.add(key)
                rows.append(key)
        for r in c.execute("SELECT bot_id, site FROM modes"):
            key = (r["bot_id"], r["site"])
            if key not in seen:
                seen.add(key)
                rows.append(key)
    return [{"bot_id": b, "site": site, "mode": consent.mode_for(b, site), "sensitive": consent.is_sensitive(site)}
            for b, site in rows if b in config.BOTS_BY_ID]


RULE_STALE_SEC = 60 * 86400   # an Always Allow rule unused this long is shown as "still needed?"


def _routine_name(bot_id: str, job_id: str, source: str) -> str:
    """The name of the scheduled job a rule covers, from the card it came from or the bot's job list (Oct 8 QA
    round 2, L1: "Money may make changes in its scheduled job" did not say which)."""
    m = re.match(r"card (\d+)$", source)
    if m:
        card = consent.get_request(int(m.group(1)))
        if card and card.get("routine_name"):
            return card["routine_name"]
    home = config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id
    job = watcher._jobs_of(home / "cron" / "jobs.json").get(job_id)
    return str(job.get("name") or "") if isinstance(job, dict) else ""


def _rule_scope(r: dict) -> str:
    """An Allow rule's scope in plain words: the recipient, or "its scheduled job <name>"."""
    scope = r["scope"]
    if scope.startswith("routine:"):
        job = _routine_name(r["bot_id"], scope.split(":", 1)[1], r.get("source") or "")
        scope = f"its scheduled job {job}" if job else "its scheduled job"
    return scope


def _rule_who(bot_id: str) -> str:
    if bot_id == consent.EVERY_ZIP:
        return "Every Zip"
    return config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "The Zip"


def rule_sentence(r: dict) -> str:
    """A rule as Colin reads it on Custom Rules: "Money can email andrea@x.com without asking." or "Builder asks
    before pushing code to any repo." """
    who = _rule_who(r["bot_id"])
    if consent.is_ask_rule(r):
        what = r["kind"].removeprefix(consent.ASK_RULE_PREFIX)
        return f"{who} asks before {consent.ask_rule_phrase(what, r['scope'])}."
    scope = _rule_scope(r)
    if r["kind"] == "send":
        verb = "email" if "@" in scope else "send messages to"
        return f"{who} can {verb} {scope} without asking."
    if r["kind"] == "write":
        return f"{who} can make changes in {scope} without asking."
    if r["kind"] == "signin":
        return f"{who} can sign in to {scope} without asking."
    return f"{who} can do this without asking: {scope}."


def _rule_rows() -> list[dict]:
    """The Allow rules, for Permissions in app builds from before Custom Rules (which list both kinds)."""
    return [r for r in _custom_rule_rows() if r["effect"] == "allow"]


def _custom_rule_rows(bot_id: str | None = None) -> list[dict]:
    now = time.time()
    out = []
    for r in consent.rules():
        if r["bot_id"] not in config.BOTS_BY_ID and not (consent.is_ask_rule(r) and r["bot_id"] == consent.EVERY_ZIP):
            continue
        if bot_id and r["bot_id"] not in (bot_id, consent.EVERY_ZIP):
            continue
        ask = consent.is_ask_rule(r)
        out.append({"id": r["id"], "bot_id": r["bot_id"], "kind": r["kind"],
                    "scope": r["scope"] if ask else _rule_scope(r), "site": r.get("site"),
                    "effect": "ask" if ask else "allow", "sentence": rule_sentence(r),
                    "what": r["kind"].removeprefix(consent.ASK_RULE_PREFIX) if ask else None,
                    "where": r["scope"] if ask else None,
                    "created_at": r["created"], "last_used": r.get("last_used"), "uses": r.get("uses") or 0,
                    # An Ask First rule never goes stale: it costs nothing until it pauses something.
                    "stale": not ask and now - (r.get("last_used") or r["created"]) > RULE_STALE_SEC})
    return out


@app.get("/api/permissions")
def permissions(_: str = Depends(require_device)):
    """Sign-in settings per site, the Always Allow rules, the sign-in log and what the Zips did and mentioned."""
    return {"permissions": _permission_rows(),
            "signins": [{**s, "at": s["at"]} for s in consent.recent_signins(40)],
            "rules": _rule_rows(),
            "actions": [a for a in consent.recent_actions(40) if a["bot_id"] in config.BOTS_BY_ID]}


# MARK: Custom Rules (Allow rules from cards, and Colin's own Ask First rules)

RULE_SAVED = "Saved to Custom Rules"


def _rule_choices() -> dict:
    """What the app's Ask First form offers, in the words the rules use."""
    return {"what": [{"id": w, "title": phrase[0][:1].upper() + phrase[0][1:]}
                     for w, phrase in consent.ASK_RULE_WHATS.items()],
            "places": [{"id": consent.ANYWHERE, "title": "Anywhere"}]
                      + [{"id": p, "title": title} for p, (title, _hosts) in consent.ASK_RULE_PLACES.items()]}


@app.get("/api/rules")
def custom_rules(bot_id: str | None = None, _: str = Depends(require_device)):
    """Every rule in a plain sentence, newest first. `bot_id` limits the list to one Zip (and rules for every Zip)."""
    if bot_id:
        _bot(bot_id)
    return {"rules": _custom_rule_rows(bot_id), "choices": _rule_choices()}


class AskRuleBody(BaseModel):
    bot_id: str           # a Zip's id, or "*" for every Zip
    what: str             # push, change or send (consent.ASK_RULE_WHATS)
    where: str = "*"      # anywhere, a named place, a site, an email address or a branch


def _rule_bot(bot_id: str, every: bool) -> str:
    bot_id = (bot_id or "").strip()
    if every and bot_id == consent.EVERY_ZIP:
        return bot_id
    _bot(bot_id)
    return bot_id


def _rule_reply(rule_id: int) -> dict:
    row = next((r for r in _custom_rule_rows() if r["id"] == rule_id), None)
    return {"ok": True, "rule": row, "message": RULE_SAVED}


@app.post("/api/rules")
def add_ask_rule(body: AskRuleBody, _: str = Depends(require_device)):
    """A new Ask First rule. It can only add a card before something a Zip would otherwise just do."""
    bot_id = _rule_bot(body.bot_id, every=True)
    try:
        rid = consent.set_ask_rule(bot_id, body.what, validate.short(body.where, 200, "place") or consent.ANYWHERE)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _rule_reply(rid)


class RuleEditBody(BaseModel):
    bot_id: str | None = None
    what: str | None = None
    where: str | None = None


@app.put("/api/rules/{rule_id}")
def edit_rule(rule_id: int, body: RuleEditBody, _: str = Depends(require_device)):
    """Edit Rule: another Zip, or for an Ask First rule what it covers and where. An Allow rule is never widened
    (consent.update_rule)."""
    r = consent.get_rule(rule_id)
    if r is None:
        raise HTTPException(404, "That rule is already gone.")
    bot_id = _rule_bot(body.bot_id, every=consent.is_ask_rule(r)) if body.bot_id else None
    try:
        new = consent.update_rule(rule_id, bot_id=bot_id, what=body.what or None,
                                  where=validate.short(body.where, 200, "place") if body.where is not None else None)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _rule_reply(new["id"])


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, _: str = Depends(require_device)):
    if not consent.remove_rule(rule_id):
        raise HTTPException(404, "That rule is already gone.")
    return {"ok": True}


class PermissionBody(BaseModel):
    bot_id: str
    site: str
    mode: str


@app.put("/api/permissions")
def set_permission(body: PermissionBody, _: str = Depends(require_device)):
    _bot(body.bot_id)
    if body.mode not in consent.MODES:
        raise HTTPException(400, "Pick When I Ask, Ask Me First or Anytime.")
    site = consent.site_of(validate.short(body.site, 200, "site") or "")
    if not site:
        raise HTTPException(400, "That site does not look right.")
    consent.set_mode(body.bot_id, site, body.mode)
    return {"ok": True}


# MARK: Zip templates (the New Zip gallery)

TEMPLATES = config.SHARED_DIR / "zip-templates.json"
# Which connected accounts each template's Zip uses (ids from accounts.SERVICES). Onboarding offers Connect Google
# only when a picked Zip uses Gmail, Calendar or Drive (Colin, Oct 8 evening). A "services" list in
# zip-templates.json wins over this one.
TEMPLATE_SERVICES = {
    "inbox": ["gmail"],
    "calendar": ["calendar", "gmail"],
    "todo": ["trello", "gmail"],
    "meetings": ["drive", "calendar"],
    "brief": ["gmail", "calendar", "trello"],
    "saver": ["gmail"],
    "family": ["gmail", "calendar"],
    "leads": ["gmail"],
    "errands": ["calendar"],
    "cleanup": [],
    "publisher": [],
    "coder": [],
}


def template_services(t: dict) -> list[str]:
    given = t.get("services")
    names = given if isinstance(given, list) else TEMPLATE_SERVICES.get(str(t.get("id")), [])
    return [str(s) for s in names if isinstance(s, str)][:10]


# A service a template can do without (the Daily Brief still briefs with no board).
TEMPLATE_OPTIONAL = {"brief": {"trello"}}


def template_missing(t: dict) -> list[str]:
    """Services this home can never give the template. With connectors, every service can be connected. Without
    them, mail and a calendar can be added in the app (so those templates stay available and the app asks for the
    mailbox), a board needs integrations.trello, and Drive needs a plan of the person's own."""
    from . import home
    if home.connectors() != "none":
        return []
    optional = TEMPLATE_OPTIONAL.get(str(t.get("id")), set())
    out = []
    for s in template_services(t):
        if s in optional or s in ("gmail", "mail", "calendar"):
            continue
        if s == "trello" and home.trello():
            continue
        out.append(s)
    return out


_TRELLO_ASIDE = re.compile(r"\s*\([^()]*\bTrello\b[^()]*\)")
_TRELLO_SENTENCE = re.compile(r"[^.!?\n]*\bTrello\b[^.!?\n]*[.!?]?")


def template_text(text: str) -> str:
    """A template's words with nothing about a Trello board in a home that has none (live convo QA 2: To-Do offered
    to "connect the Trello board" in Sam's home). A home's copy of the templates is written once, so older copies
    still name it."""
    from . import home
    if home.trello():
        return text
    out = _TRELLO_ASIDE.sub("", text)
    return _TRELLO_SENTENCE.sub("", out) if "Trello" in out else out


@app.get("/api/templates")
def zip_templates(_: str = Depends(require_device)):
    """The gallery's cards. Picking one asks the lead Zip to create it, so it can ask what the Zip needs."""
    try:
        data = json.loads(TEMPLATES.read_text())
    except (OSError, ValueError):
        data = []
    taken = {b.id for b in config.BOTS}
    first = who.first()

    def named(v):
        """zip-templates.json says {name} for the person (WP2); the app shows the person's first name. An integration
        the home lacks is left out, as the plugin does for the Zip it makes (template_text)."""
        if isinstance(v, str):
            return template_text(v.replace("{name}", first))
        if isinstance(v, list):
            return [x for x in (named(x) for x in v) if x != ""]
        if isinstance(v, dict):
            return {k: named(x) for k, x in v.items()}
        return v
    data = [named(t) for t in data if isinstance(t, dict)]
    return {"templates": [{"id": t["id"], "name": t["name"], "pitch": t["pitch"], "look": t["look"],
                           "needs": t.get("needs") or [], "services": template_services(t), "routine": (t.get("routine") or {}).get("display"),
                           "added": t.get("zip_id") in taken, "available": not template_missing(t),
                           "missing": template_missing(t)} for t in data]}


# MARK: How a Zip looks

@app.get("/api/looks/parts")
def look_parts(_: str = Depends(require_device)):
    return {"parts": looks.PARTS, "bodies": looks.BODIES, "outfits": looks.OUTFITS}


@app.put("/api/bots/{bot_id}/look")
def set_look(bot_id: str, body: dict, _: str = Depends(require_device)):
    _bot(bot_id)
    try:
        return {"ok": True, "look": looks.save(bot_id, body)}
    except looks.LookError as e:
        raise HTTPException(400, str(e))


# MARK: Renaming bots

class RenameBody(BaseModel):
    name: str


@app.post("/api/bots/{bot_id}/name")
def rename_bot(bot_id: str, body: RenameBody, _: str = Depends(require_device)):
    """Renames a bot everywhere it matters: the app, the bot's own SOUL and Taffy's team table."""
    import json as _json
    import re as _re
    bot = _bot(bot_id)
    name = " ".join(body.name.split())
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .'\-]{0,23}", name):
        raise HTTPException(400, "Use up to 24 letters, numbers and spaces.")
    if any(b.id != bot_id and b.name.lower() == name.lower() for b in config.BOTS):
        raise HTTPException(409, f"Another bot is already called {name}.")
    if name == bot.name:
        return {"ok": True, "name": name}
    old = bot.name

    soul = config.HERMES_HOME / "SOUL.md" if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id / "SOUL.md"
    try:
        lines = soul.read_text().split("\n")
        if lines and lines[0].strip() == f"# {old}":
            lines[0] = f"# {name}"
        for i, l in enumerate(lines):
            if l.startswith(f"You are {old},"):
                lines[i] = f"You are {name}," + l[len(f"You are {old},"):]
                break
        soul.write_text("\n".join(lines))
    except OSError:
        log.warning("could not update the SOUL for %s", bot_id)
    if bot_id != "default":
        taffy_soul = config.HERMES_HOME / "SOUL.md"
        try:
            lines = taffy_soul.read_text().split("\n")
            prefix = f"| `{bot_id}` | "
            inside = False   # the hub's own team block is rewritten by team.sync_souls, not here
            for i, l in enumerate(lines):
                if l.startswith(team.SOUL_START):
                    inside = True
                elif l.startswith(team.SOUL_END):
                    inside = False
                elif l.startswith(prefix) and not inside:
                    rest = _re.sub(r"^Called [^.|]+\. ", "", l[len(prefix):])
                    lines[i] = prefix + ("" if name.lower() == bot_id else f"Called {name}. ") + rest
            taffy_soul.write_text("\n".join(lines))
        except OSError:
            log.warning("could not update Taffy's team table for %s", bot_id)

    try:
        names = _json.loads(config.NAMES.read_text())
    except (OSError, ValueError):
        names = {}
    names[bot_id] = name
    tmp = config.NAMES.with_suffix(".tmp")
    tmp.write_text(_json.dumps(names, indent=1))
    tmp.replace(config.NAMES)
    config.reload_bots()
    try:
        team.sync_souls()   # every bot's roster shows the new name right away
    except Exception:
        log.warning("team roster sync after rename failed", exc_info=True)
    return {"ok": True, "name": name}


# MARK: Bots Taffy created

@app.delete("/api/bots/{bot_id}")
def remove_bot(bot_id: str, _: str = Depends(require_device)):
    """Deletes a bot: its Hermes profile (history, memory, vault) goes; its work folder stays. Taffy stays."""
    import json as _json
    import os as _os
    import subprocess as _sp
    bot = _bot(bot_id)
    if bot_id == "default":
        raise HTTPException(400, f"{bot.name} leads the team and cannot be deleted.")
    if turns.is_busy(bot_id):
        raise HTTPException(409, f"{bot.name} is answering right now. Try again when it finishes.")
    p = _sp.run([str(config.HERMES_BIN), "profile", "delete", "-y", bot_id], capture_output=True, text=True,
                timeout=120, env=dict(_os.environ, HERMES_HOME=str(config.HERMES_HOME)))
    if p.returncode != 0 and (config.HERMES_HOME / "profiles" / bot_id).exists():
        log.info("profile delete failed: %s", (p.stdout + p.stderr)[-300:])
        raise HTTPException(500, f"Could not remove {bot.name}. Try again in a moment.")
    looks.forget(bot_id)
    try:
        reg = [e for e in _json.loads(config.REGISTRY.read_text()) if e.get("id") != bot_id]
        tmp = config.REGISTRY.with_suffix(".tmp")
        tmp.write_text(_json.dumps(reg, indent=1))
        tmp.replace(config.REGISTRY)
    except (OSError, ValueError):
        pass
    soul = config.HERMES_HOME / "SOUL.md"
    try:
        lines = soul.read_text().split("\n")
        out, inside = [], False
        for l in lines:   # Taffy's own team table; the hub's team block is rebuilt by team.sync_souls
            inside = (inside or l.startswith(team.SOUL_START)) and not l.startswith(team.SOUL_END)
            if inside or not l.startswith(f"| `{bot_id}` |"):
                out.append(l)
        soul.write_text("\n".join(out))
    except OSError:
        pass
    store.set_session(bot_id, "")
    config.reload_bots()
    return {"ok": True}


# MARK: Paired devices

@app.get("/api/devices")
def list_devices(authorization: str | None = Header(None), _: str = Depends(require_device)):
    return {"devices": store.devices((authorization or "")[7:])}


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: int, _: str = Depends(require_device)):
    if not store.remove_device(device_id):
        raise HTTPException(404, "That device is already gone.")
    return {"ok": True}


class DeleteEverythingBody(BaseModel):
    confirm: str = ""


@app.post("/api/delete-everything")
def delete_everything(body: DeleteEverythingBody, authorization: str | None = Header(None),
                      _: str = Depends(require_device)):
    """Settings, Delete Everything: removes what the hub itself holds about the person (erase.py lists exactly what
    goes and what stays). The app asks for a confirmation first; the request must carry the phrase too, so a
    stray call cannot do it. Hermes's own files are never touched. Zips that are answering must finish first."""
    if body.confirm.strip().lower() != "delete everything":
        raise HTTPException(400, "Say \"delete everything\" to confirm.")
    try:
        return erase.delete_everything((authorization or "")[7:])
    except erase.Busy:
        raise HTTPException(409, "A Zip is answering right now. Try again when it finishes.") from None


class SnoozeBody(BaseModel):
    until: float | None = None   # Unix time; None ends the snooze


@app.post("/api/bots/{bot_id}/snooze")
def snooze_bot(bot_id: str, body: SnoozeBody, _: str = Depends(require_device)):
    """No alerts from this bot until the given time. It keeps working; results wait in the app."""
    _bot(bot_id)
    snoozed = {k: v for k, v in (store.get_setting("snoozed", {}) or {}).items() if v > time.time()}
    if body.until and body.until > time.time():
        snoozed[bot_id] = min(body.until, time.time() + 31 * 86400)
    else:
        snoozed.pop(bot_id, None)
    store.set_setting("snoozed", snoozed)
    return {"ok": True, "snoozed_until": snoozed.get(bot_id)}


class NotifyBody(BaseModel):
    on: bool


@app.put("/api/bots/{bot_id}/notify")
def notify_bot(bot_id: str, body: NotifyBody, _: str = Depends(require_device)):
    """Whether this bot's finished work and waiting cards send a notification. Off keeps them in the app only."""
    _bot(bot_id)
    store.set_notify(bot_id, body.on)
    return {"ok": True, "notify": store.notify_on(bot_id)}


# MARK: Pictures bots share (share_screen)

@app.get("/api/media/{media_id}")
def get_media(media_id: str, _: str = Depends(require_device)):
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{32}", media_id):
        raise HTTPException(404, "That picture isn't there anymore.")
    from . import safe_files
    media = config.HUB_DIR / "media"
    try:
        # Only a regular file in the hub's own media folder, never a link to somewhere else (safe_files.py).
        f = safe_files.open_file(str(media / f"{media_id}.jpg"), (media,), protected=None)
    except safe_files.Refused:
        raise HTTPException(404, "That picture is gone.") from None
    return _file_response(f, None, "image/jpeg", {"Cache-Control": "private, max-age=86400"})


# MARK: Welcome flow

@app.get("/api/me")
async def about_me(request: Request, _: str = Depends(require_device)):
    """What Taffy knows about the person and what is already set up, so the welcome never asks twice."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    try:
        await accounts.check()   # "Your Accounts" lists what really works, not a fixed list
    except Exception:
        log.warning("accounts check for /api/me failed", exc_info=True)
    return await asyncio.to_thread(profile.summary, host)


# MARK: What I Know About You (facts.py): the facts the Zips share, which the person can read, change and delete

class FactBody(BaseModel):
    text: str
    kind: str | None = None


@app.get("/api/facts")
def list_facts(_: str = Depends(require_device)):
    """Every current fact, newest first, with where it came from in plain words and the heading it goes under."""
    items = facts.for_app()
    return {"facts": items, "headings": [facts.KIND_HEADINGS[k] for k in facts.KINDS]}


@app.post("/api/facts")
def add_fact(body: FactBody, _: str = Depends(require_device)):
    text = validate.short(body.text, 400, "fact") or ""
    try:
        f = facts.add_by_person(text, kind=body.kind if body.kind in facts.KINDS else "other")
    except facts.FactError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": f.get("id")}


@app.put("/api/facts/{fact_id}")
def edit_fact(fact_id: int, body: FactBody, _: str = Depends(require_device)):
    text = validate.short(body.text, 400, "fact") or ""
    try:
        facts.edit(fact_id, text)
    except facts.FactError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/facts/{fact_id}")
def delete_fact(fact_id: int, _: str = Depends(require_device)):
    if not facts.delete(fact_id):
        raise HTTPException(404, "That fact is already gone.")
    return {"ok": True}


# MARK: Onboarding (the app's first-run flow)

@app.post("/api/onboarding")
def save_onboarding(body: dict, device: str = Depends(require_device)):
    """The answers from the app's onboarding. The agent's name renames the lead Zip, with the same rules and
    side effects as renaming it from its profile."""
    try:
        answers = onboarding.clean(body)
    except onboarding.OnboardingError as e:
        raise HTTPException(400, str(e))
    if answers["agent_name"]:
        rename_bot("default", RenameBody(name=answers["agent_name"]), device)
        answers["agent_name"] = config.BOTS_BY_ID["default"].name
    if answers["look"]["body"]:
        try:   # the look picked on the first screen becomes the lead Zip's look
            looks.save("default", {"body": answers["look"]["body"], "outfit": answers["look"]["outfit"] or "none"})
        except looks.LookError:
            pass
    record = onboarding.save(answers)
    try:
        onboarding.apply(answers)
    except Exception:
        log.warning("could not pass the onboarding answers to the Zips", exc_info=True)
    return {"ok": True, "onboarding": record}


@app.get("/api/onboarding")
def get_onboarding(_: str = Depends(require_device)):
    return {"onboarding": onboarding.load()}


# MARK: Voice calls (ChatGPT realtime voice through Codex, see voice.py)

class VoiceStartBody(BaseModel):
    sdp: str
    voice: str | None = None
    voice_chosen: bool | None = None   # the person just chose `voice` on this device (voice.pick_voice)


@app.post("/api/bots/{bot_id}/voice")
async def voice_start(bot_id: str, body: VoiceStartBody, _: str = Depends(require_device)):
    _bot(bot_id)
    if not body.sdp.startswith("v=0") or len(body.sdp) > 200_000:
        raise HTTPException(400, "The call couldn't start. Try again.")
    try:
        call, answer = await voice.start(bot_id, body.sdp, body.voice, chosen=body.voice_chosen)
    except voice.MinutesUsed:
        raise HTTPException(503, voice.MINUTES_USED)   # not a broken sign-in: the app switches to On-Device Voice
    except Exception as e:
        log.warning("voice start failed: %s", e)
        health.voice_failed()
        # A missing Codex or ChatGPT sign-in says what to do; the app shows this sentence (CallEngine).
        raise HTTPException(503, voice.start_error(e))
    return {"call_id": call.id, "sdp": answer, "voice": call.voice}


@app.get("/api/voice/{call_id}/events")
async def voice_events(call_id: str, _: str = Depends(require_device)):
    call = voice.calls.get(call_id)
    if not call:
        raise HTTPException(404, "That call has ended.")

    async def gen():
        while True:
            try:
                ev = await asyncio.wait_for(call.events.get(), 15)
            except TimeoutError:
                yield ": keepalive\n\n"
                if call.closed:
                    return
                continue
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") == "closed":
                return
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ReadyBody(BaseModel):
    waited_ms: int | None = None     # from the tap on Call to connected; a long wait gets an honest hello


@app.post("/api/voice/{call_id}/ready")
async def voice_ready(call_id: str, body: ReadyBody | None = None, _: str = Depends(require_device)):
    await voice.greet(call_id, waited_ms=body.waited_ms if body else None)
    return {"ok": True}


class TalkingBody(BaseModel):
    talking: bool


class AwayBody(BaseModel):
    away: bool


@app.post("/api/voice/{call_id}/away")
def voice_away(call_id: str, body: AwayBody, _: str = Depends(require_device)):
    """The call is on hold on the phone (he took a phone call, Siri spoke), or back."""
    voice.set_away(call_id, body.away)
    return {"ok": True}


@app.post("/api/voice/{call_id}/talking")
def voice_talking(call_id: str, body: TalkingBody, _: str = Depends(require_device)):
    """The phone hears Colin start or stop speaking, before any words are recognized."""
    voice.set_talking(call_id, body.talking)
    return {"ok": True}


@app.post("/api/voice/{call_id}/stop")
async def voice_stop(call_id: str, _: str = Depends(require_device)):
    await voice.stop(call_id, reason="the app ended it")
    return {"ok": True}


class DiagnosticsBody(BaseModel):
    events: list          # anything that is not an event object is skipped, not the whole batch


@app.post("/api/voice/{call_id}/diagnostics")
def voice_diagnostics(call_id: str, body: DiagnosticsBody, _: str = Depends(require_device)):
    """The app's own events for a call (CallKit or the fallback and why, audio routes, interruptions, its
    timings), saved with the hub's lines for the same call. Ids starting `app-` are calls that never got a
    hub id. Nothing is saved when Save Call Transcripts is off."""
    if not calllog.valid_id(call_id) or len(body.events) > calllog.APP_EVENTS_PER_POST:
        raise HTTPException(400, "That didn't work.")
    return {"ok": True, "saved": calllog.app_events(call_id, body.events)}


@app.get("/api/voices")
def voice_list(_: str = Depends(require_device)):
    return {"voices": voice.VOICES, "default": voice.DEFAULT_VOICE}


@app.get("/api/voice/engines")
def voice_engines(_: str = Depends(require_device)):
    """Whether ChatGPT's voice can take a call now (signed in, minutes left); the app uses On-Device Voice when not."""
    return voice.engines()


# MARK: On-Device Voice calls (the phone listens and speaks; the words stream from here, see ondevice.py)

class LocalVoiceStartBody(BaseModel):
    voice: str | None = None
    reason: str | None = None     # why this call is on-device: chosen, signed_out, minutes, chatgpt_failed
    voice_chosen: bool | None = None


@app.post("/api/bots/{bot_id}/voice/local")
async def voice_local_start(bot_id: str, body: LocalVoiceStartBody, _: str = Depends(require_device)):
    _bot(bot_id)
    call = await ondevice.start(bot_id, body.voice, body.reason, body.voice_chosen)
    return {"call_id": call.id, "greeting": call.local.greeting, "greeting_late": call.local.greeting_late,
            "voice": call.voice, "engine": ondevice.ENGINE}


def _local_call(call_id: str) -> voice.Call:
    call = voice.calls.get(call_id)
    if not call or call.engine != ondevice.ENGINE:
        raise HTTPException(404, "That call has ended.")
    return call


class TurnBody(BaseModel):
    n: int
    text: str | None = None
    news: int | None = None
    speculative: bool = False


@app.post("/api/voice/{call_id}/turn")
async def voice_turn(call_id: str, body: TurnBody, _: str = Depends(require_device)):
    """One reply, streamed as server-sent events: text pieces, then done."""
    call = _local_call(call_id)
    if (body.text is None) == (body.news is None) or len(body.text or "") > 8000 or not 0 <= body.n < 1_000_000:
        raise HTTPException(400, "That didn't work.")

    async def gen():
        async for ev in ondevice.turn(call, body.n, text=body.text, news=body.news, speculative=body.speculative):
            yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ConfirmBody(BaseModel):
    n: int


@app.post("/api/voice/{call_id}/confirm")
def voice_confirm(call_id: str, body: ConfirmBody, _: str = Depends(require_device)):
    """The phone is sure the person finished: a speculative turn becomes real (its hand-off may start)."""
    return {"ok": ondevice.confirm(_local_call(call_id), body.n)}


class SaidBody(BaseModel):
    n: int
    spoken: str = ""
    cut_off: bool = False
    first_audio_ms: int | None = None


@app.post("/api/voice/{call_id}/said")
def voice_said(call_id: str, body: SaidBody, _: str = Depends(require_device)):
    """What the phone actually said for a turn, and whether the person cut it off."""
    return {"ok": ondevice.said(_local_call(call_id), body.n, body.spoken, body.cut_off, body.first_audio_ms)}


# MARK: Bots using Colin's Mac (see mac.py)

@app.get("/api/mac/online")
def mac_online(_: str = Depends(require_device)):
    return {"online": mac.online()}


class MacAnswerBody(BaseModel):
    allow: bool
    key_id: str = ""
    signature: str = ""


class MacApproverBody(BaseModel):
    key: str
    name: str = "iPhone"


@app.get("/api/mac/approvals")
def mac_approvals(_: str = Depends(require_device)):
    """The open request to use the Mac (for the chat card and the call screen), whether the Mac can be reached, and
    the task a Zip is doing on it now."""
    return mac.state_for_phone()


@app.post("/api/mac/approvals/{approval_id}")
async def mac_answer(approval_id: str, body: MacAnswerBody, _: str = Depends(require_device)):
    """Colin's answer from the phone. Allow carries the phone's Face ID signature; the Mac checks it (mac.py)."""
    try:
        return {"ok": True, "text": await mac.answer_from_phone(approval_id, body.allow, body.key_id, body.signature)}
    except mac.MacError as e:
        raise HTTPException(409, str(e))


@app.post("/api/mac/approver")
async def mac_approver(body: MacApproverBody, authorization: str | None = Header(None),
                       _: str = Depends(require_device)):
    """This phone's public key for allowing Mac use from afar. The Mac asks Colin once whether to trust it."""
    try:
        await mac.register_approver(store._hash((authorization or "")[7:]), body.key, body.name)
    except (mac.MacError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/mac/stop")
async def mac_stop(_: str = Depends(require_device)):
    """Stop from the phone: the same as Stop on the Mac's in-use bar."""
    return {"ok": True, "text": await mac.stop_from_phone()}


@app.websocket("/api/mac/link")
async def mac_link(ws: WebSocket):
    """The Mac app's connection while Taffy is open on the Mac."""
    auth = ws.headers.get("authorization") or ""
    await ws.accept()
    if not auth.startswith("Bearer ") or not store.device_for_token(auth[7:]):
        await ws.close(code=4401, reason="not paired")
        return
    if not store.may_hold_mac_link(auth[7:]):
        await ws.close(code=4403, reason="another Mac holds the link")
        return
    await mac.serve_link(ws)


def _internal_key() -> str:
    """A key only processes running as this home's user can read (the bots' tools)."""
    import os
    import secrets as _secrets
    path = config.HUB_DIR / "internal-key"
    if not path.exists():
        path.write_text(_secrets.token_urlsafe(32))
        os.chmod(path, 0o600)
    return path.read_text().strip()


def require_internal(request: Request, x_taffy_internal: str | None = Header(None)) -> None:
    import hmac
    # Requests that came through Tailscale carry these headers; internal calls never do.
    if request.headers.get("tailscale-user-login") or request.headers.get("x-forwarded-for"):
        raise HTTPException(404, "Not found.")
    if not x_taffy_internal or not hmac.compare_digest(x_taffy_internal, _internal_key()):
        raise HTTPException(404, "Not found.")


class MacBody(BaseModel):
    bot_id: str
    session_id: str = ""
    args: dict = {}

    @model_validator(mode="after")
    def _conversation(self):
        """The Zip's tools send the session Hermes is running now. After a compaction that moved the chat to a
        continuation session, that is not the id the hub stored for the chat; every check here compares with the
        stored one (lineage.py)."""
        if self.session_id:
            try:
                from . import lineage
                self.session_id = lineage.root(self.bot_id if self.bot_id in config.BOTS_BY_ID else None,
                                               self.session_id) or self.session_id
            except Exception:
                pass
        return self


MAC_OPS = {"open", "run", "screenshot", "ui", "press", "click", "type", "key", "scroll", "steps"}


@app.post("/internal/mac/{op}")
async def internal_mac(op: str, body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": "You are answering a teammate's question. Answer only; use tools like this in "
                                      "your own work."}
    try:
        if op == "start":
            text = await mac.start(bot.id, bot.name, str(body.args.get("task") or ""),
                                   str(body.args.get("person_said") or body.args.get("colin_said") or ""),
                                   body.session_id)
            return {"ok": True, "result": text}
        if op == "done":
            return {"ok": True, "result": await mac.done(bot.id, body.session_id)}
        if op == "status":
            return {"ok": True, "result": {"online": mac.online()}}
        if op == "react":
            return await _bot_react(bot.id, str(body.args.get("kind") or ""))
        if op not in MAC_OPS:
            raise HTTPException(404, "Unknown action.")
        return {"ok": True, "result": await mac.act(bot.id, op, body.args, body.session_id)}
    except mac.MacError as e:
        return {"ok": False, "error": str(e)}


_internal_key()   # created at startup so the bots' tools can read it


# MARK: Choice cards (see choices.py)

class ChoiceBody(BaseModel):
    picked: str | None = None
    custom: bool = False


@app.post("/api/bots/{bot_id}/choices/{feed_id}")
async def answer_choice(bot_id: str, feed_id: int, body: ChoiceBody, _: str = Depends(require_device)):
    """Colin tapped an option (the app then sends it as his message) or dismissed the card."""
    _bot(bot_id)
    try:
        card = choices.answer(bot_id, feed_id, body.picked, body.custom)
    except choices.ChoiceError as e:
        raise HTTPException(409, str(e))
    _acknowledge(bot_id, card)
    return {"ok": True, "card": card}


def _acknowledge(bot_id: str, card: dict, push: bool = False) -> None:
    """An answer the hub closed itself wakes the Zip for a one-line reply (notices.ack_note)."""
    note = card.pop("ack", None)
    if note:
        asyncio.create_task(turns.follow_up(bot_id, note, None, urgent=False, push=push))


class LockBody(BaseModel):
    action: str             # "option:N" (the card's own answer N), "yes", "not_now" or "reply"
    text: str | None = None
    unlocked: bool = False  # the button asked for Face ID first (a money or health question's answers)


@app.post("/api/bots/{bot_id}/choices/{feed_id}/lock")
async def lock_answer(bot_id: str, feed_id: int, body: LockBody, _: str = Depends(require_device)):
    """An answer from the notification itself (Push.swift), with the app in the background. Colin's words go to Zero
    here, since the app is not open to send them. A Yes on a send, a prepared action or a money or health question
    never answers it: a second notification opens the card instead (notices.lock_answer)."""
    _bot(bot_id)
    try:
        card = notices.lock_answer(feed_id, body.action, body.text or "", unlocked=body.unlocked)
    except notices.AnswerError as e:
        raise HTTPException(409, str(e))
    words = card.pop("send", None)
    if words:
        turns.send("default", words)
    _acknowledge(bot_id, card, push=True)   # answered from the lock screen: the reply comes as a notification
    line = card.pop("open", None)
    if line:
        r = notices.by_feed(feed_id)
        await notices.send_push(notices._lead(), line, notices.collapse_id(r["subject"]) if r else "q-open", feed_id,
                                category=notices.OPEN_ONLY)
    return {"ok": True, "card": card, "opened": bool(line)}


class NotificationReplyBody(BaseModel):
    text: str
    line: str | None = None       # the notification's own text, which the words answer
    item_id: int | None = None    # the chat item it came from (a scheduled report), for the whole of it


@app.post("/api/bots/{bot_id}/notification-reply")
async def notification_reply(bot_id: str, body: NotificationReplyBody, _: str = Depends(require_device)):
    """Words typed on a Zip's notification (Oct 10, Push.swift: Reply on any message from a Zip). They go to that Zip
    as a reply to what the notification said (the whole report when it came from one), the same as a swipe-to-reply
    in the chat, and the Zip's answer comes back as a notification, since the app is not open."""
    bot = _bot(bot_id)
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(400, "Type an answer first.")
    quoted = " ".join((body.line or "").split())
    if body.item_id:
        row = store.db().execute("SELECT * FROM feed WHERE id = ? AND bot_id = ?", (body.item_id, bot_id)).fetchone()
        if row and row["kind"] in ("checkin", "message"):
            quoted = f"{row['title']}: {row['body']}" if row["kind"] == "checkin" else row["body"]
    turn = turns.send(bot_id, text[:4000], reply_to={"text": quoted[:20000], "who": "bot"} if quoted else None)
    asyncio.create_task(_tell_reply(turn, bot.name))
    return {"ok": True}


async def _tell_reply(turn: turns.Turn, name: str) -> None:
    reply = ""
    try:
        async for ev in turn.follow():
            if ev and ev.get("type") == "reply":
                reply = ev.get("text") or ""
    except Exception:
        log.warning("could not follow the reply to a notification", exc_info=True)
        return
    if reply.strip():
        await watcher.push(name, reply, turn.bot_id, urgent=True, kind="done")   # he is waiting on it


class StopBody(BaseModel):
    what: str   # "subject": don't ask about this again; "kind": stop this kind of question


@app.post("/api/bots/{bot_id}/choices/{feed_id}/stop")
def stop_question(bot_id: str, feed_id: int, body: StopBody, _: str = Depends(require_device)):
    """The menu on a proactive question from Zero (notices.py)."""
    _bot(bot_id)
    try:
        return {"ok": True, "card": notices.stop(feed_id, body.what)}
    except notices.AnswerError as e:
        raise HTTPException(409, str(e))


# MARK: Proactivity (notices.py): off by default; Settings turns it on and shows each kind of question

class ProactivityBody(BaseModel):
    enabled: bool | None = None
    noticers: dict[str, str] | None = None   # noticer id -> on | digest | off


@app.get("/api/proactivity")
def get_proactivity(_: str = Depends(require_device)):
    return notices.status()


@app.put("/api/proactivity")
def put_proactivity(body: ProactivityBody, _: str = Depends(require_device)):
    for nid, m in (body.noticers or {}).items():
        if nid not in notices.NOTICERS or m not in notices.MODES:
            raise HTTPException(400, "Unknown kind of question or setting.")
    for nid, m in (body.noticers or {}).items():
        notices.set_mode(nid, m, "" if m == "on" else "You changed this in Settings.")
    if body.enabled is not None:
        notices.set_enabled(body.enabled)
    return notices.status()


@app.post("/api/proactivity/stopped/{notice_id}/undo")
def undo_stopped_question(notice_id: int, _: str = Depends(require_device)):
    if not notices.undo_stop(notice_id):
        raise HTTPException(404, "That question isn't stopped.")
    return notices.status()


@app.post("/internal/chat/offer_choices")
async def internal_offer_choices(body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; offer choices to {who.first()} in your own chat."}
    turn = turns.current(bot.id)
    during = bool(turn and not turn.done and body.session_id and body.session_id == store.session_for(bot.id))
    try:
        return {"ok": True, "result": choices.offer(bot.id, str(body.args.get("question") or ""),
                                                    list(body.args.get("options") or []), during)}
    except choices.ChoiceError as e:
        return {"ok": False, "error": str(e)}


# MARK: Sign-in cards from a chat (see signin.py)

class SignInCardBody(BaseModel):
    outcome: str   # "declined" when Colin closes the card


@app.post("/api/bots/{bot_id}/login-requests/{feed_id}")
def close_signin_card(bot_id: str, feed_id: int, body: SignInCardBody, _: str = Depends(require_device)):
    _bot(bot_id)
    if body.outcome != "declined":
        raise HTTPException(400, "Use Save Login or Sign In Myself.")
    try:
        return {"ok": True, "card": signin.resolve(bot_id, feed_id, "declined")}
    except signin.AskError as e:
        raise HTTPException(409, str(e))


@app.post("/internal/chat/ask_sign_in")
async def internal_ask_sign_in(body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; ask {who.first()} to sign in from your own chat."}
    try:
        url = validate.secure_url(str(body.args.get("url") or ""))
    except HTTPException:
        return {"ok": False, "error": "Give the sign-in page's full address, starting with https://."}
    try:
        feed_id, result = signin.ask(bot.id, url, str(body.args.get("why") or ""), body.session_id or None)
    except signin.AskError as e:
        return {"ok": False, "error": str(e)}
    if feed_id:
        site = consent.site_of(url)
        why = " ".join(str(body.args.get("why") or "").split())[:150]
        await watcher.push(f"{bot.name} needs a login", f"Can you sign in to {site}" + (f" so I can {why}" if why else "")
                           + "?", bot.id)
    return {"ok": True, "result": result}


@app.post("/internal/chat/close_sign_in")
async def internal_close_sign_in(body: MacBody, _: None = Depends(require_internal)):
    """close_sign_in_card: the Zip closes a sign-in card it no longer needs (Oct 9: Colin bought his contacts over
    the phone, and the Lens Shop card stayed open). The app drops the card on its next look."""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": "You are answering a teammate; close sign-in cards from your own chat."}
    try:
        sites = signin.retire(bot.id, str(body.args.get("site") or body.args.get("url") or ""))
    except signin.AskError as e:
        return {"ok": False, "error": str(e)}
    listed = " and ".join(sites)
    return {"ok": True, "result": f"The card to sign in to {listed} is closed and gone from {who.possessive()} "
                                  "chat. No need to mention it unless it matters to your answer."}


@app.post("/internal/chat/close_card")
async def internal_close_card(body: MacBody, _: None = Depends(require_internal)):
    """close_card: the Zip closes one of its own okay cards that the person's later words made wrong (live convo QA
    2: "No, I meant the other Jordan" left the first Jordan's send card open, and Inbox asked Sam to decline it). The
    card leaves the app's list, and the chat's history notes it closed; nothing is sent."""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": "You are answering a teammate; close cards from your own chat."}
    try:
        req = int(body.args.get("request"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Give the card's request number."}
    withdraw = getattr(consent, "withdraw", None)   # the home's plugin may predate close_card
    r = withdraw(bot.id, req) if withdraw else None
    if not r:
        return {"ok": False, "error": f"Request {req} is not one of your cards still waiting in this chat."}
    log.info("%s closed its own card %s (%s)", bot.id, req, (r.get("kind") or "action"))
    store.add_feed("signin_decision", bot.id, None, f"Closed: {(r['reason'] or 'a card')[:80]}",
                   f"{bot.name} closed this card, so nothing was done: {r['reason']}.")
    return {"ok": True, "result": f"Request {req} is closed and gone from {who.possessive()} chat; nothing was "
                                  "sent or done. No need to mention it unless it matters to your answer."}


# MARK: Bots talking to each other (see team.py)

@app.post("/internal/team/{op}")
async def internal_team(op: str, body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    a = body.args
    try:
        if op == "context":
            return {"ok": True, "result": await asyncio.to_thread(team.context_for, bot.id, body.session_id,
                                                                  bool(a.get("first")), bool(a.get("work")),
                                                                  str(a.get("message") or "")[:4000])}
        if op == "ask":
            return {"ok": True, "result": await team.ask(bot.id, str(a.get("bot") or ""), str(a.get("question") or ""),
                                                         body.session_id)}
        if op == "tell":
            return {"ok": True, "result": team.tell(bot.id, str(a.get("bot") or ""), str(a.get("note") or ""),
                                                    body.session_id)}
        if op == "note":
            if team.in_team_session(body.session_id):
                return {"ok": False, "error": "You are answering a teammate's question. Save notes in your own work."}
            rep = a.get("replaces")
            return {"ok": True, "result": team.add_note(bot.id, str(a.get("fact") or ""),
                                                        int(rep) if str(rep or "").isdigit() else None,
                                                        body.session_id or None)}
        if op == "fact":
            if team.in_team_session(body.session_id):
                return {"ok": False, "error": "You are answering a teammate's question. Save facts in your own work."}
            return {"ok": True, "result": team.save_fact(bot.id, a, body.session_id or None)}
        if op == "recall":
            return {"ok": True, "result": await asyncio.to_thread(facts.recall, str(a.get("query") or "")[:300],
                                                                  bot.id, body.session_id or "")}
    except team.TeamError as e:
        return {"ok": False, "error": str(e)}
    raise HTTPException(404, "Unknown action.")


async def _bot_react(bot_id: str, kind: str) -> dict:
    """A bot's tapback on Colin's latest message in its chat."""
    if kind not in store.REACTIONS:
        return {"ok": False, "error": "Unknown reaction. Use heart, like, dislike, laugh, emphasize or question."}
    msgs = await hermes.messages(bot_id, limit=30)
    latest = next((m for m in reversed(msgs) if m["role"] == "user" and m.get("id") is not None), None)
    if not latest:
        return {"ok": False, "error": f"There is no message from {who.first()} to react to."}
    store.set_reaction(bot_id, str(latest["id"]), "bot", kind, latest["text"])
    return {"ok": True, "result": f"Reacted with {kind}."}


# MARK: Connected accounts (Gmail, Google Calendar, Google Drive, Trello and every other connector on the person's
# Claude account, or the Google apps on a ChatGPT plan; see accounts.py, docs/specs/2026-10-08-google-connect-design.md
# and docs/qa/2026-10-08-google-connect-v2.md)

@app.get("/api/accounts")
async def get_accounts(_: str = Depends(require_device)):
    """What the Zips can use, from a check under ten minutes old."""
    return await accounts.check()


@app.post("/api/accounts/check")
async def check_accounts(_: str = Depends(require_device)):
    """A fresh check, for the Connect screen while it is open (it polls; close checks share one answer)."""
    return await accounts.check(force=True)


class AccountCardBody(BaseModel):
    outcome: str   # "declined" when the person closes the card


@app.post("/api/bots/{bot_id}/account-requests/{feed_id}")
async def close_account_card(bot_id: str, feed_id: int, body: AccountCardBody, _: str = Depends(require_device)):
    _bot(bot_id)
    if body.outcome != "declined":
        raise HTTPException(400, "The card closes by itself once the account is connected.")
    try:
        return {"ok": True, "card": accounts.close(bot_id, feed_id)}
    except accounts.AskError as e:
        raise HTTPException(409, str(e))


@app.post("/internal/accounts/need")
async def internal_account_need(body: MacBody, _: None = Depends(require_internal)):
    """The connectors tool, before a run (and after one that found the account missing)."""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    service = str(body.args.get("service") or "")
    if accounts.home.connectors() == "none":
        return {"ok": True, "result": {"connected": False, "state": accounts.NEEDS_OWN_PLAN,
                                       "message": accounts.no_connectors_message()}}
    if team.in_team_session(body.session_id):
        # A teammate's question gets no card; the asking Zip hears that the account is missing.
        result = await accounts.check()
        st = accounts.state_of(result, service)
        if not accounts.find(service):
            return {"ok": True, "result": {"connected": False, "state": accounts.NO_SUCH,
                                           "message": accounts.no_such(service, result)}}
        if st in (accounts.CONNECTED, accounts.UNKNOWN, accounts.TROUBLE):
            return {"ok": True, "result": {"connected": True, "state": st, **accounts.route(result, service)}}
        return {"ok": True, "result": {"connected": False, "state": st,
                                       "message": f"{accounts.find(service).name} "
                                                  f"isn't connected. Say so in your answer; don't ask {who.first()} "
                                                  "from here."}}
    try:
        result = await accounts.need(bot.id, service, str(body.args.get("why") or ""), body.session_id or None,
                                     bool(body.args.get("failed")))
    except accounts.AskError as e:
        return {"ok": False, "error": str(e)}
    if result.get("card"):
        s = accounts.find(service)
        await watcher.push(f"{bot.name} needs {s.name}", f"Can you connect {'Google' if s.group == 'google' else s.name} "
                                                          f"so I can use {s.name}?", bot.id)
    return {"ok": True, "result": result}


# MARK: The home itself: settings, mailboxes, calendars, Sign In with Google (homeapi.py)

from . import homeapi  # noqa: E402

homeapi.install(app, require_device, require_internal)


# MARK: Connect Apps: Trello, Google Drive and the rest through each provider's own API (apps.py, appsapi.py)

from . import appsapi  # noqa: E402

appsapi.install(app, require_device, require_internal)


# MARK: Fluid app (Oct 9): Stop, honest status, notification kinds, While You Were Away
#
# Evidence rows are from docs/research/2026-10-09-grokbot-dots-user-feedback.md; the app side and the field shapes
# are in docs/qa/2026-10-09-fluid-app.md.

@app.post("/api/bots/{bot_id}/stop")
async def stop_turn(bot_id: str, _: str = Depends(require_device)):
    """Stop (#55, #56): interrupts what the Zip is doing in its chat, hands back the person's queued messages (not
    sent) and holds the hub's own work in that chat until the person talks to it again (turns.stop)."""
    _bot(bot_id)
    out = await turns.stop(bot_id)
    activity.clear_outcome(bot_id)
    return {"ok": True, "stopped": out["stopped"], "returned": out["returned"]}


class NotifyKindsBody(BaseModel):
    needs: bool | None = None
    done: bool | None = None
    progress: bool | None = None


@app.put("/api/bots/{bot_id}/notify-kinds")
def set_notify_kinds(bot_id: str, body: NotifyKindsBody, _: str = Depends(require_device)):
    """Which kinds of news from this Zip reach the phone (#75 to #77): decisions and blocks, finished work, routine
    progress. The Zip's notify switch stays the master (notifyprefs.py)."""
    _bot(bot_id)
    return {"ok": True, "notify_kinds": notifyprefs.set_kinds(bot_id, needs=body.needs, done=body.done,
                                                               progress=body.progress)}


class ToneBody(BaseModel):
    tone: str


@app.put("/api/bots/{bot_id}/tone")
def set_tone(bot_id: str, body: ToneBody, _: str = Depends(require_device)):
    """The Zip's message tone (tones.py): one of the app's tones, "default", "none", or "custom" for a sound the
    person saved on their device."""
    _bot(bot_id)
    try:
        return {"ok": True, "tone": tones.set_tone(bot_id, body.tone)}
    except ValueError:
        raise HTTPException(400, "That tone isn't one Zero has.")


EXPIRED_STATUS_SEC = 24 * 3600


def _card_line(r: dict) -> str:
    """What an open card asks, in a few words, for a Zip's row."""
    kind = r.get("kind") or "signin"
    if kind == "signin":
        return f"sign in to {r.get('site') or 'a site'}"
    if kind == "navigate":
        return f"open {r.get('site') or 'a site'}"
    reason = " ".join(str(r.get("reason") or "").split()).rstrip(".")
    return (reason[:1].lower() + reason[1:])[:90] if reason else "the action on its card"


def _first_sentence(text: str, n: int = 100) -> str:
    t = re.sub(r"\s*URL:\s*\S+", "", " ".join(str(text or "").split())).strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    t = (m.group(1) if m else t).rstrip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "\u2026"


def _bot_statuses(pending: list[dict]) -> dict[str, dict]:
    """One honest line per Zip for its row (#43, #50 to #53), most pressing first: paused on a card, waiting for a
    sign-in, blocked, its last reply failed, a card that closed with no answer. A Zip with none of these has no
    status; while it works, the app shows its steps from the feed instead (activity.live)."""
    try:
        needs = board.grouped()["needs_you"]
    except Exception:
        needs = []
    now = time.time()
    out: dict[str, dict] = {}
    for b in config.BOTS:
        try:
            st = _bot_status(b, [r for r in pending if r.get("bot_id") == b.id],
                             [t for t in needs if (t.get("assignee") or "default") == b.id and t.get("status") == "blocked"],
                             now)
        except Exception:
            log.warning("could not work out %s's status", b.id, exc_info=True)
            st = None
        if st:
            out[b.id] = st
    return out


def _live_card(b: config.Bot, r: dict, said: float) -> bool:
    """A consent card the Zip is waiting on now. A card from its chat with the person counts until the person writes
    again; a card from a board task, a scheduled job or other work holds that work until it is answered or runs out."""
    sid = r.get("session_id")
    if r.get("task_id") or not sid or sid != store.session_for(b.id):
        return True
    return float(r.get("created") or 0) >= said


def _bot_status(b: config.Bot, cards: list[dict], blocked: list[dict], now: float) -> dict | None:
    """Paused only on a live wait (Oct 9, 11:18 p.m.): a card raised in the person's latest turn or after it, or one
    holding other work. An older card in the chat stays there to use, but the Zip is not paused on it."""
    said = store.said_at(b.id)
    cards = [r for r in cards if _live_card(b, r, said)]
    if cards:
        more = f", and {len(cards) - 1} more" if len(cards) > 1 else ""
        return {"state": "paused", "line": f"Paused for your okay: {_card_line(cards[0])}{more}.",
                "at": float(cards[0].get("created") or now)}
    asks = signin.live_asks(b.id)
    if asks:
        row, card = asks[0]
        what = (f"sign in to {card.get('site')}" if row["kind"] == "login" and card.get("site")
                else f"connect {card.get('name')}" if card.get("name") else "answer its card")
        return {"state": "paused", "line": f"Waiting for you to {what}.", "at": float(row["created_at"] or now),
                "feed_id": row["id"]}
    f = store.latest_feed_for(b.id)
    if blocked:
        t = blocked[0]
        reason = _first_sentence(t.get("block_reason") or "") or t.get("title") or "It needs you."
        return {"state": "blocked", "line": f"Blocked: {reason}", "task_id": t.get("id")}
    o = activity.outcome(b.id, now)
    if o:
        return {"state": "failed", "line": "Its last reply didn't finish.", "detail": o["text"],
                "words": o["words"]}
    if f and f["kind"] == "signin_decision" and (f["body"] or "").startswith(consent.NO_ANSWER_TITLE) \
            and now - f["created_at"] < EXPIRED_STATUS_SEC and f["created_at"] > activity.began_at(b.id):
        return {"state": "expired", "line": "Its card closed with no answer.", "what": f["title"] or "",
                "feed_id": f["id"]}
    return None


AWAY_KINDS = ("task_done", "action", "task_blocked", "task_review", "task_failed", "signin_request",
              "signin_decision", "message", "choices", "login", "account")


@app.get("/api/away")
def away(since: float, _: str = Depends(require_device)):
    """While You Were Away (#75, #76): what happened since the person last looked, one line per Zip, only for things
    worth saying (finished, waiting on them, problems, cards that closed, messages). Routine check-ins, team chatter
    and the Zips' own notes are left out. An empty list means there is nothing to say, and the app shows nothing."""
    return {"since": since, "items": _away_items(since)}


def _away_items(since: float, limit: int = 400) -> list[dict]:
    q = (f"SELECT * FROM feed WHERE created_at > ? AND kind IN ({','.join('?' * len(AWAY_KINDS))}) "
         f"ORDER BY id DESC LIMIT ?")
    rows = [dict(r) for r in store.db().execute(q, (since, *AWAY_KINDS, limit)).fetchall()]
    per: dict[str, dict] = {}
    for r in reversed(rows):
        bot = r["bot_id"] or "default"
        if bot not in config.BOTS_BY_ID:
            continue
        d = per.setdefault(bot, {"done": [], "needs": [], "failed": [], "closed": [], "messages": 0, "at": 0})
        d["at"] = max(d["at"], r["created_at"])
        first = ((r["body"] or "").split("\n\n")[0] or r["title"] or "").strip()
        k = r["kind"]
        if k in ("task_done", "action"):
            d["done"].append(first if k == "task_done" else (r["body"] or "").strip())
        elif k in ("task_blocked", "task_review", "signin_request"):
            d["needs"].append(first if k != "signin_request" else _first_sentence(r["body"] or "", 80))
        elif k in ("choices", "login", "account"):
            if '"state": "open"' in (r["body"] or ""):
                d["needs"].append(r["title"] or "a question")
        elif k == "task_failed":
            d["failed"].append(first)
        elif k == "signin_decision" and (r["body"] or "").startswith(consent.NO_ANSWER_TITLE):
            d["closed"].append(r["title"] or "a card")
        elif k == "message":
            d["messages"] += 1
    items = []
    for bot, d in per.items():
        parts = []
        if d["needs"]:
            parts.append(f"Needs you: {_clip(d['needs'][-1])}." if len(d["needs"]) == 1
                         else f"{len(d['needs'])} things need you.")
        if d["failed"]:
            parts.append(f"Hit a problem with {_clip(d['failed'][-1])}." if len(d["failed"]) == 1
                         else f"Hit {len(d['failed'])} problems.")
        if d["closed"]:
            parts.append(f"A card closed with no answer: {_clip(d['closed'][-1])}." if len(d["closed"]) == 1
                         else f"{len(d['closed'])} cards closed with no answer.")
        if d["done"]:
            parts.append(f"Finished: {_clip(d['done'][-1])}." if len(d["done"]) == 1
                         else f"Finished {len(d['done'])} things.")
        if d["messages"] and not parts:
            parts.append("Sent you a message." if d["messages"] == 1 else f"Sent you {d['messages']} messages.")
        if parts:
            items.append({"bot_id": bot, "name": config.BOTS_BY_ID[bot].name, "line": " ".join(parts),
                          "needs": len(d["needs"]) + len(d["failed"]) + len(d["closed"]), "at": d["at"]})
    items.sort(key=lambda i: (-min(i["needs"], 1), -i["at"]))
    return items[:6]


def _clip(text: str, n: int = 70) -> str:
    t = " ".join(str(text or "").split()).rstrip(".")
    return t if len(t) <= n else t[: n - 1].rstrip() + "\u2026"

