""










import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from email.parser import Parser
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from . import config, home


class _Accounts(Mapping):
    ""



    def __init__(self, everything: bool = False):
        self.everything = everything

    def _now(self) -> dict:
        return {str(a["id"]): str(a["address"]) for a in home.mail_accounts()
                if self.everything or not (a.get("zips_own") and a.get("owner_zip"))}

    def __getitem__(self, key):
        return self._now()[key]

    def __iter__(self):
        return iter(self._now())

    def __len__(self):
        return len(self._now())


class _Own:
    ""


    def _now(self) -> set[str]:
        return {a.lower() for a in ALL.values()} | {x for a in home.mail_accounts() for x in home.mailbox_addresses(a)}

    def __contains__(self, addr) -> bool:
        return str(addr or "").lower() in self._now()

    def __iter__(self):
        return iter(self._now())


ACCOUNTS = _Accounts()
ALL = _Accounts(everything=True)


def _known(account: str) -> bool:
    ""

    return account in ALL or account in ACCOUNTS


def _address_of(account: str) -> str:
    return ALL.get(account) or ACCOUNTS.get(account) or ""
OWN = _Own()


def _account(account: str) -> dict | None:
    return next((a for a in home.mail_accounts() if a["id"] == account), None)


def default_account() -> str | None:
    ""
    accts = [a for a in home.mail_accounts() if str(a["id"]) in ACCOUNTS]
    for a in accts:
        if a.get("default"):
            return str(a["id"])
    return str(accts[0]["id"]) if accts else None


def account_for(addr: str) -> str | None:
    ""
    dom = (addr or "").rsplit("@", 1)[-1].lower()
    for a in home.mail_accounts():
        if str(a["id"]) not in ACCOUNTS:
            continue
        own = str(a["address"]).rsplit("@", 1)[-1].lower()
        if own not in PUBLIC_DOMAINS and dom and (dom == own or dom.endswith("." + own)):
            return str(a["id"])
    return default_account()


PUBLIC_DOMAINS = {"gmail.com", "googlemail.com", "icloud.com", "me.com", "mac.com", "outlook.com", "hotmail.com",
                  "live.com", "yahoo.com", "aol.com", "proton.me", "protonmail.com", "fastmail.com"}


def uses_google(account: str) -> bool:
    ""
    a = _account(account)
    return bool(a and home.is_google(a))


def all_mail_folder(account: str) -> str | None:
    ""
    for a in home.mail_accounts():
        if a["id"] == account:
            return a.get("all_mail_folder") or None
    return None
TIMEOUT_SEC = 60
READ_MAX = 6000

READ_HEADERS = ("From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted")


AUTOMATED = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|mailer|mailer-daemon|postmaster|bounces?|news|"
    r"newsletters?|marketing|info|support|help|billing|receipts?|orders?|alerts?|updates?|hello|team|admin|accounts?|"
    r"service|services|security|digest|calendar|feedback|reply|contact|store|shop|deals|offers|rewards|statements?)"
    r"(\d+)?([+._-].*)?@", re.I)
AUTOMATED_DOMAINS = re.compile(r"@(.+\.)?(mailchimp|sendgrid|substack|linkedin|facebookmail|amazonses|mandrillapp|"
                               r"hubspot|intercom|zendesk|github|google|apple|paypal|venmo|chase|walmart|amazon)\.",
                               re.I)


class MailError(RuntimeError):
    pass


class MessageGone(MailError):
    ""


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
    ""
    return subprocess.run([_himalaya(), *argv], capture_output=True, text=True, timeout=TIMEOUT_SEC,
                          cwd=str(config.HOME), env=_env())


def _people(x) -> list[tuple[str, str]]:
    ""
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
            "flags": {str(f).lower() for f in flags} if isinstance(flags, list) else set(),
            "thread": str(e.get("thread") or "")}


def envelopes(account: str, folder: str = "INBOX", page_size: int = 50, query: list[str] | None = None) -> list[dict]:
    ""
    if not _known(account):
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
    ""
    if not _known(account) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", msg_id or "") or msg_id.startswith("-"):
        raise MailError("not a message this module may read")
    if uses_google(account):
        return _gmail_read(account, msg_id)
    try:
        out = run(["message", "read", "-a", account, "-f", folder, "-p", "-o", "plain",
                   *[a for h in READ_HEADERS for a in ("-H", h)], "--", msg_id])
    except subprocess.TimeoutExpired:
        raise MailError("reading the message took too long") from None
    if out.returncode != 0:
        if "FETCH returned no body for the requested message" in (out.stderr or ""):
            raise MessageGone("That email is no longer available.")
        raise MailError(f"reading the message failed: {(out.stderr or '').strip()[-200:]}")
    return (out.stdout or "")[:READ_MAX]


def message_link(account: str, folder: str, msg_id: str) -> tuple[str | None, str | None]:
    ""




    acct = _account(account)
    if not acct or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", msg_id) or msg_id.startswith("-"):
        raise MailError("Give a connected mailbox and the message id from email read or search.")
    if not folder or len(folder) > 100 or folder.startswith("-") or any(ord(c) < 32 for c in folder):
        raise MailError("Give the folder used to read the email, such as INBOX. Message ids depend on the folder.")
    address = str(acct.get("address") or "")
    if not address:
        raise MailError("That mailbox has no email address.")
    if home.is_google(acct):
        from . import google
        try:
            msg = gmail_get(address, f"messages/{msg_id}", {"format": "metadata",
                                                           "metadataHeaders": ["Message-ID"]})
        except google.GoogleError as e:
            if e.reason != "notFound":
                raise MailError(str(e)) from None
            raise MailError(f"I couldn't find email {msg_id} in {account}. Read it again and check its id.") from None
        message_id = _header(msg, "Message-ID")
    else:
        try:
            out = run(["message", "read", "-a", account, "-f", folder, "-p", "-o", "plain",
                       "-H", "Message-ID", "--", msg_id])
        except (subprocess.TimeoutExpired, OSError):
            raise MailError(f"I couldn't read email {msg_id} in {account} {folder}. Try again.") from None
        if out.returncode != 0:
            raise MailError(f"I couldn't find email {msg_id} in {account} {folder}. Read it again and check its id.")
        message_id = str(Parser().parsestr(out.stdout or "", headersonly=True).get("Message-ID") or "")
    try:
        return web_link(acct, message_id)
    except ValueError:
        raise MailError(f"Email {msg_id} in {account} has no usable Message-ID. I can't make an exact link.") from None


def web_link(acct: dict, message_id: str) -> tuple[str | None, str | None]:
    ""
    address = str(acct.get("address") or "")
    gmail = (home.is_google(acct) or str(acct.get("provider") or "").lower() == "gmail"
             or address.lower().endswith(("@gmail.com", "@googlemail.com")))
    if gmail:
        message_id = message_id.strip().removeprefix("<").removesuffix(">")
        if not re.fullmatch(r"[^\s<>]+@[^\s<>]+", message_id):
            raise ValueError(message_id)
        return (f"https://mail.google.com/mail/?authuser={quote(address, safe='')}"
                f"#search/rfc822msgid%3A{quote(message_id, safe='')}", None)
    if acct.get("provider") == "icloud" or address.lower().endswith(("@icloud.com", "@me.com", "@mac.com")):
        return "https://www.icloud.com/mail", "iCloud Mail inbox"
    return None, "Mail inbox"




GMAIL_LABELS = {"INBOX": "INBOX", "Sent": "SENT", "Drafts": "DRAFT", "[Gmail]/Sent Mail": "SENT"}
GMAIL_ALL = {"[Gmail]/All Mail", "All Mail"}


def gmail_get(address: str, path: str, params: dict | None = None) -> dict:
    ""
    from . import google
    return google.api_get(address, f"{google.GMAIL_API}/users/me/{path}", params)


def _gmail_query(query: list[str] | None) -> str:
    ""
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
    address = _address_of(account)
    params: dict = {"maxResults": int(page_size)}
    if folder not in GMAIL_ALL:
        params["labelIds"] = GMAIL_LABELS.get(folder, folder)
    if q := _gmail_query(query):
        params["q"] = q
    try:
        listed = gmail_get(address, "messages", params)
        out = []
        for m in (listed.get("messages") or [])[:int(page_size)]:
            e = _gmail_metadata(account, m, folder)
            if e:
                out.append(e)
        return out
    except google.GoogleError as e:
        raise MailError(f"listing {account} {folder} failed: {e}") from None


def _gmail_metadata(account: str, message: dict, folder: str) -> dict | None:
    full = gmail_get(_address_of(account), f"messages/{message['id']}",
                     {"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]})
    labels = set(full.get("labelIds") or [])
    return _envelope({"id": full.get("id") or message["id"], "thread": full.get("threadId") or message.get("threadId") or "",
                      "subject": _header(full, "Subject"), "from": _header(full, "From"), "to": _header(full, "To"),
                      "date": _header(full, "Date"), "flags": [] if "UNREAD" in labels else ["Seen"]}, account, folder)


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
    ""
    from . import google
    try:
        msg = gmail_get(_address_of(account), f"messages/{msg_id}", {"format": "full"})
    except google.GoogleError as e:
        if e.reason == "notFound":
            raise MessageGone("That email is no longer available.") from None
        raise MailError(f"reading the message failed: {e}") from None
    head = "\n".join(f"{k}: {_header(msg, k)}" for k in READ_HEADERS if _header(msg, k))
    return (head + "\n\n" + _gmail_text(msg.get("payload") or {}))[:READ_MAX]





LMS_SUBJECT = re.compile(r"\b(has given feedback for|has graded|you have been enrolled|new assignment submission|"
                         r"assignment .* is due|course announcement|forum digest)\b|^\d{6}\s+[A-Z]{2,4}\s+\d{3}\b",
                         re.I)
LMS_BODY = re.compile(r"this is a copy of a message posted|change your forum digest|to change your notification|"
                      r"you are receiving this (email|message|notification) because|unsubscribe from this forum|"
                      r"view the feedback|see this post in context|reply to this post|do not reply to this (email|"
                      r"message)|this is an automated (message|notification|email)|an automatic notification",
                      re.I)


def is_notification(subject: str, body: str = "") -> bool:
    ""

    return bool(LMS_SUBJECT.search(subject or "") or LMS_BODY.search(body or ""))


def is_person(addr: str, name: str = "") -> bool:
    addr = (addr or "").lower()
    if not addr or "@" not in addr or addr in OWN:
        return False
    return not AUTOMATED.match(addr) and not AUTOMATED_DOMAINS.search(addr)


def norm_subject(subject: str) -> str:
    ""
    s = (subject or "").strip()
    while True:
        t = re.sub(r"^\s*(re|fw|fwd|aw)\s*(\[\d+\])?\s*:\s*", "", s, flags=re.I)
        if t == s:
            break
        s = t
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))[:120]


_HEAD_BLOCK = r"(?:(?:" + "|".join(re.escape(h) for h in READ_HEADERS) + r"):.*\n?)+$"






BULK_FOOTER = re.compile(r"\bunsubscribe\b|manage (your )?(email |subscription |notification )?preferences|"
                         r"update your (email )?preferences|email preferences|view (this email |it )?in (your |a )?"
                         r"browser|opt[- ]out of (these|this|future)|you('re| are) (receiving|getting) this (email|"
                         r"because)|sent to you because you (signed up|subscribed)|no longer wish to receive", re.I)


def headers_of(text: str) -> dict:
    ""
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
    ""
    h = headers_of(text)
    if h.get("list-unsubscribe") or h.get("list-id"):
        return True
    if h.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return True
    if h.get("auto-submitted", "no").lower() != "no":
        return True
    return bool(BULK_FOOTER.search(text or ""))


def own_text(body: str) -> str:
    ""
    lines = []
    for line in (body or "").splitlines():
        if re.match(r"\s*On .{4,200} wrote:\s*$", line) or line.strip().startswith("-----Original Message"):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)

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
