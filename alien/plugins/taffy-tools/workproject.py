""


import json

from . import mac

SCHEMA = {
    "name": "work_project",
    "description": (
        "Organize Colin's projects in Work: archive one he's done with, restore it, pin it, rename it, "
        "move it to another area (School, Clients, Career, Money, Personal or a new one), or merge two that are the "
        "same thing. Name projects loosely, like \"the Jake meeting\"; a name that matches more than one project "
        "returns the matches, so ask Colin which one. To merge two projects with the same name, give only that name. "
        "Archiving clears the project's mail asks from Needs You; nothing is deleted. Each change returns an undo_id. "
        "Tell Colin what you did in one line, and call again with action undo and that id if he wants it back. "
        "Archive on your own a project that is finished or no longer matters to him, and say so in one line; merge "
        "only when he asks. Projects whose dates are two days past, or quiet for two weeks, archive themselves, and "
        "anything new that needs him brings one back. Use action list to see every project, archived ones included."
    ),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string",
                   "enum": ["archive", "restore", "pin", "unpin", "rename", "move_area", "merge", "undo", "list"]},
        "project": {"type": "string", "description": "The project's name, loosely."},
        "into": {"type": "string", "description": "For merge: the project to keep."},
        "title": {"type": "string", "description": "For rename: the new name."},
        "area": {"type": "string", "description": "For move_area: the area's name."},
        "undo_id": {"type": "string", "description": "For undo: the undo_id an earlier call returned."},
    }, "required": ["action"]},
}


def work_project(args: dict, **kwargs) -> str:
    reply = mac._call("work_project", args or {}, 60, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({k: v for k, v in reply.items() if k in ("error", "matches")} or
                          {"error": "Could not change that project."})
    return json.dumps({k: v for k, v in reply.items() if k != "ok"})
