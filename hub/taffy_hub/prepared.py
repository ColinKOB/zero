"""Prepared actions (proactivity phase 2): Grok Bot's "I've drafted this", held to Colin's rule that a send is approved
only on a card that showed the exact recipient and text.

For an email Colin sent with no reply, a reply waiting on him, or the follow-up after a meeting, the owning Zip
prepares the exact send before Zero asks:

1. The question is held back (state "preparing") and the Zip gets one short turn in a session apart from Colin's chat
   (hermes lane "prep"), with Haiku's draft as a starting point. It calls the email tool to send it.
2. The gate stops that call, as it stops every send, and writes the action card with why "prepared": the exact call's
   fingerprint, recipient and text. A prepared card is hidden from Work, the badge and notifications (consent.py), and
   the gate never lets a standing rule or a review cover a send in a prep session.
3. The hub checks the card: a send, to exactly the person the noticer found on Colin's side of the thread (someone he
   wrote to). Anything else is thrown away and the question goes up the old way, with the draft as his own words.
4. The question goes up with the first option "Send ... as written" and, under it, exactly what the card shows.
5. Tapping that option in the app is Allow Once on that card (consent.decide), nothing broader. The Zip is told to
   make the same call again; the gate lets that one exact call through once, and any other call raises a new card.
   A lock-screen Yes never gets here (notices.lock_answer).

At most five a day (a setting), between 7 a.m. and 9 p.m., and never while Colin's Claude plan is at its limit.
"""

import asyncio
import json
import logging
import time

from . import config, consent, notices, store, who

log = logging.getLogger("taffy.prepared")

PREP_WAIT_SEC = 10 * 60      # a preparation that isn't ready by then: the question goes up without it
START_HOUR, END_HOUR = 7, 21
KINDS = ("unreplied", "reply_waiting", "followup")   # where the draft is the obvious next step

NOTE = """[Preparation from the hub, not a message from {first}]
{lead} is about to ask {first} about this: {evidence}

Prepare the email so that {possessive} one tap can send it. Use the email tool with action "send", account "{account}", to \
"{to}", subject "{subject}". Start from this draft and change it only with facts you know for certain. Keep it short \
and in {possessive} voice:

{body}

The send will stop and say it is prepared. That is expected: stop there and reply with one short line. Do not send \
to anyone else, do not do anything else, and do not mention permissions."""

RESUME = """[From the hub, not a message from {first}] {first} tapped "{label}" on {lead}'s question, which approves the \
email you prepared to {to} once. Make exactly the same email tool call again now, unchanged; it goes through once. \
Then reply with one short line."""


async def run_turn(bot_id: str, text: str) -> str | None:
    """A turn in the Zip's prep session. Tests replace this."""
    from . import hermes
    return await hermes.run_turn(bot_id, text, lane="prep")


def _plan_ok() -> bool:
    try:
        from . import health
        return bool(health.status.get("claude", True))
    except Exception:
        return True


def allowed() -> bool:
    """A preparation may start now: 7 a.m. to 9 p.m., inside the day's five, and the plan not at its limit."""
    if not START_HOUR <= notices._et().hour < END_HOUR or not _plan_ok():
        return False
    return notices._count("prep", notices.day_start()) < int(notices.settings()["prep_daily"])


def plan(c: notices.Candidate, draft: str) -> dict:
    d = c.draft or {}
    # A name that is only the address again makes "store1234@... <store1234@...>", which the email tool refuses
    # (Oct 9, 8:49 a.m.: School's prepared nudge failed once and was retried).
    name = str(d.get("to_name") or "").strip()
    to = f"{name} <{d['to_addr']}>" if name and "@" not in name else d["to_addr"]
    label = (c.prepare or {}).get("label") or f"{c.draft_options[0].text} as written"
    return {"bot": (c.prepare or {}).get("bot") or c.owner or "default", "account": d["account"], "to": to,
            "to_addr": d["to_addr"].lower(), "subject": d["subject"], "body": draft, "label": label[:80],
            "state": "queued", "queued": notices.now()}


def _prep(r: dict) -> dict:
    try:
        return json.loads(r.get("prep") or "{}") or {}
    except ValueError:
        return {}


def _save(nid: int, p: dict) -> None:
    notices._set(nid, prep=json.dumps(p))


def start(nid: int) -> None:
    """Queued; the next tick (every couple of seconds) starts it, one at a time per Zip."""
    log.info("preparing an action for question %s", nid)


_tasks: dict[str, asyncio.Task] = {}


def _busy(bot_id: str) -> bool:
    t = _tasks.get(bot_id)
    return t is not None and not t.done()


async def prepare(nid: int) -> bool:
    """The Zip's prep turn, then the question goes up (with the prepared action when it can be used)."""
    r = notices.by_id(nid)
    if not r or r["state"] != "preparing":
        return False
    p = _prep(r)
    card = json.loads(r["card"] or "{}")
    p.update(state="running", started=notices.now(), since=time.time())   # consent.db keeps wall-clock times
    _save(nid, p)
    text = NOTE.format(first=who.first(), possessive=who.possessive(), lead=notices._lead(), evidence=card.get("evidence", ""), account=p["account"], to=p["to"],
                       subject=p["subject"], body=p["body"])
    try:
        await run_turn(p["bot"], text)
    except Exception:
        log.warning("the prep turn for question %s failed", nid, exc_info=True)
    return finish(nid)


def _found(bot_id: str, since: float) -> dict | None:
    """The newest prepared card this Zip made since its prep turn started."""
    with consent.db() as c:
        row = c.execute("SELECT * FROM requests WHERE bot_id=? AND why=? AND status='pending' AND created>=? "
                        "AND COALESCE(test,0)=0 ORDER BY id DESC LIMIT 1",
                        (bot_id, consent.PREPARED, since - 5)).fetchone()
    return dict(row) if row else None


def usable(req: dict | None, p: dict) -> bool:
    """A send, to exactly the person the noticer found on Colin's side, and nobody else. A target that came from the
    content (a new address in an email) is never an option."""
    if not req or (req.get("kind") or "") != "send" or (req.get("why") or "") != consent.PREPARED:
        return False
    targets = {t.strip().lower() for t in (req.get("target") or "").split(",") if t.strip()}
    return targets == {p.get("to_addr")} and p.get("to_addr", "") in (req.get("inputs") or "").lower()


def _drop(req: dict | None) -> None:
    if req:
        with consent.db() as c:
            c.execute("UPDATE requests SET status='expired', decided=? WHERE id=? AND status='pending'",
                      (time.time(), req["id"]))


def finish(nid: int) -> bool:
    """After the prep turn (or its time limit): attach the card if it can be used, then post the question either
    way. True when the question carries a prepared action."""
    r = notices.by_id(nid)
    if not r or r["state"] != "preparing":
        return False
    p = _prep(r)
    req = _found(p["bot"], float(p.get("since") or 0))
    ok = usable(req, p)
    if ok:
        card = json.loads(r["card"] or "{}")
        opts = card.get("options") or []
        first = {"text": p["label"], "kind": "prepared", "say": "", "hub": "", "follow": "", "wait_days": 0}
        card["options"] = [first] + opts[1:]
        card["detail"] = {"label": "what will be sent", "text": req.get("inputs") or ""}
        card["prepared"] = {"by": notices._bot_name(p["bot"]), "to": p["to_addr"], "request": req["id"]}
        card.setdefault("ref", {})["prepared"] = req["id"]
        notices._set(nid, card=json.dumps(card))
        p.update(state="ready", request=req["id"])
    else:
        _drop(req)
        p.update(state="unused")
        log.info("question %s goes up without a prepared action", nid)
    _save(nid, p)
    notices.publish(nid)
    return ok


def approve(r: dict, card: dict) -> tuple[str, str | None]:
    """Colin tapped the prepared option in the app: Allow Once on that card, and the Zip makes the same call again.
    (follow-through line, Colin's words for Zero or None)."""
    p = _prep(r)
    req_id = (card.get("ref") or {}).get("prepared")
    req = consent.get_request(int(req_id)) if req_id else None
    if req and req["status"] == "pending" and (req.get("why") or "") == consent.PREPARED:
        consent.decide(int(req_id), True)          # Allow Once on exactly this call; never a standing rule
        p.update(state="approved", approved=notices.now())
        _save(r["id"], p)
        return f"Approved {notices.at_clock(notices.now())} {notices._bot_name(p.get('bot'))} is sending it now.", None
    # The prepared card expired: the exact text goes to Zero as Colin's words, and the send card shows it again.
    text = (card.get("detail") or {}).get("text") or ""
    p.update(state="expired")
    _save(r["id"], p)
    return ("That prepared message had expired, so it went to Zero. Approve it on the card in Work.",
            f"Please send this email exactly as written:\n\n{text}" if text else None)


async def send_approved(nid: int) -> bool:
    """The Zip makes the approved call again. True when it went out."""
    r = notices.by_id(nid)
    p = _prep(r or {})
    if not r or p.get("state") != "approved":
        return False
    p.update(state="sending")
    _save(nid, p)
    zip_name = notices._bot_name(p["bot"])
    reply = None
    try:
        reply = await run_turn(p["bot"], RESUME.format(first=who.first(), label=p["label"], lead=notices._lead(), to=p["to"]))
    except Exception:
        log.warning("the approved send for question %s failed", nid, exc_info=True)
    req = consent.get_request(int(p["request"])) if p.get("request") else None
    sent = bool(req and req.get("used"))
    p.update(state="sent" if sent else "failed")
    _save(nid, p)
    r = notices.by_id(nid)
    if sent:
        notices._follow(r, f"Sent to {p['to_addr']} {notices.at_clock(notices.now())}")
    else:
        notices._follow(r, f"{zip_name} couldn't send it. Open {zip_name}'s chat to see why.")
        if reply and p["bot"] in config.BOTS_BY_ID:
            store.add_feed("message", p["bot"], None, zip_name, reply)
    return sent


async def tick() -> None:
    """From notices.tick: start queued preparations, give up on slow ones, and send what Colin approved."""
    t = notices.now()
    rows = [dict(x) for x in notices._db().execute(
        "SELECT * FROM notices WHERE prep IS NOT NULL AND (state='preparing' OR (state='picked' AND "
        "pick_kind='prepared')) ORDER BY id")]
    for r in rows:
        p = _prep(r)
        bot = p.get("bot") or "default"
        if r["state"] == "preparing":
            began = float(p.get("started") or p.get("queued") or t)
            if t - began > PREP_WAIT_SEC:
                finish(r["id"])        # too slow: the question goes up without it
            elif p.get("state") == "queued" and not _busy(bot):
                _tasks[bot] = asyncio.create_task(prepare(r["id"]))
        elif p.get("state") == "approved" and not _busy(bot):
            _tasks[bot] = asyncio.create_task(send_approved(r["id"]))
