""
















import asyncio
import hashlib
import os
import re
import secrets
import shlex
import shutil
import stat
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import config, home, safe_files, who


LEFT_OUT = ("node_modules", ".build", "build", "DerivedData", "dist", ".next", ".venv", "venv", "__pycache__", "Pods",
            ".gradle", "target", ".DS_Store")
MAX_COPY_BYTES = 500 * 1024 * 1024
MAX_COPY_KB = 2 * 1024 * 1024
UNPACKED_LIMIT = MAX_COPY_KB * 1024 * 3 // 2
UPLOAD_SEC = 10 * 60
CURL_MAX_SEC = 570
FOLDER = "from-mac"
INDEX = "INDEX.md"
SPOOL = "mac-copies"


class TooBig(Exception):
    ""


@dataclass
class Upload:
    ""
    bot_id: str
    folder: Path
    name: str
    archive: Path
    expires: float
    cap: int = MAX_COPY_BYTES
    size: int | None = None
    closed: bool = False


_uploads: dict[str, Upload] = {}


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def mint(bot_id: str, folder: Path, name: str) -> tuple[str, Upload]:
    ""
    spool = config.HUB_DIR / SPOOL
    spool.mkdir(parents=True, exist_ok=True)
    for old in spool.iterdir():
        try:
            if time.time() - old.stat().st_mtime > 2 * UPLOAD_SEC:
                old.unlink()
        except OSError:
            pass
    secret = secrets.token_urlsafe(32)
    up = Upload(bot_id, folder, name, spool / f"{secrets.token_hex(8)}.tar.gz", time.time() + UPLOAD_SEC)
    _uploads[_digest(secret)] = up
    return secret, up


def close(secret: str, up: Upload) -> None:
    ""
    _uploads.pop(_digest(secret), None)
    up.closed = True
    up.archive.unlink(missing_ok=True)


async def receive(secret: str, chunks) -> int:
    ""

    up = _uploads.pop(_digest(secret), None)
    if up is None or up.closed or time.time() > up.expires:
        raise LookupError("That upload address is not valid.")
    size, whole = 0, False
    try:
        fd = os.open(up.archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            async for chunk in chunks:
                size += len(chunk)
                if size > up.cap:
                    raise TooBig(f"The copy is over {up.cap // (1024 * 1024)} MB compressed.")
                f.write(chunk)
        whole = True
    finally:
        if not whole or up.closed:
            up.archive.unlink(missing_ok=True)
    if not up.closed:
        up.size = size
    return size




def path_parts(path: str) -> tuple[str, str]:
    ""
    from .mac import MacError
    path = path.strip().rstrip("/")
    p = PurePosixPath(path)
    if ".." in p.parts:
        raise MacError("Give the path without '..'.")
    if p.name in ("", "~", ".") or len(p.parts) < 2:
        raise MacError("Name one folder or file to copy, not the whole home folder or disk.")
    if any(ord(c) < 32 for c in p.name) or p.name == INDEX:
        raise MacError("That name can't be copied as it is. Copy the folder it is in.")
    return str(p.parent), p.name


def _left_out(name: str) -> list[str]:
    return [n for n in LEFT_OUT if n != name]


def facts_command(path: str) -> str:
    ""

    from .mac import _quoted_path
    _, name = path_parts(path)
    masks = " ".join(f"-I {shlex.quote(n)}" for n in _left_out(name))
    tracked = "|".join(re.escape(n) for n in _left_out(name) if n != ".DS_Store")
    return (f"P={_quoted_path(path.strip().rstrip('/'))}; "
            "if [ ! -e \"$P\" ]; then echo '@@X|'; exit 0; fi; "
            "if [ -d \"$P\" ]; then echo '@@T|folder'; else echo '@@T|file'; fi; "
            f"echo \"@@K|$(/usr/bin/du -sk {masks} -- \"$P\" | /usr/bin/cut -f1)\"; "

            "if [ -d \"$P/.git\" ] && /usr/bin/xcode-select -p >/dev/null 2>&1; then "
            "echo \"@@R|$(/usr/bin/git -C \"$P\" config --get remote.origin.url)\"; "
            "echo \"@@B|$(/usr/bin/git -C \"$P\" rev-parse --abbrev-ref HEAD)\"; "
            "echo \"@@H|$(/usr/bin/git -C \"$P\" rev-parse HEAD)\"; "
            f"echo \"@@L|$(/usr/bin/git -C \"$P\" ls-files | /usr/bin/grep -cE '(^|/)({tracked})/')\"; "
            "fi")


def upload_command(path: str, url: str) -> str:
    ""


    from .mac import _quoted_path
    parent, name = path_parts(path)
    leave = " ".join(f"--exclude {shlex.quote(n)}" for n in _left_out(name))
    return (f"COPYFILE_DISABLE=1 /usr/bin/tar -czf - {leave} -C {_quoted_path(parent)} -- {shlex.quote(name)} | "
            f"/usr/bin/curl -q -sS --fail --connect-timeout 20 --max-time {CURL_MAX_SEC} "
            f"-H 'Content-Type: application/gzip' -T - -o /dev/null {shlex.quote(url)}; "
            "echo \"@@C|${pipestatus[1]}|${pipestatus[2]}\"")


def markers(stdout: str) -> dict[str, str]:
    ""
    out: dict[str, str] = {}
    for line in (stdout or "").splitlines():
        tag, sep, rest = line.strip().partition("|")
        if sep and tag.startswith("@@") and tag not in out:
            out[tag] = rest.strip()
    return out


def clean_remote(url: str) -> str:
    ""
    return re.sub(r"^([a-z+]+://)[^/@]*@", r"\1", url.strip())


def https_github(url: str) -> str | None:
    ""
    m = re.match(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
                 url)
    return f"https://github.com/{m.group(1)}.git" if m else None




def ready_folder(folder: Path) -> None:
    ""
    folder.parent.mkdir(parents=True, exist_ok=True)
    os.close(_open_from_mac(folder))


def _staging(folder: Path) -> Path:
    ""


    root = folder.parent.parent / ".mac-incoming"
    root.mkdir(mode=0o700, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="copy-", dir=root))


def _open_from_mac(folder: Path) -> int:
    ""

    from .mac import MacError
    parent = os.open(folder.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            os.mkdir(folder.name, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
        return os.open(folder.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError:
        raise MacError(f"{folder} isn't a plain folder. Move it aside, then copy again.") from None
    finally:
        os.close(parent)


def install(up: Upload) -> dict:
    ""



    from .mac import MacError
    seen = {"files": 0, "bytes": 0, "skipped": 0, "secret": {}}

    def keep(member: tarfile.TarInfo, where: str):
        try:
            member = tarfile.data_filter(member, where)
        except tarfile.FilterError:
            seen["skipped"] += 1
            return None
        secret = safe_files.secret_part(member.name, member.isdir())
        if secret:
            seen["secret"][secret] = seen["secret"].get(secret, 0) + (0 if member.isdir() else 1)
            return None
        if member.isfile():
            seen["files"] += 1
            seen["bytes"] += member.size
            if seen["bytes"] > UNPACKED_LIMIT:
                raise MacError("The copy unpacks to far more than the folder measured on the Mac, so it was not kept.")
        return member

    work = _staging(up.folder)
    try:
        try:
            with tarfile.open(up.archive, "r:gz") as tf:
                tf.extractall(work, filter=keep)
        except (tarfile.TarError, EOFError, OSError) as e:
            raise MacError(f"The copy arrived damaged ({type(e).__name__}). Try again.") from None
        new = work / up.name
        if not os.path.lexists(new):
            raise MacError("The copy arrived empty. Check the path on the Mac and try again.")
        scrub_git_configs(work)
        dest = _open_from_mac(up.folder)
        staged = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                os.rename(up.name, f".old-{secrets.token_hex(4)}", src_dir_fd=dest, dst_dir_fd=staged)
            except FileNotFoundError:
                pass
            os.rename(up.name, up.name, src_dir_fd=staged, dst_dir_fd=dest)
        finally:
            os.close(staged)
            os.close(dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return seen


_CONFIG_SECRET = re.compile(r"^\s*(?:extraheader|password|token)\s*=", re.I)
_CREDENTIALS = re.compile(r"(https?://)[^/@\s\"]*@", re.I)


def scrub_git_configs(root: Path) -> None:
    ""


    for dirpath, _, files in os.walk(root):
        parts = Path(dirpath).relative_to(root).parts
        if ".git" not in parts or "config" not in files:
            continue
        path = Path(dirpath) / "config"
        if path.is_symlink():
            path.unlink()
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept = [_CREDENTIALS.sub(r"\1", ln) for ln in lines if not _CONFIG_SECRET.match(ln)]
        if kept != lines:
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def note_in_index(folder: Path, name: str, line: str) -> None:
    ""

    folder_fd = _open_from_mac(folder)
    try:
        text = ""
        try:
            fd = os.open(INDEX, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
            with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    text = f.read(1_000_000)
        except OSError:
            pass
        lines = [ln for ln in text.splitlines() if not ln.startswith(f"- {name}: ")]
        if not any(ln.startswith("# ") for ln in lines):
            lines = [f"# Copied from {who.mac()}", "",
                     "One line per copy. A new copy of the same name replaces the old one whole.", ""] + lines
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(line)
        tmp = f".{INDEX}.{secrets.token_hex(4)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.rename(tmp, INDEX, src_dir_fd=folder_fd, dst_dir_fd=folder_fd)
    finally:
        os.close(folder_fd)


def index_line(name: str, mac_path: str, facts: dict[str, str]) -> str:
    when = datetime.now(home.zone()).strftime("%b %-d, %Y at %-I:%M %p %Z")
    line = f"- {name}: from {mac_path} on {who.mac()}, copied {when}."
    remote, branch, head = clean_remote(facts.get("@@R", "")), facts.get("@@B", ""), facts.get("@@H", "")
    if head:
        line += f" Git: {remote or 'no remote'}, branch {branch or 'unknown'}, commit {head[:12]}."
    return line




async def copy(bot_id: str, folder: Path, path: str, hub_url: str, run) -> str:
    ""

    from .mac import MacError
    _, name = path_parts(path)
    if safe_files.secret_part(name) or safe_files.secret_part(name, is_dir=True):
        raise MacError(f"{path} looks like a sign-in, key or private folder, so it stays on {who.mac()}. Copy the "
                       "folder or files around it instead.")
    if not hub_url:
        raise MacError(f"{who.mac()} isn't connected in a way that can send files here. Open Zero on it, then try "
                       "again.")
    looked = await run(facts_command(path), 120)
    facts = markers(str(looked.get("stdout") or ""))
    if "@@X" in facts:
        raise MacError(f"Nothing is at {path} on {who.mac()}. Find it with mac_run and mdfind -name first.")
    kb = facts.get("@@K", "")
    if not kb.isdigit():
        raise MacError(f"I couldn't measure {path} on {who.mac()}. Copy a smaller folder inside it.")
    if int(kb) > MAX_COPY_KB:
        raise MacError(f"{path} is about {int(kb) / 1024 / 1024:.1f} GB, even without folders a build makes again, "
                       f"and a copy can be at most {MAX_COPY_KB // 1024 // 1024} GB. Copy a smaller folder inside it.")
    await asyncio.to_thread(ready_folder, folder)
    free = shutil.disk_usage(folder).free
    if free < int(kb) * 1024 * 2 + 1024 ** 3:
        raise MacError(f"There isn't room for {path} here right now ({free // 1024 ** 2} MB free). Copy a smaller "
                       f"folder, or tell {who.first()} the disk is nearly full.")
    secret, up = mint(bot_id, folder, name)
    try:
        sent = await run(upload_command(path, f"{hub_url}/api/mac/upload/{secret}"), 600)
        ended = markers(str(sent.get("stdout") or "")).get("@@C", "").split("|")
        if up.size is None:
            why = " ".join(str(sent.get("stderr") or "").split())[:300]
            if sent.get("timed_out"):
                why = "it took longer than the 10 minutes a copy can take"
            raise MacError(f"The copy didn't arrive: {why or 'no reason given'}. Copy a smaller folder, or try again.")
        seen = await asyncio.to_thread(install, up)
    finally:
        close(secret, up)
    await asyncio.to_thread(note_in_index, folder, name, index_line(name, path, facts))
    return _result(path, folder / name, facts, seen, ended, sent)


def _size(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{max(n / 1024 ** 2, 0.1):.1f} MB"


def _result(path: str, target: Path, facts: dict[str, str], seen: dict, ended: list[str], sent: dict) -> str:
    kind = "folder" if facts.get("@@T") == "folder" else "file"
    files = f"{seen['files']:,} files, " if kind == "folder" else ""
    out = [f"Copied {path} from {who.mac()}. It is at {target} ({files}{_size(seen['bytes'])}), and "
           f"{target.parent / INDEX} lists every copy. Look there first next time."]
    if kind == "folder":
        out.append("Folders a build or install makes again (node_modules, build, dist, .venv and the like) were left "
                   "out.")
    if facts.get("@@H"):
        remote = clean_remote(facts.get("@@R", ""))
        out.append(f"It is a git repo on branch {facts.get('@@B') or 'unknown'} with its history and any uncommitted "
                   "work" + (f", from {remote}." if remote else ", with no remote."))
        github = https_github(remote)
        if github and github == remote:
            out.append("git pull works there.")
        elif github:
            out.append(f"To pull, first run: git remote set-url origin {github}")
        tracked = facts.get("@@L", "0")
        if tracked.isdigit() and int(tracked):
            out.append(f"{tracked} files git tracks are in left-out folders, so git status shows them as deleted. "
                       "Bring them back with git checkout -- <folder> before you commit.")
    if seen["skipped"]:
        out.append(f"{seen['skipped']} links or special files pointing outside the {kind} were left out.")
    if seen.get("secret"):
        top = PurePosixPath(path.strip().rstrip("/")).name
        parts = sorted(str(PurePosixPath(p).relative_to(top)) + ("/" if p.endswith("/") else "") for p in seen["secret"])
        shown = ", ".join(parts[:10]) + (f" and {len(parts) - 10} more" if len(parts) > 10 else "")
        out.append(f"Left out what looks like sign-ins or keys, which stay on {who.mac()}: {shown}. If the work needs "
                   f"one, ask {who.first()} for it.")
    if ended and ended[0] not in ("", "0"):
        why = " ".join(str(sent.get("stderr") or "").split())[:200]
        out.append(f"Some files couldn't be read on the Mac, so the copy may be missing them: {why}")
        if sent.get("note"):
            out.append(str(sent["note"]))
    return " ".join(out)
