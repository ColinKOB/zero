"""Limits for Zips whose terminal runs in a container (docs/plans/2026-10-08-zip-sandbox-plan.md, stage 3).

A containerized Zip cannot reach alien's files or services from its terminal, but its browser and screen still run on
alien as Colin's user. This closes the two gaps the plan names before Clients and Builder move:

1. The browser opens only http and https pages (and about:blank), and never alien itself, the home network or the
   tailnet, apart from the Zip's own preview ports on 127.0.0.1. That covers browser_navigate (and browser_steps,
   which goes through it), Hermes's raw browser_cdp tool and browser_exec. browser_exec runs its Python on alien, not
   in the container, so for these Zips it may use only the browser helpers and plain Python: no modules beyond a few
   harmless ones, no files, no other programs, and every address it opens written out in full.
2. The screen tool (computer_use) cannot open a terminal, a file manager or a run dialog: the key combinations that
   do are refused, and so are other apps than the browser. alien/sandbox/zip-desktop-lockdown.py removes the launchers
   from the screen itself. Both are speed bumps, not walls; the screen still runs on alien.

Like the rest of the gate this is a tripwire over what the tool call says. A page's own scripts and redirects are not
seen here; see the stage 3 notes in docs/qa/2026-10-08-sandbox-stage3-code.md.

Zero and the Zips still on the local backend are unchanged: their terminal already runs on alien.
"""

import ast
import concurrent.futures
import ipaddress
import os
import posixpath
import re
import socket
import string
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import home as settings, safe_files


def _p(text: str) -> str:
    """A message written for Colin's home, said for this home (its server's name and its person's)."""
    return settings.personalize(text)

_info_cache: dict[str, tuple[float, dict | None]] = {}


def _terminal_block(text: str) -> str:
    m = re.search(r"(?m)^terminal:[ \t]*\n((?:[ \t]+.*\n?|[ \t]*\n)*)", text)
    return m.group(1) if m else ""


def container_info(home: Path) -> dict | None:
    """{"ports": {preview ports}} when the profile in `home` runs its terminal in Docker, else None. Read from the
    profile's own config.yaml (the gate's process reads it; no Zip does), and cached until the file changes."""
    path = Path(home) / "config.yaml"
    try:
        mtime = (path.stat().st_mtime, settings.preview_range())
    except OSError:
        return None
    hit = _info_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        block = _terminal_block(path.read_text(errors="replace"))
    except OSError:
        return None
    info = None
    if re.search(r"(?m)^\s+backend:\s*['\"]?docker['\"]?\s*$", block):
        ports = set()
        for m in re.finditer(r"(?:127\.0\.0\.1:)(\d{2,5})(?:-(\d{2,5}))?:\d", block):
            first, last = int(m.group(1)), int(m.group(2) or m.group(1))
            if 0 < first <= last <= 65535 and last - first < 200:
                ports.update(range(first, last + 1))
        own = settings.preview_range()
        info = {"ports": {p for p in ports if p in own}}
    _info_cache[str(path)] = (mtime, info)
    return info


ALLOWED_SCHEMES = {"http", "https"}
LOCAL_SCHEMES = re.compile(r"(?<![\w.+-])(file|view-source|chrome|chrome-untrusted|chrome-extension|chrome-search|"
                           r"devtools|filesystem|about|data|javascript|blob|ws|wss|ftp|smb|sftp|ssh)\s*:", re.I)
TEXT_SCHEMES = re.compile(r"(?<![\w.+-])(file|view-source|chrome|chrome-untrusted|chrome-extension|chrome-search|"
                          r"devtools|filesystem)\s*:", re.I)
_HTTP_URL = re.compile(r"(?i)\bhttps?://[^\s'\"\\<>`)\]}]+")
_BARE_HOST = re.compile(r"(?i)(?<![\w.@/-])(localhost|(?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:.]+\])(?::(\d{1,5}))?(?![\w.-])")
_LOCAL_NAMES = re.compile(r"(?i)(^|\.)(localhost|local|lan|home|internal|intranet|corp|home\.arpa|ts\.net|"
                          r"localdomain)$")
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}

_dns_cache: dict[str, tuple[float, list[str]]] = {}
_DNS_SEC = 60
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _getaddrinfo(host: str) -> list[str]:
    return [i[4][0] for i in socket.getaddrinfo(host, None)]


def _resolve(host: str) -> list[str]:
    """The addresses a name resolves to, or [] when it does not resolve quickly (the browser cannot open it either).
    Tests replace this."""
    hit = _dns_cache.get(host)
    if hit and time.time() - hit[0] < _DNS_SEC:
        return hit[1]
    try:
        ips = _pool.submit(_getaddrinfo, host).result(timeout=2)
    except Exception:
        ips = []
    _dns_cache[host] = (time.time(), ips)
    return ips


def _ip(host: str):
    """The address a host stands for when it is written as one, in any form a browser accepts (127.1, 0x7f000001,
    2130706433, 0177.0.0.1, [::ffff:127.0.0.1]), else None."""
    h = host.strip("[]")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.inet_aton(h)) if re.fullmatch(r"[0-9a-fA-FxX.]+", h) else None
        except (OSError, ValueError):
            ip = None
    if ip is not None and ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def _local_ip(ip) -> bool:
    return not ip.is_global or ip.is_multicast


def host_problem(host: str, port: int | None, ports: set[int]) -> str | None:
    """Why the browser may not open this host, or None. Loopback is fine on the Zip's own preview ports only."""
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return "it has no address"
    own = port is not None and port in ports
    ip = _ip(host)
    if ip is not None:
        if ip.is_loopback and own:
            return None
        return _p("that address is on alien or Colin's own network") if _local_ip(ip) else None
    if re.fullmatch(r"[\d.]+", host) or re.search(r"\.\d+$", host):
        return "that address is not a normal web address"
    if host in _LOOPBACK_NAMES or host.endswith(".localhost"):
        return None if own else _p("that address is alien itself")
    if "." not in host or _LOCAL_NAMES.search(host):
        return _p("that name belongs to alien or Colin's own network")
    for addr in _resolve(host):
        a = _ip(addr.split("%")[0])
        if a is not None and _local_ip(a) and not (a.is_loopback and own):
            return host + _p(" points to an address on alien or Colin's own network")
    return None


def url_problem(url: str, ports: set[int]) -> str | None:
    """Why a containerized Zip's browser may not open this address, or None. Only http, https and about:blank."""
    # The browser drops tabs and line breaks from an address and reads a backslash as a slash.
    u = re.sub(r"[\t\r\n]", "", (url or "").strip()).replace("\\", "/")
    if not u:
        return None
    if re.fullmatch(r"(?i)about:blank(#.*)?", u):
        return None
    m = re.match(r"^([a-zA-Z][\w+.-]*):(?!\d)", u)
    if m:
        if m.group(1).lower() not in ALLOWED_SCHEMES:
            return f"the browser opens only http and https pages, not {m.group(1).lower()}: addresses"
    else:
        u = "http://" + u
    try:
        parts = urlsplit(u)
        port = parts.port
    except ValueError:
        return "that address could not be read"
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return f"the browser opens only http and https pages, not {parts.scheme}: addresses"
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    return host_problem(parts.hostname or "", port, ports)


def text_problem(text: str, ports: set[int]) -> str | None:
    """Addresses written anywhere in a piece of text (a page script, typed text): local schemes, and http addresses or
    bare hosts that reach alien or Colin's network."""
    text = text or ""
    m = TEXT_SCHEMES.search(text)
    if m:
        return f"the browser opens only http and https pages, not {m.group(1).lower()}: addresses"
    for u in _HTTP_URL.findall(text):
        why = url_problem(u.rstrip(".,;:!?"), ports)
        if why:
            return why
    for m in _BARE_HOST.finditer(text):
        host, port = m.group(1), m.group(2)
        if host.lower() != "localhost" and _ip(host) is None:
            continue
        why = host_problem(host, int(port) if port else None, ports)
        if why:
            return why
    return None


def _message(why: str, ports: set[int]) -> str:
    own = (f" Your own previews are at http://127.0.0.1:{min(ports)} to {max(ports)}." if ports else "")
    return (f"Blocked: {why}. " + _p("Your browser runs on alien outside your container, so it opens only public "
                                     "http and https pages.") + f"{own} Read files with your file tools instead, and "
            "do not try another way.")


# The browser helpers browser_exec offers (browser_harness.helpers), minus http_get (an outside fetch from alien that
# follows redirects anywhere; use curl in the container) and the recording and daemon helpers.
HELPERS = {"cdp", "drain_events", "goto_url", "page_info", "click_at_xy", "type_text", "fill_input", "press_key",
           "scroll", "capture_screenshot", "list_tabs", "current_tab", "activate_tab", "switch_tab", "new_tab",
           "close_tab", "ensure_real_tab", "iframe_target", "wait", "wait_for_load", "wait_for_element",
           "wait_for_network_idle", "js", "dispatch_key", "upload_file"}
URL_HELPERS = {"new_tab": ("url", 0), "goto_url": ("url", 0)}
BUILTINS = {"print", "len", "range", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "frozenset",
            "sorted", "reversed", "enumerate", "zip", "min", "max", "sum", "any", "all", "abs", "round", "isinstance",
            "map", "filter", "repr", "divmod", "chr", "ord", "hex", "bytes", "type", "Exception", "ValueError",
            "KeyError", "TypeError", "RuntimeError", "IndexError", "TimeoutError", "AttributeError", "StopIteration",
            "iter", "next", "callable"}
MODULES = {"json": {"dumps", "loads", "JSONDecodeError"},
           "re": {"search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split", "compile", "escape",
                  "I", "IGNORECASE", "M", "MULTILINE", "S", "DOTALL", "X", "VERBOSE", "A", "ASCII"},
           "time": {"sleep", "time", "monotonic", "strftime", "localtime", "gmtime"},
           "math": None,   # any public name: the module holds only numbers and functions of numbers
           "datetime": {"datetime", "date", "timedelta", "timezone"}}
# Attributes that lead from an ordinary object back to the interpreter (frames, code, globals).
_BAD_ATTR = re.compile(r"^(_|(gi|cr|ag|f|tb|co)_|mro$|func_)")
_JS_SINKS = re.compile(r"\blocation\b|window\.open|\.href\s*=|\.src\s*=|\bfetch\s*\(|XMLHttpRequest|sendBeacon|"
                       r"\bimport\s*\(|\.action\s*=|\.submit\s*\(|WebSocket|EventSource", re.I)
CDP_BLOCKED = {"DOM.setFileInputFiles": "picking files from alien for a page",
               "Page.setDownloadBehavior": "choosing where downloads go on alien",
               "Browser.setDownloadBehavior": "choosing where downloads go on alien",
               "Browser.grantPermissions": "giving pages new permissions",
               "Browser.setPermission": "giving pages new permissions",
               "Target.exposeDevToolsProtocol": "giving a page control of the browser",
               "Tracing.start": "writing a trace file on alien",
               "Page.setInterceptFileChooserDialog": "picking files from alien for a page"}
_URL_PARAM = re.compile(r"(?i)^(url|.*Url|.*URL)$")
EXEC_RULE = ("browser_exec runs on alien outside your container, so there it may use only the browser helpers and plain "
             "Python")


class _Problem(Exception):
    pass


class _IterOf:
    """A loop variable: it takes each value of what the loop goes over."""

    def __init__(self, node):
        self.node = node


def _str_values(node, bindings: dict, depth: int = 0) -> list[str] | None:
    """Every value a URL argument can have when the code spells it out: a string, an f-string or sum whose host is
    written out, or a name only ever set to such values. None when the code works it out some other way."""
    if depth > 4:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    first = None
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        first = node.values[0].value
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _str_values(node.left, bindings, depth + 1)
        if left is not None and len(left) == 1:
            first = left[0]
    if isinstance(first, str):
        m = re.match(r"(?i)^(https?://[^/?#]+)[/?#]", first)
        if m and "{" not in m.group(1):
            return [first]
        return None
    if isinstance(node, _IterOf):
        seqs = [node.node] if not isinstance(node.node, ast.Name) else bindings.get(node.node.id) or [None]
        out = []
        for seq in seqs:
            if not isinstance(seq, (ast.List, ast.Tuple, ast.Set)):
                return None
            for e in seq.elts:
                got = _str_values(e, bindings, depth + 1)
                if got is None:
                    return None
                out += got
        return out
    if isinstance(node, ast.Name):
        values = bindings.get(node.id)
        if not values:
            return None
        out = []
        for v in values:
            got = _str_values(v, bindings, depth + 1) if v is not None else None
            if got is None:
                return None
            out += got
        return out
    return None


def _bindings(tree) -> tuple[dict, set]:
    """name -> the expressions it is set to (None for a value the code works out), and every name the code binds."""
    values: dict[str, list] = {}
    bound: set[str] = set()

    def add(target, value):
        if isinstance(target, ast.Name):
            values.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for t in target.elts:
                add(t, None)
        elif isinstance(target, ast.Starred):
            add(target.value, None)

    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                add(t, n.value)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            add(n.target, n.value if isinstance(n, (ast.AnnAssign, ast.NamedExpr)) else None)
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            add(n.target, _IterOf(n.iter) if isinstance(n.target, ast.Name) else None)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    add(item.optional_vars, None)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if not isinstance(n, ast.Lambda):
                bound.add(n.name)
            a = n.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + \
                    ([a.kwarg] if a.kwarg else []):
                values.setdefault(arg.arg, []).append(None)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            values.setdefault(n.name, []).append(None)
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
    bound |= set(values)
    return values, bound


def _check_format(node: ast.Call) -> None:
    """str.format can read attributes of what it is given ("{0.__globals__}"), so only a written-out pattern with
    plain fields is allowed."""
    f = node.func
    if not isinstance(f.value, ast.Constant) or not isinstance(f.value.value, str):
        raise _Problem("it formats text with a pattern that is not written out; use an f-string instead")
    for _lit, field, spec, _conv in string.Formatter().parse(f.value.value):
        if field and re.search(r"[.\[]", field) or spec and "{" in spec:
            raise _Problem("it formats text with a pattern that reads attributes; use an f-string instead")


def inside(path: str, roots: tuple[str, ...], home: str) -> bool:
    """The file a path really names on alien is inside one of the roots. Links are followed: a link a Zip makes in its
    work folder names whatever it points to on alien, not a file in the container."""
    p = (home.rstrip("/") + path[1:]) if path.startswith("~/") else path
    if not p.startswith("/"):
        return False
    real = os.path.realpath(posixpath.normpath(p))
    return any(real.startswith(os.path.realpath(r.rstrip("/")) + "/") for r in roots)


def _check_path(node, what: str, roots: tuple[str, ...], home: str) -> None:
    vals = _str_values(node, {}) if node is not None else None
    if not vals:
        raise _Problem(f"{what} must be given as a written-out path")
    for v in vals:
        try:
            safe_files.resolve(v, roots, home)
        except safe_files.Refused as e:
            raise _Problem(f"{what} must be a file in the work or shared folders ({v} is not: {e})") from None


def _check_url_arg(node, bindings, ports, what: str) -> None:
    vals = _str_values(node, bindings)
    if vals is None:
        raise _Problem(f"{what} must be written out in full as an https:// address (to follow a link on the page, "
                       "click it instead)")
    for v in vals:
        why = url_problem(v, ports)
        if why:
            raise _Problem(why)


def _check_cdp(call: ast.Call, bindings, ports) -> None:
    if not call.args or not (isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
        raise _Problem("cdp() must name its method as a written-out string")
    method = call.args[0].value
    if method in CDP_BLOCKED:
        raise _Problem(f"{method} is off ({_p(CDP_BLOCKED[method])})")
    for kw in call.keywords:
        if kw.arg is None:
            raise _Problem("cdp() must spell out its parameters (no **)")
        if _URL_PARAM.match(kw.arg):
            _check_url_arg(kw.value, bindings, ports, f"the {kw.arg} given to {method}")
        if method in ("Runtime.evaluate", "Runtime.callFunctionOn") and kw.arg in ("expression", "functionDeclaration"):
            _check_script(kw.value, ports)


def _check_script(node, ports) -> None:
    """A page script built from changing parts may not move the page or send requests."""
    if isinstance(node, ast.Constant):
        return
    text = " ".join(n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str))
    if _JS_SINKS.search(text):
        raise _Problem("a page script that opens addresses or sends requests must be written out in full, with no "
                       "changing parts")


def browser_exec_problem(code: str, ports: set[int], work_roots: tuple[str, ...], home: str = "") -> str | None:
    """Why this browser_exec code may not run for a containerized Zip, or None."""
    try:
        tree = ast.parse(code or "", mode="exec")
    except SyntaxError as e:
        return f"{_p(EXEC_RULE)}, and this code does not parse ({e.msg} on line {e.lineno})"
    bindings, bound = _bindings(tree)
    aliases: dict[str, str] = {}
    imported: set[str] = set()
    calls_ok: set[int] = set()
    try:
        for n in ast.walk(tree):
            if isinstance(n, (ast.Global, ast.Nonlocal, ast.ClassDef, ast.Match)):
                raise _Problem(f"{type(n).__name__.lower()} statements are not available")
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name not in MODULES:
                        raise _Problem(f"the module {a.name} is not available")
                    aliases[a.asname or a.name] = a.name
            elif isinstance(n, ast.ImportFrom):
                if n.level or n.module not in MODULES:
                    raise _Problem(f"the module {n.module or '.'} is not available")
                allowed = MODULES[n.module]
                for a in n.names:
                    if a.name == "*" or a.name.startswith("_") or (allowed is not None and a.name not in allowed):
                        raise _Problem(f"{n.module}.{a.name} is not available")
                    imported.add(a.asname or a.name)
        for name in bound:
            if name in HELPERS or name in aliases:
                raise _Problem(f"the name {name} belongs to a helper or module and cannot be reused")
        attr_of = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name) and f.id in HELPERS and f.id not in bound:
                    calls_ok.add(id(f))
                    if f.id in URL_HELPERS:
                        key, pos = URL_HELPERS[f.id]
                        arg = n.args[pos] if len(n.args) > pos else next(
                            (k.value for k in n.keywords if k.arg == key), None)
                        if arg is not None:
                            _check_url_arg(arg, bindings, ports, f"the address given to {f.id}")
                    elif f.id == "cdp":
                        _check_cdp(n, bindings, ports)
                    elif f.id == "js":
                        if n.args:
                            _check_script(n.args[0], ports)
                    elif f.id == "capture_screenshot":
                        if n.args or any(k.arg in ("path", None) for k in n.keywords):
                            raise _Problem("capture_screenshot() saves where Hermes can show it; leave out the path")
                    elif f.id == "upload_file":
                        node = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "path"),
                                                                     None)
                        _check_path(node, "the file given to upload_file", work_roots, home)
                    if any(isinstance(a, ast.Starred) for a in n.args) or any(k.arg is None for k in n.keywords):
                        raise _Problem("helpers must be given their arguments one by one (no * or **)")
                elif isinstance(f, ast.Attribute) and f.attr in ("format", "format_map"):
                    _check_format(n)
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute):
                if _BAD_ATTR.search(n.attr):
                    raise _Problem(f"the attribute {n.attr} is not available")
                if isinstance(n.value, ast.Name) and n.value.id in aliases:
                    allowed = MODULES[aliases[n.value.id]]
                    if allowed is not None and n.attr not in allowed:
                        raise _Problem(f"{aliases[n.value.id]}.{n.attr} is not available")
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                name = n.id
                if name in HELPERS and name not in bound:
                    if id(n) not in calls_ok:
                        raise _Problem(f"the helper {name} may only be called directly")
                elif name in aliases:
                    if id(n) not in attr_of:
                        raise _Problem(f"the module {name} may only be used as {name}.<function>")
                elif name in bound or name in BUILTINS or name in imported:
                    if name.startswith("__"):
                        raise _Problem(f"the name {name} is not available")
                else:
                    raise _Problem(f"{name} is not available (only the browser helpers, these modules: "
                                   f"{', '.join(sorted(MODULES))}, and plain Python are)")
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                why = text_problem(n.value, ports)
                if why:
                    raise _Problem(why)
    except _Problem as e:
        return f"{_p(EXEC_RULE)}: {e}"
    return None


def cdp_tool_problem(args: dict, ports: set[int]) -> str | None:
    """Hermes's browser_cdp tool: the same method and address limits as cdp() inside browser_exec."""
    method = str(args.get("method") or "")
    if method in CDP_BLOCKED:
        return f"{method} is off ({_p(CDP_BLOCKED[method])})"
    params = args.get("params") if isinstance(args.get("params"), dict) else {}
    for k, v in params.items():
        if _URL_PARAM.match(str(k)):
            why = url_problem(str(v), ports)
            if why:
                return why
    import json
    return text_problem(json.dumps(params, ensure_ascii=False), ports)


_MODS = {"ctrl": "ctrl", "control": "ctrl", "primary": "ctrl", "alt": "alt", "option": "alt", "mod1": "alt",
         "shift": "shift", "super": "super", "win": "super", "windows": "super", "meta": "super", "cmd": "super",
         "command": "super", "hyper": "super", "fn": "fn"}
_KEYS_ALONE = {"menu", "f12"}
_ALT_KEYS = {"f1", "f2", "f3", "f", "e"}                       # app menu, run dialog, app finder, browser menus
_CTRL_KEYS = {"o", "s", "p", "j", "escape", "esc"}             # open file, save page, print, downloads, app menu
_CTRL_SHIFT_KEYS = {"i", "j", "c", "o", "escape", "esc", "m"}  # developer tools, bookmarks manager, task manager
BROWSER_APPS = re.compile(r"(?i)chrom|browser")


def _combo(keys: str, modifiers) -> tuple[set, str]:
    mods, key = set(), ""
    for part in re.split(r"[+\s]+", (keys or "").strip().lower()):
        part = re.sub(r"_[lr]$", "", part.strip("<>"))
        if not part:
            continue
        if part in _MODS:
            mods.add(_MODS[part])
        else:
            key = part
    for m in modifiers or []:
        if str(m).lower() in _MODS:
            mods.add(_MODS[str(m).lower()])
    return mods, key


def key_problem(keys: str, modifiers) -> str | None:
    mods, key = _combo(keys, modifiers)
    if "super" in mods or key in _KEYS_ALONE or key.startswith("xf86"):
        return _p("that key opens alien's menus or launchers")
    if {"ctrl", "alt"} <= mods:
        return _p("Ctrl+Alt combinations open a terminal or other programs on alien")
    if mods == {"alt"} and key in _ALT_KEYS:
        return "that key opens a run dialog, an app menu or the browser's menu"
    if mods == {"ctrl"} and key in _CTRL_KEYS:
        return "that key opens a file dialog, the downloads page or an app menu"
    if mods == {"ctrl", "shift"} and key in _CTRL_SHIFT_KEYS:
        return "that key opens the browser's developer tools or another window"
    return None


def computer_use_problem(args: dict, ports: set[int]) -> str | None:
    action = str(args.get("action") or "")
    if action in ("capture", "wait", "list_apps", "list_windows", ""):
        return None
    app = str(args.get("app") or "")
    if action == "focus_app" or (app and app != "screen"):
        if not BROWSER_APPS.search(app):
            return "only the browser may be used on your screen"
    if action == "key" or args.get("modifiers"):
        why = key_problem(str(args.get("keys") or ""), args.get("modifiers"))
        if why:
            return why
    for field in ("text", "value"):
        if isinstance(args.get(field), str):
            why = text_problem(args[field], ports) or _typed_url(args[field], ports)
            if why:
                return why
    return None


def _typed_url(text: str, ports: set[int]) -> str | None:
    """What is typed into the address bar as a whole: "file:///home", "about:settings", "chrome://settings"."""
    t = (text or "").strip()
    if not t or "\n" in t.rstrip("\n") or " " in t:
        return None
    if LOCAL_SCHEMES.match(t) and not re.fullmatch(r"(?i)about:blank", t):
        return url_problem(t, ports)
    return None


def work_roots(real_home: Path, bot: str | None = None) -> tuple[str, ...]:
    """The folders a Zip may hand files on from (safe_files.zip_roots); every Zip's work folder when no Zip is named."""
    if bot is not None:
        return safe_files.zip_roots(real_home, bot)
    return (str(settings.path("work", real_home)).rstrip("/") + "/",
            str(settings.path("shared", real_home)).rstrip("/") + "/")


def check(tool_name: str, args: dict, info: dict | None, real_home: Path, bot: str = "default") -> dict | None:
    """A block for a containerized Zip's browser or screen call that would reach alien itself, else None."""
    if info is None:
        return None
    ports = set(info.get("ports") or ())
    why = None
    if tool_name == "browser_navigate":
        why = url_problem(str(args.get("url") or ""), ports)
    elif tool_name == "browser_exec":
        if args.get("local"):
            why = "the local browser profile is not available to you"
        else:
            why = browser_exec_problem(str(args.get("code") or ""), ports, work_roots(real_home, bot),
                                       str(real_home))
    elif tool_name == "browser_cdp":
        why = cdp_tool_problem(args, ports)
    elif tool_name == "computer_use":
        why = computer_use_problem(args, ports)
    if not why:
        return None
    if tool_name == "computer_use":
        return {"action": "block", "message": (
            f"Blocked: {why}. " + _p("Your screen runs on alien outside your container, so it is for the browser "
                                     "only.") + " Do not try another way.")}
    return {"action": "block", "message": _message(why, ports)}
