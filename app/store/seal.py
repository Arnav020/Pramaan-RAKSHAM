"""Pramaan Seal: institutions sign what they really send, so "genuine" becomes provable.

An issuer (a bank) seals a notice or SMS. The seal is an Ed25519 signature by the issuer's own key over:
issuer, seal code, document type, document id, title, issue date, and a fingerprint of the normalised text.
The seal travels as a QR code / link (https://<pramaan>/s/<CODE>) or a short verify line in an SMS.

Anyone can scan it and see exactly what the issuer sent. Pramaan also checks every forwarded item that
carries a seal: valid and unchanged -> "Signed by <issuer>"; changed text (an edited amount, a new UPI ID)
-> a strong "content changed" signal; forged, unknown or revoked -> a strong "invalid seal" signal.

Prototype: one fictional TEST issuer (Demo Bank) with a local key in data/keys/issuers/. Seal records are
files in data/seals/ (they survive a demo-database reset and are deployed with the data).
Production: issuer keys in each bank's HSM / AWS KMS, or C2PA content credentials for media.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.config import DATA_DIR, RAILS_DATA_DIR, STATE_DIR

KEYS = DATA_DIR / "keys" / "issuers"
REGISTRY = STATE_DIR / "seals"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I confusion when read aloud
CODE = r"[A-HJ-NP-Z2-9]{8}"
CODE_RE = re.compile(rf"/s/({CODE})\b|\bPRAMAAN[\s-]*SEAL[\s:#-]*({CODE})\b", re.IGNORECASE)
_IST = timezone(timedelta(hours=5, minutes=30))
# The footer a sealed document carries; it is not part of the sealed text.
_FOOTER = re.compile(r"(?im)^.*(pramaan\s*seal|scan\s+(?:the\s+)?(?:qr|code)\s+to\s+verify|verify\s*:|/s/\w{6,10}\b).*$")


@lru_cache(maxsize=1)
def issuers() -> dict:
    return yaml.safe_load((RAILS_DATA_DIR / "issuers.yaml").read_text(encoding="utf-8"))["issuers"]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _private_key(issuer: str) -> Ed25519PrivateKey:
    path = KEYS / f"{issuer}.pem"
    if not path.exists():
        if issuer not in issuers():
            raise KeyError(issuer)
        KEYS.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (KEYS / f"{issuer}.pub").write_bytes(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        return key
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def public_key(issuer: str) -> Ed25519PublicKey | None:
    pub = KEYS / f"{issuer}.pub"
    if pub.exists():
        return Ed25519PublicKey.from_public_bytes(pub.read_bytes())
    if (KEYS / f"{issuer}.pem").exists():
        return _private_key(issuer).public_key()
    return None


def key_id(issuer: str) -> str | None:
    k = public_key(issuer)
    return hashlib.sha256(k.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).hexdigest()[:12] if k else None


# ------------------------------------------------------------------ text
def strip_footer(text: str) -> str:
    return _FOOTER.sub(" ", text or "")


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", strip_footer(text)).lower()
    t = re.sub(r"[^\w@.₹/-]+", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .")


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()[:32]


_CRITICAL = re.compile(r"[\w.\-]+@[\w]+|https?://\S+|\b[\w-]+\.(?:in|com|test|org|net)\b|(?:₹|rs\.?|inr)?\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE)


def critical_tokens(text: str, ocr: bool = False) -> set[str]:
    """Amounts, numbers, UPI IDs and links: the parts a forger changes. Read from the raw text, so '₹25,000' stays one amount.
    ocr: undo the usual reading slips first ('24 0ct' -> '24 oct', '50.000' -> '50,000')."""
    t = unicodedata.normalize("NFKC", strip_footer(text)).lower()
    if ocr:
        t = re.sub(r"(?<=\d)\s?0(?=[a-z]{2})", " o", t)           # '24 0ct' read as '240ct'
        t = re.sub(r"(?<=\d)\.(?=\d{3}\b)", ",", t)             # '50.000' for '50,000'
    out = set()
    for m in _CRITICAL.finditer(t):
        tok = re.sub(r"^(?:₹|rs\.?|inr)\s?|[\s,]", "", m.group(0)).rstrip(".")
        if len(tok) >= 2:
            out.add(tok)
    return out


def _misread(tok: str, sealed: set[str]) -> bool:
    """OCR swaps a character, it rarely adds one: a number one character off a sealed number, same length, is a misreading.
    Short numbers (a phone's clock, a page number) are screen noise. A changed amount almost always changes its length."""
    if not tok.isdigit():
        return False
    if len(tok) <= 2:
        return True
    return any(len(s) == len(tok) and sum(x != y for x, y in zip(s, tok)) <= 1 for s in sealed)


def _negation_removed(a: list[str], b: list[str], w_idx: int, squashed_b: str) -> bool:
    """A forger deletes the 'not' and keeps the words around it ('do not pay' -> 'do pay'). OCR dropping a whole line
    removes the neighbours too, so in a photo the negation only counts as removed when its neighbours now touch."""
    prev, nxt = (a[w_idx - 1] if w_idx else ""), (a[w_idx + 1] if w_idx + 1 < len(a) else "")
    return bool(prev and nxt) and (prev + nxt) in squashed_b and (prev + a[w_idx] + nxt) not in squashed_b


# Words that flip a sentence's meaning: dropping one ("do NOT pay any fee" -> "do pay any fee") is a forgery, not noise.
_NEGATIONS = {"not", "no", "never", "dont", "don't", "cannot", "can't", "won't", "nahi", "mat", "नहीं", "मत", "न", "कभी"}


def compare(sealed_text: str, message_text: str, tolerant: bool = False) -> dict:
    """How far the forwarded text is from what the issuer sealed, word ORDER included (a set of words would
    let a forger delete 'not' or swap who pays whom).
    exact (typed or pasted text): the sealed words must appear in the same order, with almost no extra text.
    tolerant (OCR / speech): reading noise drops or merges words, so unchanged means every critical token
    (amounts, numbers, UPI IDs, links) and every negation is still there and most words are there in order."""
    from difflib import SequenceMatcher
    a, b = normalise(sealed_text).split(), normalise(message_text).split()
    sm = SequenceMatcher(None, a, b, autojunk=False)
    in_order = sum(blk.size for blk in sm.get_matching_blocks())
    coverage = in_order / max(1, len(a))                 # how much of the sealed text is present, in the same order
    extra = (len(b) - in_order) / max(1, len(b))         # how much of the message is not the sealed text
    ca, cb = critical_tokens(sealed_text), critical_tokens(message_text, ocr=tolerant)
    added = cb - ca
    if tolerant:
        added = {t for t in added if not _misread(t, ca)}
    changed = sorted(added)[:6]
    removed = sorted(ca - cb)[:6]
    if tolerant:
        squashed = "".join(b)
        lost_neg = sorted({w for i, w in enumerate(a) if w in _NEGATIONS and _negation_removed(a, b, i, squashed)})
    else:
        lost_neg = sorted({w for w in a if w in _NEGATIONS} - {w for w in b if w in _NEGATIONS})
    if lost_neg:
        removed = (removed + [f"'{w}'" for w in lost_neg])[:6]
        changed = (changed + [f"removed '{w}'" for w in lost_neg])[:6]
    # tolerant: a photo can lose lines (lower coverage) but nothing new may be added (extra stays low)
    same = (coverage >= 0.6 and extra <= 0.25) if tolerant else (coverage >= 0.97 and extra <= 0.15)
    same = same and not changed
    return {"coverage": round(coverage, 3), "extra": round(extra, 3), "added_critical": changed, "missing_critical": removed, "same": same}


# --------------------------------------------------------------- issuing
def _new_code() -> str:
    while True:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        if not (REGISTRY / f"{code}.json").exists():
            return code


def issue(issuer: str, doc_type: str, title: str, text: str, doc_id: str = "") -> dict:
    if issuer not in issuers():
        raise KeyError(issuer)
    code = _new_code()
    issued = datetime.now(_IST).isoformat(timespec="seconds")
    payload = {"v": 1, "iss": issuer, "code": code, "type": doc_type, "id": doc_id or code, "title": title.strip()[:160],
               "issued": issued, "th": fingerprint(text), "kid": key_id(issuer) or ""}
    sig = _b64(_private_key(issuer).sign(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")))
    rec = {"payload": payload, "signature": sig, "text": strip_footer(text).strip(), "revoked": None}
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / f"{code}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def revoke(code: str, by: str) -> bool:
    rec = load(code)
    if not rec or rec.get("revoked"):
        return False
    rec["revoked"] = {"at": datetime.now(_IST).isoformat(timespec="seconds"), "by": by}
    (REGISTRY / f"{code}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def load(code: str) -> dict | None:
    code = (code or "").upper()
    if not re.fullmatch(CODE, code):
        return None
    p = REGISTRY / f"{code}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def list_seals(limit: int = 100) -> list[dict]:
    if not REGISTRY.exists():
        return []
    out = []
    for p in sorted(REGISTRY.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        r = json.loads(p.read_text(encoding="utf-8"))
        pl = r["payload"]
        out.append({"code": pl["code"], "issuer": pl["iss"], "issuer_name": issuers().get(pl["iss"], {}).get("name", pl["iss"]),
                    "doc_type": pl["type"], "title": pl["title"], "issued": pl["issued"][:16].replace("T", " "), "revoked": r.get("revoked")})
    return out


# ------------------------------------------------------------ verifying
def verify_record(rec: dict) -> dict:
    """Is this seal genuinely from its issuer, and is the stored text the text that was signed?"""
    pl, sig = rec["payload"], rec["signature"]
    pub = public_key(pl["iss"]) if pl.get("iss") in issuers() else None
    if pub is None:
        return {"valid": False, "why": "unknown_issuer"}
    try:
        pub.verify(_unb64(sig), json.dumps(pl, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (InvalidSignature, ValueError):
        return {"valid": False, "why": "bad_signature"}
    if fingerprint(rec.get("text", "")) != pl["th"]:
        return {"valid": False, "why": "registry_text_altered"}
    if rec.get("revoked"):
        return {"valid": False, "why": "revoked", "revoked": rec["revoked"]}
    return {"valid": True}


def codes_in(text: str) -> list[str]:
    out = []
    for m in CODE_RE.finditer(text or ""):
        c = (m.group(1) or m.group(2) or "").upper()
        if c and c not in out:
            out.append(c)
    return out[:3]


def near_code(code: str, known: list[str]) -> str | None:
    """OCR misreads one character of a seal code (M as H). Among codes that exist, the one a single character away."""
    hits = [k for k in known if len(k) == len(code) and k != code and sum(x != y for x, y in zip(k, code)) == 1]
    return hits[0] if len(hits) == 1 else None


def issued_codes() -> list[str]:
    return [p.stem for p in REGISTRY.glob("*.json")] if REGISTRY.exists() else []
