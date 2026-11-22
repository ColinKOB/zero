""
























import hashlib
import json
import logging
import re
import sqlite3
import time
import zlib

from . import config, consent, facts, shortreply, store, who

log = logging.getLogger("taffy.problems")

KINDS = ("break", "polish", "security_money", "crash")
STATES = ("new", "diagnosing", "fixing", "pr_open", "fixed_live", "needs_colin", "not_a_bug", "duplicate")
SOURCES = ("code", "zero", "colin", "tool", "outside")
PLACE_DAY = 24 * 3600
FIXED_TITLE = "Fixed"
CLOSED = ("fixed_live", "not_a_bug", "duplicate")
REPEAT_RUNS = 2
SEEN_MAX = 20
WORKED_AFTER = 3600
TEST_ZIP = re.compile(r"trace\d{8,10}")
_HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.I)

_PLAIN_LABELS = {"www", "my", "app", "apps", "login", "secure", "account", "accounts", "auth", "sso", "identity",
                 "mail", "portal", "online", "web", "home"}
PAUSED_TITLE = "Stopped checking {site}"
PAUSED_LINE = ("It failed the same way on two scheduled checks in a row. I've flagged it for a fix and will try again "
               "after the next update. You don't need to do anything.")

PAUSED_DAILY_LINE = ("It failed again after a day's pause. I've flagged it for a fix and will try once a day until it "
                     "works. You don't need to do anything.")
MAX_PAYLOAD = 2 * 1024 * 1024
_CREDENTIAL = re.compile(r"\b(?:Bearer|Basic)\s+\S+|\b(?:sk-(?:proj-|ant-)?|gh[pousr]_)[A-Za-z0-9_-]{10,}", re.I)
DIAGNOSTIC_KINDS = {"crashDiagnostics": "crash", "hangDiagnostics": "break", "cpuExceptionDiagnostics": "polish",
                    "diskWriteExceptionDiagnostics": "polish", "appLaunchDiagnostics": "polish"}
DIAGNOSTIC_SUMMARIES = {"crashDiagnostics": "crashed", "hangDiagnostics": "stopped responding",
                        "cpuExceptionDiagnostics": "exceeded its CPU limit",
                        "diskWriteExceptionDiagnostics": "exceeded its disk write limit",
                        "appLaunchDiagnostics": "took too long to open"}


class ProblemError(ValueError):
    ""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS problem_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            reporter_bot TEXT NOT NULL,
            about_bot TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('break', 'polish', 'security_money', 'crash')),
            summary TEXT NOT NULL,
            evidence TEXT NOT NULL,
            signature TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new'
                CHECK(state IN ('new', 'diagnosing', 'fixing', 'pr_open', 'fixed_live', 'needs_colin', 'not_a_bug', 'duplicate')),
            doctor_note TEXT NOT NULL DEFAULT '',
            pr_url TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS problem_reports_open ON problem_reports(signature)
            WHERE state NOT IN ('fixed_live', 'not_a_bug', 'duplicate');
        CREATE INDEX IF NOT EXISTS problem_reports_state ON problem_reports(state, id);
        CREATE TABLE IF NOT EXISTS app_diagnostics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signature TEXT UNIQUE NOT NULL,
            platform TEXT NOT NULL,
            payload BLOB NOT NULL,
            report_ids TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL
        );
    """)

    cols = {r[1] for r in c.execute("PRAGMA table_info(problem_reports)")}

    for col in ("site TEXT NOT NULL DEFAULT ''", "seen TEXT NOT NULL DEFAULT '[]'", "place TEXT NOT NULL DEFAULT ''",
                "expected TEXT NOT NULL DEFAULT ''", "session_id TEXT NOT NULL DEFAULT ''",
                "source TEXT NOT NULL DEFAULT 'code'", "his_words TEXT NOT NULL DEFAULT ''",
                "tool_error TEXT NOT NULL DEFAULT ''", "importance INTEGER NOT NULL DEFAULT 0"):
        if col.split()[0] not in cols:
            c.execute(f"ALTER TABLE problem_reports ADD COLUMN {col}")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value, field: str, limit: int, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit:
        raise ProblemError(f"Give {field} as text, at most {limit} characters.")
    if facts.secret(value) or _CREDENTIAL.search(value):
        raise ProblemError("Remove secrets and account numbers, then report only what failed.")
    return value.strip()


def _seen(row) -> list:
    try:
        seen = json.loads(row["seen"] or "[]")
    except (ValueError, TypeError):
        return []
    return [s for s in seen if isinstance(s, list) and len(s) == 2] if isinstance(seen, list) else []


def _site_signature(about: str, site: str) -> str:
    return _hash([about, "break", "site", site])


def _record(c, reporter: str, about: str, kind: str, summary: str, evidence: str, signature: str,
            site: str = "", run: str = "", extra: dict | None = None) -> dict:
    now = time.time()
    extra = extra or {}
    row = c.execute("SELECT * FROM problem_reports WHERE signature=? AND state NOT IN "
                    "('fixed_live', 'not_a_bug', 'duplicate')", (signature,)).fetchone()
    if row is None and extra.get("place") and extra.get("source") != "outside":

        since = 0 if extra.get("source") == "code" else now - PLACE_DAY
        row = c.execute("SELECT * FROM problem_reports WHERE about_bot=? AND place=? AND source!='outside' AND "
                        "created_at>? AND state NOT IN ('fixed_live', 'not_a_bug', 'duplicate') ORDER BY id LIMIT 1",
                        (about, extra["place"], since)).fetchone()
    if row:

        if evidence not in row["evidence"]:
            evidence = row["evidence"] + "\n\n" + evidence
        else:
            evidence = row["evidence"]
        seen = (_seen(row) + [[now, run]])[-SEEN_MAX:]
        c.execute("UPDATE problem_reports SET evidence=?, seen=?, updated_at=? WHERE id=?",
                  (evidence, _json(seen), now, row["id"]))
        return {"id": row["id"], "duplicate": True}
    cols = ("place", "expected", "session_id", "source", "his_words", "tool_error")
    report_id = c.execute(
        "INSERT INTO problem_reports (created_at, reporter_bot, about_bot, kind, summary, evidence, signature, "
        "updated_at, site, seen, " + ", ".join(cols) + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now, reporter, about, kind, summary, evidence, signature, now, site, _json([[now, run]]),
         *(extra.get(k, "code" if k == "source" else "") for k in cols))).lastrowid
    return {"id": report_id, "duplicate": False}


def _sites(bot_id: str, saved: list | None = None) -> set[str]:
    ""

    from . import logins
    saved = logins.list_all() if saved is None else saved
    hosts = {consent.site_of(str(r.get("origin") or "")) for r in saved if r.get("bot") == bot_id}
    return {s for s in hosts | consent.signed_in_sites(bot_id) if s}


def site_named(sites: set[str], *texts: str) -> str:
    ""


    companies = {consent.base(s) for s in sites}
    labels = {s.split(".")[0]: s for s in sites
              if len(s.split(".")[0]) >= 4 and s.split(".")[0] not in _PLAIN_LABELS}
    for text in texts:
        found = [(m.start(), consent.site_of(m.group(0))) for m in _HOST.finditer(text or "")
                 if consent.base(consent.site_of(m.group(0))) in companies]
        for label, host in labels.items():
            m = re.search(rf"\b{re.escape(label)}\b", text or "", re.I)
            if m:
                found.append((m.start(), host))
        if found:
            return min(found)[1]
    return ""


def _run(bot_id: str, session_id: str) -> str:
    ""

    from . import lineage, team
    if not session_id:
        return ""
    if team.in_team_session(session_id):
        q = team.answering(bot_id, time.time())
        row = store.db().execute("SELECT from_bot, session_id FROM team_msgs WHERE id=?", (q,)).fetchone() if q else None
        if not row:
            return ""
        bot_id, session_id = row["from_bot"], row["session_id"] or ""
    run = lineage.root(bot_id, session_id) or session_id
    return run if run.startswith("cron_") else ""


def _repeat(c, report_id: int) -> bool:
    ""




    r = c.execute("SELECT * FROM problem_reports WHERE id=?", (report_id,)).fetchone()
    runs = len({run for _, run in _seen(r) if run})
    if not r["site"] or runs < REPEAT_RUNS:
        return False
    from . import readokays
    daily = readokays.hard_stops()
    if consent.paused_site(r["about_bot"], r["site"]):
        if not daily or runs < REPEAT_RUNS + 1:
            return False
    else:
        consent.pause_site(r["about_bot"], r["site"], report_id)
        if daily:
            return False
    if r["state"] != "new":
        return False
    c.execute("UPDATE problem_reports SET state='diagnosing', doctor_note=?, updated_at=? WHERE id=?",
              (f"Failed the same way on {runs} scheduled runs, so unattended work skips {r['site']} until a "
               "deploy lands or it works again" + (", with one try a day." if daily else "."), time.time(), report_id))
    return True


async def tell(report_id: int) -> None:
    ""

    r = store.db().execute("SELECT about_bot, site FROM problem_reports WHERE id=?", (report_id,)).fetchone()
    if not r:
        return
    title = PAUSED_TITLE.format(site=r["site"])
    from . import notices, readokays
    line = PAUSED_DAILY_LINE if readokays.hard_stops() else PAUSED_LINE
    store.add_feed("checkin", r["about_bot"], None, title, line, written_by="hub", written_for="person")
    about = config.BOTS_BY_ID.get(r["about_bot"])
    await notices.nudge(f"problem:{report_id}:paused", title, line, r["about_bot"], kind="progress",
                        held=f"{about.name} stopped checking {r['site']}" if about else None)


def report(reporter: str, args: dict, session_id: str = "") -> dict:
    kind = args.get("kind")
    if kind not in KINDS[:3]:
        raise ProblemError("Choose break, polish or security_money.")
    summary = _text(args.get("what_happened"), "what_happened", 600)
    evidence = _text(args.get("evidence"), "evidence", 600)
    about = _text(args.get("concerns_zip"), "concerns_zip", 80)
    about = reporter if about == "self" else about
    if about not in config.BOTS_BY_ID:
        raise ProblemError("concerns_zip must be self or a Zip id from the team roster.")


    if config.is_sandbox(reporter) or config.is_sandbox(about):
        return {"id": 0, "duplicate": False}
    extra = _repair_fields(args, session_id)
    site = site_named(_sites(about), summary, evidence) if kind == "break" else ""
    signature = _site_signature(about, site) if site else _hash([about, kind, " ".join(summary.lower().split())])
    if extra["source"] == "outside":
        signature = _hash(["outside", signature])
    run = _run(reporter, session_id)
    with store._lock:
        c = store.db()
        result = _record(c, reporter, about, kind, summary, f"{reporter}: {evidence}", signature, site, run, extra)
        told = _repeat(c, result["id"])
        c.commit()
    if run and site and consent.paused_site(about, site):
        from . import readokays
        result["next"] = ((f"{site} has failed on more than one scheduled check, so unattended work tries it again "
                           "tomorrow." if readokays.hard_stops() else
                           f"{site} has now failed on {REPEAT_RUNS} scheduled checks, so it is paused for unattended "
                           f"work and {who.first()} has been told.")
                          + " Don't try it again now. Say in one clause that you couldn't check it.")
    else:


        result["next"] = (f"If {who.first()} asked for this, say in one line that it's logged and that you'll say "
                          "here when the fix is live. Don't guess when.")
    if told:
        result["told"] = True
    return result


def _quote(value, limit: int) -> str:
    ""

    text = " ".join(str(value or "").split())[:limit]
    return "" if facts.secret(text) or _CREDENTIAL.search(text) else text


def _repair_fields(args: dict, session_id: str) -> dict:
    ""
    place = _text(args.get("where") or "", "where", 200, empty=True)
    source = args.get("source") or "code"
    if source not in SOURCES:
        raise ProblemError("Unknown report source.")
    return {"place": " ".join(place.lower().split()), "expected": _text(args.get("expected") or "", "expected", 400,
                                                                         empty=True),
            "session_id": str(session_id or "")[:200], "source": source,
            "his_words": _quote(args.get("his_words"), 600), "tool_error": _quote(args.get("tool_error"), 600)}


def list_reports(state: str | None = None, after: int = 0, limit: int = 100) -> list[dict]:
    if state is not None and state not in STATES:
        raise ProblemError("Unknown repair state.")
    sql, params = "SELECT * FROM problem_reports WHERE id>?", [after]
    if state:
        sql += " AND state=?"
        params.append(state)
    return [dict(r) for r in store.db().execute(sql + " ORDER BY id LIMIT ?", (*params, limit))]


def update(report_id: int, changes: dict) -> dict:
    ""


    if not changes or changes.keys() - {"state", "doctor_note", "pr_url", "line"}:
        raise ProblemError("Update state, doctor_note, pr_url or line only.")
    values = dict(changes)
    line = values.pop("line", None)
    if line is not None:
        line = _text(line, "line", 200)
        if values.get("state") != "fixed_live":
            raise ProblemError("Give line only with state fixed_live.")
    if "state" in values and values["state"] not in STATES:
        raise ProblemError("Unknown repair state.")
    if "doctor_note" in values:
        values["doctor_note"] = _text(values["doctor_note"], "doctor_note", 4000, empty=True)
    if "pr_url" in values:
        url = _text(values["pr_url"], "pr_url", 300, empty=True)
        if url and not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/pull/[1-9]\d*", url):
            raise ProblemError("Give a GitHub pull request URL without query parameters.")
        values["pr_url"] = url
    with store._lock:
        c = store.db()
        before = c.execute("SELECT state FROM problem_reports WHERE id=?", (report_id,)).fetchone()
        try:
            cur = c.execute("UPDATE problem_reports SET " + ", ".join(f"{k}=?" for k in values) +
                            ", updated_at=? WHERE id=?", (*values.values(), time.time(), report_id))
        except sqlite3.IntegrityError:
            c.rollback()
            raise ProblemError("Another open report already has this signature.") from None
        if not cur.rowcount:
            raise ProblemError("Unknown problem report.")
        c.commit()
        row = dict(c.execute("SELECT * FROM problem_reports WHERE id=?", (report_id,)).fetchone())
    if line and before["state"] not in CLOSED:
        row["told"] = line
    return row


async def tell_fixed(line: str) -> None:
    ""


    store.add_feed("checkin", "default", None, FIXED_TITLE, f"{FIXED_TITLE}: {line}", written_by="hub",
                   written_for="person")
    from . import notices
    await notices.nudge(f"problem:fixed:{_hash(line)[:12]}", FIXED_TITLE, line, "default", kind="progress", held=line)


def _move(c, report_id: int, state: str, note: str) -> None:
    c.execute("UPDATE problem_reports SET state=?, doctor_note=?, updated_at=? WHERE id=?",
              (state, note, time.time(), report_id))


def _test_zip(bot_id: str) -> bool:
    return config.is_sandbox(bot_id) or bool(TEST_ZIP.fullmatch(bot_id or ""))


def _worked_again(r, now: float) -> bool:
    ""
    used = consent.last_opened(r["about_bot"], r["site"])
    last = max([r["created_at"], *(float(at) for at, _ in _seen(r))])
    return bool(used) and used > last + WORKED_AFTER and now > used + WORKED_AFTER


def triage(now: float | None = None) -> dict:
    ""








    from . import deployed, logins
    now = time.time() if now is None else now
    moved = {"not_a_bug": 0, "duplicate": 0, "fixed_live": 0, "resumed": 0}
    sites: dict[str, set[str]] = {}
    saved = None
    with store._lock:
        c = store.db()
        for r in c.execute("SELECT * FROM problem_reports WHERE state NOT IN (?, ?, ?) ORDER BY id", CLOSED).fetchall():
            if _test_zip(r["reporter_bot"]) or _test_zip(r["about_bot"]):
                _move(c, r["id"], "not_a_bug", "Filed by a test Zip during a test run.")
                moved["not_a_bug"] += 1
                continue
            if r["site"] or r["kind"] != "break" or r["source"] == "outside":
                continue
            if r["about_bot"] not in sites:
                saved = logins.list_all() if saved is None else saved
                sites[r["about_bot"]] = _sites(r["about_bot"], saved)
            site = site_named(sites[r["about_bot"]], r["summary"], r["evidence"])
            if not site:
                continue
            signature = _site_signature(r["about_bot"], site)
            first = c.execute("SELECT * FROM problem_reports WHERE signature=? AND state NOT IN (?, ?, ?)",
                              (signature, *CLOSED)).fetchone()
            if first is None:
                c.execute("UPDATE problem_reports SET site=?, signature=? WHERE id=?", (site, signature, r["id"]))
                continue
            evidence = first["evidence"] if r["evidence"] in first["evidence"] else \
                first["evidence"] + "\n\n" + r["evidence"]
            seen = (_seen(first) + (_seen(r) or [[r["created_at"], ""]]))[-SEEN_MAX:]
            c.execute("UPDATE problem_reports SET evidence=?, seen=?, updated_at=? WHERE id=?",
                      (evidence, _json(seen), now, first["id"]))
            _move(c, r["id"], "duplicate", f"Same problem as report {first['id']}.")
            moved["duplicate"] += 1
        for r in c.execute("SELECT * FROM problem_reports WHERE site!='' AND state NOT IN (?, ?, ?)", CLOSED).fetchall():
            if _worked_again(r, now):
                _move(c, r["id"], "fixed_live", f"{r['site']} worked again on a later run, with no new failure.")
                moved["fixed_live"] += 1
        stamp = float(deployed.read().get("at") or 0)
        for p in consent.paused_sites():
            still = c.execute("SELECT id FROM problem_reports WHERE id=? AND state NOT IN (?, ?, ?)",
                              (p["report_id"], *CLOSED)).fetchone()
            if still is None or p["at"] < stamp:
                consent.unpause_site(p["bot_id"], p["site"])
                moved["resumed"] += 1
        c.commit()
    return moved




_NOISE = re.compile(r"\b(?:t_[0-9a-z]+|[0-9a-f]{8,}|\d[\d:.,-]*)\b", re.I)
RANK_MAX = 40
SKIP_RANK = config.PRIVATE_ZIPS


def failure_key(r) -> str:
    ""
    what = str(r["place"] or "") or " ".join(_NOISE.sub("#", str(r["summary"] or "").lower()).split())
    return f"{r['about_bot']}|{r['kind']}|{what}"


def open_groups(rows=None) -> list[list[dict]]:
    ""
    rows = rows if rows is not None else store.db().execute(
        "SELECT * FROM problem_reports WHERE state NOT IN ('fixed_live', 'not_a_bug', 'duplicate') ORDER BY id").fetchall()
    groups: dict[str, list[dict]] = {}
    for r in map(dict, rows):
        groups.setdefault(f"outside|{r['id']}" if r["source"] == "outside" else failure_key(r), []).append(r)
    return list(groups.values())


SAME = """These are open failure reports from {first}'s assistant app. Some describe one failure in different words.

{items}

Group the numbers that are the same failure, meaning one fix would clear them all. Leave out a number that stands alone. \
When you are not sure, leave it out.

Answer with this JSON and nothing else: {{"same": [[1, 4], [2, 7, 9]]}}"""
SAME_BATCH = 60


async def merged_groups(ask) -> tuple[list[list[dict]], bool]:
    ""

    groups, whole = open_groups(), True
    by_bot: dict[str, list[list[dict]]] = {}
    for g in groups:
        if g[0]["source"] != "outside" and g[0]["about_bot"] not in SKIP_RANK:
            by_bot.setdefault(g[0]["about_bot"], []).append(g)
    joined: dict[int, list[dict]] = {}
    for mine in by_bot.values():
        for start in range(0, len(mine), SAME_BATCH):
            batch = mine[start:start + SAME_BATCH]
            items = "\n".join(f"{i}. {consent.scrub(g[0]['summary'])[:240]}" for i, g in enumerate(batch, 1))
            try:
                with shortreply.reading(tuple({g[0]['about_bot'] for g in batch})):
                    found = re.search(r"\{.*\}", await ask(SAME.format(first=who.first(), items=items), 90) or "", re.S)
                same = json.loads(found.group(0)).get("same") if found else None
            except Exception as e:
                log.info("no answer on which reports are the same failure (%s)", type(e).__name__)
                same = None
            whole = whole and isinstance(same, list)
            used: set[int] = set()
            for numbers in same if isinstance(same, list) else []:
                picked = [n for n in numbers if isinstance(n, int) and 1 <= n <= len(batch) and n not in used] \
                    if isinstance(numbers, list) else []
                used.update(picked)
                for n in picked[1:]:
                    joined[batch[n - 1][0]["id"]] = batch[picked[0] - 1]
    out = []
    for g in groups:
        target = joined.get(g[0]["id"])
        if target is None:
            out.append(g)
        else:
            target.extend(g)
    return [sorted(g, key=lambda r: r["id"]) for g in out], whole


def close_duplicates(apply: bool = False, groups: list[list[dict]] | None = None) -> dict:
    ""

    groups = open_groups() if groups is None else groups
    closed = 0
    with store._lock:
        c = store.db()
        for group in groups:
            keep = next((r for r in group if r["state"] != "new"), group[0])
            copies = [r for r in group if r["id"] != keep["id"] and r["state"] == "new"]
            closed += len(copies)
            if not apply or not copies:
                continue
            seen = [*_seen(keep)]
            for r in copies:
                seen += _seen(r) or [[r["created_at"], ""]]
                _move(c, r["id"], "duplicate", f"Same problem as report {keep['id']}.")
            c.execute("UPDATE problem_reports SET seen=?, updated_at=? WHERE id=?",
                      (_json(sorted(seen, key=lambda s: s[0])[-SEEN_MAX:]), time.time(), keep["id"]))
        if apply:
            c.commit()
    return {"open": sum(len(g) for g in groups), "failures": len(groups), "closed": closed}


RANK = """These are the open failures in {first}'s assistant app. Rate how much each one matters to {first}.

{items}

3: {first} loses something or is misled: work that silently does not happen, a wrong result, a broken feature used daily.
2: A real fault that has a way around it or touches something used now and then.
1: Cosmetic, one-off, or already handled.

Answer with this JSON and nothing else: {{"ratings": [{{"id": 1, "importance": 3}}]}}"""


async def rank_open(ask=None) -> int:
    ""
    if ask is None:
        from . import nightly
        if not nightly.ENABLED:
            return 0
        ask = nightly.ask_model
    groups = [g for g in open_groups() if not g[0]["importance"] and g[0]["about_bot"] not in SKIP_RANK][:RANK_MAX]
    if not groups:
        return 0
    items = "\n".join(f"{i}. Seen {len(g)} time{'s' if len(g) != 1 else ''}. {consent.scrub(g[0]['summary'])[:200]}"
                      for i, g in enumerate(groups, 1))
    try:
        with shortreply.reading(tuple({g[0]['about_bot'] for g in groups})):
            found = re.search(r"\{.*\}", await ask(RANK.format(first=who.first(), items=items), 60) or "", re.S)
        ratings = json.loads(found.group(0)).get("ratings") if found else []
    except Exception as e:
        log.info("no ranking of the repair queue (%s)", type(e).__name__)
        return 0
    rated = 0
    with store._lock:
        for r in ratings if isinstance(ratings, list) else []:
            i, level = (r.get("id"), r.get("importance")) if isinstance(r, dict) else (None, None)
            if isinstance(i, int) and 1 <= i <= len(groups) and level in (1, 2, 3):
                store.db().executemany("UPDATE problem_reports SET importance=? WHERE id=?",
                                       [(level, x["id"]) for x in groups[i - 1]])
                rated += 1
        store.db().commit()
    return rated


def _frames(value):
    ""
    if isinstance(value, list):
        return [_frames(v) for v in value]
    if isinstance(value, dict):
        return {k: _frames(v) for k, v in value.items() if k in
                ("callStacks", "callStackRootFrames", "subFrames", "threadAttributed", "binaryUUID", "binaryName",
                 "offsetIntoBinaryTextSegment")}
    return value


def diagnostic_signature(platform: str, collection: str, diagnostic: dict) -> str:
    meta = diagnostic.get("diagnosticMetaData") or {}
    return _hash([platform, collection, diagnostic.get("applicationVersion"), meta.get("applicationBuildVersion"),
                  *[meta.get(k, diagnostic.get(k)) for k in ("exceptionType", "exceptionCode", "signal")],
                  meta.get("terminationReason") if not diagnostic.get("callStackTree") else None,
                  _frames(diagnostic.get("callStackTree", {}))])


def ingest(platform: str, payload: dict) -> dict:
    if platform not in ("iphone", "mac") or not isinstance(payload, dict):
        raise ProblemError("Give the app platform and a MetricKit diagnostic payload.")
    allowed = {*DIAGNOSTIC_KINDS, "version", "timeStampBegin", "timeStampEnd"}
    if payload.keys() - allowed or not any(payload.get(k) for k in DIAGNOSTIC_KINDS):
        raise ProblemError("Only MetricKit diagnostic payloads are accepted. Do not send chat content.")
    for key in DIAGNOSTIC_KINDS:
        if key in payload and (not isinstance(payload[key], list) or not all(
                isinstance(d, dict) and d and isinstance(d.get("diagnosticMetaData", {}), dict)
                for d in payload[key])):
            raise ProblemError("Each diagnostic collection must contain diagnostic objects.")
    try:
        raw = _json(payload).encode()
    except (ValueError, TypeError, RecursionError):
        raise ProblemError("Invalid diagnostic JSON.") from None
    if len(raw) > MAX_PAYLOAD:
        raise ProblemError("The diagnostic payload is too large.")
    signature = _hash([platform, payload])
    with store._lock:
        c = store.db()
        row = c.execute("SELECT id, report_ids FROM app_diagnostics WHERE signature=?", (signature,)).fetchone()
        if row:
            return {"id": row["id"], "duplicate": True, "report_ids": json.loads(row["report_ids"])}
        diagnostic_id = c.execute(
            "INSERT INTO app_diagnostics (signature, platform, payload, created_at) VALUES (?, ?, ?, ?)",
            (signature, platform, zlib.compress(raw), time.time())).lastrowid
        reports = []
        for collection, kind in DIAGNOSTIC_KINDS.items():
            for index, diagnostic in enumerate(payload.get(collection, [])):
                summary = f"The {'Mac' if platform == 'mac' else 'iPhone'} app {DIAGNOSTIC_SUMMARIES[collection]}."
                evidence = f"MetricKit payload {diagnostic_id}, {collection}[{index}]."
                reports.append(_record(c, "app", "default", kind, summary, evidence,
                                       diagnostic_signature(platform, collection, diagnostic))["id"])
        c.execute("UPDATE app_diagnostics SET report_ids=? WHERE id=?", (_json(reports), diagnostic_id))
        c.commit()
    return {"id": diagnostic_id, "duplicate": False, "report_ids": reports}


def diagnostic(diagnostic_id: int) -> dict | None:
    row = store.db().execute("SELECT platform, payload FROM app_diagnostics WHERE id=?", (diagnostic_id,)).fetchone()
    return {"platform": row["platform"], "payload": json.loads(zlib.decompress(row["payload"]))} if row else None
