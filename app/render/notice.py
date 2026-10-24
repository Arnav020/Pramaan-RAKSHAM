"""Sealed documents an issuer sends: an A4 notice (PDF + PNG) or an SMS with a verify line.

The text that gets sealed is exactly the text printed on the notice (letterhead, title, reference,
body, sign-off), so a genuine PDF compares as unchanged word for word. Only the seal footer
(QR + code + link) sits outside the signed text.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import STATIC_DIR, get_settings
from app.render.card import qr_data_uri, renderer
from app.store import seal

TEMPLATES = Path(__file__).with_name("templates")
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
_IST = timezone(timedelta(hours=5, minutes=30))


def seal_url(code: str) -> str:
    return f"{get_settings().public_base_url}/s/{code}"


def notice_text(issuer: str, title: str, ref: str, date: str, body: str, signoff: str) -> str:
    """The canonical printed text of a notice: what the seal signs."""
    who = seal.issuers()[issuer]
    lines = [who["name"], who["label"], title.strip(), f"Ref {ref.strip()} · {date}", "", body.strip(), "", signoff.strip()]
    return "\n".join(lines)


def sms_text(body: str, code: str) -> str:
    return f"{body.strip()}\nVerify: {seal_url(code)}"


def issue_notice(issuer: str, title: str, body: str, ref: str = "", signoff: str = "") -> dict:
    date = datetime.now(_IST).strftime("%d %b %Y")
    ref = ref.strip() or f"DB/{datetime.now(_IST):%Y%m%d}/{abs(hash(title)) % 10000:04d}"
    signoff = signoff.strip() or f"Customer Safety Team, {seal.issuers()[issuer]['name']}"
    text = notice_text(issuer, title, ref, date, body, signoff)
    rec = seal.issue(issuer, "notice", title, text, doc_id=ref)
    rec["layout"] = {"title": title.strip(), "ref": ref, "date": date, "body": body.strip(), "signoff": signoff}
    return rec


def issue_sms(issuer: str, title: str, body: str) -> dict:
    rec = seal.issue(issuer, "sms", title, body.strip())
    rec["sms"] = sms_text(body, rec["payload"]["code"])
    return rec


def _notice_html(rec: dict, layout: dict | None = None) -> str:
    pl = rec["payload"]
    who = seal.issuers()[pl["iss"]]
    if layout is None:   # rebuild the layout from the sealed text (letterhead, label, title, ref line, body, sign-off)
        lines = rec["text"].split("\n")
        body = "\n".join(lines[5:-2]).strip() if len(lines) > 7 else rec["text"]
        ref_line = lines[3] if len(lines) > 3 else ""
        layout = {"title": pl["title"], "ref": ref_line.replace("Ref ", "").split(" · ")[0], "date": ref_line.split(" · ")[-1],
                  "body": body, "signoff": lines[-1] if lines else ""}
    return _env.get_template("sealed_notice.html").render(
        issuer=who, layout=layout, paragraphs=[p.strip() for p in layout["body"].split("\n") if p.strip()],
        code=pl["code"], url=seal_url(pl["code"]), qr=qr_data_uri(seal_url(pl["code"])))


async def render_notice(rec: dict, layout: dict | None = None) -> tuple[bytes, bytes]:
    """(PDF, PNG) of a sealed notice, drawn by the same headless Chromium as the verdict cards."""
    await renderer.start()
    html = _notice_html(rec, layout)
    tmp = STATIC_DIR / f"_notice_{rec['payload']['code']}.html"     # served from static so the fonts resolve
    tmp.write_text(html, encoding="utf-8")
    try:
        page = await renderer._browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1.5)
        try:
            await page.goto(tmp.as_uri(), wait_until="load")
            await page.evaluate("document.fonts.ready")
            pdf = await page.pdf(format="A4", print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
            png = await page.screenshot(type="png", full_page=True)
        finally:
            await page.close()
    finally:
        tmp.unlink(missing_ok=True)
    return pdf, png


def render_notice_sync(rec: dict, layout: dict | None = None) -> tuple[bytes, bytes]:
    return asyncio.run(render_notice(rec, layout))
