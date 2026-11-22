""

from pathlib import Path

from . import home

_REPO_GUIDE = Path(__file__).resolve().parents[2] / "shared/zip-voice.md"
_GUIDE = home.path("shared") / "zip-voice.md"


def text():
    ""
    return next((p.read_text().strip() for p in (_GUIDE, _REPO_GUIDE) if p.is_file()), "")
