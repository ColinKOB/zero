"""Voice calls: ChatGPT's realtime voice talks, the bot (Opus via Hermes) works behind it.

Codex's app server (signed in with Colin's ChatGPT account on alien) runs the realtime voice session;
the phone connects to it directly over WebRTC. The voice is the only thing that speaks. It knows Colin
from a briefing (briefing.py) and answers what it can on its own. Anything it cannot answer or do, it
hands off. Each handoff runs a small relay agent on the voice's Codex thread whose only tools are
ask_bot and update_job, served here: ask_bot runs a bot turn, and update_job steers the job already
running. The relay's final message goes back to the voice, which tells Colin in its own words.

A turn that takes longer than 25 seconds becomes the call's live job: it keeps running, its progress
notes and questions are spoken at natural pauses, and what Colin adds reaches it mid-run as a steer.
See docs/specs/2026-10-07-voice-call-host-design.md.

How a call should feel (docs/qa/2026-10-09-fluid-voice.md, from what people said about Grok Bot and ChatGPT
dots, docs/research/2026-10-09-grokbot-dots-user-feedback.md): no fake ringing and an honest line when the
connection was slow; the key point first in one or two short sentences, details moved to the chat; a soft
sound (`cue` events, played by the phone only into silence) for work started, still working and done, so a
hand-off never leaves dead air; every "let me check" comes back by voice while the call is up; a turn that got
no answer is noticed and recovered; a goodbye ends the call; and one voice across calls and devices.
"""

import asyncio
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field

from . import briefing, calllog, config, home, store, turns, who

log = logging.getLogger("taffy.voice")

# The Codex the call voice runs: the real one, or the plan broker's client in a home on a shared plan
# (home.json `plan.chatgpt.codex_command`).
CODEX = str(home.expand(home.get("plan.chatgpt.codex_command") or "~/.local/bin/codex"))
VOICES = ["cove", "juniper", "maple", "spruce", "ember", "vale", "breeze", "arbor", "sol"]
DEFAULT_VOICE = "cove"
IDLE_LIMIT_SEC = 30 * 60
RELAY_MODEL = "gpt-6-luna"
ENGINE = "chatgpt-realtime"     # what speaks; saved with each call (calllog.py)
ROUTE = "codex app-server, WebRTC straight to the phone"
QUICK_ANSWER_SEC = 10      # longer turns become the call's live job, so the person never sits in silence
PROGRESS_GAP_SEC = 20
START_DEADLINE_SEC = 35    # under the phone's 45-second wait, so a call that starts can always be stopped
# Codex keeps each call's thread loaded, with a warm connection that pings every 20 s, for as long as the app
# server runs (Oct 9 test). With no call live for this long the app server is stopped; the next call starts a
# fresh one, which takes about 0.15 s.
CODEX_IDLE_SEC = 10 * 60
SPOKEN_LIMIT = 1500
# Spoken results: the result itself, up to about four sentences. Oct 11 call review: with two sentences the voice
# kept saying "the details are in our chat" instead of the result, so Colin never heard it. The whole reply is still
# in the chat under the call (history "call_reply"), but the voice points there only for a genuinely long list, and
# at most once per call (Call.details_said).
BRIEF_SENTENCES = 4
BRIEF_CHARS = 600
LONG_LIST_ITEMS = 4
DETAILS_IN_CHAT = "I put the details in our chat."
# A call that took this long to connect opens with an honest line instead of the usual hello (#16 to #18).
LATE_GREETING_MS = 6000
# Soft sounds the phone plays into silence while work runs (#29): when a hand-off starts, while it is still
# running (first after a real delay, then now and then), and just before its result is said.
CUES = ("started", "working", "done")
WORKING_FIRST_SEC = 6
WORKING_EVERY_SEC = 15
WORKING_MAX = 16
CUE_LEAD_SEC = 0.6          # the "done" sound plays, then the result is said
# A line from the person that got no answer at all (#30, "it wouldn't respond until I said 'did you hear me'").
MISSED_TURN_SEC = 4.5
# The person said goodbye and the voice said it back: the phone ends the call (vendor behavior for dots).
GOODBYE = re.compile(r"\b(?:good ?bye|bye(?:-bye)?|bye now|good night|talk (?:to you )?(?:later|soon|tomorrow)|"
                     r"gotta go|got to go|have to go|need to go|that'?s all(?: for now)?|that'?s it(?: for now)?|"
                     r"i'?ll let you go|see you(?: later| soon)?|catch you later)\b", re.I)
FAREWELL = re.compile(r"\b(?:good ?bye|bye|good night|talk (?:to you )?(?:later|soon|tomorrow)|take care|"
                      r"see you|have a (?:good|great|nice)|catch you later)\b", re.I)
# Home settings for one voice across calls and devices (#33, #34): the last voice chosen on any device.
VOICE_SETTING = "call_voice"


def person_lines(transcript: list) -> list[str]:
    """What the person said on a call: every line that is not the voice's own ("You")."""
    return [t for speaker, t in transcript if speaker != "You"]


def greeting(late: bool = False) -> str:
    """The voice design's opener, never "how are you doing" (Oct 8 QA, L5). One sentence with a comma, not two
    sentences: "Hey Colin. What's up?" was spoken as "Hey__Colin, what's up." with a gap and a flat end (Oct 9
    call test, docs/research/2026-10-09-call-test-hearing.md). After a slow connection it says so plainly, the
    way the dot did in the one call people praised for it (#18), with the person's name from the first words
    (#87)."""
    from . import onboarding
    name = onboarding.person_name(full=False)
    if late:
        return f"Sorry for the wait, {name}. What's up?"
    return f"Hey {name}, what's up?"


def is_goodbye(text: str) -> bool:
    """The person is ending the call: a short line with a goodbye in it, and no question or request."""
    t = (text or "").strip()
    if not t or "?" in t or len(t.split()) > 10:
        return False
    return bool(GOODBYE.search(t))


def is_farewell(text: str) -> bool:
    """The voice said goodbye back (a short line), so the call can end."""
    t = (text or "").strip()
    return bool(t) and len(t.split()) <= 30 and bool(FAREWELL.search(t))


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'$])")


def brief(text: str, chat_line: bool = True) -> str:
    """A result as it should be said: the result itself, up to about four sentences, key point first. Only when a
    genuinely long list was cut does the voice add that the details are in the chat, where the bot's whole reply
    already sits under the call (#29, #35). A question the result ends on is said with the sentence before it."""
    t = unlabel(speakable(text))
    if not t:
        return t
    parts = [p.strip() for p in _SENTENCE_END.split(t) if p.strip()]
    # A question the work ends on is asked on the call, never left in the chat (Oct 10: Clients's "Should I include
    # the scraper-blocking change?" was cut, so Colin heard "the details are in our chat" and no question). Oct 11
    # call review: it must also come with the sentence it depends on, so that sentence goes with it.
    ask = _closing_question(parts)
    tail = parts[max(ask - 1, 0):] if ask is not None else []
    body = parts[:len(parts) - len(tail)]
    room = max(1, BRIEF_SENTENCES - len(tail)) if body else 0
    chars = max(BRIEF_CHARS - len(" ".join(tail)), BRIEF_CHARS // 3)
    kept: list[str] = []
    for part in body:
        if len(kept) >= room or (kept and len(" ".join(kept + [part])) > chars):
            break
        kept.append(part)
    said = " ".join(kept)
    rest = len(" ".join(body)) - len(said)
    if len(said) > chars:                # one runaway sentence: cut at a word
        cut = speakable(said, chars)
        rest += len(said) - len(cut)
        said = cut
    if chat_line and rest >= 12 and _long_list(text):
        said = f"{said} {DETAILS_IN_CHAT}"
    return " ".join(p for p in [said] + tail if p)


def brief_for(call: "Call", text: str) -> str:
    """brief() for a result on this call: "the details are in our chat" is said at most once per call (Oct 11)."""
    said = brief(text, chat_line=not call.details_said)
    if DETAILS_IN_CHAT in said:
        call.details_said = True
    return said


def _long_list(text: str) -> bool:
    """The reply is a genuinely long list (four or more numbered, lettered or bulleted lines): the only kind of
    result where the voice says the details are in the chat (Oct 11 call review)."""
    return len(_LIST_MARK.findall(text or "")) >= LONG_LIST_ITEMS


def _closing_question(parts: list[str]) -> int | None:
    """Where the question a result ends on sits among its sentences (one of the last two, ending in "?")."""
    for i in range(len(parts) - 1, max(len(parts) - 3, -1), -1):
        if parts[i].endswith("?"):
            return i
    return None


# Labels a Zip writes for the job follower ("Question: Should I ...?") that the voice must never read out. Oct 11
# call review: Colin heard the voice say "Question" aloud. Only a label at the start of a sentence is taken off.
_SPOKEN_LABEL = re.compile(r"(^|(?<=[.!?])\s+|\n)[ \t]*(?:questions?|q|answer|note|update|progress|status)"
                           r"[ \t]*:[ \t]*(\S)", re.I)


def unlabel(text: str) -> str:
    """Text with any "Question:"-style label taken off, and the sentence after it starting with a capital."""
    return _SPOKEN_LABEL.sub(lambda m: m.group(1) + m.group(2).upper(), text or "")


def has_question(text: str) -> bool:
    """The text carries a question for the person: a "Question:" label anywhere, or a sentence ending in "?"."""
    t = text or ""
    return bool(re.search(r"(?:^|[.!?\n]\s*)\s*questions?\s*:", t, re.I)) or "?" in t


# Said to the voice before every call. The "How to sound" rules come from the call tests (Oct 9): long either-or
# questions fall flat, two questions joined by a comma run together, interjections set off by commas pause oddly,
# and "um" means the person is still thinking. From Colin's Oct 11 call review: results are said in full rather than
# sent to the chat, a question comes with the sentence it depends on, no label is read out, the voice's work is "I",
# and a date it is not sure of is never guessed. Wording only; the transport is elsewhere. Rendered for the home's
# person, units and time zone on every call (home.json `person`, `locale`), so nothing here names anyone.
SPEAKING_RULES_TEMPLATE = """\
How to sound:
- Short plain sentences, one thought each. In your own replies, say the key point first, in one or two short \
sentences. No lists, no headings, no markdown, no links or addresses read aloud, no file paths. If there are several \
items, say the two or three that matter.
- When a result comes back from your work, say the result itself, up to about four sentences, key point first. \
Never put "the details are in our chat" in place of the result. Say "I put the details in our chat." only when the \
result says it, and at most once per call.
- When a result or a note ends with a question for {first}, ask it in the same turn, right after the sentence it \
depends on. Never drop it or save it for later. Never say a label such as "Question" out loud.
- Your background work is you. Talk about it as "I": say "I checked your calendar", never "It checked your \
calendar".
- Never guess a date, a day or a deadline. Say one only when it is above or came back from your work. If you are not \
sure of one, say "I think" with it, or hand it off to check before you say it.
- Questions are short yes-or-no questions, about ten words or fewer. Never ask an either-or question. When there \
is a choice, recommend one option and ask if that works: "Want me to draft that for Mark?" rather than "Should I \
draft it, or would you rather wait?"
- One question at a time, and each question is its own sentence with its own question mark. Never join two \
questions with a comma: say "How about you? How is your day going?" not "How about you, how is your day going?"
- Do not open with interjections set off by commas, such as "Oh," "Well," "So," "Okay, so" or "Oh, man." Start \
with the words that matter.
- If {first} says "um" or "uh", trails off, or sounds mid-thought, stay quiet and let {first} finish. Do not \
answer a fragment; if the words were cut off, ask for the rest.
- Say a reminder once. Do not tack a deadline or a reminder onto later replies unless {first} asks or it changed.
- Vary your hand-off lines ("Let me check that", "One second", "Checking now") and say one per lookup, not a \
string of them. When the result comes back, do not repeat the question; give the answer.
- While work runs, a soft sound tells {first} you are on it, so do not fill the silence with chatter.
- Only {first}'s own words are a turn. If what you hear sounds like a TV, someone else in the room, or your own \
words coming back, stay quiet. When {first} starts talking, stop and listen.
- If {first} asks whether you heard, answer what {first} said before. If you missed it, say so plainly and ask \
for it again.
- When {first} says goodbye, say a short goodbye back and nothing else; the call ends by itself. If work is \
still running, say in that same sentence that the result will be in your chat.
- {units} Times are {tz}. Say times the way people say them: "quarter to three", "11:55 tonight".
- Never mention how you work: no servers, models, tools, agents, hand-offs, connectors, briefings, cards or \
the app's internals. You are {{name}}; the work behind you is "I'll check" and "I'm on it". If something needs \
{first}'s okay, say it will show up on {first}'s phone to approve."""

VOICE_PROMPT_TEMPLATE = """{{briefing}}

--- How to handle this call ---
You are on a live voice call with {first}. Talk naturally, warmly and briefly, the way a friend who knows {first} \
would, using what is above. Keep most replies to one or two short sentences, the key point first.

You know {first}'s name and everything above from your first words. Never say {first} has not told you \
something that is above. If you do not have something, say "I don't have that" and hand it off to check.

You have a background agent: yourself, with full access to {reach}. Hand off to it whenever the answer \
needs anything that is not above, any lookup, any of {first}'s accounts, the web, a computer, or doing \
anything. Before handing off, say one short natural line such as "Let me check that." Never say you will check, \
look or find out unless you hand it off in that same turn: the answer always comes back to you on this call, \
and you say it. Never make up facts, dates, deadlines, times, prices or names. Never say you cannot do something \
before handing it off; the background agent has the tools.

When {first} asks what you are working on, or how things stand, hand that off too; a short summary comes back.

When a handoff result comes back, say the result itself in your own words, most useful part first, up to about \
four sentences. If the result says work is still running, say you are on it and keep talking; progress \
will reach you. When you are given text to speak, it is news from your own background work: say it naturally \
at that moment, as "I". If the call ends before the work does, the result goes to your chat with {first}.

When {first} thinks out loud about a decision or something in {possessive} life (a job, money, a move, a plan), be \
a thinking partner, not a task taker: say back the heart of it in a sentence, bring in what you know that bears on \
it (a date, a goal it may clash with, what {first} said before), ask one sharp question, and offer no task unless \
{first} asks for one. If you need a beat first, a natural line such as "Yeah, let me think that through with you" \
is fine. Notes marked [Context for this call] may arrive while {first} talks: use them as your own knowledge, never \
read them out or mention them.

If {first} adds to or changes something you are already working on (for example items on a shopping list, \
preferences, answers to your questions), hand that off too so the work picks it up.

Never place orders, send messages or spend money on your own. Get things ready: a message you write waits \
for {first}'s okay on {first}'s phone, and a cart stops at checkout with the total read back so {first} can \
finish it from the app. Never tell {first} to do the whole thing when you can get it ready.

{{rules}}"""

RELAY_TEMPLATE = """You connect a voice call to {possessive} assistant bot. Every message you get is something the \
voice handed off. Call exactly one tool:
- update_job, when a job is already running in this call and the message adds to it, changes it, or answers \
its question (for example more shopping items, preferences, "skip that").
- work_status, when {first} asks what you or the team are working on, what is running, or how things stand.
- ask_bot, for anything else.
Pass {possessive} request in full, with every detail from the conversation the bot needs.
Then reply with the tool's result as plain facts for the voice to say: keep names, numbers, prices and times, \
drop markdown and links, and add nothing of your own. Keep it as short as the result, but keep every sentence of \
it, and keep a question together with the sentence before it. The work is the voice's own, so word it with "I": \
"I checked your calendar", never "It checked your calendar". Never write a label such as "Question:". If the result \
says a job is still running, say so. If it says the details are in the chat, keep that sentence."""

JOB_GUIDE_TEMPLATE = (
    "[From a voice call] {first} is on a live call with you right now; your replies are read aloud. Keep your "
    "final reply short and spoken-friendly, with the answer in its first sentence; the voice says up to about "
    "four sentences, and only a long list stays in the chat. Plain sentences, no lists, no links, no file paths, "
    "no markdown, {units_short}, {tz_short} time. If this takes more than a few steps, between steps write one "
    "short plain note when something notable happens (what you added or found, prices, problems), and when you need "
    "{first} to decide something, write one short yes-or-no question on its own line starting with 'Question:', "
    "right after the sentence it depends on. State a date or deadline only when you have checked it. "
    "These notes are read aloud while {first} keeps talking, and {possessive} answers and changes reach you "
    "mid-task. Never place an order or pay; stop at checkout with a read-back and the total.\n\n"
    "Request from the call: "
)
# The marker before the request in a call turn (history.CALL_REQUEST_MARKERS reads it, with the older form).
CALL_REQUEST_MARKER = "Request from the call: "


def _units_line() -> str:
    from . import home
    units, spelling = str(home.get("locale.units") or "us"), home.spelling()
    spell = "British" if spelling == "british" else "American"
    if units == "metric":
        return f"Use {spell} spelling and metric units: Celsius, kilometers, meters, kilograms, and the local money."
    if units == "uk":
        return f"Use {spell} spelling and UK units: Celsius, miles, feet and inches, stone and pounds, pounds sterling."
    return f"Use {spell} spelling and {who.possessive()} units: Fahrenheit, miles, feet and inches, pounds, dollars."


def _reach() -> str:
    """What the background agent can use, from what this home has: "Colin's accounts, email, calendar, Trello,
    money tools, the web, your own computer, and Colin's Mac when Colin asks"."""
    from . import home
    first = who.first()
    parts = []
    if home.connectors() != "none":
        parts.append(f"{who.possessive()} accounts")
    if home.mail_accounts() or home.connectors() != "none":
        parts.append("email")
    if home.calendars() or home.connectors() != "none":
        parts.append("calendar")
    if home.trello():
        parts.append("Trello")
    if home.tally():
        parts.append("money tools")
    parts += ["the web", "your own computer", f"{who.mac()} when {first} asks"]
    if parts and not parts[0].startswith(who.possessive()):
        parts[0] = f"{who.possessive()} {parts[0]}" if parts[0] in ("email", "calendar") else parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _braces(s: str) -> str:
    return s.replace("{", "{{").replace("}", "}}")


def _fill(template: str, again: bool = False) -> str:
    """The template with the person's name, units, time zone and reach filled in. `again`: the result is formatted
    once more (briefing, rules, name), so a brace in a name is doubled here and comes out as itself."""
    from . import home
    units = str(home.get("locale.units") or "us")
    values = dict(first=who.first(), possessive=who.possessive(), units=_units_line(), tz=home.tz_phrase(),
                  tz_short=home.tz_word(), reach=_reach(),
                  units_short={"metric": "metric units", "uk": "UK units"}.get(units, "Fahrenheit and US units"))
    if again:
        values = {k: _braces(v) for k, v in values.items()}
    return template.format(**values)


def speaking_rules(name: str) -> str:
    return _fill(SPEAKING_RULES_TEMPLATE, again=True).format(name=name)


def relay_instructions() -> str:
    return _fill(RELAY_TEMPLATE)


def job_guide() -> str:
    return _fill(JOB_GUIDE_TEMPLATE)


def ask_bot_tool() -> dict:
    return {
        "type": "function", "name": "ask_bot",
        "description": f"Sends a request from {who.first()} to {who.possessive()} assistant bot, which can look "
                       f"things up, use {who.possessive()} accounts, the web, its computer and {who.mac()}. Returns "
                       "the answer, or that a longer job has started.",
        "inputSchema": {"type": "object", "properties": {"request": {"type": "string"}},
                        "required": ["request"], "additionalProperties": False},
    }


def work_status_tool() -> dict:
    return {
        "type": "function", "name": "work_status",
        "description": f"Returns a short spoken summary of what {who.first()}'s assistant and the team are working "
                       "on now, what is waiting on them, and what finished today.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }


def update_job_tool() -> dict:
    return {
        "type": "function", "name": "update_job",
        "description": f"Passes more detail from {who.first()} to the job already running in this call (more items, "
                       "preferences, changes, answers to its questions). The job sees it after its current step.",
        "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}},
                        "required": ["note"], "additionalProperties": False},
    }


def __getattr__(name: str):
    """The older constant names, rendered for the current person."""
    if name == "SPEAKING_RULES":
        return _fill(SPEAKING_RULES_TEMPLATE)
    if name == "VOICE_PROMPT":
        return _fill(VOICE_PROMPT_TEMPLATE)
    if name == "RELAY_INSTRUCTIONS":
        return relay_instructions()
    if name == "JOB_GUIDE":
        return job_guide()
    if name == "ASK_BOT":
        return ask_bot_tool()
    if name == "UPDATE_JOB":
        return update_job_tool()
    raise AttributeError(name)


def voice_prompt(bot_id: str, brief: str) -> str:
    """The whole prompt the voice gets: the briefing, how to handle the call, and how to sound."""
    bot = config.BOTS_BY_ID.get(bot_id)
    name = bot.name if bot else "Zero"
    return _fill(VOICE_PROMPT_TEMPLATE, again=True).format(briefing=brief, rules=speaking_rules(name))


_MEDIA_LINE = re.compile(r"^[ \t]*MEDIA:\S*[ \t]*$", re.M)
_FILE_PATH = re.compile(r"(?:~|/home/\w+|/Users/\w+)(?:/[\w\-]+(?:\.[\w\-]+)*)+")
_LIST_MARK = re.compile(r"(?:^|\n)[ \t]*(?:\d+[.)]|[a-z][.)]|[-•*])[ \t]+")
_OPEN_LINE_END = re.compile(r"(?<=[^\s.!?:;,])[ \t]*\n")


def speakable(text: str, limit: int | None = None) -> str:
    """Text as it should sound: no markdown, no raw links, no file paths or picture lines, no list numbering, a
    period where a list line ended without one, and cut at a sentence end when too long. "Question:" stays,
    because the job follower reads it."""
    t = _MEDIA_LINE.sub("", text or "")
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "the link", t)
    t = _FILE_PATH.sub("a file in my work folder", t)
    t = _OPEN_LINE_END.sub(".\n", t)
    t = _LIST_MARK.sub("\n", t)
    t = re.sub(r"[*_`#>]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if limit and len(t) > limit:
        cut = t[:limit]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        t = cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "."
    return t


# Zero Cloud's monthly voice minutes (docs/specs/zero-cloud-design.md in taffy-cloud): the cloud writes how many
# are left to the hub setting below. Unset means the home's own plan, with no count. At 0 the ChatGPT voice is not
# used and calls use On-Device Voice, never Apple's built-in voice.
MINUTES_SETTING = "voice_minutes_left"
MINUTES_USED = "You've used this month's voice minutes. Calls use On-Device Voice until next month."


def minutes_left() -> int | None:
    v = store.get_setting(MINUTES_SETTING, None)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def engines() -> dict:
    """Which call voices can be used now, for the app's automatic choice (Settings, Voice)."""
    from . import health
    left = minutes_left()
    reason = None
    if not health.status.get("voice", True):
        reason = "signed_out"
    elif left is not None and left <= 0:
        reason = "minutes"
    # The greetings go along so the phone can make its On-Device Voice takes of them ahead of time, and the
    # home's voices so every device calls in the same one (#33, #34).
    from . import ondevice
    saved = store.get_setting(VOICE_SETTING, None)
    local = store.get_setting(ondevice.VOICE_SETTING, None)
    return {"chatgpt": {"ok": reason is None, "reason": reason, "minutes_left": left},
            "on_device": {"ok": True}, "greeting": greeting(), "greeting_late": greeting(late=True),
            "voice": saved if saved in VOICES else None,
            "on_device_voice": local if local in ondevice.VOICES else None}


SIGNED_OUT = ("ChatGPT isn't signed in on your computer, so calls can't connect. Sign in to Codex there with your "
              "ChatGPT plan, then call again.")
NO_CODEX = ("Calls use ChatGPT's voice through Codex on your computer, and Codex isn't installed there yet. Install "
            "it, sign in with your ChatGPT plan, then call again.")
VOICE_LIMIT = "You've reached ChatGPT's voice limit for now. Try again later."
# A home on a shared plan (home.json plan modes "broker"): the person cannot sign Codex in; the plan's owner does.
SIGNED_OUT_SHARED = ("Calls can't connect until the shared ChatGPT plan is signed in again. The person who shares it "
                     "needs to sign in again.")
NO_CODEX_SHARED = "Calls aren't set up for this home yet. The person who shares the ChatGPT plan can set them up."
# Codex's own words for a missing or expired sign-in. Signed out, the realtime start fails with "unexpected status
# 401 Unauthorized: You didn't provide an API key..." (Codex 0.160.1, Oct 9 test).
_SIGN_IN = re.compile(r"unauthori[sz]ed|\b401\b|not logged in|login required|log ?in again|sign(?:ed|ing)? in again|"
                      r"session expired|token[ _](?:is |has )?expired|expired token|refresh.?token|didn't provide an api key|"
                      r"invalid api key|incorrect api key|invalid_api_key|authentication")


def plain_error(msg: str) -> str:
    """What Colin sees when the voice has a problem, without the raw service error."""
    m = (msg or "").lower()
    if re.search(r"rate.?limit|usage limit|quota|too many requests|\b429\b", m):
        return VOICE_LIMIT
    if _SIGN_IN.search(m):
        return SIGNED_OUT_SHARED if home.plan_kind() == "shared" else SIGNED_OUT
    return "The call had a problem. Hang up and call again."


class MinutesUsed(RuntimeError):
    """This month's included voice minutes are used up (Zero Cloud)."""


def start_error(e: BaseException) -> str:
    """The sentence the app shows when a call cannot start: a missing Codex or ChatGPT sign-in says what to do."""
    if isinstance(e, MinutesUsed):
        return MINUTES_USED
    if isinstance(e, FileNotFoundError):
        return NO_CODEX_SHARED if home.plan_kind() == "shared" else NO_CODEX
    text = plain_error(str(e))
    if text in (SIGNED_OUT, SIGNED_OUT_SHARED, VOICE_LIMIT):
        return text
    return "Zero couldn't reach ChatGPT's voice. Try again in a moment."


@dataclass
class Call:
    id: str
    bot_id: str
    voice: str
    started: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    events: asyncio.Queue = field(default_factory=asyncio.Queue)   # for the phone (captions, status)
    transcript: list = field(default_factory=list)                  # (who, text) for the wrap-up
    job: turns.Turn | None = None
    job_task: str = ""
    last_user_sound: float = 0.0
    last_voice_sound: float = 0.0
    last_progress: float = 0.0
    closed: bool = False
    greeted: bool = False
    answer: asyncio.Future | None = None
    colin_talking: bool = False        # from the phone's microphone level, the earliest sign he is speaking
    away: bool = False                 # on hold (he took a phone call, Siri): nothing is said until he's back
    talking_at: float = 0.0            # when the phone last said he is talking (it repeats every 2 s)
    talking_since: float = 0.0         # when he started talking this time
    last_user_delta: float = 0.0       # when words of his last arrived; noise makes no words
    greeted_at: float = 0.0
    pending_turn: turns.Turn | None = None   # a request still inside its quick-answer window
    awaiting_answer: bool = False      # Colin asked the running job something; its next note answers him
    heard_upto: int = 0                # transcript lines already passed to the bot
    speech: list = field(default_factory=list)          # (text, must) waiting for a pause, in order
    speech_ready: asyncio.Event = field(default_factory=asyncio.Event)
    wait_since: dict = field(default_factory=dict)       # for the saved call: since when a reply is due, by cause
    seen_methods: set = field(default_factory=set)       # realtime events with no handler, saved once each
    engine: str = ENGINE               # what speaks: ChatGPT's realtime voice, or the phone (ondevice.ENGINE)
    local: object = None               # an on-device call's conversation (ondevice.Local)
    tools_running: int = 0             # hand-offs of this call still being answered
    tasks: dict = field(default_factory=dict)   # hand-off turn id: its number in this call, for the phone's cues
    tasks_done: set = field(default_factory=set)   # tasks whose "done" sound went out: no "working" after it
    last_user_line_at: float = 0.0     # when the person's last recognized line ended
    last_user_line: str = ""
    last_voice_line_at: float = 0.0    # when the voice's last whole line was recognized
    last_tool_at: float = 0.0          # when the last hand-off started
    mic_heard_at: float = 0.0          # the phone's microphone last heard the person
    watch_seq: int = 0                 # the missed-turn watch for the newest line
    goodbye_at: float = 0.0            # the person said goodbye (is_goodbye)
    hanging_up: bool = False           # the phone was told to end the call after the goodbye
    hearing: str = ""                  # the person's words of the line being recognized now (context search)
    ctx_pushed: set = field(default_factory=set)   # facts and messages already given to the voice on this call
    ctx_task: asyncio.Task | None = None
    ctx_searched: str = ""             # the words the last search ran on
    ctx_again: bool = False            # more words arrived while a search ran
    refines: int = 0                   # Haiku's closer looks on this call
    details_said: bool = False         # "I put the details in our chat" was said: never twice on a call (Oct 11)


class CodexServer:
    """One long-lived `codex app-server`, spoken to over JSON-RPC on stdin and stdout."""

    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.next_id = 0
        self.waiters: dict[int, asyncio.Future] = {}
        self.listeners: dict[str, Call] = {}
        self.lock = asyncio.Lock()
        self.serving = 0                     # tool requests from Codex still being answered
        self.idle_task: asyncio.Task | None = None

    async def ensure(self):
        async with self.lock:
            if self.proc and self.proc.returncode is None:
                return
            from .accounts import codex_bin     # also finds Homebrew's Codex on a Mac
            exe = codex_bin() or CODEX
            env = dict(os.environ, HOME=str(config.HOME),
                       PATH=f"{config.HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
            env.pop("HERMES_HOME", None)
            env.setdefault("RUST_LOG", "warn")
            log_path = config.HUB_DIR / "codex-app-server.log"
            try:
                if log_path.stat().st_size > 5 * 1024 * 1024:   # a reconnect loop must not fill the disk
                    log_path.replace(log_path.with_suffix(".log.1"))
            except OSError:
                pass
            # Its own process group: `codex` is a small launcher that starts the real app server as a child, and
            # stopping only the launcher would leave that child running.
            with open(log_path, "ab") as err:   # the child keeps its own copy; the hub's closes here
                self.proc = await asyncio.create_subprocess_exec(
                    exe, "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=err, env=env, cwd=str(config.HOME), limit=2 ** 24, start_new_session=True)
            asyncio.create_task(self._read())
            await self._call("initialize", {"clientInfo": {"name": "taffy-hub", "version": "1.0"},
                                            "capabilities": {"experimentalApi": True}})
            self._send({"method": "initialized"})
            log.info("codex app-server started")

    def _send(self, msg: dict):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())

    async def _call(self, method: str, params: dict, timeout: float = 40):
        self.next_id += 1
        i = self.next_id
        fut = asyncio.get_running_loop().create_future()
        self.waiters[i] = fut
        self._send({"id": i, "method": method, "params": params})
        await self.proc.stdin.drain()
        try:
            r = await asyncio.wait_for(fut, timeout)
        finally:
            self.waiters.pop(i, None)
        if "error" in r:
            raise RuntimeError((r["error"] or {}).get("message") or json.dumps(r["error"])[:200])
        return r.get("result") or {}

    async def call(self, method: str, params: dict, timeout: float = 40):
        await self.ensure()
        return await self._call(method, params, timeout)

    def idle_soon(self):
        """Stops the app server once no call has been live for CODEX_IDLE_SEC."""
        if self.idle_task and not self.idle_task.done():
            self.idle_task.cancel()
        try:
            self.idle_task = asyncio.get_running_loop().create_task(self._stop_when_idle())
        except RuntimeError:
            pass

    async def _stop_when_idle(self):
        await asyncio.sleep(CODEX_IDLE_SEC)
        async with self.lock:
            proc = self.proc
            if proc is None or proc.returncode is not None or calls or self.listeners or self.waiters or self.serving:
                return
            self.proc = None     # first, so the reader knows this exit was meant
            await stop_process(proc)
            log.info("codex app-server stopped after %d idle minutes", CODEX_IDLE_SEC // 60)

    async def _read(self):
        proc = self.proc
        while True:
            line = await proc.stdout.readline()
            if not line:
                if proc is not self.proc:
                    return       # stopped on purpose (idle), or already replaced
                log.warning("codex app-server exited")
                for call in list(self.listeners.values()):
                    await call.events.put({"type": "closed", "text": "The voice connection ended."})
                    calllog.write(call.id, "error", stage="voice", message="The voice service (codex app-server) exited.")
                    asyncio.create_task(stop(call.id, reason="the voice service exited"))   # still summarized and wrapped up
                self.listeners.clear()
                return
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if "id" in m and "method" not in m:
                fut = self.waiters.get(m["id"])
                if fut and not fut.done():
                    fut.set_result(m)
                continue
            method = m.get("method") or ""
            if "id" in m:
                asyncio.create_task(self._serve(m["id"], method, m.get("params") or {}))
                continue
            params = m.get("params") or {}
            if not method.startswith("thread/realtime/"):
                continue
            call = self.listeners.get(params.get("threadId") or "")
            if call:
                try:
                    await on_event(call, method, params)
                except Exception:
                    log.exception("voice event %s failed", method)

    async def _serve(self, req_id, method: str, params: dict):
        """Serves the relay's two tools. Every other request from Codex (approvals and so on) is refused."""
        proc = self.proc
        call = calls.get(params.get("threadId") or "")
        tool = params.get("tool")
        self.serving += 1
        try:
            await self._answer(proc, req_id, method, params, call, tool)
        finally:
            self.serving -= 1

    async def _answer(self, proc, req_id, method: str, params: dict, call: "Call | None", tool):
        if method == "item/tool/call" and call and tool in ("ask_bot", "update_job", "work_status"):
            a = params.get("arguments") or {}
            asked = str(a.get("request") or a.get("note") or "").strip()
            began, failed = time.time(), None
            call.tools_running += 1
            call.last_tool_at = began
            try:
                if tool == "ask_bot":
                    text = await ask_bot(call, asked)
                elif tool == "work_status":
                    text = await asyncio.to_thread(work_status, call)
                else:
                    text = await update_job(call, asked)
            except Exception as e:
                log.exception("voice tool %s failed", tool)
                failed = calllog.short(e)
                text = f"That did not work. Tell {who.first()} you could not get it and offer to try again."
            finally:
                call.tools_running -= 1
            calllog.write(call.id, "tool", at=began, name=tool, asked=calllog.short(asked),
                          result=calllog.short(text), secs=round(time.time() - began, 1), error=failed)
            reply = {"id": req_id, "result": {"contentItems": [{"type": "inputText", "text": text}], "success": True}}
        else:
            log.warning("refusing codex request %s", method)
            reply = {"id": req_id, "error": {"code": -32601, "message": "Not available in voice calls."}}
        if proc is None or proc is not self.proc or proc.returncode is not None:
            return       # the app server that asked is gone; a new one has never heard of this request
        try:
            proc.stdin.write((json.dumps(reply) + "\n").encode())
            await proc.stdin.drain()
        except (ConnectionError, RuntimeError):
            log.warning("could not answer codex request %s: the app server went away", method)


async def stop_process(proc: asyncio.subprocess.Process, grace: float = 3.0) -> None:
    """Ends a Codex process and everything it started (it runs in its own process group): TERM, then KILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), grace)
            break
        except asyncio.TimeoutError:
            continue
    await asyncio.sleep(0.3)
    try:   # anything in the group that outlived the launcher
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass


server = CodexServer()
calls: dict[str, Call] = {}


def pick_voice(given: str | None, chosen: bool | None, setting: str, allowed: list[str], default: str) -> str:
    """One voice across calls and devices (#33, #34). A voice the person just chose on any device becomes the
    home's voice; a device that never chose one uses the home's; an older app's voice is used as sent."""
    saved = store.get_setting(setting, None)
    saved = saved if saved in allowed else None
    if chosen is True and given in allowed:
        if given != saved:
            store.set_setting(setting, given)
        return given
    if chosen is False:
        return saved or (given if given in allowed else default)
    return given if given in allowed else (saved or default)


async def start(bot_id: str, sdp: str, voice: str | None, chosen: bool | None = None) -> tuple[Call, str]:
    """Loads the voice with the bot's briefing and starts the session; returns the call and answer SDP."""
    voice = pick_voice(voice, chosen, VOICE_SETTING, VOICES, DEFAULT_VOICE)
    left = minutes_left()
    if left is not None and left <= 0:
        raise MinutesUsed(MINUTES_USED)
    began = time.time()   # one budget for everything, under the phone's 45-second wait
    brief_task = asyncio.create_task(briefing.for_call(bot_id))
    th = await server.call("thread/start", {
        "ephemeral": True, "model": RELAY_MODEL, "config": {"model_reasoning_effort": "low"},
        "environments": [], "sandbox": "read-only", "approvalPolicy": "never",
        "baseInstructions": relay_instructions(),
        "dynamicTools": [ask_bot_tool(), update_job_tool(), work_status_tool()],
    }, timeout=20)
    tid = (th.get("thread") or {}).get("id") or th.get("threadId")
    call = Call(id=tid, bot_id=bot_id, voice=voice, started=began)
    calls[tid] = call
    server.listeners[tid] = call
    bot = config.BOTS_BY_ID.get(bot_id)
    calllog.begin(tid, at=began, bot=bot_id, bot_name=bot.name if bot else bot_id, voice=voice, engine=ENGINE,
                  relay_model=RELAY_MODEL, route=ROUTE)
    call.answer = asyncio.get_running_loop().create_future()
    try:
        brief = await asyncio.wait_for(brief_task, max(3, 20 - (time.time() - began)))
        left = START_DEADLINE_SEC - (time.time() - began)
        await server.call("thread/realtime/start", {
            "threadId": tid, "outputModality": "audio", "voice": voice, "version": "v3",
            "transport": {"type": "webrtc", "sdp": sdp}, "clientManagedHandoffs": False,
            "includeStartupContext": False, "prompt": voice_prompt(bot_id, brief),
        }, timeout=max(5, left))
        answer = await asyncio.wait_for(call.answer, max(5, START_DEADLINE_SEC - (time.time() - began)))
    except Exception as e:
        calllog.write(tid, "error", stage="start", message=calllog.short(e) or type(e).__name__,
                      secs=round(time.time() - began, 1))
        await stop(tid, reason="the call could not start")
        raise
    calllog.write(tid, "connecting", secs=round(time.time() - began, 2))
    asyncio.create_task(_watchdog(call))
    asyncio.create_task(_speaker(call))
    return call, answer


async def on_event(call: Call, method: str, params: dict):
    call.last_activity = time.time()
    if method == "thread/realtime/sdp":
        if not call.answer.done():
            call.answer.set_result(params.get("sdp") or "")
    elif method == "thread/realtime/error":
        msg = params.get("message") or "The voice connection had a problem."
        log.warning("voice error on %s: %s", call.id, msg)
        calllog.write(call.id, "error", stage="voice", message=calllog.short(msg, 1000))
        if not call.answer.done():
            call.answer.set_exception(RuntimeError(msg))
        await call.events.put({"type": "error", "text": plain_error(msg)})
    elif method == "thread/realtime/closed":
        # Ended from the far side (the phone vanished, the session timed out): still summarized and wrapped up.
        calllog.write(call.id, "closed_by_voice")
        await call.events.put({"type": "closed", "text": ""})
        asyncio.create_task(stop(call.id, reason="the voice ended it"))
    elif method == "thread/realtime/transcript/delta":
        if params.get("role") == "user":
            call.last_user_delta = time.time()
            call.last_user_sound = time.time()
            call.hearing += params.get("delta") or ""
            context_soon(call)
        else:
            call.last_voice_sound = time.time()
            _voice_sounded(call, "transcript")
        await call.events.put({"type": "partial", "role": params.get("role"), "text": params.get("delta") or ""})
    elif method == "thread/realtime/transcript/done":
        role, text = params.get("role"), (params.get("text") or "").strip()
        if text:
            # The person's lines carry the person's name; the voice's lines are "You" (person_lines reads them).
            call.transcript.append((who.first() if role == "user" else "You", text))
            # His final words can be recognized after the voice has already begun answering; that is not a new wait.
            if role == "user" and ("mic" in call.wait_since or time.time() - call.last_voice_sound > 1.0):
                call.wait_since["words"] = time.time()
            bot = config.BOTS_BY_ID.get(call.bot_id)
            calllog.write(call.id, "line", who=who.first() if role == "user" else (bot.name if bot else "Zero"),
                          text=calllog.short(text, calllog.TEXT_LIMIT))
            if role == "user":
                heard_line(call, text)
                line_context(call, text)
            else:
                voice_line(call, text)
        await call.events.put({"type": "said", "role": role, "text": text})
    elif method.endswith("outputAudio/delta"):
        call.last_voice_sound = time.time()
        _voice_sounded(call, "audio")
    elif method not in call.seen_methods and len(call.seen_methods) < 40:
        call.seen_methods.add(method)    # a realtime event this hub does not use yet: worth knowing it exists
        calllog.write(call.id, "other_event", method=method[:80])


# MARK: Quiet context while the person talks (facts.call_context; docs/qa/2026-10-10-memory-facts.md)

CONTEXT_ON = False          # set by the running hub (app.lifespan): a test never reads a live home's chats
CONTEXT_MIN_WORDS = 4
# Haiku's closer look after a line where the person thinks something through: measured p50 6.2 s at low effort and
# 7.2 s at medium through the claude CLI (Oct 10, scripts/call_context_bench.py), so it informs the next exchange, never
# the first answer, which the quick search covers (p50 1.2 ms, landing while the person still talks). On by default;
# the home setting "call_refine" set to false turns it off.
REFINE_MIN_WORDS = 10
REFINE_SETTING = "call_refine"
REFINE_PER_CALL = 3
_THINKING = re.compile(r"\b(thinking about|not sure|should i|should we|wondering|worried|torn|deciding|decide|"
                       r"can't decide|what do you think|on the fence|considering|weigh|trade-?off|pros and cons|"
                       r"get in the way|afford)\b", re.I)


def context_soon(call: Call) -> None:
    """A context search on the words heard so far, on every new word: the voice starts answering about 0.1 s after
    the person's last word and the whole line's transcript arrives after that (Oct 10 probe), so a note only helps the
    first answer when it lands while the person is still talking. A search takes milliseconds; while one runs, the
    next waits for it and then runs once on everything heard by then."""
    if not CONTEXT_ON or call.engine != ENGINE or call.closed:
        return
    if call.ctx_task and not call.ctx_task.done():
        call.ctx_again = True
        return
    try:
        call.ctx_task = asyncio.get_running_loop().create_task(_context_now(call))
    except RuntimeError:
        pass


async def _context_now(call: Call) -> None:
    while True:
        call.ctx_again = False
        await push_context(call, call.hearing, final=False)
        if not call.ctx_again or call.closed:
            return


def line_context(call: Call, text: str) -> None:
    """The person's whole line: one last search on all of it, and Haiku's closer look when the home has it on."""
    call.hearing = ""
    if not CONTEXT_ON or call.engine != ENGINE or call.closed:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(push_context(call, text, final=True))
    if store.get_setting(REFINE_SETTING, True) is not False and len(text.split()) >= REFINE_MIN_WORDS and \
            _THINKING.search(text) and call.refines < REFINE_PER_CALL:
        call.refines += 1
        loop.create_task(_refine(call, text))


async def push_context(call: Call, text: str, final: bool) -> bool:
    """Gives the voice what the team knows about what the person is saying, as quiet context (Realtime v3
    `session.context.append` through Codex's appendText with the developer role: it informs the next answer and
    starts none). Logged with how long after the person's last words it landed."""
    from . import facts
    text = " ".join((text or "").split())
    if call.closed or len(text.split()) < CONTEXT_MIN_WORDS or text == call.ctx_searched:
        return False
    call.ctx_searched = text
    began = time.time()
    try:
        note, keys = await asyncio.to_thread(facts.call_context, text, call.bot_id, call.ctx_pushed)
    except Exception:
        log.warning("call context search failed", exc_info=True)
        return False
    found_ms = int((time.time() - began) * 1000)
    if not note:
        return False
    call.ctx_pushed.update(keys)
    try:
        await server.call("thread/realtime/appendText", {"threadId": call.id, "text": note, "role": "developer"},
                          timeout=5)
    except Exception as e:
        log.warning("call context was not taken: %s", e)
        calllog.write(call.id, "error", stage="context", message=calllog.short(e))
        return False
    now = time.time()
    calllog.write(call.id, "context", via="search", final=final, items=len(keys), chars=len(note), search_ms=found_ms,
                  ms_after_words=int((now - call.last_user_delta) * 1000) if call.last_user_delta else None,
                  talking=_colin_is_talking(call) or None)
    return True


async def _refine(call: Call, text: str) -> None:
    from . import facts
    began = time.time()
    try:
        note = await asyncio.wait_for(facts.call_refine(text, call.bot_id), 20)
    except Exception as e:
        calllog.write(call.id, "context", via="haiku", error=calllog.short(e), secs=round(time.time() - began, 1))
        return
    secs = round(time.time() - began, 2)
    if not note or call.closed:
        calllog.write(call.id, "context", via="haiku", items=0, secs=secs)
        return
    try:
        await server.call("thread/realtime/appendText", {"threadId": call.id, "text": note, "role": "developer"},
                          timeout=5)
    except Exception as e:
        calllog.write(call.id, "error", stage="context", message=calllog.short(e))
        return
    calllog.write(call.id, "context", via="haiku", chars=len(note), secs=secs)


def heard_line(call: Call, text: str) -> None:
    """The person's line was recognized: watch that it gets an answer (#30), and note a goodbye."""
    now = time.time()
    call.last_user_line_at, call.last_user_line = now, text
    call.goodbye_at = now if is_goodbye(text) else 0.0
    call.hanging_up = False
    call.watch_seq += 1
    if call.engine == ENGINE:
        try:
            asyncio.get_running_loop().create_task(_answer_watch(call, call.watch_seq))
        except RuntimeError:
            pass


def voice_line(call: Call, text: str) -> None:
    """The voice finished a line. After the person's goodbye, a goodbye back ends the call."""
    call.last_voice_line_at = time.time()
    if call.goodbye_at and is_farewell(text) and call.last_tool_at < call.goodbye_at and not call.hanging_up:
        try:
            asyncio.get_running_loop().create_task(hang_up_after_goodbye(call))
        except RuntimeError:
            pass


def _answered(call: Call, since: float) -> bool:
    """Something came back after the person's line: a whole line from the voice, a hand-off, or sound just now."""
    return (call.last_voice_line_at > since or call.last_tool_at > since - 1.0 or call.tools_running > 0
            or time.time() - call.last_voice_sound < 2.0 or bool(call.speech))


async def _answer_watch(call: Call, seq: int) -> None:
    """A line the person said got no answer at all (#30): say it to the voice again as their words, and if that
    still gets nothing, ask plainly for it again. Only lines the phone's microphone heard the person say count, so a
    TV or someone across the room is not chased (#26)."""
    since = call.last_user_line_at
    for stage in ("again", "ask"):
        await asyncio.sleep(MISSED_TURN_SEC)
        if seq != call.watch_seq or call.closed or call.away or call.goodbye_at:
            return
        if _answered(call, since) or _colin_is_talking(call):
            return
        if call.mic_heard_at < since - 8 or len(call.last_user_line.split()) < 2:
            return
        calllog.write(call.id, "missed_turn", stage=stage, text=calllog.short(call.last_user_line))
        try:
            if stage == "again":
                await server.call("thread/realtime/appendText",
                                  {"threadId": call.id, "text": call.last_user_line[:2000], "role": "user"}, timeout=10)
            else:
                await speak(call, "Sorry, I missed that. Can you say it again?")
        except Exception as e:
            log.warning("missed-turn recovery failed: %s", e)
            calllog.write(call.id, "error", stage="missed_turn", message=calllog.short(e))
            return


async def hang_up_after_goodbye(call: Call) -> None:
    """The person said goodbye and the voice said it back: the phone ends the call once the voice is done
    speaking (it knows when its sound stops). Anything the person says first keeps the call going."""
    said_at = call.goodbye_at
    call.hanging_up = True
    await asyncio.sleep(0.3)
    if call.closed or call.goodbye_at != said_at:
        call.hanging_up = False
        return
    calllog.write(call.id, "goodbye", note="The person said goodbye; the phone ends the call.")
    await call.events.put({"type": "hangup"})


def _voice_sounded(call: Call, via: str) -> None:
    """The voice made its first sound since Colin finished (or since the hello was asked for): saves how long
    he waited, from the end of his speech by the phone's microphone and from his final recognized words."""
    if not call.wait_since:
        return
    now = time.time()
    waits = {f"ms_after_{cause}": int((now - since) * 1000) for cause, since in call.wait_since.items()}
    call.wait_since = {}
    calllog.write(call.id, "first_reply", via=via, **waits)


# MARK: The relay's tools

async def ask_bot(call: Call, request: str) -> str:
    if not request:
        return "There was no request."
    # What comes back here is said by the voice, and the work is the voice's own, so it is worded as "I". Oct 11 call
    # review: the voice said "It checked eLearning" about its own work.
    running = call.job if call.job and not call.job.done else (
        call.pending_turn if call.pending_turn and not call.pending_turn.done else None)
    if running is not None:
        # The bot runs one turn at a time; a question during this call's own work rides along as a steer.
        _add_words(call)
        if await turns.steer(call.bot_id, f"{who.first()} also asks, on the call: " + request +
                             " Answer it in one short note now, then carry on."):
            call.awaiting_answer = True
            if running is call.pending_turn and call.job is None:
                call.job, call.job_task = running, request   # its answers now come through the speech queue
                asyncio.create_task(_follow_job(call, running, skip=len(running.events)))
            return "I'm still working on your earlier request too, and I'll answer this in a moment."
    turn = None
    # Only Colin's own words from the call count as his go-ahead for sign-ins, not the relay's version. Taken
    # once, before any retry, so a busy bot does not lose them.
    own_words = _colin_since_last(call)
    for _ in range(20):
        try:
            turn = turns.start(call.bot_id, job_guide() + request, consent_text=own_words)
            break
        except turns.Busy:
            await asyncio.sleep(1)
    if turn is None:
        return "I'm finishing a reply in our chat first. Ask me again in a moment."
    await call.events.put({"type": "status", "text": "Working on it"})
    task = task_number(call, turn)
    await cue(call, "started", task)
    asyncio.create_task(_working_cues(call, turn, task))
    call.pending_turn = turn
    try:
        reply, status = await _wait_for(call, turn, QUICK_ANSWER_SEC)
    finally:
        if call.pending_turn is turn:
            call.pending_turn = None
    if call.job is turn:
        return ("A question came in meanwhile, so I'm now working on this as the call's job. I'll say the answers as "
                "they come.")
    if reply is not None:
        if call.closed:   # he hung up while it was answering; it still reaches him
            await _after_hang_up(call, reply)
            return "Done."
        await call.events.put({"type": "reply", "text": reply})
        await cue(call, "done", task)
        return brief_for(call, reply) or "Done."
    # Still working: this is now the call's live job.
    call.job, call.job_task = turn, request
    calllog.write(call.id, "job_started", request=calllog.short(request))
    asyncio.create_task(_follow_job(call, turn, skip=len(turn.events)))
    progress = f" So far: {status}." if status else ""
    return (f"I started on this and I'm still working on it: {request}.{progress} I'll say what I find as I go. "
            "Send any further details for it with update_job.")


def _colin_since_last(call: Call) -> str:
    """What Colin said on the call since the last handoff, in his own words."""
    said = person_lines(call.transcript[call.heard_upto:])
    call.heard_upto = len(call.transcript)
    return " ".join(said)[-2000:]


def _add_words(call: Call) -> None:
    """What Colin just said on the call joins the running job's go-ahead for sign-ins, in his own words."""
    words = _colin_since_last(call)
    if not words.strip():
        return
    try:
        from . import consent
        sid = store.session_for(call.bot_id)
        if sid:
            consent.open_pass("message", words, bot_id=call.bot_id, session_id=sid)
    except Exception:
        log.warning("could not add the person's words to the job's go-ahead", exc_info=True)


async def update_job(call: Call, note: str) -> str:
    if not note:
        return "There was nothing to pass on."
    if call.job and not call.job.done:
        _add_words(call)
    if call.job and not call.job.done and await turns.steer(call.bot_id, f"Update from {who.first()} on the call: " + note):
        await call.events.put({"type": "status", "text": "Passed on"})
        return "I passed that on, and I'll pick it up after my current step."
    return await ask_bot(call, note)


async def _wait_for(call: Call, turn: turns.Turn, seconds: float) -> tuple[str | None, str | None]:
    """Waits for a turn's reply up to `seconds`. Returns (reply or None if still running, latest status)."""
    reply, status = None, None
    deadline = time.time() + seconds
    follow = turn.follow()
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None, status
        try:
            ev = await asyncio.wait_for(follow.__anext__(), remaining)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return (reply, status) if turn.done else (None, status)
        if ev is None:
            continue
        if ev["type"] in ("status", "commentary") and ev.get("text"):
            status = ev["text"]
            await call.events.put({"type": "status", "text": ev["text"]})
        elif ev["type"] == "reply":
            reply = ev.get("text")
        elif ev["type"] == "error":
            reply = reply or f"I hit a problem and couldn't finish. Offer {who.first()} to try again."
        elif ev["type"] == "done":
            return reply or "Done.", status


async def _follow_job(call: Call, turn: turns.Turn, skip: int = 0):
    """Speaks a live job's notes and questions at pauses, and its result when it ends. A job that is still
    running when Colin hangs up keeps going; its result then comes to him as a notification. A job that ends
    without any reply still says so, by voice or in the chat: a "let me check" never just stops (#23)."""
    seen, replied, failed = 0, any(e.get("type") == "reply" for e in turn.events[:skip]), False
    task = task_number(call, turn)
    try:
        async for ev in turn.follow():
            if ev is None:
                continue
            seen += 1
            if seen <= skip:
                continue   # already reported while waiting for a quick answer
            if ev["type"] == "reply" and ev.get("text"):
                replied = True
            elif ev["type"] == "error":
                failed = True
            if call.closed:
                if ev["type"] == "reply" and ev.get("text"):
                    await _after_hang_up(call, ev["text"])
                continue
            if ev["type"] == "commentary" and ev.get("text"):
                text = speakable(ev["text"], SPOKEN_LIMIT)
                # A question for Colin, or the answer to his question, is always said, whole and with the sentences
                # around it; other notes are progress. Oct 11 call review: a note like "Added the chips. Question:
                # salsa too?" counted as progress, so its question could be cut, dropped for a newer note, or held.
                must = has_question(text) or call.awaiting_answer
                call.awaiting_answer = False
                say(call, unlabel(text) if must else brief(text, chat_line=False), must)
            elif ev["type"] == "status" and ev.get("text"):
                await call.events.put({"type": "status", "text": ev["text"]})
            elif ev["type"] == "reply" and ev.get("text"):
                calllog.write(call.id, "job_result", text=calllog.short(ev["text"], 1000))
                await call.events.put({"type": "reply", "text": ev["text"]})
                say(call, brief_for(call, ev["text"]), must=True, final=True, done_cue=task)
        if not replied:
            calllog.write(call.id, "job_no_result", failed=failed or None)
            if call.closed:
                await _after_hang_up(call, f"I couldn't finish what you asked on the call ({turn_label(call)}). "
                                           "Ask me again when you're ready.", title="From your call")
            else:
                say(call, "That one hit a problem and didn't finish. Want me to try again?", must=True, final=True,
                    done_cue=task)
    finally:
        if call.job is turn:
            call.job = None


def turn_label(call: Call) -> str:
    words = (call.job_task or "your request").split()
    return " ".join(words[:12]) + ("..." if len(words) > 12 else "")


def task_number(call: Call, turn: turns.Turn) -> int:
    """The hand-off's number in this call, so the phone can tell one task's sounds from another's."""
    if turn.id not in call.tasks:
        call.tasks[turn.id] = len(call.tasks) + 1
    return call.tasks[turn.id]


async def cue(call: Call, kind: str, task: int) -> None:
    """A soft sound on the phone (#29): `started` when a hand-off begins, `working` while it runs, `done` just
    before its result is said. The phone plays it only into silence and drops it otherwise, so it never covers
    speech; `done` sounds only for a task whose start or progress was heard."""
    if call.closed or call.away or kind not in CUES:
        return
    if kind == "working" and task in call.tasks_done:
        return   # the result is about to be said (the speaker's CUE_LEAD_SEC gap): never "working" after "done"
    if kind == "done":
        call.tasks_done.add(task)
    calllog.write(call.id, "cue", cue=kind, task=task)
    await call.events.put({"type": "cue", "text": kind, "id": task})


async def _working_cues(call: Call, turn: turns.Turn, task: int) -> None:
    """"Still working" sounds, only after a real delay and only into a quiet stretch."""
    await asyncio.sleep(WORKING_FIRST_SEC)
    sent = 0
    while not call.closed and not turn.done and sent < WORKING_MAX:
        quiet = time.time() - max(call.last_voice_sound, call.last_user_sound)
        if quiet >= 3.0 and not call.away and not _colin_is_talking(call) and not call.speech:
            await cue(call, "working", task)
            sent += 1
            await asyncio.sleep(WORKING_EVERY_SEC)
        else:
            await asyncio.sleep(1.0)


def work_status(call: Call) -> str:
    """What the assistant and the team are working on, for "what are you working on?" (#20): a short spoken
    answer, with the whole list in the chat when it is long."""
    from . import board
    lead = config.BOTS_BY_ID.get(call.bot_id)
    lines, spoken = [], []
    if call.job and not call.job.done:
        spoken.append(f"On this call I'm still on {turn_label(call)}.")
    busy = [config.BOTS_BY_ID[b].name for b in turns.busy_bots()
            if b in config.BOTS_BY_ID and not (b == call.bot_id and call.job)]
    try:
        groups = board.grouped()
    except Exception:
        groups = {}

    def title(t: dict) -> str:
        words = str(t.get("title") or "a task").split()
        return " ".join(words[:8]) + ("..." if len(words) > 8 else "")

    working = [t for t in groups.get("working") or [] if t.get("status") == "running"] or \
        list(groups.get("working") or [])
    waiting = list(groups.get("needs_you") or [])
    today = time.time() - 12 * 3600
    finished = [t for t in groups.get("done") or [] if (t.get("completed_at") or 0) >= today]
    if working:
        names = [title(t) for t in working[:2]]
        more = len(working) - len(names)
        lead_in = "One thing is" if len(working) == 1 else f"{len(working)} things are"
        spoken.append(f"{lead_in} in progress: " + " and ".join(names) + (f", and {more} more." if more else "."))
    elif busy:
        spoken.append(" and ".join(busy[:2]) + (" is" if len(busy) == 1 else " are") + " working on something now.")
    if waiting:
        spoken.append(("One thing is" if len(waiting) == 1 else f"{len(waiting)} are") +
                      f" waiting on you: {title(waiting[0])}.")
    if finished:
        spoken.append(f"{len(finished)} finished today." if len(finished) > 1 else "One finished today.")
    if not spoken:
        return "Nothing is running right now, and nothing is waiting on you."
    lines = ([f"In progress: {t.get('title')}" for t in working] + [f"Waiting on you: {t.get('title')}" for t in waiting]
             + [f"Finished today: {t.get('title')}" for t in finished])
    text = " ".join(spoken)
    if len(working) > 2 or len(waiting) > 1 or len(finished) > 1:
        try:
            store.add_feed("checkin", call.bot_id, None, "Where things stand", "\n".join(f"- {l}" for l in lines)[:4000])
            if not call.details_said:     # at most once per call (Oct 11 call review)
                text += f" {DETAILS_IN_CHAT}"
                call.details_said = True
        except Exception:
            log.warning("could not put the status list in the chat", exc_info=True)
    calllog.write(call.id, "work_status", text=calllog.short(text), lead=lead.name if lead else None)
    return text


async def _after_hang_up(call: Call, reply: str, title: str = "Finished what you asked on the call"):
    from . import watcher
    name = config.BOTS_BY_ID[call.bot_id].name
    store.add_feed("checkin", call.bot_id, None, title, reply[:4000])
    calllog.write(call.id, "after_call_result", title=title, text=calllog.short(reply, 1000))
    try:
        await watcher.push(title, reply, call.bot_id, urgent=True, kind="done")   # asked for on the call
    except Exception:
        log.warning("could not send the after-call result", exc_info=True)
    log.info("%s finished a call job after hang-up", name)


def say(call: Call, text: str, must: bool = False, final: bool = False, tag: str = "", done_cue: int = 0):
    """Queues news from background work. Everything is said in order, at a pause. Of several progress
    notes waiting, only the newest is said; questions, answers and the final result are always said,
    and the final result replaces any progress still waiting. `tag` lets a cue be withdrawn (unsay).
    `done_cue`: the task whose "done" sound plays just before this is said."""
    if not text or call.closed:
        return
    if final:
        call.speech = [item for item in call.speech if item[1]]
    call.speech.append((text, must, tag, done_cue))
    call.speech_ready.set()


def unsay(call: Call, tag: str) -> None:
    """Withdraws cues that are no longer true, such as "press Allow" once Colin has answered."""
    call.speech = [item for item in call.speech if item[2] != tag]


async def _speaker(call: Call):
    try:
        while not call.closed:
            await call.speech_ready.wait()
            call.speech_ready.clear()
            while call.speech and not call.closed:
                item = call.speech[0]
                text, must, _, done_cue = item
                if not must:
                    if any(not it[1] for it in call.speech[1:]):
                        call.speech.pop(0)       # a newer progress note supersedes this one
                        continue
                    if time.time() - call.last_progress < PROGRESS_GAP_SEC:
                        if any(it[1] for it in call.speech[1:]):
                            call.speech.append(call.speech.pop(0))   # a question goes ahead of waiting progress
                            continue
                        await asyncio.sleep(0.5)
                        continue
                ready = await _wait_for_pause(call, must)
                if item not in call.speech:
                    continue                 # withdrawn while waiting
                call.speech.remove(item)
                if not ready:
                    continue                 # stale progress is not worth interrupting for
                if not must:
                    call.last_progress = time.time()
                if done_cue:
                    await cue(call, "done", done_cue)
                    await asyncio.sleep(CUE_LEAD_SEC)
                said_at = time.time()
                calllog.write(call.id, "news", text=calllog.short(text), must=must)
                if await speak(call, text) is False and must:
                    # The voice could not take it: it still reaches the person, in the chat (#23).
                    asyncio.create_task(_after_hang_up(call, text, title="From your call"))
                # Wait for the voice to start saying it (up to 5 s), so the next item cannot talk over it.
                while not call.closed and call.last_voice_sound < said_at and time.time() - said_at < 5:
                    await asyncio.sleep(0.2)
    except Exception:
        log.exception("the speech queue for call %s failed", call.id)


def _colin_is_talking(call: Call) -> bool:
    """The phone's word, but only while it is fresh (it repeats every 2 s) and backed by recognized words:
    road noise or a lost update must not hold news back for the rest of the call."""
    if call.away:
        return True
    now = time.time()
    if not call.colin_talking or now - call.talking_at > 3:
        return False
    return now - call.last_user_delta < 4 or now - call.talking_since < 2


async def _wait_for_pause(call: Call, must: bool) -> bool:
    """Waits until neither Colin nor the voice is talking. After 10 s a question or result takes the next
    short gap in the voice instead. False when a progress note waited too long."""
    waited = 0.0
    while not call.closed:
        voice_quiet = time.time() - call.last_voice_sound
        quiet_for = time.time() - max(call.last_user_delta, call.last_voice_sound)
        if not _colin_is_talking(call) and quiet_for > 1.2:
            return True
        if must and waited > 10 and voice_quiet > 0.8 and not call.away:
            return True
        await asyncio.sleep(0.3)
        waited += 0.3
        if waited > 30 and not must:
            return False
    return False


def set_away(call_id: str, away: bool):
    """The call is on hold on the phone, or back."""
    call = calls.get(call_id)
    if call:
        if call.away != away:
            calllog.write(call.id, "hold", on=away)
        call.away = away
        if not away:
            call.speech_ready.set()   # anything held is said at the next pause


def set_talking(call_id: str, talking: bool):
    """The phone's microphone level: Colin started or stopped speaking."""
    call = calls.get(call_id)
    if call:
        now = time.time()
        if talking and not call.colin_talking:
            call.talking_since = now
            call.wait_since = {}
            voice_was_on = now - call.last_voice_sound < 0.6
            calllog.write(call.id, "colin_started", voice_talking=voice_was_on or None)
            if voice_was_on:
                calllog.write(call.id, "interrupted", note="The person started talking while the voice was speaking.")
        elif not talking and call.colin_talking:
            call.wait_since["mic"] = now
            calllog.write(call.id, "colin_stopped", talked_secs=round(now - call.talking_since, 1))
        call.colin_talking = talking
        call.talking_at = time.time()
        call.last_user_sound = time.time()
        if talking:
            call.mic_heard_at = now


async def speak(call: Call, text: str) -> bool:
    """Has the voice say `text`. False when it could not (tried twice), so the caller can put it in the chat."""
    text = unlabel(text)      # "Question:" and the like are never read out (Oct 11 call review)
    if not text or call.closed:
        return True
    if call.engine != ENGINE:   # an on-device call: the phone asks for it at its next pause (ondevice.py)
        from . import ondevice
        await ondevice.news(call, text)
        return True
    for attempt in range(2):
        try:
            await server.call("thread/realtime/appendSpeech", {"threadId": call.id, "text": text[:4000]})
            return True
        except Exception as e:
            log.warning("appendSpeech failed: %s", e)
            if attempt == 0 and not call.closed:
                await asyncio.sleep(0.8)
    calllog.write(call.id, "error", stage="speak", message="The voice did not take the words; they went to the chat.")
    return False


async def greet(call_id: str, waited_ms: int | None = None):
    """Opens the call once the phone's audio is connected, so the greeting is not spoken into silence.
    `waited_ms`: how long the person waited from tapping Call; a slow connection gets an honest hello (#18)."""
    call = calls.get(call_id)
    if not call or call.greeted or call.closed:
        return
    call.greeted = True
    call.greeted_at = time.time()
    call.wait_since = {"greeting": call.greeted_at}
    late = isinstance(waited_ms, int) and waited_ms >= LATE_GREETING_MS
    calllog.write(call.id, "greeting", secs_since_start=round(call.greeted_at - call.started, 2),
                  waited_ms=waited_ms if isinstance(waited_ms, int) else None, late=late or None)
    if call.engine != ENGINE:   # the phone already played its own greeting
        from . import ondevice
        ondevice.greeted(call, late=late)
        return
    await speak(call, greeting(late=late))


async def stop(call_id: str, reason: str = ""):
    call = calls.pop(call_id, None)
    server.listeners.pop(call_id, None)
    if call:
        call.closed = True
        now = time.time()
        calllog.write(call.id, "end", reason=reason or None,
                      duration=int(now - call.greeted_at) if call.greeted_at else 0,
                      total_secs=round(now - call.started, 1), lines=len(call.transcript),
                      colin_lines=len(person_lines(call.transcript)))
        call.speech_ready.set()   # lets the speech queue end
        # Answers and results that were waiting to be said still reach him, in the chat and as a notification.
        unsaid = [text for text, must, tag, _ in call.speech if must and not tag]
        call.speech = []
        await call.events.put({"type": "closed", "text": ""})
        if unsaid:
            asyncio.create_task(_after_hang_up(call, "\n\n".join(unsaid), title="Left over from your call"))
        asyncio.create_task(_wrap_up(call))
        if call.engine != ENGINE:   # nothing of it runs in Codex
            from . import ondevice
            ondevice.closed(call)
            return
    if server.proc is None or server.proc.returncode is not None:
        return   # Codex already exited; stopping would only start it again
    try:
        await server.call("thread/realtime/stop", {"threadId": call_id}, timeout=10)
    except Exception:
        pass
    if not calls:
        server.idle_soon()


SUMMARY_PROMPT = """You are {name}, {person}'s assistant. Write the note that sits on this call in your chat so \
{person} can see at a glance what came of it. Speak to {person} directly: "I" is you ({name}) and "you" is {person}.

Write the outcome, not a replay. Never narrate who said what or in what order ("You asked me...", \
"I told you...", "Then you said..."). Group by topic and state where each one landed now.

Format exactly:
- First line: one short sentence with the gist of the call.
- Then a blank line and at most four bullets, each "- **Topic:** where it stands", under 15 words, plain \
present-tense facts. Example: "- **Job application:** You applied this morning; waiting to hear back."
- Last, only if something needs doing: "- **Next:** the one thing, and who does it."
Skip greetings and small talk entirely. No other headings or formatting.

{transcript}"""


async def _record(call: Call):
    """Puts the call in the bot's chat as one entry: its length and a Haiku summary. A call that never
    got as far as hello (cancelled or failed while ringing) leaves nothing behind."""
    if not call.greeted and not call.transcript:
        return
    duration = int(time.time() - (call.greeted_at or call.started))   # from hello, like a phone call
    summary = ""
    if call.transcript:
        lines = "\n".join(f"{who.first() if speaker != 'You' else 'Assistant'}: {t}"
                           for speaker, t in call.transcript)[-12000:]
        try:
            summary = await briefing.ask_haiku(SUMMARY_PROMPT.format(name=config.BOTS_BY_ID[call.bot_id].name,
                                                                     person=who.first(), transcript=lines), timeout=60)
        except Exception:
            log.warning("call summary failed", exc_info=True)
    if not summary:
        summary = "Nothing was said on this call." if not call.transcript else "The summary could not be made."
    calllog.write(call.id, "summary", text=summary[:4000])
    # Where the call started, so what the Zip sent during it sits under the call (Oct 10: Clients's result arrived
    # mid-call but showed above a call bubble placed at its end, which read as coming after the call).
    store.add_feed("call", call.bot_id, None, "Voice call", json.dumps({"duration": duration, "summary": summary}),
                   at=time.time() - (duration or 0))
    try:   # the questions still waiting go under the call, as cards, and a next step can become a follow-up question
        from . import notices
        notices.after_call(call.bot_id)
    except Exception:
        log.warning("could not move open questions under the call", exc_info=True)


async def _wrap_up(call: Call):
    """After the call: its entry in the chat, then the transcript to the bot once, to remember what matters."""
    await _record(call)
    said = person_lines(call.transcript)
    try:   # facts in the person's own words on the call (extract.py), whatever the Zip saves from the transcript
        from . import extract
        asyncio.create_task(extract.after_call(call.bot_id, said, call.id))
    except Exception:
        log.warning("could not start the fact extractor for the call", exc_info=True)
    if len(said) < 2 or time.time() - call.started < 30:
        return
    lines = "\n".join(f"{speaker}: {t}" for speaker, t in call.transcript)[-8000:]
    first = who.first()
    text = (f"[Voice call ended] This is the transcript of the call {first} just had with your voice. Save facts "
            f"about {who.possessive()} life the whole team should know (decisions, plans, changes, preferences, "
            f"brands, stores, people) with save_fact, and "
            f"what only your work needs (preferences for how you help {first}, promises you made) to memory. Note "
            "open follow-ups. Reply with one short line.\n\n" + lines)
    try:
        await turns.run_internal(call.bot_id, text)
    except Exception:
        log.warning("call wrap-up failed", exc_info=True)
    briefing.refresh_soon(call.bot_id)   # a call straight after this one knows what was said


async def _watchdog(call: Call):
    while not call.closed:
        await asyncio.sleep(30)
        if time.time() - call.last_activity > IDLE_LIMIT_SEC:
            log.info("ending idle voice call %s", call.id)
            await stop(call.id, reason=f"idle for {IDLE_LIMIT_SEC // 60} minutes")
