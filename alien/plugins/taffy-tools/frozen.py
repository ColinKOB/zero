""













import threading
import time
from dataclasses import dataclass, field

from . import mac


ENGINE_REFUSAL = "pre_tool_call plugin callback timed out"
MAC_TIMEOUT = "The Mac did not answer in time"
FROZE = (ENGINE_REFUSAL, MAC_TIMEOUT, "Could not reach the hub: timed out")

ENGINE_HOLD_SEC = 60
HOOK_WAIT_SEC = 25
MAC_REST_SEC = 90

MAC_RESTING = ("Colin's Mac isn't answering, so leave it alone for about a minute and a half. Keep going another way "
               "now: the web, his connected accounts, a teammate, or what you already read. Then tell Colin the answer "
               "you found. If only the Mac can answer, try it once more after that.")

_lock = threading.Lock()


@dataclass
class _Reply:
    steps: set[str] = field(default_factory=set)
    reported: bool = False
    mac_back: float = 0.0
    tools_back: float = 0.0


_replies: dict[tuple[str, str], _Reply] = {}


def _key(kw: dict) -> tuple[str, str]:
    return str(kw.get("session_id") or ""), str(kw.get("turn_id") or "")


def before_tool(tool_name: str = "", args=None, **kw):
    ""
    reply = _replies.get(_key(kw))
    if reply and tool_name.startswith("mac_") and tool_name != "mac_done" and time.monotonic() < reply.mac_back:
        from . import home
        return {"action": "block", "message": home.personalize(MAC_RESTING)}
    return None


def after_tool(tool_name: str = "", args=None, result=None, status=None, error_message=None, **kw):
    said = f"{error_message or ''} {str(result or '')[:300]}"
    if not any(sign in said for sign in FROZE):
        return None
    key = _key(kw)
    if not key[1]:
        return None
    now = time.monotonic()
    with _lock:
        reply = _replies.setdefault(key, _Reply())
        while len(_replies) > 50:
            _replies.pop(next(iter(_replies)))
        reply.steps.add(str(kw.get("api_request_id") or kw.get("tool_call_id") or len(reply.steps)))
        again = len(reply.steps) >= 2
        if again and MAC_TIMEOUT in said:
            reply.mac_back = now + MAC_REST_SEC
        report = again and not reply.reported
        reply.reported = reply.reported or report
        if ENGINE_REFUSAL in said and not reply.tools_back:
            reply.tools_back = now + ENGINE_HOLD_SEC
        wait = min(reply.tools_back - now, HOOK_WAIT_SEC) if ENGINE_REFUSAL in said else 0
    if report:
        mac._call("stuck", {"tool": tool_name, "error": str(error_message or result or "")[:300]}, 10,
                  session_id=key[0], area="chat")
    if wait > 0:
        time.sleep(wait)
    return None
