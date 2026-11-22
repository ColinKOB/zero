""










import io
import json
import logging
import re
import zipfile
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("taffy.sites")

TOKEN_FILE = config.HOME / ".config" / "zero" / "netlify.token"
API = "https://api.netlify.com/api/v1"
MAX_FILE = 5 * 1024 * 1024
MAX_SITE = 50 * 1024 * 1024
SIGNUPS_TTL = 300
SIGNUPS_TIMEOUT = 10
PER_PAGE, MAX_PAGES = 100, 10
TRANSPORT = None


class NotConnected(Exception):
    ""


class PublishError(Exception):
    ""


def token() -> str | None:
    try:
        t = TOKEN_FILE.read_text().strip()
    except OSError:
        return None
    return t or None


def connected() -> bool:
    return token() is not None


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=TRANSPORT)


def _headers() -> dict:
    t = token()
    if not t:
        raise NotConnected()
    return {"Authorization": f"Bearer {t}"}


def site_name(team_id: str, team_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", team_name.lower()).strip("-")[:30] or "team"
    return f"zero-{slug}-{re.sub(r'[^a-z0-9]', '', team_id)[-6:]}"


def bundle(folder: Path) -> bytes:
    ""

    folder = Path(folder)
    if not (folder / "index.html").is_file():
        raise PublishError("The site folder needs an index.html at its top level.")
    buf = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            size = p.stat().st_size
            if size > MAX_FILE:
                raise PublishError(f"{p.name} is over 5 MB. Shrink it or leave it out.")
            total += size
            if total > MAX_SITE:
                raise PublishError("The site is over 50 MB. Shrink the images.")
            z.write(p, p.relative_to(folder).as_posix())
    return buf.getvalue()


async def publish(team_id: str, team_name: str, folder: Path, site_id: str | None, draft: bool = False) -> dict:
    ""


    data = bundle(folder)
    headers = _headers()
    async with _client(120) as client:
        if not site_id:
            r = await client.post(f"{API}/sites", headers=headers, json={"name": site_name(team_id, team_name)})
            if r.status_code == 422:
                r = await client.post(f"{API}/sites", headers=headers, json={})
            if r.status_code >= 400:
                raise PublishError(f"Netlify would not make the site ({r.status_code}).")
            site_id = _json(r).get("id")
            if not site_id:
                raise PublishError("Netlify did not return a site id.")
        r = await client.post(f"{API}/sites/{site_id}/deploys", params={"draft": "true"} if draft else None,
                              headers={**headers, "Content-Type": "application/zip"}, content=data)
        if r.status_code >= 400:
            raise PublishError(f"Netlify would not take the deploy ({r.status_code}).")
        dep = _json(r)
        if draft:
            url = dep.get("deploy_ssl_url") or dep.get("deploy_url")
        else:
            url = dep.get("ssl_url") or dep.get("url") or await _site_url(client, site_id, headers)
    return {"site_id": site_id, "url": url or "", "deploy_id": str(dep.get("id") or "")}


async def restore(site_id: str, deploy_id: str) -> dict:
    ""
    headers = _headers()
    async with _client(60) as client:
        r = await client.post(f"{API}/sites/{site_id}/deploys/{deploy_id}/restore", headers=headers, json={})
        if r.status_code >= 400:
            raise PublishError(f"Netlify would not publish the deploy ({r.status_code}).")
        dep = _json(r)
        url = dep.get("ssl_url") or dep.get("url") or await _site_url(client, site_id, headers)
    return {"site_id": site_id, "url": url or "", "deploy_id": deploy_id}


async def _site_url(client: httpx.AsyncClient, site_id: str, headers: dict) -> str:
    site = _json(await client.get(f"{API}/sites/{site_id}", headers=headers))
    return site.get("ssl_url") or site.get("url") or ""


async def signups(site_id: str) -> int | None:
    ""
    try:
        headers = _headers()
    except NotConnected:
        return None
    total = 0
    try:
        async with _client(SIGNUPS_TIMEOUT) as client:
            for page in range(1, MAX_PAGES + 1):
                r = await client.get(f"{API}/sites/{site_id}/submissions", headers=headers,
                                     params={"per_page": PER_PAGE, "page": page})
                if r.status_code >= 400:
                    return None
                body = r.json()
                if not isinstance(body, list):
                    return None
                total += len(body)
                if len(body) < PER_PAGE:
                    break
    except (httpx.HTTPError, ValueError):
        return None
    return total


def preview_note(folder: Path) -> str:
    return f"Preview: {folder / 'index.html'}"


def not_connected_need() -> tuple[str, str]:
    ""
    return ("Put the site online",
            "This home is not connected to Netlify yet, so the site stays a local preview in the team folder. Connect "
            "Netlify for this home and I will publish it.")


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
