"""Internals QA round 8 (docs/qa/2026-10-08-internals-qa-round-8.md): git with its own options before the
subcommand is read like the plain command by every git rule; a push goes where git's own settings send it; and the
hub hides the note beside a carded call only when the turn's later reply already says all of it.

The judge is never asked. Nothing here reads the home folder or the settings files of whoever runs the tests (git's
settings files are a stand-in home's), needs Hermes, or depends on the folder the tests run from. No git command is
run.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, plugin, tt  # noqa: E402
import hermes_detector  # noqa: E402
from taffy_hub import consent as hub_consent  # noqa: E402
from taffy_hub import hermes as hub_hermes  # noqa: E402
from taffy_hub import history  # noqa: E402

tiers = tt.tiers
GITHUB = "https://github.com/colin/site.git"


class StandInHome:
    """A temporary home folder for the gate (tiers._HOME), with git's settings files read only from it."""

    def make_home(self) -> str:
        home = tempfile.mkdtemp(dir="/tmp", prefix="zero-r8-")
        self.addCleanup(shutil.rmtree, home, True)
        pin = tt.home.pinned(tt.home.colin_settings())
        pin.__enter__()
        self.addCleanup(pin.__exit__, None, None, None)
        for name, value in (("_HOME", home), ("_git_files", lambda: [os.path.join(home, ".gitconfig")])):
            patcher = mock.patch.object(tiers, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return home

    def repo(self, folder: str, config: str) -> str:
        os.makedirs(os.path.join(folder, ".git"), exist_ok=True)
        with open(os.path.join(folder, ".git", "config"), "w") as f:
            f.write(config)
        return folder


class GitOptions(StandInHome, GateCase):
    """`git -c k=v push`, `git --no-pager push`, `git -C dir push` and the rest are the commands they look like."""

    FORMS = {
        "git push origin main": [
            "git -c core.askPass=true push origin main", "git --no-pager push origin main", "git -P push origin main",
            "git -p push origin main", "git --paginate push origin main", "git -C . -c a=b push origin main",
            "git --work-tree=. push origin main", "git --work-tree . push origin main",
            'git -c http.extraHeader="Authorization: Bearer abc" push origin main', "git --git-dir=.git push origin main",
            "git --git-dir .git push origin main", "git --namespace=x push origin main", "git --namespace x push origin main",
            "git --exec-path=/usr/lib/git-core push origin main", "git --exec-path push origin main",
            "git --literal-pathspecs --no-optional-locks --bare push origin main", "git --no-replace-objects push origin main",
            "git --config-env=user.name=NAME push origin main", "git -c a=b -c c=d --no-pager -P push origin main",
            "GIT_PAGER=cat git --no-pager push origin main", "env GIT_TERMINAL_PROMPT=0 git -c a=b push origin main",
            "nohup git --no-pager push origin main &",
            "command git -c a=b push origin main", "/usr/bin/git -c a=b push origin main", "time git -P push origin main",
            "git -c user.name=Builder -c user.email=b@x commit -am fix && git -c user.name=Builder push origin main",
            "bash -c 'git --no-pager push origin main'",
            "python3 -c \"import subprocess; subprocess.run(['git', '-c', 'a=b', 'push', 'origin', 'main'])\"",
        ],
        "git push --force origin main": ["git -c a=b push --force origin main", "git --no-pager push -f origin main"],
        "git push origin --delete main": ["git -c a=b push origin --delete main", "git -P push --delete origin main"],
        "git push origin :main": ["git -P push origin :main"],
        "sudo -u jdoe git push origin main": ["sudo -u jdoe git -c a=b push origin main"],
        "git push origin --delete v1": ["git -c a=b push origin --delete v1"],
        "git push origin :refs/tags/v1": ["git --no-pager push origin :refs/tags/v1"],
        "git push --tags": ["git --no-pager push --tags", "git -c a=b push origin --tags"],
        "git push --mirror": ["git -c a=b push --mirror"],
        "git push": ["git -c a=b push", "git --no-pager push origin"],
        "git push origin feat": ["git -c a=b push origin feat", "git -C . push origin feat"],
        "git push origin HEAD": ["git -c a=b push origin HEAD"],
    }
    OUTSIDE = {
        "git reset --hard": ["git -c a=b reset --hard", "git --no-pager reset --hard HEAD~1", "git -c a=b reset --hard origin/main"],
        "git clean -fdx": ["git -c a=b clean -fdx", "git --no-pager clean -f -d"],
        "git branch -D old": ["git -c a=b branch -D old", "git -P branch -D old"],
        "git branch --delete --force old": ["git --no-pager branch --delete --force old"],
        "git push --force origin feat": ["git -c a=b push --force origin feat"],
    }

    def setUp(self):
        super().setUp()
        self.home = self.make_home()
        self.site = self.repo(os.path.join(self.home, "Projects", "site"), f'[remote "origin"]\n\turl = {GITHUB}\n')
        self.work = self.repo(os.path.join(self.home, "taffy", "work", "builder", "site"),
                              f'[remote "origin"]\n\turl = {GITHUB}\n')
        self.in_site, self.in_work = "~/Projects/site", "~/taffy/work/builder/site"

    def same(self, a, b):
        return (a.kind, a.summary, a.target) if a else None, (b.kind, b.summary, b.target) if b else None

    def asks_for(self, plain: str, cwd: str) -> list[str]:
        """The card titles a command gets: the gate's own card, and Hermes's check through the hub."""
        a = tiers.classify_command(plain, cwd=cwd)
        titles = [a.summary] if a is not None and tiers.floor(a.kind) == "ask" else []
        flagged = tiers.unjudged(plain, cwd, hermes_detector.detect) if hermes_detector.detect(plain) else None
        return titles + ([plugin.plain_command(flagged, plain)] if flagged else [])

    def test_pushes_read_like_the_plain_push(self):
        for plain, forms in self.FORMS.items():
            want = tiers.classify_command(plain, cwd=self.in_site)
            self.assertIsNotNone(want, plain)
            for form in forms:
                with self.subTest(form=form):
                    got = tiers.classify_command(form, cwd=self.in_site)
                    self.assertIsNotNone(got)
                    if "commit -am" in form or "python3" in form:
                        self.assertEqual((got.kind, got.summary), (want.kind, want.summary))
                    else:
                        self.assertEqual(*self.same(got, want))

    def test_the_report_cases_ask(self):
        for form, summary in (("git -c core.askPass=true push origin main", "Push to main, which can publish it"),
                              ("git --no-pager push origin main", "Push to main, which can publish it"),
                              ("git -c a=b push origin --delete main", "Delete a branch on GitHub"),
                              ("git --no-pager push --tags", "Push every branch or tag"),
                              ("git -c a=b push", "Push code without naming the branch")):
            with self.subTest(form=form):
                a = tiers.classify_command(form, cwd=self.in_site)
                self.assertEqual(a.summary, summary)
                self.assertEqual(tiers.floor(a.kind), "ask")

    def test_rules_outside_the_work_folder_ask_with_the_plain_forms_title(self):
        for plain, forms in self.OUTSIDE.items():
            want = self.asks_for(plain, self.in_site)
            self.assertTrue(want, plain)
            for form in forms:
                with self.subTest(form=form):
                    a = tiers.classify_command(form, cwd=self.in_site)
                    self.assertEqual(tiers.floor(a.kind), "ask")
                    for title in want:
                        self.assertIn(title[1:], a.summary)

    def test_inside_the_work_folder_they_stay_quiet_like_the_plain_forms(self):
        for plain, forms in self.OUTSIDE.items():
            if "push" in plain:
                continue
            self.assertEqual(self.asks_for(plain, self.in_work), [], plain)
            for form in forms:
                with self.subTest(form=form):
                    self.assertIsNone(tiers.classify_command(form, cwd=self.in_work))
                    self.assertIsNone(tiers.unjudged(form, self.in_work, hermes_detector.detect))

    def test_the_folder_comes_from_c_git_dir_and_work_tree(self):
        work, site, in_site, in_work = self.work, self.site, self.in_site, self.in_work
        for cmd, cwd in ((f"git -C {work} -c a=b reset --hard", in_site), (f"git -c a=b -C {work} branch -D old", in_site),
                         (f"git -C {self.home}/taffy/work -C builder/site --no-pager clean -fd", in_site),
                         (f"git --git-dir={work}/.git --work-tree={work} reset --hard", in_site),
                         (f"GIT_DIR={work}/.git git branch -D old", in_work)):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.asks_for(cmd, cwd), [])
        for cmd, cwd in ((f"git -C {site} -c a=b reset --hard", in_work), (f"git -C {site} reset --hard", in_work),
                         (f"git --work-tree={self.home}/Documents reset --hard", in_work),
                         ("git --work-tree=~/Documents checkout . && git --work-tree ~/Documents reset --hard", in_work),
                         (f"git --git-dir={site}/.git branch -D old", in_work),
                         (f"GIT_WORK_TREE={self.home}/Documents git reset --hard", in_work),
                         (f"env GIT_DIR={site}/.git git branch -D old", in_work),
                         (f"git --work-tree={self.home}/Documents clean -fdx", in_work),
                         ("git --work-tree=$TREE reset --hard", in_work), ("git --git-dir=$REPO branch -D old", in_work),
                         ("git -C $DIR -c a=b reset --hard", in_work), ("git -c a=b reset --hard", None)):
            with self.subTest(cmd=cmd):
                self.assertTrue(self.asks_for(cmd, cwd))

    def test_a_push_reads_the_repository_git_is_pointed_at(self):
        scratch = self.repo(os.path.join(self.home, "taffy", "work", "builder", "mirror"),
                            f'[remote "origin"]\n\turl = {self.home}/taffy/work/builder/mirror/remote.git\n')
        for cmd in (f"git -C {scratch} -c a=b push --force origin main", f"git --git-dir={scratch}/.git push origin main",
                    f"GIT_DIR={scratch}/.git git push origin main", f"cd {scratch} && git --no-pager push origin main"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(tiers.classify_command(cmd, cwd=self.in_site))
        for cmd in (f"git -C {self.site} push origin main", f"git --git-dir={self.site}/.git push origin main",
                    f"GIT_DIR={self.site}/.git git -c a=b push origin main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(tiers.classify_command(cmd, cwd="~/taffy/work/builder/mirror").summary,
                                 "Push to main, which can publish it")

    def test_hermes_is_not_asked_twice(self):
        for forms in self.OUTSIDE.values():
            for form in forms:
                with self.subTest(form=form):
                    self.assertIsNone(tiers.unjudged(form, self.in_site, hermes_detector.detect))
                    self.assertIsNone(tiers.unjudged(form + " && sudo true", self.in_site, hermes_detector.detect))

    def test_through_the_gate(self):
        self.ask("tidy up the site repo")
        blocked = self.call("terminal", {"command": f"cd {self.site} && git -c a=b reset --hard"})
        self.assertIsNotNone(blocked)
        self.assertEqual(self.cards()[0]["reason"], "Run a command that throws away code changes that are not saved")
        self.assertIsNone(self.call("terminal", {"command": f"cd {self.work} && git -c a=b reset --hard"}))

    def test_code_and_scheduled_scripts(self):
        detect = hermes_detector.detect
        for code in ("import subprocess\nsubprocess.run(['git', '-c', 'a=b', 'reset', '--hard'])",
                     "import os\nos.system('git --no-pager branch -D old')",
                     "import subprocess\nsubprocess.run(['git', '--no-pager', 'push', 'origin', 'main'])"):
            with self.subTest(code=code):
                a = tiers.classify_command(code, code=True, detect=detect)
                self.assertIsNotNone(a)
                self.assertEqual(tiers.floor(a.kind), "ask")

    def test_aliases(self):
        self.assertEqual(tiers.classify_command("git -c alias.ship=push ship origin main", cwd=self.in_site).summary,
                         "Push to main, which can publish it")
        self.assertEqual(tiers.classify_command("git -c alias.wipe='reset --hard' wipe", cwd=self.in_site).summary,
                         "Run a command that throws away code changes that are not saved")
        aliased = self.repo(os.path.join(self.home, "Projects", "aliased"),
                            f'[remote "origin"]\n\turl = {GITHUB}\n[alias]\n\tship = push origin main\n\tpush = status\n'
                            '\tnuke = !rm -rf ~/Documents\n\tquiet = !echo hi\n')
        in_aliased = "~/Projects/aliased"
        self.assertEqual(tiers.classify_command("git ship", cwd=in_aliased).summary, "Push to main, which can publish it")
        self.assertEqual(tiers.classify_command("git nuke", cwd=in_aliased).target, "~/Documents")
        self.assertIsNone(tiers.classify_command("git quiet", cwd=in_aliased))
        self.assertIsNone(tiers.classify_command("git -c alias.x='!rm -rf ~/Documents' status", cwd=in_aliased))
        # git never lets an alias replace its own commands.
        self.assertEqual(tiers.classify_command("git push origin main", cwd=in_aliased).summary,
                         "Push to main, which can publish it")

    def test_settings_that_run_a_command_are_judged_as_that_command(self):
        for cmd in ("git -c core.sshCommand='rm -rf ~/Documents' fetch", "git -c alias.x='!rm -rf ~/Documents' x",
                    "git -c core.pager='rm -rf ~/Documents' log"):
            with self.subTest(cmd=cmd):
                a = tiers.classify_command(cmd, cwd=self.in_site)
                self.assertEqual((a.kind, a.target), ("delete", "~/Documents"))
        self.assertIsNone(tiers.classify_command("git -c core.sshCommand='ssh -i ~/.ssh/deploy' fetch", cwd=self.in_work))

    def test_ordinary_git_stays_quiet(self):
        for cmd in ("git -c a=b status", "git --no-pager log --oneline -5", "git --no-pager diff",
                    "git -c user.name=Builder commit -m 'push the fix to main'", "git -P log -S 'DROP TABLE x'",
                    "git --no-pager branch -d merged", "git -c a=b fetch origin", "git --no-pager show HEAD",
                    "git -C . -c a=b tag v1", "git --no-pager stash list"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(tiers.classify_command(cmd, cwd=self.in_site))

    def test_the_parser(self):
        g = tiers._git_call(["git", "-C", "a", "-C", "b", "-c", "User.Name=B", "--no-pager", "--git-dir=.git",
                             "push", "origin", "main"], "/tmp/x")
        self.assertEqual((g.sub, g.args, g.where, g.repo), ("push", ["origin", "main"], "/tmp/x/a/b", "/tmp/x/a/b/.git"))
        self.assertEqual(g.config, [("user.name", "B")])
        self.assertTrue(g.options)
        self.assertEqual(g.plain, "git push origin main")
        g = tiers._git_call(["GIT_WORK_TREE=/srv/w", "git", "reset", "--hard"], "/tmp/x")
        self.assertEqual((g.folders, g.options), (["/tmp/x", "/srv/w"], True))
        self.assertIsNone(tiers._git_call(["git", "-c", "a=b"], None))
        self.assertIsNone(tiers._git_call(["gitk", "push"], None))
        self.assertEqual(tiers._git_call(["git", "-C", "rel", "push"], None).folders, [None])


class EffectivePushUrl(StandInHome, unittest.TestCase):
    """The remote's pushurl lines, else its url lines, rewritten by pushInsteadOf and insteadOf from the repository's
    settings, the user's and -c, as git does. Anything the gate cannot read counts as a real remote."""

    PUBLISH = "Push to main, which can publish it"

    def setUp(self):
        self.home = self.make_home()
        self.root = os.path.join(self.home, "taffy", "work", "builder")
        self.scratch = os.path.join(self.root, "mirror.git")
        self.n = 0

    def push(self, config: str, cmd: str = "git push origin main", user: str | None = None):
        self.n += 1
        repo = self.repo(os.path.join(self.root, f"repo{self.n}"), config)
        user_file = os.path.join(self.home, ".gitconfig")
        if user is not None:
            with open(user_file, "w") as f:
                f.write(user)
        try:
            return tiers.classify_command(f"cd {repo} && {cmd}")
        finally:
            if user is not None:
                os.remove(user_file)

    def test_a_plain_scratch_remote_still_asks_nothing(self):
        self.assertIsNone(self.push(f'[remote "origin"]\n\turl = {self.scratch}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'))
        self.assertIsNone(self.push(f'[remote "origin"]\n\turl = {self.scratch}\n\turl = {self.root}/other.git\n'))
        self.assertIsNone(self.push(f'[Remote "origin"]\n\tURL = "{self.scratch}" # the mirror\n[core]\n\tbare = false\n'))
        self.assertIsNone(self.push(f'[remote "origin"]\n\turl = {self.scratch}\n\tpush = refs/heads/feat:refs/heads/main\n',
                                    "git push origin feat"))

    def test_the_report_cases(self):
        s, gh = self.scratch, GITHUB
        with open(os.path.join(self.home, "inc.cfg"), "w") as f:
            f.write(f'[remote "origin"]\n\tpushurl = {gh}\n')
        cases = {
            "G01": (f'[remote "origin"]\n\turl = {gh}\n\turl = {s}\n', "git push origin main"),
            "G02": (f'[remote "origin"]\n\turl = {s}\n\turl = {gh}\n', "git push origin main"),
            "G03": (f'[remote "origin"]\n\turl = {s}\n\tpushurl = {s}\n\tpushurl = {gh}\n', "git push origin main"),
            "G04": (f'[remote "origin"]\n\turl = {s}\n[url "https://github.com/colin/"]\n\tinsteadOf = {self.root}/\n',
                    "git push origin main"),
            "G05": (f'[remote "origin"]\n\turl = {s}\n[url "https://github.com/colin/"]\n\tpushInsteadOf = {self.root}/\n',
                    "git push origin main"),
            "G06": (f'[remote "origin"]\n\turl = {s}\n[include]\n\tpath = {self.home}/inc.cfg\n', "git push origin main"),
            "G06 relative": (f'[remote "origin"]\n\turl = {s}\n[include]\n\tpath = ../../../../../../inc.cfg\n',
                             "git push origin main"),
            "G06 home": (f'[remote "origin"]\n\turl = {s}\n[include]\n\tpath = ~/inc.cfg\n', "git push origin main"),
            "G07": (f'[remote "origin"]\n\turl = {gh}\n\tpush = refs/heads/feat:refs/heads/main\n', "git push origin feat"),
            "G07 glob": (f'[remote "origin"]\n\turl = {gh}\n\tpush = +refs/heads/*:refs/heads/main\n', "git push origin feat"),
            "G08": (f'[remote "origin"]\n\turl = {gh}\n\turl =\n\turl = {s}\n', "git push origin main"),
            "G09": (f'[remote "origin"]\n\turl = {s}\n', f"git -c remote.origin.pushurl={gh} push origin main"),
            "G10": (f'[remote "origin"]\n\turl = {s}\n', f"git -c url.https://github.com/colin/.insteadOf={self.root}/ push origin main"),
            "typed path": ("", f"git -c url.https://github.com/colin/.pushInsteadOf={self.root}/ push {s} main"),
            "missing include": (f'[remote "origin"]\n\turl = {s}\n[include]\n\tpath = {self.home}/nothing.cfg\n',
                                "git push origin main"),
            "includeIf": (f'[remote "origin"]\n\turl = {s}\n[includeIf "gitdir:/srv/"]\n\tpath = {self.home}/inc.cfg\n',
                          "git push origin main"),
            "config-env": (f'[remote "origin"]\n\turl = {s}\n', "git --config-env=remote.origin.pushurl=URL push origin main"),
            "GIT_CONFIG": (f'[remote "origin"]\n\turl = {s}\n',
                           "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.pushurl GIT_CONFIG_VALUE_0=x git push origin main"),
            "-c include": (f'[remote "origin"]\n\turl = {s}\n', f"git -c include.path={self.home}/inc.cfg push origin main"),
            "emptied by -c": (f'[remote "origin"]\n\turl = {s}\n', "git -c remote.origin.url= push origin main"),
            "a bad line": (f'[remote "origin"\n\turl = {s}\n', "git push origin main"),
            "two rewrites": (f'[remote "origin"]\n\turl = {s}\n[url "/tmp/a/"]\n\tinsteadOf = {self.root}/\n'
                             f'[url "https://github.com/colin/"]\n\tinsteadOf = {self.root}/\n', "git push origin main"),
        }
        for name, (config, cmd) in cases.items():
            with self.subTest(case=name):
                a = self.push(config, cmd)
                self.assertIsNotNone(a)
                self.assertEqual(a.summary, self.PUBLISH)

    def test_the_users_own_settings(self):
        config = f'[remote "origin"]\n\turl = {self.scratch}\n'
        a = self.push(config, user=f'[url "git@github.com:colin/"]\n\tinsteadOf = {self.root}/\n')
        self.assertEqual(a.summary, self.PUBLISH)
        a = self.push(config, user=f'[url "git@github.com:colin/"]\n\tpushInsteadOf = {self.root}/\n')
        self.assertEqual(a.summary, self.PUBLISH)
        self.assertIsNone(self.push(config, user='[user]\n\tname = Colin\n[url "https://github.com/"]\n\tinsteadOf = gh:\n'))

    def test_a_settings_file_the_gate_cannot_read_counts_as_a_real_remote(self):
        os.makedirs(os.path.join(self.home, ".gitconfig"))
        self.assertEqual(tiers.classify_command(f"git push {self.scratch} main").summary, self.PUBLISH)
        os.rmdir(os.path.join(self.home, ".gitconfig"))
        self.assertIsNone(tiers.classify_command(f"git push {self.scratch} main"))
        # A linked work tree's .git is a file pointing elsewhere.
        linked = os.path.join(self.root, "linked")
        os.makedirs(linked)
        with open(os.path.join(linked, ".git"), "w") as f:
            f.write("gitdir: /srv/repo/.git/worktrees/linked\n")
        self.assertEqual(tiers.classify_command(f"cd {linked} && git push origin main").summary, self.PUBLISH)

    def test_git_rewrites_the_way_git_does(self):
        s = self.scratch
        # pushInsteadOf is ignored for a remote with its own pushurl.
        self.assertIsNone(self.push(f'[remote "origin"]\n\turl = {GITHUB}\n\tpushurl = {s}\n'
                                    f'[url "https://github.com/colin/"]\n\tpushInsteadOf = {self.root}/\n'))
        # insteadOf can point a GitHub address at a scratch folder, and the longest match wins.
        self.assertIsNone(self.push(f'[remote "origin"]\n\turl = gh:colin/site\n[url "{self.root}/"]\n\tinsteadOf = gh:colin/\n'
                                    f'[url "https://github.com/"]\n\tinsteadOf = gh:\n'))
        self.assertEqual(self.push(f'[remote "origin"]\n\turl = gh:colin/site\n[url "https://github.com/"]\n\tinsteadOf = gh:\n')
                         .summary, self.PUBLISH)
        a = self.push(f'[remote "origin"]\n\turl = {GITHUB}\n\tpush = refs/heads/feat:refs/heads/main\n', "git push origin feat")
        self.assertEqual(a.target, "main")
        self.assertEqual(self.push(f'[remote "origin"]\n\turl = {GITHUB}\n\tpush = refs/heads/feat:refs/heads/main\n',
                                   "git push origin other").summary, "Push to the branch other")

    def test_the_folder_on_this_computer_named_is_the_one_outside(self):
        a = self.push(f'[remote "origin"]\n\turl = {self.scratch}\n\turl = /srv/git/site.git\n')
        self.assertEqual(a.summary, "Push main to a folder on this computer (/srv/git/site.git)")

    def test_reading_settings_files(self):
        parse = tiers._parse_git_config
        self.assertEqual(parse('[Remote "Origin"]\n URL = "a b" ; note\n\tpushurl=c\\\n d\n[url.X]\n\tInsteadOf\n'),
                         [("remote.Origin.url", "a b"), ("remote.Origin.pushurl", "c d"), ("url.x.insteadof", "true")])
        self.assertEqual(parse("[core] bare = false\n"), [("core.bare", "false")])
        self.assertEqual(parse('[a "x\\"y"]\n\tk = "q\\"r" # c\n'), [('a.x"y.k', 'q"r')])
        self.assertIsNone(parse("url = x\n"))
        self.assertIsNone(parse('[a]\n\tk = "open\n'))
        self.assertEqual(tiers._git_key("Remote.Origin.PushURL"), "remote.Origin.pushurl")


class CardNotes(unittest.TestCase):
    """Hidden only when the turn's later reply already says all of it; keyed to the card the call raised."""

    BLOCK = ("Not done yet: send this needs Colin's yes for this exact action. A card showing it is now in his app "
             "(request 5). Reply to Colin with only one short plain line.")
    ANSWERS = [
        "Theo said he can do Thursday or Friday and asked whether the budget is final. Replying that Thursday works; "
        "that needs your okay.",
        "Found 3 roles that fit: Project Engineer at Acme Homes ($78k), Field Engineer at Toll Brothers ($72k) and "
        "Estimator at Lennar ($70k). I'll ask the Acme Homes recruiter about the start date.",
        "The Halloween card for Miss Dana reads: Happy Halloween from Colin and the kids!",
        "OK, here is what the lease says: the deposit comes back within 30 days of move-out.",
        "Your flight is confirmed for Friday at 7:05 a.m., seat 14C. Sending the itinerary to Theo now.",
        "Your Northwind card statement is $412.18, due on the 21st. Paying it is up to you.",
        "The Sprint board has 4 cards in Doing and 2 in Review. Posting the summary to the team channel.",
        "Nothing to ask first: origin is a local bare repo in the scratch folder, so the force-push won't discard anything.",
        "This needs your approval.",
    ]

    def raw(self, note, result=None, later="Waiting for your okay on the card.", call_id="call_1"):
        out = [{"id": 1, "role": "user", "content": "What did Theo say? Tell him Thursday works."},
               {"id": 2, "role": "assistant", "content": note,
                "tool_calls": [{"id": call_id, "function": {"name": "terminal"}}]},
               {"id": 3, "role": "tool", "tool_call_id": call_id,
                "content": json.dumps({"error": self.BLOCK}) if result is None else result}]
        if later:
            out.append({"id": 4, "role": "assistant", "content": later})
        return out

    def shown(self, raw, bot_id=None, session_id=None):
        return [m["display"] for m in history.visible(hub_hermes._shown(raw, bot_id, session_id))]

    def test_real_answers_stay(self):
        for answer in self.ANSWERS:
            with self.subTest(answer=answer[:40]):
                self.assertEqual(self.shown(self.raw(answer)), ["What did Theo say? Tell him Thursday works.", answer,
                                                                "Waiting for your okay on the card."])

    def test_a_note_the_later_reply_already_says_is_hidden(self):
        for note in ("Waiting for your okay on the card.", "waiting for your okay on the card", "Waiting for your okay"):
            with self.subTest(note=note):
                self.assertEqual(self.shown(self.raw(note)), ["What did Theo say? Tell him Thursday works.",
                                                              "Waiting for your okay on the card."])
        timeout = json.dumps({"output": "", "error": "BLOCKED: Command timed out without user response. The user has NOT consented."})
        self.assertEqual(self.shown(self.raw("Waiting for your okay on the card.", timeout)),
                         ["What did Theo say? Tell him Thursday works.", "Waiting for your okay on the card."])

    def test_kept_with_no_card_or_nothing_after(self):
        note = "Waiting for your okay on the card."
        self.assertEqual(self.shown(self.raw(note, json.dumps({"output": "sent"}))).count(note), 2)
        self.assertIn(note, self.shown(self.raw(note, later=None)))

    def test_keyed_to_the_card_the_call_raised(self):
        note = "Waiting for your okay on the card."
        cards = {5: {"id": 5, "bot_id": "builder", "session_id": "s1"}, 6: {"id": 6, "bot_id": "career", "session_id": "s1"}}
        with mock.patch.object(hub_consent, "get_request", lambda req: cards.get(req)):
            self.assertEqual(self.shown(self.raw(note), "builder", "s1").count(note), 1)
            self.assertEqual(self.shown(self.raw(note), "career", "s1").count(note), 2)
            self.assertEqual(self.shown(self.raw(note), "builder", "s2").count(note), 2)
            other = json.dumps({"error": self.BLOCK.replace("request 5", "request 9")})
            self.assertEqual(self.shown(self.raw(note, other), "builder", "s1").count(note), 2)
            quoted = json.dumps({"content": "notes.md: " + self.BLOCK})
            self.assertEqual(self.shown(self.raw(note, quoted), "builder", "s1").count(note), 2)

    def test_messages_passes_the_bot_and_its_conversation(self):
        seen = {}

        def fake_shown(raw, bot_id=None, session_id=None, **_):
            seen.update(bot=bot_id, sid=session_id)
            return []

        class Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": []}

        async def fake_session(bot_id, fresh=False, team=False):
            return "sid-7"

        async def fake_get(bot_id, sid, raw_limit):
            return Response()

        with mock.patch.object(hub_hermes, "ensure_session", fake_session), \
                mock.patch.object(hub_hermes, "_get_messages", fake_get), \
                mock.patch.object(hub_hermes, "_shown", fake_shown):
            asyncio.run(hub_hermes.messages("builder"))
        self.assertEqual(seen, {"bot": "builder", "sid": "sid-7"})


if __name__ == "__main__":
    unittest.main()
