""

import asyncio
import json
import logging
import mimetypes
import re
import sqlite3
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import accounts, activity, approvals, apns, board, calllog, choices, erase, looks, config, consent, mailcard, resultcard, textcard, signin, todo, twofactor, health, hermes, history, logins, mac, notices, notifyprefs, onboarding, ondevice, profile, screen, store, team, turns, validate, voice, watcher, who
from . import computerlogins, computers, desk, events, facts, looking, newzip, notice_feedback, project, purchases, receipts, shortreply, teams, telemetry, tones, uploads, usage, wallet, zipfiles
from . import buzz, chatquestions, claudeaccounts, daily, pastedkeys, problems, readmarks, remindermirror, reviewwords, task_context
from . import handoff, passcard, places, usageview
from . import asktitles, catchup, jobwords, ownerrules, quiet, rulereach, workgroups
from . import compactions
from . import work
from . import workmail, worktexts
from . import mail
from . import workstate
from . import chatgate, worknotes
from . import workreply
from . import workprojects
from . import made
from . import meetprep
from . import sessioncard
from . import school
from . import signinclosed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("taffy.hub")
logging.getLogger("httpx").setLevel(logging.WARNING)






BACKGROUND = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    signin.loop = twofactor.loop = asyncio.get_running_loop()
    store.db()
    from . import signin_access
    signin_access.migrate_passwords(logins.list_all())
    consent.clear_relays()
    team.close_interrupted_asks()
    from . import readokays
    readokays.close_pending_reads()
    readokays.close_non_stops()
    from . import service_keys
    service_keys.reword_open_cards()
    from . import noticers
    noticers.close_doubles()
    mac.close_stale_lines()
    telemetry.start()
    if BACKGROUND:
        from . import extract
        extract.ENABLED = voice.HANDOFF_ON = True
        from . import openloops
        openloops.ENABLED = True
        from . import picture
        picture.ENABLED = True
        from . import teamvoice
        teamvoice.ENABLED = True
        from . import passcard
        passcard.ENABLED = True
        from . import doing
        doing.ENABLED = True
        sessioncard.NAMING = True
        quiet.ENABLED = True
        from . import chatgate, worknotes
        import threading as _threads
        _threads.Thread(target=chatgate.backfill, daemon=True).start()
        worknotes.ENABLED = True
        asktitles.ENABLED = True
        jobwords.ENABLED = True
        rulereach.ENABLED = True
        ownerrules.ENABLED = True
        from . import notebooks
        notebooks.ENABLED = True
        watcher.FINISHED_LINES = True
        from . import finishedline
        finishedline.ENABLED = True
        from . import privatewords
        privatewords.ENABLED = True
        import threading
        threading.Thread(target=privatewords.size, daemon=True, name="privatewords").start()
        from . import gotdone
        gotdone.ENABLED = True
        from . import donecard
        donecard.ENABLED = True
        from . import nightly
        nightly.ENABLED = True
        from . import remindertime
        remindertime.ENABLED = True
        reviewwords.ENABLED = True
        from . import plainupdate
        plainupdate.ENABLED = True
        workgroups.ENABLED = True
        from . import todogate
        todogate.ENABLED = True
        from . import bills
        bills.ENABLED = True
        from . import waits
        waits.SETTLE_ENABLED = True
        remindermirror.ENABLED = True
        from . import settled
        settled.ENABLED = True
        from . import stillasks
        stillasks.ENABLED = True
        from . import statuscheck
        statuscheck.ENABLED = True
        from . import wayforward
        wayforward.ENABLED = True
        mac.STARTED = time.time()
        from . import consolidate
        consolidate.ENABLED = True
        catchup.ENABLED = True
        team.FOLLOW_UP = True
        choices.LOOK_AGAIN = True
    tasks = [asyncio.create_task(watcher.run()), asyncio.create_task(team.keep_souls_current()),
             asyncio.create_task(health.run()), asyncio.create_task(accounts.run()), asyncio.create_task(handoff.run()),
             asyncio.create_task(places.recover_owed()),
             asyncio.create_task(calllog.run()), asyncio.create_task(usage.run()), asyncio.create_task(claudeaccounts.run()),
             asyncio.create_task(_watch_orders()), asyncio.create_task(_watch_summaries()),
             asyncio.create_task(_nightly_facts()), asyncio.create_task(_resume_cut_off_turns()),
             asyncio.create_task(_watch_waits()), asyncio.create_task(_watch_texts()),
             asyncio.create_task(teams.run()), asyncio.create_task(_watch_zip_mail()),
             asyncio.create_task(telemetry.run()), asyncio.create_task(modelsetup.run()),
             asyncio.create_task(workmail.run()), asyncio.create_task(workreply.run()), asyncio.create_task(made.run()),
             asyncio.create_task(sessioncard.run()), asyncio.create_task(quiet.run()), asyncio.create_task(worknotes.run()),
             asyncio.create_task(meetprep.run()), asyncio.create_task(school.run()),
             asyncio.create_task(worktexts.run()), asyncio.create_task(_watch_terra()), asyncio.create_task(_watch_code_sessions()),
             asyncio.create_task(_watch_mac_line()), asyncio.create_task(_watch_mac_wakes()), asyncio.create_task(remindermirror.run()),
             asyncio.create_task(daily.run()), asyncio.create_task(rulereach.run()), asyncio.create_task(ownerrules.run()), asyncio.create_task(_keep_notebooks()), asyncio.create_task(asktitles.run()), asyncio.create_task(reviewwords.run()),
             asyncio.create_task(workgroups.run()), asyncio.create_task(_gate_todos()), asyncio.create_task(catchup.run()),
             asyncio.create_task(_redo_finished_lines()), asyncio.create_task(_drop_reminder_twins())] if BACKGROUND else []
    yield
    for t in tasks:
        t.cancel()
    telemetry.stop()


async def _drop_reminder_twins():
    ""



    from . import remindertime
    await asyncio.sleep(60)
    if not remindertime.ENABLED:
        return
    for step in (remindertime.revive, remindertime.settle):
        try:
            await asyncio.to_thread(step)
        except Exception:
            log.exception("reminder upkeep at start failed")
    await remindertime.drop_twins()


async def _resume_cut_off_turns():
    ""
    from . import turnresume
    await turnresume.run()


async def _watch_terra():
    ""
    from . import maccodex
    await maccodex.run()


async def _watch_mac_wakes():
    ""
    from . import macwake
    await macwake.run()


async def _watch_mac_line():
    ""
    from . import macqueue
    await macqueue.run()


async def _watch_code_sessions():
    ""
    from . import codesessions
    await codesessions.run()


async def _watch_waits():
    ""
    from . import waits
    await waits.run()


async def _watch_texts():
    ""
    from . import textwatch
    await textwatch.run()


async def _watch_zip_mail():
    ""
    from . import zipmail
    await zipmail.run()


async def _gate_todos() -> None:
    ""
    from . import todogate
    await todogate.run()


async def _redo_finished_lines() -> None:
    ""
    from . import finishedline
    try:
        await finishedline.redo_old()
    except Exception:
        log.warning("could not rewrite the older finished-task rows", exc_info=True)


async def _watch_orders():
    ""
    from . import extract
    await extract.watch_orders()


async def _nightly_facts():
    ""
    from . import consolidate
    await consolidate.run()


async def _keep_notebooks():
    ""
    from . import notebooks
    await notebooks.run()


async def _watch_summaries():
    ""
    from . import extract
    await extract.watch_summaries()



app = FastAPI(title="Zero hub", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


app.add_middleware(telemetry.UsageMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    ""
    fields = [".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": f"Check these fields: {', '.join(fields) or 'request'}."})





_TESTER_MAY = re.compile(r"^/api/(?:bots/[^/]+/messages|bots/[^/]+/notification-reply|unpair|signin-requests/[^/]+)$")


def require_device(request: Request, authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Pair this device first.")
    name = store.device_for_token(authorization[7:])
    if not name:
        raise HTTPException(401, "This device is not paired.")
    if request.method not in ("GET", "HEAD") and not _TESTER_MAY.match(request.url.path) \
            and store.tester_name(authorization[7:]):
        raise HTTPException(403, "A test pairing can only read, send chat messages and answer test cards.")
    return name


def _tester(authorization: str | None) -> str | None:
    ""
    ok = isinstance(authorization, str) and authorization.startswith("Bearer ")
    return store.tester_name(authorization[7:]) if ok else None


def _chat_tester(authorization: str | None, bot_id: str) -> str | None:
    ""
    named = _tester(authorization)
    if named or config.is_sandbox(bot_id) or not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        return named
    return store.tool_pairing(authorization[7:])


@app.get("/api/usage")
def get_usage(_: str = Depends(require_device)):
    return usageview.snapshot()


@app.get("/api/compactions")
def get_compactions(days: int = Query(3, ge=1, le=30), _: str = Depends(require_device)):
    ""
    return compactions.summary(days)


@app.post("/api/diagnostics")
async def app_diagnostics(request: Request, _: str = Depends(require_device)):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > problems.MAX_PAYLOAD:
            raise HTTPException(413, "The diagnostic payload is too large.")
        raw.extend(chunk)
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) != {"platform", "payload"}:
            raise problems.ProblemError("Give the platform and diagnostic payload only.")
        return await asyncio.to_thread(problems.ingest, body["platform"], body["payload"])
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid MetricKit diagnostic payload.") from None


@app.get("/api/repairs")
def repairs(state: str | None = None, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
            _: str = Depends(require_device)):
    try:
        return {"reports": problems.list_reports(state, after, limit)}
    except problems.ProblemError as e:
        raise HTTPException(422, str(e)) from None


class PairBody(BaseModel):
    code: str
    device_name: str = "iPhone"


    tester: bool = False


_pair_failures: list[float] = []
_pair_lock = __import__("threading").Lock()


@app.post("/api/pair")
def pair(body: PairBody):
    with _pair_lock:
        now = time.time()
        _pair_failures[:] = [t for t in _pair_failures if now - t < 600]
        if len(_pair_failures) >= 10:
            raise HTTPException(429, "Too many wrong codes. Wait ten minutes and try again.")
        token = store.redeem_pairing_code(body.code[:32], body.device_name[:80], tester=body.tester)
        if not token:
            _pair_failures.append(now)
            raise HTTPException(400, "That code is wrong or expired. Make a new one where you got it.")
    return {"token": token}


_new_codes: list[float] = []


@app.post("/api/pair/new-code")
def new_pairing_code(_: str = Depends(require_device)):
    ""

    with _pair_lock:
        now = time.time()
        _new_codes[:] = [t for t in _new_codes if now - t < 3600]
        if len(_new_codes) >= 5:
            raise HTTPException(429, "That's enough codes for now. Try again in an hour.")
        _new_codes.append(now)
    code = store.new_pairing_code(15 * 60)
    from . import pairlookup
    pairlookup.announce_soon(code)
    return {"code": code, "expires_in": 15 * 60}


@app.post("/api/unpair")
def unpair(authorization: str | None = Header(None), _: str = Depends(require_device)):
    store.revoke_device(authorization[7:])
    return {"ok": True}


class PlanCodeBody(BaseModel):
    code: str


@app.post("/api/plan/claude/start")
def plan_claude_start(_: str = Depends(require_device)):
    ""
    from . import plansignin
    try:
        return {"url": plansignin.start()}
    except plansignin.SignInError as e:
        raise HTTPException(409, str(e))


@app.post("/api/plan/claude/code")
def plan_claude_code(body: PlanCodeBody, _: str = Depends(require_device)):
    ""
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
    ""



    from . import deployed
    body = {"ok": True, "time": int(time.time()), "bots": health.status["bots"], "voice": health.status["voice"]}
    commit = deployed.short_commit()
    if commit:
        body["commit"] = commit
    return body


@app.get("/api/daily-checks")
def daily_checks(_: str = Depends(require_device)):
    ""
    return daily.summary()


def _bot(bot_id: str) -> config.Bot:
    bot = config.BOTS_BY_ID.get(bot_id)
    if not bot:
        raise HTTPException(404, "That bot isn't on your team anymore.")
    return bot




_preview_cache: dict[str, tuple[float, str | None, float | None]] = {}
PREVIEW_TTL = 20


async def _last_message(bot_id: str) -> tuple[str | None, float | None]:
    ""
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

    shown = [m for m in shortreply.apply(bot_id, history.visible(msgs)) if m["class"] in ("colin", "bot", "board_reply")]
    if shown:
        m = shown[-1]
        prefix = "You: " if m["class"] == "colin" else ""
        result = (prefix + " ".join(m["display"].split())[:140], m.get("ts"))
    _preview_cache[bot_id] = (time.time(), *result)
    return result


def _card_body(body: str | None) -> bool:
    ""
    try:
        return isinstance(json.loads(body or ""), dict)
    except ValueError:
        return False


def _task_preview(row: dict) -> str | None:
    ""



    try:
        task = board.get_task(row["task_id"]) if row.get("task_id") else None
    except Exception:
        log.warning("could not read task %s for the chats list", row.get("task_id"), exc_info=True)
        return None
    card = (task or {}).get("card")
    waits = "blocked" if row["kind"] == "task_blocked" else "review"
    if not card or task.get("status") != waits:
        return None
    return f"{card.get('ask') or ''} {card.get('does') or ''}".strip() or None


@app.get("/api/bots")
async def bots(every: int = Query(0, alias="all"), _: str = Depends(require_device)):
    ""

    if config.reload_bots():
        from . import zipapps
        asyncio.get_running_loop().create_task(zipapps.announce())


    shown = [b for b in config.BOTS if every or not teams.member_of(b.id)]
    load = await asyncio.to_thread(board.bot_load)
    previews = await asyncio.gather(*[_last_message(b.id) for b in shown])
    asks: dict[str, int] = {}
    pending = await asyncio.to_thread(consent.pending)
    for r in pending:
        asks[r["bot_id"]] = asks.get(r["bot_id"], 0) + 1
    for bid, n in (await asyncio.to_thread(teams.open_needs_by_bot)).items():
        asks[bid] = asks.get(bid, 0) + n
    statuses = await asyncio.to_thread(_bot_statuses, pending)
    hellos = await asyncio.to_thread(config.hellos)
    from . import turnresume
    out = []
    for b, (preview, ts) in zip(shown, previews):
        l = load.get(b.id, {"working": 0, "running": 0, "needs_you": 0})
        f = await asyncio.to_thread(quiet.latest_shown, b.id)
        if f and f["kind"] in ("choices", "login", "account", "code", "mac_ask", "project", "mail", "kit") and '"state": "open"' not in (f["body"] or ""):
            f = None
        if f and f["kind"] == "team":


            f = None
        task_line = await asyncio.to_thread(_task_preview, f) if f and f["kind"] in ("task_blocked", "task_review") else None
        if f and f["kind"] in ("task_blocked", "task_review") and not task_line:
            f = None
        if f and (ts is None or f["created_at"] > ts):
            if f["kind"] == "call":
                try:
                    secs = int(json.loads(f["body"] or "{}").get("duration") or 0)
                except ValueError:
                    secs = 0
                first = f"Voice call, {secs // 60}:{secs % 60:02d}"
            elif task_line:
                first = task_line
            elif f["kind"] in ("mac", "mac_ask"):
                first = f["title"] or "Used your Mac"
            elif f["kind"] == "choices":
                first = f["title"] or "Asked you to pick"
            elif f["kind"] == "kit":
                first = f["title"] or "A card"
            elif f["kind"] == "project":
                first = f"Ready to open: {f['title']}"
            elif f["kind"] == "mail":
                first = mailcard.title_for(json.loads(f["body"] or "{}"))
            elif f["kind"] == textcard.KIND:
                first = f["title"] or "A text to send"
            elif f["kind"] == "result":
                first = resultcard.title_for(json.loads(f["body"] or "{}"))
            elif f["kind"] == "todo_card":
                from . import donecard
                first = donecard.title_for(json.loads(f["body"] or "{}"))
            elif f["kind"] == "login":

                try:
                    card = json.loads(f["body"] or "{}")
                except ValueError:
                    card = {}
                first = ((f["title"] or "This site wants a person to finish a check.")
                         if card.get("purpose") == "challenge" else f"Asked you to {signin.ask_line(card)}")
            elif f["kind"] == "account":
                first = f"Asked you to {(f['title'] or 'connect an account').lower()}"
            elif f["kind"] == "code":
                first = f["title"] or "Asked you for a code"
            elif f["kind"] == "team":
                try:
                    tm = json.loads(f["body"] or "{}")
                except ValueError:
                    tm = {}
                first = f"{f['title']}: {tm.get('text') or ''}" if tm.get("kind") != "answer" else (tm.get("text") or "")
            elif f["kind"] == "image":
                first = "Sent a picture: " + (f["title"] or "")
            elif _card_body(f["body"]):


                first = f["title"] or "A card"
            else:
                first = (f["body"] or "").split("\n\n")[-1].strip() or (f["title"] or "")


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


                    "running": l.get("running", 0),
                    "busy": turns.shows_working(b.id), "preview": preview, "last_at": ts,

                    **({"resuming": True} if turnresume.resuming(b.id) else {}),
                    "hello": hellos.get(b.id),
                    "making": newzip.making(b.id),
                    **({"team": b.team or (teams.member_of(b.id) or {}).get("id")}
                       if b.team or teams.member_of(b.id) else {})})
    return {"bots": out + newzip.pending_bots()}


@app.get("/api/bots/{bot_id}/messages")
async def bot_messages(bot_id: str, after: str | None = Query(None, max_length=200), _: str = Depends(require_device)):
    ""


    if bot_id not in config.BOTS_BY_ID and any(b["id"] == bot_id for b in newzip.pending_bots()):
        return {"messages": [], "busy": newzip.making(bot_id), "reactions": {}}
    _bot(bot_id)
    try:
        msgs = await hermes.messages(bot_id)
    except Exception:
        raise HTTPException(503, "Could not load this conversation right now.")





    from . import saidlines, topics, turnsummary
    rows = topics.expand(turnsummary.apply(bot_id, hermes.mark_backup(
        bot_id, saidlines.weave(bot_id, shortreply.apply(bot_id, history.classify(msgs))))))
    out = {"messages": rows, "busy": turns.shows_working(bot_id), "reactions": store.reactions_for(bot_id)}
    if after is not None:
        part = history.newer(rows, after)
        out.update(messages=rows if part is None else part, partial=part is not None)
    return out


class PushTokenBody(BaseModel):
    token: str
    environment: str = "development"
    platform: str = "iphone"


@app.post("/api/push-token")
def push_token(body: PushTokenBody, authorization: str | None = Header(None), _: str = Depends(require_device)):
    ""
    token = body.token.strip().lower()
    if not (16 <= len(token) <= 200 and all(c in "0123456789abcdef" for c in token)):
        raise HTTPException(400, "That isn't a push token.")
    if body.environment not in apns.HOSTS or body.platform not in ("iphone", "mac"):
        raise HTTPException(400, "Unknown device type.")
    apns.save_token(store._hash(authorization[7:]), token, body.environment, body.platform)
    return {"ok": True}


@app.get("/api/icloud")
def icloud_status(_: str = Depends(require_device)):
    ""

    from . import icloud
    return {"enabled": icloud.switched_on(), "container": icloud.container(), "record_type": icloud.RECORD_TYPE,
            "mac_ready": icloud.can_save()}


class ReactionBody(BaseModel):
    kind: str | None = None
    excerpt: str = ""


@app.post("/api/bots/{bot_id}/messages/{message_id}/reaction")
def react(bot_id: str, message_id: str, body: ReactionBody, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    if body.kind is not None and body.kind not in store.REACTIONS:
        raise HTTPException(400, "That reaction isn't available.")
    if len(message_id) > 64 or message_id.startswith(("local-", "pending-", "feed-")):
        raise HTTPException(400, "That message can't take a reaction yet.")
    store.set_reaction(bot_id, message_id, "colin", body.kind, body.excerpt)
    return {"ok": True}


class ReplyTo(BaseModel):
    text: str
    who: str = "bot"


class SendBody(BaseModel):
    text: str = ""
    attachments: list[str] = []
    reply_to: ReplyTo | None = None


def _sse(turn: turns.Turn):
    async def gen():
        yield f"data: {json.dumps({'type': 'turn', 'id': turn.id})}\n\n"
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
    ""
    _bot(bot_id)

    desk.read_chat(bot_id, looking.key(authorization))
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(400, "Type a message first.")
    if len(text) > 20000:
        raise HTTPException(400, "That message is too long.")
    text, note = pastedkeys.hide(text)
    if body.attachments:
        try:
            text = f"{text or 'Here is what I attached.'}\n\n{uploads.lines(bot_id, body.attachments)}"
        except uploads.UploadError as e:
            raise HTTPException(400, str(e))
    reply_to = None
    if body.reply_to and body.reply_to.text.strip():
        if body.reply_to.who not in ("bot", "person", "other"):
            raise HTTPException(400, "That reply can't be sent.")

        reply_to = {"text": pastedkeys.hide(body.reply_to.text[:20000])[0], "who": body.reply_to.who}
    device = looking.key(authorization)
    looking.saw(device)
    return _sse(turns.send(bot_id, text, reply_to=reply_to, device=device, note=note,
                           tester=_chat_tester(authorization, bot_id)))


@app.post("/api/bots/{bot_id}/uploads")
async def upload(bot_id: str, request: Request, name: str = "", _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    too_big = HTTPException(413, "That file is over 25 MB. Send a smaller one.")
    try:
        stated = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(400, "That upload couldn't be read. Try again.")
    if stated > uploads.MAX_BYTES:
        raise too_big
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > uploads.MAX_BYTES:
            raise too_big
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        return await asyncio.to_thread(uploads.save, bot_id, name, data)
    except uploads.UploadError as e:
        raise HTTPException(400, str(e))


@app.get("/api/bots/{bot_id}/uploads/{upload_id}")
def get_upload(bot_id: str, upload_id: str, thumb: int = 0, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    try:
        p = uploads.path(bot_id, upload_id)
    except uploads.UploadError as e:
        raise HTTPException(404, str(e))
    if thumb and uploads.kind_of(p.name) == "image":
        small = uploads.thumbnail(p)
        if small:
            return Response(small, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
    from fastapi.responses import FileResponse
    return FileResponse(p, filename=p.name, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/bots/{bot_id}/files")
def zip_files(bot_id: str, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    settling: list[str] = []
    files = zipfiles.listing(bot_id, settling)
    return {"files": files, "settling": bool(settling)}


@app.get("/api/bots/{bot_id}/files/{file_id:path}")
def zip_file(bot_id: str, file_id: str, _: str = Depends(require_device)):
    ""

    _bot(bot_id)
    f = zipfiles.open_file(bot_id, file_id)
    if f is None:
        raise HTTPException(404, "That file isn't here anymore.")
    name = zipfiles.display_name(file_id)

    def chunks():
        with f:
            while block := f.read(256 * 1024):
                yield block
    return StreamingResponse(chunks(), media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",

                             headers={"Content-Disposition": f"attachment; filename*=utf-8''{quote(name)}",

                                      "Cache-Control": "private, no-cache"})


@app.get("/api/bots/{bot_id}/turn")
async def follow_turn(bot_id: str, id: str | None = None, _: str = Depends(require_device)):
    ""

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

    items = [it for it in receipts.present(store.activity_for(bot_id))
             if it.get("kind") != mailcard.KIND or not work._json(it.get("body")).get("work_source")]
    said = store.said_at(bot_id)
    for it in items:
        if (aside := notices.aside_cards(it)) is not None:

            it["aside_cards"], it["aside_waiting"] = aside
            continue
        if it.get("kind") == mailcard.KIND:
            it["body"] = json.dumps(mailcard.public(mailcard.settle(it)))
            continue
        if it.get("kind") == textcard.KIND:
            it["body"] = json.dumps(textcard.public(textcard.settle(it)))
            continue
        if it.get("kind") == passcard.KIND:
            passcard.present(it)
            continue
        if it.get("kind") not in signin.ASK_KINDS:
            continue
        try:
            card = json.loads(it.get("body") or "{}")
        except ValueError:
            continue
        if card.get("state") != "open":
            continue


        card["waiting"] = signin.waiting(it, said)
        if card.get("why"):
            card["why"] = who.to_you(card["why"])
        card.pop("session_id", None)
        it["body"] = json.dumps(card)



    return {"items": chatgate.present(quiet.present(worknotes.mark(bot_id, items)))}




@app.get("/api/work")
def work_overview(_: str = Depends(require_device)):
    return work.for_app(work.overview())


class NotTodayBody(BaseModel):
    id: str = Field(min_length=1, max_length=300)


@app.post("/api/work/not-today")
def work_not_today(body: NotTodayBody, _: str = Depends(require_device)):
    ""

    started = workgroups.generation()
    try:
        return {"ok": True, **workgroups.not_today(body.id, work.overview()["needs_you"], started=started)}
    except workgroups.NotTodayError as e:
        raise HTTPException(409, str(e))


@app.get("/api/work/projects/{project_id}")
def work_project(project_id: str, _: str = Depends(require_device)):
    detail = work.project_detail(project_id)
    if detail is None:
        raise HTTPException(404, "That project is no longer here.")
    return detail


@app.get("/api/work/mail/{source_id}")
def work_mail_message(source_id: str, _: str = Depends(require_device)):
    try:
        return workmail.message(source_id)
    except mail.MessageGone as e:
        raise HTTPException(404, str(e)) from None
    except mail.MailError as e:
        raise HTTPException(503, str(e)) from None


class WorkStateBody(BaseModel):
    action: Literal["done", "snooze", "undo", "mark_read"]
    expected_revision: int = Field(ge=0, strict=True)
    source_revision: str = Field(min_length=1, max_length=100)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    snoozed_until: float | None = None


@app.post("/api/work/items/{item_id}/state")
def work_item_state(item_id: str, body: WorkStateBody, _: str = Depends(require_device)):
    try:
        return workstate.change(item_id, **body.model_dump())
    except workstate.StateError as e:
        return JSONResponse(status_code=e.status, content={"detail": str(e), "current": e.current})


class WorkReplyBody(BaseModel):
    action: Literal["edit", "send", "write", "pick", "rewrite", "status"]
    version: int | None = Field(default=None, ge=1, strict=True)
    to: list[str] | None = None
    cc: list[str] | None = None
    subject: str | None = None
    body: str | None = None
    choice: int | None = Field(default=None, ge=0, strict=True)
    change: str | None = Field(default=None, max_length=workreply.CHANGE_MAX)


@app.post("/api/work/items/{item_id}/reply")
async def work_item_reply(item_id: str, body: WorkReplyBody, _: str = Depends(require_device)):
    ""



    edits = body.model_dump(exclude={"action", "version", "choice", "change"}, exclude_none=True)
    try:
        if body.action == "write":
            return {"ok": True, "reply_draft": await workreply.write_now(item_id)}
        if body.action == "status":
            return {"ok": True, "reply_draft": await asyncio.to_thread(workreply.status, item_id)}
        if body.action == "pick":
            return {"ok": True, "reply_draft": workreply.pick(item_id, body.version, body.choice)}
        if body.action == "rewrite":
            return {"ok": True, **await workreply.rewrite(item_id, body.version, body.change or "")}
        if body.action == "edit":
            return {"ok": True, "reply_draft": workreply.edit(item_id, body.version, edits)}
        with store._lock:
            ask, _, _ = workreply._current(item_id, body.version, sending=True)
        in_reply_to = await asyncio.to_thread(workreply.reply_to, ask["reference"])
        bot_id, card, note = workreply.send(item_id, body.version, edits, in_reply_to)
    except workreply.ReplyError as e:
        raise HTTPException(e.status, str(e)) from None
    except mail.MessageGone as e:
        raise HTTPException(404, str(e)) from None
    except mail.MailError as e:
        raise HTTPException(503, str(e)) from None
    with store._lock:
        card_id = int(workreply._row(item_id)["card_id"])
    await _send_tapped_mail(bot_id, card_id, card, note)
    workreply.shown(item_id)
    with store._lock:
        return {"ok": True, "reply_draft": workreply.public(workreply._ask(item_id) or ask)}


class WorkProjectStateBody(BaseModel):
    action: Literal[workprojects.ACTIONS]
    expected_revision: int | None = Field(default=None, ge=0, strict=True)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    title: str | None = Field(default=None, max_length=200)
    area: str | None = Field(default=None, max_length=80)
    target_id: str | None = Field(default=None, max_length=100)
    rank: float | None = Field(default=None, allow_inf_nan=False)
    undo_operation_id: str | None = Field(default=None, max_length=128)


@app.post("/api/work/projects/{project_id}/state")
def work_project_state(project_id: str, body: WorkProjectStateBody, _: str = Depends(require_device)):
    try:
        return workprojects.change(project_id, **body.model_dump())
    except workprojects.ProjectError as e:
        return JSONResponse(status_code=e.status, content={"detail": str(e), "current": e.current})


_work_refresh_task: asyncio.Task | None = None


async def _refresh_work_sources() -> None:
    ""
    try:
        await worktexts.refresh()
    except Exception:
        pass
    await workmail.refresh()


@app.post("/api/work/refresh")
async def refresh_work(_: str = Depends(require_device)):
    global _work_refresh_task
    result = workstate.reserve_refresh()
    if result["accepted"] and (_work_refresh_task is None or _work_refresh_task.done()):
        _work_refresh_task = asyncio.create_task(_refresh_work_sources())
    return {**result, "accounts": workmail.freshness()}


class DriveBody(BaseModel):
    seconds: int = Field(ge=60, le=6 * 3600, strict=True)


@app.post("/api/work/meetings/{source_id}/drive")
def work_meeting_drive(source_id: str, body: DriveBody, _: str = Depends(require_device)):
    ""
    try:
        return meetprep.report_drive(source_id, body.seconds)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None


@app.get("/api/work/made")
def made_items(project_id: str | None = None, kind: Literal["page", "file", "draft"] | None = None,
               q: str = Query(default="", max_length=200), limit: int = Query(default=60, ge=1, le=100),
               cursor: str = Query(default="", max_length=256), group: Literal["project", "shelf"] | None = None,
               shelf: Literal["pages", "videos", "pictures", "drafts", "files", "reports"] | None = None,
               _: str = Depends(require_device)):
    work.rebuild()
    if group == "shelf":
        return made.by_shelf(project_id, q, limit)
    if group == "project" and not project_id:
        return made.by_project(kind, q, limit)
    try:
        return made.listing(project_id, kind, q, limit, cursor, shelf)
    except made.CursorError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/work/made/{item_id}")
def made_item(item_id: str, _: str = Depends(require_device)):
    work.rebuild()
    item = made.get(item_id)
    if item is None:
        raise HTTPException(404, "That item is no longer here.")
    return item


@app.get("/api/work/made/{item_id}/thumbnail")
def made_thumbnail(item_id: str, _: str = Depends(require_device)):
    ""
    found = made.thumbnail(item_id)
    if found is None:
        raise HTTPException(404, "That picture isn't there anymore.")
    return _file_response(found[0], None, found[1], {"Cache-Control": "private, max-age=86400"})


def _board_payload() -> dict:
    from . import waits
    return {**board.grouped(), "requests": _signin_requests(), "waiting_on": waits.for_app()}


@app.get("/api/board")
async def get_board(bot: str | None = None, _: str = Depends(require_device)):
    ""


    from . import doing
    if bot is not None:
        _bot(bot)
        working = (await asyncio.to_thread(board.grouped))["working"]
        await doing.settle(doing.attach(working))
        mine = [t for t in doing.attach(working)
                if (t["assignee"] or "default") == bot and t["status"] in board.ACTIVE]
        return {"running": [t for t in mine if t["status"] == "running"],
                "queued": [t for t in mine if t["status"] != "running"]}
    out = await asyncio.to_thread(_board_payload)
    out["working"] = doing.attach(out["working"])
    await doing.settle(out["working"])
    out["working"] = doing.attach(out["working"])
    return out


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, _: str = Depends(require_device)):
    t = board.get_task(validate.task_id(task_id))
    if not t:
        raise HTTPException(404, "That task isn't there anymore.")
    return t


def _file_response(f, filename: str | None, media_type: str | None = None, headers: dict | None = None):
    ""

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
        raise HTTPException(409, "That request already moved on."
                            if moved else "The board did not accept that. Try again in a moment.")
    return {"ok": True}


def _task_pass(task_id: str, note: str | None) -> None:
    ""

    if note and note.strip():
        consent.open_pass("button", note.strip(), task_id=task_id)


@app.post("/api/tasks/{task_id}/approve")
def approve(task_id: str, body: NoteBody, _: str = Depends(require_device)):
    tid = validate.task_id(task_id)
    from . import reviewcard
    return _act(reviewcard.approve(tid, validate.short(body.note, 4000, "note")))


@app.post("/api/tasks/{task_id}/changes")
def changes(task_id: str, body: NoteBody, _: str = Depends(require_device)):
    if not body.note:
        raise HTTPException(400, "Say what to change.")
    tid = validate.task_id(task_id)
    note = validate.short(body.note, 4000, "note")
    _task_pass(tid, note)
    task = board.get_task(tid) or {"id": tid}
    out = _act(board.request_changes(tid, note))
    from . import reviewcard
    reviewcard.answered_line(task, "Changes asked")
    return out


@app.post("/api/tasks/{task_id}/decline")
def decline(task_id: str, _: str = Depends(require_device)):
    ""

    tid = validate.task_id(task_id)
    from . import reviewcard
    task = board.get_task(tid) or {"id": tid}
    unsent = (task.get("card") or {}).get("no") == reviewcard.DONT_SEND
    out = _act(board.decline(tid, unsent))
    reviewcard.answered_line(task, "Not sent" if unsent else "Not needed")
    return out


@app.post("/api/tasks/{task_id}/answer")
def answer(task_id: str, body: NoteBody, _: str = Depends(require_device)):
    if not body.note:
        raise HTTPException(400, "Type an answer.")
    tid = validate.task_id(task_id)
    note = validate.short(body.note, 4000, "answer")
    _task_pass(tid, note)
    from . import reviewcard
    try:
        task = board.get_task(tid) or {"id": tid}
    except sqlite3.Error:
        log.warning("could not read the card answered on %s", tid, exc_info=True)
        task = {"id": tid}
    out = _act(board.answer_block(tid, reviewcard.answer_note(task, note)))
    reviewcard.answered_words(task, note)
    return out


@app.post("/api/tasks/{task_id}/done")
def done(task_id: str, _: str = Depends(require_device)):
    ""
    return _act(board.mark_done(validate.task_id(task_id), None))




@app.get("/api/checkins")
def checkins(_: str = Depends(require_device)):
    return {"items": store.checkins()}


class SettingsBody(BaseModel):
    muted_bots: list[str] | None = None
    save_call_transcripts: bool | None = None
    keep_replies_short: bool | None = None
    share_usage: bool | None = None


def _settings() -> dict:


    sharing = telemetry.sharing()
    return {"muted_bots": store.get_setting("muted_bots", []), "save_call_transcripts": calllog.enabled(),
            "keep_replies_short": shortreply.enabled(), "share_usage": sharing,
            "telemetry_id": telemetry.install_id() if sharing else None, "home_kind": telemetry.home_kind(),
            "owner_home": telemetry.owner_home()}


@app.get("/api/settings")
def get_settings(_: str = Depends(require_device)):
    return _settings()


@app.put("/api/settings")
def put_settings(body: SettingsBody, _: str = Depends(require_device)):
    ""
    if body.muted_bots is not None:
        store.set_setting("muted_bots", [b for b in body.muted_bots if b in config.BOTS_BY_ID])
    if body.save_call_transcripts is not None:
        calllog.set_enabled(body.save_call_transcripts)
    if body.keep_replies_short is not None:
        shortreply.set_enabled(body.keep_replies_short)
    if body.share_usage is not None:
        telemetry.set_sharing(body.share_usage)
    return _settings()


@app.get("/api/feed")
def feed(after: int = 0, changed_after: float = 0, idle: float | None = None, front: int | None = None,
         chat: str | None = None, authorization: str | None = Header(None), _: str = Depends(require_device)):
    ""




    device = looking.key(authorization)
    looking.saw(device, idle)
    looking.showing(device, None if front is None else bool(front), chat)
    desk.polled(device, None if front is None else bool(front), idle)
    items, more = store.feed_since(after)
    now = time.time()
    changed = store.feed_changed_bots(changed_after) if changed_after else []
    busy = turns.busy_bots()
    shown = busy + [b for b in activity.retrying_ids() if b not in busy]
    return {"items": items, "has_more": more, "max_id": store.max_feed_id(), "busy": shown + newzip.busy_ids(),
            "changed": changed, "time": now, "computers": _live_computers(set(busy)), "pinging": _pinging(),
            "sessions": _session_cards(), "todo": _todo_list(), "reads": _read_marks(changed_after),
            "activity": {**newzip.live(now), **activity.all_live(list(config.BOTS_BY_ID), now)},
            "queued": turns.queued_counts(), "running": _running_tasks(changed_after, now)}







LINE_CHANGED = ""


def _running_tasks(changed_after: float = 0, now: float | None = None) -> list[str] | None:
    ""




    from . import doing
    try:
        running = sorted(b for b, d in board.bot_load().items() if d["running"])
    except Exception:
        log.warning("could not read the running tasks", exc_info=True)
        return None
    if running and changed_after and doing.changed_since(changed_after, now):
        running.append(LINE_CHANGED)
    return running


def _live_computers(busy: set[str]) -> dict[str, str]:
    ""

    try:
        return screen.live_states(busy)
    except Exception:
        log.warning("could not read the computers", exc_info=True)
        return {}


def _session_cards() -> dict[str, dict] | None:
    ""

    try:
        return sessioncard.active()
    except Exception:
        log.warning("could not read the session cards", exc_info=True)
        return None


def _todo_list() -> dict | None:
    ""
    try:
        todo.sync()
        return todo.view()
    except Exception:
        log.warning("could not read the to-do list", exc_info=True)
        return None


def _read_marks(after: float) -> dict[str, float] | None:
    ""
    try:
        return readmarks.changed(after)
    except Exception:
        log.warning("could not read the read marks", exc_info=True)
        return None


def _pinging() -> list[dict]:
    try:
        return team.pinging()
    except Exception:
        log.warning("could not read open team questions", exc_info=True)
        return []




app.mount("/screen", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="screen")


@app.get("/api/bots/{bot_id}/screen")
def screen_status(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    return screen.status(bot_id)


@app.get("/api/bots/{bot_id}/screen/snapshot")
def screen_snapshot(bot_id: str, _: str = Depends(require_device)):
    ""
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


@app.post("/api/bots/{bot_id}/screen/restart")
def screen_restart(bot_id: str, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    ok, out = screen.restart(bot_id)
    if not ok:
        log.info("screen restart failed: %s", out[-300:])
        raise HTTPException(409, "The computer would not restart. Try again in a moment.")
    return screen.status(bot_id)


@app.post("/api/bots/{bot_id}/screen/control")
def screen_control(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    return screen.take_control(bot_id)


@app.post("/api/bots/{bot_id}/screen/release")
def screen_release(bot_id: str, _: str = Depends(require_device)):
    ""

    _bot(bot_id)
    signinclosed.note_closed(bot_id)
    out = screen.hand_back(bot_id)
    signinclosed.released(bot_id)
    return out


@app.post("/api/bots/{bot_id}/screen/clipboard")
def screen_clipboard(bot_id: str, response: Response, _: str = Depends(require_device)):
    _bot(bot_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"text": screen.copy_text(bot_id)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(409, "Copy did not finish. Try again.")


class UseComputerLogin(BaseModel):
    choice: str


@app.get("/api/bots/{bot_id}/screen/logins")
def computer_logins(bot_id: str, response: Response, _: str = Depends(require_device)):
    _bot(bot_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return computerlogins.request(bot_id, "list")
    except computerlogins.NotReady as exc:
        raise HTTPException(exc.status, str(exc))


@app.post("/api/bots/{bot_id}/screen/logins/use")
def use_computer_login(bot_id: str, body: UseComputerLogin, response: Response, _: str = Depends(require_device)):
    _bot(bot_id)
    response.headers["Cache-Control"] = "no-store"
    if not body.choice or len(body.choice) > 128:
        raise HTTPException(400, "Choose a saved login first.")
    try:
        return computerlogins.request(bot_id, "fill", body.choice)
    except computerlogins.NotReady as exc:
        raise HTTPException(exc.status, str(exc))


@app.post("/api/bots/{bot_id}/screen/ticket")
def screen_ticket(bot_id: str, control: bool = False, _: str = Depends(require_device)):
    ""
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




class LoginBody(BaseModel):
    bots: list[str]
    label: str
    url: str
    identifier: str
    identifier_type: str = "email"
    password: str
    otp_secret: str | None = None
    task_id: str | None = None
    request_id: int | None = None
    access: str | None = None


@app.get("/api/logins")
def list_logins(_: str = Depends(require_device)):
    return {"logins": logins.list_all()}


class SignInAudience(BaseModel):
    mode: str
    zips: list[str] = []


class SignInAccessBody(BaseModel):
    site: str
    access: SignInAudience


class SignInSharingBody(BaseModel):
    share_new_signins: bool


@app.get("/api/sign-ins")
def list_sign_ins(_: str = Depends(require_device)):
    from . import signin_access
    return signin_access.page()


@app.put("/api/sign-ins/access")
def change_sign_in_access(body: SignInAccessBody, _: str = Depends(require_device)):
    from . import signin_access
    try:
        site = consent.base(consent.site_of(validate.secure_url(body.site if "://" in body.site else "https://" + body.site)))
        signin_access.set_access(site, body.access.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    signin_access.reconcile(site)
    return signin_access.page()


@app.put("/api/sign-ins/sharing/{bot_id}")
def change_sign_in_sharing(bot_id: str, body: SignInSharingBody, _: str = Depends(require_device)):
    from . import signin_access
    try:
        signin_access.set_sharing(bot_id, body.share_new_signins)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return signin_access.page()


class SignInUseOnBody(BaseModel):
    site: str
    use_on: str


class SignInNoteBody(BaseModel):
    site: str
    text: str


def _sign_in_site(raw: str) -> str:
    try:
        return consent.base(consent.site_of(validate.secure_url(raw if "://" in raw else "https://" + raw)))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/sign-ins/use-on")
def change_sign_in_use_on(body: SignInUseOnBody, _: str = Depends(require_device)):
    ""
    from . import signin_access, signin_notes
    try:
        signin_notes.set_use_on(_sign_in_site(body.site), body.use_on)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return signin_access.page()


@app.post("/api/sign-ins/notes")
def add_sign_in_note(body: SignInNoteBody, _: str = Depends(require_device)):
    from . import signin_access, signin_notes
    try:
        signin_notes.add(_sign_in_site(body.site), signin_notes.PERSON, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return signin_access.page()


@app.delete("/api/sign-ins/notes/{note_id}")
def delete_sign_in_note(note_id: int, _: str = Depends(require_device)):
    from . import signin_access, signin_notes
    signin_notes.delete(note_id)
    return signin_access.page()


@app.delete("/api/sign-ins")
def remove_sign_in(site: str, _: str = Depends(require_device)):
    from . import signin_access
    failed = signin_access.remove_site(site)
    if failed:
        raise HTTPException(409, "Access is removed. Some saved passwords could not be deleted. Try Remove Sign-in again.")
    return signin_access.page()


@app.post("/api/logins")
def save_login(body: LoginBody, _: str = Depends(require_device)):
    saved, _failed, warning, resumed = _save_login(body)
    return {"saved": saved, "warning": warning, "resumed": resumed}


def _save_login(body: LoginBody) -> tuple[list[dict], list[str], str | None, list[str]]:
    from . import signin_access
    with signin_access.lock:
        return _save_login_locked(body)


def _save_login_locked(body: LoginBody) -> tuple[list[dict], list[str], str | None, list[str]]:
    ""

    bots = [b for b in body.bots if b in config.BOTS_BY_ID]
    from . import signin_access
    chosen = None
    if body.access is not None:
        try:
            chosen = signin_access.validate_access({"mode": body.access, "zips": body.bots})
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if chosen and chosen["mode"] == "all":
        bots = list(config.BOTS_BY_ID)
    if not bots:
        raise HTTPException(400, "Pick at least one bot.")
    url = validate.secure_url(body.url)
    task_id = validate.task_id(body.task_id) if body.task_id else None
    label = validate.short(body.label, 120, "label") or url
    identifier = validate.short(body.identifier, 320, "username")
    if not identifier or not body.password.strip() or len(body.password) > 1024:
        raise HTTPException(400, "Enter the username and password.")


    if (task_id or body.request_id) and len(identifier.split()) > 1:
        raise HTTPException(400, "That username has a space in it. Check it, or close this and tap Not Now.")
    try:
        saved, failed = logins.save_reporting(bots, label, url, identifier, body.identifier_type, body.password,
                                              (body.otp_secret or "").strip() or None)
    except logins.SaveFailed as e:
        names = ", ".join(config.BOTS_BY_ID[b].name if b in config.BOTS_BY_ID else b for b in e.stuck)
        raise HTTPException(400, f"That login could not be saved, and part of it is left with {names}. Remove it "
                                 "there and try again.")
    except Exception as e:
        log.info("login save failed: %s", type(e).__name__)
        raise HTTPException(400, "That login could not be saved. Check the website and the 2FA key.")
    warning = None
    resumed: list[str] = []
    if chosen:
        signin_access.set_access(url, chosen)
    else:
        previous = signin_access._row(url)
        if previous is None or previous["removed"]:
            signin_access.set_access(url, {"mode": "selected", "zips": bots})
        elif previous["mode"] == "selected":
            signin_access.set_access(url, {"mode": "selected", "zips": sorted(set(json.loads(previous["zips"])) | set(bots))})
    signin.share_saved(url, exclude=body.request_id)
    if body.request_id:

        owner = signin.card_owner(body.request_id)
        try:
            signin.resolve(owner if owner in bots else bots[0], body.request_id, "saved")
        except signin.AskError:
            warning = "The login is saved, but that request had already moved on."
    if not body.task_id and not body.request_id:

        site = consent.site_of(url)
        for tid in board.waiting_signins(site, bots):
            t = board.get_task(tid) or {}
            consent.allow_now(t.get("assignee") or "default", url, "You saved a login it was waiting for", tid)
            ok, _ = board.answer_block(tid, f"{who.first()} saved a login for this site in your vault. Sign in "
                                            "with your browser vault tools (they fill the password without showing it) and continue.")
            if ok:
                resumed.append(tid)
    if body.task_id:

        for b in bots:
            consent.allow_now(b, url, "You saved a login for this request", task_id)
        ok, _ = board.answer_block(task_id,
                                   f"{who.first()} saved a login for this site in your vault. Sign in with your "
                                   "browser vault tools (they fill the password without showing it) and continue.")
        if not ok:
            warning = "The login is saved, but the request had already moved on."
    return saved, failed, warning, resumed


@app.delete("/api/logins/{bot_id}/{item_id}")
def delete_login(bot_id: str, item_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    return {"removed": logins.remove(bot_id, item_id[:64])}




class WalletAddBody(BaseModel):
    kind: str
    label: str | None = None
    zips: list[str] | None = None
    meta: dict = {}
    secret: dict
    limits: dict | None = None


    task_id: str | None = None
    signin_request_id: int | None = None
    identifier_type: str | None = None
    login_access: str | None = None


class WalletPatchBody(BaseModel):
    label: str | None = None
    zips: list[str] | None = None
    limits: dict | None = None
    paused: bool | None = None


class WalletPushBody(BaseModel):
    secret: dict
    zips: list[str]


class YourInfoBody(BaseModel):
    field: str
    value: str | dict | None = None
    source: str = "you"

    fill_only: bool = False


@app.get("/api/your-info")
async def your_info(_: str = Depends(require_device)):
    ""

    from . import yourinfo
    await yourinfo.ready()
    return await asyncio.to_thread(yourinfo.page)


@app.put("/api/your-info")
def save_your_info(body: YourInfoBody, _: str = Depends(require_device)):
    from . import yourinfo
    try:
        yourinfo.set_field(body.field, body.value, "scan" if body.source == "scan" else "you", fill_only=body.fill_only)
    except yourinfo.InfoError as e:
        raise HTTPException(400, str(e))
    return yourinfo.page()


class WalletRequestBody(BaseModel):
    kind: str
    label: str | None = None
    meta: dict = {}
    secret: dict
    zips: list[str] | None = None


class PurchaseDecisionBody(BaseModel):
    decision: str


def _wallet_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except wallet.NeedsSecret as e:
        return JSONResponse({"needs_secret": True, "detail": str(e)}, status_code=409)
    except wallet.NotFound as e:
        raise HTTPException(404, str(e))
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    except purchases.NotFound as e:
        raise HTTPException(404, str(e))
    except purchases.Refused as e:
        raise HTTPException(409, str(e))


@app.get("/api/wallet")
def list_wallet(_: str = Depends(require_device)):
    return _wallet_call(lambda: {"items": wallet.list_items()})


@app.post("/api/wallet")
def add_wallet_item(body: WalletAddBody, _: str = Depends(require_device)):
    ""
    if body.kind not in wallet.KINDS:
        raise HTTPException(400, "That kind of item can't be saved here.")
    if body.kind == "login":
        return _wallet_login(body)
    out = _wallet_call(wallet.add, body.kind, body.secret, body.label, body.zips, body.limits)
    failed = out.pop("failed", [])
    return {"item": out, "failed": failed}


def _wallet_login(body: WalletAddBody) -> dict:
    ""
    s = body.secret or {}
    username = str(s.get("username") or "")
    id_type = body.identifier_type or str(s.get("identifier_type") or "") or wallet.identifier_type(username)
    saved, failed, warning, resumed = _save_login(LoginBody(
        bots=body.zips if body.zips is not None else [b.id for b in config.BOTS], label=body.label or "",
        url=str(s.get("origin") or ""), identifier=username, identifier_type=id_type,
        password=str(s.get("password") or ""), otp_secret=s.get("otp_secret") or None, task_id=body.task_id,
        request_id=body.signin_request_id, access=body.login_access))
    item = wallet.login_item(saved[0]["origin"], username) if saved else None
    if item is None:
        raise HTTPException(400, "That login could not be saved. Try again in a minute.")
    return {"item": item, "failed": failed, "warning": warning, "resumed": resumed}


@app.patch("/api/wallet/{item_id}")
def change_wallet_item(item_id: str, body: WalletPatchBody, _: str = Depends(require_device)):
    return _wallet_call(wallet.update, item_id[:64], label=body.label, zips=body.zips, limits=body.limits,
                        paused=body.paused)


@app.post("/api/wallet/{item_id}/push")
def push_wallet_item(item_id: str, body: WalletPushBody, _: str = Depends(require_device)):
    ""

    out = _wallet_call(wallet.push, item_id[:64], body.secret, body.zips)
    item = {k: v for k, v in out.items() if k not in ("removed", "failed")}
    return {**out, "item": item}


@app.delete("/api/wallet/{item_id}")
def delete_wallet_item(item_id: str, _: str = Depends(require_device)):
    return _wallet_call(wallet.delete, item_id[:64])


@app.post("/api/wallet/requests/{request_id}")
async def answer_wallet_request(request_id: str, body: WalletRequestBody, _: str = Depends(require_device)):
    ""
    if body.kind not in wallet.KINDS:
        raise HTTPException(400, "That kind of item can't be saved here.")
    out = await asyncio.to_thread(_wallet_call, wallet.answer_request, request_id[:24], body.kind, body.secret,
                                  body.label, body.zips)
    if isinstance(out, JSONResponse):
        return out
    item, bot_id, note, session_id = out
    asyncio.create_task(turns.follow_up(bot_id, note, session_id, urgent=True))
    failed = item.pop("failed", []) if item else []
    return {"item": item, "failed": failed}


@app.post("/api/wallet/requests/{request_id}/dismiss")
async def dismiss_wallet_request(request_id: str, _: str = Depends(require_device)):
    bot_id, note, session_id = _wallet_call(wallet.dismiss_request, request_id[:24])
    asyncio.create_task(turns.follow_up(bot_id, note, session_id, urgent=True))
    return {"ok": True}


@app.post("/api/wallet/purchases/{purchase_id}/decision")
async def decide_purchase(purchase_id: str, body: PurchaseDecisionBody, _: str = Depends(require_device)):
    ""
    out = await asyncio.to_thread(_wallet_call, purchases.decide, purchase_id[:24], body.decision)
    asyncio.create_task(turns.follow_up(out["bot"], out["note"], out["session_id"], urgent=True))
    return {"ok": True, "state": out["purchase"]["state"]}


@app.post("/api/wallet/purchases/{purchase_id}/not-me")
def purchase_not_me(purchase_id: str, _: str = Depends(require_device)):
    return _wallet_call(purchases.not_me, purchase_id[:24])


class SignInBody(BaseModel):
    url: str


@app.post("/api/bots/{bot_id}/signin")
def start_signin(bot_id: str, body: SignInBody, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    began = time.time()
    url = validate.secure_url(body.url)
    if not screen.status(bot_id)["running"]:
        ok, out = screen.start(bot_id)
        if not ok:
            log.info("screen start failed: %s", out[-300:])
            raise HTTPException(409, "The computer would not wake up. Try again in a moment.")
    try:
        tab = logins.open_in_bot_browser(bot_id, url)
    except Exception:
        log.exception("could not open sign-in page")
        raise HTTPException(500, "Could not open that site on the bot's computer.")
    signinclosed.opened(bot_id, url, tab)
    screen.take_control(bot_id)
    if signinclosed.closed_since(bot_id, began):



        signinclosed.forget(bot_id)
        screen.hand_back(bot_id)
        raise HTTPException(409, "That sign-in window was closed. Tap Sign In to open it again.")
    t = screen.mint_ticket(bot_id, True)
    return {"url": f"/screen/screen.html?ticket={t}&control=1"}


class CardLoginBody(BaseModel):
    username: str
    password: str
    use_on: str = "anywhere"


@app.post("/api/bots/{bot_id}/signin/{request_id}/login")
def card_sign_in(bot_id: str, request_id: int, body: CardLoginBody, _: str = Depends(require_device)):
    ""
    from . import signin_card, signin_notes
    _bot(bot_id)
    try:
        card = signin_card.check(bot_id, request_id)
    except signin.AskError as e:
        raise HTTPException(409, str(e))
    if body.use_on not in signin_notes.USE_ON:
        raise HTTPException(400, "Choose Use Anywhere or Only Your Mac.")
    url = str(card.get("url") or "")
    site = consent.site_of(url)
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(400, "Enter the username and password.")
    previous_use = signin_notes.use_on(site)
    try:

        signin_notes.set_use_on(site, body.use_on, source=bot_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    previous = card.get("progress")
    signin_card.set_progress(bot_id, request_id, "signing_in")
    try:
        _save_login(LoginBody(bots=[bot_id], label=consent.base(site) or site, url=url, identifier=username,
                              identifier_type=wallet.identifier_type(username), password=body.password,
                              request_id=request_id))
    except Exception:
        try:
            signin_card.set_progress(bot_id, request_id, previous or "", state="open")
            signin_notes.set_use_on(site, previous_use, source=bot_id)
        except Exception:
            log.warning("could not put a sign-in card back", exc_info=True)
        raise
    signin_card.start_watch(bot_id, request_id, site)
    return {"progress": signin.card_for(bot_id, request_id).get("progress")}


class ImportedLogin(BaseModel):
    site: str
    username: str
    password: str
    use_on: str = "anywhere"


class ImportLoginsBody(BaseModel):
    logins: list[ImportedLogin]


@app.post("/api/logins/import")
def import_logins(body: ImportLoginsBody, _: str = Depends(require_device)):
    ""





    from . import signin_access, signin_card, signin_notes
    if not body.logins or len(body.logins) > 200:
        raise HTTPException(400, "Choose between 1 and 200 logins.")
    results = []
    for item in body.logins:
        try:
            raw = item.site.strip()
            host = urlsplit(raw).hostname if "://" in raw else raw.split("/")[0]
            if not host or " " in host or "." not in host:
                raise ValueError("no host")
            url = validate.secure_url("https://" + host)
            site = consent.site_of(url)
        except (ValueError, HTTPException):
            results.append({"site": item.site[:200], "ok": False, "error": "That website does not look right."})
            continue
        name = consent.base(site) or site
        if item.use_on not in signin_notes.USE_ON:
            results.append({"site": name, "ok": False, "error": "Choose Use Anywhere or Only Your Mac."})
            continue
        row = signin_access._row(url)
        if row is not None and not row["removed"]:
            access = "all" if row["mode"] == "all" else None
            bots = list(config.BOTS_BY_ID) if access else json.loads(row["zips"] or "[]")
        else:
            access, bots = "all", list(config.BOTS_BY_ID)
        try:

            kept_key = logins.kept_otp_secret(bots, url, item.username.strip())
        except Exception as exc:
            log.info("could not read the saved 2FA key for %s (%s)", name, type(exc).__name__)
            results.append({"site": name, "ok": False, "error": "That login could not be saved. Try again in a minute."})
            continue
        cards = signin.open_cards_for(site)
        previous_use = signin_notes.use_on(site)
        try:
            if item.use_on != previous_use:
                signin_notes.set_use_on(site, item.use_on, source=bots[0] if bots else None)
            _save_login(LoginBody(bots=bots, label=name, url=url, identifier=item.username.strip(),
                                  identifier_type=wallet.identifier_type(item.username.strip()),
                                  password=item.password, otp_secret=kept_key, access=access))
        except (HTTPException, ValueError) as exc:
            try:
                signin_notes.set_use_on(site, previous_use, source=bots[0] if bots else None)
            except Exception:
                log.warning("could not put back where %s may be used", name, exc_info=True)
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            results.append({"site": name, "ok": False, "error": str(detail)})
            continue
        for bot_id, card_id in cards:
            try:
                card = signin.card_for(bot_id, card_id)
            except signin.AskError:
                continue
            if card.get("state") != "saved":
                continue
            if card.get("place") == "mac":
                signin_card.set_progress(bot_id, card_id, "saved_mac")
            else:
                signin_card.set_progress(bot_id, card_id, "signing_in")
                signin_card.start_watch(bot_id, card_id, site)
        audience = signin_access.audience(signin_access._row(url))
        results.append({"site": name, "ok": True, "error": None,
                        "zips": ["all"] if audience["mode"] == "all" else audience["zips"]})
    return {"results": results}


@app.post("/api/bots/{bot_id}/signin/{request_id}/not-now")
def card_not_now(bot_id: str, request_id: int, _: str = Depends(require_device)):
    ""

    from . import signin_card
    _bot(bot_id)
    try:
        signin_card.check(bot_id, request_id)
        signin.resolve(bot_id, request_id, "declined")
    except signin.AskError as e:
        raise HTTPException(409, str(e))
    signin_card.set_progress(bot_id, request_id, "not_now")
    return {"progress": "not_now"}


class SignedInBody(BaseModel):
    task_id: str | None = None
    request_id: int | None = None
    site: str | None = None
    declined: bool = False


@app.post("/api/bots/{bot_id}/signed-in")
def finish_signin(bot_id: str, body: SignedInBody, _: str = Depends(require_device)):
    _bot(bot_id)
    card = None
    if body.request_id is not None:
        try:
            card = signin.card_for(bot_id, body.request_id)
        except signin.AskError as e:
            raise HTTPException(409, str(e))
        if card.get("purpose") == "challenge" and body.task_id is not None:
            raise HTTPException(400, "Answer this check from its chat card.")
    signinclosed.note_closed(bot_id)
    screen.hand_back(bot_id)
    tid = validate.task_id(body.task_id) if body.task_id else None
    site = validate.short(body.site, 200, "site") or ""
    if body.declined and (card is None or card.get("purpose", "signin") in signin.ASKED):




        signinclosed.start(bot_id, site, task_id=tid, request_id=body.request_id)
        return screen.status(bot_id)
    signinclosed.forget(bot_id)
    if body.declined:
        try:
            signin.resolve(bot_id, body.request_id, "declined")
        except signin.AskError as e:
            raise HTTPException(409, str(e))
        return screen.status(bot_id)
    try:
        signinclosed.signed_in(bot_id, site, tid, body.request_id)
    except signin.AskError as e:
        raise HTTPException(409, str(e))
    return screen.status(bot_id)


@app.post("/api/tasks/{task_id}/not-now")
def signin_not_now(task_id: str, body: NoteBody, _: str = Depends(require_device)):
    ""

    tid = validate.task_id(task_id)
    site = consent.site_of(validate.short(body.note, 200, "site") or "")
    if not site:
        raise HTTPException(400, "Choose a site.")
    first = who.first()
    consent.deny_now((board.get_task(tid) or {}).get("assignee") or "default", site, f"{first} said not now", tid)
    return _act(board.answer_block(tid, f"{first} said not now to signing in to {site}. No login was saved and you may "
                                        f"not sign in there. Finish what you can without it and say in a sentence what "
                                        f"you could not check. Don't ask for this sign-in again unless {first} asks."))


class AppErrorBody(BaseModel):
    screen: str
    message: str
    detail: str = ""
    bot_id: str | None = None


_app_errors: list[float] = []


@app.post("/api/app-errors")
def app_error(body: AppErrorBody, device: str = Depends(require_device)):
    ""

    now = time.time()
    _app_errors[:] = [t for t in _app_errors if now - t < 60]
    if len(_app_errors) >= 30:
        return {"ok": False}
    _app_errors.append(now)

    def clean(s, n):
        return re.sub(r"\?\S*", "", " ".join(str(s or "").split()))[:n]
    log.warning("app error on %s for %s (%s): %s [%s]", clean(body.screen, 40), clean(body.bot_id, 40) or "-",
                clean(device, 80), clean(body.message, 300), clean(body.detail, 300) or "no detail")
    return {"ok": True}




def stray_session(bot_id: str, session_id: str | None) -> bool:
    ""


    if not session_id or not store.session_for(bot_id):
        return False
    rows = store.db().execute("SELECT session_id FROM bot_sessions WHERE bot_id = ? OR bot_id LIKE ?",
                              (bot_id, f"{bot_id}#%")).fetchall()
    return session_id not in {row["session_id"] for row in rows}


def card_origin(r: dict, why: str) -> str | None:
    ""

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
        return None
    if r.get("session_id") and r["session_id"] == store.session_for(r.get("bot_id") or ""):


        return ""
    if stray_session(r.get("bot_id") or "", r.get("session_id")):
        return f"This came up in a run outside the app, not in your chat with {name}."
    return f"This came up outside your chat with {name}."


def _signin_requests() -> list[dict]:
    out = []
    for r in consent.pending():
        if r["bot_id"] in config.BOTS_BY_ID:
            kind = r.get("kind") or "signin"
            context = task_context.get(r.get("task_id"))
            why = r.get("why") or ("background" if not r["reason"] else "")
            item = {"id": r["id"], "bot_id": r["bot_id"], "site": r["site"],
                    "reason": context.title if context and kind in ("signin", "navigate") else task_context.computer_words(r["reason"], kind),
                    "created_at": r["created"], "why": why, "origin": context.purpose if context else card_origin(r, why),
                    "kind": kind, "excerpt": r.get("excerpt") or None,
                    "sensitive": consent.is_sensitive(r["site"]) if kind in ("signin", "navigate") else False}
            if kind not in ("signin", "navigate"):


                item.update(target=r.get("target") or "", inputs=r.get("inputs") or "",
                            can_always=bool(r.get("scope")), scope=r.get("scope") or "",
                            details=r.get("details") or None, routine_name=r.get("routine_name") or None)
                if kind == "send":

                    item["mail"] = mailcard.okay_mail_public(r["id"])



                because = "\n".join(part for part in (context.purpose if context else "", r.get("because") or "") if part)
                item.update(title=task_context.computer_words(r.get("title") or "", kind) or None, because=because or None,
                            affects=[a for a in str(r.get("affects") or "").split("\n") if a.strip()] or None,
                            safe=r.get("safe") or None, button=r.get("button") or None,
                            icons=[i for i in str(r.get("icons") or "").split("\n") if i] or None,
                            asked=None if context else r.get("asked") or None)
                if r.get("ask_rule"):

                    rule = consent.get_rule(r["ask_rule"])
                    item["paused_by"] = rule_sentence(rule) if rule else "An Ask First rule you removed."
            out.append(item)
    return out


@app.get("/api/signin-requests")
def signin_requests(_: str = Depends(require_device)):
    return {"requests": _signin_requests()}


class DecisionBody(BaseModel):
    decision: str


class OkayMailBody(BaseModel):
    to: list | None = None
    cc: list | None = None
    subject: str | None = None
    body: str | None = None


@app.post("/api/signin-requests/{req_id}/edit-mail")
async def edit_okay_mail(req_id: int, body: OkayMailBody, authorization: str | None = Header(None),
                         _: str = Depends(require_device)):
    ""


    if _tester(authorization) and not (consent.get_request(req_id) or {}).get("test"):
        raise HTTPException(403, "A test pairing can't answer a real card.")
    try:
        bot_id, feed_id, card = await asyncio.to_thread(mailcard.from_okay, req_id, body.model_dump(exclude_none=True))
    except mailcard.MailError as e:
        raise HTTPException(409, str(e))
    asyncio.create_task(mac.clear_okay(req_id))
    return {"ok": True, "bot_id": bot_id, "feed_id": feed_id, "card": mailcard.public(card)}


class UndoBody(BaseModel):
    kind: str
    id: int
    bot_id: str | None = None


@app.post("/api/undo-dismissal")
async def undo_dismissal(body: UndoBody, authorization: str | None = Header(None), _: str = Depends(require_device)):
    ""

    from . import undo
    try:
        if body.kind == "question":
            card = await asyncio.to_thread(undo.question, _bot(body.bot_id or "").id, body.id)
            return {"ok": True, "card": card}
        if body.kind == "approval":
            if _tester(authorization) and not (consent.get_request(body.id) or {}).get("test"):
                raise HTTPException(403, "A test pairing can't answer a real card.")
            await asyncio.to_thread(undo.approval, body.id)
            return {"ok": True}
    except undo.UndoError as e:
        raise HTTPException(409, str(e))
    raise HTTPException(400, "Say whether it was a question or an approval.")


@app.post("/api/signin-requests/{req_id}")
async def decide_signin(req_id: int, body: DecisionBody, authorization: str | None = Header(None),
                        _: str = Depends(require_device)):
    if _tester(authorization) and not (consent.get_request(req_id) or {}).get("test"):

        raise HTTPException(403, "A test pairing can't answer a real card.")
    out = await _decide_signin(req_id, body)
    decided = consent.get_request(req_id)
    if body.decision != "deny" and decided and not decided.get("test") \
            and (decided.get("kind") or "signin") in ("signin", "navigate"):
        company = consent.base(decided["site"])
        for other in consent.pending():
            if (other.get("kind") or "signin") in ("signin", "navigate") \
                    and consent.base(other["site"]) == company \
                    and consent.stored_mode(other["bot_id"], other["site"]) != "ask":
                try:
                    await _decide_signin(other["id"], body)
                except HTTPException as e:
                    if e.status_code != 409:
                        raise
    return out


async def _decide_signin(req_id: int, body: DecisionBody):
    if body.decision not in ("once", "always", "deny"):
        raise HTTPException(400, "Choose Allow Once, Always Allow or Not Now.")
    allow = body.decision != "deny"
    r = consent.decide(req_id, allow, always=body.decision == "always")
    asyncio.create_task(mac.clear_okay(req_id))
    if not r:
        gone = consent.get_request(req_id)
        if gone and gone.get("status") == "expired":
            raise HTTPException(409, consent.expired_line(gone))
        raise HTTPException(409, "That request already moved on.")
    try:
        teams.settle_decided()
    except Exception:
        log.exception("could not settle the team need for request %s", req_id)
    name = config.BOTS_BY_ID[r["bot_id"]].name if r["bot_id"] in config.BOTS_BY_ID else "The Zip"
    kind = r.get("kind") or "signin"
    confirmation = None
    if allow and body.decision == "always" and not r.get("test"):

        if kind in ("signin", "navigate"):
            confirmation = f"Every Zip can sign in to {r['site']} without asking."
        elif r.get("scope") and kind in consent.RULE_KINDS:
            made = consent.rule_for(r["bot_id"], kind, r["scope"])
            confirmation = rule_sentence(made) if made else None
    if kind == "command":



        waiting = await approvals.decide(r, allow)
        later = ""
        if not waiting and r.get("session_id"):
            later = f" {name} is picking it back up now."
            label = who.tapped("Allow on your card") if allow else who.hub_note()
            asyncio.create_task(turns.follow_up(r["bot_id"], f"{label} {approvals.late_note(r, allow)}",
                                                r.get("session_id"), urgent=r.get("why") == "asked"))
        store.add_feed("signin_decision", r["bot_id"], r["task_id"], receipts.answered(allow, r["reason"] or kind),
                       later.strip(), written_by="hub", written_for="record")
        return {"ok": True}
    action = kind not in ("signin", "navigate")
    if allow and action and r["bot_id"] in config.BOTS_BY_ID:
        from . import xapi
        posted = await asyncio.to_thread(xapi.post_from_card, r)
        if posted is not None:
            return _after_x_post(r, posted)
    if action:
        what = r["reason"] or "that"
        always = body.decision == "always" and r.get("scope")
        if allow:
            note = (f"{who.tapped('Allow on your card')} {who.first()} approved this: {what}."
                    + (f" {who.first()} also allowed this kind of action for {r['scope']} from now on."
                       if always else "")
                    + " Make exactly the same call again now, unchanged; it goes through once. Do not ask "
                    + f"{who.first()} to confirm it again in the chat: the tap was the yes. Then carry on and tell "
                    + f"{who.first()} in a sentence or two what was done.")
        else:
            note = (f"{who.first()} said not now to this: {what}. Do not do it, and do not try another way. "
                    f"Finish what you can and tell {who.first()} in a sentence what is left undone.")
    elif allow:
        note = (f"{who.tapped('Allow on your sign-in card')} {who.first()} allowed you to sign in to {r['site']}"
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



        if allow:
            asyncio.create_task(team.finish_after_signin(r["bot_id"], r["site"], r.get("created") or time.time()))
    elif action and r.get("session_id") and team.in_team_session(r["session_id"]):


        if allow:
            asyncio.create_task(team.finish_after_card(r["bot_id"], what, r.get("created") or time.time()))
    elif stray_session(r["bot_id"], r.get("session_id")):



        pass
    elif why != "scheduled" and not r.get("routine"):




        asked = why == "asked" or (not action and why in ("mismatch", "sensitive", "ask", "content")
                                   and bool(r.get("reason")))


        more = any(o["bot_id"] == r["bot_id"] and o.get("session_id") == r.get("session_id")
                   and abs((o.get("created") or 0) - (r.get("created") or 0)) <= 120 for o in consent.pending())
        turns.resume_after_card(r["bot_id"], note, r.get("session_id"), asked, more)
    if action:
        job, later = r.get("routine_name") or "its scheduled job", ""
        if allow and r.get("routine") and not r["task_id"]:
            if _run_job_now(r):
                later = f" {name} is running {job} again now to do it."
            elif body.decision != "always":
                later = f" {name} will do it once on the next run of {job}."


        line = (f"Not sent: {receipts.what(r.get('title') or r['reason'] or kind)}" if kind == "send" and not allow
                else receipts.answered(allow, r.get("title") or r["reason"] or kind))
        store.add_feed("signin_decision", r["bot_id"], r["task_id"], line, later.strip(),
                       written_by="hub", written_for="record")
    else:
        store.add_feed("signin_decision", r["bot_id"], r["task_id"],
                       receipts.answered(allow, receipts.SIGN_IN + r["site"]), "",
                       written_by="hub", written_for="record")
    if confirmation:
        store.add_feed("signin_decision", r["bot_id"], r["task_id"], consent.ALLOW_RULE_ADDED,
                       consent.allowed_line(confirmation), written_by="hub", written_for="record")
    return {"ok": True, "confirmation": consent.allowed_line(confirmation) if confirmation else None,
            "confirmation_title": consent.ALLOW_RULE_ADDED if confirmation else None}


def _run_job_now(r: dict) -> bool:
    ""




    from . import jobretry, readokays
    job_id = str(r.get("routine") or "")
    if not readokays.hard_stops() or not job_id or job_id.startswith("team:") or r["bot_id"] not in config.BOTS_BY_ID:
        return False
    try:
        for bot_id, out in watcher._cron_dirs():
            if bot_id != r["bot_id"]:
                continue
            job = watcher._jobs_of(out.parent / "jobs.json").get(job_id)
            key = f"{bot_id}:{job_id}"
            if job is None or jobretry._busy(key, job, time.time()):
                return False
            return jobretry._launch(key, bot_id, job_id, job, time.time())
    except Exception:
        log.exception("could not run job %s after its card", job_id)
    return False


def _after_x_post(r: dict, posted: dict) -> dict:
    ""


    from . import xapi
    if "again" in posted:
        xapi.reopen(r, posted["again"])
        return {"ok": True}
    if "url" in posted:
        line, shown = "Posted on X", posted["url"]
        note = (f"{who.first()} tapped Allow on your post card, and the hub posted it on X"
                f"{': ' + posted['url'] if posted['url'] else ''}. It is live, so don't post it again.")
    else:
        line, shown = "X didn't confirm the post", "It may or may not be on X. Check the profile before you post it again."
        note = (f"{who.first()} tapped Allow on your post card. The hub sent it to X, but X didn't confirm it, so it may "
                "or may not be live. Don't post it again.")
    store.add_feed("signin_decision", r["bot_id"], r["task_id"], line, shown, written_by="hub", written_for="record")
    if r["task_id"]:
        ok, _out = board.answer_block(r["task_id"], note)
        if not ok:
            board.comment(r["task_id"], note)
    else:
        store.add_card_note(r["bot_id"], note)
    return {"ok": True}


def _permission_rows() -> list[dict]:
    sites = {consent.base(consent.site_of(row.get("origin") or row.get("label") or ""))
             for row in logins.list_all()}
    overrides = set()
    with consent.db() as c:
        sites.update(r["site"] for r in c.execute("SELECT bot_id, site FROM signed_in")
                     if not str(r["bot_id"]).startswith("test:"))
        for r in c.execute("SELECT bot_id, site, mode FROM modes"):
            site = consent.base(r["site"])
            sites.add(site)
            if r["mode"] == "ask" and r["bot_id"] in config.BOTS_BY_ID:
                overrides.add((r["bot_id"], site))
    rows = [("*", site) for site in sorted(sites) if site] + sorted(overrides)
    return [{"bot_id": bot, "site": site, "mode": consent.mode_for(bot, site), "sensitive": consent.is_sensitive(site)}
            for bot, site in rows]


RULE_STALE_SEC = 60 * 86400


def _routine_name(bot_id: str, job_id: str, source: str) -> str:
    ""

    m = re.match(r"card (\d+)$", source)
    if m:
        card = consent.get_request(int(m.group(1)))
        if card and card.get("routine_name"):
            return card["routine_name"]
    home = config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id
    job = watcher._jobs_of(home / "cron" / "jobs.json").get(job_id)
    return str(job.get("name") or "") if isinstance(job, dict) else ""


def _rule_scope(r: dict) -> str:
    ""
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
    ""

    who = _rule_who(r["bot_id"])
    if consent.is_ask_rule(r):
        what = r["kind"].removeprefix(consent.ASK_RULE_PREFIX)
        return f"{who} asks before {consent.ask_rule_phrase(what, r['scope'])}."
    scope = _rule_scope(r)
    if r["kind"] == "send":

        verb = "email" if "@" in scope else \
            "send data to" if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", scope) else "send messages to"
        return f"{who} can {verb} {scope} without asking."
    if r["kind"] == "write":
        return f"{who} can make changes in {scope} without asking."
    if r["kind"] == "signin":
        return f"{who} can sign in to {scope} without asking."
    if r["kind"] == "submit":
        return f"{who} can submit forms on {scope} without asking."
    if r["kind"] == "book":
        return f"{who} can book on {scope} without asking."
    return f"{who} can do this without asking: {scope}."


def _rule_rows() -> list[dict]:
    ""
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

                    "stale": not ask and now - (r.get("last_used") or r["created"]) > RULE_STALE_SEC})
    return out


@app.get("/api/permissions")
def permissions(_: str = Depends(require_device)):
    ""
    return {"permissions": _permission_rows(),
            "signins": [{**s, "how": "asked" if s["how"] == consent.ASKED_NO_FIT else s["how"]}
                        for s in consent.recent_signins(40)],
            "rules": _rule_rows(),
            "actions": [a for a in consent.recent_actions(40) if a["bot_id"] in config.BOTS_BY_ID]}




RULE_SAVED = "Saved to Custom Rules"


def _rule_choices() -> dict:
    ""
    return {"what": [{"id": w, "title": phrase[0][:1].upper() + phrase[0][1:]}
                     for w, phrase in consent.ASK_RULE_WHATS.items()],
            "places": [{"id": consent.ANYWHERE, "title": "Anywhere"}]
                      + [{"id": p, "title": title} for p, (title, _hosts) in consent.ASK_RULE_PLACES.items()]}


@app.get("/api/rules")
def custom_rules(bot_id: str | None = None, _: str = Depends(require_device)):
    ""
    if bot_id:
        _bot(bot_id)
    return {"rules": _custom_rule_rows(bot_id), "choices": _rule_choices()}


class AskRuleBody(BaseModel):
    bot_id: str
    what: str
    where: str = "*"


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
    ""
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
    ""

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
    if body.bot_id != "*":
        _bot(body.bot_id)
    if body.mode not in consent.MODES:
        raise HTTPException(400, "Pick When I Ask, Ask Me First or Anytime.")
    site = consent.site_of(validate.short(body.site, 200, "site") or "")
    if not site:
        raise HTTPException(400, "That site does not look right.")
    consent.set_mode(body.bot_id, site, body.mode)
    return {"ok": True}




TEMPLATES = config.SHARED_DIR / "zip-templates.json"



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



TEMPLATE_OPTIONAL = {"brief": {"trello"}}


def template_missing(t: dict) -> list[str]:
    ""


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
    ""


    from . import home
    if home.trello():
        return text
    out = _TRELLO_ASIDE.sub("", text)
    return _TRELLO_SENTENCE.sub("", out) if "Trello" in out else out


@app.get("/api/templates")
def zip_templates(_: str = Depends(require_device)):
    ""
    try:
        data = json.loads(TEMPLATES.read_text())
    except (OSError, ValueError):
        data = []
    taken = {b.id for b in config.BOTS}
    first = who.first()

    def named(v):
        ""

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




class NewZipBody(BaseModel):
    name: str
    look: dict
    color: str = ""
    model: str = ""
    general: bool = False


@app.post("/api/zips", status_code=202)
async def new_zip(body: NewZipBody, authorization: str | None = Header(None), _: str = Depends(require_device)):
    ""
    try:
        return {"bot": newzip.start(body.name, body.look, body.color, device=looking.key(authorization),
                                    model=body.model, general=body.general)}
    except newzip.NewZipError as e:
        raise HTTPException(409, str(e))




class RenameBody(BaseModel):
    name: str


@app.post("/api/bots/{bot_id}/name")
def rename_bot(bot_id: str, body: RenameBody, _: str = Depends(require_device)):
    ""
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
            inside = False
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
            log.warning("could not update Zero's team table for %s", bot_id)

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
        team.sync_souls()
    except Exception:
        log.warning("team roster sync after rename failed", exc_info=True)
    return {"ok": True, "name": name}




@app.delete("/api/bots/{bot_id}")
def remove_bot(bot_id: str, _: str = Depends(require_device)):
    ""
    if bot_id not in config.BOTS_BY_ID:
        try:
            if newzip.forget(bot_id):
                return {"ok": True}
        except newzip.NewZipError as e:
            raise HTTPException(409, str(e))
    bot = _bot(bot_id)
    if bot_id == "default":
        raise HTTPException(400, f"{bot.name} leads the team and cannot be deleted.")
    if turns.is_busy(bot_id):
        raise HTTPException(409, f"{bot.name} is answering right now. Try again when it finishes.")
    _remove_bot(bot_id)
    newzip.drop(bot_id)
    return {"ok": True}


def _remove_bot(bot_id: str) -> None:
    import json as _json
    import os as _os
    import subprocess as _sp
    bot = _bot(bot_id)
    try:
        wallet.keep_from_zip(bot_id)
        logins.keep_from_zip(bot_id)
    except wallet.WalletError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    p = _sp.run([str(config.HERMES_BIN), "profile", "delete", "-y", bot_id], capture_output=True, text=True,
                timeout=120, env=dict(_os.environ, HERMES_HOME=str(config.HERMES_HOME)))
    if p.returncode != 0 and (config.HERMES_HOME / "profiles" / bot_id).exists():
        log.info("profile delete failed: %s", (p.stdout + p.stderr)[-300:])
        raise HTTPException(500, f"Could not remove {bot.name}. Try again in a moment.")
    computers.release(bot_id)
    looks.forget(bot_id)
    wallet.zip_deleted(bot_id)
    from . import signin_access
    signin_access.zip_deleted(bot_id)
    try:
        config.refuse_live_registry(config.REGISTRY)
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
        for l in lines:
            inside = (inside or l.startswith(team.SOUL_START)) and not l.startswith(team.SOUL_END)
            if inside or not l.startswith(f"| `{bot_id}` |"):
                out.append(l)
        soul.write_text("\n".join(out))
    except OSError:
        pass
    store.set_session(bot_id, "")
    config.reload_bots()
    from . import zipmail


    threading.Thread(target=zipmail.retire_zip, args=(bot_id,), name=f"zipmail-retire-{bot_id}", daemon=True).start()




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
    ""


    if body.confirm.strip().lower() != "delete everything":
        raise HTTPException(400, "Say \"delete everything\" to confirm.")
    try:
        return erase.delete_everything((authorization or "")[7:])
    except erase.Busy:
        raise HTTPException(409, "A Zip is answering right now. Try again when it finishes.") from None
    except erase.Unfinished as e:
        raise HTTPException(409, str(e)) from None


class SnoozeBody(BaseModel):
    until: float | None = None


@app.post("/api/bots/{bot_id}/snooze")
def snooze_bot(bot_id: str, body: SnoozeBody, _: str = Depends(require_device)):
    ""
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
    ""
    _bot(bot_id)
    store.set_notify(bot_id, body.on)
    return {"ok": True, "notify": store.notify_on(bot_id)}




@app.get("/api/media/{media_id}")
def get_media(media_id: str, _: str = Depends(require_device)):
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{32}", media_id):
        raise HTTPException(404, "That picture isn't there anymore.")
    from . import safe_files
    media = config.HUB_DIR / "media"
    try:

        f = safe_files.open_file(str(media / f"{media_id}.jpg"), (media,), protected=None)
    except safe_files.Refused:
        raise HTTPException(404, "That picture is gone.") from None
    return _file_response(f, None, "image/jpeg", {"Cache-Control": "private, max-age=86400"})


@app.post("/api/media/{media_id}/page-link")
def result_page_link(media_id: str, _: str = Depends(require_device)):
    ""

    from . import resultlinks
    found = resultcard.path_of(media_id, "page")
    if not found or not found[0].is_file():
        raise HTTPException(404, "That page isn't there anymore.")
    return {"path": resultlinks.mint(media_id), "expires_in": resultlinks.LIFETIME}


@app.get("/result-page/{media_id}")
def open_result_page(media_id: str, expires: str = "", sig: str = ""):
    ""
    from fastapi.responses import HTMLResponse, PlainTextResponse
    from . import resultlinks, safe_files
    ended = PlainTextResponse("This link has ended. Open the page from Zero again.", 403,
                              headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    found = resultcard.path_of(media_id, "page") if resultlinks.check(media_id, expires, sig) else None
    if not found:
        return ended
    try:
        f = safe_files.open_file(str(found[0]), (resultcard.media_dir(),), protected=None)
    except safe_files.Refused:
        return ended
    with f:
        page = resultlinks.browser_page(f.read())
    return HTMLResponse(page, headers=resultlinks.PAGE_HEADERS)


@app.get("/api/media/{media_id}/{part}")
def get_result_part(media_id: str, part: str, _: str = Depends(require_device)):
    ""
    found = resultcard.path_of(media_id, part)
    if not found:
        raise HTTPException(404, "That isn't there anymore.")
    from . import safe_files
    path, name = found
    try:
        f = safe_files.open_file(str(path), (resultcard.media_dir(),), protected=None)
    except safe_files.Refused:
        raise HTTPException(404, "That is gone.") from None
    if part == "picture":
        return _file_response(f, None, "image/jpeg", {"Cache-Control": "private, max-age=86400"})
    if part == "page":
        return _file_response(f, None, "text/html; charset=utf-8", {"Cache-Control": "private, max-age=86400"})
    return _file_response(f, name)




@app.get("/api/me")
async def about_me(request: Request, _: str = Depends(require_device)):
    ""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    try:
        await accounts.check()
    except Exception:
        log.warning("accounts check for /api/me failed", exc_info=True)
    return await asyncio.to_thread(profile.summary, host)




class TrackingBody(BaseModel):
    action: str
    revision: int


@app.get("/api/bots/{bot_id}/tracking")
def list_tracking(bot_id: str, _: str = Depends(require_device)):
    _bot(bot_id)
    from . import loopcheck
    return {"notes": loopcheck.for_app(bot_id)}


@app.post("/api/bots/{bot_id}/tracking/{note_id}")
def update_tracking(bot_id: str, note_id: int, body: TrackingBody, _: str = Depends(require_device)):
    _bot(bot_id)
    from . import loopcheck
    if body.action not in ("done", "remove", "keep"):
        raise HTTPException(400, "Choose done, remove or keep.")
    if not loopcheck.by_person(bot_id, note_id, body.action, body.revision):
        raise HTTPException(409, "This changed. Refresh the list and try again.")
    return {"ok": True}

class FactBody(BaseModel):
    text: str
    kind: str | None = None


@app.get("/api/facts")
def list_facts(_: str = Depends(require_device)):
    ""
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


@app.post("/api/facts/{fact_id}/restore")
def restore_fact(fact_id: int, _: str = Depends(require_device)):
    ""
    from . import factkeeper
    try:
        factkeeper.restore(fact_id)
    except facts.FactError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.delete("/api/facts/{fact_id}")
def delete_fact(fact_id: int, _: str = Depends(require_device)):
    if not facts.delete(fact_id):
        raise HTTPException(404, "That fact is already gone.")
    return {"ok": True}





_REACT_TOPICS = {"work": "what keeps them busy these days", "home": "where they live", "age": "how old they are",
                 "family": "who their people are", "sleep": "when they usually sleep"}
REACT_PROMPT = """You are {agent}, an assistant meeting {person} for the first time. You asked {person} {topic}. \
They answered:
<answer>
{answer}
</answer>
Write one short casual sentence, at most 12 words, that reacts to one concrete detail of the answer the way a friend \
catching up would. Keep the warmth at about 4 out of 10: no exclamation points, no praise such as "congrats" or \
"amazing", no question, no advice, no emojis and no em dashes. For an age or a sleep schedule a plain acknowledgment \
is enough. Write only the sentence."""


REACT_SYSTEM = "You write one short, casual line a friendly assistant says back to a person. Answer with the line only."


class TidyDictationBody(BaseModel):
    text: str
    before: str = ""
    bot_id: str = ""


@app.post("/api/dictation/tidy")
async def tidy_dictation(body: TidyDictationBody, _: str = Depends(require_device)):
    ""

    from . import tidydictation
    if len(body.text) > 100_000 or len(body.before) > 100_000:
        raise HTTPException(400, "The message is too long.")
    return await tidydictation.tidy(body.text, body.before, body.bot_id)


@app.post("/api/onboarding/react")
async def onboarding_react(body: dict, _: str = Depends(require_device)):
    ""


    topic = _REACT_TOPICS.get(str(body.get("question") or ""))
    answer = " ".join(str(body.get("answer") or "").split())[:600]
    if not topic or not answer:
        return {"line": None}
    prompt = REACT_PROMPT.format(agent=config.BOTS_BY_ID["default"].name, person=str(body.get("person") or "them")[:40],
                                 topic=topic, answer=answer)
    try:
        with shortreply.reading(()):
            text = await asyncio.wait_for(shortreply.ask_quick(prompt, timeout=4.5, max_tokens=60, system=REACT_SYSTEM), 5)
    except Exception:
        return {"line": None}
    line = re.sub(r"\s*—\s*", ", ", " ".join(text.strip().strip('"').split()))
    if not line or "?" in line or len(line) > 120 or not line[-1] in ".!":
        return {"line": None}
    return {"line": line.replace("!", ".")}

@app.post("/api/onboarding")
def save_onboarding(body: dict, device: str = Depends(require_device)):
    ""

    try:
        answers = onboarding.clean(body)
    except onboarding.OnboardingError as e:
        raise HTTPException(400, str(e))
    if answers["agent_name"]:
        rename_bot("default", RenameBody(name=answers["agent_name"]), device)
        answers["agent_name"] = config.BOTS_BY_ID["default"].name
    if answers["look"]["body"]:
        try:
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




class VoiceStartBody(BaseModel):
    sdp: str
    voice: str | None = None
    voice_chosen: bool | None = None
    self_test: bool = False
    self_test_probe: bool = False


@app.get("/api/bots/{bot_id}/voice/self-test")
def voice_self_test_capability(bot_id: str, _: str = Depends(require_device)):
    bot = _bot(bot_id)
    if not re.fullmatch(r"trace\d{8,10}", bot.id) or not bot.name.startswith("Trace Test"):
        raise HTTPException(403, "Only the throwaway self-test Zip may check this capability.")
    return {"self_test_call_version": 1, "self_test_probe_version": 2}


class SelfTestAskBody(BaseModel):
    text: str
    want: str = ""
    wait: float = 30.0
    spoken: bool = False


@app.post("/api/voice/{call_id}/self-test-ask")
async def voice_self_test_ask(call_id: str, body: SelfTestAskBody, _: str = Depends(require_device)):
    ""

    try:
        return {"said": await voice.self_test_ask(call_id, body.text, body.want, body.wait, body.spoken)}
    except LookupError as e:
        raise HTTPException(403, str(e))


@app.post("/api/bots/{bot_id}/voice")
async def voice_start(bot_id: str, body: VoiceStartBody, _: str = Depends(require_device)):
    bot = _bot(bot_id)
    if body.self_test and (not re.fullmatch(r"trace\d{8,10}", bot.id) or not bot.name.startswith("Trace Test")):
        raise HTTPException(403, "Only the throwaway self-test Zip may make a self-test call.")
    if not body.sdp.startswith("v=0") or len(body.sdp) > 200_000:
        raise HTTPException(400, "The call couldn't start. Try again.")
    try:
        if body.self_test:
            call, answer = await voice.start_self_test(bot_id, body.sdp, body.voice, chosen=body.voice_chosen,
                                                       probe=body.self_test_probe)
        else:
            call, answer = await voice.start(bot_id, body.sdp, body.voice, chosen=body.voice_chosen)
    except voice.MinutesUsed:
        raise HTTPException(503, voice.MINUTES_USED)
    except Exception as e:

        log.warning("voice start failed: %s: %s", type(e).__name__, e, exc_info=True)
        health.voice_failed()

        raise HTTPException(503, voice.start_error(e))
    return {"call_id": call.id, "sdp": answer, "voice": call.voice}


class RejoinBody(BaseModel):
    sdp: str


@app.post("/api/voice/{call_id}/rejoin")
async def voice_rejoin(call_id: str, body: RejoinBody, _: str = Depends(require_device)):
    ""
    if not body.sdp.startswith("v=0") or len(body.sdp) > 200_000:
        raise HTTPException(400, "The call couldn't reconnect.")
    try:
        answer = await voice.rejoin(call_id, body.sdp)
    except LookupError:
        raise HTTPException(404, "That call has ended.")
    except Exception as e:
        log.warning("voice rejoin failed: %s", e)
        raise HTTPException(503, "The call couldn't reconnect.")
    return {"sdp": answer}


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
    waited_ms: int | None = None


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
    ""
    voice.set_away(call_id, body.away)
    return {"ok": True}


@app.post("/api/voice/{call_id}/talking")
def voice_talking(call_id: str, body: TalkingBody, _: str = Depends(require_device)):
    ""
    voice.set_talking(call_id, body.talking)
    return {"ok": True}


@app.post("/api/voice/{call_id}/stop")
async def voice_stop(call_id: str, _: str = Depends(require_device)):
    await voice.stop(call_id, reason="the app ended it")
    return {"ok": True}


class DiagnosticsBody(BaseModel):
    events: list


@app.post("/api/voice/{call_id}/diagnostics")
def voice_diagnostics(call_id: str, body: DiagnosticsBody, _: str = Depends(require_device)):
    ""


    if not calllog.valid_id(call_id) or len(body.events) > calllog.APP_EVENTS_PER_POST:
        raise HTTPException(400, "That didn't work.")
    for event in body.events:
        if isinstance(event, dict) and event.get("event") == "model_usage":
            usage.log_realtime(call_id, event)
    return {"ok": True, "saved": calllog.app_events(call_id, body.events)}


@app.get("/api/voices")
def voice_list(_: str = Depends(require_device)):
    return {"voices": voice.VOICES, "default": voice.DEFAULT_VOICE}


@app.get("/api/voice/engines")
def voice_engines(_: str = Depends(require_device)):
    ""
    return voice.engines()




class LocalVoiceStartBody(BaseModel):
    voice: str | None = None
    reason: str | None = None
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
    ""
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
    ""
    return {"ok": ondevice.confirm(_local_call(call_id), body.n)}


class SaidBody(BaseModel):
    n: int
    spoken: str = ""
    cut_off: bool = False
    first_audio_ms: int | None = None


@app.post("/api/voice/{call_id}/said")
def voice_said(call_id: str, body: SaidBody, _: str = Depends(require_device)):
    ""
    return {"ok": ondevice.said(_local_call(call_id), body.n, body.spoken, body.cut_off, body.first_audio_ms)}




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
    ""

    return mac.state_for_phone()


@app.post("/api/mac/approvals/{approval_id}")
async def mac_answer(approval_id: str, body: MacAnswerBody, _: str = Depends(require_device)):
    ""
    try:
        return {"ok": True, "text": await mac.answer_from_phone(approval_id, body.allow, body.key_id, body.signature)}
    except mac.MacError as e:
        raise HTTPException(409, str(e))


@app.post("/api/mac/approver")
async def mac_approver(body: MacApproverBody, authorization: str | None = Header(None),
                       _: str = Depends(require_device)):
    ""
    try:
        await mac.register_approver(store._hash((authorization or "")[7:]), body.key, body.name)
    except (mac.MacError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class MacAccessBody(BaseModel):
    mode: str
    key_id: str = ""
    signature: str = ""
    at: int = 0


@app.post("/api/mac/access")
async def mac_access(body: MacAccessBody, _: str = Depends(require_device)):
    ""
    try:
        return {"ok": True, "access": await mac.set_access(body.mode, body.key_id, body.signature, body.at)}
    except mac.MacError as e:
        raise HTTPException(409, str(e))


@app.post("/api/mac/stop")
async def mac_stop(_: str = Depends(require_device)):
    ""
    return {"ok": True, "text": await mac.stop_from_phone()}




class MacRemoteStartBody(BaseModel):
    session: str
    key_id: str
    signature: str
    at: int
    url: str = ""
    device: str = "iPhone"
    width: int = 1280


class MacRemoteStopBody(BaseModel):
    session: str = ""


def _own_device(authorization: str | None) -> str:
    ""
    token = (authorization or "")[7:]
    if store.tester_name(token):
        raise HTTPException(403, "A test pairing can't open the Mac.")
    return store._hash(token)


@app.post("/api/mac/remote/start")
async def mac_remote_start(body: MacRemoteStartBody, authorization: str | None = Header(None),
                           _: str = Depends(require_device)):
    ""
    from . import macremote
    device = _own_device(authorization)
    try:
        return {"ok": True, **await macremote.start(device, body.session, body.key_id, body.signature, body.at,
                                                     body.url[:2000], "iPad" if body.device == "iPad" else "iPhone",
                                                     body.width)}
    except macremote.RemoteError as e:
        raise HTTPException(409, str(e))


@app.post("/api/mac/remote/stop")
async def mac_remote_stop(body: MacRemoteStopBody, authorization: str | None = Header(None),
                          _: str = Depends(require_device)):
    ""
    from . import macremote
    device = _own_device(authorization)
    if macremote.owns(device, body.session):
        await macremote.stop(reason="closed")
    return {"ok": True}


@app.websocket("/api/mac/remote/ws")
async def mac_remote_ws(ws: WebSocket, session: str = ""):
    ""
    from . import macremote
    auth = ws.headers.get("authorization") or ""
    await ws.accept()
    if not auth.startswith("Bearer ") or not store.device_for_token(auth[7:]):
        await ws.close(code=4401, reason="not paired")
        return
    if store.tester_name(auth[7:]):
        await ws.close(code=4403, reason="a test pairing can't open the Mac")
        return
    await macremote.serve_phone(ws, store._hash(auth[7:]), session)


@app.websocket("/api/mac/link")
async def mac_link(ws: WebSocket):
    ""
    auth = ws.headers.get("authorization") or ""
    await ws.accept()
    if not auth.startswith("Bearer ") or not store.device_for_token(auth[7:]):
        await ws.close(code=4401, reason="not paired")
        return
    if store.tester_name(auth[7:]):
        await ws.close(code=4403, reason="a test pairing can't be the Mac")
        return
    if not store.may_hold_mac_link(auth[7:]):
        await ws.close(code=4403, reason="another Mac holds the link")
        return
    await mac.serve_link(ws)


@app.put("/api/mac/upload/{secret}")
async def mac_upload(secret: str, request: Request):
    ""


    from . import maccopy
    try:
        return {"ok": True, "bytes": await maccopy.receive(secret, request.stream())}
    except maccopy.TooBig as e:
        raise HTTPException(413, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


def _internal_key() -> str:
    ""
    import os
    import secrets as _secrets
    path = config.HUB_DIR / "internal-key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_secrets.token_urlsafe(32))
        os.chmod(path, 0o600)
    return path.read_text().strip()


def require_internal(request: Request, x_taffy_internal: str | None = Header(None)) -> None:
    import hmac

    if request.headers.get("tailscale-user-login") or request.headers.get("x-forwarded-for"):
        raise HTTPException(404, "Not found.")
    if not x_taffy_internal or not hmac.compare_digest(x_taffy_internal, _internal_key()):
        raise HTTPException(404, "Not found.")


class InternalSignInNote(BaseModel):
    bot: str
    site: str
    text: str


@app.post("/internal/sign-in-notes", dependencies=[Depends(require_internal)])
def internal_sign_in_note(body: InternalSignInNote):
    ""
    from . import signin_notes
    if body.bot not in config.BOTS_BY_ID:
        raise HTTPException(400, "That Zip is no longer here.")
    try:
        note = signin_notes.add(_sign_in_site(body.site), body.bot, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"saved": True, "site": note["site"]}


@app.get("/internal/sign-in-notes", dependencies=[Depends(require_internal)])
def internal_sign_in_notes(site: str):
    ""
    from . import signin_notes
    try:
        key = _sign_in_site(site)
    except HTTPException:
        return {"text": "", "mac_only": False}
    return {"text": signin_notes.heard(key), "mac_only": signin_notes.mac_only(key)}


from . import teach
app.include_router(teach.router(require_device, require_internal))


def require_doctor(request: Request, _: None = Depends(require_internal)) -> None:
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(404, "Not found.")


class InternalZipBody(BaseModel):
    name: str
    look: dict
    color: str = ""
    model: str = ""
    general: bool = False


@app.post("/internal/zips", status_code=202)
async def internal_new_zip(body: InternalZipBody, _: None = Depends(require_doctor)):
    ""


    try:
        return {"bot": newzip.start(body.name, body.look, body.color, model=body.model, general=body.general)}
    except newzip.NewZipError as e:
        raise HTTPException(409, str(e))


@app.get("/internal/problems")
def doctor_reports(state: str | None = None, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
                   _: None = Depends(require_doctor)):
    try:
        return {"reports": problems.list_reports(state, after, limit)}
    except problems.ProblemError as e:
        raise HTTPException(422, str(e)) from None


@app.post("/internal/problems/group")
async def doctor_group(body: dict, _: None = Depends(require_doctor)):
    ""

    from . import nightly
    groups, whole = await problems.merged_groups(nightly.ask_model)
    done = await asyncio.to_thread(problems.close_duplicates, body.get("apply") is True, groups)
    biggest = sorted(groups, key=len, reverse=True)[:25]
    return {**done, "model_answered": whole,
            "largest": [{"count": len(g), "zip": g[0]["about_bot"], "kind": g[0]["kind"], "place": g[0]["place"]} for g in biggest]}


@app.post("/internal/blocks/reroute")
async def doctor_reroute_blocks(body: dict, _: None = Depends(require_doctor)):
    ""

    from . import wayforward
    return {"cards": await wayforward.reroute_open(body.get("apply") is True)}


@app.patch("/internal/problems/{report_id}")
async def doctor_update(report_id: int, body: dict, _: None = Depends(require_doctor)):
    try:
        row = await asyncio.to_thread(problems.update, report_id, body)
    except problems.ProblemError as e:
        raise HTTPException(422, str(e)) from None
    line = row.pop("told", None)
    if line:
        await problems.tell_fixed(line)
    return row


@app.get("/internal/problems/diagnostics/{diagnostic_id}")
def doctor_diagnostic(diagnostic_id: int, _: None = Depends(require_doctor)):
    result = problems.diagnostic(diagnostic_id)
    if result is None:
        raise HTTPException(404, "Unknown diagnostic payload.")
    return result


class MacBody(BaseModel):
    bot_id: str
    session_id: str = ""
    origin_task: str | None = None
    args: dict = {}
    sent_session: str = ""

    @model_validator(mode="after")
    def _conversation(self):
        ""


        self.sent_session = self.session_id
        if self.session_id:
            try:
                from . import lineage
                self.session_id = lineage.root(self.bot_id if self.bot_id in config.BOTS_BY_ID else None,
                                               self.session_id) or self.session_id
            except Exception:
                pass
        return self


@app.post("/internal/finish-judge/board")
async def internal_finish_judge_board(body: MacBody, _: None = Depends(require_internal)):
    ""

    from . import finishjudge
    a = body.args
    if body.bot_id in config.BOTS_BY_ID and a.get("task_id") and finishjudge.mode() != "off":
        finishjudge.board_later(body.bot_id, body.sent_session or body.session_id, str(a["task_id"]),
                                str(a.get("summary") or ""), bool(a.get("sent_back")))
    return {"ok": True}


@app.post("/internal/finish-judge/close")
async def internal_finish_judge_close(body: MacBody, _: None = Depends(require_internal)):
    ""

    from . import finishjudge
    a = body.args
    if body.bot_id not in config.BOTS_BY_ID or not a.get("task_id"):
        return {"ok": True, "result": {}}
    try:
        found = await finishjudge.board_close(body.bot_id, body.sent_session or body.session_id, str(a["task_id"]),
                                              str(a.get("summary") or ""))
    except Exception:
        log.warning("the finish check on a board close failed", exc_info=True)
        found = {}
    return {"ok": True, "result": found}


MAC_OPS = {"open", "run", "screenshot", "ui", "press", "click", "type", "key", "scroll", "steps", "inspect",
           "copy", "upload", "login_context", "fill_login", "login_cancel", "login_status"}


@app.post("/internal/board/step")
async def internal_board_step(body: MacBody, _: None = Depends(require_internal)):
    ""


    from . import doing
    args = body.args or {}
    if body.bot_id not in config.BOTS_BY_ID or not validate.TASK_ID.match(body.origin_task or ""):
        return {"ok": False}
    step = doing.note_step(str(body.origin_task), body.bot_id, str(args.get("tool") or ""), args.get("args"))
    return {"ok": True, "step": step}


@app.post("/internal/mac/{op}")
async def internal_mac(op: str, body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if config.is_sandbox(bot.id) and (op in MAC_OPS or op in ("start", "done")):


        from . import maccodex
        path = str((body.args or {}).get("path") or "").strip()
        if not (config.mac_practice(bot.id) and (op in ("start", "done") or (op == "copy" and maccodex.practice_path(path)))):
            return {"ok": False, "error": "A test Zip doesn't use the Mac."}
    if op == "react" and team.in_team_session(body.session_id):

        return {"ok": False, "error": "You are answering a teammate's question. Answer only; use tools like this in "
                                      "your own work."}
    try:
        if op == "start":
            text = await mac.start(bot.id, bot.name, str(body.args.get("task") or ""),
                                   str(body.args.get("person_said") or body.args.get("colin_said") or ""),
                                   body.session_id, body.origin_task)
            return {"ok": True, "result": text}
        if op == "done":
            return {"ok": True, "result": await mac.done(bot.id, body.session_id)}
        if op == "status":
            return {"ok": True, "result": {"online": mac.online(), "access": mac.access(), "walled": mac.walled()}}
        if op == "probe":
            if not re.fullmatch(r"trace\d{8,10}", bot.id) or not bot.name.startswith("Trace Test"):
                raise HTTPException(403, "Only the throwaway self-test Zip may probe the Mac.")
            reply = await mac._call("in_use")
            if not isinstance(reply.get("result"), bool):
                raise mac.MacError("The Mac gave an invalid probe reply.")
            return {"ok": True, "result": {"online": True, "bridge_answered": True,
                                           "in_use": reply["result"]}}
        if op == "react":
            return await _bot_react(bot.id, str(body.args.get("kind") or ""))
        if op == "messages":
            from . import imessage
            return {"ok": True, "result": await imessage.read(bot.id, body.args)}
        if op == "reminders":
            action = str(body.args.get("action") or "")


            result = await remindermirror.relay(bot.id, action, body.args, _waits_for_reply(body))
            from . import remindertime
            if action == "add" and remindertime.ENABLED:

                kept = await asyncio.to_thread(remindertime.after_add, bot.id, body.args, result)
                if kept and isinstance(result, dict):
                    result = {**result, "you_message_then": remindertime.said_when(kept["at"])}
                    remindertime.drop_twins_later(bot.id)
            return {"ok": True, "result": result}
        if op == "contacts":
            return {"ok": True, "result": await mac.contacts(bot.id, str(body.args.get("action") or ""), body.args)}
        if op == "claude":
            return {"ok": True, "result": await mac.claude(bot.id, str(body.args.get("brief") or ""),
                                                           str(body.args.get("folder") or ""))}
        if op == "codex":
            from . import maccodex
            return {"ok": True, "result": await maccodex.call(bot.id, bot.name, body.args, body.session_id,
                                                              body.origin_task)}
        if op not in MAC_OPS:
            raise HTTPException(404, "Unknown action.")
        return {"ok": True, "result": await mac.act(bot.id, op, body.args, body.session_id,
                                                    origin_task=body.origin_task)}
    except mac.MacError as e:
        if getattr(e, "code", "") == "no_answer" and (op, str(body.args.get("action") or "")) in SLOW_READS:


            return {"ok": False, "error": SLOW_MAC_LINE}
        return {"ok": False, "error": str(e)}


SLOW_READS = {("messages", a) for a in ("recent", "read", "search", "since", "lookup")} | {
    ("reminders", "lists"), ("reminders", "due"), ("contacts", "find")}
SLOW_MAC_LINE = (f"{mac.NO_ANSWER} It is busy, not off: make the same call once more. If it is still slow, carry on "
                 "with what you have and say in one short line what you will finish when it answers. Never ask for "
                 "the job to be done by hand or copied.")


MEDIA_OPS = {"image"}


@app.post("/internal/media/{op}")
async def internal_media(op: str, body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id not in config.BOTS_BY_ID:
        return {"ok": False, "error": "Unknown bot."}
    if op not in MEDIA_OPS:
        raise HTTPException(404, "Unknown action.")
    from . import mediagen
    try:
        result = await asyncio.to_thread(mediagen.run, op, body.bot_id, body.args)
    except mediagen.MediaError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "result": result}


_internal_key()


@app.post("/internal/self-test/push-answer")
async def self_test_push_answer(body: MacBody, _: None = Depends(require_internal)):
    ""
    from . import apns
    return {"ok": True, "result": apns.last_answer()}


@app.post("/internal/self-test/stop-calls")
async def self_test_stop_calls(body: MacBody, _: None = Depends(require_internal)):
    if not re.fullmatch(r"trace\d{8,10}", body.bot_id):
        raise HTTPException(403, "Only throwaway self-test calls may be stopped here.")
    try:
        await voice.stop_self_test(body.bot_id)
    except TimeoutError:
        raise HTTPException(504, "The self-test call did not stop in time.")
    return {"ok": True, "active": False, "pending": False}




class ChoiceBody(BaseModel):
    picked: str | None = None
    custom: bool = False
    ordered: list[str] | None = None


class QuestionFeedbackBody(BaseModel):
    rating: Literal["good", "bad"]
    reason: str = Field(default="", max_length=notice_feedback.REASON_MAX)


@app.put("/api/bots/{bot_id}/choices/{feed_id}/feedback")
def rate_question(bot_id: str, feed_id: int, body: QuestionFeedbackBody, _: str = Depends(require_device)):
    _bot(bot_id)
    try:
        feedback = notice_feedback.rate(bot_id, feed_id, body.rating, body.reason)
    except notices.AnswerError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "feedback": feedback}


@app.post("/api/bots/{bot_id}/mac-wait/{feed_id}/go")
def mac_go_ahead(bot_id: str, feed_id: int, _: str = Depends(require_device)):
    ""
    from . import macqueue, waits
    _bot(bot_id)
    try:
        state = macqueue.go_ahead(bot_id, feed_id)
    except macqueue.GoAheadError as e:
        raise HTTPException(409, str(e))
    waits.kick()
    return {"ok": True, "state": state}


@app.post("/api/bots/{bot_id}/choices/{feed_id}")
async def answer_choice(bot_id: str, feed_id: int, body: ChoiceBody, authorization: str | None = Header(None),
                        _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    desk.read_chat(bot_id, looking.key(authorization))
    try:
        card = await choices.answer_async(bot_id, feed_id, body.picked, body.custom, ordered=body.ordered)
    except choices.ChoiceError as e:
        raise HTTPException(409, str(e))
    _acknowledge(bot_id, card)
    return {"ok": True, "card": card}


class StackAnswerBody(BaseModel):
    picked: str = Field(min_length=1, max_length=500)
    custom: bool = False


@app.post("/api/bots/{bot_id}/choices/{feed_id}/questions/{index}")
async def answer_stack_question(bot_id: str, feed_id: int, index: int, body: StackAnswerBody,
                                authorization: str | None = Header(None),
                                _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    desk.read_chat(bot_id, looking.key(authorization))
    try:
        card, send = choices.answer_question(bot_id, feed_id, index, body.picked, body.custom)
    except choices.ChoiceError as e:
        raise HTTPException(409, str(e))
    device = looking.key(authorization)
    looking.saw(device)
    turn = turns.send(bot_id, send, device=device, tester=_tester(authorization)) if send else None
    return {"ok": True, "card": card, "complete": card["state"] == "picked", "turn_id": turn.id if turn else None}


def _acknowledge(bot_id: str, card: dict, push: bool = False) -> None:
    ""
    note = card.pop("ack", None)
    if note:
        asyncio.create_task(turns.follow_up(bot_id, note, None, urgent=False, push=push))


class LockBody(BaseModel):
    action: str
    text: str | None = None
    unlocked: bool = False


@app.post("/api/bots/{bot_id}/choices/{feed_id}/lock")
async def lock_answer(bot_id: str, feed_id: int, body: LockBody, authorization: str | None = Header(None),
                      _: str = Depends(require_device)):
    ""


    _bot(bot_id)
    desk.read_chat(bot_id, looking.key(authorization))
    try:
        card = notices.lock_answer(feed_id, body.action, body.text or "", unlocked=body.unlocked)
    except notices.AnswerError as e:
        raise HTTPException(409, str(e))
    words = card.pop("send", None)
    if words:
        turns.send("default", words)
    _acknowledge(bot_id, card, push=True)
    line = card.pop("open", None)
    if line:
        r = notices.by_feed(feed_id)
        await notices.send_push(notices._lead(), line, notices.collapse_id(r["subject"]) if r else "q-open", feed_id,
                                category=notices.OPEN_ONLY)
    return {"ok": True, "card": card, "opened": bool(line)}


class NotificationReplyBody(BaseModel):
    text: str
    line: str | None = None
    item_id: int | None = None


@app.post("/api/bots/{bot_id}/notification-reply")
async def notification_reply(bot_id: str, body: NotificationReplyBody, authorization: str | None = Header(None),
                             _: str = Depends(require_device)):
    ""


    bot = _bot(bot_id)
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(400, "Type an answer first.")
    desk.read_chat(bot_id, looking.key(authorization))
    quoted = " ".join((body.line or "").split())
    if body.item_id:
        row = store.db().execute("SELECT * FROM feed WHERE id = ? AND bot_id = ?", (body.item_id, bot_id)).fetchone()
        if row and row["kind"] in ("checkin", "message"):
            quoted = f"{row['title']}: {row['body']}" if row["kind"] == "checkin" else row["body"]
    text, note = pastedkeys.hide(text[:4000])
    turn = turns.send(bot_id, text, reply_to={"text": quoted[:20000], "who": "bot"} if quoted else None, note=note,
                      tester=_tester(authorization))
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
        await watcher.push(name, reply, turn.bot_id, urgent=True, kind="done")


class StopBody(BaseModel):
    what: str


@app.post("/api/bots/{bot_id}/choices/{feed_id}/stop")
def stop_question(bot_id: str, feed_id: int, body: StopBody, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    try:
        return {"ok": True, "card": notices.stop(feed_id, body.what)}
    except notices.AnswerError as e:
        raise HTTPException(409, str(e))




class ProactivityBody(BaseModel):
    enabled: bool | None = None
    noticers: dict[str, str] | None = None
    time_sensitive: bool | None = None
    recap_evening_hour: int | None = Field(None, ge=12, le=21)


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
    if body.time_sensitive is not None:
        notices.set_time_sensitive(body.time_sensitive)
    if body.recap_evening_hour is not None:
        notices.set_recap_evening(body.recap_evening_hour)
    return notices.status()


@app.post("/api/proactivity/stopped/{notice_id}/undo")
def undo_stopped_question(notice_id: int, _: str = Depends(require_device)):
    if not notices.undo_stop(notice_id):
        raise HTTPException(404, "That question isn't stopped.")
    return notices.status()




class MailBody(BaseModel):
    action: str
    version: int | None = None
    to: list[str] | None = None
    cc: list[str] | None = None
    subject: str | None = None
    body: str | None = None


@app.post("/api/bots/{bot_id}/mail/{feed_id}")
async def mail_card(bot_id: str, feed_id: int, body: MailBody, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    if body.action not in ("send", "edit", "dismiss", "reopen"):
        raise HTTPException(400, "Unknown action.")
    try:
        if body.action == "dismiss":
            card = mailcard.dismiss(bot_id, feed_id)
        elif body.action == "reopen":
            card = mailcard.reopen(bot_id, feed_id)
        elif body.action == "edit":
            card = mailcard.edit(bot_id, feed_id, body.model_dump(exclude={"action"}, exclude_none=True))
        else:
            card, note = mailcard.send(bot_id, feed_id, body.model_dump(exclude={"action"}, exclude_none=True))
            card = await _send_tapped_mail(bot_id, feed_id, card, note)
    except mailcard.MailError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "card": mailcard.public(card)}


MAIL_TAP_WAIT_SEC = 20


async def _send_tapped_mail(bot_id: str, feed_id: int, card: dict, note: str) -> dict:
    ""


    if not mailcard.hub_sends():
        asyncio.create_task(turns.follow_up(bot_id, note, card.get("session_id"), urgent=False, push=False))
        return card
    task = asyncio.ensure_future(asyncio.to_thread(mailcard.send_from_hub, bot_id, feed_id))
    finished, _ = await asyncio.wait({task}, timeout=MAIL_TAP_WAIT_SEC)
    return task.result() if finished else card




class TextBody(BaseModel):
    action: str
    version: int | None = None
    text: str | None = None


@app.post("/api/bots/{bot_id}/text/{feed_id}")
async def text_card(bot_id: str, feed_id: int, body: TextBody, _: str = Depends(require_device)):
    ""

    _bot(bot_id)
    if body.action not in ("send", "edit", "dismiss", "reopen"):
        raise HTTPException(400, "Unknown action.")
    edits = body.model_dump(exclude={"action"}, exclude_none=True)
    try:
        if body.action == "dismiss":
            card = textcard.dismiss(bot_id, feed_id)
        elif body.action == "reopen":
            card = textcard.reopen(bot_id, feed_id)
        elif body.action == "edit":
            card = textcard.edit(bot_id, feed_id, edits)
        else:
            card = await textcard.send(bot_id, feed_id, edits)
    except textcard.TextError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "card": textcard.public(card)}


@app.post("/internal/chat/follow_up_ask")
async def internal_follow_up_ask(body: MacBody, _: None = Depends(require_internal)):
    ""
    from . import followup
    args = body.args or {}
    return {"ok": True, "result": {"own": await followup.own(*(str(args.get(k) or "") for k in
                                                               ("request", "name", "when", "prompt")),
                                                             zips=(body.bot_id,))}}


@app.post("/internal/chat/sign_in_ask")
async def internal_sign_in_ask(body: MacBody, _: None = Depends(require_internal)):
    ""
    from . import signinask
    args = body.args or {}
    return {"ok": True, "result": {"asks": await signinask.asks_him(str(args.get("reason") or ""),
                                                                   str(args.get("title") or ""), zips=(body.bot_id,))}}


@app.post("/internal/chat/block_check")
async def internal_block_check(body: MacBody, _: None = Depends(require_internal)):
    ""

    from . import signinask
    args = body.args or {}
    task = str(body.origin_task or args.get("task_id") or "")
    if task and signin.open_check(body.bot_id, task) is not None:
        return {"ok": True, "result": {"check": True, "card_open": True}}
    return {"ok": True, "result": {"check": await signinask.names_check(str(args.get("reason") or ""),
                                                                        str(args.get("title") or ""), zips=(body.bot_id,)),
                                   "card_open": False}}


@app.post("/internal/chat/build_check")
async def internal_build_check(body: MacBody, _: None = Depends(require_internal)):
    ""
    from . import buildcheck
    args = body.args or {}
    return {"ok": True, "result": await buildcheck.check(body.bot_id, args, str(body.origin_task or ""),
                                                         str(args.get("request") or ""))}


@app.post("/internal/chat/text_card")
async def internal_text_card(body: MacBody, _: None = Depends(require_internal)):
    ""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id) or not _waits_for_reply(body):
        return {"ok": False, "error": f"Not in a chat with {who.first()}. Put the text in your reply instead."}
    practice = config.practice(bot.id)
    if config.is_sandbox(bot.id) and not practice:
        return {"ok": False, "error": "A test Zip never drafts texts."}
    try:
        offered = await textcard.offer(bot.id, body.session_id, body.args, True, practice=practice)
    except (textcard.TextError, mac.MacError) as e:
        return {"ok": False, "error": str(e), "open_texts": textcard.drafts(bot.id)}
    return {"ok": True, **offered}


@app.post("/internal/chat/result_card")
async def internal_result_card(body: MacBody, _: None = Depends(require_internal)):
    ""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; show the result to {who.first()} in your own chat."}
    try:
        resultcard.offer(bot.id, body.args, _waits_for_reply(body))
    except (resultcard.ResultError, TypeError, ValueError) as e:
        return {"ok": False, "error": str(e) if isinstance(e, resultcard.ResultError) else "That card could not be made."}
    return {"ok": True, "made": made.item_id(bot.id, "result", str(body.args.get("media")))}


@app.post("/internal/chat/mail_card")
async def internal_mail_card(body: MacBody, _: None = Depends(require_internal)):
    ""


    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    in_chat = _waits_for_reply(body)
    if team.in_team_session(body.session_id) or not (in_chat or _board_task(body)):
        return {"ok": False, "error": f"Not in a chat with {who.first()}."}
    try:

        offered = mailcard.offer(bot.id, body.session_id if in_chat else None, body.args, in_chat)
    except mailcard.MailError as e:
        return {"ok": False, "error": str(e), "current_drafts": mailcard.drafts(bot.id)}



    if not in_chat and not offered.get("revised") and not offered.get("settled") \
            and body.origin_task not in _task_drafts_pushed:
        _task_drafts_pushed.add(body.origin_task)
        await watcher.push(f"{bot.name} drafted email", "My drafts are ready in my chat for you to check and send.",
                           bot.id)
    return {"ok": True, **offered}


_task_drafts_pushed: set[str] = set()


def _board_task(body: MacBody) -> bool:
    ""




    return bool(body.origin_task) and not team.in_side_lane(body.session_id)


@app.post("/internal/chat/mail_card_get")
async def internal_mail_card_get(body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    try:
        return {"ok": True, "card": mailcard.for_send(body.bot_id, int(body.args.get("card")))}
    except (mailcard.MailError, TypeError, ValueError) as e:
        return {"ok": False, "error": str(e) if isinstance(e, mailcard.MailError) else "Give the card's number."}


@app.post("/internal/chat/mail_card_done")
async def internal_mail_card_done(body: MacBody, _: None = Depends(require_internal)):
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    try:
        attempt = body.args.get("attempt")
        mailcard.done(body.bot_id, int(body.args.get("card")), str(body.args.get("state") or ""),
                      str(body.args.get("error") or ""), int(attempt) if attempt is not None else None)
    except (mailcard.MailError, TypeError, ValueError):
        return {"ok": False}
    return {"ok": True}


@app.post("/internal/chat/mail_sent")
async def internal_mail_sent(body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    try:
        events.mail_sent(body.bot_id, body.args)
    except (mailcard.MailError, TypeError, ValueError) as e:
        return {"ok": False, "error": str(e) if isinstance(e, mailcard.MailError) else "That send could not be recorded."}
    return {"ok": True}


@app.post("/internal/chat/calendar_created")
async def internal_calendar_created(body: MacBody, _: None = Depends(require_internal)):
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    try:
        events.calendar_created(body.bot_id, body.args)
    except (TypeError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@app.post("/internal/chat/offer_choices")
async def internal_offer_choices(body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; offer choices to {who.first()} in your own chat."}
    try:
        choices.refuse_leave(str(body.args.get("question") or ""), body.args.get("questions"),
                             body.args.get("options"), proposal=body.args.get("proposal_asked") is True,
                             needs_him=lambda q: mac.asks_about_refused_site(bot.id, q))
        return {"ok": True, "result": choices.offer(bot.id, str(body.args.get("question") or ""),
                                                    list(body.args.get("options") or []), _waits_for_reply(body),
                                                    reasoning=str(body.args.get("reasoning") or ""),
                                                    title=choices.clean_title(body.args.get("title")),
                                                    steps=body.args.get("steps", False),
                                                    questions=body.args.get("questions"), pick=body.args.get("pick"),
                                                    origin_task=body.origin_task, session_id=body.session_id,
                                                    about_file=body.args.get("about")
                                                    if isinstance(body.args.get("about"), dict) else None,
                                                    answer_context=str(body.args.get("reasoning") or "")
                                                    if body.args.get("questions") and body.session_id
                                                    and body.session_id != store.session_for(bot.id)
                                                    else "")}
    except choices.ChoiceError as e:
        return {"ok": False, "error": str(e)}




@app.post("/internal/chat/card")
async def internal_card(body: MacBody, _: None = Depends(require_internal)):
    ""

    from . import kit
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; make cards for {who.first()} in your own chat."}
    args = body.args or {}
    action = str(args.get("action") or "post")
    try:
        if action == "post":
            prepared = await asyncio.to_thread(kit.prepare, bot.id, args.get("title"), args.get("blocks"))
            return {"ok": True, **kit.put_up(bot.id, prepared, _waits_for_reply(body))}
        card_id = str(args.get("card") or "")
        if action == "update":
            return {"ok": True, **kit.update(bot.id, card_id, args)}
        if action == "read":
            return {"ok": True, **kit.read(bot.id, card_id)}
        if action == "close":
            return {"ok": True, **kit.close(bot.id, card_id, str(args.get("why") or ""))}
    except kit.KitError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "Use action post, update, read or close."}


class KitCheckBody(BaseModel):
    item: str
    checked: bool


@app.post("/api/bots/{bot_id}/kit/{feed_id}/check")
def kit_check(bot_id: str, feed_id: int, body: KitCheckBody, _: str = Depends(require_device)):
    ""
    from . import kit
    _bot(bot_id)
    try:
        return {"ok": True, "card": kit.check(bot_id, feed_id, body.item, body.checked)}
    except kit.KitError as e:
        raise HTTPException(409, str(e))


class KitAnswerBody(BaseModel):
    button: str | None = None
    answers: dict[str, str] = {}


@app.post("/api/bots/{bot_id}/kit/{feed_id}/answer")
async def kit_answer(bot_id: str, feed_id: int, body: KitAnswerBody, authorization: str | None = Header(None),
                     _: str = Depends(require_device)):
    ""

    from . import kit
    _bot(bot_id)
    desk.read_chat(bot_id, looking.key(authorization))
    try:
        card, send = kit.answer(bot_id, feed_id, body.button, body.answers)
    except kit.KitError as e:
        raise HTTPException(409, str(e))
    device = looking.key(authorization)
    looking.saw(device)
    turn = turns.send(bot_id, send, device=device, tester=_tester(authorization)) if send else None
    return {"ok": True, "card": card, "turn_id": turn.id if turn else None}


def _waits_for_reply(body: MacBody) -> bool:
    ""

    turn = turns.current(body.bot_id)
    running = bool(turn and not turn.done) or turns.in_internal(body.bot_id)
    return bool(running and body.session_id and body.session_id == store.session_for(body.bot_id))




class TodoAddBody(BaseModel):
    title: str
    bot_id: str | None = None
    due: str | None = None


class TodoOrderBody(BaseModel):
    order: list[str]
    shown: int | None = None
    groups: dict[str, Literal["today", "tomorrow", "week", "later"]] | None = None


class TodoLineBody(BaseModel):
    done: bool


def _todo_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except todo.TodoError as e:
        raise HTTPException(400, str(e))


@app.get("/api/todo")
def todo_list(_: str = Depends(require_device)):
    todo.sync(force=True)
    return todo.view()


@app.post("/api/todo/lines")
def todo_add(body: TodoAddBody, _: str = Depends(require_device)):
    ""
    line = _todo_call(todo.add_his, body.title, body.bot_id, body.due)
    return {"line": line, "list": todo.view()}


@app.put("/api/todo/order")
async def todo_order(body: TodoOrderBody, _: str = Depends(require_device)):
    ""



    _todo_call(todo.save_order, body.order, body.shown, body.groups)
    return todo.view()


@app.get("/api/todo/lines/{line_id}")
def todo_details(line_id: str, _: str = Depends(require_device)):
    ""
    out = todo.details(line_id)
    if out is None:
        raise HTTPException(404, "That line is gone.")
    todo.seen(line_id)
    return out


@app.post("/api/todo/lines/{line_id}")
def todo_check(line_id: str, body: TodoLineBody, _: str = Depends(require_device)):
    ""
    line = _todo_call(todo.finish, line_id, todo.YOU, body.done)
    return {"line": line, "list": todo.view()}




class ReadMarkBody(BaseModel):
    chat: str
    at: float


@app.get("/api/read-marks")
def read_marks(_: str = Depends(require_device)):
    return {"marks": readmarks.changed()}


@app.post("/api/read-marks")
def read_mark(body: ReadMarkBody, _: str = Depends(require_device)):
    ""
    try:
        return {"chat": body.chat, "at": readmarks.read(body.chat, body.at)}
    except readmarks.ReadMarkError as e:
        raise HTTPException(400, str(e))


@app.post("/internal/chat/todo_list")
async def internal_todo_list(body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    try:
        out = await asyncio.to_thread(todo.tool, body.bot_id, body.args or {}, turns.in_internal(body.bot_id),
                                      _waits_for_reply(body))
    except todo.TodoError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "text": out["text"]}




@app.post("/internal/chat/session_card")
async def internal_session_card(body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; keep the card in your own chat with {who.first()}."}
    args = body.args or {}
    try:
        if args.get("rows") is None:
            card = await asyncio.to_thread(sessioncard.current, body.bot_id)
        else:
            card = await asyncio.to_thread(sessioncard.save, body.bot_id, args.get("title"), args.get("rows"),
                                           args.get("expected_revision"))
    except sessioncard.CardError as e:
        return {"ok": False, "error": str(e), "card": sessioncard.describe(e.card) if e.card else None}
    except sqlite3.Error:
        return {"ok": False, "error": "I couldn't read the board just now. Try again in a moment."}
    return {"ok": True, "card": card, "text": sessioncard.describe(card)}


@app.post("/internal/chat/work_project")
async def internal_work_project(body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    try:
        return await asyncio.to_thread(workprojects.ask, body.args or {})
    except workprojects.ProjectError as e:
        return {"ok": False, "error": str(e)}


@app.post("/internal/chat/code_session")
async def internal_code_session(body: MacBody, _: None = Depends(require_internal)):
    ""
    from . import codesessions
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if bot is None:
        raise HTTPException(404, "Unknown bot.")
    if config.is_sandbox(bot.id):
        return {"ok": False, "error": "A test Zip doesn't start coding sessions."}
    try:
        return {"ok": True, "result": await asyncio.to_thread(codesessions.call, bot.id, bot.name, body.args or {},
                                                              body.origin_task)}
    except (codesessions.SessionError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e)}




@app.post("/internal/chat/offer_project")
async def internal_offer_project(body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; offer the project to {who.first()} in your own chat."}
    try:
        result = await asyncio.to_thread(project.offer, bot.id, body.args, _waits_for_reply(body))
        return {"ok": True, "result": result}
    except project.ProjectError as e:
        return {"ok": False, "error": str(e)}


class ProjectBody(BaseModel):
    action: str
    resources: list | None = None
    workspace: dict | None = None
    skipped: list | None = None


@app.post("/api/bots/{bot_id}/project/{feed_id}")
async def project_card(bot_id: str, feed_id: int, body: ProjectBody, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    try:
        if body.action == "get":
            card = project.card(bot_id, feed_id)
        elif body.action == "save":
            card = await asyncio.to_thread(project.save, bot_id, feed_id, body.resources or [], body.workspace)
        elif body.action == "opened":
            card = project.opened(bot_id, feed_id, body.skipped or [])
        elif body.action == "open_on_mac":
            await project.open_on_mac(bot_id, feed_id)
            card = project.card(bot_id, feed_id)
        else:
            raise HTTPException(400, "Unknown action.")
    except project.ProjectError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "card": card}




class SignInCardBody(BaseModel):
    outcome: str


@app.post("/api/bots/{bot_id}/login-requests/{feed_id}")
def close_signin_card(bot_id: str, feed_id: int, body: SignInCardBody, _: str = Depends(require_device)):
    _bot(bot_id)
    if body.outcome != "declined":
        raise HTTPException(400, "Use Save Login or Sign In Myself.")
    try:
        card = signin.card_for(bot_id, feed_id)
        if card.get("purpose") == "challenge" and card.get("state") == "open":
            screen.hand_back(bot_id)
        return {"ok": True, "card": signin.resolve(bot_id, feed_id, "declined")}
    except signin.AskError as e:
        raise HTTPException(409, str(e))


def _google_to_mac(body: MacBody, url: str) -> str | None:
    ""

    page = signin.google_return_page(url, str(body.args.get("sso_url") or ""))
    if page and consent.base(consent.site_of(page)) == "google.com":
        return None
    return mac.google_route(body.bot_id, body.session_id or "", page, body.origin_task)


@app.post("/internal/chat/signin_recovery")
async def internal_signin_recovery(body: MacBody, _: None = Depends(require_internal)):
    _bot(body.bot_id)
    try:
        url = validate.secure_url(str(body.args.get("url") or ""))
    except HTTPException:
        return {"ok": False, "error": "Give the sign-in page's full address, starting with https://."}
    google = signin.google_wall(url, str(body.args.get("sso_url") or ""))
    if google:


        to_mac = _google_to_mac(body, url) if google == "sign_in" else None
        return {"ok": True, "reason": None, "instruction": to_mac or signin.google_note(consent.site_of(url)),
                "challenge": False}
    key = str(body.args.get("recovery_key") or body.session_id or "default")
    reason, instruction = await asyncio.to_thread(signin.recover, body.bot_id, url, key,
                                                  str(body.args.get("sso_url") or ""))
    challenge = bool(instruction and consent.login_recovery(body.bot_id, key, consent.site_of(url)).get("kind") == "challenge")
    return {"ok": True, "reason": reason, "instruction": instruction, "challenge": challenge}


@app.post("/internal/x/post")
async def internal_x_post(body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    from . import xapi
    try:
        result = await asyncio.to_thread(xapi.post, body.bot_id, str(body.args.get("text") or ""),
                                         str(body.args.get("reply_id") or ""), bool(body.args.get("test")))
    except xapi.Refused as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "result": result}


@app.post("/internal/chat/ask_sign_in")
async def internal_ask_sign_in(body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; ask {who.first()} to sign in from your own chat."}
    try:
        purpose = signin.purpose_of(body.args.get("purpose", "signin"))
    except signin.AskError as e:
        return {"ok": False, "error": str(e)}
    try:
        url = validate.secure_url(str(body.args.get("url") or ""))
    except HTTPException:
        return {"ok": False, "error": "Give the sign-in page's full address, starting with https://."}
    if body.args.get("on_mac") and purpose == signin.Purpose.signin:
        return await _ask_sign_in_on_mac(bot, body, url)


    from . import apps, keys, service_keys
    google = signin.google_wall(url, str(body.args.get("sso_url") or ""), purpose)
    if google:


        if google == "files":
            if apps.connected_natively("google_drive", bot_id=bot.id):
                return {"ok": True, "result": f"No card went to {who.first()}. Google Drive is connected, so read "
                                              "this file with the drive tool instead of the website."}
            told = await accounts.need_app(bot.id, "google_drive", str(body.args.get("why") or ""),
                                           body.session_id or None, origin_task=body.origin_task)
            if told:
                return {"ok": True, "result": told + " Once it's connected, read the file with the drive tool, not "
                                                     "the website. Google doesn't allow signing in on your browser."}
        to_mac = _google_to_mac(body, url) if google == "sign_in" else None
        return {"ok": True, "result": to_mac or signin.google_note(consent.site_of(url))}
    if (purpose == signin.Purpose.signin and consent.base(consent.site_of(url)) in ("x.com", "twitter.com")
            and service_keys.has(bot.id, "x")):
        return {"ok": True, "result": "You have X API keys, so you don't need to sign in to X. Post with post_to_x; "
                                      "it needs no browser. No card went to " + who.first() + "."}
    key_app = keys.for_site(consent.base(consent.site_of(url))) if purpose == signin.Purpose.signin else None
    if key_app:
        name = apps.BY_ID[key_app].name
        if apps.connected_natively(key_app, bot_id=bot.id):
            return {"ok": True, "result": f"{name} is connected in Zero. Use the {key_app} tool instead of the "
                                          "website; it reads the same data."}
        told = await accounts.need_app(bot.id, key_app, str(body.args.get("why") or ""), body.session_id or None,
                                       origin_task=body.origin_task)
        if told:
            return {"ok": True, "result": told + f" Once it's connected, use the {key_app} tool, not the website."}
    if purpose == signin.Purpose.challenge:
        mac.note_wall(bot.id, url)
        to_mac = mac.challenge_route(bot.id, body.session_id or "", url, body.origin_task)
        if to_mac:
            return {"ok": True, "result": to_mac}
        if signin.check_failed_here(bot.id, consent.site_of(url)):
            return {"ok": True, "result": signin.check_failed_note(url)}
    if purpose == signin.Purpose.signin:
        from . import shared_sessions
        try:
            loaded = await asyncio.to_thread(shared_sessions.before_ask, bot.id, url)
        except Exception:
            log.warning("Shared sign-in lookup was deferred for %s", bot.id)
            loaded = False
        if loaded:
            return {"ok": True, "result": "I loaded a shared sign-in. Open the page again and check. "
                                          "Ask again only if it still shows a sign-in page."}
    sso_url = str(body.args.get("sso_url") or "")
    if purpose == signin.Purpose.signin and not consent.sign_in_provider(sso_url) \
            and await asyncio.to_thread(logins.share_for_site, bot.id, url):
        return {"ok": True, "result": "A saved sign-in is now available. Use browser_vault_list and "
                                      "browser_vault_fill, then check the page before asking again."}
    try:
        feed_id, result = signin.ask(bot.id, url, str(body.args.get("why") or ""), body.session_id or None,
                                    purpose=purpose, recovery_key=str(body.args.get("recovery_key") or "") or None,
                                    origin_task=body.origin_task, sso_url=sso_url,
                                    site_name=str(body.args.get("site_name") or ""))
    except signin.AskError as e:
        return {"ok": False, "error": str(e)}
    if feed_id:
        site = consent.site_of(url)
        why = " ".join(str(body.args.get("why") or "").split())[:150]
        if purpose == signin.Purpose.challenge:
            await watcher.push(f"{bot.name} needs your help",
                               signin.check_action(site, str(body.args.get("site_name") or "")) + ".", bot.id)
        else:
            await watcher.push(f"{bot.name} needs a login", f"Can you sign in to {site}" + (f" so I can {why}" if why else "")
                               + "?", bot.id)
    return {"ok": True, "result": result}


@app.post("/internal/chat/ask_wake_mac")
async def internal_ask_wake_mac(body: MacBody, _: None = Depends(require_internal)):
    ""
    from . import macwake
    bot = _bot(body.bot_id)
    if config.is_sandbox(bot.id):
        return {"ok": False, "error": "A test Zip doesn't use the Mac."}
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; ask {who.first()} from your own work."}
    try:
        return {"ok": True, "result": await macwake.ask(bot.id, body.session_id or "", body.origin_task,
                                                       str(body.args.get("question") or ""))}
    except macwake.WakeError as e:
        return {"ok": False, "error": str(e)}


class WakeAnswerBody(BaseModel):
    wake: bool


@app.post("/api/bots/{bot_id}/wake/{feed_id}")
async def wake_answer(bot_id: str, feed_id: int, body: WakeAnswerBody, authorization: str | None = Header(None),
                      _: str = Depends(require_device)):
    ""

    from . import macwake
    _bot(bot_id)
    desk.read_chat(bot_id, looking.key(authorization))
    try:
        card = macwake.answer_from_notification(bot_id, feed_id, body.wake)
    except (macwake.WakeError, choices.ChoiceError) as e:
        raise HTTPException(409, str(e))
    words = card.get("send")
    if words:
        turns.send(bot_id, words, device=looking.key(authorization), tester=_tester(authorization))
    return {"ok": True}


async def _ask_sign_in_on_mac(bot, body: MacBody, url: str) -> dict:
    ""



    if not mac.online():
        return {"ok": False, "error": f"{who.mac()} isn't connected right now, so a sign-in there can't start. Ask "
                                      f"without on_mac if the site works in your browser, or tell {who.first()} "
                                      "what you need."}
    try:
        feed_id, result = signin.ask(bot.id, url, str(body.args.get("why") or ""), body.session_id or None,
                                    origin_task=body.origin_task, place="mac")
    except signin.AskError as e:
        return {"ok": False, "error": str(e)}
    if feed_id:
        why = " ".join(str(body.args.get("why") or "").split())[:150]
        await watcher.push(f"{bot.name} needs you on your Mac", f"Can you sign in to {consent.site_of(url)} on your Mac"
                           + (f" so I can {why}" if why else "") + "?", bot.id)
    return {"ok": True, "result": result}


@app.post("/internal/chat/browser_state")
async def internal_browser_state(body: MacBody, _: None = Depends(require_internal)):
    _bot(body.bot_id)
    from . import shared_sessions, signin_access
    try:
        await asyncio.to_thread(shared_sessions.sync, body.bot_id, str(body.args.get("phase") or ""),
                                str(body.args.get("url") or ""))
    except Exception as exc:
        log.warning("shared sign-in cleanup failed bot=%s site=%s step=%s error=%s", body.bot_id,
                    signin_access.site_label(str(body.args.get("url") or "")),
                    str(body.args.get("phase") or "(unknown)"), type(exc).__name__)
        return {"ok": False, "error": "The shared sign-in will be checked again on the next browser step."}
    return {"ok": True}


@app.post("/internal/chat/login_access")
def internal_login_access(body: MacBody, _: None = Depends(require_internal)):
    _bot(body.bot_id)
    from . import signin_access
    with signin_access.lock:
        args = body.args or {}
        if args.get("site"):
            return {"ok": True, "allowed": signin_access.allowed(body.bot_id, str(args["site"]))}
        source_path = Path(str(args.get("source") or "")).resolve()
        source = next((b.id for b in config.BOTS if (screen.profile_home(b.id) / "vault").resolve() == source_path), None)
        if source is None:
            return {"ok": True, "allowed": False}
        try:
            logins.authorized_item(body.bot_id, source, str(args.get("handle") or ""))
        except Exception:
            return {"ok": True, "allowed": False}
        return {"ok": True, "allowed": True}


@app.post("/internal/chat/login_prepare")
async def internal_login_prepare(body: MacBody, _: None = Depends(require_internal)):
    _bot(body.bot_id)
    from . import signin_access
    try:

        refreshed = await asyncio.to_thread(logins.ensure_for_bot, body.bot_id)
    except Exception:
        return {"ok": False}
    return {"ok": refreshed is True}


@app.post("/internal/chat/login_record")
def internal_login_record(body: MacBody, _: None = Depends(require_internal)):
    _bot(body.bot_id)
    from . import signin_access
    args = body.args or {}
    source = str(screen.profile_home(body.bot_id) / "vault")
    if source != str(args.get("source") or ""):
        return {"ok": False}
    vault = logins._store(body.bot_id)
    item = next((m for m in vault.list_items() if m.kind == "login" and m.id == args.get("handle")), None)
    if item is None or not item.origin:
        return {"ok": False}
    signin_access.ensure(item.origin, body.bot_id)
    return {"ok": True}


WALLET_OPS = {"list", "use_card", "use_id", "ask", "report", "redeem", "unfill", "step", "submit", "clicked", "active",
              "fill_check", "your_info"}


@app.post("/internal/wallet/{op}")
async def internal_wallet(op: str, body: MacBody, _: None = Depends(require_internal)):
    ""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if op not in WALLET_OPS:
        raise HTTPException(404, "Unknown action.")
    a = body.args or {}
    try:
        if op == "list":
            return {"ok": True, "result": await asyncio.to_thread(wallet.for_bot, bot.id)}
        if op == "redeem":
            total = a.get("total_cents")
            out = await asyncio.to_thread(purchases.redeem, bot.id, str(a.get("token") or ""),
                                          str(a.get("handle") or ""), str(a.get("origin") or ""),
                                          int(total) if isinstance(total, (int, float)) else None)
            return {"ok": True, "result": out}
        if op == "unfill":
            return {"ok": True, "result": await asyncio.to_thread(purchases.unfill, bot.id,
                                                                  str(a.get("purchase") or ""))}
        if op == "step":
            total = a.get("total_cents")
            return {"ok": True, "result": await asyncio.to_thread(
                purchases.step, bot.id, str(a.get("origin") or ""),
                int(total) if isinstance(total, (int, float)) and total >= 0 else None)}
        if op == "submit":
            total = a.get("total_cents")
            done = await asyncio.to_thread(purchases.submit, bot.id, str(a.get("origin") or ""),
                                           int(total) if isinstance(total, (int, float)) and total >= 0 else None)
            if done and done.get("error"):
                return {"ok": True, "result": {"error": done["error"]}}
            return {"ok": True, "result": {"purchase_id": done["id"]} if done else None}
        if op == "clicked":
            done = await asyncio.to_thread(purchases.clicked, bot.id, str(a.get("origin") or ""), bool(a.get("ok")))
            return {"ok": True, "result": {"purchase_id": done["id"], "state": done["state"]} if done else None}
        if op == "active":
            return {"ok": True, "result": purchases.active(bot.id, str(a.get("origin") or ""))}
        if op == "fill_check":
            item = await asyncio.to_thread(wallet.by_handle, bot.id, str(a.get("handle") or ""))
            return {"ok": True, "result": {"known": item is not None, "kind": item["kind"] if item else None,
                                           "allowed": bool(item and bot.id in item["zips"] and
                                                           item["handles"].get(bot.id) == a.get("handle"))}}
        if op == "report":
            return {"ok": True, "result": await asyncio.to_thread(
                purchases.report, bot.id, str(a.get("purchase") or ""), str(a.get("outcome") or ""))}
        if op == "your_info":
            from . import yourinfo
            try:
                return {"ok": True, "result": await asyncio.to_thread(
                    yourinfo.save_from_zip, bot.id, str(a.get("field") or ""), a.get("value"))}
            except yourinfo.InfoError as e:
                return {"ok": False, "error": str(e)}
        if op == "ask":
            feed_id, text = await asyncio.to_thread(wallet.ask, bot.id, str(a.get("kind") or ""),
                                                    str(a.get("why") or ""), body.session_id or None,
                                                    str(a.get("id_type") or ""))
            if feed_id:
                why = " ".join(str(a.get("why") or "").split())[:150]
                await watcher.push(f"{bot.name} needs something saved",
                                   f"Can you add {wallet.ask_name(a['kind'], str(a.get('id_type') or ''))}"
                                   + (f" so I can {why}" if why else "") + "?", bot.id)
            return {"ok": True, "result": text}
        return await _wallet_use(bot, op, a, body.session_id)
    except (wallet.WalletError, purchases.Refused, purchases.NotFound) as e:
        return {"ok": False, "error": str(e)}


async def _wallet_use(bot: config.Bot, op: str, a: dict, session_id: str) -> dict:
    ""
    kind = "card" if op == "use_card" else "identity"
    item = await asyncio.to_thread(wallet.find_for_bot, bot.id, str(a.get("item") or ""), kind)
    origin = wallet.origin_of(str(a.get("site") or ""))
    page = str(a.get("page_origin") or "")
    if page.startswith("https://") and wallet.same_site(page, origin):
        origin = wallet.origin_of(page)
    if kind == "card":
        try:
            amount = purchases._cents(a.get("amount"))
        except (TypeError, ValueError):
            amount = 0
        if not 0 < amount <= 10_000_000:
            return {"ok": False, "error": "Give the amount in dollars, the total you expect to pay."}
        currency = str(a.get("currency") or "USD").strip().upper()[:3] or "USD"
        what = " ".join(str(a.get("what") or "").split())[:120]
        if not what:
            return {"ok": False, "error": "Say what you're buying, in a few words."}
    else:
        amount, currency, what = 0, "USD", " ".join(str(a.get("why") or "").split())[:120]
    out = await asyncio.to_thread(purchases.request, bot.id, item, origin, amount, currency, what, session_id or None)
    site, first = wallet.site_name(origin), who.first()
    p = purchases.get(out["purchase_id"])
    if out["new_card"]:
        title = f"{bot.name} needs your ID" if kind == "identity" else f"{bot.name} wants to pay"
        await watcher.push(title, purchases.pay_title(p, item), bot.id)
    asked = f"to use {item['label']} on {site}" if kind == "identity" else \
        f"to pay {purchases.money(amount, currency)} at {site} with {item['label']}"
    return {"ok": True, "result": {
        "approved": False, "purchase": out["purchase_id"],
        "next": (f"{first} has a card in your chat {asked}. Stop here. {who.quiet_card()} A note arrives here when "
                 f"{first} answers.")}}


class CodeCardBody(BaseModel):
    code: str | None = None
    declined: bool = False


@app.post("/api/bots/{bot_id}/code-requests/{feed_id}")
def answer_code_card(bot_id: str, feed_id: int, body: CodeCardBody, _: str = Depends(require_device)):
    ""
    _bot(bot_id)
    try:
        if body.declined:
            return {"ok": True, "card": twofactor.decline(bot_id, feed_id)}
        return {"ok": True, "card": twofactor.submit(bot_id, feed_id, body.code or "")}
    except twofactor.CodeError as e:
        raise HTTPException(409, str(e))


@app.post("/internal/chat/ask_for_code")
async def internal_ask_for_code(body: MacBody, _: None = Depends(require_internal)):
    ""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; ask {who.first()} for the code from your own chat."}
    try:
        feed_id, result = twofactor.ask(bot.id, str(body.args.get("site") or ""), str(body.args.get("why") or ""),
                                        body.session_id or None,
                                        body.args.get("digits") if isinstance(body.args.get("digits"), int) else None,
                                        str(body.args.get("url") or ""))
    except twofactor.CodeError as e:
        return {"ok": False, "error": str(e)}
    if feed_id:
        name = twofactor._site_name(str(body.args.get("site") or ""))
        await watcher.push(f"{bot.name} needs a code", f"{name} sent you a code. Enter it so I can carry on.", bot.id,
                           urgent=True, now=True)
    return {"ok": True, "result": result}


@app.post("/internal/chat/take_code")
async def internal_take_code(body: MacBody, _: None = Depends(require_internal)):
    ""

    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; the code is for your own chat with {who.first()}."}
    code = twofactor.take(bot.id, str(body.args.get("site") or ""))
    if not code:
        return {"ok": False, "error": f"{who.first()} hasn't entered a code for this website yet, or it is more than ten "
                                      "minutes old. If the page is the right one, ask again with ask_for_code."}
    return {"ok": True, "code": code}


@app.post("/internal/chat/close_code")
async def internal_close_code(body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    try:
        twofactor.retire(bot.id, str(body.args.get("site") or ""))
    except twofactor.CodeError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "result": "The code card is closed and gone from the chat."}


@app.post("/internal/chat/close_sign_in")
async def internal_close_sign_in(body: MacBody, _: None = Depends(require_internal)):
    ""

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


@app.post("/internal/chat/hand_over")
async def internal_hand_over(body: MacBody, _: None = Depends(require_internal)):
    ""

    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": f"You are answering a teammate; hand {who.first()} a step from your own chat."}
    a = body.args
    place = str(a.get("place") or "browser")
    if place == "mac":
        try:
            mac._require(bot.id, body.session_id)
        except mac.MacError:
            return {"ok": False, "error": f"You aren't using {who.mac()} in this conversation now. Call mac_start "
                                          "first, or give place browser for your own browser."}
        refused = mac.refused_step(bot.id, body.session_id, str(a.get("action") or "")) \
            if a.get("kind") == "takeover" else ""
        if refused:
            return {"ok": False, "error": refused}
    files = a.get("files") if isinstance(a.get("files"), list) else []
    try:
        feed_id, result = signin.park(bot.id, body.session_id or None, str(a.get("kind") or ""),
                                      str(a.get("action") or ""), url=str(a.get("url") or ""), place=place,
                                      app=str(a.get("app") or ""), done=a.get("done"), expect=str(a.get("expect") or ""),
                                      files=files, job=str(a.get("job") or ""))
    except signin.AskError as e:
        return {"ok": False, "error": str(e)}
    if place == "mac":
        mac.hand_over(bot.id, body.session_id, feed_id)
    action = signin.card_for(bot.id, feed_id).get("why") or ""
    await watcher.push(f"{bot.name} needs you", action[:1].upper() + action[1:] + ".", bot.id)
    return {"ok": True, "result": result}


FROZE = "My tools froze twice in a row in one reply."



RULES_MISSING = "I could not load my rules for this run, so I did nothing. This needs a fix before I try again."


@app.post("/internal/chat/rules_missing")
async def internal_rules_missing(body: MacBody, _: None = Depends(require_internal)):
    ""


    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    task = str(body.args.get("task") or "")
    try:
        task = validate.task_id(task) if task else ""
    except HTTPException:
        task = ""
    log.warning("%s could not load its rules, so its run did nothing (first tool refused: %s, board task: %s)",
                bot.id, str(body.args.get("tool") or "")[:60], task or "none")
    if task:
        ok, detail = await asyncio.to_thread(board.block, task, RULES_MISSING)
        if not ok:
            log.warning("could not block %s after its run had no rules: %s", task, detail[:200])
    return {"ok": True}


@app.post("/internal/chat/stuck")
async def internal_stuck(body: MacBody, _: None = Depends(require_internal)):
    ""


    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    tool = str(body.args.get("tool") or "")
    try:
        problems.report(bot.id, {"kind": "break", "concerns_zip": "self", "what_happened": FROZE,
                                 "evidence": f"Tool: {tool[:60]}. {str(body.args.get('error') or '')[:300]}"})
    except problems.ProblemError:
        log.warning("could not file the frozen-tools report for %s", bot.id)
    return {"ok": True}


@app.post("/internal/chat/close_card")
async def internal_close_card(body: MacBody, _: None = Depends(require_internal)):
    ""


    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if team.in_team_session(body.session_id):
        return {"ok": False, "error": "You are answering a teammate; close cards from your own chat."}
    if body.args.get("draft_card") is not None:
        return _close_draft(bot, body.args)
    if chatquestions.is_card_number(body.args.get("request")):
        return chatquestions.close_own(bot.id, body.args["request"], body.args.get("why"))
    if chatquestions.is_question_number(body.args.get("request")):
        return await chatquestions.settle(bot.id, body.args["request"], body.args.get("why"), body.session_id)
    try:
        req = int(body.args.get("request"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Give the card's request number."}
    withdraw = getattr(consent, "withdraw", None)
    if bot.id == "default":
        return _lead_closes(bot, req, body.args.get("why"), withdraw)
    r = withdraw(bot.id, req) if withdraw else None
    if not r:
        return _already_settled(req) or {"ok": False, "error": f"Request {req} is not one of your cards still waiting "
                                                              "in this chat. For a mail draft, give its draft_card."}


    log.info("%s closed its own card %s (%s)", bot.id, req, (r.get("kind") or "action"))
    return {"ok": True, "result": f"Request {req} is closed and gone from {who.possessive()} chat; nothing was "
                                  "sent or done. No need to mention it unless it matters to your answer."}


def _already_settled(req: int) -> dict | None:
    ""

    row = consent.get_request(req)
    if not row or row.get("status") == "pending":
        return None
    return {"ok": True, "result": f"Request {req} is already closed or answered, so there is nothing to do."}


def _lead_closes(bot, req: int, why, withdraw) -> dict:
    ""


    row = consent.get_request(req)
    own_chat_card = bool(row and row.get("bot_id") == bot.id and not row.get("task_id"))
    why = " ".join(str(why or "").split())[:300]
    if not own_chat_card and not why:
        return {"ok": False, "error": "Say in why what shows it's settled (Sent has the email, he answered "
                                      "elsewhere). Close another Zip's card only with that proof."}
    try:
        r = withdraw(bot.id, req, any_zip=True) if withdraw else None
    except TypeError:
        r = withdraw(bot.id, req)
    if not r:
        return _already_settled(req) or {"ok": False, "error": f"Request {req} is not an okay card still waiting. For a "
                                                              "mail draft, give its draft_card."}
    owner = r["bot_id"]
    log.info("%s closed card %s from %s (%s)", bot.id, req, owner, r.get("kind") or "action")
    if own_chat_card:
        return {"ok": True, "result": f"Request {req} is closed and gone from {who.possessive()} chat; nothing was "
                                      "sent or done. No need to mention it unless it matters to your answer."}
    note = (f"{bot.name} closed your card because it is already settled: {why}. Nothing was done from it: "
            f"{watcher._card_what(r)}. Don't try it again another way, and don't ask {who.first()} about it.")
    if r.get("task_id"):
        ok, _out = board.answer_block(r["task_id"], note)
        if not ok:
            board.comment(r["task_id"], note)
    elif owner != bot.id:
        store.add_card_note(owner, note)
    return {"ok": True, "result": f"Request {req} is closed. {who.first()} wasn't told, and nothing was sent or "
                                  "done. Don't mention it unless he asks."}


def _close_draft(bot, args: dict) -> dict:
    ""


    owner = bot.id
    try:
        if bot.id == "default":
            row = store.db().execute("SELECT bot_id FROM feed WHERE id=? AND kind=?",
                                     (int(args["draft_card"]), mailcard.KIND)).fetchone()
            owner = row["bot_id"] if row else bot.id
        why = mailcard._one_line(args.get("why"), 200)
        if owner != bot.id and not why:
            return {"ok": False, "error": "Say in why what shows it's settled (Sent has the email, he answered "
                                          "elsewhere). Close another Zip's draft only with that proof."}
        card, closed_now = mailcard.close(owner, int(args["draft_card"]))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Give the draft_card number."}
    except mailcard.MailError:
        return {"ok": False, "error": f"Draft {args['draft_card']} is not one of your drafts. A teammate closes its own."}
    if card.get("state") != "dismissed":
        return {"ok": False, "error": f"That draft is {card.get('state')}, so it can't be closed."}
    if owner != bot.id:
        store.add_card_note(owner, f"{bot.name} closed your draft \"{card.get('subject', '')[:80]}\" because it is "
                                   f"already settled: {why}. Nothing was sent. Don't send it another way, and don't "
                                   f"ask {who.first()} about it.")
        return {"ok": True, "result": f"That draft is closed. {who.first()} wasn't told, and nothing was sent. It is "
                                      "still in the mailbox's Drafts folder."}
    store.add_feed("signin_decision", bot.id, None, quiet.ended("not_sent", card["subject"]),
                   why or f"{bot.name} closed this draft.", written_by="hub", written_for="record")
    if closed_now and mailcard.replaced_by_sent(bot.id, int(args["draft_card"]), card, args.get("replaced_by")):



        mailcard.drop_drafts_soon([(bot.id, card)])
    return {"ok": True, "result": f"That draft is closed and gone from {who.possessive()} chat; nothing was sent. It is "
                                  "still in the mailbox's Drafts folder."}


@app.post("/internal/pages/{op}")
def internal_pages(op: str, body: MacBody, _: None = Depends(require_internal)):
    ""

    from . import pages
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    if config.is_sandbox(bot.id):
        return {"ok": False, "error": "Test Zips don't put pages online."}
    try:
        return {"ok": True, "result": pages.handle(bot.id, {**body.args, "action": op})}
    except pages.PageError as e:
        return {"ok": False, "error": str(e)}
    except httpx.HTTPError:
        log.warning("share_page %s failed for %s", op, bot.id, exc_info=True)
        return {"ok": False, "error": "Netlify could not be reached. Nothing changed; try again in a minute."}




@app.post("/internal/team/{op}")
async def internal_team(op: str, body: MacBody, _: None = Depends(require_internal)):
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    a = body.args
    try:
        if op == "report_problem":
            result = await asyncio.to_thread(problems.report, bot.id, a, body.session_id)
            if result.pop("told", False):
                await problems.tell(result["id"])
            return {"ok": True, "result": result}
        if op == "context":
            message = str(a.get("message") or "")[:4000]
            from . import firstline
            guide = None if a.get("work") else firstline.guide(bot.id, body.session_id, message)
            from . import topics, turnsummary, turns
            active = turns.current(bot.id)
            summary_guide = turnsummary.GUIDE + " " + topics.GUIDE if active and active.typed else None
            if summary_guide and buzz.RULE_WORDS.search(message):
                summary_guide += " " + buzz.rule_guide()
            from . import todo
            todo_part = todo.asked_about(bot.id, message) if active and active.typed else None
            if todo_part:
                summary_guide = (summary_guide + "\n\n" if summary_guide else "") + todo_part

            beside = sum(len(p) + 2 for p in (guide, summary_guide) if p)
            ctx = await asyncio.to_thread(team.context_for, bot.id, body.session_id, bool(a.get("first")),
                                          bool(a.get("work")), message, body.sent_session, beside)






            from . import asked
            resumed = asked.take_resume(body.origin_task or "") if a.get("first") and a.get("work") else ""
            rest = "\n\n".join(p for p in (resumed, ctx, guide, summary_guide) if p)
            chat = None
            if a.get("scheduled") and a.get("first") and not a.get("chat_hook"):
                from . import recentchat
                room = recentchat.BLOCK_MAX - len(rest) - 2
                chat = await recentchat.block(bot.id, limit=max(room, recentchat.SUMMARY_FLOOR))
            return {"ok": True, "result": "\n\n".join(p for p in (chat, rest) if p)}
        if op == "right_now":

            from . import rightnow
            return {"ok": True, "result": await asyncio.to_thread(
                rightnow.for_turn, bot.id, body.session_id, bool(a.get("first")), bool(a.get("work")),
                body.sent_session)}
        if op == "standing":


            from . import rulereach, standing
            asked = str(a.get("asked") or "")
            picked = await rulereach.for_turn(bot.id, body.session_id, bool(a.get("first")), bool(a.get("work")),
                                              str(a.get("task") or ""), asked, str(a.get("instructions") or ""))
            closed = await asyncio.to_thread(standing.closed_text, bot.id) if asked else ""

            from . import yourinfo
            info = await asyncio.to_thread(yourinfo.for_turn, bot.id, body.session_id, bool(a.get("first")),
                                           bool(a.get("kept", True)), body.sent_session)
            block = await asyncio.to_thread(
                standing.for_turn, bot.id, body.session_id, bool(a.get("first")), bool(a.get("work")),
                body.sent_session, bool(a.get("kept", True)), picked,
                standing.MAX_CHARS - sum(len(x) + 2 for x in (closed, info) if x), not asked)
            return {"ok": True, "result": "\n\n".join(p for p in (closed, info, block) if p)}
        if op == "closed_topics":

            from . import standing
            return {"ok": True, "result": await asyncio.to_thread(standing.closed_text, bot.id)}
        if op == "voice_guide":

            from . import standing, zipvoice
            return {"ok": True, "result": await asyncio.to_thread(standing.displaced, bot.id, zipvoice.TEXT)}
        if op == "recent_chat":

            from . import recentchat
            return {"ok": True, "result": await recentchat.block(bot.id, limit=recentchat.BLOCK_MAX)}
        if op == "ask":
            title = choices.clean_title(a.get("title"))
            result = await team.ask(bot.id, str(a.get("bot") or ""), str(a.get("question") or ""), body.session_id,
                                    bool(a.get("work")), title=title, origin_task=body.origin_task)

            return {"ok": True, "result": result, "pending": team.waiting_on(bot.id, body.session_id)}
        if op == "answers":

            return {"ok": True, "result": team.take_answers(bot.id, body.session_id)}
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
        if op == "hand_offs":

            return {"ok": True, "result": facts.hand_offs(str(a.get("text") or "")[:20000],
                                                          str(a.get("old") or "")[:40000])}
        if op == "recall":
            return {"ok": True, "result": await asyncio.to_thread(facts.recall, str(a.get("query") or "")[:300],
                                                                  bot.id, body.session_id or "")}
        if op == "course_status":

            unit = re.search(r"\d+", str(a.get("unit") or ""))
            return {"ok": True, "result": await asyncio.to_thread(school.course_status, str(a.get("course") or "")[:100],
                                                                  int(unit.group(0)) if unit else None)}
        if op == "fix_school_date":


            from . import schoolcheck
            if bot.id not in ("default", schoolcheck.checker()) or config.is_sandbox(bot.id):
                return {"ok": False, "error": "Only the school Zip and Zero correct school dates. Ask the school Zip."}
            unit = re.search(r"\d+", str(a.get("unit") or ""))
            return {"ok": True, "result": await asyncio.to_thread(
                school.fix_date, bot.id, str(a.get("course") or "")[:100], str(a.get("due") or "")[:40],
                str(a.get("source") or ""), str(a.get("why") or ""), int(unit.group(0)) if unit else None,
                str(a.get("item") or "")[:120])}
        if op == "bills":

            from . import bills
            action = str(a.get("action") or "list")
            if action in ("record", "settle") and config.is_sandbox(bot.id):
                return {"ok": False, "error": "A test Zip reads the bills and changes none of them."}
            try:
                if action == "record":
                    return {"ok": True, "result": await bills.record(bot.id, a)}
                if action == "settle":
                    return {"ok": True, "result": await asyncio.to_thread(bills.settle, bot.id, a)}
                return {"ok": True, "result": await asyncio.to_thread(bills.tool, bot.id, a)}
            except bills.BillError as e:
                return {"ok": False, "error": str(e)}
    except (team.TeamError, problems.ProblemError) as e:
        return {"ok": False, "error": str(e)}
    raise HTTPException(404, "Unknown action.")


async def _bot_react(bot_id: str, kind: str) -> dict:
    ""
    if kind not in store.REACTIONS:
        return {"ok": False, "error": "Unknown reaction. Use heart, like, dislike, laugh, emphasize or question."}
    msgs = await hermes.messages(bot_id, limit=30)
    latest = next((m for m in reversed(msgs) if m["role"] == "user" and m.get("id") is not None), None)
    if not latest:
        return {"ok": False, "error": f"There is no message from {who.first()} to react to."}
    store.set_reaction(bot_id, str(latest["id"]), "bot", kind, latest["text"])
    return {"ok": True, "result": f"Reacted with {kind}."}








class ClaudeCodeBody(BaseModel):
    code: str = Field(max_length=400)


class ClaudeAutoBody(BaseModel):
    on: bool


def _claude_accounts_error(e: Exception) -> HTTPException:
    return HTTPException(422 if isinstance(e, ValueError) else 503, str(e))


@app.get("/api/claude-accounts")
async def claude_accounts(_: str = Depends(require_device)):
    return await asyncio.to_thread(claudeaccounts.view)


@app.post("/api/claude-accounts/login")
async def claude_account_login(_: str = Depends(require_device)):
    try:
        return await asyncio.to_thread(claudeaccounts.start_login)
    except (ValueError, RuntimeError, OSError) as e:
        raise _claude_accounts_error(e) from None


@app.post("/api/claude-accounts/login/{login}")
async def claude_account_finish(login: str, body: ClaudeCodeBody, _: str = Depends(require_device)):
    try:
        return await asyncio.to_thread(claudeaccounts.finish_login, login, body.code)
    except (ValueError, RuntimeError, OSError) as e:
        raise _claude_accounts_error(e) from None


@app.post("/api/claude-accounts/{account_id}/use")
async def claude_account_use(account_id: str, _: str = Depends(require_device)):
    try:
        return await asyncio.to_thread(claudeaccounts.use, account_id)
    except ValueError as e:
        raise _claude_accounts_error(e) from None


@app.post("/api/claude-accounts/auto")
async def claude_account_auto(body: ClaudeAutoBody, _: str = Depends(require_device)):
    return await asyncio.to_thread(claudeaccounts.set_auto, body.on)


@app.post("/api/claude-accounts/read")
async def claude_account_read(_: str = Depends(require_device)):
    await asyncio.to_thread(claudeaccounts.read_all)
    return await asyncio.to_thread(claudeaccounts.view)


class ClaudeCodeDaysBody(BaseModel):
    days: dict


@app.post("/api/usage/claude-code")
async def claude_code_usage(body: ClaudeCodeDaysBody, _: str = Depends(require_device)):
    ""
    from . import usagesplit
    await asyncio.to_thread(usagesplit.save_mac, body.days)
    return {"ok": True}


@app.delete("/api/claude-accounts/{account_id}")
async def claude_account_remove(account_id: str, _: str = Depends(require_device)):
    try:
        return await asyncio.to_thread(claudeaccounts.remove, account_id)
    except ValueError as e:
        raise _claude_accounts_error(e) from None


@app.get("/api/accounts")
async def get_accounts(_: str = Depends(require_device)):
    ""
    return {**await accounts.check(), "connections_switch": accounts.connections_switch()}


class ConnectionsBody(BaseModel):
    on: bool


@app.post("/api/accounts/connections")
async def set_connections(body: ConnectionsBody, _: str = Depends(require_device)):
    ""
    if accounts.connections_switch() is None:
        raise HTTPException(409, "This home has no such switch.")
    await asyncio.to_thread(accounts.set_connections, body.on)
    return {**await accounts.check(force=True), "connections_switch": accounts.connections_switch()}


@app.post("/api/accounts/check")
async def check_accounts(_: str = Depends(require_device)):
    ""
    return {**await accounts.check(force=True), "connections_switch": accounts.connections_switch()}


class AccountCardBody(BaseModel):
    outcome: str


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
    ""
    bot = config.BOTS_BY_ID.get(body.bot_id)
    if not bot:
        raise HTTPException(404, "Unknown bot.")
    service = str(body.args.get("service") or "")
    if accounts.home.connectors() == "none":
        return {"ok": True, "result": {"connected": False, "state": accounts.NEEDS_OWN_PLAN,
                                       "message": accounts.no_connectors_message()}}
    if team.in_team_session(body.session_id):

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


@app.post("/internal/accounts/ask_app")
async def internal_ask_app(body: MacBody, _: None = Depends(require_internal)):
    ""

    from . import apps
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    app_id = apps.find_id(str(body.args.get("app") or ""))
    if not app_id:
        return {"ok": False, "error": "Name one of these apps: " + ", ".join(
            a.id for a in apps.CATALOG if a.native) + "."}
    row = await asyncio.to_thread(apps.describe, app_id, accounts.cached(), bot_id=body.bot_id)
    tool = apps.TOOLS.get(app_id, app_id)
    if row["state"] == apps.CONNECTED:
        return {"ok": True, "result": f"{row['name']} is already connected. Use the {tool} tool."}
    if row["state"] == apps.ELSEWHERE:
        return {"ok": True, "result": f"{row['name']} works through the connectors tool here.", "connectors": True}
    told = None if body.args.get("no_card") else \
        await accounts.need_app(body.bot_id, app_id, str(body.args.get("why") or ""), body.session_id or None,
                                origin_task=body.origin_task)
    if told:
        return {"ok": True, "result": told, "card": True}
    service = {v: k for k, v in accounts.SERVICE_APPS.items()}.get(app_id)
    if service and accounts.home.connectors() != "none":


        return {"ok": True, "result": f"Use the connectors tool with service \"{service}\" for {row['name']}.",
                "connectors": True}
    if team.in_team_session(body.session_id):
        return {"ok": True, "result": f"{row['name']} isn't connected. Say so in your answer; don't ask "
                                      f"{who.first()} from here."}
    return {"ok": True, "result": f"{row['name']} can't be connected from a card here ({row['detail'] or 'not set up'}). "
                                  f"Tell {who.first()} so in one short line."}


@app.post("/internal/accounts/ask_mailbox")
async def internal_ask_mailbox(body: MacBody, _: None = Depends(require_internal)):
    ""

    from . import apps, mailboxes
    if body.bot_id not in config.BOTS_BY_ID:
        raise HTTPException(404, "Unknown bot.")
    address = str(body.args.get("address") or "").strip().lower()[:254]
    why = str(body.args.get("why") or "")
    if address and not mailboxes.ADDRESS.fullmatch(address):
        address = ""
    provider = await asyncio.to_thread(mailboxes.provider_for, address) if address else \
        ("icloud" if str(body.args.get("provider") or "").lower() == "icloud" else "other")
    box = mailboxes.icloud_mailbox() if provider == "icloud" else None
    if box and address and mailboxes.receives(box, address):


        await asyncio.to_thread(mailboxes.add_alias, box["id"], address, body.bot_id)
        return {"ok": True, "result": f"{address} arrives in the {box['id']} mailbox ({box['address']}). Search "
                                      f"again with {address} as account; that searches the mailbox for mail sent "
                                      "to it.", "alias": box["id"]}
    if box and address:



        return {"ok": True, "result": f"iCloud is already connected as account {box['id']} ({box['address']}), and "
                                      f"{address} isn't one of its addresses. Search there with account {box['id']}. If "
                                      f"you truly need a different iCloud account, ask {who.first()} in chat."}
    if team.in_team_session(body.session_id):
        return {"ok": True, "result": f"{address or 'That mailbox'} isn't connected. Say so in your answer; don't ask "
                                      f"{who.first()} from here."}


    if provider == "gmail" and not await asyncio.to_thread(apps.connected_natively, "gmail"):
        told = await accounts.need_app(body.bot_id, "gmail", why, body.session_id or None,
                                       origin_task=body.origin_task)
        if told:
            return {"ok": True, "result": told, "card": True}
    fid, told = await asyncio.to_thread(accounts.ask_mailbox, body.bot_id, address, provider, why,
                                        body.session_id or None)
    if fid:
        name = accounts.MAILBOX_NAMES.get(provider, "Email")
        await watcher.push(f"{config.BOTS_BY_ID[body.bot_id].name} needs {name}",
                           f"Can you connect {name} so I can read it?", body.bot_id)
    return {"ok": True, "result": told, "card": True}




from . import modelsetup


class ModelPin(BaseModel):
    model: str | None = None
    effort: str | None = None


@app.get("/api/models")
async def get_models(_: str = Depends(require_device)):
    return await modelsetup.payload()


@app.put("/api/models")
async def put_models(body: dict, _: str = Depends(require_device)):
    try:
        return await modelsetup.save(body)
    except modelsetup.ChoiceError as e:
        raise HTTPException(400, str(e))


@app.put("/api/models/zip/{bot_id}")
async def put_zip_model(bot_id: str, body: ModelPin, _: str = Depends(require_device)):
    try:


        sent = body.model_fields_set
        key = modelsetup.KEEP if sent == {"effort"} else body.model
        return await modelsetup.pin(bot_id, key, body.effort if "effort" in sent else modelsetup.KEEP)
    except modelsetup.ChoiceError as e:
        raise HTTPException(400, str(e))




from . import homeapi

homeapi.install(app, require_device, require_internal)




from . import errandsapi

errandsapi.install(app, require_device, require_internal)




from . import siteicon


@app.get("/api/site-icon/{host}")
async def site_icon(host: str, _: str = Depends(require_device)):
    got = await asyncio.to_thread(siteicon.icon, host)
    if not got:
        raise HTTPException(404, "No icon for that site.")
    return Response(got[0], media_type=got[1], headers={"Cache-Control": "private, max-age=604800"})


@app.get("/api/kit-images/{name}")
def kit_image(name: str, _: str = Depends(require_device)):
    ""
    from . import kit
    path = kit.image_path(name)
    if not path:
        raise HTTPException(404, "No such picture.")
    from fastapi.responses import FileResponse
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})




from . import appsapi

appsapi.install(app, require_device, require_internal)




from . import teamsapi

teamsapi.install(app, require_device, require_internal, _remove_bot, MacBody)


@app.post("/internal/new_zip/{op}")
async def internal_new_zip(op: str, body: MacBody, _: None = Depends(require_internal)):
    ""
    if body.bot_id != "default":
        return {"ok": False, "error": "Only the lead makes new Zips."}
    if op != "plan":
        raise HTTPException(404, "Unknown action.")
    try:
        return {"ok": True, "result": newzip.plan_for(str(body.args.get("request") or ""))}
    except newzip.NewZipError as e:
        return {"ok": False, "error": str(e)}







@app.post("/api/bots/{bot_id}/stop")
async def stop_turn(bot_id: str, _: str = Depends(require_device)):
    ""


    _bot(bot_id)
    out = await turns.stop_pressed(bot_id)
    activity.clear_outcome(bot_id)
    if mac.working_on_mac(bot_id):
        await mac.stop_from_phone()
    return {"ok": True, "stopped": out["stopped"], "returned": out["returned"], "settled": out["settled"]}


@app.post("/api/bots/{bot_id}/stop-work")
async def stop_work(bot_id: str, _: str = Depends(require_device)):
    ""



    _bot(bot_id)
    turn, settled = False, True
    if turns.is_busy(bot_id):
        out = await turns.stop_pressed(bot_id)
        turn, settled = out["stopped"], out["settled"]
        activity.clear_outcome(bot_id)
    tasks = await asyncio.to_thread(board.stop_running, bot_id)
    on_mac = mac.working_on_mac(bot_id)
    if on_mac:
        await mac.stop_from_phone()
    if not turn and (tasks or on_mac):
        turns.say_stopped(bot_id)
    return {"ok": True, "stopped": bool(turn or tasks or on_mac), "tasks": tasks, "settled": settled}


class NotifyKindsBody(BaseModel):
    needs: bool | None = None
    done: bool | None = None
    progress: bool | None = None


@app.put("/api/bots/{bot_id}/notify-kinds")
def set_notify_kinds(bot_id: str, body: NotifyKindsBody, _: str = Depends(require_device)):
    ""

    _bot(bot_id)
    return {"ok": True, "notify_kinds": notifyprefs.set_kinds(bot_id, needs=body.needs, done=body.done,
                                                               progress=body.progress)}


class ToneBody(BaseModel):
    tone: str


@app.put("/api/bots/{bot_id}/tone")
def set_tone(bot_id: str, body: ToneBody, _: str = Depends(require_device)):
    ""

    _bot(bot_id)
    try:
        return {"ok": True, "tone": tones.set_tone(bot_id, body.tone)}
    except ValueError:
        raise HTTPException(400, "That tone isn't one Zero has.")


EXPIRED_STATUS_SEC = 24 * 3600


def _card_line(r: dict) -> str:
    ""
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
    ""


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
    ""

    sid = r.get("session_id")
    if r.get("task_id") or not sid or sid != store.session_for(b.id):
        return True
    return float(r.get("created") or 0) >= said


def _bot_status(b: config.Bot, cards: list[dict], blocked: list[dict], now: float) -> dict | None:
    ""

    said = store.said_at(b.id)
    cards = [r for r in cards if _live_card(b, r, said)]
    if cards:
        more = f", and {len(cards) - 1} more" if len(cards) > 1 else ""
        return {"state": "paused", "line": f"Paused for your okay: {_card_line(cards[0])}{more}.",
                "at": float(cards[0].get("created") or now)}
    asks = signin.live_asks(b.id)
    if asks:
        row, card = asks[0]
        what = (signin.ask_line(card) if row["kind"] == "login" else
                f"connect {card.get('name')}" if card.get("name") else "answer its card")
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
              "signin_decision", "message", "choices", "login", "account", "code", "project", "mail")


@app.get("/api/away")
def away(since: float, _: str = Depends(require_device)):
    ""


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
        d = per.setdefault(bot, {"done": [], "needs": [], "failed": [], "closed": [], "ready": [], "messages": 0,
                                 "at": 0})
        d["at"] = max(d["at"], r["created_at"])
        first = ((r["body"] or "").split("\n\n")[0] or r["title"] or "").strip()
        k = r["kind"]
        if k == "task_blocked" and r.get("quiet_word") == quiet.ROUTED:
            continue
        if k in ("task_done", "action"):
            d["done"].append(first if k == "task_done" else (r["body"] or "").strip())
        elif k in ("task_blocked", "task_review", "signin_request"):
            d["needs"].append(first if k != "signin_request" else _first_sentence(r["body"] or "", 80))
        elif k in ("choices", "login", "account", "code", "mail"):
            if '"state": "open"' in (r["body"] or ""):
                d["needs"].append(r["title"] or "a question")
        elif k == "task_failed":
            d["failed"].append(first)
        elif k == "signin_decision" and (r["body"] or "").startswith(consent.NO_ANSWER_TITLE):
            d["closed"].append(r["title"] or "a card")
        elif k == "project":
            if '"state": "open"' in (r["body"] or ""):
                d["ready"].append(r["title"] or "a project")
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
        if d["ready"]:
            parts.append(f"Ready to open: {_clip(d['ready'][-1])}." if len(d["ready"]) == 1
                         else f"{len(d['ready'])} projects are ready to open.")
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

