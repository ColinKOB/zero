"""Runs the hub suite once per home shape: no settings file, Colin's rendered file, Riley's (shared plan, no
connectors, one Google mailbox) and a stranger's (another time zone, metric units, an IMAP mailbox).

    python tests/run_matrix.py            (from hub/; add -k NAME to pick tests, as unittest does)

Each run is a separate process with ZERO_HOME set (unset for the first), so module-level values are read fresh.
"""

import os
import subprocess
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
SHAPES = [("defaults", None)] + [(n, HUB / "tests" / "homes" / f"{n}.json") for n in ("colin", "riley", "stranger")]


def main() -> int:
    failed = []
    for name, f in SHAPES:
        env = {k: v for k, v in os.environ.items() if k != "ZERO_HOME"}
        env["ZERO_TESTS"] = "1"   # apns.send never pushes to a real phone from a test run
        if f is not None:
            env["ZERO_HOME"] = str(f)
        print(f"== {name}", flush=True)
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-b", "-s", "tests", *sys.argv[1:]],
                           cwd=HUB, env=env)
        if r.returncode:
            failed.append(name)
    print("matrix: " + ("all shapes passed" if not failed else "failed in " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
