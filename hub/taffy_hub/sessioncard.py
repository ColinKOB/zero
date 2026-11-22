""






























from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import sqlite3
import time
import uuid

from . import board, config, consent, doing, home, lineage, made, store, who, work

log = logging.getLogger("taffy.sessioncard")

MAX_ROWS = 20
TITLE_MAX = 60

SHORT_MAX = 44
NOTE_MAX = 80
MADE_ID = re.compile(r"made_[0-9a-f]{24}")

STATUSES = ("done", "needs_you", "working", "planned")

AUTO_WINDOW_SEC = 4 * 3600

AUTO_TITLE = "Today"
NAMING = False
NAME_EVERY_SEC = 15
NAME_TIMEOUT_SEC = 12
_tried: set[str] = set()


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS session_cards (
            id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, state TEXT NOT NULL,
            revision INTEGER NOT NULL, title TEXT NOT NULL, rows TEXT NOT NULL,
            started_on TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            status_hash TEXT, changed_at REAL NOT NULL, finished_on TEXT, archived_on TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS session_cards_active ON session_cards(bot_id) WHERE state='active';
    """)

    if "offered" not in {r[1] for r in c.execute("PRAGMA table_info(session_cards)")}:
        c.execute("ALTER TABLE session_cards ADD COLUMN offered TEXT")


class CardError(Exception):
    def __init__(self, message: str, card: dict | None = None):
        super().__init__(message)
        self.card = card


def _today() -> str:
    return datetime.datetime.now(home.zone()).date().isoformat()


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_rows(raw, bot_id: str) -> list[dict]:
    ""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ROWS:
        raise CardError(f"Give between 1 and {MAX_ROWS} lines.")
    rows, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise CardError("Each line needs a title.")
        title = _text(item.get("title"), TITLE_MAX)
        if not title:
            raise CardError("Each line needs a title.")
        ref = {}
        if item.get("task"):
            ref["task"] = _text(item["task"], 64)
        if item.get("approval") not in (None, ""):
            try:
                ref["approval"] = int(item["approval"])
            except (TypeError, ValueError):
                raise CardError(f"\"{title}\": approval is the request's number.") from None
        if item.get("made"):
            ref["made"] = _text(item["made"], 64)
        owner = _text(item.get("owner"), 40)
        if owner:
            if ref.get("task") or ref.get("approval"):
                raise CardError(f"\"{title}\": leave out owner; the task or request already says whose it is.")
            owner = _owner_id(owner)
            if owner is None:
                raise CardError(f"\"{title}\": owner is \"you\" or a teammate's name.")
        key = _ref_key(ref) or ("title", title.casefold())
        row_id = _row_id(bot_id, key)
        if row_id in seen:
            raise CardError(f"\"{title}\" is on the card twice.")
        seen.add(row_id)
        rows.append({"id": row_id, "title": title, "ref": ref, "owner": owner or None,
                     "note": _text(item.get("note"), NOTE_MAX)})
    return rows


def _ref_key(ref: dict) -> tuple | None:
    for kind in ("task", "approval", "made"):
        if kind in ref:
            return (kind, ref[kind])
    return None


def _row_id(bot_id: str, key: tuple) -> str:
    return "r_" + work._revision([bot_id, *key])[:16]


def _owner_id(name: str) -> str | None:
    if name.casefold() in ("you", "me", "the person", who.first().casefold()):
        return "you"
    for b in config.BOTS:
        if name.casefold() in (b.id.casefold(), b.name.casefold()):
            return b.id
    return None


def _tasks(ids: set[str]) -> dict[str, dict]:
    ""

    if not ids:
        return {}
    with board._conn() as c:
        q = f"SELECT id, status, assignee, completed_at FROM tasks WHERE id IN ({','.join('?' * len(ids))})"
        return {r["id"]: dict(r) for r in c.execute(q, sorted(ids))}


def _status_of(task: dict | None) -> str | None:
    if task is None:
        return None
    status = "blocked" if task["status"] == "triage" else task["status"]
    if status in board.FINISHED:
        return "done"
    if status in board.NEEDS_YOU:
        return "needs_you"
    if status in board.ACTIVE:
        return "working"
    return None


def _view_row(row: dict, bot_id: str, tasks: dict, pending: dict) -> dict | None:
    ""
    ref = row["ref"]
    task = tasks.get(ref.get("task", ""))
    item = made.get(ref["made"]) if ref.get("made") else None
    approval = pending.get(ref.get("approval", -1))
    owner = row["owner"] or bot_id
    if task and task.get("assignee") in config.BOTS_BY_ID:
        owner = task["assignee"]
    elif approval:
        owner = approval["bot_id"]
    elif item:
        owner = item["bot_id"]
    status, link = "planned", None
    if approval:
        status, link = "needs_you", {"kind": "approval", "request_id": approval["id"]}
    elif ref.get("task"):
        status = _status_of(task) or "planned"
        link = {"kind": "task", "task_id": ref["task"]} if task else None
    elif item:
        status = "done"
    elif "approval" in ref and not ref.get("made"):
        return None
    if item and item.get("availability") == "available" and status != "needs_you":
        link = {"kind": "made", "item_id": item["id"], "title": item["title"], "item_kind": item["kind"],
                "open": _opener(item)}
    if owner not in config.BOTS_BY_ID and owner != "you":
        owner = bot_id
    out = {"id": row["id"], "title": row["title"], "note": row["note"], "status": status, "owner": owner, "link": link}
    if status == "working" and (now := doing.written(ref.get("task", ""))):
        out["doing"] = now
    return out


def _opener(item: dict) -> dict | None:
    ""
    action = item.get("action") or {}
    if action.get("kind") == "result":
        feed = store.db().execute("SELECT body FROM feed WHERE id=?", (action["feed_id"],)).fetchone()
        name = str(work._json(feed["body"] if feed else None).get("name") or "") or item["title"]
        return {"type": "result", "media": action["media"], "name": name}
    if action.get("kind") == "file":
        return {"type": "file", "bot_id": action["bot_id"], "file_id": action["file_id"],
                "name": action["file_id"].rsplit("/", 1)[-1]}
    return None


def _derive(card: sqlite3.Row, tasks: dict, pending: dict) -> list[dict]:
    rows = [_view_row(r, card["bot_id"], tasks, pending) for r in json.loads(card["rows"])]
    return [r for r in rows if r is not None]


def _public(card: sqlite3.Row, rows: list[dict]) -> dict:
    title = "" if card["title"] == AUTO_TITLE else card["title"]
    return {"id": card["id"], "bot_id": card["bot_id"], "title": title, "revision": card["revision"],
            "started_on": card["started_on"], "updated_at": max(card["updated_at"], card["changed_at"]),
            "state": card["state"], "archived_on": card["archived_on"], "rows": rows,
            "counts": {s: sum(r["status"] == s for r in rows) for s in STATUSES}}


def _settle(c: sqlite3.Connection, card: sqlite3.Row, rows: list[dict], today: str) -> sqlite3.Row:
    ""
    digest = work._revision(rows)
    finished = all(r["status"] == "done" for r in rows)
    finished_on = (card["finished_on"] or today) if finished else None
    if card["status_hash"] != digest or card["finished_on"] != finished_on:
        changed = time.time() if card["status_hash"] not in (None, digest) else card["changed_at"]
        c.execute("UPDATE session_cards SET status_hash=?, changed_at=?, finished_on=? WHERE id=?",
                  (digest, changed, finished_on, card["id"]))
    if finished_on:
        c.execute("UPDATE session_cards SET state='archived', archived_on=? WHERE id=?", (finished_on, card["id"]))
    return c.execute("SELECT * FROM session_cards WHERE id=?", (card["id"],)).fetchone()


def _pending() -> dict[int, dict]:
    return {r["id"]: r for r in consent.pending() if r["bot_id"] in config.BOTS_BY_ID}



_BREAKS = (". ", "; ", ", ", " (", " - ", " and ", " while ", " with ", " so ", " for ", " to ", " from ", " using ",
           " via ", " after ", " before ", " because ", " by ", " on ", " in ", " at ", " under ", " over ", " about ",
           " into ", " across ", " than ")
_BREAK = re.compile("(?=" + "|".join(re.escape(b) for b in _BREAKS) + ")")
_DANGLING = {"a", "an", "the", "and", "or", "of", "for", "to", "from", "with", "by", "on", "in", "at", "my",
             "their", "our", "your", "new", "this", "that"}


def _dangles(word: str) -> bool:
    ""
    word = word.casefold().strip(",:;")
    return word in _DANGLING or word.endswith(("'s", "’s"))


def _short(title: str) -> str:
    ""

    title = " ".join(str(title or "").split())
    if len(title) <= SHORT_MAX:
        return title
    cuts = [m.start() for m in _BREAK.finditer(title) if 16 <= m.start() <= SHORT_MAX]
    if cuts:
        head = title[:max(cuts)]
    else:
        words = title[:SHORT_MAX + 1].rsplit(" ", 1)[0].split(" ")
        while len(words) > 2 and _dangles(words[-1]):
            words.pop()
        head = " ".join(words)
    return head.rstrip(" ,:;-") or title[:SHORT_MAX]


NAME_SYSTEM = """You name one line on a person's list of work in progress. Write what the work is in 2 to 6 plain \
everyday words, like "Redo the Shorts voice clone", "Jake meeting prep" or "PodGuard social calendar".

- A whole phrase that reads finished. Never end on a word like and, for, from, the or with.
- Keep the project, course, person or thing the task is about when the task names one.
- No colon, quotes, em dashes, emojis, markdown, tool names, ids or helper names."""

NAME_PROMPT = """Task title: {title}
Task brief: {brief}

Write the line's name."""


def clean_name(text: str | None) -> str | None:
    ""
    t = " ".join(str(text or "").split()).strip().strip("\"'“”`").rstrip(".")
    words = t.split()
    if not t or len(t) > SHORT_MAX or not 2 <= len(words) <= 7 or not t[0].isupper():
        return None
    if re.search(r"[:?!*_#\[\]<>|—–]", t) or _dangles(words[-1]):
        return None
    return t


def _unnamed() -> list[tuple[str, str, dict]]:
    ""
    with store._lock:
        cards = store.db().execute("SELECT id, rows FROM session_cards WHERE state='active'").fetchall()
    lines = [(c["id"], r) for c in cards for r in json.loads(c["rows"])
             if r["ref"].get("task") and not r.get("named") and r["id"] not in _tried]
    if not lines or not config.KANBAN_DB.exists():
        return []
    ids = sorted({r["ref"]["task"] for _, r in lines})
    with board._conn() as k:
        tasks = {t["id"]: dict(t) for t in k.execute(
            f"SELECT id, title, body FROM tasks WHERE id IN ({','.join('?' * len(ids))})", ids)}
    out = []
    for card_id, r in lines:
        task = tasks.get(r["ref"]["task"])
        full = " ".join(str((task or {}).get("title") or "").split())

        if task and len(full) > SHORT_MAX and full.startswith(r["title"]):
            out.append((card_id, r["id"], task))
    return out


def _rename(card_id: str, row_id: str, title: str) -> None:
    with store._lock:
        c = store.db()
        card = c.execute("SELECT rows FROM session_cards WHERE id=?", (card_id,)).fetchone()
        if not card:
            return
        rows = json.loads(card["rows"])
        for r in rows:
            if r["id"] == row_id and not r.get("named"):
                r["title"], r["named"] = title, True
        c.execute("UPDATE session_cards SET rows=? WHERE id=?", (json.dumps(rows), card_id))
        c.commit()


async def name_rows() -> int:
    ""


    from . import shortreply
    by_task: dict[str, list[tuple[str, str]]] = {}
    tasks: dict[str, dict] = {}
    for card_id, row_id, task in await asyncio.to_thread(_unnamed):
        _tried.add(row_id)
        by_task.setdefault(task["id"], []).append((card_id, row_id))
        tasks[task["id"]] = task
    named = 0
    for task_id, lines in by_task.items():
        task = tasks[task_id]
        if shortreply.withheld(*board.task_zips(task)):
            continue
        prompt = NAME_PROMPT.format(title=" ".join(str(task.get("title") or "").split())[:200],
                                    brief=" ".join(str(task.get("body") or "").split())[:500] or "(none)")
        try:
            with shortreply.reading_task(task):
                title = clean_name(await asyncio.wait_for(
                    shortreply.ask_helper(prompt, timeout=NAME_TIMEOUT_SEC, system=NAME_SYSTEM, max_tokens=30),
                    NAME_TIMEOUT_SEC + 3))
        except Exception as e:
            log.info("no short title for task %s (%s)", task_id, type(e).__name__)
            continue
        if title:
            for card_id, row_id in lines:
                await asyncio.to_thread(_rename, card_id, row_id, title)
            named += len(lines)
    return named


async def run() -> None:
    ""
    while True:
        await asyncio.sleep(NAME_EVERY_SEC)
        if NAMING:
            try:
                await name_rows()
            except Exception:
                log.warning("could not name the session card's lines", exc_info=True)


def _chat_records(bots: list[str], since: float) -> tuple[dict[str, list[tuple[float, dict]]], set[str]]:
    ""



    chats = {sid: b for b in bots if (sid := store.session_for(b))}
    found: dict[str, list[tuple[float, dict]]] = {b: [] for b in bots}
    unfinished: set[str] = set()
    if not chats:
        return found, unfinished

    def chat_of(sid):
        return chats.get(sid) or chats.get(lineage.root(None, sid)) if sid else None

    if config.KANBAN_DB.exists():
        with board._conn() as k:
            for r in k.execute("SELECT id, title, assignee, status, created_at, session_id FROM tasks "
                               "WHERE created_at >= ? AND session_id IS NOT NULL", (since,)):
                if not (bot := chat_of(r["session_id"])):
                    continue
                record = (r["created_at"], {"title": r["title"] or r["id"], "ref": {"task": r["id"]}})
                for chat in {bot, r["assignee"]} & found.keys():
                    found[chat].append(record)
                if r["status"] in board.WORKING + board.NEEDS_YOU:
                    unfinished.add(r["id"])
    for r in consent.pending():
        if (r.get("created") or 0) >= since and (bot := chat_of(r.get("session_id"))) and r["bot_id"] == bot:
            title = r.get("title") or r.get("reason") or r.get("site")
            found[bot].append((r["created"], {"title": title, "ref": {"approval": r["id"]}}))
    return found, unfinished


def _offered(card: sqlite3.Row) -> set[tuple]:
    keys = {tuple(k) for k in json.loads(card["offered"] or "[]")}
    return keys | {k for r in json.loads(card["rows"]) if (k := _ref_key(r["ref"]))}


def auto(c: sqlite3.Connection, bots: list[str]) -> None:
    ""



    now = time.time()
    records, unfinished = _chat_records(bots, now - AUTO_WINDOW_SEC)
    for bot, found in records.items():
        if not found:
            continue
        cards = c.execute("SELECT * FROM session_cards WHERE bot_id=?", (bot,)).fetchall()
        seen = set().union(*(_offered(card) for card in cards))
        new, keys = [], set()
        for _, record in sorted(found, key=lambda f: f[0]):
            key = _ref_key(record["ref"])
            if key in seen or key in keys:
                continue
            keys.add(key)
            new.append({"id": _row_id(bot, key), "title": _short(record["title"]), "ref": record["ref"],
                        "owner": None, "note": ""})
        card = next((x for x in cards if x["state"] == "active"), None)
        if card:
            rows = _derive(card, _tasks(_refs([card])), _pending())
            card = _settle(c, card, rows, _today())
            if card["state"] == "archived":
                card = None
        late = []
        if card is None:
            late = [r for r in new if r["ref"].get("task") and r["ref"]["task"] not in unfinished]
            new = [r for r in new if r not in late]
            if not any("task" in r["ref"] for r in new):
                continue
        rows = json.loads(card["rows"]) if card else []
        new = new[:MAX_ROWS - len(rows)]
        if not new:
            continue
        offered = sorted(set().union(*(_offered(x) for x in [card] if x)) | {_ref_key(r["ref"]) for r in new + late})
        if card:
            c.execute("UPDATE session_cards SET rows=?, offered=?, revision=revision+1, updated_at=? WHERE id=?",
                      (json.dumps(rows + new), json.dumps(offered), now, card["id"]))
        else:
            c.execute("INSERT INTO session_cards(id,bot_id,state,revision,title,rows,started_on,created_at,"
                      "updated_at,changed_at,offered) VALUES(?,?,'active',1,?,?,?,?,?,?,?)",
                      ("session_" + uuid.uuid4().hex, bot, AUTO_TITLE, json.dumps(new), _today(), now, now, now,
                       json.dumps(offered)))


def _refs(cards: list[sqlite3.Row]) -> set[str]:
    return {r["ref"]["task"] for card in cards for r in json.loads(card["rows"]) if r["ref"].get("task")}


def active() -> dict[str, dict]:
    ""
    with store._lock:
        c = store.db()
        auto(c, list(config.BOTS_BY_ID))
        cards = [r for r in c.execute("SELECT * FROM session_cards WHERE state='active'")
                 if r["bot_id"] in config.BOTS_BY_ID]
        if not cards:
            c.commit()
            return {}
        tasks, pending, today = _tasks(_refs(cards)), _pending(), _today()
        out = {}
        for card in cards:
            rows = _derive(card, tasks, pending)
            card = _settle(c, card, rows, today)
            if card["state"] == "active":
                out[card["bot_id"]] = _public(card, rows)
        c.commit()
        return out


def current(bot_id: str) -> dict | None:
    with store._lock:
        c = store.db()
        auto(c, [bot_id])
        card = c.execute("SELECT * FROM session_cards WHERE bot_id=? AND state='active'", (bot_id,)).fetchone()
        if not card:
            c.commit()
            return None
        rows = _derive(card, _tasks(_refs([card])), _pending())
        card = _settle(c, card, rows, _today())
        c.commit()
        return _public(card, rows) if card["state"] == "active" else None


def _check_refs(rows: list[dict]) -> None:
    ""
    tasks = _tasks({r["ref"]["task"] for r in rows if r["ref"].get("task")})
    for r in rows:
        ref = r["ref"]
        if ref.get("task") and ref["task"] not in tasks:
            raise CardError(f"\"{r['title']}\": there is no board task {ref['task']}.")
        if "approval" in ref:
            request = consent.get_request(ref["approval"])
            if not request or request.get("bot_id") not in config.BOTS_BY_ID:
                raise CardError(f"\"{r['title']}\": there is no request {ref['approval']}.")


        if ref.get("made") and not MADE_ID.fullmatch(ref["made"]):
            raise CardError(f"\"{r['title']}\": made is the id share_result gave, like made_1a2b3c.")


def save(bot_id: str, title, raw_rows, expected_revision) -> dict:
    ""

    title = _text(title, TITLE_MAX)
    if not title:
        raise CardError("Give the card a short title, like This Week.")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise CardError("expected_revision is the card's revision, or 0 for a new card.")
    rows = _parse_rows(raw_rows, bot_id)
    try:
        _check_refs(rows)
    except sqlite3.Error:
        raise CardError("I couldn't read the board just now. Try again in a moment.") from None
    with store._lock:
        existing = current(bot_id)
        c, now = store.db(), time.time()
        if existing:
            stored = c.execute("SELECT * FROM session_cards WHERE id=?", (existing["id"],)).fetchone()
            if expected_revision == existing["revision"] - 1 and stored["title"] == title and \
                    json.loads(stored["rows"]) == rows:
                return existing
            if expected_revision != existing["revision"]:
                raise CardError("The card changed since you read it. Here it is now; update this revision.", existing)
            offered = sorted(_offered(stored) | {k for r in rows if (k := _ref_key(r["ref"]))})
            c.execute("UPDATE session_cards SET title=?, rows=?, offered=?, revision=revision+1, updated_at=? "
                      "WHERE id=?", (title, json.dumps(rows), json.dumps(offered), now, existing["id"]))
        else:
            if expected_revision != 0:
                raise CardError("This chat has no card now. Use expected_revision 0 to start one.")
            offered = sorted({k for r in rows if (k := _ref_key(r["ref"]))})
            c.execute("INSERT INTO session_cards(id,bot_id,state,revision,title,rows,started_on,created_at,updated_at,"
                      "changed_at,offered) VALUES(?,?,'active',1,?,?,?,?,?,?,?)",
                      ("session_" + uuid.uuid4().hex, bot_id, title, json.dumps(rows), _today(), now, now, now,
                       json.dumps(offered)))
        c.commit()
        return current(bot_id)


def describe(card: dict | None) -> str:
    ""
    if not card:
        return "This chat has no session card now."
    person = who.first()
    words = {"done": "done", "needs_you": f"waiting on {person}", "planned": "planned"}
    lines = [f"{card['title'] or 'Your session card'} (revision {card['revision']}):"]
    for r in card["rows"]:
        owner = person if r["owner"] == "you" else getattr(config.BOTS_BY_ID.get(r["owner"]), "name", r["owner"])
        state = f"{owner} is on it" if r["status"] == "working" else words[r["status"]]
        lines.append(f"- {r['title']}: {state}")
    return "\n".join(lines)
