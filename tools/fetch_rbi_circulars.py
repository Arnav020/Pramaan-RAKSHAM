"""Politely fetch recent RBI notifications into a reference index for document Source-Match.

One listing page + one request per notification, 2.5 s apart, with an honest
User-Agent. Stores only public metadata: reference numbers, date, title,
addressee and the first lines of the body.

Usage:  python -m tools.fetch_rbi_circulars [max_items]
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date

import httpx
from bs4 import BeautifulSoup

from app.config import RAILS_DATA_DIR

BASE = "https://www.rbi.org.in/Scripts/"
UA = {"User-Agent": "Mozilla/5.0 (Pramaan student hackathon project; polite crawl, 1 request / 2.5 s)"}
OUT = RAILS_DATA_DIR / "rbi_circulars.json"
REF = re.compile(r"RBI/\d{4}-\d{2}/\d+")
DEPT = re.compile(r"\b[A-Za-z]{2,10}(?:\.[A-Za-z0-9()\-]{1,20}){1,6}/[\d.]+/\d{4}-\d{2}\b")
DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b")


def parse(html: str, url: str) -> dict | None:
    lines = [l.strip() for l in BeautifulSoup(html, "html.parser").get_text("\n").splitlines() if l.strip()]
    try:
        i = next(k for k, l in enumerate(lines) if REF.fullmatch(l))
    except StopIteration:
        return None
    title = lines[i - 1]
    dept = next((l for l in lines[i + 1:i + 4] if DEPT.search(l)), None)
    dt = next((l for l in lines[i + 1:i + 5] if DATE.fullmatch(l)), None)
    body_start = i + 1 + sum(1 for x in (dept, dt) if x)
    addressee = lines[body_start] if body_start < len(lines) else ""
    body = []
    for l in lines[body_start + 1:]:
        if l.startswith("Yours faithfully") or len(" ".join(body)) > 900:
            break
        if l not in ("Madam / Sir,", "Madam/Sir,", "Dear Sir/Madam,"):
            body.append(l)
    return {"ref": lines[i], "dept_ref": dept, "date": dt, "title": title, "addressee": addressee, "summary": " ".join(body)[:900], "url": url}


def main(max_items: int = 40) -> None:
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as c:
        listing = c.get(BASE + "NotificationUser.aspx").text
        ids = list(dict.fromkeys(re.findall(r"NotificationUser\.aspx\?Id=(\d+)&(?:amp;)?Mode=0", listing)))[:max_items]
        print(f"{len(ids)} notifications listed")
        items = []
        for n, i in enumerate(ids, 1):
            time.sleep(2.5)
            url = f"{BASE}NotificationUser.aspx?Id={i}&Mode=0"
            try:
                rec = parse(c.get(url).text, url)
            except httpx.HTTPError as e:
                print(f"  {i}: {type(e).__name__}, stopping politely")
                break
            if rec:
                items.append(rec)
                print(f"  {n:2d} {rec['ref']:18s} {rec['date']} | {rec['title'][:70]}")
    OUT.write_text(json.dumps({
        "_provenance": {"dataset": "RBI notifications (reference index for document Source-Match)", "source_url": BASE + "NotificationUser.aspx",
                        "fetched_at": date.today().isoformat(), "note": f"{len(items)} recent notifications, public metadata only. Production: full sync of RBI and SEBI circular indexes."},
        "circulars": items}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(items)} -> {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
