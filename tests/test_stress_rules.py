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


def test_investment_call_to_action_is_a_payment_request_even_when_ocr_drops_spaces():
    for t in ("START INVESTING NOW", "STARTINVESTINGNOW", "Invest now and get 100% guaranteed returns"):
        assert AskType.PAY in detect_asks(t), t


def test_money_doubling_poster_text_is_high_risk():
    r = v("DOUBLE YOUR MONEY IN JUST 3 DAYS! Safe · Secure · 100% Guaranteed Returns. STARTER PLAN 1,000 Get 2,000 in 3 Days. "
          "START INVESTING NOW")
    assert r.level == VerdictLevel.AMBER and r.amber_kind == "high_risk"


def test_genuine_sip_ad_is_not_warned():
    r = v("Start investing in mutual funds with a SIP from Rs 500 a month. Mutual fund investments are subject to market risks.")
    assert r.level != VerdictLevel.RED and r.amber_kind != "high_risk"


def test_kyc_script_with_a_bare_lookalike_domain_and_mobile_is_red():
    # Before: only Lane A was strong (look-alike domain, mobile for a bank); the KYC playbook missed the bare address.
    r = v("Your SBI KYC expires today, update at sbi-kyc-update.in or call 9123456780")
    assert r.level == VerdictLevel.RED


# ---------------------------------------------------------------- 25 Oct, after the blind set: no new false reds
GENUINE_STAFF = (
    "Dear HDFC Bank customer, your relationship manager Ravi (9876543210) will call you today about your pending KYC update. You can also visit your branch.",
    "SBI: Your KYC update is due by 31 Dec. Please visit your branch or call your branch manager on 9812345678 for help. Never share OTP.",
    "ICICI Bank: Your credit card bill of Rs 12,400 is due tomorrow. Pay via iMobile or call your RM on 9898989898. Late fee applies.",
    "Axis Bank: Loan EMI of Rs 8,200 is overdue. Please pay today to avoid penalty. For help call your loan officer 9000011111.",
)


def test_genuine_bank_reminders_with_a_staff_mobile_are_never_red():
    for t in GENUINE_STAFF:
        r = v(t)
        assert r.level != VerdictLevel.RED and r.amber_kind != "high_risk", t


def test_helpline_or_officer_on_a_mobile_is_still_red():
    r = v("SBI customer care: your account is blocked. Call our officer on 9123456780 and share the OTP to unblock.")
    assert r.level == VerdictLevel.RED


def test_corroborated_red_needs_lane_a_and_a_script():
    from app.fusion.engine import corroborated
    r = v("Your SBI KYC expires today, update at sbi-kyc-update.in or call 9123456780")
    assert corroborated(r.lanes) and r.level == VerdictLevel.RED
    r = v("Your KYC is due. Please visit your branch.")      # a script hint alone, nothing contradicted in Lane A
    assert not corroborated(r.lanes) and r.level != VerdictLevel.RED


def test_a_card_bill_is_not_the_instant_loan_scam():
    r = v("ICICI Bank: Your credit card bill of Rs 12,400 is due tomorrow. Pay via iMobile.")
    assert not any(s.code == "PLAYBOOK_MATCH" and s.params.get("playbook") == "fake_loan" for s in r.lanes["C"].signals)


def test_ai_named_script_alone_is_never_strong():
    from app.extract.rules_extractor import extract_with_rules
    from app.lanes.lane_c import run_lane_c
    ex = extract_with_rules("Your KYC update is pending. You can visit your branch.")
    ex.ai_script = {"id": "kyc_phishing", "quote": "Your KYC update is pending"}
    c = run_lane_c(ex)
    pm = [s for s in c.signals if s.code == "PLAYBOOK_MATCH"]
    assert pm and pm[0].strength.value == "WEAK" and pm[0].params["method"] in ("ai", "rules+ai")


def test_ai_threat_needs_a_real_threat_in_its_quote():
    from app.extract.extractor import merge_llm
    from app.extract.rules_extractor import extract_with_rules
    base = extract_with_rules("Your bill is due tomorrow. Late fee applies.")
    got = merge_llm(base, {"pressure": [{"type": "threat", "quote": "Late fee applies"}]}, "test")
    assert Pressure.THREAT not in got.pressure


# ---------------------------------------------------------------- 25 Oct, found by the jury kit (public item p02)
def test_account_is_closed_and_update_pan_by_link():
    assert Pressure.THREAT in detect_pressure("Your SBI YONO Account is Closed Today")
    assert AskType.CREDENTIALS in detect_asks("Update your PAN NUMBER in the following link")
    assert AskType.CLICK_LINK in detect_asks("Update your PAN NUMBER in the following link")
    assert AskType.PAY in detect_asks("Deposit the processing charges first to claim the prize.")


def test_harmless_account_and_link_wording_is_not_a_warning():
    for t in ("Your account has been credited with Rs 500.", "Please read the link below for our new interest rates."):
        r = v(t)
        assert r.level != VerdictLevel.RED and r.amber_kind != "high_risk", t
