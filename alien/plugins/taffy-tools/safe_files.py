"""Reading a file a Zip names, without following it out of the folders that Zip may share (Zip sandbox fixes, Oct 8).

A Zip can make links in its work folder that point anywhere on alien, and a containerized Zip does this from inside
its container, where the rest of alien is out of reach. Everything on alien that reads a file a Zip names and hands it
on (an email attachment, a picture for Colin's app, a board attachment, a file the browser uploads, a file shown on a
card) asks this module first:

  resolve(path, roots)     the real path, after every link and "..", if it is a regular file inside one of the roots,
                           is not a protected path, and has no other hard links; else Refused with a plain reason
  open_file(path, roots)   the same, opened without following a link, and checked again on the open file, so a link
                           swapped in after the check is caught
  card_text(path, limit)   for card text only: any regular, single-link file that is not protected, or None

Only the standard library is used, so the hub loads this file by path (hub/taffy_hub/safe_files.py) and the hub and
the gate share one copy of the rules, SECRET_PATHS included.
"""

import os
import re
import stat
import sys

# Paths no Zip tool may name, read or hand on: the vaults, key files, credential files, the consent and hub databases,
# the Zips' own settings and plugins (gate.py re-exports this as gate.SECRET_PATHS).
SECRET_PATHS = re.compile(
    r"(vault\.key|vault\.json|/vault\b|vault_store|\.credentials\.json|/\.config/himalaya|\.pass\b|"
    r"colin-notify|telegram\.env|taffy-push|AuthKey_|tally/server/\.env|(^|[/\s'\"])\.env(?![\w.-])|consent\.db|hub\.db|hub-state|"
    # Saved calls and the command that prints them. Colin and the lead session read them over ssh, which the gate
    # never sees; a Zip must not read what Colin said on calls with the other Zips (Oct 8 QA round 10, L4).
    r"zero[-_]?calls|hub-state/calls|"
    r"browser-profile|DevToolsActivePort|Xauthority|/\.ssh\b|id_rsa|id_ed25519|/bot-desktop/lease|"
    r"hub-src|taffy_hub|plugins/taffy-tools|systemd/user|"
    # The Zips' own Hermes settings and plugins, and Claude's and Codex's sign-ins and settings: a second layer
    # behind the command check in tiers.py (Oct 8 QA round 3, H2).
    r"\.taffy/(?:profiles/[^/\s'\"]+/)?(?:config\.yaml|plugins\b)|\.hermes/config\.yaml|\.claude\.json\b|"
    r"(?:~|\$HOME|\$\{HOME\}|/home/[^/\s'\"]+|/Users/[^/\s'\"]+)/\.(?:claude|codex)/|"
    # Colin's switch for paid judge calls (fast_steps.paid_calls_on): only he turns it on (Oct 8 QA round 2, M7).
    r"taffy/owner\b|paid-decisions|"
    # GitHub: bots use gh and git, which read the token themselves; nothing may print or copy it.
    r"\.config/gh\b|hosts\.yml|gh\s+auth\s+(token|refresh|login|logout|setup-git)|show-token|"
    r"\bgit\s+credential\b|git-credentials|\bGH_TOKEN\b|\bGITHUB_TOKEN\b|GH_ENTERPRISE_TOKEN)",
    re.I)


def _home_settings():
    """The home's settings reader (home.py), loaded by path when the hub loads this file on its own."""
    if __package__:
        from . import home
        return home
    import importlib.util
    mod = sys.modules.get("taffy_tools.home") or sys.modules.get("taffy_plugin_home")
    if mod is None:
        spec = importlib.util.spec_from_file_location("taffy_plugin_home",
                                                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "home.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["taffy_plugin_home"] = mod
        spec.loader.exec_module(mod)
    return mod


def _home_secret_paths():
    """The home's own protected places when its settings put them somewhere the patterns above do not name: the
    owner switches, the push key, Tally's key file and the hub's state. Appended to SECRET_PATHS, never replacing it."""
    try:
        home = _home_settings()
        found = [home.get("paths.owner"), home.get("push.key_dir"), (home.tally() or {}).get("env_file"),
                 home.get("paths.hub_state")]
    except Exception:
        return []
    out = []
    for value in found:
        text = str(value or "").strip().rstrip("/")
        if not text or SECRET_PATHS.search(text):
            continue   # already protected
        out.append(re.escape(text.removeprefix("~/")) + r"\b")
    return out


_EXTRA = _home_secret_paths()
if _EXTRA:
    SECRET_PATHS = re.compile(SECRET_PATHS.pattern[:-1] + "|" + "|".join(_EXTRA) + ")", re.I)


class Refused(Exception):
    """A file a Zip named that may not be read for it. The message is a plain sentence for the Zip."""


def zip_roots(real_home, bot: str) -> tuple[str, ...]:
    """The folders a Zip may hand files on from: its own work folder, the shared folder and the board's attachment
    store (files Colin put on a task). Zero, the lead on alien's own terminal, reviews the whole team's work folders."""
    settings = _home_settings()
    work = str(settings.path("work", real_home)).rstrip("/")
    own = work if bot in ("default", "", None) else f"{work}/{bot}"
    return (own, str(settings.path("shared", real_home)).rstrip("/"),
            str(settings.path("hermes_home", real_home) / "kanban" / "attachments"))


def _expand(path: str, home: str | None) -> str:
    if not isinstance(path, str) or not path or "\0" in path:
        raise Refused("that is not a usable file path")
    if path.startswith("~/") and home:
        path = home.rstrip("/") + path[1:]
    if not path.startswith("/"):
        raise Refused(f"{path} is not a full path (start it with / or ~/)")
    return path


def _root_of(real: str, roots) -> tuple[str, str] | None:
    """(the root's real path, the rest of `real` below it) for the first root `real` is inside."""
    for r in roots:
        rr = os.path.realpath(str(r)).rstrip("/")
        if rr and real.startswith(rr + "/"):
            return rr, real[len(rr):]
    return None


def _check_stat(st: os.stat_result, shown: str) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise Refused(f"{shown} is not a regular file")
    if st.st_nlink > 1:
        # A hard link to a file elsewhere passes every path check; nothing the Zips make needs one.
        raise Refused(f"{shown} has other hard links; copy the file instead of linking it")


def resolve(path: str, roots, home: str | None = None, protected=SECRET_PATHS) -> str:
    """The real path of the file `path` names, if it may be handed on; else raises Refused. Links and ".." are
    followed first, so a link in a work folder names the file it points to on alien."""
    p = _expand(path, home)
    if protected is not None and protected.search(p):
        raise Refused(f"{path} is a protected path")
    real = os.path.realpath(p)
    found = _root_of(real, roots)
    if found is None:
        raise Refused(f"{path} is not in the folders you may share from")
    if protected is not None and protected.search(found[1]):
        raise Refused(f"{path} is a protected path")
    try:
        st = os.lstat(real)
    except OSError:
        raise Refused(f"{path} does not exist") from None
    _check_stat(st, path)
    return real


def _fd_path(fd: int) -> str | None:
    """The path the kernel has for an open file: /proc on Linux, F_GETPATH on macOS, else None."""
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        pass
    if sys.platform == "darwin":
        try:
            import fcntl
            raw = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), b"\0" * 1024)
            return raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape") or None
        except OSError:
            return None
    return None


def open_file(path: str, roots, home: str | None = None, protected=SECRET_PATHS):
    """The file `path` names, opened for reading (binary), under the same rules as resolve. The open never follows a
    link, and the open file is checked again: a link or folder swapped in after the check is refused."""
    real = resolve(path, roots, home, protected)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(real, flags)
    except OSError:
        raise Refused(f"{path} could not be opened") from None
    try:
        st = os.fstat(fd)
        _check_stat(st, path)
        where = _fd_path(fd)
        if where is None:
            # No kernel path for the open file: it must still be the file the checked path names.
            again = os.path.realpath(real)
            st2 = os.stat(again)
            if again != real or (st2.st_dev, st2.st_ino) != (st.st_dev, st.st_ino):
                raise Refused(f"{path} changed while it was being opened")
            where = again
        found = _root_of(os.path.realpath(where), roots)
        if found is None or protected is not None and protected.search(found[1]):
            raise Refused(f"{path} changed while it was being opened")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_file(path: str, roots, home: str | None = None, limit: int = 64 * 1024 * 1024,
              protected=SECRET_PATHS) -> bytes:
    with open_file(path, roots, home, protected) as f:
        return f.read(limit)


def card_text(path: str, limit: int, protected=SECRET_PATHS) -> bytes | None:
    """The first `limit` bytes of a file a command names, for a card's text, or None when it may not be shown: a
    protected file (after links), anything but a regular file, or a file with other hard links. No folder limit:
    the commands themselves may name any file."""
    try:
        if not path.startswith("/") or "\0" in path:
            return None
        real = os.path.realpath(path)
        if protected is not None and (protected.search(path) or protected.search(real)):
            return None
        fd = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except (OSError, ValueError):
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            return None
        where = _fd_path(fd)
        if where is not None and protected is not None and protected.search(os.path.realpath(where)):
            return None
        with os.fdopen(fd, "rb") as f:
            fd = -1
            return f.read(limit)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
