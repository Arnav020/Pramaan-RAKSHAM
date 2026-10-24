"""The semantic matcher catches rewordings the phrase rules miss, and leaves genuine messages alone.
Skips on a machine without the local model."""

import pytest

from app.lanes import semantic


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("PRAMAAN_SEMANTIC", "on")
    if not semantic.enabled():
        pytest.skip("multilingual-e5-small not downloaded")


def top(text):
    m = semantic.match(text)
    return m[0]["playbook"] if m else None


def test_rewordings_the_rules_never_saw():
    assert top("Officer says a courier with banned substances was booked using my identity, I must stay on the video call and move my savings for checking") == "digital_arrest"
    # Known miss, kept visible: a money-doubling promise with no "double"/"returns" words stays below the line
    assert top("put in fifty thousand and within a week you will get one lakh back, our members are already earning") is None
    # The two rewordings that slipped past the phrase rules on 23 Oct
    assert top("He says he is from CBI, a parcel in my name has drugs, I must transfer 50,000 to cbi.verify@ybl now and tell no one.") == "digital_arrest"
    assert top("So we are having an investment scheme, you can invest anywhere above 10,000 rupees and we'll get your money doubled in just three days.") == "money_doubling"
    assert top("Earn daily from your phone by giving five star ratings to hotels, join the channel to receive work") == "task_job_scam"


def test_hindi_and_hinglish():
    assert top("आपका बिजली कनेक्शन आज रात काटा जाएगा क्योंकि पिछला बिल जमा नहीं हुआ, इस नंबर पर तुरंत संपर्क करें") == "utility_disconnection"
    assert top("aapka parcel adhoore pate ki wajah se ruka hai, link pe jaakar address update karo") in ("delivery_phishing", "kyc_phishing")


def test_genuine_messages_do_not_match():
    for text in ("Rs 1,500 debited from your account via UPI to Big Bazaar. Not you? Call us on the number on your card.",
                 "Your electricity bill for October is Rs 980, pay before 5 Nov on the official app.",
                 "Demo Bank will never ask for your OTP or PIN. Never share them with anyone.",
                 "Happy Diwali! See you at Nani's place on Sunday."):
        assert semantic.match(text) == [], text


def test_semantic_match_becomes_lane_c_evidence(monkeypatch):
    from app.extract.rules_extractor import extract_with_rules
    from app.lanes.lane_c import run_lane_c
    r = run_lane_c(extract_with_rules("Officer says a courier with banned substances was booked using my identity, "
                                      "I must stay on the video call and move my savings for checking"))
    pb = next(s for s in r.signals if s.code == "PLAYBOOK_MATCH")
    assert pb.params["playbook"] == "digital_arrest" and pb.params["method"] in ("semantic", "rules+semantic")
