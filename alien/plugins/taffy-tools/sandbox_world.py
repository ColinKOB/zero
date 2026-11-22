""



































import json
import re
from datetime import datetime
from pathlib import Path

NOTE = "This is the test copy of Colin's accounts."


def _home() -> Path | None:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return None


def current() -> dict | None:
    ""
    from . import gate
    if not gate._sandboxed(gate._bot()):
        return None
    home = _home()
    f = home / "sandbox-world.json" if home else None
    if not f or not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except ValueError:
        return {}


def _when(s: str) -> str:
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return s
    return d.strftime("%a %b %-d, %-I:%M %p").replace(":00 ", " ")


def calendar_text(world: dict) -> str:
    rows = sorted(world.get("calendar") or [], key=lambda e: e.get("start", ""))
    lines = [f"- {_when(e['start'])} to {_when(e['end']).split(', ')[-1]}: {e['title']}"
             + (f" ({e['where']})" if e.get("where") else "") + (" [all day]" if e.get("all_day") else "")
             for e in rows]
    return f"{NOTE} Every event on Colin's calendar in it:\n" + ("\n".join(lines) or "No events.")


def trello_text(world: dict) -> str:
    t = world.get("trello") or {}
    out = [f"{NOTE} Board \"{t.get('board', '')}\", every open card:"]
    for lst, cards in (t.get("lists") or {}).items():
        for c in cards:
            out.append(f"- [{lst}] {c['name']}" + (f" (due {c['due']})" if c.get("due") else "")
                       + (f": {c['desc']}" if c.get("desc") else ""))
    return "\n".join(out)


def mail_text(world: dict, query: str = "") -> str:
    hits = _search(world, query)
    lines = [f"- {m['id']} {m['date'][:10]} from {m['from']}: {m['subject']} | {m['body'][:200]}" for m in hits]
    return f"{NOTE} Matching messages:\n" + ("\n".join(lines) or "None.")





FILTER_WORDS = {"from", "subject", "the", "and", "or", "after", "before", "since", "older", "newer", "unread", "read",
                "inbox", "all", "mail", "label", "has", "attachment", "older_than", "newer_than"}
DATE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


def _search(world: dict, query: str) -> list[dict]:
    q = (query or "").lower()
    after = re.search(r"(?:after|since)[: ]+(\d{4}[-/]\d{1,2}[-/]\d{1,2})", q)
    before = re.search(r"before[: ]+(\d{4}[-/]\d{1,2}[-/]\d{1,2})", q)
    words = re.sub(r"(?:after|since|before)[: ]+\d{4}[-/]\d{1,2}[-/]\d{1,2}", " ", q)
    terms = [t for t in re.findall(r"[\w@.$-]+", words) if len(t) > 2 and ":" not in t
             and t not in FILTER_WORDS and not DATE.match(t)]
    mail = sorted(world.get("mail") or [], key=lambda m: m.get("date", ""), reverse=True)
    if after:
        mail = [m for m in mail if m.get("date", "")[:10] >= after.group(1).replace("/", "-")]
    if before:
        mail = [m for m in mail if m.get("date", "")[:10] < before.group(1).replace("/", "-")]
    if not terms:
        return mail
    return [m for m in mail if any(t in (m["from"] + " " + m["subject"] + " " + m["body"]).lower() for t in terms)]


def connectors(args: dict, world: dict) -> str:
    service = str(args.get("service") or "").strip().lower()
    if service == "calendar":
        return json.dumps({"service": service, "result": calendar_text(world)})
    if service == "trello":
        return json.dumps({"service": service, "result": trello_text(world)})
    if service == "gmail":
        return json.dumps({"service": service, "result": mail_text(world, args.get("request") or "")})
    if service == "drive" and "drive" in world:
        files = json.loads(drive({"action": "recent"}, world))["files"]
        return json.dumps({"service": service, "result": f"{NOTE} Every file in Drive:\n" + "\n".join(
            f"- {f['id']} {f['name']} ({f.get('kind') or 'file'}, changed {str(f.get('modified') or '')[:10]})"
            for f in files)})
    return json.dumps({"error": f"{service} isn't connected in this test.", "account_not_connected": True})


def email(args: dict, world: dict, session_id: str = "") -> str:
    action = str(args.get("action") or "search").lower()
    if action in ("search", "list"):
        found = _search(world, args.get("query") or "")
        return json.dumps({"account": "personal", "note": NOTE, "envelopes": [
            {"id": m["id"], "date": m["date"], "from": m["from"], "subject": m["subject"]} for m in found]})
    if action == "read":
        m = next((m for m in world.get("mail") or [] if m["id"] == str(args.get("id") or "")), None)
        return json.dumps(m or {"error": "No message with that id in the test copy."})
    if action == "draft" and practice():
        return draft_mail(args, world, session_id)
    if practice():
        return json.dumps({"error": "This is a practice mailbox. Reading and drafting work here; nothing can be sent."})
    return json.dumps({"error": "Only reading mail works in this test."})


def practice() -> bool:
    ""
    from . import gate
    return gate._practice(gate._bot())


def draft_mail(args: dict, world: dict, session_id: str) -> str:
    ""


    from . import email_tool, mac
    try:
        if str(args.get("open_question") or "").strip():
            raise email_tool.Invalid("Ask the question separately and wait for the answer before saving the draft.")
        m = {"account": "practice", "folder": "INBOX", "to": email_tool._people(args.get("to"), "to"),
             "cc": email_tool._people(args.get("cc"), "cc"), "subject": email_tool._text(args, "subject", 300),
             "body": str(args.get("body") or "").replace("\r\n", "\n"),
             "in_reply_to": email_tool._text(args, "in_reply_to", 64)}
    except email_tool.Invalid as e:
        return json.dumps({"error": str(e)})
    card = {"account": m["account"], "folder": m["folder"], "from": str(world.get("me") or ""),
            "to": [{"name": n, "addr": a} for n, a in m["to"]], "cc": [{"name": n, "addr": a} for n, a in m["cc"]],
            "subject": m["subject"], "body": m["body"], "in_reply_to": m["in_reply_to"], "attach": [],
            "attach_names": [], "content_hash": "", "draft_id": "", "saved_in": "Drafts",
            "draft_card": args.get("draft_card"), "version": args.get("version")}
    reply = mac._call("mail_card", card, 15, session_id=session_id, area="chat")
    out = {"done": "saved as a draft", "account": "practice", "to": email_tool._shown(m["to"]),
           "cc": email_tool._shown(m["cc"]), "subject": m["subject"]}
    if reply.get("ok"):

        out["note"] = ("Colin has it as a card in your chat with Edit and Send, waiting there for his tap on Send. "
                       "Nothing was sent. When you tell him, say the draft is waiting in your chat to send. For a "
                       "revision, reply with one short line about what changed. For a new draft, reply with one short "
                       "line saying who the draft is for, without the subject or the text, and don't send it: his tap "
                       "on Send is the okay.")
    elif args.get("draft_card"):
        out = {"error": str(reply.get("error") or "The draft card could not be updated."),
               "current_drafts": reply.get("current_drafts") or []}
    else:
        out = {"error": str(reply.get("error") or "The draft could not be shown as a card.")}
    return json.dumps(out, ensure_ascii=False)


def _conversations(world: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in sorted(world.get("texts") or [], key=lambda t: str(t.get("date") or "")):
        out.setdefault(str(t.get("conversation") or t.get("from") or ""), []).append(t)
    return out


def _others(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(str(t.get("from") or "") for t in rows if str(t.get("from") or "").lower() != "me"))


def messages(args: dict, world: dict, session_id: str = "") -> str | None:
    ""


    if "texts" not in world:
        return None
    action, convos = str(args.get("action") or ""), _conversations(world)
    if action == "draft":
        if not practice():
            return json.dumps({"error": "Only reading texts works in this test."})
        key, to = str(args.get("conversation") or "").strip(), str(args.get("to") or "").strip()
        if key and key not in convos:
            return json.dumps({"error": "No conversation with that id in the test copy. Use one from recent or search."})
        people = [{"handle": f"practice:{n}", "name": n} for n in (_others(convos[key]) if key else [to])]
        from . import mac
        reply = mac._call("text_card", {"conversation": key, "to": to, "text": str(args.get("text") or ""),
                                        "practice_people": people}, 40, session_id=session_id, area="chat")
        if not reply.get("ok"):
            return json.dumps({"error": reply.get("error") or "The text card could not be made."}, ensure_ascii=False)
        note = ("Colin sees the text as a card in his chat with Edit and Send. Nothing was sent. Reply with one short "
                "line saying who it's for, without repeating the text: his tap on Send is the okay, and it sends.")
        if reply.get("revised"):
            note = "The open card now has the new words. Nothing was sent. Reply with one short line about what changed."
        return json.dumps({"result": {"card": "in the chat", "note": note}}, ensure_ascii=False)
    if action == "read":
        rows = convos.get(str(args.get("conversation") or ""))
        if rows is None:
            return json.dumps({"error": "No conversation with that id in the test copy."})
        return json.dumps({"result": {"note": NOTE, "conversation": {"id": args.get("conversation"),
                                                                    "people": [{"name": n} for n in _others(rows)]},
                                      "messages": [{"when": t.get("date"), "from": t.get("from"), "text": t.get("text")}
                                                   for t in rows[-int(args.get("limit") or 30):]]}}, ensure_ascii=False)
    terms = [w for w in re.findall(r"[\w']+", str(args.get("query") or "").lower()) if len(w) > 2]
    found = []
    for key, rows in convos.items():
        hits = [t for t in rows if any(w in (str(t.get("from")) + " " + str(t.get("text"))).lower() for w in terms)]
        if action == "search" and not hits:
            continue
        last = (hits if action == "search" else rows)[-1]
        found.append({"conversation": key, "name": ", ".join(_others(rows)), "when": last.get("date"),
                      "from": last.get("from"), "text": last.get("text")})
    found.sort(key=lambda c: str(c["when"] or ""), reverse=True)
    return json.dumps({"result": {"note": NOTE, "conversations": found}}, ensure_ascii=False)


def todo_text(args: dict, world: dict) -> str | None:
    ""


    if "needs_you" not in world or str(args.get("action") or "read").lower() != "read":
        return None
    lines = []
    for n, row in enumerate(world["needs_you"] or [], 1):
        kind = {"deadline": "a real deadline", "pace": "a pace date from the study plan, not a deadline"}.get(
            str(row.get("kind") or ""), "")
        due = f" Date: {row['due']}" + (f" ({kind})" if kind else "") + "." if row.get("due") else " No date."
        lines.append(f"[n{n}] {row.get('title', '')}.{due} From: {row.get('from') or 'the list'}."
                     + (" Done." if row.get("done") else ""))
    return f"{NOTE} Every row in Colin's Needs You list:\n" + ("\n".join(lines) or "Nothing is on the list.")


def calendar(args: dict, world: dict) -> str:
    if str(args.get("action") or "list").lower() in ("list", "search", "read", "get"):
        return json.dumps({"result": calendar_text(world)})
    return json.dumps({"error": "Only reading the calendar works in this test."})


def course_status(args: dict, world: dict) -> str:
    ""
    if not world.get("courses"):
        return json.dumps({"error": "No course site in this test."})
    return json.dumps({"result": world["courses"]})


def trello(args: dict, world: dict) -> str:
    return json.dumps({"result": trello_text(world)})


def drive(args: dict, world: dict) -> str:
    ""
    files = sorted(world.get("drive") or [], key=lambda f: f.get("modified", ""), reverse=True)
    action = str(args.get("action") or "search").lower()
    if action == "read":
        f = next((f for f in files if str(args.get("file") or "") in (f.get("id"), f.get("name"))), None)
        return json.dumps(f or {"error": "No file with that id in the test copy."})
    if action == "search":
        terms = [t for t in re.findall(r"[\w.$-]+", str(args.get("query") or "").lower()) if len(t) > 1]
        files = [f for f in files if terms and all(t in (f.get("name", "") + " " + f.get("text", "")).lower()
                                                   for t in terms)]
    return json.dumps({"note": NOTE, "files": [{k: f.get(k) for k in ("id", "name", "kind", "modified")}
                                               for f in files[:20]]})


def recall(args: dict, world: dict) -> str | None:
    ""
    if "facts" not in world:
        return None
    terms = [t for t in re.findall(r"[\w.$-]+", str(args.get("query") or "").lower()) if len(t) > 2]
    found = [str(f.get("text") if isinstance(f, dict) else f) for f in world.get("facts") or []]
    found = [f for f in found if any(t in f.lower() for t in terms)]
    if not found:
        return json.dumps({"result": f"Nothing saved matches \"{args.get('query') or ''}\"."})
    return json.dumps({"result": "Saved facts (newest wins when two disagree):\n" +
                                 "\n".join(f"[{i}] {f}" for i, f in enumerate(found, 1))})


def _mine(m: dict, world: dict) -> bool:
    sender = str(m.get("from") or "").lower()
    me = str(world.get("me") or "").lower()
    return bool(me and me in sender)


def _mail_hits(world: dict, terms: list[str], every: bool) -> list[dict]:
    ""

    def hay(m):
        return " ".join(str(m.get(k) or "") for k in ("from", "to", "subject", "body")).lower()
    test = all if every else any
    return [m for m in world.get("mail") or [] if terms and test(t in hay(m) for t in terms)]


def _envelopes(mail: list[dict], world: dict) -> str:
    return json.dumps({"envelopes": [{"id": m["id"], "date": m["date"], "from": m.get("from", ""),
                                      "to": m.get("to", ""), "subject": m.get("subject", ""),
                                      **({"status": "sent"} if _mine(m, world) else {})} for m in mail]})


def look_up(m: dict, world: dict) -> dict:
    ""
    from . import lookup
    ws, items, not_searched = m["words"], [], []
    contacts = [{"name": c.get("name", ""), **{k: [{"value": v} for v in c.get(k) or []]
                                                for k in ("phones", "emails", "addresses")}}
                for c in world.get("contacts") or [] if lookup.hits(c.get("name", ""), ws)]
    raw = recall({"query": m["query"]}, world)
    facts = lookup.fact_lines(raw, ws) if raw else []
    emails, phones = lookup.addresses_in(contacts, facts, ws)
    mail = _mail_hits(world, ws, True)
    loose = not mail and len(ws) > 1
    if loose:
        mail = _mail_hits(world, ws[:3], False)
    mail += [x for e in emails for x in _mail_hits(world, [e], True) if x not in mail]
    found = lookup.mail_items(_envelopes(mail, world), "personal", "INBOX", ws)

    for it in list(found):
        base = lookup.base_subject(it["subject"])
        newer = [x for x in world.get("mail") or [] if x not in mail and lookup.base_subject(x.get("subject")) == base
                 and lookup.stamp(x.get("date")) > it["when"]]
        for extra in lookup.mail_items(_envelopes(newer, world), "personal", "INBOX", ws):
            extra["found_by"] = "thread check"
            found.append(extra)
            mail += [x for x in newer if x["id"] == extra["open"]["id"]]
    lookup.mark_threads(found)
    items += found
    if "texts" in world:
        texts = [t for t in world["texts"] if lookup.hits(" ".join(str(t.get(k) or "") for k in ("from", "text")), ws)
                 or any(p[-10:] in re.sub(r"\D", "", str(t.get("from") or "")) for p in phones)]
        items += lookup.text_items(json.dumps({"messages": [{**t, "when": t.get("date")} for t in texts]}), ws)
    else:
        not_searched.append({"place": "texts", "why": "There are no texts in this test copy."})
    events = [{"title": e.get("title"), "location": e.get("where"), "start": e.get("start"), "calendar": "test"}
              for e in world.get("calendar") or []]
    items += lookup.event_items(json.dumps({"events": events}), "test", ws)
    cards = [{"id": c.get("name"), "name": c.get("name"), "list": lst, "due": c.get("due") or None,
              "description": c.get("desc", "")}
             for lst, cs in ((world.get("trello") or {}).get("lists") or {}).items() for c in cs
             if lookup.hits(c.get("name", "") + " " + c.get("desc", ""), ws)]
    items += lookup.trello_items(json.dumps({"cards": cards}), ws)
    if "drive" in world:
        files = [f for f in world["drive"] if lookup.hits(f.get("name", "") + " " + f.get("text", ""), ws)]
        items += lookup.drive_items(json.dumps({"files": files}), ws)
    else:
        not_searched.append({"place": "drive", "why": "There is no Drive in this test copy."})
    todo = [str(t) for t in world.get("todo") or [] if lookup.hits(str(t), ws)]
    searched = ["mail", "calendar", "trello"] + [p for p in ("texts", "drive") if p in world] + \
        [p for p, k in (("saved facts", "facts"), ("contacts", "contacts"), ("to-do list", "todo")) if k in world]
    return lookup.answer(m, items, contacts, facts, todo, searched=searched, not_searched=not_searched, loose=loose,
                         note=NOTE)
