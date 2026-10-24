"""Semantic scam-pattern matching: understands the meaning of a message, not just exact phrases.

A small multilingual sentence-embedding model (intfloat/multilingual-e5-small, CPU, offline from
models/hf) compares each sentence of the message, with warnings set aside, to example sentences
written for each scam playbook (app/rails/data/playbook_exemplars.yaml). It catches rewordings
the phrase rules miss ("tell no one", "money doubled in three days"), in English, Hindi and Hinglish.

The phrase rules stay as a floor. A semantic match is evidence for Lane C, never a verdict.
Thresholds were chosen on the dev split only (see eval/semantic_calibration.json).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from app.config import RAILS_DATA_DIR, ROOT

log = logging.getLogger("pramaan.semantic")
MODEL_DIR = ROOT / "models" / "hf" / "models--intfloat--multilingual-e5-small" / "snapshots"
EXEMPLARS = RAILS_DATA_DIR / "playbook_exemplars.yaml"
# Scores are MARGINS: similarity to the closest scam example minus similarity to the closest genuine
# message. Raw similarity alone does not separate scams from normal bills and alerts (dev calibration).
MATCH = 0.035      # evidence of that playbook (dev-calibrated, eval/semantic_calibration.json)
STRONG = 0.065     # close enough to count like a full phrase-rule match
_lock = threading.Lock()
_SPLIT = re.compile(r"(?<=[.!?।\n])\s+|;\s+")


def enabled() -> bool:
    return os.getenv("PRAMAAN_SEMANTIC", "on").lower() not in ("0", "off", "false") and _model_path() is not None


def _model_path() -> Path | None:
    snaps = sorted(MODEL_DIR.glob("*")) if MODEL_DIR.exists() else []
    return snaps[-1] if snaps else None


@lru_cache(maxsize=1)
def _model():
    from app.lanes.hf_lock import HF_LOAD_LOCK
    with HF_LOAD_LOCK:
        import torch
        from transformers import AutoModel, AutoTokenizer
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 2)))
        path = _model_path()
        tok = AutoTokenizer.from_pretrained(path, local_files_only=True)
        m = AutoModel.from_pretrained(path, local_files_only=True)
        m.eval()
    return tok, m


def embed(texts: list[str]) -> np.ndarray:
    """L2-normalised e5 embeddings ("query: " prefix, mean pooling)."""
    import torch
    tok, m = _model()
    out = []
    with _lock, torch.no_grad():
        for i in range(0, len(texts), 32):
            batch = tok([f"query: {t}" for t in texts[i:i + 32]], padding=True, truncation=True, max_length=256, return_tensors="pt")
            h = m(**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1).float()
            v = (h * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            out.append(torch.nn.functional.normalize(v, dim=-1).numpy())
    return np.vstack(out) if out else np.zeros((0, 384), dtype=np.float32)


@lru_cache(maxsize=1)
def _exemplars() -> tuple[list[str], list[str], np.ndarray, np.ndarray]:
    """(playbook ids, scam example texts, scam vectors, genuine vectors)."""
    d = yaml.safe_load(EXEMPLARS.read_text(encoding="utf-8"))
    ids, texts = [], []
    for pid, sents in d["exemplars"].items():
        for s in sents:
            ids.append(pid)
            texts.append(s)
    genuine = d.get("genuine", [])
    key = hashlib.sha256(EXEMPLARS.read_bytes()).hexdigest()[:16]
    cache = ROOT / "data" / "cache" / f"exemplars_{key}.npz"
    if cache.exists():
        z = np.load(cache)
        vecs, gvecs = z["scam"], z["genuine"]
    else:
        vecs, gvecs = embed(texts), embed(genuine)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, scam=vecs, genuine=gvecs)
    return ids, texts, vecs, gvecs


def margins(text: str) -> tuple[list[str], np.ndarray, list[str], list[str]]:
    """Per sentence x scam example: similarity minus that sentence's best genuine similarity."""
    sents = sentences(text)
    ids, ex_texts, ex_vecs, g_vecs = _exemplars()
    if not sents:
        return sents, np.zeros((0, len(ids))), ids, ex_texts
    q = embed(sents)
    scam = q @ ex_vecs.T
    gbest = (q @ g_vecs.T).max(axis=1, keepdims=True) if len(g_vecs) else 0.0
    return sents, scam - gbest, ids, ex_texts


_CLAUSE = re.compile(r",\s+|\s+(?:and|but|then|so)\s+|\s+(?:और|फिर|लेकिन)\s+", re.IGNORECASE)


def sentences(text: str) -> list[str]:
    """Sentences, plus the clauses of long sentences (one scam idea often hides inside a long sentence),
    plus the whole message when it is short. Warnings are set aside first."""
    from app.extract.intent_rules import strip_negated
    t = strip_negated(text or "")
    parts = []
    for sent in (p.strip() for p in _SPLIT.split(t)):
        if len(sent) >= 12:
            parts.append(sent)
        if len(sent) > 80:
            parts += [c.strip() for c in _CLAUSE.split(sent) if len(c.strip()) >= 18]
    if len(parts) > 1 and len(t) <= 600:
        parts.append(t.strip())
    seen, out = set(), []
    for x in parts:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:32]


def match(text: str) -> list[dict]:
    """Playbooks the message resembles, best first: {playbook, similarity, sentence, example}."""
    sents, sims, ids, ex_texts = margins(text)       # sentences x exemplars, as margins
    if not sents:
        return []
    best: dict[str, dict] = {}
    hidden = {x for x in os.getenv("PRAMAAN_HOLDOUT_PLAYBOOKS", "").split(",") if x}   # unseen-family study only
    for j, pid in enumerate(ids):
        if pid in hidden:
            continue
        i = int(np.argmax(sims[:, j]))
        s = float(sims[i, j])
        if s >= MATCH and (pid not in best or s > best[pid]["similarity"]):
            best[pid] = {"playbook": pid, "similarity": round(s, 3), "sentence": sents[i][:200], "example": ex_texts[j]}
    return sorted(best.values(), key=lambda x: -x["similarity"])


def warm() -> None:
    if enabled():
        _exemplars()
