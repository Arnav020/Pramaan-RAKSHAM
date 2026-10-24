"""Convert the RBI Alert List PDF (downloaded by a person; the site has a CAPTCHA)
into app/rails/data/alert_list.json.

Usage:  python tools/load_alert_list.py data/raw/registries/<file>.pdf
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "rails" / "data" / "alert_list.json"
ROW = re.compile(r"^(\d{1,3})\s+(.+?)\s*(https?://\S+)?\s*$")
URL = re.compile(r"https?://\S+")


def parse(text: str) -> tuple[str | None, list[dict]]:
    m = re.search(r"Updated as on ([A-Za-z]+ \d{1,2}, \d{4})", text)
    as_on = m.group(1) if m else None
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Sr. No"):
            continue
        r = ROW.match(line)
        if r and (not rows or int(r.group(1)) == rows[-1]["sr"] + 1):
            name = r.group(2).strip()
            urls = URL.findall(line)
            name = URL.sub("", name).strip()
            rows.append({"sr": int(r.group(1)), "name": name, "websites": urls})
        elif rows and URL.match(line):
            rows[-1]["websites"].extend(URL.findall(line))
    return as_on, rows


def main(pdf_path: str) -> None:
    pdf = Path(pdf_path)
    text = "\n".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
    as_on, rows = parse(text)
    assert rows and rows[-1]["sr"] == len(rows), f"parse error: {len(rows)} rows, last sr {rows[-1]['sr'] if rows else None}"
    entities = []
    for r in rows:
        domains = sorted({re.sub(r"^www\.", "", re.sub(r"^https?://", "", u).split("/")[0].lower()) for u in r["websites"]})
        compact = re.sub(r"[^a-z0-9]", "", r["name"].lower())
        entities.append({"sr": r["sr"], "name": r["name"], "aliases": [compact] if compact != r["name"].lower() else [], "domains": domains})
    out = {
        "_provenance": {
            "dataset": f"RBI Alert List of unauthorised forex trading platforms (updated as on {as_on})",
            "source_url": "https://rbidocs.rbi.org.in/rdocs/Content/PDFs/ALERTLIST10022023CC5FC4ACD619431497A56B6894188478.PDF",
            "fetched_at": date.today().isoformat(),
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "note": f"{len(entities)} entities. RBI states the list is not exhaustive; absence from it is not authorisation. Downloaded manually (the site uses a CAPTCHA).",
        },
        "entities": entities,
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(entities)} entities as on {as_on} -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
