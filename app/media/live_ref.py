"""Register an official video live, and make a labelled test edit of it.

Why: Source-Match only knows footage that was registered. On stage a judge can register a clip they record themself
(with their consent), then turn it into a "same footage, different words" edit with their own new recording, forward it,
and watch Pramaan find the original. Nothing here is a deepfake generator: the footage is untouched and the new words
are a real recording by the same consenting person; the edit only swaps the sound track and carries a visible label.

Safety:
- Only a signed-in issuer (an institution's console) can register footage; it is marked as that issuer's official clip.
- Consent is a required tick box; the clip and its test edit are deleted 24 hours after registration (purge_expired).
- Every test edit has a visible "TEST EDIT" band burned into the picture and a metadata note; it is for demonstrations only.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import DATA_DIR, STATIC_DIR
from app.lanes.source_match import INDEX_PATH, build_reference, save_index
from app.media.prep import MediaError, extract_audio, extract_frames, probe, sha256_file
from app.media.stt import transcribe

GENUINE_DIR = DATA_DIR / "media" / "genuine"
EDITS_DIR = DATA_DIR / "media" / "live_edits"
TTL = timedelta(hours=24)
MAX_SECONDS = 60
PREFIX = "live_"


def _refs() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))["refs"]


def _run(cmd: list[str], timeout: int = 180) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg failed").strip().splitlines()[-1][:200])


def register(src: Path, issuer_name: str, title: str, language: str | None = None) -> dict:
    """Fingerprint and transcribe a genuine clip, then add it to the Source-Match index as the issuer's official video."""
    try:
        pr = probe(src)
    except MediaError:
        raise ValueError("That file could not be read as a video.")
    if pr.kind != "video":
        raise ValueError("Please upload a video.")
    if pr.duration and pr.duration > MAX_SECONDS:
        raise ValueError(f"Keep it under {MAX_SECONDS} seconds.")
    rid = PREFIX + secrets.token_hex(4)
    GENUINE_DIR.mkdir(parents=True, exist_ok=True)
    dst = GENUINE_DIR / f"{rid}.mp4"
    # One clean copy: H.264, 25 fps, no metadata (phone videos carry location and device details).
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map_metadata", "-1", "-t", str(MAX_SECONDS), "-r", "25",
          "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "96k",
          "-movflags", "+faststart", str(dst)])
    frames = extract_frames(dst)
    with tempfile.TemporaryDirectory() as td:
        tr = transcribe(extract_audio(dst, Path(td) / "a.mp3"), language=language)
    now = datetime.now(timezone.utc)
    meta = {"id": rid, "file": dst.name, "title_en": f"{title.strip()[:80] or 'Official video'} ({issuer_name}, registered live)",
            "title_hi": f"{title.strip()[:80] or 'आधिकारिक वीडियो'} ({issuer_name}, लाइव दर्ज)", "language": tr.language or language or "en",
            "published": now.date().isoformat(), "speaker": "registered live with the speaker's consent", "official": issuer_name,
            "sha256": sha256_file(dst), "live": True, "expires_at": (now + TTL).isoformat()}
    ref = build_reference(frames, {"text": tr.text, "words": tr.words, "segments": tr.segments, "language": tr.language}, meta)
    save_index([r for r in _refs() if r["id"] != rid] + [ref])
    return {"id": rid, "title": meta["title_en"], "words": len(tr.words), "frames": len(frames), "transcript": tr.text, "expires_at": meta["expires_at"]}


def live_refs() -> list[dict]:
    return [{k: r.get(k) for k in ("id", "title_en", "official", "expires_at", "published")} | {"transcript": r["transcript"].get("text", "")}
            for r in _refs() if r.get("live")]


def _label_png(width: int, height: int, out: Path) -> None:
    band = max(28, height // 16)
    im = Image.new("RGBA", (width, band), (168, 50, 45, 230))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(str(STATIC_DIR / "fonts" / "NotoSans.ttf"), int(band * 0.55))
    except OSError:
        font = ImageFont.load_default()
    d.text((12, band * 0.18), "TEST EDIT · new words over genuine footage · Pramaan demo", fill=(255, 255, 255, 255), font=font)
    im.save(out)


def make_test_edit(ref_id: str, voice: Path) -> Path:
    """The registered footage with a new sound track (the same consenting person's new recording) and a visible label."""
    ref = next((r for r in _refs() if r["id"] == ref_id and r.get("live")), None)
    if ref is None:
        raise KeyError(ref_id)
    video = GENUINE_DIR / ref["file"]
    try:
        pr = probe(voice)
    except MediaError:
        raise ValueError("That file could not be read as a recording.")
    if pr.kind not in ("audio", "video") or not (pr.has_audio or pr.kind == "audio"):
        raise ValueError("Please upload a voice recording.")
    EDITS_DIR.mkdir(parents=True, exist_ok=True)
    out = EDITS_DIR / f"{ref_id}_test_edit_{secrets.token_hex(2)}.mp4"
    with tempfile.TemporaryDirectory() as td:
        frames = extract_frames(video, max_frames=1)
        w, h = frames[0].image.size if frames else (1280, 720)
        # extract_frames scales to a fixed width: compute the band for the real video size
        vw = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                 str(video)], capture_output=True, text=True).stdout.strip().split(",")[0] or w)
        vh = int(round(vw * h / w))
        label = Path(td) / "label.png"
        _label_png(vw, vh, label)
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(voice), "-i", str(label),
              "-filter_complex", "[0:v][2:v]overlay=0:0[v]", "-map", "[v]", "-map", "1:a:0", "-shortest",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "96k",
              "-metadata", "comment=Pramaan demo test edit: genuine footage with a new sound track. Not a real statement.", str(out)])
    return out


def purge_expired() -> int:
    """Delete live clips (and their test edits) 24 hours after registration."""
    now = datetime.now(timezone.utc).isoformat()
    refs = _refs()
    gone = [r for r in refs if r.get("live") and (r.get("expires_at") or "") < now]
    if not gone:
        return 0
    save_index([r for r in refs if r not in gone])
    for r in gone:
        (GENUINE_DIR / r["file"]).unlink(missing_ok=True)
        for p in EDITS_DIR.glob(f"{r['id']}_*"):
            p.unlink(missing_ok=True)
    return len(gone)


def remove(ref_id: str) -> bool:
    refs = _refs()
    keep = [r for r in refs if not (r["id"] == ref_id and r.get("live"))]
    if len(keep) == len(refs):
        return False
    save_index(keep)
    (GENUINE_DIR / f"{ref_id}.mp4").unlink(missing_ok=True)
    for p in EDITS_DIR.glob(f"{ref_id}_*"):
        p.unlink(missing_ok=True)
    return True

