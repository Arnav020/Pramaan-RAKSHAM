"""The whole test kit in one command: python -m eval.kit [--quick]

Runs, in order, and stops at the first broken step:
  1. the unit and integration tests                     (pytest)
  2. our labelled evaluation: test, dev and public splits (eval.run)       -> eval/results/latest.json
  3. the open public dataset, 5,971 SMS never seen       (eval.open_kit)   -> eval/results/open_kit.json
  4. the red-team suite, including known misses          (eval.redteam)    -> eval/results/redteam.json
  5. the robustness studies (rewrites, unseen families)  (eval.robustness) -> eval/results/robustness.json   [skipped by --quick]
Then writes eval/results/kit_summary.json: what each part covers (input types, languages, lanes, verdicts) and its
headline numbers, all read back from the result files. See eval/REPRODUCE.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import yaml

from app.config import DATA_DIR, ROOT

EVAL = ROOT / "eval"
RES = EVAL / "results"


def step(name: str, args: list[str]) -> float:
    print(f"\n=== {name}: {' '.join(args)}", flush=True)
    t = time.perf_counter()
    r = subprocess.run([sys.executable, *args], cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"{name} failed (exit {r.returncode})")
    return round(time.perf_counter() - t, 1)


def coverage() -> dict:
    """What the labelled sets exercise, counted from the frozen files (not from memory)."""
    items = yaml.safe_load((EVAL / "dataset.yaml").read_text(encoding="utf-8"))["items"]
    items += yaml.safe_load((EVAL / "public_set.yaml").read_text(encoding="utf-8"))["items"]

    def kind(i):
        m = (i.get("media") or "").lower()
        return "video" if m.endswith((".mp4", ".mov")) else "pdf" if m.endswith(".pdf") else "image" if m.endswith((".png", ".jpg")) else "text"
    present = [i for i in items if not i.get("media") or (DATA_DIR / "media" / i["media"]).exists()]
    return {
        "items": len(items), "runnable_here": len(present),
        "by_split_label": dict(Counter(f"{i['split']}/{i['label']}" for i in items)),
        "by_input": dict(Counter(kind(i) for i in items)),
        "by_language": dict(Counter(i.get("lang") or "?" for i in items)),
        "by_category": dict(Counter(i.get("cat") or "?" for i in items)),
        "note": "Video items need the team's consented clips (not in the repository); without them those items are skipped and say so.",
    }


def load(name: str):
    p = RES / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def summary(times: dict) -> dict:
    r, o, t, rob = load("latest.json"), load("open_kit.json"), load("redteam.json"), load("robustness.json")
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "step_seconds": times, "coverage": coverage()}
    if r:
        m = r["metrics"]
        out["labelled"] = {s: {"n": m[s]["n"], "confusion": m[s]["confusion"], "tiers": m[s].get("tiers", {}).get("confusion"),
                               "false_red_on_genuine": m[s]["false_red_on_genuine"], "scam_marked_green": m[s]["scam_marked_green"]}
                           for s in ("test", "dev", "public") if s in m}
        out["labelled"]["frozen_unchanged"] = {"test": r["test_split_unchanged"], "public": r["public_split_unchanged"]}
        out["lanes_exercised"] = sorted({k for i in r["items"] for k, v in (i.get("lane_status") or {}).items() if v not in ("NOT_APPLICABLE", "UNAVAILABLE")})
        out["verdicts_seen"] = dict(Counter(i["level"] for i in r["items"]))
    if o:
        out["open_dataset"] = {"citation": o["dataset"]["citation"], "n": o["dataset"]["n_run"],
                               "per_label": {k: {"n": v["n"], "tiers": v["tiers"], "warned": v["warned"], "not_clear": v["not_clear"]}
                                             for k, v in o["per_label"].items()}}
    if t:
        out["redteam"] = {"defended": t["passed"], "total": t["total"], "known_misses": t.get("known_misses")}
    if rob:
        out["robustness"] = {"generated_at": rob.get("generated_at")}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip the robustness studies (~7 min, and they need the free AI tier)")
    a = ap.parse_args()
    times = {"pytest": step("tests", ["-m", "pytest", "-q"]),
             "eval_run": step("labelled evaluation", ["-m", "eval.run"]),
             "open_kit": step("open dataset", ["-m", "eval.open_kit"]),
             "redteam": step("red team", ["-m", "eval.redteam"])}
    if not a.quick:
        times["robustness"] = step("robustness", ["-m", "eval.robustness"])
    s = summary(times)
    (RES / "kit_summary.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nkit complete · summary in {RES / 'kit_summary.json'}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--summary-only":
        s = summary({})
        (RES / "kit_summary.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(s, indent=1, ensure_ascii=False)[:3000])
    else:
        main()
