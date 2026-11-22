""

























import re
import time
from urllib.parse import urlparse

STALL_SEC = 300
STALE_SEC = 6 * 3600
MAX_STEPS = 8

_live: dict[str, dict] = {}
_expect: dict[str, str] = {}
_outcome: dict[str, dict] = {}
_began: dict[str, float] = {}




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




QUIET = {"offer_choices", "offer_project", "react", "react_to_message", "todo_list", "app_todo_list", "show_tip", "clarify", "team_note", "save_fact", "recall",
         "close_sign_in_card", "close_card", "explain_card", "ask_to_connect", "close_code_card"}



APP_NAMES = {"gmail": "Gmail", "calendar": "Google Calendar", "drive": "Google Drive", "trello": "Trello",
             "outlook": "Outlook", "dropbox": "Dropbox", "notion": "Notion", "linear": "Linear", "github": "GitHub",
             "slack": "Slack", "todoist": "Todoist", "figma": "Figma", "atlassian": "Jira", "1password": "1Password",
             "posthog": "PostHog", "railway": "Railway", "cloudflare": "Cloudflare", "vercel": "Vercel",
             "stripe": "Stripe"}
APP_TOOLS = {"trello", "drive", "outlook", "dropbox", "notion", "linear", "github", "posthog", "railway", "cloudflare",
             "vercel", "stripe"}

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
    "team_chats": "Looking back through the team's chats",
    "skill_view": "Looking up how to do this",
    "skills_list": "Looking up how to do this",
    "skill_manage": "Writing down how it did this",
    "delegate_task": "Handing part of this to a helper",
    "cronjob_manage": "Setting up a scheduled job",
    "image_generate": "Making a picture",
    "bank": "Checking your bank",
    "bills": "Checking your bills",
    "watch_texts": "Watching for texts",
    "vision_analyze": "Looking at a picture",
    "video_analyze": "Watching a video",
    "ask_to_sign_in": "Asking you to sign in",
    "ask_for_code": "Asking you for a code",
    "enter_texted_code": "Entering a sign-in code",
    "team_add_bot": "Setting up a new Zip",
    "send_message": "Sending a message",
}


def label(tool: str | None, args=None, preview: str | None = None) -> str | None:
    ""

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
    if tool == "browser_download":
        host = _host(a.get("url"))
        return f"Downloading a file from {host}" if host else "Downloading a file"
    if tool in ("browser_exec", "browser_cdp", "browser_steps", "run_steps"):
        host = _host(a.get("code") or a.get("url") or a.get("steps"))
        return f"Opening {host}" if host else PLAIN[tool]
    if tool == "connectors":
        service = str(a.get("service") or "").lower()
        return f"Working in {APP_NAMES[service]}" if service in APP_NAMES else PLAIN[tool]
    if tool in APP_TOOLS:
        return f"Working in {APP_NAMES[tool]}"
    if tool == "email":
        return _mail_step(a)
    if tool == "messages":
        return _texts_step(a)
    if tool == "look_up":
        q = _plain_words(a.get("query"))
        return f"Looking up “{_short(q, 30)}”" if q else "Looking something up"
    if tool == "calendar":
        action = str(a.get("action") or "")
        return ("Updating your calendar" if action in ("create", "update", "delete", "move") else
                "Checking your calendar")
    if tool == "reminders":
        action = str(a.get("action") or "")
        return {"add": "Adding a reminder", "complete": "Checking off a reminder"}.get(action, "Checking your reminders")
    if tool == "contacts":
        return "Adding a contact" if a.get("action") == "add" else "Looking up a contact"
    if tool == "share_page":
        action = str(a.get("action") or "")
        return {"publish": "Updating a shared page" if a.get("page") else "Making a page to share",
                "take_down": "Taking down a shared page"}.get(action, "Checking shared pages")
    if tool == "make_media":
        return "Making a picture"
    if tool == "ask_teammate":
        return f"Asking {_teammate(a.get('bot'))}"
    if tool == "tell_teammate":
        return f"Telling {_teammate(a.get('bot'))}"
    if tool == "mac_open":
        app = _mac_app(a.get("target"))
        return f"Working in {app} on your Mac" if app else MAC_STEP
    if tool.startswith("mac_"):
        return MAC_STEP
    if tool.startswith("kanban_") and tool not in PLAIN:
        return "Checking the board"
    if tool.startswith("browser_") and tool not in PLAIN:
        return "Using its browser"
    return PLAIN.get(tool, "Working on it")


MAC_STEP = "Using your Mac"







_ADDRESS = re.compile(r"[\w.%+-]+@[\w.-]+|\+?\d[\d\s().-]{6,}\d")
_MAIL_WORDS = re.compile(r"^(from|to|cc|bcc|subject|body|before|after|date|flag|and|or|not|in)$", re.I)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _plain_words(text) -> str:
    ""
    t = " ".join(str(text or "").replace("\u201c", '"').replace("\u201d", '"').split()).strip(" \"'")
    return "" if not t or _ADDRESS.search(t) else t


def _mail_search(query) -> str:
    q = " ".join(str(query or "").replace("\u201c", '"').replace("\u201d", '"').split())
    sender = re.search(r'\bfrom\s+(?:"([^"]+)"|(\S+))', q, re.I)
    name = " ".join(((sender.group(1) or sender.group(2)) if sender else "").split())
    if name and _NAME.match(name) and not _MAIL_WORDS.match(name):
        return f"Looking for mail from {name[:1].upper()}{name[1:]}"
    phrase = re.search(r'"([^"]+)"', q)
    words = phrase.group(1) if phrase else " ".join(
        w for w in q.split() if not _MAIL_WORDS.match(w) and not _DAY.match(w) and "@" not in w)
    words = _plain_words(words)
    return f"Searching your mail for “{_short(words, 30)}”" if words else "Searching your mail"


def _mail_step(a: dict) -> str:
    action = str(a.get("action") or "")
    if action in ("draft", "send", "reply", "forward"):
        return "Writing an email"
    if action == "search":
        return _mail_search(a.get("query"))
    if action == "read":
        return "Reading an email"
    if action == "attachments":
        return "Opening an email's attachments"
    if action == "list":
        folder = str(a.get("folder") or "INBOX").upper()
        return "Checking your inbox" if folder == "INBOX" else "Checking your mail"
    return "Checking your email"


def _texts_step(a: dict) -> str:
    action = str(a.get("action") or "")
    if action == "read":
        who_ = " ".join(str(a.get("conversation") or "").split())
        return f"Reading your texts with {who_}" if _NAME.match(who_) else "Reading a text thread"
    if action == "search":
        q = _plain_words(a.get("query"))
        return f"Searching your texts for “{_short(q, 30)}”" if q else "Searching your texts"
    if action == "draft":
        return "Writing a text"
    return "Checking your texts"










_NAME = re.compile(r"^[A-Za-z][A-Za-z .'\-]{0,29}$")


def _quoted(text, n: int = 30) -> str:
    t = _short(text, n)
    return f"“{t}”" if t else ""


def did(tool: str | None, args=None) -> dict | None:
    a = args if isinstance(args, dict) else {}
    action = str(a.get("action") or "")

    def step(lead: str, rest: str, icon: str) -> dict:
        return {"lead": lead, "rest": rest, "icon": icon}

    if tool == "messages":
        if action == "read":
            who_ = " ".join(str(a.get("conversation") or "").split())
            return step("Read your texts", f"with {who_}" if _NAME.match(who_) else "", "texts")
        if action == "search":
            q = _quoted(a.get("query"))
            return step("Searched your texts", f"for {q}" if q else "", "texts")
        if action == "recent":
            return step("Checked your texts", "", "texts")
        return None
    if tool == "fix_school_date":
        course = " ".join(str(a.get("course") or "").split())[:40]
        return step("Corrected a due date on your list", f"for {course}" if course else "", "calendar")
    if tool == "look_up":
        q = _quoted(a.get("query"))
        return step("Looked up", q, "search") if q else None
    if tool == "web_search":
        q = _quoted(a.get("query"))
        return step("Searched the web", f"for {q}" if q else "", "web")
    if tool == "web_extract":
        host = _host(a.get("urls") or a.get("url"))
        return step("Read", host or "a web page", "page")
    if tool in ("browser_navigate", "browser_exec", "browser_cdp", "browser_steps", "run_steps"):
        host = _host(a.get("url") or a.get("code") or a.get("steps"))
        return step("Opened", host, "page") if host else None
    if tool == "read_file":
        name = str(a.get("path") or a.get("file") or "").rstrip("/").rsplit("/", 1)[-1]
        return step("Read", _short(name) if name else "a file", "file")
    if tool == "email":
        if action in ("draft", "send", "reply", "forward"):
            return None
        q = _quoted(_plain_words(a.get("query")))
        return step("Checked your email", f"for {q}" if q else "", "mail")
    if tool == "calendar":
        return None if action in ("create", "update", "delete", "move") else step("Checked your calendar", "", "calendar")
    if tool == "contacts":
        return None if action == "add" else step("Looked up a contact", "", "contact")
    if tool == "session_search":
        return step("Looked back through your chats", "", "chats")
    if tool == "team_chats":
        return step("Looked back through the team's chats", "", "chats")
    if tool == "vision_analyze":
        return step("Looked at a picture", "", "picture")
    if tool == "bank":
        return step("Checked your bank", "", "money")
    if tool == "bills":
        return step("Checked your bills", "", "money")
    if tool == "tally":
        return step("Read your transactions", "", "money")
    if tool == "connectors":
        service = str(a.get("service") or "").lower()
        return step("Checked", APP_NAMES[service], "app") if service in APP_NAMES else None
    if tool in APP_TOOLS:
        return step("Checked", APP_NAMES[tool], "app")
    return None


def _mac_app(target) -> str:
    ""
    t = str(target or "").strip()
    if not t or len(t) > 30 or any(c in t for c in "/:~.@"):
        return ""
    return t[:1].upper() + t[1:]




_MAIL_STEP = re.compile(r"\b(e?mails?|inbox|Gmail)\b")


def _app_of(step: str) -> str:
    if _MAIL_STEP.search(step):
        return "mail"
    if step == MAC_STEP or step.endswith(" on your Mac"):
        return "mac"
    return step


def _add_step(steps: list, step: str) -> None:
    if steps and _app_of(steps[-1]) == _app_of(step):
        if step != MAC_STEP:
            steps[-1] = step
        return
    steps.append(step)
    del steps[:-MAX_STEPS]
















THINK_SHOW_SEC = 8
THINK_MAX_CHARS = 80
_THINK_KEEP = 4000

_TITLE = re.compile(r"^[ \t]*\*\*([^*\n]{2,160})\*\*", re.M)

_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z\"“(]|$)")
_INTERNAL = re.compile(
    r"\b(users?|system|prompt|instructions?|tools?|function|tokens?|reasoning|assistant|model|json|api|schema|gpt|"
    r"claude|openai|anthropic|hermes|codex)\b|\w+_\w+|[`{}<>\[\]=/\\#]|\(\)",
    re.I)


def _clean_thought(text: str) -> str:
    t = " ".join(str(text or "").replace("*", "").split()).strip(" -–—:;,\"'")
    return t[:-1] if t.endswith(".") and not t.endswith("..") else t


def _fit(line: str) -> str:
    ""
    if len(line) <= THINK_MAX_CHARS:
        return line
    clause = re.split(r",\s|;\s|\s[–—-]\s", line, maxsplit=1)[0].strip()
    if len(clause.split()) >= 3 and len(clause) <= THINK_MAX_CHARS:
        return clause
    return line[: THINK_MAX_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def thought_line(summary: str) -> str | None:
    ""
    text = str(summary or "")
    titles = _TITLE.findall(text)
    if titles:
        candidates = titles[::-1]
    else:

        para = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        candidates = []
        for p in para[::-1]:

            whole = [s for s in _SENTENCE_END.split(p.strip() + " ")[:-1] if s.strip()]
            candidates.extend(whole[::-1])
            if candidates:
                break
    for c in candidates:
        line = _clean_thought(c)
        if len(line.split()) >= 2 and not _INTERNAL.search(c.replace("**", "")):
            return _fit(line[:1].upper() + line[1:])
    return None


def _think(cur: dict, delta: str) -> None:
    th = cur.get("thinking")
    if th is None:
        th = cur["thinking"] = {"since": cur.get("quiet_from", cur["since"]), "text": "", "line": None}
    th["text"] = (th["text"] + delta)[-_THINK_KEEP:]
    th["line"] = thought_line(th["text"]) or th["line"]
    if th["line"] and cur["last_at"] - th["since"] >= THINK_SHOW_SEC:
        _say(cur, th["line"], th["since"])


def _shown_thought(cur: dict, now: float) -> dict | None:
    th = cur.get("thinking")
    if not th or not th["line"] or now - th["since"] < THINK_SHOW_SEC:
        return None
    return {"text": th["line"], "since": th["since"]}












WORDING_HOLD_SEC = 4
MAX_SAID = 30
QUIET_SAID = {"Looking up how to do this", "Writing down how it did this", "Checking the board", "Working on it",
              "Handing work to the team", "Wrapping up the task", "Leaving a note on the task",
              "Marking the task as waiting on you"}
_QUERY = re.compile(r"\s*(?:for\s+)?“[^”]*”\s*$")


def plain_step(step: str) -> str:
    ""
    bare = _QUERY.sub("", step).strip()
    if bare == "Looking up":
        bare = "Looking something up"
    return bare.rstrip(".") + "."


def _say(cur: dict, text: str, at: float, held: bool = False) -> dict | None:
    said = cur.setdefault("said", [])
    if said and said[-1]["text"] == text:
        return None
    item = {"text": text, "at": at}
    if held:
        item["held"] = True
    said.append(item)
    del said[:-MAX_SAID]
    return item


def _say_step(bot_id: str, cur: dict, step: str) -> None:
    if step in QUIET_SAID:
        return
    query = _QUERY.search(step)
    words = cur.get("words") or ""
    from . import jobwords
    if not query or cur.get("searches", 0) >= jobwords.MAX_SEARCHES or not jobwords.allowed(bot_id):
        _say(cur, plain_step(step), cur["last_at"])
        return
    item = _say(cur, plain_step(step), cur["last_at"], held=True)
    if item is None:
        return
    cur["searches"] = cur.get("searches", 0) + 1

    async def word() -> None:
        line = await jobwords.search_line(bot_id, words, step)
        if _live.get(bot_id) is cur:
            if line:
                item["text"] = line
            item.pop("held", None)
    jobwords.start(word())


def _want_name(bot_id: str, cur: dict) -> None:
    ""
    from . import jobwords
    if cur.get("named") or cur.get("source") != "app" or not cur.get("words"):
        return
    cur["named"] = True
    if not jobwords.allowed(bot_id):
        return

    async def name() -> None:
        found = await jobwords.name(bot_id, cur["words"])
        if found and _live.get(bot_id) is cur:
            cur["name"] = found
    jobwords.start(name())


def said_now(cur: dict, now: float) -> list[dict]:
    ""
    return [{"text": i["text"], "at": i["at"]} for i in cur.get("said") or []
            if not i.get("held") or now - i["at"] >= WORDING_HOLD_SEC]




def expect(bot_id: str, words: str) -> None:
    ""
    _expect[bot_id] = words


def begin(bot_id: str) -> None:
    now = time.time()
    words = _expect.pop(bot_id, None)
    _live[bot_id] = {"since": now, "last_at": now, "steps": [], "run_id": None,
                     "source": "app" if words is not None else "hub", "words": words or "", "waiting": False,
                     "said": [], "named": False, "searches": 0}
    if words is not None:
        _began[bot_id] = now
        _outcome.pop(bot_id, None)


def note(bot_id: str, ev: dict) -> None:
    ""
    cur = _live.get(bot_id)
    if cur is None:
        return
    cur["last_at"] = time.time()
    if ev.get("type") == "thinking":
        if ev.get("text"):
            _think(cur, ev["text"])
        return
    if ev.get("type") in ("status", "tool_started", "tool_done", "commentary", "delta", "reply", "error"):

        cur.pop("thinking", None)
        cur["quiet_from"] = cur["last_at"]
    if ev.get("type") in ("status", "commentary", "delta", "reply", "error"):

        cur["waiting"] = bool(ev.get("waiting"))
    if ev.get("type") in ("status", "commentary", "checklist") and not ev.get("waiting"):
        _want_name(bot_id, cur)
    if ev.get("type") == "run" and ev.get("id"):
        cur["run_id"] = ev["id"]
    elif ev.get("type") == "checklist":
        cur["checklist"] = {k: ev[k] for k in ("steps", "done", "total", "revision")}
    elif ev.get("type") == "status" and ev.get("text"):
        if not ev.get("waiting"):


            same = cur["steps"] and _app_of(cur["steps"][-1]) == _app_of(ev["text"])
            cur["uses"] = cur.get("uses", 0) + 1 if same else 1
            _say_step(bot_id, cur, ev["text"])
        _add_step(cur["steps"], ev["text"])
    elif ev.get("type") == "tool_done":
        cur["result_at"] = cur["last_at"]


def end(bot_id: str) -> None:
    _live.pop(bot_id, None)




RETRY_STEP = "Trying that again"


def retrying(bot_id: str) -> None:
    cur = _live.get(bot_id)
    if cur is not None and not cur.get("retrying"):
        return
    now = time.time()
    _live[bot_id] = {"since": cur["since"] if cur else now, "last_at": now, "steps": [RETRY_STEP], "run_id": None,
                     "source": "hub", "words": "", "waiting": False, "retrying": True}


def done_retrying(bot_id: str) -> None:
    if (_live.get(bot_id) or {}).get("retrying"):
        _live.pop(bot_id, None)


def retrying_ids() -> list[str]:
    return [b for b, cur in list(_live.items()) if cur.get("retrying")]


def run_id(bot_id: str) -> str | None:
    cur = _live.get(bot_id)
    return cur["run_id"] if cur else None


def waiting(bot_id: str) -> bool:
    ""
    cur = _live.get(bot_id)
    return bool(cur and cur.get("waiting"))


def resumed(bot_id: str, run_id: str | None = None) -> None:
    ""


    cur = _live.get(bot_id)
    if cur is None or (run_id and cur.get("run_id") and cur["run_id"] != run_id):
        return
    cur["waiting"] = False
    cur["last_at"] = time.time()


def live(bot_id: str, now: float | None = None) -> dict | None:
    ""
    cur = _live.get(bot_id)
    if cur is None:
        return None
    now = now or time.time()
    held = bool(cur.get("waiting"))
    out = {"since": cur["since"], "last_at": cur["last_at"], "steps": list(cur["steps"]),
           "stalled": not held and now - cur["last_at"] >= STALL_SEC, "source": cur["source"], "waiting": held}
    if cur["source"] == "app" and cur["words"]:
        out["words"] = cur["words"]


    if cur.get("checklist"):
        out["checklist"] = cur["checklist"]
    if cur.get("uses"):
        out["uses"] = cur["uses"]
    if cur.get("result_at"):
        out["result_at"] = cur["result_at"]
    if thought := _shown_thought(cur, now):
        out["thinking"] = thought
    if cur.get("name"):
        out["name"] = cur["name"]
    if said := said_now(cur, now):
        out["said"] = said
    return out


def all_live(bots, now: float | None = None) -> dict[str, dict]:
    return {b: s for b in bots if (s := live(b, now))}




def finished(bot_id: str, failed: str | None, words: str, stopped: bool = False) -> None:
    ""
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
    ""
    for d in (_live, _expect, _outcome, _began):
        d.clear()
