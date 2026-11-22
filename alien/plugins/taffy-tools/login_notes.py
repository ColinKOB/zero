""












import json
import time
import urllib.parse
import urllib.request

from . import consent, gate, home, mac

CACHE_SEC = 30
_cache: dict[str, tuple[float, dict]] = {}

SCHEMA = {
    "name": "login_note",
    "description": (
        "Leave a short note on a site's saved login for the other Zips who sign in there: something special about "
        "signing in that you learned, such as 'This site texts a code; I read it from Messages on Colin's Mac, which "
        "he allowed on Nov 20.' Every Zip that uses this login reads its notes first, and Colin sees them in "
        "Settings, Sign-Ins. Never put a password, a code or any other secret in a note."),
    "parameters": {
        "type": "object",
        "properties": {
            "site": {"type": "string", "description": "The site's address, like https://studentaid.gov."},
            "note": {"type": "string", "description": "One or two plain sentences, under 280 characters."},
        },
        "required": ["site", "note"],
    },
}


def _request(path: str, body: dict | None = None, timeout: float = 4) -> dict:
    key = mac.key_file().read_text().strip()
    req = urllib.request.Request(mac.hub() + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "x-taffy-internal": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return {"error": str(json.loads(e.read()).get("detail") or f"The hub refused that ({e.code}).")}
        except Exception:
            return {"error": f"The hub refused that ({e.code})."}


def about(site: str, fresh: bool = False) -> dict:
    ""

    key = consent.base(consent.site_of(site) or site or "")
    if not key:
        return {"text": "", "mac_only": False}
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_SEC:
        return hit[1]
    try:
        out = _request("sign-in-notes?" + urllib.parse.urlencode({"site": key}))
        out = {"text": str(out.get("text") or ""), "mac_only": out.get("mac_only") is True}
    except Exception:
        gate.log.debug("could not read a login's notes", exc_info=True)
        return {"text": "", "mac_only": False}
    if len(_cache) > 128:
        _cache.clear()
    _cache[key] = (time.monotonic(), out)
    return out


def tool(args: dict, **_kw) -> str:
    site, note = str(args.get("site") or "").strip(), str(args.get("note") or "").strip()
    if not site or not note:
        return json.dumps({"error": "Give the site and the note."})
    try:
        out = _request("sign-in-notes", {"bot": gate._bot(), "site": site, "text": note})
    except Exception:
        return json.dumps({"error": "The note could not be saved just now. Try again later."})
    if out.get("error"):
        return json.dumps({"error": out["error"]})
    _cache.pop(str(out.get("site") or ""), None)
    return json.dumps({"saved": True, "note": "Saved. Other Zips read it before they use this login."})


MAC_ONLY = ("Not signed in: Colin keeps the {site} login for his Mac only, so it is never filled in your own browser. "
            "Do this site's work on his Mac: call mac_start, open the sign-in page with mac_codex, and when it stops at "
            "the sign-in, call mac_codex again with sign_in set to this login's handle.")


UNCHECKED = ("Not signed in: I couldn't check where Colin lets this login be used. Try again in a minute.")


def mac_only(site: str) -> bool:
    ""

    import sqlite3
    key = consent.base(consent.site_of(site) or site or "")
    db = gate._hub_state() / "hub.db"
    if not db.exists():
        return False
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(signin_access)")}
        if "use_on" not in cols:
            return False
        row = c.execute("SELECT use_on FROM signin_access WHERE site=?", (key,)).fetchone()
    finally:
        c.close()
    return bool(row) and row[0] == "mac"


def any_mac_only() -> bool:
    ""
    import sqlite3
    db = gate._hub_state() / "hub.db"
    if not db.exists():
        return False
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        if "use_on" not in {r[1] for r in c.execute("PRAGMA table_info(signin_access)")}:
            return False
        return c.execute("SELECT 1 FROM signin_access WHERE use_on='mac' AND removed=0 LIMIT 1").fetchone() is not None
    finally:
        c.close()


def block(tool_name: str, site: str) -> dict | None:
    ""
    if tool_name != "browser_vault_fill" or not site:
        return None
    try:
        kept = mac_only(site)
    except Exception:
        gate.log.warning("could not read where a login may be used", exc_info=True)
        return {"action": "block", "message": home.say(UNCHECKED)}
    if kept:
        return {"action": "block", "message": home.say(MAC_ONLY, site=consent.base(site))}
    return None


def with_notes(tool_name: str, result, site: str):
    ""
    if tool_name not in ("browser_vault_list", "browser_vault_fill") or not isinstance(result, str) or not site:
        return None
    text = about(site).get("text") or ""
    if not text:
        return None
    try:
        data = json.loads(result)
    except ValueError:
        data = None
    if isinstance(data, dict):
        data["login_notes"] = text
        return json.dumps(data, ensure_ascii=False)
    return f"{result}\n\n{text}"
