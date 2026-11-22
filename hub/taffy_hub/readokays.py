""












from __future__ import annotations

import importlib
import json
import logging
import time

from . import consent

log = logging.getLogger("taffy.readokays")


SERVICES = {"gmail.com": "gmail", "calendar.google.com": "calendar", "drive.google.com": "drive", "trello.com": "trello"}


def _tiers():
    from . import noticers
    noticers._plugin_tools()
    return importlib.import_module(f"{noticers._PLUGIN_PKG}.tiers")



STOP_FIELDS = ("send_to", "share_with", "delete", "publish")


def _call(r) -> tuple[str, str, list, bool, dict]:
    ""

    try:
        call = json.loads(r["request"] or "")
    except (TypeError, ValueError):
        call = None
    if isinstance(call, dict):
        return (str(call.get("service") or ""), str(call.get("request") or ""), list(call.get("guests") or []),
                call.get("notify_guests") is True, call)
    return SERVICES.get(r["site"] or "", ""), r["inputs"] or "", [], False, {}






NOT_STOPS = ("write", "mac")


def hard_stops() -> bool:
    ""
    try:
        return bool(_tiers().hard_stops())
    except Exception:
        log.warning("could not read whether the four hard stops are on", exc_info=True)
        return False


def close_non_stops() -> int:
    ""
    if not hard_stops():
        return 0
    closed = 0
    with consent.db() as c:
        rows = c.execute("SELECT id FROM requests WHERE status='pending' AND kind IN (?, ?)", NOT_STOPS).fetchall()
        for r in rows:
            closed += c.execute("UPDATE requests SET status='withdrawn', decided=? WHERE id=? AND status='pending'",
                                (time.time(), r["id"])).rowcount
    log.info("closed %d card(s) that the four hard stops make without asking", closed)
    return closed


def close_pending_reads(classify=None) -> int:
    ""
    if classify is None:
        try:
            classify = _tiers().classify_connector
        except Exception:
            log.warning("could not load the rules to recheck pending okays", exc_info=True)
            return 0
    closed = 0
    with consent.db() as c:
        rows = c.execute("SELECT id, site, inputs, request, kind FROM requests WHERE status='pending' "
                         "AND kind != 'read' AND (COALESCE(inputs, '') != '' OR COALESCE(request, '') != '')").fetchall()
        for r in rows:
            if (r["site"] or "") not in SERVICES:
                continue
            service, request, guests, notify, call = _call(r)
            if service not in SERVICES.values() or not request:
                continue
            if hard_stops() and not all(k in call for k in STOP_FIELDS):


                continue
            try:
                kind = classify(service, request, guests, notify, call).kind
            except Exception:
                continue


            if kind == "read" or (kind == "write" and (hard_stops() or
                                                      (service == "calendar" and r["kind"] != "write"))):
                closed += c.execute("UPDATE requests SET status='withdrawn', decided=? WHERE id=? AND status='pending'",
                                    (time.time(), r["id"])).rowcount
    log.info("closed %d okay card(s) that today's rules make or read without asking", closed)
    return closed
