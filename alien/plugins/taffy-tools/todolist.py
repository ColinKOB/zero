""










import json

from . import mac

SCHEMA = {
    "name": "app_todo_list",
    "description": (
        "The person's to-do list at the top of their app: one line per thing they have to do, in Zero's order, never "
        "reset. This is their list, not your own plan for this turn. Read it with action read. Add a line for one of "
        "their own tasks (school, work projects, errands, an email they owe): a title of two to six words starting with a verb (\"Sign the field trip "
        "form\"), and when it helps, a due date, a short note shown under the title (\"5.5 hours, $165\"), facts "
        "and links shown in its details, and the Work project it belongs to. A new line arrives quietly unless your "
        "message buzzed their phone. Don't also tell them in chat that you added it. Change or finish only your own "
        "lines; finish a line as soon as it is done or no longer needed. "


        "When the person tells you they did one of their own to-dos (\"sent Jordan my hours\", \"paper's in\"), "
        "finish it with its id from read, whether it is a line or a Needs You row such as a course deadline, an "
        "email or text they owed, or a reminder. It moves to Done Today, where they can put it back, and a card under "
        "your reply shows what is left, so reply in one short line. When they ask you to change one of their own lines (a "
        "new time, more added to it), update the line, and the card shows the change, so reply in one short line. An okay, a sign-in or a decision about your "
        "own work goes on a card in Needs You and never on this list. The list is grouped Today, Tomorrow, This Week and Later; "
        "a line follows its due date unless you set when. Zero alone orders the list: action order with the "
        "ids top to bottom and a reason for the first line in under ten words that names the fact. "

        "Every line shows in their Needs You list, and Needs You orders itself by date, so action order changes "
        "nothing. Adding something that is already a Needs You row adds nothing; give its id as about. "
        "A read also lists what their Needs You panel and Work page show, with where each row and its date come from. "
        "When they ask about something on their list, read this first and answer from the row; never ask them where "
        "they saw it."
    ),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["read", "add", "update", "finish", "order"],
                   "description": "read (the default), add, update, finish, or order (Zero only)."},
        "line": {"type": "string", "description": "update: the line's id from read or add. finish: a line's id, or "
                                                 "the id of one of their own Needs You rows from read."},
        "about": {"type": "string", "description": (
            "add, optional: the id of the Needs You row this is about, from read (a course item, an email, a card). "
            "That row is the one place for it, so no second line is added.")},
        "title": {"type": "string", "description": "add, update: two to six words starting with a verb."},
        "note": {"type": "string", "description": "add, update: a few words under the title, like \"First one "
                                                 "at 11:00 AM\". Leave out to show the due date."},
        "due": {"type": "string", "description": "add, update: 2026-11-05, or 2026-11-05T15:30 with a time."},
        "facts": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                  "description": "add, update: up to 6 [label, value] pairs for the details, like "
                                 "[\"Takes\", \"About 15 minutes\"]."},
        "links": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "url": {"type": "string"}}},
            "description": "add, update: up to 6 {label, url} the person may need, shown as buttons in the details."},
        "project": {"type": "string", "description": "add, update: the Work project id, for its Open Project button."},
        "when": {"type": "string", "enum": ["today", "tomorrow", "week", "later"],
                 "description": "add, update: which group the line sits in. Leave out to follow its due date."},
        "owner": {"type": "string", "description": "add, Zero only: the Zip the line belongs to, when not you."},
        "order": {"type": "array", "items": {"type": "string"},
                  "description": "order: line ids top to bottom. Lines left out keep their order below them."},
        "reason": {"type": "string", "description": "order: why the first line is first, under ten words."},
    }},
}


def todo_list(args: dict, **kwargs) -> str:
    from . import sandbox_world
    world = sandbox_world.current()
    made_up = sandbox_world.todo_text(args or {}, world) if world is not None else None
    if made_up is not None:
        return json.dumps({"result": made_up})
    reply = mac._call("todo_list", args or {}, 30, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not reach the list."})
    return json.dumps({"result": reply.get("text")})
