"""Fusion: turn three lane results into one verdict using the rule table."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.explain.templates import HIGH_RISK_TITLE, NO_RISK_TITLE, TITLES, reason_for
from app.models import Extraction, Lane, LaneResult, Signal, Strength, Verdict, VerdictLevel

RULES_PATH = Path(__file__).with_name("rules_v2.yaml")

# Order in which strong reasons are shown: visible proof first.
_LANE_PRIORITY = {Lane.B: 0, Lane.A: 1, Lane.C: 2}
_STRENGTH_PRIORITY = {Strength.STRONG: 0, Strength.POSITIVE: 1, Strength.WEAK: 2, Strength.INFO: 3}
# Within a lane, the most explanatory reason first (a named scam pattern beats a generic ask).
_CODE_PRIORITY = {"SOURCE_MISMATCH": 0, "DOC_SOURCE_MISMATCH": 0, "SEALED": 0, "OFFICIAL_ORIGINAL": 0, "SEAL_CONTENT_CHANGED": 0, "SEAL_INVALID": 0, "PLAYBOOK_MATCH": 1, "ASK_CREDENTIALS": 1, "ALERT_LIST_HIT": 1, "CAMPAIGN_MEMORY_HIT": 0, "QR_PAYEE_NOT_CLAIMED_ENTITY": 0, "DOC_REFERENCE_IMPOSSIBLE": 0, "DOC_ACTIVE_CONTENT": 0}


@lru_cache(maxsize=1)
def load_rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


def strong_lanes(lanes: dict[str, LaneResult]) -> list[Lane]:
    return sorted({s.lane for r in lanes.values() if r.available for s in r.signals if s.strength == Strength.STRONG}, key=lambda l: l.value)


def _matches(cond: dict, lanes: dict[str, LaneResult], strong: list[Lane]) -> bool:
    a, b, c = lanes.get("A"), lanes.get("B"), lanes.get("C")
    all_codes = {s.code for r in lanes.values() for s in r.signals}
    if "min_strong_lanes" in cond and len(strong) < cond["min_strong_lanes"]:
        return False
    if "max_strong_lanes" in cond and len(strong) > cond["max_strong_lanes"]:
        return False
    if "has_signal" in cond and cond["has_signal"] not in all_codes:
        return False
    if "no_signal" in cond and cond["no_signal"] in all_codes:
        return False
    if "lane_a" in cond and (a is None or a.status != cond["lane_a"]):
        return False
    if "lane_b_in" in cond and (b is not None and b.status not in cond["lane_b_in"]):
        return False
    if "lane_c_not" in cond and (c is not None and c.status == cond["lane_c_not"]):
        return False
    if cond.get("corroborated") and not corroborated(lanes):
        return False
    return True


def corroborated(lanes: dict[str, LaneResult]) -> bool:
    """Two independent lanes agree, one of them less sharply: Lane A caught a hard contradiction (look-alike domain,
    personal UPI ID or mobile number for an institution...) AND Lane C matched a published scam script, with pressure
    or a request. Lane C need not be strong on its own, because Lane A has already caught the sender lying."""
    a, c = lanes.get("A"), lanes.get("C")
    if a is None or c is None or not a.available or not c.available:
        return False
    a_strong = any(s.strength == Strength.STRONG for s in a.signals)
    # A near-complete match by the phrase rules (at least 3 of 4 indicator groups), never a partial, semantic or
    # AI-named one: a forwarded murder-mystery riddle mentioning police must not corroborate anything.
    script = any(s.code == "PLAYBOOK_MATCH" and str(s.params.get("method", "")).startswith("rules")
                 and (s.params.get("score") or 0) >= 0.75 for s in c.signals)
    pushing = c.status != "NONE" and (any(s.code.startswith(("PRESSURE_", "ASK_")) for s in c.signals) or bool(c.detail.get("asks")))
    return a_strong and script and pushing


def user_visible(s: Signal) -> bool:
    """The voice detector stays a weak signal in Lane B and in the analyst trace, but is never shown to
    users as a reason: on our own data it called every genuine voice fake (see the Lab)."""
    return not (s.code == "DETECTOR_INDICATOR" and s.params.get("kind") == "audio")


def pick_reasons(level: VerdictLevel, signals: list[Signal], limit: int = 3) -> list[Signal]:
    signals = [s for s in signals if user_visible(s)]
    if level == VerdictLevel.GREEN:
        pool = [s for s in signals if s.strength == Strength.POSITIVE]
    elif level == VerdictLevel.RED:
        # the named scam script is part of the evidence for a corroborated red, even when it is weak on its own
        pool = [s for s in signals if s.strength == Strength.STRONG or s.code == "PLAYBOOK_MATCH"]
    else:
        pool = [s for s in signals if s.strength in (Strength.STRONG, Strength.WEAK, Strength.POSITIVE)]
        if not pool:   # never an amber card with no reason: context notes ("anyone can type a header") explain it
            pool = [s for s in signals if s.strength == Strength.INFO]
    pool.sort(key=lambda s: (_STRENGTH_PRIORITY[s.strength], _LANE_PRIORITY[s.lane], _CODE_PRIORITY.get(s.code, 5)))
    if level == VerdictLevel.AMBER:
        # Amber shows the strongest concern first, then what DID check out, then hints.
        order = {Strength.STRONG: 0, Strength.POSITIVE: 1, Strength.WEAK: 2, Strength.INFO: 3}
        pool.sort(key=lambda s: (order[s.strength], _LANE_PRIORITY[s.lane], _CODE_PRIORITY.get(s.code, 5)))
    chosen, seen_codes, seen_lanes = [], set(), set()
    # First pass: one reason per lane, so the card shows independent evidence.
    for s in pool:
        if s.lane not in seen_lanes and s.code not in seen_codes:
            chosen.append(s); seen_lanes.add(s.lane); seen_codes.add(s.code)
    for s in pool:
        if len(chosen) >= limit:
            break
        if s.code not in seen_codes:
            chosen.append(s); seen_codes.add(s.code)
    return chosen[:limit]


def fuse(extraction: Extraction, lanes: dict[str, LaneResult]) -> Verdict:
    rules = load_rules()
    strong = strong_lanes(lanes)
    for rule in rules["rules"]:
        if _matches(rule.get("when", {}), lanes, strong):
            level = VerdictLevel(rule["verdict"])
            needs_analyst = bool(rule.get("needs_analyst", False))
            break
    signals = [s for r in lanes.values() if r.available for s in r.signals]
    reasons = [reason_for(s) for s in pick_reasons(level, signals)]
    kind = _amber_kind(extraction, lanes) if level == VerdictLevel.AMBER else None
    title = NO_RISK_TITLE if kind == "no_risk_signs" else HIGH_RISK_TITLE if kind == "high_risk" else TITLES[level]
    advice = ADVICE[kind or level.value]
    return Verdict(
        level=level, title_en=title["en"], title_hi=title["hi"], reasons=reasons,
        rule_id=rule["id"], rules_version=rules["version"], strong_lanes=strong,
        needs_analyst=needs_analyst, advice_en=advice[0], advice_hi=advice[1], amber_kind=kind,
        lanes=lanes, extraction=extraction,
    )


# One clear next step under every verdict. Amber says WHY it is amber, so it never reads as a shrug.
ADVICE = {
    "RED": ("Do not pay or share any code. Already paid? Call 1930 now.",
            "भुगतान न करें, कोई कोड न बताएँ। अगर पैसे भेज चुके हैं तो तुरंत 1930 पर कॉल करें।"),
    "GREEN": ("Sent from a verified official channel. Still, never share your OTP or PIN.",
              "यह आधिकारिक माध्यम से भेजा गया है। फिर भी अपना OTP या PIN कभी न बताएँ।"),
    "high_risk": ("High-risk request: don't pay or share anything until you've checked through an official channel.",
                  "ज़्यादा जोखिम वाली माँग: आधिकारिक माध्यम से जाँचे बिना न भुगतान करें, न कुछ बताएँ।"),
    "nothing_to_check": ("It gives no payment ID, number or link we can check. Contact the organisation yourself, using its official number.",
                         "इसमें कोई पेमेंट आईडी, नंबर या लिंक नहीं है जिसे जाँचा जा सके। संस्था से उसके आधिकारिक नंबर पर ख़ुद संपर्क करें।"),
    "unverified": ("Don't act on it yet. Check through an official channel first.",
                   "अभी कुछ न करें। पहले किसी आधिकारिक माध्यम से जाँच लें।"),
    "no_risk_signs": ("It asks for no money, codes or app installs, and matches no scam pattern we know. If someone later asks you to pay, check again.",
                      "इसमें न पैसे, न कोड, न ऐप इंस्टॉल की माँग है, और यह किसी जाने-माने धोखाधड़ी पैटर्न से मेल नहीं खाता। बाद में कोई भुगतान माँगे तो फिर जाँचें।"),
}


def _amber_kind(extraction: Extraction, lanes: dict[str, LaneResult]) -> str:
    """high_risk: don't act · unverified: something to check, unconfirmed · nothing_to_check: pressure but nothing checkable ·
    no_risk_signs: nothing to check and nothing risky (no ask, no pressure, no pattern, no hidden instructions)."""
    c = lanes.get("C")
    if c is not None and c.status == "HIGH":
        return "high_risk"
    b = lanes.get("B")
    # Media we looked at counts as checkable even when nothing was found: an unknown video is "unverified", never "no warning signs".
    checkable = extraction.contact_points or (b is not None and b.status not in ("NOT_APPLICABLE", "UNAVAILABLE"))
    if checkable or any(s.strength in (Strength.STRONG, Strength.WEAK) for l in lanes.values() if l.available for s in l.signals):
        return "unverified" if checkable else "nothing_to_check"
    calm = c is not None and c.available and c.status == "NONE" and not extraction.hidden_instructions
    return "no_risk_signs" if calm else "nothing_to_check"
