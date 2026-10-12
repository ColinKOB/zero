"""Zero's quiet look (proactivity phase 2): Grok Bot's primary bot also wakes "when things go quiet" and looks across
everything connected for something worth picking up that no fixed rule covers (the dog with no sitter on the
examples slide).

When Colin's chat with Zero has been idle for two hours (notices.quiet_wake_due: 9 a.m. to 9 p.m., at most four a
day), the hub gives Zero one turn in a session apart from his chat (hermes lane "quiet"). There it may read its
memory, the calendar and Trello copy, and his accounts through the connectors tool, but the gate lets it change
nothing and raise no cards. It answers with at most two things worth asking about, as JSON, or nothing.

What it raises is a candidate like any noticer's, under the kind "quiet": the same code rules (specific, dated or with
a consequence, one tap, nothing Zips never do), the same one-question-per-subject rule, the same daily and hourly
budget, quiet hours and busy blocks, and Colin can turn the kind off in Settings. Each option is Colin's own words
to Zero, so anything it leads to that needs his okay still gets its card.
"""

import json
import logging
import re
from datetime import datetime, timedelta

from . import calcache, config, home, notices, who
from .notices import Candidate, Option

log = logging.getLogger("taffy.quietlook")

MAX_RAISED = 2
CLOSING = re.compile(r"\b(already|handled|taken care|done|have it|has it|got it|covered|not needed|no need|"
                     r"it's fine|all set|"
                     r"skip|leave it|ignore)\b", re.I)

NOTE = """[Quiet look from the hub, not a message from {first}. {first} won't see this conversation.]
{possessive} chat with you has been quiet for two hours. It is {now}. Look over what you know: your memory, your \
notes on how {first} wants these questions, the calendar and Trello below, and {accounts} if a \
quick read would settle something. Decide whether there is one thing (two at most) {first} would want raised now that \
is not already an open question below: something with a date or a consequence that nobody is handling, such as a \
trip with no pet sitter, an event with nothing arranged, or a promise about to slip. You only read here; change \
nothing.

How {first} wants these questions, in {possessive} words:
{prefs}

Open questions already asked (don't repeat these):
{open}

Questions {first} turned down in the last two weeks (raise one again only if something about it changed):
{dismissed}

Kinds of questions {first} turned off (don't raise these):
{off}

Calendar, today through the next three days:
{calendar}

Trello cards due soon or in progress:
{cards}

Reply with one JSON object and nothing else. If nothing is worth raising, reply {{"raise": []}}. Otherwise:
{{"raise": [{{"subject": "a short stable key, like dog-sitter-oct-17", "owner": "which Zip would follow through: \
{owners}", "evidence": "one or two plain sentences: what you found and \
where", "question": "one short question", "options": ["two or three short answers in {possessive} words; the first \
one does something"], "deadline": "YYYY-MM-DD HH:MM when it matters, or null", "consequence": "what happens if nobody \
acts, or empty", "about": "the person, thing or event"}}]}}
Start the evidence with where you found it, in a few words ("Your calendar shows...", "Your October 3 email to \
Jake..."). Write it to {first} as a teammate would: "you" and "your", never {possessive} name, and "I" for what \
you checked. Only raise what you checked yourself, with nothing found that shows it is handled. Never raise a \
newsletter, a sale or any mass email. Never turn a purchase, a receipt, a sign-in or an account notice into a money or security worry unless \
the source itself says something is wrong. Keep the look short: a few quick reads at most, no research, no new tasks \
and no messages to teammates. If what you would raise was turned down before and nothing about it changed, leave it.
Never offer to pay, buy, order, sign or move money. Plain complete sentences, no em dashes, no emojis."""


async def run_turn(text: str) -> str | None:
    """Zero's turn in its quiet session. Tests replace this."""
    from . import hermes
    return await hermes.run_turn("default", text, lane="quiet")


def _calendar(now: float) -> str:
    end = now + 3 * 86400
    lines = []
    for e in calcache.events_between(notices.day_start(now), end)[:40]:
        when = "all day" if e.get("all_day") else f"{notices.clock(e['start'])} to {notices.clock(e['end'])}"
        place = f", {e['location']}" if e.get("location") else ""
        lines.append(f"- {notices.date_short(e['start'])} {notices._et(e['start']).strftime('%a')}, {when}: "
                     f"{e['title']}{place}")
    return "\n".join(lines) or "(no copy of the calendar yet)"


def _cards(now: float) -> str:
    lines = []
    for c in calcache.trello_cards()[:30]:
        due = f", due {notices.date_short(c['due'])}" if c.get("due") else ""
        lines.append(f"- {c['name']} ({c.get('list') or 'no list'}{due})")
    return "\n".join(lines) or "(none)"


def prompt() -> str:
    now = notices.now()
    open_rows = notices._open_rows()
    opened = "\n".join(f"- {json.loads(r['card'] or '{}').get('question', '')}" for r in open_rows) or "(none)"
    dismissed = "\n".join(f"- {json.loads(r['card'] or '{}').get('question', '')}" for r in notices._db().execute(
        "SELECT card FROM notices WHERE state='dismissed' AND updated>? ORDER BY updated DESC LIMIT 10",
        (now - 14 * 86400,))) or "(none)"
    off = [notices.NOTICERS[n][0] for n in notices.NOTICERS if notices.mode(n) == "off"]
    ids = [b.id for b in config.BOTS]
    owners = ", ".join(ids[:-1]) + f" or {ids[-1]}" if len(ids) > 1 else ids[0]
    accounts = ("the email and calendar tools" if home.connectors() == "none"
                else f"{who.possessive()} accounts through the connectors tool")
    return NOTE.format(first=who.first(), possessive=who.possessive(), owners=owners, accounts=accounts,
                       now=notices._et(now).strftime("%A, %B %-d, %Y, %-I:%M %p ") + home.tz_word(),
                       prefs=notices.preferences() or "(nothing saved yet)", open=opened, dismissed=dismissed,
                       off="\n".join(f"- {o}" for o in off) or "(none)", calendar=_calendar(now), cards=_cards(now))


def _deadline(v) -> float | None:
    if not isinstance(v, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(v.strip()[:16], fmt).replace(tzinfo=notices.ET)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            d += timedelta(hours=17)
        return d.timestamp() if d.timestamp() > notices.now() else None
    return None


def candidates(reply: str) -> list[Candidate]:
    """Zero's answer as candidates. Anything malformed is dropped; the code rules in notices.check decide the rest."""
    data = notices._parse(reply or "")
    items = (data or {}).get("raise") if isinstance(data, dict) else None
    out = []
    for item in (items if isinstance(items, list) else [])[:MAX_RAISED]:
        if not isinstance(item, dict):
            continue
        question = notices.clean(item.get("question") or "", 200)
        evidence = notices.clean(item.get("evidence") or "", 320)
        texts = [notices.clean(o, 80) for o in (item.get("options") or []) if isinstance(o, str) and o.strip()][:3]
        if not question or not evidence or len(texts) < 2:
            continue
        if notices.off_voice(question) or notices.off_voice(evidence):
            log.info("quiet look's question dropped: it speaks about the person, not to them")
            continue   # Oct 10: the card is Zero speaking to the person; a third-person one is not asked
        options = []
        for i, t in enumerate(texts):
            if i > 0 and CLOSING.search(t):
                options.append(Option(t, "done", follow="Okay. I'll leave it with you."))
            else:
                options.append(Option(t, "act" if i == 0 else "decide", say=f"About your question \"{question}\": {t}"))
        owner = str(item.get("owner") or "default")
        key = "-".join(re.findall(r"[a-z0-9]+", str(item.get("subject") or question).lower()))[:60]
        out.append(Candidate(
            noticer="quiet", subject=f"quiet:{key}", owner=owner if owner in config.BOTS_BY_ID else "default",
            evidence=evidence, question=question, options=options, lock=question,
            facts={"what": notices.clean(item.get("about") or "", 120), "when": str(item.get("deadline") or "")},
            consequence=notices.clean(item.get("consequence") or "", 200), deadline=_deadline(item.get("deadline")),
            haiku=False))
    return out


async def run() -> list[Candidate]:
    """One quiet look. Returns what Zero would raise; notices.run_pass puts it through the rules."""
    if not notices.enabled() or notices.mode("quiet") == "off":
        return []
    try:
        from . import health
        if not health.status.get("claude", True):
            return []
    except Exception:
        pass
    reply = await run_turn(prompt())
    found = candidates(reply or "")
    log.info("quiet look raised %d", len(found))
    return found
