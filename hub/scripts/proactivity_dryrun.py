"""Proactivity dry run on a home's live data, read-only (docs/qa/2026-10-09-proactivity-quality.md).

Runs every noticer against the hub's real mail, calendar and Trello copy, Tally and the Zips' notes, with the Haiku
checks on (the home's Claude plan), and lists what would be raised now. Nothing is posted, pushed or written outside a
fresh folder under /tmp:

- hub.db and consent.db are copied into /tmp through SQLite's backup from read-only connections, and every write
  goes to those copies. A guard refuses any SQLite connection to the live files.
- The calendar and Trello copies and the briefings are copied too; mail is read with himalaya's preview flag (it
  stays unread); chats are read only from sessions that already exist (none is created).
- No push, no prepared send (no Zip turn), no quiet look (its turn would open a Hermes session), no cache refresh.

Run on the home's server as the home's user, from a copy of hub/ (never from the deployed folder):
  ~/taffy/.venv/bin/python scripts/proactivity_dryrun.py [--home FILE] [--fresh] [--passes 6] [--judge-all] [--keep]

--home FILE   the home's settings (home.json); default: ZERO_HOME, then ~/taffy/home.json, then Colin's defaults
--fresh       as if proactivity were turned on just now: no earlier questions, an empty log, the first day's limits
--passes N    looks to simulate, ten minutes apart (default 6, one hour)
--ahead H     pretend it is H hours later (the mail and calendar copy are still today's)
--judge-all   instead of the looks, put every candidate through the judgment with no pacing
"""

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "--home" in sys.argv:   # read before the hub is imported, so every path comes from that home
    import os
    os.environ["ZERO_HOME"] = sys.argv[sys.argv.index("--home") + 1]

from taffy_hub import config  # noqa: E402

LIVE_HUB_DB = config.HUB_DB.resolve()
LIVE_CONSENT = (config.HERMES_HOME / "consent.db").resolve()
LIVE = {LIVE_HUB_DB, LIVE_CONSENT}

_connect = sqlite3.connect


def _guarded_connect(database, *a, **kw):
    """Any SQLite connection the hub opens: never one to the live files unless read-only."""
    text = str(database)
    if text.startswith("file:"):
        if "mode=ro" not in text:
            raise RuntimeError(f"dry run: refusing a writable URI connection {text}")
    elif text != ":memory:" and Path(text).resolve() in LIVE:
        raise RuntimeError(f"dry run: refusing to open the live file {text}")
    return _connect(database, *a, **kw)


sqlite3.connect = _guarded_connect


def copy_db(src: Path, dst: Path) -> None:
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    s.backup(d)
    s.close()
    d.close()


def card_lines(card: dict) -> list[str]:
    opts = " / ".join(o["text"] if isinstance(o, dict) else o for o in card.get("options") or [])
    return [f"    evidence: {card.get('evidence', '')}", f"    question: {card.get('question', '')}",
            f"    options:  {opts}"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", help="the home's settings file (home.json)")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--passes", type=int, default=6)
    ap.add_argument("--judge-all", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--ahead", type=float, default=0, help="pretend it is this many hours later")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="pq-dry-", dir="/tmp"))
    try:
        copy_db(LIVE_HUB_DB, work / "hub.db")
        if LIVE_CONSENT.exists():
            copy_db(LIVE_CONSENT, work / "consent.db")
        for name in ("calendar.json", "trello.json", "bots.json", "bot-names.json", "person.json"):
            if (config.HUB_DIR / name).exists():
                shutil.copy2(config.HUB_DIR / name, work / name)
        if (config.HUB_DIR / "briefings").is_dir():
            shutil.copytree(config.HUB_DIR / "briefings", work / "briefings")

        from taffy_hub import (briefing, calcache, consent, hermes, noticers, notices,  # noqa: F401
                               prepared, quietlook, store)
        config.HUB_DIR, config.HUB_DB = work, work / "hub.db"
        notices.CALENDAR_FILE = work / "calendar.json"
        calcache.TRELLO_FILE = work / "trello.json"
        briefing.DIR = work / "briefings"
        sys.modules["taffy_consent"].DB_PATH = work / "consent.db"
        store._local.conn = None
        store._schema_ready = False

        pushes = []

        async def no_push(title, body, collapse, feed_id, category=None):
            pushes.append(body)
            return True

        async def no_refresh(service):
            return False

        async def existing_session(bot_id, fresh=False, team=False, lane=None):
            sid = None if fresh else store.session_for(hermes.session_key(bot_id, "team" if team else lane))
            if not sid:
                raise RuntimeError("dry run: no session is created")
            return sid

        async def no_quiet():
            return []
        notices.send_push = no_push
        calcache.refresh = no_refresh
        hermes.ensure_session = existing_session
        prepared.allowed = lambda: False
        quietlook.run = no_quiet

        offset = [args.ahead * 3600]
        notices.now = lambda: time.time() + offset[0]

        db = store.db()
        s = notices.settings()
        s["enabled"] = True
        s["noticers"].pop("conflict", None)        # the lead sets the clash noticer back on after deploy
        if args.fresh:
            db.execute("DELETE FROM notices")
            db.execute("DELETE FROM notice_log")
            db.commit()
            if "enabled_at" in notices.DEFAULTS:
                s["enabled_at"] = 0
        notices._save(s)

        from taffy_hub import home
        print(f"Dry run at {notices._et().strftime('%A %b %-d, %-I:%M %p')} {home.tz_word()}"
              f"{' (fresh: as if just turned on)' if args.fresh else ''}. Work folder {work}.")
        print(f"Calendar copy: {len(notices.calendar_events())} events. Trello copy: {len(calcache.trello_cards())}.")

        src = noticers.Sources()
        wakes = {"timer", "promise"}
        cands = await noticers.find(wakes, src)
        print(f"\nCandidates the noticers hand on ({len(cands)}):")
        for c in cands:
            print(f"  [{c.noticer}] {c.subject}")
            print(f"    {notices.tidy(c.evidence) if hasattr(notices, 'tidy') else c.evidence}")

        if args.judge_all:
            s = notices.settings()
            s["haiku_daily"] = 200
            notices._save(s)
            print("\nEvery candidate through the judgment, with no pacing:")
            try:
                await notices.consider(cands, paced=False)
            except TypeError:
                await notices.consider(cands)
            for r in store.db().execute("SELECT * FROM notices ORDER BY id"):
                card = json.loads(r["card"] or "{}")
                verdict = {"open": "ASK", "preparing": "ASK", "handled": "SKIP"}.get(r["state"], r["state"])
                print(f"  {verdict:5} [{r['noticer']}] {r['subject']}")
                if r["state"] == "handled":
                    print(f"    why: {r['end_reason']}")
                elif card:
                    print("\n".join(card_lines(card)))
            return 0

        print(f"\nWhat {args.passes} looks ten minutes apart would raise:")
        for i in range(args.passes):
            offset[0] = args.ahead * 3600 + i * 600.0
            cands = await noticers.find(wakes, src)
            try:
                raised = await notices.consider(cands, paced=True)
            except TypeError:
                raised = await notices.consider(cands)
            await notices.deliver()
            for nid in raised:
                r = notices.by_id(nid)
                print(f"  look {i + 1}: [{r['noticer']}] {r['subject']}")
                print("\n".join(card_lines(json.loads(r["card"] or "{}"))))
        print("\nOutcome in the scratch copy:")
        for r in store.db().execute("SELECT noticer, subject, state, end_reason FROM notices ORDER BY id"):
            why = f" ({r['end_reason']})" if r["end_reason"] else ""
            print(f"  {r['state']:9} [{r['noticer']}] {r['subject']}{why}")
        print(f"\nLock-screen notifications that would have gone out: {pushes or 'none'}")
        log = store.db().execute("SELECT kind, COUNT(*) FROM notice_log WHERE at>=? GROUP BY kind",
                                 (notices.day_start(),)).fetchall()
        print("Log today in the copy: " + ", ".join(f"{k} {n}" for k, n in log))
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
