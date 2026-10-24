"""PROTECT: Lookup before paying, the partner API, the pay-time mock and the bot shortcut."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.lookup import lookup


def test_lookup_results_by_kind():
    assert lookup("demofund.mf@validhdfc")["result"] == "official"
    assert lookup("sbi-kyc-update.in")["result"] == "pattern"
    assert lookup("hello there")["result"] == "invalid"
    r = lookup("someone.new@ybl")
    assert r["result"] == "no_warning" and "not proof of safety" in r["headline_en"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.store.db as db
    import app.store.vault as vault
    import app.service as service
    import app.server as server
    import app.extract.extractor as ex
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(service, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(server, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(vault, "VAULT", tmp_path / "vault")
    monkeypatch.setattr(vault, "CHAIN", tmp_path / "vault" / "chain.jsonl")
    monkeypatch.setattr(ex, "complete_json", lambda *a, **k: (_ for _ in ()).throw(ex.LLMUnavailable("offline")))
    monkeypatch.setenv("DESK_TOKEN", "a-tok")
    monkeypatch.setenv("PARTNER_KEYS", "demobank:pk-123")
    with TestClient(server.app) as c:
        yield c


def test_confirmed_campaign_shows_in_lookup_and_api(client):
    j = client.post("/api/check", data={"text": "SEBI registered. Guaranteed 300% returns, pay ₹10,000 to lookupscam@ybl today"}).json()
    rep = client.post(f"/api/report/{j['id']}", json={}).json()
    client.post("/desk/login", data={"token": "a-tok"})
    client.post(f"/desk/{rep['campaign_id']}/confirmed", json={})
    res = client.get("/api/v1/lookup", params={"q": "lookupscam@ybl"}, headers={"X-Pramaan-Key": "pk-123"}).json()
    assert res["result"] == "warning" and res["campaigns"] and res["seen_30d"] >= 1 and res["partner"] == "demobank"
    page = client.get("/lookup", params={"q": "lookupscam@ybl"})
    assert page.status_code == 200 and "Confirmed by bank analysts" in page.text
    a = client.post("/api/appeal-lookup", json={"indicator": "upi:lookupscam@ybl", "note": "this is my shop"}).json()
    assert a["status"] == "open"
    assert "Lookup · lookupscam@ybl" in client.get("/desk").text


def test_partner_key_skips_the_public_rate_limit(client, monkeypatch):
    import app.guard as guard
    monkeypatch.setattr(guard, "WEB", guard.RateLimiter(2, 600))
    for _ in range(2):
        assert client.get("/api/v1/lookup", params={"q": "a.b@ybl"}).status_code == 200
    assert client.get("/api/v1/lookup", params={"q": "a.b@ybl"}).status_code == 429
    assert client.get("/api/v1/lookup", params={"q": "a.b@ybl"}, headers={"X-Pramaan-Key": "pk-123"}).status_code == 200


def test_pay_mock_page(client):
    r = client.get("/demo-bank/pay")
    assert r.status_code == 200 and "MOCK" in r.text and "/api/v1/lookup" in r.text


def test_bot_adds_lookup_for_a_bare_id(monkeypatch):
    from app.channels.telegram import Bot

    class FakeBot(Bot):
        def __init__(self):
            super().__init__("TEST:TOKEN"); self.sent = []

        async def call(self, method, **p):
            self.sent.append((method, p)); return {"message_id": len(self.sent)}

    async def fake_render(rec, lang="en"):
        return b"png"
    monkeypatch.setattr("app.channels.telegram.renderer.render", fake_render)
    bot = FakeBot()
    asyncio.run(bot.on_message({"chat": {"id": 777}, "message_id": 1, "text": "profitwealth@ybl", "from": {}}))
    texts = [p.get("text") or "" for m, p in bot.sent if m == "sendMessage"]
    assert any(t.startswith("🔎") and "profitwealth@ybl" in t for t in texts)


def test_confirmed_dates_are_shown_in_ist():
    from app.store.campaigns import ist_date
    assert ist_date("2026-10-23T21:40:00+00:00") == "2026-10-24"      # 03:10 IST is not "yesterday"
    assert ist_date(None) == ""


def test_seal_qr_is_not_called_a_payment():
    from app.server import env
    src = env.loader.get_source(env, "proof.html")[0]
    # "really pays" only when the QR holds a UPI payment; a seal link gets its own neutral label
    assert src.index("{% if q.upi %}") < src.index("THE QR CODE REALLY PAYS") < src.index("THE QR CODE OPENS A PRAMAAN SEAL PAGE")
