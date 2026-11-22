""

import json
import os
import time

from . import mac







SCHEMA = {
    "name": "offer_choices",
    "description": (
        "Show Colin a short question with two to four options he can tap instead of typing, like Grok Bot's "
        "choice cards. Two or more questions in a turn always go in one questions stack, never in numbered text. "
        "Use questions for two to six questions, each with a short title, a one-line question, and two to four "
        "options. Leave the top-level question, options, and steps out for a stack. Colin can always write his "
        "own answer. There is no skip. Answers wait until the whole stack is done, then return together in one "
        "message. Use the same tool during scheduled check-ins, and include the facts needed for the answers "
        "in reasoning, because the scheduled run has a separate conversation. "
        "When working on an assigned task, include what you need and why. Your chat will introduce who handed "
        "you the work and name the task before the card. "
        "Use it whenever you would end by asking him to decide between a few clear answers, for example "
        "\"Which fix should go in the next app update?\" with \"The failure reasons\", \"Both\", \"Neither yet\". "
        "Never use it to ask permission or to confirm a step. Do the work, including the call that sends, spends, "
        "deletes for good, submits or publishes: the app decides what needs his okay and shows him its own one "
        "card, so never ask him about those yourself. Options are what he would say, in his voice, under 80 characters. "
        "Each option is a complete decision you can carry out without asking again, with the when or how that "
        "matters (\"Pause it now and restart it at midnight\", not \"Finish it at midnight\"). "
        "The card shows under your reply, so keep the reply short and do not list the options in it. His answer "
        "comes back as his own message, after \"About your question\" and the question. A tapped option is his "
        "decision: do it, and don't ask him to confirm it. If he wrote his own answer, act on the most likely "
        "reading of it as an answer to that question and say in a few words how you read it. Ask again only when "
        "the readings lead to steps that can't be undone. Set steps to true when the options are separate tasks he could all "
        "choose to do. With three or more tasks, he can choose all, remove tasks, and put them in order. "
        "Never mark alternatives or meta answers such as Both, Neither, or Hold for now as steps. When his "
        "answer is an ordered list, take it one task at a time in that order. After each item, post one short "
        "completion line immediately so he can steer; don't save them for a final summary. If an item leaves a "
        "draft, Send, sign-in, or approval card waiting for him, leave it open and start the next item only after "
        "he answers it or tells you to go on. Never perform an action that still needs his approval. "
        "When you ask him to approve or review something you made (posts, a page, a plan, a draft), pass item with "
        "the file's full path: he sees it as a card with a preview and Open right above the question, so don't "
        "share_result it separately. "
        "When you have a view, say which option you would choose with pick: bring him a decision with your "
        "recommendation, not a bare list. The card marks that option as your pick. Leave pick out when you "
        "truly have no view, and never pick for him on a matter of his own taste or money without a reason "
        "you give in reasoning."
    ),
    "parameters": {"type": "object", "properties": {
        "title": {"type": "string", "description": (
            "A title of 3 to 6 words in title case that names what the card is about, like \"POL 101 Paper Due "
            "Date\" or \"Passport Renewal Plan\". It heads the card once he has answered, so use the topic and "
            "leave out verbs like \"Want\" and question marks.")},
        "question": {"type": "string", "description": (
            "One short question on one line, with nothing before it, like \"Which day works for the call?\". "
            "Put the facts behind it in reasoning, not here.")},
        "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
        "pick": {"type": "string", "description": (
            "Optional. The option you recommend: its exact words, or its number (1 is the first). Say why in "
            "reasoning.")},
        "questions": {"type": "array", "minItems": 2, "maxItems": 6, "items": {
            "type": "object", "properties": {
                "title": {"type": "string", "description": "A short title naming this question's topic."},
                "question": {"type": "string", "maxLength": 200},
                "options": {"type": "array", "minItems": 2, "maxItems": 4,
                            "items": {"type": "string", "maxLength": 80}},
                "pick": {"type": "string", "description": "Optional. The option you recommend, by words or number."},
            }, "required": ["title", "question", "options"],
        }},
        "item": {"type": "string", "description": (
            "The file this asks him to approve or review, a full path in your work folder or the shared folder.")},
        "item_title": {"type": "string", "description": "What the item is, in two to five words, shown on its card."},
        "steps": {"type": "boolean", "description": (
            "Optional, false by default. True only when every option is a separate task that can be done. "
            "Allows Colin to order all or some of three or more tasks before starting.")},
        "reasoning": {"type": "string", "description": (
            "Optional. What led to the question, shown folded under it behind a Reasoning button. Leave it out "
            "when your reply already says it. Write two to four short sentences to Colin, in this order: what you "
            "or a teammate found, as plain past-tense facts (\"School said you haven't finished your final term "
            "paper for POL 101.\"); your guess, marked \"I'd guess\" (\"Last week's form was due Sunday at 11:55 "
            "p.m., so I'd guess this one is too.\"); then what you couldn't check (\"The dates page wouldn't "
            "load.\"). Say \"I\" for yourself and never your own name. Use the words he would use: no site, tool or "
            "check names, and no labels he wouldn't recognize.")},
    }},
}


def scheduled_questions_pre_llm_call(platform: str = "", session_id: str = "", **kwargs):
    ""
    from . import gate, home
    if platform != "cron" and not (session_id or "").startswith("cron:") and not gate._session_env("HERMES_CRON_SESSION"):
        return None
    return {"context": home.personalize(
        "For this scheduled check-in, put two or more questions for Colin in one offer_choices questions stack. "
        "Never put them in a numbered list in the report. Include the relevant findings in reasoning so your "
        "chat can continue from all his answers. The report posts in your chat too, so do not repeat the questions "
        "or options in it. His answers arrive together only after he finishes the stack."
    )}


def offer_choices(args: dict, **kwargs) -> str:
    from . import proposal
    args = dict(args or {})

    args["proposal_asked"] = proposal.asked(kwargs.get("session_id") or "")
    item = str(args.pop("item", "") or "").strip()
    item_title = args.pop("item_title", "")
    key = None
    if item:

        from . import consent, gate, results, safe_files
        try:
            path = safe_files.resolve(item, safe_files.zip_roots(consent.REAL_HOME, gate._bot()), str(consent.REAL_HOME))
        except safe_files.Refused:
            path = None
        key = _item_key(gate._bot(), kwargs.get("session_id") or "", path) if path else None
        shown = _UNANSWERED.get(key) if key else None
        if shown is None or time.time() - shown[0] > RETRY_SEC:
            shown = (time.time(), json.loads(results.share_result({"path": item, "title": item_title}, **kwargs)))
            if shown[1].get("error"):
                return json.dumps({"error": shown[1]["error"]})
        args["about"] = {"path": path, "made": shown[1].get("made")}
    reply = mac._call("offer_choices", args, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        if key:
            _UNANSWERED[key] = shown
        return json.dumps({"error": reply.get("error") or "Could not show the options."})
    if key:
        _UNANSWERED.pop(key, None)
    return json.dumps({"result": reply.get("result")})





RETRY_SEC = 600
_UNANSWERED: dict[tuple, tuple[float, dict]] = {}


def _item_key(bot: str, session_id: str, path: str) -> tuple:
    try:
        st = os.stat(path)
        return (bot, session_id, path, st.st_size, st.st_mtime_ns)
    except OSError:
        return (bot, session_id, path, None, None)
