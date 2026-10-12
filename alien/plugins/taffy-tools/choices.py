"""offer_choices: Grok Bot's tap-to-answer cards in Colin's chat (see the hub's choices.py)."""

import json

from . import mac

SCHEMA = {
    "name": "offer_choices",
    "description": (
        "Show Colin a short question with two to four options he can tap instead of typing, like Grok Bot's "
        "choice cards. Use it whenever you would end by asking him to pick between a few clear next steps or "
        "answers, for example \"Want me to open the BUS 305 presentation?\" with \"Open it\", \"Plan POL 101 "
        "instead\", \"That's enough for now\". Options are what he would say, in his voice, under 80 characters. "
        "The card shows under your reply, so keep the reply short and do not list the options in it. His tap "
        "comes back as his own message."
    ),
    "parameters": {"type": "object", "properties": {
        "question": {"type": "string", "description": "One short question."},
        "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
    }, "required": ["question", "options"]},
}


def offer_choices(args: dict, **kwargs) -> str:
    reply = mac._call("offer_choices", args or {}, 15, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not show the options."})
    return json.dumps({"result": reply.get("result")})
