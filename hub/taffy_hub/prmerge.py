""













import re
import shlex
import time
from datetime import datetime

_REPO_FLAG = r"(?:\s+(?:-R|--repo)(?:\s+|=)[^\s;&|]+)*"
MERGE = re.compile(rf"(?:^|[\s;&|(`])gh({_REPO_FLAG})\s+pr({_REPO_FLAG})\s+merge\b([^;&|\n]*)")
GATE = "Codex review"
GATE_WORKFLOW = ".github/workflows/codex-gate.yml"
PASSED = {"success", "neutral", "skipped"}
START_SEC = 120
_VALUE_FLAGS = {"-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject", "-A", "--author-email",
                "--match-head-commit"}
_PR_LINK = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
NAME_IT = ("Name the pull request in full, as gh pr merge <number> -R owner/repo or with its link, so its checks can "
           "be read before it merges.")


def target(rest: str) -> tuple[str, int] | None:
    ""
    try:
        words = shlex.split(rest)
    except ValueError:
        return None
    repo, chosen, skip = "", "", False
    for i, word in enumerate(words):
        if skip:
            skip = False
            continue
        if word in ("-R", "--repo"):
            repo, skip = (words[i + 1] if i + 1 < len(words) else ""), True
        elif word.startswith("--repo="):
            repo = word.split("=", 1)[1]
        elif word in _VALUE_FLAGS:
            skip = True
        elif not word.startswith("-") and not chosen:
            chosen = word
    link = _PR_LINK.match(chosen)
    if link:
        return link[1], int(link[2])
    if chosen.isdigit() and _REPO.match(repo):
        return repo, int(chosen)
    return None


def _waiting(get, repo: str, sha: str) -> list[str]:
    ""
    out: list[str] = []
    runs = (get(f"/repos/{repo}/actions/runs", {"head_sha": sha, "per_page": 50}) or {}).get("workflow_runs") or []
    latest: dict[str, dict] = {}
    for run in runs:
        latest.setdefault(str(run.get("name") or run.get("workflow_id")), run)
    for name, run in latest.items():
        if run.get("status") != "completed":
            out.append(f"{name} is still running")
        elif run.get("conclusion") not in PASSED:
            out.append(f"{name} {run.get('conclusion') or 'failed'}")
    statuses = (get(f"/repos/{repo}/commits/{sha}/status") or {}).get("statuses") or []
    seen = set()
    for st in statuses:
        name = str(st.get("context") or "a check")
        seen.add(name)
        if st.get("state") == "pending":
            out.append(f"{name} is still running")
        elif st.get("state") != "success":
            out.append(f"{name} {st.get('state') or 'failed'}")
    if GATE not in seen:
        try:
            gated = bool(get(f"/repos/{repo}/contents/{GATE_WORKFLOW}"))
        except Exception as e:
            if "couldn't find that" not in str(e):
                raise
            gated = False
        if gated:
            out.append(f"{GATE} has not passed yet")
    if not latest and not statuses:
        commit = get(f"/repos/{repo}/commits/{sha}") or {}
        when = str(((commit.get("commit") or {}).get("committer") or {}).get("date") or "")
        try:
            age = time.time() - datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
        except ValueError:
            age = 0
        if age < START_SEC:
            out.append("its checks have not started yet")
    return out


def blocked(command: str, get, heads: list | None = None) -> str | None:
    ""

    for match in MERGE.finditer(command or ""):
        found = target(" ".join(match.groups()))
        if found is None:
            return NAME_IT
        repo, number = found
        pr = get(f"/repos/{repo}/pulls/{number}") or {}
        if pr.get("merged") or pr.get("state") == "closed":
            continue
        sha = str((pr.get("head") or {}).get("sha") or "")
        if not sha:
            return f"{repo}#{number} could not be read, so it was not merged."
        waiting = _waiting(get, repo, sha)
        if waiting:
            return (f"Not merged: {repo}#{number} merges only once every check and the Codex review have passed, and "
                    f"never with --admin. Now: {'; '.join(waiting[:6])}. Wait for them, fix what failed, then merge.")
        if "--match-head-commit" in match[3] and sha not in match[3]:
            return f"Not merged: {repo}#{number}'s checks were read on {sha[:8]}, a different head from the one named."
        if heads is not None:
            heads.append((match.start(3), sha))
    return None


def pinned(command: str, heads: list) -> str:
    ""


    for at, sha in sorted(heads, reverse=True):
        command = f"{command[:at]} --match-head-commit {sha}{command[at:]}"
    return command
