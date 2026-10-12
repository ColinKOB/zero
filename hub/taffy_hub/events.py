"""Small event lines in a Zip's chat, like Grok Bot's "Created routine" and "Updated memory for".

Each is a feed item of kind "event" in that Zip's own chat. Only things that really happened are recorded: the
scheduled-job scan (watcher.scan_jobs) sees a routine appear in the Zip's job list, and a Zip's chat turn
(hermes.stream_turn) sees its memory tool finish a write. Never a notification, and never the home preview.
"""

import json

from . import store

KIND = "event"
MEMORY_ACTIONS = ("add", "replace", "remove")


def routine_created(bot_id: str, name: str, when: str = "") -> int:
    return store.add_feed(KIND, bot_id, None, "Added a scheduled job",
                          json.dumps({"event": "routine", "name": name, "when": when}))


def memory_updated(bot_id: str) -> int:
    return store.add_feed(KIND, bot_id, None, "Updated memory", json.dumps({"event": "memory"}))


def writes_memory(args) -> bool:
    """Whether a memory tool call is a write (the tool only has writes, so an unreadable call still counts)."""
    if not isinstance(args, dict):
        return True
    ops = args.get("operations") if args.get("action") == "batch" else [args]
    return any((op or {}).get("action") in MEMORY_ACTIONS for op in (ops or [])) or args.get("action") == "batch"
