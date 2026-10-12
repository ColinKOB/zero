"""A faithful copy of Hermes's dangerous-command detector, for the hub's tests (Oct 8 QA round 5, M1).

Where it came from: alien, ~/taffy/hermes (https://github.com/NousResearch/hermes-agent, MIT License, see LICENSE),
commit 74943bb889b7978f9d6ff0aa77ae442547201cc2 (Oct 7 2026), copied unchanged on Oct 8 2026:

  tools/approval_detection.py  sha256 cba0adbbd592661729f7d3865f8c834d57a8b5e6fb5763539695190820a094a3
  tools/ansi_strip.py          sha256 71f339d1be720c24f407565b4a90cb55164dead2e860cf438258104fd96c1922

Not copied: hermes_constants.get_hermes_home (it folds Hermes's own home folder into ~/.hermes before matching) and
cron/lifecycle_guard.py (a last check for gateway restarts spelled with spliced quotes). The detector imports both
inside try blocks and carries on without them, so this copy matches the real one on every command outside those two.

To refresh it after Hermes updates, copy the two files again from ~/taffy/hermes/tools/ and update the hashes and
commit above. hermes_detector.py loads it (or Hermes's own detector, when Hermes is importable).
"""
