"""Documents and images: reference Source-Match, UPI QR decoding, PDF forensics.
The unit tests build their own inputs; the end-to-end ones use data/media/test (skipped if absent)."""

import asyncio
import io
from datetime import date
from pathlib import Path

import pytest
import qrcode
from pypdf import PdfWriter

from app.config import DATA_DIR
from app.lanes.doc_match import check_references
from app.media.docs import decode_qr, parse_qr, read_pdf
from app.models import VerdictLevel
from app.pipeline import CheckInput, run_check

TEST = DATA_DIR / "media" / "test"
TODAY = date(2026, 10, 22)


def codes(text):
    return {r.code: r for r in check_references(text, today=TODAY)}


def test_real_reference_used_for_a_different_subject():
    c = codes("RBI/2026-27/290 Mandatory safety deposit: transfer Rs 25,000 to rbi.safe@okaxis within 24 hours or your account will be frozen.")
    assert "DOC_SOURCE_MISMATCH" in c
    assert "Cash Reserve" in c["DOC_SOURCE_MISMATCH"].params["real_title"]


def test_impossible_reference_numbers():
    assert "DOC_REFERENCE_IMPOSSIBLE" in codes("Ref: RBI/2026-27/912 pay the fee")
    assert "DOC_REFERENCE_IMPOSSIBLE" in codes("Ref: RBI/2027-28/5 pay the fee")      # future financial year


def test_genuine_reference_with_its_own_content_matches():
    c = codes("RBI/2026-27/290 Section 42(1) of the Reserve Bank of India Act, 1934 - Change in Daily Minimum Cash Reserve Maintenance "
              "Requirement. The scheduled banks are allowed to maintain minimum Cash Reserve Ratio (CRR) daily during the reporting fortnight.")
    assert "DOC_SOURCE_MATCH" in c and "DOC_SOURCE_MISMATCH" not in c


def test_upi_qr_roundtrip():
    img = qrcode.make("upi://pay?pa=rbi.safe@okaxis&pn=Rahul%20Kumar&am=25000").convert("RGB")
    got = decode_qr(img)
    assert got and got[0].upi == "rbi.safe@okaxis" and got[0].payee_name == "Rahul Kumar" and got[0].amount == "25000"
    assert parse_qr("https://example.com").upi is None


def test_pdf_active_content_and_editor(tmp_path):
    w = PdfWriter()
    w.add_blank_page(595, 842)
    w.add_js("app.alert('x');")
    w.add_metadata({"/Producer": "Canva"})
    p = tmp_path / "x.pdf"
    with open(p, "wb") as f:
        w.write(f)
    ev = read_pdf(p)
    assert "JavaScript" in ev.active_content and ev.editor_hint == "canva"


needs_docs = pytest.mark.skipif(not (TEST / "forged_rbi_notice_realref.pdf").exists(), reason="run python -m tools.make_test_docs")


@needs_docs
@pytest.mark.parametrize("name,level", [
    ("forged_rbi_notice_realref.pdf", VerdictLevel.RED),
    ("forged_rbi_notice_futureref.pdf", VerdictLevel.RED),
    ("forged_rbi_notice_js.pdf", VerdictLevel.RED),
    ("scam_poster_qr.png", VerdictLevel.RED),
    ("genuine_demobank_notice.pdf", VerdictLevel.GREEN),
    ("genuine_rbi_circular_copy.pdf", VerdictLevel.AMBER),
])
def test_documents_end_to_end(name, level):
    v = asyncio.run(run_check(CheckInput(media_paths=[TEST / name]))).verdict
    assert v.level == level, [r.code for r in v.reasons]
