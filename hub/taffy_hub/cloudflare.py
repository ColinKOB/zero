""











import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

import httpx

from . import config, consent, keys, zipfiles

WRANGLER_VERSION = "4.151.3"
MAX_FILES = 10_000
MAX_BYTES = 100_000_000
MAX_FILE_BYTES = 25_000_000
MAX_DEPTH = 20
MAX_PAGES = 100
DEPLOY_TIMEOUT = 180
_DIR = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_ID = re.compile(r"[0-9a-f]{32}")
_SKIP = {".git", "node_modules", ".wrangler", ".venv", "__pycache__"}
_PRIVATE = {"package.json", "package-lock.json", "npm-shrinkwrap.json", ".npmrc", ".yarnrc", "tsconfig.json", "jsconfig.json"}
_SOURCE = {".js", ".mjs", ".json", ".txt"}
_CONFIG = {"name", "main", "compatibility_date", "compatibility_flags", "workers_dev", "preview_urls", "assets", "kv_namespaces", "routes", "$schema"}
_ASSETS = {"directory", "binding", "run_worker_first", "html_handling", "not_found_handling"}
_READ_FAILURE = "I couldn't verify the live Cloudflare project. Nothing was published."
_UNCERTAIN = "Cloudflare may have changed, but I couldn't verify the full publish. Ask the owner to check it and adopt the complete project again."


@dataclass(frozen=True)
class ProjectSnapshot:
    account: str
    worker: str
    config: dict
    files: tuple[tuple[str, bytes], ...]
    asset_paths: tuple[str, ...]
    domains: tuple[str, ...]
    content_hash: str


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _relative(value) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise keys.KeyAppError("Choose a project folder inside this Zip's work folder.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) > MAX_DEPTH:
        raise keys.KeyAppError("Choose a project folder inside this Zip's work folder.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise keys.KeyAppError("The project has a filename I can't safely publish.") from None
    return path.as_posix()


def _jsonc(raw: bytes) -> dict:

    try:
        text = raw.decode("utf-8-sig")
        clean = []
        i = 0
        while i < len(text):
            if text[i] == '"':
                end = i + 1
                while end < len(text):
                    if text[end] == "\\":
                        end += 2
                    elif text[end] == '"':
                        break
                    else:
                        end += 1
                clean.append(text[i:end + 1])
                i = end + 1
            elif text.startswith("//", i):
                end = text.find("\n", i)
                i = len(text) if end < 0 else end
                clean.append(" ")
            elif text.startswith("/*", i):
                end = text.find("*/", i + 2)
                if end < 0:
                    raise ValueError()
                i = end + 2
                clean.append(" ")
            else:
                clean.append(text[i])
                i += 1
        text = "".join(clean)
        clean = []
        quoted = escaped = False
        for i, char in enumerate(text):
            if not quoted and char == "," and text[i + 1:].lstrip().startswith(("}", "]")):
                continue
            clean.append(char)
            if char == '"' and not escaped:
                quoted = not quoted
            escaped = quoted and char == "\\" and not escaped

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result

        def invalid_constant(_):
            raise ValueError()

        value = json.loads("".join(clean), object_pairs_hook=unique, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise keys.KeyAppError("The project needs a valid wrangler.json or wrangler.jsonc file.") from None


def _project_config(value: dict, account: str) -> dict:
    bad = "This publisher supports plain JavaScript, static assets, KV, and custom domains only. Remove unsupported project settings first."
    if set(value) - _CONFIG or not isinstance(value.get("name"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", value["name"]):
        raise keys.KeyAppError(bad)
    main = _relative(value.get("main"))
    if PurePosixPath(main).suffix not in {".js", ".mjs"} or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value.get("compatibility_date", ""))):
        raise keys.KeyAppError(bad)
    if value.get("workers_dev") is not False or value.get("preview_urls", False) is not False:
        raise keys.KeyAppError("Turn off workers_dev and preview_urls before publishing this project.")
    flags = value.get("compatibility_flags", [])
    if not isinstance(flags, list) or any(not isinstance(f, str) or not re.fullmatch(r"[a-z0-9_]+", f) for f in flags):
        raise keys.KeyAppError(bad)
    assets = value.get("assets")
    if not isinstance(assets, dict) or set(assets) - _ASSETS:
        raise keys.KeyAppError(bad)
    directory = _relative(assets.get("directory"))
    if directory == "." or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(assets.get("binding", ""))):
        raise keys.KeyAppError(bad)
    if assets.get("run_worker_first") is not True:
        raise keys.KeyAppError("Keep assets.run_worker_first enabled for this project.")
    if assets.get("html_handling", "auto-trailing-slash") not in ("auto-trailing-slash", "force-trailing-slash", "drop-trailing-slash", "none") or assets.get("not_found_handling", "none") not in ("none", "404-page", "single-page-application"):
        raise keys.KeyAppError(bad)
    namespaces = value.get("kv_namespaces", [])
    if not isinstance(namespaces, list):
        raise keys.KeyAppError(bad)
    names = {assets["binding"]}
    for item in namespaces:
        if not isinstance(item, dict) or set(item) != {"binding", "id"} or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item.get("binding", ""))) or not _ID.fullmatch(str(item.get("id", ""))) or item["binding"] in names:
            raise keys.KeyAppError(bad)
        names.add(item["binding"])
    routes = value.get("routes")
    if not isinstance(routes, list) or not routes or len(routes) > 1000:
        raise keys.KeyAppError(bad)
    hosts = set()
    for route in routes:
        if not isinstance(route, dict) or set(route) - {"pattern", "custom_domain", "zone_id", "zone_name"} or route.get("custom_domain") is not True:
            raise keys.KeyAppError(bad)
        host = route.get("pattern")
        if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host) or host in hosts:
            raise keys.KeyAppError(bad)
        if "zone_id" in route and not _ID.fullmatch(str(route["zone_id"])):
            raise keys.KeyAppError(bad)
        if "zone_name" in route and (not isinstance(route["zone_name"], str) or not (host == route["zone_name"] or host.endswith("." + route["zone_name"]))):
            raise keys.KeyAppError(bad)
        hosts.add(host)
    return {**{k: v for k, v in value.items() if k != "$schema"}, "main": main, "assets": {**assets, "directory": directory}, "account_id": account, "preview_urls": False}


def _snapshot(args: dict, bot_id: str) -> ProjectSnapshot:
    if not isinstance(bot_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", bot_id):
        raise keys.KeyAppError("Choose a valid Zip before inspecting its project.")
    account = args.get("account")
    if not isinstance(account, str) or not _ID.fullmatch(account):
        raise keys.KeyAppError("Choose an account from the Cloudflare account list.")
    folder = args.get("folder")
    if isinstance(folder, str) and folder.startswith("/workspace/"):
        folder = folder[len("/workspace/"):]
    folder = _relative(folder)
    root = Path(zipfiles.made_root(bot_id))
    captured = {}
    skipped = set()
    scanned = total = 0

    def scan(fd: int, here: PurePosixPath):
        nonlocal scanned, total
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                scanned += 1
                rel = here / entry.name
                _relative(rel.as_posix())
                if scanned > MAX_FILES or len(rel.parts) > MAX_DEPTH:
                    raise keys.KeyAppError("The project has too many files or folders to publish safely.")
                st = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    if entry.name in _SKIP:
                        skipped.add(rel.as_posix())
                        continue
                    inner = os.open(entry.name, _DIR, dir_fd=fd)
                    try:
                        scan(inner, rel)
                    finally:
                        os.close(inner)
                else:
                    opened = os.open(entry.name, _FILE, dir_fd=fd)
                    with os.fdopen(opened, "rb") as stream:
                        st = os.fstat(stream.fileno())
                        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                            raise keys.KeyAppError("The project must contain ordinary files, without symbolic or hard links.")
                        if st.st_size > MAX_FILE_BYTES:
                            raise keys.KeyAppError("A project file is too large to publish safely.")
                        data = stream.read(min(MAX_FILE_BYTES, MAX_BYTES - total) + 1)
                        after = os.fstat(stream.fileno())
                        if (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
                            raise keys.KeyAppError("The project changed while I read it. Inspect it again.")
                        total += len(data)
                        if len(data) > MAX_FILE_BYTES or total > MAX_BYTES:
                            raise keys.KeyAppError("The project is too large to publish safely.")
                        captured[rel.as_posix()] = (data, st.st_mode)
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise keys.KeyAppError("The project changed while I read it. Inspect it again.")

    fd = None
    try:
        fd = os.open(root.anchor, _DIR)
        for part in (*root.parts[1:], *PurePosixPath(folder).parts):
            inner = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = inner
        scan(fd, PurePosixPath())
    except OSError:
        raise keys.KeyAppError("I couldn't safely read that project inside this Zip's work folder.") from None
    finally:
        if fd is not None:
            os.close(fd)
    configs = [p for p in ("wrangler.json", "wrangler.jsonc") if p in captured]
    if len(configs) != 1 or "wrangler.toml" in captured:
        raise keys.KeyAppError("Keep exactly one wrangler.json or wrangler.jsonc file in the project.")
    project = _project_config(_jsonc(captured[configs[0]][0]), account)
    asset_root = project["assets"]["directory"] + "/"
    if any(p.startswith(asset_root) or p + "/" == asset_root for p in skipped):
        raise keys.KeyAppError("Remove ignored folders from the assets directory.")
    files = {}
    assets = []
    for name, (data, mode) in captured.items():
        path = PurePosixPath(name)
        unsafe = any(p in _PRIVATE or p.startswith((".env", ".dev.vars", "tsconfig.")) for p in path.parts)
        in_assets = name.startswith(asset_root)
        if in_assets and (unsafe or path.name == ".assetsignore"):
            raise keys.KeyAppError("Remove private settings and asset exclusion rules from the assets directory.")
        source = path.suffix in _SOURCE and not unsafe and not any(p.startswith(".") for p in path.parts)
        if in_assets or (source and name not in configs):
            if mode & 0o111 or path.suffix.lower() in {".exe", ".dll", ".so", ".dylib", ".node", ".sh", ".py"}:
                raise keys.KeyAppError("This project includes executable files that I can't safely stage.")
            files[name] = data
            if in_assets:
                assets.append(name[len(asset_root):])
    if project["main"] not in files or not assets:
        raise keys.KeyAppError("Include the complete JavaScript source and a nonempty assets directory.")
    hosts = tuple(sorted(r["pattern"] for r in project["routes"]))
    digest = hashlib.sha256()
    for name, data in sorted({**files, configs[0]: captured[configs[0]][0]}.items()):
        digest.update(_canonical([name, len(data)]))
        digest.update(data)
    return ProjectSnapshot(account, project["name"], project, tuple(sorted(files.items())), tuple(sorted(assets)), hosts, digest.hexdigest())


def _api(stored: dict, method: str, path: str, **kwargs) -> dict:
    try:
        reply = httpx.request(method, keys.BY_ID["cloudflare"].api + path, headers={"Authorization": "Bearer " + stored["token"], "Accept": "application/json"}, timeout=keys.TIMEOUT_SEC, **kwargs)
        data = keys._json(reply)
    except (httpx.HTTPError, ValueError, KeyError):
        raise keys.KeyAppError(_READ_FAILURE) from None
    if reply.status_code == 401:
        raise keys.KeyAppError("Cloudflare stopped accepting the connection. Connect it again.", rejected=True)
    if reply.status_code == 403:
        raise keys.KeyAppError(keys.CLOUDFLARE_RECONNECT, rejected=True)
    if not 200 <= reply.status_code < 300 or data.get("success") is not True or "result" not in data:
        raise keys.KeyAppError(_READ_FAILURE)
    return data


def _pages(stored: dict, path: str, **params) -> list[dict]:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        data = _api(stored, "GET", path, params={**params, "page": page, "per_page": 100})
        result = data["result"]
        info = data.get("result_info", {})
        if not isinstance(result, list) or any(not isinstance(r, dict) for r in result) or not isinstance(info, dict):
            raise keys.KeyAppError(_READ_FAILURE)
        rows.extend(result)
        pages = info.get("total_pages")
        if pages is not None:
            if type(pages) is not int or pages < 0 or info.get("page", page) != page:
                raise keys.KeyAppError(_READ_FAILURE)
            if page >= pages:
                return rows
        elif len(result) < 100:
            return rows
    raise keys.KeyAppError("Cloudflare returned too many records to verify safely.")


def _deployment(stored: dict, snapshot: ProjectSnapshot) -> dict:
    data = _api(stored, "GET", f"/accounts/{snapshot.account}/workers/scripts/{snapshot.worker}/deployments")["result"]
    rows = data.get("deployments") if isinstance(data, dict) else data
    try:
        row = rows[0]
        versions = row["versions"]
        if len(versions) != 1 or versions[0]["percentage"] != 100:
            raise ValueError()
        value = {"id": row["id"], "version_id": versions[0]["version_id"]}
        if any(not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", v) for v in value.values()):
            raise ValueError()
        return value
    except (KeyError, TypeError, IndexError, ValueError):
        raise keys.KeyAppError("I couldn't verify a single live deployment for this Worker.") from None


def _preserve_settings(snapshot: ProjectSnapshot, settings: dict) -> dict:
    failure = "The live Worker has settings or bindings I can't prove this publish will preserve."
    bindings = settings.get("bindings")
    if not isinstance(bindings, list):
        raise keys.KeyAppError(failure)
    kv = {}
    assets = []
    seen = set()
    for binding in bindings:
        if not isinstance(binding, dict) or not isinstance(binding.get("name"), str) or binding["name"] in seen:
            raise keys.KeyAppError(failure)
        seen.add(binding["name"])
        kind = binding.get("type")
        if kind == "kv_namespace":
            kv[binding["name"]] = binding.get("namespace_id")
        elif kind == "assets":
            assets.append(binding["name"])
        elif kind not in {"secret_text", "plain_text", "json"}:
            raise keys.KeyAppError(failure)
    if kv != {r["binding"]: r["id"] for r in snapshot.config.get("kv_namespaces", [])} or assets != [snapshot.config["assets"]["binding"]]:
        raise keys.KeyAppError(failure)
    live_assets = settings.get("assets")
    if not isinstance(live_assets, dict) or not isinstance(live_assets.get("config"), dict):
        raise keys.KeyAppError(failure)
    remote = live_assets["config"]
    defaults = {"html_handling": "auto-trailing-slash", "not_found_handling": "none"}
    for name, value in remote.items():
        if name not in {"run_worker_first", *defaults} or value != snapshot.config["assets"].get(name, defaults.get(name)):
            raise keys.KeyAppError(failure)
    if remote.get("run_worker_first") is not True:
        raise keys.KeyAppError(failure)
    preserved = {}
    known = {"bindings", "assets", "compatibility_date", "compatibility_flags", "logpush", "observability", "limits", "placement", "tags", "tail_consumers", "migration_tag", "usage_model"}
    if any(value not in (None, False, [], {}, "") for name, value in settings.items() if name not in known):
        raise keys.KeyAppError(failure)
    if settings.get("tail_consumers") or settings.get("migration_tag") or settings.get("usage_model") not in (None, "standard"):
        raise keys.KeyAppError(failure)
    for name in ("logpush", "observability", "limits", "placement", "tags"):
        if name in settings and settings[name] is not None:
            preserved[name] = settings[name]
    return preserved


def _preflight(stored: dict, snapshot: ProjectSnapshot) -> dict:
    prefix = f"/accounts/{snapshot.account}/workers"
    settings = _api(stored, "GET", f"{prefix}/scripts/{snapshot.worker}/settings")["result"]
    if not isinstance(settings, dict):
        raise keys.KeyAppError(_READ_FAILURE)
    preserved = _preserve_settings(snapshot, settings)
    domains = _pages(stored, prefix + "/domains")
    by_host = {}
    for item in domains:
        host = item.get("hostname")
        if not isinstance(host, str) or host in by_host or not isinstance(item.get("service"), str):
            raise keys.KeyAppError(_READ_FAILURE)
        by_host[host] = item
    own = {host for host, item in by_host.items() if item["service"] == snapshot.worker}
    if not own.issubset(snapshot.domains):
        raise keys.KeyAppError("Keep every existing custom domain in the project before publishing.")
    zones = _pages(stored, "/zones")
    for zone in zones:
        if not _ID.fullmatch(str(zone.get("id", ""))) or not isinstance(zone.get("name"), str):
            raise keys.KeyAppError(_READ_FAILURE)
    origins = []
    for route in snapshot.config["routes"]:
        host = route["pattern"]
        existing = by_host.get(host)
        if existing and (existing["service"] != snapshot.worker or existing.get("environment", "production") != "production" or existing.get("enabled", True) is not True or existing.get("previews_enabled", False)):
            raise keys.KeyAppError("A requested domain belongs to another Worker or uses unsupported routing settings.")
        candidates = [z for z in zones if host == z["name"] or host.endswith("." + z["name"])]
        if not candidates:
            raise keys.KeyAppError("I couldn't verify the zone for every requested domain.")
        zone = max(candidates, key=lambda z: len(z["name"]))
        if zone.get("account", {}).get("id") != snapshot.account or route.get("zone_id", zone["id"]) != zone["id"] or route.get("zone_name", zone["name"]) != zone["name"]:
            raise keys.KeyAppError("Every requested domain must belong to the selected Cloudflare account and zone.")
        records = _pages(stored, f"/zones/{zone['id']}/dns_records", name=host)
        if any(record.get("name") != host for record in records):
            raise keys.KeyAppError(_READ_FAILURE)
        if records and not existing:
            raise keys.KeyAppError("A requested new domain already has DNS records. I won't overwrite them.")
        origins.append({"hostname": host, "zone_id": zone["id"]})
    _publish_permissions(stored, snapshot.account, {origin["zone_id"] for origin in origins})
    for zone in zones:
        routes = _pages(stored, f"/zones/{zone['id']}/workers/routes")
        for route in routes:
            if not isinstance(route.get("pattern"), str):
                raise keys.KeyAppError(_READ_FAILURE)
            pattern = route["pattern"].split("://")[-1].split("/")[0]
            if route.get("script") == snapshot.worker or any(fnmatch.fnmatchcase(host, pattern) for host in snapshot.domains):
                raise keys.KeyAppError("This Worker has a route I can't safely preserve with this project's custom domains.")
    return {"settings": settings, "preserved": preserved, "deployment": _deployment(stored, snapshot), "domains": sorted(domains, key=lambda d: d["hostname"]), "origins": sorted(origins, key=lambda x: x["hostname"])}


def _publish_permissions(stored: dict, account: str, zones: set[str]):

    verified = _api(stored, "GET", "/user/tokens/verify")["result"]
    if not isinstance(verified, dict) or verified.get("status") != "active" or not _ID.fullmatch(str(verified.get("id", ""))):
        raise keys.KeyAppError(keys.CLOUDFLARE_RECONNECT, rejected=True)
    token = _api(stored, "GET", "/user/tokens/" + verified["id"])["result"]
    if not isinstance(token, dict) or token.get("id") != verified["id"] or token.get("status") != "active":
        raise keys.KeyAppError(keys.CLOUDFLARE_RECONNECT, rejected=True)
    policies = token.get("policies")
    if not isinstance(policies, list) or not policies:
        raise keys.KeyAppError(keys.CLOUDFLARE_RECONNECT, rejected=True)
    for policy in policies:

        if (not isinstance(policy, dict) or policy.get("effect") != "allow"
                or not isinstance(policy.get("resources"), dict)
                or not isinstance(policy.get("permission_groups"), list)
                or any(not isinstance(group, dict) for group in policy["permission_groups"])):
            raise keys.KeyAppError(keys.CLOUDFLARE_RECONNECT, rejected=True)
    account_resource = "com.cloudflare.api.account." + account
    requirements = [("Workers Scripts", (account_resource, "com.cloudflare.api.account.*"))]
    requirements += [("Workers Routes", ("com.cloudflare.api.account.zone." + zone,
                                         "com.cloudflare.api.account.zone.*")) for zone in zones]
    for permission, resources in requirements:
        allowed = False
        for policy in policies:
            if not any(group.get("name") in (permission + " Edit", permission + " Write")
                       for group in policy["permission_groups"]):
                continue
            scope = policy["resources"]
            if any(scope.get(resource) == "*" for resource in resources):
                allowed = True
            if permission == "Workers Routes":
                for parent in (account_resource, "com.cloudflare.api.account.*"):
                    nested = scope.get(parent)
                    if isinstance(nested, dict) and any(nested.get(resource) == "*" for resource in resources):
                        allowed = True
        if not allowed:
            raise keys.KeyAppError(keys.CLOUDFLARE_RECONNECT, rejected=True)


@contextlib.contextmanager
def _project_lock(snapshot: ProjectSnapshot):
    folder = Path(config.HUB_DIR) / "cloudflare-projects" / snapshot.account
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(folder / (snapshot.worker + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise keys.KeyAppError("This Worker is already being checked or published. Try again when that finishes.") from None
        yield folder / (snapshot.worker + ".json")
    finally:
        os.close(fd)


def _baseline(path: Path, snapshot: ProjectSnapshot, live: dict) -> dict:
    try:
        with os.fdopen(os.open(path, _FILE), "rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        baseline = json.loads(data)
        if len(data) > MAX_BYTES or baseline.get("uncertain") or baseline["account"] != snapshot.account or baseline["worker"] != snapshot.worker or baseline["deployment"] != live["deployment"]:
            raise ValueError()
        paths = baseline["asset_paths"]
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
            raise ValueError()
        if not set(paths).issubset(snapshot.asset_paths):
            raise keys.KeyAppError("The project omits previously published assets. Restore every recorded asset path before publishing.")
        return baseline
    except (OSError, ValueError, KeyError, TypeError):
        raise keys.KeyAppError("The owner must adopt a trusted complete copy of the current live project before publishing.") from None


def _save_baseline(path: Path, snapshot: ProjectSnapshot, live: dict, *, uncertain: bool = False):
    value = {"account": snapshot.account, "worker": snapshot.worker, "deployment": live["deployment"], "asset_paths": list(snapshot.asset_paths), "config": snapshot.config, "content_hash": snapshot.content_hash}
    if uncertain:
        value["uncertain"] = True
    fd, temporary = tempfile.mkstemp(prefix=".baseline-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _bound_snapshot(snapshot: ProjectSnapshot, baseline: dict, live: dict) -> ProjectSnapshot:
    digest = hashlib.sha256(_canonical({"project": snapshot.content_hash, "baseline": baseline, "live": {k: live[k] for k in ("settings", "deployment", "domains", "origins")}})).hexdigest()
    return replace(snapshot, content_hash=digest)


def _retained_settings(live: dict) -> bytes:
    return _canonical({"bindings": sorted(live["settings"]["bindings"], key=lambda binding: binding["name"]), "settings": live["preserved"]})


def inspect_project(stored: dict, args: dict, bot_id: str) -> dict:
    snapshot = _snapshot(args, bot_id)
    with _project_lock(snapshot) as path:
        live = _preflight(stored, snapshot)
        snapshot = _bound_snapshot(snapshot, _baseline(path, snapshot, live), live)
    return {"worker": snapshot.worker, "domains": list(snapshot.domains), "content_hash": snapshot.content_hash, "asset_count": len(snapshot.asset_paths)}


def adopt_project(stored: dict, args: dict, bot_id: str) -> dict:
    ""
    if consent.test_mode():
        raise keys.KeyAppError("Test runs cannot adopt or publish Cloudflare projects.")
    snapshot = _snapshot(args, bot_id)
    with _project_lock(snapshot) as path:
        live = _preflight(stored, snapshot)
        if _deployment(stored, snapshot) != live["deployment"]:
            raise keys.KeyAppError("The live deployment changed during adoption. Check the complete project and try again.")
        _save_baseline(path, snapshot, live)
    return {"message": "Recorded the owner's complete asset inventory for the current live deployment."}


def _consume_approval(args: dict, bot_id: str, snapshot: ProjectSnapshot):
    if consent.test_mode():
        raise keys.KeyAppError("Test runs cannot publish Cloudflare projects.")
    fingerprint = hashlib.sha256(json.dumps({"tool": "cloudflare", "args": {k: args.get(k) for k in ("action", "account", "folder", "content_hash")}, "file": snapshot.content_hash}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:32]
    connection = None
    try:
        connection = consent.db()
        connection.execute("BEGIN IMMEDIATE")
        now = time.time()
        row = connection.execute("SELECT id FROM requests WHERE bot_id=? AND fingerprint=? AND kind='publish' AND status='allowed' AND used IS NULL AND decided>? AND decided<=? AND COALESCE(test,0)=0 ORDER BY id DESC LIMIT 1", (bot_id, fingerprint, now - consent.ACTION_ONCE_SEC, now)).fetchone()
        if row is None:
            raise keys.KeyAppError("Approve this exact project publish once before I can continue.")
        connection.execute("UPDATE requests SET used=? WHERE id=? AND used IS NULL", (now, row["id"]))
        connection.commit()
    except sqlite3.Error:
        if connection is not None:
            connection.rollback()
        raise keys.KeyAppError("I couldn't safely check the publish approval. Nothing was published.") from None
    finally:
        if connection is not None:
            connection.close()


def _run_command(command: list[str], *, env: dict, cwd: Path) -> int:
    with subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True) as process:
        try:
            process.communicate(timeout=DEPLOY_TIMEOUT)
        except BaseException:

            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
        return process.returncode


@contextlib.contextmanager
def _run_wrangler(stored: dict, snapshot: ProjectSnapshot, live: dict) -> Iterator[Callable[[], None]]:
    with contextlib.ExitStack() as resources:
        try:
            safe_path = "/usr/local/bin:/opt/homebrew/bin:" + os.defpath
            executable = shutil.which("npx", path=safe_path)
            if not executable:
                raise keys.KeyAppError("The publisher is unavailable. Ask the owner to check its installation.")
            root = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix="zero-cloudflare-")))
            stage = root / "project"
            stage.mkdir()
            for name, data in snapshot.files:
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (stage / "wrangler.json").write_bytes(_canonical({**snapshot.config, **live["preserved"], "routes": [], "workers_dev": False}))
            (stage / "package.json").write_text('{"private":true,"type":"module"}')
            for name in ("home", "tmp", "cache"):
                (root / name).mkdir()
            for name in ("npm-user", "npm-global"):
                (root / name).touch(mode=0o600)
            env = {"PATH": safe_path, "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"), "XDG_CONFIG_HOME": str(root / "home"), "XDG_CACHE_HOME": str(root / "cache"), "CI": "1", "LANG": "C.UTF-8", "WRANGLER_SEND_METRICS": "false", "WRANGLER_LOG_PATH": str(root / "wrangler.log"), "CLOUDFLARE_ACCOUNT_ID": snapshot.account, "npm_config_ignore_scripts": "true", "npm_config_userconfig": str(root / "npm-user"), "npm_config_globalconfig": str(root / "npm-global"), "npm_config_cache": str(root / "cache"), "npm_config_registry": "https://registry.npmjs.org", "npm_config_audit": "false", "npm_config_fund": "false"}
            command = [executable, "--yes", "--package=wrangler@" + WRANGLER_VERSION, "wrangler", "deploy", "--config", "wrangler.json", "--keep-vars"]

            metadata = root / "bundle-meta.json"
            result = _run_command(command + ["--dry-run", "--outdir", str(root / "build"), "--metafile", str(metadata)], env=env, cwd=stage)
            if result != 0:
                raise keys.KeyAppError("The captured project could not be built safely. Nothing was published.")
            inputs = json.loads(metadata.read_bytes())["inputs"]
            if not inputs or not isinstance(inputs, dict):
                raise ValueError()
            allowed = {str((stage / name).resolve()) for name, _ in snapshot.files}
            if any(str((stage / name).resolve()) not in allowed for name in inputs):
                raise keys.KeyAppError("The project imports files outside its captured source. Nothing was published.")
            if consent.test_mode():
                raise keys.KeyAppError("Test runs cannot publish Cloudflare projects.")

            if _canonical(_preflight(stored, snapshot)) != _canonical(live):
                raise keys.KeyAppError("The live project changed before publishing. Check it again.")
            publish_env = {**env, "CLOUDFLARE_API_TOKEN": stored["token"]}
        except keys.KeyAppError:
            raise
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            raise keys.KeyAppError("The captured project could not be prepared safely. Nothing was published.") from None

        def publish() -> None:
            if _run_command(command, env=publish_env, cwd=stage) != 0:
                raise keys.KeyAppError(_UNCERTAIN)

        yield publish


def deploy_project(stored: dict, args: dict, bot_id: str) -> dict:
    if consent.test_mode():
        raise keys.KeyAppError("Test runs cannot publish Cloudflare projects.")
    if args.get("action") != "deploy_project" or not isinstance(args.get("content_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", args["content_hash"]):
        raise keys.KeyAppError("Inspect the complete project and approve its current content before publishing.")
    snapshot = _snapshot(args, bot_id)
    with _project_lock(snapshot) as path:
        live = _preflight(stored, snapshot)
        snapshot = _bound_snapshot(snapshot, _baseline(path, snapshot, live), live)
        if snapshot.content_hash != args["content_hash"]:
            raise keys.KeyAppError("The project or live deployment changed. Inspect it again and approve the new publish.")
        with _run_wrangler(stored, snapshot, live) as publish:
            _consume_approval(args, bot_id, snapshot)
            try:

                _save_baseline(path, snapshot, live, uncertain=True)
                publish()
                current = _preflight(stored, snapshot)
                if current["deployment"] == live["deployment"] or _retained_settings(current) != _retained_settings(live):
                    raise keys.KeyAppError(_UNCERTAIN)
                _api(stored, "PUT", f"/accounts/{snapshot.account}/workers/scripts/{snapshot.worker}/domains/records", json={"origins": current["origins"], "override_scope": True, "override_existing_origin": False, "override_existing_dns_record": False})
                verified = _preflight(stored, snapshot)
                own = {d["hostname"] for d in verified["domains"] if d["service"] == snapshot.worker}
                if own != set(snapshot.domains) or verified["deployment"] != current["deployment"] or _retained_settings(verified) != _retained_settings(live):
                    raise keys.KeyAppError(_UNCERTAIN)
                _save_baseline(path, snapshot, verified)
            except (keys.KeyAppError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):

                raise keys.KeyAppError(_UNCERTAIN) from None
    return {"message": "I published the complete project and checked its sites.", "published": True}


def deploy_worker(stored: dict, args: dict) -> dict:
    raise keys.KeyAppError("Publish the complete project folder so its sites and settings stay connected.")
