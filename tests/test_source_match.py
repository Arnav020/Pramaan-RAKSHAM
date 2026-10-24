"""Source-Match on our consented clips. Skipped when the media is not on this
machine (videos are never committed). Build media first:
    python -m tools.build_index && python -m tools.make_test_fakes
"""

import asyncio
from pathlib import Path

import pytest

from app.config import DATA_DIR
from app.lanes.source_match import INDEX_PATH, word_diff
from app.models import VerdictLevel
from app.pipeline import CheckInput, run_check

TEST = DATA_DIR / "media" / "test"
GEN = DATA_DIR / "media" / "genuine"
needs_media = pytest.mark.skipif(not (INDEX_PATH.exists() and TEST.exists()), reason="consented media not on this machine")


def run(path: Path, text: str = ""):
    return asyncio.run(run_check(CheckInput(text=text, media_paths=[path]))).verdict


def test_word_diff_coverage_is_trim_robust_and_catches_replacement():
    ref = "invest regularly stay diversified and be patient anyone who promises guaranteed returns is not telling the truth"
    assert word_diff(ref, "stay diversified and be patient")["similarity"] == 1.0
    d = word_diff(ref, "guaranteed returns of three hundred percent pay ten thousand rupees today")
    assert d["similarity"] < 0.55 and d["added"]


@needs_media
@pytest.mark.parametrize("name", sorted(p.name for p in TEST.glob("fake_revoiced_*.mp4")) if TEST.exists() else [])
def test_revoiced_fakes_are_source_mismatch(name):
    v = run(TEST / name)
    assert v.lanes["B"].status == "SOURCE_MISMATCH"
    assert any(r.code == "SOURCE_MISMATCH" for r in v.reasons)


@needs_media
@pytest.mark.parametrize("name", sorted(p.name for p in TEST.glob("genuine_*.mp4")) if TEST.exists() else [])
def test_genuine_variants_find_source_and_are_never_red(name):
    v = run(TEST / name)
    assert v.lanes["B"].detail.get("source"), "source not found"
    assert v.lanes["B"].status in ("NONE", "INDICATORS")
    assert v.level != VerdictLevel.RED


@needs_media
def test_fake_with_payment_caption_is_red_and_cites_footage():
    v = run(TEST / "fake_revoiced_clip1_arnav_en_wa.mp4", "Forwarded: pay ₹10,000 to profitwealth@ybl to join")
    assert v.level == VerdictLevel.RED
    assert v.reasons[0].code == "SOURCE_MISMATCH"   # visible proof is shown first
