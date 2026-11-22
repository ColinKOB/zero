""



import json
import re

NOTE = "[Hub note: Reply check]"
_ADVERB = (r"(?!(?:nearly)\b)(?:[a-z]+ly|already|also|just|now|still|then|once|twice|again|since|ago|earlier|"
           r"today|yesterday|indeed|even|too|very|quite|rather|most|more|less|so|far|long|straightaway|forthwith|well)")
_MODIFIERS = rf"(?:{_ADVERB}(?:\s+(?:and\s+)?|,\s*(?:and\s+)?))*"
_CLAIM = re.compile(
    rf"\bI(?:['’](?:ve|d)| have| had)?\s+{_MODIFIERS}(?:sent|e-?mailed|mailed|replied|forwarded)\b"
    rf"|\b(?:it|(?:your|the|my)\s+(?:e-?mails?|messages?|repl(?:y|ies)))"
    rf"(?:['’]s|\s+(?:is|are|was|were|has|have))\s+{_MODIFIERS}"
    rf"(?:been\s+{_MODIFIERS})?(?:sent\b|on\s+(?:its|their|the)\s+way\b)"
    r"|(?:^\s*|(?<=[.!?])\s+)(?:(?:done|all set)[,!:]\s*)?"
    r"sent(?:\s+(?:it|that|them|the|your)\b|(?=[.!]|$))",
    re.I | re.M,
)
_NONCLAIM_PREFIX = re.compile(
    r"\b(?:if|unless|whether|have|had|should|could|would|might|must|can|did)\s*$"
    r"|\b(?:draft|preview|example|wording)\s+(?:says|reads)\s*$", re.I,
)
_CHAT_ANTECEDENT = re.compile(r"\b(?P<item>cards?|links?|messages?)[ \t]+(?:that[ \t]+)?$", re.I)
_CHAT_OBJECT = re.compile(
    r"^\s+(?:you\s+)?(?:(?:a|an|the|those|these|my|your|both|two|three|\d+)\s+)*"
    r"(?:(?:project|approval|choice)\s+)?(?P<item>cards?|links?|messages?)\b", re.I,
)
_CHAT_LOCATION = re.compile(r"^\s+(?:(?:to\s+)?you\s+)?(?:above|here|in (?:this|the) chat)\b", re.I)
_CHAT_REFERENCE_TAIL = re.compile(
    r"^[ \t]*(?:(?:to[ \t]+)?you\b[ \t]*)?"
    r"(?:(?:earlier|before|above|here|in (?:this|the) chat)\b[ \t]*)?"
    r"(?=$|[,:]|\b(?:is|are|was|were|can|will|opens?|lets?|has|have)\b)", re.I,
)



_CHAT_ITEM = re.compile(r"\b(?:lists?|dates?|summary|summaries|tables?|numbers?|figures?|details|times?)"
                        r"(?:\s+of\s+\w+)?[ \t]+(?:that[ \t]+)?$", re.I)



_CHAT_AMOUNT = re.compile(r"^\s+(?:you\s+)?(?:a\s+lot|so\s+much|too\s+much|a\s+bunch|a\s+ton|lots|plenty)\b", re.I)
_FROM_SOURCE = re.compile(r"\bfrom\b.*", re.I)
_OTHER_RECIPIENT = re.compile(r"\bto\s+(?!you\b)\S+", re.I)
_COMPOUND_SEND = re.compile(r",|\b(?:and|plus|then|also|as well as)\b", re.I)
_EXTERNAL_ROUTE = re.compile(r"\b(?:by|via|over|through|using|on)\s+(?!(?:this|the)\s+chat\b)\S+", re.I)
_EXTERNAL_CHANNEL = re.compile(
    r"\b(?:e-?mail(?:s|ed|ing)?|mailed|texted|messaged|forwarded|replied|inbox|mailbox|"
    r"invitations?|invites?|sms|whatsapp|slack|teams|telegram|discord)\b"
    r"|\b(?:by|via|over|through)\s+(?:text|mail)\b"
    r"|\b[\w.+-]+@[\w.-]+\b", re.I,
)
_QUOTED = re.compile(r'"[^"\n]*"|“[^”]*”|```.*?```|^>[^\n]*', re.S | re.M)
_HIDDEN_REFERENCE = re.compile(r"\b(?:my|the) short version(?:\s+(?:above|earlier))?\b", re.I)
_RETRACTION = re.compile(r"\b(?:correction|correcting myself|I (?:first |previously )?(?:wrote|said))\b", re.I)










_TEAMMATE = ("[Board update from the hub", "(information from a teammate")
_REACH = (r"(?:get\s+(?:into|in|to|past|at)|see|read|open|reach|access|load"
          r"|log\s*(?:in|into|on)|sign\s*(?:in|into|on))")
_COULD_NOT = (r"(?:couldn['’]t|could\s+not|wasn['’]t\s+able\s+to|was\s+unable\s+to"
              r"|ha(?:ve|s)n['’]t\s+been\s+able\s+to)\s+(?:yet\s+)?(?:" + _REACH + r"|check|pull\s+up|view)"
              r"|(?:can['’]t|cannot|can\s+not)\s+(?:yet\s+)?" + _REACH)
_UNREACHED = re.compile(r"(?:\b(?i:I|we)|(?<![\w'’])(?!You\b|He\b|She\b|They\b|It\b)[A-Z][a-z]+)"
                        r"\s+(?i:" + _COULD_NOT + r")\b")

_FAILED = re.compile(
    r"\b(?:" + _COULD_NOT + r"|never\s+(?:got\s+to|loaded|opened)"
    r"|(?:wouldn['’]t|won['’]t|didn['’]t|did\s+not|would\s+not)\s+(?:load|open|let)"
    r"|(?:isn['’]t|wasn['’]t)\s+(?:loading|opening|reachable)"
    r"|blank\s+page|(?:went|goes|going|still|same|stays?)\s+blank)\b", re.I)
_SAYS_UNREACHED = re.compile(
    _FAILED.pattern + r"|\bout\s+of\s+reach\b|\bno\s+access\b|\bha(?:ve|s)n['’]t\s+(?:seen|read|opened)\b"
    r"|\bwithout\s+(?:seeing|reading|opening)\b", re.I)


_READ_IT = re.compile(r"\bI(?:['’]ve|\s+have)?\s+(?:\w+\s+)?(?:read|opened|checked|got\s+in(?:to)?|pulled\s+up"
                      r"|looked\s+(?:at|through)|went\s+through)\b", re.I)
_FILLER = {"your", "their", "that", "this", "with", "from", "into", "page", "site", "because", "before", "after"}
_ADVICE = re.compile(
    r"\b(?:recommend(?:s|ed|ing|ations?)?|suggest(?:s|ed|ing|ions?)?|best\s+bet"
    r"|I['’]d\s+(?:pick|take|choose|go\s+with|go\s+for|book|buy|get|order)"
    r"|you\s+should\s+(?:take|pick|choose|go\s+with|book|buy|get|order))\b", re.I)


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def starts_turn(message: dict) -> bool:
    ""
    from . import history
    return (message.get("role") == "user" and message.get("display_kind") != "steer"
            and not history.unwrap_steer(str(message.get("content") or "")).startswith(NOTE))


def current_turn(raw: list[dict]) -> list[dict]:
    ""
    start = None
    for i, message in enumerate(raw):
        if starts_turn(message):
            start = i
    return raw[start:] if start is not None else []


def _sent(raw: list[dict]) -> bool:
    sends = set()
    for message in raw:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or call
                args = _object(function.get("arguments"))
                if call.get("id") and function.get("name") == "email" and args.get("action") in ("send", "reply"):
                    sends.add(str(call["id"]))
        elif message.get("role") == "tool":
            if str(message.get("tool_call_id") or "") not in sends:
                continue
            result = _object(message.get("content"))
            if not result or result.get("error") or result.get("ok") is False or result.get("success") is False:
                continue
            if result.get("exit_code", result.get("returncode", 0)) != 0:
                continue
            if result.get("done") == "sent":
                return True
    return False


def _prose(text: str) -> str:
    ""
    from . import who
    text = _QUOTED.sub("", text)
    header = re.search(r"(?m)^(?:To|From|Subject):\s*\S", text)
    if not header:
        return text
    names = [re.escape(name) for name in (who.first(), who.full()) if name]
    signature = re.search(r"(?m)^[ \t]*(?:" + "|".join(names) + r")[ \t]*$", text[header.end():]) if names else None

    if not signature:
        return text
    return text[:header.start()] + text[header.end() + signature.end():]






_RECAP_DAYS = 7
_SUBJECT_PREFIX = re.compile(r"^(?:\s*(?:re|fwd?|aw)\s*:)+\s*", re.I)


def _recorded(sentence: str, bot_id: str, at: float) -> bool:
    ""

    from . import store
    rows = store.db().execute(
        "SELECT body FROM feed WHERE kind='event' AND bot_id=? AND json_extract(body, '$.action')='sent' "
        "AND created_at BETWEEN ? AND ?", (bot_id, at - _RECAP_DAYS * 86400, at)).fetchall()
    lowered = sentence.lower()
    for row in rows:
        mail = _object(row["body"]).get("mail") or {}
        for person in (mail.get("to") or []) + (mail.get("cc") or []):
            addr = str(person.get("addr") or "").lower()
            if addr and addr in lowered:
                return True
            for part in re.findall(r"[^\W\d_]{3,}", str(person.get("name") or "")):
                if re.search(rf"\b{re.escape(part[0].upper() + part[1:].lower())}\b", sentence):
                    return True
        subject = _SUBJECT_PREFIX.sub("", str(mail.get("subject") or "")).strip().lower()
        if len(subject) >= 8 and subject in lowered:
            return True
    return False


def _when(at) -> float:
    ""
    import time
    try:
        return float(at)
    except (TypeError, ValueError):
        return time.time()


def _unproved_send(text: str, raw: list[dict], bot_id: str | None = None, at=None):
    ""

    from . import chatter
    full, short = chatter.split_short(text)
    prose = _prose(full) + "\n" + _prose(short or "")
    for match in _CLAIM.finditer(prose):
        if match.group(0).lower().endswith("sent"):
            head = re.split(r"[.!?;\n]", prose[:match.start()])[-1]
            tail = re.split(r"[.!?;\n]", prose[match.end():], maxsplit=1)[0]
            direct = _CHAT_OBJECT.match(tail)
            item = direct or (_CHAT_REFERENCE_TAIL.match(tail)
                              and _CHAT_ANTECEDENT.search(head))
            location = tail[direct.end():] if direct else " " + tail.lstrip()
            if (_CHAT_ITEM.search(head) and _CHAT_REFERENCE_TAIL.match(tail)
                    and not _EXTERNAL_CHANNEL.search(head + _FROM_SOURCE.sub("", tail))
                    and not _OTHER_RECIPIENT.search(tail)
                    and not _EXTERNAL_ROUTE.search(tail)
                    and not _COMPOUND_SEND.search(tail)):
                continue
            if (_CHAT_AMOUNT.match(tail) and not _EXTERNAL_CHANNEL.search(head + tail)
                    and not _OTHER_RECIPIENT.search(tail)):
                continue
            if (item and not _EXTERNAL_CHANNEL.search(head + match.group(0) + tail)
                    and not _OTHER_RECIPIENT.search(tail)
                    and not _EXTERNAL_ROUTE.search(tail)
                    and not _COMPOUND_SEND.search(tail)
                    and (item["item"].lower().startswith("card") or _CHAT_LOCATION.match(location))):
                continue
        if not _NONCLAIM_PREFIX.search(prose[:match.start()]):
            if _sent(raw):
                return None
            sentence = (re.split(r"[.!?;](?=\s)|\n", prose[:match.start()])[-1] + match.group(0)
                        + re.split(r"[.!?;](?=\s|$)|\n", prose[match.end():], maxsplit=1)[0])
            if not (bot_id and _recorded(sentence, bot_id, _when(at))):
                return match
    return None










_LEFT_OUT = re.compile(
    r"\bI(?:['’]ve\s+not|\s+have\s+not|\s+did\s+not|\s+haven['’]t|\s+didn['’]t)\s+(?:\w+\s+)?"
    r"(?:read|checked?|looked|opened?|searched?|gone)\b"
    r"|\b(?:isn['’]t|aren['’]t|not)\s+(?:yet\s+)?(?:included|checked|covered)\b", re.I)


def _left_out(text: str, turn: list[dict]) -> str | None:
    ""
    from . import chatter
    asked = {w for s in _subject(turn) for w in _words(s)}
    for version in chatter.split_short(text):
        shown = _QUOTED.sub("", chatter.without_hermes_notes(version or ""))
        opening = _words(_opening(shown))
        for sentence in _sentences(shown):
            named = _words(sentence) & asked
            if _LEFT_OUT.search(sentence) and named and not named & opening:
                return sentence.strip()
    return None





_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b\.?", re.I)
_UNSETTLED = re.compile(r"\b(?:unconfirmed|unverified|tentative|not\s+(?:yet\s+)?(?:confirmed|verified))\b", re.I)
_HEDGED = re.compile(r"\b(?:isn['’]t|aren['’]t|hasn['’]t|haven['’]t)\s+(?:\w+\s+)?(?:confirmed|verified)\b"
                     r"|\b(?:to\s+be|need(?:s)?\s+to\s+be|still\s+need\s+to|have\s+to|need\s+to)\s+(?:confirm|confirmed)\b", re.I)


def _clock_sentences(text: str) -> list[tuple[str, set[str]]]:
    ""
    plain = re.sub(r"\b([ap])\.m\.", r"\1m", text, flags=re.I)
    return [(s, {f"{int(h)}:{m or '00'}{ap.lower()}" for h, m, ap in _CLOCK.findall(s)}) for s in _sentences(plain)]












def _source_words(target: str) -> set[str]:
    ""
    name = re.split(r"[/\\]", target.rstrip("/\\"))[-1] if target else ""
    return _words(re.sub(r"\.[a-z0-9]{1,5}$", "", name, flags=re.I).replace("_", " ").replace("-", " "))


def _unsettled(text: str, turn: list[dict]) -> tuple[str, str] | None:
    ""
    from . import chatter
    record: dict[str, str] = {}
    plain: dict[str, list[set[str]]] = {}
    targets: dict[str, str] = {}
    for message in turn:
        if message.get("role") == "assistant":
            for call_id, _, args in _calls(message):
                targets[call_id] = target(args)
        if message.get("role") != "tool":
            continue
        found = _object(message.get("content"))
        result = str(found.get("result") or found.get("content") or message.get("content") or "")
        source = _source_words(targets.get(str(message.get("tool_call_id") or ""), ""))
        for sentence, clocks in _clock_sentences(result):
            for clock in clocks:
                if _UNSETTLED.search(sentence):
                    record[clock] = sentence
                else:
                    record.pop(clock, None)
                    if source:
                        plain.setdefault(clock, []).append(source)
    for version in chatter.split_short(text) if record else ():
        said = _clock_sentences(_QUOTED.sub("", chatter.without_hermes_notes(version or "")))
        for i, (sentence, clocks) in enumerate(said):
            near = sentence + " " + (said[i + 1][0] if i + 1 < len(said) else "")
            for clock in clocks & set(record):
                if any(words & _words(near) for words in plain.get(clock, [])):
                    continue
                if not (_UNSETTLED.search(near) or _UNCHECKED.search(near) or _HEDGED.search(near)) \
                        and _words(sentence) & _words(record[clock]):
                    return sentence.strip(), record[clock].strip()
    return None


def unsettled(text: str, raw: list[dict]) -> str | None:
    ""
    found = _unsettled(text, current_turn(raw))
    if not found:
        return None
    return (f'Your reply says "{found[0]}", but a record you read in this request says "{found[1][:300]}". {_UNSETTLED_FIX}')


_UNSETTLED_FIX = ("Say in that sentence that the time isn't confirmed, or confirm it where the answer lives and say what "
                  "you read.")


def shown_after_retry(why: str) -> bool:
    ""



    return why.endswith(_UNSETTLED_FIX)


def left_out(text: str, raw: list[dict]) -> str | None:
    ""

    skipped = _left_out(text, current_turn(raw))
    if not skipped:
        return None



    return (f'Your reply says "{skipped}", and that is part of what the person asked for. If one of your tools can reach '
            "it, reach it now and answer with it: your own browser and search for the web, and their Mac (mac_start) only "
            "for an app on it or a page that needs their sign-in or blocks your browser. If you still can't or won't, say what you left out in the "
            "first sentence of both versions.")


def live_held(raw: list[dict], i: int, turn: list[dict]) -> bool:
    ""
    after = raw[i + 1] if i + 1 < len(raw) else {}
    text = str(raw[i].get("content") or "")
    return (after.get("role") == "user" and str(after.get("content") or "").startswith(NOTE)
            and bool(_left_out(text, turn) or _unsettled(text, turn)))


_APPS = {"PostHog": "posthog", "Posthog": "posthog", "Trello": "trello", "Google Drive": "drive", "Drive": "drive",
         "Outlook": "outlook", "Dropbox": "dropbox", "Notion": "notion", "Linear": "linear", "GitHub": "github",
         "Github": "github", "Railway": "railway", "Cloudflare": "cloudflare", "Vercel": "vercel", "Stripe": "stripe"}
_APP = r"(?:the\s+|your\s+|my\s+)?(?P<app>" + "|".join(sorted(map(re.escape, _APPS), key=len, reverse=True)) + r")"
_DOWN = (re.compile(r"\b" + _APP + r"(?i:\s+(?:connection|tool|key|integration|account|api|access))?"
                   r"(?i:\s+(?:wasn['’]t|was\s+not|isn['’]t|is\s+not|weren['’]t)\s+(?:available|connected|reachable"
                   r"|responding|working|set\s+up)|\s+(?:failed|was\s+down|is\s+down))\b"),
         re.compile(r"\b(?i:I|we)\s+(?i:couldn['’]t|could\s+not|can['’]t|cannot|wasn['’]t\s+able\s+to|was\s+unable"
                    r"\s+to)\s+(?i:reach|connect\s+to|get\s+into|access|use|load|open)\s+" + _APP + r"\b"))




_LOGIN = (re.compile(r"\b" + _APP + r"(?i:\s+(?:web|website|browser|dashboard|site))?"
                     r"(?i:\s+(?:log-?in|sign-?in|sign\s+in|password|credentials))\b"),
          re.compile(r"(?i:\b(?:sign|log)\s*in(?:to|\s+to)?\s+)" + _APP + r"\b"))
_NO_NEED = re.compile(r"\b(?:without|(?:don['’]t|do\s+not|doesn['’]t|does\s+not|no\s+longer|never)\s+need(?:s|ed)?)\b",
                      re.I)
_CONDITION = re.compile(r"\b(?:if|unless|whether)\b", re.I)




_KEY_DODGE = (re.compile(r"\b(?:save|store|add|put|keep)\b[^.?!]{0,50}\b(?:api\s+)?keys?\b[^.?!]{0,50}"
                         r"\b(?:vault|login\s+items?|saved\s+logins?)\b", re.I),
              re.compile(r"\b(?:paste|send|share|give\s+me|text)\b[^.?!]{0,40}\bapi\s+keys?\b[^.?!]{0,30}"
                         r"\b(?:here|in\s+(?:this|the)\s+chat|to\s+me)\b", re.I),
              re.compile(r"\b(?:I|we)\s+(?:can['’]t|cannot|can\s+not|don['’]t\s+have\s+a\s+way\s+to|have\s+no\s+way\s+to)"
                         r"\s+(?:use|store|keep|hold|take)\b[^.?!]{0,30}\bapi\s+keys?\b", re.I))
_KEY_DODGE_FIX = ("A key never goes in the vault, a login item or the chat, and you can use any service's API. Call "
                  "ask_for_api_key: he gets one card with a secure field for each value. Then use service_api.")


def _key_dodge(text: str, turn: list[dict]) -> str | None:
    ""
    if any(name == "ask_for_api_key" for message in turn for _, name, _args in _calls(message)):
        return None
    return next((s.strip() for s in _sentences(_prose(text)) if any(p.search(s) for p in _KEY_DODGE)), None)


def app_login(text: str) -> str | None:
    ""

    for sentence in _sentences(text or ""):
        match = next((m for pattern in _LOGIN if (m := pattern.search(sentence))), None)
        if match and not _NO_NEED.search(sentence[:match.start()]):
            return _APPS[match["app"]]
    return None
_RECAP = re.compile(r"\b(?:earlier|yesterday|last\s+(?:night|time|week|run)|said|reported|wrote|told)\b"
                    r"|\bthis\s+morning\b|\bat\s+\d", re.I)


def _untried_app(text: str, turn: list[dict]) -> tuple[str, str] | None:
    ""

    from . import chatter
    called = set()
    for message in turn:
        for _, name, args in _calls(message):
            called |= {name, str(args.get("service") or args.get("app") or "").lower().removeprefix("google_")}
    for version in chatter.split_short(text):
        for sentence in _sentences(_prose(chatter.without_hermes_notes(version or ""))):
            match = next((m for pattern in _DOWN if (m := pattern.search(sentence))), None)
            if (match and _APPS[match["app"]] not in called and not _RECAP.search(sentence)
                    and not _CONDITION.search(sentence[:match.start()])):
                return sentence.strip(), _APPS[match["app"]]
            tool = app_login(sentence)
            if tool and tool not in called and not _RECAP.search(sentence):
                return sentence.strip(), tool
    return None


def _teammate_unreached(turn: list[dict]) -> tuple[str | None, bool]:
    ""

    for message in turn:
        said, advised, _ = _report(message)
        if said:
            return said, advised
    return None, False


_GAP_REPAIR = "A teammate said in this request: "
_OWN_REPAIR = "You said in this request: "
_TOOL_REPAIR = "A tool said in this request: "


def from_teammate(request: str, tools=()) -> bool:
    ""


    said, advised = _teammate_unreached([{"role": "user", "content": request}])
    return ((said is not None and advised) or "ask_teammate" in tools
            or request.startswith(NOTE) and any(p in request for p in (_GAP_REPAIR, _OWN_REPAIR, _TOOL_REPAIR)))


def _opening(text: str) -> str:
    ""
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])|\n+", text.strip()):
        out += " " + sentence
        if len(sentence.split()) > 3:
            break
    return out


def _advice_before_gap(text: str, said: str = "", advised: bool = False) -> str:
    ""


    from . import chatter
    match = _UNREACHED.search(said)
    named = set(re.findall(r"[a-z]{4,}", said[match.end():].lower())) - _FILLER if match else set()

    def stated(opening: str) -> bool:
        return bool(_SAYS_UNREACHED.search(opening) or (
            _READ_IT.search(opening) and any(word in opening.lower() for word in named)))
    shown = [chatter.without_hermes_notes(c) for c in chatter.split_short(text) if c]
    return next((_opening(c).strip() for c in shown if (advised and not chatter.empty(c) or _ADVICE.search(
        _QUOTED.sub("", c))) and not stated(_opening(c))), "")








_SWITCH = "reply_check.whole_reply"
_TAINT = re.compile(r"\b[A-Z]{2,4}\s?\d{3}\b")
_CARRY = re.compile(r"\b(?:unchanged|still\s+(?:stands?|good|holds?|works?|applies)|stands?|(?:is|are|['’]s)\s+ready)\b",
                    re.I)
_UNCHECKED = re.compile(
    r"\b(?:made|built|picked|chosen|written)\s+(?:\w+\s+)?(?:without|before)\b|\bunchecked\b|\bunconfirmed\b"
    r"|\bnot\s+(?:yet\s+)?(?:checked|confirmed|verified)\b|\b(?:can['’]t|cannot|couldn['’]t)\s+confirm\b"
    r"|\buntil\s+\w+\s+(?:checks?|confirms?|sees?)\b|\bdon['’]t\s+rely\b|\bwithout\s+(?:it|that|the|your|any)\b"
    r"|\b(?:can['’]t|cannot)\s+(?:yet\s+)?(?:tell|say|know)\b|\bnot\s+sure\b|\bdon['’]t\s+know\b", re.I)
_CLOSES = re.compile(r"\b(?:ask(?:ing)?|pull(?:s|ing)?\s+(?:it\s+)?up|download|sign(?:ing)?\s+in|log(?:ging)?\s+in"
                     r"|paste|retry|try(?:ing)?\s+again)\b", re.I)
_REACHED = re.compile(r"\b(?:is|are|['’]s)\s+open\b|\b(?:loaded|signed\s+in)\b", re.I)
_SOURCE_ARGS = ("url", "urls", "target", "path")
_COMMON = {"that", "this", "with", "from", "into", "your", "their", "there", "what", "which", "when", "will", "would",
           "could", "should", "have", "been", "were", "they", "them", "then", "than", "just", "also", "only", "some",
           "here", "about", "because", "before", "after", "again", "still", "same", "page", "site", "website",
           "browser", "file", "window", "screen", "link", "open", "opened", "opening", "load", "loaded", "loading",
           "blank", "went", "read", "reading", "show", "tried", "trying", "need", "want", "https", "http", "html",
           "index", "login", "sign", "home", "colin", "couldn", "didn", "wouldn", "wasn", "doesn", "whether"}
_NEXT_STEP = re.compile(r",?\s+(?:so\s+|and\s+)?I['’](?:m|ll)\s.*$", re.S)


def enforcing() -> bool:
    from . import home
    return home.get(_SWITCH) == "enforce"


def _words(text: str) -> set[str]:
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"[a-z]{4,}", text.lower())} - _COMMON


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(\d])|\n+", text.strip()) if s.strip()]


def _calls(message: dict):
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function") or call
            yield str(call.get("id") or ""), function.get("name"), _object(function.get("arguments"))


def target(args) -> str:
    ""
    args = _object(args)
    values = [args.get(key) for key in _SOURCE_ARGS]
    found = " ".join(v if isinstance(v, str) else " ".join(map(str, v)) for v in values if isinstance(v, (str, list)))
    return re.sub(r"(?:^|(?<=\s))(?:/Users/|/home/)[^/\s]+/", "", found) if re.search(r"[/.]", found) else ""


def _report(message: dict) -> tuple[str | None, bool, str]:
    ""

    from . import history
    text = str(message.get("content") or "")
    if message.get("role") == "tool":
        text = str(_object(text).get("result") or text)
    elif message.get("role") == "user":
        text = history.unwrap_steer(text)
    else:
        return None, False, ""
    start = 0 if text.lstrip().startswith(_TEAMMATE[0]) else text.find(_TEAMMATE[1])
    match = _UNREACHED.search(text, start) if start >= 0 else None
    if not match:
        return None, False, text
    head = max(text.rfind(mark, 0, match.start()) for mark in (". ", ": ", "\n")) + 1
    said = re.split(r"(?<=[.!?:])\s|\n", text[head:].strip(), maxsplit=1)[0][:240].rstrip(" .:;,")
    end = text.find("\n\n", match.end())
    return said, bool(_ADVICE.search(text, start)), text[start:end if end >= 0 else None]


def _subject(turn: list[dict]) -> list[str]:
    ""

    from . import history, turnsummary, who
    texts = []
    for message in turn:
        if message.get("role") == "user":
            text = turnsummary.peel(history.unwrap_steer(str(message.get("content") or "")))[1]
            if not text.lstrip().startswith(("[Hub note", "[Board update", NOTE, who.tap_prefix())):
                texts.append(text)
        for _, name, args in _calls(message):
            keys = {"mac_start": ("task", "colin_said"), "ask_teammate": ("question",),
                    "kanban_create": ("title", "body")}.get(name, ())
            texts += [str(args[key]) for key in keys if args.get(key)]
    return [s for text in texts for s in _sentences(text)]


def _own_failure(sentence: str) -> str:
    ""

    match = _FAILED.search(sentence)
    if not match:
        return ""
    actor = re.search(r"([A-Za-z]+)\W*$", sentence[:match.start()])
    actor = actor.group(1) if actor else ""
    if actor.lower() in ("he", "she", "they", "you") or (
            actor[:1].isupper() and actor not in ("I", "We") and re.match(_COULD_NOT, match.group(0), re.I)):
        return ""
    return sentence[:match.end()] + _NEXT_STEP.sub("", sentence[match.end():])


def stumbled(commentary: str) -> bool:
    ""
    return any(_own_failure(s) for s in _sentences(commentary or ""))


def failed(result) -> bool:
    ""
    result = _object(result)
    return bool(result.get("error") or result.get("success") is False or result.get("ok") is False)


def unreached(raw: list[dict]) -> list[dict]:
    ""






    turn = current_turn(raw)
    if not turn:
        return []
    gaps = []
    for message in turn:
        said, advised, text = _report(message)
        if said:
            match = _UNREACHED.search(said)
            named = _words(said[match.end():] if match else said)
            gaps.append({"said": said, "named": named, "about": named, "origin": "teammate", "advised": advised,
                         "tainted": set(_TAINT.findall(text)) if advised else set()})
    subject = [(s, _words(s)) for s in _subject(turn)]

    def gap(said: str, named: set[str], origin: str):
        about = {w for s, words in subject if words & named for w in words}
        if about:
            gaps.append({"said": said, "named": named, "about": named | about, "origin": origin, "advised": False,
                         "tainted": set()})

    last, targets = "", {}
    for message in turn:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            for sentence in _sentences(str(message.get("content") or "")):
                if failure := _own_failure(sentence):
                    gap(sentence.strip().rstrip(" .:;,"), _words(failure) or _words(last), "own")
                elif _REACHED.search(sentence) or _READ_IT.search(sentence):
                    gaps = [g for g in gaps if g["origin"] == "teammate" or not g["about"] & _words(sentence)]
            for call_id, _, args in _calls(message):
                targets[call_id] = target(args)
                last = targets[call_id] or last
        elif message.get("role") == "tool" and targets.get(str(message.get("tool_call_id") or "")):
            page = targets[str(message["tool_call_id"])]
            if failed(message.get("content")):
                error = str(_object(message.get("content")).get("error") or "it failed")[:160]
                gap(f"{page}: {error}", _words(page), "tool")
            else:
                gaps = [g for g in gaps if g["origin"] != "tool" or not g["named"] & _words(page)]
    earlier = [_report(m) for m in raw[:len(raw) - len(turn)] if m.get("role") in ("user", "tool")]
    for g in gaps:
        if g["origin"] != "teammate":
            for said, advised, text in earlier:
                if said and advised and _words(said) & g["about"]:
                    g["tainted"] |= set(_TAINT.findall(text))
    return gaps


def whole_reply(text: str, raw: list[dict]) -> str | None:
    ""




    from . import chatter
    gaps = unreached(raw)
    for version in chatter.split_short(text) if gaps else ():
        shown = _QUOTED.sub("", chatter.without_hermes_notes(version or ""))
        opening = _opening(shown).lower()
        open_gaps = [g for g in gaps if not (_READ_IT.search(opening) and any(w in opening for w in g["about"]))]
        for sentence in _sentences(shown) if open_gaps else ():
            if _UNCHECKED.search(sentence):
                continue
            items = {re.sub(r"\s", "", t) for t in _TAINT.findall(sentence)}
            advice = (_ADVICE.search(sentence) and not _CLOSES.search(sentence)
                      and not _SAYS_UNREACHED.search(sentence))
            for g in open_gaps:
                if (items & {re.sub(r"\s", "", t) for t in g["tainted"]}
                        or advice and _words(sentence) & g["named"]
                        or (g["advised"] or g["origin"] != "teammate") and _CARRY.search(sentence)):
                    return _gap_note(g, f'This sentence of your reply leans on it anyway: "{sentence.strip()}"')
        own = [g for g in open_gaps if g["origin"] != "teammate"]
        if own and _ADVICE.search(shown) and not _SAYS_UNREACHED.search(_opening(shown)):
            return _gap_note(own[0], "Your reply recommends something, and its first sentence does not say what could "
                                     f'not be reached: "{_opening(shown).strip()}"')
    return None


def _gap_note(gap: dict, problem: str) -> str:
    who = {"teammate": _GAP_REPAIR, "own": _OWN_REPAIR, "tool": _TOOL_REPAIR}[gap["origin"]]
    return (f'{who}"{gap["said"]}." {problem} Start with what could not be reached and what that leaves unknown. '
            "Recommend nothing that depends on it. If you keep any item the source would decide, mark it unchecked "
            "in that same sentence. If you reached it yourself later in this request, say that first instead.")


def reason(text: str, raw: list[dict], bot_id: str | None = None, at=None) -> str | None:
    ""

    from . import chatter
    full, _ = chatter.split_short(text)
    prose = _prose(full)
    if _HIDDEN_REFERENCE.search(prose):
        return ("The phone summary is private formatting, not an earlier message the person saw. "
                "Give the checked answer directly. Do not refer to or correct a hidden short version.")
    turn = current_turn(raw)
    if not turn:
        return None

    match = _unproved_send(text, turn, bot_id, at)
    if match:

        said = (re.split(r"[.!?;](?=\s)|\n", match.string[:match.start()])[-1] + match.group(0)
                + re.split(r"[.!?;](?=\s|$)|\n", match.string[match.end():], maxsplit=1)[0]).strip()
        return (f'There is no successful recorded email send in this request supporting "{match.group(0)}" '
                f'in "{said}". If that sentence means messages in this chat, reword it without "sent". '
                "A choice or approval card is not a completed action. Say what is ready or waiting for approval "
                "and show the proposed text. Do not claim the action happened, and do not perform it to make "
                "the claim true. If you wrote text in chat, show that text instead of claiming an external save.")
    dodge = _key_dodge(text, turn)
    if dodge:
        return f'Your reply said "{dodge}" {_KEY_DODGE_FIX}'
    untried = _untried_app(text, turn)
    if untried:
        sentence, tool = untried


        return (f'Your reply says "{sentence}", but this request has no call to the {tool} tool. Call {tool} now and '
                "report what it returns; it reads the app through Zero's connection, with no website login. Use it to "
                "read, and never send a change or a made-up value to test it. If it fails or says the app isn't "
                f"connected, say that in its own words. If you have no {tool} tool here, say you don't have it in this chat.")
    said, advised = _teammate_unreached(turn)
    opening = _advice_before_gap(text, said, advised) if said else ""
    if opening:
        return (f'{_GAP_REPAIR}"{said}." Your reply recommends something anyway, and its first sentence, '
                f'"{opening}", does not say what could not be reached. Start with what could not be reached and what '
                "that leaves unknown. Recommend nothing that depends on it, including what the teammate suggested "
                "without it. If you reached it yourself later in this request, say that first instead.")
    if _RETRACTION.search(prose):
        for i, message in enumerate(turn):
            if (message.get("role") == "assistant"
                    and _unproved_send(str(message.get("content") or ""), turn[:i], bot_id, at)):
                return ("The earlier send claim was rejected before publication. The person did not see it. "
                        "Give the proposed email and its actual approval state directly, without retracting unseen text.")
    return None


def without_claim(text: str, raw: list[dict], bot_id: str | None = None) -> str | None:
    ""


    turn = current_turn(raw)
    out = text
    for _ in range(3):
        match = _unproved_send(out, turn, bot_id)
        if not match:
            out = re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", out)).strip()
            return out if out and out != text.strip() and _visible(out) else None
        said = (re.split(r"[.!?;](?=\s)|\n", match.string[:match.start()])[-1] + match.group(0)
                + re.split(r"[.!?;](?=\s|$)|\n", match.string[match.end():], maxsplit=1)[0]).strip()
        at = out.find(said) if said else -1
        if at < 0:
            return None
        end = at + len(said)
        while end < len(out) and out[end] in ".!?;":
            end += 1
        out = out[:at] + out[end:].lstrip(" \t")
    return None


def _visible(text: str) -> str:
    ""
    from . import chatter
    return chatter.without_hermes_notes(chatter.split_short(text)[0]).strip()


def needed(text: str, teammate: bool = False) -> bool:
    ""
    return bool(teammate or _unproved_send(text or "", []) or _untried_app(text or "", []) or _key_dodge(text or "", [])
                or _LEFT_OUT.search(_prose(text or "")) or _CLOCK.search(text or "")
                or _HIDDEN_REFERENCE.search(text or "")
                or _RETRACTION.search(text or "") or _advice_before_gap(text or ""))


def correction(why: str) -> str:
    return (NOTE + "\n" + why + "\nThe rejected text was not shown. Answer the original request again without "
            "apologizing for or referring to that text. Keep all requested work in the reply.")
