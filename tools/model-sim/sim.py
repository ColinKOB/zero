"""The fair blind test (Colin, Oct 12): every scenario once with each model, through the same hub chat the app uses,
with a simulated user answering what the phone shows. Writes ~/bench/sim/app-results.json for the blind page
(tools/model-sim/view.py).

Colin: "exactly apples to apples, with what it currently uses: the Zips' current harness and instructions and
everything. I don't want to be voting on which AI I like best. I want to be voting on which AI I like responding and
working with me best."

So this talks to the test home's lead Zip the way the iPhone app does:
- A paired device (`taffy-hub pair`) sends each message with POST /api/bots/default/messages and follows the SSE
  stream: the hub's turn, sign-in consent, the chat ("api_server") instructions, the SOUL, the plugin and the gate,
  Haiku's short version (shortreply.py), step lines and backup marking all run exactly as for Colin.
- What the phone shows is read back with GET /api/bots/default/messages: each bubble's display text, the full text
  behind Show Full Reply, the backup flag. Step lines come from the stream (the app's activity row), choice cards and
  other cards from GET /api/bots/default/activity.
- The simulated user (Sonnet through `claude -p`, as before) reads only that: displayed text, step lines and cards.
  It can tap a choice the way the app does (answer the card, then send the option as the person's message).

Only the model changes between runs, the way alien/bin/model-trial.py changes it: config.yaml model.provider and
default, then the home's set-toolsets.py, then a restart of this home's taffy-gateway (never another home's).
The home goes back to Opus at the end.

Each scenario starts from the same home: the lead's chat pointer is cleared (as the hub does when a session is gone,
store.set_session(bot, "")), so the next message opens a new Hermes session, and everything a conversation can leave
behind is put back: the Zip's memory files, the shared facts store, scheduled jobs, files saved outside the run
folder, and the conversation's own sessions (which the facts recall and session_search would otherwise find in the
next scenario). The sample files are copied fresh into the lead's work folder, which its sandbox mounts read-write.

Run as the test home's user with its venv (ruamel for config.yaml):
    . ~/bench/env.sh && ~/taffy/.venv/bin/python ~/bench/sim/sim.py [scenario ids]
Progress is saved after every run; a second start skips runs already in the results.
"""
import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS  # noqa: E402

HOME = Path.home()
HERMES_HOME = Path(os.environ.get("HERMES_HOME", HOME / ".taffy"))
CONF = json.loads((HOME / "taffy" / "home.json").read_text())
HUB_PORT = int(CONF["ports"]["hub"])
HERMES = f"http://127.0.0.1:{int(CONF['ports']['hermes_api'])}"
HUB_DB = HOME / "taffy" / "hub-state" / "hub.db"
USAGE = HOME / "taffy" / "hub-state" / "usage"
BUSY = HOME / "taffy" / "hub-state" / "busy.json"
STATE_DB = HERMES_HOME / "state.db"
CONFIG = HERMES_HOME / "config.yaml"
VENV = HOME / "taffy" / ".venv" / "bin"
BIN = HOME / "taffy" / "bin"
WORK = HOME / "taffy" / "work" / "default"
FILES = WORK / "files"                   # the same path in every run, so A and B read the same words
FIX = HOME / "bench" / "fix"
TRIP = Path(__file__).parent / "fix-trip"
OUT = HOME / "bench" / "sim"
RUNS = OUT / "app-runs"
BASE = OUT / "app-baseline"
RESULTS = Path(os.environ.get("SIM_RESULTS") or OUT / "app-results.json")
BOT = "default"
MODELS = {"opus": {"provider": "claude-subscription-directsdk-experimental", "default": "opus"},
          "sol": {"provider": "openai-codex", "default": "gpt-6.1-sol"}}
SERVED = {"opus": "opus", "sol": "gpt-6.1-sol"}
VISIBLE = {"colin", "bot", "board_reply", "call_reply"}   # history.VISIBLE: what the app's chat shows
CARD_KINDS = {"choices", "login", "account", "signin", "signin_decision", "action", "image", "checkin", "mac_ask",
              "mac", "event", "team", "call"}
MAX_TURNS = 5
PAUSE_WAIT = 120         # a turn held on a card: how long to wait before the person answers in words instead
PRODUCT_CHARS = 40000
SETTLE = 60


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# MARK: The hub, as the app talks to it

class Hub:
    def __init__(self):
        self.token = self._pair()

    def _pair(self) -> str:
        saved = RUNS / "device.json"
        if saved.exists():
            token = json.loads(saved.read_text())["token"]
            try:
                self.token = token
                self.get("/api/me")
                return token
            except Exception:
                pass
        out = subprocess.run([str(BIN / "taffy-hub"), "pair"], capture_output=True, text=True, check=True).stdout
        # "Pairing code: ABCD-EFGH (valid for 0.25 hours)"; the code itself has no dash
        code = next(l.split(":", 1)[1].split("(")[0].strip().replace("-", "") for l in out.splitlines()
                    if l.startswith("Pairing code:"))
        token = self._call("POST", "/api/pair", {"code": code, "device_name": "Blind test (simulated person)"},
                           auth=False)["token"]
        saved.write_text(json.dumps({"token": token}))
        saved.chmod(0o600)
        return token

    def _call(self, method: str, path: str, body=None, auth=True, timeout=60):
        c = http.client.HTTPConnection("127.0.0.1", HUB_PORT, timeout=timeout)
        h = {"Content-Type": "application/json"}
        if auth:
            h["Authorization"] = f"Bearer {self.token}"
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        if r.status >= 400:
            raise RuntimeError(f"{method} {path}: {r.status} {data[:300]!r}")
        return json.loads(data or b"{}")

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, body):
        return self._call("POST", path, body)

    def unpair(self):
        try:
            self.post("/api/unpair", {})
        finally:
            (RUNS / "device.json").unlink(missing_ok=True)

    def send(self, text: str) -> dict:
        """One message, as the app sends it: POST, then the turn's SSE events until done. A turn held on a card for
        PAUSE_WAIT is left running (the person will answer in words, which makes it give way: turns.send)."""
        c = http.client.HTTPConnection("127.0.0.1", HUB_PORT, timeout=1800)
        c.request("POST", f"/api/bots/{BOT}/messages", json.dumps({"text": text}),
                  {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        r = c.getresponse()
        if r.status >= 400:
            raise RuntimeError(f"send: {r.status} {r.read()[:300]!r}")
        events, paused_at = [], None
        c.sock.settimeout(90)   # the hub sends a keepalive every 15 s
        while True:
            try:
                line = r.readline()
            except TimeoutError:
                events.append({"type": "lost", "t": round(time.time(), 2)})
                break
            if not line:
                break
            line = line.decode(errors="replace").strip()
            if line.startswith("data:"):
                try:
                    ev = json.loads(line[5:])
                except ValueError:
                    continue
                if ev.get("type") == "delta":
                    continue
                ev["t"] = round(time.time(), 2)
                events.append(ev)
                if ev.get("type") == "status" and ev.get("waiting"):
                    paused_at = paused_at or time.time()
                elif ev.get("type") in ("status", "commentary", "reply"):
                    paused_at = None if not ev.get("waiting") else paused_at
                if ev.get("type") == "done":
                    break
            if paused_at and time.time() - paused_at > PAUSE_WAIT:
                events.append({"type": "paused", "t": round(time.time(), 2)})
                break
        c.close()
        return {"events": events}

    def shown(self) -> list[dict]:
        return self.get(f"/api/bots/{BOT}/messages")["messages"]

    def cards(self) -> list[dict]:
        return self.get(f"/api/bots/{BOT}/activity")["items"]

    def idle(self, bot: str | None = None) -> bool:
        bots = self.get("/api/bots")["bots"]
        return not any(b["busy"] for b in bots if bot is None or b["id"] == bot)

    def wait_idle(self, bot: str | None = BOT, limit: float = 600) -> None:
        deadline = time.time() + limit
        while not self.idle(bot) and time.time() < deadline:
            time.sleep(2)


# MARK: Changing only the model, as alien/bin/model-trial.py does

def busy_ok() -> bool:
    try:
        b = json.loads(BUSY.read_text())
    except (OSError, ValueError):
        return False
    return time.time() - b.get("at", 0) < 30 and not (b["calls"] or b["queued"] or b["mac"])


def set_model(model: str, hub: Hub) -> None:
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(CONFIG.read_text())
    shutil.copy2(CONFIG, CONFIG.with_name(f"config.yaml.bak-trial-{time.strftime('%Y%m%d-%H%M%S')}"))
    data.setdefault("model", {}).update(MODELS[model])
    with CONFIG.open("w") as f:
        yaml.dump(data, f)
    # The home's own deploy runs it this way (install.sh, zero-home-create): its venv, its HERMES_HOME and ZERO_HOME.
    subprocess.run([str(VENV / "python"), str(BIN / "set-toolsets.py")], check=True, capture_output=True,
                   env={**os.environ, "HERMES_HOME": str(HERMES_HOME), "ZERO_HOME": str(HOME / "taffy" / "home.json")})
    deadline = time.time() + 600
    while not (busy_ok() and hub.idle()):
        if time.time() > deadline:
            raise RuntimeError("the test home stayed busy; not restarting its gateway")
        time.sleep(5)
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    subprocess.run(["systemctl", "--user", "restart", "taffy-gateway"], check=True, env=env)
    for _ in range(120):
        time.sleep(2)
        try:
            if urllib.request.urlopen(f"{HERMES}/health", timeout=5).status == 200:
                break
        except Exception:
            pass
    time.sleep(10)
    got = yaml.load(CONFIG.read_text())
    log(f"model set to {dict(got['model'])}, fallback {list(got.get('fallback_providers') or [])}")


# MARK: The same home for every scenario

def hermes_key() -> str:
    for l in (HERMES_HOME / ".env").read_text().splitlines():
        if l.startswith("API_SERVER_KEY="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no API_SERVER_KEY")


def hub_db():
    c = sqlite3.connect(HUB_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def cron_ids() -> list[str]:
    try:
        d = json.loads((HERMES_HOME / "cron" / "jobs.json").read_text())
    except (OSError, ValueError):
        return []
    jobs = d.get("jobs", []) if isinstance(d, dict) else d
    return [j["id"] for j in jobs]


def work_entries() -> set[str]:
    return {str(p.relative_to(WORK)) for p in WORK.rglob("*") if p.is_file() and not p.is_relative_to(FILES)}


def baseline() -> dict:
    """The home as it is before the first scenario, kept for putting it back."""
    f = BASE / "baseline.json"
    if f.exists():
        return json.loads(f.read_text())
    BASE.mkdir(parents=True, exist_ok=True)
    shutil.copytree(HERMES_HOME / "memories", BASE / "memories", dirs_exist_ok=True)
    with hub_db() as c:
        facts = [dict(r) for r in c.execute("SELECT * FROM facts")]
        row = c.execute("SELECT session_id FROM bot_sessions WHERE bot_id=?", (BOT,)).fetchone()
    b = {"facts": facts, "session": row["session_id"] if row else "", "cron": cron_ids(),
         "work": sorted(work_entries()), "at": time.time()}
    f.write_text(json.dumps(b, indent=1))
    return b


def session_family(sid: str) -> list[str]:
    """A chat session and the continuations a compaction made of it."""
    out, todo = [], [sid]
    c = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=30)
    while todo:
        s = todo.pop()
        if s and s not in out:
            out.append(s)
            todo += [r[0] for r in c.execute("SELECT id FROM sessions WHERE parent_session_id=?", (s,))]
    c.close()
    return out


def reset(b: dict, leftovers: Path | None = None, sessions: list[str] = ()) -> dict:
    """Puts back what a conversation can leave behind, and clears the chat pointer for a new session."""
    done = {}
    for f in (BASE / "memories").glob("*.md"):
        live = HERMES_HOME / "memories" / f.name
        if not live.exists() or live.read_bytes() != f.read_bytes():
            shutil.copy2(f, live)
            done.setdefault("memory", []).append(f.name)
    keep = {r["id"]: r for r in b["facts"]}
    with hub_db() as c:
        new = [r["id"] for r in c.execute("SELECT id FROM facts") if r["id"] not in keep]
        if new:
            c.execute(f"DELETE FROM facts WHERE id IN ({','.join('?' * len(new))})", new)
            done["facts_removed"] = len(new)
        for r in c.execute("SELECT * FROM facts").fetchall():
            old = keep[r["id"]]
            if dict(r) != old:
                cols = [k for k in old if k != "id"]
                c.execute(f"UPDATE facts SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                          [old[k] for k in cols] + [r["id"]])
                done["facts_restored"] = done.get("facts_restored", 0) + 1
        c.execute("INSERT OR REPLACE INTO bot_sessions (bot_id, session_id) VALUES (?, '')", (BOT,))
        c.commit()
    for j in set(cron_ids()) - set(b["cron"]):
        subprocess.run([str(VENV / "hermes"), "cron", "remove", j], capture_output=True)
        done.setdefault("cron_removed", []).append(j)
    if leftovers:
        for rel in sorted(work_entries() - set(b["work"])):
            dst = leftovers / "elsewhere" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(WORK / rel), dst)
            done.setdefault("moved", []).append(rel)
    # Any lead chat opened since the baseline that is still there (a run cut off before its own clean-up): the facts
    # recall and session_search would find it in the next conversation.
    c = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=30)
    stray = [r[0] for r in c.execute("SELECT id FROM sessions WHERE source='api_server' AND started_at > ? AND id != ? "
                                     "AND title LIKE '% in the Taffy app%'", (b["at"], b["session"]))]
    c.close()
    sessions = list(dict.fromkeys([*sessions, *stray]))
    key = hermes_key()
    for s in sessions:
        req = urllib.request.Request(f"{HERMES}/api/sessions/{s}", method="DELETE",
                                     headers={"Authorization": f"Bearer {key}"})
        try:
            urllib.request.urlopen(req, timeout=30).read()
            done.setdefault("sessions_deleted", []).append(s)
        except Exception as e:
            log("could not delete session", s, e)
    if FILES.exists():
        shutil.rmtree(FILES)
    return done


# MARK: The simulated person

USER_PROMPT = """You are role-playing a person texting their personal assistant app. Stay in character.

Your brief (the assistant cannot see it):
{brief}

The conversation so far, as your phone shows it:
{transcript}

Write your next message to the assistant: short and casual, like a real text, one to three sentences. Give facts from
your brief only when the assistant asks for them or clearly needs them. Some long replies are shown short by the app;
if you need more detail than you see, ask for it. If the assistant offered options to tap, you may tap one instead of
typing (put its exact words in "tap"). If your brief says to end now, or the task is finished, or the assistant is
stuck after you have tried once to help, end the conversation.

Answer with JSON only: {{"message": "your next message, or empty if tapping or ending", "tap": "an offered option, or
empty", "done": true or false}}"""


def phone_text(rows: list[dict]) -> str:
    out = []
    for m in rows:
        if m["role"] == "user":
            out.append("You: " + m["text"])
        elif m["role"] == "zip":
            out.append("Assistant: " + m["text"][:4000])
        elif m["role"] == "steps" and m["steps"]:
            out.append("(While it worked, the app showed: " + ", ".join(m["steps"]) + ")")
        elif m["role"] == "card" and m["kind"] == "choices":
            opts = "".join(f" [{o}]" for o in m.get("options") or [])
            out.append(f"(Options to tap under the reply: {m['text']}{opts}"
                       + (f"; you tapped [{m['picked']}]" if m.get("picked") else "") + ")")
        elif m["role"] == "card" and m["kind"] == "event":
            out.append(f"(The app showed: {m['text']})")
        elif m["role"] == "card":
            out.append(f"(In the chat: {m['title'] + ': ' if m.get('title') else ''}{m['text']})")
        elif m["role"] == "error":
            out.append("(The app showed: " + m["text"] + ")")
    return "\n\n".join(out)


def user_turn(brief: str, rows: list[dict]) -> dict:
    p = subprocess.run(["claude", "-p", "--model", "sonnet", "--tools", "", "--output-format", "json"],
                       input=USER_PROMPT.format(brief=brief, transcript=phone_text(rows)), capture_output=True,
                       text=True, timeout=300, cwd=str(HOME))
    try:
        text = json.loads(p.stdout.strip().splitlines()[-1]).get("result", "")
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (ValueError, IndexError):
        return {"message": "", "done": True, "error": (p.stderr or p.stdout)[-300:]}


# MARK: One conversation

def card_row(item: dict) -> dict:
    """An item from the Zip's activity, as the app's chat draws it (AppState.loadChat): a choice card with its options,
    a sign-in or account card while open, a small centered event line ("Updated memory"), or a labeled line such as
    "Done: Set up a scheduled job: ..."."""
    raw = item.get("body") or ""
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    kind, title = item["kind"], item.get("title") or ""
    if kind == "choices":
        text = body.get("question") or title
    elif kind == "event":
        text = body.get("name") or title
    elif kind in ("login", "account"):
        text = body.get("why") or title
    else:
        text = raw if not body else (body.get("text") or body.get("summary") or body.get("task") or title)
    return {"role": "card", "kind": kind, "feed_id": item["id"], "title": title, "text": text,
            "options": body.get("options") or [], "state": body.get("state")}


def run(scn: dict, model: str, hub: Hub, b: dict) -> dict:
    rid = f"{scn['id']}-{model}-{time.strftime('%H%M%S')}"
    keep = RUNS / rid
    keep.mkdir(parents=True)
    reset(b)
    shutil.copytree(FIX, FILES, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(TRIP, FILES / "trip", dirs_exist_ok=True)
    brief = scn["brief"].format(d=FILES)
    message, tap = scn["open"].format(d=FILES), None
    rows, turns, errors = [], [], []
    seen_msgs = {m["id"] for m in hub.shown()}
    seen_cards = max([c["id"] for c in hub.cards()] or [0])
    started = time.time()
    for n in range(MAX_TURNS):
        hub.wait_idle()   # a card's follow-up or a board update still running from the last turn
        if tap:
            hub.post(f"/api/bots/{BOT}/choices/{tap['feed_id']}", {"picked": tap["option"]})
        rows.append({"role": "user", "text": message, **({"tapped": True} if tap else {})})
        t0 = time.time()
        got = hub.send(message)
        secs = round(time.time() - t0, 1)
        time.sleep(2)   # choice cards go up just after the reply (choices.flush)
        ev = got["events"]
        steps = []
        for e in ev:
            if e.get("type") == "status" and e.get("text") and (not steps or steps[-1] != e["text"]):
                steps.append(e["text"])
        rows.append({"role": "steps", "steps": steps, "secs": secs})
        shown = hub.shown()
        new = [m for m in shown if m["id"] not in seen_msgs]
        seen_msgs |= {m["id"] for m in shown}
        for m in new:
            if m.get("role") == "assistant" and m.get("class") in VISIBLE:
                row = {"role": "zip", "text": m.get("display") or m.get("text") or "", "id": m["id"]}
                if m.get("full") and m["full"] != row["text"]:
                    row["full"] = m["full"]
                if m.get("backup"):
                    row["backup"] = True
                if "<short>" in row["text"].lower():
                    log(f"warning: {rid} shows a raw <short> tag")
                rows.append(row)
        for e in ev:
            if e.get("type") in ("error", "stopped") and e.get("text"):
                rows.append({"role": "error", "text": e["text"]})
        if any(e.get("type") == "paused" for e in ev):
            rows.append({"role": "error", "text": "Paused for your okay."})
        new_cards = [card_row(c) for c in hub.cards() if c["id"] > seen_cards and c["kind"] in CARD_KINDS]
        seen_cards = max([seen_cards] + [c["feed_id"] for c in new_cards])
        rows += new_cards
        turns.append({"secs": secs, "events": ev, "new_messages": new})
        if n == MAX_TURNS - 1:
            break
        nxt = user_turn(brief, rows)
        if nxt.get("error"):
            errors.append("simulated user: " + nxt["error"])
        pick = (nxt.get("tap") or "").strip()
        choice = next((c for c in reversed(new_cards) if c["kind"] == "choices" and pick in c["options"]), None) \
            if pick else None
        if choice:
            choice["picked"] = pick
            tap, message = {"feed_id": choice["feed_id"], "option": pick}, pick
        elif nxt.get("done") or not nxt.get("message"):
            break
        else:
            tap, message = None, nxt["message"]
    ended = time.time()
    hub.wait_idle(None)
    time.sleep(SETTLE)   # the facts reader and Hermes's memory review run just after a turn
    product = {}
    for rel in scn["product"]:
        f = FILES / rel
        product[rel] = f.read_text(errors="replace")[:PRODUCT_CHARS] if f.is_file() else None
    check = None
    if scn.get("check"):
        c = subprocess.run(scn["check"].format(d=FILES), shell=True, capture_output=True, text=True, timeout=120)
        check = {"passed": c.returncode == 0, "output": (c.stdout + c.stderr)[-800:]}
    elsewhere = sorted(work_entries() - set(b["work"]))
    with hub_db() as c:
        row = c.execute("SELECT session_id FROM bot_sessions WHERE bot_id=?", (BOT,)).fetchone()
    sid = row["session_id"] if row else ""
    family = session_family(sid) if sid else []
    (keep / "phone.json").write_text(json.dumps(hub.shown(), indent=1))
    shutil.copytree(FILES, keep / "files", ignore=shutil.ignore_patterns("__pycache__"))
    cleaned = reset(b, leftovers=keep, sessions=family)
    log(f"{rid}: {sum(r['role'] == 'user' for r in rows)} turns, {round(ended - started)} s, reset {cleaned}")
    return {"run": rid, "scenario": scn["id"], "model": model, "sessions": family, "started": started, "ended": ended,
            "secs": round(sum(t["secs"] for t in turns), 1), "turns": sum(r["role"] == "user" for r in rows),
            "transcript": rows, "turn_detail": turns, "product": product, "check": check,
            "saved_elsewhere": elsewhere, "errors": errors}


# MARK: Usage, from the home's usage log

def usage_for(r: dict) -> dict:
    """The Zip's own model calls (matched by session) and the hub's calls for this Zip in the run's time window
    (Haiku's short versions and the facts reader), kept apart."""
    zips = {"read": 0, "write": 0, "uncached": 0, "out": 0, "reasoning": 0, "calls": 0}
    hub = dict(zips)
    served, origins = set(), {}
    sessions = set(r["sessions"])
    for f in sorted(USAGE.glob("*.jsonl")):
        for line in f.open():
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if x.get("home") not in (None, "", "test"):
                continue
            if x.get("session") in sessions:
                into = zips
                origins[x.get("origin") or ""] = origins.get(x.get("origin") or "", 0) + 1
                if x.get("origin") not in ("title-generation",) and x.get("model"):
                    served.add(x["model"])
            elif x.get("zip") == BOT and x.get("provider") == "hub" and \
                    r["started"] <= float(x.get("ts") or 0) <= r["ended"] + 60:
                into = hub
            else:
                continue
            for k in ("read", "write", "uncached", "out", "reasoning"):
                into[k] += int(x.get(k) or 0)
            into["calls"] += 1
    return {"usage": zips, "hub_usage": hub, "served": sorted(served), "origins": origins}


# MARK: Main

def save(results: list[dict]) -> None:
    tmp = RESULTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, indent=1))
    tmp.replace(RESULTS)


def hub_current() -> None:
    """The running hub must be running the hub code on disk. On Oct 12 the test home's hub-src was rewritten six
    minutes after its hub started, and the stale hub showed the Zips' <short> tags as words."""
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    pid = subprocess.run(["systemctl", "--user", "show", "taffy-hub", "-p", "MainPID", "--value"], capture_output=True,
                         text=True, env=env).stdout.strip()
    started = os.stat(f"/proc/{pid}").st_ctime
    src = HOME / "taffy" / "hub-src"
    newest = max(f.stat().st_ctime for f in src.rglob("*.py"))
    if newest > started:
        raise SystemExit(f"the test home's hub started before its code changed; restart it first "
                         f"(`taffy-hub restart`)")


def main() -> None:
    hub_current()
    RUNS.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else []
    done = {(r["scenario"], r["model"]) for r in results}
    hub = Hub()
    b = baseline()
    try:
        for model in MODELS:
            plan = [s for s in SCENARIOS if (not only or s["id"] in only) and (s["id"], model) not in done]
            if not plan:
                continue
            log(f"switching the test home's lead Zip to {model}")
            set_model(model, hub)
            for s in plan:
                r = run(s, model, hub, b)
                results.append(r)
                save(results)
        time.sleep(30)   # the usage log is written as each call ends; a memory review can finish a little later
        for r in results:
            r.update(usage_for(r))
            r["clean"] = r["served"] == [SERVED[r["model"]]]
        save(results)
        log("wrong model served:", [f"{r['run']} {r['served']}" for r in results if not r["clean"]])
    finally:
        log("putting the test home back on opus")
        hub.wait_idle(None)
        time.sleep(SETTLE)
        reset(b, sessions=[])
        set_model("opus", hub)
        with hub_db() as c:   # the lead's own chat from before the test, as it was
            c.execute("INSERT OR REPLACE INTO bot_sessions (bot_id, session_id) VALUES (?, ?)", (BOT, b["session"]))
            c.commit()
        hub.unpair()
        log("done")


if __name__ == "__main__":
    main()
