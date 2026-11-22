""
























from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import config, lineage, maccopy, mail, openloops, resultcard, safe_files, store, uploads, work, zipfiles

_generation = 0


PREVIEWS_PER_SCAN = 3
VIDEO_TYPES = (".mp4", ".mov", ".m4v")
PREVIEW_KINDS = (".html", ".htm", ".pdf", *VIDEO_TYPES)
PREVIEW_HEIGHT = 960
PAGE_MAX = 5 * 1024 * 1024
PDF_MAX = 50 * 1024 * 1024
VIDEO_MAX = 300 * 1024 * 1024

SHELVES = {"pages": "Pages", "videos": "Videos", "pictures": "Pictures", "drafts": "Drafts", "files": "Files",
           "reports": "Reports"}


_CODE_FOLDERS = {"repos", "vendor", "node_modules"}

REPORT_DAYS = 90

_WRITES = ("write_file", "patch", "share_result")
_renderer = None


_LOCAL_ASSET = re.compile(r"""<(?:link|img|script)\b[^>]*\b(?:href|src)\s*=\s*["']?(?![a-z][a-z0-9+.-]*:|//|#)[^"'\s>]""",
                          re.I)


SHOWN_BEFORE_SEC = 30 * 60
SHOWN_AFTER_SEC = 5 * 60
_DATE = re.compile(r"(?<!\d)(?:19|20)\d{2}[-_. ]?[01]\d[-_. ]?[0-3]\d(?:[-_ T]?\d{4}(?:\d{2})?)?(?!\d)")

_GENERIC = {"index", "main", "readme", "untitled", "output", "final", "page", "document"}

_FILLER = {"a", "an", "and", "the", "of", "for", "to", "re", "fwd", "fw", "with", "my", "your", "on", "in", "at", "by",
           "from", "prep", "plan", "notes", "note", "draft", "page", "file"}


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS work_made (
            id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, kind TEXT NOT NULL,
            project_id TEXT NOT NULL, body TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS work_made_project ON work_made(project_id,active);
        CREATE TABLE IF NOT EXISTS work_made_scans (bot_id TEXT PRIMARY KEY, last_success REAL NOT NULL);
    """)


def cancel_scans() -> None:
    global _generation
    _generation += 1





_changes = 0


def _changed(c: sqlite3.Connection, before: int) -> None:
    global _changes
    if c.total_changes != before:
        _changes += 1


def _cards() -> tuple:
    ""

    return tuple(store.db().execute(
        "SELECT count(*), max(id), max(COALESCE(changed_at, created_at)) FROM feed "
        "WHERE kind IN ('result','mail','project') AND team_id IS NULL").fetchone())


def item_id(bot: str, kind: str, key: str) -> str:
    return "made_" + work._revision([bot, kind, key])


def _words(text: str) -> str:
    ""
    text = " ".join(re.sub(r"[-_.]+", " ", _DATE.sub(" ", text)).split())
    return text[:1].upper() + text[1:]


def title_of(file_id: str) -> str:
    rel = PurePosixPath(file_id)
    title = _words(rel.stem)
    if (not title or title.casefold() in _GENERIC) and len(rel.parts) > 1:
        title = _words(rel.parent.name) or title
    return title or rel.name


def _file_title(file_id: str) -> str:
    ""


    suffix = PurePosixPath(file_id).suffix
    title = title_of(file_id)
    return title if title.endswith(suffix) else title + suffix



_INITIALS = re.compile(r"\b([a-z])[.&]\s*([a-z])\b\.?")

_TEST_SHOT = re.compile(r"(?:^|[-_. ])test\d*(?:[-_. ]|$)", re.I)


def _tokens(text: str) -> list[str]:
    ""
    return re.findall(r"[a-z]+|\d+", _INITIALS.sub(r"\1\2", _DATE.sub(" ", text).casefold()))


def _terms(*texts: str) -> frozenset[str]:
    ""
    return frozenset(w for w in _tokens(" ".join(texts)) if w not in _FILLER and (len(w) > 1 or w.isdigit()))


def _runs(tokens: list[str], longest: int = 6) -> set[str]:
    ""
    return {"".join(tokens[i:j]) for i in range(len(tokens)) for j in range(i + 1, min(len(tokens), i + longest) + 1)}


def name_runs(title: str) -> list[list[str]]:
    ""

    out, run = [], []
    for word in [*re.split(r"[\s()\[\],:;]+", title), ""]:
        if word and any(ch.isupper() or ch.isdigit() for ch in word):
            run.append(word)
            continue
        if len(run) >= 2:
            out.append(_tokens(" ".join(run)))
        run = []
    return out


def _names(title: str) -> set[str]:
    ""
    return {"".join(words) for words in name_runs(title)}


def _courses(tokens: list[str]) -> set[str]:
    ""
    return {a + b for a, b in zip(tokens, tokens[1:]) if a.isalpha() and 2 <= len(a) <= 4 and b.isdigit() and len(b) == 3}


@dataclass
class Filing:
    ""
    id: str
    title: str
    subjects: list[frozenset[str]]
    names: set[str]
    courses: set[str]
    vocabulary: frozenset[str]
    zips: set[str]
    key: str = ""

    def __post_init__(self) -> None:
        self.key = openloops._key(self.title)


def _filed(item: dict, projects: list[Filing]) -> str:
    ""








    title, suffix = item["title"], PurePosixPath(item.get("name") or "").suffix
    texts = (title[:-len(suffix)] if suffix and title.endswith(suffix) else title,
             PurePosixPath(item.get("name") or "").stem, PurePosixPath(item["backing"].get("file_id") or "").stem)
    words, tokens = _terms(*texts), [_tokens(t) for t in texts]
    runs = set().union(*map(_runs, tokens))
    courses = set().union(*map(_courses, tokens))
    key = openloops._key(item["title"])
    best: dict[str, tuple[int, int]] = {}
    for p in projects:
        if p.key == key:
            score = (4, len(words))
        elif subset := max((len(t) for t in p.subjects if len(t) >= 2 and t <= words), default=0):
            score = (3, subset)
        elif p.names & runs or len(courses) == 1 and courses <= p.courses:
            score = (2, 0)
        elif item["bot_id"] in p.zips and len(words) >= 3 and words <= p.vocabulary and \
                any(words & t for t in p.subjects):
            score = (1, len(words))
        else:
            continue
        best[p.id] = max(best.get(p.id, (0, 0)), score)
    top = max(best.values(), default=None)
    winners = [pid for pid, score in best.items() if score == top]
    if len(winners) > 1:
        winners = [p.id for p in projects if p.id in winners and item["bot_id"] in p.zips]
    return winners[0] if len(winners) == 1 else work.OTHER


def _working(file_id: str) -> bool:
    ""

    rel = PurePosixPath(file_id)

    if rel.parts[:1] in ((maccopy.FOLDER,), ("Uploads",), ("Downloads",), ("mail",), ("handoff",)):
        return True
    if any(p.casefold() in _CODE_FOLDERS or "backup" in p.casefold() for p in rel.parts[:-1]):
        return True
    return uploads.kind_of(rel.name) == "image" and bool(_TEST_SHOT.search(rel.stem))


def _base(bot: str, kind: str, key: str, title: str, at: float, backing: dict) -> dict:
    return {"id": item_id(bot, backing["type"], key), "kind": kind, "title": title, "bot_id": bot,
            "project_id": work.OTHER, "project_title": "Other", "people": [], "date": None,
            "created_at": at, "updated_at": at, "backing": backing, "source_refs": []}


def _save(c: sqlite3.Connection, item: dict) -> None:
    before = c.total_changes
    old = c.execute("SELECT body FROM work_made WHERE id=?", (item["id"],)).fetchone()
    if old:
        previous = work._json(old["body"])
        item["created_at"] = previous["created_at"]
        item["updated_at"] = max(previous["updated_at"], item["updated_at"])
        if previous.get("routine"):
            item.setdefault("routine", previous["routine"])
    c.execute("INSERT INTO work_made(id,bot_id,kind,project_id,body,active) VALUES(?,?,?,?,?,1) "
              "ON CONFLICT(id) DO UPDATE SET bot_id=excluded.bot_id,kind=excluded.kind,project_id=excluded.project_id,"
              "body=excluded.body,active=1 WHERE work_made.body!=excluded.body OR work_made.active=0",
              (item["id"], item["bot_id"], item["kind"], item["project_id"], json.dumps(item)))
    _changed(c, before)


def _file_id(bot: str, target: str) -> str | None:
    path = Path(target.replace("~/", str(config.HOME) + "/", 1)) if target.startswith("~/") else Path(target)
    try:
        rel = PurePosixPath(path.relative_to(zipfiles.made_root(bot)).as_posix())
    except ValueError:
        return None
    if ".." in rel.parts or not zipfiles._shown(rel) or zipfiles._sent(rel):
        return None
    return rel.as_posix()


_backfilled: tuple | None = None


def backfill(sources_key: str | None = None) -> None:
    ""

    global _backfilled
    with store._lock:
        c = store.db()
        reads = (sources_key, _changes, _generation, _cards(), sorted(config.BOTS_BY_ID), work._mailboxes())
        if sources_key is not None and reads == _backfilled:
            return
        known = work._known_accounts()
        projects = {r["id"]: work._json(r["body"]) for r in c.execute("SELECT * FROM work_projects WHERE active=1")}
        source_projects = {r["id"]: r["project_id"] for r in c.execute("SELECT id,project_id FROM work_sources WHERE active=1")}
        placements = {r["id"]: r["project_id"] for r in c.execute("SELECT id,project_id FROM work_sources")}
        items = {}
        for r in c.execute("SELECT body FROM work_made WHERE active=1"):
            item = work._json(r["body"])
            if item["backing"]["type"] == "file" and item["bot_id"] in config.BOTS_BY_ID and \
                    not _working(item["backing"]["file_id"]):
                item.update(project_id=work.OTHER, project_title="Other", people=[], date=None, source_refs=[],
                            title=_file_title(item["backing"]["file_id"]), name=PurePosixPath(item["backing"]["file_id"]).name)
                items[item["id"]] = item
        for r in c.execute("SELECT * FROM feed WHERE kind IN ('result','mail') AND team_id IS NULL ORDER BY id"):
            bot, b = r["bot_id"], work._json(r["body"])
            if bot not in config.BOTS_BY_ID:
                continue
            if r["kind"] == "result":
                media = str(b.get("media") or "")
                if not resultcard.MEDIA_ID.fullmatch(media):
                    continue
                name, titled = str(b.get("name") or ""), str(b.get("title") or "")
                item = _base(bot, "page" if b.get("kind") == "site" else "file", media,
                             titled or name or r["title"] or "File", r["created_at"],
                             {"type": "result", "media": media, "feed_id": r["id"]})
                item["display_title"] = titled or (title_of(name) if name else item["title"])
                item["action"] = {"kind": "result", "bot_id": bot, "feed_id": r["id"], "media": media}
                item["name"] = name

                item["thumbnail"] = {"type": "result", "media": media} if b.get("preview") else None
            else:
                account = str(b.get("account") or "")
                if account and not known(account):
                    continue
                item = _base(bot, "draft", str(r["id"]), str(b.get("subject") or r["title"] or "Draft"), r["created_at"],
                             {"type": "draft", "feed_id": r["id"], "account": account})
                item["action"] = {"kind": "review_draft", "bot_id": bot, "feed_id": r["id"]}
                item["people"] = b.get("to", []) + b.get("cc", [])
            item["updated_at"] = r["changed_at"] or r["created_at"]
            source_id = f"feed:{r['id']}"
            pid = source_projects.get(source_id)
            if not pid and r["kind"] == "mail":

                pid = placements.get(source_id) or placements.get(str(b.get("work_source") or ""))
            item["project_id"] = pid if pid in projects else work.OTHER
            ref = {"kind": r["kind"], "feed_id": r["id"], "bot_id": bot}
            if previous := items.get(item["id"]):
                previous["source_refs"].append(ref)
                if previous["project_id"] == work.OTHER:
                    previous["project_id"] = item["project_id"]
            else:
                item["source_refs"] = [ref]
                items[item["id"]] = item
        for r in c.execute("SELECT * FROM feed WHERE kind='project' AND team_id IS NULL ORDER BY id"):
            b, bot = work._json(r["body"]), r["bot_id"]
            kit = b.get("kit")
            if bot not in config.BOTS_BY_ID or b.get("state") == "dismissed" or not isinstance(kit, dict):
                continue
            source_id = f"kit:{bot}:{kit.get('id') or r['id']}"
            if source_id not in source_projects:
                continue
            for resource in [*kit.get("resources", []), *([kit["workspace"]] if kit.get("workspace") else [])]:
                if resource.get("kind") != "file":
                    continue
                file_id = _file_id(bot, str(resource.get("target") or ""))
                item = items.get(item_id(bot, "file", file_id)) if file_id else None
                if item:
                    item["project_id"] = source_projects.get(source_id, work.OTHER)
                    item["source_refs"].append({"kind": "project", "feed_id": r["id"], "bot_id": bot, "kit_id": kit.get("id")})
        subjects: dict[str, list[str]] = {}
        vocabulary: dict[str, set[str]] = {}
        zips: dict[str, set[str]] = {pid: {p.get("bot_id")} for pid, p in projects.items()}
        for r in c.execute("SELECT project_id,kind,body FROM work_sources WHERE active=1"):
            body = work._json(r["body"])
            if r["project_id"] not in projects:
                continue
            zips[r["project_id"]].add(body.get("bot_id"))
            subject = (body.get("identity") or {}).get("subject")
            if subject and r["kind"] in ("project", "loop", "calendar"):
                subjects.setdefault(r["project_id"], []).append(subject)
            if r["kind"] == "loop":
                vocabulary.setdefault(r["project_id"], set()).update(_terms(body.get("summary") or ""))
        named = []
        for pid, p in projects.items():
            if pid == work.OTHER:
                continue
            title = p.get("title") or ""
            said = [title, *subjects.get(pid, [])]
            mine = [_terms(t) for t in said]
            named.append(Filing(pid, title, mine, _names(title), set().union(*(_courses(_tokens(t)) for t in said)),
                                frozenset(vocabulary.get(pid, set()).union(*mine)), zips[pid] - {None}))
        for item in items.values():
            if item["project_id"] == work.OTHER:
                item["project_id"] = _filed(item, named)
        try:
            c.execute("UPDATE work_made SET active=0 WHERE json_extract(body,'$.backing.type')!='file' "
                      "OR json_extract(body,'$.backing.file_id') LIKE ?", (maccopy.FOLDER + "/%",))

            c.executemany("UPDATE work_made SET active=0 WHERE id=?", [
                (r["id"],) for r in c.execute("SELECT id,json_extract(body,'$.backing.file_id') AS file_id FROM work_made "
                                              "WHERE active=1").fetchall()
                if r["file_id"] and _working(r["file_id"])])
            for item in items.values():
                project = projects.get(item["project_id"], {}) if item["project_id"] != work.OTHER else {}
                item["project_title"] = project.get("title") or "Other"
                item["date"] = project.get("date")
                item["people"] = work._unique(item["people"] + project.get("people", []))
                _save(c, item)
            c.commit()
        except Exception:
            c.rollback()
            _backfilled = None
            raise

        _backfilled = (sources_key, _changes, _generation, _cards(), sorted(config.BOTS_BY_ID),
                       work._mailboxes()) if sources_key is not None else None


def _job(session_id: str) -> str:
    ""
    parts = session_id.split("_")
    return parts[1] if len(parts) > 2 and parts[0] == "cron" else session_id


def scheduled_files(bot: str) -> dict[str, str]:
    ""


    path = lineage._state_db(bot)
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            rows = conn.execute(
                "SELECT s.id, m.tool_calls FROM sessions s JOIN messages m ON m.session_id = s.id "
                "WHERE s.source = 'cron' AND s.started_at >= ? AND m.tool_calls IS NOT NULL "
                "ORDER BY s.started_at LIMIT 20000", (time.time() - REPORT_DAYS * 86400,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for session_id, calls in rows:
        try:
            calls = json.loads(calls)
        except (TypeError, ValueError):
            continue
        for call in calls if isinstance(calls, list) else []:
            f = (call.get("function") or call) if isinstance(call, dict) else {}
            if not isinstance(f, dict) or f.get("name") not in _WRITES:
                continue
            try:
                args = json.loads(f.get("arguments") or "{}") if isinstance(f.get("arguments"), str) else f.get("arguments")
            except ValueError:
                continue
            if isinstance(args, dict) and (file_id := _file_id(bot, str(args.get("path") or ""))):
                out[file_id] = _job(str(session_id))
    return out


def scan_files() -> None:
    ""
    generation = _generation
    for bot in tuple(config.BOTS_BY_ID):
        files = zipfiles.listing(bot, limit=None)
        reports = scheduled_files(bot)
        with store._lock:
            if generation != _generation:
                return
            if bot not in config.BOTS_BY_ID:
                continue
            c = store.db()
            try:
                for f in files:
                    erased_at = store.get_setting("openloops_waits_erased_at", 0) or 0
                    if f["source"] != "made" or f["modified"] <= erased_at:
                        continue
                    if f.get("checkout") or _working(f["id"]):
                        before = c.total_changes
                        c.execute("UPDATE work_made SET active=0 WHERE id=?", (item_id(bot, "file", f["id"]),))
                        _changed(c, before)
                        continue
                    kind = "page" if PurePosixPath(f["id"]).suffix.lower() in (".html", ".htm") else "file"
                    item = _base(bot, kind, f["id"], _file_title(f["id"]), f["modified"], {"type": "file", "file_id": f["id"]})
                    item["name"] = f["name"]
                    item["action"] = {"kind": "file", "bot_id": bot, "file_id": f["id"]}
                    item["size"] = f["size"]
                    if routine := reports.get(f["id"]):
                        item["routine"] = routine
                    _save(c, item)
                c.execute("INSERT INTO work_made_scans(bot_id,last_success) VALUES(?,?) "
                          "ON CONFLICT(bot_id) DO UPDATE SET last_success=excluded.last_success", (bot, time.time()))
                c.commit()
            except Exception:
                c.rollback()
                raise
    if generation == _generation:
        work.rebuild()
    if generation == _generation:
        previews(generation=generation)


def _public(item: dict, known=None) -> dict | None:
    ""
    bot, backing = item["bot_id"], item["backing"]
    account = backing.get("account")
    if bot not in config.BOTS_BY_ID or (account and not (known or mail._known)(account)):
        return None
    available = False
    unavailable_reason = "This item is no longer available."
    if backing["type"] == "file":
        file = zipfiles.open_file(bot, backing["file_id"])
        if file:
            age = time.time() - os.fstat(file.fileno()).st_mtime
            available = age >= zipfiles.SETTLE_SEC or age < 0
            if not available:
                unavailable_reason = "This file is still being saved."
            file.close()
    else:
        feed = store.db().execute("SELECT * FROM feed WHERE id=? AND bot_id=? AND team_id IS NULL",
                                  (backing["feed_id"], bot)).fetchone()
        if not feed:
            return None
        b = work._json(feed["body"])
        if backing["type"] == "draft":
            if feed["kind"] != "mail" or str(b.get("account") or "") != backing["account"]:
                return None
            available = b.get("state") in ("open", "sending", "sent")
        else:
            if feed["kind"] != "result" or b.get("media") != backing["media"]:
                return None
            path = resultcard.media_dir() / f"{backing['media']}.file"
            try:
                with safe_files.open_file(str(path), (resultcard.media_dir(),), protected=None):
                    available = True
            except (OSError, safe_files.Refused):
                pass
    return {**item, "availability": "available" if available else "unavailable",
            "unavailable_reason": None if available else unavailable_reason,
            "action": item["action"] if available else None}


def get(item_id: str) -> dict | None:
    row = store.db().execute("SELECT body FROM work_made WHERE id=? AND active=1", (item_id,)).fetchone()
    return _public(work._json(row["body"])) if row else None


class CursorError(ValueError):
    pass


def _compact(item: dict) -> dict:
    return {k: item.get(k) for k in ("id", "kind", "title", "display_title", "name", "bot_id", "made_by", "created_at",
                                     "action")}


def _shelf(item: dict) -> str:
    ""
    if item["kind"] == "draft":
        return "drafts"
    suffix = PurePosixPath(item.get("name") or item["backing"].get("file_id") or "").suffix.lower()
    if suffix in VIDEO_TYPES:
        return "videos"
    if uploads.kind_of(f"x{suffix}") == "image":
        return "pictures"
    return "pages" if item["kind"] == "page" else "files"




THINGS_FOR_SEC = 300
_things: tuple | None = None


def things() -> list[dict]:
    ""


    global _things
    reads = (str(config.HUB_DB), _changes, _generation, _cards(), sorted(config.BOTS_BY_ID), work._mailboxes())
    if _things and _things[0] == reads and time.monotonic() - _things[1] < THINGS_FOR_SEC and \
            _folders(_things[3]) == _things[4]:
        return [dict(i) for i in _things[2]]
    folders: set[Path] = {resultcard.media_dir()}
    out = _things_now(folders)
    _things = (reads, time.monotonic(), out, folders, _folders(folders))
    return [dict(i) for i in out]


def _folders(paths: set[Path]) -> dict:
    ""
    out = {}
    for p in paths:
        try:
            out[p] = os.stat(p).st_mtime_ns
        except OSError:
            out[p] = None
    return out


def _things_now(folders: set[Path]) -> list[dict]:
    known = work._known_accounts()
    items = [p for r in store.db().execute("SELECT body FROM work_made WHERE active=1")
             if (p := _public(work._json(r["body"]), known)) and p["availability"] == "available"
             and not (p["backing"]["type"] == "file" and _working(p["backing"]["file_id"]))]
    for item in items:
        item.setdefault("name", "")
        item.setdefault("thumbnail", None)
        item.update(made_by=item["bot_id"], versions=[], parts=[], display_title=title_of(
            item["backing"]["file_id"]) if item["backing"]["type"] == "file" else item.get("display_title") or item["title"])
    files = [i for i in items if i["backing"]["type"] == "file"]
    path = {f["id"]: PurePosixPath(f["backing"]["file_id"]) for f in files}
    roots: dict[str, Path] = {}
    for f in files:
        root = roots.get(f["bot_id"]) or roots.setdefault(f["bot_id"], zipfiles.made_root(f["bot_id"]))
        folders.add(root / path[f["id"]].parent)
    claimed: set[str] = set()
    pages: set[str] = set()


    within: dict[tuple[str, ...], list[dict]] = {}
    for f in files:
        parts = path[f["id"]].parts
        for n in range(1, len(parts) + 1):
            within.setdefault(parts[:n], []).append(f)

    def absorb(owner: dict, folder: PurePosixPath) -> None:
        ""
        if not folder.parts:
            return
        for f in within.get(folder.parts, ()):
            if f["id"] not in claimed and f["bot_id"] == owner["made_by"]:
                claimed.add(f["id"])
                owner["parts"].append(f)

    results = sorted((i for i in items if i["backing"]["type"] == "result"), key=lambda i: -i["created_at"])
    shown: dict[str, dict] = {}
    for r in results:
        near = []
        for f in files:
            if path[f["id"]].name != r["name"]:
                continue
            gaps = [abs(r["created_at"] - t) for t in (f["created_at"], f["updated_at"])
                    if -SHOWN_AFTER_SEC <= r["created_at"] - t <= SHOWN_BEFORE_SEC]
            if gaps:
                near.append((min(gaps), f["id"], f))
        if near:
            f = min(near, key=lambda n: n[:2])[2]
            r["made_by"] = f["bot_id"]
            shown[r["id"]] = f
            if f.get("routine"):
                r["routine"] = f["routine"]
    for r in results:
        if f := shown.get(r["id"]):
            claimed.add(f["id"])
            if r["project_id"] == work.OTHER and f["project_id"] != work.OTHER:
                r.update(project_id=f["project_id"], project_title=f["project_title"])

            if path[f["id"]].suffix.lower() in (".html", ".htm"):
                absorb(r, path[f["id"]].parent)


    for f in sorted((f for f in files if path[f["id"]].name.lower() in ("index.html", "index.htm")),
                    key=lambda f: len(path[f["id"]].parts)):
        if f["id"] not in claimed:
            claimed.add(f["id"])
            pages.add(f["id"])
            absorb(f, path[f["id"]].parent)
    rows: dict[tuple, list[dict]] = {}
    for item in items:
        if item["id"] in claimed - pages:
            continue

        if item["backing"]["type"] == "file" and len(path[item["id"]].parts) > 1 and item["id"] not in pages and \
                not item.get("routine"):
            continue
        if item["backing"]["type"] == "file" and uploads.kind_of(item["name"]) == "image":
            item["thumbnail"] = {"type": "file", "bot_id": item["bot_id"], "file_id": item["backing"]["file_id"]}
        elif not item["thumbnail"] and item["backing"]["type"] != "draft" and _preview_path(item, ".jpg").is_file():
            item["thumbnail"] = {"type": "preview", "key": _preview_key(item)}
        rows.setdefault((item["kind"], openloops._key(item["display_title"]) or item["id"], item["project_id"]), []).append(item)

    for kind, key, pid in [k for k in rows if k[2] == work.OTHER]:
        filed = [k for k in rows if k[:2] == (kind, key) and k[2] != work.OTHER]
        if len(filed) == 1:
            rows[filed[0]] += rows.pop((kind, key, pid))
    out = []
    for same in rows.values():
        same.sort(key=lambda i: (i["created_at"], i["id"]), reverse=True)
        head = same[0]
        filed = next((i for i in same if i["project_id"] != work.OTHER), head)
        if head["thumbnail"]:

            head["thumbnail"] = {**head["thumbnail"], "path": f"/api/work/made/{head['id']}/thumbnail"}

        head.update(project_id=filed["project_id"], project_title=filed["project_title"],
                    shelf="reports" if any(i.get("routine") for i in same) else _shelf(head),
                    versions=[_compact(i) for i in same[1:]],
                    parts=[_compact(p) for p in sorted(head["parts"], key=lambda p: path[p["id"]].as_posix())])
        out.append(head)
    out.sort(key=lambda i: (i["created_at"], i["id"]), reverse=True)
    return out


def _preview_key(item: dict) -> str:
    ""
    backing = item["backing"]
    return work._revision([item["bot_id"], backing.get("file_id") or backing.get("media"), item["updated_at"],
                           item.get("size")])


def _preview_path(item: dict, suffix: str) -> Path:

    return resultcard.media_dir() / "made" / f"{_preview_key(item)}{suffix}"


def _page_renderer():
    ""
    global _renderer
    if _renderer is None:
        path = Path(config.PLUGIN_DIR) / "sitepreview.py"
        spec = importlib.util.spec_from_file_location("zero_sitepreview", path) if path.is_file() else None
        if spec is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _renderer = module.render
    return _renderer


def _frame(source: Path, out: Path) -> bool | None:
    ""

    tool = shutil.which("ffmpeg")
    if not tool:
        return None
    for at in ("1", "0"):
        try:
            subprocess.run(["nice", "-n", "15", tool, "-nostdin", "-loglevel", "error", "-y", "-protocol_whitelist",
                            "file", "-f", "mov", "-ss", at, "-i", str(source), "-frames:v", "1", "-vf", "scale=720:-2",
                            "-q:v", "4", str(out)], capture_output=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.is_file() and out.stat().st_size:
            return True
    return False


def _draw(item: dict, source: Path, out: Path) -> bool | None:
    ""

    if source.suffix.lower() in VIDEO_TYPES:
        return _frame(source, out)
    if source.suffix.lower() == ".pdf":
        tool = shutil.which("pdftoppm")
        if not tool:
            return None
        try:
            subprocess.run(["nice", "-n", "15", tool, "-f", "1", "-l", "1", "-jpeg", "-scale-to-x", "720",
                            "-scale-to-y", "-1", "-singlefile", str(source), str(out.with_suffix(""))],
                           capture_output=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError):
            return False
        return out.is_file()
    render = _page_renderer()
    if render is None:
        return None
    try:
        render(str(source), str(out))
        from PIL import Image
        with Image.open(out) as img:
            top = img.crop((0, 0, img.width, min(img.height, PREVIEW_HEIGHT)))
            top.load()
        top.save(out, "JPEG", quality=84)
    except Exception:
        return False
    return True


def _source(item: dict):
    ""
    if item["backing"]["type"] == "file":
        return zipfiles.open_file(item["bot_id"], item["backing"]["file_id"])
    try:
        return safe_files.open_file(str(resultcard.media_dir() / f"{item['backing']['media']}.file"),
                                    (resultcard.media_dir(),), protected=None)
    except (OSError, safe_files.Refused):
        return None


def previews(limit: int = PREVIEWS_PER_SCAN, generation: int | None = None) -> int:
    ""




    global _changes
    generation = _generation if generation is None else generation
    drawn = 0
    for item in things():
        if drawn >= limit or generation != _generation:
            break
        suffix = PurePosixPath(item.get("name") or item["backing"].get("file_id") or "").suffix.lower()
        if item["thumbnail"] or item["backing"]["type"] == "draft" or suffix not in PREVIEW_KINDS or \
                item["backing"]["type"] == "result" and suffix not in VIDEO_TYPES:
            continue
        out, failed = _preview_path(item, ".jpg"), _preview_path(item, ".none")
        if out.is_file() or failed.is_file():
            continue
        f = _source(item)
        if f is None:
            continue
        limit_bytes = VIDEO_MAX if suffix in VIDEO_TYPES else PDF_MAX if suffix == ".pdf" else PAGE_MAX
        with tempfile.TemporaryDirectory(prefix="made-preview-") as scratch:
            source, picture = Path(scratch) / f"file{suffix}", Path(scratch) / "picture.jpg"
            with f, source.open("wb") as copy:
                size = 0
                while size <= limit_bytes and (chunk := f.read(1024 * 1024)):
                    copy.write(chunk)
                    size += len(chunk)
            whole = size <= limit_bytes and not (suffix in (".html", ".htm") and
                                                 _LOCAL_ASSET.search(source.read_text("utf-8", "replace")))
            ok = _draw(item, source, picture) if whole else False
            if ok is None:
                continue



            with store._lock:
                if generation != _generation:
                    return drawn
                out.parent.mkdir(parents=True, exist_ok=True)
                if ok:
                    os.chmod(picture, 0o600)
                    os.replace(picture, out)
                    drawn += 1
                    _changes += 1
                else:
                    failed.touch()
    return drawn


def thumbnail(item_id: str):
    ""
    item = next((i for i in things() if i["id"] == item_id), None)
    shown = (item or {}).get("thumbnail") or {}
    if shown.get("type") == "file":
        f = zipfiles.open_file(shown["bot_id"], shown["file_id"])
        return (f, mimetypes.guess_type(shown["file_id"])[0] or "application/octet-stream") if f else None
    if shown.get("type") == "result":
        path = (resultcard.path_of(shown["media"], "picture") or (None,))[0]
    elif shown.get("type") == "preview":
        path = resultcard.media_dir() / "made" / f"{shown['key']}.jpg"
    else:
        return None
    try:
        return safe_files.open_file(str(path), (resultcard.media_dir(),), protected=None), "image/jpeg"
    except (OSError, TypeError, safe_files.Refused):
        return None


def _cursor(cursor: str) -> tuple | None:
    if not cursor:
        return None
    try:
        before = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(before, list) or len(before) != 2 or not isinstance(before[0], (float, int)) or \
                not math.isfinite(before[0]) or not isinstance(before[1], str):
            raise ValueError
    except (ValueError, TypeError):
        raise CursorError("Refresh this list and try again.") from None
    return tuple(before)


def _matching(project_id: str | None, kind: str | None, query: str, shelf: str | None = None):
    for item in things():
        if project_id and item["project_id"] != project_id or kind and item["kind"] != kind or \
                shelf and item["shelf"] != shelf:
            continue
        search_text = "\n".join([item["title"], item["display_title"], item["project_title"],
                                 *(str(value) for person in item["people"]
                                   for value in (person.values() if isinstance(person, dict) else [person]))])
        if query.casefold() in search_text.casefold():
            yield item


def listing(project_id: str | None = None, kind: str | None = None, query: str = "", limit: int = 60, cursor: str = "",
            shelf: str | None = None) -> dict:
    before = _cursor(cursor)
    page = []
    for item in _matching(project_id, kind, query, shelf):
        if before and (item["created_at"], item["id"]) >= before:
            continue
        page.append(item)
        if len(page) > limit:
            break
    more = len(page) > limit
    page = page[:limit]
    next_cursor = base64.urlsafe_b64encode(json.dumps([page[-1]["created_at"], page[-1]["id"]]).encode()).decode().rstrip("=") if more else None
    return {"items": page, "next_cursor": next_cursor}


def by_project(kind: str | None = None, query: str = "", limit: int = 60) -> dict:
    ""


    groups: dict[str, list[dict]] = {}
    for item in _matching(None, kind, query):
        groups.setdefault(item["project_id"], []).append(item)
    out = [{"project_id": pid, "title": "No project" if pid == work.OTHER else items[0]["project_title"],
            "latest_at": items[0]["created_at"], "items": items[:limit], "more": max(0, len(items) - limit)}
           for pid, items in groups.items()]
    out.sort(key=lambda g: (g["project_id"] == work.OTHER, -g["latest_at"], g["title"].casefold()))
    return {"groups": out, "items": [i for g in out for i in g["items"]], "next_cursor": None}


def by_shelf(project_id: str | None = None, query: str = "", limit: int = 12) -> dict:
    ""


    on: dict[str, list[dict]] = {}
    for item in _matching(project_id, None, query):
        on.setdefault(item["shelf"], []).append(item)
    shelves = [{"id": sid, "title": title, "items": on[sid][:limit], "more": max(0, len(on[sid]) - limit)}
               for sid, title in SHELVES.items() if on.get(sid)]
    return {"shelves": shelves, "items": [i for s in shelves for i in s["items"]], "next_cursor": None}


async def run() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await asyncio.to_thread(scan_files)
        except Exception:
            pass
        await asyncio.sleep(300)
