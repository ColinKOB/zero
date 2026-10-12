"""Memory eval (docs/qa/2026-10-10-memory-facts.md): does a Zip remember what the person told it, weeks later, in a
fresh conversation, without asking again?

Runs on alien as the test home's user, against that home's hub only, with fictional data about the test persona
(Sam Rivera, a baker in Providence). Never point it at Colin's or Riley's home.

    sudo -n -u zero-test python3 memory_eval.py --hub http://127.0.0.1:8652 --out /tmp/zm-eval/run1 [--only S1,S2]

Each scenario tells one Zip something in passing, the Zips' chat sessions are then replaced with new ones (as after
two weeks, when the old conversation is long gone from the model's context), and a request that depends on the fact
goes to a Zip in its new session. The reply is graded by fixed patterns (and read by a person afterwards; the JSONL
keeps every event). Per turn it records the Hermes session's cache write tokens and API calls before and after, and
at the end what each Zip saved: team notes or facts, MEMORY.md and USER.md.

Pairing: --code from `~/taffy/bin/taffy-hub pair` (as the home's user); the token goes to a mode-600 file in --out
and is never printed. --unpair at the end removes the device.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hubapi import Hub, HubError  # noqa: E402

HOME = Path.home()
HUB_DB = HOME / "taffy" / "hub-state" / "hub.db"
HERMES = HOME / ".taffy"

# Two sets of fictional values, so a second run is not answered from the first run's chats.
VALUES = {
    "a": dict(coffee="the Cafe Bustelo espresso brick, the 10 ounce one", store="the Stop & Shop on Hope Street",
              coffee_re=r"bustelo", store_re=r"stop\s*(&|and)\s*shop",
              job="the Production Supervisor job at Narragansett Bay Foods in Cranston", job_date="October 7",
              pay="$58k to $64k", job_short="that job with Narragansett Bay Foods",
              job_re=[r"production supervisor", r"58", r"64", r"cranston", r"october 7|oct\.? 7|applied"],
              goals="open a second bakery location by 2028, and I teach a sourdough class on Saturday mornings that I "
                    "really love", goals_re=[r"sourdough", r"second (bakery|location|shop|spot)", r"2028"],
              lenses="I get my contact lenses from Lens Shop, and I always order by phone because they price "
                     "match", lenses_re=r"1-?800[ -]?contacts", lenses_how_re=[r"phone|call", r"price.?match"],
              list_store="Walmart", items="paper towels, dog food for Pepper, and AA batteries",
              items_re=[r"paper towels", r"dog food", r"batter"],
              unit_pref="For oven temperatures, give me Celsius. I trained in Lyon and still think in Celsius for "
                        "baking.", unit_ask="What temperature should I bake baguettes at?",
              unit_re=r"°\s?c|celsius|\b2[3-5]\d\s?(degrees)?\s?c\b",
              new_coffee="Lavazza Super Crema whole bean", new_store="Whole Foods", new_coffee_re=r"lavazza",
              new_store_re=r"whole foods",
              dog_food="Pepper eats Blue Buffalo Life Protection lamb and rice, the 30 pound bag, and I get it on "
                       "Chewy autoship", dog_re=[r"blue buffalo", r"30"],
              accountant="My accountant is Dana Whitfield at Whitfield & Moore", acct_re=r"dana|whitfield"),
    "b": dict(coffee="Dunkin Original Blend ground, the 20 ounce bag", store="the Target in Seekonk",
              coffee_re=r"dunkin", store_re=r"target",
              job="the Kitchen Operations Manager job at Gregg's Restaurants in East Providence", job_date="October 6",
              pay="$61k to $67k", job_short="the Gregg's job",
              job_re=[r"kitchen operations", r"61", r"67", r"east providence", r"october 6|oct\.? 6|applied"],
              goals="write a bread cookbook by next fall, and I coach my niece's soccer team on Sundays, which I love",
              goals_re=[r"cookbook", r"soccer", r"niece", r"coach"],
              lenses="I get my contact lenses at Costco Optical in Seekonk, always in person, because they need my "
                     "prescription card", lenses_re=r"costco", lenses_how_re=[r"in person|in-person|store|prescription"],
              list_store="Target", items="parchment paper, olive oil, and dish soap",
              items_re=[r"parchment", r"olive oil", r"dish soap"],
              unit_pref="For recipes, give me weights in grams, not cups. I bake by weight.",
              unit_ask="How much flour goes in a standard sandwich loaf?",
              unit_re=r"\d\s?(g|grams)\b",
              new_coffee="Peet's Big Bang whole bean", new_store="Wegmans", new_coffee_re=r"peet",
              new_store_re=r"wegmans",
              dog_food="Pepper eats Purina Pro Plan salmon and rice, the 35 pound bag, from the Petco on Bald Hill Road",
              dog_re=[r"purina|pro plan", r"35"],
              accountant="My accountant is Marcus Lee at Lee Tax Group", acct_re=r"marcus|lee tax"),
    "c": dict(coffee="Death Wish ground, the 16 ounce bag", store="the Whole Foods on Waterman Street",
              coffee_re=r"death wish", store_re=r"whole foods",
              job="the Bakery Operations Supervisor job at Sweenor's Chocolates in Wakefield", job_date="October 8",
              pay="$54k to $60k", job_short="the Sweenor's job",
              job_re=[r"bakery operations", r"54", r"60", r"wakefield", r"october 8|oct\.? 8|applied"],
              goals="run a pop-up stall at WaterFire next summer, and I play cello in a community orchestra on "
                    "Thursday evenings, which I love", goals_re=[r"waterfire|pop-?up", r"cello", r"orchestra"],
              lenses="I get my contact lenses from Warby Parker online, always the 90-day Scout pack, because their "
                     "subscription is cheapest", lenses_re=r"warby", lenses_how_re=[r"online|scout|subscription|90"],
              list_store="CVS", items="toothpaste, cotton balls, and Advil",
              items_re=[r"toothpaste", r"cotton", r"advil"],
              unit_pref="For distances, give me kilometers. I run in kilometers.",
              unit_ask="How far is it from Providence to Newport?", unit_re=r"\bkm\b|kilomet",
              new_coffee="Stumptown Hair Bender whole bean", new_store="Trader Joe's", new_coffee_re=r"stumptown|hair bender",
              new_store_re=r"trader joe",
              dog_food="Pepper eats Hill's Science Diet small bites chicken, the 15 pound bag, from the PetSmart in "
                       "Seekonk", dog_re=[r"hill'?s|science diet", r"15"],
              accountant="My accountant is Rachel Okonkwo at Ocean State Tax",
              acct_re=r"rachel|okonkwo|ocean state"),
}


def scenarios(v: dict):
    """(tells, asks, correction tells, correction asks) for one set of values. A tell is ("tell", bot, text); an ask
    is ("ask", bot, text, grade) with grade {"must": [all needed], "any": [[one of each list]], "fail": [none]}."""
    tells = [
        ("S1", ("tell", "default", f"Grabbed coffee on the way home today: {v['coffee']}, from {v['store']}. That's "
                                   "my usual.")),
        ("S2", ("tell", "default", f"Quick update: on {v['job_date']} I applied for {v['job']}. The posting says "
                                   f"{v['pay']}.")),
        ("S2", ("tell", "default", f"Longer term I want to {v['goals']}.")),
        ("S3", ("tell", "inbox", f"Side note while you're in my email: {v['lenses']}.")),
        ("S4", ("tell", "todo", f"Start a {v['list_store']} list for me: {v['items']}.")),
        ("S5", ("tell", "default", v["unit_pref"])),
        ("S7", ("tell", "todo", f"FYI for any shopping: {v['dog_food']}.")),
        ("S8", ("tell", "inbox", f"{v['accountant']}, in case anything from them comes in.")),
    ]
    asks_ = [
        ("S1", ("ask", "default", "Can you order new coffee for me?",
                {"must": [v["coffee_re"], v["store_re"]],
                 "fail": [r"which (brand|kind|coffee|roast)", r"what (brand|kind|coffee)",
                          r"where do you (usually )?(buy|get)", r"what do you (usually )?(drink|buy)"]})),
        ("S1b", ("ask", "todo", "Put my usual coffee on my shopping list.",
                 {"must": [v["coffee_re"]], "fail": [r"which (brand|kind|coffee)", r"what (brand|kind) (of coffee|do you)"]})),
        ("S2", ("ask", "default", f"I'm thinking about taking {v['job_short']} but I'm not sure. It could get in the "
                                  "way of my other passions.",
                {"any": [v["job_re"], v["goals_re"]],
                 "fail": [r"r[ée]sum[ée]", r"cover letter", r"want me to (pull|look up|find|draft)"]})),
        ("S3", ("ask", "default", "I need to reorder my contacts.",
                {"must": [v["lenses_re"]], "any": [v["lenses_how_re"]],
                 "fail": [r"where do you (usually )?(get|buy|order)", r"which (company|store|site)"]})),
        ("S4", ("ask", "default", f"What's on my {v['list_store']} list?", {"must": v["items_re"]})),
        ("S5", ("ask", "default", v["unit_ask"], {"must": [v["unit_re"]]})),
        ("S7", ("ask", "default", "Can you order dog food for Pepper?",
                {"must": v["dog_re"], "fail": [r"which (brand|food|kind)", r"what (brand|food|kind) does"]})),
        ("S8", ("ask", "default", "Who's my accountant again?", {"must": [v["acct_re"]],
                                                                "fail": [r"(don't|do not) have"]})),
    ]
    corr = [("S6", ("tell", "default", f"Actually, not {v['coffee'].split(',')[0].replace('the ', '')} anymore. I "
                                       f"switched to {v['new_coffee']}, and I buy it at {v['new_store']} now.")) ]
    old = v["coffee_re"]
    corr_asks = [
        ("S6", ("ask", "default", "Order my coffee please.",
                {"must": [v["new_coffee_re"], v["new_store_re"]],
                 "fail": [old + r"(?![^.]*(no longer|anymore|instead|switched|old|before|replac|swap|remov))",
                          r"which (brand|kind)", r"where do you (usually )?(buy|get)"]})),
        ("S6b", ("ask", "todo", "Add my coffee to the shopping list.",
                 {"must": [v["new_coffee_re"]],
                  "fail": [old + r"(?![^.]*(no longer|anymore|instead|switched|old|before|replac|swap|remov))"]})),
    ]
    return tells, asks_, corr, corr_asks


FRESH_ALL = ("fresh", ["default", "inbox", "todo"])

# "Weeks of use": what a home has after a few weeks, so a new fact competes with many older ones and memory is nearly
# full (Hermes caps MEMORY.md at 4,400 characters in these homes). Fictional, about the test persona.
FILLER = [
    "Sam's bakery, Rivera Bakes, opens at 6 a.m. Tuesday to Sunday and is closed Mondays.",
    "Sam's partner Alex works as a nurse at Miriam Hospital, mostly night shifts.",
    "Pepper is a four year old beagle mix; the vet is Dr. Okafor at East Side Animal Hospital.",
    "Sam's flour comes from Miller Flour Co. with delivery every Tuesday morning.",
    "Sam's sister Lucia lives in Pawtucket and has two kids, Mateo and Ana.",
    "Sam prefers texts over calls for anything that is not urgent.",
    "Sam's farmers market booth is at Lippitt Park on Saturdays from May to November.",
    "Sam wants invoices to wholesale customers sent on the 1st and 15th of each month.",
    "Sam's landlord is Jordan Lee; rent for the bakery is due on the 1st.",
    "Sam runs three mornings a week, Tuesday, Thursday and Sunday, before opening.",
    "Sam's oven technician is Rhode Island Ovens; the deck oven was serviced in September.",
    "Sam takes Wednesdays afternoon off to do bookkeeping and ordering.",
    "Sam drinks oat milk and keeps a carton at the bakery.",
    "Sam's best friend Priya is organizing a school fair this fall.",
    "Sam dislikes long emails; drafts should be three or four sentences.",
    "Sam's wholesale customers include Coffee Exchange and Seven Stars Bakery.",
    "Sam uses Square for the bakery's card payments.",
    "Sam's birthday is March 14; Alex's is August 2.",
    "Sam wants the weekly prep list ready by Friday at noon.",
    "Sam is trying to cut back on sugar during the week.",
    "Sam's parents live in Cranston and come for dinner the first Sunday of each month.",
    "Sam's insurance agent is at Amica; the bakery policy renews in January.",
    "Sam prefers Southwest when flying and usually leaves from T.F. Green.",
    "Sam keeps the bakery's recipes in a shared Google Doc called Recipe Book.",
    "Sam wants reminders the evening before, not the morning of.",
    "Sam's car is a 2019 Subaru Outback; oil changes at Valvoline on North Main.",
    "Sam is learning Spanish on Duolingo, about ten minutes a day.",
    "Sam's dentist is Dr. Chen on Wayland Square; cleanings every six months.",
    "Sam buys butter in 36-pound cases from Restaurant Depot.",
    "Sam wants the bakery's Instagram posted three times a week.",
    "Sam's niece Ana is allergic to tree nuts, so her birthday cake must be nut free.",
    "Sam's electrician is Dave Kowalski; he redid the bakery's wiring in August.",
    "Sam's favorite restaurant for date night is Al Forno.",
    "Sam goes to bed around 9 p.m. on work nights.",
    "Sam wants every to-do list sorted by store when it is for shopping.",
    "Sam's part-time baker is Tess, who works Thursday to Saturday.",
    "Sam pays the bakery's utilities from the business checking account.",
    "Sam is saving for a new mixer, a 60-quart Hobart.",
    "Sam's knife sharpening happens monthly at Stock Culinary Goods.",
    "Sam volunteers at the Providence Community Fridge on the last Sunday of the month.",
]
ASK_WINDOW = 150   # an ask that is still working after this is stopped; its first lines already show what it knew


def profile_home(bot: str) -> Path:
    return HERMES if bot == "default" else HERMES / "profiles" / bot


def hub_db():
    c = sqlite3.connect(str(HUB_DB), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def session_of(bot: str) -> str | None:
    with hub_db() as c:
        r = c.execute("SELECT session_id FROM bot_sessions WHERE bot_id=?", (bot,)).fetchone()
    return r["session_id"] if r else None


def usage(bot: str) -> dict:
    sid = session_of(bot)
    if not sid:
        return {}
    try:
        c = sqlite3.connect(f"file:{profile_home(bot) / 'state.db'}?mode=ro", uri=True, timeout=10)
        r = c.execute("SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, api_call_count "
                      "FROM sessions WHERE id=?", (sid,)).fetchone()
    except sqlite3.Error:
        return {}
    return dict(zip(("input", "output", "cache_read", "cache_write", "api_calls"), r)) if r else {}


def fresh(bots: list[str]) -> None:
    """New chat sessions for these Zips, as after a long gap. The old sessions stay in Hermes (and in search)."""
    with hub_db() as c:
        for b in bots:
            c.execute("DELETE FROM bot_sessions WHERE bot_id=?", (b,))
        c.commit()


def stream(hub: Hub, bot: str, text: str, window: float) -> list[dict]:
    """Sends a message and records every event with its time, until done or `window` seconds."""
    req = urllib.request.Request(f"{hub.url}/api/bots/{bot}/messages", method="POST",
                                 data=json.dumps({"text": text}).encode())
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {hub.token}")
    began, events = time.time(), []
    try:
        with urllib.request.urlopen(req, timeout=window + 30) as r:
            for raw in r:
                line = raw.decode(errors="replace").strip()
                if line.startswith("data:"):
                    try:
                        ev = json.loads(line[5:])
                    except ValueError:
                        continue
                    ev["t"] = round(time.time() - began, 2)
                    events.append(ev)
                    if ev.get("type") == "done":
                        break
                if time.time() - began > window:
                    events.append({"type": "eval_stopped", "t": round(time.time() - began, 2)})
                    try:
                        hub.post(f"/api/bots/{bot}/stop")
                    except HubError:
                        pass
                    break
    except Exception as e:   # a timeout still leaves what arrived
        events.append({"type": "eval_error", "text": str(e)[:200], "t": round(time.time() - began, 2)})
    return events


def said(events: list[dict]) -> str:
    return "\n".join(str(e.get("text") or "") for e in events if e.get("type") in ("reply", "commentary", "status")
                     and e.get("text"))


def grade(text: str, g: dict) -> tuple[str, list[str]]:
    low, why = text.lower(), []
    for p in g.get("fail", []):
        if re.search(p, low):
            why.append(f"fail pattern: {p}")
    for p in g.get("must", []):
        if not re.search(p, low):
            why.append(f"missing: {p}")
    for group in g.get("any", []):
        if not any(re.search(p, low) for p in group):
            why.append(f"missing one of: {group}")
    return ("pass" if not why else "fail"), why


def saved_state() -> dict:
    out = {}
    for b in ("default", "inbox", "todo"):
        for name in ("MEMORY.md", "USER.md"):
            try:
                out[f"{b}/{name}"] = (profile_home(b) / "memories" / name).read_text()
            except OSError:
                out[f"{b}/{name}"] = ""
    with hub_db() as c:
        for table in ("team_notes", "facts"):
            try:
                out[table] = [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")]
            except sqlite3.Error:
                out[table] = None
    return out


def run_step(hub: Hub, step, log) -> dict:
    kind = step[0]
    if kind == "fresh":
        fresh(step[1])
        rec = {"step": "fresh", "bots": step[1]}
        log(rec)
        return rec
    bot, text = step[1], step[2]
    before = usage(bot)
    window = ASK_WINDOW if kind == "ask" else 240
    events = stream(hub, bot, text, window)
    time.sleep(3)   # the session's counters are written as the turn ends
    after = usage(bot)
    rec = {"step": kind, "bot": bot, "text": text, "events": events, "said": said(events),
           "secs": events[-1]["t"] if events else None,
           "usage_before": before, "usage_after": after,
           "cache_write": (after.get("cache_write", 0) - before.get("cache_write", 0)) if before and after else None,
           "api_calls": (after.get("api_calls", 0) - before.get("api_calls", 0)) if before and after else None}
    if kind == "ask":
        rec["grade"], rec["why"] = grade(rec["said"], step[3])
    log(rec)
    time.sleep(2)   # a turn still running waits in the hub's queue anyway (turns.send)
    return rec


MEM_SEP = "\n§\n"
AGE_DAYS = (3, 30)   # filler notes are dated over the last month


def age(out: Path) -> None:
    """Weeks of use: each Zip's MEMORY.md filled to about 4,000 characters and 40 older shared notes (facts when the
    facts store is there, else team notes). The files are backed up to `out` first; --restore puts them back."""
    import random
    rnd = random.Random(7)
    backup = out / "age-backup"
    backup.mkdir(exist_ok=True)
    for b in ("default", "inbox", "todo"):
        path = profile_home(b) / "memories" / "MEMORY.md"
        text = path.read_text() if path.exists() else ""
        (backup / f"{b}-MEMORY.md").write_text(text)
        entries = [e for e in text.split(MEM_SEP) if e.strip()]
        pool = FILLER[:]
        rnd.shuffle(pool)
        while pool and len(MEM_SEP.join(entries)) < 3900:
            entries.append(pool.pop())
        path.write_text(MEM_SEP.join(entries))
    now = int(time.time())
    with hub_db() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        top = {}
        for table in ("team_notes", "facts"):
            if table in tables:
                top[table] = c.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
        (backup / "tops.json").write_text(json.dumps(top))
        for i, text in enumerate(FILLER):
            ts = now - rnd.randint(AGE_DAYS[0] * 86400, AGE_DAYS[1] * 86400)
            bot = ("default", "inbox", "todo")[i % 3]
            if "facts" in tables:
                c.execute("INSERT INTO facts (subject, kind, text, bot_id, source, confidence, visibility, observed_at, "
                          "created_at) VALUES ('', 'other', ?, ?, 'zip', 'said', 'team', ?, ?)", (text, bot, ts, ts))
            else:
                c.execute("INSERT INTO team_notes (bot_id, text, created_at) VALUES (?, ?, ?)", (bot, text, ts))
        c.commit()


def restore(out: Path) -> None:
    """Undoes age(): the Zips' MEMORY.md as it was, and the filler notes gone. What the run itself saved is kept."""
    backup = out / "age-backup"
    for b in ("default", "inbox", "todo"):
        f = backup / f"{b}-MEMORY.md"
        if f.exists():
            path = profile_home(b) / "memories" / "MEMORY.md"
            now_text = path.read_text() if path.exists() else ""
            keep = [e for e in now_text.split(MEM_SEP) if e.strip() and e.strip() not in FILLER]
            path.write_text(MEM_SEP.join(keep))
    with hub_db() as c:
        for table in ("team_notes", "facts"):
            try:
                c.execute(f"DELETE FROM {table} WHERE text IN ({','.join('?' * len(FILLER))})", FILLER)
            except sqlite3.Error:
                pass
        c.commit()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="http://127.0.0.1:8652")
    ap.add_argument("--out", required=True)
    ap.add_argument("--code")
    ap.add_argument("--only", default="")
    ap.add_argument("--values", default="a", choices=sorted(VALUES))
    ap.add_argument("--aged", action="store_true", help="weeks of use: full memory and 40 older notes first")
    ap.add_argument("--restore", action="store_true", help="undo --aged for this --out and stop")
    ap.add_argument("--unpair", action="store_true")
    ap.add_argument("--asks-only", action="store_true", help="skip the tells (they ran before)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.restore:
        restore(out)
        print("restored")
        return 0
    token_file = Path(a.out).parent / "token"
    hub = Hub(a.hub, timeout=60)
    if token_file.exists():
        hub.token = token_file.read_text().strip()
    elif a.code:
        hub.pair(a.code, "Memory eval (temporary)")
        hub.save_token(token_file)
    else:
        print("no token: pass --code from taffy-hub pair")
        return 2
    if a.unpair:
        hub.unpair()
        token_file.unlink(missing_ok=True)
        print("unpaired")
        return 0
    only = {s for s in a.only.split(",") if s}
    log_path = out / "transcript.jsonl"

    def log(rec):
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
        with log_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        brief = {k: rec.get(k) for k in ("sid", "step", "bot", "grade", "secs", "cache_write", "api_calls")}
        print(json.dumps(brief), "|", (rec.get("said") or "")[:300].replace("\n", " / "), flush=True)

    tells, asks_, corr, corr_asks = scenarios(VALUES[a.values])
    want = lambda sid: not only or sid in only or sid.rstrip("b") in only  # noqa: E731
    if a.aged:
        age(out)
    (out / "saved-before.json").write_text(json.dumps(saved_state(), indent=1))
    if not a.asks_only:
        for sid, st in tells:
            if want(sid):
                run_step(hub, st, lambda r, sid=sid: log({"sid": sid, **r}))
    run_step(hub, FRESH_ALL, log)
    results = {}
    for sid, st in asks_:
        if want(sid):
            results[sid] = run_step(hub, st, lambda r, sid=sid: log({"sid": sid, **r}))
    if want("S6"):
        for sid, st in corr:
            run_step(hub, st, lambda r, sid=sid: log({"sid": sid, **r}))
        run_step(hub, FRESH_ALL, log)
        for sid, st in corr_asks:
            results[sid] = run_step(hub, st, lambda r, sid=sid: log({"sid": sid, **r}))
    (out / "saved-after.json").write_text(json.dumps(saved_state(), indent=1))
    summary = {sid: {"grade": r.get("grade"), "why": r.get("why"), "secs": r.get("secs"),
                     "cache_write": r.get("cache_write"), "api_calls": r.get("api_calls")}
               for sid, r in results.items()}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
