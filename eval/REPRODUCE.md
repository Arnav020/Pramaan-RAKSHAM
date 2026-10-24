# Test kit: how to reproduce every number

Everything Pramaan claims about accuracy comes from the scripts below. Their outputs are JSON files in `eval/results/`,
and the Lab (`/lab`) and red-team page (`/redteam`) read those files. No number is typed in by hand.

## One command

```bash
python -m eval.kit          # tests, labelled evaluation, open dataset, red team, robustness (~20 min)
python -m eval.kit --quick  # without the robustness studies (~12 min)
```

It stops at the first failing step and writes `eval/results/kit_summary.json`: what each part covers (input types,
languages, lanes, verdicts) and its headline numbers.

Setup first: `powershell -File run.ps1` (Windows) or `./run.sh` creates `.venv`, installs the pinned requirements and
Chromium, and copies `.env.example` to `.env`. With no API keys everything runs on rules only; add a free `GROQ_API_KEY`
for the AI extractor and speech-to-text.

## The parts

| # | Command | Data | Output | Time |
|---|---|---|---|---|
| 1 | `python -m pytest` | Fixtures inside the tests | pass / fail (174 tests) | ~1 min |
| 2 | `python -m eval.run` | `eval/dataset.yaml` (test 51, dev 18) and `eval/public_set.yaml` (24) | `latest.json` | ~5 min |
| 3 | `python -m eval.open_kit` | 5,971 public SMS (below), downloaded once and hash-checked | `open_kit.json` | ~6 min |
| 4 | `python -m eval.redteam` | Attacks defined in `eval/redteam.py` | `redteam.json` | ~2 min |
| 5 | `python -m eval.robustness` | AI rewrites of frozen test scams; playbooks hidden one at a time | `robustness.json` | ~7 min |

## Datasets

| Set | Size | Where it comes from | Licence | Frozen? |
|---|---|---|---|---|
| **Test split** | 51 (26 scam, 25 genuine; text, video, PDF, image; EN / HI / Hinglish) | Written by the team from cited public advisories; videos are consented team clips and their forwarded variants | Ours | Yes, before the first measurement: `eval/FROZEN.sha256` |
| Dev split | 18 | As above; used to develop rules (later items such as the lip-sync fakes went here, never into test) | Ours | No |
| **Public split** | 24 (16 scam, 8 genuine) | Messages quoted in news reports, fact checks and official advisories; each item has its source URL and date | Quoted for research, with attribution | Yes, before any detection change of 24 Oct: `eval/PUBLIC_FROZEN.sha256` |
| **Open dataset** | 5,971 (4,844 ham, 489 spam, 638 smishing) | Mishra, S. and Soni, D. (2022). *SMS Phishing Dataset for Machine Learning and Pattern Recognition*. Mendeley Data, V1. doi:10.17632/f45bkkt8pr.1 | CC BY 4.0 | Publisher's SHA-256 checked on every run; never used to write or tune a rule |

**Disclosure on the public split:** after it was frozen, its first run showed four scam families with no playbook; we
added them from official advisories (the Lab says so next to the numbers). On 24 Oct (finale) we also tightened the OTP
playbook and the green rule after our own red-team audit. Both changes were made for reasons found outside this set,
but the "after" figures are not fully independent of it.

**Why the open dataset is a hard test for us:** most of it is UK and US prize and marketing SMS from before UPI existed.
Pramaan's rails and playbooks are written for Indian financial scams. The result shows how far the rules carry outside
India and lists where they fail; it is not a claim about Indian traffic.

## Synthetic data and its generation code

| Synthetic item | Made by | Code |
|---|---|---|
| Re-voiced test videos (genuine footage, new words from an offline Windows voice) and forwarding variants (WhatsApp-style compression, crop, mirror + trim, low quality) | `ffmpeg` + Windows System.Speech | `tools/make_test_fakes.py` |
| Realistic lip-sync test fake of a consented team clip | LatentSync 1.5 on a Kaggle T4 GPU | `eval/generate/lipsync_latentsync.ipynb` |
| Forged "RBI" notices, a genuine-style Demo Bank notice, a scam poster with a UPI QR (all marked SPECIMEN) | HTML → PDF with headless Chromium | `tools/make_test_docs.py` |
| Stress-test media: SMS / WhatsApp screenshots, UPI QR posters, PDF notices (one scanned-style), synthetic voice notes and voice-over slide videos (no faces, no real voices) | Headless Chromium, `qrcode`, Windows System.Speech, `ffmpeg` | `eval/generate/make_stress_media.py` |
| Sealed Demo Bank notices and SMS, and edited / copied versions | Pramaan Seal with a fictional test issuer | `tools/make_seal_demos.py` |
| 100 rewrites of 20 frozen test scams (Hinglish, formal, typos, Hindi) | Groq `gpt-oss-120b`, prompt in the code | `eval/robustness.py` (`variants`), stored in `eval/paraphrase/variants.json` |
| A live test edit of a consented clip registered at the demo | `ffmpeg` (sound track swap + visible label) | `app/media/live_ref.py` |

Every person in every video is a team member who gave written consent, or someone who ticked the consent box when
registering their own clip live. We never generate or alter media of real public figures.

Media files are not in the repository (privacy and size). Without them, the video items are skipped and the outputs list
them under `skipped`; every text item, the open dataset and the red-team text attacks run anywhere.

## What "correct" means

| Measure | Definition |
|---|---|
| Red precision | Of all red verdicts, the share that were scams |
| Genuine marked red | Genuine items wrongly accused (the error we refuse to make) |
| Scams caught | Scams that were not green |
| **Tiers** (added 24 Oct) | What the person is told: **Warn** (red, or amber with a high-risk request: don't act) · **Check first** (amber: confirm officially) · **Clear** (green, or "No warning signs") |
| Genuine warned / not cleared | False alarms counted at both levels, not only red |

Rates come with Wilson 95% intervals. The sets are small, so the intervals are wide, and we show them.

## Failure cases we name

- `open_kit.json` → `failures`: smishing that came out Clear, and legitimate messages that were warned, quoted.
- `redteam.json` → group "Known misses": attacks that still beat us, each with what happens and what we would change.
- `latest.json` → every item with its verdict, tier, rule and reasons; the Lab lists the misses by category.
