"""Opus against Sol on ten Zip tasks in the test home (docs/plans/2026-10-11-opus-vs-sol-bakeoff.md).

Runs as the test home's user. Each run gets a fresh copy of the sample files and a fresh session, records the reply,
the wall time and the session id, and the objective checks (the code task's tests, the routine task's schedule).
Usage comes from the test home's usage log by session id. Results: ~/bench/results.jsonl.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOME = Path.home()
WORK = HOME / "taffy" / "work" / "default" / "bench"
FIX = HOME / "bench" / "fix"
OUT = HOME / "bench" / os.environ.get("RESULTS", "results.jsonl")
JOBS = HOME / ".taffy" / "cron" / "jobs.json"
HERMES = str(HOME / "taffy" / ".venv" / "bin" / "hermes")
MODELS = {"opus": ("claude-subscription-directsdk-experimental", "opus"), "sol": ("openai-codex", "gpt-6.1-sol")}
RUNS = int(os.environ.get("RUNS", "3"))

TASKS = {
    "t01-school": "The course pages and my calendar are in {d}/school. What's due this week (Monday October 12 to Sunday "
                  "October 18), in date order with times?",
    "t02-mail": "My recent emails are in {d}/mail as .eml files. Which ones need a reply or action from me, by when, and "
                "which can wait or be ignored?",
    "t03-draft": "Draft a reply to Dana's email in {d}/mail telling her we can start on November 3. Save the draft as "
                 "{d}/reply.txt. Do not send anything.",
    "t04-store": "I saved two store pages in {d}/store. What is the cheapest 1 TB SSD I can buy right now, and from which "
                 "store?",
    "t05-money": "From {d}/money/transactions.csv: how much did I spend on dining in September 2026, and at which "
                 "merchant did I spend the most on dining that month?",
    "t06-pdf": "Read {d}/syllabus/syllabus.pdf and list every deadline and exam with its date and time.",
    "t07-code": "The tests in {d}/repo fail (run them with: cd {d}/repo && python3 -m unittest). Fix the code so they "
                "pass without changing the tests, then tell me what was wrong.",
    "t08-routine": "Every weekday at 7:30 a.m., give me a one-line summary of that day's calendar. Set that up.",
    "t09-signin": "Download my latest statement from https://bank.sam-example.test and tell me the closing balance.",
    "t10-notes": "Using my preferences in {d}/notes/preferences.md and my calendar in {d}/school/calendar.ics, suggest "
                 "two times next week (October 12 to 16) for a one-hour coffee with Priya.",
}


def run_one(task: str, model: str, n: int) -> dict:
    rid = f"{task}-{model}-{n}-{uuid.uuid4().hex[:6]}"
    d = WORK / rid
    shutil.copytree(FIX, d)
    prompt = TASKS[task].format(d=d)
    provider, name = MODELS[model]
    before_jobs = JOBS.read_text() if JOBS.exists() else None
    t0 = time.time()
    p = subprocess.run([HERMES, "chat", "-q", prompt, "--oneshot", "-Q", "--provider", provider, "-m", name],
                       capture_output=True, text=True, timeout=900, cwd=str(HOME))
    secs = round(time.time() - t0, 1)
    out = p.stdout
    sid = next((l.split(":", 1)[1].strip() for l in (out + "\n" + p.stderr).splitlines()[::-1]
                if l.startswith("session_id:")), "")
    reply = "\n".join(l for l in out.splitlines() if not l.startswith(("session_id:", "⚠ install out of sync"))).strip()
    checks = {}
    if task == "t07-code":
        t = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=str(d / "repo"), capture_output=True, text=True)
        checks["tests_pass"] = t.returncode == 0
        checks["tests_unchanged"] = (d / "repo" / "test_split.py").read_text() == (FIX / "repo" / "test_split.py").read_text()
    if task == "t03-draft":
        f = d / "reply.txt"
        checks["draft_saved"] = f.exists()
        checks["draft"] = f.read_text()[:1500] if f.exists() else ""
    if task == "t08-routine":
        after = JOBS.read_text() if JOBS.exists() else None
        try:
            old = {j.get("id") for j in json.loads(before_jobs or '{"jobs": []}').get("jobs", [])}
            new = [j for j in json.loads(after or '{"jobs": []}').get("jobs", []) if j.get("id") not in old]
        except ValueError:
            new = []
        checks["jobs_created"] = [{"name": j.get("name"), "schedule": j.get("schedule"), "tz": j.get("timezone")}
                                  for j in new]
        if before_jobs is not None:
            JOBS.write_text(before_jobs)
        elif JOBS.exists():
            JOBS.unlink()
    return {"run": rid, "task": task, "model": model, "n": n, "secs": secs, "session": sid, "exit": p.returncode,
            "reply": reply[-6000:], "checks": checks, "stderr": p.stderr[-800:] if p.returncode else ""}


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    plan = [(t, m, n) for t in TASKS for m in MODELS for n in range(1, RUNS + 1)]
    random.seed(11)
    random.shuffle(plan)
    serial = [x for x in plan if x[0] == "t08-routine"]
    rest = [x for x in plan if x[0] != "t08-routine"]
    with OUT.open("a") as f:
        def save(r):
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(f"{r['run']}: {r['secs']} s exit {r['exit']}", flush=True)
        with ThreadPoolExecutor(3) as pool:
            for r in pool.map(lambda x: run_one(*x), rest):
                save(r)
        for x in serial:
            save(run_one(*x))
    print("done", flush=True)


if __name__ == "__main__":
    main()
