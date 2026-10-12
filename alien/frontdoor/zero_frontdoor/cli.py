"""zero-account: the owner's command line for front door accounts. Run it on alien as the owner.

    zero-account attach HOME_ID USERNAME     give an existing home (colin, riley, ...) a username and a password,
                                             asked for twice and never shown or stored in plain text
    zero-account passwd USERNAME             a new password for an account
    zero-account list                        every account, its home, plan and state
    zero-account set-plan USERNAME PLAN      for billing and tests (plans.py)
    zero-account invite USERNAME [--hours 72]
                                             let one username sign up in the app while sign-up is closed (the default)
    zero-account invites                     the open invites
    zero-account remove USERNAME [--delete-home]
                                             forget the account; --delete-home also removes its home and all its data,
                                             only for homes the front door made (u-* and qa-*)

--password-stdin reads the password from the first line of standard input instead of asking (for scripts).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import homes, plans, store


def _password(a, username: str) -> str:
    if a.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Password: ")
        if getpass.getpass("Same password again: ") != pw:
            raise SystemExit("The two passwords are different. Nothing changed.")
    problem = store.password_problem(username, pw)
    if problem:
        raise SystemExit(problem)
    return pw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zero-account", description="Front door accounts for Zero homes.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    at = sub.add_parser("attach")
    at.add_argument("home_id")
    at.add_argument("username")
    pw = sub.add_parser("passwd")
    pw.add_argument("username")
    sub.add_parser("list")
    sp = sub.add_parser("set-plan")
    sp.add_argument("username")
    sp.add_argument("plan")
    inv = sub.add_parser("invite")
    inv.add_argument("username")
    inv.add_argument("--hours", type=float, default=72)
    sub.add_parser("invites")
    rm = sub.add_parser("remove")
    rm.add_argument("username")
    rm.add_argument("--delete-home", action="store_true")
    for p in (at, pw):
        p.add_argument("--password-stdin", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "attach":
        live = homes.LiveHomes()
        if a.home_id not in live.registry()["homes"]:
            raise SystemExit(f"{a.home_id} is not a home in the registry.")
        if store.by_home(a.home_id):
            raise SystemExit(f"{a.home_id} already has an account ({store.by_home(a.home_id)['username']}).")
        problem = store.username_problem(a.username)
        if problem:
            raise SystemExit(problem)
        if store.get(a.username):
            raise SystemExit("That username is taken.")
        store.add(a.username, _password(a, a.username), a.home_id)
        print(f"{store.normalize_username(a.username)} now signs in to the home {a.home_id}.")
    elif a.cmd == "passwd":
        if not store.get(a.username):
            raise SystemExit("No such account.")
        store.set_password(a.username, _password(a, a.username))
        print("Password changed.")
    elif a.cmd == "list":
        for r in store.all_accounts():
            print(f"{r['username']:<24} {r['home_id']:<18} {r['plan']:<8} {r['state']}")
    elif a.cmd == "invite":
        problem = store.username_problem(a.username)
        if problem:
            raise SystemExit(problem)
        if store.get(a.username):
            raise SystemExit("That username already has an account.")
        inv = store.add_invite(a.username, a.hours)
        print(f"{inv['username']} may create an account in the app for the next {a.hours:g} hours, once.")
    elif a.cmd == "invites":
        import time as _t
        for r in store.all_invites():
            left = (r["expires_at"] - _t.time()) / 3600
            print(f"{r['username']:<24} {'expired' if left <= 0 else f'{left:.0f} hours left'}")
    elif a.cmd == "set-plan":
        print(plans.set_plan(a.username, a.plan))
    elif a.cmd == "remove":
        acct = store.get(a.username)
        if not acct:
            raise SystemExit("No such account.")
        if a.delete_home:
            from_door = homes.load_lib().ACCOUNT_HOME_RE.match(acct["home_id"])
            if not from_door:
                raise SystemExit(f"{acct['home_id']} was not made by the front door; only the account can be removed.")
            homes.LiveHomes().remove(acct["home_id"])
            print(f"Removed the home {acct['home_id']}.")
        store.remove(a.username)
        print(f"Removed the account {acct['username']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
