""



















import json
import logging
import os
import sys
import threading

log = logging.getLogger(__name__)


REFUSAL = ("Your rules did not load for this run, so no tool can run and nothing was done. Do not try another tool or "
           "another way. Stop now and say in one sentence that your rules did not load and you did nothing.")

_told: set[tuple[str, str]] = set()
_told_lock = threading.Lock()


def gate_registered() -> bool:
    ""
    plugins = sys.modules.get("hermes_cli.plugins")
    try:
        hooks = plugins.get_plugin_manager()._hooks.get("pre_tool_call") or []
    except Exception:
        return False
    return any(getattr(cb, "__name__", "") == "pre_tool_call"
               and str(getattr(cb, "__module__", "")).rsplit(".", 1)[-1] == "gate" for cb in hooks)


def _tell_hub(tool: str) -> None:
    ""

    task = os.environ.get("HERMES_KANBAN_TASK", "")
    try:
        from . import gate, mac
        bot = gate._bot()
    except Exception:
        log.warning("no gate for this run, and the hub could not be told: the plugin did not import")
        return
    with _told_lock:
        if (bot, task) in _told:
            return
        _told.add((bot, task))
    log.warning("no gate registered for %s: refusing every tool (first: %s)", bot, tool)
    try:
        mac._call("rules_missing", {"tool": str(tool)[:60], "task": task}, 15, area="chat")
    except Exception:
        log.warning("could not tell the hub that %s has no gate", bot, exc_info=True)


def install() -> bool:
    ""

    module = sys.modules.get("tools.registry")
    registry = getattr(module, "registry", None)
    if registry is None:
        return False
    cls = type(registry)
    inner = getattr(cls, "dispatch", None)
    if inner is None:
        return False
    if getattr(inner, "zero_fail_closed", False):
        return True

    def dispatch(self, name, args, *a, **kw):
        if not gate_registered():
            _tell_hub(name)
            return json.dumps({"error": REFUSAL})
        return inner(self, name, args, *a, **kw)

    dispatch.zero_fail_closed = True
    cls.dispatch = dispatch
    return True
