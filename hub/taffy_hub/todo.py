""























from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta

from . import config, home, store, who

log = logging.getLogger("taffy.todo")

YOU = "you"
SHOWN = 5
SHOWN_MAX = 20
TITLE_MAX = 80
NOTE_MAX = 60
REASON_MAX = 80
FACTS_MAX = 6
LINKS_MAX = 6
OPEN_MAX = 200
SYNC_EVERY_SEC = 5
NEW_SEC = 24 * 3600
MOVED_REASON = "You moved this up. I’m on it."
SETTING = "todo_list"

_synced_at = 0.0


_unclaimed: dict[str, list[str]] = {}


class TodoError(Exception):
    pass


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS todo_lines (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,        -- a Zip's id, or "you" for the person's own task
            title TEXT NOT NULL,
            note TEXT,                  -- the gray words under the title, when the Zip gave some
            due TEXT,                   -- 2026-11-03, or 2026-11-03T11:00 in the home's time
            reason TEXT,                -- Zero's few words on why it is first; shown only while it is
            facts TEXT,                 -- JSON [[label, value]] for the details
            links TEXT,                 -- JSON [{"label", "url"}]: the details' resource pills
            project TEXT,               -- a Work project id: the details' Open Project
            card TEXT,                  -- the card it opens in the app: task-<id>, signin-<id> or message-<feed id>
            source TEXT UNIQUE,         -- the record it stands for (task:<id>, request:<id>, feed:<id>), or NULL
            came_in TEXT,               -- JSON [feed ids]: the messages behind it, shown in full in the details
            buzz TEXT,                  -- JSON {"buzz", "why"}: whether adding it buzzed the phone, or NULL
            pos INTEGER NOT NULL,
            status TEXT NOT NULL,       -- open | done
            seen INTEGER NOT NULL DEFAULT 0,
            added_by TEXT NOT NULL,     -- you, a Zip's id, or hub (sync)
            added_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            done_at REAL
        );
        CREATE INDEX IF NOT EXISTS todo_lines_open ON todo_lines(status, pos);
    """)


    if "do_on" not in {r[1] for r in c.execute("PRAGMA table_info(todo_lines)")}:
        c.execute("ALTER TABLE todo_lines ADD COLUMN do_on TEXT")




def _meta() -> dict:
    meta = store.get_setting(SETTING, None) or {}
    return {"revision": int(meta.get("revision") or 0), "shown": int(meta.get("shown") or SHOWN),
            "sorted_on": meta.get("sorted_on"), "ordered_on": meta.get("ordered_on")}


def _bump(**changes) -> int:
    meta = {**_meta(), **changes}
    meta["revision"] += 1
    store.set_setting(SETTING, meta)
    return meta["revision"]




def _line(value, limit: int, what: str, required: bool = False) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        if required:
            raise TodoError(f"Give the {what}.")
        return None
    if len(text) > limit:
        raise TodoError(f"Keep the {what} under {limit} characters.")
    return text


_DUE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?$")


def _due(value) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not _DUE.match(text):
        raise TodoError("Give the due date as 2026-11-05, or 2026-11-05T15:30 with a time.")
    try:
        _parse_due(text)
    except ValueError:
        raise TodoError("That due date is not a real day.") from None
    return text.replace(" ", "T")


def _parse_due(text: str) -> datetime:
    if "T" in text or " " in text:
        return datetime.strptime(text.replace(" ", "T"), "%Y-%m-%dT%H:%M").replace(tzinfo=home.TZ)
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=home.TZ)


def _facts(value) -> list[list[str]]:
    if not value:
        return []
    if not isinstance(value, list) or len(value) > FACTS_MAX:
        raise TodoError(f"Give up to {FACTS_MAX} facts, each [label, value].")
    out = []
    for f in value:
        if isinstance(f, dict):
            f = [f.get("label"), f.get("value")]
        if not isinstance(f, (list, tuple)) or len(f) != 2:
            raise TodoError("Each fact is [label, value], like [\"Takes\", \"About 15 minutes\"].")
        out.append([_line(f[0], 24, "fact's label", True), _line(f[1], 80, "fact", True)])
    return out


def _links(value) -> list[dict]:
    if not value:
        return []
    if not isinstance(value, list) or len(value) > LINKS_MAX:
        raise TodoError(f"Give up to {LINKS_MAX} links, each {{label, url}}.")
    out = []
    for link in value:
        if not isinstance(link, dict):
            raise TodoError("Each link is {label, url}.")
        url = str(link.get("url") or "").strip()
        if not re.match(r"^https?://\S+$", url, re.I) or len(url) > 2000:
            raise TodoError("A link's url must be a full web address starting with https://.")
        out.append({"label": _line(link.get("label"), 40, "link's label", True), "url": url})
    return out


def _owner(value, by: str) -> str:
    owner = str(value or by).strip()
    if owner != YOU and owner not in config.BOTS_BY_ID:
        raise TodoError(f'"{owner}" is not one of the team.')
    return owner




GROUPS = ("today", "tomorrow", "week", "later")
WEEK_DAYS = 6


def _today_date():
    return datetime.now(home.TZ).date()


def group_of(do_on: str | None, due: str | None, today=None) -> str:
    ""

    today = today or _today_date()
    if do_on == "later":
        return "later"
    day = do_on or (due[:10] if due else None)
    if not day:
        return "later"
    d = datetime.strptime(day, "%Y-%m-%d").date()
    if d <= today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return "week" if d <= today + timedelta(days=WEEK_DAYS) else "later"


def _do_on(group: str, current: str | None = None, due: str | None = None) -> str | None:
    ""
    if group not in GROUPS:
        raise TodoError("Put a line in today, tomorrow, week or later.")
    if group_of(current, due) == group:
        return current
    today = _today_date()
    return {"today": today.isoformat(), "tomorrow": (today + timedelta(days=1)).isoformat(),
            "week": (today + timedelta(days=2)).isoformat(), "later": "later"}[group]


def _ordered(rows) -> list:
    ""
    today = _today_date()
    return sorted(rows, key=lambda r: (GROUPS.index(group_of(r["do_on"], r["due"], today)), r["pos"], r["added_at"]))




def _open_ids(c: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in c.execute("SELECT id FROM todo_lines WHERE status='open' ORDER BY pos, added_at")]


def _write_order(c: sqlite3.Connection, ids: list[str]) -> None:
    c.executemany("UPDATE todo_lines SET pos=? WHERE id=?", [(i, line_id) for i, line_id in enumerate(ids)])


def _place(c: sqlite3.Connection, line_id: str, his: bool, buzzed: bool) -> None:
    ""

    ids = [i for i in _open_ids(c) if i != line_id]
    if his:
        at = 0
    elif buzzed:
        at = min(1, len(ids))
    else:
        at = min(_meta()["shown"], len(ids))
    ids.insert(at, line_id)
    _write_order(c, ids)


def _trim(c: sqlite3.Connection) -> None:
    ids = _open_ids(c)
    if len(ids) > OPEN_MAX:
        late = ids[OPEN_MAX:]
        c.executemany("UPDATE todo_lines SET status='done', done_at=? WHERE id=?", [(time.time(), i) for i in late])




def _new_id() -> str:
    return "l" + secrets.token_hex(5)


def add(title, by: str, *, owner=None, note=None, due=None, facts=None, links=None, project=None, card=None,
        source: str | None = None, came_in: list[int] | None = None, buzz: dict | None = None,
        reason=None, when=None) -> dict:
    ""

    title = _line(title, TITLE_MAX, "title", True)
    owner = _owner(owner, by if by != "hub" else "default")
    fields = {"note": _line(note, NOTE_MAX, "note"), "due": _due(due), "facts": json.dumps(_facts(facts)),
              "links": json.dumps(_links(links)), "project": _line(project, 120, "project"),
              "reason": _line(reason, REASON_MAX, "reason")}


    urgent = by == YOU or bool((buzz or {}).get("buzz")) or str(source or "").startswith(("task:", "request:"))
    do_on = _do_on(str(when), None, fields["due"]) if when else (_today_date().isoformat() if urgent else None)
    now = time.time()
    with store._lock:
        c = store.db()
        if source:
            had = c.execute("SELECT * FROM todo_lines WHERE source=?", (source,)).fetchone()
            if had:
                return _public(had)
        line_id = _new_id()
        c.execute("INSERT INTO todo_lines (id, owner, title, note, due, reason, facts, links, project, card, source, "
                  "came_in, buzz, pos, status, seen, added_by, added_at, updated_at, do_on) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?,?,?,?,?)",
                  (line_id, owner, title, fields["note"], fields["due"], fields["reason"], fields["facts"],
                   fields["links"], fields["project"], card, source, json.dumps(came_in or []),
                   json.dumps(buzz) if buzz else None, 1_000_000, int(by == YOU), by, now, now, do_on))
        _place(c, line_id, by == YOU, bool((buzz or {}).get("buzz")))
        _trim(c)
        c.commit()
        _bump()
        return _public(c.execute("SELECT * FROM todo_lines WHERE id=?", (line_id,)).fetchone())


EDITABLE = ("title", "note", "due", "facts", "links", "project", "reason", "when")







DATE_NOTE = ("{first} added a task to your list: \u201c{title}\u201d (line {line}), with no date. If what you know "
             "tells you when it should be done, set it with app_todo_list update (line and due) and tell {first} in "
             "one line, such as \u201cI set it for Friday, since the form is due Monday.\u201d If you truly can't "
             "tell, ask {first} once with offer_choices: one short question with three or four dates. Ask about this "
             "line only this once.")


def add_his(title, bot_id: str | None = None, due=None) -> dict:
    ""

    owner = bot_id if bot_id in config.BOTS_BY_ID and not config.is_sandbox(bot_id) else "default"
    line = add(title, YOU, owner=owner, due=due)
    if not line.get("due"):
        store.add_card_note(owner, DATE_NOTE.format(first=who.first(), title=line["title"], line=line["id"]))
    return line


def update(line_id: str, by: str, **changes) -> dict:
    ""
    with store._lock:
        c = store.db()
        row = _owned(c, line_id, by)
        sets = {}
        for key, value in changes.items():
            if key not in EDITABLE or value is None:
                continue
            if key == "title":
                sets[key] = _line(value, TITLE_MAX, "title", True)
            elif key == "note":
                sets[key] = _line(value, NOTE_MAX, "note")
            elif key == "reason":
                sets[key] = _line(value, REASON_MAX, "reason")
            elif key == "project":
                sets[key] = _line(value, 120, "project")
            elif key == "due":
                sets[key] = _due(value)
            elif key == "facts":
                sets[key] = json.dumps(_facts(value))
            elif key == "links":
                sets[key] = json.dumps(_links(value))
            elif key == "when":
                sets["do_on"] = _do_on(str(value), row["do_on"], sets.get("due", row["due"]))
        if sets:
            sets["updated_at"] = time.time()
            c.execute(f"UPDATE todo_lines SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                      (*sets.values(), row["id"]))
            c.commit()
            _bump()
        return _public(c.execute("SELECT * FROM todo_lines WHERE id=?", (row["id"],)).fetchone())


def _owned(c: sqlite3.Connection, line_id: str, by: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM todo_lines WHERE id=?", (str(line_id or "").strip(),)).fetchone()
    if not row:
        raise TodoError("There is no line with that id. Read the list for the current ids.")
    if by not in (YOU, "default", "hub") and row["owner"] != by:
        raise TodoError("That line is another Zip's. Change only your own lines.")
    return row


def finish(line_id: str, by: str, done: bool = True) -> dict:
    ""
    with store._lock:
        c = store.db()
        row = _owned(c, line_id, by)
        if (row["status"] == "done") != done:
            c.execute("UPDATE todo_lines SET status=?, done_at=?, updated_at=? WHERE id=?",
                      ("done" if done else "open", time.time() if done else None, time.time(), row["id"]))
            if not done:
                ids = _open_ids(c)
                _write_order(c, ids)


                c.execute("UPDATE work_user_state SET done=0, handled_revision=NULL WHERE id=?", (f"todo:{row['id']}",))
            c.commit()
            _bump()
        return _public(c.execute("SELECT * FROM todo_lines WHERE id=?", (row["id"],)).fetchone())


def seen(line_id: str) -> None:
    with store._lock:
        c = store.db()
        if c.execute("UPDATE todo_lines SET seen=1 WHERE id=? AND seen=0", (line_id,)).rowcount:
            c.commit()
            _bump()


def save_order(ids: list[str], shown: int | None = None, groups: dict | None = None) -> dict:
    ""



    with store._lock:
        c = store.db()
        first_before = _first(c)
        regrouped = []
        for line_id, group in (groups or {}).items():
            row = c.execute("SELECT * FROM todo_lines WHERE id=? AND status='open'", (str(line_id),)).fetchone()
            if row and group_of(row["do_on"], row["due"]) != group:
                c.execute("UPDATE todo_lines SET do_on=?, updated_at=? WHERE id=?",
                          (_do_on(str(group), row["do_on"], row["due"]), time.time(), row["id"]))
                regrouped.append(row["id"])
        before = _open_ids(c)
        current = set(before)
        wanted = [i for i in dict.fromkeys(str(x) for x in ids) if i in current]
        if not wanted and before:
            raise TodoError("None of those lines are on the list now.")
        rest = [i for i in before if i not in set(wanted)]
        after = list(wanted)
        for line_id in rest:
            at = before.index(line_id)
            above = next((before[j] for j in range(at - 1, -1, -1) if before[j] in after), None)
            after.insert(after.index(above) + 1 if above else 0, line_id)
        moved = [i for i in wanted if [x for x in before if x in set(wanted)].index(i) != wanted.index(i)]
        moved += [i for i in regrouped if i not in moved]
        _write_order(c, after)
        top, was_top = _first(c), first_before
        if top and top != was_top:
            c.execute("UPDATE todo_lines SET reason=?, updated_at=? WHERE id=?", (MOVED_REASON, time.time(), top))
        c.commit()
        changes = {}
        if shown is not None:
            changes["shown"] = max(1, min(SHOWN_MAX, int(shown)))
        _bump(**changes)
        named = [i for i in {*after, was_top} if i]
        titles = {r["id"]: r["title"] for r in c.execute(
            f"SELECT id, title FROM todo_lines WHERE id IN ({','.join('?' * len(named))})", named)} if named else {}
        return {"top": top, "top_title": titles.get(top), "was_top": was_top, "was_top_title": titles.get(was_top),
                "moved": [titles[i] for i in moved if i in titles]}


def _first(c: sqlite3.Connection) -> str | None:
    ""
    rows = _ordered(c.execute("SELECT id, do_on, due, pos, added_at FROM todo_lines WHERE status='open'").fetchall())
    return rows[0]["id"] if rows else None


def order(ids: list[str], reason=None) -> dict:
    ""

    reason = _line(reason, REASON_MAX, "reason")
    with store._lock:
        c = store.db()
        before = _open_ids(c)
        wanted = [i for i in dict.fromkeys(str(x) for x in ids or []) if i in set(before)]
        unknown = [str(x) for x in ids or [] if str(x) not in set(before)]
        if unknown:
            raise TodoError(f"These ids are not open lines: {', '.join(unknown[:5])}. Read the list for the current ids.")
        after = wanted + [i for i in before if i not in set(wanted)]
        _write_order(c, after)
        first = _first(c)
        if reason and first:
            c.execute("UPDATE todo_lines SET reason=?, updated_at=? WHERE id=?", (reason, time.time(), first))
        c.commit()
        _bump()
    return view()




SYNCED = ("task:%", "request:%", "card:%")


def sync(force: bool = False) -> None:
    ""


    global _synced_at
    if not force and time.time() - _synced_at < SYNC_EVERY_SEC:
        return
    _synced_at = time.time()
    where = " OR ".join("source LIKE ?" for _ in SYNCED)
    with store._lock:
        c = store.db()
        done = c.execute(f"UPDATE todo_lines SET status='done', done_at=?, updated_at=? WHERE status='open' AND ({where})",
                         (time.time(), time.time(), *SYNCED)).rowcount
        done += _finish_answered_cards(c)
        c.commit()
    if done:
        _bump()


CARD_KINDS = ("choices", "mail", "project", "login", "result")


def _finish_answered_cards(c: sqlite3.Connection) -> int:
    ""

    finished = 0
    for r in c.execute("SELECT id, source FROM todo_lines WHERE status='open' AND source LIKE 'feed:%'").fetchall():
        parts = str(r["source"]).split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        card = c.execute("SELECT kind, body FROM feed WHERE id=?", (int(parts[1]),)).fetchone()
        if card is None or card["kind"] not in CARD_KINDS:
            continue
        try:
            state = (json.loads(card["body"] or "{}") or {}).get("state", "open")
        except ValueError:
            state = "open"
        if state != "open":
            c.execute("UPDATE todo_lines SET status='done', done_at=?, updated_at=? WHERE id=?",
                      (time.time(), time.time(), r["id"]))
            finished += 1
    return finished


RESORT = False










def _due_ts(due: str | None) -> float | None:
    if not due:
        return None
    try:
        return _parse_due(due).timestamp()
    except ValueError:
        return None


KEEP_DONE_SEC = 2 * 86400


def sources() -> list:
    from . import work
    out = []
    for r in store.db().execute("SELECT * FROM todo_lines WHERE status='open' OR (status='done' AND done_at>?) "
                                "ORDER BY added_at, id", (time.time() - KEEP_DONE_SEC,)):
        owner = r["owner"] if r["owner"] in config.BOTS_BY_ID else "default"
        out.append(work.Source(f"todo:{r['id']}", "todo", r["title"], owner, float(r["added_at"] or 0),
                               {"card": f"todo:{r['id']}"}, r["note"] or "",
                               "needs_you" if r["status"] == "open" else "updates", date=_due_ts(r["due"]),
                               reference={"line": r["id"], "yours": YOU in (r["owner"], r["added_by"])}))
    return out


def line_of(item_id: str) -> str | None:
    ""
    return item_id.removeprefix("todo:") if str(item_id).startswith("todo:") else None


def _same_words(a: str, b: str) -> bool:
    def norm(t):
        return " ".join(re.sub(r"[^\w\s]", " ", str(t or "").casefold()).split())
    return bool(norm(a)) and norm(a) == norm(b)


def already_shown(bot_id: str, title: str, about: str | None) -> dict | None:
    ""


    from . import work, workstate
    try:
        rows = [r for r in work._sources() if r.get("section") == "needs_you" and workstate.visible(r)
                and r.get("kind") != "todo"]
    except Exception:
        log.warning("could not read Needs You for a new line", exc_info=True)
        return None
    if about:
        return next((r for r in rows if r.get("id") == str(about).strip()), None)
    return next((r for r in rows if (bot_id == "default" or r.get("bot_id") == bot_id)
                 and any(_same_words(title, r.get(k)) for k in ("title", "short_title", "ask"))), None)




def _due_words(due: str | None, now: datetime) -> tuple[str | None, str | None]:
    ""

    if not due:
        return None, None
    at = _parse_due(due)
    timed = "T" in due
    clock = at.strftime("%-I:%M %p") if timed else ""
    end = at if timed else at + timedelta(days=1)
    if end <= now:
        return "Overdue", "danger"
    day = at.date()
    if day == now.date():
        return ("Today at " + clock) if timed else "Today", "amber"
    if day == now.date() + timedelta(days=1):
        return ("Tomorrow at " + clock) if timed else "Tomorrow", None
    words = at.strftime("%a, %b %-d") + (" at " + clock if timed else "")
    return words, None


def _when_words(at: float, now: datetime) -> str:
    t = datetime.fromtimestamp(at, home.TZ)
    clock = t.strftime("%-I:%M %p")
    if t.date() == now.date():
        return clock
    if t.date() == now.date() - timedelta(days=1):
        return "yesterday at " + clock
    return t.strftime("%b %-d at ") + clock


def _public(row: sqlite3.Row, now: datetime | None = None) -> dict:
    now = now or datetime.now(home.TZ)
    due_words, tone = _due_words(row["due"], now)
    buzz = json.loads(row["buzz"]) if row["buzz"] else None
    line = row["note"] or due_words or ("You added this" if YOU in (row["owner"], row["added_by"]) else None)
    return {"id": row["id"], "owner": row["owner"], "title": row["title"], "line": line, "tone": tone,
            "due": row["due"], "reason": row["reason"], "bell": bool(buzz and buzz.get("buzz")),
            "new": not row["seen"] and time.time() - row["added_at"] < NEW_SEC, "card": row["card"],
            "project": row["project"], "status": row["status"],
            "group": group_of(row["do_on"], row["due"], now.date())}


def view() -> dict:
    ""
    meta = _meta()
    now = datetime.now(home.TZ)
    with store._lock:
        rows = _ordered(store.db().execute("SELECT * FROM todo_lines WHERE status='open'").fetchall())
    return {"revision": meta["revision"], "shown": meta["shown"], "lines": [_public(r, now) for r in rows]}


def details(line_id: str) -> dict | None:
    ""

    with store._lock:
        c = store.db()
        row = c.execute("SELECT * FROM todo_lines WHERE id=?", (line_id,)).fetchone()
        if not row:
            return None
        ids = [int(i) for i in json.loads(row["came_in"] or "[]")]
        feed = c.execute(f"SELECT id, bot_id, title, body, created_at FROM feed WHERE id IN "
                         f"({','.join('?' * len(ids))}) ORDER BY id", ids).fetchall() if ids else []
    now = datetime.now(home.TZ)
    out = _public(row, now)
    facts = json.loads(row["facts"] or "[]")
    if row["due"]:
        words, tone = _due_words(row["due"], now)
        when = _parse_due(row["due"])
        exact = when.strftime("%a, %b %-d") + (when.strftime(" at %-I:%M %p") if "T" in row["due"] else "")
        facts = [["Due", exact if words != exact else words, tone]] + facts
    out.update(facts=facts, links=json.loads(row["links"] or "[]"), how=_how(row, now),
               came_in=[{"id": f["id"], "bot_id": f["bot_id"] or "default", "label": f["title"] or "",
                         "text": f["body"] or "", "at": _when_words(f["created_at"], now)} for f in feed],
               added_by=row["added_by"])
    return out


def _how(row: sqlite3.Row, now: datetime) -> dict:
    ""
    when = _when_words(row["added_at"], now)
    buzz = json.loads(row["buzz"]) if row["buzz"] else None
    if row["added_by"] == YOU:
        return {"buzz": False, "text": f"Added quietly at {when}. You added it yourself, so I didn’t buzz you."}
    if buzz and buzz.get("buzz"):
        why = buzz.get("why") or "it couldn’t wait."
        return {"buzz": True, "text": f"Added at {when}. {why}"}
    if buzz:
        why = buzz.get("why") or "It can wait until you look."
        return {"buzz": False, "text": f"Added quietly at {when}. {why}"}
    return {"buzz": False, "text": f"Added at {when}."}


def for_zip(bot_id: str) -> list[dict]:
    ""


    lines = view()["lines"]
    if bot_id != "default":
        return [l for l in lines if l["owner"] == bot_id]
    return [{**l, "line": ""} if config.is_private(l["owner"]) else l for l in lines]


def describe(bot_id: str, limit: int = 30) -> str:
    ""
    lines = for_zip(bot_id)
    if not lines:
        return "The list is empty."
    heads = {"today": "Today:", "tomorrow": "Tomorrow:", "week": "This Week:", "later": "Later:"}
    out, group = [], None
    for i, l in enumerate(lines[:limit]):
        if l["group"] != group:
            group = l["group"]
            out.append(heads[group])
        owner = who.first() if l["owner"] == YOU else config.BOTS_BY_ID[l["owner"]].name \
            if l["owner"] in config.BOTS_BY_ID else l["owner"]
        bits = [b for b in (l["line"], l["due"] and f"due {l['due']}", "buzzed" if l["bell"] else None) if b]
        out.append(f"{i + 1}. [{l['id']}] {l['title']} ({owner}{'; ' + '; '.join(bits) if bits else ''})")
    if len(lines) > limit:
        out.append(f"...and {len(lines) - limit} more.")
    return "\n".join(out)





ASKED = re.compile(r"\b(to-?dos?|to do list|my (list|tasks|day)|tasks? (for )?today|today'?s tasks|what'?s (next|on))\b",
                   re.I)


def asked_about(bot_id: str, message: str, limit: int = 12) -> str | None:
    ""
    if bot_id != "default" or not ASKED.search(message or ""):
        return None
    return (f"{who.possessive()} to-do list right now, in order. Lines marked {who.first()} are ones "
            f"{who.first()} added. Work from these:\n" + describe(bot_id, limit))









_SOURCE_WORDS = {"deadline": "the course site", "cerego": "a Cerego reminder email", "task": "a board task",
                 "approval": "an okay card", "choices": "a question card", "team_need": "a team's request",
                 "mail": "an email a helper drafted", "email": "an email waiting on a reply",
                 "text": "a text waiting on a reply", "meeting": "the calendar", "calendar": "the calendar"}
_GROUP_WORDS = {"now": "Now", "week": "This Week", "whenever": "Whenever"}


def _date_words(row: dict) -> str:
    ""
    from . import workdue
    if workdue.pace_at(row) is not None or row.get("pace_at") is not None:
        return ("no due date is posted; the unit's last day is only the course's pace: "
                + (row.get("summary") or "").removeprefix("No due date is posted in ").strip())
    if row.get("due_at") is None:
        return "no date"
    when = datetime.fromtimestamp(float(row["due_at"]), home.TZ).strftime("%a, %b %-d" + (" at %-I:%M %p"
                                                                                        if row.get("due_timed") else ""))
    if row.get("kind") == "deadline":
        return f"due {when}, a deadline the course site posts ({(row.get('summary') or '').strip()})"
    return f"due {when}, read from the row's own words"


def needs_you_lines(bot_id: str, limit: int = 30) -> str:
    ""
    from . import work
    try:
        rows = work.overview()["needs_you"]
    except Exception:
        log.warning("could not read Needs You for a Zip", exc_info=True)
        return ""

    rows = [r for r in rows if r.get("kind") != "todo" and (bot_id == "default" or r.get("bot_id") == bot_id)]

    rows = [{**r, "act": "", "short_title": "", "why_label": "", "summary": ""}
            if config.is_private(r.get("bot_id")) and r.get("bot_id") != bot_id else r for r in rows]
    from . import remindermirror
    radar = remindermirror.due_today_ids() if bot_id == "default" else set()
    hidden = remindermirror.left_out() if bot_id == "default" else []
    out = []
    if rows:
        out.append(f"What {who.possessive()} Needs You panel and Work page show right now (not this list; answer about "
                   "these from here):")
    for r in rows[:limit]:
        title = r.get("act") or r.get("short_title") or r.get("title") or ""
        label = r.get("why_label") or ""
        out.append(f"- [{r.get('id')}] {title} ({_GROUP_WORDS.get(r.get('group'), r.get('group') or '')}"
                   + (f", shown as {label}" if label else "")
                   + f"; from {_SOURCE_WORDS.get(r.get('kind'), r.get('kind') or 'a helper')}; {_date_words(r)})"
                   + ("; due today, so see whether you can prepare it or do part of it, by your usual rules on what "
                      f"needs {who.first()}'s yes" if r.get("id") in radar else ""))
    if len(rows) > limit:
        out.append(f"...and {len(rows) - limit} more.")
    if hidden:

        out.append(f"Reminders kept off {who.possessive()} list because they don't need {who.first()} soon (they are "
                   "still in Reminders): " + "; ".join(hidden) + ".")
    return "\n".join(out)




def saved_note(change: dict) -> str | None:
    ""
    if not change.get("top") or change["top"] == change.get("was_top"):
        return None
    first = who.first()
    others = [t for t in change.get("moved", []) if t != change.get("top_title")]
    return (f"[From the hub, not a message from {first}] {first} saved a new order for {who.possessive()} list. "
            f"First now: \u201c{change['top_title']}\u201d"
            + (f" (it was \u201c{change['was_top_title']}\u201d)" if change.get("was_top_title") else "") + "."
            + (f" {first} also moved: " + "; ".join(f"\u201c{t}\u201d" for t in others[:5]) + "." if others else "")
            + f"\n\nReply once, in one or two short sentences: what is first now and what you will do about it, "
            f"such as \u201cGot it. \u201cSend Matt your October hours\u201d is first now. I\u2019ll fill in the draft "
            f"to Matt.\u201d" + (" Say you will follow the new order for the rest too." if others else "")
            + " Then start on it if you can. Don't list the order back, and don't ask about each line.")


KEPT_ORDER = "Got it. I\u2019ll follow your new order."




def _why(decision: dict, sent: bool) -> str:
    ""
    if decision.get("buzz") and sent:
        if decision.get("source") == "asked":
            return "I buzzed you because you asked for this."
        if decision.get("reason") == "urgent words":
            return "I buzzed you because it is due soon."
        return "I buzzed you because it couldn\u2019t wait."
    if decision.get("buzz"):
        return "Your notifications were off, so it waited here."
    note = (decision.get("note") or "").strip()
    if note:
        return note if note.endswith((".", "!", "?")) else note + "."
    return "It can wait until you look."


def from_relay(bot_id: str, fid: int | None, titles: list[str], decision: dict, sent: bool) -> None:
    ""


    buzz = {"buzz": bool(decision.get("buzz") and sent), "why": _why(decision, sent)}
    for n, title in enumerate(titles):
        try:
            add(title, bot_id if bot_id in config.BOTS_BY_ID else "default", buzz=buzz,
                came_in=[fid] if fid else [], source=f"feed:{fid}:{n}" if fid else None)
        except TodoError:
            log.warning("a [TODO] from %s was refused", bot_id, exc_info=True)
    ids = _unclaimed.pop(bot_id, [])
    if not ids:
        return
    with store._lock:
        c = store.db()
        for line_id in ids:
            row = c.execute("SELECT * FROM todo_lines WHERE id=? AND buzz IS NULL", (line_id,)).fetchone()
            if not row:
                continue
            came = json.loads(row["came_in"] or "[]") + ([fid] if fid else [])
            c.execute("UPDATE todo_lines SET buzz=?, came_in=? WHERE id=?", (json.dumps(buzz), json.dumps(came), line_id))
            if buzz["buzz"] and row["status"] == "open":
                _place(c, line_id, False, True)
                if not row["do_on"]:
                    c.execute("UPDATE todo_lines SET do_on=? WHERE id=?", (_today_date().isoformat(), line_id))
        c.commit()
    _bump()




def tool(bot_id: str, args: dict, internal: bool, card: bool = False) -> dict:
    ""


    action = str(args.get("action") or "read").strip().lower()
    if action == "read":
        shown = needs_you_lines(bot_id)
        return {"text": describe(bot_id) + (f"\n\n{shown}" if shown else "")}
    if action == "add":
        same = already_shown(bot_id, str(args.get("title") or ""), args.get("about"))
        if same is not None:
            return {"text": f"That is already on Needs You as [{same['id']}], so I didn't add a second row. "
                            "Answer about it from that row."}
        if args.get("about"):
            raise TodoError("No open Needs You row has that id. Read the list for the current ids.")
        line = add(args.get("title"), bot_id, owner=args.get("owner") if bot_id == "default" else None,
                   note=args.get("note"), due=args.get("due"), facts=args.get("facts"), links=args.get("links"),
                   project=args.get("project"), when=args.get("when"))
        if internal:
            _unclaimed.setdefault(bot_id, []).append(line["id"])
        return {"text": f"Added [{line['id']}] {line['title']}.", "line": line}
    if action == "update":

        item_id = f"todo:{str(args.get('line') or '').strip()}"
        was = None
        if card:
            from . import donecard
            try:
                was = donecard.row_now(item_id)
            except Exception:
                log.warning("could not read the row before a change", exc_info=True)
        line = update(args.get("line"), bot_id, **{k: args.get(k) for k in EDITABLE if k != "reason"})
        if was:
            from . import donecard
            donecard.changed(bot_id, item_id, was, card)
        return {"text": f"Changed [{line['id']}] {line['title']}."
                        + (CARD_NOTE.format(first=who.first()) if was else ""), "line": line}
    if action == "finish":
        return finish_for(bot_id, args.get("line"), card)
    if action == "order":

        return {"text": "The list orders itself by date now, so there is nothing to order. " + describe(bot_id)}
    raise TodoError("Use read, add, update, finish or order.")






CARD_NOTE = (" A card under your reply shows {first} the change and what is left today, so answer in one short line "
             "and don't repeat the card.")


def finish_for(bot_id: str, given, card: bool = False) -> dict:
    ""

    import secrets as _secrets

    from . import autofinish, donecard, remindermirror, work, workstate
    given = str(given or "").strip()
    with store._lock:
        c = store.db()
        line = c.execute("SELECT * FROM todo_lines WHERE id=?", (given,)).fetchone()
        if line:
            _owned(c, given, bot_id)
    item_id = f"todo:{given}" if line else given
    work.rebuild()
    row = next((r for r in work._sources() if r["id"] == item_id and r.get("section") == "needs_you"
                and workstate.visible(r)), None)
    if row is None and not line:
        raise TodoError("No open line or Needs You row has that id. Read the list for the current ids.")
    if row is not None and not line:
        if not donecard.is_todo(row):
            raise TodoError("That one waits on its own card. It is answered or reviewed there, not checked off here.")
        if bot_id != "default" and row.get("bot_id") != bot_id:
            raise TodoError("That row is another Zip's. Check off only your own rows.")
    title = (row or {}).get("ask") or (row or {}).get("short_title") or (row or {}).get("title") or line["title"]
    if not autofinish.finish(item_id, donecard.ZIP_WHY, key=f"zip-{_secrets.token_hex(4)}"):
        if not line:
            return {"text": f"[{item_id}] {title} is already checked off."}
        finish(given, bot_id)
        return {"text": f"Checked off [{given}] {title}."}
    if row is not None and row.get("kind") == "reminder":
        remindermirror.complete_soon(item_id)
    donecard.told(bot_id, [item_id], card)
    return {"text": f"Checked off [{given}] {title}. It is in Done Today, where {who.first()} can put it back."
                    + (CARD_NOTE.format(first=who.first()) if card else "")}


def _set_meta(**changes) -> None:
    store.set_setting(SETTING, {**_meta(), **changes})


def _day() -> str:
    return datetime.now(home.TZ).date().isoformat()





GUIDE = (
    "{first}'s to-do list sits at the top of the app: one line for each of {first}'s own tasks, such as school, work "
    "projects, errands and emails {first} owes a reply. If this message gives {first} a task like that, also write "
    "one line of its own: [TODO] and that thing in two to six words, starting with a verb, such as "
    "\"[TODO] Sign the field trip form\". An okay, a sign-in or a decision about your own work gets a card in "
    "Needs You and no [TODO]. News that needs nothing from {first} gets no [TODO]."
)


def guide() -> str:
    return GUIDE.format(first=who.first())




RANK_GUIDE = (
    "The list is grouped by when: Today, Tomorrow, This Week and Later. Put each line where it belongs with "
    "app_todo_list update and when (today, tomorrow, week or later); leftovers from yesterday are under Today already. "
    "Then order the list the way a great assistant would for {first}'s day: anything overdue first, then things with a "
    "time today, then results {first} asked for that are ready, then asks from Zips, then news. Move yesterday's "
    "leftovers up when they still matter. Keep {first}'s own order where nothing is more urgent. Call app_todo_list "
    "with action order, the ids top to bottom (you can leave out the ones whose place doesn't matter), and reason: "
    "why the first line is first, in under ten words that name the fact (\"Overdue since last night. Do it before the "
    "quiz.\", \"The first post goes out at 11:00 AM.\"). Finish any line that is already done. Then reply with only "
    "[SILENT]."
)


def morning_due() -> bool:
    ""

    if not RESORT:
        return False
    from . import watcher
    if watcher._night() or _meta()["sorted_on"] == _day():
        return False
    with store._lock:
        return bool(store.db().execute("SELECT 1 FROM todo_lines WHERE status='open' LIMIT 1").fetchone())


async def resort() -> None:
    ""


    from . import turns
    today = _day()
    _set_meta(sorted_on=today)
    with store._lock:
        c = store.db()
        c.execute("UPDATE todo_lines SET reason=NULL WHERE reason=?", (MOVED_REASON,))
        c.commit()
    first = who.first()
    note = (f"[The morning re-sort from the hub, not a message from {first}]\n" + describe("default", limit=60)
            + "\n\n" + RANK_GUIDE.format(first=first))
    try:
        await turns.run_internal("default", note)
    except Exception:
        log.warning("Zero could not re-sort the list", exc_info=True)
    if _meta().get("ordered_on") != today:
        sort_plainly()


def sort_plainly() -> None:
    ""
    now = datetime.now(home.TZ)
    with store._lock:
        c = store.db()
        rows = c.execute("SELECT id, due FROM todo_lines WHERE status='open' ORDER BY pos, added_at").fetchall()
        tone = {r["id"]: _due_words(r["due"], now)[1] for r in rows}
        rank = {"danger": 0, "amber": 1}
        ids = sorted((r["id"] for r in rows), key=lambda i: rank.get(tone[i], 2))
        _write_order(c, ids)
        if ids and tone[ids[0]] in rank:
            c.execute("UPDATE todo_lines SET reason=? WHERE id=?",
                      ("It is overdue." if tone[ids[0]] == "danger" else "It is due today.", ids[0]))
        c.commit()
    _bump()
