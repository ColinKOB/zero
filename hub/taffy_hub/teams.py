""













import asyncio
from collections import Counter
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from . import about, config, hermes, home, receipts, store, taste, who

log = logging.getLogger("taffy.teams")




_BACKGROUND: set = set()


def spawn(coro) -> "asyncio.Task":
    task = asyncio.get_running_loop().create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    kind TEXT NOT NULL,
    lead TEXT NOT NULL,
    state TEXT NOT NULL,              -- setting_up | active | paused | deleting | deleted | merged
    budget_min INTEGER NOT NULL,      -- wall-clock minutes per member per day
    paused_reason TEXT,               -- you | budget | plan_limit, while paused
    resume_at REAL,
    site_id TEXT,
    site_url TEXT,
    preview_url TEXT,                 -- the last draft deploy from Preview Site
    signups INTEGER,
    signups_at REAL,
    summary_text TEXT,
    summary_at REAL,
    proposal_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members (
    team_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    template TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    brief TEXT NOT NULL DEFAULT '',   -- the lead's facts for this member (propose), in its job
    hello TEXT NOT NULL DEFAULT '',
    ready INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team_id, bot_id)
);
CREATE TABLE IF NOT EXISTS team_proposals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    kind TEXT NOT NULL,
    members TEXT NOT NULL,            -- JSON [{template, name, role, brief, hello, color, shape}]
    milestones TEXT NOT NULL,         -- JSON [{title, owner (member name), done_when}]
    state TEXT NOT NULL,              -- open | starting | started | changed
    team_id TEXT,
    feed_id INTEGER,
    created TEXT NOT NULL DEFAULT '[]',   -- JSON member names made so far
    note TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    title TEXT NOT NULL,
    owner TEXT NOT NULL,              -- bot id
    state TEXT NOT NULL,              -- todo | doing | blocked | ready (finished, its card waits on the person) | done
    result TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    ord INTEGER NOT NULL,
    done_when TEXT NOT NULL DEFAULT '',
    turns INTEGER NOT NULL DEFAULT 0,
    check_json TEXT,                  -- the taste kit check sent with state done (taste.validate)
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_needs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    from_bot TEXT NOT NULL,
    kind TEXT NOT NULL,               -- approve | decide | do
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '[]',
    items TEXT NOT NULL DEFAULT '[]',
    request_ids TEXT NOT NULL DEFAULT '[]',   -- approve: the consent request ids this decides
    milestone_id INTEGER,
    state TEXT NOT NULL,              -- open | approved | declined | answered
    answer TEXT,
    created_at REAL NOT NULL,
    answered_at REAL,
    about TEXT,                       -- the item to approve: {title, preview, path in the team folder} (about.py)
    due_at REAL,                      -- when what it holds up is due (a post's time, a launch day); raised again before
    raised_at REAL,                   -- when that due date raised it again (DUE_AHEAD_SEC)
    woke_at REAL                      -- when an approved post's time woke its Zip to post it (Scheduler._posts_due)
);
CREATE TABLE IF NOT EXISTS team_wakes (    -- lines for a member's next work brief, until a turn carries them
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note',     -- note (an answer, a notice from the hub) | instruction (the person's own words in the group)
    text TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_turns (    -- the minute ledger every budget is read from
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    kind TEXT NOT NULL,               -- work | checkin | reply | summary
    started REAL NOT NULL,
    ended REAL,
    outcome TEXT
);
"""

TABLES = ("teams", "team_members", "team_proposals", "team_milestones", "team_needs", "team_turns", "team_wakes")


MOVES = {
    "team": {("setting_up", "active"), ("active", "paused"), ("paused", "active"),
             ("setting_up", "deleting"), ("active", "deleting"), ("paused", "deleting"),
             ("deleting", "deleted"), ("deleting", "paused")},
    "milestone": {("todo", "doing"), ("todo", "blocked"), ("todo", "done"), ("doing", "blocked"), ("doing", "done"),
                  ("blocked", "doing"), ("blocked", "done"), ("blocked", "todo"),
                  ("todo", "ready"), ("doing", "ready"), ("blocked", "ready"),
                  ("ready", "doing"), ("ready", "done"), ("ready", "blocked"), ("ready", "todo")},
    "need": {("open", "approved"), ("open", "declined"), ("open", "answered")},
    "proposal": {("open", "starting"), ("open", "changed"), ("starting", "started"), ("starting", "open")},
}
STATE_TABLES = {"team": ("teams", "id"), "milestone": ("team_milestones", "id"), "need": ("team_needs", "id"),
                "proposal": ("team_proposals", "id")}

MAX_RUNNING = 2
TURN_CAP_SEC = 10 * 60






BUDGET_MIN_DEFAULT = 4 * 60
MAX_TURNS_PER_MILESTONE = 8




STALL_SEC = 24 * 3600
STALL_ASK_SEC = 12 * 3600
STALL_SCAN_SEC = 300
STALL_SETTING = "team_stalls"



DUE_AHEAD_SEC = 48 * 3600
TICK_SEC = 15
WORK_COOLDOWN_SEC = 120
INSTRUCTIONS_KEPT = 5
PLAN_LIMIT_RESUME_SEC = 3600
READY_WAIT_SEC = 180
START_TURN_WAIT_SEC = 20 * 60
NAME_MAX, GOAL_MAX = 40, 200



BRIEF_MIN, BRIEF_MAX, HELLO_MAX = 250, 2000, 200
TITLE_MAX, DETAIL_MAX = 80, 600


def _title(text) -> str:
    ""

    text = " ".join(receipts.without_refs(str(text or "")).split())
    if len(text) <= TITLE_MAX:
        return text
    return text[:TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "\u2026"

PULL_REQUEST = re.compile(r"^https://github\.com/(?P<repo>[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})/pull/(?P<number>[0-9]{1,9})/?$")


KEPT_WHOLE = ("instruction", "answer", "stall")
MESSAGE_MAX = 4000 + 10 * 400
THREAD_KINDS = ("hello", "message", "checkin", "reply", "summary", "work", "need", "milestone")
NEED_KINDS = ("approve", "decide", "do")


POSTS_MAX = 40
POST_TEXT_MAX = 2200
_POSTS = re.compile(r"\b(posts|tweets|captions|reels|carousels)\b", re.I)



POST_SCAN_SEC = 60
SIGN_IN_URLS = {"x.com": "https://x.com/i/flow/login", "instagram.com": "https://www.instagram.com/accounts/login/"}
ONE_PER_POST = ("Each post gets its own card, so {first} can say yes to one and no to another. Pass posts, one entry "
                "per post with its text, where it goes and when, instead of one request for all of them.")

TEMPLATES = config.SHARED_DIR / "team-templates.json"
ZIP_TEMPLATES = config.SHARED_DIR / "zip-templates.json"

START_WORDS = 'Start the team "{name}" (proposal {pid}).'
CHANGE_WORDS = 'About the "{name}" team you proposed (proposal {pid}): {text}'
CHECKIN_QUESTION = "How is everything going? Each of you: what you did, what is next, and what you need."

_claimed: set[str] = set()
_limit_words = ("plan's usage limit", "usage limit", "rate limit", "rate_limit", "quota", "limit reached",
                "out of extra usage", "exceeded your")


class TeamsError(Exception):
    ""


def _db() -> sqlite3.Connection:
    return store.db()


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)
    for alter in ("ALTER TABLE team_wakes ADD COLUMN kind TEXT NOT NULL DEFAULT 'note'",
                  "ALTER TABLE team_wakes ADD COLUMN tell_id INTEGER",
                  "ALTER TABLE team_wakes ADD COLUMN started INTEGER NOT NULL DEFAULT 0",
                  "ALTER TABLE teams ADD COLUMN preview_url TEXT",
                  "ALTER TABLE team_needs ADD COLUMN about TEXT",
                  "ALTER TABLE team_needs ADD COLUMN due_at REAL",
                  "ALTER TABLE team_needs ADD COLUMN raised_at REAL",
                  "ALTER TABLE team_needs ADD COLUMN woke_at REAL",
                  "ALTER TABLE team_members RENAME COLUMN focus TO brief"):
        try:
            c.execute(alter)
        except sqlite3.OperationalError:
            pass

    c.execute("UPDATE team_wakes SET kind='instruction' WHERE kind='unrouted'")


def reset_for_tests() -> None:
    scheduler.reset()
    _rounds.clear()


def _now() -> float:
    return time.time()


def _midnight(now: float | None = None) -> float:
    d = datetime.fromtimestamp(now or _now(), home.TZ)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _loads(text, default):
    try:
        v = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _move(kind: str, row_id, to: str, c: sqlite3.Connection | None = None, **also) -> dict:
    ""
    table, key = STATE_TABLES[kind]
    c = c or _db()
    with store._lock:
        row = c.execute(f"SELECT * FROM {table} WHERE {key}=?", (row_id,)).fetchone()
        if not row:
            raise TeamsError(f"No such {kind}.")
        frm = row["state"]
        if frm != to and (frm, to) not in MOVES[kind]:
            raise TeamsError(f"A {kind} cannot go from {frm} to {to}.")
        sets, vals = ["state=?"], [to]
        if "updated_at" in row.keys():
            sets.append("updated_at=?")
            vals.append(_now())
        for k, v in also.items():
            sets.append(f"{k}=?")
            vals.append(v)
        c.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE {key}=?", (*vals, row_id))
        c.commit()
        return dict(c.execute(f"SELECT * FROM {table} WHERE {key}=?", (row_id,)).fetchone())




def _repo_shared(name: str) -> Path:
    ""
    return Path(__file__).resolve().parents[2] / "alien" / "shared" / name


def _read_json(path: Path, fallback: Path):
    for p in (path, fallback):
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            continue
    return None


def catalog() -> dict:
    ""

    data = _read_json(TEMPLATES, _repo_shared("team-templates.json")) or {}
    templates = {t["id"]: t for t in data.get("templates", []) if isinstance(t, dict) and t.get("id")}
    zips = _read_json(ZIP_TEMPLATES, _repo_shared("zip-templates.json")) or []
    for t in zips if isinstance(zips, list) else []:
        if isinstance(t, dict) and t.get("id") and t["id"] not in templates:
            templates[t["id"]] = {"id": t["id"], "name": t["name"], "role": t.get("role", ""),
                                 "description": t.get("description", ""), "look": t.get("look") or {},
                                 "job": t.get("job", ""), "browser": False, "reused": True}
    return {"templates": templates, "playbooks": data.get("playbooks") or {}}


def kinds() -> list[str]:
    return list(catalog()["playbooks"])


def _personal(text: str) -> str:
    first = who.first()
    return (text or "").replace("{name}", first).replace("{first_name}", first)




def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:24] or "team"


def _unique_team_id(name: str) -> str:
    base = "t_" + _slug(name).replace("-", "_")
    c = _db()
    tid, n = base, 2
    while c.execute("SELECT 1 FROM teams WHERE id=?", (tid,)).fetchone() or \
            c.execute("SELECT 1 FROM feed WHERE team_id=? LIMIT 1", (tid,)).fetchone():
        tid = f"{base}_{n}"
        n += 1
    return tid


def _taken_names() -> set[str]:
    config.reload_bots()
    names = {b.name.lower() for b in config.BOTS}
    for r in _db().execute("SELECT name FROM team_members"):
        names.add(r["name"].lower())
    return names


def _free_name(name: str, taken: set[str]) -> str:
    out, n = name, 2
    while out.lower() in taken:
        out = f"{name} {n}"
        n += 1
    taken.add(out.lower())
    return out


REPLACED_NOTE = "A newer plan replaced this one."
PROJECT_NOT_TEAM = (
    "Not proposed: a project gets one Zip with modes now, not a team. Make one Zip with team_add_bot once {person} "
    "says yes to it, and write its job with one short section per mode it needs (for example Marketing, Dev and Ops). "
    "That Zip switches modes in one chat and starts board tasks assigned to itself when two things need doing at once.")




PALETTE = ("#F59E0B", "#EC4899", "#10B981", "#14B8A6", "#6366F1", "#EF4444", "#8B5CF6", "#0EA5E9", "#84CC16",
           "#F97316", "#A855F7", "#06B6D4", "#E11D48", "#65A30D")


def _taken_colors() -> set[str]:
    from . import looks
    return {str((looks.for_bot(b.id) or {}).get("color") or b.color).upper() for b in config.BOTS}


def _free_color(want: str, taken: set[str]) -> str:
    ""
    pick = want if want.upper() not in taken else next((c for c in PALETTE if c.upper() not in taken), want)
    taken.add(pick.upper())
    return pick


BRIEF_WANTED = (
    "Each member needs a brief of real facts, at least {min} characters, before the team can be proposed.{thin} "
    "First look at what already exists: {person}'s Mac (mac_start, then mac_run with mdfind -name and ls ~/Projects "
    "~/Desktop ~/Documents for the project's folders and repos), {person}'s mail and Drive, and recall. Then write "
    "each member's brief: the exact paths and accounts it will use, current numbers with their dates, the known gaps, "
    "its teammates and their jobs, and what needs {person}'s yes."
)




BRIEF_LEAD = ("The lead wrote this from what already exists for this project. Where it differs from the job above, "
              "follow this and build on what is there.")


def _text_block(raw, cap: int) -> str:
    ""
    lines = (" ".join(line.split()) for line in str(raw or "").splitlines())
    return "\n".join(line for line in lines if line)[:cap]


def _hello_from(title: str) -> str:
    ""
    title = title.rstrip(" .")
    first = title.split(" ", 1)[0]
    if first[1:] == first[1:].lower():
        title = title[:1].lower() + title[1:]
    return f"I'm starting on {title}."


def propose(lead: str, args: dict) -> dict:
    ""


    cat = catalog()
    kind = str(args.get("kind") or "general").strip().lower()
    if kind not in cat["playbooks"]:
        raise TeamsError(f"Unknown team kind {kind!r}. Kinds: {', '.join(cat['playbooks'])}.")
    book = cat["playbooks"][kind]
    name = " ".join(str(args.get("name") or book.get("name") or "Team").split())[:NAME_MAX]
    goal = " ".join(str(args.get("goal") or "").split())[:GOAL_MAX]
    if len(goal) < 8:
        raise TeamsError("Write the goal as one plain sentence.")
    wanted = [m for m in args.get("members") or [] if isinstance(m, dict)]
    taken = _taken_names()
    colors = _taken_colors()
    members = []
    for m in wanted:
        tid = str(m.get("template") or "").strip().lower()
        t = cat["templates"].get(tid)
        if not t:
            raise TeamsError(f"No team template {tid!r}. Templates: {', '.join(cat['templates'])}.")
        look = t.get("look") or {}
        members.append({"template": tid, "name": _free_name(" ".join(str(m.get("name") or t["name"]).split())[:24], taken),
                        "role": _personal(t.get("role", ""))[:90], "brief": _text_block(m.get("brief"), BRIEF_MAX),
                        "hello": " ".join(str(m.get("hello") or "").split())[:HELLO_MAX],
                        "color": _free_color(str(look.get("color") or "#64748B"), colors),
                        "shape": str(look.get("shape") or "pebble")})
    thin = [m["name"] for m in members if len(m["brief"]) < BRIEF_MIN]
    if not members or thin:
        raise TeamsError(BRIEF_WANTED.format(min=BRIEF_MIN, person=who.first(),
                                             thin=f" Too short now: {', '.join(thin)}." if thin else ""))
    from_book = not args.get("milestones")
    milestones = []
    for ms in args.get("milestones") or book.get("milestones") or []:
        ms = ms if isinstance(ms, dict) else {"title": str(ms)}
        title = _title(ms.get("title"))
        if not title:
            continue

        key = str(ms.get("owner") or "").strip().lower()
        owner = next((m["name"] for m in members if m["name"].lower() == key), None) or \
            next((m["name"] for m in members if m["template"] == key), None)
        if owner is None and from_book:
            continue
        milestones.append({"title": title, "owner": owner or members[0]["name"],
                           "done_when": " ".join(str(ms.get("done_when") or "").split())[:300]})
    idle = [m["name"] for m in members if not any(ms["owner"] == m["name"] for ms in milestones)]
    if idle:
        raise TeamsError(f"Give {', '.join(idle)} a first milestone: work it can start now with what it can reach, "
                         f"such as {who.possessive()} Mac, mail or the live site.")
    for m in members:
        m["hello"] = m["hello"] or _hello_from(next(ms["title"] for ms in milestones if ms["owner"] == m["name"]))



    for (old_pid,) in _db().execute("SELECT id FROM team_proposals WHERE state='open' AND team_id IS NULL").fetchall():
        _move("proposal", old_pid, "changed")
        _set_proposal(old_pid, note=REPLACED_NOTE)
    pid = "p_" + secrets.token_hex(4)
    body = {"proposal_id": pid, "name": name, "goal": goal, "kind": kind,
            "members": [{"name": m["name"], "role": m["role"], "color": m["color"], "shape": m["shape"]} for m in members],
            "milestones": [m["title"] for m in milestones], "state": "open", "created": [], "team_id": None, "note": None}
    fid = store.add_feed("team_proposal", lead, None, f"Team proposal: {name}", json.dumps(body),
                         written_by="zip", written_for="person")
    with store._lock:
        _db().execute("INSERT INTO team_proposals (id, name, goal, kind, members, milestones, state, feed_id, created_at) "
                      "VALUES (?,?,?,?,?,?,'open',?,?)",
                      (pid, name, goal, kind, json.dumps(members), json.dumps(milestones), fid, _now()))
        _db().commit()
    return {"proposal_id": pid, "name": name, "members": [m["name"] for m in members],
            "milestones": [m["title"] for m in milestones]}


def proposal(pid: str) -> dict | None:
    r = _db().execute("SELECT * FROM team_proposals WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    p = dict(r)
    p["members"] = _loads(p["members"], [])
    p["milestones"] = _loads(p["milestones"], [])
    p["created"] = _loads(p["created"], [])
    return p


def _card(p: dict) -> None:
    ""
    if not p.get("feed_id"):
        return
    body = {"proposal_id": p["id"], "name": p["name"], "goal": p["goal"], "kind": p["kind"],
            "members": [{"name": m["name"], "role": m["role"], "color": m["color"], "shape": m["shape"]} for m in p["members"]],
            "milestones": [m["title"] for m in p["milestones"]], "state": p["state"], "created": p["created"],
            "team_id": p.get("team_id"), "note": p.get("note")}
    store.update_feed(p["feed_id"], f"Team proposal: {p['name']}", json.dumps(body))


def _set_proposal(pid: str, **cols) -> dict:
    sets = ", ".join(f"{k}=?" for k in cols)
    with store._lock:
        _db().execute(f"UPDATE team_proposals SET {sets} WHERE id=?", (*cols.values(), pid))
        _db().commit()
    p = proposal(pid)
    _card(p)
    return p




def _bot_ids_taken() -> set[str]:
    config.reload_bots()
    ids = set(config.BOTS_BY_ID) | {"default", "taffy", "zero", "hub", "chip", "admin", "root", "system", "all", "team"}
    for r in _db().execute("SELECT bot_id FROM team_members"):
        ids.add(r["bot_id"])
    profiles = config.HERMES_HOME / "profiles"
    if profiles.is_dir():
        ids |= {p.name for p in profiles.iterdir()}
    return ids


def _member_id(team_id: str, template: str, taken: set[str]) -> str:
    head = re.sub(r"[^a-z0-9]", "", team_id.removeprefix("t_"))[:8]
    base = (head + re.sub(r"[^a-z0-9]", "", template))[:20]
    if not base or not base[0].isalpha():
        base = ("t" + base)[:20]
    out, n = base, 2
    while out in taken:
        out = (base[:18] + str(n))[:20]
        n += 1
    taken.add(out)
    return out


def start(pid: str) -> dict:
    ""


    from . import turns
    p = proposal(pid)
    if not p:
        raise TeamsError("That proposal is gone.")
    if p["state"] == "started":
        raise TeamsError("That team already started.")
    if p["state"] == "changed":
        raise TeamsError("That proposal was changed. Start the newer one.")
    if p["state"] == "open":
        _move("proposal", pid, "starting")
    if p["state"] == "open" and not p.get("team_id"):
        tid = _unique_team_id(p["name"])
        taken = _bot_ids_taken()
        now = _now()
        c = _db()
        with store._lock:
            c.execute("INSERT INTO teams (id, name, goal, kind, lead, state, budget_min, proposal_id, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,'setting_up',?,?,?,?)",
                      (tid, p["name"], p["goal"], p["kind"], "default", BUDGET_MIN_DEFAULT, pid, now, now))
            ids = {}
            for m in p["members"]:
                bid = _member_id(tid, m["template"], taken)
                ids[m["name"]] = bid
                ids.setdefault(m["template"], bid)
                c.execute("INSERT INTO team_members (team_id, bot_id, template, name, role, brief, hello) VALUES (?,?,?,?,?,?,?)",
                          (tid, bid, m["template"], m["name"], m["role"], m.get("brief") or "", m.get("hello") or ""))
            for i, ms in enumerate(p["milestones"]):
                c.execute("INSERT INTO team_milestones (team_id, title, owner, state, ord, done_when, updated_at) "
                          "VALUES (?,?,?,'todo',?,?,?)",
                          (tid, ms["title"], ids.get(ms["owner"]) or next(iter(ids.values())), i, ms.get("done_when") or "", now))
            c.commit()
        (config.WORK_DIR / "teams" / tid).mkdir(parents=True, exist_ok=True)
        p = _set_proposal(pid, team_id=tid, note=None)
    turn = turns.send("default", START_WORDS.format(name=p["name"], pid=pid))
    if hasattr(turn, "follow"):
        try:
            spawn(_watch_start(pid, turn))
        except RuntimeError:
            pass
    return p


async def _watch_start(pid: str, turn) -> None:
    ""


    error = None

    async def follow():
        nonlocal error
        async for ev in turn.follow():
            if ev and ev.get("type") == "error":
                error = ev.get("text") or error
    try:
        await asyncio.wait_for(follow(), START_TURN_WAIT_SEC)
    except TimeoutError:
        error = error or "Zero's turn is taking too long."
    p = proposal(pid)
    if p and p["state"] == "starting" and pid not in _claimed:
        start_failed(pid, error or "Zero's turn ended before the team was set up.")


def claim_start(pid: str) -> None:
    ""
    _claimed.add(pid)


def change(pid: str, text: str) -> dict:
    from . import turns
    p = proposal(pid)
    if not p:
        raise TeamsError("That proposal is gone.")
    text = " ".join((text or "").split())
    if not text:
        raise TeamsError("Say what to change.")
    if p["state"] == "open":
        p = _move("proposal", pid, "changed")
        _set_proposal(pid, note=None)
    turns.send("default", CHANGE_WORDS.format(name=p["name"], pid=pid, text=text[:2000]))
    return proposal(pid)


def start_plan(pid: str) -> dict:
    ""

    try:
        return _start_plan(pid)
    except TeamsError as e:
        p = proposal(pid)
        if p and p["state"] == "starting":
            start_failed(pid, str(e))
        raise


def _start_plan(pid: str) -> dict:
    config.reload_bots()
    p = proposal(pid)
    if not p or not p.get("team_id"):
        raise TeamsError("There is no team being started for that proposal. Ask the person to tap Start Team.")
    t = get(p["team_id"])
    cat = catalog()["templates"]
    members = []
    for m in _members(p["team_id"]):
        tpl = cat.get(m["template"]) or {}
        job = _personal(tpl.get("job") or "")
        if m["brief"]:
            job += f"\n\n## Your focus on this team\n\n{BRIEF_LEAD}\n\n{m['brief']}"
        members.append({"bot_id": m["bot_id"], "name": m["name"], "template": m["template"], "role": m["role"],
                        "description": _personal(tpl.get("description") or m["role"]), "job": job,
                        "color": next((x.get("color") for x in p["members"] if x.get("name") == m["name"] and x.get("color")),
                                      (tpl.get("look") or {}).get("color") or "#64748B"),
                        "shape": (tpl.get("look") or {}).get("shape") or "pebble",
                        "hello": m["hello"], "exists": bool(m["ready"]) or m["bot_id"] in config.BOTS_BY_ID})
    return {"proposal_id": pid, "team_id": p["team_id"], "name": t["name"], "goal": t["goal"], "members": members}


def member_created(pid: str, bot_id: str) -> dict:
    p = proposal(pid)
    if not p or not p.get("team_id"):
        raise TeamsError("No team is being started for that proposal.")
    m = _db().execute("SELECT * FROM team_members WHERE team_id=? AND bot_id=?", (p["team_id"], bot_id)).fetchone()
    if not m:
        raise TeamsError(f"{bot_id} is not a member of that team.")
    created = p["created"] if m["name"] in p["created"] else p["created"] + [m["name"]]
    return _set_proposal(pid, created=json.dumps(created))


def start_failed(pid: str, note: str) -> dict:
    ""
    p = proposal(pid)
    if not p:
        raise TeamsError("That proposal is gone.")
    _claimed.discard(pid)
    if p["state"] == "starting":
        _move("proposal", pid, "open")
    return _set_proposal(pid, note=" ".join((note or "").split())[:300] or "The team could not be set up.")


async def started(pid: str) -> dict:
    ""

    p = proposal(pid)
    if not p or not p.get("team_id"):
        raise TeamsError("No team is being started for that proposal.")
    if p["state"] == "started":
        return p
    claim_start(pid)
    try:
        return await _bring_online(p)
    except Exception:
        start_failed(pid, "The team could not come online.")
        raise


async def _bring_online(p: dict) -> dict:
    pid, tid = p["id"], p["team_id"]
    config.reload_bots()
    deadline = _now() + READY_WAIT_SEC
    waiting = [m["bot_id"] for m in _members(tid) if not m["ready"]]
    while waiting and _now() < deadline:
        config.reload_bots()
        for bid in list(waiting):
            if bid in config.BOTS_BY_ID and await _served(bid):
                with store._lock:
                    _db().execute("UPDATE team_members SET ready=1 WHERE team_id=? AND bot_id=?", (tid, bid))
                    _db().commit()
                waiting.remove(bid)
        if waiting:
            await asyncio.sleep(2)
    if waiting:
        return start_failed(pid, f"{', '.join(waiting)} did not come online.")
    greeted = {r["body"].get("from") for r in thread(tid) if r["body"].get("kind") == "hello"}
    for m in _members(tid):
        if m["hello"] and m["bot_id"] not in greeted:
            await say(tid, m["bot_id"], "hello", m["hello"])
    _move("team", tid, "active")
    p = _set_proposal(pid, state="started")
    _claimed.discard(pid)
    scheduler.kick()
    return p


async def _served(bot_id: str) -> bool:
    try:
        await hermes.ensure_session(bot_id, lane="work")
        return True
    except Exception:
        return False




def _team_row(tid: str) -> dict:
    r = _db().execute("SELECT * FROM teams WHERE id=? AND state NOT IN ('deleted', 'merged')", (tid,)).fetchone()
    if not r:
        raise TeamsError("That team is gone.")
    return dict(r)


def get(tid: str) -> dict:
    return _team_row(tid)


def _members(tid: str) -> list[dict]:
    return [dict(r) for r in _db().execute("SELECT * FROM team_members WHERE team_id=? ORDER BY rowid", (tid,))]


def member_of(bot_id: str) -> dict | None:
    ""


    r = _db().execute("SELECT t.* FROM teams t JOIN team_members m ON m.team_id = t.id WHERE m.bot_id=? AND "
                      "t.state NOT IN ('deleted', 'merged')", (bot_id,)).fetchone()
    return dict(r) if r else None


def extra_folders(bot_id: str) -> list[Path]:
    ""

    b = config.BOTS_BY_ID.get(bot_id)
    out = [config.WORK_DIR / a for a in (b.merged_from if b else ())]
    t = member_of(bot_id)
    team = t["id"] if t else (b.merged_team if b else None)
    return out + ([config.WORK_DIR / "teams" / team] if team else [])


def milestones(tid: str) -> list[dict]:
    return [{"id": r["id"], "title": r["title"], "owner": r["owner"], "state": r["state"], "result": r["result"],
             "link": r["link"], "order": r["ord"], "check": _loads(r["check_json"], {}) or None}
            for r in _db().execute("SELECT * FROM team_milestones WHERE team_id=? ORDER BY ord, id", (tid,))]


def _need_row(r) -> dict:
    return {"id": r["id"], "team_id": r["team_id"], "from_bot": r["from_bot"], "kind": r["kind"], "title": r["title"],
            "detail": r["detail"], "options": _loads(r["options"], []), "items": _loads(r["items"], []),
            "state": r["state"], "answer": r["answer"], "created_at": r["created_at"], "milestone_id": r["milestone_id"],
            "about": _loads(r["about"], {}) or None, "due_at": r["due_at"]}


def needs(tid: str) -> list[dict]:
    return [_need_row(r) for r in _db().execute("SELECT * FROM team_needs WHERE team_id=? ORDER BY id", (tid,))]


_backfill_lock = threading.Lock()


def _backfill_need_rows(tid: str) -> None:
    ""


    with _backfill_lock:
        _backfill_unlocked(tid)


def _backfill_unlocked(tid: str) -> None:
    have = {(_loads(r["body"], {}) or {}).get("need_id") for r in store.db().execute(
        "SELECT body FROM feed WHERE kind='team' AND team_id=? AND body LIKE '%\"need_id\"%'", (tid,)).fetchall()}
    for n in _db().execute("SELECT id, from_bot, title FROM team_needs WHERE team_id=? AND state='open' ORDER BY id",
                           (tid,)).fetchall():
        if n["id"] not in have:
            post(tid, n["from_bot"], "need", n["title"], need_id=n["id"])


def _reveal_posts(tid: str) -> None:
    ""


    with _backfill_lock:
        cards = [r for r in _db().execute("SELECT * FROM team_needs WHERE team_id=? AND state='open' AND kind='approve'",
                                          (tid,)).fetchall() if _post_of(r) and not waits_for_its_date(r)]
        if not cards:
            return
        shown = {b.get("need_id") for b in (_loads(r["body"], {}) or {} for r in store.db().execute(
            "SELECT body FROM feed WHERE kind='team' AND team_id=? AND body LIKE '%\"shown\"%'", (tid,)).fetchall())}
        for r in cards:
            if r["id"] not in shown:
                post(tid, r["from_bot"], "need", r["title"], need_id=r["id"], shown=True)


def thread(tid: str, after: int = 0, limit: int = 200) -> list[dict]:
    if after == 0:
        _backfill_need_rows(tid)
    _reveal_posts(tid)
    rows = store.db().execute("SELECT * FROM feed WHERE kind='team' AND team_id=? AND id>? ORDER BY id LIMIT ?",
                              (tid, after, limit)).fetchall()
    waiting = _waiting_posts(tid)
    out = [{"id": r["id"], "kind": "team", "created_at": r["created_at"], "team_id": tid, "body": _loads(r["body"], {})}
           for r in rows]
    latest = {m["body"]["need_id"]: m["id"] for m in out if m["body"].get("need_id")}
    return [m for m in out if m["body"].get("need_id") not in waiting and
            latest.get(m["body"].get("need_id"), m["id"]) == m["id"]]


def _last_line(tid: str) -> dict | None:
    r = store.db().execute("SELECT * FROM feed WHERE kind='team' AND team_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if not r:
        return None
    b = _loads(r["body"], {})
    return {"from": b.get("from"), "text": b.get("text"), "at": r["created_at"]}


OLD_BUDGET_MIN = 60


def _budget_min(t) -> int:
    ""
    return BUDGET_MIN_DEFAULT if int(t["budget_min"]) == OLD_BUDGET_MIN else int(t["budget_min"])


def budget(t: dict) -> dict:
    n = max(1, len(_members(t["id"])))
    return {"used_min": int(_used_seconds(t["id"]) // 60), "max_min": _budget_min(t) * n,
            "paused_reason": t.get("paused_reason") if t["state"] == "paused" else None}


def _public_member(m: dict, asks: dict[str, int]) -> dict:
    from . import looks
    look = looks.for_bot(m["bot_id"]) or {}
    b = config.BOTS_BY_ID.get(m["bot_id"])


    return {"id": m["bot_id"], "name": m["name"], "role": m["role"], "color": look.get("color") or (b.color if b else "#64748B"),
            "shape": look.get("shape") or (b.shape if b else "pebble"), "symbol": b.symbol if b else "sparkles",
            "look": look or None, "needs_you": asks.get(m["bot_id"], 0)}


def open_needs_by_bot(tid: str | None = None) -> dict[str, int]:
    ""




    rows = _db().execute("SELECT n.* FROM team_needs n JOIN teams t ON t.id = n.team_id "
                         "WHERE n.state='open' AND (n.kind IN ('decide','do') OR (n.kind='approve' AND n.request_ids='[]')) "
                         "AND t.state NOT IN ('deleting','deleted','merged') "
                         "AND (? IS NULL OR t.id = ?)", (tid, tid)).fetchall()
    return dict(Counter(r["from_bot"] for r in rows if not waits_for_its_date(r)))


def summary(t: dict) -> dict:
    ms = milestones(t["id"])
    asks = open_needs_by_bot(t["id"])
    return {"id": t["id"], "name": t["name"], "goal": t["goal"], "kind": t["kind"], "state": t["state"],
            "members": [_public_member(m, asks) for m in _members(t["id"])],
            "progress": {"done": sum(1 for m in ms if m["state"] == "done"), "total": len(ms)},
            "needs_open": sum(asks.values()), "last": _last_line(t["id"]),
            "site": {"url": t["site_url"], "signups": t.get("signups") or 0} if t.get("site_url") else None,
            "preview_url": t.get("preview_url") or None,
            "has_site": (config.WORK_DIR / "teams" / t["id"] / "site" / "index.html").is_file()}


def detail(tid: str) -> dict:
    t = _team_row(tid)
    out = summary(t)
    waiting = _waiting_posts(tid)
    out.update({"milestones": milestones(tid), "needs": [n for n in needs(tid) if n["id"] not in waiting],
                "summary": {"text": t["summary_text"], "at": t["summary_at"]} if t.get("summary_text") else None,
                "budget": budget(t)})
    return out


def list_teams() -> list[dict]:
    return [summary(dict(r)) for r in _db().execute("SELECT * FROM teams WHERE state NOT IN ('deleted', 'deleting', 'merged') "
                                                     "ORDER BY created_at")]


def all_member_ids() -> set[str]:
    return {r["bot_id"] for r in _db().execute("SELECT m.bot_id FROM team_members m JOIN teams t ON t.id=m.team_id "
                                               "WHERE t.state NOT IN ('deleted', 'merged')")}




def _name(bot_id: str) -> str:
    if bot_id == "you":
        return who.first()
    m = _db().execute("SELECT name FROM team_members WHERE bot_id=?", (bot_id,)).fetchone()
    if m:
        return m["name"]
    b = config.BOTS_BY_ID.get(bot_id)
    return b.name if b else bot_id


def post(tid: str, frm: str, kind: str, text: str, **extra) -> int:
    ""

    if kind not in THREAD_KINDS:
        raise ValueError(kind)
    text = (text or "").strip()[:MESSAGE_MAX]
    body = {"from": frm, "to": "team", "kind": kind, "text": text, **extra}
    caption = {"hello": f"{_name(frm)} said hello", "checkin": f"{_name(frm)} checked in", "summary": "Summary",
               "work": f"{_name(frm)} worked", "reply": f"{_name(frm)} replied", "need": f"{_name(frm)} needs you",
               "milestone": f"{_name(frm)} finished a milestone"}.get(kind, f"{_name(frm)} said")
    if kind == "need":
        return store.add_feed("team", None, None, caption, json.dumps(body), at=int(_now()), team_id=tid,
                              written_by="zip", written_for="person")
    return store.add_feed("team", None, None, caption, json.dumps(body), at=int(_now()), team_id=tid,
                          written_by="zip", written_for="zip")


FIRST_PERSON_RETRY = ("Say that again in the first person. You are {name}: say \"I\" and \"my\", never \"{name}\" or "
                      "\"{name}'s\". Keep every fact. Reply with the line only.")


async def say(tid: str, bot_id: str, kind: str, text: str, retry=None) -> int | None:
    ""

    name = _name(bot_id)
    if who.names_itself(text, name):
        try:
            again = await (retry or (lambda t: scheduler.team_turn(tid, bot_id, "reply", t)))(FIRST_PERSON_RETRY.format(name=name))
        except Exception:
            again = None
        again = (again or "").strip()
        if again and not who.names_itself(again, name):
            text = again
    text = (text or "").strip()
    if not text:
        return None
    return post(tid, bot_id, kind, text)


SILENT = "nothing"



SILENT_SAYINGS = {SILENT, "nothing to add", "nothing from me", "nothing here", "nothing new", "nothing to report",
                  "nothing new from me", "nothing to add from me", "no reply", "no response"}


def _silent(reply: str | None) -> bool:
    ""

    return " ".join((reply or "").split()).strip("*_`\"'.!() ").lower() in ("", *SILENT_SAYINGS)




EVERYONE_TAGS = ("everyone", "all", "team")
LEAD_TAGS = ("zero", "lead")
WORD = re.compile(r"\w+(?:['’-]\w+)*")


def _tagged(after: str, members: list[dict]) -> str | None:
    ""
    low = after.lower()
    for bid, name in sorted([("default", _name("default"))] + [(m["bot_id"], m["name"]) for m in members],
                            key=lambda x: -len(x[1])):
        if re.match(rf"{re.escape(name.lower())}(?!\w)", low):
            return bid
    word = WORD.match(low)
    if not word:
        return None
    w = word.group()
    if w in EVERYONE_TAGS:
        return "everyone"
    if w in LEAD_TAGS:
        return "default"
    owners: dict[str, set[str]] = {}
    for m in members:
        for x in WORD.findall(m["name"].lower()):
            owners.setdefault(x, set()).add(m["bot_id"])

    short = {x: next(iter(ids)) for x, ids in owners.items() if len(ids) == 1}
    if w in short:
        return short[w]
    hits = {bid for x, bid in short.items() if len(w) >= 3 and x.startswith(w)}
    return hits.pop() if len(hits) == 1 else None


def recipients(tid: str, text: str, to: list[str] | None = None) -> dict[str, bool]:
    ""




    members = _members(tid)
    everyone = ["default"] + [m["bot_id"] for m in members]
    broadcast = False
    if to:
        unknown = sorted(set(to) - set(everyone))
        if unknown:
            raise TeamsError(f"Not on this team: {', '.join(unknown)}.")
        picked = set(to)
    else:
        picked = set()
        for after in re.findall(r"(?<!\w)@(?=([^\n]*))", text or ""):
            got = _tagged(after, members)
            if got == "everyone":
                broadcast = True
            elif got:
                picked.add(got)
    if picked == set(everyone):
        return {bid: False for bid in everyone}
    if not picked:
        broadcast = True
    return {bid: bid in picked for bid in everyone if (broadcast and bid != "default") or bid in picked}


def checked_mentions(tid: str, text: str, mentions: list[dict], to: list[str] | None) -> list[dict]:
    ""
    if not mentions:
        return []
    members = {"default", *(m["bot_id"] for m in _members(tid))}
    try:
        encoded = text.encode("utf-16-le")
    except UnicodeEncodeError:
        raise TeamsError("That mention couldn't be read. Pick the Zip again.") from None
    end = 0
    for m in sorted(mentions, key=lambda m: m["location"]):
        start, length = m["location"], m["length"]
        if m["id"] not in members or m["id"] not in (to or []):
            raise TeamsError("Pick a Zip on this team.")
        if start < end or length <= 0 or start + length > len(encoded) // 2:
            raise TeamsError("That mention couldn't be read. Pick the Zip again.")
        try:
            name = encoded[start * 2:(start + length) * 2].decode("utf-16-le")
        except UnicodeDecodeError:
            raise TeamsError("That mention couldn't be read. Pick the Zip again.") from None
        if name != m["name"] or not name.strip():
            raise TeamsError("That mention couldn't be read. Pick the Zip again.")
        end = start + length
    return mentions




def _member(tid: str, bot_id: str) -> dict:
    m = _db().execute("SELECT * FROM team_members WHERE team_id=? AND bot_id=?", (tid, bot_id)).fetchone()
    if not m:
        raise TeamsError("You are not on this team.")
    return dict(m)


def update_milestone(bot_id: str, args: dict) -> dict:
    ""

    t = member_of(bot_id)
    if not t:
        raise TeamsError("You are not on a team.")
    if args.get("id") is None and args.get("title") is not None:
        return _add_milestone(t["id"], bot_id, str(args["title"]))
    try:
        mid = int(args.get("id"))
    except (TypeError, ValueError):
        raise TeamsError("Name the milestone by its id, or give a title with no id to add a new one.") from None
    r = _db().execute("SELECT * FROM team_milestones WHERE id=? AND team_id=?", (mid, t["id"])).fetchone()
    if not r:
        raise TeamsError("No such milestone on your team.")
    if r["owner"] != bot_id:
        raise TeamsError(f"Milestone {mid} belongs to {_name(r['owner'])}. Update your own, or hand it a heads-up.")
    cols = {}
    if args.get("result") is not None:
        cols["result"] = " ".join(str(args["result"]).split())[:600]
    if args.get("link") is not None:
        cols["link"] = str(args["link"]).strip()[:500]
    state = str(args.get("state") or "").strip().lower()
    if state == "done":
        try:
            check = taste.validate(args.get("check"))
        except taste.CheckError as e:
            raise TeamsError(str(e)) from None
        if check is not None:
            cols["check_json"] = json.dumps(check)
        if r["state"] != "done" and waits_on_person(cols.get("result", r["result"])) and not _db().execute(
                "SELECT 1 FROM team_needs WHERE team_id=? AND milestone_id=? AND state='open'", (t["id"], mid)).fetchone():
            raise TeamsError(WAITS_UNSEEN.format(first=who.first()))
    if state in ("blocked", "ready"):


        if _ready_card(t["id"], mid):
            state = "ready"
        elif state == "ready":
            raise TeamsError(READY_UNSEEN.format(first=who.first()))
    if state and state != r["state"]:
        if r["state"] in ("blocked", "ready") and state in ("todo", "doing"):
            cols["turns"] = 0
        row = _move("milestone", mid, state, **cols)
    elif cols:
        with store._lock:
            _db().execute(f"UPDATE team_milestones SET {', '.join(k + '=?' for k in cols)}, updated_at=? WHERE id=?",
                          (*cols.values(), _now(), mid))
            _db().commit()
        row = dict(_db().execute("SELECT * FROM team_milestones WHERE id=?", (mid,)).fetchone())
    else:
        row = dict(r)
    _changed(t["id"], "milestone", {"id": mid, "state": row["state"]})
    if row["state"] == "done" and r["state"] != "done":


        done = _db().execute("SELECT COUNT(*) FROM team_milestones WHERE team_id=? AND state='done'", (t["id"],)).fetchone()[0]
        total = _db().execute("SELECT COUNT(*) FROM team_milestones WHERE team_id=?", (t["id"],)).fetchone()[0]
        post(t["id"], bot_id, "milestone", row["title"], result=row["result"] or "", link=row["link"] or "",
             done=done, total=total, milestone_id=mid)
    if row["state"] == "done" and r["state"] != "done":
        _unblock(t["id"], f"\"{row['title']}\" is done")
        scheduler.kick()
    return {"id": mid, "state": row["state"], "result": row["result"], "link": row["link"]}


WAITS_UNSEEN = (
    "This result says the work waits on {first}, but nothing about it is in {first}'s Needs You, so {first} will not "
    "hear about it. First raise it with team_need: kind decide with the choices {first} has, and item set to the file "
    "when {first} is to review or approve something you made. Then mark this milestone done. Only if the work is "
    "finished and nothing is left for {first} to do, rewrite the result to say so.")


READY_UNSEEN = (
    "Ready means the work is finished and {first}'s yes is the one step left, with the finished thing on the card. "
    "First raise it with team_need, with item set to the file you made. Then mark the milestone ready.")
ONE_READY = (
    "{first} already has one finished thing from this team to look at: \"{title}\" from {name}. {first} sees one at "
    "a time. Mark this milestone blocked with a result that says it is finished and waits its turn. You will hear when "
    "that one is answered; raise this then.")


def _ready_card(tid: str, mid: int | None):
    ""
    return _db().execute("SELECT id FROM team_needs WHERE team_id=? AND milestone_id=? AND state='open' AND "
                         "about IS NOT NULL", (tid, mid)).fetchone() if mid else None


def waits_on_person(text: str) -> bool:
    ""




    first = re.escape(who.first())
    return bool(re.search(
        r"\bready to (?:ship|deploy|merge|publish|launch|go live)\b"
        r"|\bneeds? (?:a |to be )?(?:deploy(?:ed|ing|ment)?|ship(?:ped|ping)?|merg(?:e|ed|ing)|publish(?:ed|ing)?)\b"
        r"|\bnot (?:yet )?(?:deployed|shipped|merged|published|live)\b"
        rf"|\bready for (?:{first}'s |your )?(?:review|approval|sign-?off)\b"
        rf"|\b(?:awaiting|waiting (?:on|for)|waits (?:on|for)|needs) {first}\b"
        rf"|\b(?:awaiting|waiting (?:on|for)|pending) (?:{first}'s |your )?(?:review|approval|okay|ok|go-ahead|"
        rf"sign-?off|decision|answer|yes)\b"
        rf"|\bfor {first} to (?:review|approve|decide|check|sign|pick|choose|look)\b"
        rf"|\b{first} (?:still )?(?:needs|has) to\b", text or "", re.I))


def _add_milestone(tid: str, owner: str, title: str) -> dict:
    title = _title(title)
    if len(title) < 4:
        raise TeamsError("Write the new milestone's title as a short plain sentence.")
    with store._lock:
        ord_ = _db().execute("SELECT COALESCE(MAX(ord), 0) + 1 AS o FROM team_milestones WHERE team_id=?", (tid,)).fetchone()["o"]
        cur = _db().execute("INSERT INTO team_milestones (team_id, title, owner, state, ord, updated_at) VALUES (?,?,?,?,?,?)",
                            (tid, title, owner, "todo", ord_, _now()))
        _db().commit()
    mid = cur.lastrowid
    _changed(tid, "milestone", {"id": mid, "state": "todo"})
    scheduler.kick()
    return {"id": mid, "state": "todo", "result": "", "link": "", "next": "It is on the plan; your next work turn picks it up."}


def _open_needs_text(tid: str, bot_id: str) -> str:
    ""

    from . import asks
    shared = asks.context(bot_id)
    shared = "\n\n" + shared if shared else ""
    mine = [n for n in needs(tid) if n["from_bot"] == bot_id and n["state"] == "open"]
    if not mine:
        return shared
    lines = "\n".join(f"- [{n['id']}] {n['title']}" for n in mine)
    return (f"\n\nYour requests still waiting on {who.first()}:\n{lines}\nIf one no longer applies (you have what it "
            "asked for, or it asks the same as another), close it with team_need, close and its id, and a short reason." + shared)


def own_chat_needs(bot_id: str) -> str:
    ""


    t = member_of(bot_id)
    if not t or t["state"] in ("deleting", "deleted"):
        return ""
    mine = [n for n in needs(t["id"]) if n["from_bot"] == bot_id and n["state"] == "open" and n["kind"] in ("decide", "do")]
    if not mine:
        return ""
    first = who.first()
    lines = "\n".join(f"- [{n['id']}] {n['title']}" for n in mine)
    return (f"[Your team requests still waiting on {first} in the team's Needs You:]\n{lines}\nThese are what waits on "
            f"{first} from you. If this conversation settles one ({first} answers it here, or connects or sends what it "
            "asked for), close it with team_need, close and its id, and a reason that says what was decided or done; "
            "closing needs no mention in your reply. Bring them up only when they fit.")






OWN_WORK_MAX = 6
OWN_WORK_CHARS = 300


def own_work(bot_id: str) -> str:
    ""
    t = member_of(bot_id)
    if not t or t["state"] in ("deleting", "deleted"):
        return ""
    mine = sorted((m for m in milestones(t["id"]) if m["owner"] == bot_id and (m["result"] or "").strip()),
                  key=lambda m: -m["id"])[:OWN_WORK_MAX]
    if not mine:
        return ""
    lines = "\n".join(f"- [{m['id']}] {m['title']} ({m['state']}): {_clip(m['result'], OWN_WORK_CHARS)}"
                      for m in mine)
    return (f"[Your own work for the team \"{t['name']}\", from its plan, newest first:]\n{lines}\nBuild on it, and "
            f"never offer {who.first()} work these results already cover as if it were new.")


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def close_need(bot_id: str, nid, reason: str, session_id: str = "") -> dict:
    ""

    t = member_of(bot_id)
    if not t:
        raise TeamsError("You are not on a team.")
    try:
        nid = int(nid)
    except (TypeError, ValueError):
        raise TeamsError("Name the request by its id.") from None
    r = _db().execute("SELECT * FROM team_needs WHERE id=? AND team_id=?", (nid, t["id"])).fetchone()
    if not r:
        raise TeamsError("No such request on your team.")
    if r["from_bot"] != bot_id:
        raise TeamsError(f"Request {nid} is {_name(r['from_bot'])}'s; only the Zip that raised it can close it.")
    if r["state"] != "open":
        return {"need_id": nid, "state": r["state"], "next": "It was already settled."}
    if r["kind"] == "approve":
        raise TeamsError(f"Request {nid} is an approval for an action; {who.first()} approves or declines it.")
    why = " ".join(str(reason or "").split())[:300] or "No longer needed."
    _move("need", nid, "answered", answer=f"Closed by {_name(bot_id)}: {why}", answered_at=_now())
    from . import asks
    asks.answered("need", nid, bot_id, r["title"], f"Closed: {why}")
    _changed(t["id"], "need", _need_row(_db().execute("SELECT * FROM team_needs WHERE id=?", (nid,)).fetchone()))
    closed = f"Your request [{nid}] \"{r['title']}\" is closed: {why.rstrip('.')}"
    if session_id and session_id == store.session_for(bot_id):
        closed = f"In your own chat with {who.first()}: {closed}"
        if bot_id not in _unblock(t["id"], closed):
            scheduler.wake(t["id"], bot_id, closed)
    else:
        _unblock(t["id"], closed)
    return {"need_id": nid, "state": "answered", "next": "Closed. It is gone from Needs You."}


def _unblock(tid: str, what: str = "Something on the team changed") -> set[str]:
    ""


    waiting_on = {n["milestone_id"] for n in needs(tid) if n["state"] == "open" and n["milestone_id"]}
    rows = _db().execute("SELECT * FROM team_milestones WHERE team_id=? AND state='blocked'", (tid,)).fetchall()
    told = set()
    for r in rows:
        if r["id"] in waiting_on:
            continue
        scheduler.note(tid, r["owner"], f"{what}. Check whether what blocked [{r['id']}] \"{r['title']}\" is cleared. If it "
                       "is, use team_milestone to resume it with the evidence. Otherwise keep it blocked and update "
                       "the reason. Put any work you can finish independently in a separate milestone.")
        told.add(r["owner"])
    if rows:
        scheduler.kick()
    return told


def _in_front(tid: str, bot_id: str, mid: int) -> bool:
    ""



    return bool(_db().execute("SELECT 1 FROM team_needs WHERE team_id=? AND from_bot=? AND state='open' AND "
                              "(milestone_id=? OR milestone_id IS NULL)", (tid, bot_id, mid)).fetchone())


def _asking(tid: str, bot_id: str, milestone) -> bool:
    ""



    if _db().execute("SELECT 1 FROM team_needs WHERE team_id=? AND from_bot=? AND state='open'", (tid, bot_id)).fetchone():
        return True
    from . import asks
    names = asks._names(f"{milestone['title']} {milestone['result'] or ''}")
    return any(a.bot == bot_id and a.source != "need" and asks.words(a.text) & names for a in asks.open_for(bot_id))


def _skipped_while_blocked(tid: str, mid: int) -> bool:
    ""


    declined = _db().execute("SELECT MAX(answered_at) FROM team_needs WHERE team_id=? AND milestone_id=? AND "
                             "state='declined'", (tid, mid)).fetchone()[0]
    return bool(declined) and not _db().execute(
        "SELECT 1 FROM feed WHERE team_id=? AND kind='team_plan' AND json_extract(body, '$.id')=? AND "
        "json_extract(body, '$.state')!='blocked' AND created_at>?", (tid, mid, declined)).fetchone()


STALL_NOTE = (
    "This work of yours has been blocked a day or more, and no request of yours about it is in front of {person}:\n"
    "{lines}\n{open}{mates}For each thing it waits on: if it is gone, resume the milestone with team_milestone and say "
    "what cleared it. If it needs {person} and nothing above covers it, raise one team_need that says exactly what you "
    "need from {person}. If it waits on someone else, keep it blocked and say who and since when in its result.")
STALL_MATE = "[{mid}] names {names}. If it waits on them, send them a heads-up now with tell_teammate: what you need and since when.\n"


def _stall_note(tid: str, bot_id: str, rows: list, marks: dict) -> str:
    ""


    from . import asks
    person = who.first()
    lines = "\n".join(f"- [{r['id']}] \"{r['title']}\", blocked since "
                      f"{datetime.fromtimestamp(marks[str(r['id'])]['since'], home.TZ).strftime('%b %-d')}: "
                      f"{r['result'] or 'no result yet'}" for r in rows)
    mine = [f"\"{a.text}\"" for a in asks.open_for(bot_id) if a.bot == bot_id]
    open_ = f"Already in front of {person} from you: {'; '.join(mine)}. Do not ask again for what these cover.\n" if mine else ""
    mates = ""
    for r in rows:
        names = [m["name"] for m in _members(tid) if m["bot_id"] != bot_id and
                 re.search(rf"(?<!\w){re.escape(m['name'])}(?!\w)", f"{r['title']} {r['result']}")]
        if names:
            mates += STALL_MATE.format(mid=r["id"], names=" and ".join(names))
    return STALL_NOTE.format(person=person, lines=lines, open=open_, mates=mates)


def _declined_needs(tid: str, bot_id: str, mid: int | None) -> list[sqlite3.Row]:
    ""

    rows = _db().execute("SELECT * FROM team_needs WHERE team_id=? AND from_bot=? AND milestone_id IS ? AND "
                         "state='declined' AND kind IN ('decide','do')", (tid, bot_id, mid)).fetchall()
    if not rows:
        return []
    message = _db().execute("SELECT id, created_at FROM feed WHERE team_id=? AND kind='team' AND "
                            "json_extract(body, '$.from')='you' ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if not message:
        return rows
    held = []
    for r in rows:
        settled = _db().execute("SELECT id FROM feed WHERE team_id=? AND kind='team_need' AND "
                                "json_extract(body, '$.id')=? AND json_extract(body, '$.state')='declined' "
                                "ORDER BY id LIMIT 1", (tid, r["id"])).fetchone()
        changed = message["id"] > settled["id"] if settled else message["created_at"] > (r["answered_at"] or r["created_at"])
        if not changed:
            held.append(r)
    return held


def add_need(bot_id: str, args: dict, session_id: str = "") -> dict:
    ""
    if args.get("close") is not None:
        return close_need(bot_id, args.get("close"), str(args.get("reason") or ""), session_id)
    t = member_of(bot_id)
    if not t:
        raise TeamsError("You are not on a team.")
    if args.get("posts") is not None:
        return _add_posts(t["id"], bot_id, args.get("posts"))
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in ("decide", "do"):
        raise TeamsError("kind must be decide or do. Approvals come from the card the gate shows when you try the action.")
    title = _title(args.get("title"))
    detail = " ".join(str(args.get("detail") or "").split())[:DETAIL_MAX]
    if len(title) < 4:
        raise TeamsError("Write the title as a short plain sentence.")
    _no_app_login(bot_id, f"{title}. {detail}")
    if about.asks_to_approve(title, detail) and _POSTS.search(f"{title} {detail}"):
        raise TeamsError(ONE_PER_POST.format(first=who.first()))
    due = _when(args["due"])[0] if args.get("due") else None
    options =[" ".join(str(o).split())[:80] for o in (args.get("options") or []) if str(o).strip()][:5]
    if kind == "decide" and len(options) < 2:
        raise TeamsError("A decide item needs two to five options, in your own words.")
    items = [{"label": " ".join(str(i.get("label") or "").split())[:80], "detail": " ".join(str(i.get("detail") or "").split())[:300]}
             for i in (args.get("items") or []) if isinstance(i, dict) and str(i.get("label") or "").strip()][:20]
    with store._lock:
        mid = _current_milestone(t["id"], bot_id)
        made = _need_about(t["id"], mid, str(args.get("item") or "").strip(), title, detail, options, items)
        from . import asks
        covered = asks.covered(bot_id, title) if kind == "decide" else ""
        if covered:
            raise TeamsError(covered)
        existing = None if made else asks.wait_on(bot_id, title)
        if existing:
            return {"waiting_on": {"kind": existing.source, "id": existing.id, "bot_id": existing.bot},
                    "next": asks.waiting(existing)}
        same = _db().execute("SELECT id, title FROM team_needs WHERE team_id=? AND from_bot=? AND state='open' AND "
                             "kind IN ('decide','do') AND milestone_id IS ?", (t["id"], bot_id, mid)).fetchone()
        if same:
            raise TeamsError(f"You already have request {same['id']} open for this milestone (\"{same['title']}\"). Wait "
                             "for that answer, or close it with team_need close and file the new one.")
        for declined in _declined_needs(t["id"], bot_id, mid):
            if re.sub(r"\W+", " ", title.casefold()).strip() == re.sub(r"\W+", " ", declined["title"].casefold()).strip():
                raise TeamsError(f"{who.first()} skipped request {declined['id']} (\"{declined['title']}\"). Do not "
                                 "ask again or reword it. Carry on using your own best judgment. Mark the milestone "
                                 "done if finished, or blocked with what is still missing. Revisit this request only "
                                 f"after {who.first()} writes in the team chat or you move to a different milestone.")
        if made:
            other = _db().execute("SELECT title, from_bot FROM team_needs WHERE team_id=? AND state='open' AND "
                                  "about IS NOT NULL AND milestone_id IS NOT ?", (t["id"], mid)).fetchone()
            if other:
                raise TeamsError(ONE_READY.format(first=who.first(), title=other["title"], name=_name(other["from_bot"])))
        nid = _insert_need(t["id"], bot_id, kind, title, detail, options, items, [], mid, made, due=due)
        if made and mid and _db().execute("SELECT state FROM team_milestones WHERE id=?", (mid,)).fetchone()[0] == "blocked":
            _move("milestone", mid, "ready")
            _changed(t["id"], "milestone", {"id": mid, "state": "ready"})
    return {"need_id": nid, "next": f"{who.first()} will see it in Needs You. Mark your milestone blocked if you cannot "
                                    "go on without the answer, and keep doing every part that does not need it."}


def _no_app_login(bot_id: str, text: str) -> None:
    ""


    from . import apps, replycheck
    tool = replycheck.app_login(text)
    app_id = apps.find_id(tool) if tool else None
    if not app_id:
        return
    name = apps.BY_ID[app_id].name
    if apps.connected_natively(app_id, bot_id=bot_id):
        raise TeamsError(f"{name} is connected in Zero, so you need no {name} login. Use the {tool} tool, and "
                         "file a request only for something it can't do.")
    raise TeamsError(f"You need no {name} login. Call ask_to_connect with app {app_id}: it puts up the Connect {name} "
                     f"card, and then the {tool} tool reads it.")


def _need_about(tid: str, mid: int | None, raw: str, *texts) -> dict | None:
    ""


    root = (config.WORK_DIR / "teams" / tid).resolve()
    if not raw:
        if not about.asks_to_approve(*texts):
            return None
        link = _db().execute("SELECT link FROM team_milestones WHERE id=?", (mid,)).fetchone() if mid else None
        raw = str(link["link"] or "").strip() if link else ""
        if not raw or re.match(r"(?i)^https?://", raw):
            raise TeamsError(about.NEEDS_ITEM.format(first=who.first()))
    pr = PULL_REQUEST.match(raw)
    if pr:
        return {"title": f"Pull request {pr['repo']}#{pr['number']}", "preview": "Open it on GitHub to merge it.",
                "path": raw}
    path = Path(raw).expanduser()
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if root not in path.parents or not path.is_file():
        raise TeamsError(f"item must be a file in the team folder {root}. " + about.NEEDS_ITEM.format(first=who.first()))
    try:
        return {**about.of_file(path, (root,)), "path": str(path.relative_to(root))}
    except about.AboutError as e:
        raise TeamsError(str(e)) from None


def _doing_milestone(tid: str, bot_id: str) -> int | None:
    r = _db().execute("SELECT id FROM team_milestones WHERE team_id=? AND owner=? AND state='doing' ORDER BY ord LIMIT 1",
                      (tid, bot_id)).fetchone()
    return r["id"] if r else None


def _current_milestone(tid: str, bot_id: str) -> int | None:
    ""
    r = _db().execute("SELECT id FROM team_milestones WHERE team_id=? AND owner=? AND state IN ('doing','blocked') "
                      "ORDER BY CASE state WHEN 'doing' THEN 0 ELSE 1 END, ord LIMIT 1", (tid, bot_id)).fetchone()
    return r["id"] if r else None


def _insert_need(tid, from_bot, kind, title, detail, options, items, request_ids, milestone_id, made=None,
                 due=None, push=True) -> int:
    ""
    with store._lock:
        cur = _db().execute("INSERT INTO team_needs (team_id, from_bot, kind, title, detail, options, items, request_ids, "
                            "milestone_id, state, created_at, about, due_at) VALUES (?,?,?,?,?,?,?,?,?,'open',?,?,?)",
                            (tid, from_bot, kind, title, detail, json.dumps(options), json.dumps(items),
                             json.dumps(request_ids), milestone_id, _now(), json.dumps(made) if made else None, due))
        _db().commit()
        nid = cur.lastrowid
    _changed(tid, "need", _need_row(_db().execute("SELECT * FROM team_needs WHERE id=?", (nid,)).fetchone()))
    post(tid, from_bot, "need", title, need_id=nid)
    if push:
        _push_need(tid, from_bot, title)
    return nid


def _when(raw) -> tuple[float, bool]:
    ""

    text = " ".join(str(raw or "").replace("T", " ").split())
    for fmt, timed in (("%Y-%m-%d %H:%M", True), ("%Y-%m-%d", False)):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=home.zone()).timestamp(), timed
        except ValueError:
            pass
    raise TeamsError(f"Write the date as YYYY-MM-DD, or with a time as YYYY-MM-DD HH:MM ({home.tz_word()}).")


def _day(ts: float, timed: bool = False) -> str:
    d = datetime.fromtimestamp(ts, home.zone())
    day = d.strftime("%A, %b %-d")
    return f"{day} at {d.strftime('%-I:%M %p').lower().replace('am', 'a.m.').replace('pm', 'p.m.')}" if timed else day


def _add_posts(tid: str, bot_id: str, raw) -> dict:
    ""



    if not isinstance(raw, list) or not raw:
        raise TeamsError("posts is a list with one entry per post: {text, where, at}.")
    if len(raw) > POSTS_MAX:
        raise TeamsError(f"File at most {POSTS_MAX} posts at once.")
    cards = []
    for i, p in enumerate(raw, 1):
        p = p if isinstance(p, dict) else {}
        text = str(p.get("text") or "").strip()
        where = " ".join(str(p.get("where") or "").split())[:30]
        if not text or not where or not p.get("at"):
            raise TeamsError(f"Post {i} needs its text, where it goes (X, Instagram) and when it goes out (at).")
        if len(text) > POST_TEXT_MAX:
            raise TeamsError(f"Post {i} is longer than {POST_TEXT_MAX} characters.")
        at, timed = _when(p["at"])
        media = " ".join(str(p.get("media") or "").split())[:200]
        item = {"label": f"Post on {where} on {_day(at, timed)}"[:80], "detail": text + (f"\n\n{media}" if media else ""),
                "kind": "post", "where": where, "at": at, "timed": timed, "text": text}
        cards.append(item)
    first = who.first()
    with store._lock:
        mid = _current_milestone(tid, bot_id)
        waiting = {(it.get("where"), it.get("at"), it.get("text")) for r in _db().execute(
            "SELECT items FROM team_needs WHERE team_id=? AND from_bot=? AND kind='approve' AND state='open'",
            (tid, bot_id)) for it in _loads(r["items"], []) if isinstance(it, dict) and it.get("kind") == "post"}
        new = [it for it in cards if (it["where"], it["at"], it["text"]) not in waiting]
        ids = [_insert_need(tid, bot_id, "approve", it["label"], "", [], [it], [], mid, due=it["at"], push=False)
               for it in sorted(new, key=lambda it: it["at"])]
    if ids:

        days = sorted(it["at"] for it in new if it["at"] - DUE_AHEAD_SEC <= _now())
        if days:
            span = _day(days[0]) if len(days) == 1 else f"{_day(days[0])} to {_day(days[-1])}"
            _push_need(tid, bot_id, f"{len(days)} post{'s' if len(days) > 1 else ''} for {span} "
                                    f"{'are' if len(days) > 1 else 'is'} ready for your yes, one card each.")
        if mid:
            try:
                _move("milestone", mid, "blocked")
                _changed(tid, "milestone", {"id": mid, "state": "blocked"})
            except TeamsError:
                pass
    return {"need_ids": ids, "already_waiting": len(cards) - len(new),
            "next": f"Each post gets its own card in {first}'s Needs You, two days before it goes out. After {first}'s yes on one, you are woken at its time "
                    f"to post it, and that yes covers that post then. Post nothing before its time."}


def _push_need(tid: str, from_bot: str, title: str) -> None:
    ""
    from . import watcher
    t = _team_row(tid)
    coro = watcher.push(f"{t['name']} needs you", f"{t['name']}: {title}", from_bot, kind="needs", exact=True)
    try:
        spawn(coro)
    except RuntimeError:
        asyncio.run(coro)


def _changed(tid: str, what: str, body: dict) -> None:
    ""
    kind = "team_need" if what == "need" else "team_plan"
    store.add_feed(kind, None, None, what, json.dumps(body), at=int(_now()), team_id=tid,
                   written_by="hub", written_for="record")


ITEM_DETAIL_MAX = 900
ITEM_INPUTS_MAX = 600


def _item_detail(r: dict) -> str:
    ""

    kind = r.get("kind") or "signin"
    site = _domain(str(r.get("site") or ""))
    if kind in ("signin", "navigate"):
        return site
    parts = [str(r["because"])] if kind == "delete" and r.get("because") else []
    target = " ".join(str(r.get("target") or "").split())
    if target:
        parts.append(f"To {target}" if kind in ("send", "submit") else target)
    elif site:
        parts.append(f"On {site}")
    inputs = str(r.get("inputs") or "").strip()
    if inputs:
        parts.append(inputs[:ITEM_INPUTS_MAX] + ("..." if len(inputs) > ITEM_INPUTS_MAX else ""))
    excerpt = " ".join(str(r.get("excerpt") or "").split())
    if excerpt:
        parts.append(f"Read just before this: {excerpt[:200]}")
    return "\n".join(parts)[:ITEM_DETAIL_MAX]


def on_request(r: dict) -> bool:
    ""


    from . import consent
    if r.get("reason_state") == consent.REASON_NEEDED:
        return False
    t = member_of(r.get("bot_id") or "")
    if not t:
        return False
    kind = r.get("kind") or "signin"
    reason = " ".join(str(r.get("reason") or "").split())
    site = str(r.get("site") or "")


    title = " ".join(str(r.get("title") or "").split())
    if title and not title.startswith("Change ") and str(r.get("icons") or "").split("\n")[0] == "repeat":



        title = "Set up a scheduled job" if title == "A new scheduled job" else f"Run “{title}”"
    label = title or reason or (f"Sign in to {site}" if kind in ("signin", "navigate") else kind)
    item = {"label": label[:80], "detail": _item_detail(r), "request_id": r["id"], "kind": kind}
    if kind == "delete" and r.get("because"):
        item["because"] = str(r["because"])
    facts = [a for a in str(r.get("affects") or "").split("\n") if a.strip()][:3]
    if facts:
        item["facts"] = facts
        icons = [i for i in str(r.get("icons") or "").split("\n") if i]
        if icons:
            item["icons"] = icons
    folder = None
    if kind == "publish" and r.get("target"):

        from . import sites
        try:
            folder = _site_folder(t["id"], str(r["target"]))
            item["detail"] = sites.preview_note(folder)
            item["folder"] = str(folder)
        except TeamsError as e:
            item["detail"] = str(e)
        check = _loads(r.get("details"), {})
        if check.get("answers"):
            item["check"] = {"type": check.get("type"), "answers": check["answers"]}
    mid = _current_milestone(t["id"], r["bot_id"])
    c = _db()

    batch_kind = "signin" if kind in ("signin", "navigate") else kind
    row = next((x for x in c.execute("SELECT * FROM team_needs WHERE team_id=? AND from_bot=? AND kind='approve' AND "
                                     "state='open' ORDER BY id DESC", (t["id"], r["bot_id"]))
                if _batch_kind(_loads(x["items"], [])) == batch_kind), None)
    if row:
        items = _loads(row["items"], []) + [item]
        ids = _loads(row["request_ids"], []) + [r["id"]]
        title = f"{len(items)} things to approve" if len(items) > 1 else row["title"]
        with store._lock:
            c.execute("UPDATE team_needs SET items=?, request_ids=?, title=? WHERE id=?",
                      (json.dumps(items), json.dumps(ids), title, row["id"]))
            c.commit()
        _changed(t["id"], "need", _need_row(c.execute("SELECT * FROM team_needs WHERE id=?", (row["id"],)).fetchone()))
        _push_need(t["id"], r["bot_id"], title)
        nid = row["id"]
    else:
        nid = _insert_need(t["id"], r["bot_id"], "approve", _title(label), "", [], [item], [r["id"]], mid)
    if folder:
        from . import sites
        if sites.connected():
            _spawn(_preview(t["id"], nid, r["id"], folder))
    if mid:
        try:
            _move("milestone", mid, "blocked")
            _changed(t["id"], "milestone", {"id": mid, "state": "blocked"})
        except TeamsError:
            pass
    return True


def _spawn(coro) -> None:
    ""
    try:
        spawn(coro)
    except RuntimeError:
        coro.close()


_preview_locks: dict[str, asyncio.Lock] = {}


async def _preview(tid: str, nid: int, rid: int, folder: Path) -> None:
    ""


    from . import sites
    async with _preview_locks.setdefault(tid, asyncio.Lock()):
        try:
            t = _team_row(tid)
            out = await sites.publish(tid, t["name"], folder, t.get("site_id"), draft=True)
        except Exception:
            log.warning("could not make a draft of %s for the publish need", folder, exc_info=True)
            _set_item(tid, nid, rid, preview_failed=True)
            return
        with store._lock:
            _db().execute("UPDATE teams SET site_id=COALESCE(site_id, ?), updated_at=? WHERE id=?", (out["site_id"], _now(), tid))
            _db().commit()
    _set_item(tid, nid, rid, detail=f"Preview: {out['url']}", deploy_id=out["deploy_id"], folder=str(folder))


async def preview_site(tid: str) -> dict:
    ""


    from . import sites
    _team_row(tid)
    try:
        folder = _site_folder(tid, "site")
    except TeamsError:
        raise TeamsError("The team's site folder isn't inside its own folder, so it can't be previewed.") from None
    if not (folder / "index.html").is_file():
        raise TeamsError("The team hasn't made a site yet.")
    if not sites.connected():
        raise TeamsError("Previews need a Netlify account connected to this home.")
    async with _preview_locks.setdefault(tid, asyncio.Lock()):
        t = _team_row(tid)
        try:
            out = await sites.publish(tid, t["name"], folder, t.get("site_id"), draft=True)
        except Exception as e:
            log.warning("preview of %s failed", folder, exc_info=True)
            raise TeamsError("The preview didn't build. Try again in a minute.") from e
        with store._lock:
            _db().execute("UPDATE teams SET site_id=COALESCE(site_id, ?), preview_url=?, updated_at=? WHERE id=?",
                          (out["site_id"], out["url"], _now(), tid))
            _db().commit()
    return {"url": out["url"]}


def _set_item(tid: str, nid: int, rid: int, **fields) -> None:
    ""
    c = _db()
    with store._lock:
        row = c.execute("SELECT items FROM team_needs WHERE id=?", (nid,)).fetchone()
        if not row:
            return
        items = _loads(row["items"], [])
        for it in items:
            if isinstance(it, dict) and it.get("request_id") == rid:
                it.update(fields)
        c.execute("UPDATE team_needs SET items=? WHERE id=?", (json.dumps(items), nid))
        c.commit()
    _changed(tid, "need", _need_row(c.execute("SELECT * FROM team_needs WHERE id=?", (nid,)).fetchone()))


def _approved_draft(tid: str, folder: Path) -> tuple[int, dict] | None:
    ""

    want = Path(folder).resolve()
    for r in _db().execute("SELECT id, items FROM team_needs WHERE team_id=? AND kind='approve' AND state='approved' "
                           "ORDER BY id DESC", (tid,)):
        for it in _loads(r["items"], []):
            if isinstance(it, dict) and it.get("kind") == "publish" and it.get("folder") and \
                    Path(it["folder"]).resolve() == want and not it.get("published_at"):
                return r["id"], it
    return None


def _batch_kind(items: list) -> str:
    k = (items[0].get("kind") if items and isinstance(items[0], dict) else None) or "signin"
    return "signin" if k in ("signin", "navigate") else k


def _domain(site: str) -> str:
    from . import consent
    try:
        return consent.site_of(site) or site
    except Exception:
        return site


def need_due(r) -> float | None:
    ""

    if r["due_at"]:
        return r["due_at"]
    from . import loopcheck
    filed = datetime.fromtimestamp(r["created_at"], home.zone()).date()
    days = [d for d, _ in loopcheck._dates({"summary": r["title"], "next_step": r["detail"] or "",
                                           "opened_at": r["created_at"]}) if d.date() >= filed]
    return datetime.combine(min(days).date(), datetime.min.time(), home.zone()).timestamp() if days else None


def _post_of(r) -> dict | None:
    ""
    return next((it for it in _loads(r["items"], []) if isinstance(it, dict) and it.get("kind") == "post"), None)


def waits_for_its_date(r) -> bool:
    ""


    return bool(r["due_at"]) and r["due_at"] - DUE_AHEAD_SEC > _now() and _post_of(r) is not None


def _waiting_posts(tid: str) -> set[int]:
    return {r["id"] for r in _db().execute("SELECT * FROM team_needs WHERE team_id=? AND state='open' AND kind='approve'",
                                           (tid,)).fetchall() if waits_for_its_date(r)}





_SITES_NAMED = (("x.com", "X", re.compile(r"\b(?:X|Twitter)\b")),
                ("instagram.com", "Instagram", re.compile(r"\bInstagram\b", re.I)))


def _post_sites(r) -> list[str]:
    ""

    from . import consent
    item = _post_of(r)
    if item:
        return [s for s in (consent.post_site(item["where"]),) if s]
    text = f"{r['title']} {r['detail'] or ''}"
    return [site for site, _, named in _SITES_NAMED if named.search(text)] if _POSTS.search(text) else []




FILE_POSTS = ("One yes can't schedule these posts, because each one needs its own card. File the drafted posts now with "
              "team_need posts, one entry per post with its text, where it goes and when it goes out.")


def _batch_of_posts(r) -> bool:
    ""
    return r["kind"] != "approve" and bool(_post_sites(r)) and about.asks_to_approve(r["title"], r["detail"])


def _can_sign_in(bot_id: str, site: str) -> bool:
    ""

    from . import consent, logins, service_keys, shared_sessions, signin_access
    if site == "x.com" and service_keys.has(bot_id, "x"):
        return True
    if not signin_access.allowed(bot_id, site):
        return False
    shared = shared_sessions._row(site)
    return (site in consent.signed_in_sites() or bool(shared and not shared["removed"] and shared["sealed"] is not None)
            or logins.has_for_site(bot_id, site))


def _ask_sign_ins(rows) -> dict[str, list[str]]:
    ""



    from . import signin, signin_access
    wanted: dict[tuple, float] = {}
    for at, r in rows:
        for site in _post_sites(r):
            wanted.setdefault((r["from_bot"], site), at)
    asked: dict[str, list[str]] = {}
    for (bot, site), at in wanted.items():
        if bot not in config.BOTS_BY_ID or _can_sign_in(bot, site):
            continue
        place = next(p for s, p, _ in _SITES_NAMED if s == site)
        why = f"post the {place} posts that start {_day(at)}"
        kept = signin_access._row(site)
        others = [_name(b) for b in _loads(kept["zips"], []) if b != bot] \
            if kept and not kept["removed"] and kept["mode"] == "selected" else []
        if others:
            why += (f". {site} sign-ins stay with {' and '.join(others)} for now, and signing in here lets "
                    f"{_name(bot)} use this one too")
        try:
            signin.ask(bot, SIGN_IN_URLS[site], why, None)
        except signin.AskError:
            continue
        if signin._open_card(bot, site, signin.Purpose.signin):
            asked.setdefault(bot, []).append(place)
    return asked


def _every_name(rows: list) -> str:
    ""

    from . import notify
    days = [datetime.fromtimestamp(at, home.zone()).strftime("%b %-d") for at, _ in rows]
    same = len(set(days)) == 1

    def sentence(titles: list[str]) -> str:
        names = [(f"the {_post_of(r)['where']} post" if _post_of(r) else f"“{t}”") + ("" if same else f" by {day}")
                 for t, (_, r), day in zip(titles, rows, days)]
        return f"Answer {', '.join(names[:-1])} and {names[-1]}" + (f" by {days[0]}." if same else ".")

    titles = [r["title"].rstrip(".?") for _, r in rows]
    room = notify.LIMIT - len(f"{_team_row(rows[0][1]['team_id'])['name']}: ")
    if len(sentence(titles)) > room:
        each = max((room - len(sentence([""] * len(rows)))) // len(rows), 8)
        titles = [_clip(t, each) for t in titles]
    return sentence(titles)


def _slips(r, at: float) -> str:
    m = _db().execute("SELECT title FROM team_milestones WHERE id=?", (r["milestone_id"],)).fetchone() \
        if r["milestone_id"] else None
    what = f"“{m[0]}”" if m else "the team's plan"
    return f"Without an answer, {what} slips past {_day(at)}."


def due_line(rows: list) -> str:
    ""

    at, r = rows[0]
    if 2 <= len(rows) <= 3:
        if all(_post_of(x) for _, x in rows):
            return f"{_every_name(rows)} They can't go out without your yes."
        return f"{_every_name(rows)} {_slips(next((x for _, x in rows if x['milestone_id']), r), at)}"
    item = _post_of(r)
    many = f"{len(rows)} requests are still waiting on you, starting with " if len(rows) > 1 else ""
    if item:

        what = f"the {item['where']} post for {_day(at, item.get('timed', False))}"
        if all(_post_of(x) for _, x in rows):
            return (f"{len(rows)} posts are ready for your yes, starting with {what.rstrip('.')}. None can go out without it."
                    if len(rows) > 1 else f"{what[0].upper()}{what[1:]} is ready for your yes, and it can't go out without it.")
        return f"{many}{what}. It can't go out without your yes."
    title = f"“{r['title'].rstrip('.?')}”"
    return f"{many}{title}. {_slips(r, at)}" if many else f"{title} is still waiting on you. {_slips(r, at)}"


def _resume(tid: str, r, note: str) -> None:
    ""

    _changed(tid, "need", _need_row(_db().execute("SELECT * FROM team_needs WHERE id=?", (r["id"],)).fetchone()))
    settled = _db().execute("SELECT state FROM team_needs WHERE id=?", (r["id"],)).fetchone()
    ready = bool(r["about"]) and bool(_db().execute("SELECT 1 FROM team_milestones WHERE id=? AND state='ready'",
                                                    (r["milestone_id"],)).fetchone())
    if r["milestone_id"] and (ready or (r["kind"] == "approve" and settled["state"] == "approved")):
        try:
            _move("milestone", r["milestone_id"], "doing", turns=0)
            _changed(tid, "milestone", {"id": r["milestone_id"], "state": "doing"})
        except TeamsError:
            pass
    scheduler.wake(tid, r["from_bot"], note, kind="answer")
    if r["about"]:
        _unblock(tid, f"{who.first()} answered \"{r['title']}\"")


def _settle_approval(r) -> bool:
    ""



    from . import consent
    ids = [int(i) for i in _loads(r["request_ids"], [])]
    if not ids:
        return False
    status = [(consent.get_request(rid) or {}).get("status") for rid in ids]
    if "pending" in status:
        return False
    allowed, denied = status.count("allowed"), status.count("denied")
    first = who.first()
    with store._lock:
        if _db().execute("SELECT state FROM team_needs WHERE id=?", (r["id"],)).fetchone()["state"] != "open":
            return False
        _move("need", r["id"], "approved" if allowed else "declined",
              answer="approve" if allowed else "decline" if denied else "expired", answered_at=_now())
    if allowed:
        note = (f"{first} approved: {r['title']}. Make exactly the same call again now, unchanged; it goes through "
                f"once. Do not ask {first} to confirm it again.")
        if denied:
            note += f" {first} said not now to some of them; leave those undone."
    elif denied:
        note = (f"{first} said not now to: {r['title']}. Do not do it, and do not try another way. Finish what you can "
                "and record what is left undone on the milestone.")
    else:
        note = f"{first} did not answer in time: {r['title']}. It did not happen; raise it again if it still matters."
    if 0 < allowed + denied < len(ids):
        note += " Some of those requests had expired; raise them again if they still matter."
    _resume(r["team_id"], r, note)
    return True


def settle_decided() -> int:
    ""



    from . import consent
    consent.expire_old()
    rows = _db().execute("SELECT n.* FROM team_needs n JOIN teams t ON t.id = n.team_id WHERE n.kind='approve' AND "
                         "n.state='open' AND t.state NOT IN ('deleting','deleted','merged') ORDER BY n.id").fetchall()
    return sum(_settle_approval(r) for r in rows)


def answer_need(tid: str, nid: int, answer: str, files: str = "") -> dict:
    ""

    from . import consent
    _team_row(tid)
    r = _db().execute("SELECT * FROM team_needs WHERE id=? AND team_id=?", (nid, tid)).fetchone()
    if not r:
        raise TeamsError("No such need.")
    if r["state"] != "open":
        raise TeamsError("That one is already answered.")
    a = " ".join((answer or "").split())
    if files and not a:
        a = "Here are the files."
    word = a.lower().rstrip(".")
    kind = r["kind"]
    if kind == "approve":
        if word not in ("approve", "decline"):
            raise TeamsError("Approve or decline this one.")
        if not _loads(r["request_ids"], []):
            return _answer_post(tid, r, word == "approve")
        for rid in _loads(r["request_ids"], []):
            consent.decide(int(rid), word == "approve")
        if not _settle_approval(r):
            raise TeamsError("That one is already answered.")
        return _need_row(_db().execute("SELECT * FROM team_needs WHERE id=?", (nid,)).fetchone())
    if kind == "decide":
        if not a:
            raise TeamsError("Pick an option or write an answer.")
        state = "declined" if word == "decline" else "answered"
        row = _move("need", nid, state, answer=a[:600], answered_at=_now())
        note = (f"{who.first()} answered \"{r['title']}\": {a}." if state == "answered"
                else f"{who.first()} declined to decide \"{r['title']}\".")
    else:
        if word in ("approve", "done", "did it"):
            row = _move("need", nid, "approved", answer=word, answered_at=_now())
            note = f"{who.first()} did this: {r['title']}. Carry on from there."
        elif word == "decline":
            row = _move("need", nid, "declined", answer=word, answered_at=_now())
            note = f"{who.first()} skipped: {r['title']}."
        else:
            if not a:
                raise TeamsError("Say what happened with this one.")
            row = _move("need", nid, "answered", answer=a[:600], answered_at=_now())
            note = f"{who.first()} said about \"{r['title']}\": {a}."
    if row["state"] == "declined":
        note += (" Do not ask again or reword this request. Carry on using your own best judgment. Finish what you "
                 "can without it. Mark the milestone done if finished, or blocked with what is still missing. "
                 f"Revisit this request only after {who.first()} writes in the team chat or you move to a different milestone.")
    elif _batch_of_posts(r):
        note += f" {FILE_POSTS}"
    if files:
        note += f" {who.first()} attached:\n{files}"
    typed = kind in ("decide", "do") and word not in ("approve", "done", "did it", "decline") \
        and a not in (_loads(r["options"], []) or []) and a != "Here are the files."
    if typed or files:
        post(tid, "you", "message", "\n\n".join(x for x in ((answer or "").strip() if typed else "", files) if x))
    _resume(tid, r, note)
    from . import asks
    asks.answered("need", nid, r["from_bot"], r["title"], a + ("\n" + files if files else ""))
    return _need_row(_db().execute("SELECT * FROM team_needs WHERE id=?", (nid,)).fetchone())


def _answer_post(tid: str, r, yes: bool) -> dict:
    ""
    from . import consent
    item = _post_of(r)
    if not item:
        raise TeamsError("That one is already answered.")
    first, when = who.first(), _day(item["at"], item.get("timed", False))
    now = item["at"] <= _now()
    row = _move("need", r["id"], "approved" if yes else "declined", answer="approve" if yes else "decline",
                answered_at=_now(), **({"woke_at": _now()} if yes and now else {}))
    if yes:
        note = post_now(item, r["from_bot"]) if now else (
            f"{first} approved the {item['where']} post for {when}: \"{item['text']}\" You are woken at that time to "
            "post it, and that yes covers that one post then, so no second card comes. Do not post it before then.")
        site = consent.post_site(item["where"])
        if site and site not in consent.signed_in_sites() and not _posts_by_api(r["from_bot"], site):
            note += (f" Your browser is not signed in to {item['where']} yet. Ask {first} now with ask_to_sign_in "
                     f"({SIGN_IN_URLS[site]}), so it can go out at its time.")
    else:
        note = (f"{first} said no to the {item['where']} post for {when}: \"{item['text']}\" Do not post it or a "
                f"reworded version. {first}'s other posts keep their own answers.")
    _resume(tid, r, note)
    return _need_row(_db().execute("SELECT * FROM team_needs WHERE id=?", (row["id"],)).fetchone())


def _posts_by_api(bot_id: str, site: str) -> bool:
    from . import service_keys
    return site == "x.com" and service_keys.has(bot_id, "x")


def post_now(item: dict, bot_id: str = "") -> str:
    ""
    from . import consent
    first, site, text = who.first(), consent.post_site(item["where"]), item["text"]
    head = f"It is time for the {item['where']} post {first} approved for {_day(item['at'], item.get('timed', False))}."
    if site == "x.com":
        how = f"Post it now with post_to_x, with the text exactly: \"{text}\""
    elif site == "instagram.com":
        media = item["detail"][len(text):].strip()
        how = (f"Post it now on instagram.com from your browser{f' with {media}' if media else ''}, with the caption "
               f"exactly: \"{text}\" Then click Share.")
    else:
        return f"{head} Post it now, exactly as written: \"{text}\""
    if _posts_by_api(bot_id, site):
        return (f"{head} {how} The yes on its card covers this one post, so no new card comes. It goes through X's "
                f"API, so you need no browser or sign-in. Tell {first} the post's link, or in one line what X said.")
    return (f"{head} {how} The yes on its card covers this one post, so no new card comes. If you are not signed in, "
            f"ask {first} with ask_to_sign_in ({SIGN_IN_URLS[site]}) and post it once he has. If it fails another "
            f"way, tell {first} in one line what happened.")




def set_paused(tid: str, paused: bool) -> dict:
    t = _team_row(tid)
    if paused and (t["state"] != "paused" or t["paused_reason"] != "you"):

        _move("team", tid, "paused", paused_reason="you", resume_at=None)
        _stop_soon(tid)
    elif not paused and t["state"] == "paused":
        _move("team", tid, "active", paused_reason=None, resume_at=None)
        scheduler.kick()
    return detail(tid)


def pause_for(tid: str, reason: str, resume_at: float | None) -> None:
    ""
    t = _team_row(tid)
    if t["state"] in ("active", "paused"):
        _move("team", tid, "paused", paused_reason=reason, resume_at=resume_at)
        _changed(tid, "team", {"state": "paused", "paused_reason": reason})
    _stop_soon(tid)


def _stop_soon(tid: str) -> None:
    ""
    try:
        spawn(scheduler.stop_team(tid))
    except RuntimeError:
        pass


async def delete(tid: str, remove_bot) -> None:
    ""



    from . import turns
    t = _team_row(tid)
    _move("team", tid, "deleting")
    await scheduler.stop_team(tid)
    failed = []
    for m in _members(tid):
        try:
            await turns.stop(m["bot_id"])
        except Exception:
            log.warning("could not stop %s before deleting it", m["bot_id"], exc_info=True)
        if m["bot_id"] not in config.BOTS_BY_ID:
            continue
        try:
            await asyncio.to_thread(remove_bot, m["bot_id"])
        except Exception:
            log.warning("could not remove %s", m["bot_id"], exc_info=True)
            failed.append(m["name"])
    if failed:
        _move("team", tid, "paused", paused_reason="you")
        raise TeamsError(f"Could not remove {', '.join(failed)}. Try again in a moment.")
    _move("team", tid, "deleted")
    c = _db()
    with store._lock:
        for table in ("team_members", "team_milestones", "team_needs", "team_turns", "team_wakes"):
            c.execute(f"DELETE FROM {table} WHERE team_id=?", (tid,))
        c.execute("DELETE FROM feed WHERE team_id=?", (tid,))
        c.execute("DELETE FROM teams WHERE id=?", (tid,))
        if t.get("proposal_id"):
            c.execute("UPDATE team_proposals SET team_id=NULL WHERE id=?", (t["proposal_id"],))
        c.commit()




CHECKIN_GUIDE = (
    "{lead} asked the team to check in. Your milestones and what you recorded on them (your work runs in another "
    "conversation, so this is the record):\n{plan}\nOpen in Needs You from you: {needs}\n\n"
    "Answer in the first person, in three short sentences at most: what you did, what "
    "is next, and what you need (name the teammate if you need something from one). Facts only, from the record "
    "above, with estimates labeled as estimates. No greeting, thanks or sign-off, no markdown. You are {name}: say "
    "\"I\", never your own name."
)
ROUND2_GUIDE = (
    "Your teammates answered the check-in below. One of them needs something from you, or you named one. Reply to "
    "that teammate only, in one or two sentences, in the first person. If nothing needs saying, reply with the single "
    "word: {silent}.\n\n{answers}"
)
SUMMARY_GUIDE = (
    "You lead the team \"{team}\" (goal: {goal}). The members just checked in below. Write a three-line summary for "
    "{person}: one line on progress, one on what is next, one on what needs {person} (or that nothing does). Use the shared voice guide. "
    "No markdown, no names in the third person for yourself.\n\n{answers}"
)
THREAD_LINES = 20
LINE_MAX = 600


REPLY_GUIDE = (
    "{person} wrote in the group chat of your team \"{team}\" (goal: {goal}). It went to {sent}.\n\n"
    "The chat before it, oldest first:\n{before}\n\n{person}'s message: \"{text}\"\n\n"
    "Teammates who already answered it:\n{after}\n\n"
    "{rule} Never acknowledge the message or say you saw it, and never repeat what a teammate already said. Answer in "
    "the first person in one to three short sentences, as {name}: say \"I\", never your own name. This conversation is "
    "for answering only; any work it asks for happens in your next work turn, so say what you will do, not that you "
    "did it.\n\n{about}"
)
NAMED_RULE = "{person} sent it to you by name, so answer it."
EVERYONE_RULE = ("{person} sent it to the whole team. Reply only when you have something useful to add or work you will "
                 "take on. Otherwise reply with exactly the word {silent} and nothing else.")


LEAD_EVERYONE_RULE = ("{person} sent it to the whole team, and you read it first. If it asks about the team as a whole, "
                      "such as how it is going or what is next, answer it. Otherwise reply only when you have something "
                      "useful to add, and else reply with exactly the word {silent} and nothing else.")
MEMBER_ABOUT = "Your role: {role}\nYour brief:\n{brief}\nYour milestones:\n{mine}"
LEAD_ABOUT = (
    "You lead this team. Members use {person}'s Mac and the files on it in their work, so when something needs the Mac, "
    "say the team will do it. Never ask {person} to send a message for it.\n\n"
    "The members and the briefs you wrote them:\n{members}\n\nPlan:\n{plan}"
)


def _plan_text(tid: str) -> str:
    from . import teamfiles
    plan = "\n".join(f"- [{m['id']}] {m['title']} ({_name(m['owner'])}, {m['state']})" + (f": {m['result']}" if m["result"] else "")
                     for m in milestones(tid)) or "- (no milestones)"
    return f"{plan}\nNewest files in the team folder: {teamfiles.newest_names(tid)}"


async def checkin(tid: str) -> None:
    t = _team_row(tid)
    members = [m for m in _members(tid) if m["ready"]]
    lead = _name("default")
    post(tid, "default", "checkin", CHECKIN_QUESTION)
    guide = CHECKIN_GUIDE
    answers: dict[str, str] = {}

    async def ask(m):
        mine = [x for x in milestones(tid) if x["owner"] == m["bot_id"]]
        plan = "\n".join(f"- [{x['id']}] {x['title']} ({x['state']})" + (f": {x['result']}" if x["result"] else "")
                         + (f" {x['link']}" if x["link"] else "") for x in mine) or "- (none)"
        open_needs = [n["title"] for n in needs(tid) if n["from_bot"] == m["bot_id"] and n["state"] == "open"]
        text = guide.format(lead=lead, name=m["name"], plan=plan, needs="; ".join(open_needs) or "nothing")
        reply = await scheduler.team_turn(tid, m["bot_id"], "checkin", text)
        if reply:
            answers[m["bot_id"]] = reply
            await say(tid, m["bot_id"], "checkin", reply)
    await asyncio.gather(*[ask(m) for m in members])
    named = {m["bot_id"]: m for m in members}
    for bid, text in list(answers.items()):
        others = [m for m in members if m["bot_id"] != bid and re.search(rf"(?<!\w){re.escape(m['name'])}(?!\w)", text)]
        if not others:
            continue
        for other in others:
            lines = "\n".join(f"{_name(b)}: {a}" for b, a in answers.items() if b != other["bot_id"])
            reply = await scheduler.team_turn(tid, other["bot_id"], "reply",
                                              ROUND2_GUIDE.format(answers=lines, silent=SILENT))
            if not _silent(reply):
                await say(tid, other["bot_id"], "reply", reply)
    lines = "\n".join(f"{named[b]['name']}: {a}" for b, a in answers.items())
    text = await scheduler.team_turn(tid, "default", "summary", SUMMARY_GUIDE.format(
        team=t["name"], goal=t["goal"], person=who.first(), answers=lines or "(nobody answered)"))
    if text:
        fid = await say(tid, "default", "summary", text)
        if fid:
            with store._lock:
                _db().execute("UPDATE teams SET summary_text=?, summary_at=?, updated_at=? WHERE id=?",
                              (text.strip()[:2000], _now(), _now(), tid))
                _db().commit()


def _chat(tid: str, fid: int, before: bool) -> str:
    ""

    q = ("SELECT body FROM feed WHERE kind='team' AND team_id=? AND id<? ORDER BY id DESC LIMIT ?" if before else
         "SELECT body FROM feed WHERE kind='team' AND team_id=? AND id>? ORDER BY id LIMIT ?")
    rows = [_loads(r["body"], {}) for r in store.db().execute(q, (tid, fid, THREAD_LINES))]
    if not before:
        rows = [b for b in rows if b.get("kind") == "reply"]
    lines = [f"{_name(b.get('from'))}: {' '.join(str(b.get('text') or '').split())[:LINE_MAX]}" for b in rows]
    return "\n".join(reversed(lines) if before else lines) or "(nothing yet)"


def _reply_prompt(tid: str, fid: int, text: str, bid: str, named: bool, readers: dict[str, bool]) -> str:
    ""
    t = _team_row(tid)
    person = who.first()
    names = ", ".join("you" if b == bid else _name(b) for b in readers)
    sent = f"the whole team ({names})" if {m["bot_id"] for m in _members(tid)} <= set(readers) else names
    if bid == "default":
        about = LEAD_ABOUT.format(person=person, plan=_plan_text(tid), members="\n\n".join(
            f"{m['name']} ({m['role']}):\n{m['brief'] or '(no brief)'}" for m in _members(tid)))
    else:
        m = _member(tid, bid)
        mine = "\n".join(f"- [{x['id']}] {x['title']} ({x['state']})" + (f": {x['result']}" if x["result"] else "")
                         for x in milestones(tid) if x["owner"] == bid) or "- (none)"
        about = MEMBER_ABOUT.format(role=m["role"], brief=m["brief"] or "(no brief)", mine=mine)
    rule = (NAMED_RULE if named else LEAD_EVERYONE_RULE if bid == "default" else EVERYONE_RULE).format(
        person=person, silent=SILENT)
    return REPLY_GUIDE.format(person=person, team=t["name"], goal=t["goal"], sent=sent, text=text,
                              before=_chat(tid, fid, before=True), after=_chat(tid, fid, before=False),
                              rule=rule, name=_name(bid), about=about)


_rounds: dict[str, asyncio.Lock] = {}


def _round(tid: str) -> asyncio.Lock:
    return _rounds.setdefault(tid, asyncio.Lock())


async def message(tid: str, text: str, to: list[str] | None = None, *, mentions: list[dict] | None = None) -> None:
    ""


    _team_row(tid)
    text = text.strip()[:MESSAGE_MAX]
    readers = recipients(tid, text, to)
    said_at = _now()
    mentions = checked_mentions(tid, text, mentions or [], to)
    fid = post(tid, "you", "message", text, **({"mentions": mentions} if mentions else {}))
    members = {m["bot_id"] for m in _members(tid)}
    for bid, named in readers.items():
        if named and bid in members:
            scheduler.note(tid, bid, text, kind="instruction", at=said_at)

    async def read(bid: str, named: bool) -> None:
        reply = None
        try:
            reply = await scheduler.team_turn(tid, bid, "reply",
                                              lambda: _reply_prompt(tid, fid, text, bid, named, readers),
                                              interrupt=named or bid == "default")
        finally:
            if bid in members and not named and (reply is None or not _silent(reply)):
                scheduler.note(tid, bid, text, kind="instruction", at=said_at)
        if not _silent(reply):
            await say(tid, bid, "reply", reply)




    order = sorted(readers, key=lambda b: (b != "default", scheduler.working(b)))
    async with _round(tid):
        for i, bid in enumerate(order):
            try:
                await read(bid, readers[bid])
            except asyncio.CancelledError:
                for later in order[i + 1:]:
                    if later in members and not readers[later]:
                        scheduler.note(tid, later, text, kind="instruction", at=said_at)
                raise
    scheduler.kick()


async def post_from_tool(bot_id: str, session_id: str, text: str) -> str:
    ""
    from . import team
    t = member_of(bot_id)
    if not t and bot_id != "default":
        raise TeamsError("You are not on a team.")
    if not team.in_team_session(session_id):
        raise TeamsError("The group chat is for check-ins and the person's questions. Your work speaks through "
                         "team_milestone.")
    text = " ".join((text or "").split())
    if not text:
        raise TeamsError("Write the line.")
    tid = t["id"] if t else scheduler.current_team.get(bot_id)
    if not tid:
        raise TeamsError("No team is in this conversation.")
    name = _name(bot_id)
    if who.names_itself(text, name):
        raise TeamsError(f"Say it in the first person: \"I\" and \"my\", never \"{name}\". Then call team_post again.")
    fid = post(tid, bot_id, "reply", text[:1200])
    return "Posted." if fid else "Nothing to post."




async def publish(bot_id: str, args: dict) -> dict:
    ""

    from . import sites
    t = member_of(bot_id)
    if not t:
        raise TeamsError("You are not on a team.")
    folder = _site_folder(t["id"], str(args.get("folder") or ""))
    if not sites.connected():
        title, detail = sites.not_connected_need()
        if not _db().execute("SELECT 1 FROM team_needs WHERE team_id=? AND kind='do' AND title=? AND state='open'",
                             (t["id"], title)).fetchone():
            _insert_need(t["id"], bot_id, "do", title, detail, [], [], [], _doing_milestone(t["id"], bot_id))
        raise TeamsError(f"Not published: this home is not connected to Netlify, so the site stays a local preview at "
                         f"{folder}. {who.first()} has the one step in Needs You; carry on with the rest.")
    draft = _approved_draft(t["id"], folder)
    if draft and not draft[1].get("deploy_id") and not draft[1].get("preview_failed"):
        raise TeamsError("Not published yet: the preview the approval is for is still uploading. Call team_site_publish "
                         "again in a minute, with the same folder.")
    restore = bool(draft and draft[1].get("deploy_id"))
    try:
        if restore:
            out = await sites.restore(t["site_id"], draft[1]["deploy_id"])
        else:
            out = await sites.publish(t["id"], t["name"], folder, t.get("site_id"))
    except sites.NotConnected:
        raise TeamsError("Not published: this home is not connected to Netlify.") from None
    except sites.PublishError as e:
        raise TeamsError(f"Not published: {e}") from None
    if draft:
        _set_item(t["id"], draft[0], draft[1]["request_id"], published_at=_now())
    set_site(t["id"], out["site_id"], out["url"])
    post(t["id"], bot_id, "work", f"The site is online at {out['url']}.")
    return {"url": out["url"], "next": "Put the link in the milestone's link field."}


def _site_folder(tid: str, raw: str) -> Path:
    ""
    root = (config.WORK_DIR / "teams" / tid).resolve()
    folder = Path(raw).expanduser()
    folder = (root / folder).resolve() if not folder.is_absolute() else folder.resolve()
    if root not in folder.parents and folder != root:
        raise TeamsError(f"The site folder must be inside the team folder {root}.")
    return folder


async def refresh_signups(t: dict) -> dict:
    ""

    from . import sites
    n = await sites.signups(t["site_id"])
    with store._lock:
        _db().execute("UPDATE teams SET signups=COALESCE(?, signups), signups_at=? WHERE id=?", (n, _now(), t["id"]))
        _db().commit()
    if n is not None:
        t["signups"] = n
    return t


def set_site(tid: str, site_id: str, url: str) -> None:
    with store._lock:
        _db().execute("UPDATE teams SET site_id=?, site_url=?, updated_at=? WHERE id=?", (site_id, url, _now(), tid))
        _db().commit()
    _changed(tid, "team", {"site": {"url": url}})




SOUL_START = "<!-- team-space:start -->"
SOUL_END = "<!-- team-space:end -->"


def soul_block(bot_id: str) -> str | None:
    ""
    t = member_of(bot_id)
    if not t:
        return None
    me = _member(t["id"], bot_id)
    mates = "\n".join(f"- {m['name']} (`{m['bot_id']}`): {m['role']}" for m in _members(t["id"]) if m["bot_id"] != bot_id)
    folder = config.WORK_DIR / "teams" / t["id"]
    first = who.first()
    return f"""{SOUL_START}
## Your team

You are {me['name']} on the team "{t['name']}". The goal: {t['goal']}
Teammates:
{mates or '- (none yet)'}

The plan lives with the hub; each work turn tells you which milestone is yours now and what done means for it. Shared
files go in `{folder}` (one folder for the whole team; name files plainly, keep the latest version at the top level).
Your work turns run on their own between check-ins, so nobody is reading along: produce the artifact, verify claims
against real sources, label every estimate as an estimate, and finish by calling team_milestone with the result and
a link or path. Put only money, {first}'s identity (accounts, sign-ups, sending as {first}) and real decisions in
Needs You with team_need; everything else you decide yourself and note in the result. Never leave a placeholder you
do not mention. In the team's chat {first} writes to the whole team or tags Zips by name. Answer when {first} names
you or asks for a check-in; when a message goes to everyone, speak only when you have something useful to add or work
you will take on. You speak there as "I", never as {me['name']}.
{taste.soul_lines()}
{SOUL_END}"""




WORK_BRIEF = (
    "[Team work, not a message from {person}] {now} You are on the team \"{team}\" (goal: {goal}). Your milestone now is "
    "[{mid}] \"{title}\".{done_when}{result} Work on it for this turn: make the real thing (a sheet, a document, a page) in "
    "`{folder}`, check claims against real sources and say which, and label estimates. When the milestone is done, call "
    "team_milestone with id {mid}, state done, a one-line result and the file path or link. When it is finished and "
    "{person}'s yes is the one step left (posts to approve, a fix to ship), call team_need with item set to the file, "
    "then team_milestone with state ready. If you cannot finish this "
    "turn but have a concrete next step you can finish yourself, call team_milestone with state doing and a one-line "
    "result of where you are. If the outcome waits on {person}, a customer, a reply, a date or the outside world, "
    "mark it blocked with that reason, even if there is no question to ask. Keep waiting outcomes blocked; put "
    "drafts, follow-ups and other work you can finish in separate milestones with a title and no id. If something only {person} can "
    "do stands in the way (money, an account, a decision between real options), call team_need and mark the milestone "
    "blocked; keep doing every part that does not need it. Before you raise a team_need for files, code or links, look "
    "for them on {person}'s Mac. Do not message {person} from here; your report is the milestone's result.{mates}{notes}"
)
INSTRUCTION_BRIEF = (
    "[Team work, not a message from {person}] {now} You are on the team \"{team}\" (goal: {goal}). Every milestone of yours "
    "is done or waiting on {person} or an outside event:\n{mine}{notes}\n\nThis turn is for acting on that, once. If it changes work you "
    "finished, change the real files in `{folder}` and call team_milestone with that milestone's id and a new one-line "
    "result (and link). If it needs new work, call team_milestone with a title and no id to add a milestone you own; "
    "the team queue picks it up. Keep waiting outcomes blocked until you have evidence that their blocker is gone; "
    "an answer, a teammate's update or a new message alone is not evidence that a customer signed or a future event "
    "happened. Update drafts and prepare follow-ups as separate work you can finish. Resume blocked work with "
    "team_milestone only when you can explain what cleared the blocker. If nothing of yours needs to change, reply "
    "with one line saying so. Do not message "
    "{person} from here.{mates}"
)


def _now_line() -> str:
    ""

    from . import notices
    t = time.time()
    day = datetime.fromtimestamp(t, home.TZ).strftime("%a %b %-d")
    return (f"It is {day} at {notices.clock(t)} {home.tz_word()}. Times from tools are often UTC, so convert one before "
            "you write it down.")


CAP_QUESTION = (
    "Write one question for {person} about work you have paused. Do not act or use tools. The goal is {goal}. "
    "The work is {title}. Finished means {done_when}. Your saved progress: {result}. Your last reply: {reply}. "
    "Related requests and answers: {needs}. Return only JSON with title, detail and options. The title names the "
    "one decision or action you need from {person}. Detail is one to three short plain sentences in first person: "
    "what I tried, what I am stuck on, and the one thing I need. Use only the facts above; do not invent completed "
    "work or permission. Ask only for something the person must do or decide, never permission to draft, research "
    "or update a file yourself. Options are two to five distinct answers that say what choosing them means. "
    "Never use Done, Not Now, turns, milestones, budgets, tokens, file paths or your own name ({name}). "
    "Choosing an option will tell you the answer; it will not complete the work or automatically restart it. "
    "Title is at most 80 characters, detail at most 600, each option at most 80."
)


MATES_LINE = (" To get a fact from a teammate on this team, or to tell one something it needs for its own work, use "
              "ask_teammate or tell_teammate with its name: {names}. For what another Zip already knows, such as "
              "its notes on this project, ask that Zip the same way.")
INSTRUCTIONS_HEADING = "{person} said in the group chat (these are {person}'s own words, oldest first; act on them):"
NOTES_HEADING = "Since your last turn:"


def _notes_text(pending: list[dict], person: str) -> str:
    ""
    out = ""
    said = [n["text"] for n in pending if n["kind"] == "instruction"]
    if said:
        out += "\n\n" + INSTRUCTIONS_HEADING.format(person=person) + "\n" + "\n".join(f"- \"{s}\"" for s in said)
    rest = [n["text"] for n in pending if n["kind"] != "instruction"]
    if rest:
        out += "\n\n" + NOTES_HEADING + "\n" + "\n".join(f"- {n}" for n in rest)
    return out


def _used_seconds(tid: str, bot_id: str | None = None, since: float | None = None) -> float:
    ""

    since = _midnight() if since is None else since
    now = _now()
    q = "SELECT started, ended FROM team_turns WHERE team_id=? AND started>=?"
    args: tuple = (tid, since)
    if bot_id:
        q += " AND bot_id=?"
        args += (bot_id,)
    return sum((r["ended"] - r["started"]) if r["ended"] else min(now - r["started"], scheduler.turn_cap_sec)
               for r in _db().execute(q, args))


def _limited(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in _limit_words)


class Scheduler:
    ""


    def __init__(self):
        self.running: dict[tuple[str, str], asyncio.Task] = {}
        self.run_ids: dict[tuple[str, str], str] = {}
        self.current_team: dict[str, str] = {}
        self.active: dict[str, set[asyncio.Task]] = {}
        self._slots: asyncio.Semaphore | None = None
        self._bot_locks: dict[str, asyncio.Lock] = {}
        self._kick = None
        self.team_waiting = 0
        self._signups: dict[str, asyncio.Task] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.held = False
        self.max_running = MAX_RUNNING
        self.turn_cap_sec = TURN_CAP_SEC
        self.cooldown_sec = WORK_COOLDOWN_SEC
        self._stall_scan = 0.0
        self._due_scan = 0.0
        self._post_scan = 0.0

    def reset(self) -> None:
        for t in self.running.values():
            t.cancel()
        self.__init__()

    def slots(self) -> asyncio.Semaphore:
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.max_running)
        return self._slots

    def kick(self) -> None:
        if self._kick is not None:
            self._kick.set()

    def note(self, tid: str, bot_id: str, text: str, kind: str = "note", at: float | None = None) -> int | None:
        ""



        with store._lock:
            wid = _db().execute("INSERT INTO team_wakes (team_id, bot_id, kind, text, at) VALUES (?,?,?,?,?)",
                                (tid, bot_id, kind, text if kind in KEPT_WHOLE else text[:600], at or _now())).lastrowid
            if kind == "instruction":
                _db().execute("DELETE FROM team_wakes WHERE team_id=? AND bot_id=? AND kind='instruction' AND id NOT IN "
                              "(SELECT id FROM team_wakes WHERE team_id=? AND bot_id=? AND kind='instruction' "
                              "ORDER BY at DESC, id DESC LIMIT ?)", (tid, bot_id, tid, bot_id, INSTRUCTIONS_KEPT))
            _db().commit()
        return wid

    def pending(self, tid: str, bot_id: str) -> list[dict]:
        ""
        with store._lock:
            _db().execute("DELETE FROM team_wakes WHERE team_id=? AND bot_id=? AND tell_id IS NOT NULL AND "
                          "started=0 AND tell_id NOT IN (SELECT id FROM team_msgs WHERE delivered=0)", (tid, bot_id))
            _db().commit()
        return [dict(r) for r in _db().execute("SELECT * FROM team_wakes WHERE team_id=? AND bot_id=? ORDER BY at, id",
                                               (tid, bot_id))]

    def wake(self, tid: str, bot_id: str, text: str, kind: str = "note") -> None:
        self.note(tid, bot_id, text, kind=kind)
        self.kick()

    async def stop_team(self, tid: str) -> None:
        ""
        tasks = {task for key, task in self.running.items() if key[0] == tid} | self.active.get(tid, set())
        await self._stop(tasks)

    async def stop_all(self) -> None:
        ""
        tasks = set(self.running.values()) | {t for tasks in self.active.values() for t in tasks}
        await self._stop(tasks)

    async def _stop(self, tasks: set) -> None:
        tasks = {t for t in tasks if not t.done() and t is not asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def reconcile(self) -> None:
        ""
        now = _now()
        with store._lock:
            _db().execute("UPDATE team_turns SET ended=MIN(?, started + ?), outcome='lost' WHERE ended IS NULL",
                          (now, self.turn_cap_sec))
            _db().execute("UPDATE team_msgs SET delivered=0 WHERE id IN "
                          "(SELECT tell_id FROM team_wakes WHERE started=1)")
            _db().commit()

    async def run(self) -> None:
        self._kick = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.reconcile()
        try:
            recheck_needs_once()
        except Exception:
            log.exception("could not wake members to recheck their requests")
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("team scheduler tick failed")
            try:
                await asyncio.wait_for(self._kick.wait(), TICK_SEC)
            except TimeoutError:
                pass
            self._kick.clear()

    def _stalled(self) -> None:
        ""



        now = _now()
        if now - self._stall_scan < STALL_SCAN_SEC:
            return
        self._stall_scan = now
        seen = store.get_setting(STALL_SETTING, {}) or {}
        kept, woken = {}, {}
        for r in _db().execute("SELECT m.* FROM team_milestones m JOIN teams t ON t.id = m.team_id "
                               "WHERE m.state IN ('blocked','ready') AND t.state='active' ORDER BY m.id").fetchall():
            mark = kept[str(r["id"])] = dict(seen.get(str(r["id"])) or {"since": r["updated_at"]})
            answered = _db().execute("SELECT MAX(answered_at) FROM team_needs WHERE milestone_id=?",
                                     (r["id"],)).fetchone()[0]
            tid, owner = r["team_id"], r["owner"]
            if (mark.get("asked") or now - max(mark["since"], answered or 0) < STALL_SEC
                    or _declined_needs(tid, owner, r["id"]) or _skipped_while_blocked(tid, r["id"])
                    or _in_front(tid, owner, r["id"])):
                continue
            if not mark.get("woke"):
                mark["woke"] = now
                woken.setdefault((tid, owner), []).append(r)
            elif now - mark["woke"] >= STALL_ASK_SEC and not _asking(tid, owner, r):
                mark["asked"] = now
                spawn(self._cap_need(tid, owner, r, ""))
        for (tid, owner), rows in woken.items():
            self.wake(tid, owner, _stall_note(tid, owner, rows, kept), kind="stall")
        if kept != seen:
            store.set_setting(STALL_SETTING, kept)

    def _due_soon(self) -> None:
        ""

        now = _now()
        if now - self._due_scan < STALL_SCAN_SEC:
            return
        self._due_scan = now
        due: dict[str, list] = {}
        for r in _db().execute("SELECT n.* FROM team_needs n JOIN teams t ON t.id = n.team_id WHERE n.state='open' "
                               "AND n.raised_at IS NULL AND t.state='active' ORDER BY n.id").fetchall():
            at = need_due(r)
            day_end = at and datetime.fromtimestamp(at, home.zone()).replace(hour=23, minute=59, second=59).timestamp()

            if at and r["created_at"] < at - DUE_AHEAD_SEC <= now <= day_end:
                due.setdefault(r["team_id"], []).append((at, r))
        for tid, rows in due.items():
            rows.sort(key=lambda x: x[0])
            at, first = rows[0]
            with store._lock:
                _db().executemany("UPDATE team_needs SET raised_at=? WHERE id=?", [(now, r["id"]) for _, r in rows])
                _db().commit()
            line = due_line(rows)
            try:
                for bot, places in _ask_sign_ins(rows).items():
                    line += f" {_name(bot)} also needs you to sign in to {' and '.join(places)} before then."
            except Exception:
                log.exception("could not ask for the sign-ins a team's posts need")
            _push_need(tid, first["from_bot"], line)
            for _, r in rows:
                if _batch_of_posts(r):
                    self.wake(tid, r["from_bot"], f"{who.first()} hasn't answered \"{r['title']}\" and its date is close. "
                                                  f"{FILE_POSTS} Then withdraw the old request with team_need close={r['id']} "
                                                  "and a reason.", kind="answer")

    def _posts_due(self) -> None:
        ""

        now = _now()
        if now - self._post_scan < POST_SCAN_SEC:
            return
        self._post_scan = now
        for r in _db().execute("SELECT n.* FROM team_needs n JOIN teams t ON t.id = n.team_id WHERE n.kind='approve' "
                               "AND n.state='approved' AND n.woke_at IS NULL AND n.due_at <= ? AND t.state='active' "
                               "ORDER BY n.due_at, n.id", (now,)).fetchall():
            item = _post_of(r)
            if not item:
                continue
            with store._lock:
                woke = _db().execute("UPDATE team_needs SET woke_at=? WHERE id=? AND woke_at IS NULL",
                                     (now, r["id"])).rowcount
                _db().commit()
            if woke:
                self.wake(r["team_id"], r["from_bot"], post_now(item, r["from_bot"]), kind="answer")

    def _resume_due(self, now: float) -> None:
        for r in _db().execute("SELECT * FROM teams WHERE state='paused' AND paused_reason IN ('budget','plan_limit')"):
            if r["resume_at"] is not None and r["resume_at"] <= now:
                _move("team", r["id"], "active", paused_reason=None, resume_at=None)
                _changed(r["id"], "team", {"state": "active"})

    def plan(self) -> list[tuple[str, str, int | None]]:
        ""



        out = []
        active = [dict(r) for r in _db().execute("SELECT * FROM teams WHERE state='active' ORDER BY updated_at")]
        for t in active:
            exhausted = True
            for m in _members(t["id"]):
                if not m["ready"] or (t["id"], m["bot_id"]) in self.running:
                    exhausted = False
                    continue
                if _used_seconds(t["id"], m["bot_id"]) >= _budget_min(t) * 60:
                    continue
                exhausted = False
                waiting = bool(self.pending(t["id"], m["bot_id"]))
                rested = self._rested(t["id"], m["bot_id"]) >= self.cooldown_sec
                if not waiting and not rested:
                    continue
                r = _db().execute("SELECT id FROM team_milestones WHERE team_id=? AND owner=? AND state IN ('doing','todo') "
                                  "ORDER BY CASE state WHEN 'doing' THEN 0 ELSE 1 END, ord LIMIT 1",
                                  (t["id"], m["bot_id"])).fetchone()
                if r:
                    out.append((t["id"], m["bot_id"], r["id"]))
                elif waiting and rested:
                    out.append((t["id"], m["bot_id"], None))
            if exhausted and _members(t["id"]) and any(
                    _db().execute("SELECT 1 FROM team_milestones WHERE team_id=? AND state IN ('doing','todo')", (t["id"],))):
                pause_for(t["id"], "budget", _midnight() + 86400)
        return out

    def _rested(self, tid: str, bid: str) -> float:
        ""
        r = _db().execute("SELECT MAX(ended) AS e FROM team_turns WHERE team_id=? AND bot_id=? AND kind IN ('work','tell')",
                          (tid, bid)).fetchone()
        return _now() - r["e"] if r and r["e"] else 1e9

    def _refresh_signups(self) -> None:
        ""

        from . import sites
        for key in [k for k, t in self._signups.items() if t.done()]:
            self._signups.pop(key, None)
        stale = _now() - sites.SIGNUPS_TTL
        for r in _db().execute("SELECT * FROM teams WHERE site_id IS NOT NULL AND state IN ('active', 'paused') "
                               "AND COALESCE(signups_at, 0) < ?", (stale,)).fetchall():
            if r["id"] not in self._signups:
                self._signups[r["id"]] = asyncio.create_task(self._read_signups(dict(r)))

    async def _read_signups(self, t: dict) -> None:
        try:
            await refresh_signups(t)
        except Exception:
            log.warning("could not read the signups of %s", t["id"], exc_info=True)

    async def tick(self) -> None:
        if self.held:
            return
        try:
            settle_decided()
        except Exception:
            log.exception("could not settle decided approvals")
        self._resume_due(_now())
        try:
            self._stalled()
        except Exception:
            log.exception("could not look for stalled team work")
        try:
            self._due_soon()
        except Exception:
            log.exception("could not raise team requests that come due")
        try:
            self._posts_due()
        except Exception:
            log.exception("could not wake team members for approved posts")
        self._refresh_signups()
        free = self.max_running - sum(1 for t in self.running.values() if not t.done()) - self.team_waiting
        for key in [k for k, t in self.running.items() if t.done()]:
            self.running.pop(key, None)
        for tid, bid, mid in self.plan():
            if free <= 0:
                break
            tells = [n for n in self.pending(tid, bid) if n["tell_id"] is not None]
            self.running[(tid, bid)] = asyncio.create_task(
                self._tell(tid, bid, tells[0]) if tells else self._work(tid, bid, mid))
            free -= 1

    def _begin(self, tid: str, bid: str, kind: str) -> int:
        with store._lock:
            cur = _db().execute("INSERT INTO team_turns (team_id, bot_id, kind, started) VALUES (?,?,?,?)", (tid, bid, kind, _now()))
            _db().commit()
            return cur.lastrowid

    def _end(self, ledger: int, outcome: str) -> None:
        with store._lock:
            _db().execute("UPDATE team_turns SET ended=?, outcome=? WHERE id=?", (_now(), outcome, ledger))
            _db().commit()

    async def _turn(self, tid: str, bid: str, kind: str, text, lane: str,
                    interrupt: bool = True) -> tuple[str | None, str | None, str]:
        ""
        self.active.setdefault(tid, set()).add(asyncio.current_task())
        try:
            return await self._run_turn(tid, bid, kind, text, lane, interrupt)
        finally:
            self.active.get(tid, set()).discard(asyncio.current_task())

    async def _make_room(self, bid: str) -> None:
        ""


        me = asyncio.current_task()
        live = [(key, task) for key, task in self.running.items() if not task.done() and task is not me]
        victims = [task for key, task in live if key[1] == bid]
        if not victims and self.slots().locked() and live:
            victims.append(live[0][1])
        for task in victims:
            task.cancel()
        if victims:
            await asyncio.gather(*victims, return_exceptions=True)

    def working(self, bid: str) -> bool:
        ""
        return any(key[1] == bid and not task.done() for key, task in self.running.items())

    async def _run_turn(self, tid: str, bid: str, kind: str, text, lane: str,
                        interrupt: bool = True) -> tuple[str | None, str | None, str]:
        ""

        reply = error = None
        run_id = None
        outcome = "done"
        key = (tid, bid)
        waiting = lane == "team"
        if waiting:
            self.team_waiting += 1
        try:
            if waiting and interrupt:
                await self._make_room(bid)

            async with self._bot_locks.setdefault(bid, asyncio.Lock()), self.slots():
                if waiting:
                    self.team_waiting -= 1
                    waiting = False
                cap = self.turn_cap_sec
                if kind == "tell":
                    from . import team
                    t = _team_row(tid)
                    remaining = _budget_min(t) * 60 - _used_seconds(tid, bid)
                    if self.held or t["state"] != "active" or remaining <= 0:
                        return None, None, "deferred"
                    member = next((m for m in _members(tid) if m["bot_id"] == bid), None)
                    if member is None or not member["ready"]:
                        return None, None, "deferred"
                    cap = min(cap, team.TELL_CAP_SEC, remaining)
                if callable(text):
                    text = text()
                if kind == "tell" and text is None:
                    return None, None, "stale"
                self.current_team[bid] = tid
                ledger = self._begin(tid, bid, kind)
                try:
                    async with asyncio.timeout(cap):
                        async for ev in hermes.stream_turn(bid, text, lane=lane):
                            if ev["type"] == "run":
                                run_id = ev["id"]
                                self.run_ids[key] = run_id
                            elif ev["type"] == "reply":
                                reply = ev["text"]
                            elif ev["type"] == "error":
                                error = ev["text"]
                except TimeoutError:
                    outcome = "capped"
                    if run_id:
                        await hermes.stop(bid, run_id)
                except asyncio.CancelledError:
                    outcome = "stopped"
                    if run_id:
                        await hermes.stop(bid, run_id)
                    raise
                except Exception:
                    log.exception("team turn failed for %s", bid)
                    outcome = "failed"
                finally:
                    self.run_ids.pop(key, None)
                    self.current_team.pop(bid, None)
                    self._end(ledger, outcome if not error else "error")
                    from . import mac
                    mac.end_turn_grants(bid)
        finally:
            if waiting:
                self.team_waiting -= 1
        return reply, error, outcome

    async def _tell(self, tid: str, bid: str, wake: dict) -> None:
        from . import team
        mid = wake["tell_id"]
        claimed = False
        outcome = "failed"
        error = None

        def prompt():
            nonlocal claimed
            with store._lock:
                note = team.tell_note(mid)
                if note is None:
                    return None
                _db().execute("UPDATE team_wakes SET started=1 WHERE id=?", (wake["id"],))
                if not team._claim(mid):
                    return None
                claimed = True
            if wake["started"]:
                from . import waits
                note = waits.interrupted_note(note)
            t = _team_row(tid)
            folder = config.WORK_DIR / "teams" / tid
            return f"Your team: {t['name']}. Goal: {t['goal']}. Team folder: {folder}.\n\n{note}"

        try:
            with team.tell_origin(mid):
                reply, error, outcome = await self._turn(tid, bid, "tell", prompt, "work", interrupt=False)
            if claimed and outcome == "done" and not error:
                team.handled(mid, reply)
        finally:
            if claimed and (outcome != "done" or error):
                team._give_back(mid)
        if claimed or outcome == "stale":
            with store._lock:
                _db().execute("DELETE FROM team_wakes WHERE id=?", (wake["id"],))
                _db().commit()
        if error and _limited(error):
            pause_for(tid, "plan_limit", _now() + PLAN_LIMIT_RESUME_SEC)
        self.kick()

    async def team_turn(self, tid: str, bid: str, kind: str, text, interrupt: bool = True) -> str | None:
        ""


        reply, error, _ = await self._turn(tid, bid, kind, text, "team", interrupt)
        if error and _limited(error):
            pause_for(tid, "plan_limit", _now() + PLAN_LIMIT_RESUME_SEC)
        return reply

    async def _way_forward(self, tid: str, bid: str, row, reply: str) -> None:
        ""



        from . import wayforward
        if (_db().execute("SELECT 1 FROM team_needs WHERE team_id=? AND milestone_id=? AND state='open'",
                          (tid, row["id"])).fetchone() or _declined_needs(tid, bid, row["id"])):
            return
        first, reason = who.first(), reply or row["result"] or ""
        prompt = wayforward.PROMPT.format(
            zip=_name(bid), first=first, title=wayforward._flat(row["title"])[:200],
            body=wayforward._flat(row["done_when"])[:600] or "(nothing more)", reason=wayforward._flat(reason)[:1500],
            notes=f"- {wayforward._flat(row['result'])[:300]}" if row["result"] else "(none)",
            routes="\n".join(f"- {r.format(first=first)}" for r in wayforward.ROUTES))
        data = await wayforward._ask(prompt, None, 300, (bid,)) or {}
        untried = wayforward._flat(data.get("untried"))
        if untried:
            self.note(tid, bid, f"Before this goes to {first}, try another route: {untried[:400]}")
            return
        line, options = wayforward._sentence(data.get("in_the_way")), wayforward._options(data.get("options"))
        if not (line and options):
            found = wayforward.fallback(reason)
            line, options = found["ask"], found["options"]
        _insert_need(tid, bid, "decide", _title(f"Next step for {row['title']}"), line[:DETAIL_MAX], options, [], [],
                     row["id"])

    async def _cap_need(self, tid: str, bid: str, row, reply: str) -> None:
        if (_db().execute("SELECT 1 FROM team_needs WHERE team_id=? AND milestone_id=? AND state='open'",
                          (tid, row["id"])).fetchone() or _declined_needs(tid, bid, row["id"])):
            return
        title = _title(f"Next step for {row['title']}")
        detail = (f"I paused work on \"{row['title']}\". I could not produce a reliable explanation of what stopped "
                  "progress. Should I leave this paused or plan a smaller next step?")
        options = ["Leave this paused", "Plan a smaller next step"]
        try:
            t = _team_row(tid)
            remaining = _budget_min(t) * 60 - _used_seconds(tid, bid)
            if t["state"] == "active" and remaining > 0:
                related = [{k: n[k] for k in ("title", "detail", "state", "answer")}
                           for n in needs(tid) if n["milestone_id"] == row["id"]]
                text = CAP_QUESTION.format(person=who.first(), goal=t["goal"], title=row["title"],
                                           done_when=row["done_when"], result=row["result"], reply=reply[:1200],
                                           needs=json.dumps(related), name=_name(bid))
                async with asyncio.timeout(min(30, remaining)):
                    answer = await self.team_turn(tid, bid, "summary", text, interrupt=False)
                copy = _loads(answer, {})
                raw = [copy.get("title"), copy.get("detail")]
                choices = copy.get("options")
                if (all(isinstance(x, str) and x.strip() for x in raw) and isinstance(choices, list)
                        and 2 <= len(choices) <= 5 and all(isinstance(x, str) and x.strip() for x in choices)):
                    raw = [" ".join(x.split()) for x in raw]
                    choices = [" ".join(x.split()) for x in choices]
                    joined = " ".join(raw + choices)
                    forbidden = r"\b(turns?|milestones?|budgets?|tokens?|servers?)\b|https?://|(?:/[^\s/]+){2,}"
                    sentences = [s for s in re.split(r"(?<=[.!?])\s+", raw[1]) if s]
                    if (len(raw[0]) <= TITLE_MAX and len(raw[1]) <= DETAIL_MAX and len(sentences) <= 3
                            and all(len(x) <= 80 for x in choices) and len({x.casefold() for x in choices}) == len(choices)
                            and not any(x.casefold() in ("done", "not now") for x in choices)
                            and re.search(r"\bI\b|\bmy\b", raw[1], re.I)
                            and not re.search(forbidden, joined, re.I)
                            and not re.search(rf"(?<!\w){re.escape(_name(bid))}(?!\w)", joined, re.I)):
                        title, detail = raw
                        options = choices
        except Exception:
            log.warning("could not explain paused team work %s", row["id"], exc_info=True)
        finally:

            with store._lock:
                current = _db().execute("SELECT state FROM team_milestones WHERE id=?", (row["id"],)).fetchone()
                t = _db().execute("SELECT state FROM teams WHERE id=?", (tid,)).fetchone()
                existing = _db().execute("SELECT 1 FROM team_needs WHERE team_id=? AND milestone_id=? AND state='open'",
                                         (tid, row["id"])).fetchone()
                if (current and current["state"] == "blocked" and t and t["state"] not in ("deleting", "deleted", "merged")
                        and not existing and not _declined_needs(tid, bid, row["id"])):
                    _insert_need(tid, bid, "decide", title, detail, options, [], [], row["id"])

    async def _work(self, tid: str, bid: str, mid: int | None) -> None:
        ""
        try:
            t = _team_row(tid)
        except TeamsError:
            return
        r = None
        if mid is not None:
            r = _db().execute("SELECT * FROM team_milestones WHERE id=?", (mid,)).fetchone()
            if not r or r["state"] not in ("todo", "doing"):
                return
            if r["state"] == "todo":
                _move("milestone", mid, "doing")
                _changed(tid, "milestone", {"id": mid, "state": "doing"})
            with store._lock:
                _db().execute("UPDATE team_milestones SET turns=turns+1 WHERE id=?", (mid,))
                _db().commit()
        pending = self.pending(tid, bid)
        if r is None and not pending:
            return
        folder = config.WORK_DIR / "teams" / tid
        notes = _notes_text(pending, who.first()) + _open_needs_text(tid, bid)
        names = [m["name"] for m in _members(tid) if m["bot_id"] != bid]
        mates = MATES_LINE.format(names=", ".join(names)) if names else ""
        if r is not None:
            text = WORK_BRIEF.format(
                now=_now_line(),
                person=who.first(), team=t["name"], goal=t["goal"], mid=mid, title=r["title"],
                done_when=f" Done means: {r['done_when']}" if r["done_when"] else "",
                result=f" Where you left it: {r['result']}" if r["result"] else "",
                folder=folder, mates=mates, notes=notes)
        else:
            mine = [x for x in milestones(tid) if x["owner"] == bid]
            text = INSTRUCTION_BRIEF.format(
                now=_now_line(),
                person=who.first(), team=t["name"], goal=t["goal"], folder=folder, notes=notes, mates=mates,
                mine="\n".join(f"- [{x['id']}] {x['title']} ({x['state']})" + (f": {x['result']}" if x["result"] else "")
                               for x in mine) or "- (none)")
        try:
            reply, error, outcome = await self._turn(tid, bid, "work", text, "work")
        except asyncio.CancelledError:
            if r is not None:
                with store._lock:
                    _db().execute("UPDATE team_milestones SET turns=MAX(turns - 1, 0) WHERE id=?", (mid,))
                    _db().commit()
            raise
        if outcome == "done" and not error and pending:
            with store._lock:
                _db().execute(f"DELETE FROM team_wakes WHERE id IN ({','.join('?' * len(pending))})", [n["id"] for n in pending])
                _db().commit()
        if error and _limited(error):
            pause_for(tid, "plan_limit", _now() + PLAN_LIMIT_RESUME_SEC)
            if r is not None:
                self.note(tid, bid, "Your last turn stopped at the plan's limit; pick the milestone back up.")
            return
        line = " ".join((reply or "").split())
        if r is None:
            self.kick()
            return
        after = _db().execute("SELECT * FROM team_milestones WHERE id=?", (mid,)).fetchone()


        if after and after["state"] == "doing" and after["turns"] == MAX_TURNS_PER_MILESTONE:
            await self._way_forward(tid, bid, after, line)
        self.kick()


scheduler = Scheduler()

RECHECK_SETTING = "team_needs_rechecked"
RECHECK_NOTE = ("{person} may have settled some of your requests outside the team, in your own chat or by connecting an "
                "account. Check each one below against what you can reach now.")


def recheck_needs_once() -> None:
    ""



    if store.get_setting(RECHECK_SETTING):
        return
    for r in _db().execute("SELECT DISTINCT n.team_id, n.from_bot FROM team_needs n JOIN teams t ON t.id = n.team_id "
                           "WHERE n.state='open' AND n.kind IN ('decide','do') AND t.state NOT IN ('deleting','deleted','merged')"
                           ).fetchall():
        scheduler.note(r["team_id"], r["from_bot"], RECHECK_NOTE.format(person=who.first()))
    store.set_setting(RECHECK_SETTING, _now())


def stop_all_work(timeout: float = 60) -> None:
    ""

    scheduler.held = True
    loop = scheduler.loop
    if loop is None or not loop.is_running():
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run_coroutine_threadsafe(scheduler.stop_all(), loop).result(timeout)
    else:
        raise RuntimeError("stop_all_work blocks; call it from a thread off the hub's loop")


def release_work() -> None:
    scheduler.held = False
    scheduler.kick()


async def run() -> None:
    await scheduler.run()
