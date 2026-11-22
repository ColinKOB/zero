""





import mimetypes
import os
import time
from pathlib import Path

from . import config, uploads

INBOX = "From you"
LIST_MAX = 2000


class TeamFileError(Exception):
    pass


def root(tid: str) -> Path:
    return Path(config.WORK_DIR) / "teams" / tid


def save(tid: str, name: str, data: bytes) -> dict:
    ""

    if not data:
        raise TeamFileError("That file is empty.")
    if len(data) > uploads.MAX_BYTES:
        raise TeamFileError("That file is over 25 MB. Send a smaller one.")
    d = root(tid) / INBOX
    d.mkdir(parents=True, exist_ok=True)


    if d.is_symlink() or d.resolve() != root(tid).resolve() / INBOX:
        raise TeamFileError("The team's folder isn't set up right for uploads.")
    clean = uploads.safe_name(name)
    stem, suffix = Path(clean).stem, Path(clean).suffix
    stamp = time.strftime("%Y-%m-%d %H%M")
    target, n = d / f"{stamp} {clean}", 2
    while True:
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            break
        except FileExistsError:
            target = d / f"{stamp} {stem} {n}{suffix}"
            n += 1
    return {"id": f"{INBOX}/{target.name}", "name": clean, "kind": uploads.kind_of(clean), "size": len(data)}


def resolve(tid: str, rel: str) -> Path:
    ""
    base = root(tid).resolve()
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel or any(part in ("", "..") or part.startswith(".") for part in rel.split("/")):
        raise TeamFileError("That file isn't in the team's folder.")
    p = (base / rel).resolve()
    if base not in p.parents or not p.is_file():
        raise TeamFileError("That file isn't in the team's folder.")
    return p


def lines(tid: str, ids: list[str]) -> str:
    ""
    if len(ids) > uploads.MAX_PER_MESSAGE:
        raise TeamFileError(f"Send at most {uploads.MAX_PER_MESSAGE} files at a time.")
    out = []
    for i in ids:
        p = resolve(tid, i)
        out.append(f"[{'Image' if uploads.kind_of(p.name) == 'image' else 'File'} attached at: {p}]")
    return "\n".join(out)


def open_checked(tid: str, rel: str):
    ""

    p = resolve(tid, rel)
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise TeamFileError("That file isn't in the team's folder.") from None
    f = os.fdopen(fd, "rb")
    try:
        again = resolve(tid, rel)
        a, b = os.fstat(f.fileno()), os.stat(again)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise TeamFileError("That file isn't in the team's folder.")
    except BaseException:
        f.close()
        raise
    return f, p.name, a.st_size


def listing(tid: str) -> list[dict]:
    ""

    base = root(tid)
    if not base.is_dir():
        return []
    out = []
    for p in base.rglob("*"):
        rel = p.relative_to(base)
        if any(part.startswith(".") for part in rel.parts) or not p.is_file() or p.is_symlink():
            continue
        st = p.stat()
        out.append({"path": rel.as_posix(), "name": p.name, "folder": rel.parent.as_posix() if rel.parent != Path(".") else "",
                    "size": st.st_size, "modified": st.st_mtime, "kind": uploads.kind_of(p.name),
                    "type": mimetypes.guess_type(p.name)[0] or "application/octet-stream"})
    out.sort(key=lambda f: -f["modified"])
    return out[:LIST_MAX]


def newest_names(tid: str, n: int = 8) -> str:
    ""

    files = listing(tid)[:n]
    return ", ".join(f"{f['path']} ({time.strftime('%-I:%M %p', time.localtime(f['modified']))})" for f in files) or "none yet"
