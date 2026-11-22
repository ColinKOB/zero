""

import json
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx
from websockets.sync.client import connect

from . import consent


class Unavailable(RuntimeError):
    pass


class NotReady(Unavailable):
    ""


def web_origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = parsed.port
    suffix = f":{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else ""
    return f"{parsed.scheme}://{host}{suffix}"


class Browser:
    def __init__(self, profile):
        try:
            lines = (profile / "DevToolsActivePort").read_text().splitlines()
            port = int(lines[0])
            if not 0 < port < 65536 or not lines[1].startswith("/devtools/browser/"):
                raise Unavailable("The browser is not ready.")
            with httpx.Client(trust_env=False, timeout=2) as client:
                info = client.get(f"http://127.0.0.1:{port}/json/version").json()
            endpoint = info.get("webSocketDebuggerUrl", "")
            parsed = urlsplit(endpoint)
            if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port != port or parsed.path != lines[1]:
                raise Unavailable("The browser is not ready.")
            self.socket = connect(endpoint, open_timeout=2, close_timeout=1, max_size=16 * 1024 * 1024, proxy=None)
            self.counter = 0
        except Exception:
            raise NotReady("The browser is not ready.") from None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.socket.close()

    def call(self, method, params=None, session=None):
        self.counter += 1
        message = {"id": self.counter, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.socket.send(json.dumps(message))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            event = json.loads(self.socket.recv(timeout=max(.01, deadline - time.monotonic())))
            if event.get("method") == "Fetch.requestPaused":
                self.counter += 1
                self.socket.send(json.dumps({"id": self.counter, "method": "Fetch.fulfillRequest",
                    "sessionId": event["sessionId"], "params": {"requestId": event["params"]["requestId"],
                    "responseCode": 200, "responseHeaders": [{"name": "Content-Type", "value": "text/html"}],
                    "body": "PCFkb2N0eXBlIGh0bWw+"}}))
            if event.get("id") == message["id"]:
                if "error" in event:
                    error = event["error"]
                    raise Unavailable(f"{method} failed: {error.get('code')} {error.get('message')}")
                return event.get("result") or {}
        raise Unavailable(f"{method} got no answer in time.")

    def cookies(self):
        return self.call("Storage.getCookies").get("cookies", [])

    def origins(self, site):
        targets = self.call("Target.getTargets").get("targetInfos", [])
        return {web_origin(t.get("url", "")) for t in targets if t.get("type") == "page"
                for p in [urlsplit(t.get("url", ""))] if p.scheme in ("http", "https")
                and consent.base(p.hostname or "") == site}

    @contextmanager
    def _page(self):
        ""
        target = self.call("Target.createTarget", {"url": "about:blank", "background": True})["targetId"]
        try:
            yield self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        finally:
            self.call("Target.closeTarget", {"targetId": target})

    def local_storage(self, origin, values=None):


        with self._page() as session:
            self.call("Page.enable", session=session)
            self.call("Network.setBypassServiceWorker", {"bypass": True}, session)
            self.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, session)
            self.call("Page.navigate", {"url": origin + "/"}, session)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = self.call("Runtime.evaluate", {"expression": "location.origin", "returnByValue": True}, session)
                if result.get("result", {}).get("value") == origin:
                    break
                time.sleep(.02)
            else:
                raise Unavailable("The browser could not open its saved sign-in.")
            expression = "Object.fromEntries(Object.entries(localStorage))"
            if values is not None:
                expression = "localStorage.clear(); for (const [k,v] of Object.entries(" + json.dumps(values) + ")) localStorage.setItem(k,v); true"
            out = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session)
            if out.get("exceptionDetails"):
                raise Unavailable("The browser could not transfer the sign-in.")
            return out.get("result", {}).get("value") or {}

    def site_cookies(self, site):
        return [c for c in self.cookies() if consent.base(c.get("domain", "").lstrip(".")) == site]

    def page_storage(self, origin):
        ""
        for target in self.call("Target.getTargets").get("targetInfos", []):
            if target.get("type") != "page" or web_origin(target.get("url", "")) != origin:
                continue
            session = self.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})["sessionId"]
            try:
                out = self.call("Runtime.evaluate", {"expression": "[location.origin, Object.fromEntries("
                                                     "Object.entries(localStorage))]", "returnByValue": True}, session)
            finally:
                self.call("Target.detachFromTarget", {"sessionId": session})
            if out.get("exceptionDetails"):
                raise Unavailable("The browser could not read the sign-in.")
            found = out.get("result", {}).get("value") or []

            if len(found) != 2 or found[0] != origin:
                return None
            return found[1] or {}
        return None

    def snapshot(self, site, known_origins=(), saved=None):
        ""




        cookies = self.site_cookies(site)
        open_now = self.origins(site)
        values = {}
        for origin in sorted(open_now | set(known_origins)):
            current = self.page_storage(origin) if origin in open_now else None
            if current is None:
                kept = origin not in open_now and saved is not None and origin in saved
                current = saved[origin] if kept else self.local_storage(origin)
            values[origin] = current
        return {"cookies": cookies, "origins": values}

    def restore(self, site, snapshot):
        wanted = snapshot.get("cookies", [])
        identities = {(c["name"], c["domain"], c["path"], json.dumps(c.get("partitionKey"), sort_keys=True)) for c in wanted}
        removed = []
        for c in self.cookies():
            identity = (c["name"], c["domain"], c["path"], json.dumps(c.get("partitionKey"), sort_keys=True))
            if consent.base(c["domain"].lstrip(".")) == site and identity not in identities:
                removed.append({**c, "value": "", "expires": 1})
        fields = {"name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "expires", "priority",
                  "sameParty", "sourceScheme", "sourcePort", "partitionKey"}
        params = [{k: v for k, v in c.items() if k in fields and not (k == "expires" and v <= 0)} for c in wanted + removed]
        for cookie in params:
            if not cookie["domain"].startswith("."):
                domain = cookie.pop("domain")
                scheme = "https" if cookie.get("secure") else "http"
                cookie["url"] = f"{scheme}://{domain}{cookie.get('path', '/')}"
        if params:
            self.call("Storage.setCookies", {"cookies": params})
        for origin, values in snapshot.get("origins", {}).items():
            self.local_storage(origin, values)

    def clear_site(self, site, known_origins=()):
        ""
        origins = self.origins(site) | set(known_origins)

        for target in self.call("Target.getTargets").get("targetInfos", []):
            if target.get("type") == "page" and consent.base(consent.site_of(target.get("url", ""))) == site:
                self.call("Target.closeTarget", {"targetId": target["targetId"]})



        with self._page() as session:
            for origin in sorted(origins):
                self.call("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"}, session)
            for c in self.site_cookies(site):
                self.call("Network.deleteCookies",
                          {k: c[k] for k in ("name", "domain", "path", "partitionKey") if k in c}, session)
        if left := self.site_cookies(site):
            raise Unavailable(f"Storage.getCookies still lists {len(left)} cookies for {site} after clearing.")
