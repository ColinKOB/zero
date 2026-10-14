"""Proves one home cannot read another's files, mail, logins, cards or chats (plan section 4, "Isolation, Verified").

Run it as one home's user with the other home as the target ("victim"). On alien, for the test home:

  # as zero-test: can the test home reach Colin's things?
  python3 isolation_probe.py --victim-home /home/jdoe --victim-hub 8650 --victim-hermes 8642 \
      --victim-uid 1000 --tally 18930
  # as jdoe: can Colin's user reach the test home's? (the reverse direction zero-home-check also runs)
  python3 isolation_probe.py --victim-home /home/zero-test --victim-hub 8652 --victim-hermes 8644

Every probe only asks whether something can be opened, listed or answered. It reads at most one byte of a file and
never prints, keeps or sends what it read; the report names paths and HTTP statuses only. HTTP probes are GETs with
no credentials. Standard library only, Python 3.8 or newer.

Each line is `<STATUS> <category>/<check>: <detail>` with STATUS one of PASS (refused, as it must be), FAIL (it
could be read or answered), INFO (not present on this host, so nothing to prove). Exit status: 0 when nothing
failed, 1 when something leaked, 2 when the probe was run wrongly (for example as the victim's own user, where
every read would succeed and prove nothing). `--json FILE` writes the same results for zero-home-check and
zero-e2e.sh. This line format and exit-code contract is the one assumed for WP3's zero-home-check.

`--self-test` proves the probe itself on any machine (the Mac, in dry mode): it builds a fake victim home in a
temporary folder, checks that a locked home (mode 000, a 401 hub) gives only PASS, then unlocks it and checks that
the same probe now reports FAIL for every category. A probe that cannot see a leak would fail its self-test.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ZIPS = ("school", "builder", "clients", "money", "career", "inbox", "errands")

TARGETS = [
    ("files", "home_listing", "."),
    ("files", "work", "taffy/work"),
    ("files", "shared_notes", "taffy/shared"),
    ("files", "soul", ".taffy/SOUL.md"),
    ("files", "user_notes", ".taffy/memories/USER.md"),
    ("files", "home_settings", "taffy/home.json"),
    ("files", "ssh_keys", ".ssh"),
    ("files", "backups", "backups"),
    ("mail", "himalaya_config", ".config/himalaya"),
    ("mail", "himalaya_toml", ".config/himalaya/config.toml"),
    ("mail", "maildir", "Mail"),
    ("mail", "mail_cache", ".local/share/himalaya"),
    ("logins", "lead_vault", ".taffy/vault"),
    ("logins", "zip_vault", ".taffy/profiles/*/vault"),
    ("logins", "claude_sign_in", ".claude/.credentials.json"),
    ("logins", "codex_sign_in", ".codex/auth.json"),
    ("logins", "hermes_auth", ".taffy/auth.json"),
    ("logins", "hermes_env", ".taffy/.env"),
    ("logins", "push_key", ".config/taffy-push"),
    ("logins", "backup_password", ".config/taffy-backup"),
    ("logins", "hub_internal_key", "taffy/hub-state/internal-key"),
    ("cards", "consent_db", ".taffy/consent.db"),
    ("cards", "hub_db", "taffy/hub-state/hub.db"),
    ("cards", "kanban_db", ".taffy/kanban.db"),
    ("cards", "tally_env", "tally/server/.env"),
    ("cards", "tally_data", "tally"),
    ("chats", "lead_sessions", ".taffy/state.db"),
    ("chats", "lead_session_files", ".taffy/sessions"),
    ("chats", "zip_sessions", ".taffy/profiles/*/state.db"),
    ("chats", "calls", "taffy/hub-state/calls"),
    ("chats", "media", "taffy/hub-state/media"),
]

HUB_GETS = [("cards", "hub_feed", "/api/feed"), ("chats", "hub_lead_chat", "/api/bots/default/messages"),
            ("logins", "hub_logins", "/api/logins"), ("files", "hub_me", "/api/me"),
            ("cards", "hub_signin_cards", "/api/signin-requests")]
HERMES_GETS = [("chats", "hermes_sessions", "/api/sessions"), ("chats", "hermes_models", "/v1/models")]


class Results:
    def __init__(self, quiet: bool = False):
        self.items: list[dict] = []
        self.quiet = quiet

    def add(self, status: str, category: str, check: str, detail: str) -> None:
        self.items.append({"status": status, "category": category, "check": check, "detail": detail})
        if not self.quiet:
            print(f"{status:<4} {category}/{check}: {detail}", flush=True)

    def count(self, status: str) -> int:
        return sum(1 for i in self.items if i["status"] == status)


def _expand(rel: str) -> list[str]:
    return [rel.replace("*", z) for z in ZIPS] if "*" in rel else [rel]


def probe_path(path: Path) -> str:
    """'denied', 'absent' or 'readable'. Never returns content."""
    try:
        if path.is_dir():
            os.listdir(path)
            return "readable"
        with open(path, "rb") as f:
            f.read(1)
        return "readable"
    except PermissionError:
        return "denied"
    except FileNotFoundError:
        # is_dir() and open() both say "not found" when a parent cannot be searched on some systems; ask the parent.
        parent = path.parent
        while parent != parent.parent:
            try:
                os.stat(parent)
            except PermissionError:
                return "denied"
            except FileNotFoundError:
                parent = parent.parent
                continue
            break
        return "absent"
    except OSError as e:
        return "denied" if e.errno in (errno.EACCES, errno.EPERM) else "absent"


def probe_files(victim: Path, res: Results) -> None:
    for category, check, rel in TARGETS:
        states = {r: probe_path(victim / r) for r in _expand(rel)}
        readable = [r for r, s in states.items() if s == "readable"]
        denied = [r for r, s in states.items() if s == "denied"]
        if readable:
            res.add("FAIL", category, check, f"readable: {', '.join(str(victim / r) for r in readable)}")
        elif denied:
            res.add("PASS", category, check, f"refused: {victim / denied[0]}" + (f" (+{len(denied) - 1})" if len(denied) > 1 else ""))
        else:
            res.add("INFO", category, check, f"not present: {victim / rel}")


def http_status(port: int, path: str, timeout: float = 5) -> int | str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError) as e:
        return f"unreachable ({getattr(e, 'reason', e)})"


def probe_http(port: int | None, gets: list, what: str, res: Results) -> None:
    if not port:
        return
    for category, check, path in gets:
        st = http_status(port, path)
        if st == 200:
            res.add("FAIL", category, check, f"{what} on 127.0.0.1:{port} answered {path} without a credential")
        elif isinstance(st, int):
            res.add("PASS", category, check, f"{what} {path} answered {st}")
        else:
            res.add("PASS", category, check, f"{what} {path} {st}")


def probe_socket(path: str | None, res: Results) -> None:
    if not path:
        return
    if not os.path.exists(path):
        try:
            os.stat(os.path.dirname(path))
        except PermissionError:
            res.add("PASS", "files", "docker_socket", f"refused: {path}")
            return
        res.add("INFO", "files", "docker_socket", f"not present: {path}")
        return
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        res.add("FAIL", "files", "docker_socket", f"connected to {path}")
    except PermissionError:
        res.add("PASS", "files", "docker_socket", f"refused: {path}")
    except OSError as e:
        res.add("PASS" if e.errno in (errno.EACCES, errno.EPERM) else "INFO", "files", "docker_socket", f"{path}: {e.strerror}")
    finally:
        s.close()


def probe_procs(uid: int | None, res: Results) -> None:
    """Another user's processes: their environment (where keys live) and working folder must be closed."""
    if uid is None or not os.path.isdir("/proc"):
        return
    leaked, checked = [], 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
        except OSError:
            continue
        checked += 1
        for leaf in ("environ", "cwd/."):
            try:
                if leaf == "environ":
                    with open(f"/proc/{pid}/environ", "rb") as f:
                        f.read(1)
                else:
                    os.listdir(f"/proc/{pid}/cwd")
                leaked.append(f"{pid}/{leaf.split('/')[0]}")
            except OSError:
                pass
        if checked >= 200:
            break
    if not checked:
        res.add("INFO", "logins", "process_env", f"no processes of uid {uid}")
    elif leaked:
        res.add("FAIL", "logins", "process_env", f"readable: /proc/{', /proc/'.join(leaked[:5])}")
    else:
        res.add("PASS", "logins", "process_env", f"environment and folder of {checked} processes of uid {uid} refused")


def run(args, res: Results) -> None:
    if not getattr(args, "network_only", False):
        probe_files(Path(args.victim_home), res)
    probe_http(args.victim_hub, HUB_GETS, "the other home's hub", res)
    probe_http(args.victim_hermes, HERMES_GETS, "the other home's Hermes API", res)
    if args.tally:
        probe_http(args.tally, [("cards", "tally_api", "/api/transactions"), ("cards", "tally_accounts", "/api/accounts")],
                   "Tally", res)
    if getattr(args, "network_only", False):
        return
    probe_socket(args.victim_docker_sock or (f"/run/user/{args.victim_uid}/docker.sock" if args.victim_uid is not None else None), res)
    probe_procs(args.victim_uid, res)


# MARK: Self-test

def _fake_victim(root: Path) -> None:
    for _, _, rel in TARGETS:
        for r in _expand(rel):
            p = root / r
            if r == ".":
                continue
            if Path(r).suffix or Path(r).name in ("internal-key", "config.toml", ".env"):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"fixture, not a secret\n")
            else:
                p.mkdir(parents=True, exist_ok=True)


class _Hub(BaseHTTPRequestHandler):
    locked = True

    def do_GET(self):
        self.send_response(401 if _Hub.locked else 200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="zero-isolation-selftest-"))
    victim = tmp / "victim"
    victim.mkdir()
    _fake_victim(victim)
    srv = HTTPServer(("127.0.0.1", 0), _Hub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    ns = argparse.Namespace(victim_home=str(victim), victim_hub=port, victim_hermes=port, tally=None,
                            victim_docker_sock=None, victim_uid=None)
    ok = True
    try:
        if os.geteuid() == 0:
            print("self-test: run as a normal user; root can read a mode-000 folder")
            return 2
        os.chmod(victim, 0o000)
        _Hub.locked = True
        locked = Results(quiet=True)
        run(ns, locked)
        bad = [i for i in locked.items if i["status"] != "PASS"]
        print(f"self-test, locked home: {locked.count('PASS')} refused, {len(bad)} not refused")
        for i in bad:
            print(f"  unexpected {i['status']} {i['category']}/{i['check']}: {i['detail']}")
        ok &= not bad
        os.chmod(victim, 0o755)
        _Hub.locked = False
        unlocked = Results(quiet=True)
        run(ns, unlocked)
        cats = {i["category"] for i in unlocked.items if i["status"] == "FAIL"}
        missed = [i for i in unlocked.items if i["status"] == "PASS"]
        print(f"self-test, open home: {unlocked.count('FAIL')} leaks seen in {', '.join(sorted(cats))}; "
              f"{len(missed)} missed")
        for i in missed:
            print(f"  missed {i['category']}/{i['check']}: {i['detail']}")
        ok &= cats >= {"files", "mail", "logins", "cards", "chats"} and not missed
    finally:
        os.chmod(victim, 0o755)
        srv.shutdown()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("self-test " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Can this user read the other home's files, mail, logins, cards or chats?")
    ap.add_argument("--victim-home", help="the other home's directory, e.g. /home/jdoe")
    ap.add_argument("--victim-hub", type=int, help="the other home's hub port on loopback")
    ap.add_argument("--victim-hermes", type=int, help="the other home's Hermes API port on loopback")
    ap.add_argument("--tally", type=int, help="Tally's port, when the other home has it")
    ap.add_argument("--victim-uid", type=int, help="the other home's uid (process and Docker socket checks)")
    ap.add_argument("--victim-docker-sock")
    ap.add_argument("--json")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--network-only", action="store_true",
                    help="only the loopback HTTP probes (safe to run as any user, e.g. against Colin's live hub)")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.network_only:
        res = Results()
        run(a, res)
        print(f"\n{res.count('PASS')} refused, {res.count('FAIL')} leaked")
        if a.json:
            Path(a.json).write_text(json.dumps({"items": res.items}, indent=1))
        return 1 if res.count("FAIL") else 0
    if not a.victim_home:
        ap.error("--victim-home is required")
    try:
        owner = os.stat(a.victim_home).st_uid
    except PermissionError:
        owner = None
    except FileNotFoundError:
        print(f"{a.victim_home} does not exist", file=sys.stderr)
        return 2
    if owner is not None and (owner == os.getuid() or os.geteuid() == 0):
        print("Run this as the other home's user. As the owner or as root every read succeeds and proves nothing.",
              file=sys.stderr)
        return 2
    if a.victim_uid is None and owner is not None:
        a.victim_uid = owner
    res = Results()
    run(a, res)
    n = {s: res.count(s) for s in ("PASS", "FAIL", "INFO")}
    print(f"\n{n['PASS']} refused, {n['FAIL']} leaked, {n['INFO']} not present")
    if a.json:
        Path(a.json).write_text(json.dumps({"counts": n, "items": res.items, "uid": os.getuid()}, indent=1))
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
