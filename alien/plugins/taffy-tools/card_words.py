""

















import datetime
import json
import os
import posixpath
import re
import stat
import time
from dataclasses import dataclass

from . import consent, home, tiers

MAC = "mac"
INSPECT_MAX = 3
WALK_MAX = 20000
BORN_SLACK_SEC = 300


@dataclass(frozen=True)
class _ScratchCopy:
    source: str
    identity: tuple[int, ...]



_scratch_copies: dict[tuple[str, str], _ScratchCopy] = {}


def _scratch_root(bot: str) -> str:
    root = home.path("hermes_home", consent.REAL_HOME)
    if bot != "default":
        root = root / "profiles" / bot
    return str(root / "cache" / "scratch")


def _local_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _file_identity(path: str) -> tuple[int, ...] | None:
    try:
        if os.path.realpath(path) != path:
            return None
        s = os.lstat(path)
        if stat.S_ISREG(s.st_mode) and s.st_nlink == 1:
            return s.st_dev, s.st_ino, s.st_ctime_ns, s.st_mtime_ns, s.st_size
    except OSError:
        pass
    return None


def _same_copy(path: str, source: str) -> bool:
    try:

        with open(path, "rb") as copy, open(source, "rb") as original:
            if not stat.S_ISREG(os.fstat(original.fileno()).st_mode) or os.path.samefile(path, source):
                return False
            while True:
                block = copy.read(65536)
                if block != original.read(65536):
                    return False
                if not block:
                    return True
    except OSError:
        return False


def owns_scratch_copies(paths: list[str], bot: str) -> bool:
    ""
    if not paths or len(paths) > 20 or any(_GLOB.search(p) or not p.startswith(("/", "~/")) for p in paths):
        return False
    targets = [_local_path(p) for p in paths]
    root = _local_path(_scratch_root(bot))
    for path in targets:
        made = _scratch_copies.get((bot, path))
        if not path.startswith(root + os.sep) or made is None or _file_identity(path) != made.identity:
            return False
        if made.source in targets or not _same_copy(path, made.source):
            return False
    return True




_ICLOUD = "~/Library/Mobile Documents/com~apple~CloudDocs"


def where(path: str, place: str) -> tuple[str, str]:
    ""
    p = path.rstrip("/") or path
    if place == MAC:
        if p == _ICLOUD or p.startswith(_ICLOUD + "/"):
            p = "iCloud Drive" + p[len(_ICLOUD):]
    parent, name = posixpath.split(p)
    if parent == "~":
        return name, "your home folder" if place == MAC else "its home folder"
    if parent in ("", "/"):
        return name, "the top of the disk"
    return name, posixpath.basename(parent) or parent


def on(place: str) -> str:
    return " on your Mac" if place == MAC else ""


def size(n_bytes: int) -> str:
    n = float(max(n_bytes, 0))
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            if unit == "bytes":
                return f"{int(n)} bytes"
            return f"{n:.1f} {unit}" if n < 10 else f"{n:.0f} {unit}"
        n /= 1000
    return f"{n:.0f} TB"


def when(ts: float, now: float | None = None) -> str:
    ""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(home.timezone())
    except Exception:
        tz = None
    t = datetime.datetime.fromtimestamp(ts, tz)
    today = datetime.datetime.fromtimestamp(now if now is not None else time.time(), tz).date()
    clock = t.strftime("%I:%M %p").lstrip("0")
    if t.date() == today:
        return f"today at {clock}"
    if t.date() == today - datetime.timedelta(days=1):
        return f"yesterday at {clock}"
    return "on " + t.strftime("%b %d").replace(" 0", " ") + ("" if t.year == today.year else f", {t.year}")


def _list(items: list[str]) -> str:
    return tiers._and(items) if items else ""




_MKDIR = re.compile(r"^(?:\S*/)?mkdir\s+(.*)$", re.S)
_FILL = re.compile(r"^(?:\S*/)?(cp|ditto|rsync|mv)\s+(.*)$", re.S)
_REMOTE = re.compile(r"^[\w.@-]+:")
_VALUE_FLAGS = {"mkdir": {"-m"}, "rsync": {"-e", "--rsh", "--exclude", "--include", "--filter", "-f"}}


def _operands(words: list[str], cmd: str) -> list[str]:
    out, skip, ended = [], False, False
    for w in tiers._plain_words(words):
        if skip:
            skip = False
            continue
        if w == "--" and not ended:
            ended = True
        elif ended or not w.startswith("-"):
            out.append(w)
        elif w in _VALUE_FLAGS.get(cmd, ()):
            skip = True
    return out


def made_by_command(cmd: str, cwd: str | None) -> list[tuple[str, str, list[str]]]:
    ""

    out = []
    for seg, here in tiers._segments(cmd, cwd):
        m = _MKDIR.match(seg)
        if m:
            for w in _operands(tiers._words(m.group(1)), "mkdir"):
                p = tiers._norm(w, here)
                if p:
                    out.append((p.rstrip("/"), "mkdir", []))
            continue
        m = _FILL.match(seg)
        if not m:
            continue
        words = _operands(tiers._words(m.group(2)), m.group(1))
        if len(words) < 2 or _REMOTE.match(words[-1]):
            continue
        dest = tiers._norm(words[-1], here)
        if not dest:
            continue
        sources = [tiers._norm(w, here) or w for w in words[:-1] if not _REMOTE.match(w)]

        moves = m.group(1) == "mv" or re.search(r"(?:^|\s)--remove-source-files\b", m.group(2)) is not None
        out.append((dest.rstrip("/"), "move" if moves else "copy", sources))
    return out


_BRANCHES = re.compile(r"\|\||;|\n|\||(?<!&)&(?!&)|\$\(|`|\b(?:if|then|else|for|while|until|case)\b")


def runs_every_part(cmd: str) -> bool:
    ""

    plain = re.sub(r"\d*>&\d+|&>", "", cmd)
    return _BRANCHES.search(plain) is None


def _result_failed(result) -> bool:
    data = result
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return False
    if not isinstance(data, dict):
        return False
    if data.get("error"):
        return True
    if data.get("ok") is False or data.get("success") is False:
        return True
    inner = data.get("result") if isinstance(data.get("result"), dict) else data
    if inner.get("error") or inner.get("timed_out"):
        return True
    code = inner.get("exit_code", inner.get("returncode"))
    return isinstance(code, int) and not isinstance(code, bool) and code != 0


def _work(tool_name: str, args: dict, cwd: str | None, contained: bool) -> list[tuple[str, str, list[str]]]:
    ""
    if tool_name not in ("terminal", "mac_run") or not isinstance(args, dict) or (tool_name == "terminal" and contained):
        return []
    cmd = str(args.get("command") or "")
    if not runs_every_part(cmd):
        return []
    work = made_by_command(cmd, "~" if tool_name == "mac_run" else cwd)
    if tool_name != "terminal":
        return work
    out = list(work)
    for path, how, sources in work:
        if how == "copy" and os.path.isdir(os.path.expanduser(path)):

            out += [(posixpath.join(path, posixpath.basename(s)), how, [s]) for s in sources
                    if not _GLOB.search(s) and s.startswith(("/", "~/"))]
    return out


def existed_before(tool_name: str, args: dict, cwd: str | None, contained: bool, exists) -> dict[str, bool | None]:
    ""


    return {path: exists(path) for path in dict.fromkeys(p for p, _how, _src in _work(tool_name, args, cwd, contained))}


def note_result(tool_name: str, args: dict, result, bot: str, cwd: str | None, contained: bool,
                existed: dict[str, bool | None], born_of=None) -> None:
    ""


    if _result_failed(result):
        return
    place = MAC if tool_name == "mac_run" else f"computer:{bot}"
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        data = {}
    inner = data.get("result", data) if isinstance(data, dict) else {}
    code = inner.get("exit_code", inner.get("returncode")) if isinstance(inner, dict) else None
    succeeded = type(code) is int and code == 0
    made = set()
    for path, how, sources in _work(tool_name, args, cwd, contained):
        if existed.get(path) is False and path not in made:
            made.add(path)

            consent.note_made(bot, place, path, "made", born=born_of(path) if born_of else None)
        if how != "mkdir":
            consent.note_made(bot, place, path, how, sources)
        full, root = _local_path(path), _local_path(_scratch_root(bot))
        if tool_name == "terminal" and succeeded and existed.get(path) is False and how == "copy" \
                and len(sources) == 1 and full.startswith(root + os.sep):
            identity, source = _file_identity(full), _local_path(sources[0])
            if identity is not None and _same_copy(full, source) and _file_identity(full) == identity:
                if len(_scratch_copies) >= 1024:
                    _scratch_copies.pop(next(iter(_scratch_copies)))
                _scratch_copies[bot, full] = _ScratchCopy(source, identity)


def born_here(path: str) -> float | None:
    try:
        return getattr(os.lstat(os.path.expanduser(path)), "st_birthtime", None)
    except OSError:
        return None


def born_mac(path: str, session_id: str) -> float | None:
    from . import mac
    reply = mac._call("inspect", {"path": path, "brief": True}, 15, session_id=session_id)
    result = reply.get("result") if reply.get("ok") else None
    return result.get("born") if isinstance(result, dict) else None


def exists_here(path: str) -> bool | None:
    try:
        os.lstat(os.path.expanduser(path))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def exists_mac(path: str, session_id: str) -> bool | None:
    ""
    from . import mac
    reply = mac._call("inspect", {"path": path, "brief": True}, 15, session_id=session_id)
    result = reply.get("result") if reply.get("ok") else None
    return bool(result.get("exists")) if isinstance(result, dict) and "exists" in result else None




def inspect_here(path: str) -> dict | None:
    ""
    full = os.path.expanduser(path)
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        return {"exists": False}
    except OSError:
        return None
    born = getattr(st, "st_birthtime", None)
    if not os.path.isdir(full) or os.path.islink(full):
        return {"exists": True, "kind": "file", "files": 1, "bytes": st.st_size, "born": born,
                "names": [os.path.basename(full)]}
    files, total, names, seen, failed = 0, 0, [], 0, []
    for root, dirs, fnames in os.walk(full, onerror=failed.append):
        dirs.sort()
        for f in sorted(fnames):
            seen += 1
            if seen > WALK_MAX:
                return {"exists": True, "kind": "folder", "files": None, "bytes": None, "born": born, "names": names}
            files += 1
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
            if len(names) < 3:
                names.append(f)
    if failed:
        return {"exists": True, "kind": "folder", "files": None, "bytes": None, "born": born, "names": []}
    try:
        empty = not any(True for _ in os.scandir(full))
    except OSError:
        empty = False
    return {"exists": True, "kind": "folder", "files": files, "bytes": total, "born": born, "names": names,
            "empty": empty}


def originals_here(path: str, folder: str) -> bool | None:
    ""

    import filecmp
    full, other = os.path.expanduser(path), os.path.expanduser(folder)
    if _inside(os.path.realpath(other), os.path.realpath(full)):
        return False
    failed, seen = [], 0
    for root, _dirs, fnames in os.walk(full, onerror=failed.append):
        for f in fnames:
            seen += 1
            twin = os.path.join(other, f)
            try:
                mine = os.path.join(root, f)
                same = os.path.isfile(twin) and not os.path.islink(twin) and not os.path.samefile(mine, twin) and \
                    filecmp.cmp(mine, twin, shallow=False)
            except OSError:
                return None
            if seen > WALK_MAX or not same:
                return False
    return None if failed else seen > 0


def originals_mac(path: str, folder: str, session_id: str) -> bool | None:
    ""
    from . import mac
    reply = mac._call("inspect", {"path": path, "compare": folder}, 20, session_id=session_id)
    result = reply.get("result") if reply.get("ok") else None
    return result.get("originals") if isinstance(result, dict) else None


def inspect_mac(path: str, session_id: str) -> dict | None:
    ""
    from . import mac
    reply = mac._call("inspect", {"path": path}, 20, session_id=session_id)
    return reply.get("result") if reply.get("ok") and isinstance(reply.get("result"), dict) else None




def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _source_folder(sources: list[str], place: str) -> str:
    ""
    folders = {where(s.rstrip("/"), place)[1] for s in sources if s and not s.startswith("$")}
    return folders.pop() if len(folders) == 1 else ""


def _contents(facts: dict | None) -> str:
    if not facts or not facts.get("exists", True):
        return "It is not there anymore." if facts else ""
    if facts.get("kind") == "file":
        return f"It is a {size(facts['bytes'])} file." if facts.get("bytes") is not None else ""
    n = facts.get("files")
    if n is None:
        return ""
    if n == 0:

        return "It is empty." if facts.get("empty") else ""
    names = [str(x) for x in facts.get("names") or []][:3]
    amount = f"{n} file{'s' if n != 1 else ''}, {size(facts.get('bytes') or 0)} in all"
    if not names:
        return f"It holds {amount}."
    return f"It holds {amount}: {_list(names)}." if n <= 3 else f"It holds {amount}, such as {_list(names)}."


_GLOB = re.compile(r"[*?\[\]{}]")


def _made(path: str, mine: list[dict], born: float | None) -> dict | None:
    ""



    if not born:
        return None
    for r in reversed(mine):
        if r["path"] != path or r["how"] != "made":
            continue

        if (abs(born - r["born"]) < 2) if r.get("born") else abs(born - r["at"]) <= BORN_SLACK_SEC:
            return r
    return None


def _same(path: str, place: str) -> str:
    return path.rstrip("/") or path


def _home_spelled_out(path: str) -> bool:
    ""

    return path.startswith(("/Users/", "/home/"))


def _originals_there(path: str, copied_from: list[str], place: str, originals) -> bool:
    ""


    folders = {posixpath.dirname(s.rstrip("/")) for s in copied_from}
    if originals is None or len(folders) != 1:
        return False
    folder = folders.pop()
    if not folder.startswith(("/", "~")) or _GLOB.search(folder) or _home_spelled_out(folder):
        return False
    return originals(path, folder) is True


def delete_words(paths: list[str], place: str, bot_name, inspect, originals=None) -> dict:
    ""




    if not paths or len(paths) > INSPECT_MAX or any(not p.startswith(("/", "~")) or _GLOB.search(p) for p in paths):
        return {"button": "Delete"}
    paths = [_same(p, place) for p in paths]
    records = [{**r, "path": _same(r["path"], place), "sources": [_same(s, place) for s in r["sources"]]}
               for r in consent.made_records(place)]
    lines, kinds, makers, moved, copied_from, looked = [], [], [], False, [], []
    for path in paths:
        facts = inspect(path)
        looked.append(facts)
        born = (facts or {}).get("born")
        mine = [r for r in records if _inside(path, r["path"]) or _inside(r["path"], path)]
        made = _made(path, mine, born)

        filled = [r for r in mine if r["how"] in ("copy", "move") and _inside(r["path"], path)
                  and not (born and r["at"] < born - BORN_SLACK_SEC)]
        kind = (facts or {}).get("kind") if (facts or {}).get("exists", True) else ""
        kinds.append(kind or "")
        name, folder = where(path, place)
        noun = f"the {kind} {name}" if kind else name
        if made is not None:
            maker = bot_name(made["bot_id"])
            makers.append(maker)

            lines.append(f"{maker} made {'a ' + kind if kind else noun} in {folder}{on(place)} {when(born)}.")
        elif born:
            lines.append(f"{noun[:1].upper() + noun[1:]} in {folder}{on(place)} was made {when(born)}.")
        else:
            lines.append(f"{noun[:1].upper() + noun[1:]} is in {folder}{on(place)}.")
        moves = [r for r in filled if r["how"] == "move"]
        if moves:
            moved = True
            src = _source_folder([s for r in moves for s in r["sources"]], place)
            lines.append(f"{bot_name(moves[-1]['bot_id'])} moved files{' from ' + src if src else ''} into it, "
                         "so they would be deleted too.")
        copied_from += [s for r in filled if r["how"] == "copy" for s in r["sources"]]
        contents = _contents(facts)
        if contents:
            lines.append(contents)
    one = len(paths) == 1
    ours = bool(makers) and len(makers) == len(paths)

    if any(_inside(s.rstrip("/"), q) for s in copied_from for q in paths):
        copied_from = []
    copies = ours and one and bool(copied_from) and not moved and not any(_home_spelled_out(p) for p in paths) \
        and (looked[0] or {}).get("files") is not None and _originals_there(paths[0], copied_from, place, originals)
    who = (makers[0] if len(set(makers)) == 1 else "your Zips") if ours else ""
    if ours and one:
        title = f"Delete the copy {who} made" if copies else f"Delete the {kinds[0] or 'item'} {who} made"
    elif ours:
        title = f"Delete {len(paths)} things {who} made"
    elif one:
        name, folder = where(paths[0], place)
        title = f"Delete {name} from {folder}{on(place)}"
    else:
        title = f"Delete {len(paths)} things{on(place)}"
    safe = ""
    if copies:
        src = _source_folder(copied_from, place)
        whose = "Your files" if place == MAC else "The files"
        safe = f"{whose} in {src} stay as they are." if src else "The original files stay as they are."
    if not one:
        button = "Delete All"
    elif copies:
        button = "Delete Copy"
    else:
        button = {"folder": "Delete Folder", "file": "Delete File"}.get(kinds[0], "Delete")
    return {"title": title, "affects": lines, "safe": safe, "button": button}


_PRODUCTION = re.compile(r"^(?:Publish (.+) to production on (?:Netlify|Vercel)|Deploy (.+) with (?:Cloudflare|Railway)|"
                         r"Deploy (.+))$")
_NAMED_PEOPLE = re.compile(r"^(?:To|Cc): (.+)$", re.M)
_PERSON = re.compile(r"\s*\"?([^\",<]+?)\"?\s*<([^>]+)>")


def send_words(action: tiers.Action) -> dict:
    ""
    title = action.summary
    for line in _NAMED_PEOPLE.findall(action.inputs or ""):
        for name, addr in _PERSON.findall(line):
            if name.strip() and addr.strip() in title:
                title = title.replace(addr.strip(), name.strip(), 1)
    title = re.sub(r"\"([^\"]+)\"", "“\\1”", title)
    if title.startswith(("Invite ", "Send the guests an update")):


        many = "," in (action.target or "")
        if title.startswith("Invite "):
            return {"title": title, "affects": ["Google emails each guest an invitation from your account." if many
                                                else "Google emails them an invitation from your account."],
                    "icons": ["calendar", "envelope"], "button": "Send Invites" if many else "Send Invite"}
        return {"title": title, "affects": ["Google emails the event's guests about the change."],
                "icons": ["calendar", "envelope"], "button": "Send Update"}
    email = title.startswith("Email ") or "Subject: " in (action.inputs or "")
    if title.startswith(("Upload ", "Send data ")):
        return {"title": title, "button": "Upload" if title.startswith("Upload ") else "Send Data"}
    return {"title": title, "button": "Send Email" if email else "Send Message" if action.target else "Send"}


_JOB_SITE = re.compile(r"\b(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{2,24})\b", re.I)
_NOT_A_SITE = {"txt", "md", "py", "sh", "js", "ts", "json", "log", "csv", "yml", "yaml", "png", "jpg", "pdf", "html"}


_JOB_QUIET = re.compile(r"\[?silent\]?|\bonly (?:tell|message|notify|alert|ping|post|let)\b|\bonly (?:if|when)\b",
                        re.I)


def job_words(job: dict, text_only: bool) -> dict:
    ""


    name = str(job.get("name") or "")
    prompt = str(job.get("prompt") or "")
    when = str(job.get("when") or "")
    facts, icons = [], ["repeat"]
    if when:
        facts.append(when[:1].upper() + when[1:])
        icons.append("clock")
    sites = []
    for m in _JOB_SITE.finditer(prompt):
        host = m.group(1).lower()
        if host.rsplit(".", 1)[-1] not in _NOT_A_SITE and host not in sites:
            sites.append(host)
    if sites and not text_only:
        facts.append("Checks " + " and ".join(sites[:2]))
        icons.append("globe")
    facts.append("Tells you only when something needs you" if _JOB_QUIET.search(prompt)
                 else "Posts here each time it runs")
    icons.append("bell")
    if name:
        title = name if job.get("creating") else f"Change {name}"
    else:
        title = "A new scheduled job" if job.get("creating") else "Change a scheduled job"
    return {"title": title, "affects": facts, "icons": icons, "button": "Allow"}


def words(action: tiers.Action, tool_name: str, bot: str, bot_name, place_inspect=None) -> dict:
    ""

    kind, summary = action.kind, action.summary
    if kind == "delete" and action.extra.get("paths") and place_inspect is not None:
        place, inspect, originals = place_inspect
        return delete_words(list(action.extra["paths"]), place, bot_name, inspect, originals)
    if kind == "send":
        return send_words(action)
    if kind == "write" and action.extra.get("calendar_events"):
        events = action.extra["calendar_events"]
        return {"title": summary, "affects": events, "icons": ["calendar"] * (len(events) + 1),
                "button": "Add Events" if len(events) > 1 else "Add Event"}
    if action.extra.get("job"):
        return job_words(action.extra["job"], bool(action.extra.get("text_only")))
    if action.extra.get("claude"):
        folder = action.extra["claude"]
        return {"title": f"Have Claude Code work in {folder.rsplit('/', 1)[-1]}",
                "affects": [f"Claude Code can change files and run commands in {folder} on your Mac.",
                            "It can't use the internet, your accounts or other folders, and it stops after 10 "
                            "minutes."],
                "button": "Start"}
    who = bot_name(bot)
    m = re.match(r"^Click \"(.+)\" on (\S+)$", summary)
    if m and kind in ("book", "submit", "publish", "delete"):
        label, site = m.group(1), m.group(2)
        title = {"book": f"Make a booking on {site}", "submit": f"Send a form on {site}",
                 "publish": f"Post on {site}", "delete": f"Delete something on {site}"}[kind]
        return {"title": title, "affects": [f"{who} will press “{label}” on the page."],
                "button": {"book": "Book", "submit": "Submit", "publish": "Post", "delete": "Delete"}[kind]}
    if kind == "publish" and action.extra.get("x_post"):
        reply = action.extra["x_post"]
        affects = [f"This replies to {reply}." if isinstance(reply, str) else
                   "Anyone who can see your X account will see it."]
        return {"title": summary, "affects": affects, "button": "Post"}
    if kind == "publish":
        m = _PRODUCTION.match(summary)
        if m:
            name = next(g for g in m.groups() if g)
            if action.extra.get("hub_consumes_approval"):
                return {"title": f"Put the new version of {name} online",
                        "affects": ["I will publish these sites: " +
                                    ", ".join(action.extra.get("domains", [])) + "."], "button": "Publish"}
            return {"title": f"Put the new version of {name} online",
                    "affects": [f"Everyone who visits {name} will see this version."], "button": "Publish"}
        return {"title": _yours(summary), "button": "Publish"}
    title = _yours(summary)
    button = {"book": "Book", "submit": "Submit", "delete": "Delete"}.get(kind, "")
    return {"title": title if title != summary else "", "button": button}


def _yours(title: str) -> str:
    ""
    return title.replace(" on the Mac", " on your Mac")
