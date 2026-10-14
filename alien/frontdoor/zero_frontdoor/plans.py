"""Which plan an account is on. Every account starts on "free". Billing (built separately) calls set_plan when a
payment starts or ends; nothing else in the front door decides what a plan allows yet.

    from zero_frontdoor import plans
    plans.get_plan("sam")            -> "free"
    plans.set_plan("sam", "paid")

`account` is a username or an account row (a dict with "username").
"""

from __future__ import annotations

import re

from . import store

DEFAULT_PLAN = "free"
PLAN_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _username(account) -> str:
    if isinstance(account, dict):
        return str(account.get("username") or "")
    return str(account or "")


def get_plan(account) -> str:
    """The account's plan, or "free" when it has none. Raises KeyError for an unknown account."""
    row = store.get(_username(account))
    if not row:
        raise KeyError(_username(account))
    return row.get("plan") or DEFAULT_PLAN


def set_plan(account, plan: str) -> str:
    """Puts the account on `plan` and returns it. Raises ValueError for a malformed plan name and KeyError for an
    unknown account."""
    plan = (plan or "").strip().lower()
    if not PLAN_RE.match(plan):
        raise ValueError(f"{plan!r} is not a plan name")
    store._set_plan_row(_username(account), plan)
    return plan
