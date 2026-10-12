"""Runs every scenario once with each model in the test home, with a simulated user answering the Zip, and writes
~/bench/sim/results.json for the blind page (tools/model-sim/view.py).

Runs as the test home's user with the test home's environment (tools/model-bench/env.sh), like the bench:
    . ~/bench/env.sh && python3 ~/bench/sim/sim.py
Each run gets a fresh copy of the sample files and its own Hermes session, continued with --resume each turn. The
simulated user is Sonnet through Claude Code (the plan, not the API); it sees only the conversation and its brief.
Usage comes from the test home's usage log, matched by session id.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS  # noqa: E402

HOME = Path.home()
WORK = HOME / "taffy" / "work" / "default" / "sim"
FIX = HOME / "bench" / "fix"
TRIP = Path(__file__).parent / "fix-trip"
OUT = HOME / "bench" / "sim"
USAGE = HOME / "taffy" / "hub-state" / "usage"
HERMES = str(HOME / "taffy" / ".venv" / "bin" / "hermes")
MODELS = {"opus": ("claude-subscription-directsdk-experimental", "opus"), "sol": ("openai-codex", "gpt-6.1-sol")}
SERVED = {"opus": "opus", "sol": "gpt-6.1-sol"}
MAX_TURNS = 5
PRODUCT_CHARS = 40000

USER_PROMPT = """You are role-playing a person texting their personal assistant app. Stay in character.

Your brief (the assistant cannot see it):
{brief}

The conversation so far:
{transcript}

Write your next message to the assistant: short and casual, like a real text, one to three sentences. Give facts from
your brief only when the assistant asks for them or clearly needs them. If your brief says to end now, or the task is
finished, or the assistant is stuck after you have tried once to help, end the conversation.

Answer with JSON only: {{"message": "your next message, or empty if ending", "done": true or false}}"""


def zip_turn(model: str, text: str, session: str, cwd: Path) -> tuple[str, str, float, str]:
    provider, name = MODELS[model]
    cmd = [HERMES, "chat", "-q", text, "--oneshot", "-Q", "--provider", provider, "-m", name]
    if session:
        cmd += ["--resume", session]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=str(cwd))
    secs = round(time.time() - t0, 1)
    both = (p.stdout or "") + "\n" + (p.stderr or "")
    sid = next((l.split(":", 1)[1].strip() for l in both.splitlines()[::-1] if l.startswith("session_id:")), session)
    reply = "\n".join(l for l in p.stdout.splitlines() if not l.startswith(("session_id:", "⚠ install out of sync")))
    reply = re.sub(r"<short>.*?</short>", "", reply, flags=re.S).strip()
    return reply, sid, secs, (p.stderr[-600:] if p.returncode else "")


def user_turn(brief: str, transcript: list[dict]) -> dict:
    convo = "\n\n".join(f"{'You' if m['role'] == 'user' else 'Assistant'}: {m['text'][:4000]}" for m in transcript)
    p = subprocess.run(["claude", "-p", "--model", "sonnet", "--tools", "", "--output-format", "json"],
                       input=USER_PROMPT.format(brief=brief, transcript=convo), capture_output=True, text=True,
                       timeout=300)
    try:
        text = json.loads(p.stdout.strip().splitlines()[-1]).get("result", "")
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (ValueError, IndexError):
        return {"message": "", "done": True, "error": (p.stderr or p.stdout)[-300:]}


def run(scn: dict, model: str) -> dict:
    rid = f"{scn['id']}-{model}-{uuid.uuid4().hex[:6]}"
    d = WORK / rid
    shutil.copytree(FIX, d)
    shutil.copytree(TRIP, d / "trip")
    brief = scn["brief"].format(d=d)
    transcript, session, secs, errors = [], "", 0.0, []
    message = scn["open"].format(d=d)
    for turn in range(MAX_TURNS):
        transcript.append({"role": "user", "text": message})
        reply, session, s, err = zip_turn(model, message, session, HOME)
        secs += s
        transcript.append({"role": "zip", "text": reply, "secs": s})
        if err:
            errors.append(err)
        if turn == MAX_TURNS - 1:
            break
        nxt = user_turn(brief, transcript)
        if nxt.get("done") or not nxt.get("message"):
            break
        message = nxt["message"]
    product = {}
    for rel in scn["product"]:
        f = d / rel
        product[rel] = f.read_text(errors="replace")[:PRODUCT_CHARS] if f.is_file() else None
    check = None
    if scn.get("check"):
        c = subprocess.run(scn["check"].format(d=d), shell=True, capture_output=True, text=True, timeout=120)
        check = {"passed": c.returncode == 0, "output": (c.stdout + c.stderr)[-800:]}
    # Show paths relative to the run folder, so a folder name never hints at the model.
    def scrub(t):
        return t.replace(str(d), "~/files") if isinstance(t, str) else t
    for m in transcript:
        m["text"] = scrub(m["text"])
    return {"run": rid, "scenario": scn["id"], "model": model, "session": session, "secs": round(secs, 1),
            "turns": sum(m["role"] == "user" for m in transcript), "transcript": transcript,
            "product": {k: scrub(v) for k, v in product.items()}, "check": check, "errors": errors}


def usage_by_session() -> dict:
    u = defaultdict(lambda: {"read": 0, "write": 0, "uncached": 0, "out": 0, "reasoning": 0, "calls": 0, "models": set()})
    for f in sorted(USAGE.glob("*.jsonl")):
        for line in f.open():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not r.get("session"):
                continue
            x = u[r["session"]]
            for k in ("read", "write", "uncached", "out", "reasoning"):
                x[k] += int(r.get(k) or 0)
            x["calls"] += 1
            if r.get("model"):
                x["models"].add(r["model"])
    return u


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])
    plan = [(s, m) for s in SCENARIOS for m in MODELS if not only or s["id"] in only]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda x: run(*x), plan))
    for r in results:
        print(f"{r['run']}: {r['turns']} turns, {r['secs']} s, errors {len(r['errors'])}", flush=True)
    time.sleep(5)   # the usage log is written as each call ends
    usage = usage_by_session()
    for r in results:
        x = usage.get(r["session"]) or {"calls": 0, "models": set()}
        r["usage"] = {k: v for k, v in x.items() if k != "models"}
        r["served"] = sorted(x["models"])
        r["clean"] = r["served"] == [SERVED[r["model"]]]
    path = OUT / "results.json"
    old = json.loads(path.read_text()) if path.exists() and only else []
    keep = [o for o in old if o["scenario"] not in only]
    path.write_text(json.dumps(keep + results, indent=1))
    print("wrong model served:", [f"{r['run']} {r['served']}" for r in results if not r["clean"]])
    print("done", flush=True)


if __name__ == "__main__":
    main()
