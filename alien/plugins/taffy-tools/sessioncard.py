""



import json

from . import mac

_ROW = {"type": "object", "properties": {
    "title": {"type": "string", "description": "The work in two to six words, like \"Jake meeting prep\"."},
    "task": {"type": "string", "description": "The board task doing this work, yours or a teammate's. Its status "
                                              "shows on the line: done, waiting on Colin, or the teammate on it."},
    "approval": {"type": "integer", "description": "The number of a request waiting on Colin. The line opens it."},
    "made": {"type": "string", "description": "The made id share_result returned for this work's output. The "
                                              "line links to it."},
    "owner": {"type": "string", "description": "Only for a line with no task or request: \"you\" when it is "
                                               "Colin's to do, or a teammate's name."},
    "note": {"type": "string", "description": "A short detail under the title, like \"Two drafts to review\". "
                                             "Never a status: the line shows its own."},
}, "required": ["title"]}

SCHEMA = {
    "name": "session_card",
    "description": (
        "Keep one living card at the top of your chat with Colin for a day or week of work: one line per item, each "
        "with its status and a link to what got made. Use it when a session has three or more items going at once. "
        "The card updates itself as tasks finish and requests are answered, with no new message, so don't announce "
        "each change in chat; write to Colin only when something needs him. When he asks how everything is looking, "
        "call with no lines to read the card, then answer in a sentence or two and let the card show the rest. "
        "Point each line at its record: task for board work, approval for a request waiting on him, made for a "
        "finished output. A line with none of these shows as planned. You can't mark a line done yourself. Giving "
        "lines replaces the whole list in the order given; keep every unfinished line unless Colin dropped it. "
        "Pass the revision the card last returned, or 0 to start one. A card whose lines are all done is archived "
        "the next day."
    ),
    "parameters": {"type": "object", "properties": {
        "title": {"type": "string", "description": "Shown at the top, like \"This Week\" or \"Today\"."},
        "rows": {"type": "array", "items": _ROW, "minItems": 1, "maxItems": 20,
                 "description": "Every line, in order. Leave out to read the card."},
        "expected_revision": {"type": "integer", "minimum": 0,
                              "description": "The card's revision from your last call, or 0 for a new card."},
    }},
}


def session_card(args: dict, **kwargs) -> str:
    reply = mac._call("session_card", args or {}, 30, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        out = {"error": reply.get("error") or "Could not update the card."}
        if reply.get("card"):
            out["card"] = reply["card"]
        return json.dumps(out)
    card = reply.get("card")
    return json.dumps({"card": reply.get("text"), "revision": card["revision"] if card else 0})
