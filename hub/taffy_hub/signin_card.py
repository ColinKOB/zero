""




















import asyncio
import json
import logging
import time

from . import consent, signin, signin_notes, signinclosed, shortreply, store, who

log = logging.getLogger("taffy.signin_card")

POLL_SEC = 4
WATCH_SEC = 180
LOOK_ANYWAY_SEC = 40
CODE_SEC = 45
SETTLED = ("signed_in", "saved", "saved_mac")
OPEN_AGAIN = ("wrong", "needs_you")

PROMPT = ("{first} saved a login for {site}, and a helper just used it to sign in, in its own browser. Below are the "
          "browser tab's title, its address and the start of its visible text. Decide what the page shows now.\n\n"
          "Title: {title}\nAddress: {url}\nText:\n{text}\n\n"
          "Answer with one word: SIGNED_IN if it shows someone signed in to {site} (an account menu, a dashboard, their "
          "name), WRONG if it says the username or password is wrong or not recognized, CODE if it asks for a code, a "
          "passkey, a security question or a human check, NOT_YET if it still shows the sign-in form with no error or "
          "is loading, or UNSURE if you can't tell.")


def set_progress(bot_id: str, request_id: int, progress: str, *, state: str | None = None,
                 reason: str | None = None) -> dict:
    row = signin._card_row(bot_id, request_id)
    card = json.loads(row["body"] or "{}")
    card["progress"] = progress
    card["progress_at"] = time.time()
    if state:
        card["state"] = state
    if reason is not None:
        card["failure_reason"] = reason
    store.update_feed(request_id, row["title"], json.dumps(card))
    return card


def check(bot_id: str, request_id: int) -> dict:
    ""
    card = signin.card_for(bot_id, request_id)
    if signin._purpose(card) != signin.Purpose.signin or card.get("state") != "open":
        raise signin.AskError("This card has already moved on.")
    if card.get("place") == "mac":
        raise signin.AskError("This sign-in happens on your Mac.")
    return card


def _filled_since(bot_id: str, site: str, since: float) -> bool:
    ""
    want = consent.base(site)
    try:
        with consent.db() as c:
            rows = c.execute("SELECT site FROM signins WHERE bot_id=? AND at>=?", (bot_id, since)).fetchall()
    except Exception:
        return False
    return any(consent.base(r["site"] if hasattr(r, "keys") else r[0]) == want for r in rows)


async def judge(page: dict | None, site: str, bot_id: str = "") -> str:
    ""
    if not page or not page.get("url", "").startswith("http"):
        return "UNSURE"
    if consent.base(signinclosed._host(page["url"])) != consent.base(site):
        return "UNSURE"
    prompt = PROMPT.format(first=who.first(), site=consent.base(site), title=page["title"][:200],
                           url=page["url"][:300], text=page["text"][:signinclosed.PAGE_TEXT_MAX])
    try:
        with shortreply.reading((bot_id,) if bot_id else ()):
            word = await shortreply.ask_helper(prompt, timeout=signinclosed.TIMEOUT_SEC, max_tokens=5, effort="low")
    except Exception as e:
        log.info("could not judge a card sign-in: %s", str(e)[:200])
        return "UNSURE"
    word = word.strip().strip(".").upper()
    return word if word in ("SIGNED_IN", "WRONG", "CODE", "NOT_YET") else "UNSURE"


def _open_again(bot_id: str, request_id: int, progress: str, site: str) -> None:
    name = consent.base(site) or site
    reason = (f"That username or password didn't work on {name}." if progress == "wrong"
              else f"{name} wants a code or a check. Take over to finish.")
    set_progress(bot_id, request_id, progress, state="open", reason=reason)
    store.add_card_note(bot_id, f"The sign-in to {consent.base(site)} didn't go through: {reason} "
                                f"{who.possessive()} card is open again. Don't try that login again; stop and wait for "
                                f"{who.first()}.")


async def watch(bot_id: str, request_id: int, site: str, started: float, sleep=asyncio.sleep, clock=time.time) -> str:
    ""
    last, code_since = None, None
    while clock() - started < WATCH_SEC:
        await sleep(POLL_SEC)
        try:
            card = signin.card_for(bot_id, request_id)
        except signin.AskError:
            return "gone"
        if card.get("progress") != "signing_in":
            return str(card.get("progress") or "")
        if not _filled_since(bot_id, site, started) and clock() - started < LOOK_ANYWAY_SEC:
            continue
        page = await asyncio.to_thread(signinclosed.read_page, bot_id, site, None)
        shape = (page or {}).get("url"), (page or {}).get("title"), ((page or {}).get("text") or "")[:400]
        if shape == last and code_since is None:
            continue
        last = shape
        verdict = await judge(page, site, bot_id)
        if verdict == "SIGNED_IN":
            set_progress(bot_id, request_id, "signed_in")
            return "signed_in"
        if verdict == "WRONG":
            _open_again(bot_id, request_id, "wrong", site)
            return "wrong"
        if verdict == "CODE":
            code_since = code_since or clock()
            if clock() - code_since >= CODE_SEC:
                _open_again(bot_id, request_id, "needs_you", site)
                return "needs_you"
        else:
            code_since = None
    set_progress(bot_id, request_id, "saved")
    return "saved"


def start_watch(bot_id: str, request_id: int, site: str) -> None:
    if signin_notes.mac_only(site):
        set_progress(bot_id, request_id, "saved_mac")
        return
    if not signin.loop:
        set_progress(bot_id, request_id, "saved")
        return

    async def run():
        try:
            await watch(bot_id, request_id, site, time.time())
        except Exception:
            log.warning("card sign-in watch failed for %s", bot_id, exc_info=True)
            try:
                set_progress(bot_id, request_id, "saved")
            except Exception:
                pass
    asyncio.run_coroutine_threadsafe(run(), signin.loop)
