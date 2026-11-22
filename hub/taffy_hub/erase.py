""































import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path

from . import calllog, config, logins, onboarding, store, team, wallet

log = logging.getLogger("taffy.erase")







HUB_FOLDERS = ("calls", "media", "briefings", "outbox", "mac-copies", "apps", "site-icons", "cloudflare-projects",
               "service-keys", "kit-images", "model-keys")
HUB_FILES = ("person.json", "calendar.json", "trello.json", "codex-app-server.log", "codex-app-server.log.1",
             "icloud-outbox.json")




OTHER_HUB_PATHS = {
    "hub.db": "its tables are checked one by one (TABLES and KEPT_TABLES)",
    "wallet.json": "wallet.clear_all removes it once every vault copy is gone (_clear_wallet)",
    "google": "each Google sign-in's token file goes with google.disconnect (_clear_accounts)",
    "zip-mail.json": "zipmail.retire_all empties it as each Zip's address is retired (_clear_accounts)",
    "shared-sessions.key": "shared_sessions.clear removes it",
    "bots.json": "the Zips stay; _detach_team_zips drops their team fields",
    "bot-names.json": "the Zips' names stay", "bot-looks.json": "the Zips' looks stay",
    "railway-projects.json": "which Railway projects each Zip may use, a Zip setting that stays with the Zips",
    "internal-key": "the hub's internal key stays", "mac-approvers.json": "the pairing list's public keys stay",
    "mac-access.json": "the paired Mac's own answer to Ask Each Time or Use Automatically",
    "busy.json": "which Zips are working now", "deployed.json": "the deployed versions",
    "backup.json": "the last backup's health", "backup-mac.json": "the last Mac copy's health",
    "telemetry.json": "the home's anonymous id and the Share Usage choice",
    "usage": "token counts and ids per model call, no message text (usage_log.py); files age out after KEEP_DAYS",
    "claude-accounts.json": "claudeaccounts.clear removes it with the added Claude accounts (_clear_accounts)",
    "claude-accounts": "each added Claude account's own sign-in folder goes with claudeaccounts.clear",
    "icloud.json": "whether the home's own Mac can save notices to iCloud, which the Mac app tells again at each link",
}



PERSONAL_SETTINGS = ("your_info_ready", "your_info_prefilled", "work_now_seeded", "onboarding", "onboarding_ack", "muted_bots", "snoozed", "held_pushes", "pending_updates",
                     "task_chat_context_seen",
                     "proactivity_brief_lines", "proactivity_mail_sig", "proactivity_brief_day",
                     "card_notes", "board_notes", "report_notes", "kit_tap_notes", "proactivity_held_alerts", "proactivity_held_alert_subjects", "proactivity_busy_held",
                     "todo_list",
                     "zip_computer_check",
                     "zip_signin_notes",
                     "job_retries",
                     "reminder_radar",
                     "quiet_handoffs_told",
                     "late_handoffs_told",
                     "night_standing",
                     "stalled_tasks")
KEPT_SETTINGS = {
    "signin_passwords_migrated": "a migration marker; retaining it prevents treating newly restored passwords as legacy grants",
    "transient_triage_requeued": "a migration marker and a time: when waiting tasks stopped counting as blocked",
    "health": "health state", "daily_checks": "the morning self-checks' states, counts and ids, no content",
    "said_at": "when each Zip last spoke, times only",
    "chatgate_backfill": "a migration marker: older chat rows were given their fields once (chatgate.py)",
    "worknotes_seen": "each Zip's newest feed id and a time, so the work notes are not rebuilt for nothing",
    "user_md_synced": "a stamp; the About entry itself is cleared", "chat_prompts": "session ids and file hashes",
    "save_call_transcripts": "the switch stays, as the docstring says",
    "keep_replies_short": "a switch", "proactivity": "a switch and its timing choices",
    "call_voice": "a voice choice", "on_device_voice": "a voice choice", "notify_tones": "sound choices",
    "notify_kinds_off": "alert switches", "notify_off_bots": "alert switches",
    "zipmail_auto_off": "Zips whose address was removed, so it is not made again",
    "accounts_ever": "service ids", "accounts_told": "service ids", "apps_told": "app ids",
    "cron_jobs_seen": "a cursor", "cron_seen_at_cursor": "a cursor", "long_tasks_told": "a cursor",
    "lead_direct_chats_seen": "a cursor", "waits_scanned:*": "a mailbox cursor",
    "textwatch_cursor": "a cursor: the Mac's last text id that text watches read",
    "textwatch_blind": "a time: when the texts last could not be read",
    "waits_blind:*": "a time per mailbox: when it last could not be read",
    "apns_last": "the time and status code of Apple's last answer, no content",
    "facts_orders_seen": "a cursor of order emails already read",
    "private_catchup_done": "that the one-time catch-up of Nov 20 2026 ran (catchup.py); it holds counts only",
    "facts_summaries_seen": "a cursor: when each Zip's last compaction summary was written",
    "notify_buzzed": "a rate limit", "notify_haiku_count": "a rate limit",
    "facts_haiku": "a rate limit", "openloops_haiku": "a rate limit", "settled_calls": "a rate limit",
    "settled_first_sweep": "a one-time flag and its counts; the notes it closed are erased",
    "openloops_waits_erased_at": "a deletion timestamp; prevents retained waits from restoring deleted loop history",
    "proactivity_halved_said": "a flag", "proactivity_cache": "when each calendar copy was made",
    "proactivity_school_checks": "course-site check results, which expire in two days (schoolcheck.KEEP_SEC)",
    "team_needs_rechecked": "a one-time flag; the teams it covered are erased",
    "finished_lines_redone": "a one-time flag; the rows it rewrote are erased",
    "finished_lines_tidied": "a one-time flag; the rows it rewrote are erased",
    "todo_gate_first_pass": "a one-time flag; the verdicts it wrote are erased",
    "team_stalls": "milestone ids and times; the next scan drops ids whose milestones are gone",
    "got_done_recaps": "the dates of the last recaps, no content",
    "places_device": "the iPhone's location permission (always, denied...), never a place; the next sync rewrites it",
}

TABLES = ("work_meetings", "work_places", "work_meeting_visits", "place_reminders",
          "feed", "bot_sessions", "reactions", "notices", "notice_log", "notice_feedback", "team_notes", "team_msgs", "team_seen",
          "team_cursor", "undone_dismissals", "facts", "fact_shown", "teams", "team_members", "team_proposals", "team_milestones",
          "team_needs", "team_turns", "team_wakes", "ask_waiters", "mac_queue", "mac_wakes", "open_loops", "open_loop_shown", "open_loop_sessions",
          "first_lines", "short_replies", "chat_turns", "purchases", "open_loop_events", "problem_reports", "app_diagnostics",
          "open_loop_checks", "open_loop_check_runs", "signin_sharing", "right_now_shown", "standing_shown", "fact_changes", "fact_conflicts",
          "fact_nights", "work_projects", "work_sources", "work_mail_accounts", "work_mail_messages", "work_mail_budget",
          "work_user_state", "work_state_operations", "work_group_verdicts", "work_not_today", "work_now_raised",
          "work_project_lines", "todo_gate", "work_refresh_control", "work_made", "work_made_scans",
          "work_reply_drafts", "work_reply_budget", "work_notes", "review_checks", "review_check_budget", "open_turns",
          "work_text_state", "work_text_seen", "work_text_rows", "work_text_budget",
          "session_cards", "school_link", "school_deadlines", "school_courses", "school_syllabus", "school_mail_reads", "school_date_fixes", "work_project_state",
          "work_areas",
          "work_project_operations", "checklists", "phone_followups", "device_use", "walled_sites", "mac_his_jobs", "taught_logins", "codex_runs", "code_sessions",
          "x_posts", "service_key_asks", "buzz_log", "todo_lines", "reminder_mirror", "reminder_verdicts", "reminder_replaced", "your_info", "your_info_shown", "reminder_deliveries", "work_finish_checks", "finish_judge_log", "ask_titles", "review_words", "review_redrafts", "review_carried", "block_cards", "asked_again", "resume_notes",
          "read_marks", "bills", "got_done", "rule_reach", "rule_picks", "rule_topics", "night_misses", "notebook_tidy",
          "build_checks", "handoffs", "settled_claims", "signin_notes")

KEPT_TABLES = {
    "signin_revoked_passwords": "revoked handles stay denied if a failed deletion or old backup leaves a password copy",
    "signin_access": "clear() retains only deny-all tombstones so old stored copies cannot be reimported",
    "shared_sessions": "clear() replaces authentication with site and origin sign-out tombstones before deletion",
    "shared_session_applied": "clear() empties these generation receipts so closed browsers also apply the sign-out",
    "devices": "the pairing list", "pairing_codes": "the pairing list", "cursors": "cursors",
    "settings": "sorted name by name above", "push_tokens": "only the deleting phone's token goes (device_token)",
    "message_ids": "ids only", "card_calls": "ids only", "backup_replies": "hashes only",
    "mail_seen": "a mailbox cursor; clearing it hands old mail to the Zip again",
    "zipmail_seen": "a mailbox cursor; clearing it hands old mail to the Zip again",
    "zipmail_held": "addresses only, and their held mail is on disk",
    "waits": "a Zip's errands; the errand machinery keeps running through a deletion",
    "wake_notes": "a Zip's errands; the errand machinery keeps running through a deletion",
    "correspondents": "whom a Zip's own mailbox wrote to, so replies reach it",
    "text_watches": "a Zip's errands, like waits; the watch machinery keeps running through a deletion",
    "text_watch_seen": "text ids a watch already woke its Zip for; clearing it wakes the Zip for them again",
    "triggers": "routines set up for a Zip, which stay with the Zip",
    "shared_pages": "pages.take_down_all() empties it; a page whose takedown failed stays listed so a retry finishes it",
    "trigger_events": "the requests queued for those routines",
    "trigger_mail": "a mailbox cursor for mail routines; clearing it runs them again on mail they already handled",
    **{t: "the search index over facts, emptied with them by the facts_ad trigger (facts.py)"
       for t in ("facts_fts", "facts_fts_config", "facts_fts_data", "facts_fts_docsize", "facts_fts_idx")},
}

class Busy(Exception):
    ""


class Unfinished(Exception):
    ""




def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _remove(path: Path) -> int:
    ""
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
    ""
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
        rows = logins.list_all(everywhere=True)
    except Exception:
        return 0
    for row in rows:
        try:
            if logins.remove(row["bot"], row["id"]):
                removed += 1
        except Exception:
            log.warning("could not remove a saved login for %s", row.get("bot"))
    return removed


def _clear_wallet() -> tuple[int, int]:
    ""
    try:
        return wallet.clear_all()
    except Exception:
        log.warning("could not remove the wallet items", exc_info=True)
        return 0, 1


def _clear_banks() -> int:
    ""

    from . import plaid
    try:
        return plaid.clear_all()
    except Exception:
        log.warning("could not end every bank connection", exc_info=True)
        return 0


def _clear_accounts() -> tuple[int, int]:
    ""

    from . import claudeaccounts, google, home, mailboxes
    gone = left = 0
    gone += claudeaccounts.clear()
    try:
        managed = set()
        try:
            text = (mailboxes.himalaya_dir() / "config.toml").read_text()
            import re
            managed = set(re.findall(r"(?m)^# zero:begin (\S+)$", text))
        except OSError:
            pass
        for m in list(home.mail_accounts()):
            if m.get("kind") != "zip_mail" and (home.is_google(m) or m["id"] in managed):
                mailboxes.remove_mailbox(m["id"])
                gone += 1
        from . import zipmail
        retired, left = zipmail.retire_all()
        gone += retired
        for c in list(home.calendars()):
            mailboxes.remove_calendar(c["id"])
            gone += 1
        for address in google.accounts():
            if google.disconnect(address):
                gone += 1
    except Exception:
        log.warning("could not remove every account added in the app", exc_info=True)
        from . import zipmail
        left = max(left, len(zipmail.registry()))
    return gone, left


def _detach_team_zips() -> int:
    ""
    config.refuse_live_registry(config.REGISTRY)
    try:
        entries = json.loads(config.REGISTRY.read_text())
    except (OSError, ValueError):
        return 0
    if not isinstance(entries, list):
        return 0
    detached = 0
    for e in entries:
        if isinstance(e, dict) and ("team" in e or "team_name" in e):
            e.pop("team", None)
            e.pop("team_name", None)
            detached += 1
    if detached:
        tmp = config.REGISTRY.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=1))
        tmp.replace(config.REGISTRY)
        config._registry_mtime = None
        config.reload_bots()
    return detached


def delete_everything(device_token: str | None = None) -> dict:
    ""


    from . import teams, turns
    if turns.busy_bots():
        raise Busy()
    turns.drop_held_answers()
    removed: dict[str, int] = {}
    teams.stop_all_work()
    from . import openloops
    openloops.cancel_reads()
    from . import workmail
    workmail.cancel_reads()
    from . import made
    made.cancel_scans()
    from . import meetprep
    meetprep.cancel_reads()
    from . import school
    school.cancel_reads()
    from . import worktexts
    worktexts.cancel_reads()
    from . import remindermirror
    remindermirror.cancel_reads()
    from . import yourinfo
    yourinfo.cancel_writes()
    from . import privatewords
    privatewords.reset()
    from . import workstate
    workstate._finishing.clear()
    from . import asktitles, workgroups
    asktitles.cancel_writes()
    from . import reviewwords
    reviewwords.cancel_writes()
    workgroups.cancel_writes()
    from . import todogate
    todogate.cancel_writes()
    from . import ownerrules
    ownerrules.cancel_writes()
    from . import donecard
    donecard.cancel_writes()
    from . import settled
    settled.cancel_writes()
    from . import icloud
    icloud.cancel_writes()
    try:
        removed["team_zips"] = _detach_team_zips()

        removed["accounts"], left = _clear_accounts()
        removed["banks"] = _clear_banks()
        from . import pages
        removed["shared_pages"], pages_left = pages.take_down_all()
        from . import teach
        teach.forget_all()
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
            store.set_setting("openloops_waits_erased_at", time.time())
            conn.commit()
    finally:
        teams.release_work()
    hub = config.HUB_DIR
    from . import shared_sessions, signin_access
    with signin_access.lock:
        removed["shared_signins"] = shared_sessions.clear()
        signin_access.revoke_passwords()
        removed["saved_logins"] = _clear_logins()
        with store.db() as conn:
            conn.execute("UPDATE signin_access SET mode='selected',zips='[]',removed=1,use_on='anywhere',version=version+1")
    for name in HUB_FOLDERS:
        removed[name] = _remove(hub / name)
    for name in HUB_FILES:
        _remove(hub / name)
    calllog._open.clear()
    removed["profile_notes"] = _clear_about()


    removed["profile_history"] = _remove(config.HERMES_HOME / "memories" / team.USER_HISTORY)
    team._write_notes_file()
    removed["wallet_items"], wallet_left = _clear_wallet()
    log.info("everything deleted at the person's request: %s", removed)
    if wallet_left:
        raise Unfinished("Everything else is deleted, but some saved cards, addresses or IDs could not be removed "
                         "from your Zips yet. Try Delete Everything again in a minute.")
    if pages_left:
        raise Unfinished("Everything else is deleted, but some pages your Zips shared are still online. Try Delete "
                         "Everything again in a minute.")
    if left:
        raise Unfinished("Everything else is deleted, but some of your Zips' own email addresses could not be "
                         "removed yet. Try Delete Everything again in a minute.")
    return {"ok": True, "removed": removed}
