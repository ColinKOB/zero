""




import json
import re

from . import home, mac


CODE_FIELD = re.compile(r"code|otp|one[- ]?time|verif|passcode|\bpin\b|token|security|2fa|authenticat|digit", re.I)

ASK_SCHEMA = {
    "name": "ask_for_code",
    "description": (
        "Ask Colin for the one-time code a website just texted him (or emailed him) while you are signing in for "
        "him, when the site asks for it and you have no 2FA key saved for it (browser_vault_enter_code handles saved "
        "keys; use that first). This is how you sign in to a site that takes a phone number or email and a code, with "
        "no saved login needed: type his number or address yourself and press the button first. He gets a card in this chat, \"<Site> sent you a code\", with boxes his phone fills in "
        "from Messages, and a notification. Call it right after the site says it sent the code, so the code is fresh. "
        "Then stop, say nothing about the card (it asks him itself), and wait: a note arrives in this chat "
        "when he has entered it. Then call enter_texted_code. You never see the code and must never ask him to type it "
        "in the chat."
    ),
    "parameters": {"type": "object", "properties": {
        "site": {"type": "string", "description": "The site sending the code, as a person says it: \"Chase\"."},
        "url": {"type": "string", "description": "The address of the page that asks for the code, starting with "
                "https://. The code can be typed only on a page of this website."},
        "why": {"type": "string", "description": "What you are signing in to do, in a few words, written to Colin as "
                "\"you\": \"check your balance\"."},
        "digits": {"type": "integer", "description": "How many digits the code has, only if it is all digits and the "
                   "page says (usually 6). Leave it out for a code with letters."},
    }, "required": ["site", "url"]},
}

ENTER_SCHEMA = {
    "name": "enter_texted_code",
    "description": (
        "Type the code Colin entered on his card into the site's code field. Look at the page first and pass the ref "
        "of the code field. It returns only whether the code was typed; you cannot read it. Then press the page's "
        "Verify or Continue button yourself. If it says no code has been entered, wait for the note that he entered "
        "it."
    ),
    "parameters": {"type": "object", "properties": {
        "ref": {"type": "string", "description": "The ref of the code field from the page snapshot."},
    }, "required": ["ref"]},
}

CLOSE_SCHEMA = {
    "name": "close_code_card",
    "description": (
        "Close the code card in Colin's chat when you no longer need the code: the sign-in worked another way or the "
        "site stopped asking. Nothing else happens."
    ),
    "parameters": {"type": "object", "properties": {
        "site": {"type": "string", "description": "The card's site; leave it out to close the newest."},
    }},
}


def ask_for_code(args: dict, **kwargs) -> str:
    reply = mac._call("ask_for_code", args or {}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or home.personalize("Could not ask Colin for the code.")})
    return json.dumps({"result": reply.get("result")})


def close_code_card(args: dict, **kwargs) -> str:
    reply = mac._call("close_code", args or {}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not close that card."})
    return json.dumps({"result": reply.get("result")})


def enter_texted_code(args: dict, dispatch, site: str = "", label_of=None, mark_filled=None, signed_in=None,
                      secure=None, **kwargs) -> str:
    ""



    ref = str((args or {}).get("ref") or "").strip()
    if not ref:
        return json.dumps({"error": "Pass the ref of the code field from the page."})
    if secure is not None and not secure():
        return json.dumps({"error": "This page is not secure (https), so the code is not typed into it. Stop and say "
                                    "the sign-in page is not secure."})
    if label_of is not None:
        label = label_of(ref)
        if not label or not CODE_FIELD.search(label):
            return json.dumps({"error": "That field isn't labelled as a code field. Take a snapshot of the page and "
                                        "pass the ref of the field that asks for the code."})
    reply = mac._call("take_code", {"site": site}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    code = reply.get("code") if reply.get("ok") else None
    if not code:
        return json.dumps({"error": reply.get("error") or "No code has been entered yet."})
    if mark_filled is not None:
        mark_filled()
    try:
        out = dispatch("browser_type", {"ref": ref, "text": str(code)})
    except Exception as e:
        return json.dumps({"error": f"Could not type into that field: {str(e).replace(str(code), '(code)')[:200]}"})
    del code
    text = str(out)
    if '"error"' in text[:80]:
        return json.dumps({"error": "The browser would not type into that field. Look at the page again and try the "
                                    "code field's ref. The code is used up; call ask_for_code if the page still wants it."})
    if signed_in is not None:
        signed_in()
    return json.dumps({"result": "Typed the code into the field. Press the page's Verify or Continue button now."})
