""















import json
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

TIMEOUT_SEC = 30
TEXT_MAX = 30000


class KeyAppError(RuntimeError):
    ""


    def __init__(self, message: str, rejected: bool = False, why: str = ""):
        super().__init__(message)
        self.rejected = rejected
        self.why = why


@dataclass(frozen=True)
class KeyApp:
    id: str
    name: str
    sites: tuple[str, ...]
    api: str
    key_url: str
    make: str
    can: str
    blurb: str
    graphql: str = ""
    post_reads: str = ""
    reads: str = ""


CLOUDFLARE_PERMISSIONS = (
    {"key": "zone", "type": "read"},
    {"key": "dns", "type": "read"},
    {"key": "account_settings", "type": "read"},
    {"key": "workers_scripts", "type": "edit"},
    {"key": "workers_routes", "type": "edit"},
)
CLOUDFLARE_RECONNECT = "Connect Cloudflare again so I can verify permission to publish the complete project."
CLOUDFLARE_CONNECT_URL = "https://dash.cloudflare.com/profile/api-tokens?" + urlencode({
    "permissionGroupKeys": json.dumps(CLOUDFLARE_PERMISSIONS, separators=(",", ":")),
    "accountId": "*", "zoneId": "all", "name": "Zero shared access",
})


APPS = (
    KeyApp("posthog", "PostHog", ("posthog.com",), "https://us.posthog.com",
           "https://us.posthog.com/settings/user-api-keys",
           "Create a personal API key, choose Read-only, and copy it.",
           "Your Zips read your PostHog events, insights and errors. They don't change anything.",
           "Read your events and insights", post_reads=r"^/api/(?:environments|projects)/[^/]+/query/?$",
           reads=r"^/api/(?:users/@me|organizations(?:/[^/]+)?|projects|(?:environments|projects)/[^/]+"
                 r"(?:/(?:insights|dashboards|events|persons|cohorts|feature_flags|experiments|surveys|annotations"
                 r"|actions|event_definitions|property_definitions|error_tracking/issues|session_recordings)"
                 r"(?:/[^/]+)?)?)/?$"),
    KeyApp("railway", "Railway", ("railway.app", "railway.com"), "https://backboard.railway.com",
           "https://railway.com/account/tokens", "Create an account token and copy it.",
           "Your Zips read your Railway projects, deployments and logs. Railway asks for member access to show logs. "
           "They don't change anything.",
           "Read your projects and deploys", graphql="/graphql/v2"),
    KeyApp("cloudflare", "Cloudflare", ("cloudflare.com",), "https://api.cloudflare.com/client/v4",
           CLOUDFLARE_CONNECT_URL,
           "The site permissions are filled in. Add User > API Tokens > Read, then create the key and copy it.",
           "Your Zips read your sites, DNS and Workers. I ask before publishing a change.",
           "Read your sites and publish with your approval", graphql="/graphql",
           reads=r"^/(?:user|user/tokens/verify|zones|zones/[^/]+(?:/(?:dns_records(?:/[^/]+)?|settings(?:/[^/]+)?"
                 r"|pagerules|rulesets|ssl/certificate_packs|analytics/dashboard))?|accounts|accounts/[^/]+"
                 r"(?:/(?:workers/(?:scripts(?:/[^/]+/(?:deployments|subdomain))?|subdomain)"
                 r"|pages/projects(?:/[^/]+(?:/deployments(?:/[^/]+)?)?)?"
                 r"|registrar/domains(?:/[^/]+)?))?)/?$"),
    KeyApp("vercel", "Vercel", ("vercel.com",), "https://api.vercel.com", "https://vercel.com/account/tokens",
           "Create a token and copy it.",
           "Your Zips read your Vercel projects and deployments. They don't change anything.",
           "Read your projects and deploys",
           reads=r"^/v\d+/(?:user|teams(?:/[^/]+)?|projects(?:/[^/]+(?:/domains(?:/[^/]+)?)?)?"
                 r"|deployments(?:/[^/]+(?:/events)?)?|domains(?:/[^/]+)?)/?$"),
    KeyApp("stripe", "Stripe", ("stripe.com",), "https://api.stripe.com", "https://dashboard.stripe.com/apikeys",
           "Create a restricted key with Read access, and copy it.",
           "Your Zips read your Stripe payments, customers and payouts. They don't change anything.",
           "Read your payments and payouts",
           reads=r"^/v1/(?:account|balance|balance_transactions|charges|payment_intents|customers|payouts|invoices"
                 r"|subscriptions|refunds|disputes|products|prices|events|checkout/sessions)(?:/[^/]+)?/?$"),
)
BY_ID = {a.id: a for a in APPS}
_BY_SITE = {s: a.id for a in APPS for s in a.sites}


def for_site(site_base: str) -> str | None:
    ""
    return _BY_SITE.get(str(site_base or "").lower())


def _send(method: str, url: str, key: str, body: dict | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    return httpx.request(method, url, headers=headers, json=body, timeout=TIMEOUT_SEC)


def _clean_key(pasted: str) -> str:
    key = "".join(str(pasted or "").split())
    return key.removeprefix("Bearer").strip()




def _rejected(app_id: str, r: httpx.Response) -> bool:
    if r.status_code == 401:
        return True
    if app_id == "vercel" and r.status_code == 403:
        return True
    if app_id == "cloudflare" and r.status_code == 400:
        return True
    if app_id == "railway":
        data = _json(r)
        return not ((data.get("data") or {}).get("me"))
    return False


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _proof_reply(a: KeyApp, r: httpx.Response, permission: str) -> dict:
    if r.status_code == 401:
        raise KeyAppError(f"{a.name} didn't accept that key. Make a new one and paste it again.", rejected=True)
    if r.status_code == 403:
        raise KeyAppError(f"{a.name} refused this read. {permission}")
    if r.status_code == 429 or r.status_code >= 500:
        raise KeyAppError(f"{a.name} didn't answer just now. Try again in a minute.")
    data = _json(r)
    if not 200 <= r.status_code < 300 or not data or data.get("error") or data.get("errors") or data.get("success") is False:
        raise KeyAppError(f"{a.name} didn't return a readable result. Check the key's read access and try again.")
    return data


def _proof_label(value, key: str) -> str:
    return " ".join(value.replace(key, "(hidden)").split())[:120] if isinstance(value, str) else ""


def _proof_rows(a: KeyApp, data: dict, field: str) -> list[dict]:
    rows = data.get(field)
    if (not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                                        or not row["id"].strip() for row in rows)):
        raise KeyAppError(f"{a.name} didn't return a readable list. Try again before connecting it.")
    if a.id == "cloudflare" and data.get("success") is not True:
        raise KeyAppError("Cloudflare didn't confirm that the list was readable. Try again before connecting it.")
    if a.id == "stripe" and (data.get("object") != "list" or not isinstance(data.get("has_more"), bool)
                             or (not rows and data["has_more"])):
        raise KeyAppError("Stripe didn't return a readable payments list. Try again before connecting it.")
    return rows


def _check_resources(a: KeyApp, key: str) -> dict:
    ""
    account = ""
    identity_detail = ""
    resource = {}
    if a.id == "cloudflare":
        permission = "Give the key Zone Read access for your sites and try again."
        verified = _proof_reply(a, _send("GET", a.api + "/user/tokens/verify", key), permission)
        token = verified.get("result")
        if verified.get("success") is not True or not isinstance(token, dict) or token.get("status") != "active":
            raise KeyAppError("Cloudflare didn't confirm an active key. Make a new one and paste it again.")
        data = _proof_reply(a, _send("GET", a.api + "/zones?per_page=5", key), permission)
        rows = _proof_rows(a, data, "result")
        if rows:
            zone = rows[0]
            name = _proof_label(zone.get("name"), key)
            if not name:
                raise KeyAppError("Cloudflare didn't return a site name. Check the key's Zone Read access and try again.")
            owner = zone.get("account")
            owner = owner if isinstance(owner, dict) else {}
            account = _proof_label(owner.get("name"), key) or _proof_label(owner.get("id"), key) or name
            resource = {"id": _proof_label(zone["id"], key), "name": name}
            detail = f"I read the site {name}."
        else:

            data = _proof_reply(a, _send("GET", a.api + "/accounts?per_page=5", key),
                                "Give the key Account Settings Read access so I can identify the account, then try again.")
            accounts = _proof_rows(a, data, "result")
            if not accounts:
                raise KeyAppError("Cloudflare returned no sites or account identity. Check the key's account access and try again.")
            account = _proof_label(accounts[0].get("name"), key) or _proof_label(accounts[0]["id"], key)
            detail = "Cloudflare returned no sites for this key."
        kind = "sites"
    elif a.id == "vercel":
        permission = "Check the token's account and project access, then try again."
        data = _proof_reply(a, _send("GET", a.api + "/v2/user", key), permission)
        user = data.get("user")
        if isinstance(user, dict):
            account = (_proof_label(user.get("email"), key) or _proof_label(user.get("username"), key)
                       or _proof_label(user.get("id"), key))
        if not account:
            raise KeyAppError("Vercel didn't return your account identity. Check the token's account access and try again.")
        data = _proof_reply(a, _send("GET", a.api + "/v10/projects?limit=1", key), permission)
        rows = _proof_rows(a, data, "projects")
        if rows:
            name = _proof_label(rows[0].get("name"), key)
            if not name:
                raise KeyAppError("Vercel didn't return a project name. Check the token's project access and try again.")
            resource = {"id": _proof_label(rows[0]["id"], key), "name": name}
        detail = f"I read the project {resource['name']}." if rows else "Vercel returned no projects for this key."
        kind = "projects"
    else:
        permission = "Give the restricted key Read access to Payment Intents and try again."
        r = _send("GET", a.api + "/v1/account", key)
        if r.status_code == 403:
            identity_detail = "Stripe did not allow me to read the account name. "
        else:
            data = _proof_reply(a, r, "Check the key's account read access and try again.")
            if data.get("object") != "account" or not isinstance(data.get("id"), str) or not data["id"].strip():
                raise KeyAppError("Stripe didn't return a readable account identity. Check the key's account access and try again.")
            settings = data.get("settings")
            dashboard = settings.get("dashboard") if isinstance(settings, dict) else None
            name = dashboard.get("display_name") if isinstance(dashboard, dict) else None
            account = _proof_label(name, key) or _proof_label(data.get("email"), key) or _proof_label(data["id"], key)
        data = _proof_reply(a, _send("GET", a.api + "/v1/payment_intents?limit=1", key), permission)
        rows = _proof_rows(a, data, "data")
        if rows:
            resource = {"id": _proof_label(rows[0]["id"], key)}
        detail = f"I read payment {resource['id']}." if rows else "Stripe returned no payments for this key."
        kind = "payments"
    proof = {"kind": kind, "empty": not rows, "resource": resource, "detail": identity_detail + detail}
    return {"key": key, "account": account, "api": a.api, "proof": proof}


def check(app_id: str, pasted: str) -> dict:
    ""
    a = BY_ID[app_id]
    key = _clean_key(pasted)
    if len(key) < 16:
        raise KeyAppError(f"That doesn't look like a {a.name} key. Copy the whole key and paste it again.")
    api = a.api
    try:
        if app_id in ("cloudflare", "vercel", "stripe"):
            return _check_resources(a, key)
        if app_id == "posthog":

            for api in ("https://us.posthog.com", "https://eu.posthog.com"):
                r = _send("GET", api + "/api/users/@me/", key)
                if not _rejected(app_id, r):
                    break
            me = _json(r)
            account = me.get("email") or me.get("first_name") or ""
        elif app_id == "railway":
            r = _send("POST", api + a.graphql, key, {"query": "{ me { name email } }"})
            me = (_json(r).get("data") or {}).get("me") or {}
            account = me.get("email") or me.get("name") or ""
    except httpx.HTTPError:
        raise KeyAppError(f"{a.name} didn't answer just now. Try again in a minute.") from None
    if _rejected(app_id, r):
        raise KeyAppError(f"{a.name} didn't accept that key. Make a new one and paste it again.", rejected=True)
    if r.status_code >= 500:
        raise KeyAppError(f"{a.name} didn't answer just now. Try again in a minute.")
    return {"key": key, "account": str(account)[:120], "api": api}






_SECRET_PATH = re.compile(r"/(?:env|secrets?|tokens?|api-keys?|keys)(?:/|$)", re.I)
_SECRET_FIELD = re.compile(r"\b(?:variables\w*|\w*secrets?|\w*[tT]okens?|\w*[aA]pi[kK]eys?)\b")
_STRINGS = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|#[^\n]*')
_WRITES = re.compile(r"\b(?:mutation|subscription)\b", re.I)


_SECRET_KEY = re.compile(r"secret|token|password|api_?key|private_?key", re.I)


def _redact(data):
    if isinstance(data, dict):
        return {k: "(hidden)" if _SECRET_KEY.search(str(k)) and isinstance(v, str) else _redact(v)
                for k, v in data.items()}
    if isinstance(data, list):
        return [_redact(v) for v in data]
    return data


def _query_only(q: str) -> bool:
    ""

    return bool(q.strip()) and not _WRITES.search(_STRINGS.sub(" ", q))


def _path(a: KeyApp, path: str) -> str:
    p = str(path or "").strip()
    if p.startswith(a.api):
        p = p[len(a.api):]
    if not p.startswith("/") or "://" in p or ".." in p or "\\" in p or "@" in p.split("?")[0]:
        raise KeyAppError(f"Give a path on {a.name}'s API that starts with /, like the examples in the tool.")
    return p


RAIL_LOG_SCOPES = {"project:member", "workspace:member", "workspace:admin"}
RAIL_LOGS_RECONNECT = "Railway needs to be reconnected with log access before I can read deploy logs."
RAIL_LOGS_REFUSED = ("Railway refused this deployment's logs. Its project may not be one shared with Zero "
                     "when Railway was connected.")


def _logs_refused(data) -> bool:
    errors = data.get("errors") if isinstance(data, dict) else None
    return any(isinstance(e, dict) and e.get("message") == "Not Authorized" and (e.get("path") or [None])[0] ==
               "deploymentLogs" for e in errors or [])


def request(app_id: str, stored: dict, method: str, path: str, body=None) -> dict:
    ""
    a = BY_ID[app_id]
    method = str(method or "GET").upper()
    p = _path(a, path)
    if _SECRET_PATH.search(p.split("?")[0]):
        raise KeyAppError(f"That {a.name} read returns secrets, so I don't fetch it.")


    if method == "GET" and not re.match(a.reads or r"(?!)", p.split("?")[0]):
        raise KeyAppError(f"That isn't one of the {a.name} reads I offer. Use a path like the examples in the tool.")
    if method == "POST":
        plain = p.split("?")[0]
        if a.graphql and plain == a.graphql:
            q = str(body.get("query") or "") if isinstance(body, dict) else ""
            if not _query_only(q):
                raise KeyAppError(f"Only GraphQL queries are allowed on {a.name}, nothing that changes it.")
            if _SECRET_FIELD.search(_STRINGS.sub(" ", q)):
                raise KeyAppError(f"That {a.name} query asks for secrets, so I don't send it.")

            body = {"query": q, **({"variables": body["variables"]} if isinstance(body.get("variables"), dict) else {})}
        elif not (a.post_reads and re.match(a.post_reads, plain)):
            raise KeyAppError(f"{a.name} is read only here. Use GET.")
    elif method != "GET":
        raise KeyAppError(f"{a.name} is read only here. Use GET.")
    try:
        url = (stored.get("api") or a.api) + p
        if stored.get("auth") == "oauth":
            from . import oauth
            try:
                r = oauth.request(app_id, method, url, json=body if method == "POST" else None)
            except oauth.OAuthError as e:
                raise KeyAppError(str(e), rejected=e.state in (oauth.MISSING, oauth.NEEDS_SIGN_IN)) from None
        else:
            r = _send(method, url, stored["token"], body if method == "POST" else None)
    except httpx.HTTPError:
        raise KeyAppError(f"{a.name} didn't answer just now.") from None
    err = _json(r).get("error")
    if r.status_code == 401 or (app_id == "vercel" and r.status_code == 403 and isinstance(err, dict)
                                and err.get("invalidToken")):
        raise KeyAppError(f"{a.name} stopped accepting the key.", rejected=True)
    text = r.text
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if app_id == "railway" and _logs_refused(data):


        if stored.get("auth") == "oauth" and not RAIL_LOG_SCOPES & set(str(stored.get("scope") or "").split()):
            raise KeyAppError(RAIL_LOGS_RECONNECT, rejected=True, why="read your deploy logs")
        raise KeyAppError(RAIL_LOGS_REFUSED)
    if data is not None:
        data = _redact(data)
        text = json.dumps(data)
    out = {"status": r.status_code}
    if data is not None and len(text) <= TEXT_MAX:
        out["data"] = data
    else:
        out["text"] = text[:TEXT_MAX]
        if len(text) > TEXT_MAX:
            out["cut"] = f"Only the first {TEXT_MAX} characters. Ask for less (a limit, fewer fields)."
    return out







FRIENDLY = {"posthog": ("projects", "events", "errors", "insights", "query"),
            "railway": ("projects", "deployments", "logs")}
_HOGQL_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
_HOGQL_BAD = re.compile(r"\b(?:insert|alter|drop|delete|update|create|attach|detach|optimize|system|kill|"
                        r"truncate|rename|grant|revoke|outfile|infile)\b", re.I)
_RID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PROJECT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_RAIL_PROJECTS = ("query { projects { edges { node { id name services { edges { node { id name } } } } } } }")
RAIL_OAUTH_PROJECTS = "query { externalWorkspaces { id name projects { id name } } }"
_RAIL_DEPLOYS = ("query ($serviceId: String!) { deployments(input: {serviceId: $serviceId}, first: 5) "
                 "{ edges { node { id status createdAt } } } }")
_RAIL_LOGS = ("query ($deploymentId: String!, $limit: Int) { deploymentLogs(deploymentId: $deploymentId, "
              "limit: $limit) { timestamp message severity } }")
_ERRORS = ("select count() from events where event = '$exception' and timestamp > now() - interval 7 day")


def _hogql(text: str) -> str:
    q = " ".join(str(text or "").split())
    if not q or len(q) > 4000:
        raise KeyAppError("query must be one HogQL select, up to 4000 characters.")
    bare = _HOGQL_STRING.sub(" ", q)
    bare = re.sub(r"/\*.*?\*/", " ", bare, flags=re.S)
    bare = re.sub(r"--.*", " ", bare)
    bare = bare.strip().rstrip(";").strip()
    if ";" in bare or not re.match(r"(?is)^(select|with)\b", bare) or _HOGQL_BAD.search(bare):
        raise KeyAppError("query must be one HogQL select. It can't change anything.")
    return q


def _project(stored: dict, args: dict) -> str | dict:
    asked = str(args.get("project") or "").strip()
    if asked:
        if not _PROJECT.match(asked):
            raise KeyAppError("project must be an id from action projects.")
        return asked
    data = request("posthog", stored, "GET", "/api/projects/")
    rows = (data.get("data") or {}).get("results") if isinstance(data.get("data"), dict) else None
    rows = [r for r in rows or [] if isinstance(r, dict) and r.get("id") is not None]
    if len(rows) == 1:
        return str(rows[0]["id"])
    return {"projects": [{"id": r.get("id"), "name": r.get("name")} for r in rows[:50]],
            "need": "Name the project id. This key can see more than one."}


def _rows(data: dict, key: str = "results") -> list:
    body = data.get("data")
    if isinstance(body, dict) and isinstance(body.get(key), list):
        return body[key]
    return []


def _posthog(stored: dict, action: str, args: dict) -> dict:
    if action == "projects":
        data = request("posthog", stored, "GET", "/api/projects/")
        return {"projects": [{"id": r.get("id"), "name": r.get("name")} for r in _rows(data) if isinstance(r, dict)][:50]}
    pid = _project(stored, args)
    if isinstance(pid, dict):
        return pid
    if action == "events":
        data = request("posthog", stored, "GET", f"/api/projects/{pid}/event_definitions/?limit=40")
        names = [str(r.get("name")) for r in _rows(data) if isinstance(r, dict) and r.get("name")]
        return {"project": pid, "events": names[:40]}
    if action == "insights":
        data = request("posthog", stored, "GET", f"/api/projects/{pid}/insights/?limit=20")
        return {"project": pid, "insights": [{"id": r.get("id"), "name": r.get("name")}
                                             for r in _rows(data) if isinstance(r, dict)][:20]}
    if action == "errors":
        hogql = _ERRORS
    else:
        hogql = _hogql(args.get("query"))
    body = {"query": {"kind": "HogQLQuery", "query": hogql}}
    out = request("posthog", stored, "POST", f"/api/projects/{pid}/query/", body)
    out["project"] = pid
    return out


def _rid(value, label: str) -> str:
    v = str(value or "").strip()
    if not _RID.match(v):
        raise KeyAppError(f"{label} must be an id from action projects or deployments.")
    return v


def _railway(stored: dict, action: str, args: dict) -> dict:
    if action == "projects":
        query, variables = RAIL_OAUTH_PROJECTS if stored.get("auth") == "oauth" else _RAIL_PROJECTS, None
    elif action == "deployments":
        query = _RAIL_DEPLOYS
        variables = {"serviceId": _rid(args.get("service"), "service")}
    else:
        try:
            limit = int(args.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        query = _RAIL_LOGS
        variables = {"deploymentId": _rid(args.get("deployment"), "deployment"), "limit": max(1, min(limit, 100))}
    body = {"query": query, **({"variables": variables} if variables else {})}
    return request("railway", stored, "POST", "/graphql/v2", body)


def friendly(app_id: str, stored: dict, action: str, args: dict) -> dict:
    ""
    args = args if isinstance(args, dict) else {}
    if action not in FRIENDLY.get(app_id, ()):
        name = BY_ID[app_id].name if app_id in BY_ID else "That app"
        raise KeyAppError(f"{name} has no {action} action. Use a path, as the tool describes.")
    if app_id == "posthog":
        return _posthog(stored, action, args)
    return _railway(stored, action, args)
