""
















import os
import re
import stat
import sys



_ZERO_MAY_READ = (r"\.credentials\.json|plugins/taffy-tools|"
                  r"\.taffy/(?:profiles/[^/\s'\"]+/)?(?:config\.yaml|plugins\b)|\.hermes/config\.yaml|\.claude\.json\b|"
                  r"(?:~|\$HOME|\$\{HOME\}|/home/[^/\s'\"]+|/Users/[^/\s'\"]+)/\.(?:claude|codex)/")



_NO_ZIP_READS = (
    r"vault\.key|vault\.json|/vault\b|vault_store|/\.config/himalaya|\.pass\b|"
    r"colin-notify|telegram\.env|taffy-push|AuthKey_|tally/server/\.env|(^|[/\s'\"])\.env(?![\w.-])|consent\.db|hub\.db|hub-state|"


    r"zero[-_]?calls|hub-state/calls|"


    r"/\.config/zero(?![\w.-])|zero-zip-mail|"
    r"browser-profile|DevToolsActivePort|Xauthority|/\.ssh\b|id_rsa|id_ed25519|/bot-desktop/lease|"
    r"hub-src|taffy_hub|systemd/user|"

    r"taffy/owner\b|paid-decisions|"

    r"\.config/gh\b|hosts\.yml|gh\s+auth\s+(token|refresh|login|logout|setup-git)|show-token|"
    r"\bgit\s+credential\b|git-credentials|\bGH_TOKEN\b|\bGITHUB_TOKEN\b|GH_ENTERPRISE_TOKEN|"

    r"\bMODEL_API_KEY\b")
SECRET_PATHS = re.compile(f"({_NO_ZIP_READS}|{_ZERO_MAY_READ})", re.I)


def _home_settings():
    ""
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
    ""

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
            continue
        out.append(re.escape(text.removeprefix("~/")) + r"\b")
    return out


_EXTRA = _home_secret_paths()
if _EXTRA:
    SECRET_PATHS = re.compile(SECRET_PATHS.pattern[:-1] + "|" + "|".join(_EXTRA) + ")", re.I)
SECRET_PATHS_FOR_ZERO_READS = re.compile("|".join([_NO_ZIP_READS, *_EXTRA]), re.I)







SECRET_DIRS = frozenset({"private", ".ssh", ".gnupg", ".aws", "keychains"})
SECRET_NAMES = re.compile(
    r"^(?:\.env(?:\.(?!example$|sample$|template$|dist$)[\w.-]+)?|\.netrc|\.pgpass|id_(?:rsa|dsa|ecdsa|ed25519)|"
    r"client_secret.*|.*\.(?:keychain|keychain-db|p12|pfx)|"
    r"(?:.*[-_.])?(?:credentials?|token|cookies?)"
    r"(?:\.(?:json|txt|pickle|pkl|ya?ml|toml|ini|dat|db|sqlite|cfg|conf|plist))?)$", re.I)


def secret_part(rel: str, is_dir: bool = False) -> str | None:
    ""


    parts = [p for p in str(rel).split("/") if p and p != "."]
    for i, part in enumerate(parts):
        if part == ".git":
            return None
        last = i == len(parts) - 1
        if (not last or is_dir) and part.lower() in SECRET_DIRS:
            return "/".join(parts[:i + 1]) + "/"
        if last and not is_dir and SECRET_NAMES.match(part):
            return "/".join(parts)
    return None






_WILD = re.compile(r"[*?\[]")
_BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
_HEREDOC_BODY = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
_CD = re.compile(r"(?:^|[;&|(\s])(?:cd|pushd)\s+(['\"]?)([^\s'\";&|)]+)\1")
_LISTINGS = 200


class _TooBroad(Exception):
    pass


def _spellings(word: str, limit: int = 64) -> list[str]:
    ""
    out = [word]
    for _ in range(4):
        nxt = []
        for w in out:
            m = _BRACES.search(w)
            nxt += [w[:m.start()] + part + w[m.end():] for part in m.group(1).split(",")] if m else [w]
        out = nxt[:limit]
    return out


def _glob_hits(pattern: str, budget: list[int]) -> list[str]:
    ""

    import fnmatch
    paths = ["/"]
    for part in pattern.split("/")[1:]:
        if not part or not paths:
            continue
        nxt = []
        for base in paths:
            if not _WILD.search(part):
                cand = os.path.join(base, part)
                if os.path.lexists(cand):
                    nxt.append(cand)
                continue
            budget[0] -= 1
            if budget[0] < 0:
                raise _TooBroad
            try:
                names = os.listdir(base)
            except OSError:
                continue
            nxt += [os.path.join(base, n) for n in names
                    if fnmatch.fnmatchcase(n, part) and (part.startswith(".") or not n.startswith("."))]
        paths = nxt[:2000]
    return paths


def _shell_tokens(text: str) -> list[str]:
    ""
    import shlex
    try:
        lex = shlex.shlex(text, posix=False)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return text.split()


def names_protected(text: str, home: str | None = None, cwd: str | None = None,
                    protected=SECRET_PATHS, _depth: int = 0) -> str | None:
    ""


    import shlex
    if protected is None:
        return None
    text = _with_variables(text)
    m = protected.search(text)
    if m:
        return m.group(0)
    body = _HEREDOC_BODY.sub("", text)
    home = (home or "").rstrip("/")
    here = [b for b in [cwd] + [g[1] for g in _CD.findall(body)] if b]
    here = [home + b[1:] if b.startswith("~/") and home else b for b in here]
    bases = here + ([home + "/taffy", home] if home else [])
    budget = [_LISTINGS]
    for tok in _shell_tokens(body):
        tok = tok.strip(";&|()`")
        if tok[:1] in ("'", '"') and tok[-1:] == tok[:1]:
            inner = tok[1:-1]
            found = re.search(r"\s", inner) and _depth < 2 and names_protected(inner, home, cwd, protected, _depth + 1)
            if found:
                return found
            continue
        try:
            word = "".join(shlex.split(tok))
        except ValueError:
            word = tok
        for w in _spellings(word) + _spellings(word.split("=", 1)[1] if "=" in word else ""):
            m = protected.search(w)
            if m:
                return m.group(0)
            if not _WILD.search(w):
                continue
            for pre in ("~/", "$HOME/", "${HOME}/"):
                if w.startswith(pre) and home:
                    w = home + "/" + w[len(pre):]


            pats = [w] if w.startswith("/") else [os.path.join(b, w) for b in (bases if "/" in w else here)
                                                  if b.startswith("/")]
            for pat in pats:
                try:
                    hits = _glob_hits(os.path.normpath(pat), budget)
                except _TooBroad:
                    return tok
                if any(protected.search(h) or protected.search(os.path.realpath(h)) for h in hits):
                    return tok
    return None






_ASSIGN = re.compile(r"(?:^|[;&|(\s])(?:export\s+|local\s+|declare\s+(?:-\w+\s+)?)?([A-Za-z_]\w*)="
                     r"('[^']*'|\"[^\"]*\"|[^\s;&|)]*)")
_JOIN = re.compile(r"""(['"])\s*\)?\s*([+,/]?)\s*[rbuf]{0,2}(['"])""", re.I)
_LITERAL = re.compile(r"""(['"])([^'"\n]*)\1""")


def _with_variables(text: str) -> str:
    ""
    values = {name: value.strip("'\"") for name, value in _ASSIGN.findall(text)}
    if not values:
        return text
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: values.get(m.group(1) or m.group(2), m.group(0)), text)


def names_protected_in_code(text: str, home: str | None = None, protected=SECRET_PATHS) -> str | None:
    ""

    if protected is None or not isinstance(text, str) or not re.search(r"""['"]""", text):
        return None
    home = (home or "").rstrip("/")
    budget = [_LISTINGS]
    for sep in ("/", ""):
        joined = _JOIN.sub(lambda m: {"+": "", "": "", "/": "/"}.get(m.group(2), sep), text)
        m = protected.search(joined)
        if m:
            return m.group(0)
        for _, lit in _LITERAL.findall(joined):
            if not _WILD.search(lit):
                continue
            for pre in ("~/", "$HOME/", "${HOME}/"):
                if lit.startswith(pre) and home:
                    lit = home + "/" + lit[len(pre):]
            if not lit.startswith("/"):
                continue
            try:
                hits = _glob_hits(os.path.normpath(lit), budget)
            except _TooBroad:
                return lit
            if any(protected.search(h) or protected.search(os.path.realpath(h)) for h in hits):
                return lit
    return None


class Refused(Exception):
    ""


def zip_roots(real_home, bot: str) -> tuple[str, ...]:
    ""

    settings = _home_settings()
    work = str(settings.path("work", real_home)).rstrip("/")
    own = work if bot in ("default", "", None) else f"{work}/{bot}"
    roots = (own, str(settings.path("shared", real_home)).rstrip("/"),
             str(settings.path("hermes_home", real_home) / "kanban" / "attachments"))
    return roots + extra_folders(bot, real_home)


_FOLDER_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def extra_folders(bot: str, real_home=None) -> tuple[str, ...]:
    ""



    import json
    settings = _home_settings()
    work = str(settings.path("work", real_home)).rstrip("/")
    entry = {}
    try:
        reg = json.loads((settings.path("hub_state", real_home) / "bots.json").read_text())
        entry = next((e for e in reg if isinstance(e, dict) and e.get("id") == bot), {}) if isinstance(reg, list) else {}
    except (OSError, ValueError):
        pass
    absorbed = entry.get("merged_from") if isinstance(entry.get("merged_from"), list) else []
    out = [f"{work}/{a}" for a in absorbed if isinstance(a, str) and _FOLDER_ID.fullmatch(a) and a != bot]
    team = (team_of(bot, real_home) or (str(entry.get("merged_team") or ""),))[0]
    if _FOLDER_ID.fullmatch(team):
        out.append(f"{work}/teams/{team}")
    return tuple(out)


def team_of(bot: str, real_home=None) -> tuple[str, str] | None:
    ""




    import sqlite3
    db = _home_settings().path("hub_state", real_home) / "hub.db"
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            r = c.execute("SELECT t.id, t.name FROM teams t JOIN team_members m ON m.team_id = t.id "
                          "WHERE m.bot_id=? AND t.state NOT IN ('deleted', 'merged')", (bot,)).fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return None
    return (str(r[0]), str(r[1] or r[0])) if r else None


def _expand(path: str, home: str | None) -> str:
    if not isinstance(path, str) or not path or "\0" in path:
        raise Refused("that is not a usable file path")
    if path.startswith("~/") and home:
        path = home.rstrip("/") + path[1:]
    if not path.startswith("/"):
        raise Refused(f"{path} is not a full path (start it with / or ~/)")
    return path


def _root_of(real: str, roots) -> tuple[str, str] | None:
    ""
    for r in roots:
        rr = os.path.realpath(str(r)).rstrip("/")
        if rr and real.startswith(rr + "/"):
            return rr, real[len(rr):]
    return None


def _check_stat(st: os.stat_result, shown: str) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise Refused(f"{shown} is not a regular file")
    if st.st_nlink > 1:

        raise Refused(f"{shown} has other hard links; copy the file instead of linking it")


def resolve(path: str, roots, home: str | None = None, protected=SECRET_PATHS) -> str:
    ""

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
    ""
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
    ""

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
    ""


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
