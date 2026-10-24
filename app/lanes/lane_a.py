"""Lane A · WHO: check every contact point against India's official trust rails.

Output status:
  VERIFIED      every contact point matches the claimed sender's official records
  CONTRADICTED  at least one strong contradiction (claim vs rail, alert list, FRI)
  UNVERIFIABLE  anything else, including "nothing to check"
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from app.extract.contacts import free_host_platform, is_ip_host, registrable_domain
from app.models import (
    AskType, ClaimType, ContactPoint, ContactType, Extraction, Lane, LaneResult, LaneStatusA, Signal, Strength,
)
from app.rails.registry import Entity, get_registry

_INSTITUTION_CLAIMS = {ClaimType.BANK, ClaimType.NBFC, ClaimType.REGULATOR, ClaimType.PAYMENT_APP}
_SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "rb.gy", "cutt.ly", "is.gd", "shorturl.at", "tiny.cc"}
_MESSAGING = {"wa.me", "whatsapp.com", "t.me", "telegram.me", "telegram.org"}


def _sig(code: str, strength: Strength, prov_key: str | None = None, **params) -> Signal:
    reg = get_registry()
    return Signal(code=code, lane=Lane.A, strength=strength, params=params, provenance=reg.prov.get(prov_key) if prov_key else None)


def _claimed_entities(ex: Extraction, types: tuple[ClaimType, ...] | None = None) -> list[Entity]:
    reg = get_registry()
    out = []
    for c in ex.claims:
        if types and c.type not in types:
            continue
        if c.name:
            e = reg.find_entity_by_name(c.name)
            if e and e not in out:
                out.append(e)
    return out


def _has_claim(ex: Extraction, *types: ClaimType) -> bool:
    return any(c.type in types for c in ex.claims)


_GENERIC_TOKENS = {"bank", "india", "indian", "central", "union", "federal", "state", "first", "online", "small", "finance", "demo", "cyber", "crime", "police", "court", "customs"}
_BANKISH = {"bank", "netbanking", "banking", "kyc", "login", "secure", "verify", "update", "account", "rewards", "card", "upi", "help", "support", "refund"}


def _brand_tokens(e: Entity) -> set[str]:
    toks = {e.id}
    for d in e.domains:
        toks.add(d.split(".")[0])
    for a in e.aliases:
        a = re.sub(r"[^a-z0-9]", "", a.lower())
        if len(a) >= 3:
            toks.add(a)
    return {t for t in toks if len(t) >= 3 and t not in _GENERIC_TOKENS and t.isascii()}


def _looks_like(host: str, e: Entity, claimed: list[Entity]) -> bool:
    """True if a host imitates entity e. Short brand tokens (sbi, pnb) need a
    bank-ish companion word or a matching claim, so 'yesterday.com' is not 'YES Bank'."""
    segs = [s for s in re.split(r"[.\-_]", host) if s]
    companion = any(s in _BANKISH or any(b in s for b in _BANKISH) for s in segs) or e in claimed
    for tok in _brand_tokens(e):
        for s in segs:
            if s == tok and (len(tok) >= 5 or companion):
                return True
            if len(tok) >= 5 and tok in s:
                return True
            if len(tok) in (3, 4) and s.startswith(tok) and companion and len(s) <= len(tok) + 8:
                return True
    return False


# --------------------------------------------------------------------------- UPI
_QR_PAYEE = re.compile(r"UPI QR code: pay to (\S+) \(([^)]{2,80})\)")


def _qr_payee_signal(cp: ContactPoint, ex: Extraction) -> Signal | None:
    """A QR code's hidden payee name vs who the message claims to be."""
    m = next((m for m in _QR_PAYEE.finditer(ex.text) if m.group(1).lower() == cp.value), None)
    if not m:
        return None
    payee = m.group(2).strip()
    # Compare only against a named bank or regulator ("SEBI-registered adviser" names no one).
    claimed = [e for e in _claimed_entities(ex, (ClaimType.BANK, ClaimType.REGULATOR, ClaimType.NBFC))]
    if claimed and not any(fuzz.partial_ratio(payee.lower(), a.lower()) >= 85 for e in claimed for a in [e.name] + e.aliases if len(a) > 3):
        return _sig("QR_PAYEE_NOT_CLAIMED_ENTITY", Strength.STRONG, "upi", upi=cp.value, payee=payee, claimed=claimed[0].name)
    return _sig("QR_PAYEE_NAME", Strength.INFO, upi=cp.value, payee=payee)


def check_upi(cp: ContactPoint, ex: Extraction) -> list[Signal]:
    sigs_qr = [s for s in [_qr_payee_signal(cp, ex)] if s]
    return sigs_qr + _check_upi_rules(cp, ex)


def _check_upi_rules(cp: ContactPoint, ex: Extraction) -> list[Signal]:
    reg = get_registry()
    name, _, handle = cp.value.partition("@")
    is_valid = handle.startswith(reg.valid_prefix)
    cat = name.rsplit(".", 1)[-1] if "." in name else None
    sigs: list[Signal] = []
    if is_valid:
        if cat in reg.sebi_categories:
            sigs.append(_sig("UPI_VALID_HANDLE", Strength.POSITIVE, "upi", upi=cp.value, category=reg.sebi_categories[cat]))
        else:
            sigs.append(_sig("UPI_VALID_HANDLE_NO_CATEGORY", Strength.WEAK, "upi", upi=cp.value))
        return sigs
    if _has_claim(ex, ClaimType.SEBI_INTERMEDIARY) or (_has_claim(ex, ClaimType.PUBLIC_FIGURE) and ex.asks):
        # A SEBI-registered intermediary must collect via an @valid handle.
        sigs.append(_sig("UPI_NOT_VALID_FOR_SEBI_CLAIM", Strength.STRONG, "upi", upi=cp.value))
    elif _has_claim(ex, ClaimType.REGULATOR):
        sigs.append(_sig("UPI_FOR_REGULATOR", Strength.STRONG, "regulators", upi=cp.value))
    elif _has_claim(ex, ClaimType.BANK):
        sigs.append(_sig("UPI_PERSONAL_FOR_BANK", Strength.STRONG, "upi", upi=cp.value))
    else:
        sigs.append(_sig("UPI_UNVERIFIED", Strength.WEAK, "upi", upi=cp.value, consumer_handle=handle in reg.psp_handles))
    return sigs


# ------------------------------------------------------------------------ Domain
_PROTECTED_ZONES = ("bank.in", "fin.in", "gov.in", "nic.in")
_GOVT_ZONES = (".gov.in", ".nic.in")


_CONFUSABLE_SCRIPTS = {"CYRILLIC", "GREEK", "ARMENIAN"}   # alphabets with letters that pass for Latin ones


def _mixed_script(host: str) -> bool:
    """Latin letters next to look-alike letters from another alphabet ('sbі' with a Cyrillic і).
    Devanagari domains such as '.भारत' are genuine and are not flagged."""
    import unicodedata
    scripts = {"LATIN" if ch.isascii() else unicodedata.name(ch, "").split(" ")[0] for ch in host if ch.isalpha()}
    return "LATIN" in scripts and bool(scripts & _CONFUSABLE_SCRIPTS)


def check_domain(cp: ContactPoint, ex: Extraction) -> list[Signal]:
    reg = get_registry()
    regdom, host = registrable_domain(cp.value)
    if is_ip_host(host):
        # Banks, regulators and registered firms send people to their domain, never to a raw IP address.
        strong = _has_claim(ex, *_INSTITUTION_CLAIMS, ClaimType.SEBI_INTERMEDIARY) or bool(set(ex.asks) & {AskType.CREDENTIALS, AskType.PAY})
        return [_sig("DOMAIN_RAW_IP", Strength.STRONG if strong else Strength.WEAK, domain=host)]
    if host.startswith("xn--") or ".xn--" in host or _mixed_script(host):
        return [_sig("DOMAIN_PUNYCODE", Strength.STRONG, "banks", domain=host)]
    # 'sbi.bank.in.verify-kyc.co': an official-looking zone at the front, someone else's domain at the end
    for zone in _PROTECTED_ZONES:
        if f".{zone}." in f".{host}." and not (regdom == zone or regdom.endswith("." + zone)):
            return [_sig("DOMAIN_ZONE_SPOOF", Strength.STRONG, "banks", domain=host, zone=zone, owner=regdom)]
    owner = reg.entity_for_domain(regdom)
    claimed = _claimed_entities(ex)
    if owner is not None:
        if claimed and owner not in claimed and any(c.kind == "bank" for c in claimed):
            return [_sig("DOMAIN_OTHER_INSTITUTION", Strength.WEAK, "banks", domain=regdom, owner=owner.name)]
        return [_sig("DOMAIN_OFFICIAL", Strength.POSITIVE, "banks", domain=regdom, owner=owner.name)]
    if regdom.endswith(_GOVT_ZONES):
        # Only government bodies can register under gov.in / nic.in (NIC is the registrar), so a state police or
        # ministry site we have not listed is still genuine government. It is never treated as a look-alike.
        return [_sig("DOMAIN_GOVERNMENT", Strength.POSITIVE, "regulators", domain=regdom)]
    # Look-alike: contains a brand token or is a small edit away from an official domain.
    label = regdom.split(".")[0]
    for e in reg.all_entities():
        if e.domains and _looks_like(host, e, claimed):
            return [_sig("DOMAIN_LOOKALIKE", Strength.STRONG, "banks", domain=host, imitates=e.name, official=e.domains[0])]
        for d in e.domains:
            if Levenshtein.distance(regdom, d) <= 2 and len(d) > 6:
                return [_sig("DOMAIN_LOOKALIKE", Strength.STRONG, "banks", domain=host, imitates=e.name, official=d)]
    if regdom in _SHORTENERS:
        return [_sig("DOMAIN_SHORTENER", Strength.WEAK, domain=regdom)]
    platform = free_host_platform(host)
    if platform:
        # No bank, regulator or SEBI-registered firm runs its customer site on a free subdomain.
        strong = _has_claim(ex, *_INSTITUTION_CLAIMS, ClaimType.SEBI_INTERMEDIARY)
        return [_sig("DOMAIN_FREE_HOSTING", Strength.STRONG if strong else Strength.WEAK, domain=regdom, platform=platform)]
    if regdom in _MESSAGING:
        return [_sig("LINK_TO_PRIVATE_CHAT", Strength.WEAK, domain=regdom)]
    govt = [e for e in _claimed_entities(ex, (ClaimType.REGULATOR,)) if e.government]
    if govt:
        # A message in the name of a government body that sends you to a site outside gov.in / nic.in.
        return [_sig("DOMAIN_NOT_GOV_FOR_GOVT_CLAIM", Strength.STRONG, "regulators", domain=regdom, claimed=govt[0].name)]
    if _has_claim(ex, *_INSTITUTION_CLAIMS):
        return [_sig("DOMAIN_NOT_OFFICIAL_FOR_CLAIM", Strength.WEAK, "banks", domain=regdom, label=label)]
    return [_sig("DOMAIN_UNVERIFIED", Strength.WEAK, domain=regdom)]


# ------------------------------------------------------------------------- Phone
def check_phone(cp: ContactPoint, ex: Extraction) -> list[Signal]:
    reg = get_registry()
    p = cp.value
    sigs: list[Signal] = []
    risk = reg.fri.get(p)
    if risk in ("VERY_HIGH", "HIGH"):
        sigs.append(_sig("PHONE_FRI_HIGH", Strength.STRONG if risk == "VERY_HIGH" else Strength.WEAK, "fri", phone=p, risk=risk, mock=True))
    if p.startswith("1600"):
        sigs.append(_sig("PHONE_1600_SERIES", Strength.INFO, "trai_1600", phone=p))
    elif p.startswith("140"):
        sigs.append(_sig("PHONE_140_PROMO", Strength.INFO, "trai_1600", phone=p))
    elif p.startswith(("1800", "1860")):
        sigs.append(_sig("PHONE_TOLL_FREE", Strength.INFO, phone=p))
    elif len(p) == 10 and p[0] in "6789":
        if _has_claim(ex, ClaimType.BANK, ClaimType.NBFC, ClaimType.REGULATOR) and _staff_number(ex.text, p):
            # "call your RM Ravi on 98...": individual bank staff do use mobiles; a helpline or "officer" never does
            sigs.append(_sig("PHONE_MOBILE_STAFF", Strength.WEAK, "trai_1600", phone=p))
        elif _has_claim(ex, ClaimType.BANK, ClaimType.NBFC, ClaimType.REGULATOR):
            sigs.append(_sig("PHONE_MOBILE_FOR_INSTITUTION", Strength.STRONG, "trai_1600", phone=p))
        else:
            sigs.append(_sig("PHONE_UNVERIFIED", Strength.WEAK, phone=p))
    return sigs


# "your RM / your loan officer / your agent" names one staff member; scams say "our officer / our customer care".
_STAFF_ROLE = re.compile(r"\b(?:relationship\s+manager|branch\s+manager|personal\s+banker|policy\s+agent"
                         r"|your\s+(?:rm|agent|loan\s+officer|account\s+manager|branch))\b", re.IGNORECASE)


def _staff_number(text: str, phone: str) -> bool:
    """The number is given as a named staff member's (within a few words before it), not as a helpline."""
    digits = re.sub(r"\D", "", text)
    if phone not in digits:
        return False
    for m in _STAFF_ROLE.finditer(text):
        after = re.sub(r"\D", "", text[m.end():m.end() + 45])
        if after.startswith(phone) or phone in after[:12]:
            return True
    return False


# --------------------------------------------------------------- SMS header etc.
def check_sms_header(cp: ContactPoint, ex: Extraction) -> list[Signal]:
    reg = get_registry()
    m = re.match(r"([A-Z]{2})-([A-Z0-9]{6})(?:-([PSTG]))?", cp.value)
    if not m:
        return []
    core, suffix = m.group(2), m.group(3)
    bank = reg.bank_for_header(core)
    if bank and suffix in ("S", "T", None):
        # Context only: a header written inside the text proves nothing, anyone can type "VM-SBIINB".
        # Only the phone's own sender line is the real header, and Pramaan never sees it.
        return [_sig("SMS_HEADER_KNOWN", Strength.INFO, "dlt", header=cp.value, owner=bank.name, suffix=suffix)]
    if bank and suffix == "P":
        return [_sig("SMS_HEADER_PROMO", Strength.INFO, "dlt", header=cp.value, owner=bank.name)]
    return [_sig("SMS_HEADER_UNKNOWN", Strength.WEAK, "dlt", header=cp.value)]


def check_sebi_reg(cp: ContactPoint, ex: Extraction) -> list[Signal]:
    reg = get_registry()
    rec = reg.sebi_snapshot.get(cp.value)
    if rec:
        return [_sig("SEBI_REG_FOUND", Strength.POSITIVE, "sebi", reg_no=cp.value, name=rec.get("name"))]
    return [_sig("SEBI_REG_UNCONFIRMED", Strength.WEAK, "sebi", reg_no=cp.value)]


def check_email(cp: ContactPoint, ex: Extraction) -> list[Signal]:
    dom = cp.value.split("@", 1)[1]
    if dom in {"gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "rediffmail.com", "proton.me"} and _has_claim(ex, *_INSTITUTION_CLAIMS):
        return [_sig("EMAIL_FREE_FOR_INSTITUTION", Strength.STRONG, "banks", email=cp.value)]
    return [s.model_copy(update={"code": s.code.replace("DOMAIN", "EMAIL_DOMAIN")}) for s in check_domain(ContactPoint(type=ContactType.URL, value=dom, raw=dom), ex)]


_CHECKS = {
    ContactType.UPI: check_upi, ContactType.URL: check_domain, ContactType.PHONE: check_phone,
    ContactType.SMS_HEADER: check_sms_header, ContactType.SEBI_REG_NO: check_sebi_reg, ContactType.EMAIL: check_email,
}


def run_lane_a(ex: Extraction) -> LaneResult:
    reg = get_registry()
    signals: list[Signal] = []
    per_cp: list[dict] = []
    for cp in ex.contact_points:
        fn = _CHECKS.get(cp.type)
        got = fn(cp, ex) if fn else []
        per_cp.append({"type": cp.type.value, "value": cp.value, "codes": [s.code for s in got]})
        signals.extend(got)
    hit = reg.alert_list_hit(ex.text)
    if hit:
        signals.append(_sig("ALERT_LIST_HIT", Strength.STRONG, "alert_list", entity=hit["name"]))

    if any(s.strength == Strength.STRONG for s in signals):
        status = LaneStatusA.CONTRADICTED
    else:
        info_only = {s.code for s in signals if s.strength == Strength.INFO}
        # Context-only findings (e.g. "1600-series number") neither verify nor contradict.
        checkable = [c for c in per_cp if any(code not in info_only for code in c["codes"])]
        all_pos = checkable and all(
            any(s.code == code and s.strength == Strength.POSITIVE for s in signals for code in c["codes"]) for c in checkable
        )
        status = LaneStatusA.VERIFIED if all_pos and not any(s.strength == Strength.WEAK for s in signals) else LaneStatusA.UNVERIFIABLE
    return LaneResult(lane=Lane.A, status=status.value, signals=signals, detail={"contact_points": per_cp})
