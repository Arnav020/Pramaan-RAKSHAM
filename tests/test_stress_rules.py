"""Gaps found by the finale-night stress test (eval/stress_set.yaml), each pinned by a test.

Every scam test below came out "check first" (or worse) before its fix; every genuine test guards against the
fix overreaching."""

from app.extract.claims import detect_claims
from app.extract.contacts import normalise_text
from app.extract.intent_rules import detect_asks, detect_pressure
from app.models import AskType, ClaimType, Pressure, VerdictLevel
from app.pipeline import check_text


def v(text):
    return check_text(text, use_llm=False)


def codes(r, lane):
    return {s.code: s.strength.value for s in r.lanes[lane].signals}


# ---------------------------------------------------------------- asks the rules used to miss
def test_card_bank_pan_details_are_a_credentials_ask():
    for t in ("Update your KYC and enter your debit card details here.",
              "Verify your PAN at the link to avoid a block.",
              "Submit your bank details to receive the refund.",
              "Please read out the verification code you received to our agent."):
        assert AskType.CREDENTIALS in detect_asks(t), t


def test_safety_advice_about_card_details_is_not_an_ask():
    assert AskType.CREDENTIALS not in detect_asks("Never share your card details or OTP with anyone.")


def test_hinglish_and_hindi_payment_requests():
    for t in ("Bas 10,000 bhejo is UPI pe: doublepaisa@ybl", "Send 5k on PhonePe now", "अभी 10000 रुपये भेज दो",
              "Please return it to ramesh.k77@ybl"):
        assert AskType.PAY in detect_asks(t), t


def test_call_a_mobile_number_is_a_call_back():
    assert AskType.CALL_BACK in detect_asks("Call electricity officer 9123456780 now.")


def test_deadline_cut_off_is_a_threat_but_a_due_date_is_not():
    assert Pressure.THREAT in detect_pressure("Your electricity connection will be cut tonight.")
    assert Pressure.THREAT not in detect_pressure("Your electricity bill of Rs 1,540 is due on 30 Oct.")


def test_fixed_multiple_and_advance_fee_promises():
    assert Pressure.GUARANTEED_RETURNS in detect_pressure("Turn Rs 21,000 into Rs 5 lakh in a month.")
    assert Pressure.TOO_GOOD in detect_pressure("Pay Rs 6,800 TDS charges to release the bonus.")


# ---------------------------------------------------------------- obfuscation
def test_spaced_letters_are_joined():
    assert normalise_text("account b l o c k e d, share O T P") == "account blocked, share OTP"


def test_a_link_broken_across_lines_is_rejoined():
    assert "http://sbi-yono-update.co.in/kyc" in normalise_text("immediately: http://sbi-yono-\nupdate.co.in/kyc")


def test_squashed_ocr_court_name_is_a_claim_but_courtesy_is_not():
    assert any(c.type == ClaimType.REGULATOR for c in detect_claims("SUPREMECOURTOFINDIA Office of the Registrar"))
    assert not any(c.type == ClaimType.REGULATOR for c in detect_claims("Thanks for your courtesy yesterday."))


# ---------------------------------------------------------------- government senders
def test_government_claim_with_a_non_gov_link_is_strong_and_red_with_an_ask():
    r = v("Income Tax Department: refund of Rs 18,760 approved. Submit your bank details at incometax-refund-gov.in today.")
    assert r.level == VerdictLevel.RED


def test_govt_claim_with_unrelated_domain_is_a_strong_contradiction():
    r = v("Parivahan: pay your pending challan at quick-challan-pay.com")
    assert codes(r, "A").get("DOMAIN_NOT_GOV_FOR_GOVT_CLAIM") == "STRONG"


def test_genuine_gov_in_messages_are_not_warned():
    for t in ("A challan of Rs 500 has been issued for vehicle DL01AB1234. View and pay only on echallan.parivahan.gov.in.",
              "Your Speed Post article EB123456789IN has been delivered. Track at indiapost.gov.in.",
              "Punjab Police advisory: report cyber fraud at punjabpolice.gov.in or call 1930."):
        r = v(t)
        assert r.level != VerdictLevel.RED and r.amber_kind != "high_risk", t
        assert not any(s.code == "DOMAIN_LOOKALIKE" for s in r.lanes["A"].signals), t


# ---------------------------------------------------------------- new playbooks and the high-risk title
def test_new_scam_scripts_are_high_risk():
    for t in ("Hi Mom, this is my new number, my phone broke. Send Rs 15,000 to aarav.new21@okaxis urgently, don't call.",
              "I sent Rs 5,000 to you by mistake, please return it to ramesh.k77@ybl, urgent.",
              "The gift I sent from London is held at customs. Pay Rs 45,000 clearance to receive it.",
              "Amazon work from home job. Pay Rs 1,500 registration fee to amazonjobs.hr@paytm to confirm your seat."):
        r = v(t)
        assert r.amber_kind == "high_risk" or r.level == VerdictLevel.RED, t


def test_high_risk_amber_reads_as_a_warning():
    r = v("Sir I am from your bank. To cancel the transaction please tell me the 6 digit OTP you just received.")
    assert r.level == VerdictLevel.AMBER and r.amber_kind == "high_risk"
    assert r.title_en.startswith("Warning signs of a scam")


def test_genuine_otp_and_friend_requests_stay_calm():
    for t in ("482913 is your OTP for a transaction of Rs 2,450 at Amazon. Do not share it with anyone. -HDFC Bank",
              "Hey, can you pay me back the 300 for lunch? My UPI is rahul.kumar@okhdfcbank. No rush!",
              "Kal movie ke tickets maine book kar diye, tu 250 bhej dena jab time mile."):
        r = v(t)
        assert r.level != VerdictLevel.RED and r.amber_kind != "high_risk", t


def test_ai_guaranteed_returns_needs_a_quote_about_money_growing():
    from app.extract.extractor import merge_llm
    from app.extract.rules_extractor import extract_with_rules
    from app.models import Pressure
    base = extract_with_rules("Bhai 2000 bhej de, kal lauta dunga.")
    got = merge_llm(base, {"pressure": [{"type": "guaranteed_returns", "quote": "kal lauta dunga"}]}, "test")
    assert Pressure.GUARANTEED_RETURNS not in got.pressure
    base = extract_with_rules("Invest now, 20% profit every week, pakka.")
    got = merge_llm(base, {"pressure": [{"type": "guaranteed_returns", "quote": "20% profit every week"}]}, "test")
    assert Pressure.GUARANTEED_RETURNS in got.pressure
