""




import json

from . import consent, home, mac

SCHEMA = {"name": "make_media", "description": (
    "Make one picture and put it in the chat. For videos, load the make-video skill, render with Remotion, "
    "then share the MP4 with share_result. If pictures are not set up, say so in one line and stop."), "parameters": {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["image"]},
        "prompt": {"type": "string", "description": "What to make, in one line."}},
    "required": ["action"]}}


def schema() -> dict:
    return home.personalize_schema(SCHEMA)


def make_media(args: dict, **kwargs) -> str:
    args = args if isinstance(args, dict) else {}
    action = str(args.get("action") or "")
    if action != "image":
        return json.dumps({"error": "action must be image. For video, use the make-video skill and share_result."})
    if consent.test_mode():
        return json.dumps({"test_mode": True, "would_make": action, "note": "Test mode: nothing was made."})
    reply = mac._call(action, args, timeout=120, session_id=kwargs.get("session_id") or "", area="media")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "That didn't work just now."})
    result = reply.get("result") or {}
    return json.dumps(result if isinstance(result, dict) else {"result": result}, ensure_ascii=False)
