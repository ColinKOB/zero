"""Fable grades the blind test (Colin, Oct 12), in two separate parts.

The simulated person only saw the app's short version of a long reply, so the conversations were partly shaped by
the shortener. Colin: "Just remove the shortened answers from Fable's grading ... the shortener needs to be graded,
and so do the models as well."

models: for each scenario, Fable reads every Zip's full replies (never the short versions), the steps, the cards and
    the end product, and scores the Zips blind under the same A/B/C labels as the page (sealed.json). It is told that
    a request to resend something came from the app hiding it, not from the Zip. Writes grades.json.

shortener: for every reply the app shortened, in any results file given, Fable compares the short version with the
    full one, with the person's message before and after, and then sums up the patterns. Model names are never
    shown. Writes shortener.json next to the first results file.

    python3 tools/model-sim/grade.py models  <three-way results.json>
    python3 tools/model-sim/grade.py shortener <results.json> [<results.json> ...]

Calls Fable through `claude -p` with Claude Code's own context stripped (no tools, skills, MCP or settings): with
them, a one-word call cost $2.32 at list price; without, $0.04.
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS  # noqa: E402

FABLE = "claude-fable-5-1"
SYSTEM = "You are a careful, fair evaluator of AI assistant conversations. You answer with JSON only."


def fable(prompt: str) -> dict:
    """One Fable call; the JSON object in its answer. Asked again once when that does not parse."""
    for _ in range(2):
        p = subprocess.run(["claude", "-p", "--model", FABLE, "--tools", "", "--system-prompt", SYSTEM,
                            "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "",
                            "--output-format", "json"],
                           input=prompt, capture_output=True, text=True, timeout=1200, cwd=str(Path.home()))
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1])
            text = out.get("result", "")
            got = json.loads(text[text.find("{"): text.rfind("}") + 1])
            got["_cost"] = out.get("total_cost_usd")
            return got
        except (ValueError, IndexError):
            continue
    raise RuntimeError(f"Fable gave no JSON: {(p.stderr or p.stdout)[-400:]}")


def chat_full(rows: list[dict]) -> str:
    """The conversation with each Zip reply in full, as the Zip wrote it."""
    out = []
    for m in rows:
        if m["role"] == "user":
            out.append(("Person (tapped an option): " if m.get("tapped") else "Person: ") + m["text"])
        elif m["role"] == "zip":
            out.append("Zip: " + (m.get("full") or m["text"]))
        elif m["role"] == "steps" and m.get("steps"):
            out.append("(While it worked: " + ", ".join(m["steps"][-8:]) + f"; {round(m.get('secs') or 0)} s)")
        elif m["role"] == "card":
            opts = " ".join(f"[{o}]" for o in m.get("options") or [])
            out.append(f"(Card in the chat: {m.get('title') or ''} {m['text']} {opts})".replace("  ", " "))
        elif m["role"] == "error":
            out.append("(App: " + m["text"] + ")")
    return "\n".join(out)


# MARK: The models

MODELS_PROMPT = """Three AI assistants, Zip A, Zip B and Zip C, each had the same conversation with the same simulated
person (Sam) through a phone chat app. You do not know which models they are. Do not guess.

What the simulated person was told (the assistants could not see this):
{brief}

Below, each Zip's replies are shown in full. One thing about the app: it showed the person a shortened version of
some long replies, and the person could not open the full text. So when the person says they cannot see something,
or asks for it to be sent again, that was the app hiding it, not the Zip failing to send it. Do not hold that against
the Zip, and do not credit a Zip for avoiding it. Judge what each Zip actually wrote and did.

{sides}

Score each Zip from 1 (poor) to 5 (excellent) on:
- understanding: understood what the person wanted; asked when it truly needed to, and did not guess facts it lacked
- change: handled the change of mind or new requirement well (use null if the scenario had none)
- accuracy: nothing invented, wrong, or claimed as done when it was not
- product: the end product is complete, correct and saved where asked (null if the scenario has no end product)
- communication: leads with the point, plain and clear, the right length, a good teammate to work with

Then rank the three from best to worst overall, and say how confident you are in the ranking.

Answer with JSON only:
{{"zips": {{"A": {{"understanding": n, "change": n or null, "accuracy": n, "product": n or null, "communication": n,
"strengths": "one sentence", "problems": "one sentence, quoting a few words where it helps"}}, "B": {{...}}, "C": {{...}}}},
"ranking": ["A", "B", "C"], "confidence": "high, medium or low", "close_call": true or false,
"why": "two or three plain sentences on what decided the ranking"}}"""


def side_full(label: str, r: dict) -> str:
    out = [f"=== ZIP {label} === ({round(r['secs'])} s, {r['turns']} turns)", "", chat_full(r["transcript"]), "",
           "End product:"]
    for name, body in (r["product"] or {}).items():
        out.append(f"--- {name} ---\n" + ("(not saved)" if body is None else body[:12000]))
    if not r["product"]:
        out.append("(none expected)")
    if r.get("check"):
        out.append("Tests: " + ("pass" if r["check"]["passed"] else "fail"))
    if r.get("saved_elsewhere"):
        out.append("Also saved elsewhere: " + ", ".join(r["saved_elsewhere"]))
    return "\n".join(out)


def grade_models(results: Path) -> None:
    runs = json.loads(results.read_text())
    sealed = json.loads(results.with_name("sealed.json").read_text())
    out_file = results.with_name("grades.json")
    done = json.loads(out_file.read_text()) if out_file.exists() else {}

    def one(s):
        sides = "\n\n".join(side_full(k, next(r for r in runs if r["scenario"] == s["id"] and r["model"] == m))
                            for k, m in sorted(sealed[s["id"]].items()))
        g = fable(MODELS_PROMPT.format(brief=s["brief"], sides=sides))
        print(f"graded {s['id']}: {g.get('ranking')} ({g.get('confidence')})", flush=True)
        return s["id"], g

    todo = [s for s in SCENARIOS if s["id"] in sealed and s["id"] not in done]
    with ThreadPoolExecutor(4) as pool:
        for sid, g in pool.map(one, todo):
            done[sid] = g
            out_file.write_text(json.dumps(done, indent=1))


# MARK: The shortener

SHORT_PROMPT = """A phone chat app shows long assistant replies in a short version (written by a small model), with a
Show Full Reply button. The goal: the key point first, two or three sentences, nothing invented, nothing the person
needs taken away. It should not shorten a reply when the person asked for the full thing (a list, a plan, a draft,
the text again).

Below are the shortened replies from one conversation. For each, you see the person's message before it, the full
reply, the short version the person saw, and the person's next message.

{items}

Grade each one:
- verdict: "good" (a faithful, useful short version), "ok" (usable, with a flaw), or "bad" (misleading, or it hid
  something the person needed)
- should_shorten: false if the person had asked for the full thing, or the full reply was the thing itself (a list,
  plan or draft they wanted to read)
- key_point: true if the short version leads with the reply's main point
- kept_needed: true if everything the person needed to act on survived (questions to them, decisions, names, numbers)
- faithful: true if nothing was added, changed or turned around
- note: one plain sentence on what it did well or badly

Answer with JSON only: {{"items": [{{"n": 1, "verdict": "...", "should_shorten": true, "key_point": true,
"kept_needed": true, "faithful": true, "note": "..."}}]}}"""

SUMMARY_PROMPT = """Below are graded examples of a phone app's reply shortener: for each, the verdict and a note from
a reviewer. {n} replies in all.

{lines}

Write a short report for the app's owner on how well the shortener works. Answer with JSON only:
{{"summary": "three or four plain sentences: how often it helps, how often it hurts, and the main pattern",
"failure_patterns": ["each a plain sentence naming a repeated failure, with how many examples show it"],
"what_works": ["each a plain sentence"], "fixes": ["each a plain sentence: a concrete change to the shortener or the
rule for when it runs"]}}"""


def pairs(results: list[Path]) -> list[dict]:
    """Every shortened reply, with the messages around it, grouped by run (model names left out)."""
    runs = []
    for f in results:
        for r in json.loads(f.read_text()):
            t, items = r["transcript"], []
            for i, m in enumerate(t):
                if m["role"] == "zip" and m.get("full"):
                    before = next((x["text"] for x in reversed(t[:i]) if x["role"] == "user"), "")
                    after = next((x["text"] for x in t[i + 1:] if x["role"] == "user"), "(the conversation ended)")
                    items.append({"before": before, "full": m["full"], "short": m["text"], "after": after})
            if items:
                runs.append({"file": f.parent.name, "run": r["run"], "scenario": r["scenario"], "items": items})
    return runs


def grade_shortener(results: list[Path]) -> None:
    out_file = results[0].with_name("shortener.json")
    done = json.loads(out_file.read_text()) if out_file.exists() else {"runs": {}}

    def one(run):
        text = "\n\n".join(f"#{n}\nPerson before: {x['before']}\nFull reply:\n{x['full']}\nShort version shown:\n"
                           f"{x['short']}\nPerson next: {x['after']}" for n, x in enumerate(run["items"], 1))
        g = fable(SHORT_PROMPT.format(items=text))
        print(f"graded the shortener in {run['run']}: {[x.get('verdict') for x in g.get('items', [])]}", flush=True)
        for x, it in zip(g.get("items", []), run["items"]):
            x.update(it)
        return run["file"] + "/" + run["run"], {**run, "items": g.get("items", []), "_cost": g.get("_cost")}

    todo = [r for r in pairs(results) if r["file"] + "/" + r["run"] not in done["runs"]]
    with ThreadPoolExecutor(4) as pool:
        for key, g in pool.map(one, todo):
            done["runs"][key] = g
            out_file.write_text(json.dumps(done, indent=1))
    items = [x for r in done["runs"].values() for x in r["items"]]
    lines = "\n".join(f"- [{x.get('verdict')}; should shorten: {x.get('should_shorten')}; kept what was needed: "
                      f"{x.get('kept_needed')}; faithful: {x.get('faithful')}] {x.get('note')}" for x in items)
    done["summary"] = fable(SUMMARY_PROMPT.format(n=len(items), lines=lines))
    out_file.write_text(json.dumps(done, indent=1))
    print("shortener summary written", flush=True)


if __name__ == "__main__":
    what, files = sys.argv[1], [Path(a).resolve() for a in sys.argv[2:]]
    if what == "models":
        grade_models(files[0])
    elif what == "shortener":
        grade_shortener(files)
    else:
        raise SystemExit("models or shortener")
