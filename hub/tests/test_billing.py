"""Billing (billing.py): Stripe events set and clear a person's plan. Nothing here calls Stripe; events are built
by hand in the shape Stripe sends.

Run: python3 -m unittest hub/tests/test_billing.py (from the repo)
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import billing  # noqa: E402

SECRET = "whsec_test_forward"


class FakeAccounts:
    def __init__(self):
        self.plans: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def get_plan(self, account_id):
        return self.plans.get(account_id, "free")

    def set_plan(self, account_id, plan):
        self.plans[account_id] = plan
        self.calls.append((account_id, plan))


def event(kind, obj, eid=None, livemode=False):
    event.n = getattr(event, "n", 0) + 1
    return {"id": eid or f"evt_{event.n}", "type": kind, "livemode": livemode, "data": {"object": obj}}


def checkout(account="acct-1", plan="cloud", customer="cus_1", sub="sub_1", **kw):
    s = {"id": "cs_1", "object": "checkout.session", "mode": "subscription", "status": "complete",
         "payment_status": "paid", "client_reference_id": account, "customer": customer, "subscription": sub,
         "metadata": {"zero_plan": plan}}
    s.update(kw)
    return event("checkout.session.completed", s)


def subscription(kind="updated", sub="sub_1", customer="cus_1", status="active", lookup="zero_cloud_monthly"):
    return event(f"customer.subscription.{kind}", {
        "id": sub, "object": "subscription", "customer": customer, "status": status,
        "items": {"data": [{"price": {"id": "price_x", "lookup_key": lookup, "metadata": {}}}]}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.accounts = FakeAccounts()
        self.topups = []
        self.b = billing.Billing(self.accounts, Path(self.tmp.name) / "billing.db",
                                 on_top_up=lambda a, m: self.topups.append((a, m)))

    def tearDown(self):
        self.b.close()
        self.tmp.cleanup()


class Checkout(Base):
    def test_sets_the_plan_from_the_link(self):
        self.assertEqual(self.b.handle(checkout(plan="plus")), "plus")
        self.assertEqual(self.accounts.plans, {"acct-1": "plus"})

    def test_remembers_the_customer(self):
        self.b.handle(checkout())
        self.assertEqual(self.b.account_for("cus_1"), "acct-1")

    def test_without_account_changes_nothing(self):
        self.assertEqual(self.b.handle(checkout(account=None)), "no account")
        self.assertEqual(self.accounts.calls, [])

    def test_unknown_plan_changes_nothing(self):
        self.assertEqual(self.b.handle(checkout(plan="gold")), "unknown plan")
        self.assertEqual(self.accounts.calls, [])

    def test_unpaid_changes_nothing(self):
        self.assertEqual(self.b.handle(checkout(payment_status="unpaid")), "unpaid")
        self.assertEqual(self.accounts.calls, [])

    def test_top_up_adds_minutes_not_a_plan(self):
        e = checkout(mode="payment", subscription=None, metadata={"zero_kind": "top_up", "zero_minutes": "30"})
        self.assertEqual(self.b.handle(e), "top_up")
        self.assertEqual(self.topups, [("acct-1", 30)])
        self.assertEqual(self.accounts.calls, [])

    def test_plan_from_the_link_id_when_metadata_is_missing(self):
        b = billing.Billing(self.accounts, Path(self.tmp.name) / "b2.db", link_plans={"plink_plus": "plus"})
        self.assertEqual(b.handle(checkout(metadata={}, payment_link="plink_plus")), "plus")
        b.close()

    def test_links_from_file(self):
        f = Path(self.tmp.name) / "stripe.json"
        f.write_text(json.dumps({"test": {"payment_links": {"lite": {"id": "plink_a", "url": "u"},
                                                            "top_up": {"id": "plink_b", "url": "u"}}}}))
        self.assertEqual(billing.links_from_file(f), {"plink_a": "lite", "plink_b": "top_up"})

    def test_same_event_twice_applies_once(self):
        e = checkout()
        self.b.handle(e)
        self.assertEqual(self.b.handle(e), "duplicate")
        self.assertEqual(len(self.accounts.calls), 1)


class Ending(Base):
    def setUp(self):
        super().setUp()
        self.b.handle(checkout(plan="cloud"))

    def test_cancellation_returns_to_free(self):
        self.assertEqual(self.b.handle(subscription("deleted", status="canceled")), "free")
        self.assertEqual(self.accounts.plans["acct-1"], "free")

    def test_final_failed_renewal_returns_to_free(self):
        e = event("invoice.payment_failed", {"customer": "cus_1", "next_payment_attempt": None})
        self.assertEqual(self.b.handle(e), "free")
        self.assertEqual(self.accounts.plans["acct-1"], "free")

    def test_failed_renewal_with_retries_left_keeps_the_plan(self):
        e = event("invoice.payment_failed", {"customer": "cus_1", "next_payment_attempt": int(time.time()) + 86400})
        self.assertEqual(self.b.handle(e), "retrying")
        self.assertEqual(self.accounts.plans["acct-1"], "cloud")

    def test_unpaid_subscription_returns_to_free(self):
        self.assertEqual(self.b.handle(subscription(status="unpaid")), "free")

    def test_plan_change_in_the_portal(self):
        self.assertEqual(self.b.handle(subscription(lookup="zero_cloud_plus_monthly")), "plus")
        self.assertEqual(self.accounts.plans["acct-1"], "plus")

    def test_cancel_at_period_end_keeps_the_plan_until_deleted(self):
        self.assertEqual(self.b.handle(subscription(status="active")), "cloud")
        self.assertEqual(self.accounts.plans["acct-1"], "cloud")

    def test_old_subscription_ending_does_not_touch_the_new_one(self):
        self.b.handle(checkout(plan="plus", sub="sub_2"))
        self.assertEqual(self.b.handle(subscription("deleted", sub="sub_1", status="canceled")), "old subscription")
        self.assertEqual(self.accounts.plans["acct-1"], "plus")

    def test_unknown_customer_is_ignored(self):
        self.assertEqual(self.b.handle(subscription("deleted", customer="cus_other")), "unknown customer")


class Signatures(Base):
    def test_good_signature(self):
        body = json.dumps(checkout()).encode()
        status, out = self.b.receive(body, billing.sign(body, SECRET), [SECRET])
        self.assertEqual((status, out["result"]), (200, "cloud"))

    def test_second_secret_works_during_a_change(self):
        body = json.dumps(checkout()).encode()
        status, _ = self.b.receive(body, billing.sign(body, "new"), ["old", "new"])
        self.assertEqual(status, 200)

    def test_wrong_secret(self):
        body = json.dumps(checkout()).encode()
        status, out = self.b.receive(body, billing.sign(body, "other"), [SECRET])
        self.assertEqual((status, out["error"]), (400, "bad_signature"))
        self.assertEqual(self.accounts.calls, [])

    def test_changed_body(self):
        body = json.dumps(checkout()).encode()
        sig = billing.sign(body, SECRET)
        status, _ = self.b.receive(body.replace(b"cloud", b"plus"), sig, [SECRET])
        self.assertEqual(status, 400)

    def test_old_signature(self):
        body = b"{}"
        with self.assertRaises(billing.BadSignature):
            billing.verify(body, billing.sign(body, SECRET, now=time.time() - 3600), SECRET)

    def test_no_secret_configured_refuses(self):
        body = b"{}"
        with self.assertRaises(billing.BadSignature):
            billing.verify(body, billing.sign(body, SECRET), [])

    def test_missing_header(self):
        status, _ = self.b.receive(b"{}", None, [SECRET])
        self.assertEqual(status, 400)


class Links(unittest.TestCase):
    def test_checkout_url_carries_the_account(self):
        url = billing.checkout_url("https://buy.stripe.com/test_abc", "acct 1", "pat@example.com")
        self.assertEqual(url, "https://buy.stripe.com/test_abc?client_reference_id=acct+1"
                              "&prefilled_email=pat%40example.com")


class Router(Base):
    def test_route(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi not installed")
        app = FastAPI()
        app.include_router(billing.router(self.b, secrets=lambda: [SECRET]))
        c = TestClient(app)
        body = json.dumps(checkout(plan="lite")).encode()
        r = c.post("/billing/stripe/webhook", content=body, headers={"Stripe-Signature": billing.sign(body, SECRET)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.accounts.plans["acct-1"], "lite")
        r = c.post("/billing/stripe/webhook", content=body, headers={"Stripe-Signature": "t=1,v1=00"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
