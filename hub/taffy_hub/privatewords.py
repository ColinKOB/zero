""























from __future__ import annotations

import ast
import json
import logging
import re
import threading
import time
from pathlib import Path

from . import config, store

log = logging.getLogger("taffy.privatewords")

ENABLED = False
WINDOW = 8
BOARD_EVERY_SEC = 60.0
SHORT_MIN = 4
FEED_KINDS = ("message", "message_local", "task_done", "task_failed", "task_review", "checkin", "team", "action")
_WORD = re.compile(r"[^\W_]+")
_ANSWER = re.compile(r'^To the question "(.+?)", the answer is:\s*(.*)$', re.S)
_HUB_NOTE = re.compile(r"^(?:UNBLOCK:\s*)?\[(?:Hub note|From the hub)", re.I)
_HUB_AUTHORS = {"Hub", "Mail check"}

_lock = threading.Lock()
_state: dict = {}


def words(text) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def windows(text) -> set[int]:
    ""
    w = words(text)
    return {hash(" ".join(w[i:i + WINDOW])) for i in range(len(w) - WINDOW + 1)}


def stored(text, common: set[int]) -> set[int]:
    ""

    w = words(text)
    hashes = [hash(" ".join(w[i:i + WINDOW])) for i in range(len(w) - WINDOW + 1)]
    fixed = [False] * len(w)
    for i, h in enumerate(hashes):
        if h in common:
            fixed[i:i + WINDOW] = [True] * WINDOW
    return {h for i, h in enumerate(hashes) if sum(fixed[i:i + WINDOW]) <= WINDOW // 4}


def _short(text) -> tuple[int, int] | None:
    ""
    w = words(text)
    return (len(w), hash(" ".join(w))) if SHORT_MIN <= len(w) < WINDOW else None


def _phrase(text) -> str:
    return " " + " ".join(words(text)) + " "


def _literals() -> str:
    ""
    out = []
    for path in Path(__file__).resolve().parent.glob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            continue
        out += [_phrase(n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return "|".join(out)


def _common() -> set[int]:
    ""
    out: set[int] = set()
    for path in Path(__file__).resolve().parent.glob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 30:
                for part in re.split(r"\{[^}]*\}", n.value):
                    out |= windows(part)
    return out


def _empty() -> dict:
    return {"feed": 0, "facts": 0, "words": set(), "board": set(), "allowed": set(), "board_at": 0.0, "short": {},
            "short_notes": {}, "common": _state.get("common") if _state.get("common") is not None else _common(),
            "literals": _state.get("literals") if _state.get("literals") is not None else _literals()}


def _keep_short(s: dict, short: dict, text, allowed_text: str) -> None:
    ""
    found = _short(text)
    if found and _phrase(text) not in s["literals"] and _phrase(text) not in allowed_text:
        short.setdefault(found[0], set()).add(found[1])


def reset() -> None:
    ""
    with _lock:
        _state.clear()


def _text_of(body) -> str:
    ""
    try:
        data = json.loads(body) if isinstance(body, str) and body.lstrip().startswith("{") else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        return " ".join(str(data.get(k) or "") for k in ("text", "plain", "result", "summary", "why", "reasoning"))
    return str(body or "")


def _read_store(s: dict) -> None:
    c = store.db()
    top = c.execute("SELECT COALESCE(MAX(id), 0) FROM feed").fetchone()[0]
    if top < s["feed"]:
        s.update(feed=0, facts=0, words=set(), short_notes={})
    private = sorted(config.PRIVATE_ZIPS)
    marks = ",".join("?" * len(private))
    kinds = ",".join("?" * len(FEED_KINDS))
    for r in c.execute(f"SELECT id, kind, bot_id, body FROM feed WHERE id > ? AND kind IN ({kinds}) ORDER BY id",
                       (s["feed"], *FEED_KINDS)):
        s["feed"] = r["id"]
        if r["kind"] == "team":
            try:
                body = json.loads(r["body"] or "{}")
            except ValueError:
                continue
            if isinstance(body, dict) and config.sealed(body.get("from"), body.get("to")):
                s["words"] |= stored(_text_of(r["body"]), s["common"])
        elif config.is_private(r["bot_id"]) and not _HUB_NOTE.match(str(r["body"] or "")):
            s["words"] |= stored(_text_of(r["body"]), s["common"])
    try:
        for r in c.execute(f"SELECT id, text FROM facts WHERE id > ? AND bot_id IN ({marks}) ORDER BY id",
                           (s["facts"], *private)):
            s["facts"] = r["id"]
            s["words"] |= stored(r["text"], s["common"])
            _keep_short(s, s["short_notes"], r["text"], "")
    except Exception:
        log.debug("no notes table to read", exc_info=True)


def _read_board(s: dict, only: str = "") -> None:
    ""

    import sqlite3
    if not config.KANBAN_DB.exists():
        return
    private = sorted(config.PRIVATE_ZIPS)
    marks = ",".join("?" * len(private))
    found, allowed, short, texts, allowed_text = set(), set(), {}, [], []
    c = sqlite3.connect(f"file:{config.KANBAN_DB}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        tasks = c.execute(
            f"SELECT id, title, body, result FROM tasks WHERE (assignee IN ({marks}) OR created_by IN ({marks}) OR id IN "
            f"(SELECT task_id FROM task_comments WHERE author IN ({marks})))" + (" AND id = ?" if only else ""),
            (*private, *private, *private, *((only,) if only else ()))).fetchall()
        for t in tasks:
            allowed |= windows(t["title"])
            allowed_text.append(_phrase(t["title"]))
            texts += [t["body"], t["result"]]
            found |= stored(t["body"], s["common"]) | stored(t["result"], s["common"])
            for r in c.execute("SELECT summary FROM task_runs WHERE task_id = ?", (t["id"],)):
                texts.append(r["summary"])
                found |= stored(r["summary"], s["common"])
            for r in c.execute("SELECT author, body FROM task_comments WHERE task_id = ?", (t["id"],)):
                body = str(r["body"] or "").strip()
                if (r["author"] or "") in _HUB_AUTHORS or _HUB_NOTE.match(body):
                    continue
                said = re.search(r"answered in the app:\s*(.*)$", body, re.S)
                body = said.group(1).strip() if said else body
                asked = _ANSWER.match(body)
                if asked:
                    allowed |= windows(asked.group(1))
                    allowed_text.append(_phrase(asked.group(1)))
                    body = asked.group(2)
                texts.append(body)
                found |= stored(body, s["common"])
            for r in c.execute("SELECT payload FROM task_events WHERE task_id = ? AND kind IN ('blocked', "
                               "'block_loop_detected')", (t["id"],)):
                try:
                    reason = str(json.loads(r["payload"] or "{}").get("reason") or "")
                except (ValueError, AttributeError):
                    continue
                allowed |= windows(reason.split("\n", 1)[0])
                allowed_text.append(_phrase(reason.split("\n", 1)[0]))
    finally:
        c.close()
    joined = "|".join(allowed_text)
    for text in texts:
        _keep_short(s, short, text, joined)
    if only:
        s["board"] |= found
        s["allowed"] |= allowed
        for n, hashes in short.items():
            s["short"].setdefault(n, set()).update(hashes)
    else:
        s["board"], s["allowed"] = found, allowed
        s["short"] = {**{n: set(h) for n, h in short.items()}}


def note_task(task) -> None:
    ""

    if not ENABLED:
        return
    task_id = task if isinstance(task, str) else str((task or {}).get("task_id") or (task or {}).get("id") or "")
    if not task_id:
        return
    with _lock:
        if not _state:
            _state.update(_empty())
        try:
            _read_board(_state, task_id)
        except Exception:
            log.warning("could not read %s into the private words", task_id, exc_info=True)


def _fresh() -> dict:
    with _lock:
        if not _state:
            _state.update(_empty())
        s = _state
        try:
            _read_store(s)
        except Exception:
            log.warning("could not read the private Zip's stored words", exc_info=True)
        if time.monotonic() - s["board_at"] >= BOARD_EVERY_SEC:
            s["board_at"] = time.monotonic()
            try:
                _read_board(s)
            except Exception:
                log.warning("could not read the private Zip's tasks", exc_info=True)
        return s


def carries(prompt: str) -> bool:
    ""
    if not ENABLED or not prompt:
        return False
    return _carries(_fresh(), prompt)


def _carries(s: dict, prompt: str) -> bool:
    mine = windows(prompt)
    if not mine:
        return False
    hits = (mine & (s["words"] | s["board"])) - s["allowed"] - s["common"]
    if hits:
        return True
    w = words(prompt)
    return any(hash(" ".join(w[i:i + n])) in hashes for kept in (s["short"], s["short_notes"])
               for n, hashes in kept.items() for i in range(len(w) - n + 1))
















def _needed() -> bool:
    ""


    if not ENABLED:
        return False
    from . import shortreply
    return shortreply.withheld(*config.PRIVATE_ZIPS)


def holds(text) -> bool:
    ""


    return _needed() and carries(str(text or ""))


def keep(rows, text=str) -> list:
    ""
    rows = list(rows or ())
    if not rows or not _needed():
        return rows
    s = _fresh()
    return [r for r in rows if not _carries(s, str(text(r) or ""))]


_SENTENCE = re.compile(r"[^.!?\n]*(?:[.!?]+[\"'”’)\]]*|$)")


def _marked(s: dict, text: str) -> list[tuple[int, int]]:
    ""
    found = list(_WORD.finditer(text))
    w = [m.group(0).lower() for m in found]
    bad = set()
    for i in range(len(w) - WINDOW + 1):
        h = hash(" ".join(w[i:i + WINDOW]))
        if (h in s["words"] or h in s["board"]) and h not in s["allowed"] and h not in s["common"]:
            bad.update(range(i, i + WINDOW))
    for kept in (s["short"], s["short_notes"]):
        for n, hashes in kept.items():
            for i in range(len(w) - n + 1):
                if hash(" ".join(w[i:i + n])) in hashes:
                    bad.update(range(i, i + n))
    return [found[i].span() for i in sorted(bad)]


def leave_out(text) -> str:
    ""

    text = str(text or "")
    if not text or not _needed():
        return text
    s = _fresh()
    if not _carries(s, text):
        return text
    spans = _marked(s, text)
    out, at = [], 0
    for line in text.split("\n"):
        start, end = at, at + len(line)
        at = end + 1
        if not any(start <= a < end for a, _ in spans):
            out.append(line)
            continue
        parts = [(start + m.start(), m.group(0)) for m in _SENTENCE.finditer(line) if m.group(0).strip()]
        kept = [p for off, p in parts if not any(off <= a < off + len(p) for a, _ in spans)]
        if kept:
            out.append(" ".join(p.strip() for p in kept))
    left = "\n".join(out)
    return "" if _carries(s, left) else left


def size() -> dict:
    ""
    s = _fresh()
    return {"words": len(s["words"]), "board": len(s["board"]), "allowed": len(s["allowed"]), "common": len(s["common"])}
