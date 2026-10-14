"""Adding and removing a home's mailboxes and calendars from the app (plan 2026-10-09, section 4, "Her Mail, Calendar
and Board Without Connectors").

Mailboxes, two ways:
- Sign In with Google (google.py): a Gmail address the app signed in. The mailbox is listed with `kind: "google_oauth"`;
  the hub reads it through the Gmail API and the plugin's email tool asks the hub for a token.
- An app password (iCloud Mail, Gmail with an app password, any IMAP account): the hub writes a himalaya account
  into `~/.config/himalaya/config.toml` between `# zero:begin <id>` and `# zero:end <id>` lines, with the password
  in `~/.config/himalaya/<id>.pass` (mode 600), and checks it with `himalaya folder list`. A mailbox that fails the
  check is not kept.

Calendars: `google` (the same Google sign-in), `caldav` (iCloud with an app-specific password, or any CalDAV
server; the password in `~/.config/zero-calendars/<id>.pass`, mode 600) or `ical` (a private address, read only; the
address is a secret, so it lives in `~/.config/zero-calendars/<id>.url`, mode 600, never in home.json).

Each kept mailbox or calendar is listed in home.json (`mail.accounts`, `calendars`), which the plugin's tools and the
hub's noticers read. Every answer is {ok, checked, detail} with `detail` one plain sentence.
"""

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

from . import config, google, home, mail

log = logging.getLogger("taffy.mailboxes")

PRESETS = {
    "icloud": {"imap_host": "imap.mail.me.com", "imap_port": 993, "imap_tls": "tls",
               "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_tls": "start-tls",
               "sent": "Sent Messages", "drafts": "Drafts", "trash": "Deleted Messages", "all_mail": None,
               "label": "iCloud", "help_url": "https://account.apple.com/account/manage"},
    "gmail": {"imap_host": "imap.gmail.com", "imap_port": 993, "imap_tls": "tls",
              "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_tls": "start-tls",
              "sent": "[Gmail]/Sent Mail", "drafts": "[Gmail]/Drafts", "trash": "[Gmail]/Trash",
              "all_mail": "[Gmail]/All Mail", "label": "Gmail",
              "help_url": "https://myaccount.google.com/apppasswords"},
    "other": {"imap_port": 993, "imap_tls": "tls", "smtp_port": 587, "smtp_tls": "start-tls",
              "sent": "Sent", "drafts": "Drafts", "trash": "Trash", "all_mail": None, "label": "Email"},
}
CALDAV_ICLOUD = "https://caldav.icloud.com"
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}")
HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")
LOOPBACK = {"127.0.0.1", "localhost"}
CHECK_TIMEOUT_SEC = 30


class MailboxError(ValueError):
    pass


def himalaya_dir() -> Path:
    return config.HOME / ".config" / "himalaya"


def calendars_dir() -> Path:
    return config.HOME / ".config" / "zero-calendars"


def _secret_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _new_id(base: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]", "", base.lower())[:16] or "mail"
    if not base[0].isalpha():
        base = "m" + base
    cand, n = base, 2
    while cand in taken:
        cand, n = f"{base}{n}", n + 1
    return cand


def _owner(owner_zip: str | None) -> str:
    z = str(owner_zip or "default")
    return z if z in config.BOTS_BY_ID else "default"


# MARK: himalaya's settings

def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return json.dumps(str(v))   # a TOML basic string accepts JSON's escapes


def himalaya_block(acct_id: str, address: str, display: str, p: dict, default: bool) -> str:
    pass_file = himalaya_dir() / f"{acct_id}.pass"
    lines = [f"# zero:begin {acct_id}", f"[accounts.{acct_id}]", f"email = {_toml(address)}",
             f"display-name = {_toml(display)}", f"default = {_toml(default)}", "",
             'backend.type = "imap"', f"backend.host = {_toml(p['imap_host'])}",
             f"backend.port = {_toml(int(p['imap_port']))}", f"backend.encryption.type = {_toml(p['imap_tls'])}",
             f"backend.login = {_toml(p.get('login') or address)}", 'backend.auth.type = "password"',
             f"backend.auth.cmd = {_toml('cat ' + str(pass_file))}", "",
             'message.send.backend.type = "smtp"', f"message.send.backend.host = {_toml(p['smtp_host'])}",
             f"message.send.backend.port = {_toml(int(p['smtp_port']))}",
             f"message.send.backend.encryption.type = {_toml(p['smtp_tls'])}",
             f"message.send.backend.login = {_toml(p.get('login') or address)}",
             'message.send.backend.auth.type = "password"',
             f"message.send.backend.auth.cmd = {_toml('cat ' + str(pass_file))}", "",
             'folder.aliases.inbox = "INBOX"', f"folder.aliases.sent = {_toml(p['sent'])}",
             f"folder.aliases.drafts = {_toml(p['drafts'])}", f"folder.aliases.trash = {_toml(p['trash'])}",
             f"# zero:end {acct_id}", ""]
    return "\n".join(lines)


def _config_file() -> Path:
    return himalaya_dir() / "config.toml"


def _without_block(text: str, acct_id: str) -> str:
    return re.sub(rf"(?ms)^# zero:begin {re.escape(acct_id)}\n.*?^# zero:end {re.escape(acct_id)}\n?", "", text)


def _write_block(acct_id: str, block: str | None) -> None:
    f = _config_file()
    try:
        text = f.read_text()
    except FileNotFoundError:
        text = ""
    text = _without_block(text, acct_id)
    if block:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    _secret_write(f, text)


def check_himalaya(acct_id: str) -> tuple[bool, str]:
    """`himalaya folder list` for the account: (worked, a plain sentence)."""
    try:
        out = mail.run(["folder", "list", "-a", acct_id, "-o", "json"])
    except subprocess.TimeoutExpired:
        return False, "The mail server took too long to answer. Try again in a minute."
    except OSError:
        return False, "Mail isn't set up on this computer yet."
    if out.returncode == 0:
        return True, "Connected. Your Zips can read this mailbox."
    err = (out.stderr or "").lower()
    if any(w in err for w in ("authentication", "auth", "login", "password", "credentials", "invalid")):
        return False, "The mail server did not accept that password. Check the app password and try again."
    if any(w in err for w in ("resolve", "dns", "connect", "connection", "timed out", "network", "tls")):
        return False, "The mail server could not be reached. Check the server name and try again."
    return False, "The mailbox could not be opened. Check the address and password and try again."


# MARK: Mailboxes

def _accounts_raw() -> list[dict]:
    return [dict(a) for a in (home.get("mail.accounts") or []) if isinstance(a, dict)]


def _save_accounts(accts: list[dict]) -> None:
    if accts and not any(a.get("default") for a in accts):
        accts[0]["default"] = True
    home.save({"mail": {"accounts": accts}})


def describe_mailbox(a: dict) -> dict:
    out = {k: a.get(k) for k in ("id", "address", "label", "provider", "default", "owner_zip")}
    out["auth"] = "google" if home.is_google(a) else "password"
    out["kind"] = "google_oauth" if home.is_google(a) else "imap"
    if out["auth"] == "google":
        out["provider"] = "google"
        g = google.describe(a["address"])
        out.update(state=g["state"], detail=g["detail"] if g["state"] != google.CONNECTED else
                   "Connected. Your Zips can read this mailbox.")
    else:
        out.update(state="connected", detail="Connected. Your Zips can read this mailbox.")
    return out


def mailboxes() -> list[dict]:
    return [describe_mailbox(a) for a in home.mail_accounts()]


def add_password_mailbox(body: dict) -> dict:
    """POST /api/accounts/mailboxes. Writes, checks, and keeps the mailbox only when the check passed."""
    address = str(body.get("address") or "").strip()
    provider = str(body.get("provider") or "other").strip().lower()
    password = str(body.get("password") or "")
    if not ADDRESS.fullmatch(address):
        return {"ok": False, "checked": False, "detail": "That doesn't look like an email address."}
    if provider not in PRESETS:
        return {"ok": False, "checked": False, "detail": "Pick iCloud, Gmail or Other."}
    if not password.strip() or len(password) > 200 or "\n" in password:
        return {"ok": False, "checked": False, "detail": "Enter the app password for this mailbox."}
    p = dict(PRESETS[provider])
    for side in ("imap", "smtp"):
        given = body.get(side)
        if isinstance(given, dict):
            body = {**body, f"{side}_host": given.get("host") or body.get(f"{side}_host"),
                    f"{side}_port": given.get("port") or body.get(f"{side}_port")}
    for k in ("imap_host", "smtp_host"):
        if body.get(k):
            p[k] = str(body[k]).strip()
    for k in ("imap_port", "smtp_port"):
        if body.get(k):
            try:
                p[k] = int(body[k])
            except (TypeError, ValueError):
                return {"ok": False, "checked": False, "detail": "The port should be a number."}
    if not p.get("imap_host") or not p.get("smtp_host") or not HOST.fullmatch(p["imap_host"]) \
            or not HOST.fullmatch(p["smtp_host"]):
        return {"ok": False, "checked": False, "detail": "Enter the incoming and outgoing mail server names."}
    for side in ("imap", "smtp"):
        if p[f"{side}_host"].lower() in LOOPBACK:
            p[f"{side}_tls"] = "none"
    accts = _accounts_raw()
    if any(a.get("address", "").lower() == address.lower() for a in accts):
        return {"ok": False, "checked": False, "detail": "That mailbox is already added."}
    acct_id = _new_id(address.split("@")[0] if provider == "other" else provider, {a["id"] for a in accts})
    label = " ".join(str(body.get("label") or p["label"]).split())[:40]
    from . import who
    _secret_write(himalaya_dir() / f"{acct_id}.pass", password)
    _write_block(acct_id, himalaya_block(acct_id, address, who.full(), p, default=not accts))
    ok, detail = check_himalaya(acct_id)
    if not ok:
        _write_block(acct_id, None)
        _unlink(himalaya_dir() / f"{acct_id}.pass")
        return {"ok": False, "checked": False, "detail": detail}
    entry = {"id": acct_id, "address": address, "label": label, "default": not accts,
             "owner_zip": _owner(body.get("owner_zip")), "provider": provider, "kind": "imap"}
    if p.get("all_mail"):
        entry["all_mail_folder"] = p["all_mail"]
    accts.append(entry)
    _save_accounts(accts)
    log.info("mailboxes: added %s (%s)", acct_id, provider)
    return {"ok": True, "checked": True, "detail": detail, "mailbox": describe_mailbox(entry)}


def add_google_mailbox(address: str, owner_zip: str | None = None) -> dict:
    """The Gmail address the person just signed in with Google, as a mailbox. Checked through the Gmail API."""
    accts = _accounts_raw()
    existing = next((a for a in accts if a.get("address", "").lower() == address.lower()), None)
    try:
        google.check_mail(address)
    except google.GoogleError as e:
        return {"ok": False, "checked": False, "detail": str(e)}
    if existing:
        existing.update(kind="google_oauth", provider="gmail")
        existing.pop("auth", None)
        existing.setdefault("all_mail_folder", PRESETS["gmail"]["all_mail"])
        entry = existing
    else:
        entry = {"id": _new_id("gmail", {a["id"] for a in accts}), "address": address, "label": "Gmail",
                 "default": not accts, "owner_zip": _owner(owner_zip), "provider": "gmail", "kind": "google_oauth",
                 "all_mail_folder": PRESETS["gmail"]["all_mail"]}
        accts.append(entry)
    _save_accounts(accts)
    return {"ok": True, "checked": True, "detail": "Connected. Your Zips can read this mailbox.",
            "mailbox": describe_mailbox(entry)}


def remove_mailbox(acct_id: str) -> dict:
    accts = _accounts_raw()
    entry = next((a for a in accts if a.get("id") == acct_id), None)
    if not entry:
        return {"ok": False, "checked": False, "detail": "That mailbox isn't here."}
    accts = [a for a in accts if a.get("id") != acct_id]
    _save_accounts(accts)
    if home.is_google(entry):
        _forget_google_if_unused(entry["address"])
    else:
        _write_block(acct_id, None)
        _unlink(himalaya_dir() / f"{acct_id}.pass")
    log.info("mailboxes: removed %s", acct_id)
    return {"ok": True, "checked": False, "detail": "Removed. Your Zips no longer read this mailbox."}


# MARK: Calendars

def _calendars_raw() -> list[dict]:
    return [dict(c) for c in (home.get("calendars") or []) if isinstance(c, dict)]


def describe_calendar(c: dict) -> dict:
    out = {k: c.get(k) for k in ("id", "kind", "label", "username", "account")}
    url = str(c.get("url") or "")
    out["provider"] = {"google": "google", "google_oauth": "google", "ical": "link"}.get(
        str(c.get("kind")), "icloud" if url.startswith(
        CALDAV_ICLOUD) else "other")
    if c.get("kind") == "caldav":
        out["url"] = c.get("url")
    if home.is_google_calendar(c):
        out["address"] = out["account"] = home.calendar_address(c)
        g = google.describe(home.calendar_address(c))
        out.update(state=g["state"], detail=g["detail"] if g["state"] != google.CONNECTED else
                   "Connected. Your Zips can read this calendar.")
    else:
        out.update(state="connected", detail="Connected. Your Zips can read this calendar."
                   if c.get("kind") == "caldav" else "Connected. Your Zips can read this calendar but not change it.")
    return out


def calendars() -> list[dict]:
    return [describe_calendar(c) for c in home.calendars()]


def http_request(method: str, url: str, **kw) -> httpx.Response:
    """One request to a calendar server. Tests replace this."""
    with httpx.Client(timeout=CHECK_TIMEOUT_SEC, follow_redirects=True) as c:
        return c.request(method, url, **kw)


PROPFIND_PRINCIPAL = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop>'
                      '<d:current-user-principal/></d:prop></d:propfind>')


def check_caldav(url: str, username: str, password: str) -> tuple[bool, str]:
    try:
        r = http_request("PROPFIND", url, auth=(username, password), content=PROPFIND_PRINCIPAL,
                         headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"})
    except httpx.HTTPError:
        return False, "The calendar server could not be reached. Check the address and try again."
    if r.status_code in (200, 207):
        return True, "Connected. Your Zips can read this calendar."
    if r.status_code in (401, 403):
        return False, "The calendar server did not accept that password. Check the app password and try again."
    return False, "That address didn't open a calendar. Check it and try again."


def check_ical(url: str) -> tuple[bool, str]:
    try:
        r = http_request("GET", url)
    except httpx.HTTPError:
        return False, "That calendar address could not be reached. Check it and try again."
    if r.status_code == 200 and "BEGIN:VCALENDAR" in r.text[:2000]:
        return True, "Connected. Your Zips can read this calendar but not change it."
    return False, "That address didn't open a calendar. Copy the private address again and try again."


def add_calendar(body: dict) -> dict:
    """POST /api/accounts/calendars: {kind: caldav | ical | google, ...}."""
    kind = str(body.get("kind") or "").strip().lower()
    if not kind and str(body.get("provider") or "") == "link":
        kind = "ical"
    if not kind and str(body.get("provider") or "") in ("icloud", "other"):
        kind = "caldav"
    cals = _calendars_raw()
    taken = {c.get("id") for c in cals}
    label = " ".join(str(body.get("label") or "").split())[:40]
    if kind in ("google", "google_oauth"):
        address = str(body.get("account") or body.get("address") or "").strip().lower()
        if google.state(address) == google.MISSING:
            return {"ok": False, "checked": False, "detail": "Sign in with Google first."}
        return add_google_calendar(address)
    if kind == "caldav":
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        url = str(body.get("url") or CALDAV_ICLOUD).strip()
        if not username or not password.strip() or "\n" in password:
            return {"ok": False, "checked": False, "detail": "Enter the account name and the app password."}
        if not url.startswith("https://"):
            return {"ok": False, "checked": False, "detail": "The calendar address should start with https://."}
        ok, detail = check_caldav(url, username, password)
        if not ok:
            return {"ok": False, "checked": False, "detail": detail}
        cid = _new_id("icloud" if url.startswith(CALDAV_ICLOUD) else "caldav", taken)
        _secret_write(calendars_dir() / f"{cid}.pass", password)
        entry = {"id": cid, "kind": "caldav", "url": url, "username": username,
                 "password_file": f"~/.config/zero-calendars/{cid}.pass",
                 "label": label or ("iCloud" if url.startswith(CALDAV_ICLOUD) else "Calendar")}
    elif kind == "ical":
        url = str(body.get("url") or "").strip()
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if not url.startswith("https://"):
            return {"ok": False, "checked": False, "detail": "Paste the calendar's private address."}
        ok, detail = check_ical(url)
        if not ok:
            return {"ok": False, "checked": False, "detail": detail}
        cid = _new_id("ical", taken)
        _secret_write(calendars_dir() / f"{cid}.url", url)
        entry = {"id": cid, "kind": "ical", "label": label or "Calendar", "url": None,
                 "url_file": f"~/.config/zero-calendars/{cid}.url"}
    else:
        return {"ok": False, "checked": False, "detail": "Pick Google, iCloud or a calendar address."}
    cals.append(entry)
    home.save({"calendars": cals})
    log.info("calendars: added %s (%s)", entry["id"], kind)
    return {"ok": True, "checked": True, "detail": detail, "calendar": describe_calendar(entry)}


def add_google_calendar(address: str) -> dict:
    cals = _calendars_raw()
    try:
        google.check_calendar(address)
    except google.GoogleError as e:
        return {"ok": False, "checked": False, "detail": str(e)}
    entry = next((c for c in cals if home.is_google_calendar(c) and home.calendar_address(c) == address), None)
    if not entry:
        entry = {"id": _new_id("google", {c.get("id") for c in cals}), "kind": "google_oauth", "address": address,
                 "calendar_id": "primary", "label": "Google Calendar"}
        cals.append(entry)
        home.save({"calendars": cals})
    return {"ok": True, "checked": True, "detail": "Connected. Your Zips can read this calendar.",
            "calendar": describe_calendar(entry)}


def remove_calendar(cal_id: str) -> dict:
    cals = _calendars_raw()
    entry = next((c for c in cals if c.get("id") == cal_id), None)
    if not entry:
        return {"ok": False, "checked": False, "detail": "That calendar isn't here."}
    home.save({"calendars": [c for c in cals if c.get("id") != cal_id]})
    _unlink(calendars_dir() / f"{cal_id}.pass")
    _unlink(calendars_dir() / f"{cal_id}.url")
    if home.is_google_calendar(entry):
        _forget_google_if_unused(home.calendar_address(entry))
    return {"ok": True, "checked": False, "detail": "Removed. Your Zips no longer read this calendar."}


# MARK: Google

def _forget_google_if_unused(address: str) -> None:
    """Disconnects the Google account once no mailbox, calendar or Google Drive connection uses it."""
    a = (address or "").lower()
    from . import apps
    if apps.drive_address() == a:
        return
    if any(home.is_google(m) and m["address"].lower() == a for m in home.mail_accounts()):
        return
    if any(home.is_google_calendar(c) and home.calendar_address(c) == a for c in home.calendars()):
        return
    try:
        google.disconnect(a)
    except google.GoogleError:
        pass


def connect_google(body: dict) -> dict:
    """POST /api/accounts/google: the app's code and verifier. Adds the Gmail mailbox and the Google calendar
    the person allowed (`use` limits it to "mail" or "calendar"). `use` ["drive"] is Connect Apps' Google Drive: the
    sign-in asked for Drive only (with the scopes granted before), and no mailbox or calendar is added for it."""
    use = body.get("use") or ["mail", "calendar"]
    use = {u for u in use if u in ("mail", "calendar", "drive")} if isinstance(use, list) else {"mail", "calendar"}
    if use == {"drive"}:
        from . import apps
        return apps.connect_drive(body)
    try:
        acct = google.exchange(str(body.get("code") or ""), str(body.get("code_verifier") or ""),
                               body.get("redirect_uri") or None, body.get("client_id") or None)
    except google.GoogleError as e:
        return {"ok": False, "checked": False, "detail": str(e)}
    address = acct["address"]
    out = {"ok": True, "checked": True, "id": address, "address": address, "google": google.describe(address),
           "mailbox": None, "calendar": None}
    notes = []
    if "mail" in use:
        if acct["mail"]:
            r = add_google_mailbox(address, body.get("owner_zip"))
            out["mailbox"] = r.get("mailbox")
            if not r["ok"]:
                out["checked"] = False
                notes.append(r["detail"])
        else:
            notes.append("Google didn't give Zero access to your mail. Sign in again and allow mail.")
    if "calendar" in use:
        if acct["calendar"]:
            r = add_google_calendar(address)
            out["calendar"] = r.get("calendar")
            if not r["ok"]:
                out["checked"] = False
                notes.append(r["detail"])
        else:
            notes.append("Google didn't give Zero access to your calendar. Sign in again and allow the calendar.")
    if not out["mailbox"] and not out["calendar"]:
        _forget_google_if_unused(address)
        out["ok"] = False
        out["checked"] = False
    out["detail"] = " ".join(notes) if notes else f"Connected {address}. Your Zips can use it."
    return out


def disconnect_google(address: str) -> dict:
    """DELETE /api/accounts/google/{address}: removes its mailbox and calendar, revokes and forgets the tokens."""
    a = (address or "").strip().lower()
    accts = [m for m in _accounts_raw() if not (home.is_google(m) and m.get("address", "").lower() == a)]
    cals = [c for c in _calendars_raw() if not (home.is_google_calendar(c) and home.calendar_address(c) == a)]
    if len(accts) != len(_accounts_raw()):
        _save_accounts(accts)
    if len(cals) != len(_calendars_raw()):
        home.save({"calendars": cals})
    try:
        gone = google.disconnect(a)
    except google.GoogleError:
        gone = False
    return {"ok": True, "checked": False, "detail": "Disconnected. Your Zips no longer use this Google account."
            if gone else "That Google account wasn't connected here."}


def status() -> dict:
    """For /api/accounts and the app's Mailbox and Calendar screens."""
    return {"mailboxes": mailboxes(), "calendars": calendars(),
            "google": {**google.setup(), "accounts": [google.describe(a) for a in google.accounts()]},
            "presets": {k: {"label": v["label"], "help_url": v.get("help_url")} for k, v in PRESETS.items()},
            "at": time.time()}
