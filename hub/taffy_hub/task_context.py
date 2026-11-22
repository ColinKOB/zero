""

import logging
import re
from dataclasses import dataclass

from . import board, config, who

log = logging.getLogger("taffy.task_context")
_TASK_VERBS = r"(?:design|build|make|find|check|research|review|prepare|send|update)\b"


def _lower_verb(text: str, verbs: str) -> str:
    return text[:1].lower() + text[1:] if re.match(verbs, text, re.I) else text


def computer_words(text: str, kind: str) -> str:
    if kind != "system":
        return text
    return re.sub(r"\bopenscad\b", "OpenSCAD", text.replace("this computer", "my computer"), flags=re.I)


@dataclass(frozen=True)
class TaskContext:
    id: str
    title: str
    creator: str

    @property
    def purpose(self) -> str:
        return f"For: {self.title}"





def get(task_id: str | None) -> TaskContext | None:
    if not task_id:
        return None
    try:
        task = board.get_task(task_id) or {}
    except Exception:
        log.exception("could not read task context for %s", task_id)
        task = {}
    body = str(task.get("body") or "")
    title = " ".join(str(task.get("title") or body.split("\n", 1)[0] or "the task I was assigned").split())[:200].rstrip(".?!")
    creator_id = str(task.get("created_by") or "")
    creator = config.BOTS_BY_ID.get(creator_id)
    if creator and creator_id == task.get("assignee"):
        name = "I"
    elif creator:
        name = creator.name
    else:
        name = "You" if creator_id.lower() in {"user", "human", "you", who.first().lower()} else ""
    return TaskContext(task_id, title, name)
