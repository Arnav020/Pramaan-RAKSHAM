"""Runtime settings, read once from the environment (.env in development)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAILS_DATA_DIR = ROOT / "app" / "rails" / "data"
STATIC_DIR = ROOT / "app" / "static"
# What a running Pramaan writes (database, proof media, vault, seal registry). Tests point it at a temp folder.
STATE_DIR = Path(os.environ.get("PRAMAAN_STATE_DIR") or DATA_DIR)
STORE_DIR = STATE_DIR / "store"         # transient media for proof pages (deleted after 24 h)
DB_PATH = STATE_DIR / "pramaan.db"

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    gemini_api_key: str
    telegram_bot_token: str
    aws_profile: str
    aws_region: str
    public_base_url: str
    # LLM routing: the first model that answers wins.
    # Checked against the live model lists on 22 Oct 2026 (gemini-2.5-* is closed to new keys).
    groq_models: tuple[str, ...] = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")
    gemini_models: tuple[str, ...] = ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview", "gemini-flash-lite-latest")
    llm_timeout_s: float = 20.0
    gemini_timeout_s: float = 30.0   # free Gemini answers in ~8-16 s, so it is the last resort


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        aws_profile=os.getenv("AWS_PROFILE", "pramaan").strip(),
        aws_region=os.getenv("AWS_REGION", "ap-south-1").strip(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip().rstrip("/"),
    )
