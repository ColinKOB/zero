""
























import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger("taffy.billing")

FREE = "free"
PAID_PLANS = ("lite", "cloud", "plus")
PLAN_NAMES = {"lite": "Zero Cloud Lite", "cloud": "Zero Cloud", "plus": "Zero Cloud Plus"}

LOOKUP_KEYS = {"zero_cloud_lite_monthly": "lite", "zero_cloud_monthly": "cloud", "zero_cloud_plus_monthly": "plus"}
TOP_UP_MINUTES = 30
TOLERANCE_SEC = 300
ENDED = {"canceled", "unpaid", "incomplete_expired"}


class Accounts(Protocol):
    def get_plan(self, account_id: str) -> str: ...
    def set_plan(self, account_id: str, plan: str) -> None: ...


class BadSignature(Exception):
    pass




def sign(payload: bytes, secret: str, now: float | None = None) -> str:
    t = int(time.time() if now is None else now)
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(payload: bytes, header: str | None, secrets: list[str] | str, now: float | None = None,
           tolerance: int = TOLERANCE_SEC) -> None:
    ""
    if isinstance(secrets, str):
        secrets = [secrets]
    secrets = [s for s in secrets if s]
    if not secrets:
        raise BadSignature("no secret configured")
    if not header:
        raise BadSignature("no signature")
    t, sigs = None, []
    for part in header.split(","):
        k, _, v = part.strip().partition("=")
        if k == "t":
            t = v
        elif k == "v1":
            sigs.append(v)
    if not t or not t.isdigit() or not sigs:
        raise BadSignature("malformed signature")
    now = time.time() if now is None else now
    if abs(now - int(t)) > tolerance:
        raise BadSignature("signature too old")
    signed = f"{t}.".encode() + payload
    for s in secrets:
        want = hmac.new(s.encode(), signed, hashlib.sha256).hexdigest()
        if any(hmac.compare_digest(want, sig) for sig in sigs):
            return
    raise BadSignature("signature does not match")




def checkout_url(link: str, account_id: str, email: str | None = None) -> str:
    ""
    q = {"client_reference_id": account_id}
    if email:
        q["prefilled_email"] = email
    return link + ("&" if "?" in link else "?") + urllib.parse.urlencode(q)




class Billing:
    def __init__(self, accounts: Accounts, db_path: str | Path,
                 on_top_up: Callable[[str, int], None] | None = None, link_plans: dict[str, str] | None = None):
        ""

        self.accounts = accounts
        self.link_plans = link_plans or {}
        self.on_top_up = on_top_up
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS customers (customer TEXT PRIMARY KEY, account TEXT NOT NULL,
                subscription TEXT, livemode INTEGER, updated REAL);
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, type TEXT, at REAL);
        """)

    def close(self) -> None:
        self._db.close()

    def account_for(self, customer: str | None) -> str | None:
        if not customer:
            return None
        row = self._db.execute("SELECT account FROM customers WHERE customer=?", (customer,)).fetchone()
        return row[0] if row else None

    def _remember(self, customer: str, account: str, subscription: str | None, livemode: bool) -> None:
        self._db.execute("INSERT INTO customers VALUES (?,?,?,?,?) ON CONFLICT(customer) DO UPDATE SET "
                         "account=excluded.account, subscription=coalesce(excluded.subscription, subscription), "
                         "livemode=excluded.livemode, updated=excluded.updated",
                         (customer, account, subscription, int(livemode), time.time()))

    def _current_subscription(self, customer: str) -> str | None:
        row = self._db.execute("SELECT subscription FROM customers WHERE customer=?", (customer,)).fetchone()
        return row[0] if row else None

    def handle(self, event: dict) -> str:
        ""
        eid, kind = event.get("id"), event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        with self._lock:
            if eid and self._db.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone():
                return "duplicate"
            result = self._apply(kind, obj, bool(event.get("livemode")))
            if eid:
                self._db.execute("INSERT INTO events VALUES (?,?,?)", (eid, kind, time.time()))
            self._db.commit()
        log.info("stripe %s %s: %s", kind, eid, result)
        return result

    def _apply(self, kind: str, obj: dict, livemode: bool) -> str:
        if kind == "checkout.session.completed":
            return self._checkout(obj, livemode)
        if kind in ("customer.subscription.updated", "customer.subscription.deleted"):
            account = self.account_for(obj.get("customer"))
            if not account:
                return "unknown customer"
            current = self._current_subscription(obj.get("customer"))
            if current and obj.get("id") and obj["id"] != current:
                return "old subscription"
            if kind.endswith("deleted") or obj.get("status") in ENDED:
                self.accounts.set_plan(account, FREE)
                return "free"
            plan = plan_of_subscription(obj)
            if plan and obj.get("status") in ("active", "trialing", "past_due"):
                if self.accounts.get_plan(account) != plan:
                    self.accounts.set_plan(account, plan)
                return plan
            return "ignored"
        if kind == "invoice.payment_failed":
            account = self.account_for(obj.get("customer"))
            if not account:
                return "unknown customer"
            if obj.get("next_payment_attempt") is None:
                self.accounts.set_plan(account, FREE)
                return "free"
            return "retrying"
        return "ignored"

    def _checkout(self, s: dict, livemode: bool) -> str:
        meta = dict(s.get("metadata") or {})
        by_link = self.link_plans.get(s.get("payment_link") or "")
        if by_link == "top_up" and "zero_kind" not in meta:
            meta["zero_kind"] = "top_up"
        elif by_link in PAID_PLANS and "zero_plan" not in meta:
            meta["zero_plan"] = by_link
        account = s.get("client_reference_id")
        if not account:
            log.warning("checkout %s has no client_reference_id; the plan cannot be applied", s.get("id"))
            return "no account"
        if s.get("status") not in (None, "complete") or s.get("payment_status") not in ("paid", "no_payment_required"):
            return "unpaid"
        if meta.get("zero_kind") == "top_up":
            if self.on_top_up:
                self.on_top_up(account, int(meta.get("zero_minutes") or TOP_UP_MINUTES))
            return "top_up"
        plan = meta.get("zero_plan")
        if plan not in PAID_PLANS:
            log.warning("checkout %s has no known zero_plan", s.get("id"))
            return "unknown plan"
        if s.get("customer"):
            self._remember(s["customer"], account, s.get("subscription"), livemode)
        self.accounts.set_plan(account, plan)
        return plan

    def receive(self, payload: bytes, signature: str | None, secrets: list[str] | str) -> tuple[int, dict]:
        ""
        try:
            verify(payload, signature, secrets)
        except BadSignature as e:
            return 400, {"error": "bad_signature", "detail": str(e)}
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return 400, {"error": "bad_json"}
        if not isinstance(event, dict):
            return 400, {"error": "bad_json"}
        return 200, {"received": True, "result": self.handle(event)}


def plan_of_subscription(sub: dict) -> str | None:
    for item in ((sub.get("items") or {}).get("data") or []):
        price = item.get("price") or {}
        plan = LOOKUP_KEYS.get(price.get("lookup_key") or "") or (price.get("metadata") or {}).get("zero_plan")
        if plan in PAID_PLANS:
            return plan
    return None


def links_from_file(path: str | Path) -> dict[str, str]:
    ""
    out = {}
    for mode in json.loads(Path(path).read_text()).values():
        for plan, link in (mode.get("payment_links") or {}).items():
            out[link["id"]] = plan
    return out


def forward_secrets() -> list[str]:
    ""
    return [s.strip() for s in os.environ.get("BILLING_FORWARD_SECRET", "").split(",") if s.strip()]


def router(billing: Billing, secrets: Callable[[], list[str]] = forward_secrets):
    ""
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    r = APIRouter()

    @r.post("/billing/stripe/webhook")
    async def stripe_webhook(request: Request):
        status, body = billing.receive(await request.body(), request.headers.get("stripe-signature"), secrets())
        return JSONResponse(body, status_code=status)

    return r
