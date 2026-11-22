""

















import json
import re

QUOTE_MAX = 1200
_DO = re.compile(r"^[ \t*_#-]*do:", re.I | re.M)
_DONE = re.compile(r"^[ \t*_#-]*done(?: when)?:", re.I | re.M)

_asked: set[tuple[str, str]] = set()


def asked_for(body: str) -> str:
    ""
    text = str(body or "").strip()
    found = _DO.search(text)
    if found:
        text = text[found.start():]
    if len(text) <= QUOTE_MAX:
        return text
    done = _DONE.search(text)
    tail = text[done.start():][:QUOTE_MAX // 2].rstrip() if done else ""
    return f"{text[:QUOTE_MAX - len(tail)].rstrip()} ...\n{tail}".rstrip()





_SUPERSEDED = re.compile(r"\b(?:supersed\w*|duplicate\w*|replaced)\b[^\n]{0,120}?\b(t_[0-9a-f]{6,})\b", re.I)


def superseded_by(summary: str, task: str, open_task) -> str | None:
    ""
    for m in _SUPERSEDED.finditer(str(summary or "")):
        other = m.group(1)
        if other != task and open_task(other):
            return other
    return None


def send_back(task: str, run: str, status: str, body: str, summary: str = "", open_task=None, *,
              private: bool = False, judge=None) -> str | None:
    ""


    words = asked_for(body)
    if status != "running" or (task, run) in _asked:
        return None
    if open_task is not None and superseded_by(summary, task, open_task):
        return None
    _asked.add((task, run))
    if private:
        if not words:
            return None



        return (f"Not closed yet: a one-time check against what this task asked for:\n\n{words}\n\nFinish what "
                "you can and call kanban_complete again naming each as done or not done. If your summary does, send it "
                "unchanged. " + CHANGED)
    try:
        gap = " ".join(str((judge or (lambda _s: ""))(summary) or "").split())
    except Exception:
        gap = ""
    if not gap:
        return None
    return (f"Not closed yet. {gap} Do that, then call kanban_complete again. If it can't be done, say so in the "
            "summary and why. " + CHANGED)


CHANGED = "If you changed something, read it back, link it in the summary and picture it with share_screen."




JUDGE_SEC = 45


def _under_tests() -> bool:
    ""
    import os
    import sys
    from pathlib import Path
    started = sys.argv[0] if sys.argv else ""
    return os.environ.get("ZERO_TESTS") == "1" or "unittest" in started or "tests" in Path(started).parts


def hub_judge(task: str, session_id: str, args: dict):
    ""

    def judge(summary: str) -> str:
        if _under_tests():
            return ""
        extra = {k: v for k, v in args.items() if k not in ("summary", "result", "task_id")}
        if extra:
            summary += "\n\nAlso handed in with the close: " + json.dumps(extra, ensure_ascii=False)[:3000]
        from . import mac
        reply = mac._call("close", {"task_id": task, "summary": summary}, JUDGE_SEC, session_id=session_id,
                          area="finish-judge")
        found = (reply.get("result") or {}) if reply.get("ok") is True else {}
        return str(found.get("gap") or "")
    return judge
