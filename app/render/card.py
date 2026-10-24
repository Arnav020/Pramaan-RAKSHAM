"""Render the verdict certificate (PNG) from an HTML template with headless Chromium.

One browser is kept warm for the server's lifetime, so a card renders in ~0.3 s.
"""

from __future__ import annotations

import asyncio
import base64
import html
import io
import logging
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import qrcode
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import Browser, async_playwright

from app.config import STATIC_DIR, STORE_DIR
from app.models import Lane, VerdictLevel
from app.render.guilloche import card_background_svg

log_site = logging.getLogger("pramaan.site")
TEMPLATES = Path(__file__).with_name("templates")
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))

LANE_LABEL = {Lane.A: "A · WHO", Lane.B: "B · CHANGED", Lane.C: "C · WHAT"}
STAMP = {
    VerdictLevel.RED: ("#A8322D", "#F5DEDA", "Matches a known scam pattern", "जाने-माने धोखाधड़ी पैटर्न से मेल"),
    VerdictLevel.AMBER: ("#A87310", "#F5EAD0", "Can't verify", "पुष्टि नहीं हो सकी"),
    VerdictLevel.GREEN: ("#2E7D52", "#E0EFE5", "Verified sender", "सत्यापित भेजने वाला"),
}
ACTION = {
    VerdictLevel.RED: ("Do not pay or share any code. Already paid? Call 1930 now.",
                       "भुगतान न करें, कोई कोड न बताएँ। अगर पैसे भेज चुके हैं तो तुरंत 1930 पर कॉल करें।"),
    VerdictLevel.AMBER: ("Don't act on it yet. Check through an official channel first.",
                         "अभी कुछ न करें। पहले किसी आधिकारिक माध्यम से जाँच लें।"),
    VerdictLevel.GREEN: ("Sent from a verified official channel. Still, never share your OTP or PIN.",
                         "यह आधिकारिक माध्यम से भेजा गया है। फिर भी अपना OTP या PIN कभी न बताएँ।"),
}
_GRAIN = quote(
    "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' seed='7'/>"
    "<feColorMatrix values='0 0 0 0 0.953  0 0 0 0 0.937  0 0 0 0 0.902  3.4 0 0 0 -2.15'/></filter><rect width='100%' height='100%' filter='url(#n)'/></svg>",
    safe="",
)

_IST = timezone(timedelta(hours=5, minutes=30))


def qr_data_uri(url: str) -> str:
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    q.add_data(url)
    q.make(fit=True)
    img = q.make_image(fill_color="#1A1D1B", back_color="#FBF9F4")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _img_uri(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def words_snippet(diff: dict | None, max_del: int = 10, max_ins: int = 18) -> str | None:
    """A short 'what was changed' line: original words struck out, inserted words highlighted.
    Picks the most telling change: one that inserts money, a payment ID or a pitch, else the longest."""
    if not diff:
        return None
    from app.lanes.lane_b import _risky_insertion
    cands = [op for op in diff.get("ops", []) if op["op"] in ("replace", "insert") and len(op["query"].split()) >= 3]
    if not cands:
        return None
    op = max(cands, key=lambda o: (_risky_insertion([o["query"]]), len(o["query"].split())))
    words = op["query"].split()
    d = " ".join(op["ref"].split()[:max_del])
    i = " ".join(words[:max_ins])
    out = ""
    if d:
        out += f"<del>{html.escape(d)}{'…' if len(op['ref'].split()) > max_del else ''}</del> "
    out += f"<ins>{html.escape(i)}{'…' if len(words) > max_ins else ''}</ins>"
    return out


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def _doc_panel(rec) -> dict | None:
    """Card panel for notices and posters: the page itself, the real circular it borrows from,
    and who the QR code really pays."""
    d = rec.proof.get("doc")
    if not d:
        return None
    codes = {r.code: r for r in rec.verdict.reasons}
    facts = []
    for ref in d.get("refs", []):
        if ref["code"] == "DOC_SOURCE_MISMATCH":
            facts.append({"kind": "real", "label": f"GENUINE {ref['ref']} · {ref.get('real_date') or ''}",
                          "html": f"is about <b>{html.escape(_clip(ref['real_title'], 110))}</b>, not a deposit or payment"})
        elif ref["code"] == "DOC_REFERENCE_IMPOSSIBLE":
            facts.append({"kind": "bad", "label": f"{ref['ref']} CANNOT EXIST", "html": html.escape(ref.get("why_en", ""))})
    for q in d.get("qr", []):
        if q.get("upi"):
            who = html.escape(q.get("payee_name") or "unknown name")
            amt = f" · ₹{html.escape(q['amount'])}" if q.get("amount") else ""
            facts.append({"kind": "bad", "label": "THE QR CODE REALLY PAYS", "html": f"<b>{who}</b><br><span class='mono'>{html.escape(q['upi'])}</span>{amt}"})
    if d.get("active_content"):
        facts.append({"kind": "bad", "label": "HIDDEN ACTIVE CONTENT", "html": html.escape(", ".join(d["active_content"])) + " inside the PDF"})
    if not facts and not any(c in codes for c in ("DOC_SOURCE_MATCH",)):
        return None
    page_img = None
    if d.get("page"):
        page_img = _img_uri(STORE_DIR / rec.id / d["page"])
    return {"page_img": page_img, "facts": facts[:3]}


def card_context(rec, lang: str = "en") -> dict:
    v = rec.verdict
    color, tint, t_en, t_hi = STAMP[v.level]
    if v.amber_kind == "no_risk_signs":
        from app.explain.templates import NO_RISK_TITLE
        color, tint, t_en, t_hi = "#5B6B66", "#E6E9E4", NO_RISK_TITLE["en"].split(".")[0], NO_RISK_TITLE["hi"].split("।")[0]
    elif v.amber_kind == "high_risk":
        from app.explain.templates import HIGH_RISK_TITLE
        t_en, t_hi = HIGH_RISK_TITLE["en"].split(".")[0], HIGH_RISK_TITLE["hi"].split("।")[0]
    a_en, a_hi = (v.advice_en, v.advice_hi) if v.advice_en else ACTION[v.level]   # older records have no advice
    reasons = []
    for r in v.reasons:
        primary, secondary = (r.text_hi, r.text_en) if lang == "hi" else (r.text_en, r.text_hi)
        reasons.append({"lane_label": LANE_LABEL[r.lane], "strength": r.strength.value, "primary": primary, "secondary": secondary})
    source = None
    th = rec.proof.get("thumbs")
    if th and any(r.code == "SOURCE_MISMATCH" for r in v.reasons):
        d = STORE_DIR / rec.id
        source = {"ref_img": _img_uri(d / th["ref"]), "query_img": _img_uri(d / th["query"]), "words": words_snippet(rec.proof.get("diff"))}
    doc = _doc_panel(rec)
    issued = v.created_at.astimezone(_IST).strftime("%d %b %Y · %H:%M")
    height = 1350
    return {
        "lang": lang, "serial": rec.serial, "issued_ist": issued, "title_en": t_en, "title_hi": t_hi,
        "stamp_color": color, "stamp_tint": tint, "stamp_size": 54 if v.level == VerdictLevel.RED else 66,
        "action_en": a_en, "action_hi": a_hi, "reasons": reasons, "source": source, "height": height,
        "qr": qr_data_uri(rec.verify_url), "sig_short": rec.signature[:16], "key_id": rec.payload["kid"],
        "rules_version": v.rules_version, "grain": _GRAIN, "doc": doc,
        "background": card_background_svg(1080, height, accent=color),
    }


class CardRenderer:
    """Keeps one headless Chromium alive; renders cards on demand."""

    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._browser is None:
            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch()   # raises if Chromium is missing; the next render retries

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def visit_site(self, url: str, timeout_ms: int = 9000) -> tuple[bytes | None, str | None]:
        """One look at a website's homepage, in a throwaway, locked-down browser context: every request
        (images and scripts included) must go to a public address over http(s), no downloads, no pop-ups,
        no service workers, nothing typed or clicked. Returns (phone-sized screenshot, the page's HTML after
        its scripts ran), either None if anything goes wrong. Many scam sites are JavaScript apps whose
        downloaded HTML is an empty shell; the rendered HTML is what a visitor actually reads."""
        from urllib.parse import urlsplit
        from app.lanes.site_check import MAX_BYTES, UA, is_public_ip, public_ips
        try:
            await self.start()
        except Exception:
            return None, None
        checked: dict[str, bool] = {}

        async def guard(route):
            host = urlsplit(route.request.url).hostname or ""
            ok = route.request.url.startswith(("http://", "https://")) and route.request.resource_type not in ("media", "websocket", "eventsource")
            if ok and host not in checked:
                try:
                    checked[host] = bool(await asyncio.to_thread(public_ips, host))
                except PermissionError:
                    checked[host] = False
            if ok and checked.get(host):
                await route.continue_()
            else:
                await route.abort()

        ctx = None
        try:
            ctx = await self._browser.new_context(viewport={"width": 412, "height": 860}, device_scale_factor=1, user_agent=UA,
                                                  accept_downloads=False, service_workers="block", java_script_enabled=True)
            await ctx.route("**/*", guard)
            page = await ctx.new_page()
            ctx.on("page", lambda p: asyncio.ensure_future(p.close()))   # any pop-up the site opens is closed at once
            # The browser does its own DNS lookup after our check: every response's real server address is checked
            # too, and one private address (DNS rebinding) throws away the whole visit.
            bad_peer: list[str] = []
            peer_checks: list[asyncio.Future] = []

            async def check_peer(resp) -> None:
                try:
                    addr = await resp.server_addr()
                except Exception:
                    return
                if addr and not is_public_ip(addr.get("ipAddress")):
                    bad_peer.append(addr.get("ipAddress"))
            page.on("response", lambda resp: peer_checks.append(asyncio.ensure_future(check_peer(resp))))
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1200)   # let the first screen settle
            await asyncio.gather(*peer_checks, return_exceptions=True)
            if bad_peer:
                log_site.warning("site visit discarded for %s: the browser reached a non-public address", url)
                return None, None
            html = None
            try:
                html = (await page.content())[:MAX_BYTES]
            except Exception as e:
                log_site.info("rendered page unreadable for %s: %s", url, type(e).__name__)
            try:
                shot = await page.screenshot(type="jpeg", quality=72, full_page=False, timeout=4000)
            except Exception as e:
                log_site.info("site screenshot skipped for %s: %s", url, type(e).__name__)
                shot = None
            return shot, html
        except Exception as e:
            log_site.info("site visit skipped for %s: %s", url, type(e).__name__)
            return None, None
        finally:
            if ctx is not None:
                await ctx.close()

    def rendered_html_sync(self, url: str, timeout_s: float = 14) -> str | None:
        """visit_site's HTML for code running in a worker thread (Lookup), using the server's own loop and browser.
        None when there is no running server loop (tests, CLI) or when called on the loop's own thread."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return None
        try:
            if asyncio.get_running_loop() is loop:
                return None
        except RuntimeError:
            pass
        fut = asyncio.run_coroutine_threadsafe(self.visit_site(url), loop)
        try:
            return fut.result(timeout_s)[1]
        except Exception:
            fut.cancel()
            return None

    async def render(self, rec, lang: str = "en") -> bytes:
        await self.start()
        ctx = card_context(rec, lang)
        page_html = _env.get_template("card.html").render(**ctx)
        tmp = STATIC_DIR / f"_card_{rec.id}.html"   # served from the static dir so fonts resolve
        tmp.write_text(page_html, encoding="utf-8")
        try:
            async with self._lock:
                page = await self._browser.new_page(viewport={"width": 1080, "height": ctx["height"]}, device_scale_factor=1)
                try:
                    await page.goto(tmp.as_uri(), wait_until="load")
                    await page.evaluate("document.fonts.ready")
                    h = int(await page.evaluate("document.querySelector('.card').scrollHeight"))
                    if abs(h - ctx["height"]) > 4:   # re-draw the background for the exact height
                        ctx["height"] = h
                        ctx["background"] = card_background_svg(1080, h, accent=ctx["stamp_color"])
                        tmp.write_text(_env.get_template("card.html").render(**ctx), encoding="utf-8")
                        await page.set_viewport_size({"width": 1080, "height": h})
                        await page.goto(tmp.as_uri(), wait_until="load")
                        await page.evaluate("document.fonts.ready")
                    return await page.screenshot(type="png", full_page=True)
                finally:
                    await page.close()
        finally:
            tmp.unlink(missing_ok=True)


renderer = CardRenderer()
