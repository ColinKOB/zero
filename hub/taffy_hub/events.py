""







import json
import threading

from . import mailcard, openloops, store

KIND = "event"
MEMORY_ACTIONS = ("add", "replace", "remove")


def routine_created(bot_id: str, name: str, when: str = "") -> int:
    return store.add_feed(KIND, bot_id, None, "Added a scheduled job",
                          json.dumps({"event": "routine", "name": name, "when": when}),
                          written_by="hub", written_for="record")


def memory_updated(bot_id: str) -> int:
    return store.add_feed(KIND, bot_id, None, "Updated memory", json.dumps({"event": "memory"}),
                          written_by="hub", written_for="record")


def stopped(bot_id: str, line: str, at: float | None = None) -> int:
    ""
    return store.add_feed(KIND, bot_id, None, "Stopped", json.dumps({"event": "stopped", "name": line}), at=at,
                          written_by="hub", written_for="record")


_sent_lock = threading.Lock()


def mail_sent(bot_id: str, args: dict) -> int:
    ""



    message_id = mailcard._one_line(args.get("message_id"), 200)
    if not message_id:
        raise mailcard.MailError("A sent message needs its id.")
    mail = {"state": "sent", "from": mailcard._one_line(args.get("from"), 200),
            "to": mailcard._people(args.get("to"), "To"), "cc": mailcard._people(args.get("cc"), "Cc"),
            "subject": mailcard._one_line(args.get("subject"), mailcard.SUBJECT_MAX),
            "body": str(args.get("body") or "").replace("\r\n", "\n")[:mailcard.BODY_MAX],
            "attach_names": [mailcard._one_line(n, 200) for n in args.get("attach_names") or []][:10]}
    if not mail["to"]:
        raise mailcard.MailError("A sent message needs a recipient.")
    card = args.get("card")
    account = mailcard._one_line(args.get("account"), 200)
    who = ", ".join(p["name"] or p["addr"] for p in mail["to"])
    body = {"event": "receipt", "action": "sent", "name": f"Sent to {who}",
            "message_id": message_id, "account": account, "thread": mailcard._one_line(args.get("thread"), 64),
            "card": int(card) if card else None, "mail": mail}
    with _sent_lock:
        from . import loopevents
        loopevents.record(bot_id, "mail_sent", f"{account}:{message_id}",
                          {"message": message_id, "account": account, "thread": str(args.get("thread") or ""),
                           "card": str(card or ""), "reply_to": str(args.get("reply_to") or ""),
                           "people": [openloops._key(p[k]) for p in mail["to"] + mail["cc"]
                                      for k in ("name", "addr") if p.get(k)],
                           "subject": mail["subject"], "to": mail["to"]})
        row = store.db().execute("SELECT id FROM feed WHERE kind=? AND bot_id=? AND json_extract(body, '$.action')='sent' "
                                 "AND json_extract(body, '$.message_id')=? "
                                 "AND COALESCE(json_extract(body, '$.account'), '')=?",
                                 (KIND, bot_id, message_id, account)).fetchone()
        if row:
            return row["id"]
        line = store.add_feed(KIND, bot_id, None, "Sent an email", json.dumps(body),
                              written_by="hub", written_for="record")
    mailcard.close_sent_copies(mail["to"] + mail["cc"], mail["subject"], str(args.get("reply_to") or ""),
                               int(card) if card else None)
    return line


def writes_memory(args) -> bool:
    ""
    if not isinstance(args, dict):
        return True
    ops = args.get("operations") if args.get("action") == "batch" else [args]
    return any((op or {}).get("action") in MEMORY_ACTIONS for op in (ops or [])) or args.get("action") == "batch"


def calendar_created(bot_id: str, args: dict) -> None:
    from . import loopevents
    data = {k: mailcard._one_line(args.get(k), 200) for k in ("calendar", "event", "title", "start", "end")}
    if not data["event"] or not data["calendar"]:
        raise ValueError("A calendar receipt needs its calendar and event ids")
    loopevents.record(bot_id, "calendar_created", f"{data['calendar']}:{data['event']}", data)
