"""A home for a test: writes a home.json into a temporary folder and points the hub's reader at it.

    with home_fixture.use("riley"):            # a sample from tests/homes/
        ...
    home_fixture.use_in(self, "stranger")      # in setUp; undone by addCleanup
    home_fixture.use_in(self, {"person": {"name": "Sam"}})   # laid over Colin's defaults (home_id "colin" added)

The suite as a whole runs for any home: set ZERO_HOME to one of tests/homes/*.json (or any file) before running it
(tests/run_matrix.py runs all three). A test that checks one home's facts pins that home with use_in; every other
test passes whatever ZERO_HOME says.
"""

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import home  # noqa: E402

HOMES = Path(__file__).resolve().parent / "homes"
SAMPLES = ("colin", "riley", "stranger")


def sample(name: str) -> dict:
    return json.loads((HOMES / f"{name}.json").read_text())


@contextlib.contextmanager
def use(settings: str | dict | None):
    """`settings`: a sample's name, a dict laid over the defaults, or None for no file at all."""
    saved = home.FILE
    folder = Path(tempfile.mkdtemp(prefix="home-"))
    f = folder / "home.json"
    if isinstance(settings, str):
        f.write_text(json.dumps(sample(settings)))
    elif isinstance(settings, dict):
        f.write_text(json.dumps(settings if "home_id" in settings else {"home_id": "colin", **settings}))
    home.FILE = f
    home.reset_cache()
    saved_env = os.environ.get("ZERO_HOME")
    os.environ["ZERO_HOME"] = str(f)
    try:
        yield f
    finally:
        home.FILE = saved
        home.reset_cache()
        if saved_env is None:
            os.environ.pop("ZERO_HOME", None)
        else:
            os.environ["ZERO_HOME"] = saved_env


def use_in(test, settings: str | dict | None) -> Path:
    cm = use(settings)
    f = cm.__enter__()
    test.addCleanup(cm.__exit__, None, None, None)
    return f


_pinned: list = []


def pin(settings: str | dict | None = None) -> None:
    """For setUpModule in a test file whose tests check one home's facts (Colin's mailboxes, connectors, name and
    time zone by default): the whole module runs in that home, whatever ZERO_HOME says. unpin() in tearDownModule."""
    cm = use(settings)
    cm.__enter__()
    _pinned.append(cm)


def unpin() -> None:
    if _pinned:
        _pinned.pop().__exit__(None, None, None)
