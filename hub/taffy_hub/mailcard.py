""




















import email.utils
import json
import logging
import re
import threading
import time

from . import choices, config, consent, mailkey, plugintools, store, turnsummary, who

log = logging.getLogger("taffy.mailcard")

KIND = "mail"
SEND_WAIT_SEC = 180
BODY_MAX = 10000
PEOPLE_MAX = 20
SUBJECT_MAX = 300
_ADDR = re.compile(r"^[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+$")
UNSURE = "I couldn't confirm that this went out. Check your Sent folder before you send it again."
RETRY = "That didn't send. Tap Send to try again."
_PRIVATE = ("attach", "content_hash", "session_id", "draft_id", "req", "sending_at", "expect_reply", "reply_by", "reply_plan",
            "revised_at")




_lock = threading.RLock()


class MailError(Exception):
    pass


def _one_line(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _people(items, what: str) -> list[dict]:
    ""
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name, addr = _one_line(item.get("name"), 100), _one_line(item.get("addr"), 200)
        else:
            name, addr = email.utils.parseaddr(_one_line(item, 300))
            name = _one_line(name, 100)
        if not _ADDR.match(addr):
            raise MailError(f"{addr or what} is not an email address.")
        if addr.lower() not in {p["addr"].lower() for p in out}:
            out.append({"name": name, "addr": addr})
    if len(out) > PEOPLE_MAX:
        raise MailError(f"That is too many people in {what}.")
    return out


def _tuples(people: list[dict]) -> list[tuple[str, str]]:
    return [(p["name"], p["addr"]) for p in people]


def _shown(people: list[dict]) -> str:
    return ", ".join(email.utils.formataddr((p["name"], p["addr"])) if p["name"] else p["addr"] for p in people)


def approve_mail(bot_id: str, summary: str, target: str, inputs: str, fingerprint: str, session_id: str | None) -> int:
    ""

    rid = consent.action_request(bot_id, "send", "", summary, target, inputs, fingerprint, session_id, None, why="drafted")
    with consent.db() as c:
        c.execute("UPDATE requests SET status='allowed', decided=? WHERE id=? AND status='pending'", (time.time(), rid))
    return rid


def _row(feed_id: int, bot_id: str):
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?", (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise MailError("That draft is gone.")
    return row, json.loads(row["body"] or "{}")


def _save(row, card: dict) -> None:
    store.update_feed(row["id"], row["title"], json.dumps(card))


def offer(bot_id: str, session_id: str | None, args: dict, during_turn: bool, work_source: str = "") -> dict:
    ""


    if str(args.get("open_question") or "").strip():
        raise MailError("Ask the question separately and wait for the answer before saving the draft.")
    to, cc = _people(args.get("to"), "To"), _people(args.get("cc"), "Cc")
    subject, body = _one_line(args.get("subject"), SUBJECT_MAX), str(args.get("body") or "").replace("\r\n", "\n")
    if not to or not subject or not body.strip():
        raise MailError("A draft needs a recipient, a subject and some text.")
    if len(body) > BODY_MAX:
        raise MailError(f"The text is too long for a card. Keep it under {BODY_MAX:,} characters.")
    names = [str(n)[:200] for n in args.get("attach_names") or []][:10]
    paths = [str(p) for p in args.get("attach") or []][:10]
    card = {"state": "open", "version": 1, "account": str(args.get("account") or "")[:100], "folder": str(args.get("folder") or "INBOX")[:100],
            "from": _one_line(args.get("from"), 200), "to": to, "cc": cc, "subject": subject, "body": body,
            "in_reply_to": _one_line(args.get("in_reply_to"), 64), "attach": paths, "attach_names": names,
            "content_hash": _one_line(args.get("content_hash"), 80), "note": None,
            "draft_id": _one_line(args.get("draft_id"), 100), "saved_in": "Gmail" if args.get("saved_in") == "Gmail" else "Drafts",
            "session_id": session_id or None, "expect_reply": bool(args.get("expect_reply")),
            "reply_by": _one_line(args.get("reply_by"), 40), "reply_plan": _one_line(args.get("reply_plan"), 600)}
    if work_source:
        card["work_source"] = work_source

    with _lock:
        revised = _replace_open(bot_id, card, args)
        if revised:
            return revised
        already = _already_settled(bot_id, card)
        if already:
            return already

    def publish() -> int:
        with _lock:
            revised = _replace_open(bot_id, card, args)
            return revised["id"] if revised else store.add_feed(KIND, bot_id, None, subject, json.dumps(card),
                                                                written_by="zip", written_for="person")
    if during_turn:
        choices.defer(bot_id, publish, ask=f"Send this email? {subject}")
        return {"revised": False}
    return {"revised": False, "id": publish(), "version": 1}


def _same_draft(bot_id: str, new: dict):
    ""
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)).fetchall():
        try:
            old = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if old.get("account") != new.get("account"):
            continue
        if old.get("in_reply_to") or new.get("in_reply_to"):

            same = (old.get("in_reply_to") == new.get("in_reply_to") and old.get("folder") == new.get("folder"))
        else:
            same = (old.get("subject") == new.get("subject")
                    and {p["addr"].lower() for p in old.get("to", [])} == {p["addr"].lower() for p in new.get("to", [])})
        if same:
            return row, old
    return None


def _already_settled(bot_id: str, new: dict, now: float | None = None) -> dict | None:
    ""





    found = _same_draft(bot_id, new)
    if not found:
        return None
    row, old = found
    now = time.time() if now is None else now
    state = old.get("state")
    if state == "sending":
        said = (f"Card {row['id']} for this same email is being sent right now, after {who.possessive()} tap on Send. "
                "No new card was made. Don't draft or send it again; the hub tells you when it went.")
    elif state == "sent" and now - float(old.get("at") or 0) < SENT_WINDOW_SEC:
        said = (f"This same email already went out from card {row['id']} "
                f"{_when(float(old.get('at') or now), now)}. No new card was made. Don't send it again.")
    else:
        return None
    return {"revised": False, "id": row["id"], "settled": state, "said": said}


def _when(at: float, now: float) -> str:
    from . import calendar_noticers
    return calendar_noticers.when(at, now)


def _replace_open(bot_id: str, new: dict, args: dict) -> dict | None:
    found = _same_draft(bot_id, new)
    if found:
        row, old = found
        if old.get("state") != "open":
            return None
        if args.get("draft_card") is not None and args["draft_card"] != row["id"]:
            raise MailError("That draft changed. Read the current draft before revising it.")
        _check_version(old, args.get("version"))
        if _message_changed(old, new):
            _revise(old, new, "zip")
        else:
            new.update({k: old[k] for k in ("version", "previous", "revision_source", "req", "sending_at", "error") if k in old})
        _save(row, new)

        return {"revised": True, "id": row["id"], "version": new["version"], "replaced_draft_id": old.get("draft_id") or ""}
    return None


def _check_version(card: dict, version) -> None:
    current = int(card.get("version") or 1)

    if (version is None and current > 1) or (version is not None and version != current):
        raise MailError("This draft changed. Review the current text and try again.")


def _message_changed(old: dict, new: dict) -> bool:
    return any(old.get(k) != new.get(k) for k in ("from", "to", "cc", "subject", "body", "attach", "content_hash"))


def _revise(old: dict, new: dict, source: str) -> None:
    revoke(int(old.get("req") or 0))
    new.update(version=int(old.get("version") or 1) + 1, revised_at=time.time(),
               previous={k: old.get(k) for k in ("to", "cc", "subject", "body")}, revision_source=source, error=None)
    for key in ("req", "sending_at"):
        new.pop(key, None)


def edit(bot_id: str, feed_id: int, edits: dict) -> dict:
    ""
    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") != "open":
            raise MailError("That draft is no longer open.")
        _check_version(card, edits.get("version"))
        new = _edited(card, edits)
        if _message_changed(card, new):
            _revise(card, new, "person")
            _save(row, new)
        return new


NO_PARTS = "This email can't be changed here. Tap Not Now and ask for a new version."


def okay_mail(req_id: int) -> dict | None:
    ""

    keep = getattr(consent, "request_mail", None)
    try:
        mail = keep(int(req_id)) if keep else None
    except Exception:
        log.warning("could not read the email of request %s", req_id, exc_info=True)
        return None
    if not isinstance(mail, dict) or not mail.get("to") or not mail.get("subject"):
        return None
    return mail


def okay_mail_public(req_id: int) -> dict | None:
    ""
    mail = okay_mail(req_id)
    return {k: mail.get(k) for k in ("from", "to", "cc", "subject", "body", "attach_names")} if mail else None


def from_okay(req_id: int, edits: dict) -> tuple[str, int, dict]:
    ""







    from . import board, teams
    r = consent.get_request(int(req_id))
    if not r or r.get("status") != "pending" or (r.get("kind") or "") != "send":
        raise MailError("That request already moved on.")
    if r.get("test") or teams.member_of(r.get("bot_id") or ""):
        raise MailError(NO_PARTS)
    mail = okay_mail(req_id)
    if mail is None:
        raise MailError(NO_PARTS)
    bot_id = r["bot_id"]

    probe = {"to": _people(mail.get("to"), "To"), "cc": _people(mail.get("cc"), "Cc"),
             "subject": _one_line(mail.get("subject"), SUBJECT_MAX), "body": str(mail.get("body") or "")}
    _edited(probe, edits or {})
    with _lock:


        before = {row["id"]: (row["title"], row["body"], row["changed_at"]) for row in store.db().execute(
            "SELECT id, title, body, changed_at FROM feed WHERE kind=? AND bot_id=?", (KIND, bot_id))}
        closed = consent.withdraw(bot_id, int(req_id), any_zip=True)
        if not closed:
            raise MailError("That request already moved on.")
        try:
            args = dict(mail)
            same = _same_draft(bot_id, {**mail, "to": probe["to"]})
            if same and same[1].get("state") == "open":


                args.update(draft_card=same[0]["id"], version=int(same[1].get("version") or 1))
            offered = offer(bot_id, r.get("session_id"), args, during_turn=False)
            feed_id = offered["id"]
            current = int(_row(feed_id, bot_id)[1].get("version") or 1)
            card = edit(bot_id, feed_id, {**{k: v for k, v in (edits or {}).items() if k in ("to", "cc", "subject", "body")},
                                          "version": current})
        except Exception:


            with consent.db() as c:
                c.execute("UPDATE requests SET status='pending', decided=NULL WHERE id=? AND status='withdrawn'",
                          (int(req_id),))
            with store._lock:
                for row in store.db().execute("SELECT id, title, body, changed_at FROM feed WHERE kind=? AND bot_id=?",
                                              (KIND, bot_id)).fetchall():
                    if row["id"] not in before:
                        store.db().execute("DELETE FROM feed WHERE id=?", (row["id"],))
                    elif (row["title"], row["body"], row["changed_at"]) != before[row["id"]]:
                        store.db().execute("UPDATE feed SET title=?, body=?, changed_at=? WHERE id=?",
                                           (*before[row["id"]], row["id"]))
                store.db().commit()
            raise
    to = ", ".join(p["addr"] for p in card["to"])
    first = who.first()
    note = (f"{who.hub_note()} {first} is changing your email about \"{card['subject']}\" before it goes, so your send "
            f"was not made and its card is closed. The message is now a draft card in your chat, to {to}, which "
            f"{first} sends. Don't send it yourself and don't draft it again. You hear when it goes.")
    try:
        if r.get("task_id"):
            ok, _out = board.answer_block(r["task_id"], note)
            if not ok:
                board.comment(r["task_id"], note)
        else:
            store.add_card_note(bot_id, note)
    except Exception:
        log.warning("could not tell %s that its email is being changed", bot_id, exc_info=True)
    log.info("request %s became mail card %s for %s", req_id, feed_id, bot_id)
    return bot_id, feed_id, card


CONTEXT_PREFIX = "[Current mail drafts: "
OLDER_MAX = 8


OLDER_TEXT = 200


def drafts(bot_id: str) -> list[dict]:
    ""
    with _lock:
        drafts = []
        for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                      (KIND, bot_id)).fetchall():
            card = json.loads(row["body"] or "{}")
            if card.get("state") == "open":
                drafts.append({"draft_card": row["id"], "version": card.get("version", 1), **_warning(card),
                               **{k: card.get(k) for k in ("account", "folder", "to", "cc", "subject", "body", "in_reply_to",
                                                          "attach", "expect_reply", "reply_by", "reply_plan")}})
                if len(drafts) == 3:
                    break
    return drafts


def _warning(card: dict) -> dict:
    ""
    return {"card_says": card["error"]} if card.get("error") else {}


def _older_drafts(bot_id: str, newest: list[dict]) -> list[dict]:
    ""


    whole = {d["draft_card"] for d in newest}
    with _lock:
        rows = store.db().execute("SELECT * FROM feed WHERE kind=? ORDER BY id DESC", (KIND,)).fetchall()
    older = []
    for row in rows:
        mine = row["bot_id"] == bot_id
        if not mine and (bot_id != config.LEAD.id or config.is_sandbox(row["bot_id"])):
            continue
        card = json.loads(row["body"] or "{}")
        if card.get("state") == "open" and row["id"] not in whole:
            line = {"draft_card": row["id"], "version": card.get("version", 1), **_warning(card), "to": card.get("to"),
                    "subject": card.get("subject"), "starts": str(card.get("body") or "")[:OLDER_TEXT]}
            if not mine:
                teammate = config.BOTS_BY_ID.get(row["bot_id"])
                line["teammate"] = teammate.name if teammate else row["bot_id"]
            older.append(line)
    return older[:OLDER_MAX]


SENT_WINDOW_SEC = 24 * 3600
SENT_MAX = 8


def sent_lately(bot_id: str, now: float | None = None) -> list[dict]:
    ""



    from . import calendar_noticers
    now = now or time.time()
    out = []
    for row in store.db().execute("SELECT bot_id, created_at, body FROM feed WHERE kind='event' AND created_at>=? "
                                  "AND json_extract(body, '$.action')='sent' ORDER BY id DESC", (now - SENT_WINDOW_SEC,)):
        mine = row["bot_id"] == bot_id
        if not mine and (bot_id != config.LEAD.id or config.is_sandbox(row["bot_id"])):
            continue
        try:
            receipt = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        mail = receipt.get("mail") or {}


        line = {"to": ", ".join(p.get("addr") or "" for p in mail.get("to") or []),
                "subject": str(mail.get("subject") or "")[:100], "sent": calendar_noticers.when(row["created_at"], now),
                **{k: receipt[k] for k in ("account", "card", "thread") if receipt.get(k)},
                "starts": str(mail.get("body") or "")[:OLDER_TEXT]}
        if not mine:
            teammate = config.BOTS_BY_ID.get(row["bot_id"])
            line["teammate"] = teammate.name if teammate else row["bot_id"]
        out.append(line)
        if len(out) == SENT_MAX:
            break
    return out


def settled_lately(bot_id: str, now: float | None = None) -> list[dict]:
    ""



    now = time.time() if now is None else now
    out = []
    with _lock:
        rows = store.db().execute("SELECT * FROM feed WHERE kind=? AND created_at>=? ORDER BY id DESC",
                                  (KIND, now - 7 * SENT_WINDOW_SEC)).fetchall()
    for row in rows:
        mine = row["bot_id"] == bot_id
        if not mine and (bot_id != config.LEAD.id or config.is_sandbox(row["bot_id"])):
            continue
        card = json.loads(row["body"] or "{}")
        state, at = card.get("state"), float(card.get("sending_at") or card.get("at") or 0)
        if not (state == "sending" or (state == "dismissed" and card.get("declined"))) or now - at > SENT_WINDOW_SEC:
            continue
        line = {"draft_card": row["id"], "account": card.get("account") or "",
                "to": ", ".join(p.get("addr") or "" for p in card.get("to") or []), "subject": card.get("subject"),
                **({"answers_message": card["in_reply_to"]} if card.get("in_reply_to") else {}),
                "starts": str(card.get("body") or "")[:OLDER_TEXT],
                "outcome": ("being sent now after the tap on Send" if state == "sending" else
                            f"not sent: {who.first()} closed it without sending"),
                "when": _when(at, now)}
        if not mine:
            teammate = config.BOTS_BY_ID.get(row["bot_id"])
            line["teammate"] = teammate.name if teammate else row["bot_id"]
        out.append(line)
        if len(out) == SENT_MAX:
            break
    return out


def with_drafts(bot_id: str, text: str) -> str:
    ""

    _look_for_unsure()
    current = drafts(bot_id)
    older = _older_drafts(bot_id, current)
    sent = sent_lately(bot_id)
    ended = settled_lately(bot_id)
    from . import textcard
    texts = textcard.declined_lately(bot_id)
    if not current and not older and not sent and not ended and not texts:
        return text
    also = f". Also open, each still waiting for a Send: {json.dumps(older, ensure_ascii=True)}" if older else ""
    went = (f". Sent in the last day, so these went out; never call them unsent or waiting: "
            f"{json.dumps(sent, ensure_ascii=True)}" if sent else "")


    went += (f". Settled without a send receipt yet. Never draft or send again one that is being sent. One "
             f"{who.first()} closed without sending is not offered again as it was; write a new version only when "
             f"{who.first()} asks for one: {json.dumps(ended, ensure_ascii=True)}" if ended else "")
    went += (f". Texts {who.first()} chose not to send, so they did not go out; don't offer them again as they were, "
             f"and write a new one only when {who.first()} asks: {json.dumps(texts, ensure_ascii=True)}" if texts else "")

    turn_id, text = turnsummary.peel(text)
    return ((f"[Chat turn: {turn_id}]\n" if turn_id else "") + CONTEXT_PREFIX + json.dumps(current, ensure_ascii=True) + also +
            went + ". Context only, never send approval. For a requested revision, start from this saved text and call draft "
            "with draft_card and version, preserving unrelated edits. Reply with one short line about what changed. To retire "
            "your own draft that is no longer right, call close_card with its draft_card and a short why.]\n" + text)






SENT_LOOK_EVERY_SEC = 15 * 60
_sent_looks: dict[int, float] = {}


def _in_sent(bot_id: str, card: dict, since: float) -> bool:
    tool = plugintools.module("email_tool")
    addr = card["to"][0]["addr"]
    day = time.strftime("%Y-%m-%d", time.localtime(since - 86400))
    with plugintools.as_zip(bot_id):
        out = json.loads(tool.email_tool({"action": "search", "account": card["account"], "folder": "Sent",
                                          "query": f"to {addr} and after {day}", "page_size": 20}))
    if out.get("error"):
        raise RuntimeError(out["error"])
    want = " ".join(str(card.get("subject") or "").split()).lower()
    start = time.strftime("%Y-%m-%d %H:%M", time.gmtime(since - 60))


    return any(e.get("status") == "sent" and e.get("account", card["account"]) == card["account"]
               and " ".join(str(e.get("subject") or "").split()).lower() == want
               and tool._minute(e.get("date")) >= start
               for e in out.get("envelopes") or [] if isinstance(e, dict))


def look_in_sent(bot_id: str, feed_id: int) -> bool:
    ""
    with _lock:
        row, card = _row(feed_id, bot_id)
    if card.get("state") != "open" or card.get("error") != UNSURE or not card.get("to"):
        return False
    try:
        found = _in_sent(bot_id, card, float(card.get("sending_at") or row["created_at"]))
    except Exception:
        log.warning("could not look in Sent for mail card %s of %s", feed_id, bot_id, exc_info=True)
        return False
    if not found:
        return False
    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") != "open" or card.get("error") != UNSURE:
            return False
        card.update(state="sent", at=time.time(), error=None)
        _save(row, card)
    store.add_card_note(bot_id, f"Your mail card \"{card['subject'][:80]}\" to {_shown(card['to'])} is in your Sent "
                                "folder, so it went out and its card is closed. Don't send it again.")
    return True


def _look_soon(bot_id: str, feed_id: int) -> None:
    ""
    now = time.time()
    if not hub_sends() or now - _sent_looks.get(feed_id, 0) < SENT_LOOK_EVERY_SEC:
        return
    _sent_looks[feed_id] = now
    threading.Thread(target=look_in_sent, args=(bot_id, feed_id), daemon=True).start()


def _look_for_unsure() -> None:
    ""
    for row in store.db().execute("SELECT id, bot_id FROM feed WHERE kind=? AND json_extract(body, '$.state')='open' "
                                  "AND json_extract(body, '$.error')=?", (KIND, UNSURE)).fetchall():
        _look_soon(row["bot_id"], row["id"])


def public(card: dict) -> dict:
    ""
    out = {k: v for k, v in card.items() if k not in _PRIVATE}

    out["note"] = None
    out["can_edit"] = card.get("state") == "open"
    out["can_reopen"] = can_reopen(card)
    return out


def can_reopen(card: dict, now: float | None = None) -> bool:
    ""

    now = time.time() if now is None else now
    return card.get("state") == "dismissed" and bool(card.get("declined")) and \
        now - float(card.get("at") or 0) <= SENT_WINDOW_SEC


def revoke(req: int) -> None:
    ""
    with consent.db() as c:
        c.execute("UPDATE requests SET status='expired', decided=? WHERE id=? AND status='allowed' AND used IS NULL",
                  (time.time(), req))


def settle(row: dict) -> dict:
    ""
    with _lock:
        fresh = store.db().execute("SELECT * FROM feed WHERE id=?", (row["id"],)).fetchone()
        row = dict(fresh) if fresh else row
        card = json.loads(row.get("body") or "{}")
        if card.get("state") == "sending" and time.time() - float(card.get("sending_at") or 0) > SEND_WAIT_SEC:


            req = int(card.get("req") or 0)
            used = (consent.get_request(req) or {}).get("used")
            if not used:
                revoke(req)
            card.update(state="open", error=UNSURE if used else RETRY)
            store.update_feed(row["id"], row["title"], json.dumps(card))
            if used:
                _look_soon(row["bot_id"], row["id"])
        return card


def _edited(card: dict, edits: dict) -> dict:
    ""

    out = dict(card)
    if edits.get("to") is not None:
        out["to"] = _people(edits["to"], "To")
    if edits.get("cc") is not None:
        out["cc"] = _people(edits["cc"], "Cc")
    if edits.get("subject") is not None:
        out["subject"] = _one_line(edits["subject"], SUBJECT_MAX + 1)
        if len(out["subject"]) > SUBJECT_MAX:
            raise MailError("That subject is too long to send.")
    if edits.get("body") is not None:
        out["body"] = str(edits["body"]).replace("\r\n", "\n")
    if not out["to"] or not out["subject"] or not out["body"].strip():
        raise MailError("Add a recipient, a subject and some text before sending.")
    if len(out["body"]) > BODY_MAX:
        raise MailError("That message is too long to send from here.")
    return out


def _card_text(card: dict) -> str:
    ""
    lines = [f"From: {card.get('from') or card.get('account')}", f"To: {_shown(card['to'])}"]
    if card.get("cc"):
        lines.append(f"Cc: {_shown(card['cc'])}")
    lines.append(f"Subject: {card['subject']}")
    for name in card.get("attach_names") or []:
        lines.append(f"Attached: {name}")
    return "\n".join(lines) + "\n\n" + card["body"]


def fingerprint(card: dict) -> str:
    ""
    m = {"action": "send", "account": card["account"], "folder": card["folder"], "subject": card["subject"],
         "in_reply_to": card.get("in_reply_to") or "", "attach": card.get("attach") or [], "body": card["body"],
         "to": _tuples(card["to"]), "cc": _tuples(card.get("cc") or [])}
    return mailkey.mail_fingerprint(m, card.get("content_hash") or "")


def send(bot_id: str, feed_id: int, edits: dict, said: str = "") -> tuple[dict, str]:
    ""

    if config.is_sandbox(bot_id):
        raise MailError("This is a practice draft, so it is not sent.")
    with _lock:
        row, card = _row(feed_id, bot_id)
        card = settle(dict(row)) if card.get("state") == "sending" else card
        if card.get("state") != "open":
            raise MailError({"sent": "That draft was already sent.", "sending": "That draft is already on its way."}.get(
                card.get("state"), "That draft is closed."))
        _check_version(card, (edits or {}).get("version"))
        card = _edited(card, edits or {})
        to = ", ".join(p["addr"] for p in card["to"])
        everyone = ", ".join(dict.fromkeys(p["addr"] for p in card["to"] + (card.get("cc") or [])))
        req = approve_mail(bot_id, f"Email {to} about \"{card['subject']}\"", everyone, _card_text(card),
                                   fingerprint(card), card.get("session_id"))
        card.update(state="sending", sending_at=time.time(), error=None, req=req)
        _save(row, card)
        first = who.first()
        if said:
            note = (f"{CHAT_SEND_PREFIX}{first} wrote \"{said}\" under the mail card for \"{card['subject']}\" to {to}, the "
                    f"one open draft, so the hub sent it on as {who.possessive()} tap on Send. That is {who.possessive()} "
                    f"okay. Call the email tool now with action \"send\" and card {feed_id}, and nothing else: the card "
                    f"holds the exact message. Then reply with one short line saying it went out.]")
        else:
            note = (f"{who.hub_note()} {first} tapped Send on the mail card for \"{card['subject']}\" to {to}. That tap is {who.possessive()} "
                    f"okay. Call the email tool now with action \"send\" and card {feed_id}, and nothing else: the card holds "
                    f"the exact message, with any change {first} made. Then reply with one short line saying it went out.")
        return card, note


def hub_sends() -> bool:
    ""
    return plugintools.can_act_as_zip() and plugintools.module("email_tool") is not None


def _email_send(bot_id: str, feed_id: int, session_id: str) -> dict:
    ""


    tool = plugintools.module("email_tool")
    with plugintools.as_zip(bot_id):
        return json.loads(tool.email_tool({"action": "send", "card": feed_id}, session_id=session_id))


ATTACH_CHANGED = "An attached file changed after I drafted this, so I didn't send it. Ask me for a fresh draft."


def _attach_hash(bot_id: str, paths: list[str]) -> str:
    ""
    with plugintools.as_zip(bot_id):
        return plugintools.module("email_tool")._content_hash(paths)


def send_from_hub(bot_id: str, feed_id: int) -> dict:
    ""


    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") != "sending":
            return card
        req = int(card.get("req") or 0)

        with consent.db() as c:
            c.execute("UPDATE requests SET used=? WHERE id=? AND used IS NULL", (time.time(), req))
    if card.get("attach") and _attach_hash(bot_id, card["attach"]) != (card.get("content_hash") or ""):


        with _lock:
            row, card = _row(feed_id, bot_id)
            if card.get("state") == "sending" and int(card.get("req") or 0) == req:
                card.update(state="open", error=ATTACH_CHANGED)
                _save(row, card)
        return card
    try:
        out = _email_send(bot_id, feed_id, card.get("session_id") or "")
        state = "sent" if out.get("done") == "sent" else "failed"
    except Exception:

        log.exception("the hub's send of mail card %s for %s", feed_id, bot_id)
        out, state = {}, "unsure"


    card = done(bot_id, feed_id, state, attempt=req)
    if state == "sent" and card.get("state") == "sent":
        to = ", ".join(p["addr"] for p in card["to"])
        waiting = str(out.get("waiting") or "")
        wait = " The hub waits for their reply and wakes you when it comes." \
            if waiting and not waiting.startswith("Sent, but") else ""
        store.add_card_note(bot_id, f"{who.first()} tapped Send on your mail card for \"{card['subject']}\" to {to}, "
                                    f"and the hub sent it. It went out, so don't send it again.{wait}")

    elif state != "sent":
        log.warning("the hub's send of mail card %s for %s did not go: %s", feed_id, bot_id,
                    str(out.get("error") or "")[:300])
    return card


def sent_line(card: dict) -> str:
    ""


    first = next(iter(card.get("to") or []), {})
    name = (first.get("name") or "").split()

    named = (name[0] if len(name) <= 2 else " ".join(name)) if name else (first.get("addr") or "They")
    return f"Sent. {named} has it now."


def close_sent_copies(people: list[dict], subject: str, reply_to: str = "", sent_card: int | None = None) -> list[int]:
    ""





    sent = {p["addr"].lower() for p in people}
    if reply_to or not sent or not subject:
        return []
    closed = []
    with _lock:
        for row in store.db().execute("SELECT * FROM feed WHERE kind=? ORDER BY id", (KIND,)).fetchall():
            try:
                card = json.loads(row["body"] or "{}")
            except ValueError:
                continue
            if row["id"] == sent_card or card.get("state") != "open" or card.get("in_reply_to") \
                    or card.get("subject") != subject \
                    or {p["addr"].lower() for p in card.get("to", []) + card.get("cc", [])} != sent:
                continue
            revoke(int(card.get("req") or 0))
            card.update(state="dismissed", at=time.time())
            _save(row, card)
            closed.append((row["bot_id"], row["id"], card))
    for bot_id, _fid, card in closed:
        store.add_card_note(bot_id, f"Your draft \"{card['subject'][:80]}\" to {_shown(card['to'])} is closed: the "
                                    "same email already went out. Don't send it again.")
    if closed and hub_sends():
        threading.Thread(target=_drop_drafts, args=([(b, c) for b, _f, c in closed],), daemon=True).start()
    return [fid for _b, fid, _c in closed]


def _people_of(card: dict) -> set[str]:
    ""
    out = set()
    for p in (card.get("to") or []) + (card.get("cc") or []) + (card.get("people") or []):
        if isinstance(p, dict):
            for k in ("name", "addr", "handle"):
                v = " ".join(str(p.get(k) or "").split()).lower()
                if v:
                    out.add(v)
    if card.get("name"):
        out.add(" ".join(str(card["name"]).split()).lower())
    return out


def replaced_by_sent(bot_id: str, feed_id: int, card: dict, replaced_by) -> bool:
    ""


    try:
        other_id = int(replaced_by)
    except (TypeError, ValueError):
        return False
    from . import textcard
    row = store.db().execute("SELECT body FROM feed WHERE id=? AND bot_id=? AND kind IN (?, ?) AND id != ?",
                             (other_id, bot_id, textcard.KIND, KIND, feed_id)).fetchone()
    try:
        other = json.loads(row["body"] or "{}") if row else {}
    except ValueError:
        return False
    return other.get("state") == "sent" and bool(_people_of(card) & _people_of(other))


def drop_drafts_soon(cards: list[tuple[str, dict]]) -> None:
    if cards and hub_sends():
        threading.Thread(target=_drop_drafts, args=(cards,), daemon=True).start()


def _drop_drafts(cards: list[tuple[str, dict]]) -> None:
    ""

    tool = plugintools.module("email_tool")
    for bot_id, card in cards:
        m = {"account": card.get("account") or "", "folder": card.get("folder") or "INBOX", "subject": card["subject"],
             "in_reply_to": "", "to": _tuples(card["to"]), "draft_id": card.get("draft_id") or ""}
        try:
            with plugintools.as_zip(bot_id):
                tool._drop_draft(m)
        except Exception:
            log.warning("could not drop the Drafts copy of mail card for %s", bot_id, exc_info=True)





CHAT_SEND_PREFIX = "[Send from the chat: "
CHAT_SEND_WINDOW_SEC = 45 * 60
_PLAIN_SEND = re.compile(
    r"(?:(?:ok(?:ay)?|yes|yep|yeah|sure|great|perfect|nice|good|cool|lgtm|thanks|thank you|all good|"
    r"(?:(?:that|this|it) )?(?:looks|sounds|reads) (?:good|great|fine|perfect|right)|"
    r"(?:that's|that is|it's|it is) (?:good|great|fine|perfect)) ?[,.!-]? )*"
    r"(?:(?:please|go ahead and|you can|just) )*"
    r"send(?: (?:it|that|this|the (?:draft|email|mail|message|reply|note)))?(?: (?:off|out|over|away))?"
    r"(?:,? (?:now|please|thanks|thank you|go ahead))*[.!]*")


def plain_send(words: str) -> bool:
    ""
    t = " ".join(str(words or "").replace("’", "'").lower().replace(",", ", ").split()).replace(" ,", ",")
    return bool(t) and _PLAIN_SEND.fullmatch(t) is not None


def send_said(bot_id: str, words: str, before: float, now: float | None = None) -> str | None:
    ""




    if not plain_send(words):
        return None
    since = (now or time.time()) - CHAT_SEND_WINDOW_SEC
    with _lock:
        rows = store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)).fetchall()
        cards = [(r, json.loads(r["body"] or "{}")) for r in rows]


        recent_open = [(r, c) for r, c in cards
                       if c.get("state") == "open" and since <= max(r["created_at"], r["changed_at"] or 0) <= before]
        if not cards or len(recent_open) != 1 or recent_open[0][0]["id"] != cards[0][0]["id"] \
                or recent_open[0][1].get("error") not in (None, "", RETRY) or _newer_card(bot_id, recent_open[0][0]):
            return None
        row, card = recent_open[0]
        try:
            return send(bot_id, row["id"], {"version": int(card.get("version") or 1)}, said=" ".join(words.split()))[1]
        except MailError:
            return None




_CHAT_WORDS = (KIND, "message", "message_local", "checkin", "action")


def _newer_card(bot_id: str, row) -> bool:
    ""

    if store.db().execute(f"SELECT 1 FROM feed WHERE bot_id=? AND id>? AND kind NOT IN ({','.join('?' * len(_CHAT_WORDS))}) "
                          "LIMIT 1", (bot_id, row["id"], *_CHAT_WORDS)).fetchone():
        return True
    with consent.db() as c:
        return c.execute("SELECT 1 FROM requests WHERE bot_id=? AND status='pending' AND COALESCE(raised,created)>=? "
                         f"AND COALESCE(test,0)=0 AND {consent.VISIBLE_REQUEST_SQL} LIMIT 1",
                         (bot_id, row["created_at"])).fetchone() is not None


def dismiss(bot_id: str, feed_id: int) -> dict:
    ""

    card, closed = close(bot_id, feed_id)
    if closed:
        with _lock:
            row, card = _row(feed_id, bot_id)
            card["declined"] = True
            _save(row, card)
    return card


def reopen(bot_id: str, feed_id: int) -> dict:
    ""

    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") == "open":
            return card
        if not can_reopen(card):
            raise MailError("This draft can't be brought back now.")
        card.pop("declined", None)
        card.update(state="open", at=time.time())
        _save(row, card)
        return card


def close(bot_id: str, feed_id: int) -> tuple[dict, bool]:
    ""

    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") != "open":
            return card, False
        card.update(state="dismissed", at=time.time())
        _save(row, card)
        return card, True


def checked(bot_id: str, feed_id: int, version: int, close: bool, line: str) -> bool:
    ""


    with _lock:
        row, card = _row(feed_id, bot_id)
        if card.get("state") != "open" or int(card.get("version") or 1) != version or card.get("error") == UNSURE:
            return False
        card.update({"state": "dismissed", "at": time.time()} if close else {"error": line})
        _save(row, card)
    return True


def for_send(bot_id: str, feed_id: int) -> dict:
    ""
    with _lock:
        row, card = _row(feed_id, bot_id)
    if card.get("state") != "sending":


        raise MailError(f"Still waiting, nothing failed: {who.first()} has not tapped Send on that card yet. When "
                        f"{who.first()} does, it goes out without you and you hear back after. End your turn now, and "
                        "don't call send for this card again." if card.get("state") == "open"
                        else "That card is not waiting to be sent.")
    return {"id": feed_id, "account": card["account"], "folder": card["folder"], "from": card.get("from") or "",
            "to": [_shown([p]) for p in card["to"]],
            "cc": [_shown([p]) for p in card.get("cc") or []], "subject": card["subject"], "body": card["body"],
            "in_reply_to": card.get("in_reply_to") or "", "attach": card.get("attach") or [],
            "draft_id": card.get("draft_id") or "", "attempt": card.get("req") or 0,
            "expect_reply": bool(card.get("expect_reply")), "reply_by": card.get("reply_by") or "",
            "reply_plan": card.get("reply_plan") or ""}


def done(bot_id: str, feed_id: int, state: str, error: str = "", attempt: int | None = None) -> dict:
    ""


    with _lock:
        row, card = _row(feed_id, bot_id)
        if attempt is not None and int(card.get("req") or 0) != int(attempt):
            return card
        waiting = card.get("state") == "sending"
        unsure = card.get("state") == "open" and bool(card.get("error"))
        if state == "sent" and (waiting or unsure):
            card.update(state="sent", at=time.time(), error=None)
        elif state == "unsure" and waiting:
            card.update(state="open", error=UNSURE)
            _look_soon(bot_id, feed_id)
        elif state != "sent" and waiting:
            revoke(int(card.get("req") or 0))
            card.update(state="open", error=RETRY)
        else:
            return card
        _save(row, card)
        return card


def title_for(card: dict) -> str:
    return f"Draft reply: {card.get('subject') or ''}".strip()
