"""Delete Everything: removes the person's data that the hub itself owns (the app's Settings, Delete Everything).

What goes:
- the hub's database rows about the person: the activity feed, chat session links, tapbacks, Zero's questions,
  team notes and teammate messages, and the settings that hold the person's words or answers;
- the hub's own folders of the person's things: saved calls, pictures, briefings, the calendar copy, the outbox
  and the saved name;
- the About entry the hub wrote into each Zip's USER.md (only the text between the hub's own markers), and the
  team notes file the hub keeps in step with the database;
- the saved logins in each Zip's vault (the same removal Settings, Saved Logins uses);
- the accounts added in the app: each Google sign-in is revoked at Google and its tokens forgotten, and the
  mailboxes and calendars added with a password or a private address lose their saved password, address and
  himalaya entry (only the entries between the hub's own `# zero:begin` and `# zero:end` lines) and leave
  home.json. Mailboxes set up by hand outside the app stay.

What stays, on purpose:
- Hermes's own files: its sessions database, SOUL.md, profiles, skills, config, and anything a Zip wrote into its
  own memory. The hub has no safe way to rewrite those, so it only detaches them: the chat session links are
  cleared, so the app starts new conversations;
- the hub's pairing list, its internal key, its health and cursor settings and the "Save Call Transcripts" switch;
- the Zips themselves, their names and looks, and the sign-in permission rules kept by the taffy-tools plugin;
- anything outside the hub's own folder and the files named above.
"""

import logging
import shutil
import sqlite3
from pathlib import Path

from . import calllog, config, logins, onboarding, store, team

log = logging.getLogger("taffy.erase")

# Folders and files inside the hub's own folder (config.HUB_DIR) that hold the person's things.
HUB_FOLDERS = ("calls", "media", "briefings", "outbox")
HUB_FILES = ("person.json", "calendar.json", "trello.json", "codex-app-server.log", "codex-app-server.log.1")
# Hub settings that hold the person's words, answers or choices. Cursors and health state are left alone, because
# clearing them would make the hub re-announce old work.
PERSONAL_SETTINGS = ("onboarding", "onboarding_ack", "muted_bots", "snoozed", "held_pushes", "pending_updates",
                     "proactivity_brief_lines", "proactivity_mail_sig", "proactivity_brief_day")
# Tables that hold the person's data. Created lazily by their modules, so each is checked before use.
TABLES = ("feed", "bot_sessions", "reactions", "notices", "notice_log", "team_notes", "team_msgs", "team_seen",
          "team_cursor", "facts", "fact_shown")


class Busy(Exception):
    """A Zip is answering right now."""


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _remove(path: Path) -> int:
    """Removes a file, a folder or a link to one (the link only, never what it points to). Returns files removed."""
    if path.is_symlink():
        path.unlink()
        return 1
    if path.is_dir():
        count = sum(1 for p in path.rglob("*") if p.is_file())
        shutil.rmtree(path, ignore_errors=True)
        return count
    if path.exists():
        path.unlink()
        return 1
    return 0


def strip_about(text: str) -> str | None:
    """The USER.md text without the hub's About entry, or None when it has none. Hermes's own entries stay."""
    start, end = onboarding.ABOUT_START, onboarding.ABOUT_END
    if start not in text or end not in text or text.index(start) > text.rindex(end):
        return None
    before, after = text[:text.index(start)], text[text.rindex(end) + len(end):]
    delim = onboarding.ENTRY_DELIMITER
    after = after.lstrip("\n")
    if before.endswith(delim):
        before = before[:-len(delim)]
    if after.startswith(delim.strip("\n")):
        after = after[len(delim.strip("\n")):].lstrip("\n")
    out = before + delim + after if before.strip() and after.strip() else before + after
    return out.strip("\n") + "\n" if out.strip() else ""


def _clear_about() -> int:
    cleared = 0
    for path in onboarding._user_files():
        try:
            text = path.read_text()
        except OSError:
            continue
        new = strip_about(text)
        if new is None:
            continue
        try:
            path.write_text(new)
            cleared += 1
        except OSError:
            log.warning("could not clear the About entry in %s", path)
    return cleared


def _clear_logins() -> int:
    removed = 0
    try:
        rows = logins.list_all()
    except Exception:
        return 0
    for row in rows:
        try:
            if logins.remove(row["bot"], row["id"]):
                removed += 1
        except Exception:
            log.warning("could not remove a saved login for %s", row.get("bot"))
    return removed


def _clear_accounts() -> int:
    """The mailboxes, calendars and Google sign-ins added in the app. Returns how many went."""
    from . import google, home, mailboxes
    gone = 0
    try:
        managed = set()
        try:
            text = (mailboxes.himalaya_dir() / "config.toml").read_text()
            import re
            managed = set(re.findall(r"(?m)^# zero:begin (\S+)$", text))
        except OSError:
            pass
        for m in list(home.mail_accounts()):
            if home.is_google(m) or m["id"] in managed:
                mailboxes.remove_mailbox(m["id"])
                gone += 1
        for c in list(home.calendars()):
            mailboxes.remove_calendar(c["id"])
            gone += 1
        for address in google.accounts():
            if google.disconnect(address):
                gone += 1
    except Exception:
        log.warning("could not remove every account added in the app", exc_info=True)
    return gone


def delete_everything(device_token: str | None = None) -> dict:
    """Runs the whole removal and returns counts of what went. Raises Busy while any Zip is answering."""
    from . import turns
    if turns.busy_bots():
        raise Busy()
    removed: dict[str, int] = {}
    with store._lock:
        conn = store.db()
        existing = _tables(conn)
        for table in TABLES:
            if table in existing:
                cur = conn.execute(f"DELETE FROM {table}")
                if table in ("feed", "team_notes", "facts"):
                    removed[table] = cur.rowcount
        for name in PERSONAL_SETTINGS:
            conn.execute("DELETE FROM settings WHERE name = ?", (name,))
        if device_token:
            conn.execute("DELETE FROM push_tokens WHERE device_hash = ?", (store._hash(device_token),))
        conn.commit()
    hub = config.HUB_DIR
    for name in HUB_FOLDERS:
        removed[name] = _remove(hub / name)
    for name in HUB_FILES:
        _remove(hub / name)
    calllog._open.clear()
    removed["profile_notes"] = _clear_about()
    team._write_notes_file()   # the file the Zips read, now empty
    removed["saved_logins"] = _clear_logins()
    removed["accounts"] = _clear_accounts()
    log.info("everything deleted at the person's request: %s", removed)
    return {"ok": True, "removed": removed}
