"""Source-Match: find the genuine footage a video was made from, then compare words.

No face recognition is used anywhere. Matching is on content fingerprints only:
  1. Keyframes are hashed with PDQ (Meta's open perceptual hash, the industry
     standard for near-duplicate matching). Query frames are hashed in all 8
     flips/rotations; reference frames are indexed with centre crops, so
     re-compression, mirroring, trimming and cropping still match.
  2. A reference is "the source" only if many query frames match it at a
     consistent time offset (same footage, same order), not merely similar.
  3. The query's words are then aligned to the reference transcript.
     Same footage + different words = SOURCE_MISMATCH, a strong signal with
     proof a person can see.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pdqhash
import yaml
from PIL import Image

from app.config import DATA_DIR

INDEX_PATH = DATA_DIR / "index" / "reference_index.json"
CATALOG_PATH = Path(__file__).with_name("reference_catalog.yaml")

FRAME_MATCH_MAX = 45        # PDQ bits (of 256). Same source re-encoded: ~5-20. Different footage: ~95+.
MIN_MATCH_RATIO = 0.35      # share of query frames that must match at a consistent offset
MIN_MATCHED_FRAMES = 4
OFFSET_TOLERANCE_S = 1.25
CROPS = (1.0, 0.9, 0.8, 0.7)
SAME_WORDS = 0.72           # in-order word coverage at or above: words unchanged (genuine re-encodes: 0.79-1.0)
DIFFERENT_WORDS = 0.55      # below: words changed (our re-voiced fakes: 0.29-0.39)


# --------------------------------------------------------------------- hashing
def _bits(h) -> np.ndarray:
    return np.asarray(h, dtype=bool).reshape(-1, 256)


def hash_frame(img: Image.Image, dihedral: bool = False) -> tuple[np.ndarray, int]:
    a = np.asarray(img.convert("RGB"))
    if dihedral:
        hs, q = pdqhash.compute_dihedral(a)
        return _bits(hs), int(q)
    h, q = pdqhash.compute(a)
    return _bits(h), int(q)


def _centre_crop(img: Image.Image, f: float) -> Image.Image:
    if f >= 1.0:
        return img
    w, h = img.size
    cw, ch = int(w * f), int(h * f)
    l, t = (w - cw) // 2, (h - ch) // 2
    return img.crop((l, t, l + cw, t + ch))


def _pack(b: np.ndarray) -> str:
    return np.packbits(b.astype(np.uint8), axis=-1).tobytes().hex()


def _unpack(s: str) -> np.ndarray:
    return np.unpackbits(np.frombuffer(bytes.fromhex(s), dtype=np.uint8)).astype(bool).reshape(-1, 256)


# ----------------------------------------------------------------------- index
@dataclass
class RefClip:
    id: str
    title_en: str
    title_hi: str
    language: str
    file: str
    times: np.ndarray          # (n,) frame times
    hashes: np.ndarray         # (n * len(CROPS), 256) bool
    hash_times: np.ndarray     # (n * len(CROPS),) time of each hash row
    transcript: dict
    official: str | None = None  # the institution whose registered official channel published it (None for a person)


def build_reference(frames, transcript: dict, meta: dict) -> dict:
    rows, times = [], []
    for fr in frames:
        for c in CROPS:
            h, _ = hash_frame(_centre_crop(fr.image, c))
            rows.append(_pack(h[0])); times.append(fr.t)
    return {**meta, "frame_times": [fr.t for fr in frames], "hashes": rows, "hash_times": times, "transcript": transcript}


def save_index(refs: list[dict]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps({"version": 1, "crops": CROPS, "refs": refs}, ensure_ascii=False), encoding="utf-8")
    load_index.cache_clear()


@lru_cache(maxsize=1)
def load_index() -> list[RefClip]:
    if not INDEX_PATH.exists():
        return []
    d = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    official = {c["id"]: c.get("official") for c in load_catalog()} if CATALOG_PATH.exists() else {}
    out = []
    for r in d["refs"]:
        out.append(RefClip(
            id=r["id"], title_en=r["title_en"], title_hi=r["title_hi"], language=r["language"], file=r["file"],
            times=np.array(r["frame_times"]), hashes=np.concatenate([_unpack(h) for h in r["hashes"]]),
            hash_times=np.array(r["hash_times"]), transcript=r["transcript"], official=official.get(r["id"]) or r.get("official"),
        ))
    return out


def load_catalog() -> list[dict]:
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))["clips"]


# -------------------------------------------------------------------- matching
@dataclass
class VisualMatch:
    ref: RefClip
    matched: int
    total: int
    ratio: float
    offset_s: float            # ref_time ~= query_time + offset
    median_distance: int
    pairs: list[tuple[float, float, int]]   # (query_t, ref_t, distance) for the proof strip


def match_frames(query_frames, refs: list[RefClip] | None = None) -> VisualMatch | None:
    """Find the reference whose footage the query re-uses, at a consistent time offset.

    A full query x reference distance matrix is computed once, then candidate
    offsets are scanned: for each offset, a query frame counts if ANY reference
    frame near (query_time + offset) is within the PDQ threshold. This stays
    correct for near-static talking-head video, where the single closest frame
    would land at a random time.
    """
    refs = load_index() if refs is None else refs
    if not refs or not query_frames:
        return None
    q_hashes = np.stack([hash_frame(f.image, dihedral=True)[0] for f in query_frames])   # (nq, 8, 256)
    q_times = np.array([f.t for f in query_frames])
    best: VisualMatch | None = None
    for ref in refs:
        # (nq, rows): min over the 8 dihedral variants of the query frame
        D = (q_hashes[:, :, None, :] != ref.hashes[None, None, :, :]).sum(-1).min(axis=1)
        if D.min() > FRAME_MATCH_MAX:
            continue
        ref_dur = float(ref.hash_times.max()) if len(ref.hash_times) else 0.0
        q_dur = float(q_times.max()) if len(q_times) else 0.0
        top = (0, 10**9, 0.0, None)   # (matched, median_dist, offset, per-frame)
        for off in np.arange(-q_dur, ref_dur + 0.01, 0.5):
            near = np.abs(ref.hash_times[None, :] - (q_times[:, None] + off)) <= OFFSET_TOLERANCE_S   # (nq, rows)
            Dm = np.where(near, D, 999)
            dmin = Dm.min(axis=1)
            ok = dmin <= FRAME_MATCH_MAX
            n = int(ok.sum())
            if n == 0:
                continue
            med = int(np.median(dmin[ok]))
            if (n, -med) > (top[0], -top[1]):
                top = (n, med, float(off), (dmin, Dm.argmin(axis=1), ok))
        n, med, off, per = top
        if per is None:
            continue
        dmin, arg, ok = per
        pairs = [(float(q_times[i]), float(ref.hash_times[arg[i]]), int(dmin[i])) for i in range(len(q_times)) if ok[i]]
        vm = VisualMatch(ref=ref, matched=n, total=len(q_times), ratio=round(n / len(q_times), 3), offset_s=round(off, 2),
                         median_distance=med, pairs=pairs)
        if best is None or (vm.matched, -vm.median_distance) > (best.matched, -best.median_distance):
            best = vm
    if best and best.matched >= MIN_MATCHED_FRAMES and best.ratio >= MIN_MATCH_RATIO:
        return best
    return None


# ------------------------------------------------------------------ word diff
_PUNCT = re.compile(r"[^\w\sऀ-ॿ]")


def norm_words(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = _PUNCT.sub(" ", t)
    return [w for w in t.split() if w]


def word_diff(reference: str, query: str) -> dict:
    """Align query words to reference words.

    `coverage` = share of the query's words that appear in the original, in the
    same order. It is robust to trimming (a clip of the middle of a speech still
    scores high) yet drops sharply when words are replaced. `added` lists the
    query words that are NOT in the original: what the forger put in.
    """
    a, b = norm_words(reference), norm_words(query)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        ops.append({"op": tag, "ref": " ".join(a[i1:i2]), "query": " ".join(b[j1:j2])})
    matched = sum(bl.size for bl in sm.get_matching_blocks())
    coverage = matched / len(b) if b else 0.0
    added = [o["query"] for o in ops if o["op"] in ("replace", "insert") and o["query"]]
    removed = [o["ref"] for o in ops if o["op"] in ("replace", "delete") and o["ref"]]
    return {"similarity": round(coverage, 3), "ratio": round(sm.ratio(), 3), "ops": ops, "added": added, "removed": removed,
            "ref_words": len(a), "query_words": len(b)}


def word_flags(ref_words: list[str], query_words: list[str]) -> tuple[list[bool], list[bool]]:
    """Per displayed word: is this query word NOT in the original (inserted), and is this
    original word missing from the query (removed)? Used to highlight the transcripts."""
    a = [" ".join(norm_words(w)) for w in ref_words]
    b = [" ".join(norm_words(w)) for w in query_words]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    qf, rf = [True] * len(b), [True] * len(a)
    for bl in sm.get_matching_blocks():
        for k in range(bl.size):
            rf[bl.a + k] = False
            qf[bl.b + k] = False
    qf = [f and bool(b[i]) for i, f in enumerate(qf)]
    rf = [f and bool(a[i]) for i, f in enumerate(rf)]
    return qf, rf


def ref_text_window(ref: RefClip, start: float, end: float) -> str:
    """Reference words spoken between start and end (seconds), for trimmed queries."""
    words = ref.transcript.get("words") or []
    if not words:
        return ref.transcript.get("text", "")
    return " ".join(w["word"] for w in words if w["end"] >= start - 0.5 and w["start"] <= end + 0.5)
