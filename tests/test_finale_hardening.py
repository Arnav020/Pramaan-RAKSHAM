"""Attacks and mistakes found in the finale self-audit (24 Oct 2026), each pinned by a test.

Every test here failed, or the hole it covers existed, before the fix it guards."""

import pytest
from fastapi.testclient import TestClient

from app.models import VerdictLevel
from app.pipeline import check_text
from app.store import campaigns, seal


def v(text):
    return check_text(text, use_llm=False)


# ---------------------------------------------------------------- green must not be borrowed
def test_a_typed_sms_header_and_an_official_link_do_not_make_a_scam_green():
    # Before: GREEN "Verified sender". Anyone can type "AD-SBIINB-S" and paste an official link into a scam.
    r = v("AD-SBIINB-S Dear customer, pay Rs 99 KYC fee today at https://onlinesbi.sbi to keep account active.")
    assert r.level != VerdictLevel.GREEN


def test_a_sms_header_in_the_text_is_context_not_proof():
    r = v("VM-HDFCBK-S: Rs 500 debited from a/c XX12")
    sig = next(s for s in r.lanes["A"].signals if s.code == "SMS_HEADER_KNOWN")
    assert sig.strength.value == "INFO" and "Anyone can type a header" in [x for x in r.reasons if x.code == "SMS_HEADER_KNOWN"][0].text_en \
        if any(x.code == "SMS_HEADER_KNOWN" for x in r.reasons) else sig.strength.value == "INFO"


def test_genuine_official_link_alone_is_still_green():
    r = v("HDFC Bank: your statement is ready at https://hdfc.bank.in/statements")
    assert r.level == VerdictLevel.GREEN


# ---------------------------------------------------------------- calm messages read calm
def test_everyday_messages_are_not_presented_as_warnings():
    for text in ("Happy birthday! Let's meet for dinner tomorrow.",
                 "Rs 500 debited from a/c XX1234 by UPI to merchant. Not you? Call 1800 1234. -SBI"):
        r = v(text)
        assert r.level == VerdictLevel.AMBER and r.amber_kind == "no_risk_signs", text
        assert r.title_en.startswith("No warning signs")


def test_a_genuine_otp_sms_is_not_a_scam_pattern():
    r = v("123456 is your OTP for login to SBI YONO. Never share it with anyone. -SBI")
    assert not any(s.code == "PLAYBOOK_MATCH" for s in r.lanes["C"].signals)
    assert r.amber_kind == "no_risk_signs"


def test_otp_request_still_matches_the_vishing_pattern():
    r = v("This is SBI customer care. Please share the OTP you just received to stop the block on your account.")
    assert any(s.code == "PLAYBOOK_MATCH" and s.params["playbook"] == "otp_vishing" for s in r.lanes["C"].signals)
    assert r.amber_kind == "high_risk"


# ---------------------------------------------------------------- seal edits
SEALED = ("Dear customer, Demo Bank will never ask you to pay a fee to unblock your account. "
          "Do not share your OTP. Call 1600123456 for help.")


@pytest.mark.parametrize("edited", [
    SEALED.replace("never ", ""),                                   # meaning flipped by deleting one word
    SEALED.replace("Do not share your OTP", "Do share your OTP"),
    "Call 1600123456 for help. Do not share your OTP. Dear customer, Demo Bank will never ask you to pay a fee to unblock your account.",
])
def test_seal_compare_catches_meaning_changing_edits(edited):
    assert not seal.compare(SEALED, edited)["same"]


def test_seal_compare_accepts_the_exact_text_and_ocr_noise():
    assert seal.compare(SEALED, SEALED)["same"]
    assert seal.compare(SEALED, "Is this real? " + SEALED)["same"]
    assert seal.compare(SEALED, SEALED.replace("Dear", "Deer"), tolerant=True)["same"]


# ---------------------------------------------------------------- campaign poisoning
def test_shared_platforms_and_official_domains_never_become_campaign_indicators():
    verdict = {"lanes": {}, "extraction": {"contact_points": [
        {"type": "url", "value": "https://t.me/joinchat/abc"}, {"type": "url", "value": "https://www.youtube.com/watch?v=x"},
        {"type": "url", "value": "https://hdfc.bank.in/x"}, {"type": "url", "value": "https://profit-guru-signals.com/"},
        {"type": "upi", "value": "advisor.brk@validhdfc"}, {"type": "upi", "value": "profitwealth@ybl"}]}}
    inds = campaigns.indicators_from_verdict(verdict)
    assert "domain:profit-guru-signals.com" in inds and "upi:profitwealth@ybl" in inds
    for bad in ("domain:t.me", "domain:youtube.com", "domain:hdfc.bank.in", "upi:advisor.brk@validhdfc"):
        assert bad not in inds, bad


# ---------------------------------------------------------------- web endpoints
@pytest.fixture()
def desk(monkeypatch):
    monkeypatch.setenv("DESK_TOKEN", "analyst-tok")
    monkeypatch.setenv("DESK_LEAD_TOKEN", "lead-tok")
    from app.server import app
    with TestClient(app) as c:
        yield c


def test_malformed_json_is_a_400_not_a_server_error(desk):
    j = desk.post("/api/check", data={"text": "Pay ₹5,000 to badjson@ybl"}).json()
    r = desk.post(f"/api/appeal/{j['id']}", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert desk.post("/api/appeal-lookup", content=b"[]", headers={"Content-Type": "application/json"}).status_code == 400


def test_unknown_campaign_is_404(desk):
    desk.post("/desk/login", data={"token": "lead-tok"})
    assert desk.post("/desk/C-ABCDEF/confirmed").status_code == 404
    assert desk.get("/desk/C-ABCDEF/packet.json").status_code == 404


def test_reporting_twice_seals_the_evidence_once(desk):
    j = desk.post("/api/check", data={"text": "SEBI registered adviser. Guaranteed 300% returns. Pay ₹9,000 to twicereport@ybl"}).json()
    a = desk.post(f"/api/report/{j['id']}").json()
    b = desk.post(f"/api/report/{j['id']}").json()
    assert b["chain_seq"] == a["chain_seq"] and b.get("already_reported")


def test_appeal_is_answered_and_the_verdict_marked_corrected(desk):
    j = desk.post("/api/check", data={"text": "Pay ₹5,000 to correctme@ybl, guaranteed 300% returns"}).json()
    a = desk.post(f"/api/appeal/{j['id']}", json={"note": "my own shop"}).json()
    again = desk.post(f"/api/appeal/{j['id']}", json={"note": "again"}).json()
    assert again["appeal"] == a["appeal"]                         # one open appeal per check: no queue flooding
    assert 'id="appeal"' in desk.get(f"/p/{j['id']}").text
    desk.post("/desk/login", data={"token": "analyst-tok"})
    out = desk.post(f"/desk/appeal/{a['appeal']}/upheld", json={"note": "Shop UPI ID confirmed by the bank."}).json()
    assert out["status"] == "upheld" and out["minutes"] >= 0
    page = desk.get(f"/p/{j['id']}").text
    assert "Corrected by a bank analyst" in page and "Shop UPI ID confirmed" in page
    assert 'id="appeal"' not in page                              # no second appeal on a corrected verdict
    from app.store import db
    assert db.appeal_counts()["median_minutes"] is not None


# ---------------------------------------------------------------- live video registration
def test_registering_a_live_video_needs_an_issuer_and_consent(monkeypatch):
    monkeypatch.setenv("ISSUER_TOKENS", "demobank:issuer-tok")
    from app.server import app
    with TestClient(app) as c:
        files = {"video": ("clip.mp4", b"not really a video", "video/mp4")}
        assert c.post("/issuer/video", data={"consent": "yes"}, files=files).status_code == 403      # not signed in
        c.post("/issuer/login", data={"token": "issuer-tok"})
        r = c.post("/issuer/video", data={"title": "x"}, files=files)
        assert r.status_code == 400 and "consent" in r.json()["detail"]
        r = c.post("/issuer/video", data={"consent": "yes"}, files=files)
        assert r.status_code == 400                                                                   # not a video: refused
        assert c.post("/issuer/video/live_nothere/edit", files={"voice": ("v.wav", b"x", "audio/wav")}).status_code == 404
        assert "Register an official video" in c.get("/issuer").text


def test_links_to_a_bare_ip_address_are_found_and_flagged():
    # Found in the open-dataset failure list: such links used to be dropped, so nothing was checked.
    r = v("SBI alert: your account is locked. Log in via the secure link http://95.141.32.7:81/default.aspx and enter your PIN.")
    assert any(c.value.startswith("http://95.141.32.7") for c in r.extraction.contact_points)
    raw = next(s for s in r.lanes["A"].signals if s.code == "DOMAIN_RAW_IP")
    assert raw.strength.value == "STRONG"
    assert not v("Version 1.2.3.4 of the app is out.").extraction.contact_points
