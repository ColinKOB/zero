"""Does to a hub what the app's onboarding does, without the simulator: pair, save the answers, add the mailbox
(when the hub has WP1's endpoint), and ask the lead for the picked Zips. Used by `zero-e2e.sh --headless` and by
the harness's own tests; the simulator run is the real proof, this is the quick one.

The calls and their order follow ios/Taffy/Views/Onboarding/OnboardingSetup.swift (saveOnboardingAnswers,
sendPickedZips, finishOnboarding) and the plan's new endpoints (section 9).

  python3 simulate_app.py --hub URL --code CODE [--persona persona.json] [--token-out F] [--no-mailbox]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from hubapi import Hub, HubError  # noqa: E402

VOICES = {"Kore": "kore", "Puck": "puck", "Charon": "charon", "Aoede": "aoede", "Zephyr": "zephyr"}


def onboard(hub: Hub, persona: dict, mailbox: bool = True) -> list[str]:
    """Returns what was done, one line each."""
    done = []
    answers = {"agent_name": persona["agent_name"], "person_name": persona["person_name"],
               "about": persona["about"], "voice": VOICES.get(persona.get("voice"), "kore"), "no_computer": False,
               "sources": {k: True for k in ("files", "apps", "contact", "calendar", "email", "picked_files")},
               "look": {"body": "", "outfit": ""}}
    hub.post("/api/onboarding", answers)
    done.append("saved the onboarding answers")
    # How Should Your Zips Ask?: the app turns the sleep answer into quiet hours and saves them with Zero's questions
    # on (the app's own parser is exercised by the simulator run; here the persona says what it should give).
    # The app sends the phone's own time zone with them (OnboardingHome.swift, TimeZone.current). The persona says
    # which zone its phone is in, and where it lives agrees (live convo QA 2: Sam "lived in Portland" while the app
    # sent the simulator's Eastern zone and the home was made in Central).
    if persona.get("quiet_hours"):
        person = {"quiet_hours": persona["quiet_hours"]}
        if persona.get("timezone"):
            person["timezone"] = persona["timezone"]
        try:
            hub.call("PUT", "/api/home-settings", {"person": person, "proactivity": {"enabled": True}})
            done.append(f"saved quiet hours {persona['quiet_hours']}"
                        + (f" and time zone {persona['timezone']}" if persona.get("timezone") else ""))
        except HubError as e:
            done.append(f"quiet hours not saved: HTTP {e.status} ({e.detail})")
    mb = persona.get("mailbox") or {}
    if mailbox and mb:
        # The app's provider ids: "iCloud Mail" -> icloud, "Gmail" -> gmail, "Other Email" -> other.
        provider = next((p for p in ("icloud", "gmail", "other") if mb.get("provider", "other").lower().startswith(p)),
                        "other")
        body = {"address": mb["address"], "provider": provider,
                "password": mb["password"], "imap_host": mb.get("imap_host"), "imap_port": mb.get("imap_port"),
                "smtp_host": mb.get("smtp_host"), "smtp_port": mb.get("smtp_port")}
        try:
            r = hub.post("/api/accounts/mailboxes", body)
            done.append(f"added the mailbox (checked: {r.get('checked')})")
        except HubError as e:
            done.append(f"mailbox not added: HTTP {e.status} ({'no endpoint yet' if e.status in (404, 405) else e.detail})")
    templates = {t["id"]: t for t in hub.get("/api/templates").get("templates", [])}
    picks = [templates[p] for p in persona.get("picks", []) if p in templates]
    if picks:
        listing = ", ".join(f"{t['name']} (the {t['id']} template)" for t in picks)
        reply = hub.ask("default", f"Please make me these Zips from their templates: {listing}.", wait=600)
        done.append(f"asked the lead for {', '.join(t['name'] for t in picks)}; it answered: {(reply or '')[:120]!r}")
    return done


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hub", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--persona", default=str(HERE / "persona.json"))
    ap.add_argument("--token-out", help="keep the device token here (mode 600) for assert_state.py")
    ap.add_argument("--no-mailbox", action="store_true")
    a = ap.parse_args(argv)
    hub = Hub(a.hub)
    hub.pair(a.code, "Zero E2E headless app")
    if a.token_out:
        hub.save_token(Path(a.token_out))
    for line in onboard(hub, json.loads(Path(a.persona).read_text()), mailbox=not a.no_mailbox):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
