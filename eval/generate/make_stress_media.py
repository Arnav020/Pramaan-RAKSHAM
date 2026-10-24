"""Generate the synthetic media for the stress test: python -m eval.generate.make_stress_media

Writes to data/media/stress/ (not committed: regenerate with this script). Everything is synthetic:
- screenshots of SMS / WhatsApp chats, rendered from HTML (Playwright + the app's own fonts)
- UPI QR posters (qrcode)
- PDF notices with a real text layer, and one scanned-style PDF (image only, so the OCR path runs)
- voice notes spoken by Windows' built-in synthetic voice (System.Speech), never a real person's voice
- videos: a still slide plus that synthetic voice (ffmpeg), so there is no face at all
All names, UPI IDs, phone numbers and domains are fictional.
"""

from __future__ import annotations

import asyncio
import base64
import io
import subprocess
import sys
from html import escape
from pathlib import Path

import qrcode
from PIL import Image

from app.config import DATA_DIR, STATIC_DIR

OUT = DATA_DIR / "media" / "stress"
FONTS = f"""<style>
@font-face{{font-family:'Noto Sans';src:url('{(STATIC_DIR / "fonts" / "NotoSans.ttf").as_uri()}')}}
@font-face{{font-family:'Noto Sans Devanagari';src:url('{(STATIC_DIR / "fonts" / "NotoSansDevanagari.ttf").as_uri()}')}}
@font-face{{font-family:'Gelasio';src:url('{(STATIC_DIR / "fonts" / "Gelasio.ttf").as_uri()}')}}
</style>"""

# ------------------------------------------------------------------ chat screenshots
SMS = """<!doctype html><html><head><meta charset="utf-8">{fonts}<style>
body{{margin:0;background:#eef1f4;font-family:'Noto Sans','Noto Sans Devanagari',sans-serif;width:420px}}
.top{{background:#fff;padding:14px 16px;border-bottom:1px solid #dde;font-weight:700;font-size:16px}}
.top small{{display:block;font-weight:400;color:#778;font-size:12px}}
.b{{background:#fff;margin:14px 60px 6px 12px;padding:12px 14px;border-radius:14px 14px 14px 4px;font-size:15px;line-height:1.45;color:#111;box-shadow:0 1px 2px #0002}}
.t{{font-size:11px;color:#889;margin:0 0 14px 16px}}
</style></head><body><div class="top">{sender}<small>{sub}</small></div>
{bubbles}</body></html>"""

WA = """<!doctype html><html><head><meta charset="utf-8">{fonts}<style>
body{{margin:0;background:#e5ddd5;font-family:'Noto Sans','Noto Sans Devanagari',sans-serif;width:420px}}
.top{{background:#075e54;color:#fff;padding:14px 16px;font-weight:700;font-size:16px}}
.top small{{display:block;font-weight:400;opacity:.8;font-size:12px}}
.b{{background:#fff;margin:12px 60px 4px 12px;padding:10px 12px;border-radius:0 10px 10px 10px;font-size:15px;line-height:1.45;color:#111}}
.t{{font-size:11px;color:#667;margin:0 0 10px 18px}}
</style></head><body><div class="top">{sender}<small>{sub}</small></div>
{bubbles}</body></html>"""

SHOTS = {
    "shot_sms_kyc.png": (SMS, "VK-SBIUPD", "Text message · Today 10:42", [
        "Dear Customer, your SBI YONO account will be BLOCKED today. Update your PAN immediately: http://sbi-yono-update.co.in/kyc"]),
    "shot_wa_investment.png": (WA, "+91 90000 33333", "VIP Stock Club (admin)", [
        "Welcome to VIP Stock Club! Our SEBI registered expert gives 15% guaranteed weekly returns.",
        "Today's slot is closing. Pay Rs 15,000 membership to vipclub.stocks@ybl and send screenshot."]),
    "shot_wa_family_hi.png": (WA, "+91 90000 44444", "online", [
        "मम्मी ये मेरा नया नंबर है, पुराना फ़ोन टूट गया।",
        "बहुत ज़रूरी है, अभी 10000 रुपये भेज दो इस UPI पर: rohit.help22@ybl . कॉल मत करना, बाद में बताऊँगा।"]),
    "shot_sms_otp_genuine.png": (SMS, "AX-HDFCBK-T", "Text message · Today 09:15", [
        "531902 is your OTP for a payment of Rs 1,299.00 at Myntra on HDFC Bank Card ending 2231. Do not share this OTP with anyone. -HDFC Bank"]),
    "shot_sms_debit_genuine.png": (SMS, "JD-SBIUPI-S", "Text message · Yesterday", [
        "Dear UPI user A/C X4521 debited by 250.00 on 23Oct26 trf to ZOMATO Refno 429911223344. If not u? call 1800111109. -SBI"]),
}

# ------------------------------------------------------------------ notices (PDF)
NOTICE = """<!doctype html><html><head><meta charset="utf-8">{fonts}<style>
body{{font-family:'Gelasio',Georgia,serif;margin:56px;color:#111;font-size:15px}}
.h{{text-align:center;border-bottom:3px double #333;padding-bottom:10px;margin-bottom:18px}}
.h b{{display:block;font-size:22px;letter-spacing:2px}} p{{line-height:1.6}}
.qr{{float:right;text-align:center;font-family:'Noto Sans';font-size:11px;margin:0 0 8px 12px}} .qr img{{width:140px}}
</style></head><body><div class="h"><b>{head}</b>{sub}</div>{qr}{body}</body></html>"""

NOTICES = {
    "notice_court_summons.pdf": ("SUPREME COURT OF INDIA", "Office of the Registrar · Case No. SC/CYB/2026/4471", "upi://pay?pa=court.fine.deposit@ybl&pn=Mahesh%20Verma&am=95000",
        "<p>To the account holder,</p><p>A money laundering case has been registered against your Aadhaar-linked bank account. You are hereby directed to "
        "deposit a refundable security amount of <b>Rs 95,000</b> within 24 hours to avoid an arrest warrant.</p>"
        "<p>Pay to <b>court.fine.deposit@ybl</b> or scan the QR code. This matter is confidential; do not discuss it with your family or bank.</p>"),
    "notice_bank_genuine.pdf": ("DEMO BANK", "Customer communication · Ref DB/CC/2026/118", None,
        "<p>Dear Customer,</p><p>We are revising the interest rate on savings accounts from 1 November 2026. The revised rates are available on our "
        "website demobank.bank.in and at all branches.</p><p>Demo Bank will never ask for your OTP, PIN or password. For queries call our "
        "official number printed on your passbook.</p>"),
}

# ------------------------------------------------------------------ QR posters (PNG)
POSTERS = {
    "poster_qr_electricity.png": ("upi://pay?pa=bill.clear.help@ybl&pn=Ramesh%20Kumar", "PSPCL ELECTRICITY BILL", "Your connection will be cut tonight. Scan to pay pending bill now."),
    "poster_qr_shop_genuine.png": ("upi://pay?pa=sharmastore@ybl&pn=Sharma%20General%20Store", "SHARMA GENERAL STORE", "Scan & pay with any UPI app. Thank you!"),
}

# ------------------------------------------------------------------ voice notes and videos
VOICES = {
    "voice_bank_otp.wav": "Hello sir, I am calling from your bank's head office. Your debit card has been blocked due to suspicious activity. "
                          "To unblock it right now, please tell me the six digit OTP you have just received on your phone.",
    "voice_digital_arrest.wav": "This is officer Rathore from the Mumbai crime branch. A parcel with drugs and fake passports was found in your name. "
                                "You are under digital arrest. Do not tell anyone. Transfer two lakh rupees for verification immediately or you will be arrested.",
    "voice_genuine_reminder.wav": "Hi, this is Neha. Just reminding you that we are meeting at six for dinner. See you there.",
}
VIDEOS = {
    "video_investment_ad.mp4": ("Earn 3% daily, guaranteed", "Invest in our crypto plan and earn three percent daily guaranteed profit. "
                                "Double your money in one month. Send ten thousand rupees to crypto king at y b l to start today, offer ends tonight."),
    "video_awareness_genuine.mp4": ("Stay safe online", "Your bank will never ask for your OTP or PIN. If you get a suspicious call, hang up and "
                                    "call the number printed on your card. If you have lost money, call 1930."),
}


def qr_uri(data: str) -> str:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def say(text: str, out: Path) -> None:
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.Rate = 0; $s.SetOutputToWaveFile('{out}'); $s.Speak(@'\n{text}\n'@); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


async def render_all() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(device_scale_factor=2, viewport={"width": 420, "height": 300})
        for name, (tpl, sender, sub, msgs) in SHOTS.items():
            bubbles = "".join(f'<div class="b">{escape(m)}</div><div class="t">{"10:4" + str(i)}</div>' for i, m in enumerate(msgs))
            await page.set_content(tpl.format(fonts=FONTS, sender=escape(sender), sub=escape(sub), bubbles=bubbles))
            await page.evaluate("document.fonts.ready")
            await page.screenshot(path=str(OUT / name), full_page=True)
        for name, (head, sub, qr, body) in NOTICES.items():
            q = f'<div class="qr"><img src="{qr_uri(qr)}"><br>Scan to deposit</div>' if qr else ""
            await page.set_content(NOTICE.format(fonts=FONTS, head=head, sub=f"<div>{sub}</div>", qr=q, body=body))
            await page.evaluate("document.fonts.ready")
            await page.pdf(path=str(OUT / name), format="A4", print_background=True)
        # a scanned-looking copy of the court summons: the page as a picture inside a PDF, so only OCR can read it
        head, sub, qr, body = NOTICES["notice_court_summons.pdf"]
        await page.set_viewport_size({"width": 900, "height": 1200})
        await page.set_content(NOTICE.format(fonts=FONTS, head=head, sub=f"<div>{sub}</div>", qr=f'<div class="qr"><img src="{qr_uri(qr)}"></div>', body=body))
        await page.evaluate("document.fonts.ready")
        png = await page.screenshot(full_page=True)
        Image.open(io.BytesIO(png)).convert("L").convert("RGB").save(OUT / "notice_court_summons_scanned.pdf", "PDF", resolution=110)
        for name, (data, head, line) in POSTERS.items():
            html = (f'<!doctype html><html><head><meta charset="utf-8">{FONTS}</head><body style="margin:0;width:600px;font-family:Noto Sans;text-align:center;'
                    f'background:#fff8e6;padding:30px 0"><h1 style="margin:0 20px">{escape(head)}</h1><p style="font-size:20px;margin:14px 30px">{escape(line)}</p>'
                    f'<img src="{qr_uri(data)}" style="width:360px"></body></html>')
            await page.set_viewport_size({"width": 600, "height": 700})
            await page.set_content(html)
            await page.evaluate("document.fonts.ready")
            await page.screenshot(path=str(OUT / name), full_page=True)
        for name, (title, _) in VIDEOS.items():
            await page.set_viewport_size({"width": 1280, "height": 720})
            await page.set_content(f'<!doctype html><html><head><meta charset="utf-8">{FONTS}</head><body style="margin:0;width:1280px;height:720px;'
                                   f'display:flex;align-items:center;justify-content:center;background:#1d2b3a;color:#fff;font-family:Noto Sans;font-size:64px">'
                                   f'{escape(title)}</body></html>')
            await page.screenshot(path=str(OUT / (name + ".png")))
        await b.close()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("The synthetic voices use Windows System.Speech; run this on Windows.")
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(render_all())
    for name, text in VOICES.items():
        say(text, OUT / name)
    for name, (_, text) in VIDEOS.items():
        wav = OUT / (name + ".wav")
        say(text, wav)
        still = OUT / (name + ".png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(still), "-i", str(wav), "-c:v", "libx264", "-tune", "stillimage",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(OUT / name)], check=True)
        wav.unlink(missing_ok=True)
        still.unlink(missing_ok=True)
    for f in sorted(OUT.iterdir()):
        print(f"{f.stat().st_size // 1024:6d} KB  {f.name}")


if __name__ == "__main__":
    main()
