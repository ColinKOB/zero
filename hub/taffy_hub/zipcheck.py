""




































































from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger("taffy.zipcheck")

SETTING = "zip_computer_check"


NOTES = "zip_signin_notes"
CHECKS = ("thinking", "apps", "mail", "browser", "signins", "files", "terminal", "board")
HOME_ROW = "home"


OK, FIXED, FAILED, SIGNED_OUT, UNSURE, SKIPPED = "ok", "fixed", "failed", "signed_out", "unsure", "skipped"
IDLE = "idle"

IMAGE = "taffy/zip-sandbox"
PUBLIC_PAGE = "https://example.com/"
PUBLIC_TITLE = "Example Domain"


FILES = {"pdf": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
         "docx": "https://raw.githubusercontent.com/python-openxml/python-docx/master/tests/test_files/test.docx"}
MAX_SITES = 10
PAGE_TEXT_MAX = 2000
THINK_TIMEOUT = 60
JUDGE_TIMEOUT = 30
EXEC_TIMEOUT = 90
MEMORY_LOW = 0.1
MEMORY_STALL = 10.0
DISK_LOW = 0.1
USE_DAYS = 14
EVIDENCE_MAX = 12
SKIP_DATA = config.PRIVATE_ZIPS

JUDGE = ("A browser loaded the front page of {site} with a saved sign-in. Below are the page's title, its address after "
         "any redirects, and the start of its visible text. Decide what the page shows.\n\n"
         "Title: {title}\nAddress: {url}\nText:\n{text}\n\n"
         "Answer with one word: SIGNED_IN if it shows someone signed in (an account menu, a dashboard, their name or "
         "courses), SIGNED_OUT if it asks to sign in or log in, CHECK if it is a human check (a captcha, \"verify you "
         "are human\", press and hold), or UNSURE if you can't tell.")




CONFIRM = ("A browser loaded the front page of {site} twice: visit A with a saved sign-in, and visit B as a stranger with "
           "no sign-in.\n\nVisit A\nTitle: {title}\nAddress: {url}\nText:\n{text}\n\n"
           "Visit B\nTitle: {b_title}\nAddress: {b_url}\nText:\n{b_text}\n\n"
           "Answer with one word. SIGNED_OUT only if visit A plainly shows that nobody is signed in: it stopped at a "
           "sign-in page or form, or it offers a Log In or Sign In control for a personal account. UNSURE if visit A is "
           "a general public page with no such control, or if you can't tell.")

NEED = ("The saved sign-in for {site} has run out, and a card would ask {first} to sign in again. Decide whether one of "
        "these helpers will need that sign-in soon. Here is what is known:\n\n{evidence}\n\n"
        "Answer with JSON only: {{\"needed\": true or false, \"zip\": \"the helper's name\", \"for\": \"to ...\"}}. "
        "\"for\" says what the helper needs the sign-in for, in under ten plain words, speaking to {first} as \"you\". "
        "needed is false when {first} chose to skip or pause this site, when the job is finished, or when nothing shows "
        "work coming that needs it. A visit with no job behind it is not a need.")




@dataclass
class Container:
    id: str
    name: str
    state: str
    image: str
    labels: dict
    mounts: list = field(default_factory=list)

    @property
    def profile(self) -> str:
        return self.labels.get("hermes-profile", "")

    @property
    def task(self) -> str:
        return self.labels.get("hermes-task-id", "")

    @property
    def is_zip_sandbox(self) -> bool:
        return self.image.split(":")[0] == IMAGE or self.labels.get("org.opencontainers.image.title") == IMAGE


class Probes:
    ""



    def _docker(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
        from .computers import _docker_env
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, env=_docker_env())

    def containers(self) -> list[Container]:
        ids = self._docker("ps", "-aq").stdout.split()
        if not ids:
            return []
        p = self._docker("inspect", *ids)
        out = []
        for c in json.loads(p.stdout or "[]"):
            out.append(Container(
                id=c["Id"][:12], name=c.get("Name", "").lstrip("/"), state=c.get("State", {}).get("Status", ""),
                image=c.get("Config", {}).get("Image", ""), labels=c.get("Config", {}).get("Labels") or {},
                mounts=[{"source": m.get("Source", ""), "destination": m.get("Destination", ""), "rw": bool(m.get("RW"))}
                        for m in c.get("Mounts") or []]))
        return out

    def exec(self, cid: str, script: str, timeout: float = EXEC_TIMEOUT) -> tuple[int, str]:
        try:
            p = self._docker("exec", cid, "sh", "-c", script, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        return p.returncode, (p.stdout + p.stderr)[-4000:]

    def remove_container(self, cid: str) -> bool:
        return self._docker("rm", "-f", cid, timeout=60).returncode == 0

    def image_of(self, bot: str) -> str:
        ""
        return _profile_value(bot, "docker_image")

    def work_folder(self, bot: str) -> str:
        ""
        for vol in _profile_list(bot, "docker_volumes"):
            host = vol.split(":")[0]
            if "/taffy/work/" in host:
                return host
        return ""

    def host_has(self, path: str) -> bool:
        return os.path.isfile(path)



    def model_for(self, bot: str):
        from . import modelsetup
        m = modelsetup.M.resolve(modelsetup.current(), bot, "everyday")
        if m is None:
            m = modelsetup.M.BY_ID.get(modelsetup._config_model(bot) or "")
        return m

    async def think(self, model) -> str:
        from . import shortreply
        with shortreply.reading(()):
            return await shortreply.ask_helper("Reply with the one word OK.", model=model, timeout=THINK_TIMEOUT,
                                               max_tokens=5, effort="low", fallback=False)

    async def judge(self, prompt: str) -> str:
        from . import shortreply
        with shortreply.reading(()):
            return await shortreply.ask_helper(prompt, timeout=JUDGE_TIMEOUT, max_tokens=5, effort="low")

    async def need(self, prompt: str) -> str:
        from . import shortreply
        with shortreply.reading(()):
            return await shortreply.ask_helper(prompt, timeout=JUDGE_TIMEOUT, max_tokens=120, effort="low")

    def need_evidence(self, site: dict) -> list[str]:
        return _need_evidence(site)



    def mailboxes(self, bot: str) -> list[str]:
        from . import home, mail
        out = []
        for a in home.mail_accounts():
            owner = str(a.get("owner_zip") or config.LEAD.id)
            if owner == bot and str(a["id"]) in mail.ACCOUNTS:
                out.append(str(a["id"]))
        return out

    def list_mail(self, account: str) -> None:
        from . import mail
        mail.envelopes(account, "INBOX", page_size=1)

    def apps_for(self, bot: str) -> list[str]:
        from . import apps
        return [a for a in APP_READS if apps.connected_natively(a, bot_id=bot)]

    def read_app(self, app: str, bot: str) -> None:
        from . import apps
        action, args = APP_READS[app]
        apps.call(app, action, dict(args), bot)



    def profile(self, bot: str) -> Path:
        from . import screen
        return screen.profile_home(bot) / "bot-desktop" / "browser-profile"

    def live_browser(self, bot: str) -> tuple[str, int | None]:
        ""
        from . import cdp_storage, screen
        profile = self.profile(bot)
        pid = _lock_pid(profile)
        if pid is None:
            return "off", None
        try:
            if screen.status(bot).get("holder") == "human":
                return "human", pid
        except Exception:
            pass
        for attempt in range(2):
            try:
                with cdp_storage.Browser(profile) as b:
                    b.call("Browser.getVersion")
                return "ok", pid
            except cdp_storage.Unavailable:
                if attempt == 0:
                    time.sleep(5)
        return "wedged", pid

    def stop_browser(self, bot: str, pid: int) -> bool:
        ""
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            return False
        if f"--user-data-dir={self.profile(bot)}".encode() not in cmd:
            return False
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        for _ in range(50):
            if not Path(f"/proc/{pid}").exists():
                return True
            time.sleep(.1)
        return not Path(f"/proc/{pid}").exists()

    def browser_probe(self, bot: str | None, url: str, site: str = "", snapshot: dict | None = None) -> dict:
        ""


        return _headless_visit(self.profile(bot) if bot else None, url, site, snapshot)

    def signin_sites(self) -> list[dict]:
        return _signin_sites()

    def snapshot(self, site: str) -> dict | None:
        from . import shared_sessions
        row = shared_sessions._row(site)
        return shared_sessions._snapshot(row) if row and not row["removed"] else None



    def swap(self) -> tuple[int, int]:
        ""
        total = free = 0
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("SwapTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("SwapFree:"):
                free = int(line.split()[1]) * 1024
        return total - free, total

    def memory(self) -> tuple[int, int, float]:
        ""

        info = {ln.split(":")[0]: int(ln.split()[1]) * 1024 for ln in Path("/proc/meminfo").read_text().splitlines()
                if ln.split(":")[0] in ("MemAvailable", "MemTotal")}
        stall = 0.0
        try:
            for ln in Path("/proc/pressure/memory").read_text().splitlines():
                if ln.startswith("full "):
                    stall = float(ln.split("avg300=")[1].split()[0])
        except (OSError, IndexError, ValueError):
            pass
        return info.get("MemAvailable", 0), info.get("MemTotal", 0), stall

    def disk(self) -> tuple[int, int]:
        ""
        u = shutil.disk_usage(config.HOME)
        return u.free, u.total

    def mac_linked(self) -> bool:
        from . import mac
        return mac.online()




APP_READS = {"trello": ("boards", {}), "google_drive": ("recent", {"limit": 1}),
             "outlook": ("mail", {"query": "", "limit": 1}), "dropbox": ("list", {"path": "", "limit": 1}),
             "notion": ("search", {"query": "", "limit": 1}), "linear": ("my_issues", {"limit": 1}),
             "github": ("repos", {"limit": 1})}


def _profile_text(bot: str) -> str:
    from . import screen
    try:
        return (screen.profile_home(bot) / "config.yaml").read_text(errors="replace")
    except OSError:
        return ""


def _terminal_block(bot: str) -> str:
    m = re.search(r"(?m)^terminal:[ \t]*\n((?:[ \t]+.*\n?|[ \t]*\n)*)", _profile_text(bot))
    return m.group(1) if m else ""


def _profile_value(bot: str, key: str) -> str:
    m = re.search(rf"(?m)^\s+{key}:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", _terminal_block(bot))
    return m.group(1).strip() if m else ""


def _profile_list(bot: str, key: str) -> list[str]:
    m = re.search(rf"(?m)^(\s+){key}:\s*\n((?:\1[ \t]*-.*\n?)*)", _terminal_block(bot))
    if not m:
        return []
    return [x.strip().strip("'\"") for x in re.findall(r"-\s*(.+)", m.group(2))]


def _lock_pid(profile: Path) -> int | None:
    ""
    lock = profile / "SingletonLock"
    try:
        pid = int(os.readlink(lock).rsplit("-", 1)[1])
    except (OSError, ValueError, IndexError):
        return None
    return pid if Path(f"/proc/{pid}").exists() else None




_VISIT_JS = ("JSON.stringify({title: document.title, url: location.href, ready: document.readyState, "
             "text: (document.body ? document.body.innerText : '').slice(0, %d)})" % PAGE_TEXT_MAX)
_COPY = ("Local State", "Default/Cookies", "Default/Network/Cookies", "Default/Local Storage", "Default/Preferences")


def _copy_profile(profile: Path, to: Path) -> str | None:
    ""
    if not profile.is_dir():
        return "its browser profile is missing"
    try:
        json.loads((profile / "Local State").read_text())
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return "its browser profile's settings file is damaged"
    for relative in _COPY:
        source, target = profile / relative, to / relative
        if source.is_symlink() or not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if source.is_dir():
                shutil.copytree(source, target, symlinks=False)
            elif source.name == "Cookies":
                with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
                    if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        return "its saved sign-ins file is damaged"
                    src.backup(dst)
            else:
                shutil.copy2(source, target)
        except sqlite3.DatabaseError:
            return "its saved sign-ins file is damaged"
        except OSError as e:
            return f"its browser profile couldn't be read ({type(e).__name__})"
    return None


def _headless_visit(profile: Path | None, url: str, site: str = "", snapshot: dict | None = None,
                    wait: float = 15) -> dict:
    from . import cdp_storage
    try:
        from tools.bot_desktop.browser import dock_argv, executable
    except ImportError:
        return {"error": "the browser program isn't installed"}
    binary = executable()
    if not binary or not Path(binary).is_file():
        return {"error": "the browser program isn't installed"}
    with tempfile.TemporaryDirectory(prefix="zero-check-browser-") as folder:
        copied = Path(folder)
        if profile is not None:
            problem = _copy_profile(profile, copied)
            if problem:
                return {"error": problem, "profile": True}

        argv = dock_argv(binary, str(copied)) + ["--headless", "--disable-background-networking", "--disable-sync",
                                                    "--disable-extensions", "--mute-audio", "about:blank"]
        process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            for _ in range(200):
                if (copied / "DevToolsActivePort").exists():
                    break
                if process.poll() is not None:
                    return {"error": "the browser didn't start"}
                time.sleep(.05)
            else:
                return {"error": "the browser didn't start in time"}
            browser = None
            for _ in range(40):
                try:
                    browser = cdp_storage.Browser(copied)
                    break
                except cdp_storage.NotReady:
                    time.sleep(.25)
            if browser is None:
                return {"error": "the browser started but didn't answer"}
            with browser:
                if snapshot is not None:
                    browser.restore(site, snapshot)
                target = browser.call("Target.createTarget", {"url": "about:blank"})["targetId"]
                session = browser.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]


                agent = browser.call("Browser.getVersion").get("userAgent", "")
                if "HeadlessChrome" in agent:
                    browser.call("Emulation.setUserAgentOverride",
                                 {"userAgent": agent.replace("HeadlessChrome", "Chrome")}, session)
                browser.call("Page.navigate", {"url": url}, session)
                found, deadline = {}, time.monotonic() + wait
                while time.monotonic() < deadline:
                    time.sleep(.5)
                    try:
                        out = browser.call("Runtime.evaluate", {"expression": _VISIT_JS, "returnByValue": True},
                                           session)
                        found = json.loads(out.get("result", {}).get("value") or "{}")
                    except (cdp_storage.Unavailable, ValueError):
                        continue
                    if found.get("ready") == "complete" and found.get("url", "").startswith("http"):
                        time.sleep(1.5)
                        out = browser.call("Runtime.evaluate", {"expression": _VISIT_JS, "returnByValue": True},
                                           session)
                        found = json.loads(out.get("result", {}).get("value") or "{}") or found
                        break
                if not found.get("url", "").startswith("http"):
                    return {"error": "the page didn't load"}
                return {"title": str(found.get("title") or ""), "url": str(found.get("url") or ""),
                        "text": str(found.get("text") or "")}
        except Exception as e:
            return {"error": f"the browser failed ({type(e).__name__})"}
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)




def _signin_sites(limit: int = MAX_SITES) -> list[dict]:
    ""


    from . import consent, home, logins, shared_sessions, signin_access, store
    db = store.db()
    shared = {r["site"] for r in db.execute("SELECT site FROM shared_sessions WHERE removed=0 AND sealed IS NOT NULL")}
    counts: dict[str, int] = {}
    last: dict[str, tuple[float, str, str]] = {}
    for row in db.execute("SELECT bot_id, body, created_at FROM feed WHERE kind='login' ORDER BY id"):
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue


        if not isinstance(card, dict) or card.get("purpose", "signin") != "signin" or card.get("from_check"):
            continue
        site = consent.base(consent.site_of(card.get("site") or ""))
        if site not in shared:
            continue
        counts[site] = counts.get(site, 0) + 1
        last[site] = (float(row["created_at"] or 0), row["bot_id"], str(card.get("url") or ""))
    course = consent.base(consent.site_of(str(home.get("school.course_site") or "")))
    if course in shared:
        counts[course] = max(counts.get(course, 0), 1)
    ranked = sorted(counts, key=lambda s: (s != course, -counts[s], s))[:limit]
    out = []
    for site in ranked:
        zips = sorted(b for b in config.BOTS_BY_ID if not config.is_sandbox(b) and b not in SKIP_DATA
                      and signin_access.allowed(b, site))
        if not zips:
            continue
        _, needer, url = last.get(site, (0, "", ""))
        needer = needer if needer in zips else zips[0]


        asker = config.LEAD.id if config.LEAD.id in zips else needer
        out.append({"site": site, "url": _front_page(site, url), "zips": zips, "asker": asker, "needer": needer,
                    "saved": any(logins.has_for_site(b, site) for b in zips), "cards": counts[site]})
    return out


_SIGN_IN_HOSTS = {"signin", "login", "accounts", "account", "auth", "sso", "id", "idmsa"}


def _front_page(site: str, card_url: str) -> str:
    ""

    from . import cdp_storage, consent
    origin = cdp_storage.web_origin(card_url)


    if consent.site_of(card_url).split(".")[0] in _SIGN_IN_HOSTS:
        return f"https://{site}/"
    return (origin + "/") if origin.startswith("https://") else f"https://{site}/"




@dataclass
class Run:
    table: dict = field(default_factory=dict)
    fixed: list = field(default_factory=list)
    would_fix: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    settled: set = field(default_factory=set)
    left_out: dict = field(default_factory=dict)
    model_calls: int = 0
    seconds: float = 0.0

    def set(self, row: str, check: str, state: str, reason: str = "") -> None:
        self.table.setdefault(row, {})[check] = {"state": state, "reason": reason}


def _zips() -> list[str]:
    return [b for b in config.BOTS_BY_ID if not config.is_sandbox(b)]


def _name(bot: str) -> str:
    b = config.BOTS_BY_ID.get(bot)
    return b.name if b else bot


async def check(dry: bool = False, probes: Probes | None = None) -> Run:
    p = probes or Probes()
    began = time.monotonic()
    run = Run()
    zips = _zips()
    for z in zips:
        run.table[z] = {}
    containers = await asyncio.to_thread(_safe, p.containers, [])
    await _housekeeping(run, p, containers, dry)
    await _thinking(run, p, zips)
    for z in zips:
        await asyncio.to_thread(_mail_and_apps, run, p, z)
    for z in zips:
        await asyncio.to_thread(_browser, run, p, z, dry)
    await _signins(run, p)
    by_zip = _work_containers(containers, {z: _safe(p.image_of, "", z) for z in zips})
    for z in zips:
        await asyncio.to_thread(_in_container, run, p, z, by_zip.get(z))
    run.seconds = round(time.monotonic() - began, 1)
    return run


def _safe(fn, default, *args):
    try:
        return fn(*args)
    except Exception:
        log.warning("computer check step failed", exc_info=True)
        return default


async def _housekeeping(run: Run, p: Probes, containers: list[Container], dry: bool) -> None:


    known = set(config.BOTS_BY_ID)
    strays = [c for c in containers if c.is_zip_sandbox and _owner(c) and _owner(c) not in known]
    if not strays:
        run.set(HOME_ROW, "containers", OK, f"{len(containers)} containers, all for current Zips")
    elif dry:
        run.would_fix += [f"remove container {c.name} (made for {_owner(c)}, which is no longer a Zip)" for c in strays]
        run.set(HOME_ROW, "containers", FAILED,
                f"{len(strays)} {'belongs' if len(strays) == 1 else 'belong'} to no current Zip")
    else:
        gone = [c for c in strays if await asyncio.to_thread(p.remove_container, c.id)]
        run.fixed += [f"removed container {c.name} (made for {_owner(c)}, which is no longer a Zip)" for c in gone]
        left = len(strays) - len(gone)
        run.set(HOME_ROW, "containers", FIXED if not left else FAILED,
                f"removed {len(gone)} that belonged to no current Zip" + (f"; {left} wouldn't go" if left else ""))
    used, total = await asyncio.to_thread(_safe, p.swap, (0, 0))
    available, memory, stall = await asyncio.to_thread(_safe, p.memory, (0, 0, 0.0))
    swap = f"swap {round(100 * used / total) if total else 0}% used"
    room = f"{available / 2 ** 30:.1f} GB of memory available"
    if memory and available / memory < MEMORY_LOW:
        run.set(HOME_ROW, "swap", FAILED, f"memory is short: {room}, {swap}")
    elif stall >= MEMORY_STALL:
        run.set(HOME_ROW, "swap", FAILED, f"programs are waiting on memory {stall:.0f}% of the time; {room}, {swap}")
    else:
        run.set(HOME_ROW, "swap", OK, f"{swap}, {room}")
    free, size = await asyncio.to_thread(_safe, p.disk, (1, 1))
    run.set(HOME_ROW, "disk", FAILED if size and free / size < DISK_LOW else OK,
            f"{round(100 * free / size) if size else 0}% free")
    linked = await asyncio.to_thread(_safe, p.mac_linked, False)
    run.set(HOME_ROW, "mac", OK if linked else SKIPPED,
            "linked" if linked else "not linked now (asleep or away), which is never a failure")


def _owner(c: Container) -> str:
    ""
    if c.profile:
        return config.LEAD.id if c.profile == "default" else c.profile
    if c.name.startswith("zip-ports-"):
        return c.name.removeprefix("zip-ports-")
    return ""


def _work_containers(containers: list[Container], images: dict[str, str] | None = None) -> dict[str, Container]:
    ""


    out: dict[str, Container] = {}
    for c in containers:
        if not c.is_zip_sandbox or c.state != "running" or not c.profile:
            continue
        z = _owner(c)
        wanted = (images or {}).get(z)
        if wanted and c.image != wanted:
            continue
        if z not in out or (c.task == "default" and out[z].task != "default"):
            out[z] = c
    return out


async def _thinking(run: Run, p: Probes, zips: list[str]) -> None:
    by_model: dict[str, tuple[object, list[str]]] = {}
    for z in zips:
        m = _safe(p.model_for, None, z)
        if m is None:
            run.set(z, "thinking", SKIPPED, "its model isn't one the hub can call")
            continue
        if getattr(m, "keyed", False) is True:


            run.set(z, "thinking", SKIPPED, f"{m.key} runs on its own key, which its turns check")
            continue
        by_model.setdefault(m.key, (m, []))[1].append(z)
    for key, (m, users) in by_model.items():
        run.model_calls += 1
        try:
            answer = await p.think(m)
            state, reason = (OK, f"{key} answered") if answer.strip() else (FAILED, f"{key} gave an empty answer")
        except Exception as e:
            kind = getattr(e, "kind", "") or type(e).__name__
            state, reason = FAILED, f"{key} didn't answer ({kind})"
        for z in users:
            run.set(z, "thinking", state, reason)


def _mail_and_apps(run: Run, p: Probes, z: str) -> None:
    if z in SKIP_DATA:
        run.set(z, "mail", SKIPPED, "this Zip's data is left out of the daily checks")
        run.set(z, "apps", SKIPPED, "this Zip's data is left out of the daily checks")
        return
    boxes = _safe(p.mailboxes, [], z)
    bad = []
    for aid in boxes:
        try:
            p.list_mail(aid)
        except Exception as e:
            bad.append(f"{aid} ({type(e).__name__})")
    run.set(z, "mail", SKIPPED if not boxes else FAILED if bad else OK,
            "no mailbox of its own" if not boxes else f"couldn't list {', '.join(bad)}" if bad
            else f"{len(boxes)} mailbox{'es' if len(boxes) > 1 else ''} listed")
    connected = _safe(p.apps_for, [], z)
    bad = []
    for app in connected:
        try:
            p.read_app(app, z)
        except Exception as e:
            bad.append(f"{app} ({type(e).__name__})")
    run.set(z, "apps", SKIPPED if not connected else FAILED if bad else OK,
            "no apps connected" if not connected else f"couldn't read {', '.join(bad)}" if bad
            else f"read {', '.join(connected)}")


def _browser(run: Run, p: Probes, z: str, dry: bool) -> None:
    live, pid = _safe(p.live_browser, ("off", None), z)
    note = ""
    if live == "wedged" and pid:
        if dry:
            run.would_fix.append(f"stop {_name(z)}'s browser, which is running but not answering, so it starts fresh")
            note = "; its own browser is running but not answering"
        elif _safe(p.stop_browser, False, z, pid):
            run.fixed.append(f"stopped {_name(z)}'s browser, which was running but not answering")
            live = "fixed"
        else:
            note = "; its own browser is running but not answering, and it wouldn't stop"
    found = _safe(p.browser_probe, {"error": "the check itself failed"}, z, PUBLIC_PAGE)
    if found.get("error"):
        run.set(z, "browser", FAILED, found["error"] + note)
    elif PUBLIC_TITLE not in found.get("title", ""):
        run.set(z, "browser", FAILED, "the browser started but the public page didn't load" + note)
    elif note:
        run.set(z, "browser", FAILED, "a fresh browser works" + note)
    else:
        run.set(z, "browser", FIXED if live == "fixed" else OK,
                "restarted, then loaded a page" if live == "fixed" else "started and loaded a public page")


async def _read_site(run: Run, p: Probes, s: dict) -> tuple[str, str]:
    ""
    snapshot = await asyncio.to_thread(_safe, p.snapshot, None, s["site"])
    if snapshot is None:
        return UNSURE, "no saved sign-in to load"
    page = await asyncio.to_thread(_safe, p.browser_probe, {"error": "the check failed"}, None, s["url"], s["site"],
                                   snapshot)
    del snapshot
    if page.get("error"):
        return UNSURE, page["error"]
    run.model_calls += 1
    prompt = JUDGE.format(site=s["site"], title=page["title"][:200], url=page["url"][:300],
                          text=page["text"][:PAGE_TEXT_MAX])
    word = await _word(p, prompt)
    verdict = {"SIGNED_IN": OK, "SIGNED_OUT": SIGNED_OUT, "CHECK": "check"}.get(word, UNSURE)
    if verdict == SIGNED_OUT:
        stranger = await asyncio.to_thread(_safe, p.browser_probe, {"error": "the check failed"}, None, s["url"],
                                           s["site"], None)
        if stranger.get("error"):
            return UNSURE, stranger["error"]
        run.model_calls += 1
        word = await _word(p, CONFIRM.format(
            site=s["site"], title=page["title"][:200], url=page["url"][:300], text=page["text"][:PAGE_TEXT_MAX],
            b_title=stranger["title"][:200], b_url=stranger["url"][:300], b_text=stranger["text"][:PAGE_TEXT_MAX]))
        verdict = SIGNED_OUT if word == "SIGNED_OUT" else UNSURE
    return verdict, {"check": "a human check", UNSURE: "the page didn't say"}.get(verdict, "")


async def _word(p: Probes, prompt: str) -> str:
    try:
        return (await p.judge(prompt)).strip().strip(".").upper()
    except Exception:
        return "UNSURE"


def _notes() -> dict:
    from . import store
    notes = store.get_setting(NOTES, {}) or {}
    return notes if isinstance(notes, dict) else {}


def _save_notes(notes: dict) -> None:
    from . import store
    store.set_setting(NOTES, notes)


def _last_check() -> float:
    from . import store
    return float((store.get_setting(SETTING, {}) or {}).get("at") or 0)


async def _weigh(run: Run, p: Probes, s: dict, notes: dict, last_check: float) -> str:
    ""
    site = s["site"]
    if site in notes.get("left_out", {}):
        return IDLE
    if float(notes.get("signed_in", {}).get(site) or 0) > last_check > 0:

        run.left_out[site] = "a fresh sign-in did not last until the next morning's check"
        log.info("computer check: leaving %s out of the sign-in check: %s", site, run.left_out[site])
        return IDLE
    if site in notes.get("not_now", {}):
        return IDLE
    need = await _need(run, p, s)
    if need is None:
        return IDLE
    run.asks.append({"site": site, "url": s["url"], "zips": s["zips"], "asker": s["asker"], **need})
    return SIGNED_OUT


async def _need(run: Run, p: Probes, s: dict) -> dict | None:
    ""

    from . import who
    evidence = await asyncio.to_thread(_safe, p.need_evidence, [], s)
    if not evidence:
        return None
    run.model_calls += 1
    try:
        answer = await p.need(NEED.format(site=_label(s["url"], s["site"]), first=who.first(),
                                          evidence="\n".join(f"- {line}" for line in evidence[:EVIDENCE_MAX])))
        found = json.loads(re.search(r"\{.*\}", answer, re.S).group(0))
    except Exception as e:
        log.info("computer check: could not weigh the need for %s (%s)", s["site"], type(e).__name__)
        return None
    if not isinstance(found, dict) or found.get("needed") is not True:
        return None
    needer = _zip_named(found.get("zip"), s)
    purpose = " ".join(str(found.get("for") or "").split()).rstrip(".")[:90]
    purpose = purpose if purpose.lower().startswith("to ") else "for its work"
    subject = "I need" if needer == s["asker"] else f"{_name(needer)} needs"
    return {"needer": needer, "why": who.to_you(f"{subject} this sign-in {purpose}.")}


def _zip_named(name, s: dict) -> str:
    said = str(name or "").strip().casefold()
    for z in s["zips"]:
        if said and said in (z.casefold(), _name(z).casefold()):
            return z
    return s.get("needer") or s["asker"]


def _label(url: str, site: str) -> str:
    from . import consent
    return (consent.site_of(url) or site).removeprefix("www.")




_PLAIN_LABELS = {"www", "login", "signin", "secure", "students", "portal", "account", "accounts", "auth", "app", "apps",
                 "my"}


def _site_words(s: dict) -> set[str]:
    ""

    labels = {s["site"].split(".")[0], _label(s["url"], s["site"]).split(".")[0]}
    return {w[:8] for w in labels if len(w) >= 4 and w not in _PLAIN_LABELS}


def _names_site(text, words: set[str]) -> bool:
    squeezed = re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    return any(w in squeezed for w in words)


def _visits(z: str, site: str, now: float) -> str:
    ""
    from datetime import datetime

    from . import consent, lineage
    path = lineage._state_db(z)
    if not path.exists():
        return ""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        rows = conn.execute("SELECT timestamp, tool_calls FROM messages WHERE timestamp > ? AND tool_calls LIKE ?",
                            (now - USE_DAYS * 86400, f"%{site}%")).fetchall()
    finally:
        conn.close()
    days = set()
    for at, calls in rows:
        opened = re.findall(r"https?://[^\s\"'\\]+", calls or "") if "browser_navigate" in (calls or "") else []
        if any(consent.base(consent.site_of(u)) == site for u in opened):
            days.add(datetime.fromtimestamp(float(at)).date())
    if not days:
        return ""
    return (f"{_name(z)} opened this site on {len(days)} of the last {USE_DAYS} days, most recently "
            f"{max(days).strftime('%b %-d')}.")


def _open_work(words: set[str], zips: list[str]) -> list[str]:
    from . import openloops
    rows = openloops._db().execute("SELECT bot_id, topic, summary, next_step FROM open_loops WHERE status='open'")
    return [f"Open job for {_name(r['bot_id'])}: {r['topic']}. Next step: {r['next_step'] or 'none'}"[:260] for r in rows
            if r["bot_id"] in zips and _names_site(f"{r['topic']} {r['summary']} {r['next_step']}", words)]


def _board_work(words: set[str], zips: list[str]) -> list[str]:
    from . import board
    groups = board.grouped()
    return [f"Board task for {_name(t.get('assignee') or config.LEAD.id)}: {t.get('title')}"[:260]
            for t in groups.get("needs_you", []) + groups.get("working", [])
            if (t.get("assignee") or config.LEAD.id) in zips and _names_site(f"{t.get('title')} {t.get('body')}", words)]


def _routines(words: set[str], zips: list[str]) -> list[str]:
    from . import watcher
    out = []
    for z in zips:
        home = config.HERMES_HOME if z == config.LEAD.id else config.HERMES_HOME / "profiles" / z
        for job in watcher._jobs_of(home / "cron" / "jobs.json").values():
            if job.get("enabled") is not False and _names_site(f"{job.get('name')} {job.get('prompt')}", words):
                out.append(f"{_name(z)} has a routine that uses this site: {job.get('name') or 'unnamed'}."[:260])
    return out


def _decisions(words: set[str]) -> list[str]:
    from . import facts
    holds, args = facts.live()
    rows = facts._db().execute(f"SELECT kind, text FROM facts WHERE {holds} AND kind IN ('decision', 'preference')", args)
    return [f"Saved {r['kind']}: {r['text']}"[:260] for r in rows if _names_site(r["text"], words)]


def _need_evidence(s: dict, now: float | None = None) -> list[str]:
    ""
    now = time.time() if now is None else now
    words, zips = _site_words(s), [z for z in s["zips"] if not config.is_sandbox(z)]
    out = list(_safe(_decisions, [], words))
    out += _safe(_open_work, [], words, zips) + _safe(_board_work, [], words, zips) + _safe(_routines, [], words, zips)
    return out + [line for line in (_safe(_visits, "", z, s["site"], now) for z in zips) if line]


async def _signins(run: Run, p: Probes) -> None:
    sites = await asyncio.to_thread(_safe, p.signin_sites, [])
    notes = await asyncio.to_thread(_safe, _notes, {})
    last_check = await asyncio.to_thread(_safe, _last_check, 0.0)
    verdicts: dict[str, list[tuple[str, str]]] = {}
    for s in sites:
        verdict, why = await _read_site(run, p, s)
        if verdict == SIGNED_OUT and s.get("saved"):
            verdict = "saved"
        if verdict == SIGNED_OUT:
            verdict = await _weigh(run, p, s, notes, last_check)
        if verdict in (OK, "saved", IDLE):
            run.settled.add(s["site"])
        label = f"{s['site']} ({why})" if why and verdict in (UNSURE, "check") else s["site"]
        for z in s["zips"]:
            verdicts.setdefault(z, []).append((label, verdict))
    for z in run.table:
        if z == HOME_ROW:
            continue
        mine = verdicts.get(z)
        if not mine:
            run.set(z, "signins", SKIPPED, "no shared sign-in it needs was checked")
            continue
        out = [site for site, v in mine if v == SIGNED_OUT]
        unsure = [site for site, v in mine if v in (UNSURE, "check")]
        saved = [site for site, v in mine if v == "saved"]
        idle = [site for site, v in mine if v == IDLE]
        bits = ([f"signed out of {', '.join(out)}"] if out else []) + \
               ([f"couldn't tell for {', '.join(unsure)}"] if unsure else []) + \
               ([f"will sign in with a saved login at {', '.join(saved)}"] if saved else []) + \
               ([f"signed out of {', '.join(idle)}, which no job needs now"] if idle else [])
        ok = len(mine) - len(out) - len(unsure) - len(saved) - len(idle)
        state = SIGNED_OUT if out else UNSURE if unsure else OK
        run.set(z, "signins", state, "; ".join(bits + [f"signed in at {ok} of {len(mine)}"]))


FILES_SCRIPT = r"""
d=$(mktemp -d /tmp/zero-check-XXXXXX) || exit 1
trap 'rm -rf "$d"' EXIT
command -v readfile >/dev/null 2>&1 || echo "noreader"
for k in pdf docx; do
  eval u=\$U_$k
  if ! curl -fsSL --max-time 30 -o "$d/t.$k" "$u"; then echo "$k nodownload"; continue; fi
  if command -v readfile >/dev/null 2>&1; then
    n=$(readfile "$d/t.$k" 2>/dev/null | tr -d '[:space:]' | wc -c)
    if [ "$n" -gt 0 ]; then echo "$k ok"; else echo "$k unread"; fi
  fi
done
"""

TERMINAL_SCRIPT = r"""
echo "run ok"
d=@DIR@
mkdir -p "$d"
trap 'rm -rf "$d"' EXIT
{ @SPAWN@; } >"$d/out" 2>&1
p=$(tail -n 1 "$d/out")
sleep 1
c=$(pgrep -f '^sleep @MARK@$' 2>/dev/null | head -1)
[ -n "$c" ] || { echo "nochild"; @STOP@ 2>/dev/null; exit 0; }
@STOP@ 2>/dev/null
sleep 1
if kill -0 "$c" 2>/dev/null; then echo "child left"; kill "$c" 2>/dev/null; else echo "child gone"; fi
"""


def _old_spawn(command: str, temp_dir: str, log: str, pid: str, exit_path: str) -> str:
    ""
    return (f"mkdir -p {temp_dir} && ( nohup bash -lc {shlex.quote(command)} > {log} 2>&1; "
            f"rc=$?; printf '%s\\n' \"$rc\" > {exit_path} ) & echo $! > {pid} && cat {pid}")


def terminal_script() -> str:
    ""

    try:
        from tools import sandbox_process_group as group
        spawn, stop = group.spawn_command, group.stop_command('"$p"')
    except ImportError:
        spawn, stop = _old_spawn, 'kill "$p"'
    folder = f"/tmp/zero-check-term-{secrets.token_hex(6)}"
    mark = str(61000 + secrets.randbelow(999))
    started = spawn(f'sh -c "sleep {mark} & wait"', folder, f"{folder}/log", f"{folder}/pid", f"{folder}/exit")
    return (TERMINAL_SCRIPT.replace("@DIR@", folder).replace("@SPAWN@", started).replace("@STOP@", stop)
            .replace("@MARK@", mark))


BOARD_SCRIPT = r"""
f="$W/.zero-check-$T"
if echo check > "$f" 2>/dev/null; then echo "wrote"; else echo "nowrite"; fi
"""


def _in_container(run: Run, p: Probes, z: str, c: Container | None) -> None:
    if z == config.LEAD.id and c is None:
        for name in ("files", "terminal", "board"):
            run.set(z, name, SKIPPED, "this Zip works on the server itself, not in a container")
        return
    if c is None:
        for name in ("files", "terminal", "board"):
            run.set(z, name, SKIPPED, "its computer isn't running now; it starts on its next task")
        return

    env = " ".join(f"U_{k}='{u}'" for k, u in FILES.items())
    rc, out = p.exec(c.id, f"{env}; {FILES_SCRIPT}")
    lines = set(out.split("\n"))
    if rc == 124:
        run.set(z, "files", FAILED, "the file test timed out")
    elif "noreader" in lines:
        run.set(z, "files", FAILED, "its computer has no program to read a PDF or Word file (readfile)"
                + ("; the test files downloaded" if not any(x.endswith("nodownload") for x in lines) else ""))
    else:
        bad = [x for x in lines if x.split(" ")[-1] in ("nodownload", "unread")]
        run.set(z, "files", FAILED if bad else OK,
                ("; ".join(f"the {x.split()[0]} file " + ("didn't download" if x.endswith("nodownload")
                                                          else "came out empty") for x in sorted(bad)))
                if bad else "downloaded and read a PDF and a Word file")

    rc, out = p.exec(c.id, terminal_script())
    lines = set(out.split("\n"))
    if "run ok" not in lines:
        run.set(z, "terminal", FAILED, "a command didn't run" + (" (timed out)" if rc == 124 else ""))
    elif "child left" in lines:
        run.set(z, "terminal", FAILED, "stopping a background command left its child running (reports 38 and 39)")
    elif "child gone" in lines:
        run.set(z, "terminal", OK, "ran a command, and a stopped background command left nothing behind")
    else:
        run.set(z, "terminal", UNSURE, "ran a command; the background test couldn't start")


    work = p.work_folder(z)
    mounts = {m["destination"]: m for m in c.mounts}
    problems = []
    if not work:
        problems.append("no work folder is set for it")
    elif work not in mounts or not mounts[work]["rw"]:
        problems.append("its work folder isn't mounted read-write")
    else:
        token = secrets.token_hex(6)
        rc, out = p.exec(c.id, f"W='{work}'; T='{token}'; {BOARD_SCRIPT}")
        path = f"{work}/.zero-check-{token}"
        seen = p.host_has(path)
        p.exec(c.id, f"rm -f '{path}'")
        if "wrote" not in out:
            problems.append("it can't write in its work folder")
        elif not seen:
            problems.append("a file it writes in its work folder doesn't show on the server, so it can't be attached")
    board = str(config.HERMES_HOME / "kanban" / "workspaces" / z)
    if board not in mounts or not mounts[board]["rw"]:
        problems.append("board tasks' own work folders aren't mounted, so a board worker starts without one "
                        "(report 82)")
    run.set(z, "board", FAILED if problems else OK, "; ".join(problems) or "work folder mounted and attachable")




def _open_cards() -> list[tuple[dict, dict]]:
    ""
    from . import signin, store
    out = []
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? ORDER BY id", (signin.KIND,)).fetchall():
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if isinstance(card, dict) and card.get("state") == "open" and card.get("purpose", "signin") == "signin":
            out.append((dict(row), card))
    return out


def _raise(a: dict) -> int | None:
    from . import signin, store
    site = signin.consent.site_of(a["url"])
    before = signin._open_card(a["asker"], site, signin.Purpose.signin)
    if before and not json.loads(before["body"] or "{}").get("from_check"):
        return None
    try:
        signin.ask(a["asker"], a["url"], a["why"], None, failure_reason=a["why"])
    except signin.AskError:
        log.warning("could not raise the sign-in card for %s", a["site"])
        return None
    row = signin._open_card(a["asker"], site, signin.Purpose.signin)
    if not row:
        return None
    card = json.loads(row["body"] or "{}")
    card["from_check"] = True
    store.update_feed(row["id"], row["title"], json.dumps(card))
    return row["id"]


def raise_asks(run: Run) -> list[int]:
    ""


    from . import signin, store
    wanted = {a["site"]: a for a in run.asks}
    showing, held = None, set()
    for row, card in _open_cards():
        site = signin.consent.base(signin.consent.site_of(card.get("site") or ""))
        if not card.get("from_check"):
            held.add(site)
            continue
        ask = wanted.get(site)
        if site in run.settled or (ask and showing):
            card.update(state="retired", at=time.time())
            store.update_feed(row["id"], row["title"], json.dumps(card))
        elif ask:
            showing = site
            card.update(why=ask["why"], failure_reason=ask["why"])
            store.update_feed(row["id"], row["title"], json.dumps(card))
        elif showing is None:
            showing = site
    line = [a for a in run.asks if a["site"] != showing and a["site"] not in held]
    ids = []
    if showing is None and line:
        first = line.pop(0)
        raised = _raise(first)
        ids += [raised] if raised else []
    notes = _notes()
    notes["waiting"] = line
    _save_notes(notes)
    return ids


def card_answered(site: str, outcome: str, from_check: bool) -> None:
    ""

    from . import consent
    base = consent.base(consent.site_of(site) or site)
    notes = _notes()
    if outcome == "signed_in":
        notes.setdefault("signed_in", {})[base] = time.time()
        notes.get("not_now", {}).pop(base, None)
        notes.get("left_out", {}).pop(base, None)
    elif outcome == "declined" and from_check:
        notes.setdefault("not_now", {})[base] = time.time()
    line = [a for a in notes.get("waiting", []) if a.get("site") != base]
    next_up = line.pop(0) if from_check and line and not any(c.get("from_check") for _, c in _open_cards()) else None
    notes["waiting"] = line
    _save_notes(notes)
    if next_up:
        _raise(next_up)


def lines(run: Run) -> list[str]:
    ""
    out = [f"{_label(a['url'], a['site'])}: {a['why']}" for a in run.asks]
    down = sorted({_name(z) for z, row in run.table.items() if z != HOME_ROW
                   and row.get("thinking", {}).get("state") == FAILED})
    if down:
        names = down[0] if len(down) == 1 else ", ".join(down[:-1]) + " and " + down[-1]
        verb = "isn't" if len(down) == 1 else "aren't"
        out.append(f"{names} {verb} answering this morning, so {'its' if len(down) == 1 else 'their'} morning jobs "
                   "may not run. I've flagged it for a fix.")
    return out


def daily_results(run: Run) -> dict[str, dict]:
    ""

    from . import daily
    out = {}
    for name in CHECKS + ("containers", "swap", "disk"):
        rows = {z: row[name] for z, row in run.table.items() if name in row}
        failed = {z: r for z, r in rows.items() if r["state"] == FAILED}
        title = f"Zips' computers: {name}"
        if failed:
            who = ", ".join(_name(z) if z != HOME_ROW else "the server" for z in failed)
            out[f"computer:{name}"] = daily._result(
                title, "failed", f"Failed for {who}.", config.LEAD.id, f"daily check zip computers {name}",
                f"Each Zip's computer passes the morning {name} check (zipcheck.py).",
                "; ".join(f"{z}: {r['reason']}" for z, r in failed.items())[:600])
        elif rows:
            state = "needs_person" if any(r["state"] == SIGNED_OUT for r in rows.values()) else "ok"
            out[f"computer:{name}"] = daily._result(title, state, "Passed." if state == "ok" else
                                                    "A sign-in a Zip needs is out; the person has one card at a time.")
    return out


def save(run: Run, now: float) -> None:
    from . import store
    store.set_setting(SETTING, {"at": now, "lines": lines(run), "fixed": run.fixed, "seconds": run.seconds,
                                "model_calls": run.model_calls, "table": run.table})
    if run.left_out:
        notes = _notes()
        notes.setdefault("left_out", {}).update(run.left_out)
        _save_notes(notes)


def brief_text(now: float | None = None) -> str:
    ""
    from . import store
    saved = store.get_setting(SETTING, {}) or {}
    now = time.time() if now is None else now
    if not saved.get("lines") or now - float(saved.get("at") or 0) > 20 * 3600:
        return ""
    return "[The Zips' computers, checked this morning:]\n" + "\n".join(f"- {x}" for x in saved["lines"])


async def morning(now: float) -> dict[str, dict]:
    ""
    run = await check(dry=False)
    for line in run.fixed:
        log.info("computer check fixed: %s", line)
    await asyncio.to_thread(raise_asks, run)
    await asyncio.to_thread(save, run, now)
    log.info("computer check: %.0f s, %d model calls, %d fixed, %d asks", run.seconds, run.model_calls,
             len(run.fixed), len(run.asks))
    return daily_results(run)




def _table(run: Run) -> str:
    cols = list(CHECKS)
    rows = [z for z in run.table if z != HOME_ROW]
    mark = {OK: "pass", FIXED: "fixed", FAILED: "FAIL", SIGNED_OUT: "SIGNED OUT", UNSURE: "unsure", SKIPPED: "skip"}
    width = max(len(_name(z)) for z in rows) + 2 if rows else 10
    out = ["Zip".ljust(width) + "".join(c.ljust(11) for c in cols)]
    for z in rows:
        out.append(_name(z).ljust(width) + "".join(mark.get(run.table[z].get(c, {}).get("state", ""), "-").ljust(11)
                                                 for c in cols))
    out.append("")
    for z in rows:
        for c in cols:
            r = run.table[z].get(c)
            if r and r["state"] not in (OK,):
                out.append(f"  {_name(z)} / {c}: {mark.get(r['state'])}: {r['reason']}")
    out.append("")
    for c, r in run.table.get(HOME_ROW, {}).items():
        out.append(f"  server / {c}: {mark.get(r['state'])}: {r['reason']}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if "--dry-run" not in argv:
        print("Usage: python -m taffy_hub.zipcheck --dry-run   (checks only: fixes, asks, files and saves nothing)")
        return 2
    run = asyncio.run(check(dry=True))
    print(_table(run))
    print(f"\nTook {run.seconds} s with {run.model_calls} model calls.")
    print("Would fix:" if run.would_fix else "Would fix: nothing")
    for x in run.would_fix:
        print(f"  - {x}")
    print("Would ask the person:" if run.asks else "Would ask the person: nothing")
    for a in run.asks:
        print(f"  - sign in to {a['site']} (card in {_name(a['asker'])}'s chat): {a['why']}")
    for x in lines(run):
        print(f"  brief line: {x}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main(sys.argv[1:]))
