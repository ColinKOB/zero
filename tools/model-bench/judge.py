"""Blind grading for the Opus against Sol runs: per task, all six replies in random order with no model names, graded
against the answer key by Opus through Claude Code. Writes ~/bench/grades.json (the judge's view) and prints a table.

Usage: python3 judge.py results.jsonl usage-YYYY-MM-DD.jsonl [more usage files]
"""
import json
import random
import subprocess
import sys
from collections import defaultdict

KEYS = {
    "t01-school": "Correct list, in date order: Tue Oct 13 11:55 PM POL 101 Form J reflection; Wed Oct 14 11:55 PM BUS 305 "
                  "Unit 5 paper (extended); Thu Oct 15 11:55 PM BUS 305 discussion post; Fri Oct 16 11:55 PM POL 101 Unit 7 "
                  "video response; Sun Oct 18 11:55 PM BUS 305 Unit 6 quiz, Viscaffe case study and two discussion replies. "
                  "Missing any item or a wrong date is wrong; calendar events (shifts, dentist) are not assignments.",
    "t02-mail": "Needs action: Dana (confirm Nov 3 start by end of day Monday Oct 12); Lakeside Dental (confirm the Wed Oct 14 "
                "3 PM cleaning before Monday noon or it is released); City Power ($84.20 due Oct 23, no autopay). Can wait: "
                "Priya's coffee invite. Ignore: the 40% off deals email.",
    "t03-draft": "A draft saved to reply.txt, to Dana, saying we can start on November 3, plain and polite. Nothing sent.",
    "t04-store": "SK hynix Gold P31 1TB at DiskDepot for $52.49. (Teamgroup $44.99 is backordered and WD Black $49.99 and "
                 "WD Blue $51.99 are unavailable, so they do not count.)",
    "t05-money": "$113.38 on dining in September 2026; Chick-fil-A was the most ($49.87 over four visits). The Oct 1 "
                 "Chick-fil-A charge is not September.",
    "t06-pdf": "Project 1 proposal Fri Oct 16, 2026, 5 p.m.; Project 1 final report Mon Nov 2, 9 a.m.; Project 2 report Fri "
               "Dec 4, 5 p.m.; Final exam Wed Dec 16, 1 to 3 p.m., Room 204.",
    "t07-code": "The objective check decides: tests_pass and tests_unchanged both true is correct. The explanation should "
                "say the equal shares were rounded so they did not add up to the total (the leftover cent).",
    "t08-routine": "A scheduled job created for weekdays at 7:30 a.m. Eastern (for example cron 30 7 * * 1-5), whose prompt "
                   "summarizes that day's calendar in one line. Check jobs_created.",
    "t09-signin": "The site cannot be reached or needs a sign-in the Zip does not have. Correct: says so plainly and early, "
                  "asks for what it needs (a sign-in or the statement file), and never invents a balance.",
    "t10-notes": "Two one-hour times between Mon Oct 12 and Fri Oct 16, none before 10 a.m., none on Thursday Oct 15, not "
                 "during the Tue Oct 13 9 to 5 shift or the Wed Oct 14 3 to 4 dentist visit; mentioning a Main Street spot "
                 "is a plus.",
}

PROMPT = """You are grading an assistant's replies to one task. Grade each reply against the answer key only.
Grades: "correct" (complete and right, would need no follow-up), "partial" (some right, something missing or a
follow-up needed), "wrong" (a wrong fact, an invented fact, or the task not done). Also rate how easy the reply is to
read on a phone from 1 (wall of text) to 5 (short and clear).

Task: {task}
Answer key: {key}

{runs}

Answer with JSON only: {{"grades": [{{"label": "A", "grade": "correct|partial|wrong", "readable": 1-5, "why": "one sentence"}}, ...]}}"""


def weighted(u):
    return 0.1 * u["read"] + 2 * u["write"] + u["uncached"] + 5 * (u["out"] + u.get("reasoning", 0))


def main():
    results = [json.loads(l) for l in open(sys.argv[1])]
    usage = defaultdict(lambda: {"read": 0, "write": 0, "uncached": 0, "out": 0, "reasoning": 0, "calls": 0})
    for f in sys.argv[2:]:
        for l in open(f):
            r = json.loads(l)
            if "session" in r:
                u = usage[r["session"]]
                for k in ("read", "write", "uncached", "out", "reasoning"):
                    u[k] += int(r.get(k) or 0)
                u["calls"] += 1
                if r.get("origin") == "cli":
                    u.setdefault("models", set()).add(r["model"])
    from run import TASKS
    graded = []
    for task in sorted({r["task"] for r in results}):
        runs = [r for r in results if r["task"] == task]
        random.shuffle(runs)
        labels = {chr(65 + i): r for i, r in enumerate(runs)}
        body = "\n\n".join(f"Reply {lab}:\n{r['reply'][:3500]}\nChecks: {json.dumps(r['checks'])[:1500]}"
                           for lab, r in labels.items())
        p = subprocess.run(["claude", "-p", "--model", "opus", "--tools", "", "--output-format", "json"],
                           input=PROMPT.format(task=TASKS[task].format(d="(the task folder)"), key=KEYS[task], runs=body),
                           capture_output=True, text=True, timeout=600)
        text = json.loads(p.stdout.strip().splitlines()[-1]).get("result", "")
        text = text[text.find("{"): text.rfind("}") + 1]
        for g in json.loads(text)["grades"]:
            r = labels[g["label"]]
            served = sorted(usage[r["session"]].get("models", set()))
            graded.append({**g, "task": task, "model": r["model"], "secs": r["secs"], "served": served,
                           "clean": served == [{"opus": "opus", "sol": "gpt-6.1-sol"}[r["model"]]],
                           "weighted": round(weighted(usage[r["session"]])), "calls": usage[r["session"]]["calls"],
                           "usage": usage[r["session"]]})
    for x in graded:
        x["usage"] = {k: v for k, v in x["usage"].items() if k != "models"}
    json.dump(graded, open("grades.json", "w"), indent=1)
    print("runs answered by the wrong model:", [f"{x['task']} {x['model']} {x['served']}" for x in graded if not x["clean"]])
    for m in ("opus", "sol"):
        g = [x for x in graded if x["model"] == m]
        score = sum({"correct": 1, "partial": 0.5, "wrong": 0}[x["grade"]] for x in g)
        print(m, f"score {score}/{len(g)}", "correct", sum(x["grade"] == "correct" for x in g),
              "partial", sum(x["grade"] == "partial" for x in g), "wrong", sum(x["grade"] == "wrong" for x in g),
              "readable", round(sum(x["readable"] for x in g) / len(g), 2),
              "median secs", sorted(x["secs"] for x in g)[len(g) // 2],
              "weighted tokens", sum(x["weighted"] for x in g))


if __name__ == "__main__":
    main()
