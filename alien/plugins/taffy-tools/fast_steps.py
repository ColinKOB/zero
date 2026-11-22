""










import json
import os
import pwd
import re
import time
import urllib.error
import urllib.request

from . import home, link_click, tiers

MODEL = "gpt-6-luna"
DEFAULT_MIN_CONFIDENCE = 0.4
REAL_HOME = os.environ.get("ZERO_USER_HOME") or pwd.getpwuid(os.getuid()).pw_dir
ENV_FILE = str(home.path("hermes_home", REAL_HOME) / ".env")
CONSEQUENTIAL = re.compile(r"\b(submit|send|pay|purchase|buy|checkout|place order|confirm|delete|remove|publish|post)\b", re.I)


REF_LINE = re.compile(r'^\s*-\s+(?P<role>[^"\[]*)(?:"(?P<name>[^"]*)")?.*?\[ref=(?P<ref>e\d+)\]')



SETTLE_SEC = 2.0
UNCHANGED = "; the page did not change after this step, so it may not have worked. Check the page before saying it is done"

SCHEMA = {
    "name": "browser_steps",
    "description": (
        "Fast path for routine work in your browser. Give a short list of plain-English steps and they run "
        "back to back without a model turn per click, each grounded on the page's accessibility snapshot "
        "by a fast classifier. Step forms: 'go to URL' opens a page; 'type \"TEXT\" into TARGET' fills a "
        "field; 'press KEY' presses a key such as Enter or Tab; any other text clicks the matching "
        "element (for example 'open the Unit 6 forum' or 'click Submit assignment'). The run stops at the "
        "first step it is unsure about and returns what it did plus the current page snapshot, so you can "
        "continue with the normal browser tools. Never use it for steps that send, submit, publish or pay "
        "without Colin's explicit yes. Optional goal: a yes/no question checked against the final page."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25},
            "goal": {"type": "string", "description": "Yes/no question that is true when the task is done."},
            "min_confidence": {"type": "number", "description": f"Stop below this (default {DEFAULT_MIN_CONFIDENCE})."},
        },
        "required": ["steps"],
    },
}

EXACT_SCHEMA = {
    "name": "browser_actions",
    "description": (
        "Prefer this for routine browser clicks and fills. Fill a whole form in one call: one "
        "'type \"TEXT\" into @eN' step per field, with the field refs from your last browser_snapshot. "
        "Steps are 'go to URL', 'click @eN' or 'click \"NAME\"', 'type \"TEXT\" into @eN' or "
        "'type \"TEXT\" into NAME', or 'press KEY'. A ref keeps meaning the element you saw. A name must match "
        "one control; on a page with the same form twice, a name resolves inside the form your other steps use. "
        "Stops on an ambiguous or missing name, a failed action, or a permission request. "
        "Returns the full final snapshot, including noninteractive text, without a separate snapshot call. "
        "Submit, send or pay in its own call after checking the filled values; those still need the person's "
        "explicit approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {"steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25}},
        "required": ["steps"],
    },
}







_ANY_REF_LINE = re.compile(r'^(?P<indent>\s*)-\s+(?P<role>[^"\[\s]+)[^"\[\n]*(?:"(?P<name>[^"\n]*)")?[^\n]*?'
                           r'\[(?:[^\]\n]*?[ ,])?ref=(?P<ref>e\d+)[,\]]')
_ROLE_LINE = re.compile(r'^(?P<indent>\s*)-\s+(?P<role>[^"\[\s:]+)')
_REF_TARGET = re.compile(r'^\s*(?:(?:click|open)\s+)?["\'\[]?\s*(?:ref=)?@?(?P<ref>e\d+)\s*["\'\]]?\s*$', re.I)
CONTROLS = frozenset({"link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "tab",
                      "menuitem", "menuitemcheckbox", "menuitemradio", "option", "slider", "spinbutton", "listbox",
                      "treeitem", "textarea"})
FIELDS = frozenset({"textbox", "searchbox", "combobox", "textarea", "spinbutton"})
_MARK = re.compile(r"\s*(?:\(required\)|\*)\s*$", re.I)


def ref_target(target: str | None) -> str | None:
    ""
    m = _REF_TARGET.match(target or "")
    return "@" + m["ref"] if m else None


def outline(snapshot: str) -> list[dict]:
    ""

    out, stack, count, forms = [], [], {}, 0
    for line in (snapshot or "").splitlines():
        m = _ROLE_LINE.match(line)
        if not m:
            continue
        indent, role = len(m["indent"]), m["role"].lower()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        form = next((f for _i, f in reversed(stack) if f), None)
        if role == "form":
            forms += 1
            stack.append((indent, forms))
        else:
            stack.append((indent, None))
        r = _ANY_REF_LINE.match(line)
        if r:
            key = (role, r["name"] or "")
            count[key] = count.get(key, 0) + 1
            out.append({"ref": "@" + r["ref"], "role": role, "name": r["name"] or "", "form": form,
                        "place": count[key]})
    return out


def _label(node: dict) -> str:
    return f'{node["role"]} "{node["name"]}"' if node["name"] else node["role"]


def exact_find(snapshot: str, kind: str, target: str, forms=()) -> tuple[dict | None, str]:
    ""

    name = re.sub(r'^\s*(?:click|open)\s+', '', target, flags=re.I).strip()
    if len(name) >= 2 and name[0] == name[-1] == '"':
        name = name[1:-1]
    want = name.casefold()
    nodes = outline(snapshot)
    found = [n for n in nodes if n["name"] and n["name"].casefold() == want] or \
        [n for n in nodes if n["name"] and _MARK.sub("", n["name"]).casefold() == want]
    for keep in (lambda n: n["role"] in CONTROLS, lambda n: kind != "type" or n["role"] in FIELDS,
                 lambda n: n["form"] in forms):
        if len(found) > 1 and any(keep(n) for n in found):
            found = [n for n in found if keep(n)]
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, f"no control is named {name!r}"
    where = ", ".join(n["ref"] + (f" in form {n['form']}" if n["form"] else "") for n in found[:6])
    return None, f"{name!r} matches {len(found)} controls ({where}); use the ref of the one you mean"


def exact_pick(snapshot: str, target: str, done: list[str]) -> tuple[str | None, float]:
    ""
    node, _why = exact_find(snapshot, "click", target)
    return (node["ref"], 1.0) if node else (None, 0.0)


def _api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        with open(ENV_FILE) as fh:
            for line in fh:
                if line.startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


JUDGE_TIMEOUT_SEC = 3


def _owner() -> str:
    ""
    return str(home.path("owner", pwd.getpwuid(os.getuid()).pw_dir))


def paid_calls_on() -> bool:
    ""



    return home.judge_enabled() and os.path.exists(os.path.join(_owner(), "paid-decisions-on"))


def browser_steps_on() -> bool:
    ""


    return paid_calls_on() and os.path.exists(os.path.join(_owner(), "browser-steps-on"))


def _post(body: dict, timeout: float) -> list[dict]:
    if not paid_calls_on():
        raise RuntimeError("Paid decision calls are off")
    key = _api_key()
    if not key:
        raise RuntimeError("No OPENAI_API_KEY for the Decisions API")
    req = urllib.request.Request("https://api.openai.com/v1/decisions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["answers"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Decisions API error {e.code}: {e.read().decode()[:200]}")


def _decide(body: dict) -> dict:
    return _post(body, 30)[0]


def decide_one(body: dict, timeout: float = JUDGE_TIMEOUT_SEC) -> dict:
    ""
    return _post(body, timeout)[0]


def probability(a: dict) -> float | None:
    ""

    if not isinstance(a, dict) or a.get("type") == "refusal":
        return None
    p = a.get("probability")
    return float(p) if isinstance(p, (int, float)) else None


def choice(a: dict) -> tuple[str | None, float]:
    ""
    if not isinstance(a, dict) or a.get("type") != "choice":
        return None, 0.0
    return a.get("choice"), float(a.get("confidence") or 0.0)


def elements(snapshot: str) -> list[dict]:
    out, seen = [], set()
    for line in snapshot.splitlines():
        m = REF_LINE.match(line)
        if m and m["ref"] not in seen:
            seen.add(m["ref"])
            role = m["role"].strip().rstrip(":")
            label = f'{role} "{m["name"]}"' if m["name"] else role
            out.append({"value": "@" + m["ref"], "description": label[:200]})
    return out


def pick(snapshot: str, target: str, done: list[str]) -> tuple[str | None, float]:
    choices = elements(snapshot)
    if not choices:
        return None, 0.0
    a = _decide({
        "model": MODEL,
        "input": f"Accessibility snapshot of a web page:\n{snapshot[:60000]}\n\n"
                 f"Steps already done: {'; '.join(done) or 'none'}\nNext step: {target}",
        "questions": [{"type": "choice", "name": "element",
                       "instructions": "Which element ref should be clicked or typed into to carry out the next step?",
                       "choices": choices[:400]}],
    })
    return choice(a)


def check(snapshot: str, goal: str) -> float | None:
    a = _decide({"model": MODEL, "input": f"Accessibility snapshot of a web page:\n{snapshot[:60000]}",
                 "questions": [{"type": "predicate", "name": "done", "instructions": goal}]})
    return probability(a)


def parse(step: str) -> tuple[str, str | None, str | None]:
    m = re.match(r'^\s*(?:go to|open|navigate to)\s+(https?://\S+)\s*$', step, re.I)
    if m:
        return "goto", None, m.group(1)
    m = re.match(r'^\s*type\s+"(.*)"\s+(?:into|in)\s+(.+)$', step, re.I | re.S)
    if m:
        return "type", m.group(2), m.group(1)
    m = re.match(r'^\s*press\s+(?:the\s+)?(?:key\s+)?([A-Za-z]+(?:\+[A-Za-z]+)*)\s*$', step, re.I)
    if m:
        return "key", None, m.group(1)
    return "click", step, None


class Sensitive(Exception):
    ""


class Blocked(Exception):
    ""




_SECRET_VALUE = re.compile(r"^\d{4,8}$|^(?:\d[ -]?){11,18}\d$|^\d{3}-?\d{2}-?\d{4}$")
_SECRET_LABEL = re.compile(r"password|passcode|pass\s*phrase|\bcode\b|\bpin\b|cvv|cvc|security|card number|"
                           r"account number|routing|ssn|social security|secret|token|api key|private key", re.I)
_FIELD_LINE = re.compile(r'^(?P<head>\s*-\s+(?:textbox|searchbox|combobox|textarea|spinbutton)\b[^"\n]*"(?P<name>[^"]*)"'
                         r'[^:\n]*:\s*)(?P<value>.+)$', re.I | re.M)
HIDDEN = "[hidden]"


def looks_secret(value: str, label: str = "") -> bool:
    ""
    v = (value or "").strip()
    if not v:
        return False
    if _SECRET_LABEL.search(label or "") or _SECRET_VALUE.match(v):
        return True
    if re.search(r"\s", v) or "@" in v or "://" in v or len(v) < 6:
        return False
    return bool(re.search(r"[A-Za-z]", v) and re.search(r"\d", v))


def redact(text: str, secrets: list[str]) -> str:
    ""
    out = text or ""
    for v in sorted({s for s in secrets if s and len(s) >= 3}, key=len, reverse=True):
        out = out.replace(v, HIDDEN)
    return _FIELD_LINE.sub(lambda m: m["head"] + HIDDEN if _SECRET_LABEL.search(m["name"]) else m.group(0), out)


def _shown(kind: str, target: str | None, value: str | None, step: str) -> str:
    ""

    if kind == "type":
        return f"type into {target}"
    if kind == "goto":
        return "go to " + re.sub(r"[?#].*$", "", value or "")
    return step


def refusal(steps: list) -> str | None:
    ""

    gone = False
    for i, step in enumerate(steps or [], 1):
        kind, target, _value = parse(str(step))
        if kind == "type" and tiers.SECRET_FIELD.search(target or ""):
            return f"browser_steps did not run: step {i} types into a password or code field. " + tiers.TYPE_SECRET_NEVER
        if kind != "goto" and gone and ref_target(target):
            return (f"browser_steps did not run: step {i} names a ref after a 'go to'. A ref names an element of the "
                    "page you last read, so open the page, read it, then send the steps with its refs.")
        gone = gone or kind == "goto"
    return None


def unchanged(before: str, read, sleep=time.sleep, clock=time.monotonic) -> bool:
    ""

    deadline = clock() + SETTLE_SEC
    while True:
        try:
            if read() != before:
                return False
        except Exception:
            return False
        if clock() >= deadline:
            return True
        sleep(0.5)


def run(dispatch, args: dict, page=None, *, picker=None, tool_name="browser_steps") -> str:
    ""



    steps = args.get("steps") or []
    parsed = [parse(str(s)) for s in steps]
    refused = refusal(steps)
    if refused:
        return refused
    secrets = [v for kind, target, v in parsed if kind == "type" and looks_secret(v or "", target or "")]

    def show(step: str) -> str:
        return redact(step, secrets)

    threshold = max(0.3, float(args.get("min_confidence", DEFAULT_MIN_CONFIDENCE) or DEFAULT_MIN_CONFIDENCE))
    t0 = time.time()

    def call(name: str, a: dict) -> str:
        out = dispatch(name, a)
        try:
            data = json.loads(out)
        except (TypeError, ValueError):
            return str(out)
        if isinstance(data, dict) and (data.get("error") or data.get("success") is False or data.get("ok") is False):
            raise RuntimeError(f"{name}: {data.get('error') or data.get('message') or 'The action failed.'}")
        if isinstance(data, dict):
            return data.get("snapshot") or data.get("content") or data.get("result") or json.dumps(data)
        return str(data)

    no_change = []
    log, done, said, stopped = [], [], [], None
    exact = picker is exact_pick
    anchors: dict[str, dict] = {}
    forms: set = set()
    current = None
    typed = False

    def snapshot() -> str:
        nonlocal current
        snap, current = current, None
        return snap if snap is not None else call("browser_snapshot", {})

    def anchor(snap: str) -> None:
        ""
        nodes = {n["ref"]: n for n in outline(snap)}
        for _k, target, _v in parsed:
            ref = ref_target(target)
            if ref and ref not in anchors and ref in nodes:
                anchors[ref] = nodes[ref]
                if nodes[ref]["form"]:
                    forms.add(nodes[ref]["form"])

    for i, (step, (kind, target, value)) in enumerate(zip(steps, parsed), 1):
        step = str(step)
        try:
            if kind == "goto":
                current = None
                call("browser_navigate", {"url": value})
                log.append(f"{i}. {show(step)}: opened")
            elif kind == "key":
                current = None
                call("browser_press", {"key": value})
                log.append(f"{i}. {show(step)}: pressed {value}")
            else:
                snap = snapshot()
                if not anchors and any(ref_target(t) for _k, t, _v in parsed):
                    anchor(snap)
                named = ref_target(target)
                node, why = None, ""
                if named:
                    was = anchors.get(named)
                    node = next((n for n in outline(snap) if was and (n["role"], n["name"], n["place"]) ==
                                 (was["role"], was["name"], was["place"])), None)
                    why = f"{named} is not on the page now; read the page again for its refs"
                elif exact:
                    node, why = exact_find(redact(snap, secrets), kind, show(target or ""), forms)
                if named or exact:
                    ref, conf = (node["ref"], 1.0) if node else (None, 0.0)
                    if node is None:
                        stopped = f"Stopped before step {i} ({show(step)!r}): {why}."
                        break
                else:
                    ref, conf = (picker or pick)(redact(snap, secrets), show(target or ""), said)
                if not ref or conf < threshold:
                    stopped = (f"Stopped before step {i} ({show(step)!r}): unsure which element (best guess {ref}, "
                               f"confidence {conf:.2f}).")
                    break
                label = _label(node) if node else \
                    next((e["description"] for e in elements(snap) if e["value"] == ref), "")
                if node and node["form"]:
                    forms.add(node["form"])
                if kind == "type" and tiers.SECRET_FIELD.search(label):
                    stopped = f"Stopped before step {i} ({show(step)!r}): {label} is a password or code field. " + \
                        tiers.TYPE_SECRET_NEVER
                    break
                if kind == "click" and typed and (CONSEQUENTIAL.search(label) or CONSEQUENTIAL.search(step)):


                    stopped = (f"Stopped before step {i} ({show(step)!r}): the form is filled. Check the values in "
                               f"the page below, then press {label} ({ref}) with browser_click in its own call.")
                    break
                if kind == "click" and CONSEQUENTIAL.search(label) and not CONSEQUENTIAL.search(step):
                    stopped = (f"Stopped before step {i} ({show(step)!r}): {label!r} looks like a send, submit, pay or "
                               "delete button. Say that action explicitly in the step, and only with Colin's yes.")
                    break

                opened = None
                if kind == "type":
                    call("browser_type", {"ref": ref, "text": value})
                    typed = True
                elif page is not None:
                    _out, opened = link_click.click(ref, lambda: call("browser_click", {"ref": ref}), page,
                                                    lambda url: call("browser_navigate", {"url": url}))
                else:
                    call("browser_click", {"ref": ref})
                after = []

                def read() -> str:
                    after.append(call("browser_snapshot", {}))
                    return after[-1]
                same = not opened and unchanged(snap, read)
                current = after[-1] if after and not opened else None
                if same:
                    no_change.append(i)
                log.append(f"{i}. {show(step)}: {ref} (confidence {conf:.2f})"
                           + (f"; the click did not open the link, so its page was opened directly: {opened}"
                              if opened else "") + (UNCHANGED if same else ""))
            done.append(step)
            said.append(show(_shown(kind, target, value, step)))
        except Sensitive:
            raise
        except Blocked as e:
            stopped = f"Stopped before step {i} ({show(step)!r}): {e}"
            break
        except Exception as e:
            stopped = f"Stopped at step {i} ({show(step)!r}): {show(str(e))}"
            break




    try:
        final = redact(call("browser_snapshot", {"full": True}), secrets)
    except Exception as e:
        final = f"(could not read the page: {show(str(e))})"
    lines = [f"{tool_name}: {len(done)} of {len(steps)} steps done in {time.time() - t0:.1f} s."
             + (f" The page did not change after step{'s' * (len(no_change) > 1)} {', '.join(map(str, no_change))}."
                if no_change else "")] + log
    if stopped:
        lines.append(stopped)
    elif args.get("goal"):
        try:
            p = check(final, args["goal"])
            lines.append(f"Goal check ({args['goal']}): " + (f"probability {p:.2f}." if p is not None else "no answer."))
        except Exception as e:
            lines.append(f"Goal check failed: {e}")
    lines.append("\nCurrent page snapshot:\n" + final)
    return "\n".join(lines)
