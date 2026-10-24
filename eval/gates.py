"""Check every result file against the pass / fail targets in eval/gates.yaml: python -m eval.gates

Prints one line per gate (PASS / FAIL / MISSING, the measured value and the target) and writes
eval/results/gates.json. Exit code 1 if any gate fails. A failed gate is reported, never edited away.
"""

from __future__ import annotations

import json
import operator
import sys
from datetime import datetime, timezone

import yaml

from app.config import ROOT

EVAL = ROOT / "eval"
RES = EVAL / "results"
OPS = {"==": operator.eq, "<=": operator.le, ">=": operator.ge, "<": operator.lt, ">": operator.gt}


def _derive(name: str, d: dict) -> dict:
    """A few gates read values the result files hold only as counts."""
    if name == "redteam.json":
        d = {**d, "passed_all": d.get("passed") == d.get("total")}
    if name == "open_kit.json":
        for lab, x in d.get("per_label", {}).items():
            n = x.get("n") or 0
            x["clear_rate"] = round(x["tiers"].get("CLEAR", 0) / n, 4) if n else None
    return d


def _get(d, path: str):
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def main() -> int:
    spec = yaml.safe_load((EVAL / "gates.yaml").read_text(encoding="utf-8"))
    cache: dict[str, dict | None] = {}
    rows, failed = [], 0
    for g in spec["gates"]:
        if g["file"] not in cache:
            p = RES / g["file"]
            cache[g["file"]] = _derive(g["file"], json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None
        d = cache[g["file"]]
        got = _get(d, g["path"]) if d is not None else None
        if got is None:
            status = "MISSING"
        else:
            status = "PASS" if OPS[g["op"]](got, g["value"]) else "FAIL"
        failed += status != "PASS"
        rows.append({"id": g["id"], "status": status, "measured": got, "target": f"{g['op']} {g['value']}", "why": g["why"], "file": g["file"]})
        print(f"{status:7s} {g['id']:30s} measured {str(got):8s} target {g['op']} {g['value']}")
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "gates_frozen_at": spec["frozen_at"],
           "passed": sum(r["status"] == "PASS" for r in rows), "total": len(rows), "gates": rows}
    (RES / "gates.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{out['passed']}/{out['total']} gates pass · written {RES / 'gates.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
