"""Evaluation scripts. Importing this package isolates them from the live system.

Every measurement runs in a fresh, empty state folder (database, vault, campaigns), so campaigns confirmed during a demo
rehearsal can never leak into a measured verdict as "campaign memory". Only the demo Seal registry is copied in, read-only
in spirit, because some red-team attacks forge or reuse those seals. Set PRAMAAN_STATE_DIR yourself to override.
"""

import os
import shutil
import tempfile
from pathlib import Path

if "PRAMAAN_STATE_DIR" not in os.environ:
    _state = Path(tempfile.mkdtemp(prefix="pramaan-eval-"))
    _seals = Path(__file__).resolve().parent.parent / "data" / "seals"
    if _seals.exists():
        shutil.copytree(_seals, _state / "seals")
    os.environ["PRAMAAN_STATE_DIR"] = str(_state)
