""






















import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import httpx

from . import config, store, who

log = logging.getLogger("taffy.service_keys")

FOLDER = "service-keys"
SHARED = "_shared"
ID_PREFIX = "key."
CATEGORY = "API Keys"
TIMEOUT_SEC = 30
TEXT_MAX = 30000
ASK_DAYS = 30
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HOST = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_PRIVATE_HOST = re.compile(r"(?:^|\.)(?:localhost|local|internal|lan|home|intranet|ts\.net|onion)$")
AUTHS = ("bearer", "header", "query", "basic", "oauth1")
OAUTH1_VALUES = ("consumer_key", "consumer_secret", "access_token", "access_token_secret")
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
_RESERVED_HEADER = re.compile(r"^(?:authorization|cookie|host|content-length|transfer-encoding|connection|proxy-.*"
                              r"|x-[\w-]*(?:key|token|secret|auth)[\w-]*)$", re.I)


PRESETS = {
    "x": {"name": "X", "auth": "oauth1", "hosts": ["api.x.com"], "url": "https://console.x.com",
          "make": "Open your app and set its permission to Read and Write. Then open Keys and Tokens and copy all four "
                  "values below.",
          "fields": [{"id": "consumer_key", "label": "API Key"}, {"id": "consumer_secret", "label": "API Key Secret"},
                     {"id": "access_token", "label": "Access Token"},
                     {"id": "access_token_secret", "label": "Access Token Secret"}],
          "check_url": "https://api.x.com/2/users/me"},
}
_ALIASES = {"twitter": "x", "x-com": "x", "x-api": "x", "twitter-api": "x"}


class KeyFileBad(RuntimeError):
    ""


class ServiceError(RuntimeError):
    ""

    def __init__(self, message: str, not_sent: bool = True):
        super().__init__(message)
        self.not_sent = not_sent


@store.schema
def _schema(conn: sqlite3.Connection) -> None:

    conn.execute("CREATE TABLE IF NOT EXISTS service_key_asks (bot_id TEXT NOT NULL, service TEXT NOT NULL, "
                 "spec TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (bot_id, service))")




def path_of(bot_id: str, service: str) -> Path:
    if not _NAME.match(bot_id or "") or not _NAME.match(service or ""):
        raise ValueError("not a Zip or service name")
    return config.HUB_DIR / FOLDER / bot_id / f"{service}.json"


def _shared_path(service: str) -> Path:
    if not _NAME.match(service or ""):
        raise ValueError("not a service name")
    return config.HUB_DIR / FOLDER / SHARED / f"{service}.json"


def has(bot_id: str, service: str) -> bool:
    ""
    try:
        return path_of(bot_id, service).is_file() or _shared_path(service).is_file()
    except ValueError:
        return False


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise KeyFileBad("The saved key file can't be read.") from None
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict) or not values:
        raise KeyFileBad("The saved key file has no values in it.")
    if data.get("auth") == "oauth1" and not all(isinstance(values.get(k), str) and values[k].strip()
                                                for k in OAUTH1_VALUES):
        raise KeyFileBad("The saved key file is missing one of its four values.")
    return data


def load(bot_id: str, service: str) -> dict | None:
    ""
    return _read(path_of(bot_id, service)) or _read(_shared_path(service))


def _write(path: Path, data: dict) -> None:
    root = config.HUB_DIR / FOLDER
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path.parent.mkdir(exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    tmp.replace(path)




def _encode(value: str) -> str:
    ""
    return quote(str(value), safe="-._~")


def oauth1_signature(method: str, url: str, params: dict, consumer_secret: str, token_secret: str) -> str:
    ""
    pairs = sorted((_encode(k), _encode(v)) for k, v in params.items())
    text = "&".join(f"{k}={v}" for k, v in pairs)
    base = "&".join((method.upper(), _encode(url), _encode(text)))
    key = f"{_encode(consumer_secret)}&{_encode(token_secret)}"
    return base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()


def oauth1_header(method: str, url: str, values: dict, signed: dict | None = None) -> str:
    ""

    params = {"oauth_consumer_key": values["consumer_key"], "oauth_nonce": secrets.token_hex(16),
              "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
              "oauth_token": values["access_token"], "oauth_version": "1.0"}
    oauth = {**params, "oauth_signature": oauth1_signature(method, url, {**(signed or {}), **params},
                                                           values["consumer_secret"], values["access_token_secret"])}
    return "OAuth " + ", ".join(f'{_encode(k)}="{_encode(v)}"' for k, v in sorted(oauth.items()))


def _values_in_order(stored: dict) -> list[tuple[dict, str]]:
    fields = stored.get("fields") or [{"id": k} for k in stored["values"]]
    return [(f, str(stored["values"].get(f["id"]) or "")) for f in fields]


def apply_auth(stored: dict, method: str, url: str, params: dict, form: dict | None) -> tuple[dict, dict]:
    ""
    auth, values = stored.get("auth"), stored["values"]
    pairs = _values_in_order(stored)
    if auth == "oauth1":
        return {"Authorization": oauth1_header(method, url, values, {**params, **(form or {})})}, {}
    if auth == "bearer":
        return {"Authorization": f"Bearer {pairs[0][1]}"}, {}
    if auth == "basic":
        pair = f"{pairs[0][1]}:{pairs[1][1] if len(pairs) > 1 else ''}"
        return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}, {}
    if auth == "header":
        return {f["name"]: v for f, v in pairs}, {}
    if auth == "query":
        return {}, {f["name"]: v for f, v in pairs}
    raise ServiceError("Not done: that key's way of signing in isn't one I know.")


def scrub(text: str, stored: dict) -> str:
    ""
    for value in (stored.get("values") or {}).values():
        if isinstance(value, str) and value:
            for form in (value, _encode(value), base64.b64encode(value.encode()).decode()):
                text = text.replace(form, "(hidden)")
    return text




def card_id(bot_id: str, service: str) -> str:
    return f"{ID_PREFIX}{bot_id}.{service}"


def is_id(app_id: str) -> bool:
    return str(app_id or "").startswith(ID_PREFIX)


def _parts(app_id: str) -> tuple[str, str]:
    parts = str(app_id)[len(ID_PREFIX):].split(".")
    if len(parts) != 2 or not _NAME.match(parts[0]) or not _NAME.match(parts[1]):
        raise ServiceError("There is no such key.")
    return parts[0], parts[1]


def slug(name: str) -> str:
    made = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:40]
    return _ALIASES.get(made, made)


def _clean_host(value) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if not _HOST.match(host) or _PRIVATE_HOST.search(host):
        raise ServiceError(f"Not asked: {str(value)[:80]!r} isn't a public address for this service's API. Give the "
                           "API's own host, like api.example.com.")
    return host


def _clean_https(value, what: str) -> str:
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ServiceError(f"Not asked: {what} must be a full address starting with https://.")
    return url


def _spec(args: dict) -> dict:
    ""
    name = " ".join(str(args.get("service") or "").split())[:40]
    service = slug(name)
    if not _NAME.match(service):
        raise ServiceError("Not asked: name the service, like SendGrid.")
    if service in PRESETS:
        return {"service": service, **PRESETS[service]}
    url = _clean_https(args.get("url"), "the page where the key is made")
    hosts = args.get("hosts")
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 5:
        raise ServiceError("Not asked: list the API's hosts in hosts, like [\"api.example.com\"].")
    hosts = sorted({_clean_host(h) for h in hosts})
    auth = str(args.get("auth") or "bearer").strip().lower()
    if auth not in AUTHS:
        raise ServiceError(f"Not asked: auth must be one of {', '.join(AUTHS)}.")
    fields = args.get("fields") if isinstance(args.get("fields"), list) else []
    if auth == "oauth1":
        labels = {"consumer_key": "API Key", "consumer_secret": "API Secret", "access_token": "Access Token",
                  "access_token_secret": "Access Token Secret"}
        fields = [{"id": k, "label": labels[k]} for k in OAUTH1_VALUES]
    else:
        fields = _clean_fields(fields, auth)
    check = str(args.get("check_url") or "").strip()
    if check:
        check = _clean_https(check, "check_url")
        if (urlsplit(check).hostname or "").lower() not in hosts:
            raise ServiceError("Not asked: check_url must be on one of the hosts.")
    make = " ".join(str(args.get("make") or "").split())[:240]
    if not make:
        raise ServiceError(f"Not asked: say in make what {who.first()} makes on that page, in one sentence.")
    return {"service": service, "name": name, "auth": auth, "hosts": hosts, "url": url, "make": make, "fields": fields,
            **({"check_url": check} if check else {})}


def _clean_fields(fields: list, auth: str) -> list[dict]:
    out = []
    for i, f in enumerate(fields[:5]):
        f = f if isinstance(f, dict) else {}
        label = " ".join(str(f.get("label") or "").split())[:40] or "API Key"
        name = str(f.get("name") or "").strip()[:64]
        if auth in ("header", "query") and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ServiceError(f"Not asked: each field needs the {auth} name the API reads it from, in name.")
        out.append({"id": f"value{i + 1}", "label": label, **({"name": name} if name else {})})
    if not out:
        if auth in ("header", "query"):
            raise ServiceError(f"Not asked: fields must name the {auth} the API reads the key from.")
        out = [{"id": "value1", "label": "Username"}, {"id": "value2", "label": "Password"}] if auth == "basic" \
            else [{"id": "value1", "label": "API Key"}]
    if auth == "bearer":
        out = out[:1]
    return out


def _bot_name(bot_id: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    return bot.name if bot else bot_id


def _can(spec: dict) -> str:


    return (f"I send this key only to {spec['name']}. I read freely, and I ask you before anything is sent, posted or "
            "deleted.")


def ask(bot_id: str, args: dict, session_id: str | None, origin_task: str | None = None) -> tuple[int | None, str]:
    ""
    from . import accounts, apps
    native = apps.find_id(str(args.get("service") or ""))
    if native and apps.BY_ID[native].native:
        return None, (f"{apps.BY_ID[native].name} connects in Zero. Use ask_to_connect with app \"{native}\" and why, "
                      "with no key.")
    spec = _spec(args)
    service, name = spec["service"], spec["name"]
    if has(bot_id, service):
        tool = "post_to_x to post, and service_api for the rest of its API" if service == "x" else "service_api"
        return None, f"You already have a key for {name}. Use {tool}."
    why = " ".join(str(args.get("why") or "").split())[:160]
    if not why:
        return None, (f"Before {who.first()} gets a card, say why you need {name}: call ask_for_api_key again with why, a "
                      f"few words written to {who.first()} as \"you\".")
    app_id = card_id(bot_id, service)


    if (old := _ask_of(bot_id, service)) and old != spec:
        return None, (f"{who.first()} already has a card for your {name} key, and it keeps the setup you gave first. If "
                      f"that setup is wrong, tell {who.first()} in chat to close the card, then ask again.")
    with store.db() as c:
        c.execute("INSERT OR REPLACE INTO service_key_asks (bot_id, service, spec, at) VALUES (?,?,?,?)",
                  (bot_id, service, json.dumps(spec), time.time()))
    wait = (f"Stop working on this now. {who.quiet_card()} You'll get a note here when it's added, and you'll carry on "
            "with service_api then.")
    for row, card in accounts._open_cards(bot_id):
        if card.get("app") == app_id:
            store.touch_feed(row["id"])
            return None, f"{who.possessive()} card for your {name} key is now at the bottom of your chat. {wait}"
    fid = store.add_feed(accounts.KIND, bot_id, origin_task, f"Connect {name}", json.dumps(
        {"service": app_id, "app": app_id, "via": "zero", "name": name, "group": "", "why": why, "state": "open",
         "provider": name, "can": _can(spec), "session_id": session_id, "key": True, "connection_id": None}),
             written_by="zip", written_for="person")
    return fid, f"{who.first()} now has a card in your chat to add the {name} key, and a notification. {wait}"


def reword_open_cards() -> int:
    ""
    from . import accounts
    changed = 0
    for row in store.db().execute("SELECT id, title, body FROM feed WHERE kind=?", (accounts.KIND,)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if not isinstance(card, dict) or not card.get("key") or card.get("state") != "open" or not card.get("name"):
            continue
        can = _can({"name": card["name"]})
        if card.get("can") != can:
            card["can"] = can
            store.update_feed(row["id"], row["title"], json.dumps(card))
            changed += 1
    return changed


def forget_ask(app_id: str) -> None:
    ""
    try:
        bot_id, service = _parts(app_id)
    except ServiceError:
        return
    with store.db() as c:
        c.execute("DELETE FROM service_key_asks WHERE bot_id=? AND service=?", (bot_id, service))


def _ask_of(bot_id: str, service: str) -> dict | None:
    row = store.db().execute("SELECT spec FROM service_key_asks WHERE bot_id=? AND service=?",
                             (bot_id, service)).fetchone()
    return json.loads(row["spec"]) if row else None




def _row(app_id: str, name: str, bot_id: str, state: str, can: str, blurb: str, account: str = "",
         shared: bool | None = None) -> dict:
    connected = state == "connected"
    return {"id": app_id, "name": name, "category": CATEGORY, "symbol": "key.fill", "native": True, "can": can,
            "blurb": blurb, "popular": False, "account": account, "via": "zero" if connected else "",
            "multiple": False, "connection_count": 0, "state": state, "detail": "" if connected else f"{name} needs a key.",
            "action": "disconnect" if connected else "connect", **({"shared": shared} if connected else {}),
            "owner_name": _bot_name(bot_id)}


def rows() -> list[dict]:
    ""
    out, seen = [], set()
    root = config.HUB_DIR / FOLDER
    for path in sorted(root.glob("*/*.json")) if root.is_dir() else []:
        try:
            data = _read(path)
        except KeyFileBad:
            data = {"name": path.stem, "hosts": []}
        if not data:
            continue
        shared = path.parent.name == SHARED
        bot_id = str(data.get("owner") or "") if shared else path.parent.name
        if not _NAME.match(bot_id):
            continue
        app_id = card_id(bot_id, path.stem)
        seen.add(app_id)
        who_uses = "Every Zip" if shared else f"{_bot_name(bot_id)} only"
        hosts = " and ".join(data.get("hosts") or []) or "its service"
        out.append(_row(app_id, str(data.get("name") or path.stem), bot_id, "connected",
                        f"This key goes only to {hosts}.", who_uses, str(data.get("account") or ""), shared))
    for r in store.db().execute("SELECT bot_id, service, spec FROM service_key_asks WHERE at>? ORDER BY at",
                                (time.time() - ASK_DAYS * 86400,)).fetchall():
        app_id = card_id(r["bot_id"], r["service"])
        if app_id not in seen:
            spec = json.loads(r["spec"])
            out.append(_row(app_id, spec["name"], r["bot_id"], "not_connected", _can(spec),
                            f"For {_bot_name(r['bot_id'])}"))
    return out


def connected(app_id: str) -> bool:
    try:
        return has(*_parts(app_id))
    except ServiceError:
        return False


def start(app_id: str) -> dict:
    ""
    spec = _ask_of(*_parts(app_id))
    if not spec:
        raise ServiceError("That request for a key is gone. Ask the Zip again.")
    return {"mode": "key", "url": spec["url"], "make": spec["make"],
            "fields": [{"id": f["id"], "label": f["label"]} for f in spec["fields"]]}


def _check(stored: dict) -> str:
    ""

    url = stored.get("check_url")
    if not url:
        return ""
    name = stored["name"]
    try:
        r = _request(stored, "GET", url, {}, {}, None, None)
    except (httpx.HTTPError, ServiceError):
        log.info("service key for %s: the check did not get an answer", name)
        return ""
    if r.status_code in (401, 403):
        raise ServiceError(f"{name} didn't accept that key. Copy it again and paste it here.")
    try:
        data = (r.json().get("data") or {}) if 200 <= r.status_code < 300 else {}
    except (ValueError, AttributeError):
        return ""
    handle = data.get("username") if isinstance(data, dict) else None
    return f"@{handle}"[:80] if isinstance(handle, str) and handle else ""


def save(app_id: str, body: dict) -> dict:
    ""
    bot_id, service = _parts(app_id)
    spec = _ask_of(bot_id, service)
    if not spec:
        return {"ok": False, "detail": "That request for a key is gone. Ask the Zip again."}
    fields, given = spec["fields"], body.get("values") if isinstance(body.get("values"), dict) else None
    if given is None:
        if len(fields) > 1:
            return {"ok": False, "detail": f"{spec['name']} needs {len(fields)} values. Update Zero to add them."}
        given = {fields[0]["id"]: body.get("code")}
    values = {f["id"]: "".join(str(given.get(f["id"]) or "").split()) for f in fields}

    username = fields[0]["id"] if spec.get("auth") == "basic" and len(fields) > 1 else None
    missing = next((f["label"] for f in fields if len(values[f["id"]]) < (1 if f["id"] == username else 8)), None)
    if missing:
        return {"ok": False, "detail": f"That doesn't look like the {missing}. Copy the whole value and paste it again."}
    stored = {**spec, "values": values, "added_at": time.time()}
    try:
        account = _check(stored)
    except ServiceError as e:
        return {"ok": False, "detail": str(e)}
    _write(path_of(bot_id, service), {**stored, **({"account": account} if account else {})})
    forget_ask(app_id)
    log.info("service key added for %s: %s", bot_id, service)
    row = next((r for r in rows() if r["id"] == app_id), None)
    return {"ok": True, "detail": f"{spec['name']} is added for {_bot_name(bot_id)}.", "app": row}


def remove(app_id: str) -> dict:
    ""
    bot_id, service = _parts(app_id)
    name = service
    for path in (path_of(bot_id, service), _shared_path(service)):
        try:
            data = _read(path) or {}
        except KeyFileBad:
            data = {}
        if path.parent.name == SHARED and data.get("owner") != bot_id:
            continue
        name = str(data.get("name") or name)
        path.unlink(missing_ok=True)
    forget_ask(app_id)
    return {"ok": True, "detail": f"Removed. {_bot_name(bot_id)} no longer uses this key. You can delete it on {name} too."}


def share(app_id: str, shared: bool) -> dict:
    ""
    bot_id, service = _parts(app_id)
    own, common = path_of(bot_id, service), _shared_path(service)
    source, target = (own, common) if shared else (common, own)
    data = _read(source)
    if not data:
        current = _read(target)
        if current and (not shared or current.get("owner") == bot_id):
            return {"ok": True, "detail": "", "app": next((r for r in rows() if r["id"] == app_id), None)}
        return {"ok": False, "detail": "That key is gone."}
    if shared and common.is_file():
        return {"ok": False, "detail": f"Every Zip already shares a {data.get('name') or service} key. Remove that one first."}
    if not shared and data.get("owner") != bot_id:
        return {"ok": False, "detail": "That key belongs to another Zip."}
    _write(target, {**data, "owner": bot_id} if shared else {k: v for k, v in data.items() if k != "owner"})
    source.unlink()
    return {"ok": True, "detail": "", "app": next((r for r in rows() if r["id"] == app_id), None)}




def _send(method: str, url: str, headers: dict, params: dict, body, form) -> httpx.Response:
    ""
    return httpx.request(method, url, headers=headers, params=params or None, json=body, data=form,
                         timeout=TIMEOUT_SEC, follow_redirects=False)


def _public(host: str) -> bool:
    from . import siteicon
    return siteicon._vetted(host) is not None


def _request(stored: dict, method: str, url: str, params: dict, headers: dict, body, form) -> httpx.Response:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in (stored.get("hosts") or []) or parts.username or parts.password \
            or parts.port not in (None, 443):
        hosts = " and ".join(stored.get("hosts") or [])
        raise ServiceError(f"Not done: this key goes only to https addresses on {hosts}.")
    if not _public(host):
        raise ServiceError(f"Not done: {host} isn't a public address, so the key didn't go there.")
    bare = urlunsplit(("https", parts.netloc, parts.path or "/", "", ""))
    query = {**dict(parse_qsl(parts.query, keep_blank_values=True)), **params}
    auth_headers, auth_query = apply_auth(stored, method, bare, query, form)
    return _send(method, bare, {**headers, **auth_headers}, {**query, **auth_query}, body, form)


def _flat(value, what: str) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ServiceError(f"Not done: {what} must be an object of names and values.")
    return {str(k): ("" if v is None else str(v)) for k, v in value.items()}


def call(bot_id: str, args: dict) -> dict:
    ""
    from . import consent, keys
    service = slug(args.get("service"))
    method = str(args.get("method") or "GET").upper()
    if method not in METHODS:
        raise ServiceError(f"Not done: method must be one of {', '.join(METHODS)}.")
    try:
        stored = load(bot_id, service) if _NAME.match(service) else None
    except KeyFileBad as e:
        raise ServiceError(f"Not done: {e}") from None
    if stored is None:
        name = str(args.get("service") or "that service")[:40]
        return {"connected": False, "message": f"You have no key for {name}. Ask {who.first()} for one with "
                                               "ask_for_api_key. Never ask for it in the chat."}
    headers = _flat(args.get("headers"), "headers")
    bad = next((h for h in headers if _RESERVED_HEADER.match(h)), None)
    if bad:
        raise ServiceError(f"Not done: the hub adds the key itself, so leave out the {bad} header.")
    params, form, body = _flat(args.get("query"), "query"), args.get("form"), args.get("body")
    form = _flat(form, "form") or None
    reads = method in ("GET", "HEAD")
    if not reads and consent.test_mode():
        return {"result": f"Test mode: {method} to {stored.get('name') or service} is ready. Nothing was sent."}
    try:
        r = _request(stored, method, str(args.get("url") or ""), params, headers, None if reads else body,
                     None if reads else form)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise ServiceError(f"Not done: {stored.get('name') or service} could not be reached. Try again soon.") from None
    except httpx.HTTPError:
        name = stored.get("name") or service
        if reads:
            raise ServiceError(f"Not done: {name} didn't answer. Try again soon.") from None
        raise ServiceError(f"{name} didn't answer, so this may or may not have gone through. Don't send it again. "
                           f"Check on {name}, and tell {who.first()} what you find.", not_sent=False) from None
    return _answer(r, stored, keys)


def _answer(r: httpx.Response, stored: dict, keys) -> dict:
    name = stored.get("name") or "That service"
    out = {"status": r.status_code}
    if r.status_code == 401:
        out["note"] = f"{name} refused the key. {who.first()} may need to make a new one."
    elif r.status_code == 402:
        out["note"] = f"{name} says this account needs payment or credits."
    elif 300 <= r.status_code < 400:
        out["note"] = f"{name} tried to send this elsewhere. I didn't follow it, so the key stayed put."
        return out
    text = scrub(r.text or "", stored)
    try:
        data = keys._redact(json.loads(text))
    except ValueError:
        data = None
    if data is not None and len(json.dumps(data)) <= TEXT_MAX:
        out["data"] = data
    else:
        out["text"] = (json.dumps(data) if data is not None else text)[:TEXT_MAX]
        if len(out["text"]) >= TEXT_MAX:
            out["cut"] = f"Only the first {TEXT_MAX} characters. Ask for less."
    return out
