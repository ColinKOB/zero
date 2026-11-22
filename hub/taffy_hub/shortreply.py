""
































import asyncio
import contextlib
import contextvars
import decimal
import functools
import hashlib
import logging
import re
import time

from . import store, zipvoice

log = logging.getLogger("taffy.shortreply")

SETTING = "keep_replies_short"
KEEP_DAYS = 120


LONG_WORDS = 90

SHORT_MAX_PARAGRAPHS = 2



SHORT_MAX_WORDS = 130




EFFORT = "medium"
CALL_TIMEOUT_SEC = 15
TIMEOUT_SEC = 25
MIN_RETRY_SEC = 6
ATTEMPTS = 2


def enabled() -> bool:
    return bool(store.get_setting(SETTING, True))


def set_enabled(on: bool) -> None:
    store.set_setting(SETTING, bool(on))




_URL = re.compile(r"https?://[^\s<>()\[\]\"']+[^\s<>()\[\]\"'.,;:!?]")

_PATH = re.compile(r"(?<![\w/.])(?:~|/home|/Users|/srv|/tmp|/var)/[^\s\"'<>()]*[^\s\"'<>().,;:!?]")





_ATTACHMENT = re.compile(r"^[ \t]*MEDIA:\S*[ \t]*$|!\[[^\]\n]*\]\(data:[^)\s]*\)", re.M)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")



PILL = re.compile(r"\[([^\[\]\n]+)\]\((zero-(?:item|place|file)):((?:[^()\n]|\([^()\n]*\))+)\)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+\S")


def _pills(text: str) -> set[tuple[str, str]]:
    return {(scheme, " ".join(target.split())) for _, scheme, target in PILL.findall(text)}


def words_in(text: str) -> int:
    ""
    return len(_ATTACHMENT.sub(" ", text or "").split())


def is_long(text: str) -> bool:
    ""
    return words_in(text) >= LONG_WORDS


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def _work_blocks(text: str) -> list[str]:
    ""
    blocks = re.findall(r'(?ms)^```.*?^```[^\n]*|^[ \t]*["“].*?["”][ \t]*(?=\n\s*\n|\Z)|(?:^>[^\n]*(?:\n|$))+', text)
    if re.search(r"(?m)^(?:To|Subject):\s*\S", text):
        blocks.append(text)
    return blocks





_CLOSING_QUESTIONS = re.compile(r"(?:^[ \t]*\d{1,2}[.)][ \t]+[^\n]*\?[^\n]*\s*)+\Z", re.M)


def _split_questions(text: str) -> tuple[str, str]:
    ""
    text = (text or "").rstrip()
    m = _CLOSING_QUESTIONS.search(text)
    return (text[:m.start()].rstrip(), m.group(0).strip()) if m else (text, "")


def _drops_work(full: str, short: str) -> bool:
    compact = " ".join(short.split())
    return any(" ".join(block.split()) not in compact for block in (*_work_blocks(full), _split_questions(full)[1])
               if block)


def has_work(text: str) -> bool:
    return bool(_work_blocks(text or ""))








_PROMISE = re.compile(r"(?:^|(?<=[.!?:]\s))(?:on it|will do|starting now)\b|"
                      r"\b(?:i'll|i will|i'm going to|i am going to)\s+(?:also\s+|now\s+|then\s+)?([a-z]+)", re.M)


def _promises(text: str) -> set[str]:
    return {m.group(1) or m.group(0).strip() for m in _PROMISE.finditer(text.lower().replace("’", "'"))}


def _ends_asking(text: str) -> bool:
    return _ATTACHMENT.sub("", text or "").rstrip().rstrip(")\"'”’*_ ").endswith("?")







_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")


_CONDITION = re.compile(r"\b(?:if|unless|until|pending|unconfirmed|estimated?|estimates|assum\w*|would|could|might)\b",
                        re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for line in (text or "").splitlines() for s in _SENTENCE_GAP.split(line) if s.strip()]


def _questions(text: str) -> int:
    return sum(1 for s in _sentences(_QUOTED.sub("", _ATTACHMENT.sub("", text or "")))
               if s.rstrip(")\"'”’*_ ").endswith("?"))


def _hedged_figures(text: str) -> set[str]:
    ""

    hedged, plain = set(), set()
    for s in _sentences(text):
        for m in _NUMBER.finditer(s):
            (hedged if _CONDITION.search(s[:m.start()]) else plain).add(m.group(0))
    return hedged - plain


def _unhedged(full: str, short: str) -> str | None:
    ""
    hedged = _hedged_figures(full)
    for s in _sentences(short):
        if _CONDITION.search(s):
            continue
        for m in _NUMBER.finditer(s):
            if m.group(0) in hedged:
                return m.group(0)
    return None


def _changes_ask(full: str, short: str) -> str | None:
    ""
    if _ends_asking(full) and "?" not in short:
        return "the reply's closing question turned into a statement; keep it as a question, as the last sentence"
    if (asked := _questions(full)) > _questions(short):
        return (f"the reply asks {asked} question{'s' if asked > 1 else ''} and the short version asks fewer; keep each "
                "question as a question, never as something already decided")
    if figure := _unhedged(full, short):
        return (f"the reply gives {figure} only under a condition (\"if\", \"until\", an estimate) and the short version "
                "states it as settled; keep the condition with the figure")


    said = " ".join(s for s in re.split(r"(?<=[.!?])\s+", full.lower().replace("’", "'")) if not s.rstrip().endswith("?"))
    made = sorted(p for p in _promises(short) - _promises(full) if " " in p or not re.search(rf"\b{re.escape(p)}\b", said))
    if made:
        return f'a promise the full reply does not make ("{made[0]}"); keep each offer and question as the reply says it'
    return None




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


_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12"}
_COUNT_WORDS = re.compile(r"\b(" + "|".join(w for w in _NUMBER_WORDS if w != "one") + r")\b", re.I)
_ANY_NUMBER_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)

_DATE_NAMES = [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may", "may"),
               ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sept", "sep"),
               ("october", "oct"), ("november", "nov"), ("december", "dec"), ("monday", "mon"),
               ("tuesday", "tues", "tue"), ("wednesday", "wed"), ("thursday", "thurs", "thur", "thu"),
               ("friday", "fri"), ("saturday", "sat"), ("sunday", "sun")]
_DATE_FORMS = {form: names for names in _DATE_NAMES for form in names}


def _numbers(text: str) -> set[str]:
    ""
    return {n.replace(",", "") for n in _NUMBER.findall(_URL.sub(" ", _EMAIL.sub(" ", text)))}


def _rounds(short_n: str, original_n: str) -> bool:
    ""


    if "." not in original_n:
        return False
    places = len(short_n.split(".", 1)[1]) if "." in short_n else 0
    if places >= len(original_n.split(".", 1)[1]):
        return False
    try:
        return (decimal.Decimal(original_n).quantize(decimal.Decimal(1).scaleb(-places), decimal.ROUND_HALF_UP)
                == decimal.Decimal(short_n))
    except (decimal.InvalidOperation, ValueError):
        return False


def _number_from(short_n: str, originals: set[str]) -> bool:
    ""
    return short_n in originals or any(_rounds(short_n, o) for o in originals)


def _known_names() -> list[str]:
    ""
    from . import config, onboarding
    try:
        return [b.name for b in config.BOTS] + onboarding.person_name().split()
    except Exception:
        return []


def _name_in(word: str, source: str, source_lower: str) -> bool:
    if word in source:
        return True
    forms = _DATE_FORMS.get(word.lower().rstrip("."))
    return bool(forms) and any(re.search(r"\b" + f + r"\b", source_lower) for f in forms)



_SENTENCE_GAP = re.compile(r"(?<=[.!?])(?<!\bMr\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bMrs\.)(?<!\bProf\.)\s+")


def faithful(original: str, short: str, words: str = "", name: str = "") -> str | None:
    ""








    short = _ATTACHMENT.sub("", short or "").strip()
    whole = _ATTACHMENT.sub("", original or "").strip()
    body, asks = _split_questions(whole)
    rest, kept = _split_questions(short) if asks else (short, "")
    if asks and " ".join(kept.split()) != " ".join(asks.split()):
        return "the numbered questions at the end changed; end with them exactly as written"
    if why := _changes_ask(original or "", short):
        return why
    short = rest
    if _drops_work(body, short):
        return "quoted work missing from the short reply; show the reply whole"
    if not short:
        return "empty"
    n, full_n = words_in(short), words_in(body)
    if n >= full_n:
        return f"no fewer words than the full reply ({n} of {full_n})"
    paras = len(_paragraphs(short))
    if paras > SHORT_MAX_PARAGRAPHS:
        return f"{paras} paragraphs"
    if n > SHORT_MAX_WORDS:
        return f"{n} words"
    from . import who
    if who.names_itself(short, name, whole):
        return f'"{name}" where {name} should say "I"'
    source = f"{whole}\n{words or ''}"
    known = _numbers(source) | {_NUMBER_WORDS[w.lower()] for w in _ANY_NUMBER_WORD.findall(source)}
    said = sorted(_numbers(short) | {_NUMBER_WORDS[w.lower()] for w in _COUNT_WORDS.findall(_URL.sub(" ", short))})
    extra = [x for x in said if not _number_from(x, known)]
    if extra:
        return f'a number not in the full reply ("{extra[0]}")'
    links = set(_URL.findall(short)) | set(_PATH.findall(short))
    if links - (set(_URL.findall(whole)) | set(_PATH.findall(whole))):
        return "a link not in the full reply"
    pills, kept = _pills(body), _pills(short)
    if kept - _pills(whole):
        return "a link not in the full reply"
    if pills - kept:
        return "an item link from the full reply missing (keep each [words](zero-...) link exactly as written)"
    if {e.lower() for e in _EMAIL.findall(short)} - {e.lower() for e in _EMAIL.findall(source)}:
        return "an address not in the full reply"



    source += "\n" + " ".join(_known_names())
    lower = source.lower()
    for sentence in _SENTENCE_GAP.split(_PATH.sub(". ", _URL.sub(". ", short))):
        for m in _CAP_WORD.finditer(sentence):
            if m.start() == 0 or sentence[:m.start()].strip(" \"'“(") == "":
                continue
            word = re.sub(r"['’]s$", "", m.group(0)).rstrip("'’-")
            if not _name_in(word, source, lower) and word.lower().replace("’", "'") not in _STARTERS:
                return f'"{word}", which is not in the full reply'
    return None








PROMPT = zipvoice.TEXT + "\n\n" + """You shorten a chat reply that {name}, {person}'s assistant, is about to send. {person} reads it on a \
phone, with paragraphs together in one message, and wants a teammate's answer at a glance, not a report.

Use one to three short sentences, usually 20 to 40 words, in at most two short paragraphs. Sixty words is a \
ceiling, never a target. Answer directly, then stop. Add supporting facts only when needed to understand or act \
on the answer. A grade, time or price can be the answer itself. {name} says "I" and "my", never "{name}".

Rules:
- When {person} asked more than one thing and the reply answers them, the short version answers each one, briefly. A question {person} asked and doesn't see answered reads as ignored.
- If {person} asked something the reply cannot answer from what {name} has, say what is missing briefly, only when it affects the answer.
- Never put a question to {person} on its own without the reason for asking. For example: "Your notes don't mention \
Alex, so is Alex coming?" If the reply asks {person} something, keep it as a question and make it the last sentence, never a promise \
such as "On it". When it asks several things, keep each as a question {person} can answer, never a summary such as "I need a few answers". \
Never offer or ask something the reply does not.
- Use only facts in the reply. Never add, guess or change anything. Keep who does what exactly as the reply says \
(what {name} did stays "I did", and a teammate or {person} keeps their name), and keep each thing's state: done \
stays done, sent stays sent, something for later stays later, and something only proposed stays proposed ("I'd put it on your calendar" is not "on your calendar"). These are truth constraints, not required disclaimers. State a relevant limit once, in a few words.
- A first name already in the reply is fine. Omit redundant full dates. Copy every date, time, phone number, link and file path you keep exactly as written. An amount may be \
rounded to whole dollars or a whole number ($126 for $125.87), never changed. Keep any decision, deadline or warning \
that changes what {person} should do, and any change {name} made that {person} did not ask for.
- Keep a rule of thumb the reply uses, by name, such as 50/30/20. Never bring in one the reply does not use.
- Leave out background, how {name} found things, the working, and caveats that change nothing. Leave out any share, \
percentage or total the reply itself calls overstated, misleading or incomplete, and then its caveat too. Instead of \
listing many items, name the most important one or say how many there are, unless the items are needed to act now. \
For an imminent event, keep its time, join link and passcode or location, and the direct answers or items needed, when present in the reply.
- Stay on {person}'s message: leave out news about anything else unless it is urgent.
- Where {name} offers to say more once {person} answers, write "say more confidently", never "say plainly".
- Use the chat voice in the guide above. No lists, headings, markdown, em dashes, semicolons or emojis, and no "not just X, but \
Y". Keep every item link written as [words](zero-item:...), [words](zero-place:...) or [words](zero-file:...) exactly as \
written: the app shows it as a button, so it is not markdown to remove. Do not say that this is shorter, a summary, or that more detail exists.
- Write only the new reply.

{person}'s message:
<message>
{words}
</message>

{name}'s full reply:
<reply>
{reply}
</reply>

Before you answer, check your short version against these, and fix it if one fails:
- It answers directly in one to three short sentences, unless essential facts or questions need more.
- It adds facts only when needed and uses the shortest clear names, such as a first name or "Form H".
- It has at most two short paragraphs. Sixty words is a ceiling, never a target.
- Every number, date and name in it is in the reply. Draft, waiting and done states are unchanged.
"""


def again(prompt: str, short: str, why: str) -> str:
    ""
    shorter = any(w in why for w in ("words", "paragraphs"))
    return (f"{prompt}\n\nYour last rewrite was:\n<rewrite>\n{short}\n</rewrite>\nIt could not be used because it had "
            f"{why}. Write it again" + (" with fewer words, in at most two short paragraphs." if shorter else "."))


SYSTEM = "You rewrite a chat reply to be short. Answer with the rewritten reply only."
MAX_TOKENS = 400




THINKING_TOKENS = 16000


async def ask_quick(prompt: str, timeout: float = CALL_TIMEOUT_SEC, system: str = SYSTEM,
                    max_tokens: int = MAX_TOKENS, effort: str = "low", bot_id: str | None = None) -> str:
    ""

    from . import modelsetup
    return await ask_helper(prompt, model=modelsetup.quick_model(bot_id), timeout=timeout, system=system,
                            max_tokens=max_tokens, effort=effort)




class HelperError(RuntimeError):
    ""



    def __init__(self, plan: str, model: str, kind: str, detail: str, before: list[str] | None = None):
        self.plan, self.model, self.kind, self.detail = plan, model, kind, detail
        why = {"refused": "the plan refused the run", "limited": "the plan reached its limit"}.get(kind, "")
        detail = " ".join(str(detail or "").split())[:240]
        self.line = f"{plan} ({model}): " + ", ".join(x for x in (why, detail) if x)
        super().__init__("no answer from " + "; then from ".join([*(before or []), self.line]))
















CLAUDE_HOLD_SEC = 30 * 60
_claude_held = {"until": 0.0, "kind": ""}
_last_route = [""]


def claude_held() -> bool:
    ""
    return time.time() < _claude_held["until"]


def hold_claude(kind: str) -> None:
    ""
    _claude_held.update(until=time.time() + CLAUDE_HOLD_SEC, kind=kind)


def _counterpart(m, plan: str):
    ""

    from . import modelsetup
    if m.plan == plan:
        return m
    return next(x for x in modelsetup.M.CATALOG.values() if x.plan == plan and x.tier == m.tier)


def route(model=None, fallback: bool = True) -> list:
    ""



    from . import home, modelsetup
    M = modelsetup.M
    s = modelsetup.current()
    use = s.use if s is not None else tuple(M.PLANS)
    if home.plan_kind() == "shared" and (model is None or model.plan == "claude"):


        return [model or M.HELPER["claude"]]
    m = model or M.HELPER[use[0]]
    if m.plan not in use:
        m = _counterpart(m, use[0])
    if not fallback or m.plan != "claude":
        return [m]
    return [m] + [_counterpart(m, p) for p in use if p != m.plan]


async def _ask_one(m, prompt: str, timeout: float, system: str, max_tokens: int, effort: str, origin: str | None):
    ""
    from . import modelsetup
    plan = modelsetup.M.PLANS[m.plan]
    try:
        if plan.sign_in == "codex":
            from . import voice
            try:
                return await voice.server.ask_once(prompt, system, m.id, effort, timeout)
            except (RuntimeError, OSError) as e:
                raise HelperError(plan.name, m.id, "failed", f"{type(e).__name__}: {e}") from e
        return await ask_haiku(prompt, timeout=timeout, system=system, max_tokens=max_tokens, effort=effort, model=m,
                               origin=origin)
    except (asyncio.TimeoutError, TimeoutError):
        raise HelperError(plan.name, m.id, "failed", f"no answer in {timeout:.0f} s") from None












_READING: contextvars.ContextVar[frozenset | None] = contextvars.ContextVar("zero_reading", default=None)
_TITLES: contextvars.ContextVar[frozenset] = contextvars.ContextVar("zero_reading_titles", default=frozenset())


class PrivateZip(HelperError):
    ""

    def __init__(self, why: str):
        super().__init__("none", "-", "private", why)


@contextlib.contextmanager
def reading(zips, titles=()):
    ""



    if isinstance(titles, str):
        titles = (titles,)
    named = _TITLES.set(_TITLES.get() | frozenset(str(z or "") for z in (titles or ())))
    try:
        if zips is None:
            yield
            return
        if isinstance(zips, str):
            zips = (zips,)
        outer = _READING.get()
        token = _READING.set((outer or frozenset()) | frozenset(str(z or "") for z in (zips or ())))
        try:
            yield
        finally:
            _READING.reset(token)
    finally:
        _TITLES.reset(named)


@contextlib.contextmanager
def reading_task(task, *, titles_only: bool = False, also=()):
    ""




    from . import board, privatewords
    zips = board.task_zips(task)
    privatewords.note_task(task)
    also = (also,) if isinstance(also, str) else tuple(also or ())
    with reading(also if titles_only else also + zips, titles=zips if titles_only else ()):
        yield


@contextlib.contextmanager
def reading_message(msg, *, also=()):
    ""


    from . import privatewords, team
    also = (also,) if isinstance(also, str) else tuple(also or ())
    item = dict(msg or {})
    with reading_text(f"{privatewords._text_of(item.get('body'))}\n{item.get('text') or ''}",
                      also + team.message_zips(msg)):
        yield


@contextlib.contextmanager
def reading_text(text, zips, *, titles=()):
    ""



    from . import config, privatewords
    zips = (zips,) if isinstance(zips, str) else tuple(zips or ())
    with reading(zips + (tuple(sorted(config.PRIVATE_ZIPS)) if privatewords.holds(text) else ()), titles=titles):
        yield


def task_sealed(task) -> bool:
    ""

    from . import board, config
    return config.sealed(*board.task_zips(task))


def declared() -> tuple[frozenset | None, frozenset]:
    ""
    return _READING.get(), _TITLES.get()


def own_company(zip_id: str) -> str | None:
    ""

    from . import modelsetup
    try:
        return modelsetup.M.company(modelsetup.current(), zip_id, modelsetup._config_model(zip_id))
    except Exception:
        log.warning("could not read %s's own model", zip_id, exc_info=True)
        return None


def _company_of(m) -> str | None:
    from . import modelsetup
    return modelsetup.M.company_of(m)


def _private_in(prompt: str) -> tuple[set, bool]:
    ""



    from . import config, privatewords
    named = {z for z in (_READING.get() or frozenset()) | _TITLES.get() if config.is_private(z)}
    if named:
        return named, False
    return (set(config.PRIVATE_ZIPS), True) if privatewords.carries(prompt) else (set(), False)


def allowed_order(order: list, prompt: str) -> list:
    ""



    private, by_content = _private_in(prompt)
    if not private:
        return order
    companies = {own_company(z) for z in private}
    company = companies.pop() if len(companies) == 1 else None
    kept = [m for m in order if company is not None and _company_of(m) == company]
    if kept:
        return kept
    who_called = _caller_chain()
    if by_content:
        _report_content(who_called)
    raise PrivateZip(f"{who_called}: the prompt holds words of {', '.join(sorted(private))}, which go only to "
                     f"{company or 'its own company'}, and this call would use "
                     f"{', '.join(str(_company_of(m)) for m in order) or 'none'}")


_withheld: dict = {}
WITHHELD_SEC = 5.0


def withheld(*zips) -> bool:
    ""

    from . import config
    private = frozenset(str(z) for z in zips if config.is_private(z))
    if not private:
        return False
    key = (private, own_company, route)
    found = _withheld.get(key)
    if found is not None and time.monotonic() - found[0] < WITHHELD_SEC:
        return found[1]
    try:
        allowed = _company_of(route()[0])
        out = any(own_company(z) != allowed for z in private)
    except Exception:
        out = True
    _withheld.clear()
    _withheld[key] = (time.monotonic(), out)
    return out


def guard(prompt: str = "") -> None:
    ""
    declared = _READING.get()
    if declared is None:


        who_called = _caller_chain()
        _report_undeclared(who_called)
        raise PrivateZip(f"{who_called} did not say whose words its prompt carries (shortreply.reading)")


def _caller_chain(depth: int = 4) -> str:
    ""
    import inspect
    chain = []
    for frame in inspect.stack()[2:]:
        mod = frame.frame.f_globals.get("__name__", "")
        if not mod.startswith("taffy_hub.") or mod == __name__:
            continue
        chain.append(f"{mod.rsplit('.', 1)[-1]}.{frame.function}")
        if len(chain) >= depth:
            break
    return " <- ".join(chain) or "an unknown caller"


def _report_content(who_called: str) -> None:
    ""
    try:
        from . import problems
        problems.report("default", {
            "kind": "security_money", "concerns_zip": "default", "source": "code", "where": who_called[:200],
            "what_happened": f"A hub model call was refused because its prompt held words a private Zip stored: "
                             f"{who_called[:300]}. A declaration on this route named the wrong Zips.",
            "evidence": "privatewords.carries matched the prompt against the private Zip's stored words.",
            "expected": "Every route that carries a task's or a message's words declares through board.task_zips or "
                        "team.message_zips."})
    except Exception:
        log.exception("a model call refused for a private Zip's words (%s) could not be reported", who_called)


def _report_undeclared(who_called: str) -> None:
    ""
    try:
        from . import problems
        problems.report("default", {
            "kind": "break", "concerns_zip": "default", "source": "code", "where": who_called[:200],
            "what_happened": f"A hub model call was refused because it did not say whose words its prompt carries: "
                             f"{who_called[:300]}. The feature it serves gets no answer for every Zip until this is fixed.",
            "evidence": "shortreply.guard found no shortreply.reading declaration around the call.",
            "expected": "Every hub model call runs inside shortreply.reading(<the Zips whose words are in the prompt>)."})
    except Exception:
        log.exception("a model call without a declaration (%s) could not be reported", who_called)


async def ask_helper(prompt: str, model=None, timeout: float = CALL_TIMEOUT_SEC, system: str = SYSTEM,
                     max_tokens: int = MAX_TOKENS, effort: str = "low", origin: str | None = None,
                     fallback: bool = True) -> str:
    ""





    guard(prompt)
    from . import modelsetup, usage
    origin = origin or usage.caller()
    began = time.monotonic()
    order = allowed_order(route(model, fallback), prompt)
    skipped = len(order) > 1 and claude_held()
    if skipped:
        order = order[1:]
    tried: list[str] = []
    for i, m in enumerate(order):
        left = timeout if i == 0 else max(1.0, timeout - (time.monotonic() - began))
        try:
            answer = await _ask_one(m, prompt, left, system, max_tokens, effort, origin)
        except HelperError as e:
            if m.plan == "claude" and e.kind in ("refused", "limited"):
                hold_claude(e.kind)
            if i + 1 < len(order) and m.plan == "claude" and e.kind in ("refused", "limited"):
                tried.append(e.line)
                continue
            raise HelperError(e.plan, e.model, e.kind, e.detail, tried) from e
        if m.plan == "claude":
            _claude_held.update(until=0.0, kind="")
            why = "Claude" if _last_route[0] else ""
        else:
            s = modelsetup.current()
            why = ("the model choice leaves Claude out" if s is not None and "claude" not in s.use
                   else ("Claude reached its limit" if _claude_held["kind"] == "limited" else "Claude refused the run")
                   if tried or skipped else "")
        if why and why != _last_route[0]:

            log.info("helper calls answer on %s", "Claude again" if m.plan == "claude" else
                     f"{modelsetup.M.PLANS[m.plan].name} ({m.id} for this one): {why}")
            _last_route[0] = why
        return answer
    raise HelperError("no plan", "-", "failed", "nothing to ask")


async def ask_haiku(prompt: str, timeout: float = CALL_TIMEOUT_SEC, system: str = SYSTEM,
                    max_tokens: int = MAX_TOKENS, effort: str = "low", model=None, origin: str | None = None) -> str:
    ""






    import json
    import shutil
    import tempfile
    from pathlib import Path
    from . import ondevice, usage
    if effort not in ("low", "medium", "high"):
        raise ValueError(f"effort {effort!r}: low, medium or high")
    origin, started = origin or usage.caller(), time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="zero-short-"))
    proc = None
    result = None
    completed = False
    try:
        (tmp / "system.md").write_text(system)
        (tmp / "tools.json").write_text("[]")
        from . import modelsetup
        model = model or modelsetup.M.CATALOG["haiku"]
        if effort == "low":

            body = {"thinking": model.thinking_off or {"type": "adaptive"}, "context_management": {"edits": []},
                    "output_config": {"effort": "low"},
                    "max_tokens": max_tokens if model.thinking_off else max_tokens + THINKING_TOKENS}
        else:
            body = {"thinking": {"type": "adaptive"}, "context_management": {"edits": []},
                    "output_config": {"effort": effort}, "max_tokens": max_tokens + THINKING_TOKENS}
        (tmp / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps(body)}}))
        argv = ondevice.argv(ondevice.claude_exe(), tmp, model.id)
        argv[argv.index("--effort") + 1] = effort
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=ondevice._env(max_tokens=body["max_tokens"]),
            cwd=str(tmp), limit=2 ** 24)
        frame = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}}
        out, err = await asyncio.wait_for(proc.communicate((json.dumps(frame) + "\n").encode()), timeout)
        text, failure, result = "", "", None
        for line in out.decode(errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                said = "".join(b.get("text", "") for b in (ev.get("message") or {}).get("content") or []
                               if b.get("type") == "text")
                if ev.get("error"):
                    failure += f"{said} ({ev['error']}) "
                else:
                    text += said
            elif ev.get("type") == "result":
                result = ev
        if result is None or result.get("is_error") or not text.strip():



            from . import claudeaccounts
            said = " ".join(x for x in (failure, str((result or {}).get("result") or ""), err.decode(errors="replace")[-400:])
                            if x.strip())
            kind = claudeaccounts.refusal(said) or "failed"
            detail = said or ("an empty answer" if result is not None else "no result")
            raise HelperError("Claude", model.id, kind, detail)
        completed = True
        return text.strip()
    finally:
        usage.log_cli(origin, result, getattr(model, "id", ""), started,
                      outcome="completed" if completed else "failed")
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)




_CITATION = re.compile(r"\(\[[^\]\n]+\]\([^)\s]+\)\)")
_LEADING_CITATIONS = re.compile(r"^(?:\s*" + _CITATION.pattern + r")+\s*")
_TRAILING_CITATIONS = re.compile(r"(?:\s*" + _CITATION.pattern + r")+\s*$")


def strip_citations(text: str) -> str:
    ""
    paras = [_LEADING_CITATIONS.sub("", p) for p in re.split(r"\n\s*\n", (text or "").strip())]
    return _TRAILING_CITATIONS.sub("", "\n\n".join(p for p in paras if p.strip())).strip()


def _tidy(text: str) -> str:
    text = text.strip().strip("\"“”").strip()
    text = re.sub(r"^</?reply>|</?reply>$", "", text).strip()
    text = strip_citations(text)
    return re.sub(r"\s*—\s*", ", ", text)


async def shorten(bot_id: str, words: str, reply: str, ask=None, timeout: float = TIMEOUT_SEC,
                  clock=time.monotonic) -> str | None:
    ""



    if not is_long(reply) or _work_blocks(reply):
        return None
    from . import config, onboarding
    bot = config.BOTS_BY_ID.get(bot_id)
    media = _attachments(reply)
    body = _ATTACHMENT.sub("", reply).strip()
    prose, asks = _split_questions(body)
    prompt = PROMPT.format(name=bot.name if bot else "Zero", person=onboarding.person_name(),
                           words=(words or "(no message: this picks up earlier work)").strip()[:2000],
                           reply=prose[:12000])
    started = clock()
    why, short = None, ""
    for attempt in range(ATTEMPTS):
        left = timeout - (clock() - started)
        if attempt and left < MIN_RETRY_SEC:
            break
        wait = min(CALL_TIMEOUT_SEC, left)
        asked = again(prompt, short, why) if why else prompt
        try:
            ask_now = ask or functools.partial(ask_quick, bot_id=bot_id)
            with reading_text(f"{words or ''}\n{prose}", (bot_id,)):
                answer = await asyncio.wait_for(ask_now(asked, timeout=wait, effort=EFFORT), wait)
        except Exception as e:
            log.warning("kept %s's long reply: Haiku did not answer (%s) after %.1f s", bot_id, type(e).__name__,
                        clock() - started)
            return None
        short = _tidy(_ATTACHMENT.sub("", answer))
        why = faithful(body, "\n\n".join(p for p in (short, asks) if p), words, bot.name if bot else "Zero")
        if not why:
            break
    took = clock() - started
    if why or not short:
        log.info("kept %s's long reply: Haiku's short version had %s (%.1f s)", bot_id, why or "no time", took)
        return None
    log.info("shortened %s's reply from %d to %d words with Haiku at %s effort in %.1f s", bot_id, words_in(body),
             words_in(short), EFFORT, took)
    return "\n\n".join(p for p in (short, asks, *media) if p)




_last_words: dict[str, str] = {}


def _attachments(text: str) -> list[str]:
    return [m.group(0).strip() for m in _ATTACHMENT.finditer(text or "")]


def _key(bot_id: str, text: str) -> str:
    return hashlib.sha256(f"{bot_id}\n{' '.join(_ATTACHMENT.sub(' ', text or '').split())}".encode()).hexdigest()


def _legacy_key(bot_id: str, text: str) -> str:
    ""
    return hashlib.sha256(f"{bot_id}\n{' '.join((text or '').split())}".encode()).hexdigest()


@store.schema
def _tables(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS short_replies (key TEXT PRIMARY KEY, bot_id TEXT, short TEXT NOT NULL, "
                 "created_at INTEGER)")

    if "words" not in {r[1] for r in conn.execute("PRAGMA table_info(short_replies)")}:
        try:
            conn.execute("ALTER TABLE short_replies ADD COLUMN words TEXT")
        except Exception:
            pass


def remember(bot_id: str, full: str, short: str, words: str | None = None) -> None:
    ""
    with store._lock:
        conn = store.db()
        conn.execute("INSERT OR REPLACE INTO short_replies (key, bot_id, short, created_at, words) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (_key(bot_id, full), bot_id, _ATTACHMENT.sub("", short).strip(), int(time.time()), words))
        conn.execute("DELETE FROM short_replies WHERE created_at < ?", (int(time.time()) - KEEP_DAYS * 86400,))
        conn.commit()


def _stored(bot_id: str, full: str) -> str | None:
    ""
    conn = store.db()
    for key in dict.fromkeys((_key(bot_id, full), _legacy_key(bot_id, full))):
        row = conn.execute("SELECT short FROM short_replies WHERE key = ?", (key,)).fetchone()
        if row:
            return _ATTACHMENT.sub("", row["short"] or "").strip()
    return None


def lookup(bot_id: str, full: str) -> str | None:
    ""
    short = _stored(bot_id, full)
    return short if short and not _drops_work(full, short) else None


def offer(bot_id: str, full: str, short: str | None, words: str | None = None) -> bool:
    ""



    if not enabled() or short is None:
        return False
    if _stored(bot_id, full) is not None:
        return True
    body = _ATTACHMENT.sub("", full).strip()
    if not is_long(body):
        return False
    if not short.strip():
        remember(bot_id, full, "", words)
        log.info("kept %s's reply whole: its own choice", bot_id)
        return True
    from . import config
    bot = config.BOTS_BY_ID.get(bot_id)
    why = faithful(body, short, words or "", bot.name if bot else "")
    if why:
        log.info("dropped %s's own short version: it had %s", bot_id, why)
        return False
    remember(bot_id, full, short, words)
    log.info("kept %s's own short version (%d of %d words)", bot_id, words_in(short), words_in(body))
    return True


async def for_reply(bot_id: str, words: str | None, reply: str) -> str | None:
    ""


    if words:
        _last_words[bot_id] = words
    try:
        if not enabled():
            return None
        from . import replypage
        asked = _last_words.get(bot_id, "") if words is None else words
        to_page = replypage.wanted(reply, asked)
        kept = _stored(bot_id, reply)
        if kept == "" and not to_page:
            return None
        if kept and _drops_work(reply, kept):
            return None
        if kept and (not to_page or replypage.linked(bot_id, reply, kept)):
            return "\n\n".join([kept, *_attachments(reply)])

        making = asyncio.create_task(replypage.make(bot_id, reply, asked)) if to_page else None
        short = kept or await shorten(bot_id, asked, reply) or (replypage.lead(reply, asked) if to_page else None)
        card = await making if making else None
        if not short:
            replypage.discard(card)
            return None
        if card:
            short = replypage.show(bot_id, short, card)
        remember(bot_id, reply, short, asked)
        return "\n\n".join([_ATTACHMENT.sub("", short).strip(), *_attachments(reply)])
    except Exception:
        log.warning("could not shorten %s's reply; it goes out as written", bot_id, exc_info=True)
        return None


def apply(bot_id: str, msgs: list[dict]) -> list[dict]:
    ""

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
