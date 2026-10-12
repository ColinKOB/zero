"""A small client for the hub's HTTP API, standard library only, so it runs on the Mac, on alien and in the
simulator harness without the hub's dependencies. Tokens are kept in memory or in a mode-600 file and never printed.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


class HubError(RuntimeError):
    def __init__(self, status: int, detail: str, path: str):
        super().__init__(f"{path}: HTTP {status}: {detail}")
        self.status, self.detail, self.path = status, detail, path


class Hub:
    def __init__(self, url: str, token: str | None = None, timeout: float = 30):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method: str, path: str, body=None, auth: bool = True, timeout: float | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                detail = json.loads(raw).get("detail", "")
            except ValueError:
                detail = raw[:200].decode(errors="replace")
            raise HubError(e.code, str(detail), path) from None

    def status(self, method: str, path: str, auth: bool = True, body=None) -> int:
        """The HTTP status only (for the checks that expect a refusal)."""
        try:
            self.call(method, path, body=body, auth=auth)
            return 200
        except HubError as e:
            return e.status

    def get(self, path: str, **kw):
        return self.call("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self.call("POST", path, body if body is not None else {}, **kw)

    # MARK: Pairing

    def pair(self, code: str, device_name: str = "Zero E2E harness") -> str:
        clean = "".join(ch for ch in code.upper() if ch.isalnum())
        self.token = self.post("/api/pair", {"code": clean, "device_name": device_name}, auth=False)["token"]
        return self.token

    def new_code(self) -> str:
        return self.post("/api/pair/new-code")["code"]

    def unpair(self) -> None:
        if self.token:
            try:
                self.post("/api/unpair")
            finally:
                self.token = None

    def save_token(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(self.token or "")

    @classmethod
    def from_token_file(cls, url: str, path: Path) -> "Hub":
        return cls(url, Path(path).read_text().strip())

    # MARK: Chat

    def ask(self, bot: str, text: str, wait: float = 300) -> str | None:
        """Sends a message to a Zip and follows the turn's event stream (SSE) to its reply; the text or None."""
        req = urllib.request.Request(f"{self.url}/api/bots/{bot}/messages", method="POST",
                                     data=json.dumps({"text": text}).encode())
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.token}")
        reply = None
        deadline = time.time() + wait
        try:
            with urllib.request.urlopen(req, timeout=wait) as r:
                for raw in r:
                    line = raw.decode(errors="replace").strip()
                    if line.startswith("data:"):
                        try:
                            ev = json.loads(line[5:])
                        except ValueError:
                            continue
                        if ev.get("type") == "reply":
                            reply = ev.get("text")
                        elif ev.get("type") == "done":
                            break
                    if time.time() > deadline:
                        break
        except urllib.error.HTTPError as e:
            raise HubError(e.code, e.read()[:200].decode(errors="replace"), f"/api/bots/{bot}/messages") from None
        return reply

    def messages(self, bot: str) -> list[dict]:
        data = self.get(f"/api/bots/{bot}/messages")
        return data.get("messages", data if isinstance(data, list) else [])
