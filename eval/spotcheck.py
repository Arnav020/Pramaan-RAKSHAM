"""Spot-check kit: run Pramaan on every file in a folder and print a pass / fail table.

    python -m eval.spotcheck                 # our kit, data/kit/ (49 items, expected answers in expected.csv)
    python -m eval.spotcheck <any folder>    # a jury's own files: .txt messages, screenshots, PDFs, voice notes, videos
    python -m eval.spotcheck <folder> --llm  # also use the AI reader, as the live site does

A folder may hold an expected.csv (file,label with label = scam or genuine). Without one, the table shows what
Pramaan said and nothing is scored. The same files can be dropped on the site's /kit page.

Scoring, by what the person is told (see eval/run.py):
  scam     PASS = "don't act" (red, or amber with a high-risk request) · soft = "check first" · HARD = cleared
  genuine  PASS = anything except a warning                          · soft = "don't act"    · HARD = red
Websites named in messages are not visited unless --sites is given (kit items include real scam links).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, ROOT

KIT = DATA_DIR / "kit"
RES = ROOT / "eval" / "results"
MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".mp4", ".mov", ".webm", ".mkv", ".wav", ".mp3", ".m4a", ".ogg", ".opus", ".aac"}


def score(label: str | None, level: str, tier: str) -> str:
    if label == "scam":
        return "PASS" if tier == "WARN" else "HARD" if tier == "CLEAR" else "soft"
    if label == "genuine":
        return "HARD" if level == "RED" else "soft" if tier == "WARN" else "PASS"
    return "-"


async def run(folder: Path, use_llm: bool) -> list[dict]:
    from app.pipeline import CheckInput, run_check
    from eval.run import tier
    expected = {}
    if (folder / "expected.csv").exists():
        with open(folder / "expected.csv", encoding="utf-8") as f:
            expected = {r["file"]: r["label"].strip().lower() for r in csv.DictReader(f)}
    files = sorted(p for p in folder.iterdir() if p.is_file() and (p.suffix.lower() == ".txt" or p.suffix.lower() in MEDIA))
    out = []
    for p in files:
        t = time.perf_counter()
        if p.suffix.lower() == ".txt":
            inp = CheckInput(text=p.read_text(encoding="utf-8", errors="replace")[:6000], use_llm=use_llm)
        else:
            inp = CheckInput(media_paths=[p], use_llm=use_llm)
        try:
            v = (await run_check(inp)).verdict
        except Exception as e:      # one unreadable file must not stop the table
            out.append({"file": p.name, "label": expected.get(p.name), "level": "ERROR", "tier": "-", "result": "ERROR",
                        "why": f"{type(e).__name__}: {str(e)[:100]}", "seconds": round(time.perf_counter() - t, 2)})
            continue
        tr = tier(v.level.value, v.amber_kind)
        label = expected.get(p.name)
        out.append({"file": p.name, "label": label, "level": v.level.value, "amber_kind": v.amber_kind, "tier": tr,
                    "title": v.title_en, "rule": v.rule_id, "result": score(label, v.level.value, tr),
                    "why": v.reasons[0].text_en if v.reasons else "", "seconds": round(time.perf_counter() - t, 2)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=str(KIT))
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--sites", action="store_true", help="visit websites named in the messages")
    a = ap.parse_args()
    if not a.sites:
        os.environ["PRAMAAN_SITE_CHECK"] = "off"
    folder = Path(a.folder).resolve()
    from app.store import db
    db.init()
    rows = asyncio.run(run(folder, a.llm))
    w = max([len(r["file"]) for r in rows] + [10])
    print(f"{'file':{w}s}  {'expected':8s}  {'verdict':22s}  result")
    for r in rows:
        said = "No warning signs" if r.get("amber_kind") == "no_risk_signs" else {
            "WARN": "Warning / scam pattern", "CHECK": "Check first", "CLEAR": "Clear"}.get(r["tier"], r["level"])
        if r["level"] == "RED":
            said = "RED · scam pattern"
        print(f"{r['file']:{w}s}  {str(r['label'] or '-'):8s}  {said:22s}  {r['result']}")
    c = Counter(r["result"] for r in rows)
    scored = [r for r in rows if r["result"] in ("PASS", "soft", "HARD")]
    summary = {"n": len(rows), "scored": len(scored), "PASS": c["PASS"], "soft": c["soft"], "HARD": c["HARD"], "ERROR": c["ERROR"],
               "pass_rate": round(c["PASS"] / len(scored), 4) if scored else None}
    print(f"\n{summary['n']} files · {summary['scored']} with an expected answer · PASS {c['PASS']} · soft {c['soft']} · HARD {c['HARD']}"
          f" · errors {c['ERROR']} · AI reader {'on' if a.llm else 'off'}")
    RES.mkdir(parents=True, exist_ok=True)
    name = "spotcheck.json" if folder == KIT.resolve() else f"spotcheck-{folder.name}.json"
    (RES / name).write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "folder": folder.name, "llm": a.llm,
                                        "sites": a.sites, "summary": summary, "items": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"saved {RES / name}")


if __name__ == "__main__":
    main()
