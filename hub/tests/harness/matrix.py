"""Runs the hub and plugin suites once per home shape (plan section 7, "Suites That Run for Any Home").

For each shape it lays out a temporary home with homes.make_home (every path inside the temporary folder), sets
ZERO_HOME to its home.json (unset for "none") and ZERO_HOME_SHAPE to the shape's name, and runs pytest over
hub/tests in a fresh process. The report compares the shapes: a test that fails in one shape and passes in another
depends on whose home it runs in, which is the bug this matrix exists to find. Failures that are the same in every
shape are listed once as "fails everywhere" (on the Mac today: the Mac bridge test that only loads on alien, and the
saved-login tests that see an earlier run's state).

  python3 matrix.py [--shapes none,colin,riley] [--python python3.13] [--json out.json] [-- extra pytest args]

Exit status 0 when no test's result depends on the shape (failing everywhere is reported, not fatal), 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HUB = HERE.parents[1]
sys.path.insert(0, str(HERE))

import homes  # noqa: E402

SUMMARY = re.compile(r"^(?:=+ )?(\d+ (?:passed|failed|errors?|skipped|deselected)\b.*?) in [\d.]+s", re.M)
FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)")


def pick_python() -> str:
    for p in (os.environ.get("ZERO_E2E_PYTHON"), shutil.which("python3.13"), sys.executable):
        if p and subprocess.run([p, "-c", "import fastapi, httpx, pytest"], capture_output=True).returncode == 0:
            return p
    return sys.executable


def run_shape(shape: str, python: str, extra: list[str]) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"zero-matrix-{shape}-"))
    try:
        _, home_json = homes.make_home(shape, tmp)
        env = {k: v for k, v in os.environ.items() if k not in ("ZERO_HOME", "ZERO_HOME_SHAPE")}
        env["ZERO_HOME_SHAPE"] = shape
        if home_json:
            env["ZERO_HOME"] = str(home_json)
        started = time.time()
        p = subprocess.run([python, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", "tests", *extra],
                           cwd=HUB, env=env, capture_output=True, text=True)
        out = p.stdout + p.stderr
        m = None
        for m in SUMMARY.finditer(out):
            pass
        failed = sorted({f.group(1) for line in out.splitlines() if (f := FAILED.match(line))})
        return {"shape": shape, "summary": m.group(1) if m else (out.strip().splitlines() or ["no output"])[-1],
                "failed": failed, "exit": p.returncode, "seconds": round(time.time() - started, 1),
                "home_json": str(home_json) if home_json else None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compare(results: list[dict]) -> tuple[list[str], dict[str, list[str]]]:
    """(tests failing in every shape, {test: [shapes where it fails]} for the shape-dependent ones)."""
    sets = {r["shape"]: set(r["failed"]) for r in results}
    every = set.intersection(*sets.values()) if sets else set()
    union = set.union(*sets.values()) if sets else set()
    dependent = {t: sorted(s for s, f in sets.items() if t in f) for t in sorted(union - every)}
    return sorted(every), dependent


def main(argv: list[str]) -> int:
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="The hub and plugin suites, once per home shape.")
    ap.add_argument("--shapes", default="none,colin,riley")
    ap.add_argument("--python", default=None)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    python = a.python or pick_python()
    results = []
    for shape in a.shapes.split(","):
        r = run_shape(shape.strip(), python, extra)
        results.append(r)
        print(f"{r['shape']:<6} {r['summary']}  ({r['seconds']}s)", flush=True)
    every, dependent = compare(results)
    if every:
        print(f"\nfails in every shape ({len(every)}):")
        for t in every:
            print(f"  {t}")
    if dependent:
        print(f"\nresult depends on the home ({len(dependent)}):")
        for t, shapes in dependent.items():
            print(f"  {t}: fails in {', '.join(shapes)}")
    else:
        print("\nno test's result depends on the home shape")
    if a.json:
        Path(a.json).write_text(json.dumps({"python": python, "results": results, "fails_everywhere": every,
                                            "shape_dependent": dependent}, indent=1))
    return 1 if dependent else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
