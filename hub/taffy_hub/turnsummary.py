""

import hashlib
import json
import re
import time
from collections import Counter
from contextlib import contextmanager

from . import store

MARKER = re.compile(r"^\[Chat turn: ([a-f0-9]{32})\]\n")




GUIDE = ("For typed chat, keep useful progress updates while you work. After multiple messages, end with one "
         "short final message: what you found, the answer or plan, and anything you need from the person. "
         "Use a few plain first-person sentences in one paragraph. State each fact once in that final message. "
         "Earlier updates will fold away, so the final message must stand on its own. "
         "Never hide requested work, a draft, a link or an attachment in a summary. Calls are unchanged.")


@store.schema
def _tables(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS chat_turns (id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, "
                 "started REAL NOT NULL, ended REAL, state TEXT NOT NULL, reply_hash TEXT, summary TEXT)")

    if "worked" not in {r[1] for r in conn.execute("PRAGMA table_info(chat_turns)")}:
        try:
            conn.execute("ALTER TABLE chat_turns ADD COLUMN worked INTEGER")
        except Exception:
            pass


    if "checklist" not in {r[1] for r in conn.execute("PRAGMA table_info(chat_turns)")}:
        try:
            conn.execute("ALTER TABLE chat_turns ADD COLUMN checklist TEXT")
        except Exception:
            pass

    if "steps" not in {r[1] for r in conn.execute("PRAGMA table_info(chat_turns)")}:
        try:
            conn.execute("ALTER TABLE chat_turns ADD COLUMN steps TEXT")
        except Exception:
            pass


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def peel(text: str) -> tuple[str | None, str]:
    match = MARKER.match(text)
    return (match[1], text[match.end():]) if match else (None, text)


@contextmanager
def recording(turn):
    if not turn.typed:
        yield
        return
    with store._lock:
        store.db().execute("INSERT INTO chat_turns (id, bot_id, started, state) VALUES (?, ?, ?, 'running')",
                           (turn.id, turn.bot_id, time.time()))
        store.db().commit()
    turn.summary_recorded = True
    token = store.feed_turn.set((turn.bot_id, turn.id))
    try:
        yield
    finally:
        store.feed_turn.reset(token)


def worked(turn, replying: bool = False) -> bool:
    ""

    said = sum(1 for e in turn.events if e.get("type") in ("commentary", "reply"))
    return bool(getattr(turn, "used_tools", False)) or said + int(replying) > 1


def note_checklist(turn, ev: dict) -> None:
    ""
    if not turn.typed:
        return
    steps = {k: ev[k] for k in ("steps", "done", "total", "revision")}
    with store._lock:
        store.db().execute("UPDATE chat_turns SET checklist=? WHERE id=? AND bot_id=? AND (checklist IS NULL OR "
                           "json_extract(checklist, '$.revision') < ?)",
                           (json.dumps(steps), turn.id, turn.bot_id, steps["revision"]))
        store.db().commit()


STEPS_MAX = 40


def note_step(turn, did: dict, at: float | None = None) -> None:
    ""

    if not turn.typed or not did:
        return
    item = {"at": round(at or time.time(), 1), **{k: str(did.get(k) or "") for k in ("lead", "rest", "icon")}}
    with store._lock:
        row = store.db().execute("SELECT steps FROM chat_turns WHERE id=? AND bot_id=?", (turn.id, turn.bot_id)).fetchone()
        if row is None:
            return
        try:
            steps = json.loads(row["steps"] or "[]")
        except ValueError:
            steps = []
        if steps and all(steps[-1].get(k) == item[k] for k in ("lead", "rest")):
            return
        steps = (steps + [item])[-STEPS_MAX:]
        store.db().execute("UPDATE chat_turns SET steps=? WHERE id=? AND bot_id=?", (json.dumps(steps), turn.id, turn.bot_id))
        store.db().commit()







def _settled(raw: str | None, state: str) -> str | None:
    ""
    try:
        steps = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not steps or state == "yielded" or all(s["state"] == "done" for s in steps["steps"]):
        return None
    if state == "complete":
        kept = [s for s in steps["steps"] if s["state"] == "done"]
    else:
        kept = [{**s, "state": "planned"} if s["state"] == "doing" else s for s in steps["steps"]]
    if kept == steps["steps"]:
        return None
    return json.dumps({"steps": kept, "done": sum(s["state"] == "done" for s in kept), "total": len(kept),
                       "revision": max(int(steps.get("revision") or 0) + 1, int(time.time() * 1000))})


def finish(turn) -> None:
    ""
    if not turn.typed:
        return
    from . import shortreply
    reply = next((e for e in reversed(turn.events) if e.get("type") == "reply"), None)
    state = ("stopped" if turn.stopped else "failed" if any(e.get("type") == "error" for e in turn.events)
             else "yielded" if turn.yielded else "complete" if reply else "incomplete")
    went_to_work = worked(turn)
    summary = reply["text"] if reply and state == "complete" and went_to_work else None
    if summary and (shortreply.words_in(summary) > shortreply.SHORT_MAX_WORDS or shortreply.has_work(summary)):
        summary = None
    source = turn.summary_source
    with store._lock:
        row = store.db().execute("SELECT checklist FROM chat_turns WHERE id=? AND bot_id=?",
                                 (turn.id, turn.bot_id)).fetchone()
        steps = _settled(row["checklist"] if row else None, state)
        store.db().execute("UPDATE chat_turns SET ended=?, state=?, reply_hash=?, summary=?, worked=?, "
                           "checklist=COALESCE(?, checklist) WHERE id=? AND bot_id=?",
                           (time.time(), state, fingerprint(source) if source else None, summary, int(went_to_work),
                            steps, turn.id, turn.bot_id))
        store.db().commit()


def apply(bot_id: str, messages: list[dict]) -> list[dict]:
    ids = {m["turn_id"] for m in messages if m.get("turn_id")}
    if not ids:
        return messages
    records = {r["id"]: dict(r) for r in store.db().execute(
        f"SELECT * FROM chat_turns WHERE bot_id=? AND id IN ({','.join('?' * len(ids))})", (bot_id, *ids))}
    out = []
    last = {m.get("turn_id"): i for i, m in enumerate(messages) if m.get("role") == "assistant"}


    said = Counter(m.get("turn_id") for m in messages
                   if m.get("role") == "assistant" and not str(m.get("id") or "").startswith("first-"))
    for i, message in enumerate(messages):
        record = records.get(message.get("turn_id"))
        if record is None:
            out.append(message)
            continue
        went_to_work = bool(record["worked"]) if record["worked"] is not None else said[record["id"]] > 1
        summary = (went_to_work and i == last.get(record["id"]) and message.get("role") == "assistant" and
                   record["state"] == "complete" and record["summary"] is not None and
                   fingerprint(message.get("text") or "") == record["reply_hash"])
        meta = {"id": record["id"], "state": record["state"], "seconds": max(0, (record["ended"] or record["started"]) -
                record["started"]), "is_summary": bool(summary)}
        if record.get("steps"):
            try:
                did = json.loads(record["steps"])
            except ValueError:
                did = []
            if did:
                meta["steps"] = did
        if record.get("checklist"):
            steps = json.loads(record["checklist"])
            if steps.get("steps"):
                meta["checklist"] = steps
        item = {**message, "turn": meta}
        if summary:
            original = message.get("full") or message.get("display") or message["text"]
            item["display"] = record["summary"]
            if original != record["summary"]:
                item["full"] = original
        elif not went_to_work and item.get("full"):


            item["display"] = item.pop("full")
        out.append(item)
    return out
