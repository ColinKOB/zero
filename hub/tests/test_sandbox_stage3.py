"""Zip sandbox stage 3, the gate fixes (docs/qa/2026-10-08-sandbox-stage3-code.md): a containerized Zip's browser opens
only public http and https pages (browser_navigate, browser_steps, browser_cdp and browser_exec, whose Python runs on
alien), its screen tool cannot open a terminal or another app, its container's /root and /workspace are its own to
delete in, and computer_use stays only with Zero and School. Zero and the Zips still on alien's own terminal are
unchanged.

Name lookups are faked; nothing reaches the network, Hermes or the home folder.

Run: python3 -m unittest discover -s hub/tests
"""

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gate import GateCase, gate, plugin, tt  # noqa: E402

sandbox, tiers = tt.sandbox, tt.tiers
REPO = Path(__file__).resolve().parents[2]

DOCKER_CONFIG = """model:
  default: claude
terminal:
  home_mode: real
  backend: docker
  docker_image: taffy/zip-sandbox:2026-10-08-v1
  docker_volumes:
  - /home/jdoe/taffy/work:/home/jdoe/taffy/work:ro
  docker_extra_args:
  - -p
  - 127.0.0.1:18220-18229:18220-18229
  - --network
  - zip-school
browser:
  headed: true
"""
LOCAL_CONFIG = "terminal:\n  home_mode: real\n  backend: local\nbrowser:\n  headed: true\n"
NAMES = {"example.com": ["93.184.216.34"], "elearning.lakeview.test": ["104.18.1.1"], "127.0.0.1.nip.io": ["127.0.0.1"],
         "rebind.example.net": ["10.0.0.65"], "github.com": ["140.82.112.3"]}


class ContainerCase(GateCase):
    """School runs its terminal in a container; Builder and Zero do not. alien's home folder is a temporary one."""

    bot = "school"

    def setUp(self):
        super().setUp()
        home = gate._taffy_home()
        for bot, text in (("school", DOCKER_CONFIG), ("builder", LOCAL_CONFIG)):
            (home / "profiles" / bot).mkdir(parents=True, exist_ok=True)
            (home / "profiles" / bot / "config.yaml").write_text(text)
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(LOCAL_CONFIG)
        self.real_home = Path(tempfile.mkdtemp())
        for d in ("taffy/work/school", "taffy/shared", "Projects/secret"):
            (self.real_home / d).mkdir(parents=True)
        # Password fills other tests made in this process are remembered in memory; this browser has none.
        patches = [mock.patch.object(plugin, "REAL_HOME", self.real_home),
                   mock.patch.object(sandbox, "_resolve", lambda host: NAMES.get(host, [])),
                   mock.patch.dict(gate._filled, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def blocked(self, tool, args, bot=None):
        if bot:
            with mock.patch.object(gate, "_bot", lambda: bot):
                return self.call(tool, args)
        return self.call(tool, args)


class ContainerSettings(ContainerCase):
    def test_reads_the_terminal_block(self):
        info = sandbox.container_info(gate._taffy_home() / "profiles" / "school")
        self.assertEqual(info["ports"], set(range(18220, 18230)))
        self.assertIsNone(sandbox.container_info(gate._taffy_home() / "profiles" / "builder"))
        self.assertIsNone(sandbox.container_info(gate._taffy_home()))
        self.assertIsNone(sandbox.container_info(gate._taffy_home() / "profiles" / "nobody"))

    def test_flow_style_args_and_a_changed_file(self):
        path = gate._taffy_home() / "profiles" / "career" / "config.yaml"
        path.parent.mkdir(parents=True)
        path.write_text('terminal:\n  backend: "docker"\n  docker_extra_args: ["-p", "127.0.0.1:18230-18239:18230-18239"]\n')
        self.assertEqual(sandbox.container_info(path.parent)["ports"], set(range(18230, 18240)))
        path.write_text("terminal:\n  backend: local\n")
        os.utime(path, (1, 1))
        self.assertIsNone(sandbox.container_info(path.parent))

    def test_a_backend_word_outside_the_terminal_block_does_not_count(self):
        path = gate._taffy_home() / "profiles" / "money" / "config.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("terminal:\n  backend: local\nbrowser:\n  backend: docker\n")
        self.assertIsNone(sandbox.container_info(path.parent))


class BrowserAddresses(ContainerCase):
    BLOCKED = ["file:///home/jdoe/.ssh/config", "FILE:///etc/passwd", "view-source:file:///etc/hosts",
               "view-source:http://127.0.0.1:8650/", "chrome://settings", "chrome-extension://abc/x.html",
               "devtools://devtools/bundled/inspector.html", "about:settings", "data:text/html,<b>x</b>",
               "javascript:alert(1)", "filesystem:http://example.com/temporary/x", "ftp://example.com/",
               "http://127.0.0.1:8650/api/bots", "http://localhost:6443/", "http://127.0.0.1/", "http://[::1]:8650/",
               "http://[::ffff:127.0.0.1]:8650/", "http://2130706433:8650/", "http://0x7f.1:8650/",
               "http://0177.0.0.1:8650/", "http://127.1:8650/", "http://0.0.0.0:8650/", "http://10.0.0.65:8080/",
               "http://10.0.0.1/", "http://192.168.1.1/", "http://100.64.0.10:7443/", "http://169.254.169.254/",
               "http://alien:7443/", "http://alien.tail1234.ts.net/", "http://printer.local/", "http://router.lan/",
               "http://127.0.0.1.nip.io:8650/", "http://rebind.example.net/", "http://user:pw@127.0.0.1:8650/",
               "http:\\\\127.0.0.1:8650", "http:127.0.0.1:8650", "localhost:8650", "127.0.0.1:8650/api",
               "http://127.0.0.1:18230/", "https://foo.localhost/", "http://10.1/", "http://0x0a000041:8080/"]
    ALLOWED = ["https://example.com/", "https://elearning.lakeview.test/course/1", "http://127.0.0.1:18220/",
               "http://localhost:18229/index.html", "http://[::1]:18225/", "about:blank", "example.com/page",
               "https://github.com/colin/site/pull/3", "https://nowhere.invalid/"]

    def test_blocked(self):
        for url in self.BLOCKED:
            with self.subTest(url=url):
                b = self.blocked("browser_navigate", {"url": url})
                self.assertIsNotNone(b)
                self.assertTrue(b["message"].startswith("Blocked:"), b["message"])
                self.assertIn("18220 to 18229", b["message"])

    def test_allowed(self):
        for url in self.ALLOWED:
            with self.subTest(url=url):
                self.assertIsNone(self.blocked("browser_navigate", {"url": url}))

    def test_zero_and_zips_on_alien_are_unchanged(self):
        for bot in ("builder", "default"):
            for url in ("file:///etc/hosts", "http://127.0.0.1:8650/"):
                with self.subTest(bot=bot, url=url):
                    self.assertIsNone(self.blocked("browser_navigate", {"url": url}, bot=bot))

    def test_browser_steps_goto_goes_through_the_same_check(self):
        """browser_steps checks each goto with gate.pre_tool_call("browser_navigate", ...), registered as on alien."""
        handlers = {}
        ctx = types.SimpleNamespace(register_tool=lambda name, toolset, schema, handler: handlers.__setitem__(name, handler),
                                    register_hook=lambda *a, **kw: None,
                                    dispatch_tool=lambda *a, **kw: self.fail("a step ran"))
        page = types.ModuleType("tools.browser_vault_tool")   # Hermes's page lookup, faked even where Hermes exists
        page._current_page_origin = lambda task_id: "https://example.com/"
        tools_pkg = types.ModuleType("tools")
        tools_pkg.browser_vault_tool = page
        fake_hermes = mock.patch.dict(sys.modules, {"tools": tools_pkg, "tools.browser_vault_tool": page})
        fake_hermes.start()
        self.addCleanup(fake_hermes.stop)
        with mock.patch.object(tt.fast_steps, "browser_steps_on", lambda: True), \
                mock.patch.object(tt, "_no_browser_password_manager", lambda: None):
            tt.register(ctx)
        for step in ("go to http://127.0.0.1:8650/api", "open http://10.0.0.65:8080/", "navigate to http://alien:7443/"):
            with self.subTest(step=step):
                out = json.loads(handlers["browser_steps"]({"steps": [step]}, session_id="chat-1"))
                self.assertIn("alien", out["error"])

    def test_raw_cdp_tool(self):
        for args in ({"method": "DOM.setFileInputFiles", "params": {"files": ["/etc/passwd"], "nodeId": 3}},
                     {"method": "Page.navigate", "params": {"url": "file:///etc/passwd"}},
                     {"method": "Target.createTarget", "params": {"url": "http://127.0.0.1:8650"}},
                     {"method": "Browser.setDownloadBehavior", "params": {"behavior": "allow", "downloadPath": "/home"}},
                     {"method": "Runtime.evaluate", "params": {"expression": "fetch('http://10.0.0.65:8080/')"}}):
            with self.subTest(args=args):
                self.assertIsNotNone(self.blocked("browser_cdp", args))
        for args in ({"method": "Runtime.evaluate", "params": {"expression": "document.title"}},
                     {"method": "Page.navigate", "params": {"url": "https://example.com/"}},
                     {"method": "Target.getTargets"}):
            with self.subTest(args=args):
                self.assertIsNone(self.blocked("browser_cdp", args))


class BrowserExec(ContainerCase):
    """browser_exec's Python runs on alien: for a containerized Zip only the helpers and plain Python."""

    def exec_(self, code, **extra):
        return self.blocked("browser_exec", {"code": code, **extra})

    OK = [
        'new_tab("https://elearning.lakeview.test/course/1")\nwait_for_load()\nprint(page_info())',
        'goto_url("https://example.com/")\nprint(js("document.title"))',
        'import json, time\nfor u in ["https://example.com/a", "https://example.com/b"]:\n    goto_url(u)\n'
        '    time.sleep(1)\n    print(json.dumps(page_info()))',
        'urls = ["https://example.com/a", "https://github.com/x"]\nfor u in urls:\n    new_tab(u)',
        'q = "math"\ngoto_url(f"https://example.com/search?q={q}")',
        'goto_url("https://example.com/item/" + str(3))',
        'nodes = cdp("Accessibility.getFullAXTree")["nodes"]\nprint(len([n for n in nodes if n.get("role")]))',
        'box = cdp("DOM.getBoxModel", backendNodeId=12)\nclick_at_xy(10, 20)\ntype_text("hello")\npress_key("Enter")',
        'capture_screenshot()\nprint(current_tab())',
        'from datetime import datetime\nprint(datetime.now().isoformat())',
        'import re\nm = re.search(r"Due: (\\w+)", js("document.body.innerText"))\nprint(m.group(1) if m else None)',
        'print("{} items, {:.1f}%".format(3, 2.5))',
        'def grab(sel):\n    return js(f"document.querySelector({sel!r}).innerText")\nprint(grab("#due"))',
        'try:\n    wait_for_element("#x", timeout=5)\nexcept Exception as e:\n    print(e)',
        'new_tab("http://127.0.0.1:18221/")\nprint(page_info())',
        'fill_input("#q", "algebra")\nscroll(0, 0, dy=-300)\nprint(list_tabs())',
    ]
    BAD = [
        ("import os\nos.system('id')", "module os"),
        ("print(open('/etc/passwd').read())", "open is not available"),
        ("subprocess.run(['id'])", "subprocess is not available"),
        ("print(os.environ)", "os is not available"),
        ("__import__('os').system('id')", "__import__"),
        ("print(globals())", "globals"),
        ("import json\nprint(json.codecs)", "json.codecs"),
        ("j = json", "json is not available"),
        ("import json\nj = json\nprint(j.codecs)", "may only be used as json"),
        ("from os import system", "module os"),
        ("from json import *", "json.*"),
        ("import urllib.request", "urllib.request"),
        ("g = (x for x in [1])\nprint(g.gi_frame.f_globals)", "the attribute"),
        ("g = (x for x in [1])\nf = g.gi_frame", "gi_frame"),
        ("print(print.__self__)", "__self__"),
        ("print('{0.__globals__}'.format(goto_url))", "pattern that reads attributes"),
        ("fmt = '{}'\nprint(fmt.format(1))", "not written out"),
        ("g = goto_url\ng('file:///etc')", "may only be called directly"),
        ("print(list(map(goto_url, ['https://example.com'])))", "may only be called directly"),
        ("goto_url = print", "cannot be reused"),
        ("class X: pass", "classdef"),
        ("new_tab('file:///home/jdoe/Projects/secret/a.txt')", "not file: addresses"),
        ("goto_url('view-source:file:///etc/hosts')", "not view-source:"),
        ("goto_url('http://127.0.0.1:8650/api/bots')", "alien"),
        ("goto_url('http://10.0.0.65:8080/')", "Colin's own network"),
        ("link = js('document.links[0].href')\ngoto_url(link)", "written out in full"),
        ("goto_url(f'{base}/x')", "written out in full"),
        ("cdp('Page.navigate', url='file:///etc/passwd')", "not file:"),
        ("m = 'Page.navigate'\ncdp(m, url='https://example.com')", "method as a written-out string"),
        ("cdp('Page.navigate', **{'url': 'file:///etc'})", "no **"),
        ("cdp('DOM.setFileInputFiles', files=['/etc/passwd'], nodeId=1)", "picking files from alien"),
        ("cdp('Browser.setDownloadBehavior', behavior='allow', downloadPath='/home/jdoe')", "downloads"),
        ("print(http_get('https://example.com'))", "http_get is not available"),
        ("capture_screenshot('/home/jdoe/.bashrc')", "leave out the path"),
        ("capture_screenshot(path='/tmp/x.png')", "leave out the path"),
        ("js('fetch(\"http://127.0.0.1:8650/api/bots\").then(r => r.text())')", "alien"),
        ("u = js('x')\njs(f\"location.href = '{u}'\")", "written out in full, with no changing parts"),
        ("js('window.open(' + js('x') + ')')", "no changing parts"),
        ("print('see file:///etc/passwd')", "not file:"),
        ("args = ['https://example.com']\nnew_tab(*args)", "written out in full"),
        ("args = ['https://example.com']\nwait_for_load(*args)", "one by one"),
        ("start_recording('x')", "start_recording is not available"),
        ("import json\njson = 1", "cannot be reused"),
        ("def f():\n    global goto_url", "global"),
        ("print(", "does not parse"),
    ]

    def test_ordinary_code_runs(self):
        for code in self.OK:
            with self.subTest(code=code):
                self.assertIsNone(self.exec_(code))

    def test_what_reaches_alien_is_refused(self):
        for code, words in self.BAD:
            with self.subTest(code=code):
                b = self.exec_(code)
                self.assertIsNotNone(b, code)
                self.assertIn("runs on alien outside your container", b["message"])
                self.assertIn(words, b["message"])

    def test_uploads_only_from_the_work_and_shared_folders(self):
        ok = self.real_home / "taffy" / "work" / "school" / "essay.pdf"
        ok.write_text("x")
        self.assertIsNone(self.exec_(f'upload_file("#f", "{ok}")'))
        self.assertIsNone(self.exec_('upload_file("#f", "~/taffy/work/school/essay.pdf")'))
        secret = self.real_home / "Projects" / "secret" / "notes.txt"
        secret.write_text("x")
        self.assertIn("work or shared folders", self.exec_(f'upload_file("#f", "{secret}")')["message"])
        # A link the Zip made in its own folder names the file it points to on alien.
        link = self.real_home / "taffy" / "work" / "school" / "notes.txt"
        link.symlink_to(secret)
        self.assertIn("work or shared folders", self.exec_(f'upload_file("#f", "{link}")')["message"])
        self.assertIn("written-out path", self.exec_('p = js("x")\nupload_file("#f", p)')["message"])

    def test_the_local_profile_is_off(self):
        self.assertIn("local browser profile", self.exec_('print(page_info())', local=True)["message"])

    def test_zero_and_zips_on_alien_are_unchanged(self):
        for bot in ("builder", "default"):
            with self.subTest(bot=bot):
                self.assertIsNone(self.blocked("browser_exec", {"code": "import os\nprint(os.listdir('/'))"}, bot=bot))

    def test_the_post_fill_lock_still_applies(self):
        gate._mark_filled("chat-1")
        b = self.exec_('print(js("document.title"))')
        self.assertIn("password was filled", b["message"])


class Screen(ContainerCase):
    BLOCKED = [{"action": "key", "keys": "ctrl+alt+t"}, {"action": "key", "keys": "Control_L+Alt_L+t"},
               {"action": "key", "keys": "alt+f2"}, {"action": "key", "keys": "alt+F1"}, {"action": "key", "keys": "super"},
               {"action": "key", "keys": "Super_L"}, {"action": "key", "keys": "super+r"},
               {"action": "key", "keys": "cmd+space"}, {"action": "key", "keys": "ctrl+escape"},
               {"action": "key", "keys": "ctrl+o"}, {"action": "key", "keys": "ctrl+s"}, {"action": "key", "keys": "ctrl+j"},
               {"action": "key", "keys": "f12"}, {"action": "key", "keys": "ctrl+shift+i"},
               {"action": "key", "keys": "ctrl+alt+f"}, {"action": "key", "keys": "XF86WWW"},
               {"action": "key", "keys": "Menu"}, {"action": "key", "keys": "alt+f"},
               {"action": "click", "element": 3, "modifiers": ["super"]},
               {"action": "focus_app", "app": "xfce4-terminal"}, {"action": "focus_app", "app": "Thunar"},
               {"action": "click", "coordinate": [10, 700], "app": "desktop"},
               {"action": "double_click", "element": 2, "app": "xterm"},
               {"action": "type", "text": "file:///home/jdoe/.ssh/"},
               {"action": "type", "text": "chrome://settings\n"}, {"action": "type", "text": "about:downloads"},
               {"action": "type", "text": "http://127.0.0.1:8650/api/bots"}, {"action": "type", "text": "localhost:6443"},
               {"action": "set_value", "element": 4, "value": "view-source:file:///etc/hosts"}]
    ALLOWED = [{"action": "capture"}, {"action": "capture", "app": "desktop"}, {"action": "list_apps"},
               {"action": "key", "keys": "ctrl+l"}, {"action": "key", "keys": "return"}, {"action": "key", "keys": "alt+left"},
               {"action": "key", "keys": "ctrl+t"}, {"action": "key", "keys": "alt+tab"}, {"action": "key", "keys": "ctrl+shift+t"},
               {"action": "click", "element": 5}, {"action": "click", "coordinate": [400, 300], "app": "Chromium"},
               {"action": "focus_app", "app": "Google Chrome for Testing"},
               {"action": "type", "text": "https://elearning.lakeview.test\n"}, {"action": "type", "text": "Hello Andrea, thanks."},
               {"action": "type", "text": "http://127.0.0.1:18222/"}, {"action": "scroll", "direction": "down"}]

    def test_blocked(self):
        for args in self.BLOCKED:
            with self.subTest(args=args):
                b = self.blocked("computer_use", args)
                self.assertIsNotNone(b, args)
                self.assertIn("Your screen runs on alien outside your container", b["message"])

    def test_allowed(self):
        for args in self.ALLOWED:
            with self.subTest(args=args):
                self.assertIsNone(self.blocked("computer_use", args))

    def test_zero_is_unchanged(self):
        self.assertIsNone(self.blocked("computer_use", {"action": "key", "keys": "ctrl+alt+t"}, bot="default"))

    def test_every_zip_keeps_the_screen_tool(self):
        script = REPO / "alien" / "bin" / "set-toolsets.py"
        if not script.exists():
            self.skipTest("repo-only check (alien runs the hub without the repo)")
        ruamel = types.ModuleType("ruamel")
        ruamel.yaml = types.ModuleType("ruamel.yaml")
        ruamel.yaml.YAML = object
        with mock.patch.dict(sys.modules, {"ruamel": ruamel, "ruamel.yaml": ruamel.yaml}):
            spec = importlib.util.spec_from_file_location("set_toolsets_s3", script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        # Colin: the screen tool is critical to being a Zip, so every Zip keeps it (Oct 8).
        for bot, lead, keeps in (("default", True, True), ("school", False, True), ("builder", False, True),
                                 ("clients", False, True), ("career", False, True), ("money", False, True)):
            with self.subTest(bot=bot):
                lists = mod.lists(lead, bot)
                for platform in ("api_server", "cli", "cron"):
                    self.assertEqual("computer_use" in lists[platform], keeps)
                    self.assertIn("browser", lists[platform])


class ContainerDeletes(ContainerCase):
    def setUp(self):
        super().setUp()
        self.ask("clean up the scratch files")

    def test_the_containers_own_folders(self):
        for cmd in ("rm -rf /root/scratch", "rm -rf /workspace/build-output", "find /root/tmp -delete"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.blocked("terminal", {"command": cmd}))
        self.assertEqual(self.cards(), [])

    def test_still_asks_on_alien_and_for_the_folders_themselves(self):
        for bot in ("builder", "default"):
            with self.subTest(bot=bot):
                b = self.blocked("terminal", {"command": "rm -rf /root/scratch"}, bot=bot)
                self.assertIsNotNone(b)
        self.assertIsNotNone(self.blocked("terminal", {"command": "rm -rf ~/Documents/old"}))
        self.assertIsNotNone(self.blocked("terminal", {"command": "rm -rf /root"}))

    def test_the_setting_does_not_leak_out_of_the_call(self):
        self.blocked("terminal", {"command": "rm -rf /root/scratch"})
        self.assertFalse(tiers._safe("/root/scratch"))
        self.assertIsNotNone(tiers.classify_command("rm -rf /root/scratch"))


class DesktopLockdown(unittest.TestCase):
    """alien/sandbox/zip-desktop-lockdown.py on a copy of the screen settings Hermes seeds (launcher.sh)."""

    PANEL = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array">
    <value type="int" value="1"/>
    <value type="int" value="2"/>
    <property name="panel-1" type="empty">
      <property name="plugin-ids" type="array">
        <value type="int" value="1"/><value type="int" value="2"/><value type="int" value="3"/>
        <value type="int" value="4"/><value type="int" value="5"/>
      </property>
    </property>
    <property name="panel-2" type="empty">
      <property name="plugin-ids" type="array"><value type="int" value="21"/><value type="int" value="22"/><value type="int" value="23"/></property>
    </property>
  </property>
  <property name="plugins" type="empty">
    <property name="plugin-1" type="string" value="applicationsmenu"/>
    <property name="plugin-2" type="string" value="tasklist"/>
    <property name="plugin-3" type="string" value="separator"/>
    <property name="plugin-4" type="string" value="systray"/>
    <property name="plugin-5" type="string" value="clock"/>
    <property name="plugin-21" type="string" value="launcher"><property name="items" type="array"><value type="string" value="hermes.desktop"/></property></property>
    <property name="plugin-22" type="string" value="launcher"><property name="items" type="array"><value type="string" value="hermes.desktop"/></property></property>
    <property name="plugin-23" type="string" value="launcher"><property name="items" type="array"><value type="string" value="hermes.desktop"/></property></property>
  </property>
</channel>
"""
    SHORTCUTS = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-keyboard-shortcuts" version="1.0">
  <property name="commands" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Primary&gt;&lt;Alt&gt;t" type="string" value="exo-open --launch TerminalEmulator"/>
      <property name="&lt;Alt&gt;F2" type="string" value="xfce4-appfinder --collapsed"/>
    </property>
  </property>
  <property name="xfwm4" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Alt&gt;Tab" type="string" value="cycle_windows_key"/>
    </property>
  </property>
</channel>
"""

    def setUp(self):
        script = REPO / "alien" / "sandbox" / "zip-desktop-lockdown.py"
        if not script.exists():
            self.skipTest("repo-only check (alien runs the hub without the repo)")
        spec = importlib.util.spec_from_file_location("zip_desktop_lockdown", script)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.home = Path(tempfile.mkdtemp()) / ".taffy"
        self.desk = self.home / "profiles" / "clients" / "bot-desktop"
        conf = self.desk / "xdg" / "xfce4" / "xfconf" / "xfce-perchannel-xml"
        conf.mkdir(parents=True)
        (conf / "xfce4-panel.xml").write_text(self.PANEL)
        (conf / "xfce4-keyboard-shortcuts.xml").write_text(self.SHORTCUTS)
        for n, name in ((21, "Terminal"), (22, "Browser"), (23, "Files")):
            d = self.desk / "xdg" / "xfce4" / "panel" / f"launcher-{n}"
            d.mkdir(parents=True)
            (d / "hermes.desktop").write_text(f"[Desktop Entry]\nName={name}\nExec=x\n")
        self.apps = Path(tempfile.mkdtemp())
        for name in ("xfce4-terminal", "thunar", "chromium", "gnome-calculator"):
            (self.apps / f"{name}.desktop").write_text("[Desktop Entry]\n")
        self.conf = conf

    def test_locks_the_screen(self):
        out = self.mod.lock(self.home, "clients", [str(self.apps)])
        self.assertIn("removed from the panel: ", out)
        panel = ET.parse(self.conf / "xfce4-panel.xml").getroot()
        ids = [v.get("value") for v in panel.iter("value") if v.get("type") == "int"]
        self.assertNotIn("1", ids[2:7])
        self.assertIn("22", ids)
        self.assertNotIn("21", ids)
        self.assertNotIn("23", ids)
        self.assertFalse((self.desk / "xdg" / "xfce4" / "panel" / "launcher-21").exists())
        self.assertTrue((self.desk / "xdg" / "xfce4" / "panel" / "launcher-22").exists())
        keys = (self.conf / "xfce4-keyboard-shortcuts.xml").read_text()
        self.assertNotIn("TerminalEmulator", keys)
        self.assertNotIn("appfinder", keys)
        self.assertIn('name="override" type="bool" value="true"', keys)
        self.assertIn("cycle_windows_key", keys)
        desktop = (self.conf / "xfce4-desktop.xml").read_text()
        self.assertIn('name="desktop-menu"', desktop)
        hidden = self.desk / "xdg" / ".local-share" / "applications"
        self.assertIn("Hidden=true", (hidden / "xfce4-terminal.desktop").read_text())
        self.assertTrue((hidden / "gnome-calculator.desktop").exists())
        self.assertFalse((hidden / "chromium.desktop").exists())
        self.assertTrue(list(self.conf.glob("xfce4-panel.xml.bak-*")))

    def test_skips_a_running_screen(self):
        # The running check itself is tested against a made-up /proc in test_sandbox_fixes.py.
        with mock.patch.object(self.mod, "running", lambda desk: desk == self.desk):
            self.assertIn("skipped", self.mod.lock(self.home, "clients", [str(self.apps)]))
        self.assertIn("TerminalEmulator", (self.conf / "xfce4-keyboard-shortcuts.xml").read_text())


if __name__ == "__main__":
    unittest.main()
