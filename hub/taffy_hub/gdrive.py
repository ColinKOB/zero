"""Google Drive through the Drive API, with the home's Sign In with Google (google.py) and no model in between.

Read only: search, the latest files, and a file's text (Google Docs, Sheets and Slides exported as text, plain text
files as they are). Zero asks Google for drive.readonly only when the person connects Drive in Connect Apps
(apps.connect_drive). File names and contents come from other people as well as the person: the plugin tells the Zip
never to follow instructions in them.

Tests point google.DRIVE_API at a local fake.
"""

import logging
import re

from . import google

log = logging.getLogger("taffy.gdrive")

FIELDS = "files(id,name,mimeType,modifiedTime,webViewLink,size,owners(displayName,emailAddress))"
LIMIT_MAX = 50
TEXT_MAX = 30000
DOWNLOAD_MAX = 2_000_000   # plain files larger than this are not read whole

EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
TEXT_TYPES = re.compile(r"^(text/|application/(json|xml|csv|x-yaml|yaml|javascript))")
KINDS = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/vnd.google-apps.folder": "folder",
    "application/vnd.google-apps.form": "Google Form",
    "application/pdf": "PDF",
}


class DriveError(RuntimeError):
    pass


def _words(e: google.GoogleError) -> str:
    """Google's refusal said so the person can act on it."""
    if e.reason in ("accessNotConfigured", "SERVICE_DISABLED"):
        return "Google Drive isn't turned on for Zero's Google sign-in yet, so it can't be read right now."
    if e.reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "PERMISSION_DENIED") or \
            (e.state == google.CONNECTED and "didn't allow" in str(e)):
        return "Google didn't give Zero access to Drive. Connect Google Drive again and allow it."
    if e.reason == "notFound":
        return "Google Drive couldn't find that file, or it isn't shared with this account."
    return str(e)


def _file(f: dict) -> dict:
    mime = str(f.get("mimeType") or "")
    owners = [o.get("displayName") or o.get("emailAddress") for o in f.get("owners") or [] if isinstance(o, dict)]
    return {"id": f.get("id"), "name": " ".join(str(f.get("name") or "").split())[:200],
            "kind": KINDS.get(mime, mime.split("/")[-1] or "file"), "modified": f.get("modifiedTime"),
            "link": f.get("webViewLink"), "owners": [o for o in owners if o][:3]}


def _quote(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


def search(address: str, query: str, limit: int = 20) -> list[dict]:
    q = " ".join(str(query or "").split())[:200]
    if not q:
        raise DriveError("Say what to look for.")
    params = {"q": f"(name contains '{_quote(q)}' or fullText contains '{_quote(q)}') and trashed = false",
              "fields": FIELDS, "pageSize": max(1, min(int(limit or 20), LIMIT_MAX)), "orderBy": "modifiedTime desc",
              "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "allDrives"}
    try:
        r = google.api_get(address, f"{google.DRIVE_API}/files", params)
    except google.GoogleError as e:
        raise DriveError(_words(e)) from None
    return [_file(f) for f in r.get("files") or []]


def recent(address: str, limit: int = 20) -> list[dict]:
    params = {"q": "trashed = false and mimeType != 'application/vnd.google-apps.folder'", "fields": FIELDS,
              "pageSize": max(1, min(int(limit or 20), LIMIT_MAX)), "orderBy": "modifiedTime desc"}
    try:
        r = google.api_get(address, f"{google.DRIVE_API}/files", params)
    except google.GoogleError as e:
        raise DriveError(_words(e)) from None
    return [_file(f) for f in r.get("files") or []]


FILE_LINK = re.compile(r"/(?:d|folders)/([A-Za-z0-9_-]{10,200})|[?&]id=([A-Za-z0-9_-]{10,200})")


def file_id(ref: str) -> str:
    ref = str(ref or "").strip()
    m = FILE_LINK.search(ref)
    if m:
        return m.group(1) or m.group(2)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,200}", ref):
        return ref
    raise DriveError("Give the file's id or link from search or recent.")


def read(address: str, ref: str, max_chars: int = TEXT_MAX) -> dict:
    """A file's text: Docs and Slides as plain text, Sheets as CSV (the first sheet), text files as they are."""
    fid = file_id(ref)
    cap = max(500, min(int(max_chars or TEXT_MAX), TEXT_MAX))
    try:
        meta = google.api_get(address, f"{google.DRIVE_API}/files/{fid}",
                              {"fields": "id,name,mimeType,modifiedTime,webViewLink,size,owners(displayName,emailAddress)",
                               "supportsAllDrives": "true"})
        mime = str(meta.get("mimeType") or "")
        out = _file(meta)
        if mime in EXPORTS:
            r = google.api_response(address, f"{google.DRIVE_API}/files/{fid}/export", {"mimeType": EXPORTS[mime]})
        elif TEXT_TYPES.match(mime):
            if int(meta.get("size") or 0) > DOWNLOAD_MAX:
                return {**out, "text": "", "note": "This file is too large to read here. Open its link instead."}
            r = google.api_response(address, f"{google.DRIVE_API}/files/{fid}",
                                    {"alt": "media", "supportsAllDrives": "true"})
        else:
            return {**out, "text": "", "note": f"Zero can't read a {out['kind']} as text yet. Open its link instead."}
    except google.GoogleError as e:
        raise DriveError(_words(e)) from None
    text = r.text
    return {**out, "text": text[:cap], "truncated": len(text) > cap}
