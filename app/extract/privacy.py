"""Mask the victim's private numbers before any text reaches a third-party AI service.

Masked: OTP / PIN / verification codes, card numbers (Luhn-valid), CVVs, 12-digit IDs such as
Aadhaar or a UTR, account numbers, PAN. Kept: the scammer-side identifiers the checks need
(UPI IDs, phone numbers, links), which our local rules have already extracted anyway.
Audio sent for speech-to-text and images sent for Hindi OCR cannot be masked this way; that is
stated in docs/DATA_POLICY.md.
"""

from __future__ import annotations

import re

_OTP_BEFORE = re.compile(r"(?i)\b(otp|one[\s-]*time\s+password|verification\s+code|security\s+code|code|m?pin|ओटीपी|पिन)\b(\W{0,12}(?:is\s+|:|-)?\W{0,4})(\d{4,8})\b")
_OTP_AFTER = re.compile(r"(?i)\b(\d{4,8})\b(?=\W{0,6}(?:is\s+(?:your|the)\s+)?(?:otp|one[\s-]*time\s+password|verification\s+code|ओटीपी)\b)")
_CVV = re.compile(r"(?i)\b(cvv|cvc)\b(\W{0,8})(\d{3,4})\b")
_ACCOUNT = re.compile(r"(?i)\b(a/?c|account|खाता)(\s*(?:no\.?|number|संख्या))?(\s*[:\-]?\s*)(\d[\d\s-]{7,20}\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_LONG_DIGITS = re.compile(r"(?<![\d+])(?:\d[ -]?){11,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def _long_number(m: re.Match) -> str:
    raw = m.group(0)
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return raw                                     # +91 phone number written without the plus: needed for checks
    if 13 <= len(digits) <= 19 and _luhn(digits):
        return "[CARD]"
    if len(digits) == 12:
        return "[12-DIGIT ID]"                         # Aadhaar, or a UTR / transaction reference
    return raw


def mask_for_ai(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}

    def tally(kind: str):
        counts[kind] = counts.get(kind, 0) + 1

    def otp_before(m):
        tally("otp")
        return f"{m.group(1)}{m.group(2)}[OTP]"

    def otp_after(m):
        tally("otp")
        return "[OTP]"

    def cvv(m):
        tally("cvv")
        return f"{m.group(1)}{m.group(2)}[CVV]"

    def account(m):
        tally("account")
        return f"{m.group(1)}{m.group(2) or ''}{m.group(3)}[ACCOUNT]"

    def pan(m):
        tally("pan")
        return "[PAN]"

    def long_number(m):
        out = _long_number(m)
        if out != m.group(0):
            tally("card" if out == "[CARD]" else "id12")
        return out

    t = _OTP_BEFORE.sub(otp_before, text or "")
    t = _OTP_AFTER.sub(otp_after, t)
    t = _CVV.sub(cvv, t)
    t = _ACCOUNT.sub(account, t)
    t = _PAN.sub(pan, t)
    t = _LONG_DIGITS.sub(long_number, t)
    return t, counts
