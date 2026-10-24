"""End-to-end verdicts on text, rules only (no LLM) so tests are deterministic."""

from app.models import Lane, LaneResult, Signal, Strength, VerdictLevel
from app.pipeline import check_text
from app.fusion.engine import fuse
from app.extract.rules_extractor import extract_with_rules


def v(text):
    return check_text(text, use_llm=False)


def test_investment_scam_is_red_with_two_lanes():
    r = v("I am a SEBI registered adviser. Guaranteed 300% returns! Join VIP group, pay ₹10,000 to profitwealth@ybl today.")
    assert r.level == VerdictLevel.RED
    assert set(r.strong_lanes) >= {Lane.A, Lane.C}
    assert 1 <= len(r.reasons) <= 3
    assert {x.lane for x in r.reasons} >= {Lane.A, Lane.C}


def test_genuine_bank_link_is_green():
    r = v("HDFC Bank: your statement is ready at https://hdfc.bank.in/statements")
    assert r.level == VerdictLevel.GREEN and r.rule_id == "R3_verified_sender"


def test_single_lane_strong_is_only_amber():
    # Strong Lane C (OTP ask) but nothing contradicted in Lane A -> amber, not red.
    r = v("Please share the OTP you just received.")
    assert r.level == VerdictLevel.AMBER


def test_detector_only_never_red():
    ex = extract_with_rules("Watch this video")
    lanes = {
        "A": LaneResult(lane=Lane.A, status="UNVERIFIABLE"),
        "B": LaneResult(lane=Lane.B, status="INDICATORS", signals=[Signal(code="DETECTOR_INDICATOR", lane=Lane.B, strength=Strength.WEAK)]),
        "C": LaneResult(lane=Lane.C, status="NONE"),
    }
    assert fuse(ex, lanes).level == VerdictLevel.AMBER


def test_same_lane_strong_signals_do_not_count_twice():
    ex = extract_with_rules("x")
    lanes = {
        "A": LaneResult(lane=Lane.A, status="CONTRADICTED", signals=[
            Signal(code="DOMAIN_LOOKALIKE", lane=Lane.A, strength=Strength.STRONG),
            Signal(code="PHONE_MOBILE_FOR_INSTITUTION", lane=Lane.A, strength=Strength.STRONG)]),
        "B": LaneResult(lane=Lane.B, status="NOT_APPLICABLE"),
        "C": LaneResult(lane=Lane.C, status="NONE"),
    }
    assert fuse(ex, lanes).level == VerdictLevel.AMBER


def test_verified_but_high_ask_goes_to_analyst():
    r = v("Pay your SIP to abcsec.brk@validhdfc. Guaranteed 40% returns, pay ₹50,000 today only!")
    assert r.level == VerdictLevel.AMBER and r.needs_analyst


def test_digital_arrest_red():
    r = v("CBI officer here. Parcel with drugs in your name, you will be arrested. Transfer ₹50,000 for verification to cbi.verify@ybl. Don't tell anyone.")
    assert r.level == VerdictLevel.RED


def test_reason_text_never_says_fake():
    r = v("I am a SEBI registered adviser. Guaranteed 300% returns! pay ₹10,000 to profitwealth@ybl today.")
    for x in r.reasons:
        assert "fake" not in x.text_en.lower() and "fraudster" not in x.text_en.lower()
        assert "?" not in x.text_en  # every template parameter was filled


def test_kyc_phishing_lookalike_is_red():
    r = v("Dear SBI customer your KYC has expired and account will be blocked today. Update immediately at sbi-kyc-update.in/verify")
    assert r.level == VerdictLevel.RED


def test_genuine_bank_warning_is_not_a_scam_pattern():
    r = v("Demo Bank will never ask for your OTP, PIN or password. We never ask you to move money to a safe account. If you have lost money, call 1930.")
    assert r.level == VerdictLevel.AMBER
    assert not any(x.code == "PLAYBOOK_MATCH" for x in r.reasons)
    assert "credentials" not in [a.value for a in r.extraction.asks]


def test_negation_cannot_hide_a_real_ask():
    r = v("SBI never asks for OTP. But for this refund, share the OTP you just got. Visit sbi-refund-help.com")
    assert "credentials" in [a.value for a in r.extraction.asks]
    assert r.level == VerdictLevel.RED
