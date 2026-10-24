"""The website check: the address guard, what we read from a page, and how findings become signals.
No real website is visited here."""

import pytest

from app.extract.rules_extractor import extract_with_rules
from app.lanes import site_check
from app.lanes.site_check import SiteFacts, public_ips, read_page, signals_for


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.1", "[::1]"])
def test_internal_and_cloud_metadata_addresses_are_refused(host):
    with pytest.raises(PermissionError):
        public_ips(host.strip("[]"))


def test_only_plain_web_urls_are_allowed():
    allowed = site_check._allowed_url
    assert allowed("https://example.com/") and allowed("http://example.com:80/")
    for bad in ("file:///etc/passwd", "ftp://example.com/", "https://example.com:8443/", "https://user:pw@example.com/", "javascript:alert(1)"):
        assert not allowed(bad), bad


def test_short_links_and_official_sites_are_not_opened():
    assert site_check.inspect("https://bit.ly/3xYz").skipped == "short_link"
    assert site_check.inspect("https://sbi.bank.in/kyc").skipped == "official_or_messaging"


PHISH = """<html><head><title>SBI NetBanking - Login</title><meta name="description" content="State Bank of India secure login"></head>
<body><form><input name="username"><input type="password" name="pwd"><input name="otp" placeholder="Enter OTP"></form>
<a href="/files/SBI-Rewards.apk">Download our app</a><p>Guaranteed returns of 300% for loyal customers. Pay to rewards.sbi@ybl</p></body></html>"""


def test_reading_a_phishing_page():
    p = read_page(PHISH, "https://sbi-rewards-login.in/")
    assert p["title"] == "SBI NetBanking - Login"
    assert {"password", "OTP"} <= set(p["asks_for"])
    assert p["apk_links"] == ["https://sbi-rewards-login.in/files/SBI-Rewards.apk"]
    assert site_check._impersonated_entity(f"{p['title']}. {p['description']}", "sbi-rewards-login.in")


def test_a_news_site_that_mentions_rbi_is_not_impersonating():
    assert site_check._impersonated_entity("Markets today: what the RBI policy means for you. Business news", "news.example.in") is None


def facts(**kw):
    base = dict(url="https://x.in/", host="x.in", domain="x.in", checked_at="2026-10-24T00:00:00+00:00", exists=True)
    return SiteFacts(**(base | kw))


def codes(f, text):
    return {s.code: s.strength.value for s in signals_for(f, extract_with_rules(text))}


def test_new_domain_is_strong_only_when_money_or_authority_is_involved():
    new = facts(age_days=12, registered="2026-10-12")
    assert codes(new, "Invest ₹10,000 at x.in and get your money doubled in 3 days")["SITE_NEW_DOMAIN"] == "STRONG"
    assert codes(new, "Our new bakery is open, see the menu at x.in")["SITE_NEW_DOMAIN"] == "WEAK"
    assert "SITE_NEW_DOMAIN" not in codes(facts(age_days=2000, registered="2021-01-01"), "Invest ₹10,000 at x.in")


def test_page_findings_become_signals():
    f = facts(age_days=900, impersonates="State Bank of India", asks_for=["password", "OTP"], apk_links=["https://x.in/a.apk"])
    c = codes(f, "Update your KYC at x.in")
    assert c["SITE_IMPERSONATES"] == "STRONG" and c["SITE_ASKS_SECRETS"] == "STRONG" and c["SITE_OFFERS_APK"] == "STRONG"


def test_missing_site_is_only_a_hint():
    assert codes(facts(exists=False, rdap_status="not_registered"), "Invest at x.in") == {"SITE_NOT_FOUND": "WEAK"}


def test_money_doubling_is_caught():
    from app.extract.intent_rules import detect_pressure
    from app.lanes.lane_c import run_lane_c
    from app.models import Pressure
    for text in ("we'll get your money doubled in just three days", "Your investment will double in 10 days", "2x returns guaranteed",
                 "पैसा दोगुना करें सिर्फ़ 7 दिनों में", "paisa double scheme"):
        assert Pressure.GUARANTEED_RETURNS in detect_pressure(text), text
    # the call from our test recording
    r = run_lane_c(extract_with_rules("We are having an investment scheme, invest above 10,000 rupees and we'll get your money doubled in just three days. "
                                      "Our organization is investbetter.in, you can visit our website and see the results"))
    pb = next(s for s in r.signals if s.code == "PLAYBOOK_MATCH")
    assert pb.params["playbook"] == "money_doubling" and r.status == "HIGH"


def test_voice_detector_never_reaches_the_card():
    from app.fusion.engine import pick_reasons
    from app.models import Lane, Signal, Strength, VerdictLevel
    sigs = [Signal(code="DETECTOR_INDICATOR", lane=Lane.B, strength=Strength.WEAK, params={"kind": "audio", "detector": "wav2vec2"}),
            Signal(code="DETECTOR_INDICATOR", lane=Lane.B, strength=Strength.WEAK, params={"kind": "video", "detector": "GenD"}),
            Signal(code="DOMAIN_UNVERIFIED", lane=Lane.A, strength=Strength.WEAK, params={"domain": "x.in"})]
    shown = pick_reasons(VerdictLevel.AMBER, sigs)
    assert all(s.params.get("kind") != "audio" for s in shown)
    assert any(s.params.get("kind") == "video" for s in shown)


# ---------------------------------------------------------------- free hosting and JavaScript pages
SCAM_PAGE_TEXT = ("AUTOMATED HIGH-YIELD LIQUIDITY PROTOCOL. 100% Guaranteed 72H Return. Double Your Money in Just 3 Days! "
                  "Invest today and unlock extraordinary, risk-free returns. Deposit ₹10,000 today and receive a guaranteed ₹20,000 "
                  "deposited straight to your bank account in 72 hours flat. Start Investing. Daily profit credit: 33.33% every 24h. "
                  "Instant UPI / IMPS automatic payout. Zero processing fee. Starter Wealth Booster plan: invest ₹10,000, get ₹20,000. "
                  "Executive Rapid Doubler: your money doubles in exactly 72 hours, 2x guaranteed.")
SHELL = "<html><head><title>InvestBest | Premier Wealth</title></head><body><div id='root'></div><script src='/app.js'></script></body></html>"
RENDERED = f"<html><head><title>InvestBest | Premier Wealth</title></head><body><main><p>{SCAM_PAGE_TEXT}</p></main></body></html>"


def test_a_free_subdomain_is_its_own_site():
    from app.extract.contacts import free_host_platform, registrable_domain
    assert registrable_domain("http://invest-best-one.vercel.app/plans") == ("invest-best-one.vercel.app", "invest-best-one.vercel.app")
    assert registrable_domain("https://docs.team.github.io/") == ("team.github.io", "docs.team.github.io")
    assert registrable_domain("https://vercel.app/")[0] == "vercel.app"
    assert free_host_platform("invest-best-one.vercel.app") == "vercel.app"
    assert free_host_platform("vercel.app") is None and free_host_platform("sbi.bank.in") is None and free_host_platform("example.com") is None


def test_free_hosting_is_strong_only_for_an_institution():
    from app.lanes.lane_a import run_lane_a
    plain = {s.code: s.strength.value for s in run_lane_a(extract_with_rules("My portfolio: https://arnav.github.io")).signals}
    assert plain == {"DOMAIN_FREE_HOSTING": "WEAK"}
    bank = {s.code: s.strength.value for s in run_lane_a(extract_with_rules("HDFC Bank: complete your KYC at https://hdfc-help.netlify.app")).signals}
    assert "STRONG" in bank.values()


def test_free_hosting_with_returns_or_a_money_ask_is_strong():
    site = facts(host="invest-best-one.vercel.app", domain="invest-best-one.vercel.app", free_host="vercel.app")
    assert "SITE_FREE_HOSTING" not in codes(site, "Have a look at invest-best-one.vercel.app")
    assert codes(facts(**{**vars(site), "promises_returns": True}), "Have a look")["SITE_FREE_HOSTING"] == "STRONG"
    assert codes(site, "Pay ₹5,000 today to join, details at invest-best-one.vercel.app")["SITE_FREE_HOSTING"] == "STRONG"


def test_free_host_is_not_dated_and_a_javascript_shell_is_read_rendered(monkeypatch, tmp_path):
    monkeypatch.setattr(site_check, "CACHE", tmp_path)
    monkeypatch.setattr(site_check, "public_ips", lambda host: ["76.76.21.21"])
    monkeypatch.setattr(site_check, "rdap", lambda d: pytest.fail("RDAP would only date the platform"))
    monkeypatch.setattr(site_check, "fetch_homepage", lambda host: {"status": "ok", "code": 200, "url": f"https://{host}/", "html": SHELL})
    f = site_check.inspect("http://invest-best-one.vercel.app/")
    assert f.free_host == "vercel.app" and f.rdap_status == "free_hosting" and f.age_days is None
    assert site_check.needs_render(f) and not f.promises_returns
    assert site_check.absorb_rendered(f, RENDERED)
    assert f.rendered and f.promises_returns and not site_check.needs_render(f)
    assert site_check.inspect("http://invest-best-one.vercel.app/").rendered    # the rendered reading is cached
    assert "text" not in site_check.public_facts(f) and "screenshot" not in site_check.public_facts(f)


def test_rendered_page_that_says_less_is_ignored():
    f = facts(status="ok", words=500, text="a long real page")
    assert not site_check.absorb_rendered(f, SHELL) and not f.rendered


def test_page_playbook_needs_its_words_together():
    """A long page mentions 'credit' and 'payout' somewhere; that is not an instant-loan scam."""
    from app.lanes.lane_c import page_intent
    sigs = {s.code: s for s in page_intent(SCAM_PAGE_TEXT)}
    assert sigs["PLAYBOOK_MATCH"].params["playbook"] == "money_doubling"
    assert sigs["ASK_PAY_WITH_PRESSURE"].strength.value == "STRONG"
    calm = page_intent("Demo Bank. Home loans and credit cards. Pay your bills online. Branch timings and contact details.")
    assert not any(s.strength.value == "STRONG" for s in calm)


def test_a_bare_link_to_a_free_scam_site_is_red():
    from app.fusion.engine import fuse
    from app.lanes.lane_a import run_lane_a
    from app.lanes.lane_b import run_lane_b
    from app.lanes.lane_c import run_lane_c
    from app.pipeline import _apply_sites
    ex = extract_with_rules("http://invest-best-one.vercel.app/")
    lanes = {"A": run_lane_a(ex), "B": run_lane_b([], ex.text), "C": run_lane_c(ex)}
    site = facts(host="invest-best-one.vercel.app", domain="invest-best-one.vercel.app", free_host="vercel.app",
                 status="ok", rendered=True, promises_returns=True, text=SCAM_PAGE_TEXT)
    _apply_sites([(site, None)], ex, lanes)
    v = fuse(ex, lanes)
    assert v.level.value == "RED" and {"A", "C"} <= set(v.strong_lanes)
    assert all("text" not in d for d in lanes["A"].detail["sites"])


def test_a_bare_link_to_an_ordinary_site_is_not_red():
    from app.fusion.engine import fuse
    from app.lanes.lane_a import run_lane_a
    from app.lanes.lane_b import run_lane_b
    from app.lanes.lane_c import run_lane_c
    from app.pipeline import _apply_sites
    ex = extract_with_rules("Our bakery menu: https://bakery.github.io")
    lanes = {"A": run_lane_a(ex), "B": run_lane_b([], ex.text), "C": run_lane_c(ex)}
    site = facts(host="bakery.github.io", domain="bakery.github.io", free_host="github.io", status="ok",
                 text="Fresh bread every morning. Cakes to order. Open 8 am to 8 pm. Pay at the counter.")
    _apply_sites([(site, None)], ex, lanes)
    assert fuse(ex, lanes).level.value != "RED"


def test_pramaan_never_visits_its_own_pages(monkeypatch):
    """Seal and proof links point at Pramaan itself; its pages quote scams as examples and must not be read as one."""
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: type("S", (), {"public_base_url": "https://13-207-22-33.sslip.io"})())
    assert site_check.inspect("https://13-207-22-33.sslip.io/s/Z3U7WMLU").skipped == "pramaan"


def test_genuine_advisories_are_not_read_as_scam_pages():
    import yaml
    from pathlib import Path
    from app.lanes.lane_c import page_intent
    items = yaml.safe_load((Path(__file__).parent.parent / "eval" / "public_set.yaml").read_text(encoding="utf-8"))["items"]
    genuine = [i for i in items if i.get("label") == "genuine"]
    assert genuine and all(not page_intent(i["text"]) for i in genuine)
