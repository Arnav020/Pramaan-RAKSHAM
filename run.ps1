# One-command local run (Windows):  powershell -File run.ps1
# First run sets up a virtual environment (CPU PyTorch, ~2 GB download), then starts Pramaan on http://localhost:8000.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    python -m venv .venv
    .venv\Scripts\python -m pip install -q --upgrade pip
    .venv\Scripts\pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
    .venv\Scripts\pip install -q -r requirements.txt
    .venv\Scripts\python -m playwright install chromium
}
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example. Pramaan runs without keys (rules only); add free Groq/Gemini keys and a Telegram bot token to enable the AI extractor and the bot."
}
.venv\Scripts\python -m uvicorn app.server:app --port 8000
