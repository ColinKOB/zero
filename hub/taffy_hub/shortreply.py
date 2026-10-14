"""Short replies in the person's chat (Oct 10).

Colin, on a four-paragraph status reply: "Long answers like this make this feel less like a teammate and more like an
llm." Users of Grok Bot and ChatGPT dots asked for the same: the key point first, very concise finals, details only
when asked (research rows #7, #13, #29, #75, #76). The SOUL length rules alone did not hold (live convo QA S17: status
and summary replies still ran 12 to 15 sentences), so the hub enforces it here.

When a Zip's finished reply to the person is long (`is_long`) and the person did not ask for detail, steps, a list,
a draft or the full text (`asked_for_detail`), and the reply is not itself an artifact such as a draft or code
(`is_artifact`), Haiku on the home's own plan (`ask_haiku`: thinking off and low effort, in the shape the plan
broker allows for the on-device voice, so shared homes work too) writes two or three sentences in the Zip's voice.
Code then checks the short version against the original (`faithful`): every number, link, file path, address and
name in it must be in the original, the original's links and paths must survive, a question to the person must stay
a question and none may be added, and nothing the reply says someone else does may become the Zip's own doing. A
rewrite that fails is asked for once more with the reason. One that still fails loses its failing paragraph or last
sentence (`repair`) when what is left passes, and otherwise the reply's own opening shows, word for word (`lead`; Oct
11, when four rewrites in a row invented a question and the person got all 13 sentences). Haiku slow or erroring
keeps the original.

Only the display changes. The Zip's Hermes history keeps the full original, so its context is the same. The hub
remembers which reply was shortened (table short_replies, keyed by the reply's text), the app shows the short
version with Show Full Reply, and push notifications use the short version. Not used for team messages between Zips
or for calls, which have their own two-sentence rule.

Per home: Settings, Keep Replies Short (store setting `keep_replies_short`, on unless the person turns it off).
"""

import asyncio
import hashlib
import logging
import re
import time

from . import store

log = logging.getLogger("taffy.shortreply")

SETTING = "keep_replies_short"
TIMEOUT_SEC = 10
ATTEMPTS = 2
KEEP_DAYS = 120

LONG_UNITS = 4
LONG_UNITS_CHARS = 350
LONG_CHARS = 450
SHORT_MAX_SENTENCES = 4
SHORT_MAX_CHARS = 480
SHORT_MAX_WORDS = 75


def enabled() -> bool:
    return bool(store.get_setting(SETTING, True))


def set_enabled(on: bool) -> None:
    store.set_setting(SETTING, bool(on))


# MARK: Measuring

_URL = re.compile(r"https?://[^\s<>()\[\]\"']+[^\s<>()\[\]\"'.,;:!?]")
_PATH = re.compile(r"(?<![\w/.])(?:~|/home|/Users|/srv|/tmp|/var)/[^\s\"'<>()]*[^\s\"'<>().,;:!?]")
# Hermes's attachment line (voice._MEDIA_LINE): never shown to Haiku, always kept, word for word, under the reply.
_MEDIA = re.compile(r"^[ \t]*MEDIA:\S*[ \t]*$", re.M)
# The same attachment as the hub receives it in a reply stream: Hermes's API turns an image's MEDIA line into an
# inline data URL (api_server._resolve_media_to_data_urls), while the saved session keeps the MEDIA line. Either way it
# is not prose: it is never measured, never sent to Haiku and always kept, and the remembered short version is keyed
# on the reply without it, so the streamed reply and the saved one find the same short version.
_ATTACHMENT = re.compile(r"^[ \t]*MEDIA:\S*[ \t]*$|!\[[^\]\n]*\]\(data:[^)\s]*\)", re.M)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+\S")
_FENCE = re.compile(r"```.*?(?:```|$)", re.S)
_BOUNDARY = re.compile(r"[.!?]+[\"'”)]?(?=\s+[A-Z0-9\"'“(]|\s*$)", re.M)


def _prose(text: str) -> str:
    text = _FENCE.sub(" ", _ATTACHMENT.sub("", text or ""))
    text = _PATH.sub("file", _URL.sub("link", text))
    text = re.sub(r"[*_#`>|]+", "", text)
    return text


def measure(text: str) -> tuple[int, int]:
    """(sentences, counting each list item as one; characters of prose, with a link counted as a word)."""
    units = 0
    for line in _prose(text).splitlines():
        line = line.strip()
        if not line:
            continue
        if _LIST_ITEM.match(line):
            units += 1
            continue
        units += max(1, len(_BOUNDARY.findall(line)))
    chars = len(" ".join(_prose(text).split()))
    return units, chars


def is_long(text: str) -> bool:
    units, chars = measure(text)
    return (units > LONG_UNITS and chars > LONG_UNITS_CHARS) or (chars > LONG_CHARS and units >= 3)


# MARK: When not to shorten

_DETAIL = re.compile(r"""\b(
    details? | detailed | in\ depth | deep\ dive | elaborate | explain | walk\ me\ through | step[\s-]by[\s-]step |
    (the\ |all\ the\ |what\ are\ the\ )steps | instructions | how\ (do|can|should|would)\ (i|we|you)\b | how\ to |
    recipe | (make|give|send|write)\ (me\ )?(a\ |the\ )?(full\ )?list |
    list\ (out|all|everything|every|each|them|those|the|my)\b |
    as\ a\ list | bullet | itemi[sz]e | break\ (it|that|this|them)?\s?down | breakdown | rundown | go\ through |
    spell\ (it|that)\ out | tell\ me\ (more|everything) | everything\ you | more\ about | long\ version |
    full | whole | entire | complete\ (list|text|version) | word\ for\ word | verbatim | exact\ (words|wording|text) |
    exactly | quote | paste | read\ (me|it|out|back) | show\ me | let\ me\ see | what\ did\ [^?.!]{1,50}\ say |
    what\ does\ [^?.!]{1,40}\ say | draft | write | compose | rewrite | reword | proofread | translate | template |
    code | script | command | essay | letter | bio | outline | agenda | itinerary | plan\ (it|this|out)
)\b""", re.I | re.X)


def asked_for_detail(words: str) -> bool:
    return bool(_DETAIL.search(words or ""))


_HEADER = re.compile(r"^\s*(subject|to|cc|bcc|from)\s*:\s*\S", re.I | re.M)
_SALUTATION = re.compile(r"^\s*(hi|hello|hey|dear|good (morning|afternoon|evening))\b[^\n.!?]{0,40},\s*$", re.I | re.M)
_SIGNOFF = re.compile(r"^\s*(best|thanks|thank you|cheers|sincerely|regards|best regards|kind regards|warmly|"
                      r"love|talk soon)\s*,?\s*$", re.I | re.M)
_LONG_QUOTE = re.compile(r"(?:^|(?<=[\s:(]))(?:“(?=\S)[^“”]{120,}”|\"(?=\S)[^\"]{120,}\"(?=[\s.,;:!?)]|$))",
                         re.M)


def is_artifact(text: str) -> bool:
    """A reply that is itself the thing: code, an email or message draft, a quoted text, a table, a list of links."""
    t = text or ""
    return bool("```" in t or _HEADER.search(t) or (_SALUTATION.search(t) and _SIGNOFF.search(t))
                or _LONG_QUOTE.search(t) or re.search(r"^\s*>", t, re.M) or re.search(r"\|\s*-{3,}", t)
                or len(set(_URL.findall(t))) > 2)


def should_shorten(words: str, reply: str) -> bool:
    return is_long(reply) and not asked_for_detail(words) and not is_artifact(reply)


# MARK: Checking the short version

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CAP_WORD = re.compile(r"\b[A-Z][\w'’&-]*")
_STARTERS = {"want", "should", "shall", "do", "does", "can", "could", "would", "will", "tell", "let", "if", "once",
             "then", "when", "here", "that", "this", "these", "those", "it", "its", "the", "a", "an", "you", "your",
             "we", "i", "i'm", "i'll", "i've", "i'd", "also", "and", "but", "so", "yes", "no", "nothing", "both",
             "one", "two", "three", "four", "five", "all", "everything", "next", "for", "to", "after", "before",
             "until", "there", "they", "my", "our", "just", "only", "right", "now", "today", "tomorrow", "good",
             "since", "with", "on", "in", "at", "by", "as", "or", "not", "none", "either", "please", "that's", "it's",
             "there's", "here's", "what", "what's", "how", "why", "where", "who", "which", "you're", "you'll",
             "you've", "we're", "we'll", "let's", "don't", "nothing's"}


_AUX = (r"(?:['\u2019](?:ll|ve|d|m)|\s+(?:will|can|could|would|have|had|am|did|do|won['\u2019]t|can['\u2019]t|"
        r"couldn['\u2019]t|didn['\u2019]t|don['\u2019]t|haven['\u2019]t))?")
_SKIP = r"(?:\s+(?:not|also|still|just|already|then|now|going to|be|been|last|first|never|even|only|\w+ly))*"
_FIRST_PERSON = re.compile(r"\bI" + _AUX + _SKIP + r"\s+([a-z]+)")


def _own_verbs(text: str) -> set[str]:
    return {m.group(1) for m in _FIRST_PERSON.finditer(text or "")}


def _said_by_me(original: str, verb: str) -> bool:
    stem = re.escape(verb[:max(3, min(len(verb), 5))])
    return any(re.search(r"\b" + stem, m.group(0)) for m in re.finditer(r"\bI\b[^.!?\n]*", original))


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(_URL.sub(" ", _EMAIL.sub(" ", text)))}


def _asks(text: str) -> bool:
    """The reply ends by asking the person something (a question in its last paragraph)."""
    paras = [p for p in re.split(r"\n\s*\n", _URL.sub("link", text or "").strip()) if p.strip()]
    return bool(paras) and "?" in paras[-1]


def faithful(original: str, short: str, words: str = "") -> str | None:
    """Why the short version cannot stand in for the original, or None when it can."""
    if not short.strip():
        return "empty"
    units, chars = measure(short)
    if units > SHORT_MAX_SENTENCES:
        return f"{units} sentences"
    if chars > SHORT_MAX_CHARS or chars > 0.75 * measure(original)[1] or len(short.split()) > SHORT_MAX_WORDS:
        return "too long"
    if "```" in short or any(_LIST_ITEM.match(line) for line in short.splitlines()):
        return "a list or code"
    source = f"{original}\n{words}"
    extra = _numbers(short) - _numbers(source)
    if extra:
        return "a number not in the original"
    urls = set(_URL.findall(short)) | set(_PATH.findall(short))
    original_urls = set(_URL.findall(original)) | set(_PATH.findall(original))
    if urls - original_urls:
        return "a link not in the original"
    if original_urls - urls:
        return "a link left out"
    if {e.lower() for e in _EMAIL.findall(short)} - {e.lower() for e in _EMAIL.findall(source)}:
        return "an address not in the original"
    lower_source = source.lower()
    for sentence in re.split(r"(?<=[.!?])\s+", _PATH.sub(". ", _URL.sub(". ", short))):
        for i, m in enumerate(_CAP_WORD.finditer(sentence)):
            word = re.sub(r"['\u2019]s$", "", m.group(0)).rstrip("'\u2019-")
            if m.start() == 0 or sentence[:m.start()].strip(" \"'“(") == "":
                if word.lower().replace("\u2019", "'") in _STARTERS or word.lower() in lower_source:
                    continue
                return f'"{word}" is not in the original'
            if word not in source and word.lower().replace("\u2019", "'") not in _STARTERS:
                return f'"{word}" is not in the original'
    if _asks(original) and "?" not in short:
        return "the question was left out"
    if "?" in _URL.sub(" ", short) and "?" not in _URL.sub(" ", original):
        return "a question the reply does not ask"
    moved = sorted(v for v in _own_verbs(short) if not _said_by_me(original, v))
    if moved:
        return f'"I {moved[0]}", which the reply does not say about you (keep who does what)'
    return None


# MARK: Asking Haiku

PROMPT = """You shorten a chat reply that {name}, {person}'s assistant, is about to send. {person} reads it on a \
phone and wants a teammate's short answer, not a report.

Rewrite it as two or three short, plain, complete sentences (never more than three, and 50 words at most in \
all) in {name}'s own voice (first person, the same tone):
1. Lead with the key point: the answer, the result, or what changed.
2. End with the next step, or the one thing {name} needs from {person}, if the reply has one. If the reply asks \
{person} a question, keep that question. If the reply asks no question, write no question: end with a plain \
statement, never "Do you want me to..." or "Can you...".

Rules:
- Use only facts in the reply. Never add, guess or change anything. Keep who does what exactly as the reply \
says ({name}, a teammate or {person}), and keep each thing's state: done stays done, sent stays sent, and \
something for later stays later. Never offer or ask something the reply does not.
- Copy every number, amount, date, time, name, phone number, link and file path you keep exactly as written. \
Keep every link and file path. \
Keep any decision, deadline or warning that changes what {person} should do.
- Leave out background, how {name} found things, and caveats that change nothing. Instead of listing many items, \
name the most important one or say how many there are.
- Stay on {person}'s message: leave out news about anything else (a teammate's findings, other deadlines, tips) \
unless it is urgent.
- Put the key point in one short paragraph and the next step, if there is one, in a second one, with a blank line \
between, because each paragraph shows as its own message.
- "I" is {name}. Something a teammate (such as School) or {person} did stays theirs: "School checked", never \
"I checked".
- No lists, headings, markdown, em dashes or emojis. Do not say that this is shorter, a summary, or that more \
detail exists.
- Write only the new reply.

{person}'s message:
<message>
{words}
</message>

{name}'s full reply:
<reply>
{reply}
</reply>"""


SYSTEM = "You rewrite a chat reply to be short. Answer with the rewritten reply only."
MAX_TOKENS = 400


async def ask_haiku(prompt: str, timeout: float = TIMEOUT_SEC, system: str = SYSTEM) -> str:
    """One answer from Haiku on the home's Claude plan, with thinking off and low effort: briefing.ask_haiku's
    default effort spent 1,800 to 4,500 thinking tokens and 10 to 20 s on these rewrites, this takes 3 to 5 s. The
    run has the shape the plan broker allows for the on-device voice (ondevice.argv, DIRECTSDK with --effort), so a
    home on a shared plan works the same as one on its own plan, and never an API key (ondevice._env)."""
    import json
    import shutil
    import tempfile
    from pathlib import Path
    from . import ondevice, usage
    origin, started = usage.caller(), time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="zero-short-"))
    proc = None
    try:
        (tmp / "system.md").write_text(system)
        (tmp / "tools.json").write_text("[]")
        body = {"thinking": {"type": "disabled"}, "context_management": {"edits": []},
                "output_config": {"effort": "low"}, "max_tokens": MAX_TOKENS}
        (tmp / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps(body)}}))
        proc = await asyncio.create_subprocess_exec(
            *ondevice.argv(ondevice.claude_exe(), tmp), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=ondevice._env(), cwd=str(tmp), limit=2 ** 24)
        frame = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}}
        out, _ = await asyncio.wait_for(proc.communicate((json.dumps(frame) + "\n").encode()), timeout)
        text, result = "", None
        for line in out.decode(errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant" and not ev.get("error"):
                text += "".join(b.get("text", "") for b in (ev.get("message") or {}).get("content") or []
                                if b.get("type") == "text")
            elif ev.get("type") == "result":
                result = ev
        usage.log_cli(origin, result, ondevice.MODEL, started)
        if result is None or result.get("is_error") or not text.strip():
            raise RuntimeError("no answer from Haiku: " + str((result or {}).get("subtype") or "no result"))
        return text.strip()
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”)]?\s+(?=[A-Z0-9\"'“(])")


def _split(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(paragraph.strip()) if s.strip()]


def _paragraphs(text: str) -> list[list[str]]:
    return [_split(p) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _join(paras: list[list[str]]) -> str:
    return "\n\n".join(" ".join(p) for p in paras if p)


def repair(original: str, short: str, words: str = "") -> str | None:
    """The rewrite without the paragraph the checks refuse (from the end first), or else without the last sentence
    of one paragraph; None when no such cut passes `faithful`. Whole paragraphs go first and only a paragraph's
    last sentence is ever cut, so nothing left points at a sentence that went ("I couldn't read it just now" after
    its question was cut). Every sentence that stays is Haiku's and was checked, so a cut cannot add anything."""
    paras = _paragraphs(short)
    if sum(len(p) for p in paras) < 2:
        return None
    candidates = [[p for n, p in enumerate(paras) if n != i] for i in reversed(range(1, len(paras)))]
    candidates += [[p[:-1] if n == i else p for n, p in enumerate(paras)]
                   for i in reversed(range(len(paras))) if len(paras[i]) > 1]
    for c in candidates:
        text = _join(c)
        if text and not faithful(original, text, words):
            return text
    return None


def lead(original: str, words: str = "") -> str | None:
    """The reply's own opening, word for word: its first paragraph of prose, up to three sentences, and its closing
    question when it asks one. None when that is not a fair stand-in (it opens with a list or a lead-in such as
    "Here's what I found:", or it would drop a link or a file the reply gives)."""
    paras = [p for p in re.split(r"\n\s*\n", original.strip()) if p.strip()]
    if not paras or _LIST_ITEM.match(paras[0]) or paras[0].rstrip().endswith(":"):
        return None
    first = _split(paras[0])[:3]
    while len(first) > 1 and len(" ".join(first)) > SHORT_MAX_CHARS * 0.7:
        first = first[:-1]
    out = [first]
    if _asks(original) and len(paras) > 1:
        asked = [s for s in _split(paras[-1]) if "?" in s][-1:]
        if asked:
            out.append(asked)
    text = _join(out)
    return None if faithful(original, text, words) else text


def _tidy(text: str) -> str:
    text = text.strip().strip("\"“”").strip()
    text = re.sub(r"^</?reply>|</?reply>$", "", text).strip()
    return re.sub(r"\s*—\s*", ", ", text)


async def shorten(bot_id: str, words: str, reply: str, ask=None, timeout: float = TIMEOUT_SEC) -> str | None:
    """The short version of `reply`, or None to keep the original (not long, asked for, an artifact, Haiku slow or
    failed, or a rewrite that does not hold up)."""
    if not should_shorten(words, reply):
        return None
    from . import config, onboarding
    bot = config.BOTS_BY_ID.get(bot_id)
    media = _attachments(reply)
    body = _ATTACHMENT.sub("", reply).strip()
    prompt = PROMPT.format(name=bot.name if bot else "Zero", person=onboarding.person_name(),
                           words=(words or "(no message: this picks up earlier work)").strip()[:2000],
                           reply=body[:12000])
    started = time.monotonic()
    why, short = None, ""
    for attempt in range(ATTEMPTS):
        left = timeout - (time.monotonic() - started)
        if left < 1.5:
            break
        asked = prompt if not why else (f"{prompt}\n\nYour last rewrite was:\n<rewrite>\n{short}\n</rewrite>\n"
                                         f"It could not be used because it had {why}. Write it again"
                                         + (" in exactly two sentences." if "sentences" in why or "long" in why
                                            else "."))
        try:
            short = _tidy(_ATTACHMENT.sub("", await asyncio.wait_for((ask or ask_haiku)(asked, timeout=left),
                                                                     left + 1)))
        except Exception as e:
            log.warning("kept %s's long reply: Haiku did not answer (%s) after %.1f s", bot_id, type(e).__name__,
                        time.monotonic() - started)
            return None
        why = faithful(body, short, words)
        if not why:
            break
    took = time.monotonic() - started
    if why and short:
        fixed = repair(body, short, words)
        if fixed:
            log.info("shortened %s's reply by dropping a sentence the checks refused (%s) in %.1f s", bot_id, why,
                     took)
            return "\n\n".join([fixed, *media])
        opening = lead(body, words)
        if opening:
            log.info("showed the opening of %s's reply: the short version had %s (%.1f s)", bot_id, why, took)
            return "\n\n".join([opening, *media])
    if why or not short:
        log.info("kept %s's long reply: the short version had %s (%.1f s)", bot_id, why or "no time", took)
        return None
    log.info("shortened %s's reply from %d to %d characters in %.1f s", bot_id, len(reply), len(short), took)
    return "\n\n".join([short, *media])


# MARK: Remembering and showing

_last_words: dict[str, str] = {}


def _attachments(text: str) -> list[str]:
    return [m.group(0).strip() for m in _ATTACHMENT.finditer(text or "")]


def _key(bot_id: str, text: str) -> str:
    return hashlib.sha256(f"{bot_id}\n{' '.join(_ATTACHMENT.sub(' ', text or '').split())}".encode()).hexdigest()


def _legacy_key(bot_id: str, text: str) -> str:
    """The key before Oct 10's night review, which kept the attachment line in (rows already in the table)."""
    return hashlib.sha256(f"{bot_id}\n{' '.join((text or '').split())}".encode()).hexdigest()


def _table():
    conn = store.db()
    conn.execute("CREATE TABLE IF NOT EXISTS short_replies (key TEXT PRIMARY KEY, bot_id TEXT, short TEXT NOT NULL, "
                 "created_at INTEGER)")
    return conn


def remember(bot_id: str, full: str, short: str) -> None:
    with store._lock:
        conn = _table()
        conn.execute("INSERT OR REPLACE INTO short_replies (key, bot_id, short, created_at) VALUES (?, ?, ?, ?)",
                     (_key(bot_id, full), bot_id, _ATTACHMENT.sub("", short).strip(), int(time.time())))
        conn.execute("DELETE FROM short_replies WHERE created_at < ?", (int(time.time()) - KEEP_DAYS * 86400,))
        conn.commit()


def lookup(bot_id: str, full: str) -> str | None:
    """The remembered short version, without attachments (apply adds the message's own)."""
    conn = _table()
    for key in dict.fromkeys((_key(bot_id, full), _legacy_key(bot_id, full))):
        row = conn.execute("SELECT short FROM short_replies WHERE key = ?", (key,)).fetchone()
        if row:
            return _ATTACHMENT.sub("", row["short"]).strip() or None
    return None


def offer(bot_id: str, full: str, short: str) -> bool:
    """A Zip's own short version of its long reply (the <short> block it led with, chatter.split_short): kept as the
    reply's short version when it passes the same checks as Haiku's (`faithful`), so the reply shows at once with no
    Haiku call. One that fails is dropped, and Haiku writes one as before. Fable's usage review, Oct 11: the model
    that wrote the reply writes its short version in the same turn, for a few dozen tokens."""
    if not enabled() or not short:
        return False
    if lookup(bot_id, full):
        return True
    body = _ATTACHMENT.sub("", full).strip()
    if not is_long(body) or is_artifact(body):
        return False
    why = faithful(body, short)
    if why:
        log.info("dropped %s's own short version: it had %s", bot_id, why)
        return False
    remember(bot_id, full, short)
    log.info("kept %s's own short version (%d of %d characters)", bot_id, len(short), len(body))
    return True


async def for_reply(bot_id: str, words: str | None, reply: str) -> str | None:
    """A finished reply to the person: its short version (remembered for the chat), or None to show it as it is.
    `words` None means the reply picks up the person's latest request (after a card); "" is a reply to no request
    of theirs (Zero's board relay), which must not replace that request."""
    if words:
        _last_words[bot_id] = words
    try:
        if not enabled():
            return None
        own = lookup(bot_id, reply)
        asked = _last_words.get(bot_id, "") if words is None else words
        if own and not asked_for_detail(asked):
            return "\n\n".join([own, *_attachments(reply)])
        short = await shorten(bot_id, _last_words.get(bot_id, "") if words is None else words, reply)
        if short:
            remember(bot_id, reply, short)
        return short
    except Exception:
        log.warning("could not shorten %s's reply; it goes out as written", bot_id, exc_info=True)
        return None


def apply(bot_id: str, msgs: list[dict]) -> list[dict]:
    """The chat as the app shows it: a shortened reply's `display` is the short version and `full` the original.
    With Keep Replies Short off, every reply shows as written."""
    try:
        if not enabled():
            return msgs
        out = []
        for m in msgs:
            if m.get("role") == "assistant" and m.get("class") in ("bot", "board_reply"):
                text = m.get("text") or ""
                short = lookup(bot_id, text)
                if short:
                    m = {**m, "display": "\n\n".join([short, *_attachments(text)]),
                         "full": m.get("display") or text}
            out.append(m)
        return out
    except Exception:
        log.warning("could not read the short replies; showing them as written", exc_info=True)
        return msgs
