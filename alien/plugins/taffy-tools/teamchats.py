""















import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
MAX_RESULTS = 20
PER_CHAT = 2
SNIPPET_WORDS = 28
RECENT_CHARS = 300
READ_EACH_SIDE = 6
READ_CHARS = 1200
WAIT_SEC = 3


TALK = ("api_server", "cli")
TALK_WEIGHT = 1.3
KINDS = {"kanban": "its work on a task", "oneshot": "its work on a task"}







_MARKER = re.compile(r"\[Chat turn: [a-f0-9]{32}\]\s*")
_FROM = re.compile(r"\[From ([A-Z][\w' ]{0,30}?)\s*[\]:,(]")
SUMMARY = "[CONTEXT COMPACTION"
NOT_SUMMARY = f"m.content NOT LIKE '{SUMMARY}%'"
PERSON = ("m.role = 'user' AND (m.content NOT LIKE '[%' OR m.content LIKE '[Chat turn:%') "
          f"AND s.source IN ({', '.join(repr(t) for t in TALK)})")

SCHEMA = {
    "name": "team_chats",
    "description": (
        "Search what was said in past chats across the whole team: Zero's chats and every Zip's, in one call. "
        "Use it when the person refers to something said before (\"what did I tell School about the essay\", "
        "\"where did we land on the logo\"), before asking them to repeat something any Zip may already have been "
        "told, and to see what another Zip did or decided. action search (the default) takes a few plain words "
        "and returns the matching lines with who said them, which Zip's chat, when (Eastern time), and whether it "
        "was a chat with the person or the Zip's own work on a task. Narrow it "
        "with zip, since and until. For what the person themself said, set who to person; for the latest "
        "mention, set sort to newest. said_by tells the person's own words from a note the app wrote into the "
        "chat and from a message another Zip sent, so never quote a note as something the person said. "
        "action read opens one chat around a match: give the zip, chat and message "
        "from a search result. Your own chats are included, so this covers session_search too. It only reads. "
        "Answer in plain words: say which Zip and when (\"you told School on Tuesday\"), and never show the "
        "person a chat id, a message number or a folder name."
    ),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["search", "read"], "description": "search (the default) or read."},
        "query": {"type": "string", "description": (
            "search: a few plain words to find. All of them must appear; if nothing has them all, lines with any of "
            "them come back. Leave it out, with a zip, to list that Zip's latest lines newest first: with who "
            "person, that is the last things the person said to it, whatever words they used.")},
        "zip": {"type": "string", "description": "search, optional: only this Zip's chats, by its id from a "
                                                 "result or its name. read: the Zip whose chat to open."},
        "since": {"type": "string", "description": "search, optional: on or after this day, as 2026-11-05."},
        "until": {"type": "string", "description": "search, optional: on or before this day, as 2026-11-05."},
        "limit": {"type": "integer", "description": f"search, optional: how many lines, up to {MAX_RESULTS}. "
                                                    "The default is 8."},
        "who": {"type": "string", "enum": ["anyone", "person"], "description": (
            "search, optional: person for only what the person typed or said; anyone (the default) for every line.")},
        "sort": {"type": "string", "enum": ["best", "newest"], "description": (
            "search, optional: best match first (the default), or newest first.")},
        "chat": {"type": "string", "description": "read: the chat id from a search result."},
        "message": {"type": "integer", "description": "read: the message number from a search result; the "
                                                      "lines around it come back. Leave out for the chat's end."},
    }},
}


def _homes() -> list[tuple[str, str, Path]]:
    ""

    from . import gate, private_zips, team
    me = gate._bot()
    root = gate._taffy_home()
    entries = {str(e.get("id")): e for e in team._registry_entries()}
    out = [("default", "Zero", root)]
    try:
        folders = sorted(p for p in (root / "profiles").iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        folders = []
    for p in folders:
        entry = entries.get(p.name) or {}
        if entry.get("sandbox") is True or (p.name in private_zips.PRIVATE_ZIPS and p.name != me):
            continue
        out.append((p.name, str(entry.get("name") or p.name.title()), p))
    return out


def _open(folder: Path) -> sqlite3.Connection:
    db = folder / "state.db"
    if not db.exists():
        raise OSError("no chats saved yet")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=WAIT_SEC)


def _when(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), EASTERN).strftime("%a %b %-d %Y, %-I:%M %p")
    except (TypeError, ValueError, OSError):
        return ""


def _day(text, end: bool) -> float | None:
    ""
    text = str(text or "").strip()
    if not text:
        return None
    try:
        d = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=EASTERN)
    except ValueError:
        raise ValueError(f"\"{text}\" isn't a day I can read. Write it as 2026-11-05.") from None
    return d.timestamp() + (86399 if end else 0)


def _words(query: str) -> list[str]:
    ""
    return ['"' + w.replace('"', "") + '"' for w in re.findall(r"[\w'’]+", query or "") if w.strip("'’")][:12]


def _who(role: str, name: str, head: str = "", talk: bool = True) -> str:
    ""
    if role != "user":
        return name
    if not talk:
        return "the task it was given"
    head = (head or "").lstrip()
    if not head.startswith("[") or head.startswith("[Chat turn:"):
        return "the person"
    sender = _FROM.match(head)
    return sender.group(1).strip() if sender else "a note from the app, not the person"





_HUB_LINE = re.compile(r"^\[(?:Your open question cards|Your open questions to|Your question cards answered lately|"
                       r"Current mail drafts|Send from the chat|Hub note)[^\n]*\n?", re.M)


def _text(text) -> str:
    return _HUB_LINE.sub("", _MARKER.sub("", str(text or "")))


def _matches(conn, match: str, since, until, limit: int, person: bool = False, newest: bool = False) -> list[tuple]:
    ""

    if match:
        sql = ("SELECT m.id, m.session_id, m.role, m.timestamp, "
               f"snippet(messages_fts, 0, '', '', ' ... ', {SNIPPET_WORDS}), bm25(messages_fts), s.source, "
               "substr(m.content, 1, 80) "
               "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
               "LEFT JOIN sessions s ON s.id = m.session_id "
               f"WHERE messages_fts MATCH ? AND m.role IN ('user', 'assistant') AND {NOT_SUMMARY}")
        args: list = [match]
    else:
        sql = (f"SELECT m.id, m.session_id, m.role, m.timestamp, substr(m.content, 1, {RECENT_CHARS}), 0, s.source, "
               "substr(m.content, 1, 80) FROM messages m LEFT JOIN sessions s ON s.id = m.session_id "
               f"WHERE m.role IN ('user', 'assistant') AND COALESCE(m.content, '') <> '' AND {NOT_SUMMARY}")
        args = []
        newest = True
    if person:
        sql += f" AND {PERSON}"
    if since is not None:
        sql += " AND m.timestamp >= ?"
        args.append(since)
    if until is not None:
        sql += " AND m.timestamp <= ?"
        args.append(until)
    sql += (" ORDER BY m.timestamp DESC, m.id DESC" if newest
            else " ORDER BY bm25(messages_fts), m.timestamp DESC") + " LIMIT ?"
    args.append(limit * 4)
    return conn.execute(sql, args).fetchall()


def _pick(homes, wanted: str):
    ""
    wanted = (wanted or "").strip().lower()
    if not wanted:
        return homes
    if wanted in ("zero", "default", "me", "lead"):
        wanted = "default"
    return [h for h in homes if wanted in (h[0].lower(), h[1].lower())]


def _search(args: dict) -> dict:
    words = _words(str(args.get("query") or ""))
    if not words and not str(args.get("zip") or "").strip():
        return {"error": "Give a few words to look for in query, or a zip to list its latest lines."}
    try:
        since, until = _day(args.get("since"), False), _day(args.get("until"), True)
    except ValueError as e:
        return {"error": str(e)}
    try:
        limit = max(1, min(MAX_RESULTS, int(args.get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8
    person = str(args.get("who") or "").strip().lower() == "person"
    newest = str(args.get("sort") or "").strip().lower() == "newest" or not words
    homes = _homes()
    chosen = _pick(homes, str(args.get("zip") or ""))
    if not chosen:
        return {"error": "No Zip by that name. The team is: " + ", ".join(h[1] for h in homes) + "."}

    def run(match: str):
        found, unread = [], []
        for bot, name, folder in chosen:
            try:
                conn = _open(folder)
                try:
                    rows = _matches(conn, match, since, until, limit, person, newest)
                finally:
                    conn.close()
            except (sqlite3.Error, OSError):
                unread.append(name)
                continue
            seen: dict[str, int] = {}
            for mid, chat, role, ts, text, score, source, head in rows:
                if words and seen.get(chat, 0) >= PER_CHAT:
                    continue
                seen[chat] = seen.get(chat, 0) + 1
                talk = source in TALK
                found.append((0 if newest else score * (TALK_WEIGHT if talk else 1), -float(ts or 0), {
                    "zip": bot, "zip_name": name, "chat": chat, "message": mid, "when": _when(ts),
                    "kind": "a chat with the person" if talk else KINDS.get(source, "its own work"),
                    "said_by": _who(role, name, head, talk), "text": " ".join(_text(text).split())}))
        found.sort(key=lambda r: (r[0], r[1]))
        return [r[2] for r in found[:limit]], unread

    results, unread = run(" ".join(words))
    loose = False
    if not results and len(words) > 1:
        results, unread = run(" OR ".join(words))
        loose = bool(results)
    out: dict = {"results": results, "searched": [h[1] for h in chosen if h[1] not in unread]}
    if loose:
        out["note"] = "Nothing had every word, so these have at least one of them."
    if not results:
        out["note"] = ("Nothing in these chats matches. Try fewer or different words before saying it was never said."
                       + (" This looked only at the person's own words; leave who out to search every line."
                          if person else ""))
    if unread:
        out["could_not_read"] = unread
        out["could_not_read_note"] = ("These chats could not be opened just now, so they were not searched. Say so "
                                      "if the answer may be there.")
    return out


def _read(args: dict) -> dict:
    homes = _homes()
    chosen = _pick(homes, str(args.get("zip") or "")) if str(args.get("zip") or "").strip() else []
    chat = str(args.get("chat") or "").strip()
    if len(chosen) != 1 or not chat:
        return {"error": "read needs zip and chat, both from a search result."}
    bot, name, folder = chosen[0]
    try:
        conn = _open(folder)
    except (sqlite3.Error, OSError):
        return {"error": f"{name}'s chats could not be opened just now."}
    try:
        try:
            around = int(args.get("message")) if args.get("message") is not None else None
        except (TypeError, ValueError):
            around = None
        said = f"role IN ('user', 'assistant') AND COALESCE(content, '') <> '' AND content NOT LIKE '{SUMMARY}%'"
        found = conn.execute("SELECT source FROM sessions WHERE id = ?", (chat,)).fetchone()
        talk = bool(found) and found[0] in TALK
        if around is None:
            rows = conn.execute(f"SELECT id, role, timestamp, content FROM messages WHERE session_id = ? AND {said} "
                                "ORDER BY id DESC LIMIT ?", (chat, READ_EACH_SIDE * 2)).fetchall()[::-1]
        else:
            before = conn.execute(f"SELECT id, role, timestamp, content FROM messages WHERE session_id = ? AND {said} "
                                  "AND id <= ? ORDER BY id DESC LIMIT ?", (chat, around, READ_EACH_SIDE + 1)).fetchall()
            after = conn.execute(f"SELECT id, role, timestamp, content FROM messages WHERE session_id = ? AND {said} "
                                 "AND id > ? ORDER BY id LIMIT ?", (chat, around, READ_EACH_SIDE)).fetchall()
            rows = before[::-1] + after
    except sqlite3.Error:
        return {"error": f"{name}'s chats could not be read just now."}
    finally:
        conn.close()
    if not rows:
        return {"error": "No chat with that id for this Zip. Use the zip and chat from a search result."}
    lines = []
    for mid, role, ts, content in rows:
        head = str(content or "")[:80]
        text = _text(content).strip()
        if len(text) > READ_CHARS:
            text = text[:READ_CHARS].rstrip() + " ... (cut; read again with this message number for what follows)"
        lines.append({"message": mid, "when": _when(ts), "said_by": _who(role, name, head, talk), "text": text})
    return {"zip": bot, "zip_name": name, "chat": chat, "lines": lines}


def team_chats(args: dict, **kwargs) -> str:
    args = args if isinstance(args, dict) else {}
    reading = str(args.get("action") or "search").strip().lower() == "read"
    return json.dumps(_read(args) if reading else _search(args))
