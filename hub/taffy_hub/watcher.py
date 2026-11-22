""







import asyncio
import json
import logging
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import apns, asked, blockask, board, withdrawn, buzz, chatter, choices, config, consent, desk, events, gotdone, jobretry, mailcard, nightly, notices, notify, notifyprefs, reviewcard, receipts, remindertime, screen, store, task_context, teams, turns, wallpapers, wayforward, who

log = logging.getLogger("taffy.watcher")

POLL_SEC = 2
_rechecking: list = [None]
BATCH_WINDOW_SEC = 60

MAX_UPDATE_TRIES = 3


def _payload(ev: dict) -> dict:
    try:
        return json.loads(ev.get("payload") or "{}") or {}
    except ValueError:
        return {}


def _feed_item(ev: dict) -> tuple[str, str, str] | None:
    ""

    kind = ev["kind"]
    bot = config.BOTS_BY_ID.get(ev["assignee"] or "default")
    who_local = bot.name if bot else (ev["assignee"] or "A Zip")
    p = _payload(ev)
    if kind == "spawned":
        return "task_started", f"{who_local} started", ev["title"]
    if kind == "completed":
        return "task_done", f"{who_local} finished", ev.get("summary") or ev["title"]
    if kind in ("blocked", "block_loop_detected"):


        reason = str(p.get("reason") or "").strip()
        return "task_blocked", f"{who_local} needs you", reason if " " in reason else ev["title"]
    if kind in ("review_requested", "review"):
        return "task_review", f"{who_local} wants your review", ev.get("summary") or ev["title"]
    if kind in ("crashed", "gave_up", "timed_out"):
        return "task_failed", f"{who_local} hit a problem", p.get("error") or ev["title"]
    return None


FINISHED_LINES = False





def _blocked_line(asked: str) -> str:
    asked = " ".join(str(asked or "").split())
    return f"{asked[:1].upper()}{asked[1:]}" if asked else "I need your help with something."


def _first_person(kind: str, task: str, detail: str) -> str:
    ""
    task = " ".join(str(task or "").split())[:80]
    detail = " ".join(str(detail or "").split())
    extra = f" {detail[:1].upper()}{detail[1:]}" if detail and detail != task else ""
    if kind == "task_blocked":
        return _blocked_line(detail)
    if kind == "task_review":
        return f"\"{task}\" is ready for your review.{extra}"
    if kind == "task_failed":
        return f"I hit a problem on \"{task}\".{extra}"
    return f"{task}.{extra}"


def _push_detail(ev: dict, kind: str, body: str) -> str:
    ""
    if kind not in ("task_blocked", "task_review"):
        return body
    try:
        card = (board.get_task(ev["task_id"]) or {}).get("card")
    except sqlite3.Error:
        log.warning("could not read the review card for the push", exc_info=True)
        card = None
    return f"{card['ask']} {card['does']}".strip() if card else reviewcard.to_you(body)


BOOTED_AT = time.time()
RETRY_EVERY_TICKS = 30


async def retry_stuck() -> None:
    try:
        tried = await asyncio.to_thread(board.retry_stuck, BOOTED_AT)
        if tried:
            log.info("handed back blocks a fix may have removed: %s", tried)
    except Exception:
        log.exception("could not retry blocked tasks")


async def restore_waiting() -> None:
    ""

    try:
        restored = await asyncio.to_thread(board.restore_waiting)
        if restored:
            log.info("put %d tasks back to waiting on the person: %s", len(restored), restored)
    except Exception:
        log.exception("could not put tasks back to waiting on the person")


async def park_waits() -> None:
    ""

    try:
        parked = await asyncio.to_thread(board.park_waits)
        if parked:
            log.info("parked tasks that are only waiting: %s", parked)
        moved = await asyncio.to_thread(board.requeue_transient_triage)
        if moved:
            log.info("returned %d tasks parked for a passing block to their Zips", moved)
    except Exception:
        log.exception("could not park waiting tasks")


async def raise_parked() -> None:
    ""

    try:
        made = await wayforward.sweep()
    except Exception:
        log.exception("could not prepare stuck tasks' cards")
        return
    for task_id in made:
        try:
            task = await asyncio.to_thread(board.get_task, task_id) or {}
            card, bot_id = task.get("card"), task.get("assignee") or "default"
            if card and await asked.check(task_id):
                continue
            if not card or store.db().execute(
                    "SELECT 1 FROM feed WHERE kind='task_blocked' AND task_id=? AND created_at>=?",
                    (task_id, float(task.get("filed_at") or 0) - 5)).fetchone():
                continue
            bot = config.BOTS_BY_ID.get(bot_id)
            title = f"{bot.name if bot else 'A Zip'} needs you"

            fid = store.add_feed("task_blocked", bot_id, task_id, title, _blocked_line(card["ask"]),
                                 written_by="hub", written_for="record")

            store.set_plain_title(fid, _blocked_line(card["ask"]))
            await notices.nudge(f"task:{task_id}:blocked", title, _blocked_line(card["ask"]),
                                bot_id, kind="needs", held=f"{title}: {_blocked_line(card['ask'])}", feed_id=fid)
        except Exception:
            log.exception("could not raise the card for %s", task_id)


async def tell_quiet_handoffs() -> None:
    try:
        from . import handoffs
        await asyncio.to_thread(handoffs.tell_late)
        await asyncio.to_thread(handoffs.tell_zero)
    except Exception:
        log.exception("could not check the tasks Zero handed off")


async def close_mailed() -> None:
    try:
        closed = await asyncio.to_thread(reviewcard.close_mailed)
        if closed:
            log.info("closed tasks whose email cards were all sent or closed: %s", closed)
    except Exception:
        log.exception("could not close tasks whose emails had their own cards")


def _by_mail(task_id: str) -> bool:
    ""
    try:
        return bool((board.get_task(task_id) or {}).get("mail_cards"))
    except sqlite3.Error:
        log.warning("could not read task %s", task_id, exc_info=True)
        return False


async def _carry_on(task_id: str) -> bool:
    try:
        return await reviewcard.carry_on(task_id)
    except Exception:
        log.warning("could not check whether %s needs a review card", task_id, exc_info=True)
        return False


def _redraft(task_id: str) -> bool:
    try:
        return reviewcard.redraft(task_id)
    except Exception:
        log.warning("could not hand %s back for a draft card", task_id, exc_info=True)
        return False


async def wake_parked() -> None:
    try:
        woken = await asyncio.to_thread(board.wake_parked)
        if woken:
            log.info("woke parked tasks whose date came: %s", woken)
    except Exception:
        log.exception("could not wake parked tasks")


def _push_sync(title: str, body: str, bot_id: str | None = None, topic: str = "", **opts) -> bool:
    ""
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
               exact: bool = False, judged: dict | None = None, subtitle: str | None = None,
               now: bool = False) -> bool:
    ""
























    decision = judged or {"buzz": True, "source": "hub", "reason": kind or "needs"}
    fid = decision.get("feed_id") or (extra or {}).get("feed_id") or (extra or {}).get("item_id")


    from . import shortreply
    sealed = bool((extra or {}).get("task_id")) and shortreply.task_sealed(extra["task_id"])
    exact = exact or sealed
    sid, _ = notify.sender(bot_id, bots)

    def told(outcome: str, ok: bool) -> bool:
        buzz.record(sid, decision, outcome, fid)
        return ok

    if not apns.ready():
        return told("unsent", False)
    from . import quiet
    if not urgent and isinstance(fid, int) and quiet.folds(fid):

        return told("quiet", False)
    who_local = bots if bots is not None else ([bot_id] if bot_id else [])
    if who_local and not any(notifyprefs.allows(b, kind) for b in who_local):
        return told("off", False)
    if not urgent:
        if who_local and all(store.quiet(b) for b in who_local):
            return told("snoozed", False)

        if not sealed and (decision["source"] == "hub" or (decision["source"] == "report"
                                                           and decision.get("reason") == "marker")):
            rule = await buzz.ruled_out(sid, title, body)
            if rule:
                decision = {**decision, "buzz": False, "reason": "rule", "note": rule[:buzz.NOTE_CHARS]}
                return told("quiet", False)
        if _night():
            held = store.get_setting("held_pushes", []) or []
            held.append({"title": title, "body": body[:300], "at": time.time(), "bot": sid, "kind": kind, "exact": exact,
                         "task_id": (extra or {}).get("task_id")})
            store.set_setting("held_pushes", held[-40:])
            return told("held", True)
    ok = await _send(title, body, bot_id, bots=bots, kind=kind, urgent=urgent, extra=extra, exact=exact,
                     subtitle=subtitle, now=now)
    return told("sent" if ok else "unsent", ok)


async def _send(topic: str, body: str, bot_id: str | None, *, bots: list[str] | None = None, kind: str | None = None,
                urgent: bool = False, extra: dict | None = None, exact: bool = False,
                subtitle: str | None = None, now: bool = False) -> bool:
    ""

    sid, name = notify.sender(bot_id, bots)
    hint = topic if topic and topic.strip() != name else ""
    text = notify.fit(notify.plain(body)) if exact else await notify.line(body, sid, context=hint)
    opts = {"relevance": notify.relevance(kind, urgent), "topic": topic, "hold": not now,
            "subtitle": notify.subtitle(topic if subtitle is None else subtitle, name)}
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


def drop_held(task_id: str) -> int:
    ""

    held = store.get_setting("held_pushes", []) or []
    left = [h for h in held if h.get("task_id") != task_id]
    if len(left) != len(held):
        store.set_setting("held_pushes", left)
    return len(held) - len(left)


async def _send_held(h: dict) -> bool:
    return await _send(h["title"], h["body"], h.get("bot"), kind=h.get("kind", "done"), exact=h.get("exact", False))


async def send_morning_digest() -> None:
    ""


    held = store.get_setting("held_pushes", []) or []
    if not held or _night():
        return
    rolled = [h for h in held if h.get("kind", "done") in ("done", "progress")]
    left = [h for h in held if h not in rolled and not await _send_held(h)]
    if len(rolled) == 1:
        ok = await _send_held(rolled[0])
    else:
        ok = not rolled or await _send("While you slept", f"While you slept, {len(rolled)} updates came in from "
                                                          f"{_names([h.get('bot') or 'default' for h in rolled])}.",
                                       None, kind="done")
    store.set_setting("held_pushes", left if ok else left + rolled)


_beside: dict[str, asyncio.Task] = {}


def _beside_the_loop(name: str, work) -> None:
    ""
    async def safely():
        try:
            await work()
        except Exception:
            log.exception("%s failed", name)
    if name not in _beside or _beside[name].done():
        _beside[name] = asyncio.create_task(safely())


def _cron_dirs() -> list[tuple[str, Path]]:
    dirs = [("default", config.HERMES_HOME / "cron" / "output")]
    for b in config.BOTS:
        if b.id != "default":
            dirs.append((b.id, config.HERMES_HOME / "profiles" / b.id / "cron" / "output"))
    return dirs


def _cron_report(path: Path) -> tuple[str, str, bool] | None:
    ""


    text = path.read_text(errors="replace")
    name = text.splitlines()[0].removeprefix("# Cron Job:").strip() if text else path.parent.name
    body = None
    for marker in ("## Response", "## Output", "## Result"):
        if marker in text:
            body = text.split(marker, 1)[1].strip()
            break
    else:


        if "\n---\n" in text:
            body = text.split("\n---\n", 1)[1].strip()


    if not body or body.startswith("[SILENT]") or re.search(r"(?m)^\s*\[SILENT\]\s*$", body):
        return None


    if body.startswith(jobretry.NOT_PROCESSED):
        return None
    prompt = text.split("## Prompt", 1)[1] if "## Prompt" in text else ""
    for end in ("## Your previous run's output", "## Response"):
        prompt = prompt.split(end, 1)[0]
    return name, body, LATER in prompt.lower()






LATER = "<later>"
REPORT_NOTE_CHARS = 3000


def _is_title(line: str) -> bool:
    return not notify._MARKERS.match(line) and (re.fullmatch(r"\*\*.*\*\*", line.strip()) is not None
                                                or not line.rstrip().endswith((".", "?", "!", ":")))


def _held_text(body: str) -> str:
    ""

    lines = re.sub(r"(?i)</?later>", "", body).strip().splitlines()

    titled = len(lines) > 1 and _is_title(lines[0]) and not re.match(r"\s*(?:[-*\u2022]|\d+[.)])\s", lines[0])
    return "\n".join(lines[1:] if titled else lines).strip()


def split_later(body: str, asked: bool = False) -> tuple[str, str]:
    ""






    i = body.lower().find(LATER)
    if i >= 0:
        return body[:i].strip(), re.sub(r"(?i)</later>", "", body[i + len(LATER):]).strip()
    if not asked:
        return body, ""
    lines = body.strip().splitlines()
    cut = next((k for k, ln in enumerate(lines) if re.match(r"\s*(#|[-*\u2022]\s|\d+[.)]\s)", ln)), len(lines))
    head = lines[:cut]
    if head and _is_title(head[0]):
        head = head[1:]
    lead, rest = "\n".join(head).strip(), "\n".join(lines[cut:]).strip()
    if lead.endswith(":"):
        m = re.search(r"[^\n.!?]*:$", lead)
        lead, rest = lead[:m.start()].strip(), f"{m.group().strip()}\n{rest}".strip()
    return (lead, rest) if lead else ("", body.strip())


def _report_note(name: str, shown: str, later: str) -> str:
    ""

    first = who.first()
    if not shown:

        return (f'Your scheduled job "{name}" ran on its own and showed {first} nothing, since none of it needed '
                f'{first}. This is what it found: "{later[:REPORT_NOTE_CHARS]}" Bring it up only if {first} asks.')
    note = f'Your scheduled job "{name}" posted this in {first}\'s chat on its own: "{shown[:REPORT_NOTE_CHARS]}"'
    if later:
        note += (f" It held this back for later: \"{later[:REPORT_NOTE_CHARS]}\" Bring that up one item at a time, "
                 f"once the first is settled or {first} asks, never all at once.")
    return note


def _cron_once(path: Path) -> bool:
    ""

    try:
        head = path.read_text(errors="replace")[:600]
    except OSError:
        return False
    return re.search(r"^\*\*Schedule:\*\*\s*once\b", head, re.M) is not None


async def relay_watch(bot_id: str, name: str, body: str) -> tuple[str, int | None]:
    ""




    words = notify.report_text(body)
    if not words:
        return "", None
    first = who.first()
    note = (f"{who.hub_note()} Your scheduled check \"{name}\" just reported this:\n\n{words}\n\n"
            f"Recheck what it found first. If the recheck clears it and nothing needs {first}, reply with only "
            f"[SILENT]. Otherwise tell {first} about it here in your own words. This is your own chat, so speak as "
            f"yourself and never "
            f"tell {first} to ask you. If it points to an email or another item, read it first and say what it says. "
            f"Offer the next step yourself, and leave out how the check works. If you need two or more answers "
            f"from {first}, call offer_choices with one questions stack. Never ask them in numbered text. "
            f"Include the relevant findings in reasoning so the answers retain their context.")
    try:
        reply = await turns.run_internal(bot_id, note)
    except Exception:
        log.exception("%s could not tell its %s report itself", bot_id, name)
        reply = None
    try:
        if reply is not None and chatter.empty(reply):
            return "", None
        if reply:
            from . import shortreply
            shown = await shortreply.for_reply(bot_id, None, reply) or reply
            return shown, store.add_feed("message", bot_id, None, turns._name(bot_id), shown,
                                         written_by="zip", written_for="person")


        return words, store.add_feed("checkin", bot_id, None, name, words, written_by="hub", written_for="person")
    finally:
        choices.flush(bot_id)


async def scan_cron() -> None:
    ""







    last = store.get_cursor("cron_mtime_ms", 0)
    seen = set(store.get_setting("cron_seen_at_cursor", []) or [])
    found = []
    for bot_id, d in _cron_dirs():
        if not d.is_dir():
            continue
        for f in d.glob("*/*.md"):
            try:
                m = int(f.stat().st_mtime * 1000)
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
        once = report is not None and _cron_once(f)


        kept = remindertime.delivering(bot_id, f.parent.name) if once else None
        if kept is False:
            report = None
        if report:
            name, body, asked = report
            mark = notify.marker(body)
            if script and mark != "QUIET":

                shown, fid = await relay_watch(bot_id, name, body)
                if shown:


                    told.append((bot_id, name, body if mark else shown, script, fid, once))
            else:
                lead, later = split_later(body, asked)
                shown, later = notify.report_text(lead), notify.report_text(later)
                if shown:
                    if notices.on_cron_report(bot_id, name):
                        shown = await gotdone.fold_brief(shown)
                    fid = store.add_feed("message", bot_id, None, turns._name(bot_id), shown,
                                         written_by="zip", written_for="person") if kept else \
                        store.add_feed("checkin", bot_id, None, name, shown, written_by="zip", written_for="person")
                    told.append((bot_id, name, lead, script, fid, once))
                elif later:


                    told.append((bot_id, name, _held_text(body), script, None, once))
                if shown or later:
                    store.add_card_note(bot_id, _report_note(name, shown, later), store.REPORT_NOTES,
                                        store.REPORT_NOTES_MAX)
        if m > last:
            last, seen = m, {key}
        else:
            seen.add(key)
        store.set_cursor("cron_mtime_ms", last)
        store.set_setting("cron_seen_at_cursor", sorted(seen))
    for bot_id, name, *_ in told:
        if notices.on_cron_report(bot_id, name):
            await notices.post_brief()
    for bot_id, name, body, script, fid, once in told:

        result = gotdone.report(bot_id, name, body, fid) if fid and not script and not once and \
            not notices.on_cron_report(bot_id, name) else None
        if not notifyprefs.allows(bot_id, "needs") and not notifyprefs.allows(bot_id, "progress"):
            continue
        try:
            verdict = await notify.judge_report(bot_id, name, body, script=script, reminder=once,
                                                 brief=bool(fid) and notices.on_cron_report(bot_id, name))
        except Exception:
            log.exception("could not judge %s's %s", bot_id, name)
            continue
        judged = {"buzz": verdict["buzz"], "source": "report", "reason": verdict.get("reason") or ""}
        if not verdict["buzz"]:
            buzz.record(bot_id, judged, "quiet", fid)
            gotdone.report_quiet(result)
        if verdict["buzz"]:
            gotdone.claim(result, gotdone.PUSHED)
            if fid is None:
                fid = store.add_feed("checkin", bot_id, None, name, verdict["line"],
                                     written_by="zip", written_for="person")
            judged["feed_id"] = fid



            await push(name, verdict["line"], bot_id, kind=verdict["kind"], extra={"item_id": fid} if fid else None,
                       judged=judged, **({"exact": True} if once else {}))


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
    ""

    parts = str(expr or "").split()
    if len(parts) != 5:
        return str(expr or "")

    if parts[2:] == ["*", "*", "*"] and not (parts[0].isdigit() and parts[1].isdigit()):
        mstep = re.fullmatch(r"\*/(\d+)", parts[0])
        hstep = re.fullmatch(r"\*/(\d+)", parts[1])
        hours = re.fullmatch(r"(\d+)-(\d+)", parts[1])
        if mstep and parts[1] == "*":
            return f"every {int(mstep.group(1))} minutes"
        if mstep and hours:
            step = int(mstep.group(1))
            last = (59 // step) * step if step else 0
            return (f"every {step} minutes from {_clock_words(int(hours.group(1)), 0)} to "
                    f"{_clock_words(int(hours.group(2)), last)}")
        if parts[0].isdigit() and hstep:
            return f"every {int(hstep.group(1))} hours"
        return SOMETIMES
    if not parts[0].isdigit() or not parts[1].isdigit():
        return SOMETIMES
    minute, hour, dom, month, dow = int(parts[0]), int(parts[1]), parts[2], parts[3], parts[4]
    if not (0 <= minute < 60 and 0 <= hour < 24) or month != "*":
        return SOMETIMES
    at = _clock_words(hour, minute)
    if dom == "*" and dow == "*":
        return f"every day at {at}"
    if dom == "*" and dow in ("1-5", "1,2,3,4,5"):
        return f"weekdays at {at}"
    if dom == "*" and dow in ("0,6", "6,0"):
        return f"weekends at {at}"
    if dom == "*" and (r := re.fullmatch(r"([0-7])-([0-7])", dow)):
        return f"{_CRON_DAYS[r.group(1)]} through {_CRON_DAYS[r.group(2)]} at {at}"
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
    return SOMETIMES


SOMETIMES = "on a schedule"
_ONCE = re.compile(r"^\s*once at (\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})", re.I)


def _once_words(text: str) -> str | None:
    ""
    m = _ONCE.match(text or "") or re.match(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})", text or "")
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None
    return f"once on {d.strftime('%A, %b')} {d.day} at {_clock_words(int(m.group(2)), int(m.group(3)))}"


def _when(j: dict) -> str:
    ""
    s = j.get("schedule") or {}
    expr = str(s.get("expr") or "")
    display = str(s.get("display") or "")
    if expr and (not display or display == expr):
        return cron_words(expr)
    if display:
        if re.fullmatch(r"[\d*/,\-]+(\s+[\d*/,\-]+){4}", display):
            return cron_words(display)
        return _once_words(display) or display
    run_at = str(s.get("run_at") or "")
    return _once_words(run_at) or run_at


FAILING_AFTER = 2


def _finished(j: dict) -> bool:
    ""
    if str(j.get("state") or "") == "completed":
        return True
    r = j.get("repeat") or {}
    times = r.get("times") if isinstance(r, dict) else None
    return bool(times) and int((r.get("completed") or 0)) >= int(times)


def scan_jobs() -> list[tuple[str, str, str, str]]:
    ""




    alerts: list[tuple[str, str, str, str]] = []
    seen = store.get_setting("cron_jobs_seen", None)
    retried = store.get_setting(jobretry.SETTING, None) or {}
    now: dict[str, dict] = {}
    for bot_id, out in _cron_dirs():
        for jid, j in _jobs_of(out.parent / "jobs.json").items():
            if seen is not None and f"{bot_id}:{jid}" not in seen:
                try:
                    from . import mac
                    mac.note_job(bot_id, jid, j.get("created_at"))
                except Exception:
                    log.warning("could not note who made a scheduled job", exc_info=True)
            now[f"{bot_id}:{jid}"] = {"bot": bot_id, "name": str(j.get("name") or "A job")[:80], "when": _when(j),
                                      "sched": json.dumps(j.get("schedule"), sort_keys=True, default=str),
                                      "on": bool(j.get("enabled", True)),
                                      "done": _finished(j),
                                      "failing": int(j.get("failure_streak") or 0) >= FAILING_AFTER}


            if f"{bot_id}:{jid}" in retried:
                now[f"{bot_id}:{jid}"]["failing"] = bool(((seen or {}).get(f"{bot_id}:{jid}") or {}).get("failing"))
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
                owner = config.BOTS_BY_ID.get(j["bot"])
                job = f"{owner.name if owner else 'A Zip'}'s scheduled job \"{j['name']}\""
                held = (f"{job} has failed {FAILING_AFTER} times in a row" if j["failing"] else f"{job} ran normally again")
                store.add_feed("checkin", j["bot"], None, *alert, written_by="hub", written_for="record")
                alerts.append((j["bot"], *alert, held))
            if old is None:
                events.routine_created(j["bot"], j["name"], j["when"])
                continue

            elif j["done"]:
                continue
            elif ((old.get("sched") != j["sched"] if "sched" in old else old.get("when") != j["when"])
                  or old.get("on") != j["on"] or old.get("name") != j["name"]):
                title = "Changed a scheduled job" if j["on"] else "Paused a scheduled job"
                body = f"{j['name']} ({j['when']})"
            else:
                continue
            store.add_feed("checkin", j["bot"], None, title, body, written_by="hub", written_for="record")
        for key, old in seen.items():
            if key not in now:
                store.add_feed("checkin", old.get("bot") or "default", None, "Removed a scheduled job",
                               old.get("name") or "A job",
                               written_by="hub", written_for="record")
    if seen != now:
        store.set_setting("cron_jobs_seen", now)
    return alerts


BUSY_FILE = config.HUB_DIR / "busy.json"




JOB_CLAIM_LIVE_S = 300


def _running_jobs() -> int:
    ""
    n = 0
    for _bot, out in _cron_dirs():
        for j in _jobs_of(out.parent / "jobs.json").values():
            claim = j.get("fire_claim")
            try:
                age = time.time() - datetime.fromisoformat(claim["at"]).timestamp()
            except (TypeError, KeyError, ValueError):
                continue
            if 0 <= age < JOB_CLAIM_LIVE_S:
                n += 1
    return n


def busy_now() -> dict:
    ""




    from . import mac, voice
    working = sum(1 for b in config.BOTS_BY_ID if b in turns._turns or turns._lock(b).locked())
    return {"calls": len(voice.calls), "queued": sum(len(q) for q in turns._queued.values()),
            "mac": mac.in_use(), "jobs": _running_jobs(), "turns": working}


def write_busy(last: dict | None) -> dict:
    ""
    now = busy_now()
    if now != (last or {}).get("state") or time.time() - (last or {}).get("at", 0) > 5:
        tmp = BUSY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({**now, "at": time.time()}))
        tmp.replace(BUSY_FILE)
        return {"state": now, "at": time.time()}
    return last


OUTBOX = config.HUB_DIR / "outbox"


def scan_outbox() -> None:
    ""
    if not OUTBOX.is_dir():
        return
    for f in sorted(OUTBOX.glob("*.json")):
        try:
            note = json.loads(f.read_text())
            bot = note.get("bot") or "default"
            if note.get("kind") == "calendar_created":
                try:
                    if bot in config.BOTS_BY_ID:
                        events.calendar_created(bot, note["args"])
                except Exception:
                    log.exception("calendar receipt %s could not be recorded; will retry", f.name)
                    continue
                f.unlink(missing_ok=True)
                continue
            if note.get("kind") == "mail_sent":
                try:
                    if not isinstance(note.get("args"), dict):
                        raise ValueError("Sent receipt arguments must be an object")
                    if bot in config.BOTS_BY_ID:
                        events.mail_sent(bot, note["args"])
                except (mailcard.MailError, TypeError, ValueError, KeyError):
                    log.warning("invalid sent receipt %s; kept for inspection", f.name)
                    f.replace(f.with_suffix(".invalid"))
                    continue
                except Exception:
                    log.exception("sent receipt %s could not be recorded; will retry", f.name)
                    continue
                f.unlink(missing_ok=True)
                continue
            if bot in config.BOTS_BY_ID:
                store.add_feed("image", bot, note.get("task_id"), note.get("caption") or "Picture",
                               json.dumps({"media": note["media"], "caption": note.get("caption") or "",
                                           "width": note.get("width"), "height": note.get("height")}),
                                               written_by="zip", written_for="person")
        except Exception:
            log.exception("bad outbox note %s", f.name)
        f.unlink(missing_ok=True)


async def scan_consent() -> None:
    ""
    req_cursor = store.get_cursor("consent_request", -1)
    if req_cursor < 0:
        with consent.db() as c:
            req_cursor = c.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()[0]
        store.set_cursor("consent_request", req_cursor)
    for r in consent.requests_after(req_cursor):
        bot = config.BOTS_BY_ID.get(r["bot_id"])
        name = bot.name if bot else r["bot_id"]
        try:

            taken = teams.on_request(r)
        except Exception:
            log.exception("could not turn a request into a team need")
            taken = False
        if taken:
            if r.get("reason_state") == consent.REASON_READY:
                consent.request_announced(r["id"])
            store.set_cursor("consent_request", max(req_cursor, r["id"]))
            continue
        notices.on_request(r)
        if (r.get("kind") or "signin") not in ("signin", "navigate"):


            said = task_context.computer_words(r.get("title") or r["reason"], r.get("kind") or "")
            store.add_feed("signin_request", r["bot_id"], r["task_id"], f"{name} needs your okay", said,
                           written_by="zip", written_for="person")
            reason = " ".join(str(said or "").split()).rstrip(".")
            if r.get("title"):

                reason = re.sub(rf"\b{re.escape(name)}\b", "I", reason)
            from . import mac
            if mac.okay_here(r):

                await mac.show_okay(r, f"{reason[:1].upper()}{reason[1:]}")
                if r.get("reason_state") == consent.REASON_READY:
                    consent.request_announced(r["id"])
                store.set_cursor("consent_request", max(req_cursor, r["id"]))
                continue
            await push(f"{name} needs your okay", f"I need your okay for this: {reason[:1].upper()}{reason[1:]}."[:300],
                       r["bot_id"],
                       urgent=bool(r.get("session_id")) and (r.get("why") or "") == "asked", kind="needs")
            if r.get("reason_state") == consent.REASON_READY:
                consent.request_announced(r["id"])
            store.set_cursor("consent_request", max(req_cursor, r["id"]))
            continue
        verb = "open" if (r.get("kind") or "signin") == "navigate" else "sign in to"
        store.add_feed("signin_request", r["bot_id"], r["task_id"],
                       f"{name} wants to {'open a site' if verb == 'open' else 'sign in'}",
                       f"{name} wants to {verb} {r['site']}. {r['reason']}".strip(),
                           written_by="zip", written_for="person")


        from . import team
        await push(f"{name} wants to {verb} {r['site']}", f"Can I {verb} {r['site']}?", r["bot_id"],
                   urgent=((r.get("why") or "") in ("mismatch", "ask", "sensitive")
                           or bool(r.get("session_id") and r["reason"]))
                   and not team.in_team_session(r.get("session_id")), kind="needs")
        from . import voice
        for call in list(voice.calls.values()):
            if call.bot_id == r["bot_id"] and not call.closed:
                voice.say(call, f"To keep going I need you to allow signing in to {r['site']} in the Zero app.",
                          must=True, tag="signin")
        store.set_cursor("consent_request", max(req_cursor, r["id"]))
    sign_cursor = store.get_cursor("consent_signin", -1)
    if sign_cursor < 0:
        with consent.db() as c:
            sign_cursor = c.execute("SELECT COALESCE(MAX(id), 0) FROM signins").fetchone()[0]
        store.set_cursor("consent_signin", sign_cursor)
    from . import team
    for s in consent.signins_after(sign_cursor):



        if said := receipts.signin_worth_saying(s["site"], s.get("how")):
            store.add_feed("checkin", s["bot_id"], None, "Sign-in", said, written_by="hub", written_for="record")
        store.set_cursor("consent_signin", s["id"])

    act_cursor = store.get_cursor("consent_action", -1)
    if act_cursor < 0:
        with consent.db() as c:
            act_cursor = c.execute("SELECT COALESCE(MAX(id), 0) FROM actions").fetchone()[0]
        store.set_cursor("consent_action", act_cursor)
    for a in consent.actions_after(act_cursor):


        in_chat = (turns.current(a["bot_id"]) is not None
                   or 0 <= float(a["at"] or 0) - store.said_at(a["bot_id"]) <= 600)
        if a["bot_id"] in config.BOTS_BY_ID and in_chat:
            notices.on_action(a)
        elif a["bot_id"] in config.BOTS_BY_ID and a.get("kind") == "signin":
            notices.on_action(a)
        elif a["bot_id"] in config.BOTS_BY_ID and DRAFT_PR in (a.get("summary") or ""):

            notices.on_action(a)
        elif a["bot_id"] in config.BOTS_BY_ID:
            how = {"tell": "Done", "button": "Done with your okay", "rule": "Done under a rule you set",
                   "review": "Done as you approved"}.get(a["how"], "Done")
            text = receipts.line(a["summary"])
            step = team.answering(a["bot_id"], float(a["at"] or time.time())) if " to answer " in text else None
            fid = store.add_feed("action", a["bot_id"], None, how, text, step_of=step,
                                 written_by="hub", written_for="record")
            if step:
                from . import teamvoice
                teamvoice.schedule_step(fid)
            notices.on_action(a)
        store.set_cursor("consent_action", a["id"])

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
    ""


    bot = r.get("bot_id") or ""
    if bot not in config.BOTS_BY_ID:
        return
    what = _card_what(r)
    store.add_feed("signin_decision", bot, r.get("task_id"), what, consent.expired_line(r),
                   written_by="hub", written_for="record")
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


def _handed_off_by(ev: dict) -> str | None:
    ""

    creator, assignee = ev.get("created_by") or "", ev.get("assignee") or "default"
    if creator != assignee and creator in config.BOTS_BY_ID and assignee in config.BOTS_BY_ID:
        return creator
    return None


def _exchange(ev: dict, asker: str) -> int:
    ""
    from . import team
    return team.open_exchange("task", asker, ev["assignee"], (ev.get("body") or "").strip() or ev["title"], ev["title"],
                              task_id=ev["task_id"])


TELL_MAX = 1200


def _fit(text: str, room: int) -> str:
    ""
    if len(text) <= room:
        return text
    more = " The rest is in the task's note."
    cut = text[:room - len(more)]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > 0 else cut.rsplit(" ", 1)[0]) + more


def _tell_creator(ev: dict, kind: str, body: str) -> None:
    ""


    creator = _handed_off_by(ev)
    if kind not in HANDOFF_NEWS or creator in (None, "default"):
        return
    from . import team
    head = f"The task you handed me, \"{ev['title'][:120]}\" ({ev['task_id']}), {HANDOFF_NEWS[kind]}."
    text = head + (" " + _fit(body, TELL_MAX - len(head) - 1) if body and body != ev["title"] else "")
    try:

        team.tell(ev.get("assignee") or "default", creator, text, None, from_hub=True,
                  shown=kind not in ("task_done", "task_failed"))
    except Exception:
        log.warning("could not tell %s about task %s", creator, ev.get("task_id"), exc_info=True)








DRAFT_PR = " and open a draft pull request on "

UPDATE_GUIDE = ("Tell {first} what changed in one to three plain sentences, in your own words: the result and the "
                "thing itself (the answer, the draft, the link). Name who did it only when ownership matters. Do not repeat a "
                "teammate's summary or list what they checked. Do not restate anything you told {first} when you "
                "handed the work off: who it goes to, who is copied, which thread or account it is in, or that "
                "nothing is sent without an okay. A finding that only confirms what the hand-off expected (such "
                "as that someone has not replied) gets nothing: reply with only [SILENT]. The hub tells {first} about "
                "finished work itself, as a notification or in the next recap, so a result {first} did not ask for "
                "that needs nothing from {first} gets only [SILENT] from you. When a result and a question about the "
                "same work arrive together, say what was done first, then ask the one question. Never name pull "
                "request numbers, task ids, branch or variable names, commands or file paths: say what it does for "
                "{first}. "
                "If {first} just said they are lost or it is too much, name the one thing that matters most and "
                "ask one question, with no list. If something is blocked, say the cause "
                "and exactly what would clear it. If nothing needs {first}, say so in the same breath, not as its "
                "own line. A line marked as already sent to {first}'s phone reached {first} as its own notification: "
                "never tell {first} that line again, even in other words, and use it only for context. If you have "
                "nothing to add beyond such lines, reply with only [SILENT]. If the news is the materials for an "
                "assignment or project {first} is about to sit down to, and you know its pages, also call "
                "offer_project with them, so one tap on {first}'s Mac opens them beside the app {first} writes in.")


async def tell_plainly(batch: list[tuple]) -> None:
    ""






    batch = _told_first(batch)[0]
    items = [i for i in batch if not i[1].startswith("! ") and _plain(i)]
    if not items:
        return
    gotdone.said_in_chat(_task_ids(items))
    zips = list(dict.fromkeys(i[0] for i in items))
    said = [_plain(i) for i in items]
    text = "\n".join(f"{notify.sender(i[0])[1]}: {line}" for i, line in zip(items, said))
    fid = store.add_feed("message", "default", None, notify.sender("default")[1], text,
                         written_by="model", written_for="person")
    decision = buzz.decide("default", text, asked=_asked(batch))
    if len(zips) == 1:
        decision["line"] = " ".join(said)
    await _relay("default", _names(zips), text, fid, decision, bots=zips, kind="done",
                 sender=zips[0] if len(zips) == 1 else None)


_TASK_ID = re.compile(r'" \((t_\w+)\)\.')


def _plain(item: tuple) -> str:
    ""

    if len(item) > 2:
        return item[2] or ""
    m = _TASK_ID.search(item[1])
    return " ".join((item[1][2:m.start()] + '".' if m else item[1][2:]).split())


def _task_ids(batch: list[tuple]) -> list[str]:
    ""
    found = (None if i[1].startswith("! ") else _TASK_ID.search(i[1]) for i in batch)
    return [m.group(1) for m in found if m]


def _told_first(batch: list[tuple]) -> tuple[list[tuple], bool]:
    ""

    out, any_told = [], False
    for item in batch:
        m = None if item[1].startswith("! ") else _TASK_ID.search(item[1])
        if m and gotdone.told(m.group(1)):
            out.append((item[0], "! " + item[1][2:], *item[2:]))
            any_told = True
        else:
            out.append(item)
    return out, any_told


def task_asked(task_id: str) -> bool:
    ""

    try:
        origin = consent.task_origin(task_id)
    except Exception:
        log.warning("could not read where task %s came from", task_id, exc_info=True)
        return False
    return origin is not None and origin["kind"] in ("task", "task2")


def _asked(batch: list[tuple[str, str]]) -> bool:
    ""

    return any(task_asked(t) for t in _task_ids(batch))


async def _relay(bot_id: str, topic: str, text: str, fid: int | None, decision: dict, sender: str | None = None,
                 **opts) -> bool:
    ""




    if not decision["buzz"]:
        buzz.record(bot_id, decision, "quiet", fid)
        sent = False
    else:
        line = decision.get("line")
        sent = await push(topic, line or text, sender or bot_id, judged={**decision, "feed_id": fid},
                          **({"exact": True} if line else {}), **opts)
    from . import hermes, todo
    try:
        todo.from_relay(bot_id, fid, hermes.last_todo.pop(bot_id, None) or [], decision, sent)
    except Exception:
        log.warning("could not put %s's message on the list", bot_id, exc_info=True)
    return sent


def _list_note(first: str) -> str:
    ""

    from . import todo
    try:
        lines = todo.describe("default")
    except Exception:
        log.warning("could not read the list for the team update", exc_info=True)
        return ""
    return (f"{first}'s list now, top to bottom:\n{lines}\nIf this news should be first, put it there with app_todo_list "
            "order and a reason in under ten words. Finish a line this news shows is done.")


async def _team_update(batch: list[tuple[str, str]]) -> bool:
    ""






    first = who.first()
    batch, results_told = _told_first(batch)
    sent = [i[1][2:] for i in batch if i[1].startswith("! ")]
    if len(sent) == len(batch) and not results_told:



        for line in sent:
            store.add_card_note("default", f"Board news {first} already got on {who.possessive()} phone as its own "
                                           f"notification. Do not tell {first} again; use it if asked: {line}",
                                queue=store.BOARD_NOTES, keep=store.BOARD_NOTES_MAX)
        return True
    lines = "\n".join((f"- (already sent to {who.possessive()} phone) " + i[1][2:]) if i[1].startswith("! ")
                      else i[1] for i in batch)
    note = (f"[Board update from the hub, not a message from {first}]\n" + lines + "\n\n" +
            UPDATE_GUIDE.format(first=first) + "\n\n" + buzz.guide("default") + "\n\n" + _list_note(first))
    try:
        reply, mark = buzz.take("default", await turns.run_internal("default", note))
        news = any(not i[1].startswith("! ") for i in batch)
        if not reply:
            return False
        if chatter.chatter(reply, chatter.team_names()):


            if news and _asked(batch):
                await tell_plainly(batch)
            return True
        from . import shortreply
        reply = await shortreply.for_reply("default", "", reply) or reply
        fid = store.add_feed("message", "default", None, config.BOTS_BY_ID["default"].name, reply,
                             written_by="zip", written_for="person")


        if news:
            gotdone.said_in_chat(_task_ids(batch))

            told = list(dict.fromkeys(i[0] for i in batch if not i[1].startswith("! ")))
            await _relay("default", _names(told), reply, fid, buzz.decide("default", reply, mark, _asked(batch)),
                         bots=told, kind="done")
        return True
    finally:
        choices.flush("default")


async def _status_line(ev: dict) -> None:
    ""

    from . import doing
    if not doing.ENABLED:
        return
    try:
        brief = await asyncio.to_thread(board.get_task, ev["task_id"]) or {}
        if brief.get("status") == "running":
            doing.start({"id": ev["task_id"], "title": ev["title"], "body": brief.get("body"),
                         "assignee": ev["assignee"], "created_by": brief.get("created_by"), "status": "running"})
    except Exception:
        log.warning("could not start the status line for task %s", ev.get("task_id"), exc_info=True)




TASK_ENDS = {"completed": "The work this asked about is done.", "archived": "The work this asked about was stopped."}


async def _settle_ended(ev: dict) -> None:
    from . import things, waits
    try:
        await asyncio.to_thread(things.settle, {"kind": "task", "id": ev["task_id"]}, things.DONE, TASK_ENDS[ev["kind"]])
    except Exception:
        log.warning("could not close the open questions of %s", ev.get("task_id"), exc_info=True)
    try:

        await waits.settle_task(ev["task_id"], ev.get("title") or "",
                                "finished" if ev["kind"] == "completed" else "been stopped", ev.get("summary") or "")
    except Exception:
        log.warning("could not reconcile the waits of %s", ev.get("task_id"), exc_info=True)


def lead_line(ev: dict, title: str, body: str, told: bool) -> str:
    ""

    from . import shortreply
    said = "" if shortreply.task_sealed(ev) else body
    return f"{'!' if told else '-'} {title}: \"{ev['title']}\" ({ev['task_id']}). {said}".strip()



SETTLES = ("scheduled", "unblocked", "specified", "promoted", "claimed", "completed", "archived")


async def _keeps_question(ev: dict) -> bool:
    ""



    try:
        task = await asyncio.to_thread(board.get_task, ev["task_id"]) or {}
    except sqlite3.Error:
        log.warning("could not read task %s for its block", ev["task_id"], exc_info=True)
        return False
    if not task.get("standing"):
        return False
    reason = str(_payload(ev).get("reason") or "")
    if _payload(ev).get("kind") == "capability" and reason:
        await asyncio.to_thread(wayforward._report_limit, task, reason, reason)
    await asyncio.to_thread(board.keep_waiting, ev["task_id"])
    log.info("%s blocked again with its question to the person still open: it keeps waiting", ev["task_id"])
    return True


async def board_event(ev: dict) -> tuple[str, str, str] | None:
    ""







    from . import team
    if ev["kind"] in SETTLES:


        try:
            await asyncio.to_thread(withdrawn.settle, ev["task_id"])
        except Exception:
            log.warning("could not settle the card on %s", ev["task_id"], exc_info=True)
    if ev["kind"] == "completed":
        from . import loopevents
        loopevents.task_completed(ev)
        try:

            await asyncio.to_thread(board.keep_artifacts, ev["task_id"])
        except Exception:
            log.warning("could not keep the files of %s", ev.get("task_id"), exc_info=True)
    if ev["kind"] in TASK_ENDS:
        await _settle_ended(ev)
    if ev["kind"] in ("blocked", "block_loop_detected") and await _keeps_question(ev):
        return None
    if ev["kind"] in ("blocked", "block_loop_detected") and await asyncio.to_thread(board.park_waits, ev["task_id"]):

        notices.wake("board")
        return None
    item = _feed_item(ev)
    notices.on_board_event(ev)
    if ev["kind"] in ("blocked", "review_requested", "review", "completed", "created"):
        notices.wake("board")
    asker = _handed_off_by(ev)
    if asker and ev["kind"] == "created":
        _exchange(ev, asker)
    if asker:
        from . import passcard
        passcard.touch_task(ev["task_id"])
    if not item:
        return None
    kind, title, body = item
    parked_ask = False
    if kind == "task_review" and _by_mail(ev["task_id"]):
        return None
    if kind == "task_review" and await asyncio.to_thread(_redraft, ev["task_id"]):
        return None
    if kind == "task_review" and await _carry_on(ev["task_id"]):
        return None
    asks = False
    if ev["kind"] == "blocked" and blockask.quiet(_payload(ev).get("reason") or "", _payload(ev).get("kind"), ev["title"]):


        try:
            asks = await wayforward.asks_him(ev["task_id"])
        except Exception:
            log.warning("could not judge whether the block on %s asks the person something", ev["task_id"], exc_info=True)
    if ev["kind"] == "blocked" and not asks and blockask.quiet(_payload(ev).get("reason") or "", _payload(ev).get("kind"),
                                                               ev["title"]):


        if not asker:
            try:
                await wayforward.stopped_line(ev["task_id"], ev["title"], _payload(ev).get("reason") or "", ev["assignee"])
            except Exception:
                log.warning("could not say that %s stopped", ev["task_id"], exc_info=True)
        return None
    if ev["kind"] == "blocked" and blockask.asks_nothing(_payload(ev).get("reason") or "", _payload(ev).get("kind"),
                                                         ev["title"]):
        await retry_stuck()
        return None
    if ev["kind"] == "block_loop_detected":


        try:
            decided = await wayforward.prepare(ev["task_id"])
        except Exception:
            log.warning("could not prepare the card for %s", ev["task_id"], exc_info=True)
            decided = ""
        task = await asyncio.to_thread(board.get_task, ev["task_id"]) or {}
        parked = task.get("raw_status") == "triage" and not task.get("on_card") and \
            blockask.quiet(task.get("raw_reason") or "", task.get("block_kind"), ev["title"])
        if decided in ("back", "waiting", "zip", "reported", "held") or (parked and not task.get("card")):
            return None
        if parked:
            body = task["card"]["ask"]
            parked_ask = True
    if kind == "task_blocked":



        try:
            task = await asyncio.to_thread(board.get_task, ev["task_id"]) or {}
        except sqlite3.Error:
            log.warning("could not read task %s for its block", ev["task_id"], exc_info=True)
            task = None
        if task is not None and not task.get("card") and not task.get("on_card") and \
                not blockask.signs_in(_payload(ev).get("reason") or "", ev["title"]):
            log.info("block on %s asks nothing of the person: no row or push", ev["task_id"])
            return None
        if asks and task and task.get("card"):
            body = task["card"]["ask"]

        try:
            if await asked.check(ev["task_id"]):
                return None
        except Exception:
            log.warning("could not check %s against the person's earlier answers", ev["task_id"], exc_info=True)
        if ev["kind"] == "blocked" and (asker or _payload(ev).get("kind") in blockask.QUIET_KINDS):


            try:
                if await wayforward.route_question(ev["task_id"]):
                    return None
            except Exception:
                log.warning("could not judge who answers the block on %s", ev["task_id"], exc_info=True)
        try:
            await wayforward.explain(ev["task_id"])
        except Exception:
            log.warning("could not write the background for %s", ev["task_id"], exc_info=True)
    if kind == "task_started":
        await _status_line(ev)
    handed_off_end = bool(asker) and kind in ("task_done", "task_failed")
    finished = f"I finished \u201c{ev['title']}\u201d."
    shown, known, done = body, False, True
    between = False
    result_line, row_id = "", None
    if kind == "task_done" and FINISHED_LINES:



        from . import finishedline
        written = await finishedline.write(ev["assignee"] or "default", ev["title"], body, task=ev)
        shown, known, done = written if written else (finished, False, True)
        result_line = written[0] if written else ""




        from . import passcard
        between = bool(asker and written and done and not known and not passcard.of_task(ev["task_id"])
                       and await finishedline.between_zips(
            ev["assignee"] or "default", asker, ev["title"], body, shown, task=ev))
        known = known or between
    if handed_off_end:
        note = body if kind == "task_done" else ("I couldn't finish this." + (f" {body}" if body != ev["title"] else ""))
        team.close_exchange(_exchange(ev, asker), note, ev.get("worked"))
    if not done:
        log.info("task %s closed with nothing done: no row, relay or push", ev["task_id"])
        return None





    from . import passcard
    if kind != "task_started" and (not handed_off_end or board.chat_looked(ev["assignee"], ev["task_id"])
                                   or passcard.of_task(ev["task_id"])):

        said = _blocked_line(_push_detail(ev, kind, body)) if kind == "task_blocked" else f"{ev['title']}\n\n{shown}"
        said = receipts.without_refs(said).strip()


        if kind == "task_blocked" and parked_ask:
            row_id = fid = store.add_feed(kind, ev["assignee"], ev["task_id"], title, said, written_by="hub",
                                          written_for="record")
        elif kind == "task_done" and result_line:
            row_id = fid = store.add_feed(kind, ev["assignee"], ev["task_id"], title, said, written_by="model",
                                          written_for="person")
        elif kind == "task_done":
            row_id = fid = store.add_feed(kind, ev["assignee"], ev["task_id"], title, said, written_by="hub",
                                          written_for="person")
        elif kind == "task_failed":
            row_id = fid = store.add_feed(kind, ev["assignee"], ev["task_id"], title, said, written_by="zip",
                                          written_for="person")
        else:
            row_id = fid = store.add_feed(kind, ev["assignee"], ev["task_id"], title, said, written_by="zip",
                                          written_for="hub")
        if known:
            from . import quiet
            quiet.save(fid, {"quiet": "relay", "quiet_title": None,
                             "quiet_why": "Between your helpers" if between else "You already knew"})
    who_local = ev["assignee"] or "default"
    if kind == "task_failed":
        from . import health
        health.turn_finished(body)
    waiting_on_signin = kind == "task_blocked" and any(r.get("task_id") == ev["task_id"] for r in consent.pending())
    told = waiting_on_signin
    detail = ""
    if kind in ("task_blocked", "task_review", "task_failed") and not waiting_on_signin:

        pushed = _push_detail(ev, kind, body)
        detail = pushed if kind != "task_failed" else ""

        led = await gotdone.result_then_ask(who_local, body, pushed, task=ev) if kind == "task_review" else ""
        told = bool(await notices.nudge(f"task:{ev['task_id']}:{kind.removeprefix('task_')}",
                                        title, led or _first_person(kind, ev["title"], pushed),
                                        who_local, kind="needs", exact=bool(led),
                                        held=f"{title}: {_blocked_line(pushed)}" if kind == "task_blocked"
                                        else f"{title} on \"{ev['title'][:80]}\"", feed_id=row_id))
    from . import recheck
    if kind == "task_done" and recheck.closed_quietly(ev["task_id"]):
        return None
    if kind == "task_done" and not known:
        gotdone.finished(ev, result_line, row_id, body)
    _tell_creator(ev, kind, body)
    if kind == "task_started" or who_local == "default" or (between and asker != "default"):
        return None



    plain = "" if known else (shown if FINISHED_LINES else finished) if kind == "task_done" \
        else _first_person(kind, ev["title"], detail)
    return who_local, lead_line(ev, title, body, told), plain


async def run() -> None:
    if store.get_cursor("cron_mtime_ms", -1) < 0:
        store.set_cursor("cron_mtime_ms", int(time.time() * 1000))
    cursor = store.get_cursor("kanban_event", -1)
    if cursor < 0:
        cursor = board.latest_event_id()
        store.set_cursor("kanban_event", cursor)


    pending: list[tuple] = [tuple(x) for x in (store.get_setting("pending_updates", []) or [])]
    in_flight: list[tuple] = []
    pending_since: float | None = time.time() if pending else None
    tries = 0
    update_task: asyncio.Task | None = None
    tick = 0
    busy_written: dict | None = None
    cron_task: asyncio.Task | None = None
    resort_task: asyncio.Task | None = None

    while True:
        tick += 1
        config.reload_bots()
        try:
            from . import loopevents
            for bot_id in config.BOTS_BY_ID:
                loopevents.sync_waits(bot_id)
            loopevents.retry_pending()
        except Exception:
            log.exception("could not reconcile loop events")
        try:
            busy_written = write_busy(busy_written)
        except Exception:
            log.exception("busy file failed")
        try:
            for ev in board.events_after(cursor):
                try:
                    line = await board_event(ev)
                    if line:
                        pending.append(line)
                        pending_since = pending_since or time.time()
                        store.set_setting("pending_updates", [list(p) for p in pending + in_flight])
                except Exception:
                    log.exception("could not handle board event %s", ev.get("id"))
                cursor = ev["id"]
                store.set_cursor("kanban_event", cursor)
        except Exception:
            log.exception("board read failed")

        if tick == 1:
            await restore_waiting()
        if tick % RETRY_EVERY_TICKS == 1:
            await park_waits()
            await retry_stuck()
            await raise_parked()
            await wake_parked()
            await close_mailed()
            await tell_quiet_handoffs()


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
                    await tell_plainly(in_flight)
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


            if cron_task is not None and not cron_task.cancelled() and cron_task.exception():
                log.error("cron scan failed", exc_info=cron_task.exception())
            cron_task = asyncio.create_task(scan_cron())
        if tick % 15 == 0:
            try:

                _beside_the_loop("judge", jobretry.judge_open)
                await asyncio.to_thread(jobretry.sweep)
                for bot_id, title, body, held in await asyncio.to_thread(scan_jobs):
                    await notices.nudge(f"job:{bot_id}:{title}:{body[:60]}", title, body, bot_id, kind="progress",
                                        held=held)
            except Exception:
                log.exception("scheduled job scan failed")
            try:
                load = board.bot_load()
                busy = set(turns.busy_bots()) | {b for b, d in load.items() if d["working"]}
                waiting = {b for b, d in load.items() if d["needs_you"]}
                for b in await asyncio.to_thread(screen.sweep_idle_desktops, busy, waiting, turns.is_busy):
                    log.info("stopped %s's idle desktop", b)
            except Exception:
                log.exception("idle desktop sweep failed")
        try:
            await asyncio.to_thread(desk.send_due)
        except Exception:
            log.exception("phone follow-ups failed")
        if tick % 30 == 0:
            try:
                await send_morning_digest()
            except Exception:
                log.exception("morning digest failed")
            try:
                from . import todo
                if (resort_task is None or resort_task.done()) and todo.morning_due():
                    resort_task = asyncio.create_task(todo.resort())
            except Exception:
                log.exception("the list's morning re-sort failed")
        if tick % 300 == 0:
            try:
                from . import problems
                moved = await asyncio.to_thread(problems.triage)
                if any(moved.values()):
                    log.info("problem reports moved: %s", moved)
            except Exception:
                log.exception("problem report triage failed")
            try:
                from . import stalls
                await asyncio.to_thread(stalls.sweep)
            except Exception:
                log.exception("stalled task sweep failed")
        try:
            from . import loopcheck
            loopcheck.tick()
        except Exception:
            log.exception("daily note check failed")
        try:
            await gotdone.tick()
        except Exception:
            log.exception("the recap check failed")
        if tick % 15 == 0:
            _beside_the_loop("tell", nightly.tick)
        try:
            await notices.tick()
        except Exception:
            log.exception("proactivity tick failed")
        if tick % 15 == 0:
            try:
                await asyncio.to_thread(screen.sweep_stale_control)
            except Exception:
                log.exception("lease sweep failed")
            try:
                await asyncio.to_thread(wallpapers.ensure_all)
            except Exception:
                log.exception("setting computer backgrounds failed")


            if _rechecking[0] is None or _rechecking[0].done():
                _rechecking[0] = asyncio.create_task(choices.recheck())
            try:
                from . import openloops
                openloops.expire_asks()
            except Exception:
                log.exception("closing old asks failed")
        await asyncio.sleep(POLL_SEC)
