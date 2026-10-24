"""Bot actions after a verdict: call mode, complaint drafts, 'Ask someone I trust'.

Telegram is never contacted: Bot.call is replaced by a recorder.
"""

import asyncio
import json

import pytest

from app.channels.telegram import Bot
from app.explain import complaint
from app.service import check_and_record
from app.store import db

SCAM = "SEBI registered adviser. Guaranteed 300% returns, pay ₹10,000 to profitwealth@ybl today. Call 9876543210"


class FakeBot(Bot):
    def __init__(self):
        super().__init__("TEST:TOKEN")
        self.sent = []

    async def call(self, method, **params):
        self.sent.append((method, params))
        return {"message_id": len(self.sent)}


@pytest.fixture(autouse=True)
def stub_card(monkeypatch):
    # The real renderer keeps one browser per event loop; these tests only need the bytes.
    async def fake_render(rec, lang="en"):
        return b"\x89PNG test card"
    monkeypatch.setattr("app.channels.telegram.renderer.render", fake_render)


@pytest.fixture(scope="module")
def rec():
    db.init()
    return asyncio.run(check_and_record(SCAM, [], channel="telegram", chat_raw=111))


def texts(bot):
    return [p.get("text") or p.get("caption") or "" for _, p in bot.sent]


def test_drafts_fill_facts_and_leave_transaction_blanks(rec):
    ncrp = complaint.ncrp_text(rec)
    assert "profitwealth@ybl" in ncrp and rec.serial in ncrp and rec.proof_url in ncrp
    assert "9876543210" in ncrp
    assert complaint.BLANK["utr"] in ncrp and complaint.BLANK["amount"] in ncrp   # we never fill transaction data
    subject, body = complaint.bank_email(rec)
    assert "profitwealth@ybl" in subject and "Fraud Risk Management" in body
    assert "1930" in complaint.full_packet(rec) and rec.serial in complaint.call_script(rec, "hi")


def test_golden_hour_buttons_send_each_draft(rec):
    bot = FakeBot()
    q = lambda data: {"id": "q", "data": data, "message": {"chat": {"id": 111}}, "from": {"language_code": "en"}}
    for action in ("paid", "cs", "ncrp", "bem", "pkt"):
        asyncio.run(bot.on_callback(q(f"{action}:{rec.id}")))
    methods = [m for m, _ in bot.sent if m != "answerCallbackQuery"]
    assert methods == ["sendMessage"] * 4 + ["sendDocument"]
    kit = next(p for m, p in bot.sent if m == "sendMessage")
    assert "ncrp:" in kit["reply_markup"] and "pkt:" in kit["reply_markup"]
    assert any("<pre>" in t and "profitwealth@ybl" in t for t in texts(bot))


def test_trust_link_round_trip(rec):
    asker, contact = FakeBot(), FakeBot()
    asyncio.run(asker.on_callback({"id": "q", "data": f"trust:{rec.id}", "message": {"chat": {"id": 111}}, "from": {"first_name": "Meena"}}))
    fwd = texts(asker)[-1]
    assert "Meena wants your advice" in fwd and "?start=t_" in fwd
    token = fwd.split("?start=t_")[1].split()[0]
    # the son opens the link from his own chat: he sees the check and an "I'll call them" button
    asyncio.run(contact.on_message({"chat": {"id": 222}, "message_id": 1, "text": f"/start t_{token}", "from": {}}))
    shown = contact.sent[-1][1]
    assert "Someone you know asked you" in (shown.get("caption") or shown.get("text"))
    assert f"tc:{token}" in shown["reply_markup"]
    # he taps it: the person who asked is told, once
    asyncio.run(contact.on_callback({"id": "q", "data": f"tc:{token}", "message": {"chat": {"id": 222}}, "from": {}}))
    told = [p for m, p in contact.sent if m == "sendMessage" and str(p.get("chat_id")) == "111"]
    assert len(told) == 1 and "calling you now" in told[0]["text"]
    asyncio.run(contact.on_callback({"id": "q", "data": f"tc:{token}", "message": {"chat": {"id": 222}}, "from": {}}))
    assert len([p for m, p in contact.sent if m == "sendMessage" and str(p.get("chat_id")) == "111"]) == 1


def test_trust_link_unknown_token():
    bot = FakeBot()
    asyncio.run(bot.on_message({"chat": {"id": 333}, "message_id": 1, "text": "/start t_nope", "from": {}}))
    assert "expired" in texts(bot)[-1]


def test_call_mode_changes_advice_not_verdict(rec):
    bot = FakeBot()
    asyncio.run(bot.on_message({"chat": {"id": 444}, "message_id": 1, "text": "📞 I'm on a call right now", "from": {}}))
    assert "On a call right now?" in texts(bot)[-1]
    assert bot.call_mode.get(444)
    on_call, normal = bot.caption(rec, "en", on_call=True), bot.caption(rec, "en")
    assert on_call.startswith("📵 <b>Hang up now.</b>") and "never ask you to pay" in on_call
    assert rec.verdict.title_en in on_call and rec.verdict.title_en in normal and "Hang up" not in normal


def test_start_shows_call_button():
    bot = FakeBot()
    asyncio.run(bot.on_message({"chat": {"id": 555}, "message_id": 1, "text": "/start", "from": {}}))
    kb = json.loads(bot.sent[-1][1]["reply_markup"])
    assert kb["keyboard"][0][0]["text"] == "📞 I'm on a call right now"


def test_card_has_four_actions_and_more(rec):
    bot = FakeBot()
    kb = bot.keyboard(rec, "en")["inline_keyboard"]
    labels = [b["text"] for row in kb for b in row if "url" not in b]          # 'See the proof' appears only on an HTTPS server
    assert labels == ["👪 Family", "💸 I already paid", "🚩 Report to the bank", "⋯ More"]
    q = lambda data: {"id": "q", "data": data, "message": {"chat": {"id": 111}}, "from": {}}
    for action in ("more", "family", "appeal"):
        asyncio.run(bot.on_callback(q(f"{action}:{rec.id}")))
    sent = [p for m, p in bot.sent if m == "sendMessage"]
    assert "appeal:" in sent[0]["reply_markup"] and "trust:" in sent[1]["reply_markup"] and "fam:" in sent[1]["reply_markup"]
    assert "analyst will review" in sent[2]["text"]
    assert any(a["check_id"] == rec.id for a in db.list_appeals())
