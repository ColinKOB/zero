""



















import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import threading
import time
import urllib.parse
from pathlib import Path

import httpx

from . import config, home, store

log = logging.getLogger("taffy.zipmail")

FOLDERS = ("INBOX", "Sent", "Drafts", "Trash")
SYNC_SEC = 30
IDLE_SEC = 600

WARM_SEC = 600
RETRY_SEC, RETRY_MAX_SEC = 60, 3600
AUTO_OFF = "zipmail_auto_off"
PAGE = 20
PAGES_PER_PASS = 10
TIMEOUT_SEC = 30
SEEN_DAYS = 30
ACCOUNT_ID = re.compile(r"zip-[a-z0-9]{1,20}-[a-z2-9]{6}")
ADDRESS = re.compile(r"[a-z0-9]{1,20}-[a-z2-9]{6}@zip\.heyzero\.app")

_lock = threading.Lock()
_sync_lock = threading.Lock()
_auto_lock = threading.Lock()


class ZipMailError(RuntimeError):
    ""


def settings_file() -> Path:
    return config.HOME / ".config" / "zero" / "zip-mail.json"


def maildir_root() -> Path:
    return config.HOME / ".local" / "share" / "zero-zip-mail"


def send_bin() -> Path:
    return config.HOME / "taffy" / "bin" / "zipmail-send"


def settings() -> dict | None:
    try:
        s = json.loads(settings_file().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(s, dict) or not str(s.get("url") or "").strip() or not str(s.get("token") or "").strip():
        return None
    return {"url": str(s["url"]).strip().rstrip("/"), "token": str(s["token"]).strip()}


def set_up() -> bool:
    ""
    return settings() is not None and os.access(send_bin(), os.X_OK)


def http(method: str, url: str, **kw) -> httpx.Response:
    ""
    with httpx.Client(timeout=TIMEOUT_SEC) as c:
        return c.request(method, url, **kw)


def _request(method: str, path: str, body=None, ok_statuses=(200,)) -> httpx.Response:
    s = settings()
    if not s:
        raise ZipMailError("Zip mail isn't set up in this home.")
    try:
        r = http(method, s["url"] + path, headers={"Authorization": f"Bearer {s['token']}"},
                 **({"json": body} if body is not None else {}))
    except httpx.HTTPError:
        raise ZipMailError("Zero's mail service could not be reached just now. Try again in a minute.") from None
    if r.status_code not in ok_statuses:
        try:
            said = str((r.json() or {}).get("error") or "")
        except (ValueError, AttributeError):
            said = ""
        raise ZipMailError(said or f"Zero's mail service answered {r.status_code}.")
    return r


def _json(method: str, path: str, body=None):
    try:
        return _request(method, path, body).json()
    except ValueError:
        raise ZipMailError("Zero's mail service gave an answer this home could not read.") from None




def registry() -> list[dict]:
    return home.zip_mail_registry()


def _write_registry(rows: list[dict]) -> None:
    from . import mailboxes
    f = home.zip_mail_file()
    if f is None:
        raise ZipMailError("This home has no place to keep Zip mail addresses.")
    mailboxes._secret_write(f, json.dumps(rows, indent=2) + "\n")


def account_of(bot_id: str) -> dict | None:
    return next((r for r in registry() if r["owner_zip"] == bot_id), None)


def entry(acct_id: str) -> dict | None:
    return next((r for r in registry() if r["id"] == acct_id), None)


def is_zip_mail(acct_id: str) -> bool:
    return entry(acct_id) is not None




def himalaya_block(acct_id: str, address: str, display: str) -> str:
    from . import mailboxes
    t = mailboxes._toml
    root = maildir_root() / acct_id
    cmd = f"{shlex.quote(str(send_bin()))} --account {acct_id}"
    reg = home.zip_mail_file()
    if reg is not None:
        cmd = f"{home.ZIP_MAIL_ENV}={shlex.quote(str(reg))} {cmd}"
    lines = [f"# zero:begin {acct_id}", f"[accounts.{acct_id}]", f"email = {t(address)}",
             f"display-name = {t(display)}", "default = false", "",
             'backend.type = "maildir"', f"backend.root-dir = {t(str(root))}", "backend.maildirpp = false", "",
             'message.send.backend.type = "sendmail"', f"message.send.backend.cmd = {t(cmd)}",
             "message.send.save-copy = true", "",
             'folder.aliases.inbox = "INBOX"', 'folder.aliases.sent = "Sent"', 'folder.aliases.drafts = "Drafts"',
             'folder.aliases.trash = "Trash"', f"# zero:end {acct_id}", ""]
    return "\n".join(lines)


def _make_maildir(acct_id: str) -> None:
    root = maildir_root() / acct_id
    for folder in FOLDERS:
        for sub in ("cur", "new", "tmp"):
            (root / folder / sub).mkdir(parents=True, exist_ok=True)
    for p in [maildir_root(), root, *(root / f for f in FOLDERS)]:
        os.chmod(p, 0o700)


def _ensure_block(acct_id: str, address: str, display: str) -> None:
    from . import mailboxes
    block = himalaya_block(acct_id, address, display)
    try:
        if block in mailboxes._config_file().read_text():
            return
    except OSError:
        pass
    mailboxes._write_block(acct_id, block)




def create(bot_id: str, ahead: bool = False) -> dict | None:
    ""



    if not settings():
        raise ZipMailError("Zip mail isn't set up in this home.")
    config.reload_bots()
    bot = config.BOTS_BY_ID.get(bot_id)
    if not bot:
        raise ZipMailError("That Zip isn't on the team.")
    if config.is_sandbox(bot_id):
        raise ZipMailError("This test Zip has no mailbox of its own.")
    if not os.access(send_bin(), os.X_OK):

        raise ZipMailError("Zip mail isn't fully installed on this computer yet.")


    with _sync_lock, _lock:
        if bot_id not in config.BOTS_BY_ID:
            raise ZipMailError("That Zip isn't on the team.")
        if ahead and bot_id in _off():
            return None
        row = account_of(bot_id)
        if row is None:
            got = _json("POST", "/addresses", {"zip": bot_id, "name": bot.name})
            address = str((got or {}).get("address") or "").strip().lower() if isinstance(got, dict) else ""
            if not ADDRESS.fullmatch(address):
                raise ZipMailError("Zero's mail service gave an address this home could not use.")
            row = {"id": "zip-" + address.split("@")[0], "address": address, "owner_zip": bot_id,
                   "created": time.time()}
        if not ACCOUNT_ID.fullmatch(row["id"]):
            raise ZipMailError("This Zip's address could not be set up here.")
        _make_maildir(row["id"])
        _ensure_block(row["id"], row["address"], bot.name)
        if entry(row["id"]) is None:
            _write_registry(registry() + [row])
        if not ahead:
            _auto(bot_id, True)
    log.info("zipmail: %s has %s", bot_id, row["id"])
    return {"address": row["address"], "account": row["id"],
            "just_made": time.time() - float(row.get("created") or 0) < WARM_SEC}


def _off() -> set[str]:
    return set(store.get_setting(AUTO_OFF, []) or [])


def _auto(bot_id: str, on: bool) -> None:
    ""

    with _auto_lock:
        off = _off()
        if (bot_id not in off) == on:
            return
        store.set_setting(AUTO_OFF, sorted(off - {bot_id} if on else off | {bot_id}))


def _all_off(bot_ids) -> None:
    with _auto_lock:
        store.set_setting(AUTO_OFF, sorted(_off() | set(bot_ids)))


_retry: dict[str, tuple[int, float]] = {}


_blocks_fresh = False


def _refresh_blocks() -> None:
    ""

    global _blocks_fresh
    if _blocks_fresh:
        return
    with _sync_lock, _lock:
        for row in registry():
            bot = config.BOTS_BY_ID.get(row["owner_zip"])
            try:
                _ensure_block(row["id"], row["address"], bot.name if bot else row["owner_zip"])
            except OSError:
                log.warning("zipmail: could not refresh %s's mail settings", row["id"])
                return
    _blocks_fresh = True


def provision_once() -> str | None:
    ""

    if not set_up():
        return None
    config.reload_bots()
    _refresh_blocks()
    have = {r["owner_zip"] for r in registry()}
    off = _off()
    now = time.time()
    for bot in config.BOTS:
        fails, after = _retry.get(bot.id, (0, 0.0))
        if bot.id in have or bot.id in off or after > now or config.is_sandbox(bot.id):
            continue
        try:
            if create(bot.id, ahead=True) is None:
                continue
        except Exception as e:
            wait = min(RETRY_SEC * 2 ** fails, RETRY_MAX_SEC)
            _retry[bot.id] = (fails + 1, now + wait)
            log.warning("zipmail: could not make %s's address (%s). Trying again in %d minutes.",
                        bot.id, e, max(1, wait // 60))
            return None
        _retry.pop(bot.id, None)
        log.info("zipmail: made %s's address ahead of time", bot.id)
        return bot.id
    return None


def retire_zip(bot_id: str) -> None:
    ""


    with _sync_lock, _lock:
        row = account_of(bot_id)
    if row:
        try:
            out = retire(row["id"])
        except Exception as e:
            out = {"ok": False, "detail": str(e)}
        if not out.get("ok"):
            log.warning("zipmail: %s was deleted but its address %s stays: %s", bot_id, row["address"],
                        out.get("detail"))
            return
    _auto(bot_id, True)


def retire(acct_id: str) -> dict:
    ""

    row = entry(acct_id)
    if not row:
        return {"ok": False, "checked": False, "detail": "That mailbox isn't here."}
    if not settings():
        return {"ok": False, "checked": False,
                "detail": "Zip mail isn't set up in this home, so this address can't be removed yet."}
    try:
        sync_once()
    except ZipMailError:
        log.warning("zipmail: the last copy before retiring %s failed", acct_id)
    from . import mailboxes, waits
    with _sync_lock:
        _auto(row["owner_zip"], False)
        try:
            _request("DELETE", "/addresses/" + urllib.parse.quote(row["address"], safe=""), ok_statuses=(200, 404))
        except ZipMailError as e:
            return {"ok": False, "checked": False, "detail": str(e)}

        try:
            shutil.rmtree(maildir_root() / acct_id)
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("zipmail: could not delete the mailbox of %s", acct_id, exc_info=True)
            return {"ok": False, "checked": False,
                    "detail": "The address no longer gets mail, but its saved mail could not be deleted. Try again."}
        with _lock:
            mailboxes._write_block(acct_id, None)
            _write_registry([r for r in registry() if r["id"] != acct_id])
        with store._lock:
            store.db().execute("DELETE FROM zipmail_seen WHERE account = ?", (acct_id,))
            store.db().commit()
    waits.close_for_mailbox(acct_id, None, "The mailbox was removed.")
    log.info("zipmail: retired %s", acct_id)
    return {"ok": True, "checked": False, "detail": "Removed. This address no longer gets mail."}


def retire_all() -> tuple[int, int]:
    ""



    config.reload_bots()
    _all_off(b.id for b in config.BOTS)
    with _lock:
        pass
    gone = 0
    for _ in range(3):
        rows = registry()
        if not rows:
            break
        for r in rows:
            try:
                gone += bool(retire(r["id"])["ok"])
            except Exception:
                log.warning("zipmail: could not retire %s", r["id"], exc_info=True)
    return gone, len(registry()) + retire_unregistered()


def retire_unregistered() -> int:
    ""



    with _sync_lock:
        return _retire_held()


def _retire_held() -> int:
    held = [r["address"] for r in store.db().execute("SELECT address FROM zipmail_held").fetchall()]
    if not held:
        return 0
    try:
        shutil.rmtree(maildir_root() / ".held")
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("zipmail: could not delete the held mail", exc_info=True)
        return len(held)
    left = 0
    for address in held:
        try:
            _request("DELETE", "/addresses/" + urllib.parse.quote(address, safe=""), ok_statuses=(200, 404))
        except ZipMailError:
            left += 1
            continue
        with store._lock:
            store.db().execute("DELETE FROM zipmail_held WHERE address = ?", (address,))
            store.db().commit()
    with store._lock:
        store.db().execute("DELETE FROM zipmail_seen WHERE account = ?", (KEEP,))
        store.db().commit()
    return left




def _seen(key: str) -> bool:
    return store.db().execute("SELECT 1 FROM zipmail_seen WHERE key = ?", (key,)).fetchone() is not None


def _record(key: str, acct_id: str) -> None:
    with store._lock:
        store.db().execute("INSERT OR IGNORE INTO zipmail_seen (key, account, at) VALUES (?, ?, ?)",
                           (key, acct_id, time.time()))
        store.db().commit()


def file_name(key: str) -> str:
    ""
    tail = key.rsplit("/", 1)[-1]
    m = re.match(r"(\d{13})-", tail)
    received = int(m.group(1)) // 1000 if m else int(time.time())
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", tail.removesuffix(".eml"))[:120] or "message"
    return f"{received}.{safe}.zipmail"


def _on_disk(inbox: Path, name: str) -> bool:
    for sub in ("new", "cur"):
        try:
            if any(f.split(":", 1)[0] == name for f in os.listdir(inbox / sub)):
                return True
        except FileNotFoundError:
            pass
    return False


def _deliver(acct_id: str, key: str, raw: bytes) -> None:
    inbox = maildir_root() / acct_id / "INBOX"
    if not (inbox / "new").is_dir():
        _make_maildir(acct_id)
    _write_new(inbox, file_name(key), raw)


def _hold(key: str, raw: bytes) -> None:
    ""

    held = maildir_root() / ".held"
    for sub in ("cur", "new", "tmp"):
        (held / sub).mkdir(parents=True, exist_ok=True)
    os.chmod(held, 0o700)
    _write_new(held, file_name(key), raw)


def _write_new(inbox: Path, name: str, raw: bytes) -> None:
    if _on_disk(inbox, name):
        return
    tmp = inbox / "tmp" / name
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, inbox / "new" / name)

    fd = os.open(inbox / "new", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


KEEP = "keep"


def _owner_row(address: str, rows: dict[str, dict | str | None]) -> dict | str | None:
    ""



    if address not in rows:
        rows[address] = _look_up(address)
    return rows[address]


def _look_up(address: str) -> dict | str | None:
    with _lock:
        row = next((r for r in registry() if r["address"].lower() == address), None)
        if row is not None:
            return row
        live = next((a for a in _json("GET", "/addresses") or [] if isinstance(a, dict)
                     and str(a.get("address") or "").lower() == address), None)
        if live is None:
            return None
        config.reload_bots()
        bot = config.BOTS_BY_ID.get(str(live.get("zip") or ""))
        if not bot or not ADDRESS.fullmatch(address) or account_of(bot.id) is not None:
            return KEEP
        row = {"id": "zip-" + address.split("@")[0], "address": address, "owner_zip": bot.id,
               "created": time.time()}
        _make_maildir(row["id"])
        _ensure_block(row["id"], address, bot.name)
        _write_registry(registry() + [row])
        log.info("zipmail: adopted %s for %s", row["id"], bot.id)
        return row


def sync_once() -> int:
    ""
    if not settings():
        return 0
    written = 0
    with _sync_lock:

        by_address: dict[str, dict | str | None] = {r["address"].lower(): r for r in registry()}
        for _ in range(PAGES_PER_PASS):
            items = _json("GET", f"/inbox?limit={PAGE}")
            if not isinstance(items, list) or not items:
                break
            done = []
            for it in items:
                key = str((it or {}).get("key") or "") if isinstance(it, dict) else ""
                if not key:
                    continue
                if _seen(key):
                    done.append(key)
                    continue
                row = _owner_row(str(it.get("address") or "").lower(), by_address)
                if row is None:


                    log.warning("zipmail: dropping %s, for an address this home retired", key)
                    done.append(key)
                    continue
                try:
                    raw = _request("GET", "/inbox/" + urllib.parse.quote(key, safe="")).content
                except ZipMailError:
                    log.warning("zipmail: could not fetch %s; trying again next pass", key)
                    continue
                if row == KEEP:
                    log.warning("zipmail: %s is for a live address with no Zip here; held on disk", key)
                    with store._lock:
                        store.db().execute("INSERT OR IGNORE INTO zipmail_held (address, at) VALUES (?, ?)",
                                           (str(it.get("address") or "").lower(), time.time()))
                        store.db().commit()
                    _hold(key, raw)
                    _record(key, KEEP)
                    done.append(key)
                    continue
                _deliver(row["id"], key, raw)
                _record(key, row["id"])
                written += 1
                done.append(key)
            if done:
                _json("POST", "/ack", {"keys": done})
            if len(items) < PAGE:
                break
        with store._lock:
            store.db().execute("DELETE FROM zipmail_seen WHERE at < ?", (time.time() - SEEN_DAYS * 86400,))
            store.db().commit()
    if written:
        log.info("zipmail: %d new message(s)", written)
    return written


async def run() -> None:
    ""
    while True:
        if settings():
            for step in (provision_once, sync_once):
                try:
                    await asyncio.to_thread(step)
                except Exception:
                    log.exception("zipmail: a pass failed")
        await asyncio.sleep(SYNC_SEC if registry() else IDLE_SEC)
