"""Notifications that read like a text from a teammate (Colin, Oct 10, 7:04 p.m., from his lock screen: "I feel like
there's a ton of room for improvement on these notifications").

He saw "Zero: A bill is due soon." and "Daily spending update: Dining for this week (Oct 5 to 11) is at $126.08, which
is $66.08 over the $60 cap. That includes a new $10.97 Chick-fil-A charge from last night t..." (cut off). What he
approved, and where it lives:

- Every notification comes from a Zip. The title is the Zip's name (`sender`), never a job's name, and the payload
  carries the Zip's id and name so the app's Notification Service Extension shows it as a message from that Zip, with
  its avatar (iOS Communication Notifications, apns.send).
- One specific sentence that fits (`line`): the key fact and the ask in about 100 characters, never cut off. Markdown
  and lists are flattened, numbers and names kept. A longer text is rewritten by Haiku on the home's own plan into one
  sentence in the Zip's voice and checked against the original (`faithful`): no number, name or ask that the
  original does not have. Without Haiku (budget spent, the plan's limit, a slow answer, a test run) the text is cut
  at a sentence or a clause (`fit`), never mid-word.
- Only buzz when there's a reason (`judge_report`): a scheduled job's report goes to the Zip's chat quietly, and the
  phone buzzes only when something crossed a line for the first time this week, needs the person, or is
  time-sensitive. The job may say so itself with a `[NOTIFY] ...` line (or `[QUIET]`); otherwise Haiku decides, and
  what already buzzed this week is remembered per job (`_buzzed`), so "dining is over" buzzes once a week, not daily.
- No vague lines for money or health: the specific line goes out, and the person's iPhone hides it until Face ID
  when Show Previews is When Unlocked (each category's hidden-preview placeholder says which Zip it is from).
- Grouped by Zip (thread-id), with a relevance score so the important one leads its group (`relevance`).
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime

from . import config, home, store

log = logging.getLogger("taffy.notify")

TARGET = 90    # what Haiku is asked for: an unlocked banner shows about two lines (95 characters), the lock screen more
LIMIT = 120    # what any line may be; Colin's own example ran 117 characters

HAIKU_DAILY = 40          # rewrites and buzz checks a day on the home's own plan
HAIKU_DAILY_FRUGAL = 15   # a home on a shared plan spends someone else's limits (home.json `plan.frugal`)
_COUNT = "notify_haiku_count"
_BUZZED = "notify_buzzed"


# MARK: Who it is from

def sender(bot_id: str | None = None, bots: list[str] | None = None) -> tuple[str, str]:
    """(id, name) of the Zip a notification comes from: the one named, the only one among `bots`, else the lead."""
    ids = [bot_id] if bot_id else list(dict.fromkeys(b for b in (bots or []) if b))
    if len(ids) == 1 and ids[0] in config.BOTS_BY_ID:
        b = config.BOTS_BY_ID[ids[0]]
        return b.id, b.name
    lead = config.BOTS_BY_ID.get("default")
    return "default", (lead.name if lead else "Zero")


def relevance(kind: str | None = None, urgent: bool = False) -> float:
    """How a notification ranks inside its Zip's group on the lock screen (aps relevance-score, 0 to 1)."""
    if urgent:
        return 1.0
    return {"needs": 0.9, "card": 0.8, "done": 0.6, "progress": 0.3}.get(kind or "needs", 0.5)


# MARK: Plain text

_FENCE = re.compile(r"```[^\n]*\n?|```")
_LINK = re.compile(r"!?\[([^\]\n]*)\]\((?:[^)\s]+)\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_QUOTE = re.compile(r"^\s*>\s?", re.M)
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(r"(?<![\w*])([*_])(?=\S)([^*_\n]+?)(?<=\S)\1(?![\w*])")
_CODE = re.compile(r"`([^`\n]*)`")
_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+(?=\S)")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}.*$", re.M)
_MARKERS = re.compile(r"^\s*\[(NOTIFY|QUIET|SILENT)\]\s*", re.I | re.M)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def plain(text: str) -> str:
    """One paragraph of plain words: no markdown, no list markers, no emojis or em dashes, nothing about the hub. A
    list becomes its items in one sentence ("A; B; C")."""
    from . import notices
    t = str(text or "")
    t = _MARKERS.sub("", t)
    t = _FENCE.sub("", t)
    t = _LINK.sub(r"\1", t)
    t = _TABLE_RULE.sub("", t)
    t = _HEADING.sub("", t)
    t = _QUOTE.sub("", t)
    t = _BOLD.sub(r"\2", t)
    t = _ITALIC.sub(r"\2", t)
    t = _CODE.sub(r"\1", t)
    lines, items = [], []
    for raw in t.splitlines():
        line = raw.strip().strip("|").strip()
        if not line:
            continue
        if _ITEM.match(raw):
            items.append(_ITEM.sub("", raw).strip().rstrip(".;,"))
            continue
        if items:
            lines.append("; ".join(items) + ".")
            items = []
        lines.append(line.replace(" | ", ", "))
    if items:
        lines.append("; ".join(items) + ".")
    # A heading or a label line ("Spending:") runs into the next one as a sentence.
    out = ""
    for line in lines:
        if out and not out.endswith((".", "!", "?", ":", ";", ",")):
            out += "."
        out = f"{out} {line}" if out else line
    out = _EMOJI.sub("", out)
    out = notices.clean(out, 100_000)
    return re.sub(r"\s+", " ", out).strip()


# Abbreviations that end with a period without ending the sentence.
_ABBR = re.compile(r"(?:\b(?:a\.m|p\.m|e\.g|i\.e|etc|vs|Mr|Mrs|Ms|Dr|St|Sr|Jr|Inc|Co|No|approx|U\.S|"
                   r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)"
                   r"|(?:^|(?<=\s))[A-Z])\.$")


def sentences(text: str) -> list[str]:
    """The text's sentences, keeping "7 p.m." and "Oct. 5" inside theirs."""
    t = " ".join(str(text or "").split())
    out, start = [], 0
    for m in re.finditer(r"[.!?]+[\"'”)]?(?=\s+[A-Z0-9\"'“($]|$)", t):
        piece = t[start:m.end()]
        if m.group(0) == "." and _ABBR.search(t[max(0, m.start() - 6):m.end()]) and m.end() < len(t):
            continue
        out.append(piece.strip())
        start = m.end()
    if t[start:].strip():
        out.append(t[start:].strip())
    return [s for s in out if s]


def _end(s: str, question: bool = False) -> str:
    s = s.rstrip(" ,;:-").rstrip()
    if s.endswith(("...", ".", "!", "?")):
        return s
    return s + ("?" if question else ".")


def _shorten(s: str, limit: int) -> str:
    """One sentence cut to fit, at a clause, never mid-word: parentheses go first, then trailing clauses."""
    q = s.rstrip().endswith("?")
    t = re.sub(r"\s*\([^()]*\)", "", s).strip()
    if len(t) <= limit:
        return _end(t, q)
    body = t[:limit]
    cuts = [m.start() for m in re.finditer(r",\s|;\s|:\s|\s(?:and|but|which|so|because|while|after|since)\s", body)]
    cuts = [c for c in cuts if c >= 25]
    if cuts:
        return _end(t[:cuts[-1]], q)
    cut = t[:limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "..."


def fit(text: str, limit: int = LIMIT) -> str:
    """The text as one or two sentences inside `limit`, without a model: the first sentence, with the last question
    when there is one (the ask), each cut at a clause if it has to be."""
    t = plain(text)
    if len(t) <= limit:
        return t
    ss = sentences(t)
    if not ss:
        return ""
    first = ss[0]
    ask = next((s for s in reversed(ss) if s.endswith("?")), None)
    if ask == first:
        ask = None
    if ask:
        if len(first) + 1 + len(ask) <= limit:
            return f"{first} {ask}"
        room = limit - len(ask) - 1
        if room >= 40 and len(ask) <= limit // 2:
            return f"{_shorten(first, room)} {ask}"
    if len(first) <= limit:
        if len(ss) > 1 and len(first) + 1 + len(ss[1]) <= limit:
            return f"{first} {ss[1]}"
        return first
    return _shorten(first, limit)


# MARK: Haiku on the home's plan

async def ask_haiku(prompt: str) -> str:
    """One quick answer from Haiku on the home's own plan (shortreply.ask_haiku: thinking off, low effort, the shape
    a shared home's broker allows). Tests replace this; a test run never reaches the real one (`_haiku_ok`)."""
    from . import shortreply
    return await shortreply.ask_haiku(prompt, timeout=12, system=SYSTEM)


_REAL_ASK = ask_haiku
SYSTEM = "You write one phone notification from an assistant. Answer with exactly what is asked for, nothing else."


def _today() -> str:
    return datetime.now(home.TZ).strftime("%Y-%m-%d")


def haiku_left() -> int:
    cap = HAIKU_DAILY_FRUGAL if home.get("plan.frugal") else HAIKU_DAILY
    c = store.get_setting(_COUNT, {}) or {}
    return max(0, cap - (int(c.get("n") or 0) if c.get("day") == _today() else 0))


def _spend() -> None:
    c = store.get_setting(_COUNT, {}) or {}
    n = int(c.get("n") or 0) if c.get("day") == _today() else 0
    store.set_setting(_COUNT, {"day": _today(), "n": n + 1})


def _haiku_ok() -> bool:
    from . import apns
    if ask_haiku is _REAL_ASK and apns._under_tests():
        return False   # never a real model call from the test suite
    try:
        from . import health
        if not health.status.get("claude", True):
            return False
    except Exception:
        pass
    return haiku_left() > 0


# MARK: Checking a rewrite

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CAP = re.compile(r"\b[A-Z][\w'’&-]*")
_STARTERS = {"want", "should", "shall", "do", "does", "can", "could", "would", "will", "let", "if", "your", "you",
             "you're", "you've", "you'll", "i", "i'm", "i'll", "i've", "i'd", "it", "it's", "the", "a", "an", "this",
             "that", "these", "there", "there's", "here", "here's", "my", "no", "yes", "nothing", "one", "two",
             "three", "all", "both", "today", "tomorrow", "tonight", "now", "just", "also", "and", "but", "so",
             "please", "what", "how", "when", "which", "who", "ok", "okay", "heads", "quick", "new", "time",
             "dining", "spending", "we", "we're", "let's", "need", "ready", "done", "still"}


def _numbers(text: str) -> list[float]:
    out = []
    for n in _NUMBER.findall(text or ""):
        try:
            out.append(float(n.replace(",", "")))
        except ValueError:
            pass
    return out


def faithful(source: str, line: str, offer: bool = False) -> str | None:
    """Why `line` cannot stand for `source` on the lock screen, or None when it can. Every number in it must be one
    in the source (a dollar amount may be rounded to whole dollars), every capitalized name must be in the source,
    and it may ask something only if the source does, unless `offer` (a report's buzz may offer the Zip's help)."""
    from . import notices
    if not line or not line.strip():
        return "empty"
    if len(line) > LIMIT:
        return "too long"
    if "\n" in line or _ITEM.match(line) or "**" in line or "#" in line:
        return "a list or markdown"
    if len(sentences(line)) > 2:
        return "more than two sentences"
    src = _numbers(source)
    for n in _numbers(line):
        if not any(abs(n - s) < 1e-9 or (n == int(n) and (abs(round(s) - n) < 1e-9 or abs(int(s) - n) < 1e-9))
                   for s in src):
            return f"the number {n:g} is not in the original"
    lower = source.lower()
    for s in sentences(line):
        for i, m in enumerate(_CAP.finditer(s)):
            word = re.sub(r"['’]s$", "", m.group(0)).rstrip("'’-")
            if word.lower() in lower:
                continue
            if i == 0 and s[:m.start()].strip(" \"'“(") == "" and word.lower().replace("’", "'") in _STARTERS:
                continue
            return f'"{word}" is not in the original'
    if "?" in line and "?" not in source and not offer:
        return "a question the original does not ask"
    if offer and notices.NEVER_SAY.search(line):
        return "an offer Zips never make"
    if re.search(r"\b(the hub|cron|scheduled job|briefing)\b", line, re.I):
        return "plumbing words"
    return None


def _tidy(text: str) -> str:
    from . import notices
    t = str(text or "").strip().strip("\"“”").strip()
    t = re.sub(r"^(notification|line|text)\s*:\s*", "", t, flags=re.I)
    return notices.clean(t, 100_000)


# MARK: The line

LINE_PROMPT = """{name} is {person}'s assistant. Rewrite this message from {name} as the text of one phone \
notification for {first}.

Rules:
- One sentence, or two short ones, {target} characters at most in all, in {name}'s own voice ("I", "you").
- Lead with the key fact. Keep the names, amounts, dates and times that matter, copied exactly; a dollar amount \
may be rounded to whole dollars.
- If the message asks {first} something or needs something from {first}, end with that ask as a short question. \
Never ask or offer anything the message does not.
- Use only facts in the message. Never add, guess or change anything.
- No markdown, lists, quotes, em dashes or emojis. Never mention the hub, a job, a schedule or a report.
- Write only the notification text.
{context}
<message>
{text}
</message>"""


async def line(text: str, bot_id: str | None = None, *, context: str = "") -> str:
    """The notification text for `text`: as it is when it already fits, else Haiku's one sentence when it holds up,
    else `fit`. Never longer than LIMIT."""
    t = plain(text)
    if len(t) <= LIMIT and len(sentences(t)) <= 2:
        return t
    if _haiku_ok():
        from . import notices, who
        _, name = sender(bot_id)
        prompt = LINE_PROMPT.format(name=name, person=notices.person_name(), first=who.first(), target=TARGET,
                                    context=f"\nWhat this is about: {context}\n" if context else "", text=t[:4000])
        _spend()
        try:
            out = _tidy(await asyncio.wait_for(ask_haiku(prompt), 15))
            why = faithful(t, out)
            if why is None:
                return out
            log.info("kept the cut line for %s: Haiku's rewrite failed (%s)", bot_id or "default", why)
        except Exception as e:
            log.warning("Haiku did not write a notification line (%s)", type(e).__name__)
    return fit(t)


# MARK: Scheduled reports: quiet unless it is worth a buzz

URGENT = re.compile(r"\b(urgent\w*|right away|as soon as possible|asap|overdue|past due|bounced|returned payment|"
                    r"declined|overdrawn|insufficient funds|final notice|due today|expires today|cancel+ed)\b", re.I)

BUZZ_PROMPT = """{name} is {person}'s assistant. {name} just posted this scheduled update ("{job}") in its chat \
with {first}. Decide whether it is worth buzzing {first}'s phone, and if so, write the notification.

Buzz only when one of these is true:
- "threshold": something crossed a line that matters (over a budget or cap, a balance or limit passed) and it is not \
already in the list of what buzzed this week below.
- "needs": {first} must do or decide something (say what a purchase was for, answer a question, approve something).
- "time": it is time-sensitive (due today or tomorrow, a payment that bounced or failed, something that expires).
Otherwise it is routine: buzz is false. A report that only says how things stand is routine, even with numbers.

Already buzzed this week for this update (do not buzz for these again unless they got clearly worse):
{buzzed}

Right now it is {now}.

Answer with one JSON object and nothing else:
{{"buzz": true or false, "reason": "threshold" or "needs" or "time" or "routine", "key": "a short stable \
kebab-case name for the one thing that matters most, such as dining-over-cap or bilt-payment-reversed", "line": \
"the notification, in {name}'s voice: one sentence, or two short ones, {target} characters at most, with the key \
fact first and then one short question offering the next step {name} can take in the chat or asking what {first} \
needs to answer. Copy names and amounts exactly (round dollars to whole dollars). Use only facts in the update. No \
em dashes, emojis or markdown. Never mention the job, a schedule or a report."}}

The update:
<update>
{text}
</update>"""


def _week() -> str:
    y, w, _ = datetime.now(home.TZ).isocalendar()
    return f"{y}-W{w:02d}"


def _buzzed(bot_id: str, job: str) -> list[dict]:
    allv = store.get_setting(_BUZZED, {}) or {}
    got = allv.get(f"{bot_id}:{job}") or {}
    return got.get("items", []) if got.get("week") == _week() else []


def _remember_buzz(bot_id: str, job: str, key: str, line_: str) -> None:
    allv = store.get_setting(_BUZZED, {}) or {}
    items = _buzzed(bot_id, job) + [{"key": key, "line": line_[:200], "at": time.time()}]
    allv[f"{bot_id}:{job}"] = {"week": _week(), "items": items[-20:]}
    # Only this week's entries are worth keeping.
    allv = {k: v for k, v in allv.items() if (v or {}).get("week") == _week()}
    store.set_setting(_BUZZED, allv)


def _key(text: str) -> str:
    return "k" + hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()[:10]


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def report_text(text: str) -> str:
    """A report as the chat shows it: the job's own [NOTIFY] or [QUIET] marker line taken out."""
    kept = []
    for raw in str(text or "").splitlines():
        m = _MARKERS.match(raw)
        if m and m.group(1).upper() in ("NOTIFY", "QUIET"):
            continue
        kept.append(raw)
    return "\n".join(kept).strip()


async def judge_report(bot_id: str, job: str, text: str, script: bool = False) -> dict:
    """Whether a scheduled job's report buzzes the phone. Returns {"buzz", "line", "kind", "reason", "key"}.

    1. The job's own marker: a line "[NOTIFY] <sentence>" buzzes with that sentence; "[QUIET]" never buzzes.
    2. Haiku: threshold crossed, needs the person, or time-sensitive; and the thing that matters must not have
       buzzed this week already (per job, by its key).
    3. Without Haiku: a script watch (silent unless it found something) buzzes; an agent's report buzzes only for
       plainly urgent words (`URGENT`)."""
    quiet = {"buzz": False, "line": "", "kind": "progress", "reason": "routine", "key": ""}
    for raw in str(text or "").splitlines():
        m = _MARKERS.match(raw)
        if m and m.group(1).upper() == "QUIET":
            return {**quiet, "reason": "marker"}
        if m and m.group(1).upper() == "NOTIFY":
            said = raw[m.end():].strip()
            body = report_text(text)
            ln = fit(said) if said else await line(body, bot_id)
            key = _key(said or body)
            if any(b["key"] == key for b in _buzzed(bot_id, job)):
                return {**quiet, "reason": "already"}
            _remember_buzz(bot_id, job, key, ln)
            return {"buzz": True, "line": ln, "kind": "needs", "reason": "marker", "key": key}
    body = report_text(text)
    if not body:
        return quiet
    seen = _buzzed(bot_id, job)
    if _haiku_ok():
        from . import notices, who
        _, name = sender(bot_id)
        shown = "\n".join(f"- {b['key']}: {b['line']}" for b in seen) or "(nothing yet)"
        prompt = BUZZ_PROMPT.format(name=name, person=notices.person_name(), first=who.first(), job=job,
                                    buzzed=shown, now=datetime.now(home.TZ).strftime("%A, %B %-d, %-I:%M %p"),
                                    target=TARGET, text=plain(body)[:5000])
        _spend()
        try:
            out = _parse(await asyncio.wait_for(ask_haiku(prompt), 20))
        except Exception as e:
            log.warning("Haiku could not check %s's %s (%s)", bot_id, job, type(e).__name__)
            out = None
        if out is not None:
            if not out.get("buzz") or out.get("reason") not in ("threshold", "needs", "time"):
                return {**quiet, "reason": "routine"}
            key = re.sub(r"[^a-z0-9-]+", "-", str(out.get("key") or "").lower()).strip("-")[:60] or _key(body)
            if any(b["key"] == key for b in seen):
                return {**quiet, "reason": "already"}
            ln = _tidy(out.get("line") or "")
            why = faithful(plain(body), ln, offer=True)
            if why:
                log.info("buzz line for %s's %s failed (%s); cut from the report", bot_id, job, why)
                ln = fit(body)
            _remember_buzz(bot_id, job, key, ln)
            return {"buzz": True, "line": ln, "kind": "needs", "reason": out["reason"], "key": key}
    # No Haiku: a script watch speaks only when it found something; an agent's report only for plain urgency.
    if script or URGENT.search(body):
        key = _key(body)
        if any(b["key"] == key for b in seen):
            return {**quiet, "reason": "already"}
        hit = URGENT.search(body)
        pick = body
        if hit and not script:
            pick = next((s for s in sentences(plain(body)) if URGENT.search(s)), body)
        ln = fit(pick)
        _remember_buzz(bot_id, job, key, ln)
        return {"buzz": True, "line": ln, "kind": "needs" if not script else "progress",
                "reason": "time" if hit else "watch", "key": key}
    return quiet
