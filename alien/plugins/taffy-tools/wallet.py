""














import itertools
import json
import re
import logging
import time

from . import consent, home

log = logging.getLogger("taffy.wallet")

SCREEN_TOOLS = {"browser_vision", "vision_analyze", "computer_use"}






LOCK_IDLE_SEC = 2 * 3600


def _call(op: str, args: dict, session_id: str = "", timeout: float = 20) -> dict:
    from . import mac
    reply = mac._call(op, args, timeout, session_id=session_id, area="wallet")
    if not reply.get("ok") and str(reply.get("error") or "").startswith(("Could not reach", "The hub")):
        return {"ok": False, "error": "Your saved items can't be reached right now. Try again in a minute."}
    return reply


def _page_origin(task_id: str) -> str:
    from . import gate
    return gate._here(task_id)


def _page_url(task_id: str) -> str:
    try:
        from tools.browser_vault_tool import _eval_js
        res = _eval_js(task_id or "default", "window.location.href")
        return str(res.get("result") or "").strip().strip('"') if res.get("success") else ""
    except Exception:
        log.debug("page address lookup failed", exc_info=True)
        return ""


def _out(reply: dict) -> str:
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "That didn't work. Try again in a minute."})
    return json.dumps({"result": reply.get("result")})




LIST_SCHEMA = {
    "name": "wallet_list",
    "description": (
        "List the cards, addresses and ID numbers Colin saved for you: each one's name, its handle, and details that "
        "are not secret (a card's brand and last four digits, an address's city). You never see the numbers. Fill an "
        "address with browser_vault_fill and its handle. A card needs use_card first, and an ID number use_id. "
        "Check it before you buy anything. With no card, take the checkout to the pay button and hand that press to "
        "Colin with hand_over (kind final)."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def wallet_list(args: dict, **kwargs) -> str:
    return _out(_call("list", {}, kwargs.get("session_id") or ""))


USE_CARD_SCHEMA = {
    "name": "use_card",
    "description": (
        "Get Colin's okay to pay with one of his saved cards, before you go to checkout. Open the cart first and check "
        "every item and quantity against what he asked for. Give the card, the shop's "
        "site, the total you expect to pay in dollars and what you're buying in a few words. He gets a card to "
        "approve it, at any amount, even when he asked for the purchase himself. The card is the question, so don't "
        "ask him in the chat first. Stop and wait for the note. It gives you an approval: go to the checkout page and "
        "call browser_vault_fill with the card's handle and the approval, then place the order and call "
        "report_purchase. An approval works once, only on that site, for 10 minutes. Never pay any other way."
    ),
    "parameters": {"type": "object", "properties": {
        "item": {"type": "string", "description": "The card's name from wallet_list, such as \"Visa ending 4242\"."},
        "site": {"type": "string", "description": "The shop's site, such as amazon.com."},
        "amount": {"type": "number", "description": "The total you expect to pay, in dollars, with tax and shipping "
                                                    "if you know them."},
        "what": {"type": "string", "description": "What you're buying, in a few words (\"air filters\")."},
        "currency": {"type": "string", "description": "Leave out for US dollars."},
    }, "required": ["item", "site", "amount", "what"]},
}


def use_card(args: dict, **kwargs) -> str:
    args = dict(args or {})
    args["page_origin"] = _page_origin(kwargs.get("task_id") or "")
    return _out(_call("use_card", args, kwargs.get("session_id") or ""))


USE_ID_SCHEMA = {
    "name": "use_id",
    "description": (
        "Get Colin's okay to put one of his saved ID numbers (a driver's license, passport or Social Security number) "
        "into a form. He always gets a card to approve it; stop and wait for the note. With his approval, call "
        "browser_vault_fill on that page with the item's handle and the approval. A saved ID works on any site he "
        "approves it for. His name, date of birth, birthplace, phone, email and home address are on his Your Info, "
        "which you are given: type those yourself."
    ),
    "parameters": {"type": "object", "properties": {
        "item": {"type": "string", "description": "The ID's name from wallet_list."},
        "site": {"type": "string", "description": "The site of the form, such as dmv.ca.gov."},
        "why": {"type": "string", "description": "What it's for, in a few words, written to Colin (\"renew your "
                                                 "registration\")."},
    }, "required": ["item", "site", "why"]},
}


def use_id(args: dict, **kwargs) -> str:
    args = dict(args or {})
    args["page_origin"] = _page_origin(kwargs.get("task_id") or "")
    return _out(_call("use_id", args, kwargs.get("session_id") or ""))




ASK_SCHEMA = {
    "name": "ask_for_wallet_item",
    "description": (
        "Ask Colin to save a card, an ID number or a login when you need one he hasn't given you. When you need his "
        "home address or date of birth, it is on his Your Info, which you are given: type it into the form yourself. "
        "When one is "
        "missing there and you don't know it, ask for it (kind address, or identity with id_type dob) and his answer "
        "is saved to Your Info. When he tells you one, save it with save_your_info and never ask again. He gets "
        "a card in this chat to add it; the values go straight to your saved items and you never see them. Then stop "
        "and say nothing about the card (it asks him itself); a note arrives here when it is saved. Ask for a card "
        "only once he asks you to pay yourself next time; until then, hand him the pay press with hand_over. Never "
        "ask him to type a card number, an ID number or a password into the chat, and never save one you read "
        "somewhere."
    ),
    "parameters": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["card", "address", "identity", "login"]},
        "id_type": {"type": "string", "enum": ["drivers_license", "passport", "ssn", "dob"],
                    "description": "For kind identity: which ID you need, so his card names it."},
        "why": {"type": "string", "description": "What you need it for, in a few words, written to Colin as \"you\" "
                                                 "(\"order your filters\")."},
    }, "required": ["kind", "why"]},
}


def ask_for_wallet_item(args: dict, **kwargs) -> str:
    return _out(_call("ask", args or {}, kwargs.get("session_id") or ""))




SAVE_INFO_SCHEMA = {
    "name": "save_your_info",
    "description": (
        "Save one of Colin's own details to his Your Info, where every Zip gets it: his legal name (with his middle "
        "name), date of birth, birthplace, phone, email or home address. Use it when he tells you one that is missing "
        "or has changed, in this chat or in an answer to your question, then tell him in one line that it's saved. "
        "Only what he said about himself; never someone else's, and never an ID number (those go through "
        "ask_for_wallet_item). Never ask him for something his Your Info already holds."
    ),
    "parameters": {"type": "object", "properties": {
        "field": {"type": "string", "enum": ["legal_name", "birthday", "birthplace", "phone", "email", "address"]},
        "value": {"type": "string", "description": "The value as he gave it; a date of birth as YYYY-MM-DD. Leave "
                                                   "out for an address."},
        "address": {"type": "object", "description": "For field address: its parts.", "properties": {
            k: {"type": "string"} for k in ("line1", "line2", "city", "region", "postal_code", "country")}},
    }, "required": ["field"]},
}


def save_your_info(args: dict, **kwargs) -> str:
    args = dict(args or {})
    value = args.get("address") if args.get("field") == "address" else args.get("value")
    return _out(_call("your_info", {"field": args.get("field"), "value": value}, kwargs.get("session_id") or ""))


REPORT_SCHEMA = {
    "name": "report_purchase",
    "description": (
        "Say how an order went right after you placed it: confirmed when the shop showed an order confirmation, "
        "uncertain when you can't tell. Give the purchase number you got with the approval. Never place an uncertain "
        "order again; tell Colin what the page showed."
    ),
    "parameters": {"type": "object", "properties": {
        "purchase": {"type": "string", "description": "The purchase number, such as p_1a2b3c4d5e6f."},
        "outcome": {"type": "string", "enum": ["confirmed", "uncertain"]},
    }, "required": ["purchase", "outcome"]},
}


def report_purchase(args: dict, **kwargs) -> str:


    return _out(_call("report", args or {}, kwargs.get("session_id") or ""))


TOOLS = (("wallet_list", LIST_SCHEMA, wallet_list), ("use_card", USE_CARD_SCHEMA, use_card),
         ("use_id", USE_ID_SCHEMA, use_id), ("ask_for_wallet_item", ASK_SCHEMA, ask_for_wallet_item),
         ("save_your_info", SAVE_INFO_SCHEMA, save_your_info),
         ("report_purchase", REPORT_SCHEMA, report_purchase))




def redeem(approval: str, handle: str, origin: str, total_cents=None) -> dict:
    ""

    reply = _call("redeem", {"token": approval, "handle": handle, "origin": origin, "total_cents": total_cents})
    if not reply.get("ok"):
        return {"ok": False, "error": reply.get("error") or "The approval could not be checked. Nothing was filled."}
    result = reply.get("result") or {}
    if not isinstance(result, dict):
        return {"ok": False}
    return result


def install_redeemer() -> bool:
    try:
        from tools import browser_vault_tool
    except Exception:
        return False
    register = getattr(browser_vault_tool, "register_approval_redeemer", None)
    if register is None:
        log.warning("this Hermes has no card approvals (hermes-wallet.patch); card and ID fills stay off")
        return False
    register(redeem)
    return True




def _kind(handle: str) -> str:
    try:
        from agent.vault_backends import backend_for_handle
        b = backend_for_handle(handle)
        meta = b.get_meta(handle) if b else None
        return str(getattr(meta, "kind", "") or "")
    except Exception:
        log.debug("vault item lookup failed", exc_info=True)
        return ""


def _lock_table(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS wallet_locks (bot TEXT, origin TEXT, at REAL, session TEXT DEFAULT '', "
              "PRIMARY KEY (bot, origin))")


def _browser_session(task_id: str) -> str:
    ""
    try:
        from tools import browser_tool
        task = task_id or "default"
        key = browser_tool._last_active_session_key.get(task) or task
        return str((browser_tool._active_sessions.get(key) or {}).get("session_name") or "")
    except Exception:
        log.debug("browser session lookup failed", exc_info=True)
        return ""


def lock_screen(bot: str, origin: str, task_id: str = "") -> None:
    ""


    with consent.db() as c:
        _lock_table(c)
        c.execute("INSERT OR REPLACE INTO wallet_locks (bot, origin, at, session) VALUES (?,?,?,?)",
                  (bot, origin, time.time(), _browser_session(task_id)))


def browser_used(tool_name: str, bot: str, task_id: str = "") -> None:
    ""

    if not (tool_name.startswith("browser_") or tool_name in SCREEN_TOOLS):
        return
    here, session = _page_origin(task_id), _browser_session(task_id)
    if not (here or session):
        return
    with consent.db() as c:
        _lock_table(c)
        c.execute("UPDATE wallet_locks SET at=? WHERE bot=? AND at>? AND (origin=? OR (session<>'' AND session=?))",
                  (time.time(), bot, time.time() - LOCK_IDLE_SEC, here, session))


def unlock_screen(bot: str, origin: str | None = None) -> None:
    ""
    with consent.db() as c:
        _lock_table(c)
        if origin is None:
            c.execute("DELETE FROM wallet_locks WHERE bot=?", (bot,))
        else:
            c.execute("DELETE FROM wallet_locks WHERE bot=? AND origin=?", (bot, origin))


def _locked_origins(bot: str) -> set[str]:
    try:
        with consent.db() as c:
            _lock_table(c)
            rows = c.execute("SELECT origin FROM wallet_locks WHERE bot=? AND at>?",
                             (bot, time.time() - LOCK_IDLE_SEC)).fetchall()
    except Exception:

        log.warning("could not read the wallet fill locks", exc_info=True)
        return {ANY_SITE}
    return {str(r["origin"] or "") for r in rows}


ANY_SITE = "*"


def _held(origins: set[str], here: str) -> bool:
    return ANY_SITE in origins or here in origins


def scripts_locked(bot: str) -> bool:
    ""
    return bool(_locked_origins(bot))


_SUBMIT_KEYS = {"enter", "return", "numpadenter", "space", "spacebar"}


def press_block(key: str, bot: str, task_id: str) -> dict | None:
    ""


    key = str(key or "")
    if ("space" if key == " " else key.split("+")[-1].strip().lower()) not in _SUBMIT_KEYS:
        return None
    origins = _locked_origins(bot)
    here = _page_origin(task_id)
    if not origins or (here and not _held(origins, here)):
        return None
    return {"action": "block", "message": (
        f"Not done: {key.strip() or 'Space'} can place the order on a page with saved details filled in. Click the "
        "order button instead (browser_click), so the order goes through once and Colin gets a receipt.")}


def screen_block(tool_name: str, bot: str, task_id: str) -> dict | None:
    ""
    if tool_name not in SCREEN_TOOLS:
        return None
    origins = _locked_origins(bot)
    if not origins:
        return None
    here = _page_origin(task_id)
    if here and not _held(origins, here) and tool_name != "computer_use":
        return None
    return {"action": "block", "message": (
        "Not done: saved details from the Wallet are filled in on a page in your browser, so screenshots and the "
        "screen tools are off there until the order is placed. Use browser_snapshot to read the page.")}




_fills: dict[tuple[str, str], tuple[str, str, bool, int]] = {}
_last_fill: dict[tuple[str, str], int] = {}
_fill_numbers = itertools.count()


def _start_fill(bot: str, call: str, here: str, kind: str, task_id: str) -> None:
    n = next(_fill_numbers)
    _fills[(bot, call)] = (here, kind, _held(_locked_origins(bot), here), n)
    _last_fill[(bot, here)] = n
    lock_screen(bot, here, task_id)


def fill_done(bot: str, ok: bool, call: str = "", purchase: str = "") -> None:
    ""


    if purchase and not ok:
        _call("unfill", {"purchase": purchase}, timeout=10)
    fill = _fills.pop((bot, call), None)
    if fill is None:
        return
    origin, kind, was_locked, n = fill
    if not ok and not was_locked and _last_fill.get((bot, origin)) == n:
        unlock_screen(bot, origin)


def _address_site_ok(here: str, request: str) -> bool:
    ""

    name = consent.base(consent.site_of(here))
    said = request.lower()
    if name and (name in said or name.split(".")[0] in re.findall(r"[a-z0-9-]+", said)):
        return True
    reply = _call("active", {"origin": here}, timeout=10)
    return bool(reply.get("ok") and reply.get("result"))


def fill_block(args: dict, bot: str, session_id: str, kanban_task: str, task_id: str, told: dict,
               told_key: str, call: str = "") -> tuple[bool, dict | None, bool]:
    ""




    handle = str(args.get("handle") or "")
    kind = _kind(handle)
    if not kind:
        return True, _block("Not filled: that saved item couldn't be read. Call browser_vault_list and use a handle "
                            "from it."), False
    if kind in ("payment", "identity"):
        if not str(args.get("approval") or "").strip():
            tool = "use_card" if kind == "payment" else "use_id"
            return True, _block(f"Not filled: this needs Colin's okay first. Call {tool}, then call "
                                "browser_vault_fill again with the approval it gives you."), False
        here = _page_origin(task_id)
        if not here:
            return True, _block("Not filled: open the checkout page first, then call browser_vault_fill again."), False
        _start_fill(bot, call, here, kind, task_id)
        return True, None, True
    if kind != "address":
        return False, None, False
    here = _page_origin(task_id)
    if not here.startswith("https://"):
        return True, _block("Not filled: open the site's secure checkout or form page first."), False
    reply = _call("fill_check", {"handle": handle}, session_id)
    result = reply.get("result") or {}
    if not reply.get("ok") or not result.get("known") or not result.get("allowed"):
        return True, _block("Not filled: Colin hasn't given you this address. Ask with ask_for_wallet_item if you "
                            "need it."), False
    asked = consent.live_pass(session_id=session_id or None, task_id=kanban_task or None)
    if asked is None:
        return True, _block("Not filled: fill Colin's address only for something Colin asked for."), False
    if not _address_site_ok(here, str(asked.get("text") or "")):
        return True, _block("Not filled: Colin's address goes only on a site he named or where he approved the "
                            "purchase. Call use_card with the site and the total first, or ask Colin in a sentence."), \
            False
    told[told_key] = (f"filled your address on {consent.site_of(here)}", time.time())
    _start_fill(bot, call, here, kind, task_id)
    return True, None, True


def _block(message: str) -> dict:
    return {"action": "block", "message": home.personalize(message)}




_clicking: dict[str, tuple[str, str, str, str]] = {}


def after_click(tool_name: str, bot: str, result, task_id: str = "", call: str = "") -> None:
    ""




    pending = _clicking.get(bot)
    if pending is None or pending[0] != tool_name or pending[3] != call:
        return
    del _clicking[bot]
    try:
        out = json.loads(result) if isinstance(result, str) else result if isinstance(result, dict) else {}
    except ValueError:
        out = {}
    failed = isinstance(out, dict) and (out.get("success") is False or (bool(out.get("error")) and not out.get("success")))
    _call("clicked", {"origin": pending[1], "ok": not failed}, timeout=10)
    after = _page_url(task_id)
    if not failed and pending[2] and after and after != pending[2]:
        unlock_screen(bot, pending[1])


def _page_total(task_id: str) -> int | None:
    ""
    try:
        from tools.browser_vault_tool import _read_order_total
        return _read_order_total(task_id or "default")
    except Exception:
        return None


_COMMITS = ("book", "submit")


def commit_click(label: str, kind: str | None, bot: str, task_id: str, tool_name: str = "browser_click", call: str = "") \
        -> dict | None | bool:
    ""





    from . import tiers
    final = tiers.final_booking(label)
    if not (final or kind in _COMMITS or tiers.bare_confirm(label)) or not _locked_origins(bot):
        return False
    here = _page_origin(task_id)
    if not here:
        return _block("Not done: could not check which page is open. Try the click again in a moment.")
    if not _held(_locked_origins(bot), here):
        return False

    reply = _call("submit" if final else "step", {"origin": here, "total_cents": _page_total(task_id)}, timeout=10)
    if not reply.get("ok"):
        return {"action": "block", "message": home.personalize(
            f"Not done: {reply.get('error') or 'the total could not be checked. Try again in a minute.'}")}
    result = reply.get("result")
    if isinstance(result, dict) and result.get("error"):
        return {"action": "block", "message": home.personalize(f"Not done: {result['error']}")}
    if not result:
        return False
    if not final:
        return False
    _clicking[bot] = (tool_name, here, _page_url(task_id), call)
    return None


def pay_click(label: str, bot: str, task_id: str, tool_name: str = "browser_click", call: str = "") \
        -> dict | None | bool:
    ""

    from . import tiers
    kind = tiers.order_click(label) or ("checkout" if tiers.bare_confirm(label) else None)
    if kind is None:
        return False
    here = _page_origin(task_id)
    if not here:
        return False
    total = _page_total(task_id)
    if kind == "submit" or tiers.checkout_can_order(label):
        reply = _call("submit", {"origin": here, "total_cents": total}, timeout=10)
        result = reply.get("result") if reply.get("ok") else None
        if isinstance(result, dict) and result.get("error"):
            return {"action": "block", "message": home.personalize(f"Not done: {result['error']}")}
        if result:
            _clicking[bot] = (tool_name, here, _page_url(task_id), call)
            return None
        if kind == "submit":
            return {"action": "block", "message": (
                "Not done: an order button works once, right after you fill the card with an approval on this page. "
                "Call use_card first if you haven't.")}
        if total is None:


            return {"action": "block", "message": home.personalize(
                "Not done: on some sites this Checkout button places the order, and I can't read a total on this "
                "page. Fill the card with browser_vault_fill first, or tell Colin the total in a sentence.")}
    reply = _call("step", {"origin": here, "total_cents": total}, timeout=10)
    result = reply.get("result") if reply.get("ok") else None
    if isinstance(result, dict) and result.get("error"):
        return {"action": "block", "message": home.personalize(f"Not done: {result['error']}")}
    if result:
        return None
    return {"action": "block", "message": home.personalize(
        "Not done: checkout opens only for a purchase Colin approved. Call use_card with the site, the total and "
        "what you're buying first.")}
