"""QA round 10, L4 (docs/qa/2026-10-08-call-fixes.md): the saved calls and the zero-calls command are protected
from the Zips that run on alien. A Zip's terminal, code and file tools cannot name them. Colin and the lead
session read them over ssh, which the gate never sees.

Run: python3 -m unittest discover -s hub/tests
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, tt  # noqa: E402

safe_files = tt.safe_files
HOME = "/home/jdoe"
CALLS = f"{HOME}/taffy/hub-state/calls"
CMD = f"{HOME}/taffy/bin/zero-calls"

TERMINAL = [
    "~/taffy/bin/zero-calls show last",
    "zero-calls list --days 30",
    "python3 ~/taffy/bin/zero-calls list --days 30",
    f"{CMD} show last",
    "cd ~/taffy/bin && ./zero-calls show last",
    "cd ~/taffy/bin; python3 zero-calls show 3f2a",
    "bash -c 'zero-calls show last'",
    "ZERO_CALLS_DIR=/tmp/x zero-calls list",
    "cat ~/taffy/bin/zero-calls",
    "cp ~/taffy/bin/zero-calls /tmp/z && python3 /tmp/z show last",
    "cat ~/taffy/hub-state/calls/2026-10-08/abc.jsonl",
    f"cat {CALLS}/2026-10-08/abc.jsonl",
    f"ls {CALLS}",
    "ls ~/taffy/hub-state/calls/2026-10-08",
    "grep -r money ~/taffy/hub-state/calls",
    "cd ~/taffy/hub-state/calls && cat */*.jsonl",
    "tar czf /tmp/c.tgz -C ~/taffy hub-state/calls",
    "rsync -a ~/taffy/hub-state/calls /tmp/c",
]
CODE = [
    "import subprocess\nprint(subprocess.run(['/home/jdoe/taffy/bin/zero-calls', 'show', 'last'], capture_output=True).stdout)",
    "import subprocess\nsubprocess.run('zero-calls list', shell=True)",
    "import os\nos.system('python3 ~/taffy/bin/zero-calls show last')",
    "print(open('/home/jdoe/taffy/hub-state/calls/2026-10-08/abc.jsonl').read())",
    "from pathlib import Path\nprint(list(Path.home().joinpath('taffy/hub-state/calls').iterdir()))",
    "import glob\nprint(glob.glob('/home/jdoe/taffy/hub-state/calls/*/*.jsonl'))",
]
FILES = [
    ("read_file", {"path": f"{CALLS}/2026-10-08/abc.jsonl"}),
    ("read_file", {"path": "~/taffy/hub-state/calls/2026-10-08/abc.jsonl"}),
    ("read_file", {"path": CMD}),
    ("search_files", {"pattern": "money", "path": CALLS}),
    ("search_files", {"pattern": "call", "path": f"{HOME}/taffy/bin/zero-calls"}),
    ("write_file", {"path": f"{CALLS}/2026-10-08/abc.jsonl", "content": "x"}),
    ("write_file", {"path": CMD, "content": "#!/bin/sh\ncat ~/x"}),
    ("patch", {"path": CMD, "patch": "x"}),
]


class ZipsCannotReadSavedCalls(GateCase):
    bot = "builder"

    def blocked(self, tool, args):
        out = self.call(tool, args)
        self.assertIsNotNone(out, f"{tool} {args} was allowed")
        self.assertEqual(out["action"], "block")
        self.assertIn("protected location", out["message"])
        self.assertEqual(self.cards(), [])

    def test_terminal(self):
        for cmd in TERMINAL:
            with self.subTest(cmd=cmd):
                self.blocked("terminal", {"command": cmd})

    def test_code(self):
        for code in CODE:
            with self.subTest(code=code):
                self.blocked("execute_code", {"code": code})

    def test_file_tools(self):
        for tool, args in FILES:
            with self.subTest(tool=tool, args=args):
                self.blocked(tool, args)

    def test_every_zip_with_the_host_terminal_is_covered(self):
        for bot in ("builder", "clients", "default", "money", "career", "school"):
            self.bot = bot
            with self.subTest(bot=bot):
                self.blocked("terminal", {"command": "~/taffy/bin/zero-calls show last"})

    def test_other_commands_in_the_same_folder_are_untouched(self):
        for cmd in ("ls ~/taffy/work/builder", "cat ~/taffy/state/walmart-privacy-watch.json", "git status",
                    "python3 ~/taffy/work/builder/site/build.py"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.call("terminal", {"command": cmd}))

    def test_the_other_tools_that_hand_files_on_refuse_them_too(self):
        tmp = Path(tempfile.mkdtemp())
        calls = tmp / "taffy" / "hub-state" / "calls" / "2026-10-08"
        calls.mkdir(parents=True)
        (calls / "abc.jsonl").write_text('{"event": "line"}\n')
        script = tmp / "taffy" / "bin" / "zero-calls"
        script.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env python3\n")
        work = tmp / "taffy" / "work" / "builder"
        work.mkdir(parents=True)
        (work / "link.jsonl").symlink_to(calls / "abc.jsonl")
        roots = safe_files.zip_roots(tmp, "builder") + (str(tmp / "taffy"),)
        for path in (calls / "abc.jsonl", work / "link.jsonl", script):
            with self.subTest(path=str(path)):
                with self.assertRaises(safe_files.Refused):
                    safe_files.resolve(str(path), roots)
                with self.assertRaises(safe_files.Refused):
                    safe_files.open_file(str(path), roots)
                self.assertIsNone(safe_files.card_text(str(path), 100))
        (work / "innocent").symlink_to(calls)
        with self.assertRaises(safe_files.Refused):
            safe_files.resolve(str(work / "innocent" / "abc.jsonl"), roots)
        self.assertIsNone(safe_files.card_text(str(work / "innocent" / "abc.jsonl"), 100))


class TheCommandStillWorksForColin(unittest.TestCase):
    """The protection is in the gate only. The command and the folder are ordinary files to anyone on ssh."""

    def test_the_command_reads_calls_without_the_gate(self):
        import subprocess
        repo = Path(__file__).resolve().parents[2]
        script = repo / "alien" / "bin" / "zero-calls"
        if not script.exists():
            self.skipTest("alien/ is not in this checkout")
        tmp = Path(tempfile.mkdtemp())
        day = tmp / "2026-10-08"
        day.mkdir()
        (day / "abc123.jsonl").write_text(
            '{"t": "2026-10-08T10:00:00-04:00", "s": 0, "event": "start", "from": "hub", "bot": "money", "name": "Money"}\n')
        out = subprocess.run([sys.executable, str(script), "list", "--days", "30"], capture_output=True, text=True,
                             env={**os.environ, "ZERO_CALLS_DIR": str(tmp)})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("abc123"[:6], out.stdout)


if __name__ == "__main__":
    unittest.main()
