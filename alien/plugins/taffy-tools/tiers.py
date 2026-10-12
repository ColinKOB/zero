"""Permission tiers over actions (docs/research/2026-10-08-smart-permissions.md, approved by Colin on Oct 8).

Every tool call is an action of some kind. The hard rules below set its tier from what code can see:

  act    do it, nothing to say
  tell   do it, then say so in one line (and it shows in the app's activity)
  ask    a one-tap card first; the Zip stops and waits for Colin's answer
  never  the Zip does not do it and says why; no card, because a tap must not be able to unlock it

The judge in gate.py may only raise a tier, and only in the two places Fable's plan names: a scheduled write
the routine's own words might not cover, and an action right after the Zip read content Colin did not write.
It can never produce or unlock "never".

classify() turns a tool call into an Action. It reads terminal and code commands (including commands run on
Colin's Mac), connector requests, browser clicks (by the clicked element's label) and the plugin's own tools.
It is a pattern match, not a sandbox: docs/qa/2026-10-08-internals-fixes-r1.md lists what it cannot see.
"""

import contextvars
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
    """consent.py, for card titles shared with the hub (plain_command)."""
    from . import consent
    return consent
RANK = {t: i for i, t in enumerate(TIERS)}

# Kinds that always need Colin's yes for that specific action. "system" is changing the computer itself (software,
# services, user accounts, files outside the Zips' folders) with administrator rights, and "schedule" is a job that
# runs a script on its own, which nothing checks when it runs (Oct 8 QA round 6, M1 and L4).
ASK_KINDS = {"send", "submit", "publish", "delete", "book", "account", "mac", "system", "schedule"}
# Kinds a Zip never does. "agent" is starting another assistant or changing the Zips' own settings from a terminal;
# "hardline" is what Hermes refuses outright (wiping a system folder, erasing a disk, shutting the computer down).
NEVER_KINDS = {"pay", "secret", "school", "agent", "hardline"}
# Kinds whose card may offer "Always allow" (a rule with a scope). Never pay, secret, permanent delete, account
# security or anything content-originated.
RULE_KINDS = {"signin", "send", "write"}

SECURITY = re.compile(r"password|passcode|2-step|two-factor|2fa|mfa|security (settings|key|question)|recovery (code|email|phone)", re.I)


@dataclass
class Action:
    kind: str                 # read, navigate, signin, write, send, submit, publish, delete, book, account, pay, secret,
                              # mac, system, schedule, agent, hardline
    summary: str              # what the Zip is about to do, in a short plain sentence: the card's title and the push
    target: str = ""          # who or what it is aimed at: a recipient, a branch, a site, a path
    site: str = ""            # the site involved, when there is one
    inputs: str = ""          # what Colin sees on the card (the message text, the request), scrubbed by the gate
    scope: str = ""           # what an "Always allow" rule would cover (one recipient, a routine), empty for none
    why_never: str = ""       # the refusal for a never action
    details: str = ""         # the exact command, shown behind Details on the card when it is not the inputs
    extra: dict = field(default_factory=dict)

    def describe(self) -> str:
        """The summary with the request or text behind it, for the judge and the activity list."""
        more = " ".join((self.extra.get("request") or "").split())[:200]
        return f"{self.summary} ({more})" if more and more not in self.summary else self.summary

    def fingerprint(self, tool_name: str, args: dict) -> str:
        """The exact call: Allow Once covers this call and no other. A message read from a file counts by its
        contents, so the file cannot change between the card and the send."""
        try:
            blob = json.dumps({"tool": tool_name, "args": args, "file": self.extra.get("content_hash", "")},
                              sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            blob = f"{tool_name}:{args}"
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


def floor(kind: str, site_class: str = "ordinary", context: str = "chat", mode: str | None = None,
          text: str = "", saved: bool = False) -> str:
    """The tier from hard rules alone, as measured in permissions-eval/analyze.py (95.3% of 127 cases). `saved` is
    whether the Zip has a saved login for the site: since Oct 9 a teammate's question or scheduled work may sign in
    with it to an ordinary site (gate._unattended), and Colin sees it in the activity. The eval's cases predate
    that and carry no saved login, so they score as before."""
    if kind in NEVER_KINDS:
        return "never"
    if kind == "account" and SECURITY.search(text or ""):
        return "never"
    if kind == "signin" and context in ("team", "scheduled") and site_class != "sensitive" and mode != "ask" \
            and (saved or mode == "anytime"):
        return "act" if mode == "anytime" else "tell"
    if kind == "signin" and context == "team" and site_class != "sensitive" and mode != "ask":
        return "never"   # no saved login: answered without the site
    if kind in ASK_KINDS:
        return "ask"
    if kind == "signin" and (site_class == "sensitive" or mode == "ask" or
                             (context in ("scheduled", "team") and mode != "anytime")):
        return "ask"
    if kind == "write":
        return "tell"
    return "act"


# ── Commands (terminal, execute_code, mac_run) ─────────────────────────────────────────────────────────────

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PROD_BRANCH = re.compile(r"^(?:\+)?(?:HEAD:)?(?:refs/heads/)?(main|master|prod|production|release|live|gh-pages|trunk)$", re.I)
# Where a Zip may delete without asking: its own work folder and temporary folders. "~" stands for the home
# folder of whichever computer runs the command (alien, or Colin's Mac for mac_run). The work folder is the home's
# paths.work (_work_root); "~/taffy/work/" is its default.
_SAFE_ROOTS = ("/tmp/", "/var/tmp/", "/private/tmp/", "/private/var/folders/", "~/taffy/work/")
# The machine value for a command that runs on the person's Mac (mac_run). "Colin's Mac" is the older value, still
# accepted from any caller that passes it.
MAC = "mac"


def _on_mac(where: str) -> bool:
    return where in (MAC, "Colin's Mac")


def _work_root() -> str:
    """The home's work folder in "~/" form, as _safe compares it ("~/taffy/work/" unless the settings move it)."""
    try:
        from . import home
        work = str(home.get("paths.work") or "~/taffy/work").rstrip("/")
    except Exception:
        work = "~/taffy/work"
    if not work.startswith("~/"):
        work = re.sub(r"^/(?:home|Users)/[^/]+/", "~/", work)
    return work + "/"
# A containerized Zip's home and Hermes's workspace exist only inside its container, so deleting in them touches nothing
# on alien. The gate adds these for those Zips' calls only (Zip sandbox plan, stage 3); on alien itself they are not safe.
CONTAINER_ROOTS = ("/root/", "/workspace/")
EXTRA_SAFE_ROOTS: contextvars.ContextVar[tuple] = contextvars.ContextVar("taffy_extra_safe_roots", default=())
# Build output any project makes again, so removing it by a relative name is fine wherever the project is.
_REGENERABLE = {"node_modules", "dist", "build", ".next", "out", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "coverage", ".coverage", "htmlcov", ".cache", ".parcel-cache", ".turbo", "target",
                ".venv", "venv", ".svelte-kit", ".nuxt", ".expo"}
_HOME = os.path.expanduser("~")

# A binary may be named by its full path (/usr/sbin/sendmail), so "/" counts as the start of a command name.
_CMD_START = r"(^|[\s;&|(`$/])"
_SEND_CMDS = [
    (re.compile(r"\bhimalaya\b[^|;&]*\b(message|template)\s+(send|reply|forward|write)\b"), "email"),
    (re.compile(_CMD_START + r"(sendmail|msmtp|mutt|neomutt|mailx|ssmtp|swaks)\b"), "email"),
    (re.compile(_CMD_START + r"mail\s+(-[a-zA-Z]+\s+)*-s\b"), "email"),
    (re.compile(r"\bsmtplib\b|\bSMTP(_SSL)?\s*\("), "email"),
    (re.compile(r"api\.(sendgrid\.com|mailgun\.net|resend\.com|postmarkapp\.com|brevo\.com|sparkpost\.com)"), "email"),
    (re.compile(r"gmail\.googleapis\.com/[^\s'\"]*/(send|drafts/send)"), "email"),
    (re.compile(r"api\.twilio\.com|messages\.create\("), "text"),
    (re.compile(r"api\.telegram\.org/bot[^\s'\"]*/send|hooks\.slack\.com/|discord(app)?\.com/api/webhooks/|slack\.com/api/chat\.postMessage"),
     "chat"),
    (re.compile(r"\bosascript\b.*\b(send|Messages|Mail)\b", re.S), "mac"),
    (re.compile(r"\bgh\s+(issue|pr)\s+comment\b|\bgh\s+pr\s+review\b"), "github"),
]
# npx, pnpm dlx, bunx and yarn dlx run the same CLIs under another name (netlify-cli, @railway/cli).
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
    (re.compile(r"\b(firebase|fly|flyctl)\s+deploy\b|\bheroku\s+(releases|container:release)\b"), "Deploy an app"),
    (re.compile(r"\b(npm|pnpm|yarn)\s+(npm\s+)?publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgem\s+push\b"), "Publish a package"),
    (re.compile(r"\bdocker\s+push\b"), "Push a container image"),
    (re.compile(r"\b(eas\s+submit|fastlane\s+(deliver|pilot|release|upload_to_app_store)|xcrun\s+altool\s+--upload)\b"), "Upload an app for release"),
    # Oct 8 QA round 3, M5: workflows that deploy, public posts, and local services put on the internet.
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
    # Oct 8 QA round 4, L1: build hooks and API calls that deploy without the CLI's --prod.
    (re.compile(r"api\.netlify\.com/build_hooks/"), "Start a production build on Netlify"),
    (re.compile(r"\b(?:netlify|ntl)(?:-cli)?\s+api\s+(?:createSiteDeploy|restoreSiteDeploy|rollbackSiteDeploy|"
                r"createSiteBuild|updateSiteDeploy|unlockDeploy)\b"), "Publish the site on Netlify"),
    (re.compile(r"api\.vercel\.com/v\d+/integrations/deploy/"), "Start a deploy on Vercel"),
    (re.compile(r"api\.cloudflare\.com/client/v4/[^\s'\"]*/pages/webhooks/deploy_hooks/"), "Start a deploy on Cloudflare"),
    (re.compile(r"api\.render\.com/deploy/"), "Start a deploy on Render"),
]
# Netlify's deploy API by curl or another HTTP client: creating or restoring a deploy publishes the site. Listing
# deploys (a GET) is reading.
_NETLIFY_API = re.compile(r"api\.netlify\.com/api/v1/(?:sites/[^\s'\"/]+/(?:deploys|builds)|deploys/[^\s'\"/]+/restore)\b")
_HTTP_WRITE = re.compile(r"(?:-X|--request)[\s=]*['\"]?(?:POST|PUT|PATCH)\b|\s(?:-d|-F|-T)\b|--data\b|--data-\w+|--form\b|"
                         r"--upload-file\b|\bhttp(?:ie)?\s+(?:POST|PUT|PATCH)\b|\brequests\.(?:post|put|patch)\b|"
                         r"method\s*[:=]\s*['\"](?:POST|PUT|PATCH)", re.I)
# A deploy's card names the site or project it publishes (Oct 8 QA round 3, M3), and Allow Once on a scheduled
# job's card covers that one target only (H1).
_NAMED_PUBLISH = {"Publish the site to production on Netlify": "Publish {name} to production on Netlify",
                  "Publish the site to production on Vercel": "Publish {name} to production on Vercel",
                  "Deploy with Cloudflare": "Deploy {name} with Cloudflare",
                  "Deploy with Railway": "Deploy {name} with Railway",
                  "Deploy an app": "Deploy {name}"}
_TARGET_FLAG = re.compile(r"(?:^|\s)(--site|--project-name|--project|--app|--service|--cwd|--name|-a|-s)(?:=|\s+)"
                          r"(['\"]?)([^\s'\";&|<>]+)\2")


def _deploy_target(cmd: str, cwd: str | None, pat: re.Pattern) -> tuple[str, str]:
    """(target, name). The target is what a deploy publishes: the site, app or project it names (--site, --app,
    --project-name, --cwd), or else the folder it runs in, so Allow Once covers that deploy only. The name is what
    its card calls it: the named site, or the folder only when the command moved into it or it is a project in a
    Zip's work folder. The terminal starts in ~/taffy, which is not a site: "Publish taffy to production on Netlify"
    (Oct 8 QA round 4, L4)."""
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
            moved = _norm(here, None) != start   # both in the same form: _norm writes the home folder as ~
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
    (re.compile(r"\bhimalaya\b[^|;&]*\bmessage\s+delete\b"), "Delete email"),
    (re.compile(r"(^|[\s;&|(])(trash|trash-put|gio\s+trash)\s"), "Move files to the trash"),
    (re.compile(r"(^|[\s;&|(])crontab\s+(-\w*\s+)*-\w*r\b"), "Remove every job this computer runs on a schedule"),
    (re.compile(r"\bgh\s+release\s+delete\b"), "Delete a GitHub release"),
    (re.compile(r"(^|[\s;&|(/])dropdb\b"), "Delete a database"),
    # Every client a Zip might type SQL into, not only psql, mysql and sqlite3 (Oct 8 QA round 6, L2: Django's dbshell,
    # pgcli, litecli and sqlite-utils deleted data with no card).
    (re.compile(_SQL_CLIENT + r"[^|;&]*\b(?i:drop\s+(table|database|schema)|delete\s+from|truncate)\b"),
     "Delete data from a database"),
    (re.compile(r"\bredis-cli\b[^|;&]*\b(?i:flushall|flushdb|del|unlink)\b"), "Delete data from a database"),
    # MongoDB's shell and drivers delete with methods, not SQL words (Oct 8 QA round 7: `mongosh app --eval
    # 'db.users.drop()'` ran with no card).
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
    """A path made absolute and tidy ("~" kept for the home folder), or None when it depends on a working folder
    or a variable that code cannot see."""
    p = (path or "").strip()
    for var, to in (("${HOME}", "~"), ("$HOME", "~"), ("${TMPDIR}", "/tmp"), ("$TMPDIR", "/tmp")):
        if p == var or p.startswith(var + "/"):
            p = to + p[len(var):]
    if p == _HOME or p.startswith(_HOME + "/"):
        p = "~" + p[len(_HOME):]
    if p.startswith("$") or p.startswith("~") and not (p == "~" or p.startswith("~/")):
        return None   # another variable, or another user's home
    if not p.startswith(("/", "~")):
        if cwd is None:
            return None
        p = cwd.rstrip("/") + "/" + p
        if p == _HOME or p.startswith(_HOME + "/"):
            p = "~" + p[len(_HOME):]   # a folder given in full: "Delete ~/Documents/old", not the full path
    if p.startswith("~"):
        return "~" + posixpath.normpath("/" + p[1:].lstrip("/")).rstrip("/") + ("/" if p.endswith("/") else "")
    return posixpath.normpath(p)


def _safe(norm: str | None) -> bool:
    """Strictly inside the work folder or a temporary folder (deleting /tmp or the work folder itself is not)."""
    if norm is None:
        return False
    n = norm.rstrip("/") + "/"
    n = re.sub(r"^/(?:home|Users)/[^/]+/taffy/work/", "~/taffy/work/", n)   # alien's home, seen from elsewhere
    work = _work_root()
    if work != "~/taffy/work/":
        n = re.sub(r"^/(?:home|Users)/[^/]+/" + re.escape(work[2:]), work, n)
    roots = tuple(r for r in _SAFE_ROOTS if r != "~/taffy/work/") + (work,)
    return any(n.startswith(r) and n != r for r in roots + EXTRA_SAFE_ROOTS.get())


def _risky(path: str, cwd: str | None) -> bool:
    """A path a delete needs Colin's yes for. A relative path in an unknown folder is risky: a Zip's terminal
    starts in ~/taffy, not in its work folder (Oct 8 QA round 2, H2: `cd ~ && rm -rf Projects/old-site`)."""
    if not path or path.startswith("-"):
        return False
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if not path.startswith(("/", "~", "$")) and ".." not in path.split("/") and name in _REGENERABLE:
        return False
    return not _safe(_norm(path, cwd))


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
# `bash -c '...'`, `sh -lc "..."` and `eval '...'`: the quoted script is spliced in as commands of its own.
_SHELL_C = re.compile(r"""(?<![\w/-])(?:\S*/)?(?:ba|z|da|k|fi)?sh\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*c[a-zA-Z]*\s+"""
                      r"""(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|(\S+))""", re.S)
_EVAL = re.compile(r"""(?<![\w-])eval\s+(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""", re.S)
# Words that run the command after them: `nohup rm -rf x`, `env FOO=1 claude -p`, `then rm ...` inside an if, `do
# rm ...` inside a loop (Oct 8 QA round 3, M5). The values are the options that take an argument, and how many
# plain arguments come before the command (a timeout's duration, ssh's host).
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
    """The command with shell -c and eval scripts spliced in, so `bash -c 'rm -rf ~/x'` reads as `rm -rf ~/x`. A
    double-quoted script loses its escapes as the shell would take them off (`\\"` is a quote inside it)."""
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
    """(parts, substitutions): the command cut at ;, &&, ||, & and newlines (and | when `pipes`) where the shell
    cuts it, outside quotes, $(...) and backticks, and the text of each $(...) and backtick. None when a quote or
    bracket does not close. A quoted script keeps its ";" (Oct 8 QA round 5, M1: `python3 -c "import json; ..."` was
    cut in two, and Hermes's detector calls the first half a malformed payload)."""
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
    """The parts of a command (see _scan), or a plain cut at every separator when its quotes do not close."""
    scanned = _scan(cmd, pipes)
    if scanned is not None:
        return scanned[0]
    sep = r"&&|\|\||[;\n|]|(?<![<>&])&(?![>&])" if pipes else r"&&|\|\||[;\n]|(?<![<>&|])&(?![>&|])"
    return [p for p in re.split(sep, cmd) if p.strip()]


def _strip_wrappers(words: list[str], names: list | None = None) -> list[str]:
    """The words of the command a wrapper runs: `sudo -u x env A=1 nohup rm -rf y` -> `rm -rf y`. The wrappers' own
    names go into `names` when it is given."""
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_]\w*=", w):
            i += 1
            continue
        name = w.rsplit("/", 1)[-1]
        if name not in _WRAPPERS:
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
    """Each simple command with the working folder it runs in, following cd and pushd. Here-document bodies
    (an email's text, a file being written) are dropped first, so words in them are not read as commands. Shell
    -c scripts are spliced in and wrappers (sudo, env, nohup, then, do) are taken off the front."""
    out = []
    for seg in _split(_unwrap(_HEREDOC.sub("", cmd))):
        s = seg.strip().lstrip("( ")
        while s.endswith(")") and s.count(")") > s.count("("):   # a subshell's end, not $(...)'s
            s = s[:-1].rstrip()
        words = _words(s)
        inner = _strip_wrappers(words)
        if inner is not words and 0 < len(inner) < len(words):
            # git's variables (GIT_DIR=..., GIT_WORK_TREE=...) stay with it: they work like its own options.
            kept = [w for w in words[:len(words) - len(inner)] if _GIT_ENV.match(w)] \
                if inner[0].rsplit("/", 1)[-1] == "git" else []
            s = shlex.join(kept + inner)
        m = re.match(r"^(?:cd|pushd)(?:\s+(.+))?$", s)
        if m:
            target = (_words(m.group(1) or "~") or ["~"])[0]
            cwd = None if target == "-" else _norm(target, cwd)
            continue
        if s:
            out.append((s, cwd))
    return out


def _shell_delete(cmd: str, cwd: str | None) -> Action | None:
    """rm, find -delete, git clean and rsync --delete outside the work folder and /tmp need a yes; tidying the
    Zip's own work, temporary files and build output is fine. A loop's variable stands for the loop's list. Every
    part of the command is judged, and the card names each risky one."""
    found = []
    loops = {m.group(1): _words(m.group(2)) for m in re.finditer(r"\bfor\s+(\w+)\s+in\s+([^;\n]+?)\s*(?:;|\n)\s*do\b",
                                                                   _unwrap(cmd))}
    for seg, here in _segments(cmd, cwd):
        m = re.match(r"^(?:sudo\s+(?:-\S+\s+)*)?(?:command\s+)?(?:\S*/)?(rm|unlink|shred)\s+(.*)$", seg, re.S)
        if m:
            # A path another command works out as it runs (`rm -rf $(cat list)`) is one unknown path.
            # A redirection and its file (2>/dev/null, > rm.log) are not paths.
            words, paths, ended = _plain_words(_words(re.sub(r"\$\([^()]*\)|`[^`]*`", "$(...)", m.group(2)))), [], False
            for w in words:
                if (w.count("'") + w.count('"')) % 2:
                    w = re.sub(r"""['")\]]+$""", "", w)   # the end of the quoted script around it
                if w == "--" and not ended:
                    ended = True
                elif ended or not w.startswith("-"):
                    paths += loops.get(re.sub(r"^\$\{?(\w+)\}?$", r"\1", w), [w]) if w.startswith("$") else [w]
            risky = [("a path another command works out" if "$(...)" in p else _norm(p, here) or p)
                     for p in paths if _risky(p, here)]
            if risky:
                found.append(Action("delete", f"Delete {', '.join(risky)[:120]}", target=", ".join(risky)[:200],
                                    inputs=cmd))
            continue
        m = re.match(r"^(?:sudo\s+)?find\s+(.*)$", seg, re.S)
        if m and re.search(r"\s-delete\b|-exec(?:dir)?\s+(?:\S*/)?(rm|shred|unlink)\b", seg):
            roots = []
            for w in _plain_words(_words(m.group(1))):
                if w.startswith(("-", "(", "!", ")")):
                    break
                roots.append(w)
            risky = [r for r in roots or ["."] if not _safe(_norm(r, here))]
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
            if not all(_safe(f) for f in git.folders):
                found.append(Action("delete", "Delete untracked files with git clean",
                                    target=git.tree or "the current folder", inputs=cmd))
            continue
        if re.match(r"^rsync\b", seg) and re.search(r"\s--delete", seg):
            dest = ([w for w in _plain_words(_words(seg))[1:] if not w.startswith("-")] or [""])[-1]
            remote = re.match(r"^[\w.@-]+:", dest) is not None
            if remote or not _safe(_norm(dest, here)):
                found.append(Action("delete", f"Delete files in {dest[:120]} that are not in the copy",
                                    target=dest[:200], inputs=cmd))
    if re.search(r"\bxargs\s+(?:-\S+\s+)*(?:\S*/)?rm\b", cmd):
        roots = [r for s, h in _segments(cmd, cwd) for r in (_find_roots(s, h) or [])]
        if not roots or not all(_safe(r) for r in roots):
            found.append(Action("delete", "Delete a list of files", target="files listed by another command",
                                inputs=cmd))
    return _combine(found, cmd)


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
    """Deleting from code (shutil.rmtree, os.remove, Path.unlink): a literal path is judged like rm. A path held
    in a variable is risky when the code names the home folder or a path outside the safe folders."""
    paths, unknown = [], False
    for m in _PY_DELETE.finditer(code):
        arg = next((g for g in m.groups() if g is not None), "").strip()
        lit = _PY_LITERAL.match(arg)
        if lit:
            paths.append(lit.group(2).split("{")[0])
        else:
            unknown = True
    risky = [p for p in paths if _risky(p, cwd)]
    unknown = unknown or any(unsure and re.match(r"^(?:sudo\s+)?(?:\S*/)?(?:rm|unlink|shred)\b", text)
                             for text, unsure in _code_commands(code))
    if unknown and not risky:
        literals = [m.group(2) for m in re.finditer(r"""(['"])((?:/|~/)[^'"\n]*)\1""", code)]
        if _PY_HOME.search(code) or any(not _safe(_norm(x, cwd)) for x in literals):
            risky = ["files named in the code"]
    if risky:
        return Action("delete", f"Delete {', '.join(risky)[:120]} from code", target=", ".join(risky)[:200], inputs=code)
    return None


def _gh_apis(cmd: str) -> list[Action]:
    """Every gh api call in a command that writes."""
    return [a for m in re.finditer(r"\bgh\s+api\b([^|;&\n]*)", cmd) for a in [_gh_api(m.group(1), cmd)] if a]


def _gh_api(rest: str, cmd: str) -> Action | None:
    """gh api writes (a method other than GET, or fields, which make it a POST) post, change or delete on GitHub."""
    meth = re.search(r"(?:-X|--method)[\s=]*['\"]?(\w+)", rest)
    method = meth.group(1).upper() if meth else ("POST" if re.search(r"\s(-f|-F|--field|--raw-field|--input)\b", rest)
                                                 else "GET")
    if method == "GET":
        return None
    if method == "DELETE":
        return Action("delete", "Delete something on GitHub", inputs=cmd)
    return Action("publish", "Post or change something on GitHub", inputs=cmd)


# ── git ──────────────────────────────────────────────────────────────────────────────────────────────────────

# git's own options, which come before its subcommand (git.c, handle_options). Every git rule reads the command as if
# they were not there, so `git -c user.name=Builder --no-pager push origin main` is the push it looks like (Oct 8 QA
# round 8, M1: the rules matched only `git push` and `git -C <folder> push`, and so does Hermes's own check).
# Options that take a value, as the next word or after "=" (-c and -C only as the next word):
_GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix",
                   "--attr-source", "--list-cmds"}
# One shell word in a command's text, quotes and all (`http.extraHeader="Authorization: Bearer x"`).
_SH_WORD = r"""(?:[^\s'"|;&<>()`]|"[^"]*"|'[^']*')+"""
_GIT_OPTS = (r"(?:\s+(?:-[cC]\s+" + _SH_WORD + r"|--(?:git-dir|work-tree|namespace|config-env|super-prefix|attr-source|"
             r"list-cmds)(?:=|\s+)" + _SH_WORD + r"|--exec-path(?:=" + _SH_WORD + r")?|--[a-z][\w-]*|-[pPhv]))*")
# A push's arguments run to the end of its command. `2>&1` and `&>log` are redirections, not the end of the command,
# so they stay in (and _plain_words drops them).
_GIT_PUSH = re.compile(r"\bgit" + _GIT_OPTS + r"\s+push\b((?:[<>]&|&>|[^|;&\n])*)")
# A redirection: `2>&1`, `2>/dev/null`, `> out.log`, `>>log`, `&>log`, `<in`, `1>&2`, `>|file`, `<<<text`. Alone (`>`,
# `2>`) it takes the next word as its file.
_REDIRECT = re.compile(r"^(?:\d+|&)?(?:>>|>\||>&|>|<<<|<&|<>|<)(.*)$", re.S)
# git push options that take a value as the next word.
_PUSH_VALUE_FLAGS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
# Variables that act like git's options: the repository, its work tree, and settings the gate cannot read.
_GIT_ENV = re.compile(r"^GIT_(?:DIR|WORK_TREE|NAMESPACE|CONFIG\w*|EXEC_PATH)=")
# Subcommands git never lets an alias replace, among them every one a rule reads.
_GIT_BUILTINS = {"push", "reset", "clean", "branch", "tag", "remote", "commit", "checkout", "switch", "restore", "fetch",
                 "pull", "merge", "rebase", "status", "log", "diff", "add", "rm", "mv", "stash", "show", "config", "init",
                 "clone", "worktree", "submodule", "gc", "prune", "reflog", "update-ref", "filter-branch", "cherry-pick",
                 "revert", "am", "apply", "bisect", "grep", "describe", "rev-parse", "ls-files", "ls-remote", "help"}
# The families of Hermes's findings that git's own rules decide (_GATE_FAMILIES).
_GIT_FAMILIES = ("delete", "push", "workspace")
# Settings whose value is a command git runs (`git -c core.sshCommand='...' push`): judged as that command.
_GIT_RUNS = re.compile(r"^(?:core\.(?:sshcommand|pager|editor|askpass|fsmonitor)|sequence\.editor|diff\.external|"
                       r"pager\.[\w-]+|gpg\.(?:[\w-]+\.)?program)$")


def _git_files() -> list[str]:
    """The settings files git reads before the repository's own, in its order: the system file, then the user's."""
    return ["/etc/gitconfig", os.path.join(_HOME, ".config", "git", "config"), os.path.join(_HOME, ".gitconfig")]


@dataclass
class _Git:
    """One git command read past its own options: `git -C site -c a=b push origin main` is `push origin main` run in
    site with a=b set."""
    sub: str
    args: list
    where: str | None              # the folder git runs in, after -C
    repo: str | None               # where its repository is: --git-dir (or GIT_DIR), else `where`
    folders: list                  # every folder it can change: its repository and its work tree
    config: list                   # (key, value) set by -c and --config-env, in order; None for a value not shown
    unread: bool = False           # settings given in a way the gate cannot read (GIT_CONFIG_* variables)
    options: bool = False          # anything came before the subcommand
    alias: bool = False            # the subcommand came from an alias
    shell: str = ""                # what a "!" alias runs, as a shell command
    tree: str | None = None        # its work tree: --work-tree (or GIT_WORK_TREE), else `where`

    @property
    def plain(self) -> str:
        """The command as it would be written with no options: what Hermes's own check can read."""
        return shlex.join(["git", self.sub, *self.args])


def _git_key(key: str) -> str:
    """A setting's name as git compares it: the section and the name in lower case, the part between as written."""
    first, _, rest = key.partition(".")
    middle, _, last = rest.rpartition(".")
    return ".".join(p for p in (first.lower(), middle, last.lower()) if p) if rest else first.lower()


def _git_call(words: list[str], here: str | None) -> _Git | None:
    """A git command in a simple command's words (variables like GIT_DIR=... may come first), or None."""
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
            i += 1   # a flag (--no-pager, -P, --bare, --literal-pathspecs ...) or --exec-path=<folder>
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
    # The repository is --git-dir's, else the one git finds from where it runs; the work tree is --work-tree's, else
    # the folder it runs in.
    folders = list(dict.fromkeys([git_dir or where, work_tree or where]))
    g = _Git(words[i], list(words[i + 1:]), where, git_dir or where, folders, config, unread, options,
             tree=work_tree or where)
    if given_dir and git_dir is None or given_tree and work_tree is None:
        g.folders.append(None)   # a folder the gate cannot work out ($VAR, or relative to an unknown folder)
    # An alias (`git -c alias.ship=push ship origin main`, or [alias] in a settings file) runs what it names. git
    # never lets an alias replace one of its own commands. One that starts with "!" is a shell command, run with the
    # words after it (_git_option_forms judges it).
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
    """A git command that works only inside the work folder or /tmp: the folder it runs in, its repository and its
    work tree (`git --work-tree=~/Documents reset --hard` run from the work folder changes ~/Documents)."""
    g = _git_call(words, here)
    return all(_safe(f) for f in g.folders) if g is not None else _safe(here)


def _git_option_forms(cmd: str, cwd: str | None, where: str, detect=None, depth: int = 0) -> list[Action]:
    """git with its own options before the subcommand. Hermes's check reads only plainly written forms (`git reset
    --hard`, `git push --force`), so it never asks about `git -c a=b reset --hard`; the gate asks what Hermes would
    have asked about the plain form, with the same card title (Oct 8 QA round 8, M1). A setting whose value is a
    command (`-c core.sshCommand=...`, a "!" alias) is judged as that command."""
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
            continue   # nothing to ask, or Hermes reads it as written
        family = _family(found)
        if family in _GIT_FAMILIES and not _judged(seg, here, family, cmd):
            out.append(Action("system", _consent().plain_command(found, g.plain), inputs=cmd))
    return out


def _git_layers(repo: str | None, git: _Git | None = None) -> list[tuple[str, str | None]] | None:
    """Every git setting for a command in `repo`, in the order git reads them (the system file, the user's two files,
    the repository's own, then -c on the command line), so the last one of a name wins. None when one of them
    cannot be read or depends on something the gate cannot see (GIT_CONFIG_* variables, [includeIf])."""
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
    """The value git uses for a setting (the last one set), or None when it is not set or cannot be read."""
    entries = _git_layers(git.repo if git is not None else None, git)
    values = [v for k, v in entries or [] if k == key]
    return values[-1] if values else None


def _repo_config_path(repo: str | None) -> str | None:
    """The settings file of the repository `repo` is in, found the way git finds it (the folder, then each one above
    it; a bare repository or a .git folder named directly counts); "" when there is none, and None when it cannot be
    read (a .git file pointing elsewhere: a linked work tree or a submodule)."""
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
    """The settings in a git settings file, with its [include] files read in place: [] when the file does not exist
    (git skips it too), None when it or an include cannot be read, or it holds an [includeIf], which depends on
    things the gate does not check."""
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
            # git skips an include that is not there, but one could be put there before the push runs.
            got = _read_git_config(inc, depth + 1) if os.path.isfile(inc) else None
            if got is None:
                return None
            out += got
            continue
        out.append((key, value))
    return out


def _parse_git_config(text: str) -> list[tuple[str, str]] | None:
    """(name, value) for each setting in a git settings file's text, in order, or None when the text is not one git
    would read the same way."""
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
            raw = raw[:-1] + lines[i]   # a line ending in "\" goes on to the next
            i += 1
        value = _git_value(raw)
        if value is None:
            return None
        out.append((section + "." + m.group(1).lower(), value))
    return out


def _git_value(raw: str) -> str | None:
    """A setting's value as git reads it: quotes removed, escapes read, a comment cut off, outer spaces dropped."""
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
            pending += ch   # kept only if more of the value follows
        else:
            out.append(pending + ch)
            pending = ""
    return None if quoted else "".join(out)


def _push_urls(remote: str, repo: str | None, git: _Git | None = None) -> tuple[list[str], list[str]] | None:
    """Where git sends a push to `remote` (a name from the settings or a path or URL typed in the command), and the
    remote's own push mappings: (URLs, `push =` lines). The URLs are the remote's pushurl lines, else its url lines,
    rewritten by url.<base>.pushInsteadOf and insteadOf from every settings file and -c, as git does (Oct 8 QA round
    8, L2: a second url, a pushurl or an insteadOf in the repository's own settings sent a push to GitHub while the
    gate saw a scratch folder). None when the gate cannot tell: a file it cannot read, a remote name it cannot look
    up, an emptied list, or two rewrites that fit equally well."""
    entries = _git_layers(repo, git)
    if entries is None:
        return None
    if any(v is None for k, v in entries if k.startswith(("remote.", "url.", "alias."))):
        return None   # a value from --config-env, which the gate cannot see
    named = bool(remote) and re.fullmatch(r"[\w.-]+", remote) is not None
    if named and any(k.startswith(f"remote.{remote}.") for k, _ in entries):
        pushurls = [v for k, v in entries if k == f"remote.{remote}.pushurl"]
        urls = [v for k, v in entries if k == f"remote.{remote}.url"]
        specs = [v for k, v in entries if k == f"remote.{remote}.push"]
        if "" in pushurls + urls or not (pushurls or urls):
            return None
    elif named and not remote.endswith(".git"):
        return None   # a remote name the gate cannot look up
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
            # git pushes only to the rewritten ones then; whether insteadOf applies to them too has changed between
            # versions, so both count.
            moved = [p for p, u in zip(pushed, urls) if p != u]
            dests = moved + [rewrite(p, rewrites["insteadof"]) if p else None for p in moved]
        else:
            dests = [rewrite(u, rewrites["insteadof"]) for u in urls]
    if not dests or any(d is None for d in dests):
        return None
    return list(dict.fromkeys(dests)), specs


def _url_folder(url: str, repo: str | None) -> str | None:
    """The folder on this computer a push URL names, or None for another computer (https://, ssh://, host:path)."""
    if url.startswith("file://"):
        path = url[len("file://"):]
    elif "://" in url or re.match(r"^[^/]*:", url):
        return None
    else:
        path = url
    return _norm(path, repo) or path


def _local_remote(remote: str, repo: str | None, git: _Git | None = None) -> str | None:
    """The folder on this computer a push goes to, or None when it goes to another computer or is not known. When git
    pushes to several places, all of them must be on this computer; the one outside the scratch folders is named."""
    return _folder_of(_push_urls(remote, repo, git), repo)


def _folder_of(got: tuple | None, repo: str | None) -> str | None:
    """_local_remote for what _push_urls found."""
    if got is None:
        return None
    folders = [_url_folder(u, repo) for u in got[0]]
    if any(f is None for f in folders):
        return None
    return next((f for f in folders if not _safe(f)), folders[0])


def _mapped(ref: str, specs: list[str]) -> list[str] | None:
    """The branches a push of `ref` (no ":" in it) updates under the remote's `push =` lines (`push =
    refs/heads/feat:refs/heads/main` makes `git push origin feat` update main), or None when no line covers it."""
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
    """The words of a command without its redirections and their files: a redirection is never a branch, a remote,
    a path to delete or a package (Oct 8, Colin's live test: `git push --force origin main 2>&1` was carded as "Push
    to main, 2>")."""
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
    """The first git push in a command (see _push), including one to a scratch folder."""
    found = _git_pushes(cmd, cwd, keep_local=True)
    return found[0] if found else None


def _git_pushes(cmd: str, cwd: str | None = None, keep_local: bool = False) -> list[Action]:
    """Every git push in a command, not only the first (Oct 8 QA round 4, M2). `cwd` is where the command starts,
    so a push's remote can be looked up in its repository. A push to a folder inside the work folder or /tmp is the
    Zip's own scratch work and is left out, unless `keep_local` (Hermes's own check asks about a force push, and the
    gate answers it). git's own options before `push` are read past (round 8, M1), and so is an alias for push."""
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
        a.extra["push"] = True   # what an Ask First rule about pushing code matches (consent.ask_rule_for)
    return [a for a in found if keep_local or not a.extra.get("scratch")]


def _push(args: str, cmd: str, repo: str | None = None, git: _Git | None = None) -> Action:
    """git push to a production branch publishes; to another branch it is a reversible write. A push that does
    not name its branch is treated as publishing, with a hint to name it. A push to a folder on this computer says
    so. `repo` is the repository the push runs in, when known, and `git` the command's own options."""
    # A push inside code or a quoted script can end in the quote or bracket around it: `os.system('git push --force
    # origin main')` (Oct 8 QA round 6, L1: the branch was read as `main')"`).
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
        # A bare repository or another folder on this computer publishes nothing (Oct 8, Colin's live test). One in
        # the Zip's work folder or /tmp is its own scratch copy, like the files there: no card, no line.
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
    """The person's own email addresses (their mailboxes and calendars), in lower case."""
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
    """(text, sha256) of the file a send reads its message from (`< reply.eml`, `cat reply.eml |`), so the card
    shows what goes out and Allow Once covers that exact text (Oct 8 QA round 2, M2)."""
    m = re.search(r"(?<!<)<\s*(['\"]?)([^\s;&|'\"<>]+)\1", cmd) or re.search(r"\bcat\s+(['\"]?)([^\s;&|'\"]+)\1\s*\|", cmd)
    if not m:
        return None
    n = _norm(m.group(2), cwd)
    if n is None:
        return None
    path = _HOME + n[1:] if n.startswith("~") else n
    # Never a protected file, even through a link or a hard link: the card would show it (safe_files.py).
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


# ── Other assistants and the Zips' own settings ───────────────────────────────────────────────────────────

# Another assistant started from a Zip's terminal acts outside the gate: `claude -p` reaches Colin's Google and
# Trello through the same connectors with none of their limits, and `codex exec` runs a shell the gate never sees.
# Hermes's own command line changes the Zips' plugins, settings and sign-ins: `hermes plugins disable taffy-tools`
# switches the gate off at the next restart (Oct 8 QA round 3, H2). The connectors tool runs claude itself from the
# plugin's code, which does not pass through here.
_AGENTS = {"claude": "claude", "claude-code": "claude", "codex": "codex", "hermes": "hermes", "taffy": "hermes",
           "hermes-agent": "hermes-agent", "hermes-acp": "hermes-agent", "hermes_cli": "hermes", "run_agent": "hermes-agent"}
# What a Zip may still run: version and help, and Hermes's read-only views and its board.
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
_WHICH_SUB = re.compile(r"(?:\$\(|`)\s*(?:which|command\s+-v|type\s+-[pP]|whereis)\s+([\w./~-]+)\s*(?:\)|`)")
# `$(which claude) -p` counts only where a command starts, not as an argument (`echo $(which claude)`).
_WHICH_AT_START = re.compile(r"(?:^|[;&|\n(]|\b(?:then|do|else|exec|nohup|sudo|command|time)\s)\s*"
                             r"(?:[A-Za-z_]\w*=\S*\s+)*" + _WHICH_SUB.pattern)

AGENT_NEVER = ("Starting another assistant from your terminal (claude, codex or Hermes) is off for Zips, because it "
               "would act outside Colin's permissions. Use your own tools instead: the connectors tool for Gmail, "
               "Google Calendar, Google Drive and Trello, and your terminal, browser and files for the rest.")
SETTINGS_NEVER = ("Changing the Zips' own plugins, settings, tools, sign-ins or scheduled jobs from the terminal is off "
                  "for Zips. Colin does that himself. Use your cronjob tool for scheduled jobs, and tell Colin in one "
                  "sentence what else you would change.")


def _agent_of(word: str) -> str | None:
    """Which assistant a command word runs, by its name or path: /usr/bin/claude, ~/.local/share/claude/versions/2.1,
    @anthropic-ai/claude-code, @openai/codex, codex.js, ~/taffy/.venv/bin/hermes, python -m hermes_cli."""
    w = word.strip("'\"")
    if re.search(r"/claude/versions/[^/]+$", w):
        return "claude"
    # A package's own files run by node: .../node_modules/@anthropic-ai/claude-code/cli.js (Oct 8 QA round 5, L4).
    pkg = re.search(r"(?:^|/)(@anthropic-ai/claude-code|@openai/codex|hermes[-_]agent|hermes_cli)(?:/|$)", w)
    if pkg:
        return {"@anthropic-ai/claude-code": "claude", "@openai/codex": "codex", "hermes_cli": "hermes"}.get(
            pkg.group(1), "hermes-agent")
    name = w.rsplit("/", 1)[-1].split("@", 1)[0] if not w.startswith("@") else w.split("/", 1)[-1].split("@", 1)[0]
    if re.match(r"^[\w.-]+:[\w.-]+$", name) and "/" in w:
        name = name.split(":", 1)[0]   # a container image's tag: ghcr.io/openai/codex:latest
    name = re.sub(r"\.(js|mjs|cjs|py|sh)$", "", name.split(".", 1)[0] if name.startswith("hermes_cli.") else name)
    return _AGENTS.get(name)


def _after_options(args: list[str], takes: str = "", long_takes: tuple = ()) -> list[str]:
    """The words after a launcher's options: what it runs. `takes` are the short option letters that take a value
    (the last letter of a group like -dmS counts), `long_takes` the long options that do."""
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
    """The command lines a launcher runs, read the way the launcher reads them (Oct 8 QA round 4, M1: every quoted
    string in a command was read as one whenever a launcher word appeared anywhere, so `git commit -m "Runs at
    startup"` and `sudo grep -r "hermes" /etc` were refused). sudo, env, nohup, xargs, ssh, watch and timeout are
    taken off by _strip_wrappers; these are the rest."""
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
            # BSD script: script [-q] file command...
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
            return [_words(a) for a in _after_options(args, "tNcX")]   # the keys it types
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
        # find -exec, -execdir, -ok and -okdir run a command per file, up to ";" or "+" (Oct 8 QA round 5, L4).
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
        # The image is a program too: `docker run ghcr.io/openai/codex exec hi` would run without Colin's sign-ins.
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
    """(assistant, its arguments) when these words start one: in the command's own place, behind wrappers (env,
    sudo, nohup, xargs, ssh host), through runners (npx, node, python -m) or launchers (tmux, screen, su -c)."""
    if depth < 3 and words and words[0].rsplit("/", 1)[-1] == "env" and any(w.startswith(("-S", "--split")) for w in words):
        for inner in _launched(words):   # `env -S 'claude -p'`: the wrapper's own option holds the command
            found = _agent_words(inner, depth + 1)
            if found:
                return found
    words = _strip_wrappers(words)
    if words and re.search(r"\s", words[0].strip()):
        # A whole command line passed as one word: ssh host 'claude -p hi', watch 'codex exec hi'.
        words = _strip_wrappers(_words(words[0]) + words[1:])
    for _ in range(3):
        if not words:
            return None
        head = words[0].rsplit("/", 1)[-1]
        if head in _RUNNERS or re.match(r"^python3(\.\d+)?$", head):
            rest = words[1:]
            if head in ("pnpm", "yarn") and rest[:1] == ["dlx"] or head in ("uv", "pipx") and rest[:1] == ["run"] or \
                    head in ("npm", "pnpm", "yarn") and rest[:1] in (["exec"], ["x"]):
                rest = rest[1:]
                for i, w in enumerate(rest[:-1]):   # npm exec -c 'claude -p hi'
                    if w in ("-c", "--call"):
                        rest = _words(rest[i + 1])
                        break
            elif head in ("npm", "pnpm", "yarn"):
                break   # npm run, npm install: not a launcher
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


def _agent_action(agent: str, args: list[str], cmd: str) -> Action | None:
    """None for a version, help or read-only look; otherwise the refusal."""
    if agent == "hermes" and [a for a in args if not a.startswith("-")][:2] in (["cron", "list"], ["cron", "status"]):
        return None   # reading the scheduled jobs (Oct 8 QA round 5, nitpick AF03)
    if agent == "hermes" and any(a in _HERMES_SETTINGS for a in args):
        return Action("agent", "Change the Zips' own settings from the terminal", inputs=cmd, why_never=SETTINGS_NEVER)
    allowed = _AGENT_READ_ONLY.get(agent, set())
    sub = next((a for a in args if not a.startswith("-")), "")
    if args and (sub in allowed if agent == "hermes" and sub else all(a in allowed for a in args)):
        return None
    name = {"hermes-agent": "Hermes"}.get(agent, agent.capitalize())
    return Action("agent", f"Start {name} from the terminal", inputs=cmd, why_never=AGENT_NEVER)


# Code that starts a program: Python's subprocess, os and pty, Node's child_process, Bun and pexpect. Its first
# argument is what runs (a command line, a program name, or a list whose first item is the program).
_SPAWN_CALL = re.compile(
    r"(?:(?<![\w.])|(?<=subprocess\.)|(?<=\bos\.)|(?<=\bpty\.)|(?<=pexpect\.)|(?<=asyncio\.))"
    r"(?:run|call|check_call|check_output|getoutput|getstatusoutput|Popen|system|popen|exec[lv]p?e?|spawn[lv]p?e?|"
    r"posix_spawnp?|spawn|create_subprocess_exec|create_subprocess_shell|execSync|execFileSync|spawnSync|execFile)"
    r"\s*\(|(?:child_process|\bcp|childProcess|\)|Bun)\.(?:exec|execSync|execFile|execFileSync|spawn|spawnSync)\s*\(")
_PY_STRING = r"""[rRbBuUfF]{0,2}(['"])((?:(?!\1)[^\\\n]|\\.)*)\1"""


def _bracketed(text: str, start: int) -> str:
    """What sits inside the bracket that opens at text[start], skipping quoted text."""
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
    """The text last assigned to a variable in this code (CLAUDE = "/usr/bin/claude", cmd = ["codex", "exec"])."""
    found = re.findall(rf"(?m)^[ \t]*(?:const\s+|let\s+|var\s+)?{re.escape(name)}\s*(?::\s*[\w\[\], ]+)?=\s*(.+)$", code)
    return found[-1].strip() if found else None


_ITEM = re.compile(r"""(?:shutil\.)?which\s*\(\s*(['"])([^'"]+)\1\s*\)|[rRbBuUfF]{0,2}(['"])((?:(?!\3)[^\\\n]|\\.)*)\3|"""
                   r"""([A-Za-z_][\w.]*)""")


def _items(text: str, code: str) -> list[str]:
    """The items of a list or tuple in code: strings as they are, variables by what was assigned to them,
    shutil.which('x') as x, and anything else as "?"."""
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
    """The words a spawn call runs, from its first argument at code[pos:]: a command line, a program name (and
    the argument list after it, as in spawn('codex', ['exec'])), a list, or a variable holding one of these."""
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
    """Code that starts claude, codex or Hermes: as the program of a subprocess or child_process call (directly,
    as a command line, or through a variable or `bash -c`), or Hermes's agent imported. A word in a string, a path
    passed as cwd= or a grep pattern in an argument list is not a program (Oct 8 QA round 4, M1)."""
    for m in _SPAWN_CALL.finditer(code):
        pos = m.end()
        if re.search(r"\bos\.spawn[lv]p?e?\s*\($", code[:pos]):
            # os.spawnlp(os.P_WAIT, 'codex', ...): the first argument is the mode (Oct 8 QA round 5, L4).
            comma = code.find(",", pos)
            pos = comma + 1 if comma >= 0 else pos
        found = _agent_words(_program_words(code, pos))
        if found:
            a = _agent_action(*found, code)
            if a is not None:
                return a
    if _HERMES_IMPORT.search(code):
        return _agent_action("hermes-agent", [], code)
    return None


# Calls that hand their argument to a shell: os.system, os.popen, subprocess's getoutput, Node's exec and execSync.
# subprocess.run("...", shell=True) is one too.
_SHELL_CALL = re.compile(r"(?:\bos\.(?:system|popen)|\bsubprocess\.(?:getoutput|getstatusoutput)|"
                         r"(?:child_process|\bcp|childProcess)\.(?:exec|execSync)|\bexecSync|create_subprocess_shell)\s*\($")
_CODE_PROGRAM = re.compile(r"^[\w.+@/~-]+$")


def _code_commands(code: str) -> list[tuple[str, bool]]:
    """(command line, has unknown parts) for each program this code starts: an argument list read as the command it
    runs (`subprocess.run(['systemctl', '--user', 'stop', 'adguard'])` is `systemctl --user stop adguard`), and a
    string handed to a shell as it is. A list item the code works out as it runs (a variable that is not a plain
    string) is left out and marked unknown (Oct 8 QA round 6, L1: commands inside Python were read only in part)."""
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


# Commands Hermes refuses outright when a Zip types them (detect_hardline_command in tools/approval_detection.py):
# wiping the computer, a system folder or the home folder, erasing a disk, freezing the computer, stopping every
# program, and restarting or shutting it down. Hermes cannot see them inside code, a `-c` script or a scheduled job's
# script, so the gate refuses them wherever it reads commands for alien (Oct 8 QA round 6, L1: `python3 -c
# "os.system('sudo reboot')"` was answered yes as a short script).
_WIPE_DIRS = {"/", "/home", "/root", "/etc", "/usr", "/var", "/bin", "/sbin", "/boot", "/lib"}
_DISK = re.compile(r"/dev/(?:sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*\b")
_FORK_BOMB = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")


def _hardline(words: list[str], seg: str) -> str | None:
    """What a simple command (wrappers already taken off) would do that Hermes never allows, or None."""
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


# ── Changing the computer itself (Oct 8 QA round 6, L4) ─────────────────────────────────────────────────────
#
# The Zips run as Colin's own user on alien, which has passwordless sudo, so `sudo apt-get remove -y nginx` runs with
# no prompt from anyone (Hermes treats plain sudo as needing a password it does not have). Any administrator command
# that changes the computer asks, with a card that says what it changes; reading as administrator stays quiet.
_ROOT_WORDS = {"sudo", "doas", "pkexec", "run0"}
# Programs that only look, whatever their arguments (the ones that can also change things are read by subcommand).
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
# Package managers for the whole computer, which change it even typed without sudo (as root, or set up to allow it).
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
    """The arguments that are not options or redirections (`< list.txt` is input, not a package)."""
    return [a for a in _plain_words(args) if not a.startswith("-")]


def _name_list(items: list[str], limit: int = 3) -> str:
    items = list(dict.fromkeys(i for i in items if i))
    shown = ", ".join(items[:limit]) + (" and more" if len(items) > limit else "")
    return shown


def _system_change(words: list[str], root: bool, place: str, seg: str, cwd: str | None) -> Action | None:
    """The change a simple command makes to the computer, or None for reading or ordinary work. `root` is whether
    it runs with administrator rights, and `place` how the card names the computer ("this computer")."""
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
            title = ""   # `sudo npm run build`: not a package change, judged below as any administrator command
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
        # What it writes, as administrator. A > or >> in front of sudo is written by the Zip's own user, not here.
        if head == "sed" and not any(a.startswith("-i") or a == "--in-place" for a in args):
            return None   # sed that prints
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
            return None   # inside the work folder or a temporary folder
        if any(t.startswith("/etc/sudoers") for t in outside):
            return Action("system", f"Change who has administrator rights on {place}", target=_name_list(outside),
                          inputs=seg)
        return Action("system", f"Change {_name_list(outside)} with administrator rights" if outside else
                      "Change files with administrator rights", target=_name_list(outside), inputs=seg)
    if head in ("rm", "unlink", "shred", "find", "crontab"):
        return None   # deletes and the crontab have checks of their own (_shell_delete, _crontab_change)
    if head in ("kill", "pkill", "killall"):
        return Action("system", f"Stop a program on {place} with administrator rights", inputs=seg)
    if head in _READ_SUBS:
        reads = _READ_SUBS[head]
        if reads is None or any(a in reads for a in args[:2]) or not args and head in ("docker", "tailscale", "ufw"):
            return None
    return Action("system", f"Run {head} with administrator rights on {place}", inputs=seg)


def _strip_root(words: list[str]) -> tuple[list[str], bool, bool]:
    """(the command after its wrappers, whether a wrapper gives it administrator rights, whether it runs on another
    computer over ssh)."""
    names: list[str] = []
    inner = _strip_wrappers(words, names)
    if inner and re.search(r"\s", inner[0].strip()):
        inner = _strip_wrappers(_words(inner[0]) + inner[1:], names)   # ssh host 'sudo apt remove x'
    return inner, any(n in _ROOT_WORDS for n in names), "ssh" in names


_C_SCRIPT = re.compile(r"""(?:^|\s)(?:-[A-Za-z]*c[A-Za-z]*|--command)(?:\s+|=)(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|(\S+))""", re.S)


def system_changes(cmd: str, cwd: str | None, where: str = "your computer", root: bool = False,
                   depth: int = 0) -> list[tuple[Action, str]]:
    """(action, the simple command as _segments writes it) for each part of a command that changes the computer:
    software, services, user accounts, permissions, and files outside the Zips' folders written with administrator
    rights. A shell started with sudo (`sudo bash -c '...'`, `sudo sh <<EOF`) runs everything in it as administrator."""
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
                # A shell that reads its commands from what is fed to it (`sudo bash <<EOF`), as administrator.
                for text in fed:
                    out += system_changes(text, cwd, where, True, depth + 1)
                if not fed:
                    out.append((Action("system", f"Open an administrator shell on {place}", inputs=s), s))
                continue
        a = _system_change(inner, rooted, "another computer" if remote else place, s, cwd)
        if a is not None:
            out.append((a, shlex.join(inner) if 0 < len(inner) < len(words) else s))
    return out


# Text a shell or `at` reads as commands: `bash <<EOF`, `at now <<< '...'`, `echo '...' | sh`. Code fed to an
# interpreter: `python3 -c '...'`, `node -e '...'`, `python3 - <<EOF`.
_SHELLS = r"(?:ba|z|da|k|fi)?sh|at|batch"
_INTERPRETERS = r"python[\d.]*|node|deno|bun|ruby|perl"
_HERESTRING = re.compile(rf"""(?<![\w/-])(?:\S*/)?({_SHELLS}|{_INTERPRETERS})\b[^\n;&|]*?<<<\s*(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|(\S+))""")
_ECHO_PIPE = re.compile(rf"""\b(?:echo|printf)\s+(?:-\w+\s+)*(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|([^|;&\n'"]+?))\s*\|\s*"""
                        rf"""(?:sudo\s+(?:-\S+\s+)*)?(?:\S*/)?({_SHELLS}|{_INTERPRETERS})\b""")
_INLINE_CODE = re.compile(rf"""(?<![\w/-])(?:\S*/)?(?:{_INTERPRETERS})\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*[ce]\s+"""
                          r"""(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""", re.S)


def _fed(cmd: str) -> tuple[list[str], list[str]]:
    """(shell text, code) that this command hands to a shell, `at` or an interpreter to run."""
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
    """Starting claude, codex or Hermes, or changing Hermes's settings, from a terminal or from code. A command
    counts only where it runs: as a plain command; behind env, exec, command, nohup, sudo, ssh, xargs or watch;
    inside `sh -c`, eval, su -c, tmux, screen or systemd-run; by full path, through npx or node, or as `$(which
    claude)`; in text fed to a shell or `at`; and as the program of a subprocess call. The same words in
    arguments, grep patterns, sed scripts, commit messages, file contents or paths are ordinary work."""
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


# Text a command writes for the computer to run later: crontab lines, a systemd unit's ExecStart, a file handed to
# `at -f` or `crontab`. A crontab line that starts Claude keeps starting it after the turn ends (Oct 8 QA round 5, L4).
_ECHO_TEXT = re.compile(r"""\b(?:echo|printf)\s+(?:-\w+\s+)*(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|([^|;&\n'"<>]+))""")
_CRON_LINE = re.compile(r"^\s*(?:@(?:reboot|yearly|annually|monthly|weekly|daily|midnight|hourly)|(?:\S+\s+){4}\S+)\s+(.+)$")
_EXEC_LINE = re.compile(r"(?m)^\s*Exec(?:Start|StartPre|StartPost|Stop|Reload)\s*=\s*[-@:+!]*(.+)$")


def _small_file(path: str, cwd: str | None) -> str:
    """The text of a small file a command names, or "" when it cannot be read."""
    n = _norm(path, cwd)
    if n is None:
        return ""
    data = safe_files.card_text(_HOME + n[1:] if n.startswith("~") else n, 64 * 1024)
    return "" if data is None else data.decode("utf-8", "replace")


def _crontab_writes(cmd: str, cwd: str | None) -> list[tuple[str, str | None]]:
    """(the file it installs or "-" for its input, folder) for each `crontab` that replaces or edits the user's
    crontab. `crontab -l` only lists it, and `crontab -r` removes it (a delete of its own)."""
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
    """The command lines this command sets up to run later: from crontab lines it installs, ExecStart lines it
    writes, and files it hands to `at -f`."""
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
    """Writing the user's crontab is an account change that asks: scheduled jobs belong in the Zips' own scheduler
    (Oct 8 QA round 5, L4)."""
    if not _crontab_writes(cmd, cwd):
        return None
    return Action("account", "Change the jobs this computer runs on a schedule", inputs=cmd,
                  extra={"hint": "Scheduled jobs belong in your cronjob tool, where Colin can see them; use it instead "
                                 "of the crontab unless he asked for the crontab itself."})


def classify_command(cmd: str, where: str = "your computer", cwd: str | None = None, code: bool = False,
                     detect=None, _depth: int = 0) -> Action | None:
    """A shell or code command, or None when it is ordinary work (act). `cwd` is the folder it runs in, when
    known; relative paths in an unknown folder count as outside the work folder. `code` is Python from
    execute_code rather than a shell command. `detect` is Hermes's own check, given when Hermes will not see the
    commands this code starts (execute_code, a scheduled job's script): what it would ask about asks here."""
    if not cmd:
        return None
    if _PIPE_TO_SHELL.search(cmd):
        return Action("secret", "Run a script downloaded from the web", inputs=cmd,
                      why_never="Running a script piped from the web is off for Zips. If Colin asked you to install "
                                "something, use the package manager (apt, pip, npm, brew), or give him the command to run.")
    for pat, what in _PAY_CMDS:
        if pat.search(cmd):
            return Action("pay", what, inputs=cmd, why_never="Zips never move money. Tell Colin what you found so he can do it.")
    agent = _agent_command(cmd, cwd, code=code)
    if agent is not None:
        return agent
    if not _on_mac(where) and not code:
        # What Hermes refuses outright, refused here too, so no card asks for something that cannot run. A
        # command run on another computer over ssh is that computer's (the system check below asks about it).
        for piece in _split(_unwrap(_HEREDOC.sub("", cmd))):
            s = piece.strip().lstrip("( ")
            while s.endswith(")") and s.count(")") > s.count("("):
                s = s[:-1].rstrip()
            inner, _root, remote = _strip_root(_words(s))
            what = None if remote else _hardline(inner, s)
            if what:
                return _hardline_action(what, cmd)
    # Every risky part is judged, not only the first one found (Oct 8 QA round 4, M2): a told push followed by a
    # delete outside the work folder, or a deploy and a send in one line, each need their yes.
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
    found += _gh_apis(cmd)
    found += [_shell_delete(cmd, cwd), _code_delete(cmd, cwd), None if code else _crontab_change(cmd, cwd)]
    if not code:
        found += [a for a, _seg in system_changes(cmd, cwd, where)]
    if _depth < 2:
        # The programs code starts, read like the same command typed in a terminal (Oct 8 QA round 6, L1): Python
        # from execute_code, and code a command hands to an interpreter (`python3 -c`, `python3 - <<EOF`).
        for text in ([cmd] if code else _fed(cmd)[1]):
            for inner, unsure in _code_commands(text):
                if unsure and re.match(r"^(?:sudo\s+)?(?:\S*/)?(?:rm|unlink|shred)\b", inner):
                    continue   # a path the code works out: _code_delete judges it like rmtree(path)
                a = classify_command(inner, where, cwd, detect=detect, _depth=_depth + 1)
                if a is not None and floor(a.kind) == "never":
                    a.inputs = cmd
                    return a
                if a is not None:
                    a.inputs, a.details = cmd, ""
                    found.append(a)
                flagged = unjudged(inner, cwd, detect) if detect is not None else None
                if flagged and flagged not in _ARTIFACTS:
                    found.append(Action("system", _consent().plain_command(flagged, inner), inputs=cmd))
    if not code and _depth < 2:
        # What runs inside $(...) and backticks, and text fed to a shell (`bash <<EOF`), are commands of their own
        # (Oct 8 QA round 5, L1). The hub counts on the gate having judged them.
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
    """One action for a command that does several things: the riskiest part sets the kind, the title names every
    part, and no standing rule covers it when more than one part needs a yes."""
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
    lower = lambda t: t[:1].lower() + t[1:] if t[1:2].islower() else t   # noqa: E731
    asks = sum(1 for a in parts if floor(a.kind) == "ask")
    return Action(first.kind, (summaries[0] + "".join(", and " + lower(t) for t in summaries[1:]))[:300],
                  target=", ".join(targets)[:300], site=first.site, inputs=first.inputs if first.details else cmd,
                  details=first.details, scope=first.scope if asks <= 1 else "", extra=extra)


# ── Hermes's own command check ─────────────────────────────────────────────────────────────────────────────

# After the gate lets a terminal command through, Hermes checks it against its own list and waits for an answer
# (tools/approval_detection.py on alien). It reports only the first thing it found, so the hub cannot tell from its
# prompt what else the command does (Oct 8 QA round 4, M2: `rm -rf build && sudo systemctl restart nginx` was
# answered yes as a delete). The gate works out, part by part, whether it already judged everything Hermes would
# ask about, and records that for the hub (consent.remember_command, hub approvals.py).
#
# The gate uses Hermes's own detector when it can import it. This copy of the patterns that matter for a Zip on
# Linux, with Hermes's descriptions, is the stand-in elsewhere (tests on a Mac).
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
# Hermes calls a script it cannot read as words (an unclosed quote) a malformed payload and asks about it. Its parser
# limit and this are about the text the gate hands its detector, so on a part of a command the gate cut out they are
# the gate's own doing; the whole command is always checked as it is (Oct 8 QA round 5, M1).
_MALFORMED = "command parser limit or malformed executable payload"
_ARTIFACTS = {_MALFORMED, "command parser limit exceeded"}
_PAYLOAD_RUNNER = re.compile(r"^(?:python[\d.]*|pypy[\d.]*|perl|ruby|node|nodejs|deno|bun|php|lua|osascript|pwsh|"
                             r"powershell|sort|rg|ag|man)$")


def hermes_like(text: str) -> str | None:
    """Hermes's description of the first thing in this text its own check would ask about, or None."""
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


# Hermes's descriptions of what the gate judges itself, by family.
_GATE_FAMILIES = [
    (re.compile(r"^(delete in root path|recursive delete|find -delete|find -exec|xargs with rm|git clean)", re.I), "delete"),
    (re.compile(r"^(script execution via|shell command via|shell execution via heredoc)", re.I), "script"),
    (re.compile(r"^SQL (DROP|DELETE|TRUNCATE)", re.I), "sql"),
    (re.compile(r"^git force push", re.I), "push"),
    (re.compile(r"^(git reset --hard|git branch force delete|package manager uninstall|docker compose)", re.I),
     "workspace"),
    (re.compile(r"^overwrite project env/config", re.I), "project file"),
    # What the gate's system check asks about when it runs as administrator (Oct 8 QA round 6, L4).
    (re.compile(r"^(stop/restart system service|overwrite system (config|file)|copy/move file into system config|"
                r"in-place edit of system config|recursive chown to root|world/other-writable)", re.I), "system"),
]
# A database client or library in the command: with one, SQL words are going to a database, not text being searched
# or written (`grep -rn "DROP TABLE" migrations/`, `git log -S "DELETE FROM"`; Oct 8 QA round 5, L2).
_DB_CLIENT = re.compile(r"(?<![\w-])(?:psql|pg_\w+|mysql\w*|mariadb\w*|sqlite3?|sqlite-utils|duckdb|redis-cli|mongo(?:sh)?|"
                        r"clickhouse(?:-client)?|cockroach|sqlcmd|bq|snowsql|supabase|turso|wrangler\s+d1|prisma|knex|sequelize|"
                        r"psycopg\d?|pymysql|asyncpg|sqlalchemy|dbmate|flyway|liquibase|usql|pgcli|mycli|litecli|dbshell|"
                        r"dbconsole|rails\s+db|heroku\s+pg|(?:fly|flyctl)\s+(?:postgres|pg|mpg))(?![\w-])|"
                        r"\.(?:execute|executemany|executescript|query|raw)\s*\(", re.I)
# SQL words are only text when every program that sees them reads or writes text: a search, a log, a file being
# written. Any other program might be a database client the gate does not know (Oct 8 QA round 6, L2: round 5 cleared
# SQL whenever no client it knew was in the command, so `manage.py dbshell`, pgcli, litecli and sqlite-utils got
# through).
_SQL_WORDS = re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b|\bDELETE\s+FROM\b|\bTRUNCATE\b", re.I)
_TEXT_READERS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "git", "sed", "awk", "gawk", "cat", "less", "more", "head",
                 "tail", "echo", "printf", "find", "wc", "sort", "uniq", "jq", "diff", "tee", "cut", "tr", "column",
                 "bat", "nl", "xxd", "file"}


def _sql_as_text(cmd: str) -> bool:
    """True when the SQL words in this command only pass through programs that search, show or write text."""
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


# Variables that point docker compose at another project or another computer: like -p, -f and -H (Oct 8 QA round 5,
# L1: `COMPOSE_PROJECT_NAME=adguard docker compose down` in the work folder stopped AdGuard).
_COMPOSE_ELSEWHERE = re.compile(r"\b(?:COMPOSE_PROJECT_NAME|COMPOSE_FILE|COMPOSE_PROJECT_DIRECTORY|COMPOSE_ENV_FILES|"
                                r"DOCKER_HOST|DOCKER_CONTEXT|CONTAINER_HOST)=")


def _family(description: str) -> str | None:
    return next((f for p, f in _GATE_FAMILIES if p.match(description or "")), None)


def _in_workspace(words: list[str], here: str | None, elsewhere: bool = False) -> bool:
    """Routine work inside a Zip's own project, which needs no card (Oct 8 QA round 4, M3: Grok Bot and dots do not
    ask about these either): resetting a repo or deleting its branches, removing a package from the project, and
    stopping the project's containers, strictly inside the work folder or /tmp. pip and brew act on the whole
    computer, and global npm removes software for everything else, so those still ask. `elsewhere` is a compose
    project, file or daemon set by a variable in the command."""
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
        # Compose works in the folder of the file it is given, so `-f ~/taffy/work/.../compose.yml` is the
        # project's own wherever it runs from (Oct 8 QA round 5, nitpick T46).
        named = [w for i, w in enumerate(words[:-1]) if w in ("-f", "--file", "--project-directory")]
        return (_safe(here) or bool(named)) and all(_safe(_norm(f, here)) for f in files)
    return False


def _write_targets(seg: str) -> list[str]:
    """The files a simple command writes: its > and >> targets, tee's files, and the destination of cp, mv or
    install."""
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
    """Did the gate's own rules decide this part of a command? The command as a whole already passed the gate, so
    a part it judged was either fine or allowed by Colin on its card."""
    words = _words(seg)
    head = words[0].rsplit("/", 1)[-1] if words else ""
    git = _git_call(words, here)
    if family == "delete":
        return head in ("rm", "unlink", "shred", "find", "xargs", "rsync") or git is not None and git.sub == "clean"
    if family == "script":
        return True   # the gate reads -c scripts and heredocs itself (_unwrap, _fed, _code_delete)
    if family == "sql":
        a = classify_command(seg, cwd=here)
        if a is not None and a.kind == "delete":
            return True
        # SQL fed to a client (`manage.py dbshell <<< "DROP ..."`): the gate's database card covered the command.
        whole = classify_command(cmd, cwd=here) if cmd and cmd != seg else None
        return whole is not None and whole.kind == "delete" and "from a database" in whole.summary or \
            _sql_as_text(cmd or seg)
    if family == "push":
        a = _git_push(seg, here)
        return a is not None and (a.kind != "write" or bool(a.extra.get("scratch")) or _git_safe(words, here))
    if family == "workspace":
        return _in_workspace(words, here, elsewhere)
    if family == "project file":
        # A settings file of the Zip's own project (`cat > .env.local <<EOF` in the work folder, round 5 L2). The
        # gate refuses the protected ones (.env, the Zips' own settings) before this.
        targets = _write_targets(seg)
        return bool(targets) and all(_safe(_norm(t, here)) for t in targets)
    return False


def _substitutions(cmd: str) -> list[str]:
    """The commands inside $(...) and backticks, which run before the command around them."""
    text = _unwrap(_HEREDOC.sub("", cmd))
    scanned = _scan(text)
    if scanned is not None:
        return [t for t in scanned[1] if t.strip()]
    return [a or b for a, b in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", text) if (a or b).strip()]


def _bare(seg: str) -> str:
    """A simple command with what its $(...) and backticks run left out."""
    for _ in range(3):
        new = re.sub(r"\$\([^()]*\)|`[^`]*`", "x", seg)
        if new == seg:
            break
        seg = new
    return seg


def unjudged(cmd: str, cwd: str | None, detect=None) -> str | None:
    """Hermes's description of the first part of this command its own check would ask about that the gate did not
    judge, or None when the gate judged every such part. `detect(text)` is Hermes's detector (hermes_like when it
    is not importable). What runs inside $(...) and backticks and the text fed to a shell or an interpreter are
    checked first, as commands of their own (Oct 8 QA round 5, L1: a restart in backticks or a heredoc rode along
    behind a routine delete). Then each simple command is checked on its own, and then the pipelines, neighbouring
    parts and the whole command as Hermes reads them (with sudo and env still on), so a pattern across parts (`chmod
    +x x; ./x`) or in a wrapper (`sudo -S`) must be one the parts already explained."""
    return _unjudged(cmd, cwd, detect or hermes_like, 0)[0]


def _unjudged(cmd: str, cwd: str | None, detect, depth: int) -> tuple[str | None, set]:
    """(what the gate did not judge, the families of what it did judge) for unjudged."""
    families = {"script"}
    if depth < 2:
        shell, code = _fed(cmd)
        for text in _substitutions(cmd) + shell:
            found, inner = _unjudged(text, cwd, detect, depth + 1)
            if found:
                return found, families
            families |= inner
        for text in code:
            # The programs the code starts are commands of their own, as if typed (Oct 8 QA round 6, L1: a service
            # stopped through subprocess.run([...]) rode along as "a short Python script").
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
            found = detect(_bare(seg))   # what runs inside $(...) was checked above
        git = _git_call(_words(seg), here)
        if not found and git is not None and (git.options or git.alias):
            # Hermes reads git only as written plainly, so what it would find in the plain form is the gate's own to
            # ask about, and _git_option_forms did (round 8, M1).
            plain = detect(git.plain)
            if _family(plain) in _GIT_FAMILIES:
                families.add(_family(plain))
                continue
        if not found or found in _ARTIFACTS:
            continue
        family = _family(found)
        if seg.strip() in system:
            # The gate's system check asked about this part (`sudo systemctl restart nginx`) and Colin allowed it.
            families |= {family or found, found}
            continue
        if family is None or not _judged(seg, here, family, cmd, elsewhere):
            return found, families
        families.add(family)
    pieces = [p.strip() for p in _split(_HEREDOC.sub("", cmd), pipes=False)]
    # The wrappers alone (`sudo -S` in front of a judged delete), since Hermes names only its first finding.
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

# ── Connector requests (Trello, Calendar, Drive, Gmail through Claude Code) ────────────────────────────────

_W = lambda words: re.compile(r"\b(" + words + r")\b", re.I)   # noqa: E731
# Sending, sharing, deleting and answering invitations are matched as verbs: at the start of a clause ("Delete the
# old drafts"), after "please", "can you", "I want you to" and the like, or after "and" or "then". A word in a title or a
# question is not a request to do it: "Find the Empty Bowls flyer", "Who has permission to edit the budget sheet?"
# and "Did the school send the permission slip yet?" are reads (Oct 8 QA round 3, M1).
_DELETE_V = r"(?:permanently\s+)?(?:delete|remove|trash|erase|cancel|empty|clear out|get rid of)"
_SHARE_V = (r"share|unshare|invite|give\b[^.;]*?\baccess|make\b[^.;]*?\bpublic|transfer ownership|"
            r"change\b[^.;]*?\bpermissions?|add\b[^.;]*?\bas (?:an? )?(?:editor|viewer|commenter|collaborator|owner)|"
            r"remove\b[^.;]*?\baccess|add (?:a )?member")
_RSVP_V = r"accept|decline|rsvp|respond to|tentatively accept|say (?:yes|no|maybe) to"
_SEND_V = r"send|reply|respond|forward|email|e-mail|mail|message|write back"
_INVITE = _W(r"invite|invitation|guests?|attendees?|participants?|send (an |the )?invite|notify")
_LEAD = (r"(?:(?:please|pls|also|then|and|now|ok|okay|just|so|can you|could you|would you|will you|i need you to|"
         r"i want you to|go ahead and|let's|lets|you can|you should)\s+)*")
_QUESTION = re.compile(r"^(?:so\s+|and\s+|also\s+|but\s+)?(?:did|does|do|what|what's|whats|who|who's|whom|whose|when|"
                       r"where|which|why|how|has|have|had|is|are|was|were|any|anything|anyone|anybody|is there|"
                       r"are there|was there)\b", re.I)
_ASKING = re.compile(r"^(?:please\s+)?(?:can|could|would|will) you\b", re.I)
# A request worded as a question or an aside still asks for the thing after these words: "Is it possible to delete
# the Old ideas card?", "Why don't you invite Theo Avery to the Thursday sync?", "Do me a favor and delete ..." (Oct 8 QA
# round 4, L2: the whole sentence was dropped as a question, so a Trello delete got no card).
_REQUEST_IN_QUESTION = re.compile(
    r"\b(?:why (?:don'?t|dont|not)(?: you)?|(?:can|could|would|will) you|(?:is it|would it be|is that|is it ok(?:ay)? if"
    r" you) ?(?:possible|ok|okay|alright|all right|fine)?(?: for you)? to|do me a favou?r(?:,| and)|any chance you "
    r"(?:could|can)|how about you)\s+(?:please\s+|just\s+|go ahead and\s+|"
    r"also\s+)*(?=\w)", re.I)


def _clauses(req: str, commas: bool = True) -> list[str]:
    """The parts of a request that ask for something. A question is dropped ("Did Riley reply?") but what follows
    its first comma still counts ("Did Theo reply, and if not, send him a reminder?"), and "Can you send ...?" is a
    request. `commas` splits sentences at commas too, for finding verbs; names in a list need them kept."""
    out = []
    for sent in re.split(r"(?<=[.?!;:])\s+|\s+-\s+", req):
        sent = sent.strip()
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
    """One of these verbs, used as a verb, in a part of the request that asks for something."""
    for c in _clauses(req):
        if re.match(rf"{_LEAD}(?:{verbs})\b", c, re.I) or re.search(rf"\b(?:and|then|please)\s+(?:also\s+)?(?:{verbs})\b",
                                                                      c, re.I):
            return True
    return False


def _title(req: str) -> str:
    """A change's card title and Done line: the request's first sentence, as Colin or the job worded it (Oct 8 QA
    round 3, L6: "Update your Trello board" said nothing about the change)."""
    first = re.split(r"(?<=[.!?])\s+|\n", req.strip(), maxsplit=1)[0].rstrip(".!")
    first = re.sub(r"^(?:please|pls|can you|could you)\s+", "", first, flags=re.I).strip()
    if len(first) > 100:
        first = first[:100].rsplit(" ", 1)[0]
    return first[:1].upper() + first[1:] if first else ""
_WRITE = _W(r"create|add|move|archive|remove|update|rename|label|mark|comment|check off|complete|write|append|edit|"
            r"fill|copy|schedule|block|reschedule|set|change|put|insert|draft|attach|assign")
# "Add Theo Avery to the Thursday meeting": adding a person to an event sends Google's invitation email from Colin's
# account (Oct 8 QA round 2, H3). A capitalized word after "add" is usually the event's title, though ("Add Soccer
# practice Saturday at 9am" became "Invite Soccer", round 3 H3). So a name counts as a guest only where the wording
# makes it one: "add <name> to/on/in <an event>", "invite <name>", "loop in <name>", "send <name> an invite",
# "<name> as a guest", "... and add <name>" after the event, or "with <name>" when the request also asks to
# invite. A name is capitalized words, none of them an event, day or activity word. Anything else is the title,
# and the connector run is told not to add anyone (tools._limits).
_NAME = r"[A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+){0,2}"
_WHO = rf"((?:{_NAME}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)(?:\s*,\s*(?:{_NAME}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+))*" \
       rf"(?:,?\s+(?i:and)\s+(?:{_NAME}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+))?)"
_EVENT = r"([^.,;!?]+)"
_GUEST_PATTERNS = [
    re.compile(rf"\b(?i:add|include|bring)\s+{_WHO}\s+(?i:as\s+(?:an?\s+)?(?:guest|attendee|invitee)s?)"
               rf"(?:\s+(?i:to|on|in|for)\s+{_EVENT})?"),
    re.compile(rf"\b(?i:add|include|bring)\s+{_WHO}\s+((?i:to|on|in|into|at))\s+{_EVENT}"),
    re.compile(rf"\b(?i:invite|loop\s+in)\s+{_WHO}(?:\s+(?i:to|on|in|for)\s+{_EVENT})?"),
    re.compile(rf"\b(?i:send)\s+{_WHO}\s+(?i:an?|the)\s+(?i:invite|invitation|calendar invite)"
               rf"(?:\s+(?i:to|for)\s+{_EVENT})?"),
    re.compile(rf"\b(?i:send)\s+(?i:an?|the)\s+(?i:invite|invitation|calendar invite)\s+(?i:to)\s+{_WHO}"),
    re.compile(rf"\b(?i:and|then|also)\s+(?i:add|include)\s+{_WHO}\s*(?:$|[.,;!?]|\s+(?i:too|as well)\b)"),
]
_WITH_WHO = re.compile(rf"\b(?i:with)\s+{_WHO}")
# Words that are not people: days, months, times, event and activity words, holidays, and the like. A "name" with
# any of these in it is an event's title.
_NOT_PEOPLE = set("""
monday tuesday wednesday thursday friday saturday sunday today tomorrow tonight morning afternoon evening noon
midnight weekend week month year daily weekly monthly every next this last
january february march april may june july august september october november december jan feb mar apr jun jul
aug sep sept oct nov dec
lunch dinner breakfast brunch coffee drinks meeting call standup sync review gym workout run dentist doctor
appointment class lecture event reminder block focus study work office hours deadline due exam quiz test paper
the a an my me myself colin it this that time notes note zoom meet google teams trello gmail calendar drive
busy free hold travel flight trip vacation holiday birthday anniversary church school pickup dropoff practice
halloween thanksgiving christmas xmas easter passover hanukkah chanukah kwanzaa diwali ramadan eid new year
years valentine valentine's valentines memorial labor labour independence juneteenth veterans mlk presidents
columbus mother's father's mothers fathers patrick's st good friday ash wednesday advent lent mass service worship
choir rehearsal recital concert show movie game match tournament party date night haircut nails massage
soccer baseball basketball football hockey tennis golf swim swimming lacrosse volleyball softball track dance
ballet karate piano guitar violin lesson lessons tutoring homework practice band orchestra scouts youth group
small bible prayer sermon sunday's volunteer shift payday rent bills bill trash recycling garbage laundry
chores groceries grocery shopping errands oil change car wash cleaning cleaners vet checkup physical therapy pt
bus train carpool drop pick up yoga pilates hike bike ride walk dog cat cookout bbq barbecue picnic potluck
reservation flight check-in checkout wedding funeral shower graduation reunion conference webinar interview
orientation open house tour visit appt mtg team staff family everyone all
""".split())
# A request to add a person is an invitation only when what they are added to is an event that already exists (Oct 8
# QA round 5, M3). Earlier rounds guessed from what came after "to": a day, a time, a place word. Venues with no
# place word in their name ("Add Science Fair to Thursday at Lincoln Elementary") kept turning titles into guests.
# The rule now is the other way round: after "add <name> to/on/in", the words up to the next preposition must name
# an event ("the Thursday meeting", "Theo's sync", "the board call", "dinner", "soccer practice") and must not start
# with a bare day, date or time. People are added to events, not to days ("to Thursday", "to Saturday morning"), to
# places ("to the gym") or to the calendar. Anything else is a new event's title.
_CALENDAR_WORDS = re.compile(r"\b(?:calendar|cal|schedule|agenda|planner|reminders?)\b", re.I)
_EVENT_WORDS = set("""
meeting meetings call calls sync syncs standup stand-up huddle dinner lunch breakfast brunch party event invite
invitation session review interview appointment webinar retreat game practice rehearsal recital concert show class
lecture workshop kickoff kick-off check-in checkin 1:1 one-on-one catchup catch-up chat hangout coffee drinks
celebration ceremony service shower wedding reunion gathering playdate sleepover hike workout tour visit outing date
festival fair fundraiser potluck picnic bbq barbecue cookout study lesson tutoring reservation trip flight meetup
""".split())
# Words a day, date or time starts with. A possessive ("Thursday's sync") is an event's name, not a day.
_WHEN_WORDS = set("""
today tomorrow tonight morning afternoon evening night noon midnight weekend week weekday month year next every each
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun
mondays tuesdays wednesdays thursdays fridays saturdays sundays
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
""".split())
# Where the event's name ends: a preposition starts its place or time ("the meeting in the library on Tuesday").
_EVENT_END = set("at in on from for with by near until till around after before during about to via over".split())
# A possessive is an event's name, not a day: "to next week's review", "to Thursday's sync" (Oct 8 QA round 6, L3).
_WHEN_AFTER_TO = re.compile(r"\bto\s+(?:the\s+|my\s+|our\s+|this\s+|next\s+)?(?:" + "|".join(sorted(
    w for w in _WHEN_WORDS if w not in ("next", "every", "each", "may"))) + r"|calendar|schedule|\d)\b(?!['’]s\b)", re.I)


def _event_ref(text: str) -> bool:
    """Does this name an event that already exists (what a person can be added to)? See above."""
    if not text or _CALENDAR_WORDS.search(text) or _WHEN_AFTER_TO.search(text):
        return False   # "to my calendar"; "School Night to Thursday": the first "to" was in the title
    name = []
    for w in text.split():
        w = w.strip("'’\".,!?").lower()
        if w in _EVENT_END:
            break
        name.append(w)
    # "next week's review", "this month's board meeting": a day or time that owns the event names it.
    owner = next((i for i, w in enumerate(name) if re.search(r"['’]s$", w) and
                  (re.sub(r"['’]s$", "", w) in _WHEN_WORDS or re.match(r"^\d", w))), None)
    if owner is not None and all(w in _WHEN_WORDS or w in ("the", "this", "that") for w in name[:owner]):
        name = name[owner + 1:]
    elif not name or name[0] in _WHEN_WORDS or re.match(r"^\d", name[0]):
        return False
    return any(re.sub(r"['’]s$", "", w) in _EVENT_WORDS for w in name)


def _names(who: str) -> list[str]:
    """The people in a WHO match: emails as they are, and names cut short at the first event or day word. A name
    that starts with one is not a person ("Soccer Practice", "Mass Sunday")."""
    out = []
    for part in re.split(r"\s*,\s*|\s+(?i:and)\s+", who.strip()):
        if _EMAIL.fullmatch(part):
            out.append(part)
            continue
        words = []
        for w in part.split():
            if w.lower() in _NOT_PEOPLE or re.sub(r"['’]s$", "", w.lower()) in _NOT_PEOPLE:
                break
            words.append(w)
        if words:
            out.append(" ".join(words))
    return out


# "Add Theo Avery in the conference room for the Thursday meeting": a person put somewhere for an event is joining it.
# The event must be one that already exists and is named as such (a day, a possessive, or "our", "my", "that"), so a
# title with a venue ("Add Pumpkin Carving in the backyard for the party") stays a title (Oct 8 QA round 6, L3). "at
# <place>" reads the same way, and only with an owned event after it (round 7: "Add Matt Lee at the cafe for our
# Friday standup"), so "Add Lunch at the Diner" and "Add Trivia at Joe's for the party" stay titles.
_FOR_EVENT = re.compile(r"^(?:the\s+|a\s+|my\s+|our\s+)?[^,.;]*?\s+for\s+(.+)$", re.I)
_OWNED_EVENT = re.compile(r"^(?:(?:the|our|my|this|that|next)\s+)*(?:\w+['’]s\s+|(?:" + "|".join(sorted(
    w for w in _WHEN_WORDS if w not in ("next", "every", "each"))) + r")\b|(?:our|my|that|this)\s)", re.I)
# Sharing an event, including someone, or letting someone know about a change all send Google's email from Colin's
# account: "Share the Thursday meeting with Theo Avery", "Schedule a call with Theo on Thursday and include him",
# "Move the Thursday sync to Friday and let him know".
_SHARE_EVENT = re.compile(rf"\b(?i:share|forward)\s+([^.,;!?]+?)\s+(?i:with|to)\s+{_WHO}")
_LET_KNOW = re.compile(rf"\b(?i:let)\s+({_NAME}|(?i:him|her|them|everyone|everybody|the\s+(?:guests|attendees|others|"
                       rf"group|team)))\s+(?i:know)\b")
_INCLUDE_THEM = re.compile(r"\b(?:include|invite|add|loop\s+in|cc)\s+(?:him|her|them)\b", re.I)


def _people(req: str) -> list[tuple[str, str]]:
    """(person, event) for each person a calendar request clearly adds or invites as a guest."""
    out = []
    for m in _SHARE_EVENT.finditer(req):
        if _event_ref(m.group(1).strip()):
            out += [(n, m.group(1).strip()) for n in _names(m.group(2))]
    for pat in _GUEST_PATTERNS:
        for m in pat.finditer(req):
            if pat is _GUEST_PATTERNS[1]:
                event = m.group(3).strip()
                place = m.group(2).lower() in ("in", "at")
                joined = _FOR_EVENT.match(event) if place and (m.group(2).lower() == "at" or not _event_ref(event)) \
                    else None
                if joined and _event_ref(joined.group(1)) and _OWNED_EVENT.match(joined.group(1)):
                    event = joined.group(1).strip()
                elif m.group(2).lower() == "at":
                    continue   # "Add Lunch at the Diner", "Add Theo at the meeting": only "at <place> for <our event>"
                # "Add Soccer Practice to my calendar", "Add Science Fair to Thursday at Lincoln Elementary", "Add
                # Book Club on Zoom", and "Add Board Meeting in Room B" (an event word inside the name): a title.
                if not _event_ref(event) or any(re.sub(r"['’]s$", "", w.lower()) in _NOT_PEOPLE
                                                for w in re.split(r"[\s,]+", m.group(1)) if w):
                    continue
                out += [(n, event) for n in _names(m.group(1))]
                continue
            event = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
            if event and not _event_ref(event):
                event = ""
            out += [(n, event) for n in _names(m.group(1))]
    if not out and (_INVITE.search(req) or _INCLUDE_THEM.search(req) or _LET_KNOW.search(req)):
        for m in _WITH_WHO.finditer(req):
            out += [(n, "") for n in _names(m.group(1))]
    if not out:
        for m in _LET_KNOW.finditer(req):
            if m.group(1)[:1].isupper() and m.group(1).lower() not in ("him", "her", "them", "everyone", "everybody"):
                out += [(n, "") for n in _names(m.group(1))]
    return list(dict.fromkeys(out))


def classify_connector(service: str, request: str) -> Action:
    req = " ".join((request or "").split())
    short = req[:200]
    site = {"gmail": "gmail.com", "calendar": "calendar.google.com", "drive": "drive.google.com",
            "trello": "trello.com"}.get(service) or OTHER_SITES.get(service, service or "")
    asks = " . ".join(_clauses(req, commas=False))   # the request without its questions
    changes = bool(asks) and _WRITE.search(asks) is not None

    def act(kind, summary, kinds=None, **kw):
        extra = {"request": short, **({"kinds": kinds} if kinds else {})}
        return Action(kind, summary, site=site, inputs=req, extra=extra, **kw)

    def change(fallback):
        return act("write", _title(req) or fallback)

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
        if _verb(_RSVP_V, req) and re.search(r"\b(invit|event|meeting|call)", req, re.I):
            return act("send", "Answer a calendar invitation")
        people = _people(asks)
        if people:
            who = list(dict.fromkeys(n for n, _ in people))
            names = who[0] if len(who) == 1 else ", ".join(who[:-1]) + " and " + who[-1]
            event = next((e for _, e in people if e), "")
            if not event and _LET_KNOW.search(asks) and not (_INVITE.search(asks) or _INCLUDE_THEM.search(asks)):
                summary = f"Send {names} an update about a calendar event"
            else:
                summary = f"Invite {names} to {event}" if event else f"Invite {names} to a calendar event"
            return act("send", summary, target=", ".join(who), scope=names.lower() if len(who) == 1 else "")
        if changes and (_INVITE.search(asks) or _EMAIL.search(asks)):
            found = _recipients(asks)
            to = [a for a in found if a.lower() not in _own_addresses()]
            if found and not to:
                # Only the person's own address, naming their own calendar: an event on it, not an invitation
                # (Oct 10: "On Colin's primary calendar (jdoe1@example.com) ... create one event" became
                # "Send calendar invitations to jdoe1@example.com").
                return change("Add an event to your calendar")
            return act("send", f"Send calendar invitations to {', '.join(to)}" if to else "Send calendar invitations",
                       target=", ".join(to), scope=to[0].lower() if len(to) == 1 else "")
        if _LET_KNOW.search(asks) and (changes or _verb(r"move|reschedule|push|change|cancel", req)):
            return act("send", "Send the guests an update about a calendar event")
        if _verb(_DELETE_V, req):
            return act("delete", "Delete or cancel a calendar event")
        if changes:
            return change("Change your calendar")
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
        # Removing someone from a board changes who can see it, like sharing it (Oct 8 QA round 3, M1).
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


# Any other connector on Colin's Claude account, such as Notion, Slack or Linear (Colin, Oct 8 evening): reading acts,
# a change he can undo tells, sending, posting, sharing, inviting, publishing and deleting ask, and moving money is
# never done (Oct 8 QA round 9, M1). The connectors tool also limits the run's tools to what the request asked for
# (connector_guard.py), so a request that reads cannot refund, merge or post.
OTHER_SITES = {"notion": "notion.so", "slack": "slack.com", "linear": "linear.app", "github": "github.com",
               "asana": "asana.com", "atlassian": "atlassian.net", "jira": "atlassian.net", "confluence": "atlassian.net",
               "figma": "figma.com", "hugging_face": "huggingface.co", "canva": "canva.com", "box": "box.com",
               "dropbox": "dropbox.com", "hubspot": "hubspot.com", "intercom": "intercom.com", "stripe": "stripe.com",
               "netlify": "netlify.com", "todoist": "todoist.com", "airtable": "airtable.com", "monday": "monday.com",
               "zoom": "zoom.us", "clickup": "clickup.com", "sentry": "sentry.io", "vercel": "vercel.com",
               "paypal": "paypal.com", "square": "squareup.com", "shopify": "shopify.com", "quickbooks": "intuit.com"}
# A sentence can say a message is to go out later: "Schedule a message to #general for 9am" sends (round 9, L1).
_OTHER_SEND_V = (r"send|post|reply|respond|forward|message|dm|email|e-mail|comment|announce|notify|ping|mention|"
                 r"write back|schedule\b[^.;]*?\b(?:message|post|dm|email|reminder|announcement|reply)s?")
# Merging, deploying, rolling back, promoting, pushing and opening a pull request publish code or a site (round 9, M1).
_OTHER_PUBLISH_V = (r"publish|unpublish|go live|deploy|redeploy|re-deploy|make\b[^.;]*?\bpublic|roll\s*back|rollback|"
                    r"revert\b[^.;]*?\b(?:commit|deploy|deployment|release|site|production|prod|merge|pr|pull request)|"
                    r"promote\b[^.;]*?\b(?:production|prod|live|deploy|deployment|preview|build|release)|"
                    r"merge\b[^.;]*?\b(?:pull request|pr|branch|main|master|production|prod|release|develop)|"
                    r"merge\s+(?:#|pr\b|pull\b)|release\s+(?:v?\d|version|the\s+(?:new\s+)?(?:version|build|app|release))|"
                    r"push\b[^.;]*?\b(?:main|master|branch|commits?|changes|code|repo|repository|origin|production|prod|"
                    r"live|site|tags?)|(?:open|create|make|submit|raise|file|start)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+|draft\s+)?"
                    r"(?:pull request|pr|merge request)|(?:trigger|start|run|kick off)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
                    r"(?:\w+\s+)?(?:build|deploy|deployment|release)")
# Up to three words between a verb and what it acts on, but not "about", "of" and the like, so "Create a report of
# last month's payments" is a change and "Issue a $40 refund" is money.
_FEW = (r"(?:(?!(?:about|of|on|for|regarding|from|in|into|with|to|listing|called|named|titled|showing|that|which)\b)"
        r"\S+\s+){0,3}?")
_AMOUNT = r"(?:[$£€]\s?\d[\d,.]*|\d[\d,.]*\s*(?:dollars|bucks|usd|eur|euros?|pounds|gbp)\b)"
# Moving money: paying, refunding, charging, paying out, transferring, sending an amount, and invoices, payment links
# and subscriptions that bill (round 9, M1). Zips never do these (the house rules).
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
# Ordinary changes worded with verbs the Google and Trello words miss: "Close the login bug issue", "Transition
# PROJ-12 to Done" (round 9, L1). They tell.
_OTHER_CHANGE_V = (r"close|reopen|re-open|transition|resolve|unassign|tag|untag|pin|unpin|star|unstar|duplicate|"
                   r"restore|convert|link|unlink|prioriti[sz]e|deprioriti[sz]e|snooze|upload|import|sort|merge|estimate|"
                   r"toggle|enable|disable|watch|unwatch|follow|unfollow|start|stop|finish|record|bump|upgrade|"
                   r"downgrade|transfer|push|delay|postpone|wire\s+up|hook\s+up|connect|configure|install")
_MONEY_NEVER = "Zips never pay, refund, charge, transfer money or change billing. Tell Colin what you found so he can do it."


def classify_other_connector(service: str, req: str, asks: str, changes: bool, act, change) -> Action:
    name = " ".join(w.capitalize() for w in (service or "the account").split("_"))
    if _verb(_OTHER_MONEY_V, req):
        return act("pay", _title(req) or f"Move money in {name}", why_never=_MONEY_NEVER)
    found = []   # every kind that asks, in the card's order; the run's guard opens all of them once Colin says yes
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
    # Pushing through the GitHub connector counts as a push for Ask First rules, as a git push does.
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


# ── Browser clicks, by the label of the element ───────────────────────────────────────────────────────────

_CLICK_PAY = _W(r"pay|pay now|buy|buy now|purchase|checkout|check out|place (your |my )?order|confirm (your )?(purchase|order|payment)|"
                r"complete (purchase|order|payment)|submit (order|payment)|donate|transfer|send money|add payment|"
                r"start (a |my )?subscription|subscribe and save|upgrade|request (a )?ride|order now")
_CLICK_SECURITY = _W(r"change password|update password|reset password|new password|two-factor|2-step|2fa|"
                     r"security settings|turn off (two|2)|remove (security|passkey)")
_CLICK_BOOK = _W(r"book|book now|reserve|confirm (booking|reservation|appointment)|schedule (appointment|now)")
_CLICK_PUBLISH = _W(r"publish|publish deploy|deploy|go live|post|tweet|merge|make public|share publicly")
# Buttons that send outright. "Reply", "Comment", "Message", "Share" and "Invite" usually only open a composer or a
# dialog, so they count as sends only once the Zip has typed into the page (Oct 8 QA round 2, M1).
_CLICK_SEND = _W(r"send|send message|send invite|send invitation|send now|reply all|forward")
_CLICK_SEND_WHEN_COMPOSED = _W(r"reply|comment|post comment|message|share|invite|submit comment")
# A sign-in's second step on ordinary sites (Fable's tier table: the 2FA entry is act).
_CLICK_CODE = _W(r"send (me )?(a |the )?(verification |security |login |sign-in |one-time )?code|text me( a code)?|"
                 r"email me( a code| a link)?|call me|resend( code| link)?|send (me )?(a )?(sign-in |login |magic )?link|"
                 r"get (a )?code|verify by (text|email|sms)")
_CLICK_SUBMIT = _W(r"submit|apply now|apply for|apply to|easy apply|submit application|sign up|register|enroll|"
                   r"file (the )?claim|turn in|save batch")
# "Finish" ends a wizard; it submits something only when the Zip filled in fields (Oct 8 QA round 3, L1).
_FINISH = re.compile(r"^(finish|finish and submit|complete)$", re.I)
# Buttons that confirm whatever the page or its dialog is about: judged by that dialog or heading (round 3, L1:
# Netflix's "Confirm" after "Cancel your membership?" and GitHub's "Yes, I understand" passed with no card).
_CONFIRM = re.compile(r"^(confirm|yes|yes,? confirm|i understand|yes,? i understand|i'?m sure|yes,? i'?m sure|"
                      r"i understand the consequences.*|yes,? do it)$", re.I)
_CONFIRM_IN_DIALOG = re.compile(r"^(ok|okay|continue|proceed|yes,? continue|yes,? proceed)$", re.I)
_CLICK_DELETE = _W(r"delete|cancel (my |the |your )?(subscription|order|membership|plan|account)|empty trash|"
                   r"permanently|close account|remove (account|member|user|collaborator|contact|repository|site|"
                   r"device|photo|file|card|post)")
_CLICK_ACCOUNT = _W(r"accept|i agree|agree and continue|accept terms|change (email|plan)|downgrade|cancel plan")
_CLICK_COOKIE = re.compile(r"cookie|consent", re.I)
# What a cookie banner's buttons say; on a page with a cookie or consent notice they are not accepting terms.
_COOKIE_GENERIC = re.compile(r"^(accept|accept all|allow|allow all|agree|i agree|i accept|ok|okay|got it|"
                             r"(accept|agree|allow)( all)? (and|&) (close|continue))$", re.I)
_CLICK_TERMS = _W(r"terms|conditions|agreement|policy|contract|eula")
# Invitation answers: the buttons on calendar and invitation pages, and on any page that shows an invitation.
_RSVP_LABEL = re.compile(r"^(yes|no|maybe|going|not going|can'?t go|cannot go|rsvp|rsvp now|decline|accept|tentative|"
                         r"attending|not attending|interested|i'?m going|count me in|yes,? i'?ll (be there|attend)|"
                         r"no,? i can'?t (make it|attend))$", re.I)
_INVITE_SITE = re.compile(r"(^|\.)(calendar\.google\.com|outlook\.(live|office|office365)\.com|evite\.com|partiful\.com|"
                          r"paperlesspost\.com|punchbowl\.com|greenvelope\.com|lu\.ma|luma\.com|calendar\.yahoo\.com|"
                          r"icloud\.com)$", re.I)
_CLICK_TELL = _W(r"add to cart|add to bag|unsubscribe|save( draft)?|archive|remove")
_OPENS_COMPOSER = re.compile(r"^(new|create|write|compose|start|add)\b", re.I)


def _compose(fields: dict) -> tuple[list[str], str]:
    """(recipients, text) from the fields the Zip filled on the page: the To line, the subject and the message."""
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


# A button that declines or skips ("Don't send" in Google Calendar's guest dialog) does nothing risky (Oct 8 QA
# round 4, L3). RSVP answers such as "No" and "Not going" are matched before this, since they tell the host.
_DECLINE = re.compile(r"^(?:no,?\s+)?(?:don[’']?t|do not|never|skip|no thanks|not now|maybe later)\b", re.I)


def classify_click(label: str, site: str = "", page: dict | None = None) -> Action | None:
    """A browser click on an element with this accessible label, or None when it is ordinary browsing. `page` is
    what the gate knows about the page: the fields the Zip filled ("fields") and whether it has a cookie or consent
    notice ("cookie")."""
    label = " ".join((label or "").split())
    if not label:
        return None
    page = page or {}
    say = f"Click \"{label[:80]}\"" + (f" on {site}" if site else "")
    if _CLICK_SECURITY.search(label):
        return Action("account", say, site=site, target=site, inputs=label,
                      why_never="Zips never change passwords, 2FA or security settings. Colin does that himself.")
    if _CLICK_PAY.search(label) and not _CLICK_TELL.search(label):
        return Action("pay", say, site=site, target=site, inputs=label,
                      why_never="Zips never pay, order or move money. Stop before this button and tell Colin it is "
                                "ready for him.")
    if re.search(r"\b(cart|bag|basket)\b", label, re.I) and not _CLICK_PAY.search(label.replace("checkout", "")):
        return Action("write", say, site=site, target=site, inputs=label)   # editing a cart is reversible
    if _CLICK_CODE.search(label):
        return None
    if not _CLICK_TERMS.search(label) and (_CLICK_COOKIE.search(label) or (page.get("cookie") and _COOKIE_GENERIC.match(label))):
        return None   # a cookie banner
    if consent_base(site) == "linkedin.com" and re.match(r"^(connect|invite .* to connect)\b", label, re.I):
        return Action("send", "Send a LinkedIn connection request", site=site, target=site, inputs=label)
    if _RSVP_LABEL.match(label) and (page.get("invite") or _INVITE_SITE.search(site or "")):
        # Answering an invitation tells its host and the other guests (Oct 8 QA round 3, M4).
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
    if kind is None and composed and _CLICK_SEND_WHEN_COMPOSED.search(label) and not _OPENS_COMPOSER.search(label):
        kind = "send"
    if kind is None and composed and _FINISH.match(label):
        kind = "submit"
    if kind is None and (_CONFIRM.match(label) or page.get("dialogs") and _CONFIRM_IN_DIALOG.match(label)):
        for about in reversed(page.get("dialogs") or page.get("headings") or []):
            a = classify_click(about, site, {"fields": page.get("fields")})
            if a is not None and a.kind not in ("write", "read", "navigate"):
                a.summary = f"{say}: {' '.join(about.split())[:100]}"
                a.inputs = f"{label}\n\n{about}"[:4000]
                a.scope = ""
                return a
    if kind in ("publish", "send") and _OPENS_COMPOSER.search(label) and not composed:
        kind = None   # "New post", "Write a message": opens the composer
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
            # Titled from what the Zip filled in, not the button (Oct 8 QA round 3, M2).
            if re.search(r"(^|\.)(mail\.google|outlook\.(live|office|office365)|mail\.yahoo|icloud|proton(mail)?|"
                         r"fastmail)\.", site + ".", re.I):
                say = _send_summary("email", to, _subject(text))
            else:
                say = f"Send a message to {', '.join(to)} on {site}" if to else f"Send a message on {site}"
    return Action(kind, say, site=site, target=target, inputs=inputs,
                  scope=target.lower() if kind == "send" and target != site and "," not in target else "")


def consent_base(site: str) -> str:
    """The company part of a host, for the few site rules here (consent.base has the full rule)."""
    parts = (site or "").lower().strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else site


# ── Scheduled jobs that run a script (Oct 8 QA round 6, M1) ───────────────────────────────────────────────
#
# Hermes's cronjob tool can run a script from the Zip's scripts folder on every tick, and with no_agent the script is
# the whole job: no Zip runs, so nothing it does passes through the gate or Hermes's own check. So creating or
# changing such a job is judged by what its script does, read with the same checks as a command typed now: refused
# when any part is something Zips never do, a card when any part would ask (Allow covers that exact script text), and
# a told line otherwise. A script the gate cannot read asks, and so does one that runs another file, which could
# change later. Editing the script of a job that exists is judged the same way.
JOB_TOOLS = {"cronjob", "cronjob_manage"}   # Hermes's name for it, and the older name it still accepts
_SCRIPTS_PATH = re.compile(r"(?:~|\$HOME|\$\{HOME\}|/home/[^/\s'\"]+|/Users/[^/\s'\"]+)/\.taffy/(?:profiles/[^/\s'\"]+/)?"
                           r"scripts/[^\s'\";&|<>]+")
_FILE_RUNNER = re.compile(r"^(?:(?:ba|z|da|k)?sh|source|\.|python[\d.]*|pypy[\d.]*|node|perl|ruby|deno|bun|php|tsx|ts-node)$")
_SYSTEM_BIN = re.compile(r"^(?:/(?:usr|bin|sbin|opt|snap|nix|lib|etc)/|~/\.local/bin/|~/\.cargo/bin/|~/go/bin/|~/\.npm-global/)")


@dataclass
class JobScripts:
    """What the gate knows about a Zip's scheduled scripts: Hermes's detector, how to read a file (a job's script
    is named relative to the Zip's scripts folder), and which jobs run a file."""
    detect: object                       # detect(text) -> Hermes's description or None
    read: object                         # read(path, in_scripts: bool) -> the file's text, or None when unreadable
    users: object = lambda path: []      # users(path) -> the names of jobs that run this file


def _runs_file(words: list[str]) -> str | None:
    """The file a simple command runs as a program or script (`bash deploy.sh`, `python3 tidy.py`, `./run`), or None.
    Installed programs (/usr/bin/himalaya, ~/.local/bin/x) are not files a Zip writes."""
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
                return None   # code given inline, or a module
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
    """(what this script does that is not plain work, the other files it runs). Hermes runs .sh and .bash with bash
    and anything else with Python (cron/scheduler_script.py)."""
    shell = name.lower().endswith((".sh", ".bash")) or "\n" in name and not re.match(r"#!\S*python", text)
    detect = jobs.detect if jobs is not None else hermes_like
    found = [classify_command(text, code=not shell, detect=None if shell else detect)]
    commands = [text] if shell else [c for c, _unsure in _code_commands(text)]
    if shell:
        flagged = unjudged(text, None, detect)
        if flagged and flagged not in _ARTIFACTS:
            found.append(Action("system", _consent().plain_command(flagged, text), inputs=text))
    others = [f for c in commands for seg, _here in _segments(c, None) for f in [_runs_file(_words(seg))] if f]
    return _combine(found, text), list(dict.fromkeys(others))


def _script_card(title: str, paths: list[str], texts: list[tuple[str, str | None]], schedule: str, parts: list[Action],
                 others: list[str]) -> Action:
    """The card for a job whose script would do something that needs Colin's yes: what runs on its own and when,
    what the script would do each time, and the script itself behind Details."""
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
                  extra={"content_hash": digest})


def _judge_job_scripts(title: str, told: str, paths: list[str], texts: list[tuple[str, str | None]],
                       schedule: str, jobs: JobScripts | None) -> Action:
    """Refused, a card, or a told line for scripts that will run on their own (see above)."""
    parts, others, seen = [], [], {p for p, _t in texts}
    for p, text in list(texts):
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
            if f not in seen and len(texts) < 6:
                seen.add(f)
                texts.append((f, jobs.read(f, False) if jobs is not None else None))
    if parts or others or any(t is None for _p, t in texts):
        return _script_card(title, paths, texts, schedule, parts, others)
    return Action("write", told)


def classify_job(args: dict, jobs: JobScripts | None) -> Action | None:
    """Creating or changing a scheduled job with Hermes's cronjob tool. A job without a script is a told line, as
    before; a job with one is judged by its script."""
    if str(args.get("action") or "").lower() not in ("create", "add", "update", "edit"):
        return None
    name = str(args.get("name") or "").strip()
    told = f"Set up a scheduled job: {(name or str(args.get('prompt') or args.get('job_id') or ''))[:120]}"
    paths = [str(args[k]).strip() for k in ("script", "monitor_script", "monitor")
             if isinstance(args.get(k), str) and args[k].strip() and not re.match(r"^https?://", args[k].strip())]
    if not paths:
        return Action("write", told)
    texts = [(p if "\n" not in p else "the script", p if "\n" in p else (jobs.read(p, True) if jobs is not None else None))
             for p in paths]
    creating = str(args.get("action")).lower() in ("create", "add")
    title = (f"Set up \"{name}\" to run on its own" if name else "Set up a script to run on its own") if creating else \
        (f"Change the script \"{name}\" runs on its own" if name else "Change the script a scheduled job runs on its own")
    return _judge_job_scripts(title, told, [p for p, _t in texts], texts, str(args.get("schedule") or "").strip(),
                              jobs)


def _job_script(path: str, cwd: str | None, jobs: JobScripts) -> tuple[str, list[str]] | None:
    """(the path as written, the jobs that run it) when this path is the script of a scheduled job."""
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
    """Writing the script of a scheduled job that exists, with write_file, patch or the terminal: judged like the job
    itself, since the next run uses the new text with no other check."""
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
            text = None   # what the file will hold, when the command says it plainly
            if head in ("cat", "tee") and len(bodies) == 1:
                text = bodies[0]
            elif head in ("echo", "printf"):
                m = _ECHO_TEXT.search(seg)
                text = next((g for g in m.groups() if g is not None), "").replace("\\n", "\n") if m else None
            elif head in ("cp", "install") and len([w for w in words[1:] if not w.startswith("-")]) == 2:
                text = jobs.read(_norm([w for w in words[1:] if not w.startswith("-")][0], here) or "", False)
            found.append(_script_edit(hit[0], hit[1], text, jobs, cmd))
    return _combine(found, cmd)


# ── Every tool ────────────────────────────────────────────────────────────────────────────────────────────

# Tools that only read, search, look or keep the Zip's own notes and board.
_ACT_TOOLS = {"web_search", "web_extract", "read_file", "search_files", "browser_snapshot", "browser_back",
              "browser_scroll", "browser_get_images", "browser_vision", "vision_analyze", "session_search",
              "memory", "todo", "clarify", "skill_view", "skills_list", "team_note", "save_fact", "recall", "ask_teammate",
              "tell_teammate", "react", "offer_choices", "ask_to_sign_in", "close_sign_in_card", "close_card", "share_screen", "tally",
              "write_file", "patch", "process_manage", "delegate_task", "text_to_speech", "browser_navigate",
              "browser_vault_list", "browser_vault_fill", "browser_vault_enter_code", "browser_console"}


# Fields a Zip never types into itself: browser_type and every typed step of browser_steps (Oct 8 QA round 7, M1).
SECRET_FIELD = re.compile(r"password|passcode|security code|one-time code", re.I)
TYPE_SECRET_NEVER = ("Never type a password or code yourself. Saved logins are filled with browser_vault_fill, and 2FA "
                     "with browser_vault_enter_code.")


def classify(tool_name: str, args: dict, click_label: str = "", site: str = "", cwd: str | None = None,
             page: dict | None = None, jobs: JobScripts | None = None) -> Action | None:
    """The action a tool call is, or None for plain work (act). Sign-ins and page visits are decided by gate._check,
    and the Mac's own approval covers mac_* tools, so they are not classified here except for what mac_run runs.
    `cwd` is the folder a command runs in when the gate knows it; `page` is what it knows about the browser page;
    `jobs` is what it knows about the Zip's scheduled scripts and Hermes's own check."""
    args = args if isinstance(args, dict) else {}
    if tool_name == "email":   # the host-side mail tool (email_tool.py) judges its own calls
        from . import email_tool
        return email_tool.classify(args)
    if tool_name == "calendar":   # the host-side calendar tool (calendar_tool.py), tiered as the connector calendar is
        from . import calendar_tool
        return calendar_tool.classify(args)
    if tool_name in ("trello", "drive", "outlook", "dropbox", "notion", "linear", "github"):
        # apps connected in Connect Apps (apps_tool.py): reading acts, changes ask with a card
        from . import apps_tool
        return apps_tool.classify(tool_name, args)
    if tool_name in ("terminal", "execute_code"):
        folder = str(args.get("workdir")) if args.get("workdir") else cwd
        a = classify_command(str(args.get("command") or args.get("code") or ""), cwd=folder,
                             code=tool_name == "execute_code",
                             detect=jobs.detect if jobs is not None and tool_name == "execute_code" else None)
        if tool_name == "terminal" and (a is None or floor(a.kind) != "never"):
            edit = classify_script_edit(tool_name, args, folder, jobs)
            if edit is not None:
                # The script edit leads: words in the new script are what will run on its own, not now.
                return edit if a is None or floor(edit.kind) == "never" else _combine([edit, a], str(args.get("command")))
        return a
    if tool_name == "mac_run":
        a = classify_command(str(args.get("command") or ""), where=MAC)
        if a is not None and "the Mac" not in a.summary:
            a.summary += " on the Mac"
        return a
    if tool_name == "connectors":
        a = classify_connector(str(args.get("service") or ""), str(args.get("request") or ""))
        return None if a.kind == "read" else a
    if tool_name == "send_message":
        target = str(args.get("target") or args.get("chat_id") or args.get("platform") or "")
        return Action("send", f"Send a message to {target or 'someone'}", target=target,
                      inputs=str(args.get("message") or args.get("text") or ""), scope=target)
    if tool_name == "browser_click":
        return classify_click(click_label, site, page)
    if tool_name == "browser_type" and SECRET_FIELD.search(click_label):
        return Action("secret", f"Type into the \"{click_label[:60]}\" field", site=site,
                      why_never=TYPE_SECRET_NEVER)
    if tool_name in JOB_TOOLS:
        return classify_job(args, jobs)
    if tool_name in ("write_file", "patch"):
        return classify_script_edit(tool_name, args, cwd, jobs)
    return None
