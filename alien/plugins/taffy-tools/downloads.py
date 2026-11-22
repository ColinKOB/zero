""

























from __future__ import annotations

import codecs
import email.message
import hashlib
import html
import html.parser
import http.client
import http.cookiejar
import io
import json
import logging
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote, urljoin, urlsplit

from . import consent, home, safe_files, sandbox

log = logging.getLogger("taffy.downloads")

MAX_BYTES = 25 * 1024 * 1024
MAX_HOPS = 5
TIMEOUT_SEC = 20
DOWNLOAD_SEC = 120
TEXT_MAX = 30_000
DOCX_XML_MAX = 40 * 1024 * 1024
PDF_SEC = 30
PDF_TEXT_MAX = 5 * 1024 * 1024
FOLDER = "Downloads"
_SAFE = re.compile(r"[^A-Za-z0-9 ._()-]+")
_BOT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REDIRECTS = {301, 302, 303, 307, 308}
_URL_SAFE = "/%:@!$&'()*+,;=-._~"


EXTENSIONS = {".docx": "docx", ".pdf": "pdf", ".html": "html", ".htm": "html", ".txt": "text", ".md": "text",
              ".markdown": "text", ".csv": "text", ".json": "text", ".text": "text"}
TYPES = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
         "application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt", "text/markdown": ".md",
         "text/csv": ".csv", "application/json": ".json"}

FILE_ENDINGS = {".docx", ".doc", ".pdf", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".rtf", ".zip", ".txt", ".md",
                ".csv", ".png", ".jpg", ".jpeg", ".mp3", ".mp4", ".m4a"}

SCHEMA = {
    "name": "browser_download",
    "description": (
        "Download a file from a link into your work folder and read its text. Use it for any link that is a file "
        "rather than a page: a .docx, .pdf, .txt, .md or .html file, or anything else a site offers to download. "
        "Reach for it as soon as browser_navigate to a link fails with net::ERR_ABORTED, or clicking a file link "
        "opens nothing; that is the browser starting a download, and this tool is how you get the file. It uses your "
        "browser's sign-in for that site, so it gets files from sites you are signed in to, like submitted drafts on "
        "a course site. https links only, up to 25 MB. Returns where the file was saved and its text (for "
        ".docx, .pdf, .txt, .md and .html); open other kinds from the saved path with your own tools."),
    "parameters": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The file's full https link, as the page gives it."}},
        "required": ["url"],
    },
}

ABORTED_HINT = ("That link is probably a file download, which the browser does not open as a page. Call "
                "browser_download with url \"{url}\" to save the file in your work folder and read its text.")


TOO_SLOW = "Not downloaded: the site sent the file too slowly. Try once more now, or get it another way."


class Refused(Exception):
    ""


class Partial(Exception):
    ""




@dataclass(frozen=True)
class Fetched:
    url: str
    content_type: str
    disposition: str
    body: bytes
    redirected: bool = False
    charset: str = ""


@dataclass(frozen=True)
class Saved:
    path: str
    size: int
    kind: str
    text: str
    full_text: str | None
    reused: bool
    source: str
    whole: bool = True


@dataclass
class Network:
    ""
    resolve: Callable[[str, int], list[str]] = field(default=lambda host, port: _addresses(host, port))
    context: ssl.SSLContext | None = None
    is_local: Callable[[str], bool] = field(default=lambda ip: _local(ip))


def _addresses(host: str, port: int) -> list[str]:
    ""
    try:
        infos = sandbox._pool.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM).result(timeout=5)
    except Exception:
        return []
    return list(dict.fromkeys(i[4][0] for i in infos))


def _local(ip: str) -> bool:
    ""
    a = sandbox._ip(ip.split("%")[0])
    return a is None or sandbox._local_ip(a)




class _Pinned(http.client.HTTPSConnection):
    ""

    def __init__(self, host: str, port: int, ip: str, context: ssl.SSLContext, timeout: float):
        super().__init__(host, port, context=context, timeout=timeout)
        self._ip = ip

    def shut(self):
        try:
            if self.sock is not None:
                self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _company(host: str) -> str:
    ""


    host = host.rstrip(".").lower().removeprefix("www.")
    return consent.base(host) if consent._suffix_rules()[0] else host


def _hop(url: str, company: str, net: Network) -> tuple[str, int, str, list[str]]:
    ""
    try:
        parts = urlsplit(url.strip())
        port = parts.port or 443
    except ValueError:
        raise Refused("That link could not be read. Copy the file's full https link from the page.") from None
    if parts.scheme.lower() != "https":

        raise Refused("Not downloaded: browser_download takes only https links, so the file and your sign-in stay "
                      "encrypted on the way.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or parts.username or parts.password:
        raise Refused("That link has no plain site name in it. Copy the file's full https link from the page.")
    literal = sandbox._ip(host)
    if literal is None and ("." not in host or host in sandbox._LOOPBACK_NAMES or host.endswith(".localhost")
                            or sandbox._LOCAL_NAMES.search(host) or re.search(r"(^|\.)\d+$", host)):
        ips = []
    else:
        ips = [str(literal)] if literal is not None else net.resolve(host, port)
        if not ips:
            raise Refused("Not downloaded: the site could not be found. Check the link.")
    if not ips or any(net.is_local(ip) for ip in ips):
        raise Refused(home.say("Not downloaded: the link leads to this home's own computer or network, and downloads "
                               "come only from public sites."))

    target = quote(parts.path or "/", safe=_URL_SAFE) + (f"?{quote(parts.query, safe=_URL_SAFE + '?')}"
                                                          if parts.query else "")
    return host, port, target, ips


def _get(host, port, ips, target, headers, context, live, deadline):
    ""

    for n, ip in enumerate(ips):
        if time.monotonic() >= deadline:
            raise Refused(TOO_SLOW)


        conn = _Pinned(host, port, ip, context, min(TIMEOUT_SEC, max(0.1, deadline - time.monotonic())))
        live[:] = [conn]
        try:
            conn.request("GET", target, headers=headers)
            return conn, conn.getresponse()
        except ssl.SSLError:
            conn.close()
            raise Refused("Not downloaded: the site's security certificate did not check out.") from None
        except (OSError, http.client.HTTPException):
            conn.close()
            if n == len(ips) - 1:
                raise Refused("Not downloaded: the site could not be reached. Try again in a minute.") from None
    raise Refused("Not downloaded: the site could not be reached. Try again in a minute.")


def fetch(url: str, cookies_for: Callable[[str], list[dict]], user_agent: str = "", net: Network | None = None) -> Fetched:
    ""
    net = net or Network()
    context = net.context or ssl.create_default_context()
    try:
        company = _company(urlsplit(url.strip()).hostname or "")
    except ValueError:
        company = ""
    deadline = time.monotonic() + DOWNLOAD_SEC
    live: list[_Pinned] = []


    timer = threading.Timer(DOWNLOAD_SEC, lambda: [c.shut() for c in live])
    timer.daemon = True
    timer.start()
    try:
        return _fetch(url, company, cookies_for, user_agent, net, context, deadline, live)
    except Refused:
        if time.monotonic() >= deadline:
            raise Refused(TOO_SLOW) from None
        raise
    finally:
        timer.cancel()


def _set_cookies(resp, url: str) -> set[tuple[str, str, str]]:
    ""


    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    here = (parts.path or "/").rsplit("/", 1)[0] or "/"
    out = set()
    for attrs in http.cookiejar.parse_ns_headers(resp.headers.get_all("Set-Cookie") or []):
        if attrs:
            rest = {k.lower(): v for k, v in attrs[1:]}
            domain = (rest.get("domain") or "").lower().lstrip(".")
            if domain and host != domain and not host.endswith("." + domain):
                continue
            path = rest.get("path") or ""
            if not _browser_accepts(attrs[0][0], attrs[0][1], "secure" in rest, domain, path,
                                    str(rest.get("samesite") or ""), "partitioned" in rest, "httponly" in rest):
                continue
            out.add(_key(attrs[0][0], "." + domain.lstrip(".") if domain else host, path if path.startswith("/") else here))
    return out


COOKIE_MAX = 4096


def _browser_accepts(name: str, value: str | None, secure: bool, domain: str, path: str, samesite: str,
                     partitioned: bool, httponly: bool) -> bool:
    ""




    has_domain = bool(domain)
    if len(name) + len(value or "") > COOKIE_MAX:
        return False
    if has_domain and consent.is_public_suffix(domain):
        return False
    if (samesite.strip().lower() == "none" or partitioned) and not secure:
        return False
    low = name.lower()
    if low.startswith(("__http-", "__host-http-")) and not httponly:
        return False
    if low.startswith("__host-"):
        return secure and not has_domain and path == "/"
    if low.startswith("__secure-"):
        return secure
    return True


def _in_partition(c: dict, host: str) -> bool:
    ""


    key = c.get("partitionKey")
    if not key:
        return True
    if isinstance(key, dict):
        if key.get("hasCrossSiteAncestor"):
            return False
        key = key.get("topLevelSite") or ""
    return str(key).rstrip("/").lower() == f"https://{_company(host)}"


def _key(name: str, domain: str, path: str) -> tuple[str, str, str]:
    ""


    return name, domain.lower().lstrip("."), path or "/"


def _fetch(url, company, cookies_for, user_agent, net, context, deadline, live) -> Fetched:
    redirected = False





    jar = http.cookiejar.CookieJar(http.cookiejar.DefaultCookiePolicy(
        strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain))
    touched: set[tuple[str, str, str]] = set()




    away = False
    first = None
    for _ in range(MAX_HOPS + 1):
        host, port, target, ips = _hop(url, company, net)
        first = first or host


        away = away or _company(host) != company or (
            host != first and consent.is_sensitive(host) and not consent.is_sensitive(first))
        headers = {"Accept": "*/*", "Accept-Encoding": "identity"}
        if user_agent:
            headers["User-Agent"] = user_agent
        asked = urllib.request.Request(url)
        scoped = []
        if not away:
            jar.add_cookie_header(asked)
            scoped = [(c.get("path") or "/", f"{c['name']}={c['value']}") for c in cookies_for(url)
                      if _key(c["name"], c["domain"], c.get("path") or "/") not in touched
                      and _in_partition(c, host)]


            scoped += [(c.path, f"{c.name}={c.value}") for c in jar._cookies_for_request(asked)]
        scoped.sort(key=lambda s: -len(s[0]))
        if scoped:
            headers["Cookie"] = "; ".join(pair for _, pair in scoped)
        if time.monotonic() >= deadline:
            raise Refused(TOO_SLOW)
        conn, resp = _get(host, port, ips, target, headers, context, live, deadline)
        try:
            if not away:
                touched |= _set_cookies(resp, url)
                jar.extract_cookies(resp, asked)
                for c in list(jar):
                    rest = {k.lower(): v for k, v in c._rest.items()}
                    if not _browser_accepts(c.name, c.value, c.secure, c.domain if c.domain_specified else "", c.path,
                                            str(rest.get("samesite") or ""), "partitioned" in rest,
                                            "httponly" in rest):
                        jar.clear(c.domain, c.path, c.name)
            if resp.status in _REDIRECTS and resp.getheader("Location"):
                url = urljoin(url, resp.getheader("Location").strip())
                redirected = True
                continue
            if resp.status in (401, 403):
                raise Refused("Not downloaded: the site refused the file, so you may be signed out. Open the page "
                              "with the link using browser_navigate, sign in if it asks, then try again.")
            if resp.status == 404:
                raise Refused("Not downloaded: the site says that file is not there. Check the link on the page.")
            if resp.status != 200:


                raise Refused(f"Not downloaded: the site answered with error {int(resp.status)} instead of sending "
                              "the file.")
            body = _decoded(_read_capped(resp, deadline), resp.getheader("Content-Encoding") or "")
            content_type = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            declared = email.message.Message()
            declared["Content-Type"] = resp.getheader("Content-Type") or ""
            return Fetched(url, content_type, resp.getheader("Content-Disposition") or "", body, redirected,
                           declared.get_content_charset() or "")
        finally:
            conn.close()
    raise Refused("Not downloaded: the site kept redirecting. Open the page with the link and try that link again.")


def _read_capped(resp, deadline: float) -> bytes:
    too_big = Refused("Not downloaded: the file is over 25 MB, the most browser_download takes. Tell the person where "
                      "it is instead.")
    try:
        if int(resp.getheader("Content-Length") or 0) > MAX_BYTES:
            raise too_big
    except ValueError:
        pass
    chunks, total = [], 0
    while True:
        if time.monotonic() > deadline:
            raise Refused(TOO_SLOW)
        try:
            chunk = resp.read1(64 * 1024)
        except (OSError, http.client.HTTPException):
            raise Refused("Not downloaded: the download stopped partway. Try again.") from None
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise too_big
        chunks.append(chunk)
    if time.monotonic() >= deadline:
        raise Refused(TOO_SLOW)
    declared = resp.getheader("Content-Length")
    if declared and declared.strip().isdigit() and total != int(declared):
        raise Refused("Not downloaded: the download stopped partway. Try again.")
    return b"".join(chunks)




def file_name(f: Fetched) -> str:
    ""

    raw = ""
    if f.disposition:
        m = email.message.Message()
        m["Content-Disposition"] = f.disposition
        try:
            raw = m.get_filename() or ""
        except Exception:
            raw = ""
    if not raw:
        raw = unquote(urlsplit(f.url).path.rsplit("/", 1)[-1])
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    base = " ".join(_SAFE.sub(" ", base).split())
    stem, ext = os.path.splitext(base)
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext or ""):
        stem, ext = base, ""
    stem = stem.strip(" .")[:80].strip(" .")
    ext = ext.lower() or TYPES.get(f.content_type, "")
    name = (stem or "Download") + ext
    return ("Download" + ext) if safe_files.SECRET_PATHS.search("/" + name) else name


def kind_of(name: str, content_type: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return EXTENSIONS.get(ext) or EXTENSIONS.get(TYPES.get(content_type, ""), "other")


def folder(bot: str) -> Path:
    return home.path("work", consent.REAL_HOME) / bot / FOLDER


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _subdir(parent_fd: int, name: str) -> int:
    ""
    try:
        os.mkdir(name, 0o775, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError:
        raise Refused(f"Not saved: your {name} folder is a link, not a plain folder. Move it aside and try "
                      "again.") from None


def _same_file(dir_fd: int, name: str, body: bytes) -> bool | None:
    ""

    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=dir_fd)
    except OSError:
        return False
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size != len(body):
            return False
        return hashlib.sha256(f.read(len(body) + 1)).digest() == hashlib.sha256(body).digest()


def _publish(dir_fd: int, tmp: str, name: str) -> bool:
    ""
    try:
        os.link(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    except FileExistsError:
        return False
    return True


def save(bot: str, name: str, body: bytes, work: Path | None = None) -> tuple[Path, bool]:
    ""

    if not _BOT.match(bot or ""):
        raise Refused("Not saved: this Zip's work folder could not be told.")
    work = Path(work or home.path("work", consent.REAL_HOME))

    own = "taffy" if bot == "default" and home.is_owner_home() else bot
    work.mkdir(parents=True, exist_ok=True)
    fds = [os.open(work, _DIR_FLAGS & ~getattr(os, "O_NOFOLLOW", 0))]
    tmp = f".{name}.{secrets.token_hex(4)}.part"
    try:
        fds.append(_subdir(fds[-1], own))
        here = _subdir(fds[-1], FOLDER)
        fds.append(here)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o664, dir_fd=here)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        stem, ext = os.path.splitext(name)
        for n in range(1, 100):
            candidate = name if n == 1 else f"{stem} ({n}){ext}"
            same = _same_file(here, candidate, body)
            if same:
                return work / own / FOLDER / candidate, True
            if same is None and _publish(here, tmp, candidate):
                return work / own / FOLDER / candidate, False

        raise Refused(f"Not saved: your {FOLDER} folder already has 99 files with this file's name. Clear some out "
                      "first.")
    finally:
        if len(fds) == 3:
            try:
                os.unlink(tmp, dir_fd=fds[2])
            except OSError:
                pass
        for fd in fds:
            os.close(fd)





_DOCX_TOKEN = re.compile(r"<(/?)w:(t|tab|br|cr|p|instrText|delText)\b[^<>]*?(/?)>|<[^<>]*>?|[^<]+")


def _docx_text(body: bytes) -> str:
    ""

    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z, z.open("word/document.xml") as f:
            xml = f.read(DOCX_XML_MAX + 1)
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, ValueError):
        return ""
    if len(xml) > DOCX_XML_MAX:
        return ""
    out, skipping, in_text = [], 0, False
    utf16 = xml.startswith((b"\xff\xfe", b"\xfe\xff")) or xml[:2] in (b"<\x00", b"\x00<")
    for m in _DOCX_TOKEN.finditer(xml.decode("utf-16" if utf16 else "utf-8-sig", "replace")):
        closing, tag, alone = m.group(1), m.group(2), m.group(3)
        if tag in ("instrText", "delText"):
            if not alone:
                skipping = max(0, skipping - 1) if closing else skipping + 1
        elif skipping:
            continue
        elif tag == "t":
            in_text = not closing and not alone
        elif tag == "tab" and not closing:
            out.append("\t")
        elif tag in ("br", "cr") and not closing:
            out.append("\n")
        elif tag == "p" and (closing or alone):
            out.append("\n")
        elif tag is None and in_text and not m.group(0).startswith("<"):
            out.append(m.group(0))
    return html.unescape("".join(out))


def _pdf_text(body: bytes) -> str:
    ""
    tool = shutil.which("pdftotext")
    if not tool:
        return ""
    with tempfile.TemporaryDirectory(prefix="zip-download-") as d:
        path = os.path.join(d, "file.pdf")
        with open(path, "wb") as f:
            f.write(body)
        try:
            p = subprocess.Popen([tool, "-enc", "UTF-8", "-nopgbrk", path, "-"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL)
        except OSError:
            return ""
        timer = threading.Timer(PDF_SEC, p.kill)
        timer.start()
        try:
            out = p.stdout.read(PDF_TEXT_MAX + 1)
            if len(out) > PDF_TEXT_MAX:
                p.kill()
            code = p.wait()
        finally:
            timer.cancel()
            p.stdout.close()
    if len(out) > PDF_TEXT_MAX or code != 0:
        raise Partial(out[:PDF_TEXT_MAX].decode("utf-8", "replace"))
    return out.decode("utf-8", "replace")


class _HTMLText(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
             "footer", "table", "ul", "ol", "blockquote", "pre", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skipping = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(data)


def _html_text(body: bytes) -> str:
    p = _HTMLText()
    try:
        p.feed(body.decode("utf-8", "replace"))
        p.close()
    except Exception:
        pass
    return "".join(p.out)


def _plain_text(body: bytes) -> str:
    return body.decode("utf-8-sig", "replace")


READERS: dict[str, Callable[[bytes], str]] = {"docx": _docx_text, "pdf": _pdf_text, "html": _html_text,
                                              "text": _plain_text}




_WEB_CHARSET = re.compile(r"^(cp\d+|iso8859-\d+|latin-1|ascii|mac-[a-z]+|koi8-[a-z]+|shift_jis|euc_[a-z]+|gb\w+|big5\w*|"
                          r"utf-16(-[bl]e)?|utf-32(-[bl]e)?|iso2022_\w+|hz)$")


def _web_charset(charset: str) -> str:
    ""

    try:
        name = codecs.lookup(charset).name if charset else ""
    except (LookupError, ValueError):
        return ""
    return name if _WEB_CHARSET.match(name) else ""


MAX_MEMBERS = 1000
CHUNK = 1 << 16


def _decoded(body: bytes, encoding: str) -> bytes:
    ""

    enc = encoding.strip().lower()
    if enc in ("", "identity"):
        return body
    if enc not in ("gzip", "x-gzip", "deflate"):

        raise Refused("Not downloaded: the site sent the file packed in a way this can't unpack. Tell the person "
                      "where the file is instead.")
    too_big = Refused("Not downloaded: the file is over 25 MB once unpacked, the most browser_download takes. Tell "
                      "the person where it is instead.")
    tries = [zlib.MAX_WBITS | 16] if enc != "deflate" else [zlib.MAX_WBITS, -zlib.MAX_WBITS]
    for wbits in tries:



        view, at, chunks, size = memoryview(body), 0, [], 0
        try:
            for _ in range(MAX_MEMBERS):
                d = zlib.decompressobj(wbits)
                while not d.eof and at < len(body):
                    end = min(at + CHUNK, len(body))
                    part = d.decompress(view[at:end], MAX_BYTES + 1 - size)
                    chunks.append(part)
                    size += len(part)
                    if size > MAX_BYTES or d.unconsumed_tail:
                        raise too_big
                    at = end - len(d.unused_data)
                if not d.eof:
                    break
                if at >= len(body) or wbits != zlib.MAX_WBITS | 16:
                    return b"".join(chunks)
            else:
                raise Refused("Not downloaded: the site sent the file in too many pieces to unpack. Tell the person "
                              "where it is instead.")
        except zlib.error:
            continue
    raise Refused("Not downloaded: the site sent the file damaged on the way. Try again in a minute.")



_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.I)


def _text_charset(kind: str, body: bytes, charset: str) -> str:
    ""


    if charset:
        return charset
    for bom, name in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF8, "utf-8"),
                      (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if body.startswith(bom):
            return name
    m = _META_CHARSET.search(body[:2048]) if kind == "html" else None
    return m.group(1).decode("ascii", "replace") if m else ""


def text_of(kind: str, body: bytes, charset: str = "") -> tuple[str, bool]:
    ""

    reader = READERS.get(kind)
    if reader is None:
        return "", True
    charset = _text_charset(kind, body, charset)
    if kind in ("text", "html") and _web_charset(charset):
        try:
            body = body.decode(_web_charset(charset), "replace").encode("utf-8", "replace")
        except UnicodeError:
            pass
    try:
        raw, whole = reader(body), True
    except Partial as e:
        raw, whole = e.args[0], False
    lines = [line.rstrip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip(), whole








_CANVAS_FILE = re.compile(r"^((?:/(?:courses|users|groups)/[\d~]+)?/files/[\d~]+)(?:/(?:preview|file_preview))?/?$")


def canvas_download(url: str) -> str:
    ""
    parts = urlsplit(url)
    m = _CANVAS_FILE.match(parts.path)
    if not m:
        return ""
    query = "&".join(q for q in (parts.query, "download_frd=1") if q)
    return parts._replace(path=m.group(1) + "/download", query=query, fragment="").geturl()


def _stand_in_page(f: Fetched, url: str) -> bool:
    ""

    asked_ending = os.path.splitext(unquote(urlsplit(url).path))[1].lower()
    attachment = f.disposition.strip().lower().startswith("attachment")
    page = f.content_type in ("text/html", "application/xhtml+xml")
    return page and not attachment and (f.redirected or asked_ending not in (".html", ".htm", ".xhtml"))


def download(url: str, bot: str, cookies_for: Callable[[str], list[dict]], user_agent: str = "",
             net: Network | None = None, work: Path | None = None) -> Saved:
    f = fetch(url, cookies_for, user_agent, net)
    if _stand_in_page(f, url) and canvas_download(url):
        url = canvas_download(url)
        f = fetch(url, cookies_for, user_agent, net)
    name = file_name(f)
    kind = kind_of(name, f.content_type)
    if _stand_in_page(f, url):

        raise Refused("Not downloaded: the site sent a web page instead of the file, so you may be signed out. "
                      "Open the page with the link using browser_navigate, sign in if it asks, then try again.")
    path, reused = save(bot, name, f.body, work)
    text, whole = text_of(kind, f.body, f.charset)
    full = None
    if len(text) > TEXT_MAX:
        if kind == "text" and not _web_charset(_text_charset(kind, f.body, f.charset)):
            full = str(path)
        else:
            full_path, _ = save(bot, name + ".txt", text.encode("utf-8"), work)
            full = str(full_path)
        text = text[:TEXT_MAX]
    return Saved(str(path), len(f.body), kind, text, full, reused, f.url, whole)


def result(s: Saved) -> dict:



    src = urlsplit(s.source)
    out = {"success": True, "saved": s.path, "bytes": s.size, "from": f"{src.scheme}://{src.netloc}"}
    if s.reused:
        out["note"] = "The same file was already saved here from an earlier download, so that copy was kept."
    if s.text:
        out["text"] = s.text
        if s.full_text:
            out["text_cut"] = (f"The text is longer than this. All of it that could be read is in {s.full_text}; "
                               "read it with read_file.")
        if not s.whole:
            out["text_partial"] = ("Only part of this file's text could be read. The file itself is saved whole; "
                                   "do not treat this text as the complete document.")
    elif s.kind in READERS:
        out["no_text"] = ("The file is saved, but no text could be read from it (it may be scanned pages or "
                          "damaged). Open it from the saved path with your own tools.")
    else:
        out["no_text"] = ("The file is saved. This kind of file is not read as text here, so open it from the "
                          "saved path with your own tools.")
    return out




def browser_session(task_id: str) -> tuple[str, Callable[[str], list[dict]]]:
    ""

    try:
        from tools.browser_supervisor import SUPERVISOR_REGISTRY
        from tools.browser_vault_tool import _ensure_supervisor
        if _ensure_supervisor(task_id) is None:
            raise LookupError
        cdp = SUPERVISOR_REGISTRY.capture(task_id)
    except Exception:
        raise Refused("Not downloaded: your browser is not open. Open the page with the file's link using "
                      "browser_navigate first, then try again.") from None
    try:
        agent = str(((cdp.call("Browser.getVersion") or {}).get("result") or {}).get("userAgent") or "")
    except Exception:
        agent = ""

    def cookies_for(url: str) -> list[dict]:
        try:
            reply = cdp.call("Network.getCookies", {"urls": [url]}, session_id=cdp.page_session_id)
        except Exception:
            raise Refused("Not downloaded: your browser's sign-in for that site could not be read. Open the page "
                          "with the link using browser_navigate, then try again.") from None
        jar = ((reply or {}).get("result") or {}).get("cookies") or []
        return [c for c in jar if isinstance(c, dict) and c.get("name") and "value" in c and c.get("domain")]
    return agent, cookies_for


def handler(bot_of: Callable[[], str]):
    ""
    def browser_download(args: dict, **kw) -> str:
        url = str((args or {}).get("url") or "").strip()
        if not url:
            return json.dumps({"error": "Give the file's full https link as url."})
        try:
            agent, cookies_for = browser_session(kw.get("task_id") or "default")
            saved = download(url, bot_of(), cookies_for, agent)
        except Refused as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            log.warning("browser_download failed", exc_info=True)
            return json.dumps({"error": f"browser_download failed: {type(e).__name__}"})
        return json.dumps(result(saved), ensure_ascii=False)
    return browser_download




def aborted(result) -> bool:
    ""

    try:
        data = json.loads(result) if isinstance(result, str) else None
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("success") is False and "ERR_ABORTED" in str(data.get("error") or "")


def with_hint(result: str, url: str) -> str:
    ""
    hint = ABORTED_HINT.format(url=url)
    try:
        data = json.loads(result)
    except ValueError:
        data = None
    if isinstance(data, dict):
        data["file_link"] = hint
        return json.dumps(data, ensure_ascii=False)
    return f"{result}\n\n{hint}"
