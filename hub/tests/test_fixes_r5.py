"""Internals QA round 5 (docs/qa/2026-10-08-internals-qa-round-5.md): one-line scripts no longer put a command card in
front of Colin, Allow Once covers one folder, "Add <title> to <day> at <venue>" is an event, and the Lows. Hermes's
prompts come from its own detector (hermes_detector: Hermes's module when it is importable, otherwise the faithful
copy in hermes_fixture/), never from the gate's copy of its patterns. The judge is always mocked and paid calls stay
off. The terminal's folder is set by each test.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import hashlib
import re
import sys
import tempfile
import time
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
HOME_FOLDER = "~/taffy"

SCRIPTS = [
    """python3 -c "import json,sys; d=json.load(open('package.json')); print(d['version'])\"""",
    """node -e "const p=require('./package.json'); console.log(p.scripts)\"""",
    """perl -pi -e 's/foo/bar/; s/baz/qux/' src/config.js""",
    """python3 -c 'import sys; print(sys.version)'""",
    """rm -rf node_modules && npm ci && node -e "console.log('ok'); process.exit(0)\"""",
    """ruby -e 'puts 1; puts 2'""",
    """python3 -c "print('a && b || c; d | e & f')\"""",
    """node -e 'let a = 1; if (a) { console.log(a) } else { process.exit(1) }' && npm test""",
    """python3 -c "import os; print(os.getcwd()); print(len(os.listdir('.')))" | head -5""",
    """deno eval "console.log(1); console.log(2)\"""",
    """perl -ne 'print if /TODO/; print "x\\n" if /FIXME/' src/app.js""",
    """node --input-type=module -e "import fs from 'fs'; console.log(fs.readdirSync('.').length)\"""",
    """python3 -c "import sys; print(sys.argv[1:])" "a; b" 'c && d'""",
    """cd ~/taffy/work/builder/site && python3 -c "import json; print(json.load(open('package.json'))['name'])\"""",
    """python3 -c "import json
data = json.load(open('package.json')); print(data['version'])\"""",
    """bash -c 'npm run build; npm test'""",
    """sh -c "cd dist && ls; echo done\"""",
    """python3 -c "print('$(date +%Y)'); print(1)\"""",
    """python3 -c 'import json, pathlib; p = pathlib.Path("data.json"); print(json.loads(p.read_text())["count"])'""",
]


class HermesDetectorFixture(unittest.TestCase):
    """M1: the tests use the detector Hermes uses, and the copy of it is Hermes's file unchanged."""

    def test_the_copy_matches_its_recorded_hashes(self):
        note = (hermes_detector.FIXTURE.parent / "__init__.py").read_text()
        for name in ("approval_detection.py", "ansi_strip.py"):
            with self.subTest(name=name):
                recorded = re.search(rf"tools/{re.escape(name)}\s+sha256 (\w+)", note).group(1)
                self.assertEqual(hashlib.sha256((hermes_detector.FIXTURE / name).read_bytes()).hexdigest(), recorded)

    def test_it_sees_what_the_report_saw(self):
        self.assertEqual(hermes_detector.detect('python3 -c "import json'),
                         "command parser limit or malformed executable payload")
        self.assertEqual(hermes_detector.detect(SCRIPTS[0]), "script execution via -e/-c flag")
        self.assertIsNone(hermes_detector.detect("npm run build"))

    def test_it_leaves_no_tools_module_behind(self):
        before = sys.modules.get("tools")
        hermes_detector.detect("rm -rf build")
        self.assertIs(sys.modules.get("tools"), before)

    def test_the_gate_copy_now_has_the_malformed_check(self):
        self.assertEqual(tiers.hermes_like('python3 -c "import json'), tiers._MALFORMED)
        self.assertEqual(tiers.hermes_like("node -e 'x"), tiers._MALFORMED)
        self.assertIsNone(tiers.hermes_like('echo "a'))


class HubCase(AppCase):
    """The gate sees each terminal call first, then Hermes asks with its own detector's first finding, and the hub
    answers or makes a card."""

    def setUp(self):
        super().setUp()
        self.answers = []
        self.waiting = True

        async def answer(bot_id, run_id, request_id, choice):
            self.answers.append(choice)
            return self.waiting
        self.cwd = WORK
        for target, name, value in ((approvals, "answer", answer), (gate, "_bot", lambda: "builder"),
                                    (gate, "_terminal_cwd", lambda task_id: self.cwd),
                                    (gate, "_hermes_detect", lambda: hermes_detector.detect),
                                    (gate, "_in_team_session", lambda sid: False), (gate, "_session_env", lambda n: ""),
                                    (gate, "_running_task", lambda bot: None),
                                    (gate, "_taffy_home", lambda: Path(tempfile.gettempdir()) / "zero-tests-no-taffy"),
                                    (tt.fast_steps, "paid_calls_on", lambda: False)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        approvals._allowed_later.clear()
        plugin.open_pass("message", "work on the site", bot_id="builder", session_id="sid-1")
        self.runs = 0

    def run_command(self, cmd):
        """None when it ran with no card, the hub's status line when Hermes's prompt became a card, or the gate's
        block."""
        blocked = gate.pre_tool_call("terminal", {"command": cmd}, session_id="sid-1")
        if blocked is not None:
            return blocked
        description = hermes_detector.detect(cmd)
        if description is None:
            return None
        self.runs += 1
        run = f"run{self.runs}"
        data = {"description": description, "command": cmd, "request_id": "r1", "run_id": run}
        return asyncio.run(approvals.handle("builder", run, data, session_id="sid-1"))

    def cards(self):
        return [r for r in plugin.pending() if r["kind"] == "command"]


class InlineScripts(HubCase):
    """M1: a one-line script with a ";" inside its quotes is answered at once, as before round 4."""

    def test_the_parts_are_cut_where_the_shell_cuts_them(self):
        self.assertEqual(tiers._split(SCRIPTS[0]), [SCRIPTS[0]])
        self.assertEqual(tiers._split("a 'b; c' && d | e; f &"), ["a 'b; c' ", " d ", " e", " f "])
        self.assertEqual(tiers._split("a | b && c", pipes=False), ["a | b ", " c"])
        self.assertEqual(tiers._scan("echo $(cd x; ls) `pwd`")[1], ["cd x; ls", "pwd"])
        self.assertEqual(tiers._split("echo 'unclosed; rm x"), ["echo 'unclosed", " rm x"])
        self.assertEqual([s for s, _ in tiers._segments("(cd x; ls) && echo $(date)", WORK)], ["ls", "echo $(date)"])

    def test_the_gate_judges_every_part(self):
        for cmd in SCRIPTS:
            for cwd in (WORK, HOME_FOLDER):
                with self.subTest(cmd=cmd, cwd=cwd):
                    self.assertIsNone(tiers.classify_command(cmd, cwd=cwd))
                    self.assertIsNone(tiers.unjudged(cmd, cwd, hermes_detector.detect))
                    self.assertIsNone(tiers.unjudged(cmd, cwd))

    def test_hermes_is_answered_yes_with_no_card(self):
        for cmd in SCRIPTS:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_command(cmd))
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.answers, ["once"] * self.runs)
        self.assertGreaterEqual(self.runs, len(SCRIPTS) - 1)

    def test_from_the_starting_folder_too(self):
        self.cwd = HOME_FOLDER
        for cmd in SCRIPTS[:4] + SCRIPTS[5:8]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_command(cmd))
        self.assertEqual(self.cards(), [])

    def test_a_script_that_does_more_still_asks_and_says_what(self):
        for cmd, title in [
                ("""python3 -c "import os; os.system('systemctl --user restart adguard')\"""",
                 "Run a command that stops or restarts a service"),
                ("""python3 -c "import json; print(1)" && systemctl --user restart adguard""",
                 "Run a command that stops or restarts a service"),
                ("""node -e "console.log(1); console.log(2)"; git reset --hard origin/main""",
                 "Run a command that throws away code changes that are not saved")]:
            with self.subTest(cmd=cmd):
                self.cwd = HOME_FOLDER
                self.assertEqual(self.run_command(cmd), "Waiting for your okay")
                self.assertEqual(self.cards()[-1]["reason"], title)

    def test_every_card_title_says_what_the_command_does(self):
        descriptions = [d for _p, d in hermes_detector.module.DANGEROUS_PATTERNS] + [
            "command parser limit or malformed executable payload", "command parser limit exceeded",
            "script execution via -e/-c flag", "script execution via heredoc", "shell command via -c/-lc flag",
            "arbitrary program execution via rg --pre", "stop/restart hermes gateway via shell-spliced verb (kills "
            "running agents)"]
        for d in descriptions:
            with self.subTest(description=d):
                title = approvals.plain(d, "some-tool --flag")
                self.assertNotEqual(title, "Run a command that needs your okay")
                self.assertFalse(re.search(r"hermes|gateway|systemd|systemctl|curl|wget|SQL|metadata|endpoint|sudo|"
                                           r"daemon|regex|heredoc|payload|parser|—", title, re.I), title)
        for desc, cmd, title in [
                ("script execution via -e/-c flag", 'python3 -c "x"', "Run a short Python script"),
                ("script execution via heredoc", "node - <<EOF", "Run a short JavaScript script"),
                ("command parser limit or malformed executable payload", "perl -e 'x", "Run a short Perl script"),
                ("command parser limit or malformed executable payload", "awk 'x", "Run a command too long or tangled to read"),
                ("chmod +x followed by immediate execution", "", "Run a script it just made runnable"),
                ("docker compose restart/stop/kill/down (container lifecycle)", "", "Run a command that stops or restarts containers")]:
            with self.subTest(desc=desc, cmd=cmd):
                self.assertEqual(approvals.plain(desc, cmd), title)


class WhatRunsInside(HubCase):
    """L1: what runs inside backticks, $(...) and heredocs is a part of its own, and so is a compose project set by a
    variable."""

    EVASIONS = [
        "rm -rf build `systemctl --user stop taffy-gateway`",
        "rm -rf build $(systemctl --user stop taffy-gateway)",
        "rm -rf build && bash <<'EOF'\nsystemctl --user stop taffy-gateway\nEOF",
        "rm -rf build && python3 - <<'EOF'\nimport os\nos.system('systemctl --user stop taffy-gateway')\nEOF",
        "rm -rf build && sh <<< 'systemctl --user restart adguard'",
        "rm -rf build; echo 'systemctl --user restart adguard' | bash",
    ]

    def test_the_report_evasions_get_a_card(self):
        for cmd in self.EVASIONS:
            with self.subTest(cmd=cmd):
                self.assertEqual(tiers.unjudged(cmd, WORK, hermes_detector.detect), "stop/restart system service")
                if "$(" in cmd or "`" in cmd:
                    self.assertEqual(tiers.classify_command(cmd, cwd=WORK).summary,
                                     "Delete a path another command works out")
                    continue
                self.assertEqual(self.run_command(cmd), "Waiting for your okay")
                self.assertEqual(self.cards()[-1]["reason"], "Run a command that stops or restarts a service")
        self.assertNotIn("once", self.answers)

    def test_after_the_delete_is_allowed_the_restart_still_asks(self):
        cmd = "rm -rf build `systemctl --user stop taffy-gateway`"
        self.assertIn("Not done yet", self.run_command(cmd)["message"])
        gate_card = [r for r in plugin.pending() if r["kind"] == "delete"][-1]
        self.client.post(f"/api/signin-requests/{gate_card['id']}", json={"decision": "once"})
        self.assertEqual(self.run_command(cmd), "Waiting for your okay")
        self.assertEqual(self.cards()[-1]["reason"], "Run a command that stops or restarts a service")

    def test_compose_pointed_elsewhere_by_a_variable_asks(self):
        for cmd in ["COMPOSE_PROJECT_NAME=adguard docker compose down", "export COMPOSE_PROJECT_NAME=adguard; docker compose down",
                    "COMPOSE_FILE=~/adguard/compose.yml docker compose stop", "DOCKER_HOST=ssh://nas docker compose down",
                    "docker compose --env-file ~/adguard/.env down"]:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(tiers.unjudged(cmd, WORK, hermes_detector.detect))
        self.assertIsNone(tiers.unjudged("docker compose down", WORK, hermes_detector.detect))
        self.assertIsNone(tiers.unjudged("COMPOSE_PROFILES=dev docker compose down", WORK, hermes_detector.detect))

    def test_routine_work_inside_a_heredoc_or_substitution_is_still_answered(self):
        for cmd in ["bash <<'EOF'\nrm -rf node_modules\nnpm ci\nEOF", "echo \"built $(rm -rf dist && npm run build)\"",
                    "rm -rf build && bash -c 'rm -rf dist; npm run build'"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_command(cmd))
        self.assertEqual(self.cards(), [])

    def test_the_gate_reads_inside_too(self):
        a = tiers.classify_command("echo $(rm -rf ~/Documents/old)", cwd=WORK)
        self.assertEqual((a.kind, a.summary, a.inputs), ("delete", "Delete ~/Documents/old",
                                                        "echo $(rm -rf ~/Documents/old)"))
        a = tiers.classify_command("bash <<'EOF'\nrm -rf ~/Documents/old\nEOF", cwd=WORK)
        self.assertEqual(a.summary, "Delete ~/Documents/old")
        self.assertEqual(tiers.classify_command("echo $(claude -p hi)").kind, "agent")
        self.assertIsNone(tiers.classify_command("kill $(pgrep -f 'npm run dev')", cwd=WORK))

    def test_a_delete_allowed_inside_a_substitution_is_asked_once(self):
        cmd = "echo $(rm -rf ~/Documents/old)"
        self.assertIn("Not done yet", self.run_command(cmd)["message"])
        gate_card = [r for r in plugin.pending() if r["kind"] == "delete"][-1]
        self.client.post(f"/api/signin-requests/{gate_card['id']}", json={"decision": "once"})
        self.assertIsNone(self.run_command(cmd))
        self.assertEqual(self.cards(), [])


class OrdinaryWorkHermesFlags(HubCase):
    """L2: SQL words with no database client, and a settings file in the Zip's own project, need no card. A script
    made runnable and run at once still asks, under a title that says so."""

    def test_sql_words_in_a_search_are_answered(self):
        for cmd in ['grep -rn "DROP TABLE" migrations/', 'git log -S "DELETE FROM"', "grep -rn 'TRUNCATE TABLE' db/",
                    "rg -l 'DELETE FROM users' src", "echo 'DROP TABLE x' > notes.sql"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_command(cmd))
        self.assertEqual(self.cards(), [])

    def test_sql_sent_to_a_database_still_asks(self):
        self.assertEqual(self.run_command("echo 'DROP TABLE x' | psql tally"), "Waiting for your okay")
        self.assertEqual(self.cards()[-1]["reason"], "Run a command that deletes data from a database")
        self.assertIn("Not done yet", self.run_command("psql tally -c 'DROP TABLE old_import'")["message"])
        self.assertEqual(tiers.classify_command("""python3 -c "import sqlite3; sqlite3.connect('x.db').execute('DROP TABLE t')\"""",
                                                cwd=WORK).kind, "delete")

    def test_a_settings_file_in_the_project(self):
        for cmd in ["cat > .env.local <<'EOF'\nVITE_API=http://localhost:3000\nEOF", "printf 'a: 1\\n' > config.yaml",
                    "echo 'PORT=3000' | tee .env.development"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_command(cmd))
        self.assertEqual(self.cards(), [])
        self.cwd = HOME_FOLDER
        self.assertEqual(self.run_command("cat > .env.local <<'EOF'\nA=1\nEOF"), "Waiting for your okay")
        self.assertEqual(self.cards()[-1]["reason"], "Run a command that changes a settings file")

    def test_a_script_made_runnable_and_run(self):
        self.assertEqual(self.run_command("chmod +x scripts/build.sh && ./scripts/build.sh"), "Waiting for your okay")
        self.assertEqual(self.cards()[-1]["reason"], "Run a script it just made runnable")


class AllowOnceCoversOneFolder(GateCase):
    """M2: Allow Once on a command covers the same command in the same folder only."""

    bot = "builder"

    def setUp(self):
        super().setUp()
        self.cwd = WORK
        gate._terminal_cwd = lambda task_id: self.cwd

    def call_in(self, folder, cmd, tool="terminal"):
        self.cwd = folder
        return self.call(tool, {"command": cmd} if tool == "terminal" else {"code": cmd})

    def test_a_deploy_allowed_for_one_site_does_not_publish_another(self):
        self.ask("publish the site")
        cmd = "netlify deploy --prod --dir dist"
        heron, landing = "~/taffy/work/builder/sugared-heron", "~/taffy/work/builder/zero-landing"
        self.assertIsNotNone(self.call_in(heron, cmd))
        card = self.cards()[-1]
        self.assertEqual(card["reason"], "Publish sugared-heron to production on Netlify")
        plugin.decide(card["id"], True)
        self.assertIsNotNone(self.call_in(landing, cmd))
        self.assertEqual(self.cards()[-1]["reason"], "Publish zero-landing to production on Netlify")
        self.assertIsNone(self.call_in(heron, cmd))
        self.assertIsNotNone(self.call_in(heron, cmd))

    def test_a_relative_delete_allowed_in_one_folder_asks_in_another(self):
        self.ask("clear out the old folder")
        self.assertIsNotNone(self.call_in("~/Documents", "rm -rf old"))
        card = self.cards()[-1]
        self.assertEqual(card["reason"], "Delete ~/Documents/old")
        plugin.decide(card["id"], True)
        self.assertIsNotNone(self.call_in("~/Projects", "rm -rf old"))
        self.assertEqual(self.cards()[-1]["reason"], "Delete ~/Projects/old")
        self.assertIsNone(self.call_in("~/Documents", "rm -rf old"))

    def test_the_folder_is_the_same_however_it_is_written(self):
        self.ask("clear out the old folder")
        self.call_in("~/Documents", "rm -rf old")
        plugin.decide(self.cards()[-1]["id"], True)
        self.assertIsNone(self.call_in(str(Path.home() / "Documents"), "rm -rf old"))

    def test_a_delete_title_uses_the_home_folder_sign(self):
        a = tiers.classify_command("rm -rf old", cwd=str(Path.home() / "Documents"))
        self.assertEqual(a.summary, "Delete ~/Documents/old")

    def test_code_is_bound_to_its_folder_too(self):
        self.ask("tidy up")
        code = "import shutil\nshutil.rmtree('old')"
        self.assertIsNotNone(self.call_in("~/Documents", code, tool="execute_code"))
        plugin.decide(self.cards()[-1]["id"], True)
        self.assertIsNotNone(self.call_in("~/Projects", code, tool="execute_code"))
        self.assertIsNone(self.call_in("~/Documents", code, tool="execute_code"))


class ScheduledAllowOnceOneFolder(GateCase):
    """M2, S6: a scheduled job's Allow Once does not cover the same command in another folder, even within the half
    hour the exact call is remembered."""

    bot = "builder"

    def setUp(self):
        super().setUp()
        gate._session_env = lambda name: "1" if name == "HERMES_CRON_SESSION" else ""
        real = gate._routine
        gate._routine = lambda task_id: {"id": "nightly", "name": "Nightly deploy", "prompt": "deploy the site"}
        self.addCleanup(setattr, gate, "_routine", real)
        self.cwd = "~/taffy/work/builder/sugared-heron"
        gate._terminal_cwd = lambda task_id: self.cwd

    def run_call(self, cmd, run):
        return gate.pre_tool_call("terminal", {"command": cmd}, session_id=f"cron-{run}", task_id=f"cron:nightly:{run}")

    def test_another_folder_gets_its_own_card(self):
        cmd = "netlify deploy --prod --dir dist"
        self.assertIsNotNone(self.run_call(cmd, "run1"))
        plugin.decide(self.cards()[-1]["id"], True)
        self.cwd = "~/taffy/work/builder/zero-landing"
        self.assertIsNotNone(self.run_call(cmd, "run1"))
        self.cwd = "~/taffy/work/builder/sugared-heron"
        self.assertIsNone(self.run_call(cmd, "run2"))


class LateAllowInTheSameFolder(HubCase):
    """M2 for Hermes's own prompts: a late Allow Once covers the next try in the folder the card was for."""

    def setUp(self):
        super().setUp()
        self.waiting = False
        self.cwd = HOME_FOLDER

    def test_another_folder_gets_its_own_card(self):
        cmd = "git reset --hard origin/main"
        self.cwd = "~/Projects/site-a"
        self.assertEqual(self.run_command(cmd), "Waiting for your okay")
        self.client.post(f"/api/signin-requests/{self.cards()[-1]['id']}", json={"decision": "once"})
        self.answers.clear()
        self.cwd = "~/Projects/site-b"
        self.assertEqual(self.run_command(cmd), "Waiting for your okay")
        self.assertEqual(self.answers, [])

    def test_the_same_folder_goes_through_once(self):
        cmd = "git reset --hard origin/main"
        self.cwd = "~/Projects/site-a"
        self.run_command(cmd)
        self.client.post(f"/api/signin-requests/{self.cards()[-1]['id']}", json={"decision": "once"})
        self.answers.clear()
        self.assertIsNone(self.run_command(cmd))
        self.assertEqual(self.answers, ["once"])
        self.assertEqual(self.run_command(cmd), "Waiting for your okay")


class LateAllowSaysHowLate(unittest.TestCase):
    """L3: a late Allow says how late it is and asks the Zip to check the command still fits."""

    def card(self, ago):
        return {"reason": "Run a command that stops or restarts a service", "created": time.time() - ago}

    def test_the_note_names_the_delay(self):
        for ago, words in [(3 * 3600 + 200, "3 hours after you asked"), (3700, "an hour after you asked"),
                           (21 * 60, "20 minutes after you asked")]:
            with self.subTest(ago=ago):
                note = approvals.late_note(self.card(ago), True)
                self.assertIn(words, note)
                self.assertIn("Check that it still fits what you are doing now", note)
                self.assertIn("in the same folder", note)
                self.assertNotIn("again now", note)
        note = approvals.late_note(self.card(400), True)
        self.assertIn("Run exactly the same command again now", note)
        self.assertNotIn("after you asked", note)
        self.assertIn("Colin said not now", approvals.late_note(self.card(3 * 3600), False))


class LateAllowNoteThroughTheHub(HubCase):
    """L3 through the card endpoint: a three-hour-old card's Allow resumes the chat with the delay in the note."""

    def test_an_old_card(self):
        self.waiting = False
        self.cwd = HOME_FOLDER
        self.run_command("systemctl --user restart adguard")
        card = self.cards()[-1]
        with plugin.db() as c:
            c.execute("UPDATE requests SET created=? WHERE id=?", (time.time() - 3 * 3600, card["id"]))
        self.client.post(f"/api/signin-requests/{card['id']}", json={"decision": "once"})
        self.assertIn("3 hours after you asked", self.followed[-1][1])


class EventsAtVenues(GateCase):
    """M3: a person is invited only with an invite verb, an email address, or "add <Name> to" an event that already
    exists. A new event with a title, a day and a venue is an event, whatever the venue is called."""

    TITLES = [
        "Add Science Fair to Thursday at Lincoln Elementary", "Add Pumpkin Picking to Saturday at Alstede Farms",
        "Add Book Fair to Monday at Lakeview", "Add Fall Festival to Saturday in Ocean City",
        "Add Back to School Night to Thursday at 7 at the high school",
        "Add Farmers Market to Saturday morning at Pier Village", "Add Robotics Competition to Saturday at Rutgers",
        "Add Career Day to Friday at Lakeview University",
        "Add Science Fair to Thursday at Lincoln Elementary at 6pm", "Add Spirit Week to Monday through Friday at Lincoln",
        "Add Picture Day to October 14 at Lincoln Elementary", "Add Field Trip to Friday at the Franklin Institute",
        "Add Pumpkin Patch to tomorrow at Johnson Farm", "Add Movie Night to tonight at the Smiths",
        "Add Trunk or Treat to the 31st at Grace Church", "Add Science Fair to Thursday's schedule at Lincoln",
        "Add Band Concert to May 3 at the high school", "Add Book Fair to next Monday at Lakeview",
        "Add Choir Rehearsal to Wednesday at St. Mary's", "Add Open House to Tuesday 6pm at Lincoln Elementary",
        "Add Science Fair Dinner to Thursday at Lincoln Elementary",
        "Add Back to School Dinner to Thursday at the high school", "Add Lincoln Elementary Science Fair to Thursday",
        "Add Theo Avery to Book Club",
    ]
    INVITES = [
        ("Add Theo Avery to the Science Fair at Lincoln Elementary", "Theo Avery",
         "Invite Theo Avery to the Science Fair at Lincoln Elementary"),
        ("Add Riley to Thursday's sync at Lakeview", "Riley", "Invite Riley to Thursday's sync at Lakeview"),
        ("Add Jake to our Friday standup", "Jake", "Invite Jake to our Friday standup"),
        ("Add Matt to that call", "Matt", "Invite Matt to that call"),
        ("Add Riley to my dinner with Theo on Friday", "Riley", None),
        ("Add Emma to the Saturday practice at Alstede Farms", "Emma", None),
        ("Invite Theo to the Science Fair on Thursday", "Theo", "Invite Theo to the Science Fair on Thursday"),
        ("Add ken@example.com to Thursday at Lincoln Elementary", "ken@example.com", None),
        ("Send Riley an invite to Science Fair on Thursday", "Riley", None),
        ("Add Science Fair on Thursday at Lincoln Elementary and invite Theo Avery", "Theo Avery", None),
        ("Add Science Fair to Thursday at Lincoln Elementary with Riley as a guest", "Riley", None),
        ("Loop in Jake on the Science Fair planning call", "Jake", None),
    ]

    def test_new_events_at_venues_are_changes(self):
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

    def test_the_event_reference(self):
        for text in ["the Thursday meeting", "Theo's sync", "the board call", "dinner", "soccer practice on Saturday",
                     "Thursday's sync", "our Friday standup", "that call", "the Science Fair at Lincoln"]:
            with self.subTest(text=text):
                self.assertTrue(tiers._event_ref(text))
        for text in ["Thursday at Lincoln Elementary", "Saturday morning at Pier Village", "my calendar", "the gym",
                     "the library Tuesday", "October 14", "5pm", "tomorrow at the fair", "School Night to Thursday",
                     "Book Club", ""]:
            with self.subTest(text=text):
                self.assertFalse(tiers._event_ref(text))

    def test_the_report_case_raises_no_card_and_offers_no_rule(self):
        req = "Add Science Fair to Thursday at Lincoln Elementary"
        self.ask(req)
        self.assertIsNone(self.call("connectors", {"service": "calendar", "request": req}))
        self.assertEqual(self.cards(), [])
        self.assertEqual(plugin.recent_actions(1)[0]["how"], "tell")


class AssistantsStartedLater(GateCase):
    """L4: the remaining ways to start an assistant are refused, including ones scheduled to start it later, and any
    write to the user's crontab asks, since scheduled jobs belong in the Zips' own scheduler."""

    bot = "builder"
    REFUSED = [
        "find . -maxdepth 0 -exec claude -p hi \\;", "find . -execdir codex exec hi {} +", "npm exec -- claude -p hi",
        "pnpm exec claude -p hi", "yarn exec codex exec hi", "npm exec -c 'claude -p hi'", "npm x -- codex exec hi",
        "node ~/.npm-global/lib/node_modules/@anthropic-ai/claude-code/cli.js -p hi",
        "node /usr/lib/node_modules/@openai/codex/bin/codex.js exec hi",
        "(crontab -l; echo '* * * * * claude -p hi') | crontab -",
        "crontab - <<'EOF'\n0 7 * * * ~/.local/bin/claude -p brief\nEOF",
        "echo '@reboot codex exec hi' | crontab -", "printf '*/5 * * * * cd ~/taffy && taffy chat -q hi\\n' | crontab -",
        "cat > ~/.config/systemd/user/c.service <<'EOF'\n[Service]\nExecStart=/usr/bin/claude -p hi\nEOF",
        "printf '[Service]\\nExecStart=codex exec hi\\n' > ~/.config/systemd/user/c.service",
        "systemd-run --user --on-calendar=hourly claude -p hi", "echo 'claude -p hi' | at now + 1 hour",
        "docker run ghcr.io/openai/codex exec hi", "docker run --rm -it ghcr.io/openai/codex:latest exec hi",
        "podman run -e X=1 node:20 npx @anthropic-ai/claude-code -p hi", "docker exec web claude -p hi",
        "echo $(claude -p hi)",
    ]
    FINE = [
        "crontab -l", "crontab -l | grep backup", "find . -name '*.py' -exec grep -l claude {} \\;",
        "npm run claude-review", "npm exec eslint .", "docker run --name hermes nginx",
        "docker run -v ~/x:/x node:20 npm test", "docker compose logs hermes", "npx -y prettier --write .",
        "echo '* * * * * claude -p hi' > notes.txt", "cat > docs/cron.md <<'EOF'\n0 7 * * * claude -p brief\nEOF",
        "grep ExecStart /etc/systemd/system/nginx.service", "systemctl --user status adguard",
    ]
    CRONTAB_WRITES = ["(crontab -l; echo '0 3 * * * /usr/bin/backup.sh') | crontab -", "crontab -e",
                      "EDITOR=nano crontab -e", "VISUAL='sed -i s/x/y/' crontab -e", "crontab mycron.txt",
                      "crontab < jobs.txt", "echo '0 3 * * * ~/bin/backup.sh' | crontab -u jdoe -"]

    def test_refused(self):
        for cmd in self.REFUSED:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd, cwd=WORK)
                self.assertEqual(a.kind if a else None, "agent", a and a.summary)
        code = "import os\nos.spawnlp(os.P_WAIT, 'codex', 'codex', 'exec', 'hi')"
        self.assertEqual(tiers.classify_command(code, code=True).kind, "agent")
        code = "import os\nos.spawnv(os.P_NOWAIT, '/usr/local/bin/claude', ['claude', '-p', 'hi'])"
        self.assertEqual(tiers.classify_command(code, code=True).kind, "agent")

    def test_ordinary_commands_pass(self):
        for cmd in self.FINE:
            with self.subTest(cmd=cmd):
                self.assertIsNone(tiers.classify_command(cmd, cwd=WORK))

    def test_a_crontab_or_at_file_that_starts_an_assistant(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "jobs.txt").write_text("SHELL=/bin/bash\n# nightly\n15 2 * * * codex exec 'tidy up'\n")
        (folder / "job.sh").write_text("cd ~/taffy/work\nclaude -p 'write the report'\n")
        (folder / "backup.txt").write_text("0 3 * * * /usr/bin/backup.sh\n")
        for cmd in [f"crontab {folder}/jobs.txt", f"crontab < {folder}/jobs.txt", f"at now + 2 hours -f {folder}/job.sh",
                    "crontab jobs.txt"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(tiers.classify_command(cmd, cwd=str(folder)).kind, "agent")
        a = tiers.classify_command(f"crontab {folder}/backup.txt", cwd=str(folder))
        self.assertEqual(a.kind, "account")

    def test_writing_the_crontab_asks(self):
        for cmd in self.CRONTAB_WRITES:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd, cwd=WORK)
                self.assertEqual((a.kind, a.summary), ("account", "Change the jobs this computer runs on a schedule"))
        self.assertEqual(tiers.classify_command("crontab -r").summary, "Remove every job this computer runs on a schedule")
        self.ask("back up the database every night")
        blocked = self.call("terminal", {"command": self.CRONTAB_WRITES[0]})
        self.assertIn("cronjob tool", blocked["message"])
        card = self.cards()[-1]
        self.assertEqual((card["kind"], card["scope"]), ("account", ""))

    def test_the_refusal_raises_no_card(self):
        self.ask("check the build every hour")
        blocked = self.call("terminal", {"command": "(crontab -l; echo '0 * * * * claude -p check') | crontab -"})
        self.assertIn("Starting another assistant", blocked["message"])
        self.assertEqual(self.cards(), [])


class Nitpicks(unittest.TestCase):
    """From the report's nitpicks: a read-only Hermes command, and compose given the project's own file."""

    def test_hermes_cron_list_only_reads(self):
        self.assertIsNone(tiers.classify_command("hermes cron list"))
        self.assertEqual(tiers.classify_command("hermes cron create --every 1h x").kind, "agent")

    def test_compose_with_the_projects_own_file(self):
        detect = hermes_detector.detect
        self.assertIsNone(tiers.unjudged("docker compose -f ~/taffy/work/builder/site/compose.yml down", HOME_FOLDER,
                                         detect))
        self.assertIsNotNone(tiers.unjudged("docker compose -f ~/adguard/compose.yml down", WORK, detect))
        self.assertIsNotNone(tiers.unjudged("docker compose down", HOME_FOLDER, detect))


class CommandCardCopy(unittest.TestCase):
    """L3, in the app's source (repo only): the card stops promising five minutes once they have passed."""

    def setUp(self):
        if not (REPO / "alien").exists() or not (REPO / "ios").exists():
            self.skipTest("the app's source is not here")

    def test_wording_after_the_wait(self):
        text = (REPO / "ios" / "Taffy" / "Views" / "ChatView.swift").read_text()
        self.assertIn("stopped and is waiting for you. If you allow it, it picks this back up.", text)
        self.assertIn("commandWait: TimeInterval = 5 * 60", text)
        self.assertIn("TimelineView(.periodic(from: .now, by: 30))", text)



def setUpModule():
    import home_fixture
    home_fixture.pin(None)


def tearDownModule():
    import home_fixture
    home_fixture.unpin()

if __name__ == "__main__":
    unittest.main()


class OwnCalendarIsNotAnInvitation(GateCase):
    """Oct 10, Colin's live chat: Zero named his own calendar by its address, and the card asked to "Send calendar
    invitations to jdoe1@example.com". An event on the person's own calendar is a change, not a send."""

    def test_his_own_address_is_his_calendar(self):
        from unittest import mock
        req = ("On Colin's primary calendar (jdoe1@example.com), first list all events on Sunday, October 11, "
               "2026 from 12 p.m. to 11 p.m. (America/New_York). Then create one event on Sunday, October 11, 2026 "
               "from 4:00 p.m. to 10:00 p.m. titled \"School: BUS 305 Unit 6 paper, lectures and quiz\".")
        with mock.patch.object(tiers, "_own_addresses", return_value={"jdoe1@example.com"}):
            a = tiers.classify_connector("calendar", req)
            self.assertEqual(a.kind, "write", a.summary)
            b = tiers.classify_connector("calendar", req.replace("titled", "and invite ken@example.com, titled"))
            self.assertEqual((b.kind, b.target), ("send", "ken@example.com"))
