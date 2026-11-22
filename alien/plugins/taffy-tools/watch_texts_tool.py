""











import json

from . import home, mac

ACTIONS = ("start", "list", "stop")

SCHEMA = {"name": "watch_texts", "description": (
    "Watch Colin's texts for new ones from people you name, so a reply that comes by text reaches you. start "
    "watches contacts, each a name from his Contacts or a phone number or email. The hub wakes you in this chat "
    "when one of them texts, with one note for a burst of texts. Give why (what you will do with a reply), and "
    "optionally until (a date such as 2026-11-20, or 'in 2 weeks'; a month by default) and once true to stop after "
    "the first reply. If a name fits more than one person, the answer lists them: use the full name or the number. "
    "list shows your watches, and stop ends one by id. Tell Colin in one line when you start or stop one."),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "contacts": {"type": "array", "items": {"type": "string"},
                         "description": "start: names, phone numbers or emails, at most 10."},
            "why": {"type": "string", "description": "start: what you will do when one of them texts."},
            "until": {"type": "string", "description": "start: when the watch ends by itself."},
            "once": {"type": "boolean", "description": "start: true to end the watch after the first reply."},
            "id": {"type": "integer", "description": "stop: the watch's id."}},
        "required": ["action"]}}


def schema() -> dict:
    return home.personalize_schema(SCHEMA)


def classify(args: dict):
    ""
    return None


def watch_texts(args: dict, **kwargs) -> str:
    args = args if isinstance(args, dict) else {}
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        return json.dumps({"error": "action must be start, list or stop."})
    payload = {k: args[k] for k in ("contacts", "why", "until", "once", "id") if k in args}
    reply = mac._call(action, payload, 90, session_id=kwargs.get("session_id") or "", area="textwatch")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "The hub did not take that."}, ensure_ascii=False)
    return json.dumps({"result": reply.get("result")}, ensure_ascii=False)
