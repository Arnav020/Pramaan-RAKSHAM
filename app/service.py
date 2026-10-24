"""One call that every channel uses: run a check, sign it, keep its proof.

Channels (Telegram, web upload, share target) only differ in how they receive
the item and deliver the card; the verdict logic is identical for all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, STORE_DIR, get_settings
from app.lanes.site_check import public_facts
from app.lanes.source_match import load_index, word_flags
from app.models import Verdict
from app.pipeline import CheckInput, CheckResult, run_check
from app.store import db
from app.store.signing import key_id, sign

log = logging.getLogger("pramaan.service")
GENUINE_DIR = DATA_DIR / "media" / "genuine"


@dataclass
class Record:
    id: str
    serial: str
    verdict: Verdict
    timings: dict
    proof: dict
    media: list[str]
    signature: str
    payload: dict

    @property
    def proof_url(self) -> str:
        return f"{get_settings().public_base_url}/p/{self.id}"

    @property
    def verify_url(self) -> str:
        return f"{get_settings().public_base_url}/v/{self.serial}?s={self.signature}"


def reason_digest(v: Verdict) -> str:
    return hashlib.sha256("\n".join(r.text_en for r in v.reasons).encode("utf-8")).hexdigest()[:16]


def _grab_frame(video: Path, t: float, out: Path, width: int = 640) -> bool:
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t, 0):.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-q:v", "3", str(out)], check=True, capture_output=True, timeout=30)
        return out.exists()
    except (subprocess.SubprocessError, OSError):
        return False


def build_proof(res: CheckResult, check_dir: Path, media_files: list[str]) -> dict:
    """Evidence for the proof page and the card: source match, word diff, thumbnails."""
    v = res.verdict
    b = v.lanes.get("B")
    proof: dict = {"timings": {k: round(x, 3) for k, x in res.timings.items()}, "media": media_files}
    sites = []
    for i, (facts, shot) in enumerate(res.sites):
        d = public_facts(facts)
        if shot:
            check_dir.mkdir(parents=True, exist_ok=True)
            (check_dir / f"site_{i}.jpg").write_bytes(shot)
            d["screenshot"] = f"site_{i}.jpg"
        sites.append(d)
    if sites:
        proof["sites"] = sites
    doc = next((m.doc for m in res.media if m.doc is not None), None)
    if doc is not None:
        page = None
        if doc.page_images:
            im = doc.page_images[0].copy()
            im.thumbnail((900, 1270))
            im.save(check_dir / "doc_page.jpg", quality=85)
            page = "doc_page.jpg"
        proof["doc"] = {
            "kind": doc.kind, "page": page, "pages": doc.pages, "producer": doc.producer, "creator": doc.creator,
            "created": doc.created, "modified": doc.modified, "incremental_saves": doc.incremental_saves,
            "active_content": doc.active_content, "links": doc.links[:10], "editor_hint": doc.editor_hint,
            "ocr": doc.ocr_provider if doc.ocr_used else None, "text": doc.text[:3000],
            "qr": [{"upi": q.upi, "payee_name": q.payee_name, "amount": q.amount, "note": q.note, "raw": q.raw[:200]} for q in doc.qr],
            "refs": (b.detail.get("doc_refs") if b else None) or [],
        }
    elif b and b.detail.get("doc_refs"):
        proof["doc"] = {"kind": "text", "page": None, "qr": [], "refs": b.detail["doc_refs"]}
    if b and b.detail.get("source"):
        src = dict(b.detail["source"])
        ref = next((r for r in load_index() if r.id == src["ref_id"]), None)
        proof["source"] = src
        proof["diff"] = b.detail.get("diff")
        query_video = check_dir / media_files[0] if media_files else None
        if ref and query_video and query_video.exists() and src.get("pairs"):
            pairs = src["pairs"]
            qt, rt, _ = pairs[len(pairs) // 2]
            ok1 = _grab_frame(GENUINE_DIR / ref.file, rt, check_dir / "thumb_ref.jpg")
            ok2 = _grab_frame(query_video, qt, check_dir / "thumb_query.jpg")
            if ok1 and ok2:
                proof["thumbs"] = {"ref": "thumb_ref.jpg", "query": "thumb_query.jpg", "ref_t": rt, "query_t": qt}
        tr = next((m.transcript for m in res.media if m.transcript), None)
        if tr:
            proof["query_transcript"] = {"text": tr.text, "words": tr.words, "language": tr.language}
        if ref:
            proof["ref_transcript"] = {"text": ref.transcript.get("text", ""), "words": ref.transcript.get("words", [])}
        if tr and ref:
            qw = [w["word"] for w in tr.words] or tr.text.split()
            rw = [w["word"] for w in ref.transcript.get("words", [])] or ref.transcript.get("text", "").split()
            proof["qflags"], proof["rflags"] = word_flags(rw, qw)
    return proof


async def check_and_record(text: str, media_paths: list[Path], channel: str, chat_raw: str | int | None = None,
                           language_hint: str | None = None) -> Record:
    db.init()
    res = await run_check(CheckInput(text=text, media_paths=media_paths, language_hint=language_hint))
    v = res.verdict
    check_id, serial = db.new_id(), db.next_serial()
    media_files = db.save_media(check_id, media_paths) if media_paths else []
    proof = build_proof(res, STORE_DIR / check_id, media_files)
    issued = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = {"v": 1, "serial": serial, "id": check_id, "level": v.level.value, "issued": issued,
               "reasons": [r.code for r in v.reasons], "digest": reason_digest(v), "kid": key_id()}
    signature = sign(payload)
    db.insert_check({
        "id": check_id, "serial": serial, "created_at": issued, "channel": channel,
        "chat_ref": db.pseudonym(channel, chat_raw) if chat_raw is not None else None,
        "level": v.level.value, "rule_id": v.rule_id, "payload": json.dumps(payload, ensure_ascii=False), "signature": signature,
        "verdict": v.model_dump_json(), "proof": json.dumps(proof, ensure_ascii=False), "media": json.dumps(media_files),
        "media_expires_at": (datetime.now(timezone.utc) + db.MEDIA_TTL).isoformat(),   # every check, text-only too: content goes after 24 h
    })
    from app.store import campaigns
    campaigns.record_check(check_id, json.loads(v.model_dump_json()))
    return Record(id=check_id, serial=serial, verdict=v, timings=res.timings, proof=proof, media=media_files, signature=signature, payload=payload)


def report_check(check_id: str, reporter_ref: str | None, note: str = "") -> dict:
    """User tapped Report: seal the evidence in the vault and file it into a campaign."""
    from app.store import campaigns, vault
    row = db.get_check(check_id=check_id)
    if not row:
        raise KeyError(check_id)
    if row.get("reported"):          # a second tap is not a second report: the evidence is sealed once
        with db.conn() as c:
            r = c.execute("SELECT campaign_id, chain_seq FROM reports WHERE check_id = ?", (check_id,)).fetchone()
        if r:
            from app.store.campaigns import indicators_from_verdict
            return {"serial": row["serial"], "chain_seq": r["chain_seq"], "files": None, "campaign_id": r["campaign_id"],
                    "indicators": indicators_from_verdict(row["verdict"]), "already_reported": True}
    d = STORE_DIR / check_id
    d.mkdir(parents=True, exist_ok=True)
    vjson = d / "verdict.json"
    vjson.write_text(json.dumps({"serial": row["serial"], "payload": row["payload"], "signature": row["signature"], "verdict": row["verdict"]},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    files = [d / m for m in row["media"]] + [p for p in (d / "card_en.png", d / "card_hi.png", vjson) if p.exists()]
    manifest = vault.seal_report(row, files, reporter_ref, note)
    filed = campaigns.file_report(row, manifest["chain_seq"], hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest())
    db.set_flag(check_id, "reported", 1)
    return {"serial": row["serial"], "chain_seq": manifest["chain_seq"], "files": len(manifest["files"]), **filed}
