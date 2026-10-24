"""Evidence Vault: tamper-evident storage for items a user chooses to REPORT.

- Every file is stored under its SHA-256 and never overwritten (write-once).
- Every vault event is appended to a hash chain: each record includes the
  previous record's hash, so editing or deleting history breaks the chain.
- Each report gets a signed manifest (Ed25519) listing its files and hashes,
  the verdict and the chain position: what an investigator would receive.

Prototype: a local append-only folder. Production design: S3 Object Lock in
compliance mode with a KMS-signed manifest (same interface).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import STATE_DIR
from app.store.signing import key_id, sign, verify

VAULT = STATE_DIR / "vault"
CHAIN = VAULT / "chain.jsonl"
_LOCK = threading.Lock()
GENESIS = "0" * 64


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _last_hash() -> tuple[str, int]:
    if not CHAIN.exists():
        return GENESIS, 0
    last, n = GENESIS, 0
    with open(CHAIN, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last, n = json.loads(line)["hash"], n + 1
    return last, n


def append_event(kind: str, data: dict) -> dict:
    with _LOCK:
        VAULT.mkdir(parents=True, exist_ok=True)
        prev, n = _last_hash()
        rec = {"seq": n + 1, "at": datetime.now(timezone.utc).isoformat(), "kind": kind, "data": data, "prev": prev}
        rec["hash"] = _sha(_canon(rec))
        with open(CHAIN, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


def store_file(src: Path) -> dict:
    b = src.read_bytes()
    h = _sha(b)
    dst = VAULT / "objects" / h[:2] / h
    if not dst.exists():                         # write-once: identical content is stored once, never replaced
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        dst.chmod(0o444)
    return {"sha256": h, "bytes": len(b), "name": src.name}


def seal_report(check: dict, files: list[Path], reporter_ref: str | None, note: str = "") -> dict:
    """Store a reported check's evidence and return its signed manifest."""
    objs = [store_file(p) for p in files if p.exists()]
    body = {
        "type": "pramaan.report.v1", "serial": check["serial"], "check_id": check["id"], "verdict": check["level"],
        "rule": check.get("rule_id"), "reasons": check["payload"].get("reasons"), "files": objs,
        "reporter": reporter_ref, "note": note[:500], "reported_at": datetime.now(timezone.utc).isoformat(),
    }
    ev = append_event("report", {"serial": check["serial"], "files": [o["sha256"] for o in objs], "manifest_sha256": _sha(_canon(body))})
    manifest = {**body, "chain_seq": ev["seq"], "chain_hash": ev["hash"], "kid": key_id()}
    manifest["signature"] = sign(manifest)
    out = VAULT / "manifests" / f"{check['serial']}-{ev['seq']}.json"   # one file per chain record: never overwritten
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "x", encoding="utf-8") as f:                         # "x": fails rather than replace an existing manifest
        f.write(json.dumps(manifest, indent=1, ensure_ascii=False))
    out.chmod(0o444)
    return manifest


def verify_chain() -> dict:
    """Recompute every hash. Returns {'ok': bool, 'records': n, 'broken_at': seq|None}."""
    if not CHAIN.exists():
        return {"ok": True, "records": 0, "broken_at": None, "head": GENESIS}
    prev, n = GENESIS, 0
    with open(CHAIN, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            n += 1
            claimed = rec.pop("hash")
            if rec["prev"] != prev or _sha(_canon(rec)) != claimed:
                return {"ok": False, "records": n, "broken_at": rec.get("seq"), "head": prev}
            prev = claimed
    return {"ok": True, "records": n, "broken_at": None, "head": prev}


def verify_manifest(manifest: dict) -> bool:
    m = dict(manifest)
    sig = m.pop("signature", "")
    files_ok = all((VAULT / "objects" / f["sha256"][:2] / f["sha256"]).exists() and
                   _sha((VAULT / "objects" / f["sha256"][:2] / f["sha256"]).read_bytes()) == f["sha256"] for f in m.get("files", []))
    return verify(m, sig) and files_ok
