"""Pramaan Seal: genuine sealed messages are green; edits, copies, forgeries and revocations are caught."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.pipeline import CheckInput, run_check


@pytest.fixture()
def seals(tmp_path, monkeypatch):
    from app.store import seal
    monkeypatch.setattr(seal, "KEYS", tmp_path / "keys")
    monkeypatch.setattr(seal, "REGISTRY", tmp_path / "seals")
    return seal


def check(text):
    return asyncio.run(run_check(CheckInput(text=text, use_llm=False))).verdict


BODY = "Demo Bank: Your fixed deposit of Rs 50,000 matures on 1 Nov 2026 and will be credited to your savings account ending 4821."


def sealed_sms(seals):
    from app.render.notice import issue_sms, sms_text
    rec = issue_sms("demobank", "FD maturity", BODY)
    return rec, sms_text(BODY, rec["payload"]["code"])


def test_genuine_sealed_sms_is_green(seals):
    rec, sms = sealed_sms(seals)
    v = check(sms)
    assert v.level.value == "GREEN" and v.rule_id == "R2_sealed_content"
    assert v.reasons[0].code == "SEALED" and "Signed by Demo Bank" in v.reasons[0].text_en


def test_edited_amount_is_caught(seals):
    rec, sms = sealed_sms(seals)
    v = check(sms.replace("Rs 50,000", "Rs 5,00,000"))
    sig = next(s for s in v.lanes["B"].signals if s.code == "SEAL_CONTENT_CHANGED")
    assert sig.strength.value == "STRONG" and "500000" in sig.params["changed"]
    assert v.level.value != "GREEN"


def test_copied_seal_with_a_scam_ask_is_red(seals):
    rec, sms = sealed_sms(seals)
    verify_line = sms.splitlines()[-1]
    scam = ("Demo Bank: your account is frozen. Pay a refundable safety deposit of Rs 25,000 to demobank.refund@ybl today "
            f"to unfreeze it.\n{verify_line}")
    v = check(scam)
    assert v.level.value == "RED"
    assert {"SEAL_CONTENT_CHANGED"} <= {s.code for s in v.lanes["B"].signals}


def test_unknown_revoked_and_tampered_seals(seals):
    assert "SEAL_NOT_ISSUED" in {s.code for s in check("Verify: https://x.test/s/ABCDEFGH").lanes["A"].signals}
    rec, sms = sealed_sms(seals)
    code = rec["payload"]["code"]
    seals.revoke(code, "lead")
    assert "SEAL_REVOKED" in {s.code for s in check(sms).lanes["A"].signals}
    rec2, sms2 = sealed_sms(seals)
    p = seals.REGISTRY / f"{rec2['payload']['code']}.json"
    d = json.loads(p.read_text(encoding="utf-8")); d["text"] = d["text"].replace("50,000", "5,000")     # an insider edits the registry
    p.write_text(json.dumps(d), encoding="utf-8")
    assert "SEAL_FORGED" in {s.code for s in check(sms2).lanes["A"].signals}


def test_seal_page_and_issuer_console(seals, monkeypatch):
    monkeypatch.setenv("ISSUER_TOKENS", "demobank:issuer-tok")
    from app.server import app
    with TestClient(app) as c:
        assert c.get("/s/ABCDEFGH").status_code == 404
        assert c.get("/issuer", follow_redirects=False).status_code == 303          # sign-in first
        c.post("/issuer/login", data={"token": "issuer-tok"})
        r = c.post("/issuer/seal", data={"doc_type": "sms", "title": "FD maturity", "body": BODY})
        assert r.status_code == 200 and "Verify:" in r.text
        code = r.url.params["new"]
        page = c.get(f"/s/{code}")
        assert page.status_code == 200 and "Genuine: signed by Demo Bank" in page.text and "Rs 50,000" in page.text
