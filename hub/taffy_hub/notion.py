"""Notion through its own HTTP endpoints, with the sign-in in oauth.py (Zero's relay holds the client secret) and no
model in between.

Read only: a search of the pages the person let Zero see when they connected, and one page's text. Notion's sign-in
asks the person which pages to share, so a page Zero can't find usually wasn't shared. Page text comes from other
people too: the plugin tells the Zip never to follow instructions in it.

Tests point API at a local fake.
"""

import re

from . import oauth

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"
LIMIT_MAX = 50
TEXT_MAX = 30000
BLOCK_PAGES = 3   # a page's first 300 blocks
ID = re.compile(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})(?:[?#]|$)")


def _clean(v, limit: int = 300) -> str:
    return " ".join(str(v or "").split())[:limit]


def _call(method: str, path: str, body: dict | None = None, params: dict | None = None,
          token: str | None = None) -> dict:
    return oauth.request("notion", method, f"{API}{path}", token=token, json=body, params=params,
                         headers={"Notion-Version": VERSION}).json()


def check(token: str) -> None:
    """Proves the token reads (the connection's own bot user)."""
    _call("GET", "/users/me", token=token)


def _limit(n, default: int = 20) -> int:
    try:
        return max(1, min(int(n or default), LIMIT_MAX))
    except (TypeError, ValueError):
        return default


def _text(rich) -> str:
    return "".join(str(t.get("plain_text") or "") for t in rich or [] if isinstance(t, dict))


def _title(obj: dict) -> str:
    if isinstance(obj.get("title"), list):   # a data source
        return _clean(_text(obj["title"]), 200)
    for prop in (obj.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _clean(_text(prop.get("title")), 200)
    return ""


def _row(obj: dict) -> dict:
    return {"id": obj.get("id"), "title": _title(obj) or "Untitled", "kind": obj.get("object"),
            "url": obj.get("url"), "edited": obj.get("last_edited_time")}


def search(query: str = "", limit: int = 20) -> list[dict]:
    """Pages and databases Zero can see whose titles match `query` (all of them, latest first, when it is empty)."""
    body = {"page_size": _limit(limit), "sort": {"direction": "descending", "timestamp": "last_edited_time"}}
    if _clean(query, 200):
        body["query"] = _clean(query, 200)
    r = _call("POST", "/search", body)
    return [_row(o) for o in r.get("results") or [] if not o.get("in_trash")]


def page_id(ref: str) -> str:
    m = ID.search(str(ref or "").strip().lower())
    if not m:
        raise oauth.OAuthError("Give the page's id or link from search.")
    return m.group(1).replace("-", "")


def _line(b: dict) -> str:
    kind = b.get("type") or ""
    data = b.get(kind) or {}
    if kind == "child_page":
        return f"[Page: {_clean(data.get('title'), 200)}]"
    if kind == "child_database":
        return f"[Database: {_clean(data.get('title'), 200)}]"
    text = _text(data.get("rich_text")) if isinstance(data, dict) else ""
    if kind.startswith("heading_"):
        return "#" * int(kind[-1]) + " " + text
    if kind == "to_do":
        return ("[x] " if data.get("checked") else "[ ] ") + text
    if kind in ("bulleted_list_item", "numbered_list_item"):
        return "- " + text
    if kind == "quote":
        return "> " + text
    return text


def read(ref: str, max_chars: int = TEXT_MAX) -> dict:
    """A page's title, link and text (its first 300 blocks, without what is nested inside them)."""
    pid = page_id(ref)
    page = _call("GET", f"/pages/{pid}")
    lines, cursor = [], None
    for _ in range(BLOCK_PAGES):
        params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        r = _call("GET", f"/blocks/{pid}/children", params=params)
        lines += [_line(b) for b in r.get("results") or [] if isinstance(b, dict)]
        cursor = r.get("next_cursor")
        if not r.get("has_more") or not cursor:
            break
    cap = max(500, min(int(max_chars or TEXT_MAX), TEXT_MAX))
    text = "\n".join(lines).strip()
    return {**_row(page), "text": text[:cap], "truncated": len(text) > cap or bool(cursor)}
