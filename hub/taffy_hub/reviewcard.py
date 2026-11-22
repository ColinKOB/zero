""











import ast
import asyncio
import email.utils
import json
import logging
import re
import time

from . import blockask, config, store, who

log = logging.getLogger("taffy.reviewcard")

_ADDRESS = re.compile(r"^[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+$")
_HEADER = re.compile(r"(?<![-\w])(?:To|Cc):[ \t]*(.+?)(?=\s+Subject:|\s+Cc:|\n|$)", re.I)
_DRAFT_NOTE = re.compile(r"\s*\(draft \d+[^)]*\)", re.I)
_VERBS = {"has": "have", "is": "are", "was": "were", "does": "do"}
_PRONOUNS = {"himself": "yourself", "his": "your", "him": "you", "he": "you"}
_PRONOUN = re.compile(r"\b(?:himself|his|him|he)\b", re.I)


def _metadata(raw) -> dict:
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _people(text: str) -> list[tuple[str, str]]:
    out = []
    for name, addr in email.utils.getaddresses([text.strip().rstrip(".")]):
        if _ADDRESS.match(addr) and addr.lower() not in {a.lower() for _, a in out}:
            out.append((name.strip(), addr))
    return out


def recipients(note: str, metadata: dict) -> list[tuple[str, str]]:
    ""
    found = [p for m in _HEADER.finditer(note or "") for p in _people(m.group(1))]
    to = metadata.get("to")
    filed = _people(", ".join(str(x) for x in to) if isinstance(to, (list, tuple)) else str(to or ""))
    seen = {a.lower() for _, a in found}
    return found + [p for p in filed if p[1].lower() not in seen]


def to_you(text: str) -> str:
    ""
    name = re.escape(who.first())

    def line(s: str) -> str:
        if re.fullmatch(rf"\s*{name}(?: [\w'.-]+)?\s*", s):
            return s

        s = re.sub(rf"[^.!?]*\b{name}\b[^.!?]*", lambda m: _PRONOUN.sub(lambda p: _PRONOUNS[p[0].lower()], m[0]), s)
        s = re.sub(rf"\b{name}['’]s\b", "your", s)
        s = re.sub(rf"\b{name} (has|is|was|does)\b", lambda m: f"you {_VERBS[m[1]]}", s)
        s = re.sub(rf"\b{name} (needs|wants|knows|sends|asks|signs|logs|places|says)\b", lambda m: f"you {m[1][:-1]}", s)
        s = re.sub(rf"\b{name}\b(?! [A-Z]['’])", "you", s)
        return re.sub(r"(^|[.!?:]\s+)(you|your)\b", lambda m: m[1] + m[2].capitalize(), s)
    return "\n".join(line(s) for s in str(text or "").split("\n"))


def _still_open(metadata: dict) -> str:
    ""
    blanks = metadata.get("blanks")
    if isinstance(blanks, str) and blanks.startswith("["):
        try:
            blanks = ast.literal_eval(blanks)
        except (SyntaxError, ValueError):
            pass
    if isinstance(blanks, (list, tuple)):
        blanks = "; ".join(str(b) for b in blanks if b)
    return f"Still open: {to_you(blanks).rstrip('.')}." if blanks else ""




DONT_SEND, NOT_NEEDED = "Don't Send", "Not Needed"


def read(note: str | None, raw_metadata, zip_name: str, title: str = "", zips: tuple = ()) -> dict:
    ""
    metadata = _metadata(raw_metadata)
    note = _DRAFT_NOTE.sub("", str(note or "")).strip()
    people = recipients(note, metadata)
    if people:

        names = " and ".join(n or a for n, a in people)
        ask, button, does = f"Send this email to {names}?", "Send", "Approve sends it."
    else:



        from . import reviewwords
        written = reviewwords.get(title, note, zips) if title else None
        plain = re.sub(r"[.?!]+$", "", " ".join(title.split()))
        if _ANY_ADDRESS.search(plain):
            plain = ""
        ask = written[0] if written else (f"Okay to go ahead with \u201c{plain}\u201d?" if plain else "Okay to go ahead?")
        more = reviewwords.details(title, note) if written else {}
        lead = "\n".join(x for x in (written[1] if written else "", f"What I checked: {more['checked']}" if more.get("checked") else "",
                                     f"The risk: {more['risk']}" if more.get("risk") else "") if x)
        return {"sends_to": [], "names": [], "ask": ask, "button": "Approve", "does": "", "lead": lead, "more": "",
                "more_label": "", "no": more.get("no") or (DONT_SEND if written and written[2] else NOT_NEEDED),
                "worded": bool(written)}
    draft = metadata.get("full_text")
    if people and isinstance(draft, str) and draft.strip():
        lead, more, label = _still_open(metadata), draft.strip(), "See the Draft"
    else:
        lead, _, more = to_you(note).partition("\n")
        lead, more = lead.strip(), more.strip()
        label = "See the Draft" if people else "See the Rest"
    return {"sends_to": [a for _, a in people], "names": [n or a for n, a in people], "ask": ask, "button": button, "does": does, "lead": lead, "more": more,
            "more_label": label if more else "", "no": DONT_SEND}


def text(card: dict) -> str:
    ""
    first = f"{card['ask']} {card['does']}".strip()
    return "\n\n".join(x for x in (first, card["lead"], card["more"]) if x)


def blocked(reason: str | None, kind: str | None, status: str, zip_name: str, title: str = "",
            ready: dict | None = None) -> dict | None:
    ""




    reason = str(reason or "")
    if blockask.signs_in(reason, title):
        return None
    parsed = blockask.parse(reason)
    if parsed:


        from . import wayforward
        ask, more = parsed["ask"], parsed["details"]
        options = [o for o in parsed["options"] if not wayforward.offers_final_action(o)]
    elif kind in blockask.QUIET_KINDS and status in ("triage", "blocked") and ready:



        return {"ask": ready["ask"], "options": list(ready["options"]),
                "pick": (ready["options"] or [None])[0] if status == "triage" else None,
                "button": "", "does": "", "lead": "",
                "more": ready["more"], "more_label": "See the Details" if ready["more"] else ""}
    else:
        return None
    return {"ask": to_you(ask), "options": options, "button": "", "does": "", "lead": "", "more": to_you(more).strip(),
            "more_label": "See the Details" if more.strip() else ""}


def blocked_text(card: dict) -> str:
    ""
    answers = f" You can answer: {' or '.join(card['options'])}." if card["options"] else ""
    return "\n\n".join(x for x in (card["ask"] + answers, card["more"]) if x)


def _pass_for(task: dict, card: dict, note: str | None) -> None:
    ""


    from . import consent
    own = (note or "").strip()
    to = card.get("sends_to") or []
    said = f"Approved sending to {', '.join(to)}." if to else f"Approved: {task.get('title') or 'this task'}."
    pass_id = consent.open_pass("approve" if to else "button", " ".join(x for x in (own, said) if x), task_id=task["id"])
    consent.card_words(pass_id, own)


def _sent_to_since(since: float) -> set[str]:
    ""

    from . import store
    out = set()
    for row in store.db().execute("SELECT kind, body FROM feed WHERE COALESCE(changed_at, created_at)>=? AND "
                                  "((kind='event' AND json_extract(body, '$.action')='sent') OR "
                                  "(kind='mail' AND json_extract(body, '$.state')='sent'))", (since,)):
        try:
            body = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        mail = body.get("mail") if row["kind"] == "event" else body
        out |= {str(p.get("addr") or "").lower() for p in (mail or {}).get("to", []) + (mail or {}).get("cc", [])
                if isinstance(p, dict)}
    return out


def already_went_out(task: dict, card: dict) -> bool:
    ""



    from . import board
    if card.get("sends_to"):
        return False
    meta = _metadata(board.run_metadata(task["id"]))
    listed = meta.get("recipients") or meta.get("to")
    addrs = {a.lower() for _, a in _people(", ".join(str(x) for x in listed) if isinstance(listed, (list, tuple))
                                            else str(listed or ""))}
    return bool(addrs) and addrs <= _sent_to_since(float(task.get("created_at") or 0))


_ANY_ADDRESS = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")
_FROM_LINE = re.compile(r"(?im)^[ \t]*From:.*$")
_LINK = re.compile(r"https?://[^\s<>()\"']+")
_WAITING_MAIL = ("open", "sending")


def _named(note: str | None, meta: dict) -> set[str]:
    ""
    listed = meta.get("recipients") or meta.get("to") or meta.get("draft_to")
    out = {a.lower() for _, a in _people(", ".join(str(x) for x in listed) if isinstance(listed, (list, tuple))
                                         else str(listed or ""))}
    return out | {a.lower() for a in _ANY_ADDRESS.findall(_FROM_LINE.sub("", str(note or "")))}


def mail_cards(bot_id: str, since: float, note: str | None, raw_metadata, attachments: int) -> list[dict]:
    ""






    from . import mailcard, store
    meta, note = _metadata(raw_metadata), str(note or "")
    wanted = _named(note, meta)
    if not wanted or attachments:
        return []
    cards = []
    for row in store.db().execute("SELECT id, created_at, body FROM feed WHERE kind=? AND bot_id=? AND "
                                  "COALESCE(changed_at, created_at)>=?", (mailcard.KIND, bot_id, since)):
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") not in (*_WAITING_MAIL, "sent", "dismissed") \
                or float(card.get("revised_at") or row["created_at"] or 0) < since:
            continue
        to = {str(p.get("addr") or "").lower() for p in card.get("to", []) + card.get("cc", []) if isinstance(p, dict)}
        if to & wanted:
            cards.append({"id": row["id"], "to": to, "body": str(card.get("body") or "")})
    links = {link.rstrip(".,;:!?") for link in _LINK.findall(note)}
    if not wanted <= set().union(*(c["to"] for c in cards)) or any(all(x not in c["body"] for c in cards) for x in links):
        return []
    return cards


REDRAFT = ("This review names email addresses, and none of its emails has a draft card. If it is an email for {first} "
           "to send, save it as a draft with the email tool (to, subject and the full text, from the account it should "
           "go from), so {first} sees it on its card, where Send sends it. Then request review again with the addresses "
           "in metadata `to` and none in the summary. Don't send it yourself. If it isn't an email to send, request "
           "review again as it was.")


@store.schema
def _tables(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS review_redrafts (task_id TEXT PRIMARY KEY, at REAL NOT NULL)")


def redraft(task_id: str) -> bool:
    ""






    from . import board
    task = board.get_task(task_id)
    if not task or task.get("raw_status") != "review" or task.get("mail_cards"):
        return False
    summary, metadata = board.run_filed(task_id)
    if not _named(summary, _metadata(metadata)):
        return False
    with store._lock:
        if store.db().execute("SELECT 1 FROM review_redrafts WHERE task_id=?", (task_id,)).fetchone():
            return False
        store.db().execute("INSERT INTO review_redrafts(task_id, at) VALUES (?,?)", (task_id, time.time()))
        store.db().commit()
    ok, detail = board.hand_back(task_id, REDRAFT.format(first=who.first()))
    if not ok:
        log.info("could not hand %s back for a draft card: %s", task_id, detail[-200:])
    return ok


CARRY_ON = ("{first} doesn't need to decide this step, so no card went up. Settle it with your teammates and carry on. "
            "Request review again only when the work is ready to reach customers, and say what changes for them.")
CARRY_MAX = 2


@store.schema
def _carried_table(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS review_carried (task_id TEXT PRIMARY KEY, times INTEGER NOT NULL, at REAL NOT NULL)")


async def carry_on(task_id: str, ask=None) -> bool:
    ""


    from . import board, reviewwords
    if not (ask or reviewwords.ENABLED):
        return False
    task = board.get_task(task_id)
    if not task or task.get("raw_status") != "review" or task.get("mail_cards") or (task.get("card") or {}).get("sends_to"):
        return False
    summary, metadata = await asyncio.to_thread(board.run_filed, task_id)
    note = _DRAFT_NOTE.sub("", str(summary or "")).strip()
    if not task.get("title") or recipients(note, _metadata(metadata)):
        return False
    from . import shortreply
    if shortreply.withheld(*board.task_zips(task)):
        return False
    if not (await reviewwords.written(task["title"], note, ask, zips=board.task_zips(task))).get("step"):
        return False
    with store._lock:
        row = store.db().execute("SELECT times FROM review_carried WHERE task_id=?", (task_id,)).fetchone()
        if row and row["times"] >= CARRY_MAX:
            return False
        store.db().execute("INSERT INTO review_carried(task_id, times, at) VALUES (?,1,?) ON CONFLICT(task_id) DO UPDATE "
                           "SET times=times+1, at=excluded.at", (task_id, time.time()))
        store.db().commit()
    ok, detail = await asyncio.to_thread(board.hand_back, task_id, CARRY_ON.format(first=who.first()))
    if not ok:
        log.info("could not hand %s back to carry on: %s", task_id, detail[-200:])
    return ok


def mailed_line(task: dict) -> str | None:
    ""


    from . import store
    ids = task.get("mail_cards") or []
    for fid in ids:
        row = store.db().execute("SELECT body FROM feed WHERE id=?", (fid,)).fetchone()
        if row and _metadata(row["body"]).get("state") in _WAITING_MAIL:
            return None
    return "Each email on this was sent or closed from its own card, so this is done." if ids else None


def close_mailed() -> list[str]:
    ""
    from . import board
    out = []
    for task in board.grouped()["working"]:
        line = mailed_line(task)
        if line and board.drop_waiting(task["id"], line)[0]:
            out.append(task["id"])
    return out


def answered_line(task: dict, outcome: str) -> None:
    ""


    ask = " ".join(str((task.get("card") or {}).get("ask") or "").split())
    if not ask:
        return
    try:
        store.add_feed("signin_decision", task.get("assignee") or "default", task.get("id"), f"{outcome}: {ask}", "",
                       written_by="hub", written_for="record")
    except Exception:
        log.warning("could not write the line for an answered review card", exc_info=True)


def answer_note(task: dict, words: str) -> str:
    ""

    ask = " ".join(str((task.get("card") or {}).get("ask") or "").split())
    return f'To the question "{ask}", the answer is: {words}' if ask and task.get("status") == "blocked" else words


def answered_words(task: dict, words: str) -> None:
    ""

    ask = " ".join(str((task.get("card") or {}).get("ask") or "").split())
    if not ask or not words.strip() or task.get("status") != "blocked":
        return
    try:
        store.add_feed("signin_decision", task.get("assignee") or "default", task.get("id"), f"Answered: {ask}",
                       words.strip(), written_by="person", written_for="record")
    except Exception:
        log.warning("could not write the line for an answered card", exc_info=True)


def approve(task_id: str, note: str | None) -> tuple[bool, str]:
    ""


    from . import board
    task = board.get_task(task_id) or {"id": task_id}
    card = task.get("card") or {}
    if already_went_out(task, card):
        return board.drop_waiting(task_id, "You approved it. The emails it prepared had already gone out, so there "
                                           "was nothing left to do.")
    if task.get("mail_cards"):
        return False, "Each email on this has its own card. Send or close them there, and this closes by itself."
    ok, out = board.approve(task_id, note, card.get("sends_to") or [])
    if ok:
        try:
            _pass_for(task, card, note)
        except Exception:
            log.warning("could not record the approval", exc_info=True)
        answered_line(task, "Approved")
    return ok, out
