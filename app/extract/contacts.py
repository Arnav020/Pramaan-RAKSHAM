"""Deterministic extraction of contact points (UPI IDs, phones, links, ...).

This runs before any LLM and never depends on one, so a payment destination
is never missed because a model was rate-limited or manipulated.
"""

from __future__ import annotations

import re
import unicodedata

import tldextract

from app.models import ContactPoint, ContactType

# Offline public-suffix list (bundled snapshot); no network call at runtime.
# India's restricted second-level zones are added explicitly: only RBI-regulated
# banks can hold *.bank.in (IDRBT is registrar), so 'sbi.bank.in' is the owner.
_TLD = tldextract.TLDExtract(
    suffix_list_urls=(),
    extra_suffixes=("bank.in", "fin.in", "gov.in", "nic.in", "co.in", "org.in", "net.in", "ac.in", "edu.in", "res.in", "test"),  # .test: reserved, used by our fictional Demo Bank
)

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


def normalise_text(text: str) -> str:
    """Undo common obfuscation: odd Unicode forms, zero-width chars, Hindi digits."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_ZERO_WIDTH).translate(_DEVANAGARI_DIGITS)
    return text


# UPI IDs look like emails without a TLD: name@handle (handle has no dot).
# Allow spaces around '@', which scammers use to dodge filters.
_UPI_RE = re.compile(r"(?<![\w.@-])([a-zA-Z0-9][\w.\-]{1,255})\s?@\s?([a-zA-Z][a-zA-Z0-9]{1,63})(?![\w@]|\.\w)")
_EMAIL_RE = re.compile(r"(?<![\w.@-])([\w.+\-]+)@([a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+)")

_URL_RE = re.compile(
    r"(?:(?:https?|hxxps?)://|www\.)[^\s<>\"'()]+"
    r"|(?<![\w@./-])(?:[a-zA-Z0-9\-]{1,63}\.)+[a-zA-Z]{2,24}(?:/[^\s<>\"'()]*)?",
    re.IGNORECASE,
)

# Indian numbers: +91 / 0 prefixes, separators allowed. 1600/140/1800/1860
# service series are 10-11 digits.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?(?:\d[\s-]?){9,12}\d(?!\d)")

_SMS_HEADER_RE = re.compile(r"\b([A-Z]{2})-([A-Z0-9]{6})(?:-([PSTG]))?\b")
_SEBI_REG_RE = re.compile(r"\bIN[A-Z]\d{9}\b")
_IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_ACCOUNT_RE = re.compile(r"(?:a/?c|account|खाता)\s*(?:no\.?|number|संख्या)?\s*[:\-]?\s*(\d[\d\s-]{7,20}\d)", re.IGNORECASE)
_APK_RE = re.compile(r"\b[\w\-]+\.apk\b", re.IGNORECASE)

# Spoken UPI IDs, as speech-to-text writes them from a video or voice note:
# "profit wealth at the rate YBL" (Indian English for '@'), "profit dot wealth at Y B L".
# After plain "at", only handles that are not everyday words count, so "my account at SBI" is not a UPI ID.
_SPOKEN_AT_RE = re.compile(r"\s+(at\s+the\s+rate(?:\s+of)?|at)\s+", re.IGNORECASE)
_SPOKEN_STOP = {"to", "pay", "send", "transfer", "on", "is", "the", "id", "upi", "number", "rupees", "rs", "a", "an", "my", "our",
                "your", "and", "or", "please", "now", "today", "only", "via", "using", "into", "in", "account", "this", "that", "me", "us"}
_SPOKEN_WORDY_HANDLES = {"upi", "fam", "kotak", "icici", "sbi", "hdfcbank", "axisbank", "pnb", "slice", "paytm", "freecharge"}


def _spoken_handle(after: str, handles: set[str]) -> tuple[str, int] | None:
    """The UPI handle spoken right after 'at': 'YBL', 'Y B L', 'Y.B.L', 'ok axis', 'valid hdfc'.
    Returns (handle, characters used); the longest run of up to 4 words, separated only by spaces or dots, wins."""
    best, prev_end, parts = None, 0, []
    for tok in re.finditer(r"[A-Za-z]+", after[:40]):
        if not re.fullmatch(r"[\s.]*", after[prev_end:tok.start()]) or len(parts) == 4:
            break
        parts.append(tok.group(0).lower())
        prev_end = tok.end()
        h = "".join(parts)
        if h in handles or re.fullmatch(r"valid[a-z]{2,12}", h):
            best = (h, tok.end())
    return best


def _spoken_name(before: str) -> tuple[str, int] | None:
    """Up to 3 words spoken right before 'at', joined ('profit wealth' -> 'profitwealth', 'profit dot wealth' -> 'profit.wealth').
    Stops at filler words ('pay ... to') and at punctuation. Returns (name, characters used)."""
    words = []
    end = len(before)
    for tok in reversed(list(re.finditer(r"[A-Za-z0-9]+", before))):
        if not re.fullmatch(r"\s*", before[tok.end():end]):
            break
        lw = tok.group(0).lower()
        if lw in _SPOKEN_STOP:
            break
        words.insert(0, "." if lw == "dot" else lw)
        end = tok.start()
    while words and words[0] == ".":
        words.pop(0)
    name = "".join(words)
    if sum(w != "." for w in words) > 3 or not re.fullmatch(r"[a-z0-9][a-z0-9.]{1,60}", name) or name.endswith(".") or ".." in name:
        return None
    return name, len(before) - end


def _spoken_upis(t: str) -> list[tuple[str, int, int]]:
    """(upi id, start, end) for every spoken UPI ID in the text."""
    from app.rails.registry import get_registry   # lazy: the registry is data, extraction stays import-light
    handles = get_registry().psp_handles
    out = []
    for m in _SPOKEN_AT_RE.finditer(t):
        hit = _spoken_handle(t[m.end():], handles)
        if not hit or (m.group(1).lower() == "at" and hit[0] in _SPOKEN_WORDY_HANDLES):
            continue
        name = _spoken_name(t[max(0, m.start() - 80):m.start()])
        if not name:
            continue
        out.append((f"{name[0]}@{hit[0]}", m.start() - name[1], m.end() + hit[1]))
    return out


def _context(text: str, start: int, end: int, width: int = 40) -> str:
    return text[max(0, start - width): end + width].replace("\n", " ").strip()


def _norm_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        digits = digits[1:]
    # Service series keep their own length (1600xxxxxx, 140xxxxxxx, 1800xxxxxxx).
    if digits.startswith(("1600", "140", "1800", "1860")) and 10 <= len(digits) <= 11:
        return digits
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    if 10 <= len(digits) <= 11 and digits.startswith("0"):  # landline with STD code
        return digits
    return None


def registrable_domain(host_or_url: str) -> tuple[str, str]:
    """Return (registrable domain, full host). 'https://x.sbi.bank.in/a' -> ('sbi.bank.in', 'x.sbi.bank.in')."""
    s = host_or_url.strip().lower()
    s = re.sub(r"^(?:https?|hxxps?)://", "", s)
    host = re.split(r"[/?#:]", s, maxsplit=1)[0].strip(".")
    ext = _TLD(host)
    if not ext.suffix:
        return host, host
    reg = ".".join(p for p in (ext.domain, ext.suffix) if p)
    return reg, host


def extract_contact_points(text: str) -> list[ContactPoint]:
    t = normalise_text(text)
    found: list[ContactPoint] = []
    seen: set[tuple[ContactType, str]] = set()
    consumed: list[tuple[int, int]] = []

    def add(kind: ContactType, value: str, m: re.Match, raw: str | None = None) -> None:
        key = (kind, value)
        consumed_span = (m.start(), m.end())
        if key in seen:
            consumed.append(consumed_span)   # a repeat still owns its text (stops 'x.safe@ybl' re-reading as a URL)
            return
        seen.add(key)
        found.append(ContactPoint(type=kind, value=value, raw=raw or m.group(0), context=_context(t, m.start(), m.end())))
        consumed.append((m.start(), m.end()))

    def overlaps(m: re.Match) -> bool:
        return any(m.start() < e and m.end() > s for s, e in consumed)

    for m in _EMAIL_RE.finditer(t):
        add(ContactType.EMAIL, m.group(0).lower(), m)
    for m in _UPI_RE.finditer(t):
        if overlaps(m):
            continue
        add(ContactType.UPI, f"{m.group(1)}@{m.group(2)}".lower(), m)
    for upi, s, e in _spoken_upis(t):
        span = re.compile(re.escape(t[s:e])).search(t, s)   # a Match over the spoken words, for add()
        if span and not overlaps(span):
            add(ContactType.UPI, upi, span)
    for m in _URL_RE.finditer(t):
        if overlaps(m):
            continue
        raw = m.group(0).rstrip(".,;:!?)")
        reg, host = registrable_domain(raw)
        if "." not in reg or reg.replace(".", "").isdigit():
            continue
        ext = _TLD(host)
        if not ext.suffix:  # e.g. "Rs.500" or "file.apk"
            continue
        add(ContactType.URL, raw if raw.lower().startswith(("http", "hxxp")) else "https://" + raw, m, raw)
    for m in _SMS_HEADER_RE.finditer(t):
        add(ContactType.SMS_HEADER, m.group(0), m)
    for m in _SEBI_REG_RE.finditer(t):
        add(ContactType.SEBI_REG_NO, m.group(0), m)
    for m in _ACCOUNT_RE.finditer(t):
        acc = re.sub(r"\D", "", m.group(1))
        ifsc = _IFSC_RE.search(t)
        add(ContactType.BANK_ACCOUNT, acc + (f"|{ifsc.group(0)}" if ifsc else ""), m)
    for m in _APK_RE.finditer(t):
        add(ContactType.APP, m.group(0).lower(), m)
    for m in _PHONE_RE.finditer(t):
        if overlaps(m):
            continue
        p = _norm_phone(m.group(0))
        if p:
            add(ContactType.PHONE, p, m)
    own = _own_host()
    return [c for c in found if not (c.type == ContactType.URL and ((own and registrable_domain(c.value)[1] == own) or _PRAMAAN_LINK.search(c.value)))]


# Pramaan's own seal and verify links, on whichever server issued them
_PRAMAAN_LINK = re.compile(r"/(?:s/[A-HJ-NP-Z2-9]{8}|v/PR-\d{6}-\d{4})(?:[/?#]|$)")


def _own_host() -> str | None:
    """Pramaan's own address (seal and verify links). It is never a contact point of the sender."""
    try:
        from app.config import get_settings
        return registrable_domain(get_settings().public_base_url)[1] or None
    except Exception:
        return None
