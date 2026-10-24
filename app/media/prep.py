"""Media preparation with ffmpeg: type detection, keyframes, audio, metadata stripping.

Everything here is CPU-only and bounded (max frames, max duration) so a hostile
upload cannot exhaust the server.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

MAX_DURATION_S = 180       # longer media is truncated for checking
MAX_FRAMES = 90
FRAME_FPS = 2.0            # keyframe sampling rate
FRAME_WIDTH = 480

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".3gp", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".aac", ".amr", ".flac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic"}
PDF_EXT = {".pdf"}


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except FileNotFoundError as e:
        raise MediaError(f"{cmd[0]} not installed") from e
    except subprocess.CalledProcessError as e:
        raise MediaError(f"{cmd[0]} failed: {e.stderr.decode('utf-8', 'ignore')[-300:]}") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{cmd[0]} timed out") from e


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Probe:
    kind: str                      # video | audio | image | pdf | unknown
    duration: float = 0.0
    width: int = 0
    height: int = 0
    has_audio: bool = False
    has_video: bool = False
    tags: dict = field(default_factory=dict)


def probe(path: Path) -> Probe:
    ext = path.suffix.lower()
    with open(path, "rb") as f:
        head = f.read(8)
    if ext in PDF_EXT or head.startswith(b"%PDF"):
        return Probe(kind="pdf")
    if ext in IMAGE_EXT:
        try:
            with Image.open(path) as im:
                return Probe(kind="image", width=im.width, height=im.height)
        except Exception as e:
            raise MediaError(f"unreadable image: {e}") from e
    out = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=30)
    info = json.loads(out.stdout or b"{}")
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration") or 0.0)
    kind = "video" if v else "audio" if a else "unknown"
    return Probe(kind=kind, duration=dur, width=int(v.get("width", 0)) if v else 0, height=int(v.get("height", 0)) if v else 0,
                 has_audio=a is not None, has_video=v is not None, tags=info.get("format", {}).get("tags", {}))


@dataclass
class Frame:
    t: float
    image: Image.Image


def extract_frames(path: Path, fps: float = FRAME_FPS, max_frames: int = MAX_FRAMES, width: int = FRAME_WIDTH) -> list[Frame]:
    tmp = Path(tempfile.mkdtemp(prefix="pramaan_frames_"))
    try:
        _run(["ffmpeg", "-v", "error", "-y", "-t", str(MAX_DURATION_S), "-i", str(path),
              "-vf", f"fps={fps},scale={width}:-2", "-frames:v", str(max_frames), "-q:v", "3", str(tmp / "f_%04d.jpg")], timeout=180)
        frames = []
        for i, fp in enumerate(sorted(tmp.glob("f_*.jpg"))):
            with Image.open(fp) as im:
                frames.append(Frame(t=round(i / fps, 2), image=im.convert("RGB").copy()))
        return frames
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_audio(path: Path, out: Path) -> Path:
    """16 kHz mono MP3, small enough for speech-to-text upload limits."""
    _run(["ffmpeg", "-v", "error", "-y", "-t", str(MAX_DURATION_S), "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(out)], timeout=180)
    if not out.exists() or out.stat().st_size < 1000:
        raise MediaError("no usable audio track")
    return out


def strip_metadata(path: Path, out: Path) -> Path:
    """Copy streams without container metadata (removes GPS location, device model)."""
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0:v?", "-map", "0:a?", "-map_metadata", "-1", "-c", "copy", str(out)], timeout=180)
    return out
