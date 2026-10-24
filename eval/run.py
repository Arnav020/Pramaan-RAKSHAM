"""Reproducible evaluation: python -m eval.run [--split test|dev|all] [--no-llm]

Every number on the Pramaan Lab board comes from this script's output file
(eval/results/latest.json). Nothing is typed in by hand.

Reports, per split:
  - confusion matrix (verdict x label) and key rates with Wilson 95% intervals
  - ablation: verdicts re-fused with each lane switched off (same lane outputs)
  - key experiment: weak detector alone vs Pramaan fusion, by forwarding condition
  - latency p50 / p95 by input type
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import DATA_DIR, ROOT
from app.fusion.engine import fuse, load_rules
from app.models import Extraction, LaneResult, VerdictLevel
from app.pipeline import CheckInput, run_check

EVAL = ROOT / "eval"
MEDIA = DATA_DIR / "media"
RESULTS = EVAL / "results"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None, float | None]:
    if n == 0:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(p, 4), round(max(0, c - h), 4), round(min(1, c + h), 4)


def rate(k: int, n: int) -> dict:
    p, lo, hi = wilson(k, n)
    return {"k": k, "n": n, "p": p, "ci95": [lo, hi]}


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    i = min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))
    return round(v[i], 2)


TIERS = ("WARN", "CHECK", "CLEAR")


def tier(level: str, amber_kind: str | None) -> str:
    """What the person is actually told to do, which is what a false alarm costs them:
    WARN  = don't act (red, or amber with a high-risk ask)
    CHECK = check through an official channel first (amber, unverified / nothing to check)
    CLEAR = no warning (green, or amber "no warning signs")."""
    if level == "RED" or (level == "AMBER" and amber_kind == "high_risk"):
        return "WARN"
    if level == "GREEN" or (level == "AMBER" and amber_kind == "no_risk_signs"):
        return "CLEAR"
    return "CHECK"


def tier_metrics(rows: list[dict]) -> dict:
    """The symmetric view: every way a genuine message can be flagged counts, not only red."""
    scams = [r for r in rows if r["label"] == "scam"]
    gen = [r for r in rows if r["label"] == "genuine"]
    cm = {t: {"scam": 0, "genuine": 0} for t in TIERS}
    for r in rows:
        cm[r["tier"]][r["label"]] += 1
    return {
        "confusion": cm,
        "genuine_warned": rate(sum(r["tier"] == "WARN" for r in gen), len(gen)),
        "genuine_not_clear": rate(sum(r["tier"] != "CLEAR" for r in gen), len(gen)),
        "scam_warned": rate(sum(r["tier"] == "WARN" for r in scams), len(scams)),
        "scam_not_clear": rate(sum(r["tier"] != "CLEAR" for r in scams), len(scams)),
        "scam_clear": rate(sum(r["tier"] == "CLEAR" for r in scams), len(scams)),
    }


def metrics(rows: list[dict], level_key: str = "level") -> dict:
    scams = [r for r in rows if r["label"] == "scam"]
    gen = [r for r in rows if r["label"] == "genuine"]
    official = [r for r in gen if r.get("kind") == "official"]
    red = [r for r in rows if r[level_key] == "RED"]
    cm = {lvl: {"scam": 0, "genuine": 0} for lvl in ("RED", "AMBER", "GREEN")}
    for r in rows:
        cm[r[level_key]][r["label"]] += 1
    return {
        "n": len(rows), "scam": len(scams), "genuine": len(gen),
        "confusion": cm,
        "red_precision": rate(sum(r["label"] == "scam" for r in red), len(red)),
        "scam_recall_amber_or_red": rate(sum(r[level_key] != "GREEN" for r in scams), len(scams)),
        "scam_recall_red": rate(sum(r[level_key] == "RED" for r in scams), len(scams)),
        "false_red_on_genuine": rate(sum(r[level_key] == "RED" for r in gen), len(gen)),
        "green_on_official_genuine": rate(sum(r[level_key] == "GREEN" for r in official), len(official)),
        "scam_marked_green": rate(sum(r[level_key] == "GREEN" for r in scams), len(scams)),
        "genuine_marked_amber": rate(sum(r[level_key] == "AMBER" for r in gen), len(gen)),
        **({"tiers": tier_metrics(rows)} if level_key == "level" and rows and "tier" in rows[0] else {}),
    }


async def run_item(it: dict, use_llm: bool) -> dict:
    media = [MEDIA / it["media"]] if it.get("media") else []
    missing = [str(m) for m in media if not m.exists()]
    if missing:
        return {**it, "skipped": f"missing {missing}"}
    t = time.perf_counter()
    res = await run_check(CheckInput(text=it.get("text") or it.get("caption") or "", media_paths=media, use_llm=use_llm))
    v = res.verdict
    dets = v.lanes["B"].detail.get("detectors", [])
    vid = next((d for d in dets if d["kind"] == "video"), None)
    aud = next((d for d in dets if d["kind"] == "audio"), None)
    return {
        "id": it["id"], "split": it["split"], "label": it["label"], "kind": it.get("kind"), "lang": it.get("lang"), "cat": it.get("cat"),
        "sub": it.get("sub") or ("original" if it.get("media", "").startswith("genuine/") else None),
        "input": "video" if it.get("media", "").endswith((".mp4", ".mov")) else ("document" if it.get("media") else "text"),
        "level": v.level.value, "amber_kind": v.amber_kind, "tier": tier(v.level.value, v.amber_kind), "rule": v.rule_id, "strong_lanes": [l.value for l in v.strong_lanes],
        "lanes": {k: l.model_dump(mode="json") for k, l in v.lanes.items()}, "reasons": [r.code for r in v.reasons],
        "extractor": v.extraction.extractor, "video_detector": vid, "audio_detector": aud,
        "seconds": round(time.perf_counter() - t, 2),
        "_extraction": v.extraction.model_dump(mode="json"),
    }


def ablate(rows: list[dict]) -> dict:
    out = {}
    for off in ("A", "B", "C"):
        rr = []
        for r in rows:
            lanes = {k: LaneResult.model_validate(l) for k, l in r["lanes"].items()}
            lanes[off] = LaneResult(lane=lanes[off].lane, status="UNAVAILABLE", available=False)
            v = fuse(Extraction.model_validate(r["_extraction"]), lanes)
            rr.append({**r, "ablated": v.level.value})
        out[f"without_{off}"] = metrics(rr, "ablated")
    return out


def key_experiment(rows: list[dict]) -> dict:
    """Weak detector alone ('flag as fake if score >= 0.5') vs Pramaan's verdict, on videos."""
    vids = [r for r in rows if r["input"] == "video"]
    out = {"by_condition": {}, "detector": None}
    for r in vids:
        d = r.get("video_detector") or {}
        a = r.get("audio_detector") or {}
        r["detector_flag"] = bool(d.get("flagged") or a.get("flagged"))
        out["detector"] = out["detector"] or d.get("detector")
    groups = defaultdict(list)
    for r in vids:
        groups[(r["label"], r.get("sub") or "original")].append(r)
    for (label, sub), rs in sorted(groups.items()):
        out["by_condition"][f"{label}/{sub}"] = {
            "n": len(rs),
            "detector_says_fake": sum(r["detector_flag"] for r in rs),
            "pramaan_red": sum(r["level"] == "RED" for r in rs),
            "pramaan_amber": sum(r["level"] == "AMBER" for r in rs),
            "video_scores": [(r.get("video_detector") or {}).get("score") for r in rs],
            "audio_scores": [(r.get("audio_detector") or {}).get("score") for r in rs],
        }
    gen = [r for r in vids if r["label"] == "genuine"]
    fake = [r for r in vids if r["label"] == "scam"]
    out["genuine_false_alarm"] = {"detector_alone": rate(sum(r["detector_flag"] for r in gen), len(gen)),
                                  "pramaan_red": rate(sum(r["level"] == "RED" for r in gen), len(gen))}
    out["fake_caught"] = {"detector_alone": rate(sum(r["detector_flag"] for r in fake), len(fake)),
                          "pramaan_red": rate(sum(r["level"] == "RED" for r in fake), len(fake))}
    out["per_detector"] = per_detector(vids)
    return out


def per_detector(vids: list[dict]) -> dict:
    """Each weak detector on its own, so neither is blamed for the other."""
    gen = [r for r in vids if r["label"] == "genuine"]
    fake = [r for r in vids if r["label"] == "scam"]
    res = {}
    for key, name in (("video_detector", "video"), ("audio_detector", "audio")):
        flag = lambda r: bool((r.get(key) or {}).get("flagged"))
        det = next(((r.get(key) or {}).get("detector") for r in vids if r.get(key)), None)
        res[name] = {"detector": det, "genuine_flagged_fake": rate(sum(flag(r) for r in gen), len(gen)), "fakes_flagged": rate(sum(flag(r) for r in fake), len(fake))}
    return res


def latency(rows: list[dict]) -> dict:
    by = defaultdict(list)
    for r in rows:
        by[r["input"]].append(r["seconds"])
    return {k: {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": round(max(v), 2)} for k, v in by.items()}


def subgroups(rows: list[dict]) -> dict:
    out = {}
    for key in ("lang", "input", "cat"):
        g = defaultdict(list)
        for r in rows:
            g[r.get(key) or "-"].append(r)
        out[key] = {k: {"n": len(v), "correct_side": sum((r["label"] == "scam") == (r["level"] != "GREEN") and not (r["label"] == "genuine" and r["level"] == "RED") for r in v),
                        "levels": dict(Counter(r["level"] for r in v)), "labels": dict(Counter(r["label"] for r in v))} for k, v in sorted(g.items())}
    return out


async def main_async(split: str, use_llm: bool) -> Path:
    d = yaml.safe_load((EVAL / "dataset.yaml").read_text(encoding="utf-8"))
    public = yaml.safe_load((EVAL / "public_set.yaml").read_text(encoding="utf-8"))["items"]
    test = [i for i in d["items"] if i["split"] == "test"]
    h = hashlib.sha256(json.dumps(test, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    frozen = (EVAL / "FROZEN.sha256").read_text().split()[0]
    hp = hashlib.sha256(json.dumps(public, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    pf = EVAL / "PUBLIC_FROZEN.sha256"
    public_frozen = pf.read_text().split()[0] if pf.exists() else None
    items = [i for i in d["items"] + public if split == "all" or i["split"] == split]
    rows = []
    for it in items:
        r = await run_item(it, use_llm)
        rows.append(r)
        print(f"{it['id']:5s} {it['label']:7s} -> {r.get('level', 'SKIP'):5s} {r.get('tier', ''):5s} {r.get('seconds', '')}s {r.get('skipped', '')}", flush=True)
    done = [r for r in rows if "skipped" not in r]
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except OSError:
        commit = None
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "git_commit": commit, "split": split, "use_llm": use_llm,
        "rules_version": load_rules()["version"], "test_split_sha256": h, "test_split_frozen_sha256": frozen, "test_split_unchanged": h == frozen,
        "public_split_sha256": hp, "public_split_unchanged": hp == public_frozen,
        "public_sources": [{"id": i["id"], "label": i["label"], "cat": i["cat"], "fidelity": i.get("fidelity"), "date": i.get("date"), "source": i.get("source")} for i in public],
        "machine": f"{platform.system()} {platform.machine()} · {platform.processor()[:40]}",
        "skipped": [r["id"] for r in rows if "skipped" in r],
        "metrics": {s: metrics([r for r in done if r["split"] == s]) for s in sorted({r["split"] for r in done})} | {"all": metrics(done)},
        "ablation": ablate([r for r in done if r["split"] == "test"] or done),
        "key_experiment": key_experiment(done),
        "latency_seconds": latency(done),
        "subgroups": subgroups([r for r in done if r["split"] == "test"] or done),
        "items": [{k: v for k, v in r.items() if k not in ("_extraction", "lanes")} | {"lane_status": {k: l["status"] for k, l in r["lanes"].items()}} for r in done],
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    p = RESULTS / f"run-{stamp}-{split}{'' if use_llm else '-nollm'}.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    # Only a full run updates the Lab; a single-split run is kept under its own name
    (RESULTS / ("latest.json" if split == "all" else f"latest-{split}.json")).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["test", "dev", "public", "all"])
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    p = asyncio.run(main_async(a.split, not a.no_llm))
    r = json.loads(p.read_text(encoding="utf-8"))
    m = r["metrics"].get("test") or r["metrics"].get(r["split"]) or r["metrics"]["all"]
    print(f"\nsaved {p}\ntest split unchanged since freeze: {r['test_split_unchanged']}")
    for k in ("red_precision", "scam_recall_amber_or_red", "scam_recall_red", "false_red_on_genuine", "genuine_marked_amber",
              "green_on_official_genuine", "scam_marked_green"):
        x = m[k]
        print(f"  {k:28s} {x['k']}/{x['n']} = {x['p']}  CI95 {x['ci95']}")
    for k, x in m["tiers"].items():
        if k != "confusion":
            print(f"  tier.{k:23s} {x['k']}/{x['n']} = {x['p']}  CI95 {x['ci95']}")
    ke = r["key_experiment"]
    print("  key experiment, genuine videos: detector alone flags", ke["genuine_false_alarm"]["detector_alone"], "| Pramaan red", ke["genuine_false_alarm"]["pramaan_red"])
    print("  key experiment, fake videos:    detector alone flags", ke["fake_caught"]["detector_alone"], "| Pramaan red", ke["fake_caught"]["pramaan_red"])


if __name__ == "__main__":
    main()
