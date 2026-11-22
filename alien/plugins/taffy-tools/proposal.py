""












import re
import time

from . import consent, home, lineage, tiers

_TURNS_MAX = 500
_turns: dict[str, tuple[float, bool, str]] = {}


_WOULD = r"(?:you\s*would|you['’]?d)"
_ADD = r"(?:add|put|block|book|schedule|create|set\s+up)"
_ASKS = re.compile(
    r"\b(?:what|which\s+\w+|where|when|how)\s+would\s+you\b"
    rf"|(?:^\W*|\b(?:tell|show|give|send|let|walk|run|list|see|hear|know)\b[^.?!]{{0,20}}?\b)what\s+{_WOULD}"
    r"|\bshow\s+me\s+what\b"
    r"|(?<!\bi\s)(?<!i['’]d\s)\b(?:suggest|propose|recommend)\b|\b(?:suggestions?|recommendations?)\b"
    rf"|\bwhat\s+should\s+(?:i|we)\s+{_ADD}\b|\bwhat\s+should\s+go\s+on\b"
    rf"|\bbefore\s+you\s+{_ADD}\b|\b(?:don['’]?t|do\s+not)\s+{_ADD}\b[^.?!]*\byet\b", re.I)
_LEAD = (r"(?:(?:please|then|also|and|so|just|now|ok|okay|yes|yeah|yep|sure|can\s+you|could\s+you|would\s+you|will\s+you|"
         r"go\s+ahead\s+and|you\s+can|i\s+want\s+you\s+to|i\s+need\s+you\s+to|let['’]?s)[\s,]+)*")

_GO = re.compile(rf"^\W*{_LEAD}(?:{_ADD}|pencil|slot|move|reschedule|update|change)\b"
                 r"(?!\s+(?:out\s+)?(?:me\s+)?(?:an?\s+|the\s+|my\s+)?(?:plan|list|draft|outline|summary|proposal)\b)"
                 r"|^\W*(?:yes|yeah|yep|sure|ok|okay|go\s+ahead|do\s+(?:it|that)|sounds\s+good|looks\s+good|approved?)\b",
                 re.I)
_READ_ONLY = re.compile(r"\bread[- ]only\b", re.I)
_CLAUSE = re.compile(r"[.?!;\n]+|,?\s+(?:and|then|but|also)\s+", re.I)
_CARD_ANSWER = re.compile(r'(?:^|\n)About your question ".*?":\s*(.*)$', re.S)

_NOT_HIS = re.compile(r"\[(?:From |Quiet look|Preparation|Voice call ended|News from )|Since your last turn|"
                      r"\[[^\]\n]*not a message from ")


def _his_words(message: str) -> str:
    text = (message or "").lstrip()
    while text.startswith("[") and not _NOT_HIS.match(text) and "\n" in text:
        text = text.split("\n", 1)[1].lstrip()
    if _NOT_HIS.match(text):
        return ""
    answer = _CARD_ANSWER.search(text)
    return (answer[1] if answer else text).removeprefix("[Voice call]").strip()


def asks_to_see(words: str) -> bool:
    ""
    asked = False
    for clause in _CLAUSE.split(words or ""):
        if _ASKS.search(clause):
            asked = True
        elif _GO.search(clause):
            return False
    return asked


def pre_llm_call(session_id: str = "", platform: str = "", user_message=None, **kw):
    ""
    if not session_id or platform == "cron":
        return None
    from . import teammates
    words = _his_words(teammates._message_text(user_message))
    _turns[lineage.root(session_id)] = (time.time(), asks_to_see(words), words)
    while len(_turns) > _TURNS_MAX:
        _turns.pop(next(iter(_turns)))
    return None


def turn_words(session_id: str) -> tuple[float, str]:
    ""
    began, _asks, words = _turns.get(lineage.root(session_id), (0.0, False, ""))
    return began, words


def asked(session_id: str) -> bool:
    ""
    if not session_id:
        return False
    root = lineage.root(session_id)
    began, asks, _words = _turns.get(root, (0.0, False, ""))
    p = consent.live_pass(session_id=root)
    if p is not None and p["kind"] == "message" and p["created"] > began:
        own = dict(p).get("own_words")
        return asks_to_see(_his_words(str(p["text"] or "") if own is None else own))
    return asks


def calendar_write_block(tool_name: str, args: dict, session_id: str) -> dict | None:
    ""
    if tool_name == "calendar":
        writes = str(args.get("action") or "") in ("create", "update")
    elif tool_name == "connectors" and str(args.get("service") or "") == "calendar":


        request = str(args.get("request") or "")
        writes = not _READ_ONLY.search(request) and tiers.classify_connector("calendar", request).kind != "read"
    else:
        return None
    if not writes or not asked(session_id):
        return None
    return {"action": "block", "message": home.personalize(
        "Not added: Colin asked to see what you would put on the calendar and has not said to add it. Nothing changed. "
        "Give him the list in your reply and offer the yes with offer_choices. Add it once he says yes.")}
