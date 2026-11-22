""
























import importlib
import json
import logging
import re
import sys
import threading
import time
import types
from pathlib import Path
from urllib.parse import urlsplit

from . import cdp_storage, config, consent, logins, screen, signin, signin_access, store, validate, who

log = logging.getLogger("taffy.teach")

STEP_KEYS = ("do", "label", "takes", "page")
DOES = ("open", "type", "press", "key", "provider")
TAKES = ("number", "email", "code", "password", "other")
LABEL_MAX = 80
WATCH_MAX_SEC = 20 * 60
FOLLOW_SEC = 30 * 60
FOLLOWED_SEC = 10 * 60
MISSES = 2
EVENTS_MAX = 500


@store.schema
def _schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS taught_logins (site TEXT PRIMARY KEY, steps TEXT NOT NULL, "
                 "taught_by TEXT NOT NULL, taught_at REAL NOT NULL)")




def _rules():
    ""

    name = "taffy_teach_rules"
    if name not in sys.modules:
        folder = config.PLUGIN_DIR if (config.PLUGIN_DIR / "tiers.py").exists() else \
            Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools"
        package = types.ModuleType(name)
        package.__path__ = [str(folder)]
        sys.modules[name] = package
        sys.modules[f"{name}.consent"] = sys.modules["taffy_consent"]
    return importlib.import_module(f"{name}.tiers")




_NEVER_FIELD = re.compile(r"new password|confirm (your )?password|re-?enter (your )?password|card number|name on card|"
                          r"\bcvv\b|\bcvc\b|expir|routing|account number|\bssn\b|social security", re.I)
_NEVER_PAGE = re.compile(r"checkout|check-out|check out|payment|billing|/pay\b|/pay-|security settings|"
                         r"settings/security|account/security|change[- ]password|reset[- ]password|"
                         r"(two-factor|2fa|2-step)[- ]settings", re.I)


def refusal(step: dict, site: str, about=()) -> str | None:
    ""

    if any(_NEVER_PAGE.search(str(t or "")) for t in (urlsplit(step.get("page") or "").path, *about)):
        return "a page for paying or changing security settings"
    label = step.get("label") or ""
    if step.get("do") == "type" and _NEVER_FIELD.search(label):
        return f'the field "{label}"'
    if step.get("do") == "press":
        page = {"fields": {"typed": "x"}, "headings": list(about), "dialogs": []}
        if _rules().classify_click(label, site, page) is not None:
            return f'the button "{label}"'
    return None




_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DIGITS = re.compile(r"\+?\d[\d\s().-]*\d")
_IDLIKE = re.compile(r"\d{3,}|^[\w-]{24,}$|^[0-9a-f]{12,}$", re.I)


def _label(text) -> str:
    t = " ".join(str(text or "").split())
    t = _EMAIL.sub("…", t)
    t = _DIGITS.sub(lambda m: m.group() if sum(c.isdigit() for c in m.group()) < 3 else "…", t)
    return consent.scrub(t)[:LABEL_MAX].strip()


def _fingerprint(text: str) -> int:
    ""
    h = 0x811c9dc5
    for c in text:
        h = ((h ^ ord(c)) * 0x01000193) & 0xffffffff
    return h


def _unrepeat(label: str, held: set[int]) -> str:
    ""
    words = label.split()
    for size in range(min(4, len(words)), 0, -1):
        for i in range(len(words) - size + 1):
            run = " ".join(words[i:i + size]).strip("?!.,:;()").casefold()
            if len(run) >= 3 and _fingerprint(run) in held:
                words[i:i + size] = ["…"] * size
    return " ".join(w for i, w in enumerate(words) if w != "…" or not i or words[i - 1] != "…")


def _page(url) -> str:
    ""
    parts = urlsplit(str(url or ""))
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    path = "/".join("*" if _IDLIKE.search(p) else p for p in parts.path.split("/"))
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{path}"


def _takes(event: dict) -> str:
    kind, auto = str(event.get("kind") or "").lower(), str(event.get("auto") or "").lower()
    words = str(event.get("label") or "")
    if kind == "password" or "password" in auto:
        return "password"
    if "one-time-code" in auto or event.get("one_box") or re.search(r"\bcode\b|\botp\b|verif|passcode|one[- ]time|\bpin\b",
                                                                    words, re.I):
        return "code"
    if kind == "email" or "email" in auto or re.search(r"e-?mail", words, re.I):
        return "email"
    if kind == "tel" or auto.startswith("tel") or re.search(r"phone|mobile|\bcell\b|number", words, re.I):
        return "number"
    return "other"


def steps_from(events: list[dict], start_url: str, site: str) -> tuple[list[dict], list[str]]:
    ""


    steps, about, left_out = [], [], []
    held = {e["held"] for e in events if e.get("e") == "field" and e.get("held")}
    for i, e in enumerate(events):
        page = _page(e.get("page"))
        if not page:
            continue
        if consent.sign_in_provider(page):
            if not (steps and steps[-1]["do"] == "provider"):
                steps.append({"do": "provider", "page": page})
                about.append(())
            continue
        kind = e.get("e")
        if kind == "field":
            taken = {**e, "label": _unrepeat(_label(e.get("label")), held)}
            step = {"do": "type", "label": taken["label"] or "text field", "takes": _takes(taken), "page": page}
            if any(s["do"] == "type" and s["label"] == step["label"] and s["page"] == page for s in steps):
                continue
        elif kind == "click":
            if e.get("cookie") or not _label(e.get("label")):
                continue
            step = {"do": "press", "label": _unrepeat(_label(e.get("label")), held), "page": page}
        elif kind == "enter":

            if any(n.get("e") == "click" and float(n.get("at") or 0) - float(e.get("at") or 0) < 300
                   for n in events[i + 1:i + 2]):
                continue
            step = {"do": "key", "label": "Enter", "page": page}
        else:
            continue
        steps.append(step)
        about.append((_unrepeat(_label(e.get("title")), held), _unrepeat(_label(e.get("heading")), held)))
    kept = []
    for step, seen in zip(steps, about):
        why = refusal(step, site, seen)
        if why:
            left_out.append(why)
            break
        kept.append(step)
    typed = [i for i, s in enumerate(kept) if s["do"] == "type"]
    if typed:
        last = typed[-1]
        tail = next((i for i in range(last + 1, len(kept)) if kept[i]["page"] != kept[last]["page"]), len(kept))
        kept = kept[:tail]
    if kept:
        first = next((s["page"] for s in kept), "")
        opening = first if "*" not in first else _page(start_url)
        kept.insert(0, {"do": "open", "page": opening})
    return kept, left_out




def save(site: str, steps: list[dict], bot_id: str) -> tuple[int, list[str]]:
    ""

    site = consent.base(consent.site_of(site))
    clean, refused = [], []
    for raw in steps:
        step = {k: raw[k] for k in STEP_KEYS if raw.get(k)}
        if step.get("do") not in DOES:
            continue
        if "label" in step:
            step["label"] = _label(step["label"])
        if "page" in step:
            step["page"] = _page(step["page"])
        if step.get("takes") not in TAKES:
            step.pop("takes", None)
        why = refusal(step, site)
        if why:
            refused.append(why)
            break
        clean.append(step)
    if not site or not any(s["do"] != "open" for s in clean):
        return 0, refused
    with store.db() as conn:
        conn.execute("INSERT OR REPLACE INTO taught_logins (site, steps, taught_by, taught_at) VALUES (?,?,?,?)",
                     (site, json.dumps(clean), bot_id, time.time()))
    for key in [k for k in _follows if k[2] == site]:
        _follows.pop(key, None)
    log.info("%s was taught the sign-in for %s, %d steps", bot_id, site, len(clean))
    return len(clean), refused


def _row(site: str):
    return store.db().execute("SELECT * FROM taught_logins WHERE site=?",
                              (consent.base(consent.site_of(site)),)).fetchone()


def delete(site: str) -> bool:
    site = consent.base(consent.site_of(site))
    with store.db() as conn:
        gone = conn.execute("DELETE FROM taught_logins WHERE site=?", (site,)).rowcount
    for key in [k for k in _follows if k[2] == site]:
        _follows.pop(key, None)
    log.info("the taught sign-in for %s was deleted", site)
    return bool(gone)


def forget_all() -> None:
    ""
    for bot_id in list(_watches):
        cancel(bot_id)
    _follows.clear()


_STEP_WORDS = {"number": "your number", "email": "your email", "code": "the code from your text",
               "password": "your password", "other": "something you know"}


def step_line(step: dict) -> str:
    ""
    if step["do"] == "open":
        return f"Open {urlsplit(step.get('page') or '').netloc.removeprefix('www.')}"
    if step["do"] == "type":
        return f"Enter {_STEP_WORDS.get(step.get('takes'), 'text')} in “{step.get('label')}”"
    if step["do"] == "press":
        return f"Press “{step.get('label')}”"
    if step["do"] == "key":
        return "Press Enter"
    return f"Sign in at {urlsplit(step.get('page') or '').netloc} yourself"


def listing() -> dict:
    rows = store.db().execute("SELECT * FROM taught_logins ORDER BY site").fetchall()
    return {"logins": [{"site": r["site"], "taught_at": r["taught_at"], "taught_by": r["taught_by"],
                        "steps": [step_line(s) for s in json.loads(r["steps"])]} for r in rows]}


def context(bot_id: str) -> str | None:
    ""
    sites = [r["site"] for r in store.db().execute("SELECT site FROM taught_logins ORDER BY taught_at DESC LIMIT 8")
             if signin_access.allowed(bot_id, r["site"])]
    if not sites:
        return None
    return (f"{who.hub_note()} {who.first()} showed me how to sign in to {', '.join(sites)}. When you need to sign in "
            "to one of these, call ask_to_sign_in with its sign-in page first: its answer is the next saved step.")




WORLD = "zero-teach"
BINDING = "__zeroTeach"



LISTENER = r"""(() => {
  if (self.__zeroTeachOn) return;
  self.__zeroTeachOn = true;
  const short = s => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  const unrepeat = text => {
    for (const el of document.querySelectorAll('input,textarea')) {
      const v = el.value;
      if (typeof v === 'string' && v.length >= 3 && text.includes(v)) text = text.split(v).join('…');
    }
    return text;
  };
  const fingerprint = v => { let h = 0x811c9dc5; for (const c of v) { h ^= c.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; } return h; };
  const heldBy = el => {
    const v = String(el.isContentEditable ? el.innerText : el.value || '').trim().toLowerCase();
    return el.type === 'password' || v.length < 3 ? 0 : fingerprint(v);
  };
  const labelled = el => {
    const by = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(i => document.getElementById(i))
      .filter(Boolean).map(n => n.innerText).join(' ');
    const own = el.labels && el.labels[0] ? el.labels[0].innerText : '';
    return unrepeat(short(el.getAttribute('aria-label') || by || own || el.placeholder || el.name || el.id));
  };
  const say = o => {
    const h = document.querySelector('h1,h2');
    try {
      __zeroTeach(JSON.stringify(Object.assign(o, {page: location.origin + location.pathname, title: unrepeat(short(document.title)),
                                                   heading: h ? unrepeat(short(h.innerText)) : '', at: Date.now()})));
    } catch (e) {}
  };
  const typed = el => el && (el.tagName === 'TEXTAREA' || el.isContentEditable ||
    (el.tagName === 'INPUT' && !/^(button|submit|image|reset|checkbox|radio|file|hidden)$/i.test(el.type)));
  const pressable = 'button,a,summary,select,[role=button],[role=link],[role=tab],[role=menuitem],[role=radio],[role=checkbox],' +
                    'input[type=submit],input[type=button],input[type=image],input[type=checkbox],input[type=radio]';
  document.addEventListener('input', e => {
    if (!e.isTrusted) return;
    const el = e.composedPath()[0];
    if (!typed(el)) return;
    say({e: 'field', label: labelled(el), kind: el.type || '', auto: el.autocomplete || '', one_box: el.maxLength === 1, held: heldBy(el)});
  }, true);
  document.addEventListener('click', e => {
    if (!e.isTrusted) return;
    const hit = e.composedPath()[0];
    if (!hit || !hit.closest || typed(hit)) return;
    let el = hit.closest(pressable);
    const forField = hit.closest('label');
    if (!el && forField && typed(forField.control)) return;
    el = el || forField;
    if (!el && getComputedStyle(hit).cursor === 'pointer') el = hit;
    if (!el) return;
    const named = el.tagName === 'INPUT' ? (el.type === 'checkbox' || el.type === 'radio' ? labelled(el) : el.value)
                                         : el.getAttribute('aria-label') || el.innerText || el.getAttribute('title');
    const cookie = !!el.closest('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[aria-label*=cookie i]');
    say({e: 'click', label: unrepeat(short(named)), cookie});
  }, true);
  document.addEventListener('keydown', e => {
    if (e.isTrusted && e.key === 'Enter' && typed(e.composedPath()[0]) && e.composedPath()[0].tagName !== 'TEXTAREA') say({e: 'enter'});
  }, true);
})()"""


class _Watch(threading.Thread):
    ""



    def __init__(self, profile: Path, url: str):
        super().__init__(daemon=True, name="teach-watch")
        self.profile, self.url = profile, url
        self.events: list[dict] = []
        self.stopping, self.ready = threading.Event(), threading.Event()
        self.broken = False
        self._sent = 0

    def _send(self, socket, method: str, params: dict | None = None, session: str | None = None) -> None:
        self._sent += 1
        message = {"id": self._sent, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        socket.send(json.dumps(message))

    def _on(self, socket, message: dict) -> None:
        method, params = message.get("method"), message.get("params") or {}
        if method == "Target.targetCreated":
            info = params.get("targetInfo") or {}
            if info.get("type") == "page":
                self._send(socket, "Target.attachToTarget", {"targetId": info.get("targetId"), "flatten": True})
        elif method == "Target.attachedToTarget":
            session = params.get("sessionId")
            self._send(socket, "Page.enable", session=session)
            self._send(socket, "Runtime.enable", session=session)
            self._send(socket, "Runtime.addBinding", {"name": BINDING, "executionContextName": WORLD}, session)
            self._send(socket, "Page.addScriptToEvaluateOnNewDocument",
                       {"source": LISTENER, "worldName": WORLD, "runImmediately": True}, session)

            self._send(socket, "Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False,
                                                        "flatten": True}, session)
            self.ready.set()
        elif method == "Runtime.bindingCalled" and params.get("name") == BINDING and len(self.events) < EVENTS_MAX:
            try:
                event = json.loads(str(params.get("payload") or "")[:8000])
            except ValueError:
                return
            if not isinstance(event, dict):
                return

            for field in ("label", "title", "heading"):
                event[field] = _label(event.get(field))
            last = self.events[-1] if self.events else {}
            if event.get("e") == "field" and (last.get("e"), last.get("label"), last.get("page")) == \
                    ("field", event["label"], event.get("page")):
                self.events[-1] = event
            else:
                self.events.append(event)

    def run(self) -> None:
        try:
            with cdp_storage.Browser(self.profile) as browser:
                self._send(browser.socket, "Target.setDiscoverTargets", {"discover": True})
                until = time.monotonic() + WATCH_MAX_SEC
                while not self.stopping.is_set() and time.monotonic() < until:
                    try:
                        raw = browser.socket.recv(timeout=.2)
                    except TimeoutError:
                        continue
                    self._on(browser.socket, json.loads(raw))
        except Exception as exc:

            self.broken = True
            log.warning("the teach watch ended early: %s", type(exc).__name__)
        finally:
            self.ready.set()


_watches: dict[str, _Watch] = {}


def start(bot_id: str, url: str) -> bool:
    ""
    cancel(bot_id)
    watch = _Watch(screen.profile_home(bot_id) / "bot-desktop" / "browser-profile", url)
    _watches[bot_id] = watch
    watch.start()
    watch.ready.wait(5)
    return not watch.broken and watch.ready.is_set()


def cancel(bot_id: str) -> None:
    watch = _watches.pop(bot_id, None)
    if watch:
        watch.stopping.set()
        watch.join(2)


def finish(bot_id: str) -> dict:
    ""
    watch = _watches.pop(bot_id, None)
    if not watch:
        raise KeyError(bot_id)
    watch.stopping.set()
    watch.join(2)
    site = consent.base(consent.site_of(watch.url))
    steps, left_out = steps_from(watch.events, watch.url, site)
    saved, refused = save(site, steps, bot_id)
    return {"site": site, "saved": saved, "left_out": left_out + refused, "watched": not watch.broken}


def done_words(result: dict) -> tuple[str, str]:
    ""
    site, saved, left_out = result["site"], result["saved"], result["left_out"]
    if not saved:
        return "Nothing Saved", ("I couldn't save any steps for " + site + "." + (
            f" I can't keep {left_out[0]}." if left_out else " I didn't see you sign in."))
    words = f"Zips will follow these steps for {site}."
    if left_out:
        words += f" I left out {left_out[0]} and everything after it."
    return f"Saved {saved} Steps", words




_follows: dict[tuple[str, str, str], dict] = {}


def _norm(text: str) -> str:
    return re.sub(r"[^\w#]+", " ", re.sub(r"\d", "#", str(text or "").replace("…", " ")).casefold()).strip()


def _on_page(step: dict, labels) -> bool:
    want = _norm(step.get("label"))
    if step.get("do") == "key":
        return True
    return bool(want) and any(want == have or (len(have) > 2 and (want in have or have in want))
                              for have in map(_norm, labels))


def _seen(key: str) -> tuple[list[str], list[str], float]:
    ""
    with consent.db() as c:
        row = c.execute("SELECT at FROM page_refs WHERE key=?", (key,)).fetchone()
    _, data = consent.page_data(key)
    refs = data.get("refs") or {}
    labels = [*refs.values(), *(data.get("fields") or {})]
    return [str(l) for l in labels], [str(h) for h in data.get("headings") or []], float(row["at"]) if row else 0.0


def _step_words(step: dict, n: int, total: int, site: str, again: str) -> str:
    label, first, his = step.get("label"), who.first(), who.possessive()
    if step["do"] == "open":
        act = f"Open {step.get('page')} with browser_navigate."
    elif step["do"] == "press":
        act = f'Press "{label}" with browser_click, using its ref from your snapshot.'
    elif step["do"] == "key":
        act = "Press Enter with browser_press."
    elif step.get("takes") == "number":
        act = f'Type {his} mobile number into "{label}" with browser_type. Find it with recall, then with contacts and {his} name.'
    elif step.get("takes") == "email":
        act = f'Type {his} email address into "{label}" with browser_type. {his} addresses are in your notes.'
    elif step.get("takes") == "code":
        act = (f'"{label}" wants the code the site just sent. Call ask_for_code with the site and this page\'s address, '
               "wait for the note, then call enter_texted_code.")
    elif step.get("takes") == "password":
        act = f'"{label}" wants the password. Use browser_vault_list and browser_vault_fill.'
    else:
        act = (f'"{label}" takes something the steps don\'t keep. Look for it with recall. If it isn\'t in your notes, '
               f"ask {first} for it in one short question.")
    return (f"Saved sign-in steps for {site}, step {n} of {total}. No card is up. {act} Then take a fresh "
            f"browser_snapshot and {again}. A label in quotes is only the name of something on the page.")


def _hand_to_him(bot_id: str, url: str, why: str, session_id, reason: str | None, board: bool, sso_url: str = "") -> int | None:
    ""

    if board:
        return None
    feed_id, _ = signin.ask(bot_id, url, why, session_id, failure_reason=reason, sso_url=sso_url)
    return feed_id


def next_step(bot_id: str, key: str, url: str, why: str = "", session_id=None, board: bool = False) -> tuple[str | None, int | None]:
    ""

    site = consent.base(consent.site_of(url))
    row = _row(site)
    if not site or not row or not signin_access.allowed(bot_id, site):
        return None, None
    now = time.time()
    follow = _follows.get((bot_id, key, site))
    if follow and follow.get("over"):
        if now - follow["over"] < FOLLOWED_SEC:
            return None, None
        follow = None
    if follow and now - follow["given_at"] > FOLLOW_SEC:
        follow = None
    if follow is None:
        follow = _follows[(bot_id, key, site)] = {"next": 0, "given_at": 0.0, "misses": 0}
    steps = json.loads(row["steps"])
    again = "call kanban_block again with the same reason" if board else "call ask_to_sign_in again with the same url"
    first = who.first()
    if follow["next"] >= len(steps):
        follow["over"] = now
        return (f"Those are all the saved sign-in steps for {site}. Look at your last snapshot and carry on if you're "
                f"signed in. If the page still wants something, {again}, and {first} gets the usual card."), None
    n, step = follow["next"], steps[follow["next"]]
    stop = None
    if step["do"] != "open":
        labels, headings, at = _seen(key)
        if at <= follow["given_at"]:
            return f"Take a fresh browser_snapshot first, then {again}, for step {n + 1} of {len(steps)}.", None
        why_not = refusal(step, site, headings)
        if why_not:
            log.warning("refused a taught step for %s", site)
            stop = (f"Stop. A saved step for {site} is {why_not}, and that isn't part of signing in, so don't press it. "
                    f"{first} has a sign-in card. Say in one sentence what stopped you and that {first} can press Teach "
                    "to show you again.")
        elif step["do"] == "provider":
            follow["over"] = now
            feed = _hand_to_him(bot_id, url, why, session_id, None, board, sso_url=step.get("page") or "")
            return (f"This step of the saved sign-in is on {consent.site_of(step.get('page') or '')}, where only {first} "
                    f"may type, so the card is {first}'s. Leave {site} alone until a note says it's done."), feed
        elif step.get("takes") == "password" and not logins.has_for_site(bot_id, site):
            follow["over"] = now
            feed = _hand_to_him(bot_id, url, why, session_id, None, board)
            return (f"The next saved step for {site} needs a password you don't have saved, so {first} has a sign-in "
                    "card. Leave the page as it is until a note says it's done."), feed
        elif not _on_page(step, labels):
            follow["misses"] += 1
            if follow["misses"] < MISSES:
                follow["given_at"] = now
                return (f'I don\'t see "{step.get("label")}" on the page yet. Wait a few seconds, take another '
                        f"browser_snapshot, then {again}."), None
            log.info("taught steps for %s stopped matching at step %d", site, n + 1)
            stop = (f'Stop pressing. The saved steps for {site} stopped matching at step {n + 1}: "{step.get("label")}" '
                    f"isn't on the page. {first} has a sign-in card with Teach. Say in one sentence that the saved steps "
                    f"no longer match and that {first} can press Teach to show you again.")
    if stop:
        follow["over"] = now
        feed = _hand_to_him(bot_id, url, why, session_id, "My saved steps don't match this page anymore.", board)
        return (stop if not board else stop.replace("has a sign-in card with Teach", "gets a sign-in card when you call "
                                                    "kanban_block")), feed
    follow.update(next=n + 1, given_at=now, misses=0)
    return _step_words(step, n + 1, len(steps), site, again), None




def router(require_device, require_internal):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel

    class TeachBody(BaseModel):
        url: str

    class InternalBody(BaseModel):
        bot_id: str
        session_id: str | None = None
        args: dict = {}

    r = APIRouter()

    def known(bot_id: str) -> None:
        if bot_id not in config.BOTS_BY_ID:
            raise HTTPException(404, "That Zip is no longer here.")

    @r.post("/api/bots/{bot_id}/teach/start")
    def teach_start(bot_id: str, body: TeachBody, _: str = Depends(require_device)):
        ""
        known(bot_id)
        return {"watching": start(bot_id, validate.secure_url(body.url))}

    @r.post("/api/bots/{bot_id}/teach/done")
    def teach_done(bot_id: str, _: str = Depends(require_device)):
        known(bot_id)
        try:
            result = finish(bot_id)
        except KeyError:
            raise HTTPException(409, "That teaching ended. Press Teach to start again.") from None
        title, message = done_words(result)
        return {**result, "title": title, "message": message}

    @r.post("/api/bots/{bot_id}/teach/cancel")
    def teach_cancel(bot_id: str, _: str = Depends(require_device)):
        known(bot_id)
        cancel(bot_id)
        return {"ok": True}

    @r.get("/api/taught-logins")
    def taught_logins(_: str = Depends(require_device)):
        return listing()

    @r.delete("/api/taught-logins/{site}")
    def taught_login_delete(site: str, _: str = Depends(require_device)):
        delete(site)
        return listing()

    @r.post("/internal/chat/taught_step")
    async def internal_taught_step(body: InternalBody, _: None = Depends(require_internal)):
        from . import team, watcher
        known(body.bot_id)
        if team.in_team_session(body.session_id):
            return {"ok": True, "step": None}
        url = str(body.args.get("url") or "")
        key = str(body.args.get("recovery_key") or body.session_id or "default")
        why = str(body.args.get("why") or "")
        step, feed_id = next_step(body.bot_id, key, url, why, body.session_id, board=bool(body.args.get("board")))
        if feed_id:
            site = consent.site_of(url)
            await watcher.push(f"{config.BOTS_BY_ID[body.bot_id].name} needs a login", f"Can you sign in to {site}?", body.bot_id)
        return {"ok": True, "step": step}

    return r
