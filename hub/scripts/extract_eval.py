"""Phase 2 measurement (docs/qa/2026-10-10-memory-facts.md): the fact extractor and the order-email reader on scripted,
fictional input, with real Haiku calls on the home's Claude plan, in a temporary database (no live home is read or
written).

    cd hub && ZERO_HOME=tests/homes/stranger.json ~/taffy/.venv/bin/python scripts/extract_eval.py --out /tmp/zm-x

Prints and writes: each message, whether the code signal fired, what Haiku saved, the time each call took, and the
checks: no secrets saved, duplicates, corrections replaced, and three purchase facts from three order emails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import config, extract, facts, store, team  # noqa: E402

# (bot, words, expectation): "save:<subject words>" when a useful fact should come out, "none" when nothing should,
# "replace:<subject words>" when it corrects an earlier one.
CHATS = [
    ("default", "Grabbed coffee on the way home: the Cafe Bustelo espresso brick, the 10 ounce one, from the Stop & "
                "Shop on Hope Street. That's my usual.", "save:coffee"),
    ("default", "Can you order new coffee for me?", "none"),
    ("todo", "Add paper towels and AA batteries to my Walmart list.", "none"),
    ("inbox", "I get my contact lenses from Lens Shop and I always order by phone because they price match.",
     "save:contact lenses"),
    ("default", "My password for Square is Pepper2026!, can you log in and check sales?", "none"),
    ("default", "I'm so tired today, long day at the bakery.", "none"),
    ("default", "Quick update: on October 7 I applied for the Production Supervisor job at Narragansett Bay Foods in "
                "Cranston. The posting says $58k to $64k.", "save:job"),
    ("default", "Email Jordan Lee that the rent check goes out Monday.", "none"),
    ("todo", "Pepper eats Blue Buffalo Life Protection lamb and rice, the 30 pound bag, and I get it on Chewy "
             "autoship every 6 weeks.", "save:dog food"),
    ("default", "My card ending in 4111 2222 3333 4444 got charged twice at Target.", "none"),
    ("default", "My sister Lucia's birthday is November 3, she loves lemon cake.", "save:lucia"),
    ("default", "For oven temperatures give me Celsius, I trained in Lyon.", "save:units"),
    ("default", "Yeah the Cafe Bustelo brick from Stop & Shop, same as always.", "none"),
    ("default", "Actually, not Cafe Bustelo anymore. I switched to Lavazza Super Crema whole bean, and I buy it at "
                "Whole Foods now instead of Stop & Shop.", "replace:coffee"),
    ("inbox", "My accountant is Dana Whitfield at Whitfield & Moore, anything from her is important.",
     "save:accountant"),
    ("default", "I decided to turn down the Narragansett Bay Foods job, the bakery comes first.", "replace:job"),
    ("default", "What's the weather tomorrow?", "none"),
    ("default", "I always want drafts to be three or four sentences, no more.", "save:drafts"),
    ("default", "I booked the oven technician from Rhode Island Ovens for next Tuesday at 2.", "save:oven"),
    ("default", "The verification code is 551204, go ahead.", "none"),
]

ORDERS = [
    ("Your Whole Foods Market order", "Whole Foods Market <no-reply@wholefoods.example>",
     "Thanks for shopping with us, Sam!\nOrder #WF-88231 placed October 1, 2026.\n\n"
     "Lavazza Super Crema Whole Bean Coffee, 2.2 lb   Qty 1   $27.99\n"
     "365 Organic Oat Milk, 64 fl oz   Qty 2   $3.49 each\n\nSubtotal $34.97\nTotal $36.71"),
    ("Order confirmation: your Chewy order #4410992", "Chewy <orders@chewy.example>",
     "Hi Sam, we've received your order.\nOrder date: 09/26/2026\n\nBlue Buffalo Life Protection Formula Adult Lamb & "
     "Brown Rice Dry Dog Food, 30-lb bag x 1 ... $64.98\nAutoship: every 6 weeks\n\nOrder total: $64.98"),
    ("Thanks for your order from Lens Shop", "Lens Shop <service@1800contacts.example>",
     "Order number 77120345\nPlaced: October 3, 2026\n\nAcuvue Oasys 1-Day 90 pack, Right eye   1 box   $109.99\n"
     "Acuvue Oasys 1-Day 90 pack, Left eye    1 box   $109.99\n\nPrice match applied. Total $219.98"),
]


def _setup(tmp: Path) -> None:
    config.HUB_DB = tmp / "hub.db"
    config.HERMES_HOME = tmp / "hermes"
    config.HERMES_HOME.mkdir(parents=True, exist_ok=True)
    team.NOTES_FILE = tmp / "team-notes.md"
    config.reload_bots = lambda: False
    for b in [config.Bot("default", "Nova", "Lead", "#F2711C", "sparkles"),
              config.Bot("inbox", "Inbox", "Mail", "#06B6D4", "tray"),
              config.Bot("todo", "To-Do", "Lists", "#84CC16", "checklist")]:
        config.BOTS_BY_ID[b.id] = b
    config.BOTS[:] = [config.BOTS_BY_ID[i] for i in ("default", "inbox", "todo")]
    store._local.conn = None
    store._schema_ready = False
    facts.reset_for_tests()
    extract.HAIKU_DAILY = 1000


async def main(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    _setup(Path(tempfile.mkdtemp(prefix="zm-x-")))
    timings, rows = [], []
    real_ask = extract.ask_haiku

    async def timed(prompt):
        t = time.monotonic()
        try:
            return await real_ask(prompt)
        finally:
            timings.append(round(time.monotonic() - t, 2))
    extract.ask_haiku = timed
    for i, (bot, words, expect) in enumerate(CHATS):
        before = {f["id"] for f in facts.current(None)}
        fired = extract.signal(words)
        saved = await extract._extract(bot, words, "Okay.", f"s{i}", "", "chat") if fired else []
        after = facts.current(None)
        new = [f for f in after if f["id"] not in before]
        replaced = sorted(before - {f["id"] for f in after})
        rows.append({"i": i + 1, "bot": bot, "words": words, "expect": expect, "signal": fired,
                     "saved": [{"id": f["id"], "subject": f["subject"], "kind": f["kind"], "text": f["text"],
                                "confidence": f["confidence"], "visibility": f["visibility"]} for f in new],
                     "replaced": replaced, "haiku_secs": timings[-1] if fired and timings else None})
        print(json.dumps(rows[-1]), flush=True)
    # Order emails, through the same path the hub uses (mail.read replaced by the seeded text).
    from taffy_hub import mail
    seeded = {}
    for n, (subject, sender, body) in enumerate(ORDERS):
        seeded[str(900 + n)] = body
    real_read = mail.read
    mail.read = lambda account, folder, msg_id: seeded[msg_id]
    order_rows = []
    try:
        for n, (subject, sender, body) in enumerate(ORDERS):
            e = {"id": str(900 + n), "folder": "INBOX", "subject": subject, "from_name": sender.split(" <")[0],
                 "from_addr": sender.split("<")[1].rstrip(">"), "date": time.time() - (5 - n) * 86400}
            assert extract.is_order(subject), subject
            ids = await extract.read_order("sam", e)
            order_rows.append({"subject": subject, "facts": [facts.get(i) for i in ids],
                               "haiku_secs": timings[-1] if timings else None})
            print(json.dumps({"order": subject, "facts": [facts.get(i)["text"] for i in ids]}), flush=True)
    finally:
        mail.read = real_read
    saved_all = [s for r in rows for s in r["saved"]]
    secretish = re.compile(r"password|pepper2026|4111|551204|verification", re.I)
    secrets = [s for s in saved_all if secretish.search(s["text"])]
    expected_none = [r for r in rows if r["expect"] == "none" and r["saved"]]
    missed = [r for r in rows if r["expect"].startswith("save") and not r["saved"]]
    corrections = [r for r in rows if r["expect"].startswith("replace")]
    corrected = [r for r in corrections if r["replaced"]]
    current = facts.current(None)
    dupes = 0
    for a in current:
        for b in current:
            if a["id"] < b["id"] and a["subject"] == b["subject"] and a["kind"] == b["kind"] and \
                    facts._similar(a["text"], b["text"]) >= 0.5:
                dupes += 1
    summary = {"messages": len(rows), "signal_fired": sum(r["signal"] for r in rows),
               "facts_saved": len(saved_all), "secrets_saved": len(secrets),
               "saved_when_nothing_expected": [r["i"] for r in expected_none],
               "missed_expected": [r["i"] for r in missed],
               "corrections_replaced": f"{len(corrected)}/{len(corrections)}",
               "current_facts": len(current), "near_duplicates": dupes,
               "duplicate_rate": round(dupes / max(1, len(current)), 3),
               "order_emails": len(ORDERS), "purchase_facts": sum(len(o["facts"]) for o in order_rows),
               "orders_with_facts": sum(1 for o in order_rows if o["facts"]),
               "haiku_secs": sorted(timings), "current": [f"[{f['id']}] ({f['subject']}, {f['kind']}) {f['text']}"
                                                           for f in current]}
    (out / "extract-eval.json").write_text(json.dumps({"rows": rows, "orders": order_rows, "summary": summary},
                                                      indent=1, default=str))
    print(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/zm-x")
    asyncio.run(main(Path(ap.parse_args().out)))
