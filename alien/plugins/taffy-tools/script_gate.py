""














import ast
import re




_GLOBALS = {"document", "window", "JSON", "Math", "Array", "Object", "String", "Number", "Boolean", "Date", "Set",
            "Map", "URL", "URLSearchParams", "parseInt", "parseFloat", "isNaN", "isFinite", "getComputedStyle",
            "location", "navigator", "Intl", "RegExp", "Symbol", "NodeFilter", "Node", "Element", "HTMLElement",
            "console", "performance", "innerWidth", "innerHeight", "scrollX", "scrollY", "devicePixelRatio",
            "FormData", "DOMParser", "TextEncoder", "TextDecoder", "Blob", "XPathResult"}
_KEYWORDS = {"const", "let", "var", "return", "if", "else", "for", "of", "in", "while", "do", "break", "continue",
             "new", "typeof", "instanceof", "null", "true", "false", "undefined", "function", "this", "void",
             "switch", "case", "default", "try", "catch", "finally", "async", "await", "NaN", "Infinity"}



_READ_METHODS = {
    "querySelector", "querySelectorAll", "getElementById", "getElementsByClassName", "getElementsByTagName",
    "getElementsByName", "getAttribute", "getAttributeNames", "hasAttribute", "closest", "matches", "contains",
    "getBoundingClientRect", "getClientRects", "item", "namedItem", "checkValidity", "evaluate", "iterateNext",
    "snapshotItem", "createTreeWalker", "nextNode", "map", "filter", "forEach", "find", "findIndex", "some",
    "every", "reduce", "slice", "join", "split", "trim", "trimStart", "trimEnd", "toLowerCase", "toUpperCase",
    "includes", "indexOf", "lastIndexOf", "startsWith", "endsWith", "replace", "replaceAll", "match", "matchAll",
    "test", "exec", "search", "substring", "substr", "charAt", "padStart", "padEnd", "repeat", "concat", "flat",
    "flatMap", "sort", "reverse", "at", "keys", "values", "entries", "from", "isArray", "stringify", "parse",
    "toString", "toFixed", "toISOString", "parseFromString", "getAll", "toLocaleString", "toLocaleDateString", "toLocaleTimeString", "getTime",
    "now", "round", "floor", "ceil", "max", "min", "abs", "has", "get", "fromEntries", "freeze",
    "getPropertyValue", "trunc", "sign", "pow", "sqrt",

    "getEntriesByType", "getEntriesByName", "getEntries", "elementFromPoint", "elementsFromPoint", "checkVisibility",
    "hasFocus",
    "localeCompare", "normalize", "getFullYear", "getMonth", "getDate", "getDay", "getHours", "getMinutes",
    "DateTimeFormat", "NumberFormat", "format", "describe"}
_READ_PROPS = _READ_METHODS | {
    "body", "head", "title", "forms", "links", "images", "tables", "rows", "cells", "options", "elements",
    "documentElement", "innerText", "textContent", "innerHTML", "outerHTML", "value", "checked", "selected",
    "selectedIndex", "disabled", "readOnly", "required", "type", "name", "id", "className", "classList", "dataset",
    "href", "src", "alt", "placeholder", "label", "labels", "htmlFor", "action", "method", "target", "rel",
    "children", "childNodes", "parentElement", "parentNode", "nextElementSibling", "previousElementSibling",
    "firstElementChild", "lastElementChild", "firstChild", "lastChild", "nextSibling", "previousSibling",
    "childElementCount", "tagName", "nodeName", "nodeType", "nodeValue", "data", "length", "size", "pathname",
    "hostname", "host", "origin", "protocol", "search", "hash", "port", "top", "left", "right", "bottom", "width",
    "height", "x", "y", "offsetWidth", "offsetHeight", "offsetTop", "offsetLeft", "clientWidth", "clientHeight",
    "scrollWidth", "scrollHeight", "scrollTop", "scrollLeft", "display", "visibility", "opacity", "color",
    "language", "userAgent", "readyState", "URL", "referrer", "cookieEnabled", "lang", "dir", "content",
    "naturalWidth", "naturalHeight", "currentSrc", "duration", "currentTime", "paused", "validity", "valid",
    "validationMessage", "maxLength", "min", "max", "step", "pattern", "accept", "multiple", "files", "tBodies",
    "tHead", "tFoot", "caption", "rowIndex", "cellIndex", "colSpan", "rowSpan", "text", "defaultValue",
    "ariaLabel", "role", "isConnected", "hidden", "style", "sheet", "cssText", "relList", "nonce", "baseURI",
    "PI", "E", "MAX_SAFE_INTEGER", "FIRST_ORDERED_NODE_TYPE", "ORDERED_NODE_SNAPSHOT_TYPE", "SHOW_TEXT",
    "SHOW_ELEMENT", "snapshotLength", "singleNodeValue", "stringValue", "numberValue", "currentNode", "prototype",
    "scrollX", "scrollY", "pageXOffset", "pageYOffset", "innerWidth", "innerHeight", "outerWidth", "outerHeight",
    "devicePixelRatio", "screen", "availWidth", "availHeight", "ok", "status", "statusText", "url", "headers",
    "redirected", "complete", "loading", "decoding", "srcset", "sizes", "media", "responseStatus", "transferSize",
    "encodedBodySize", "decodedBodySize", "initiatorType", "entryType", "startTime", "responseEnd",
    "nextHopProtocol", "characterSet", "contentType", "lastModified", "visibilityState", "activeElement",
    "offsetParent", "fontSize", "fontFamily", "fontWeight", "lineHeight", "backgroundColor", "position", "zIndex",
    "overflow", "overflowX", "overflowY", "margin", "padding", "border", "textAlign", "ariaHidden", "ariaExpanded",
    "ariaChecked", "ariaSelected", "ariaDisabled"}



_OWN_CHANGES = {"push", "unshift", "add", "set"}

_FILL_PROPS = {"value", "checked", "selected", "selectedIndex"}


_CALLABLE_GLOBALS = {"parseInt", "parseFloat", "isNaN", "isFinite", "String", "Number", "Boolean", "Array", "Date",
                     "RegExp", "Symbol", "getComputedStyle", "BigInt", "FormData", "DOMParser", "URL",
                     "URLSearchParams", "Set", "Map", "TextDecoder", "TextEncoder"}

_GLOBAL_FUNCTIONS = {"fetch", "open", "close", "alert", "confirm", "prompt", "eval", "setTimeout", "setInterval",
                     "postMessage", "requestAnimationFrame", "queueMicrotask", "XMLHttpRequest", "WebSocket",
                     "EventSource", "Image", "Function", "navigator", "location", "window", "document", "self", "top",
                     "parent", "frames", "globalThis", "opener", "history", "localStorage", "sessionStorage",
                     "indexedDB", "caches", "crypto", "name", "status", "print", "stop", "focus", "blur", "scroll",
                     "scrollTo", "scrollBy", "importScripts", "structuredClone", "showModalDialog"}






_FETCH_OPTIONS = {"method", "credentials", "cache", "redirect", "mode"}
_URL_TERM = r"(?:STR\d+|\d+|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[[\w$]+\])*)"


def _closing(code: str, i: int) -> int:
    ""
    depth = 0
    for j in range(i, len(code)):
        depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(code[j], 0)
        if depth == 0:
            return j
    return len(code)


def _opening(code: str, i: int) -> int:
    ""
    depth = 0
    for j in range(i, -1, -1):
        depth += {")": 1, "]": 1, "}": 1, "(": -1, "[": -1, "{": -1}.get(code[j], 0)
        if depth == 0:
            return j
    return -1


def _top_level(text: str) -> list[str]:
    ""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    return [p.strip() for p in parts + [text[start:]]]






_ADDRESS_CHARS = re.compile(r"[A-Za-z0-9/._~%?#=&+,;:@!$*()'-]*")


def _own_address(fixed: str, whole: bool, same_site) -> bool:
    ""

    if not _ADDRESS_CHARS.fullmatch(fixed):
        return False
    if fixed.startswith("/"):
        return len(fixed) > 1 and fixed[1] != "/"
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", fixed):
        m = re.match(r"https?://([A-Za-z0-9.-]+)(?::\d{1,5})?([/?#]|$)", fixed, re.I)
        return bool(m) and (whole or bool(m.group(2))) and same_site(f"https://{m.group(1)}/")
    return whole or re.search(r"[/?#]", fixed) is not None


def _fetch_chain(code: str, start: int, close: int) -> set[int]:
    ""



    found = set()
    if re.search(r"\(\s*await\s+$", code[:start]):
        m = re.match(r"\s*\)\s*\.\s*(text|json)\s*\(\s*\)", code[close + 1:])
        if m:
            found.add(close + 1 + m.start(1))
        return found
    i, first = close, True
    while m := re.match(r"\s*\.\s*(then|catch)\s*\(", code[i + 1:]):
        found.add(i + 1 + m.start(1))
        paren = i + m.end()
        body = re.match(r"\s*([A-Za-z_$][\w$]*)\s*=>\s*\1\s*\.\s*(text|json)\s*\(\s*\)\s*\)", code[paren + 1:])
        if body and first and m.group(1) == "then":
            found.add(paren + 1 + body.start(2))
        i, first = _closing(code, paren), False
    return found


def _fetch_reads(args: str, strings: list[str], same_site) -> bool:
    ""
    parts = _top_level(args)
    if len(parts) > 2:
        return False



    url = re.fullmatch(rf"(location\s*\.\s*origin\s*\+\s*)?STR(\d+)((?:\s*\+\s*{_URL_TERM})*)", parts[0])
    if not url:
        return False
    fixed, adds = strings[int(url.group(2))], bool(url.group(3))
    if url.group(1):
        if not (re.match(r"/(?!/)", fixed) and _ADDRESS_CHARS.fullmatch(fixed)):
            return False
    elif not _own_address(fixed, not adds, same_site) or (adds and not re.match(r"https?://", fixed, re.I)):
        return False
    if len(parts) == 1:
        return True
    options = re.fullmatch(r"\{(.*)\}", parts[1], re.S)
    if not options:
        return False
    for item in _top_level(options.group(1)):
        pair = re.fullmatch(r"(?:([A-Za-z_$][\w$]*)|STR(\d+))\s*:\s*STR(\d+)", item)
        if item and not pair:
            return False
        if pair:
            key, value = pair.group(1) or strings[int(pair.group(2))], strings[int(pair.group(3))]
            if key not in _FETCH_OPTIONS or (key == "method" and value.strip().upper() not in ("GET", "HEAD")):
                return False
    return True


_STRING = re.compile(r"""'(?:[^'\\\n]|\\[^xu\n])*'|"(?:[^"\\\n]|\\[^xu\n])*"|`(?:[^`\\$]|\\[^xu]|\$(?!\{))*`""")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


_FRESH = re.compile(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(?:\[\s*\]|\{\s*\}|new\s+(?:Set|Map)\s*\((?:[^()]|\([^()]*\))*\))"
                    r"\s*(?=[;,\n]|$)")


def _own_lists(code: str, others: set[str]) -> dict[str, tuple[int, int]]:
    ""


    own = {}
    for m in _FRESH.finditer(code):
        name = m.group(1)



        bindings = re.findall(rf"(?:\b(?:const|let|var|function)\s+|,\s*){re.escape(name)}\s*(?==(?!=)|[,;)]|$)", code)
        made_by = re.match(r"new\s+(Set|Map)", code[m.end(1):].split("=", 1)[1].strip())
        if name in others or name in _GLOBAL_FUNCTIONS or re.search(r"\bfor\s*\(\s*$", code[:m.start()]) or \
                len(bindings) != 1 or (made_by and re.search(rf"\b(?:const|let|var|function|class)\s+{made_by[1]}\b|"
                                                           rf"[(,]\s*{made_by[1]}\s*[,)=]", code)):
            continue
        depth, i = 0, m.start() - 1
        while i >= 0:
            depth += {"}": 1, "{": -1}.get(code[i], 0)
            if depth < 0:
                break
            i -= 1
        own[name] = (m.end(), _closing(code, i) if i >= 0 else len(code))
    return own


def _on_own(code: str, at: int, own: dict) -> str:
    ""
    m = re.search(r"([A-Za-z_$][\w$]*)\s*(?:\?\.\s*)?\.?\s*$", code[:at])
    if not m or m.group(1) not in own or code[:m.start()].rstrip().endswith((".", "?.")):
        return ""
    start, end = own[m.group(1)]
    return m.group(1) if start <= m.start() < end else ""


def js_verdict(js: str, same_site=lambda url: False) -> str:
    ""

    js = str(js or "")
    if not js.strip():
        return "read"
    if re.search(r"\\[xu]|\$\{", js):
        return "act"
    if re.search(r"[\w$)\]]\s*`", js):
        return "act"
    if "/*" in js or re.search(r"`[^`]*\n[^`]*`", js):


        return "act"
    for m in re.finditer(r"(?<![\w$.])location\b", js):


        if not re.match(r"\s*(\?\.|\.)\s*(href|pathname|hostname|host|origin|protocol|search|hash|port)\b(?!\s*(=(?!=)|\())",
                        js[m.end():]):
            return "act"
    strings: list[str] = []

    def keep(m):
        strings.append(m.group(0)[1:-1])
        return f" STR{len(strings) - 1} "
    code = _STRING.sub(keep, js)
    if re.search(r"['\"`]", code):
        return "act"





    regex = re.compile(r"/(?![/*\s])(?:\\.|\[[^\]\n]*\]|[^/\n\\\[])+/[dgimsuy]*")
    out, i = [], 0
    while True:
        m = regex.search(code, i)
        if not m:
            out.append(code[i:])
            break
        before = code[:m.start()].rstrip()
        starts = not before or before[-1] in "=(,:!&|?{};[+-*%<>~^" or re.search(r"\b(return|typeof|case|in|of)$", before)
        if not starts:
            out.append(code[i:m.start() + 1])
            i = m.start() + 1
            continue
        if ";" in m.group(0) or re.search(r"\.[A-Za-z_$][\w$]*\s*\(", m.group(0)):
            return "act"
        out.append(code[i:m.start()] + " RX ")
        i = m.end()
    code = "".join(out)
    code = re.sub(r"//[^\n]*", " ", code)
    fetches = set()
    for m in re.finditer(r"(?<![\w$])fetch\s*\(", code):
        if code[:m.start()].rstrip().endswith("."):
            continue
        close = _closing(code, m.end() - 1)
        if not _fetch_reads(code[m.end():close], strings, same_site):
            return "act"
        fetches |= {m.start()} | _fetch_chain(code, m.start(), close)
    declared = set(re.findall(r"\b(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", code))
    declared |= set(re.findall(r"([A-Za-z_$][\w$]*)\s*=>", code))
    for params in re.findall(r"\(([^()]*)\)\s*=>", code) + re.findall(r"function\s*[\w$]*\s*\(([^()]*)\)", code):
        declared |= set(_IDENT.findall(params))
    for group in re.findall(r"\b(?:const|let|var)\s*\[([^\]]*)\]", code):
        declared |= set(_IDENT.findall(group))
    aliases: dict[str, str] = {}
    for group in re.findall(r"\b(?:const|let|var)\s*\{([^}]*)\}", code):
        for part in group.split(","):
            source, _, local = part.partition(":")
            source, local = source.strip().split("=")[0].strip(), (local or source).strip().split("=")[0].strip()
            if not source:
                continue
            if source not in _READ_PROPS:
                return "act"
            if _IDENT.fullmatch(local):
                declared.add(local)
                aliases[local] = source
    for local, source in aliases.items():
        if source not in _READ_METHODS and re.search(rf"(?<![\w$.]){re.escape(local)}\s*(\?\.\s*)?\(", code):
            return "act"
    declared |= set(re.findall(r"\bcatch\s*\(\s*([A-Za-z_$][\w$]*)", code))
    others = set(re.findall(r"([A-Za-z_$][\w$]*)\s*=>", code)) | set(aliases) | \
        set(re.findall(r"\bcatch\s*\(\s*([A-Za-z_$][\w$]*)", code))
    for params in re.findall(r"\(([^()]*)\)\s*=>", code) + re.findall(r"function\s*[\w$]*\s*\(([^()]*)\)", code) + \
            re.findall(r"\b(?:const|let|var)\s*[\[{]([^\]}]*)[\]}]", code):
        others |= set(_IDENT.findall(params))
    own = _own_lists(code, others)


    written = {n for n in own if re.search(rf"(?<![\w$.]){re.escape(n)}\s*(?:\?\.\s*)?(?:\.\s*[A-Za-z_$][\w$]*|\[[^\]]*\])"
                                           r"\s*(?:=(?!=|>)|\+=|-=|\|\|=|&&=|\?\?=|\+\+|--)", code)}
    for n in written:
        if re.search(rf"(?<![\w$.]){re.escape(n)}\s*(?:\?\.\s*)?\.\s*[A-Za-z_$][\w$]*\s*(?:\?\.\s*)?\(", code):
            return "act"
    if declared & _GLOBAL_FUNCTIONS:
        return "act"
    if "let" in declared:
        return "act"
    functions = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", code))
    functions |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^()]*\)|[A-Za-z_$][\w$]*)"
                                r"\s*=>", code))
    functions |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*function\b", code))

    undeclared = re.sub(r"\b(?:const|let|var)\s*[\[{][^\]}]*[\]}]\s*=", " ", code)
    for m in re.finditer(r"[}\])]\s*(=(?!=|>)|\+=|-=|\|\|=|&&=|\?\?=)", undeclared):
        opened = _opening(undeclared, m.start()) if undeclared[m.start()] == "]" else -1
        if opened < 0 or not _on_own(undeclared, opened, own):
            return "act"

    if re.search(r"\.(classList|relList|part|sandbox)\s*(\?\.|\.)\s*replace\b", code):
        return "act"
    filled = False
    for m in _IDENT.finditer(code):
        name, start, end = m.group(0), m.start(), m.end()
        if re.fullmatch(r"STR\d+|RX", name) or start in fetches:
            continue
        if code[end:].lstrip().startswith(":") and code[:start].rstrip()[-1:] in ("{", ","):
            continue
        before = code[:start].rstrip()
        after = code[end:].lstrip()
        is_prop = before.endswith(".") and not before.endswith("...")
        calls = after.startswith("(")
        assigns = bool(re.match(r"(=(?!=|>)|\+=|-=|\*=|/=|\|\|=|&&=|\?\?=|\+\+|--)", after))
        if is_prop:
            if _on_own(code, start, own) and ((calls and name in _OWN_CHANGES) or (assigns and name not in _OWN_CHANGES)):
                continue
            if name not in _READ_PROPS or (calls and name not in _READ_METHODS):
                return "act"
            if assigns:
                if name in _FILL_PROPS:
                    filled = True
                    continue
                return "act"
        else:
            if name not in _GLOBALS and name not in _KEYWORDS and name not in declared:
                return "act"
            if calls and name not in _KEYWORDS and name not in _CALLABLE_GLOBALS and name not in functions:
                return "act"
            if assigns and (before.endswith("]") or name not in declared):
                return "act"


    for m in re.finditer(r"\]\s*(\?\.\s*)?\(", code):
        depth, i = 0, m.start()
        while i >= 0:
            if code[i] == "]":
                depth += 1
            elif code[i] == "[":
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        inner = code[i + 1:m.start()].strip() if i >= 0 else ""
        if not (re.fullmatch(r"STR\d+", inner) and strings[int(inner[3:])] in _READ_METHODS):
            return "act"


    for m in re.finditer(r"\)\s*(\?\.\s*)?\(", code):
        opened = _opening(code, m.start())
        before, inner = code[:opened].rstrip(), code[opened + 1:m.start()].strip()
        grouped = not re.search(r"[\w$\])]$", before) or re.search(
            r"\b(?:if|while|for|switch|return|typeof|void|await|in|of|case|do|else|catch)$", before)
        if not grouped or len(_top_level(inner)) != 1 or \
                not re.match(r"(?:async\b\s*)?(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)", inner):
            return "act"



    for m in re.finditer(r"\[", code):
        if not re.search(r"[\w$)\]]\s*(\?\.\s*)?$", code[:m.start()]) or \
                re.search(r"(?:^|[;{}\n]|\bfor\s*(?:await\s*)?\()\s*(?:const|let|var)\s*$", code[:m.start()]):
            continue
        depth, j = 0, m.start()
        while j < len(code):
            if code[j] == "[":
                depth += 1
            elif code[j] == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner = code[m.start() + 1:j].strip()
        if _on_own(code, m.start(), own) and not re.match(r"\s*(\?\.\s*)?\(", code[j + 1:]):
            continue
        if not re.fullmatch(r"STR\d+|\d+|-?\d+|[A-Za-z_$][\w$]*", inner):
            return "act"

    for m in re.finditer(r"\[\s*([^\[\]]*?)\s*\]", code):
        inner, after = m.group(1), code[m.end():].lstrip()
        if _on_own(code, m.start(), own) and not after.startswith("("):
            continue
        if re.fullmatch(r"STR\d+", inner):
            key = strings[int(inner[3:])]
            if key not in _READ_PROPS or (after.startswith("(") and key not in _READ_METHODS):
                return "act"
            if re.match(r"=(?!=|>)", after):
                if key in _FILL_PROPS:
                    filled = True
                    continue
                return "act"
        elif after.startswith("(") and inner:
            return "act"
        elif re.match(r"=(?!=|>)|\+=|-=", after) and not re.search(r"\b(?:const|let|var)\s*$", code[:m.start()]):
            return "act"
    return "fill" if filled else "read"




READ_HELPERS = {"page_info", "capture_screenshot", "list_tabs", "current_tab", "wait", "wait_for_load",
                "wait_for_element", "wait_for_network_idle", "scroll", "new_tab", "goto_url", "switch_tab",
                "activate_tab", "close_tab", "ensure_real_tab", "iframe_target", "drain_events"}
FILL_HELPERS = {"type_text", "fill_input", "upload_file"}
ACT_HELPERS = {"click_at_xy", "press_key", "dispatch_key", "js", "cdp"}
HELPERS = READ_HELPERS | FILL_HELPERS | ACT_HELPERS
SAFE_MODULES = {"json", "re", "time", "math", "datetime"}

SAFE_CALLS = {"print", "len", "range", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "frozenset",
              "sorted", "reversed", "enumerate", "zip", "min", "max", "sum", "any", "all", "abs", "round",
              "isinstance", "map", "filter", "repr", "divmod", "chr", "ord", "hex", "bytes", "type", "iter", "next",
              "callable", "Exception", "ValueError", "KeyError", "TypeError", "RuntimeError", "IndexError",
              "TimeoutError", "AttributeError", "StopIteration"}
SUBMIT_KEYS = {"enter", "return", "numpadenter", " ", "space", "spacebar"}


def _const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _js_text(node, loop_ints: set[str]) -> str | None:
    ""

    s = _const_str(node)
    if s is not None:
        return s
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and v.conversion == ord("r"):
                out.append('"x"')
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in loop_ints:
                out.append("0")
            else:
                return None
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _js_text(node.left, loop_ints), _js_text(node.right, loop_ints)
        return left + right if left is not None and right is not None else None
    return None


_WEB = re.compile(r"\s*https?://[^/\s{}'\"]+", re.I)


def _web_address(node, tree, depth: int = 0) -> bool:
    ""

    if depth > 3:
        return False
    s = _const_str(node)
    if s is not None:
        return bool(_WEB.match(s))
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        return isinstance(first, ast.Constant) and bool(_WEB.match(str(first.value)))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _web_address(node.left, tree, depth + 1)
    if isinstance(node, ast.Name):
        values = []
        for n in ast.walk(tree):
            if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == node.id:
                it = n.iter
                if isinstance(it, (ast.List, ast.Tuple)):
                    values += it.elts
                else:
                    values.append(it)
            elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets):
                values += n.value.elts if isinstance(n.value, (ast.List, ast.Tuple)) else [n.value]
        return bool(values) and all(_web_address(v, tree, depth + 1) for v in values)
    return False


def exec_verdict(code: str, cdp_reads, same_site=lambda url: False) -> tuple[str, list[str]]:
    ""

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return "act", [code]
    loop_ints = {t.id for f in ast.walk(tree) if isinstance(f, ast.For) and isinstance(f.target, ast.Name)
                 and isinstance(f.iter, ast.Call) and isinstance(f.iter.func, ast.Name) and f.iter.func.id == "range"
                 for t in [f.target]}
    verdict, scripts = "read", []

    def worse(v):
        nonlocal verdict
        order = {"read": 0, "fill": 1, "act": 2}
        if order[v] > order[verdict]:
            verdict = v


    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] not in SAFE_MODULES for a in n.names):
            worse("act")
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] not in SAFE_MODULES:
            worse("act")
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for node in ast.walk(tree):


        if isinstance(node, ast.Name) and node.id in HELPERS and id(node) not in called:
            worse("act")
        if isinstance(node, ast.Name) and node.id in ("exec", "eval", "compile", "getattr", "setattr", "globals",
                                                      "locals", "vars", "__import__", "open", "breakpoint"):
            worse("act")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            worse("act")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            worse("act")
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func, ast.Name):
            worse("act")
            continue
        name = node.func.id
        if name not in HELPERS and name not in SAFE_CALLS and name not in defined:
            worse("act")
            continue
        if name in ("goto_url", "new_tab"):
            if not node.args or not _web_address(node.args[0], tree):
                worse("act")
        if name in FILL_HELPERS:
            worse("fill")
        elif name in ("press_key", "dispatch_key"):
            key = _const_str(node.args[0]) if node.args else None
            worse("fill" if key is not None and key.strip().lower() not in SUBMIT_KEYS else "act")
        elif name == "click_at_xy":
            worse("act")
        elif name == "js":
            text = _js_text(node.args[0], loop_ints) if node.args else None
            if text is None:
                worse("act")
                scripts.append(ast.unparse(node))
                continue
            scripts.append(text)
            worse(js_verdict(text, same_site))
        elif name == "cdp":
            method = _const_str(node.args[0]) if node.args else None
            if method is None or not cdp_reads(method) or len(node.args) > 1 or \
                    any(kw.arg is None for kw in node.keywords):
                worse("act")
                continue
            if any(kw.arg in ("url", "URL") and not re.match(r"https?://", _const_str(kw.value) or "https://")
                   for kw in node.keywords):
                worse("act")
            for kw in node.keywords:
                if kw.arg in ("expression", "functionDeclaration", "source"):
                    text = _js_text(kw.value, loop_ints)
                    if text is None:
                        worse("act")
                    else:
                        scripts.append(text)
                        worse(js_verdict(text, same_site))
    return verdict, scripts
