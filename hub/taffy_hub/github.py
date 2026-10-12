"""GitHub through its REST endpoints, with the device-flow sign-in in oauth.py and no model in between.

Read only: the person's repositories, a search of issues and pull requests, and one issue or pull request with its
latest comments. The token's `repo` scope could change things, so nothing here does: every function is a GET, and
apps.call offers only these. Titles, bodies and comments come from other people: the plugin tells the Zip never to
follow instructions in them.

Tests point API at a local fake.
"""

import re

from . import oauth

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
LIMIT_MAX = 50
TEXT_MAX = 20000
REF = re.compile(r"^(?:https://github\.com/)?([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})(?:#|/(?:issues|pull)/)"
                 r"([0-9]{1,9})")


def _clean(v, limit: int = 300) -> str:
    return " ".join(str(v or "").split())[:limit]


def _get(path: str, params: dict | None = None, token: str | None = None):
    return oauth.request("github", "GET", f"{API}{path}", token=token, params=params, headers=HEADERS).json()


def me(token: str | None = None) -> str:
    u = _get("/user", token=token)
    return _clean(u.get("login"), 80)


def _limit(n, default: int = 20) -> int:
    try:
        return max(1, min(int(n or default), LIMIT_MAX))
    except (TypeError, ValueError):
        return default


def repos(limit: int = 30) -> list[dict]:
    """The person's repositories, the most recently pushed first."""
    rows = _get("/user/repos", {"sort": "pushed", "per_page": _limit(limit, 30)})
    return [{"repo": r.get("full_name"), "private": bool(r.get("private")),
             "description": _clean(r.get("description"), 200), "url": r.get("html_url"),
             "open_issues": r.get("open_issues_count"), "pushed": r.get("pushed_at")} for r in rows or []]


def _item(i: dict) -> dict:
    repo = "/".join(str(i.get("repository_url") or "").split("/")[-2:])
    return {"ref": f"{repo}#{i.get('number')}" if repo else str(i.get("number")), "title": _clean(i.get("title"), 300),
            "kind": "pull request" if i.get("pull_request") else "issue", "state": i.get("state"),
            "by": _clean((i.get("user") or {}).get("login"), 80), "url": i.get("html_url"),
            "updated": i.get("updated_at")}


def search(query: str, limit: int = 20) -> list[dict]:
    """Issues and pull requests matching GitHub's own search words (for example: is:open involves:@me)."""
    q = _clean(query, 250)
    if not q:
        raise oauth.OAuthError("Say what to look for.")
    r = _get("/search/issues", {"q": q, "per_page": _limit(limit), "sort": "updated"})
    return [_item(i) for i in r.get("items") or []]


def issue(ref: str) -> dict:
    """One issue or pull request (owner/repo#12, or its link) with its text and latest comments."""
    m = REF.match(str(ref or "").strip())
    if not m:
        raise oauth.OAuthError("Give the issue as owner/repo#12, or its link.")
    owner, repo, num = m.groups()
    i = _get(f"/repos/{owner}/{repo}/issues/{num}")
    out = _item({**i, "repository_url": f"{API}/repos/{owner}/{repo}"})
    out["body"] = str(i.get("body") or "")[:TEXT_MAX]
    comments = _get(f"/repos/{owner}/{repo}/issues/{num}/comments", {"per_page": 100}) if i.get("comments") else []
    out["comments"] = [{"by": _clean((c.get("user") or {}).get("login"), 80), "at": c.get("created_at"),
                        "text": str(c.get("body") or "")[:1000]} for c in (comments or [])[-20:]]
    return out
