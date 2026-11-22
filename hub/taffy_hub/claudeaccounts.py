""




























import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, store, usage

log = logging.getLogger("taffy.claudeaccounts")

MAIN = "main"
AUTO_AT = 98.0
READ_EVERY = 30 * 60
LOGIN_SEC = 15 * 60
_lock = threading.Lock()
_logins: dict[str, dict] = {}
_erased = 0
_URL = re.compile(r"https://\S+/oauth/authorize\?\S+")
_ID = re.compile(r"acct-[0-9a-f]{8}")


def _file() -> Path:
    return config.HUB_DIR / "claude-accounts.json"


def folders() -> Path:
    return config.HUB_DIR / "claude-accounts"


def _load() -> dict:
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("accounts", [])
    data.setdefault("active", MAIN)
    data.setdefault("auto", True)
    data.setdefault("readings", {})
    data.setdefault("refused", {})
    return data


def _save(data: dict) -> None:
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(f)


def folder(account_id: str) -> Path | None:
    ""
    return None if account_id == MAIN else folders() / account_id


def _status(account_id: str) -> dict:
    ""
    env = dict(os.environ)
    if account_id != MAIN:
        env["CLAUDE_CONFIG_DIR"] = str(folder(account_id))
    try:
        out = subprocess.run(["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=20, env=env)
        data = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return {"signed_in": bool(data.get("loggedIn")), "email": data.get("email") or "",
            "plan": data.get("subscriptionType") or ""}


def view() -> dict:
    ""
    with _lock:
        data = _load()
        if not any(a["id"] == MAIN for a in data["accounts"]):
            s = _status(MAIN)
            data["accounts"].insert(0, {"id": MAIN, "label": "Main", "email": s.get("email", ""),
                                        "plan": s.get("plan", ""), "added": None})
            _save(data)
    from . import usagesplit
    out = []
    for a in data["accounts"]:
        r = data["readings"].get(a["id"]) or {}
        try:
            parts = usagesplit.split(a["id"])
        except Exception:
            log.warning("could not split %s's week", a["id"], exc_info=True)
            parts = []
        out.append({"id": a["id"], "label": a["label"], "email": a.get("email") or "", "plan": a.get("plan") or "",
                    "active": a["id"] == data["active"], "weekly": r.get("weekly"), "weekly_resets": r.get("weekly_resets"),
                    "five_hour": r.get("five_hour"), "five_hour_resets": r.get("five_hour_resets"), "read_at": r.get("at"),
                    "split": parts})
    return {"accounts": out, "auto": data["auto"]}


def active() -> str:
    return _load()["active"]


def use(account_id: str) -> dict:
    with _lock:
        data = _load()
        if not any(a["id"] == account_id for a in data["accounts"]) and account_id != MAIN:
            raise ValueError("That account isn't here anymore.")
        data["active"] = account_id
        _save(data)
    log.info("Zips now think on Claude account %s", account_id)
    return view()


def set_auto(on: bool) -> dict:
    with _lock:
        data = _load()
        data["auto"] = bool(on)
        _save(data)
    return view()


def remove(account_id: str) -> dict:
    if account_id == MAIN:
        raise ValueError("The main account is Claude Code's own sign-in on the home computer. It stays.")
    with _lock:
        data = _load()

        if not _ID.fullmatch(account_id) or not any(a["id"] == account_id for a in data["accounts"]):
            raise ValueError("That account isn't here anymore.")
        data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
        data["readings"].pop(account_id, None)
        data["refused"].pop(account_id, None)
        if data["active"] == account_id:
            data["active"] = MAIN
        _save(data)
    shutil.rmtree(folders() / account_id, ignore_errors=True)
    return view()


def clear() -> int:
    ""
    global _erased
    with _lock:
        _erased += 1
        n = len([a for a in _load()["accounts"] if a["id"] != MAIN])
        for e in list(_logins.values()):
            e["proc"].kill()
        _logins.clear()
        shutil.rmtree(folders(), ignore_errors=True)
        _file().unlink(missing_ok=True)
    return n




def start_login() -> dict:
    ""

    _drop_stale()
    account_id = "acct-" + secrets.token_hex(4)
    d = folders() / account_id
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(d), BROWSER="true")
    proc = subprocess.Popen(["claude", "auth", "login", "--claudeai"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env, bufsize=1)
    url, deadline, seen = None, time.time() + 30, []
    while time.time() < deadline and proc.poll() is None:
        line = proc.stdout.readline()
        seen.append(line)
        if m := _URL.search(line):
            url = m.group(0)
            break
    if not url:
        proc.kill()
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError("Claude's sign-in didn't start. Try again in a minute.")
    login = secrets.token_urlsafe(12)
    _logins[login] = {"proc": proc, "dir": d, "id": account_id, "started": time.time()}
    return {"login": login, "url": url}


def finish_login(login: str, code: str) -> dict:
    ""
    entry = _logins.pop(login, None)
    if entry is None:
        raise ValueError("That sign-in ran out. Start it again.")
    proc, account_id = entry["proc"], entry["id"]
    erased = _erased
    code = code.strip()
    if not code or len(code) > 400 or any(c.isspace() for c in code):
        proc.kill()
        shutil.rmtree(entry["dir"], ignore_errors=True)
        raise ValueError("Paste the whole code from Claude's page.")
    try:
        proc.communicate(code + "\n", timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
    view()
    s = _status(account_id)
    if not s.get("signed_in"):
        shutil.rmtree(entry["dir"], ignore_errors=True)
        raise ValueError("That code didn't sign in. Start again and paste the code right after signing in.")
    with _lock:
        if _erased != erased:
            shutil.rmtree(entry["dir"], ignore_errors=True)
            raise ValueError("Everything was deleted while this signed in, so it wasn't added.")
        data = _load()
        if any((a.get("email") or "").lower() == s["email"].lower() for a in data["accounts"] if s["email"]):
            shutil.rmtree(entry["dir"], ignore_errors=True)
            raise ValueError(f"{s['email']} is already here. Sign in with your other Claude account.")
        data["accounts"].append({"id": account_id, "label": f"Account {len(data['accounts']) + 1}", "email": s["email"],
                                 "plan": s["plan"], "added": time.time()})
        _save(data)
    return view()


def _drop_stale() -> None:
    for login, e in list(_logins.items()):
        if time.time() - e["started"] > LOGIN_SEC:
            _logins.pop(login, None)
            e["proc"].kill()
            shutil.rmtree(e["dir"], ignore_errors=True)




def read_all() -> dict:
    ""

    data = _load()
    ids = [a["id"] for a in data["accounts"]] or [MAIN]
    readings = {}
    for account_id in ids:
        d = folder(account_id)
        rec = usage.read_plan(credentials=(d / ".credentials.json") if d else None)
        if rec:
            readings[account_id] = {**{k: rec.get(k) for k in ("weekly", "weekly_resets", "five_hour", "five_hour_resets")},
                                    "at": time.time()}
    with _lock:
        if not _file().exists():
            return readings
        data = _load()
        data["readings"].update({k: v for k, v in readings.items() if any(a["id"] == k for a in data["accounts"])})
        _save(data)
    return readings


def _week(r: dict) -> float:


    try:
        if datetime.fromisoformat(str(r.get("weekly_resets"))).timestamp() <= time.time():
            return 0.0
    except ValueError:
        pass
    return float(r.get("weekly") or 0)


def move_if_spent() -> str | None:
    ""
    data = _load()
    if not data["auto"]:
        return None
    cur = data["readings"].get(data["active"]) or {}
    if _week(cur) < AUTO_AT:
        return None
    others = [(_week(r), a["id"]) for a in data["accounts"]
              if a["id"] != data["active"] and a["id"] not in data["refused"] and (r := data["readings"].get(a["id"]))]
    room = sorted(o for o in others if o[0] < AUTO_AT - 5)
    if not room:
        return None
    use(room[0][1])
    return room[0][1]




REFUSED = re.compile(r"can(?:'|’)?t use your claude subscription|oauth authentication is currently not allowed|"
                     r"payment is past due", re.I)
LIMITED = re.compile(r"you(?:'|’)ve hit your [\w -]*limit", re.I)


def refusal(text: str) -> str | None:
    ""

    return "refused" if REFUSED.search(text or "") else "limited" if LIMITED.search(text or "") else None


_REAL_RUN = subprocess.run


def probe(account_id: str) -> str:
    ""
    from . import accounts, apns
    if subprocess.run is _REAL_RUN and apns._under_tests():
        return "unknown"
    env = accounts._env()
    if account_id != MAIN:
        env["CLAUDE_CONFIG_DIR"] = str(folder(account_id))
    try:
        out = subprocess.run([accounts.claude_bin() or "claude", "-p", "Reply with the word OK.", "--model", "haiku",
                              "--output-format", "json"], capture_output=True, text=True, timeout=90, env=env,
                             stdin=subprocess.DEVNULL, cwd=str(config.HOME))
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        data = {}
    if out.returncode == 0 and isinstance(data, dict) and data.get("is_error") is False:
        return "ok"
    text = f"{out.stdout}\n{out.stderr}"
    return refusal(text) or "unknown"


def _mark(account_id: str, refused: bool) -> None:
    with _lock:
        data = _load()
        if refused == (account_id in data["refused"]):
            return
        if refused:
            data["refused"].setdefault(account_id, time.time())
        else:
            data["refused"].pop(account_id, None)
        _save(data)


def off_refused() -> tuple[str | None, bool]:
    ""

    data = _load()
    cur = data["active"]
    if probe(cur) != "refused":
        return None, False
    _mark(cur, True)
    log.warning("Claude account %s refused its run (plan cancelled, past due or signed out)", cur)
    if not data["auto"]:
        return None, True
    others = sorted((_week(data["readings"].get(a["id"]) or {}), a["id"]) for a in data["accounts"]
                    if a["id"] != cur and a["id"] not in data["refused"])
    for week, account_id in others:
        if week >= AUTO_AT - 5:
            continue
        kind = probe(account_id)
        if kind == "ok":
            use(account_id)
            return account_id, True
        if kind == "refused":
            _mark(account_id, True)
    return None, True


def works_again() -> list[str]:
    ""
    back = []
    for account_id in list(_load()["refused"]):
        if probe(account_id) == "ok":
            _mark(account_id, False)
            back.append(account_id)
    return back


def _who(account_id: str) -> str:
    a = {x["id"]: x for x in view()["accounts"]}.get(account_id) or {}
    return a.get("email") or a.get("label") or "your other Claude account"


async def run() -> None:
    ""
    from . import health
    while True:
        await asyncio.sleep(READ_EVERY)
        try:
            await asyncio.to_thread(read_all)
            moved = await asyncio.to_thread(move_if_spent)
            if moved:
                await _tell_moved(moved)
            if _load()["refused"]:
                active_id = _load()["active"]
                for account_id in await asyncio.to_thread(works_again):
                    if account_id == active_id:
                        await health.claude_works_again()
                    else:
                        await _tell(f"Your Claude account {_who(account_id)} works again.", "Claude account works")
        except Exception:
            log.warning("Claude account check failed", exc_info=True)


async def _tell(line: str, title: str) -> None:
    from . import watcher
    store.add_feed("checkin", "default", None, title, line, written_by="hub", written_for="person")
    await watcher.push(title, line, "default", kind="progress")


async def _tell_moved(account_id: str, refused: bool = False) -> None:
    who = _who(account_id)
    if refused:
        line = (f"I moved your Zips to {who} because your other Claude account stopped working. Check that account's "
                "plan at claude.ai.")
    else:
        line = f"My Claude account ran out for the week, so I switched to {who}. You can change it in Settings, Accounts."
    await _tell(line, "Switched Claude accounts")
