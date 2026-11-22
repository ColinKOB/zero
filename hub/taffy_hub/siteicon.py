""









import contextlib
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from . import config

DIR = config.HUB_DIR / "site-icons"
KEEP_SEC = 30 * 86400
MISS_SEC = 86400
MAX_BYTES = 512 * 1024
PAGE_BYTES = 400_000
TIMEOUT_SEC = 8
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"

_HOST = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_TYPES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif", b"RIFF": "image/webp",
          b"\x00\x00\x01\x00": "image/x-icon"}


def clean_host(host: str) -> str | None:
    h = str(host or "").strip().lower().rstrip(".")
    return h if _HOST.match(h) else None


def _vetted(host: str) -> str | None:
    ""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    ips = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    return str(ips[0]) if ips and all(ip.is_global for ip in ips) else None


@contextlib.contextmanager
def _open(url: str, host: str, ip: str):
    ""


    parts = urlsplit(url)
    netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{parts.port}" if parts.port else "")
    target = urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
    with httpx.Client(timeout=TIMEOUT_SEC, follow_redirects=False) as client:
        req = client.build_request("GET", target, headers={"User-Agent": UA, "Host": parts.netloc},
                                   extensions={"sni_hostname": host})
        r = client.send(req, stream=True)
        try:
            yield r
        finally:
            r.close()


def _get(url: str, limit: int) -> tuple[int, bytes, str] | None:
    ""


    for _ in range(4):
        parts = urlsplit(url)
        host = clean_host(parts.hostname or "")
        ip = _vetted(host) if parts.scheme == "https" and host else None
        if not ip:
            return None
        try:
            with _open(url, host, ip) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > limit:
                        break
                return r.status_code, bytes(body[:limit + 1]), url
        except httpx.HTTPError:
            return None
    return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k: (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        if not a.get("href") or not ({"icon", "apple-touch-icon", "apple-touch-icon-precomposed"} & set(rel)):
            return
        size = max((int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))), default=0)
        score = (1000 if "apple-touch-icon" in " ".join(rel) else 0) + min(size, 512)
        if a["href"].lower().endswith(".svg"):
            score -= 2000
        self.found.append((score, a["href"]))


def _image(got: tuple[int, bytes, str] | None) -> tuple[bytes, str] | None:
    if got is None or got[0] != 200 or len(got[1]) > MAX_BYTES:
        return None
    kind = next((t for magic, t in _TYPES.items() if got[1].startswith(magic)), None)
    return (got[1], kind) if kind else None


def _fetch(host: str) -> tuple[bytes, str] | None:
    base = f"https://{host}/"
    page = _get(base, PAGE_BYTES) or _get(f"https://www.{host}/", PAGE_BYTES)
    if page is not None and page[0] == 200:
        p = _Links()
        try:
            p.feed(page[1].decode("utf-8", "replace"))
        except Exception:
            pass
        for _, href in sorted(p.found, reverse=True)[:4]:
            got = _image(_get(urljoin(page[2], href), MAX_BYTES))
            if got:
                return got
    for path in ("/apple-touch-icon.png", "/favicon.ico"):
        got = _image(_get(urljoin(base, path), MAX_BYTES))
        if got:
            return got
    return None


def icon(host: str) -> tuple[bytes, str] | None:
    ""
    h = clean_host(host)
    if not h:
        return None
    DIR.mkdir(parents=True, exist_ok=True)
    data, miss = DIR / h, DIR / f"{h}.none"
    now = time.time()
    if data.exists() and now - data.stat().st_mtime < KEEP_SEC:
        raw = data.read_bytes()
        kind = next((t for magic, t in _TYPES.items() if raw.startswith(magic)), None)
        if kind:
            return raw, kind
    if miss.exists() and now - miss.stat().st_mtime < MISS_SEC:
        return None
    got = _fetch(h)
    if got:
        data.write_bytes(got[0])
        miss.unlink(missing_ok=True)
    else:
        miss.touch()
    return got
