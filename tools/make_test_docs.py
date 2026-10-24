"""Make test documents: forged 'RBI' notices, a genuine-style Demo Bank notice,
a scam poster with a UPI QR code. All carry a small SPECIMEN footer and are
used only to test Pramaan. Output: data/media/test/ (git-ignored).

Usage:  python -m tools.make_test_docs
"""

from __future__ import annotations

import asyncio
import base64
import io
import json

import qrcode
from playwright.async_api import async_playwright
from pypdf import PdfReader, PdfWriter

from app.config import DATA_DIR, RAILS_DATA_DIR, STATIC_DIR

OUT = DATA_DIR / "media" / "test"
SPECIMEN = "SPECIMEN · TEST DATA · created for the Pramaan hackathon demo · not a real document"


def qr_uri(data: str) -> str:
    img = qrcode.make(data, box_size=8, border=2)
    b = io.BytesIO(); img.save(b, format="PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


NOTICE = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{css}"><style>
body{{font-family:'Gelasio',Georgia,serif;margin:48px 56px;color:#111;font-size:15px}}
.lh{{text-align:center;border-bottom:3px double #1f3a5f;padding-bottom:10px;margin-bottom:18px}}
.lh .n{{font-size:22px;font-weight:700;letter-spacing:2px;color:#1f3a5f}} .lh .h{{font-family:'Noto Sans Devanagari';font-size:18px;color:#1f3a5f}}
.ref{{display:flex;justify-content:space-between;font-family:'Noto Sans';font-size:13px;margin-bottom:16px}}
h3{{text-align:center;text-decoration:underline;font-size:16px}} p{{line-height:1.6}} .qr{{float:right;text-align:center;font-family:'Noto Sans';font-size:11px;margin:0 0 8px 12px}}
.qr img{{width:150px}} .sp{{position:fixed;bottom:12px;left:0;right:0;text-align:center;font-family:'Noto Sans';font-size:9px;color:#999}}
</style></head><body>
<div class="lh"><div class="h">{hi_head}</div><div class="n">{head}</div><div style="font-family:'Noto Sans';font-size:12px">{sub}</div></div>
<div class="ref"><span>{ref}</span><span>{date}</span></div>
{qr}
<p>{to}</p><h3>{subject}</h3>{body}
<p style="margin-top:28px">Yours faithfully,<br>({signer})<br>{desig}</p>
<div class="sp">{specimen}</div></body></html>"""

FORGED_BODY = """<p>It has come to the notice of the Reserve Bank that your savings account has been linked to suspicious transactions under
investigation for money laundering. As per the above circular, all affected customers must place a <b>refundable safety deposit of
Rs 25,000</b> with the Reserve Bank within <b>24 hours</b> to protect their account.</p>
<p>Transfer the amount to the RBI safety account <b>rbi.safe@okaxis</b> or scan the QR code. Failure to comply will result in your account
being frozen and legal action. Do not discuss this matter with bank staff or family members, as the investigation is confidential.</p>
<p>For assistance call our officer on 9876543210.</p>"""


async def render(page, html: str, pdf_path=None, png_path=None, size=(1240, 1754)):
    tmp = STATIC_DIR / "_doc_tmp.html"
    tmp.write_text(html, encoding="utf-8")
    await page.set_viewport_size({"width": size[0], "height": size[1]})
    await page.goto(tmp.as_uri()); await page.evaluate("document.fonts.ready")
    if pdf_path:
        await page.pdf(path=str(pdf_path), format="A4", print_background=True)
    if png_path:
        await page.screenshot(path=str(png_path), full_page=True)
    tmp.unlink(missing_ok=True)


def set_meta(path, producer: str, creator: str, js: str | None = None):
    w = PdfWriter(clone_from=PdfReader(path))
    w.add_metadata({"/Producer": producer, "/Creator": creator})
    if js:
        w.add_js(js)
    with open(path, "wb") as f:
        w.write(f)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    css = (STATIC_DIR / "pramaan.css").as_uri()
    real = {c["ref"]: c for c in json.loads((RAILS_DATA_DIR / "rbi_circulars.json").read_text(encoding="utf-8"))["circulars"]}
    c290 = real["RBI/2026-27/290"]
    qr = f'<div class="qr"><img src="{qr_uri("upi://pay?pa=rbi.safe@okaxis&pn=Rahul%20Kumar&am=25000&tn=RBI%20safety%20deposit")}"><br>Scan to deposit</div>'
    common = dict(css=css, hi_head="भारतीय रिज़र्व बैंक", head="RESERVE BANK OF INDIA", sub="Department of Regulation · Central Office · Mumbai",
                  to="To,<br>The Account Holder", signer="R. K. Sharma", desig="Chief General Manager", specimen=SPECIMEN)
    async with async_playwright() as pw:
        b = await pw.chromium.launch(); page = await b.new_page()
        # 1. forged notice citing a REAL circular number about something else
        p1 = OUT / "forged_rbi_notice_realref.pdf"
        await render(page, NOTICE.format(**common, ref=c290["ref"] + "<br>DoR.RET.REC.247/12.01.001/2026-27", date="October 20, 2026",
                                         qr=qr, subject="Mandatory Safety Deposit for Account Protection", body=FORGED_BODY), pdf_path=p1)
        set_meta(p1, "Canva", "Canva")
        # 2. forged notice citing an impossible number
        p2 = OUT / "forged_rbi_notice_futureref.pdf"
        await render(page, NOTICE.format(**common, ref="RBI/2026-27/912", date="October 20, 2026", qr=qr,
                                         subject="Mandatory Safety Deposit for Account Protection", body=FORGED_BODY), pdf_path=p2)
        # 3. forged notice with hidden JavaScript (harmless alert) to test active-content detection
        p3 = OUT / "forged_rbi_notice_js.pdf"
        await render(page, NOTICE.format(**common, ref="RBI/2026-27/912", date="October 20, 2026", qr="",
                                         subject="Mandatory Safety Deposit for Account Protection", body=FORGED_BODY), pdf_path=p3)
        set_meta(p3, "iLovePDF", "Microsoft Word", js="app.alert('Pramaan test: harmless script');")
        # 4. genuine-style Demo Bank notice (fictional bank, reserved .test domain, 1600 number)
        p4 = OUT / "genuine_demobank_notice.pdf"
        await render(page, NOTICE.format(css=css, hi_head="डेमो बैंक", head="DEMO BANK", sub="Customer Safety Team · demobank.test",
                                         to="Dear Customer,", ref="DB/CS/2026/118", date="October 20, 2026", qr="", signer="Customer Safety Team",
                                         desig="Demo Bank", specimen=SPECIMEN, subject="Stay safe from fraud calls and messages",
                                         body="<p>Demo Bank will never ask for your OTP, PIN or password, and we never ask you to move money to a 'safe account'. "
                                              "Our official calls come only from numbers starting with 1600, such as 1600123456. Read our safety guide at "
                                              "https://demobank.test/safety. If you have lost money to a fraud, call 1930 immediately.</p>"), pdf_path=p4)
        # 5. faithful copy of the real circular (control: must not be flagged)
        p5 = OUT / "genuine_rbi_circular_copy.pdf"
        await render(page, NOTICE.format(**common | {"to": c290["addressee"], "signer": "Chief General Manager", "desig": ""}, ref=c290["ref"],
                                         date=c290["date"], qr="", subject=c290["title"], body=f"<p>{c290['summary']}</p>"), pdf_path=p5)
        # 6. scam poster image with a UPI QR code
        poster = f"""<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{css}"><style>
        body{{margin:0;width:1080px;height:1350px;background:linear-gradient(160deg,#0b3d2e,#14532d);color:#fff;font-family:'Noto Sans';text-align:center}}
        h1{{font-size:78px;margin:70px 40px 10px;color:#facc15}} h2{{font-size:44px;margin:10px}} p{{font-size:34px;margin:14px 60px}}
        .q{{background:#fff;display:inline-block;padding:18px;border-radius:16px;margin-top:20px}} .q img{{width:360px}} .s{{font-size:16px;color:#cfd;position:absolute;bottom:14px;width:100%}}
        </style></head><body><h1>GUARANTEED 300% RETURNS</h1><h2>SEBI Registered Advisor · VIP Stock Tips</h2>
        <p>Join our VIP Telegram group today only. Limited seats!</p><p>Pay ₹10,000 registration fee</p>
        <div class="q"><img src="{qr_uri('upi://pay?pa=profitwealth@ybl&pn=Profit%20Wealth%20Traders&am=10000')}"></div>
        <p style="font-size:28px">Scan &amp; pay to join · t.me/vipstocktipsdemo</p><div class="s">{SPECIMEN}</div></body></html>"""
        await render(page, poster, png_path=OUT / "scam_poster_qr.png", size=(1080, 1350))
        # 7. Hindi message screenshot (tests the Devanagari OCR fallback)
        hindi = f"""<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{css}"><style>
        body{{margin:0;width:900px;background:#efeae2;font-family:'Noto Sans Devanagari','Noto Sans'}} .b{{background:#fff;margin:40px;padding:26px 30px;border-radius:14px;font-size:30px;line-height:1.6}}
        .h{{font-weight:700;color:#1f3a5f}} .s{{font-size:13px;color:#999;text-align:center;padding-bottom:16px}}</style></head><body>
        <div class="b"><div class="h">भारतीय रिज़र्व बैंक · सूचना</div>आपका बैंक खाता मनी लॉन्ड्रिंग जाँच में पाया गया है। खाता बंद होने से बचाने के लिए
        24 घंटे के अंदर ₹25,000 सुरक्षा जमा rbi.safe@okaxis पर भेजें। किसी को न बताएँ। अधिकारी से बात करें: 9876543210</div>
        <div class="s">{SPECIMEN}</div></body></html>"""
        await render(page, hindi, png_path=OUT / "hindi_rbi_message.png", size=(900, 600))
        await b.close()
    for f in sorted(OUT.glob("*.pdf")) + sorted(OUT.glob("*.png")):
        print(f.name)


if __name__ == "__main__":
    asyncio.run(main())
