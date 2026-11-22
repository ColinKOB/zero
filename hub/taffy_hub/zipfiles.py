""











import os
import re
import stat
import time
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from . import config, home, uploads

MAX_FILES = 40

MAX_SCANNED = 20_000
SETTLE_SEC = 5

MAX_DEPTH = 12
_FOLDER = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
SHOWN_TYPES = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".csv", ".pptx", ".txt", ".md", ".rtf", ".pages", ".numbers",
               ".key", ".html", ".png", ".jpg", ".jpeg", ".heic", ".heif", ".gif", ".webp", ".mp4", ".mov", ".m4a", ".mp3",
               ".zip"}
NOISE_FOLDERS = {"node_modules", ".venv", "venv", "__pycache__", ".git", "caches", "cache", "site-packages"}
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} ")


def made_root(bot_id: str) -> Path:
    ""

    owner_lead = bot_id == "default" and home.is_owner_home()
    return Path(config.WORK_DIR) / ("taffy" if owner_lead else bot_id)


ABSORBED = "@"


def _absorbed(bot_id: str) -> tuple[str, ...]:
    return getattr(config.BOTS_BY_ID.get(bot_id), "merged_from", ())


def _inner(rel: PurePosixPath) -> PurePosixPath:
    ""
    return PurePosixPath(*rel.parts[1:]) if rel.parts and rel.parts[0].startswith(ABSORBED) else rel


def _root(bot_id: str, rel: PurePosixPath) -> Path | None:
    ""


    if rel.parts and rel.parts[0].startswith(ABSORBED):
        member = rel.parts[0][len(ABSORBED):]
        return Path(config.WORK_DIR) / member if member in _absorbed(bot_id) else None
    return uploads.folder(bot_id).parent if _sent(rel) else made_root(bot_id)


def _utf8(name: str) -> bool:
    ""
    try:
        name.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def _hidden_or_noise(part: str) -> bool:

    return part.startswith(".") or part.lower() in NOISE_FOLDERS or part.lower().startswith(("venv-", "venv_"))


def _shown(rel: PurePosixPath) -> bool:
    ""
    return (0 < len(rel.parts) <= MAX_DEPTH and not any(_hidden_or_noise(p) for p in rel.parts)
            and rel.suffix.lower() in SHOWN_TYPES)


def _own(st: os.stat_result) -> bool:
    ""

    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


def _sent(rel: PurePosixPath) -> bool:
    return _inner(rel).parts[:1] == ("Uploads",)


def display_name(file_id: str) -> str:
    ""
    rel = PurePosixPath(file_id)
    return (_STAMP.sub("", rel.name) if _sent(rel) else rel.name) or rel.name


def _entry(rel: PurePosixPath, st: os.stat_result, checkout: bool = False) -> dict:
    return {"id": rel.as_posix(), "name": display_name(rel.as_posix()), "kind": uploads.kind_of(rel.name),
            "size": st.st_size, "modified": st.st_mtime, "source": "sent" if _sent(rel) else "made",
            "checkout": checkout}


def _has_git(fd: int) -> bool:
    try:
        os.stat(".git", dir_fd=fd, follow_symlinks=False)
        return True
    except OSError:
        return False


def listing(bot_id: str, settling: list | None = None, *, limit: int | None = MAX_FILES) -> list[dict]:
    ""




    found: list[dict] = []
    budget = MAX_SCANNED

    def scan(fd: int, here: PurePosixPath, checkout: bool = False) -> None:
        ""
        nonlocal budget
        checkout = checkout or _has_git(fd)
        try:
            with os.scandir(fd) as entries:
                for e in entries:
                    budget -= 1
                    if budget < 0:
                        return
                    if _hidden_or_noise(e.name) or e.is_symlink() or not _utf8(e.name):
                        continue
                    rel = here / e.name
                    if not _inner(here).parts and e.name == "Uploads":
                        continue
                    if not here.parts and e.name.startswith(ABSORBED):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if len(rel.parts) < MAX_DEPTH:
                            try:
                                inner = os.open(e.name, _FOLDER, dir_fd=fd)
                            except OSError:
                                continue
                            scan(inner, rel, checkout)
                    elif e.is_file(follow_symlinks=False) and _shown(rel):
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue


                        if not _own(st):
                            continue
                        age = time.time() - st.st_mtime
                        if age >= SETTLE_SEC or age < 0:
                            found.append(_entry(rel, st, checkout))
                        elif settling is not None:
                            settling.append(rel.as_posix())
        except OSError:
            pass
        finally:
            os.close(fd)



    starts = [(made_root(bot_id), PurePosixPath()), (uploads.folder(bot_id), PurePosixPath("Uploads"))]
    for member in _absorbed(bot_id):
        starts += [(Path(config.WORK_DIR) / member, PurePosixPath(ABSORBED + member)),
                   (Path(config.WORK_DIR) / member / "Uploads", PurePosixPath(ABSORBED + member, "Uploads"))]
    for folder, here in starts:
        budget = MAX_SCANNED
        try:
            scan(os.open(folder, _FOLDER), here)
        except OSError:
            continue
    found.sort(key=lambda f: f["modified"], reverse=True)
    return found if limit is None else found[:limit]


def open_file(bot_id: str, file_id: str) -> BinaryIO | None:
    ""





    rel = PurePosixPath(str(file_id or ""))
    if rel.is_absolute() or ".." in rel.parts or "\x00" in str(rel) or not _shown(rel):
        return None
    root = _root(bot_id, rel)
    rel = _inner(rel)
    if root is None or not rel.parts:
        return None
    try:
        folder = os.open(root, _FOLDER)
    except OSError:
        return None
    try:
        for part in rel.parts[:-1]:
            inner = os.open(part, _FOLDER, dir_fd=folder)
            os.close(folder)
            folder = inner
        fd = os.open(rel.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except OSError:
        return None
    finally:
        os.close(folder)
    if not _own(os.fstat(fd)):
        os.close(fd)
        return None
    return os.fdopen(fd, "rb")
