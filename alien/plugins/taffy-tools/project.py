""



import json

from . import mac

_RESOURCE = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["url", "file", "app", "email"]},
    "target": {"type": "string", "description": "An https link, a full file path on the person's Mac (/Users/... or "
                                                "~/...), or an app's full name, such as Pages or Microsoft Word."},
    "label": {"type": "string", "description": "What the person sees, under 60 characters, like \"Rubric\"."},
    "account": {"type": "string", "description": "For email, the account used by the email tool."},
    "id": {"type": "string", "description": "For email, the exact message id from the email tool."},
    "folder": {"type": "string", "description": "Required for email. Copy the folder used to read it, including "
                                                "INBOX when the email tool used its default."},
}, "required": ["kind", "label"]}

SCHEMA = {
    "name": "offer_project",
    "description": (
        "Show the person a project card with an Open Project button. One tap on their Mac opens everything an "
        "assignment or project needs, side by side, and ends on the document they write in. Offer it whenever the "
        "person is about to sit down to an assignment or project and you know its pages: when they say they will work "
        "on it, when you or a teammate just gathered its materials, or when it is due within two days. Resources are "
        "its pages in order: the assignment or rubric page, earlier drafts and feedback, readings, lecture videos. "
        "Links from course sites, Google Drive, Google Docs and YouTube are fine; use Mac file paths only when the "
        "person told you them or you saw them. Leave out `workspace` to end on the app the person writes in, from "
        "the team's saved facts; if nobody knows it yet, the tool says to ask once. Give `workspace` only for a "
        "specific document. If the assignment must be turned in as a file type that app does not save in (a .docx "
        "for someone who writes in Pages), remind them in one line to export it before they submit. Offer it even "
        "when the Mac is off; they open it once the Mac is on. Offering the same title again updates the card. The "
        "card shows under your reply, so keep the reply short and do not list what is in it. "
        "When you have read an email, use {kind: 'email', account: 'personal', id: '6789', folder: 'INBOX', "
        "label: 'Meeting notes'} "
        "with the account, id and folder from the email tool. Omit target for email; the hub resolves its exact link. "
        "Never build a Gmail search for an email you have read. Only when the exact email is unknown, use a simple "
        "search such as loom, without stacked operators or a guessed sender. Other resource kinds need target. "
        "Non-Gmail emails open one inbox row per destination, labeled as an inbox, because there is no exact web link."
    ),
    "parameters": {"type": "object", "properties": {
        "title": {"type": "string", "description": "The assignment, under 80 characters, like \"English paper\"."},
        "due": {"type": "string", "description": "When it is due, like 2026-10-16T23:59, in the person's time zone."},
        "match": {"type": "string", "description": "The assignment's name as the calendar, Trello or the course "
                                                   "site writes it, so a reminder about it can offer this card."},
        "resources": {"type": "array", "items": _RESOURCE, "minItems": 1, "maxItems": 8,
                      "description": "What to open, in order: the assignment or rubric page, earlier drafts, "
                                     "readings, lecture videos, notes."},
        "workspace": {**_RESOURCE, "description": "A specific document the person writes in, opened last, in front. "
                                                  "Leave it out to use the app they write in."},
    }, "required": ["title", "resources"]},
}


def offer_project(args: dict, **kwargs) -> str:

    reply = mac._call("offer_project", args or {}, 600, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not show the project card."})
    return json.dumps({"result": reply.get("result")})
