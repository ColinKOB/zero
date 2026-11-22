""


























import asyncio
import json
import logging
import threading
import time
from urllib.parse import urlsplit

from . import consent, screen, shortreply, who

log = logging.getLogger("taffy.signinclosed")

OPEN_SEC = 6 * 3600
PAGE_TEXT_MAX = 2000
TIMEOUT_SEC = 20

SIGNED_IN, NOT_YET, UNSURE = "signed_in", "not_yet", "unsure"

_PAGE_JS = ("JSON.stringify({title: document.title, url: location.href, "
            "text: (document.body ? document.body.innerText : '').slice(0, %d)})" % PAGE_TEXT_MAX)

PROMPT = ("{first} was asked to sign in to {site} in a browser, and then closed the window. Below are the browser tab's "
          "title, its address and the start of its visible text. Decide what the page shows now.\n\n"
          "Title: {title}\nAddress: {url}\nText:\n{text}\n\n"
          "Answer with one word: SIGNED_IN if it shows someone signed in to {site} (an account menu, a dashboard, a "
          "project list, their name), NOT_YET if it still asks to sign in, asks for a code, shows a human check or a "
          "sign-in error, or UNSURE if you can't tell.")

CHECK_PROMPT = ("{first} was asked to clear a human check on {site} in a browser, and then closed the window. Below are "
                "the browser tab's title, its address and the start of its visible text. Decide what the page shows "
                "now.\n\nTitle: {title}\nAddress: {url}\nText:\n{text}\n\n"
                "Answer with one word: CLEARED if it shows the site's own page with no human check, STILL if it still "
                "shows a human check or says the check failed, or UNSURE if you can't tell.")

_closed: dict[str, float] = {}
_opened: dict[str, dict] = {}
_lock = threading.Lock()


def opened(bot_id: str, url: str, target: str | None) -> None:
    ""
    with _lock:
        _opened[bot_id] = {"url": url, "target": target, "at": time.time()}


def note_closed(bot_id: str) -> None:
    ""
    with _lock:
        _closed[bot_id] = time.time()


def closed_since(bot_id: str, began: float) -> bool:
    ""
    with _lock:
        return _closed.get(bot_id, 0) > began


def forget(bot_id: str) -> dict | None:
    ""

    with _lock:
        found = _opened.pop(bot_id, None)
    return found if found and time.time() - found["at"] < OPEN_SEC else None


def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")


def same_address(now: str, was: str) -> bool:
    ""

    a, b = urlsplit(now or ""), urlsplit(was or "")
    path = (a.path or "").rstrip("/")
    return bool(path) and _host(now) == _host(was) and path == (b.path or "").rstrip("/")


def read_page(bot_id: str, site: str, record: dict | None) -> dict | None:
    ""

    from . import cdp_storage
    profile = screen.profile_home(bot_id) / "bot-desktop" / "browser-profile"
    want = consent.base(_host(site) or site)
    try:
        with cdp_storage.Browser(profile) as browser:
            tabs = [t for t in browser.call("Target.getTargets").get("targetInfos", []) if t.get("type") == "page"]
            tab = next((t for t in tabs if record and record.get("target") and t.get("targetId") == record["target"]),
                       None)
            if tab is None:
                tab = next((t for t in tabs if want and consent.base(_host(t.get("url", ""))) == want), None)
            if tab is None:
                return None
            session = browser.call("Target.attachToTarget", {"targetId": tab["targetId"], "flatten": True})["sessionId"]
            try:
                out = browser.call("Runtime.evaluate", {"expression": _PAGE_JS, "returnByValue": True}, session)
            finally:
                browser.call("Target.detachFromTarget", {"sessionId": session})
            found = json.loads(out.get("result", {}).get("value") or "{}")
            return {"url": str(found.get("url") or tab.get("url") or ""),
                    "title": str(found.get("title") or tab.get("title") or ""), "text": str(found.get("text") or "")}
    except Exception as e:
        log.info("could not read %s's browser after its sign-in closed: %s", bot_id, str(e)[:200])
        return None


async def judge(page: dict | None, site: str, opened_url: str = "", check: bool = False) -> str:
    ""



    if not page or not page.get("url", "").startswith("http"):
        return UNSURE
    if consent.base(_host(page["url"])) != consent.base(_host(site) or site):
        return UNSURE
    if opened_url and not check and same_address(page["url"], opened_url):
        return NOT_YET
    prompt = (CHECK_PROMPT if check else PROMPT).format(
        first=who.first(), site=consent.base(_host(site) or site), title=page["title"][:200],
        url=page["url"][:300], text=page["text"][:PAGE_TEXT_MAX])
    try:
        word = await shortreply.ask_helper(prompt, timeout=TIMEOUT_SEC, max_tokens=5, effort="low")
    except Exception as e:
        log.info("could not judge a closed sign-in: %s", str(e)[:200])
        return UNSURE
    words = {"CLEARED": SIGNED_IN, "STILL": NOT_YET} if check else {"SIGNED_IN": SIGNED_IN, "NOT_YET": NOT_YET}
    return words.get(word.strip().strip(".").upper(), UNSURE)


def signed_in(bot_id: str, site: str, task_id: str | None = None, request_id: int | None = None) -> None:
    ""
    from . import board, signin
    if request_id is None:



        found = consent.site_of(site or "")
        if found:
            consent.mark_signed_in(bot_id, found)
            signin.share_browser(bot_id, found)
    else:
        signin.resolve(bot_id, request_id, "signed_in")
    if task_id:
        board.answer_block(task_id, f"{who.first()} signed in to {site or 'the site'} in your browser. "
                                    "The session is saved in your browser profile; continue with it.")


def _open_card(bot_id: str, request_id: int) -> dict | None:
    from . import signin
    try:
        card = signin.card_for(bot_id, request_id)
    except signin.AskError:
        return None
    if card.get("state") != "open" or card.get("purpose", "signin") not in signin.ASKED:
        return None
    return card


def card_for_site(bot_id: str, url: str) -> int | None:
    ""
    from . import signin, store
    want = consent.base(_host(url))
    if not want:
        return None
    for row in store.db().execute("SELECT id, body FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 40",
                                  (signin.KIND, bot_id)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if isinstance(card, dict) and card.get("state") == "open" and \
                card.get("purpose", "signin") == signin.Purpose.signin and \
                consent.base(_host(card.get("url") or "") or card.get("site") or "") == want:
            return int(row["id"])
    return None


async def settle(bot_id: str, site: str, record: dict | None, task_id: str | None = None,
                 request_id: int | None = None) -> str:
    ""
    from . import board, signin
    held = await asyncio.to_thread(_open_card, bot_id, request_id) if request_id is not None else None
    check = bool(held) and held.get("purpose") == signin.Purpose.challenge

    site = (held.get("url") or held.get("site") or site) if check else site
    site = site or (record or {}).get("url") or ""
    shown = consent.base(_host(site) or site) or "the site"
    page = await asyncio.to_thread(read_page, bot_id, site, record)
    from . import shortreply
    with shortreply.reading((bot_id,)):
        verdict = await judge(page, site, (record or {}).get("url") or "", check)
    log.info("%s's sign-in window for %s closed without I'm Signed In: %s", bot_id, shown, verdict)
    first = who.first()
    if check:
        await _settle_check(bot_id, request_id, held, verdict, shown)
    elif verdict == SIGNED_IN:
        await asyncio.to_thread(signed_in, bot_id, site, task_id, request_id)
    elif verdict == NOT_YET:

        if task_id:
            await asyncio.to_thread(board.comment, task_id, f"{first} closed the sign-in window for {shown} before "
                                                            "signing in; the page still asks to sign in.")
    elif task_id:
        await asyncio.to_thread(board.answer_block, task_id, (
            f"{first} closed the sign-in window for {shown}, and your browser didn't show whether the sign-in finished. "
            f"Open {shown} in your browser and check whether you are signed in before you ask again. If you are, carry "
            f"on with the task. If it still asks you to sign in, block again with \"Sign in to {shown}\" and its "
            "sign-in page address."))
    elif request_id is not None:
        card = await asyncio.to_thread(_open_card, bot_id, request_id)
        if card is not None:
            await signin._continue(bot_id, (
                f"{who.hub_note()} {first} closed the sign-in window for {shown}, and your browser didn't show "
                f"whether the sign-in finished. Open the page again and check before you ask again. If you are signed "
                f"in, close the sign-in card with close_sign_in_card and carry on with what {first} asked. If it still "
                f"asks you to sign in, leave the card open and tell {first} in one sentence what the page shows."),
                card.get("session_id"))
    return verdict


async def _settle_check(bot_id: str, request_id: int, card: dict, verdict: str, shown: str) -> None:
    ""

    from . import board, signin
    if verdict == SIGNED_IN:
        await asyncio.to_thread(signin.resolve, bot_id, request_id, "signed_in")
    elif verdict == UNSURE:
        first = who.first()
        note = (f"{first} closed the window for the human check on {shown}, and your browser didn't show whether it "
                f"cleared. Take a fresh browser_snapshot of the page. If the check is gone, close the card with "
                f"close_sign_in_card and carry on with what {first} asked. If it still shows, leave the card open and "
                "stop. Never press the check yourself.")
        task = (await asyncio.to_thread(signin._card_row, bot_id, request_id))["task_id"]
        if task:
            await asyncio.to_thread(board.answer_block, task, note)
        else:
            await signin._continue(bot_id, f"{who.hub_note()} {note}", card.get("session_id"))


def _closed_check(bot_id: str) -> None:
    ""
    from . import signin
    request_id = signin.open_check(bot_id)
    card = _open_card(bot_id, request_id) if request_id is not None else None
    if card is not None:
        start(bot_id, card.get("url") or card.get("site") or "", request_id=request_id)


async def _logged(job, bot_id: str) -> None:
    ""
    try:
        await job
    except Exception:
        log.exception("could not settle %s's closed sign-in", bot_id)


def start(bot_id: str, site: str, task_id: str | None = None, request_id: int | None = None) -> None:
    ""

    from . import signin
    record = forget(bot_id)
    if task_id is None and (request_id is None or _open_card(bot_id, request_id) is None):
        return
    job = _logged(settle(bot_id, site, record, task_id, request_id), bot_id)
    loop = signin.loop
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(job, loop)
    else:
        asyncio.run(job)


def released(bot_id: str) -> None:
    ""

    with _lock:
        record = _opened.get(bot_id)
    if not record or time.time() - record["at"] >= OPEN_SEC:
        _closed_check(bot_id)
        return
    request_id = card_for_site(bot_id, record["url"])
    if request_id is None:
        forget(bot_id)
        _closed_check(bot_id)
        return
    start(bot_id, record["url"], request_id=request_id)


def _waiting_task(bot_id: str, url: str) -> str | None:
    ""
    import re
    from . import blockask, board
    want = consent.base(_host(url))
    for t in board.grouped()["needs_you"]:
        if t.get("raw_status") not in ("blocked", "triage") or (t.get("assignee") or "default") != bot_id:
            continue
        reason = t.get("raw_reason") or ""
        if not blockask.signs_in(reason, t.get("title") or ""):
            continue
        found = re.search(r"https?://[^\s)]+", "\n".join(x for x in (reason, t.get("body")) if x))
        if want and found and consent.base(_host(found.group(0))) == want:
            return t["id"]
    return None


def abandoned(bot_id: str) -> None:
    ""

    with _lock:
        record = _opened.get(bot_id)
    if not record or time.time() - record["at"] >= OPEN_SEC:
        _closed_check(bot_id)
        return
    request_id = card_for_site(bot_id, record["url"])
    task_id = None if request_id is not None else _waiting_task(bot_id, record["url"])
    if request_id is None and task_id is None:
        forget(bot_id)
        return
    start(bot_id, _host(record["url"]), task_id=task_id, request_id=request_id)
