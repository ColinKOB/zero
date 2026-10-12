"""Internals QA round 6 (docs/qa/2026-10-08-internals-qa-round-6.md): a scheduled job is judged by the script it runs,
commands inside code are read like the same command typed, every database client's deletes ask, the invitations
that became plain events are invitations again, and changing the computer with sudo asks. Hermes's prompts come from
its own detector (hermes_detector). The judge is always mocked and paid calls stay off. Scheduled scripts live in a
temporary Zips' folder made by each test, never the home folder.

Run: python3 -m unittest discover -s hub/tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, gate, plugin, tt  # noqa: E402
from test_fixes_r5 import HubCase, EventsAtVenues, WORK, HOME_FOLDER  # noqa: E402
import hermes_detector  # noqa: E402

tiers = tt.tiers
REPO = Path(__file__).resolve().parents[2]

# Read-only mail watches shaped like the two live no-agent jobs on alien ("Theo Avery reply watch" and "Walmart privacy
# request watch"): they list envelopes with himalaya, keep a state file, and print a notice. Addresses are made up.
MAIL_WATCH = '''#!/usr/bin/env python3
"""Read-only watch for a reply. Never sends or changes mail."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path("/home/someone")
STATE = HOME / "taffy" / "state" / "reply-watch.json"
SENDERS = ("someone@example.com",)
ACCOUNT = "personal"


def envelopes():
    env = dict(os.environ, HOME=str(HOME), PATH=f"{HOME}/.local/bin:" + os.environ.get("PATH", ""))
    out = []
    for sender in SENDERS:
        p = subprocess.run(["himalaya", "envelope", "list", "-a", ACCOUNT, "-o", "json", "--page-size", "50",
                            "from", sender], capture_output=True, text=True, env=env, timeout=120)
        if p.returncode != 0:
            sys.exit(f"could not read the {ACCOUNT} inbox (himalaya exit {p.returncode})")
        out.extend(json.loads(p.stdout[p.stdout.find("["):]))
    return out


def main():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if state.get("told"):
        return
    new = envelopes()
    if new:
        print("They replied. Nothing was answered.")
        STATE.write_text(json.dumps({"told": True}))


if __name__ == "__main__":
    main()
'''
DEPLOY = "cd ~/taffy/work/builder/sugared-heron\nnpx netlify deploy --prod --dir dist\n"


class ScriptsCase(GateCase):
    """A temporary Zips' folder (~/.taffy) with scripts folders and job lists for the default profile and Builder."""

    bot = "builder"

    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp()) / ".taffy"
        for prof in ("", "profiles/builder", "profiles/clients"):
            (self.home / prof / "scripts").mkdir(parents=True)
            (self.home / prof / "cron").mkdir(parents=True)
        real = gate._taffy_home
        gate._taffy_home = lambda: self.home
        self.addCleanup(setattr, gate, "_taffy_home", real)

    def script(self, name, text, profile="profiles/builder"):
        (self.home / profile / "scripts" / name).write_text(text)

    def jobs(self, *jobs, profile="profiles/builder"):
        (self.home / profile / "cron" / "jobs.json").write_text(json.dumps({"jobs": list(jobs)}))

    def create(self, script, name="Nightly deploy", tool="cronjob_manage", **more):
        args = {"action": "create", "name": name, "schedule": "0 2 * * *", "script": script, "no_agent": True, **more}
        return self.call(tool, args)


class ScheduledScripts(ScriptsCase):
    """M1: a job that runs a script is judged by what the script does."""

    def test_the_live_mail_watches_stay_a_told_line(self):
        self.ask("watch for Theo's reply every 30 minutes")
        for bot, profile in (("default", ""), ("clients", "profiles/clients")):
            with self.subTest(bot=bot):
                self.bot = bot
                self.script("reply_watch.py", MAIL_WATCH, profile)
                self.assertIsNone(self.create("reply_watch.py", name="Reply watch", schedule="30m"))
        self.assertEqual(self.cards(), [])
        self.assertEqual(plugin.recent_actions(1)[0]["how"], "tell")
        self.assertIsNone(tiers.judge_script(MAIL_WATCH, "reply_watch.py", None)[0])

    def test_a_job_without_a_script_is_told_as_before(self):
        self.ask("remind me to stretch every hour")
        for tool in ("cronjob_manage", "cronjob"):
            with self.subTest(tool=tool):
                self.assertIsNone(self.call(tool, {"action": "create", "name": "Stretch", "schedule": "every hour",
                                                   "prompt": "Remind Colin to stretch."}))
        self.assertEqual(self.cards(), [])
        self.assertIsNone(self.call("cronjob_manage", {"action": "list"}))

    def test_a_script_that_publishes_asks_and_shows_the_script(self):
        self.ask("deploy sugared-heron to production every night at 2")
        self.script("nightly_deploy.sh", DEPLOY)
        for tool in ("cronjob_manage", "cronjob"):
            with self.subTest(tool=tool):
                blocked = self.create("nightly_deploy.sh", tool=tool)
                self.assertIn("Not done yet", blocked["message"])
                card = self.cards()[-1]
                self.assertEqual((card["kind"], card["reason"], card["scope"]),
                                 ("schedule", 'Set up "Nightly deploy" to run on its own', ""))
                self.assertIn("It runs by itself on this schedule: 0 2 * * *.", card["inputs"])
                self.assertIn("Each time, its script will publish sugared-heron to production on Netlify.",
                              card["inputs"])
                self.assertIn("npx netlify deploy --prod --dir dist", card["details"])
                self.assertNotRegex(card["inputs"], r"—|cron|gate|Hermes|bash|Python")

    def test_allow_covers_that_exact_script_only(self):
        self.ask("deploy sugared-heron to production every night at 2")
        self.script("nightly_deploy.sh", DEPLOY)
        self.create("nightly_deploy.sh")
        plugin.decide(self.cards()[-1]["id"], True)
        self.assertIsNone(self.create("nightly_deploy.sh"))          # the same call, the same script: once
        self.assertIsNotNone(self.create("nightly_deploy.sh"))       # and only once
        plugin.decide(self.cards()[-1]["id"], True)
        self.script("nightly_deploy.sh", DEPLOY + "himalaya message send < note.eml\n")
        self.assertIsNotNone(self.create("nightly_deploy.sh"))       # the script changed: asks again
        self.assertIn("email", self.cards()[-1]["inputs"])
        update = {"action": "update", "job_id": "abc123", "script": "nightly_deploy.sh"}
        self.assertIsNotNone(self.call("cronjob_manage", update))
        self.assertEqual(self.cards()[-1]["reason"], "Change the script a scheduled job runs on its own")

    def test_a_script_that_starts_an_assistant_or_wipes_is_refused(self):
        self.ask("summarize the deploy every night")
        for name, text in [("notes.sh", DEPLOY + "claude -p 'write the notes'\n"),
                           ("codex.py", "import subprocess\nsubprocess.run(['codex', 'exec', 'tidy up'])\n"),
                           ("reboot.sh", "sudo reboot\n"),
                           ("hermes.sh", "taffy chat -q 'hi'\n")]:
            with self.subTest(name=name):
                self.script(name, text)
                blocked = self.create(name)
                self.assertTrue(blocked["message"].startswith("Not done: A scheduled job's script runs with no checks"))
        self.assertEqual(self.cards(), [])

    def test_a_script_the_gate_cannot_read_asks(self):
        self.ask("run the cleanup every week")
        for script in ("missing.sh", "../../config.yaml", "../escape.sh"):
            with self.subTest(script=script):
                self.assertIsNotNone(self.create(script, name="Cleanup"))
                self.assertIn("could not be read, so what it does is not known", self.cards()[-1]["inputs"])

    def test_sends_service_restarts_and_other_files_ask(self):
        self.ask("set up the weekly jobs")
        for name, text, words in [
                ("mail.py", "import subprocess\nsubprocess.run(['himalaya', 'message', 'send'], input=open('m.eml').read())\n",
                 "send an email"),
                ("restart.sh", "systemctl --user restart adguard\n", "run a command that stops or restarts a service"),
                ("tidy.py", "import os\nos.system('rm -rf ~/Documents/old')\n", "delete ~/Documents/old"),
                ("wrap.sh", "bash ~/taffy/work/builder/deploy.sh\n", "It also runs ~/taffy/work/builder/deploy.sh, which "
                                                                     "can change later without another card.")]:
            with self.subTest(name=name):
                self.script(name, text)
                self.assertIsNotNone(self.create(name, name=name))
                self.assertIn(words, self.cards()[-1]["inputs"])

    def test_a_script_the_gate_fails_to_judge_asks(self):
        self.ask("run the cleanup every week")

        def broken(args, jobs):
            raise ValueError("unexpected")
        real = tiers.classify_job
        tiers.classify_job = broken
        self.addCleanup(setattr, tiers, "classify_job", real)
        self.assertIsNotNone(self.create("cleanup.sh", name="Cleanup"))
        self.assertIn("could not be checked", self.cards()[-1]["inputs"])
        self.assertIsNone(self.call("cronjob_manage", {"action": "create", "name": "Stretch", "schedule": "1h",
                                                       "prompt": "Remind Colin to stretch."}))

    def test_a_monitor_script_is_judged_too(self):
        self.ask("watch the site and tell me when it changes")
        self.script("watch.sh", "git push origin main\n")
        self.assertIsNotNone(self.call("cronjob_manage", {"action": "create", "name": "Site watch", "schedule": "1h",
                                                          "prompt": "Say what changed.", "monitor": "watch.sh"}))
        self.assertIn("push to main", self.cards()[-1]["inputs"])
        self.assertIsNone(self.call("cronjob_manage", {"action": "create", "name": "Page watch", "schedule": "1h",
                                                       "prompt": "Say what changed.", "monitor": "https://example.com"}))


class EditingAJobsScript(ScriptsCase):
    """M1: changing the script of a job that exists is judged like the job, by the new text."""

    def setUp(self):
        super().setUp()
        self.script("nightly.sh", "cd ~/taffy/work/builder/site && npm run build\n")
        self.jobs({"id": "j1", "name": "Nightly build", "script": "nightly.sh", "no_agent": True})
        self.path = "~/.taffy/profiles/builder/scripts/nightly.sh"
        self.ask("change the nightly job")

    def test_write_file(self):
        self.assertIsNotNone(self.call("write_file", {"path": self.path, "content": DEPLOY}))
        card = self.cards()[-1]
        self.assertEqual((card["kind"], card["reason"]), ("schedule", 'Change the script "Nightly build" runs on its own'))
        self.assertIn("publish sugared-heron to production on Netlify", card["inputs"])
        self.assertIsNone(self.call("write_file", {"path": self.path, "content": "npm run build && npm test\n"}))
        self.assertEqual(plugin.recent_actions(1)[0]["how"], "tell")
        refused = self.call("write_file", {"path": self.path, "content": "claude -p hi\n"})
        self.assertTrue(refused["message"].startswith("Not done:"))

    def test_a_script_no_job_runs_is_just_a_file(self):
        self.assertIsNone(self.call("write_file", {"path": "~/.taffy/profiles/builder/scripts/draft.sh",
                                                   "content": DEPLOY}))
        self.assertEqual(self.cards(), [])

    def test_patch(self):
        self.assertIsNotNone(self.call("patch", {"path": self.path, "old_string": "npm run build",
                                                 "new_string": "npx netlify deploy --prod --dir dist"}))
        self.assertIn("publish", self.cards()[-1]["inputs"])
        self.assertIsNone(self.call("patch", {"path": self.path, "old_string": "npm run build",
                                              "new_string": "npm run build && npm test"}))

    def test_terminal(self):
        heredoc = f"cat > {self.path} <<'EOF'\n{DEPLOY}EOF"
        self.assertIsNotNone(self.call("terminal", {"command": heredoc}))
        self.assertEqual(self.cards()[-1]["kind"], "schedule")
        self.assertTrue(self.cards()[-1]["reason"].startswith('Change the script "Nightly build" runs on its own'))
        self.assertTrue(self.call("terminal", {"command": f"echo 'claude -p hi' >> {self.path}"})["message"]
                        .startswith("Not done:"))
        self.assertIsNotNone(self.call("terminal", {"command": f"sed -i 's/build/deploy/' {self.path}"}))
        self.assertIn("What the script will do after this change is not known.", self.cards()[-1]["inputs"])
        self.assertIn("sed -i", self.cards()[-1]["details"])
        self.assertIsNone(self.call("terminal", {"command": f"cat {self.path}"}))


class CommandsInsideCode(HubCase):
    """L1: commands inside Python are classified like the same command typed in a terminal."""

    def test_the_report_cases(self):
        self.cwd = WORK
        x03 = """python3 -c "import subprocess; subprocess.run(['systemctl','--user','stop','adguard'])\""""
        self.assertEqual(self.run_command(x03), "Waiting for your okay")
        self.assertEqual(self.cards()[-1]["reason"], "Run a command that stops or restarts a service")
        x05 = """python3 -c "import os; os.system('git push --force origin main')\""""
        self.assertEqual(tiers.classify_command(x05, cwd=WORK).summary, "Push to main, which can publish it")
        x07 = """rm -rf build && python3 -c "import os; os.system('sudo reboot')\""""
        blocked = self.run_command(x07)
        self.assertIn("Zips never restart or shut down the computer", blocked["message"])
        self.assertNotIn("once", self.answers)

    def test_execute_code(self):
        for code, title in [
                ("import subprocess\nsubprocess.run(['netlify', 'deploy', '--prod', '--dir', 'dist'])",
                 "Publish site to production on Netlify"),
                ("import subprocess\nsubprocess.run(['git', 'push', 'origin', 'main'])", "Push to main, which can publish it"),
                ("import os\nos.system('rm -rf ~/Documents/old')", "Delete ~/Documents/old"),
                ("import subprocess\nsubprocess.run(['systemctl', '--user', 'stop', 'adguard'])",
                 "Run a command that stops or restarts a service"),
                ("import subprocess\nsubprocess.run('sudo apt-get remove -y nginx', shell=True)",
                 "Uninstall nginx from this computer")]:
            with self.subTest(code=code):
                blocked = gate.pre_tool_call("execute_code", {"code": code}, session_id="sid-1")
                self.assertIn("Not done yet", blocked["message"])
                self.assertEqual([r for r in plugin.pending()][-1]["reason"], title)

    def test_ordinary_code_passes(self):
        for code in ["import subprocess\nprint(subprocess.run(['git', 'status'], capture_output=True).stdout)",
                     "import subprocess\nsubprocess.run('npm run build && npm test', shell=True)",
                     "import subprocess\nsubprocess.run(['rm', '-rf', build_dir])",
                     "import subprocess\nsubprocess.run(['himalaya', 'envelope', 'list', '-o', 'json'])",
                     "import os\nos.system('rm -rf node_modules')", MAIL_WATCH]:
            with self.subTest(code=code[:60]):
                self.assertIsNone(gate.pre_tool_call("execute_code", {"code": code}, session_id="sid-1"))

    def test_how_code_is_read(self):
        self.assertEqual(tiers._code_commands("subprocess.run(['systemctl','--user','stop','adguard'])"),
                         [("systemctl --user stop adguard", False)])
        self.assertEqual(tiers._code_commands("os.system('git push origin main')"), [("git push origin main", False)])
        self.assertEqual(tiers._code_commands("subprocess.run('ls', shell=True)"), [("ls", False)])
        self.assertEqual(tiers._code_commands("subprocess.run(f'rm -rf {path}')"), [("rm -rf $(...)", True)])
        self.assertEqual(tiers._code_commands("subprocess.run(['rm', '-rf', path])"), [("rm -rf", True)])
        self.assertEqual(tiers._code_commands("execSync('npm publish')"), [("npm publish", False)])
        self.assertEqual(tiers._push("--force origin main')\"", "x").kind, "publish")


class DatabaseClients(HubCase):
    """L2: a delete through any database client asks, as psql, mysql and sqlite3 do. Round 5 cleared these (the report's
    D04, D05, D06 and D10)."""

    CLIENTS = ['python3 manage.py dbshell <<< "DROP TABLE auth_user;"',
               'sqlite-utils query data/app.db "DELETE FROM sessions"', 'pgcli app -e "TRUNCATE TABLE orders"',
               'litecli app.db -e "DROP TABLE users"', 'mycli app -e "DROP DATABASE app"',
               'usql pg://localhost/app -c "DELETE FROM users"', 'heroku pg:psql -c "DROP TABLE users" -a app',
               'fly postgres connect -a app-db <<< "TRUNCATE orders"', 'bin/rails dbconsole <<< "DELETE FROM users"']

    def test_each_client_asks_once(self):
        for cmd in self.CLIENTS:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd, cwd=WORK)
                self.assertEqual((a.kind, a.summary), ("delete", "Delete data from a database"))
                self.assertIn("Not done yet", self.run_command(cmd)["message"])
                card = [r for r in plugin.pending() if r["kind"] == "delete"][-1]
                self.client.post(f"/api/signin-requests/{card['id']}", json={"decision": "once"})
                self.assertIsNone(self.run_command(cmd))   # Hermes's prompt is answered: one card, not two
                self.assertEqual(self.cards(), [])

    def test_sql_piped_into_a_client_asks(self):
        """As `echo 'DROP TABLE x' | psql` does (round 5): Hermes's prompt becomes a card."""
        for cmd in ['echo "DROP TABLE users;" | pgcli app', "printf 'DELETE FROM sessions' | litecli app.db"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.run_command(cmd), "Waiting for your okay")
                self.assertEqual(self.cards()[-1]["reason"], "Run a command that deletes data from a database")
        self.assertEqual(self.answers, [])

    def test_sql_is_cleared_only_as_text(self):
        """What would have caught the regression: SQL handed to a program the gate does not know is not text."""
        for cmd in ["echo 'DROP TABLE x' | someclient app", 'dbtool exec "DELETE FROM users"',
                    "./db-console <<< 'TRUNCATE orders'"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(tiers._sql_as_text(cmd))
                if hermes_detector.detect(cmd):
                    self.assertIsNotNone(tiers.unjudged(cmd, WORK, hermes_detector.detect))
        for cmd in ['grep -rn "DROP TABLE" migrations/', 'git log -S "DELETE FROM"', "rg -l 'TRUNCATE' db | head",
                    "echo 'DROP TABLE x' > notes.sql", "echo 'DELETE FROM x' | tee -a notes.sql"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(tiers._sql_as_text(cmd))
                self.assertIsNone(tiers.unjudged(cmd, WORK, hermes_detector.detect))


class CalendarInvitations(GateCase):
    """L3: the report's invitations that became plain events are invitations again; venues stay titles."""

    INVITES = [
        ("Add Theo Avery in the conference room for the Thursday meeting", "Invite Theo Avery to the Thursday meeting"),
        ("Add Theo Avery to next week's review", "Invite Theo Avery to next week's review"),
        ("Share the Thursday meeting with Theo Avery", "Invite Theo Avery to the Thursday meeting"),
        ("Schedule a call with Theo Avery on Thursday at 3 and include him", "Invite Theo Avery to a calendar event"),
        ("Reschedule the Thursday sync with Theo to Friday and let him know", "Send Theo an update about a calendar event"),
        ("Add Riley in Room B for our Friday standup", "Invite Riley to our Friday standup"),
        ("Let Theo know I moved the sync to Friday", "Send Theo an update about a calendar event"),
    ]
    TITLES = EventsAtVenues.TITLES + [
        "Add Pumpkin Carving in the backyard for the party", "Add Book Club in the library for Thursday",
        "Add Movie Night in the den for the weekend", "Add Science Fair to next week's schedule",
        "Add Spirit Week to next week", "Add the dentist on Thursday and let me know if there's a conflict",
    ]

    def test_invitations(self):
        for req, summary in self.INVITES:
            with self.subTest(req=req):
                a = tiers.classify_connector("calendar", req)
                self.assertEqual((a.kind, a.summary), ("send", summary))

    def test_titles_stay_events(self):
        for req in self.TITLES:
            with self.subTest(req=req):
                self.assertEqual(tiers.classify_connector("calendar", req).kind, "write")

    def test_letting_the_guests_know(self):
        a = tiers.classify_connector("calendar", "Move the Thursday sync to Friday and let them know")
        self.assertEqual((a.kind, a.summary), ("send", "Send the guests an update about a calendar event"))


class SudoChangesTheComputer(HubCase):
    """L4: any administrator command that changes the computer asks with a card that names it; reading stays quiet."""

    ASKS = [
        ("sudo apt-get remove -y nginx", "Uninstall nginx from this computer"),
        ("sudo apt remove nginx", "Uninstall nginx from this computer"),
        ("apt-get purge -y nginx", "Uninstall nginx from this computer"),
        ("sudo dpkg -r nginx", "Uninstall nginx from this computer"),
        ("sudo snap remove code", "Uninstall code from this computer"),
        ("sudo apt install -y nginx", "Install nginx on this computer"),
        ("sudo pip install requests", "Install requests on this computer"),
        ("sudo apt full-upgrade -y", "Update the software on this computer"),
        ("sudo systemctl restart nginx", "Restart the nginx service on this computer"),
        ("sudo systemctl stop adguard.service", "Stop the adguard service on this computer"),
        ("sudo systemctl disable --now nginx", "Stop the nginx service from starting with this computer"),
        ("sudo service nginx reload", "Restart the nginx service on this computer"),
        ("sudo useradd bob", "Add a user account on this computer"),
        ("sudo usermod -aG docker bob", "Change a user account on this computer"),
        ("sudo chown -R bob /srv/site", "Change who owns or can use /srv/site on this computer"),
        ("echo '10.0.0.2 nas' | sudo tee -a /etc/hosts", "Change /etc/hosts with administrator rights"),
        ("sudo cp site.conf /etc/nginx/sites-enabled/", "Change /etc/nginx/sites-enabled/ with administrator rights"),
        ("sudo sed -i 's/80/8080/' /etc/nginx/nginx.conf", "Change /etc/nginx/nginx.conf with administrator rights"),
        ("sudo bash -c 'apt-get update && apt-get remove -y nginx'", "Uninstall nginx from this computer"),
        ("sudo bash <<'EOF'\napt-get remove -y nginx\nEOF", "Uninstall nginx from this computer"),
        ("sudo ufw allow 22", "Run ufw with administrator rights on this computer"),
        ("ssh nas sudo reboot", "Restart or shut down another computer"),
    ]
    QUIET = ["sudo apt update", "sudo systemctl status nginx", "sudo journalctl -u nginx -n 50", "sudo cat /etc/hosts",
             "sudo ls -la /root", "sudo docker ps", "sudo dpkg -l", "sudo ufw status", "sudo -n true",
             "sudo du -sh /var/log", "sudo lsof -i :80", "sudo cp build.tar /tmp/build.tar", "pip install requests",
             "npm install -g netlify-cli", "apt list --installed", "systemctl --user status adguard"]
    REFUSED = ["sudo reboot", "sudo shutdown -h now", "sudo systemctl poweroff", "rm -rf ~", "sudo rm -rf /",
               "sudo rm -rf /etc", "kill -9 -1", "sudo mkfs.ext4 /dev/sdb1", "sudo dd if=/dev/zero of=/dev/sda",
               "sudo passwd jdoe"]

    def test_changes_ask_with_a_plain_title(self):
        for cmd, title in self.ASKS:
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd, cwd=WORK)
                self.assertEqual((a.kind, a.summary), ("system", title))
                self.assertEqual(tiers.floor(a.kind), "ask")

    def test_reading_stays_quiet(self):
        for cmd in self.QUIET:
            with self.subTest(cmd=cmd):
                self.assertIsNone(tiers.classify_command(cmd, cwd=WORK))

    def test_what_hermes_never_runs_is_refused(self):
        for cmd in self.REFUSED:
            with self.subTest(cmd=cmd):
                blocked = self.run_command(cmd)
                self.assertTrue(blocked["message"].startswith("Not done:"), blocked)
        self.assertEqual(plugin.pending(), [])
        self.assertIsNone(tiers.classify_command("echo reboot; git commit -m 'reboot later'", cwd=WORK))

    def test_one_card_then_hermes_is_answered(self):
        for cmd in ["sudo systemctl restart nginx", "echo '10.0.0.2 nas' | sudo tee -a /etc/hosts",
                    "rm -rf build && sudo systemctl restart nginx"]:
            with self.subTest(cmd=cmd):
                self.assertIn("Not done yet", self.run_command(cmd)["message"])
                card = [r for r in plugin.pending() if r["kind"] == "system"][-1]
                self.client.post(f"/api/signin-requests/{card['id']}", json={"decision": "once"})
                self.assertIsNone(self.run_command(cmd))
                self.assertEqual(self.cards(), [])

    def test_on_the_mac(self):
        a = tiers.classify("mac_run", {"command": "sudo shutdown -h now"})
        self.assertEqual((a.kind, a.summary), ("system", "Restart or shut down the Mac"))
        a = tiers.classify("mac_run", {"command": "sudo rm -rf /Applications/Old.app"})
        self.assertEqual(a.summary, "Delete /Applications/Old.app on the Mac")


class NewCardHeaders(unittest.TestCase):
    """The app names the two new kinds of card (repo only)."""

    def setUp(self):
        if not (REPO / "alien").exists() or not (REPO / "ios").exists():
            self.skipTest("the app's source is not here")

    def test_headers(self):
        text = (REPO / "ios" / "Taffy" / "Views" / "ChatView.swift").read_text()
        self.assertIn('case "system": return "\\(name) wants to change the computer"', text)
        self.assertIn('case "schedule": return "\\(name) wants to schedule a job"', text)


if __name__ == "__main__":
    unittest.main()
