#!/usr/bin/env python3
"""zip-desktop-lockdown.py [--home ~/.taffy] <bot>...: takes the ways to start other programs off a containerized Zip's
screen on alien (Zip sandbox plan, stage 3).

The screen runs on alien as Colin's user, so a terminal opened on it is outside the Zip's container. Hermes seeds each
screen's Xfce settings once (tools/bot_desktop/launcher.sh) with a Terminal, Files and Text Editor launcher, an
applications menu and Xfce's launcher shortcuts (Ctrl+Alt+T, Alt+F2, Super+R and the rest). This script, for each
named Zip:

  1. turns off every Xfce command shortcut (xfce4-keyboard-shortcuts.xml: the custom set is emptied and overrides the
     defaults); window shortcuts such as Alt+Tab stay;
  2. removes the applications menu and every dock launcher except Browser (xfce4-panel.xml and xfce4/panel/);
  3. hides the desktop's right-click menus (xfce4-desktop.xml);
  4. hides terminals, file managers, run dialogs and editors from the app lists (.local-share/applications).

The gate refuses the same key combinations and other apps than the browser (plugins/taffy-tools/sandbox.py); this is
the second layer. It is a speed bump, not a wall. Run it only while that Zip's screen is stopped (xfconfd rewrites
these files while it runs); a running screen is skipped. Each changed file is backed up first as <file>.bak-<time>.
Undo by restoring the backups, or by deleting the bot-desktop/xdg folder so Hermes seeds it again.
"""

import argparse
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HIDE = ["xfce4-terminal", "xfce4-terminal-settings", "xfce4-terminal-emulator", "exo-terminal-emulator", "xterm",
        "uxterm", "debian-xterm", "debian-uxterm", "org.gnome.Terminal", "thunar", "thunar-settings",
        "thunar-bulk-rename", "Thunar", "Thunar-bulk-rename", "Thunar-folder-handler", "xfce4-file-manager",
        "exo-file-manager", "exo-preferred-applications", "xfce4-appfinder", "xfce4-run", "xfce4-settings-manager",
        "xfce4-taskmanager", "mousepad", "org.xfce.mousepad", "org.xfce.mousepad-settings", "vim", "nvim", "htop",
        "python3", "python3.12", "python3.13", "python3.14", "lxterminal", "xdg-open"]
APP_DIRS = ["/usr/share/applications", "/usr/local/share/applications"]
KEEP = ("chrom", "browser")   # the bot's browser stays
STAMP = time.strftime("%Y%m%d-%H%M%S")


def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{STAMP}"))


def prop(parent: ET.Element, name: str, ptype: str = "empty", value: str | None = None) -> ET.Element:
    """The child <property name=...>, created when missing."""
    for p in parent.findall("property"):
        if p.get("name") == name:
            if value is not None:
                p.set("type", ptype)
                p.set("value", value)
            return p
    p = ET.SubElement(parent, "property", {"name": name, "type": ptype})
    if value is not None:
        p.set("value", value)
    return p


def load(path: Path, channel: str) -> ET.ElementTree:
    if path.exists():
        return ET.parse(path)
    return ET.ElementTree(ET.Element("channel", {"name": channel, "version": "1.0"}))


def save(tree: ET.ElementTree, path: Path) -> None:
    backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, "  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def shortcuts(xml: Path) -> None:
    tree = load(xml, "xfce4-keyboard-shortcuts")
    commands = prop(tree.getroot(), "commands")
    custom = prop(commands, "custom")
    for child in list(custom):
        custom.remove(child)
    prop(custom, "override", "bool", "true")
    save(tree, xml)


def panel(xml: Path, launchers: Path) -> list[str]:
    """Drops the applications menu and every launcher but Browser. Returns what it removed."""
    if not xml.exists():
        return []
    tree = ET.parse(xml)
    root = tree.getroot()
    plugins = prop(root, "plugins")
    kinds = {p.get("name"): p.get("value") for p in plugins.findall("property")}
    removed, drop = [], set()
    for name, kind in kinds.items():
        if not name or not name.startswith("plugin-"):
            continue
        n = name.split("-", 1)[1]
        if kind == "applicationsmenu":
            drop.add(n)
            removed.append("applications menu")
        elif kind == "launcher":
            label = ""
            for f in (launchers / f"launcher-{n}").glob("*.desktop"):
                for line in f.read_text(errors="replace").splitlines():
                    if line.startswith("Name="):
                        label = line[5:].strip()
                        break
            if label != "Browser":
                drop.add(n)
                removed.append(label or f"launcher {n}")
    panels = prop(root, "panels")
    for p in panels.findall("property"):
        ids = next((x for x in p.findall("property") if x.get("name") == "plugin-ids"), None)
        if ids is None:
            continue
        for v in list(ids.findall("value")):
            if v.get("value") in drop:
                ids.remove(v)
    for p in list(plugins.findall("property")):
        if p.get("name", "").split("-", 1)[-1] in drop:
            plugins.remove(p)
    save(tree, xml)
    for n in drop:
        d = launchers / f"launcher-{n}"
        if d.is_dir():
            shutil.move(str(d), str(d.with_name(f"{d.name}.removed-{STAMP}")))
    return removed


def desktop_menus(xml: Path) -> None:
    tree = load(xml, "xfce4-desktop")
    root = tree.getroot()
    for menu in ("desktop-menu", "windowlist-menu"):
        prop(prop(root, menu), "show", "bool", "false")
    save(tree, xml)


def hide_apps(apps: Path, app_dirs: list[str]) -> int:
    names = set(HIDE)
    for d in app_dirs:
        for f in Path(d).glob("*.desktop") if Path(d).is_dir() else []:
            if not any(k in f.stem.lower() for k in KEEP):
                names.add(f.stem)
    apps.mkdir(parents=True, exist_ok=True)
    for name in sorted(names):
        (apps / f"{name}.desktop").write_text(f"[Desktop Entry]\nType=Application\nName={name}\nHidden=true\n"
                                              "NoDisplay=true\n")
    return len(names)


PROC = Path("/proc")   # tests point this at a made-up /proc


def _proc_stat(pid: int) -> tuple[str, int] | None:
    """(state, start time in clock ticks after boot) from /proc/<pid>/stat, or None when there is no such process.
    The command name in the second field may hold spaces and brackets, so the fields are counted from the last ")"."""
    try:
        raw = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2:].split()
    try:
        return fields[0], int(fields[19])
    except (IndexError, ValueError):
        return None


def _boot_time() -> float | None:
    try:
        for line in (PROC / "stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _cmdline(pid: int) -> list[str]:
    try:
        return [a for a in (PROC / str(pid) / "cmdline").read_bytes().decode("utf-8", "replace").split("\0") if a]
    except OSError:
        return []


def _launcher_alive(desk: Path) -> bool:
    """launcher.pid holds "<pid> <start time>" (Hermes's tools/bot_desktop/runtime.py; an older Hermes wrote only the
    pid). The screen counts as running when that process is alive (not a zombie), started when the file says, and is
    Hermes's screen launcher, as Hermes's own check does. A pid the system has since given to another process is
    not the screen."""
    try:
        pid_s, _, born_s = (desk / "launcher.pid").read_text(encoding="utf-8-sig").strip().partition(" ")
        pid = int(pid_s)
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    st = _proc_stat(pid)
    if st is None:
        if PROC.is_dir():
            return False
        try:   # no /proc to look in: a live pid is taken as the screen, the safe side for this script
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False
    state, ticks = st
    if state in ("Z", "X", "x"):
        return False
    if born_s.strip():
        boot = _boot_time()
        try:
            born = float(born_s)
        except ValueError:
            return False
        if boot is not None and abs(boot + ticks / os.sysconf("SC_CLK_TCK") - born) > 1.0:
            return False
    args = _cmdline(pid)
    return any(Path(a).name == "launcher.sh" and "bot_desktop" in a for a in args[:3])


def _xvnc_for(desk: Path) -> int | None:
    """The pid of an X server bound to this Zip's screen socket (<desk>/rfb.sock), launcher or not: a launcher that
    died can leave its Xvnc and the Xfce session running."""
    socks = {str(desk / "rfb.sock"), os.path.realpath(desk / "rfb.sock")}
    try:
        pids = [int(p.name) for p in PROC.iterdir() if p.name.isdigit()]
    except OSError:
        return None
    for pid in pids:
        args = _cmdline(pid)
        if args and "Xvnc" in Path(args[0]).name and any(a in socks or a.split("=", 1)[-1] in socks for a in args):
            st = _proc_stat(pid)
            if st is not None and st[0] not in ("Z", "X", "x"):
                return pid
    return None


def running(desk: Path) -> bool:
    """Whether this Zip's screen is up: its launcher (checked as above) or an Xvnc on its socket."""
    return _launcher_alive(desk) or _xvnc_for(desk) is not None


def lock(home: Path, bot: str, app_dirs: list[str]) -> str:
    desk = (home if bot == "default" else home / "profiles" / bot) / "bot-desktop"
    xdg = desk / "xdg"
    if not xdg.is_dir():
        return f"{bot}: no screen settings yet (start its screen once, stop it, then run this again)"
    if running(desk):
        return f"{bot}: skipped, its screen is running; run this again after it stops"
    conf = xdg / "xfce4" / "xfconf" / "xfce-perchannel-xml"
    shortcuts(conf / "xfce4-keyboard-shortcuts.xml")
    removed = panel(conf / "xfce4-panel.xml", xdg / "xfce4" / "panel")
    desktop_menus(conf / "xfce4-desktop.xml")
    hidden = hide_apps(xdg / ".local-share" / "applications", app_dirs)
    return (f"{bot}: shortcuts off; removed from the panel: {', '.join(removed) or 'nothing'}; desktop menus off; "
            f"{hidden} apps hidden")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bots", nargs="+")
    ap.add_argument("--home", default=str(Path.home() / ".taffy"))
    ap.add_argument("--apps-dir", action="append", help="where the system's app entries are (default: "
                                                        + ", ".join(APP_DIRS) + ")")
    a = ap.parse_args(argv)
    for bot in a.bots:
        print(lock(Path(a.home), bot, a.apps_dir or APP_DIRS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
