"""Document Source-Match: does the notice's reference number belong to it?

Forged "RBI notices" borrow the look and the numbering of real circulars. RBI
numbers circulars in sequence (RBI/2026-27/1, /2, ...), so we can check:
  - the number belongs to a REAL circular about something else  -> STRONG
  - the number is far beyond the last one RBI had issued          -> STRONG
  - the number is in the range we index but unknown               -> WEAK
  - the content matches the real circular                         -> POSITIVE
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from app.config import RAILS_DATA_DIR
from app.extract.intent_rules import detect_asks
from app.models import AskType, Provenance

REF_RE = re.compile(r"\bRBI\s*/\s*(\d{4})\s*-\s*(\d{2})\s*/\s*(\d{1,4})\b", re.IGNORECASE)
_STOP = set("""a an the of to and or in on for by with from at as is are be this that these those it its all any our your
their his her we you they not no will shall may can has have had been was were which who whom whose what when where
reserve bank india rbi circular notification notice dear sir madam yours faithfully regards ref no date dated please
section act directions amendment""".split())
GROWTH_PER_MONTH = 30           # RBI issues roughly 20-30 numbered circulars a month


@dataclass
class Circular:
    ref: str
    fy: str
    n: int
    title: str
    summary: str
    date: str | None
    url: str


@lru_cache(maxsize=1)
def load_circulars() -> tuple[list[Circular], dict]:
    p = RAILS_DATA_DIR / "rbi_circulars.json"
    if not p.exists():
        return [], {}
    d = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for c in d.get("circulars", []):
        m = REF_RE.search(c["ref"])
        if m:
            out.append(Circular(ref=c["ref"], fy=f"{m.group(1)}-{m.group(2)}", n=int(m.group(3)), title=c["title"],
                                summary=c.get("summary", ""), date=c.get("date"), url=c["url"]))
    return out, d.get("_provenance", {})


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in _STOP}


def _fy_now(today: date) -> str:
    y = today.year if today.month >= 4 else today.year - 1
    return f"{y}-{str(y + 1)[2:]}"


@dataclass
class DocRefResult:
    code: str
    strength: str
    params: dict


def check_references(text: str, today: date | None = None) -> list[DocRefResult]:
    circs, prov = load_circulars()
    if not circs:
        return []
    today = today or date.today()
    by_ref = {(c.fy, c.n): c for c in circs}
    doc_words = _words(REF_RE.sub(" ", text))
    doc_asks = set(detect_asks(text)) & {AskType.PAY, AskType.CREDENTIALS, AskType.INSTALL_APP}
    fetched = prov.get("fetched_at")
    out: list[DocRefResult] = []
    seen = set()
    for m in REF_RE.finditer(text):
        fy, n = f"{m.group(1)}-{m.group(2)}", int(m.group(3))
        ref = f"RBI/{fy}/{n}"
        if ref in seen:
            continue
        seen.add(ref)
        known = [c for c in circs if c.fy == fy]
        c = by_ref.get((fy, n))
        if c:
            cw = _words(c.title + " " + c.summary)
            overlap = len(doc_words & cw) / max(1, min(len(cw), 40))
            real_asks = set(detect_asks(c.title + " " + c.summary))
            if overlap < 0.25 or (doc_asks and not real_asks):
                out.append(DocRefResult("DOC_SOURCE_MISMATCH", "STRONG", {"ref": ref, "issuer": "RBI", "real_title": c.title, "real_date": c.date,
                                                                           "real_url": c.url, "overlap": round(overlap, 2)}))
            else:
                out.append(DocRefResult("DOC_SOURCE_MATCH", "POSITIVE", {"ref": ref, "issuer": "RBI", "real_title": c.title, "real_url": c.url}))
            continue
        if fy > _fy_now(today):
            out.append(DocRefResult("DOC_REFERENCE_IMPOSSIBLE", "STRONG", {"ref": ref, "issuer": "RBI", "why_en": f"the financial year {fy} has not started yet",
                                                                           "why_hi": f"वित्त वर्ष {fy} अभी शुरू ही नहीं हुआ है"}))
            continue
        if known:
            latest = max(known, key=lambda x: x.n)
            idx_date = datetime.fromisoformat(fetched).date() if fetched else today
            months = max(0, (today.year - idx_date.year) * 12 + today.month - idx_date.month)
            ceiling = latest.n + GROWTH_PER_MONTH * (months + 1)
            if fy == _fy_now(today) and n > ceiling:
                out.append(DocRefResult("DOC_REFERENCE_IMPOSSIBLE", "STRONG", {
                    "ref": ref, "issuer": "RBI", "latest_ref": latest.ref, "latest_date": latest.date,
                    "why_en": f"RBI's latest circular was {latest.ref} ({latest.date}), so number {n} cannot exist yet",
                    "why_hi": f"RBI का सबसे नया सर्कुलर {latest.ref} ({latest.date}) है, इसलिए नंबर {n} अभी हो ही नहीं सकता"}))
                continue
            lo = min(x.n for x in known)
            if lo <= n <= latest.n:
                out.append(DocRefResult("DOC_REFERENCE_NOT_FOUND", "WEAK", {"ref": ref, "issuer": "RBI"}))
    return out


def provenance() -> Provenance:
    _, p = load_circulars()
    return Provenance(dataset=p.get("dataset", "RBI notifications"), source_url=p.get("source_url"), fetched_at=p.get("fetched_at"), note=p.get("note"))
