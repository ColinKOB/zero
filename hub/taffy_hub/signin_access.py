""





import json
import logging
import threading

from . import config, consent, store

lock = threading.RLock()
log = logging.getLogger("taffy.signin_access")


@store.schema
def _schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signin_access (
            site TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('all','selected')),
            zips TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, removed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS signin_sharing (
            bot_id TEXT PRIMARY KEY, share_new INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signin_revoked_passwords (
            bot_id TEXT NOT NULL, handle TEXT NOT NULL, PRIMARY KEY(bot_id,handle)
        );
    """)

    if "use_on" not in {r[1] for r in conn.execute("PRAGMA table_info(signin_access)")}:
        conn.execute("ALTER TABLE signin_access ADD COLUMN use_on TEXT NOT NULL DEFAULT 'anywhere'")


def site_key(site):
    return consent.base(consent.site_of(site))


def site_label(site):
    ""
    try:
        return site_key((site or "").partition("?")[0].partition("#")[0]) or "(unknown)"
    except ValueError:
        return "(unknown)"


def sharing(bot):
    row = store.db().execute("SELECT share_new FROM signin_sharing WHERE bot_id=?", (bot,)).fetchone()
    return bool(row[0]) if row else not config.is_money(bot)


def set_sharing(bot, shared):
    if bot not in config.BOTS_BY_ID:
        raise ValueError("That Zip is no longer here.")
    with lock, store.db() as conn:
        conn.execute("INSERT OR REPLACE INTO signin_sharing VALUES (?,?)", (bot, int(shared)))


def _row(site):
    return store.db().execute("SELECT * FROM signin_access WHERE site=?", (site_key(site),)).fetchone()


def audience(row):
    return {"mode": row["mode"], "zips": json.loads(row["zips"])}


def ensure(site, source=None, *, legacy=False):
    ""
    site = site_key(site)
    if not site:
        raise ValueError("Give the site's address.")
    with lock:
        row = _row(site)
        if row is not None:
            return row
        conn = store.db()
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='shared_sessions'").fetchone():
            previous = conn.execute("SELECT source_bot FROM shared_sessions WHERE site=?", (site,)).fetchone()
            if previous:
                source, legacy = previous[0], True
        private = source and (config.is_money(source) if legacy else not sharing(source))
        with conn:
            conn.execute("INSERT OR IGNORE INTO signin_access(site,mode,zips) VALUES (?,?,?)",
                         (site, "selected" if private else "all", json.dumps([source] if private else [])))
        return _row(site)


def allowed(bot, site, *, source=None):
    if bot not in config.BOTS_BY_ID:
        return False
    row = _row(site)
    if row is None:
        conn = store.db()
        previous = (conn.execute("SELECT source_bot FROM shared_sessions WHERE site=?", (site_key(site),)).fetchone()
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='shared_sessions'").fetchone() else None)
        if previous or source:
            row = ensure(site, previous[0] if previous else source, legacy=bool(previous))
        else:
            return True
    return not row["removed"] and (row["mode"] == "all" or bot in json.loads(row["zips"]))


def may_capture(bot, site):
    row = _row(site)
    if row is None:
        return bot in config.BOTS_BY_ID and sharing(bot)
    return allowed(bot, site) and (sharing(bot) or (row["mode"] == "selected" and json.loads(row["zips"]) == [bot]))


def complete_human_signin(site, bot):
    ""
    row = _row(site)
    if row and row["removed"]:
        set_access(site, {"mode": "all" if sharing(bot) else "selected", "zips": [] if sharing(bot) else [bot]})
    elif row and row["mode"] == "selected" and bot not in json.loads(row["zips"]):
        set_access(site, {"mode": "selected", "zips": json.loads(row["zips"]) + [bot]})


def validate_access(access):
    if not isinstance(access, dict) or access.get("mode") not in ("all", "selected"):
        raise ValueError("Choose All Zips or the Zips that can use this sign-in.")
    zips = access.get("zips", [])
    if not isinstance(zips, list) or any(not isinstance(b, str) or b not in config.BOTS_BY_ID for b in zips):
        raise ValueError("One of those Zips is no longer here. Refresh and try again.")
    return {"mode": access["mode"], "zips": sorted(set(zips)) if access["mode"] == "selected" else []}


def set_access(site, access, *, removed=False):
    access = validate_access(access)
    site = site_key(site)
    with lock:
        ensure(site)
        if not removed:
            from . import logins
            for bot in config.BOTS_BY_ID:
                if (access["mode"] == "all" or bot in access["zips"]) and not allowed(bot, site):
                    if not logins.ensure_for_bot(bot, site, owner_grant=True):
                        raise ValueError("The saved password could not be updated for that Zip. Try again in a moment.")
        with store.db() as conn:
            conn.execute("UPDATE signin_access SET mode=?,zips=?,version=version+1,removed=? WHERE site=?",
                         (access["mode"], json.dumps(access["zips"]), int(removed), site))

            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='shared_sessions'").fetchone():
                conn.execute("UPDATE shared_sessions SET generation=generation+1 WHERE site=?", (site,))
        return audience(_row(site))


def migrate_passwords(rows):
    legacy = not store.get_setting("signin_passwords_migrated", False)
    grouped = {}
    for row in rows:
        site = site_key(row.get("origin") or "")
        if site:
            grouped.setdefault(site, set()).add(row["bot"])
    for site, bots in grouped.items():
        source = next(iter(bots)) if len(bots) == 1 else None
        ensure(site, None if legacy else source, legacy=legacy)
    if legacy:
        store.set_setting("signin_passwords_migrated", True)


def page():
    from . import logins, looks, shared_sessions, signin_notes
    rows = logins.list_all()
    notes = signin_notes.all_sites()
    migrate_passwords(rows)
    sessions = {r["site"]: r for r in store.db().execute("SELECT * FROM shared_sessions WHERE removed=0 AND sealed IS NOT NULL")}
    for site, row in sessions.items():
        ensure(site, row["source_bot"], legacy=True)
    sites = set(sessions) | {site_key(r.get("origin") or "") for r in rows}
    result = []
    for site in sorted(sites - {""}):
        policy = _row(site)
        if not policy:
            continue
        passwords = {}
        for r in rows:
            if site_key(r.get("origin") or "") == site and not password_revoked(r["bot"], r["id"]):
                username = str(r.get("identifier") or "")
                passwords.setdefault(username.lower(), {"id": r["id"], "label": r.get("label") or site,
                                                       "username": username, "has_otp": bool(r.get("has_otp"))})
        selected = json.loads(policy["zips"])
        session = sessions.get(site)
        result.append({"site": site, "access": audience(policy), "has_browser": session is not None,
                       "use_on": policy["use_on"] if "use_on" in policy.keys() else "anywhere",
                       "notes": notes.get(site, []),
                       "signed_in_at": session["updated"] if session else None, "logins": list(passwords.values()),
                       "money_only": policy["mode"] == "selected" and bool(selected) and all(config.is_money(b) for b in selected)})
    zips = [{"id": b.id, "name": b.name, "role": b.role, "color": b.color, "symbol": b.symbol,
             "shape": b.shape, "look": looks.for_bot(b.id), "working": 0, "needs_you": 0, "busy": False} for b in config.BOTS]
    return {"sites": result, "zips": zips, "sharing": [{"bot_id": b.id, "share_new_signins": sharing(b.id),
                                           "private_by_default": config.is_money(b.id)} for b in config.BOTS]}


def reconcile(site=None):
    ""
    from . import shared_sessions
    for bot in config.BOTS_BY_ID:
        try:
            shared_sessions.apply(bot, site)
        except (OSError, ValueError, shared_sessions.cdp_storage.Unavailable, TimeoutError, ConnectionError) as exc:
            log.warning("shared sign-in cleanup failed bot=%s site=%s step=reconcile error=%s",
                        bot, site_label(site) if site else "(all sites)", type(exc).__name__)


def remove_site(site):
    from . import logins, shared_sessions
    site = site_key(site)
    with lock:
        set_access(site, {"mode": "selected", "zips": []}, removed=True)
        revoke_passwords(site)
        with store.db() as conn:
            conn.execute("UPDATE shared_sessions SET generation=generation+1,sealed=NULL,digest='',removed=1,authorised=0 WHERE site=?", (site,))
        failed = []
        for row in logins.list_all(everywhere=True):
            if site_key(row.get("origin") or "") == site:
                try:
                    logins.remove(row["bot"], row["id"])
                except Exception:
                    failed.append(row["bot"])
        consent.forget_signed_in(site)
    reconcile(site)
    return failed


def password_revoked(bot, handle):
    return store.db().execute("SELECT 1 FROM signin_revoked_passwords WHERE bot_id=? AND handle=?", (bot, handle)).fetchone() is not None


def revoke_password_handles(bot, handles):
    with store.db() as conn:
        conn.executemany("INSERT OR IGNORE INTO signin_revoked_passwords VALUES (?,?)", [(bot, h) for h in handles])


def revoke_passwords(site=None):
    from . import logins
    for bot in config.BOTS_BY_ID:
        try:
            items = logins._store(bot).list_items()
        except Exception:
            continue
        with store.db() as conn:
            conn.executemany("INSERT OR IGNORE INTO signin_revoked_passwords VALUES (?,?)",
                [(bot, m.id) for m in items if m.kind == "login" and (site is None or site_key(m.origin or "") == site_key(site))])


def zip_deleted(bot):
    from . import shared_sessions
    with lock, store.db() as conn:
        for row in conn.execute("SELECT * FROM signin_access WHERE mode='selected'").fetchall():
            zips = json.loads(row["zips"])
            if bot in zips:
                conn.execute("UPDATE signin_access SET zips=?,version=version+1 WHERE site=?",
                             (json.dumps([b for b in zips if b != bot]), row["site"]))
        conn.execute("DELETE FROM signin_sharing WHERE bot_id=?", (bot,))
        conn.execute("DELETE FROM shared_session_applied WHERE bot_id=?", (bot,))
