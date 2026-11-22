""







import re

from . import chatter


REACTIONS_PREFIX = "[Reactions from "
STOPPED_PREFIX = "[Stopped by "

CALL_REQUEST_MARKERS = ("Request from the call: ", "Colin's request: ")
VOICE_TAG = "[Voice call] "


SENT_PREFIX = "[Sent "

KEY_HIDDEN_PREFIX = "[Key hidden: "


QUIET_PREFIXES = (REACTIONS_PREFIX, STOPPED_PREFIX, SENT_PREFIX, KEY_HIDDEN_PREFIX, "[Current mail drafts: ",
                  "[Send from the chat: ", "[Your open question cards: ", "[Your open questions to ",
                  "[Your question cards answered lately: ")
_CHAT_TURN_LINE = re.compile(r"\[Chat turn: [a-f0-9]{32}\]")
_SENT_LINE = re.compile(r"\[Sent [A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} at [^\]\n]+\]")







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


    ("[Test pairing ", "tester", "tester_reply"),
]

VISIBLE = {"colin", "bot", "board_reply", "call_reply"}



_HUB_HEAD = re.compile(r"^\[[^\n\]]{1,80}?, not a message from [^\n\]]+\]")




STEER_OPEN, STEER_CLOSE = "[OUT-OF-BAND USER MESSAGE", "[/OUT-OF-BAND USER MESSAGE]"

CALL_UPDATE = re.compile(r"^Update from [^\n]{1,60}? on the call: ")




_HOOK_HEADS = (
    "[Right now, from the team's notes (",
    "[Standing, from the team's notes: ",
    "[Standing, changed since you last saw it: ",
    "[Not loaded this turn: ",
    "Save each lasting preference or decision ",
    "[Your recent chat with ",
    "[Team notes: facts about ",
    "[New team notes since your last turn, recorded by teammates.]",
    "[Relevant to this message, from what ",
    "[Open loops with ",
    "[Current team context from this home's configuration. This replaces any earlier roster. ",
    "[Onboarding, not a message from the person: they just set you up and told you about their ",
    "[Your team requests still waiting on ",
    "[Your own work for the team ",
    "[For this typed chat turn, before your first tool call, write one short first-person sentence saying ",
    "For typed chat, keep useful progress updates while you work. After multiple messages, end with one ",
    "Before anything that takes more than a minute, check in your first steps what it needs that you don't have: ",
    "If a saved-login page fails, use ask_to_sign_in to retry once and then flag the problem for a fix.",
    "On a job with several steps, say one short line in the first person as you start each new step or ",
    "In typed chat, keep useful progress updates while you work. End a multi-message turn with one ",
    "Use report_problem when a tool keeps failing, your reply is rejected by the hub's checks, ",
    "Read public pages with web_search and web_extract first. Open the browser only for signing in, ",
    "For this scheduled check-in, put two or more questions for ",
)
_HOOK_SUFFIX = re.compile(
    r"(?m)^(?:" + "|".join(re.escape(head) for head in _HOOK_HEADS) +
    r"|# How I talk with you[ \t]*$"
    r"|\[[^\n\]]+ dismissed these questions in the app\. Don't raise them again on your own, in this reply "
    r"|\[[^\n\]]+ also talked with teammates in their own chats since your last turn\. Their latest "

    r"|(?<=\n)\[Hub note, not a message from [^\n\]]+\] "
    r"|(?<=\n)\[From [^\n\]]+, a teammate, [^\n\]]+\] )"
)
_SCREENSHOT_LINE = re.compile(r"(?m)^[ \t]*\[screenshot\][ \t]*(?:\n|$)")


def _without_hook_context(text: str) -> str:
    ""
    from . import uploads
    leaked = _HOOK_SUFFIX.search(text)
    if leaked:
        text = text[:leaked.start()].rstrip()
    if _SCREENSHOT_LINE.search(text) and any(a["kind"] == "image" for a in uploads.peel(text)[1]):
        text = _SCREENSHOT_LINE.sub("", text).rstrip()
    return text





TYPED = "Typed in the chat: "
_MARKER_HEADS = ("[hub note", "[chat turn", "[sent ", "[reactions from ", "[stopped by ", "[key hidden",
                 "[replying to ", "[current mail drafts", "[send from the chat", "[your open question cards",
                 "[your open questions to ", "[your question cards answered lately",
                 "[board update", "[kanban]", "[voice call", "[from ", "[test pairing", "[out-of-band user message",
                 "[/out-of-band user message", "[quiet look", "[preparation", "[onboarding", "[right now",
                 "[standing", "[team notes", "[new team notes", "[open loops", "[your recent chat",
                 "[current team context")
_INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff"


def defang(text: str) -> str:
    ""
    from . import who
    heads = (*_MARKER_HEADS, who.tap_prefix().lower())
    out = []
    for line in str(text or "").split("\n"):
        bare = line.lstrip(" \t" + _INVISIBLE).lower()
        out.append(TYPED + line.lstrip(" \t" + _INVISIBLE) if bare.startswith(heads) else line)
    return "\n".join(out)


def unwrap_steer(text: str) -> str:
    ""
    t = text.strip()
    if not t.startswith(STEER_OPEN):
        return text
    body = t.split("]", 1)[1] if "]" in t else t
    return body.split(STEER_CLOSE, 1)[0].strip()


def reply_excerpt(text: str) -> str:
    ""
    t = " ".join(str(text or "").split())
    if len(t) <= REPLY_EXCERPT_MAX:
        return t
    cut = t[:REPLY_EXCERPT_MAX].rsplit(" ", 1)[0] or t[:REPLY_EXCERPT_MAX]
    return cut.rstrip(" .,;:") + "..."


def reply_line(excerpt: str, who: str, name: str) -> str:
    ""

    whose = _REPLY_WHO.get(who) or f"{' '.join(str(name or 'the person').split())[:60]}'s earlier message"
    return f'{REPLY_PREFIX}{whose}: "{reply_excerpt(excerpt)}"]'


def parse_reply_line(line: str) -> dict | None:
    ""
    m = _REPLY_LINE.match(line or "")
    if not m or not m.group(2).strip():
        return None
    whose = m.group(1)
    who = "bot" if whose == "your message" else "other" if whose == _REPLY_WHO["other"] else "person"
    return {"text": m.group(2), "who": who}


def quiet_line(line: str) -> bool:
    ""

    if line.startswith(SENT_PREFIX):
        return _SENT_LINE.fullmatch(line) is not None
    return (line.startswith(QUIET_PREFIXES) and line.endswith("]")) or _CHAT_TURN_LINE.fullmatch(line) is not None


def peel(text: str) -> tuple[str, dict | None]:
    ""

    from . import turnsummary
    text = turnsummary.peel(unwrap_steer(text))[1]
    reply = None
    while "\n" in text:
        first, rest = text.split("\n", 1)
        if first.startswith(QUIET_PREFIXES):
            text = rest
            continue
        quoted = parse_reply_line(first) if reply is None and first.startswith(REPLY_PREFIX) else None
        if quoted is None:
            break
        reply, text = quoted, rest
    kept = [line for line in text.split("\n") if not quiet_line(line)]
    if any(line.strip() for line in kept):
        text = "\n".join(kept)
    return _without_hook_context(text.removeprefix(VOICE_TAG)), reply


def strip(text: str) -> str:
    ""
    return peel(text)[0]


def classify(msgs: list[dict]) -> list[dict]:
    ""
    from . import replycheck
    out = []
    reply_class = "bot"
    hub_turn = False
    for m in msgs:
        text = m.get("text") or ""
        if m.get("role") == "user":
            bare, quoted = peel(text)
            cls, reply_class = "colin", "bot"
            for prefix, kind, reply in _KINDS:
                if bare.startswith(prefix):
                    cls, reply_class = kind, reply
                    break
            else:
                from . import who
                if bare.startswith(who.tap_prefix()) or _HUB_HEAD.match(bare):
                    cls, reply_class = "hub_note", "bot"
            if not bare.startswith(replycheck.NOTE):
                hub_turn = cls in _HUB_TURN_KINDS
            display = bare
            if cls == "colin" and CALL_UPDATE.match(bare):

                cls, reply_class, display = "call_turn", "call_reply", CALL_UPDATE.sub("", bare, count=1)
            if cls == "call_turn" and display is bare:
                for marker in CALL_REQUEST_MARKERS:
                    if marker in bare:
                        display = bare.split(marker, 1)[1]
                        break
            if cls == "colin":
                from . import uploads
                display, attachments = uploads.peel(display)
            else:
                attachments = []
            item = {**m, "class": cls, "display": display}
            if attachments:
                item["attachments"] = attachments
            if quoted and cls == "colin":
                item["reply_to"] = quoted
            out.append(item)
        else:
            step = hub_turn and m.get("step") and not m.get("before_card")
            out.append({**m, "class": "quiet" if step else _quiet(reply_class, text), "display": text})
    _hide_card_talk(out)
    return out





_HUB_STARTED = {"board_reply", "team_reply", "wrap_up_reply"}







_HUB_TURN_KINDS = {"hub_note", "board_update"}


def _quiet(cls: str, text: str) -> str:
    if chatter.empty(text) or chatter.interrupted(text) or (
            cls in _HUB_STARTED and chatter.chatter(text, chatter.team_names())):
        return "quiet"
    return cls


def _words(text: str) -> str:
    ""
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def _hide_card_talk(out: list[dict]) -> None:
    ""




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
    ""
    return [m for m in classify(msgs) if m["class"] in VISIBLE]


def spoken_for(text: str) -> bool:
    ""


    from . import who
    name = re.escape(who.first() or "")
    return bool(name) and bool(re.match(rf"\s*{name} (?:asked|is|wants|needs|said|says|told|would|has|had|just|will|prefers|likes)\b",
                         text or "") or re.search(rf"\b{name} (?:doesn't|does not|won't) need to\b", text or ""))


def colin_words(msgs: list[dict]) -> list[dict]:
    ""
    return [m for m in classify(msgs) if m["class"] == "colin" and not spoken_for(str(m.get("display") or ""))]


def newer(rows: list[dict], after: str) -> list[dict] | None:
    ""



    ids = [str(r.get("id")) for r in rows]
    if after not in ids:
        return None
    i = len(ids) - 1 - ids[::-1].index(after)
    turn = rows[i].get("turn_id")
    while turn and i > 0 and rows[i - 1].get("turn_id") == turn:
        i -= 1
    return rows[i:]
