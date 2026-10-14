"""What each message in a bot's conversation is, decided once for everyone: the app's chat, the home
list preview, the call voice's briefing and the Mac quote check all read the same answer.

A conversation holds the person's messages and the bot's replies, plus turns the hub starts itself: board
updates, notes after a sign-in answer, a call's requests and the after-call wrap-up. Every assistant
message up to the next user message belongs to the turn that user message started.
"""

import re

from . import chatter

REACTIONS_PREFIX = "[Reactions from "
STOPPED_PREFIX = "[Stopped by "
CALL_REQUEST_MARKERS = ("Request from the call: ", "Colin's request: ")
VOICE_TAG = "[Voice call] "

REPLY_PREFIX = "[Replying to "
REPLY_EXCERPT_MAX = 300
_REPLY_WHO = {"bot": "your message", "other": "an earlier update in this chat"}
_REPLY_LINE = re.compile(r'^\[Replying to (your message|an earlier update in this chat|[^\n"]{1,60}?\'s earlier message): '
                         r'"(.*)"\]$')

_KINDS = [
    ("[Board update", "board_update", "board_reply"),
    ("[kanban]", "board_update", "board_reply"),
    ("[Hub note", "hub_note", "bot"),
    ("[Voice call ended", "wrap_up", "wrap_up_reply"),
    ("[From a voice call]", "call_turn", "call_reply"),
    ("[From ", "team", "team_reply"),
]

VISIBLE = {"colin", "bot", "board_reply", "call_reply"}


# Hermes's wrapper around words that reach a running turn (turns.steer), stored as a user message. In the chat it
# showed as "[OUT-OF-BAND USER MESSAGE — a direct message from the user, ...]" (Oct 10); only the words inside count.
STEER_OPEN, STEER_CLOSE = "[OUT-OF-BAND USER MESSAGE", "[/OUT-OF-BAND USER MESSAGE]"
CALL_UPDATE = re.compile(r"^Update from [^\n]{1,60}? on the call: ")


def unwrap_steer(text: str) -> str:
    """The words inside Hermes's out-of-band wrapper, or the text unchanged."""
    t = text.strip()
    if not t.startswith(STEER_OPEN):
        return text
    body = t.split("]", 1)[1] if "]" in t else t
    return body.split(STEER_CLOSE, 1)[0].strip()


def reply_excerpt(text: str) -> str:
    """The quoted message on one line, cut at a word near REPLY_EXCERPT_MAX characters."""
    t = " ".join(str(text or "").split())
    if len(t) <= REPLY_EXCERPT_MAX:
        return t
    cut = t[:REPLY_EXCERPT_MAX].rsplit(" ", 1)[0] or t[:REPLY_EXCERPT_MAX]
    return cut.rstrip(" .,;:") + "..."


def reply_line(excerpt: str, who: str, name: str) -> str:
    """The first line of a reply's turn. `who` is "bot" (the Zip's message), "person" (their own earlier message) or
    "other" (a team update, a call or a note); `name` is the person's first name."""
    whose = _REPLY_WHO.get(who) or f"{' '.join(str(name or 'the person').split())[:60]}'s earlier message"
    return f'{REPLY_PREFIX}{whose}: "{reply_excerpt(excerpt)}"]'


def parse_reply_line(line: str) -> dict | None:
    """{"text", "who"} for a reply line, or None when the line is not one."""
    m = _REPLY_LINE.match(line or "")
    if not m or not m.group(2).strip():
        return None
    whose = m.group(1)
    who = "bot" if whose == "your message" else "other" if whose == _REPLY_WHO["other"] else "person"
    return {"text": m.group(2), "who": who}


def peel(text: str) -> tuple[str, dict | None]:
    """The person's words without the quiet notes the hub adds in front of them, and the message they replied to."""
    text = unwrap_steer(text)
    reply = None
    while "\n" in text:
        first, rest = text.split("\n", 1)
        if first.startswith((REACTIONS_PREFIX, STOPPED_PREFIX)):
            text = rest
            continue
        quoted = parse_reply_line(first) if reply is None and first.startswith(REPLY_PREFIX) else None
        if quoted is None:
            break
        reply, text = quoted, rest
    return text.removeprefix(VOICE_TAG), reply


def strip(text: str) -> str:
    """The message without the quiet notes the hub adds in front of Colin's words."""
    return peel(text)[0]


def classify(msgs: list[dict]) -> list[dict]:
    """Each message with `class` and `display` (its text as Colin should see it)."""
    out = []
    reply_class = "bot"
    for m in msgs:
        text = m.get("text") or ""
        if m.get("role") == "user":
            bare, quoted = peel(text)
            cls, reply_class = "colin", "bot"
            for prefix, kind, reply in _KINDS:
                if bare.startswith(prefix):
                    cls, reply_class = kind, reply
                    break
            display = bare
            if cls == "colin" and CALL_UPDATE.match(bare):
                cls, reply_class, display = "call_turn", "call_reply", CALL_UPDATE.sub("", bare, count=1)
            if cls == "call_turn" and display is bare:
                for marker in CALL_REQUEST_MARKERS:
                    if marker in bare:
                        display = bare.split(marker, 1)[1]
                        break
            item = {**m, "class": cls, "display": display}
            if quoted and cls == "colin":
                item["reply_to"] = quoted
            out.append(item)
        else:
            out.append({**m, "class": _quiet(reply_class, text), "display": text})
    _hide_card_talk(out)
    return out


_HUB_STARTED = {"board_reply", "team_reply", "wrap_up_reply"}


def _quiet(cls: str, text: str) -> str:
    if chatter.empty(text) or chatter.interrupted(text) or (
            cls in _HUB_STARTED and chatter.chatter(text, chatter.team_names())):
        return "quiet"
    return cls


def _words(text: str) -> str:
    """A message's words in lower case, one space apart, for telling whether one message says what another says."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def _hide_card_talk(out: list[dict]) -> None:
    """Colin's live test (Oct 8): Builder wrote a note just before its command put up a card, then a second message
    about the card. The note written beside the call that raised the card (`before_card`, set from the card itself)
    is hidden only when the same turn's later reply already says all of it, word for word ("Waiting for your okay on
    the card." written twice). Anything else stays, whatever words it uses: a real answer written before the call is
    never hidden (Oct 8 QA round 8, L1: hiding by words like "okay", "card" and "confirmed" hid real answers)."""
    for i, m in enumerate(out):
        if not m.get("before_card") or m.get("role") != "assistant" or m["class"] not in VISIBLE:
            continue
        later = []
        for n in out[i + 1:]:
            if n.get("role") == "user":
                break
            if n.get("role") == "assistant" and n["class"] in VISIBLE:
                later.append(_words(n["display"]))
        said = _words(m["display"])
        if later and (said.strip() == "" or any(said in text for text in later)):
            m["class"] = "card_lead"


def visible(msgs: list[dict]) -> list[dict]:
    """The conversation as Colin sees it in the chat (call replies included)."""
    return [m for m in classify(msgs) if m["class"] in VISIBLE]


def colin_words(msgs: list[dict]) -> list[dict]:
    """Only what Colin himself typed or said in the chat."""
    return [m for m in classify(msgs) if m["class"] == "colin"]
