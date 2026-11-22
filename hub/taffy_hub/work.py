""





from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field

from . import board, config, consent, home, loopevents, mail, notices, openloops, store, teams

OTHER = "other"


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS work_projects (
            id TEXT PRIMARY KEY, body TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS work_sources (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
            account TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL,
            body TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS work_sources_project ON work_sources(project_id, active);
    """)

    if "home_id" not in {r[1] for r in c.execute("PRAGMA table_info(work_sources)")}:
        c.execute("ALTER TABLE work_sources ADD COLUMN home_id TEXT")


def _json(value: str | None) -> dict:
    try:
        out = json.loads(value or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _revision(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


@dataclass
class Source:
    id: str
    kind: str
    title: str
    bot_id: str
    at: float
    identity: dict = field(default_factory=dict)
    summary: str = ""
    section: str = "updates"
    date: float | None = None
    place: str = ""
    people: list = field(default_factory=list)
    links: list = field(default_factory=list)
    action: dict | None = None
    reference: dict = field(default_factory=dict)
    stale: bool = False
    creates_project: bool = False


    short_title: str = ""
    ask: str = ""
    also: list = field(default_factory=list)




    since: float | None = None

    blocked_at: float | None = None

    suggest: dict | None = None

    def payload(self) -> dict:
        data = dict(vars(self))
        data["revision"] = _revision(self._content(data, self.at if self.since is None else self.since))
        return data

    def legacy_revision(self) -> str:
        ""
        return _revision(self._content(dict(vars(self)), self.at))

    def _content(self, data: dict, at: float) -> dict:

        content = {k: v for k, v in data.items() if k not in ("stale", "short_title", "ask", "also", "since", "blocked_at",
                                                                  "suggest")}
        content["at"] = at
        if self.kind == "todo":


            content.pop("section", None)
        if self.kind in ("email", "text"):


            content["reference"] = {k: v for k, v in data["reference"].items() if k not in (
                "provider_ref", "message_id_header", "triage", "resolved", "was", "why", "fold", "mentions")}
            content["action"] = {k: v for k, v in (data["action"] or {}).items()
                                 if k not in ("provider_ref", "source_id", "source_path", "open_url")}
        return content


def _compatible(a: dict, b: dict) -> bool:
    if any(a.get(k) and b.get(k) and a[k] != b[k] for k in (*openloops.REFS, "person")):
        return False
    for key in ("task", "card", "wait", "kit"):
        if a.get(key) and a[key] == b.get(key):
            return True
    if a.get("account") and a.get("account") == b.get("account") and any(
            a.get(k) and a[k] == b.get(k) for k in ("thread", "message")):
        return True
    if a.get("calendar") and a.get("calendar") == b.get("calendar") and a.get("event") == b.get("event"):
        return bool(a.get("event"))
    return bool(a.get("subject") and a.get("person") and
                (a["subject"], a["person"]) == (b.get("subject"), b.get("person")))


def _same_card(a: Source, b: Source) -> bool:
    return any(source.reference.get("feed_id") and str(source.reference["feed_id"]) == other.identity.get("card")
               for source, other in ((a, b), (b, a)))


class _Matches:
    ""




    def __init__(self) -> None:
        self.pages: dict[tuple, list[str]] = {}
        self.always: list[str] = []

    @staticmethod
    def _keys(identity: dict, reference: dict, *, member: bool) -> list[tuple]:
        ""
        i = identity
        keys = [(k, i[k]) for k in ("task", "card", "wait", "kit") if i.get(k)]
        if i.get("account"):
            keys += [(k, i["account"], i[k]) for k in ("thread", "message") if i.get(k)]
        if i.get("calendar") and i.get("event"):
            keys.append(("calendar", i["calendar"], i["event"]))
        if i.get("subject") and i.get("person"):
            keys.append(("subject", i["subject"], i["person"]))

        if member:
            if reference.get("feed_id"):
                keys.append(("feed_id", str(reference["feed_id"])))
        else:
            if reference.get("feed_id"):
                keys.append(("card", str(reference["feed_id"])))
            if i.get("card"):
                keys.append(("feed_id", i["card"]))
        return keys

    def add(self, pid: str, source: Source) -> None:
        try:
            keys = self._keys(source.identity, source.reference, member=True)
            for k in keys:
                hash(k)
        except TypeError:
            self.always.append(pid)
            return
        for k in keys:
            self.pages.setdefault(k, []).append(pid)

    def candidates(self, source: Source) -> set[str]:
        out = set(self.always)
        try:
            for k in self._keys(source.identity, source.reference, member=False):
                out.update(self.pages.get(k, ()))
        except TypeError:
            return {pid for pids in self.pages.values() for pid in pids} | out
        return out


def _feed_sources() -> list[Source]:
    out = []
    for r in store.db().execute("SELECT * FROM feed WHERE kind IN ('project','choices','mail','result') ORDER BY id"):
        if r["bot_id"] not in config.BOTS_BY_ID or r["team_id"]:
            continue
        b, kind = _json(r["body"]), r["kind"]
        ident = {"task": str(r["task_id"])} if r["task_id"] else {"card": str(r["id"])}
        source = Source(f"feed:{r['id']}", kind, r["title"] or "", r["bot_id"],
                        r["changed_at"] or r["created_at"], ident,
                        reference={"feed_id": r["id"], "bot_id": r["bot_id"], "revision": _revision(b)},
                        since=r["created_at"] if kind in ("choices", "mail") else None)
        if kind == "project":
            kit = b.get("kit")
            if not isinstance(kit, dict) or b.get("state") == "dismissed":
                continue
            source.id = f"kit:{r['bot_id']}:{kit.get('id') or r['id']}"
            source.identity["card"] = source.id
            source.title = str(kit.get("title") or source.title)
            source.identity.update(kit=str(kit.get("id") or ""), subject=openloops._key(source.title))
            source.date = kit.get("due")
            source.creates_project = True
            source.links = [*kit.get("resources", []), *([kit["workspace"]] if kit.get("workspace") else [])]
            if any(link.get("email") and not mail._known(link["email"].get("account", "")) for link in source.links):
                continue
            for link in source.links:
                if email := link.get("email"):
                    source.identity.update(account=email.get("account", ""), message=str(email.get("id") or ""))
        elif kind in ("choices", "mail"):

            if b.get("state") != "open" or (kind == "mail" and b.get("work_source")):
                continue
            source.section = "needs_you"
            source.action = {"kind": "answer" if kind == "choices" else "review_draft",
                             "bot_id": r["bot_id"], "feed_id": r["id"]}
            source.title = str(b.get("title") or b.get("subject") or b.get("question") or source.title)
            if kind == "choices" and b.get("notice"):
                source.suggest = notices.suggestion(r["id"], b)
            if kind == "mail":
                account = str(b.get("account") or "")
                if account and not mail._known(account):
                    continue
                source.identity.update(account=account, message=str(b.get("in_reply_to") or ""))
                source.people = b.get("to", []) + b.get("cc", [])
        else:
            source.section = "made"
            source.action = {"kind": "result", "bot_id": r["bot_id"], "feed_id": r["id"]}
            source.reference["media"] = str(b.get("media") or "")
        out.append(source)
    return out


def _loop_sources() -> list[Source]:
    out = []
    for r in store.db().execute("SELECT * FROM open_loops WHERE status='open' ORDER BY id"):
        if r["bot_id"] not in config.BOTS_BY_ID:
            continue
        identity = openloops.identity_of(r["identity"], r["topic"])
        if identity.get("account") and not mail._known(identity["account"]):
            continue
        if card := identity.get("card"):
            feed = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='project'", (card,)).fetchone()
            if feed and feed["bot_id"] in config.BOTS_BY_ID:
                kit = _json(feed["body"]).get("kit") or {}
                identity["card"] = f"kit:{feed['bot_id']}:{kit.get('id') or card}"
        out.append(Source(f"loop:{r['id']}", "loop", r["topic"], r["bot_id"], r["content_at"] or r["updated_at"],
                          identity, r["summary"], people=([{"name": identity["person"]}] if identity.get("person") else []),
                          reference={"loop_id": r["id"]},
                          stale=r["updated_at"] < time.time() - openloops.STALE_DAYS * 86400, creates_project=True))
    return out


def _calendar_sources() -> list[Source]:
    out = []
    receipts = store.db().execute("SELECT * FROM open_loop_events WHERE kind='calendar_created' ORDER BY id").fetchall()
    calendars = {str(c["id"]): c for c in home.calendars()}
    cached = notices.calendar_events()
    for r in receipts:
        if r["bot_id"] not in config.BOTS_BY_ID:
            continue
        data = _json(r["data"])
        cal, event = str(data.get("calendar") or ""), str(data.get("event") or "")
        if cal not in calendars:
            continue
        label = calendars[cal].get("label")
        unique_label = label and sum(c.get("label") == label for c in calendars.values()) == 1
        matches = [e for e in cached if str(e["id"]) == event and
                   (e.get("calendar_id") == cal or e.get("calendar") == cal or
                    (unique_label and e.get("calendar") == label))]
        e = matches[0] if len(matches) == 1 else data
        date = notices._ts(e.get("start"))
        if not cal or not event or date is None or e.get("status") == "cancelled":
            continue
        out.append(Source(f"calendar:{cal}:{event}", "calendar", str(e.get("title") or data.get("title") or "Event"),
                          r["bot_id"], r["created"], {"calendar": cal, "event": event}, date=date,
                          place=str(e.get("location") or ""), people=e.get("attendees") or [],
                          reference={"calendar": cal, "event": event}, stale=len(matches) != 1))
    return out


def _team_sources() -> list[Source]:
    from . import teams
    rows = store.db().execute("SELECT n.* FROM team_needs n JOIN teams t ON t.id=n.team_id "
                              "WHERE n.state='open' AND (n.kind IN ('decide','do') OR "
                              "(n.kind='approve' AND n.request_ids='[]')) "
                              "AND t.state NOT IN ('deleting','deleted','merged') ORDER BY n.id")
    return [Source(f"team_need:{r['id']}", "team_need", r["title"], r["from_bot"], r["created_at"],
                   section="needs_you", action={"kind": "answer_team", "team_id": r["team_id"], "need_id": r["id"]},
                   reference={"team_id": r["team_id"], "need_id": r["id"],
                              "revision": _revision([r["title"], r["detail"], r["options"], r["items"]])})
            for r in rows if r["from_bot"] in config.BOTS_BY_ID and not teams.waits_for_its_date(r)]


LONG_TASK = "Working for over an hour, so it may be stuck."


def _board_sources() -> list[Source]:
    out = []
    long = {t["id"] for t in board.long_running(board.LONG_TASK_SEC)}
    for group, tasks in board.grouped().items():
        for t in tasks:
            owner = t.get("assignee") or "default"
            if owner not in config.BOTS_BY_ID:
                continue
            section = {"needs_you": "needs_you", "working": "in_progress", "done": "updates"}[group]
            out.append(Source(f"task:{t['id']}", "task", t["title"], owner,
                              t.get("completed_at") or t.get("started_at") or t["created_at"],
                              {"task": str(t["id"])},
                              (t.get("card") or {}).get("background") or t.get("summary") or t.get("block_reason")
                              or (LONG_TASK if t["id"] in long else ""), section,
                              action={"kind": "task", "task_id": t["id"]}, reference={"task_id": t["id"]},

                              ask=(t.get("card") or {}).get("ask", "") if (t.get("card") or {}).get("worded") else "",
                              blocked_at=(t.get("filed_at") or t["created_at"]) if t.get("status") == "blocked" else None))
    return out


def _approval_sources() -> list[Source]:
    return [Source(f"approval:{r['id']}", "approval", r.get("title") or r.get("reason") or "Review this request",
                   r["bot_id"], r.get("created") or 0,
                   {"task": str(r.get("task_id") or "")}, section="needs_you",
                   action={"kind": "review_approval", "request_id": r["id"]}, reference={"request_id": r["id"]})
            for r in consent.pending() if r["bot_id"] in config.BOTS_BY_ID]


def _visible(body: dict, known=None) -> bool:
    ""
    identity = body.get("identity") or {}
    account = identity.get("account")
    return body.get("bot_id") in config.BOTS_BY_ID and (not account or (known or mail._known)(account))


def _known_accounts():
    ""

    seen: dict = {}

    def known(account) -> bool:
        if account not in seen:
            seen[account] = mail._known(account)
        return seen[account]
    return known


def _mailboxes() -> str:
    ""
    try:
        return json.dumps(home.mail_accounts(), default=str, sort_keys=True)
    except Exception:
        return ""





_built: str | None = None


def _inputs(sources: list, failures: list, moves: dict) -> str:
    ""

    from . import made, workgroups
    c = store.db()
    tables = [tuple(c.execute(f"SELECT count(*), max(rowid) FROM {t}").fetchone()) for t in ("work_sources", "work_projects")]
    gathered = sorted(json.dumps(s.__dict__, default=str, sort_keys=True) for s in sources)

    now = time.time()
    ahead = sorted(s.date for s in sources if s.date is not None and s.date >= now)
    raw = json.dumps([str(config.HUB_DB), tables, gathered, sorted(failures), moves, ahead,
                      sorted((b, config.is_sandbox(b)) for b in config.BOTS_BY_ID), _mailboxes(),
                      workgroups.generation(), made._generation],
                     default=str, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def rebuild() -> dict:
    ""
    with store._lock:
        c = store.db()
        from . import meetprep, school, workmail, worktexts
        from . import remindermirror, todo
        sources = _feed_sources() + _loop_sources() + _calendar_sources() + _team_sources() + workmail.sources() + \
            worktexts.sources() + meetprep.sources() + school.sources() + todo.sources() + remindermirror.sources()
        failures = []
        for kind, producer in (("task", _board_sources), ("approval", _approval_sources)):
            try:
                sources.extend(producer())
            except (OSError, sqlite3.Error):
                failures.append(kind)
                sources.extend(Source(**{k: v for k, v in _json(r["body"]).items() if k != "revision"})
                               for r in c.execute("SELECT body FROM work_sources WHERE kind=? AND active=1", (kind,))
                               if _visible(_json(r["body"])))


        sources = [s for s in sources if not config.is_sandbox(s.bot_id)]
        from . import made, workprojects
        moves = workprojects.merges()
        key = _inputs(sources, failures, moves)
        global _built
        if key == _built:


            before = c.total_changes
            for s in sources:
                if s.since is not None and s.at != s.since:
                    _keep_marks(c, s, s.payload()["revision"])
            if c.total_changes != before:
                c.commit()
            workprojects.bring_back(c)
            workprojects.tidy(c)
            made.backfill(key)
            return {"failures": failures, "accounts": workmail.freshness()}

        previous = {r["id"]: r["home_id"] or r["project_id"] for r in c.execute("SELECT id,project_id,home_id FROM work_sources")}
        groups: dict[str, list[Source]] = {OTHER: []}
        courses = {_shared_title(s.title, None) for s in sources if s.kind == "course"}
        titled: dict[str, str] = {}
        merged: dict[str, str] = {}

        sources.sort(key=lambda s: (not s.creates_project, s.id not in previous, s.id))
        index = _Matches()
        for source in sources:
            near = index.candidates(source)
            matching = [pid for pid, members in groups.items() if pid != OTHER and pid in near and
                        any(_compatible(source.identity, m.identity) or _same_card(source, m) for m in members) and
                        all(not any(source.identity.get(k) and m.identity.get(k) and
                                    source.identity[k] != m.identity[k] for k in (*openloops.REFS, "person"))
                            for m in members)]
            old = previous.get(source.id)
            filed = source.reference.get("filed_project_id") if source.kind in ("email", "text", "meeting") else None
            filed = merged.get(filed, filed)




            title = _shared_title(source.title, courses) if source.creates_project else ""



            if source.kind == "calendar" and not source.creates_project and not matching and \
                    not (filed in groups and filed != OTHER):
                home_title = titled.get(_shared_title(source.title, courses))
                if not home_title:
                    continue
                matching = [home_title]
            by_title = not (filed in groups and filed != OTHER) and not matching and title in titled and \
                not any(source.identity.get(k) and m.identity.get(k) and source.identity[k] != m.identity[k]
                        for m in groups[titled.get(title, OTHER)] for k in openloops.REFS)
            pid = filed if filed in groups and filed != OTHER else matching[0] if matching else (
                titled[title] if by_title else old if source.creates_project and old not in groups else None)
            if by_title:

                for alias in (old, "project_" + _revision(source.id), "project_" + _revision([source.id, source.identity])):
                    if alias and alias != pid:
                        merged[alias] = pid
            if not pid or pid == OTHER:
                pid = "project_" + _revision(source.id) if source.creates_project else OTHER
                if source.creates_project and pid in groups:
                    pid = "project_" + _revision([source.id, source.identity])
            groups.setdefault(pid, []).append(source)
            index.add(pid, source)
            if title and pid != OTHER:
                titled.setdefault(title, pid)
        home = {s.id: pid for pid, members in groups.items() for s in members}
        for pid in [p for p in groups if p in moves]:
            if (target := workprojects.resolve(pid, moves, groups)) != pid:
                groups[target].extend(groups.pop(pid))
        try:
            c.execute("UPDATE work_sources SET active=0")
            c.execute("UPDATE work_projects SET active=0")
            for pid, members in groups.items():
                body = _project_body(pid, members)
                c.execute("INSERT INTO work_projects(id,body,active) VALUES(?,?,1) "
                          "ON CONFLICT(id) DO UPDATE SET body=excluded.body,active=1", (pid, json.dumps(body)))
                for s in members:
                    payload = s.payload()
                    _keep_marks(c, s, payload["revision"])
                    c.execute("INSERT INTO work_sources(id,project_id,home_id,kind,account,revision,body,active) "
                              "VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET project_id=excluded.project_id,"
                              "home_id=excluded.home_id,kind=excluded.kind,account=excluded.account,"
                              "revision=excluded.revision,body=excluded.body,active=1",
                              (s.id, pid, home[s.id], s.kind, s.identity.get("account", ""), payload["revision"],
                               json.dumps(payload)))
            c.commit()
        except Exception:
            c.rollback()
            _built = None
            raise
        _built = _inputs(sources, failures, moves)
        workprojects.bring_back(c)
        workprojects.tidy(c)
        made.backfill(_built)
        return {"failures": failures, "accounts": workmail.freshness()}


def _keep_marks(c: sqlite3.Connection, source: Source, revision: str) -> None:
    ""


    if source.since is None or source.at == source.since:
        return
    old = source.legacy_revision()
    for column in ("handled_revision", "last_read_revision"):
        c.execute(f"UPDATE work_user_state SET {column}=? WHERE id=? AND {column}=?", (revision, source.id, old))


def _shared_title(title: str, courses: set[str] | None) -> str:
    ""



    from . import school
    codes = {"course " + re.sub(r"[ -]", "", code) for code in school.COURSE.findall(title)}
    return codes.pop() if len(codes) == 1 and (courses is None or codes <= courses) else openloops._key(title)


def _project_body(pid: str, members: list[Source]) -> dict:
    dates = [s.date for s in members if s.date is not None]
    upcoming = [d for d in dates if d >= time.time()]
    previous = store.db().execute("SELECT body FROM work_projects WHERE id=?", (pid,)).fetchone()
    primary = _json(previous["body"]).get("primary_source_id") if previous else None

    owner = next((s for s in members if s.kind == "course"), None)
    owner = owner or next((s for s in members if s.creates_project and s.id == primary), None)
    owner = owner or next((s for s in members if s.creates_project), None)
    members = sorted(members, key=lambda s: s.id)
    return {"id": pid, "title": owner.title if owner else "Other", "date": min(upcoming or dates, default=None),
            "place": next((s.place for s in members if s.place), ""), "bot_id": owner.bot_id if owner else None,
            "primary_source_id": owner.id if owner else None,
            "people": _unique([p for s in members for p in s.people]),
            "sources": [{"id": s.id, "kind": s.kind, **s.reference} for s in members],
            "revision": _revision([s.payload()["revision"] for s in members]),
            "stale": bool(members) and all(s.stale for s in members)}


def _unique(items: list[dict]) -> list[dict]:
    return list({json.dumps(i, sort_keys=True): i for i in items}.values())


def _sources() -> list[dict]:
    from . import workreply, workstate
    out = []
    known = _known_accounts()
    for r in store.db().execute("SELECT * FROM work_sources WHERE active=1"):
        body = _json(r["body"])
        if _visible(body, known):
            if body.get("kind") == "email":
                body["reply_draft"] = workreply.public(body)
                body["reply_wanted"] = workreply.wanted(body)
            out.append(workstate.decorate({**body, "project_id": r["project_id"]}))
    return out


def projects_for_model() -> list[dict]:
    ""



    from . import shortreply
    rows = [_json(r["body"]) for r in store.db().execute("SELECT body FROM work_projects WHERE active=1 AND id!=?",
                                                         (OTHER,))]
    return [p for p in rows if not shortreply.withheld(p.get("bot_id"))]


def zips_of(projects: list[dict]) -> tuple[str, ...]:
    ""


    ids = [str(p.get("id")) for p in projects or () if p.get("id")]
    if not ids:
        return ()
    marks = ",".join("?" * len(ids))
    rows = [_json(r["body"]) for r in store.db().execute(f"SELECT body FROM work_projects WHERE id IN ({marks})", ids)]
    return tuple(sorted({str(p.get("bot_id")) for p in rows if config.is_private(p.get("bot_id"))}))


def overview() -> dict:
    from . import asktitles, made, meetprep, todogate, workdue, workgroups, workprojects, workstate
    started = workgroups.generation()
    gate_started = todogate._generation
    status = rebuild()
    with store._lock:
        sources = _sources()
        projects = [workstate.decorate(_json(r["body"])) for r in store.db().execute("SELECT body FROM work_projects WHERE active=1")]
        groups = workprojects.annotate(projects, sources)
        projects.sort(key=lambda p: (p["id"] == OTHER, p["date"] is None, p["date"] or 0, p["title"].casefold()))

        archived = [p for p in projects if p.get("archived_at")]
        projects = [p for p in projects if not p.get("archived_at")]


        needs, cleared = workdue.arrange(todogate.screen([s for s in sources if s["section"] == "needs_you" and
                                                          workstate.visible(s)], projects, gate_started, every=sources), projects)
        needs = asktitles.add(todogate.words(needs, projects))



        needs, now_count = workgroups.add(needs, started=started)
        workgroups.project_lines(projects, sources, started=started)
        return {"needs_you": needs, "now_count": now_count, "cleared": cleared, "done_today": done_today(sources),
                "projects": projects, "archived_projects": archived, **groups,
                "in_progress": [s for s in sources if s["section"] == "in_progress"],
                "recently_made": made.listing()["items"],
                "meetings": meetprep.upcoming(),
                "freshness": status}


def for_app(out: dict) -> dict:
    ""


    return {**out, **{k: [{**p, "sources": []} for p in out.get(k, [])] for k in ("projects", "archived_projects")}}


def done_today(sources: list[dict], now: float | None = None) -> list[dict]:
    ""


    from datetime import datetime

    from . import workstate
    start = datetime.fromtimestamp(now or time.time(), home.zone()).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    out = []
    for s in sources:
        if s["kind"] == "task" and s["section"] == "updates":
            at = s.get("at") or 0
        elif s["user_state"]["done"]:
            at = workstate.done_at(s["id"]) or 0
        else:
            continue
        if at >= start:
            done = {"id": s["id"], "title": s.get("ask") or s.get("short_title") or s["title"], "bot_id": s["bot_id"],
                    "at": at, "kind": s["kind"]}
            if s["user_state"]["done"]:


                from . import autofinish
                done["undo"] = {"expected_revision": s["user_state"]["revision"], "source_revision": s["revision"]}
                if why := autofinish.by_itself(s["id"]):
                    done["why"] = why
            out.append(done)
    return sorted(out, key=lambda d: d["at"], reverse=True)


def project_detail(project_id: str) -> dict | None:
    from . import made, meetprep, todogate, workdue, workgroups, workprojects, workstate
    rebuild()
    with store._lock:
        c = store.db()
        active = {r["id"] for r in c.execute("SELECT id FROM work_projects WHERE active=1")}

        project_id = workprojects.resolve(project_id, workprojects.merges(), active)
        row = c.execute("SELECT body FROM work_projects WHERE id=? AND active=1", (project_id,)).fetchone()
        if not row:
            return None
        every = _sources()
        sources = [s for s in every if s["project_id"] == project_id]
        project = workstate.decorate(_json(row["body"]))
        workprojects.annotate([project], every)
        workgroups.project_lines([project], every)
        made_page = made.listing(project_id=project_id)
        return {**project, "updates": sorted([s for s in sources if s["section"] == "updates" or
                                                                           (s["kind"] in ("email", "mail") and s["user_state"]["done"])],
                                                                      key=lambda s: -s["at"]),
                "needs_you": workgroups.add(todogate.words(workdue.arrange(todogate.screen(
                    [s for s in sources if s["section"] == "needs_you" and workstate.visible(s)], [project]),
                    [project])[0], [project]))[0],
                "handled": [s for s in sources if s["section"] == "needs_you" and not workstate.visible(s)],
                "in_progress": [s for s in sources if s["section"] == "in_progress"],
                "made": made_page["items"], "made_next_cursor": made_page["next_cursor"],
                "prep": meetprep.prep(project_id),
                "links_and_files": _unique([link for s in sources for link in s["links"]])}
