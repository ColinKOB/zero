""

from . import mac


def _computer(tool_name):
    return tool_name.startswith("computer") or tool_name == "run_steps"


def before_tool(tool_name="", **kwargs):
    if tool_name.startswith("browser_vault") or tool_name == "mac_fill_login":
        reply = mac._call("login_prepare", {}, 45, area="chat")
        if reply.get("ok") is not True:
            return {"action": "block", "message": "I could not check saved sign-in access. Try again in a moment."}
    if _computer(tool_name):
        reply = mac._call("browser_state", {"phase": "before"}, 45, area="chat")
        if reply.get("ok") is not True:
            return {"action": "block", "message": "I could not check sign-in access. Try again in a moment."}


def after_tool(tool_name="", **kwargs):
    if _computer(tool_name):
        mac._call("browser_state", {"phase": "after"}, 45, area="chat")


def install():
    try:
        from tools.browser_tool_session import register_browser_state_hook
    except ImportError:
        return

    def sync(phase, url=""):
        reply = mac._call("browser_state", {"phase": phase, "url": url}, 45, area="chat")
        if reply.get("ok") is not True:
            raise RuntimeError("Sign-in access could not be checked.")
        return True

    register_browser_state_hook(sync)
