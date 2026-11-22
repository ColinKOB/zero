""







import json
import logging
import os
import subprocess
from pathlib import Path

from . import config, screen, signin_access as access

log = logging.getLogger("taffy.logins")


def _store(bot_id: str):
    from agent.vault_store import VaultStore
    return VaultStore(base_dir=screen.profile_home(bot_id) / "vault")


def _base(host: str) -> str:
    ""

    from . import consent
    return consent.base(host)


def sign_in_origins(url: str) -> list[str]:
    ""





    from urllib.parse import urlsplit
    import httpx
    first = urlsplit(url)
    origins = [f"{first.scheme}://{first.netloc}"]
    try:
        r = httpx.get(url, follow_redirects=True, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code >= 400:
            log.info("sign-in page lookup for %s got HTTP %s", first.hostname, r.status_code)
        for u in [*(h.url for h in r.history), r.url]:
            p = urlsplit(str(u))
            o = f"{p.scheme}://{p.netloc}"
            if p.scheme == "https" and p.hostname and _base(p.hostname) == _base(first.hostname or "") \
                    and o not in origins:
                origins.append(o)
    except Exception as e:
        log.info("sign-in page lookup for %s failed (%s)", first.hostname, type(e).__name__)
    return origins[:4]


def _key(origin: str | None, identifier: str | None) -> tuple[str, str]:
    ""

    from urllib.parse import urlsplit
    host = (urlsplit(origin or "").hostname or "").lower().removeprefix("www.")
    return _base(host) if host else "", (identifier or "").strip().lower()


def _copies(bot_id: str, origin: str | None, identifier: str | None) -> list:
    key = _key(origin, identifier)
    return [m for m in _store(bot_id).list_items()
            if m.kind == "login" and key[0] and _key(m.origin, m.identifier) == key]


def kept_otp_secret(bot_ids: list[str], url: str, identifier: str) -> str | None:
    ""


    for bot_id in bot_ids:
        store = _store(bot_id)
        for m in _copies(bot_id, url, identifier):
            key = store.resolve_secret(m.id).get("otp_secret")
            if key:
                return key
    return None


class SaveFailed(Exception):
    ""

    def __init__(self, stuck: list[str]):
        super().__init__("partial copies left")
        self.stuck = stuck


def save(bot_ids: list[str], label: str, url: str, identifier: str, identifier_type: str,
         password: str, otp_secret: str | None) -> list[dict]:
    ""
    saved, failed = save_reporting(bot_ids, label, url, identifier, identifier_type, password, otp_secret)
    if failed:
        log.warning("a saved login left an old or partial copy behind for %s", ", ".join(failed))
    return saved


def save_reporting(bot_ids: list[str], label: str, url: str, identifier: str, identifier_type: str,
                   password: str, otp_secret: str | None) -> tuple[list[dict], list[str]]:
    ""





    from agent.vault_store import LOGIN_IDENTIFIER_TYPES, normalize_origin, normalize_otp_secret
    if identifier_type not in LOGIN_IDENTIFIER_TYPES:
        raise ValueError("bad identifier type")
    normalize_origin(url)
    if otp_secret:
        normalize_otp_secret(otp_secret)
    origins = sign_in_origins(url)
    saved = []
    old = {b: [m.id for m in _copies(b, origins[0], identifier)] for b in bot_ids}
    try:
        for bot_id in bot_ids:
            for i, origin in enumerate(origins):
                secret = {"identifier_type": identifier_type, "identifier": identifier, "password": password}
                if otp_secret:
                    secret["otp_secret"] = otp_secret
                meta = _store(bot_id).add_item("login", label if i == 0 else f"{label} (sign-in page)", secret,
                                               origin=origin)
                saved.append({"bot": bot_id, **meta.to_dict()})
    except Exception:
        stuck = []
        for item in saved:
            try:
                _store(item["bot"]).remove_item(item["id"])
            except Exception:
                stuck.append(item["bot"])
        if stuck:
            log.warning("a failed login save left a partial copy for %s", ", ".join(sorted(set(stuck))))
            raise SaveFailed(sorted(set(stuck)))
        raise
    failed = []
    for bot_id, ids in old.items():
        for item_id in ids:
            try:
                _store(bot_id).remove_item(item_id)
            except Exception:
                if bot_id not in failed:
                    failed.append(bot_id)
    return saved, failed


def copy_to(from_bot: str, item_id: str, to_bot: str, *, owner_grant=False) -> bool:
    ""

    with access.lock:
        return _copy_to(from_bot, item_id, to_bot, owner_grant=owner_grant)


def _copy_to(from_bot, item_id, to_bot, *, owner_grant=False):
    if config.is_sandbox(from_bot) or config.is_sandbox(to_bot):
        return False
    src = _store(from_bot)
    item = next((m for m in src.list_items() if m.id == item_id), None)
    if item is None:
        return False
    if access.password_revoked(from_bot, item_id):
        return False
    site = _key(item.origin, item.identifier)[0]
    if not site or (not owner_grant and not access.allowed(to_bot, site)):
        return False
    old = [m.id for m in _copies(to_bot, item.origin, item.identifier)]


    group, origins = [item], {item.origin}
    if item.origin:
        password = src.resolve_secret(item.id).get("password")
        for m in _copies(from_bot, item.origin, item.identifier):
            if m.id != item.id and m.label.endswith("(sign-in page)") and m.origin not in origins and \
                    src.resolve_secret(m.id).get("password") == password:
                group.append(m)
                origins.add(m.origin)
        del password
    done = []
    try:
        for m in group:
            secret = src.resolve_secret(m.id)
            done.append(_store(to_bot).add_item(
                "login", m.label, {"identifier_type": m.identifier_type, "identifier": m.identifier, **secret},
                origin=m.origin).id)
            del secret
    except Exception as e:
        log.warning("could not copy a login to %s (%s)", to_bot, type(e).__name__)
        access.revoke_password_handles(to_bot, done)
        for i in done:
            try:
                _store(to_bot).remove_item(i)
            except Exception:
                log.warning("a failed login copy left a partial copy for %s", to_bot)
        return False

    access.revoke_password_handles(to_bot, old)
    for handle in old:
        try:
            _store(to_bot).remove_item(handle)
        except Exception:
            log.warning("an old login copy could not be removed for %s", to_bot)
    return True


def list_all(everywhere: bool = False) -> list[dict]:
    ""






    out = []
    for b in config.BOTS:
        if b.sandbox and not everywhere:
            continue
        try:
            items = [m for m in _store(b.id).list_items() if m.kind == "login"]
        except Exception:
            continue
        groups: dict[tuple, list] = {}
        for m in items:
            groups.setdefault(_key(m.origin, m.identifier) if m.origin else ("", m.id), []).append(m)
        for group in groups.values():

            mains = [m for m in group if not m.label.endswith("(sign-in page)")] or group
            main = max(mains, key=lambda m: str(getattr(m, "created_at", "") or ""))
            out.append({"bot": b.id, **main.to_dict(), "created_at": str(getattr(main, "created_at", "") or ""),
                        "copies": len(group)})
    return out


def has_for_site(bot_id: str, site: str) -> bool:
    from . import consent
    company = consent.base(consent.site_of(site))
    if not access.allowed(bot_id, company):
        return False
    return any(row["bot"] == bot_id and not access.password_revoked(bot_id, row["id"])
               and _key(row.get("origin"), row.get("identifier"))[0] == company
               for row in list_all())


def shared_sites(bot_id: str) -> dict[str, float]:
    ""

    from datetime import datetime
    out: dict[str, float] = {}
    if config.is_sandbox(bot_id):
        return out
    for row in list_all():
        site = _key(row.get("origin"), row.get("identifier"))[0]
        if not site or access.password_revoked(row["bot"], row["id"]) or not access.allowed(bot_id, site):
            continue
        try:
            at = datetime.fromisoformat(row.get("created_at") or "").timestamp()
        except ValueError:
            at = 0.0
        out[site] = max(out.get(site, 0.0), at)
    return out


def share_for_site(bot_id: str, site: str) -> bool:
    ""
    from . import consent
    company = consent.base(consent.site_of(site))
    with access.lock:
        return _refresh_for_bot(bot_id, company)[1]


def ensure_for_bot(bot_id, site=None, *, owner_grant=False):
    ""
    with access.lock:
        return _refresh_for_bot(bot_id, site, owner_grant=owner_grant)[0]


def _refresh_for_bot(bot_id, site=None, *, owner_grant=False):





    if config.is_sandbox(bot_id):
        return True, False
    rows = list_all()
    access.migrate_passwords(rows)
    rows = [r for r in rows if not access.password_revoked(r["bot"], r["id"])]
    held = {_key(r.get("origin"), r.get("identifier")): r for r in rows if r["bot"] == bot_id}
    seen, ok, copied = set(), True, False
    for row in sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True):
        key = _key(row.get("origin"), row.get("identifier"))
        if not key[0] or key in seen or (site is not None and key[0] != site) \
                or (not owner_grant and not access.allowed(bot_id, key[0])):
            continue
        seen.add(key)
        current = held.get(key)
        if row["bot"] == bot_id:
            continue
        if current:
            try:
                source = _store(row["bot"]).resolve_secret(row["id"])
                target = _store(bot_id).resolve_secret(current["id"])
                if source == target and row.get("origin") == current.get("origin"):
                    continue
            except Exception:
                ok = False
                continue
        granted = copy_to(row["bot"], row["id"], bot_id, owner_grant=True) if owner_grant \
            else copy_to(row["bot"], row["id"], bot_id)
        ok = ok and granted
        copied = copied or granted
    return ok, copied


def authorized_item(bot_id, source_bot, handle):
    ""
    if source_bot not in config.BOTS_BY_ID:
        raise ValueError("That saved sign-in is no longer available.")
    if access.password_revoked(source_bot, handle):
        raise ValueError("That saved sign-in was removed.")
    vault = _store(source_bot)
    item = next((m for m in vault.list_items() if m.id == handle and m.kind == "login"), None)
    if item is None:
        raise ValueError("That saved sign-in is no longer available.")
    site = _key(item.origin, item.identifier)[0]
    access.ensure(site, source_bot)
    if not site or not access.allowed(bot_id, site):
        raise ValueError("This sign-in is not shared with that Zip.")
    return vault, item


def resolve(bot_id, source_bot, handle):
    with access.lock:
        vault, _ = authorized_item(bot_id, source_bot, handle)
        return vault.resolve_secret(handle)


def keep_from_zip(bot_id):
    ""
    with access.lock:
        rows = list_all()
        access.migrate_passwords(rows)
        for row in [r for r in rows if r["bot"] == bot_id]:
            key = _key(row.get("origin"), row.get("identifier"))
            if not key[0]:
                continue
            others = [b for b in config.BOTS_BY_ID if b != bot_id and access.allowed(b, key[0])]
            if not others or any(r["bot"] != bot_id and _key(r.get("origin"), r.get("identifier")) == key
                                 and not access.password_revoked(r["bot"], r["id"]) for r in rows):
                continue
            if not any(copy_to(bot_id, row["id"], b) for b in others):
                raise ValueError("A shared password could not be kept for your other Zips. Try again in a moment.")


def remove(bot_id: str, item_id: str) -> bool:
    ""
    store = _store(bot_id)
    item = next((m for m in store.list_items() if m.id == item_id), None)
    if item is None:
        return False
    ids = {m.id for m in _copies(bot_id, item.origin, item.identifier)} | {item_id} if item.origin else {item_id}
    removed = False
    for i in ids:
        removed = store.remove_item(i) or removed
    return removed


def _pinned_browser(home: Path) -> dict[str, str]:
    ""


    pinned = config._env_value(home / ".env", "AGENT_BROWSER_EXECUTABLE_PATH")
    return {"AGENT_BROWSER_EXECUTABLE_PATH": pinned} if pinned else {}


def _bring_to_front(browser, page: str | None) -> None:
    ""

    from . import cdp_storage
    try:
        for tab in browser.call("Target.getTargets").get("targetInfos", []):
            if tab.get("type") == "page" and tab.get("url") == "about:blank" and tab.get("targetId") != page:
                browser.call("Target.closeTarget", {"targetId": tab["targetId"]})
        if page:
            browser.call("Target.activateTarget", {"targetId": page})
    except cdp_storage.Unavailable as e:
        log.info("could not tidy the tabs: %s", e)


def open_in_bot_browser(bot_id: str, url: str) -> str | None:
    ""


    home = screen.profile_home(bot_id)
    env_file = home / "bot-desktop" / "env"
    disp = {}
    for line in env_file.read_text().splitlines():
        line = line.removeprefix("export ").strip()
        if "=" in line:
            k, v = line.split("=", 1)
            disp[k] = v.strip().strip('"').strip("'")
    probe = subprocess.run(
        [os.sys.executable, "-c",
         "import json; from tools.bot_desktop import browser as b; "
         "exe, udd = b.dock_launch(); print(json.dumps(b.dock_argv(exe, udd)))"],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, HERMES_HOME=str(home), **_pinned_browser(home)))
    lines = [l for l in probe.stdout.splitlines() if l.startswith("[")]
    if not lines:
        raise RuntimeError(f"Could not find the bot's browser: {probe.stderr[-300:]}")


    argv = json.loads(lines[-1]) + ["--start-maximized", "--hide-crash-restore-bubble", "--", "about:blank"]
    env = dict(os.environ, HERMES_HOME=str(home), **disp)
    from . import cdp_storage, shared_sessions
    import time
    profile = home / "bot-desktop" / "browser-profile"

    deadline = time.monotonic() + 45


    started = None
    while True:
        step = "reach the browser"
        try:
            with cdp_storage.Browser(profile) as browser:
                step = "apply shared sign-ins"
                shared_sessions.sync(bot_id, "before", url)
                step = "open the page"
                page = browser.call("Target.createTarget", {"url": url}).get("targetId")
                _bring_to_front(browser, page)
            return page
        except cdp_storage.NotReady:
            if started is None:
                started = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True, cwd=str(Path.home()))
                continue

            if (code := started.poll()) is not None and code < 0:
                raise RuntimeError(f"The browser crashed as it started (signal {-code}).") from None
            if time.monotonic() > deadline:
                raise RuntimeError("The browser did not become ready.") from None
            time.sleep(.1)
        except cdp_storage.Unavailable as e:

            raise RuntimeError(f"Could not {step}: {e}") from e


def add_sign_in_pages() -> list[str]:
    ""

    added = []
    for b in config.BOTS:
        try:
            store = _store(b.id)
            items = [m for m in store.list_items() if m.kind == "login" and m.origin]
        except Exception:
            continue
        have = {str(m.origin) for m in items}
        for m in items:
            if m.label.endswith("(sign-in page)"):
                continue
            for origin in sign_in_origins(str(m.origin))[1:]:
                if origin in have:
                    continue
                secret = store.resolve_secret(m.id)
                store.add_item("login", f"{m.label} (sign-in page)",
                               {"identifier_type": m.identifier_type, "identifier": m.identifier, **secret},
                               origin=origin)
                del secret
                have.add(origin)
                added.append(f"{b.id}: {origin}")
    return added
