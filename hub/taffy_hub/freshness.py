""


















import hashlib
import json
import logging
import os
import sqlite3
import subprocess
from pathlib import Path

from . import config, store

log = logging.getLogger("taffy.freshness")

SETTING = "chat_prompts"
MIN_LINE = 8


def home(bot_id: str) -> Path:
    return config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id


def soul_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if len(line.strip()) >= MIN_LINE]


def missing_lines(prompt: str, soul: str) -> list[str]:
    ""
    return [line for line in soul_lines(soul) if line not in prompt]


def stored_prompt(bot_id: str, session_id: str) -> tuple[str, str | None] | None:
    ""

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
    ""
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
    ""


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
            continue
        tip, prompt = found
        previous = records.get(key) or {}
        changed = previous.get("sid") == sid and previous.get("soul") not in (None, version)
        if prompt is None or not (everything or changed or missing_lines(prompt, soul)):
            records[key] = done
            continue
        if clear_prompt(bot_id, tip):
            refreshed.append(key)
            records[key] = done
    store.set_setting(SETTING, records)
    if refreshed:
        log.info("chats refreshed for new instructions: %s", ", ".join(refreshed))
    return refreshed
