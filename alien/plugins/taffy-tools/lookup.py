""





























import concurrent.futures
import contextvars
import datetime as dt
import email.utils
import json
import re
import time

from . import home

DEADLINE_SEC = 75
PERSON_WAIT_SEC = 25
THREAD_WAIT_SEC = 12
THREAD_CHECKS = 4
WORKERS = 12
PAGE = 10
WINDOW_DAYS = 60
CAPS = {"mail": 8, "texts": 5, "calendar": 5, "trello": 4, "drive": 4}
FACTS_MAX, TODO_MAX, CONTACTS_MAX = 5, 3, 2
GIST_MAX = 160
ADDRESSES_MAX, NUMBERS_MAX = 3, 2


_FILLER = {"the", "a", "an", "and", "or", "of", "for", "to", "my", "me", "about", "with", "from", "on", "in", "at",
           "re", "any", "anything", "what", "whats", "is", "was", "his", "her", "their", "email", "emails", "mail",
           "text", "texts", "message", "messages", "calendar", "trello", "drive", "card", "event", "file", "latest",
           "last", "newest", "recent", "colin", "colin's", "please", "find", "look", "up"}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w$.])\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w.])")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA = {
    "name": "look_up",
    "description": (
        "Search everything you may read for a person, topic or thing in one call: every mailbox (received and Sent), "
        "texts, the calendar, Trello, Drive, saved facts, Contacts and Colin's to-do list. For a person it also "
        "searches by their saved addresses and numbers. It answers newest first, each item with its place, date, who "
        "it is from or to, a one-line gist and what to open it with. Mail marks the newest message of each thread, "
        "and says when a folder that could hold a newer one couldn't be searched. "
        "Call it before you ask Colin for something or say it isn't there. A place it couldn't search is named in "
        "not_searched; never say something isn't there while a place that could hold it is listed."),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "The person, topic or thing in two to four plain words, like "
                                                   "'Jake invoice', 'Priya' or 'ENG 102 paper'. Mail must hold every "
                                                   "word, so a long list of words finds less. A phrase in double "
                                                   "quotes is searched as one phrase."},
        "after": {"type": "string", "description": "Optional: only things on or after this day, 2026-11-01."},
        "before": {"type": "string", "description": "Optional: only things on or before this day, 2026-11-09."},
    }, "required": ["query"]},
}


def schema() -> dict:
    return home.personalize_schema(SCHEMA)


class Invalid(ValueError):
    ""


def parse(args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    query = " ".join(str(args.get("query") or "").replace("\u201c", '"').replace("\u201d", '"').split())[:120]
    if len(query) < 2:
        raise Invalid("look_up needs a query: a person, topic or thing in a few words.")
    out = {"query": query, "words": words(query)}
    for key in ("after", "before"):
        value = str(args.get(key) or "").strip()
        if value and not _DATE.match(value):
            raise Invalid(f"{key} must be a day like 2026-11-01.")
        out[key] = value
    if out["after"] and out["before"] and out["after"] > out["before"]:
        raise Invalid("after must come before before.")
    return out


def words(query: str) -> list[str]:
    ""


    phrases = [" ".join(p.lower().split()) for p in re.findall(r'"([^"]+)"', query) if p.strip()]
    rest = re.sub(r'"[^"]+"', " ", query).replace('"', " ")
    found = [w for w in re.findall(r"[\w@.'+-]+", rest.lower()) if w.strip(".'-")]
    kept = [w for w in found if w not in _FILLER and len(w) > 1] or ([] if phrases else found)
    return list(dict.fromkeys(phrases + kept))[:6]




def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(home.timezone())
    except Exception:
        return dt.timezone.utc


def stamp(raw) -> str:
    ""

    s = str(raw or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    d = None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M%z", "%Y-%m-%d %H:%M:%S%z"):
            try:
                d = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                pass
    if d is None:
        try:
            d = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            return ""
    if d is None:
        return ""
    if d.tzinfo is not None:
        d = d.astimezone(_tz())
    return d.strftime("%Y-%m-%d %H:%M")


def _in_window(when: str, m: dict) -> bool:
    day = when[:10]
    if not day:
        return True
    return (not m["after"] or day >= m["after"]) and (not m["before"] or day <= m["before"])


def _day_before(day: str) -> str:
    return str(dt.date.fromisoformat(day) - dt.timedelta(days=1))


def _day_after(day: str) -> str:
    return str(dt.date.fromisoformat(day) + dt.timedelta(days=1))




def hits(text: str, ws: list[str]) -> int:
    ""
    low = str(text or "").lower()
    return sum(1 for w in ws if w in low)


def _gist(text: str) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= GIST_MAX else s[:GIST_MAX - 1].rstrip() + "…"


def _who(value) -> str:
    ""
    if isinstance(value, list):
        return ", ".join(x for x in (_who(v) for v in value[:3]) if x)
    if isinstance(value, dict):
        name, addr = str(value.get("name") or "").strip(), str(value.get("addr") or value.get("address") or "").strip()
        return f"{name} <{addr}>" if name and addr else name or addr
    return str(value or "").strip()





def mail_items(raw: str, account: str, folder: str, ws: list[str]) -> list[dict]:
    data = _json(raw)
    out = []
    for e in data.get("envelopes") or []:
        if not isinstance(e, dict):
            continue
        status = e.get("status")
        sender, to = _who(e.get("from")), _who(e.get("to"))
        if status == "sent":
            who = f"Colin's sent mail to {to}" if to else "Colin's sent mail"
        elif status == "draft":
            who = f"Colin's unsent draft to {to}" if to else "Colin's unsent draft"
        else:
            who = f"from {sender}" + (f" to {to}" if to else "")
        acc, fold = str(e.get("account") or account), str(e.get("folder") or folder)
        subject = str(e.get("subject") or "")
        out.append({"when": stamp(e.get("date")), "place": f"mail ({acc})", "who": who, "gist": _gist(subject),
                    "subject": subject, "account": acc,
                    "open": {"tool": "email", "action": "read", "account": acc, "folder": fold, "id": str(e.get("id"))},
                    "score": hits(f"{sender} {to} {subject}", ws)})
    return out


def text_items(raw: str, ws: list[str]) -> list[dict]:
    data = _json(raw)
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    out = []
    for t in (result or {}).get("messages") or []:
        if not isinstance(t, dict):
            continue
        sender = "Colin" if t.get("from") == "me" else str(t.get("from") or "")
        out.append({"when": stamp(t.get("when") or t.get("date")), "place": "texts", "who": f"from {sender}",
                    "gist": _gist(t.get("text")),
                    "open": {"tool": "messages", "action": "read", "conversation": str(t.get("conversation") or "")},
                    "score": hits(f"{sender} {t.get('handle') or ''} {t.get('text') or ''}", ws)})
    return out


def event_items(raw: str, label: str, ws: list[str]) -> list[dict]:
    data = _json(raw)
    out = []
    for e in data.get("events") or []:
        if not isinstance(e, dict):
            continue
        text = " ".join(str(e.get(k) or "") for k in ("title", "location", "notes"))
        score = hits(text + " " + " ".join(map(str, e.get("attendees") or [])), ws)
        if not score:
            continue
        where = f" ({e['location']})" if e.get("location") else ""
        out.append({"when": stamp(e.get("start")), "place": f"calendar ({label})", "who": "",
                    "gist": _gist(f"{e.get('title') or ''}{where}"),
                    "open": {"tool": "calendar", "action": "search", "calendar": str(e.get("calendar") or label),
                             "query": str(e.get("title") or "")[:80]}, "score": score})
    return out


def trello_items(raw: str, ws: list[str]) -> list[dict]:
    data = _json(raw)
    out = []
    for c in data.get("cards") or []:
        if not isinstance(c, dict):
            continue
        where = " / ".join(str(c[k]) for k in ("board", "list") if c.get(k))
        due = f", due {stamp(c['due'])}" if c.get("due") else ""
        out.append({"when": stamp(c.get("due")), "place": "trello", "who": "",
                    "gist": _gist(f"{c.get('name') or ''}" + (f" [{where}]" if where else "") + due
                                  + (" (done)" if c.get("due_complete") else "")),
                    "open": {"tool": "trello", "action": "card", "card": str(c.get("id") or c.get("url") or "")},
                    "score": hits(str(c.get("name") or "") + " " + str(c.get("description") or ""), ws) or 1})
    if not out and data.get("result"):
        out.append(_words_item("trello", data["result"], {"tool": "trello", "action": "search"}))
    return out


def drive_items(raw: str, ws: list[str]) -> list[dict]:
    data = _json(raw)
    out = []
    for f in data.get("files") or []:
        if not isinstance(f, dict):
            continue
        out.append({"when": stamp(f.get("modified")), "place": "drive", "who": ", ".join(f.get("owners") or []),
                    "gist": _gist(f"{f.get('name') or ''} ({f.get('kind') or 'file'})"),
                    "open": {"tool": "drive", "action": "read", "file": str(f.get("id") or f.get("link") or "")},
                    "score": hits(str(f.get("name") or ""), ws) or 1})
    if not out and data.get("result"):
        out.append(_words_item("drive", data["result"], {"tool": "drive", "action": "search"}))
    return out


def _words_item(place: str, text, open_with: dict) -> dict:
    ""
    return {"when": "", "place": place, "who": "", "gist": _gist(str(text)[:600]), "open": open_with, "score": 1}


def contact_entries(raw: str) -> list[dict]:
    data = _json(raw)
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    out = []
    for c in (result or {}).get("contacts") or []:
        if isinstance(c, dict):
            out.append({k: c[k] for k in ("name", "organization", "phones", "emails", "addresses", "birthday")
                        if c.get(k)})
    return out


def fact_lines(raw: str, ws: list[str]) -> list[str]:
    ""
    data = _json(raw)
    text = data.get("result") if isinstance(data.get("result"), str) else ""
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip().startswith(("[", "- ")) and not ln.strip().endswith(":")]
    return sorted(lines, key=lambda ln: -hits(ln, ws))


def todo_lines(raw: str, ws: list[str]) -> list[str]:
    data = _json(raw)
    text = data.get("result") if isinstance(data.get("result"), str) else ""
    return [ln.strip(" -") for ln in text.splitlines() if ln.strip() and hits(ln, ws)]


def _json(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"result": str(raw or "")}
    return data if isinstance(data, dict) else {"result": data}


def addresses_in(contacts: list[dict], facts: list[str], ws: list[str]) -> tuple[list[str], list[str]]:
    ""

    emails, phones = [], []
    for c in contacts:
        emails += [str(x.get("value") or "") for x in c.get("emails") or [] if isinstance(x, dict)]
        phones += [str(x.get("value") or "") for x in c.get("phones") or [] if isinstance(x, dict)]
    for line in facts:
        if hits(line, ws):
            emails += _EMAIL.findall(line)
            phones += [p.strip() for p in _PHONE.findall(line)]
    emails = list(dict.fromkeys(e.lower() for e in emails if _EMAIL.fullmatch(e)))

    phones = list(dict.fromkeys(re.sub(r"\D", "", p)[-10:] for p in phones if sum(ch.isdigit() for ch in p) >= 10))
    return emails[:ADDRESSES_MAX], phones[:NUMBERS_MAX]




def base_subject(subject: str) -> str:
    s, before = " ".join(str(subject or "").split()), None
    while s != before:
        before, s = s, re.sub(r"(?i)^(?:re|fwd?|aw|sv)\s*:\s*", "", s)
    return s.lower()


def mark_threads(mail: list[dict], unsure: dict[str, list[str]] | None = None) -> None:
    ""



    unsure = unsure or {}
    groups: dict[tuple, list[dict]] = {}
    for it in mail:
        if base_subject(it.get("subject", "")):
            groups.setdefault((it.get("account"), base_subject(it["subject"])), []).append(it)
    for (account, _base), group in groups.items():
        group.sort(key=lambda x: x["when"], reverse=True)
        top = group[0]
        if unsure.get(account):
            top["thread"] = (f"newest found, but {', '.join(dict.fromkeys(unsure[account]))} couldn't be searched, so "
                             "a newer message may be there")
        elif len(group) == 1:
            continue
        else:
            top["thread"] = "newest in this thread"
        for older in group[1:]:
            older["thread"] = (f"older: this thread has a newer message ({top['who']}, {top['when']}). Read the newest "
                               "before saying what is still open.")




class Run:
    ""

    def __init__(self, forward: dict, gate=None):
        self.forward, self.gate = forward, gate
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)
        self.calls: list[dict] = []
        self.refused: dict[str, str] = {}

    def submit(self, place: str, tool: str, args: dict, fn, kind: str = "") -> dict | None:
        if self.gate is not None:
            try:
                blocked = self.gate.pre_tool_call(tool, args, **self.forward)
            except Exception as e:
                blocked = {"action": "block", "message": f"the check before reading it failed ({str(e)[:120]})"}
            if blocked and blocked.get("action") == "block":
                self.refused.setdefault(place, _first_sentence(blocked.get("message") or "Not allowed."))
                return None
            if blocked and blocked.get("action") == "modify" and isinstance(blocked.get("args"), dict):
                args = {**args, **blocked["args"]}
        context = contextvars.copy_context()
        call = {"place": place, "tool": tool, "args": args, "kind": kind,
                "future": self.pool.submit(context.run, fn, args, **self.forward)}
        self.calls.append(call)
        return call

    def wait(self, calls: list[dict], until: float) -> None:
        pending = [c["future"] for c in calls if not c["future"].done()]
        if pending:
            concurrent.futures.wait(pending, timeout=max(0.0, until - time.monotonic()))

    def result(self, call: dict) -> tuple[str | None, str]:
        ""
        f = call["future"]
        if not f.done():
            return None, "still searching when the lookup had to answer"
        try:
            out = f.result()
        except Exception as e:
            return None, f"it failed: {str(e)[:160]}"
        if not call.get("posted") and self.gate is not None:
            call["posted"] = True
            try:
                self.gate.post_tool_call(call["tool"], call["args"], result=out, **self.forward)
            except Exception:
                pass
        err = _json(out).get("error")
        if err:
            return None, _first_sentence(str(err))
        return out, ""

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def _first_sentence(text: str) -> str:
    s = " ".join(str(text or "").split())
    m = re.match(r"(.{20,240}?[.!?])(?:\s|$)", s)
    return (m.group(1) if m else s[:240]).strip()


def look_up(args: dict, **kwargs) -> str:
    try:
        m = parse(args)
    except Invalid as e:
        return json.dumps({"error": str(e)})
    forward = {k: v for k, v in kwargs.items() if k in ("task_id", "parent_agent", "session_id")}
    from . import sandbox_world
    world = sandbox_world.current()
    from . import gate
    if world is not None:
        out = sandbox_world.look_up(m, world)
        refused = gate_check(m, forward, gate)
        if refused:
            out.setdefault("not_searched", []).extend(
                {"place": place, "why": f"not yours to read: {why}"} for place, why in refused.items())
        return json.dumps(out, ensure_ascii=False)
    run = Run(forward, gate)
    try:
        return json.dumps(_search_everything(m, run), ensure_ascii=False)
    finally:
        run.close()


def _mail_query(terms: list[str], m: dict) -> str:
    q = " ".join(f'"{t}"' if " " in t else t for t in terms)
    if m["after"]:
        q += f" after {_day_before(m['after'])}"
    if m["before"]:
        q += f" before {_day_after(m['before'])}"
    return q


def window(m: dict) -> tuple[str, str]:
    ""
    today = dt.date.today()
    return (m["after"] or str(today - dt.timedelta(days=WINDOW_DAYS)),
            m["before"] or str(today + dt.timedelta(days=WINDOW_DAYS)))


def calendar_read(start: str, end: str, ws: list[str]) -> dict:
    ""

    return {"service": "calendar", "read": {"what": "events", "start": start, "end": end, "words": ws}}


def word_calls(m: dict) -> list[tuple[str, str, dict]]:
    ""

    start, end = window(m)
    return [("calendar (Google)", "connectors", calendar_read(start, end, m["words"])),
            ("trello", "trello", {"action": "search", "query": m["query"]}),
            ("drive", "drive", {"action": "search", "query": m["query"]})]


def gate_check(m: dict, forward: dict, gate) -> dict[str, str]:
    ""


    refused = {}
    for place, tool, args in word_calls(m):
        try:
            out = gate.pre_tool_call(tool, args, **forward)
        except Exception as e:
            out = {"action": "block", "message": f"the check before reading it failed ({str(e)[:120]})"}
        if out and out.get("action") == "block":
            refused[place] = _first_sentence(out.get("message") or "Not allowed.")
    return refused


def _search_everything(m: dict, run: Run) -> dict:
    from . import apps_tool, calendar_tool, contacts_tool, email_tool, messages_tool, teammates, todolist, tools
    started = time.monotonic()
    deadline = started + DEADLINE_SEC
    ws, q = m["words"], m["query"]
    mailboxes, gaps = [], []
    for acc, a in email_tool.accounts().items():
        folders, skipped = email_tool._mail_folders(a)
        mailboxes += [(acc, folder) for folder in folders]
        gaps += [{"place": f"mail ({place})", "why": why, "account": acc} for place, why in skipped]

    def mail_search(args: dict, **kw) -> str:
        ""
        mm = email_tool.parse(args)
        email_tool._session.value = kw.get("session_id") or ""
        return json.dumps(email_tool._one_search(mm), ensure_ascii=False)

    def quiet(handler):
        ""
        return lambda args, **kw: handler(args, **kw, _quiet=True)

    def mail(terms: list[str], kind: str) -> list[dict]:
        return [c for acc, folder in mailboxes if (c := run.submit(
            f"mail ({acc} {folder})", "email", {"action": "search", "account": acc, "folder": folder,
                                        "query": _mail_query(terms, m), "page_size": PAGE}, mail_search, kind))]


    person = []
    if len(q.split()) <= 3:
        person.append(run.submit("contacts", "contacts", {"action": "find", "query": q}, contacts_tool.contacts_tool))
    facts = run.submit("saved facts", "recall", {"query": q}, teammates.handler("recall"))
    person.append(facts)
    by_words = mail(ws, "words")
    texts = [run.submit("texts", "messages", {"action": "search", "query": w, "limit": 15}, messages_tool.messages)
             for w in (ws[:2] if len(ws) > 1 else ws)]
    start, end = window(m)
    events = [run.submit(f"calendar ({cid})", "calendar", {"action": "list", "calendar": cid, "start": start,
                                                           "end": end}, calendar_tool.calendar_tool)
              for cid in calendar_tool.calendars()]
    worded = {place: (tool, args) for place, tool, args in word_calls(m)}
    if home.connectors_on():
        events.append(run.submit("calendar (Google)", *worded["calendar (Google)"], tools.connectors))
    trello = run.submit("trello", *worded["trello"], quiet(apps_tool.trello_tool))
    drive = run.submit("drive", *worded["drive"], quiet(apps_tool.drive_tool))
    todo = run.submit("to-do list", "app_todo_list", {"action": "read"}, todolist.todo_list)


    person = [c for c in person if c]
    run.wait(person + by_words, min(deadline, started + PERSON_WAIT_SEC))
    contacts = []
    for c in person:
        if c["place"] == "contacts":
            out, _ = run.result(c)
            contacts = contact_entries(out) if out else []
    fact_out = run.result(facts)[0] if facts else None
    fact_list = fact_lines(fact_out, ws) if fact_out else []
    emails, phones = addresses_in(contacts, fact_list, ws)
    by_address = [c for e in emails for c in mail([e], "address")]
    texts += [run.submit("texts", "messages", {"action": "search", "query": p, "limit": 15}, messages_tool.messages)
              for p in phones]
    found_by_words = any(_json(run.result(c)[0] or "{}").get("envelopes") for c in by_words)
    loose = [] if found_by_words or len(ws) < 2 else [c for w in ws[:3] for c in mail([w], "loose")]


    mail_calls = by_words + by_address + loose
    run.wait(mail_calls, deadline - THREAD_WAIT_SEC)
    items = _mail_from(run, mail_calls, ws)
    checks = _thread_checks(items, mailboxes, run, mail_search)
    run.wait([c for c in run.calls], deadline)
    items = _mail_from(run, mail_calls + checks, ws)
    unsure: dict[str, list[str]] = {}
    for c in mail_calls + checks:
        if run.result(c)[1]:
            unsure.setdefault(c["args"]["account"], []).append(f"{c['args']['account']} {c['args']['folder']}")
    for gap in gaps:
        unsure.setdefault(gap["account"], []).append(gap["place"][len("mail ("):-1])
    mark_threads(items, unsure)

    for c in texts:
        if c:
            out, _ = run.result(c)
            items += text_items(out, ws) if out else []
    for c in events:
        if c:
            out, _ = run.result(c)
            if out and c["tool"] == "connectors":
                items.append(_words_item("calendar (Google)", _json(out).get("result"),
                                         {"tool": "connectors", "service": "calendar"}))
            elif out:
                items += event_items(out, c["args"].get("calendar") or "", ws)
    for c, convert in ((trello, trello_items), (drive, drive_items)):
        if c:
            out, _ = run.result(c)
            items += convert(out, ws) if out else []
    todo_out = run.result(todo)[0] if todo else None
    return answer(m, items, contacts, fact_list, todo_lines(todo_out, ws) if todo_out else [],
                  searched=_places(run), loose=bool(loose),
                  not_searched=_missing(run) + [{"place": g["place"], "why": g["why"]} for g in gaps])


def _mail_from(run: Run, calls: list[dict], ws: list[str]) -> list[dict]:
    seen, out = set(), []
    for c in calls:
        raw, _ = run.result(c)
        if not raw:
            continue
        for it in mail_items(raw, c["args"]["account"], c["args"]["folder"], ws):
            key = (it["open"]["account"], it["open"]["folder"], it["open"]["id"])
            if key in seen:
                continue
            seen.add(key)
            if c["kind"] == "thread":
                it["found_by"] = "thread check"
            out.append(it)
    return out


def _thread_checks(items: list[dict], mailboxes: list[tuple], run: Run, mail_search) -> list[dict]:
    ""

    newest: dict[tuple, dict] = {}
    for it in sorted(items, key=lambda x: x["when"], reverse=True):
        key = (it["account"], base_subject(it["subject"]))
        if key[1] and key not in newest:
            newest[key] = it
    calls = []
    for (acc, base), it in list(newest.items())[:THREAD_CHECKS]:
        subject_words = sorted(set(re.findall(r"[A-Za-z0-9]{3,}", base)), key=len, reverse=True)[:4]
        if not subject_words or not it["when"]:
            continue
        query = " ".join(x for w in subject_words for x in ("subject", w, "and")) + f" after {_day_before(it['when'][:10])}"
        for a, folder in mailboxes:
            if a == acc:
                call = run.submit(f"mail ({acc} {folder})", "email", {"action": "search", "account": acc, "folder": folder,
                                                              "query": query, "page_size": PAGE}, mail_search, "thread")
                if call:
                    calls.append(call)
    return calls


def _places(run: Run) -> list[str]:
    done = []
    for c in run.calls:
        if run.result(c)[0] is not None and c["place"] not in done:
            done.append(c["place"])
    return done



_MAIL_KINDS = {"words": "the search for those words", "address": "the search by address",
               "loose": "the search for each word", "thread": "the check for newer messages in a thread"}


def _missing(run: Run) -> list[dict]:
    ""



    answered = set(_places(run))
    out, named = [], set()
    for c in run.calls:
        _, why = run.result(c)
        if not why:
            continue
        key = (c["place"], "")
        if c["place"] in answered:
            if c.get("kind") not in _MAIL_KINDS:
                continue
            why = f"{_MAIL_KINDS[c['kind']]}: {why}"
            key = (c["place"], why)
        if key not in named:
            named.add(key)
            out.append({"place": c["place"], "why": why})
    for place, why in run.refused.items():
        if place not in {p for p, _w in named} and place not in answered:
            out.append({"place": place, "why": f"not yours to read: {why}"})
    return out


def answer(m: dict, items: list[dict], contacts: list[dict], facts: list[str], todo: list[str], *,
           searched: list[str], not_searched: list[dict], loose: bool = False, note: str = "") -> dict:
    ""
    items = [it for it in items if _in_window(it["when"], m)]
    kept, cut = [], {}
    for kind, cap in CAPS.items():
        mine = [it for it in items if it["place"].split(" ")[0] == kind]

        mine.sort(key=lambda x: x["when"], reverse=True)
        mine.sort(key=lambda x: -x.get("score", 0))
        kept += mine[:cap]
        if len(mine) > cap:
            cut[kind] = len(mine) - cap
    kept.sort(key=lambda x: (x["when"] != "", x["when"]), reverse=True)
    shown = []
    for it in kept:
        row = {"when": it["when"] or "undated", "place": it["place"]}
        if it.get("who"):
            row["who"] = it["who"]
        row["gist"] = it["gist"]
        if it.get("thread"):
            row["thread"] = it["thread"]
        if it.get("found_by"):
            row["found_by"] = it["found_by"]
        row["open"] = it["open"]
        shown.append(row)
    out = {"query": m["query"], "items": shown}
    if contacts:
        out["contacts"] = contacts[:CONTACTS_MAX]
    if facts:
        out["saved_facts"] = facts[:FACTS_MAX]
    if todo:
        out["todo_list"] = todo[:TODO_MAX]
    out["searched"] = searched
    if not_searched:
        out["not_searched"] = not_searched
    if cut:
        out["cut"] = (", ".join(f"{n} more {k}" for k, n in cut.items()) +
                      " not shown. Search that place with its own tool to see them.")
    notes = []
    if loose:
        notes.append("No mail had every word, so mail with any of them is shown.")
    if not shown and not contacts and not facts and not todo:
        notes.append(home.personalize("Nothing matched in the places searched. Try other words (a last name, an "
                                      "address, a company) before telling Colin it isn't there."))
    if not_searched:
        notes.append("Don't say something isn't there while a place that could hold it is in not_searched.")
    if note:
        notes.append(note)
    if notes:
        out["note"] = " ".join(notes)
    return out
