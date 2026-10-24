"""Weak forensic detectors. They are HINTS, never decisive (fusion caps them at amber).

Video: GenD (CLIP ViT-L/14, layer-norm tuned, FaceForensics++; WACV 2026 method)
       on face crops found by OpenCV's Haar face DETECTOR (locates a face, never
       identifies anyone).
Audio: wav2vec2 synthetic-speech classifier.

Scores are kept for analysts and for the Lab's key experiment; users never see
a score. If a model is missing or slow, the check simply runs without it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from app.config import ROOT

log = logging.getLogger("pramaan.detectors")
HF_CACHE = ROOT / "models" / "hf"
# GenD's own model code loads its CLIP backbone without a cache_dir, so point the
# whole Hugging Face hub at our folder (already holds CLIP) and never fetch at runtime.
os.environ.setdefault("HF_HUB_CACHE", str(HF_CACHE))
if (HF_CACHE / "models--openai--clip-vit-large-patch14").exists():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
VIDEO_MODEL = "HoopitAI/video-deepfake-detection-GenD_CLIP_L_14_FF"
AUDIO_MODEL = "MelodyMachine/Deepfake-audio-detection-V2"
VIDEO_THRESHOLD = 0.5
AUDIO_THRESHOLD = 0.5
MAX_FACES = 4               # 4 face crops keep a CPU check ~5 s; the Lab uses the same setting
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("PRAMAAN_DETECTORS", "on").lower() not in ("0", "off", "false")


def _torch():
    import torch
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 2)))
    return torch


# ---------------------------------------------------------------- face crops
@lru_cache(maxsize=1)
def _face_cascade():
    import cv2
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def face_crops(frames, max_faces: int = MAX_FACES) -> list[Image.Image]:
    """Largest face per frame, with margin, from evenly spaced frames."""
    import cv2
    if not frames:
        return []
    idx = np.linspace(0, len(frames) - 1, num=min(len(frames), max_faces * 2)).astype(int)
    crops = []
    for i in idx:
        img = frames[i].image
        g = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
        faces = _face_cascade().detectMultiScale(g, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(faces) == 0:
            continue
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        cx, cy, s = x + w / 2, y + h / 2, max(w, h) * 1.3
        box = (int(max(0, cx - s / 2)), int(max(0, cy - s / 2)), int(min(img.width, cx + s / 2)), int(min(img.height, cy + s / 2)))
        crops.append(img.crop(box).resize((224, 224)))
        if len(crops) >= max_faces:
            break
    return crops


# --------------------------------------------------------------- video model
@lru_cache(maxsize=1)
def _video_model():
    from app.lanes.hf_lock import HF_LOAD_LOCK
    with HF_LOAD_LOCK:
        from transformers import AutoModel
        m = AutoModel.from_pretrained(VIDEO_MODEL, trust_remote_code=True, cache_dir=str(HF_CACHE))   # code reviewed 22 Oct 2026
        m.eval()
    return m


def video_score(frames) -> dict | None:
    """Mean 'fake' probability over face crops, or None if no face / model unavailable."""
    if not enabled():
        return None
    crops = face_crops(frames)
    if not crops:
        return {"detector": "GenD CLIP-L/14 (FF++)", "faces": 0, "score": None, "flagged": False}
    t = time.perf_counter()
    try:
        torch = _torch()
        with _lock:
            m = _video_model()
            x = torch.stack([m.feature_extractor.preprocess(c) for c in crops])
            with torch.no_grad():
                p = m(x).softmax(dim=-1)[:, 1].numpy()
    except Exception as e:
        log.warning("video detector unavailable: %s", e)
        return None
    score = float(np.mean(p))
    return {"detector": "GenD CLIP-L/14 (FF++)", "faces": len(crops), "score": round(score, 3), "per_face": [round(float(v), 3) for v in p],
            "flagged": score >= VIDEO_THRESHOLD, "seconds": round(time.perf_counter() - t, 2)}


# --------------------------------------------------------------- audio model
@lru_cache(maxsize=1)
def _audio_model():
    from app.lanes.hf_lock import HF_LOAD_LOCK
    with HF_LOAD_LOCK:
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        fe = AutoFeatureExtractor.from_pretrained(AUDIO_MODEL, cache_dir=str(HF_CACHE))
        m = AutoModelForAudioClassification.from_pretrained(AUDIO_MODEL, cache_dir=str(HF_CACHE))
        m.eval()
    return fe, m


def _load_audio_16k(path: Path, max_s: float = 30.0) -> np.ndarray:
    import subprocess
    out = subprocess.run(["ffmpeg", "-v", "error", "-t", str(max_s), "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True, timeout=60).stdout
    return np.frombuffer(out, dtype=np.float32)


def audio_score(audio_path: Path | None) -> dict | None:
    if not enabled() or audio_path is None or not Path(audio_path).exists():
        return None
    t = time.perf_counter()
    try:
        torch = _torch()
        wav = _load_audio_16k(Path(audio_path))
        if len(wav) < 16000:
            return None
        with _lock:
            fe, m = _audio_model()
            chunks = [wav[i:i + 16000 * 6] for i in range(0, len(wav), 16000 * 6) if len(wav[i:i + 16000 * 6]) >= 16000 * 2][:5]
            labels = {i: l.lower() for i, l in m.config.id2label.items()}
            fake_idx = next(i for i, l in labels.items() if "fake" in l or "spoof" in l)
            probs = []
            for ch in chunks:
                inp = fe(ch, sampling_rate=16000, return_tensors="pt")
                with torch.no_grad():
                    probs.append(float(m(**inp).logits.softmax(-1)[0, fake_idx]))
    except Exception as e:
        log.warning("audio detector unavailable: %s", e)
        return None
    score = float(np.mean(probs)) if probs else 0.0
    return {"detector": "wav2vec2 synthetic-speech (MelodyMachine V2)", "chunks": len(probs), "score": round(score, 3),
            "flagged": score >= AUDIO_THRESHOLD, "seconds": round(time.perf_counter() - t, 2)}
