""



















import contextlib
import hashlib
import ipaddress
import json
import os
import pwd
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse


def _home_settings():
    ""

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
EVERY_ZIP = "*"
TEST_OWNER = "test:"


GO_AHEAD_PASSES = ("message", "button", "approve", "task", "task2")
MESSAGE_PASS_SEC = 45 * 60
TASK_PASS_SEC = 24 * 3600
ONCE_SEC = 30 * 60





TEST_ENV = "TAFFY_TEST_CARDS"


def test_mode() -> bool:
    ""

    return os.environ.get(TEST_ENV, "").strip() == "1"


def _test() -> int:
    return 1 if test_mode() else 0






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


SENSITIVE_HOSTS = frozenset("""
accounts.google.com myaccount.google.com appleid.apple.com account.apple.com iforgot.apple.com
account.microsoft.com account.live.com login.live.com
""".split())


SIGN_IN_PROVIDER_HOSTS = frozenset("""
accounts.google.com login.microsoftonline.com login.live.com appleid.apple.com
""".split())
SIGN_IN_PROVIDER_SUFFIXES = (".okta.com", ".onelogin.com")

SENSITIVE_PATTERN = re.compile(
    r"(^[\w-]*(bank|banking|fcu|creditunion)\.(com|org|net)$|\.bank$|\.gov$|\.mil$|"
    r"^[\w-]*(health|hospital|medical|clinic)[\w-]*\.org$|(^|\.)mychart\.)", re.I)

PUBLIC_SITES = frozenset("""
weather.gov nps.gov recreation.gov noaa.gov usa.gov unclaimed.org missingmoney.com
""".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS passes (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, bot_id TEXT, session_id TEXT, task_id TEXT,
  text TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, relay_session_id TEXT);
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
CREATE TABLE IF NOT EXISTS request_mail (
  req INTEGER PRIMARY KEY, mail TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS made (
  id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, place TEXT NOT NULL, path TEXT NOT NULL, how TEXT NOT NULL,
  sources TEXT, at REAL NOT NULL, born REAL);
CREATE INDEX IF NOT EXISTS made_place ON made(place, at);
CREATE INDEX IF NOT EXISTS commands_hash ON commands(hash);
CREATE TABLE IF NOT EXISTS login_recovery (
  bot_id TEXT NOT NULL, key TEXT NOT NULL, site TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'observing',
  filled REAL NOT NULL DEFAULT 0, evidence TEXT NOT NULL DEFAULT '{}', at REAL NOT NULL, started REAL NOT NULL,
  PRIMARY KEY(bot_id, key, site));
CREATE TABLE IF NOT EXISTS paused_sites (
  bot_id TEXT NOT NULL, site TEXT NOT NULL, report_id INTEGER NOT NULL, at REAL NOT NULL,
  PRIMARY KEY (bot_id, site));
CREATE TABLE IF NOT EXISTS site_tries (
  bot_id TEXT NOT NULL, run TEXT NOT NULL, site TEXT NOT NULL, failed INTEGER NOT NULL DEFAULT 0,
  worked INTEGER NOT NULL DEFAULT 0, at REAL NOT NULL, PRIMARY KEY (bot_id, run, site));
CREATE TABLE IF NOT EXISTS site_opened (
  bot_id TEXT NOT NULL, site TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (bot_id, site));
"""
SCHEDULED_DENY_SEC = 24 * 3600
REQUEST_TTL_SEC = 6 * 3600
REQUEST_TTL_LONG_SEC = 24 * 3600
SIGNIN_REPEAT_SEC = 30 * 60


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
    _add_column(c, "passes", "relay_session_id TEXT")
    for col in ("own_words TEXT",
                "card_asked TEXT"):
        _add_column(c, "passes", col)
    for col in ("why TEXT",
                "kind TEXT",
                "target TEXT",
                "inputs TEXT",
                "fingerprint TEXT",
                "scope TEXT",
                "excerpt TEXT",
                "used REAL",
                "details TEXT",
                "routine TEXT",
                "routine_name TEXT",
                "likeness TEXT",
                "test INTEGER",
                "ask_rule INTEGER",


                "title TEXT", "affects TEXT", "safe TEXT", "button TEXT",
                "icons TEXT",
                "asked TEXT",
                "because TEXT",
                "reason_state TEXT",
                "raised REAL",
                "folder TEXT",


                "request TEXT"):
        _add_column(c, "requests", col)
    _add_column(c, "actions", "test INTEGER")
    _add_column(c, "signins", "test INTEGER")
    _add_column(c, "content", "site TEXT")
    _add_column(c, "commands", "place TEXT")
    _add_column(c, "paused_sites", "tried REAL")
    return c


def _add_column(c: sqlite3.Connection, table: str, col: str) -> None:
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e):
            raise


def site_of(url_or_host: str) -> str:
    ""
    s = (url_or_host or "").strip().lower()
    host = urlparse(s).hostname if "://" in s else s.split("/")[0].split(":")[0]
    return (host or "").removeprefix("www.")


_PSL_FILE = Path("/usr/share/publicsuffix/public_suffix_list.dat")
_psl: tuple[set, set, set] | None = None


def _suffix_rules() -> tuple[set, set, set]:
    ""

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

            exact |= {"sharepoint.com", "my.salesforce.com", "force.com", "my.site.com", "zendesk.com",
                      "atlassian.net", "slack.com", "notion.site", "wixsite.com", "squarespace.com", "wordpress.com",
                      "instructure.com", "moodlecloud.com", "okta.com", "teachable.com", "thinkific.com",
                      "bubbleapps.io", "softr.app", "glide.page", "webflow.io"}
        _psl = (exact, wild, exceptions)
    return _psl


def base(site: str) -> str:
    ""


    host = (site or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) < 2:
        return host
    if not _suffix_rules()[0]:
        return ".".join(parts[-2:])
    suffix_len = _suffix_len(parts)
    if suffix_len >= len(parts):
        return host
    return ".".join(parts[-(suffix_len + 1):])


def _suffix_len(parts: list[str]) -> int:
    ""
    exact, wild, exceptions = _suffix_rules()
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        if cand in exceptions:
            return len(parts) - i - 1
        if cand in exact or (i + 1 < len(parts) and ".".join(parts[i + 1:]) in wild):
            return len(parts) - i
    return 1


def is_public_suffix(host: str) -> bool:
    ""
    parts = (host or "").lower().strip(".").split(".")
    return bool(parts[0]) and (len(parts) == 1 or _suffix_len(parts) >= len(parts))


def login_site(host: str) -> str:
    ""



    host = (host or "").lower().strip(".")
    if not host or not _suffix_rules()[0] or is_public_suffix(host):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        return base(host)


def is_sensitive(site: str, kind: str = "signin") -> bool:
    ""

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


def account_security(site: str) -> bool:
    ""

    return site_of(site) in SENSITIVE_HOSTS


def sign_in_provider(url: str) -> bool:
    ""
    if not str(url or "").lower().startswith("https://"):
        return False
    host = site_of(url)
    return host in SIGN_IN_PROVIDER_HOSTS or host.endswith(SIGN_IN_PROVIDER_SUFFIXES)




_SECRETISH = re.compile(r"\S{16,}")


_SECRET_SHAPES = re.compile(
    r"\b(?:sk|pk|rk)[-_](?:live[-_]|test[-_]|proj[-_]|ant[-_]|or[-_])?[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|"
    r"\bgithub_pat_[A-Za-z0-9_]{20,}|\bglpat-[A-Za-z0-9_-]{16,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b|"
    r"\bAIza[0-9A-Za-z_-]{30,}|\bya29\.[0-9A-Za-z_-]{20,}|\bnpm_[A-Za-z0-9]{30,}|\bnfp_[A-Za-z0-9]{20,}|"
    r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


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


def scrub(text: str, found: list | None = None) -> str:
    ""




    def shape(m):
        if found is not None:
            found.append(("key", m.group(0)))
        return "[removed]"
    out = _SECRET_SHAPES.sub(shape, text or "")
    for pat in _SECRET_VALUES:
        def value(m):
            if m["val"] == "[removed]":
                return m["pre"] + m["val"]
            if found is not None:
                found.append(("value", m["val"]))
            return m["pre"] + "[removed]"
        out = pat.sub(value, out)

    def keep(m):
        w = m.group(0)


        core = re.sub(r"^[(<\"'\[{]+|[)>\"',;:.!?\]}]+$", "", w)
        if "[removed]" in w or "/" in w or re.match(r"(https?://|www\.)", core, re.I) or \
                re.match(r"[\w-]+(\.[\w-]+)*\.[a-z]{2,}(?:[/:?#]|$)", core, re.I) or _ADDRESS_WORD.fullmatch(w):
            return w
        letters = re.sub(r"^[\W_]+|[\W_]+$", "", w)



        mixed = any(all(re.search(p, part) for p in (r"[a-z]", r"[A-Z]", r"\d")) for part in re.split(r"[_-]+", letters))
        if mixed and found is not None:
            found.append(("word", letters))
        return "[removed]" if mixed else w
    return _SECRETISH.sub(keep, out)


def what_looks_secret(text: str) -> str:
    ""

    found: list = []
    scrub(text, found)
    if not found:
        return ""
    kind, word = found[0]
    masked = f"{word[:2]}...{word[-2:]}" if len(word) >= 8 else "..."
    at = (text or "").find(word)
    before = " ".join((text or "")[:at].split()[-3:]) if at > 0 else ""
    if before and scrub(before) != before:
        before = ""
    what = {"key": "something shaped like a key or token",
            "value": "a value right after a word like password, token or key",
            "word": "a long word that mixes capitals, small letters and digits, the way a key does"}[kind]
    return (f"It holds {what}: {masked} ({len(word)} characters)" + (f', after "{before}"' if before else "") + "."
            + (" If that is a file or folder name, write it as a path (~/folder/name) or put spaces in it."
               if kind == "word" else "") + " If it really is a password or key, leave it out.")





_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?"
                          r"(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.S)
_SECRET_FIELDS = [
    re.compile(r"""(?P<pre>\\*["'][\w.-]*(?i:secret|token|passw|api[_-]?key|private[_-]?key|credential|cookie|authorization)"""
               r"""[\w.-]*\\*["']\s*:\s*\\*["'])(?P<val>[^"'\\]+)"""),
    re.compile(r"(?P<pre>\b[A-Z0-9_]*(?:KEY|CREDENTIALS?|COOKIE)[A-Z0-9_]*\s*=\s*['\"]?)(?P<val>[^\s'\"]+)"),
    re.compile(r"(?P<pre>\bmachine\s+\S+.*?\bpassword\s+)(?P<val>\S+)"),
]
_ENCODED = re.compile(r"[A-Za-z0-9+_=-]{32,}|[A-Za-z0-9+/=]{64,}|\b[0-9a-fA-F]{64,}\b")


def mask_secrets(text: str) -> str:
    ""



    out = _PRIVATE_KEY.sub("[removed]", text or "")
    out = _SECRET_SHAPES.sub("[removed]", out)
    for pat in [*_SECRET_VALUES, *_SECRET_FIELDS]:
        out = pat.sub(lambda m: m["pre"] + "[removed]", out)

    def encoded(m):
        w = m.group(0)
        hex_run = len(w) >= 64 and not re.search(r"[^0-9a-fA-F]", w)
        return "[removed]" if hex_run or all(re.search(p, w) for p in (r"[a-z]", r"[A-Z]", r"\d")) else w
    return _ENCODED.sub(encoded, out)


def open_pass(kind: str, text: str, bot_id: str | None = None, session_id: str | None = None,
              task_id: str | None = None, ttl: float | None = None, expires: float | None = None) -> int:
    now = time.time()
    ttl = ttl or (TASK_PASS_SEC if task_id else MESSAGE_PASS_SEC)
    text = scrub(text)
    if kind not in ("task", "task2", "origin"):
        text = text[:2000]
    with db() as c:
        cur = c.execute("INSERT INTO passes (kind, bot_id, session_id, task_id, text, created, expires) "
                        "VALUES (?,?,?,?,?,?,?)", (kind, bot_id, session_id, task_id, text, now, expires or now + ttl))
        return cur.lastrowid


def card_words(pass_id: int, own: str, asked: str = "") -> None:
    ""

    with db() as c:
        c.execute("UPDATE passes SET own_words=?, card_asked=? WHERE id=?",
                  (scrub(own)[:2000], scrub(asked)[:2000], pass_id))


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


def get_pass(pass_id: int) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM passes WHERE id=?", (pass_id,)).fetchone()


def relay_pass(parent: sqlite3.Row, bot_id: str, session_id: str) -> int:
    ""
    with db() as c:
        return c.execute("INSERT INTO passes (kind, bot_id, relay_session_id, text, created, expires) VALUES (?,?,?,?,?,?)",
                         (parent["kind"], bot_id, session_id, parent["text"], parent["created"],
                          parent["expires"])).lastrowid


def relayed_pass(session_id: str) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM passes WHERE relay_session_id=? ORDER BY id DESC LIMIT 1",
                         (session_id,)).fetchone()


def close_pass(pass_id: int) -> None:
    with db() as c:
        c.execute("UPDATE passes SET expires=MIN(expires, ?), relay_session_id=NULL WHERE id=?", (time.time(), pass_id))


def clear_relays() -> None:
    ""
    with db() as c:
        c.execute("UPDATE passes SET expires=MIN(expires, ?), relay_session_id=NULL WHERE relay_session_id IS NOT NULL",
                  (time.time(),))


def task_origin(task_id: str) -> sqlite3.Row | None:
    ""
    with db() as c:
        return c.execute("SELECT * FROM passes WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()


def inherit(task_id: str, parent: sqlite3.Row) -> None:
    ""


    if parent["expires"] <= time.time() or parent["kind"] not in ("message", "button", "approve", "task"):
        open_pass("origin", parent["text"], task_id=task_id, expires=min(time.time(), parent["expires"]))
    elif parent["kind"] in ("message", "button", "approve"):
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









def stored_mode(bot_id: str, site: str) -> str | None:
    ""


    company = base(site_of(site))
    with db() as c:
        rows = [r for r in c.execute("SELECT bot_id, site, mode FROM modes ORDER BY updated DESC, rowid DESC")
                if base(r["site"]) == company]
    if any(r["bot_id"] == bot_id and r["mode"] == "ask" for r in rows):
        return "ask"
    r = next((r for r in rows if r["mode"] != "ask" or r["bot_id"] == EVERY_ZIP), None)
    return r["mode"] if r else None


def mode_for(bot_id: str, site: str) -> str:
    ""
    return stored_mode(bot_id, site) or ("ask" if is_sensitive(site) else "when_asked")


def set_mode(bot_id: str, site: str, mode: str, shared: bool = True) -> None:
    ""




    if mode not in MODES:
        raise ValueError("bad mode")
    if test_mode():
        return
    host = site_of(site)
    with db() as c:
        if shared:
            for r in c.execute("SELECT bot_id, site FROM modes").fetchall():
                if base(r["site"]) == base(host) and bot_id in (r["bot_id"], EVERY_ZIP):
                    c.execute("DELETE FROM modes WHERE bot_id=? AND site=?", (r["bot_id"], r["site"]))
        c.execute("INSERT OR REPLACE INTO modes (bot_id, site, mode, updated) VALUES (?,?,?,?)",
                  (EVERY_ZIP if shared else bot_id, host, mode, time.time()))




def _recent_allows(c: sqlite3.Connection, site: str) -> list[sqlite3.Row]:
    ""
    company = base(site_of(site))
    rows = c.execute("SELECT bot_id, site, session_id, task_id, decided FROM requests WHERE status='allowed' AND decided>? "
                     "AND COALESCE(kind,'signin') IN ('signin','navigate') AND COALESCE(test,0)=?",
                     (time.time() - ONCE_SEC, _test())).fetchall()
    return [r for r in rows if base(r["site"]) == company]


def _last_denied(c: sqlite3.Connection, bot_id: str, site: str) -> float:
    ""
    now = time.time()
    company = base(site_of(site))
    rows = c.execute("SELECT site, why, decided FROM requests WHERE bot_id=? AND status='denied' AND decided>? "
                     "AND COALESCE(kind,'signin') IN ('signin','navigate') AND COALESCE(test,0)=?",
                     (bot_id, now - SCHEDULED_DENY_SEC, _test())).fetchall()
    return max((r["decided"] for r in rows if base(r["site"]) == company
                and (r["why"] == "scheduled" or now - r["decided"] < ONCE_SEC)), default=0)


def allowed_once(bot_id: str, site: str, session_id: str | None, task_id: str | None) -> bool:
    ""



    if not session_id and not task_id:
        return False
    with db() as c:
        allowed = _recent_allows(c, site)
        denied = _last_denied(c, bot_id, site)
        ask = _own_ask(c, bot_id, site)
    for r in allowed:
        if ask and r["bot_id"] != bot_id:
            continue
        own_request = r["bot_id"] == bot_id and ((r["session_id"] and r["session_id"] == session_id)
                                               or (r["task_id"] and r["task_id"] == task_id))
        if r["decided"] > denied or own_request:
            return True
    return False


def _own_ask(c: sqlite3.Connection, bot_id: str, site: str) -> bool:
    return any(r["mode"] == "ask" and base(r["site"]) == base(site_of(site))
               for r in c.execute("SELECT site, mode FROM modes WHERE bot_id=?", (bot_id,)))


def denied_recently(bot_id: str, site: str) -> bool:
    ""

    with db() as c:
        return _last_denied(c, bot_id, site) > 0


def request(bot_id: str, site: str, reason: str, session_id: str | None, task_id: str | None,
            why: str = "", kind: str = "signin") -> int:
    ""



    with db() as c:
        r = c.execute("SELECT id FROM requests WHERE bot_id=? AND site=? AND status='pending' AND COALESCE(kind,'signin') IN ('signin','navigate') "
                      "AND COALESCE(test,0)=?", (bot_id, site, _test())).fetchone()
        if r:
            c.execute("UPDATE requests SET reason=?, session_id=?, task_id=?, why=?, kind=?, raised=? WHERE id=?",
                      (reason[:300], session_id, task_id, why, kind, time.time(), r["id"]))
            return r["id"]
        cur = c.execute("INSERT INTO requests (bot_id, site, reason, session_id, task_id, created, why, kind, test) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (bot_id, site, reason[:300], session_id, task_id, time.time(), why, kind, _test()))
        return cur.lastrowid


def mark_signed_in(bot_id: str, site: str) -> None:
    ""



    owner = TEST_OWNER + bot_id if test_mode() else bot_id
    with db() as c:
        c.execute("INSERT OR REPLACE INTO signed_in (bot_id, site, at) VALUES (?,?,?)",
                  (owner, base(site_of(site)), time.time()))


def _signed_in_rows(c: sqlite3.Connection) -> list[sqlite3.Row]:
    ""
    return [r for r in c.execute("SELECT bot_id, site, at FROM signed_in")
            if r["bot_id"].startswith(TEST_OWNER) == test_mode()]


def signed_in_sites(bot_id: str | None = None) -> set[str]:
    ""

    with db() as c:
        return {r["site"] for r in _signed_in_rows(c)}


def signed_in_at(site: str) -> float | None:
    ""

    company = base(site_of(site))
    with db() as c:
        return max((r["at"] for r in _signed_in_rows(c) if base(r["site"]) == company), default=None)


def forget_signed_in(site: str) -> int:
    ""

    company = base(site_of(site))
    with db() as c:
        stale = [(r["bot_id"], r["site"]) for r in _signed_in_rows(c) if base(r["site"]) == company]
        c.executemany("DELETE FROM signed_in WHERE bot_id=? AND site=?", stale)
    return len(stale)


def shared_site_allowed(bot_id: str, site: str) -> bool:
    ""








    with db() as c:
        denied = _last_denied(c, bot_id, site)
        own_ask = _own_ask(c, bot_id, site)
        if any(r["decided"] > denied and (not own_ask or r["bot_id"] == bot_id) for r in _recent_allows(c, site)):
            return True
    if denied or stored_mode(bot_id, site) == "ask":
        return False
    if signed_in_at(site) is not None:
        return True
    return not is_sensitive(site) and _fits_a_live_pass(site)


def _fits_a_live_pass(site: str) -> bool:
    ""

    if test_mode():
        return False
    company = base(site_of(site))
    marks = ",".join("?" * len(GO_AHEAD_PASSES))
    with db() as c:
        sites = c.execute("SELECT r.site FROM relevance r JOIN passes p ON p.id=r.pass_id "
                          f"WHERE r.ok=1 AND p.expires>? AND p.kind IN ({marks})",
                          (time.time(), *GO_AHEAD_PASSES)).fetchall()
    return any(base(r["site"]) == company for r in sites)


def close_message_passes(session_id: str) -> None:
    ""

    with db() as c:
        c.execute("UPDATE passes SET expires=? WHERE session_id=? AND kind='message' AND expires>?",
                  (time.time(), session_id, time.time()))


def expire_old(now: float | None = None) -> int:
    ""


    now = now or time.time()
    with db() as c:
        cur = c.execute(
            "UPDATE requests SET status='expired', decided=? WHERE status='pending' AND ("
            "(created < ? AND (task_id IS NOT NULL OR why IN ('scheduled','prepared') OR routine IS NOT NULL)) OR "
            "(created < ? AND task_id IS NULL AND COALESCE(why,'') NOT IN ('scheduled','prepared') AND routine IS NULL))",
            (now, now - REQUEST_TTL_LONG_SEC, now - REQUEST_TTL_SEC))
        return cur.rowcount


def reopen_pass(session_id: str | None) -> int | None:
    ""



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





PREPARED = "prepared"
REASON_NEEDED = "needed"
REASON_READY = "ready"
REASON_ANNOUNCED = "announced"
VISIBLE_REQUEST_SQL = "COALESCE(reason_state,'') != 'needed'"



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
                                           f"AND COALESCE(why,'') != 'prepared' AND {VISIBLE_REQUEST_SQL} ORDER BY id")]


def get_request(req_id: int) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
    return dict(r) if r else None


def decide(req_id: int, allow: bool, always: bool = False) -> dict | None:
    ""


    expire_old()
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=? AND status='pending'", (req_id,)).fetchone()
        if not r:
            return None
        if allow and r["reason_state"] == REASON_NEEDED:
            return None
        c.execute("UPDATE requests SET status=?, decided=? WHERE id=?",
                  ("allowed" if allow else "denied", time.time(), req_id))
    kind = r["kind"] or "signin"
    if allow and always and not r["test"]:
        if kind in ("signin", "navigate"):
            set_mode(r["bot_id"], r["site"], "anytime")
        elif r["scope"] and kind in RULE_KINDS:
            set_rule(r["bot_id"], kind, r["scope"], r["site"], source=f"card {req_id}")
    return dict(r)


def open_chat_cards(bot_id: str, session_id: str | None, exclude: int | None = None) -> list[dict]:
    ""
    if not session_id:
        return []
    expire_old()
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM requests WHERE bot_id=? AND session_id=? AND status='pending' AND task_id IS NULL "
            "AND COALESCE(kind,'signin') NOT IN ('signin','navigate') AND COALESCE(test,0)=? AND id != ? "
            f"AND {VISIBLE_REQUEST_SQL} ORDER BY id",
            (bot_id, session_id, _test(), exclude or 0))]


def withdraw(bot_id: str, req_id: int, any_zip: bool = False) -> dict | None:
    ""






    expire_old()
    with db() as c:
        mine = "" if any_zip else "AND bot_id=? AND task_id IS NULL "
        r = c.execute("SELECT * FROM requests WHERE id=? AND status='pending' " + mine +
                      "AND COALESCE(kind,'signin') NOT IN ('signin','navigate')",
                      (req_id,) if any_zip else (req_id, bot_id)).fetchone()
        if not r:
            return None
        c.execute("UPDATE requests SET status='withdrawn', decided=? WHERE id=?", (time.time(), req_id))
    return dict(r)


MAIL_KEPT_SEC = 14 * 86400


def remember_mail(req_id: int, mail: dict) -> None:
    ""




    with db() as c:
        c.execute("DELETE FROM request_mail WHERE at<?", (time.time() - MAIL_KEPT_SEC,))
        c.execute("INSERT OR REPLACE INTO request_mail (req, mail, at) VALUES (?,?,?)",
                  (req_id, json.dumps(mail, ensure_ascii=False), time.time()))


def request_mail(req_id: int) -> dict | None:
    with db() as c:
        r = c.execute("SELECT mail FROM request_mail WHERE req=?", (req_id,)).fetchone()
    try:
        out = json.loads(r["mail"]) if r else None
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def close_sent_copies(recipients: list[str], subject: str) -> list[dict]:
    ""




    sent = {a.strip().lower() for a in recipients if a and a.strip()}
    head = f"\nSubject: {subject}\n"
    if not sent or not subject.strip():
        return []
    closed = []
    with db() as c:
        for r in c.execute("SELECT * FROM requests WHERE status='pending' AND kind='send' AND COALESCE(test,0)=?",
                           (_test(),)).fetchall():
            if {a.strip().lower() for a in (r["target"] or "").split(",") if a.strip()} == sent \
                    and head in (r["inputs"] or "") and "\nIn reply to: " not in (r["inputs"] or ""):
                c.execute("UPDATE requests SET status='withdrawn', decided=? WHERE id=? AND status='pending'",
                          (time.time(), r["id"]))
                closed.append(dict(r))
    return closed


def allow_now(bot_id: str, site: str, reason: str, task_id: str | None = None) -> None:
    ""
    with db() as c:
        c.execute("INSERT INTO requests (bot_id, site, reason, task_id, status, created, decided) "
                  "VALUES (?,?,?,?, 'allowed', ?, ?)", (bot_id, site_of(site), reason[:300], task_id,
                                                         time.time(), time.time()))


def deny_now(bot_id: str, site: str, reason: str, task_id: str | None = None) -> None:
    ""
    with db() as c:
        c.execute("INSERT INTO requests (bot_id, site, reason, task_id, status, created, decided) "
                  "VALUES (?,?,?,?, 'denied', ?, ?)", (bot_id, site_of(site), reason[:300], task_id,
                                                        time.time(), time.time()))





RULE_KINDS = {"signin", "send", "write", "submit", "book"}
ACTION_ONCE_SEC = 30 * 60


ROUTINE_ONCE_SEC = 8 * 86400
SEND_TEXT_MAX = 12000


def waiting_request(bot_id: str, fingerprint: str, session_id: str | None, task_id: str | None) -> int | None:
    ""
    expire_old()
    with db() as c:
        r = c.execute("SELECT id FROM requests WHERE bot_id=? AND fingerprint=? AND status='pending' AND COALESCE(test,0)=? "
                      "AND COALESCE(session_id,'')=? AND COALESCE(task_id,'')=?",
                      (bot_id, fingerprint, _test(), session_id or "", task_id or "")).fetchone()
    return r["id"] if r else None


def action_request(bot_id: str, kind: str, site: str, summary: str, target: str, inputs: str, fingerprint: str,
                   session_id: str | None, task_id: str | None, why: str = "", scope: str = "",
                   excerpt: str = "", details: str = "", routine: dict | None = None,
                   likeness: dict | None = None, ask_rule: int | None = None, words: dict | None = None,
                   asked: str = "", folder: str | None = None, request: str = "") -> int:
    ""









    expire_old()
    inputs, excerpt, details = scrub(inputs)[:SEND_TEXT_MAX], scrub(excerpt)[:1200], scrub(details)[:4000]
    words = words or {}
    title, safe, button = (scrub(str(words.get(k) or ""))[:n] for k, n in (("title", 200), ("safe", 300),
                                                                             ("button", 30)))

    affects_limit = 32000 if kind == "publish" and target == "cloudflare" else 1200
    affects = scrub("\n".join(str(a) for a in words.get("affects") or [] if a))[:affects_limit]
    icons = "\n".join(i for i in (str(x) for x in words.get("icons") or []) if re.fullmatch(r"[a-z.]{1,40}", i))[:300]
    asked = scrub(" ".join((asked or "").split()))[:300]
    rid, rname = ((routine or {}).get("id") or None), ((routine or {}).get("name") or None)
    like = json.dumps(likeness, sort_keys=True)[:6000] if likeness else None
    with db() as c:
        r = c.execute("SELECT id FROM requests WHERE bot_id=? AND fingerprint=? AND status='pending' AND COALESCE(test,0)=?",
                      (bot_id, fingerprint, _test())).fetchone()
        if r:

            c.execute("UPDATE requests SET reason=?, session_id=?, task_id=?, why=?, excerpt=?, routine=?, "
                      "routine_name=?, likeness=?, scope=?, ask_rule=?, title=?, affects=?, safe=?, button=?, "
                      "icons=?, asked=? WHERE id=?",
                      (summary[:300], session_id, task_id, why, excerpt, rid, rname, like,
                       scope[:200] if kind in RULE_KINDS else "", ask_rule, title, affects, safe, button, icons, asked,
                       r["id"]))
            c.execute("UPDATE requests SET raised=? WHERE id=?", (time.time(), r["id"]))
            return r["id"]
        cur = c.execute(
            "INSERT INTO requests (bot_id, site, reason, session_id, task_id, created, why, kind, target, inputs, "
            "fingerprint, scope, excerpt, details, routine, routine_name, likeness, test, ask_rule, title, affects, "
            "safe, button, icons, asked, reason_state, folder, request) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bot_id, site or "", summary[:300], session_id, task_id, time.time(), why, kind,
             target[:300], inputs, fingerprint, scope[:200] if kind in RULE_KINDS else "", excerpt, details,
             rid, rname, like, _test(), ask_rule, title, affects, safe, button, icons, asked,
             REASON_NEEDED if kind == "delete" and not _test() else None, folder, scrub(request or "")[:6000] or None))
        c.execute("UPDATE requests SET raised=created WHERE id=?", (cur.lastrowid,))
        return cur.lastrowid


BECAUSE_MAX = 140


def explain_request(req_id: int, bot_id: str, text: str) -> bool:
    ""

    expire_old()
    text = scrub(" ".join((text or "").split()))
    if not text:
        return False
    with db() as c:
        r = c.execute("SELECT * FROM requests WHERE id=? AND bot_id=? AND status='pending' "
                      "AND COALESCE(kind,'signin') NOT IN ('signin','navigate') AND COALESCE(test,0)=?",
                      (req_id, bot_id, _test())).fetchone()
        if r is None:
            return False
        if r["reason_state"] is not None:


            def normalize(value):
                return " ".join(re.findall(r"\w+", str(value or "").lower()))
            if len(text) > BECAUSE_MAX or len(normalize(text).split()) < 4 or normalize(text) in {
                    normalize(r[k]) for k in ("reason", "title", "target", "inputs", "details")}:
                return False
        now = time.time()
        updated = c.execute("UPDATE requests SET because=?, reason_state=CASE WHEN reason_state=? THEN ? ELSE reason_state END, "
                            "created=CASE WHEN reason_state=? THEN ? ELSE created END, "
                            "raised=CASE WHEN reason_state=? THEN ? ELSE raised END WHERE id=? AND status='pending' "
                            "AND bot_id=? AND COALESCE(test,0)=?",
                            (text[:BECAUSE_MAX], REASON_NEEDED, REASON_READY, REASON_NEEDED, now, REASON_NEEDED, now,
                             req_id, bot_id, _test()))
        return updated.rowcount > 0


def request_announced(req_id: int) -> None:
    ""
    with db() as c:
        c.execute("UPDATE requests SET reason_state=? WHERE id=? AND reason_state=?",
                  (REASON_ANNOUNCED, req_id, REASON_READY))


MADE_KEEP_SEC = 7 * 24 * 3600


def note_made(bot_id: str, place: str, path: str, how: str, sources: list[str] | None = None,
              born: float | None = None) -> None:
    ""

    now = time.time()
    with db() as c:
        c.execute("DELETE FROM made WHERE at<?", (now - MADE_KEEP_SEC,))
        c.execute("INSERT INTO made (bot_id, place, path, how, sources, at, born) VALUES (?,?,?,?,?,?,?)",
                  (bot_id, place, path[:500], how, json.dumps((sources or [])[:20]), now, born))


def made_records(place: str) -> list[dict]:
    ""
    with db() as c:
        rows = c.execute("SELECT * FROM made WHERE place=? AND at>? ORDER BY id", (place, time.time() - MADE_KEEP_SEC))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d.get("sources") or "[]")
            except ValueError:
                d["sources"] = []
            out.append(d)
        return out


CLOSE_TEXT = 0.85


def alike(allowed: dict | None, now: dict | None) -> bool:
    ""




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
        words = lambda t: re.findall(r"[\w@+-]+(?:\.[\w@+-]+)*", t.lower())
        return difflib.SequenceMatcher(None, words(a), words(b)).ratio() >= CLOSE_TEXT
    return a == b


def approval_available(bot_id: str, fingerprint: str) -> dict | None:
    ""
    with contextlib.closing(db()) as c:
        row = c.execute("SELECT * FROM requests WHERE bot_id=? AND fingerprint=? AND status='allowed' "
                        "AND used IS NULL AND decided>? AND COALESCE(test,0)=? ORDER BY id DESC LIMIT 1",
                        (bot_id, fingerprint, time.time() - ACTION_ONCE_SEC, _test())).fetchone()
    return dict(row) if row else None


def use_approval(bot_id: str, fingerprint: str, routine: str | None = None, kind: str = "",
                 likeness: dict | None = None) -> dict | None:
    ""


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


def task_approvals(bot_id: str, task_id: str) -> list[tuple[str, str]]:
    ""
    with db() as c:
        return [(r["folder"], r["fingerprint"]) for r in c.execute(
            "SELECT folder, fingerprint FROM requests WHERE bot_id=? AND task_id=? AND folder IS NOT NULL AND "
            "status='allowed' AND used IS NULL AND decided>? AND COALESCE(test,0)=? ORDER BY id DESC",
            (bot_id, task_id, time.time() - ACTION_ONCE_SEC, _test()))]


def give_back(request_id: int) -> None:
    ""
    with db() as c:
        c.execute("UPDATE requests SET used=NULL WHERE id=?", (request_id,))


def use_review(pass_id: int, what: str) -> bool:
    ""
    key = f"review:{pass_id}:{what.strip().lower()}"
    with db() as c:
        if c.execute("SELECT 1 FROM judgments WHERE key=?", (key,)).fetchone():
            return False
        c.execute("INSERT INTO judgments (key, value, at) VALUES (?,?,?)", (key, "used", time.time()))
    return True


def post_site(where: str) -> str:
    ""

    where = " ".join(str(where or "").lower().split())
    if re.search(r"\binstagram\b", where):
        return "instagram.com"
    return "x.com" if re.search(r"^(?:x|x\.com|twitter|twitter\.com)\b", where) else ""


def use_team_post(need_id: int) -> bool:
    ""
    with db() as c:
        return c.execute("INSERT OR IGNORE INTO judgments (key, value, at) VALUES (?,?,?)",
                         (f"team_post:{int(need_id)}", "used", time.time())).rowcount == 1


def give_back_team_post(need_id: int) -> None:
    ""
    with db() as c:
        c.execute("DELETE FROM judgments WHERE key=?", (f"team_post:{int(need_id)}",))


def action_denied(bot_id: str, fingerprint: str, since: float = 0) -> bool:
    ""


    with db() as c:
        return c.execute("SELECT 1 FROM requests WHERE bot_id=? AND fingerprint=? AND status='denied' AND decided>? "
                         "AND COALESCE(test,0)=?", (bot_id, fingerprint, max(time.time() - ONCE_SEC, since),
                                                    _test())).fetchone() is not None


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
    ""

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










ASK_RULE_PREFIX = "ask:"
ANYWHERE = "*"



ASK_RULE_WHATS = {
    "push": ("pushing code to any repo", "pushing code to the branch {where}"),
    "change": ("changing anything", "changing anything in {where}"),
    "send": ("sending any message or email", "sending anything in {where}"),
}

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
    ""

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
    ""
    anywhere, somewhere = ASK_RULE_WHATS.get(what, ("doing this", "doing this in {where}"))
    if not where or where == ANYWHERE:
        return anywhere
    if what == "send" and "@" in where:
        return f"sending anything to anyone at {where[1:]}" if where.startswith("@") else f"sending anything to {where}"
    return somewhere.format(where=ASK_RULE_PLACES.get(where, (where,))[0])


def set_ask_rule(bot_id: str, what: str, where: str = ANYWHERE, source: str = "") -> int:
    ""
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
    ""




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
    ""

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
    ""

    expire_old()
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM requests WHERE status='expired' AND decided>? AND COALESCE(test,0)=0 "
            f"AND COALESCE(why,'') != 'prepared' AND {VISIBLE_REQUEST_SQL} ORDER BY decided, id", (since,))]


def log_action(bot_id: str, kind: str, site: str, summary: str, how: str) -> int:
    ""
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


CONTENT_SEC = 30 * 60


def remember_content(key: str, source: str, text: str, site: str = "") -> None:
    ""

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






def content_check() -> bool:
    return os.environ.get("ZERO_CONTENT_CHECK") == "1"


def recent_content(key: str) -> dict | None:
    if not content_check():
        return None
    with db() as c:
        r = c.execute("SELECT * FROM content WHERE key=? AND at>?", (key, time.time() - CONTENT_SEC)).fetchone()
    return dict(r) if r else None


def earlier_content(key: str) -> dict | None:
    ""
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
    if "refs" not in data:
        data = {"refs": data}
    return r["site"] or "", data


def remember_refs(key: str, site: str, refs: dict, fields: dict | None = None, cookie: bool = False,
                  facts: dict | None = None) -> None:
    ""



    with db() as c:
        old_site, old = _page(c, key)
        typed = old.get("typed", {}) if old_site == site else {}
        data = {"refs": refs, "fields": fields or {}, "typed": typed, "cookie": bool(cookie), **(facts or {})}
        c.execute("INSERT OR REPLACE INTO page_refs (key, site, refs, at) VALUES (?,?,?,?)",
                  (key, site, json.dumps(data)[:200000], time.time()))


def remember_typed(key: str, label: str, text: str) -> None:
    ""
    if not label:
        return
    with db() as c:
        site, data = _page(c, key)
        data.setdefault("typed", {})[label] = scrub(text or "")[:4000]
        c.execute("INSERT OR REPLACE INTO page_refs (key, site, refs, at) VALUES (?,?,?,?)",
                  (key, site, json.dumps(data)[:200000], time.time()))


def page_data(key: str) -> tuple[str, dict]:
    ""
    with db() as c:
        return _page(c, key)


def save_page_data(key: str, site: str, data: dict) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO page_refs (key, site, refs, at) VALUES (?,?,?,?)",
                  (key, site, json.dumps(data)[:200000], time.time()))


def page_info(key: str) -> dict:
    ""

    with db() as c:
        site, data = _page(c, key)
    return {"site": site, "fields": {**(data.get("fields") or {}), **(data.get("typed") or {})},
            "cookie_refs": list(data.get("cookie_refs") or []), "cookie_labels": list(data.get("cookie_labels") or []),
            "headings": list(data.get("headings") or []), "dialogs": list(data.get("dialogs") or []),
            "invite": bool(data.get("invite")), "sworn": bool(data.get("sworn")),
            "form_end": str(data.get("form_end") or "")}


def ref_label(key: str, ref: str) -> tuple[str, str]:
    ""
    with db() as c:
        site, data = _page(c, key)
    return str((data.get("refs") or {}).get(ref.lstrip("@"), "")), site





ASKED_NO_FIT = "asked_no_fit"


def log_signin(bot_id: str, site: str, reason: str, how: str) -> None:
    ""




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


def site_opened(bot_id: str, site: str) -> None:
    ""
    with db() as c:
        c.execute("INSERT OR REPLACE INTO site_opened (bot_id, site, at) VALUES (?,?,?)",
                  (bot_id, site_of(site), time.time()))


def last_opened(bot_id: str, site: str) -> float | None:
    ""


    with db() as c:
        r = c.execute("SELECT at FROM site_opened WHERE bot_id=? AND site=?", (bot_id, site_of(site))).fetchone()
    return r[0] if r else None







def pause_site(bot_id: str, site: str, report_id: int) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO paused_sites (bot_id, site, report_id, at) VALUES (?,?,?,?)",
                  (bot_id, site_of(site), report_id, time.time()))


def paused_site(bot_id: str, site: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM paused_sites WHERE bot_id=? AND site=?", (bot_id, site_of(site))).fetchone()
    return dict(r) if r else None


def chat_took_browser(bot_id: str) -> None:
    ""

    with db() as c:
        c.execute("INSERT OR REPLACE INTO judgments (key, value, at) VALUES (?,?,?)",
                  (f"chat_browser:{bot_id}", "1", time.time()))


def chat_has_browser(bot_id: str, within: float) -> bool:
    ""
    with db() as c:
        r = c.execute("SELECT at FROM judgments WHERE key=?", (f"chat_browser:{bot_id}",)).fetchone()
    return bool(r) and time.time() - r["at"] < within





PAUSE_SEC = 24 * 3600
PAUSE_TRY_SEC = 30 * 60


def pause_try(bot_id: str, site: str) -> bool:
    ""

    now, site = time.time(), site_of(site)
    with db() as c:
        r = c.execute("SELECT at, tried FROM paused_sites WHERE bot_id=? AND site=?", (bot_id, site)).fetchone()
        if r is None:
            return True
        tried = r["tried"] or 0.0
        if now - tried < PAUSE_TRY_SEC:
            return True
        if now - max(r["at"], tried) < PAUSE_SEC:
            return False
        c.execute("UPDATE paused_sites SET tried=? WHERE bot_id=? AND site=?", (now, bot_id, site))
    return True


def paused_sites() -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM paused_sites ORDER BY at")]


def unpause_site(bot_id: str, site: str) -> None:
    with db() as c:
        c.execute("DELETE FROM paused_sites WHERE bot_id=? AND site=?", (bot_id, site_of(site)))


SITE_TRIES_SEC = 3 * 3600


def site_tried(bot_id: str, run: str, site: str, worked: bool) -> int:
    ""

    now, site = time.time(), site_of(site)
    with db() as c:
        c.execute("DELETE FROM site_tries WHERE at<?", (now - SITE_TRIES_SEC,))
        c.execute("INSERT INTO site_tries (bot_id, run, site, failed, worked, at) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT (bot_id, run, site) DO UPDATE SET failed=failed+excluded.failed, "
                  "worked=MAX(worked, excluded.worked), at=excluded.at",
                  (bot_id, run, site, 0 if worked else 1, int(worked), now))
        r = c.execute("SELECT failed, worked FROM site_tries WHERE bot_id=? AND run=? AND site=?",
                      (bot_id, run, site)).fetchone()
    return 0 if r["worked"] else r["failed"]


def requests_after(after_id: int) -> list[dict]:
    expire_old()
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM requests WHERE (id>? OR reason_state='ready') "
                                           "AND status='pending' AND COALESCE(test,0)=0 "
                                           f"AND COALESCE(why,'') != 'prepared' AND {VISIBLE_REQUEST_SQL} ORDER BY id", (after_id,))]




def test_cards(status: str | None = "pending") -> list[dict]:
    ""
    expire_old()
    with db() as c:
        q = "SELECT * FROM requests WHERE COALESCE(test,0)=1" + (" AND status=?" if status else "") + " ORDER BY id"
        return [dict(r) for r in c.execute(q, (status,) if status else ())]


def decide_test(req_id: int, allow: bool) -> dict | None:
    ""
    with db() as c:
        r = c.execute("SELECT id FROM requests WHERE id=? AND COALESCE(test,0)=1", (req_id,)).fetchone()
    return decide(req_id, allow) if r else None


def clear_test_cards() -> int:
    ""
    with db() as c:
        return c.execute("UPDATE requests SET status='expired', decided=? WHERE status='pending' AND COALESCE(test,0)=1",
                         (time.time(),)).rowcount




COMMAND_SEC = 15 * 60


def _command_hash(command: str) -> str:
    return hashlib.sha256((command or "").strip().encode("utf-8", "replace")).hexdigest()


def remember_command(bot_id: str, forms: list[str], flagged: str | None, place: str | None = None) -> None:
    ""



    now = time.time()
    with db() as c:
        c.execute("DELETE FROM commands WHERE at<?", (now - COMMAND_SEC,))
        for h in dict.fromkeys(_command_hash(f) for f in forms if f and f.strip()):
            c.execute("INSERT INTO commands (hash, bot_id, cleared, flagged, at, place) VALUES (?,?,?,?,?,?)",
                      (h, bot_id, 0 if flagged else 1, flagged or "", now, place_hash(place)))


def place_hash(place: str | None) -> str:
    ""
    return _command_hash(place)[:16] if place else ""


def gate_checked(bot_id: str, command: str) -> dict | None:
    ""
    with db() as c:
        r = c.execute("SELECT * FROM commands WHERE hash=? AND bot_id=? AND at>? ORDER BY at DESC LIMIT 1",
                      (_command_hash(command), bot_id, time.time() - COMMAND_SEC)).fetchone()
    return dict(r) if r else None





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

_SCRIPT_DESC = re.compile(r"^(script execution via|command parser limit)", re.I)
_LANGUAGES = [(r"python[\d.]*|pypy[\d.]*", "Python"), (r"node|nodejs|deno|bun", "JavaScript"), (r"perl", "Perl"),
              (r"ruby", "Ruby"), (r"php", "PHP"), (r"pwsh|powershell", "PowerShell"), (r"osascript", "AppleScript"),
              (r"lua", "Lua")]


def plain_command(description: str, command: str = "") -> str:
    ""

    d = description or ""


    m = re.match(r"^computer_use:\s*type\s+'(.{1,60})'", d, re.I)
    if m:
        return f'Type "{m.group(1)}" on your Mac'
    if re.match(r"^computer_use:\s*(key|press|hotkey)", d, re.I):
        return "Press keys on your Mac"
    if re.match(r"^computer_use:\s*(click|double|right|drag|scroll|move)", d, re.I):
        return "Click on your Mac"
    if re.match(r"^computer_use", d, re.I):
        return "Use your Mac"
    if _SCRIPT_DESC.match(d):
        for pat, language in _LANGUAGES:
            if re.search(rf"(?:^|[\s;&|(/])(?:{pat})(?=\s|$)", command or ""):
                return f"Run a short {language} script"
        return "Run a short script" if d.lower().startswith("script") else "Run a command too long or tangled to read"
    said = next((say for pat, say in _PLAIN_COMMANDS if re.search(pat, d, re.I)), None)
    if said:
        return said


    line = " ".join((command or "").strip().splitlines()[0].split()) if (command or "").strip() else ""
    if not line:
        return "Run a command it couldn't describe"
    return f'Run the command "{line if len(line) <= 60 else line[:57].rstrip() + "..."}"'


_EVENT_TITLE = re.compile(r"\b(?:titled|called|named)\s*:?\s*[\"“]([^\"”\n]{1,120})(?:[\"”]|$)", re.I)
_EVENT_WORD = re.compile(r"\b(?:events?|calendar|appointments?)\b", re.I)
_EVENT_NEW = re.compile(r"\b(?:create|add|schedule|put|make|set up|book)\b", re.I)
_EVENT_EDIT = re.compile(r"\b(?:move|reschedule|change|update|edit|rename|delete|cancel|remove)\b", re.I)
_EVENT_DAY = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tonight|tomorrow)\b", re.I)
_EVENT_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)
_EVENT_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.I)


def calendar_events(text: str) -> list[str]:
    ""
    intent = re.sub(r'["“][^"”]*["”]', "", text)
    if not _EVENT_NEW.search(intent) or not _EVENT_WORD.search(intent) or _EVENT_EDIT.search(intent) \
            or re.search(r"\b(?:push|shift|modify|postpone|bump)\b", intent, re.I):
        return []
    events = []


    numbered = list(re.finditer(r'\b\d{1,2}[.)]\s*([^"“\n]{0,60}?)["“]([^"”]+)["”]', text))
    days = {d[:3].lower(): d for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}
    months = {m[:3].lower(): m for m in ("January", "February", "March", "April", "May", "June", "July", "August",
                                        "September", "October", "November", "December")}
    for i, title in enumerate(numbered):
        rest = title[1] + " " + text[title.end():numbered[i + 1].start() if i + 1 < len(numbered) else len(text)]
        rest = re.split(r'\b(?:location|notes|popup|reminder)\b', rest, maxsplit=1, flags=re.I)[0]
        day = re.search(r'\b(Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:r(?:s(?:day)?)?)?|'
                        r'Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b', rest, re.I)
        date = _EVENT_DATE.search(rest)
        clocks = list(_EVENT_TIME.finditer(rest))
        when = []
        if date:
            year = re.match(r',?\s+(\d{4})\b', rest[date.end():])
            when.append((f"{days[day[1][:3].lower()]}, " if day else "")
                        + f"{months[date[1][:3].lower()]} {int(date[2])}"
                        + (f", {year[1]}" if year else ""))
        elif day:
            when.append(days[day[1][:3].lower()])
        if clocks:
            clocks = clocks[:2] if len(clocks) > 1 and re.fullmatch(
                r"\s*(?:to|until|till|-|\u2013)\s*", rest[clocks[0].end():clocks[1].start()], re.I) else clocks[:1]
            when.append(" to ".join(f"{int(c[1])}{':' + c[2] if c[2] else ''} {c[3].lower()}.m."
                                    for c in clocks))
        events.append(", ".join([title[2].strip(), *when]))
    return events


def new_event(text: str) -> str:
    ""



    text = " ".join((text or "").split())
    m = _EVENT_TITLE.search(text)
    if not m:
        return ""
    rest = (text[:m.start()] + " " + text[m.end():]).strip()
    if not _EVENT_WORD.search(rest) or not _EVENT_NEW.search(rest) or _EVENT_EDIT.search(rest):
        return ""


    after = rest[_EVENT_NEW.search(rest).start():]
    day = _EVENT_DAY.search(after) or _EVENT_DAY.search(rest)
    date = None if day else (_EVENT_DATE.search(after) or _EVENT_DATE.search(rest))
    clock = _EVENT_TIME.search(after) or _EVENT_TIME.search(rest)
    when = []
    if day:
        word = day.group(1).lower()
        when.append(word if word in ("today", "tonight", "tomorrow") else word[:3].capitalize())
    elif date:
        when.append(f"{date.group(1)[:3].capitalize()} {int(date.group(2))}")
    if clock:
        when.append(f"{int(clock.group(1))}{':' + clock.group(2) if clock.group(2) else ''} {clock.group(3).upper()}M")
    title = m.group(1).strip().rstrip(".")
    return ", ".join([title, " ".join(when)]) if when else title


LOGIN_RECOVERY_SEC = 30 * 60
LOGIN_PAGE_TOOLS = frozenset(("browser_navigate", "browser_snapshot", "browser_click", "browser_press",
                              "browser_vault_fill", "browser_back"))
_LOGIN_REJECTED = re.compile(
    r"\b(?:(?:incorrect|invalid|wrong) (?:(?:username|e-?mail(?: address)?|account)(?: or password)?|password|credentials)|"
    r"(?:(?:username|e-?mail(?: address)?|account)(?: or password)?|password|credentials) "
    r"(?:you entered )?(?:is |are |was |were )?(?:incorrect|invalid|wrong)|"
    r"(?:your )?account (?:is |has been )?locked)\b", re.I)
_LOGIN_PHONE = re.compile(r"\b(?:code (?:was |has been )?sent to (?:your |the )?(?:phone|mobile)|"
                          r"(?:check|approve[^.\n]*) (?:on )?your (?:phone|mobile)|"
                          r"enter[^.\n]*(?:texted|sms) code|"
                          r"(?:enter|type)[^.\n]*code from your authenticator app|"
                          r"(?:we )?emailed (?:you )?a code|code (?:was |has been )?sent to (?:your )?email)\b", re.I)
_LOGIN_CHALLENGE = re.compile(r"captcha|verify (?:that )?you(?: are|'re) human|human verification|"
                              r"unusual browser|checking your browser", re.I)


def _login_text(value, limit=220):

    text = re.sub(r"(?i)\b(?:set-cookie|cookie|authorization)\s*[:=][^\n]*", "[removed]", str(value or ""))
    text = re.sub(r"https?://[^\s\"'<>]+", lambda m: _login_url(m[0]), text)
    text = scrub(text)

    text = re.sub(r"(?i)\b(?:password|passcode|token|secret|api[_ -]?key)\s*[:=]\s*\[removed\]", "[removed]", text)
    return text[:limit]


def _login_url(value):
    try:
        p = urlparse(str(value or ""))
        return f"{p.scheme}://{p.hostname}{p.path}" if p.scheme in ("https", "http") and p.hostname else ""
    except ValueError:
        return ""





_PAGE_FIELD = re.compile(r'textbox "([^"]*)"', re.I)
_PAGE_CONTACT_LABEL = re.compile(r"\b(?:phone|mobile|e-?mail)\b", re.I)
_PAGE_USERNAME_LABEL = re.compile(r"\buser\s?(?:name|id)\b", re.I)
_PAGE_CODE_LABEL = re.compile(r"\b(?:one[- ]?time|otp|verification|security|passcode|sms|texted|emailed)\b|"
                              r"^(?:enter )?(?:the |your )?code$", re.I)


def code_signin_step(page: str) -> str:
    ""

    labels = _PAGE_FIELD.findall(page or "")
    if any(_PAGE_CODE_LABEL.search(x) for x in labels) and not re.search("authenticator", page, re.I):
        return "code"
    if any(re.search("password", x, re.I) or _PAGE_USERNAME_LABEL.search(x) for x in labels):
        return ""
    return "contact" if any(_PAGE_CONTACT_LABEL.search(x) for x in labels) else ""


def remember_login_page(bot, key, site, tool, result):
    ""
    if not site or not key or tool not in LOGIN_PAGE_TOOLS:
        return
    try:
        out = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        return
    if not isinstance(out, dict):
        return
    now, site = time.time(), base(site)
    with db() as c:
        c.execute("DELETE FROM login_recovery WHERE started<?", (now - LOGIN_RECOVERY_SEC,))
        c.execute("INSERT OR IGNORE INTO login_recovery(bot_id,key,site,at,started) VALUES (?,?,?,?,?)", (bot, key, site, now, now))
        row = c.execute("SELECT * FROM login_recovery WHERE bot_id=? AND key=? AND site=?", (bot, key, site)).fetchone()
        if tool == "browser_vault_fill":
            if out.get("success") is True and out.get("kind") == "login":
                page_site = json.loads(row["evidence"]).get("page_site", base(site_of(str(out.get("origin") or "")) or site))
                c.execute("UPDATE login_recovery SET filled=?,at=?,evidence=? WHERE bot_id=? AND key=? AND site=?",
                          (now, now, json.dumps({"page_site": page_site}), bot, key, site))
                return
            if out.get("success") is not False:
                return
        page = str(out.get("snapshot") or "")
        reason, kind = "", "page"
        if _LOGIN_CHALLENGE.search(page) or out.get("bot_detection_warning"):
            kind = "challenge"
        else:
            for line in page.splitlines():

                rejected = _LOGIN_REJECTED.search(line)
                phone = _LOGIN_PHONE.search(line)
                match = phone or rejected
                before = line[:match.start()].rsplit(".", 1)[-1] if match else ""
                if match and not re.search(r"\b(?:if|when|example)\b", before, re.I):


                    reason = _login_text(phone[0], 120) if phone else rejected[0]
                    kind = "credentials"
                    break
        status = out.get("status", out.get("status_code"))
        page_error = next((line for line in page.splitlines() if re.search(
            r"timed? out|timeout|kept reloading|page reload|redirect loop|browser not ready|form not found", line, re.I)), "")
        evidence = {"kind": kind, "reason": reason, "step": tool,
                    "page_site": base(site_of(str(out.get("url") or "")) or site),
                    "url": _login_text(_login_url(out.get("url")), 180),
                    "title": _login_text(out.get("title"), 70),
                    "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
                    "error": _login_text(out.get("error") or out.get("verification_error") or page_error)}
        previous = json.loads(row["evidence"])
        if previous.get("kind") == "credentials" and kind == "page":
            evidence = previous
        evidence["signs_in"] = code_signin_step(page) if page else previous.get("signs_in", "")
        phase = row["phase"]
        if phase == "retry" and tool == "browser_navigate":
            phase = "retried" if evidence.get("error") or page else "reopened"
        elif phase == "reopened" and (tool == "browser_snapshot" or evidence["error"]):
            phase = "retried"
        c.execute("UPDATE login_recovery SET evidence=?,phase=?,at=? WHERE bot_id=? AND key=? AND site=?",
                  (json.dumps(evidence), phase, now, bot, key, site))


def login_page_seen(bot, key, site):
    with db() as c:
        return c.execute("SELECT 1 FROM login_recovery WHERE bot_id=? AND key=? AND site=? AND at>? "
                         "AND (filled>0 OR json_extract(evidence,'$.kind') IN ('credentials','challenge') "
                         "OR json_extract(evidence,'$.error')!='')",
                         (bot, key, base(site), time.time() - LOGIN_RECOVERY_SEC)).fetchone() is not None


def login_page_step(bot, key, site):
    ""
    with db() as c:
        row = c.execute("SELECT evidence FROM login_recovery WHERE bot_id=? AND key=? AND site=? AND at>?",
                        (bot, key, base(site), time.time() - LOGIN_RECOVERY_SEC)).fetchone()
    evidence = json.loads(row["evidence"]) if row else {}
    return evidence.get("signs_in", "") if evidence.get("step") else None


def login_page_targets(bot, key, site):
    ""
    with db() as c:
        return {r[0] for r in c.execute("SELECT site FROM login_recovery WHERE bot_id=? AND key=? AND started>? "
                                       "AND json_extract(evidence,'$.page_site')=?",
                                       (bot, key, time.time() - LOGIN_RECOVERY_SEC, base(site)))}


def login_recovery(bot, key, site):
    ""
    now, site = time.time(), base(site)
    with db() as c:
        c.execute("DELETE FROM login_recovery WHERE started<?", (now - LOGIN_RECOVERY_SEC,))
        c.execute("INSERT OR IGNORE INTO login_recovery(bot_id,key,site,at,started) VALUES (?,?,?,?,?)", (bot, key, site, now, now))
        row = c.execute("SELECT * FROM login_recovery WHERE bot_id=? AND key=? AND site=?", (bot, key, site)).fetchone()
        result = json.loads(row["evidence"])
        result["filled"] = bool(row["filled"])
        result["phase"] = row["phase"]
        if result.get("kind") not in ("credentials", "challenge") and row["phase"] == "observing" \
                and (result["filled"] or result.get("error")):
            c.execute("UPDATE login_recovery SET phase='retry' WHERE bot_id=? AND key=? AND site=?", (bot, key, site))
        return result


def dump() -> str:
    ""
    with db() as c:
        return json.dumps({t: [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 10")]
                           for t in ("passes", "modes", "requests", "signins", "rules", "actions")},
                      indent=1, default=str)
