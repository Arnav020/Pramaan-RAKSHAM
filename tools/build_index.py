"""Build the Source-Match reference index from data/media/genuine/ + the catalog.

Usage:  python -m tools.build_index
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from app.config import DATA_DIR
from app.lanes.source_match import build_reference, load_catalog, save_index
from app.media.prep import extract_audio, extract_frames, sha256_file
from app.media.stt import transcribe

GENUINE = DATA_DIR / "media" / "genuine"


def main() -> None:
    refs = []
    for c in load_catalog():
        p = GENUINE / c["file"]
        if not p.exists():
            print(f"skip {c['id']}: {p.name} missing")
            continue
        t = time.time()
        frames = extract_frames(p)
        with tempfile.TemporaryDirectory() as td:
            tr = transcribe(extract_audio(p, Path(td) / "a.mp3"), language=c["language"])
        meta = {k: c[k] for k in ("id", "file", "title_en", "title_hi", "language", "published", "speaker")}
        meta["sha256"] = sha256_file(p)
        refs.append(build_reference(frames, {"text": tr.text, "words": tr.words, "segments": tr.segments, "language": tr.language}, meta))
        print(f"{c['id']}: {len(frames)} frames, {len(tr.words)} words ({time.time() - t:.1f}s)")
    if not refs:
        sys.exit("no reference clips found")
    save_index(refs)
    print(f"index saved with {len(refs)} clips")


if __name__ == "__main__":
    main()
