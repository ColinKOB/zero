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


class LastJSONShape(unittest.TestCase):
    def test_brain(self):
        self.assertEqual(ze.brain(ai()), "none")
        self.assertEqual(ze.brain(ai(codex=True)), "chatgpt")
        self.assertEqual(ze.brain(ai(claude=True, codex=True)), "claude")


if __name__ == "__main__":
    unittest.main(verbosity=2)
