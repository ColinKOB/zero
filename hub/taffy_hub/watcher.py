"""Watches the kanban board and scheduled jobs, and tells Colin and Taffy when the team's work changes.

Hermes does not wake an API-server session when a task it created finishes, so the hub does it:
board events become feed items, and finished, blocked or review work is batched into one short
board-update turn in Taffy's app session so Taffy can relay what matters. Each change reaches
Colin's phone once: as that batched team update, or (for check-ins) as the check-in itself.
"""

import asyncio
import json
import logging
import math
import re
import time
from pathlib import Path

from . import apns, board, chatter, config, consent, events, notices, notifyprefs, notify, screen, store, turns, who

log = logging.getLogger("taffy.watcher")

POLL_SEC = 2
BATCH_WINDOW_SEC = 60          # finished work is told as one story; anything that needs Colin goes out at once
# The person's night (home.json `person.quiet_hours`, 11 p.m. to 7 a.m. by default): routine news waits for the morning.
MAX_UPDATE_TRIES = 3


def _payload(ev: dict) -> dict:
    try:
        return json.loads(ev.get("payload") or "{}") or {}
    except ValueError:
        return {}


def _feed_item(ev: dict) -> tuple[str, str, str] | None:
    kind = ev["kind"]
    bot = config.BOTS_BY_ID.get(ev["assignee"] or "default")
    who_local = bot.name if bot else (ev["assignee"] or "A Zip")
    p = _payload(ev)
    if kind == "spawned":
        return "task_started", f"{who_local} started", ev["title"]
    if kind == "completed":
        return "task_done", f"{who_local} finished", p.get("summary") or ev["title"]
    if kind == "blocked":
        return "task_blocked", f"{who_local} needs you", p.get("reason") or ev["title"]
    if kind in ("review_requested", "review"):
        return "task_review", f"{who_local} wants your review", p.get("summary") or ev["title"]
    if kind in ("crashed", "gave_up", "timed_out"):
        return "task_failed", f"{who_local} hit a problem", p.get("error") or ev["title"]
    return None


def _first_person(kind: str, task: str, detail: str) -> str:
    """A board event as the Zip says it on the lock screen (it is the sender)."""
    task = " ".join(str(task or "").split())[:80]
    detail = " ".join(str(detail or "").split())
    extra = f" {detail[:1].upper()}{detail[1:]}" if detail and detail != task else ""
    if kind == "task_blocked":
        return f"I'm stuck on \"{task}\" and need you.{extra}"
    if kind == "task_review":
        return f"\"{task}\" is ready for your review.{extra}"
    if kind == "task_failed":
        return f"I hit a problem on \"{task}\".{extra}"
    return f"{task}.{extra}"


def _push_sync(title: str, body: str, bot_id: str | None = None, topic: str = "", **opts) -> bool:
    """`title` is the Zip's name; `topic` what the news is about, kept only for the log and tests."""
    try:
        return apns.send(title, body, bot_id, **opts)
    except Exception:
        log.exception("push failed")
        return False


def _lead() -> str:
    return config.BOTS_BY_ID["default"].name if "default" in config.BOTS_BY_ID else "Zero"


def _night() -> bool:
    from datetime import datetime
    from . import home
    start, end = home.quiet_hours()
    h = datetime.now(home.TZ).hour
    return (h >= start or h < end) if start > end else (start <= h < end)


async def push(title: str, body: str, bot_id: str | None = None, *, bots: list[str] | None = None,
               urgent: bool = False, kind: str | None = None, extra: dict | None = None,
               exact: bool = False) -> bool:
    """A notification on Colin's iPhone and Mac (apns.py). Returns whether it went out (or was held for the
    morning).

    - It comes from a Zip (Oct 10): the notification's title is the Zip's name (notify.sender: `bot_id`, the only
      Zip in `bots`, else the lead), and its text is one sentence that fits (notify.line). `title` here is only what
      the news is about, a hint for that sentence ("Team update", "Finished what you asked on the call").
    - A muted or snoozed bot never nudges; with `bots`, the nudge goes out unless every one of them is quiet.
      `bot_id` None means no particular bot, not Taffy.
    - A Zip whose "Notify Me When It Finishes or Needs Me" switch is off sends nothing, urgent or not: its finished
      work and waiting cards stay in the app. With it on, the person picks the kinds (notifyprefs.py, research rows
      #75 to #77): "needs" (decisions and blocks), "done" (finished work) and "progress" (routine check-ins). A push
      with no kind counts as "needs".
    - At night routine news is held and sent as one "While you slept" message at 7 a.m.
    - `urgent` (the system is down, a result Colin is waiting on right now) skips both.
    - `exact`: the text is already the Zip's own words (a reply's short version), so it is cut to fit at a sentence or
      a clause (notify.fit) instead of being rewritten."""
    if not apns.ready():
        return False
    who_local = bots if bots is not None else ([bot_id] if bot_id else [])
    if who_local and not any(notifyprefs.allows(b, kind) for b in who_local):
        return False
    if not urgent:
        if who_local and all(store.quiet(b) for b in who_local):
            return False
        if _night():
            held = store.get_setting("held_pushes", []) or []
            sid, _ = notify.sender(bot_id, bots)
            held.append({"title": title, "body": body[:300], "at": time.time(), "bot": sid})
            store.set_setting("held_pushes", held[-40:])
            return True
    return await _send(title, body, bot_id, bots=bots, kind=kind, urgent=urgent, extra=extra, exact=exact)


async def _send(topic: str, body: str, bot_id: str | None, *, bots: list[str] | None = None, kind: str | None = None,
                urgent: bool = False, extra: dict | None = None, exact: bool = False) -> bool:
    """The notification itself: from the Zip, in one sentence, grouped under that Zip."""
    sid, name = notify.sender(bot_id, bots)
    hint = topic if topic and topic.strip() != name else ""
    text = notify.fit(notify.plain(body)) if exact else await notify.line(body, sid, context=hint)
    opts = {"relevance": notify.relevance(kind, urgent), "topic": topic}
    if extra:
        opts["extra"] = extra
    return await asyncio.to_thread(_push_sync, name, text, sid, **opts)


def _names(ids: list[str]) -> str:
    names = []
    for b in ids:
        n = notify.sender(b)[1]
        if n not in names:
            names.append(n)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


async def send_morning_digest() -> None:
    """After quiet hours: what was held overnight, in one message (one held item goes out as itself)."""
    held = store.get_setting("held_pushes", []) or []
    if not held or _night():
        return
    if len(held) == 1:
        h = held[0]
        ok = await _send(h["title"], h["body"], h.get("bot"), kind="done")
    else:
        ok = await _send("While you slept", f"While you slept, {len(held)} updates came in from "
                                            f"{_names([h.get('bot') or 'default' for h in held])}.", None, kind="done")
    if ok:
        store.set_setting("held_pushes", [])


def _cron_dirs() -> list[tuple[str, Path]]:
    dirs = [("default", config.HERMES_HOME / "cron" / "output")]
    for b in config.BOTS:
        if b.id != "default":
            dirs.append((b.id, config.HERMES_HOME / "profiles" / b.id / "cron" / "output"))
    return dirs


def _cron_report(path: Path) -> tuple[str, str] | None:
    """(job name, delivered text) from a Hermes cron output file, or None when the run was silent."""
    text = path.read_text(errors="replace")
    name = text.splitlines()[0].removeprefix("# Cron Job:").strip() if text else path.parent.name
    body = None
    for marker in ("## Response", "## Output", "## Result"):
        if marker in text:
            body = text.split(marker, 1)[1].strip()
            break
    else:
        # Script-only jobs (the mail watches) write a short header, then "---" and the script's words.
        # A failed script has no "---"; scan_jobs tells Colin in plain words once it keeps failing.
        if "\n---\n" in text:
            body = text.split("\n---\n", 1)[1].strip()
    if not body or body.startswith("[SILENT]"):
        return None
    return name, body


async def scan_cron() -> None:
    """Reports each new cron output once. Files go in time order and the cursor is saved after each one, so
    an error part way through never sends the earlier ones again (Oct 7: a cursor bug sent one report every
    10 seconds).

    A report goes to its Zip's chat quietly (Oct 10, Colin: "Only buzz when there's a reason"). The phone buzzes
    only when notify.judge_report finds a reason: a line crossed for the first time this week, something that needs
    the person, or something time-sensitive, in one sentence from the Zip ("You're $66 over this week's dining budget
    after last night's Chick-fil-A. Want me to plan cheap dinners through Sunday?")."""
    last = store.get_cursor("cron_mtime_ms", 0)
    seen = set(store.get_setting("cron_seen_at_cursor", []) or [])
    found = []
    for bot_id, d in _cron_dirs():
        if not d.is_dir():
            continue
        for f in d.glob("*/*.md"):
            try:
                m = int(f.stat().st_mtime * 1000)   # whole milliseconds, the cursor's own unit
            except OSError:
                continue
            if m < last or (m == last and str(f) in seen):
                continue
            found.append((m, str(f), bot_id, f))
    told = []
    for m, key, bot_id, f in sorted(found):
        try:
            report = _cron_report(f)
            script = report is not None and "no_agent" in f.read_text(errors="replace")[:800]
        except OSError:
            report, script = None, False
        if report:
            name, body = report
            shown = notify.report_text(body)
            if shown:
                fid = store.add_feed("checkin", bot_id, None, name, shown)
                told.append((bot_id, name, body, script, fid))
        if m > last:
            last, seen = m, {key}
        else:
            seen.add(key)
        store.set_cursor("cron_mtime_ms", last)
        store.set_setting("cron_seen_at_cursor", sorted(seen))
    for bot_id, name, *_ in told:
        if notices.on_cron_report(bot_id, name):
            await notices.post_brief()   # the open-questions paragraph, right under the 7:30 brief
    for bot_id, name, body, script, fid in told:
        if not notifyprefs.allows(bot_id, "needs") and not notifyprefs.allows(bot_id, "progress"):
            continue   # nothing from this Zip may reach the phone: no need to ask Haiku
        try:
            verdict = await notify.judge_report(bot_id, name, body, script=script)
        except Exception:
            log.exception("could not judge %s's %s", bot_id, name)
            continue
        if verdict["buzz"]:
            await push(name, verdict["line"], bot_id, kind=verdict["kind"], extra={"item_id": fid} if fid else None)


def _jobs_of(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    items = jobs.values() if isinstance(jobs, dict) else jobs
    return {str(j.get("id")): j for j in items if isinstance(j, dict) and j.get("id")}


_CRON_DAYS = {"0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday", "5": "Friday",
              "6": "Saturday", "7": "Sunday"}


def _clock_words(hour: int, minute: int) -> str:
    h = hour % 12 or 12
    ampm = "a.m." if hour < 12 else "p.m."
    return f"{h} {ampm}" if minute == 0 else f"{h}:{minute:02d} {ampm}"


def cron_words(expr: str) -> str:
    """A cron expression in plain words ("every day at 3 p.m."), or the expression itself when it is one of the
    unusual shapes. Colin saw "Ocean City 3-7 day surf planner (0 15 * * *)" in his chat (Oct 7)."""
    parts = str(expr or "").split()
    if len(parts) != 5 or not parts[0].isdigit() or not parts[1].isdigit():
        return str(expr or "")
    minute, hour, dom, month, dow = int(parts[0]), int(parts[1]), parts[2], parts[3], parts[4]
    if not (0 <= minute < 60 and 0 <= hour < 24) or month != "*":
        return str(expr)
    at = _clock_words(hour, minute)
    if dom == "*" and dow == "*":
        return f"every day at {at}"
    if dom == "*" and dow in ("1-5", "1,2,3,4,5"):
        return f"weekdays at {at}"
    if dom == "*" and dow in ("0,6", "6,0"):
        return f"weekends at {at}"
    if dom == "*" and re.fullmatch(r"[0-7](,[0-7])*", dow):
        days = [_CRON_DAYS[d] for d in dow.split(",")]
        return f"every {' and '.join(days) if len(days) <= 2 else ', '.join(days[:-1]) + ' and ' + days[-1]} at {at}"
    if dow == "*" and re.fullmatch(r"\*/\d+", dom):
        n = int(dom[2:])
        return f"every {'other day' if n == 2 else f'{n} days'} at {at}"
    if dow == "*" and dom.isdigit():
        d = int(dom)
        suffix = "th" if 11 <= d <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return f"on the {d}{suffix} of every month at {at}"
    return str(expr)


def _when(j: dict) -> str:
    """When a job runs, in plain words. Hermes's `display` is often the raw cron expression."""
    s = j.get("schedule") or {}
    expr = str(s.get("expr") or "")
    display = str(s.get("display") or "")
    if expr and (not display or display == expr):
        return cron_words(expr)
    if display:
        return cron_words(display) if re.fullmatch(r"[\d*/,\-]+(\s+[\d*/,\-]+){4}", display) else display
    return str(s.get("run_at") or "")


FAILING_AFTER = 2   # failed runs in a row before Colin hears that a job is broken


def scan_jobs() -> list[tuple[str, str, str]]:
    """Scheduled jobs are reported in the bot's chat when one is added, changed, paused or removed, so
    nothing runs on a schedule that Colin can't see. The first scan only remembers what exists.
    Returns the jobs that started failing or recovered, which are also pushed: a broken mail watch is
    otherwise silent, which looks the same as nothing new."""
    alerts: list[tuple[str, str, str]] = []
    seen = store.get_setting("cron_jobs_seen", None)
    now: dict[str, dict] = {}
    for bot_id, out in _cron_dirs():
        for jid, j in _jobs_of(out.parent / "jobs.json").items():
            now[f"{bot_id}:{jid}"] = {"bot": bot_id, "name": str(j.get("name") or "A job")[:80], "when": _when(j),
                                      "sched": json.dumps(j.get("schedule"), sort_keys=True, default=str),
                                      "on": bool(j.get("enabled", True)),
                                      "failing": int(j.get("failure_streak") or 0) >= FAILING_AFTER}
    if seen is not None:
        for key, j in now.items():
            old = seen.get(key)
            if j["failing"] != bool((old or {}).get("failing")) and (old is not None or j["failing"]):
                if j["failing"]:
                    alert = ("A scheduled job keeps failing",
                             f"{j['name']} has failed {FAILING_AFTER} times in a row, so it may be missing things. "
                             "Want me to look into it?")
                else:
                    alert = ("A scheduled job works again", f"{j['name']} ran normally again.")
                store.add_feed("checkin", j["bot"], None, *alert)
                alerts.append((j["bot"], *alert))
            if old is None:
                events.routine_created(j["bot"], j["name"], j["when"])   # "Created routine" in that Zip's chat
                continue
            # The schedule itself, not its wording: rewording "when" (Oct 9) made every job look changed.
            elif ((old.get("sched") != j["sched"] if "sched" in old else old.get("when") != j["when"])
                  or old.get("on") != j["on"] or old.get("name") != j["name"]):
                title = "Changed a scheduled job" if j["on"] else "Paused a scheduled job"
                body = f"{j['name']} ({j['when']})"
            else:
                continue
            store.add_feed("checkin", j["bot"], None, title, body)
        for key, old in seen.items():
            if key not in now:
                store.add_feed("checkin", old.get("bot") or "default", None, "Removed a scheduled job", old.get("name") or "A job")
    if seen != now:
        store.set_setting("cron_jobs_seen", now)
    return alerts


LONG_TASK_SEC = 3600


async def scan_long_tasks() -> None:
    """Tells Colin once when a board task has been running for over an hour; it is probably stuck, and it
    holds that bot's only slot."""
    told = set(store.get_setting("long_tasks_told", []) or [])
    tasks = await asyncio.to_thread(board.long_running, LONG_TASK_SEC)
    for t in tasks:
        if t["id"] in told:
            continue
        bot = config.BOTS_BY_ID.get(t.get("assignee") or "")
        name = bot.name if bot else "A Zip"
        body = (f"{name} has been working on \"{t['title'][:80]}\" for over an hour and may be stuck. "
                f"Its other work waits until it finishes.")
        store.add_feed("checkin", bot.id if bot else "default", None, "A task is taking a long time", body)
        mine = (f"I've been working on \"{t['title'][:80]}\" for over an hour and may be stuck." if bot else body)
        await notices.nudge(f"task:{t['id']}:long", "A task is taking a long time", mine, bot.id if bot else None,
                            kind="progress")
        told.add(t["id"])
    running = {t["id"] for t in tasks}
    store.set_setting("long_tasks_told", sorted(told & running))


BUSY_FILE = config.HUB_DIR / "busy.json"


def busy_now() -> dict:
    """What a hub restart would lose: live calls, messages waiting for a busy bot, and a Mac task. A chat
    turn already running is safe (Hermes finishes it and the app reloads it), so it doesn't count."""
    from . import mac, voice
    grant = mac._grant
    return {"calls": len(voice.calls), "queued": sum(len(q) for q in turns._queued.values()),
            "mac": bool(grant and not grant.ended)}


def write_busy(last: dict | None) -> dict:
    """Saves busy_now() for `taffy-hub restart`, which waits for a quiet moment before restarting."""
    now = busy_now()
    if now != (last or {}).get("state") or time.time() - (last or {}).get("at", 0) > 5:
        tmp = BUSY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({**now, "at": time.time()}))
        tmp.replace(BUSY_FILE)
        return {"state": now, "at": time.time()}
    return last


OUTBOX = config.HUB_DIR / "outbox"


def scan_outbox() -> None:
    """Pictures bots shared with share_screen land in the outbox; each becomes an item in that bot's chat."""
    if not OUTBOX.is_dir():
        return
    for f in sorted(OUTBOX.glob("*.json")):
        try:
            note = json.loads(f.read_text())
            bot = note.get("bot") or "default"
            if bot in config.BOTS_BY_ID:
                store.add_feed("image", bot, note.get("task_id"), note.get("caption") or "Picture",
                               json.dumps({"media": note["media"], "caption": note.get("caption") or "",
                                           "width": note.get("width"), "height": note.get("height")}))
        except Exception:
            log.exception("bad outbox note %s", f.name)
        f.unlink(missing_ok=True)


async def scan_consent() -> None:
    """New sign-in requests nudge Colin; every sign-in lands in that bot's timeline."""
    req_cursor = store.get_cursor("consent_request", -1)
    if req_cursor < 0:
        with consent.db() as c:
            req_cursor = c.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()[0]
        store.set_cursor("consent_request", req_cursor)
    for r in consent.requests_after(req_cursor):
        bot = config.BOTS_BY_ID.get(r["bot_id"])
        name = bot.name if bot else r["bot_id"]
        notices.on_request(r)
        if (r.get("kind") or "signin") not in ("signin", "navigate"):
            # An action card (send, publish, delete...): Colin's yes is the only way it happens.
            store.add_feed("signin_request", r["bot_id"], r["task_id"], f"{name} needs your okay", r["reason"])
            reason = " ".join(str(r["reason"] or "").split()).rstrip(".")
            await push(f"{name} needs your okay", f"I need your okay for this: {reason[:1].upper()}{reason[1:]}."[:300],
                       r["bot_id"],
                       urgent=bool(r.get("session_id")) and (r.get("why") or "") == "asked", kind="needs")
            store.set_cursor("consent_request", r["id"])
            continue
        verb = "open" if (r.get("kind") or "signin") == "navigate" else "sign in to"
        store.add_feed("signin_request", r["bot_id"], r["task_id"],
                       f"{name} wants to {'open a site' if verb == 'open' else 'sign in'}",
                       f"{name} wants to {verb} {r['site']}. {r['reason']}".strip())
        # A card from something Colin just asked for is news he is waiting on, even at night. One from answering a
        # teammate is not (Oct 9): the Zip already answered without the site.
        from . import team
        await push(f"{name} wants to {verb} {r['site']}", f"Can I {verb} {r['site']}?", r["bot_id"],
                   urgent=((r.get("why") or "") in ("mismatch", "ask", "sensitive")
                           or bool(r.get("session_id") and r["reason"]))
                   and not team.in_team_session(r.get("session_id")), kind="needs")
        from . import voice   # on a call he may not see the card, so the voice says it
        for call in list(voice.calls.values()):
            if call.bot_id == r["bot_id"] and not call.closed:
                voice.say(call, f"To keep going I need you to allow signing in to {r['site']} in the Zero app.",
                          must=True, tag="signin")
        store.set_cursor("consent_request", r["id"])
    sign_cursor = store.get_cursor("consent_signin", -1)
    if sign_cursor < 0:
        with consent.db() as c:
            sign_cursor = c.execute("SELECT COALESCE(MAX(id), 0) FROM signins").fetchone()[0]
        store.set_cursor("consent_signin", sign_cursor)
    for s in consent.signins_after(sign_cursor):
        store.add_feed("signin", s["bot_id"], None, f"Signed in to {s['site']}", s["reason"])
        store.set_cursor("consent_signin", s["id"])
    # What the Zips did and mention (the "tell" tier), and actions Colin approved, one line each in that Zip's chat.
    act_cursor = store.get_cursor("consent_action", -1)
    if act_cursor < 0:
        with consent.db() as c:
            act_cursor = c.execute("SELECT COALESCE(MAX(id), 0) FROM actions").fetchone()[0]
        store.set_cursor("consent_action", act_cursor)
    for a in consent.actions_after(act_cursor):
        # Done while the person was talking with that Zip: its own reply says what it did, so a second line in the
        # chat only repeats it (Oct 10: "Add both to Trello" got a Done card quoting the request to Trello).
        in_chat = (turns.current(a["bot_id"]) is not None
                   or 0 <= float(a["at"] or 0) - store.said_at(a["bot_id"]) <= 600)
        if a["bot_id"] in config.BOTS_BY_ID and in_chat:
            notices.on_action(a)
        elif a["bot_id"] in config.BOTS_BY_ID:
            how = {"tell": "Done", "button": "Done with your okay", "rule": "Done under a rule you set",
                   "review": "Done as you approved"}.get(a["how"], "Done")
            text = a["summary"][:1].upper() + a["summary"][1:]
            store.add_feed("action", a["bot_id"], None, how, text)
            notices.on_action(a)
        store.set_cursor("consent_action", a["id"])
    # Cards that ran out of time without an answer. The cursor is the latest expiry seen, in milliseconds.
    since = store.get_cursor("consent_expired_ms", -1)
    if since < 0:
        since = int(time.time() * 1000)
        store.set_cursor("consent_expired_ms", since)
    for r in consent.expired_since(since / 1000):
        try:
            tell_expired(r)
        except Exception:
            log.warning("could not tell %s about expired card %s", r.get("bot_id"), r.get("id"), exc_info=True)
        store.set_cursor("consent_expired_ms", math.ceil(r["decided"] * 1000))


def _card_what(r: dict) -> str:
    kind = r.get("kind") or "signin"
    if kind == "signin":
        return f"Sign in to {r['site']}"
    if kind == "navigate":
        return f"Open {r['site']}"
    reason = " ".join(str(r.get("reason") or "").split()).rstrip(".")
    return (reason[:1].upper() + reason[1:])[:160] or "The action on the card"


def tell_expired(r: dict) -> None:
    """A card nobody answered in time: the Zip's chat says so in plain words, a board task waiting on it hears it
    and carries on without it, and a chat's Zip learns it at the start of its next turn (team.context_for), so it
    never tells Colin the card is still waiting. Scheduled work needs nothing: its next run asks again."""
    bot = r.get("bot_id") or ""
    if bot not in config.BOTS_BY_ID:
        return
    what = _card_what(r)
    store.add_feed("signin_decision", bot, r.get("task_id"), what, consent.expired_line(r))
    note = (f"{who.first()} did not answer your card in time, so it closed and this was not done: {what}. Do not "
            f"do it another way. If it still matters, ask {who.first()} again in a sentence.")
    if r.get("task_id"):
        ok, _out = board.answer_block(r["task_id"], note + " Finish what you can without it.")
        if not ok:
            board.comment(r["task_id"], note)
    elif (r.get("why") or "") != "scheduled" and not r.get("routine"):
        store.add_card_note(bot, note)


HANDOFF_NEWS = {"task_done": "is finished", "task_blocked": "is blocked", "task_review": "is waiting for review",
                "task_failed": "hit a problem"}


def _tell_creator(ev: dict, kind: str, body: str) -> None:
    """The Zip that handed off a task hears how it went, so it can finish its own work and tell Colin (Oct 8 QA,
    M10: Builder blocked on Clients' Halloween publish and Clients never heard). The lead already hears through
    the team update."""
    creator, assignee = ev.get("created_by") or "", ev.get("assignee") or "default"
    if kind not in HANDOFF_NEWS or creator in ("", "default", assignee) or creator not in config.BOTS_BY_ID \
            or assignee not in config.BOTS_BY_ID:
        return
    from . import team
    text = (f"The task you handed me, \"{ev['title'][:120]}\" ({ev['task_id']}), {HANDOFF_NEWS[kind]}."
            + (f" {body[:600]}" if body and body != ev["title"] else ""))
    try:
        team.tell(assignee, creator, text, None, from_hub=True)
    except Exception:
        log.warning("could not tell %s about task %s", creator, ev.get("task_id"), exc_info=True)


# How the lead relays the team (#80-#82): the outcome in its own words with the thing itself, not the specialist's
# summary again; who did it in a few words; a block with its cause and what would clear it. Nothing to add after
# news that already reached the phone is a quiet [SILENT] (#75, #76), which the hub never posts or pushes.
UPDATE_GUIDE = ("Tell {first} what changed in one to three plain sentences, in your own words: the result and the "
                "thing itself (the answer, the draft, the link), and who did it in a few words. Do not repeat a "
                "teammate's summary or list what they checked. If something is blocked, say the cause and exactly "
                "what would clear it. If nothing needs {first}, say so in the same breath, not as its own line. "
                "If every line above was already sent to {first}'s phone and you have nothing to add, reply with "
                "only [SILENT].")


async def _team_update(batch: list[tuple[str, str]]) -> bool:
    """One board-update turn in Taffy's session, then one push. Returns False if Taffy did not answer. A reply with
    nothing in it ("." or an acknowledgment) is quiet: it is never posted or pushed, and when the batch held news
    that only Taffy would tell, it counts as no answer, so the news still reaches Colin (plainly, after the retries)."""
    first = who.first()
    lines = "\n".join((f"- (already sent to {who.possessive()} phone) " + line[2:]) if line.startswith("! ")
                      else line for _, line in batch)
    note = (f"[Board update from the hub, not a message from {first}]\n" + lines + "\n\n" +
            UPDATE_GUIDE.format(first=first))
    reply = await turns.run_internal("default", note)
    news = any(not line.startswith("! ") for _, line in batch)
    if not reply or chatter.chatter(reply, chatter.team_names()):
        return not news and bool(reply)   # quiet is fine only when everything already reached the phone
    from . import shortreply   # a long relay shows short in the chat and the notification (shortreply.py)
    reply = await shortreply.for_reply("default", "", reply) or reply
    store.add_feed("message", "default", None, config.BOTS_BY_ID["default"].name, reply)
    # Things that need Colin were pushed the moment they happened; the story goes out only when there is news
    # beyond them, and unless every bot in it is muted or snoozed.
    if news:
        await push("Team update", reply, "default", bots=[b for b, _ in batch], kind="done")   # in the lead's words
    return True


async def run() -> None:
    if store.get_cursor("cron_mtime_ms", -1) < 0:
        store.set_cursor("cron_mtime_ms", int(time.time() * 1000))
    cursor = store.get_cursor("kanban_event", -1)
    if cursor < 0:
        cursor = board.latest_event_id()
        store.set_cursor("kanban_event", cursor)

    # Lines not yet told survive a hub restart.
    pending: list[tuple[str, str]] = [tuple(x) for x in (store.get_setting("pending_updates", []) or [])]
    in_flight: list[tuple[str, str]] = []
    pending_since: float | None = time.time() if pending else None
    tries = 0
    update_task: asyncio.Task | None = None
    tick = 0
    busy_written: dict | None = None
    cron_task: asyncio.Task | None = None

    while True:
        tick += 1
        config.reload_bots()
        try:
            busy_written = write_busy(busy_written)
        except Exception:
            log.exception("busy file failed")
        try:
            for ev in board.events_after(cursor):
                try:
                    item = _feed_item(ev)
                    notices.on_board_event(ev)
                    if ev["kind"] in ("blocked", "review_requested", "review", "completed", "created"):
                        notices.wake("board")
                    if item:
                        kind, title, body = item
                        store.add_feed(kind, ev["assignee"], ev["task_id"], title, f"{ev['title']}\n\n{body}".strip())
                        who_local = ev["assignee"] or "default"
                        if kind == "task_failed":
                            from . import health
                            health.turn_finished(body)   # a board task stopped by the Claude plan's limit
                        waiting_on_signin = kind == "task_blocked" and any(
                            r.get("task_id") == ev["task_id"] for r in consent.pending())
                        if kind in ("task_blocked", "task_review", "task_failed") and not waiting_on_signin:
                            # Needs Colin: told now, plainly, without waiting for Taffy (her own tasks too). A task
                            # blocked on a sign-in already sent its own "wants to sign in" card.
                            await notices.nudge(f"task:{ev['task_id']}:{kind.removeprefix('task_')}", title,
                                                _first_person(kind, ev["title"], body), who_local, kind="needs")
                        _tell_creator(ev, kind, body)
                        if kind != "task_started" and who_local != "default":
                            mark = "!" if kind != "task_done" else "-"   # ! = already pushed to Colin
                            pending.append((who_local, f"{mark} {title}: \"{ev['title']}\" ({ev['task_id']}). {body}".strip()))
                            pending_since = pending_since or time.time()
                            store.set_setting("pending_updates", [list(p) for p in pending + in_flight])
                except Exception:
                    log.exception("could not handle board event %s", ev.get("id"))
                cursor = ev["id"]
                store.set_cursor("kanban_event", cursor)
        except Exception:
            log.exception("board read failed")

        # The team update runs in the background so board and cron scanning keep going.
        if update_task and update_task.done():
            ok = False
            try:
                ok = update_task.result()
            except Exception:
                log.exception("team update failed")
            update_task = None
            if ok:
                tries = 0
                store.set_setting("pending_updates", [list(p) for p in pending])
            else:
                tries += 1
                if tries >= MAX_UPDATE_TRIES:
                    # Taffy could not answer: still tell Colin what happened, plainly.
                    lines = "\n".join(line[2:] for _, line in in_flight)
                    store.add_feed("message", "default", None, "Team update", lines)
                    await push("Team update", lines, "default", bots=[b for b, _ in in_flight], kind="done")
                    tries = 0
                else:
                    pending = in_flight + pending
                    pending_since = pending_since or time.time()
        if pending and update_task is None and time.time() - (pending_since or 0) >= BATCH_WINDOW_SEC:
            in_flight, pending, pending_since = pending, [], None
            update_task = asyncio.create_task(_team_update(in_flight))

        try:
            await scan_consent()
        except Exception:
            log.exception("consent scan failed")
        try:
            scan_outbox()
        except Exception:
            log.exception("outbox scan failed")

        if tick % 5 == 0 and (cron_task is None or cron_task.done()):
            # In the background: deciding whether a report is worth a buzz may take Haiku a few seconds, and sign-in
            # cards and board news must not wait for it. One scan at a time.
            if cron_task is not None and not cron_task.cancelled() and cron_task.exception():
                log.error("cron scan failed", exc_info=cron_task.exception())
            cron_task = asyncio.create_task(scan_cron())
        if tick % 15 == 0:
            try:
                for bot_id, title, body in await asyncio.to_thread(scan_jobs):
                    await notices.nudge(f"job:{bot_id}:{title}:{body[:60]}", title, body, bot_id, kind="progress")
            except Exception:
                log.exception("scheduled job scan failed")
            try:
                await scan_long_tasks()
            except Exception:
                log.exception("long task scan failed")
            try:
                load = board.bot_load()
                busy = set(turns.busy_bots()) | {b for b, d in load.items() if d["working"]}
                waiting = {b for b, d in load.items() if d["needs_you"]}
                for b in await asyncio.to_thread(screen.sweep_idle_desktops, busy, waiting, turns.is_busy):
                    log.info("stopped %s's idle desktop", b)
            except Exception:
                log.exception("idle desktop sweep failed")
        if tick % 30 == 0:
            try:
                await send_morning_digest()
            except Exception:
                log.exception("morning digest failed")
        try:
            await notices.tick()   # proactive questions: off unless Colin turned them on (notices.py)
        except Exception:
            log.exception("proactivity tick failed")
        if tick % 15 == 0:
            try:
                await asyncio.to_thread(screen.sweep_stale_control)
            except Exception:
                log.exception("lease sweep failed")
        await asyncio.sleep(POLL_SEC)
