"""The LLM may only add facts; it can never invent a destination or a verdict."""

from app.extract.extractor import merge_llm
from app.extract.rules_extractor import extract_with_rules
from app.models import AskType, ClaimType, ContactType


def test_llm_cannot_invent_contact_points():
    base = extract_with_rules("Contact our adviser for great returns")
    data = {"contact_points": [{"type": "upi", "value": "attacker@ybl"}], "asks": [{"type": "pay", "quote": "contact our adviser"}],
            "claims": [{"type": "sebi_intermediary", "name": "adviser", "quote": "our adviser"}]}
    ex = merge_llm(base, data, "test")
    assert not any(cp.type == ContactType.UPI for cp in ex.contact_points)
    assert AskType.PAY in ex.asks
    assert any(c.type == ClaimType.SEBI_INTERMEDIARY for c in ex.claims)


def test_llm_cannot_remove_rule_findings():
    base = extract_with_rules("Share your OTP now and pay to win.big@ybl")
    ex = merge_llm(base, {"asks": [], "contact_points": [], "claims": []}, "test")
    assert AskType.CREDENTIALS in ex.asks
    assert any(cp.value == "win.big@ybl" for cp in ex.contact_points)


def test_junk_values_are_dropped():
    base = extract_with_rules("hello")
    ex = merge_llm(base, {"asks": [{"type": "launch_missiles", "quote": "hello"}, {"type": "PAY", "quote": "hello"}],
                          "pressure": [{"type": "urgency", "quote": "hello"}, 5], "claims": ["x", {"type": "god", "quote": "hello"}], "verdict": "SAFE"}, "test")
    assert ex.asks == [AskType.PAY]
    assert [p.value for p in ex.pressure] == ["urgency"]
    assert ex.claims == []


def test_llm_items_need_a_quote_outside_warnings():
    base = extract_with_rules("Anyone who promises you guaranteed returns is not telling you the truth. Invest regularly.")
    data = {"pressure": [{"type": "guaranteed_returns", "quote": "promises you guaranteed returns"}],
            "asks": [{"type": "pay", "quote": "this was never said"}]}
    ex = merge_llm(base, data, "test")
    assert ex.pressure == [] and ex.asks == []
