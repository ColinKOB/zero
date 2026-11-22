""


































import logging
import re
import time
from urllib.parse import urlparse

from . import chatter, replycheck, zipvoice

log = logging.getLogger("taffy.finalpass")

MARK = "Before this reply goes out, account for what this request's record shows."



_STATUS_NOTE = "Nothing you read in this request"
QUIET_MARK = "so this update was not shown"





_HOLD_MAX_SEC = 30 * 60
_holding: dict[str, list[tuple[str, float]]] = {}


def _same_words(text) -> str:
    return " ".join(str(text or "").split())


def hold(bot_id: str, text: str) -> None:
    ""
    _holding.setdefault(bot_id, []).append((text, time.monotonic()))


def release(bot_id: str) -> None:
    _holding.pop(bot_id, None)


def holding(bot_id: str) -> list[str]:
    ""
    return [text for text, at in _holding.get(bot_id, []) if time.monotonic() - at < _HOLD_MAX_SEC]


def held(bot_id: str | None, text) -> bool:
    return bool(bot_id and bot_id in _holding) and _same_words(text) in {_same_words(t) for t in holding(bot_id)}



_PLUMBING = re.compile(r"^(?:skill_|memory$|save_fact$|session_search$|tell_teammate$|close_|explain_card$|offer_choices$"
                       r"|ask_to_|ask_for_|report_problem$|todo_list$|app_todo_list$|vision_analyze$|write_file$|patch$|read_file$"
                       r"|mac_(?:click|press|scroll|type|ui|screenshot|done|key)$|browser_(?:click|type|scroll|press"
                       r"|snapshot|console|vision|vault_)|kanban_block$)")


_ON_PURPOSE = re.compile(r"^(?:Not done|Not shown|Not sent|Blocked|Refusing)\b|\bcard\b|\bSay nothing\b|\bgo-ahead\b"
                         r"|\bAsk \w+ first\b|\bnever (?:run|opened)\b|\boff limits\b|\bneeds \w+['’]s yes\b"
                         r"|\bis off for Zips\b|\bdid not run, because\b", re.I)
_GENERIC = {"list", "read", "call", "show", "search", "status", "create", "start", "open", "manage", "tool", "browser",
            "navigate", "extract"}
_ANSWERED = re.compile(r"^(?P<who>[^\n]{1,60}?) answered \(information from a teammate[^\n]*\n"
                       r"|^You asked (?P<again>[^\n]{1,60}?) this at [^\n]*Its answer then:\n")
_NAME = re.compile(r"(?<=[a-z,;:)] )[A-Z][a-z]{2,}\b")
_NOT_NAMES = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec", "January",
              "February", "March", "April", "June", "July", "August", "September", "October", "November", "December",
              "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "The", "This", "That"}


_BROAD = re.compile(r"\bon\s+top\s+of\s+(?:whatever|everything)\b|\bwhatever\s+i\s+need\b|\bcatch\s+me\s+up\b"
                    r"|\beverything\s+i\s+(?:need|have)\s+to\s+(?:do|handle)\b", re.I)
_CONFLICT = re.compile(r"\bconflict|\bcontradict|\bdisagree|\b(?:don|doesn)['’]t\s+match\b|\bmismatch", re.I)
_SAYS_CONFLICT = re.compile(_CONFLICT.pattern + r"|\b(?:don|doesn)['’]t\s+list\b|\b(?:not|isn['’]t)\s+listed\b"
                            r"|\bnotes\s+(?:say|show|differ)|\bdiffers?\b", re.I)
_DEADLINE = re.compile(r"\b(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?\b|\btomorrow\b|\btonight\b"
                       r"|\b(?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d|\b\d{1,2}/\d{1,2}\b", re.I)
_SCHEDULES = re.compile(r"cron|remind|wait|loop|follow|schedul|calendar")
_RECOMMENDS = re.compile(r"\bI['’]d\b|\bI\s+would\b|\brecommend|\bsuggest|\bmy\s+pick\b|\bbest\s+bet\b|\bgo\s+with\b", re.I)
_DRAFTED = re.compile(r"\bdraft(?:ed)?\b.*(?:\bready\b|\bbelow\b|:)|^>|[\"“][^\"”]{40,}[\"”]", re.I | re.M)
_ASKS = re.compile(r"\?\s*$|^(?:\d+\.\s*)?(?:please\s+)?(?:give|tell|send)\s+me\b|\bI\s+need\s+(?:you\s+to|one\b|a\s+line)"
                   r"|\b(?:give|tell|send)\s+me\s+(?:one|a)\s+line\b", re.I)
_OFFER = re.compile(r"^\W*(?:want|should|shall|do\s+you\s+want)\s+(?:me|i)\b|\bwant\s+me\s+to\b", re.I)


_ERRAND_CALLS = 8
_BLOCKS = re.compile(r"\n\s*\n|\n(?=\s*\d+\.\s)")

_LONG_BESIDE = 800
_ALSO_CALLED = {"messages": "text", "email": "mail"}


def _turn_calls(turn: list[dict]) -> list[dict]:
    ""
    calls, by_id = [], {}
    for message in turn:
        if message.get("role") == "assistant":
            for call_id, name, args in replycheck._calls(message):
                by_id[call_id] = {"name": name or "", "args": args, "failed": False, "error": "",
                                  "source": _source(name or "", args)}
        elif message.get("role") == "tool":
            call = by_id.get(str(message.get("tool_call_id") or ""))
            if call:
                result = replycheck._object(message.get("content"))
                call["failed"] = replycheck.failed(message.get("content"))
                call["error"] = str(result.get("error") or result.get("message") or "it failed") if call["failed"] else ""
                calls.append(call)
    return calls


def _source(name: str, args: dict) -> str:
    ""
    for key in ("service", "app", "account"):
        if args.get(key):
            return str(args[key]).lower().removeprefix("google_")
    for key in ("url", "target"):
        if isinstance(args.get(key), str) and "://" in args[key]:
            return (urlparse(args[key]).hostname or "").removeprefix("www.")
    return ""


def _recovered(call: dict, later: list[dict]) -> bool:
    ""

    for other in later:
        if other["failed"]:
            continue
        if other["name"] == "connectors" and other["source"] and other["source"] in (call["name"], call["source"]):
            return True
        if other["name"] == call["name"] and (not call["source"] or not other["source"] or other["source"] == call["source"]):
            return True
        if call["name"].startswith("mac_") and other["name"].startswith("mac_"):
            return True
        if call["name"] == "reminders" and _SCHEDULES.search(other["name"]):
            return True
    return False


def _names_it(call: dict, reply: str) -> bool:
    ""
    said = f"{call['name'].replace('_', ' ')} {call['source']} {_ALSO_CALLED.get(call['name'], '')}"
    words = [w for w in re.findall(r"[a-z]{3,}", said.lower())
             if w not in _GENERIC and w not in ("com", "org", "net", "www", "edu")]
    return any(re.search(rf"\b{re.escape(w.rstrip('s'))}", reply, re.I) for w in words)


def _hands_back(call: dict, reply: str) -> bool:
    ""

    words = re.findall(r"[a-z']+", call["error"].lower())
    said = " ".join(re.findall(r"[a-z']+", reply.lower()))
    return any(" ".join(words[i:i + 5]) in said for i in range(len(words) - 4))


def failures(turn: list[dict], reply: str) -> list[dict]:
    ""

    calls, out, seen = _turn_calls(turn), [], set()
    for i, call in enumerate(calls):
        if (call["failed"] and not _PLUMBING.match(call["name"]) and not _ON_PURPOSE.search(call["error"])
                and not _recovered(call, calls[i + 1:]) and (not _names_it(call, reply) or _hands_back(call, reply))
                and (call["name"], call["source"]) not in seen):
            seen.add((call["name"], call["source"]))
            out.append(call)
    return out


def answers(turn: list[dict]) -> dict[str, str]:
    ""

    out = {}
    for message in turn:
        if message.get("role") == "tool" and message.get("tool_name") in (None, "ask_teammate"):
            text = str(replycheck._object(message.get("content")).get("result") or "")
            match = _ANSWERED.match(text)
            if match:
                out[match["who"] or match["again"]] = (text[match.end():].replace(zipvoice.DATE_SOURCE, "")
                                                       .replace(zipvoice.KEEP_DOUBT, "").strip())
    return out


def _unpassed(answer: str, who: str, reply: str) -> bool:
    ""
    if re.search(rf"\b{re.escape(who)}\b", reply, re.I):
        return False
    return not any(re.search(rf"\b{re.escape(n)}\b", reply) for n in set(_NAME.findall(answer)) - _NOT_NAMES)


def conflicts(turn: list[dict], reply: str) -> dict[str, str]:
    ""

    if _SAYS_CONFLICT.search(reply):
        return {}
    out = {}
    for who, answer in answers(turn).items():
        at = _CONFLICT.search(answer)
        if at:
            out[who] = " ".join(answer[at.start():].split())[:300]
    return out


def no_next_date(turn: list[dict]) -> bool:
    ""

    from . import history, turnsummary
    calls = _turn_calls(turn)
    request = turnsummary.peel(history.unwrap_steer(str(turn[0].get("content") or "")))[1] if turn else ""
    return (len(calls) >= _ERRAND_CALLS and bool(_DEADLINE.search(request)) and any(c["name"].startswith(("ask_to_", "offer_choices")) and not c["failed"]
                                                    for c in calls)
            and not any(_SCHEDULES.search(c["name"]) and not c["failed"] for c in calls))






_WILL = re.compile(r"\bI(?:['’]ll|\s+will|['’]m\s+going\s+to)\b(?!\s+(?:need|be|assume)\b)|^\W*Will\b|\byou['’]ll\s+hear\s+from\s+me\b",
                   re.I)
_LATER = re.compile(r"\b(?:remind(?:er)?s?|nudge|ping\s+you|check\s+(?:back|again)|re-?check|look\s+again|circle\s+back"
                    r"|keep\s+(?:checking|watching|an\s+eye)|keep\s+(?:it|this|that|them)\s+on\s+your\s+radar"
                    r"|watch\s+for|bring\s+(?:it|this|that|them)\s+up)\b", re.I)


_RETRY = re.compile(r"\b(?:once|when|as\s+soon\s+as)\s+(?:it|the|that|this|they)\b[^.,;]{0,40}"
                    r"\b(?:again|back|up|works?|loads?|opens?)\b", re.I)


_AWAY = re.compile(r"\b(?:once|when|while|after)\s+you['’]re\s+(?:away|off|not\s+(?:using|on|at))\b", re.I)
_NEXT = re.compile(r"^\W*Next,?\s+I['’]ll\b|\bnext\W*$", re.I)
_EVERY = (r"(?:each|every)\s+(?:day|week|month|morning|night|evening|few\s+days|(?:mon|tues|wednes|thurs|fri|satur|sun)day)"
          r"|weekly|daily")
_WHEN = re.compile(r"(?<!for )\b(?:tomorrow|tonight|later\s+today|this\s+(?:afternoon|evening|weekend)|next\s+week"
                   r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day|" + _EVERY + r")\b(?!['’]s)", re.I)
_CLAUSE_END = re.compile(r"[,;:]|\s(?:since|because|which|so|while|and\s+the)\s|\bnow\b|\bright\s+away\b", re.I)

_ONLY_IF = re.compile(r"\bif\s+(?:you\s+(?:want|like|prefer|need)|you['’]d|that\b)|\bsay\s+the\s+word\b|\b(?:let\s+me\s+know|tell\s+me)\b"
                      r"|\b(?:once|when|after|as\s+soon\s+as)\s+you\b|\bwant\s+me\b", re.I)
_REPEATS = re.compile(_EVERY + r"|\bkeep\s+(?:checking|watching|an\s+eye)\b|\bwatch\s+for\b", re.I)
_WAITS = {"wait", "extend", "place", "mail_routine", "trigger_create"}


def promises(reply: str) -> list[str]:
    ""
    out = []
    for sentence in replycheck._sentences(replycheck._prose(reply or "")):
        will, away = _WILL.search(sentence), _AWAY.search(sentence)
        if not will or sentence.rstrip().endswith("?") or _ONLY_IF.search(sentence) and not away:
            continue
        rest = sentence[will.end():]
        clause = _CLAUSE_END.split(rest, maxsplit=1)[0]
        if _LATER.search(rest) or _WHEN.search(clause) or _RETRY.search(sentence) or away or _NEXT.search(sentence):
            out.append(sentence.strip())
    return out


def _sets_later(call: dict) -> str:
    ""

    name, args = call["name"], call["args"]
    action = str(args.get("action") or "")
    if call["failed"]:
        return ""
    if name in ("cronjob", "cronjob_manage") and action in ("create", "update", "resume"):
        one_time = args.get("repeat") == 1 or re.match(r"\d{4}-\d{2}-\d{2}|in\s", str(args.get("schedule") or ""))
        return "once" if one_time else "repeats"
    if name == "errands" and action in _WAITS or name == "email" and args.get("expect_reply") or \
            name == "watch_texts" and action == "start":
        return "repeats"
    if name == "reminders" and action == "add" or name == "kanban_schedule" or name == "calendar" and action in ("add", "create"):
        return "once"
    if name == "connectors" and "calendar" in call["source"] and not re.match(r"\W*read\b", str(args.get("request") or ""), re.I):
        return "once"
    return ""


def _on_the_books(line: str, bot_id: str | None) -> bool:
    ""

    from . import config, waits, watcher
    mine = bot_id if config.is_sandbox(bot_id) else None
    books = [f"{w['what']} {w['next_step']}" for w in waits.listed(mine)]
    for owner, out in watcher._cron_dirs():
        if mine in (None, owner):
            books += [str(j.get("name") or "") for j in watcher._jobs_of(out.parent / "jobs.json").values()
                      if j.get("enabled", True)]
    said = replycheck._words(line)
    return any(words and len(said & words) >= min(3, len(words)) for words in map(replycheck._words, books))


def unset_promises(turn: list[dict], reply: str, bot_id: str | None = None) -> list[str]:
    ""
    set_up = {_sets_later(c) for c in _turn_calls(turn)}
    if "repeats" in set_up:
        return []
    lines = [s for s in promises(reply) if "once" not in set_up or _REPEATS.search(s)]
    return [s for s in lines if not _on_the_books(s, bot_id)]


def _one_line(answer: str, limit: int = 300) -> str:
    ""
    sentences = replycheck._sentences(answer)
    if len(sentences) < 2:
        return " ".join(answer.split())[:limit]
    return " ... ".join(" ".join(s.split())[:limit // 2] for s in (sentences[0], sentences[-1]))


def unread(turn: list[dict], reply: str) -> list[str]:
    ""
    from . import history, home, turnsummary
    request = turnsummary.peel(history.unwrap_steer(str(turn[0].get("content") or "")))[1] if turn else ""
    if not _BROAD.search(request) or len(_turn_calls(turn)) < _ERRAND_CALLS:
        return []
    known = {str(a["id"]): str(a.get("label") or a["id"]) + " mail" for a in home.mail_accounts() if not a.get("zips_own")}
    if home.is_owner_home():
        known["icloud"] = "iCloud mail"
    if home.trello():
        known["trello"] = "Trello"
    for call in _turn_calls(turn):
        if not call["failed"]:
            known.pop(call["source"], None)
            known.pop(call["name"], None)
    return [label for label in known.values() if not re.search(rf"\b{re.escape(label.split()[0])}\b", reply, re.I)]


def questions(turn: list[dict], reply: str) -> list[str]:
    ""


    calls = _turn_calls(turn)
    if len(calls) < _ERRAND_CALLS or any(c["name"].startswith(("ask_to_", "offer_choices")) or
                                         "card" in c["error"].lower() for c in calls):
        return []
    out = []
    for block in _BLOCKS.split(reply):
        if _RECOMMENDS.search(block) or _DRAFTED.search(block):
            continue
        out += [s.strip() for s in replycheck._sentences(block) if _ASKS.search(s.strip()) and not _OFFER.search(s)]
    return out










_ACCOUNTS = {"email", "calendar", "drive", "trello", "connectors", "messages", "course_status", "bank", "tally",
             "outlook", "notion", "dropbox", "github", "linear", "contacts", "reminders", "app_todo_list", "look_up"}
_LOOKUP_Q = re.compile(r"\b(?:do|did)\s+you\s+(?:have|know|remember|still\s+have)\b|\b(?:send|give|share|paste|forward)\s+me\b"
                       r"|\btell\s+me\s+(?:the|which|where|when|what|who|h(?:is|er)|their|your)\b"
                       r"|\b(?:what|which)(?:['’]s|\s+is|\s+was|\s+are|\s+were)\s+(?:the|h(?:is|er)|their|your)\s+(?:\w+\s+){0,2}"
                       r"(?:address|date|link|url|name|number|email|price|time|order|account|site|page|class|course|code)\b"
                       r"|\b(?:where|when)\s+(?:is|was|are|were|did|does|do)\b|\bwho\s+(?:is|was|are|sent|should\s+(?:I|this))\b"
                       r"|\bwhich\s+(?:one|account|email|site|page|order|class|course|form|file|card|address)\b"
                       r"|\bwhat\s+(?:date|day|time|address|link|email|number|price|order|account|site|page|class|course)\b"
                       r"|\bwhat\s+did\s+\w+(?:\s+and\s+\w+)?\s+(?:decide|agree|settle|say|quote|send)\b", re.I)

_HIS_CALL = re.compile(r"\byou\s+(?:want|pick|prefer|choose|decide|like)\b|\byou['’](?:re\s+ready|d\s+(?:like|rather))\b"
                       r"|^\W*should\s+I\b", re.I)
_NOT_FOUND = re.compile(r"\b(?:couldn['’]t|could\s+not|didn['’]t|did\s+not|can['’]t|cannot)\s+(?:find|locate|spot)\b"
                        r"|\bno\s+sign\s+of\b|\bI\s+don['’]t\s+have\s+(?:a|an|the|your|h(?:is|er)|their|any)\b"
                        r"|\bnothing\s+(?:in|on|from)\s+[^.]{0,40}\b(?:mentions?|about|shows?|says?|matches)\b"
                        r"|\b(?:no|haven['’]t\s+(?:seen|found))\s+(?:email|message|record|mention|note)s?\s+(?:from|about|of)\b"
                        r"|\bhaven['’]t\s+(?:told|given|sent)\s+me\b|\bisn['’]t\s+saved\b", re.I)




_PICKS = re.compile(r"\bI(?:['’]d|\s+would)\s+(?:go|pick|choose|take|recommend|suggest|say|lean)\b|\brecommend|\bsuggest"
                    r"|\bmy\s+pick\b|\bbest\s+bet\b|\bgo\s+with\b", re.I)


def _lookup_questions(reply: str) -> list[str]:
    out = []
    for block in _BLOCKS.split(reply or ""):
        if _PICKS.search(block) or _DRAFTED.search(block):
            continue
        out += [s.strip() for s in replycheck._sentences(block) if _ASKS.search(s.strip()) and not _OFFER.search(s)
                and _LOOKUP_Q.search(s) and not _HIS_CALL.search(s)]
    return out


def _not_found(reply: str) -> list[str]:
    return [s.strip() for s in replycheck._sentences(replycheck._prose(reply or "")) if _NOT_FOUND.search(s)]


def asks_first(reply: str) -> bool:
    ""
    return bool(_lookup_questions(reply) or _not_found(reply))


def _said_before(sentence: str, earlier: list[dict]) -> bool:
    ""
    head = " ".join(re.findall(r"[a-z0-9]+", sentence.lower()))[:60]
    return bool(head) and any(head in " ".join(re.findall(r"[a-z0-9]+", str(m.get("content") or "").lower()))
                              for m in earlier if m.get("role") == "assistant")


def unlooked(turn: list[dict], reply: str, earlier: list[dict] = ()) -> list[str]:
    ""


    searched = {(c["source"] or c["name"]) if c["name"] == "connectors" else c["name"] for c in _turn_calls(turn)
                if c["name"] in _ACCOUNTS and not c["failed"]}



    cards = [str(c["args"].get("question") or "").strip() for c in _turn_calls(turn) if c["name"] == "offer_choices"]
    asked = _lookup_questions(reply) + [q for card in cards if card for q in _lookup_questions(card.rstrip("?") + "?")]
    out = asked if not searched else []

    out += _not_found(reply) if len(searched) < 2 and "look_up" not in searched else []
    return [s for s in dict.fromkeys(out) if not _said_before(s, earlier)]


def _with_what_teammates_said(question: str, reply: str, turn: list[dict]) -> str:
    ""


    block = next((b for b in _BLOCKS.split(reply) if question in b), question)
    named = set(re.findall(r"\b[A-Z][a-z]{2,}\b", block)) - _NOT_NAMES - {"Give", "Tell", "Send", "Can", "What"}
    numbers = set(re.findall(r"\b\d{2,}\b", block))
    said = [f'  {who} said: "{" ".join(s.split())[:240]}"' for who, answer in answers(turn).items()
            for s in replycheck._sentences(answer)
            if named & set(re.findall(r"\b[A-Z][a-z]{2,}\b", s)) or numbers & set(re.findall(r"\b\d{2,}\b", s))]
    return "\n".join([f'- "{question}"'] + said[:3])


def _beside_calls(turn: list[dict]) -> list[str]:
    ""
    return [text for text in (chatter.without_hermes_notes(str(m.get("content") or "")) for m in turn
                              if m.get("role") == "assistant" and m.get("tool_calls")) if text.strip()]


def note(reply: str, raw: list[dict], bot_id: str | None = None, given: str = "") -> str | None:
    ""

    turn = replycheck.current_turn(raw)
    if any(str(m.get("content") or "").startswith(replycheck.NOTE) for m in turn if m.get("role") == "user"):
        return None
    shown = chatter.without_hermes_notes(chatter.split_short(reply)[0] or "")
    promised = unset_promises(turn, shown, bot_id)
    looked = unlooked(turn, shown, raw[:len(raw) - len(turn)])
    if not promised and not looked and not any(m.get("role") == "tool" for m in turn):
        return None
    beside = _beside_calls(turn)
    showed = "\n\n".join([*beside, shown])
    failed = failures(turn, showed)
    missed = {who: answer for who, answer in answers(turn).items() if _unpassed(answer, who, showed)}
    sources = unread(turn, showed)
    asked = [q for q in questions(turn, shown) if q not in looked]
    disputed = conflicts(turn, showed)
    no_date = no_next_date(turn)
    if not (failed or missed or sources or asked or disputed or no_date or promised or looked):
        return None
    parts = [MARK]
    if looked:
        parts.append("You asked {first} for this, or said it isn't there, before searching {first}'s things:\n" + "\n".join(
            f'- "{line}"' for line in looked) + "\nSearch first: call look_up with the names, places and dates from the message. "
            "It searches mail (every account, and Sent), texts, the calendar, Drive, Trello, the to-do list and saved "
            "facts at once. If what you find answers it, give that answer and drop the "
            "question, and close any card of yours it answers. If nothing turns up, keep the question and say in a few "
            "words where you looked.")
    if promised:
        parts.append("Your reply promises this for later, and nothing in this request set it up. You won't remember it on "
                     "your own:\n" + "\n".join(f'- "{line}"' for line in promised) + "\nSet it now, after a look at errands "
                     "list and cronjob_manage list: reminders add with a due time, or cronjob_manage (action create, a schedule, repeat 1 if it's one time, deliver local, a "
                     "prompt that stands alone), or errands wait to check back on a date. If a tool refused, use another "
                     "one. If {first} named no time, pick a sensible one. Then give the real time in your reply. If nothing "
                     "can be set, say that in place of the promise. Change nothing only if a job from an earlier request or "
                     "a task already running does it, or the line is in a draft for someone else.")
    if failed:
        parts.append("These calls failed and nothing later reached the same thing. If the error says how to fix it, do "
                     "that now. Otherwise try another route to the same result: a calendar event or your own scheduled "
                     "alert in place of a reminder, another account or tool for a read. Don't hand {first} setup steps "
                     "when another route does the job. If nothing works, name in one line what you couldn't do:\n" + "\n".join(
                         f'- {c["name"]}{" (" + c["source"] + ")" if c["source"] else ""}: "{c["error"][:200]}"'
                         for c in failed))
    if missed:
        parts.append("These teammates answered and your reply shows nothing of it. Pass on what {first} needs to know or "
                     "act on, or leave it out if it truly changes nothing:\n" + "\n".join(
                         f'- {who}: "{_one_line(answer)}"' for who, answer in missed.items()))
    if disputed:
        parts.append("A teammate flagged a conflict your reply leaves out. Settle it from the source if you can, or tell "
                     "{first} what it changes:\n" + "\n".join(f'- {who}: "{line}"' for who, line in disputed.items()))
    if no_date:
        parts.append("The request has a date and a card is waiting on {first}, and nothing is set to look again if he "
                     "doesn't answer it. Schedule your own recheck before the date, and say when it is.")
    if sources:
        parts.append(f"The request covers everything, and this request never read: {', '.join(sources)}. Read what you "
                     "can now. In one line, name any you didn't check, so the reply doesn't read as complete.")
    if asked:
        parts.append("You put these to {first}:\n" + "\n".join(_with_what_teammates_said(q, shown, turn) for q in asked) + "\nFor each one, decide from "
                     "what this request already gave you: can you draft it so he only has to approve it? Which choice "
                     "would you make, and why? Put the draft or your recommendation in the reply. Keep a question only "
                     "if he alone can answer it, and then still say what you'd do.")






    kept = _figures(showed)
    on_record = _on_record(given, kept)
    if failed or missed or sources or asked or disputed or no_date or looked:
        parts.append((f"Keep these figures as they are: {', '.join(sorted(kept))}. " if kept else "")
                     + "Don't work out a new estimate. A figure changes only if a tool result from this request shows the "
                       "new one" + (", or your saved facts or memory show the current one" if on_record else "") + ". If "
                       "you doubt a figure, say it's unverified. If a count in your reply disagrees with a list you read "
                       "in this request, correct it. In one line, say what you didn't check that a recommendation "
                       "depends on." + (f"\n{ON_RECORD}{', '.join(on_record)}" if on_record else ""))




    parts.append("Skip anything that was refused on purpose or waits on a card. Your reply was [SILENT]. Do what this "
                 "list calls for. Then reply with what {first} needs to hear, such as an answer you still owe. "
                 "Reply with only [SILENT] again only if a card you put up says it all or there is no news to give."
                 if chatter.empty(shown) else
                 "Skip anything that was refused on purpose or waits on a card. Then write the complete reply again, no "
                 "longer than the facts need: change or add only what this list calls for. Never answer this with "
                 "[SILENT], even if you put up a card just now."
                 if not any(len(text) >= _LONG_BESIDE for text in beside) else
                 "Skip anything that was refused on purpose or waits on a card. Your long message from earlier in this "
                 "request stays in {first}'s chat, and this reply takes the place of your last reply only. So don't "
                 "repeat that message. Write what your last reply said, plus only what this list adds or changes and "
                 "what you need from {first}. Never answer this with [SILENT], even if you put up a card just now.")
    return "\n\n".join(parts).replace("{first}", _first())


ON_RECORD = "Figures in your saved facts and memory: "
_ON_RECORD_MAX = 40


def given(bot_id: str, session_id: str) -> str:
    ""

    from . import briefing, facts, lineage
    try:
        home = briefing._home(bot_id)
        return "\n".join([facts.shown_text({session_id, lineage.root(bot_id, session_id) or ""}),
                          *(briefing._read(home / "memories" / name, 20000) for name in ("MEMORY.md", "USER.md"))])
    except Exception:
        log.warning("the final pass could not read what %s was given", bot_id, exc_info=True)
        return ""


def _on_record(given: str, kept: set[str]) -> list[str]:
    ""
    known, out = {f.replace(",", "") for f in kept}, []
    for found in _FIGURE.findall(given or ""):
        figure = " ".join(found.split())
        if figure.replace(",", "") not in known and figure not in out:
            out.append(figure)
    return out[:_ON_RECORD_MAX]


_FIGURE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?k?\b|\b\d[\d,]*(?:\.\d+)?\s?%", re.I)


def _figures(text: str) -> set[str]:
    ""
    return {" ".join(found.split()) for found in _FIGURE.findall(text or "")}


def _before_the_pass(turn: list[dict]) -> str:
    ""
    said = []
    for message in turn:
        if message.get("role") == "user" and str(message.get("content") or "").startswith(replycheck.NOTE):
            break
        if message.get("role") == "assistant":
            said.append(str(message.get("content") or ""))
    return " ".join(said)


def moved_figures(first: str, second: str, turn: list[dict]) -> list[str]:
    ""

    read = " ".join(str(m.get("content") or "") for m in turn if m.get("role") in ("tool", "user")
                    and not str(m.get("content") or "").startswith(replycheck.NOTE)).replace(",", "")

    on_record = {f.replace(",", "") for m in turn if m.get("role") == "user"
                 and str(m.get("content") or "").startswith(replycheck.NOTE)
                 for line in re.findall(rf"(?m)^{re.escape(ON_RECORD)}(.*)$", str(m.get("content") or ""))
                 for f in _figures(line)}
    known = {f.replace(",", "") for f in _figures(first) | _figures(_before_the_pass(turn))} | on_record
    return sorted(f for f in _figures(second) if f.replace(",", "") not in known
                  and not re.search(rf"(?<![\d.,]){re.escape(re.sub(r'[$,%k\s]', '', f.lower()))}(?![\d])", read))


def _first() -> str:
    from . import who
    return who.first()


def marked(note: str) -> str:
    ""
    return note if MARK in note else f"{MARK}\n{note}"


def _note_after(raw: list[dict], i: int) -> tuple[int | None, str]:
    ""
    following = next((n for n in range(i + 1, len(raw)) if raw[n].get("display_kind") != "hidden"), None)
    content = str(raw[following].get("content") or "") if following is not None else ""
    if following is None or raw[following].get("role") != "user" or not content.startswith(replycheck.NOTE):
        return None, ""
    return following, content


def quieted(raw: list[dict], i: int) -> bool:
    ""
    return QUIET_MARK in _note_after(raw, i)[1]


def pass_reply(raw: list[dict], i: int) -> int | None:
    ""
    following, content = _note_after(raw, i)
    if following is None or not (MARK in content or _STATUS_NOTE in content):
        return None
    for at in range(following + 1, len(raw)):
        message = raw[at]
        if replycheck.starts_turn(message):
            return None
        if (message.get("role") == "assistant" and not message.get("tool_calls")
                and not chatter.interrupted(message.get("content"))
                and not chatter.empty(chatter.without_hermes_notes(str(message.get("content") or "")))):
            return at
    return None


def _moved(raw: list[dict], i: int, at: int) -> list[str]:
    return moved_figures(str(raw[i].get("content") or ""), str(raw[at].get("content") or ""),
                         replycheck.current_turn(raw[:at + 1]))


def replaced(raw: list[dict], i: int) -> bool:
    ""

    at = pass_reply(raw, i)
    return at is not None and not _moved(raw, i, at)


def rejected(raw: list[dict], at: int) -> bool:
    ""

    for i in range(at - 1, -1, -1):
        if replycheck.starts_turn(raw[i]):
            return False
        if raw[i].get("role") == "assistant" and pass_reply(raw, i) == at:
            return bool(_moved(raw, i, at))
    return False
