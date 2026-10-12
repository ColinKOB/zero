"""When a bot may sign in to a website with a saved login.

A bot signs in only with one of three go-aheads from Colin:
  1. He asked for something that needs the site: a message from the app (or work handed down from
     one) opens a "pass" carrying his words, and a quick relevance check confirms the site fits them.
  2. He pressed a button: Allow Once or Always Allow on a request card, Save Login on a login card,
     or Send Answer on a task.
  3. A standing setting for that bot and site ("Anytime").
Scheduled jobs never carry a pass. Sensitive sites (money, health, government, account settings)
always ask unless Colin set them to Anytime himself.

Shared by the taffy-tools plugin (which gates the vault tools) and the hub (which opens passes,
shows request cards and stores Colin's choices). Lives in one SQLite file under HERMES_HOME.
"""

import hashlib
import json
import os
import pwd
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse


def _home_settings():
    """The home's settings reader (home.py). The hub loads this file on its own, outside the plugin's package, so the
    reader is loaded by path then; both copies share their pins (see home.py)."""
    if __package__:
        from . import home
        return home
    import importlib.util
    import sys
    mod = sys.modules.get("taffy_tools.home") or sys.modules.get("taffy_plugin_home")
    if mod is None:
        spec = importlib.util.spec_from_file_location("taffy_plugin_home",
                                                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "home.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["taffy_plugin_home"] = mod
        spec.loader.exec_module(mod)
    return mod


home = _home_settings()
REAL_HOME = Path(os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir)
DB_PATH = home.path("hermes_home", REAL_HOME) / "consent.db"

MODES = ("when_asked", "ask", "anytime")
MESSAGE_PASS_SEC = 45 * 60        # a request from Colin covers sign-ins for this long
TASK_PASS_SEC = 24 * 3600         # work handed down from his request keeps it for a day
ONCE_SEC = 30 * 60                # Allow Once covers one sitting

# Test mode (Zip sandbox plan, stage 3): a Zip run from the Hermes command line with TAFFY_TEST_CARDS=1 records its
# cards, sign-ins and "tell" actions here marked as tests. The hub never lists, pushes or shows them (pending,
# requests_after, actions_after and the rest leave them out), Colin's answers never cover them and theirs never
# cover his, and Always Allow on one makes no rule. alien/bin/zip-test-cards lists and answers them.
TEST_ENV = "TAFFY_TEST_CARDS"


def test_mode() -> bool:
    """True in a process started with TAFFY_TEST_CARDS=1 (a Hermes CLI run for testing). The gateway and the hub never
    set it."""
    return os.environ.get(TEST_ENV, "").strip() == "1"


def _test() -> int:
    return 1 if test_mode() else 0

# Where signing in can move money, touch health records or change an account. A reviewed list of companies
# plus a few category patterns, checked on the company part of the host (see base). Oct 8 QA: the old
# substring pattern flagged mayoclinic.org, healthline.com, weather.gov, bankrate.com and tally.so and missed
# bilt.com, ally.com, sofi.com, affirm.com and others. Opening a public page is never gated by this list;
# it applies to sign-ins and to using a session the bot already has.
SENSITIVE_COMPANIES = frozenset("""
chase.com wellsfargo.com bankofamerica.com capitalone.com americanexpress.com amex.com discover.com citi.com
citibank.com usbank.com pnc.com truist.com tdbank.com ally.com sofi.com synchrony.com synchronybank.com
barclaycardus.com barclays.com marcus.com bilt.com biltrewards.com navyfederal.org penfed.org usaa.com
huntington.com regions.com key.com keybank.com citizensbank.com 53.com mtb.com santanderbank.com chime.com
varo.com bmo.com bankofthewest.com firstrepublic.com hsbc.com
schwab.com fidelity.com vanguard.com etrade.com robinhood.com coinbase.com kraken.com gemini.com binance.us
webull.com wealthfront.com betterment.com acorns.com stash.com merrilledge.com interactivebrokers.com
tdameritrade.com public.com m1.com
paypal.com venmo.com cash.app squareup.com zellepay.com wise.com stripe.com affirm.com klarna.com afterpay.com
sezzle.com plaid.com simplefin.org
intuit.com hrblock.com taxact.com freetaxusa.com id.me login.gov
geico.com progressive.com statefarm.com allstate.com libertymutual.com nationwide.com travelers.com
bluecrossnc.com bcbs.com anthem.com aetna.com cigna.com uhc.com myuhc.com humana.com kaiserpermanente.org
healthcare.gov medicare.gov
zocdoc.com cvs.com walgreens.com riteaid.com goodrx.com labcorp.com questdiagnostics.com followmyhealth.com
""".split())
# Account-security hosts of companies that are otherwise ordinary (Gmail and Drive are fine; the Google account
# page is not).
SENSITIVE_HOSTS = frozenset("""
accounts.google.com myaccount.google.com appleid.apple.com account.apple.com iforgot.apple.com
account.microsoft.com account.live.com login.live.com
""".split())
# Categories by the company name: banks and credit unions, government, hospital and clinic portals (.org), MyChart.
SENSITIVE_PATTERN = re.compile(
    r"(^[\w-]*(bank|banking|fcu|creditunion)\.(com|org|net)$|\.bank$|\.gov$|\.mil$|"
    r"^[\w-]*(health|hospital|medical|clinic)[\w-]*\.org$|(^|\.)mychart\.)", re.I)
# Public pages people open all the time that match a category above but never need a sign-in.
PUBLIC_SITES = frozenset("""
weather.gov nps.gov recreation.gov noaa.gov usa.gov unclaimed.org missingmoney.com
""".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS passes (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, bot_id TEXT, session_id TEXT, task_id TEXT,
  text TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
CREATE INDEX IF NOT EXISTS passes_session ON passes(session_id);
CREATE INDEX IF NOT EXISTS passes_task ON passes(task_id);
CREATE TABLE IF NOT EXISTS modes (
  bot_id TEXT NOT NULL, site TEXT NOT NULL, mode TEXT NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY (bot_id, site));
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, site TEXT NOT NULL, reason TEXT NOT NULL,
  session_id TEXT, task_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
  created REAL NOT NULL, decided REAL);
CREATE TABLE IF NOT EXISTS signins (
  id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, site TEXT NOT NULL, reason TEXT NOT NULL,
  how TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS relevance (
  pass_id INTEGER NOT NULL, site TEXT NOT NULL, ok INTEGER NOT NULL, PRIMARY KEY (pass_id, site));
CREATE TABLE IF NOT EXISTS signed_in (
  bot_id TEXT NOT NULL, site TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (bot_id, site));
CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, site TEXT,
  created REAL NOT NULL, last_used REAL, uses INTEGER NOT NULL DEFAULT 0, source TEXT);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, kind TEXT NOT NULL, site TEXT, summary TEXT NOT NULL,
  how TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS content (
  key TEXT PRIMARY KEY, source TEXT NOT NULL, excerpt TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS judgments (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS page_refs (
  key TEXT PRIMARY KEY, site TEXT, refs TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS commands (
  hash TEXT NOT NULL, bot_id TEXT NOT NULL, cleared INTEGER NOT NULL, flagged TEXT, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS commands_hash ON commands(hash);
"""
SCHEDULED_DENY_SEC = 24 * 3600   # Not Now on a card from scheduled work holds for a day
REQUEST_TTL_SEC = 6 * 3600        # an unanswered card from a chat expires after this
REQUEST_TTL_LONG_SEC = 24 * 3600  # one from scheduled work or a board task after a day
SIGNIN_REPEAT_SEC = 30 * 60       # repeated sign-ins to one site in one sitting are logged once


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    for p in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal"), DB_PATH.with_name(DB_PATH.name + "-shm")):
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    for col in ("why TEXT",        # why the card came up, for its wording
                "kind TEXT",       # signin (the default), navigate, or an action kind (send, publish, delete...)
                "target TEXT",     # action cards: who or what the action is aimed at
                "inputs TEXT",     # action cards: exactly what will be sent or run, as Colin sees it
                "fingerprint TEXT",  # action cards: the exact tool call that Allow Once covers
                "scope TEXT",      # action cards: what Always Allow would cover; empty when it is not offered
                "excerpt TEXT",    # content the Zip read just before, when that may be where the idea came from
                "used REAL",       # when the approved action ran (Allow Once covers one run)
                "details TEXT",    # action cards: the exact command, behind Details on the card
                "routine TEXT",    # cards from scheduled work: the job's id, so the answer goes to its next run
                "routine_name TEXT",
                "likeness TEXT",   # cards from scheduled work: what Allow Once covers in the job's next run (JSON)
                "test INTEGER",    # 1 for a card raised in test mode; the hub never shows it (see test_mode)
                "ask_rule INTEGER"):   # the Ask First rule that paused this action, when one did (Custom Rules)
        _add_column(c, "requests", col)
    _add_column(c, "actions", "test INTEGER")
    _add_column(c, "signins", "test INTEGER")
    _add_column(c, "content", "site TEXT")   # the site a page was read on
    _add_column(c, "commands", "place TEXT")   # a hash of the folder the command ran in
    return c


def _add_column(c: sqlite3.Connection, table: str, col: str) -> None:
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e):
            raise


def site_of(url_or_host: str) -> str:
    """The host a login belongs to, lowercased, without "www."."""
    s = (url_or_host or "").strip().lower()
    host = urlparse(s).hostname if "://" in s else s.split("/")[0].split(":")[0]
    return (host or "").removeprefix("www.")


_PSL_FILE = Path("/usr/share/publicsuffix/public_suffix_list.dat")
_psl: tuple[set, set, set] | None = None


def _suffix_rules() -> tuple[set, set, set]:
    """The Public Suffix List, private section included, so sharepoint.com, github.io, myshopify.com and
    co.uk count as shared suffixes rather than companies. Loaded once from the system copy."""
    global _psl
    if _psl is None:
        exact, wild, exceptions = set(), set(), set()
        try:
            for line in _PSL_FILE.read_text(encoding="utf-8").splitlines():
                rule = line.strip().split(" ")[0].lower()
                if not rule or rule.startswith("//"):
                    continue
                if rule.startswith("!"):
                    exceptions.add(rule[1:])
                elif rule.startswith("*."):
                    wild.add(rule[2:])
                else:
                    exact.add(rule)
        except OSError:
            import logging
            logging.getLogger("taffy.consent").warning(
                "No Public Suffix List at %s: sites are grouped by their last two labels", _PSL_FILE)
        if exact:
            # Hosts that give every customer a subdomain but are not on the list.
            exact |= {"sharepoint.com", "my.salesforce.com", "force.com", "my.site.com", "zendesk.com",
                      "atlassian.net", "slack.com", "notion.site", "wixsite.com", "squarespace.com", "wordpress.com",
                      "instructure.com", "moodlecloud.com", "okta.com", "teachable.com", "thinkific.com",
                      "bubbleapps.io", "softr.app", "glide.page", "webflow.io"}
        _psl = (exact, wild, exceptions)
    return _psl


def base(site: str) -> str:
    """The company part of a host: identity.bloomerang.co and crm.bloomerang.co are both bloomerang.co.
    Go-aheads and settings for one apply to the other, since sites often sign in on a sibling host. Hosts
    under a shared suffix stay separate: lakeview.sharepoint.com is not othercollege.sharepoint.com."""
    host = (site or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) < 2:
        return host
    exact, wild, exceptions = _suffix_rules()
    if not exact:
        return ".".join(parts[-2:])
    suffix_len = 1
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        if cand in exceptions:
            suffix_len = len(parts) - i - 1
            break
        if cand in exact or (i + 1 < len(parts) and ".".join(parts[i + 1:]) in wild):
            suffix_len = len(parts) - i
            break
    if suffix_len >= len(parts):
        return host
    return ".".join(parts[-(suffix_len + 1):])


def is_sensitive(site: str, kind: str = "signin") -> bool:
    """Money, health, government or account-security sites. Opening a public page (kind "navigate") is
    ordinary browsing everywhere; only signing in, or using a session the bot already has, is sensitive."""
    if kind == "navigate":
        return False
    host = site_of(site)
    if not host:
        return False
    company = base(host)
    if host in PUBLIC_SITES or company in PUBLIC_SITES:
        return False
    return (host in SENSITIVE_HOSTS or company in SENSITIVE_COMPANIES
            or bool(SENSITIVE_PATTERN.search(company)) or bool(SENSITIVE_PATTERN.search(host)))


# ── Passes: Colin's own requests ────────────────────────────────────────────────────────────

_SECRETISH = re.compile(r"\S{16,}")
# Keys and tokens by their known shapes: OpenAI, Anthropic and Stripe keys, GitHub, GitLab, Slack, AWS, Google,
# npm, Netlify, SendGrid and JWTs.
_SECRET_SHAPES = re.compile(
    r"\b(?:sk|pk|rk)[-_](?:live[-_]|test[-_]|proj[-_]|ant[-_]|or[-_])?[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|"
    r"\bgithub_pat_[A-Za-z0-9_]{20,}|\bglpat-[A-Za-z0-9_-]{16,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b|"
    r"\bAIza[0-9A-Za-z_-]{30,}|\bya29\.[0-9A-Za-z_-]{20,}|\bnpm_[A-Za-z0-9]{30,}|\bnfp_[A-Za-z0-9]{20,}|"
    r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
# A value given to a secret-looking option, header or variable: --auth X, Authorization: Bearer X, API_KEY=X,
# mysql -pX, and the password in https://user:pass@host.
_SECRET_VALUES = [
    re.compile(r"(?P<pre>(?i:(?<![\w-])--?(?:auth|token|password|passwd|api-?key|secret|access-?token|client-?secret|"
               r"private-?key))(?:\s+|=)['\"]?)(?P<val>[^\s'\"]+)"),
    re.compile(r"(?P<pre>(?i:authorization|x-api-key|api-key|x-auth-token)\s*:\s*(?:(?i:bearer|basic|token)\s+)?)"
               r"(?P<val>[^\s'\"]+)"),
    re.compile(r"(?P<pre>\b(?i:bearer)\s+)(?P<val>[A-Za-z0-9._~+/=-]{12,})"),
    re.compile(r"(?P<pre>\b[A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|APIKEY|AUTH)[A-Za-z0-9_]*\s*[=:]\s*['\"]?)"
               r"(?P<val>[^\s'\"]+)"),
    re.compile(r"(?P<pre>\b(?i:password|passwd|passcode|token|api[_ ]?key|secret)\s*(?:[=:]|\bis\b)\s*['\"]?)"
               r"(?P<val>[^\s'\"]+)"),
    re.compile(r"(?P<pre>\b(?:mysql|mysqldump|mariadb)\b[^|;&\n]*?\s-p)(?P<val>\S+)"),
    re.compile(r"(?P<pre>://[^/\s:@]+:)(?P<val>[^@\s/]+)(?=@)"),
]


_ADDRESS_WORD = re.compile(r"[(<\"']*[\w.+-]+@[\w-]+(?:\.[\w-]+)+[)>\"',;:.]*")


def scrub(text: str) -> str:
    """Drops keys, tokens and pasted passwords before text is stored here, shown on a card or sent to the relevance
    check. Known key shapes and the values of secret-looking options go, and so does any other long word that mixes
    capitals, small letters and digits. Paths, sites, URLs and commands stay as they are: a card must say which site
    goes live and which file is deleted (Oct 8 QA round 3, M3: `--site sugared-heron-2025` read `--site
    [removed]`)."""
    out = _SECRET_SHAPES.sub("[removed]", text or "")
    for pat in _SECRET_VALUES:
        out = pat.sub(lambda m: m["pre"] + ("[removed]" if m["val"] != "[removed]" else m["val"]), out)

    def keep(m):
        w = m.group(0)
        if "[removed]" in w or "/" in w or re.match(r"(https?://|www\.)", w, re.I) or \
                re.match(r"[\w-]+(\.[\w-]+)*\.[a-z]{2,}(?:[/:?#]|$)", w, re.I) or _ADDRESS_WORD.fullmatch(w):
            return w   # an email address is who a message goes to, not a secret: a send's card must show it
        letters = re.sub(r"^[\W_]+|[\W_]+$", "", w)
        mixed = all(re.search(p, letters) for p in (r"[a-z]", r"[A-Z]", r"\d"))
        return "[removed]" if mixed else w
    return _SECRETISH.sub(keep, out)


def open_pass(kind: str, text: str, bot_id: str | None = None, session_id: str | None = None,
              task_id: str | None = None, ttl: float | None = None, expires: float | None = None) -> int:
    now = time.time()
    ttl = ttl or (TASK_PASS_SEC if task_id else MESSAGE_PASS_SEC)
    text = scrub(text)
    with db() as c:
        cur = c.execute("INSERT INTO passes (kind, bot_id, session_id, task_id, text, created, expires) "
                        "VALUES (?,?,?,?,?,?,?)", (kind, bot_id, session_id, task_id, text[:2000], now, expires or now + ttl))
        return cur.lastrowid


def live_pass(session_id: str | None = None, task_id: str | None = None) -> sqlite3.Row | None:
    now = time.time()
    with db() as c:
        if task_id:
            r = c.execute("SELECT * FROM passes WHERE task_id=? AND expires>? ORDER BY id DESC LIMIT 1",
                          (task_id, now)).fetchone()
            if r:
                return r
        if session_id:
            return c.execute("SELECT * FROM passes WHERE session_id=? AND expires>? ORDER BY id DESC LIMIT 1",
                             (session_id, now)).fetchone()
    return None


def inherit(task_id: str, parent: sqlite3.Row) -> None:
    """Work handed off during Colin's request carries that request along, for two hand-offs at most.
    A task made straight from his request gets a day; a task one of those tasks makes keeps its
    parent's deadline; past that, nothing carries over and the bot asks."""
    if parent["kind"] in ("message", "button", "approve"):
        open_pass("task", parent["text"], task_id=task_id, ttl=TASK_PASS_SEC)
    elif parent["kind"] == "task":
        open_pass("task2", parent["text"], task_id=task_id, expires=parent["expires"])


def cached_relevance(pass_id: int, site: str) -> bool | None:
    with db() as c:
        r = c.execute("SELECT ok FROM relevance WHERE pass_id=? AND site=?", (pass_id, site)).fetchone()
    return None if r is None else bool(r["ok"])


def remember_relevance(pass_id: int, site: str, ok: bool) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO relevance (pass_id, site, ok) VALUES (?,?,?)", (pass_id, site, int(ok)))


# ── Modes: Colin's standing choice per bot and site ─────────────────────────────────────────

def stored_mode(bot_id: str, site: str) -> str | None:
    """The mode Colin chose for this bot on this site (or a sibling host), or None if he never chose."""
    with db() as c:
        r = c.execute("SELECT mode FROM modes WHERE bot_id=? AND site=?", (bot_id, site)).fetchone()
        if not r:
            r = next((x for x in c.execute("SELECT site, mode FROM modes WHERE bot_id=? ORDER BY updated DESC",
                                           (bot_id,)) if base(x["site"]) == base(site)), None)
    return r["mode"] if r else None


def mode_for(bot_id: str, site: str) -> str:
    """Colin's choice, or the default: sensitive sites ask first, everything else is When I Ask."""
    return stored_mode(bot_id, site) or ("ask" if is_sensitive(site) else "when_asked")


def set_mode(bot_id: str, site: str, mode: str) -> None:
    if mode not in MODES:
        raise ValueError("bad mode")
    with db() as c:
        c.execute("INSERT OR REPLACE INTO modes (bot_id, site, mode, updated) VALUES (?,?,?,?)",
                  (bot_id, site_of(site), mode, time.time()))


# ── Requests: the cards in the app ──────────────────────────────────────────────────────────

def allowed_once(bot_id: str, site: str, session_id: str | None, task_id: str | None) -> bool:
    """Allow Once covers only the conversation or task it was asked from, for one sitting, on that
    site's company domain."""
    if not session_id and not task_id:
        return False
    with db() as c:
        rows = c.execute("SELECT site FROM requests WHERE bot_id=? AND status='allowed' AND decided>? AND COALESCE(kind,'signin') IN ('signin','navigate') "
                         "AND ((session_id IS NOT NULL AND session_id=?) OR (task_id IS NOT NULL AND task_id=?)) "
                         "AND COALESCE(test,0)=?",
                         (bot_id, time.time() - ONCE_SEC, session_id or "", task_id or "", _test())).fetchall()
    return any(base(r["site"]) == base(site) for r in rows)


def denied_recently(bot_id: str, site: str) -> bool:
    """Not Now holds for half an hour, and for a day when the card came from scheduled work, so a job
    that runs every few hours does not ask again each time."""
    now = time.time()
    with db() as c:
        rows = c.execute("SELECT site, why, decided FROM requests WHERE bot_id=? AND status='denied' AND decided>? "
                         "AND COALESCE(kind,'signin') IN ('signin','navigate') AND COALESCE(test,0)=?",
                         (bot_id, now - SCHEDULED_DENY_SEC, _test())).fetchall()
    return any(base(r["site"]) == base(site) and (r["why"] == "scheduled" or now - r["decided"] < ONCE_SEC)
               for r in rows)


def request(bot_id: str, site: str, reason: str, session_id: str | None, task_id: str | None,
            why: str = "", kind: str = "signin") -> int:
    """One open card per bot and site; a repeat ask updates the reason instead of stacking cards. `why` is
    mismatch (the site does not look needed for what Colin asked), ask (Colin set the site to ask every
    time), sensitive (a money, health, government or account site, which asks by default), scheduled (work
    Colin did not start), or background (no request from him to go on). `kind` is signin or navigate."""
    with db() as c:
        r = c.execute("SELECT id FROM requests WHERE bot_id=? AND site=? AND status='pending' AND COALESCE(kind,'signin') IN ('signin','navigate') "
                      "AND COALESCE(test,0)=?", (bot_id, site, _test())).fetchone()
        if r:
            c.execute("UPDATE requests SET reason=?, session_id=?, task_id=?, why=?, kind=? WHERE id=?",
                      (reason[:300], session_id, task_id, why, kind, r["id"]))
            return r["id"]
        cur = c.execute("INSERT INTO requests (bot_id, site, reason, session_id, task_id, created, why, kind, test) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (bot_id, site, reason[:300], session_id, task_id, time.time(), why, kind, _test()))
        return cur.lastrowid


def mark_signed_in(bot_id: str, site: str) -> None:
    """Colin signed this bot in by hand; its browser now holds the session, so the site is gated like a
    saved login."""
    with db() as c:
        c.execute("INSERT OR REPLACE INTO signed_in (bot_id, site, at) VALUES (?,?,?)", (bot_id, base(site), time.time()))


def signed_in_sites(bot_id: str) -> set[str]:
    with db() as c:
        return {r["site"] for r in c.execute("SELECT site FROM signed_in WHERE bot_id=?", (bot_id,))}


def close_message_passes(session_id: str) -> None:
    """Colin's request covers the turn it started and the board tasks made during it, not later turns the
    hub starts in the same conversation (board updates, notes, call wrap-ups)."""
    with db() as c:
        c.execute("UPDATE passes SET expires=? WHERE session_id=? AND kind='message' AND expires>?",
                  (time.time(), session_id, time.time()))


def expire_old(now: float | None = None) -> int:
    """Cards nobody answered go away: after a few hours for a chat, a day for scheduled work, board tasks and actions
    a Zip prepared for one of Zero's questions (Oct 8 QA: yesterday's cards sat in Needs You and resumed old
    conversations)."""
    now = now or time.time()
    with db() as c:
        cur = c.execute(
            "UPDATE requests SET status='expired', decided=? WHERE status='pending' AND ("
            "(created < ? AND (task_id IS NOT NULL OR why IN ('scheduled','prepared') OR routine IS NOT NULL)) OR "
            "(created < ? AND task_id IS NULL AND COALESCE(why,'') NOT IN ('scheduled','prepared') AND routine IS NULL))",
            (now, now - REQUEST_TTL_LONG_SEC, now - REQUEST_TTL_SEC))
        return cur.rowcount


def reopen_pass(session_id: str | None) -> int | None:
    """Colin's latest request in this conversation counts again for a follow-up turn the hub starts on its
    behalf (the bot picking up after he allowed a sign-in, saved a login or said not now), until the 45
    minutes from his message run out. Returns the pass id, or None when there is nothing to reopen. Board
    updates and other hub turns never call this, so they still run without his words."""
    if not session_id:
        return None
    now = time.time()
    with db() as c:
        r = c.execute("SELECT id, created FROM passes WHERE session_id=? AND kind='message' ORDER BY id DESC LIMIT 1",
                      (session_id,)).fetchone()
        if not r or r["created"] + MESSAGE_PASS_SEC <= now:
            return None
        c.execute("UPDATE passes SET expires=? WHERE id=?", (r["created"] + MESSAGE_PASS_SEC, r["id"]))
        return r["id"]


# A card a Zip prepared before Zero asks Colin about it (why='prepared', proactivity phase 2) is not a card in Work: it
# stays out of sight and is shown, with its exact recipient and text, under Zero's question. Tapping that option is
# Allow Once on it. Nothing else lists it, pushes it or counts it as waiting on Colin.
PREPARED = "prepared"
# Colin's words when he taps a send on one of Zero's questions start with this (the hub's notices._send_say). Such a
# send, or one whose text the hub drafted for a question (proactive_draft), never goes out under a standing rule: its
# text came from, or was shaped by, someone else's email, so it always shows the exact-text card (QA round 10, M2).
PROACTIVE_SEND_OPENING = "Please send this email"


def _draft_key(body: str) -> str:
    import hashlib
    return "proactive-draft:" + hashlib.sha256(" ".join(str(body or "").split()).lower().encode()).hexdigest()[:32]


def remember_proactive_draft(body: str) -> None:
    remember_judgment(_draft_key(body), "draft")


def proactive_draft(body: str) -> bool:
    return bool(body) and cached_judgment(_draft_key(body), max_age=30 * 86400) is not None


def pending() -> list[dict]:
    expire_old()
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM requests WHERE status='pending' AND COALESCE(test,0)=0 "
                                           "AND COALESCE(why,'') != 'prepared' ORDER BY id")]


def get_request(req_id: int) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
    return dict(r) if r else None


def decide(req_id: int, allow: bool, always: bool = False) -> dict | None:
    """Colin's answer on a card. Always Allow on a sign-in card sets the site to Anytime for that bot; on an
    action card it writes a rule for the card's kind and scope (one recipient, one routine), and only when the
    card offered one."""
    expire_old()
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=? AND status='pending'", (req_id,)).fetchone()
        if not r:
            return None
        c.execute("UPDATE requests SET status=?, decided=? WHERE id=?",
                  ("allowed" if allow else "denied", time.time(), req_id))
    kind = r["kind"] or "signin"
    if allow and always and not r["test"]:   # a test card never makes a standing setting or rule
        if kind in ("signin", "navigate"):
            set_mode(r["bot_id"], r["site"], "anytime")
        elif r["scope"] and kind in RULE_KINDS:
            set_rule(r["bot_id"], kind, r["scope"], r["site"], source=f"card {req_id}")
    return dict(r)


def open_chat_cards(bot_id: str, session_id: str | None, exclude: int | None = None) -> list[dict]:
    """This Zip's action cards still waiting in this chat (not sign-ins, not board work), oldest first."""
    if not session_id:
        return []
    expire_old()
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM requests WHERE bot_id=? AND session_id=? AND status='pending' AND task_id IS NULL "
            "AND COALESCE(kind,'signin') NOT IN ('signin','navigate') AND COALESCE(test,0)=? AND id != ? ORDER BY id",
            (bot_id, session_id, _test(), exclude or 0))]


def withdraw(bot_id: str, req_id: int) -> dict | None:
    """The Zip closes one of its own action cards that the person's later words made wrong (live convo QA 2: "No, I
    meant the other Jordan" left the card for the first Jordan open and approvable, and Inbox asked Sam to decline
    it). Only a card still waiting, in a chat, from that Zip. Closing a card never does anything."""
    expire_old()
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=? AND bot_id=? AND status='pending' AND task_id IS NULL "
                      "AND COALESCE(kind,'signin') NOT IN ('signin','navigate')", (req_id, bot_id)).fetchone()
        if not r:
            return None
        c.execute("UPDATE requests SET status='withdrawn', decided=? WHERE id=?", (time.time(), req_id))
    return dict(r)


def allow_now(bot_id: str, site: str, reason: str, task_id: str | None = None) -> None:
    """A button press that is itself the go-ahead (Save Login on a sign-in card)."""
    with db() as c:
        c.execute("INSERT INTO requests (bot_id, site, reason, task_id, status, created, decided) "
                  "VALUES (?,?,?,?, 'allowed', ?, ?)", (bot_id, site_of(site), reason[:300], task_id,
                                                         time.time(), time.time()))


# ── Action cards: send, publish, delete and the rest (gate.py, tiers.py) ───────────────────────────────

RULE_KINDS = {"signin", "send", "write"}     # what Always Allow can cover; never pay, secrets, deletes or accounts
ACTION_ONCE_SEC = 30 * 60                  # Allow Once on an action card covers the same call for half an hour
# Allow Once on a card from scheduled work waits for the job's next run, which may be days later (Fable's plan:
# "if Colin answers after the task finished, the next run uses the answer"). A week covers weekly jobs.
ROUTINE_ONCE_SEC = 8 * 86400
SEND_TEXT_MAX = 12000   # how much of a message a send's card shows; email_tool.BODY_MAX keeps sends within it


def action_request(bot_id: str, kind: str, site: str, summary: str, target: str, inputs: str, fingerprint: str,
                   session_id: str | None, task_id: str | None, why: str = "", scope: str = "",
                   excerpt: str = "", details: str = "", routine: dict | None = None,
                   likeness: dict | None = None, ask_rule: int | None = None) -> int:
    """One open card per bot and exact call. `why`: asked (Colin asked for it), scheduled, background,
    content (it may have come from something the Zip read), uncovered (a scheduled change the routine
    does not spell out) or prepared (made ahead for one of Zero's questions; see PREPARED). `routine`
    ({"id", "name"}) marks a card from scheduled work, and `likeness` says what
    its Allow Once covers in the job's next run (see alike). `ask_rule` is the Ask First rule that paused it, if
    any. A terminal action has no site: the card list used to report a site called "publish" (Oct 8 QA round 3,
    L6)."""
    # A send's card shows the whole message (the email tool refuses longer ones); other cards keep the old limit.
    inputs, excerpt, details = scrub(inputs)[:SEND_TEXT_MAX if kind == "send" else 4000], scrub(excerpt)[:1200], \
        scrub(details)[:4000]
    rid, rname = ((routine or {}).get("id") or None), ((routine or {}).get("name") or None)
    like = json.dumps(likeness, sort_keys=True)[:6000] if likeness else None
    with db() as c:
        r = c.execute("SELECT id FROM requests WHERE bot_id=? AND fingerprint=? AND status='pending' AND COALESCE(test,0)=?",
                      (bot_id, fingerprint, _test())).fetchone()
        if r:
            c.execute("UPDATE requests SET reason=?, session_id=?, task_id=?, why=?, excerpt=?, routine=?, "
                      "routine_name=?, likeness=?, scope=?, ask_rule=? WHERE id=?",
                      (summary[:300], session_id, task_id, why, excerpt, rid, rname, like,
                       scope[:200] if kind in RULE_KINDS else "", ask_rule, r["id"]))
            return r["id"]
        cur = c.execute(
            "INSERT INTO requests (bot_id, site, reason, session_id, task_id, created, why, kind, target, inputs, "
            "fingerprint, scope, excerpt, details, routine, routine_name, likeness, test, ask_rule) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bot_id, site or "", summary[:300], session_id, task_id, time.time(), why, kind,
             target[:300], inputs, fingerprint, scope[:200] if kind in RULE_KINDS else "", excerpt, details,
             rid, rname, like, _test(), ask_rule))
        return cur.lastrowid


CLOSE_TEXT = 0.85   # how alike a job's next send must be to the one Colin allowed (difflib ratio over the words)


def alike(allowed: dict | None, now: dict | None) -> bool:
    """Does the action a job's next run is about to take match the one Colin allowed once on its card? A change
    (write) matches on its kind and where it happens, since the next run words its request afresh (Oct 8 QA round
    2, M4). Anything else must be the same action: the same recipients and nearly the same text for a send, the
    same command in the same folder, the same site and fields for a click (round 3, H1: one Allow Once let the
    next run email anyone on gmail.com, or deploy any site)."""
    if not allowed or not now or allowed.get("kind") != now.get("kind"):
        return False
    if allowed.get("place", "") != now.get("place", ""):
        return False
    if now.get("kind") == "write":
        return True
    if sorted(allowed.get("targets") or []) != sorted(now.get("targets") or []):
        return False
    a, b = str(allowed.get("text") or ""), str(now.get("text") or "")
    if not a or not b:
        return False
    if allowed.get("close") and now.get("close"):
        import difflib
        words = lambda t: re.findall(r"[\w@+-]+(?:\.[\w@+-]+)*", t.lower())   # noqa: E731
        return difflib.SequenceMatcher(None, words(a), words(b)).ratio() >= CLOSE_TEXT
    return a == b


def use_approval(bot_id: str, fingerprint: str, routine: str | None = None, kind: str = "",
                 likeness: dict | None = None) -> dict | None:
    """Allow Once for this exact call: consumed by the first run, so a second send needs a second yes. During
    scheduled work, Allow Once on a card from the same job also covers the same action in a later run (see alike):
    any change of the same kind in the same place, but only the same send, deploy or delete."""
    now = time.time()
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE bot_id=? AND fingerprint=? AND status='allowed' AND used IS NULL "
                      "AND decided>? AND COALESCE(test,0)=? ORDER BY id DESC LIMIT 1",
                      (bot_id, fingerprint, now - ACTION_ONCE_SEC, _test())).fetchone()
        if not r and routine:
            for row in c.execute("SELECT * FROM requests WHERE bot_id=? AND routine=? AND kind=? AND "
                                 "status='allowed' AND used IS NULL AND decided>? AND COALESCE(test,0)=? ORDER BY id DESC",
                                 (bot_id, routine, kind, now - ROUTINE_ONCE_SEC, _test())).fetchall():
                try:
                    allowed = json.loads(row["likeness"] or "null")
                except ValueError:
                    allowed = None
                if alike(allowed, likeness):
                    r = row
                    break
        if not r:
            return None
        c.execute("UPDATE requests SET used=? WHERE id=?", (now, r["id"]))
    return dict(r)


def use_review(pass_id: int, what: str) -> bool:
    """Approve on a review covers each thing it names once (Oct 8 QA round 2, M3). True the first time."""
    key = f"review:{pass_id}:{what.strip().lower()}"
    with db() as c:
        if c.execute("SELECT 1 FROM judgments WHERE key=?", (key,)).fetchone():
            return False
        c.execute("INSERT INTO judgments (key, value, at) VALUES (?,?,?)", (key, "used", time.time()))
    return True


def action_denied(bot_id: str, fingerprint: str) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM requests WHERE bot_id=? AND fingerprint=? AND status='denied' AND decided>? "
                         "AND COALESCE(test,0)=?", (bot_id, fingerprint, time.time() - ONCE_SEC, _test())).fetchone() \
            is not None


def set_rule(bot_id: str, kind: str, scope: str, site: str | None = None, source: str = "") -> int:
    if kind not in RULE_KINDS or not scope.strip():
        raise ValueError("no rule for this kind")
    scope = scope.strip().lower()
    with db() as c:
        r = c.execute("SELECT id FROM rules WHERE bot_id=? AND kind=? AND scope=?", (bot_id, kind, scope)).fetchone()
        if r:
            return r["id"]
        return c.execute("INSERT INTO rules (bot_id, kind, scope, site, created, source) VALUES (?,?,?,?,?,?)",
                         (bot_id, kind, scope, site, time.time(), source)).lastrowid


def rule_for(bot_id: str, kind: str, scope: str) -> dict | None:
    """The Allow rule covering this kind and scope. Ask First rules are stored as "ask:<what>", so they never
    match here."""
    if not scope or kind not in RULE_KINDS:
        return None
    with db() as c:
        r = c.execute("SELECT * FROM rules WHERE bot_id=? AND kind=? AND scope=?",
                      (bot_id, kind, scope.strip().lower())).fetchone()
    return dict(r) if r else None


def touch_rule(rule_id: int) -> None:
    with db() as c:
        c.execute("UPDATE rules SET last_used=?, uses=uses+1 WHERE id=?", (time.time(), rule_id))


def rules(bot_id: str | None = None) -> list[dict]:
    with db() as c:
        q = "SELECT * FROM rules" + (" WHERE bot_id=?" if bot_id else "") + " ORDER BY created DESC"
        return [dict(r) for r in c.execute(q, (bot_id,) if bot_id else ())]


def get_rule(rule_id: int) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
    return dict(r) if r else None


def remove_rule(rule_id: int) -> bool:
    with db() as c:
        return c.execute("DELETE FROM rules WHERE id=?", (rule_id,)).rowcount > 0


# ── Custom Rules: Ask First (Grok Bot 0.68.1) ───────────────────────────────────────────────────────────
#
# Colin can make a Zip ask before something it would otherwise just do, or do under an Allow rule. An Ask First
# rule only ever adds a card: it never lets anything through, a never stays never, and Allow Once on its card still
# covers that one call. Rules match what tiers.classify already says about an action (its kind, its site and
# targets, and whether it is a git push), so what a rule covers is predictable. They live in the same table as the
# Allow rules, with the kind "ask:<what>" so no Allow rule lookup can ever read one as a yes. bot_id "*" is every Zip.

ASK_RULE_PREFIX = "ask:"
EVERY_ZIP = "*"
ANYWHERE = "*"
# What an Ask First rule can cover: (the phrase for anywhere, the phrase for one place). "change" is the kind the
# tiers call write (done, then mentioned), "push" any git push, "send" any message (which already asks unless an
# Allow rule covers it).
ASK_RULE_WHATS = {
    "push": ("pushing code to any repo", "pushing code to the branch {where}"),
    "change": ("changing anything", "changing anything in {where}"),
    "send": ("sending any message or email", "sending anything in {where}"),
}
# Named places, by the hosts the classifier reports for them (the connectors' sites and the browser's page host).
ASK_RULE_PLACES = {
    "google-docs": ("Google Docs and Drive", ("docs.google.com", "drive.google.com", "sheets.google.com",
                                              "slides.google.com")),
    "gmail": ("Gmail", ("gmail.com", "mail.google.com")),
    "calendar": ("Google Calendar", ("calendar.google.com",)),
    "trello": ("Trello", ("trello.com",)),
    "github": ("GitHub", ("github.com",)),
    "notion": ("Notion", ("notion.so", "notion.com")),
    "slack": ("Slack", ("slack.com",)),
    "netlify": ("Netlify", ("netlify.com", "netlify.app")),
}
_BRANCH = re.compile(r"^[\w][\w./-]{0,99}$")


def is_ask_rule(rule: dict) -> bool:
    return str(rule.get("kind") or "").startswith(ASK_RULE_PREFIX)


def ask_rule_where(what: str, where: str) -> str:
    """The place an Ask First rule names, cleaned up, or ValueError. Anywhere is "*"; otherwise a named place (see
    ASK_RULE_PLACES), a site, an email address or "@domain" for a send, or a branch name for a push."""
    if what not in ASK_RULE_WHATS:
        raise ValueError("Pick what the Zip should ask about.")
    w = " ".join(str(where or "").split()).lower()
    if not w or w in (ANYWHERE, "anywhere", "any"):
        return ANYWHERE
    if what == "push":
        w = w.removeprefix("refs/heads/")
        if _BRANCH.match(w) and ".." not in w:
            return w
        raise ValueError("Type the name of a branch, like main or staging.")
    if w in ASK_RULE_PLACES:
        return w
    if what == "send" and re.fullmatch(r"@?[\w.+-]*@?[\w-]+(\.[\w-]+)+", w) and "@" in w:
        return w
    host = site_of(w)
    if host and "." in host and re.fullmatch(r"[a-z0-9.-]+", host):
        return host
    raise ValueError("Type a website, like docs.google.com" + (", or an email address." if what == "send" else "."))


def ask_rule_phrase(what: str, where: str) -> str:
    """"pushing code to any repo", "changing anything in Google Docs and Drive", "sending to andrea@x.com"."""
    anywhere, somewhere = ASK_RULE_WHATS.get(what, ("doing this", "doing this in {where}"))
    if not where or where == ANYWHERE:
        return anywhere
    if what == "send" and "@" in where:
        return f"sending anything to anyone at {where[1:]}" if where.startswith("@") else f"sending anything to {where}"
    return somewhere.format(where=ASK_RULE_PLACES.get(where, (where,))[0])


def set_ask_rule(bot_id: str, what: str, where: str = ANYWHERE, source: str = "") -> int:
    """Add an Ask First rule (the same rule twice is one rule). `source` defaults to the person's first name."""
    source = source or home.first_name(REAL_HOME)
    where = ask_rule_where(what, where)
    if not str(bot_id or "").strip():
        raise ValueError("Pick a Zip.")
    kind = ASK_RULE_PREFIX + what
    with db() as c:
        r = c.execute("SELECT id FROM rules WHERE bot_id=? AND kind=? AND scope=?", (bot_id, kind, where)).fetchone()
        if r:
            return r["id"]
        return c.execute("INSERT INTO rules (bot_id, kind, scope, site, created, source) VALUES (?,?,?,?,?,?)",
                         (bot_id, kind, where, None, time.time(), source)).lastrowid


def update_rule(rule_id: int, bot_id: str | None = None, what: str | None = None, where: str | None = None) -> dict:
    """Edit Rule. Any rule can move to another Zip. An Ask First rule can change what it covers and where, since it
    only ever adds a card. An Allow rule keeps its kind and its one recipient or job: it already covers the least a
    rule can, so there is nothing narrower to change it to, and anything else would widen it. LookupError when the
    rule is gone, ValueError for a change that is not allowed. If the edited rule now matches another rule, the two
    become one."""
    r = get_rule(rule_id)
    if r is None:
        raise LookupError("That rule is already gone.")
    bot_id = str(bot_id or r["bot_id"]).strip()
    if is_ask_rule(r):
        what = what or r["kind"][len(ASK_RULE_PREFIX):]
        kind, scope = ASK_RULE_PREFIX + what, ask_rule_where(what, r["scope"] if where is None else where)
    else:
        if bot_id == EVERY_ZIP:
            raise ValueError("An Allow rule covers one Zip. Make a rule for each Zip instead.")
        if what not in (None, "", r["kind"]):
            raise ValueError("An Allow rule cannot change what it covers. Remove it and allow the new action on its card.")
        if where not in (None, "") and where.strip().lower() != r["scope"]:
            raise ValueError("This rule already covers only one person or one job, so it cannot be narrowed further.")
        kind, scope = r["kind"], r["scope"]
    with db() as c:
        same = c.execute("SELECT * FROM rules WHERE bot_id=? AND kind=? AND scope=? AND id!=?",
                         (bot_id, kind, scope, rule_id)).fetchone()
        if same:
            c.execute("UPDATE rules SET uses=uses+?, last_used=MAX(COALESCE(last_used,0), COALESCE(?,0)) WHERE id=?",
                      (r["uses"] or 0, r["last_used"], same["id"]))
            c.execute("DELETE FROM rules WHERE id=?", (rule_id,))
            rule_id = same["id"]
        else:
            c.execute("UPDATE rules SET bot_id=?, kind=?, scope=? WHERE id=?", (bot_id, kind, scope, rule_id))
    return get_rule(rule_id)


def _place_fits(where: str, site: str, targets: list[str]) -> bool:
    if where == ANYWHERE:
        return True
    host = site_of(site or "")
    if where in ASK_RULE_PLACES:
        return any(host == h or host.endswith("." + h) for h in ASK_RULE_PLACES[where][1])
    if "@" in where:
        return any(t == where or (where.startswith("@") and t.endswith(where)) for t in targets)
    if "." in where and (host == where or host.endswith("." + where)):
        return True
    return where in targets


def ask_rule_for(bot_id: str, kind: str, site: str = "", targets: list[str] | None = None,
                 push: bool = False) -> dict | None:
    """The first Ask First rule for this Zip (or every Zip) that covers an action of this kind, at this site or
    aimed at these targets, or None. `push` says the action is a git push."""
    whats = ({"change"} if kind == "write" else set()) | ({"send"} if kind == "send" else set()) | \
        ({"push"} if push else set())
    if not whats:
        return None
    targets = [t.strip().lower() for t in (targets or []) if t and t.strip()]
    kinds = [ASK_RULE_PREFIX + w for w in sorted(whats)]
    with db() as c:
        rows = c.execute(f"SELECT * FROM rules WHERE bot_id IN (?, ?) AND kind IN ({','.join('?' * len(kinds))}) "
                         "ORDER BY id", (bot_id, EVERY_ZIP, *kinds)).fetchall()
    return next((dict(r) for r in rows if _place_fits(r["scope"], site, targets)), None)


def expired_since(since: float) -> list[dict]:
    """Cards that ran out of time without an answer after `since` (the hub tells the Zip's chat). Test cards and
    prepared cards are left out."""
    expire_old()
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM requests WHERE status='expired' AND decided>? AND COALESCE(test,0)=0 "
            "AND COALESCE(why,'') != 'prepared' ORDER BY decided, id", (since,))]


def log_action(bot_id: str, kind: str, site: str, summary: str, how: str) -> int:
    """A "tell" action (done, then mentioned), or an approved one, for the app's activity and the bot's chat."""
    with db() as c:
        return c.execute("INSERT INTO actions (bot_id, kind, site, summary, how, at, test) VALUES (?,?,?,?,?,?,?)",
                         (bot_id, kind, site or "", scrub(summary)[:300], how, time.time(), _test())).lastrowid


def actions_after(after_id: int) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM actions WHERE id>? AND COALESCE(test,0)=0 ORDER BY id",
                                           (after_id,))]


def recent_actions(limit: int = 50) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM actions WHERE COALESCE(test,0)=0 ORDER BY id DESC LIMIT ?",
                                           (limit,))]


CONTENT_SEC = 30 * 60   # what the Zip read counts as a possible source of instructions for half an hour


def remember_content(key: str, source: str, text: str, site: str = "") -> None:
    """The latest thing the Zip read. What it read before, from another source or site, is kept one step back
    (earlier_content): an email read before opening the page with the Send button."""
    excerpt = scrub(text or "")[:2000]
    if not key or not excerpt.strip():
        return
    with db() as c:
        old = c.execute("SELECT * FROM content WHERE key=?", (key,)).fetchone()
        if old and (old["source"] != source or base(old["site"] or "") != base(site or "")):
            c.execute("INSERT OR REPLACE INTO content (key, source, excerpt, at, site) VALUES (?,?,?,?,?)",
                      (key + "|earlier", old["source"], old["excerpt"], old["at"], old["site"] or ""))
        c.execute("INSERT OR REPLACE INTO content (key, source, excerpt, at, site) VALUES (?,?,?,?,?)",
                  (key, source, excerpt, time.time(), site or ""))


def recent_content(key: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM content WHERE key=? AND at>?", (key, time.time() - CONTENT_SEC)).fetchone()
    return dict(r) if r else None


def earlier_content(key: str) -> dict | None:
    """What the Zip read before its latest page, from another site or source, within the same half hour."""
    return recent_content(key + "|earlier") if key else None


def cached_judgment(key: str, max_age: float = 7 * 86400) -> str | None:
    with db() as c:
        r = c.execute("SELECT value FROM judgments WHERE key=? AND at>?", (key, time.time() - max_age)).fetchone()
    return r["value"] if r else None


def remember_judgment(key: str, value: str) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO judgments (key, value, at) VALUES (?,?,?)", (key, value, time.time()))


def _page(c: sqlite3.Connection, key: str) -> tuple[str, dict]:
    r = c.execute("SELECT site, refs FROM page_refs WHERE key=?", (key,)).fetchone()
    if not r:
        return "", {}
    try:
        data = json.loads(r["refs"])
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if "refs" not in data:   # the older layout: labels only
        data = {"refs": data}
    return r["site"] or "", data


def remember_refs(key: str, site: str, refs: dict, fields: dict | None = None, cookie: bool = False,
                  facts: dict | None = None) -> None:
    """The page the Zip last looked at: its elements' labels (so a click is judged by what it clicks), the values
    in its text fields, and what else the gate reads from it (gate._page_facts: the buttons inside a cookie or
    consent notice, the page's headings, whether it is an invitation). What the Zip typed on this site is kept,
    since a snapshot can show a field differently from what was typed."""
    with db() as c:
        old_site, old = _page(c, key)
        typed = old.get("typed", {}) if old_site == site else {}
        data = {"refs": refs, "fields": fields or {}, "typed": typed, "cookie": bool(cookie), **(facts or {})}
        c.execute("INSERT OR REPLACE INTO page_refs (key, site, refs, at) VALUES (?,?,?,?)",
                  (key, site, json.dumps(data)[:200000], time.time()))


def remember_typed(key: str, label: str, text: str) -> None:
    """What the Zip typed into a field on the current page (browser_type), for a send card's recipient and text."""
    if not label:
        return
    with db() as c:
        site, data = _page(c, key)
        data.setdefault("typed", {})[label] = scrub(text or "")[:4000]
        c.execute("INSERT OR REPLACE INTO page_refs (key, site, refs, at) VALUES (?,?,?,?)",
                  (key, site, json.dumps(data)[:200000], time.time()))


def page_info(key: str) -> dict:
    """{"site", "fields" (typed values over what the page showed), "cookie_refs", "cookie_labels", "headings",
    "dialogs", "invite"} for the Zip's current page."""
    with db() as c:
        site, data = _page(c, key)
    return {"site": site, "fields": {**(data.get("fields") or {}), **(data.get("typed") or {})},
            "cookie_refs": list(data.get("cookie_refs") or []), "cookie_labels": list(data.get("cookie_labels") or []),
            "headings": list(data.get("headings") or []), "dialogs": list(data.get("dialogs") or []),
            "invite": bool(data.get("invite"))}


def ref_label(key: str, ref: str) -> tuple[str, str]:
    """(label, site) for an element ref from the last snapshot, or ("", "") when unknown."""
    with db() as c:
        site, data = _page(c, key)
    return str((data.get("refs") or {}).get(ref.lstrip("@"), "")), site


# ── History ─────────────────────────────────────────────────────────────────────────────────

def log_signin(bot_id: str, site: str, reason: str, how: str) -> None:
    """One line per bot and site per sitting: a repeat within half an hour only refreshes the time (Oct 8
    QA: School's chat showed six "Signed in" lines in six minutes)."""
    now = time.time()
    with db() as c:
        for r in c.execute("SELECT id, site FROM signins WHERE bot_id=? AND at>? AND COALESCE(test,0)=? ORDER BY id DESC",
                           (bot_id, now - SIGNIN_REPEAT_SEC, _test())).fetchall():
            if base(r["site"]) == base(site):
                c.execute("UPDATE signins SET at=? WHERE id=?", (now, r["id"]))
                return
        c.execute("INSERT INTO signins (bot_id, site, reason, how, at, test) VALUES (?,?,?,?,?,?)",
                  (bot_id, site, reason[:300], how, now, _test()))


def signins_after(after_id: int) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM signins WHERE id>? AND COALESCE(test,0)=0 ORDER BY id",
                                           (after_id,))]


def recent_signins(limit: int = 50) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM signins WHERE COALESCE(test,0)=0 ORDER BY id DESC LIMIT ?",
                                           (limit,))]


def requests_after(after_id: int) -> list[dict]:
    expire_old()
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM requests WHERE id>? AND status='pending' AND COALESCE(test,0)=0 "
                                           "AND COALESCE(why,'') != 'prepared' ORDER BY id", (after_id,))]


# ── Test cards (test_mode; alien/bin/zip-test-cards) ───────────────────────────────────────────────────

def test_cards(status: str | None = "pending") -> list[dict]:
    """The cards raised in test mode, newest last. Only these can be answered with decide_test."""
    expire_old()
    with db() as c:
        q = "SELECT * FROM requests WHERE COALESCE(test,0)=1" + (" AND status=?" if status else "") + " ORDER BY id"
        return [dict(r) for r in c.execute(q, (status,) if status else ())]


def decide_test(req_id: int, allow: bool) -> dict | None:
    """Answer a test card as Colin would with Allow Once or Not Now. A real card is never touched."""
    with db() as c:
        r = c.execute("SELECT id FROM requests WHERE id=? AND COALESCE(test,0)=1", (req_id,)).fetchone()
    return decide(req_id, allow) if r else None


def clear_test_cards() -> int:
    """Close every open test card."""
    with db() as c:
        return c.execute("UPDATE requests SET status='expired', decided=? WHERE status='pending' AND COALESCE(test,0)=1",
                         (time.time(),)).rowcount


# ── Terminal commands the gate let through, for Hermes's own check (the hub's approvals.py) ───────────────

COMMAND_SEC = 15 * 60   # Hermes asks right after the gate lets a command through; older records are dropped


def _command_hash(command: str) -> str:
    return hashlib.sha256((command or "").strip().encode("utf-8", "replace")).hexdigest()


def remember_command(bot_id: str, forms: list[str], flagged: str | None, place: str | None = None) -> None:
    """The gate let a terminal command through. `flagged` is what Hermes's own check would still ask about that the
    gate did not judge (tiers.unjudged), or None when the gate judged every such part. `forms` are the command and
    the redacted copies Hermes shows in its prompt, and `place` the folder it runs in. Only hashes are kept, since a
    command can hold a secret."""
    now = time.time()
    with db() as c:
        c.execute("DELETE FROM commands WHERE at<?", (now - COMMAND_SEC,))
        for h in dict.fromkeys(_command_hash(f) for f in forms if f and f.strip()):
            c.execute("INSERT INTO commands (hash, bot_id, cleared, flagged, at, place) VALUES (?,?,?,?,?,?)",
                      (h, bot_id, 0 if flagged else 1, flagged or "", now, place_hash(place)))


def place_hash(place: str | None) -> str:
    """The folder a command runs in, as a short hash ("" when it is not known)."""
    return _command_hash(place)[:16] if place else ""


def gate_checked(bot_id: str, command: str) -> dict | None:
    """The gate's latest record of this exact command for this bot, or None when it has none."""
    with db() as c:
        r = c.execute("SELECT * FROM commands WHERE hash=? AND bot_id=? AND at>? ORDER BY at DESC LIMIT 1",
                      (_command_hash(command), bot_id, time.time() - COMMAND_SEC)).fetchone()
    return dict(r) if r else None


# The card's title, by Hermes's description: what the command can do, in plain words. Every description Hermes has
# today gets its own wording (Oct 8 QA round 5, M1: "Run a command that needs your okay" gave Colin nothing to judge).
# The order matters: the first match wins.
_PLAIN_COMMANDS = [
    (r"metadata", "Run a command that reads sign-in keys from the network"),
    (r"exfiltration|upload a secret|credential file into", "Run a command that uploads a key or password file"),
    (r"^SQL", "Run a command that deletes data from a database"),
    (r"fork bomb", "Run a command that could freeze the computer"),
    (r"chmod \+x followed by immediate execution", "Run a script it just made runnable"),
    (r"hermes update", "Run a command that updates the software your Zips run on"),
    (r"hermes config|hermes secrets", "Run a command that changes your Zips' settings or passwords"),
    (r"ssh keys", "Run a command that reads your sign-in keys"),
    (r"sensitive credential|ssh/shell-rc", "Run a command that changes sign-in keys or shell settings"),
    (r"project env/config|env/config|config file", "Run a command that changes a settings file"),
    (r"start gateway outside", "Run a command that starts a second copy of your Zips"),
    (r"hermes|gateway|self-termination", "Run a command that stops or restarts your Zips"),
    (r"(docker|podman).*(daemon|remote|context)", "Run a command that controls containers on another computer"),
    (r"docker|podman|container", "Run a command that stops or restarts containers"),
    (r"kill all processes", "Run a command that stops every running program"),
    (r"service|systemctl|launchd", "Run a command that stops or restarts a service"),
    (r"kill", "Run a command that stops running programs"),
    (r"git reset", "Run a command that throws away code changes that are not saved"),
    (r"force push", "Run a command that rewrites a project's shared history"),
    (r"branch force delete", "Run a command that deletes a code branch"),
    (r"uninstall", "Run a command that uninstalls software"),
    (r"registry", "Run a command that changes Windows settings"),
    (r"shadow copies|backups", "Run a command that deletes backups"),
    (r"system config|system file|block device|filesystem|format|partition|boot|disk|wipe",
     "Run a command that changes system settings or disks"),
    (r"delete|git clean|\brm\b", "Run a command that deletes files"),
    (r"writable|chmod|chown|icacls|acl|everyone", "Run a command that changes who can use files"),
    (r"sudo", "Run a command as the computer's administrator"),
    (r"remote content|remote script|invoke-expression|iwr", "Run a command that runs code from the web"),
    (r"decoded|transformed|obfuscation|encoded", "Run a command that runs disguised code"),
    (r"unicode", "Run a command with hidden characters that disguise what it does"),
    (r"shell command via|shell execution via", "Run several commands in one go"),
    (r"arbitrary program execution", "Run a command that starts another program from inside it"),
    (r"dynamic shell word", "Run a command whose options are only known when it runs"),
]
# Scripts are named by their language. A script Hermes cannot read as words is one it calls malformed.
_SCRIPT_DESC = re.compile(r"^(script execution via|command parser limit)", re.I)
_LANGUAGES = [(r"python[\d.]*|pypy[\d.]*", "Python"), (r"node|nodejs|deno|bun", "JavaScript"), (r"perl", "Perl"),
              (r"ruby", "Ruby"), (r"php", "PHP"), (r"pwsh|powershell", "PowerShell"), (r"osascript", "AppleScript"),
              (r"lua", "Lua")]


def plain_command(description: str, command: str = "") -> str:
    """A card's title for Hermes's description of a command (the hub's command cards, and the gate's cards for
    commands Hermes cannot see: inside code, and in a scheduled job's script)."""
    d = description or ""
    if _SCRIPT_DESC.match(d):
        for pat, language in _LANGUAGES:
            if re.search(rf"(?:^|[\s;&|(/])(?:{pat})(?=\s|$)", command or ""):
                return f"Run a short {language} script"
        return "Run a short script" if d.lower().startswith("script") else "Run a command too long or tangled to read"
    return next((say for pat, say in _PLAIN_COMMANDS if re.search(pat, d, re.I)), "Run a command that needs your okay")


def dump() -> str:
    """For debugging from a shell."""
    with db() as c:
        return json.dumps({t: [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 10")]
                           for t in ("passes", "modes", "requests", "signins", "rules", "actions")},
                      indent=1, default=str)
