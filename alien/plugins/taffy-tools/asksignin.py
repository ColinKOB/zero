"""ask_to_sign_in: a bot in a chat asks Colin to sign in to a site, right away, and close_sign_in_card: it closes that
card when it no longer needs it (see the hub's signin.py). close_card does the same for its own okay cards (a send,
a publish), when Colin's later words make one wrong."""

import json
import time

from . import consent, gate, home, mac

SCHEMA = {
    "name": "ask_to_sign_in",
    "description": (
        "Ask Colin to sign in to a website you need for what he asked, when you have no saved login for it or "
        "the saved one failed. If browser_vault_list shows a login for the site, sign in with browser_vault_fill "
        "first; being signed out is not a reason to ask. He gets a card in this chat (save the login to your vault, or sign in himself on "
        "your screen) and a notification. Use it in your first steps, before any long work. Then leave that site "
        "alone, finish any part of his request that does not need it, and tell him in one short line what you'll "
        "do once he's signed in. A note arrives in this chat when he's done (or he tells you), and you carry on "
        "then: open the page again and check, and if you still cannot read it, tell him exactly what the page "
        "shows. If he asks you to ask again, call it again: his card moves to the bottom of the "
        "chat. Give the site's sign-in page address and what you need it for, in a few words "
        "(\"publish the Halloween update\"). Colin reads that reason on the card, so write it to him: \"see your last "
        "order\", never \"see Colin's last order\". If he then gets it done another way or you no longer need it, "
        "close the card with close_sign_in_card. In a board task, use kanban_block with \"Sign in to <site>\" "
        "instead."
    ),
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string", "description": "The sign-in page, starting with https://."},
        "why": {"type": "string", "description": "What you need it for, in a few words, written to Colin as "
                "\"you\" (he reads it on the card): \"see your last order and price\"."},
        "saved_login_failed": {"type": "boolean", "description": "True only if you tried the saved login and "
                               "the site refused it."},
    }, "required": ["url", "why"]},
}


def ask_to_sign_in(args: dict, **kwargs) -> str:
    # Oct 8: School asked Colin to sign in to eLearning while its saved login sat unused in the vault.
    site = consent.site_of(str((args or {}).get("url") or ""))
    tried = (args or {}).get("saved_login_failed") or \
        time.time() - gate._filled_at(gate._fill_key(kwargs.get("session_id") or "", "")) < 1800
    if site and not tried and consent.base(site) in {consent.base(s) for s in gate._saved_sites(gate._bot())}:
        return json.dumps({"error": f"You have a saved login for {site}. Sign in with browser_vault_list and "
                                    "browser_vault_fill instead of " + home.personalize("asking Colin") + ". Ask only "
                                    "if the site refuses it, with saved_login_failed set to true."})
    reply = mac._call("ask_sign_in", args or {}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or home.personalize("Could not ask Colin to sign in.")})
    return json.dumps({"result": reply.get("result")})


CLOSE_SCHEMA = {
    "name": "close_sign_in_card",
    "description": (
        "Close a sign-in card you put in Colin's chat with ask_to_sign_in when you no longer need it: he did it "
        "another way (bought it by phone, signed in somewhere else), changed his mind, or you found what you needed "
        "without it. The card leaves his chat and nothing else happens. Give the site, or leave it out to close "
        "your newest card. Don't close a card you still need."
    ),
    "parameters": {"type": "object", "properties": {
        "site": {"type": "string", "description": "The card's site (\"1800contacts.com\") or its sign-in address."},
    }},
}


def close_sign_in_card(args: dict, **kwargs) -> str:
    reply = mac._call("close_sign_in", args or {}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not close that card."})
    return json.dumps({"result": reply.get("result")})


CLOSE_CARD_SCHEMA = {
    "name": "close_card",
    "description": (
        "Close one of your own okay cards in Colin's chat (an email to send, something to publish or delete) that "
        "his later words made wrong: he named another person (\"no, the other Jordan\"), changed the text, or "
        "doesn't want it any more. Give the request number from the note you got when the card came up. The card "
        "leaves his chat and nothing is sent or done. Close the wrong card yourself; never ask Colin to decline it. "
        "Don't close a card he still wants."
    ),
    "parameters": {"type": "object", "properties": {
        "request": {"type": "integer", "description": "The card's request number."},
    }, "required": ["request"]},
}


def close_card(args: dict, **kwargs) -> str:
    try:
        req = int((args or {}).get("request"))
    except (TypeError, ValueError):
        return json.dumps({"error": "Give the card's request number."})
    reply = mac._call("close_card", {"request": req}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not close that card."})
    return json.dumps({"result": reply.get("result")})
