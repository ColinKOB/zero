""

















import contextvars
import glob
import hashlib
import json
import os
import posixpath
import re
import shlex
from dataclasses import dataclass, field

from . import safe_files

TIERS = ("act", "tell", "ask", "never")


def _consent():
    ""
    from . import consent
    return consent
RANK = {t: i for i, t in enumerate(TIERS)}




ASK_KINDS = {"send", "submit", "publish", "delete", "book", "account", "mac", "system", "schedule"}


NEVER_KINDS = {"pay", "secret", "school", "agent", "hardline"}


RULE_KINDS = {"signin", "send", "write", "submit", "book"}













HARD_STOP_KINDS = {"send", "submit", "publish", "delete", "book", "account", "schedule", "system"}





DELETE_FLAGS = re.compile(r"delet|\brm\b|remov|drop|truncat|wipe|eras|shred|mkfs|format|\bdd\b|git clean|"
                          r"reset --hard|force push|overwrit|uninstall", re.I)


def _flagged_action(flagged: str, shown: str, inputs: str) -> "Action":
    ""
    kind = "delete" if hard_stops() and DELETE_FLAGS.search(flagged or "") else "system"
    return Action(kind, _consent().plain_command(flagged, shown), inputs=inputs)


def hard_stops() -> bool:
    ""
    try:
        from . import home
        return bool(home.get("permissions.hard_stops"))
    except Exception:
        return False


SECURITY = re.compile(r"password|passcode|2-step|two-factor|2fa|mfa|security (settings|key|question)|recovery (code|email|phone)", re.I)


@dataclass
class Action:
    kind: str

    summary: str
    target: str = ""
    site: str = ""
    inputs: str = ""
    scope: str = ""
    why_never: str = ""
    details: str = ""
    extra: dict = field(default_factory=dict)

    def describe(self) -> str:
        ""
        more = " ".join((self.extra.get("request") or "").split())[:200]
        return f"{self.summary} ({more})" if more and more not in self.summary else self.summary

    def fingerprint(self, tool_name: str, args: dict) -> str:
        ""

        try:
            blob = json.dumps({"tool": tool_name, "args": args, "file": self.extra.get("content_hash", "")},
                              sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            blob = f"{tool_name}:{args}"
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


def floor(kind: str, site_class: str = "ordinary", context: str = "chat", mode: str | None = None,
          text: str = "", saved: bool = False) -> str:
    ""




    if kind in NEVER_KINDS:
        return "never"
    if kind == "account" and SECURITY.search(text or ""):
        return "never"
    if kind == "signin" and site_class != "sensitive" and mode != "ask" and hard_stops() and \
            context not in ("prep", "quiet"):


        return "act" if mode == "anytime" else "tell"
    if kind == "signin" and context in ("team", "scheduled", "prep", "quiet") and site_class != "sensitive" \
            and mode != "ask" and (saved or mode == "anytime"):
        return "act" if mode == "anytime" else "tell"
    if kind == "signin" and context == "team" and site_class != "sensitive" and mode != "ask":
        return "never"
    if kind in ASK_KINDS and (kind in HARD_STOP_KINDS or not hard_stops()):
        return "ask"
    if kind in ASK_KINDS:
        return "tell"
    if kind == "signin" and (site_class == "sensitive" or mode == "ask" or
                             (context in ("scheduled", "team") and mode != "anytime")):
        return "ask"
    if kind == "write":
        return "tell"
    return "act"




_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PROD_BRANCH = re.compile(r"^(?:\+)?(?:HEAD:)?(?:refs/heads/)?(main|master|prod|production|release|live|gh-pages|trunk)$", re.I)



_SAFE_ROOTS = ("/tmp/", "/var/tmp/", "/private/tmp/", "/private/var/folders/", "~/taffy/work/")


MAC = "mac"


def _on_mac(where: str) -> bool:
    return where in (MAC, "Colin's Mac")


def _work_root() -> str:
    ""
    try:
        from . import home
        work = str(home.get("paths.work") or "~/taffy/work").rstrip("/")
    except Exception:
        work = "~/taffy/work"
    if not work.startswith("~/"):
        work = re.sub(r"^/(?:home|Users)/[^/]+/", "~/", work)
    return work + "/"


CONTAINER_ROOTS = ("/root/", "/workspace/")
EXTRA_SAFE_ROOTS: contextvars.ContextVar[tuple] = contextvars.ContextVar("taffy_extra_safe_roots", default=())



RESOLVE_LINKS: contextvars.ContextVar[bool] = contextvars.ContextVar("taffy_resolve_links", default=False)

_REGENERABLE = {"node_modules", "dist", "build", ".next", "out", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "coverage", ".coverage", "htmlcov", ".cache", ".parcel-cache", ".turbo", "target",
                ".venv", "venv", ".svelte-kit", ".nuxt", ".expo"}
_HOME = os.path.expanduser("~")


_CMD_START = r"(^|[\s;&|(`$/])"
_SEND_CMDS = [
    (re.compile(r"\bhimalaya\b[^|;&]*\b(message|template)\s+(send|reply|forward|write)\b"), "email"),
    (re.compile(_CMD_START + r"(sendmail|msmtp|mutt|neomutt|mailx|ssmtp|swaks)\b"), "email"),
    (re.compile(_CMD_START + r"mail\s+(-[a-zA-Z]+\s+)*-s\b"), "email"),
    (re.compile(r"\bsmtplib\b|\bSMTP(_SSL)?\s*\("), "email"),
    (re.compile(r"api\.(sendgrid\.com|mailgun\.net|resend\.com|postmarkapp\.com|brevo\.com|sparkpost\.com)"), "email"),
    (re.compile(r"gmail\.googleapis\.com/[^\s'\"]*/(send|drafts/send)"), "email"),

    (re.compile(_CMD_START + r"zipmail[-_]send\b|\bzipmail\.heyzero\.app\b", re.I), "email"),
    (re.compile(r"api\.twilio\.com|messages\.create\("), "text"),
    (re.compile(r"api\.telegram\.org/bot[^\s'\"]*/send|hooks\.slack\.com/|discord(app)?\.com/api/webhooks/|slack\.com/api/chat\.postMessage"),
     "chat"),


    (re.compile(r"\bosascript\b.*\b(send|deliver)\b", re.S), "mac"),
    (re.compile(r"\bgh\s+(issue|pr)\s+comment\b|\bgh\s+pr\s+review\b"), "github"),
]

_RUNNER = r"(?:(?:npx|bunx|pnpm\s+dlx|yarn\s+dlx)\s+(?:-y\s+|--yes\s+)?)?"
_NETLIFY = r"\b" + _RUNNER + r"(?:netlify(?:-cli)?(?:@[\w.^~-]+)?|ntl)\s+deploy\b"
_PUBLISH_CMDS = [
    (re.compile(r"\bgh\s+pr\s+merge\b"), "Merge a pull request on GitHub"),
    (re.compile(r"\bgh\s+release\s+create\b"), "Publish a GitHub release"),
    (re.compile(r"\bgh\s+issue\s+create\b"), "Open a public issue on GitHub"),
    (re.compile(r"\bgh\s+pr\s+create\b"), "Open a pull request on GitHub"),
    (re.compile(_NETLIFY + r"[^|;&]*\s(--prod\b|-p\b|--prodIfUnlocked\b)"), "Publish the site to production on Netlify"),
    (re.compile(r"\bvercel\b[^|;&]*\s--prod\b"), "Publish the site to production on Vercel"),
    (re.compile(r"\bwrangler\s+(deploy|publish|pages\s+deploy)\b"), "Deploy with Cloudflare"),
    (re.compile(r"\brailway(?:/cli)?(?:@[\w.^~-]+)?\s+(up|deploy|redeploy)\b"), "Deploy with Railway"),


    (re.compile(r"\brailway(?:/cli)?(?:@[\w.^~-]+)?\s+(?:variables?|vars?)\s+set\b"), "Change a setting on Railway"),
    (re.compile(r"\brailway(?:/cli)?(?:@[\w.^~-]+)?\s+restart\b"), "Restart a service on Railway"),
    (re.compile(r"\b(firebase|fly|flyctl)\s+deploy\b|\bheroku\s+(releases|container:release)\b"), "Deploy an app"),
    (re.compile(r"\b(npm|pnpm|yarn)\s+(npm\s+)?publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgem\s+push\b"), "Publish a package"),
    (re.compile(r"\bdocker\s+push\b"), "Push a container image"),
    (re.compile(r"\b(eas\s+submit|fastlane\s+(deliver|pilot|release|upload_to_app_store)|xcrun\s+altool\s+--upload)\b"), "Upload an app for release"),

    (re.compile(r"\bgh\s+workflow\s+run\b"), "Start a GitHub workflow, which can deploy"),
    (re.compile(r"\bgh\s+gist\s+create\b[^|;&\n]*\s(--public|-p)\b"), "Post a public gist on GitHub"),
    (re.compile(r"\bgh\s+repo\s+create\b[^|;&\n]*\s--public\b"), "Create a public GitHub repository"),
    (re.compile(r"\bcloudflared\b[^|;&\n]*\btunnel\b[^|;&\n]*(\s--url\b|\srun\b|\s--hello-world\b)"),
     "Put a local service on the internet with Cloudflare"),
    (re.compile(r"\btailscale\s+funnel\b"), "Put a local service on the internet with Tailscale"),
    (re.compile(r"\bngrok\s+(http|tcp|tls|start)\b|\b(localtunnel|lt)\s+--port\b"),
     "Put a local service on the internet"),
    (re.compile(r"\bpastebinit\b|\b(0x0\.st|termbin\.com|transfer\.sh|paste\.rs|ix\.io|dpaste\.\w+|sprunge\.us)\b"),
     "Post a file publicly"),

    (re.compile(r"api\.netlify\.com/build_hooks/"), "Start a production build on Netlify"),
    (re.compile(r"\b(?:netlify|ntl)(?:-cli)?\s+api\s+(?:createSiteDeploy|restoreSiteDeploy|rollbackSiteDeploy|"
                r"createSiteBuild|updateSiteDeploy|unlockDeploy)\b"), "Publish the site on Netlify"),
    (re.compile(r"api\.vercel\.com/v\d+/integrations/deploy/"), "Start a deploy on Vercel"),
    (re.compile(r"api\.cloudflare\.com/client/v4/[^\s'\"]*/pages/webhooks/deploy_hooks/"), "Start a deploy on Cloudflare"),
    (re.compile(r"api\.render\.com/deploy/"), "Start a deploy on Render"),
]


_NETLIFY_API = re.compile(r"api\.netlify\.com/api/v1/(?:sites/[^\s'\"/]+/(?:deploys|builds)|deploys/[^\s'\"/]+/restore)\b")
_HTTP_WRITE = re.compile(r"(?:-X|--request)[\s=]*['\"]?(?:POST|PUT|PATCH)\b|\s(?:-d|-F|-T)\b|--data\b|--data-\w+|--form\b|"
                         r"--upload-file\b|\bhttp(?:ie)?\s+(?:POST|PUT|PATCH)\b|\brequests\.(?:post|put|patch)\b|"
                         r"method\s*[:=]\s*['\"](?:POST|PUT|PATCH)", re.I)


_NAMED_PUBLISH = {"Publish the site to production on Netlify": "Publish {name} to production on Netlify",
                  "Publish the site to production on Vercel": "Publish {name} to production on Vercel",
                  "Deploy with Cloudflare": "Deploy {name} with Cloudflare",
                  "Deploy with Railway": "Deploy {name} with Railway",
                  "Deploy an app": "Deploy {name}"}
_TARGET_FLAG = re.compile(r"(?:^|\s)(--site|--project-name|--project|--app|--service|--cwd|--name|-a|-s)(?:=|\s+)"
                          r"(['\"]?)([^\s'\";&|<>]+)\2")


def _deploy_target(cmd: str, cwd: str | None, pat: re.Pattern) -> tuple[str, str]:
    ""




    start = _norm(cwd, None) if cwd else None
    for seg, here in _segments(cmd, cwd):
        if pat.search(seg):
            m = _TARGET_FLAG.search(seg)
            if m and m.group(1) == "--cwd":
                where = _norm(m.group(3), here) or m.group(3)
                return where, where
            if m:
                return m.group(3), m.group(3)
            n = re.sub(r"^/(?:home|Users)/[^/]+/taffy/work/", "~/taffy/work/", (here or "").rstrip("/") + "/")
            project = re.match(r"^(?:~/taffy/work/[^/]+|/tmp|/private/tmp)/[^/]+/", n) is not None
            moved = _norm(here, None) != start
            return here or "", here if here and (moved or project) else ""
    return cwd or "", ""


_SQL_CLIENT = (r"(?<![\w-])(?:psql|mysql|mariadb|sqlite3|sqlite-utils|pgcli|mycli|litecli|usql|duckdb|mongosh|sqlcmd|"
               r"clickhouse(?:-client)?|cockroach\s+sql|dbshell|dbconsole|rails\s+db|heroku\s+pg:psql|"
               r"(?:fly|flyctl)\s+(?:postgres|pg|mpg)\s+connect|turso\s+db\s+shell|wrangler\s+d1\s+execute|"
               r"prisma\s+db\s+execute|supabase\s+db\s+(?:execute|query))(?![\w-])")
_DELETE_CMDS = [
    (re.compile(r"\bgh\s+repo\s+delete\b"), "Delete a GitHub repository"),
    (re.compile(r"\bnetlify\s+(sites:delete|api\s+deleteSite)\b"), "Delete a Netlify site"),
    (re.compile(r"\brailway(?:/cli)?(?:@[\w.^~-]+)?\s+down\b"), "Take down a Railway deployment"),
    (re.compile(r"\brailway(?:/cli)?(?:@[\w.^~-]+)?\s+(?:variables?|vars?)\s+(?:delete|rm|remove)\b"),
     "Delete a setting on Railway"),
    (re.compile(r"\bhimalaya\b[^|;&]*\bmessage\s+delete\b"), "Delete email"),
    (re.compile(r"(^|[\s;&|(])(trash|trash-put|gio\s+trash)\s"), "Move files to the trash"),
    (re.compile(r"(^|[\s;&|(])crontab\s+(-\w*\s+)*-\w*r\b"), "Remove every job this computer runs on a schedule"),
    (re.compile(r"\bgh\s+release\s+delete\b"), "Delete a GitHub release"),
    (re.compile(r"(^|[\s;&|(/])dropdb\b"), "Delete a database"),


    (re.compile(_SQL_CLIENT + r"[^|;&]*\b(?i:drop\s+(table|database|schema)|delete\s+from|truncate)\b"),
     "Delete data from a database"),
    (re.compile(r"\bredis-cli\b[^|;&]*\b(?i:flushall|flushdb|del|unlink)\b"), "Delete data from a database"),


    (re.compile(r"(?<![\w-])(?:mongosh|mongo)(?![\w-])[^|;&]*\.(?:drop|dropDatabase|dropCollection|deleteMany|deleteOne|"
                r"remove|findOneAndDelete|findAndModify)\s*\("), "Delete data from a database"),
    (re.compile(r"(?s)\b(?:pymongo|MongoClient|motor)\b.*?\.(?:drop_database|drop_collection|delete_many|delete_one|"
                r"find_one_and_delete)\s*\(|\b(?:pymongo|MongoClient|motor)\b.*?\.drop\s*\(\s*\)"),
     "Delete data from a database"),
    (re.compile(r"\bdocker\s+(volume\s+(rm|prune)|system\s+prune\b[^|;&]*(--volumes|-a\b))"), "Delete Docker data"),
]
_ACCOUNT_CMDS = [
    (re.compile(r"\btailscale\s+serve\b(?![^|;&\n]*\bstatus\b)"), "Share a local service on your tailnet"),
    (re.compile(r"\bgh\s+repo\s+edit\b[^|;&]*--visibility\b"), "Change a repository's visibility"),
    (re.compile(r"\bnetlify\s+(dns|domains)\b|\bwrangler\s+dns\b|/zones/[^\s]*/dns_records"), "Change DNS records"),
]


_ADMIN_MERGE = re.compile(r"\bgh\s+pr\s+merge\b[^|;&]*\s--admin\b")
ADMIN_MERGE_NEVER = ("Zips never merge with --admin, because it skips the repo's checks. Merge without it. If a check "
                     "failed, tell Colin in a line which one and why.")

_PAY_CMDS = [
    (re.compile(r"\bstripe\s+(refunds|charges|payouts|transfers|payment_intents)\s+(create|confirm|capture)\b"), "Move money with Stripe"),
    (re.compile(r"api\.stripe\.com/v1/(refunds|charges|payouts|transfers|payment_intents)"), "Move money with Stripe"),
]
_PIPE_TO_SHELL = re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")


def _words(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _norm(path: str, cwd: str | None) -> str | None:
    ""

    p = (path or "").strip()
    for var, to in (("${HOME}", "~"), ("$HOME", "~"), ("${TMPDIR}", "/tmp"), ("$TMPDIR", "/tmp")):
        if p == var or p.startswith(var + "/"):
            p = to + p[len(var):]
    if p == _HOME or p.startswith(_HOME + "/"):
        p = "~" + p[len(_HOME):]
    if p.startswith("$") or p.startswith("~") and not (p == "~" or p.startswith("~/")):
        return None
    if not p.startswith(("/", "~")):
        if cwd is None:
            return None
        p = cwd.rstrip("/") + "/" + p
        if p == _HOME or p.startswith(_HOME + "/"):
            p = "~" + p[len(_HOME):]
    if p.startswith("~"):
        return "~" + posixpath.normpath("/" + p[1:].lstrip("/")).rstrip("/") + ("/" if p.endswith("/") else "")
    return posixpath.normpath(p)


def _roots() -> tuple:
    work = _work_root()
    return tuple(r for r in _SAFE_ROOTS if r != "~/taffy/work/") + (work,) + EXTRA_SAFE_ROOTS.get()


def _safe(norm: str | None, follow: bool = True) -> bool:
    ""



    if norm is None or "$" in norm or "`" in norm:
        return False
    n = _home_form(norm.rstrip("/") + "/")
    roots = _roots()
    return any(n.startswith(r) and n != r for r in roots) and \
        (not RESOLVE_LINKS.get() or _leads_inside(os.path.expanduser(n), roots, follow))


def _home_form(p: str) -> str:
    ""
    p = re.sub(r"^/(?:home|Users)/[^/]+/taffy/work/", "~/taffy/work/", p)
    work = _work_root()
    if work != "~/taffy/work/":
        p = re.sub(r"^/(?:home|Users)/[^/]+/" + re.escape(work[2:]), work, p)
    return p


def _leads_inside(full: str, roots: tuple, follow: bool) -> bool:
    ""


    real_roots = [os.path.realpath(os.path.expanduser(r.rstrip("/"))) for r in roots]
    full = full.rstrip("/") or "/"
    for p in (glob.glob(full) if re.search(r"[*?\[]", full) else []) or [full]:
        head, name = os.path.split(p)
        real = os.path.join(os.path.realpath(head), name) if not follow and name not in ("", ".", "..") \
            else os.path.realpath(p)
        if not any(real.startswith(r + os.sep) for r in real_roots):
            return False
    return True


def _spelled(path: str, cwd: str | None) -> str | None:
    ""

    p = (path or "").strip()
    for var, to in (("${HOME}", "~"), ("$HOME", "~"), ("${TMPDIR}", "/tmp"), ("$TMPDIR", "/tmp")):
        if p == var or p.startswith(var + "/"):
            p = to + p[len(var):]
    if p.startswith("$") or p.startswith("~") and not (p == "~" or p.startswith("~/")):
        return None
    if not p.startswith(("/", "~")):
        if cwd is None:
            return None
        p = cwd.rstrip("/") + "/" + p
    return os.path.expanduser(_home_form(p))


def _escapes(path: str, cwd: str | None, follow: bool = True) -> bool:
    ""
    if not RESOLVE_LINKS.get():
        return False
    full = _spelled(path, cwd)
    return full is None or not _leads_inside(full, _roots(), follow)




_BRACES = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")


_LINKS = re.compile(r"(?:^|[\s;&|(`])(?:sudo\s+)?(?:\S*/)?(?:ln\s+(?:-\S+\s+)*(?:-\w*s\w*|--symbolic)\b|"
                    r"cp\s+(?:-\S+\s+)*-\w*s\w*\b|mount\s+[^;&|\n]*--r?bind\b|bindfs\b)")


def _risky(path: str, cwd: str | None, follows_last: bool = False) -> bool:
    ""

    if not path or path.startswith("-"):
        return False
    if _BRACES.search(path):
        return True


    follow = follows_last or path.endswith("/") or path.rsplit("/", 1)[-1] in (".", "..")
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if not path.startswith(("/", "~", "$")) and ".." not in path.split("/") and name in _REGENERABLE:
        full = _spelled(path, cwd) if follow and RESOLVE_LINKS.get() else None
        if full is None or not os.path.islink(full.rstrip("/")):
            return False
    return not _safe(_norm(path, cwd), follow=False) or _escapes(path, cwd, follow)


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)

_SHELL_C = re.compile(r"""(?<![\w/-])(?:\S*/)?(?:ba|z|da|k|fi)?sh\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*c[a-zA-Z]*\s+"""
                      r"""(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|(\S+))""", re.S)
_EVAL = re.compile(r"""(?<![\w-])eval\s+(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""", re.S)



_WRAPPERS = {"command": ((), 0), "builtin": ((), 0), "exec": (("-a",), 0), "nohup": ((), 0), "setsid": ((), 0),
             "time": ((), 0), "then": ((), 0), "do": ((), 0), "else": ((), 0), "elif": ((), 0), "!": ((), 0),
             "{": ((), 0), "unbuffer": ((), 0), "chronic": ((), 0), "doas": (("-u",), 0),
             "sudo": (("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"), 0), "env": (("-u", "-C", "-S"), 0),
             "nice": (("-n",), 0), "ionice": (("-c", "-n", "-p"), 0), "stdbuf": ((), 0),
             "timeout": (("-s", "-k", "--signal", "--kill-after"), 1), "watch": (("-n", "-d"), 0),
             "xargs": (("-I", "-n", "-P", "-L", "-d", "-a", "-E", "-s"), 0), "flock": (("-w", "-E"), 1),
             "runuser": (("-u", "-g"), 0), "caffeinate": (("-t", "-w"), 0), "pkexec": (("--user",), 0),
             "run0": (("-u", "--user", "-D", "--chdir"), 0),
             "ssh": (("-i", "-p", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-E", "-e", "-m", "-O", "-Q",
                      "-S", "-W", "-w", "-B"), 1)}


def _unwrap(cmd: str) -> str:
    ""

    def script(m):
        single, double, bare = (m.groups() + (None,))[:3]
        text = single if single is not None else (re.sub(r'\\(["\\$`])', r"\1", double) if double is not None else bare)
        return " ; " + text + " ; "
    for _ in range(3):
        new = _EVAL.sub(script, _SHELL_C.sub(script, cmd))
        if new == cmd:
            break
        cmd = new
    return cmd


def _scan(cmd: str, pipes: bool = True) -> tuple[list[str], list[str]] | None:
    ""



    parts, subs, stack, starts, buf, i, n = [], [], [], [], [], 0, len(cmd)

    def cut():
        if "".join(buf).strip():
            parts.append("".join(buf))
        buf.clear()
    while i < n:
        ch, top = cmd[i], (stack[-1] if stack else None)
        if ch == "\\" and top != "'":
            buf.append(cmd[i:i + 2])
            i += 2
            continue
        if top == '"':
            if ch == '"':
                stack.pop()
            elif cmd.startswith("$(", i) or ch == "`":
                stack.append("(" if ch == "$" else "`")
                starts.append(i + (2 if ch == "$" else 1))
                buf.append(cmd[i:starts[-1]])
                i = starts[-1]
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            end = cmd.find("'", i + 1)
            if end < 0:
                return None
            buf.append(cmd[i:end + 1])
            i = end + 1
            continue
        if ch == '"':
            stack.append('"')
        elif cmd.startswith("$(", i) or (ch == "`" and top != "`"):
            stack.append("(" if ch == "$" else "`")
            starts.append(i + (2 if ch == "$" else 1))
            buf.append(cmd[i:starts[-1]])
            i = starts[-1]
            continue
        elif (ch == ")" and top == "(") or (ch == "`" and top == "`"):
            stack.pop()
            subs.append(cmd[starts.pop():i])
        elif ch == "#" and top is None and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            end = cmd.find("\n", i)
            i = n if end < 0 else end
            continue
        elif top is None and (cmd.startswith("&&", i) or cmd.startswith("||", i)):
            cut()
            i += 2
            continue
        elif top is None and (ch in ";\n" or ch == "|" and pipes or ch == "&" and cmd[i - 1:i] not in ("<", ">", "&")
                              and cmd[i + 1:i + 2] not in (">", "&")):
            cut()
            i += 1
            continue
        buf.append(ch)
        i += 1
    if stack:
        return None
    cut()
    return parts, subs


def _split(cmd: str, pipes: bool = True) -> list[str]:
    ""
    scanned = _scan(cmd, pipes)
    if scanned is not None:
        return scanned[0]
    sep = r"&&|\|\||[;\n|]|(?<![<>&])&(?![>&])" if pipes else r"&&|\|\||[;\n]|(?<![<>&|])&(?![>&|])"
    return [p for p in re.split(sep, cmd) if p.strip()]


def _strip_wrappers(words: list[str], names: list | None = None) -> list[str]:
    ""

    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_]\w*=", w):
            i += 1
            continue
        name = w.rsplit("/", 1)[-1]
        if name not in _WRAPPERS or name == "command" and words[i + 1:i + 2] in (["-v"], ["-V"]):
            break
        if names is not None:
            names.append(name)
        takes, positional = _WRAPPERS[name]
        i += 1
        while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
            flag = words[i]
            i += 1
            if flag == "--":
                break
            if flag in takes and i < len(words):
                i += 1
        while i < len(words) and re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
        i += min(positional, max(0, len(words) - i - 1))
    return words[i:]


def _segments(cmd: str, cwd: str | None) -> list[tuple[str, str | None]]:
    ""


    out = []
    outer = []
    for seg in _split(_unwrap(_HEREDOC.sub("", cmd))):
        s = seg.strip()
        while s.startswith("("):
            outer.append(cwd)
            s = s[1:].lstrip()
        closes = 0
        while s.endswith(")") and s.count(")") > s.count("("):
            s = s[:-1].rstrip()
            closes += 1
        words = _words(s)
        inner = _strip_wrappers(words)
        if inner is not words and 0 < len(inner) < len(words):

            kept = [w for w in words[:len(words) - len(inner)] if _GIT_ENV.match(w)] \
                if inner[0].rsplit("/", 1)[-1] == "git" else []
            s = shlex.join(kept + inner)
        m = re.match(r"^(cd|pushd|popd)(?:\s+(.+))?$", s)
        if m:





            ws = _words(m.group(2) or "~") or ["~"]
            plain_cd = m.group(1) != "popd" and len(ws) == 1 and not ws[0].startswith(("-", "+"))
            cwd = _norm(ws[0], cwd) if plain_cd else None
        elif s:
            out.append((s, cwd))
        for _ in range(closes):
            cwd = outer.pop() if outer else None
    return out


_FIND_TESTS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex", "-type",
               "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-size", "-maxdepth", "-mindepth", "-newer",
               "-user", "-group", "-perm"}
_FIND_FLAGS = {"-delete", "-print", "-print0", "-empty", "-prune", "-depth", "-xdev", "-mount", "-o", "-a", "-or",
               "-and", "-not", "!", "(", ")"}


def _plain_find(words: list[str]) -> bool:
    ""


    i = 0
    while i < len(words):
        w = words[i]
        if w in _FIND_TESTS:
            i += 2
        elif w in _FIND_FLAGS:
            i += 1
        elif w in ("-exec", "-execdir"):
            end = next((j for j in range(i + 1, len(words)) if words[j] in (";", "\\;", "+")), None)
            run = words[i + 1:end] if end is not None else []
            if not run or run[0].rsplit("/", 1)[-1] != "rm" or run[-1] != "{}" or \
                    not all(re.fullmatch(r"-[rfdv]+", a) for a in run[1:-1]):
                return False
            i = end + 1
        else:
            return False
    return True


_RSYNC_SHORT = set("avrlptgoDzhncuPiq")
_RSYNC_LONG = {"--archive", "--verbose", "--recursive", "--links", "--perms", "--times", "--group", "--owner",
               "--devices", "--specials", "--compress", "--human-readable", "--dry-run", "--checksum", "--update",
               "--progress", "--partial", "--itemize-changes", "--quiet", "--stats", "--delete", "--delete-before",
               "--delete-during", "--delete-delay", "--delete-after", "--delete-excluded", "--exclude", "--include"}


def _plain_rsync(words: list[str]) -> bool:
    ""


    skip = False
    for w in words:
        if skip:
            skip = False
        elif w.startswith("--"):
            name = w.split("=", 1)[0]
            if name not in _RSYNC_LONG:
                return False
            skip = name in ("--exclude", "--include") and "=" not in w
        elif w.startswith("-") and len(w) > 1 and not set(w[1:]) <= _RSYNC_SHORT:
            return False
    return True


def _shell_delete(cmd: str, cwd: str | None) -> Action | None:
    ""


    found, operands, plain = [], [], True
    linked = _LINKS.search(_unwrap(_HEREDOC.sub("", cmd))) is not None
    loops = {m.group(1): _words(m.group(2)) for m in re.finditer(r"\bfor\s+(\w+)\s+in\s+([^;\n]+?)\s*(?:;|\n)\s*do\b",
                                                                   _unwrap(cmd))}
    for seg, here in _segments(cmd, cwd):
        m = re.match(r"^(?:sudo\s+(?:-\S+\s+)*)?(?:command\s+)?(?:\S*/)?(rm|unlink|shred)\s+(.*)$", seg, re.S)
        if m:


            words, paths, ended = _plain_words(_words(re.sub(r"\$\([^()]*\)|`[^`]*`", "$(...)", m.group(2)))), [], False
            for w in words:
                if (w.count("'") + w.count('"')) % 2:
                    w = re.sub(r"""['")\]]+$""", "", w)
                if w == "--" and not ended:
                    ended = True
                elif ended or not w.startswith("-"):
                    paths += loops.get(re.sub(r"^\$\{?(\w+)\}?$", r"\1", w), [w]) if w.startswith("$") else [w]
            risky = [("a path another command works out" if "$(...)" in p else _norm(p, here) or p)
                     for p in paths if _risky(p, here, m.group(1) == "shred") or linked]


            operands += [("a path another command works out" if "$(...)" in p or ".." in p.split("/") else
                          _norm(p, here) or p) for p in paths]
            if risky:
                found.append(Action("delete", f"Delete {', '.join(risky)[:120]}", target=", ".join(risky)[:200],
                                    inputs=cmd))
            continue
        plain = False
        m = re.match(r"^(?:sudo\s+)?find\s+(.*)$", seg, re.S)
        if m and re.search(r"\s-delete\b|-exec(?:dir)?\s+(?:\S*/)?(rm|shred|unlink)\b", seg):
            roots, words = [], _plain_words(_words(m.group(1)))

            follows = "-L" in words or "-follow" in words

            while words and (words[0] in ("-H", "-L", "-P") or re.fullmatch(r"-O\d*", words[0]) or words[0] == "-D"):
                words = words[2:] if words[0] == "-D" else words[1:]
            for w in words:
                if w.startswith(("-", "(", "!", ")")):
                    break
                roots.append(w)
            follows = follows or not _plain_find(words[len(roots):])
            risky = [r for r in roots or ["."] if linked or follows or _BRACES.search(r) or not _safe(_norm(r, here)) or
                     _escapes(r, here)]
            if risky:
                found.append(Action("delete", f"Delete files found in {', '.join(risky)[:120]}",
                                    target=", ".join(risky)[:200], inputs=cmd))
            continue
        git = _git_call(_words(seg), here)
        if git is not None and git.sub == "clean":
            flags = [w for w in _plain_words(git.args) if w.startswith("-")]
            short = "".join(w.lstrip("-") for w in flags if not w.startswith("--"))
            if "n" in short or "--dry-run" in flags or not ("f" in short or "--force" in flags):
                continue
            if linked or not all(_safe(f) for f in git.folders):
                found.append(Action("delete", "Delete untracked files with git clean",
                                    target=git.tree or "the current folder", inputs=cmd))
            continue
        if re.match(r"^rsync\b", seg) and re.search(r"\s--delete", seg):


            words, operands = _plain_words(_words(seg))[1:], []
            for i, w in enumerate(words):
                if not w.startswith("-") and not (i and words[i - 1] in ("--exclude", "--include")):
                    operands.append(w)
            dest = (operands or [""])[-1]
            remote = re.match(r"^[\w.@-]+:", dest) is not None
            if remote or linked or not _plain_rsync(_plain_words(_words(seg))[1:]) or _BRACES.search(dest) or \
                    not _safe(_norm(dest, here)) or _escapes(dest, here):
                found.append(Action("delete", f"Delete files in {dest[:120]} that are not in the copy",
                                    target=dest[:200], inputs=cmd))
    if re.search(r"\bxargs\s+(?:-\S+\s+)*(?:\S*/)?rm\b", cmd):
        roots = [r for s, h in _segments(cmd, cwd) for r in (_find_roots(s, h) or [])]
        if not roots or not all(_safe(r) for r in roots):
            found.append(Action("delete", "Delete a list of files", target="files listed by another command",
                                inputs=cmd))
    a = _combine(found, cmd)
    if a is not None and len(found) == 1 and plain and operands and \
            "a path another command works out" not in operands:


        a.extra["paths"] = list(dict.fromkeys(operands))
    return a


def _find_roots(seg: str, cwd: str | None) -> list | None:
    m = re.match(r"^find\s+(.*)$", seg, re.S)
    if not m:
        return None
    roots = []
    for w in _plain_words(_words(m.group(1))):
        if w.startswith(("-", "(", "!", ")")):
            break
        roots.append(_norm(w, cwd))
    return roots or [_norm(".", cwd)]


_PY_DELETE = re.compile(r"\b(?:shutil\.)?rmtree\s*\(\s*([^,)]*)|\bos\.(?:remove|unlink|removedirs)\s*\(\s*([^,)]*)|"
                        r"\bPath\s*\(\s*([^)]*)\)\s*\.unlink\s*\(|\bsend2trash\s*\(\s*([^,)]*)|\.unlink\s*\(\s*\)")
_PY_LITERAL = re.compile(r"""^(?:os\.path\.expanduser\s*\(\s*)?[rRuU]?[fF]?(['"])(.*?)\1""")
_PY_HOME = re.compile(r"Path\.home\s*\(|expanduser\s*\(|environ\s*(\[|\.get\s*\()\s*['\"]HOME|getenv\s*\(\s*['\"]HOME")


def _code_delete(code: str, cwd: str | None) -> Action | None:
    ""

    paths, unknown, complete = [], False, True
    for m in _PY_DELETE.finditer(code):
        group = next((i for i, g in enumerate(m.groups(), 1) if g is not None), 0)
        arg = (m.group(group) if group else "").strip()
        lit = _PY_LITERAL.match(arg)
        if lit:
            paths.append(lit.group(2).split("{")[0])



            after = code[m.end(group):] if group else ""
            closes = r"\)\s*[,)]" if arg.startswith("os.path.expanduser") else r"[,)]"
            whole = arg[lit.end():].strip() == "" and re.match(r"\s*" + closes, after) is not None
            if not whole or "{" in lit.group(2) or ".." in lit.group(2).split("/"):
                complete = False
        else:
            unknown = True

    linked = re.search(r"\bsymlink(?:_to)?\s*\(|\bln\s+-\w*s", code) is not None
    risky = [p for p in paths if linked or _risky(p, cwd)]
    unknown = unknown or any(unsure and re.match(r"^(?:sudo\s+)?(?:\S*/)?(?:rm|unlink|shred)\b", text)
                             for text, unsure in _code_commands(code))
    if unknown and not risky:
        literals = [m.group(2) for m in re.finditer(r"""(['"])((?:/|~/)[^'"\n]*)\1""", code)]
        if linked or _PY_HOME.search(code) or any(not _safe(_norm(x, cwd)) for x in literals):


            return _unnamed_delete(code)
    if not risky:
        return None



    if not complete or unknown or linked:
        return _unnamed_delete(code, ", ".join(risky)[:200])
    named = [f"{posixpath.basename(p.rstrip('/')) or p} in {posixpath.dirname(p.rstrip('/')) or p}" for p in risky]
    a = Action("delete", f"Delete {', '.join(named)[:120]}", target=", ".join(risky)[:200], inputs=code)
    if not unknown and not linked:
        a.extra["paths"] = list(dict.fromkeys(_norm(p, cwd) or p for p in paths))
    return a





UNNAMED_DELETE = "Delete something"
UNNAMED_DELETE_LINE = "The command works out what to delete, so I can't name it. Look at the command before you say yes."


def _unnamed_delete(code: str, target: str = "") -> Action:
    return Action("delete", UNNAMED_DELETE, target=target or "a path the command works out",
                  inputs=UNNAMED_DELETE_LINE, details=code)


def _gh_apis(cmd: str) -> list[Action]:
    ""
    return [a for m in re.finditer(r"\bgh\s+api\b([^|;&\n]*)", cmd) for a in [_gh_api(m.group(1), cmd)] if a]


def _gh_api(rest: str, cmd: str) -> Action | None:
    ""
    meth = re.search(r"(?:-X|--method)[\s=]*['\"]?(\w+)", rest)
    method = meth.group(1).upper() if meth else ("POST" if re.search(r"\s(-f|-F|--field|--raw-field|--input)\b", rest)
                                                 else "GET")
    if method == "GET":
        return None
    if method == "DELETE":
        return Action("delete", "Delete something on GitHub", inputs=cmd)
    return Action("publish", "Post or change something on GitHub", inputs=cmd)








_GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix",
                   "--attr-source", "--list-cmds"}

_SH_WORD = r"""(?:[^\s'"|;&<>()`]|"[^"]*"|'[^']*')+"""
_GIT_OPTS = (r"(?:\s+(?:-[cC]\s+" + _SH_WORD + r"|--(?:git-dir|work-tree|namespace|config-env|super-prefix|attr-source|"
             r"list-cmds)(?:=|\s+)" + _SH_WORD + r"|--exec-path(?:=" + _SH_WORD + r")?|--[a-z][\w-]*|-[pPhv]))*")


_GIT_PUSH = re.compile(r"\bgit" + _GIT_OPTS + r"\s+push\b((?:[<>]&|&>|[^|;&\n])*)")


_REDIRECT = re.compile(r"^(?:\d+|&)?(?:>>|>\||>&|>|<<<|<&|<>|<)(.*)$", re.S)

_PUSH_VALUE_FLAGS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}

_GIT_ENV = re.compile(r"^GIT_(?:DIR|WORK_TREE|NAMESPACE|CONFIG\w*|EXEC_PATH)=")

_GIT_BUILTINS = {"push", "reset", "clean", "branch", "tag", "remote", "commit", "checkout", "switch", "restore", "fetch",
                 "pull", "merge", "rebase", "status", "log", "diff", "add", "rm", "mv", "stash", "show", "config", "init",
                 "clone", "worktree", "submodule", "gc", "prune", "reflog", "update-ref", "filter-branch", "cherry-pick",
                 "revert", "am", "apply", "bisect", "grep", "describe", "rev-parse", "ls-files", "ls-remote", "help"}

_GIT_FAMILIES = ("delete", "push", "workspace")

_GIT_RUNS = re.compile(r"^(?:core\.(?:sshcommand|pager|editor|askpass|fsmonitor)|sequence\.editor|diff\.external|"
                       r"pager\.[\w-]+|gpg\.(?:[\w-]+\.)?program)$")


def _git_files() -> list[str]:
    ""
    return ["/etc/gitconfig", os.path.join(_HOME, ".config", "git", "config"), os.path.join(_HOME, ".gitconfig")]


@dataclass
class _Git:
    ""

    sub: str
    args: list
    where: str | None
    repo: str | None
    folders: list
    config: list
    unread: bool = False
    options: bool = False
    alias: bool = False
    shell: str = ""
    tree: str | None = None

    @property
    def plain(self) -> str:
        ""
        return shlex.join(["git", self.sub, *self.args])


def _git_key(key: str) -> str:
    ""
    first, _, rest = key.partition(".")
    middle, _, last = rest.rpartition(".")
    return ".".join(p for p in (first.lower(), middle, last.lower()) if p) if rest else first.lower()


def _git_call(words: list[str], here: str | None) -> _Git | None:
    ""
    env, i = {}, 0
    while i < len(words) and re.match(r"^[A-Za-z_]\w*=", words[i]):
        name, _, value = words[i].partition("=")
        env[name] = value
        i += 1
    if i >= len(words) or words[i].rsplit("/", 1)[-1] != "git":
        return None
    i += 1
    options = any(k.startswith("GIT_") for k in env)
    where, git_dir, work_tree, config = here, env.get("GIT_DIR"), env.get("GIT_WORK_TREE"), []
    unread = any(k.startswith("GIT_CONFIG") for k in env)
    while i < len(words) and words[i].startswith("-") and words[i] != "-":
        options = True
        name, eq, value = words[i].partition("=")
        if name in _GIT_VALUE_OPTS and not eq:
            name, value = words[i], (words[i + 1] if i + 1 < len(words) else "")
            i += 2
        else:
            i += 1
        if name == "-C":
            if value:
                where = _norm(value, where)
        elif name == "--git-dir":
            git_dir = value
        elif name == "--work-tree":
            work_tree = value
        elif name == "-c":
            key, has, val = value.partition("=")
            config.append((_git_key(key), val if has else "true"))
        elif name == "--config-env":
            config.append((_git_key(value.partition("=")[0]), None))
    if i >= len(words):
        return None
    given_dir, given_tree = git_dir, work_tree
    git_dir = _norm(git_dir, where) if git_dir else None
    work_tree = _norm(work_tree, where) if work_tree else None


    folders = list(dict.fromkeys([git_dir or where, work_tree or where]))
    g = _Git(words[i], list(words[i + 1:]), where, git_dir or where, folders, config, unread, options,
             tree=work_tree or where)
    if given_dir and git_dir is None or given_tree and work_tree is None:
        g.folders.append(None)



    for _ in range(5):
        if g.sub in _GIT_BUILTINS or g.sub.startswith("-"):
            break
        value = _git_setting(f"alias.{g.sub.lower()}", g)
        if value and value.startswith("!"):
            g.shell, g.alias = " ".join([value[1:], shlex.join(g.args)]).strip(), True
            break
        expanded = _words(value or "")
        if not expanded:
            break
        g.sub, g.args, g.alias = expanded[0], expanded[1:] + g.args, True
    return g


def _git_safe(words: list[str], here: str | None) -> bool:
    ""

    g = _git_call(words, here)
    return all(_safe(f) for f in g.folders) if g is not None else _safe(here)


def _git_option_forms(cmd: str, cwd: str | None, where: str, detect=None, depth: int = 0) -> list[Action]:
    ""



    out = []
    for seg, here in _segments(cmd, cwd):
        g = _git_call(_words(seg), here)
        if g is None or not g.options and not g.alias:
            continue
        runs = [v for k, v in g.config if v and (_GIT_RUNS.match(k) or k.startswith("credential.") and v.startswith("!"))]
        for text in runs + ([g.shell] if g.shell else []):
            a = classify_command(text.lstrip("!"), where, here, _depth=depth + 1) if depth < 2 else None
            if a is not None:
                a.inputs, a.details = cmd, ""
                out.append(a)
        check = detect or hermes_like
        found = check(g.plain)
        if not found or found in _ARTIFACTS or found == check(seg):
            continue
        family = _family(found)
        if family in _GIT_FAMILIES and not _judged(seg, here, family, cmd):
            out.append(_flagged_action(found, g.plain, cmd))
    return out


def _git_layers(repo: str | None, git: _Git | None = None) -> list[tuple[str, str | None]] | None:
    ""


    if git is not None and git.unread:
        return None
    entries = []
    for path in _git_files():
        got = _read_git_config(path)
        if got is None:
            return None
        entries += got
    path = _repo_config_path(repo)
    if path is None:
        return None
    if path:
        got = _read_git_config(path)
        if got is None:
            return None
        entries += got
    for key, value in (git.config if git is not None else []):
        if key.startswith("include"):
            return None
        entries.append((key, value))
    return entries


def _git_setting(key: str, git: _Git | None) -> str | None:
    ""
    entries = _git_layers(git.repo if git is not None else None, git)
    values = [v for k, v in entries or [] if k == key]
    return values[-1] if values else None


def _repo_config_path(repo: str | None) -> str | None:
    ""


    if not repo:
        return ""
    folder = _HOME + repo[1:] if repo.startswith("~") else repo
    for _ in range(40):
        dot = os.path.join(folder, ".git")
        if os.path.isdir(dot):
            return os.path.join(dot, "config") if os.path.isfile(os.path.join(dot, "config")) else ""
        if os.path.exists(dot):
            return None
        if all(os.path.exists(os.path.join(folder, n)) for n in ("config", "HEAD", "objects")):
            return os.path.join(folder, "config")
        parent = os.path.dirname(folder)
        if parent == folder or not parent:
            break
        folder = parent
    return ""


def _read_git_config(path: str, depth: int = 0) -> list[tuple[str, str | None]] | None:
    ""


    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(256 * 1024)
    except FileNotFoundError:
        return []
    except OSError:
        return None
    entries = _parse_git_config(text)
    if entries is None or depth > 8:
        return None
    out = []
    for key, value in entries:
        if key.startswith("includeif."):
            return None
        if key == "include.path":
            if not value:
                return None
            inc = os.path.join(os.path.dirname(path), _HOME + value[1:] if value.startswith("~/") else value)

            got = _read_git_config(inc, depth + 1) if os.path.isfile(inc) else None
            if got is None:
                return None
            out += got
            continue
        out.append((key, value))
    return out


def _parse_git_config(text: str) -> list[tuple[str, str]] | None:
    ""

    out, section, lines, i = [], None, text.splitlines(), 0
    while i < len(lines):
        s = lines[i].strip()
        i += 1
        if s.startswith("["):
            m = re.match(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\](.*)$', s)
            if not m:
                return None
            name, sub, s = m.group(1), m.group(2), m.group(3).strip()
            if sub is not None:
                section = name.lower() + "." + re.sub(r"\\(.)", r"\1", sub)
            else:
                first, _, rest = name.partition(".")
                section = first.lower() + ("." + rest.lower() if rest else "")
        if not s or s[0] in "#;":
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*(?:=(.*))?$", s)
        if not m or section is None:
            return None
        raw = m.group(2)
        if raw is None:
            out.append((section + "." + m.group(1).lower(), "true"))
            continue
        while (len(raw) - len(raw.rstrip("\\"))) % 2 == 1 and i < len(lines):
            raw = raw[:-1] + lines[i]
            i += 1
        value = _git_value(raw)
        if value is None:
            return None
        out.append((section + "." + m.group(1).lower(), value))
    return out


def _git_value(raw: str) -> str | None:
    ""
    out, quoted, pending, i = [], False, "", 0
    raw = raw.strip()
    while i < len(raw):
        ch = raw[i]
        i += 1
        if ch == "\\":
            if i >= len(raw):
                return None
            esc = raw[i]
            i += 1
            if esc not in 'n"t\\b':
                return None
            out.append(pending + {"n": "\n", "t": "\t", "b": "\b"}.get(esc, esc))
            pending = ""
        elif ch == '"':
            out.append(pending)
            pending = ""
            quoted = not quoted
        elif ch in "#;" and not quoted:
            break
        elif ch.isspace() and not quoted:
            pending += ch
        else:
            out.append(pending + ch)
            pending = ""
    return None if quoted else "".join(out)


def _push_urls(remote: str, repo: str | None, git: _Git | None = None) -> tuple[list[str], list[str]] | None:
    ""





    entries = _git_layers(repo, git)
    if entries is None:
        return None
    if any(v is None for k, v in entries if k.startswith(("remote.", "url.", "alias."))):
        return None
    named = bool(remote) and re.fullmatch(r"[\w.-]+", remote) is not None
    if named and any(k.startswith(f"remote.{remote}.") for k, _ in entries):
        pushurls = [v for k, v in entries if k == f"remote.{remote}.pushurl"]
        urls = [v for k, v in entries if k == f"remote.{remote}.url"]
        specs = [v for k, v in entries if k == f"remote.{remote}.push"]
        if "" in pushurls + urls or not (pushurls or urls):
            return None
    elif named and not remote.endswith(".git"):
        return None
    elif not remote:
        return None
    else:
        pushurls, urls, specs = [], [remote], []
    rewrites = {"insteadof": [], "pushinsteadof": []}
    for k, v in entries:
        m = re.match(r"^url\.(.+)\.(insteadof|pushinsteadof)$", k)
        if m:
            rewrites[m.group(2)].append((v, m.group(1)))

    def rewrite(url: str, table: list) -> str | None:
        fits = [(len(prefix), base) for prefix, base in table if prefix and url.startswith(prefix)]
        if not fits:
            return url
        longest = max(n for n, _ in fits)
        bases = {b for n, b in fits if n == longest}
        return bases.pop() + url[longest:] if len(bases) == 1 else None

    if pushurls:
        dests = [rewrite(u, rewrites["insteadof"]) for u in pushurls]
    else:
        pushed = [rewrite(u, rewrites["pushinsteadof"]) for u in urls]
        if any(p != u for p, u in zip(pushed, urls)):


            moved = [p for p, u in zip(pushed, urls) if p != u]
            dests = moved + [rewrite(p, rewrites["insteadof"]) if p else None for p in moved]
        else:
            dests = [rewrite(u, rewrites["insteadof"]) for u in urls]
    if not dests or any(d is None for d in dests):
        return None
    return list(dict.fromkeys(dests)), specs


def _url_folder(url: str, repo: str | None) -> str | None:
    ""
    if url.startswith("file://"):
        path = url[len("file://"):]
    elif "://" in url or re.match(r"^[^/]*:", url):
        return None
    else:
        path = url
    return _norm(path, repo) or path


def _local_remote(remote: str, repo: str | None, git: _Git | None = None) -> str | None:
    ""

    return _folder_of(_push_urls(remote, repo, git), repo)


def _folder_of(got: tuple | None, repo: str | None) -> str | None:
    ""
    if got is None:
        return None
    folders = [_url_folder(u, repo) for u in got[0]]
    if any(f is None for f in folders):
        return None
    return next((f for f in folders if not _safe(f)), folders[0])


def _mapped(ref: str, specs: list[str]) -> list[str] | None:
    ""

    out = []
    for spec in specs:
        s = spec.lstrip("+")
        if not s or s.startswith("^") or s == ":":
            continue
        src, has, dst = s.partition(":")
        dst = dst if has else src
        for name in ([ref] if ref.startswith("refs/") else [ref, "refs/heads/" + ref]):
            if "*" in src:
                head, _, tail = src.partition("*")
                if name.startswith(head) and name.endswith(tail) and len(name) >= len(head) + len(tail):
                    out.append(dst.replace("*", name[len(head):len(name) - len(tail)]))
                    break
            elif name == src:
                out.append(dst)
                break
    return [re.sub(r"^refs/heads/", "", d) or "HEAD" for d in out] or None


def _plain_words(words: list[str]) -> list[str]:
    ""


    out, skip = [], False
    for w in words:
        if skip:
            skip = False
            continue
        m = _REDIRECT.match(w)
        if m:
            skip = not m.group(1)
            continue
        out.append(w)
    return out


def _git_push(cmd: str, cwd: str | None = None) -> Action | None:
    ""
    found = _git_pushes(cmd, cwd, keep_local=True)
    return found[0] if found else None


def _git_pushes(cmd: str, cwd: str | None = None, keep_local: bool = False) -> list[Action]:
    ""



    try:
        segs = _segments(cmd, cwd)
    except Exception:
        segs = []
    places = [(seg, here) for seg, here in segs if _GIT_PUSH.search(seg)]
    found = []
    for i, m in enumerate(_GIT_PUSH.finditer(cmd)):
        seg, here = places[i] if i < len(places) else (m.group(0), cwd)
        g = _git_call(_words(seg), here) or _git_call(_words(m.group(0)), here)
        found.append(_push(m.group(1), cmd, g.repo if g is not None else here, g))
    for seg, here in segs:
        g = _git_call(_words(seg), here)
        if g is not None and g.alias and g.sub == "push" and not _GIT_PUSH.search(seg):
            found.append(_push(shlex.join(g.args), cmd, g.repo, g))
    for a in found:
        a.extra["push"] = True
    return [a for a in found if keep_local or not a.extra.get("scratch")]


def _push(args: str, cmd: str, repo: str | None = None, git: _Git | None = None) -> Action:
    ""




    words = _plain_words([w for w in (re.sub(r"""['")\]]+$""", "", w) for w in _words(args)) if w])
    flags, rest, skip = [], [], False
    for w in words:
        if skip:
            skip = False
        elif w.startswith("-"):
            flags.append(w)
            skip = w in _PUSH_VALUE_FLAGS
        else:
            rest.append(w)
    if "--delete" in flags or "-d" in flags or any(w.startswith(":") for w in rest[1:]):
        return Action("delete", "Delete a branch on GitHub", target=" ".join(rest[1:]) or "a branch", inputs=cmd)
    if any(f in flags for f in ("--all", "--mirror", "--tags")):
        return Action("publish", "Push every branch or tag", target="all branches", inputs=cmd)
    refs = rest[1:]
    if not refs:
        return Action("publish", "Push code without naming the branch", target="the current branch", inputs=cmd,
                      extra={"hint": "Name the branch (git push origin <branch>): pushing a branch that does not "
                                     "deploy needs no card."})
    got = _push_urls(rest[0], repo, git)
    dests = []
    for r in refs:
        mapped = _mapped(r, got[1]) if got is not None and got[1] and ":" not in r else None
        dests += mapped or [r.split(":")[-1]]
    dests = list(dict.fromkeys(dests))
    folder = _folder_of(got, repo)
    if folder is not None:


        if _safe(folder):
            return Action("write", f"Push {', '.join(dests)} to a folder in your work folder ({folder})",
                          target=", ".join(dests), inputs=cmd, extra={"scratch": True})
        where = f"to a folder on this computer ({folder})"
        if any(_PROD_BRANCH.match(d) for d in dests) or "HEAD" in dests:
            return Action("publish", f"Push {', '.join(dests)} {where}", target=", ".join(dests), inputs=cmd)
        return Action("write", f"Push the branch {', '.join(dests)} {where}", target=", ".join(dests), inputs=cmd)
    if any(_PROD_BRANCH.match(d) for d in dests) or "HEAD" in dests:
        return Action("publish", f"Push to {', '.join(dests)}, which can publish it", target=", ".join(dests), inputs=cmd)
    return Action("write", f"Push to the branch {', '.join(dests)}", target=", ".join(dests), inputs=cmd)


def _own_addresses() -> set[str]:
    ""
    try:
        from . import home
        out = {str(a.get("address") or "").lower() for a in home.mail_accounts()}
        for c in home.calendars():
            out |= {str(c.get(k) or "").lower() for k in ("email", "account", "address", "calendar_id")}
        return {a for a in out if "@" in a}
    except Exception:
        return set()


def _recipients(text: str) -> list[str]:
    to = []
    for line in re.findall(r"(?im)^\s*(?:to|cc|bcc)\s*:\s*(.+)$", text):
        to += _EMAIL.findall(line)
    return list(dict.fromkeys(to or _EMAIL.findall(text)))[:10]


def _subject(text: str) -> str:
    m = re.search(r"(?im)^\s*subject\s*:\s*(.+)$", text)
    return m.group(1).strip()[:160] if m else ""


def _message_file(cmd: str, cwd: str | None) -> tuple[str, str] | None:
    ""

    m = re.search(r"(?<!<)<\s*(['\"]?)([^\s;&|'\"<>]+)\1", cmd) or re.search(r"\bcat\s+(['\"]?)([^\s;&|'\"]+)\1\s*\|", cmd)
    if not m:
        return None
    n = _norm(m.group(2), cwd)
    if n is None:
        return None
    path = _HOME + n[1:] if n.startswith("~") else n

    data = safe_files.card_text(path, 256 * 1024)
    if data is None:
        return "", "unreadable"
    return data.decode("utf-8", "replace"), hashlib.sha256(data).hexdigest()


def _send_summary(channel: str, to: list[str], subj: str) -> str:
    about = f" about \"{subj}\"" if subj else ""
    who = ", ".join(to)
    if channel == "email":
        return (f"Email {who}" if who else "Send an email") + about
    return {"text": "Send a text message" + (f" to {who}" if who else ""),
            "chat": "Send a chat message",
            "mac": "Send a message from the Mac's Mail or Messages" + (f" to {who}" if who else ""),
            "github": "Comment publicly on GitHub"}.get(channel, "Send a message")









_AGENTS = {"claude": "claude", "claude-code": "claude", "codex": "codex", "hermes": "hermes", "taffy": "hermes",
           "hermes-agent": "hermes-agent", "hermes-acp": "hermes-agent", "hermes_cli": "hermes", "run_agent": "hermes-agent",


           "taffy-hub": "hub", "taffy_hub": "hub", "zero-home-create": "hub"}

_AGENT_READ_ONLY = {"claude": {"--version", "-v", "--help", "-h"}, "codex": {"--version", "-V", "--help", "-h"},
                    "hermes": {"--version", "-V", "version", "--help", "-h", "help", "status", "logs", "insights",
                               "usage", "kanban"},
                    "hermes-agent": set()}
_HERMES_SETTINGS = {"plugins", "plugin", "config", "tools", "toolsets", "mcp", "auth", "login", "logout", "secrets",
                    "vault", "cron", "gateway", "approvals", "security", "hooks", "profile", "profiles", "setup",
                    "model", "fallback", "egress", "skills", "update", "uninstall", "pairing", "backup", "import",
                    "migrate", "curator", "webhook", "computer-use", "browser", "memory", "dashboard"}
_RUNNERS = {"npx", "bunx", "uvx", "node", "deno", "bun", "python", "python3", "pipx", "uv", "pnpm", "yarn", "npm"}
_HERMES_IMPORT = re.compile(r"\b(?:from|import)\s+(?:hermes_cli|run_agent)\b|\bAIAgent\s*\(")
_HUB_IMPORT = re.compile(r"\b(?:from|import)\s+taffy_hub\b|\b(?:new|redeem)_pairing_code\b|\bpairing_codes\b")
_WHICH_SUB = re.compile(r"(?:\$\(|`)\s*(?:which|command\s+-v|type\s+-[pP]|whereis)\s+([\w./~-]+)\s*(?:\)|`)")

_WHICH_AT_START = re.compile(r"(?:^|[;&|\n(]|\b(?:then|do|else|exec|nohup|sudo|command|time)\s)\s*"
                             r"(?:[A-Za-z_]\w*=\S*\s+)*" + _WHICH_SUB.pattern)

AGENT_NEVER = ("Starting another assistant from your terminal (claude, codex or Hermes) is off for Zips, because it "
               "would act outside Colin's permissions. Use your own tools instead: the connectors tool for Gmail, "
               "Google Calendar, Google Drive and Trello, and your terminal, browser and files for the rest. To hand "
               "coding work in a folder under ~/Projects on Colin's Mac to Claude Code, use mac_claude. When your own "
               "For a job in an app on his Mac, mac_codex runs Terra in the background. It isn't for coding.")
HUB_NEVER = ("The hub's own command line and its address are off for Zips: a pairing code or a device key from there "
             "would let a Zip answer its own cards. Colin pairs devices himself. Use your own tools, and tell Colin "
             "in one sentence what you wanted to do.")
SETTINGS_NEVER = ("Changing the Zips' own plugins, settings, tools, sign-ins or scheduled jobs from the terminal is off "
                  "for Zips. Colin does that himself. Use your cronjob tool for scheduled jobs, and tell Colin in one "
                  "sentence what else you would change.")


def _agent_of(word: str) -> str | None:
    ""

    w = word.strip("'\"")
    if re.search(r"/claude/versions/[^/]+$", w):
        return "claude"

    pkg = re.search(r"(?:^|/)(@anthropic-ai/claude-code|@openai/codex|hermes[-_]agent|hermes_cli|taffy_hub)(?:/|$)", w)
    if pkg:
        return {"@anthropic-ai/claude-code": "claude", "@openai/codex": "codex", "hermes_cli": "hermes",
                "taffy_hub": "hub"}.get(pkg.group(1), "hermes-agent")
    name = w.rsplit("/", 1)[-1].split("@", 1)[0] if not w.startswith("@") else w.split("/", 1)[-1].split("@", 1)[0]
    if re.match(r"^[\w.-]+:[\w.-]+$", name) and "/" in w:
        name = name.split(":", 1)[0]
    name = re.sub(r"\.(js|mjs|cjs|py|sh)$", "", name.split(".", 1)[0] if name.startswith(("hermes_cli.", "taffy_hub."))
                  else name)
    if re.search(r"[*?\[]", name):

        import fnmatch
        return next((a for n, a in _AGENTS.items() if fnmatch.fnmatchcase(n, name)), None)
    return _AGENTS.get(name)


def _after_options(args: list[str], takes: str = "", long_takes: tuple = ()) -> list[str]:
    ""

    i = 0
    while i < len(args) and args[i].startswith("-") and len(args[i]) > 1:
        flag = args[i]
        i += 1
        if flag == "--":
            break
        if flag.startswith("--"):
            if "=" not in flag and flag in long_takes:
                i += 1
        elif flag[-1] in takes:
            i += 1
    return args[i:]


_TMUX_RUNS = {"new", "new-session", "neww", "new-window", "splitw", "split-window", "respawnp", "respawn-pane",
              "respawnw", "respawn-window", "run", "run-shell", "popup", "display-popup"}
_SYSTEMD_RUN_TAKES = ("--unit", "--property", "--description", "--setenv", "--uid", "--gid", "--working-directory",
                      "--machine", "--host", "--slice", "--nice", "--on-active", "--on-boot", "--on-calendar",
                      "--timer-property", "--path-property", "--socket-property", "--service-type", "--expand-environment")


def _launched(words: list[str]) -> list[list[str]]:
    ""



    if not words:
        return []
    head, rest = words[0].rsplit("/", 1)[-1], words[1:]
    if head in ("sh", "bash", "zsh", "dash", "ksh", "fish", "su", "runuser", "script"):
        for i, w in enumerate(rest):
            if (w in ("--command",) or re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w)) and i + 1 < len(rest):
                return [_words(rest[i + 1])]
            if w.startswith("--command="):
                return [_words(w.split("=", 1)[1])]
        if head == "script":

            pos = [w for w in rest if not w.startswith("-")]
            return [pos[1:]] if len(pos) > 1 else []
        return []
    if head == "env":
        for i, w in enumerate(rest):
            if w in ("-S", "--split-string") and i + 1 < len(rest):
                return [_words(rest[i + 1]) + rest[i + 2:]]
            if w.startswith("--split-string="):
                return [_words(w.split("=", 1)[1]) + rest[i + 1:]]
        return []
    if head == "tmux":
        rest = _after_options(rest, "LSfT")
        if not rest:
            return []
        sub, args = rest[0], rest[1:]
        if sub in ("send-keys", "send"):
            return [_words(a) for a in _after_options(args, "tNcX")]
        return [_after_options(args, "snctxyeFlpwTfdh" if sub in ("popup", "display-popup") else "snctxyeFlpwTf")] \
            if sub in _TMUX_RUNS else []
    if head == "screen":
        if "-X" in rest:
            i = rest.index("-X")
            return [_words(a) for a in rest[i + 2:]] if rest[i + 1:i + 2] == ["stuff"] else []
        return [_after_options(rest, "SceEhpTtl")]
    if head == "systemd-run":
        return [_after_options(rest, "puEMH", _SYSTEMD_RUN_TAKES)]
    if head in ("dtach", "abduco"):
        rest = rest[2:] if rest[:1] and rest[0].startswith("-") else rest
        return [_after_options(rest, "erz")]
    if head == "parallel":
        cmd = _after_options(rest, "jSNnL", ("--jobs", "--sshlogin", "--joblog", "--max-args", "--delimiter"))
        return [cmd[:cmd.index(":::")] if ":::" in cmd else cmd]
    if head == "find":

        out, i = [], 0
        while i < len(rest):
            if rest[i] in ("-exec", "-execdir", "-ok", "-okdir"):
                j = i + 1
                while j < len(rest) and rest[j] not in (";", "\\;", "+"):
                    j += 1
                out.append(rest[i + 1:j])
                i = j
            i += 1
        return out
    if head in ("docker", "podman", "nerdctl"):

        args = _after_options(rest, "", ("--context", "-c", "--host", "-H", "--config", "--log-level", "-l"))
        sub, args = (args[0], args[1:]) if args else ("", [])
        takes = "evpuwlhmc" if sub == "run" else "euw"
        long_takes = ("--name", "--env", "--env-file", "--volume", "--mount", "--network", "--entrypoint", "--user",
                      "--workdir", "--platform", "--label", "--publish", "--hostname", "--memory", "--cpus",
                      "--restart", "--pull", "--add-host", "--device", "--cap-add", "--cap-drop", "--security-opt")
        if sub in ("run", "create", "exec"):
            args = _after_options(args, takes, long_takes)
            if not args:
                return []
            if sub == "exec":
                return [args[1:]]
            entry = next((rest[i + 1] for i, w in enumerate(rest[:-1]) if w == "--entrypoint"), None)
            return [args[:1]] + ([[entry] + args[1:]] if entry else []) + [args[1:]]
        return []
    if head in ("flatpak-spawn", "chroot", "nsenter", "unshare", "firejail", "bwrap", "proot"):
        return [rest[i:] for i in range(len(rest)) if not rest[i].startswith("-")][:3]
    return []


def _agent_words(words: list[str], depth: int = 0) -> tuple[str, list[str]] | None:
    ""

    if depth < 3 and words and words[0].rsplit("/", 1)[-1] == "env" and any(w.startswith(("-S", "--split")) for w in words):
        for inner in _launched(words):
            found = _agent_words(inner, depth + 1)
            if found:
                return found
    words = _strip_wrappers(words)
    if words and re.search(r"\s", words[0].strip()):

        words = _strip_wrappers(_words(words[0]) + words[1:])
    for _ in range(3):
        if not words:
            return None
        head = words[0].rsplit("/", 1)[-1]
        if head in ("sh", "bash", "zsh", "dash", "ksh") and not _launched(words):
            words = _after_options(words[1:])
            continue
        if head in _RUNNERS or re.match(r"^python3(\.\d+)?$", head):
            rest = words[1:]
            if head in ("pnpm", "yarn") and rest[:1] == ["dlx"] or head in ("uv", "pipx") and rest[:1] == ["run"] or \
                    head in ("npm", "pnpm", "yarn") and rest[:1] in (["exec"], ["x"]):
                rest = rest[1:]
                for i, w in enumerate(rest[:-1]):
                    if w in ("-c", "--call"):
                        rest = _words(rest[i + 1])
                        break
            elif head in ("npm", "pnpm", "yarn"):
                break
            if rest[:1] == ["--"]:
                rest = rest[1:]
            if rest[:1] == ["-m"] and len(rest) > 1:
                rest = rest[1:]
            rest = [w for w in rest if w not in ("-y", "--yes", "-q", "--quiet")]
            words = rest
            continue
        break
    agent = _agent_of(words[0]) if words else None
    if agent:
        return agent, words[1:]
    if depth < 3:
        for inner in _launched(words):
            found = _agent_words(inner, depth + 1)
            if found:
                return found
    return None


def _without_redirects(words: list[str]) -> list[str]:
    ""
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
        elif re.match(r"^(?:\d*|&)(?:>>?|<)(?:&\d+|&-)?$", w):
            skip = not re.search(r"&(?:\d+|-)$", w)
        elif not re.match(r"^(?:\d*|&)(?:>>?|<)", w):
            out.append(w)
    return out


def _agent_action(agent: str, args: list[str], cmd: str) -> Action | None:
    ""
    args = _without_redirects(args)
    if agent == "hub":
        return Action("agent", "Use the hub's command line from the terminal", inputs=cmd, why_never=HUB_NEVER)
    if agent == "hermes" and [a for a in args if not a.startswith("-")][:2] in (["cron", "list"], ["cron", "status"]):
        return None
    if agent == "hermes" and any(a in _HERMES_SETTINGS for a in args):
        return Action("agent", "Change the Zips' own settings from the terminal", inputs=cmd, why_never=SETTINGS_NEVER)
    allowed = _AGENT_READ_ONLY.get(agent, set())
    sub = next((a for a in args if not a.startswith("-")), "")
    if args and (sub in allowed if agent == "hermes" and sub else all(a in allowed for a in args)):
        return None
    name = {"hermes-agent": "Hermes"}.get(agent, agent.capitalize())
    return Action("agent", f"Start {name} from the terminal", inputs=cmd, why_never=AGENT_NEVER)




_SPAWN_CALL = re.compile(
    r"(?:(?<![\w.])|(?<=subprocess\.)|(?<=\bos\.)|(?<=\bpty\.)|(?<=pexpect\.)|(?<=asyncio\.))"
    r"(?:run|call|check_call|check_output|getoutput|getstatusoutput|Popen|system|popen|exec[lv]p?e?|spawn[lv]p?e?|"
    r"posix_spawnp?|spawn|create_subprocess_exec|create_subprocess_shell|execSync|execFileSync|spawnSync|execFile)"
    r"\s*\(|(?:child_process|\bcp|childProcess|\)|Bun)\.(?:exec|execSync|execFile|execFileSync|spawn|spawnSync)\s*\(")
_PY_STRING = r"""[rRbBuUfF]{0,2}(['"])((?:(?!\1)[^\\\n]|\\.)*)\1"""


def _bracketed(text: str, start: int) -> str:
    ""
    close = {"[": "]", "(": ")"}[text[start]]
    depth, i, quote = 0, start, ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == text[start]:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return text[start + 1:]


def _assigned(code: str, name: str) -> str | None:
    ""
    found = re.findall(rf"(?m)^[ \t]*(?:const\s+|let\s+|var\s+)?{re.escape(name)}\s*(?::\s*[\w\[\], ]+)?=\s*(.+)$", code)
    return found[-1].strip() if found else None


_ITEM = re.compile(r"""(?:shutil\.)?which\s*\(\s*(['"])([^'"]+)\1\s*\)|[rRbBuUfF]{0,2}(['"])((?:(?!\3)[^\\\n]|\\.)*)\3|"""
                   r"""([A-Za-z_][\w.]*)""")


def _items(text: str, code: str) -> list[str]:
    ""

    out = []
    for m in _ITEM.finditer(text):
        if m.group(2) is not None:
            out.append(m.group(2))
        elif m.group(4) is not None:
            out.append(m.group(4))
        else:
            value = _assigned(code, m.group(5))
            lit = re.fullmatch(_PY_STRING, value or "")
            out.append(lit.group(2) if lit else "?")
    return out


def _program_words(code: str, pos: int, depth: int = 0) -> list[str]:
    ""

    rest = code[pos:].lstrip()
    lit = re.match(_PY_STRING, rest)
    if lit:
        s = lit.group(2)
        if re.search(r"\s", s.strip()):
            return _words(s)
        after = rest[lit.end():]
        nxt = re.match(r"\s*,\s*(?=[\[(])", after)
        return [s] + (_items(_bracketed(after, nxt.end()), code) if nxt else [])
    if rest[:1] in ("[", "("):
        return _items(_bracketed(rest, 0), code)
    which = re.match(r"""(?:shutil\.)?which\s*\(\s*(['"])([^'"]+)\1\s*\)""", rest)
    if which:
        return [which.group(2)]
    ident = re.match(r"([A-Za-z_]\w*)\b(?!\s*[(.\[])", rest)
    if ident and depth < 2:
        value = _assigned(code, ident.group(1))
        if value:
            return _program_words(value, 0, depth + 1) if value[:1] in "[('\"" or re.match(r"[rRbBuUfF]{1,2}['\"]", value) \
                else []
    return []


def _code_agent(code: str) -> Action | None:
    ""


    for m in _SPAWN_CALL.finditer(code):
        pos = m.end()
        if re.search(r"\bos\.spawn[lv]p?e?\s*\($", code[:pos]):

            comma = code.find(",", pos)
            pos = comma + 1 if comma >= 0 else pos
        found = _agent_words(_program_words(code, pos))
        if found:
            a = _agent_action(*found, code)
            if a is not None:
                return a
    if _HERMES_IMPORT.search(code):
        return _agent_action("hermes-agent", [], code)
    if _HUB_IMPORT.search(code):
        return _agent_action("hub", [], code)
    return None




_SHELL_CALL = re.compile(r"(?:\bos\.(?:system|popen)|\bsubprocess\.(?:getoutput|getstatusoutput)|"
                         r"(?:child_process|\bcp|childProcess)\.(?:exec|execSync)|\bexecSync|create_subprocess_shell)\s*\($")
_CODE_PROGRAM = re.compile(r"^[\w.+@/~-]+$")


def _code_commands(code: str) -> list[tuple[str, bool]]:
    ""



    out = []
    for m in _SPAWN_CALL.finditer(code):
        pos = m.end()
        if re.search(r"\bos\.spawn[lv]p?e?\s*\($", code[:pos]):
            comma = code.find(",", pos)
            pos = comma + 1 if comma >= 0 else pos
        rest = code[pos:].lstrip()
        lit = re.match(_PY_STRING, rest)
        call = code[max(0, m.start() - 40):pos]
        shell = _SHELL_CALL.search(call) is not None or \
            re.match(r"[^)]*\bshell\s*=\s*True", rest[lit.end():] if lit else "") is not None
        if lit and (shell or re.search(r"\s", lit.group(2).strip())):
            text = re.sub(r"\\(.)", lambda e: {"n": "\n", "t": "\t"}.get(e.group(1), e.group(1)), lit.group(2))
            formatted = re.match(r"[rRbB]?[fF]", rest) is not None and re.search(r"\{[^{}]*\}", text) is not None
            out.append((re.sub(r"\{[^{}]*\}", "$(...)", text) if formatted else text, formatted))
            continue
        words = _program_words(code, pos)
        if not words or not _CODE_PROGRAM.match(words[0]):
            continue
        known = [w for w in words if w != "?"]
        out.append((shlex.join(known), len(known) < len(words)))
    return out







_WIPE_DIRS = {"/", "/home", "/root", "/etc", "/usr", "/var", "/bin", "/sbin", "/boot", "/lib"}
_DISK = re.compile(r"/dev/(?:sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*\b")
_FORK_BOMB = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")


def _hardline(words: list[str], seg: str) -> str | None:
    ""
    if _FORK_BOMB.search(seg):
        return "freeze the computer"
    if not words:
        return None
    head, args = words[0].rsplit("/", 1)[-1], words[1:]
    if head == "rm":
        for a in (w for w in args if not w.startswith("-")):
            p = re.sub(r"/\*$", "", a) or "/"
            if p in ("~", "~/", "$HOME", "${HOME}") or p.startswith("/") and "/" + posixpath.normpath(p).lstrip("/") in _WIPE_DIRS:
                return "delete the whole computer, a system folder or the home folder"
        return None
    if head.startswith("mkfs") or head == "dd" and any(re.match(r"^of=", w) and _DISK.search(w) for w in args) or \
            re.search(r">\s*" + _DISK.pattern, re.sub(r"'[^']*'|\"[^\"]*\"", "''", seg)):
        return "erase a disk"
    if head == "kill" and "-1" in args:
        return "stop every program on the computer"
    if head in ("shutdown", "reboot", "halt", "poweroff") or head in ("init", "telinit") and args[:1] in (["0"], ["6"]) \
            or head == "systemctl" and any(a in ("poweroff", "reboot", "halt", "kexec") for a in args):
        return "restart or shut down the computer"
    return None


def _hardline_action(what: str, cmd: str) -> Action:
    return Action("hardline", what[:1].upper() + what[1:], inputs=cmd,
                  why_never=f"Zips never {what}, whether in a command, in code or in a scheduled job. Tell Colin in "
                            "one sentence what you wanted to do, so he can do it himself if he wants to.")







_ROOT_WORDS = {"sudo", "doas", "pkexec", "run0"}

_ROOT_READS = {"cat", "ls", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "du", "df", "stat",
               "file", "wc", "sha256sum", "md5sum", "sha1sum", "tree", "journalctl", "dmesg", "lsof", "ss", "netstat",
               "ps", "top", "htop", "free", "uptime", "lsblk", "blkid", "lscpu", "lspci", "lsusb", "lsmod", "id",
               "whoami", "true", "test", "[", "nvidia-smi", "sensors", "smartctl", "uname", "hostname", "who", "w",
               "last", "getent", "findmnt", "readlink", "realpath", "which", "printenv", "echo", "printf", "diff",
               "cmp", "zcat", "zgrep", "xxd", "hexdump", "strings", "iotop", "vmstat", "iostat", "nethogs", "tcpdump",
               "nmap", "ping", "dig", "host", "nslookup", "awk", "sort", "uniq", "cut", "column", "jq", "lshw",
               "dmidecode", "aa-status", "apparmor_status", "getfacl", "lsattr", "namei"}
_READ_SUBS = {
    "systemctl": {"status", "show", "list-units", "list-unit-files", "list-timers", "list-sockets", "list-jobs",
                  "list-dependencies", "is-active", "is-enabled", "is-failed", "is-system-running", "cat", "help",
                  "get-default", "show-environment", "--version", "--failed"},
    "service": {"--status-all"},
    "apt": {"list", "show", "search", "policy", "depends", "rdepends", "changelog", "showsrc", "update", "--version"},
    "apt-get": {"update", "check", "download", "changelog", "source", "--version", "-s", "--simulate"},
    "apt-cache": None, "dpkg-query": None,
    "dpkg": {"-l", "-L", "-s", "-S", "-c", "-I", "-p", "--list", "--listfiles", "--status", "--search", "--contents",
             "--info", "--print-avail", "--get-selections", "--print-architecture", "--audit", "--verify", "-V"},
    "snap": {"list", "info", "find", "services", "changes", "change", "version", "logs", "connections", "tasks"},
    "dnf": {"list", "info", "search", "repolist", "check-update", "history", "provides", "repoquery"},
    "yum": {"list", "info", "search", "repolist", "check-update", "history", "provides"},
    "flatpak": {"list", "info", "search", "remotes", "history", "ps"},
    "docker": {"ps", "logs", "inspect", "images", "image", "stats", "info", "version", "top", "port", "events", "diff",
               "history", "search", "system"},
    "ufw": {"status", "show", "app", "version"},
    "iptables": {"-L", "-S", "--list", "--list-rules"}, "ip6tables": {"-L", "-S", "--list", "--list-rules"},
    "nft": {"list"}, "ip": {"addr", "address", "a", "link", "route", "r", "neigh", "-br", "-s", "-c", "-j"},
    "tailscale": {"status", "ip", "netcheck", "version", "whois", "ping", "dns"},
    "crontab": {"-l"}, "find": None, "fdisk": {"-l"}, "sysctl": {"-a", "-n", "-e"}, "zfs": {"list", "get"},
    "zpool": {"list", "status", "get"}, "resolvectl": {"status", "query", "dns"},
    "timedatectl": {"status", "show", "list-timezones", "timesync-status"}, "hostnamectl": {"status"},
    "loginctl": {"list-sessions", "list-users", "show-session", "show-user", "session-status", "user-status"},
}
_PACKAGES = {"apt", "apt-get", "aptitude", "dpkg", "snap", "dnf", "yum", "zypper", "pacman", "flatpak", "pip", "pip3",
             "npm", "pnpm", "yarn", "gem", "brew", "cargo", "pipx"}

_SYSTEM_PACKAGES = {"apt", "apt-get", "aptitude", "dpkg", "snap", "dnf", "yum", "zypper", "pacman", "flatpak"}
_REMOVE_SUBS = {"remove", "purge", "autoremove", "autopurge", "erase", "uninstall", "un", "rm", "-r", "-P", "-R", "-Rs",
                "-Rns", "--remove", "--purge"}
_INSTALL_SUBS = {"install", "reinstall", "add", "i", "-i", "-S", "-U", "--install", "--unpack", "localinstall"}
_UPGRADE_SUBS = {"upgrade", "full-upgrade", "dist-upgrade", "update", "refresh", "-Syu", "-Su", "up", "--configure",
                 "-a", "autoclean", "clean", "fix-broken"}
_SERVICE_VERBS = {"stop": "Stop", "kill": "Stop", "start": "Start", "restart": "Restart", "reload": "Restart",
                  "try-restart": "Restart", "reload-or-restart": "Restart", "try-reload-or-restart": "Restart",
                  "condrestart": "Restart", "force-reload": "Restart"}
_USER_CMDS = {"useradd": "Add a user account", "adduser": "Add a user account", "userdel": "Remove a user account",
              "deluser": "Remove a user account", "usermod": "Change a user account", "groupadd": "Add a user group",
              "addgroup": "Add a user group", "groupdel": "Remove a user group", "delgroup": "Remove a user group",
              "groupmod": "Change a user group", "gpasswd": "Change who is in a user group",
              "chsh": "Change a user account", "chfn": "Change a user account", "chage": "Change a user account",
              "visudo": "Change who has administrator rights"}
_PERMISSION_CMDS = {"chmod", "chown", "chgrp", "setfacl", "chattr"}
_WRITE_CMDS = {"tee", "cp", "mv", "install", "ln", "mkdir", "touch", "rmdir", "truncate", "sed", "dd", "rsync", "tar",
               "unzip", "patch", "cpio", "rename"}


def _operands(args: list[str]) -> list[str]:
    ""
    return [a for a in _plain_words(args) if not a.startswith("-")]


def _name_list(items: list[str], limit: int = 3) -> str:
    items = list(dict.fromkeys(i for i in items if i))
    shown = ", ".join(items[:limit]) + (" and more" if len(items) > limit else "")
    return shown


_PACKAGE_NAME = re.compile(r"^[a-z0-9][\w.+-]*(=[\w.*+:~-]+)?$", re.I)

_OTHER_SOURCE = re.compile(r"^(-o|--option|--allow-unauth\w*|--allow-downgrades|--force\S*)\b")


_APT_OPTS = {"-y", "--yes", "--assume-yes", "-q", "-qq", "--quiet", "--no-install-recommends"}

_PACKAGE_FILE = re.compile(r"\.(deb|rpm|zip|tar|gz|tgz|bz2|xz|zst)$", re.I)


def _named_install(head: str, sub: str, args: list[str], ops: list[str]) -> bool:
    ""



    if head not in ("apt", "apt-get") or sub != "install" or not ops:
        return False

    return all(_PACKAGE_NAME.match(p) and not p.endswith("-") and not _PACKAGE_FILE.search(p) for p in ops) \
        and not any(_OTHER_SOURCE.match(a) or "://" in a or a.startswith("-") and a not in _APT_OPTS for a in args)


def _system_change(words: list[str], root: bool, place: str, seg: str, cwd: str | None) -> Action | None:
    ""

    if not words:
        return None
    head, args = words[0].rsplit("/", 1)[-1], words[1:]
    ops = _operands(args)
    if head in _PACKAGES and (root or head in _SYSTEM_PACKAGES):
        if head in ("apt-cache", "dpkg-query"):
            return None
        verbs = [a for a in args if a in _REMOVE_SUBS | _INSTALL_SUBS | _UPGRADE_SUBS or not a.startswith("-")]
        sub = (verbs or [""])[0]
        reads = _READ_SUBS.get(head)
        if reads and (sub in reads or not sub and any(a in reads for a in args)):
            return None
        pkgs = [o for o in ops if o != sub and not re.match(r"^(global|-)", o)]
        if sub in _REMOVE_SUBS or head == "pacman" and any(a.startswith("-R") for a in args):
            title = f"Uninstall {_name_list(pkgs)} from {place}" if pkgs else f"Uninstall software from {place}"
        elif sub in _INSTALL_SUBS or head == "pacman" and any(a.startswith("-S") for a in args):
            title = f"Install {_name_list(pkgs)} on {place}" if pkgs else f"Install software on {place}"
        elif sub in _UPGRADE_SUBS:
            title = f"Update the software on {place}"
        elif head in _SYSTEM_PACKAGES:
            title = f"Change the software on {place}"
        else:
            title = ""


        carried = re.search(r"(^|\s)(export\s+)?[A-Za-z_]\w*=|\s(-E|--preserve-env)\b", seg)
        named = _named_install(head, sub, args, [o for o in ops if o != sub])
        if title and place == "this computer" and not carried and named:




            return Action("write", title, target=", ".join(pkgs)[:200], inputs=seg)
        if title:
            return Action("system", title, target=", ".join(pkgs)[:200], inputs=seg)
    if not root:
        return None
    if head in _ROOT_READS:
        return None
    if head == "systemctl" or head == "service":
        rest = [a for a in args if a not in ("--now", "--no-block", "--quiet", "-q", "--no-pager", "--system", "--user")]
        if head == "service":
            unit, verb = (rest + ["", ""])[:2]
            units = [unit]
        else:
            verb = next((a for a in rest if not a.startswith("-")), "")
            units = [a for a in _operands(rest) if a != verb]
        if verb in _READ_SUBS["systemctl"] or not verb and head == "systemctl":
            return None
        names = _name_list([re.sub(r"\.service$", "", u) for u in units])
        what = f"the {names} service" + ("s" if len(units) > 1 else "") if names else "a service"
        if verb in _SERVICE_VERBS:
            title = f"{_SERVICE_VERBS[verb]} {what} on {place}"
        elif verb in ("enable", "reenable"):
            title = f"Start {what} whenever {place} starts"
        elif verb == "disable":
            title = f"Stop {what} from starting with {place}"
        elif verb == "mask":
            title = f"Block {what} from starting on {place}"
        elif verb in ("poweroff", "reboot", "halt", "kexec"):
            title = f"Restart or shut down {place}"
        elif verb in ("suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate"):
            title = f"Put {place} to sleep"
        else:
            title = f"Change the services on {place}"
        return Action("system", title, target=names, inputs=seg)
    if head in ("shutdown", "reboot", "halt", "poweroff") or head in ("init", "telinit") and ops[:1] in (["0"], ["6"]):
        return Action("system", f"Restart or shut down {place}", inputs=seg)
    if head in ("passwd", "chpasswd"):
        return Action("account", f"Change a password on {place}", inputs=seg,
                      why_never="Zips never change passwords. Colin does that himself.")
    if head in _USER_CMDS:
        return Action("system", f"{_USER_CMDS[head]} on {place}", target=_name_list(ops), inputs=seg)
    if head in _PERMISSION_CMDS:
        paths = [o for o in ops[1:] if not _safe(_norm(o, cwd))]
        return Action("system", f"Change who owns or can use {_name_list(paths)} on {place}", target=_name_list(paths),
                      inputs=seg) if paths else None
    if head in _WRITE_CMDS:

        if head == "sed" and not any(a.startswith("-i") or a == "--in-place" for a in args):
            return None
        if head in ("tee", "mkdir", "touch", "rmdir", "truncate"):
            targets = ops
        elif head == "sed":
            targets = ops[1:]
        elif head == "dd":
            targets = [a[3:] for a in args if a.startswith("of=")]
        elif head in ("tar", "unzip", "cpio", "patch"):
            at = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-C", "--directory", "-d")), None)
            targets = [at or cwd or "the current folder"]
        else:
            targets = ops[-1:]
        outside = [t for t in targets if not _safe(_norm(t, cwd))]
        if targets and not outside:
            return None
        if any(t.startswith("/etc/sudoers") for t in outside):
            return Action("system", f"Change who has administrator rights on {place}", target=_name_list(outside),
                          inputs=seg)
        return Action("system", f"Change {_name_list(outside)} with administrator rights" if outside else
                      "Change files with administrator rights", target=_name_list(outside), inputs=seg)
    if head in ("rm", "unlink", "shred", "find", "crontab"):
        return None
    if head in ("kill", "pkill", "killall"):
        return Action("system", f"Stop a program on {place} with administrator rights", inputs=seg)
    if head in _READ_SUBS:
        reads = _READ_SUBS[head]
        if reads is None or any(a in reads for a in args[:2]) or not args and head in ("docker", "tailscale", "ufw"):
            return None
    return Action("system", f"Run {head} with administrator rights on {place}", inputs=seg)






_OTHER_HOME = re.compile(r"^(?:/home/|/Users/)([^/]+)|^/(?:var/)?root(?:/|$)")
_AS_USER = {"sudo": ("-u", "--user"), "doas": ("-u",), "runuser": ("-u", "--user"), "run0": ("-u", "--user"),
            "pkexec": ("--user",)}
OTHER_HOME_NEVER = ("Other people's home folders and accounts on this computer are theirs. Zips never open them, even "
                    "as administrator. Tell Colin in one sentence what you wanted to look at.")


def _own_accounts() -> set[str]:
    import getpass
    try:
        me = getpass.getuser()
    except Exception:
        me = ""
    return {"root", "0", "#0", me, os.path.basename(_HOME)} - {""}


def _as_account(words: list[str]) -> str | None:
    ""
    i = 0
    while i < len(words):
        name = words[i].rsplit("/", 1)[-1]
        if re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
            continue
        if name == "su":
            rest, j = words[i + 1:], 0
            while j < len(rest):
                if rest[j] in ("-c", "--command", "-s", "--shell", "-g", "--group", "-G", "--supp-group", "-w"):
                    j += 2
                elif rest[j].startswith("-"):
                    j += 1
                else:
                    return rest[j]
            return None
        if name not in _WRAPPERS:
            return None
        flags = _AS_USER.get(name, ())
        takes = _WRAPPERS[name][0]
        i += 1
        while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
            flag = words[i]
            i += 1
            if flag == "--":
                break
            if "=" in flag and flag.split("=", 1)[0] in flags:
                return flag.split("=", 1)[1]
            if flags and re.fullmatch(r"-[A-Za-z]*u", flag) or flag in flags:
                return words[i] if i < len(words) else None
            if flags and re.fullmatch(r"-u.+", flag):
                return flag[2:]
            if flag in takes and i < len(words):
                i += 1
        while i < len(words) and re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
        i += min(_WRAPPERS[name][1], max(0, len(words) - i - 1))
    return None


def _root_reach(words: list[str], seg: str, cwd: str | None, found: Action | None) -> Action | None:
    ""

    head = words[0].rsplit("/", 1)[-1] if words else ""
    paths = [words[0]] + [w.split("=", 1)[1] if re.match(r"^[\w-]+=", w) else w for w in _operands(words[1:])]
    others, outside = [], []
    for i, p in enumerate(paths):
        if p.startswith("~") and not (p == "~" or p.startswith("~/")):
            others.append(p)
            continue
        if not p.startswith(("/", "~", "$")) and (i == 0 or cwd is None and ".." not in p.split("/")):
            continue
        norm = _norm(p, cwd)
        if norm is None:
            outside.append(p)
            continue
        m = _OTHER_HOME.match(norm)
        if m and (m.group(1) is None or m.group(1) not in _own_accounts()):
            others.append(p)
        elif i and not norm.startswith("~") and not _safe(norm):
            outside.append(p)
    if others:
        return Action("secret", "Open another person's files on this computer", target=_name_list(others),
                      inputs=seg, why_never=OTHER_HOME_NEVER)
    if found is None and outside and head not in ("rm", "unlink", "shred", "crontab"):
        return Action("system", f"Read {_name_list(outside)} with administrator rights", target=_name_list(outside),
                      inputs=seg)
    return found


def _strip_root(words: list[str]) -> tuple[list[str], bool, bool]:
    ""

    names: list[str] = []
    inner = _strip_wrappers(words, names)
    if inner and re.search(r"\s", inner[0].strip()):
        inner = _strip_wrappers(_words(inner[0]) + inner[1:], names)
    return inner, any(n in _ROOT_WORDS for n in names), "ssh" in names


_C_SCRIPT = re.compile(r"""(?:^|\s)(?:-[A-Za-z]*c[A-Za-z]*|--command)(?:\s+|=)(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|(\S+))""", re.S)










_UNIT_STOPS = {"stop", "disable", "restart", "mask", "kill", "try-restart", "reload-or-restart", "condrestart",
               "force-reload", "try-reload-or-restart"}
_CONTAINER_STOPS = {"stop", "rm", "kill", "restart", "pause", "down"}
OWN_PROGRAMS_HINT = ("To stop a program you started in the background, use the process tool, or kill %1 in the same "
                     "command line that started it. Anything else running on this computer asks Colin first.")
_BACKGROUNDED = re.compile(r"(?<![&>|])&(?![&>])")


def _service_stop(words: list[str], place: str, seg: str, whole: str) -> Action | None:
    ""
    if not words:
        return None
    head, args = words[0].rsplit("/", 1)[-1], words[1:]
    plain = [a for a in args if not a.startswith("-")]
    names, what = [], ""
    if head == "systemctl" and plain and plain[0] in _UNIT_STOPS:
        names, what = plain[1:], plain[0].replace("-", " ")
    elif head == "service" and len(plain) >= 2 and plain[1] in ("stop", "restart", "force-reload"):
        names, what = plain[:1], plain[1]
    elif head in ("docker", "podman", "docker-compose"):
        sub = plain[1:] if plain[:1] in (["container"], ["compose"]) else plain
        compose = head == "docker-compose" or plain[:1] == ["compose"]
        if head == "docker-compose":
            sub = plain
        if not sub or sub[0] not in _CONTAINER_STOPS or (sub[0] == "pause" and compose):
            return None
        names, what = (sub[1:] or ["its compose project"]), sub[0]
    elif head in ("kill", "pkill", "killall"):
        if any(a in ("-l", "-L", "--list") for a in args):
            return None
        names = plain
        if names and all(re.fullmatch(r"%\d*", n) for n in names) and _BACKGROUNDED.search(whole or ""):
            return None
        what = "stop"
    elif head == "launchctl" and plain and plain[0] in ("unload", "bootout", "remove", "disable", "stop"):
        names, what = plain[1:], plain[0]
    elif head == "tailscale" and plain[:1] == ["down"]:
        names, what = ["Tailscale"], "turn off"
    elif head == "cloudflared" and "tunnel" in plain and plain[plain.index("tunnel") + 1:][:1] in (["delete"], ["cleanup"]):
        names, what = plain[plain.index("tunnel") + 2:] or ["a tunnel"], "delete"
    else:
        return None
    return Action("system", f"{what.capitalize()} {_name_list(names) or 'a service'} on {place}",
                  target=_name_list(names), inputs=seg, extra={"hint": OWN_PROGRAMS_HINT})


def system_changes(cmd: str, cwd: str | None, where: str = "your computer", root: bool = False,
                   depth: int = 0) -> list[tuple[Action, str]]:
    ""


    out = []
    place = "the Mac" if _on_mac(where) else "this computer"
    fed = _fed(cmd)[0] if depth < 2 else []
    for piece in _split(_HEREDOC.sub("", cmd)):
        s = piece.strip().lstrip("( ")
        while s.endswith(")") and s.count(")") > s.count("("):
            s = s[:-1].rstrip()
        words = _words(s)
        inner, rooted, remote = _strip_root(words)
        rooted = rooted or root
        who = None if remote or place != "this computer" else _as_account(words)
        if who and who not in _own_accounts():
            out.append((Action("secret", f"Run a command as {who} on this computer", target=who, inputs=s,
                               why_never=OTHER_HOME_NEVER), s))
            continue
        if not inner:
            continue
        head = inner[0].rsplit("/", 1)[-1]
        if head in ("su", "sh", "bash", "zsh", "dash", "ksh"):
            rooted = rooted or head == "su"
            scripts = _launched(inner)
            if scripts and scripts[0]:
                m = _C_SCRIPT.search(s)
                if depth < 2 and m:
                    single, double, bare = m.groups()
                    text = single if single is not None else (re.sub(r'\\(["\\$`])', r"\1", double) if double is not None
                                                              else bare)
                    out += system_changes(text, cwd, where, rooted, depth + 1)
                continue
            if rooted and len([w for w in inner[1:] if not w.startswith("-")]) <= (1 if head == "su" else 0):

                for text in fed:
                    out += system_changes(text, cwd, where, True, depth + 1)
                if not fed:
                    out.append((Action("system", f"Open an administrator shell on {place}", inputs=s), s))
                continue
        a = _system_change(inner, rooted, "another computer" if remote else place, s, cwd)
        if a is None and not remote and hard_stops():
            a = _service_stop(_strip_wrappers(inner), place, s, cmd)
        if rooted and not remote and place == "this computer":
            a = _root_reach(inner, s, cwd, a)
        if a is not None:
            out.append((a, shlex.join(inner) if 0 < len(inner) < len(words) else s))
    return out




_SHELLS = r"(?:ba|z|da|k|fi)?sh|at|batch"
_INTERPRETERS = r"python[\d.]*|node|deno|bun|ruby|perl"
_HERESTRING = re.compile(rf"""(?<![\w/-])(?:\S*/)?({_SHELLS}|{_INTERPRETERS})\b[^\n;&|]*?<<<\s*(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|(\S+))""")
_ECHO_PIPE = re.compile(rf"""\b(?:echo|printf)\s+(?:-\w+\s+)*(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|([^|;&\n'"]+?))\s*\|\s*"""
                        rf"""(?:sudo\s+(?:-\S+\s+)*)?(?:\S*/)?({_SHELLS}|{_INTERPRETERS})\b""")
_INLINE_CODE = re.compile(rf"""(?<![\w/-])(?:\S*/)?(?:{_INTERPRETERS})\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*[ce]\s+"""
                          r"""(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""", re.S)


def _fed(cmd: str) -> tuple[list[str], list[str]]:
    ""
    shell, code = [], []
    for m in _HEREDOC.finditer(cmd):
        line = cmd[cmd.rfind("\n", 0, m.start()) + 1:m.start()]
        last = re.split(r"&&|\|\||[;|]", line)[-1]
        head = (_strip_wrappers(_words(last)) or [""])[0].rsplit("/", 1)[-1]
        if re.fullmatch(_SHELLS, head):
            shell.append(m.group(3))
        elif re.fullmatch(_INTERPRETERS, head):
            code.append(m.group(3))
    for m in _HERESTRING.finditer(cmd):
        text = next((g for g in m.groups()[1:] if g is not None), "")
        (shell if re.fullmatch(_SHELLS, m.group(1)) else code).append(text)
    for m in _ECHO_PIPE.finditer(cmd):
        text = next((g for g in m.groups()[:3] if g is not None), "")
        (shell if re.fullmatch(_SHELLS, m.group(4)) else code).append(text.replace("\\n", "\n"))
    code += [next(g for g in m.groups() if g is not None) for m in _INLINE_CODE.finditer(_HEREDOC.sub("", cmd))]
    return shell, code


def _agent_command(cmd: str, cwd: str | None, code: bool = False, depth: int = 0) -> Action | None:
    ""




    if code:
        return _code_agent(cmd)
    for seg, _here in _segments(cmd, cwd):
        found = _agent_words(_words(seg))
        if found:
            a = _agent_action(*found, cmd)
            if a is not None:
                return a
    for m in _WHICH_AT_START.finditer(cmd):
        agent = _agent_of(m.group(1))
        if agent:
            return _agent_action(agent, ["-p"], cmd)
    shell, scripts = _fed(cmd)
    if depth < 2:
        for text in shell + _scheduled(cmd, cwd):
            a = _agent_command(text, cwd, depth=depth + 1)
            if a is not None:
                return a
    for text in scripts:
        a = _code_agent(text)
        if a is not None:
            return a
    return None




_ECHO_TEXT = re.compile(r"""\b(?:echo|printf)\s+(?:-\w+\s+)*(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|([^|;&\n'"<>]+))""")
_CRON_LINE = re.compile(r"^\s*(?:@(?:reboot|yearly|annually|monthly|weekly|daily|midnight|hourly)|(?:\S+\s+){4}\S+)\s+(.+)$")
_EXEC_LINE = re.compile(r"(?m)^\s*Exec(?:Start|StartPre|StartPost|Stop|Reload)\s*=\s*[-@:+!]*(.+)$")


def _small_file(path: str, cwd: str | None) -> str:
    ""
    n = _norm(path, cwd)
    if n is None:
        return ""
    data = safe_files.card_text(_HOME + n[1:] if n.startswith("~") else n, 64 * 1024)
    return "" if data is None else data.decode("utf-8", "replace")


def _crontab_writes(cmd: str, cwd: str | None) -> list[tuple[str, str | None]]:
    ""

    out = []
    for seg, here in _segments(cmd, cwd):
        words = _words(seg)
        if not words or words[0].rsplit("/", 1)[-1] != "crontab":
            continue
        args, i = [], 1
        while i < len(words):
            if words[i] == "-u":
                i += 2
                continue
            args.append(words[i])
            i += 1
        flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and a != "-")
        if "l" in flags or "r" in flags:
            continue
        files = [a for a in args if a == "-" or not a.startswith("-") and not re.match(r"^\d*[<>]", a)]
        out.append((files[0] if files else "-", here))
    return out


def _scheduled(cmd: str, cwd: str | None) -> list[str]:
    ""

    texts = [m.group(3) for m in _HEREDOC.finditer(cmd)]
    texts += [next((g for g in m.groups() if g is not None), "").replace("\\n", "\n") for m in _ECHO_TEXT.finditer(cmd)]
    for target, here in _crontab_writes(cmd, cwd):
        if target != "-":
            texts.append(_small_file(target, here))
    redirected = re.search(r"\bcrontab\b[^|;&\n]*<\s*(['\"]?)([^\s;&|'\"<>]+)\1", cmd)
    if redirected:
        texts.append(_small_file(redirected.group(2), cwd))
    for m in re.finditer(r"\b(?:at|batch)\b[^|;&\n]*\s-f\s*(['\"]?)([^\s;&|'\"]+)\1", cmd):
        texts.append(_small_file(m.group(2), cwd))
    crontab = bool(_crontab_writes(cmd, cwd))
    unit = re.search(r"\.(?:service|timer|socket|path)\b|systemd/|systemctl\s+(?:--user\s+)?edit\b", cmd) is not None
    lines = []
    for text in texts:
        for line in (text or "").splitlines() if crontab else []:
            cron = _CRON_LINE.match(line)
            if cron and not line.strip().startswith("#"):
                lines.append(cron.group(1))
        if unit:
            lines += [m.group(1) for m in _EXEC_LINE.finditer(text or "")]
        if text and re.search(r"\bat\b[^|;&\n]*\s-f\s", cmd):
            lines.append(text)
    return [ln for ln in lines if ln.strip()]


def _crontab_change(cmd: str, cwd: str | None) -> Action | None:
    ""

    if not _crontab_writes(cmd, cwd):
        return None
    return Action("account", "Change the jobs this computer runs on a schedule", inputs=cmd,
                  extra={"hint": "Scheduled jobs belong in your cronjob tool, where Colin can see them; use it instead "
                                 "of the crontab unless he asked for the crontab itself."})





_LOCAL_HOST = re.compile(r"127\.\d|localhost|0\.0\.0\.0|::1|ip6-localhost", re.I)
_HTTP_CLIENT = re.compile(r"://|\b(?:curl|wget|https?|httpie|xh|requests|urllib|httpx|aiohttp|fetch|nc|ncat|socat|"
                          r"telnet)\b|/dev/tcp/", re.I)


def _hub_ports() -> set[int]:
    try:
        from . import home
        ports = {int(home.get("ports.hub") or 8650)}
        url = str(home.get("network.public_url") or "")
        m = re.search(r"://[^/:\s]+:(\d+)", url)
        ports |= {int(p) for p in (m.group(1) if m else None, home.get("network.serve_port")) if p}
    except Exception:
        ports = {8650}
    return ports


def _hub_address(cmd: str) -> Action | None:
    from urllib.parse import unquote
    text = unquote(cmd)
    near = any(re.search(rf":{p}(?!\d)", text) or _LOCAL_HOST.search(text) and
               re.search(rf"(?<![\w.:-]){p}(?![\w.])", text) for p in _hub_ports())
    if near or re.search(r"/api/+pair(?![\w-])", text) and _HTTP_CLIENT.search(text):
        return Action("agent", "Reach the hub's own address from the terminal", inputs=cmd, why_never=HUB_NEVER)
    return None






_CURL_VALUE_SHORT = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")
_CURL_VALUE_LONG = {
    "--header", "--output", "--user-agent", "--user", "--max-time", "--connect-timeout", "--retry", "--retry-delay",
    "--retry-max-time", "--resolve", "--connect-to", "--cookie", "--cookie-jar", "--referer", "--proxy", "--write-out",
    "--cacert", "--capath", "--cert", "--key", "--range", "--limit-rate", "--config", "--dump-header", "--interface",
    "--output-dir", "--oauth2-bearer", "--aws-sigv4", "--url-query", "--variable", "--max-filesize", "--speed-limit",
    "--speed-time", "--trace", "--trace-ascii", "--stderr", "--netrc-file", "--request-target", "--proxy-user",
    "--max-redirs", "--expect100-timeout", "--keepalive-time", "--dns-servers", "--unix-socket", "--happy-eyeballs-timeout-ms"}
_CURL_SENDS = re.compile(r"^--(?:data(?:-ascii|-binary|-raw|-urlencode)?|json|form(?:-string)?|upload-file)$")
_WGET_SENDS = re.compile(r"^--(?:post-data|post-file|body-data|body-file)(?:=|$)")
_WEB_METHODS = {"POST", "PUT", "PATCH"}
_LOOPBACK = re.compile(r"^(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|ip6-localhost)$", re.I)



_CODE_SENDS = re.compile(
    r"\b(?:requests|httpx|axios)\.(?:post|put|patch)\s*\(|"
    r"\.(?:request|open)\s*\(\s*['\"](?:POST|PUT|PATCH)['\"]|"
    r"\bmethod\s*[:=]\s*['\"](?:POST|PUT|PATCH)['\"]|"
    r"\b(?:Request|urlopen)\s*\([^()]*(?:\([^()]*\)[^()]*)*\bdata\s*=\s*(?!None\b)|"
    r"\b(?:Request|urlopen)\s*\(\s*[^,()]*(?:\([^()]*\)[^,()]*)?,\s*(?![\w]+\s*=)[^\s)]", re.I)
_CODE_CLIENT_SENDS = re.compile(r"\b\w+\.(?:post|put|patch)\s*\(")
_HTTP_LIBRARY = re.compile(r"\b(?:requests|httpx|aiohttp|urllib3|axios|node-fetch|got|ky|superagent)\b")
_URL_HOST = re.compile(r"\bhttps?://([^/\s'\"`?#)\\]+)", re.I)


def _host_of(url: str) -> str:
    ""
    m = re.match(r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/\s]*@)?(\[[^\]]+\]|[^/:?#\s]+)", url.strip("'\""), re.I)
    host = m.group(1).lower().rstrip(".") if m else ""
    return host if re.fullmatch(r"[\w.-]+\.[a-z0-9-]+|\[[0-9a-f:]+\]|localhost", host) else ""


def _sent_file(value: str, form: bool = True) -> str:
    ""


    m = re.search(r"(?:^|=|^[\w.-]+)[@<]([^;\s]+)" if form else r"(?:^|=|^[\w.-]+)@([^;\s]+)", value)
    return m.group(1) if m and m.group(1) != "-" else ""





_DAV_READ_HOST = re.compile(r"(?:p\d+-)?(?:caldav|contacts)\.icloud\.com|apidata\.googleusercontent\.com", re.I)
_READ_ONLY_POST = re.compile(
    r"(?:https://)?(?:www\.googleapis\.com/calendar/v3/freeBusy|"
    r"graph\.microsoft\.com/(?:v1\.0|beta)/(?:me|users/[^/?#\s$]+)/calendar/getSchedule)/?(?:[?#]|$)")




_CURL_RETARGET = re.compile(r"^(?:--request-target|--connect-to|--resolve|--proxy|--preproxy|--socks\w*|--unix-socket|"
                            r"--abstract-unix-socket|--config|--variable|--expand-\w+|--url-query)(?:=|$)|^-(?!-)[A-Za-z]*[Kx]")


def _calendar_read(method: str, url: str) -> bool:
    host = _host_of(url)
    if method in ("REPORT", "PROPFIND"):
        return bool(host) and _DAV_READ_HOST.fullmatch(host) is not None
    return method in ("", "POST") and _READ_ONLY_POST.match(url.strip("'\"")) is not None


def _web_send_words(words: list[str]) -> tuple[str, str] | None:
    ""

    if not words:
        return None
    head, args = words[0].rsplit("/", 1)[-1], words[1:]
    sends, urls, files, method = False, [], [], ""
    positional: list[str] = []
    if head == "curl":
        get, i = False, 0
        while i < len(args):
            w, nxt = args[i], args[i + 1] if i + 1 < len(args) else ""
            i += 1
            if w.startswith("--"):
                name, eq, val = w.partition("=")
                val = val if eq else nxt
                if name == "--get":
                    get = True
                elif _CURL_SENDS.match(name):
                    sends = True
                    files.append(val if name == "--upload-file" else _sent_file(val, form=name.startswith("--form")))
                elif name == "--request":
                    method = val.upper()
                elif name == "--url":
                    urls.append(val)
                if not eq and (name in _CURL_VALUE_LONG or _CURL_SENDS.match(name) or name in ("--request", "--url")):
                    i += 1
                continue
            if w.startswith("-") and len(w) > 1:
                for j, ch in enumerate(w[1:], 1):
                    if ch == "G":
                        get = True
                    if ch not in _CURL_VALUE_SHORT:
                        continue
                    val = w[j + 1:] or nxt
                    if ch in "dF":
                        sends = True
                        files.append(_sent_file(val, form=ch == "F"))
                    elif ch == "T":
                        sends = True
                        files.append(val)
                    elif ch == "X":
                        method = val.upper()
                    if not w[j + 1:]:
                        i += 1
                    break
                continue
            positional.append(w)
        sends = (sends and not get) or method in _WEB_METHODS
    elif head == "wget":
        for i, w in enumerate(args):
            name, eq, val = w.partition("=")
            val = val if eq else (args[i + 1] if i + 1 < len(args) else "")
            if _WGET_SENDS.match(w):
                sends = True
                if "file" in name:
                    files.append(val)
            elif name == "--method" and val.upper() in _WEB_METHODS:
                sends = True
            elif not w.startswith("-"):
                positional.append(w)
        positional = [w for w in positional if "://" in w] or positional
    elif head in ("http", "https", "xh", "xhs"):
        positional = [w for w in args if not w.startswith("-")]
        if positional and positional[0].upper() in _WEB_METHODS | {"GET", "HEAD", "DELETE", "OPTIONS"}:
            sends = positional.pop(0).upper() in _WEB_METHODS
        else:

            items = [w for w in positional[1:] if re.match(r"^[^=:@\s]+(?::=@?|=@?|@)(?!=)", w)]
            sends = bool(items)
        files += [_sent_file(w) for w in positional[1:]]
    else:
        return None
    if not sends:
        return None


    dests = urls + (positional[:1] if head in ("http", "https", "xh", "xhs") else positional)
    if dests and all(_calendar_read(method, w) for w in dests) and not (
            head == "curl" and any(_CURL_RETARGET.match(w) for w in args)):
        return None
    hosts = list(dict.fromkeys("" if w.startswith("$") else _host_of(w) for w in dests)) or [""]
    outside = [h for h in hosts if not (h and _LOOPBACK.match(h))]
    if not outside:
        return None
    return (outside[0] if len(outside) == 1 else ""), next((f for f in files if f), "")


def _code_web_send(text: str) -> tuple[str, str] | None:
    ""
    if not (_CODE_SENDS.search(text) or _HTTP_LIBRARY.search(text) and _CODE_CLIENT_SENDS.search(text)):
        return None
    hosts = list(dict.fromkeys(h.lower() for h in _URL_HOST.findall(text)))
    if hosts and all(_LOOPBACK.match(h.split(":")[0]) for h in hosts):
        return None
    return (hosts[0].split(":")[0] if len(hosts) == 1 else ""), ""





_REMOTE_PATH = re.compile(r"^(?:(?:[\w.-]+@)?(\[[^\]]+\]|[\w.-]+):(?!//)|(?:scp|sftp|rsync|ssh)://(?:[^@/\s]*@)?(\[[^\]]+\]|[^/:\s]+))")
_SOCAT_HOST = re.compile(r"^(?:TCP|UDP|SCTP|OPENSSL|SSL|PROXY)[46]?(?:-CONNECT)?:([^:,]+):", re.I)
_KEY_IN_URL = re.compile(r"[?&](?:[\w.-]*[_.-])?(?:(?:api)?key|token|secret|passw\w*|auth\w*)(?:[_.-][\w.-]*)?=(?:\$|[^&\s#]{8,})", re.I)
_LOCAL_START = re.compile(r"[~/.]|\$\{?HOME\b")
_TAILNET_ADDRESS = re.compile(r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+")


def _own_machine(host: str) -> bool:
    ""
    from . import home
    host, name = host.lower().strip("[]"), home.host_name().lower()
    if _LOOPBACK.match(host) or host in (name, f"{name}-ts") or _TAILNET_ADDRESS.fullmatch(host):
        return True
    address = re.sub(r"^https?://|[:/].*$", "", str(home.get("network.public_url") or "")).lower()
    return address.endswith(".ts.net") and (host == address or host.endswith("." + address.split(".", 1)[1]))


def _remote_host(word: str) -> str | None:
    m = _REMOTE_PATH.match(word)
    return (m.group(1) or m.group(2) or "").lower() if m else None


def _mac_send_words(words: list[str]) -> tuple[str, str] | None:
    ""

    if not words:
        return None
    head, args = words[0].rsplit("/", 1)[-1], words[1:]


    plain, file = [w for w in args if not w.startswith("-")], ""
    if head in ("scp", "rsync"):

        remote = [i for i, w in enumerate(plain) if i and (_remote_host(w) is not None or "$" in w and not _LOCAL_START.match(w))]
        hosts = [_remote_host(plain[i]) or "$" for i in remote]
        file = plain[remote[0] - 1] if remote and _remote_host(plain[remote[0] - 1]) is None else ""
    elif head == "sftp":
        last = plain[-1] if plain else ""
        hosts = [_remote_host(last) or last.rsplit("@", 1)[-1].lower()] if last else []
    elif head in ("nc", "ncat", "netcat", "telnet"):
        hosts = [w.rsplit("@", 1)[-1].lower() for w in plain if not w.isdigit()]
    elif head == "socat":
        hosts = [m.group(1).lower() for m in map(_SOCAT_HOST.match, args) if m]
    elif head in ("curl", "wget"):
        hosts = [_host_of(w) for w in args if "://" in w and _KEY_IN_URL.search(w)]
    else:
        return None
    outside = list(dict.fromkeys(h for h in hosts if not _own_machine(h)))
    if not outside:
        return None
    host = outside[0] if len(outside) == 1 and not outside[0].startswith("$") else ""
    return host, file


def _web_sends(cmd: str, cwd: str | None, code: bool, mac: bool = False) -> list[Action]:
    ""

    found = []
    named = [p for p, _what in _SEND_CMDS + _PUBLISH_CMDS] + [_NETLIFY_API]
    pieces = [] if code else [(_web_send_words(_words(seg)) or (_mac_send_words(_words(seg)) if mac else None), seg)
                              for seg, _here in _segments(cmd, cwd)]
    pieces += [(_code_web_send(text), text) for text in ([cmd] if code else _fed(cmd)[1])]
    for sent, text in pieces:
        if sent is None or any(p.search(text) for p in named):
            continue
        host, file = sent
        name = file.rstrip("/").rsplit("/", 1)[-1]
        what = f"Upload {name}" if name and not name.startswith("$") else "Send data"
        scope = host if re.fullmatch(r"[\d.]+|\[[0-9a-f:]+\]", host) else _site_scope(host) if host else ""
        if consent_base(host) in X_SITES:
            scope = ""
        found.append(Action("send", f"{what} to {host}" if host else f"{what} over the web", target=host, site=host,
                            inputs=cmd, scope=scope))
    return found


def classify_command(cmd: str, where: str = "your computer", cwd: str | None = None, code: bool = False,
                     detect=None, _depth: int = 0) -> Action | None:
    ""



    if not cmd:
        return None
    if _PIPE_TO_SHELL.search(cmd):
        return Action("secret", "Run a script downloaded from the web", inputs=cmd,
                      why_never="Running a script piped from the web is off for Zips. If Colin asked you to install "
                                "something, use the package manager (apt, pip, npm, brew), or give him the command to run.")
    for pat, what in _PAY_CMDS:
        if pat.search(cmd):
            return Action("pay", what, inputs=cmd, why_never="Zips never move money. Tell Colin what you found so he can do it.")
    if _ADMIN_MERGE.search(cmd):
        return Action("hardline", "Merge a pull request past its checks", inputs=cmd, why_never=ADMIN_MERGE_NEVER)
    agent = _agent_command(cmd, cwd, code=code) or _hub_address(cmd)
    if agent is not None:
        return agent
    if not _on_mac(where) and not code:


        for piece in _split(_unwrap(_HEREDOC.sub("", cmd))):
            s = piece.strip().lstrip("( ")
            while s.endswith(")") and s.count(")") > s.count("("):
                s = s[:-1].rstrip()
            inner, _root, remote = _strip_root(_words(s))
            what = None if remote else _hardline(inner, s)
            if what:
                return _hardline_action(what, cmd)


    found = []
    for pat, channel in _SEND_CMDS:
        if pat.search(cmd):
            body = next((m.group(3) for m in _HEREDOC.finditer(cmd)), "")
            extra = {}
            if not body:
                msg = _message_file(cmd, cwd)
                if msg is not None:
                    body, extra["content_hash"] = msg
            text = body or cmd
            to = _recipients(text) or _recipients(cmd)
            found.append(Action("send", _send_summary(channel, to, _subject(text)), target=", ".join(to),
                                inputs=body[:4000] if body else cmd, details=cmd if body else "",
                                scope=to[0].lower() if len(to) == 1 else "", extra=extra))
            break
    found += _git_pushes(cmd, cwd)
    if not _on_mac(where) and not code:
        found += _git_option_forms(cmd, cwd, where, detect, _depth)
    for pat, what in _PUBLISH_CMDS:
        if pat.search(cmd):
            target, shown = _deploy_target(cmd, cwd, pat)
            named = _NAMED_PUBLISH.get(what)
            name = shown.rstrip("/").rsplit("/", 1)[-1] if shown else ""
            found.append(Action("publish", named.format(name=name) if named and name and name != "~" else what,
                                target=target, inputs=cmd))
    if _NETLIFY_API.search(cmd) and _HTTP_WRITE.search(cmd):
        found.append(Action("publish", "Publish the site on Netlify", inputs=cmd))
    for pats, kind in ((_DELETE_CMDS, "delete"), (_ACCOUNT_CMDS, "account")):
        for pat, what in pats:
            if pat.search(cmd):
                found.append(Action(kind, what, inputs=cmd))
    found += _gh_apis(cmd) + _web_sends(cmd, cwd, code, _on_mac(where))
    found += [_shell_delete(cmd, cwd), _code_delete(cmd, cwd), None if code else _crontab_change(cmd, cwd)]
    if not code:
        found += [a for a, _seg in system_changes(cmd, cwd, where)]
    if _depth < 2:


        for text in ([cmd] if code else _fed(cmd)[1]):
            for inner, unsure in _code_commands(text):
                if unsure and re.match(r"^(?:sudo\s+)?(?:\S*/)?(?:rm|unlink|shred)\b", inner):
                    continue
                a = classify_command(inner, where, cwd, detect=detect, _depth=_depth + 1)
                if a is not None and floor(a.kind) == "never":
                    a.inputs = cmd
                    return a
                if a is not None:
                    a.inputs, a.details = cmd, ""
                    found.append(a)
                flagged = unjudged(inner, cwd, detect) if detect is not None else None
                if flagged and flagged not in _ARTIFACTS:
                    found.append(_flagged_action(flagged, inner, cmd))
    if not code and _depth < 2:


        for text in _substitutions(cmd) + _fed(cmd)[0]:
            a = classify_command(text, where, cwd, _depth=_depth + 1)
            if a is not None and floor(a.kind) == "never":
                a.inputs = cmd
                return a
            if a is not None:
                a.inputs, a.details = cmd, ""
                found.append(a)
    found = [a for a in found if a is not None]
    if not found and re.search(_NETLIFY, cmd):
        found.append(Action("write", "Publish a preview on Netlify", inputs=cmd))
    return _combine(found, cmd)


def _combine(found: list[Action | None], cmd: str) -> Action | None:
    ""

    found = [a for a in found if a is not None]
    if len(found) <= 1:
        return found[0] if found else None
    parts = [found[i] for i in sorted(range(len(found)), key=lambda i: (-RANK[floor(found[i].kind)], i))]
    first, summaries, targets, extra = parts[0], [], [], {}
    for a in parts:
        if a.summary not in summaries:
            summaries.append(a.summary)
        targets += [t for t in (a.target or "").split(", ") if t and t not in targets]
    for a in reversed(parts):
        extra.update(a.extra)
    extra.pop("paths", None)
    extra["parts"] = len(parts)
    lower = lambda t: t[:1].lower() + t[1:] if t[1:2].islower() else t
    asks = sum(1 for a in parts if floor(a.kind) == "ask")
    return Action(first.kind, (summaries[0] + "".join(", and " + lower(t) for t in summaries[1:]))[:300],
                  target=", ".join(targets)[:300], site=first.site, inputs=first.inputs if first.details else cmd,
                  details=first.details, scope=first.scope if asks <= 1 else "", extra=extra)












_HCMD = r"(?:^|[\n`;&|(]|\$\()\s*(?:sudo\s+(?:-\S+\s+)*)?(?:env\s+(?:\w+=\S*\s+)*)?(?:(?:exec|nohup|setsid|time)\s+)*"
_HPKG = r"(?:-\S+(?:\s+[^-\s]\S*)?\s+)*"
_HSH = r"(?:bash|sh|zsh|ksh|dash)"
HERMES_LIKE = [(re.compile(p, re.I | re.S), d) for p, d in [
    (r"\brm\s+(-[^\s]*\s+)*/", "delete in root path"),
    (r"\brm\s+-[^\s]*r", "recursive delete"),
    (r"\brm\s+--recursive\b", "recursive delete (long flag)"),
    (r"\brm\s+(?!--(?:\s|$))(?:(?!\s--(?:\s|$))[^\n\"';|&])*\s(?:-[a-z]*r[a-z]*\b|--recursive\b)",
     "recursive delete (flags after operands)"),
    (r"\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b", "world/other-writable permissions"),
    (r"\bchown\s+(-[^\s]*)?R\s+root", "recursive chown to root"),
    (_HCMD + r"mkfs\b", "format filesystem"),
    (_HCMD + r"dd\s+.*if=", "disk copy"),
    (r">\s*/dev/sd", "write to block device"),
    (r"\bDROP\s+(TABLE|DATABASE)\b", "SQL DROP"),
    (r"\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)", "SQL DELETE without WHERE"),
    (r"\bTRUNCATE\s+(TABLE)?\s*\w", "SQL TRUNCATE"),
    (r">\s*(?:/etc/|/private/(?:etc|var|tmp|home)/)", "overwrite system config"),
    (r"\bsystemctl\s+(-[^\s]+\s+)*(stop|restart|disable|mask)\b", "stop/restart system service"),
    (r"\bkill\s+-9\s+-1\b", "kill all processes"),
    (r"\bpkill\s+-9\b", "force kill processes"),
    (r"\bkillall\s+(-[^\s]*\s+)*-(9|KILL|SIGKILL)\b", "force kill processes (killall -KILL)"),
    (r"\bkillall\s+(-[^\s]*\s+)*-r\b", "kill processes by regex (killall -r)"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (rf"\b(curl|wget)\b.*\|\s*(?:[/\w]*/)?{_HSH}(?:\s|$|-c)", "pipe remote content to shell"),
    (rf"\b(base64|base32|base16)\s+(?:-[dD]|--decode)\b.*\|\s*\b{_HSH}\b",
     "pipe decoded content to shell (possible command obfuscation)"),
    (r"\btee\b.*[\"']?(?:/etc/|~/\.ssh|~/\.(?:bashrc|zshrc|profile|bash_profile|zprofile)\b)",
     "overwrite system file via tee"),
    (r">>?\s*[\"']?(?:/etc/|~/\.ssh|~/\.(?:bashrc|zshrc|profile|bash_profile|zprofile)\b)",
     "overwrite system file via redirection"),
    (r"\bxargs\s+.*\brm\b", "xargs with rm"),
    (r"\bfind\b.*-exec(?:dir)?\s+(/\S*/)?rm\b", "find -exec/-execdir rm"),
    (r"\bfind\b.*-delete\b", "find -delete"),
    (r"\bhermes\s+(?:-\S+(?:\s+(?!-)\S+)?\s+)*gateway\s+(stop|restart)\b",
     "stop/restart hermes gateway (kills running agents)"),
    (r"\bhermes\s+update\b", "hermes update (restarts gateway, kills running agents)"),
    (r"\bdocker(?:-compose|\s+compose)\s+(?:-\S+(?:\s(?!-)\S+)?\s+)*(restart|stop|kill|down)\b",
     "docker compose restart/stop/kill/down (container lifecycle)"),
    (r"\bdocker\s+(?:-\S+(?:\s(?!-)\S+)?\s+)*(restart|stop|kill)\b", "docker restart/stop/kill (container lifecycle)"),
    (r"\b(pkill|killall)\b.*\b(hermes|gateway|cli\.py)\b", "kill hermes/gateway process (self-termination)"),
    (r"\bkill\b.*\$\(\s*(pgrep|pidof)\b", "kill process via pgrep/pidof expansion (self-termination)"),
    (r"\b(cp|mv|install)\b.*\s/etc/", "copy/move file into system config path"),
    (r"\bsed\s+-[^\s]*i.*\s/etc/", "in-place edit of system config"),
    (rf"\b{_HSH}\s+<<", "shell execution via heredoc"),
    (r"\bgit\s+reset\s+--h(?:a(?:r(?:d)?)?)?\b", "git reset --hard (destroys uncommitted changes)"),
    (r"\bgit\s+push\b.*--forc[a-z]*\b", "git force push (rewrites remote history)"),
    (r"\bgit\s+push\b.*-f\b", "git force push short flag (rewrites remote history)"),
    (r"\bgit\s+clean\s+-[^\s]*f", "git clean with force (deletes untracked files)"),
    (r"\bgit\s+branch\s+(?-i:-D)\b", "git branch force delete"),
    (r"\bgit\s+branch\b[^;|&\n]*?(?:-d\b|--delete\b)[^;|&\n]*?(?:-f\b|--force\b)", "git branch force delete (long flags)"),
    (r"\bchmod\s+\+x\b.*[;&|]+\s*\./", "chmod +x followed by immediate execution"),
    (r"\bsudo\b[^;|&\n]*?\s+(?:-s\b|--st[a-z]*\b|-a\b|--a[a-z]*\b)", "sudo with privilege flag (stdin/askpass/shell/list)"),
    (_HCMD + r"npm\s+" + _HPKG + r"(?:uninstall|unlink|remove|rm|r|un)\b", "package manager uninstall"),
    (_HCMD + r"pnpm\s+" + _HPKG + r"(?:uninstall|remove|rm|un)\b", "package manager uninstall"),
    (_HCMD + r"yarn\s+" + _HPKG + r"(?:global\s+)?(?:uninstall|remove)\b", "package manager uninstall"),
    (_HCMD + r"pip(?:3)?\s+" + _HPKG + r"uninstall\b", "package manager uninstall"),
    (_HCMD + r"brew\s+" + _HPKG + r"(?:uninstall|remove|rm)\b", "package manager uninstall"),
    (r"(?<![\w/-])(?:\S*/)?(?:python[\d.]*|perl|ruby|node)\s+(?:-\S+\s+)*-[a-z]*[ec]\b", "script execution via -e/-c flag"),
    (r"(?<![\w/-])(?:\S*/)?(?:python[\d.]*|perl|ruby|node)\s+(?:-\s+)?<<", "script execution via heredoc"),
    (rf"(?<![\w/-])(?:\S*/)?{_HSH}\s+(?:-\S+\s+)*-[a-z]*c[a-z]*\s", "shell command via -c/-lc flag"),
    (r">>?\s*['\"]?(?:[^\s'\";&|<>]*/)?(?:\.env(?:\.[^/\s'\";&|<>]+)*|config\.yaml)(?=[\s;&|<>\"']|$)",
     "overwrite project env/config via redirection"),
    (r"\btee\b[^\n;&|]*\s['\"]?(?:[^\s'\";&|<>]*/)?(?:\.env(?:\.[^/\s'\";&|<>]+)*|config\.yaml)(?=[\s;&|<>\"']|$)",
     "overwrite project env/config via tee"),
    (r"\b(?:DOCKER_HOST|CONTAINER_HOST)=", "docker/podman daemon redirect via environment (DOCKER_HOST/CONTAINER_HOST)"),
    (r"\bdocker\s+(?:-\S+\s+)*(?:-H|--host)\b", "docker with remote daemon redirect (-H/--host)"),
]]



_MALFORMED = "command parser limit or malformed executable payload"
_ARTIFACTS = {_MALFORMED, "command parser limit exceeded"}
_PAYLOAD_RUNNER = re.compile(r"^(?:python[\d.]*|pypy[\d.]*|perl|ruby|node|nodejs|deno|bun|php|lua|osascript|pwsh|"
                             r"powershell|sort|rg|ag|man)$")


def hermes_like(text: str) -> str | None:
    ""
    found = next((d for p, d in HERMES_LIKE if p.search(text or "")), None)
    if found and not found.startswith("script execution via"):
        return found
    for seg in _split(text or ""):
        head = (seg.split() or [""])[0].rsplit("/", 1)[-1]
        if _PAYLOAD_RUNNER.match(head):
            try:
                shlex.split(seg)
            except ValueError:
                return _MALFORMED
    return found



_GATE_FAMILIES = [
    (re.compile(r"^(delete in root path|recursive delete|find -delete|find -exec|xargs with rm|git clean)", re.I), "delete"),
    (re.compile(r"^(script execution via|shell command via|shell execution via heredoc)", re.I), "script"),
    (re.compile(r"^SQL (DROP|DELETE|TRUNCATE)", re.I), "sql"),
    (re.compile(r"^git force push", re.I), "push"),
    (re.compile(r"^(git reset --hard|git branch force delete|package manager uninstall|docker compose)", re.I),
     "workspace"),
    (re.compile(r"^overwrite project env/config", re.I), "project file"),

    (re.compile(r"^(stop/restart system service|overwrite system (config|file)|copy/move file into system config|"
                r"in-place edit of system config|recursive chown to root|world/other-writable)", re.I), "system"),
]


_DB_CLIENT = re.compile(r"(?<![\w-])(?:psql|pg_\w+|mysql\w*|mariadb\w*|sqlite3?|sqlite-utils|duckdb|redis-cli|mongo(?:sh)?|"
                        r"clickhouse(?:-client)?|cockroach|sqlcmd|bq|snowsql|supabase|turso|wrangler\s+d1|prisma|knex|sequelize|"
                        r"psycopg\d?|pymysql|asyncpg|sqlalchemy|dbmate|flyway|liquibase|usql|pgcli|mycli|litecli|dbshell|"
                        r"dbconsole|rails\s+db|heroku\s+pg|(?:fly|flyctl)\s+(?:postgres|pg|mpg))(?![\w-])|"
                        r"\.(?:execute|executemany|executescript|query|raw)\s*\(", re.I)




_SQL_WORDS = re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b|\bDELETE\s+FROM\b|\bTRUNCATE\b", re.I)
_TEXT_READERS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "git", "sed", "awk", "gawk", "cat", "less", "more", "head",
                 "tail", "echo", "printf", "find", "wc", "sort", "uniq", "jq", "diff", "tee", "cut", "tr", "column",
                 "bat", "nl", "xxd", "file"}


def _sql_as_text(cmd: str) -> bool:
    ""
    if _DB_CLIENT.search(cmd or ""):
        return False
    for piece in _split(cmd or "", pipes=False):
        if not _SQL_WORDS.search(piece):
            continue
        for part in _split(piece):
            words = _strip_wrappers(_words(part.strip().lstrip("( ")))
            if not words or words[0].rsplit("/", 1)[-1] not in _TEXT_READERS:
                return False
    return True




_COMPOSE_ELSEWHERE = re.compile(r"\b(?:COMPOSE_PROJECT_NAME|COMPOSE_FILE|COMPOSE_PROJECT_DIRECTORY|COMPOSE_ENV_FILES|"
                                r"DOCKER_HOST|DOCKER_CONTEXT|CONTAINER_HOST)=")


def _family(description: str) -> str | None:
    return next((f for p, f in _GATE_FAMILIES if p.match(description or "")), None)


def _in_workspace(words: list[str], here: str | None, elsewhere: bool = False) -> bool:
    ""




    if _git_call(words, here) is not None:
        return _git_safe(words, here)
    head = words[0].rsplit("/", 1)[-1] if words else ""
    if head in ("npm", "pnpm", "yarn"):
        if any(w in ("-g", "--global", "global", "--location=global") for w in words):
            return False
        where = next((words[i + 1] for i, w in enumerate(words[:-1]) if w in ("--prefix", "-C", "--dir", "--cwd")), None)
        return _safe(_norm(where, here) if where else here)
    if head in ("docker", "docker-compose"):
        if elsewhere or any(w in ("-p", "--project-name", "-H", "--host", "-c", "--context") or
                            w.startswith(("--project-name=", "--host=", "--context=")) for w in words):
            return False
        files = [words[i + 1] for i, w in enumerate(words[:-1])
                 if w in ("-f", "--file", "--project-directory", "--env-file")]


        named = [w for i, w in enumerate(words[:-1]) if w in ("-f", "--file", "--project-directory")]
        return (_safe(here) or bool(named)) and all(_safe(_norm(f, here)) for f in files)
    return False


def _write_targets(seg: str) -> list[str]:
    ""

    words, out = _words(seg), []
    for i, w in enumerate(words):
        m = re.match(r"^(?:\d*|&)>>?\|?(.*)$", w)
        if m and not w.startswith(">&") and not re.match(r"^\d*>&\d", w):
            target = m.group(1) or (words[i + 1] if i + 1 < len(words) else "")
            if target:
                out.append(target)
    head = words[0].rsplit("/", 1)[-1] if words else ""
    args = [w for w in words[1:] if not w.startswith("-") and not re.match(r"^(?:\d*|&)[<>]", w)]
    if head == "tee":
        out += args
    elif head in ("cp", "mv", "install") and len(args) > 1:
        out.append(args[-1])
    return out


def _judged(seg: str, here: str | None, family: str, cmd: str = "", elsewhere: bool = False) -> bool:
    ""

    words = _words(seg)
    head = words[0].rsplit("/", 1)[-1] if words else ""
    git = _git_call(words, here)
    if family == "delete":


        return head in ("rm", "unlink", "shred", "find", "xargs", "rsync") or git is not None and git.sub == "clean" \
            or git is not None and git.sub == "rm" and _git_safe(words, here)
    if family == "script":
        return True
    if family == "sql":
        a = classify_command(seg, cwd=here)
        if a is not None and a.kind == "delete":
            return True

        whole = classify_command(cmd, cwd=here) if cmd and cmd != seg else None
        return whole is not None and whole.kind == "delete" and "from a database" in whole.summary or \
            _sql_as_text(cmd or seg)
    if family == "push":
        a = _git_push(seg, here)
        return a is not None and (a.kind != "write" or bool(a.extra.get("scratch")) or _git_safe(words, here))
    if family == "workspace":
        return _in_workspace(words, here, elsewhere)
    if family == "project file":


        targets = _write_targets(seg)
        return bool(targets) and all(_safe(_norm(t, here)) for t in targets)
    return False


def _substitutions(cmd: str) -> list[str]:
    ""
    text = _unwrap(_HEREDOC.sub("", cmd))
    scanned = _scan(text)
    if scanned is not None:
        return [t for t in scanned[1] if t.strip()]
    return [a or b for a, b in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", text) if (a or b).strip()]


def _bare(seg: str) -> str:
    ""
    for _ in range(3):
        new = re.sub(r"\$\([^()]*\)|`[^`]*`", "x", seg)
        if new == seg:
            break
        seg = new
    return seg


def unjudged(cmd: str, cwd: str | None, detect=None) -> str | None:
    ""






    return _unjudged(cmd, cwd, detect or hermes_like, 0)[0]


def _unjudged(cmd: str, cwd: str | None, detect, depth: int) -> tuple[str | None, set]:
    ""
    families = {"script"}
    if depth < 2:
        shell, code = _fed(cmd)
        for text in _substitutions(cmd) + shell:
            found, inner = _unjudged(text, cwd, detect, depth + 1)
            if found:
                return found, families
            families |= inner
        for text in code:


            for command, _unsure in _code_commands(text):
                found, inner = _unjudged(command, cwd, detect, depth + 1)
                if found:
                    return found, families
                families |= inner
            found = detect(text)
            if not found or found in _ARTIFACTS:
                continue
            family = _family(found)
            if family is None or not _judged(text, cwd, family, cmd):
                return found, families
            families.add(family)
    elsewhere = _COMPOSE_ELSEWHERE.search(cmd) is not None
    system = frozenset(seg for _a, seg in system_changes(cmd, cwd))
    for seg, here in _segments(cmd, cwd):
        found = detect(seg)
        if found and seg != _bare(seg):
            found = detect(_bare(seg))
        git = _git_call(_words(seg), here)
        if not found and git is not None and (git.options or git.alias):


            plain = detect(git.plain)
            if _family(plain) in _GIT_FAMILIES:
                families.add(_family(plain))
                continue
        if not found or found in _ARTIFACTS:
            continue
        family = _family(found)
        if seg.strip() in system:

            families |= {family or found, found}
            continue
        if family is None or not _judged(seg, here, family, cmd, elsewhere):
            return found, families
        families.add(family)
    pieces = [p.strip() for p in _split(_HEREDOC.sub("", cmd), pipes=False)]

    prefixes = []
    for p in pieces:
        words = _words(p)
        inner = _strip_wrappers(words)
        if 0 < len(inner) < len(words):
            prefixes.append(shlex.join(words[:len(words) - len(inner)]) + " true")
    for text in prefixes + pieces + [a + " ; " + b for a, b in zip(pieces, pieces[1:])]:
        found = detect(text)
        if found and found not in _ARTIFACTS and _family(found) not in families and found not in families:
            return found, families
    found = detect(cmd)
    if found and (depth == 0 or found not in _ARTIFACTS) and _family(found) not in families and found not in families:
        return found, families
    return None, families



_W = lambda words: re.compile(r"\b(" + words + r")\b", re.I)




_DELETE_V = r"(?:permanently\s+)?(?:delete|remove|trash|erase|cancel|empty|clear out|get rid of)"
_SHARE_V = (r"share|unshare|invite|give\b[^.;]*?\baccess|make\b[^.;]*?\bpublic|transfer ownership|"
            r"change\b[^.;]*?\bpermissions?|add\b[^.;]*?\bas (?:an? )?(?:editor|viewer|commenter|collaborator|owner)|"
            r"remove\b[^.;]*?\baccess|add (?:a )?member")
_RSVP_V = r"accept|decline|rsvp|respond to|tentatively accept|say (?:yes|no|maybe) to"
_SEND_V = r"send|reply|respond|forward|email|e-mail|mail|message|write back"
_LEAD = (r"(?:(?:please|pls|also|then|and|now|ok|okay|just|so|can you|could you|would you|will you|i need you to|"
         r"i want you to|go ahead and|let's|lets|you can|you should)\s+)*")
_QUESTION = re.compile(r"^(?:so\s+|and\s+|also\s+|but\s+)?(?:did|does|do(?!\s+not\b)|what|what's|whats|who|who's|whom|whose|when|"
                       r"where|which|why|how|has|have|had|is|are|was|were|any|anything|anyone|anybody|is there|"
                       r"are there|was there)\b", re.I)
_ASKING = re.compile(r"^(?:please\s+)?(?:can|could|would|will) you\b", re.I)



_REQUEST_IN_QUESTION = re.compile(
    r"\b(?:why (?:don'?t|dont|not)(?: you)?|(?:can|could|would|will) you|(?:is it|would it be|is that|is it ok(?:ay)? if"
    r" you) ?(?:possible|ok|okay|alright|all right|fine)?(?: for you)? to|do me a favou?r(?:,| and)|any chance you "
    r"(?:could|can)|how about you)\s+(?:please\s+|just\s+|go ahead and\s+|"
    r"also\s+)*(?=\w)", re.I)












_REFUSED_V = (r"(?:mark|archive|label|move|star|flag|spam|change|modify|edit|write|create|update|add|delete|remove|"
              r"rename|comment|post|send|reply|forward|share|invite|publish|duplicate|copy|cancel|overwrite|touch|"
              r"alter|merge|deploy|push|close|open|submit)")
_REFUSAL = re.compile(
    r"(?:\b(?:do\s+not|don['’]?t|never|without)\s+(?:(?:ever|actually|try\s+to|attempt\s+to|need\s+to|have\s+to|"
    r"go\s+and|accidentally|yet)\s+)*|(?:^|[,;]\s*|\b(?:and|but)\s+)not\s+)"
    rf"{_REFUSED_V}\b(?:(?:\s*,\s*{_REFUSED_V}\b)*,?\s+(?:or|nor)\s+{_REFUSED_V}\b)?", re.I)




_DEFERRAL = re.compile(r"\b(?:until|till|til|unless|before|yet|for now|for the moment|in the meantime)\b", re.I)


def _without_refusal(text: str) -> str:
    ""
    text = text or ""

    def drop(m: re.Match) -> str:
        start = max(text.rfind(c, 0, m.start()) for c in ".;!?\n") + 1
        ends = [i for i in (text.find(c, m.end()) for c in ".;!?\n") if i >= 0]
        return m.group(0) if _DEFERRAL.search(text[start:min(ends, default=len(text))]) else " "
    return _REFUSAL.sub(drop, text)


def _clauses(req: str, commas: bool = True) -> list[str]:
    ""



    out = []
    for sent in re.split(r"(?<=[.?!;:])\s+|\s+-\s+", req):
        sent = _without_refusal(sent).strip()
        if not sent:
            continue
        parts = re.split(r",\s*|\s+(?=then\s)", sent) if commas else [sent]
        if (_QUESTION.match(sent) or sent.endswith("?")) and not _ASKING.match(sent):
            inner = _REQUEST_IN_QUESTION.search(sent)
            if inner:
                rest = sent[inner.end():].rstrip("?")
                parts = re.split(r",\s*|\s+(?=then\s)", rest) if commas else [rest]
            else:
                parts = parts[1:]
        out += [p.strip() for p in parts if p.strip()]
    return out


def _verb(verbs: str, req: str) -> bool:
    ""
    for c in _clauses(req):
        if re.match(rf"{_LEAD}(?:{verbs})\b", c, re.I) or re.search(rf"\b(?:and|then|please)\s+(?:also\s+)?(?:{verbs})\b",
                                                                      c, re.I):
            return True
    return False


def clip(text: str, limit: int) -> str:
    ""


    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", head)]
    if ends and ends[-1] >= limit // 2:
        return head[:ends[-1]]
    cut = head[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-\u2013\u2014")
    return cut + "\u2026"


def _title(req: str) -> str:
    ""


    first = re.split(r"(?<=[.!?])(?<!\b[ap]\.m\.)(?<!\be\.g\.)(?<!\bi\.e\.)\s+|\n", req.strip(), maxsplit=1)[0]
    first = first.rstrip(".!")
    first = re.sub(r"^(?:please|pls|can you|could you)\s+", "", first, flags=re.I).strip()
    first = clip(first, 120)
    return first[:1].upper() + first[1:] if first else ""
_WRITE_V = (r"create|add|move|archive|remove|update|rename|label|mark|comment|check off|complete|write|append|edit|"
            r"fill|copy|schedule|block|reschedule|set|change|put|insert|draft|attach|assign")
_WRITE = _W(_WRITE_V)




_READ_LEAD = re.compile(rf"{_LEAD}(?:show|list|find|look\s+(?:up|for|at|through)|search|read|pull\s+up|tell\s+me|"
                        r"give\s+me|display|fetch|summari[sz]e|check(?!\s+off))\b", re.I)
_WRITE_AS_VERB = re.compile(rf"\b(?:and|then|please|also|to|you)\s+(?:also\s+)?(?:{_WRITE_V})\b", re.I)









_DESCRIBES = re.compile(rf"\b(?:the|a|an|each|every|all|any|its|their|my|your|\w+'s|\w+s')\s+(?:{_WRITE_V})(?:s|d|ed)?\b",
                        re.I)
_HANDS_BACK = re.compile(rf"{_LEAD}(?:return|report|include|get|pull|grab|count|compare)\b", re.I)


def _changes(asks: str) -> bool:
    ""
    for c in re.split(r"(?<=[.?!;:])\s+|,\s*", asks):
        c = _DESCRIBES.sub(" ", c)
        reads = _READ_LEAD.match(c) or _HANDS_BACK.match(c.strip())
        if _WRITE.search(c) and not (reads and not _WRITE_AS_VERB.search(c)):
            return True
    return False












_WHEN_WORDS = set("""
today tomorrow tonight morning afternoon evening night noon midnight weekend week weekday month year next every each
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun
mondays tuesdays wednesdays thursdays fridays saturdays sundays
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
""".split())
_CALENDAR_EDIT_TITLE = re.compile(
    r'(?P<edit>\b(?:update|move|reschedule|change|edit)\s+'
    r'(?:(?:(?:the|my|our|an?)\s+)?(?:event|meeting|appointment)\s+)?'
    r'(?:id\s+[\w@.%+=:/-]+\s+)?(?:(?:titled|called|named)\s+)?)'
    r'["“](?P<title>[^"”]+)["”]', re.I,
)


_CALENDAR_PEOPLE_ASK = re.compile(r"\b(?:invite|loop\s+in|notify|share|let\s+\S+\s+know)\b|\bsend\b[^.;]*?\binvit", re.I)


def calendar_fields(args: dict) -> tuple[list[str], bool]:
    ""


    raw = args.get("guests")
    raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else []
    guests = list(dict.fromkeys(" ".join(str(g).split()) for g in raw if str(g or "").strip()))
    return guests, args.get("notify_guests") is True or str(args.get("notify_guests")).lower() == "true"






STOP_FIELDS = ("send_to", "share_with", "delete", "publish")


def _as_list(value) -> list[str]:
    raw = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
    return list(dict.fromkeys(" ".join(str(v).split()) for v in raw if str(v or "").strip()))


def _as_bool(value) -> bool:
    return value is True or str(value).lower() == "true"


def connector_fields(args: dict) -> dict:
    ""
    args = args if isinstance(args, dict) else {}
    return {"send_to": _as_list(args.get("send_to")), "share_with": _as_list(args.get("share_with")),
            "delete": _as_bool(args.get("delete")), "publish": _as_bool(args.get("publish"))}


def _people_list(people: list[str]) -> str:
    return people[0] if len(people) == 1 else ", ".join(people[:-1]) + " and " + people[-1]





MIXED_STOPS = ("Not done: one call can carry only one of send_to, share_with, delete, publish, or guests and "
               "notify_guests. Split this into one call per stop, each with only its own field.")


def mixed_stops(fields: dict, guests=(), notify: bool = False) -> bool:
    ""
    f = connector_fields(fields or {})
    return sum(bool(x) for x in (f["send_to"], f["share_with"], f["delete"], f["publish"], guests or notify)) > 1


def _connector_by_shape(service: str, req: str, fields: dict, act, change, add_event, changes: bool,
                        asks: str) -> Action:
    ""

    name = {"gmail": "Gmail", "calendar": "your calendar", "drive": "your Drive", "trello": "Trello"}.get(service) or \
        " ".join(w.capitalize() for w in (service or "the account").split("_"))
    found = []
    send_to = [t for t in fields.get("send_to") or [] if t.lower() not in _own_addresses()]
    share = fields.get("share_with") or []
    if send_to:
        who = _people_list(send_to)
        found.append(("send", f"Email {who}" if service == "gmail" else f"Send or post in {name} to {who}",
                      ", ".join(send_to)))
    if share:
        found.append(("account", f"Share with {_people_list(share)} in {name}", ", ".join(share)))
    if fields.get("publish"):
        found.append(("publish", _title(req) or f"Publish something in {name}", ""))
    if fields.get("delete"):
        found.append(("delete", _title(req) or f"Delete something in {name}", ""))
    if found:
        kind, summary, target = found[0]
        people = send_to + share
        a = act(kind, summary, kinds=[k for k, _s, _t in found] if len(found) > 1 else None, target=target,
                scope=people[0].lower() if len(people) == 1 and kind in ("send",) else "")
        a.extra.update(send_to=send_to, share_with=list(share))
        return a
    if service == "calendar":
        return add_event("Change your calendar") if changes else \
            Action("read", f"read the calendar: {req[:200]}", site="calendar.google.com")
    if service == "gmail":
        if re.search(r"\b(archive|label|mark|spam|move|draft)\b", asks, re.I):
            return change("Change your mail")
        return Action("read", f"read email: {req[:200]}", site="gmail.com")
    if changes or (service not in ("drive", "trello") and _verb(_OTHER_CHANGE_V, req)):
        return change(f"Change something in {name}")
    return Action("read", f"read {name}: {req[:200]}", site=act("read", "").site)


def classify_connector(service: str, request: str, guests=(), notify: bool = False, fields: dict | None = None) -> Action:
    ""


    req = " ".join((request or "").split())
    guests = [g for g in guests or [] if g.lower() not in _own_addresses()] if service == "calendar" else []
    notify = bool(notify) and service == "calendar"
    short = req[:200]
    site = {"gmail": "gmail.com", "calendar": "calendar.google.com", "drive": "drive.google.com",
            "trello": "trello.com"}.get(service) or OTHER_SITES.get(service, service or "")
    asks = " . ".join(_clauses(req, commas=False))
    changes = bool(asks) and _changes(asks)

    def act(kind, summary, kinds=None, **kw):
        extra = {"request": short, **({"kinds": kinds} if kinds else {})}
        return Action(kind, summary, site=site, inputs=req, extra=extra, **kw)

    def change(fallback):
        return act("write", _title(req) or fallback)

    def add_event(fallback):
        moved = _CALENDAR_EDIT_TITLE.search(req)
        if moved:
            destination = re.match(r"\s+to\s+(.+)", req[moved.end():], re.I)
            if destination and (destination[1].split()[0].lower().rstrip(",") in _WHEN_WORDS
                                or re.match(r"\d", destination[1])):
                return act("write", f'Move "{moved["title"]}" to {_title(destination[1])}')
        events = _consent().calendar_events(req)
        if events:
            summary = f"Add {len(events)} events to your calendar" if len(events) > 1 else "Add an event to your calendar"
            action = act("write", summary)
            action.extra["calendar_events"] = events
            return action
        event = _consent().new_event(req)
        return act("write", f"Add to your calendar: {event}") if event else change(fallback)

    if hard_stops() and mixed_stops(fields or {}, guests, notify):
        return Action("hardline", "Do more than one stop in one call", site=site, why_never=MIXED_STOPS)
    if hard_stops() and not (service == "calendar" and (guests or notify)):
        return _connector_by_shape(service, req, connector_fields(fields or {}), act, change, add_event, changes, asks)
    if service == "gmail":
        if _verb(_SEND_V, req) and not re.search(r"\bdraft\b", req, re.I):
            to = _recipients(req)
            return act("send", f"Email {', '.join(to)}" if to else "Send an email", target=", ".join(to),
                       scope=to[0].lower() if len(to) == 1 else "")
        if _verb(_DELETE_V, req) and not re.search(r"\bunsubscribe\b", req, re.I):
            return act("delete", "Delete email")
        if re.search(r"\b(forwarding|filters?|settings|vacation responder|signature)\b", asks, re.I) and changes:
            return act("account", "Change your Gmail settings")


        if re.search(r"\b(archive|label|mark|spam|move)\b", asks, re.I):
            return change("Archive or label email")
        return Action("read", f"read email: {short}", site=site)
    if service == "calendar":
        if guests or notify:

            event = _consent().new_event(req)
            what = f"\u201c{event.split(',')[0]}\u201d" if event else "a calendar event"
            if guests:
                names = guests[0] if len(guests) == 1 else ", ".join(guests[:-1]) + " and " + guests[-1]
                action = act("send", f"Invite {names} to {what}", target=", ".join(guests),
                             scope=guests[0].lower() if len(guests) == 1 else "")
            else:
                action = act("send", f"Send the guests an update about {what}")
            action.extra.update(calendar_guests=list(guests), calendar_notify=bool(notify))
            return action
        if _verb(_RSVP_V, req) and re.search(r"\b(invit|event|meeting|call)", req, re.I):
            return act("send", "Answer a calendar invitation")
        if _verb(_DELETE_V, req):
            return act("delete", "Delete or cancel a calendar event")


        if changes or _CALENDAR_PEOPLE_ASK.search(asks):
            return add_event("Change your calendar")
        return Action("read", f"read the calendar: {short}", site=site)
    if service == "drive":
        if _verb(_SHARE_V, req):
            return act("account", "Share a file from your Drive")
        if _verb(_DELETE_V, req):
            return act("delete", "Delete a file from your Drive")
        if changes:
            return change("Change a file in your Drive")
        return Action("read", f"read files: {short}", site=site)
    if service == "trello":

        if _verb(r"remove\s+(?!(?:the|a|an|this|that|all|every|it|them)\b)[^.;]*?\bfrom\s+(?:the\s+)?[^.;]*\bboard|"
                 r"remove (?:a |the )?member", req) or re.search(r"\bremove\s+[^.;]*\bas (?:a )?member\b", asks, re.I):
            return act("account", "Change who can see your Trello board")
        if _verb(_SHARE_V, req):
            return act("account", "Share your Trello board")
        if _verb(r"(?:permanently\s+)?delete|close (?:the )?board", req):
            return act("delete", "Delete on Trello")
        if changes:
            return change("Update your Trello board")
        return Action("read", f"read Trello: {short}", site=site)
    return classify_other_connector(service, req, asks, changes, act, change)






OTHER_SITES = {"notion": "notion.so", "slack": "slack.com", "linear": "linear.app", "github": "github.com",
               "asana": "asana.com", "atlassian": "atlassian.net", "jira": "atlassian.net", "confluence": "atlassian.net",
               "figma": "figma.com", "hugging_face": "huggingface.co", "canva": "canva.com", "box": "box.com",
               "dropbox": "dropbox.com", "hubspot": "hubspot.com", "intercom": "intercom.com", "stripe": "stripe.com",
               "netlify": "netlify.com", "todoist": "todoist.com", "airtable": "airtable.com", "monday": "monday.com",
               "zoom": "zoom.us", "clickup": "clickup.com", "sentry": "sentry.io", "vercel": "vercel.com",
               "paypal": "paypal.com", "square": "squareup.com", "shopify": "shopify.com", "quickbooks": "intuit.com"}

_OTHER_SEND_V = (r"send|post|reply|respond|forward|message|dm|email|e-mail|comment|announce|notify|ping|mention|"
                 r"write back|schedule\b[^.;]*?\b(?:message|post|dm|email|reminder|announcement|reply)s?")

_OTHER_PUBLISH_V = (r"publish|unpublish|go live|deploy|redeploy|re-deploy|make\b[^.;]*?\bpublic|roll\s*back|rollback|"
                    r"revert\b[^.;]*?\b(?:commit|deploy|deployment|release|site|production|prod|merge|pr|pull request)|"
                    r"promote\b[^.;]*?\b(?:production|prod|live|deploy|deployment|preview|build|release)|"
                    r"merge\b[^.;]*?\b(?:pull request|pr|branch|main|master|production|prod|release|develop)|"
                    r"merge\s+(?:#|pr\b|pull\b)|release\s+(?:v?\d|version|the\s+(?:new\s+)?(?:version|build|app|release))|"
                    r"push\b[^.;]*?\b(?:main|master|branch|commits?|changes|code|repo|repository|origin|production|prod|"
                    r"live|site|tags?)|(?:open|create|make|submit|raise|file|start)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+|draft\s+)?"
                    r"(?:pull request|pr|merge request)|(?:trigger|start|run|kick off)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
                    r"(?:\w+\s+)?(?:build|deploy|deployment|release)")


_FEW = (r"(?:(?!(?:about|of|on|for|regarding|from|in|into|with|to|listing|called|named|titled|showing|that|which)\b)"
        r"\S+\s+){0,3}?")
_AMOUNT = r"(?:[$£€]\s?\d[\d,.]*|\d[\d,.]*\s*(?:dollars|bucks|usd|eur|euros?|pounds|gbp)\b)"


_OTHER_MONEY_V = (
    r"refund|charge|pay(?!\s+(?:attention|a visit))|pay\s*out|reimburse|donate|purchase|buy|"
    r"(?:transfer|wire|withdraw)\s+" + _FEW + r"(?:" + _AMOUNT + r"|money|funds|cash|(?:the\s+|my\s+|remaining\s+)*"
    r"balance)|(?:send|give|move|transfer|pay|tip|wire)\s+(?:\S+\s+){0,3}?" + _AMOUNT + r"|"
    r"(?:process|issue|give|make|send|initiate|approve|submit|schedule|confirm|capture|collect|request|create|retry)\s+"
    + _FEW + r"(?:refunds?|payments?|payouts?|transfers?|charges?|deposits?|withdrawals?|credit notes?)|"
    r"(?:create|make|draft|send|finalize|void|issue|pay|resend|generate)\s+" + _FEW + r"invoices?|"
    r"mark\b[^.;]*?\bas\s+paid|"
    r"(?:create|make|send|share|generate)\s+" + _FEW + r"payment\s+links?|"
    r"(?:start|create|cancel|update|change|upgrade|downgrade|pause|resume|renew|end|stop|add|set up)\s+" + _FEW
    + r"subscriptions?|(?:upgrade|downgrade|subscribe)\b[^.;]*?\b(?:plan|tier|subscription|seats?|annual|monthly|"
    r"yearly|pro|premium|paid|business|enterprise)")


_OTHER_CHANGE_V = (r"close|reopen|re-open|transition|resolve|unassign|tag|untag|pin|unpin|star|unstar|duplicate|"
                   r"restore|convert|link|unlink|prioriti[sz]e|deprioriti[sz]e|snooze|upload|import|sort|merge|estimate|"
                   r"toggle|enable|disable|watch|unwatch|follow|unfollow|start|stop|finish|record|bump|upgrade|"
                   r"downgrade|transfer|push|delay|postpone|wire\s+up|hook\s+up|connect|configure|install")
_MONEY_NEVER = "Zips never pay, refund, charge, transfer money or change billing. Tell Colin what you found so he can do it."


def classify_other_connector(service: str, req: str, asks: str, changes: bool, act, change) -> Action:
    name = " ".join(w.capitalize() for w in (service or "the account").split("_"))
    if _verb(_OTHER_MONEY_V, req):
        return act("pay", _title(req) or f"Move money in {name}", why_never=_MONEY_NEVER)
    found = []
    if _verb(_SHARE_V, req) or re.search(r"\b(?:invite|add\b[^.;]*?\bto (?:the )?(?:team|workspace|channel|project|"
                                         r"page|board|repo|repository))\b", asks, re.I) or \
            _verb(r"promote\b[^.;]*?\bto (?:an? )?(?:admin|owner|editor|member|maintainer)", req):
        found.append(("account", f"Share or invite someone in {name}"))
    if _verb(_OTHER_SEND_V, req):
        found.append(("send", f"Send or post something in {name}"))
    if _verb(_OTHER_PUBLISH_V, req):
        found.append(("publish", f"Publish something in {name}"))
    if _verb(_DELETE_V, req):
        found.append(("delete", f"Delete something in {name}"))

    push = service == "github" and _verb(r"push", req)
    if found:
        kind, fallback = found[0]
        a = act(kind, _title(req) or fallback, kinds=[k for k, _ in found] if len(found) > 1 else None)
    elif changes or _verb(_OTHER_CHANGE_V, req):
        a = change(f"Change something in {name}")
    else:
        return Action("read", f"read {name}: {req[:200]}", site=OTHER_SITES.get(service, service or ""))
    if push:
        a.extra["push"] = True
    return a




_CLICK_PAY = _W(r"pay|pay now|buy|buy now|purchase|checkout|check out|place (your |my )?order|confirm (your )?(purchase|order|payment)|"
                r"complete (purchase|order|payment)|submit (order|payment)|donate|transfer|send money|add payment|"
                r"start (a |my )?subscription|subscribe and save|upgrade|request (a )?ride|order now|"

                r"(place|confirm|submit|review) (a |your )?bid|(make|submit|send) (an |your )?offer|"

                r"^bid( (us ?)?\$[\d,]+(\.\d\d)?)?$")




_ORDER_SUBMIT = re.compile(r"^(pay|pay now|pay \$?[\d.,]+|buy|buy now|purchase|order now|place (your |my )?order|"
                           r"confirm (your )?(purchase|order|payment)|complete (your )?(purchase|order|payment)|"
                           r"submit (your )?(order|payment))$", re.I)
_ORDER_CHECKOUT = re.compile(r"^((proceed|continue|go) to )?(secure )?(checkout|check out)( now)?$|"
                             r"^continue to (payment|review|shipping)$", re.I)
_NEVER_MONEY = re.compile(r"transfer|refund|payout|withdraw|send money|donat|subscri|upgrade|ride|add (a )?payment|"
                          r"gift card|crypto|wire", re.I)




_CHECKOUT_CAN_ORDER = re.compile(r"^(secure )?(checkout|check out)( now)?$", re.I)


def checkout_can_order(label: str) -> bool:
    t = " ".join(str(label or "").split()).strip(" .!")
    return bool(_CHECKOUT_CAN_ORDER.match(t) or _BARE_FINAL.match(t))







_BARE_FINAL = re.compile(r"^(confirm|yes|yes,? confirm|complete|finish|submit)$", re.I)
_BARE_CONFIRM = re.compile(r"^(confirm|yes|yes,? confirm|complete|finish|submit|ok|okay|continue|proceed|done)$", re.I)


_ORDER_PAGE = re.compile(r"\border (summary|total|review|details)\b|\breview (your |and place )?order\b|checkout|"
                         r"check out|place (your |my )?order", re.I)


def bare_confirm(label: str) -> bool:
    return bool(_BARE_CONFIRM.match(" ".join(str(label or "").split()).strip(" .!")))




_FINAL_BOOKING = re.compile(r"^(book|book now|reserve|reserve now|(confirm|complete) (your )?(booking|reservation)|"
                            r"(book|reserve) (and|&) pay)$", re.I)


def final_booking(label: str) -> bool:
    return bool(_FINAL_BOOKING.match(" ".join(str(label or "").split()).strip(" .!")))


def finish(label: str) -> bool:
    return bool(_FINISH.match(" ".join(str(label or "").split()).strip(" .!")))




_BUYING_PAGE = re.compile(r"\b(order|checkout|check out|cart|bag|basket|shipping|delivery|billing|payment|pay|purchase|"
                          r"subtotal|total)\b", re.I)


def plain_review_step(label: str, page: dict) -> bool:
    shown = (page.get("headings") or []) + (page.get("dialogs") or [])
    return bool(re.fullmatch(r"continue to review", label.strip(" .!"), re.I) and page.get("headings")
                and not any(_BUYING_PAGE.search(t) for t in shown))


def order_click(label: str) -> str | None:
    ""
    t = " ".join(str(label or "").split()).strip(" .!")
    if not t or _NEVER_MONEY.search(t):
        return None
    if _ORDER_SUBMIT.match(t):
        return "submit"
    if _ORDER_CHECKOUT.match(t):
        return "checkout"
    return None


_CLICK_SECURITY = _W(r"change password|update password|reset password|new password|two-factor|2-step|2fa|"
                     r"security settings|turn off (two|2)|remove (security|passkey)")
_CLICK_BOOK = _W(r"book|book now|reserve|(confirm|complete|finish) (my |your |the )?(booking|reservation|appointment)|"
                 r"schedule (appointment|now)")
_CLICK_PUBLISH = _W(r"publish|publish deploy|deploy|go live|post|tweet|merge|make public|share publicly")


_CLICK_SEND = _W(r"send|send message|send invite|send invitation|send now|reply all|forward")
_CLICK_SEND_WHEN_COMPOSED = _W(r"reply|comment|post comment|message|share|invite|submit comment")

_CLICK_CODE = _W(r"send (me )?(a |the )?(verification |security |login |sign-in |one-time )?code|text me( a code)?|"
                 r"email me( a code| a link)?|call me|resend( code| link)?|send (me )?(a )?(sign-in |login |magic )?link|"
                 r"get (a )?code|verify by (text|email|sms)|send me (a |an )?(text|sms)( message)?( code)?|send (a |an )?(text|sms)( message)? code")
_CLICK_SUBMIT = _W(r"submit|apply now|apply for|apply to|easy apply|submit application|sign up|register|enroll|"
                   r"file (the )?claim|turn in|save batch|certify|e-?sign|sign electronically|finali[sz]e")


_FINISH = re.compile(r"^(finish|finish and submit|complete|sign|(complete|finish) (the |my |your )?(questionnaire|form|"
                     r"application|survey|registration|responses?|submission))$", re.I)


_CONFIRM = re.compile(r"^(confirm|yes|yes,? confirm|i understand|yes,? i understand|i'?m sure|yes,? i'?m sure|"
                      r"i understand the consequences.*|yes,? do it)$", re.I)
_CONFIRM_IN_DIALOG = re.compile(r"^(ok|okay|continue|proceed|yes,? continue|yes,? proceed)$", re.I)


_CONFIRM_FILLED = re.compile(r"^(yes,? )?confirm$", re.I)
_SIGN_IN_FIELD = re.compile(r"password|passcode|\bcode\b", re.I)

_LAST_PRESS = re.compile(r"^((save (and|&) )?continue|next( step| page)?|done|go|review|proceed)$", re.I)

_NO_NAME = {"button", "link", "menuitem", "generic", "img"}
_CLICK_DELETE =_W(r"delete|cancel (my |the |your )?(subscription|order|membership|plan|account)|empty trash|"
                   r"permanently|close account|remove (account|member|user|collaborator|contact|repository|site|"
                   r"device|photo|file|card|post)")
_CLICK_ACCOUNT = _W(r"accept|^agree$|i agree|agree and continue|accept terms|change (email|plan)|downgrade|cancel plan")




_TERMS_ACCEPT = re.compile(r"^(i\s+)?(accept|agree)(\s+(to\s+)?(all|(the\s+)?terms(\s+(and|&)\s+conditions)?|"
                           r"(and|&)\s+continue))?[.!]?$", re.I)
_CLICK_COOKIE = re.compile(r"cookie|consent", re.I)

_COOKIE_GENERIC = re.compile(r"^(accept|accept all|allow|allow all|agree|i agree|i accept|ok|okay|got it|"
                             r"(accept|agree|allow)( all)? (and|&) (close|continue))$", re.I)
_CLICK_TERMS = _W(r"terms|conditions|agreement|policy|contract|eula")

_RSVP_LABEL = re.compile(r"^(yes|no|maybe|going|not going|can'?t go|cannot go|rsvp|rsvp now|decline|accept|tentative|"
                         r"attending|not attending|interested|i'?m going|count me in|yes,? i'?ll (be there|attend)|"
                         r"no,? i can'?t (make it|attend))$", re.I)
_INVITE_SITE = re.compile(r"(^|\.)(calendar\.google\.com|outlook\.(live|office|office365)\.com|evite\.com|partiful\.com|"
                          r"paperlesspost\.com|punchbowl\.com|greenvelope\.com|lu\.ma|luma\.com|calendar\.yahoo\.com|"
                          r"icloud\.com)$", re.I)
_CLICK_TELL = _W(r"add to cart|add to bag|unsubscribe|save( draft)?|archive|remove")
_OPENS_COMPOSER = re.compile(r"^(new|create|write|compose|start|add)\b", re.I)


def _compose(fields: dict) -> tuple[list[str], str]:
    ""
    to, lines, body = [], [], []
    for label, value in (fields or {}).items():
        value = " ".join(str(value).split()) if not re.search(r"message|body|comment|reply|post|text|write", label, re.I) \
            else str(value).strip()
        if not value or re.search(r"search|filter|find|password|code", label, re.I):
            continue
        if re.search(r"^(to|cc|bcc|recipients?|send to|add people|people)\b", label, re.I):
            to += _EMAIL.findall(value) or [value]
            lines.append(f"To: {value}")
        elif re.search(r"subject|title", label, re.I):
            lines.append(f"Subject: {value}")
        else:
            body.append(value)
    text = "\n".join(lines) + ("\n\n" if lines and body else "") + "\n\n".join(body)
    return list(dict.fromkeys(to))[:10], text[:4000]




PAY_CLICK_NEVER = ("Zips pay only through the Wallet (use_card, then browser_vault_fill, then the order button), and "
                   "never press a transfer, donation, subscription, upgrade or payment method button. Stop before "
                   "this one and tell Colin it is ready for him.")




_DECLINE = re.compile(r"^(?:no,?\s+)?(?:don[’']?t|do not|never|skip|no thanks|not now|maybe later)\b", re.I)





X_SITES = {"x.com", "twitter.com"}
X_MAX_CHARS = 4000
X_TOO_LONG = ("Not posted: a post can be at most 4,000 characters, with nothing that looks like a password or key, "
              "so Colin's card can show all of it. Shorten it or take that part out.")
_X_POSTING = _W(r"post|post all|reply|repost|undo repost|quote|tweet|retweet")
X_POST_NEVER = ("On X, post, reply and quote only with post_to_x, so Colin sees the exact text on his card. "
                "Reposting from here is off; give Colin the post's link instead.")


def classify_click(label: str, site: str = "", page: dict | None = None) -> Action | None:
    ""


    label = " ".join((label or "").split())
    if not label:
        return None
    page = page or {}
    say = f"Click \"{label[:80]}\"" + (f" on {site}" if site else "")
    if _CLICK_SECURITY.search(label):
        return Action("account", say, site=site, target=site, inputs=label,
                      why_never="Zips never change passwords, 2FA or security settings. Colin does that himself.")
    if consent_base(site) in X_SITES and _X_POSTING.search(label):
        return Action("hardline", say, site=site, target=site, inputs=label, why_never=X_POST_NEVER)
    if consent_base(site) == "instagram.com" and _W(r"share|post|publish|share now").fullmatch(label):
        _, caption = _compose(page.get("fields") or {})
        return Action("publish", "Post on Instagram", site=site, target=site, inputs=caption or label, scope="")


    if (_CLICK_PAY.search(label) or order_click(label)) and not _CLICK_TELL.search(label) \
            and not plain_review_step(label, page):
        return Action("pay", say, site=site, target=site, inputs=label,
                      why_never=PAY_CLICK_NEVER)
    if re.search(r"\b(cart|bag|basket)\b", label, re.I) and not _CLICK_PAY.search(label.replace("checkout", "")):
        return Action("write", say, site=site, target=site, inputs=label)
    if _CLICK_CODE.search(label):
        return None
    if not _CLICK_TERMS.search(label) and (_CLICK_COOKIE.search(label) or (page.get("cookie") and _COOKIE_GENERIC.match(label))):
        return None
    if consent_base(site) == "linkedin.com" and re.match(r"^(connect|invite .* to connect)\b", label, re.I):
        return Action("send", "Send a LinkedIn connection request", site=site, target=site, inputs=label)
    if _RSVP_LABEL.match(label) and (page.get("invite") or _INVITE_SITE.search(site or "")):

        return Action("send", f"Answer an invitation \"{label[:40]}\"" + (f" on {site}" if site else ""), site=site,
                      target=site, inputs=label)
    if _DECLINE.match(label):
        return None
    composed = any(str(v).strip() for k, v in (page.get("fields") or {}).items()
                   if not re.search(r"search|filter|find", k, re.I))
    kind = None
    for pat, k in ((_CLICK_BOOK, "book"), (_CLICK_PUBLISH, "publish"), (_CLICK_SEND, "send"),
                   (_CLICK_SUBMIT, "submit"), (_CLICK_DELETE, "delete"), (_CLICK_ACCOUNT, "account")):
        if pat.search(label):
            kind = k
            break
    if kind == "account" and hard_stops() and _TERMS_ACCEPT.match(label.strip()):
        return Action("write", say, site=site, target=site, inputs=label)
    if kind is None and composed and _CLICK_SEND_WHEN_COMPOSED.search(label) and not _OPENS_COMPOSER.search(label):
        kind = "send"
    if kind is None and composed and _FINISH.match(label):
        kind = "submit"
    if kind is None and page.get("sworn"):


        kind = "submit"
        if label in _NO_NAME:
            label = "a button with no label"
            say = f"Click \"{label}\"" + (f" on {site}" if site else "")
    if kind is None and (_CONFIRM.match(label) or page.get("dialogs") and _CONFIRM_IN_DIALOG.match(label)):
        for about in reversed(page.get("dialogs") or page.get("headings") or []):
            a = classify_click(about, site, {"fields": page.get("fields")})
            if a is not None and a.kind not in ("write", "read", "navigate"):
                a.summary = f"{say}: {' '.join(about.split())[:100]}"
                a.inputs = f"{label}\n\n{about}"[:4000]
                a.scope = ""
                return a
    if kind is None and composed and _CONFIRM_FILLED.match(label) and \
            not any(_SIGN_IN_FIELD.search(k) for k in page.get("fields") or {}):
        kind = "submit"
    if kind is None and _LAST_PRESS.match(label.strip(" .!>›→")) and \
            (page.get("form_end") == "sure" or page.get("form_end") == "filled" and composed):
        kind = "submit"
    if kind in (None, "submit") and (bare_confirm(label) or _FINISH.match(label)) and \
            any(_ORDER_PAGE.search(t) for t in (page.get("dialogs") or []) + (page.get("headings") or [])):
        return Action("pay", say, site=site, target=site, inputs=label,
                      why_never=PAY_CLICK_NEVER)
    if kind in ("publish", "send") and _OPENS_COMPOSER.search(label) and not composed:
        kind = None
    if kind is None:
        return Action("write", say, site=site, target=site, inputs=label) if _CLICK_TELL.search(label) else None
    target, inputs = site, label
    if kind in ("send", "submit", "publish"):
        to, text = _compose(page.get("fields") or {})
        if text:
            inputs = text
        if to:
            target = ", ".join(to)
        if kind == "send" and text:

            if re.search(r"(^|\.)(mail\.google|outlook\.(live|office|office365)|mail\.yahoo|icloud|proton(mail)?|"
                         r"fastmail)\.", site + ".", re.I):
                say = _send_summary("email", to, _subject(text))
            else:
                say = f"Send a message to {', '.join(to)} on {site}" if to else f"Send a message on {site}"
    if kind in ("submit", "book"):


        scope = _site_scope(site)
    else:
        scope = target.lower() if kind == "send" and target != site and "," not in target else ""
    return Action(kind, say, site=site, target=target, inputs=inputs, scope=scope)




_CC_SECOND = {"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc", "or", "ne", "go", "gob", "nic"}


def _site_scope(site: str) -> str:
    ""


    c = _consent()
    if not site or c.is_sensitive(site):
        return ""
    host = site.lower().strip(".")
    if not c._suffix_rules()[0]:


        return host[4:] if host.startswith("www.") else host
    company = c.base(site)
    labels = company.split(".")
    if len(labels) < 2:
        return ""
    if len(labels) == 2 and len(labels[1]) == 2 and labels[0] in _CC_SECOND:
        return ""
    return company


def consent_base(site: str) -> str:
    ""
    parts = (site or "").lower().strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else site
















MAC_INPUT_TOOLS = {"mac_press", "mac_type", "mac_key", "mac_steps", "mac_click"}
_MAC_KINDS = {"send", "submit", "publish", "book", "pay", "hardline"}
_MAC_CONFIRM = re.compile(r"^(confirm|yes,? confirm)\b", re.I)



_MAC_TERMS = re.compile(r"^(i agree|agree|i accept|agree (and|&) continue|accept (the )?terms)$", re.I)
_MAC_FIELD_ROLES = {"TextField", "TextArea", "SearchField", "ComboBox", "SecureTextField"}


_MAC_INERT_ROLES = _MAC_FIELD_ROLES | {"StaticText", "RadioButton", "CheckBox", "Switch", "Tab", "Slider", "Row",
                                       "Cell", "Image", "Heading"}
_MAC_MOVES = re.compile(r"^(go )?(back|forward)$", re.I)
_MESSAGE_BOX = _W(r"message|imessage|reply|comment|chat|compose|write|respond")
_NOT_MESSAGE_BOX = re.compile(r"search|address|url|find|filter|e-?mail|user ?name|password|code|subject", re.I)

_MAC_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789,./;'[]-=`") | {
    "return", "enter", "tab", "space", "delete", "backspace", "escape", "esc", "left", "right", "up", "down",
    "home", "end", "pageup", "pagedown", "forwarddelete", "f1", "f2", "f3", "f4", "f5"}


def mac_step(step) -> tuple[str, dict]:
    ""
    if isinstance(step, dict):
        return str(step.get("action") or ""), step
    raw = str(step or "").strip()
    m = re.fullmatch(r'''(?:type|enter|fill in|write|put)\s+["“'‘](.+?)["”'’]\s+(?:in|into|on)\s+(?:the\s+)?(.+)''',
                     raw, re.I)
    if m:
        return "press", {"name": m[2], "text": m[1]}
    m = re.fullmatch(r'''(?:choose|pick|select)\s+(?:"(.+?)"|“(.+?)”|'(.+?)'|‘(.+?)’|(.+?))\s+(?:in|from|for)\s+'''
                     r'''(?:the\s+)?(.+)''', raw, re.I)
    if m:
        return "press", {"name": m[6], "text": next(v for v in m.groups()[:5] if v is not None)}
    m = re.fullmatch(r'''(?:type|enter)\s+["“'‘](.+?)["”'’]''', raw, re.I)
    if m:
        return "type", {"text": m[1]}
    m = re.fullmatch(r"(?:press|hit)\s+(?:the\s+)?((?:(?:cmd|command|shift|opt|option|alt|ctrl|control)\+)*\S+?)"
                     r"(?:\s+key)?", raw, re.I)
    if m and m[1].lower().split("+")[-1] in _MAC_KEYS:
        return "key", {"keys": m[1]}
    return "press", {"name": re.sub(r"^(?:click|press|tap|open|select|turn|tick|check)\s+", "", raw, flags=re.I)}


def _by_number(args: dict, page: dict) -> bool:
    ""


    return str(args.get("element") or "") in (page.get("refs") or {}) or not str(args.get("name") or "").strip()


def _mac_label(args: dict, page: dict) -> str:
    ""
    if not _by_number(args, page):
        return " ".join(str(args["name"]).split())
    return str((page.get("refs") or {}).get(str(args.get("element") or ""), ""))


def _mac_roles(args: dict, label: str, page: dict) -> set:
    ""

    roles, refs = page.get("roles") or {}, page.get("refs") or {}
    if _by_number(args, page):
        return {roles[str(args.get("element"))]} if str(args.get("element")) in roles else set()
    return {roles[n] for n, words in refs.items() if words.casefold() == label.casefold() and n in roles}


def _mac_is(args: dict, label: str, page: dict, kinds: set) -> bool:
    ""
    roles = _mac_roles(args, label, page)
    return label.endswith(("...", "…")) or bool(roles) and roles <= kinds


def _message_box(label: str) -> bool:
    return bool(label and _MESSAGE_BOX.search(label) and not _NOT_MESSAGE_BOX.search(label))


_RETURN_KEY = re.compile(r"(return|enter)\s*$", re.I)


def _presses_focus(keys: str) -> bool:
    ""

    parts = [p for p in re.split(r"\s*\+\s*", (keys or "").strip().lower()) if p]
    return bool(parts) and parts[-1] in ("return", "enter", "space")


def _sends_line(keys: str) -> bool:
    ""
    parts = [p for p in re.split(r"\s*\+\s*", (keys or "").strip().lower()) if p]
    return bool(parts) and parts[-1] in ("return", "enter") and not set(parts[:-1]) & {"shift", "opt", "option", "alt"}


_CLICK_REACH = 20


def _mac_at(args: dict, page: dict) -> str:
    ""

    if args.get("to_x") is not None or args.get("button") == "right":
        return ""
    try:
        x, y = float(args["x"]), float(args["y"])
    except (KeyError, TypeError, ValueError):
        return ""
    near = [((px - x) ** 2 + (py - y) ** 2, n) for n, (px, py) in (page.get("points") or {}).items()]
    best = min((d for d in near if d[0] <= _CLICK_REACH ** 2), default=None)
    return best[1] if best else ""


def _as_press(action: str, args: dict, page: dict) -> tuple[str, dict]:
    ""
    number = _mac_at(args, page) if action == "click" else ""
    return ("press", {"element": number}) if number else (action, args)


def _unsent(page: dict) -> bool:
    ""
    return any(_message_box(label) and str(text).strip() for label, text in (page.get("typed") or {}).items())


def mac_note(action: str, args: dict, page: dict) -> None:
    ""

    typed = page.setdefault("typed", {})
    action, args = _as_press(action, args, page)
    if action in ("click", "open") or action == "press" and not isinstance(args.get("text"), str) or \
            action == "key" and _presses_focus(str(args.get("keys") or "")):
        page["fresh"] = False
    if action == "press":
        label = _mac_label(args, page)
        if _GOOGLE_BUTTON.search(label) and page.get("site") != GOOGLE_HOST and \
                not isinstance(args.get("text"), str):
            page["maybe_google"] = True
        if isinstance(args.get("text"), str) and label:
            typed.pop(label, None)
            typed[label] = args["text"]
        into = isinstance(args.get("text"), str) or _mac_is(args, label, page, _MAC_FIELD_ROLES)
        page["last"] = label if into else ""
        page["focus"] = label
    elif action == "type":
        label = page.get("focus") or "Typed text"
        typed[label] = typed.pop(label, "") + str(args.get("text") or "")
        page["last"] = label
    elif action == "click" or (action == "key" and not _RETURN_KEY.search(str(args.get("keys") or ""))):
        page["last"] = page["focus"] = ""
    elif action == "open":


        target = str(args.get("target") or "").strip()
        site = _consent().site_of(target) if re.match(r"https?://", target, re.I) else None
        page.update(sworn=False, form_end="", cookie=False, purchase=False,
                    maybe_google=False, scopes=[])
        if site is not None and site != page.get("site"):
            page.update(typed={}, last="", focus="", site=site)
        elif site is None and target.casefold() != str(page.get("app") or "").casefold():
            page.update(typed={}, last="", focus="", site="", app=target)












GOOGLE_HOST = "accounts.google.com"
GOOGLE_TYPE_NEVER = "Google sign-in happens in Colin's own browser. Use the account chooser, never type there."
GOOGLE_READ_FIRST = ("On Google's page, read it with mac_ui first, then press one button by its number, one press at a "
                     "time. I check what each press there shares.")


_GOOGLE_GRANT_PAGE = re.compile(
    r"wants (?:additional |to )?access (?:to )?your google account|this will allow\b|select what .{1,80} can access|"
    r"\bcan access\b.{0,40}\bgoogle account|has requested (?:access|permission)|wants permission|"
    r"google hasn.t verified this app|make sure (?:that )?you trust\b", re.I)


_GOOGLE_BASIC_PAGE = re.compile(r"google will share your name|name, email address|profile picture", re.I)

_GOOGLE_PAGE_WORDS = re.compile(_GOOGLE_GRANT_PAGE.pattern + r"|google will share your name|choose an account", re.I)


_GOOGLE_FREE = re.compile(
    r"^(cancel|back|go back|deny|don.t allow|use another account|remove an account|try another way|learn more.*|"
    r"help|privacy|terms|privacy policy|terms of service|show password|forgot (email|password)\??|"
    r"create account|english.*|sign out)$", re.I)
_GOOGLE_NAMED = re.compile(r"^(?:(.{1,80}?) wants (?:additional |to )?access|this will allow (.{1,80}?) to\b|"
                           r"select what (.{1,80}?) can access|sign in to (.{1,80})$|.*\bto continue to (.{1,80})$)",
                           re.I)


_GOOGLE_DATA = [
    (re.compile(r"\bgmail\b|\b(?:e-?mail|mail) (?:messages?|settings|labels|threads|from)\b|\bdrafts?\b|"
                r"\bsend email\b|mail\.google\.com", re.I), "Gmail"),
    (re.compile(r"\bgoogle drive\b|\bdrive files?\b|/auth/drive", re.I), "Google Drive"),
    (re.compile(r"\bgoogle docs\b|\bdocuments\b|/auth/documents", re.I), "Google Docs"),
    (re.compile(r"\bgoogle sheets\b|\bspreadsheets\b", re.I), "Google Sheets"),
    (re.compile(r"\bgoogle slides\b|\bpresentations\b", re.I), "Google Slides"),
    (re.compile(r"\bcalendars?\b|/auth/calendar", re.I), "Google Calendar"),
    (re.compile(r"\bcontacts\b|/m8/feeds|/auth/contacts|/auth/directory", re.I), "contacts"),
    (re.compile(r"\bphotos\b|/auth/photoslibrary", re.I), "Google Photos"),
    (re.compile(r"\byoutube\b", re.I), "YouTube account"),
    (re.compile(r"\btasks\b|/auth/tasks", re.I), "Google Tasks"),
    (re.compile(r"\bfitness\b|\bgoogle fit\b|\bhealth\b", re.I), "fitness data"),
    (re.compile(r"\bphone numbers?\b|\bbirthday\b|\bgender\b|\bstreet address|\baddresses\b|\bage range\b|"
                r"user\.(?:phonenumbers|birthday|gender|addresses)", re.I), "personal details"),
    (re.compile(r"\bgoogle cloud\b|\bcloud platform\b|/auth/cloud", re.I), "Google Cloud"),
]
_GOOGLE_CHANGES = re.compile(r"\b(edit|create|delete|send|compose|manage|modify|change|upload|organi[sz]e|add|"
                             r"insert|update|remove|write)\b", re.I)

_GOOGLE_BASIC_SCOPES = {"openid", "email", "profile", "https://www.googleapis.com/auth/userinfo.email",
                        "https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/plus.me"}

_GOOGLE_BUTTON = re.compile(r"\b(?:sign|log) ?in with google\b|\bcontinue with google\b|\bgoogle (?:sign|log) ?in\b|"
                            r"^google$", re.I)
_GOOGLE_NAV_KEYS = {"tab", "escape", "esc", "left", "right", "up", "down", "home", "end", "pageup", "pagedown"}


def google_scopes(url: str) -> list[str]:
    ""

    from urllib.parse import parse_qs, urlsplit
    found, seen = [], 0
    while url and seen < 4:
        seen += 1
        try:
            q = parse_qs(urlsplit(url).query)
        except ValueError:
            break
        for raw in q.get("scope", []):
            found += [s for s in re.split(r"[\s+]+", raw) if s and s not in _GOOGLE_BASIC_SCOPES]
        url = next((v for k in ("continue", "followup") for v in q.get(k, []) if "scope=" in v), "")
    return list(dict.fromkeys(found))


def google_page(lines) -> bool:
    ""
    return any(_GOOGLE_PAGE_WORDS.search(str(w)) for w in lines)


def _google_lines(page: dict) -> list[str]:
    ""
    refs, roles = page.get("refs") or {}, page.get("roles") or {}
    return [_EMAIL.sub("", w).strip() for n, w in refs.items()
            if roles.get(n) in ("StaticText", "Heading", "CheckBox", "Cell", "Row") and _EMAIL.sub("", w).strip()]


def _google_app(lines: list[str], page: dict) -> str:
    ""

    if page.get("from_site"):
        return str(page["from_site"])
    for w in lines:
        m = _GOOGLE_NAMED.match(w)
        if m:
            name = next(g for g in m.groups() if g).strip(" .:?!\"'")
            if name and name.lower() not in ("google", "your google account"):
                return name[:60]
    return "this site"


def _google_card(page: dict) -> Action:
    ""
    lines = _google_lines(page)
    app = _google_app(lines, page)
    granted = [w for w in lines if any(rx.search(w) for rx, _ in _GOOGLE_DATA)]
    scopes = page.get("scopes") or []
    what = [name for rx, name in _GOOGLE_DATA if any(rx.search(w) for w in [*granted, *scopes])]
    if what:
        changes = any(_GOOGLE_CHANGES.search(w) for w in granted) or \
            any(not re.search(r"readonly|\.read$", s) for s in scopes)
        summary = f"Let {app} {'read and change' if changes else 'read'} your {_and(what)}"
        inputs = "Google lists:\n" + "\n".join(granted) if granted else \
            f"Google will give {app} access to your {_and(what)}."
    else:
        summary = f"Let {app} use your Google account"
        inputs = ("Google's page says:\n" + "\n".join(lines[:12]) if lines else
                  "I couldn't read what Google will share, so I'm asking first.")




    asked = hashlib.sha256(json.dumps([app, summary, sorted(scopes), granted]).encode()).hexdigest()[:16]
    return Action("account", summary, target=app, site=GOOGLE_HOST, inputs=f"For {app}.\n{inputs}"[:1500],
                  extra={"content_hash": asked})


def google_step(action: str, args: dict, label: str, page: dict) -> Action | None:
    ""

    keys = str(args.get("keys") or "").strip().lower()
    last_key = re.split(r"\s*\+\s*", keys)[-1] if keys else ""
    if action == "type" or isinstance(args.get("text"), str) or \
            action == "key" and last_key not in _GOOGLE_NAV_KEYS and not _presses_focus(keys):
        return Action("secret", "Type on Google's sign-in page", site=GOOGLE_HOST, why_never=GOOGLE_TYPE_NEVER)
    if action == "key" and not _presses_focus(keys) or action == "scroll":
        return None
    if action == "click" and (args.get("to_x") is not None or args.get("button") == "right"):
        return None
    if action not in ("press", "click", "key"):
        return None



    listed = action == "press" and bool(label) and (_by_number(args, page) or bool(_mac_roles(args, label, page)))
    if not listed or not page.get("fresh"):
        return Action("hardline", "Press on Google's page without reading it", site=GOOGLE_HOST,
                      why_never=GOOGLE_READ_FIRST)
    if args.get("action") == "menu" or _EMAIL.search(label) or _GOOGLE_FREE.match(label.strip()) or \
            _mac_is(args, label, page, _MAC_INERT_ROLES):
        return None
    lines = _google_lines(page)
    if any(_GOOGLE_GRANT_PAGE.search(w) for w in lines) or page.get("scopes"):
        return _google_card(page)
    if any(_GOOGLE_BASIC_PAGE.search(w) for w in lines):
        return None
    return _google_card(page)







CLAUDE_ROOT = "~/Projects/"



CLAUDE_BRIEF_MAX = 12000
CLAUDE_FOLDER_NEVER = ("Not started: Claude Code works only in one folder inside ~/Projects on Colin's Mac, written "
                       "as ~/Projects/<folder>. Copy the work into a folder there first, or tell Colin in one "
                       "sentence what you wanted done.")
CLAUDE_BRIEF_NEVER = ("Not started: write the brief as plain instructions of at most 12,000 characters, with nothing "
                      "that looks like a password or key, so Colin's card shows all of it.")
CLAUDE_BRIEF_SHORTER = "Claude Code reads the folder itself, so name the files instead of pasting them."


def _brief_never(never: str, text: str, limit: int = CLAUDE_BRIEF_MAX, shorter: str = CLAUDE_BRIEF_SHORTER) -> str:
    ""


    if len(text) > limit:
        return f"{never} This one has {len(text):,} characters. {shorter}"
    found = _consent().what_looks_secret(text)
    return f"{never} {found}" if found else never


def claude_folder(raw: str) -> str | None:
    ""


    s = str(raw or "").strip().rstrip("/")
    s = re.sub(r"^(?:/Users/[^/\s]+|\$HOME|\$\{HOME\})(?=/)", "~", s)
    if not s.startswith(CLAUDE_ROOT) or re.search(r"[\x00-\x1f$`*?\\]", s):
        return None
    parts = [x for x in s[len(CLAUDE_ROOT):].split("/") if x not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return CLAUDE_ROOT + "/".join(parts)


def classify_mac_claude(args: dict) -> Action:
    ""


    folder = claude_folder(str(args.get("folder") or ""))
    brief = str(args.get("brief") or "").strip()
    if folder is None:
        return Action("agent", "Start Claude Code outside ~/Projects on the Mac", inputs=str(args.get("folder") or ""),
                      why_never=CLAUDE_FOLDER_NEVER)
    if not brief or len(brief) > CLAUDE_BRIEF_MAX or _consent().scrub(brief) != brief:
        return Action("agent", f"Start Claude Code in {folder} on the Mac", target=folder,
                      why_never=_brief_never(CLAUDE_BRIEF_NEVER, brief))
    return Action("write", f"Have Claude Code work in {folder} on the Mac", target=folder, inputs=brief,
                  scope=f"claude:{folder}", extra={"card": True, "claude": folder})







CODEX_REFUSED_FOLDERS = ("~/Projects", "~/Documents", "~/Applications")
CODEX_FOLDER_NEVER = ("Not started: Terra works only in a folder of its own inside the home folder on Colin's Mac, written "
                      "as ~/<folder>, and not in ~/Projects itself, ~/Documents, ~/Library or a hidden folder.")


CODEX_BRIEF_MAX = 100000
CODEX_BRIEF_NEVER = ("Not started: write the words as plain instructions of at most 100,000 characters, with nothing "
                     "that looks like a password or key.")
CODEX_BRIEF_SHORTER = "Terra can open files on the Mac itself, so give a long document's path instead of pasting it."


def codex_folder(raw: str) -> str | None:
    ""

    s = str(raw or "").strip().rstrip("/")
    s = re.sub(r"^(?:/Users/[^/\s]+|\$HOME|\$\{HOME\})(?=/)", "~", s)
    if not s.startswith("~/") or re.search(r"[\x00-\x1f$`*?\\]", s):
        return None
    parts = [x for x in s[2:].split("/") if x not in ("", ".")]
    if not parts or parts[0] == "Library" or any(x.startswith(".") for x in parts):
        return None
    folder = "~/" + "/".join(parts)
    return None if folder in CODEX_REFUSED_FOLDERS else folder


def classify_mac_codex(args: dict) -> Action | None:
    ""
    session, reply = str(args.get("session") or "").strip(), str(args.get("reply") or "").strip()
    if session:
        if not reply:
            return None
        if len(reply) > CODEX_BRIEF_MAX or _consent().scrub(reply) != reply:
            return Action("agent", "Answer Terra on the Mac",
                          why_never=_brief_never(CODEX_BRIEF_NEVER, reply, CODEX_BRIEF_MAX, CODEX_BRIEF_SHORTER))
        return Action("write", "Answer Terra on the Mac", inputs=reply, scope=f"codex:{session}")
    folder = codex_folder(str(args.get("folder") or ""))
    brief = str(args.get("brief") or "").strip()
    if folder is None:
        return Action("agent", "Start Terra outside a folder of its own on the Mac", inputs=str(args.get("folder") or ""),
                      why_never=CODEX_FOLDER_NEVER)
    if not brief or len(brief) > CODEX_BRIEF_MAX or _consent().scrub(brief) != brief:
        return Action("agent", f"Start Terra in {folder} on the Mac", target=folder,
                      why_never=_brief_never(CODEX_BRIEF_NEVER, brief, CODEX_BRIEF_MAX, CODEX_BRIEF_SHORTER))
    return Action("write", f"Have Terra work in {folder} on the Mac", target=folder, inputs=brief, scope=f"codex:{folder}")


def classify_mac(tool_name: str, args: dict, page: dict | None) -> Action | None:
    ""



    return _read_mac(tool_name, args, page)[0]


def mac_blind(tool_name: str, args: dict, page: dict | None) -> bool:
    ""


    return _read_mac(tool_name, args, page)[1]


def _read_mac(tool_name: str, args: dict, page: dict | None) -> tuple[Action | None, bool]:
    page = json.loads(json.dumps(page or {}))
    steps = [mac_step(s) for s in args.get("steps") or []] if tool_name == "mac_steps" else \
        [(tool_name.removeprefix("mac_"), args)]
    sends, blind = [], False
    for action, a in steps:
        a = a if isinstance(a, dict) else {}
        clicked = action == "click"
        action, a = _as_press(action, a, page)
        label, text = _mac_label(a, page), a.get("text")
        unsent = _unsent(page)
        last = page.get("last") or ""

        on_google = str(page.get("site") or "") == GOOGLE_HOST or bool(page.get("maybe_google"))
        grant = google_step(action, a, label, page) if on_google else None
        mac_note(action, a, page)
        site = str(page.get("site") or "")
        found = None


        if on_google and grant is not None:
            found = grant
        elif clicked and action == "click":
            if unsent and a.get("to_x") is None and a.get("button") != "right":

                found = classify_click("Send", site, {"fields": page["typed"]})
                if found is not None:
                    found.extra["hint"] = ("If that point is not the message's Send button, read the window with "
                                           "mac_ui and press the control you meant by its number.")
        elif action == "press" and not isinstance(text, str) and a.get("action") != "menu" and label and \
                not _MAC_MOVES.match(label) and not _mac_is(a, label, page, _MAC_INERT_ROLES):
            found = classify_click(label, site, {"fields": page["typed"], "sworn": page.get("sworn"),
                                                 "form_end": page.get("form_end"), "cookie": page.get("cookie")})
            if found is None and _MAC_CONFIRM.match(label):
                _, written = _compose(page["typed"])
                found = Action("submit", f"Click \"{label[:80]}\"" + (f" on {site}" if site else ""), site=site,
                               target=site, inputs=written or label, scope=_site_scope(site))
            if found is not None and found.kind == "pay" and _mac_roles(a, label, page) == {"Link"} and \
                    not order_click(label):
                found = None
        elif _message_box(page.get("last") or "") and (
                action == "key" and _sends_line(str(a.get("keys") or "")) or
                action in ("press", "type") and isinstance(text, str) and ("\n" in text or "\r" in text)):
            found = classify_click("Send", site, {"fields": page["typed"]})
        if found is not None and (found is grant or found.kind in _MAC_KINDS or
                                  found.kind == "account" and _MAC_TERMS.match(label)):
            if not site:
                found.summary = re.sub(r" on $", "", found.summary) + " on the Mac"
            sends.append(found)
            page.update(typed={}, last="")
            if clicked and action == "click":
                blind = True
        elif clicked and action == "click" or action == "press" and not label or not last and (
                action == "key" and _presses_focus(str(a.get("keys") or "")) or
                action == "type" and bool(re.search(r"\s", str(text or "")))):
            blind = True
    refused = next((f for f in sends if floor(f.kind) == "never"), None)
    found = refused or _combine(sends, "\n\n".join(f.inputs for f in sends))
    if found is not None:


        found.extra["send_sites"] = sorted({f.site for f in sends if f is not None})
    return found, blind










JOB_TOOLS = {"cronjob", "cronjob_manage"}
_SCRIPTS_PATH = re.compile(r"(?:~|\$HOME|\$\{HOME\}|/home/[^/\s'\"]+|/Users/[^/\s'\"]+)/\.taffy/(?:profiles/[^/\s'\"]+/)?"
                           r"scripts/[^\s'\";&|<>]+")
_FILE_RUNNER = re.compile(r"^(?:(?:ba|z|da|k)?sh|source|\.|python[\d.]*|pypy[\d.]*|node|perl|ruby|deno|bun|php|tsx|ts-node)$")
_SYSTEM_BIN = re.compile(r"^(?:/(?:usr|bin|sbin|opt|snap|nix|lib|etc)/|~/\.local/bin/|~/\.cargo/bin/|~/go/bin/|~/\.npm-global/)")


@dataclass
class JobScripts:
    ""

    detect: object
    read: object
    users: object = lambda path: []
    name_of: object = lambda job_id: None
    job_of: object = lambda job_id: None


def _runs_file(words: list[str]) -> str | None:
    ""

    words = _strip_wrappers(words)
    if not words:
        return None
    head = words[0].rsplit("/", 1)[-1]
    if _FILE_RUNNER.match(head):
        args = words[1:]
        if head in ("deno", "bun") and args[:1] == ["run"]:
            args = args[1:]
        for a in args:
            if a in ("-", "-m", "--eval") or re.fullmatch(r"-[A-Za-z]*[ce][A-Za-z]*", a) and head not in ("source", "."):
                return None
            if not a.startswith("-"):
                return a
        return None
    w = words[0]
    if w.startswith(("./", "../", "~/", "$HOME/", "/")) and not _SYSTEM_BIN.match(w):
        return w
    return None


def _lower(text: str) -> str:
    return text[:1].lower() + text[1:] if text[1:2].islower() or text[1:2] == " " else text


def _and(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def judge_script(text: str, name: str, jobs: JobScripts | None) -> tuple[Action | None, list[str]]:
    ""

    shell = name.lower().endswith((".sh", ".bash")) or "\n" in name and not re.match(r"#!\S*python", text)
    detect = jobs.detect if jobs is not None else hermes_like
    found = [classify_command(text, code=not shell, detect=None if shell else detect)]
    commands = [text] if shell else [c for c, _unsure in _code_commands(text)]
    if shell:
        flagged = unjudged(text, None, detect)
        if flagged and flagged not in _ARTIFACTS:
            found.append(_flagged_action(flagged, text, text))
    others = [f for c in commands for seg, _here in _segments(c, None) for f in [_runs_file(_words(seg))] if f]
    return _combine(found, text), list(dict.fromkeys(others))


def _script_card(title: str, paths: list[str], texts: list[tuple[str, str | None]], schedule: str, parts: list[Action],
                 others: list[str]) -> Action:
    ""

    lines = [f"It runs by itself on this schedule: {schedule}." if schedule else "It runs by itself on a schedule.",
             "Nothing checks each step when it runs."]
    if parts:
        lines.append(f"Each time, its script will {_and([_lower(a.summary) for a in parts])}.")
    unread = [p for p, t in texts if t is None and p in paths]
    if unread:
        lines.append(f"Its script ({', '.join(unread)}) could not be read, so what it does is not known.")
    if others:
        lines.append(f"It also runs {_and(others)}, which can change later without another card.")
    shown = "\n\n".join(f"{p}:\n{t}" for p, t in texts if t is not None)
    digest = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest()

    return Action("schedule", title, target=", ".join(paths)[:300], inputs=" ".join(lines), details=shown[:4000],
                  extra={"content_hash": digest, "script_known": not unread and not others})





SCRIPT_DEPTH = 3
SCRIPT_FILES = 8


def _judge_job_scripts(title: str, told: str, paths: list[str], texts: list[tuple[str, str | None]],
                       schedule: str, jobs: JobScripts | None) -> Action:
    ""
    parts, others, seen = [], [], {p for p, _t in texts}
    depth = {p: 0 for p, _t in texts}
    unresolved = False
    i = 0
    while i < len(texts):
        p, text = texts[i]
        i += 1
        if text is None:
            continue
        a, more = judge_script(text, p, jobs)
        if a is not None and floor(a.kind) == "never":
            return Action(a.kind, f"{title}: {_lower(a.summary)}", inputs=text,
                          why_never="A scheduled job's script runs with no checks, so it may not do anything Zips "
                                    "never do. " + (a.why_never or "Tell Colin in one sentence what you wanted it to do."))
        if a is not None and floor(a.kind) == "ask":
            parts.append(a)
        for f in more:
            others.append(f)
            if f in seen:
                continue
            if depth[p] + 1 >= SCRIPT_DEPTH or len(texts) >= SCRIPT_FILES:
                unresolved = True
                continue
            seen.add(f)
            depth[f] = depth[p] + 1
            texts.append((f, jobs.read(f, False) if jobs is not None else None))


    if parts or unresolved or (others and not hard_stops()) or any(t is None for _p, t in texts):
        return _script_card(title, paths, texts, schedule, parts, others)
    return Action("write", told)


_JOB_CHANGES = {"remove": "Remove", "delete": "Remove", "pause": "Pause", "resume": "Resume", "run": "Run",
                "run_now": "Run", "trigger": "Run"}



SCHEDULE_WORDS = re.compile(
    r"\b(every|each|daily|weekly|monthly|hourly|nightly|morning|evening|tonight|tomorrow|weekday|weekend|"
    r"remind|reminder|schedul\w*|recurring|routine|cron|job|automat\w*|keep (an )?eye|keep checking|keep track|"
    r"watch|monitor|track|follow up|check (back|in|again|on)|let me know (when|if)|tell me (when|if)|alert|notify|"
    r"pause|resume|stop|cancel|turn off|turn on|delete|remove|run (it|that|the)|at \d|\d ?(am|pm))\b"
    r"|\d ?[ap]\.m\."


    r"|\bin (\d+|an?|a few|a couple( of)?) ?(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|wks?|weeks?)\b"
    r"|\b(ping|nudge) me\b|\blater (today|tonight|this (morning|afternoon|evening|week))\b"



    r"|\bwhenever\b|\bkeep(s|ing)? on top of\b|\bkeep(s|ing)? (on|after) (them|these|those|him|her|it|both)\b"
    r"|\bstay(ing)? on top of\b|\b(get|gets|getting) back to me\b|\bhear(s)? back\b",
    re.I)






_PASTED = re.compile(r"^\s*(>|from:|to:|subject:|date:|sent:|cc:|-{3,}|_{3,}|={3,}|begin forwarded|-+ ?original message)",
                     re.I | re.M)


def asks_for_schedule(text: str) -> bool:
    text = text or ""
    if _PASTED.search(text):
        return False
    return bool(SCHEDULE_WORDS.search(text))





_NOT_A_CHANGE = {"action", "job_id", "id", "deliver"}


def text_only(job: dict) -> bool:
    ""


    tools = job.get("enabled_toolsets")
    return (isinstance(tools, list) and not any(str(t).strip() for t in tools) and not job.get("no_agent")
            and not any(str(job.get(k) or "").strip() for k in ("script", "monitor", "monitor_script", "monitor_url")))


def own_follow_up(job: dict) -> bool:
    ""




    if job.get("no_agent") or any(str(job.get(k) or "").strip() for k in ("script", "monitor", "monitor_script", "monitor_url")):
        return False
    schedule = job.get("schedule")
    if isinstance(schedule, dict):
        return schedule.get("kind") == "once"
    return schedule_words(str(schedule or "")).startswith("once, ")


_UNITS = {"m": "minute", "h": "hour", "d": "day"}
_DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


_DAY_WORDS = {**{d.lower(): str(i) for i, d in enumerate(_DAY_NAMES)},
              **{d.lower()[:3]: str(i) for i, d in enumerate(_DAY_NAMES)},
              "tues": "2", "weds": "3", "thur": "4", "thurs": "4",
              "day": "*", "daily": "*", "everyday": "*", "weekday": "1-5", "weekdays": "1-5",
              "weekend": "0,6", "weekends": "0,6"}
_DAY_FIELDS = {"*": "day", "1-5": "weekday", "0,6": "weekend day", "6,0": "weekend day"}


def _clock(hour: int, minute: int) -> str:
    return f"{(hour % 12) or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _clock_of(text: str) -> tuple[int, int] | None:
    ""
    t = text.lower().replace(" ", "")
    if t in ("noon", "midday"):
        return 12, 0
    if t == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", t)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if m.group(3) == "pm" else 0)
    return (hour, minute) if hour < 24 and minute < 60 else None


def _natural_cron(text: str) -> str | None:
    ""



    tokens = re.sub(r"^every\s+", "", text.lower()).replace(",", " ").split()
    days, i = [], 0
    while i < len(tokens) and (tokens[i] in _DAY_WORDS or (tokens[i] == "and" and days)):
        if tokens[i] != "and":
            days.append(_DAY_WORDS[tokens[i]])
        i += 1
    if not days or ("*" in days or "1-5" in days or "0,6" in days) and len(days) > 1:
        return None
    rest = tokens[i + 1:] if tokens[i:i + 1] == ["at"] else tokens[i:]
    when = _clock_of(" ".join(rest)) if rest else None
    if when is None:
        return None
    return f"{when[1]} {when[0]} * * {','.join(dict.fromkeys(days))}"


def _cron_words(expr: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}) \* \* \* \*", expr)
    if m and int(m.group(1)) < 60:
        return f"every hour at :{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{1,2}) (\d{1,2}) \* \* (\*|[0-6](?:-[0-6])?(?:,[0-6])*)", expr)
    if not m or int(m.group(1)) >= 60 or int(m.group(2)) >= 24:
        return None
    field = m.group(3)
    day = _DAY_FIELDS.get(field)
    if day is None and "-" not in field:
        names = [_DAY_NAMES[int(d)] for d in dict.fromkeys(field.split(","))]
        day = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    if day is None:
        return None
    return f"every {day} at {_clock(int(m.group(2)), int(m.group(1)))}"


def schedule_words(schedule: str) -> str:
    ""


    from datetime import datetime
    from zoneinfo import ZoneInfo
    from . import home
    s = " ".join(str(schedule or "").split())
    if not s:
        return ""
    m = re.fullmatch(r"(every |in )?(\d*) ?(m|h|d)(?:in|ins|inute|inutes|r|rs|our|ours|ay|ays)?", s, re.I)
    if m and (m.group(2) or m.group(1)):
        n, unit = int(m.group(2) or 1), _UNITS[m.group(3).lower()]
        span = f"{n} {unit}" + ("s" if n != 1 else "")
        if (m.group(1) or "").lower() == "in ":
            return f"once, in {span}"
        return f"every {span}" if n != 1 else f"every {unit}"
    try:
        when = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        when = None
    if when is not None:
        tz = ZoneInfo(home.timezone())
        when = when.astimezone(tz) if when.tzinfo else when
        return f"once, on {when:%A, %B} {when.day} at {_clock(when.hour, when.minute)}"
    expr = _natural_cron(s) or s
    return _cron_words(expr) or f"on the schedule {s}"


def _job_when(job: dict) -> str:
    schedule = job.get("schedule")
    if isinstance(schedule, dict):
        schedule = schedule.get("run_at") or schedule.get("expr") or schedule.get("display")
    return schedule_words(str(schedule or ""))


def _job_card_text(job: dict) -> str:
    ""
    when = _job_when(job)
    prompt = " ".join(str(job.get("prompt") or "").split())[:600]
    out = [f"It runs {when}." if when else ""]
    if prompt:
        out.append(f"It posts in this chat, written from these instructions: \u201c{prompt}\u201d" if text_only(job)
                   else f"Each time, it follows these instructions and can use its tools: \u201c{prompt}\u201d")
    return " ".join(x for x in out if x)


def classify_job(args: dict, jobs: JobScripts | None) -> Action | None:
    ""

    verb = str(args.get("action") or "").strip().lower()
    if verb in ("list", ""):
        return None
    if verb not in ("create", "add", "update", "edit"):



        ref = str(args.get("job_id") or args.get("id") or "").strip()
        what = str(args.get("name") or (jobs.name_of(ref) if jobs is not None and ref else None) or "").strip()[:120]
        what = what or ("" if re.fullmatch(r"[0-9a-f]{8,}", ref) else ref[:120])
        label = _JOB_CHANGES.get(verb, "Change")
        return Action("write", f"{label} a scheduled job" + (f": {what}" if what else ""))
    creating = verb in ("create", "add")
    ref = str(args.get("job_id") or args.get("id") or "").strip()
    if not creating and all(v is None for k, v in args.items() if k not in _NOT_A_CHANGE):
        return None
    saved = (jobs.job_of(ref) if jobs is not None and ref and not creating else None) or {}
    job = {**saved, **{k: v for k, v in args.items() if v is not None}}
    name = " ".join(str(job.get("name") or "").split())
    what = name or " ".join(str(job.get("prompt") or "").split())
    told = ("Set up a scheduled job" if creating else "Change the scheduled job") + (f": {what[:120]}" if what else "")
    paths = [str(args[k]).strip() for k in ("script", "monitor_script", "monitor")
             if isinstance(args.get(k), str) and args[k].strip() and not re.match(r"^https?://", args[k].strip())]
    if not paths:

        facts = {"name": name, "when": _job_when(job), "creating": creating,
                 "prompt": " ".join(str(job.get("prompt") or "").split())[:600]}
        return Action("write", told, inputs=_job_card_text(job),
                      extra={"text_only": text_only(job), "job": facts, "follow_up": own_follow_up(job)})
    texts = [(p if "\n" not in p else "the script", p if "\n" in p else (jobs.read(p, True) if jobs is not None else None))
             for p in paths]
    title = (f"Set up \"{name}\" to run on its own" if name else "Set up a script to run on its own") if creating else \
        (f"Change the script \"{name}\" runs on its own" if name else "Change the script a scheduled job runs on its own")
    return _judge_job_scripts(title, told, [p for p, _t in texts], texts, str(args.get("schedule") or "").strip(),
                              jobs)


def _job_script(path: str, cwd: str | None, jobs: JobScripts) -> tuple[str, list[str]] | None:
    ""
    n = _norm(path, cwd) if path else None
    if not n or not _SCRIPTS_PATH.fullmatch(n.replace("$HOME", "~")):
        return None
    users = list(jobs.users(n) or [])
    return (n, users) if users else None


def _script_edit(path: str, users: list[str], text: str | None, jobs: JobScripts, cmd: str = "") -> Action:
    job = users[0]
    title = f"Change the script \"{job}\" runs on its own"
    if text is None:
        return Action("schedule", title, target=path, details=cmd,
                      inputs=f"\"{job}\" runs this script by itself on its schedule, and nothing checks each step when "
                             "it runs. What the script will do after this change is not known.")
    a = _judge_job_scripts(title, title, [path], [(path, text)], "", jobs)
    if a.kind == "schedule" and cmd:
        a.details = (cmd + "\n\n" + a.details).strip()[:4000] if text not in cmd else cmd[:4000]
    return a


def classify_script_edit(tool_name: str, args: dict, cwd: str | None, jobs: JobScripts | None) -> Action | None:
    ""

    if jobs is None:
        return None
    found = []
    if tool_name in ("write_file", "patch"):
        targets = [str(args.get("path") or "")]
        if tool_name == "patch" and args.get("patch"):
            targets = re.findall(r"(?m)^\*\*\* (?:Update|Add) File:\s*(.+?)\s*$", str(args.get("patch")))
        for t in targets:
            hit = _job_script(t, cwd, jobs)
            if hit is None:
                continue
            if tool_name == "write_file":
                text = str(args.get("content") or "")
            else:
                now = jobs.read(hit[0], False) or ""
                old, new = str(args.get("old_string") or ""), str(args.get("new_string") or "")
                added = "\n".join(ln[1:] for ln in str(args.get("patch") or "").splitlines() if ln.startswith("+"))
                text = now.replace(old, new) if old and old in now else (now + "\n" + (new or added))
            found.append(_script_edit(hit[0], hit[1], text, jobs))
        return _combine(found, json.dumps(args, ensure_ascii=False, default=str)[:4000])
    cmd = str(args.get("command") or "")
    bodies = [m.group(3) for m in _HEREDOC.finditer(cmd)]
    for seg, here in _segments(cmd, cwd):
        words = _words(seg)
        head = words[0].rsplit("/", 1)[-1] if words else ""
        targets = _write_targets(seg)
        if head == "sed" and any(w.startswith("-i") or w == "--in-place" for w in words):
            targets += [w for w in words[2:] if not w.startswith("-")]
        for t in targets:
            hit = _job_script(t, here, jobs)
            if hit is None:
                continue
            text = None
            if head in ("cat", "tee") and len(bodies) == 1:
                text = bodies[0]
            elif head in ("echo", "printf"):
                m = _ECHO_TEXT.search(seg)
                text = next((g for g in m.groups() if g is not None), "").replace("\\n", "\n") if m else None
            elif head in ("cp", "install") and len([w for w in words[1:] if not w.startswith("-")]) == 2:
                text = jobs.read(_norm([w for w in words[1:] if not w.startswith("-")][0], here) or "", False)
            found.append(_script_edit(hit[0], hit[1], text, jobs, cmd))
    return _combine(found, cmd)





_ACT_TOOLS = {"report_problem", "web_search", "web_extract", "read_file", "search_files", "browser_snapshot", "browser_back",
              "browser_scroll", "browser_get_images", "browser_vision", "vision_analyze", "session_search",
              "team_chats",
              "memory", "todo", "clarify", "skill_view", "skills_list", "team_note", "save_fact", "recall", "ask_teammate",
              "tell_teammate", "react", "offer_choices", "offer_project", "ask_to_sign_in", "ask_to_connect", "ask_for_code", "close_code_card", "close_sign_in_card", "close_card", "explain_card", "share_screen", "share_result", "session_card", "card", "todo_list", "app_todo_list", "work_project", "code_session", "tally", "login_note",
              "write_file", "patch", "process_manage", "delegate_task", "text_to_speech", "browser_navigate", "browser_download",
              "browser_vault_list", "browser_vault_fill", "browser_vault_enter_code", "enter_texted_code", "browser_console",
              "team_propose", "team_start", "team_milestone", "team_need", "team_post"}










_JS_HIDES = re.compile(r"\beval\s*\(|\bnew\s+Function\s*\(|\bFunction\s*\(\s*['\"`]|"
                       r"\[\s*['\"`][^\]]*['\"`]\s*\+|\+\s*['\"`][^'\"`]*['\"`]\s*\]|atob\s*\(|fromCharCode|"
                       r"String\.raw|unescape\s*\(|decodeURIComponent\s*\([^)]*\)\s*\)", re.I)
_PAY_WORDS = re.compile(r"place[ _-]?order|pay[ _-]?now|\bpay\b|purchase|buy[ _-]?now|check[ _-]?out|complete[ _-]?"
                        r"(order|purchase)|confirm[ _-]?(order|payment)|submit[ _-]?payment|card[ _-]?number|\bcvv\b|"
                        r"\bcvc\b", re.I)
_CARD_DIGITS = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn(digits: str) -> bool:
    ""
    total, odd = 0, True
    for ch in reversed(digits):
        d = int(ch)
        if not odd:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        odd = not odd
    return total % 10 == 0


def _has_card_number(code: str) -> bool:
    for m in _CARD_DIGITS.finditer(code):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits) and not digits.startswith(("16", "17", "19", "20")):
            return True
    return False



_CDP_READS = re.compile(r"^(?!(?:Network\.(?:getCookies|getAllCookies)|Storage\.getCookies|DOMStorage\.|IndexedDB\.|CacheStorage\.))"
                        r"((DOM|CSS|Accessibility|DOMSnapshot|Page|Network|Target|Browser|Storage|Log|Performance|"
                        r"LayerTree|Memory|Overlay)\.(get|describe|query|search|capture|request(Node|ChildNodes)?|"
                        r"collect|enable|disable|resolveNode|pushNodes?\w*)\w*|Page\.(navigate|getFrameTree|"
                        r"getNavigationHistory|getLayoutMetrics|printToPDF|captureScreenshot|stopLoading)|"
                        r"Target\.(getTargets|getTargetInfo|attachToTarget|detachFromTarget|setDiscoverTargets|"
                        r"createTarget|activateTarget|closeTarget)|Runtime\.(evaluate|callFunctionOn|getProperties|"
                        r"releaseObject|releaseObjectGroup|enable|disable|awaitPromise))$", re.I)
NEVER_HIDDEN = ("Not done: this code is written so its effect can't be read (built-up names, eval, encoded text). Use "
                "plain code that reads the page, or the page's own buttons and fields.")
NEVER_EXEC_ACT = ("Not done: browser code is only for reading a page, and this code does more than read (or uses "
                  "something the gate can't tell is reading). To click, type, choose or submit, use browser_click "
                  "and browser_type, which work the same way on every site. To read, use browser_snapshot, or plainer "
                  "code that only reads text, links and values. To read another page on this site, use a plain GET "
                  "and read the reply on the same line: (await fetch('/page')).text(), or .json(). Start an address you "
                  "build with location.origin: fetch(location.origin + '/page?id=' + id). Build results with map, a "
                  "string (out += line), or your own new list: const out = [] at the top, then out.push(line).")
NEVER_PAY = ("Not done: browser code can't pay or place an order. Pay through the Wallet: use_card, then "
             "browser_vault_fill, then press the order button with browser_click.")


_QUOTED = re.compile(r"'([^'\\\n]*)'|\"([^\"\\\n]*)\"")


def _joined_strings(code: str) -> str:
    ""

    import ast
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return "".join(single or double for single, double in _QUOTED.findall(code))
    parts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append((getattr(node, "lineno", 0), getattr(node, "col_offset", 0), node.value))
    return "".join(v for _l, _c, v in sorted(parts))




def classify_script(tool_name: str, args: dict, site: str = "") -> Action | None:
    ""

    from . import script_gate

    def same_site(url: str) -> bool:
        return bool(site) and _consent().site_of(url) == site
    if tool_name == "browser_console":
        written = str(args.get("expression") or "")
        verdict, scripts = script_gate.js_verdict(written, same_site), [written]
    elif tool_name == "browser_cdp":
        method = str(args.get("method") or "").strip()
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        written = " ".join(str(params.get(k) or "") for k in ("expression", "functionDeclaration", "source", "text"))
        url = str(params.get("url") or "")
        if not _CDP_READS.match(method) or (url and not re.match(r"https?://", url.strip())):
            verdict = "act"
        elif method.lower().startswith("runtime.") and written.strip():
            verdict = script_gate.js_verdict(written, same_site)
        else:
            verdict = "read"
        scripts = [written]
    else:
        written = str(args.get("code") or "")
        verdict, scripts = script_gate.exec_verdict(written, lambda m: bool(_CDP_READS.match(m)), same_site)
    if verdict == "read":
        return None
    text = written + "\n" + "\n".join(scripts) + "\n" + _joined_strings(written)
    text = text + "\n" + re.sub(r"""['"`]\s*\+\s*['"`]""", "", text)
    where = site or "this page"
    if _JS_HIDES.search(text):
        return Action("hardline", f"Run browser code on {where} that hides what it does", site=site,
                      why_never=NEVER_HIDDEN)
    if _has_card_number(text):
        return Action("secret", f"Type a card number on {where}", site=site, why_never=TYPE_SECRET_NEVER)
    if verdict == "act" and _PAY_WORDS.search(text):
        return Action("pay", f"Pay or place an order on {where}", site=site, why_never=NEVER_PAY)
    if verdict == "act":



        return Action("hardline", f"Act on {where} through browser code", site=site, why_never=NEVER_EXEC_ACT)
    return Action("write", f"Fill in fields on {where}", site=site, target=site, inputs=written[:2000])




SCREEN_LOOKS = {"capture", "wait", "list_apps", "list_windows"}
SCREEN_UNATTENDED = ("clicking and typing on the screen waits for Colin, and nobody can answer in a board task or a "
                     "scheduled run. On a web page, use browser_click and browser_type. For anything else, finish the "
                     "rest and name the one step left in your result.")



SECRET_FIELD = re.compile(r"password|passcode|security code|one-time code", re.I)
TYPE_SECRET_NEVER = ("Never type a password or code yourself. Saved logins are filled with browser_vault_fill, and 2FA "
                     "with browser_vault_enter_code, or with ask_for_code and enter_texted_code when the site texts a code.")


def classify(tool_name: str, args: dict, click_label: str = "", site: str = "", cwd: str | None = None,
             page: dict | None = None, jobs: JobScripts | None = None, unattended: bool = False) -> Action | None:
    ""





    args = args if isinstance(args, dict) else {}
    if tool_name == "computer_use" and unattended and str(args.get("action") or "") not in SCREEN_LOOKS:
        return Action("hardline", "Click or type on the screen with nobody to ask", why_never=SCREEN_UNATTENDED)
    if tool_name == "email":
        from . import email_tool
        return email_tool.classify(args)
    if tool_name == "errands":
        from . import errands
        return errands.classify(args)
    if tool_name == "calendar":
        from . import calendar_tool
        return calendar_tool.classify(args)
    if tool_name == "reminders":
        from . import reminders_tool
        return reminders_tool.classify(args)
    if tool_name == "watch_texts":
        from . import watch_texts_tool
        return watch_texts_tool.classify(args)
    if tool_name == "contacts":
        from . import contacts_tool
        return contacts_tool.classify(args)
    if tool_name == "share_page":
        from . import pages_tool
        return pages_tool.classify(args)
    if tool_name in ("bank", "bills", "make_media"):


        return None
    if tool_name in ("trello", "drive", "outlook", "dropbox", "notion", "linear", "github", "posthog", "railway",
                     "cloudflare", "vercel", "stripe"):

        from . import apps_tool
        return apps_tool.classify(tool_name, args)
    if tool_name in ("terminal", "execute_code"):
        folder = str(args.get("workdir")) if args.get("workdir") else cwd
        cmd = str(args.get("command") or args.get("code") or "")
        a = classify_command(cmd, cwd=folder, code=tool_name == "execute_code",
                             detect=jobs.detect if jobs is not None and tool_name == "execute_code" else None)
        if unattended and (a is None or floor(a.kind) != "never"):




            shown = cmd if tool_name == "terminal" else "python3 - <<'PY'\n" + cmd + "\nPY"
            flagged = unjudged(shown, folder, jobs.detect if jobs is not None else None)
            if flagged and flagged not in _ARTIFACTS:
                a = _combine([a, _flagged_action(flagged, cmd, cmd)], cmd)
        if tool_name == "terminal" and (a is None or floor(a.kind) != "never"):
            edit = classify_script_edit(tool_name, args, folder, jobs)
            if edit is not None:

                return edit if a is None or floor(edit.kind) == "never" else _combine([edit, a], str(args.get("command")))
        return a
    if tool_name in MAC_INPUT_TOOLS:
        return classify_mac(tool_name, args, page)
    if tool_name == "mac_claude":
        return classify_mac_claude(args)
    if tool_name == "mac_codex":
        return classify_mac_codex(args)
    if tool_name == "mac_run":
        a = classify_command(str(args.get("command") or ""), where=MAC)
        if a is not None and "the Mac" not in a.summary:
            a.summary += " on the Mac"
        return a
    if tool_name == "connectors":
        if args.get("read") is not None and not str(args.get("request") or "").strip():


            return None
        guests, notify = calendar_fields(args)
        a = classify_connector(str(args.get("service") or ""), str(args.get("request") or ""), guests, notify,
                               connector_fields(args))
        return None if a.kind == "read" else a
    if tool_name == "send_message":
        target = str(args.get("target") or args.get("chat_id") or args.get("platform") or "")
        return Action("send", f"Send a message to {target or 'someone'}", target=target,
                      inputs=str(args.get("message") or args.get("text") or ""), scope=target)
    if tool_name == "browser_click":
        return classify_click(click_label, site, page)
    if tool_name == "browser_type" and _consent().site_of(site) == GOOGLE_HOST:
        return Action("secret", "Type on Google's sign-in page", site=site, why_never=GOOGLE_TYPE_NEVER)
    if tool_name == "browser_type" and SECRET_FIELD.search(click_label):
        return Action("secret", f"Type into the \"{click_label[:60]}\" field", site=site,
                      why_never=TYPE_SECRET_NEVER)
    if tool_name in ("browser_exec", "browser_cdp") or (tool_name == "browser_console" and args.get("expression")):
        return classify_script(tool_name, args, site)
    if tool_name in JOB_TOOLS:
        return classify_job(args, jobs)
    if tool_name in ("write_file", "patch"):
        return classify_script_edit(tool_name, args, cwd, jobs)
    if tool_name == "service_api":

        from . import service_tool
        return service_tool.classify(args)
    if tool_name == "post_to_x":

        reply, text = str(args.get("reply_to") or "").strip(), str(args.get("text") or "")
        if len(text) > X_MAX_CHARS or _consent().scrub(text) != text:


            return Action("hardline", "Post on X", site="x.com", why_never=X_TOO_LONG)
        return Action("publish", "Reply on X" if reply else "Post on X", target="x.com", site="x.com",
                      inputs=str(args.get("text") or ""), details=f"In reply to {reply}" if reply else "",
                      extra={"x_post": reply or True})
    if tool_name == "team_site_publish":


        folder = str(args.get("folder") or "")
        check = args.get("check") if isinstance(args.get("check"), dict) else None
        return Action("publish", "Put the team's site online", target=folder, site="netlify.com", inputs=folder,
                      details=json.dumps(check, ensure_ascii=False)[:4000] if check else "")
    return None
