"""Lane B · CHANGED: was this media altered from a genuine original?

Status:
  SOURCE_MISMATCH  same footage as an indexed genuine clip, different words (STRONG)
  INDICATORS       only weak hints (partial edits, forensic detector flags)
  NONE             no manipulation found (incl. "matches the original, words unchanged")
  NOT_APPLICABLE   no media in the item
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.extract.contacts import extract_contact_points
from app.extract.intent_rules import detect_asks, detect_pressure
from app.lanes.detectors import audio_score, video_score
from app.lanes.doc_match import check_references, provenance as doc_provenance
from app.media.docs import DocEvidence
from app.lanes.source_match import (
    DIFFERENT_WORDS, SAME_WORDS, VisualMatch, match_frames, ref_text_window, word_diff,
)
from app.media.prep import Frame
from app.media.stt import Transcript
from app.models import Lane, LaneResult, LaneStatusB, Signal, Strength


@dataclass
class MediaEvidence:
    """Everything Lane B needs about one video, prepared by the pipeline."""
    kind: str
    duration: float = 0.0
    frames: list[Frame] = field(default_factory=list)
    transcript: Transcript | None = None
    detector_flags: list[dict] = field(default_factory=list)   # weak forensic hints, filled later
    retranscribe: Callable[[str], Transcript | None] | None = None   # re-run STT in a given language
    doc: DocEvidence | None = None                                    # PDFs and images
    audio_path: Path | None = None                                    # extracted 16 kHz audio (deleted after the check)


def _risky_insertion(added: list[str]) -> bool:
    """Did the forger insert a payment destination, an ask, or a pitch?"""
    text = " ".join(added)
    return bool(extract_contact_points(text) or detect_asks(text) or detect_pressure(text))


def _sig(code: str, strength: Strength, provenance=None, **params) -> Signal:
    return Signal(code=code, lane=Lane.B, strength=strength, params=params, provenance=provenance)


def _doc_signals(ev: DocEvidence, claims_official: bool) -> list[Signal]:
    sigs = []
    if ev.active_content:
        sigs.append(_sig("DOC_ACTIVE_CONTENT", Strength.STRONG, features=ev.active_content))
    if ev.editor_hint and claims_official:
        sigs.append(_sig("DOC_METADATA_EDITED", Strength.WEAK, tool=ev.editor_hint))
    if ev.incremental_saves >= 1 and claims_official:
        sigs.append(_sig("DOC_INCREMENTAL_EDIT", Strength.WEAK, saves=ev.incremental_saves))
    return sigs


def run_lane_b(media: list[MediaEvidence], text: str = "", claims_official: bool = False) -> LaneResult:
    """text: everything the check read (message + transcripts + document/OCR text), for reference checks."""
    videos = [m for m in media if m.kind == "video" and m.frames]
    docs = [m.doc for m in media if m.doc is not None]
    sigs: list[Signal] = []
    detail: dict = {}
    for ev in docs:
        sigs.extend(_doc_signals(ev, claims_official))
    # Weak forensic detectors: hints only. Scores stay in `detail` for analysts and the Lab.
    for m in media:
        if m.kind not in ("video", "audio"):
            continue
        for kind, fn, arg in (("video", video_score, m.frames), ("audio", audio_score, m.audio_path)):
            if kind == "video" and m.kind != "video":
                continue
            try:
                res = fn(arg)
            except Exception as e:   # a weak hint must never take Source-Match down with it
                detail.setdefault("detector_errors", []).append(f"{kind}: {type(e).__name__}")
                continue
            if res is None:
                continue
            detail.setdefault("detectors", []).append({"kind": kind, **res})
            if res.get("flagged"):
                sigs.append(_sig("DETECTOR_INDICATOR", Strength.WEAK, kind=kind, detector=res["detector"]))
    for r in check_references(text):
        sigs.append(_sig(r.code, Strength(r.strength), doc_provenance(), **r.params))
        detail.setdefault("doc_refs", []).append({"code": r.code, **r.params})
    if not videos:
        if not sigs and not docs and not detail.get("detectors"):
            return LaneResult(lane=Lane.B, status=LaneStatusB.NOT_APPLICABLE.value)
        return LaneResult(lane=Lane.B, status=_status(sigs).value, signals=sigs, detail=detail)
    for m in videos:
        vm: VisualMatch | None = match_frames(m.frames)
        if vm is None:
            detail["source"] = None
        else:
            start = max(0.0, vm.offset_s)
            end = start + (m.duration or m.frames[-1].t)
            ref_text = ref_text_window(vm.ref, start, end)
            src = {"ref_id": vm.ref.id, "title_en": vm.ref.title_en, "title_hi": vm.ref.title_hi, "file": vm.ref.file,
                   "offset_s": vm.offset_s, "matched_frames": vm.matched, "total_frames": vm.total, "ratio": vm.ratio,
                   "median_distance": vm.median_distance, "pairs": vm.pairs[:40]}
            detail["source"] = src
            tr = m.transcript
            # Compare in the original's language: speech-to-text can mis-detect
            # the language of low-quality audio, which would fake a "mismatch".
            if tr is not None and m.retranscribe and vm.ref.language and (tr.language or "")[:2].lower() not in (vm.ref.language, ""):
                tr = m.retranscribe(vm.ref.language) or tr
            if tr is None or not tr.text.strip():
                sigs.append(_sig("SOURCE_FOUND_NO_AUDIO_CHECK", Strength.WEAK, source_title=vm.ref.title_en, ref_id=vm.ref.id))
            else:
                diff = word_diff(vm.ref.transcript.get("text", ""), tr.text)
                diff["ref_window"] = ref_text
                detail["diff"] = diff
                common = dict(source_title=vm.ref.title_en, source_title_hi=vm.ref.title_hi, ref_id=vm.ref.id,
                              similarity=diff["similarity"], offset_s=vm.offset_s, matched_frames=vm.matched)
                risky = _risky_insertion(diff["added"])
                if diff["similarity"] < DIFFERENT_WORDS or (diff["similarity"] < SAME_WORDS and risky):
                    sigs.append(_sig("SOURCE_MISMATCH", Strength.STRONG, **common, added=diff["added"][:6], risky_insertion=risky))
                elif diff["similarity"] < SAME_WORDS:
                    sigs.append(_sig("SOURCE_PARTIAL_EDIT", Strength.WEAK, **common, added=diff["added"][:6]))
                else:
                    sigs.append(_sig("SOURCE_MATCH_SAME_WORDS", Strength.POSITIVE, **common))
        for flag in m.detector_flags:
            sigs.append(_sig("DETECTOR_INDICATOR", Strength.WEAK, **flag))
    return LaneResult(lane=Lane.B, status=_status(sigs).value, signals=sigs, detail=detail)


def _status(sigs: list[Signal]) -> LaneStatusB:
    if any(s.strength == Strength.STRONG for s in sigs):
        return LaneStatusB.SOURCE_MISMATCH
    if any(s.strength == Strength.WEAK for s in sigs):
        return LaneStatusB.INDICATORS
    return LaneStatusB.NONE
