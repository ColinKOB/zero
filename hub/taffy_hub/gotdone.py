""






















from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import PurePosixPath

from . import board, config, home, privatewords, shortreply, store, who

log = logging.getLogger("taffy.gotdone")

ENABLED = False
SKIP_ZIPS = config.PRIVATE_ZIPS
JUDGE_SEC = 40
WRITE_SEC = 90
SUMMARY_MAX = 2500
ITEM_NOTE_MAX = 500
LINE_WORDS = 34
TOLD_LINE_WORDS = 45
HEARD_MAX = 10
HEARD_CHARS = 240
WORK_LOOKBACK_SEC = 1800
MORNING_LAST_HOUR = 9
SETTING = "got_done_recaps"


PUSHED, CHAT, RECAP, READ, LEFT_OUT = "push", "chat", "recap", "read", "left out"

SCHEMA = """
CREATE TABLE IF NOT EXISTS got_done (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,    -- task:<task id>:<board event id> or job:<zip>:<chat row>
    bot_id TEXT NOT NULL,
    task_id TEXT,
    feed_id INTEGER,             -- the row in the Zip's own chat, when it has one
    what TEXT NOT NULL,          -- one plain sentence about the result
    summary TEXT NOT NULL,       -- the note the work filed, for the model
    at REAL NOT NULL,
    told TEXT NOT NULL DEFAULT '',
    told_at REAL
);
CREATE INDEX IF NOT EXISTS got_done_told ON got_done (told, at);
CREATE INDEX IF NOT EXISTS got_done_task ON got_done (task_id);
"""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)




def note(key: str, bot_id: str, what: str, summary: str = "", task_id: str | None = None,
         feed_id: int | None = None, at: float | None = None) -> int | None:
    ""
    bot_id = bot_id or "default"
    if shortreply.withheld(bot_id) or config.is_sandbox(bot_id):
        return None
    summary = " ".join(str(summary or "").split())[:SUMMARY_MAX]
    what = " ".join(str(what or "").split()) or summary[:200]
    if not what:
        return None
    with store._lock:
        cur = store.db().execute(
            "INSERT OR IGNORE INTO got_done (key, bot_id, task_id, feed_id, what, summary, at) VALUES (?,?,?,?,?,?,?)",
            (key, bot_id, task_id, feed_id, what, summary, time.time() if at is None else at))
        store.db().commit()
    return cur.lastrowid if cur.rowcount else None


def _row(row_id: int) -> dict | None:
    r = store.db().execute("SELECT * FROM got_done WHERE id=?", (row_id,)).fetchone()
    return dict(r) if r else None


def claim(row_id: int | None, how: str) -> bool:
    ""
    if row_id is None:
        return False
    with store._lock:
        n = store.db().execute("UPDATE got_done SET told=?, told_at=? WHERE id=? AND told=''",
                               (how, time.time(), row_id)).rowcount
        store.db().commit()
    return bool(n)


def _retell(row_id: int, how: str) -> None:
    with store._lock:
        store.db().execute("UPDATE got_done SET told=?, told_at=? WHERE id=?", (how, time.time(), row_id))
        store.db().commit()


def told(task_id: str) -> bool:
    ""
    r = store.db().execute("SELECT told FROM got_done WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    return bool(r and r["told"] in (PUSHED, CHAT))


def said_in_chat(task_ids: list[str]) -> None:
    ""
    for task_id in task_ids:
        r = store.db().execute("SELECT id FROM got_done WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        if r:
            claim(r["id"], CHAT)




SYSTEM = "You decide what a busy person hears about finished work. Answer with JSON only."

WANTED = """Notify for a result {first} asked for or is waiting on, and for a result that changes something {first} \
cares about: work that went live, money moved or saved, a deadline met, or a finding {first} would act on today. Leave \
for the recap routine upkeep, regular reports with nothing new, test results and status checks, progress on a job that \
is not finished, small steps inside a bigger job, and anything {first} already heard."""

JUDGE = """{zip} just finished something for {first}. Decide whether {first} would want a notification about it now, or \
would rather read one line about it in the next short recap.

""" + WANTED + """ Weigh it against what {first} has said matters. A long or hard job can still be routine, and a small \
fix can matter a lot.

Finished: {what}
The note it filed, written for the other assistants: {summary}
Files it made: {files}
{first} asked for this: {asked}
It is now {now}.

What {first} has said about what matters and how to be told:
{wishes}

What {first} heard lately, oldest first:
{heard}

If you notify, write the notification. Put the result first, in one or two short plain sentences that are true now, \
such as "Dan's site now says 2027 on the tax banner. It's live." Use everyday words and no ids, task names, file names, \
tool names or how the work was done. {first} does not write code. For technical work, say what is different for the people who use the product ("Uploads no longer stall on big files"), never the mechanism: no databases, connections, limits, branches or integration. Say only the part that is finished; work still under way is not in this line. Leave out what was tested or still needs checking unless {first} has to do \
something about it. Ask nothing, and never end with a question. "link" is the one web address from the note that shows \
the result to {first}, or "" when there is none. "file" is the one file from the list that {first} would open to see \
the result, or "" when they are only working notes.

Answer with this JSON and nothing else: {{"notify": true or false, "line": "...", "link": "", "file": "", "why": "a few words"}}"""

_IDS = re.compile(r"\b(?:t_[0-9a-z]{4,}|made_[0-9a-z]+|[0-9a-f]{12,})\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WEB = re.compile(r"https://[^\s)\]>\"']+")


async def ask_model(prompt: str, timeout: float) -> str:
    ""
    from . import briefing, notices
    return await briefing.ask_haiku(prompt, timeout=timeout, effort=notices.CHECK_EFFORT, model=notices.CHECK_MODEL)


def _answer(text: str | None) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def clean_line(text, words: int = LINE_WORDS) -> str:
    ""
    from . import notices
    said = _SENTENCE.split(" ".join(str(text or "").split()))
    while said and said[-1].endswith("?"):
        said.pop()
    line = notices.tidy(" ".join(said)).strip()
    if not line or _IDS.search(line) or len(line.split()) > words:
        return ""
    return line if line[-1] in ".!" else line + "."


def _link_in(summary: str, link) -> str:
    ""
    link = str(link or "").strip()
    return link if link and link in {w.rstrip(".,;:") for w in _WEB.findall(summary or "")} else ""


def _heard(since: float) -> str:
    ""
    rows = store.db().execute(
        "SELECT body, created_at AS at FROM feed WHERE bot_id='default' AND kind='message' AND created_at>=? "
        "UNION ALL SELECT what AS body, told_at AS at FROM got_done WHERE told=? AND told_at>=? ORDER BY at DESC LIMIT ?",
        (since, PUSHED, since, HEARD_MAX)).fetchall()

    return "\n".join(privatewords.keep(f"- {' '.join(str(r['body'] or '').split())[:HEARD_CHARS]}"
                                       for r in reversed(rows))) or "- (nothing)"


def _wishes(item: str) -> str:
    from . import notices, ownerrules
    said = "\n".join(p for p in (privatewords.leave_out(notices._heading_entries()), ownerrules.lines(ownerrules.UPDATE, item))
                     if p)
    return _scrub(said) or "(nothing saved)"


def _scrub(text: str) -> str:
    from . import consent
    return consent.scrub(text or "")


def _name(bot_id: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    return bot.name if bot else "A teammate"


def _asked(task_id: str | None) -> bool:
    from . import watcher
    return bool(task_id) and watcher.task_asked(task_id)


async def judge(row: dict) -> dict | None:
    ""
    first = who.first()
    files = await asyncio.to_thread(_made_files, row)
    prompt = JUDGE.format(zip=_name(row["bot_id"]), first=first, what=_scrub(row["what"]),
                          summary=_scrub(row["summary"]) or "(none)", files=", ".join(files) or "(none)", asked="yes" if _asked(row["task_id"]) else "no",
                          now=datetime.now(home.TZ).strftime("%A, %B %-d, %-I:%M %p"), wishes=_wishes(f"{row['what']} {row['summary']}"),
                          heard=_scrub(_heard(time.time() - 86400)))
    try:
        with shortreply.reading_text(f"{row['what']}\n{row['summary']}", (row['bot_id'],)):
            out = _answer(await asyncio.wait_for(ask_model(prompt, JUDGE_SEC), JUDGE_SEC + 5))
    except Exception as e:
        log.info("no answer on whether finished work is worth a notification (%s)", type(e).__name__)
        return None
    if not out or out.get("notify") is not True:
        return None
    line = clean_line(out.get("line"))
    return {"line": line, "link": _link_in(row["summary"], out.get("link")), "file": str(out.get("file") or "")} if line else None




def _made_files(row: dict) -> dict[str, str]:
    ""
    from . import board, made
    out = {}
    if not row.get("task_id"):
        return out
    try:
        for path in board._declared(board.run_filed(row["task_id"])[1]):
            file_id = made._file_id(row["bot_id"], path)
            item = made.item_id(row["bot_id"], "file", file_id) if file_id else ""
            if item and made.get(item):
                out[PurePosixPath(path).name] = item
    except (sqlite3.Error, ValueError, OSError):
        log.warning("could not read the files a finished task made", exc_info=True)
    return out


def result_link(row: dict, web: str = "", file: str = "") -> str:
    ""


    from . import board, made
    bot = row["bot_id"]
    try:
        task = board.get_task(row["task_id"]) if row.get("task_id") else None
        began = float((task or {}).get("started_at") or row["at"] - WORK_LOOKBACK_SEC)
        for r in store.db().execute("SELECT title, body FROM feed WHERE kind='result' AND bot_id=? AND created_at>=? AND "
                                    "created_at<=? ORDER BY id DESC", (bot, began, row["at"] + 120)):
            card = json.loads(r["body"] or "{}")
            item = made.item_id(bot, "result", str(card.get("media") or ""))
            if made.get(item):
                return f"[{r['title'] or 'Open it'}](zero-item:{item})"
    except (sqlite3.Error, ValueError, OSError):
        log.warning("could not find what a finished task showed", exc_info=True)
    item = _made_files(row).get(file) if file else None
    return f"[Open it](zero-item:{item})" if item else web




_settling: set[asyncio.Task] = set()


def _start(row_id: int | None) -> None:
    if row_id is None or not ENABLED:
        return
    task = asyncio.create_task(settle(row_id))
    _settling.add(task)
    task.add_done_callback(_settling.discard)


def finished(ev: dict, line: str, feed_id: int | None, summary: str) -> int | None:
    ""
    if not ENABLED or shortreply.withheld(*board.task_zips(ev)):
        return None
    row_id = note(f"task:{ev['task_id']}:{ev.get('id') or ''}", ev.get("assignee") or "default", line, summary,
                  ev["task_id"], feed_id)
    _start(row_id)
    return row_id


def report(bot_id: str, job: str, text: str, feed_id: int) -> int | None:
    ""
    if not ENABLED:
        return None
    from . import notify
    return note(f"job:{bot_id}:{feed_id}", bot_id, f"{job}: {notify.plain(text)[:300]}", text, feed_id=feed_id)


def report_quiet(row_id: int | None) -> None:
    ""
    _start(row_id)


def _open_row(row: dict, text: str) -> int:
    ""
    from . import notify, quiet
    if row["bot_id"] == "default" and row["feed_id"]:
        r = store.db().execute("SELECT title, body FROM feed WHERE id=?", (row["feed_id"],)).fetchone()
        if r and text not in r["body"]:
            store.update_feed(row["feed_id"], r["title"], f"{r['body']}\n\n{text}")
        return row["feed_id"]
    fid = store.add_feed("message", "default", None, notify.sender("default")[1], text,
                         written_by="model", written_for="person")
    if row["feed_id"]:
        quiet.save(row["feed_id"], {"quiet": "relay", "quiet_title": None, "quiet_why": "You already knew"})
    return fid


async def settle(row_id: int) -> bool:
    ""

    from . import notifyprefs, watcher
    row = _row(row_id)
    if not row or row["told"] or watcher._night():
        return False
    if not notifyprefs.allows(row["bot_id"], "done") or store.quiet(row["bot_id"]):
        return False
    said = await judge(row)
    if not said or not claim(row_id, PUSHED):
        return False
    link = await asyncio.to_thread(result_link, row, said["link"], said["file"])
    if row["bot_id"] == "default" and row["feed_id"]:
        fid = _open_row(row, link) if link else row["feed_id"]
    else:
        fid = _open_row(row, f"{said['line']}\n\n{link}" if link else said["line"])
    sent = await watcher.push(_name(row["bot_id"]), said["line"], "default", bots=[row["bot_id"]], kind="done", exact=True,
                              extra={"item_id": fid},
                              judged={"buzz": True, "source": "done", "reason": "finished", "feed_id": fid})
    if not sent:
        _retell(row_id, CHAT)
    return bool(sent)




RESULT_ASK = """{zip} finished a piece of work and needs one answer from {first}. Write the notification in at most two \
short plain sentences. First say what was done. Then ask the question below, keeping its meaning. Use no ids, task \
names, file names or tool names.

The note about the work: {summary}
The question: {ask}

Answer with this JSON and nothing else: {{"line": "..."}}"""


async def result_then_ask(bot_id: str, summary: str, ask: str, *, task) -> str:
    ""

    if not ENABLED or shortreply.withheld(bot_id, *board.task_zips(task)):
        return ""
    prompt = RESULT_ASK.format(zip=_name(bot_id), first=who.first(), summary=_scrub(" ".join(str(summary).split()))[:SUMMARY_MAX],
                               ask=_scrub(" ".join(str(ask).split())))
    from . import ownerrules
    prompt += _scrub(ownerrules.block(ownerrules.WORDS, f"{summary} {ask}", bot_id, answered=False))
    try:
        with shortreply.reading_task(task, also=(bot_id,)):
            out = _answer(await asyncio.wait_for(ask_model(prompt, JUDGE_SEC), JUDGE_SEC + 5))
    except Exception as e:
        log.info("no result-first line for a review (%s)", type(e).__name__)
        return ""
    line = " ".join(str((out or {}).get("line") or "").split())
    return line if line and not _IDS.search(line) and len(line.split()) <= TOLD_LINE_WORDS else ""




RECAP = """Write the lines of {first}'s {part} recap of what the team finished. {first} reads it on a phone.

Finished work, oldest first:
{items}

Already said to {first} in the chat:
{said}

What {first} has said about what matters:
{wishes}

Write one line per finished thing {first} would want to know about. Say what was found or made and what it changes for \
{first}, in one short sentence of everyday words in the past tense, the way a teammate would text it. Leave out what was \
tested or checked. Put several items about the same work into one line. Leave out anything already said above, \
housekeeping (tidying, checks that found nothing, a closed duplicate) and steps that another line covers. Use no ids, \
task names, file names, tool names or how the work was done. {first} does not write code. For technical work, say what is different for the people who use the product ("Uploads no longer stall on big files"), never the mechanism: no databases, connections, limits, branches or integration. Say only the part that is finished; work still under way is not in this line. Ask nothing. "ids" lists the item numbers the line \
covers. "link" is one web address from those items' notes that shows the result, or "". "file" is the one file from \
those items that {first} would open to see the result, or "" when they are only working notes.

Answer with this JSON and nothing else: {{"lines": [{{"ids": [1], "line": "...", "link": "", "file": ""}}]}}"""

OPENINGS = {"morning": "Morning. Here's what got done since last night.", "evening": "Evening. Here's what got done today."}
NUMBER = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}


def pending() -> list[dict]:
    ""
    from . import readmarks
    marks = readmarks.changed()
    out = []
    for r in store.db().execute("SELECT * FROM got_done WHERE told='' ORDER BY at, id").fetchall():
        row = dict(r)
        if row["feed_id"] and marks.get(row["bot_id"], 0) >= row["at"]:
            claim(row["id"], READ)
        else:
            out.append(row)
    return out


def closing(waiting: int) -> str:
    ""
    if waiting <= 0:
        return ""
    many = NUMBER.get(waiting, str(waiting))
    return "One thing needs you when you have a minute." if waiting == 1 else f"{many} things need you when you have a minute."


def waiting_count() -> int:
    ""
    from . import work
    try:
        return len(work.overview()["needs_you"])
    except Exception:
        log.warning("could not count what waits on the person", exc_info=True)
        return 0


async def _write(rows: list[dict], part: str) -> tuple[list[dict], bool]:
    ""


    held = [r for r in rows if privatewords.holds(f"{r['what']} {r['summary']}")]
    rest = [r for r in rows if r not in held]
    written, chosen = await _write_some(rest, part) if rest else ([], False)
    return written + [{"rows": [r], "line": clean_line(r["what"]), "link": "", "file": ""} for r in held
                      if clean_line(r["what"])], chosen


async def _write_some(rows: list[dict], part: str) -> tuple[list[dict], bool]:
    numbered = dict(enumerate(rows, 1))
    files = {i: await asyncio.to_thread(_made_files, r) for i, r in numbered.items()}
    items = "\n".join(f"{i}. {_name(r['bot_id'])}: {_scrub(r['what'])} Note: {_scrub(r['summary'])[:ITEM_NOTE_MAX] or '(none)'} "
                      f"Files: {', '.join(files[i]) or '(none)'}" for i, r in numbered.items())
    since = min(r["at"] for r in rows)
    prompt = RECAP.format(first=who.first(), part=part, items=items, said=_scrub(_heard(since)), wishes=_wishes(items))
    try:
        with shortreply.reading(tuple({r['bot_id'] for r in rows})):
            out = _answer(await asyncio.wait_for(ask_model(prompt, WRITE_SEC), WRITE_SEC + 5))
    except Exception as e:
        log.info("no recap from the model (%s)", type(e).__name__)
        out = None
    lines = (out or {}).get("lines")
    if not isinstance(lines, list):
        return [{"rows": [r], "line": clean_line(r["what"]), "link": "", "file": ""} for r in rows
                if clean_line(r["what"])], False
    written, used = [], set()
    for item in lines:
        if not isinstance(item, dict) or not isinstance(item.get("ids"), list):
            continue
        ids = [i for i in item["ids"] if isinstance(i, int) and i in numbered and i not in used]
        line = clean_line(item.get("line"))
        if ids and line:
            used.update(ids)
            covered = [numbered[i] for i in ids]
            link = next((w for r in covered if (w := _link_in(r["summary"], item.get("link")))), "")
            written.append({"rows": covered, "line": line, "link": link, "file": str(item.get("file") or "")})
    return written, True


async def compose(part: str) -> dict | None:
    ""
    rows = await asyncio.to_thread(pending)
    if not rows:
        return None
    written, chosen = await _write(rows, part)
    covered = {r["id"] for w in written for r in w["rows"]}
    left_out = [r["id"] for r in rows if r["id"] not in covered] if chosen else []
    if not written:
        return {"text": "", "written": [], "left_out": left_out}
    shown = []
    for w in written:
        link = ""
        for r in w["rows"]:
            link = link or await asyncio.to_thread(result_link, r, w["link"], w["file"])
        shown.append(f"- {_name(w['rows'][0]['bot_id'])}: {w['line']}" + (f" {link}" if link else ""))
    return {"text": OPENINGS[part] + "\n\n" + "\n".join(shown), "written": written, "left_out": left_out}


def _settle_recap(recap: dict) -> None:
    for w in recap["written"]:
        for r in w["rows"]:
            claim(r["id"], RECAP)
    for row_id in recap["left_out"]:
        claim(row_id, LEFT_OUT)


def _today() -> str:
    return datetime.now(home.TZ).date().isoformat()


def _mark(**days) -> None:
    store.set_setting(SETTING, {**(store.get_setting(SETTING, {}) or {}), **days})


def folded_today() -> bool:
    ""
    return (store.get_setting(SETTING, {}) or {}).get("folded") == _today()


async def fold_brief(brief: str) -> str:
    ""

    if not ENABLED or (store.get_setting(SETTING, {}) or {}).get("morning") == _today():
        return brief
    _mark(morning=_today())
    try:
        from . import nightly
        recap = await compose("morning")
        if recap is not None:
            _settle_recap(recap)
        done = recap["text"] if recap else ""
        missed = await nightly.section()
        if not done and not missed:
            return brief
        waits = closing(await asyncio.to_thread(waiting_count))
        _mark(folded=_today())
        return "\n\n".join(p for p in (done, missed, brief, waits) if p)
    except Exception:
        log.exception("could not put what got done in the morning brief")
        return brief


async def post_recap(part: str) -> bool:
    ""

    from . import nightly, notify, watcher
    recap = await compose(part)
    if recap is not None:
        _settle_recap(recap)
    done = recap["text"] if recap else ""
    missed = await nightly.section() if part == "morning" else ""
    if not done and not missed:
        return False
    waits = closing(await asyncio.to_thread(waiting_count))
    text = "\n\n".join(p for p in (done, missed, waits) if p)
    fid = store.add_feed("message", "default", None, notify.sender("default")[1], text,
                         written_by="model", written_for="person")



    from . import notifyprefs
    sources = {r["bot_id"] for w in (recap or {}).get("written", []) for r in w["rows"]}
    if not missed and not any(notifyprefs.allows(b, "done") and not store.quiet(b) for b in sources):
        return False
    await watcher.push(notify.sender("default")[1], OPENINGS[part] if done else missed.split("\n")[-1].removeprefix("- "),
                       "default", kind="done", exact=True, extra={"item_id": fid},
                       judged={"buzz": True, "source": "done", "reason": "recap", "feed_id": fid})
    return True


_recapping: list = [None]


def due(now: datetime | None = None) -> str | None:
    ""
    from . import notices, watcher
    now = now or datetime.now(home.TZ)
    if watcher._night():
        return None
    day, marks = now.date().isoformat(), store.get_setting(SETTING, {}) or {}
    if now.hour >= int(notices.settings()["recap_evening_hour"]):
        return "evening" if marks.get("evening") != day else None
    return "morning" if now.hour >= MORNING_LAST_HOUR and marks.get("morning") != day else None


async def tick() -> None:
    ""
    if not ENABLED or (_recapping[0] is not None and not _recapping[0].done()):
        return
    part = due()
    if part:
        _mark(**{part: _today()})
        _recapping[0] = asyncio.create_task(_recap_safely(part))


async def _recap_safely(part: str) -> None:
    try:
        await post_recap(part)
    except Exception:
        log.exception("the %s recap failed", part)
