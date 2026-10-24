import os
import shutil
import tempfile
from pathlib import Path

# Tests write checks, reports, appeals and seals: keep them out of the real database, vault and seal
# registry (on the demo server they would pollute the stage data). Demo seals are copied in to read.
_state = Path(tempfile.mkdtemp(prefix="pramaan-test-"))
_seals = Path(__file__).resolve().parent.parent / "data" / "seals"
if _seals.exists():
    shutil.copytree(_seals, _state / "seals")
os.environ["PRAMAAN_STATE_DIR"] = str(_state)

# Tests must never poll the real Telegram bot (it would steal updates from a running server).
os.environ["PRAMAAN_DISABLE_BOT"] = "1"
# Heavy detectors are exercised by eval/run.py and tests/test_detectors.py, not by every test.
os.environ.setdefault("PRAMAAN_DETECTORS", "off")
# Tests never visit real websites; tests/test_site_check.py checks the visitor with crafted pages.
os.environ.setdefault("PRAMAAN_SITE_CHECK", "off")
# The semantic matcher has its own tests (test_semantic.py); elsewhere the phrase rules are tested on their own.
os.environ.setdefault("PRAMAAN_SEMANTIC", "off")
os.environ.setdefault("PRAMAAN_PROMPT_GUARD", "off")
# The suite makes many web checks from one client; the limiter itself is tested with its own instance.
os.environ.setdefault("PRAMAAN_WEB_RATE", "100000/600")

# A fresh state folder has no tables yet, even when a single test file runs on its own.
from app.store import db  # noqa: E402

db.init()
