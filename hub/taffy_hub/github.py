""












import base64
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import home, oauth, who

API = "https://api.github.com"
GIT = "https://github.com"
CLONE_SEC = 170
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
LIMIT_MAX = 50
TEXT_MAX = 20000
REF = re.compile(r"^(?:https://github\.com/)?([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})(?:#|/(?:issues|pull)/)"
                 r"([0-9]{1,9})")


def _clean(v, limit: int = 300) -> str:
    return " ".join(str(v or "").split())[:limit]


def _get(path: str, params: dict | None = None, token: str | None = None):
    return oauth.request("github", "GET", f"{API}{path}", token=token, params=params, headers=HEADERS).json()


def _post(path: str, body: dict):
    return oauth.request("github", "POST", f"{API}{path}", json=body, headers=HEADERS).json()


def me(token: str | None = None) -> str:
    u = _get("/user", token=token)
    return _clean(u.get("login"), 80)


def _limit(n, default: int = 20) -> int:
    try:
        return max(1, min(int(n or default), LIMIT_MAX))
    except (TypeError, ValueError):
        return default


def repos(limit: int = 30) -> list[dict]:
    ""
    rows = _get("/user/repos", {"sort": "pushed", "per_page": _limit(limit, 30)})
    return [{"repo": r.get("full_name"), "private": bool(r.get("private")),
             "description": _clean(r.get("description"), 200), "url": r.get("html_url"),
             "open_issues": r.get("open_issues_count"), "pushed": r.get("pushed_at")} for r in rows or []]


def _item(i: dict) -> dict:
    repo = "/".join(str(i.get("repository_url") or "").split("/")[-2:])
    return {"ref": f"{repo}#{i.get('number')}" if repo else str(i.get("number")), "title": _clean(i.get("title"), 300),
            "kind": "pull request" if i.get("pull_request") else "issue", "state": i.get("state"),
            "by": _clean((i.get("user") or {}).get("login"), 80), "url": i.get("html_url"),
            "updated": i.get("updated_at")}


def search(query: str, limit: int = 20) -> list[dict]:
    ""
    q = _clean(query, 250)
    if not q:
        raise oauth.OAuthError("Say what to look for.")
    r = _get("/search/issues", {"q": q, "per_page": _limit(limit), "sort": "updated"})
    return [_item(i) for i in r.get("items") or []]


def issue(ref: str) -> dict:
    ""
    m = REF.match(str(ref or "").strip())
    if not m:
        raise oauth.OAuthError("Give the issue as owner/repo#12, or its link.")
    owner, repo, num = m.groups()
    i = _get(f"/repos/{owner}/{repo}/issues/{num}")
    out = _item({**i, "repository_url": f"{API}/repos/{owner}/{repo}"})
    out["body"] = str(i.get("body") or "")[:TEXT_MAX]
    comments = _get(f"/repos/{owner}/{repo}/issues/{num}/comments", {"per_page": 100}) if i.get("comments") else []
    out["comments"] = [{"by": _clean((c.get("user") or {}).get("login"), 80), "at": c.get("created_at"),
                        "text": str(c.get("body") or "")[:1000]} for c in (comments or [])[-20:]]
    return out


REPO = re.compile(r"^(?:https://github\.com/)?([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?$")


def _git(args: list[str], env: dict | None = None, token: str = "", doing: str = "copy the repository") -> str:
    try:
        r = subprocess.run(["git", *args], env=env, capture_output=True, text=True, timeout=CLONE_SEC)
    except subprocess.TimeoutExpired:
        raise oauth.OAuthError(f"git took too long to {doing}. Try again in a few minutes.") from None
    if r.returncode != 0:
        why = " ".join((r.stderr.replace(token, "") if token else r.stderr or "").split())[-200:]
        raise oauth.OAuthError(f"git could not {doing}: {why}" if why else f"git could not {doing}.")
    return r.stdout.strip()


def _signed_in() -> tuple[str, dict]:
    ""

    token = oauth.access_token("github")
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return token, {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": f"http.{GIT}/.extraheader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}


def clone(repo: str, root: Path) -> dict:
    ""







    m = REPO.match(str(repo or "").strip())
    if not m or {".", ".."} & set(m.groups()):
        raise oauth.OAuthError("Give the repository as owner/name, or its link.")
    owner, name = m.groups()
    url = f"{GIT}/{owner}/{name}.git"
    dest = root / "repos" / name


    if dest.parent.is_symlink() or not dest.parent.resolve().is_relative_to(root.resolve()):
        raise oauth.OAuthError(f"{dest.parent} must be a plain folder in your own folder. Remove the link, then copy "
                               f"{owner}/{name} again.")
    if dest.exists() or dest.is_symlink():
        raise oauth.OAuthError(f"{dest} is already there. To get newer commits, rename it (your changes stay in it), "
                               f"then copy {owner}/{name} again.")
    token, env = _signed_in()
    with tempfile.TemporaryDirectory(prefix="zero-clone-") as tmp:
        work = Path(tmp) / name
        _git(["clone", "--quiet", "--depth", "1", url, str(work)], env, token)
        branch = _git(["-C", str(work), "rev-parse", "--abbrev-ref", "origin/HEAD"]).removeprefix("origin/")
        commit = _git(["-C", str(work), "rev-parse", "--short", "origin/HEAD"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), str(dest))
    return {"repo": f"{owner}/{name}", "folder": str(dest), "branch": branch, "commit": commit,
            "how": (f"The latest commit of {branch} only. This copy cannot push. To get newer commits, rename this "
                    "folder, then copy the repository again.")}



PROD_BRANCH = re.compile(r"^(main|master|prod|production|release|live|gh-pages|trunk)$", re.I)
BRANCH = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,200}(?<![./])(?<!\.lock)$")


def _work_folder(raw: str, roots: list[Path]) -> Path:
    ""

    raw = str(raw or "").strip()
    path = Path(raw.removeprefix("/workspace/") if raw.startswith("/workspace/") else raw).expanduser()
    if not str(path) or str(path) == ".":
        raise oauth.OAuthError("Give folder: the repository's folder in your work folder.")
    homes = [r.resolve() for r in roots]
    path = (homes[0] / path).resolve() if not path.is_absolute() else path.resolve()
    if not any(path == r or r in path.parents for r in homes):
        raise oauth.OAuthError(f"{path} is not in your work folder or your team's folder.")
    dot = path / ".git"
    if dot.is_symlink() or not dot.is_dir():
        raise oauth.OAuthError(f"{path} is not a git repository with its own .git folder.")
    return path


def _origin(path: Path) -> str:
    ""

    plain = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    url = subprocess.run(["git", "config", "--file", str(path / ".git" / "config"), "remote.origin.url"], env=plain,
                         capture_output=True, text=True, timeout=10).stdout.strip()
    for host in (f"{GIT}/", "https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.startswith(host):
            return url.removeprefix(host).removesuffix("/").removesuffix(".git").lower()
    return ""


def draft_pr(repo: str, folder: str, branch: str, title: str, body: str, roots: list[Path]) -> dict:
    ""







    m = REPO.match(str(repo or "").strip())
    if not m or {".", ".."} & set(m.groups()):
        raise oauth.OAuthError("Give the repository as owner/name, or its link.")
    owner, name = m.groups()
    if f"{owner}/{name}".lower() in home.runs_on_mac():
        raise oauth.OAuthError(f"{owner}/{name} runs a pull request's code on {who.first()}'s Mac, so a pull request "
                               f"there is for {who.first()} to open. Tell {who.first()} the branch and what it "
                               "changes.")
    branch = str(branch or "").strip().removeprefix("refs/heads/")
    if not BRANCH.match(branch):
        raise oauth.OAuthError("Give branch: the name of the branch you made, such as zip/pageviews-utm.")
    publishes = (f"{branch} is a branch that can publish. Put your change on a branch of its own (git switch -c "
                 "zip/<what-it-does>), then open the pull request from that branch.")
    if PROD_BRANCH.match(branch):
        raise oauth.OAuthError(publishes)
    title = _clean(title, 200)
    if len(title) < 4:
        raise oauth.OAuthError("Give the pull request a title: what the change does, in a short sentence.")
    path = _work_folder(folder, roots)
    info = _get(f"/repos/{owner}/{name}")


    if not info.get("private") or _origin(path) != f"{owner}/{name}".lower():
        raise oauth.OAuthError(f"A pull request goes only to the private repository {path} was copied from, and "
                               f"{owner}/{name} is public or is not its origin. Check the name with git remote -v.")
    base = str(info.get("default_branch") or "main")
    if branch == base:
        raise oauth.OAuthError(publishes)
    with tempfile.TemporaryDirectory(prefix="zero-push-") as tmp:
        bare = Path(tmp) / "push.git"
        plain = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}



        _git(["init", "--quiet", "--bare", str(bare)], plain, doing="get your branch")
        _git(["--git-dir", str(bare), "fetch", "--quiet", "--no-tags", "--update-shallow", str(path),
              f"refs/heads/{branch}:refs/heads/{branch}"], plain, doing=f"get {branch} from {path}")
        commit = _git(["--git-dir", str(bare), "rev-parse", "--short", f"refs/heads/{branch}"], plain)


        try:
            _git(["--git-dir", str(bare), "fetch", "--quiet", "--no-tags", "--update-shallow", str(path),
                  f"refs/remotes/origin/{base}:refs/remotes/origin/{base}"], plain)
            _git(["--git-dir", str(bare), "merge-base", "--is-ancestor", f"refs/heads/{branch}", f"refs/remotes/origin/{base}"],
                 plain)
        except oauth.OAuthError:
            pass
        else:
            raise oauth.OAuthError(f"{branch} has no commits that {base} doesn't already have, so there is nothing to "
                                   f"merge. Commit your change on {branch} in {path}, then call draft_pr again.")
        token, env = _signed_in()
        _git(["--git-dir", str(bare), "push", "--quiet", f"{GIT}/{owner}/{name}.git",
              f"refs/heads/{branch}:refs/heads/{branch}"], env, token,
             doing=f"push {branch} (if GitHub already has a different {branch}, use a new branch name)")
    open_ = _get(f"/repos/{owner}/{name}/pulls", {"head": f"{owner}:{branch}", "state": "open", "per_page": 1})
    ask = {"title": title, "head": branch, "base": base, "body": str(body or "")[:TEXT_MAX], "draft": True}
    if open_:
        pr = open_[0]
    else:
        try:
            pr = _post(f"/repos/{owner}/{name}/pulls", ask)
        except oauth.OAuthError as e:
            if e.state != oauth.CONNECTED:
                raise


            pr = _post(f"/repos/{owner}/{name}/pulls", {**ask, "draft": False})
    return {"repo": f"{owner}/{name}", "number": pr.get("number"), "url": pr.get("html_url"),
            "draft": bool(pr.get("draft")), "branch": branch, "base": base, "commit": commit,
            "next": (f"Merging is {who.first()}'s press on GitHub. Ask for it once: on a team, team_need kind do with "
                     "item set to this link, and in its detail say whether merging puts it live (for example 'Merging "
                     "this puts it live on podguard.app') or what ships it after the merge; set your milestone's link "
                     "to this link too. To change the pull request, commit on the same branch and call draft_pr "
                     "again.")}
