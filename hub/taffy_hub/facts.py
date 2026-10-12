"""What the team knows about the person: one shared facts store (docs/qa/2026-10-10-memory-facts.md).

Colin, Oct 10: never make him repeat himself. If he tells a Zip his coffee brand and where he buys it, "can you order
new coffee for me?" two weeks later, in a new conversation, to any Zip, just works.

Hermes's own memory (MEMORY.md and USER.md) is a snapshot frozen into each session's system prompt and capped at a few
thousand characters, so it cannot carry this. The facts store can:

- One fact is one sentence about the person's life, with a short subject ("coffee"), a kind (preference, place,
  purchase, supply, person, plan, decision, other), where it came from (a Zip, the person's own words in a chat, an
  order email, a cart a Zip prepared, a call, the app, an import), how sure it is (said directly, inferred, from a
  receipt), when it was observed, and who may see it (the whole team, or only the Zip that owns it, for amounts in
  Money's area and health details). A fact a newer one replaces is kept, marked replaced, for history.
- Zips save facts with `save_fact` (the older `team_note` still works and saves the same way). The hub also saves them
  itself after a chat turn whose words state one (extract.py) and from order emails.
- Reading is by relevance, never by stuffing the system prompt (its cache is per session): every turn's user message
  gets "Relevant to this message", the top few facts and the person's own past words from any Zip's chat that match
  it (FTS5, well under 100 ms), under 600 characters, through the plugin's pre_llm_call hook. Facts a conversation was
  already given are not repeated in it. `recall` digs deeper on request. Call briefings carry the whole store.
- The older team notes stream (every Zip hears about plans, decisions and people when they are saved) keeps working
  from the same table: kinds in BROADCAST.

Each home has its own hub.db, so nothing here crosses between homes.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import config, home, store

log = logging.getLogger("taffy.facts")

KINDS = ("preference", "place", "purchase", "supply", "person", "plan", "decision", "other")
# Kinds every Zip hears about when they are saved (the team notes stream); the rest come up when they are relevant.
BROADCAST = ("person", "plan", "decision", "other")
SOURCES = ("zip", "chat", "mail", "cart", "call", "app", "import")
CONFIDENCE = ("said", "inferred", "receipt")
RELEVANT_MAX_CHARS = 600
RELEVANT_FACTS = 5
RELEVANT_SNIPPETS = 2
FACTS_PER_SESSION_HOUR = 12
FACTS_PER_DAY = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'other',
    text TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    session_id TEXT,
    source TEXT NOT NULL DEFAULT 'zip',
    source_ref TEXT,
    confidence TEXT NOT NULL DEFAULT 'said',
    visibility TEXT NOT NULL DEFAULT 'team',
    data TEXT,
    observed_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    replaced_by INTEGER,
    edited_at INTEGER
);
CREATE INDEX IF NOT EXISTS facts_current ON facts (replaced_by, kind);
CREATE INDEX IF NOT EXISTS facts_ref ON facts (source, source_ref);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(subject, text, content='facts', content_rowid='id',
    tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts (rowid, subject, text) VALUES (new.id, new.subject, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts (facts_fts, rowid, subject, text) VALUES ('delete', old.id, old.subject, old.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF subject, text ON facts BEGIN
    INSERT INTO facts_fts (facts_fts, rowid, subject, text) VALUES ('delete', old.id, old.subject, old.text);
    INSERT INTO facts_fts (rowid, subject, text) VALUES (new.id, new.subject, new.text);
END;
CREATE TABLE IF NOT EXISTS fact_shown (session_id TEXT NOT NULL, fact_id INTEGER NOT NULL, at INTEGER NOT NULL,
    PRIMARY KEY (session_id, fact_id));
"""

_ready = None   # the connection and database file the schema was made for


class FactError(Exception):
    """A plain-words reason, returned to the Zip."""


def _db() -> sqlite3.Connection:
    global _ready
    c = store.db()
    key = (id(c), str(config.HUB_DB))
    if _ready != key:
        with store._lock:
            # Statements with BEGIN ... END bodies hold semicolons, so the schema runs as one script.
            c.executescript(SCHEMA)
            _from_team_notes(c)
            c.commit()
        _ready = key
    return c


def _from_team_notes(c: sqlite3.Connection) -> None:
    """Team notes saved before the facts store are facts with the same numbers, so "replaces 12" still means note 12.
    Runs once per home; the old table is left as it was."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_notes'").fetchone():
        return
    if c.execute("SELECT value FROM settings WHERE name='facts_from_team_notes'").fetchone() if _has_settings(c) \
            else None:
        return
    cols = {r[1] for r in c.execute("PRAGMA table_info(team_notes)")}
    sid = "session_id" if "session_id" in cols else "NULL"
    c.execute(f"INSERT OR IGNORE INTO facts (id, subject, kind, text, bot_id, session_id, source, confidence, "
              f"visibility, observed_at, created_at, replaced_by) SELECT id, '', 'other', text, bot_id, {sid}, 'zip', "
              f"'said', 'team', created_at, created_at, replaced_by FROM team_notes")
    if _has_settings(c):
        c.execute("INSERT OR REPLACE INTO settings (name, value) VALUES ('facts_from_team_notes', '1')")


def _has_settings(c: sqlite3.Connection) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone())


def reset_for_tests() -> None:
    global _ready
    _ready = None


def now() -> float:
    """The clock facts read (tests replace it)."""
    return time.time()


# MARK: Saving

_AMOUNT = re.compile(r"\$\s?\d|\b\d[\d,]*(\.\d\d)?\s*(dollars|bucks|usd|cad)\b", re.I)
# Health details: conditions and treatment. Where someone buys contact lenses, or that a store needs a prescription card,
# is shopping, not health (Oct 10 seeding dry run).
_HEALTH = re.compile(r"diagnos|medication|\bprescribed\b|\b(takes|taking|on) (\w+ )?prescription|therapy|\bsymptom|\ballerg(y|ic)\b.*\b(medic|epipen|inhaler)",
                     re.I)
_SECRET_WORDS = re.compile(r"(password|passcode|\bpin\b|\botp\b|security code|verification code|confirmation code|"
                           r"\bcode\s*(is|:|=)|social security|\bssn\b|routing number|\bcvv\b)", re.I)
# Nine or more digits in a run, not dates or phone numbers (team._SECRET).
_LONG_NUMBER = re.compile(r"(?<![\d-])(?!\d{4}-\d{2}-\d{2}\b)(?!\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b)(?!1-?800)"
                          r"\d(?:[ -]?\d){8,}(?![\d-])")
MONEY_BOTS = ("money",)


def sensitive(text: str) -> bool:
    """Health details or amounts: what may be kept to one Zip."""
    return bool(_HEALTH.search(text) or _AMOUNT.search(text))


def secret(text: str) -> str | None:
    """Why this text may never be saved, or None."""
    if _LONG_NUMBER.search(text):
        return "Account and card numbers are never saved as facts."
    if _SECRET_WORDS.search(text):
        return "Passwords, PINs, codes and ID numbers are never saved as facts."
    return None


def _norm_subject(s: str) -> str:
    s = " ".join(re.findall(r"[a-z0-9&'.-]+", (s or "").lower()))
    return s[:60]


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower())) - STOP


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(1, len(wa | wb))


def save(bot_id: str, text: str, subject: str = "", kind: str = "other", replaces: int | None = None,
         session_id: str | None = None, source: str = "zip", source_ref: str | None = None,
         confidence: str = "said", private: bool = False, data: dict | None = None,
         observed_at: float | None = None, limits: bool = True) -> tuple[int, str]:
    """Saves one fact. Returns (id, what to tell the Zip). A same-subject fact that says nearly the same is updated
    (replaced) instead of saved twice."""
    text = " ".join((text or "").split())
    kind = kind if kind in KINDS else "other"
    source = source if source in SOURCES else "zip"
    confidence = confidence if confidence in CONFIDENCE else "said"
    subject = _norm_subject(subject)
    if len(text) < 8:
        raise FactError("Write the fact as one complete sentence.")
    if len(text) > 400:
        raise FactError("Keep a fact under 400 characters: one fact per save.")
    why = secret(text)
    if why:
        raise FactError(why)
    # Amounts in Money's area and health details stay with the Zip that owns them; everything else is the team's.
    visibility = "owner" if (private or bot_id in MONEY_BOTS and _AMOUNT.search(text) or _HEALTH.search(text)) \
        else "team"
    t = int(now())
    c = _db()
    if limits and source == "zip":
        if session_id and c.execute("SELECT COUNT(*) FROM facts WHERE session_id=? AND source='zip' AND created_at>?",
                                    (session_id, t - 3600)).fetchone()[0] >= FACTS_PER_SESSION_HOUR:
            raise FactError("You have saved enough facts from this conversation for now. Save only what matters "
                            "later.")
        if c.execute("SELECT COUNT(*) FROM facts WHERE created_at>?", (t - 86400,)).fetchone()[0] >= FACTS_PER_DAY:
            raise FactError("The team has saved today's limit of facts. Keep this in your own memory instead.")
    with store._lock:
        same = c.execute("SELECT id FROM facts WHERE replaced_by IS NULL AND lower(text)=lower(?)", (text,)).fetchone()
        if same:
            return same["id"], f"That is already saved (fact {same['id']})."
        if source_ref and c.execute("SELECT 1 FROM facts WHERE source=? AND source_ref=? AND lower(text)=lower(?)",
                                    (source, source_ref, text)).fetchone():
            return 0, "Already saved from that source."
        if replaces is not None:
            old = c.execute("SELECT * FROM facts WHERE id=?", (replaces,)).fetchone()
            if not old:
                raise FactError(f"There is no fact {replaces} to replace.")
            if old["replaced_by"] is not None:
                replaces = _newest(c, replaces)
            subject = subject or old["subject"]
        elif subject and kind != "purchase":
            # Nearly the same sentence about the same subject is an update, not a second fact. Each purchase is its
            # own event, so two orders of the same thing both stay (supplies.py reads the gap between them).
            for r in c.execute("SELECT id, text FROM facts WHERE replaced_by IS NULL AND subject=? AND kind=?",
                               (subject, kind)):
                if _similar(r["text"], text) >= 0.5:
                    replaces = r["id"]
                    break
        fid = c.execute(
            "INSERT INTO facts (subject, kind, text, bot_id, session_id, source, source_ref, confidence, visibility, "
            "data, observed_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (subject, kind, text, bot_id, session_id, source, source_ref, confidence, visibility,
             json.dumps(data) if data else None, int(observed_at or t), t)).lastrowid
        if replaces is not None:
            c.execute("UPDATE facts SET replaced_by=? WHERE id=?", (fid, replaces))
        c.commit()
    msg = f"Saved as fact {fid}" + (f", replacing fact {replaces}" if replaces is not None else "") + "."
    if visibility == "owner":
        msg += " Only you will see it, because it holds an amount or a health detail."
    elif replaces is None and subject:
        others = [r for r in c.execute("SELECT id, text FROM facts WHERE replaced_by IS NULL AND subject=? AND id!=? "
                                       "ORDER BY id DESC LIMIT 3", (subject, fid))]
        if others:
            msg += (" Also saved about " + subject + ": " + "; ".join(f"[{r['id']}] {r['text']}" for r in others)
                    + ". If the new fact makes one of these wrong, save it again with replaces set to that number.")
    return fid, msg


def _newest(c: sqlite3.Connection, fid: int) -> int:
    seen = set()
    while fid not in seen:
        seen.add(fid)
        r = c.execute("SELECT replaced_by FROM facts WHERE id=?", (fid,)).fetchone()
        if not r or r["replaced_by"] is None:
            return fid
        fid = r["replaced_by"]
    return fid


def get(fid: int) -> dict | None:
    r = _db().execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
    return dict(r) if r else None


def _visible(bot_id: str | None) -> tuple[str, tuple]:
    """SQL for the facts a Zip may see: the team's and its own. None: the person, who sees everything."""
    if bot_id is None:
        return "1=1", ()
    return "(visibility='team' OR bot_id=?)", (bot_id,)


def current(bot_id: str | None = None, kinds: tuple | None = None, after: int = 0, limit: int = 500) -> list[dict]:
    where, args = _visible(bot_id)
    sql = f"SELECT * FROM facts WHERE replaced_by IS NULL AND id>? AND {where}"
    args = (after,) + args
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += tuple(kinds)
    rows = _db().execute(sql + " ORDER BY id DESC LIMIT ?", args + (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def replaced_since(after: int, kinds: tuple | None = None) -> list[dict]:
    """Facts up to `after` that a newer fact replaced since, so a Zip drops what it was told before."""
    sql = "SELECT * FROM facts WHERE replaced_by IS NOT NULL AND replaced_by > ? AND id <= ? AND visibility='team'"
    args: tuple = (after, after)
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += tuple(kinds)
    return [dict(r) for r in _db().execute(sql, args)]


def top_id() -> int:
    return _db().execute("SELECT COALESCE(MAX(id), 0) FROM facts").fetchone()[0]


# MARK: Searching

STOP = set("""a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing done down during each even ever few for from further get gets getting
got had has have having he her here hers how i if in into is it its itself just let lets like me more most my
myself no nor not now of off on once only or other our ours out over own please really same she should so some such
than that thats the their them then there these they this those through to too under until up very was we were what
whats when where which while who whom why will with would you your yours yourself yeah yes ok okay hey hi thanks thank
think thinking sure maybe kind sort thing things something anything want wanted need needs needed going gonna gotta
make made tell told say said know knew dont don't im i'm ive i've id i'd youre you're its it's lot still way well much
many again today tomorrow yesterday new old good great right back time one two quick quickly can't cant could
""".split())


def terms(text: str, extra_stop: set[str] | None = None) -> list[str]:
    """The words of a message worth searching for, in order, without repeats."""
    words = re.findall(r"[a-z0-9][a-z0-9&'-]*[a-z0-9]|[a-z0-9]", (text or "").lower().replace("’", "'"))
    stop = STOP | (extra_stop or set())
    out = []
    for w in words:
        w = w.strip("'-")
        if len(w) < 3 and not w.isdigit() or w in stop or w in out:
            continue
        out.append(w)
    return out[:24]


def _person_words() -> set[str]:
    try:
        from . import who
        name = f"{who.first()} {home.get('person.name') or ''}"
    except Exception:
        name = ""
    return {w for w in re.findall(r"[a-z]+", name.lower()) if len(w) > 1}


_NAME = re.compile(r"\b[A-Z][\w'.]*(?:\s+(?:&\s+)?[A-Z][\w'.]*)+")


def _with_names(query: str, ws: list[str]) -> list[str]:
    """Capitalized runs ("Whole Foods", "Death Wish", "Stop & Shop") are searched as one phrase, not as loose words:
    "foods" alone matched a dog food fact (Oct 10 eval)."""
    out = list(ws)
    for m in _NAME.finditer(query or ""):
        words = [w for w in re.findall(r"[a-z0-9']+", m.group(0).lower()) if w not in STOP]
        if len(words) < 2:
            continue
        out = [w for w in out if w not in words]
        out.append(" ".join(words))
    return out


def _fts_term(w: str) -> str:
    return '"' + w.replace('"', "") + '"'


def search(query: str, bot_id: str | None, limit: int = 5, kinds: tuple | None = None,
           exclude: set[int] | None = None, min_score: float = 0.0) -> list[dict]:
    """Current facts matching the query, best first. A fact must share at least one uncommon word with the query:
    words in more than a third of the facts (the person's name, "likes") do not count on their own."""
    ws = _with_names(query, terms(query, _person_words()))
    if not ws:
        return []
    c = _db()
    total = c.execute("SELECT COUNT(*) FROM facts WHERE replaced_by IS NULL").fetchone()[0] or 1
    where, args = _visible(bot_id)
    hits: dict[int, float] = {}
    matched: dict[int, list[tuple[str, int]]] = {}
    for w in ws:
        try:
            ids = [r[0] for r in c.execute(f"SELECT f.id FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                                          f"WHERE facts_fts MATCH ? AND f.replaced_by IS NULL AND {where}",
                                          (_fts_term(w),) + args)]
        except sqlite3.OperationalError:
            continue
        if not ids:
            continue
        df = len(ids)
        if total >= 6 and df > total / 3:
            continue
        idf = math.log(1 + total / df)
        for i in ids:
            hits[i] = hits.get(i, 0.0) + idf
            matched.setdefault(i, []).append((w, df))
    # One shared word is enough only when it is a real word that few facts have ("coffee", "accountant"); "bag" or
    # "work" alone pulled in unrelated facts (Oct 10 eval).
    for i, ms in list(matched.items()):
        if len(ms) == 1 and (len(ms[0][0]) < 4 or ms[0][1] > max(2, total * 0.02)):
            hits.pop(i, None)
    if exclude:
        for i in exclude:
            hits.pop(i, None)
    rows = []
    for fid, score in hits.items():
        r = c.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
        if r is None or (kinds and r["kind"] not in kinds):
            continue
        d = dict(r)
        # What the fact is about counts most: "order new coffee" wants the coffee fact before "ordering on Wednesdays".
        subject = d.get("subject") or ""
        if subject and any(w[:5] in subject for w in ws):
            score += 2.0
        d["score"] = round(score, 3)
        if score >= min_score:
            rows.append(d)
    rows.sort(key=lambda d: (-d["score"], -d["id"]))
    return rows[:limit]


# Turns that are not the person's words: a board task's prompt (the Zip reads the task with its tools).
_PLUMBING = re.compile(r"^work kanban task \S+$", re.I)

# The person's own words in any Zip's chats (Hermes keeps every message in an FTS5 index per profile).
_CALL_MARKERS = ("Request from the call: ", "Request from the call:")


def _state_dbs() -> list[tuple[str, Path]]:
    out = [("default", config.HERMES_HOME / "state.db")]
    for b in config.BOTS:
        if b.id != "default":
            out.append((b.id, config.HERMES_HOME / "profiles" / b.id / "state.db"))
    return [(b, p) for b, p in out if p.exists()]


def _lane_sessions() -> set[str]:
    rows = store.db().execute("SELECT session_id FROM bot_sessions WHERE bot_id LIKE '%#%'").fetchall()
    return {r[0] for r in rows if r[0]}


def _own_words(content: str) -> str | None:
    """The person's words in a user message, or None for the hub's own notes and teammates' questions."""
    t = (content or "").strip()
    for m in _CALL_MARKERS:
        if m in t:
            return t.split(m, 1)[1].strip()
    if not t or t.startswith("["):
        return None
    return t


_REQUEST = re.compile(r"^(can|could|would|will|please|order|add|put|start|make|send|email|draft|find|check|show|"
                      r"get|book|remind|what|what's|whats|who|who's|where|when|how|why|is|are|do|does)\b", re.I)


def _states_something(text: str) -> bool:
    """The person's words say something about their life, not only ask: no question, and a request only when it is
    long enough to carry details ("Start a Walmart list: paper towels, dog food and batteries")."""
    t = " ".join(text.split())
    if t.endswith("?"):
        return False
    return not (_REQUEST.match(t) and len(t.split()) < 8)


def _snippet(text: str, ws: list[str], width: int) -> str:
    low = text.lower()
    pos = [low.find(w[:6]) for w in ws if low.find(w[:6]) >= 0]
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    start = max(0, min(pos) - width // 3) if pos else 0
    cut = " ".join(text[start:].split())[:width]
    cut = cut.rsplit(" ", 1)[0] if len(cut) == width else cut
    return ("..." if start else "") + cut + ("..." if start + len(cut) < len(text) else "")


def message_search(query: str, bot_id: str | None, exclude_session: str | None = None, limit: int = 3,
                   width: int = 160, min_terms: int = 1) -> list[dict]:
    """The person's own past messages to any Zip that match the query, newest relevant first. Words from Money's chat
    go only to Money when they hold an amount (as its facts do)."""
    ws = terms(query, _person_words())
    if not ws:
        return []
    q = " OR ".join(_fts_term(w) for w in ws)
    lanes = _lane_sessions()
    found = []
    for zip_id, path in _state_dbs():
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            rows = c.execute(
                "SELECT m.id, m.session_id, m.content, m.timestamp, bm25(messages_fts) AS rank FROM messages_fts "
                "JOIN messages m ON m.id = messages_fts.rowid JOIN sessions s ON s.id = m.session_id "
                "WHERE messages_fts MATCH ? AND m.role='user' AND s.source='api_server' "
                "ORDER BY rank LIMIT 12", (q,)).fetchall()
            c.close()
        except sqlite3.Error:
            continue
        for mid, sid, content, ts, rank in rows:
            if sid == exclude_session or sid in lanes:
                continue
            words = _own_words(content)
            if not words or len(words) < 12 or not _states_something(words) or _similar(words, query) >= 0.5:
                continue
            low = words.lower()
            matched = [w for w in ws if w[:6] in low]
            if len(matched) < min_terms:
                continue
            if zip_id in MONEY_BOTS and zip_id != bot_id and _AMOUNT.search(words):
                continue
            if secret(words):
                continue
            found.append({"bot_id": zip_id, "session_id": sid, "message_id": mid, "ts": ts, "rank": rank,
                          "matched": len(matched), "text": _snippet(words, matched, width)})
    found.sort(key=lambda r: (-r["matched"], r["rank"], -(r["ts"] or 0)))
    out, seen = [], set()
    for r in found:
        key = r["text"][:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


# MARK: What a turn is given

def _name(bot_id: str) -> str:
    b = config.BOTS_BY_ID.get(bot_id)
    return b.name if b else ("the hub" if bot_id == "hub" else bot_id)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, home.TZ).strftime("%b %-d")


def source_words(f: dict) -> str:
    """Where a fact came from, in plain words (for the Zips and for the app)."""
    from . import who
    you = who.first()
    day = _day(f.get("observed_at") or f.get("created_at") or now())
    zip_name = _name(f.get("bot_id") or "")
    s = f.get("source")
    if s == "mail":
        return f"from an order email, {day}"
    if s == "cart":
        return f"from a cart {zip_name} prepared, {day}"
    if s == "call":
        return f"{you} said it on a call with {zip_name}, {day}"
    if s == "app":
        return f"{you} added it in the app, {day}"
    if s == "import":
        return f"from {zip_name}'s notes, {day}"
    if s == "chat":
        return f"{you} said it to {zip_name}, {day}"
    if f.get("confidence") == "inferred":
        return f"{zip_name} worked it out, {day}"
    return f"{zip_name} noted it, {day}"


def _line(f: dict) -> str:
    subj = f"{f['subject'][:1].upper()}{f['subject'][1:]}: " if f.get("subject") else ""
    return f"- {subj}{f['text']} ({source_words(f)})"


def _shown(session_id: str) -> set[int]:
    return {r[0] for r in _db().execute("SELECT fact_id FROM fact_shown WHERE session_id=?", (session_id,))}


def _mark_shown(session_id: str, ids: list[int]) -> None:
    if not session_id or not ids:
        return
    t = int(now())
    with store._lock:
        c = _db()
        c.executemany("INSERT OR IGNORE INTO fact_shown (session_id, fact_id, at) VALUES (?,?,?)",
                      [(session_id, i, t) for i in ids])
        c.execute("DELETE FROM fact_shown WHERE at < ?", (t - 30 * 86400,))
        c.commit()


def relevant(message: str, bot_id: str, session_id: str = "", given: set[int] | None = None) -> str:
    """"Relevant to this message" for the start of a turn: facts and the person's own earlier words that match it,
    under RELEVANT_MAX_CHARS. Facts this conversation was already given (here or in the team notes stream, `given`)
    are left out, so a long chat is not told the same thing every turn."""
    raw = (message or "").strip()
    asked = re.match(r"^\[From [^\]]{1,40}\]\s*", raw)   # a teammate's question: search its words
    message = raw[asked.end():].split("\n\n(")[0] if asked else (_own_words(raw) or "")
    if len(message) < 6 or _PLUMBING.match(message):
        return ""
    from . import who
    skip = (_shown(session_id) if session_id else set()) | (given or set())
    found = search(message, bot_id, limit=RELEVANT_FACTS, exclude=skip)
    lines = [_line(f) for f in found]
    used = [f["id"] for f in found]
    snippets = message_search(message, bot_id, exclude_session=session_id or None, limit=RELEVANT_SNIPPETS,
                              min_terms=2 if found else 1)
    fact_text = " ".join(f["text"].lower() for f in found)
    for s in snippets:
        if s["text"][:40].lower() in fact_text:
            continue
        lines.append(f'- {who.first()} to {_name(s["bot_id"])}, {_day(s["ts"])}: "{s["text"]}"')
    if not lines:
        return ""
    head = (f"[Relevant to this message, from what {who.first()} told the team before. Use it so "
            f"{who.first()} never has to repeat it; a newer word from {who.first()} wins.]")
    out, size = [head], len(head)
    kept = []
    for i, line in enumerate(lines):
        if size + len(line) + 1 > RELEVANT_MAX_CHARS and len(out) > 1:
            break
        out.append(line[:RELEVANT_MAX_CHARS - len(head) - 2] if len(out) == 1 else line)
        size += len(line) + 1
        if i < len(used):
            kept.append(used[i])
    _mark_shown(session_id, kept)
    return "\n".join(out)


def recall(query: str, bot_id: str, session_id: str = "") -> str:
    """The recall tool: everything the team knows that matches, with sources and dates, and the person's own words."""
    from . import who
    found = search(query, bot_id, limit=12)
    past = message_search(query, bot_id, limit=6, width=300)
    if not found and not past:
        return (f"Nothing saved matches \"{query}\". Try other words, or ask {who.first()}; save the answer with "
                "save_fact.")
    parts = []
    if found:
        parts.append("Saved facts (newest wins when two disagree):\n" + "\n".join(
            f"[{f['id']}] {_line(f)[2:]}" for f in found))
        _mark_shown(session_id, [f["id"] for f in found])
    if past:
        parts.append(f"{who.possessive()} own words in earlier chats:\n" + "\n".join(
            f'- To {_name(p["bot_id"])}, {_day(p["ts"])}: "{p["text"]}"' for p in past))
    return "\n\n".join(parts)


def briefing_text(bot_id: str, limit_chars: int = 6000) -> str:
    """Every current fact this Zip may see, grouped by kind, newest first, for call briefings."""
    rows = current(bot_id)
    if not rows:
        return ""
    by_kind: dict[str, list[str]] = {}
    for f in reversed(rows):
        by_kind.setdefault(f["kind"], []).append(_line(f))
    out, size = [], 0
    for k in KINDS:
        lines = by_kind.get(k, [])
        if not lines or size > limit_chars:
            continue
        out.append(KIND_HEADINGS[k] + ":")
        for line in lines:
            if size + len(line) > limit_chars:
                break
            out.append(line)
            size += len(line) + 1
    return "\n".join(out)


# MARK: The app's "What I Know About You"

KIND_HEADINGS = {"preference": "Preferences", "place": "Places", "purchase": "Purchases", "supply": "Supplies",
                 "person": "People", "plan": "Plans", "decision": "Decisions", "other": "Other Things"}


def for_app() -> list[dict]:
    """Every current fact, for the person, with its source in plain words. The person sees all of them."""
    out = []
    for f in reversed(current(None, limit=2000)):
        out.append({"id": f["id"], "text": f["text"], "subject": f["subject"], "kind": f["kind"],
                    "heading": KIND_HEADINGS.get(f["kind"], "Other Things"), "source": source_words(f).capitalize(),
                    "private": f["visibility"] == "owner", "observed_at": f["observed_at"],
                    "edited": bool(f.get("edited_at"))})
    return out


def edit(fid: int, text: str) -> dict:
    """The person changed a fact's wording. Their words are the fact now (said directly)."""
    text = " ".join((text or "").split())
    if len(text) < 3 or len(text) > 400:
        raise FactError("Keep it to one sentence.")
    why = secret(text)
    if why:
        raise FactError(why)
    with store._lock:
        c = _db()
        if not c.execute("SELECT 1 FROM facts WHERE id=? AND replaced_by IS NULL", (fid,)).fetchone():
            raise FactError("That fact is gone.")
        c.execute("UPDATE facts SET text=?, edited_at=?, confidence='said' WHERE id=?", (text, int(now()), fid))
        c.commit()
    return get(fid) or {}


def delete(fid: int) -> bool:
    """The person deleted a fact: it is gone, with every older version it replaced, and never comes back from them."""
    with store._lock:
        c = _db()
        ids, todo = set(), [fid]
        while todo:
            i = todo.pop()
            if i in ids:
                continue
            ids.add(i)
            todo += [r[0] for r in c.execute("SELECT id FROM facts WHERE replaced_by=?", (i,))]
        if not c.execute("SELECT 1 FROM facts WHERE id=?", (fid,)).fetchone():
            return False
        marks = ",".join("?" * len(ids))
        c.execute(f"DELETE FROM facts WHERE id IN ({marks})", tuple(ids))
        c.execute(f"DELETE FROM fact_shown WHERE fact_id IN ({marks})", tuple(ids))
        c.commit()
    try:
        from . import team
        team.write_notes_file()
    except Exception:
        pass
    return True


def add_by_person(text: str, kind: str = "other", subject: str = "") -> dict:
    fid, _ = save("default", text, subject=subject, kind=kind, source="app", confidence="said", limits=False)
    return get(fid) or {}


# MARK: Calls (voice.py): quiet context for the call voice while the person talks

CALL_FACTS = 4
CALL_SNIPPETS = 2
CALL_MAX_CHARS = 900
CALL_BACKGROUND = 3


def call_context(text: str, bot_id: str, pushed: set | None = None) -> tuple[str, list]:
    """Facts and the person's own earlier words that match what they are saying on a call, for the voice as a quiet
    note. FTS only (well under 100 ms), so it can run on the words as they are recognized. Returns (note, keys) where
    keys are what this note adds (fact ids and message keys), so a call never gets the same thing twice; ("", []) when
    there is nothing new."""
    from . import who
    pushed = pushed if pushed is not None else set()
    found = [f for f in search(text, bot_id, limit=CALL_FACTS + 4) if f["id"] not in pushed][:CALL_FACTS]
    if any(f["kind"] in ("plan", "decision") for f in found):
        # Thinking a plan or decision through: the person's other plans are what it may clash with, even when no
        # word matches ("it could get in the way of my other passions"; Oct 10 probe).
        have = {f["id"] for f in found} | pushed
        found += [f for f in reversed(current(bot_id, kinds=("plan", "decision"), limit=8))
                  if f["id"] not in have][:CALL_BACKGROUND]
    lines, keys = [], []
    for f in found:
        lines.append(_line(f))
        keys.append(f["id"])
    fact_text = " ".join(f["text"].lower() for f in found)
    for s in message_search(text, bot_id, limit=CALL_SNIPPETS + 2, width=200, min_terms=2 if found else 1):
        key = f"m:{s['bot_id']}:{s['message_id']}"
        if key in pushed or s["text"][:40].lower() in fact_text or len(keys) >= CALL_FACTS + CALL_BACKGROUND + CALL_SNIPPETS:
            continue
        lines.append(f'- {who.first()} told {_name(s["bot_id"])} on {_day(s["ts"])}: "{s["text"]}"')
        keys.append(key)
    if not lines:
        return "", []
    head = (f"[Context for this call: what you know about this from {who.possessive()} notes. Use it naturally; never "
            f"read it out or say where it came from. A newer word from {who.first()} wins.]")
    out, size = [head], len(head)
    for line in lines:
        if size + len(line) > CALL_MAX_CHARS:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out), keys[:len(out) - 1]


REFINE_PROMPT = """You help the voice of {name}, {person}'s assistant, during a live phone call. {person} just said:
"{said}"

Below are notes about {person} that a quick search found. Pick only what truly bears on what {person} is saying, and \
add what a thoughtful friend who knows {person} would keep in mind (a goal it may clash with, a date, a person). Write \
at most three short plain lines of facts, then one line starting "Ask:" with the single sharpest question to help \
{person} think it through. If none of the notes bear on it, answer exactly: NONE

Notes:
{notes}"""


def refine_material(text: str, bot_id: str) -> str:
    """The wider set of notes the Haiku refine reads (more than the quick note carries)."""
    found = search(text, bot_id, limit=12)
    rows = [f"- {f['text']} ({source_words(f)})" for f in found]
    for s in message_search(text, bot_id, limit=5, width=240):
        rows.append(f'- Said to {_name(s["bot_id"])} on {_day(s["ts"])}: "{s["text"]}"')
    # Plans, decisions and people are the background a decision is weighed against, even when no word matches.
    for f in current(bot_id, kinds=("plan", "decision", "person"), limit=12):
        line = f"- {f['text']} ({source_words(f)})"
        if line not in rows:
            rows.append(line)
    return "\n".join(rows[:30])


async def call_refine(text: str, bot_id: str, ask=None) -> str:
    """Haiku (low effort where the plan allows) reads the person's words with the wider notes and returns a short note
    for the voice, or "" (voice.py measures how long it takes and pushes it only if it is still timely)."""
    from . import briefing, who
    notes = refine_material(text, bot_id)
    if not notes:
        return ""
    name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "Zero"
    prompt = REFINE_PROMPT.format(name=name, person=who.first(), said=text[:1200], notes=notes)
    out = (await (ask or (lambda p: briefing.ask_haiku(p, timeout=20, effort="low")))(prompt)).strip()
    if not out or out.upper().startswith("NONE"):
        return ""
    return (f"[More context for this call, after a closer look. Use it naturally; never read it out.]\n" + out)[:900]
