#!/usr/bin/env bash
# One-command local run (Linux/macOS):  ./run.sh
# First run sets up a virtual environment (CPU PyTorch, ~2 GB download), then starts Pramaan on http://localhost:8000.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/python -m playwright install --with-deps chromium
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Pramaan runs without keys (rules only); add free Groq/Gemini keys and a Telegram bot token to enable the AI extractor and the bot."
fi
exec .venv/bin/python -m uvicorn app.server:app --port 8000
