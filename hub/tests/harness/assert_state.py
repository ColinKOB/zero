"""Hub-state assertions after a stranger's onboarding (plan section 7, "Driving the App in the Simulator").

Run after the simulator finishes the onboarding. Checks through the hub's API (any home, local or over a tunnel)
and, when the home's folder is readable (dry mode, or on alien as the home's user), through its files:

  API   lead_renamed        the lead Zip carries the name typed on Name It
        zips_created        a Zip exists for every picked template
        onboarding_saved    the hub kept the person's name and answers
        me_name             /api/me greets the person by their own first name
        lead_about          the lead's notes have the About entry with every answer
        mailbox_saved       the added mailbox is in the home's settings (needs WP1's endpoints; skipped before)
        templates_available /api/templates says which templates the home can run (WP1)
        plan_shared         a broker home reports plan "shared" (WP1)
        colin_free          no Colin fact in any answer the hub gives this home (home_checks.scan)
        inbox_lists_seeded  the Inbox Zip lists the seeded mail's subjects (with --ask-inbox; a real chat turn in
                            the test home, never in Colin's)
  FILE  registry            bots.json holds the picked Zips
        user_md_about       every USER.md (the lead's and each Zip's) has the About entry
        person_json         person.json has the person's name (what the plugin reads)
        home_json_valid     home.json passes the contract (home_checks.validate_home)
        home_json_mailbox   home.json lists the mailbox
        prompts_colin_free  nothing the hub sent to the Zips names Colin (dry mode: fake_hermes's turn log)

  python3 assert_state.py --hub URL (--token-file F | --code CODE) [--home-dir D] [--persona persona.json]
                          [--ask-inbox] [--json out.json] [--expect-mailbox] [--home-json-shape test]

Prints PASS, FAIL, WARN or SKIP per check and exits 1 when any check fails. Never prints a token or a password.
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
from hubapi import Hub, HubError  # noqa: E402

ABOUT_START, ABOUT_END = "<!-- onboarding:start -->", "<!-- onboarding:end -->"
READ_ENDPOINTS = ("/api/bots", "/api/me", "/api/onboarding", "/api/templates", "/api/accounts", "/api/settings",
                  "/api/permissions", "/api/rules", "/api/proactivity", "/api/feed", "/api/checkins",
                  "/api/home-settings", "/api/health")


class Report:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, status: str, check: str, detail: str = "") -> None:
        self.items.append({"status": status, "check": check, "detail": detail})
        print(f"{status:<5} {check}: {detail}", flush=True)

    def ok(self, cond: bool, check: str, good: str, bad: str) -> bool:
        self.add("PASS" if cond else "FAIL", check, good if cond else bad)
        return cond

    @property
    def failed(self) -> list[dict]:
        return [i for i in self.items if i["status"] == "FAIL"]


def _try(hub: Hub, path: str):
    try:
        return hub.get(path)
    except HubError as e:
        return e
    except OSError as e:
        return HubError(0, str(e), path)


def _templates(home_dir: Path | None) -> dict[str, dict]:
    for p in ([home_dir / "taffy" / "shared" / "zip-templates.json"] if home_dir else []) + \
             [HERE / "fixtures" / "zip-templates.json"]:
        try:
            return {t["id"]: t for t in json.loads(p.read_text())}
        except (OSError, ValueError):
            continue
    return {}


def _has_zip(bots: list[dict], pick: str, tpl: dict | None) -> bool:
    names = {pick, (tpl or {}).get("zip_id") or pick}
    label = (tpl or {}).get("name", "").lower()
    return any(b.get("id") in names or (label and str(b.get("name", "")).lower() == label) for b in bots)


def check_api(hub: Hub, persona: dict, rep: Report, home: dict | None, home_dir: Path | None,
              expect_mailbox: bool, ask_inbox: bool) -> dict:
    """Returns what it learned about the hub: {"wp1": the hub serves /api/home-settings}."""
    tpls = _templates(home_dir)
    bots_r = _try(hub, "/api/bots")
    if isinstance(bots_r, HubError):
        rep.add("FAIL", "hub_reachable", str(bots_r))
        return {"wp1": False}
    bots = bots_r.get("bots", [])
    lead = next((b for b in bots if b.get("id") == "default"), {})
    rep.ok(lead.get("name") == persona["agent_name"], "lead_renamed",
           f"the lead is {lead.get('name')!r}", f"the lead is {lead.get('name')!r}, expected {persona['agent_name']!r}")
    missing = [p for p in persona.get("picks", []) if not _has_zip(bots, p, tpls.get(p))]
    rep.ok(not missing, "zips_created", f"{', '.join(persona.get('picks', []))} are on the team",
           f"missing {', '.join(missing)}; the team is {', '.join(b.get('id', '?') for b in bots)}")

    ob = _try(hub, "/api/onboarding")
    rec = (ob.get("onboarding") or {}) if isinstance(ob, dict) else {}
    want_about = {k: v for k, v in persona.get("about", {}).items()}
    got_about = rec.get("about") or {}
    bad = [k for k, v in want_about.items() if got_about.get(k) != v]
    rep.ok(rec.get("person_name") == persona["person_name"] and not bad, "onboarding_saved",
           "the name and every answer were saved",
           f"saved name {rec.get('person_name')!r}; answers that differ: {', '.join(bad) or 'none'}")

    me = _try(hub, "/api/me")
    first = persona["person_name"].split()[0]
    if isinstance(me, HubError):
        rep.add("FAIL", "me_name", str(me))
    else:
        rep.ok(me.get("name") == first, "me_name", f"/api/me says {me.get('name')!r}",
               f"/api/me says {me.get('name')!r}, expected {first!r}")
        notes = " ".join(n.get("text", "") for n in me.get("notes", []) if n.get("title") == "What You Told Me")
        lacking = [v for v in [persona["person_name"], *want_about.values()] if v not in notes]
        rep.ok(bool(notes) and not lacking, "lead_about", "the lead's USER.md has the About entry",
               f"the About entry is {'missing' if not notes else 'missing ' + ', '.join(lacking)}")

    hs = _try(hub, "/api/home-settings")
    acc = _try(hub, "/api/accounts")
    wp1 = not (isinstance(hs, HubError) and hs.status in (404, 405))
    address = (persona.get("mailbox") or {}).get("address")
    if expect_mailbox and address:
        blobs = [json.dumps(x) for x in (hs, acc) if isinstance(x, dict)]
        if not wp1:
            rep.add("SKIP", "mailbox_saved", "the hub has no /api/home-settings yet (WP1 not merged)")
        else:
            rep.ok(any(address in b for b in blobs), "mailbox_saved", f"{address} is in the home's settings",
                   f"{address} is not in /api/home-settings or /api/accounts")
    else:
        rep.add("SKIP", "mailbox_saved", "this run added no mailbox")

    want_quiet = persona.get("quiet_hours")
    if not wp1:
        rep.add("SKIP", "quiet_hours_from_sleep", "the hub has no /api/home-settings yet (WP1 not merged)")
    elif want_quiet and isinstance(hs, dict):
        got = _find(hs, "quiet_hours")
        rep.ok(isinstance(got, dict) and got.get("start") == want_quiet["start"] and got.get("end") == want_quiet["end"],
               "quiet_hours_from_sleep", f"the sleep answer became quiet hours {got}",
               f"quiet hours are {got}, expected {want_quiet} from the sleep answer")
    if wp1 and isinstance(hs, dict):
        name = _find(hs.get("person", hs), "name")
        rep.add("PASS" if name == persona["person_name"] else "WARN", "settings_person_name",
                f"the home's settings name the person {name!r}" if name == persona["person_name"] else
                f"the home's settings say name {name!r}; the hub names the person from the onboarding answers instead")

    tr = _try(hub, "/api/templates")
    t_list = tr.get("templates", []) if isinstance(tr, dict) else []
    if t_list and all("available" in t for t in t_list):
        avail = {t["id"]: t["available"] for t in t_list}
        problems = [p for p in persona.get("picks", []) if p in avail and not avail[p]]
        rep.ok(not problems, "templates_available", "every picked template was available",
               f"picked but marked unavailable: {', '.join(problems)}")
    else:
        rep.add("SKIP", "templates_available", "templates carry no 'available' flag yet (WP1 not merged)")

    if home and home.get("plan", {}).get("claude", {}).get("mode") == "broker":
        if not wp1:
            rep.add("SKIP", "plan_shared", f"the hub predates WP1 (it reports plan {acc.get('plan') if isinstance(acc, dict) else None!r})")
        elif isinstance(acc, dict) and "plan" in acc:
            rep.ok(acc.get("plan") == "shared", "plan_shared", "the hub reports the shared plan",
                   f"/api/accounts plan is {acc.get('plan')!r}, expected 'shared'")
        else:
            rep.add("SKIP", "plan_shared", "/api/accounts has no plan field")

    texts = {}
    for path in READ_ENDPOINTS:
        r = _try(hub, path)
        if isinstance(r, dict):
            texts[path] = home_checks.scannable(r)
    findings = home_checks.scan(texts, home)
    _report_findings(rep, "colin_free", findings, f"{len(texts)} answers scanned")

    if ask_inbox:
        inbox = next((b["id"] for b in bots if b.get("id") == "inbox" or str(b.get("name")).lower() == "inbox"), None)
        if not inbox:
            rep.add("FAIL", "inbox_lists_seeded", "there is no Inbox Zip to ask")
        else:
            seeded = json.loads((HERE / "greenmail" / "messages.json").read_text())["messages"]
            try:
                reply = hub.ask(inbox, persona.get("inbox_probe", "List the subjects in my inbox."), wait=600) or ""
            except (HubError, OSError) as e:
                reply = ""
                rep.add("FAIL", "inbox_lists_seeded", f"the turn failed: {e}")
            else:
                found = [m["subject"] for m in seeded if _mentions(reply, m["subject"])]
                rep.ok(len(found) >= 2, "inbox_lists_seeded", f"the Inbox Zip named {len(found)} of {len(seeded)} seeded messages",
                       f"the Inbox Zip named {len(found)} of {len(seeded)} seeded messages")
    return {"wp1": wp1}


def _find(data, key: str):
    """The first value under `key` anywhere in a JSON answer (the settings' exact nesting is WP1's to pick)."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for v in data.values():
            found = _find(v, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find(v, key)
            if found is not None:
                return found
    return None


def _mentions(reply: str, subject: str) -> bool:
    words = [w for w in subject.lower().replace(":", " ").split() if len(w) > 4][:4]
    low = reply.lower()
    return sum(w in low for w in words) >= max(1, len(words) - 1)


def _report_findings(rep: Report, check: str, findings: list[dict], scope: str) -> None:
    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]
    if fails:
        rep.add("FAIL", check, f"{len(fails)} Colin fact(s) in {scope}: " +
                "; ".join(f"{f['label']} in {f['where']}: ...{f['excerpt']}..." for f in fails[:6]))
    else:
        rep.add("PASS", check, f"no Colin fact in {scope}")
    if warns:
        rep.add("WARN", check, f"{len(warns)} wording note(s): " +
                "; ".join(f"{f['label']} in {f['where']}: ...{f['excerpt']}..." for f in warns[:6]))


def check_files(home_dir: Path, persona: dict, rep: Report, home: dict | None, expect_mailbox: bool) -> None:
    tpls = _templates(home_dir)
    state = home_dir / "taffy" / "hub-state"
    try:
        reg = json.loads((state / "bots.json").read_text())
    except (OSError, ValueError):
        reg = []
    missing = [p for p in persona.get("picks", []) if not _has_zip(reg, p, tpls.get(p))]
    rep.ok(not missing, "registry", "bots.json holds every picked Zip", f"bots.json lacks {', '.join(missing)}")

    hermes = home_dir / ".taffy"
    files = [hermes / "memories" / "USER.md"] + sorted((hermes / "profiles").glob("*/memories/USER.md"))
    lacking = []
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            lacking.append(str(f.relative_to(home_dir)))
            continue
        if not (ABOUT_START in text and ABOUT_END in text and persona["person_name"] in text):
            lacking.append(str(f.relative_to(home_dir)))
    rep.ok(not lacking and len(files) >= 1 + len(persona.get("picks", [])), "user_md_about",
           f"all {len(files)} USER.md files have the About entry",
           f"{len(files)} USER.md files; without the entry: {', '.join(lacking) or 'none'}")

    try:
        person = json.loads((state / "person.json").read_text())
    except (OSError, ValueError):
        person = {}
    rep.ok(person.get("name") == persona["person_name"], "person_json", "person.json has the person's name",
           f"person.json name is {person.get('name')!r}")

    hj = home_dir / "taffy" / "home.json"
    if hj.exists():
        try:
            data = json.loads(hj.read_text())
        except ValueError as e:
            rep.add("FAIL", "home_json_valid", f"home.json is not JSON: {e}")
            data = None
        if data is not None:
            problems = home_checks.validate_home(data)
            rep.ok(not problems, "home_json_valid", "home.json matches the contract", "; ".join(problems))
            address = (persona.get("mailbox") or {}).get("address")
            if expect_mailbox == "skip":
                rep.add("SKIP", "home_json_mailbox", "the hub has no mailbox endpoint yet (WP1 not merged)")
            elif expect_mailbox and address:
                accounts = (data.get("mail") or {}).get("accounts") or []
                rep.ok(any(a.get("address") == address for a in accounts), "home_json_mailbox",
                       f"home.json lists {address}", f"home.json mail.accounts lacks {address}")
    else:
        rep.add("SKIP", "home_json_valid", "this home has no home.json")

    log = home_dir / "fake-hermes" / "turns.jsonl"
    if log.exists():
        turns = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        texts = [(f"turn {i + 1} to {t['bot']}", t["input"]) for i, t in enumerate(turns)]
        _report_findings(rep, "prompts_colin_free", home_checks.scan(texts, home), f"{len(texts)} turns the hub sent")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Hub-state assertions after a stranger's onboarding.")
    ap.add_argument("--hub", required=True)
    ap.add_argument("--token-file")
    ap.add_argument("--code", help="pair as the harness with this code (the token is kept in memory only)")
    ap.add_argument("--home-dir", help="the home's folder, when it is readable here")
    ap.add_argument("--persona", default=str(HERE / "persona.json"))
    ap.add_argument("--home-shape", default="test", choices=homes.SHAPES,
                    help="which shape's facts to scan against when no home.json is readable")
    ap.add_argument("--expect-mailbox", action="store_true", help="the run added the persona's mailbox")
    ap.add_argument("--ask-inbox", action="store_true", help="ask the Inbox Zip to list the seeded mail")
    ap.add_argument("--json", help="write the results here")
    a = ap.parse_args(argv)

    persona = json.loads(Path(a.persona).read_text())
    home_dir = Path(a.home_dir) if a.home_dir else None
    home = None
    if home_dir and (home_dir / "taffy" / "home.json").exists():
        home = json.loads((home_dir / "taffy" / "home.json").read_text())
    else:
        home = homes.shape(a.home_shape)
    if a.token_file:
        hub = Hub.from_token_file(a.hub, Path(a.token_file))
    elif a.code:
        hub = Hub(a.hub)
        hub.pair(a.code, "Zero E2E assertions")
    else:
        ap.error("give --token-file or --code")
    rep = Report()
    seen = check_api(hub, persona, rep, home, home_dir, a.expect_mailbox, a.ask_inbox)
    if home_dir:
        check_files(home_dir, persona, rep, home, a.expect_mailbox if seen.get("wp1") else
                    ("skip" if a.expect_mailbox else False))
    n = {s: sum(1 for i in rep.items if i["status"] == s) for s in ("PASS", "FAIL", "WARN", "SKIP")}
    print(f"\n{n['PASS']} passed, {n['FAIL']} failed, {n['WARN']} warnings, {n['SKIP']} skipped")
    if a.json:
        Path(a.json).write_text(json.dumps({"counts": n, "items": rep.items}, indent=1))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
