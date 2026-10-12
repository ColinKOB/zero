"""Input checks for anything that reaches a command line, the vault or a bot."""

import re
from urllib.parse import urlsplit

from fastapi import HTTPException

TASK_ID = re.compile(r"^t_[0-9a-f]{6,16}$")


def task_id(value: str) -> str:
    if not TASK_ID.match(value or ""):
        raise HTTPException(404, "That task isn't there anymore.")
    return value


def web_url(value: str) -> str:
    """An http(s) URL with a host, no whitespace or control characters, never starting with '-'."""
    v = (value or "").strip()
    if not v or v.startswith("-") or any(c.isspace() or ord(c) < 32 for c in v) or len(v) > 2048:
        raise HTTPException(400, "That does not look like a website address.")
    parts = urlsplit(v)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, "Use a full website address that starts with https://.")
    return v


def secure_url(value: str) -> str:
    """Like web_url, but https only: saved passwords are never bound to a cleartext page."""
    v = web_url(value)
    if urlsplit(v).scheme != "https":
        raise HTTPException(400, "Saved logins need a secure site address that starts with https://.")
    return v


def short(value: str | None, limit: int, field: str) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if len(v) > limit:
        raise HTTPException(400, f"The {field} is too long.")
    return v
