""





import hashlib
import ipaddress
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from websockets.exceptions import ConnectionClosed

from . import cdp_storage, config, consent, screen, signin_access as access, store

log = logging.getLogger("taffy.shared_sessions")
_locks = {}
_locks_guard = threading.Lock()
_store_lock = threading.RLock()
KEY_FILE = "shared-sessions.key"
MAX_BYTES = 8 * 1024 * 1024


@store.schema
def _schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shared_sessions (
            site TEXT PRIMARY KEY, generation INTEGER NOT NULL, source_bot TEXT NOT NULL, updated REAL NOT NULL,
            digest TEXT NOT NULL, sealed BLOB, origins TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0,
            authorised INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS shared_session_applied (
            bot_id TEXT NOT NULL, site TEXT NOT NULL, generation INTEGER NOT NULL,
            instance TEXT NOT NULL,
            PRIMARY KEY (bot_id, site)
        );
    """)


def _site(url):
    ""



    host = consent.site_of(url).strip("[]")
    if host == "localhost" or host.endswith(".localhost") or host.rpartition(".")[2].isdigit():


        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        return consent.base(host)


def _lock(bot):
    with _locks_guard:
        return _locks.setdefault(bot, threading.RLock())


def _profile(bot):
    if bot not in config.BOTS_BY_ID:
        raise ValueError("Unknown Zip.")
    return screen.profile_home(bot) / "bot-desktop" / "browser-profile"


def _human(bot):
    try:
        return screen.status(bot).get("holder") == "human"
    except ImportError:
        return False
    except Exception:
        return True


def _challenges(bot):
    sites = set()
    for row in store.db().execute("SELECT body FROM feed WHERE kind='login' AND bot_id=?", (bot,)):
        try:
            card = json.loads(row[0] or "{}")
        except ValueError:
            continue
        if not isinstance(card, dict):
            continue
        if card.get("state") == "open" and card.get("purpose") == "challenge":
            sites.add(consent.base(card.get("site") or ""))
    return sites


def _key():
    path = config.HUB_DIR / KEY_FILE
    try:
        return path.read_bytes()
    except FileNotFoundError:
        data = AESGCM.generate_key(bit_length=256)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        return data


def _row(site):
    return store.db().execute("SELECT * FROM shared_sessions WHERE site=?", (site,)).fetchone()


def _snapshot(row):
    if row["removed"]:
        return {"cookies": [], "origins": {o: {} for o in json.loads(row["origins"])}}
    sealed = row["sealed"]
    if sealed is None:
        return None
    with _store_lock:
        try:
            return json.loads(AESGCM(_key()).decrypt(sealed[:12], sealed[12:], row["site"].encode()))
        except (InvalidTag, ValueError):

            conn = store.db()
            in_transaction = conn.in_transaction
            conn.execute("UPDATE shared_sessions SET sealed=NULL, digest='' "
                         "WHERE site=? AND generation=? AND sealed=? AND removed=0",
                         (row["site"], row["generation"], sealed))

            if not in_transaction:
                conn.commit()
            return None


def _may(bot, site):
    ""








    permitted = access.allowed(bot, site)
    return permitted and not config.is_sandbox(bot)


def _version(site):
    row = access._row(site)
    return row["version"] if row is not None else 1


def _generation(bot, site):
    row = store.db().execute("SELECT generation, instance FROM shared_session_applied WHERE bot_id=? AND site=?", (bot, site)).fetchone()
    return row[0] if row and row[1] == _instance(bot) else 0


def _instance(bot):
    try:
        return (_profile(bot) / "DevToolsActivePort").read_text().splitlines()[1]
    except (OSError, IndexError):
        return ""


def _applied(bot, site, generation):
    with store.db() as conn:
        conn.execute("INSERT OR REPLACE INTO shared_session_applied VALUES (?,?,?,?)", (bot, site, generation, _instance(bot)))


def _save(bot, site, snapshot, force=False, authorised=False):
    snapshot["cookies"].sort(key=lambda c: (c.get("domain", ""), c.get("path", ""), c.get("name", ""),
                                           json.dumps(c.get("partitionKey"), sort_keys=True)))
    plain = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    if len(plain) > MAX_BYTES:
        raise ValueError("The saved sign-in is too large to share.")
    digest = hashlib.sha256(plain).hexdigest()
    with access.lock, _store_lock:
        if not _row(site) and not force and (not authorised or not (snapshot["cookies"] or any(snapshot["origins"].values()))):
            return False
        nonempty = snapshot["cookies"] or any(snapshot["origins"].values())
        if force and nonempty:
            access.complete_human_signin(site, bot)
        elif force and not access.allowed(bot, site):
            return False
        access.ensure(site, bot)
        conn = store.db()
        with conn:
            if not access.may_capture(bot, site):
                if force and access.allowed(bot, site):
                    current = _row(site)
                    if current:
                        _applied(bot, site, current["generation"])
                return False
            conn.execute("BEGIN IMMEDIATE")
            previous = _row(site)
            generation = previous["generation"] if previous else 0
            readable = previous is not None and _snapshot(previous) is not None
            if readable and not force and _generation(bot, site) != generation:
                return False
            if readable and previous["digest"] == digest and not previous["removed"]:
                _applied(bot, site, generation)
                return False
            nonce = os.urandom(12)
            sealed = nonce + AESGCM(_key()).encrypt(nonce, plain, site.encode())
            conn.execute("INSERT OR REPLACE INTO shared_sessions VALUES (?,?,?,?,?,?,?,?,?)",
                         (site, generation + 1, bot, time.time(), digest, sealed,
                          json.dumps(sorted(snapshot["origins"])), 0, int(authorised or bool(previous and previous["authorised"]))))
            conn.execute("INSERT OR REPLACE INTO shared_session_applied VALUES (?,?,?,?)", (bot, site, generation + 1, _instance(bot)))
    return True


_open_at_capture = {}


def capture(bot, site, force=False):
    ""
    site = _site(site)
    if not site or config.is_sandbox(bot):
        return False
    with access.lock, _lock(bot):
        policy = access._row(site)
        if not access.may_capture(bot, site) and not force:
            return False
        if site in _challenges(bot):
            if force:
                raise cdp_storage.Unavailable("A human check is still open on this site.")
            return False
        if _human(bot) and not force:
            return False
        old = _row(site)
        origins = json.loads(old["origins"]) if old else []
        with cdp_storage.Browser(_profile(bot)) as browser:



            was_open = _open_at_capture.get((bot, site, _instance(bot)))
            saved = None
            if old and not force and was_open is not None:
                previous = (_snapshot(old) or {}).get("origins") or {}
                saved = {o: v for o, v in previous.items() if o not in was_open}
            open_now = browser.origins(site)
            snapshot = browser.snapshot(site, origins, saved)
        _open_at_capture[(bot, site, _instance(bot))] = open_now
        if force and not (snapshot["cookies"] or any(snapshot["origins"].values())) and (policy is None or policy["removed"]):
            return False
        return _save(bot, site, snapshot, force, force or consent.shared_site_allowed(bot, site))


def apply(bot, site=None):
    ""
    with access.lock, _lock(bot):
        if _human(bot):
            return []
        pending = _pending(bot, site)
        if not pending:
            return []
        changed = []
        with cdp_storage.Browser(_profile(bot)) as browser:
            for row, snapshot in pending:
                browser.restore(row["site"], snapshot)
                permitted = _may(bot, row["site"])
                if not permitted:
                    browser.clear_site(row["site"], json.loads(row["origins"]))
                _applied(bot, row["site"], row["generation"] if permitted else -_version(row["site"]))
                if permitted and row["authorised"] and not row["removed"]:
                    consent.mark_signed_in(bot, row["site"])
                changed.append(row["site"])
        return changed


def _revoking(bot):
    ""

    return any(row["removed"] or not row["authorised"] or not _may(bot, row["site"])
               for row, _ in _pending(bot))


def _pending(bot, site=None):
    ""
    with access.lock, _lock(bot):
        rows = [_row(consent.base(consent.site_of(site)))] if site else store.db().execute("SELECT * FROM shared_sessions").fetchall()
        known = {r["site"] for r in rows if r}
        policies = store.db().execute("SELECT * FROM signin_access" + (" WHERE site=?" if site else ""),
                                      (access.site_key(site),) if site else ()).fetchall()
        for policy in policies:



            if not _site("https://" + policy["site"]):
                continue
            if policy["site"] not in known and not _may(bot, policy["site"]):
                rows.append({"site": policy["site"], "generation": policy["version"],
                             "origins": json.dumps(["https://" + policy["site"]]), "removed": 1, "authorised": 0})
        challenges = _challenges(bot)
        pending = []
        for row in rows:
            if not row:
                continue
            if not _may(bot, row["site"]):


                if _generation(bot, row["site"]) != -_version(row["site"]):
                    pending.append((row, {"cookies": [], "origins": {o: {} for o in json.loads(row["origins"])}}))
                continue
            if row["site"] in challenges:
                continue
            snapshot = _snapshot(row)
            if snapshot is not None and _generation(bot, row["site"]) < row["generation"]:
                pending.append((row, snapshot))
        return pending


def _cookie_hosts(profile, site):
    for file in (profile / "Default/Network/Cookies", profile / "Default/Cookies"):
        if not file.is_file():
            continue
        try:
            with sqlite3.connect(file.as_uri() + "?mode=ro", uri=True) as conn:
                return {r[0].lstrip(".") for r in conn.execute("SELECT DISTINCT host_key FROM cookies")
                        if consent.base(r[0].lstrip(".")) == site}
        except sqlite3.Error:
            continue
    return set()


def _copy_snapshot(bot, site, origin):
    ""
    profile = _profile(bot)
    hosts = _cookie_hosts(profile, site)
    if not hosts:
        return None
    lock = profile / "SingletonLock"
    if lock.is_symlink():
        try:
            os.kill(int(os.readlink(lock).rsplit("-", 1)[1]), 0)
            return None
        except (ValueError, ProcessLookupError):
            pass
        except PermissionError:
            return None
    from tools.bot_desktop.browser import dock_argv, executable
    binary = executable()
    if not binary or not Path(binary).is_file():
        return None
    with tempfile.TemporaryDirectory(prefix="zero-shared-read-") as folder:
        copied = Path(folder)
        for relative in ("Local State", "Default/Cookies", "Default/Network/Cookies", "Default/Local Storage"):
            source, target = profile / relative, copied / relative
            if source.is_symlink() or not source.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, symlinks=False)
            elif source.name == "Cookies":
                with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
                    src.backup(dst)
            else:
                shutil.copy2(source, target)

        argv = dock_argv(binary, str(copied)) + ["--headless", "--disable-background-networking", "--disable-sync",
                                                    "--disable-extensions", "--restore-last-session", "about:blank"]
        process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            for _ in range(100):
                if (copied / "DevToolsActivePort").exists():
                    break
                if process.poll() is not None:
                    return None
                time.sleep(.05)
            with cdp_storage.Browser(copied) as browser:
                origins = {"https://" + host for host in hosts} | ({origin} if origin else set())
                return browser.snapshot(site, origins)
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def seed(bot, url):
    ""
    with access.lock:
        return _seed(bot, url)


def _seed(bot, url):
    site = _site(url)
    if not site or config.is_sandbox(bot):
        return False
    if not access.allowed(bot, site):
        return False
    previous = _row(site)
    if previous is not None and _snapshot(previous) is not None:
        return False
    origin = cdp_storage.web_origin(url)
    for other in sorted(config.BOTS_BY_ID, key=lambda candidate: candidate == bot):
        if not access.allowed(other, site) or (other != bot and not access.sharing(other)):
            continue
        if not access.sharing(other) and access._row(site) is None:
            continue
        if _human(other) or config.is_sandbox(other) or site in _challenges(other):
            continue
        profile = _profile(other)
        try:
            with cdp_storage.Browser(profile) as browser:
                snapshot = browser.snapshot(site, {origin} if origin else ())
        except (OSError, ValueError, cdp_storage.Unavailable, TimeoutError, ConnectionError):
            snapshot = _copy_snapshot(other, site, origin)
        if snapshot and (snapshot["cookies"] or any(snapshot["origins"].values())):
            access.ensure(site, other)
            if not access.allowed(bot, site):
                return False
            saved = _save(other, site, snapshot, authorised=True)
            if saved:
                consent.mark_signed_in(other, site)
            return saved
    return False


def before_ask(bot, url):
    site = _site(url)
    if not site or not _may(bot, site):
        return False
    seed(bot, url)
    row = _row(site)
    if not row or row["removed"] or _generation(bot, site) >= row["generation"]:
        return False
    try:
        return bool(apply(bot, site))
    except (OSError, ValueError, cdp_storage.Unavailable, TimeoutError, ConnectionError):


        hint = (bot, site, row["generation"])
        with _locks_guard:
            if hint in _hints:
                return False
            _hints.add(hint)
        return True


_hints = set()





READY_WAIT = 10
_BROWSER_GONE = (cdp_storage.NotReady, ConnectionClosed)


def sync(bot, phase, url=""):
    if phase not in ("before", "after", "close"):
        raise ValueError("Unknown browser step.")
    if _human(bot):
        return
    if phase == "before":
        deadline = time.monotonic() + READY_WAIT
        while True:

            with access.lock, _lock(bot):
                if url:
                    seed(bot, url)
                    url = ""
                try:
                    apply(bot)
                    return
                except cdp_storage.NotReady:
                    if time.monotonic() >= deadline:
                        if _revoking(bot):
                            raise
                        log.info("%s's browser wasn't ready; it gets the newer sign-ins on its next step", bot)
                        return
            time.sleep(.5)
    try:
        _after(bot)
    except _BROWSER_GONE:


        log.info("%s's browser closed during a step; nothing to capture", bot)


def _after(bot):
    with access.lock, _lock(bot):
        apply(bot)
        with cdp_storage.Browser(_profile(bot)) as browser:
            targets = browser.call("Target.getTargets").get("targetInfos", [])
        sites = {_site(t.get("url", "")) for t in targets
                 if t.get("type") == "page" and t.get("url", "").startswith(("https://", "http://"))} - {""}
        for site in sites:
            target = next(t["url"] for t in targets if _site(t.get("url", "")) == site)
            if not _row(site):
                seed(bot, target)
            capture(bot, site)
        apply(bot)


def clear():
    ""
    with access.lock, _store_lock:
        conn = store.db()
        sites = [r[0] for r in conn.execute("SELECT site FROM shared_sessions")]
        with conn:
            conn.execute("UPDATE shared_sessions SET generation=generation+1, sealed=NULL, digest='', removed=1, authorised=0")
            conn.execute("DELETE FROM shared_session_applied")
        (config.HUB_DIR / KEY_FILE).unlink(missing_ok=True)
    for site in sites:
        consent.forget_signed_in(site)
    for bot in config.BOTS_BY_ID:
        try:
            apply(bot)
        except Exception:
            log.info("Shared sign-out will finish when %s opens its browser", bot)
    return len(sites)
