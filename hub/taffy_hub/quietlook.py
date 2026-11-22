""














import json
import logging
import re
from datetime import datetime, timedelta

from . import calcache, config, home, notices, who
from .notices import Candidate, Option

log = logging.getLogger("taffy.quietlook")

MAX_RAISED = 2
TRIP_DAYS = calcache.DAYS_AHEAD
NOT_NOW = "Not now"

NOTE = """[Quiet look from the hub, not a message from {first}. {first} won't see this conversation.]
{possessive} chat with you has been quiet for two hours. It is {now}. Look over what you know: your memory, your \
notes on how {first} wants these questions, the calendar and Trello below, and {accounts} if a \
quick read would settle something. Decide whether there is one thing (two at most) {first} would want raised now that \
is not already an open question below: something with a date or a consequence that nobody is handling, such as a \
trip with no pet sitter, an event with nothing arranged, or a promise about to slip. You only read here; change \
nothing.{handoff}

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

Trips and stays away through the next {trip_days} days, so packing, bookings and who covers what are ready before the \
day. Raise a trip at most once, with the subject shown for it:
{trips}

Trello cards due soon or in progress:
{cards}

{right_now}

Your recent chat with {first}, newer than your memory and notes (go by its dates when they disagree):
{chat}

Reply with one JSON object and nothing else. If nothing is worth raising, reply {{"raise": []}}. Otherwise:
{{"raise": [{{"subject": "a short stable key, like dog-sitter-oct-17", "owner": "which Zip would follow through: \
{owners}", "evidence": "the reasoning behind the question, one to three short sentences", "question": "one \
short question on one line, with no statement before it", "options": [{{"text": "a short answer in {possessive} \
words", "closes": false}}], "deadline": "YYYY-MM-DD HH:MM when it matters, or null", "consequence": "what happens if nobody \
acts, or empty", "about": "the person, thing or event"}}]}}
Give two or three options. The first one does something, or starts it with a clear next step such as "Walk me \
through it" or "Open it" when it takes more than one step. Set "closes" to true on an answer that ends the question \
with nothing for you to do, such as "I already handled it"; every other answer is sent to you to act on.
The app shows the question first and folds the evidence under it. Write the evidence as the facts you checked, \
starting with where you found them in a few words ("Your calendar shows...", "Your October 3 email to Jake...", \
"School said..."). Never guess: leave out anything you couldn't check, or name the teammate who has it ("School \
has your grades"). Use the words {first} \
would use, and leave out site, check and form labels {first} wouldn't recognize. Write it to {first} as a teammate \
would: "you" and "your", never {possessive} name, and "I" for what you checked. Only raise what you checked yourself, with nothing found that shows it is handled. Never raise a \
newsletter, a sale or any mass email. Never turn a purchase, a receipt, a sign-in or an account notice into a money or security worry unless \
the source itself says something is wrong. Keep the look short: a few quick reads at most, no research, no new tasks \
and no messages to teammates. If what you would raise was turned down before and nothing about it changed, leave it.
Never offer to pay, buy, order, sign or move money. Plain complete sentences, no em dashes, no emojis."""




HANDOFF = (" When what you found is something to do that sends nothing to anyone, spends no money, deletes nothing for "
           "good and submits nothing, don't raise it: put it on the board for yourself with kanban_create (assignee "
           "default), saying what you found and what to do, and that task does it. That is the one task you may make "
           "here. Raise a question only for a send, money, a delete for good, a final submit or publish, or a choice "
           "that is {first}'s to make.")


async def run_turn(text: str) -> str | None:
    ""
    from . import hermes
    return await hermes.run_turn("default", text, lane="quiet")


def _calendar(now: float) -> str:
    from . import factkeeper
    end = now + 3 * 86400
    lines = []
    for e in calcache.events_between(notices.day_start(now), end)[:40]:
        when = "all day" if e.get("all_day") else f"{notices.clock(e['start'])} to {notices.clock(e['end'])}"
        place = f", {e['location']}" if e.get("location") else ""
        lines.append(f"- {notices.date_short(e['start'])} {notices._et(e['start']).strftime('%a')}, {when}: "
                     f"{e['title']}{place}{factkeeper.calendar_note(e, "default")}")
    return "\n".join(lines) or "(no copy of the calendar yet)"


def _trips(now: float) -> list[tuple[str, dict]]:
    ""



    out = []
    for e in calcache.events_between(now, now + TRIP_DAYS * 86400):
        if calcache.trip(e):
            words = re.findall(r"[a-z0-9]+", re.sub(r"\([^)]*\)", " ", e["title"]).lower())
            out.append((f"trip-{'-'.join(words)[:36].strip('-')}-{notices._et(e['start']).strftime('%b%d').lower()}",
                        e))
    return out


def _trip_lines(now: float) -> str:
    lines = []
    for subject, e in _trips(now)[:10]:
        last = e["end"] - (1 if e.get("all_day") else 0)
        days = [f"{notices._et(t).strftime('%a')} {notices.date_short(t)}" for t in (e["start"], last)]
        when = days[0] if days[0] == days[1] else f"{days[0]} to {days[1]}"
        if not e.get("all_day"):
            when += f", {notices.clock(e['start'])} to {notices.clock(e['end'])}"
        place = f", {p}" if (p := calcache.physical_place(e)) else ""
        notes = f" Notes: {notices.clean(e['description'], 160)}" if e.get("description") else ""
        lines.append(f"- {when}: {e['title']}{place}.{notes} (subject: {subject})")
    return "\n".join(lines) or "(none)"


def _cards(now: float) -> str:
    lines = []
    for c in calcache.trello_cards()[:30]:
        due = f", due {notices.date_short(c['due'])}" if c.get("due") else ""
        lines.append(f"- {c['name']} ({c.get('list') or 'no list'}{due})")
    return "\n".join(lines) or "(none)"


def prompt(chat: str = "") -> str:
    ""
    now = notices.now()
    open_rows = notices._open_rows()
    opened = "\n".join(f"- {json.loads(r['card'] or '{}').get('question', '')}" for r in open_rows) or "(none)"
    dismissed = "\n".join(f"- {json.loads(r['card'] or '{}').get('question', '')}" for r in notices._db().execute(
        "SELECT card FROM notices WHERE state='dismissed' AND updated>? ORDER BY updated DESC LIMIT 10",
        (now - 14 * 86400,))) or "(none)"
    off = [notices.NOTICERS[n][0] for n in notices.NOTICERS if notices.mode(n) == "off"]
    ids = [b.id for b in config.BOTS if not b.sandbox]
    owners = ", ".join(ids[:-1]) + f" or {ids[-1]}" if len(ids) > 1 else ids[0]
    accounts = ("the email and calendar tools" if home.connectors() == "none"
                else f"{who.possessive()} accounts through the connectors tool")
    from . import rightnow, standing
    right_now = rightnow.text("default") or "[Right now: no current plans or decisions saved.]"
    from . import readokays
    handoff = HANDOFF.format(first=who.first()) if readokays.hard_stops() else ""
    return NOTE.format(first=who.first(), possessive=who.possessive(), owners=owners, accounts=accounts, handoff=handoff,
                       right_now="\n\n".join(t for t in (right_now, standing.closed_text("default")) if t),
                       now=notices._et(now).strftime("%A, %B %-d, %Y, %-I:%M %p ") + home.tz_word(),
                       prefs=notices.preferences() or "(nothing saved yet)", open=opened, dismissed=dismissed,
                       off="\n".join(f"- {o}" for o in off) or "(none)", calendar=_calendar(now), cards=_cards(now),
                       trips=_trip_lines(now), trip_days=TRIP_DAYS,
                       chat=chat or "(no recent chat)")


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
    ""
    data = notices._parse(reply or "")
    items = (data or {}).get("raise") if isinstance(data, dict) else None
    trips = [subject for subject, _ in _trips(notices.now())]
    out = []
    for item in (items if isinstance(items, list) else [])[:MAX_RAISED]:
        if not isinstance(item, dict):
            continue
        question = notices.clean(item.get("question") or "", 200)
        evidence = notices.clean(item.get("evidence") or "", 320)



        raw = [o if isinstance(o, dict) else {"text": o} for o in (item.get("options") or [])]
        picks = [(notices.clean(o.get("text") if isinstance(o.get("text"), str) else "", 80), o.get("closes") is True)
                 for o in raw]
        picks = [(t, closes) for t, closes in picks if t][:3]
        texts = [t for t, _ in picks]
        closing = {t for t, closes in picks if closes}
        if not question or not evidence or not texts:
            continue
        if len(texts) == 1:
            texts.append(NOT_NOW)
        if notices.off_voice(question) or notices.off_voice(evidence):
            log.info("quiet look's question dropped: it speaks about the person, not to them")
            continue
        options = []
        for i, t in enumerate(texts):
            if t == NOT_NOW and i > 0:
                options.append(Option(t, "wait", wait_days=2, follow="Okay. I'll ask again in two days."))
            elif i > 0 and t in closing:
                options.append(Option(t, "done", follow="Okay. I'll leave it with you."))
            else:
                options.append(Option(t, "act" if i == 0 else "decide", say=f"About your question \"{question}\": {t}"))
        owner = str(item.get("owner") or "default")
        key = "-".join(re.findall(r"[a-z0-9]+", str(item.get("subject") or question).lower()))[:60]
        key = next((t for t in trips if t in key), key)
        out.append(Candidate(
            noticer="quiet", subject=f"quiet:{key}", owner=owner if owner in config.BOTS_BY_ID else "default",
            evidence=evidence, question=question, options=options, lock=question,
            facts={"what": notices.clean(item.get("about") or "", 120), "when": str(item.get("deadline") or "")},
            consequence=notices.clean(item.get("consequence") or "", 200), deadline=_deadline(item.get("deadline")),
            haiku=False))
    return out


async def run() -> list[Candidate]:
    ""
    if not notices.enabled() or notices.mode("quiet") == "off":
        return []
    try:
        from . import health
        if not health.status.get("claude", True):
            return []
    except Exception:
        pass
    from . import recentchat
    reply = await run_turn(prompt(await recentchat.body("default")))
    found = candidates(reply or "")
    log.info("quiet look raised %d", len(found))
    return found
