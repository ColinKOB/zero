""



























import hashlib
import logging
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from . import home

log = logging.getLogger("taffy.plaid")

TIMEOUT_SEC = 30
HOSTS = {"sandbox": "https://sandbox.plaid.com", "development": "https://development.plaid.com",
         "production": "https://production.plaid.com"}
ACTIONS = ("accounts", "transactions", "subscriptions")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOT_SET_UP = "Bank data isn't set up on this home yet."
NOT_CONNECTED = "The bank isn't connected yet. Connect Your Bank is in Connections, in Zero's settings."
AGAIN = "The bank needs to be connected again."
DEAD_TOKEN = ("INVALID_ACCESS_TOKEN", "ITEM_LOGIN_REQUIRED", "ITEM_NOT_FOUND", "ACCESS_NOT_GRANTED")

APP_ID = "plaid"
COMPLETE_SCHEME = "zero-bank"
LINK_TTL_SEC = 31 * 60
POLL_SEC = 5
DASHBOARD = "https://dashboard.plaid.com/"
SETUP = "Your bank opens here once Zero has your Plaid keys."
SETUP_STEPS = ["Tap Get Plaid Keys. Copy your client ID and secret.", "Save them in your home's Plaid settings file.",
               "Tap Check Again."]
DIDNT_CONNECT = "Your bank didn't connect. Tap Connect Your Bank to try again."


class PlaidError(RuntimeError):
    ""

    def __init__(self, message: str, connected: bool = True):
        super().__init__(message)
        self.connected = connected


def _file_env(file: str) -> dict:
    out = {}
    try:
        text = Path(file).expanduser().read_text()
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _pick(name: str, file_vals: dict) -> str:
    for key in (f"ZERO_{name}", name):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return str(file_vals.get(name) or "").strip()


def config() -> dict | None:
    ""
    spec = home.get("integrations.plaid")
    file_vals = _file_env(str(spec.get("env_file"))) if isinstance(spec, dict) and spec.get("env_file") else {}
    client, secret = _pick("PLAID_CLIENT_ID", file_vals), _pick("PLAID_SECRET", file_vals)
    if not client or not secret:
        return None
    env = _pick("PLAID_ENV", file_vals).lower() or "sandbox"
    if env not in HOSTS:
        env = "sandbox"

    relay = str(home.get("apps.relay.url") or "").strip().rstrip("/")
    redirect = _pick("PLAID_REDIRECT_URI", file_vals) or (f"{relay}/callback/plaid" if relay.startswith("https://") else "")
    return {"client_id": client, "secret": secret, "env": env, "redirect_uri": redirect}


def _send(env: str, path: str, body: dict) -> httpx.Response:
    return httpx.post(HOSTS[env] + path, json=body, timeout=TIMEOUT_SEC)


def _secrets(cfg: dict) -> list[str]:
    tokens = [i.get("token") for i in _items().values()]
    return [s for s in (cfg.get("secret"), cfg.get("client_id"), cfg.get("token"), *tokens) if s]


def _hide(cfg: dict, text: str) -> str:
    for secret in _secrets(cfg):
        text = text.replace(secret, "")
    return " ".join(text.split())[:240]


def _scrub(cfg: dict, data):
    ""
    secrets_here = _secrets(cfg)

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            for secret in secrets_here:
                value = value.replace(secret, "")
        return value
    return clean(data)


def _ask(cfg: dict, path: str, extra: dict) -> dict:
    body = {"client_id": cfg["client_id"], "secret": cfg["secret"], **extra}
    try:
        r = _send(cfg["env"], path, body)
    except httpx.HTTPError:
        raise PlaidError("The bank didn't answer just now.") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    code = str(data.get("error_code") or "")
    if r.status_code >= 400 and code in DEAD_TOKEN:
        raise PlaidError(AGAIN, connected=False)
    if code == "PRODUCT_NOT_READY":
        raise PlaidError("The bank's transactions are still loading. Try again in a few minutes.")
    if r.status_code >= 400:
        detail = _hide(cfg, str(data.get("error_message") or ""))
        raise PlaidError("The bank refused that request." + (f" {detail}" if detail else ""))
    return data


def _post(cfg: dict, path: str, extra: dict) -> dict:
    return _ask(cfg, path, {"access_token": cfg["token"], **extra})




def _items() -> dict:
    ""
    from . import apps
    items = apps._document(APP_ID).get("items")
    return items if isinstance(items, dict) else {}


def _write(items: dict) -> None:
    from . import apps
    if items:
        apps._save(APP_ID, {"items": items})
    else:
        apps._forget(APP_ID)


def _mark(item_id: str, state: str) -> None:
    from . import apps
    with apps._lock:
        items = _items()
        if item_id in items and items[item_id].get("state") != state:
            items[item_id]["state"] = state
            _write(items)


def banks() -> list[dict]:
    ""
    return [{"id": item_id, "name": item.get("name") or "Your Bank", "state": item.get("state") or "connected"}
            for item_id, item in _items().items()]


def _remove_item(cfg: dict | None, item: dict) -> bool:
    ""


    if item.get("shared"):
        return True
    if not cfg or not item.get("token"):
        return False
    try:
        _ask(cfg, "/item/remove", {"access_token": item["token"]})
        return True
    except PlaidError:
        log.warning("plaid: a bank's connection could not be removed at Plaid")
        return False


def disconnect(item_id: str | None = None) -> dict:
    ""

    from . import apps
    if item_id is None:


        with _link_lock:
            _links.clear()
    with apps._lock:
        items = _items()
        gone = [items.pop(item_id)] if item_id in items else [] if item_id else list(items.values())
        if item_id is None:
            items = {}
        _write(items)
    if item_id and not gone:
        return {"ok": False, "detail": "That bank is no longer connected."}
    cfg = config()
    ended = [_remove_item(cfg, i) for i in gone if i.get("token")]
    what = gone[0].get("name") or "that bank" if item_id else "your banks"
    return {"ok": True, "detail": f"Disconnected. Your Zips no longer read {what}." + (
        "" if all(ended) else " Plaid didn't confirm, so remove Zero at my.plaid.com too.")}


def clear_all() -> int:
    ""

    count = len(_items())
    disconnect()
    return count




_link_lock = threading.Lock()
_links: dict[str, dict] = {}


def _sweep(now: float) -> None:
    for state, entry in list(_links.items()):
        if entry["exp"] < now:
            _links.pop(state, None)


def link_start() -> dict:
    ""
    cfg = config()
    if not cfg or not cfg["redirect_uri"]:
        return {"mode": "setup", "url": DASHBOARD, "make": SETUP, "steps": SETUP_STEPS}
    who = hashlib.sha256(str(home.get("home_id") or "home").encode()).hexdigest()[:32]
    try:
        data = _ask(cfg, "/link/token/create", {
            "client_name": "Zero", "language": "en", "country_codes": ["US"], "user": {"client_user_id": who},


            "products": ["transactions"], "transactions": {"days_requested": 365},

            "additional_consented_products": ["liabilities"],
            "redirect_uri": cfg["redirect_uri"],
            "hosted_link": {"completion_redirect_uri": f"{COMPLETE_SCHEME}://connected", "is_mobile_app": True}})
    except PlaidError as e:

        if "redirect uri" in str(e).lower():
            raise PlaidError(f"Your bank can't open yet. Add {cfg['redirect_uri']} to Allowed Redirect URIs in your Plaid "
                             "dashboard, then try again.") from None
        raise
    url, link_token = str(data.get("hosted_link_url") or ""), str(data.get("link_token") or "")
    if not url or not link_token:
        raise PlaidError("The bank's sign-in didn't open. Try again in a minute.")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _link_lock:
        _sweep(now)
        _links[state] = {"link_token": link_token, "exp": now + LINK_TTL_SEC, "seen": set(), "result": None}
    return {"mode": "bank", "url": url, "callback_scheme": COMPLETE_SCHEME, "state": state, "interval": POLL_SEC}


def _same_login(old: dict, institution_id: str, accounts: list[dict]) -> bool:
    ""
    if not institution_id or old.get("institution_id") != institution_id:
        return False
    return any(a in (old.get("accounts") or []) for a in accounts)


def _keep(cfg: dict, add: dict) -> str:
    ""
    from . import apps
    got = _ask(cfg, "/item/public_token/exchange", {"public_token": add["public_token"]})
    token, item_id = str(got.get("access_token") or ""), str(got.get("item_id") or "")
    if not token or not item_id:
        raise PlaidError(DIDNT_CONNECT)
    place = add.get("institution") if isinstance(add.get("institution"), dict) else {}
    accounts = [{"name": str(a.get("name") or ""), "mask": str(a.get("mask") or "")}
                for a in add.get("accounts") or [] if isinstance(a, dict)]
    name = " ".join(str(place.get("name") or "Your Bank").split())[:80]
    institution_id = str(place.get("institution_id") or "")
    with apps._lock:
        items = _items()
        replaced = [i for i, old in items.items() if i != item_id and _same_login(old, institution_id, accounts)]
        old_items = [items.pop(i) for i in replaced]
        items[item_id] = {"token": token, "name": name, "institution_id": institution_id, "accounts": accounts,
                          "state": "connected", "connected_at": time.time()}
        _write(items)
    for old in old_items:
        _remove_item(cfg, old)
    log.info("plaid: a bank was connected")
    return name


def _ended(names: list[str], problem: str = "") -> dict:
    if names:
        return {"ok": True, "pending": False, "banks": names,
                "detail": f"Connected {' and '.join(names)}. Your Zips can read your balances now."}
    return {"ok": False, "pending": False, "detail": problem or DIDNT_CONNECT}


def link_finish(state: str, closed: bool = False) -> dict:
    ""

    now = time.time()
    with _link_lock:
        entry = _links.get(str(state or ""))
        if not entry:
            return _ended([], "This connection expired. Tap Connect Your Bank to try again.")
        if entry["result"]:
            return entry["result"]
        cfg = config()
        if not cfg:
            return _ended([])
        last = closed or entry["exp"] < now
        try:
            sessions = _ask(cfg, "/link/token/get", {"link_token": entry["link_token"]}).get("link_sessions") or []
        except PlaidError as e:
            if last:
                entry["result"] = _ended([], str(e))
                return entry["result"]
            return {"ok": False, "pending": True, "interval": POLL_SEC}
        adds = [a for s in sessions if isinstance(s, dict) and isinstance(s.get("results"), dict)
                for a in s["results"].get("item_add_results") or [] if isinstance(a, dict) and a.get("public_token")]
        names, problem = entry.setdefault("names", []), ""
        for add in adds:
            if add["public_token"] in entry["seen"]:
                continue
            try:
                names.append(_keep(cfg, add))
                entry["seen"].add(add["public_token"])
            except PlaidError as e:
                problem = str(e)
        if problem and not last:
            return {"ok": False, "pending": True, "interval": POLL_SEC}
        finished = any(isinstance(s, dict) and s.get("finished_at") for s in sessions)
        if adds or finished or last:
            entry["result"] = _ended(names, problem)
            return entry["result"]
        return {"ok": False, "pending": True, "interval": POLL_SEC}




def _day(text: str, default: date) -> str:
    value = str(text or "").strip()
    if not value:
        return default.isoformat()
    if not _DATE.match(value):
        raise PlaidError("Dates look like 2026-09-01.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise PlaidError("Dates look like 2026-09-01.") from None
    return value


def _money(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None










PENDING_DAYS = 14


def _bank_time(value) -> dict:
    ""
    try:
        when = datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(home.zone())
    except ValueError:
        return {}
    clock = when.strftime("%I:%M").lstrip("0") + (" a.m." if when.hour < 12 else " p.m.")
    return {"bank_updated_at": when.isoformat(timespec="seconds"),
            "bank_updated_text": f"{clock} {home.tz_word()}, {when.strftime('%b')} {when.day}"}


def _pending(cfg: dict) -> dict[str, list[dict]] | None:
    ""
    start = (date.today() - timedelta(days=PENDING_DAYS)).isoformat()
    try:
        rows = transactions(cfg, start, date.today().isoformat(), 500, 0)["transactions"]
    except PlaidError:
        return None
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r["pending"]:
            out.setdefault(r["account_id"], []).append(
                {"date": r["date"], "name": r["name"], "amount": r["amount"], "direction": r["direction"]})
    return out


def _accounts(data: dict, pending: dict[str, list[dict]] | None = None) -> list[dict]:
    out = []
    for a in data.get("accounts") or []:
        if not isinstance(a, dict):
            continue
        bal = a.get("balances") if isinstance(a.get("balances"), dict) else {}
        kind = str(a.get("type") or "")
        account_id = str(a.get("account_id") or "")
        current, available, limit = _money(bal.get("current")), _money(bal.get("available")), _money(bal.get("limit"))
        row = {"account_id": account_id,
               "name": " ".join(str(a.get("name") or a.get("official_name") or "Account").split())[:80],
               "kind": "card" if kind == "credit" else "cash" if kind == "depository" else kind or "other",
               "type": str(a.get("subtype") or kind),
               "mask": str(a.get("mask") or ""),
               "currency": str(bal.get("iso_currency_code") or "USD")}
        if kind == "credit":
            row.update(owed=current, available_credit=available, credit_limit=limit)
        else:
            row.update(balance=current, available=available)
            if kind == "depository" and limit is not None:
                row["overdraft_limit"] = limit
        row.update(_bank_time(bal.get("last_updated_datetime")))
        if pending is not None:
            mine = pending.get(account_id) or []
            row["pending"] = mine
            row["pending_out"] = round(sum(p["amount"] for p in mine if p["direction"] == "out"), 2)
        out.append(row)
    return out







def _balances(cfg: dict, fresh: bool = False) -> list[dict]:
    data = _post(cfg, "/accounts/balance/get" if fresh else "/accounts/get", {})
    pending = _pending(cfg)
    rows = _accounts(data, pending)
    if pending is None:
        for row in rows:
            row["pending_unknown"] = True
    return rows


def merchant(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(name or "").lower()))


def _tx(row: dict) -> dict:
    raw = _money(row.get("amount")) or 0.0
    name = " ".join(str(row.get("merchant_name") or row.get("name") or "").split())[:120]
    cat = row.get("personal_finance_category") if isinstance(row.get("personal_finance_category"), dict) else {}
    return {"date": str(row.get("date") or ""), "name": name, "merchant": merchant(name),
            "amount": abs(raw), "direction": "in" if raw < 0 else "out", "pending": bool(row.get("pending")),
            "account_id": str(row.get("account_id") or ""),
            "category": str(cat.get("primary") or "")}


def transactions(cfg: dict, start: str, end: str, count: int = 100, offset: int = 0) -> dict:
    count = max(1, min(int(count), 500))
    offset = max(0, min(int(offset), 2000))
    data = _post(cfg, "/transactions/get", {"start_date": start, "end_date": end,
                                            "options": {"count": count, "offset": offset}})
    rows = [_tx(r) for r in data.get("transactions") or [] if isinstance(r, dict)]
    total = int(data.get("total_transactions") or len(rows))
    return {"transactions": rows, "total": total, "more": offset + len(rows) < total}


def recurring(rows: list[dict]) -> list[dict]:
    ""


    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("direction") != "out" or row.get("pending") or not row.get("merchant"):
            continue
        groups.setdefault(row["merchant"], []).append(row)
    found = []
    for key, items in groups.items():
        items = sorted(items, key=lambda r: r.get("date") or "")
        if len(items) < 2:
            continue
        days = []
        for a, b in zip(items, items[1:]):
            try:
                days.append((date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days)
            except ValueError:
                continue
        if not days:
            continue
        gap = sorted(days)[len(days) // 2]
        cadence = ("weekly" if 6 <= gap <= 8 else "monthly" if 25 <= gap <= 35 else
                   "yearly" if 350 <= gap <= 380 else "")
        if not cadence:
            continue
        amounts = sorted(r["amount"] for r in items)
        mid = amounts[len(amounts) // 2]
        if mid <= 0 or any(abs(r["amount"] - mid) > max(1.0, mid * 0.2) for r in items):
            continue
        last = items[-1]
        found.append({"merchant": key, "name": last["name"], "cadence": cadence, "amount": mid,
                      "charges": len(items), "last": last["date"]})
    return sorted(found, key=lambda r: r["name"].lower())


def _read_time() -> dict:
    now = datetime.now(home.zone())
    clock = now.strftime("%I:%M").lstrip("0") + (" a.m." if now.hour < 12 else " p.m.")
    return {"read_at": now.isoformat(timespec="seconds"),
            "read_at_text": f"{clock} {home.tz_word()}, {now.strftime('%b')} {now.day}"}


def _across(cfg: dict, read) -> tuple[list[tuple[dict, dict]], dict]:
    ""

    items = _items()
    if not items:
        raise PlaidError(NOT_CONNECTED, connected=False)

    def one(pair):
        item_id, item = pair
        try:
            return item_id, item, read({**cfg, "token": item.get("token") or ""}), None
        except PlaidError as e:
            return item_id, item, None, e

    with ThreadPoolExecutor(max_workers=min(len(items), 8)) as pool:
        results = list(pool.map(one, items.items()))
    answers, again, other = [], [], []
    for item_id, item, answer, error in results:
        name = item.get("name") or "Your Bank"
        if error is None:
            answers.append(({**item, "id": item_id}, answer))
            _mark(item_id, "connected")
        elif error.connected:
            other.append((name, str(error)))
        else:
            again.append(name)
            _mark(item_id, "needs_sign_in")
    if not answers:
        if again and not other:
            raise PlaidError(f"{' and '.join(again)} needs to be connected again.", connected=False)
        raise PlaidError(other[0][1] if other else AGAIN)
    notes = [f"{name} needs to be connected again, so it is missing here." for name in again]
    notes += [f"{name} could not be read just now, so it is missing here. {why}" for name, why in other]
    extra = {**_read_time(), **({"note": " ".join(notes)} if notes else {}), **({"needs_connecting": again} if again else {})}
    return answers, extra


def _join_transactions(answers: list[tuple[dict, dict]], limit: int) -> dict:
    rows = [{**r, "bank": item.get("name") or ""} for item, a in answers for r in a["transactions"]]
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return {"transactions": rows[:limit], "total": sum(a["total"] for _, a in answers),
            "more": len(rows) > limit or any(a["more"] for _, a in answers)}


def call(action: str, args: dict) -> dict:
    ""
    args = args if isinstance(args, dict) else {}
    if action not in ACTIONS:
        raise PlaidError("action must be accounts, transactions or subscriptions.")
    cfg = config()
    if not cfg:
        raise PlaidError(NOT_SET_UP, connected=False)
    if action == "accounts":
        fresh = args.get("fresh") is True
        answers, extra = _across(cfg, lambda c: _balances(c, fresh))
        rows = [{**row, "bank": item.get("name") or ""} for item, accounts in answers for row in accounts]
        how = ("Asked each bank just now." if fresh else
               "These are the balances each bank last reported, usually within the past day. Pass fresh true only "
               "when the exact amount right now matters; that read costs a small fee for each bank.")
        return _scrub(cfg, {"accounts": rows, "balances_from": how, **extra})
    today = date.today()
    start = _day(args.get("start") or args.get("start_date"), today - timedelta(days=30 if action == "transactions" else 180))
    end = _day(args.get("end") or args.get("end_date"), today)
    if end < start:
        raise PlaidError("end must be on or after start.")
    if (date.fromisoformat(end) - date.fromisoformat(start)).days > 366:
        raise PlaidError("Ask for a year or less at a time.")
    if action == "transactions":
        try:
            count = min(int(args.get("limit") or 50), 100)
        except (TypeError, ValueError):
            count = 50
        answers, extra = _across(cfg, lambda c: transactions(c, start, end, count, 0))
        return _scrub(cfg, {**_join_transactions(answers, max(1, count)), **extra})

    answers, extra = _across(cfg, lambda c: transactions(c, start, end, 500, 0))
    return _scrub(cfg, {"subscriptions": recurring([r for _, a in answers for r in a["transactions"]]),
                        "note": " ".join(filter(None, ["These are charges that repeat. Bank data does not show which "
                                                       "ones are still used.", extra.pop("note", "")])),
                        "from": start, "until": end, **extra})
