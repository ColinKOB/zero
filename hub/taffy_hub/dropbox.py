"""Dropbox through its own HTTP endpoints, with the sign-in in oauth.py and no model in between.

Read only: search, a folder's contents, and a text file's text (files.metadata.read and files.content.read are all
Zero asks for). Other files link out. File names and contents come from other people as well as the person: the
plugin tells the Zip never to follow instructions in them.

Tests point API and CONTENT at a local fake.
"""

import json
import re

from . import oauth

API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
LIMIT_MAX = 50
TEXT_MAX = 30000
DOWNLOAD_MAX = 2_000_000
TEXT_FILES = re.compile(r"\.(txt|md|markdown|csv|tsv|json|xml|ya?ml|html?|log|ini|cfg|conf|py|js|ts|swift|sh|rtf|tex)$",
                        re.I)


def _clean(v, limit: int = 300) -> str:
    return " ".join(str(v or "").split())[:limit]


def _post(path: str, body: dict | None, token: str | None = None) -> dict:
    return oauth.request("dropbox", "POST", f"{API}{path}", token=token, json=body).json()


def me(token: str | None = None) -> str:
    """The account's name. Proves the token reads files (list_folder) whether or not it may read the profile."""
    _post("/files/list_folder", {"path": "", "limit": 1}, token=token)
    try:
        a = oauth.request("dropbox", "POST", f"{API}/users/get_current_account", token=token).json()
    except oauth.OAuthError:
        return ""
    return _clean(a.get("email") or (a.get("name") or {}).get("display_name"), 120)


def _limit(n, default: int = 20) -> int:
    try:
        return max(1, min(int(n or default), LIMIT_MAX))
    except (TypeError, ValueError):
        return default


def _entry(e: dict) -> dict:
    out = {"name": _clean(e.get("name"), 200), "path": e.get("path_display"), "kind": e.get(".tag") or "file",
           "id": e.get("id")}
    if e.get(".tag") == "file":
        out.update(modified=e.get("server_modified"), size=e.get("size"))
    return out


def search(query: str, limit: int = 20) -> list[dict]:
    q = _clean(query, 200)
    if not q:
        raise oauth.OAuthError("Say what to look for.")
    r = _post("/files/search_v2", {"query": q, "options": {"max_results": _limit(limit), "file_status": "active"}})
    return [_entry((m.get("metadata") or {}).get("metadata") or {}) for m in r.get("matches") or []]


def _path(ref: str) -> str:
    p = str(ref or "").strip()
    if not p or p == "/":
        return ""
    if p.startswith("id:") or p.startswith("/"):
        return p[:1000]
    raise oauth.OAuthError("Give the path (it starts with /) or the id from search or list.")


def folder(ref: str = "", limit: int = 50) -> list[dict]:
    """A folder's files and folders (the top of the Dropbox by default)."""
    r = _post("/files/list_folder", {"path": _path(ref), "limit": _limit(limit, 50)})
    return [_entry(e) for e in r.get("entries") or []]


def read(ref: str, max_chars: int = TEXT_MAX) -> dict:
    """A text file's text. Other files say to open them in Dropbox."""
    p = _path(ref)
    if not p:
        raise oauth.OAuthError("Give the file's path or id from search or list.")
    meta = _post("/files/get_metadata", {"path": p})
    out = _entry(meta)
    if meta.get(".tag") != "file":
        return {**out, "text": "", "note": "That is a folder. Use list to see what is in it."}
    if not TEXT_FILES.search(str(meta.get("name") or "")):
        return {**out, "text": "", "note": "Zero can't read this kind of file as text yet. Open it in Dropbox instead."}
    if int(meta.get("size") or 0) > DOWNLOAD_MAX:
        return {**out, "text": "", "note": "This file is too large to read here. Open it in Dropbox instead."}
    r = oauth.request("dropbox", "POST", f"{CONTENT}/files/download",
                      headers={"Dropbox-API-Arg": json.dumps({"path": meta.get("id") or p})})
    cap = max(500, min(int(max_chars or TEXT_MAX), TEXT_MAX))
    text = r.content.decode("utf-8", errors="replace")
    return {**out, "text": text[:cap], "truncated": len(text) > cap}
