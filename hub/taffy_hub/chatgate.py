""
























from __future__ import annotations

import json
import logging
import re

from . import store

log = logging.getLogger("taffy.chatgate")

BUBBLE, LINE, CARD, NATIVE, NONE = "bubble", "line", "card", "native", "none"

TEXT_KINDS = {"message", "message_local", "checkin", "task_done", "task_review", "task_failed", "task_blocked"}

KEPT_TITLES = (re.compile(r".+ didn't pick this back up$"), re.compile(r"^Place reminder$"))
OPEN_QUESTIONS = "Open Questions"


def kept(row: dict) -> bool:
    title = str(row.get("title") or "")
    return row.get("kind") == "checkin" and any(p.match(title) for p in KEPT_TITLES)


def shown_as(row: dict) -> str:
    ""
    row = store.with_fields(dict(row))
    kind, by, for_ = row.get("kind") or "", row["written_by"], row["written_for"]
    if for_ == "person":
        if kind not in TEXT_KINDS:
            return CARD if kind != "team" else NATIVE
        if row.get("aside_ids") or (kind == "checkin" and row.get("title") == OPEN_QUESTIONS):
            return CARD
        if by in ("zip", "model", "person") or kept(row):
            return BUBBLE
        return LINE
    return NONE if kind in TEXT_KINDS else NATIVE


def present(items: list[dict]) -> list[dict]:
    ""
    for it in items:
        it["shown_as"] = shown_as(it)
    return items





INTRO = re.compile(r"^(?:I'm working (?:to|on) .+|.+ asked me (?:to|to work on) .+)\. (?:To do that|To keep going), I need")
SENT = re.compile(r"^Sent\. .+ has it now\.$")
MAC_LINE = re.compile(r"^I'll .+ (?:when you're off your Mac\. Say go ahead|as soon as one of the two jobs running on "
                      r"your Mac finishes\.$|as soon as .+ is done on your Mac\.$)")
NOW_LINE = re.compile(r"^(?:One thing needs you today: |\d+ things need you today: )")
STOPPED_FALLBACK = re.compile(r"^I've stopped on “.+” for now\. Something got in my way")
OLD_WITHDRAWN = "I no longer need an answer to this: "
FIXED = "Fixed"
JOB_TITLES = re.compile(r"^(?:Added|Changed|Paused|Removed) a scheduled job$")
DRAFT_NEWS = re.compile(r".+ has news on a draft$")
FINISHED_FALLBACK = re.compile(r"^I finished “.+”\.$")
DONE_KEY = "chatgate_backfill"
VERSION = 1


def _fields_for_old(row: dict) -> tuple[str, str, str | None] | None:
    ""

    kind, title, body = row["kind"], str(row["title"] or ""), str(row["body"] or "")
    words = " ".join(body.split())
    if kind in ("message", "message_local"):
        if words.startswith(OLD_WITHDRAWN):
            return "hub", "person", "No longer needed: " + words[len(OLD_WITHDRAWN):]
        if words.startswith("No longer needed: "):
            return "hub", "person", None
        if INTRO.match(words) or SENT.match(words) or MAC_LINE.match(words) or NOW_LINE.match(words) or \
                STOPPED_FALLBACK.match(words):
            return "hub", "record", None
        return "zip", "person", None
    if kind == "checkin":
        if title == FIXED:
            return "hub", "person", None if words.startswith(f"{FIXED}: ") else f"{FIXED}: {body.strip()}"
        if JOB_TITLES.match(title) or title == "Sign-in":
            return "hub", "record", None
        if title == "Where things stand":
            return "hub", "record", None
        if title == OPEN_QUESTIONS or kept(row):
            return "hub", "person", None
        if title.startswith("Stopped checking ") or title == "Switched Claude accounts":
            return "hub", "person", None
        return "zip", "person", None
    if kind == "task_blocked":
        return ("hub", "record", None) if row.get("plain_title") and words == " ".join(str(row["plain_title"]).split()) \
            else ("zip", "hub", None)
    if kind == "task_done":
        said = body.split("\n\n", 1)[-1].strip()
        return ("hub", "person", None) if FINISHED_FALLBACK.match(said) else ("model", "person", None)
    if kind == "task_review":
        return ("hub", "record", None) if DRAFT_NEWS.match(title) else ("zip", "person", None)
    if kind == "task_failed":
        return "zip", "person", None
    return None


def _into_call(row: dict) -> None:
    ""
    call = store.db().execute("SELECT id, title, body FROM feed WHERE kind='call' AND bot_id=? AND created_at<=? "
                              "ORDER BY created_at DESC LIMIT 1", (row["bot_id"], row["created_at"])).fetchone()
    if not call:
        return
    try:
        body = json.loads(call["body"] or "{}")
    except ValueError:
        return
    if body.get("stand"):
        return
    lines = [ln.strip()[2:].strip() for ln in str(row["body"] or "").splitlines() if ln.strip().startswith("- ")]
    if lines:
        store.update_feed(call["id"], call["title"], json.dumps({**body, "stand": lines[:20]}))


def backfill(force: bool = False) -> int:
    ""



    if not force and store.get_setting(DONE_KEY, 0) >= VERSION:
        return 0
    changed = 0
    with store._lock:
        c = store.db()
        rows = [dict(r) for r in c.execute(
            f"SELECT * FROM feed WHERE kind IN ({','.join('?' * len(TEXT_KINDS))})", tuple(TEXT_KINDS))]
        for row in rows:
            found = _fields_for_old(row)
            if not found:
                continue
            by, for_, body = found
            if row.get("written_by") in ("zip", "model", "person") and row.get("written_for") == "person":
                continue
            redone = (row["kind"] == "checkin" and row["title"] == FIXED) or row["kind"].startswith("task_") or \
                str(row["body"] or "").startswith(OLD_WITHDRAWN) or (by, for_) == ("hub", "record")
            if row.get("written_by") and not redone:
                continue
            if (row.get("written_by"), row.get("written_for")) == (by, for_) and body is None:
                continue
            if row["kind"] == "checkin" and row["title"] == "Where things stand":
                _into_call(row)
            c.execute("UPDATE feed SET written_by=?, written_for=?, body=COALESCE(?, body) WHERE id=?",
                      (by, for_, body, row["id"]))
            changed += 1
        c.commit()
        store.set_setting(DONE_KEY, VERSION)
    log.info("chat gate: %d older rows given their fields", changed)
    return changed
