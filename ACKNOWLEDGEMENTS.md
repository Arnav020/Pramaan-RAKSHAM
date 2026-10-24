# Acknowledgements and disclosure

The hackathon rules require us to disclose every external code library, model, dataset, API and AI tool we use. This file is updated as the build progresses.

## AI tools
- **Anthropic Claude**: AI coding and writing assistant used during development.
- **OpenAI ChatGPT**: generated the fictional-banknote cover image used in the presentation.

## APIs and services (runtime)
- **Groq API** (free tier): `openai/gpt-oss-120b` and `openai/gpt-oss-20b` for structured extraction; `whisper-large-v3` for speech-to-text.
- **Groq API** fallback models: `qwen/qwen3.8-27b`.
- **Groq API**: Meta's **Llama Prompt Guard 2** (`meta-llama/llama-prompt-guard-2-86m`, Llama 4 Community License) scores text for hidden instructions; an extra warning signal only, never a verdict.
- **Google Gemini API** (free tier): `gemini-3.1-flash-lite` (and `-preview`, `gemini-flash-lite-latest`) as the last-resort fallback for extraction, and Gemini vision as the OCR fallback for Hindi or hard images.
- **Telegram Bot API**: the citizen channel (`@PramaanVerifyDemoBot`).
- **Amazon Web Services** (Free plan, ap-south-1): one EC2 `m7i-flex.large` server (Ubuntu 24.04) hosts the demo. S3 Object Lock and KMS are the production design for the vault, not used by the prototype.
- **RDAP** (Registration Data Access Protocol): IANA's bootstrap file (`data.iana.org/rdap/dns.json`) and each registry's public RDAP service (for `.in`, NIXI) give a domain's registration date and registrar.
- **Caddy** (Apache-2.0) with **Let's Encrypt** certificates: HTTPS for the demo server. **sslip.io**: free DNS name that maps to the server's IP address.

## Public data and rules we rely on (each data file in `app/rails/data/` records its source and date)
- SEBI validated UPI handles (`@valid`) and SEBI Check, SEBI circular of 11 Jun 2025.
- RBI `.bank.in` domain migration (circular of 22 Apr 2025); bank domains checked in public DNS.
- TRAI direction on the 1600-series for BFSI service and transaction calls (deadlines Jan–Mar 2026).
- TRAI DLT SMS header suffixes (-P / -S / -T / -G).
- RBI Alert List of unauthorised forex trading platforms: all 95 entities, list updated 19 Nov 2025 (PDF downloaded manually; SHA-256 recorded in the data file).
- RBI notifications (25 recent circulars, public metadata, fetched politely from rbi.org.in on 22 Oct 2026) for document Source-Match.
- SEBI register counts of research analysts and investment advisers (sebi.gov.in; no bulk copy).
- Scam playbooks (15) summarised from public advisories and fact-checks, each linked in `playbooks.yaml`: SEBI PR 27/2025 and SEBI investor warnings on fake trading groups, RBI BE(A)WARE booklet (Mar 2022), RBI Sachet, RBI Alert List additions (Nov 2025), I4C advisories on digital arrest and stock scams (reported Nov 2024), ED charge sheet reporting, PIB Fact Check (SBI reward-points APK, Dec 2025; India Post address SMS, Jan 2026), the Centre's alert on fake income-tax refund messages (Aug 2023), power-utility warnings (APDCL, MSEDCL, PSPCL, Jun 2022), Newschecker on Telegram task scams (2023).
- **Public evaluation split** (`eval/public_set.yaml`, 24 items): short scam and genuine-message quotes, each with its URL and date, from Business Today, LatestLY, Fact Crescendo, The Sentinel (Assam), Voice&Data, The Tech Outlook, The Tribune, Deccan Herald, The News Minute, Business Standard, IndusInd Bank, EastMojo, CyberPeace Foundation, Newschecker, Outlook Money, Money9, News On AIR (PIB) and SEBI's investor site. Quotes are kept short for evaluation and attributed; the articles remain their publishers' work.
- DoT Financial Fraud Risk Indicator: **mocked** with fictional numbers; the real feed is only available to regulated entities.

## Models (run locally on CPU)
- **multilingual-e5-small** (`intfloat/multilingual-e5-small`, MIT; Wang et al., 2024): sentence embeddings for the scam-pattern matcher over our own exemplar sentences.

### Weak hints only
- **GenD** video deepfake detector, `HoopitAI/video-deepfake-detection-GenD_CLIP_L_14_FF` (MIT): CLIP ViT-L/14 with layer-norm tuning, trained on FaceForensics++ (method: Yermakov et al., WACV 2026). Its custom model code was reviewed before use.
- **OpenAI CLIP ViT-L/14** backbone (`openai/clip-vit-large-patch14`, MIT).
- **wav2vec2 synthetic-speech classifier**, `MelodyMachine/Deepfake-audio-detection-V2` (Apache-2.0).
- OpenCV Haar frontal-face *detector* to crop faces for GenD (finds where a face is; never identifies anyone).

## Tools used to make test data (not part of Pramaan's runtime)
- **LatentSync 1.5** by ByteDance (`bytedance/LatentSync`, Apache-2.0; checkpoint `ByteDance/LatentSync-1.5`), run once on a **Kaggle** notebook GPU (T4), to make one realistic lip-synced test fake of our own consented clip 1 with Arnav's own recorded voice. The labelled copy carries a visible "AI-generated test video" bar. Its dependencies (Whisper tiny, InsightFace, Stable Diffusion VAE `stabilityai/sd-vae-ft-mse`) were downloaded by LatentSync's own code.
- **uv** (Astral, MIT/Apache-2.0): a separate Python 3.10 environment for LatentSync on Kaggle.

## Libraries
- FastAPI, Uvicorn, Pydantic, HTTPX, python-dotenv, PyYAML (MIT / BSD)
- RapidFuzz (MIT): fuzzy name matching
- tldextract (BSD-3): registrable-domain parsing with the bundled Public Suffix List snapshot
- Beautiful Soup 4 (MIT), pypdf (BSD-3)
- pytest, pytest-asyncio (MIT)
- PyTorch (BSD-3, CPU build), Hugging Face Transformers and Hub (Apache-2.0), timm (Apache-2.0)
- NumPy (BSD-3), Pillow (MIT-CMU/HPND)
- **PDQ perceptual hash** by Meta (ThreatExchange, BSD), via the `pdqhash` Python binding (MIT): content fingerprints for Source-Match
- **FFmpeg** (LGPL/GPL, used as an external program): keyframes, audio, test variants
- **RapidOCR** (Apache-2.0, PaddleOCR models via ONNX Runtime): local OCR for images and scanned PDFs
- **OpenCV** (Apache-2.0): UPI QR-code decoding
- **PDFium** via pypdfium2 (Apache-2.0/BSD-3): rendering PDF pages
- Jinja2 (BSD-3), qrcode (BSD), cryptography (Apache-2.0/BSD): templates, QR codes, Ed25519 verdict signatures
- **Playwright** (Apache-2.0) with headless **Chromium**: renders the verdict certificate to PNG
- Windows **System.Speech** synthesizer: offline voice for our Plan-B test fakes (re-voiced copies of our own consented clips)

## Fonts (SIL Open Font License, bundled in `app/static/fonts/`)
- Gelasio (Georgia-metric serif), Noto Sans, Noto Sans Devanagari, Noto Serif Devanagari, JetBrains Mono

## Media
- All test videos are recordings of the two team members, made with written consent. No real public figure is recorded, imitated or deepfaked.
