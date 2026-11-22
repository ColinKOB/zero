""


































from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config, home, nightly, store

log = logging.getLogger("taffy.jobretry")

SETTING = "job_retries"
DELAYS = (5 * 60, 10 * 60, 20 * 60, 40 * 60, 60 * 60)
MIN_WINDOW_SEC = 2 * 3600
ONCE_WINDOW_SEC = 2 * 86400
BRIEF_LAST_HOUR = 12
JUDGE_AFTER_SEC = 45 * 60
JUDGE_EVERY_SEC = 60 * 60
CLAIM_LIVE_SEC = 300
RUN_MAX_SEC = 60 * 60

NOT_PROCESSED = "Your request was not processed."


PROVIDER_ERRORS = ("can't reach the model provider", "apiconnectionerror", "apitimeouterror", "connection error",
                   "connecterror", "connection refused", "connection reset", "name resolution", "request timed out",
                   "readtimeout", "connecttimeout", "internalservererror", "service unavailable", "bad gateway",
                   "gateway timeout", "overloaded")

_running: dict[str, tuple[subprocess.Popen, float]] = {}



LIMIT_ERRORS = ("usage limit", "rate limit", "rate_limit", "quota", "http 429", "too many requests")
RESTART_ERRORS = ("scheduler restarted", "owner exited", "interrupted by a restart")


def provider_error(text) -> bool:
    low = str(text or "").lower()
    return any(words in low for words in PROVIDER_ERRORS)


def reason(text) -> str | None:
    ""
    low = str(text or "").lower()
    if any(words in low for words in RESTART_ERRORS):
        return "restart"
    if any(words in low for words in LIMIT_ERRORS):
        return "limit"
    return "connection" if provider_error(low) else None


def _ts(iso) -> float:
    try:
        when = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return 0.0
    return (when if when.tzinfo else when.replace(tzinfo=home.zone())).timestamp()


def _day_end(ts: float) -> float:
    day = datetime.fromtimestamp(ts, home.zone()).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day + timedelta(days=1)).timestamp()


def _clock(ts: float) -> str:
    when = datetime.fromtimestamp(ts, home.zone())
    hour = when.hour % 12 or 12
    return f"{hour}{'' if when.minute == 0 else f':{when.minute:02d}'} {'a.m.' if when.hour < 12 else 'p.m.'}"


def not_processed(folder: Path) -> bool:
    ""
    try:
        newest = max(folder.glob("*.md"), key=lambda f: f.stat().st_mtime, default=None)
        text = newest.read_text(errors="replace") if newest else ""
    except OSError:
        return False
    for marker in ("## Response", "## Output", "## Result"):
        if marker in text:
            return text.split(marker, 1)[1].strip().startswith(NOT_PROCESSED)
    return False


def _failure(job: dict, folder: Path) -> str | None:
    ""
    if job.get("no_agent"):
        return None
    if str(job.get("last_status") or "") in ("error", "unknown", "interrupted"):
        return reason(job.get("last_error"))
    return "connection" if not_processed(folder) else None


def _once(job: dict) -> bool:
    return str((job.get("schedule") or {}).get("kind") or "") == "once"


def _brief(bot_id: str, job: dict) -> bool:
    return bot_id == config.LEAD.id and "brief" in str(job.get("name") or "").lower()


def _window(bot_id: str, job: dict, ran: float) -> float:
    ""
    if _once(job):
        return ran + ONCE_WINDOW_SEC
    if _brief(bot_id, job):
        noon = datetime.fromtimestamp(ran, home.zone()).replace(hour=BRIEF_LAST_HOUR, minute=0, second=0, microsecond=0)
        return max(noon.timestamp(), ran + MIN_WINDOW_SEC)
    return max(_day_end(ran), ran + MIN_WINDOW_SEC)


def _one_time_done(job: dict) -> bool:
    return str(job.get("state") or "") == "completed"


def _off(job: dict) -> bool:
    ""
    return not _one_time_done(job) and (str(job.get("state") or "") == "paused" or not job.get("enabled", True))


def _busy(key: str, job: dict, now: float) -> bool:
    ""
    held = _running.get(key)
    if held:
        proc, since = held
        if proc.poll() is None and now - since < RUN_MAX_SEC:
            return True
        if proc.poll() is None:
            proc.kill()
        _running.pop(key, None)
    claim = job.get("fire_claim")
    try:
        age = now - datetime.fromisoformat(claim["at"]).timestamp()
    except (TypeError, KeyError, ValueError):
        return False
    return 0 <= age < CLAIM_LIVE_SEC


def _launch(key: str, bot_id: str, job_id: str, job: dict, now: float) -> bool:
    ""

    verb = ["cron", "resume", job_id, "--run-now"] if _one_time_done(job) else ["cron", "run", job_id]
    cmd = [str(config.HERMES_BIN)] + ([] if bot_id == "default" else ["-p", bot_id]) + verb
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=dict(os.environ, HERMES_HOME=str(config.HERMES_HOME)), start_new_session=True)
    except OSError as e:
        log.warning("could not run job %s again: %s", key, type(e).__name__)
        return False
    _running[key] = (proc, now)
    return True


def _record_miss(key: str, entry: dict, job: dict, when: str) -> None:
    ""

    once = _one_time_done(job) or _once(job)
    after = "Tell me if you still want it." if once or not when else f"It runs again {when.rstrip('.')}."
    kind = "once" if once else "brief" if _brief(entry["bot"], job) else "job"
    nightly.miss(f"job:{key}:{int(entry['first'])}", kind, entry["bot"], f"“{entry['name']}”", entry.get("why") or "connection",
                 after, entry["first"])


async def judge_open(now: float | None = None, ask=None) -> list[str]:
    ""

    from . import watcher
    now = time.time() if now is None else now
    ask = ask or nightly.still_worth
    state = store.get_setting(SETTING, None) or {}
    stopped, changed = [], False
    jobs = {f"{bot_id}:{job_id}": job for bot_id, out in watcher._cron_dirs()
            for job_id, job in watcher._jobs_of(out.parent / "jobs.json").items()}
    for key, entry in state.items():
        job = jobs.get(key)
        if job is None or entry.get("done") or now - entry["first"] < JUDGE_AFTER_SEC \
                or now - float(entry.get("judged") or 0) < JUDGE_EVERY_SEC:
            continue
        worth = await ask(entry["bot"], entry["name"], watcher._when(job), entry["first"], str(job.get("prompt") or ""), now)
        entry["judged"], changed = now, True
        if worth is False:
            entry["until"] = now
            stopped.append(key)
    if changed:

        with store._lock:
            saved = store.get_setting(SETTING, None) or {}
            for key, entry in state.items():
                if key in saved and "judged" in entry:
                    saved[key].update(judged=entry["judged"], until=min(saved[key]["until"], entry["until"]))
            store.set_setting(SETTING, saved)
    return stopped


def sweep(now: float | None = None, launch=_launch) -> list[str]:
    ""

    from . import watcher
    now = time.time() if now is None else now
    saved = store.get_setting(SETTING, None)
    first_pass = saved is None
    state: dict[str, dict] = json.loads(json.dumps(saved or {}))
    gave_up, present, started = [], set(), False
    for bot_id, out in watcher._cron_dirs():
        for job_id, job in watcher._jobs_of(out.parent / "jobs.json").items():
            key = f"{bot_id}:{job_id}"
            present.add(key)
            ran, entry = _ts(job.get("last_run_at")), state.get(key)
            why = _failure(job, out / job_id)
            if why is None:
                if entry and ran > entry["last"]:
                    state.pop(key)
                continue
            if entry is None or (entry.get("done") and ran > entry["until"]):
                entry = state[key] = {"bot": bot_id, "name": str(job.get("name") or "A job")[:80], "first": ran,
                                      "last": ran, "until": _window(bot_id, job, ran), "tries": 0,
                                      "launched": 0, "done": first_pass or not ran, "why": why}
            entry["last"] = max(entry["last"], ran)
            if entry.get("done") or _busy(key, job, now):
                continue
            if _off(job):
                state.pop(key)
                continue
            if now >= entry["until"]:
                entry["done"] = True
                _record_miss(key, entry, job, watcher._when(job))
                gave_up.append(key)
                log.info("job %s never got through after %d more tries", key, entry["tries"])
                continue
            due = max(entry["last"], entry["launched"]) + DELAYS[min(entry["tries"], len(DELAYS) - 1)]
            if started or now < due:
                continue
            started = True
            entry["launched"] = now
            if launch(key, bot_id, job_id, job, now):
                entry["tries"] += 1
                log.info("running job %s again (try %d): %s", key, entry["tries"], why)
    for key in [k for k in state if k not in present]:
        state.pop(key)
    if state != (saved or {}) or first_pass:
        store.set_setting(SETTING, state)
    return gave_up
