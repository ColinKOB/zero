"""Phase 3 measurement (docs/qa/2026-10-10-memory-facts.md): the two ways to give the call voice context, timed on the
same utterances. FTS (facts.call_context) against a store the size of a few months of use; Haiku (facts.call_refine,
low effort on the home's own plan) on the same words. Fictional data in a temporary store.

    cd hub && ZERO_HOME=/tmp/zm/eval-home.json ~/taffy/.venv/bin/python scripts/call_context_bench.py --out /tmp/zm-bench
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import briefing, config, facts  # noqa: E402
import realtime_context_probe as probe  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "harness"))
import memory_eval  # noqa: E402

UTTERANCES = [
    ("I'm thinking about taking that job with Gregg's, but I'm not sure. It could get in the way of my other passions.",
     ["gregg", "cookbook|soccer"]),
    ("Can you order new coffee for me?", ["peet"]),
    ("Should I say yes to the farmers market again next season? It's a lot of Saturdays.", ["lippitt|saturday"]),
    ("Who's my accountant again? I need to send him the receipts.", ["marcus|lee tax"]),
    ("I'm worried about money this month with the mixer and everything.", ["mixer|hobart"]),
    ("My sister wants us over on Sunday, but I have soccer.", ["soccer|ana"]),
]
PCT = lambda xs, q: round(sorted(xs)[min(len(xs) - 1, int(q * len(xs)))], 2)  # noqa: E731


def big_store() -> None:
    """The probe's facts, the eval's 40 filler facts, 300 more generated ones, and 2,000 chat messages in two Zips."""
    probe.seed()
    for i, t in enumerate(memory_eval.FILLER):
        facts.save(("default", "inbox")[i % 2], t, kind="other", limits=False)
    rnd = random.Random(3)
    nouns = ["flour", "butter", "yeast", "sugar", "boxes", "napkins", "cups", "lids", "eggs", "milk", "salt", "jam"]
    places = ["Restaurant Depot", "Costco", "Stop & Shop", "Whole Foods", "Amazon", "WebstaurantStore"]
    for i in range(300):
        facts.save("default", f"Sam bought {rnd.randint(1, 6)} cases of {rnd.choice(nouns)} from {rnd.choice(places)} "
                              f"on day {i}.", subject=rnd.choice(nouns), kind="purchase", limits=False)
    for bot in ("default", "inbox"):
        home = config.HERMES_HOME if bot == "default" else config.HERMES_HOME / "profiles" / bot
        home.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(home / "state.db")
        c.executescript("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT); CREATE TABLE messages (id INTEGER "
                        "PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL); CREATE VIRTUAL TABLE "
                        "messages_fts USING fts5(content, tool_name, tool_calls);")
        c.execute("INSERT INTO sessions VALUES ('api_x', 'api_server')")
        for j in range(1000):
            text = (f"Message {j}: please check the {rnd.choice(nouns)} order and the {rnd.choice(nouns)} invoice "
                    f"from {rnd.choice(places)} before Friday.")
            c.execute("INSERT INTO messages VALUES (?, 'api_x', ?, ?, ?)", (j + 1, ("user", "assistant")[j % 2], text,
                                                                            time.time() - j * 600))
            c.execute("INSERT INTO messages_fts (rowid, content, tool_name, tool_calls) VALUES (?, ?, '', '')",
                      (j + 1, text))
        c.commit()
        c.close()


async def main(out: Path, repeat: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    big_store()
    import re
    fts_ms, rows = [], []
    for _ in range(10):
        for u, _want in UTTERANCES:
            words = u.split()
            for n in range(4, len(words) + 1):
                t = time.perf_counter()
                facts.call_context(" ".join(words[:n]), "default", set())
                fts_ms.append((time.perf_counter() - t) * 1000)
    for u, want in UTTERANCES:
        note, _ = facts.call_context(u, "default", set())
        rows.append({"utterance": u, "via": "fts", "note": note,
                     "hits": [w for w in want if re.search(w, note, re.I)], "want": want})
    haiku = {"low": [], "medium": []}
    for effort in ("low", "medium"):
        for _ in range(repeat):
            for u, want in UTTERANCES:
                t = time.monotonic()
                try:
                    note = await facts.call_refine(u, "default",
                                                   ask=lambda p, e=effort: briefing.ask_haiku(p, timeout=30, effort=e))
                except Exception as e:
                    note = f"(failed: {e})"
                secs = time.monotonic() - t
                haiku[effort].append(secs)
                rows.append({"utterance": u, "via": f"haiku-{effort}", "secs": round(secs, 2), "note": note,
                             "hits": [w for w in want if re.search(w, note, re.I)], "want": want})
                print(json.dumps(rows[-1])[:600], flush=True)
    summary = {"facts": len(facts.current(None)), "messages": 2000,
               "fts_ms": {"n": len(fts_ms), "p50": PCT(fts_ms, .5), "p90": PCT(fts_ms, .9), "p99": PCT(fts_ms, .99),
                          "max": round(max(fts_ms), 2)},
               "fts_recall": sum(len(r["hits"]) for r in rows if r["via"] == "fts"),
               "wanted": sum(len(w) for _, w in UTTERANCES)}
    for e, xs in haiku.items():
        if xs:
            summary[f"haiku_{e}_secs"] = {"n": len(xs), "p50": PCT(xs, .5), "p90": PCT(xs, .9), "max": round(max(xs), 2),
                                          "mean": round(statistics.mean(xs), 2)}
            summary[f"haiku_{e}_recall"] = sum(len(r["hits"]) for r in rows if r["via"] == f"haiku-{e}")
    summary["wanted_per_haiku_run"] = summary["wanted"]
    (out / "bench.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/zm-bench")
    ap.add_argument("--repeat", type=int, default=2)
    a = ap.parse_args()
    asyncio.run(main(Path(a.out), a.repeat))
