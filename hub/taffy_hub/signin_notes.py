""















import json
import re
import time

from . import config, consent, signin_access, store

MAX_CHARS = 280
MAX_PER_SITE = 20
PERSON = "person"
USE_ON = ("anywhere", "mac")
_CODE = re.compile(r"\d{6,}")


class NoteError(ValueError):
    pass


@store.schema
def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS signin_notes (
        id INTEGER PRIMARY KEY, site TEXT NOT NULL, author TEXT NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS signin_notes_site ON signin_notes(site)")


def _site(site: str) -> str:
    key = signin_access.site_key(site or "")
    if not key:
        raise NoteError("Give the site's address.")
    return key


def _holds_password(site: str, text: str) -> bool:
    from . import logins
    for row in logins.list_all():
        if signin_access.site_key(row.get("origin") or "") != site:
            continue
        try:
            secret = logins.resolve(row["bot"], row["bot"], row["id"])
        except Exception:
            continue
        password = str(secret.get("password") or "")
        if len(password) >= 4 and password in text:
            return True
    return False


def add(site: str, author: str, text: str) -> dict:
    ""
    site = _site(site)
    text = " ".join(str(text or "").split())
    if not text:
        raise NoteError("Write the note first.")
    if len(text) > MAX_CHARS:
        raise NoteError(f"Keep the note under {MAX_CHARS} characters.")
    if author != PERSON and author not in config.BOTS_BY_ID:
        raise NoteError("That Zip is no longer here.")
    if _CODE.search(text) or _holds_password(site, text):
        raise NoteError("A note can't hold a password or a code. Say where to get it instead.")
    now = time.time()
    with store.db() as c:
        cur = c.execute("INSERT INTO signin_notes(site, author, text, created_at) VALUES (?,?,?,?)",
                        (site, author, text, now))

        c.execute("DELETE FROM signin_notes WHERE site=? AND id NOT IN (SELECT id FROM signin_notes WHERE site=? "
                  "ORDER BY id DESC LIMIT ?)", (site, site, MAX_PER_SITE))
    return {"id": cur.lastrowid, "site": site, "author": author, "text": text, "created_at": now}


def delete(note_id: int) -> bool:
    with store.db() as c:
        return c.execute("DELETE FROM signin_notes WHERE id=?", (int(note_id),)).rowcount > 0


def for_site(site: str) -> list[dict]:
    try:
        site = _site(site)
    except NoteError:
        return []
    rows = store.db().execute("SELECT * FROM signin_notes WHERE site=? ORDER BY id", (site,)).fetchall()
    return [{"id": r["id"], "author": r["author"], "text": r["text"], "created_at": r["created_at"]} for r in rows]


def all_sites() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in store.db().execute("SELECT * FROM signin_notes ORDER BY id"):
        out.setdefault(r["site"], []).append({"id": r["id"], "author": r["author"], "text": r["text"],
                                             "created_at": r["created_at"]})
    return out


def use_on(site: str) -> str:
    try:
        row = signin_access._row(site)
    except Exception:
        return "anywhere"
    value = row["use_on"] if row is not None and "use_on" in row.keys() else "anywhere"
    return value if value in USE_ON else "anywhere"


def mac_only(site: str) -> bool:
    return use_on(site) == "mac"


def set_use_on(site: str, value: str, source: str | None = None) -> str:
    ""

    if value not in USE_ON:
        raise NoteError("Choose Use Anywhere or Only Your Mac.")
    key = _site(site)
    with signin_access.lock:
        with store.db() as c:
            if signin_access._row(key) is None:
                if not source:
                    raise NoteError("Save a login for this site first.")
                c.execute("INSERT INTO signin_access(site, mode, zips, use_on) VALUES (?, 'selected', ?, ?)",
                          (key, json.dumps([source]), value))
            else:
                c.execute("UPDATE signin_access SET use_on=?, version=version+1 WHERE site=?", (value, key))
    return value


def heard(site: str) -> str:
    ""
    notes = for_site(site)
    if not notes:
        return ""
    from . import who

    def by(author: str) -> str:
        if author == PERSON:
            return who.first()
        b = config.BOTS_BY_ID.get(author)
        return b.name if b else author
    lines = "; ".join(f"{by(n['author'])}: {n['text']}" for n in notes[-8:])
    where = " It is kept for the Mac only: sign in there, never in your own browser." if mac_only(site) else ""
    return f"Notes on {consent.base(site) or site}: {lines}.{where}"
