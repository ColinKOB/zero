""







import json
import mimetypes
import os
import re
import uuid
from pathlib import Path

from . import consent, gate, home, mac, safe_files, sitepreview

MEDIA = None
FILE_MAX = 25 * 1024 * 1024
ASSET_MAX = 3 * 1024 * 1024
BUNDLE_MAX = 12 * 1024 * 1024
PAGES = (".html", ".htm")
PICTURES = (".png", ".jpg", ".jpeg", ".gif", ".webp")

SCHEMA = {
    "name": "share_result",
    "description": (
        "Show Colin a finished web page or file in your chat as one card: for a web page (an .html file you made) it "
        "has a clean full-page picture of the page, Open and Open in Safari, and Publish when you can publish it; for "
        "any file it has the file to save or share. Use it when you finish a site, a document or any file he asked for, "
        "instead of a screenshot of your browser. Text he is meant to read (a message, a draft, a note, a list) goes "
        "in your reply itself, never in a .txt or .md file shared here: files are for things that can't be a message. "
        "A schedule, a plan, amounts over time or a job's steps go on a card (the card tool), never a page made for it. "
        "Give the file's full path in your own work folder or the shared "
        "folder. A page that uses its own images, styles or scripts from the same folder is fine. Colin sees the "
        "card under your reply, so keep your reply to one or two short sentences about what you made and don't "
        "describe the page again. Set can_publish only when you really can publish this page (you have a hosting "
        "account connected); his tap on Publish asks you to publish it."
    ),
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "description": "The file: a full path starting with / or ~/."},
        "title": {"type": "string", "description": "What it is, in two to five words (\"Desk Prints homepage\"). "
                                                     "Shown on the card."},
        "can_publish": {"type": "boolean", "description": "True only if you can publish this page when asked."},
    }, "required": ["path"]},
}

_LINK = re.compile(r"<link\b[^>]*>", re.I)
_SCRIPT = re.compile(r"<script\b([^>]*)>\s*</script>", re.I)
_MEDIA_TAG = re.compile(r"<(?:img|source|video|audio|input|track)\b[^>]*>", re.I)
_MEDIA_ATTR = re.compile(r"\b(src|poster|srcset)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>\"']+)", re.I)
_DEFER = re.compile(r"\s+defer\b(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?", re.I)
_SRC_ATTR = re.compile(r"\s+src\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>\"']+)", re.I)
_ATTR = re.compile(r"([\w:-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>\"']+)", re.I)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def _media() -> Path:
    return MEDIA or home.path("hub_state", consent.REAL_HOME) / "media"


def _local(ref: str) -> bool:
    ref = ref.strip()
    return bool(ref) and not re.match(r"(?i)^([a-z][a-z0-9+.-]*:|//|#)", ref)


_URL = re.compile(r"url\(\s*([\"']?)([^\"')]+)\1\s*\)", re.I)
_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?[\"']?([^\"')\s;]+)[\"']?\s*\)?([^;]*);", re.I)
_IMPORTS = re.compile(r"""((?:\bfrom\s*|\bimport\s*\(?\s*)[\"'])(\.{0,2}/[^\"']+)([\"'])""")
_INLINE_MODULE = re.compile(r"(<script\b(?=[^>]*\btype\s*=\s*[\"']?module\b)(?![^>]*\bsrc\s*=)[^>]*>)(.*?)(</script>)", re.I | re.S)
_STYLE_ATTR = re.compile(r"(\bstyle\s*=\s*([\"']))(.*?)\2", re.I | re.S)
_STYLE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)


def bundle(html: str, folder: str, roots, real_home: str) -> tuple[str, bool]:
    ""


    import base64
    spent = [0]
    changed = [False]

    def read(ref: str, base: str, limit: int = ASSET_MAX) -> tuple[bytes, str] | None:
        if not _local(ref):
            return None
        from urllib.parse import unquote
        target = os.path.normpath(os.path.join(base, unquote(ref.split("#")[0].split("?")[0])))
        try:
            with safe_files.open_file(target, roots, real_home) as f:
                data = f.read(limit + 1)
        except (safe_files.Refused, OSError):
            return None
        if len(data) > limit or spent[0] + len(data) > BUNDLE_MAX:
            return None
        spent[0] += len(data)
        changed[0] = True
        return data, os.path.dirname(target)

    def data_uri(ref: str, base: str) -> str | None:
        got = read(ref, base)
        if got is None:
            return None
        path = ref.split("#")[0].split("?")[0]
        fragment = "#" + ref.split("#", 1)[1] if "#" in ref else ""
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return f"data:{kind};base64,{base64.b64encode(got[0]).decode()}{fragment}"

    def css(text: str, base: str, depth: int = 0) -> str:
        def imported(m):
            condition = m.group(2).strip()
            if re.search(r"\b(supports|layer)\b", condition, re.I):
                return m.group(0)
            got = read(m.group(1), base) if depth < 3 else None
            if got is None:
                return m.group(0)
            inner = css(got[0].decode("utf-8", "replace"), got[1], depth + 1)
            return f"@media {condition}{{{inner}}}" if condition else inner

        def url(m):
            uri = data_uri(m.group(2).strip(), base)
            return m.group(0) if uri is None else f"url({uri})"
        return _URL.sub(url, _IMPORT.sub(imported, text)).replace("</", "<\\/")

    def link(m):
        attrs = {k.lower(): v.strip("\"'") for k, v in _ATTR.findall(m.group(0))}
        if "stylesheet" not in attrs.get("rel", "").lower().split() or "href" not in attrs:
            return m.group(0)
        got = read(attrs["href"], folder)
        if got is None:
            return m.group(0)
        media = attrs.get("media", "").replace('"', "&quot;")
        opening = f'<style media="{media}">' if media else "<style>"
        return opening + css(got[0].decode("utf-8", "replace"), got[1]) + "</style>"

    def style(m):
        return m.group(1) + css(m.group(2), folder) + m.group(3)

    deferred: list[str] = []

    def module(text: str, base: str, depth: int = 0) -> str:
        ""
        def repl(m):
            got = read(m.group(2), base) if depth < 4 else None
            if got is None:
                return m.group(0)
            inner = module(got[0].decode("utf-8", "replace"), got[1], depth + 1)
            return f"{m.group(1)}data:text/javascript;base64,{base64.b64encode(inner.encode()).decode()}{m.group(3)}"
        return _IMPORTS.sub(repl, text)

    def script(m):
        attrs = {k.lower(): v.strip("\"'") for k, v in _ATTR.findall(m.group(1))}
        if "src" not in attrs:
            return m.group(0)
        got = read(attrs["src"], folder)
        if got is None:
            return m.group(0)
        kept = _SRC_ATTR.sub("", m.group(1), count=1)
        is_module = attrs.get("type", "").lower() == "module"
        code = got[0].decode("utf-8", "replace")
        if is_module:
            code = module(code, got[1])
        code = code.replace("</", "<\\/")
        if _DEFER.search(kept) and not is_module:

            deferred.append(f"<script{_DEFER.sub('', kept)}>{code}</script>")
            return ""
        return f"<script{kept}>{code}</script>"

    def media_tag(m):
        def attr(a):
            name, raw = a.group(1).lower(), a.group(2)
            quote = raw[0] if raw[0] in "\"'" else '"'
            value = raw.strip("\"'")
            if name == "srcset":
                if "data:" in value:
                    return a.group(0)
                parts = []
                for candidate in value.split(","):
                    bits = candidate.split()
                    uri = data_uri(bits[0], folder) if bits else None
                    parts.append(" ".join([uri or bits[0]] + bits[1:]) if bits else candidate)
                return f"{name}={quote}{', '.join(parts)}{quote}"
            uri = data_uri(value, folder)
            return a.group(0) if uri is None else f"{name}={quote}{uri}{quote}"
        return _MEDIA_ATTR.sub(attr, m.group(0))

    def inline_style(m):
        return m.group(1) + _URL.sub(lambda u: _data_url(u, folder), m.group(3)) + m.group(2)

    def _data_url(u, base):
        uri = data_uri(u.group(2).strip(), base)
        return u.group(0) if uri is None else f"url({uri})"

    linked = _LINK.sub(link, html)
    out = _MEDIA_TAG.sub(media_tag, _SCRIPT.sub(script, _STYLE.sub(style, linked)))
    out = _STYLE_ATTR.sub(inline_style, out)
    out = _INLINE_MODULE.sub(lambda m: m.group(1) + module(m.group(2), folder) + m.group(3), out)
    if deferred:
        end = out.lower().rfind("</body>")
        out = (out[:end] + "".join(deferred) + out[end:]) if end >= 0 else out + "".join(deferred)
    return out, changed[0]


def share_result(args: dict, **kwargs) -> str:
    bot = gate._bot()
    path = str(args.get("path") or "").strip()
    title = " ".join(str(args.get("title") or "").split())[:60]
    real_home = str(consent.REAL_HOME)
    roots = safe_files.zip_roots(consent.REAL_HOME, bot)
    try:
        real = safe_files.resolve(path, roots, real_home)
        with safe_files.open_file(real, roots, real_home) as f:
            data = f.read(FILE_MAX + 1)
    except safe_files.Refused as e:
        return _err(f"Only files in your own work folder or the shared folder can be shown ({e}).")
    if len(data) > FILE_MAX:
        return _err("That file is larger than 25 MB, too large to put in a card.")
    name = os.path.basename(path.rstrip("/")) or "file"
    suffix = Path(name).suffix.lower()
    media, media_id = _media(), uuid.uuid4().hex
    media.mkdir(parents=True, exist_ok=True)

    def write(suffix_: str, content: bytes) -> None:
        target = media / f"{media_id}{suffix_}"
        target.write_bytes(content)
        os.chmod(target, 0o600)

    write(".file", data)
    card = {"media": media_id, "name": name, "size": len(data), "title": title, "kind": "file", "page": False,
            "preview": False, "can_publish": bool(args.get("can_publish")) and suffix in PAGES}
    (media / f"{media_id}.meta.json").write_text(json.dumps({"name": name}))
    os.chmod(media / f"{media_id}.meta.json", 0o600)
    if suffix in PAGES:
        card["kind"] = "site"
        text, changed = bundle(data.decode("utf-8", "replace"), os.path.dirname(real), roots, real_home)
        write(".html", text.encode())
        if changed:
            write(".file", text.encode())
            card["size"] = len(text.encode())
        card["page"] = True
        try:
            w, h = sitepreview.render(str(media / f"{media_id}.html"), str(media / f"{media_id}.jpg"))
            card.update(preview=True, width=w, height=h)
        except Exception as e:
            card["preview_error"] = str(e)[:200]
    elif suffix in PICTURES:
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            img.load()
            img = img.convert("RGB")
            if img.width > 1200:
                img = img.resize((1200, round(img.height * 1200 / img.width)))
            img.save(media / f"{media_id}.jpg", quality=84)
            os.chmod(media / f"{media_id}.jpg", 0o600)
            card.update(preview=True, width=img.width, height=img.height)
        except Exception:
            pass
    card.pop("preview_error", None)
    reply = mac._call("result_card", card, 20, session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        for suffix_ in (".file", ".html", ".jpg", ".meta.json"):
            (media / f"{media_id}{suffix_}").unlink(missing_ok=True)
        return _err(str(reply.get("error") or "Could not show the card."))
    note = "The card is in Colin's chat under your reply."
    if card["kind"] == "site" and not card["preview"]:
        note += " It has no picture of the page this time, only Open, Open in Safari and the file."
    if card["can_publish"]:
        note += " If he taps Publish, he asks you to publish it."
    out = {"ok": True, "shared": home.personalize(note)}
    if reply.get("made"):
        out["made"] = reply["made"]
        words = re.sub(r"[\[\]()]", "", title) or "it"
        out["shared"] += (" To point back to it in a later reply, link a few words to it: "
                          f"[{words}](zero-item:{reply['made']}).")
    return json.dumps(out)
