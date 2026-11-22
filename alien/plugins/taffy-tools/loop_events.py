""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import consent, gate, home, mac


def calendar_created(m: dict, result: dict, session_id: str) -> None:
    if not result.get("id"):
        return
    data = {"calendar": m["calendar"], "event": str(result["id"]), "title": m["title"],
            "start": m["start"].isoformat(), "end": m["end"].isoformat()}
    note = {"kind": "calendar_created", "bot": gate._bot(), "args": data}
    folder = home.path("hub_state", consent.REAL_HOME) / "outbox"
    key = hashlib.sha256(json.dumps([note["bot"], data["calendar"], data["event"]]).encode()).hexdigest()
    pending = folder / f"calendar-{key}.json"
    tmp = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".calendar-", dir=folder)
        with os.fdopen(fd, "w") as f:
            json.dump(note, f)
        Path(tmp).replace(pending)
        if mac._call("calendar_created", data, 2, session_id=session_id, area="chat").get("ok"):
            pending.unlink(missing_ok=True)
    except Exception:
        pass
    finally:
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
