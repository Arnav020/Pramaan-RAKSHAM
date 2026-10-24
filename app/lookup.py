"""Pramaan Lookup: check a UPI ID, phone number or link BEFORE you pay.

The same answer serves three places: the /lookup page, the bot (when someone sends only an ID), and the
partner API a bank app calls at the payment screen (/api/v1/lookup).

What it combines:
- India's official rules for that kind of identifier (SEBI @valid, 1600 numbers, official bank domains,
  look-alikes, RBI Alert List), from the same Lane A checks as a full verdict;
- campaigns CONFIRMED by bank analysts that contain it (only confirmed ones are ever named);
- how many checks in the last 30 days contained it (an aggregate count; nothing personal);
- for links, the safe homepage check.
Wording never accuses: "matches a confirmed scam campaign" or "no warning found, which is not proof of safety".
Lookups themselves are not stored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.explain.templates import reason_for
from app.extract.contacts import extract_contact_points, registrable_domain
from app.lanes.lane_a import run_lane_a
from app.models import ContactType, Extraction, Strength
from app.store import campaigns
from app.store.campaigns import ist_date

KINDS = {ContactType.UPI: "upi", ContactType.PHONE: "phone", ContactType.URL: "link"}

HEADLINES = {
    "warning": ("Matches a scam campaign confirmed by bank analysts", "बैंक विश्लेषकों द्वारा पुष्ट धोखाधड़ी अभियान से मेल"),
    "pattern": ("Matches a known scam pattern", "जाने-माने धोखाधड़ी पैटर्न से मेल"),
    "official": ("Matches an official record", "आधिकारिक रिकॉर्ड से मेल"),
    "no_warning": ("No warning found, which is not proof of safety", "कोई चेतावनी नहीं मिली, पर यह सुरक्षित होने का सबूत नहीं है"),
    "invalid": ("Not a UPI ID, phone number or link", "यह UPI आईडी, फ़ोन नंबर या लिंक नहीं है"),
}
ADVICE = {
    "warning": ("Do not pay. If you already paid, call 1930 now and tell your bank.", "भुगतान न करें। अगर भेज चुके हैं तो तुरंत 1930 पर कॉल करें और बैंक को बताएँ।"),
    "pattern": ("Do not pay until you have checked through the organisation's official number.", "संस्था के आधिकारिक नंबर से जाँचे बिना भुगतान न करें।"),
    "official": ("It belongs to a registered entity. Still make sure it is the one you meant to pay.", "यह एक रजिस्टर्ड संस्था का है। फिर भी पक्का करें कि आप इसी को भुगतान करना चाहते थे।"),
    "no_warning": ("Only pay people you know. A new ID asking for money after a call or message is how most scams start.",
                   "सिर्फ़ जान-पहचान वालों को भुगतान करें। कॉल या मैसेज के बाद पैसे माँगती नई आईडी से ही ज़्यादातर धोखाधड़ी शुरू होती है।"),
    "invalid": ("Paste a UPI ID (name@bank), a 10-digit phone number or a web link.", "UPI आईडी (name@bank), 10 अंकों का फ़ोन नंबर या वेब लिंक डालें।"),
}


def _indicator(cp) -> str:
    if cp.type == ContactType.UPI:
        return f"upi:{cp.value}"
    if cp.type == ContactType.PHONE:
        return f"phone:{cp.value}"
    return f"domain:{registrable_domain(cp.value)[0]}"


def seen_count(indicator: str, days: int = 30) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    campaigns.init()
    with campaigns.conn() as c:
        return c.execute("SELECT COUNT(DISTINCT check_id) FROM check_indicators WHERE indicator = ? AND created_at >= ?", (indicator, since)).fetchone()[0]


def lookup(q: str, with_site: bool = True) -> dict:
    q = (q or "").strip()[:300]
    cps = [c for c in extract_contact_points(q) if c.type in KINDS]
    if not cps:
        h, a = HEADLINES["invalid"], ADVICE["invalid"]
        return {"query": q, "kind": None, "value": None, "result": "invalid", "headline_en": h[0], "headline_hi": h[1],
                "advice_en": a[0], "advice_hi": a[1], "facts": [], "campaigns": [], "seen_30d": 0, "site": None}
    cp = cps[0]
    ind = _indicator(cp)
    lane = run_lane_a(Extraction(text=q, contact_points=[cp]))
    sigs = [s for s in lane.signals if s.code != "CAMPAIGN_MEMORY_HIT"]
    site = None
    if cp.type == ContactType.URL and with_site:
        from app.lanes import site_check
        if site_check.enabled():
            f = site_check.inspect(cp.value)
            if site_check.needs_render(f):
                from app.render.card import renderer
                site_check.absorb_rendered(f, renderer.rendered_html_sync(f.final_url or f.url))
            sigs += site_check.signals_for(f, Extraction(text=q, contact_points=[cp])) + site_check.intent_signals(f)
            site = site_check.public_facts(f)
    hits = campaigns.memory_hits([ind])
    facts = []
    for s in sigs:
        try:
            r = reason_for(s)
        except Exception:
            continue
        facts.append({"code": s.code, "strength": s.strength.value, "text_en": r.text_en, "text_hi": r.text_hi})
    if hits:
        result = "warning"
    elif any(s.strength == Strength.STRONG for s in sigs):
        result = "pattern"
    elif any(s.strength == Strength.POSITIVE for s in sigs) and not any(s.strength == Strength.WEAK for s in sigs):
        result = "official"
    else:
        result = "no_warning"
    h, a = HEADLINES[result], ADVICE[result]
    return {"query": q, "kind": KINDS[cp.type], "value": cp.value, "indicator": ind, "result": result,
            "headline_en": h[0], "headline_hi": h[1], "advice_en": a[0], "advice_hi": a[1], "facts": facts,
            "campaigns": [{"id": x["id"], "title": x["title"], "confirmed_on": ist_date(x.get("decided_at"))} for x in hits],
            "seen_30d": seen_count(ind), "site": site}
