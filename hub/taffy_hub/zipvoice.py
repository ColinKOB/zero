""

from pathlib import Path

from . import config

_REPO_GUIDE = Path(__file__).resolve().parents[2] / "alien/shared/zip-voice.md"
_GUIDE = config.SHARED_DIR / "zip-voice.md"
TEXT = next((p.read_text().strip() for p in (_GUIDE, _REPO_GUIDE) if p.is_file()), "")





DATE_SOURCE = ("When a teammate's date isn't posted where it would be, such as the course page or the invitation, "
               "keep where it came from in a few words when you pass it on (\"due Sunday at 11:59 p.m., going by the "
               "teacher's email\"). A reminder or the end of a unit is not a due date.")



KEEP_DOUBT = ("If this answer gives two numbers, or says something is unverified or unresolved, pass on both numbers and "
              "the doubt, and don't sum it up as fine.")
