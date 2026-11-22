""





























from __future__ import annotations

import functools
import json
import logging
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import config, factdates, home, shortreply, store

log = logging.getLogger("taffy.facts")



KINDS = ("preference", "place", "purchase", "supply", "person", "plan", "decision", "status", "other")

BROADCAST = ("person", "plan", "decision", "status", "other")
SOURCES = ("zip", "chat", "mail", "cart", "call", "app", "import", "setup")



STATUSES = ("current", "past", "cancelled")
CONFIDENCE = ("said", "inferred", "receipt")
RELEVANT_MAX_CHARS = 600
RELEVANT_FACTS = 5
RELEVANT_SNIPPETS = 2

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



class FactError(Exception):
    ""


def _db() -> sqlite3.Connection:
    return store.db()


@store.schema
def _tables(c: sqlite3.Connection) -> None:

    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(facts)")}
    for name, definition in {"valid_from": "INTEGER", "valid_until": "INTEGER",
                             "status": "TEXT NOT NULL DEFAULT 'current'", "evidence": "TEXT"}.items():
        if name not in cols:
            c.execute(f"ALTER TABLE facts ADD COLUMN {name} {definition}")
    if "valid_until" not in cols:
        _until_from_text(c)
    _from_team_notes(c)

    private = sorted(config.PRIVATE_ZIPS)
    c.execute(f"UPDATE facts SET visibility='owner' WHERE visibility='team' AND bot_id IN ({','.join('?' * len(private))})",
              private)


def _until_from_text(c: sqlite3.Connection) -> None:
    ""
    for r in c.execute("SELECT id, text, observed_at FROM facts WHERE kind='plan' AND valid_until IS NULL").fetchall():
        start, end = factdates.span(r[1], r[2])
        if end is not None:
            c.execute("UPDATE facts SET valid_from=?, valid_until=? WHERE id=?", (int(start), int(end), r[0]))


def _from_team_notes(c: sqlite3.Connection) -> None:
    ""

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


def now() -> float:
    ""
    return time.time()


def live(alias: str = "") -> tuple[str, tuple]:
    ""
    a = f"{alias}." if alias else ""
    return f"{a}replaced_by IS NULL AND ({a}valid_until IS NULL OR {a}valid_until > ?)", (int(now()),)


def state(f: dict, at: float | None = None) -> str:
    ""
    until = f.get("valid_until")
    if f.get("status") == "cancelled":
        return "cancelled"
    if f.get("status") == "past" or until is not None and until <= (now() if at is None else at):
        return "past"
    return "current"




_AMOUNT = re.compile(r"\$\s?\d|\b\d[\d,]*(\.\d\d)?\s*(dollars|bucks|usd|cad)\b", re.I)


_HEALTH = re.compile(r"diagnos|medication|\bprescribed\b|\b(takes|taking|on) (\w+ )?prescription|therapy|\bsymptom|\ballerg(y|ic)\b.*\b(medic|epipen|inhaler)",
                     re.I)
_SECRET_WORDS = re.compile(r"(password|passcode|\bpin\b|\botp\b|security code|verification code|confirmation code|"
                           r"\bcode\s*(is|:|=)|social security|\bssn\b|routing number|\bcvv\b)", re.I)

_LONG_NUMBER = re.compile(r"(?<![\d-])(?!\d{4}-\d{2}-\d{2}\b)(?!\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b)(?!1-?800)"
                          r"\d(?:[ -]?\d){8,}(?![\d-])")
MONEY_BOTS = ("money",)



_HOLDINGS = re.compile(r"\bbalance|\bowe[sd]?\b|\bowing\b|\bdebt\b|\bleft\s+(?:on|to\s+pay)\b|\bremaining\b|\bpaid\s+off\b"
                       r"|\$\s?[\d,.]+k?\s+(?:left|outstanding)\b"
                       r"|\bin\s+(?:\w+\s+)?(?:checking|savings)\b|\bnet\s+worth\b|\bcredit\s+limit\b", re.I)
_GRADE = re.compile(r"\bGPA\b|\bgrade[sd]?\s+(?:of|was|is|:)|\bgot\s+an?\s+[A-F][+-]?(?=[\s.,;]|$)|\bscored\s+\d"
                    r"|\b\d{1,3}(?:\.\d+)?\s?%\s+(?:on|in)\s+(?:the|h(?:is|er)|their|a|an)\b", re.I)


def sensitive(text: str) -> bool:
    ""
    return bool(_HEALTH.search(text) or _AMOUNT.search(text))


def secret(text: str) -> str | None:
    ""
    if _LONG_NUMBER.search(text):
        return "Account and card numbers are never saved as facts."
    if _SECRET_WORDS.search(text):
        return "Passwords, PINs, codes and ID numbers are never saved as facts."
    return None









_SELF = r"\b(?:h(?:im|er)self|themselves|on (?:h(?:is|er)|their) own)\b"
_HANDS_OFF = (r"\b(?:will|'ll|is to|are to|plans? to|is going to|wants? to|would rather|prefers? to|must|needs? to|"
              r"has to)\b[^.]*" + _SELF,
              r"\b(?:does|handles|completes|fills|submits|places|files|sends|signs|pays|books|orders|buys|calls|emails|"
              r"enters|types|presses|approves|sets up)\b[^.]*" + _SELF,
              r"\bonly {who} (?:can|could|should|must|is able to)\b",
              r"\bleave (?:it|this|that|them|the [\w ]+?) (?:up )?(?:to|for) {whom}\b",
              r"\b{who} (?:will|'ll|is going to|plans to|wants to) (?:handle|take care of|deal with)\b")
_MINE = re.compile(r"\b(?:myself|on my own|I(?:'ll| will| can|'m going to| am going to)|let me|I(?:'ve)? got it)\b",
                   re.I)


@functools.lru_cache(maxsize=8)
def _hands_off_re(name: str) -> re.Pattern:
    named = "|" + re.escape(name) if name else ""
    who, whom = rf"(?:he|she|they{named})", rf"(?:h(?:im|er)|them{named})"
    return re.compile("|".join(p.format(who=who, whom=whom) for p in _HANDS_OFF), re.I)


def hands_off(text: str) -> bool:
    ""
    from . import who
    return bool(_hands_off_re(who.first()).search((text or "").replace("\u2019", "'")))


def _flat_words(text: str | None) -> str:
    return " ".join(str(text or "").replace("\u2019", "'").split()).casefold()


def owned_by_words(text: str, quote: str | None, own: str) -> bool:
    ""

    if not hands_off(text):
        return True
    q, said = _flat_words(quote), _flat_words(own)
    return bool(q) and q in said and bool(_MINE.search(q))


def _sentences(text: str) -> list[str]:
    return [s for s in (" ".join(p.split()) for p in re.split(r"(?<=[.!?])\s+|\n+", text or "")) if s]



_COMMON = frozenset("will would could should must need needs want wants plan plans going rather does done have from "
                    "with that this them they their there then than when what about into only just like sure okay yeah "
                    "thanks please".split())


def _words(text: str) -> set[str]:
    from . import who
    words = set(re.findall(r"[a-z]{4,}", (text or "").casefold())) - _COMMON - {who.first().casefold()}
    return {w for w in words if not w.endswith(("self", "selves"))}


def said_so(text: str, own: str) -> bool:
    ""


    words = _words(text)
    return any(_MINE.search(s) and words & _words(s) for s in _sentences((own or "").replace("\u2019", "'")))


def without_hand_offs(text: str, own: str) -> str:
    ""

    mine = said_so(text, own)
    return " ".join(s for s in _sentences(text) if mine or not hands_off(s))


def hand_offs(text: str, old: str = "") -> list[str]:
    ""

    had = set(_sentences(old))
    return [s for s in _sentences(text) if s not in had and hands_off(s)]







RULES = ("status", "preference", "decision")


ECHO_SHARE = 0.2
_RULE_WORDS = frozenset("prefer prefers decide decided".split())
_SAID_WORD = re.compile(r"[a-z0-9][a-z0-9&'-]*[a-z0-9]")


def _plural(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _about(text: str) -> set[str]:
    ""
    return {_plural(w) for w in terms(text, _person_words() | _COMMON | _RULE_WORDS) if not w.isdigit()}


def echoes(text: str, own: str) -> bool:
    ""

    about = _about(text)
    shared = about & {_plural(w) for w in _SAID_WORD.findall(_flat_words(own))}
    return bool(about) and len(shared) >= min(2, len(about)) and len(shared) / len(about) > ECHO_SHARE


def _word_starts(text: str) -> set[str]:
    ""


    words = set(_SAID_WORD.findall(_flat_words(text))) - _COMMON - _person_words()
    return {w[:5] for w in words if len(w) > 3}


def quoted(text: str, quote: str | None, own: str) -> bool:
    ""

    q = _flat_words(quote)
    return bool(q) and q in _flat_words(own) and bool(_word_starts(text) & _word_starts(q))


def _norm_subject(s: str) -> str:
    s = " ".join(re.findall(r"[a-z0-9&'.-]+", (s or "").lower()))
    return s[:60]


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower())) - STOP


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(1, len(wa | wb))


KEEP_TO_REPLACE = 0.8


def _kept(old: str, new: str) -> float:
    ""
    wo = _words(old)
    return len(wo & _words(new)) / max(1, len(wo))










RESTATES = 0.5



_FILLER = frozenset("a an the and he she it they them said says also".split())


_TURNS = frozenset("not no nor never neither none nothing nobody without stop stops stopped cancel cancels cancelled "
                   "canceled quit quits or maybe might unless except if probably possibly tentative tentatively "

                   "former formerly ex previous previously old past until anymore".split())

_ALIAS = re.compile(r"\b([A-Z][\w&'-]*(?:\s+(?:and|of|the|&|[A-Z][\w&'-]*))*)\s+\(([A-Z]{2,4}[ -]?\d{3})\)|"
                    r"\b([A-Z]{2,4}[ -]?\d{3})\s+\(([A-Z][a-z][\w&' -]{1,60})\)")


def _plain(text: str) -> str:
    ""

    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"n't\b", " not", re.sub(r"\bwon't\b", "will not", re.sub(r"\bcan't\b", "can not", t)))
    t = re.sub(r"'s\b", "", t)
    return re.sub(r"(\d)\s*([ap])\.?\s?m\b\.?", r"\1\2m", t)


def _aliases(texts: list[str]) -> dict[str, str]:
    ""
    out = {}
    for t in texts:
        for m in _ALIAS.finditer(t or ""):
            name, code = (m[1], m[2]) if m[1] else (m[4], m[3])
            if re.search(r"[a-z]", name):
                out[_plain(name).strip()] = re.sub(r"([a-z]+)[ -]?(\d+)", r"\1 \2", code.lower())
    return out


def _gist(text: str, aliases: dict[str, str]) -> list[str]:
    ""

    t = _plain(text)
    for name, code in aliases.items():

        t = re.sub(rf"\b{re.escape(code)}\s*\(\s*{re.escape(code)}\s*\)", code,
                   re.sub(rf"\b{re.escape(name)}\b", code, t))
    return [w for w in re.findall(r"[a-z0-9]+(?:[-.,:/'][a-z0-9]+)*", t) if w not in _FILLER]


def _in_order(new: list[str], old: list[str]) -> bool:
    ""

    rest = iter(old)
    return all(w in rest for w in new)


def _turns(text: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z]+", _plain(text)) if w in _TURNS)


def _covers(c: sqlite3.Connection, world: str, world_args: tuple, old: dict, new: dict) -> bool:
    ""

    aliases, me = _aliases_for(c, world, world_args), _person_words()
    closes = _closed_by(new, aliases, me)
    return closes <= _closed_by(old, aliases, me) if closes else _flat(new["text"]) == _flat(old["text"])


def _same_message(new: dict | None, old: str | None) -> bool:
    ""
    try:
        o = json.loads(old) if old else {}
        n = new if isinstance(new, dict) else {}
        if not isinstance(o, dict):
            return False
        if n.get("message_id") and n.get("session_id") and (n["message_id"], n["session_id"]) == (
                o.get("message_id"), o.get("session_id")):
            return True
        quote = [" ".join(str(e.get("quote") or "").lower().split())[:40] for e in (n, o)]
        return bool(n.get("at") and quote[0]) and int(n["at"]) == int(o.get("at") or 0) and quote[0] == quote[1]
    except (TypeError, ValueError):
        return False


def restated(text: str, kind: str, bot_id: str, visibility: str = "team", evidence: dict | None = None,
             exclude: set[int] | frozenset = frozenset()) -> dict | None:
    ""



    if kind == "purchase":
        return None
    c = _db()
    holds, now_arg = live("f")
    world, world_args = _world(bot_id)
    aliases = _aliases([text] + [r[0] for r in c.execute(
        f"SELECT f.text FROM facts f WHERE {holds} AND {world} AND f.text LIKE '%(%'", now_arg + world_args)])
    new = _gist(text, aliases)
    words = set(new) - STOP - _person_words()
    if not words:
        return None

    names = [n for n, code in aliases.items() if set(code.split()) <= words]
    kinds = ("decision", "preference") if kind in ("decision", "preference") else (kind,)
    seen_by = "f.visibility='team'" + (" OR f.bot_id=?" if visibility == "owner" else "")
    try:
        rows = c.execute(
            f"SELECT f.* FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ? AND {holds} "
            f"AND {world} AND f.kind IN ({','.join('?' * len(kinds))}) AND f.source != 'mail' AND ({seen_by}) "
            "ORDER BY rank LIMIT 20",
            (" OR ".join(_fts_term(w) for w in sorted(words) + names),) + now_arg + world_args + kinds
            + ((bot_id,) if visibility == "owner" else ())).fetchall()
    except sqlite3.OperationalError:
        return None
    turns, best, alike = _turns(text), None, 0.0
    for r in rows:
        if r["id"] in exclude or _turns(r["text"]) != turns:
            continue
        old = _gist(r["text"], aliases)
        if not _in_order(new, old):
            continue
        score = len(set(new)) / max(1, len(set(old)))
        if (score >= RESTATES or _same_message(evidence, r["evidence"])) and score > alike:
            best, alike = dict(r), score
    return best








REPEATS = 0.5
REPEAT_KEPT = 0.7



_TOKEN = re.compile(r"[\w'-]+(?:[.,:/][\w-]+)*|[.!?:\"“”]")


def _marks(text: str) -> set[str]:
    out, prev = set(), "."
    for w in _TOKEN.findall((text or "").replace("’", "'")):
        if any(ch.isdigit() for ch in w) or (w[0].isupper() and prev not in '.!?:"“”'):
            out.add(re.sub(r"'s$", "", w.lower()))
        prev = w
    return out


def _names_kept(new: str, old: str) -> bool:
    words = {re.sub(r"'s$", "", w) for w in _TOKEN.findall((old or "").lower().replace("’", "'"))}
    zips = {w for b in config.BOTS for w in re.findall(r"[a-z]+", b.name.lower())}
    return _marks(new) - _person_words() - zips <= words





_OPPOSITES = tuple(frozenset(g.split()) for g in (
    "before after during until", "above below over under", "up down", "on off", "more most less least fewer",
    "now later once again today tomorrow yesterday", "new old", "all any some each every few none no"))


def _swapped(a: str, b: str) -> bool:
    wa, wb = (set(re.findall(r"[a-z0-9]+", (t or "").lower())) for t in (a, b))
    return any((wa - wb) & g and (wb - wa) & g for g in _OPPOSITES)


def _turned(text: str) -> frozenset:
    ""
    words = _gist(text, {})
    return frozenset((w, words[i + 1] if i + 1 < len(words) else "") for i, w in enumerate(words) if w in _TURNS)







_NOTE_KINDS = ("place", "supply", "person", "plan", "other")


def _says_all(new: str, old: str) -> bool:
    ""



    return _in_order(_gist(new, {}), _gist(old, {}))


def repeats(text: str, kind: str, bot_id: str, visibility: str = "team",
            exclude: set[int] | frozenset = frozenset()) -> dict | None:
    ""


    rule = kind in ("preference", "decision")
    if not rule and kind not in _NOTE_KINDS:
        return None
    holds, now_arg = live("f")
    world, world_args = _world(bot_id)
    kinds = ("preference",) if kind == "preference" else ("preference", "decision") if rule \
        else _NOTE_KINDS if kind == "other" else (kind,)
    seen_by = "f.visibility='team'" + (" OR f.bot_id=?" if visibility == "owner" else "")
    rows = _db().execute(
        f"SELECT f.* FROM facts f WHERE {holds} AND {world} AND f.kind IN ({','.join('?' * len(kinds))}) "
        f"AND f.source != 'mail' AND ({seen_by})",
        now_arg + world_args + kinds + ((bot_id,) if visibility == "owner" else ())).fetchall()
    turned, best, rank = _turned(text), None, None
    for r in rows:
        if r["id"] in exclude or _turned(r["text"]) != turned or _swapped(text, r["text"]):
            continue
        alike = _similar(text, r["text"])
        if alike >= REPEATS and _names_kept(text, r["text"]) and (
                _kept(text, r["text"]) >= REPEAT_KEPT if rule else _says_all(text, r["text"])):
            mine = (r["kind"] == "preference", alike)
            if rank is None or mine > rank:
                best, rank = dict(r), mine
    return best


def save(bot_id: str, text: str, subject: str = "", kind: str = "other", replaces: int | list[int] | None = None,
         session_id: str | None = None, source: str = "zip", source_ref: str | None = None,
         confidence: str = "said", private: bool = False, data: dict | None = None,
         observed_at: float | None = None, limits: bool = True, until: float | None = None,
         evidence: dict | None = None, merge: bool = True, exclude: set[int] | None = None,
         scope: str | None = None, seen: dict[int, list] | None = None) -> tuple[int, str]:
    ""








    text = " ".join((text or "").split())


    reps = list(dict.fromkeys([replaces] if isinstance(replaces, int) else replaces or []))
    replaces = reps[0] if reps else None
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


    sandbox = config.is_sandbox(bot_id)
    visibility = "owner" if (sandbox or private or config.is_private(bot_id) or scope == "zip" or bot_id in MONEY_BOTS and _AMOUNT.search(text)
                             or _AMOUNT.search(text) and _HOLDINGS.search(text)
                             or _HEALTH.search(text) or _GRADE.search(text)) else "team"
    world, world_args = _world(bot_id)
    t = int(now())
    seen_at = int(observed_at or t)
    start = None
    if until is None and kind == "plan":
        end = data.get("end") if isinstance(data, dict) else None

        start, until = (data.get("start"), end) if isinstance(end, (int, float)) and end > 0 \
            else factdates.span(text, seen_at)
    valid_from = int(start) if isinstance(start, (int, float)) else seen_at
    c = _db()



    with store._lock:
        if seen is not None and reps:
            still = [r for r in reps if unchanged({r: seen.get(r)})]
            if len(still) < len(reps):
                log.info("a note changed while it was read, so this save leaves it as it is")
                merge = False
            reps, replaces = still, still[0] if still else None


        booking = source == "mail" and kind == "plan" and isinstance(data, dict) and data.get("booking") is True
        scope = " AND subject=? AND data=?" if booking else ""
        same_args = (text, subject, json.dumps(data)) if booking else (text,)
        holds, now_arg = live()
        same = c.execute(f"SELECT id FROM facts WHERE {holds} AND {world} AND lower(text)=lower(?)" + scope,
                         now_arg + world_args + same_args).fetchone()
        if same and all(_newest(c, r) == same["id"] for r in reps):
            return same["id"], f"That is already saved (fact {same['id']})."
        if source_ref and c.execute("SELECT 1 FROM facts WHERE source=? AND source_ref=? AND lower(text)=lower(?)",
                                    (source, source_ref, text)).fetchone():
            return 0, "Already saved from that source."

        has = restated(text, kind, bot_id, visibility, evidence, exclude or set()) \
            if replaces is None and source != "mail" else None
        if has:
            log.info("restatement not saved: %r says nothing fact %d does not: %r", text, has["id"], has["text"])
            return has["id"], f"That is already saved (fact {has['id']})."
        if kind == "status" and replaces is None and evidence:

            new = {"kind": "status", "text": text, "evidence": json.dumps(evidence), "data": json.dumps(data)}
            for r in c.execute(f"SELECT * FROM facts WHERE {holds} AND {world} AND kind='status'",
                               now_arg + world_args):
                if _same_message(evidence, r["evidence"]) and _covers(c, world, world_args, dict(r), new):
                    return r["id"], f"That is already saved (fact {r['id']})."
        if reps:
            for i, rid in enumerate(reps):
                old = c.execute(f"SELECT * FROM facts WHERE id=? AND {world}", (rid,) + world_args).fetchone()
                if not old:
                    raise FactError(f"There is no fact {rid} to replace.")
                if old["replaced_by"] is not None:
                    reps[i] = _newest(c, rid)
                subject = subject or old["subject"]

            for rid in reps if observed_at is not None else ():
                if last_said(get(rid) or {}) > seen_at:
                    return rid, f"A newer fact already covers this (fact {rid})."
        elif subject and kind != "purchase" and merge:


            for r in c.execute(f"SELECT id, text, observed_at, edited_at FROM facts WHERE {holds} AND {world} AND subject=? "
                               "AND kind=?",
                               now_arg + world_args + (subject, kind)):


                if _similar(r["text"], text) >= 0.5 and _kept(r["text"], text) >= KEEP_TO_REPLACE:
                    if observed_at is not None and last_said(dict(r)) > seen_at:
                        return r["id"], f"A newer fact already covers this (fact {r['id']})."
                    replaces = r["id"]
                    reps = [replaces]
                    break
        if replaces is None and merge and source != "mail" and (has := repeats(text, kind, bot_id, visibility,
                                                                                 exclude or set())):
            log.info("repeat not saved: %r says what fact %d does: %r", text, has["id"], has["text"])
            return has["id"], f"That is already saved (fact {has['id']})."
        fid = c.execute(
            "INSERT INTO facts (subject, kind, text, bot_id, session_id, source, source_ref, confidence, visibility, "
            "data, observed_at, created_at, valid_from, valid_until, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (subject, kind, text, bot_id, session_id, source, source_ref, confidence, visibility,
             json.dumps(data) if data else None, seen_at, t, valid_from,
             int(until) if until is not None else None, _evidence(evidence))).lastrowid


        ended = list(dict.fromkeys(reps + ([same["id"]] if same else [])))
        for old_id in ended:
            c.execute("UPDATE facts SET replaced_by=? WHERE id=?", (fid, old_id))
        c.commit()
    retired = retire(fid) if kind == "status" else []
    msg = f"Saved as fact {fid}" + (f", replacing fact{'s' if len(ended) > 1 else ''} "
                                    + " and ".join(map(str, ended)) if ended else "") + "."
    if retired:
        msg += (f" It also ended {len(retired)} older note{'s' if len(retired) > 1 else ''} about it ("
                + ", ".join(map(str, retired)) + "); recall still finds them.")
    if scope == "zip" and not private and not sensitive(text) and not sandbox:
        msg += " Only you will see it, because it is about your own work."
    elif visibility == "owner" and not sandbox:
        msg += " Only you will see it, because it holds an amount, a grade or a health detail."
    elif replaces is None:
        holds, now_arg = live()
        others = [r for r in c.execute(f"SELECT id, text FROM facts WHERE {holds} AND {world} AND subject=? AND id!=? "
                                       "ORDER BY id DESC LIMIT 3", now_arg + world_args + (subject, fid))] \
            if subject else []





        corrects = bool(_CORRECTION.search(text))
        related = search(text, bot_id, limit=6 if corrects else 3, exclude={fid} | {r["id"] for r in others})
        offered = []
        if others:
            offered.append(f"Also saved about {subject}: " + "; ".join(f"[{r['id']}] {r['text']}" for r in others))
        if related:
            offered.append("Saved facts that mention the same things: "
                           + "; ".join(f"[{r['id']}] {r['text']}" for r in related))
        if offered and corrects:
            msg += (" " + ". ".join(offered) + ". This reads as a correction, and each of these stays current until "
                    "you replace it. Save this fact once more with replaces set to all the numbers it makes wrong.")
        elif offered:
            msg += (" " + ". ".join(offered)
                    + ". If the new fact makes any of these wrong, save it once more with replaces set to all of them.")
    return fid, msg


_CORRECTION = re.compile(r"\b(?:mistakes?|mistaken(?:ly)?|in error|wrong|actually|no longer|any ?more|turns? out|"
                         r"correct(?:ion|ed|s)?|instead)\b|, not\b", re.I)


def _evidence(e: dict | None) -> str | None:
    ""
    if not isinstance(e, dict):
        return None
    out = {k: e[k] for k in ("session_id", "message_id", "at", "quote") if e.get(k) not in (None, "")}
    if "quote" in out:
        out["quote"] = " ".join(str(out["quote"]).split())[:200]
    return json.dumps(out) if out else None


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








def authority(f: dict) -> int:
    ""
    if f.get("edited_at") or f.get("source") in ("app", "setup"):
        return 2
    return 1 if f.get("source") in ("zip", "cart") else 0


def last_said(f: dict) -> int:
    ""
    return max(int(f.get("observed_at") or 0), int(f.get("edited_at") or 0))


def rank(f: dict) -> tuple:
    return authority(f), f.get("kind") == "preference", last_said(f)









REVISION = ("text", "subject", "kind", "visibility", "data", "replaced_by", "status", "valid_until", "edited_at")


def revision(f: dict) -> list:
    ""
    return [f.get(k) for k in REVISION]


def revisions(rows) -> dict[int, list]:
    return {f["id"]: revision(f) for f in rows}


def unchanged(seen: dict[int, list]) -> bool:
    ""
    return all(revision(get(fid) or {}) == was for fid, was in seen.items())


def _visible(bot_id: str | None) -> tuple[str, tuple]:
    ""

    if config.is_sandbox(bot_id):
        return "bot_id=?", (bot_id,)
    if bot_id is None:
        return _world(None)
    return "(visibility='team' OR bot_id=?)", (bot_id,)


def _world(bot_id: str | None) -> tuple[str, tuple]:
    ""

    if config.is_sandbox(bot_id):
        return "bot_id=?", (bot_id,)
    boxes = config.sandboxes()
    return (f"bot_id NOT IN ({','.join('?' * len(boxes))})", boxes) if boxes else ("1=1", ())


def current(bot_id: str | None = None, kinds: tuple | None = None, after: int = 0, limit: int = 500,
            past: bool = False) -> list[dict]:
    ""
    where, args = _visible(bot_id)
    holds, now_arg = ("replaced_by IS NULL", ()) if past else live()
    sql = f"SELECT * FROM facts WHERE {holds} AND id>? AND {where}"
    args = now_arg + (after,) + args
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += tuple(kinds)
    rows = _db().execute(sql + " ORDER BY id DESC LIMIT ?", args + (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def shared(viewer: str | None = None) -> tuple[str, tuple]:
    ""
    return ("bot_id=?", (viewer,)) if config.is_sandbox(viewer) else ("visibility='team'", ())


def replaced_since(after: int, kinds: tuple | None = None, viewer: str | None = None) -> list[dict]:
    ""
    scope, scope_args = shared(viewer)
    sql = f"SELECT * FROM facts WHERE replaced_by IS NOT NULL AND replaced_by > ? AND id <= ? AND {scope}"
    args: tuple = (after, after) + scope_args
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += tuple(kinds)
    return [dict(r) for r in _db().execute(sql, args)]


def ended_between(after: int, since: float, kinds: tuple | None = None, viewer: str | None = None) -> list[dict]:
    ""

    scope, scope_args = shared(viewer)
    sql = (f"SELECT * FROM facts WHERE replaced_by IS NULL AND id <= ? AND {scope} AND valid_until > ? "
           "AND valid_until <= ?")
    args: tuple = (after,) + scope_args + (int(since), int(now()))
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += tuple(kinds)
    return [dict(r) for r in _db().execute(sql, args)]


def top_id() -> int:
    return _db().execute("SELECT COALESCE(MAX(id), 0) FROM facts").fetchone()[0]




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
    ""
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
    ""

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
           exclude: set[int] | None = None, min_score: float = 0.0, past: bool = False) -> list[dict]:
    ""


    ws = _with_names(query, terms(query, _person_words()))
    if not ws:
        return []
    c = _db()
    holds, now_arg = ("f.replaced_by IS NULL", ()) if past else live("f")
    total = c.execute(f"SELECT COUNT(*) FROM facts f WHERE {holds}", now_arg).fetchone()[0] or 1
    where, args = _visible(bot_id)
    hits: dict[int, float] = {}
    matched: dict[int, list[tuple[str, int]]] = {}
    for w in ws:
        try:
            ids = [r[0] for r in c.execute(f"SELECT f.id FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                                          f"WHERE facts_fts MATCH ? AND {holds} AND {where}",
                                          (_fts_term(w),) + now_arg + args)]
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

        subject = d.get("subject") or ""
        if subject and any(w[:5] in subject for w in ws):
            score += 2.0
        d["score"] = round(score, 3)
        if score >= min_score:
            rows.append(d)
    rows.sort(key=lambda d: (-d["score"], -d["id"]))
    return rows[:limit]



_PLUMBING = re.compile(r"^work kanban task \S+$", re.I)




_CALL_TURN = "[From a voice call]"
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


def _peeled(content: str) -> str:
    ""





    from . import history, uploads
    return uploads.peel(history.strip((content or "").strip()))[0]


def _own_words(content: str) -> str | None:
    ""


    t = _peeled(content)
    for m in _CALL_MARKERS if t.startswith(_CALL_TURN) else ():
        if m in t:
            return t.split(m, 1)[1].strip()
    from . import history
    if not t or t.startswith("[") or history.spoken_for(t):
        return None
    return t


_REQUEST = re.compile(r"^(can|could|would|will|please|order|add|put|start|make|send|email|draft|find|check|show|"
                      r"get|book|remind|what|what's|whats|who|who's|where|when|how|why|is|are|do|does)\b", re.I)


def _states_something(text: str) -> bool:
    ""

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
    ""

    ws = terms(query, _person_words())
    if not ws:
        return []
    from . import choices
    q = " OR ".join(_fts_term(w) for w in ws)
    lanes = _lane_sessions()
    answers = choices.card_answers()
    found = []
    for zip_id, path in _state_dbs():


        if (zip_id != bot_id) if config.is_sandbox(bot_id) else (config.is_sandbox(zip_id) or
                                                               config.is_private(zip_id) and zip_id != bot_id):
            continue
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
            words = words and choices.own_words(words, answers)
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




def _name(bot_id: str) -> str:
    b = config.BOTS_BY_ID.get(bot_id)
    return b.name if b else ("the hub" if bot_id == "hub" else bot_id)


def _first_upper(s: str) -> str:
    ""

    return s[:1].upper() + s[1:]


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, home.TZ).strftime("%b %-d")


def _to(bot_id: str, viewer: str | None) -> str:
    ""

    return "you" if viewer and bot_id == viewer else _name(bot_id)


def source_words(f: dict, viewer: str | None = None) -> str:
    ""
    from . import who
    you = who.first()
    day = _day(f.get("observed_at") or f.get("created_at") or now())
    zip_name = _to(f.get("bot_id") or "", viewer)
    s = f.get("source")
    if s == "mail":
        return f"from an order email, {day}"
    if s == "cart":
        return f"from a cart {zip_name} prepared, {day}"
    if s == "call":
        return f"{you} said it on a call with {zip_name}, {day}"
    if s == "setup":
        return f"{you} said it when setting up Zero, {day}"
    if s == "app":
        return f"{you} added it in the app, {day}"
    if s == "import":
        whose = "your" if zip_name == "you" else f"{zip_name}'s"
        return f"from {whose} notes, {day}"
    if s == "chat":
        return f"{you} said it to {zip_name}, {day}"
    if f.get("confidence") == "inferred":
        return f"{zip_name} worked it out, {day}"
    return f"{zip_name} noted it, {day}"


def when_over(f: dict) -> str:
    ""
    s = state(f)
    if s == "cancelled":
        return "cancelled"
    if s == "past":
        return f"past, it ended {factdates.ended_words(f['valid_until'])}" if f.get("valid_until") else "past"
    return ""


def _line(f: dict, viewer: str | None = None) -> str:
    subj = f"{f['subject'][:1].upper()}{f['subject'][1:]}: " if f.get("subject") else ""
    over = when_over(f)
    return f"- {subj}{f['text']} ({source_words(f, viewer)}{'; ' + over if over else ''})"


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


def shown_text(session_ids: set[str]) -> str:
    ""

    ids = sorted(s for s in session_ids if s)
    if not ids:
        return ""
    holds, now_arg = live("f")
    rows = _db().execute(f"SELECT DISTINCT f.id, f.text FROM facts f JOIN fact_shown s ON s.fact_id = f.id "
                         f"WHERE s.session_id IN ({','.join('?' * len(ids))}) AND {holds} ORDER BY f.id DESC",
                         (*ids, *now_arg)).fetchall()
    return "\n".join(r["text"] for r in rows)


def forget_shown_before(session_id: str, since: float) -> None:
    ""

    if not session_id or not since:
        return
    with store._lock:
        c = _db()
        c.execute("DELETE FROM fact_shown WHERE session_id=? AND at < ?", (session_id, since))
        c.commit()


def relevant(message: str, bot_id: str, session_id: str = "", given: set[int] | None = None) -> str:
    ""


    raw = _peeled(message)
    asked = re.match(r"^\[From [^\]]{1,40}\]\s*", raw)
    message = raw[asked.end():].split("\n\n(")[0] if asked else (_own_words(raw) or "")
    if len(message) < 6 or _PLUMBING.match(message):
        return ""
    from . import who
    skip = (_shown(session_id) if session_id else set()) | (given or set())
    found = search(message, bot_id, limit=RELEVANT_FACTS, exclude=skip)
    lines = [_line(f, bot_id) for f in found]
    used = [f["id"] for f in found]
    snippets = message_search(message, bot_id, exclude_session=session_id or None, limit=RELEVANT_SNIPPETS,
                              min_terms=2 if found else 1)
    fact_text = " ".join(f["text"].lower() for f in found)
    for s in snippets:
        if s["text"][:40].lower() in fact_text:
            continue
        lines.append(f'- {who.first()} to {_to(s["bot_id"], bot_id)}, {_day(s["ts"])}: "{s["text"]}"')
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
    ""
    from . import who
    found = search(query, bot_id, limit=12, past=True)
    found.sort(key=lambda f: state(f) != "current")
    past = message_search(query, bot_id, limit=6, width=300)
    if not found and not past:
        return (f"Nothing saved matches \"{query}\". Try other words, or ask {who.first()}; save the answer with "
                "save_fact.")
    parts = []
    if found:
        parts.append("Saved facts (newest wins when two disagree):\n" + "\n".join(
            f"[{f['id']}] {_line(f, bot_id)[2:]}" for f in found))
        _mark_shown(session_id, [f["id"] for f in found])
    if past:
        parts.append(f"{who.possessive()} own words in earlier chats:\n" + "\n".join(
            f'- To {_to(p["bot_id"], bot_id)}, {_day(p["ts"])}: "{p["text"]}"' for p in past))
    return "\n\n".join(parts)




BRIEFING_ORDER = ("status", "plan", "decision", "person", "preference", "place", "supply", "purchase", "other")


def briefing_text(bot_id: str, limit_chars: int = 6000) -> str:
    ""

    rows = current(bot_id)
    if not rows:
        return ""
    by_kind: dict[str, list[str]] = {}
    for f in reversed(rows):
        by_kind.setdefault(f["kind"], []).append(_line(f, bot_id))
    out, size = [], 0
    for k in BRIEFING_ORDER:
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




KIND_HEADINGS = {"preference": "Preferences", "place": "Places", "purchase": "Purchases", "supply": "Supplies",
                 "person": "People", "plan": "Plans", "decision": "Decisions", "status": "Finished",
                 "other": "Other Things"}


def for_app() -> list[dict]:
    ""
    out = []
    for f in reversed(current(None, limit=2000, past=True)):
        out.append({"id": f["id"], "text": f["text"], "subject": f["subject"], "kind": f["kind"],
                    "heading": KIND_HEADINGS.get(f["kind"], "Other Things"), "source": _first_upper(source_words(f)),
                    "private": f["visibility"] == "owner", "observed_at": f["observed_at"],
                    "edited": bool(f.get("edited_at")), "status": state(f), "valid_until": f.get("valid_until")})
    return out


def edit(fid: int, text: str) -> dict:
    ""
    text = " ".join((text or "").split())
    if len(text) < 3 or len(text) > 400:
        raise FactError("Keep it to one sentence.")
    why = secret(text)
    if why:
        raise FactError(why)
    with store._lock:
        c = _db()
        row = c.execute("SELECT kind, observed_at, valid_from, valid_until, status FROM facts WHERE id=? "
                        "AND replaced_by IS NULL", (fid,)).fetchone()
        if not row:
            raise FactError("That fact is gone.")
        start, until = row["valid_from"], row["valid_until"]
        if row["status"] != "current":
            until = None
        if row["kind"] == "plan":
            start, until = factdates.span(text, row["observed_at"])
            start = row["observed_at"] if start is None else int(start)
        c.execute("UPDATE facts SET text=?, edited_at=?, confidence='said', status='current', valid_from=?, "
                  "valid_until=? WHERE id=?",
                  (text, int(now()), start, None if until is None else int(until), fid))
        c.commit()
    return get(fid) or {}


def delete(fid: int) -> bool:
    ""
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




CALL_FACTS = 4
CALL_SNIPPETS = 2
CALL_MAX_CHARS = 900
CALL_BACKGROUND = 3


def call_context(text: str, bot_id: str, pushed: set | None = None) -> tuple[str, list]:
    ""



    from . import who
    pushed = pushed if pushed is not None else set()
    found = [f for f in search(text, bot_id, limit=CALL_FACTS + 4) if f["id"] not in pushed][:CALL_FACTS]
    if any(f["kind"] in ("plan", "decision") for f in found):


        have = {f["id"] for f in found} | pushed
        found += [f for f in reversed(current(bot_id, kinds=("plan", "decision"), limit=8))
                  if f["id"] not in have][:CALL_BACKGROUND]
    lines, keys = [], []
    for f in found:
        lines.append(_line(f, bot_id))
        keys.append(f["id"])
    fact_text = " ".join(f["text"].lower() for f in found)
    for s in message_search(text, bot_id, limit=CALL_SNIPPETS + 2, width=200, min_terms=2 if found else 1):
        key = f"m:{s['bot_id']}:{s['message_id']}"
        if key in pushed or s["text"][:40].lower() in fact_text or len(keys) >= CALL_FACTS + CALL_BACKGROUND + CALL_SNIPPETS:
            continue
        lines.append(f'- {who.first()} told {_to(s["bot_id"], bot_id)} on {_day(s["ts"])}: "{s["text"]}"')
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

Below are notes about {person} that a quick search found. In their sources, "you" is {name}. Pick only what \
truly bears on what {person} is saying, and add what a thoughtful friend who knows {person} would keep in mind (a \
goal it may clash with, a date, a person). Write at most three short plain lines of facts, then one line starting "Ask:" with the single sharpest question to help \
{person} think it through. If none of the notes bear on it, answer exactly: NONE

Notes:
{notes}"""


def refine_material(text: str, bot_id: str) -> str:
    ""
    found = search(text, bot_id, limit=12)
    rows = [f"- {f['text']} ({source_words(f, bot_id)})" for f in found]
    for s in message_search(text, bot_id, limit=5, width=240):
        rows.append(f'- Said to {_to(s["bot_id"], bot_id)} on {_day(s["ts"])}: "{s["text"]}"')

    for f in current(bot_id, kinds=("plan", "decision", "person"), limit=12):
        line = f"- {f['text']} ({source_words(f, bot_id)})"
        if line not in rows:
            rows.append(line)
    from . import privatewords
    return "\n".join(privatewords.keep(rows)[:30])


async def call_refine(text: str, bot_id: str, ask=None) -> str:
    ""

    from . import briefing, who
    notes = refine_material(text, bot_id)
    if not notes:
        return ""
    name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "Zero"
    prompt = REFINE_PROMPT.format(name=name, person=who.first(), said=text[:1200], notes=notes)
    with shortreply.reading((bot_id,)):
        out = (await (ask or (lambda p: briefing.ask_haiku(p, timeout=20, effort="low")))(prompt)).strip()
    if not out or out.upper().startswith("NONE"):
        return ""
    return (f"[More context for this call, after a closer look. Use it naturally; never read it out.]\n" + out)[:900]













_CLOSING = re.compile(r"(?:\bnot\b|\bnever\b|n't\b)[^.;]{0,60}?\b(?:brought|bring|raised?|mention(?:ed)?|ask(?:ed)?)\b"
                      r"[^.;]{0,40}?\b(?:again|anymore|any more)\b|\bstop (?:bringing|raising|mentioning|asking)\b",
                      re.I)
_COURSE = re.compile(r"\b[A-Z]{2,4}[ -]?\d{3}\b")

_COURSE_NAME = re.compile(r"\b([A-Z][\w&'-]*(?:\s+(?:and|of|the|&|[A-Z][\w&'-]*))*)\s+\(([A-Z]{2,4}[ -]?\d{3})\)|"
                          r"\b([A-Z]{2,4}[ -]?\d{3})\s+\(([A-Z][a-z][\w&' -]{1,60})\)")


def _flat(text: str) -> str:
    ""
    t = re.sub(r"'s\b", "", (text or "").lower().replace("’", "'"))
    t = re.sub(r"\b([a-z]{2,4})[ -]?(\d{3})\b", r"\1 \2", t)
    return " ".join(re.findall(r"[a-z0-9&]+", t))


def _course_names(texts: list[str]) -> dict[str, set[str]]:
    ""
    out: dict[str, set[str]] = {}
    for t in texts:
        for m in _COURSE_NAME.finditer(t or ""):
            name, code = (m[1], m[2]) if m[1] else (m[4], m[3])
            name, code = _flat(name), _flat(code)
            if re.search(r"[a-z]{3}", name):
                out.setdefault(name, set()).add(code)
                out.setdefault(code, set()).add(name)
    return out


def _named(text: str, aliases: dict[str, set[str]]) -> set[str]:
    ""


    low = (text or "").lower().replace("’", "'")
    return {t for t in {_flat(m) for m in _COURSE.findall(text)} | set(aliases)
            if re.search(r"\b" + re.escape(t).replace(r"\ ", "[ -]?") + r"\b(?!'s)", low)}



_DONE = re.compile(r"\b(?:done with|finished|completed?|quit|dropped|withdrew)\b", re.I)

_NOT_DONE = re.compile(r"(?:\b(?:not|never|yet to)|n't)\s+(?:\w+\s+){0,2}?(?:done with|finished|completed?|quit|"
                       r"dropped|withdrawn)\b|\b(?:done with|finished|completed?)\b[^.;]{0,40}\byet\b", re.I)


def _done(text: str) -> bool:
    return bool(_DONE.search(text or "")) and not _NOT_DONE.search(text or "")




_PART = re.compile(r"\b(?:unit|module|chapter|lesson|week|quiz(?:zes)?|exams?|tests?|midterms?|finals? exam|assignments?|"
                   r"homework|papers?|essays?|memos?|projects?|labs?|readings?|discussions?|posts?|worksheets?|"
                   r"presentations?|reflections?|journals?|drafts?)\b|\b\d+\.\d+\b", re.I)


def finished(text: str) -> bool:
    ""
    t = text or ""
    return bool(_CLOSING.search(t) or _done(t) and _COURSE.search(t) and not _PART.search(t))


def finished_quote(words: str) -> str:
    ""

    m = _DONE.search(words or "") or _CLOSING.search(words or "")
    at = max(0, m.start() - 80) if m else 0
    return (words or "")[at:at + 200]


def _aliases_for(c: sqlite3.Connection, where: str, args: tuple) -> dict[str, set[str]]:
    ""
    return _course_names([r[0] for r in c.execute(
        f"SELECT text FROM facts WHERE replaced_by IS NULL AND {where} AND text LIKE '%(%'", args)])


def _closed_by(r, aliases: dict[str, set[str]], me: set[str]) -> set[str]:
    ""
    try:
        quote = str((json.loads(r["evidence"] or "{}") or {}).get("quote") or "")
        data = json.loads(r["data"] or "null")
    except (ValueError, TypeError, AttributeError):
        return set()
    listed = data.get("closes") if isinstance(data, dict) else None


    done = r["kind"] == "status" and (_done(r["text"]) or _done(quote) or bool(listed and isinstance(listed, list)))
    if not quote or not (done or _CLOSING.search(r["text"]) or _CLOSING.search(quote)):
        return set()
    terms = {_flat(x) for x in listed if isinstance(x, str)} if isinstance(listed, list) \
        else _named(r["text"], aliases)
    said, out = f" {_flat(quote)} ", set()
    for term in terms:
        if len(term) < 3 or not set(term.split()) - STOP - me:
            continue
        forms = {term} | aliases.get(term, set())
        if any(f" {f} " in said for f in forms):
            out |= forms
    return out


def closed_terms(bot_id: str | None) -> set[str]:
    ""
    where, args = _visible(bot_id)
    holds, now_arg = live()
    c = _db()
    rows = c.execute(f"SELECT kind, text, data, evidence FROM facts WHERE {holds} AND {where} "
                     "AND kind IN ('decision', 'status')", now_arg + args).fetchall()
    if not rows:
        return set()
    aliases, me = _aliases_for(c, where, args), _person_words()
    return set().union(*(_closed_by(r, aliases, me) for r in rows))









_EXAMPLE = re.compile(r"(?:\blike|\bsuch as|\bfor example|\be g|\binstead of|\brather than) $")


def _names_course(text: str, courses: set[str]) -> bool:
    ""
    flat = f" {_flat(text)} "
    for t in courses:
        at = flat.find(f" {t} ")
        while at >= 0:
            if not _EXAMPLE.search(flat[:at + 1]):
                return True
            at = flat.find(f" {t} ", at + 1)
    return False


def retire(fid: int) -> list[int]:
    ""

    c = _db()
    with store._lock:
        f = c.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
        if not f or f["kind"] != "status":
            return []
        world, world_args = _world(f["bot_id"])
        aliases = _aliases_for(c, world, world_args)
        terms = _closed_by(f, aliases, _person_words())
        if not terms:
            return []
        courses = {t for t in terms if _COURSE.fullmatch(t.upper()) or any(
            _COURSE.fullmatch(a.upper()) for a in aliases.get(t, ()))}
        holds, now_arg = live()
        t, ended = int(now()), []
        for r in c.execute(f"SELECT * FROM facts WHERE {holds} AND {world} AND id<? AND kind NOT IN "
                           "('preference', 'status') ORDER BY id", now_arg + world_args + (fid,)).fetchall():
            if not (closes(r["subject"], terms) or _names_course(r["text"], courses)):
                continue
            if _same_message(json.loads(f["evidence"] or "{}"), r["evidence"]):
                c.execute("UPDATE facts SET replaced_by=? WHERE id=?", (fid, r["id"]))
            else:
                c.execute("UPDATE facts SET status='past', valid_until=? WHERE id=?", (t, r["id"]))
            ended.append(r["id"])
        c.commit()
    if ended:
        log.info("status %d ended facts %s", fid, ended)
    try:
        from . import loopcheck
        loopcheck.close_topics()
    except Exception:
        log.warning("open loops about status %d's topics were not closed", fid, exc_info=True)
    return ended


def statuses_from_decisions() -> list[str]:
    ""


    c, out = _db(), []
    holds, now_arg = live()
    me = _person_words()
    for r in c.execute(f"SELECT * FROM facts WHERE {holds} AND kind='decision' ORDER BY id", now_arg).fetchall():
        world, world_args = _world(r["bot_id"])
        f = c.execute(f"SELECT * FROM facts WHERE id=? AND {holds}", (r["id"],) + now_arg).fetchone()
        if not f or not _closed_by(f, _aliases_for(c, world, world_args), me):
            continue
        same = next((s["id"] for s in c.execute(
            f"SELECT * FROM facts WHERE {holds} AND {world} AND kind='status' AND id<?",
            now_arg + world_args + (f["id"],)) if _same_message(json.loads(f["evidence"] or "{}"), s["evidence"])
            and _covers(c, world, world_args, dict(s), dict(f))), None)
        with store._lock:
            if same:
                c.execute("UPDATE facts SET replaced_by=? WHERE id=?", (same, f["id"]))
            else:
                c.execute("UPDATE facts SET kind='status' WHERE id=?", (f["id"],))
            c.commit()
        if same:
            out.append(f"fact {f['id']} replaced by status {same} (the same message): {f['text']}")
            continue
        out.append(f"fact {f['id']} is now a status: {f['text']}")
        for gone in retire(f["id"]):
            g = get(gone) or {}
            how = f"replaced by {f['id']}" if g.get("replaced_by") else "ended as past"
            out.append(f"  fact {gone} {how}: {g.get('text', '')}")
    return out


def closes(text: str, terms: set[str]) -> str:
    ""
    flat = f" {_flat(text)} "
    return next((t for t in sorted(terms) if f" {t} " in flat), "")
