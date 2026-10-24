"""Make consented test fakes and robustness variants from our own genuine clips.

Plan B fakes (no GPU): keep the genuine footage, replace the audio with a scam
script read by the Windows offline voice (System.Speech). Variants simulate
forwarding: WhatsApp-style re-compression, cropping, mirroring, trimming.
Output: data/media/test/  (git-ignored). All source clips are team members,
recorded with written consent. Nothing here is published.

Usage:  python -m tools.make_test_fakes
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from app.config import DATA_DIR

G = DATA_DIR / "media" / "genuine"
OUT = DATA_DIR / "media" / "test"

SCRIPTS = {
    "clip1_arnav_en": ("Hello, I'm Arnav Mehra, and I have been investing for twenty years. Today I am sharing my secret formula with you. "
                       "My VIP group gives guaranteed returns of three hundred percent in one month. Join our Telegram group now. "
                       "Pay ten thousand rupees to profit wealth at Y B L today only. Seats are limited, so act fast."),
    "clip3_krish_en": ("Hello, I'm Krish from Demo Bank's customer safety team. Your account has been flagged and will be blocked today. "
                       "To keep your money safe, transfer your balance to our safe account immediately. "
                       "Share the OTP you receive with our officer to complete verification. Do not tell anyone."),
}


def sh(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def tts_windows(text: str, wav: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = 0; $s.SetOutputToWaveFile('{wav}'); $s.Speak(@'\n{text}\n'@); $s.Dispose()"
    )
    sh(["powershell", "-NoProfile", "-Command", ps])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    for clip, script in SCRIPTS.items():
        src = G / f"{clip}.mp4"
        if not src.exists():
            continue
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "v.wav"
            tts_windows(script, wav)
            fake = OUT / f"fake_revoiced_{clip}.mp4"
            # Loop the genuine footage under the new audio so lengths match.
            sh(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(src), "-i", str(wav), "-map", "0:v", "-map", "1:a",
                "-shortest", "-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", str(fake)])
            made.append(fake)
            # The fake as it arrives after a WhatsApp forward.
            wa = OUT / f"fake_revoiced_{clip}_wa.mp4"
            sh(["ffmpeg", "-v", "error", "-y", "-i", str(fake), "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "32", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "48k", str(wa)])
            made.append(wa)
    for clip in ("clip1_arnav_en", "clip3_krish_en", "clip4_krish_hi"):
        src = G / f"{clip}.mp4"
        if not src.exists():
            continue
        variants = {
            "wa": ["-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "32", "-c:a", "aac", "-b:a", "48k"],
            "crop": ["-vf", "crop=iw*0.8:ih*0.8,scale=-2:480", "-c:v", "libx264", "-crf", "30", "-c:a", "aac", "-b:a", "48k"],
            "trimflip": ["-ss", "5", "-t", "12", "-vf", "hflip,scale=-2:360", "-c:v", "libx264", "-crf", "34", "-c:a", "aac", "-b:a", "48k"],
            "lowq": ["-vf", "scale=-2:360,eq=brightness=-0.18:contrast=0.7,gblur=sigma=1.6,noise=alls=14:allf=t", "-c:v", "libx264", "-crf", "36",
                     "-c:a", "aac", "-b:a", "32k"],
        }
        for name, args in variants.items():
            out = OUT / f"genuine_{clip}_{name}.mp4"
            pre = args[:4] if args[0] == "-ss" else []
            rest = args[4:] if pre else args
            sh(["ffmpeg", "-v", "error", "-y", *pre, "-i", str(src), *rest, "-preset", "veryfast", str(out)])
            made.append(out)
    for m in made:
        print(m.relative_to(DATA_DIR))


if __name__ == "__main__":
    main()
