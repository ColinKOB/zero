""





import email.policy
from email.header import decode_header, make_header
import hashlib
import imaplib
import re
import ssl
import subprocess
import time
import tomllib
from contextlib import contextmanager
from datetime import datetime, timezone
from email.parser import BytesParser
from html.parser import HTMLParser

from . import config, google, mail, mailboxes



LIST_HEADERS = ("List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted")


class CursorChanged(mail.MailError):
    ""


def _check_account(account: str) -> None:
    if not mail._known(account):
        raise mail.MessageGone("That account is no longer connected.")


def _header(parsed, name: str) -> str:
    value = next((value for key, value in parsed.raw_items() if key.casefold() == name.casefold()), "")
    value = value.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    try:
        pieces = decode_header(value)
    except (email.errors.HeaderParseError, ValueError, IndexError):
        return value
    out = []
    for piece, charset in pieces:
        if isinstance(piece, str):
            out.append(piece)
            continue
        try:
            out.append(piece.decode(charset or "utf-8", errors="replace"))
        except LookupError:
            out.append(piece.decode("utf-8", errors="replace"))
    return "".join(out)


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("title", "script", "style"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "li", "tr", "td", "h1", "h2", "h3", "section") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("title", "script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div", "li", "tr", "td", "h1", "h2", "h3", "section") and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self, markup: str) -> str:
        self.feed(markup)
        self.close()
        return "\n".join(line for raw in "".join(self.parts).splitlines() if (line := " ".join(raw.split())))


@contextmanager
def _imap(account: str):
    _check_account(account)
    client = None
    try:
        settings = tomllib.loads(mailboxes._config_file().read_text())["accounts"][account]
        backend = settings["backend"]
        if backend.get("type") != "imap" or settings["email"].casefold() != mail._address_of(account).casefold():
            raise ValueError
        auth = backend["auth"]
        if auth.get("type") != "password":
            raise ValueError
        if isinstance(auth.get("cmd"), str):
            result = subprocess.run(auth["cmd"], shell=True, capture_output=True, timeout=mail.TIMEOUT_SEC,
                                    cwd=config.HOME, env=mail._env())
            if result.returncode:
                raise ValueError
            password = result.stdout.decode().rstrip("\r\n")
        else:
            password = auth["raw"]
        host, port = backend["host"], int(backend.get("port") or 993)
        encryption = backend.get("encryption", {}).get("type", "tls")
        context = ssl.create_default_context()
        if encryption == "tls":
            client = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=mail.TIMEOUT_SEC)
        elif encryption == "start-tls":
            client = imaplib.IMAP4(host, port, timeout=mail.TIMEOUT_SEC)
            client.starttls(ssl_context=context)
        elif encryption == "none" and host in ("localhost", "127.0.0.1", "::1"):
            client = imaplib.IMAP4(host, port, timeout=mail.TIMEOUT_SEC)
        else:
            raise ValueError
        client.login(backend["login"], password)
        if client.select("INBOX", readonly=True)[0] != "OK":
            raise ValueError
    except Exception:
        if client:
            try:
                client.logout()
            except Exception:
                pass
        raise mail.MailError("This mailbox could not connect securely.") from None
    try:
        yield client
    except (imaplib.IMAP4.error, OSError, ValueError):
        raise mail.MailError("This mailbox could not refresh.") from None
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _hits(data) -> list[bytes]:
    ""

    return b" ".join(d for d in data or [] if d).split()


def _state(client) -> tuple[str, int]:
    validity = client.response("UIDVALIDITY")[1]
    next_uid = client.response("UIDNEXT")[1]
    if not validity or not next_uid or not validity[0] or not next_uid[0] or \
            not validity[0].isdigit() or not next_uid[0].isdigit() or int(next_uid[0]) < 1:
        raise mail.MailError("The mailbox did not confirm its message identities.")
    return validity[0].decode(), int(next_uid[0]) - 1


def scan(account: str, checkpoint: dict) -> dict:
    ""
    _check_account(account)
    if mail.uses_google(account):
        return _gmail_scan(account, checkpoint)
    with _imap(account) as client:
        validity, upper = _state(client)
        same = checkpoint.get("provider") == "imap" and checkpoint.get("uid_validity") == validity
        lower = int(checkpoint.get("uid", 0)) + 1 if same else 1
        uids = []
        if lower <= upper:
            status, data = client.uid("search", None, "UID", f"{lower}:{upper}")
            if status != "OK":
                raise mail.MailError("The mailbox could not list its messages.")
            uids = sorted({int(u) for u in _hits(data) if u.isdigit() and lower <= int(u) <= upper})
        if not same:
            uids = uids[-50:]
        return {"pending": [{"provider": "imap", "uid_validity": validity, "uid": u} for u in uids],
                "checkpoint": {"provider": "imap", "uid_validity": validity, "uid": upper}}


def _gmail_scan(account: str, checkpoint: dict) -> dict:
    address = mail._address_of(account)
    bootstrap = checkpoint.get("provider") != "gmail"
    history_id = mail.gmail_get(address, "profile").get("historyId")
    if not history_id:
        raise mail.MailError("This mailbox did not confirm its change history.")
    ids, token = set(), ""
    if bootstrap:
        page = mail.gmail_get(address, "messages", {"labelIds": "INBOX", "maxResults": 50})
        ids.update(str(m["id"]) for m in page.get("messages") or [])
    else:
        while True:
            params = {"maxResults": 500}
            if token:
                params["pageToken"] = token
            params.update(startHistoryId=checkpoint["history_id"], historyTypes="messageAdded", labelId="INBOX")
            try:
                page = mail.gmail_get(address, "history", params)
            except google.GoogleError as e:
                if e.reason == "notFound":
                    return _gmail_scan(account, {"provider": "resync"})
                raise
            for event in page.get("history") or []:
                for added in event.get("messagesAdded") or []:
                    ids.add(str(added["message"]["id"]))
            token = str(page.get("nextPageToken") or "")
            if not token:
                break
    return {"pending": [{"provider": "gmail", "id": mid} for mid in sorted(ids)],
            "checkpoint": {"provider": "gmail", "history_id": str(history_id)}}


def read(account: str, reference: dict) -> tuple[dict, str]:
    _check_account(account)
    if reference["provider"] == "gmail":
        try:
            message = mail.gmail_get(mail._address_of(account), f"messages/{reference['id']}", {"format": "full"})
        except google.GoogleError as e:
            if e.reason == "notFound":
                raise mail.MessageGone("That email is no longer available.") from None
            raise mail.MailError("This email could not be read.") from None
        date = mail._header(message, "Date")
        if not mail.when(date) and message.get("internalDate"):
            date = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc).isoformat()
        envelope = mail._envelope({"id": message["id"], "thread": message.get("threadId") or "",
                                  "subject": mail._header(message, "Subject"), "from": mail._header(message, "From"),
                                  "to": ", ".join(filter(None, (mail._header(message, "To"), mail._header(message, "Cc")))),
                                  "date": date}, account, "INBOX")
        if envelope is None:
            raise mail.MailError("This email has no readable date.")
        envelope["message_id_header"] = mail._header(message, "Message-ID")
        envelope["labels"] = [str(label) for label in message.get("labelIds") or []]
        envelope["list_headers"] = {name.casefold(): mail._header(message, name) for name in LIST_HEADERS}
        envelope["received_at"] = int(message["internalDate"]) / 1000 if message.get("internalDate") else envelope["date"]
        payload = message.get("payload") or {}
        text = mail._gmail_text(payload)
        if not text.strip():
            import base64
            parts, html = [payload], []
            while parts:
                part = parts.pop()
                disposition = next((h.get("value", "") for h in part.get("headers") or []
                                    if h.get("name", "").casefold() == "content-disposition"), "")
                if part.get("filename") or disposition.split(";", 1)[0].strip().casefold() == "attachment":
                    continue
                data = (part.get("body") or {}).get("data")
                if part.get("mimeType") == "text/html" and data:
                    html.append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(errors="replace"))
                parts.extend(reversed(part.get("parts") or []))
            text = _HTMLText().text("\n".join(html))
        envelope["attachments"] = _gmail_attachments(payload)
        envelope["truncated"] = len(text) > mail.READ_MAX
        return envelope, text[:mail.READ_MAX]
    with _imap(account) as client:
        validity, _ = _state(client)
        if validity != reference["uid_validity"]:
            raise CursorChanged("The mailbox changed. I will refresh its messages again.")
        status, data = client.uid("fetch", str(int(reference["uid"])), "(UID INTERNALDATE BODY.PEEK[]<0.32768>)")
        if status != "OK":
            raise mail.MailError("This email could not be read.")
        parts = [p for p in data or [] if isinstance(p, tuple) and isinstance(p[1], bytes)]
        if not parts:
            raise mail.MessageGone("That email is no longer available.")
        raw = parts[0][1]
        metadata = b" ".join(p[0] if isinstance(p, tuple) else p for p in data if isinstance(p, (tuple, bytes)))
        uid = re.search(rb"\bUID\s+(\d+)", metadata)
        if not uid or int(uid[1]) != reference["uid"]:
            raise mail.MailError("The mailbox returned a different message.")
        parsed = BytesParser(policy=email.policy.compat32).parsebytes(raw)
        message_id = _header(parsed, "Message-ID").strip()
        if len(message_id) > 512 or not re.fullmatch(r"<[^\s<>]+@[^\s<>]+>", message_id):
            message_id = ""
        key = "rfc:" + message_id if message_id else f"imap:{validity}:{reference['uid']}"
        date = mail.when(_header(parsed, "Date"))
        stamp = imaplib.Internaldate2tuple(metadata)
        if stamp is None:
            raise mail.MailError("This email has no readable arrival time.")
        received = time.mktime(stamp)
        envelope = mail._envelope({"id": key, "subject": _header(parsed, "Subject"),
                                  "from": _header(parsed, "From"),
                                  "to": ", ".join(filter(None, (_header(parsed, "To"), _header(parsed, "Cc")))),
                                  "date": (date or datetime.fromtimestamp(received, timezone.utc)).isoformat()}, account, "INBOX")
        envelope["message_id_header"] = message_id
        envelope["labels"] = []
        envelope["list_headers"] = {name.casefold(): _header(parsed, name) for name in LIST_HEADERS}
        envelope["received_at"] = received
        text = _text_of(parsed)
        envelope["attachments"] = _attachments(parsed)
        envelope["truncated"] = len(raw) >= 32768 or len(text) > mail.READ_MAX
        return envelope, text[:mail.READ_MAX]




ATTACHMENTS = 10


def _gmail_attachments(payload: dict) -> list[str]:
    names, parts = [], [payload]
    while parts:
        part = parts.pop(0)
        if name := " ".join(str(part.get("filename") or "").split())[:120]:
            names.append(name)
        parts.extend(part.get("parts") or [])
    return names[:ATTACHMENTS]


def _attachments(parsed) -> list[str]:
    ""

    names = []
    for part in parsed.walk():
        try:
            name = str(make_header(decode_header(part.get_filename() or "")))
        except (email.errors.HeaderParseError, TypeError, ValueError, LookupError):
            name = ""
        if name := " ".join(name.split())[:120]:
            names.append(name)
    return names[:ATTACHMENTS]


def _text_of(parsed) -> str:
    texts, html = [], []
    for part in parsed.walk():
        kind = part.get_content_type()
        if kind in ("text/plain", "text/html") and part.get_content_disposition() != "attachment":
            payload = part.get_payload(decode=True) or b""
            try:
                decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            (texts if kind == "text/plain" else html).append(decoded)
    text = "\n".join(texts)
    if not text.strip() and html:
        text = _HTMLText().text("\n".join(html))
    return text


SAMPLE_CHARS = 600
_QUOTE_START = re.compile(r"^(>|On .+ wrote:$|-{2,}\s*Original Message|From: )")
_PLAIN_ADDR = re.compile(r"^[\w.+'-]+@[\w-]+(\.[\w-]+)+$")


def own_words(text: str) -> str:
    ""
    kept = []
    for line in text.splitlines():
        if _QUOTE_START.match(line.strip()):
            break
        kept.append(line)
    return "\n".join(kept).strip()[:SAMPLE_CHARS]


def sent_samples(account: str, to_addr: str, limit: int = 3) -> list[str]:
    ""

    _check_account(account)
    searches = ([f"to:{to_addr}"] if _PLAIN_ADDR.match(to_addr or "") else []) + [""]
    if mail.uses_google(account):
        address, ids = mail._address_of(account), []
        for query in searches:
            listed = mail.gmail_get(address, "messages", {"q": f"in:sent {query}".strip(), "maxResults": limit})
            ids += [str(m["id"]) for m in listed.get("messages") or [] if str(m["id"]) not in ids]
        texts = [mail._gmail_text(mail.gmail_get(address, f"messages/{mid}", {"format": "full"}).get("payload") or {})
                 for mid in ids[:limit]]
    else:
        texts = []
        with _imap(account) as client:
            sent = _sent_folder(client)
            if not sent or client.select(sent if sent.startswith('"') else f'"{sent}"', readonly=True)[0] != "OK":
                return []
            uids = []
            for query in searches:
                status, data = client.uid("search", None, *(("TO", f'"{query[3:]}"') if query else ("ALL",)))
                found = sorted(int(u) for u in _hits(data) if u.isdigit()) if status == "OK" else []
                uids += [u for u in reversed(found[-limit:]) if u not in uids]
            for uid in uids[:limit]:
                status, data = client.uid("fetch", str(uid), "(BODY.PEEK[]<0.16384>)")
                raw = next((p[1] for p in data or [] if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
                if status == "OK" and raw:
                    texts.append(_text_of(BytesParser(policy=email.policy.compat32).parsebytes(raw)))
    return [words for text in texts if (words := own_words(text))]


def signature(envelope: dict, text: str) -> str:
    body = "\n".join(str(envelope.get(k) or "") for k in ("id", "subject", "from_addr")) + "\n" + text
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def relocate(account: str, message_id: str) -> dict:
    ""
    if not re.fullmatch(r"<[^\s<>]+@[^\s<>]+>", message_id) or len(message_id) > 512:
        raise mail.MessageGone("That email is no longer available.")
    with _imap(account) as client:
        validity, _ = _state(client)
        quoted = '"' + message_id.replace('\\', '\\\\').replace('"', '\\"') + '"'
        status, data = client.uid("search", None, "HEADER", "Message-ID", quoted)
        if status != "OK":
            raise mail.MailError("This email could not be located.")
        ids = [int(u) for u in _hits(data) if u.isdigit()]
        if not ids:
            raise mail.MessageGone("That email is no longer available.")
        return {"provider": "imap", "uid_validity": validity, "uid": max(ids)}


def handled(account: str, asks: list[dict]) -> dict[str, str]:
    ""





    _check_account(account)
    gmail = [a for a in asks if (a.get("provider_ref") or {}).get("provider") == "gmail"]
    imap = [a for a in asks if (a.get("provider_ref") or {}).get("provider") == "imap"]
    out = _gmail_handled(account, gmail) if gmail else {}
    if imap:
        out.update(_imap_handled(account, imap))
    return out


def _gmail_handled(account: str, asks: list[dict]) -> dict[str, str]:
    address, out, threads = mail._address_of(account), {}, {}
    for ask in asks:
        mid = str(ask["provider_ref"]["id"])
        try:
            thread = ask.get("thread") or mail.gmail_get(address, f"messages/{mid}", {"format": "minimal"}).get("threadId")
            if not thread:
                continue
            if thread not in threads:
                threads[thread] = mail.gmail_get(address, f"threads/{thread}", {"format": "minimal"}).get("messages") or []
        except google.GoogleError as e:
            if e.reason != "notFound":
                raise mail.MailError("This mailbox could not refresh.") from None
            out[ask["message"]] = "left_inbox"
            continue
        messages = threads[thread]
        own = next((m for m in messages if str(m.get("id")) == mid), None)
        labels = set(own.get("labelIds") or []) if own else set()
        if own is None or "INBOX" not in labels or "TRASH" in labels:
            out[ask["message"]] = "left_inbox"
        elif any("SENT" in (m.get("labelIds") or []) and int(m.get("internalDate") or 0) > int(own.get("internalDate") or 0)
                 for m in messages):
            out[ask["message"]] = "replied"
    return out


def _sent_folder(client) -> str | None:
    status, rows = client.list()
    if status != "OK":
        return None
    names = {}
    for row in rows or []:
        if not isinstance(row, bytes):
            continue
        m = re.match(rb'\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$', row)
        if not m:
            continue
        name = m["name"].decode(errors="replace").strip()
        if "\\sent" in m["flags"].decode(errors="replace").casefold().split():
            return name
        names[name.strip('"').casefold()] = name
    return next((names[n] for n in ("sent", "sent items", "sent messages", "inbox.sent", "sent mail") if n in names), None)


def _imap_handled(account: str, asks: list[dict]) -> dict[str, str]:
    out = {}
    with _imap(account) as client:
        validity, _ = _state(client)
        live = {int(a["provider_ref"]["uid"]): a for a in asks if a["provider_ref"].get("uid_validity") == validity}
        if not live:
            return out
        status, data = client.uid("fetch", ",".join(str(u) for u in sorted(live)), "(UID FLAGS)")
        if status != "OK":
            raise mail.MailError("This mailbox could not refresh.")
        flags = {}
        for item in data or []:
            raw = item[0] if isinstance(item, tuple) else item
            if isinstance(raw, bytes) and (uid := re.search(rb"\bUID\s+(\d+)", raw)):
                found = re.search(rb"FLAGS\s+\(([^)]*)\)", raw)
                flags[int(uid[1])] = set(found[1].decode().casefold().split()) if found else set()
        for uid, ask in live.items():
            if uid not in flags or "\\deleted" in flags[uid]:
                out[ask["message"]] = "left_inbox"
            elif "\\answered" in flags[uid]:
                out[ask["message"]] = "replied"
    return out


_MESSAGE_ID = re.compile(r"<[^\s<>\"]+@[^\s<>\"]+>")


SENT_SCAN = 10
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def replied(account: str, asks: list[dict]) -> set[str]:
    ""






    wanted: dict[str, float] = {}
    for ask in asks:
        sender = str(ask.get("sender") or "").casefold()
        if _ADDRESS.fullmatch(sender) and _MESSAGE_ID.fullmatch(ask.get("message_id_header") or ""):
            wanted[sender] = min(wanted.get(sender, ask["since"]), ask["since"])
    if not wanted:
        return set()
    _check_account(account)
    senders = sorted(wanted, key=wanted.get, reverse=True)[:40]
    found = []
    if mail.uses_google(account):
        address = mail._address_of(account)
        try:
            for addr in senders:
                listed = mail.gmail_get(address, "messages", {
                    "labelIds": "SENT", "q": f"{{to:{addr} cc:{addr}}} after:{int(wanted[addr]) - 86400}",
                    "maxResults": SENT_SCAN})
                for m in listed.get("messages") or []:
                    meta = mail.gmail_get(address, f"messages/{m['id']}",
                                          {"format": "metadata", "metadataHeaders": ["In-Reply-To", "References"]})
                    found += [mail._header(meta, "In-Reply-To"), mail._header(meta, "References")]
        except google.GoogleError:
            raise mail.MailError("This mailbox could not refresh.") from None
    else:
        with _imap(account) as client:
            sent = _sent_folder(client)
            if not sent or client.select(sent if sent.startswith('"') else f'"{sent}"', readonly=True)[0] != "OK":
                return set()
            uids = set()
            for addr in senders:
                day = datetime.fromtimestamp(wanted[addr] - 86400, timezone.utc)
                since = f"{day.day}-{_MONTHS[day.month - 1]}-{day.year}"
                status, data = client.uid("search", None, "SINCE", since, "OR", "TO", f'"{addr}"', "CC", f'"{addr}"')
                if status == "OK":
                    uids.update(sorted(int(u) for u in _hits(data) if u.isdigit())[-SENT_SCAN:])
            if uids:
                status, data = client.uid("fetch", ",".join(str(u) for u in sorted(uids)),
                                          "(BODY.PEEK[HEADER.FIELDS (IN-REPLY-TO REFERENCES)])")
                for part in (data or []) if status == "OK" else []:
                    if isinstance(part, tuple) and isinstance(part[1], bytes):
                        parsed = BytesParser(policy=email.policy.compat32).parsebytes(part[1], headersonly=True)
                        found += [_header(parsed, "In-Reply-To"), _header(parsed, "References")]
    return set(_MESSAGE_ID.findall(" ".join(found)))


_ADDRESS = re.compile(r"[\w.+'-]+@[\w-]+(\.[\w-]+)+")


def sent_to(account: str, wanted: dict[str, float], words: bool = False) -> list[dict]:
    ""



    wanted = {a.casefold(): t for a, t in wanted.items() if _ADDRESS.fullmatch(a or "")}
    if not wanted:
        return []
    _check_account(account)
    out = []
    if mail.uses_google(account):
        address = mail._address_of(account)
        try:
            for addr, since in sorted(wanted.items())[:40]:
                listed = mail.gmail_get(address, "messages", {
                    "labelIds": "SENT", "q": f"{{to:{addr} cc:{addr}}} after:{int(since)}", "maxResults": SENT_SCAN})
                for m in listed.get("messages") or []:
                    meta = mail.gmail_get(address, f"messages/{m['id']}", {"format": "full" if words else "minimal"})
                    at = int(meta.get("internalDate") or 0) / 1000
                    if at > since:
                        out.append({"to": addr, "at": at, **({"text": own_words(
                            mail._gmail_text(meta.get("payload") or {}))} if words else {})})
        except google.GoogleError:
            raise mail.MailError("This mailbox could not refresh.") from None
    else:
        with _imap(account) as client:
            sent = _sent_folder(client)
            if not sent or client.select(sent if sent.startswith('"') else f'"{sent}"', readonly=True)[0] != "OK":
                return []
            for addr, since in sorted(wanted.items())[:40]:
                day = datetime.fromtimestamp(since - 86400, timezone.utc)
                status, data = client.uid("search", None, "SINCE", f"{day.day}-{_MONTHS[day.month - 1]}-{day.year}",
                                          "OR", "TO", f'"{addr}"', "CC", f'"{addr}"')
                uids = sorted(int(u) for u in _hits(data) if u.isdigit())[-SENT_SCAN:] \
                    if status == "OK" else []
                for uid in uids:
                    status, data = client.uid("fetch", str(uid),
                                              "(UID INTERNALDATE BODY.PEEK[]<0.16384>)" if words else "(UID INTERNALDATE)")
                    if status != "OK":
                        continue
                    metadata = b" ".join(p[0] if isinstance(p, tuple) else p for p in data or []
                                         if isinstance(p, (tuple, bytes)))
                    stamp = imaplib.Internaldate2tuple(metadata)
                    if stamp is None or (at := time.mktime(stamp)) <= since:
                        continue
                    raw = next((p[1] for p in data or [] if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
                    out.append({"to": addr, "at": at, **({"text": own_words(_text_of(
                        BytesParser(policy=email.policy.compat32).parsebytes(raw))) if raw else ""} if words else {})})
    return sorted(out, key=lambda m: -m["at"])


def written_to(account: str, addresses: list[str]) -> set[str]:
    ""

    _check_account(account)
    wanted = [a for a in addresses if _ADDRESS.fullmatch(a)][:40]
    if not wanted:
        return set()
    if mail.uses_google(account):
        address, out = mail._address_of(account), set()
        for addr in wanted:
            try:
                page = mail.gmail_get(address, "messages", {"labelIds": "SENT", "q": f"{{to:{addr} cc:{addr}}}",
                                                            "maxResults": 1})
            except google.GoogleError:
                raise mail.MailError("This mailbox could not refresh.") from None
            if page.get("messages"):
                out.add(addr)
        return out
    out = set()
    with _imap(account) as client:
        sent = _sent_folder(client)
        if not sent or client.select(sent if sent.startswith('"') else f'"{sent}"', readonly=True)[0] != "OK":
            return out
        for addr in wanted:
            status, data = client.uid("search", None, "OR", "TO", f'"{addr}"', "CC", f'"{addr}"')
            if status == "OK" and _hits(data):
                out.add(addr)
    return out
