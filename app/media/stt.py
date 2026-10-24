"""Speech-to-text: Groq Whisper (free tier), cached by audio hash.

Returns the transcript plus word timings, which the proof page uses to
highlight changed words in sync with the video.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import DATA_DIR, get_settings
from app.media.prep import sha256_file

log = logging.getLogger("pramaan.stt")
CACHE = DATA_DIR / "cache" / "stt"


class STTUnavailable(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language: str
    duration: float
    words: list[dict] = field(default_factory=list)      # {"word", "start", "end"}
    segments: list[dict] = field(default_factory=list)   # {"text", "start", "end"}
    provider: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def transcribe(audio: Path, language: str | None = None, model: str = "whisper-large-v3") -> Transcript:
    key = f"{sha256_file(audio)[:32]}_{language or 'auto'}_{model}"
    cp = CACHE / f"{key}.json"
    if cp.exists():
        d = json.loads(cp.read_text(encoding="utf-8"))
        d["provider"] = d.get("provider", "") + "(cached)"
        return Transcript(**d)
    s = get_settings()
    if not s.groq_api_key:
        raise STTUnavailable("no GROQ_API_KEY")
    data = {"model": model, "response_format": "verbose_json", "temperature": "0"}
    if language:
        data["language"] = language
    files = [("timestamp_granularities[]", (None, "word")), ("timestamp_granularities[]", (None, "segment"))]
    last_err = None
    for attempt in range(3):
        try:
            with open(audio, "rb") as f:
                r = httpx.post("https://api.groq.com/openai/v1/audio/transcriptions",
                               headers={"Authorization": f"Bearer {s.groq_api_key}"},
                               data=data, files=[("file", (audio.name, f, "audio/mpeg"))] + files, timeout=60)
            if r.status_code == 429:
                last_err = "rate limited"
                import time; time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            j = r.json()
            t = Transcript(
                text=(j.get("text") or "").strip(), language=j.get("language") or (language or ""), duration=float(j.get("duration") or 0),
                words=[{"word": w["word"], "start": w["start"], "end": w["end"]} for w in j.get("words") or []],
                segments=[{"text": sg["text"].strip(), "start": sg["start"], "end": sg["end"]} for sg in j.get("segments") or []],
                provider=f"groq:{model}",
            )
            CACHE.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(t.to_dict(), ensure_ascii=False), encoding="utf-8")
            return t
        except httpx.HTTPError as e:
            last_err = type(e).__name__
    raise STTUnavailable(str(last_err))
