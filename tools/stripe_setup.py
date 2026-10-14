#!/usr/bin/env python3
"""Creates Zero Cloud's Stripe objects, or finds the ones already made. Safe to run again.

  python3 tools/stripe_setup.py test                   products, prices, Payment Links, customer portal, webhook
  python3 tools/stripe_setup.py live                   products, prices, customer portal, webhook. No Payment Links,
                                                       so nobody can pay real money for a Cloud box before it exists
  python3 tools/stripe_setup.py live --live-links      also the live Payment Links (Colin's decision)

The key comes from STRIPE_API_KEY, or from ~/.config/zero-billing/stripe-keys.json (a Stripe CLI pairing; the
file is 0600 and never printed). Each new webhook's signing secret goes straight into the Railway variable
STRIPE_WEBHOOK_SECRET of the relay (project zero-oauth, service relay), appended to the ones already there, and is
never printed or written to disk. Everything printed and saved (docs/billing-stripe.json) is publishable: IDs and URLs.

Standard library only. The tests never run this; it is the one place that calls Stripe.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.stripe.com/v1"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "billing-stripe.json"
KEYS = Path.home() / ".config" / "zero-billing" / "stripe-keys.json"
RELAY_DIR = ROOT / "oauth-relay"
WEBHOOK_URL = "https://relay.example.com/stripe/webhook"
EVENTS = ["checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted",
          "invoice.payment_failed"]

PLANS = [
    {"plan": "lite", "name": "Zero Cloud Lite", "cents": 500, "lookup": "zero_cloud_lite_monthly",
     "description": "Your own Zero, run for you without a Mac. Includes 30 agent minutes a month and 3 scheduled "
                    "jobs a day."},
    {"plan": "cloud", "name": "Zero Cloud", "cents": 800, "lookup": "zero_cloud_monthly",
     "description": "Your own Zero, run for you without a Mac. Includes 60 agent minutes a month and 5 scheduled "
                    "jobs a day."},
    {"plan": "plus", "name": "Zero Cloud Plus", "cents": 1200, "lookup": "zero_cloud_plus_monthly",
     "description": "Your own Zero, run for you without a Mac. Includes 150 agent minutes a month, 10 scheduled "
                    "jobs a day and twice the room to chat."},
]
TOP_UP = {"name": "30 More Agent Minutes", "cents": 200, "lookup": "zero_top_up_30",
          "description": "Adds 30 agent minutes to this month on any Zero Cloud plan."}
THANKS = "Thank you. Your Zero Cloud plan is ready. You can go back to the Zero app now."
THANKS_TOP_UP = "Thank you. Your 30 extra agent minutes are ready in the Zero app."


def key_for(mode: str) -> str:
    env = os.environ.get("STRIPE_API_KEY", "")
    if env:
        if ("_live_" in env) != (mode == "live"):
            sys.exit(f"STRIPE_API_KEY is not a {mode} key")
        return env
    if not KEYS.exists():
        sys.exit("No Stripe key: set STRIPE_API_KEY or pair the CLI (see docs/billing.md)")
    d = json.loads(KEYS.read_text())
    k = d.get("livemode_key_secret" if mode == "live" else "testmode_key_secret") or ""
    if not k:
        sys.exit(f"The paired keys have no {mode} key")
    return k


class Stripe:
    def __init__(self, key: str):
        self.key = key

    def call(self, method: str, path: str, params: list | dict | None = None) -> dict:
        data = urllib.parse.urlencode(params or [], doseq=True)
        url = f"{API}{path}"
        body = None
        if method == "GET" and data:
            url += "?" + data
        elif data:
            body = data.encode()
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            err = json.loads(e.read() or b"{}").get("error", {})
            sys.exit(f"Stripe refused {method} {path}: {e.code} {err.get('type')} {err.get('message')}")

    def all(self, path: str, params: list | None = None) -> list:
        out, after = [], None
        while True:
            p = list(params or []) + [("limit", 100)] + ([("starting_after", after)] if after else [])
            page = self.call("GET", path, p)
            out += page["data"]
            if not page.get("has_more"):
                return out
            after = page["data"][-1]["id"]


def price_for(s: Stripe, spec: dict, recurring: bool, meta: dict) -> dict:
    found = s.call("GET", "/prices", [("lookup_keys[]", spec["lookup"]), ("active", "true"),
                                      ("expand[]", "data.product")])["data"]
    if found:
        p = found[0]
        if p["unit_amount"] != spec["cents"]:
            sys.exit(f"{spec['lookup']} exists at {p['unit_amount']} cents, not {spec['cents']}; change it by hand")
        return p
    prod = s.call("POST", "/products", [("name", spec["name"]), ("description", spec["description"])]
                  + [(f"metadata[{k}]", v) for k, v in meta.items()])
    params = [("product", prod["id"]), ("currency", "usd"), ("unit_amount", spec["cents"]),
              ("lookup_key", spec["lookup"])] \
        + [(f"metadata[{k}]", v) for k, v in meta.items()]
    if recurring:
        params.append(("recurring[interval]", "month"))
    p = s.call("POST", "/prices", params)
    p["product"] = prod
    return p


def link_for(s: Stripe, price: dict, meta: dict, thanks: str, existing: list) -> dict:
    for l in existing:
        if l.get("active") and all(l.get("metadata", {}).get(k) == v for k, v in meta.items()):
            return l
    params = [("line_items[0][price]", price["id"]), ("line_items[0][quantity]", 1),
              ("after_completion[type]", "hosted_confirmation"),
              ("after_completion[hosted_confirmation][custom_message]", thanks),
              ("allow_promotion_codes", "false")] + [(f"metadata[{k}]", v) for k, v in meta.items()]
    if price.get("recurring"):
        params += [(f"subscription_data[metadata][{k}]", v) for k, v in meta.items()]
    else:
        params += [(f"payment_intent_data[metadata][{k}]", v) for k, v in meta.items()]
    return s.call("POST", "/payment_links", params)


def portal_for(s: Stripe, plan_prices: list[dict]) -> dict:
    for c in s.all("/billing_portal/configurations", [("active", "true")]):
        if c.get("metadata", {}).get("zero") == "cloud":
            return c
    params = [("business_profile[headline]", "Manage your Zero Cloud plan."),
              ("features[subscription_cancel][enabled]", "true"),
              ("features[subscription_cancel][mode]", "at_period_end"),
              ("features[subscription_cancel][cancellation_reason][enabled]", "true"),
              ("features[subscription_cancel][cancellation_reason][options][]", "too_expensive"),
              ("features[subscription_cancel][cancellation_reason][options][]", "missing_features"),
              ("features[subscription_cancel][cancellation_reason][options][]", "unused"),
              ("features[subscription_cancel][cancellation_reason][options][]", "other"),
              ("features[subscription_update][enabled]", "true"),
              ("features[subscription_update][default_allowed_updates][]", "price"),
              ("features[subscription_update][proration_behavior]", "create_prorations"),
              ("features[payment_method_update][enabled]", "true"),
              ("features[invoice_history][enabled]", "true"),
              ("features[customer_update][enabled]", "true"),
              ("features[customer_update][allowed_updates][]", "email"),
              ("login_page[enabled]", "true"), ("metadata[zero]", "cloud")]
    for i, p in enumerate(plan_prices):
        params += [(f"features[subscription_update][products][{i}][product]", p["product"]["id"]),
                   (f"features[subscription_update][products][{i}][prices][]", p["id"])]
    return s.call("POST", "/billing_portal/configurations", params)


def webhook_for(s: Stripe) -> tuple[dict, str | None]:
    for w in s.all("/webhook_endpoints"):
        if w["url"] == WEBHOOK_URL:
            return w, None
    w = s.call("POST", "/webhook_endpoints", [("url", WEBHOOK_URL), ("description", "Zero Cloud plans")]
               + [("enabled_events[]", e) for e in EVENTS])
    return w, w.pop("secret")


def add_railway_secret(secret: str) -> None:
    """Appends the signing secret to STRIPE_WEBHOOK_SECRET on the relay. The value never reaches the terminal."""
    out = subprocess.run(["railway", "variable", "list", "--json"], cwd=RELAY_DIR, capture_output=True, text=True,
                         check=True).stdout
    have = [v for v in json.loads(out).get("STRIPE_WEBHOOK_SECRET", "").split(",") if v]
    if secret not in have:
        have.append(secret)
    subprocess.run(["railway", "variable", "set", "STRIPE_WEBHOOK_SECRET", "--stdin"], cwd=RELAY_DIR,
                   input=",".join(have), capture_output=True, text=True, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["test", "live"])
    ap.add_argument("--live-links", action="store_true", help="also create live Payment Links (Colin decides)")
    a = ap.parse_args()
    s = Stripe(key_for(a.mode))
    acct = s.call("GET", "/account")
    result = {"account": acct["id"], "mode": a.mode, "charges_enabled": acct.get("charges_enabled"),
              "plans": {}, "payment_links": {}}

    plan_prices = []
    for spec in PLANS:
        p = price_for(s, spec, True, {"zero_plan": spec["plan"]})
        plan_prices.append(p)
        result["plans"][spec["plan"]] = {"name": spec["name"], "usd_month": spec["cents"] / 100,
                                         "product": p["product"]["id"], "price": p["id"], "lookup_key": spec["lookup"]}
    top = price_for(s, TOP_UP, False, {"zero_kind": "top_up", "zero_minutes": "30"})
    result["top_up"] = {"name": TOP_UP["name"], "usd": TOP_UP["cents"] / 100, "product": top["product"]["id"],
                        "price": top["id"], "lookup_key": TOP_UP["lookup"]}

    if a.mode == "test" or a.live_links:
        existing = s.all("/payment_links", [("active", "true")])
        for spec, p in zip(PLANS, plan_prices):
            l = link_for(s, p, {"zero_plan": spec["plan"]}, THANKS, existing)
            result["payment_links"][spec["plan"]] = {"id": l["id"], "url": l["url"]}
        l = link_for(s, top, {"zero_kind": "top_up", "zero_minutes": "30"}, THANKS_TOP_UP, existing)
        result["payment_links"]["top_up"] = {"id": l["id"], "url": l["url"]}

    portal = portal_for(s, plan_prices)
    result["portal"] = {"configuration": portal["id"], "login_url": (portal.get("login_page") or {}).get("url")}

    hook, secret = webhook_for(s)
    if secret:
        add_railway_secret(secret)
    result["webhook"] = {"id": hook["id"], "url": hook["url"], "events": hook.get("enabled_events"),
                         "secret": "added to Railway now" if secret else "endpoint already existed; secret set then"}

    saved = json.loads(OUT.read_text()) if OUT.exists() else {}
    saved[a.mode] = result
    OUT.write_text(json.dumps(saved, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
