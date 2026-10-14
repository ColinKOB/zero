"""The run guard for connectors other than Google and Trello (Notion, Slack, Stripe, Netlify, Linear and the rest).

Their tool names are not known ahead of time, so each run of the connectors tool gets a Claude Code PreToolUse hook
for every tool of that one connector, and this file is the hook. It reads the tool's name from the hook's JSON on
stdin and lets it through only when the name says it does something this request may do (Oct 8 QA round 9, M1):

- A tool whose name starts with a reading verb ("list_refunds", "slack_read_channel", "notion-get-comments",
  "netlify-deploy-services-reader") reads, and is allowed on every run.
- A tool that names a change, a send, a publish, a delete, a share or money is allowed only when the request was
  classified as that kind. Money ("create_refund", "cancel_subscription", "finalize_invoice") is never allowed:
  Zips never move money. A send, publish, delete or share kind reaches the run only after Colin approved that exact
  request on a card, so the guard opens just the kinds that request asked for.
- A tool whose name has none of these words ("hf_fs", "dynamic_space") cannot be judged by its name and stays open,
  as it was before; the run's prompt still limits it.

This file must run on its own (python3 -I connector_guard.py <server> <kind>...), so it imports nothing from the
plugin. It exits 0 to allow and 2 to block; anything unexpected blocks. The plugin imports it too (tools.py and the
tests) for the same rules.
"""

import json
import re
import sys

# Verbs that only read. "reader", "viewer" and the like end tool names such as Netlify's
# "netlify-deploy-services-reader".
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
# A GitHub file tool commits straight to a branch, which can deploy it ("create_or_update_file").
COMMIT_NOUNS = {"file", "files", "contents"}
ALLOWED = {
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


def words(name: str) -> list[str]:
    """A tool's name as lowercase words: "slack_send_message", "notion-create-comment", "deletePage"."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", name.lower()) if w]


def tool_kinds(name: str, server: str = "") -> set[str]:
    """What a connector's tool does, judged by its name: {"read"}, {"unknown"}, or the kinds of its words."""
    if server and name.startswith(server + "__"):
        name = name[len(server) + 2:]
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
    # "slack_send_message_draft" and "create_draft" make a draft; "send_draft" sends one.
    drafts = parts[-1] in ("draft", "drafts") and len(parts) > 1 and parts[-2] not in SEND_VERBS
    kinds = set()
    for p in parts:
        k = set(_KIND_OF.get(p, ()))
        # "transfer_ownership" moves a page or a repo, not money.
        if p in ("transfer", "transfers") and any(q in ("ownership", "owner") for q in parts):
            k.discard("pay")
        if drafts:
            k.discard("send")
        kinds |= k
    if "write" in kinds and "github" in server.lower() and COMMIT_NOUNS & set(parts):
        kinds.add("publish")
    return kinds or {"unknown"}


def allowed(name: str, kinds_asked, server: str = "") -> bool:
    """Whether this run may use the tool. `kinds_asked` is the request's kind, or every kind it asked for."""
    if isinstance(kinds_asked, str):
        kinds_asked = [kinds_asked]
    if server and not name.startswith(server + "__"):
        return False
    kinds = tool_kinds(name, server)
    if "pay" in kinds:
        return False
    ok = set()
    for k in kinds_asked or ["read"]:
        ok |= ALLOWED.get(k, ALLOWED["read"])
    return kinds <= ok


def main(argv: list[str]) -> int:
    try:
        server, kinds_asked = argv[1], argv[2:]
        event = json.loads(sys.stdin.read() or "{}")
        name = str(event.get("tool_name") or "")
        if allowed(name, kinds_asked, server):
            return 0
        sys.stderr.write((MONEY if "pay" in tool_kinds(name, server) else BLOCKED) + "\n")
        return 2
    except Exception:
        sys.stderr.write(BLOCKED + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
