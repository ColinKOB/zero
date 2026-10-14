"""Teardown and root-step guards: only the test home is ever torn down without a separate, exact flag, and
the owner's home never is."""

import importlib.util
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / "bin"


def load(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


test_script = load("zero_home_test_t", BIN / "zero-home-test")
prep = load("zero_home_sudo_prep_t", BIN / "zero-home-sudo-prep")


class Args:
    def __init__(self, home, confirm=""):
        self.home = home
        self.confirm_delete_home = confirm
        self.dry_run = True


class TeardownGuards(unittest.TestCase):
    def test_only_test_without_flag(self):
        test_script.refuse_unless_test("test", "")
        with self.assertRaises(SystemExit):
            test_script.refuse_unless_test("riley", "")
        with self.assertRaises(SystemExit):
            test_script.refuse_unless_test("riley", "test")
        test_script.refuse_unless_test("riley", "riley")

    def test_owner_always_refused(self):
        with self.assertRaises(SystemExit):
            test_script.refuse_unless_test("colin", "colin")

    def test_cli_refuses_before_doing_anything(self):
        with self.assertRaises(SystemExit) as cm:
            test_script.main(["down", "--home", "riley"])
        self.assertIn("Refusing", str(cm.exception))
        with self.assertRaises(SystemExit):
            test_script.main(["reset", "--home", "riley", "--confirm-delete-home", "dana"])

    def test_remove_home_guards(self):
        ops = prep.lib.Ops(dry_run=True, quiet=True)
        with self.assertRaises(SystemExit):
            prep.remove_home(ops, Args("colin", "colin"))
        with self.assertRaises(SystemExit):
            prep.remove_home(ops, Args("riley"))
        self.assertEqual(ops.changes, [])


class RootSteps(unittest.TestCase):
    def test_next_subid_start(self):
        self.assertEqual(prep.next_subid_start([("jdoe", 100000, 65536)]), 165536)
        self.assertEqual(prep.next_subid_start([("jdoe", 100000, 65536), ("zero-riley", 165536, 65536)]), 231072)
        self.assertEqual(prep.next_subid_start([]), 100000)

    def test_sudoers_is_narrow(self):
        text = prep.SUDOERS.format(owner="jdoe")
        rule = [l for l in text.splitlines() if l.startswith("jdoe")][0]
        self.assertIn("(%zero-homes)", rule)
        self.assertNotIn("ALL=(ALL", rule)
        self.assertEqual(rule.count("/srv/zero/bin/zero-home-"), 5)
        self.assertNotIn("sudo-prep", rule)

    def test_every_step_has_a_flag(self):
        names = {n for n, _ in prep.HOST_STEPS} | {n for n, _ in prep.HOME_STEPS}
        self.assertTrue({"groups", "srv", "scripts", "engine", "image", "apparmor", "sudoers", "broker", "registry",
                         "user", "subid", "linger", "backup_drop", "serve", "push_key"} <= names)


if __name__ == "__main__":
    unittest.main()
