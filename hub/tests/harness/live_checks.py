"""Home-agnostic checks against any running hub: Colin's, Riley's, the test home's or the dry-mode fixture hub.

Without a token every request is a GET that a hub must refuse or answer without side effects, so this is safe to
point at Colin's live hub (read-only; it never pairs, never starts a turn, never sends a wrong pairing code unless
--probe-pairing is given, because ten wrong codes lock pairing for ten minutes).

  health              /api/health answers ok
  refuses_no_token    every device endpoint answers 401 without a token
  refuses_bad_token   and 401 with a made-up token
  lead_present        (token) the team has the lead Zip
  home_settings       (token) /api/home-settings has the plan's fields (WP1; skipped before)
  colin_free          (token) no Colin fact in this home's answers, unless it is Colin's home
  pairing_probe       (--probe-pairing) a wrong code answers 400, not a token

  python3 live_checks.py --hub URL [--token-file F] [--home-shape test] [--probe-pairing] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import home_checks  # noqa: E402
import homes  # noqa: E402
from assert_state import READ_ENDPOINTS, Report, _report_findings, _try  # noqa: E402
from hubapi import Hub, HubError  # noqa: E402

DEVICE_GETS = ("/api/bots", "/api/me", "/api/onboarding", "/api/feed", "/api/board", "/api/logins", "/api/devices",
               "/api/accounts", "/api/templates", "/api/settings", "/api/permissions", "/api/rules",
               "/api/proactivity", "/api/checkins", "/api/signin-requests", "/api/bots/default/messages")


def run(hub_url: str, token: str | None, home: dict | None, probe_pairing: bool) -> Report:
    rep = Report()
    anon = Hub(hub_url, timeout=15)
    try:
        h = anon.get("/api/health", auth=False)
        rep.ok(bool(h.get("ok")), "health", "the hub answers", f"/api/health said {h}")
    except (HubError, OSError) as e:
        rep.add("FAIL", "health", f"the hub does not answer: {e}")
        return rep
    open_paths = [p for p in DEVICE_GETS if anon.status("GET", p, auth=False) not in (401, 403)]
    rep.ok(not open_paths, "refuses_no_token", f"{len(DEVICE_GETS)} device endpoints refuse a request without a token",
           f"answered without a token: {', '.join(open_paths)}")
    fake = Hub(hub_url, token="zero-e2e-not-a-real-token-" + "x" * 16, timeout=15)
    open_paths = [p for p in DEVICE_GETS[:6] if fake.status("GET", p) not in (401, 403)]
    rep.ok(not open_paths, "refuses_bad_token", "a made-up token is refused",
           f"answered a made-up token: {', '.join(open_paths)}")
    if probe_pairing:
        st = anon.status("POST", "/api/pair", auth=False, body={"code": "ZZZZ2222", "device_name": "probe"})
        rep.ok(st in (400, 429), "pairing_probe", f"a wrong code answers {st}", f"a wrong code answered {st}")
    if not token:
        rep.add("SKIP", "lead_present", "no token (read-only mode)")
        return rep
    hub = Hub(hub_url, token)
    bots = _try(hub, "/api/bots")
    if isinstance(bots, HubError):
        rep.add("FAIL", "lead_present", str(bots))
        return rep
    rep.ok(any(b.get("id") == "default" for b in bots.get("bots", [])), "lead_present", "the lead Zip is on the team",
           "the team has no lead Zip")
    hs = _try(hub, "/api/home-settings")
    if isinstance(hs, HubError):
        rep.add("SKIP" if hs.status in (404, 405) else "FAIL", "home_settings",
                "the hub has no /api/home-settings yet (WP1 not merged)" if hs.status in (404, 405) else str(hs))
    else:
        blob = json.dumps(hs)
        missing = [k for k in ("person", "plan", "connectors") if f'"{k}"' not in blob]
        plan = hs.get("plan") if isinstance(hs.get("plan"), str) else (hs.get("plan") or {}).get("plan")
        rep.ok(not missing and plan in ("own", "shared", None), "home_settings",
               f"/api/home-settings has person, plan ({plan}) and connectors",
               f"/api/home-settings lacks {', '.join(missing) or 'nothing'}; plan is {plan!r}")
    texts = {p: home_checks.scannable(r) for p in READ_ENDPOINTS if isinstance(r := _try(hub, p), dict)}
    if home_checks.colin_facts(home):
        _report_findings(rep, "colin_free", home_checks.scan(texts, home), f"{len(texts)} answers")
    else:
        rep.add("SKIP", "colin_free", "this is Colin's home")
    return rep


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Home-agnostic checks against a running hub.")
    ap.add_argument("--hub", required=True)
    ap.add_argument("--token-file")
    ap.add_argument("--home-shape", default="test", choices=homes.SHAPES)
    ap.add_argument("--home-json", help="the home's settings, when readable (wins over --home-shape)")
    ap.add_argument("--probe-pairing", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    home = json.loads(Path(a.home_json).read_text()) if a.home_json else homes.shape(a.home_shape)
    token = Path(a.token_file).read_text().strip() if a.token_file else None
    rep = run(a.hub, token, home, a.probe_pairing)
    n = {s: sum(1 for i in rep.items if i["status"] == s) for s in ("PASS", "FAIL", "WARN", "SKIP")}
    print(f"\n{n['PASS']} passed, {n['FAIL']} failed, {n['WARN']} warnings, {n['SKIP']} skipped")
    if a.json:
        Path(a.json).write_text(json.dumps({"counts": n, "items": rep.items}, indent=1))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
