""











import asyncio
import json
import logging
import re
import time
from enum import Enum

from . import blockask, config, consent, store, task_context, turns, who

log = logging.getLogger("taffy.signin")

KIND = "login"


loop: asyncio.AbstractEventLoop | None = None


class AskError(Exception):
    pass


class Purpose(str, Enum):
    signin = "signin"
    challenge = "challenge"

    final = "final"
    file = "file"
    takeover = "takeover"
    stuck = "stuck"


ASKED = (Purpose.signin, Purpose.challenge)
HANDED = (Purpose.final, Purpose.file, Purpose.takeover)
PARKED = (*HANDED, Purpose.stuck)







GOOGLE_SIGN_IN_HOST = "accounts.google.com"
GOOGLE_FILE_HOSTS = frozenset({"docs.google.com", "drive.google.com", "sheets.google.com", "slides.google.com"})


def google_wall(url: str, sso_url: str = "", purpose: str = "signin") -> str | None:
    ""


    host = consent.site_of(url)
    if host in GOOGLE_FILE_HOSTS:
        return "files"
    if host == GOOGLE_SIGN_IN_HOST or consent.site_of(sso_url) == GOOGLE_SIGN_IN_HOST:
        return "sign_in"
    if purpose == Purpose.signin and consent.base(host) == "google.com":
        return "sign_in"
    return None


def google_return_page(url: str, sso_url: str = "") -> str:
    ""


    from urllib.parse import parse_qs, urlsplit
    for start in (url, sso_url):
        u = start
        for _ in range(4):
            host = consent.site_of(u)
            if not host:
                break
            if host != GOOGLE_SIGN_IN_HOST:
                return u if u == url else f"https://{host}/"
            q = parse_qs(urlsplit(u).query)
            u = next((v for k in ("redirect_uri", "continue", "origin") for v in q.get(k, [])), "")
    return ""


def google_note(site: str) -> str:
    ""
    start = (f"No card went to {who.first()}. Google doesn't allow signing in on your browser, for anyone, so a card "
             "can't help. Don't open Google's sign-in page or type there. ")
    if site in GOOGLE_FILE_HOSTS:
        return start + (f"Ask {who.first()} in one sentence to send you the file, and finish the rest without it.")
    tools = "For Gmail, Google Calendar or Drive, use your email, calendar or drive tool. "
    if consent.base(site) == "google.com":
        return start + tools + (f"If none of those covers it, tell {who.first()} in one sentence what you couldn't "
                                "reach, and finish the rest without it.")
    return start + tools + (f"If {site} has its own sign-in, such as an email and password or a code, use that, and "
                            f"ask again with that page. Otherwise tell {who.first()} in one sentence what you couldn't "
                            "reach, and finish the rest without it.")


def purpose_of(value: str = "signin", allowed: tuple = ASKED) -> Purpose:
    try:
        purpose = Purpose(value)
    except (TypeError, ValueError):
        purpose = None
    if purpose not in allowed:
        raise AskError("Choose purpose " + " or ".join(p.value for p in allowed) + ".")
    return purpose


def _purpose(card: dict) -> Purpose:
    ""
    try:
        return Purpose(card.get("purpose", "signin"))
    except (TypeError, ValueError):
        return Purpose.signin


def _open_card(bot_id: str, site: str, purpose: Purpose):
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (KIND, bot_id)):
        card = json.loads(row["body"] or "{}")
        if card.get("state") == "open" and card.get("site") == site and _purpose(card) == purpose:
            return row
    return None








CHECK_FAILED_SEC = {"declined": 3600, "checked": 3600}


def check_failed_here(bot_id: str, site: str) -> bool:
    ""
    now = time.time()
    for row in store.db().execute("SELECT body FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 200",
                                  (KIND, bot_id)):
        card = json.loads(row["body"] or "{}")
        if card.get("site") == site and _purpose(card) == Purpose.challenge \
                and now - float(card.get("at") or 0) < CHECK_FAILED_SEC.get(card.get("state"), 0):
            return True
    return False


def check_failed_note(url: str) -> str:
    ""
    first = who.first()
    return (f"{first} already tried this check on your browser and it didn't pass, so a card here can't help. Finish "
            f"every other part of the job. Then call hand_over with kind takeover, place mac, this address and the one "
            f"action \"{check_action(consent.site_of(url))} in your own browser\", so {first} gets one card for it: "
            f"{url}")


def check_action(site: str, name: str = "") -> str:
    ""

    name = " ".join(str(name or "").split()).strip(" .")
    usable = name and len(name) <= 40 and not re.search(r"[/:@<>\[\]{}]", name)
    return f"Clear the human check on {name if usable else site}"


def _check_wait(purpose: Purpose, origin_task: str | None) -> str:
    ""
    if purpose != Purpose.challenge or not origin_task:
        return ""
    return (f" This is a board task, so call kanban_block now with the reason \"{blockask.CHECK_WAIT}\" and stop. "
            f"{who.possessive()} answer on the card puts the task back in your queue.")


def challenge_tasks() -> set[str]:
    ""
    out = set()
    for row in store.db().execute("SELECT task_id, body FROM feed WHERE kind=? AND task_id IS NOT NULL AND task_id != '' "
                                  "ORDER BY id DESC LIMIT 200", (KIND,)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if isinstance(card, dict) and card.get("state") == "open" and card.get("purpose") == Purpose.challenge:
            out.add(row["task_id"])
    return out


def open_check(bot_id: str, task_id: str | None = None) -> int | None:
    ""
    for row, card in _open_asks(bot_id):
        if row["kind"] == KIND and _purpose(card) == Purpose.challenge and (not task_id or row["task_id"] == task_id):
            return int(row["id"])
    return None


def _card_row(bot_id: str, feed_id: int):
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?",
                             (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise AskError("That card is gone or belongs to another Zip.")
    return row


def card_for(bot_id: str, feed_id: int) -> dict:
    return json.loads(_card_row(bot_id, feed_id)["body"] or "{}")


def card_owner(feed_id: int) -> str | None:
    ""
    row = store.db().execute("SELECT bot_id FROM feed WHERE id=? AND kind=?", (feed_id, KIND)).fetchone()
    return row["bot_id"] if row else None


def open_cards_for(site: str) -> list[tuple[str, int]]:
    ""
    company = consent.base(consent.site_of(site))
    found = []
    for row in store.db().execute("SELECT id, bot_id, body FROM feed WHERE kind=?", (KIND,)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if isinstance(card, dict) and card.get("state") == "open" \
                and card.get("purpose", "signin") == Purpose.signin and consent.base(card.get("site") or "") == company:
            found.append((row["bot_id"], row["id"]))
    return found


def share_saved(site: str, exclude: int | None = None) -> None:
    ""
    from . import logins
    company = consent.base(consent.site_of(site))
    rows = store.db().execute("SELECT * FROM feed WHERE kind=?", (KIND,)).fetchall()
    for row in rows:
        if row["id"] == exclude or row["bot_id"] not in config.BOTS_BY_ID:
            continue
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if not isinstance(card, dict):
            continue
        if card.get("state") != "open" or card.get("purpose", "signin") != Purpose.signin \
                or consent.base(card.get("site") or "") != company:
            continue
        if logins.share_for_site(row["bot_id"], site) or logins.has_for_site(row["bot_id"], site):
            resolve(row["bot_id"], row["id"], "saved", share=False)


GOOGLE_FILES = {"docs.google.com", "drive.google.com", "sheets.google.com", "slides.google.com"}
REPUSH_SEC = 120


def recover(bot_id: str, url: str, key: str, sso_url: str = "") -> tuple[str | None, str | None]:
    ""

    site = consent.site_of(url)
    from . import logins, problems
    if site in GOOGLE_FILES:


        return None, (f"{site} signs in through Google, where a saved password can't be typed. Read the file with "
                      "the drive tool instead: search for it by name, then read it. If it is shared by link, its "
                      "export address works too: end a Docs link with /export?format=txt, or a Sheets link with "
                      f"/export?format=csv. If neither reaches it, ask {who.first()} one short question.")
    if consent.sign_in_provider(sso_url) or not logins.has_for_site(bot_id, site):
        return None, None
    failure = consent.login_recovery(bot_id, key, site)
    if failure.get("kind") == "challenge":
        return None, ("The page needs a human check. Use ask_to_sign_in with purpose='challenge' and the "
                      "current page URL. Leave it open without navigating or reloading.")
    if failure.get("kind") == "credentials":
        return f"{site} said \"{failure['reason']}\".", None
    if not failure.get("filled") and not failure.get("error"):
        return None, (f"You have a saved login for {site}. Use browser_vault_list and browser_vault_fill first, "
                      "then inspect the page. Being signed out is not a rejected login. If the form cannot be "
                      "found or the page fails, call ask_to_sign_in after that observed failure.")
    elif failure.get("phase") != "retried":
        return None, (f"You have a saved login for {site}. Retry once with a fresh browser_navigate, then "
                      "take a fresh browser_snapshot. If the session expired, use browser_vault_list and "
                      "browser_vault_fill to sign in again. Keep the normal permission checks. If it still "
                      "fails, call ask_to_sign_in again so I can check the new evidence and flag the problem. "
                      f"Do not ask {who.first()} to sign in for a timeout, reload or missing form.")
    else:
        evidence = consent.scrub(json.dumps({k: v for k, v in failure.items() if k != "phase"}))[:600]
        try:
            problems.report(bot_id, {"kind": "break", "concerns_zip": "self",
                                    "what_happened": f"Saved-login page recovery failed on {site}.",
                                    "evidence": f"Site: {site}. {evidence}"[:600]})
        except problems.ProblemError:

            problems.report(bot_id, {"kind": "break", "concerns_zip": "self",
                                    "what_happened": f"Saved-login page recovery failed on {site}.",
                                    "evidence": f"Site: {site}. Step: {failure.get('step') or 'page check'}. "
                                                "The fresh-page retry did not recover access. Details omitted safely."})
        return None, (f"I've flagged the problem with {site} for a fix. Do not ask {who.first()} to sign in or retry "
                      f"again. Finish what you can using another available source. Tell {who.first()} in one plain "
                      "sentence what failed, that you've flagged it for a fix, and what you used instead. "
                      "If no other source works, say what you could not check.")


def ask(bot_id: str, url: str, why: str, session_id: str | None,
        purpose: str = "signin", recovery_key: str | None = None,
        origin_task: str | None = None, sso_url: str = "", failure_reason: str | None = None,
        place: str = "browser", site_name: str = "") -> tuple[int | None, str]:
    ""







    purpose = purpose_of(purpose)
    site = consent.site_of(url)
    if not site:
        raise AskError("Give the sign-in page's full https address.")
    on_mac = place == "mac" and purpose == Purpose.signin
    if purpose == Purpose.signin and not failure_reason and not on_mac:
        failure_reason, instruction = recover(bot_id, url, recovery_key or session_id or "default", sso_url)
        if instruction:
            return None, instruction


    why = who.to_you(" ".join(str(why or "").split())[:200])
    context = task_context.get(origin_task)
    if context:
        why = "\n".join(part for part in (context.purpose, why) if part)
    if purpose == Purpose.challenge:
        why = check_action(site, site_name) + "."
    row = _open_card(bot_id, site, purpose)
    action = (f"finish a check on {site}" if purpose == Purpose.challenge else
              f"sign in to {site} on {who.mac()}" if on_mac else f"sign in to {site}")
    if row:
        card = json.loads(row["body"] or "{}")
        now = time.time()
        pushed = card.get("pushed_at")
        due = now - float(pushed if pushed is not None else row["created_at"] or 0) >= REPUSH_SEC
        if failure_reason is None and card.get("progress") in ("wrong", "needs_you"):
            failure_reason = card.get("failure_reason")
        card.update(url=url, why=why or card.get("why") or "", session_id=session_id or card.get("session_id"),
                    asked_again=now, failure_reason=failure_reason, place="mac" if on_mac else "browser")


        card.pop("from_check", None)
        if due:
            card["pushed_at"] = now
        store.update_feed(row["id"], row["title"], json.dumps(card))
        store.touch_feed(row["id"], now)
        return (row["id"] if due else None), (
            f"{who.possessive()} card to {action} is now at the bottom of your chat"
            + (", with a new notification" if due else "") + f". Stop here. {who.quiet_card()} A note will arrive in "
            f"this chat when {who.first()} is done, and you'll carry on then. If you no longer need it, close it with "
            "close_sign_in_card." + _check_wait(purpose, origin_task))
    title = check_action(site, site_name) if purpose == Purpose.challenge else f"Sign in to {site}"
    store.add_feed(KIND, bot_id, origin_task, title,
                   json.dumps({"url": url, "site": site, "why": why, "state": "open", "session_id": session_id,
                               "purpose": purpose.value, "pushed_at": time.time(), "failure_reason": failure_reason,
                               **({"place": "mac"} if on_mac else {})}), written_by="zip", written_for="person")
    row = _open_card(bot_id, site, purpose)
    return (row["id"] if row else None), (
        f"{who.first()} now has a card at the bottom of your chat to {action}, and a notification. Leave "
        f"{site} alone until then, but finish any part of the request that does not need it. {who.quiet_card()} "
        f"A note will arrive in this chat "
        f"when {who.first()} is done, and you'll carry on then. If {who.first()} gets it done another way or you no "
        "longer need it, close the card with close_sign_in_card." + _check_wait(purpose, origin_task))










PLACES = ("mac", "browser")


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _job_key(card: dict) -> str:
    ""
    return " ".join(re.findall(r"\w+", (card.get("job") or "").casefold())) or \
        f"{card.get('session_id') or ''} {card.get('site') or card.get('place') or ''}"






_ACKNOWLEDGE = re.compile(r"\b(notice|privacy|disclaimer|banner|acknowledge?ment|i agree|agree|accept|continue|got it|"
                          r"dismiss)\b", re.I)
_MOVE = re.compile(r"^(press |click |tap )?(open|view|go to|load|show|search|browse|filter|pick|select|choose)\b|"
                   r"\badd\b.*\bto (the |your )?(cart|list|schedule)\b", re.I)
_HIS = re.compile(r"\b(sign|signature|initial|swear|sworn|submit|send|pay|purchase|buy|order|register|enroll|"
                  r"passkey|password|code|verify|approve|confirm)\b", re.I)


def yours_to_press(action: str) -> bool:
    ""
    words = re.sub(r"(?i)^you\s+", "", action.strip())
    return not _HIS.search(words) and bool(_ACKNOWLEDGE.search(words) or _MOVE.search(words))


def park(bot_id: str, session_id: str | None, kind: str, action: str, url: str = "", place: str = "browser",
         app: str = "", done=None, expect: str = "", files=(), job: str = "") -> tuple[int, str]:
    ""



    purpose = purpose_of(kind, HANDED)
    action = who.to_you(_clean(action, 200)).rstrip(".")
    if not action:
        raise AskError(f"Say the one thing {who.first()} does, in a short sentence.")
    if purpose is not Purpose.file and yours_to_press(action):
        raise AskError(f"That step is yours, so do it now and carry on. Pressing through a notice or agreement and "
                       f"opening, searching or filling a list or cart never go to {who.first()}. If a press needs "
                       f"{who.possessive()} okay, the gate puts up the card itself.")
    if place not in PLACES:
        raise AskError("Say where the page is: mac or browser.")
    url = _clean(url, 2000)
    if url and not re.match(r"https?://", url):
        raise AskError("Give the page's full address, starting with https://.")
    if done is None:
        from . import checklist
        done = checklist.done_steps(session_id or "")
    steps = [t for t in (_clean(d, 120) for d in (done if isinstance(done, (list, tuple)) else [done])) if t][:6]
    record = {"url": url, "site": consent.site_of(url) if url else "", "why": action, "state": "open",
              "session_id": session_id, "purpose": purpose.value, "place": place, "app": _clean(app, 60),
              "done": steps, "expect": _clean(expect, 200),
              "files": [t for t in (_clean(f, 300) for f in (files or [])) if t][:4], "pushed_at": time.time(),
              "job": _clean(job, 60)}
    for row, card in _open_asks(bot_id):
        if row["kind"] == KIND and _purpose(card) in PARKED and _job_key(card) == _job_key(record):
            card.update(state="replaced", at=time.time())
            store.update_feed(row["id"], row["title"], json.dumps(card))
    feed_id = store.add_feed(KIND, bot_id, None, action[:1].upper() + action[1:], json.dumps(record),
                             written_by="zip", written_for="person")
    from . import openloops
    openloops.parked(bot_id, record)
    log.info("%s parked a %s step on %s", bot_id, purpose.value, record["site"] or place)
    first = who.first()
    paused = f" Your access to {who.mac()} is paused with the window left as it is." if place == "mac" else ""
    return feed_id, (f"{first} has a card for this step, with what you got done and where you stopped.{paused} Stop "
                     "here and leave the page as it is: don't navigate, reload or close it. In your reply, say in one "
                     f"or two short sentences what you got done and the one thing {first} does now, then stop. When "
                     f"{first} is done, a note with this record arrives in this chat (or {first} tells you), and you "
                     "look at the page again before you carry on.")


def ask_line(card: dict) -> str:
    ""

    if card.get("purpose") in {p.value for p in PARKED}:
        return card.get("why") or "do one step"
    if card.get("purpose") == Purpose.challenge:
        return "finish a check"
    return f"sign in to {card['site']}" if card.get("site") else "sign in"


def _where(card: dict) -> str:
    app = card.get("app") or ""
    if card.get("place") == "mac":
        return f"in {app} on {who.mac()}" if app else f"on {who.mac()}"
    return "in your own browser"


LOOK_FIRST = {
    "mac": ("Before any press, typing or key on the Mac, read that whole window with mac_ui and no query; a "
            "screenshot or a query shows only part of the page. Don't reopen, reload or navigate the page. If your "
            "Mac access ended, call mac_start first."),
    "browser": "Take a fresh browser_snapshot of the current page before you act. Do not navigate or reload.",
}


def record_note(card: dict) -> str:
    ""

    parts = [f"Where you stopped: {_where(card)}" + (f", at {card['url']}" if card.get("url") else "") + "."]
    if card.get("job"):
        parts.insert(0, f"The job: {card['job']}.")
    if card.get("done"):
        parts.append("Done before the stop: " + "; ".join(card["done"]) + ".")
    if _purpose(card) == Purpose.stuck:
        parts.append(f"Your next step when your tools froze: {card.get('why')}.")
    else:
        parts.append(f"{who.possessive()} step: {card.get('why')}.")
    if card.get("expect"):
        parts.append(f"What the page should show once {who.first()} is done: {card['expect']}.")
    if card.get("files"):
        parts.append("Files you already have for it: " + "; ".join(card["files"]) + ".")
    return " ".join(parts)


def resume_note(card: dict) -> str:
    first = who.first()
    opener = (f"{first} wants you to try again." if _purpose(card) == Purpose.stuck else
              f"{first} says the step is done.")
    return (f"{opener} {record_note(card)} {LOOK_FIRST.get(card.get('place') or '', LOOK_FIRST['browser'])} Compare "
            f"what you see with this record. If {first} already did something, or the site turned something down, "
            "say that in your first sentence, then carry on from what the page shows. Never replay the old plan "
            "without looking.")


def mac_park_open(bot_id: str, within: float = 1800) -> bool:
    ""

    for row, card in _open_asks(bot_id) + _recent_parked(bot_id, within):
        if row["kind"] == KIND and card.get("place") == "mac" and _purpose(card) in PARKED:
            return True
    return False


def _recent_parked(bot_id: str, within: float) -> list[tuple[dict, dict]]:
    out = []
    for row in store.db().execute("SELECT * FROM feed WHERE bot_id=? AND kind=? ORDER BY id DESC LIMIT 10",
                                  (bot_id, KIND)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") == "done" and time.time() - float(card.get("at") or 0) <= within:
            out.append((dict(row), card))
    return out











ASK_KINDS = ("login", "account", "code")


def _open_asks(bot_id: str, limit: int = 40) -> list[tuple[dict, dict]]:
    q = (f"SELECT * FROM feed WHERE bot_id=? AND kind IN ({','.join('?' * len(ASK_KINDS))}) "
         "ORDER BY id DESC LIMIT ?")
    out = []
    for row in store.db().execute(q, (bot_id, *ASK_KINDS, limit)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if card.get("state") == "open":
            out.append((dict(row), card))
    return out


def waiting(row: dict, said: float | None = None) -> bool:
    ""

    said = store.said_at(row["bot_id"]) if said is None else said
    return float(row["created_at"] or 0) >= int(said)


def live_asks(bot_id: str) -> list[tuple[dict, dict]]:
    ""
    said = store.said_at(bot_id)
    return [(row, card) for row, card in _open_asks(bot_id) if waiting(row, said)]


def old_cards(bot_id: str) -> list[tuple[dict, dict]]:
    ""
    said = store.said_at(bot_id)
    return [(row, card) for row, card in _open_asks(bot_id) if row["kind"] == KIND and not waiting(row, said)]


def retire(bot_id: str, site_or_url: str = "") -> list[str]:
    ""


    want = consent.site_of(site_or_url) if "://" in (site_or_url or "") else (site_or_url or "").strip().lower()
    want = want.removeprefix("www.")
    closed = []
    for row, card in _open_asks(bot_id):
        if row["kind"] != KIND:
            continue
        site = (card.get("site") or "").lower().removeprefix("www.")
        if want and not (site == want or consent.base(site) == consent.base(want)):
            continue
        card.update(state="retired", at=time.time())
        store.update_feed(row["id"], row["title"], json.dumps(card))
        if card.get("place") == "mac":
            from . import mac
            mac.drop_parked(bot_id, row["id"])
        closed.append(card.get("site") or card.get("place") or site)
        if not want:
            break
    if not closed:
        raise AskError(f"There is no open sign-in card for {want} in {who.possessive()} chat." if want else
                       f"There is no open sign-in card in {who.possessive()} chat.")
    log.info("%s closed its own sign-in card for %s", bot_id, ", ".join(closed))
    return closed


def old_cards_note(bot_id: str) -> str | None:
    ""

    every = old_cards(bot_id)
    parked = next((c for _, c in every if _purpose(c) in PARKED), None)
    cards = [(r, c) for r, c in every if _purpose(c) in ASKED][:3]
    park_note = None
    if parked:


        park_note = (f"{who.hub_note()} Your parked step is still open in {who.possessive()} chat. {record_note(parked)} "
                     f"If what {who.first()} says now means the step is done or you should carry on, do that: "
                     f"{LOOK_FIRST.get(parked.get('place') or '', LOOK_FIRST['browser'])} If {who.first()} already "
                     "did something, or the site turned something down, say that first. If it no longer applies, "
                     "close it with close_sign_in_card.")
        others = [c for _, c in every if _purpose(c) in PARKED and c is not parked][:3]
        if others:
            park_note += (" Other jobs are parked too, each on its own card: " + "; ".join(
                f"{c.get('job') or c.get('site') or 'a step'} ({c.get('why')})" for c in others) + ".")
    if not cards:
        return park_note
    sites = [("finish a check on " if c.get("purpose") == "challenge" else "sign in to ")
             + (c.get("site") or "a site") for _, c in cards]
    listed = sites[0] if len(sites) == 1 else ", ".join(sites[:-1]) + " and " + sites[-1]
    challenge_note = (" Keep purpose='challenge' for a human check; leave the current page open without navigating "
                      "or reloading." if any(c.get("purpose") == "challenge" for _, c in cards) else "")
    return (f"{who.hub_note()} {who.possessive()} chat still has your older card"
            f"{'s' if len(sites) > 1 else ''} to {listed}. If what {who.first()} says now means you no "
            "longer need it (done another way, bought elsewhere, not wanted any more), close it with "
            f"close_sign_in_card and don't mention it again. If you need it for what {who.first()} asks now, call "
            f"ask_to_sign_in again, which brings that same card back to the bottom of the chat as current; don't "
            f"send {who.first()} to the old one." + challenge_note)




CHECK_AGAIN = ("Open the page again now and check before you answer. If you still cannot read it, tell {first} "
               "exactly what the page shows (its title and the message on it, such as a sign-in form or \"Access "
               "denied\") and what would get past it.")

CHECK_CURRENT = ("Take a fresh browser_snapshot of the current page before answering or acting. Do not navigate "
                 "or reload. Check what is visible now; do not assume the check passed or that you are signed in. "
                 "Do not interact with a CAPTCHA. Existing site permissions still apply.")


def resolve(bot_id: str, feed_id: int, outcome: str, follow: bool = True, share: bool = True) -> dict:
    ""




    row = _card_row(bot_id, feed_id)
    card = json.loads(row["body"] or "{}")
    purpose = _purpose(card)
    outcomes = (("signed_in", "checked", "declined") if purpose == Purpose.challenge else
                ("signed_in", "done", "declined") if purpose in PARKED else ("saved", "signed_in", "declined"))
    if outcome not in outcomes:
        raise AskError("That answer does not apply to this card.")
    if card.get("state") != "open":
        return card
    if purpose == Purpose.challenge and outcome == "signed_in":
        outcome = "checked"
    if purpose in PARKED and outcome == "signed_in":
        outcome = "done"
    card.update(state=outcome, at=time.time())
    store.update_feed(feed_id, row["title"], json.dumps(card))
    site, session_id = card.get("site") or "", card.get("session_id")
    from . import asks
    if purpose in PARKED:
        asks.answered("login", feed_id, bot_id, row["title"], outcome + ". Look at the page before you carry on.")
        return _resolve_parked(bot_id, feed_id, card, outcome, follow)
    asks.answered("login", feed_id, bot_id, row["title"], outcome + ". Check access with your own sign-in tools.")
    if purpose == Purpose.challenge:
        how = ("closed the card without finishing the check" if outcome == "declined" else
               "finished using your screen for the check")
        note = (f"{who.hub_note()} {who.first()} {how} on {site}. " + CHECK_CURRENT)
        if outcome == "declined":
            note += f" Finish what you can without that page and tell {who.first()} what remains blocked."
        else:
            note += f" Continue with what {who.first()} asked only if the page is usable."
        if row["task_id"]:

            from . import board
            board.answer_block(row["task_id"], note.removeprefix(who.hub_note()).strip())
            return card
        if not follow:
            store.add_card_note(bot_id, note.removeprefix(who.hub_note()).strip())
        elif loop:
            asyncio.run_coroutine_threadsafe(_continue(bot_id, note, session_id), loop)
        return card
    if card.get("from_check"):

        if outcome == "signed_in":
            consent.mark_signed_in(bot_id, site)
            if share:
                share_browser(bot_id, site, exclude=feed_id)
        _tell_check(site, outcome, True)
        return card
    if outcome == "declined":

        note = (f"{who.hub_note()} {who.first()} closed the card without signing in to {site}. Do not "
                f"sign in there. Finish what you can without it and tell {who.first()} in a sentence what you could "
                "not check and why.")
        if loop:
            asyncio.run_coroutine_threadsafe(_continue(bot_id, note, session_id), loop)
        return card
    if outcome == "signed_in" and card.get("place") == "mac":


        note = (f"{who.hub_note()} {who.first()} signed in to {site} on {who.mac()}, in {who.possessive()} own "
                f"browser there. Carry on with what {who.first()} asked on the Mac: call mac_start with the task and "
                f"{who.possessive()} words, then mac_open {card.get('url') or site} and read it with mac_ui before "
                f"you act. If it still shows a sign-in page, tell {who.first()} exactly what the page shows.")
        if not follow:
            store.add_card_note(bot_id, note.removeprefix(who.hub_note()).strip())
        elif loop:
            asyncio.run_coroutine_threadsafe(_continue(bot_id, note, session_id), loop)
        return card
    if outcome == "signed_in":
        consent.mark_signed_in(bot_id, site)
        _tell_check(site, outcome, False)
        if share:
            share_browser(bot_id, site, exclude=feed_id)

    if share or consent.stored_mode(bot_id, site) != "ask":
        with consent.db() as c:
            c.execute("INSERT INTO requests (bot_id, site, reason, session_id, status, created, decided) "
                      "VALUES (?,?,?,?, 'allowed', ?, ?)",
                      (bot_id, site, f"{who.first()} signed in when you asked", session_id, time.time(), time.time()))
    from . import signin_notes
    if outcome == "saved" and signin_notes.mac_only(site):


        how = (f"saved a login for it in your vault, for {who.possessive()} Mac only. Never sign in to it in your own "
               f"browser. Do this site's work on {who.possessive()} Mac: call mac_start, open the sign-in page with "
               "mac_codex, and when it stops at the sign-in, call mac_codex again with sign_in set to this login's "
               "handle from browser_vault_list (the login is filled without showing it)")
    else:
        how = ("saved a login for it in your vault; sign in with your browser vault tools (they fill the password "
               "without showing it)" if outcome == "saved" else
               "signed in on your screen; the session is in your browser")
    note = (f"{who.hub_note()} {who.first()} {how}. You may use {site} for what {who.first()} asked in this "
            "conversation. Carry on with it now. " + CHECK_AGAIN.format(first=who.first()))
    if not share:
        next_step = ("Sign in with your browser vault tools." if outcome == "saved" else "Open the page again and check.")
        note = (f"{who.hub_note()} A shared sign-in is available for {site}. {next_step} "
                "Existing site permissions still apply. " + CHECK_AGAIN.format(first=who.first()))
    if not follow:
        store.add_card_note(bot_id, note.removeprefix(who.hub_note()).strip())
    elif loop:
        asyncio.run_coroutine_threadsafe(_continue(bot_id, note, session_id), loop)
    return card


def _tell_check(site: str, outcome: str, from_check: bool) -> None:
    ""
    from . import zipcheck
    try:
        zipcheck.card_answered(site, outcome, from_check)
    except Exception:
        log.warning("the morning check could not note the answer for %s", site, exc_info=True)


def _resolve_parked(bot_id: str, feed_id: int, card: dict, outcome: str, follow: bool) -> dict:
    ""

    from . import mac, openloops
    openloops.parked(bot_id, card)
    session_id, on_mac = card.get("session_id"), card.get("place") == "mac"
    if outcome == "declined":
        note = (f"{who.hub_note()} {who.first()} closed the card for this step: {card.get('why')}. Don't do that step "
                f"yourself. Finish what you can and tell {who.first()} in a sentence what is left and why.")
        if on_mac:
            mac.drop_parked(bot_id, feed_id)
    else:
        note = f"{who.hub_note()} {resume_note(card)}"
        if on_mac and not follow:
            mac.resume_parked(bot_id, feed_id, turns.current(bot_id))
    if not follow:
        store.add_card_note(bot_id, note.removeprefix(who.hub_note()).strip())
    elif loop:
        asyncio.run_coroutine_threadsafe(
            _continue(bot_id, note, session_id, mac_park=feed_id if on_mac and outcome == "done" else None), loop)
    return card


def share_browser(bot_id: str, site: str, exclude: int | None = None) -> None:
    from . import shared_sessions, signin_access
    try:
        shared_sessions.capture(bot_id, site, force=True)
    except Exception:
        log.warning("Shared sign-in save was deferred for %s", bot_id)
        return
    company = consent.base(consent.site_of(site))
    saved = shared_sessions._row(company)
    if not saved or saved["removed"] or saved["sealed"] is None:
        return
    for other in config.BOTS_BY_ID:
        if other != bot_id and signin_access.allowed(other, company):
            try:
                shared_sessions.apply(other, site)
            except Exception:
                pass
    for row in store.db().execute("SELECT * FROM feed WHERE kind=?", (KIND,)).fetchall():
        if row["id"] == exclude or row["bot_id"] == bot_id or row["bot_id"] not in config.BOTS_BY_ID:
            continue
        if not signin_access.allowed(row["bot_id"], company):
            continue
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if not isinstance(card, dict):
            continue
        if card.get("state") == "open" and card.get("purpose", "signin") == Purpose.signin \
                and consent.base(card.get("site") or "") == company:
            resolve(row["bot_id"], row["id"], "signed_in", share=False)






_SIGNED = re.compile(r"\bi(?:'ve|'m| am| have| just|'ve just| have just)?\s+(?:signed|logged)\s+(?:back\s+)?(?:in|on)\b|"
                     r"\bi(?:'?m| am) (?:back )?in(?: now)?(?:[\s,.!]*$|[,.!])|"
                     r"^(?:ok(?:ay)?[\s,.!]*)?(?:all\s+)?(?:signed|logged)\s+in(?: now)?\b", re.I)
_JUST_DONE = re.compile(r"^(?:ok(?:ay)?|alright|there|k)?[\s,.!]*(?:i'?m |i am |all |it's |its )?"
                        r"(?:done|finished|set)(?: now)?[\s,.!]*(?:(?:you can |go ahead and |please )?"
                        r"(?:try(?: it)? again|go ahead|continue|carry on|keep going)(?: now)?)?[\s.!]*$|"
                        r"^(?:ok(?:ay)?[\s,.!]*)?(?:try(?: it)? again|go ahead|carry on)(?: now)?[\s.!]*$", re.I)

_PARK_GO = re.compile(r"^(?:(?:ok(?:ay)?|alright|yes|yep|great|cool|thanks)[\s,.!]*)*(?:(?:you can|please|go ahead and)"
                      r"\s+)?(?:continue|carry on|keep going|resume|go ahead|go on|proceed|try(?: it)? again|finish it)"
                      r"(?:\s+(?:now|then|with it|from there|where you left off))?[\s.!]*$|"
                      r"\bi(?:'ve| have)?\s+(?:just\s+)?(?:did|done|uploaded|attached|added|pressed|clicked|submitted|"
                      r"finished|took care of)\b", re.I)


def _names_job(card: dict, text: str) -> bool:
    ""
    if card.get("state") != "open" or _purpose(card) not in PARKED:
        return False
    said = set(re.findall(r"\w+", text.casefold()))
    job = {w for w in re.findall(r"\w+", (card.get("job") or "").casefold()) if len(w) > 2}
    site = consent.base(card.get("site") or "").split(".")[0] if card.get("site") else ""
    return bool(job and job <= said) or bool(site and site in said)


def done_in_chat(bot_id: str, text: str) -> str | None:
    ""


    t = " ".join(str(text or "").split())
    if not t or len(t) > 160 or "?" in t:
        return None
    rows = store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                              (KIND, bot_id)).fetchall()

    rows = sorted(rows, key=lambda r: not _names_job(json.loads(r["body"] or "{}"), t))
    for row in rows:
        card = json.loads(row["body"] or "{}")
        if card.get("state") != "open":
            continue
        site = card.get("site") or ""
        named = bool(site) and consent.base(site).split(".")[0] in t.lower()
        parked = _purpose(card) in PARKED
        if _SIGNED.search(t) or _JUST_DONE.match(t) or (named and re.search(r"\b(done|finished)\b", t, re.I)) \
                or (parked and _PARK_GO.search(t)):
            resolve(bot_id, row["id"], "signed_in", follow=False)
            return site or (card.get("place") if parked else "")
        return None
    return None


async def _continue(bot_id: str, note: str, session_id: str | None = None, mac_park: int | None = None) -> None:
    ""

    from . import mac
    resumed = mac.resuming(bot_id, mac_park) if mac_park else None
    try:
        await turns.follow_up(bot_id, note, session_id, urgent=True)
    except Exception:
        log.exception("could not tell %s about the sign-in", bot_id)
    finally:
        if resumed is not None:
            resumed.done = True
