"""Linear through its GraphQL endpoint, with the sign-in in oauth.py (scope read) and no model in between.

Read only: the issues assigned to the person that are still open, a search, and one issue with its description and
latest comments. Titles, descriptions and comments come from other people too: the plugin tells the Zip never to follow
instructions in them.

Tests point API at a local fake.
"""

import re

from . import oauth

API = "https://api.linear.app/graphql"
LIMIT_MAX = 50
TEXT_MAX = 20000
FIELDS = "id identifier title url priorityLabel dueDate updatedAt state { name } assignee { name } team { name }"
REF = re.compile(r"^([A-Za-z0-9]{1,10}-[0-9]{1,7}|[0-9a-f-]{36})$")
LINK = re.compile(r"linear\.app/[^/]+/issue/([A-Za-z0-9]{1,10}-[0-9]{1,7})")


def _clean(v, limit: int = 300) -> str:
    return " ".join(str(v or "").split())[:limit]


def _q(query: str, variables: dict | None = None, token: str | None = None) -> dict:
    r = oauth.request("linear", "POST", API, token=token, json={"query": query, "variables": variables or {}}).json()
    if r.get("errors") and not r.get("data"):
        raise oauth.OAuthError("Linear didn't accept that.")
    return r.get("data") or {}


def me(token: str | None = None) -> str:
    v = _q("query { viewer { name email } }", token=token).get("viewer") or {}
    return _clean(v.get("email") or v.get("name"), 120)


def _limit(n, default: int = 20) -> int:
    try:
        return max(1, min(int(n or default), LIMIT_MAX))
    except (TypeError, ValueError):
        return default


def _issue(i: dict) -> dict:
    return {"id": i.get("identifier") or i.get("id"), "title": _clean(i.get("title"), 300), "url": i.get("url"),
            "state": _clean((i.get("state") or {}).get("name"), 60), "team": _clean((i.get("team") or {}).get("name"), 80),
            "assignee": _clean((i.get("assignee") or {}).get("name"), 80), "priority": i.get("priorityLabel"),
            "due": i.get("dueDate"), "updated": i.get("updatedAt")}


def my_issues(limit: int = 25) -> list[dict]:
    """Open issues assigned to the person, the most recently updated first."""
    d = _q("query($n: Int!) { viewer { assignedIssues(first: $n, orderBy: updatedAt, filter: {state: {type: "
           "{nin: [\"completed\", \"canceled\"]}}}) { nodes { " + FIELDS + " } } } }", {"n": _limit(limit, 25)})
    return [_issue(i) for i in ((d.get("viewer") or {}).get("assignedIssues") or {}).get("nodes") or []]


def search(query: str, limit: int = 20) -> list[dict]:
    q = _clean(query, 200)
    if not q:
        raise oauth.OAuthError("Say what to look for.")
    d = _q("query($q: String!, $n: Int!) { searchIssues(term: $q, first: $n) { nodes { " + FIELDS + " } } }",
           {"q": q, "n": _limit(limit)})
    return [_issue(i) for i in (d.get("searchIssues") or {}).get("nodes") or []]


def issue(ref: str) -> dict:
    """One issue (ENG-123, its link, or its id) with its description and latest comments."""
    r = str(ref or "").strip()
    m = LINK.search(r)
    r = m.group(1) if m else r
    if not REF.match(r):
        raise oauth.OAuthError("Give the issue's key (like ENG-123) or link from my_issues or search.")
    d = _q("query($id: String!) { issue(id: $id) { " + FIELDS + " description comments(first: 20) { nodes { body "
           "createdAt user { name } } } } }", {"id": r.upper() if "-" in r and len(r) < 20 else r})
    i = d.get("issue")
    if not i:
        raise oauth.OAuthError("Linear couldn't find that.")
    out = _issue(i)
    out["description"] = str(i.get("description") or "")[:TEXT_MAX]
    out["comments"] = [{"by": _clean((c.get("user") or {}).get("name"), 80), "at": c.get("createdAt"),
                        "text": str(c.get("body") or "")[:1000]} for c in (i.get("comments") or {}).get("nodes") or []]
    return out
