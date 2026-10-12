"""The person's mail envelopes for the noticers (proactivity phase 1, notices.py).

The hub runs as the home's user, so it reads mail the way the host-side `email` tool and the two watch scripts do:
himalaya on the home's server, called with a list of arguments (never a shell), with the same account names and folders. Only
envelopes are read in bulk. A message's text is read only for a candidate (a reply that may ask something, a renewal
notice with a date), with himalaya's preview flag so it stays unread. Nothing here sends, saves or changes mail, and
this module never opens himalaya's settings or password files: himalaya reads its own.

Tests replace `run`; nothing else in this module starts a program.
"""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import config, home


class _Accounts(Mapping):
    """The home's mailboxes by id -> address (home.json `mail.accounts`, the email tool's accounts), read each time
    so a mailbox added in the app counts at once."""

    def _now(self) -> dict:
        return {str(a["id"]): str(a["address"]) for a in home.mail_accounts()}

    def __getitem__(self, key):
        return self._now()[key]

    def __iter__(self):
        return iter(self._now())

    def __len__(self):
        return len(self._now())


class _Own:
    """The person's own addresses, lowercased (a message from one of them is not someone else's)."""

    def __contains__(self, addr) -> bool:
        return str(addr or "").lower() in {a.lower() for a in ACCOUNTS.values()}

    def __iter__(self):
        return iter({a.lower() for a in ACCOUNTS.values()})


ACCOUNTS = _Accounts()
OWN = _Own()


def _account(account: str) -> dict | None:
    return next((a for a in home.mail_accounts() if a["id"] == account), None)


def default_account() -> str | None:
    """The mailbox a reply goes from when nothing else says (home.json: the account marked default)."""
    accts = home.mail_accounts()
    for a in accts:
        if a.get("default"):
            return str(a["id"])
    return str(accts[0]["id"]) if accts else None


def account_for(addr: str) -> str | None:
    """The person's own mailbox at the same domain as `addr` (a school or work address), else the default one."""
    dom = (addr or "").rsplit("@", 1)[-1].lower()
    for a in home.mail_accounts():
        own = str(a["address"]).rsplit("@", 1)[-1].lower()
        if own not in PUBLIC_DOMAINS and dom and (dom == own or dom.endswith("." + own)):
            return str(a["id"])
    return default_account()


PUBLIC_DOMAINS = {"gmail.com", "googlemail.com", "icloud.com", "me.com", "mac.com", "outlook.com", "hotmail.com",
                  "live.com", "yahoo.com", "aol.com", "proton.me", "protonmail.com", "fastmail.com"}


def uses_google(account: str) -> bool:
    """A mailbox connected with Sign In with Google: read through the Gmail API with the home's token, not himalaya."""
    a = _account(account)
    return bool(a and home.is_google(a))


def all_mail_folder(account: str) -> str | None:
    """The folder that holds every message of the account (Gmail's All Mail), when the account names one."""
    for a in home.mail_accounts():
        if a["id"] == account:
            return a.get("all_mail_folder") or None
    return None
TIMEOUT_SEC = 60
READ_MAX = 6000
# The headers a message is read with: who and what, and the ones a mailing list or a bulk sender adds (is_bulk).
READ_HEADERS = ("From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted")

# Senders that are not a person: lists, receipts, notifications. A question from one of these is not a reply owed.
AUTOMATED = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|mailer|mailer-daemon|postmaster|bounces?|news|"
    r"newsletters?|marketing|info|support|help|billing|receipts?|orders?|alerts?|updates?|hello|team|admin|accounts?|"
    r"service|services|security|digest|calendar|feedback|reply|contact|store|shop|deals|offers|rewards|statements?)"
    r"(\d+)?([+._-].*)?@", re.I)   # store1234@theupsstore.com is a shop's inbox, not a person (Oct 9)
AUTOMATED_DOMAINS = re.compile(r"@(.+\.)?(mailchimp|sendgrid|substack|linkedin|facebookmail|amazonses|mandrillapp|"
                               r"hubspot|intercom|zendesk|github|google|apple|paypal|venmo|chase|walmart|amazon)\.",
                               re.I)


class MailError(RuntimeError):
    pass


def _himalaya() -> str:
    local = config.HOME / ".local" / "bin" / "himalaya"
    return str(local) if local.exists() else (shutil.which("himalaya") or "himalaya")


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("HIMALAYA_CONFIG", "RUST_LOG", "RUST_BACKTRACE", "HERMES_HOME", "XDG_CONFIG_HOME")}
    env["HOME"] = str(config.HOME)
    env["PATH"] = f"{config.HOME}/.local/bin:" + env.get("PATH", "/usr/bin:/bin")
    return env


def run(argv: list[str]) -> subprocess.CompletedProcess:
    """The one place himalaya runs for the hub. Tests replace this."""
    return subprocess.run([_himalaya(), *argv], capture_output=True, text=True, timeout=TIMEOUT_SEC,
                          cwd=str(config.HOME), env=_env())


def _people(x) -> list[tuple[str, str]]:
    """[(name, address)] from himalaya's from/to field, which is a dict, a list of dicts or plain text."""
    if not x:
        return []
    if isinstance(x, dict):
        return [(str(x.get("name") or "").strip(), str(x.get("addr") or x.get("address") or "").strip().lower())]
    if isinstance(x, list):
        return [p for i in x for p in _people(i)]
    from email.utils import getaddresses
    return [(n.strip(), a.strip().lower()) for n, a in getaddresses([str(x)])]


def when(raw) -> datetime | None:
    d = str(raw or "")
    for parse in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            dt = parse(d)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, IndexError):
            continue
    return None


def _envelope(e: dict, account: str, folder: str) -> dict | None:
    dt = when(e.get("date"))
    if dt is None or not e.get("id"):
        return None
    frm = (_people(e.get("from")) or [("", "")])[0]
    flags = e.get("flags") or []
    return {"id": str(e["id"]), "account": account, "folder": folder, "subject": str(e.get("subject") or "").strip(),
            "from_name": frm[0], "from_addr": frm[1], "to": _people(e.get("to")), "date": dt.timestamp(),
            "flags": {str(f).lower() for f in flags} if isinstance(flags, list) else set()}


def envelopes(account: str, folder: str = "INBOX", page_size: int = 50, query: list[str] | None = None) -> list[dict]:
    """The newest envelopes in one folder, as plain dicts. Raises MailError when the mailbox can't be read."""
    if account not in ACCOUNTS:
        raise MailError(f"unknown account {account}")
    if uses_google(account):
        return _gmail_envelopes(account, folder, page_size, query)
    argv = ["envelope", "list", "-a", account, "-f", folder, "-o", "json", "-p", "1", "-s", str(int(page_size))]
    if query:
        if any(w.startswith("-") for w in query):
            raise MailError("a query word cannot start with a dash")
        argv += ["--", *query]
    try:
        out = run(argv)
    except subprocess.TimeoutExpired:
        raise MailError(f"listing {account} {folder} took too long") from None
    if out.returncode != 0:
        raise MailError(f"listing {account} {folder} failed: {(out.stderr or '').strip()[-200:]}")
    raw = out.stdout or ""
    i = raw.find("[")
    try:
        items = json.loads(raw[i:]) if i >= 0 else []
    except ValueError:
        raise MailError(f"listing {account} {folder} returned something unreadable") from None
    return [e for e in (_envelope(x, account, folder) for x in items if isinstance(x, dict)) if e]


def read(account: str, folder: str, msg_id: str) -> str:
    """One message's text, without marking it read. Only called for candidates."""
    if account not in ACCOUNTS or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", msg_id or "") or msg_id.startswith("-"):
        raise MailError("not a message this module may read")
    if uses_google(account):
        return _gmail_read(account, msg_id)
    try:
        out = run(["message", "read", "-a", account, "-f", folder, "-p", "-o", "plain",
                   *[a for h in READ_HEADERS for a in ("-H", h)], "--", msg_id])
    except subprocess.TimeoutExpired:
        raise MailError("reading the message took too long") from None
    if out.returncode != 0:
        raise MailError(f"reading the message failed: {(out.stderr or '').strip()[-200:]}")
    return (out.stdout or "")[:READ_MAX]


# MARK: Gmail through Sign In with Google (google.py holds the token)

GMAIL_LABELS = {"INBOX": "INBOX", "Sent": "SENT", "Drafts": "DRAFT", "[Gmail]/Sent Mail": "SENT"}
GMAIL_ALL = {"[Gmail]/All Mail", "All Mail"}


def gmail_get(address: str, path: str, params: dict | None = None) -> dict:
    """One Gmail API read. Tests replace this."""
    from . import google
    return google.api_get(address, f"{google.GMAIL_API}/users/me/{path}", params)


def _gmail_query(query: list[str] | None) -> str:
    """himalaya's ["from", addr] or ["to", addr] as a Gmail search."""
    q = list(query or [])
    out = []
    while len(q) >= 2 and q[0] in ("from", "to", "subject"):
        out.append(f"{q[0]}:{q[1]}")
        q = q[2:]
        if q and q[0] == "and":
            q = q[1:]
    return " ".join(out)


def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers") or []:
        if str(h.get("name") or "").lower() == name.lower():
            return str(h.get("value") or "")
    return ""


def _gmail_envelopes(account: str, folder: str, page_size: int, query: list[str] | None) -> list[dict]:
    from . import google
    address = ACCOUNTS[account]
    params: dict = {"maxResults": int(page_size)}
    if folder not in GMAIL_ALL:
        params["labelIds"] = GMAIL_LABELS.get(folder, folder)
    if q := _gmail_query(query):
        params["q"] = q
    try:
        listed = gmail_get(address, "messages", params)
        out = []
        for m in (listed.get("messages") or [])[:int(page_size)]:
            full = gmail_get(address, f"messages/{m['id']}", {"format": "metadata",
                                                               "metadataHeaders": ["From", "To", "Subject", "Date"]})
            labels = set(full.get("labelIds") or [])
            env = {"id": full.get("id") or m["id"], "subject": _header(full, "Subject"), "from": _header(full, "From"),
                   "to": _header(full, "To"), "date": _header(full, "Date"),
                   "flags": [] if "UNREAD" in labels else ["Seen"]}
            e = _envelope(env, account, folder)
            if e:
                out.append(e)
        return out
    except google.GoogleError as e:
        raise MailError(f"listing {account} {folder} failed: {e}") from None


def _gmail_text(part: dict) -> str:
    if part.get("mimeType") == "text/plain" and (part.get("body") or {}).get("data"):
        import base64
        data = part["body"]["data"]
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(errors="replace")
    for p in part.get("parts") or []:
        t = _gmail_text(p)
        if t:
            return t
    return ""


def _gmail_read(account: str, msg_id: str) -> str:
    """One message's text. The Gmail API's get never marks a message read."""
    from . import google
    try:
        msg = gmail_get(ACCOUNTS[account], f"messages/{msg_id}", {"format": "full"})
    except google.GoogleError as e:
        raise MailError(f"reading the message failed: {e}") from None
    head = "\n".join(f"{k}: {_header(msg, k)}" for k in READ_HEADERS if _header(msg, k))
    return (head + "\n\n" + _gmail_text(msg.get("payload") or {}))[:READ_MAX]


# A course site's own notifications come "from" the teacher's address (Moodle sends feedback notices and forum copies
# that way), so the sender looks like a person. The subject and the footer give them away. Two "Bill Hart" cards
# on Oct 9 were feedback notices and a forum post copy, which nobody answers by email.
LMS_SUBJECT = re.compile(r"\b(has given feedback for|has graded|you have been enrolled|new assignment submission|"
                         r"assignment .* is due|course announcement|forum digest)\b|^\d{6}\s+[A-Z]{2,4}\s+\d{3}\b",
                         re.I)
LMS_BODY = re.compile(r"this is a copy of a message posted|change your forum digest|to change your notification|"
                      r"you are receiving this (email|message|notification) because|unsubscribe from this forum|"
                      r"view the feedback|see this post in context|reply to this post|do not reply to this (email|"
                      r"message)|this is an automated (message|notification|email)|an automatic notification",
                      re.I)


def is_notification(subject: str, body: str = "") -> bool:
    """A notice sent by a system in a person's name: a course site's feedback or forum copy, an automated message.
    Nobody answers these by email, so no reply is owed."""
    return bool(LMS_SUBJECT.search(subject or "") or LMS_BODY.search(body or ""))


def is_person(addr: str, name: str = "") -> bool:
    addr = (addr or "").lower()
    if not addr or "@" not in addr or addr in OWN:
        return False
    return not AUTOMATED.match(addr) and not AUTOMATED_DOMAINS.search(addr)


def norm_subject(subject: str) -> str:
    """"Re: Fwd: The Handoff " and "the handoff" are one thread."""
    s = (subject or "").strip()
    while True:
        t = re.sub(r"^\s*(re|fw|fwd|aw)\s*(\[\d+\])?\s*:\s*", "", s, flags=re.I)
        if t == s:
            break
        s = t
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))[:120]


_HEAD_BLOCK = r"(?:(?:" + "|".join(re.escape(h) for h in READ_HEADERS) + r"):.*\n?)+$"


# A newsletter, a mailing list or a bulk send, however personally it is written (Oct 10: a creator's weekly letter,
# "Please don't build this yet", became "a reply you owe" because it came from a person's name and asked readers a
# question). Mailing software marks its mail with List-Unsubscribe, List-Id or "Precedence: bulk", and an automatic
# message says Auto-Submitted. A footer that offers to unsubscribe or manage preferences gives the rest away.
BULK_FOOTER = re.compile(r"\bunsubscribe\b|manage (your )?(email |subscription |notification )?preferences|"
                         r"update your (email )?preferences|email preferences|view (this email |it )?in (your |a )?"
                         r"browser|opt[- ]out of (these|this|future)|you('re| are) (receiving|getting) this (email|"
                         r"because)|sent to you because you (signed up|subscribed)|no longer wish to receive", re.I)


def headers_of(text: str) -> dict:
    """The header lines at the top of a message as read() returns it, lowercased names."""
    out = {}
    for line in (text or "").splitlines():
        if not line.strip():
            break
        m = re.match(r"([A-Za-z][\w-]{1,40}):\s*(.*)$", line)
        if not m:
            break
        out[m.group(1).lower()] = m.group(2).strip()
    return out


def is_bulk(text: str) -> bool:
    """Mail sent to a list, not to the person: nobody owes it a reply."""
    h = headers_of(text)
    if h.get("list-unsubscribe") or h.get("list-id"):
        return True
    if h.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return True
    if h.get("auto-submitted", "no").lower() != "no":
        return True
    return bool(BULK_FOOTER.search(text or ""))


def own_text(body: str) -> str:
    """The sender's own words: quoted lines and the "On ... wrote:" tail are dropped."""
    lines = []
    for line in (body or "").splitlines():
        if re.match(r"\s*On .{4,200} wrote:\s*$", line) or line.strip().startswith("-----Original Message"):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    # Header lines himalaya prints before the text.
    text = "\n".join(lines)
    parts = re.split(r"\n\s*\n", text, maxsplit=1)
    if len(parts) == 2 and re.match(_HEAD_BLOCK, parts[0].strip() + "\n"):
        text = parts[1]
    return text.strip()


ASKS = re.compile(r"\?|\b(can you|could you|would you|will you|please (let me know|confirm|send|reply|advise|"
                  r"review|sign|tell)|let me know|get back to me|do you (want|have|know|think)|are you able|"
                  r"when (can|could|will) you|what do you think)\b", re.I)


def asks_something(body: str) -> bool:
    return bool(ASKS.search(own_text(body)))
