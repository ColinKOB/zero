""















































from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, store, who

log = logging.getLogger("taffy.codesessions")


MODELS = {
    "sol": ("codex", "gpt-6.1-sol", "reasoningEffort"),
    "astra": ("codex", "gpt-6-astra", "reasoningEffort"),
    "luna": ("codex", "gpt-6-luna", "reasoningEffort"),
    "opus": ("claudeAgent", "claude-opus-5-5", "effort"),
    "sonnet": ("claudeAgent", "claude-sonnet-5-5", "effort"),
    "haiku": ("claudeAgent", "claude-haiku-5-5", "effort"),
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL, DEFAULT_EFFORT = "opus", "high"
FALLBACK_MODEL, FALLBACK_EFFORT = "sol", "high"
RETRY_CLAUDE_SEC = 86400
BRIEF_MAX, REPORT_MAX, REPLY_MAX = 12000, 8000, 6000
MAX_RUNNING = 2
ANSWER_SEC = 3 * 86400
POLL_SEC, IDLE_SEC = 30, 120
ACTIVE = {"preparing", "queued", "starting", "running"}
FAILED = {"interrupted", "failed", "cancelled", "rolled_back"}
RULES = (
    "You are coding for {zip}, one of {first}'s assistants in Zero, on {first}'s home server. {zip} reads your "
    "final message and tells {first} what happened, so end every turn with a short report: what you changed, how you "
    "checked it (the commands and what they printed), and anything left or uncertain. If you need a decision, ask it "
    "as one plain question at the end and stop.\n"
    "Ask in your message, not with a tool that waits for an answer.\n"
    "Work only in this worktree. Commit your work to its branch with a clear message. Don't push, open pull requests, "
    "deploy, publish, email or post anything, and don't touch other folders: {zip} does those with {first}'s okay.\n\n"
    "The task:\n")

_settings_lock = threading.Lock()
_starting: set[str] = set()


@store.schema
def _tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS code_sessions (
        id TEXT PRIMARY KEY,              -- T3's thread id
        bot_id TEXT NOT NULL,
        folder TEXT NOT NULL,
        branch TEXT NOT NULL,
        model TEXT NOT NULL,
        effort TEXT NOT NULL,
        title TEXT NOT NULL,
        state TEXT NOT NULL,              -- running, reported, ended
        turns INTEGER NOT NULL DEFAULT 1,
        started REAL NOT NULL,
        turn_started REAL NOT NULL,
        report TEXT NOT NULL DEFAULT '',
        why_ended TEXT NOT NULL DEFAULT '',
        told INTEGER NOT NULL DEFAULT 1,
        link TEXT NOT NULL DEFAULT ''
    )""")
    columns = {r[1] for r in c.execute("PRAGMA table_info(code_sessions)")}
    if "question" not in columns:
        c.execute("ALTER TABLE code_sessions ADD COLUMN question TEXT NOT NULL DEFAULT ''")
    if "origin_task" not in columns:
        c.execute("ALTER TABLE code_sessions ADD COLUMN origin_task TEXT NOT NULL DEFAULT ''")
    if "message" not in columns:
        c.execute("ALTER TABLE code_sessions ADD COLUMN message TEXT NOT NULL DEFAULT ''")
    if "moved_to" not in columns:
        c.execute("ALTER TABLE code_sessions ADD COLUMN moved_to TEXT NOT NULL DEFAULT ''")
    if "refused_at" not in columns:
        c.execute("ALTER TABLE code_sessions ADD COLUMN refused_at REAL NOT NULL DEFAULT 0")
    if "instead_of" not in columns:
        c.execute("ALTER TABLE code_sessions ADD COLUMN instead_of TEXT NOT NULL DEFAULT ''")
    if "approved" not in columns:
        c.execute("ALTER TABLE code_sessions ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")


class SessionError(Exception):
    pass




def _t3_bin() -> str:
    found = sorted(glob.glob(str(Path.home() / ".t3/runtime/versions/*/t3")), key=os.path.getmtime)
    if not found:
        raise SessionError("T3 Code isn't installed on this server.")
    return found[-1]


def _t3(*args: str, stdin: str | None = None, timeout: float = 120, as_json: bool = True) -> dict:
    p = subprocess.run([_t3_bin(), *args, *(["--json"] if as_json else [])], input=stdin, capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        raise SessionError((p.stderr or p.stdout).strip()[-400:] or "T3 Code refused that.")
    out = p.stdout.strip().splitlines()
    return json.loads(out[-1]) if out and as_json else {}


def _settings_path() -> Path:
    return Path.home() / ".t3/userdata/settings.json"


def _selection(model: str, effort: str) -> dict:
    instance, slug, option = MODELS[model]
    return {"instanceId": instance, "model": slug, "options": [{"id": option, "value": effort}]}


def _write_default(selection: dict) -> None:
    path = _settings_path()
    try:
        settings = json.loads(path.read_text())
    except (OSError, ValueError):
        settings = {}
    settings["defaultModelSelection"] = selection
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(path)


def _register(folder: str) -> None:
    ""

    try:
        _t3("project", "add", folder, timeout=60, as_json=False)
    except SessionError:
        pass


def _launch(folder: str, message: str, title: str, branch: str, model: str, effort: str) -> dict:
    ""
    _register(folder)
    with _settings_lock:
        _write_default(_selection(model, effort))
        try:
            return _t3("thread", "start", folder, "-", "--title", title, "--worktree", "HEAD", "--branch", branch,
                       stdin=message, timeout=180)
        finally:
            _write_default(_selection(DEFAULT_MODEL, DEFAULT_EFFORT))


def _show(thread_id: str) -> dict:
    return _t3("thread", "show", thread_id, "--messages", "1", timeout=60)


def _mcp(tool: str, arguments: dict) -> dict:
    ""
    import urllib.request
    token = (Path.home() / "taffy/hub-state/t3-token").read_text().strip()
    url = "http://127.0.0.1:3773/mcp"
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json",
               "accept": "application/json, text/event-stream"}

    def post(body: dict) -> dict:
        with urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), headers), timeout=30) as r:
            if r.headers.get("mcp-session-id"):
                headers["mcp-session-id"] = r.headers["mcp-session-id"]
            raw = r.read().decode()
        if raw.lstrip().startswith(("event:", "data:")):
            raw = "\n".join(line[5:] for line in raw.splitlines() if line.startswith("data:"))
        return json.loads(raw) if raw.strip() else {}
    post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "zero-hub", "version": "1"}}})
    post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    out = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
    result = out.get("result") or {}
    if result.get("isError"):
        raise SessionError("T3 Code refused that.")
    return result.get("structuredContent") or {}


def _question(thread_id: str) -> dict | None:
    ""
    ids = _mcp("t3_pending_request", {"action": "list", "threadId": thread_id}).get("requestIds") or []
    if not ids:
        return None
    read = _mcp("t3_pending_request", {"action": "read", "threadId": thread_id, "requestId": ids[0]})
    return {"id": ids[0], "questions": read.get("questions") or []}


def _relay_folder() -> str:
    folder = Path(config.HUB_DB).parent / "t3-relay"
    if not (folder / ".git").exists():
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(folder)], check=True)
        subprocess.run(["git", "-C", str(folder), "-c", "user.name=Zero", "-c", "user.email=zero@localhost", "commit",
                        "-q", "--allow-empty", "-m", "T3 answer relay"], check=True)
    _register(str(folder))
    return str(folder)


def _answer(thread_id: str, request_id: str, answers: dict) -> None:
    message = (f"Call the t3_pending_request tool once with action respond, threadId {thread_id}, requestId "
               f"{request_id} and answers {json.dumps(answers)}. Then reply with only done, or with the error if it "
               "failed. Do nothing else.")
    folder = _relay_folder()
    with _settings_lock:
        _write_default(_selection("luna", "low"))
        try:
            out = _t3("thread", "start", folder, "-", "--title", "Answer relay", "--wait", stdin=message, timeout=240)
        finally:
            _write_default(_selection(DEFAULT_MODEL, DEFAULT_EFFORT))
    if str(out.get("reply") or "").strip().lower().rstrip(".") != "done":
        raise SessionError(f"The answer didn't go through: {str(out.get('reply') or out.get('error') or '')[:200]}")


def _answers(question: dict, text: str) -> dict:
    ""
    out = {}
    for q in question["questions"]:
        labels = [str(o.get("label") or "") for o in q.get("options") or []]
        out[str(q.get("id"))] = next((l for l in labels if l and l.lower() == text.strip().lower()), text.strip())
    return out


def _asked(question: dict) -> str:
    lines = []
    for q in question["questions"]:
        options = ", ".join(str(o.get("label")) for o in q.get("options") or [] if o.get("label"))
        lines.append(f"It asks: {q.get('question')}" + (f" (options: {options})" if options else ""))
    return "\n".join(lines)




def _db():
    return store.db()


def _get(session: str, bot_id: str) -> dict | None:
    ""
    for _ in range(3):
        row = _db().execute("SELECT * FROM code_sessions WHERE id=? AND bot_id=?", (session, bot_id)).fetchone()
        if row is None or not row["moved_to"]:
            break
        session = row["moved_to"]
    return dict(row) if row else None


def _set(session: str, **cols) -> None:
    with store._lock:
        _db().execute(f"UPDATE code_sessions SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?",
                      (*cols.values(), session))
        _db().commit()


def running() -> list[dict]:
    return [dict(r) for r in _db().execute("SELECT * FROM code_sessions WHERE state='running'").fetchall()]


def owed_tasks() -> set[str]:
    ""

    return {r["origin_task"] for r in _db().execute(
        "SELECT origin_task FROM code_sessions WHERE origin_task != '' AND (state='running' OR told=0)")}




def _folder(bot_id: str, folder: str) -> str:
    ""

    work = Path(config.WORK_DIR).expanduser().resolve()
    path = Path(folder.replace("~", str(Path.home()), 1) if folder.startswith("~") else folder).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise SessionError(f"There is no folder at {folder}.") from None
    from . import teams
    roots = [work / bot_id, *(Path(f).expanduser().resolve() for f in teams.extra_folders(bot_id))]
    if not any(path == r or r in path.parents for r in roots):
        raise SessionError(f"Start a session in a repo inside {work / bot_id}.")
    if subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", "HEAD"], capture_output=True).returncode != 0:
        raise SessionError(f"{folder} isn't a git repo with a commit yet. Run git init and commit first.")
    return str(path)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "task"


def _claude_refused(now: float) -> bool:
    ""
    last = _db().execute("SELECT MAX(refused_at) FROM code_sessions").fetchone()[0]
    return bool(last) and now - last < RETRY_CLAUDE_SEC


def _ran_instead(asked: str) -> str:
    ""
    return f"{asked.capitalize()} can't start on this server right now, so this session runs on Sol at high."


def _open_address(link: str) -> str:
    ""

    from urllib.parse import urlsplit
    parts = urlsplit(link)
    if parts.hostname not in ("127.0.0.1", "localhost") or not parts.port:
        return link
    try:
        served = json.loads(subprocess.run(["tailscale", "serve", "status", "--json"], capture_output=True, text=True,
                                           timeout=10).stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    for host, site in (served.get("Web") or {}).items():
        for handler in (site.get("Handlers") or {}).values():
            if urlsplit(str(handler.get("Proxy") or "")).port == parts.port:
                return f"https://{host.removesuffix(':443')}{parts.path}"
    return ""


def _open_line(title: str, thread: str) -> str:
    ""
    try:
        address = _open_address(str((_show(thread).get("thread") or {}).get("link") or ""))
    except (SessionError, OSError, ValueError, subprocess.SubprocessError):
        address = ""
    return f"[{title}]({address})" if address else title


def start(bot_id: str, bot_name: str, args: dict, origin_task: str | None = None) -> dict:
    brief = str(args.get("brief") or "").strip()
    if not brief or len(brief) > BRIEF_MAX:
        raise SessionError(f"Give a brief of up to {BRIEF_MAX:,} characters.")
    model = str(args.get("model") or DEFAULT_MODEL).lower()
    effort = str(args.get("effort") or DEFAULT_EFFORT).lower()
    if model not in MODELS:
        raise SessionError(f"Choose a model: {', '.join(MODELS)}.")
    if effort not in EFFORTS:
        raise SessionError(f"Choose a reasoning level: {', '.join(EFFORTS)}.")
    instead_of = ""
    if MODELS[model][0] == "claudeAgent" and _claude_refused(time.time()):
        instead_of, model, effort = model, FALLBACK_MODEL, FALLBACK_EFFORT
    folder = _folder(bot_id, str(args.get("folder") or ""))
    if len(running()) + len(_starting) >= MAX_RUNNING:
        raise SessionError(f"{MAX_RUNNING} coding sessions are already running on this server. Wait for a report.")
    title = " ".join(str(args.get("title") or brief.splitlines()[0]).split())[:80]
    branch = f"zip/{bot_id}/{_slug(title)}-{secrets.token_hex(2)}"
    key = secrets.token_hex(4)
    _starting.add(key)
    message = RULES.format(zip=bot_name, first=who.first()) + brief
    try:
        launched = _launch(folder, message, f"{bot_name}: {title}", branch, model, effort)
    finally:
        _starting.discard(key)
    thread = str(launched.get("threadId") or "")
    if not thread:
        raise SessionError("T3 Code didn't start the session.")
    now = time.time()
    with store._lock:
        _db().execute("INSERT INTO code_sessions (id, bot_id, folder, branch, model, effort, title, state, started, "
                      "turn_started, origin_task, message, instead_of, approved) "
                      "VALUES (?,?,?,?,?,?,?,'running',?,?,?,?,?,?)",
                      (thread, bot_id, folder, branch, model, effort, title, now, now, origin_task or "", message,
                       instead_of, int(bool(args.get("approved_build")))))
        _db().commit()
    wait = (f"Never block this task or ask {who.first()} while it runs: park the task with kanban_schedule, and the "
            "report brings the task back. "
            if origin_task else "")
    out = {"session": thread, "state": "running", "branch": branch, "model": f"{model} at {effort}",
           "next": f"It is working in a new worktree on that branch. {wait}A note wakes you when it finishes or asks "
                   "you something. Don't wait for it here."}
    if instead_of:
        out["model_note"] = _ran_instead(instead_of)
    if args.get("approved_build"):
        out["show_in_chat"] = _open_line(title, thread)
        out["next"] += f" Tell {who.first()} in one line that you started it, using show_in_chat as written."
    return out


def read(row: dict) -> dict:
    out = {"session": row["id"], "state": row["state"], "title": row["title"], "branch": row["branch"],
           "folder": row["folder"], "model": f"{row['model']} at {row['effort']}", "turns": row["turns"]}
    if row.get("instead_of"):
        out["model_note"] = _ran_instead(row["instead_of"])
    if row["state"] == "running":
        out["next"] = "It's still working. A note wakes you when it finishes or asks you something."
        return out
    out["report"] = row["report"] or row["why_ended"]
    if row["state"] == "reported" and row.get("question"):
        out["next"] = (f"It is waiting on that question. Answer it with reply (an option's label, or your words). If only "
                       f"{who.first()} can decide, put it to {who.first()} with offer_choices using its options and pass the pick.")
    elif row["state"] == "reported":
        out["next"] = (f"Answer with reply to go on, or stop it. Its commits are on {row['branch']} in "
                       f"{row['folder']}: check them with git there before you tell {who.first()} it works.")
    return out


def reply(row: dict, text: str) -> dict:
    text = text.strip()
    if row["state"] != "reported":
        raise SessionError("It's still working." if row["state"] == "running" else "That session has ended.")
    if not text or len(text) > REPLY_MAX:
        raise SessionError(f"Give a reply of up to {REPLY_MAX:,} characters.")
    question = json.loads(row.get("question") or "null")
    if question:
        _answer(row["id"], question["id"], _answers(question, text))
    else:
        _t3("thread", "send", row["id"], "-", stdin=text, timeout=60)
    _set(row["id"], state="running", turns=row["turns"] + 1, turn_started=time.time(), report="", told=1,
         question="")
    return {"session": row["id"], "state": "running", "next": "It has your reply. A note wakes you when it's done."}


def stop(row: dict) -> dict:
    ""

    _set(row["id"], state="ended", why_ended="You stopped it.", told=1)
    return {"session": row["id"], "state": "ended"}


def call(bot_id: str, bot_name: str, args: dict, origin_task: str | None = None) -> dict:
    session = str(args.get("session") or "").strip()
    if not session:
        return start(bot_id, bot_name, args, origin_task)
    row = _get(session, bot_id)
    if row is None:
        raise SessionError("You have no coding session with that id.")
    if args.get("stop"):
        return stop(row)
    if args.get("reply"):
        return reply(row, str(args["reply"]))
    return read(row)




def advance(row: dict, shown: dict, now: float) -> None:
    thread = shown.get("thread") or {}
    status = str(thread.get("status") or "")
    if status in ACTIVE and not thread.get("pendingRequestCount"):
        return
    words = next((m.get("text") or "" for m in reversed(shown.get("messages") or []) if m.get("role") == "assistant"), "")
    link = str(thread.get("link") or row.get("link") or "")
    if status in FAILED:
        reason = _failure_reason(row, thread, words)
        if _move_to_sol(row, reason):
            return
        _set(row["id"], state="ended", why_ended=_why_failed(row, status, reason)[:REPORT_MAX], told=0, link=link)
        return
    question = _question(row["id"]) if thread.get("pendingRequestCount") else None
    report = "\n\n".join(p for p in (words, _asked(question) if question else "") if p) or "It finished without a message."
    _set(row["id"], state="reported", report=report[:REPORT_MAX], told=0, link=link,
         question=json.dumps(question) if question else "")





_MISSING = re.compile(r"spawn (\S+) ENOENT", re.I)
_REFUSED = re.compile(r"disabled Claude subscription access|not logged in|please (?:run )?\S*\s?login|unauthori[sz]ed|"
                      r"authentication (?:failed|required)|invalid api key", re.I)

_LOGGED = re.compile(r"spawn \S+ ENOENT|disabled Claude subscription access", re.I)
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
LOG_TAIL = 256 * 1024


def _log_dir() -> Path:
    return Path.home() / ".t3/userdata/logs"


def _t3_error(row: dict, thread: dict) -> str:
    ""

    after = max(-1, int(thread.get("itemCount") or 0) - 51)
    read = _mcp("t3_thread_read", {"threadId": row["id"], "view": "activity", "limit": 50, "runLimit": 1,
                                   **({"afterPosition": after} if after >= 0 else {})})
    run = thread.get("latestRunId")
    errors = [i for i in read.get("items") or [] if i.get("type") == "error" and (not run or i.get("runId") == run)]
    return " ".join(str(errors[-1].get("text") or "").split()) if errors else ""


def _logged_error(since: float) -> str:
    ""

    found: tuple[float, str] | None = None
    for path in sorted(_log_dir().glob("*.log")):
        try:
            if path.stat().st_mtime < since:
                continue
            with path.open("rb") as f:
                f.seek(max(0, path.stat().st_size - LOG_TAIL))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not _LOGGED.search(line):
                continue
            stamp = _STAMP.search(line)
            try:
                at = datetime.fromisoformat(stamp.group(0).replace("Z", "+00:00")).timestamp() if stamp else since
            except ValueError:
                at = since
            if at >= since - 5 and (found is None or at >= found[0]):
                found = (at, " ".join(line.split())[-400:])
    return found[1] if found else ""


def _failure_reason(row: dict, thread: dict, words: str) -> str:
    ""
    reason = ""
    try:
        reason = _t3_error(row, thread)
    except Exception as e:
        log.info("could not read why coding session %s failed from T3 (%s)", row["id"], type(e).__name__)
    reason = reason or words.strip()
    if not reason or not (_MISSING.search(reason) or _REFUSED.search(reason)):
        reason = _logged_error(float(row.get("turn_started") or 0)) or reason
    return reason


def _move_to_sol(row: dict, reason: str) -> bool:
    ""


    if MODELS.get(row["model"], ("",))[0] != "claudeAgent" or row["turns"] != 1 or not row.get("message") \
            or not _REFUSED.search(reason):
        return False
    _cannot_start(row, reason)
    now = time.time()
    _set(row["id"], refused_at=now)
    bot = config.BOTS_BY_ID.get(row["bot_id"])
    branch = f"zip/{row['bot_id']}/{_slug(row['title'])}-{secrets.token_hex(2)}"
    try:
        launched = _launch(row["folder"], row["message"], f"{bot.name if bot else row['bot_id']}: {row['title']}",
                           branch, FALLBACK_MODEL, FALLBACK_EFFORT)
    except (SessionError, OSError, ValueError, subprocess.SubprocessError):
        log.warning("coding session %s could not start again on Sol", row["id"], exc_info=True)
        return False
    thread = str(launched.get("threadId") or "")
    if not thread:
        return False
    with store._lock:
        c = _db()
        c.execute("INSERT INTO code_sessions (id, bot_id, folder, branch, model, effort, title, state, started, "
                  "turn_started, origin_task, message, instead_of, approved) "
                  "VALUES (?,?,?,?,?,?,?,'running',?,?,?,?,?,?)",
                  (thread, row["bot_id"], row["folder"], branch, FALLBACK_MODEL, FALLBACK_EFFORT, row["title"], now, now,
                   row.get("origin_task") or "", row["message"], row["model"], row.get("approved") or 0))
        c.execute("UPDATE code_sessions SET state='ended', why_ended=?, told=1, moved_to=? WHERE id=?",
                  (_ran_instead(row["model"]), thread, row["id"]))
        c.commit()
    log.warning("coding session %s for %s moved to Sol at high: the Claude sign-in was refused", row["id"], row["bot_id"])
    return True


def _why_failed(row: dict, status: str, reason: str) -> str:
    ""

    log.warning("coding session %s for %s %s: %s", row["id"], row["bot_id"], status, reason[:400] or "no reason given")
    head = f"The session {status.replace('_', ' ')}: {reason}" if reason else \
        f"The session {status.replace('_', ' ')}, and T3 Code didn't say why."
    stuck = _cannot_start(row, reason)
    return f"{head}\n\n{stuck}" if stuck else head


def _cannot_start(row: dict, reason: str) -> str:
    ""


    missing, refused = _MISSING.search(reason), _REFUSED.search(reason)
    if not (missing or refused):
        return ""
    instance = MODELS.get(row["model"], ("",))[0]
    same = [m for m, v in MODELS.items() if v[0] == instance] or [row["model"]]
    other = [m for m in MODELS if m not in same]
    what = (f"T3 Code can't find the {missing.group(1)} program on this server" if missing
            else f"the {', '.join(same)} models' sign-in was refused")
    try:
        from . import problems
        problems.report(row["bot_id"], {
            "kind": "break", "concerns_zip": "self", "source": "code", "where": f"coding sessions on {instance or 'T3'}",
            "what_happened": f"Coding sessions can't start: {what}.",
            "expected": "A coding session starts and works on its brief.", "evidence": reason[:600]})
    except Exception:
        log.warning("could not file the problem report for coding session %s", row["id"], exc_info=True)
    return (f"This is not something you can fix or retry: {what}, so every session on {', '.join(same)} fails the same "
            f"way until it is repaired. It has been flagged for a fix. Don't start another session on those models. "
            + (f"A session on {', '.join(other)} may still work. " if other else "")
            + f"Otherwise make the change with your own tools, or tell {who.first()} in one line that coding sessions "
              "are down. Either way this is no reason to block a task or ask anyone.")


def _wake_note(row: dict) -> str:
    head = f"{who.hub_note()} Your coding session \"{row['title']}\" "
    tool = f"Call code_session with session {row['id']} "

    from . import chatter
    tell = (f"Tell {who.first()} only when something is ready for {who.first()} or live for the people who use it, and "
            f"say what changes for them in everyday words. Otherwise carry on and answer {chatter.SILENT}.")
    if row.get("approved"):
        tell = (f"This is the build {who.first()} approved. When it is finished, tell {who.first()} in plain words what "
                f"changed and what {who.first()} gets from it. Until then carry on and answer {chatter.SILENT}.")
    ran = f" {_ran_instead(row['instead_of'])}" if row.get("instead_of") else ""
    if row["state"] == "ended":
        return f"{head}has ended.{ran} {tool}to read why. {tell}"
    return f"{head}has a report for you.{ran} {tool}to read it. Check its work on {row['branch']}, then answer it. {tell}"


TASK_RUNNING = ("running",)


def deliver(row: dict) -> None:
    ""


    from . import waits
    task = row.get("origin_task") or ""
    if task:
        from . import board
        ok, _ = board.wake(task, _wake_note(row))
        if not ok:
            status = (board.get_task(task) or {}).get("raw_status")
            if status in TASK_RUNNING:
                return
            if status not in ("done", "archived", None):
                ok, _ = board.comment(task, _wake_note(row))
        if ok:
            _set(row["id"], told=1)
            return

    with store._lock:
        c = _db()
        c.execute("INSERT INTO wake_notes (bot_id, note, at, started) VALUES (?,?,?,0)",
                  (row["bot_id"], _wake_note(row), time.time()))
        c.execute("UPDATE code_sessions SET told=1 WHERE id=?", (row["id"],))
        c.commit()
    waits.kick()


def _expire(now: float) -> None:
    with store._lock:
        _db().execute("UPDATE code_sessions SET state='ended', why_ended='Nobody answered it for three days.' "
                      "WHERE state='reported' AND turn_started < ?", (now - ANSWER_SEC,))
        _db().commit()


async def poll_once() -> None:
    now = time.time()
    for row in running():
        try:
            shown = await asyncio.to_thread(_show, row["id"])
            await asyncio.to_thread(advance, row, shown, now)
        except Exception:
            log.exception("could not follow coding session %s", row["id"])
    _expire(now)
    for row in _db().execute("SELECT * FROM code_sessions WHERE told=0").fetchall():
        try:
            await asyncio.to_thread(deliver, dict(row))
        except Exception:
            log.exception("could not tell %s about coding session %s", row["bot_id"], row["id"])


async def run() -> None:
    while True:
        try:
            await poll_once()
        except Exception:
            log.exception("a coding-session pass failed")
        await asyncio.sleep(POLL_SEC if running() else IDLE_SEC)
