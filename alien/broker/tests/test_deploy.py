"""zero-home-deploy: what deploy.sh <home> calls as the home's user. It refuses the owner and root, any folder
outside /tmp/zero-deploy-*, symlinks, and a folder that does not hold the code it is told to deploy."""

import importlib.util
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parents[2] / "bin"


def load(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


deploy = load("zero_home_deploy_t", BIN / "zero-home-deploy")
REGISTRY = {"homes": {"colin": {"user": "jdoe", "owner": True},
                      "riley": {"user": "zero-riley"}, "test": {"user": "zero-test"}}}


class Stage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zero-deploy-hub-t", dir="/tmp"))
        (self.tmp / "taffy_hub").mkdir()
        for f in deploy.MARKERS["hub"]:
            (self.tmp / f).write_text("")

    def test_a_real_staged_hub_passes(self):
        self.assertEqual(deploy.check_stage("hub", self.tmp), self.tmp.resolve())

    def test_outside_the_prefix_is_refused(self):
        for bad in ("/home/zero-riley/taffy/hub-src", "/tmp/other", "/tmp/zero-deploy-x/../../etc"):
            with self.assertRaises(SystemExit):
                deploy.check_stage("hub", Path(bad))

    def test_the_wrong_code_is_refused(self):
        with self.assertRaises(SystemExit):
            deploy.check_stage("plugin", self.tmp)

    def test_a_symlink_is_refused(self):
        link = Path(str(self.tmp) + "-link")
        link.symlink_to(self.tmp)
        self.addCleanup(link.unlink)
        with self.assertRaises(SystemExit):
            deploy.check_stage("hub", link)


class Who(unittest.TestCase):
    def test_home_comes_from_the_running_user(self):
        with mock.patch.object(deploy.lib, "current_user", return_value="zero-riley"):
            self.assertEqual(deploy.home_of_running_user(REGISTRY), "riley")

    def test_the_owner_is_refused(self):
        with mock.patch.object(deploy.lib, "current_user", return_value="jdoe"):
            with self.assertRaises(SystemExit):
                deploy.home_of_running_user(REGISTRY)

    def test_sudoers_allows_deploy_only_as_a_home(self):
        prep = sys.modules.get("zero_home_sudo_prep_t") or load("zero_home_sudo_prep_d", BIN / "zero-home-sudo-prep")
        text = prep.SUDOERS.format(owner="jdoe")
        self.assertIn("/srv/zero/bin/zero-home-deploy", text)
        self.assertIn("ALL=(%zero-homes) NOPASSWD:", text)
        self.assertIn("zero-home-deploy", prep.HOME_SCRIPTS)


if __name__ == "__main__":
    unittest.main()
