""














import base64
import hashlib
import json
import logging
import os
import secrets
import threading

import httpx

from . import home
from .store import _norm_code

log = logging.getLogger("taffy.pairlookup")

SALT = b"zero-pair-lookup-v1"
ROUNDS = 200_000
SERVICE = "https://pair.heyzero.app"


def derive(code: str) -> tuple[str, bytes]:
    ""
    dk = hashlib.pbkdf2_hmac("sha256", _norm_code(code).encode(), SALT, ROUNDS, 64)
    return dk[:32].hex(), dk[32:]


def seal(key: bytes, servers: list[str], fp: str | None) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    plain = json.dumps({"v": 1, "servers": servers, "fp": fp}).encode()
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plain, SALT)).decode()


def open_box(key: bytes, box: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(box)
    return json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], SALT))


def enabled() -> bool:
    if os.environ.get("ZERO_PAIR_LOOKUP", "").lower() == "off":
        return False
    return home.get("network.pair_lookup", True) is not False


def announce(code: str, servers: list[str] | None = None, fp: str | None = None, timeout: float = 5) -> bool:
    ""




    try:
        return _announce(code, servers, fp, timeout)
    except Exception as e:
        log.warning("pairing lookup skipped: %r", e)
        return False


def _announce(code: str, servers: list[str] | None, fp: str | None, timeout: float) -> bool:
    if not enabled():
        return False
    if servers is None:
        servers = [str(home.get("network.public_url") or "").strip()]
    if fp is None:
        fp = str(home.get("network.tls_fingerprint") or "").strip().lower() or None
    servers = [s.rstrip("/") for s in servers if s and not _loopback(s)]
    if not servers:
        return False
    lookup_id, key = derive(code)
    r = httpx.put(f"{SERVICE}/v1/{lookup_id}", json={"box": seal(key, servers, fp)}, timeout=timeout)
    return r.status_code == 200


def announce_soon(code: str) -> None:
    ""
    threading.Thread(target=announce, args=(code,), daemon=True, name="pair-lookup").start()


def _loopback(server: str) -> bool:
    from urllib.parse import urlsplit
    host = (urlsplit(server).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.")
