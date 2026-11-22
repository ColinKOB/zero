""




















import json
import re
import sys



READ = {"get", "list", "search", "read", "fetch", "find", "query", "retrieve", "describe", "view", "show", "lookup",
        "inspect", "whoami", "browse", "download", "export", "summarize", "count", "ls", "cat", "grep",
        "explain", "reader", "readers", "viewer", "lister", "getter", "fetcher", "searcher", "finder"}
READ_SUFFIX = {"reader", "readers", "viewer", "lister", "getter", "fetcher", "searcher", "finder"}
CHANGE_SUFFIX = {"updater", "writer", "editor", "creator", "manager", "mutator"}



WORDS = {
    "write": {"create", "add", "update", "edit", "set", "move", "rename", "archive", "unarchive", "close", "reopen",
              "assign", "unassign", "label", "tag", "untag", "mark", "complete", "append", "insert", "patch", "upload",
              "duplicate", "copy", "save", "draft", "attach", "transition", "change", "modify", "upsert", "write",
              "put", "import", "sort", "reorder", "star", "unstar", "pin", "unpin", "toggle", "enable", "disable",
              "restore", "resolve", "link", "unlink", "configure", "new", "make", "start", "stop", "trigger", "run",
              "convert", "replace", "updater", "writer", "editor", "creator", "manager", "mutator"},
    "send": {"send", "post", "reply", "respond", "forward", "notify", "comment", "comments", "dm", "broadcast",
             "announce", "message", "messages", "email", "mail", "schedule", "scheduled", "ping", "mention", "tweet",
             "sms"},
    "publish": {"publish", "unpublish", "deploy", "redeploy", "release", "merge", "push", "rollback", "roll", "revert",
                "promote", "build", "commit", "golive", "pull"},
    "delete": {"delete", "remove", "trash", "destroy", "purge", "erase", "clear", "empty", "drop", "wipe", "cancel",
               "uninstall"},
    "account": {"share", "unshare", "invite", "permission", "permissions", "collaborator", "collaborators", "access",
                "grant", "revoke", "kick", "ban", "role", "roles", "member", "members", "visibility", "ownership",
                "owner", "account", "accounts"},
    "pay": {"refund", "refunds", "charge", "capture", "payout", "payouts", "pay", "payment", "payments", "transfer",
            "transfers", "withdraw", "withdrawal", "deposit", "invoice", "invoices", "subscription", "subscriptions",
            "checkout", "purchase", "buy", "billing", "finalize", "void", "dispute", "disputes", "credit", "balance",
            "donate", "donation", "wire", "money", "funds"},
}

_KIND_OF = {}
for _kind, _words in WORDS.items():
    for _w in _words:
        _KIND_OF.setdefault(_w, set()).add(_kind)


SEND_VERBS = {"send", "post", "reply", "forward", "publish", "schedule"}

COMMIT_NOUNS = {"file", "files", "contents"}



ALLOWED = {
    "lookup": {"read"},
    "read": {"read", "unknown"},
    "write": {"read", "unknown", "write"},
    "send": {"read", "unknown", "write", "send"},
    "publish": {"read", "unknown", "write", "publish"},
    "delete": {"read", "unknown", "write", "delete"},
    "account": {"read", "unknown", "write", "account"},
}



BLOCKED = ("Zero blocked this tool: this request did not ask for it, or it has not been approved. Say what you could "
           "not do. If the request asked for a change, say so and the Zip can ask again with the change spelled out.")
MONEY = ("Zero blocked this tool: Zips never pay, refund, charge, transfer or change billing. Say what you found, so "
         "the person the Zip works for can do it.")








KNOWN_TOOLS = {
    "mcp__claude_ai_Gmail": {
        **dict.fromkeys(("search_threads", "get_thread", "get_message", "list_drafts", "get_draft", "list_labels",
                         "download_gmail_attachment"), {"read"}),
        **dict.fromkeys(("create_draft", "update_draft", "label_message", "label_thread", "unlabel_message",
                         "unlabel_thread", "update_message_labels", "create_label", "update_label", "delete_label",
                         "apply_sensitive_message_label", "apply_sensitive_thread_label", "mark_message_spam",
                         "mark_thread_spam", "unmark_message_spam", "unmark_thread_spam", "trash_message",
                         "trash_thread", "untrash_message", "untrash_thread"), {"write"}),
        "delete_draft": {"delete"},
        **dict.fromkeys(("send_message", "reply", "forward"), {"send"}),
    },
    "mcp__claude_ai_Google_Calendar": {

        **dict.fromkeys(("list_calendars", "list_events", "get_event", "search_events"), {"read"}),
        **dict.fromkeys(("create_event", "update_event"), {"write"}),
        "delete_event": {"delete"},
        "respond_to_event": {"send"},
    },
    "mcp__claude_ai_Google_Drive": {
        **dict.fromkeys(("search_files", "list_recent_files", "read_file_content", "download_file_content",
                         "get_file_metadata", "get_file_permissions", "list_file_revisions", "list_shared_drives",
                         "get_shared_drive_metadata"), {"read"}),
        **dict.fromkeys(("create_file", "copy_file", "update_file", "trash_file", "resolve_comment_thread"),
                        {"write"}),
        "add_comment_reply": {"send"},
        **dict.fromkeys(("share_file", "request_access"), {"account"}),
    },
}








STOPS_TOOLS = {
    "mcp__claude_ai_GitHub": {
        **dict.fromkeys(("add_issue_comment", "add_comment_to_pending_review", "create_issue", "update_issue",
                         "create_pull_request", "update_pull_request", "create_branch",
                         "create_pending_pull_request_review", "submit_pending_pull_request_review",
                         "create_and_submit_pull_request_review", "request_copilot_review", "add_sub_issue",
                         "reprioritize_sub_issue"), {"write"}),
        **dict.fromkeys(("merge_pull_request", "enable_auto_merge", "create_release"), {"publish"}),
    },
    "mcp__claude_ai_Linear": dict.fromkeys(("create_comment", "create_issue", "update_issue", "create_project",
                                            "update_project", "create_issue_label"), {"write"}),
    "mcp__claude_ai_Notion": dict.fromkeys(("notion-create-comment", "notion-create-pages", "notion-update-page",
                                            "notion-move-pages", "notion-duplicate-page", "notion-create-database",
                                            "notion-update-database"), {"write"}),
    "mcp__claude_ai_Slack": {**dict.fromkeys(("slack_send_message", "slack_schedule_message"), {"send"}),
                             "slack_send_message_draft": {"write"}},
}
COMMIT_BRANCH_TOOLS = ("push_files", "create_or_update_file")

PROD_BRANCH = re.compile(r"^(?:refs/heads/)?(main|master|prod|production|release|live|gh-pages|trunk)$", re.I)


def words(name: str) -> list[str]:
    ""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", name.lower()) if w]


def tool_kinds(name: str, server: str = "", stops: bool = False, tool_input: dict | None = None) -> set[str]:
    ""


    if server and name.startswith(server + "__"):
        name = name[len(server) + 2:]
    known = KNOWN_TOOLS.get(server, {}).get(name)
    if known is None and stops:
        known = STOPS_TOOLS.get(server, {}).get(name)
        if known is None and "github" in server.lower() and name in COMMIT_BRANCH_TOOLS:
            branch = str((tool_input or {}).get("branch") or "").strip() if isinstance(tool_input, dict) else ""
            if branch and not PROD_BRANCH.match(branch):
                known = {"write"}
    if known is not None:
        return set(known)
    parts = words(name)
    if not parts:
        return {"unknown"}
    if parts[-1] in READ_SUFFIX:
        return {"read"}
    first = next((p for p in parts if p in READ or p in _KIND_OF), None)
    if first is None:
        return {"unknown"}
    if first in READ:
        return {"read"}

    drafts = parts[-1] in ("draft", "drafts") and len(parts) > 1 and parts[-2] not in SEND_VERBS
    kinds = set()
    for p in parts:
        k = set(_KIND_OF.get(p, ()))

        if p in ("transfer", "transfers") and any(q in ("ownership", "owner") for q in parts):
            k.discard("pay")
        if drafts:
            k.discard("send")
        kinds |= k
    if "write" in kinds and "github" in server.lower() and COMMIT_NOUNS & set(parts):
        kinds.add("publish")
    return kinds or {"unknown"}


def allowed(name: str, kinds_asked, server: str = "", stops: bool = False, tool_input: dict | None = None) -> bool:
    ""
    if isinstance(kinds_asked, str):
        kinds_asked = [kinds_asked]
    if server and not name.startswith(server + "__"):
        return False
    kinds = tool_kinds(name, server, stops, tool_input)
    if "pay" in kinds:
        return False
    ok = set()
    for k in kinds_asked or ["read"]:
        ok |= ALLOWED.get(k, ALLOWED["read"])
    return kinds <= ok


def removes_a_tab(event: dict) -> bool:
    ""
    requests = (event.get("tool_input") or {}).get("requests")
    return any(isinstance(r, dict) and "deleteSheet" in r for r in requests or []) if isinstance(requests, list) else False







GUEST_FIELDS = ("attendees", "attendeeEmails", "addedAttendees", "addedAttendeeEmails")
NO_GUESTS = ("Zero blocked this: the request didn't name {who} as a guest, and adding a guest emails them an "
             "invitation. Make the change without guests. If they should be invited, say so, and the Zip can ask again "
             "with them as guests.")
NO_REMOVING = "Zero blocked this: removing a guest emails them. Leave the guests as they are and say what you didn't do."
QUIET_UPDATE = ("Zero blocked this: without notificationLevel NONE, Google emails every guest about the change. Call "
                "update_event again with notificationLevel set to NONE.")


def _addresses(value) -> list[str]:
    out = []
    for item in value if isinstance(value, list) else [value] if value else []:
        addr = item.get("email") if isinstance(item, dict) else item
        if str(addr or "").strip():
            out.append(str(addr).strip().lower())
    return out


def calendar_ok(name: str, tool_input: dict, guests, own=(), notify: bool = False) -> str:
    ""


    tail = re.split(r"__", name)[-1]
    if tail not in ("create_event", "update_event") or not isinstance(tool_input, dict):
        return ""
    allowed_people = {g.lower() for g in guests} | {a.lower() for a in own}
    extra = [a for f in GUEST_FIELDS for a in _addresses(tool_input.get(f)) if a not in allowed_people]
    if extra:
        return NO_GUESTS.format(who=", ".join(dict.fromkeys(extra)))
    if tail == "update_event":
        if _addresses(tool_input.get("removedAttendeeEmails")):
            return NO_REMOVING
        if not notify and str(tool_input.get("notificationLevel") or "").upper() != "NONE":
            return QUIET_UPDATE
    return ""





MAIL_SENDS = ("send_message", "reply", "forward")
MAIL_NOT_NAMED = ("Zero blocked this: the request didn't name {who} to receive this, and the card was only for the "
                  "people it named. Send to those people, or say who else should get it so the Zip can ask again.")
MAIL_UNSEEN = ("Zero blocked this: who this goes to isn't in the call, so it can't be checked against the card. Send it "
               "with to (and cc) filled in, not as a saved draft or a reply to all.")


def mail_ok(name: str, tool_input: dict, allowed_to, own=()) -> str:
    ""
    tail = re.split(r"__", name)[-1]
    if tail not in MAIL_SENDS:
        return ""
    if not isinstance(tool_input, dict) or tool_input.get("draftId") or tool_input.get("replyAll") is True:
        return MAIL_UNSEEN
    people = [a for f in ("to", "cc", "bcc") for a in _addresses(tool_input.get(f))]
    if not _addresses(tool_input.get("to")):
        return MAIL_UNSEEN
    ok = {a.lower() for a in allowed_to} | {a.lower() for a in own}
    extra = [a for a in people if a not in ok]
    return MAIL_NOT_NAMED.format(who=", ".join(dict.fromkeys(extra))) if extra else ""


def main(argv: list[str]) -> int:
    try:
        server, args = argv[1], argv[2:]
        kinds_asked = [a for a in args if a not in ("calendar", "notify", "mail", "stops")
                       and not a.startswith(("guest:", "own:", "to:"))]
        stops = "stops" in args
        event = json.loads(sys.stdin.read() or "{}")
        name = str(event.get("tool_name") or "")
        if "mail" in args:
            why = mail_ok(name, event.get("tool_input") or {}, [a[3:] for a in args if a.startswith("to:")],
                          [a[4:] for a in args if a.startswith("own:")])
            if why:
                sys.stderr.write(why + "\n")
                return 2
        if "calendar" in args:
            why = calendar_ok(name, event.get("tool_input") or {}, [a[6:] for a in args if a.startswith("guest:")],
                              [a[4:] for a in args if a.startswith("own:")], "notify" in args)
            if why:
                sys.stderr.write(why + "\n")
                return 2
        if allowed(name, kinds_asked, server, stops, event.get("tool_input")) and \
                ("delete" in kinds_asked or not removes_a_tab(event)):
            return 0
        sys.stderr.write((MONEY if "pay" in tool_kinds(name, server, stops) else BLOCKED) + "\n")
        return 2
    except Exception:
        sys.stderr.write(BLOCKED + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
