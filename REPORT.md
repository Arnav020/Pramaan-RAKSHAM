# Pramaan (प्रमाण): project report

**Team Saksham, TIET Patiala · RAKSHAM 2026 · Track 03, Deepfake Detection for Financial Communications**
**Live:** https://13-207-22-33.sslip.io · Telegram bot · **Code:** this repository

> *Faces can be faked. Payment rails can't.*

## 1. Problem
India lost ₹11,333 cr to cyber fraud in Jan–Sep 2024 (I4C). Stock-trading and investment scams were the largest share. A typical scam runs like this:
1. A deepfake video of a public figure.
2. A fake "SEBI-registered adviser".
3. A WhatsApp "VIP group".
4. A UPI payment.

Forged RBI/CBI notices and fake KYC SMS follow the same script.

Deepfake detectors alone fail on real WhatsApp video. In our own test, they flagged **15 of 15 genuine compressed videos** as fake. A verdict that is wrong this often can't be used to accuse anyone.

**Users:**
- **Primary:** a bank customer, especially someone new to digital finance, before paying, or in the "golden hour" just after paying.
- **Secondary:** the bank's fraud analyst.

## 2. Insight: Face, Claim, Ask
Every scam has three parts:
- **Face:** the deepfake.
- **Claim:** borrowed authority ("SEBI-registered", "your bank").
- **Ask:** pay, OTP or install.

Others try to detect the Face, an arms race defenders lose. Pramaan checks the **Claim** against India's official trust rails and reads the **Ask**. Even a perfect deepfake still needs a real payment destination.

## 3. What it does
**Input:** text or SMS, screenshot, PDF notice, video, voice note, QR poster, UPI ID, phone number or link. Send it on the website or the Telegram bot.

**Three independent lanes:**

| Lane | Checks |
|---|---|
| **A · WHO** (provenance) | SEBI `@valid` UPI rule, `.bank.in` domains, 1600/140 bank numbers, TRAI SMS headers, RBI Alert List, regulator registries; safe website inspection; Pramaan Seal signatures |
| **B · CHANGED** (manipulation) | **Source-Match:** finds the genuine original of a re-voiced video and shows "same footage, different words". **Pramaan Seal:** a bank signs its notices (Ed25519), so any edit is caught. Deepfake detectors give weak hints only. |
| **C · WHAT** (intent) | The ask (pay, OTP, install, private group), pressure (urgency, secrecy, threats), 19 scam playbooks, English, Hindi and Hinglish |

**Fusion:** a versioned, auditable rule table decides.
- 🔴 **Matches a known scam pattern:** strong signals from **at least 2 different lanes**.
- 🟢 **Verified sender:** verified rails, or a valid bank seal, with no risk signs.
- 🟡 **Can't verify:** everything else, always with "how to check yourself".
- A detector score alone can never make a result red. There are no raw scores and no automatic blocking, and Pramaan never says "fake".

**After the verdict:**
- **Official links** come from our registry, never from the message.
- **Warn my family.**
- **Golden-Hour Kit** for anyone who already paid: call 1930, plus a pre-filled cybercrime.gov.in report.
- **Hindi and English voice** read-out.
- **A signed proof card with a QR code**, so screenshots of our verdicts can't be forged.
- **A tamper-evident evidence vault** (hash chain).
- **An analyst desk:** confirm a campaign once, protect every later victim ("review once, protect many").
- **Lookup and appeals.**

## 4. Safety and responsible AI
- **The AI only reads; rules decide.** The AI (Groq/Gemini) fills a fixed schema, can only add what the message quotes, and can't change a verdict. Prompt-injection attempts are tested.
- **Few false accusations:** red needs two lanes. When unsure, the answer is amber with self-check steps.
- **Privacy:** we store only the shared item. Private people are never identified. Free AI tiers receive only synthetic, public or consented data.
- **No deepfakes of real people:** our demo videos use consented clips of the team members.

## 5. Testing (reproducible: `eval/REPRODUCE.md`; one command: `python -m eval.kit`)

| Test | Result |
|---|---|
| Unit tests (`pytest`) | 186 / 186 pass |
| Frozen labelled test split: 51 items (text, video, PDF, image; English, Hindi, Hinglish), hash-locked | Scams warned 24/26, scams cleared 0; genuine marked red 0 |
| Public split: 24 real scams and advisories quoted in the news | Scams cleared 0/16; genuine warned 0/8 |
| Open dataset: 5,971 SMS (Mishra & Soni, CC BY 4.0), never tuned on | Legitimate SMS warned 2/4,844; legitimate marked red 0 |
| Key experiment: compressed genuine videos | Detectors alone flagged 15/15 as fake; Pramaan marked 0/15 red and caught 7/7 fakes |
| Red team | 34/34 attacks defended; 3 known misses disclosed |
| Jury spot-check kit (`data/kit`, 49 files; `/kit` page) | 47/49 pass, 0 hard errors |
| Pre-registered quality gates (`eval/gates.yaml`) | 12/12 pass |

Datasets we built ourselves are dev sets and are never reported as accuracy. All synthetic-data generators are in `tools/` and `eval/generate/`.

## 6. Architecture and scale
- **Prototype:** FastAPI, deterministic rails, rules and fusion. Groq/Gemini are used only as the extractor. Local Whisper, CLIP and FAISS power Source-Match. Runs on one EC2 server in Mumbai, inside the free-tier credits.
- **Production design:** AWS ap-south-1 with API Gateway, Step Functions, S3 Object Lock, KMS, Bedrock and Rekognition.
- **Operator:** the partner bank's fraud team.
- **Rollout:** a "forward to verify" line, then an in-app Share button and a pay-time nudge, then a shared campaign feed across banks, I4C and SEBI.
- **Cost (estimate):** ~₹0.2 per text check, ~₹4 per video check.

## 7. Limits (stated honestly)
- **Mocked:** the DoT Fraud Risk Indicator, because only regulated entities can access it.
- **Fictional:** Demo Bank is a test issuer.
- **Known misses:** grooming before the ask, look-alike `@valid` IDs, and mule accounts given as account numbers.
- **Small samples:** the sample sizes are small, so all metrics carry wide confidence intervals.

Every external tool, model, dataset and API is listed in `ACKNOWLEDGEMENTS.md`.
