"""Choose the semantic matcher's thresholds on the DEV split only: python -m eval.semantic_calibrate

For every dev text item, the best similarity to any playbook example is recorded. Scams should sit
above the match threshold; genuine messages (bank notices, safety advice, ordinary chat) below it.
Writes eval/semantic_calibration.json; the thresholds in app/lanes/semantic.py cite it.
"""

from __future__ import annotations

import json

import numpy as np
import yaml

from app.config import ROOT
from app.lanes import semantic

# Ordinary messages that must never resemble a scam (dev negatives, written for calibration).
BENIGN = [
    "Happy Diwali to you and your family, see you at dinner on Sunday.",
    "Your order has been shipped and will arrive on Thursday.",
    "Meeting moved to 4 pm, please bring the printed report.",
    "Rs 2,000 credited to your account from Ramesh via UPI. Available balance Rs 14,560.",
    "Your electricity bill of Rs 1,240 is generated. Pay by 30 Oct through the official app.",
    "Demo Bank will never ask for your OTP, PIN or password. Report fraud on 1930.",
    "Mom, I reached the hostel safely, will call you at night.",
]


def main() -> None:
    d = yaml.safe_load((ROOT / "eval" / "dataset.yaml").read_text(encoding="utf-8"))
    dev = [i for i in d["items"] if i["split"] == "dev" and (i.get("text") or i.get("caption"))]
    rows = []
    for it in dev:
        m = semantic.match(it.get("text") or it.get("caption"))
        best = m[0] if m else None
        rows.append({"id": it["id"], "label": it["label"], "best": best["similarity"] if best else None,
                     "playbook": best["playbook"] if best else None})
    # max similarity without any threshold, to see the separation
    def raw_best(t):
        sents, m, _, _ = semantic.margins(t)
        return float(m.max()) if len(sents) else -1.0
    raw = [{"id": it["id"], "label": it["label"], "raw": round(raw_best(it.get("text") or it.get("caption")), 3)} for it in dev]
    benign = [{"text": t, "raw": round(raw_best(t), 3)} for t in BENIGN]
    scam_raw = sorted(r["raw"] for r in raw if r["label"] == "scam")
    gen_raw = sorted([r["raw"] for r in raw if r["label"] == "genuine"] + [b["raw"] for b in benign])
    out = {"split": "dev", "n_scam": len(scam_raw), "n_genuine_or_benign": len(gen_raw),
           "scam_raw_min": scam_raw[0] if scam_raw else None, "scam_raw_median": float(np.median(scam_raw)) if scam_raw else None,
           "genuine_raw_max": gen_raw[-1] if gen_raw else None, "genuine_raw_median": float(np.median(gen_raw)) if gen_raw else None,
           "thresholds": {"match": semantic.MATCH, "strong": semantic.STRONG}, "items": raw, "benign": benign, "matches": rows}
    (ROOT / "eval" / "semantic_calibration.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("items", "benign", "matches")}, indent=1))
    for r in sorted(raw, key=lambda r: -r["raw"]):
        print(f"  {r['id']:5s} {r['label']:7s} {r['raw']}")
    for b in sorted(benign, key=lambda b: -b["raw"]):
        print(f"  benign  {b['raw']}  {b['text'][:60]}")


if __name__ == "__main__":
    main()
