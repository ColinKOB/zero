""











import hashlib
import json
import os
import re
import threading

SECOND = ("This failed twice the same way. Don't send it again: try a different route, or ask {person} one short "
          "question if only {person} can unblock it.")
FOURTH = ("This step has failed the same way {n} times, so it is over. Don't call {tool} for it again. Tell {person} "
          "in one line what you couldn't do, and carry on with the rest.")
STOP_AT = 4
_KEEP = 64

_lock = threading.Lock()
_replies: dict[tuple, dict] = {}


def _reply(kw: dict) -> tuple | None:
    ""
    turn = str(kw.get("turn_id") or "") or os.environ.get("HERMES_KANBAN_RUN_ID", "")
    return (str(kw.get("session_id") or ""), turn) if turn else None


def _failure(text: str) -> str:
    return " ".join(re.sub(r"\d+", "N", str(text or "")).lower().split())[:160]


def _args(args) -> str:
    try:
        text = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(args)
    return hashlib.sha1(text.encode()).hexdigest()


def count(kw: dict, tool: str, text: str, args=None) -> int:
    ""
    reply = _reply(kw)
    if reply is None or not str(text or "").strip():
        return 0
    with _lock:
        seen = _replies.setdefault(reply, {})
        while len(_replies) > _KEEP:
            _replies.pop(next(iter(_replies)))
        key = (tool, _failure(text))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= STOP_AT:
            seen[("args", tool, _args(args))] = seen[key]
        return seen[key]


def stopped(kw: dict, tool: str, args) -> int:
    ""
    with _lock:
        return (_replies.get(_reply(kw)) or {}).get(("args", tool, _args(args)), 0)


def words(n: int, tool: str, person: str) -> str:
    ""
    if n >= STOP_AT:
        return FOURTH.format(n=n, tool=tool, person=person)
    return SECOND.format(person=person) if n == 2 else ""


def failed(result) -> str:
    ""
    if not isinstance(result, str):
        return ""
    try:
        data = json.loads(result)
    except ValueError:
        return ""
    if isinstance(data, dict) and data.get("error") and data.get("success") is not True:
        return str(data["error"])
    return ""
