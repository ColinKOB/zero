"""Zip sandbox fixes (docs/qa/2026-10-08-sandbox-fixes.md).

1. Links: every reader of a file a Zip names (email attachments and the attachment folder, share_screen pictures,
   browser uploads, board artifacts, board attachments, the hub's pictures and the files shown on cards) follows links
   and ".." first and refuses anything outside that Zip's folders, protected paths and hard-linked files. The tests
   make real links, hard links and pipes in temporary folders.
2. zip-desktop-lockdown.py reads launcher.pid as "<pid> <start time>" and checks the process is that Zip's screen,
   against a made-up /proc, so no real screen is needed.

Nothing reaches the network, Hermes, himalaya or the home folder.

Run: python3 -m unittest discover -s hub/tests
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, gate, plugin, tt  # noqa: E402

safe_files, sandbox, tiers, share, email_tool = tt.safe_files, tt.sandbox, tt.tiers, tt.share, tt.email_tool


def pin_default_layout(test):
    """These tests check the plugin's logic for the usual layout (~/taffy/work and the rest, under the home the test
    passes in). Pin it, so a matrix shape with absolute paths (hub/tests/harness/homes.py) does not move it."""
    pin = tt.home.pinned(tt.home.colin_settings())
    pin.__enter__()
    test.addCleanup(pin.__exit__, None, None, None)
REPO = Path(__file__).resolve().parents[2]


def _home() -> Path:
    """A stand-in for alien's home folder: Clients' and School's work folders, the shared folder, the board's
    attachment store, and secrets beside them."""
    home = Path(tempfile.mkdtemp())
    for d in ("taffy/work/clients", "taffy/work/school", "taffy/shared", ".taffy/kanban/attachments/t_1", ".ssh",
              "Projects"):
        (home / d).mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY")
    (home / "Projects" / "notes.txt").write_text("not shared")
    return home


class SafeFiles(unittest.TestCase):
    def setUp(self):
        pin_default_layout(self)
        self.home = _home()
        self.work = self.home / "taffy" / "work" / "clients"
        self.roots = safe_files.zip_roots(self.home, "clients")

    def ok(self, path):
        return safe_files.resolve(str(path), self.roots, str(self.home))

    def refused(self, path, words=""):
        with self.assertRaises(safe_files.Refused) as e:
            self.ok(path)
        self.assertIn(words, str(e.exception))

    def test_the_folders_each_zip_may_share_from(self):
        h = str(self.home)
        self.assertEqual(safe_files.zip_roots(self.home, "clients"),
                         (f"{h}/taffy/work/clients", f"{h}/taffy/shared", f"{h}/.taffy/kanban/attachments"))
        self.assertEqual(safe_files.zip_roots(self.home, "default")[0], f"{h}/taffy/work")

    def test_plain_files_in_the_zips_folders(self):
        for rel in ("taffy/work/clients/essay.pdf", "taffy/shared/plan.md", ".taffy/kanban/attachments/t_1/a.pdf"):
            with self.subTest(rel=rel):
                (self.home / rel).write_text("x")
                self.assertEqual(self.ok(self.home / rel), os.path.realpath(self.home / rel))
        (self.work / "sub").mkdir()
        self.assertEqual(self.ok(f"{self.work}/sub/../essay.pdf"), os.path.realpath(self.work / "essay.pdf"))
        self.assertEqual(safe_files.resolve("~/taffy/work/clients/essay.pdf", self.roots, str(self.home)),
                         os.path.realpath(self.work / "essay.pdf"))

    def test_links_are_followed_before_the_check(self):
        (self.work / "key").symlink_to(self.home / ".ssh" / "id_ed25519")
        self.refused(self.work / "key", "not in the folders")
        (self.work / "notes").symlink_to(self.home / "Projects" / "notes.txt")
        self.refused(self.work / "notes", "not in the folders")
        (self.work / "elsewhere").symlink_to(self.home / "Projects")
        self.refused(self.work / "elsewhere" / "notes.txt", "not in the folders")
        # A link to a link, and a link to the folder above.
        (self.work / "twice").symlink_to(self.work / "key")
        self.refused(self.work / "twice")
        (self.work / "up").symlink_to(self.home)
        self.refused(self.work / "up" / ".ssh" / "id_ed25519")
        # A link that stays inside the Zip's folders names that file.
        (self.work / "essay.pdf").write_text("x")
        (self.work / "same").symlink_to(self.work / "essay.pdf")
        self.assertEqual(self.ok(self.work / "same"), os.path.realpath(self.work / "essay.pdf"))

    def test_dot_dot_and_other_zips(self):
        self.refused(f"{self.work}/../../../Projects/notes.txt", "not in the folders")
        (self.home / "taffy" / "work" / "school" / "x.pdf").write_text("x")
        self.refused(f"{self.work}/../school/x.pdf", "not in the folders")
        self.refused(self.home / "taffy" / "work" / "school" / "x.pdf", "not in the folders")
        # Zero, the lead, reviews every Zip's work.
        self.assertTrue(safe_files.resolve(str(self.home / "taffy" / "work" / "school" / "x.pdf"),
                                           safe_files.zip_roots(self.home, "default")))

    def test_protected_hard_linked_and_odd_files(self):
        for name in (".env", "id_ed25519", "personal.pass", "vault.json"):
            with self.subTest(name=name):
                (self.work / name).write_text("x")
                self.refused(self.work / name, "protected")
        os.link(self.home / ".ssh" / "id_ed25519", self.work / "copy")
        self.refused(self.work / "copy", "hard links")
        (self.work / "own.txt").write_text("x")
        os.link(self.work / "own.txt", self.work / "own-again.txt")
        self.refused(self.work / "own.txt", "hard links")
        os.mkfifo(self.work / "pipe")
        self.refused(self.work / "pipe", "not a regular file")
        self.refused(self.work, "not in the folders")
        (self.work / "folder").mkdir()
        self.refused(self.work / "folder", "not a regular file")
        self.refused(self.work / "missing.pdf", "does not exist")
        self.refused("taffy/work/clients/own.txt", "not a full path")
        self.refused(f"{self.work}/a\0b")

    def test_open_file_reads_exactly_the_checked_file(self):
        (self.work / "essay.pdf").write_bytes(b"essay")
        with safe_files.open_file(str(self.work / "essay.pdf"), self.roots) as f:
            self.assertEqual(f.read(), b"essay")
        self.assertEqual(safe_files.read_file(str(self.work / "essay.pdf"), self.roots), b"essay")

    def test_a_link_swapped_in_after_the_check_is_refused(self):
        """resolve passed, then the Zip replaced the file, or a folder above it, with a link."""
        (self.work / "essay.pdf").symlink_to(self.home / ".ssh" / "id_ed25519")
        with mock.patch.object(safe_files, "resolve", lambda *a, **kw: str(self.work / "essay.pdf")):
            with self.assertRaises(safe_files.Refused):
                safe_files.open_file(str(self.work / "essay.pdf"), self.roots)
        (self.work / "dir").symlink_to(self.home / "Projects")
        with mock.patch.object(safe_files, "resolve", lambda *a, **kw: str(self.work / "dir" / "notes.txt")):
            with self.assertRaises(safe_files.Refused) as e:
                safe_files.open_file(str(self.work / "dir" / "notes.txt"), self.roots)
        self.assertIn("changed", str(e.exception))

    def test_card_text(self):
        (self.work / "reply.eml").write_text("Hi")
        self.assertEqual(safe_files.card_text(str(self.work / "reply.eml"), 100), b"Hi")
        (self.work / "key").symlink_to(self.home / ".ssh" / "id_ed25519")
        self.assertIsNone(safe_files.card_text(str(self.work / "key"), 100))
        os.link(self.home / ".ssh" / "id_ed25519", self.work / "hard")
        self.assertIsNone(safe_files.card_text(str(self.work / "hard"), 100))
        os.mkfifo(self.work / "pipe")
        self.assertIsNone(safe_files.card_text(str(self.work / "pipe"), 100))
        self.assertIsNone(safe_files.card_text("reply.eml", 100))

    def test_the_gate_and_the_hub_share_one_copy(self):
        self.assertIs(gate.SECRET_PATHS, safe_files.SECRET_PATHS)
        from taffy_hub import safe_files as hub_files
        self.assertEqual(hub_files.SECRET_PATHS.pattern, safe_files.SECRET_PATHS.pattern)


class ZipCase(GateCase):
    bot = "clients"

    def setUp(self):
        super().setUp()
        self.home = _home()
        self.work = self.home / "taffy" / "work" / "clients"
        p = mock.patch.object(plugin, "REAL_HOME", self.home)
        p.start()
        self.addCleanup(p.stop)


class EmailAttachments(ZipCase):
    def setUp(self):
        super().setUp()
        self.sent = []

        def fake(argv, stdin=None):
            import subprocess
            out = ""
            if argv[:2] == ["template", "write"]:
                out = "From: Colin <jdoe1@example.com>\nTo: \nSubject: \n\n"
            elif argv[:2] == ["template", "send"]:
                self.sent.append(stdin)
            elif argv[:2] == ["attachment", "download"]:
                d = Path(argv[argv.index("-d") + 1])
                (d / "authorized_keys").write_text("ssh-ed25519 AAAA stranger")
            return subprocess.CompletedProcess(argv, 0, out, "")
        p = mock.patch.object(email_tool, "run", fake)
        p.start()
        self.addCleanup(p.stop)

    def send(self, path):
        return json.loads(email_tool.email_tool({"action": "send", "account": "personal", "to": ["a@example.com"],
                                                 "subject": "Files", "body": "Attached.", "attach": [str(path)]}))

    def test_only_the_zips_own_files(self):
        (self.work / "key").symlink_to(self.home / ".ssh" / "id_ed25519")
        os.link(self.home / ".ssh" / "id_ed25519", self.work / "hard")
        (self.home / "taffy" / "work" / "school" / "x.pdf").write_text("x")
        for path in (self.work / "key", self.work / "hard", self.home / "taffy" / "work" / "school" / "x.pdf",
                     f"{self.work}/../../../.ssh/id_ed25519"):
            with self.subTest(path=path):
                self.assertIn("cannot be attached", self.send(path)["error"])
        self.assertEqual(self.sent, [])
        (self.home / "taffy" / "shared" / "plan.pdf").write_bytes(b"plan")
        self.assertEqual(self.send(self.home / "taffy" / "shared" / "plan.pdf")["attached"], ["plan.pdf"])

    def test_attachments_never_land_outside_the_work_folder(self):
        """A stranger's attachment named authorized_keys, and a Zip that pointed its mail folder at ~/.ssh."""
        (self.work / "mail").symlink_to(self.home / ".ssh")
        out = json.loads(email_tool.email_tool({"action": "attachments", "account": "personal", "id": "9"}))
        self.assertIn("not a folder inside your work folder", out["error"])
        self.assertEqual(sorted(p.name for p in (self.home / ".ssh").iterdir()), ["id_ed25519"])
        (self.work / "mail").unlink()
        (self.work / "mail").mkdir()
        (self.work / "mail" / "personal-9").symlink_to(self.home / ".ssh")
        out = json.loads(email_tool.email_tool({"action": "attachments", "account": "personal", "id": "9"}))
        self.assertIn("error", out)
        self.assertEqual(sorted(p.name for p in (self.home / ".ssh").iterdir()), ["id_ed25519"])
        (self.work / "mail" / "personal-9").unlink()
        out = json.loads(email_tool.email_tool({"action": "attachments", "account": "personal", "id": "9"}))
        self.assertEqual(out["saved"], [str(self.work / "mail" / "personal-9" / "authorized_keys")])


class SharedPictures(ZipCase):
    def setUp(self):
        super().setUp()
        self.opened = []
        test = self

        class FakeImage:
            width, height = 10, 10

            def __init__(self, f):
                self.data = f.read()
                test.opened.append(self.data)

            def load(self):
                pass

            def convert(self, mode):
                return self

            def save(self, path, **kw):
                Path(path).write_bytes(b"jpeg")
        image = types.SimpleNamespace(open=FakeImage)
        pil = types.ModuleType("PIL")
        pil.Image = image
        out = Path(tempfile.mkdtemp())
        # Hermes's screen lease is not consulted (None makes its import fail, as off alien).
        for p in (mock.patch.dict(sys.modules, {"PIL": pil, "PIL.Image": image, "tools.bot_desktop": None}),
                  mock.patch.object(share, "MEDIA", out / "media"), mock.patch.object(share, "OUTBOX", out / "outbox")):
            p.start()
            self.addCleanup(p.stop)

    def share(self, path):
        return json.loads(share.share({"caption": "The chart.", "path": str(path)}))

    def test_links_and_other_folders_are_refused(self):
        (self.work / "chart.png").symlink_to(self.home / ".ssh" / "id_ed25519")
        (self.home / "tmp-pic.png").write_bytes(b"png")
        for path in (self.work / "chart.png", self.home / "tmp-pic.png", f"{self.work}/../../../.ssh/id_ed25519",
                     self.home / "taffy" / "work" / "school" / "x.png"):
            with self.subTest(path=path):
                self.assertIn("Only images in your own work folder", self.share(path)["error"])
        self.assertEqual(self.opened, [])
        (self.work / "chart.png").unlink()
        (self.work / "chart.png").write_bytes(b"png")
        self.assertTrue(self.share(self.work / "chart.png")["ok"])
        self.assertEqual(self.opened, [b"png"])


class BrowserUploads(ZipCase):
    def setUp(self):
        super().setUp()
        # Password fills other tests made in this process are remembered in memory; this browser has none.
        p = mock.patch.dict(gate._filled, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def exec_(self, code):
        from test_sandbox_stage3 import DOCKER_CONFIG
        prof = gate._taffy_home() / "profiles" / "clients"
        prof.mkdir(parents=True, exist_ok=True)
        (prof / "config.yaml").write_text(DOCKER_CONFIG)
        return self.call("browser_exec", {"code": code})

    def test_uploads_follow_links_and_stay_in_the_zips_folders(self):
        (self.work / "essay.pdf").write_text("x")
        self.assertIsNone(self.exec_(f'upload_file("#f", "{self.work}/essay.pdf")'))
        (self.home / "taffy" / "work" / "school" / "x.pdf").write_text("x")
        os.link(self.work / "essay.pdf", self.work / "again.pdf")
        (self.work / "key.pdf").symlink_to(self.home / ".ssh" / "id_ed25519")
        for path in (self.home / "taffy" / "work" / "school" / "x.pdf", self.work / "again.pdf", self.work / "key.pdf",
                     f"{self.work}/../school/x.pdf"):
            with self.subTest(path=path):
                self.assertIn("work or shared folders", self.exec_(f'upload_file("#f", "{path}")')["message"])


class BoardArtifacts(ZipCase):
    def complete(self, *paths):
        return self.call("kanban_complete", {"task_id": "t_1", "summary": "Done.", "artifacts": [str(p) for p in paths]})

    def test_artifacts_must_be_the_zips_own_files(self):
        (self.work / "report.pdf").write_text("x")
        (self.home / ".taffy" / "kanban" / "attachments" / "t_1" / "brief.pdf").write_text("x")
        self.assertIsNone(self.complete(self.work / "report.pdf",
                                        self.home / ".taffy" / "kanban" / "attachments" / "t_1" / "brief.pdf"))
        (self.work / "key.pdf").symlink_to(self.home / ".ssh" / "id_ed25519")
        os.link(self.home / "Projects" / "notes.txt", self.work / "notes.txt")
        for path in (self.work / "key.pdf", self.work / "notes.txt", self.home / "Projects" / "notes.txt",
                     self.work / "missing.pdf", f"{self.work}/../../../.ssh/id_ed25519"):
            with self.subTest(path=path):
                b = self.complete(self.work / "report.pdf", path)
                self.assertIn("cannot go on the board", b["message"])
                self.assertIn(f"{self.home}/taffy/work/clients", b["message"])


class CardFiles(ZipCase):
    def test_a_send_never_shows_a_linked_secret(self):
        (self.work / "reply.eml").symlink_to(self.home / ".ssh" / "id_ed25519")
        self.assertEqual(tiers._message_file(f"himalaya template send < {self.work}/reply.eml", None),
                         ("", "unreadable"))
        (self.work / "reply.eml").unlink()
        (self.work / "reply.eml").write_text("To: a@example.com\n\nHi")
        text, digest = tiers._message_file(f"himalaya template send < {self.work}/reply.eml", None)
        self.assertIn("Hi", text)

    def test_a_crontab_file_and_a_job_script(self):
        (self.work / "jobs").symlink_to(self.home / ".ssh" / "id_ed25519")
        self.assertEqual(tiers._small_file(str(self.work / "jobs"), None), "")
        scripts = gate._profile_home("clients") / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "watch.py").symlink_to(self.home / ".ssh" / "id_ed25519")
        self.assertIsNone(gate._read_script("clients", "watch.py", True))
        (scripts / "ok.py").write_text("print('hi')")
        self.assertEqual(gate._read_script("clients", "ok.py", True), "print('hi')")


class HubFiles(unittest.TestCase):
    """The board's attachments and the hub's pictures, read through the app's own endpoints."""

    def setUp(self):
        from test_fixes_r1 import AppCase
        self.case = AppCase("run")
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        from taffy_hub import board, config
        self.config = config
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "kanban"
        (self.store / "attachments" / "t_1").mkdir(parents=True)
        self.db = self.root / "kanban.db"
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE task_attachments (id INTEGER PRIMARY KEY, task_id TEXT, filename TEXT, "
                  "stored_path TEXT, size INTEGER, content_type TEXT)")
        c.commit()
        c.close()
        for p in (mock.patch.object(board, "ATTACHMENTS_ROOT", self.store.resolve()),
                  mock.patch.object(config, "KANBAN_DB", self.db)):
            p.start()
            self.addCleanup(p.stop)
        self.secret = self.root / "id_ed25519"
        self.secret.write_text("PRIVATE KEY")

    def attach(self, stored: Path | str, name="brief.pdf") -> int:
        c = sqlite3.connect(self.db)
        cur = c.execute("INSERT INTO task_attachments (task_id, filename, stored_path) VALUES ('t_abc123', ?, ?)",
                        (name, str(stored)))
        c.commit()
        c.close()
        return cur.lastrowid

    def get(self, aid):
        return self.case.client.get(f"/api/tasks/t_abc123/attachments/{aid}")

    def test_board_attachments(self):
        good = self.store / "attachments" / "t_1" / "brief.pdf"
        good.write_bytes(b"%PDF brief")
        r = self.get(self.attach(good, "Brief été.pdf"))
        self.assertEqual((r.status_code, r.content), (200, b"%PDF brief"))
        self.assertIn("filename*=utf-8''Brief%20%C3%A9t%C3%A9.pdf", r.headers["content-disposition"])
        link = self.store / "attachments" / "t_1" / "key.pdf"
        link.symlink_to(self.secret)
        folder = self.store / "attachments" / "t_2"
        folder.symlink_to(self.root)
        hard = self.store / "attachments" / "t_1" / "hard.pdf"
        os.link(self.secret, hard)
        for stored in (link, folder / "id_ed25519", hard, f"{self.store}/attachments/t_1/../../../id_ed25519",
                       self.secret, self.store / "attachments" / "t_1" / "gone.pdf"):
            with self.subTest(stored=stored):
                r = self.get(self.attach(stored))
                self.assertEqual(r.status_code, 404)
                self.assertNotIn(b"PRIVATE", r.content)

    def test_pictures(self):
        media = self.config.HUB_DIR / "media"
        media.mkdir(parents=True, exist_ok=True)
        (media / ("a" * 32 + ".jpg")).write_bytes(b"jpeg")
        r = self.case.client.get("/api/media/" + "a" * 32)
        self.assertEqual((r.status_code, r.content, r.headers["content-type"]), (200, b"jpeg", "image/jpeg"))
        (media / ("b" * 32 + ".jpg")).symlink_to(self.secret)
        os.link(self.secret, media / ("c" * 32 + ".jpg"))
        for mid in ("b" * 32, "c" * 32, "d" * 32):
            with self.subTest(mid=mid):
                self.assertEqual(self.case.client.get("/api/media/" + mid).status_code, 404)


class LockdownRunningCheck(unittest.TestCase):
    """zip-desktop-lockdown.py's running check against a made-up /proc. Stage 3 found it read "pid start-time" as
    one number, failed, and called a running screen stopped."""

    BOOT = 1791400000.0

    def setUp(self):
        script = REPO / "alien" / "sandbox" / "zip-desktop-lockdown.py"
        if not script.exists():
            self.skipTest("repo-only check (alien runs the hub without the repo)")
        spec = importlib.util.spec_from_file_location("zip_desktop_lockdown_fixes", script)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.proc = Path(tempfile.mkdtemp())
        (self.proc / "stat").write_text(f"cpu  1 2 3\nbtime {int(self.BOOT)}\nprocesses 9\n")
        p = mock.patch.object(self.mod, "PROC", self.proc)
        p.start()
        self.addCleanup(p.stop)
        self.home = Path(tempfile.mkdtemp()) / ".taffy"
        self.desk = self.home / "profiles" / "clients" / "bot-desktop"
        (self.desk / "xdg").mkdir(parents=True)
        self.hz = os.sysconf("SC_CLK_TCK")

    def process(self, pid: int, args: list[str], ticks: int = 5000, state: str = "S", comm: str = "bash") -> float:
        """A process in the made-up /proc. Returns its start time as Hermes records it."""
        d = self.proc / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) {state} " + " ".join(["1"] * 18) + f" {ticks} 0 0\n")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        return self.BOOT + ticks / self.hz

    def launcher(self, pid=4242, **kw) -> float:
        return self.process(pid, ["bash", "/home/c/taffy/hermes/tools/bot_desktop/launcher.sh"], **kw)

    def pidfile(self, text: str):
        (self.desk / "launcher.pid").write_text(text)

    def test_hermes_pid_and_start_time(self):
        born = self.launcher()
        self.pidfile(f"4242 {born:.2f}")
        self.assertTrue(self.mod.running(self.desk))
        self.assertIn("skipped", self.mod.lock(self.home, "clients", []))

    def test_a_reused_pid_is_not_the_screen(self):
        born = self.launcher()
        self.pidfile(f"4242 {born - 3600:.2f}")
        self.assertFalse(self.mod.running(self.desk))

    def test_another_program_with_that_pid(self):
        born = self.process(4242, ["/usr/bin/python3", "-m", "http.server"])
        self.pidfile(f"4242 {born:.2f}")
        self.assertFalse(self.mod.running(self.desk))

    def test_dead_and_zombie_launchers(self):
        self.pidfile("4242 1791410332.19")
        self.assertFalse(self.mod.running(self.desk))
        born = self.launcher(state="Z")
        self.pidfile(f"4242 {born:.2f}")
        self.assertFalse(self.mod.running(self.desk))
        for text in ("", "garbage", "-1 5", "0 0"):
            with self.subTest(text=text):
                self.pidfile(text)
                self.assertFalse(self.mod.running(self.desk))

    def test_the_old_one_number_format(self):
        self.launcher()
        self.pidfile("4242\n")
        self.assertTrue(self.mod.running(self.desk))

    def test_an_x_server_left_on_this_zips_socket(self):
        """A launcher that died can leave Xvnc and the Xfce session running."""
        self.process(5000, ["Xvnc", ":12", "-rfbport", "-1", "-rfbunixpath", str(self.desk / "rfb.sock")],
                     comm="Xvnc")
        self.assertTrue(self.mod.running(self.desk))
        self.assertIn("skipped", self.mod.lock(self.home, "clients", []))

    def test_another_zips_x_server(self):
        other = self.home / "profiles" / "school" / "bot-desktop" / "rfb.sock"
        self.process(5000, ["Xvnc", ":12", "-rfbunixpath", str(other)], comm="Xvnc")
        self.assertFalse(self.mod.running(self.desk))
        self.assertNotIn("skipped", self.mod.lock(self.home, "clients", []))


if __name__ == "__main__":
    unittest.main()
