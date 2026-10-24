"""Quick command-line check:  python -m app.cli "message text"  [--no-llm] [--json]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.pipeline import CheckInput, run_check

_MARK = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--media", nargs="*", default=[], help="video/audio/image/pdf files")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = asyncio.run(run_check(CheckInput(text=a.text, media_paths=[Path(m) for m in a.media], use_llm=not a.no_llm)))
    verdict, timings = res.verdict, res.timings
    if a.json:
        print(verdict.model_dump_json(indent=2))
        return
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{_MARK[verdict.level.value]} {verdict.title_en}  |  {verdict.title_hi}")
    for i, r in enumerate(verdict.reasons, 1):
        print(f"  {i}. [{r.lane.value}·{r.strength.value}] {r.text_en}\n     {r.text_hi}")
    lanes = ", ".join(f"{k}={v.status}" for k, v in verdict.lanes.items())
    print(f"  rule {verdict.rule_id} (v{verdict.rules_version}) · lanes {lanes} · extractor {verdict.extraction.extractor}")
    print("  timings " + json.dumps({k: round(v, 3) for k, v in timings.items()}))


if __name__ == "__main__":
    main()
