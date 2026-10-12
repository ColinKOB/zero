"""Internals QA round 4 (docs/qa/2026-10-08-internals-qa-round-4.md): the agent check reads only commands that run,
every risky part of a compound command is judged, Hermes's own prompts are answered from the gate's record of the
same command, event titles followed by a place stay events, and the Lows. The judge is always mocked and paid calls
stay off. Hermes is not needed: hermes_detector stands in for its detector with a faithful copy (round 5, M1),
and the terminal's folder is set by each test.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, gate, plugin, tt  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402
from taffy_hub import approvals  # noqa: E402
import hermes_detector  # noqa: E402

tiers = tt.tiers
REPO = Path(__file__).resolve().parents[2]
WORK = "~/taffy/work/builder/site"
HOME_FOLDER = "~/taffy"   # where a Zip's terminal starts


class FolderCase(GateCase):
    """The gate with a known terminal folder and Hermes's own detector (hermes_detector)."""

    bot = "builder"
    cwd = WORK

    def setUp(self):
        super().setUp()
        for name, value in (("_terminal_cwd", lambda task_id: self.cwd),
                            ("_hermes_detect", lambda: hermes_detector.detect)):
            patcher = mock.patch.object(gate, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class AgentCheckReadsOnlyCommands(FolderCase):
    """M1: claude, codex, Hermes and taffy count only where they run, not as words in arguments, patterns, messages,
    file contents or paths."""

    FINE = [
        # The report's cases.
        """grep -rl "claude" src | xargs sed -i 's/claude-haiku/claude-sonnet/'""",
        """find . -name '*.py' | xargs grep -l "codex\"""",
        """git commit -m "codex review fixes" -m "Runs at startup now\"""",
        """sudo grep -r "hermes" /etc/systemd""",
        # More of the same: launchers whose own command is ordinary, and the words as data.
        "tmux new -d -s claude-work 'npm run dev'", "ssh alien 'grep -r claude ~/taffy/work'",
        "watch -n 5 'grep -c codex log.txt'", "screen -dmS codex npm start", "nohup npm run dev > /tmp/claude.log &",
        "systemd-run --user --unit=build npm run build", "sudo -u jdoe grep claude /var/log/syslog",
        "xargs -I{} grep -l claude {}", "echo 'claude -p is the flag' > notes.txt", "printf 'codex exec\\n' >> todo.md",
        "git log --grep codex", "sed -i 's/hermes/zero/g' README.md", "ls ~/taffy", "cat ~/taffy/work/notes/claude.md",
        "rg -n 'taffy' ~/taffy/work", "cd ~/taffy/work/site && npm run build", "python3 -c \"print('claude')\"",
        "docker run --name hermes nginx", "curl -s https://api.anthropic.com/v1/models | grep claude",
        "at now + 1 hour <<< 'echo hermes'", "script -q /dev/null npm test", "echo $(which claude)",
        "git commit -m 'Use subprocess to call the build' -m 'claude review at noon'",
        "cat > run.sh <<'EOF'\n# ask claude -p later\nnpm test\nEOF",
    ]
    CODE_FINE = [
        "import pathlib, subprocess\nroot = pathlib.Path.home() / 'taffy' / 'work' / 'site'\n"
        "subprocess.run(['npm', 'run', 'build'], cwd=root)",
        "import subprocess\nsubprocess.run(['grep', '-rl', 'claude', '.'])",
        "import subprocess\nsubprocess.run(['git', 'status'], cwd='/home/jdoe/taffy')",
        "import subprocess\nMODEL = 'claude-sonnet'\nsubprocess.run(['python3', 'eval.py', '--model', MODEL])",
        "import subprocess\nnames = ['claude', 'codex']\nsubprocess.run(['echo', *names])",
        "const { execSync } = require('child_process')\nexecSync('npm run build', { cwd: '/home/jdoe/taffy/work/site' })",
        "print('run claude -p to test')\nimport asyncio\nasyncio.run(main())",
    ]
    BLOCKED = [
        "su colin -c 'claude -p hi'", "script -qc 'codex exec hi' /dev/null", "systemd-run --user claude -p hi",
        "screen -dmS x claude -p hi", "tmux new-session -d -s x claude -p hi", "tmux send-keys -t x 'claude -p hi' Enter",
        "echo 'claude -p hi' | at now", "echo \"codex exec hi\" | bash", "bash <<'EOF'\nclaude -p hi\nEOF",
        "at now <<< 'claude -p hi'", "watch -n 60 'codex exec hi'", "env -S 'claude -p hi'", "dtach -n /tmp/s claude -p hi",
        "python3 - <<'EOF'\nimport os\nos.system('claude -p hi')\nEOF", "node -e \"require('child_process').execSync('codex exec x')\"",
        "sudo -u jdoe env HOME=/home/jdoe claude -p hi", "cd ~/taffy/work && ~/.local/bin/claude -p hi",
    ]
    CODE_BLOCKED = [
        "import subprocess\nsubprocess.run(['bash', '-c', 'claude -p hi'])",
        "import subprocess\nsubprocess.run('claude -p hi', shell=True)",
        "import subprocess\ncmd = ['codex', 'exec', 'x']\nsubprocess.run(cmd)",
        "import subprocess, shutil\nsubprocess.run([shutil.which('claude'), '-p', 'x'])",
        "import subprocess\nsubprocess.run(['taffy', 'plugins', 'disable', 'x'])",
        "import subprocess\nsubprocess.Popen(['/home/jdoe/taffy/bin/taffy', 'config', 'set', 'x', 'y'])",
        "require('child_process').execSync('claude -p hi')",
        "const { spawn } = require('child_process')\nspawn('codex', ['exec', 'x'])",
        "import os\nos.execvp('claude', ['claude', '-p', 'x'])",
    ]

    def test_ordinary_commands_pass(self):
        for cmd in self.FINE:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd)
                self.assertFalse(a is not None and a.kind == "agent", a and a.summary)
        for code in self.CODE_FINE:
            with self.subTest(code=code):
                a = tiers.classify_command(code, code=True)
                self.assertFalse(a is not None and a.kind == "agent", a and a.summary)

    def test_the_report_cases_run_with_no_card_or_refusal(self):
        for cmd in self.FINE[:4]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.call("terminal", {"command": cmd}))
        for code in self.CODE_FINE[:3]:
            with self.subTest(code=code):
                self.assertIsNone(self.call("execute_code", {"code": code}))
        self.assertEqual(self.cards(), [])

    def test_assistants_started_by_a_launcher_or_code_are_still_refused(self):
        for cmd in self.BLOCKED:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd)
                self.assertEqual(a.kind if a else None, "agent")
        for code in self.CODE_BLOCKED:
            with self.subTest(code=code):
                a = tiers.classify_command(code, code=True)
                self.assertEqual(a.kind if a else None, "agent")
        blocked = self.call("terminal", {"command": "su colin -c 'claude -p hi'"})
        self.assertIn("Starting another assistant", blocked["message"])
        self.assertEqual(self.cards(), [])


class CompoundCommands(FolderCase):
    """M2, in the gate: every risky part of a command is judged and named on its card, not only the first."""

    def test_each_risky_part_counts(self):
        cases = [
            ("git push origin feature && rm -rf ~/Documents/old", "delete",
             "Delete ~/Documents/old, and push to the branch feature"),
            ("rm -rf node_modules && git push --force origin main", "publish", "Push to main, which can publish it"),
            ("git push origin feat && git push origin main", "publish",
             "Push to main, which can publish it, and push to the branch feat"),
            ("rm -rf ~/a && rm -rf ~/b", "delete", "Delete ~/a, and delete ~/b"),
            ("npm run build && netlify deploy --prod --site sugared-heron && rm -rf ~/Downloads/old", "publish",
             "Publish sugared-heron to production on Netlify, and delete ~/Downloads/old"),
            ("gh api -X POST repos/x/y/issues -f title=t && gh api -X DELETE repos/x/y", "publish",
             "Post or change something on GitHub, and delete something on GitHub"),
        ]
        for cmd, kind, summary in cases:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd, cwd=WORK)
                self.assertEqual((a.kind, a.summary), (kind, summary))

    def test_a_send_with_another_risky_part_offers_no_standing_rule(self):
        cmd = ("himalaya message send <<'EOF'\nTo: riley@example.com\nSubject: Hi\n\nHello\nEOF\n"
               "netlify deploy --prod --site sugared-heron")
        a = tiers.classify_command(cmd)
        self.assertEqual((a.kind, a.scope), ("send", ""))
        self.assertIn("publish sugared-heron", a.summary)
        self.assertEqual(tiers.classify_command(cmd.rsplit("\n", 1)[0]).scope, "riley@example.com")

    def test_one_card_covers_the_whole_command_once(self):
        cmd = "git push origin feature && rm -rf ~/Documents/old"
        self.ask("push my branch and clear out the old folder")
        self.assertIsNotNone(self.call("terminal", {"command": cmd}))
        card = self.cards()[0]
        self.assertEqual((card["kind"], card["details"]), ("delete", cmd))
        plugin.decide(card["id"], True)
        self.assertIsNone(self.call("terminal", {"command": cmd}))


class GateRecordsWhatHermesWillAsk(FolderCase):
    """M2 and M3: as it lets a terminal command through, the gate records whether it judged every part Hermes's own
    check would ask about."""

    def record(self, cmd, cwd=WORK, ask=True):
        self.cwd = cwd
        if ask:
            self.ask("tidy up the project")
        self.assertIsNone(self.call("terminal", {"command": cmd}), cmd)
        return plugin.gate_checked(self.bot, cmd)

    def test_a_chain_is_cleared_only_when_every_flagged_part_was_judged(self):
        for cmd in ["rm -rf node_modules && systemctl --user stop taffy-gateway", "rm -rf dist; chmod -R 777 ~",
                    "rm -rf build && systemctl restart nginx", "rm -rf build; chmod +x run.sh; ./run.sh",
                    "sudo -S rm -rf build", "bash -c 'npm run build && systemctl --user restart adguard'"]:
            with self.subTest(cmd=cmd):
                seen = self.record(cmd)
                self.assertEqual(seen["cleared"], 0)
                self.assertNotRegex(seen["flagged"], r"^(recursive delete|shell command)")
        seen = self.record("rm -rf build && git reset --hard origin/main", cwd=HOME_FOLDER)
        self.assertEqual((seen["cleared"], seen["flagged"]), (0, "git reset --hard (destroys uncommitted changes)"))

    def test_routine_work_in_the_zips_own_project_is_cleared(self):
        for cmd, cwd in [("rm -rf node_modules", WORK), ("python3 -c 'print(1)'", WORK),
                         ("bash -c 'npm run build'", WORK), ("find /tmp/build -delete", HOME_FOLDER),
                         ("cd ~/taffy/work/site && git reset --hard origin/feature/nav", HOME_FOLDER),
                         ("git branch -D old-experiment", WORK), ("git branch -d -f old-experiment", WORK),
                         ("docker compose down", WORK), ("docker compose -f docker-compose.yml down -v", WORK),
                         ("npm uninstall left-pad", WORK), ("pnpm remove left-pad", WORK),
                         ("git push --force origin feature/nav", WORK), ("git push -f origin feature/nav", WORK),
                         ("rm -rf build && git reset --hard origin/main", WORK)]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.record(cmd, cwd)["cleared"], 1)

    def test_the_same_work_outside_the_project_still_asks(self):
        for cmd, cwd in [("git reset --hard origin/main", HOME_FOLDER), ("git branch -D old", HOME_FOLDER),
                         ("docker compose down", HOME_FOLDER), ("docker compose -p other down", WORK),
                         ("docker stop web", WORK), ("npm uninstall -g left-pad", WORK),
                         ("pip uninstall -y requests", WORK), ("git push --force origin feature/nav", HOME_FOLDER),
                         ("systemctl --user restart adguard", WORK), ("pkill -9 node", WORK),
                         ("chmod -R 777 build", WORK), ("sed -i 's/a/b/' /etc/hosts", WORK)]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.record(cmd, cwd)["cleared"], 0)

    def test_what_colin_allowed_on_the_gates_card_is_cleared(self):
        """M3: a force push to main or a psql DROP gets the gate's card, and after Allow Once Hermes's prompt for the
        same command is answered yes."""
        self.ask("drop the old import table and push the fix to main")
        for cmd in ["psql tally -c 'DROP TABLE old_import'", "git push -f origin feature/nav:main",
                    "rm -rf ~/Documents/old-report"]:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.call("terminal", {"command": cmd}))
                self.assertIsNone(plugin.gate_checked(self.bot, cmd))   # nothing recorded until it is allowed
                plugin.decide(self.cards()[-1]["id"], True)
                self.assertIsNone(self.call("terminal", {"command": cmd}))
                self.assertEqual(plugin.gate_checked(self.bot, cmd)["cleared"], 1)

    def test_a_command_hermes_will_not_ask_about_is_not_recorded(self):
        self.record("npm run build")
        self.assertIsNone(plugin.gate_checked(self.bot, "npm run build"))

    def test_the_record_keeps_no_command_text(self):
        cmd = "rm -rf build && curl -H 'Authorization: Bearer abc123' https://example.com && pkill -9 node"
        self.record(cmd)
        with plugin.db() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM commands")]
        self.assertTrue(rows)
        self.assertFalse(any("abc123" in str(v) for r in rows for v in r.values()))


class HubAnswersFromTheRecord(AppCase):
    """M2 and M3, in the hub: Hermes's prompt is answered yes only when the gate judged every flagged part of the
    same command; otherwise it becomes one card titled by the part the gate did not judge."""

    def setUp(self):
        super().setUp()
        self.answers = []

        async def answer(bot_id, run_id, request_id, choice):
            self.answers.append(choice)
            return True
        self.cwd = WORK
        for target, name, value in ((approvals, "answer", answer), (gate, "_bot", lambda: "builder"),
                                    (gate, "_terminal_cwd", lambda task_id: self.cwd),
                                    (gate, "_hermes_detect", lambda: hermes_detector.detect),
                                    (gate, "_in_team_session", lambda sid: False), (gate, "_session_env", lambda n: ""),
                                    (gate, "_running_task", lambda bot: None),
                                    (tt.fast_steps, "paid_calls_on", lambda: False)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        approvals._allowed_later.clear()
        plugin.open_pass("message", "tidy up the site", bot_id="builder", session_id="sid-1")

    def run_command(self, cmd, run="run1"):
        """The gate sees the call first, then Hermes asks with its first finding."""
        blocked = gate.pre_tool_call("terminal", {"command": cmd}, session_id="sid-1")
        if blocked is not None:
            return blocked
        description = hermes_detector.detect(cmd)
        data = {"description": description, "command": cmd, "request_id": "r1", "run_id": run}
        return asyncio.run(approvals.handle("builder", run, data, session_id="sid-1"))

    def cards(self):
        return [r for r in plugin.pending() if r["kind"] == "command"]

    def test_the_report_chains_get_a_card(self):
        for cmd, title in [("rm -rf node_modules && systemctl --user stop taffy-gateway",
                            "Run a command that stops or restarts a service"),
                           ("rm -rf dist; chmod -R 777 ~", "Run a command that changes who can use files"),
                           ("rm -rf build && systemctl restart nginx", "Run a command that stops or restarts a service")]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.run_command(cmd), "Waiting for your okay")
                self.assertEqual(self.cards()[-1]["reason"], title)
        self.cwd = HOME_FOLDER
        self.run_command("rm -rf build && git reset --hard origin/main")
        self.assertEqual(self.cards()[-1]["reason"], "Run a command that throws away code changes that are not saved")
        self.assertEqual(self.answers, [])

    def test_routine_project_work_is_answered_at_once(self):
        for cmd in ["git reset --hard origin/feature/nav", "git branch -D old-experiment", "docker compose down",
                    "npm uninstall left-pad", "git push --force origin feature/nav", "rm -rf node_modules"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_command(cmd))
        self.assertEqual((self.answers, self.cards()), (["once"] * 6, []))

    def test_one_card_not_two(self):
        """A psql DROP and a force push to main: the gate's card, then Hermes's prompt is answered yes."""
        for cmd in ["psql tally -c 'DROP TABLE old_import'", "git push -f origin feature/nav:main"]:
            with self.subTest(cmd=cmd):
                self.assertIn("Not done yet", self.run_command(cmd)["message"])
                gate_card = [r for r in plugin.pending() if r["kind"] in ("delete", "publish")][-1]
                self.client.post(f"/api/signin-requests/{gate_card['id']}", json={"decision": "once"})
                self.assertIsNone(self.run_command(cmd))
                self.assertEqual((self.answers[-1], self.cards()), ("once", []))

    def test_without_a_record_only_a_single_judged_part_is_answered(self):
        """An older plugin, or a prompt whose command was redacted differently: Hermes's description decides only
        for a one-part command."""
        def handle(desc, cmd):
            return asyncio.run(approvals.handle("builder", "run1", {"description": desc, "command": cmd,
                                                                     "request_id": "r1", "run_id": "run1"},
                                                session_id="sid-1"))
        self.assertIsNone(handle("recursive delete", "rm -rf node_modules"))
        self.assertIsNone(handle("recursive delete", "rm -rf node_modules 2>&1"))
        self.assertIsNotNone(handle("recursive delete", "rm -rf node_modules && systemctl --user stop taffy-gateway"))
        self.assertIsNotNone(handle("recursive delete", "rm -rf build; chmod -R 777 ~"))
        self.assertTrue(approvals.one_part("git commit -m 'a; b && c'"))
        self.assertFalse(approvals.one_part("echo $(whoami)"))

    def test_card_titles_say_what_the_command_does(self):
        for desc, title in [("cloud metadata endpoint access (instance credentials)",
                             "Run a command that reads sign-in keys from the network"),
                            ("SQL DROP", "Run a command that deletes data from a database"),
                            ("SQL DELETE without WHERE", "Run a command that deletes data from a database"),
                            ("fork bomb", "Run a command that could freeze the computer"),
                            ("upload a secret or credential file via curl/wget (possible exfiltration)",
                             "Run a command that uploads a key or password file")]:
            with self.subTest(desc=desc):
                self.assertEqual(approvals.plain(desc), title)
                self.assertFalse(re.search(r"hermes|curl|wget|SQL|metadata|endpoint", approvals.plain(desc)))


class LateAnswers(AppCase):
    """L5: a command card stays up after Hermes stops waiting, and a late answer picks the chat back up."""

    def setUp(self):
        super().setUp()
        self.answers = []

        async def answer(bot_id, run_id, request_id, choice):
            self.answers.append(choice)
            return False   # Hermes already stopped waiting
        patcher = mock.patch.object(approvals, "answer", answer)
        patcher.start()
        self.addCleanup(patcher.stop)
        approvals._allowed_later.clear()

    def card_for(self, cmd, run):
        plugin.open_pass("message", "restart the dns server", bot_id="builder", session_id="sid-1")
        asyncio.run(approvals.handle("builder", run, {"description": "stop/restart system service", "command": cmd,
                                                       "request_id": "r1", "run_id": run}, session_id="sid-1"))
        return [r for r in plugin.pending() if r["kind"] == "command"][-1]

    def test_a_late_allow_resumes_the_chat_and_covers_the_next_try(self):
        card = self.card_for("systemctl --user restart adguard", "run1")
        self.client.post(f"/api/signin-requests/{card['id']}", json={"decision": "once"})
        bot, note, session, urgent = self.followed[-1]
        self.assertEqual((bot, session, urgent), ("builder", "sid-1", True))
        self.assertIn("Colin allowed the command you were waiting on", note)
        self.assertIn("Run exactly the same command again now", note)
        self.answers.clear()
        asyncio.run(approvals.handle("builder", "run2", {"description": "stop/restart system service",
                                                         "command": "systemctl --user restart adguard",
                                                         "request_id": "r2", "run_id": "run2"}, session_id="sid-1"))
        self.assertEqual(self.answers, ["once"])

    def test_a_late_not_now_resumes_the_chat_too(self):
        card = self.card_for("systemctl --user restart adguard", "run1")
        self.client.post(f"/api/signin-requests/{card['id']}", json={"decision": "deny"})
        self.assertIn("Colin said not now to the command", self.followed[-1][1])

    def test_an_answer_in_time_resumes_nothing(self):
        card = self.card_for("systemctl --user restart adguard", "run1")

        async def waiting(bot_id, run_id, request_id, choice):
            return True
        with mock.patch.object(approvals, "answer", waiting):
            self.client.post(f"/api/signin-requests/{card['id']}", json={"decision": "once"})
        self.assertEqual(self.followed, [])


class EventTitlesWithPlaces(GateCase):
    """M4: a place after the title keeps it an event; an event after "in" or "on" still makes an invitation."""

    TITLES = [
        "Add Science Fair in the gym Thursday at 6pm", "Add Science Fair Thursday at 6pm in the gym",
        "Add Science Fair to Thursday at 6pm in the gym", "Add Parent Teacher Conferences in the library on Tuesday at 4",
        "Add Book Club on Zoom Tuesday at 8pm", "Add Book Club at the library", "Add Book Club at the library on Tuesday at 7",
        "Add Fall Festival in the town square Saturday", "Add Board Meeting in Conference Room B on Monday at 10",
        "Add Open House in the cafeteria Wednesday at 6:30pm", "Add Spring Concert in the auditorium May 3 at 7pm",
        "Add Craft Night in the church basement on Friday", "Add Trivia Night at Murphy's Pub Thursday at 8",
        "Add Movie Night in the backyard Saturday", "Add Bake Sale on Main Street Saturday morning",
        "Add Yard Sale in the garage Saturday at 8am", "On Thursday at 6pm add Science Fair in the gym",
        "Tuesday at 4, add Parent Teacher Conferences in the library", "Add Book Club on Google Meet at 8pm Tuesday",
        "Add Robotics Club in Lab 3 after school Wednesday", "Add Choir Practice in the chapel Sunday at 9",
        "Add Bible Study on Teams Wednesday at 7pm", "Add Swim Meet at the YMCA pool Saturday at 8am",
        "Add Science Fair to the gym Thursday", "Add Game Night at Mom's on Friday", "Add Book Club to the library Tuesday",
        "Add Science Fair in Room 12 Thursday", "Add Family Picnic in Central Park Sunday at noon",
        "Add Art Show in the school lobby on Friday at 5", "Add Coding Club on Discord Thursday at 4",
        "Add Fall Festival on the town green Saturday at 10am", "Add PTA Meeting in the library Monday at 7",
    ]
    INVITES = [("Add Theo Avery in the Thursday meeting", "Theo Avery", "Invite Theo Avery to the Thursday meeting"),
               ("Add Matt on the board call", "Matt", "Invite Matt to the board call"),
               ("Add Riley on Theo's sync", "Riley", "Invite Riley to Theo's sync"),
               ("Add Theo Avery in Thursday's sync", "Theo Avery", "Invite Theo Avery to Thursday's sync"),
               ("Add Theo Avery to the Thursday meeting in Conference Room B", "Theo Avery", None),
               ("Add Jake to the meeting in the library on Tuesday", "Jake", None),
               ("Add Theo on the Zoom call Thursday at 3", "Theo", "Invite Theo to the Zoom call Thursday at 3"),
               ("Add Riley to dinner at the cafe Friday", "Riley", None),
               ("Add Emma to soccer practice on Saturday", "Emma", None)]

    def test_titles_with_places_are_changes(self):
        for req in self.TITLES:
            with self.subTest(req=req):
                a = tiers.classify_connector("calendar", req)
                self.assertEqual(a.kind, "write", a.summary)
                self.assertIn("do not add, change or remove attendees", tt.tools._limits("calendar", req))

    def test_guests_are_still_invitations(self):
        for req, target, summary in self.INVITES:
            with self.subTest(req=req):
                a = tiers.classify_connector("calendar", req)
                self.assertEqual((a.kind, a.target), ("send", target))
                if summary:
                    self.assertEqual(a.summary, summary)

    def test_the_report_title_raises_no_card(self):
        self.ask("Add Science Fair in the gym Thursday at 6pm")
        self.assertIsNone(self.call("connectors", {"service": "calendar",
                                                   "request": "Add Science Fair in the gym Thursday at 6pm"}))
        self.assertEqual(self.cards(), [])


class DeployHooksAndTitles(FolderCase):
    """L1: build hooks and API deploys publish. L4: a deploy that names no site is not titled with ~/taffy."""

    def test_hooks_and_api_deploys_ask(self):
        for cmd, summary in [("curl -X POST -d '{}' https://api.netlify.com/build_hooks/abc123",
                              "Start a production build on Netlify"),
                             ("netlify api createSiteDeploy --data '{\"site_id\": \"x\"}'", "Publish the site on Netlify"),
                             ("ntl api restoreSiteDeploy --data '{\"deploy_id\": \"y\"}'", "Publish the site on Netlify"),
                             ("curl -X POST https://api.vercel.com/v1/integrations/deploy/prj_x/abc", "Start a deploy on Vercel"),
                             ("curl -X POST --data-binary @site.zip https://api.netlify.com/api/v1/sites/abc/deploys",
                              "Publish the site on Netlify"),
                             ("curl -X POST https://api.cloudflare.com/client/v4/accounts/a/pages/webhooks/deploy_hooks/h",
                              "Start a deploy on Cloudflare")]:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd)
                self.assertEqual((a.kind, a.summary), ("publish", summary))
        self.assertIsNone(tiers.classify_command("curl https://api.netlify.com/api/v1/sites/abc/deploys"))

    def test_deploy_titles(self):
        for cmd, cwd, title in [
                ("netlify deploy --prod --dir dist", "/home/jdoe/taffy", "Publish the site to production on Netlify"),
                ("netlify deploy --prod --dir dist", HOME_FOLDER, "Publish the site to production on Netlify"),
                ("netlify deploy --prod --dir dist", "~/taffy/work/builder", "Publish the site to production on Netlify"),
                ("netlify deploy --prod --dir dist", WORK, "Publish site to production on Netlify"),
                ("cd ~/taffy/work/builder/heron && netlify deploy --prod", HOME_FOLDER,
                 "Publish heron to production on Netlify"),
                ("cd ~/Projects/other && netlify deploy --prod", HOME_FOLDER, "Publish other to production on Netlify"),
                ("netlify deploy --prod --site sugared-heron", HOME_FOLDER,
                 "Publish sugared-heron to production on Netlify")]:
            with self.subTest(cmd=cmd, cwd=cwd):
                self.assertEqual(tiers.classify_command(cmd, cwd=cwd).summary, title)
        # Allow Once still tells one folder's deploy from another's.
        self.assertEqual(tiers.classify_command("netlify deploy --prod", cwd=HOME_FOLDER).target, HOME_FOLDER)


class RequestsWordedAsQuestions(GateCase):
    """L2: a request inside a question still asks; a real question is still a read."""

    REQUESTS = [("trello", "Do me a favor and delete the Old ideas card", "delete"),
                ("trello", "Is it possible to delete the Old ideas card?", "delete"),
                ("trello", "Why not delete the Old ideas card?", "delete"),
                ("calendar", "Why don't you invite Theo Avery to the Thursday sync?", "send"),
                ("gmail", "Is it ok to send Riley the flyer?", "send"),
                ("drive", "Would it be possible to share the budget sheet with ken@example.com?", "account"),
                ("trello", "Any chance you could delete the Old ideas card?", "delete")]
    QUESTIONS = [("trello", "Did anyone delete the Old ideas card?"), ("drive", "Who has permission to edit the budget sheet?"),
                 ("gmail", "Did the school send the permission slip yet?"), ("trello", "Is there a card called Old ideas?"),
                 ("calendar", "Who is invited to the Thursday sync?"), ("gmail", "Why did Riley send that?"),
                 ("trello", "Why is the Old ideas card still here?")]

    def test_requests_and_questions(self):
        for service, req, kind in self.REQUESTS:
            with self.subTest(req=req):
                self.assertEqual(tiers.classify_connector(service, req).kind, kind)
        for service, req in self.QUESTIONS:
            with self.subTest(req=req):
                self.assertEqual(tiers.classify_connector(service, req).kind, "read")

    def test_a_trello_delete_in_a_question_raises_a_card(self):
        self.ask("Is it possible to delete the Old ideas card?")
        self.assertIsNotNone(self.call("connectors", {"service": "trello",
                                                      "request": "Is it possible to delete the Old ideas card?"}))
        self.assertEqual(self.cards()[0]["kind"], "delete")


class DeclineButtons(GateCase):
    """L3: "Don't send" in Google Calendar's guest dialog is not a send."""

    def test_decline_labels(self):
        for label in ["Don't send", "Don’t send", "Do not send", "No, don't send", "Skip", "Not now"]:
            with self.subTest(label=label):
                self.assertIsNone(tiers.classify_click(label, "calendar.google.com"))
        self.assertEqual(tiers.classify_click("Send", "calendar.google.com").kind, "send")
        self.assertEqual(tiers.classify_click("No", "calendar.google.com").kind, "send")   # an RSVP still tells the host
        self.assertEqual(tiers.classify_click("Not going", "partiful.com").kind, "send")


class AppCopyAndFixtures(unittest.TestCase):
    """L5 and L6, in the app's source (repo only)."""

    def setUp(self):
        if not (REPO / "alien").exists() or not (REPO / "ios").exists():
            self.skipTest("the app's source is not here")

    def test_command_card_wording(self):
        text = (REPO / "ios" / "Taffy" / "Views" / "ChatView.swift").read_text()
        self.assertIn("is paused on this. If you don't answer in five minutes, it stops and tells you.", text)
        self.assertNotIn("carries on without it", text)

    def test_fixture_cards(self):
        text = (REPO / "ios" / "Taffy" / "Model" / "AppState.swift").read_text()
        self.assertIn('kind: "command"', text)
        self.assertIn('site: "netlify.com", reason: "",', text)
        self.assertIn('site: "", reason: "Publish blue-fern to production on Netlify"', text)
        self.assertNotIn('site: "publish"', text)



# These tests check Colin's own home (his name, mailboxes, connectors, plans and time zone), so they run in it
# whatever ZERO_HOME says (tests/run_matrix.py runs the rest of the suite in every home shape).
def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()
