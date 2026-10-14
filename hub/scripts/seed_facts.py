"""One-time seeding of the facts store (docs/qa/2026-10-10-memory-facts.md, "Migration"), run by whoever deploys.

Team notes need nothing: the facts table takes them over with the same numbers the first time the new hub opens its
database (facts._from_team_notes). This script adds what the Zips kept elsewhere:

- each Zip's MEMORY.md (its own notes; Money's amounts stay Money's, facts.save decides),
- the lead's USER.md (every specialist has a copy of it, so it is read once),
- optionally notes files given with --notes (for Colin's home: the shared background documents).

Haiku reads them in small batches with the facts already saved on the same subjects, so it skips what is there and
writes one sentence per lasting fact about the person, never how a Zip does its job, tool notes, secrets or one-day
states. Run it as the home's user, with the home's own settings:

    cd ~/taffy/hub-src && ~/taffy/.venv/bin/python scripts/seed_facts.py              # dry run: a copy of hub.db
    cd ~/taffy/hub-src && ~/taffy/.venv/bin/python scripts/seed_facts.py --apply      # writes the home's hub.db

A dry run prints every fact it would save and writes them to --out; nothing in the home changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import config, extract, facts, home, store  # noqa: E402

BATCH = 12
PROMPT = """These are notes an assistant named {zip} kept about {person} and about its own work. Pull out the lasting \
facts about {person}'s life that the whole team should know weeks from now: preferences and usuals (brands, sizes, \
stores), places, purchases, supplies, people, plans, decisions, dates that matter.

Rules:
- Leave out how {zip} does its job (tools, folders, sign-ins, workarounds, settings), passwords, codes, account or card \
numbers, and anything only true for a day.
- One sentence per fact, about {person} in the third person, with every specific detail. Notes were written on or \
before {written}; write real dates, never "today".
- If a current fact below already says it, leave it out. If a note is newer and changes a current fact, give that \
fact's number in "replaces".
- "subject" is the plain thing the fact is about, one or two common words; reuse a current fact's subject when it is \
the same thing.
- "private": true only for health conditions, medications and account balances.

Current facts that may be related:
{candidates}

Notes:
{notes}

Answer with JSON only:
{{"facts": [{{"subject": "...", "kind": "preference|place|purchase|supply|person|plan|decision|other", "text": "...", \
"replaces": null, "confidence": "said", "private": false}}]}}"""


def entries(path: Path) -> list[str]:
    try:
        text = path.read_text()
    except OSError:
        return []
    out = []
    for e in text.replace("<!-- onboarding:start -->", "").replace("<!-- onboarding:end -->", "").split("§"):
        e = " ".join(e.split())
        if len(e) > 20:
            out.append(e[:1500])
    return out


def chunks(text: str, size: int = 2500) -> list[str]:
    paras, out, cur = [p.strip() for p in text.split("\n\n") if p.strip()], [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = ""
        cur += p + "\n\n"
    return out + ([cur] if cur.strip() else [])


async def seed(sources: list[tuple[str, str, list[str], float]], out: Path) -> list[dict]:
    from taffy_hub import who
    saved_all = []
    for bot_id, label, items, written in sources:
        for i in range(0, len(items), BATCH):
            batch = items[i:i + BATCH]
            notes = "\n".join(f"- {e}" for e in batch)
            cands = []
            seen = set()
            for e in batch:
                for f in facts.search(e, None, limit=3):
                    if f["id"] not in seen:
                        seen.add(f["id"])
                        cands.append(f)
            prompt = PROMPT.format(zip=label, person=who.first(), notes=notes,
                                   written=datetime.fromtimestamp(written, home.TZ).strftime("%B %-d, %Y"),
                                   candidates="\n".join(f"[{f['id']}] ({f['subject'] or 'no subject'}, {f['kind']}) "
                                                        f"{f['text']}" for f in cands[:20]) or "None.")
            try:
                answer = extract._json(await extract.ask_haiku(prompt))
            except Exception as e:
                print(f"  {label}: batch {i // BATCH + 1} failed: {e}")
                continue
            before = {f["id"] for f in facts.current(None, limit=100000)}
            ids = extract.save_answer(answer, bot_id, None, "import", {f["id"] for f in cands},
                                      observed_at=written)
            for fid in [i for i in ids if i not in before]:
                f = facts.get(fid)
                saved_all.append({"id": fid, "from": label, "subject": f["subject"], "kind": f["kind"],
                                  "text": f["text"], "visibility": f["visibility"], "replaces": None})
                print(f"  [{fid}] ({f['subject']}, {f['kind']}{', private' if f['visibility'] == 'owner' else ''}) "
                      f"{f['text']}")
    out.write_text(json.dumps(saved_all, indent=1))
    return saved_all


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the home's hub.db (default: a dry run on a copy)")
    ap.add_argument("--notes", nargs="*", default=[], help="notes files to read as well (the lead's)")
    ap.add_argument("--out", default="/tmp/seed-facts.json")
    a = ap.parse_args()
    if not a.apply:
        tmp = Path(tempfile.mkdtemp(prefix="seed-facts-"))
        if config.HUB_DB.exists():
            src = sqlite_copy(config.HUB_DB, tmp / "hub.db")
            print(f"Dry run on a copy of the home's database ({src}). Nothing in the home changes.")
        config.HUB_DB = tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False
    facts.reset_for_tests()
    extract.HAIKU_DAILY = 10_000
    config.reload_bots()
    print(f"{len(facts.current(None))} facts before (team notes are already among them).")
    sources = []
    lead_user = config.HERMES_HOME / "memories" / "USER.md"
    for b in config.BOTS:
        mem = (config.HERMES_HOME if b.id == "default" else config.HERMES_HOME / "profiles" / b.id) / "memories"
        items = entries(mem / "MEMORY.md")
        if items:
            sources.append((b.id, b.name, items, (mem / "MEMORY.md").stat().st_mtime))
    if lead_user.exists():
        lead = config.BOTS_BY_ID.get("default")
        sources.append(("default", lead.name if lead else "Zero", entries(lead_user), lead_user.stat().st_mtime))
    for f in a.notes:
        p = Path(f).expanduser()
        try:
            sources.append(("default", p.name, chunks(p.read_text())[:40], p.stat().st_mtime))
        except OSError as e:
            print(f"  {p}: not read ({e})")
    for _bot, label, items, _ in sources:
        print(f"{label}: {len(items)} notes")
    saved = asyncio.run(seed(sources, Path(a.out)))
    print(f"{len(saved)} facts {'saved' if a.apply else 'would be saved'}; {len(facts.current(None))} current now. "
          f"List: {a.out}")
    return 0


def sqlite_copy(src: Path, dest: Path) -> Path:
    """A consistent copy of a live SQLite database (the backup API, so the hub can keep running)."""
    import sqlite3
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dest)
    with d:
        s.backup(d)
    s.close()
    d.close()
    return src


if __name__ == "__main__":
    sys.exit(main())
