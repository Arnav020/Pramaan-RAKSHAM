"""Builds an Extraction using rules only. Always available, deterministic, fast."""

from __future__ import annotations

import re

from app.extract.claims import detect_claims
from app.extract.contacts import extract_contact_points, normalise_text
from app.extract.intent_rules import detect_asks, detect_pressure, has_hidden_instructions
from app.models import Extraction

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def guess_language(text: str) -> str:
    dev = len(_DEVANAGARI.findall(text))
    letters = sum(ch.isalpha() for ch in text) or 1
    if dev / letters > 0.3:
        return "hi"
    if re.search(r"\b(?:aap|apna|karo|kijiye|bhejo|paise|hai|nahi|turant)\b", text, re.IGNORECASE):
        return "hinglish"
    return "en"


def extract_with_rules(text: str) -> Extraction:
    t = normalise_text(text)
    return Extraction(
        text=t,
        language=guess_language(t),
        claims=detect_claims(t),
        contact_points=extract_contact_points(t),
        asks=detect_asks(t),
        pressure=detect_pressure(t),
        hidden_instructions=has_hidden_instructions(t),
        extractor="rules",
    )
