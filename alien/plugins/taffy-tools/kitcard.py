""






import json

from . import mac

_BLOCK = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["heading", "text", "images", "checklist", "table", "question", "date", "link",
                                        "buttons", "week", "bars", "progress", "timeline"]},
    "text": {"type": "string", "description": "heading: one short line. text: a paragraph or a few."},
    "items": {"type": "array", "description": (
        "images: up to 10 things to swipe through, each {image, title, price, link, note}; image is an https "
        "address of a picture or a full path to one in your work folder. checklist: up to 60 items, each its text "
        "or {text, checked}. week: up to 60 items, each {day, title, kind, time, note, zip, link, task, calendar}: "
        "day is 2026-11-19; kind is due (a deadline), event (on the calendar), task (work with a Zip; zip names which) "
        "or suggested (a time you propose, never a commitment); time is 19:00 or a few words like \"Tonight\" or "
        "\"By 5 PM\"; note is the short gray line; link (https), task (a board task's id) or calendar: true (opens "
        "that day in his calendar) is what a tap opens. timeline: up to 30 items, each {time, title, note, link, "
        "task}."), "items": {}},
    "title": {"type": "string", "description": "checklist, link, question, week, bars, progress or timeline: an "
                                               "optional short label."},
    "summary": {"type": "string", "description": "bars, progress or timeline: one line under the title that says what "
                                                 "it means, like \"$76 of $60. You're $16 over.\""},
    "start": {"type": "string", "description": "week: the first of its seven days, 2026-11-18. Leave it out to start "
                                               "today."},
    "day": {"type": "string", "description": "timeline: the day it covers, 2026-11-21."},
    "bars": {"type": "array", "items": {"type": "object"}, "description": (
        "bars: up to 31, each {label, value}, like {\"label\": \"M\", \"value\": 22}.")},
    "limit": {"type": "number", "description": "bars: an optional limit. It draws as a line, and a bar over it stands "
                                               "out."},
    "unit": {"type": "string", "description": "bars: $ or a word like hours."},
    "steps": {"type": "array", "items": {"type": "object"}, "description": (
        "progress: up to 30, each {title, state, note}; state is done, doing, waiting or next; note is a few words "
        "like \"Needs box sizes\".")},
    "columns": {"type": "array", "items": {"type": "string"}, "description": "table: up to 6 column names."},
    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
             "description": "table: up to 40 rows, one cell per column."},
    "question": {"type": "string", "description": "question: one short line."},
    "options": {"type": "array", "items": {"type": "string"},
                "description": "question: two to four answers he can tap, in his voice."},
    "typed": {"type": "boolean", "description": "question: true for a short typed answer instead of options."},
    "date": {"type": "string", "description": "date: 2026-11-05, or 2026-11-05T15:30 with a time."},
    "label": {"type": "string", "description": "date: what the date is, like \"Pickup\"."},
    "url": {"type": "string", "description": "link: the full web address."},
    "buttons": {"type": "array", "description": (
        "buttons: up to 4, each {label}, {label, link} or {label, submit: true}. A plain button sends its label "
        "to you as his reply, so write it as he would say it."), "items": {"type": "object"}},
}, "required": ["type"]}

SCHEMA = {
    "name": "card",
    "description": (
        "Put a card in Colin's chat that you lay out yourself from blocks: heading, text, images (a carousel he "
        "swipes, each with a picture, title, price, link and note), checklist, table, question, date, link, "
        "buttons, week, bars, progress and timeline. Use a card when its shape helps him act: things to compare or "
        "buy, a list he works through, or several questions at once. Plain text is better for an answer, a short "
        "update or one fact. "
        "When the answer has a shape, give it that shape without being asked, in one or two sentences and a card: "
        "a schedule, a plan across days or what's on his plate is a week block; one day's plan is a timeline; "
        "spending or counts over time are bars; a job with steps is progress. The app draws these and opens them "
        "bigger on a tap. Never make a page, a file or a long list for something one of these can show. "
        "The card shows under your reply, so keep the reply to a line and don't repeat what the card shows. "
        "A question block takes two to four options or a short typed answer. Several question blocks make one "
        "card whose answers come back to you together, as his one reply, when he taps Submit (the card gets a "
        "Submit button if you leave it out). A plain button sends its label to you as his reply. A button never "
        "sends, buys, posts or deletes anything: when his reply asks for that, make the call yourself, and his "
        "approval card comes up as usual. You choose the content and the order; the app picks colors and fonts. "
        "The result gives the card's id. Keep it if you'll change the card later. "
        "Change a card in place instead of posting it again: action update with its id can check or uncheck "
        "checklist items (by their text or number), add items, or give a heading or text block new text. Updates "
        "and closes are quiet: no buzz and no new message, so don't announce them in chat. Use read to see the "
        "card as it stands, including what Colin ticked. Close a card once it's settled; it stays in the chat with "
        "nothing left to tap. His ticks reach you as a note at the start of your next turn. "
        "Examples. His week: he asks what's on his plate, so reply \"GCI shipping comes first, then Chapter 19.\" and "
        "post one week block with every item you found: {type: week, start: 2026-11-18, items: [{day: 2026-11-19, "
        "title: \"Chapter 19\", kind: due, note: \"Old Testament\"}, {day: 2026-11-20, time: 19:00, title: \"Youth "
        "group\", kind: event}, {day: 2026-11-18, time: Tonight, title: \"Start Chapter 19\", kind: suggested}]}. "
        "Money: {type: bars, title: \"Dining This Week\", summary: \"$76 of $60.\", unit: $, limit: 60, bars: [{label: "
        "M, value: 22}, {label: T, value: 54}]}. A job: {type: progress, title: \"GCI Mailing\", steps: [{title: "
        "\"Letters matched\", state: done}, {title: Labels, state: waiting, note: \"Needs box sizes\"}]}. One day: "
        "{type: timeline, day: 2026-11-21, items: [{time: 10:00, title: \"Forum 2 replies\"}, {time: 17:30, title: "
        "Friendsgiving}]}. "
        "Shopping: he asks for a desk lamp under $80, so post a heading and one images block with three "
        "picks (picture, title, price, link, a short note on why), then a buttons block like \"Order the BenQ\" "
        "and \"Show me more\". Groceries: a checklist block with the list; when he says he got the milk, update "
        "with check [\"Milk\"], and close it when he's done. Several questions: a weekend plan is two or three "
        "question blocks on one card (\"Which day?\" with options, \"Who's coming?\" typed), and his answers "
        "come back together. One question with two to four answers is still offer_choices."
    ),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["post", "update", "read", "close"],
                   "description": "post makes a new card (the default). update, read and close need card."},
        "card": {"type": "string", "description": "update, read, close: the id the card tool returned."},
        "check": {"type": "array", "items": {"type": "string"},
                  "description": "update: checklist items to check, by text or number (1 is the first)."},
        "uncheck": {"type": "array", "items": {"type": "string"}, "description": "update: items to uncheck."},
        "add": {"type": "array", "items": {"type": "string"}, "description": "update: new checklist items."},
        "text": {"type": "string", "description": "update: new words for a heading or text block."},
        "block": {"type": "integer", "description": (
            "update: which block, numbered from the top starting at 1, when the card has more than one checklist "
            "or text block.")},
        "why": {"type": "string", "description": "close: a few words on why, kept with the card."},
        "title": {"type": "string", "description": "Three to six words naming the card, like \"Desk Lamps Under "
                                                  "$80\". It heads the card when it's folded."},
        "blocks": {"type": "array", "items": _BLOCK, "minItems": 1, "maxItems": 30,
                   "description": "The card's blocks, top to bottom."},
    }},
}


def card(args: dict, **kwargs) -> str:
    args = dict(args or {})
    if (args.get("action") or "post") == "post" and not args.get("blocks"):
        return json.dumps({"error": "Give the card's blocks, top to bottom."})
    try:
        args["blocks"] = _local_pictures(args.get("blocks"))
    except ValueError as e:
        return json.dumps({"error": str(e)})
    reply = mac._call("card", args, 90, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not put the card up."})
    return json.dumps({"card": reply.get("card"), "result": reply.get("result")})


def _local_pictures(blocks):
    ""

    if not isinstance(blocks, list):
        return blocks
    from . import consent, gate, safe_files
    out = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "images" and isinstance(block.get("items"), list):
            block = {**block, "items": [_resolved(item, safe_files, consent, gate) for item in block["items"]]}
        out.append(block)
    return out


def _resolved(item, safe_files, consent, gate):
    if not isinstance(item, dict):
        return item
    src = str(item.get("image") or "").strip()
    if not src or src.lower().startswith(("https://", "http://")):
        return item
    try:
        real = safe_files.resolve(src, safe_files.zip_roots(consent.REAL_HOME, gate._bot()), str(consent.REAL_HOME))
    except safe_files.Refused as e:
        raise ValueError(f"That picture can't go on a card: {e}. Use a picture in your work folder or an https "
                         "address.") from None
    return {**item, "image": real}
