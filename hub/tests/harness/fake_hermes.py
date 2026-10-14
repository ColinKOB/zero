"""A stand-in for the Hermes API server, for running the real hub on the Mac against a temporary home (dry mode).

It answers the routes the hub uses (hermes.py, approvals.py, health.py): sessions, messages, the chat stream (SSE),
steer and approval. No model is called. Every turn's input is appended to `<home>/fake-hermes/turns.jsonl`, so a test
can scan exactly what the hub sends to the Zips (the Colin-free check).

The lead behaves like the real one for the one request the onboarding sends ("Please make me these Zips from their
templates: Inbox (the inbox template), ..."): it does what the plugin's team_add_bot does to the home (a profile
folder with .env, SOUL.md and a USER.md copy, a bots.json entry, the template's look, a work folder) and answers in
one sentence. A Zip made from the inbox template answers a question about the inbox by listing the subjects of the
seeded mail (greenmail/messages.json). That reply is simulated; the live run asks the real Inbox Zip.

  python3 fake_hermes.py --home <dir> --port <n>     (the home is the user's home directory, as in homes.py)
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
SEED = HERE / "greenmail" / "messages.json"
MAKE = re.compile(r"Please make me these Zips from their templates:(.*)", re.S)
PICK = re.compile(r"\(the ([a-z][a-z0-9]{1,19}) template\)")


class FakeHermes:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.hermes = self.home / ".taffy"
        self.state = self.home / "taffy" / "hub-state"
        self.log = self.home / "fake-hermes" / "turns.jsonl"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, dict] = {}
        self.lock = threading.Lock()

    # MARK: Keys and profiles

    def key(self, bot: str) -> str | None:
        env = self.hermes / ".env" if bot == "default" else self.hermes / "profiles" / bot / ".env"
        try:
            for line in env.read_text().splitlines():
                if line.startswith("API_SERVER_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            return None
        return None

    def bot_exists(self, bot: str) -> bool:
        return bot == "default" or (self.hermes / "profiles" / bot).is_dir()

    # MARK: What a turn does

    def reply(self, bot: str, text: str) -> str:
        m = MAKE.search(text)
        if bot == "default" and m:
            made = [self.make_zip(tid) for tid in PICK.findall(m.group(1))]
            made = [n for n in made if n]
            if not made:
                return "I couldn't find those templates."
            return f"Done. {' and '.join(made)} {'is' if len(made) == 1 else 'are'} on the team now."
        if bot == "inbox" or self._template_of(bot) == "inbox":
            if re.search(r"inbox|messages|mail|subjects", text, re.I):
                subjects = [x["subject"] for x in json.loads(SEED.read_text())["messages"]]
                return "Here is what is in your inbox:\n" + "\n".join(f"- {s}" for s in subjects)
        return "Got it."

    def _template_of(self, bot: str) -> str | None:
        try:
            for e in json.loads((self.state / "bots.json").read_text()):
                if e.get("id") == bot:
                    return e.get("template")
        except (OSError, ValueError):
            pass
        return None

    def make_zip(self, template_id: str) -> str | None:
        """What team_add_bot does to the home, without Hermes."""
        try:
            templates = json.loads((self.home / "taffy" / "shared" / "zip-templates.json").read_text())
        except (OSError, ValueError):
            return None
        tpl = next((t for t in templates if t.get("id") == template_id), None)
        if not tpl:
            return None
        bot_id = tpl.get("zip_id") or tpl["id"]
        profile = self.hermes / "profiles" / bot_id
        if profile.is_dir():
            return tpl["name"]
        (profile / "memories").mkdir(parents=True, exist_ok=True)
        (profile / ".env").write_text(f"API_SERVER_KEY={secrets.token_urlsafe(24)}\n")
        person = self._person()
        (profile / "SOUL.md").write_text(tpl.get("job", "").replace("{name}", person["name"] or "the person")
                                         .replace("{first_name}", person["first_name"] or "the person"))
        lead_user = self.hermes / "memories" / "USER.md"
        (profile / "memories" / "USER.md").write_text(lead_user.read_text() if lead_user.exists() else "")
        (profile / "memories" / "MEMORY.md").write_text("")
        (self.home / "taffy" / "work" / bot_id).mkdir(parents=True, exist_ok=True)
        reg_path = self.state / "bots.json"
        try:
            reg = json.loads(reg_path.read_text())
        except (OSError, ValueError):
            reg = []
        look = tpl.get("look") or {}
        reg.append({"id": bot_id, "name": tpl["name"], "role": tpl.get("role", ""), "color": look.get("color", "#64748B"),
                    "shape": look.get("shape", "pebble"), "symbol": "sparkles", "created_at": time.time(),
                    "template": tpl["id"]})
        tmp = reg_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=1))
        tmp.replace(reg_path)
        looks_path = self.state / "bot-looks.json"
        try:
            looks = json.loads(looks_path.read_text())
        except (OSError, ValueError):
            looks = {}
        looks[bot_id] = look
        looks_path.write_text(json.dumps(looks, indent=1))
        return tpl["name"]

    def _person(self) -> dict:
        try:
            p = json.loads((self.state / "person.json").read_text())
            return {"name": p.get("name") or "", "first_name": p.get("first_name") or ""}
        except (OSError, ValueError):
            return {"name": "", "first_name": ""}

    def record(self, bot: str, sid: str, text: str) -> None:
        with self.lock, self.log.open("a") as f:
            f.write(json.dumps({"at": time.time(), "bot": bot, "session": sid, "input": text}) + "\n")


def make_handler(fake: FakeHermes):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _json(self, code: int, body) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self) -> tuple[str, str]:
            path = urlparse(self.path).path
            m = re.match(r"^/p/([a-z][a-z0-9]{1,19})(/.*)$", path)
            return (m.group(1), m.group(2)) if m else ("default", path)

        def _authorized(self, bot: str) -> bool:
            want = fake.key(bot)
            got = self.headers.get("Authorization", "")
            return bool(want) and got == f"Bearer {want}"

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            bot, path = self._route()
            if path == "/health":
                return self._json(200, {"status": "ok", "fake": True})
            if not self._authorized(bot):
                return self._json(401, {"error": "unauthorized"})
            m = re.match(r"^/api/sessions/([\w-]+)/messages$", path)
            if m:
                s = fake.sessions.get(m.group(1))
                if not s or s["bot"] != bot:
                    return self._json(404, {"error": "no such session"})
                return self._json(200, {"data": s["messages"][-500:]})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            bot, path = self._route()
            if not fake.bot_exists(bot):
                return self._json(404, {"error": "no such profile"})
            if not self._authorized(bot):
                return self._json(401, {"error": "unauthorized"})
            body = self._body()
            if path == "/api/sessions":
                sid = "s" + secrets.token_hex(6)
                fake.sessions[sid] = {"bot": bot, "title": body.get("title", ""), "messages": []}
                return self._json(200, {"session": {"id": sid}})
            m = re.match(r"^/api/sessions/([\w-]+)/chat/stream$", path)
            if m:
                return self._stream(bot, m.group(1), str(body.get("input") or ""))
            if re.match(r"^/v1/runs/[\w-]+/(steer|approval)$", path):
                return self._json(200, {"accepted": False})
            return self._json(404, {"error": "not found"})

        def _stream(self, bot: str, sid: str, text: str):
            s = fake.sessions.get(sid)
            if not s or s["bot"] != bot:
                return self._json(404, {"error": "no such session"})
            fake.record(bot, sid, text)
            now = time.time()
            s["messages"].append({"id": len(s["messages"]) + 1, "role": "user", "content": text, "timestamp": now})
            answer = fake.reply(bot, text)
            s["messages"].append({"id": len(s["messages"]) + 1, "role": "assistant", "content": answer,
                                  "timestamp": now + 0.5})
            run_id = "r" + secrets.token_hex(6)
            events = [("run.started", {"run_id": run_id}),
                      ("assistant.delta", {"run_id": run_id, "delta": answer}),
                      ("assistant.completed", {"run_id": run_id, "content": answer,
                                               "runtime": {"provider": "fake"}}),
                      ("run.completed", {"run_id": run_id})]
            payload = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def serve(home: Path, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    fake = FakeHermes(home)
    server = ThreadingHTTPServer((host, port), make_handler(fake))
    server.fake = fake
    return server


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="A stand-in Hermes API server for the hub's dry runs.")
    ap.add_argument("--home", required=True)
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args(argv)
    server = serve(Path(a.home), a.port)
    print(f"fake hermes on 127.0.0.1:{a.port} for {a.home}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
