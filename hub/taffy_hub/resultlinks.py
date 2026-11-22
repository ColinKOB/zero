""







import hashlib
import html
import hmac
import secrets
import time

from . import resultcard

LIFETIME = 300
MAX_LIFETIME = 900

_KEY = secrets.token_bytes(32)



PAGE_POLICY = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' data: blob:; "
               "style-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; media-src data: blob:; "
               "connect-src 'none'; form-action 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; "
               "base-uri 'none'; frame-ancestors 'none'")

PAGE_HEADERS = {
    "Content-Security-Policy": PAGE_POLICY,
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _sign(media: str, expires: int) -> str:
    return hmac.new(_KEY, f"result-page\n{media}\n{expires}".encode(), hashlib.sha256).hexdigest()


def browser_page(page: bytes) -> str:
    ""
    content = html.escape(page.decode("utf-8", errors="replace"), quote=True)
    return ('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Zero result</title><style>html,body{margin:0;width:100%;height:100%;overflow:hidden}'
            'iframe{display:block;border:0;width:100%;height:100%}</style></head><body>'
            '<iframe title="Result page" sandbox="allow-scripts" referrerpolicy="no-referrer" '
            f'srcdoc="{content}"></iframe></body></html>')


def mint(media: str, now: float | None = None) -> str:
    ""
    expires = int(now if now is not None else time.time()) + LIFETIME
    return f"/result-page/{media}?expires={expires}&sig={_sign(media, expires)}"


def check(media: str, expires: str, sig: str, now: float | None = None) -> bool:

    if (not resultcard.MEDIA_ID.fullmatch(media) or not (expires.isascii() and expires.isdigit())
            or len(expires) > 12 or not sig.isascii()):
        return False
    at, now = int(expires), now if now is not None else time.time()
    if not now <= at <= now + MAX_LIFETIME:
        return False
    return hmac.compare_digest(_sign(media, at), sig)
