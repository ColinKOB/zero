"""Saved logins for the bots, kept in each bot's Hermes vault.

Colin saves a login once from the app (iPhone password AutoFill fills the form). The password goes
straight into the vault of each bot he picks: encrypted on alien, bound to the site's origin, and
filled into the page by Hermes. The bots see only the site and the username, never the password.
Optional 2FA setup keys let Hermes generate the codes too.
"""

import json
import os
import subprocess
from pathlib import Path

from . import config, screen


def _store(bot_id: str):
    from agent.vault_store import VaultStore
    return VaultStore(base_dir=screen.profile_home(bot_id) / "vault")


def _base(host: str) -> str:
    """The company part of a host, by the same rule as sign-in permissions (Public Suffix List), so a
    redirect into another SharePoint or Shopify tenant is never treated as the same site."""
    from . import consent
    return consent.base(host)


def sign_in_origins(url: str) -> list[str]:
    """The site's own address plus any sign-in pages it redirects to on the same domain (for example
    crm.bloomerang.co sends you to identity.bloomerang.co). The vault fills a password only on an exact
    address, so the login is bound to each of these. Redirects to other companies (a Google or
    Microsoft sign-in) are left out: those need their own saved login."""
    from urllib.parse import urlsplit
    import httpx
    first = urlsplit(url)
    origins = [f"{first.scheme}://{first.netloc}"]
    try:
        r = httpx.get(url, follow_redirects=True, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        for u in [*(h.url for h in r.history), r.url]:
            p = urlsplit(str(u))
            o = f"{p.scheme}://{p.netloc}"
            if p.scheme == "https" and p.hostname and _base(p.hostname) == _base(first.hostname or "") \
                    and o not in origins:
                origins.append(o)
    except Exception:
        pass
    return origins[:4]


def _key(origin: str | None, identifier: str | None) -> tuple[str, str]:
    """One login as Colin sees it: the company it signs in to and the username. The vault keeps one item per
    sign-in address (crm.bloomerang.co and identity.bloomerang.co), and the app shows them as one."""
    from urllib.parse import urlsplit
    host = (urlsplit(origin or "").hostname or "").lower().removeprefix("www.")
    return _base(host) if host else "", (identifier or "").strip().lower()


def _copies(bot_id: str, origin: str | None, identifier: str | None) -> list:
    key = _key(origin, identifier)
    return [m for m in _store(bot_id).list_items()
            if m.kind == "login" and key[0] and _key(m.origin, m.identifier) == key]


def save(bot_ids: list[str], label: str, url: str, identifier: str, identifier_type: str,
         password: str, otp_secret: str | None) -> list[dict]:
    """Saves to every bot or to none: checks the item first, then rolls back on any failure.
    One vault item per sign-in address (see sign_in_origins). Saving a login that already exists (same
    company and username) replaces it, which is how a changed password is updated; the old items are
    removed only after the new ones are in, so a failure never leaves a bot without the login."""
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
        for item in saved:
            try:
                _store(item["bot"]).remove_item(item["id"])
            except Exception:
                pass
        raise
    for bot_id, ids in old.items():
        for item_id in ids:
            try:
                _store(bot_id).remove_item(item_id)
            except Exception:
                pass
    return saved


def list_all() -> list[dict]:
    """One row per bot, company and username (Oct 8 QA, M4: crm.bloomerang.co showed up twice). The row is the
    item saved for the site itself; its "(sign-in page)" copies are counted, not listed."""
    out = []
    for b in config.BOTS:
        try:
            items = [m for m in _store(b.id).list_items() if m.kind == "login"]
        except Exception:
            continue
        groups: dict[tuple, list] = {}
        for m in items:
            groups.setdefault(_key(m.origin, m.identifier) if m.origin else ("", m.id), []).append(m)
        for group in groups.values():
            main = next((m for m in group if not m.label.endswith("(sign-in page)")), group[0])
            out.append({"bot": b.id, **main.to_dict(), "copies": len(group)})
    return out


def remove(bot_id: str, item_id: str) -> bool:
    """Removes the login and every copy of it for its sign-in pages, so the bot really has to sign in again."""
    store = _store(bot_id)
    item = next((m for m in store.list_items() if m.id == item_id), None)
    if item is None:
        return False
    ids = {m.id for m in _copies(bot_id, item.origin, item.identifier)} | {item_id} if item.origin else {item_id}
    removed = False
    for i in ids:
        removed = store.remove_item(i) or removed
    return removed


def open_in_bot_browser(bot_id: str, url: str) -> None:
    """Opens a page in the bot's own browser on its screen: the same browser and cookie jar the bot
    uses, so whatever Colin signs in to there is what the bot uses afterwards."""
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
        env=dict(os.environ, HERMES_HOME=str(home)))
    lines = [l for l in probe.stdout.splitlines() if l.startswith("[")]
    if not lines:
        raise RuntimeError(f"Could not find the bot's browser: {probe.stderr[-300:]}")
    # The URL is validated by the caller (http/https, no leading '-'); "--" ends Chromium's switches.
    argv = json.loads(lines[-1]) + ["--start-maximized", "--", url]
    env = dict(os.environ, HERMES_HOME=str(home), **disp)
    subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=str(Path.home()))


def add_sign_in_pages() -> list[str]:
    """For logins saved before sign_in_origins existed: copies each one to any sign-in address its
    site redirects to. The password is copied inside the vault and never leaves this process."""
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
