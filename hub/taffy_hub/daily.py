""































import asyncio
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime

from . import config, home

log = logging.getLogger("taffy.daily")

SETTING = "daily_checks"
CHECK_HOUR = 5
TICK_SEC = 5 * 60
MAIL_QUIET = 3 * 86400
BRIEF_STALE = 30 * 3600
LATE = 3600
DAY = 86400
NOT_A_BUG_QUIET = 7 * DAY

SKIP_ZIPS = config.PRIVATE_ZIPS
SEND_CARD_SKIPPED = ("Skipped: there is no dry mode that builds a send card. Test mode reports what it would send "
                     "without making the card.")
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DIGITS = re.compile(r"\d{5,}")


def _scrub(text, limit: int = 200) -> str:
    ""
    return _DIGITS.sub("[number]", _ADDRESS.sub("[address]", " ".join(str(text or "").split())))[:limit]


def _result(name: str, state: str, detail: str, about: str = "default", where: str = "", expected: str = "",
            evidence: str = "") -> dict:
    ""
    return {"name": name, "state": state, "detail": detail, "at": time.time(), "about": about, "where": where,
            "expected": expected, "evidence": evidence}


def _iso(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None




def check_mail(now: float) -> dict[str, dict]:
    from . import mail
    out = {}
    for acct in home.mail_accounts():
        aid = str(acct.get("id") or "")
        owner = str(acct.get("owner_zip") or "default")
        if aid not in mail.ACCOUNTS or owner in SKIP_ZIPS:
            continue
        about = owner if owner in config.BOTS_BY_ID else config.LEAD.id
        where = f"daily check mail {aid}"
        expected = (f"The morning check lists the newest INBOX envelopes of mailbox '{aid}' with mail.envelopes, and "
                    f"the newest is under {MAIL_QUIET // 86400} days old.")
        try:
            newest = max((e["date"] for e in mail.envelopes(aid, "INBOX", page_size=5)), default=None)
        except Exception as e:
            out[f"mail:{aid}"] = _result(f"Mail ({aid})", "failed", "I couldn't read this mailbox.", about, where,
                                         expected, f"{type(e).__name__}: {_scrub(e)}")
            continue
        if newest is None or now - newest > MAIL_QUIET:
            age = "no messages" if newest is None else f"the newest is {int((now - newest) // 86400)} days old"
            out[f"mail:{aid}"] = _result(f"Mail ({aid})", "failed", "No new mail has arrived in days.", about, where,
                                         expected, f"The INBOX listing worked, but {age}.")
        else:
            out[f"mail:{aid}"] = _result(f"Mail ({aid})", "ok", "Reads, and new mail is arriving.", about)
    return out


def check_calendar(now: float) -> dict[str, dict]:
    from . import calsources
    if not home.calendars():
        return {"calendar": _result("Calendar", "skipped", "No calendar is connected.")}
    expected = "The morning check reads the next day of every connected calendar with calsources.events."
    try:
        events, failed = calsources.events(now, now + DAY)
    except Exception as e:
        return {"calendar": _result("Calendar", "failed", "I couldn't read the calendar.", where="daily check calendar",
                                    expected=expected, evidence=f"{type(e).__name__}: {_scrub(e)}")}
    if failed:
        return {"calendar": _result("Calendar", "failed", "I couldn't read a calendar.", where="daily check calendar",
                                    expected=expected, evidence="; ".join(_scrub(f, 120) for f in failed)[:500])}
    return {"calendar": _result("Calendar", "ok", f"Reads ({len(events)} events in the next day).")}




MAC_KEYS = ("mac", "texts")


async def check_mac() -> dict[str, dict]:
    from . import imessage, mac
    if not mac.online():
        return {k: _result(n, "asleep", "The Mac is asleep, so this waits until it wakes.")
                for k, n in (("mac", "Mac"), ("texts", "Texts"))}
    out = {}
    try:
        reply = await mac._call("in_use")
        ok = isinstance(reply.get("result"), bool)
        out["mac"] = _result("Mac", "ok", "Answers.") if ok else _result(
            "Mac", "failed", "The Mac gave a reply I couldn't use.", where="daily check mac bridge",
            expected="The Mac bridge answers the in_use op with true or false.", evidence="The reply had no yes or no.")
    except mac.MacError as e:
        out["mac"] = _result("Mac", "failed", "The Mac is connected but didn't answer.", where="daily check mac bridge",
                             expected="The Mac bridge answers the in_use op within its timeout.",
                             evidence=f"{type(e).__name__}: {_scrub(e)}")
    try:
        found = await imessage.read(config.LEAD.id, {"action": "recent", "limit": 1})
        n = len(found.get("conversations") or found.get("messages") or []) if isinstance(found, dict) else 0
        out["texts"] = _result("Texts", "ok", "Reads." if n else "Reads (nothing recent).")
        del found
    except mac.MacError as e:
        if "Read My Texts" in str(e):
            out["texts"] = _result("Texts", "off", "Reading texts is turned off on the Mac.")
        else:
            out["texts"] = _result("Texts", "failed", "I couldn't read texts on the Mac.", where="daily check texts",
                                   expected="imessage.read with action recent and limit 1 returns through the Mac.",
                                   evidence=f"{type(e).__name__}: {_scrub(e)}")
    return out




def _jobs() -> dict[str, dict[str, dict]]:
    ""
    from . import watcher
    out = {}
    for bot_id, folder in watcher._cron_dirs():
        if bot_id in SKIP_ZIPS or config.is_sandbox(bot_id) or bot_id not in config.BOTS_BY_ID:
            continue
        out[bot_id] = watcher._jobs_of(folder.parent / "jobs.json")
    return out


def check_brief(now: float, jobs: dict[str, dict[str, dict]]) -> dict[str, dict]:
    where, expected = "daily check morning brief", ("The lead's scheduled job named for the brief is enabled, its "
                                                    "next run is ahead, and its last run, under 30 hours ago, was ok.")
    briefs = [(jid, j) for jid, j in jobs.get(config.LEAD.id, {}).items()
              if "brief" in str(j.get("name") or "").lower() and j.get("enabled", True)]
    if not briefs:
        return {"brief": _result("Morning brief", "failed", "The morning brief isn't scheduled.", where=where,
                                 expected=expected, evidence="No enabled job named for the brief in the lead's jobs.")}
    jid, j = briefs[0]
    last, upcoming = _iso(j.get("last_run_at")), _iso(j.get("next_run_at"))
    wrong = []
    if last is None or now - last > BRIEF_STALE:
        wrong.append("its last run is over 30 hours old" if last else "it has never run")
    elif str(j.get("last_status") or "") != "ok":
        kind = str(j.get("last_error") or "").split(":")[0]
        wrong.append(f"its last run ended {_scrub(j.get('last_status'), 40)}: {_scrub(kind, 60)}")
    if upcoming is None or upcoming < now - LATE:
        wrong.append("its next run is missing or past")
    if wrong:
        return {"brief": _result("Morning brief", "failed", "The morning brief didn't run as planned.", where=where,
                                 expected=expected, evidence=f"Job {jid}: " + "; ".join(wrong) + ".")}
    return {"brief": _result("Morning brief", "ok", "Scheduled, and the last one ran.")}


def check_jobs(now: float, jobs: dict[str, dict[str, dict]]) -> dict[str, dict]:
    from . import watcher
    out = {}
    for bot_id, mine in jobs.items():
        name = config.BOTS_BY_ID[bot_id].name
        live = {jid: j for jid, j in mine.items() if j.get("enabled", True) and not watcher._finished(j)
                and str(j.get("state") or "") != "paused"}
        if not live:
            continue
        missed, failed = [], []
        for jid, j in live.items():
            upcoming, last = _iso(j.get("next_run_at")), _iso(j.get("last_run_at"))
            if upcoming is not None and upcoming < now - LATE:
                missed.append(f"{jid} (due {datetime.fromtimestamp(upcoming, home.TZ):%b %-d %-I:%M %p})")
            if last is not None and now - last < DAY and str(j.get("last_status") or "ok") != "ok":
                kind = str(j.get("last_error") or "").split(":")[0]
                failed.append(f"{jid} ({_scrub(j.get('last_status'), 40)}: {_scrub(kind, 60)})")
        key = f"jobs:{bot_id}"
        if not (missed or failed):
            out[key] = _result(f"{name}'s scheduled jobs", "ok", f"All {len(live)} ran on time.", bot_id)
            continue
        bits = ([f"missed their time: {', '.join(missed)}"] if missed else []) + \
               ([f"failed in the last day: {', '.join(failed)}"] if failed else [])
        out[key] = _result(f"{name}'s scheduled jobs", "failed",
                           f"{len(missed) + len(failed)} of {len(live)} didn't run as planned.", bot_id,
                           f"daily check scheduled jobs {bot_id}",
                           f"Every enabled job in Hermes's jobs.json for '{bot_id}' runs within an hour of its "
                           "next_run_at, and its runs in the last day end with last_status ok.",
                           f"Jobs (Hermes ids) that {'; '.join(bits)}."[:600])
    return out


def check_board(now: float) -> dict[str, dict]:
    ""
    from . import board
    if not config.KANBAN_DB.exists():
        return {"board": _result("Board", "skipped", "There is no board yet.")}
    marks = ",".join("?" * len(board.ACTIVE))
    try:
        with board._conn() as c:
            rows = c.execute(
                f"SELECT t.id, t.assignee, t.status, MAX(COALESCE(t.started_at, 0), t.created_at, "
                f"COALESCE((SELECT MAX(e.created_at) FROM task_events e WHERE e.task_id = t.id), 0)) AS moved "
                f"FROM tasks t WHERE t.status IN ({marks})", board.ACTIVE).fetchall()
    except sqlite3.Error as e:
        return {"board": _result("Board", "failed", "I couldn't read the board.", where="daily check board",
                                 expected="The board's kanban.db opens read-only.", evidence=_scrub(e))}
    stuck = [r for r in rows if r["moved"] and now - float(r["moved"]) > DAY and r["assignee"] not in SKIP_ZIPS]
    if not stuck:
        return {"board": _result("Board", "ok", "Nothing is stuck.")}
    moved = "task hasn't" if len(stuck) == 1 else "tasks haven't"
    return {"board": _result("Board", "failed", f"{len(stuck)} {moved} moved in over a day.",
                             where="daily check board",
                             expected="A board task that is ready, todo or running moves (an event, a start or a "
                                      "finish) at least once a day.",
                             evidence="Stuck tasks (id, Zip, status, hours since it moved): " + "; ".join(
                                 f"{r['id']}, {r['assignee'] or 'default'}, {r['status']}, "
                                 f"{int((now - float(r['moved'])) // 3600)}h" for r in stuck[:10]))}




KIND_WORDS = {"instructions": "instruction file", "notebook": "notebook", "profile": "profile of the person"}


def check_rules(now: float) -> dict[str, dict]:
    ""

    from . import ownerrules, rulereach, standing
    out = {}
    where = "daily check standing rules"
    expected = ("A new conversation of every Zip carries every saved rule that applies to everything, and at least one "
                "preference whenever preferences are saved (standing.health).")
    for bot in config.BOTS:
        if bot.id in SKIP_ZIPS or config.is_sandbox(bot.id):
            continue
        try:
            c = standing.health(bot.id)
        except Exception as e:
            out[f"rules:{bot.id}"] = _result(f"{bot.name}'s saved rules", "failed", "I couldn't build its saved rules.",
                                             bot.id, where, expected, f"{type(e).__name__}: {_scrub(e)}")
            continue
        cut = c["must"] - c["must_shown"]
        if c["preference"] and not c["preference_shown"]:
            detail = f"{bot.name} starts new conversations with none of the {c['preference']} saved preferences."
        elif cut:
            detail = f"{bot.name} starts new conversations missing {cut} of the {c['must']} rules that always apply."
        else:
            continue
        out[f"rules:{bot.id}"] = _result(f"{bot.name}'s saved rules", "failed", detail, bot.id, where, expected,
                                         f"Shown: {c['must_shown']} of {c['must']} always-on rules, "
                                         f"{c['preference_shown']} of {c['preference']} preferences, "
                                         f"{c['decision_shown']} of {c['decision']} decisions.")
    waiting = rulereach.late()
    if waiting:
        out["rules:reader"] = _result("Saved rules", "failed", f"{waiting} saved rules have waited over a day to be sorted.",
                                      where=where, expected="The helper model sorts each saved rule within a day "
                                      "(rulereach.run).", evidence=f"{waiting} rules have no answer on file.")
    unfiled = ownerrules.late()
    if unfiled:
        out["rules:decisions"] = _result("Saved rules", "failed", f"{unfiled} saved rules have waited over a day to be "
                                         "filed under the decisions they bear on.", where=where,
                                         expected="The helper model files each saved rule within a day (ownerrules.run).",
                                         evidence=f"{unfiled} rules have no answer on file.")
    return out or {"rules": _result("Saved rules", "ok", "Every Zip starts with the rules that always apply.")}


def check_limits(now: float) -> dict[str, dict]:
    ""
    from . import ziplimits
    out = {}
    expected = ("Each Zip's notebook, profile and instruction file stays under 95 percent of the limit in its "
                "config.yaml. Past the limit a notebook refuses additions and the engine cuts an instruction file.")
    for bot in config.BOTS:
        if bot.id in SKIP_ZIPS or config.is_sandbox(bot.id):
            continue
        for f in ziplimits.measure(bot.id):
            state = ziplimits.state(f["size"], f["limit"])
            if state == "ok":
                continue
            word = KIND_WORDS[f["kind"]]
            detail = (f"{bot.name}'s {word} is over its limit, at {f['size']:,} of {f['limit']:,} characters."
                      if state == "over" else
                      f"{bot.name}'s {word} is nearly full, at {f['size']:,} of {f['limit']:,} characters.")
            out[f"limits:{bot.id}:{f['kind']}"] = _result(
                f"{bot.name}'s {word}", "failed", detail, bot.id, f"daily check file limits {bot.id} {f['kind']}", expected,
                f"{f['size']} characters against a limit of {f['limit']}.")
    return out or {"limits": _result("Notebooks and instructions", "ok", "All are under their limits.")}


def check_notebooks(now: float) -> dict[str, dict]:
    ""

    from . import notebooks
    out = {}
    expected = (f"A notebook or profile past {int(notebooks.ROOM * 100)} percent of its limit is tidied within a day: "
                "entries merge or shorten, and lasting rules, passed items and spare how-tos move to saved facts "
                "(notebooks.tidy).")
    for row in notebooks.stuck(now):
        bot_id, _, kind = row["file"].partition(":")
        bot = config.BOTS_BY_ID.get(bot_id)
        if bot is None or config.is_sandbox(bot_id):
            continue
        word = KIND_WORDS[kind]
        days = int((now - row["over_since"]) // 86400)
        out[f"notebooks:{row['file']}"] = _result(
            f"{bot.name}'s {word}", "failed",
            f"{bot.name}'s {word} has stayed nearly full for {days} day{'s' if days != 1 else ''}, so it is not being "
            "tidied.", bot_id, f"daily check notebook tidy {bot_id} {kind}", expected,
            f"Last try: {row['outcome']}, {row['before']} to {row['after']} characters.")
    return out or {"notebooks": _result("Notebook tidying", "ok", "Every notebook has room to learn.")}




async def check_all(now: float | None = None) -> dict[str, dict]:
    now = time.time() if now is None else now
    jobs = await asyncio.to_thread(_jobs)
    results: dict[str, dict] = {}
    for part in (await asyncio.to_thread(check_mail, now), await asyncio.to_thread(check_calendar, now),
                 await check_mac(),
                 {"send_card": _result("Send card", "skipped", SEND_CARD_SKIPPED)},
                 check_brief(now, jobs), check_jobs(now, jobs), await asyncio.to_thread(check_board, now),
                 await asyncio.to_thread(check_rules, now), await asyncio.to_thread(check_limits, now),
                 await asyncio.to_thread(check_notebooks, now)):
        results.update(part)
    return results


def _closed_as_not_a_bug(place: str, now: float) -> bool:
    from . import store
    return bool(store.db().execute(
        "SELECT 1 FROM problem_reports WHERE place=? AND state='not_a_bug' AND updated_at>? LIMIT 1",
        (place, now - NOT_A_BUG_QUIET)).fetchone())


def file_failures(results: dict[str, dict], now: float | None = None) -> None:
    ""

    from . import problems
    now = time.time() if now is None else now
    for key, r in results.items():
        if r["state"] != "failed" or r.get("report_id"):
            continue
        place = " ".join(r["where"].lower().split())
        if _closed_as_not_a_bug(place, now):
            r["detail"] += " This was looked at and left as it is."
            continue
        args = {"kind": "break", "concerns_zip": r["about"] if r["about"] in config.BOTS_BY_ID else "self",
                "what_happened": f"The morning self-check failed: {r['name']}. {r['detail']}",
                "evidence": r["evidence"] or r["detail"], "where": r["where"], "expected": r["expected"],
                "source": "code"}
        try:
            r["report_id"] = problems.report(config.LEAD.id, args)["id"]
        except problems.ProblemError:
            try:
                r["report_id"] = problems.report(config.LEAD.id, {**args, "evidence": "Details left out: the error "
                                                                  "looked like it held a secret or a number."})["id"]
            except problems.ProblemError:
                log.warning("could not file the daily check report for %s", key)


def _public(r: dict) -> dict:
    return {k: r[k] for k in ("name", "state", "detail", "at") if k in r}


def summary() -> dict:
    ""
    from . import store
    saved = store.get_setting(SETTING, {}) or {}
    return {"day": saved.get("day"), "at": saved.get("at"),
            "checks": [{"key": k, **_public(r)} for k, r in (saved.get("checks") or {}).items()]}


async def tick(now: float | None = None) -> None:
    ""
    from . import mac, store
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, home.TZ)
    saved = store.get_setting(SETTING, {}) or {}
    today = local.date().isoformat()
    checks = saved.get("checks") or {}
    if saved.get("day") != today and local.hour >= CHECK_HOUR:
        results = await check_all(now)



        try:
            from . import zipcheck
            results.update(await zipcheck.morning(now))
        except Exception:
            log.warning("the Zips' computer check failed", exc_info=True)
        await asyncio.to_thread(file_failures, results, now)
        store.set_setting(SETTING, {"day": today, "at": now, "checks": results})

        from . import nightly, problems
        nightly.note_checks(results, now)
        nightly.note_selftest()
        await problems.rank_open()
        bad = [k for k, r in results.items() if r["state"] == "failed"]
        log.info("daily checks: %d run, failed: %s", len(results), ", ".join(bad) or "none")
    elif saved.get("day") == today and mac.online() and any(
            checks.get(k, {}).get("state") == "asleep" for k in MAC_KEYS):
        results = await check_mac()
        await asyncio.to_thread(file_failures, results, now)
        store.set_setting(SETTING, {**saved, "checks": {**checks, **results}})


async def run() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await tick()
        except Exception:
            log.warning("daily checks failed", exc_info=True)
        await asyncio.sleep(TICK_SEC)


def main() -> int:
    ""
    results = asyncio.run(check_all())
    for key, r in results.items():
        print(f"{r['state']:8} {key:24} {r['detail']}" + (f"\n{'':33} {r['evidence']}" if r["evidence"] else ""))
    return 1 if any(r["state"] == "failed" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
