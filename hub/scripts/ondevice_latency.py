"""Times On-Device Voice replies from Haiku on this computer's Claude plan, the way a call asks for them
(ondevice.Run: claude -p in the broker's shape, the history replayed, the last message asking).

    python3 hub/scripts/ondevice_latency.py [OUT.json]

For each question it starts Claude ahead of time (as a call does between turns) and also cold, and records
when the first text came, when the first sentence was complete, and every text piece with its time. The
pieces go to OUT.json (default /tmp/fv-bench/streams.json), which the Mac bench (ios/OnDeviceBench) replays
into the phone's speaking code to time the first sound. Uses a handful of Haiku turns on the plan.
"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import ondevice  # noqa: E402

BRIEF = ("You are Zero, Colin's assistant. Colin is a college student who works and runs side projects. "
         "He has a dentist appointment Thursday at 2:30 and a team call moved to 4.")
QUESTIONS = [
    "What should I have for dinner tonight?",
    "Can you remind me what I have going on Thursday?",
    "Give me a quick tip for staying focused while studying.",
    "How long should I boil an egg for a soft yolk?",
    "Can you check my email for anything from Theo?",
    "What's a good way to start a budget?",
]
SENTENCE_END = re.compile(r"[.!?](?:[\"')”’]*)\s")


async def one(question: str, warm: bool) -> dict:
    system = ondevice.prompt("default", BRIEF)
    replay = [("user", ondevice.CONNECTED), ("assistant", "Hey Colin, what's up?")]
    run = ondevice.Run(system, replay)
    t_open = time.time()
    if warm:
        await run.open()
        opened = time.time() - t_open
        await asyncio.sleep(1.0)
    t0 = time.time()
    if not warm:
        await run.open()
        opened = time.time() - t0
    pieces, text, first, sentence, tool = [], "", None, None, None
    try:
        async for ev in run.ask(question):
            now = time.time() - t0
            if ev[0] == "text":
                first = first if first is not None else now
                text += ev[1]
                pieces.append([round(now, 3), ev[1]])
                if sentence is None and SENTENCE_END.search(text + " "):
                    sentence = now
            else:
                tool = ev[1]
    finally:
        run.kill()
    return {"question": question, "warm": warm, "open_s": round(opened, 3), "first_text_s": first and round(first, 3),
            "first_sentence_s": sentence and round(sentence, 3), "total_s": round(time.time() - t0, 3),
            "tool": tool, "text": text, "pieces": pieces}


async def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fv-bench/streams.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for q in QUESTIONS:
        for warm in (True, False):
            r = await one(q, warm)
            rows.append(r)
            print(f"{'warm' if warm else 'cold'}  open {r['open_s']:.2f}s  first text {r['first_text_s']}s  "
                  f"first sentence {r['first_sentence_s']}s  total {r['total_s']:.2f}s  tool {r['tool']}  {r['text'][:70]!r}")
    out.write_text(json.dumps(rows, indent=1))

    def med(key, warm):
        vals = sorted(r[key] for r in rows if r["warm"] == warm and r[key] is not None)
        return vals[len(vals) // 2] if vals else None
    for warm in (True, False):
        print(f"median {'warm' if warm else 'cold'}: first text {med('first_text_s', warm)}s, "
              f"first sentence {med('first_sentence_s', warm)}s")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
