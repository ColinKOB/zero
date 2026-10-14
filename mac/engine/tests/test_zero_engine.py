"""Tests for mac/engine/zero_engine.py: the home it renders for each AI sign-in, with nothing of Colin's in it.

Run with any Python that has ruamel.yaml (the engine's own venv has it):
    <venv>/bin/python mac/engine/tests/test_zero_engine.py
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("zero_engine", REPO / "mac" / "engine" / "zero_engine.py")
ze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ze)


def ai(claude=False, codex=False):
    return {"claude": {"installed": True, "signed_in": claude, "path": "/opt/x/claude"},
            "codex": {"installed": True, "signed_in": codex, "path": "/opt/x/codex"},
            "ready": claude or codex}


class Args:
    def __init__(self, root, work):
        self.root, self.work, self.label, self.agents = str(root), str(work), "tech.zerotest.unit", str(root / "agents")


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zero-engine-test-"))
        root = self.tmp / "Zero"
        (root / "engine" / "plugins").mkdir(parents=True)
        shutil.copytree(REPO / "alien" / "plugins" / "taffy-tools", root / "engine" / "plugins" / "taffy-tools")
        (root / "engine" / "lib").mkdir()
        for f in ("zero-home-lib.py", "set-toolsets.py"):
            shutil.copy(REPO / "alien" / "bin" / f, root / "engine" / "lib" / f)
        shutil.copytree(REPO / "alien" / "bin" / "zero-home-templates", root / "engine" / "lib" / "zero-home-templates")
        self.ctx = ze.Ctx(Args(root, self.tmp / "Zero Work"))
        self.ctx.hermes_home.mkdir(parents=True)
        os.environ["ZERO_HOME"] = str(self.ctx.settings_file)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, a):
        L = ze.lib(self.ctx)
        settings = ze.settings_for(self.ctx, {"hub": 8650, "hermes_api": 8642}, a, {})
        self.ctx.settings_file.write_text(json.dumps(settings))
        ze.render_config(self.ctx, L, settings, a)
        return settings, L.load_yaml((self.ctx.hermes_home / "config.yaml").read_text())

    def test_claude_plan(self):
        settings, cfg = self.render(ai(claude=True))
        self.assertEqual(settings["home_id"], "mac")
        self.assertEqual(settings["host"]["kind"], "mac")
        self.assertEqual(settings["plan"]["connectors"], "claude")
        self.assertEqual(settings["plan"]["claude"]["command"], "/opt/x/claude")
        self.assertEqual(cfg["model"]["provider"], "claude-subscription-directsdk-experimental")
        self.assertEqual(cfg["fallback_providers"], [])

    def test_claude_and_chatgpt(self):
        _, cfg = self.render(ai(claude=True, codex=True))
        self.assertEqual(cfg["fallback_providers"], [{"provider": "openai-codex", "model": "gpt-6.1-sol"}])

    def test_chatgpt_only(self):
        settings, cfg = self.render(ai(codex=True))
        self.assertEqual(settings["plan"]["connectors"], "chatgpt")
        self.assertEqual(cfg["model"]["provider"], "openai-codex")
        self.assertNotIn("auxiliary", cfg)

    def test_unsafe_tools_off_and_paths_absolute(self):
        settings, cfg = self.render(ai(claude=True))
        for platform, tools in cfg["platform_toolsets"].items():
            for off in ("terminal", "code_execution", "computer_use", "browser"):
                self.assertNotIn(off, tools, platform)
            self.assertIn("file", tools)
            self.assertIn("taffy", tools)
        self.assertNotIn("bot_desktop", cfg)
        for key, value in settings["paths"].items():
            if isinstance(value, str):
                self.assertTrue(value.startswith("/"), key)

    def test_nothing_of_colins(self):
        settings, _ = self.render(ai(claude=True))
        text = json.dumps(settings)
        for word in ("alien", "jdoe", "tail0000", "AbCd1234", "Lakeview", "18930"):
            self.assertNotIn(word, text)
        self.assertIsNone(settings["integrations"]["tally"])
        self.assertIsNone(settings["integrations"]["trello"])
        self.assertFalse(settings["judge"]["enabled"])

    def test_person_kept_on_rerender(self):
        first = ze.settings_for(self.ctx, {"hub": 8650, "hermes_api": 8642}, ai(claude=True), {})
        first["person"]["name"] = "Sam Rivera"
        first["person"]["first_name"] = "Sam"
        first["mail"] = {"accounts": [{"id": "x"}]}
        again = ze.settings_for(self.ctx, {"hub": 8651, "hermes_api": 8643}, ai(codex=True), first)
        self.assertEqual(again["person"]["first_name"], "Sam")
        self.assertEqual(again["mail"], {"accounts": [{"id": "x"}]})
        self.assertEqual(again["ports"]["hub"], 8651)
        self.assertEqual(again["plan"]["connectors"], "chatgpt")


class SignInTest(unittest.TestCase):
    """zero-engine sign-in with a stand-in Claude Code in a scratch home: no download, no browser, no real sign-in."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zero-sign-in-test-"))
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        # Opens its page with `open`, as Claude Code does, then finishes after a moment (or waits, for the stop test).
        (bin_dir / "claude").write_text(
            '#!/bin/sh\nopen "https://claude.com/cai/oauth/authorize?test=1"\n'
            'sleep "${FAKE_WAIT:-1}"\necho "Login successful."\n')
        (bin_dir / "claude").chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def engine(self, *args, wait="1"):
        import subprocess
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "ZERO_ROOT": str(self.home / "Zero"),
               "ZERO_SIGN_IN_OPEN": "/usr/bin/true", "FAKE_WAIT": wait}
        return subprocess.Popen(["bash", str(REPO / "mac" / "engine" / "zero-engine"), *args], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_signs_in_and_reports_the_page(self):
        out, _ = self.engine("sign-in", "claude").communicate(timeout=30)
        self.assertIn("ZERO-STEP 2 2 Sign in on the page that opened in your browser.", out)
        self.assertIn("ZERO-URL https://claude.com/cai/oauth/authorize?test=1", out)
        self.assertTrue(out.rstrip().endswith("ZERO-DONE"), out)
        self.assertNotIn("Login successful", out)

    def test_stop_ends_quietly(self):
        import time
        p = self.engine("sign-in", "claude", wait="30")
        time.sleep(3)
        self.engine("sign-in-stop").communicate(timeout=30)
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 3, out)
        self.assertNotIn("ZERO-ERROR", out)

    def test_refuses_other_tools(self):
        out, _ = self.engine("sign-in", "bash").communicate(timeout=30)
        self.assertIn("usage: zero-engine sign-in claude|codex", out)


class LastJSONShape(unittest.TestCase):
    def test_brain(self):
        self.assertEqual(ze.brain(ai()), "none")
        self.assertEqual(ze.brain(ai(codex=True)), "chatgpt")
        self.assertEqual(ze.brain(ai(claude=True, codex=True)), "claude")


if __name__ == "__main__":
    unittest.main(verbosity=2)
