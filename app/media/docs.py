"""Documents and images: text, UPI QR codes, and file forensics.

PDF  -> embedded text (pypdf); scanned pages are rasterised (PDFium) and OCR'd.
       Forensics: producer/creator, edit history (incremental saves), active
       content (JavaScript, launch actions, embedded files) and links.
Image-> OCR (RapidOCR on CPU; Gemini as fallback for Hindi or hard images),
       QR codes (OpenCV), and editing-software tags in EXIF.
UPI QR codes ("scan to pay") are decoded to the real payee UPI ID and name,
which a printed notice or poster can hide behind official-looking text.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger("pramaan.docs")

MAX_PAGES = 5
_CONSUMER_EDITORS = ("canva", "microsoft® word", "microsoft word", "ilovepdf", "smallpdf", "pdfescape", "sejda", "wps", "pdf candy",
                     "photoshop", "picsart", "pixlr", "inshot", "kinemaster", "google docs", "pdfelement", "foxit phantom", "nitro")


@dataclass
class QRPayload:
    raw: str
    upi: str | None = None
    payee_name: str | None = None
    amount: str | None = None
    note: str | None = None


@dataclass
class DocEvidence:
    kind: str                                   # pdf | image
    text: str = ""
    ocr_used: bool = False
    ocr_provider: str = ""
    pages: int = 0
    producer: str | None = None
    creator: str | None = None
    created: str | None = None
    modified: str | None = None
    incremental_saves: int = 0
    active_content: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    qr: list[QRPayload] = field(default_factory=list)
    editor_hint: str | None = None              # consumer editing software found in metadata/EXIF
    page_images: list[Image.Image] = field(default_factory=list)


# ------------------------------------------------------------------------ QR
def parse_qr(data: str) -> QRPayload:
    p = QRPayload(raw=data[:500])
    if data.lower().startswith("upi://"):
        q = parse_qs(urlparse(data).query)
        g = lambda k: unquote(q.get(k, [""])[0]).strip() or None
        p.upi, p.payee_name, p.amount, p.note = (g("pa") or "").lower() or None, g("pn"), g("am"), g("tn")
    return p


def decode_qr(img: Image.Image) -> list[QRPayload]:
    import cv2
    out, seen = [], set()
    arr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    det = cv2.QRCodeDetector()
    for scale in (1.0, 2.0, 0.5):
        a = arr if scale == 1.0 else cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        try:
            ok, datas, _, _ = det.detectAndDecodeMulti(a)
        except cv2.error:
            ok, datas = False, []
        for d in (datas if ok else []):
            if d and d not in seen:
                seen.add(d)
                out.append(parse_qr(d))
        if out:
            break
    return out


# ----------------------------------------------------------------------- OCR
@lru_cache(maxsize=1)
def _rapidocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _garbage_ratio(lines: list[str]) -> float:
    """Share of OCR lines that look like noise (what a Latin-only model produces on Hindi)."""
    if not lines:
        return 1.0
    bad = 0
    for ln in lines:
        toks = re.findall(r"\S+", ln)
        wordlike = [x for x in toks if re.fullmatch(r"[A-Za-z][A-Za-z'.-]{2,}|[\w.\-]+@[\w]+|[\d₹,.:/+-]{4,}|https?://\S+|\S+\.\S+", x)]
        if not toks or len(wordlike) / len(toks) < 0.5:
            bad += 1
    return bad / len(lines)


def ocr_image(img: Image.Image) -> tuple[str, str]:
    """Returns (text, provider). RapidOCR first (fast, local); Gemini vision when the
    result looks like noise or is too short (RapidOCR's bundled models do not read Devanagari)."""
    im = ImageOps.exif_transpose(img).convert("RGB")
    if max(im.size) > 2000:
        im.thumbnail((2000, 2000))
    lines: list[str] = []
    try:
        res, _ = _rapidocr()(np.asarray(im))
        lines = [r[1] for r in (res or []) if r and r[2] >= 0.5]
    except Exception as e:  # OCR must never break a check
        log.warning("rapidocr failed: %s", e)
    text = "\n".join(lines)
    if len(text.strip()) >= 25 and _garbage_ratio(lines) < 0.35:
        return text, "rapidocr"
    try:
        from app.extract.llm import vision_transcribe
        g = vision_transcribe(im)
        if g and g.strip():
            return g, "gemini-vision"
    except Exception as e:
        log.info("vision OCR unavailable: %s", e)
    return text, "rapidocr"


# ----------------------------------------------------------------------- PDF
def _pdf_forensics(raw: bytes, ev: DocEvidence) -> None:
    ev.incremental_saves = max(0, raw.count(b"%%EOF") - 1)
    for key, label in ((b"/JavaScript", "JavaScript"), (b"/JS ", "JavaScript"), (b"/JS(", "JavaScript"), (b"/Launch", "launch action"),
                       (b"/EmbeddedFile", "embedded file"), (b"/OpenAction", "auto-run on open"), (b"/RichMedia", "rich media"), (b"/SubmitForm", "form submission")):
        if key in raw and label not in ev.active_content:
            ev.active_content.append(label)
    # /OpenAction is also used benignly to set the opening view; flag it only with script
    if "auto-run on open" in ev.active_content and "JavaScript" not in ev.active_content:
        ev.active_content.remove("auto-run on open")


def read_pdf(path: Path) -> DocEvidence:
    from pypdf import PdfReader
    import pypdfium2 as pdfium

    raw = path.read_bytes()
    ev = DocEvidence(kind="pdf")
    _pdf_forensics(raw, ev)
    try:
        r = PdfReader(io.BytesIO(raw))
        if r.is_encrypted:
            r.decrypt("")
        ev.pages = len(r.pages)
        md = r.metadata or {}
        ev.producer, ev.creator = (md.get("/Producer") or None), (md.get("/Creator") or None)
        ev.created, ev.modified = (str(md.get("/CreationDate")) if md.get("/CreationDate") else None), (str(md.get("/ModDate")) if md.get("/ModDate") else None)
        texts = []
        for page in r.pages[:MAX_PAGES]:
            texts.append(page.extract_text() or "")
            for a in (page.get("/Annots") or []):
                try:
                    uri = a.get_object().get("/A", {}).get("/URI")
                    if uri:
                        ev.links.append(str(uri))
                except Exception:
                    continue
        ev.text = "\n".join(texts).strip()
    except Exception as e:
        log.warning("pypdf failed on %s: %s", path.name, e)
    meta = f"{ev.producer or ''} {ev.creator or ''}".lower()
    ev.editor_hint = next((e for e in _CONSUMER_EDITORS if e in meta), None)
    try:
        doc = pdfium.PdfDocument(raw)
        for i in range(min(len(doc), MAX_PAGES)):
            ev.page_images.append(doc[i].render(scale=2.0).to_pil().convert("RGB"))
        ev.pages = ev.pages or len(doc)
    except Exception as e:
        log.warning("pdfium failed on %s: %s", path.name, e)
    for im in ev.page_images:
        ev.qr.extend(decode_qr(im))
    if len(ev.text) < 40 * max(1, min(ev.pages, MAX_PAGES)) and ev.page_images:   # scanned PDF
        parts = [ocr_image(im) for im in ev.page_images[:3]]
        ev.text = "\n".join(p[0] for p in parts).strip()
        ev.ocr_used, ev.ocr_provider = True, parts[0][1] if parts else ""
    return ev


def read_image(path: Path) -> DocEvidence:
    ev = DocEvidence(kind="image", pages=1)
    with Image.open(path) as im:
        im.load()
        try:
            sw = (im.getexif() or {}).get(0x0131)   # EXIF Software
            if sw and any(e in str(sw).lower() for e in _CONSUMER_EDITORS):
                ev.editor_hint = str(sw)
        except Exception:
            pass
        img = im.convert("RGB")
    ev.page_images = [img]
    ev.qr = decode_qr(img)
    ev.text, ev.ocr_provider = ocr_image(img)
    ev.ocr_used = True
    return ev


def qr_lines(ev: DocEvidence) -> str:
    """Render decoded QR payloads as text so the normal extractors see the real payee."""
    out = []
    for q in ev.qr:
        if q.upi:
            out.append(f"[UPI QR code: pay to {q.upi}" + (f" ({q.payee_name})" if q.payee_name else "") + (f", amount Rs {q.amount}" if q.amount else "") + "]")
        elif re.match(r"https?://", q.raw):
            out.append(f"[QR code link: {q.raw}]")
    return "\n".join(out)
