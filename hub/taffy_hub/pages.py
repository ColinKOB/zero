""

















import html
import io
import logging
import re
import secrets
import sqlite3
import time
import zipfile
from datetime import datetime

import httpx

from . import home, sites, store

log = logging.getLogger("taffy.pages")

TITLE_MAX = 120
BODY_MAX = 100_000
READY_WAIT = 45
POLL_EVERY = 1.5
NAME_BITS = 10


_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")


class PageError(Exception):
    ""


@store.schema
def _tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shared_pages (
            id TEXT PRIMARY KEY,
            bot TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            site_id TEXT NOT NULL,
            url TEXT NOT NULL,
            created REAL NOT NULL,
            updated REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS shared_pages_bot ON shared_pages(bot);
    """)




def _inline(text: str) -> str:
    ""
    out = html.escape(text, quote=True)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", out)

    def link(m: re.Match) -> str:
        return f'<a href="{m.group(2)}" rel="noopener noreferrer">{m.group(1)}</a>'
    out = re.sub(r"\[([^\]]+)\]\((https?://[^\s)\"<>]+)\)", link, out)
    out = re.sub(r'(?<![="\w>])(https?://[^\s<"]+[^\s<".,;:!?)])',
                 r'<a href="\1" rel="noopener noreferrer">\1</a>', out)
    return out


def _blocks(body: str) -> str:
    ""
    out, para, items, kind = [], [], [], ""

    def flush():
        nonlocal para, items, kind
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para = []
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{kind}>")
            items, kind = [], ""

    for raw in body.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        heading = re.match(r"(#{1,3})\s+(.*)", line)
        bullet = re.match(r"[-*•]\s+(.*)", line)
        number = re.match(r"\d+[.)]\s+(.*)", line)
        if not line:
            flush()
        elif heading:
            flush()
            level = 3 if len(heading.group(1)) == 3 else 2
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", line):
            flush()
            out.append("<hr>")
        elif bullet or number:
            want = "ul" if bullet else "ol"
            if para or (items and kind != want):
                flush()
            kind = want
            items.append((bullet or number).group(1))
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "\n".join(out)


def _stamp(at: float) -> str:
    when = datetime.fromtimestamp(at, home.zone())
    return when.strftime("%b %-d, %Y at %-I:%M %p").replace(":00 ", " ")


def render(title: str, body: str, updated: float) -> str:
    ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light dark; --ink: #1d1d1f; --soft: #6e6e73; --line: #e5e5ea; --bg: #ffffff; --link: #0a66d8; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink: #f5f5f7; --soft: #a1a1a6; --line: #38383a; --bg: #000000; --link: #4da3ff; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 17px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
main {{ max-width: 680px; margin: 0 auto; padding: 40px 20px 64px; }}
h1 {{ font-size: 28px; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.01em; }}
.updated {{ color: var(--soft); font-size: 14px; margin: 0 0 28px; }}
h2 {{ font-size: 20px; margin: 32px 0 8px; }}
h3 {{ font-size: 17px; margin: 24px 0 6px; }}
p, ul, ol {{ margin: 0 0 14px; }}
ul, ol {{ padding-left: 22px; }}
li {{ margin: 4px 0; }}
a {{ color: var(--link); text-decoration: none; word-break: break-word; }}
a:hover {{ text-decoration: underline; }}
hr {{ border: 0; border-top: 1px solid var(--line); margin: 28px 0; }}
</style>
</head>
<body>
<main>
<h1>{html.escape(title)}</h1>
<p class="updated">Updated {_stamp(updated)}</p>
{_blocks(body)}
</main>
</body>
</html>
"""


def _bundle(page: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", page)

        z.writestr("_headers", "/*\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: no-referrer\n")
    return buf.getvalue()


def check(title: str, body: str) -> tuple[str, str]:
    title = " ".join(str(title or "").split())
    body = str(body or "").strip()
    if not title:
        raise PageError("Give the page a title.")
    if len(title) > TITLE_MAX:
        raise PageError(f"The title is too long (at most {TITLE_MAX} characters).")
    if not body:
        raise PageError("The page has nothing on it. Write what it should say.")
    if len(body) > BODY_MAX:
        raise PageError("The page is too long. Keep it to what someone needs at a glance.")
    for text in (title, body):
        if _SSN.search(text) or any(_luhn(m.group(0)) for m in _CARD.finditer(text)):
            raise PageError("Nothing went online: the page has what looks like a card or Social Security number. "
                            "Take it out; a shared page never carries those.")
    return title, body


def _luhn(text: str) -> bool:
    digits = [int(c) for c in text if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0




def _client() -> httpx.Client:
    return httpx.Client(timeout=60, transport=sites.TRANSPORT)


def _headers() -> dict:
    try:
        return sites._headers()
    except sites.NotConnected:
        raise PageError("This home is not connected to Netlify, so pages can't go online yet. Connect Netlify "
                        "once for this home and I will put them online.") from None


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24].strip("-") or "page"


def _new_site(client: httpx.Client, headers: dict, title: str) -> dict:
    for _ in range(3):
        name = f"{_slug(title)}-{secrets.token_hex(NAME_BITS)}"
        r = client.post(f"{sites.API}/sites", headers=headers, json={"name": name})
        if r.status_code == 422:
            continue
        if r.status_code >= 400:
            raise PageError(f"Netlify would not make the page ({r.status_code}).")
        site = sites._json(r)
        if site.get("id"):
            return site
        raise PageError("Netlify did not return the new page.")
    raise PageError("Netlify would not make the page. Try again.")


def _deploy(client: httpx.Client, headers: dict, site_id: str, data: bytes) -> None:
    r = client.post(f"{sites.API}/sites/{site_id}/deploys", headers={**headers, "Content-Type": "application/zip"},
                    content=data)
    if r.status_code >= 400:
        raise PageError(f"Netlify would not take the page ({r.status_code}).")
    dep = sites._json(r)
    deadline = time.monotonic() + READY_WAIT
    while dep.get("state") not in ("ready", "error") and dep.get("id") and time.monotonic() < deadline:
        time.sleep(POLL_EVERY)
        dep = sites._json(client.get(f"{sites.API}/deploys/{dep['id']}", headers=headers))
    if dep.get("state") == "error":
        raise PageError("Netlify could not put the page up. Try again.")


def _delete_site(client: httpx.Client, headers: dict, site_id: str) -> None:
    r = client.delete(f"{sites.API}/sites/{site_id}", headers=headers)
    if r.status_code >= 400 and r.status_code != 404:
        raise PageError(f"Netlify would not take the page down ({r.status_code}).")




def _public(row: sqlite3.Row, body: bool = False) -> dict:
    out = {"id": row["id"], "title": row["title"], "link": row["url"], "updated": _stamp(row["updated"])}
    if body:
        out["text"] = row["body"]
    return out


def _mine(bot: str) -> list[sqlite3.Row]:
    return store.db().execute(
        "SELECT * FROM shared_pages WHERE bot = ? OR ? = 'default' ORDER BY updated DESC", (bot, bot)).fetchall()


def find(bot: str, page: str) -> sqlite3.Row:
    ""
    page = " ".join(str(page or "").split())
    if not page:
        raise PageError("Say which page: its id or its title.")
    rows = _mine(bot)
    for r in rows:
        if r["id"] == page:
            return r
    low = page.lower()
    exact = [r for r in rows if r["title"].lower() == low]
    hits = exact or [r for r in rows if low in r["title"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise PageError(f"I have no shared page called \"{page}\". The list action shows the pages I keep.")
    raise PageError("More than one page matches: " + "; ".join(f"{r['title']} ({r['id']})" for r in hits[:5])
                    + ". Give the id.")


def list_pages(bot: str) -> list[dict]:
    return [_public(r) for r in _mine(bot)]


def get(bot: str, page: str) -> dict:
    return _public(find(bot, page), body=True)


def publish(bot: str, title: str, body: str, page: str = "", now: float | None = None) -> dict:
    ""
    row = find(bot, page) if page else None
    title, body = check(title or (row["title"] if row is not None else ""), body)
    at = now if now is not None else time.time()
    headers = _headers()
    data = _bundle(render(title, body, at))
    with _client() as client:
        if row is not None:
            _deploy(client, headers, row["site_id"], data)
            with store._lock:
                conn = store.db()
                conn.execute("UPDATE shared_pages SET title = ?, body = ?, updated = ? WHERE id = ?",
                             (title, body, at, row["id"]))
                conn.commit()
            return {**_public(store.db().execute("SELECT * FROM shared_pages WHERE id = ?", (row["id"],)).fetchone()),
                    "updated_page": True}
        site = _new_site(client, headers, title)
        try:
            _deploy(client, headers, site["id"], data)
        except Exception:
            _delete_site(client, headers, site["id"])
            raise
    url = site.get("ssl_url") or site.get("url") or f"https://{site.get('name')}.netlify.app"
    pid = secrets.token_hex(4)
    with store._lock:
        conn = store.db()
        conn.execute("INSERT INTO shared_pages (id, bot, title, body, site_id, url, created, updated) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (pid, bot, title, body, site["id"], url, at, at))
        conn.commit()
    log.info("page %s online for %s", pid, bot)
    return _public(store.db().execute("SELECT * FROM shared_pages WHERE id = ?", (pid,)).fetchone())


def take_down(bot: str, page: str) -> dict:
    row = find(bot, page)
    with _client() as client:
        _delete_site(client, _headers(), row["site_id"])
    with store._lock:
        conn = store.db()
        conn.execute("DELETE FROM shared_pages WHERE id = ?", (row["id"],))
        conn.commit()
    log.info("page %s taken down for %s", row["id"], bot)
    return {"title": row["title"], "taken_down": True}


def take_down_all() -> tuple[int, int]:
    ""

    rows = store.db().execute("SELECT id, bot FROM shared_pages").fetchall()
    gone = 0
    for r in rows:
        try:
            take_down("default", r["id"])
            gone += 1
        except Exception:
            log.warning("could not take down page %s", r["id"], exc_info=True)
    return gone, len(rows) - gone


def handle(bot: str, args: dict) -> dict:
    ""
    action = str(args.get("action") or "")
    if action == "list":
        return {"pages": list_pages(bot)}
    if action == "get":
        return get(bot, str(args.get("page") or ""))
    if action == "publish":
        return publish(bot, str(args.get("title") or ""), str(args.get("text") or ""), str(args.get("page") or ""))
    if action == "take_down":
        return take_down(bot, str(args.get("page") or ""))
    raise PageError("action must be publish, list, get or take_down.")
