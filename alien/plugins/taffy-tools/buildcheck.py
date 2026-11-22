""






import os

from . import consent


def _request(session_id: str, task: str) -> str:
    ""
    row = consent.task_origin(task) if task else None
    row = row or (consent.relayed_pass(session_id) or consent.live_pass(session_id=session_id) if session_id else None)
    return str(row["text"] or "") if row else ""


def stop(args: dict, session_id: str = "") -> dict | None:
    ""
    from . import mac
    task = os.environ.get("HERMES_KANBAN_TASK") or ""
    reply = mac._call("build_check", {"title": args.get("title"), "body": args.get("body"),
                                      "assignee": args.get("assignee"), "parents": args.get("parents"),
                                      "request": _request(session_id, task)},
                      40, session_id=session_id, area="chat")
    result = reply.get("result") if reply.get("ok") else None
    if isinstance(result, dict) and result.get("stop") and result.get("message"):
        return {"action": "block", "message": str(result["message"])}
    return None
