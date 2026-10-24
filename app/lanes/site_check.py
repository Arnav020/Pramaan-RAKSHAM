"""A careful look at a website named in a message: does it exist, how old is it, and what is on its homepage.

Safety rules (a scam link is hostile input, and our server must not be turned against itself):
- Homepage only. The link's path and query are never opened: scam links often carry a code unique
  to the victim ("/kyc?u=meena"), and opening it would tell the scammer that this person clicked.
- No short links (bit.ly etc.): the short code itself is the personal link.
- Public internet only. Every address, including every redirect, must resolve to public IPs;
  private, loopback, link-local (the cloud metadata service at 169.254.169.254) and reserved
  ranges are refused.
- GET only, at most 4 redirects, 8 s, 1.5 MB, HTML only. No files are downloaded, no forms submitted.
- One look per domain per 6 hours (cached), with an honest User-Agent.

Findings become Lane A signals (who is really behind the link). An old domain is never a reason
to trust a message: it only means this signal has nothing to add.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import DATA_DIR
from app.extract.contacts import extract_contact_points, free_host_platform, registrable_domain
from app.models import ClaimType, ContactType, Extraction, Lane, Provenance, Signal, Strength

log = logging.getLogger("pramaan.site")
CACHE = DATA_DIR / "cache" / "site"
UA = "PramaanLinkCheck/1.0 (homepage safety check for a hackathon prototype; one request per domain)"
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4
CACHE_TTL = 6 * 3600
NEW_DOMAIN_DAYS = 90
THIN_WORDS = 60          # fewer words in the downloaded HTML: a JavaScript app, read its rendered page instead
CACHE_VERSION = 2
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "rb.gy", "cutt.ly", "is.gd", "shorturl.at", "tiny.cc", "ow.ly", "rebrand.ly", "s.id"}
MESSAGING = {"wa.me", "whatsapp.com", "t.me", "telegram.me", "telegram.org"}
SECRET_FIELDS = {"password": "password", "passwd": "password", "pin": "PIN", "mpin": "PIN", "otp": "OTP", "cvv": "CVV", "card": "card number",
                 "aadhaar": "Aadhaar", "aadhar": "Aadhaar", "pan": "PAN"}


def enabled() -> bool:
    return os.getenv("PRAMAAN_SITE_CHECK", "on").lower() not in ("0", "off", "false")


# ----------------------------------------------------------------- address guard
def public_ips(host: str) -> list[str]:
    """All addresses for host, or [] if any of them is not on the public internet (or it doesn't resolve)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return []
    ips = sorted({i[4][0] for i in infos})
    for ip in ips:
        a = ipaddress.ip_address(ip.split("%")[0])
        if not a.is_global or a.is_multicast:
            raise PermissionError(f"{host} resolves to a non-public address")
    return ips


def is_public_ip(ip: str | None) -> bool:
    try:
        a = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    return a.is_global and not a.is_multicast


def _connected_ip(r: httpx.Response) -> str | None:
    """The address the connection really went to. DNS can answer differently on the second lookup (DNS rebinding),
    so the check before connecting is repeated on the live socket."""
    stream = r.extensions.get("network_stream")
    try:
        addr = stream.get_extra_info("server_addr") if stream is not None else None
    except Exception:
        addr = None
    return addr[0] if addr else None


def _allowed_url(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme in ("http", "https") and p.port in (None, 80, 443) and bool(p.hostname) and not p.username


# ------------------------------------------------------------------------ RDAP
def _rdap_servers() -> dict[str, str]:
    path = DATA_DIR / "cache" / "rdap_bootstrap.json"
    if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        return json.loads(path.read_text(encoding="utf-8"))
    boot = httpx.get("https://data.iana.org/rdap/dns.json", timeout=10, headers={"User-Agent": UA}).json()
    servers = {tld: urls[0] for tlds, urls in boot["services"] for tld in tlds}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(servers), encoding="utf-8")
    return servers


def rdap(domain: str) -> dict:
    """Registration date and registrar from the registry's RDAP service (the public successor of WHOIS)."""
    try:
        base = _rdap_servers().get(domain.rsplit(".", 1)[-1])
        if not base:
            return {"status": "no_rdap"}
        r = httpx.get(f"{base.rstrip('/')}/domain/{domain}", timeout=8, follow_redirects=True,
                      headers={"Accept": "application/rdap+json", "User-Agent": UA})
    except (httpx.HTTPError, ValueError, OSError) as e:
        return {"status": "error", "error": type(e).__name__}
    if r.status_code == 404:
        return {"status": "not_registered"}
    if r.status_code != 200:
        return {"status": f"http_{r.status_code}"}
    j = r.json()
    events = {e.get("eventAction"): e.get("eventDate") for e in j.get("events", [])}
    registrar = None
    for ent in j.get("entities", []):
        if "registrar" in ent.get("roles", []):
            for item in (ent.get("vcardArray") or [None, []])[1]:
                if item and item[0] == "fn":
                    registrar = item[3]
    return {"status": "ok", "registered": events.get("registration"), "expires": events.get("expiration"), "registrar": registrar}


# --------------------------------------------------------------------- homepage
def fetch_homepage(host: str) -> dict:
    """GET the homepage (never the message's path), following redirects only to public addresses."""
    url, hops = f"https://{host}/", []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"}) as c:
        for attempt in range(MAX_REDIRECTS + 1):
            if not _allowed_url(url) or not public_ips(urlsplit(url).hostname):
                return {"status": "unreachable", "url": url, "hops": hops}
            try:
                with c.stream("GET", url) as r:
                    peer = _connected_ip(r)
                    if peer is not None and not is_public_ip(peer):
                        log.warning("refused %s: connected to a non-public address", urlsplit(url).hostname)
                        return {"status": "unreachable", "url": url, "hops": hops, "error": "non_public_peer"}
                    if r.is_redirect and "location" in r.headers:
                        hops.append(url)
                        url = urljoin(url, r.headers["location"])
                        continue
                    ctype = r.headers.get("content-type", "")
                    body = b""
                    if "html" in ctype or not ctype:
                        for chunk in r.iter_bytes():
                            body += chunk
                            if len(body) > MAX_BYTES:
                                break
                    return {"status": "ok", "code": r.status_code, "url": str(r.url), "hops": hops, "content_type": ctype,
                            "html": body.decode(r.encoding or "utf-8", errors="replace") if body else ""}
            except httpx.ConnectError:
                if url.startswith("https://") and not hops:      # some small sites still have no HTTPS
                    url = f"http://{host}/"
                    continue
                return {"status": "unreachable", "url": url, "hops": hops}
            except httpx.HTTPError as e:
                return {"status": "unreachable", "url": url, "hops": hops, "error": type(e).__name__}
    return {"status": "too_many_redirects", "url": url, "hops": hops}


def read_page(html: str, base_url: str) -> dict:
    """What a person would notice on the page: what it calls itself, what it asks you to type, what it offers."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:160]
    desc = ""
    for m in soup.find_all("meta"):
        if (m.get("name") or m.get("property") or "").lower() in ("description", "og:description") and m.get("content"):
            desc = m["content"].strip()[:240]
            break
    asks = set()
    for inp in soup.find_all(["input", "select", "textarea"]):
        blob = " ".join(str(inp.get(a, "")) for a in ("type", "name", "id", "placeholder", "autocomplete", "aria-label")).lower()
        if inp.get("type", "").lower() == "password":
            asks.add("password")
        for key, label in SECRET_FIELDS.items():
            if re.search(rf"\b{key}\b|{key}(?:number|no|code)", blob):
                asks.add(label)
    apks = sorted({urljoin(base_url, a["href"]) for a in soup.find_all("a", href=True) if a["href"].lower().split("?")[0].endswith(".apk")})[:3]
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:20000]
    return {"title": title, "description": desc, "asks_for": sorted(asks), "apk_links": apks, "text": text,
            "forms": len(soup.find_all("form")), "words": len(text.split())}


# ------------------------------------------------------------------------ main
@dataclass
class SiteFacts:
    url: str
    host: str
    domain: str
    checked_at: str
    skipped: str | None = None
    exists: bool | None = None
    ips: list[str] = field(default_factory=list)
    registered: str | None = None
    age_days: int | None = None
    registrar: str | None = None
    rdap_status: str | None = None
    status: str | None = None
    http_code: int | None = None
    final_url: str | None = None
    redirected_to: str | None = None
    title: str = ""
    description: str = ""
    asks_for: list[str] = field(default_factory=list)
    apk_links: list[str] = field(default_factory=list)
    impersonates: str | None = None
    promises_returns: bool = False
    upi_on_page: list[str] = field(default_factory=list)
    free_host: str | None = None       # 'vercel.app' when anyone could have made this site in minutes
    words: int | None = None
    rendered: bool = False             # page read after its scripts ran (headless Chromium)
    text: str = ""                     # the page's visible text, for Lane C; never stored with a verdict
    screenshot: str | None = None      # file name, filled in by the service


PRIVATE_FIELDS = ("screenshot", "text")


def public_facts(f: "SiteFacts") -> dict:
    """What a verdict, a proof page or Lookup keeps about the site (not the page's full text)."""
    return {k: v for k, v in vars(f).items() if k not in PRIVATE_FIELDS}


def inspect(url: str) -> SiteFacts:
    regdom, host = registrable_domain(url)
    now = datetime.now(timezone.utc)
    f = SiteFacts(url=f"https://{host}/", host=host, domain=regdom, checked_at=now.isoformat(timespec="seconds"))
    from app.rails.registry import get_registry
    if regdom in SHORTENERS:
        f.skipped = "short_link"
        return f
    if regdom in MESSAGING or get_registry().entity_for_domain(regdom) is not None:
        f.skipped = "official_or_messaging"
        return f
    from app.config import get_settings
    if host == (urlsplit(get_settings().public_base_url).hostname or ""):
        f.skipped = "pramaan"      # our own seal and proof links: our pages quote scams as examples
        return f
    key = _cache_key(host)
    if key.exists() and time.time() - key.stat().st_mtime < CACHE_TTL:
        return SiteFacts(**json.loads(key.read_text(encoding="utf-8")))
    try:
        f.ips = public_ips(host)
    except PermissionError:
        f.skipped, f.exists = "private_address", True
        return f
    f.exists = bool(f.ips)
    f.free_host = free_host_platform(host)
    # A free subdomain has no registration of its own: RDAP would only date the platform (vercel.app: 2020).
    info = {"status": "free_hosting"} if f.free_host else rdap(regdom)
    f.rdap_status, f.registrar = info["status"], info.get("registrar")
    if info.get("registered"):
        f.registered = info["registered"][:10]
        reg = datetime.fromisoformat(info["registered"].replace("Z", "+00:00"))
        f.age_days = max(0, (now - reg).days)
    if f.exists:
        page = fetch_homepage(host)
        f.status, f.http_code, f.final_url = page["status"], page.get("code"), page.get("url")
        if f.final_url and registrable_domain(f.final_url)[0] != regdom:
            f.redirected_to = registrable_domain(f.final_url)[1]
        if page.get("html"):
            _absorb(f, read_page(page["html"], f.final_url or f.url))
    _save(f)
    return f


def _cache_key(host: str):
    return CACHE / f"{hashlib.sha256(f'v{CACHE_VERSION}:{host}'.encode()).hexdigest()[:20]}.json"


def _save(f: "SiteFacts") -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_key(f.host).write_text(json.dumps(asdict(f), ensure_ascii=False), encoding="utf-8")


def _absorb(f: "SiteFacts", p: dict) -> None:
    """Copy what the page shows into the facts."""
    from app.extract.intent_rules import detect_pressure
    from app.models import Pressure
    f.title = p["title"] or f.title
    f.description = p["description"] or f.description
    f.asks_for, f.apk_links, f.words, f.text = p["asks_for"], p["apk_links"], p["words"], p["text"][:8000]
    f.impersonates = _impersonated_entity(f"{f.title}. {f.description}", f.domain)   # how it presents itself, not what it mentions
    f.promises_returns = Pressure.GUARANTEED_RETURNS in detect_pressure(f.text)
    f.upi_on_page = [c.value for c in extract_contact_points(f.text) if c.type == ContactType.UPI][:5]


def needs_render(f: "SiteFacts") -> bool:
    """The downloaded HTML was nearly empty: the content is drawn by JavaScript."""
    return bool(f.exists and f.status == "ok" and not f.rendered and (f.words or 0) < THIN_WORDS)


def absorb_rendered(f: "SiteFacts", html: str | None) -> bool:
    """Read the page as it looked after its scripts ran (from the locked-down browser), if that says more."""
    if not html:
        return False
    p = read_page(html, f.final_url or f.url)
    if p["words"] <= (f.words or 0):
        return False
    _absorb(f, p)
    f.rendered = True
    _save(f)
    return True


def intent_signals(f: "SiteFacts") -> list[Signal]:
    """Lane C for the website itself: what the page asks visitors to do, with the same playbooks and pressure
    rules as a message (rules only; the page is never sent to an AI service)."""
    if f.skipped or not f.text.strip():
        return []
    from app.lanes.lane_c import page_intent
    prov = Provenance(dataset="Homepage visit" + (" (rendered)" if f.rendered else ""), source_url=f.url, fetched_at=f.checked_at[:10])
    out = []
    for s in page_intent(f.text):
        out.append(Signal(code=f"SITE_{s.code}", lane=Lane.C, strength=s.strength, params={"domain": f.host, **s.params},
                          provenance=s.provenance or prov))
    return out


_SERVICE_CUE = re.compile(r"\b(?:log\s*-?in|sign\s*in|net\s*banking|internet\s+banking|online\s+banking|kyc|customer\s+(?:care|support)|rewards?|"
                          r"secure\s+(?:login|portal)|portal|helpdesk|refund)\b|लॉगिन|नेट\s*बैंकिंग", re.IGNORECASE)


def _impersonated_entity(text: str, regdom: str) -> str | None:
    """A bank or financial regulator the page's title calls itself, when the site is not theirs
    (a news site that merely mentions RBI is not impersonating it)."""
    from app.extract.claims import detect_claims
    if not _SERVICE_CUE.search(text):     # naming RBI in a headline is not claiming to be RBI
        return None
    for cl in detect_claims(text):
        if cl.type == ClaimType.BANK or (cl.type == ClaimType.REGULATOR and cl.name and "police" not in cl.name.lower()):
            return cl.name
    return None


def signals_for(f: SiteFacts, ex: Extraction) -> list[Signal]:
    """Turn what we saw into Lane A evidence. Strength depends on what the message itself asks for."""
    if f.skipped:
        return []
    prov = Provenance(dataset="Homepage visit + registry RDAP", source_url=f.url, fetched_at=f.checked_at[:10],
                      note=f"registrar: {f.registrar}" if f.registrar else None)
    asks_money = any(a.value in ("pay", "credentials", "install_app") for a in ex.asks)
    claims_authority = any(c.type in (ClaimType.BANK, ClaimType.REGULATOR, ClaimType.NBFC, ClaimType.SEBI_INTERMEDIARY) for c in ex.claims)
    sig = lambda code, strength, **p: Signal(code=code, lane=Lane.A, strength=strength, params={"domain": f.host, **p}, provenance=prov)
    out = []
    if f.exists is False:
        gone = f.rdap_status == "not_registered"
        out.append(sig("SITE_NOT_FOUND", Strength.WEAK, registered="no" if gone else "unknown"))
        return out
    if f.age_days is not None and f.age_days <= NEW_DOMAIN_DAYS:
        out.append(sig("SITE_NEW_DOMAIN", Strength.STRONG if (asks_money or claims_authority) else Strength.WEAK,
                       days=f.age_days, registered=f.registered))
    if f.impersonates:
        out.append(sig("SITE_IMPERSONATES", Strength.STRONG, entity=f.impersonates))
    if f.asks_for:
        out.append(sig("SITE_ASKS_SECRETS", Strength.STRONG if (claims_authority or f.impersonates) else Strength.WEAK,
                       fields=", ".join(f.asks_for)))
    if f.apk_links:
        out.append(sig("SITE_OFFERS_APK", Strength.STRONG))
    if f.redirected_to:
        out.append(sig("SITE_REDIRECTS", Strength.WEAK, target=f.redirected_to))
    if f.promises_returns:
        out.append(sig("SITE_PROMISES_RETURNS", Strength.WEAK))
    if f.free_host:
        # An anonymous free site is only a hint by itself; selling returns, asking for secrets, or carrying a message
        # that asks for money or claims an institution, it is not how any licensed firm operates.
        why = [(en, hi) for cond, en, hi in (
            (f.promises_returns, "promises guaranteed returns", "गारंटीड रिटर्न का वादा करती है"),
            (bool(f.asks_for), f"asks for your {', '.join(f.asks_for)}", f"आपका {', '.join(f.asks_for)} माँगती है"),
            (asks_money, "is used in a message asking for money", "पैसे माँगने वाले मैसेज में इस्तेमाल हुई है"),
            (claims_authority, "is used in a message claiming to be an official institution", "ख़ुद को आधिकारिक संस्था बताने वाले मैसेज में इस्तेमाल हुई है"),
        ) if cond]
        if why:
            out.append(sig("SITE_FREE_HOSTING", Strength.STRONG, platform=f.free_host,
                           what=" and ".join(w[0] for w in why[:2]), what_hi=" और ".join(w[1] for w in why[:2])))
    return out
