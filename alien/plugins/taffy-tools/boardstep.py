""











import json
import os
import threading
import urllib.request

from . import gate

TIMEOUT_SEC = 2
TEXT_MAX = 600

QUIET = {"offer_choices", "offer_project", "react", "react_to_message", "todo_list", "app_todo_list", "show_tip",
         "clarify", "team_note", "save_fact", "recall", "close_sign_in_card", "close_card", "explain_card",
         "ask_to_connect", "close_code_card"}

KEYS = ("action", "query", "folder", "conversation", "url", "urls", "code", "steps", "service", "page", "bot",
        "target")


def _short(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value)[:TEXT_MAX]
    return value[:TEXT_MAX] if isinstance(value, str) else value


def _args(args) -> dict:
    a = args if isinstance(args, dict) else {}
    return {k: _short(a[k]) for k in KEYS if a.get(k) not in (None, "")}


def _send(body: dict) -> None:
    try:
        from . import mac
        key = mac.key_file().read_text().strip()
        req = urllib.request.Request(f"{mac.hub()}board/step", method="POST", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json", "x-taffy-internal": key})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            r.read()
    except Exception:
        pass


def before_tool(tool_name: str = "", args=None, **kw):
    ""
    try:
        task = os.environ.get("HERMES_KANBAN_TASK", "")
        if not task or not tool_name or tool_name in QUIET or gate._session_env("HERMES_CRON_SESSION"):
            return None
        body = {"bot_id": gate._bot(), "origin_task": task, "args": {"tool": tool_name, "args": _args(args)}}
        threading.Thread(target=_send, args=(body,), daemon=True, name="board-step").start()
    except Exception:
        pass
    return None
