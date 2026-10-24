"""Make the Pramaan Seal demo items: python -m tools.make_seal_demos [--base https://<server>]

Issues seals as the fictional TEST issuer Demo Bank and writes, into data/media/test/:
  sealed_notice_genuine.pdf   a genuine sealed notice                         -> green "Signed by Demo Bank"
  sealed_notice_edited.pdf    the same notice, amount edited + a payment ask  -> red (content changed + personal UPI)
  sealed_notice_copied.pdf    a different scam notice carrying the genuine seal -> red (content changed + regulator UPI)
  sealed_sms_genuine.txt      a sealed Demo Bank SMS to paste into the bot     -> green
The seal records go to data/seals/ (deploy them with the data so the server can verify).
"""

from __future__ import annotations

import argparse
import asyncio
import os

from app.config import DATA_DIR

OUT = DATA_DIR / "media" / "test"

NOTICE_BODY = ("Dear customer, your fixed deposit number FD-0451 of Rs 50,000 matures on 1 Nov 2026. "
               "The maturity amount will be credited automatically to your linked Demo Bank savings account ending 4821. "
               "No action is needed from you.\n"
               "Demo Bank will never ask you to pay a fee, share an OTP or move money to receive your maturity amount. "
               "If anyone does, call the number printed on your passbook or report it on 1930.")
EDITED_BODY = ("Dear customer, your fixed deposit number FD-0451 of Rs 5,00,000 matures on 1 Nov 2026. "
               "To release the maturity amount, pay a processing fee of Rs 2,500 to demobank.fd@ybl within 24 hours, "
               "after which it will be credited to your account.\n"
               "If the fee is not paid today, the deposit will be frozen under RBI rules.")
COPIED_TITLE = "Account freeze notice"
COPIED_BODY = ("Your Demo Bank account has been frozen because of suspicious activity linked to a money laundering case. "
               "To unfreeze it, deposit a refundable safety amount of Rs 25,000 to rbi.safe@okaxis within 12 hours. "
               "Do not tell anyone about this notice.")
SMS_BODY = "Demo Bank: Rs 50,000 credited to your savings account ending 4821 on maturity of FD-0451. No action needed."


async def main_async() -> None:
    from app.render import notice
    from app.render.card import renderer
    OUT.mkdir(parents=True, exist_ok=True)
    rec = notice.issue_notice("demobank", "Fixed deposit maturity notice", NOTICE_BODY, ref="DB/FD/2026/0451")
    layout = rec["layout"]
    pdf, png = await notice.render_notice(rec, layout)
    (OUT / "sealed_notice_genuine.pdf").write_bytes(pdf)
    (OUT / "sealed_notice_genuine.png").write_bytes(png)
    # An edit of the genuine notice: same seal, different amount and a payment ask
    pdf, _ = await notice.render_notice(rec, layout | {"body": EDITED_BODY})
    (OUT / "sealed_notice_edited.pdf").write_bytes(pdf)
    # A different scam notice wearing the genuine seal
    pdf, _ = await notice.render_notice(rec, layout | {"title": COPIED_TITLE, "body": COPIED_BODY, "ref": "DB/SEC/2026/0099"})
    (OUT / "sealed_notice_copied.pdf").write_bytes(pdf)
    sms = notice.issue_sms("demobank", "FD maturity credit", SMS_BODY)
    (OUT / "sealed_sms_genuine.txt").write_text(notice.sms_text(SMS_BODY, sms["payload"]["code"]), encoding="utf-8")
    await renderer.close()
    print("notice seal", rec["payload"]["code"], "| sms seal", sms["payload"]["code"], "| base", notice.seal_url(""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="public address the seal QR codes should open, e.g. https://13-207-22-33.sslip.io")
    a = ap.parse_args()
    if a.base:
        os.environ["PUBLIC_BASE_URL"] = a.base.rstrip("/")
        from app.config import get_settings
        get_settings.cache_clear()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
