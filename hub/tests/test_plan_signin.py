"""Sign In With Claude from the app (plansignin.py), against a stand-in `claude` script: nothing live is reached.

Run: python3 -m unittest discover -s hub/tests (from the repo), or on alien in a copy:
~/taffy/.venv/bin/python -m unittest test_plan_signin
"""

import asyncio
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import accounts, home, plansignin  # noqa: E402

FAKE = textwrap.dedent('''\
    #!{python}
    import json, os, sys
    marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signed-in")
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        if os.path.exists(marker):
            print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "email": "sam@example.com"}}))
        else:
            print(json.dumps({{"loggedIn": False}}))
        sys.exit(0)
    if args[:2] == ["auth", "login"]:
        print("Opening browser to sign in...")
        print("If the browser didn't open, visit: \\x1b]8;;https://claude.com/cai/oauth/authorize?code=true&state=abc\\x1b\\\\"
              "https://claude.com/cai/oauth/authorize?code=true&state=abc\\x1b]8;;\\x1b\\\\")
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        code = sys.stdin.readline().strip()
        if code == "goodcode-123#abc":
            open(marker, "w").write("1")
            print("Login successful.")
            sys.exit(0)
        print("Invalid code")
        sys.exit(1)
    sys.exit(2)
''')


class PlanSignIn(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.exe = self.tmp / "claude"
        self.exe.write_text(FAKE.format(python=sys.executable))
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR)
        self.saved = (accounts.claude_bin, home.plan_kind, accounts._chatgpt, accounts._last)
        accounts.claude_bin = lambda: str(self.exe)
        home.plan_kind = lambda: "own"

    def tearDown(self):
        plansignin.cancel()
        accounts.claude_bin, home.plan_kind, accounts._chatgpt, accounts._last = self.saved

    def test_start_returns_the_page_without_terminal_codes(self):
        url = plansignin.start()
        self.assertEqual(url, "https://claude.com/cai/oauth/authorize?code=true&state=abc")

    def test_good_code_signs_in(self):
        plansignin.start()
        r = plansignin.finish("goodcode-123#abc")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["account"], "sam@example.com")
        self.assertEqual(plansignin.check()["state"], "signed_in")

    def test_wrong_code_says_so(self):
        plansignin.start()
        r = plansignin.finish("wrongcode-999")
        self.assertFalse(r["ok"])
        self.assertIn("did not work", r["detail"])
        self.assertFalse((self.tmp / "signed-in").exists())

    def test_code_without_a_running_sign_in(self):
        with self.assertRaises(plansignin.SignInError):
            plansignin.finish("goodcode-123#abc")

    def test_junk_code_is_refused_before_claude_sees_it(self):
        plansignin.start()
        with self.assertRaises(plansignin.SignInError):
            plansignin.finish("short")
        with self.assertRaises(plansignin.SignInError):
            plansignin.finish("has spaces in it\nand a newline")

    def test_a_shared_plan_never_signs_in(self):
        home.plan_kind = lambda: "shared"
        with self.assertRaises(plansignin.SignInError):
            plansignin.start()

    def test_own_home_without_connectors_reports_its_own_plan(self):
        async def no_codex():
            return {"state": "missing", "apps": {}}
        accounts._chatgpt = no_codex
        saved_run = accounts._run

        async def run(*args):
            return 0, '{"loggedIn": true, "authMethod": "claude.ai", "email": "sam@example.com"}'
        accounts._run = run
        try:
            r = asyncio.run(accounts.own_payload(1.0))
        finally:
            accounts._run = saved_run
        self.assertEqual(r["plan"], "claude")
        self.assertEqual(r["claude"]["state"], "signed_in")
        self.assertEqual(r["connectors"], "none")
        self.assertTrue(all(s["state"] == accounts.NEEDS_OWN_PLAN for s in r["services"]))


if __name__ == "__main__":
    unittest.main()
