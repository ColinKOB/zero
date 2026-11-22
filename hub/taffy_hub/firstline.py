""





















from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field

from . import chatter, config, store, who

log = logging.getLogger("taffy.firstline")

MAX_CHARS = 140




GUIDE = ("[For this typed chat turn, before your first tool call, write one short first-person sentence saying "
         "what you will actually do, based on this request and your full context. Under 20 words. No question, "
         "time estimate, timing promise, own name, markup or number the person did not say. Avoid timing words "
         "such as minutes, seconds, hours, shortly and soon. Do not invent work "
         "or repeat the plan in your final answer. For small talk, a card answer or something you can answer "
         "directly, skip the opening and answer normally. Never print SKIP.]")





CARD_ANSWER = "About your question \""
ANSWER_SEC = 600
_card_answers: dict[str, list[float]] = {}

_PROMISE = re.compile(r"\b(minutes?|seconds?|hours?|shortly|soon|right back|promise|guarantee)\b", re.I)
_FIRST_PERSON = re.compile(r"\bI\b|\b[Mm](?:e|y)\b")
_MARKUP = re.compile(r"[*_#`\[\]<>|]")


@dataclass
class Pending:
    ""
    turn: object
    words: str
    asked_at: float = field(default_factory=time.time)
    state: str = "waiting"
    line: str | None = None
    working: bool = False


_pending: dict[str, Pending] = {}


def _norm(text: str | None) -> str:
    return " ".join(re.findall(r"\w+", str(text or "").lower()))


def note_card_answer(words: str | None, bot_id: str = "default") -> None:
    ""


    now = time.time()
    for key in list(_card_answers):
        _card_answers[key] = [at for at in _card_answers[key] if now - at <= ANSWER_SEC]
        if not _card_answers[key]:
            del _card_answers[key]
    if _norm(words):
        _card_answers.setdefault(f"{bot_id}:{_norm(words)[:300]}", []).append(now)


def take_card_answer(words: str | None, bot_id: str = "") -> bool:
    ""

    if str(words or "").startswith(CARD_ANSWER):
        return True
    key = f"{bot_id}:{_norm(words)[:300]}"
    pending = [at for at in _card_answers.get(key, []) if time.time() - at <= ANSWER_SEC]
    if not pending:
        _card_answers.pop(key, None)
        return False
    pending.pop(0)
    if pending:
        _card_answers[key] = pending
    else:
        _card_answers.pop(key, None)
    return True




def _bare(text: str | None) -> str:
    return " ".join(str(text or "").split()).strip().strip("\"'“”")


def skipped(text: str | None) -> bool:
    ""
    t = _bare(text)
    return bool(re.fullmatch(r"\W*skip\W*", t, re.I)) or t.startswith("SKIP")


def clean(text: str | None, name: str = "", words: str = "") -> str | None:
    ""


    t = _bare(text)
    if not t or skipped(t):
        return None
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    if t[-1].isalnum():
        t += "."
    t = t[:-1] + "." if t.endswith("!") else t
    if len(t) > MAX_CHARS or "?" in t or _PROMISE.search(t) or _MARKUP.search(t):
        return None
    theirs = str(words or "").lower()
    if any(tok.strip(".,;:!\"'()").lower() not in theirs for tok in re.findall(r"\S*\d\S*", t)):
        return None
    if not _FIRST_PERSON.search(t) or (name and re.search(rf"\b{re.escape(name)}\b", t)):
        return None
    for person in (who.full(), who.first()):
        if person and re.search(rf"\b{re.escape(person)}\b", t) and not re.search(rf"\b{re.escape(person)}\b", words or ""):
            return None
    if len(re.findall(r"[.!]", t)) > 2:
        return None
    return t




def _spoke(turn) -> bool:
    ""
    return bool(getattr(turn, "done", False) or getattr(turn, "stopped", False) or any(
        e.get("type") in ("commentary", "reply", "error", "stopped") for e in getattr(turn, "events", [])))


def guide(bot_id: str, session_id: str, message: str) -> str | None:
    ""
    p = _pending.get(bot_id)
    if p is None or not session_id or session_id != store.session_for(bot_id) or _spoke(p.turn) \
            or _norm(p.words)[:200] not in _norm(message):
        return None
    return GUIDE


def _hash(text: str) -> str:
    text, _ = chatter.split_short(text)
    text = chatter.without_hermes_notes(text)
    return hashlib.sha256(_norm(text).encode()).hexdigest()


def _remember(p: Pending, raw: str, shown: str | None) -> None:
    try:
        _save(p.turn.bot_id, shown or "", p.asked_at, p.words, f"{'after' if p.working else 'before'}:{_hash(raw)}")
    except Exception:
        log.warning("could not keep %s's opening decision", p.turn.bot_id, exc_info=True)


async def receive(turn, event: dict) -> dict | None:
    ""
    p = _pending.get(turn.bot_id)
    if event.get("type") == "delta_reset":


        if p is not None and p.turn is turn and p.state == "waiting":
            p.state = "dropped"
        return event
    if event.get("type") == "tool_started":
        if p is not None and p.turn is turn:
            p.working = True
        return None
    if p is None or p.turn is not turn or event.get("type") != "commentary":
        return event
    if getattr(turn, "stopped", False) or getattr(turn, "done", False):
        _remember(p, event.get("text") or "", None)
        return None
    text = event.get("text") or ""
    from . import chatter, shortreply
    if shortreply.has_work(chatter.without_hermes_notes(text)):
        p.state = "dropped"
        return event
    if p.state == "said":
        if _hash(text) == _hash(p.line or ""):
            _remember(p, text, None)
            return None
        return event
    if p.state != "waiting":
        return event
    if p.working:
        p.state = "dropped"
        return event
    if skipped(text):
        _remember(p, text, None)
        p.state = "dropped"
        return None
    bot = config.BOTS_BY_ID.get(turn.bot_id)
    line = clean(text, bot.name if bot else "", p.words)
    _remember(p, text, line)
    if not line:
        return None
    p.state, p.line = "said", line
    return {**event, "text": line}


async def begin(turn, words: str) -> None:
    ""
    bot = config.BOTS_BY_ID.get(turn.bot_id)
    if not _norm(words) or chatter.chatter(words, (bot.name if bot else "", who.first())) or getattr(turn, "card_answer", False) \
            or words.startswith(CARD_ANSWER):
        return
    old = _pending.get(turn.bot_id)
    if old is not None:
        end(old.turn)
    p = Pending(turn=turn, words=words)
    _pending[turn.bot_id] = p


def end(turn) -> None:
    ""
    p = _pending.get(turn.bot_id)
    if p is None or p.turn is not turn:
        return
    _pending.pop(turn.bot_id, None)
    if p.state == "waiting":
        p.state = "dropped"




@store.schema
def _tables(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS first_lines (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, "
                 "asked_at REAL NOT NULL, words TEXT NOT NULL, text TEXT NOT NULL, raw_hash TEXT)")
    if "raw_hash" not in {r[1] for r in conn.execute("PRAGMA table_info(first_lines)")}:
        conn.execute("ALTER TABLE first_lines ADD COLUMN raw_hash TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS first_lines_bot ON first_lines (bot_id, asked_at)")


def _save(bot_id: str, text: str, asked_at: float, words: str, raw_hash: str | None = None) -> None:
    with store._lock:
        conn = store.db()
        conn.execute("INSERT INTO first_lines (bot_id, asked_at, words, text, raw_hash) VALUES (?, ?, ?, ?, ?)",
                     (bot_id, asked_at, _norm(words)[:200], text, raw_hash))
        conn.commit()


ANCHOR_SEC = 60


def weave(bot_id: str, msgs: list[dict]) -> list[dict]:
    ""


    stamps = [m["ts"] for m in msgs if isinstance(m.get("ts"), (int, float))]
    if not stamps:
        return msgs
    try:
        rows = store.db().execute("SELECT id, asked_at, words, text, raw_hash FROM first_lines WHERE bot_id = ? AND asked_at >= ? "
                                "ORDER BY asked_at, id", (bot_id, min(stamps) - ANCHOR_SEC)).fetchall()
    except Exception:
        log.warning("could not read %s's first lines", bot_id, exc_info=True)
        return msgs
    anchors: dict[tuple, int | None] = {}
    used: set[int] = set()
    openings: dict[int, dict[str, list[str]]] = {}
    after: dict[int, dict] = {}
    for r in rows:
        key = (r["asked_at"], r["words"])
        if key not in anchors:
            at = next((i for i, m in enumerate(msgs) if m.get("role") == "user" and i not in used
                       and isinstance(m.get("ts"), (int, float)) and r["asked_at"] <= m["ts"] <= r["asked_at"] + ANCHOR_SEC
                       and r["words"] in _norm(m.get("text"))), None)
            anchors[key] = at
            if at is not None:
                used.add(at)
        at = anchors[key]
        if at is None:
            continue
        if r["raw_hash"]:
            openings.setdefault(at, {}).setdefault(r["raw_hash"], []).append(r["text"])
        else:
            after[at] = {"id": f"first-{r['id']}", "role": "assistant", "text": r["text"], "ts": msgs[at]["ts"] + 0.001}
            if msgs[at].get("turn_id"):
                after[at]["turn_id"] = msgs[at]["turn_id"]
    out = []
    decisions: dict[str, list[str]] = {}
    for i, m in enumerate(msgs):
        if m.get("role") == "user":
            decisions = openings.get(i, {})
        elif m.get("role") == "assistant":
            before = m.get("_firstline", True)
            m = {k: v for k, v in m.items() if k != "_firstline"}
            key = f"{'before' if before else 'after'}:{_hash(m.get('text') or '')}"
            pending = decisions.get(key)
            if pending:
                text = pending.pop(0)
                if not text:
                    continue
                m = {**m, "text": text}
        out.append(m)
        if i in after:
            out.append(after[i])
    return out
