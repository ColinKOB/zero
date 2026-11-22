""




import json
import os
import re
import time

from . import blockask, consent, gate, home, mac

SCHEMA = {
    "name": "ask_to_sign_in",
    "description": (
        "Ask Colin to sign in to a website you need for what he asked, when the site wants a password you don't have "
        "saved, or explicitly rejected the saved credentials, or something only his hands can do. "
        "If you need a service's API rather than its website, call ask_for_api_key instead. "
        "Open the sign-in page yourself first. A site that takes a phone number or email and a code needs no card from "
        "this tool: type his number or address, press the button, then call ask_for_code. "
        "For a saved login and a timeout, reload, missing form or other page failure, call this tool to get "
        "one recovery attempt and then a problem report, without a sign-in card. "
        "I check the shared sign-in from every Zip before asking again. "
        "If browser_vault_list shows a login for the site, sign in with browser_vault_fill "
        "first; being signed out is not a reason to ask. If the site's sign-in sends you to Google, Microsoft or "
        "another single sign-on page, never type there: ask right away with the site's own sign-in page as url "
        "and that page's address as sso_url. Google never lets your browser sign in, so read Google Docs, Sheets and "
        "Drive files with the drive tool, and for a site's Google sign-in I tell you how to use "
        "Colin's Mac. He gets a card in this chat (save the login to your vault, or sign in himself on "
        "your screen) and a notification. Use it in your first steps, before any long work. Then leave that site "
        "alone and finish any part of his request that does not need it. Say nothing about the card: the card asks "
        "him itself. A note arrives in this chat when he's done (or he tells you), and you carry on "
        "then: open the page again and check, and if you still cannot read it, tell him exactly what the page "
        "shows. If he asks you to ask again, call it again: his card moves to the bottom of the "
        "chat. Give the site's sign-in page address and what you need it for, in a few words "
        "(\"publish the Halloween update\"). Colin reads that reason on the card, so write it to him: \"see your last "
        "order\", never \"see Colin's last order\". If he then gets it done another way or you no longer need it, "
        "close the card with close_sign_in_card. In a board task, use kanban_block with \"Sign in to <site>\" "
        "instead, with the site's sign-in page and any single sign-on page's address in the reason."


        " Not for a command-line tool on Colin's Mac that says it isn't logged in: your screen can't log it in. Ask "
        "him with offer_choices (Done, Not Now) to run that tool's login command in Terminal on his Mac, naming the "
        "command (railway login), why, and what you'll do once he has."
        " For a human verification or bot-detection check on the current page, use purpose='challenge' with "
        "that page's URL and site_name, in a chat or a board task. This works even with a saved login. The answer "
        "tells you what to do: carry on in Colin's own browser on his Mac, or wait on the card he got. If the check "
        "shows on his Mac too, call hand_over with kind takeover and place mac, so his card opens the Mac's browser. Never interact with a CAPTCHA yourself. Leave "
        "the current page open, do not navigate or reload. When he finishes, take a fresh browser_snapshot of the "
        "current page before continuing."
        " Set on_mac when the sign-in can only happen on Colin's own Mac: the site wants Sign in with Apple, a "
        "passkey or Touch ID, or it trusts only his own browser (it keeps refusing yours as a new device). Then the "
        "card opens his Mac on his phone at that page, he signs in there (on the Mac, Sign in with Apple takes his Mac "
        "password), and the note when he's done tells you to carry on on his Mac with mac_start and mac_open. Don't "
        "set it for an ordinary password sign-in."
    ),
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string", "description": "The sign-in page, or current challenge page, starting with https://."},
        "why": {"type": "string", "description": "What you need it for, in a few words, written to Colin as "
                "\"you\" (he reads it on the card): \"see your last order and price\"."},
        "sso_url": {"type": "string", "description": "The Google, Microsoft or other single sign-on page the "
                    "site sent you to, when it did."},
        "purpose": {"type": "string", "enum": ["signin", "challenge"], "default": "signin",
                    "description": "signin for a missing login; challenge for a human check on the current page."},
        "site_name": {"type": "string", "description": "With purpose challenge: the site's everyday name, as Colin "
                      "says it (\"Pirate Ship\"). His card reads \"Clear the human check on Pirate Ship\"."},
        "on_mac": {"type": "boolean", "description": "The sign-in must happen in Colin's own browser on his Mac: Sign "
                   "in with Apple, a passkey, or a site that trusts only his browser. url is the page to open there."},
    }, "required": ["url", "why"]},
}





HOW_CODE_SIGN_IN = ("If it takes a phone number or email and then a code, do it yourself: type Colin's mobile number "
                    "or email, press the button, then call ask_for_code. His addresses are in your notes; find his "
                    "number with recall, then with contacts and his name. If you can't find it, or the page wants a "
                    "password or something only he can do, call ask_to_sign_in again.")
_FIRST_LOOK = {None: "No card is up yet. Open {url} with browser_navigate and look at the page first. " + HOW_CODE_SIGN_IN,
               "contact": "No card is up yet, because this page signs in without a password. " + HOW_CODE_SIGN_IN,
               "code": "No card is up yet, because this page wants a code the site sent. Call ask_for_code with the "
                       "site and this page's address. If it can't be that kind of code, call ask_to_sign_in again."}
_first_looks: dict[tuple, float] = {}


def _first_look(key: str, site: str, url: str, tried: bool) -> str | None:
    ""
    step = consent.login_page_step(gate._bot(), key, site)
    if step == "" or (tried and step != "code"):
        return None
    now = time.time()
    for k in [k for k, at in _first_looks.items() if now - at > consent.LOGIN_RECOVERY_SEC]:
        _first_looks.pop(k, None)
    seen = (gate._bot(), key, consent.base(site), step)
    if seen in _first_looks:
        return None
    _first_looks[seen] = now
    return home.personalize(_FIRST_LOOK[step].format(url=url))


def ask_to_sign_in(args: dict, looked: bool = False, **kwargs) -> str:
    ""
    args = args or {}
    purpose = args.get("purpose", "signin")
    if purpose not in ("signin", "challenge"):
        return json.dumps({"error": "Choose purpose signin or challenge."})

    site = consent.site_of(str((args or {}).get("url") or ""))


    key = gate._fill_key(kwargs.get("session_id") or "", os.environ.get("HERMES_KANBAN_TASK", ""))
    tried = consent.login_page_seen(gate._bot(), key, site) if site else False


    sso = consent.sign_in_provider(str(args.get("sso_url") or "")) or consent.base(site) == "google.com"
    on_mac = bool(args.get("on_mac")) and purpose == "signin"
    if on_mac:

        reply = mac._call("ask_sign_in", {"url": str(args.get("url") or ""), "why": str(args.get("why") or ""),
                                          "on_mac": True}, 15, session_id=kwargs.get("session_id") or "", area="chat")
        if not reply.get("ok"):
            return json.dumps({"error": reply.get("error") or home.personalize("Could not ask Colin to sign in.")})
        return json.dumps({"result": reply.get("result")})
    if purpose == "signin" and site and consent.base(site) != "cloudflare.com" and not tried and not sso and consent.base(site) in {
            consent.base(s) for s in gate._saved_sites(gate._bot())}:
        return json.dumps({"error": f"You have a saved login for {site}. Sign in with browser_vault_list and "
                                    "browser_vault_fill instead of " + home.personalize("asking Colin") + ". Ask only "
                                    "if the site refuses it."})
    if purpose == "signin" and site and not sso and not looked and not consent.sign_in_provider(str(args.get("url"))):

        taught = mac._call("taught_step", {"url": str(args.get("url")), "why": str(args.get("why") or ""),
                                           "recovery_key": key}, 15, session_id=kwargs.get("session_id") or "", area="chat")
        if taught.get("step"):
            return json.dumps({"result": taught["step"]})
        first = _first_look(key, site, str(args.get("url")), tried)
        if first:
            return json.dumps({"error": first})
    args = {k: args[k] for k in ("url", "why", "purpose", "sso_url", "site_name") if k in args}
    args["recovery_key"] = key
    reply = mac._call("ask_sign_in", args, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or home.personalize("Could not ask Colin to sign in.")})
    return json.dumps({"result": reply.get("result")})


BOARD_CHECK = ("Not blocked yet: a human check is in the way, and it gets its own route. Call ask_to_sign_in now with "
               "purpose='challenge', the page's address and site_name. It sends you to Colin's own browser on his Mac "
               "or raises his one-tap card, and says how to wait. Never press the check yourself. If it says no card can "
               "help, block with the reason \"" + blockask.CHECK_WAIT + "\". If you have another question for him, ask "
               "it on its own after the check is cleared.")


def board_check(args, session_id, task_id):
    ""



    reason = str(args.get("reason") or "")
    if reason.startswith(blockask.CHECK_WAIT):
        return {"action": "modify", "args": {"kind": "needs_input"}}
    if not reason.strip():
        return None
    if any(consent.denied_recently(gate._bot(), u.rstrip(".,;")) for u in re.findall(r"https://[^\s)]+", reason)):
        return None
    task = str(args.get("task_id") or os.environ.get("HERMES_KANBAN_TASK") or task_id or "")
    rows = gate._kanban("SELECT title FROM tasks WHERE id=?", (task,)) if task else []
    reply = mac._call("block_check", {"reason": reason, "title": rows[0][0] if rows else "", "task_id": task}, 20,
                      session_id=session_id, area="chat")
    found = reply.get("result") if reply.get("ok") is True else None
    if not isinstance(found, dict):
        return None
    if found.get("card_open") is True:
        return {"action": "modify", "args": {"reason": blockask.CHECK_WAIT + "\n" + reason, "kind": "needs_input"}}
    if found.get("check") is True:
        return {"action": "block", "message": home.personalize(BOARD_CHECK)}
    return None


def board_signin(args, session_id, task_id):
    ""
    task = str(args.get("task_id") or os.environ.get("HERMES_KANBAN_TASK") or task_id or "")
    rows = gate._kanban("SELECT title,body FROM tasks WHERE id=?", (task,)) if task else []
    title, body = rows[0] if rows else ("", "")
    reason = str(args.get("reason") or "")
    if not any(str(t or "").lower().startswith("sign in") for t in (title, reason)):
        return None
    urls = re.findall(r"https://[^\s)]+", reason + "\n" + str(body or ""))
    explanations = []
    urls = [u.rstrip(".,;") for u in dict.fromkeys(urls)]
    sso_url = next((u for u in urls if consent.sign_in_provider(u)), "")


    for url in [u for u in urls if u != sso_url] or urls:
        if consent.denied_recently(gate._bot(), url):
            return {"action": "block", "message": home.say(
                "Colin said not now to signing in to {site}. Do the work without it, and say what you could not reach.",
                site=consent.site_of(url))}
        if not sso_url:
            taught = mac._call("taught_step", {"url": url, "board": True, "recovery_key": gate._fill_key(
                session_id, os.environ.get("HERMES_KANBAN_TASK", ""))}, 15, session_id=session_id, area="chat")
            if taught.get("step"):
                return {"action": "block", "message": taught["step"]}
        reply = mac._call("signin_recovery", {"url": url, "sso_url": sso_url,
                          "recovery_key": gate._fill_key(session_id, os.environ.get("HERMES_KANBAN_TASK", ""))},
                          15, session_id=session_id, area="chat")
        if reply.get("ok") and reply.get("challenge"):
            handoff = mac._call("ask_sign_in", {"url": url.rstrip(".,;"), "purpose": "challenge",
                                "why": "finish the check on this page"}, 15, session_id=session_id, area="chat")
            if handoff.get("ok"):
                return {"action": "modify", "args": {"reason": blockask.CHECK_WAIT + "\n" + reason,
                                                      "kind": "needs_input"}}
            return {"action": "block", "message": handoff.get("error") or "I could not open the human check."}
        if not reply.get("ok") or reply.get("instruction"):
            if not reply.get("ok"):
                return {"action": "modify", "args": {
                    "reason": "Page access failed. I could not check the saved sign-in.\n" + reason,
                    "kind": "transient"}}
            return {"action": "block", "message": reply.get("instruction") or reply.get("error") or
                    "I could not check this saved sign-in. Try again in a moment."}
        if reply.get("reason"):
            explanations.append(reply["reason"])
    if explanations:
        return {"action": "modify", "args": {"reason": reason + "\nSign-in help: " + " ".join(explanations)}}
    return None


CLOSE_SCHEMA = {
    "name": "close_sign_in_card",
    "description": (
        "Close a card you put in Colin's chat with ask_to_sign_in or hand_over when you no longer need it: he did it "
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


HAND_OVER_SCHEMA = {
    "name": "hand_over",
    "description": (
        "Stop at a step that is Colin's and hand it to him on a card: a final press he makes himself, such as a "
        "signature, a submit he said he'd do, or the pay press when you have no saved card (kind final; a send or "
        "purchase you make gets his okay card as usual), a file or photo only he has (kind file; first use any photo "
        "or file he already sent you), or a step he does himself on his Mac or your screen (kind takeover). Do every "
        "step you may do first. Name the job: each job keeps its own card and stays on your open list until it is "
        "finished. "
        "Use it instead of mac_done when the task isn't finished: the page stays as it is, and on the Mac your "
        "access pauses with the window left open. The card keeps where you stopped outside this chat. When he's "
        "done, a note with that record arrives (or he tells you): look at the page before anything else, and if he "
        "already did something or the site turned something down, say that first."
    ),
    "parameters": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["final", "file", "takeover"]},
        "job": {"type": "string", "description": "The job this step is part of, in two to four words: \"eBay "
                "return\", \"OpenAI credits\", \"Jury questionnaire\"."},
        "action": {"type": "string", "description": "The one thing Colin does, written to him as \"you\": "
                   "\"upload a photo of the projector with its box\". For a payment, the total the page shows and "
                   "the button: \"enter your card and press Pay ($5.00)\"."},
        "place": {"type": "string", "enum": ["mac", "browser"], "description": "mac for his Mac, browser for yours."},
        "url": {"type": "string", "description": "The page's exact address, starting with https://."},
        "app": {"type": "string", "description": "On the Mac, the app the page is in, such as Safari."},
        "done": {"type": "array", "items": {"type": "string"}, "description": "The steps you finished, a few words "
                 "each. Leave it out to use your checklist."},
        "expect": {"type": "string", "description": "What the page should show once he's done."},
        "files": {"type": "array", "items": {"type": "string"}, "description": "Files you already have for it."},
    }, "required": ["kind", "job", "action", "place", "url"]},
}


def hand_over(args: dict, **kwargs) -> str:
    reply = mac._call("hand_over", args or {}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not put the card up."})
    return json.dumps({"result": reply.get("result")})


CLOSE_CARD_SCHEMA = {
    "name": "close_card",
    "description": (
        "Settle one of your own open question cards when Colin's words in this chat answer or overtake it: give its "
        "request from the open question cards context. Close one of your own questions to Colin that no longer "
        "applies (it was done, it was decided another way, its moment passed): give its request from the open "
        "questions context and why, one plain sentence to Colin. Close one of your own okay cards in Colin's chat (an email to "
        "send, something to publish or delete) that "
        "his later words made wrong: he named another person (\"no, the other Jordan\"), changed the text, or "
        "doesn't want it any more. Give the request number from the note you got when the card came up. The card "
        "leaves his chat and nothing is sent or done. Close the wrong card yourself; never ask Colin to decline it. "
        "Don't close a card he still wants. For a mail draft, give draft_card and why instead. Close your own draft "
        "as soon as you learn it is overtaken, such as when the mail shows the people settled it without him; don't "
        "leave it open or ask him to delete it. If the draft should still go in a new form, revise it with the email "
        "tool instead. Zero only: you may also close any Zip's okay card or draft, a board task's included, once you "
        "have proof it's settled, such as Sent showing the email went. Put that proof in why. Colin isn't told, and "
        "the Zip that made it hears why. Other Zips close only their own cards."
    ),
    "parameters": {"type": "object", "properties": {
        "request": {"type": "integer", "description": "An okay card's request number, or a question card's."},
        "draft_card": {"type": "integer", "description": "A mail draft's number from the current mail drafts context."},
        "replaced_by": {"type": "integer", "description": (
            "With draft_card, when you sent the same message another way: the number of the text or email card you "
            "sent instead, once it has gone out. The draft's copy in Drafts is then removed.")},
        "why": {"type": "string", "description": "Required with request when it is your own question that no longer "
                                                 "applies, and with draft_card. "
                                                 "draft_card: one short sentence to Colin saying why it closed. "
                                                 "Question card: one line the task should know beyond his words. "
                                                 "Your own question that no longer applies: why, said to Colin. "
                                                 "Zero closing another Zip's card: the proof it's settled."},
    }},
}


def close_card(args: dict, **kwargs) -> str:
    args = args or {}
    try:
        call = ({"draft_card": int(args["draft_card"]), "why": str(args.get("why") or ""),
                 **({"replaced_by": int(args["replaced_by"])} if args.get("replaced_by") is not None else {})}
                if args.get("draft_card") is not None else
                {"request": int(args.get("request")), **({"why": str(args["why"])} if args.get("why") else {})})
    except (TypeError, ValueError):
        return json.dumps({"error": "Give the card's request number, or a mail draft's draft_card."})
    reply = mac._call("close_card", call, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not close that card."})
    return json.dumps({"result": reply.get("result")})


EXPLAIN_CARD_SCHEMA = {
    "name": "explain_card",
    "description": (
        "Say on one of your own okay cards why you want to do what it shows, in one short sentence to Colin as "
        "\"you\", tied to what he asked: \"I'm looking for the Edfinancial email in me.com.\" "
        "Call it right after a card comes up, with the request number from the "
        "note you got. The card already shows what the action touches (the recipient, the folder, how many files), "
        "so don't repeat that, and use no technical words such as paths, commands or tool names. "
        "A new delete card stays hidden until this succeeds. For a delete, say what the files are and why you "
        "want to remove them. Use only known facts; do not claim you made them or that originals remain without proof. "
        "If it fails, correct the reason and retry; if the request expired, retry the original action."
    ),
    "parameters": {"type": "object", "properties": {
        "request": {"type": "integer", "description": "The card's request number."},
        "why": {"type": "string", "description": "One short sentence, at most 140 characters, written to Colin."},
    }, "required": ["request", "why"]},
}


def explain_card(args: dict, **kwargs) -> str:
    ""
    args = args or {}
    try:
        req = int(args.get("request"))
    except (TypeError, ValueError):
        return json.dumps({"error": "Give the card's request number."})
    why = " ".join(str(args.get("why") or "").split())
    if not why:
        return json.dumps({"error": "Say why in one short sentence."})
    if len(why) > consent.BECAUSE_MAX:
        return json.dumps({"error": f"That is too long for the card. Say it in under {consent.BECAUSE_MAX} "
                                    "characters."})
    if not consent.explain_request(req, gate._bot(), why):
        row = consent.get_request(req)
        if row and row.get("bot_id") == gate._bot() and row.get("status") == "pending" \
                and row.get("reason_state") == consent.REASON_NEEDED:
            return json.dumps({"error": "The delete card is still hidden. Say what you want to remove and why, "
                                        "in a short sentence, rather than repeating its label. Use only known facts "
                                        "and retry explain_card with this request number."})
        return json.dumps({"error": "That card isn't open, or it isn't yours. If it expired and the action is "
                                    "still needed, retry the original action to get a new request."})
    return json.dumps({"result": "The card shows it."})


WAKE_SCHEMA = {
    "name": "ask_to_wake_mac",
    "description": (
        "Ask Colin whether you may wake his locked Mac, when a screen step on his Mac told you it is locked and to ask. "
        "Write the question yourself, to him as \"you\", in your own words and first person, in one or two short "
        "sentences: what this work is, then ask to wake it. For example: \"This will need your Mac to email eBay "
        "support. Do you mind if I wake it up?\" He gets it as a card in this chat and a notification, with Wake My "
        "Mac and Not Now. Then stop and leave the Mac alone; his answer arrives in this chat. On Wake My Mac, take your "
        "screen step again: the Mac wakes for it, and stays awake for this whole piece of work without asking again. "
        "It locks again by itself when you're done. On Not Now, leave the work waiting and say so in one line. Only "
        "ask after a screen step met the lock; commands and files never need it."
    ),
    "parameters": {"type": "object", "properties": {
        "question": {"type": "string", "description": "Your words to Colin: what the work is, and asking to wake his "
                     "Mac. One or two short sentences."},
    }, "required": ["question"]},
}


def ask_to_wake_mac(args: dict, **kwargs) -> str:
    reply = mac._call("ask_wake_mac", {"question": str((args or {}).get("question") or "")}, 15,
                      session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or home.personalize("Could not ask Colin.")})
    return json.dumps({"result": reply.get("result")})


CONNECT_SCHEMA = {
    "name": "ask_to_connect",
    "description": (
        "Put a card in Colin's chat to connect one of his apps when you need it and it isn't connected: gmail, "
        "google_calendar, google_drive, outlook, dropbox, trello, notion, linear, github, posthog, railway, cloudflare, "
        "vercel or stripe. He connects it with one tap, and a note arrives here when it's done. Call it when a tool "
        "says the app isn't connected, or when he asks about an app you can't see, such as his Gmail. Always say why "
        "you need it: a card without a reason isn't shown. Use it in your first steps, before long "
        "work. Then stop, and say nothing about the card: the card asks him itself."
    ),
    "parameters": {"type": "object", "properties": {
        "app": {"type": "string", "description": "The app's id, such as google_calendar."},
        "why": {"type": "string", "description": "What you need it for, in a few words, written to Colin as "
                "\"you\" (he reads it): \"add your classes\"."},
    }, "required": ["app", "why"]},
}


def ask_to_connect(args: dict, **kwargs) -> str:
    reply = mac._call("ask_app", args or {}, 15, session_id=kwargs.get("session_id") or "", area="accounts")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or home.personalize("Could not ask Colin to connect it.")})
    return json.dumps({"result": reply.get("result")})
