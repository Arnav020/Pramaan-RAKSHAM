"""Build-for-Trust controls: masking before the AI, rate limits, Desk roles, appeals."""

import pytest
from fastapi.testclient import TestClient

from app.extract.privacy import mask_for_ai
from app.guard import RateLimiter


def test_private_numbers_are_masked_before_the_ai():
    text = ("Your OTP is 482913, share it with our officer. Card 4111 1111 1111 1111 CVV 123. Aadhaar 2345 6789 0123. "
            "A/c no: 123456789012 PAN ABCPE1234F. Pay to refund.help@ybl or call +91 9876543210")
    seen, counts = mask_for_ai(text)
    for secret in ("482913", "4111 1111 1111 1111", "CVV 123", "2345 6789 0123", "123456789012", "ABCPE1234F"):
        assert secret not in seen, secret
    assert "refund.help@ybl" in seen and "9876543210" in seen          # the scammer's identifiers stay: we need them
    assert counts["otp"] >= 1 and counts["card"] == 1 and counts["account"] == 1


def test_the_llm_prompt_never_contains_the_otp(monkeypatch):
    import app.extract.extractor as ex
    seen_prompts = []

    def fake_llm(task, system, user, **kw):
        seen_prompts.append(user)
        return {"asks": [{"type": "credentials", "quote": "share it with our officer"}]}, "test"
    monkeypatch.setattr(ex, "complete_json", fake_llm)
    out = ex.extract("Your OTP is 482913, share it with our officer immediately", use_llm=True)
    assert seen_prompts and "482913" not in seen_prompts[0] and "[OTP]" in seen_prompts[0]
    assert out.ai_masked.get("otp") == 1


def test_rate_limiter():
    rl = RateLimiter(3, 600)
    assert [rl.allow("ip") for _ in range(4)] == [True, True, True, False]
    assert rl.allow("other-ip")


@pytest.fixture()
def desk(monkeypatch):
    monkeypatch.setenv("DESK_TOKEN", "analyst-tok")
    monkeypatch.setenv("DESK_LEAD_TOKEN", "lead-tok")
    from app.server import app
    with TestClient(app) as c:
        yield c


def test_desk_login_sets_a_cookie_and_hides_the_token(desk):
    assert desk.get("/desk", headers={"accept": "application/json"}).status_code == 403
    r = desk.post("/desk/login", data={"token": "wrong"})
    assert r.status_code == 403
    r = desk.post("/desk/login", data={"token": "analyst-tok"})
    assert r.status_code == 200 and "analyst-tok" not in r.text and "analyst-tok" not in str(r.url)
    assert desk.cookies.get("desk")


def test_appeal_round_trip(desk):
    j = desk.post("/api/check", data={"text": "Pay ₹5,000 to appealtest@ybl, guaranteed 300% returns"}).json()
    a = desk.post(f"/api/appeal/{j['id']}", json={"note": "This is my own shop's UPI ID"}).json()
    assert a["status"] == "open"
    assert desk.post(f"/desk/appeal/{a['appeal']}/upheld").status_code == 403          # not signed in
    desk.post("/desk/login", data={"token": "analyst-tok"})
    page = desk.get("/desk")
    assert "This is my own shop" in page.text
    assert desk.post(f"/desk/appeal/{a['appeal']}/upheld").json()["status"] == "upheld"
    assert desk.post(f"/desk/appeal/{a['appeal']}/upheld").status_code == 409          # decided once
