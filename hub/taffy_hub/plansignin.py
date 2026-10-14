"""Signing a home on the person's own plan in to their Claude account, from the app.

The home's official Claude Code does the sign-in itself (`claude auth login --claudeai`), as the home's own user, so
Zero never sees a password or keeps a token: Claude Code prints its sign-in page, the app opens it, the person signs
in on claude.ai and copies the code the page shows, the app sends the code here, and Claude Code finishes and keeps
its login in the home's ~/.claude like any Claude Code on a computer.

Only one sign-in runs at a time; a new start ends the last one. A home on a plan someone shares with it (the plan
broker) never runs this.
"""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import threading
import time

from . import accounts, home

URL_RE = re.compile(r"https://(?:claude\.com|claude\.ai|platform\.claude\.com|console\.anthropic\.com)/[^\s\x07\x1b\]]+")
CODE_RE = re.compile(r"^[A-Za-z0-9_\-#.~=+/]{8,600}$")
START_TIMEOUT = 25
FINISH_TIMEOUT = 45


class SignInError(Exception):
    """One plain sentence for the person."""


class _Login:
    def __init__(self, pid: int, fd: int):
        self.pid, self.fd = pid, fd
        self.out = ""
        self.started = time.time()

    def alive(self) -> bool:
        try:
            done, _ = os.waitpid(self.pid, os.WNOHANG)
            return done == 0
        except ChildProcessError:
            return False

    def read(self, seconds: float) -> str:
        """Whatever arrives within `seconds` (stops early when the process ends)."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.out += chunk.decode(errors="replace")
            elif not self.alive():
                break
        return self.out

    def end(self) -> None:
        try:
            if self.alive():
                os.kill(self.pid, signal.SIGTERM)
                time.sleep(0.2)
                if self.alive():
                    os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, os.WNOHANG)
        except (ProcessLookupError, ChildProcessError):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


_current: _Login | None = None
_lock = threading.Lock()


def own_plan() -> bool:
    return home.plan_kind() == "own"


def _spawn(argv: list[str]) -> _Login:
    env = accounts._env()
    env.update(TERM="dumb", NO_COLOR="1", BROWSER="/bin/true", DISPLAY="")
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvpe(argv[0], argv, env)
        finally:
            os._exit(127)
    return _Login(pid, fd)


def start() -> str:
    """Starts Claude Code's sign-in and returns the page to open."""
    global _current
    if not own_plan():
        raise SignInError("Your Zips use a plan someone shares with you, so there is nothing to sign in to here.")
    exe = accounts.claude_bin()
    if not exe:
        raise SignInError("Claude Code is missing on the computer your Zips use. Ask for help setting it up.")
    with _lock:
        if _current:
            _current.end()
            _current = None
        login = _spawn([exe, "auth", "login", "--claudeai"])
        out = ""
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            out = login.read(1.0)
            m = URL_RE.search(out)
            if m:
                _current = login
                return m.group(0)
            if not login.alive():
                break
        login.end()
    raise SignInError("Claude did not answer. Try again in a minute.")


def finish(code: str) -> dict:
    """Hands Claude Code the code from the sign-in page and waits for it to finish. {"ok", "account", "detail"}."""
    global _current
    code = (code or "").strip()
    if not CODE_RE.match(code):
        raise SignInError("That does not look like the whole code. Copy it again from the Claude page.")
    with _lock:
        login = _current
        if not login or not login.alive():
            _current = None
            raise SignInError("The sign-in ended. Choose Sign In With Claude again.")
        os.write(login.fd, (code + "\r").encode())
        deadline = time.time() + FINISH_TIMEOUT
        while login.alive() and time.time() < deadline:
            login.read(1.0)
        login.end()
        _current = None
    status = check()
    if status.get("state") == "signed_in":
        accounts._last = None
        return {"ok": True, "account": status.get("account", ""), "detail": "Claude is connected."}
    return {"ok": False, "account": "",
            "detail": "That code did not work. Choose Sign In With Claude again and copy the newest code."}


def cancel() -> None:
    global _current
    with _lock:
        if _current:
            _current.end()
            _current = None


def check() -> dict:
    """`claude auth status --json`, read the way accounts.py reads it."""
    exe = accounts.claude_bin()
    if not exe:
        return {"state": "missing", "account": ""}
    try:
        p = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=25,
                           env=accounts._env())
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unknown", "account": ""}
    return accounts.parse_auth(p.stdout)
