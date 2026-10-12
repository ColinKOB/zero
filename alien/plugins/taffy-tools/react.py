"""Tapbacks: a bot can react to Colin's latest message the way a friend would in Messages."""

import json

from . import mac

SCHEMA = {
    "name": "react",
    "description": (
        "React to Colin's latest message in this chat with a tapback, the way a friend does in Apple Messages. "
        "Do it when the moment calls for one, roughly one message in four, never on every message: heart when he "
        "thanks you, shares good news or says something kind; like when he confirms, agrees or settles a plan "
        "(\"sounds good\", \"do it\"); laugh when he jokes; emphasize for big news, a finished milestone or a deadline "
        "met; question when what he said is unclear and you are asking him about it; dislike only when he shares "
        "bad news he dislikes too. React in addition to your reply, never instead of it, and at most once per "
        "message. Skip reactions on requests, instructions and serious or sensitive topics."
    ),
    "parameters": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["heart", "like", "dislike", "laugh", "emphasize", "question"]},
    }, "required": ["kind"]},
}


def react(args: dict, **kwargs) -> str:
    reply = mac._call("react", {"kind": (args or {}).get("kind", "")}, session_id=kwargs.get("session_id") or "")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not react."})
    return json.dumps({"result": reply.get("result")})
