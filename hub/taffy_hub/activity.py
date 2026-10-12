"""What a Zip is doing right now, in plain words, and how its last turn in the person's chat ended (fluid app, Oct 9).

Users of Grok Bot and dots asked for two things the app could not show before (docs/research/2026-10-09-grokbot-dots-
user-feedback.md): a compact line of what the agent is doing, as a loading indicator and a way to spot it going off
track without a tool log (#4 and Notion's designer in "Avoid"), and honest state instead of an endless typing
indicator or a reply that never arrives (#50 to #53).

- `label(tool, args)` turns a tool call into a few plain words ("Opening elearning.lakeside.edu", "Asking Money"),
  never a tool name. Calls the person would not care about (a tapback, a choice card) have no label.
- `begin`, `note` and `end` follow the turn running in a Zip's chat session (hermes.stream_turn calls them for the
  chat lane only), so turns the hub starts are covered as well as the person's own.
- `live(bot)` is what the app shows while a turn runs: the steps so far, and `stalled` once nothing has happened for
  STALL_SEC.
- `finished(bot, ...)` records a failed reply to the person's own message, so the app can offer Retry even if it was
  closed while the turn ran. The person's next turn, or STALE_SEC, clears it; the hub's own turns do not.

All of this is in memory: after a hub restart nothing is running, and a failed reply from before is not shown again.
"""

import re
import time
from urllib.parse import urlparse

STALL_SEC = 300          # no event from the turn for this long: "This is taking longer than usual"
STALE_SEC = 6 * 3600     # a failed reply stops being news after this
MAX_STEPS = 8

_live: dict[str, dict] = {}       # bot id -> the running chat turn
_expect: dict[str, str] = {}      # bot id -> the person's words for the turn about to start (turns._run)
_outcome: dict[str, dict] = {}    # bot id -> how the person's last turn ended, when it failed
_began: dict[str, float] = {}     # bot id -> when the person's last chat turn began (a closed card is old news after it)


# MARK: Plain words for a step

_URL = re.compile(r"https?://[^\s'\"<>)\]]+")


def _host(text) -> str:
    if isinstance(text, (list, tuple)):
        text = text[0] if text else ""
    m = _URL.search(str(text or ""))
    if not m:
        return ""
    host = (urlparse(m.group(0)).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _short(text: str, n: int = 40) -> str:
    t = " ".join(str(text or "").split()).strip(" \"'")
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _teammate(name_or_id) -> str:
    from . import config
    key = str(name_or_id or "").strip()
    bot = config.BOTS_BY_ID.get(key.lower()) or next(
        (b for b in config.BOTS if b.name.lower() == key.lower()), None)
    return bot.name if bot else "a teammate"


# Tools whose call says nothing the person needs to watch for: the card or the reaction itself shows.
QUIET = {"offer_choices", "react", "react_to_message", "todo_list", "show_tip", "clarify", "team_note", "save_fact", "recall",
         "close_sign_in_card", "close_card"}

PLAIN = {
    "kanban_create": "Handing work to the team",
    "kanban_show": "Checking the board",
    "kanban_list": "Checking the board",
    "kanban_complete": "Wrapping up the task",
    "kanban_comment": "Leaving a note on the task",
    "kanban_block": "Marking the task as waiting on you",
    "connectors": "Using your connected accounts",
    "tally": "Reading your transactions",
    "terminal": "Working on its computer",
    "execute_code": "Working on its computer",
    "process_manage": "Working on its computer",
    "read_terminal": "Working on its computer",
    "read_file": "Reading a file",
    "search_files": "Looking through its files",
    "write_file": "Writing a file",
    "patch": "Editing a file",
    "web_search": "Searching the web",
    "x_search": "Searching X",
    "web_extract": "Reading a web page",
    "browser_exec": "Using its browser",
    "browser_steps": "Clicking through a site",
    "run_steps": "Clicking through a site",
    "browser_cdp": "Using its browser",
    "browser_dialog": "Using its browser",
    "browser_vault_fill": "Signing in",
    "browser_vault_enter_code": "Entering a sign-in code",
    "browser_vault_list": "Checking its saved logins",
    "browser_vault_unlock": "Signing in",
    "browser_vault_save_login": "Saving a login",
    "computer_use": "Using its computer",
    "share_screen": "Sharing its screen with you",
    "memory": "Saving a note to memory",
    "session_search": "Looking back through your chats",
    "skill_view": "Looking up how to do this",
    "skills_list": "Looking up how to do this",
    "skill_manage": "Writing down how it did this",
    "delegate_task": "Handing part of this to a helper",
    "cronjob_manage": "Setting up a scheduled job",
    "image_generate": "Making a picture",
    "vision_analyze": "Looking at a picture",
    "video_analyze": "Watching a video",
    "ask_to_sign_in": "Asking you to sign in",
    "team_add_bot": "Setting up a new Zip",
    "send_message": "Sending a message",
}


def label(tool: str | None, args=None, preview: str | None = None) -> str | None:
    """A few plain words for one step, or None when the step is not worth a line. Never a tool name: an unknown
    tool is "Working on it"."""
    if not tool or tool == "_thinking":
        return None
    if tool in QUIET:
        return None
    a = args if isinstance(args, dict) else {}
    if tool == "web_search" and a.get("query"):
        return f"Searching the web for “{_short(a['query'])}”"
    if tool == "web_extract":
        host = _host(a.get("urls") or a.get("url"))
        return f"Reading {host}" if host else PLAIN[tool]
    if tool in ("browser_exec", "browser_cdp", "browser_steps", "run_steps"):
        host = _host(a.get("code") or a.get("url") or a.get("steps"))
        return f"Opening {host}" if host else PLAIN[tool]
    if tool == "email":
        action = str(a.get("action") or "")
        return ("Writing an email" if action in ("draft", "send", "reply", "forward") else "Checking your email")
    if tool == "calendar":
        action = str(a.get("action") or "")
        return ("Updating your calendar" if action in ("create", "update", "delete", "move") else
                "Checking your calendar")
    if tool == "ask_teammate":
        return f"Asking {_teammate(a.get('bot'))}"
    if tool == "tell_teammate":
        return f"Telling {_teammate(a.get('bot'))}"
    if tool.startswith("mac_"):
        return "Using your Mac"
    if tool.startswith("kanban_") and tool not in PLAIN:
        return "Checking the board"
    if tool.startswith("browser_") and tool not in PLAIN:
        return "Using its browser"
    return PLAIN.get(tool, "Working on it")


# MARK: The running turn

def expect(bot_id: str, words: str) -> None:
    """The next chat turn for this Zip is the person's own message (turns._run), so a failure can be retried."""
    _expect[bot_id] = words


def begin(bot_id: str) -> None:
    now = time.time()
    words = _expect.pop(bot_id, None)
    _live[bot_id] = {"since": now, "last_at": now, "steps": [], "run_id": None,
                     "source": "app" if words is not None else "hub", "words": words or "", "waiting": False}
    if words is not None:   # the person's own message moves on from a failed reply or a closed card
        _began[bot_id] = now
        _outcome.pop(bot_id, None)


def note(bot_id: str, ev: dict) -> None:
    """Every event from the turn counts as a sign of life; a status line becomes a step."""
    cur = _live.get(bot_id)
    if cur is None:
        return
    cur["last_at"] = time.time()
    if ev.get("type") in ("status", "commentary", "delta", "reply", "error"):
        # Hermes holds the run on a card ("waiting": approvals.handle) until anything else happens in it.
        cur["waiting"] = bool(ev.get("waiting"))
    if ev.get("type") == "run" and ev.get("id"):
        cur["run_id"] = ev["id"]
    elif ev.get("type") == "status" and ev.get("text"):
        steps = cur["steps"]
        if not steps or steps[-1] != ev["text"]:
            steps.append(ev["text"])
            del steps[:-MAX_STEPS]


def end(bot_id: str) -> None:
    _live.pop(bot_id, None)


def run_id(bot_id: str) -> str | None:
    cur = _live.get(bot_id)
    return cur["run_id"] if cur else None


def waiting(bot_id: str) -> bool:
    """The running chat turn is held on a card the person has to answer (Hermes waits for its okay), not working."""
    cur = _live.get(bot_id)
    return bool(cur and cur.get("waiting"))


def resumed(bot_id: str, run_id: str | None = None) -> None:
    """The person answered the card the running turn was held on, and Hermes took the answer: the run is working
    again. Hermes says nothing while the allowed command runs, so without this the turn still read as held, and a
    message the person sent next (often "thanks") stopped the very command they had just allowed (turns.send)."""
    cur = _live.get(bot_id)
    if cur is None or (run_id and cur.get("run_id") and cur["run_id"] != run_id):
        return
    cur["waiting"] = False
    cur["last_at"] = time.time()


def live(bot_id: str, now: float | None = None) -> dict | None:
    """What the app shows while a Zip's chat turn runs."""
    cur = _live.get(bot_id)
    if cur is None:
        return None
    now = now or time.time()
    held = bool(cur.get("waiting"))
    out = {"since": cur["since"], "last_at": cur["last_at"], "steps": list(cur["steps"]),
           "stalled": not held and now - cur["last_at"] >= STALL_SEC, "source": cur["source"], "waiting": held}
    if cur["source"] == "app" and cur["words"]:
        out["words"] = cur["words"]
    return out


def all_live(bots, now: float | None = None) -> dict[str, dict]:
    return {b: s for b in bots if (s := live(b, now))}


# MARK: How the person's last turn ended

def finished(bot_id: str, failed: str | None, words: str, stopped: bool = False) -> None:
    """After the person's own turn: a failure is kept so the app can say so with Retry; anything else clears it."""
    if failed and not stopped:
        _outcome[bot_id] = {"state": "failed", "text": failed, "words": words, "at": time.time()}
    else:
        _outcome.pop(bot_id, None)


def outcome(bot_id: str, now: float | None = None) -> dict | None:
    o = _outcome.get(bot_id)
    if o and (now or time.time()) - o["at"] > STALE_SEC:
        _outcome.pop(bot_id, None)
        return None
    return dict(o) if o else None


def clear_outcome(bot_id: str) -> None:
    _outcome.pop(bot_id, None)


def began_at(bot_id: str) -> float:
    return _began.get(bot_id, 0.0)


def reset() -> None:
    """For tests."""
    for d in (_live, _expect, _outcome, _began):
        d.clear()
