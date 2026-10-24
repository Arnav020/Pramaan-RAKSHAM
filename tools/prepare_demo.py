"""Reset the demo data and pre-check every demo item: python -m tools.prepare_demo [--reset]

--reset   back up data/pramaan.db, data/vault and data/store to data/backup/<time>/ and start clean.
          (Stop the server first: `sudo systemctl stop pramaan`.)

Then every stage item is checked once (warm cache, stable links), one campaign is reported and CONFIRMED
so Lookup and the pay-time pause can be shown at any desk visit (rbi.safe@okaxis, from the forged RBI
notice), and data/demo_links.json is written for the landing page and the demo script.
profitwealth@ybl is deliberately left UNconfirmed: confirming it is the live "review once, protect many" beat.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR

T = DATA_DIR / "media" / "test"
G = DATA_DIR / "media" / "genuine"

ITEMS = [
    # key, what it shows, text, media
    ("expose", "Lip-synced deepfake of consented footage: same footage, different words", "", T / "fake_lipsync_clip1_arnav_en_wa.mp4"),
    ("official_video", "Demo Bank's own video, unchanged (official channel)", "", T / "genuine_clip3_krish_en_wa.mp4"),
    ("sealed_notice", "Genuine Demo Bank notice with a Pramaan Seal", "", T / "sealed_notice_genuine.pdf"),
    ("edited_notice", "The same sealed notice with the amount edited and a fee added", "", T / "sealed_notice_edited.pdf"),
    ("copied_seal", "A scam notice wearing Demo Bank's genuine seal", "", T / "sealed_notice_copied.pdf"),
    ("forged_rbi", "Forged RBI notice borrowing a real circular number, QR pays a person", "", T / "forged_rbi_notice_realref.pdf"),
    ("qr_poster", "Investment poster whose QR code pays a personal UPI ID", "", T / "scam_poster_qr.png"),
    ("blurry_genuine", "Blurry genuine clip that detectors call fake", "", T / "genuine_clip1_arnav_en_lowq.mp4"),
    ("cbi_call", "Digital-arrest call described by the victim", "He says he is from CBI, a parcel in my name has drugs, I must transfer ₹50,000 to cbi.verify@ybl now and tell no one. Calling from 9876543210", None),
    ("vip_scam", "SEBI claim + guaranteed returns + personal UPI ID", "SEBI registered adviser. Guaranteed 300% returns in our VIP group. Pay ₹10,000 to profitwealth@ybl today only.", None),
    ("genuine_bank", "Genuine bank message from an official domain", "HDFC Bank: your e-statement for October is ready. View it on https://hdfc.bank.in/statements. Never share your OTP.", None),
]


def reset() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = DATA_DIR / "backup" / stamp
    bak.mkdir(parents=True, exist_ok=True)
    for name in ("pramaan.db", "pramaan.db-wal", "pramaan.db-shm", "vault", "store", "demo_links.json"):
        p = DATA_DIR / name
        if p.exists():
            shutil.move(str(p), str(bak / name))
    return bak


async def main_async(do_reset: bool) -> None:
    if do_reset:
        print("backed up old demo data to", reset())
    from app.service import check_and_record, report_check
    from app.store import campaigns, db, seal
    db.init()
    links = {"items": {}}
    for key, what, text, media in ITEMS:
        paths = [media] if media else []
        if media and not media.exists():
            print("skip", key, "(missing", media.name, ")")
            continue
        rec = await check_and_record(text, paths, channel="demo")
        links["items"][key] = {"id": rec.id, "serial": rec.serial, "level": rec.verdict.level.value, "what": what}
        print(f"{key:15s} {rec.verdict.level.value:5s} {rec.serial}  /p/{rec.id}")
    sms_path = T / "sealed_sms_genuine.txt"
    if sms_path.exists():
        rec = await check_and_record(sms_path.read_text(encoding="utf-8"), [], channel="demo")
        links["items"]["sealed_sms"] = {"id": rec.id, "serial": rec.serial, "level": rec.verdict.level.value, "what": "Sealed Demo Bank SMS"}
        print(f"{'sealed_sms':15s} {rec.verdict.level.value:5s} {rec.serial}")
    # One confirmed campaign for Lookup / pay-time demos at any time
    forged = links["items"].get("forged_rbi")
    if forged:
        r = await asyncio.to_thread(report_check, forged["id"], None, "demo setup")
        campaigns.decide(r["campaign_id"], "confirmed", "FRM analyst (demo setup)", "pre-confirmed for the desk demo")
        links["confirmed_campaign"] = {"id": r["campaign_id"], "indicators": r["indicators"]}
        print("confirmed campaign", r["campaign_id"], r["indicators"])
    notice_seal = next((s["code"] for s in seal.list_seals() if s["doc_type"] == "notice" and not s["revoked"]), None)
    links["seal"] = notice_seal
    links["expose"] = links["items"].get("expose", {}).get("id")
    links["generated_at"] = datetime.now().isoformat(timespec="seconds")
    (DATA_DIR / "demo_links.json").write_text(json.dumps(links, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", DATA_DIR / "demo_links.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    asyncio.run(main_async(a.reset))


if __name__ == "__main__":
    main()
