"""Every Zip's long-lived chats follow its SOUL.md (Oct 9).

Hermes builds a session's system prompt on its first turn and stores it (state.db, sessions.system_prompt_hash).
Every later turn reuses those exact bytes so the prompt cache keeps working. The hub keeps one session per chat
for days (Colin's chat with each Zip, and its team, prep and quiet lanes), so a SOUL.md change never reached them:
Zero's chat still ran on the Oct 7 prompt that called it Taffy, without the length rule, the US units line or
"When to sign in".

The fix uses Hermes's own repair path. `hermes sessions repair-prompts <id> --apply --json` clears a session's
stored prompt; the next turn in that session rebuilds the prompt from the current SOUL.md, memory and skills,
and stores it again, so the cache breaks once per change and holds after that. The session, its messages and
everything the app shows stay as they are, and the Zip keeps its whole working context.

The hub checks every chat when SOUL.md files may have changed (each team sync, every two minutes). A chat is
out of date when a line of its Zip's SOUL.md is missing from the stored prompt. A chat is cleared at most once
per SOUL.md version, so a line Hermes stores differently can never make it rebuild every turn.
"""

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
from pathlib import Path

from . import config, store

log = logging.getLogger("taffy.freshness")

SETTING = "chat_prompts"   # session key -> {"sid": Hermes session, "soul": sha256 of the SOUL.md it was checked against}
MIN_LINE = 8               # shorter lines ("---", "## Tone") prove nothing about which version a prompt holds


def home(bot_id: str) -> Path:
    return config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id


def soul_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if len(line.strip()) >= MIN_LINE]


def missing_lines(prompt: str, soul: str) -> list[str]:
    """The SOUL.md lines a stored prompt does not have."""
    return [line for line in soul_lines(soul) if line not in prompt]


def stored_prompt(bot_id: str, session_id: str) -> tuple[str, str | None] | None:
    """(the session Hermes is really using, its stored prompt or None while none is stored). A compression moves a
    conversation to a child session, which holds the current prompt. None when Hermes has no such session."""
    db_path = home(bot_id) / "state.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        if not conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
            return None
        sid, seen = session_id, {session_id}
        while True:
            row = conn.execute(
                "SELECT c.id FROM sessions c JOIN sessions p ON p.id = c.parent_session_id "
                "WHERE c.parent_session_id = ? AND p.end_reason = 'compression' ORDER BY c.started_at DESC LIMIT 1",
                (sid,)).fetchone()
            if not row or row[0] in seen:
                break
            sid = row[0]
            seen.add(sid)
        row = conn.execute(
            "SELECT COALESCE(sp.prompt, s.system_prompt) FROM sessions s "
            "LEFT JOIN system_prompts sp ON sp.hash = s.system_prompt_hash WHERE s.id = ?", (sid,)).fetchone()
        return sid, (row[0] or None) if row else None
    finally:
        conn.close()


def clear_prompt(bot_id: str, session_id: str) -> bool:
    """Hermes's repair command for one session: its next turn rebuilds the prompt and stores it again."""
    cmd = ([str(config.HERMES_BIN)] + ([] if bot_id == "default" else ["-p", bot_id])
           + ["sessions", "repair-prompts", session_id, "--apply", "--json"])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90,
                           env=dict(os.environ, HERMES_HOME=str(config.HERMES_HOME)))
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("could not refresh %s's chat %s: %s", bot_id, session_id, type(e).__name__)
        return False
    out = p.stdout or ""
    try:
        cleared = json.loads(out[out.index("{"):]).get("cleared") or []
    except ValueError:
        cleared = []
    if p.returncode != 0 or session_id not in cleared:
        log.warning("could not refresh %s's chat %s (exit %s): %s", bot_id, session_id, p.returncode,
                    ((p.stderr or "") + out).strip()[-300:])
        return False
    return True


def _soul(bot_id: str) -> str | None:
    try:
        return (home(bot_id) / "SOUL.md").read_text()
    except OSError:
        return None


def check(recheck: bool = False, everything: bool = False) -> list[str]:
    """Refreshes the chats whose stored prompt misses a line of their Zip's current SOUL.md, and returns their
    session keys ("school", "school#team"). A chat already checked against this SOUL.md is skipped unless
    `recheck`. `everything` refreshes every chat that has a stored prompt, current or not."""
    records = dict(store.get_setting(SETTING, {}) or {})
    rows = store.db().execute("SELECT bot_id, session_id FROM bot_sessions").fetchall()
    refreshed = []
    for row in rows:
        key, sid = row["bot_id"], row["session_id"]
        bot_id = key.split("#", 1)[0]
        if not sid or bot_id not in config.BOTS_BY_ID:
            continue
        soul = _soul(bot_id)
        if soul is None:
            continue
        version = hashlib.sha256(soul.encode()).hexdigest()
        done = {"sid": sid, "soul": version}
        if records.get(key) == done and not (recheck or everything):
            continue
        try:
            found = stored_prompt(bot_id, sid)
        except sqlite3.Error as e:
            log.warning("could not read %s's chat %s: %s", bot_id, sid, e)
            continue
        if found is None:
            continue   # Hermes lost it; the hub starts a new session on the next message
        tip, prompt = found
        if prompt is None or not (everything or missing_lines(prompt, soul)):
            records[key] = done   # built from this SOUL.md already, or built fresh on its next turn
            continue
        if clear_prompt(bot_id, tip):
            refreshed.append(key)
            records[key] = done
    store.set_setting(SETTING, records)
    if refreshed:
        log.info("chats refreshed for new instructions: %s", ", ".join(refreshed))
    return refreshed
