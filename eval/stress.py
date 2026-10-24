"""Stress test: python -m eval.stress [--llm] [--sites] [--only s01,g03]

Runs eval/stress_set.yaml ("what would a judge type?") through the real pipeline and prints, per item, what the
person would be told. This is a DEV tool for finding weak spots before a live demo, not a measurement: we change
rules because of what it shows (see the header of stress_set.yaml). Output: eval/results/stress.json.

Outcome per item:
  ok     the verdict matches what we expect
  soft   a scam told only "check first", or a genuine message told "don't act" (wrong, but not an accusation)
  HARD   a scam cleared, or a genuine message marked red
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter

import yaml

from app.config import DATA_DIR, ROOT

MEDIA = DATA_DIR / "media" / "stress"
OUT = ROOT / "eval" / "results" / "stress.json"


def judge(expect: str, level: str, tier: str) -> str:
    if expect == "red":
        return "ok" if level == "RED" else "soft" if tier == "WARN" else "HARD" if tier == "CLEAR" else "soft"
    if expect == "warn":
        return "ok" if tier == "WARN" else "HARD" if tier == "CLEAR" else "soft"
    if expect == "check":
        return "HARD" if level == "RED" else "ok"
    if expect == "not_red":
        return "HARD" if level == "RED" else "soft" if tier == "WARN" else "ok"
    if expect == "clear":
        return "HARD" if level == "RED" else "ok" if tier == "CLEAR" else "soft"
    raise ValueError(expect)


async def run(items: list[dict], use_llm: bool) -> list[dict]:
    from app.pipeline import CheckInput, run_check
    from eval.run import tier
    out = []
    for it in items:
        paths = [MEDIA / it["media"]] if it.get("media") else []
        if paths and not paths[0].exists():
            out.append({**it, "outcome": "skipped", "why": f"missing {paths[0].name}"})
            continue
        t = time.perf_counter()
        v = (await run_check(CheckInput(text=it.get("text", ""), media_paths=paths, use_llm=use_llm))).verdict
        tr = tier(v.level.value, v.amber_kind)
        lanes = {k: l.status for k, l in v.lanes.items()}
        strong = sorted({f"{s.lane.value}:{s.code}" for l in v.lanes.values() for s in l.signals if s.strength.value == "STRONG"})
        weak = sorted({f"{s.lane.value}:{s.code}" for l in v.lanes.values() for s in l.signals if s.strength.value == "WEAK"})
        out.append({"id": it["id"], "cat": it.get("cat"), "expect": it["expect"], "level": v.level.value, "amber_kind": v.amber_kind,
                    "tier": tr, "rule": v.rule_id, "lanes": lanes, "strong": strong, "weak": weak,
                    "claims": [c.type.value + (f":{c.name}" if c.name else "") for c in v.extraction.claims],
                    "asks": sorted(a.value for a in v.extraction.asks), "contacts": [f"{c.type.value}:{c.value}" for c in v.extraction.contact_points],
                    "outcome": judge(it["expect"], v.level.value, tr), "seconds": round(time.perf_counter() - t, 2),
                    "title": v.title_en, "reasons": [r.text_en for r in v.reasons]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="use the AI extractor, as the live site does")
    ap.add_argument("--sites", action="store_true", help="visit the websites named in messages (off: fictional domains)")
    ap.add_argument("--only", default="", help="comma-separated ids")
    ap.add_argument("--set", default="stress_set", help="stress_set (tuned on) or stress_holdout (written after the fixes)")
    ap.add_argument("-v", action="store_true", help="print signals for every item, not only misses")
    a = ap.parse_args()
    if not a.sites:
        os.environ["PRAMAAN_SITE_CHECK"] = "off"
    from app.store import db
    db.init()
    src = ROOT / "eval" / f"{a.set}.yaml"
    items = yaml.safe_load(src.read_text(encoding="utf-8"))["items"]
    if a.only:
        keep = set(a.only.split(","))
        items = [i for i in items if i["id"] in keep]
    res = asyncio.run(run(items, a.llm))
    for r in res:
        if r["outcome"] == "skipped":
            print(f"{r['id']:5s} skipped  {r['why']}")
            continue
        mark = {"ok": "  ", "soft": "~ ", "HARD": "!!"}[r["outcome"]]
        print(f"{mark}{r['id']:5s} {r['expect']:7s} -> {r['level']:5s} {str(r['amber_kind'] or ''):16s} {r['tier']:5s} {r['rule']:24s} {r['cat']}")
        if r["outcome"] != "ok" or a.v:
            print(f"        lanes {r['lanes']}  strong {r['strong']}  weak {r['weak']}")
            print(f"        claims {r['claims']}  asks {r['asks']}  contacts {r['contacts']}")
    c = Counter(r["outcome"] for r in res)
    print(f"\n{len(res)} items · ok {c['ok']} · soft {c['soft']} · HARD {c['HARD']} · skipped {c['skipped']} · AI extractor {'on' if a.llm else 'off'}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out = OUT if a.set == "stress_set" else OUT.with_name(f"{a.set}.json")
    out.write_text(json.dumps({"set": a.set, "llm": a.llm, "sites": a.sites, "summary": dict(c), "items": res}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
