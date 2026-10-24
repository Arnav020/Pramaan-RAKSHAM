"""The hero flow: check -> watch -> report -> one campaign -> analyst confirms ->
Retro-Alert -> campaign memory protects the next person. Isolated DB and vault."""

import json

import pytest
from fastapi.testclient import TestClient


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
    monkeypatch.setenv("DESK_TOKEN", "test-token")
    monkeypatch.setenv("DESK_LEAD_TOKEN", "lead-token")
    with TestClient(server.app) as c:
        yield c, db, vault


def check(c, text):
    r = c.post("/api/check", data={"text": text, "lang": "en"})
    assert r.status_code == 200, r.text
    return r.json()


def test_review_once_protect_many(client):
    c, db, vault = client
    a = check(c, "SEBI registered adviser here. Guaranteed 300% returns, pay ₹10,000 to profitwealth@ybl today only!")
    b = check(c, "VIP stock group: assured returns of 200%, deposit ₹5,000 now to profitwealth@ybl")
    assert a["level"] == "RED"
    db.add_watcher(a["id"], "test", "chat-1")                      # customer A opted in to "Watch this for me"

    ra = c.post(f"/api/report/{a['id']}", json={}).json()
    rb = c.post(f"/api/report/{b['id']}", json={}).json()
    assert ra["campaign_id"] == rb["campaign_id"]                 # same UPI -> one campaign, one review
    assert "upi:profitwealth@ybl" in ra["indicators"]

    assert c.get("/desk").status_code == 403                       # analysts only
    page = c.get("/desk?t=test-token")
    assert page.status_code == 200 and ra["campaign_id"] in page.text

    assert "test-token" not in page.text and "test-token" not in str(page.url)   # the token never stays in a page or URL
    d = c.post(f"/desk/{ra['campaign_id']}/confirmed", json={}).json()
    assert d["alerts_sent"] == 1                                    # Retro-Alert to the watcher
    assert c.post(f"/desk/{ra['campaign_id']}/confirmed", json={}).json()["alerts_sent"] == 0   # never twice

    # A third person forwards a bare request with the same UPI ID: campaign memory now speaks.
    third = check(c, "Please send the amount to profitwealth@ybl")
    row = db.get_check(check_id=third["id"])
    codes = [s["code"] for s in row["verdict"]["lanes"]["A"]["signals"]]
    assert "CAMPAIGN_MEMORY_HIT" in codes

    assert c.get(f"/desk/{ra['campaign_id']}/packet.json").status_code == 403      # analysts cannot export outside the bank
    c.post("/desk/login", data={"token": "lead-token"})
    pkt = c.get(f"/desk/{ra['campaign_id']}/packet.json").json()
    assert pkt["mule_vpa_referral"] == ["profitwealth@ybl"] and pkt["signature"]

    chain = vault.verify_chain()
    assert chain["ok"] and chain["records"] >= 3
    manifest = json.loads(next((vault.VAULT / "manifests").glob("*.json")).read_text(encoding="utf-8"))
    assert vault.verify_manifest(manifest)


def test_tampered_vault_chain_is_detected(client):
    c, db, vault = client
    a = check(c, "RBI notice: deposit ₹25,000 safety deposit to rbi.safe@okaxis within 24 hours or face arrest.")
    c.post(f"/api/report/{a['id']}", json={})
    vault.append_event("note", {"x": 1})
    lines = vault.CHAIN.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0]); rec["data"]["serial"] = "PR-000000-0000"   # an insider edits history
    lines[0] = json.dumps(rec, ensure_ascii=False)
    vault.CHAIN.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = vault.verify_chain()
    assert not v["ok"] and v["broken_at"] == 1
