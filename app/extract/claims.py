"""Rule-based detection of who a message claims to be.

The LLM extractor can add claims later, but these rules always run, so the
claim check never depends on a model being available.
"""

from __future__ import annotations

import re

from app.extract.contacts import extract_contact_points, normalise_text
from app.models import Claim, ClaimType
from app.rails.registry import get_registry

_SEBI_INTERMEDIARY = re.compile(
    r"sebi[\s-]*(?:registered|regd|reg\.?|approved|certified|licensed)|registered\s+(?:with|under)\s+sebi"
    r"|research\s+analyst|investment\s+advi[sc]e?r|portfolio\s+manager|stock\s*broker|sebi\s+reg(?:istration)?\s*(?:no|number)"
    r"|सेबी\s*(?:रजिस्टर्ड|पंजीकृत|प्रमाणित)|रिसर्च\s*एनालिस्ट|निवेश\s*सलाहकार",
    re.IGNORECASE,
)
_SEBI_REGULATOR = re.compile(r"(?:from|by|of)\s+sebi\b|sebi\s+(?:officer|official|notice|department|order)|सेबी\s*(?:अधिकारी|नोटिस)", re.IGNORECASE)
_GENERIC_BANK = re.compile(r"\byour\s+bank\b(?!\s+(?:details|account\s+details))|bank\s+(?:manager|official|officer|executive)|customer\s+care|kyc\s+(?:update|expired?|pending)|बैंक\s*(?:मैनेजर|अधिकारी)", re.IGNORECASE)
_PUBLIC_FIGURE = re.compile(r"\[public figure\]|famous\s+(?:investor|businessman|billionaire)|celebrity|मशहूर\s*निवेशक", re.IGNORECASE)


def detect_claims(text: str) -> list[Claim]:
    t = normalise_text(text)
    # Look for names in the prose only: 'rbi.safe@okaxis' must not read as an
    # Axis Bank claim, and 'sbi-bank.in.co' is a link, not a claim to be SBI.
    prose = t
    for cp in extract_contact_points(t):
        prose = prose.replace(cp.raw, " ")
    low = prose.lower()
    reg = get_registry()
    claims: list[Claim] = []

    def add(kind: ClaimType, name: str | None, ev: str) -> None:
        if not any(c.type == kind and c.name == name for c in claims):
            claims.append(Claim(type=kind, name=name, evidence=ev))

    m = _SEBI_INTERMEDIARY.search(t)
    if m:
        add(ClaimType.SEBI_INTERMEDIARY, "SEBI-registered intermediary", m.group(0))
    m = _SEBI_REGULATOR.search(t)
    if m:
        add(ClaimType.REGULATOR, "Securities and Exchange Board of India", m.group(0))

    ent = reg.find_entity_by_name(low)
    if ent is not None:
        if ent.kind == "bank":
            add(ClaimType.BANK, ent.name, ent.name)
        elif not (ent.id == "sebi" and any(c.type == ClaimType.SEBI_INTERMEDIARY for c in claims)):
            add(ClaimType.REGULATOR, ent.name, ent.name)
    # OCR of a scanned notice often loses the spaces ("SUPREMECOURTOFINDIA"): long multi-word names are also
    # looked for in the text with spaces removed (short ones are not, so 'courtesy' never reads as 'court').
    squashed = re.sub(r"\s+", "", low)
    for r in reg.regulators:  # police / CBI / court style claims (several may apply)
        if r.id == "police" and any(a.strip() and (re.search(rf"\b{re.escape(a.strip())}\b", low)
                                                   or (" " in a.strip() and len(a.strip()) >= 9 and a.strip().replace(" ", "") in squashed))
                                    for a in r.aliases):
            add(ClaimType.REGULATOR, r.name, "law-enforcement claim")
    m = _GENERIC_BANK.search(t)
    if m and not any(c.type == ClaimType.BANK for c in claims):
        add(ClaimType.BANK, None, m.group(0))
    m = _PUBLIC_FIGURE.search(t)
    if m:
        add(ClaimType.PUBLIC_FIGURE, None, m.group(0))
    return claims
