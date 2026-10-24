from app.extract.rules_extractor import extract_with_rules
from app.lanes.lane_a import run_lane_a


def lane_a(text):
    r = run_lane_a(extract_with_rules(text))
    return r.status, {s.code for s in r.signals}


def test_sebi_claim_with_personal_upi_is_contradicted():
    status, codes = lane_a("I am a SEBI registered investment adviser. Pay ₹10,000 to profitwealth@ybl to join.")
    assert status == "CONTRADICTED"
    assert "UPI_NOT_VALID_FOR_SEBI_CLAIM" in codes


def test_valid_handle_is_positive():
    status, codes = lane_a("SIP instalment: pay to abcsec.brk@validhdfc")
    assert "UPI_VALID_HANDLE" in codes
    assert status == "VERIFIED"


def test_bank_lookalike_domain():
    status, codes = lane_a("Dear SBI customer, your KYC expired. Update at sbi-bank.in.co/kyc now")
    assert status == "CONTRADICTED"
    assert "DOMAIN_LOOKALIKE" in codes


def test_official_bank_domain_verified():
    status, codes = lane_a("HDFC Bank: view your statement at https://hdfc.bank.in/statements")
    assert status == "VERIFIED"
    assert "DOMAIN_OFFICIAL" in codes


def test_unrelated_domains_are_not_lookalikes():
    for text in ("Read more at indianexpress.com", "see yesterday.com", "unionpark.org events"):
        status, codes = lane_a(text)
        assert "DOMAIN_LOOKALIKE" not in codes, text


def test_bank_claim_with_mobile_number():
    status, codes = lane_a("This is your bank manager from Axis Bank, call me on 9876543210 to unblock")
    assert status == "CONTRADICTED"
    assert "PHONE_MOBILE_FOR_INSTITUTION" in codes


def test_rbi_never_collects_money():
    status, codes = lane_a("RBI notice: deposit ₹25,000 safety deposit to rbi.safe@okaxis within 24 hours")
    assert status == "CONTRADICTED"
    assert "UPI_FOR_REGULATOR" in codes


def test_alert_list_entity():
    status, codes = lane_a("Trade forex with Starnet FX and earn daily")
    assert "ALERT_LIST_HIT" in codes and status == "CONTRADICTED"


def test_nothing_to_check_is_unverifiable():
    status, codes = lane_a("Hello, how are you?")
    assert status == "UNVERIFIABLE" and not codes


def test_fri_mock_very_high():
    status, codes = lane_a("Call 9000012345 for your loan")
    assert "PHONE_FRI_HIGH" in codes and status == "CONTRADICTED"


def test_known_sms_header():
    status, codes = lane_a("VM-HDFCBK-S: Rs 500 debited from a/c XX12")
    assert "SMS_HEADER_KNOWN" in codes


def test_alert_list_full_and_word_boundaries():
    assert lane_a("Open an account on OctaFX for daily profit")[0] == "CONTRADICTED"
    assert "ALERT_LIST_HIT" in lane_a("deposit at https://www.quotex.com/signup")[1]
    assert "ALERT_LIST_HIT" in lane_a("Join StarnetFX signals")[1]
    for ok in ("This plan was contrived to help", "Sign up at fxstreetview.org", "Read the XML file"):
        assert "ALERT_LIST_HIT" not in lane_a(ok)[1], ok


def test_qr_payee_name_vs_claim():
    s, codes = lane_a("RBI notice: deposit now. [UPI QR code: pay to rbi.safe@okaxis (Rahul Kumar)]")
    assert "QR_PAYEE_NOT_CLAIMED_ENTITY" in codes
    s, codes = lane_a("SEBI registered advisor. [UPI QR code: pay to profitwealth@ybl (Profit Wealth Traders)]")
    assert "QR_PAYEE_NOT_CLAIMED_ENTITY" not in codes and "QR_PAYEE_NAME" in codes


def test_homoglyph_and_zone_spoof_domains():
    # A Cyrillic 'і' in 'sbі' looks identical to the Latin 'i'
    status, codes = lane_a("SBI alert: verify now at https://sbі-kyc.in/login")
    assert status == "CONTRADICTED" and "DOMAIN_PUNYCODE" in codes
    # 'bank.in' at the front, someone else's domain at the end
    status, codes = lane_a("SBI: your account is frozen, unlock at https://sbi.bank.in.secure-unlock.co/login")
    assert status == "CONTRADICTED" and "DOMAIN_ZONE_SPOOF" in codes
    # the real thing is not touched, and Devanagari domains are genuine
    assert "DOMAIN_OFFICIAL" in lane_a("Statement ready at https://sbi.bank.in/statements")[1]
    assert not {"DOMAIN_PUNYCODE", "DOMAIN_ZONE_SPOOF"} & set(lane_a("Visit https://www.india.भारत for schemes")[1])
