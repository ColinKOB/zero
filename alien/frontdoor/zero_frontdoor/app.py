"""The front door: username and password accounts for Zero homes on alien.

    POST /api/sign-up            {username, password, name, timezone, device_name} -> 202 {job, state}
    GET  /api/sign-up/{job}      -> {state: waiting|creating|ready|failed, step, message}
    POST /api/sign-in            {username, password, device_name} -> {server, token, username, plan}
    GET  /api/health             -> {ok, sign_up: open|closed|full}

Sign-up makes a new home (homes.py) in the background, one at a time, because a build takes a few minutes. When it
is ready the app signs in with the same username and password, which mints a device token in the new home exactly
the way a pairing code does, so the app talks to the home the same way after either.

Runs as the owner (jdoe), the only user allowed to make homes, on 127.0.0.1 behind Tailscale serve
(alien/systemd/templates/zero-frontdoor.service). Passwords and tokens are never logged; request bodies are never
echoed in errors.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import secrets
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import homes as homes_mod
from . import plans, store

log = logging.getLogger("zero-frontdoor")
if not log.handlers:   # uvicorn configures only its own loggers; this one goes to the journal too
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

app = FastAPI(title="Zero front door", docs_url=None, redoc_url=None, openapi_url=None)

# Replaced in tests with a fake that runs no commands.
HOMES = homes_mod.LiveHomes(prefix=os.environ.get("ZERO_FRONTDOOR_HOME_PREFIX", "u"))
# Closed unless the unit says "open": strangers never make homes on this machine. While closed, only a username the
# owner invited (`zero-account invite USERNAME`) can sign up; anyone can still sign in to an account that exists.
SIGN_UP = os.environ.get("ZERO_FRONTDOOR_SIGN_UP", "closed")    # open or closed

TZ_RE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$")

# Rate limits, kept in memory (a restart forgets them, which is fine at this size).
SIGN_IN_PER_USER = (5, 15 * 60)       # wrong passwords for one username, per window
SIGN_IN_ALL = (40, 15 * 60)           # wrong passwords for every username together
SIGN_UP_ALL = (5, 60 * 60)            # new accounts, per window
_fails_user: dict[str, list[float]] = {}
_fails_all: list[float] = []
_sign_ups: list[float] = []
_rate_lock = threading.Lock()

BUSY_TEXT = "Too many tries. Wait 15 minutes and try again."
WRONG_TEXT = "That username and password do not match. Check them and try again."
TROUBLE_TEXT = "Zero could not sign you in just now. Try again in a minute."


def _recent(times: list[float], window: float, now: float) -> list[float]:
    return [t for t in times if now - t < window]


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    fields = [".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": f"Check these fields: {', '.join(fields) or 'request'}."})


class Job:
    def __init__(self, username: str, home_id: str, person: str, timezone: str):
        self.id = secrets.token_urlsafe(24)
        self.username, self.home_id, self.person, self.timezone = username, home_id, person, timezone
        self.state = "waiting"
        self.step = ""
        self.started = time.time()


_jobs: dict[str, Job] = {}
_queue: "queue.Queue[Job]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()

STEP_TEXT = {
    "": "Getting ready.",
    "user": "Making room for your Zero.",
    "home": "Setting up your Zero and its first Zip. This takes a few minutes.",
    "done": "Your Zero is ready.",
}


def _build(job: Job) -> None:
    job.state = "creating"
    try:
        HOMES.create(job.home_id, job.person, job.timezone, step=lambda s: setattr(job, "step", s))
    except Exception as e:  # noqa: BLE001
        log.error("sign-up %s (%s) failed: %s", job.username, job.home_id, e)
        job.state = "failed"
        store.set_state(job.username, "failed")
        return
    store.set_state(job.username, "ready")
    job.state = "ready"
    log.info("sign-up %s ready as %s in %.0fs", job.username, job.home_id, time.time() - job.started)


def _work() -> None:
    while True:
        job = _queue.get()
        try:
            _build(job)
        finally:
            _queue.task_done()


def _enqueue(job: Job) -> None:
    global _worker
    _jobs[job.id] = job
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="sign-up", daemon=True)
            _worker.start()
    _queue.put(job)


@app.on_event("startup")
def _forget_unfinished() -> None:
    """A build the last run did not finish is marked failed; signing up again with the same details resumes it."""
    for a in store.all_accounts():
        if a["state"] == "creating":
            store.set_state(a["username"], "failed")


class SignUpBody(BaseModel):
    username: str
    password: str
    name: str = ""
    timezone: str = ""
    device_name: str = "iPhone"


@app.post("/api/sign-up", status_code=202)
def sign_up(body: SignUpBody):
    username = store.normalize_username(body.username)
    invited = store.invited(username)
    if SIGN_UP != "open" and not invited:
        raise HTTPException(403, "New accounts are not open right now.")
    problem = store.username_problem(username) or store.password_problem(username, body.password)
    if problem:
        raise HTTPException(400, problem)
    person = " ".join((body.name or "").split())[:80] or username
    timezone = body.timezone if TZ_RE.match(body.timezone or "") else "America/New_York"

    existing = store.get(username)
    if existing:
        # The same person trying again after a build that did not finish: same username and password resume it.
        if existing["state"] == "failed" and store.check(username, body.password):
            job = Job(username, existing["home_id"], existing.get("person") or person, timezone)
            store.set_state(username, "creating")
            _enqueue(job)
            return {"job": job.id, "state": job.state, "message": STEP_TEXT[""]}
        raise HTTPException(409, "That username is taken. Try another one.")

    with _rate_lock:
        now = time.time()
        _sign_ups[:] = _recent(_sign_ups, SIGN_UP_ALL[1], now)
        if len(_sign_ups) >= SIGN_UP_ALL[0]:
            raise HTTPException(429, "Zero is making a lot of new accounts right now. Try again in an hour.")
        if not HOMES.has_room():
            raise HTTPException(503, "Zero is full for now. Try again another day.")
        taken = {a["home_id"] for a in store.all_accounts()}
        home_id = HOMES.new_home_id(username, taken)
        try:
            store.add(username, body.password, home_id, state="creating", person=person)
        except ValueError as e:
            raise HTTPException(409, str(e))
        _sign_ups.append(now)
        if invited:
            store.use_invite(username)
    log.info("sign-up %s as %s%s", username, home_id, " (invited)" if invited else "")
    job = Job(username, home_id, person, timezone)
    _enqueue(job)
    return {"job": job.id, "state": job.state, "message": STEP_TEXT[""]}


@app.get("/api/sign-up/{job_id}")
def sign_up_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Zero lost track of this sign-up. Create your account again with the same "
                                 "username and password to pick up where it stopped.")
    if job.state == "failed":
        msg = ("Setting up your Zero did not finish. Try again in a few minutes with the same username and "
               "password, and Zero picks up where it stopped.")
    elif job.state == "waiting":
        msg = "Another new Zero is being set up first. Yours starts in a minute or two."
    else:
        msg = STEP_TEXT.get(job.step, STEP_TEXT[""])
    return {"state": job.state, "step": job.step, "message": msg, "seconds": int(time.time() - job.started)}


class SignInBody(BaseModel):
    username: str
    password: str
    device_name: str = "iPhone"


@app.post("/api/sign-in")
def sign_in(body: SignInBody):
    username = store.normalize_username(body.username)[:64]
    with _rate_lock:
        now = time.time()
        _fails_all[:] = _recent(_fails_all, SIGN_IN_ALL[1], now)
        mine = _fails_user[username] = _recent(_fails_user.get(username, []), SIGN_IN_PER_USER[1], now)
        if len(mine) >= SIGN_IN_PER_USER[0] or len(_fails_all) >= SIGN_IN_ALL[0]:
            raise HTTPException(429, BUSY_TEXT)
    acct = store.check(username, body.password[:store.MAX_PASSWORD])
    if not acct:
        with _rate_lock:
            _fails_user.setdefault(username, []).append(time.time())
            _fails_all.append(time.time())
        log.info("sign-in refused for %s", username or "(empty)")
        raise HTTPException(401, WRONG_TEXT)
    with _rate_lock:
        _fails_user.pop(username, None)
    if acct["state"] == "creating":
        raise HTTPException(409, "Your Zero is still being set up. Try again in a minute.")
    if acct["state"] == "failed":
        raise HTTPException(409, "Setting up your Zero did not finish. Choose Create an Account and use the same "
                                 "username and password, and Zero picks up where it stopped.")
    try:
        server, token = HOMES.device_token(acct["home_id"], (body.device_name or "iPhone")[:80])
    except Exception as e:  # noqa: BLE001
        log.error("sign-in %s: no device token from %s: %s", username, acct["home_id"], e)
        raise HTTPException(503, TROUBLE_TEXT)
    store.touch_sign_in(username)
    log.info("sign-in %s to %s", username, acct["home_id"])
    return {"server": server, "token": token, "username": username, "plan": plans.get_plan(acct)}


@app.get("/api/health")
def health():
    state = SIGN_UP if SIGN_UP != "open" else ("open" if HOMES.has_room() else "full")
    return {"ok": True, "sign_up": state}
