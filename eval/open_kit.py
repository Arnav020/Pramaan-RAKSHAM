"""Open-dataset test: Pramaan on a public, labelled SMS set it has never seen.

    python -m eval.open_kit              # download (once, hash-checked), run, write eval/results/open_kit.json
    python -m eval.open_kit --limit 300  # a quick stratified sample

Dataset: "SMS Phishing Dataset for Machine Learning and Pattern Recognition", Sandhya Mishra and Devpriya Soni
(Jaypee Institute of Information Technology), Mendeley Data, V1, 2022, doi:10.17632/f45bkkt8pr.1. Licence CC BY 4.0.
5,971 messages labelled ham (legitimate), spam (unwanted marketing) or smishing (phishing / fraud).

Why this set: it is public, labelled by someone else, and was never used to write or tune any Pramaan rule. It is also
a hard test of the wrong kind for us, on purpose: most of it is UK / US prize and marketing SMS from before UPI, while
Pramaan's rails and playbooks are for Indian financial scams. The results say how far our rules carry outside India
and where they fail; they are not a claim about Indian traffic.

How it runs (same as a real check, minus what would be unfair or unsafe at this scale):
- rules + the local semantic matcher; the AI extractor is OFF by default (5,971 calls would exhaust the free tier, and
  the rules are what we want to measure). --llm turns it on for a sample.
- websites named in the messages are NOT visited (thousands of live scam links), detectors are not used (text only).
- a separate, empty state folder, so no campaign memory or database leaks in.
- labels never reach the engine: each message is checked as text only.

Output: per-label tiers (Warn / Check first / Clear, see eval/run.py), rates with Wilson 95% intervals, the rule that
decided, and the failure cases (smishing cleared, legitimate messages warned) quoted for review.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import random
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone

# The eval package (eval/__init__.py) already gives this run a fresh, empty state folder.
from app.config import DATA_DIR, ROOT  # noqa: E402

URL = "https://data.mendeley.com/public-files/datasets/f45bkkt8pr/files/edb361de-918d-469f-9106-e84823830665/file_downloaded"
SHA256 = "9bbf3188fdad81495d8e82825648b9b63b53fc86841a3d26c02629990b233cc3"   # published by Mendeley for Dataset_5971.zip
CITATION = ("Mishra, S. and Soni, D. (2022). SMS Phishing Dataset for Machine Learning and Pattern Recognition. "
            "Mendeley Data, V1. doi:10.17632/f45bkkt8pr.1. CC BY 4.0.")
CACHE = DATA_DIR / "open" / "Dataset_5971.zip"
RESULTS = ROOT / "eval" / "results"


def fetch() -> list[dict]:
    """Download once, verify the publisher's hash, return [{id, label, text}] (labels lower-cased)."""
    if not CACHE.exists():
        import httpx
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(URL, follow_redirects=True, timeout=60, headers={"User-Agent": "PramaanEval/1.0 (one download, hackathon evaluation)"})
        r.raise_for_status()
        CACHE.write_bytes(r.content)
    got = hashlib.sha256(CACHE.read_bytes()).hexdigest()
    if got != SHA256:
        raise SystemExit(f"dataset hash mismatch: {got} (expected {SHA256}); delete {CACHE} and retry")
    with zipfile.ZipFile(CACHE) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        raw = z.read(name).decode("utf-8", errors="replace")
    rows = []
    for i, r in enumerate(csv.DictReader(io.StringIO(raw))):
        text = (r.get("TEXT") or "").strip()
        if text:
            rows.append({"id": f"m{i:04d}", "label": (r.get("LABEL") or "").strip().lower(), "text": text})
    return rows


def sample(rows: list[dict], limit: int | None, seed: int = 7) -> list[dict]:
    """Stratified by label so a small run still sees every class in its true proportion."""
    if not limit or limit >= len(rows):
        return rows
    by = defaultdict(list)
    for r in rows:
        by[r["label"]].append(r)
    rnd = random.Random(seed)
    out = []
    for lab, rs in sorted(by.items()):
        k = max(1, round(limit * len(rs) / len(rows)))
        out += rnd.sample(rs, min(k, len(rs)))
    return out


async def run(rows: list[dict], use_llm: bool) -> list[dict]:
    from app.pipeline import CheckInput, run_check
    from eval.run import tier
    out = []
    t0 = time.perf_counter()
    for n, r in enumerate(rows, 1):
        t = time.perf_counter()
        v = (await run_check(CheckInput(text=r["text"], use_llm=use_llm))).verdict
        codes = sorted({s.code for l in v.lanes.values() for s in l.signals})
        out.append({**r, "level": v.level.value, "amber_kind": v.amber_kind, "tier": tier(v.level.value, v.amber_kind),
                    "rule": v.rule_id, "codes": codes, "reasons": [x.text_en for x in v.reasons[:2]], "seconds": round(time.perf_counter() - t, 3)})
        if n % 250 == 0:
            print(f"  {n}/{len(rows)} · {time.perf_counter() - t0:.0f} s", flush=True)
    return out


def summarise(res: list[dict]) -> dict:
    from eval.run import TIERS, pct, rate
    labels = sorted({r["label"] for r in res})
    by = {lab: [r for r in res if r["label"] == lab] for lab in labels}
    per = {}
    for lab, rs in by.items():
        n = len(rs)
        per[lab] = {"n": n, "tiers": {t: sum(r["tier"] == t for r in rs) for t in TIERS},
                    "levels": dict(Counter(r["level"] for r in rs)),
                    "warned": rate(sum(r["tier"] == "WARN" for r in rs), n),
                    "not_clear": rate(sum(r["tier"] != "CLEAR" for r in rs), n),
                    "red": rate(sum(r["level"] == "RED" for r in rs), n),
                    "top_signals": Counter(c for r in rs for c in r["codes"]).most_common(8)}
    fails = {
        "smishing_cleared": [{"id": r["id"], "text": r["text"][:220], "said": r["reasons"]} for r in by.get("smishing", []) if r["tier"] == "CLEAR"][:15],
        "ham_warned": [{"id": r["id"], "text": r["text"][:220], "said": r["reasons"]} for r in by.get("ham", []) if r["tier"] == "WARN"][:15],
        "ham_red": [{"id": r["id"], "text": r["text"][:220], "said": r["reasons"]} for r in by.get("ham", []) if r["level"] == "RED"][:15],
    }
    secs = [r["seconds"] for r in res]
    return {"per_label": per, "failures": fails, "latency_s": {"p50": pct(secs, .5), "p95": pct(secs, .95)}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="stratified sample size (default: all 5,971)")
    ap.add_argument("--llm", action="store_true", help="also use the AI extractor (free tier: use with --limit)")
    a = ap.parse_args()
    os.environ["PRAMAAN_SITE_CHECK"] = "off"
    os.environ["PRAMAAN_DETECTORS"] = "off"
    os.environ.setdefault("PRAMAAN_PROMPT_GUARD", "off" if not a.llm else "on")
    from app.store import db
    db.init()
    rows = sample(fetch(), a.limit)
    print(f"{len(rows)} messages · {dict(Counter(r['label'] for r in rows))} · AI extractor {'on' if a.llm else 'off'}", flush=True)
    t0 = time.perf_counter()
    res = asyncio.run(run(rows, a.llm))
    from app.fusion.engine import load_rules
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.perf_counter() - t0, 1),
           "dataset": {"citation": CITATION, "url": URL, "sha256": SHA256, "n_total": 5971, "n_run": len(rows),
                       "sample": a.limit, "label_note": "LABEL values are lower-cased (the file mixes 'Smishing'/'smishing', 'Spam'/'spam')."},
           "config": {"rules_version": load_rules()["version"], "llm": a.llm, "semantic": os.getenv("PRAMAAN_SEMANTIC", "on"),
                      "site_visits": False, "detectors": False},
           "tuned_on_this_set": False,
           **summarise(res),
           "items": [{k: r[k] for k in ("id", "label", "level", "amber_kind", "tier", "rule", "codes")} for r in res]}
    RESULTS.mkdir(parents=True, exist_ok=True)
    name = "open_kit.json" if not a.limit and not a.llm else f"open_kit-sample{a.limit or 'all'}{'-llm' if a.llm else ''}.json"
    p = RESULTS / name
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved {p} · {out['seconds']} s")
    for lab, x in out["per_label"].items():
        print(f"  {lab:9s} n={x['n']:5d}  warned {x['warned']['k']:4d} ({x['warned']['p']})  not clear {x['not_clear']['k']:4d} ({x['not_clear']['p']})  "
              f"red {x['red']['k']} · tiers {x['tiers']}")


if __name__ == "__main__":
    main()
