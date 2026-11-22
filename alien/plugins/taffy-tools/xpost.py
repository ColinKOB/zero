""
















import json
import re
import time
from urllib.parse import urlencode, urlsplit

from . import consent, gate, home, mac, tiers

X_SITES = {"x.com", "twitter.com"}
MAX_CHARS = tiers.X_MAX_CHARS
WAIT_SEC = 20

SCHEMA = {
    "name": "post_to_x",
    "description": (
        "Post on X (x.com), or reply to a post. This is the only way to post, reply or quote on X: the site's own "
        "Post and Reply buttons are off. Colin gets a card showing the exact text, and it posts only after he taps "
        "Allow, so write the final text first. When he approves, make exactly the same call again, unchanged. A "
        "post he approved on its own team card goes out at its time with no new card. To quote a post, put its "
        "link in the text. If you have X API keys this posts through X's API, with no browser and no sign-in. So "
        "don't ask him to sign in to X first: make the call, and ask with ask_to_sign_in "
        "(https://x.com/i/flow/login) only if its result tells you to. A result that starts \"Not posted\" means "
        "nothing went out, so tell him that sentence. Any other failure may have gone out, so never try it again."
    ),
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "The post, exactly as it should appear."},
        "reply_to": {"type": "string", "description": "Optional link of the post to reply to, like "
                     "https://x.com/name/status/123."},
    }, "required": ["text"]},
}

_STATUS = re.compile(r"^/[A-Za-z0-9_]{1,15}/status/(\d{1,25})(?:/|$)|^/i/web/status/(\d{1,25})(?:/|$)")


class Refused(Exception):
    pass


def is_x(site: str) -> bool:
    return consent.base(consent.site_of(site) or site or "") in X_SITES


def text_of(args: dict) -> str:
    text = str((args or {}).get("text") or "")
    if not text.strip():
        raise Refused("Not posted: give the post's text.")
    if len(text) > MAX_CHARS:
        raise Refused(home.personalize(tiers.X_TOO_LONG))
    return text


def reply_id(args: dict) -> str:
    ""
    link = str((args or {}).get("reply_to") or "").strip()
    if not link:
        return ""
    parts = urlsplit(link if "://" in link else "https://" + link)
    m = _STATUS.match(parts.path or "")
    if parts.scheme not in ("http", "https") or not is_x(parts.hostname or "") or not m:
        raise Refused("Not posted: reply_to must be a post's link on X, like https://x.com/name/status/123.")
    return m.group(1) or m.group(2)


def compose_url(text: str, in_reply_to: str = "") -> str:
    query = {"text": text}
    if in_reply_to:
        query["in_reply_to"] = in_reply_to
    return "https://x.com/intent/post?" + urlencode(query)


def same_text(shown, text: str) -> bool:
    ""
    return isinstance(shown, str) and " ".join(shown.split()) == " ".join(text.split())



STATE_JS = """(() => {
  const box = document.querySelector('[data-testid="tweetTextarea_0"]');
  const btn = document.querySelector('[data-testid="tweetButton"]');
  const toast = document.querySelector('[data-testid="toast"]');
  const link = toast && toast.querySelector('a[href*="/status/"]');
  return JSON.stringify({host: location.hostname, path: location.pathname,
    text: box ? box.innerText : null, button: !!btn,
    enabled: !!btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true',
    toast: toast ? toast.innerText.slice(0, 200) : '', link: link ? link.href : ''});
})()"""
POST_BUTTON = '[data-testid="tweetButton"]'


class Browser:
    ""



    def __init__(self, task_id: str | None, open_page):
        self.task_id = task_id or "default"
        self.open_page = open_page

    def _run(self, command: str, args: list, timeout: int = 15) -> dict:
        from tools import browser_tool, browser_tool_session
        key = browser_tool._last_session_key(self.task_id)
        res = browser_tool_session._run_browser_command(key, command, args, timeout=timeout) or {}
        if not res.get("success"):
            raise Refused("Not posted: your browser did not answer (" + str(res.get("error") or "no reply")[:160] + ").")
        return res.get("data") or {}

    def open(self, url: str) -> None:
        out = self.open_page(url)
        try:
            data = json.loads(out) if isinstance(out, str) else out
        except ValueError:
            data = {}
        if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
            raise Refused("Not posted: X's post page would not open (" + str(data.get("error") or "")[:160] + ").")

    def state(self) -> dict:
        value = self._run("eval", [STATE_JS]).get("result")
        try:
            value = json.loads(value) if isinstance(value, str) else value
        except ValueError:
            value = None
        return value if isinstance(value, dict) else {}

    def press_post(self) -> None:
        try:
            self._run("click", [POST_BUTTON])
        except Refused:

            raise Refused("Pressing Post did not finish. It may or may not have gone out, so I did not press it "
                          "again. Open your profile on X to check.") from None


def _wait(browser, ready, sleep, clock) -> dict:
    deadline = clock() + WAIT_SEC
    state = {}
    while True:
        try:
            state = browser.state()
        except Refused:
            state = {}
        if ready(state) or clock() >= deadline:
            return state
        sleep(0.5)


def _signed_out(state: dict) -> bool:
    path = str(state.get("path") or "")
    return path.startswith(("/i/flow/login", "/login", "/i/flow/signup")) or (
        bool(state.get("host")) and not is_x(str(state.get("host"))))


SIGN_IN = ("Not posted: your browser is not signed in to X. Ask Colin to sign in with ask_to_sign_in "
           "(https://x.com/i/flow/login), then post again; it will need his yes again.")


API_WAIT_SEC = 60
HUB_DOWN = ("Not posted: Zero couldn't start the post just now. Try again in a minute; it will need the same yes.")
HUB_UNSURE = ("X did not confirm the post. It may or may not have gone out, so I did not send it again. "
                            "Check the profile on X and tell Colin what you find.")


def has_api_keys() -> bool:
    ""
    folder = gate._hub_state() / "service-keys"
    return (folder / gate._bot() / "x.json").is_file() or (folder / "_shared" / "x.json").is_file()


def by_api(args: dict, session_id: str = "", test: bool | None = None, call=mac._call) -> dict:
    ""
    text, in_reply_to = text_of(args), reply_id(args)
    test = consent.test_mode() if test is None else test
    out = call("post", {"text": text, "reply_id": in_reply_to, "test": test}, API_WAIT_SEC, session_id=session_id,
               area="x")
    if out.get("ok"):
        return out["result"]
    error = str(out.get("error") or "")
    if error.startswith(("Not posted", "This exact post", "X did not confirm")):
        raise Refused(error)

    if "Connection refused" in error or "not set up" in error or re.search(r"refused that \(4\d\d\)", error):
        raise Refused(HUB_DOWN)
    raise Refused(home.personalize(HUB_UNSURE))


def post(args: dict, browser, sleep=time.sleep, clock=time.monotonic, test: bool | None = None) -> dict:
    ""
    text, in_reply_to = text_of(args), reply_id(args)
    browser.open(compose_url(text, in_reply_to))
    state = _wait(browser, lambda s: _signed_out(s) or (s.get("button") and s.get("text") is not None), sleep, clock)
    if _signed_out(state):
        raise Refused(home.personalize(SIGN_IN))
    if not state.get("button") or state.get("text") is None:
        raise Refused(home.personalize("Not posted: X's post box did not appear. Take a browser_snapshot of x.com "
                                       "and tell Colin what it shows."))
    if not same_text(state.get("text"), text):
        raise Refused("Not posted: X's post box does not hold exactly the approved text, so I left it unsent.")
    if not state.get("enabled"):
        raise Refused(home.personalize("Not posted: X won't take this post as it is (it may be too long for the "
                                       "account). Tell Colin what X shows."))
    if consent.test_mode() if test is None else test:
        return {"result": "Test mode: X's post box holds the approved text and Post is ready. Nothing was posted."}
    browser.press_post()
    after = _wait(browser, lambda s: bool(s.get("link")) or (bool(s.get("host")) and s.get("text") is None)
                  or bool(s.get("toast")), sleep, clock)
    if after.get("link"):
        return {"result": "Posted on X.", "url": after["link"]}
    if after.get("host") and after.get("text") is None and not _signed_out(after):
        return {"result": "Posted on X. Open your profile on X to find its link."}
    if after.get("toast"):
        raise Refused(home.say("Not posted: X said \"{toast}\". I did not press Post again. Tell Colin.",
                               toast=str(after["toast"])[:200]))
    raise Refused(home.personalize(
        "X did not confirm the post. It may or may not have gone out, so I did not press Post again. Open your "
        "profile on X to check, and tell Colin what you find."))


def handler(dispatch):
    ""

    def post_to_x(args: dict, **kw) -> str:
        forward = {k: v for k, v in kw.items() if k in ("task_id", "session_id")}
        browser = Browser(kw.get("task_id"), lambda url: dispatch("browser_navigate", {"url": url}, **forward))
        try:
            if has_api_keys():
                return json.dumps(by_api(args or {}, kw.get("session_id") or ""), ensure_ascii=False)
            return json.dumps(post(args or {}, browser), ensure_ascii=False)
        except Refused as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"post_to_x failed ({type(e).__name__}). It may not have posted; check "
                                        "your profile on X before trying again."})
    return post_to_x
